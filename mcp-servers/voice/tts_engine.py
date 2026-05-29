import hashlib
import os
import time
import threading
import queue
import sounddevice as sd
import soundfile as sf
import numpy as np
from pathlib import Path
from typing import Optional, Callable
import logging

logger = logging.getLogger(__name__)

CACHE_DIR = Path("E:/ai-tutor/data/cache/audio")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class TTSEngine:
    def __init__(self):
        self._pipeline = None
        self._playback_queue = queue.Queue()
        self._stop_event = threading.Event()
        self._current_playback = None
        self._interrupt_callback: Optional[Callable] = None
        self._initialized = False

    def initialize(self):
        """Load Kokoro model. Call once at startup."""
        try:
            from kokoro import KPipeline
            self._pipeline = KPipeline(lang_code='a')
            self._initialized = True
            logger.info("Kokoro TTS pipeline initialized successfully.")
        except ImportError:
            logger.warning(
                "kokoro package not found. TTS will produce silence placeholders."
            )
            self._pipeline = None
            self._initialized = True

    def _get_cache_path(self, text: str) -> Path:
        """Return cache file path for given text."""
        return CACHE_DIR / f"{hashlib.md5(text.encode()).hexdigest()}.wav"

    def generate_audio(self, text: str) -> str:
        """Generate audio for text, cache by sentence hash. Return file path."""
        cache_path = self._get_cache_path(text)

        if cache_path.exists():
            logger.debug(f"Cache hit for text: {text[:50]!r}")
            return str(cache_path)

        if self._pipeline is not None:
            try:
                generator = self._pipeline(text, voice='af_heart', speed=1.0)
                chunks = []
                for audio_chunk in generator:
                    # Some kokoro versions yield (phonemes, audio) tuples
                    if isinstance(audio_chunk, tuple):
                        audio_chunk = audio_chunk[1]
                    if audio_chunk is not None:
                        chunks.append(np.array(audio_chunk, dtype=np.float32))

                if chunks:
                    audio_array = np.concatenate(chunks)
                else:
                    audio_array = np.zeros(int(24000 * 0.5), dtype=np.float32)

                sf.write(str(cache_path), audio_array, 24000)
                logger.info(f"Generated and cached TTS for: {text[:50]!r}")
            except Exception as exc:
                logger.error(f"TTS generation failed: {exc}. Writing silence placeholder.")
                silence = np.zeros(int(24000 * 0.5), dtype=np.float32)
                sf.write(str(cache_path), silence, 24000)
        else:
            # No pipeline — write a 0.5-second silence placeholder
            silence = np.zeros(int(24000 * 0.5), dtype=np.float32)
            sf.write(str(cache_path), silence, 24000)
            logger.debug("No TTS pipeline; wrote silence placeholder.")

        return str(cache_path)

    def speak(self, text: str, pace: str = "normal", tone: str = "friendly") -> bool:
        """Generate TTS and play. Block until done or interrupted. Return True if completed."""
        path = self.generate_audio(text)
        return self.play_audio_file(path)

    def speak_async(self, text: str) -> str:
        """Start speaking without blocking. Return audio file path immediately."""
        path = self.generate_audio(text)

        t = threading.Thread(target=self.play_audio_file, args=(path,), daemon=True)
        t.start()
        return path

    def play_audio_file(self, path: str) -> bool:
        """Play a pre-cached audio file. Block until done. Return True if completed."""
        self._stop_event.clear()
        try:
            data, samplerate = sf.read(path, dtype='float32')
        except Exception as exc:
            logger.error(f"Failed to read audio file {path!r}: {exc}")
            return False

        sd.play(data, samplerate)

        # Wait for playback to finish, polling stop_event every 50 ms
        while sd.get_stream().active:
            if self._stop_event.is_set():
                sd.stop()
                logger.info("Playback interrupted.")
                if self._interrupt_callback is not None:
                    try:
                        self._interrupt_callback()
                    except Exception as cb_exc:
                        logger.warning(f"Interrupt callback raised: {cb_exc}")
                return False
            time.sleep(0.05)

        # Final check after stream ends
        if self._stop_event.is_set():
            sd.stop()
            return False

        return True

    def stop(self):
        """Interrupt current playback immediately."""
        self._stop_event.set()
        sd.stop()

        # Reset the stop_event after 100 ms so future playback isn't blocked
        def _reset():
            time.sleep(0.1)
            self._stop_event.clear()

        threading.Thread(target=_reset, daemon=True).start()

    def set_interrupt_callback(self, callback: Callable):
        """Set callback to call when playback is interrupted."""
        self._interrupt_callback = callback
