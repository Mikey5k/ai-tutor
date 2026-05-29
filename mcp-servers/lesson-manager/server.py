#!/usr/bin/env python3
"""Lesson Manager MCP Server — handles course ingestion, curriculum generation,
student tracking, test evaluation, and resource fetching for the AI tutor system."""

import asyncio
import json
import logging
import os
import sys
import uuid
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, str(Path(__file__).parent))

from storage.db import Database
from storage.cache import Cache
from ingestion.youtube_ingester import YouTubeIngester
from ingestion.whisper_transcriber import WhisperTranscriber
from ingestion.resource_fetcher import ResourceFetcher
from curriculum.curriculum_builder import CurriculumBuilder
from curriculum.lesson_scripter import LessonScripter
from curriculum.exercise_builder import ExerciseBuilder
from curriculum.test_builder import TestBuilder
from student.student_model import StudentModel
from student.progress_tracker import ProgressTracker
from student.difficulty_adapter import DifficultyAdapter

import anthropic
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

db = Database()
cache = Cache()
yt_ingester = YouTubeIngester()
whisper = WhisperTranscriber()
resource_fetcher = ResourceFetcher(os.environ.get("SEARXNG_URL", "http://localhost:8080"))
curriculum_builder = CurriculumBuilder()
lesson_scripter = LessonScripter()
exercise_builder = ExerciseBuilder()
test_builder = TestBuilder()
student_model_store = StudentModel()
progress_tracker = ProgressTracker(db)
difficulty_adapter = DifficultyAdapter()

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

server = Server("lesson-manager")

# Track background ingestion jobs
_ingestion_jobs: dict = {}  # course_id -> {status, progress, error}

# ---------------------------------------------------------------------------
# Helper: module file paths
# ---------------------------------------------------------------------------

COURSES_DIR = Path("E:/ai-tutor/data/courses")


def _module_dir(course_id: str, module_number: int) -> Path:
    p = COURSES_DIR / course_id / "modules" / str(module_number)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_json_file(path: Path):
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_json_file(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Background ingestion pipeline
# ---------------------------------------------------------------------------

def _run_ingestion_pipeline(course_id: str, url: str, course_title: str):
    """Full pipeline: ingest -> transcript -> curriculum -> scripts/exercises/tests/resources."""
    job = _ingestion_jobs[course_id]

    def set_progress(pct: int, status: str):
        job["progress"] = pct
        job["status_message"] = status
        logger.info(f"[{course_id}] {pct}% — {status}")

    try:
        # ------------------------------------------------------------------ #
        # 1. Ingest / get transcript
        # ------------------------------------------------------------------ #
        set_progress(5, "Downloading transcript or audio")
        transcript, course_dir_str = yt_ingester.ingest(url, course_id)

        # If yt-dlp couldn't get a subtitle file it returns the audio path token
        if transcript.startswith("__AUDIO_PATH__:"):
            audio_path = transcript.split(":", 1)[1]
            set_progress(15, "Transcribing audio with Whisper")
            transcript = whisper.transcribe(audio_path)
            # Overwrite transcript.txt with the real transcription
            (Path(course_dir_str) / "transcript.txt").write_text(transcript, encoding="utf-8")

        set_progress(25, "Transcript ready")

        # ------------------------------------------------------------------ #
        # 2. Build curriculum
        # ------------------------------------------------------------------ #
        set_progress(30, "Building curriculum with Claude")
        modules = curriculum_builder.build(transcript, course_title)

        # Persist modules to DB
        for m in modules:
            module_id = str(uuid.uuid4())
            db.execute_write(
                """INSERT OR REPLACE INTO modules
                   (id, course_id, module_number, title, description, key_concepts, prerequisites, estimated_minutes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    module_id,
                    course_id,
                    m["module_number"],
                    m["title"],
                    m.get("description", ""),
                    json.dumps(m.get("key_concepts", [])),
                    json.dumps(m.get("prerequisites", [])),
                    m.get("estimated_minutes", 20),
                ),
            )

        # Update course total_modules
        db.execute_write(
            "UPDATE courses SET total_modules=?, status='curriculum_ready' WHERE id=?",
            (len(modules), course_id),
        )

        set_progress(45, f"Curriculum built: {len(modules)} modules")

        # ------------------------------------------------------------------ #
        # 3. For each module: write script, exercises, tests, resources
        # ------------------------------------------------------------------ #
        transcript_lines = transcript.split("\n")
        total_lines = max(len(transcript_lines), 1)

        for idx, m in enumerate(modules):
            mod_num = m["module_number"]
            frac_start = idx / len(modules)
            frac_end = (idx + 1) / len(modules)

            # Approximate transcript segment for this module
            seg_start = int(frac_start * total_lines)
            seg_end = int(frac_end * total_lines)
            segment = "\n".join(transcript_lines[seg_start:seg_end])

            base_pct = 45 + int((idx / len(modules)) * 45)

            # Script
            set_progress(base_pct, f"Writing script for module {mod_num}: {m['title']}")
            script = lesson_scripter.write_script(m, segment)
            _save_json_file(_module_dir(course_id, mod_num) / "script.json", script)
            db.execute_write(
                "UPDATE modules SET script_ready=1 WHERE course_id=? AND module_number=?",
                (course_id, mod_num),
            )

            # Exercises
            exercises = exercise_builder.build(m)
            _save_json_file(_module_dir(course_id, mod_num) / "exercises.json", exercises)

            # Tests
            questions = test_builder.build(m)
            _save_json_file(_module_dir(course_id, mod_num) / "test.json", questions)

            # Resources
            resources = resource_fetcher.fetch_for_module(
                m.get("key_concepts", []), m["title"]
            )
            _save_json_file(_module_dir(course_id, mod_num) / "resources.json", resources)

        # ------------------------------------------------------------------ #
        # 4. Mark course as ready
        # ------------------------------------------------------------------ #
        db.execute_write(
            "UPDATE courses SET status='ready' WHERE id=?",
            (course_id,),
        )
        job["progress"] = 100
        job["status"] = "ready"
        job["status_message"] = "Course ingestion complete"
        set_progress(100, "Complete")

    except Exception as e:
        logger.exception(f"Ingestion pipeline failed for course {course_id}")
        job["status"] = "error"
        job["error"] = str(e)
        db.execute_write(
            "UPDATE courses SET status='error' WHERE id=?",
            (course_id,),
        )


# ---------------------------------------------------------------------------
# Tool: list_tools
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        # Ingestion
        types.Tool(
            name="ingest_course",
            description="Ingest a YouTube course URL. Downloads transcript, builds curriculum, writes scripts/exercises/tests. Returns course_id immediately; runs pipeline in background.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "YouTube URL of the course"},
                    "course_title": {"type": "string", "description": "Optional title override"},
                },
                "required": ["url"],
            },
        ),
        types.Tool(
            name="get_ingestion_status",
            description="Check the progress of a course ingestion job.",
            inputSchema={
                "type": "object",
                "properties": {
                    "course_id": {"type": "string"},
                },
                "required": ["course_id"],
            },
        ),
        # Curriculum
        types.Tool(
            name="get_curriculum",
            description="Get all modules for a course.",
            inputSchema={
                "type": "object",
                "properties": {"course_id": {"type": "string"}},
                "required": ["course_id"],
            },
        ),
        types.Tool(
            name="get_module_script",
            description="Get the step-by-step lesson script for a module.",
            inputSchema={
                "type": "object",
                "properties": {
                    "course_id": {"type": "string"},
                    "module_number": {"type": "integer"},
                },
                "required": ["course_id", "module_number"],
            },
        ),
        types.Tool(
            name="get_module_exercises",
            description="Get hands-on exercises for a module.",
            inputSchema={
                "type": "object",
                "properties": {
                    "course_id": {"type": "string"},
                    "module_number": {"type": "integer"},
                },
                "required": ["course_id", "module_number"],
            },
        ),
        types.Tool(
            name="get_module_resources",
            description="Get curated online resources for a module.",
            inputSchema={
                "type": "object",
                "properties": {
                    "course_id": {"type": "string"},
                    "module_number": {"type": "integer"},
                },
                "required": ["course_id", "module_number"],
            },
        ),
        # Testing
        types.Tool(
            name="get_module_test",
            description="Get test questions for a module, calibrated for a specific student.",
            inputSchema={
                "type": "object",
                "properties": {
                    "course_id": {"type": "string"},
                    "module_number": {"type": "integer"},
                    "student_id": {"type": "string"},
                },
                "required": ["course_id", "module_number", "student_id"],
            },
        ),
        types.Tool(
            name="evaluate_answer",
            description="Evaluate a student's answer to a test question using Claude Haiku.",
            inputSchema={
                "type": "object",
                "properties": {
                    "question_id": {"type": "string"},
                    "student_answer": {"type": "string"},
                    "answer_type": {
                        "type": "string",
                        "enum": ["multiple_choice", "short_answer", "practical_task", "verbal"],
                    },
                    "question_text": {"type": "string", "description": "The original question text"},
                    "correct_answer": {"type": "string", "description": "The expected/model answer"},
                    "rubric": {"type": "string", "description": "Grading rubric"},
                },
                "required": ["question_id", "student_answer", "answer_type", "question_text", "correct_answer"],
            },
        ),
        # Student model
        types.Tool(
            name="get_student_model",
            description="Get the full student knowledge model.",
            inputSchema={
                "type": "object",
                "properties": {"student_id": {"type": "string"}},
                "required": ["student_id"],
            },
        ),
        types.Tool(
            name="update_student_model",
            description="Update fields in a student's model (deep merge). Pass updates as a JSON string.",
            inputSchema={
                "type": "object",
                "properties": {
                    "student_id": {"type": "string"},
                    "updates": {"type": "string", "description": "JSON string of updates to merge"},
                },
                "required": ["student_id", "updates"],
            },
        ),
        types.Tool(
            name="get_next_module",
            description="Get the next module number a student should study.",
            inputSchema={
                "type": "object",
                "properties": {
                    "course_id": {"type": "string"},
                    "student_id": {"type": "string"},
                },
                "required": ["course_id", "student_id"],
            },
        ),
        # Pre-generation
        types.Tool(
            name="pregenerate_module",
            description="Pre-generate script, test, and exercises for a module in the background.",
            inputSchema={
                "type": "object",
                "properties": {
                    "course_id": {"type": "string"},
                    "module_number": {"type": "integer"},
                    "student_id": {"type": "string"},
                },
                "required": ["course_id", "module_number", "student_id"],
            },
        ),
        # Progress
        types.Tool(
            name="record_module_complete",
            description="Record that a student has completed a module with a given test score.",
            inputSchema={
                "type": "object",
                "properties": {
                    "student_id": {"type": "string"},
                    "course_id": {"type": "string"},
                    "module_number": {"type": "integer"},
                    "test_score": {"type": "number", "description": "Score between 0.0 and 1.0"},
                },
                "required": ["student_id", "course_id", "module_number", "test_score"],
            },
        ),
        types.Tool(
            name="get_progress",
            description="Get a student's progress through a course.",
            inputSchema={
                "type": "object",
                "properties": {
                    "student_id": {"type": "string"},
                    "course_id": {"type": "string"},
                },
                "required": ["student_id", "course_id"],
            },
        ),
        types.Tool(
            name="update_knowledge",
            description="Update a student's confidence for a specific concept based on test result.",
            inputSchema={
                "type": "object",
                "properties": {
                    "student_id": {"type": "string"},
                    "concept": {"type": "string"},
                    "correct": {"type": "boolean"},
                },
                "required": ["student_id", "concept", "correct"],
            },
        ),
        types.Tool(
            name="get_student_summary",
            description="Get a concise text summary of a student's knowledge state for use in prompts.",
            inputSchema={
                "type": "object",
                "properties": {"student_id": {"type": "string"}},
                "required": ["student_id"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool: call_tool
# ---------------------------------------------------------------------------

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Dispatch tool calls."""

    def ok(data) -> list[types.TextContent]:
        return [types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))]

    def err(msg: str) -> list[types.TextContent]:
        return [types.TextContent(type="text", text=json.dumps({"error": msg}))]

    # ---------------------------------------------------------------------- #
    # ingest_course
    # ---------------------------------------------------------------------- #
    if name == "ingest_course":
        url = arguments["url"]
        course_title = arguments.get("course_title", "")

        # Try to get title from video info if not supplied
        if not course_title:
            try:
                info = yt_ingester.get_video_info(url)
                course_title = info.get("title", "Untitled Course")
            except Exception:
                course_title = "Untitled Course"

        course_id = str(uuid.uuid4())

        # Insert into DB
        db.execute_write(
            "INSERT INTO courses (id, title, source_url, status) VALUES (?, ?, ?, 'ingesting')",
            (course_id, course_title, url),
        )

        # Set up job tracker
        _ingestion_jobs[course_id] = {
            "status": "ingesting",
            "progress": 0,
            "status_message": "Starting ingestion pipeline",
            "error": None,
        }

        # Launch background thread
        t = threading.Thread(
            target=_run_ingestion_pipeline,
            args=(course_id, url, course_title),
            daemon=True,
            name=f"ingest-{course_id[:8]}",
        )
        t.start()

        return ok({"course_id": course_id, "status": "ingesting", "title": course_title})

    # ---------------------------------------------------------------------- #
    # get_ingestion_status
    # ---------------------------------------------------------------------- #
    elif name == "get_ingestion_status":
        course_id = arguments["course_id"]

        job = _ingestion_jobs.get(course_id)
        if job is None:
            # Check DB for historical status
            row = db.execute_one("SELECT status, title FROM courses WHERE id=?", (course_id,))
            if row:
                return ok({"course_id": course_id, "status": row["status"], "progress": 100 if row["status"] == "ready" else 0})
            return err(f"Unknown course_id: {course_id}")

        return ok({
            "course_id": course_id,
            "status": job["status"],
            "progress": job["progress"],
            "status_message": job.get("status_message", ""),
            "error": job.get("error"),
        })

    # ---------------------------------------------------------------------- #
    # get_curriculum
    # ---------------------------------------------------------------------- #
    elif name == "get_curriculum":
        course_id = arguments["course_id"]
        rows = db.execute(
            "SELECT * FROM modules WHERE course_id=? ORDER BY module_number",
            (course_id,),
        )
        if not rows:
            return err(f"No modules found for course_id: {course_id}")
        # Deserialise JSON columns
        for r in rows:
            r["key_concepts"] = json.loads(r.get("key_concepts") or "[]")
            r["prerequisites"] = json.loads(r.get("prerequisites") or "[]")
        return ok(rows)

    # ---------------------------------------------------------------------- #
    # get_module_script
    # ---------------------------------------------------------------------- #
    elif name == "get_module_script":
        course_id = arguments["course_id"]
        module_number = int(arguments["module_number"])
        path = _module_dir(course_id, module_number) / "script.json"
        data = _load_json_file(path)
        if data is None:
            return err(f"Script not ready for module {module_number} in course {course_id}")
        return ok(data)

    # ---------------------------------------------------------------------- #
    # get_module_exercises
    # ---------------------------------------------------------------------- #
    elif name == "get_module_exercises":
        course_id = arguments["course_id"]
        module_number = int(arguments["module_number"])
        path = _module_dir(course_id, module_number) / "exercises.json"
        data = _load_json_file(path)
        if data is None:
            return err(f"Exercises not ready for module {module_number} in course {course_id}")
        return ok(data)

    # ---------------------------------------------------------------------- #
    # get_module_resources
    # ---------------------------------------------------------------------- #
    elif name == "get_module_resources":
        course_id = arguments["course_id"]
        module_number = int(arguments["module_number"])
        path = _module_dir(course_id, module_number) / "resources.json"
        data = _load_json_file(path)
        if data is None:
            return err(f"Resources not ready for module {module_number} in course {course_id}")
        return ok(data)

    # ---------------------------------------------------------------------- #
    # get_module_test
    # ---------------------------------------------------------------------- #
    elif name == "get_module_test":
        course_id = arguments["course_id"]
        module_number = int(arguments["module_number"])
        student_id = arguments["student_id"]

        path = _module_dir(course_id, module_number) / "test.json"
        questions = _load_json_file(path)
        if questions is None:
            return err(f"Test not ready for module {module_number} in course {course_id}")

        student = student_model_store.get(student_id)
        calibrated = test_builder.calibrate_for_student(questions, student)
        return ok(calibrated)

    # ---------------------------------------------------------------------- #
    # evaluate_answer
    # ---------------------------------------------------------------------- #
    elif name == "evaluate_answer":
        question_id = arguments["question_id"]
        student_answer = arguments["student_answer"]
        answer_type = arguments["answer_type"]
        question_text = arguments["question_text"]
        correct_answer = arguments["correct_answer"]
        rubric = arguments.get("rubric", "Award full credit for a correct and complete answer.")

        # Multiple choice: deterministic check
        if answer_type == "multiple_choice":
            # Accept first letter or full answer starting with correct letter
            student_letter = student_answer.strip().upper()[:1]
            correct_letter = correct_answer.strip().upper()[:1]
            is_correct = student_letter == correct_letter
            return ok({
                "question_id": question_id,
                "correct": is_correct,
                "score": 1.0 if is_correct else 0.0,
                "explanation": f"The correct answer is {correct_letter}. Your answer was {student_letter}." if not is_correct else f"Correct! The answer is {correct_letter}.",
            })

        # Use Claude Haiku for open-ended answers
        haiku_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

        prompt = (
            f"You are grading a student's answer. Respond ONLY with a JSON object with these fields: "
            f"correct (boolean), score (float 0.0-1.0), explanation (string, 1-2 sentences of feedback).\n\n"
            f"Question: {question_text}\n"
            f"Answer type: {answer_type}\n"
            f"Expected answer: {correct_answer}\n"
            f"Rubric: {rubric}\n"
            f"Student's answer: {student_answer}\n\n"
            f"Grade the student's answer and return JSON only."
        )

        response = haiku_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # Fallback: assume partial credit
            result = {
                "correct": False,
                "score": 0.5,
                "explanation": "Unable to evaluate automatically. Manual review recommended.",
            }

        result["question_id"] = question_id
        return ok(result)

    # ---------------------------------------------------------------------- #
    # get_student_model
    # ---------------------------------------------------------------------- #
    elif name == "get_student_model":
        student_id = arguments["student_id"]
        model = student_model_store.get(student_id)
        return ok(model)

    # ---------------------------------------------------------------------- #
    # update_student_model
    # ---------------------------------------------------------------------- #
    elif name == "update_student_model":
        student_id = arguments["student_id"]
        updates_str = arguments["updates"]
        try:
            updates = json.loads(updates_str)
        except json.JSONDecodeError as e:
            return err(f"Invalid JSON in updates: {e}")
        updated = student_model_store.update(student_id, updates)
        return ok(updated)

    # ---------------------------------------------------------------------- #
    # get_next_module
    # ---------------------------------------------------------------------- #
    elif name == "get_next_module":
        course_id = arguments["course_id"]
        student_id = arguments["student_id"]
        next_mod = progress_tracker.get_next_module(course_id, student_id)
        return ok({"course_id": course_id, "student_id": student_id, "next_module": next_mod})

    # ---------------------------------------------------------------------- #
    # pregenerate_module
    # ---------------------------------------------------------------------- #
    elif name == "pregenerate_module":
        course_id = arguments["course_id"]
        module_number = int(arguments["module_number"])
        student_id = arguments["student_id"]

        def _pregenerate():
            try:
                logger.info(f"Pre-generating module {module_number} for course {course_id}")

                # Check if already done
                script_path = _module_dir(course_id, module_number) / "script.json"
                test_path = _module_dir(course_id, module_number) / "test.json"
                exercises_path = _module_dir(course_id, module_number) / "exercises.json"

                # Load module metadata from DB
                row = db.execute_one(
                    "SELECT * FROM modules WHERE course_id=? AND module_number=?",
                    (course_id, module_number),
                )
                if row is None:
                    logger.warning(f"Module {module_number} not found in DB for course {course_id}")
                    return

                m = dict(row)
                m["key_concepts"] = json.loads(m.get("key_concepts") or "[]")
                m["prerequisites"] = json.loads(m.get("prerequisites") or "[]")

                # Load transcript segment
                transcript_path = COURSES_DIR / course_id / "transcript.txt"
                segment = ""
                if transcript_path.exists():
                    lines = transcript_path.read_text(encoding="utf-8").split("\n")
                    total = len(lines)
                    # Crude estimate: modules are roughly equal slices
                    total_mods_row = db.execute_one("SELECT total_modules FROM courses WHERE id=?", (course_id,))
                    total_mods = total_mods_row["total_modules"] if total_mods_row else 1
                    frac = (module_number - 1) / max(total_mods, 1)
                    frac_end = module_number / max(total_mods, 1)
                    seg_start = int(frac * total)
                    seg_end = int(frac_end * total)
                    segment = "\n".join(lines[seg_start:seg_end])

                # Script
                if not script_path.exists():
                    script = lesson_scripter.write_script(m, segment)
                    _save_json_file(script_path, script)
                    db.execute_write(
                        "UPDATE modules SET script_ready=1 WHERE course_id=? AND module_number=?",
                        (course_id, module_number),
                    )

                # Test
                if not test_path.exists():
                    questions = test_builder.build(m)
                    _save_json_file(test_path, questions)

                # Exercises
                if not exercises_path.exists():
                    exercises = exercise_builder.build(m)
                    _save_json_file(exercises_path, exercises)

                logger.info(f"Pre-generation complete for module {module_number}, course {course_id}")

            except Exception as e:
                logger.exception(f"Pre-generation failed for module {module_number}, course {course_id}: {e}")

        t = threading.Thread(target=_pregenerate, daemon=True, name=f"pregen-{course_id[:8]}-{module_number}")
        t.start()

        return ok({"status": "pregeneration_started", "course_id": course_id, "module_number": module_number})

    # ---------------------------------------------------------------------- #
    # record_module_complete
    # ---------------------------------------------------------------------- #
    elif name == "record_module_complete":
        student_id = arguments["student_id"]
        course_id = arguments["course_id"]
        module_number = int(arguments["module_number"])
        test_score = float(arguments["test_score"])

        progress_tracker.record_module_complete(student_id, course_id, module_number, test_score)

        # Update student model with new score
        student = student_model_store.get(student_id)
        scores = student.get("current_course", {}).get("test_scores", {})
        scores[str(module_number)] = test_score

        completed = student.get("current_course", {}).get("completed_modules", [])
        if module_number not in completed:
            completed.append(module_number)

        student_model_store.update(student_id, {
            "current_course": {
                "course_id": course_id,
                "test_scores": scores,
                "completed_modules": completed,
                "current_module": module_number + 1,
            }
        })

        # Check if student should revisit
        should_revisit = difficulty_adapter.should_revisit(student, module_number)
        recommended_pace = difficulty_adapter.get_recommended_pace(
            student_model_store.get(student_id)
        )

        return ok({
            "recorded": True,
            "student_id": student_id,
            "module_number": module_number,
            "test_score": test_score,
            "should_revisit": should_revisit,
            "recommended_pace": recommended_pace,
        })

    # ---------------------------------------------------------------------- #
    # get_progress
    # ---------------------------------------------------------------------- #
    elif name == "get_progress":
        student_id = arguments["student_id"]
        course_id = arguments["course_id"]
        progress = progress_tracker.get_progress(student_id, course_id)
        return ok(progress)

    # ---------------------------------------------------------------------- #
    # update_knowledge
    # ---------------------------------------------------------------------- #
    elif name == "update_knowledge":
        student_id = arguments["student_id"]
        concept = arguments["concept"]
        correct = bool(arguments["correct"])
        student_model_store.update_knowledge(student_id, concept, correct)
        model = student_model_store.get(student_id)
        return ok({
            "updated": True,
            "concept": concept,
            "correct": correct,
            "new_confidence": model.get("known_concepts", {}).get(concept, 0.5),
            "weak_areas": model.get("weak_areas", []),
        })

    # ---------------------------------------------------------------------- #
    # get_student_summary
    # ---------------------------------------------------------------------- #
    elif name == "get_student_summary":
        student_id = arguments["student_id"]
        summary = student_model_store.get_summary(student_id)
        return ok({"student_id": student_id, "summary": summary})

    else:
        return err(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Health-check HTTP server on port 9105
# ---------------------------------------------------------------------------

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/health", "/"):
            body = json.dumps({"status": "ok", "server": "lesson-manager"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress access logs


def _start_health_server():
    try:
        httpd = HTTPServer(("0.0.0.0", 9105), HealthHandler)
        logger.info("Health server listening on port 9105")
        httpd.serve_forever()
    except Exception as e:
        logger.warning(f"Health server failed to start: {e}")


# ---------------------------------------------------------------------------
# Startup + entry point
# ---------------------------------------------------------------------------

async def startup():
    db.initialize()
    whisper.initialize()
    logger.info("Lesson manager ready")


async def main():
    await startup()

    # Start health server in background thread
    health_thread = threading.Thread(target=_start_health_server, daemon=True, name="health-server")
    health_thread.start()

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
