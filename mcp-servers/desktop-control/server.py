#!/usr/bin/env python3
"""Desktop Control MCP Server.

Exposes 12 tools for controlling Windows desktop applications via UIA
accessibility, mouse/keyboard input, and process management.

Health endpoint: GET http://localhost:9101/health → 200 {"status": "ok"}
"""

import asyncio
import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Make sibling modules importable when running as __main__
sys.path.insert(0, str(Path(__file__).parent))

from uia_controller import UIAController
from input_controller import InputController
from app_launcher import AppLauncher

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,  # keep stdout clean for MCP stdio transport
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Controller singletons
# ---------------------------------------------------------------------------

uia = UIAController()
input_ctrl = InputController()
launcher = AppLauncher()

# ---------------------------------------------------------------------------
# MCP server instance
# ---------------------------------------------------------------------------

server = Server("desktop-control")

# ---------------------------------------------------------------------------
# Health HTTP server (port 9101)
# ---------------------------------------------------------------------------

_HEALTH_PORT = 9101


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            body = b'{"status": "ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):  # silence access logs
        pass


def _start_health_server() -> None:
    """Start the health HTTP server in a background daemon thread."""
    try:
        httpd = HTTPServer(("0.0.0.0", _HEALTH_PORT), _HealthHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        logger.info("Health server running on port %d", _HEALTH_PORT)
    except Exception as exc:
        logger.warning("Could not start health server: %s", exc)


# ---------------------------------------------------------------------------
# Tool helpers
# ---------------------------------------------------------------------------

def _ok(data) -> list[types.TextContent]:
    """Wrap a result (str or JSON-serialisable object) in a TextContent list."""
    if isinstance(data, str):
        text = data
    else:
        text = json.dumps(data, indent=2, default=str)
    return [types.TextContent(type="text", text=text)]


def _err(exc: Exception) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=f"ERROR: {exc}")]


# ---------------------------------------------------------------------------
# UIA tools  (1–4)
# ---------------------------------------------------------------------------

@server.tool()
async def uia_get_tree(app_name: str) -> list[types.TextContent]:
    """Return the full UIA accessibility tree of the named application window as JSON."""
    logger.info("uia_get_tree app_name=%r", app_name)
    try:
        result = uia.get_tree(app_name)
        return _ok(result)
    except Exception as exc:
        logger.exception("uia_get_tree failed")
        return _err(exc)


@server.tool()
async def uia_find_element(
    app_name: str,
    element_name: str = "",
    role: str = "",
) -> list[types.TextContent]:
    """Find a UIA element inside the named app by name and/or role.

    Returns element coordinates, state, and metadata as JSON, or an error.
    """
    logger.info(
        "uia_find_element app_name=%r element_name=%r role=%r",
        app_name, element_name, role,
    )
    try:
        result = uia.find_element(
            app_name,
            element_name=element_name or None,
            role=role or None,
        )
        return _ok(result if result is not None else {"found": False})
    except Exception as exc:
        logger.exception("uia_find_element failed")
        return _err(exc)


@server.tool()
async def uia_get_text(app_name: str, element_name: str) -> list[types.TextContent]:
    """Return the text content of the named element inside the app."""
    logger.info("uia_get_text app_name=%r element_name=%r", app_name, element_name)
    try:
        text = uia.get_text(app_name, element_name)
        return _ok(text)
    except Exception as exc:
        logger.exception("uia_get_text failed")
        return _err(exc)


@server.tool()
async def uia_wait_for_element(
    app_name: str,
    element_name: str,
    timeout_seconds: int = 10,
) -> list[types.TextContent]:
    """Block until the named element appears in the app, up to timeout_seconds.

    Returns element info as JSON, or the string 'timeout' if not found in time.
    """
    logger.info(
        "uia_wait_for_element app_name=%r element_name=%r timeout=%d",
        app_name, element_name, timeout_seconds,
    )
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: uia.wait_for_element(app_name, element_name, timeout_seconds),
        )
        if result is None:
            return _ok("timeout")
        return _ok(result)
    except Exception as exc:
        logger.exception("uia_wait_for_element failed")
        return _err(exc)


# ---------------------------------------------------------------------------
# Mouse tools  (5–6)
# ---------------------------------------------------------------------------

@server.tool()
async def mouse_move(x: int, y: int, smooth: bool = True) -> list[types.TextContent]:
    """Move the mouse cursor to absolute screen coordinates (x, y).

    When smooth=True the cursor glides over 0.3 s; otherwise it jumps instantly.
    """
    logger.info("mouse_move x=%d y=%d smooth=%s", x, y, smooth)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: input_ctrl.mouse_move(x, y, smooth))
        return _ok(f"Moved mouse to ({x}, {y})")
    except Exception as exc:
        logger.exception("mouse_move failed")
        return _err(exc)


@server.tool()
async def mouse_click(
    x: int,
    y: int,
    button: str = "left",
) -> list[types.TextContent]:
    """Click the mouse at absolute screen coordinates (x, y).

    button must be one of 'left', 'right', or 'middle'.
    """
    logger.info("mouse_click x=%d y=%d button=%r", x, y, button)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: input_ctrl.mouse_click(x, y, button)
        )
        return _ok(f"Clicked {button} at ({x}, {y})")
    except Exception as exc:
        logger.exception("mouse_click failed")
        return _err(exc)


# ---------------------------------------------------------------------------
# Keyboard tools  (7–8)
# ---------------------------------------------------------------------------

@server.tool()
async def keyboard_type(
    text: str,
    interval_ms: int = 50,
) -> list[types.TextContent]:
    """Type the given text string with realistic keystroke timing.

    interval_ms controls the delay between keystrokes in milliseconds.
    """
    logger.info("keyboard_type length=%d interval_ms=%d", len(text), interval_ms)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: input_ctrl.keyboard_type(text, interval_ms)
        )
        return _ok(f"Typed {len(text)} character(s)")
    except Exception as exc:
        logger.exception("keyboard_type failed")
        return _err(exc)


@server.tool()
async def keyboard_press(key: str) -> list[types.TextContent]:
    """Press a single key or a '+'-joined key combination.

    Examples: 'enter', 'escape', 'ctrl+s', 'ctrl+shift+esc'.
    """
    logger.info("keyboard_press key=%r", key)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: input_ctrl.keyboard_press(key))
        return _ok(f"Pressed key: {key}")
    except Exception as exc:
        logger.exception("keyboard_press failed")
        return _err(exc)


# ---------------------------------------------------------------------------
# App / window tools  (9–12)
# ---------------------------------------------------------------------------

@server.tool()
async def app_open(app_name_or_path: str) -> list[types.TextContent]:
    """Launch an application and wait for its main window to appear.

    Accepts a bare executable name (e.g. 'notepad') or a full path.
    Returns window info (hwnd, title, rect) as JSON.
    """
    logger.info("app_open app_name_or_path=%r", app_name_or_path)
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: launcher.open(app_name_or_path)
        )
        return _ok(result)
    except Exception as exc:
        logger.exception("app_open failed")
        return _err(exc)


@server.tool()
async def app_close(app_name: str) -> list[types.TextContent]:
    """Close the named application's window by sending WM_CLOSE."""
    logger.info("app_close app_name=%r", app_name)
    try:
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(
            None, lambda: launcher.close(app_name)
        )
        if success:
            return _ok(f"Close signal sent to '{app_name}'")
        return _ok(f"No window found for '{app_name}'")
    except Exception as exc:
        logger.exception("app_close failed")
        return _err(exc)


@server.tool()
async def app_focus(app_name: str) -> list[types.TextContent]:
    """Bring the named application's window to the foreground."""
    logger.info("app_focus app_name=%r", app_name)
    try:
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(
            None, lambda: launcher.focus(app_name)
        )
        if success:
            return _ok(f"Focused window for '{app_name}'")
        return _ok(f"No window found for '{app_name}'")
    except Exception as exc:
        logger.exception("app_focus failed")
        return _err(exc)


@server.tool()
async def window_get_state(app_name: str) -> list[types.TextContent]:
    """Return title, size, position, and minimized/maximized state of the window."""
    logger.info("window_get_state app_name=%r", app_name)
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: launcher.get_state(app_name)
        )
        return _ok(result)
    except Exception as exc:
        logger.exception("window_get_state failed")
        return _err(exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    _start_health_server()
    logger.info("Starting desktop-control MCP server (stdio transport)")
    async with stdio_server() as streams:
        await server.run(*streams, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
