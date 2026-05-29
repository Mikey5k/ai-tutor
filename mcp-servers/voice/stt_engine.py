import numpy as np
import tempfile
import soundfile as sf
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class STTEngine:
    def __init__(self, model_size: str = "base"):
        self.model_size = model_size
        self._model = None

    def initialize(self):
        """Load Whisper model."""
        from faster_whisper import WhisperModel

        logger.info(f"Loading Whisper model: size={self.model_size!r}")
        t0 = time.time()
        self._model = WhisperModel(
            self.model_size,
            device="cpu",
            compute_type="int8",
        )
        elapsed = time.time() - t0
        logger.info(
            f"Whisper model '{self.model_size}' loaded in {elapsed:.2f}s "
            f"(device=cpu, compute_type=int8)"
        )

    def transcribe_audio(self, audio_array: np.ndarray, sample_rate: int = 16000) -> str:
        """Transcribe numpy audio array to text."""
        # Ensure float32 mono
        if audio_array.ndim > 1:
            audio_array = audio_array.mean(axis=1)
        audio_array = audio_array.astype(np.float32)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            sf.write(tmp_path, audio_array, sample_rate)
            return self.transcribe_file(tmp_path)
        finally:
            import os
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def transcribe_file(self, audio_path: str) -> str:
        """Transcribe audio file to text."""
        if self._model is None:
            logger.error("STTEngine.transcribe_file called before initialize().")
            return ""

        try:
            segments, info = self._model.transcribe(
                audio_path,
                beam_size=5,
                language="en",
            )
            transcript = " ".join(s.text for s in segments).strip()
            logger.debug(
                f"Transcription ({info.language}, "
                f"prob={info.language_probability:.2f}): {transcript!r}"
            )
            return transcript
        except Exception as exc:
            logger.error(f"Transcription failed for {audio_path!r}: {exc}")
            return ""
