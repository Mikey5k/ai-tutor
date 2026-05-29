#!/usr/bin/env python3
"""Browser Control MCP Server for AI Tutor system.

Exposes 15 browser-control tools via the MCP protocol over stdio,
backed by Playwright connected to a Chrome instance with remote debugging.
"""

import asyncio
import json
import logging
import sys
import os
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

sys.path.insert(0, str(Path(__file__).parent))

from playwright_controller import PlaywrightController
from dom_parser import DOMParser
from ax_tree_parser import AXTreeParser

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
# Singletons
# ---------------------------------------------------------------------------

controller = PlaywrightController()
dom_parser = DOMParser()
ax_parser = AXTreeParser()

server = Server("browser-control")

# ---------------------------------------------------------------------------
# Health check HTTP server (port 9102)
# ---------------------------------------------------------------------------

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path in ("/", "/health"):
            body = b'{"status": "ok", "server": "browser-control"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):  # suppress default access logs
        pass


def _start_health_server(port: int = 9102):
    httpd = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Health check server listening on port {port}")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="browser_navigate",
            description="Navigate the browser to a URL and wait for the page to load.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to navigate to.",
                    },
                    "wait_for": {
                        "type": "string",
                        "description": "Load strategy: networkidle (default), load, or domcontentloaded.",
                        "default": "networkidle",
                    },
                },
                "required": ["url"],
            },
        ),
        types.Tool(
            name="browser_get_ax_tree",
            description="Get the accessibility tree of the current page, parsed and cleaned.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="browser_query_dom",
            description=(
                "Query DOM elements by CSS selector and return specified fields. "
                "Available fields: tagName, textContent, innerText, href, src, id, "
                "className, value, checked, disabled, placeholder, type, name, alt, "
                "title, ariaLabel, role, outerHTML."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector to match elements.",
                    },
                    "return_fields": {
                        "type": "string",
                        "description": "Comma-separated list of fields to return (e.g. 'tagName,textContent,href').",
                    },
                },
                "required": ["selector", "return_fields"],
            },
        ),
        types.Tool(
            name="browser_click",
            description="Click a DOM element identified by a CSS selector.",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the element to click.",
                    },
                },
                "required": ["selector"],
            },
        ),
        types.Tool(
            name="browser_type",
            description="Type text into an input or textarea element.",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the input element.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to type.",
                    },
                    "clear_first": {
                        "type": "boolean",
                        "description": "Clear existing content before typing (default: true).",
                        "default": True,
                    },
                },
                "required": ["selector", "text"],
            },
        ),
        types.Tool(
            name="browser_eval",
            description="Execute arbitrary JavaScript in the current page context and return the result.",
            inputSchema={
                "type": "object",
                "properties": {
                    "javascript": {
                        "type": "string",
                        "description": "JavaScript expression or function body to evaluate.",
                    },
                },
                "required": ["javascript"],
            },
        ),
        types.Tool(
            name="browser_wait_for",
            description="Wait for a DOM element to reach a specified state.",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector to wait for.",
                    },
                    "state": {
                        "type": "string",
                        "description": "Element state to wait for: visible (default), hidden, attached, detached.",
                        "default": "visible",
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "description": "Timeout in milliseconds (default: 5000).",
                        "default": 5000,
                    },
                },
                "required": ["selector"],
            },
        ),
        types.Tool(
            name="browser_get_url",
            description="Get the current URL of the browser page.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="browser_get_page_text",
            description="Get the visible text content of the current page, cleaned of noise.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="browser_scroll",
            description="Scroll the page in a given direction by a number of pixels.",
            inputSchema={
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "description": "Scroll direction: down, up, left, or right.",
                    },
                    "amount_px": {
                        "type": "integer",
                        "description": "Number of pixels to scroll (default: 300).",
                        "default": 300,
                    },
                },
                "required": ["direction"],
            },
        ),
        types.Tool(
            name="browser_go_back",
            description="Navigate back in the browser history.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="browser_go_forward",
            description="Navigate forward in the browser history.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="browser_reload",
            description="Reload the current page.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="browser_open_devtools_console",
            description="Open Chrome DevTools (F12).",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="browser_get_network_requests",
            description="Return captured network requests, optionally filtered by URL substring.",
            inputSchema={
                "type": "object",
                "properties": {
                    "filter_url": {
                        "type": "string",
                        "description": "Only return requests whose URL contains this string. Leave empty for all.",
                        "default": "",
                    },
                },
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------

def _text(content: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=content)]


@server.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent]:
    try:
        await controller.ensure_connected()
    except Exception as e:
        return _text(json.dumps({"error": f"Browser not available: {e}"}))

    try:
        # ----------------------------------------------------------------
        # 1. browser_navigate
        # ----------------------------------------------------------------
        if name == "browser_navigate":
            url = arguments["url"]
            wait_for = arguments.get("wait_for", "networkidle")
            result = await controller.navigate(url, wait_for=wait_for)
            return _text(json.dumps(result))

        # ----------------------------------------------------------------
        # 2. browser_get_ax_tree
        # ----------------------------------------------------------------
        elif name == "browser_get_ax_tree":
            raw = await controller.get_ax_tree_raw()
            parsed = ax_parser.parse(raw)
            return _text(json.dumps(parsed, indent=2))

        # ----------------------------------------------------------------
        # 3. browser_query_dom
        # ----------------------------------------------------------------
        elif name == "browser_query_dom":
            selector = arguments["selector"]
            fields_str = arguments["return_fields"]
            return_fields = [f.strip() for f in fields_str.split(",") if f.strip()]
            raw_elements = await controller.query_dom(selector, return_fields)
            cleaned = dom_parser.parse_elements(raw_elements, return_fields)
            return _text(json.dumps(cleaned, indent=2))

        # ----------------------------------------------------------------
        # 4. browser_click
        # ----------------------------------------------------------------
        elif name == "browser_click":
            selector = arguments["selector"]
            success = await controller.click(selector)
            result = {"success": success, "selector": selector}
            return _text(json.dumps(result))

        # ----------------------------------------------------------------
        # 5. browser_type
        # ----------------------------------------------------------------
        elif name == "browser_type":
            selector = arguments["selector"]
            text = arguments["text"]
            clear_first = arguments.get("clear_first", True)
            success = await controller.type_text(selector, text, clear_first=clear_first)
            result = {"success": success, "selector": selector}
            return _text(json.dumps(result))

        # ----------------------------------------------------------------
        # 6. browser_eval
        # ----------------------------------------------------------------
        elif name == "browser_eval":
            javascript = arguments["javascript"]
            result = await controller.eval_js(javascript)
            # Ensure JSON-serialisable
            try:
                return _text(json.dumps({"result": result}))
            except (TypeError, ValueError):
                return _text(json.dumps({"result": str(result)}))

        # ----------------------------------------------------------------
        # 7. browser_wait_for
        # ----------------------------------------------------------------
        elif name == "browser_wait_for":
            selector = arguments["selector"]
            state = arguments.get("state", "visible")
            timeout_ms = int(arguments.get("timeout_ms", 5000))
            success = await controller.wait_for(selector, state=state, timeout_ms=timeout_ms)
            result = {"success": success, "selector": selector, "state": state}
            return _text(json.dumps(result))

        # ----------------------------------------------------------------
        # 8. browser_get_url
        # ----------------------------------------------------------------
        elif name == "browser_get_url":
            url = await controller.get_url()
            return _text(json.dumps({"url": url}))

        # ----------------------------------------------------------------
        # 9. browser_get_page_text
        # ----------------------------------------------------------------
        elif name == "browser_get_page_text":
            raw_text = await controller.get_page_text()
            cleaned = dom_parser.extract_page_text(raw_text)
            return _text(cleaned)

        # ----------------------------------------------------------------
        # 10. browser_scroll
        # ----------------------------------------------------------------
        elif name == "browser_scroll":
            direction = arguments["direction"]
            amount_px = int(arguments.get("amount_px", 300))
            success = await controller.scroll(direction, amount_px)
            result = {"success": success, "direction": direction, "amount_px": amount_px}
            return _text(json.dumps(result))

        # ----------------------------------------------------------------
        # 11. browser_go_back
        # ----------------------------------------------------------------
        elif name == "browser_go_back":
            success = await controller.go_back()
            return _text(json.dumps({"success": success}))

        # ----------------------------------------------------------------
        # 12. browser_go_forward
        # ----------------------------------------------------------------
        elif name == "browser_go_forward":
            success = await controller.go_forward()
            return _text(json.dumps({"success": success}))

        # ----------------------------------------------------------------
        # 13. browser_reload
        # ----------------------------------------------------------------
        elif name == "browser_reload":
            success = await controller.reload()
            return _text(json.dumps({"success": success}))

        # ----------------------------------------------------------------
        # 14. browser_open_devtools_console
        # ----------------------------------------------------------------
        elif name == "browser_open_devtools_console":
            success = await controller.open_devtools()
            return _text(json.dumps({"success": success}))

        # ----------------------------------------------------------------
        # 15. browser_get_network_requests
        # ----------------------------------------------------------------
        elif name == "browser_get_network_requests":
            filter_url = arguments.get("filter_url", "") or ""
            requests = await controller.get_network_requests(
                filter_url=filter_url if filter_url else None
            )
            return _text(json.dumps(requests, indent=2))

        else:
            return _text(json.dumps({"error": f"Unknown tool: {name}"}))

    except Exception as e:
        logger.exception(f"Error executing tool {name!r}: {e}")
        return _text(json.dumps({"error": str(e), "tool": name}))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    # Start health check HTTP server in background
    _start_health_server(port=9102)

    # Attempt browser connection on startup (non-fatal if Chrome isn't running yet)
    try:
        await controller.connect()
        logger.info("Browser connection established on startup.")
    except Exception as e:
        logger.warning(f"Could not connect to browser on startup: {e}")

    async with stdio_server() as streams:
        await server.run(*streams, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
