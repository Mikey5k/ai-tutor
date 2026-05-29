import anthropic
import json
import os
from pathlib import Path
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

SCRIPT_STEP_TYPES = [
    "speak",
    "mouse_move",
    "click",
    "type",
    "open_app",
    "navigate",
    "highlight",
    "pause",
    "check_student",
    "demo",
]


class LessonScripter:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        self.model = "claude-sonnet-4-6"

    def write_script(self, module: Dict, transcript_segment: str) -> Dict:
        """Write step-by-step lesson script for a module. Return script dict."""
        module_number = module.get("module_number", 1)
        title = module.get("title", "")
        key_concepts = module.get("key_concepts", [])
        estimated_minutes = module.get("estimated_minutes", 20)

        system_prompt = (
            "You are an expert AI tutor lesson designer. "
            "Write a detailed, step-by-step lesson script that an AI tutor can execute. "
            "Return ONLY a valid JSON object — no markdown fences, no prose. "
            "The JSON must have these top-level fields: "
            "module_number (int), title (str), estimated_minutes (int), steps (array). "
            f"Valid step types are: {', '.join(SCRIPT_STEP_TYPES)}. "
            "Each step object must have: id (string like 'step_01'), type (one of the valid types), "
            "and a 'narration' field for spoken text. "
            "Additional type-specific fields: "
            "'speak' — just narration. "
            "'mouse_move' — add 'target' (description of UI element). "
            "'click' — add 'target'. "
            "'type' — add 'text' (what to type) and 'target'. "
            "'open_app' — add 'app_name'. "
            "'navigate' — add 'url' or 'path'. "
            "'highlight' — add 'target' (code or UI region to highlight). "
            "'pause' — add 'duration_seconds' (int) and optional 'reason'. "
            "'check_student' — add 'question' (comprehension check question). "
            "'demo' — add 'description' (what is being demonstrated). "
            "Include 20-30 steps. Use check_student steps every 5-7 steps to verify comprehension. "
            "Make the narration conversational and clear."
        )

        user_content = (
            f"Module {module_number}: {title}\n"
            f"Key concepts: {', '.join(key_concepts)}\n"
            f"Target duration: {estimated_minutes} minutes\n\n"
            f"Source transcript segment:\n{transcript_segment[:8000]}\n\n"
            "Write the full lesson script as a JSON object."
        )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=6000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )

        raw_text = response.content[0].text.strip()

        # Strip markdown fences if present
        if raw_text.startswith("```"):
            parts = raw_text.split("```")
            # parts[1] will be the content block (possibly starting with 'json\n')
            raw_text = parts[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
        raw_text = raw_text.strip()

        try:
            script = json.loads(raw_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse script JSON for module {module_number}: {e}")
            # Return minimal fallback script
            script = {
                "module_number": module_number,
                "title": title,
                "estimated_minutes": estimated_minutes,
                "steps": [
                    {
                        "id": "step_01",
                        "type": "speak",
                        "narration": f"Welcome to module {module_number}: {title}. "
                                     f"In this lesson we will cover: {', '.join(key_concepts)}.",
                    }
                ],
            }

        # Normalise
        script["module_number"] = int(script.get("module_number", module_number))
        script["estimated_minutes"] = int(script.get("estimated_minutes", estimated_minutes))
        if "steps" not in script or not isinstance(script["steps"], list):
            script["steps"] = []

        return script
