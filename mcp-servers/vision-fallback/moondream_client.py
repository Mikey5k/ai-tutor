import base64
import json
import logging
from pathlib import Path
from typing import Optional

import ollama

logger = logging.getLogger(__name__)


class MoondreamClient:
    def __init__(self, model: str = "moondream"):
        self.model = model

    def is_available(self) -> bool:
        """Check if Ollama is running and moondream model is pulled."""
        try:
            models_response = ollama.list()
            # ollama.list() returns a dict with a "models" key; each entry has a "name" field.
            models = models_response.get("models", [])
            for entry in models:
                name = entry.get("name", "")
                if self.model in name:
                    return True
            return False
        except Exception as exc:
            logger.debug("Moondream availability check failed: %s", exc)
            return False

    def _encode_image(self, image_path: str) -> str:
        """Read image from disk and return base64-encoded string."""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def describe(self, image_path: str, question: str) -> str:
        """Send screenshot to Moondream. Return text answer."""
        b64 = self._encode_image(image_path)
        response = ollama.generate(
            model=self.model,
            prompt=question,
            images=[b64],
        )
        return response["response"]

    def find_element(self, image_path: str, description: str) -> dict:
        """Locate element in screenshot. Return coordinates."""
        b64 = self._encode_image(image_path)
        prompt = (
            f"In this screenshot, find the UI element described as: {description}. "
            "Respond with JSON only: {\"found\": bool, \"x\": int, \"y\": int, \"confidence\": float} "
            "where x,y are the pixel coordinates of the element's center. "
            "Do not include any other text."
        )
        response = ollama.generate(
            model=self.model,
            prompt=prompt,
            images=[b64],
        )
        text = response["response"].strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            inner = []
            inside = False
            for line in lines:
                if line.startswith("```") and not inside:
                    inside = True
                    continue
                if line.startswith("```") and inside:
                    break
                if inside:
                    inner.append(line)
            text = "\n".join(inner).strip()

        try:
            result = json.loads(text)
            return {
                "found": bool(result.get("found", False)),
                "x": int(result.get("x", 0)),
                "y": int(result.get("y", 0)),
                "confidence": float(result.get("confidence", 0.0)),
            }
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning("Failed to parse find_element JSON from Moondream: %s | raw: %s", exc, text)
            return {"found": False, "x": 0, "y": 0, "confidence": 0.0}
