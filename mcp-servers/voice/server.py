#!/usr/bin/env python3
import asyncio
import json
import logging
import sys
import threading
import numpy as np
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, str(Path(__file__).parent))

from tts_engine import TTSEngine
from stt_engine import STTEngine
from vad_listener import VADListener
from interrupt_handler import InterruptHandler

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Component singletons
# ---------------------------------------------------------------------------

tts = TTSEngine()
stt = STTEngine()
interrupt_handler = InterruptHandler()


def on_speech_detected(audio_array: np.ndarray):
    """Callback fired by VADListener when a complete utterance is captured."""
    transcript = stt.transcribe_audio(audio_array)
    if transcript.strip():
        logger.info(f"Speech detected: {transcript!r}")
        interrupt_handler.inject_interrupt(transcript)


vad = VADListener(speech_callback=on_speech_detected)

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

server = Server("voice")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="speak",
            description="Synthesise text to speech and play it. Blocks until playback finishes or is interrupted.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to speak."},
                    "pace": {
                        "type": "string",
                        "enum": ["slow", "normal", "fast"],
                        "default": "normal",
                        "description": "Speaking pace.",
                    },
                    "tone": {
                        "type": "string",
                        "enum": ["friendly", "formal", "excited"],
                        "default": "friendly",
                        "description": "Speaking tone.",
                    },
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="speak_async",
            description="Start TTS playback without blocking. Returns the path to the cached audio file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to speak."},
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="set_listening",
            description="Enable or disable the microphone VAD listener.",
            inputSchema={
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "default": True,
                        "description": "True to enable listening, False to disable.",
                    },
                },
            },
        ),
        types.Tool(
            name="get_speech_transcript",
            description="Retrieve the next pending speech transcript captured by the VAD listener. Returns empty string if none available.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        types.Tool(
            name="play_audio_file",
            description="Play a pre-cached audio file by path. Blocks until playback finishes or is interrupted.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the WAV audio file.",
                    },
                },
                "required": ["path"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent]:

    if name == "speak":
        text = arguments["text"]
        pace = arguments.get("pace", "normal")
        tone = arguments.get("tone", "friendly")

        # Run blocking TTS in a thread so we don't stall the event loop
        loop = asyncio.get_event_loop()
        completed = await loop.run_in_executor(
            None, lambda: tts.speak(text, pace, tone)
        )
        result = "spoken" if completed else "interrupted"
        return [types.TextContent(type="text", text=result)]

    elif name == "speak_async":
        text = arguments["text"]
        audio_path = tts.speak_async(text)
        return [types.TextContent(type="text", text=audio_path)]

    elif name == "set_listening":
        enabled = arguments.get("enabled", True)
        vad.set_enabled(enabled)
        status = f"VAD listening {'enabled' if enabled else 'disabled'}."
        return [types.TextContent(type="text", text=status)]

    elif name == "get_speech_transcript":
        transcript = interrupt_handler.get_pending_transcript()
        return [types.TextContent(type="text", text=transcript or "")]

    elif name == "play_audio_file":
        path = arguments["path"]
        loop = asyncio.get_event_loop()
        completed = await loop.run_in_executor(
            None, lambda: tts.play_audio_file(path)
        )
        result = "played" if completed else "interrupted"
        return [types.TextContent(type="text", text=result)]

    else:
        raise ValueError(f"Unknown tool: {name!r}")


# ---------------------------------------------------------------------------
# Health HTTP server (port 9104)
# ---------------------------------------------------------------------------

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/health", "/"):
            body = json.dumps(
                {"status": "ok", "vad_active": vad._listening}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):  # silence default access log
        pass


def _start_health_server():
    httpd = HTTPServer(("0.0.0.0", 9104), HealthHandler)
    logger.info("Health server listening on port 9104.")
    httpd.serve_forever()


# ---------------------------------------------------------------------------
# Startup & main
# ---------------------------------------------------------------------------

async def startup():
    tts.initialize()
    stt.initialize()
    vad.initialize()
    vad.start()

    # Start health server in a daemon thread
    health_thread = threading.Thread(
        target=_start_health_server,
        name="health-server",
        daemon=True,
    )
    health_thread.start()

    logger.info("Voice server ready.")


async def main():
    await startup()
    async with stdio_server() as streams:
        await server.run(*streams, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
