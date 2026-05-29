import google.generativeai as genai
import json
import logging
from pathlib import Path
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)


class GeminiVisionClient:
    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        self.model_name = model
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model)
        self._token_usage = 0

    def _load_image(self, image_path: str) -> Image.Image:
        """Load an image from disk and return a PIL Image."""
        return Image.open(image_path)

    def _track_usage(self, response) -> None:
        """Accumulate token usage from a response object."""
        try:
            self._token_usage += response.usage_metadata.total_token_count
        except Exception:
            pass

    def describe(self, image_path: str, question: str) -> str:
        """Send screenshot to Gemini with question. Return text answer."""
        image = self._load_image(image_path)
        response = self.model.generate_content([question, image])
        self._track_usage(response)
        return response.text

    def find_element(self, image_path: str, description: str) -> dict:
        """Ask Gemini to locate element.
        Return {found: bool, x: int, y: int, confidence: float}."""
        image = self._load_image(image_path)
        prompt = (
            f"In this screenshot, find the UI element described as: {description}. "
            "Respond with JSON: {\"found\": bool, \"x\": int, \"y\": int, \"confidence\": float} "
            "where x,y are pixel coordinates of the element's center. "
            "Only respond with JSON."
        )
        response = self.model.generate_content([prompt, image])
        self._track_usage(response)

        text = response.text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            # Remove first and last fence lines
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
            logger.warning("Failed to parse find_element JSON from Gemini: %s | raw: %s", exc, text)
            return {"found": False, "x": 0, "y": 0, "confidence": 0.0}

    def diff(self, before_path: str, after_path: str, question: str) -> str:
        """Compare two screenshots and describe what changed."""
        before_image = self._load_image(before_path)
        after_image = self._load_image(after_path)
        prompt = (
            f"These are two screenshots taken in sequence (before and after). "
            f"{question} Describe what changed between them."
        )
        response = self.model.generate_content([prompt, before_image, after_image])
        self._track_usage(response)
        return response.text

    def get_token_usage(self) -> int:
        """Return cumulative token usage for this session."""
        return self._token_usage
