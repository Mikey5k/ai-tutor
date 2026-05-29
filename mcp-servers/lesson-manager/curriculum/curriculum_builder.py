import anthropic
import json
import os
from pathlib import Path
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)


class CurriculumBuilder:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        self.model = "claude-sonnet-4-6"

    def build(self, transcript: str, course_title: str) -> List[Dict]:
        """Parse transcript into modules. Return list of module dicts."""
        system_prompt = (
            "You are an expert instructional designer analyzing a course transcript. "
            "Your task is to break the content into logical, self-contained learning modules. "
            "Return ONLY a valid JSON array — no prose, no markdown fences, no explanation. "
            "Each element must have exactly these fields: "
            "module_number (integer starting at 1), "
            "title (string), "
            "description (string, 1-2 sentences), "
            "key_concepts (array of strings, 3-8 items), "
            "prerequisites (array of module_number integers that must be completed first), "
            "estimated_minutes (integer)."
        )

        user_content = (
            f"Course title: {course_title}\n\n"
            f"Transcript (may be truncated):\n{transcript[:50000]}\n\n"
            "Return the module breakdown as a JSON array."
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )

        raw_text = response.content[0].text.strip()

        # Strip any accidental markdown fences
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
        raw_text = raw_text.strip()

        try:
            modules = json.loads(raw_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse curriculum JSON: {e}\nRaw: {raw_text[:500]}")
            # Return a single fallback module so the pipeline can continue
            modules = [
                {
                    "module_number": 1,
                    "title": course_title,
                    "description": "Full course content.",
                    "key_concepts": [],
                    "prerequisites": [],
                    "estimated_minutes": 30,
                }
            ]

        # Normalise field types
        for m in modules:
            m["module_number"] = int(m.get("module_number", 1))
            m["estimated_minutes"] = int(m.get("estimated_minutes", 20))
            m["key_concepts"] = m.get("key_concepts") or []
            m["prerequisites"] = m.get("prerequisites") or []

        return modules
