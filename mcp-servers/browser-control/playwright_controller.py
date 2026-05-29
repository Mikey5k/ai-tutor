import asyncio
import json
import logging
import subprocess
import sys
from typing import Optional, List, Dict, Any

from playwright.async_api import (
    async_playwright,
    Browser,
    Page,
    Playwright,
    Request,
)

logger = logging.getLogger(__name__)

CHROME_USER_DATA_DIR = r"E:\ai-tutor\data\chrome-profile"
CHROME_DEBUG_PORT = 9222


class PlaywrightController:
    """Manages a Playwright browser connection to Chrome with remote debugging."""

    def __init__(self, debug_port: int = CHROME_DEBUG_PORT):
        self.debug_port = debug_port
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None
        self._network_requests: List[dict] = []

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def connect(self):
        """Connect to Chrome via CDP. Launch Chrome if not already running."""
        try:
            await self._do_connect()
        except Exception as connect_err:
            logger.warning(
                f"Could not connect to Chrome on port {self.debug_port}: {connect_err}. "
                "Attempting to launch Chrome..."
            )
            self._launch_chrome()
            # Give Chrome a moment to start up
            await asyncio.sleep(2)
            await self._do_connect()

    async def _do_connect(self):
        """Internal: actually connect via CDP and grab/create a page."""
        if self._playwright is None:
            self._playwright = await async_playwright().start()

        endpoint = f"http://localhost:{self.debug_port}"
        self._browser = await self._playwright.chromium.connect_over_cdp(endpoint)
        logger.info(f"Connected to Chrome at {endpoint}")

        # Use the first available context/page, or create a new one
        contexts = self._browser.contexts
        if contexts and contexts[0].pages:
            self._page = contexts[0].pages[0]
            logger.info(f"Reusing existing page: {self._page.url}")
        else:
            context = contexts[0] if contexts else await self._browser.new_context()
            self._page = await context.new_page()
            logger.info("Created new page")

        self._register_network_listener()

    def _launch_chrome(self):
        """Launch Chrome with remote debugging enabled."""
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        chrome_exe = None
        for path in chrome_paths:
            import os
            if os.path.exists(path):
                chrome_exe = path
                break

        if chrome_exe is None:
            raise RuntimeError(
                "Chrome not found. Install Chrome or set its path manually."
            )

        args = [
            chrome_exe,
            f"--remote-debugging-port={self.debug_port}",
            f"--user-data-dir={CHROME_USER_DATA_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info(f"Launched Chrome with remote debugging on port {self.debug_port}")

    def _register_network_listener(self):
        """Attach a request listener to capture network traffic."""
        if self._page is None:
            return

        def on_request(request: Request):
            self._network_requests.append(
                {
                    "url": request.url,
                    "method": request.method,
                    "resource_type": request.resource_type,
                    "headers": dict(request.headers),
                }
            )
            # Keep the list from growing unbounded
            if len(self._network_requests) > 1000:
                self._network_requests = self._network_requests[-500:]

        self._page.on("request", on_request)

    async def ensure_connected(self):
        """Reconnect if the connection is lost."""
        try:
            if self._page is None or self._browser is None:
                raise RuntimeError("Not connected")
            # Lightweight check: evaluate a trivial expression
            await self._page.evaluate("1 + 1")
        except Exception:
            logger.info("Connection lost or not established — reconnecting...")
            self._page = None
            self._browser = None
            await self.connect()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    async def navigate(self, url: str, wait_for: str = "networkidle") -> dict:
        """Navigate to a URL and return basic page info.

        Args:
            url: Target URL.
            wait_for: Playwright waitUntil strategy (networkidle, load, domcontentloaded).

        Returns:
            Dict with 'url' and 'title' keys.
        """
        await self._page.goto(url, wait_until=wait_for, timeout=30000)
        return {
            "url": self._page.url,
            "title": await self._page.title(),
        }

    async def get_url(self) -> str:
        """Return the current page URL."""
        return self._page.url

    async def get_page_text(self) -> str:
        """Return cleaned innerText of the page body."""
        raw = await self._page.evaluate("document.body.innerText")
        if not raw:
            return ""
        # Collapse excessive whitespace
        import re
        text = re.sub(r"\n{3,}", "\n\n", raw)
        text = re.sub(r"[^\S\n]{2,}", " ", text)
        return text.strip()

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    async def click(self, selector: str) -> bool:
        """Click an element identified by selector.

        Args:
            selector: CSS or text selector.

        Returns:
            True on success, False on failure.
        """
        try:
            await self._page.click(selector, timeout=5000)
            return True
        except Exception as e:
            logger.warning(f"click({selector!r}) failed: {e}")
            return False

    async def type_text(
        self, selector: str, text: str, clear_first: bool = True
    ) -> bool:
        """Type text into an input element.

        Args:
            selector: CSS or text selector.
            text: Text to type.
            clear_first: If True, select-all and delete existing content first.

        Returns:
            True on success, False on failure.
        """
        try:
            await self._page.click(selector, timeout=5000)
            if clear_first:
                await self._page.keyboard.press("Control+a")
                await self._page.keyboard.press("Delete")
            await self._page.type(selector, text)
            return True
        except Exception as e:
            logger.warning(f"type_text({selector!r}) failed: {e}")
            return False

    async def eval_js(self, javascript: str) -> Any:
        """Evaluate arbitrary JavaScript in the page context.

        Args:
            javascript: JS expression or function body.

        Returns:
            The result of the evaluation (JSON-serialisable types).
        """
        return await self._page.evaluate(javascript)

    async def wait_for(
        self, selector: str, state: str = "visible", timeout_ms: int = 5000
    ) -> bool:
        """Wait for an element to reach a given state.

        Args:
            selector: CSS selector.
            state: One of 'attached', 'detached', 'visible', 'hidden'.
            timeout_ms: Timeout in milliseconds.

        Returns:
            True if the element reached the desired state, False on timeout.
        """
        try:
            await self._page.wait_for_selector(
                selector, state=state, timeout=timeout_ms
            )
            return True
        except Exception as e:
            logger.warning(f"wait_for({selector!r}, state={state!r}) timed out: {e}")
            return False

    # ------------------------------------------------------------------
    # Scrolling / Navigation shortcuts
    # ------------------------------------------------------------------

    async def scroll(self, direction: str, amount_px: int) -> bool:
        """Scroll the page in the given direction.

        Args:
            direction: 'down', 'up', 'right', or 'left'.
            amount_px: Pixels to scroll.

        Returns:
            True on success, False on failure.
        """
        try:
            direction = direction.lower().strip()
            if direction == "down":
                x, y = 0, amount_px
            elif direction == "up":
                x, y = 0, -amount_px
            elif direction == "right":
                x, y = amount_px, 0
            elif direction == "left":
                x, y = -amount_px, 0
            else:
                logger.warning(f"Unknown scroll direction: {direction!r}")
                return False

            await self._page.evaluate(f"window.scrollBy({x}, {y})")
            return True
        except Exception as e:
            logger.warning(f"scroll({direction!r}, {amount_px}) failed: {e}")
            return False

    async def go_back(self) -> bool:
        """Navigate back in browser history.

        Returns:
            True on success, False on failure.
        """
        try:
            await self._page.go_back(timeout=10000)
            return True
        except Exception as e:
            logger.warning(f"go_back() failed: {e}")
            return False

    async def go_forward(self) -> bool:
        """Navigate forward in browser history.

        Returns:
            True on success, False on failure.
        """
        try:
            await self._page.go_forward(timeout=10000)
            return True
        except Exception as e:
            logger.warning(f"go_forward() failed: {e}")
            return False

    async def reload(self) -> bool:
        """Reload the current page.

        Returns:
            True on success, False on failure.
        """
        try:
            await self._page.reload(timeout=30000)
            return True
        except Exception as e:
            logger.warning(f"reload() failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Network requests
    # ------------------------------------------------------------------

    async def get_network_requests(
        self, filter_url: Optional[str] = None
    ) -> List[dict]:
        """Return captured network requests, optionally filtered by URL substring.

        Args:
            filter_url: If provided, only return requests whose URL contains this string.

        Returns:
            List of request dicts.
        """
        if filter_url:
            return [r for r in self._network_requests if filter_url in r["url"]]
        return list(self._network_requests)

    # ------------------------------------------------------------------
    # Accessibility tree
    # ------------------------------------------------------------------

    async def get_ax_tree_raw(self) -> dict:
        """Return the raw accessibility snapshot for the current page.

        Returns:
            AX tree as a dict (as returned by Playwright accessibility.snapshot()).
        """
        snapshot = await self._page.accessibility.snapshot()
        return snapshot if snapshot is not None else {}

    # ------------------------------------------------------------------
    # DOM querying
    # ------------------------------------------------------------------

    async def query_dom(
        self, selector: str, return_fields: List[str]
    ) -> List[dict]:
        """Query DOM elements by selector and return specified fields.

        Args:
            selector: CSS selector string.
            return_fields: List of property names to extract from each element.

        Returns:
            List of dicts with the requested fields.
        """
        # Build a JS snippet that extracts the requested fields
        fields_js = json.dumps(return_fields)
        js = f"""
        (() => {{
            const fields = {fields_js};
            const els = Array.from(document.querySelectorAll({json.dumps(selector)}));
            return els.map(el => {{
                const obj = {{}};
                for (const field of fields) {{
                    switch (field) {{
                        case 'tagName':
                            obj[field] = el.tagName ? el.tagName.toLowerCase() : null;
                            break;
                        case 'textContent':
                            obj[field] = el.textContent ? el.textContent.trim() : null;
                            break;
                        case 'innerText':
                            obj[field] = el.innerText ? el.innerText.trim() : null;
                            break;
                        case 'href':
                            obj[field] = el.href || el.getAttribute('href') || null;
                            break;
                        case 'src':
                            obj[field] = el.src || el.getAttribute('src') || null;
                            break;
                        case 'id':
                            obj[field] = el.id || null;
                            break;
                        case 'className':
                            obj[field] = el.className || null;
                            break;
                        case 'value':
                            obj[field] = el.value !== undefined ? el.value : null;
                            break;
                        case 'checked':
                            obj[field] = el.checked !== undefined ? el.checked : null;
                            break;
                        case 'disabled':
                            obj[field] = el.disabled !== undefined ? el.disabled : null;
                            break;
                        case 'placeholder':
                            obj[field] = el.getAttribute('placeholder') || null;
                            break;
                        case 'type':
                            obj[field] = el.type || el.getAttribute('type') || null;
                            break;
                        case 'name':
                            obj[field] = el.name || el.getAttribute('name') || null;
                            break;
                        case 'alt':
                            obj[field] = el.alt || el.getAttribute('alt') || null;
                            break;
                        case 'title':
                            obj[field] = el.title || el.getAttribute('title') || null;
                            break;
                        case 'ariaLabel':
                            obj[field] = el.getAttribute('aria-label') || null;
                            break;
                        case 'role':
                            obj[field] = el.getAttribute('role') || null;
                            break;
                        case 'outerHTML':
                            obj[field] = el.outerHTML || null;
                            break;
                        default:
                            obj[field] = el.getAttribute(field) || null;
                    }}
                }}
                return obj;
            }});
        }})()
        """
        result = await self._page.evaluate(js)
        return result if isinstance(result, list) else []

    # ------------------------------------------------------------------
    # DevTools
    # ------------------------------------------------------------------

    async def open_devtools(self) -> bool:
        """Open Chrome DevTools using the F12 keyboard shortcut.

        Returns:
            True on success, False on failure.
        """
        try:
            await self._page.keyboard.press("F12")
            return True
        except Exception as e:
            logger.warning(f"open_devtools() failed: {e}")
            return False
