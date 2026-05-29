from typing import Dict, List
import logging

logger = logging.getLogger(__name__)

_DIFFICULTY_RANK = {"beginner": 0, "intermediate": 1, "advanced": 2}


class DifficultyAdapter:
    def get_recommended_pace(self, student_model: Dict) -> str:
        """Return 'slow'|'normal'|'fast' based on recent scores."""
        scores_dict: Dict = student_model.get("current_course", {}).get("test_scores", {})
        scores = list(scores_dict.values()) if isinstance(scores_dict, dict) else []

        # Use the last 3 scores
        recent = scores[-3:] if len(scores) >= 3 else scores

        if not recent:
            # No scores yet — use stated pace preference
            return student_model.get("pace_preference", "normal")

        avg = sum(recent) / len(recent)

        if avg >= 0.85:
            return "fast"
        elif avg >= 0.60:
            return "normal"
        else:
            return "slow"

    def should_revisit(self, student_model: Dict, module_number: int) -> bool:
        """Return True if student scored below 70% and should revisit the module."""
        scores_dict: Dict = student_model.get("current_course", {}).get("test_scores", {})

        # scores_dict may be keyed by module number as str or int
        score = scores_dict.get(str(module_number)) or scores_dict.get(module_number)

        if score is None:
            # Not yet attempted — no revisit needed
            return False

        return float(score) < 0.70

    def filter_questions(self, questions: List[Dict], student_model: Dict) -> List[Dict]:
        """Select appropriate difficulty questions for the student's level."""
        level = student_model.get("overall_level", "beginner")
        weak_areas = set(student_model.get("weak_areas", []))
        pace = self.get_recommended_pace(student_model)

        # Determine which difficulties to include based on pace and level
        include_difficulties = {"slow": {"beginner"}, "normal": {"beginner", "intermediate"}, "fast": {"beginner", "intermediate", "advanced"}}
        allowed = include_difficulties.get(pace, {"beginner", "intermediate"})

        # Always include beginner questions
        allowed.add("beginner")

        filtered = [q for q in questions if q.get("difficulty", "beginner") in allowed]

        # Boost weak-area questions to the front
        def sort_key(q: Dict) -> tuple:
            concept = q.get("concept_tested", "")
            in_weak = any(w.lower() in concept.lower() for w in weak_areas)
            diff_rank = _DIFFICULTY_RANK.get(q.get("difficulty", "beginner"), 0)
            return (0 if in_weak else 1, diff_rank)

        return sorted(filtered, key=sort_key)
