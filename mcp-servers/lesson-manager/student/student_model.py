import json
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

STUDENTS_DIR = Path("E:/ai-tutor/data/students")


class StudentModel:
    def __init__(self, students_dir: Path = STUDENTS_DIR):
        self.students_dir = students_dir
        self.students_dir.mkdir(parents=True, exist_ok=True)

    def _student_path(self, student_id: str) -> Path:
        return self.students_dir / f"{student_id}.json"

    def _default_model(self, student_id: str) -> Dict:
        """Return default student model dict."""
        return {
            "student_id": student_id,
            "name": student_id,
            "overall_level": "beginner",
            "pace_preference": "normal",
            "learning_style_signals": {
                "asks_many_questions": False,
                "prefers_examples": False,
                "strong_on_theory": False,
                "strong_on_practice": False,
            },
            "known_concepts": {},
            "weak_areas": [],
            "interruption_topics": [],
            "current_course": {
                "course_id": None,
                "current_module": 1,
                "completed_modules": [],
                "test_scores": {},
            },
            "session_stats": {
                "average_test_score": 0.0,
                "total_interruptions": 0,
                "total_sessions": 0,
                "total_minutes": 0,
            },
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }

    def _save(self, student_id: str, model: Dict):
        """Save student model to JSON file."""
        model["updated_at"] = datetime.utcnow().isoformat()
        path = self._student_path(student_id)
        with path.open("w", encoding="utf-8") as f:
            json.dump(model, f, ensure_ascii=False, indent=2)

    def get(self, student_id: str) -> Dict:
        """Load student model from file. Create default if not exists."""
        path = self._student_path(student_id)
        if not path.exists():
            model = self._default_model(student_id)
            self._save(student_id, model)
            return model
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Corrupted student file for {student_id}, resetting: {e}")
            model = self._default_model(student_id)
            self._save(student_id, model)
            return model

    def update(self, student_id: str, updates: Dict) -> Dict:
        """Merge updates into student model. Return updated model."""
        model = self.get(student_id)

        def deep_merge(base: dict, patch: dict) -> dict:
            for k, v in patch.items():
                if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                    deep_merge(base[k], v)
                else:
                    base[k] = v
            return base

        deep_merge(model, updates)
        self._save(student_id, model)
        return model

    def update_knowledge(self, student_id: str, concept: str, correct: bool):
        """Update confidence for a concept based on test result."""
        model = self.get(student_id)
        known = model.get("known_concepts", {})
        current_confidence = known.get(concept, 0.5)

        if correct:
            new_confidence = min(1.0, current_confidence + 0.1)
        else:
            new_confidence = max(0.0, current_confidence - 0.15)

        known[concept] = round(new_confidence, 4)
        model["known_concepts"] = known

        # Maintain weak_areas list
        weak_areas: List[str] = model.get("weak_areas", [])
        if new_confidence < 0.4 and concept not in weak_areas:
            weak_areas.append(concept)
        elif new_confidence >= 0.7 and concept in weak_areas:
            weak_areas.remove(concept)
        model["weak_areas"] = weak_areas

        self._save(student_id, model)

    def get_summary(self, student_id: str) -> str:
        """Return 200-token-max summary string for Claude context."""
        model = self.get(student_id)
        name = model.get("name", student_id)
        level = model.get("overall_level", "beginner")
        pace = model.get("pace_preference", "normal")
        weak_areas = model.get("weak_areas", [])
        current_module = model.get("current_course", {}).get("current_module", 1)
        known_concepts: Dict = model.get("known_concepts", {})

        strong_concepts = [c for c, conf in known_concepts.items() if conf >= 0.7]

        strong_str = ", ".join(strong_concepts[:5]) if strong_concepts else "none yet"
        weak_str = ", ".join(weak_areas[:5]) if weak_areas else "none identified"

        summary = (
            f"Student {name}, {level} level. "
            f"Current pace: {pace}. "
            f"Strong in: {strong_str}. "
            f"Weak in: {weak_str}. "
            f"Current module: {current_module}."
        )
        return summary

    def reset(self, student_id: str):
        """Reset student to fresh state."""
        model = self._default_model(student_id)
        # Preserve the name if the student previously existed
        old_path = self._student_path(student_id)
        if old_path.exists():
            try:
                old = json.loads(old_path.read_text(encoding="utf-8"))
                model["name"] = old.get("name", student_id)
            except Exception:
                pass
        self._save(student_id, model)
