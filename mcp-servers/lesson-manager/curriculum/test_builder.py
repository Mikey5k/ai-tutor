import anthropic
import json
import os
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


class TestBuilder:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        self.model = "claude-haiku-4-5-20251001"

    def build(self, module: Dict) -> List[Dict]:
        """Generate test questions. Return list of {id, type, question, options, correct_answer, rubric, difficulty}."""
        module_number = module.get("module_number", 1)
        title = module.get("title", "")
        key_concepts = module.get("key_concepts", [])
        description = module.get("description", "")

        system_prompt = (
            "You are an expert educator creating assessment questions for a learning module. "
            "Return ONLY a valid JSON array of 5-8 question objects — no markdown fences, no prose. "
            "Include this mix: 3 multiple_choice, 2 short_answer, 1 practical_task, 1-2 verbal. "
            "Spread questions across 3 difficulty levels: beginner, intermediate, advanced. "
            "Each question object must have: "
            "id (string like 'q_01'), "
            "type (string: 'multiple_choice' | 'short_answer' | 'practical_task' | 'verbal'), "
            "question (string, the question text), "
            "options (array of strings for multiple_choice, empty array otherwise), "
            "correct_answer (string — for multiple_choice give the letter A/B/C/D and text; "
            "for others give a model answer), "
            "rubric (string, grading criteria for partial credit), "
            "difficulty (string: 'beginner' | 'intermediate' | 'advanced'), "
            "concept_tested (string, which key concept this tests), "
            "points (int, 1 for beginner, 2 for intermediate, 3 for advanced)."
        )

        user_content = (
            f"Module {module_number}: {title}\n"
            f"Description: {description}\n"
            f"Key concepts: {', '.join(key_concepts)}\n\n"
            "Generate 5-8 assessment questions as a JSON array."
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )

        raw_text = response.content[0].text.strip()

        # Strip markdown fences
        if raw_text.startswith("```"):
            parts = raw_text.split("```")
            raw_text = parts[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
        raw_text = raw_text.strip()

        try:
            questions = json.loads(raw_text)
            if not isinstance(questions, list):
                questions = [questions]
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse test JSON for module {module_number}: {e}")
            questions = self._fallback_questions(module_number, title, key_concepts)

        # Normalise
        for i, q in enumerate(questions):
            if "id" not in q:
                q["id"] = f"q_{i + 1:02d}"
            q["options"] = q.get("options") or []
            q["points"] = int(q.get("points", 1))
            q["rubric"] = q.get("rubric", "Award full points for correct answer.")

        return questions

    def calibrate_for_student(self, questions: List[Dict], student_model: Dict) -> List[Dict]:
        """Filter/reorder questions based on student level."""
        level = student_model.get("overall_level", "beginner")
        weak_areas = set(student_model.get("weak_areas", []))

        level_priority = {
            "beginner": ["beginner", "intermediate"],
            "intermediate": ["intermediate", "beginner", "advanced"],
            "advanced": ["advanced", "intermediate", "beginner"],
        }
        priority_order = level_priority.get(level, ["beginner", "intermediate", "advanced"])

        def sort_key(q: Dict):
            diff = q.get("difficulty", "beginner")
            concept = q.get("concept_tested", "")
            # Prioritise weak areas by putting them first regardless of difficulty
            weak_boost = -10 if any(w.lower() in concept.lower() for w in weak_areas) else 0
            diff_rank = priority_order.index(diff) if diff in priority_order else 99
            return weak_boost + diff_rank

        sorted_questions = sorted(questions, key=sort_key)

        # For beginners cap at 6 questions; advanced students get all
        limits = {"beginner": 5, "intermediate": 6, "advanced": len(sorted_questions)}
        limit = limits.get(level, 6)

        return sorted_questions[:limit]

    def _fallback_questions(self, module_number: int, title: str, key_concepts: List[str]) -> List[Dict]:
        """Return minimal fallback questions when API parse fails."""
        concepts_str = ", ".join(key_concepts[:2]) if key_concepts else title
        return [
            {
                "id": "q_01",
                "type": "short_answer",
                "question": f"Describe the main purpose of {title} in your own words.",
                "options": [],
                "correct_answer": f"A clear explanation of {title} covering key concepts: {concepts_str}.",
                "rubric": "Award full credit for a clear, accurate explanation. Partial credit for partial understanding.",
                "difficulty": "beginner",
                "concept_tested": key_concepts[0] if key_concepts else title,
                "points": 1,
            },
            {
                "id": "q_02",
                "type": "multiple_choice",
                "question": f"Which of the following best describes {key_concepts[0] if key_concepts else title}?",
                "options": [
                    "A) It is a fundamental concept used to organise related code.",
                    "B) It is only used in advanced programming scenarios.",
                    "C) It replaces the need for any other programming constructs.",
                    "D) It is unrelated to the module topic.",
                ],
                "correct_answer": "A",
                "rubric": "Full credit for A only.",
                "difficulty": "beginner",
                "concept_tested": key_concepts[0] if key_concepts else title,
                "points": 1,
            },
            {
                "id": "q_03",
                "type": "practical_task",
                "question": f"Create a working example that demonstrates your understanding of {concepts_str}.",
                "options": [],
                "correct_answer": "A functional implementation that correctly uses the concepts covered in the module.",
                "rubric": "Award points based on: correctness (50%), code quality (25%), explanation (25%).",
                "difficulty": "intermediate",
                "concept_tested": key_concepts[1] if len(key_concepts) > 1 else title,
                "points": 2,
            },
        ]
