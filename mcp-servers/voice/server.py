#!/usr/bin/env python3
"""
Voice MCP server — HTTP/SSE transport on port 9103.

Runs as a persistent process (not spawned per-request), so the slow
mcp/torch import happens once at startup, not on every Claude Code
session. Register in settings.json as type:"http", url:"http://localhost:9103/mcp".
"""
import asyncio
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

_ready       = threading.Event()
_speaking    = False   # True while TTS is playing
_mic_enabled = True    # tracks last set_listening call

_current_pace     = "normal"
_last_spoken      = ""
_session_topic    = ""
_session_progress = 0


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
        "speaking": _speaking,
        "vad_active": bool(vad and vad._listening),
        "mic_enabled": _mic_enabled,
        "pace": _current_pace,
        "last_spoken_preview": _last_spoken[:60] if _last_spoken else "",
        "session_topic": _session_topic,
        "session_progress": _session_progress,
    })


@mcp.custom_route("/set_listening", methods=["GET"])
async def set_listening_http(request: Request) -> JSONResponse:
    """HTTP control endpoint for the status widget mic toggle."""
    global _mic_enabled
    enabled = request.query_params.get("enabled", "true").lower() != "false"
    _mic_enabled = enabled
    if vad:
        vad.set_enabled(enabled)
    return JSONResponse({"mic_enabled": _mic_enabled})


@mcp.custom_route("/stop", methods=["GET"])
async def stop_speaking(request: Request) -> JSONResponse:
    if tts:
        tts.stop()
    return JSONResponse({"stopped": True})

@mcp.custom_route("/repeat", methods=["GET"])
async def repeat_last(request: Request) -> JSONResponse:
    global _speaking
    if not _last_spoken:
        return JSONResponse({"result": "nothing_to_repeat"})
    if not _ready.is_set():
        return JSONResponse({"result": "not_ready"})
    _speaking = True
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: tts.speak(_last_spoken, _current_pace)
        )
    finally:
        _speaking = False
    return JSONResponse({"result": "spoken"})

@mcp.custom_route("/set_pace", methods=["GET"])
async def set_pace_route(request: Request) -> JSONResponse:
    global _current_pace
    pace = request.query_params.get("pace", "normal")
    if pace not in ("slow", "normal", "fast"):
        pace = "normal"
    _current_pace = pace
    return JSONResponse({"pace": _current_pace})

@mcp.custom_route("/mic_level", methods=["GET"])
async def mic_level_route(request: Request) -> JSONResponse:
    level = float(vad._current_level) if vad and hasattr(vad, "_current_level") else 0.0
    return JSONResponse({"level": round(level, 3)})

@mcp.custom_route("/set_session", methods=["GET"])
async def set_session_route(request: Request) -> JSONResponse:
    global _session_topic, _session_progress
    _session_topic = request.query_params.get("topic", _session_topic)
    try:
        _session_progress = int(request.query_params.get("progress", _session_progress))
    except ValueError:
        pass
    return JSONResponse({"topic": _session_topic, "progress": _session_progress})


@mcp.tool()
def speak(text: str, pace: str = "normal", tone: str = "friendly") -> str:
    """Synthesise text to speech and play it. Blocks until playback finishes or is interrupted."""
    global _speaking, _last_spoken, _current_pace
    _wait_ready()
    _last_spoken = text
    _current_pace = pace
    _speaking = True
    try:
        completed = tts.speak(text, pace, tone)
    finally:
        _speaking = False
    return "spoken" if completed else "interrupted"


@mcp.tool()
def speak_async(text: str) -> str:
    """Start TTS playback without blocking. Returns the path to the cached audio file."""
    _wait_ready()
    return tts.speak_async(text)


@mcp.tool()
def set_listening(enabled: bool = True) -> str:
    """Enable or disable the microphone VAD listener."""
    global _mic_enabled
    _wait_ready()
    _mic_enabled = enabled
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
    global _speaking
    _wait_ready()
    _speaking = True
    try:
        completed = tts.play_audio_file(path)
    finally:
        _speaking = False
    return "played" if completed else "interrupted"


@mcp.tool()
def listen(timeout_seconds: float = 20, prompt: str = "") -> str:
    """
    Block until the user finishes speaking, then return their transcript.
    Optionally speak a prompt first. Returns empty string on timeout.
    """
    global _speaking, _mic_enabled
    _wait_ready()

    if prompt.strip():
        _speaking = True
        try:
            tts.speak(prompt.strip())
        finally:
            _speaking = False

    # Drain stale transcripts
    while True:
        try:
            interrupt_handler._pending_interrupts.get_nowait()
        except queue.Empty:
            break

    _mic_enabled = True
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
