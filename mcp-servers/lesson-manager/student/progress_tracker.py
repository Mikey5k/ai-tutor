import json
from typing import Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ProgressTracker:
    def __init__(self, db):
        self.db = db

    def record_module_complete(
        self,
        student_id: str,
        course_id: str,
        module_number: int,
        test_score: float,
    ):
        """Record that a student has completed a module with the given test score."""
        existing = self.db.execute_one(
            "SELECT attempts FROM student_progress WHERE student_id=? AND course_id=? AND module_number=?",
            (student_id, course_id, module_number),
        )

        if existing:
            attempts = existing["attempts"] + 1
            self.db.execute_write(
                """UPDATE student_progress
                   SET completed=1, test_score=?, attempts=?, completed_at=?
                   WHERE student_id=? AND course_id=? AND module_number=?""",
                (
                    test_score,
                    attempts,
                    datetime.utcnow().isoformat(),
                    student_id,
                    course_id,
                    module_number,
                ),
            )
        else:
            self.db.execute_write(
                """INSERT INTO student_progress
                   (student_id, course_id, module_number, completed, test_score, attempts, completed_at)
                   VALUES (?, ?, ?, 1, ?, 1, ?)""",
                (
                    student_id,
                    course_id,
                    module_number,
                    test_score,
                    datetime.utcnow().isoformat(),
                ),
            )

        # Log completion event
        self.log_event(
            student_id,
            "module_complete",
            {
                "course_id": course_id,
                "module_number": module_number,
                "test_score": test_score,
            },
        )

    def get_progress(self, student_id: str, course_id: str) -> Dict:
        """Return progress summary for a student in a course."""
        rows = self.db.execute(
            """SELECT module_number, completed, test_score, attempts, completed_at
               FROM student_progress
               WHERE student_id=? AND course_id=?
               ORDER BY module_number""",
            (student_id, course_id),
        )

        course_row = self.db.execute_one(
            "SELECT total_modules, title FROM courses WHERE id=?",
            (course_id,),
        )

        total_modules = course_row["total_modules"] if course_row else 0
        completed_modules = [r for r in rows if r["completed"]]
        scores = [r["test_score"] for r in completed_modules if r["test_score"] is not None]
        avg_score = round(sum(scores) / len(scores), 3) if scores else 0.0

        return {
            "student_id": student_id,
            "course_id": course_id,
            "course_title": course_row["title"] if course_row else course_id,
            "total_modules": total_modules,
            "completed_count": len(completed_modules),
            "completion_percentage": round(
                len(completed_modules) / total_modules * 100, 1
            ) if total_modules else 0.0,
            "average_test_score": avg_score,
            "modules": rows,
        }

    def get_next_module(self, course_id: str, student_id: str) -> int:
        """Return the next module number the student should work on."""
        # Get all completed module numbers for this student+course
        completed = self.db.execute(
            """SELECT module_number FROM student_progress
               WHERE student_id=? AND course_id=? AND completed=1
               ORDER BY module_number""",
            (student_id, course_id),
        )
        completed_numbers = {r["module_number"] for r in completed}

        # Get total modules in course
        course_row = self.db.execute_one(
            "SELECT total_modules FROM courses WHERE id=?",
            (course_id,),
        )
        total_modules = course_row["total_modules"] if course_row else 1

        # Find first incomplete module
        for n in range(1, total_modules + 1):
            if n not in completed_numbers:
                return n

        # All done — return total + 1 as sentinel
        return total_modules + 1

    def log_event(self, student_id: str, event_type: str, event_data: Dict):
        """Log a session event to the database."""
        self.db.execute_write(
            """INSERT INTO session_events (student_id, event_type, event_data)
               VALUES (?, ?, ?)""",
            (student_id, event_type, json.dumps(event_data)),
        )
