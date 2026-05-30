import numpy as np
import threading
import queue
import time
import sounddevice as sd
import torch
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHUNK_DURATION_MS = 30
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_DURATION_MS / 1000)


def _find_wasapi_in() -> Optional[int]:
    """Return WASAPI microphone device index, or None to use system default."""
    try:
        import sounddevice as sd
        hostapis = sd.query_hostapis()
        wh = next((i for i, h in enumerate(hostapis) if "WASAPI" in h["name"]), None)
        if wh is not None:
            for i, d in enumerate(sd.query_devices()):
                if d["hostapi"] == wh and d["max_input_channels"] > 0:
                    return i
    except Exception:
        pass
    return None


class VADListener:
    def __init__(
        self,
        speech_callback: Callable[[np.ndarray], None],
        threshold: float = 0.5,
    ):
        self.speech_callback = speech_callback
        self.threshold = threshold
        self._model = None
        self._utils = None
        self._listening = False
        self._enabled = True
        self._thread: Optional[threading.Thread] = None
        self._audio_buffer = []
        self._in_speech = False
        self._silence_chunks = 0
        self.SILENCE_CHUNKS_TO_END = 15  # ~450ms of silence ends utterance

    def initialize(self):
        """Load Silero VAD model."""
        logger.info("Loading Silero VAD model...")
        try:
            model, utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                force_reload=False,
            )
            self._model = model
            self._utils = utils
            logger.info("Silero VAD model loaded successfully.")
        except Exception as exc:
            logger.error(f"Failed to load Silero VAD: {exc}")
            self._model = None
            self._utils = None

    def start(self):
        """Start listening on microphone in background thread."""
        self._wasapi_in = _find_wasapi_in()
        if self._wasapi_in is not None:
            dev_name = sd.query_devices(self._wasapi_in)["name"]
            logger.info(f"VAD using WASAPI mic: [{self._wasapi_in}] {dev_name}")
        else:
            logger.info("VAD using system default microphone.")
        self._listening = True
        self._thread = threading.Thread(
            target=self._process_loop,
            name="vad-listener",
            daemon=True,
        )
        self._thread.start()
        logger.info("VAD listener started.")

    def stop(self):
        """Stop listening."""
        self._listening = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        logger.info("VAD listener stopped.")

    def set_enabled(self, enabled: bool):
        """Enable or disable VAD processing without stopping the thread."""
        self._enabled = enabled
        logger.info(f"VAD processing {'enabled' if enabled else 'disabled'}.")

    def _process_loop(self):
        """Main VAD processing loop. Runs in background thread."""
        if self._model is None:
            logger.warning(
                "VAD model not loaded; _process_loop exiting immediately."
            )
            return

        wasapi_in = getattr(self, "_wasapi_in", None)
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype='float32',
                blocksize=CHUNK_SAMPLES,
                device=wasapi_in,
            ) as stream:
                logger.info("Microphone input stream opened.")
                while self._listening:
                    chunk, overflowed = stream.read(CHUNK_SAMPLES)
                    if overflowed:
                        logger.debug("Audio input overflowed.")

                    if not self._enabled:
                        # VAD disabled — discard audio and reset state
                        self._audio_buffer = []
                        self._in_speech = False
                        self._silence_chunks = 0
                        continue

                    chunk_1d = chunk[:, 0] if chunk.ndim > 1 else chunk.flatten()
                    is_speech = self._is_speech(chunk_1d)

                    if is_speech:
                        if not self._in_speech:
                            logger.debug("Speech start detected.")
                            self._in_speech = True
                        self._audio_buffer.append(chunk_1d)
                        self._silence_chunks = 0
                    else:
                        if self._in_speech:
                            self._silence_chunks += 1
                            # Keep buffering during silence so we don't clip
                            self._audio_buffer.append(chunk_1d)

                            if self._silence_chunks >= self.SILENCE_CHUNKS_TO_END:
                                # End of utterance detected
                                concat_audio = np.concatenate(self._audio_buffer)
                                logger.info(
                                    f"Utterance captured: {len(concat_audio)} samples "
                                    f"({len(concat_audio)/SAMPLE_RATE:.2f}s)"
                                )
                                try:
                                    self.speech_callback(concat_audio)
                                except Exception as cb_exc:
                                    logger.error(
                                        f"speech_callback raised: {cb_exc}"
                                    )
                                # Reset state
                                self._audio_buffer = []
                                self._in_speech = False
                                self._silence_chunks = 0

        except Exception as exc:
            logger.error(f"VAD _process_loop error: {exc}")
        finally:
            self._listening = False
            logger.info("VAD process loop exited.")

    def _is_speech(self, audio_chunk: np.ndarray) -> bool:
        """Run Silero VAD on audio chunk. Return True if speech detected."""
        try:
            chunk_tensor = torch.tensor(audio_chunk).flatten()
            confidence = self._model(chunk_tensor, SAMPLE_RATE)
            # confidence may be a tensor scalar or a plain float
            if isinstance(confidence, torch.Tensor):
                confidence = confidence.item()
            return confidence >= self.threshold
        except Exception as exc:
            logger.debug(f"_is_speech error: {exc}")
            return False
