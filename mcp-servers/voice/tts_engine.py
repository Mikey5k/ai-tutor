"""
TTS engine: edge-tts + sentence-level synthesis + DSP + WASAPI output.

Replaces Kokoro. No SSML — edge-tts SSML support inflates audio 8x on this
platform. We synthesize each sentence as plain text, join with randomized
silence, apply DSP, resample to the WASAPI device's native rate.
"""
import asyncio
import hashlib
import io
import logging
import random
import re
import threading
import time
from math import gcd
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy import signal

logger = logging.getLogger(__name__)

CACHE_DIR = Path("E:/ai-tutor/data/cache/audio")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

VOICE = "en-US-AriaNeural"

_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+')
_SENT_ENDER = re.compile(r'([.!?])\s*$')

_PACE_RATES = {"slow": "-15%", "normal": "-8%", "fast": "+5%"}
_TONE_RATES = {"friendly": "-8%", "formal": "-3%", "excited": "+3%"}


def _find_wasapi_out() -> tuple:
    """Return (device_index, native_sample_rate). Falls back to (None, 48000)."""
    try:
        import sounddevice as sd
        hostapis = sd.query_hostapis()
        wh = next((i for i, h in enumerate(hostapis) if "WASAPI" in h["name"]), None)
        if wh is not None:
            devices = sd.query_devices()
            for i, d in enumerate(devices):
                if d["hostapi"] == wh and d["max_output_channels"] > 0:
                    return i, int(d["default_samplerate"])
    except Exception:
        pass
    return None, 48000


class _AudioEnhancer:
    def enhance(self, audio: np.ndarray, sr: int) -> np.ndarray:
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        audio = audio.astype(np.float64)
        audio = signal.sosfilt(signal.butter(2, 80, "hp", fs=sr, output="sos"), audio)
        audio += signal.sosfilt(
            signal.butter(2, [2000, 5000], "bandpass", fs=sr, output="sos"), audio
        ) * 0.13
        audio += signal.sosfilt(
            signal.butter(2, [300, 800], "bandpass", fs=sr, output="sos"), audio
        ) * 0.06
        thr = 0.58
        mask = np.abs(audio) > thr
        audio[mask] = np.sign(audio[mask]) * (thr + (np.abs(audio[mask]) - thr) / 3.2)
        n7, n13 = int(sr * 0.007), int(sr * 0.013)
        r1 = np.zeros_like(audio); r1[n7:]  = audio[:-n7]  * 0.085
        r2 = np.zeros_like(audio); r2[n13:] = audio[:-n13] * 0.038
        audio = audio + r1 + r2
        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = audio / peak * 0.87
        return audio.astype(np.float32)


class TTSEngine:
    def __init__(self):
        self._stop_event    = threading.Event()
        self._enhancer      = _AudioEnhancer()
        self._wasapi_out    = None
        self._native_sr     = 48000
        self._initialized   = False

    # ── startup ───────────────────────────────────────────────────────────────

    def initialize(self):
        self._wasapi_out, self._native_sr = _find_wasapi_out()
        dev_name = (
            sd.query_devices(self._wasapi_out)["name"]
            if self._wasapi_out is not None else "default"
        )
        logger.info(
            f"TTS engine ready. Output: [{self._wasapi_out}] {dev_name}  "
            f"native_sr={self._native_sr}Hz"
        )
        self._initialized = True

    # ── synthesis ─────────────────────────────────────────────────────────────

    def _get_cache_path(self, text: str, rate: str) -> Path:
        key = hashlib.md5(f"v3:{VOICE}:{rate}:{text}".encode()).hexdigest()
        return CACHE_DIR / f"{key}.wav"

    def generate_audio(self, text: str, rate: str = "-8%") -> str:
        """Synthesize text → WAV file. Returns file path (disk-cached)."""
        cache_path = self._get_cache_path(text, rate)
        if cache_path.exists():
            logger.debug(f"Cache hit: {text[:40]!r}")
            return str(cache_path)

        try:
            audio, sr = asyncio.run(self._synthesize_async(text, rate))
        except RuntimeError:
            # If called from inside a running event loop (shouldn't happen
            # when used via run_in_executor, but handle it gracefully)
            loop = asyncio.new_event_loop()
            try:
                audio, sr = loop.run_until_complete(self._synthesize_async(text, rate))
            finally:
                loop.close()

        sf.write(str(cache_path), audio, sr)
        logger.info(f"Synthesized {len(audio)/sr:.1f}s for: {text[:40]!r}")
        return str(cache_path)

    async def _synthesize_async(self, text: str, rate: str) -> tuple:
        import edge_tts

        sentences = [s.strip() for s in _SENT_SPLIT.split(text.strip()) if s.strip()] or [text]
        parts: list = []
        sr: Optional[int] = None

        for i, sent in enumerate(sentences):
            communicate = edge_tts.Communicate(sent, voice=VOICE, rate=rate)
            chunks = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            if not chunks:
                continue
            frag, frag_sr = sf.read(io.BytesIO(b"".join(chunks)), dtype="float32")
            if sr is None:
                sr = frag_sr
            parts.append(frag)

            if i < len(sentences) - 1:
                m = _SENT_ENDER.search(sent)
                if m and m.group(1) == "?":
                    gap_ms = random.randint(290, 370)
                elif m and m.group(1) == "!":
                    gap_ms = random.randint(240, 300)
                else:
                    gap_ms = random.randint(200, 260)
                parts.append(np.zeros(int(frag_sr * gap_ms / 1000), dtype=np.float32))

        if not parts:
            return np.zeros(100, dtype=np.float32), 24000

        combined = np.concatenate(parts)
        combined = self._enhancer.enhance(combined, sr)
        return combined, sr

    # ── playback ──────────────────────────────────────────────────────────────

    def speak(self, text: str, pace: str = "normal", tone: str = "friendly") -> bool:
        """Synthesize and play. Blocks until done or interrupted. Returns True if completed."""
        rate = _PACE_RATES.get(pace) or _TONE_RATES.get(tone, "-8%")
        path = self.generate_audio(text, rate)
        return self.play_audio_file(path)

    def speak_async(self, text: str) -> str:
        """Start playback in background. Returns audio file path immediately."""
        path = self.generate_audio(text, "-8%")
        threading.Thread(target=self.play_audio_file, args=(path,), daemon=True).start()
        return path

    def play_audio_file(self, path: str) -> bool:
        """Play a WAV file. Blocks until done or interrupted. Returns True if completed."""
        self._stop_event.clear()
        try:
            data, sr = sf.read(path, dtype="float32")
        except Exception as exc:
            logger.error(f"Cannot read audio file {path!r}: {exc}")
            return False

        # Resample to WASAPI native rate if needed
        if sr != self._native_sr:
            g    = gcd(self._native_sr, sr)
            data = signal.resample_poly(
                data.astype(np.float64), self._native_sr // g, sr // g
            ).astype(np.float32)
            sr = self._native_sr

        mono        = data if data.ndim == 1 else data.mean(axis=1)
        chunk_size  = int(sr * 0.04)
        pos         = 0

        try:
            with sd.OutputStream(
                samplerate=sr, channels=1, dtype="float32",
                device=self._wasapi_out
            ) as stream:
                while pos < len(mono):
                    if self._stop_event.is_set():
                        logger.info("Playback interrupted.")
                        return False
                    end   = min(pos + chunk_size, len(mono))
                    chunk = mono[pos:end]
                    if len(chunk) < chunk_size:
                        chunk = np.pad(chunk, (0, chunk_size - len(chunk)))
                    stream.write(chunk.reshape(-1, 1))
                    pos = end
        except Exception as exc:
            logger.error(f"Playback error: {exc}")
            return False

        return True

    # ── control ───────────────────────────────────────────────────────────────

    def stop(self):
        """Interrupt current playback."""
        self._stop_event.set()
        sd.stop()
        def _reset():
            time.sleep(0.1)
            self._stop_event.clear()
        threading.Thread(target=_reset, daemon=True).start()

    def set_interrupt_callback(self, callback):
        pass  # kept for API compatibility, not used with chunked playback
