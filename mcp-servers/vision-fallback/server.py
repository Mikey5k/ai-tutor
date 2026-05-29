#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import ScreenshotCapture
from gemini_client import GeminiVisionClient
from moondream_client import MoondreamClient

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
VISION_PROVIDER = os.environ.get("VISION_PROVIDER", "gemini")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
HEALTH_PORT = int(os.environ.get("VISION_HEALTH_PORT", "9103"))

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
screen = ScreenshotCapture()
screen.setup_session()

gemini: GeminiVisionClient | None = (
    GeminiVisionClient(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
)
moondream = MoondreamClient()

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
server = Server("vision-fallback")


def _get_vision_client():
    """Return the best available vision client.

    Routing logic:
    - If VISION_PROVIDER == "moondream", try moondream first.
    - Otherwise (default "gemini"), try gemini first.
    - Fall back to the other client if the preferred one is unavailable.
    - Raise RuntimeError if neither is available.
    """
    if VISION_PROVIDER == "moondream":
        if moondream.is_available():
            return moondream
        if gemini is not None:
            return gemini
        raise RuntimeError(
            "No vision client available: Moondream is not running and GEMINI_API_KEY is not set."
        )
    else:
        # Default: prefer Gemini
        if gemini is not None:
            return gemini
        if moondream.is_available():
            return moondream
        raise RuntimeError(
            "No vision client available: GEMINI_API_KEY is not set and Moondream is not running."
        )


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="screen_capture",
            description=(
                "Capture a screenshot of the screen. "
                "Optionally capture a specific region."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "region": {
                        "type": "string",
                        "description": (
                            'JSON string like {"left":0,"top":0,"width":800,"height":600} '
                            "or empty string for full screen."
                        ),
                        "default": "",
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="screen_describe",
            description="Describe a screenshot by answering a question about it.",
            inputSchema={
                "type": "object",
                "properties": {
                    "screenshot_path": {
                        "type": "string",
                        "description": "Absolute path to the screenshot PNG file.",
                    },
                    "question": {
                        "type": "string",
                        "description": "Question to answer about the screenshot.",
                    },
                },
                "required": ["screenshot_path", "question"],
            },
        ),
        types.Tool(
            name="screen_find_element",
            description=(
                "Locate a UI element in a screenshot and return its pixel coordinates."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "screenshot_path": {
                        "type": "string",
                        "description": "Absolute path to the screenshot PNG file.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Natural-language description of the UI element to find.",
                    },
                },
                "required": ["screenshot_path", "description"],
            },
        ),
        types.Tool(
            name="screen_diff",
            description="Compare two screenshots and describe what changed between them.",
            inputSchema={
                "type": "object",
                "properties": {
                    "before_path": {
                        "type": "string",
                        "description": "Absolute path to the 'before' screenshot.",
                    },
                    "after_path": {
                        "type": "string",
                        "description": "Absolute path to the 'after' screenshot.",
                    },
                    "question": {
                        "type": "string",
                        "description": (
                            "Optional guiding question about what to look for in the diff."
                        ),
                        "default": "",
                    },
                },
                "required": ["before_path", "after_path"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool call handler
# ---------------------------------------------------------------------------

@server.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent]:
    try:
        if name == "screen_capture":
            return await _tool_screen_capture(arguments)
        elif name == "screen_describe":
            return await _tool_screen_describe(arguments)
        elif name == "screen_find_element":
            return await _tool_screen_find_element(arguments)
        elif name == "screen_diff":
            return await _tool_screen_diff(arguments)
        else:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as exc:
        logger.exception("Error in tool %s: %s", name, exc)
        return [types.TextContent(type="text", text=f"Error: {exc}")]


# ---------------------------------------------------------------------------
# Individual tool implementations
# ---------------------------------------------------------------------------

async def _tool_screen_capture(arguments: dict) -> list[types.TextContent]:
    """Capture a screenshot and return its file path."""
    region_str = arguments.get("region", "").strip()
    region_dict = None
    if region_str:
        try:
            region_dict = json.loads(region_str)
        except json.JSONDecodeError as exc:
            return [
                types.TextContent(
                    type="text",
                    text=f"Invalid region JSON: {exc}",
                )
            ]

    # Run blocking capture in thread pool so we don't block the event loop
    loop = asyncio.get_event_loop()
    file_path = await loop.run_in_executor(None, screen.capture, region_dict)
    logger.info("Screenshot captured: %s", file_path)
    return [types.TextContent(type="text", text=file_path)]


async def _tool_screen_describe(arguments: dict) -> list[types.TextContent]:
    """Describe a screenshot by routing to the appropriate vision client."""
    screenshot_path = arguments["screenshot_path"]
    question = arguments["question"]

    client = _get_vision_client()

    loop = asyncio.get_event_loop()
    answer = await loop.run_in_executor(None, client.describe, screenshot_path, question)

    token_info = gemini.get_token_usage() if gemini else "N/A"
    logger.info("Vision call: %s | tokens: %s", screenshot_path, token_info)

    return [types.TextContent(type="text", text=answer)]


async def _tool_screen_find_element(arguments: dict) -> list[types.TextContent]:
    """Locate a UI element in a screenshot."""
    screenshot_path = arguments["screenshot_path"]
    description = arguments["description"]

    client = _get_vision_client()

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, client.find_element, screenshot_path, description
    )

    return [types.TextContent(type="text", text=json.dumps(result))]


async def _tool_screen_diff(arguments: dict) -> list[types.TextContent]:
    """Compare two screenshots and return a description of changes."""
    before_path = arguments["before_path"]
    after_path = arguments["after_path"]
    question = arguments.get("question", "").strip() or "What changed?"

    # screen_diff is a Gemini-only method; fall back to moondream describe if needed.
    if gemini is not None:
        loop = asyncio.get_event_loop()
        answer = await loop.run_in_executor(
            None, gemini.diff, before_path, after_path, question
        )
    elif moondream.is_available():
        # Moondream cannot compare two images directly; describe the after image instead.
        logger.warning(
            "Gemini unavailable for diff; falling back to Moondream single-image description."
        )
        loop = asyncio.get_event_loop()
        prompt = f"Describe what you see in this screenshot. Context: {question}"
        answer = await loop.run_in_executor(
            None, moondream.describe, after_path, prompt
        )
    else:
        raise RuntimeError(
            "No vision client available for screen_diff: GEMINI_API_KEY is not set "
            "and Moondream is not running."
        )

    return [types.TextContent(type="text", text=answer)]


# ---------------------------------------------------------------------------
# Health server (port 9103)
# ---------------------------------------------------------------------------

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path in ("/health", "/"):
            body = json.dumps(
                {
                    "status": "ok",
                    "server": "vision-fallback",
                    "vision_provider": VISION_PROVIDER,
                    "gemini_configured": gemini is not None,
                    "moondream_available": moondream.is_available(),
                    "token_usage": gemini.get_token_usage() if gemini else 0,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):  # noqa: A002
        # Suppress default access log noise; route through Python logger instead.
        logger.debug("Health HTTP: " + format, *args)


def _start_health_server():
    try:
        httpd = HTTPServer(("0.0.0.0", HEALTH_PORT), _HealthHandler)
        logger.info("Health server listening on port %d", HEALTH_PORT)
        httpd.serve_forever()
    except Exception as exc:
        logger.error("Failed to start health server on port %d: %s", HEALTH_PORT, exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    # Start health server in a daemon thread so it doesn't block shutdown.
    health_thread = threading.Thread(target=_start_health_server, daemon=True)
    health_thread.start()

    logger.info(
        "Vision Fallback MCP server starting (provider=%s, gemini=%s)",
        VISION_PROVIDER,
        "configured" if gemini else "not configured",
    )

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
