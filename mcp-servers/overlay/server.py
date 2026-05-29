#!/usr/bin/env python3
"""
Overlay MCP Server
==================
Exposes 7 MCP tools for drawing transparent teaching-aid overlays on the
Windows desktop using a layered, click-through Win32 window.
"""

import asyncio
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Make sure sibling modules are importable when run directly
sys.path.insert(0, str(Path(__file__).parent))

from win32_window import OverlayWindow
from renderer import Renderer

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared singletons
# ---------------------------------------------------------------------------
window   = OverlayWindow()
renderer = Renderer(window)
server   = Server("overlay")

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="overlay_highlight",
            description=(
                "Draw a colored rectangle border on the screen to highlight "
                "a UI region. Fades out before expiry."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "x":           {"type": "integer", "description": "Left edge (pixels)"},
                    "y":           {"type": "integer", "description": "Top edge (pixels)"},
                    "width":       {"type": "integer", "description": "Width (pixels)"},
                    "height":      {"type": "integer", "description": "Height (pixels)"},
                    "color":       {"type": "string",  "default": "yellow",
                                   "description": "Border color name (yellow, red, cyan, green, blue, orange, white)"},
                    "duration_ms": {"type": "integer", "default": 3000,
                                   "description": "How long to show the highlight (0 = permanent)"},
                },
                "required": ["x", "y", "width", "height"],
            },
        ),
        types.Tool(
            name="overlay_spotlight",
            description=(
                "Draw a pulsing circle around a point of interest on screen."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "x":           {"type": "integer", "description": "Center X (pixels)"},
                    "y":           {"type": "integer", "description": "Center Y (pixels)"},
                    "radius":      {"type": "integer", "default": 60,
                                   "description": "Base circle radius (pixels)"},
                    "duration_ms": {"type": "integer", "default": 0,
                                   "description": "Duration in ms (0 = permanent)"},
                },
                "required": ["x", "y"],
            },
        ),
        types.Tool(
            name="overlay_arrow",
            description="Draw an arrow from one screen coordinate to another.",
            inputSchema={
                "type": "object",
                "properties": {
                    "from_x":      {"type": "integer", "description": "Arrow tail X"},
                    "from_y":      {"type": "integer", "description": "Arrow tail Y"},
                    "to_x":        {"type": "integer", "description": "Arrow head X"},
                    "to_y":        {"type": "integer", "description": "Arrow head Y"},
                    "color":       {"type": "string",  "default": "red",
                                   "description": "Arrow color name"},
                    "duration_ms": {"type": "integer", "default": 3000,
                                   "description": "Duration in ms (0 = permanent)"},
                },
                "required": ["from_x", "from_y", "to_x", "to_y"],
            },
        ),
        types.Tool(
            name="overlay_callout",
            description="Show a text callout bubble anchored to a screen position.",
            inputSchema={
                "type": "object",
                "properties": {
                    "x":           {"type": "integer", "description": "Anchor X (pixels)"},
                    "y":           {"type": "integer", "description": "Anchor Y (pixels)"},
                    "text":        {"type": "string",  "description": "Text to display"},
                    "duration_ms": {"type": "integer", "default": 5000,
                                   "description": "Duration in ms (0 = permanent)"},
                },
                "required": ["x", "y", "text"],
            },
        ),
        types.Tool(
            name="overlay_cursor_ring",
            description=(
                "Toggle a persistent ring that follows the mouse cursor, "
                "useful for drawing attention to where the tutor is pointing."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": True,
                               "description": "True to enable, False to disable"},
                    "color":   {"type": "string",  "default": "cyan",
                               "description": "Ring color name"},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="overlay_clear",
            description="Remove all overlay elements immediately.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        types.Tool(
            name="overlay_clear_all_after",
            description="Schedule removal of all overlay elements after a delay.",
            inputSchema={
                "type": "object",
                "properties": {
                    "delay_ms": {"type": "integer",
                                "description": "Delay before clearing (milliseconds)"},
                },
                "required": ["delay_ms"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool call handlers
# ---------------------------------------------------------------------------

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Dispatch incoming tool calls to the renderer."""

    if name == "overlay_highlight":
        element_id = renderer.add_highlight(
            x           = arguments["x"],
            y           = arguments["y"],
            width       = arguments["width"],
            height      = arguments["height"],
            color       = arguments.get("color", "yellow"),
            duration_ms = arguments.get("duration_ms", 3000),
        )
        return [types.TextContent(type="text", text=element_id)]

    elif name == "overlay_spotlight":
        element_id = renderer.add_spotlight(
            x           = arguments["x"],
            y           = arguments["y"],
            radius      = arguments.get("radius", 60),
            duration_ms = arguments.get("duration_ms", 0),
        )
        return [types.TextContent(type="text", text=element_id)]

    elif name == "overlay_arrow":
        element_id = renderer.add_arrow(
            from_x      = arguments["from_x"],
            from_y      = arguments["from_y"],
            to_x        = arguments["to_x"],
            to_y        = arguments["to_y"],
            color       = arguments.get("color", "red"),
            duration_ms = arguments.get("duration_ms", 3000),
        )
        return [types.TextContent(type="text", text=element_id)]

    elif name == "overlay_callout":
        element_id = renderer.add_callout(
            x           = arguments["x"],
            y           = arguments["y"],
            text        = arguments["text"],
            duration_ms = arguments.get("duration_ms", 5000),
        )
        return [types.TextContent(type="text", text=element_id)]

    elif name == "overlay_cursor_ring":
        renderer.set_cursor_ring(
            enabled = arguments.get("enabled", True),
            color   = arguments.get("color", "cyan"),
        )
        state = "enabled" if arguments.get("enabled", True) else "disabled"
        return [types.TextContent(type="text", text=f"cursor_ring {state}")]

    elif name == "overlay_clear":
        renderer.clear_all()
        return [types.TextContent(type="text", text="cleared")]

    elif name == "overlay_clear_all_after":
        delay_ms = arguments["delay_ms"]
        renderer.schedule_clear_all(delay_ms)
        return [types.TextContent(type="text", text=f"scheduled clear in {delay_ms}ms")]

    else:
        return [types.TextContent(type="text", text=f"unknown tool: {name}")]


# ---------------------------------------------------------------------------
# Health-check HTTP server (port 9100)
# ---------------------------------------------------------------------------

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Suppress default HTTP access log noise
        pass


def _start_health_server():
    try:
        httpd = HTTPServer(("127.0.0.1", 9100), _HealthHandler)
        logger.info("Health server listening on http://127.0.0.1:9100/")
        httpd.serve_forever()
    except Exception as exc:
        logger.warning("Could not start health server: %s", exc)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

async def startup():
    """Create the overlay window and start the renderer before serving MCP."""
    window.create()
    window.on_paint = renderer._paint
    renderer.start()
    logger.info("Overlay window created and renderer started")

    # Health server runs in its own daemon thread
    health_thread = threading.Thread(
        target=_start_health_server, daemon=True, name="HealthServer"
    )
    health_thread.start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    await startup()
    async with stdio_server() as streams:
        await server.run(*streams, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
