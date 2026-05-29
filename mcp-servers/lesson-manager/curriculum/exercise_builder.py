import anthropic
import json
import os
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


class ExerciseBuilder:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        self.model = "claude-haiku-4-5-20251001"

    def build(self, module: Dict) -> List[Dict]:
        """Generate 2-3 exercises for module. Return list of {instruction, expected_outcome, hints, solution}."""
        module_number = module.get("module_number", 1)
        title = module.get("title", "")
        key_concepts = module.get("key_concepts", [])
        description = module.get("description", "")

        system_prompt = (
            "You are an expert instructional designer creating hands-on exercises for a learning module. "
            "Return ONLY a valid JSON array of 2-3 exercise objects — no markdown fences, no prose. "
            "Each exercise must have these fields: "
            "id (string like 'ex_01'), "
            "title (string, short exercise name), "
            "instruction (string, clear step-by-step instructions for the student), "
            "expected_outcome (string, what the student should produce or achieve), "
            "hints (array of strings, 2-3 progressive hints from subtle to direct), "
            "solution (string, complete worked solution or example answer), "
            "difficulty (string: 'beginner' | 'intermediate' | 'advanced'), "
            "estimated_minutes (int). "
            "Make exercises practical and directly applicable to the module concepts."
        )

        user_content = (
            f"Module {module_number}: {title}\n"
            f"Description: {description}\n"
            f"Key concepts: {', '.join(key_concepts)}\n\n"
            "Generate 2-3 hands-on exercises for this module as a JSON array."
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=3000,
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
            exercises = json.loads(raw_text)
            if not isinstance(exercises, list):
                exercises = [exercises]
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse exercises JSON for module {module_number}: {e}")
            exercises = [
                {
                    "id": "ex_01",
                    "title": f"Practice: {title}",
                    "instruction": f"Apply the concepts from this module: {', '.join(key_concepts)}. "
                                   "Create a working example that demonstrates your understanding.",
                    "expected_outcome": "A working implementation demonstrating the key concepts.",
                    "hints": [
                        f"Start by reviewing {key_concepts[0] if key_concepts else 'the main concept'}.",
                        "Break the problem into smaller steps.",
                        "Refer to the lesson examples for guidance.",
                    ],
                    "solution": "Solution will vary based on implementation.",
                    "difficulty": "beginner",
                    "estimated_minutes": 15,
                }
            ]

        # Normalise
        for i, ex in enumerate(exercises):
            if "id" not in ex:
                ex["id"] = f"ex_{i + 1:02d}"
            ex["hints"] = ex.get("hints") or []
            ex["estimated_minutes"] = int(ex.get("estimated_minutes", 15))

        return exercises
