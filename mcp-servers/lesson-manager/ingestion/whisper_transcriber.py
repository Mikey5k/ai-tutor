from faster_whisper import WhisperModel
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class WhisperTranscriber:
    def __init__(self, model_size: str = "base"):
        self.model_size = model_size
        self._model: Optional[WhisperModel] = None

    def initialize(self):
        """Load the Whisper model into memory."""
        logger.info(f"Loading Whisper model '{self.model_size}'...")
        self._model = WhisperModel(
            self.model_size,
            device="cpu",
            compute_type="int8",
        )
        logger.info("Whisper model loaded.")

    def transcribe(self, audio_path: str) -> str:
        """Transcribe audio file. Return full text with timestamps."""
        if self._model is None:
            self.initialize()

        segments, _info = self._model.transcribe(audio_path, beam_size=5)

        lines = []
        for segment in segments:
            start = segment.start
            text = segment.text.strip()
            if text:
                lines.append(f"[{start:.1f}s] {text}")

        return "\n".join(lines)
