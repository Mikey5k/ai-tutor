#!/usr/bin/env python3
"""
Voice MCP server — HTTP/SSE transport on port 9103.

Runs as a persistent process (not spawned per-request), so the slow
mcp/torch import happens once at startup, not on every Claude Code
session. Register in settings.json as type:"http", url:"http://localhost:9103/mcp".
"""
import logging
import queue
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Component singletons (populated by _background_init)
# ---------------------------------------------------------------------------

tts = None
stt = None
interrupt_handler = None
vad = None

_ready = threading.Event()


def _background_init():
    """Import heavy modules and load all models in a daemon thread."""
    global tts, stt, interrupt_handler, vad
    try:
        import numpy as np
        from tts_engine import TTSEngine
        from stt_engine import STTEngine
        from vad_listener import VADListener
        from interrupt_handler import InterruptHandler

        logger.info("Background init: loading models...")

        tts = TTSEngine()
        stt = STTEngine()
        interrupt_handler = InterruptHandler()

        def on_speech_detected(audio_array: np.ndarray):
            transcript = stt.transcribe_audio(audio_array)
            if transcript.strip():
                logger.info(f"Speech detected: {transcript!r}")
                interrupt_handler.inject_interrupt(transcript)

        vad = VADListener(speech_callback=on_speech_detected)

        tts.initialize()
        stt.initialize()
        vad.initialize()
        vad.start()

        logger.info("Voice server ready.")
    except Exception as exc:
        logger.error(f"Background init failed: {exc}", exc_info=True)
    finally:
        _ready.set()


def _wait_ready():
    """Block until models are loaded (called inside tool handlers)."""
    if not _ready.is_set():
        logger.info("Waiting for models to finish loading...")
        _ready.wait(timeout=120)


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

mcp = FastMCP("voice", host="0.0.0.0", port=9103)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "ready": _ready.is_set(),
        "vad_active": bool(vad and vad._listening),
    })


@mcp.tool()
def speak(text: str, pace: str = "normal", tone: str = "friendly") -> str:
    """Synthesise text to speech and play it. Blocks until playback finishes or is interrupted."""
    _wait_ready()
    completed = tts.speak(text, pace, tone)
    return "spoken" if completed else "interrupted"


@mcp.tool()
def speak_async(text: str) -> str:
    """Start TTS playback without blocking. Returns the path to the cached audio file."""
    _wait_ready()
    return tts.speak_async(text)


@mcp.tool()
def set_listening(enabled: bool = True) -> str:
    """Enable or disable the microphone VAD listener."""
    _wait_ready()
    vad.set_enabled(enabled)
    return f"VAD listening {'enabled' if enabled else 'disabled'}."


@mcp.tool()
def get_speech_transcript() -> str:
    """Retrieve the next pending speech transcript. Returns empty string if none available."""
    _wait_ready()
    return interrupt_handler.get_pending_transcript() or ""


@mcp.tool()
def play_audio_file(path: str) -> str:
    """Play a pre-cached WAV file by path. Blocks until playback finishes or is interrupted."""
    _wait_ready()
    completed = tts.play_audio_file(path)
    return "played" if completed else "interrupted"


@mcp.tool()
def listen(timeout_seconds: float = 20, prompt: str = "") -> str:
    """
    Block until the user finishes speaking, then return their transcript.
    Optionally speak a prompt first. Returns empty string on timeout.
    """
    _wait_ready()

    if prompt.strip():
        tts.speak(prompt.strip())

    # Drain stale transcripts
    while True:
        try:
            interrupt_handler._pending_interrupts.get_nowait()
        except queue.Empty:
            break

    vad.set_enabled(True)
    try:
        return interrupt_handler._pending_interrupts.get(timeout=timeout_seconds)
    except queue.Empty:
        return ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    threading.Thread(target=_background_init, name="voice-init", daemon=True).start()
    logger.info("Voice MCP HTTP server starting on port 9103...")
    mcp.run(transport="streamable-http")
