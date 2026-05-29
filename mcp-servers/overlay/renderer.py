import ctypes
import ctypes.wintypes
import win32gui
import win32api
import win32con
import time
import threading
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

gdi32  = ctypes.windll.gdi32
user32 = ctypes.windll.user32

# GDI pen style
PS_SOLID = 0
# DrawText flags
DT_LEFT       = 0x00000000
DT_TOP        = 0x00000000
DT_SINGLELINE = 0x00000020
DT_NOCLIP     = 0x00000100
DT_WORDBREAK  = 0x00000010


@dataclass
class OverlayElement:
    element_id: str
    element_type: str   # highlight | spotlight | arrow | callout | cursor_ring
    params: dict
    created_at: float
    expires_at: float   # 0.0 = permanent (never expires)


class Renderer:
    def __init__(self, window):
        self.window = window
        self._elements: List[OverlayElement] = []
        self._elements_lock = threading.Lock()
        self._cursor_ring_enabled = False
        self._cursor_ring_color   = (0, 255, 255)   # cyan
        self._render_thread: Optional[threading.Thread] = None
        self._running = False
        self._element_counter = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Start the 30 fps render loop in a background thread."""
        self._running = True
        self._render_thread = threading.Thread(
            target=self._render_loop, daemon=True, name="OverlayRenderLoop"
        )
        self._render_thread.start()

    def stop(self):
        """Stop the render loop."""
        self._running = False
        if self._render_thread:
            self._render_thread.join(timeout=2)

    # ------------------------------------------------------------------
    # Public element API
    # ------------------------------------------------------------------

    def add_highlight(
        self,
        x: int, y: int,
        width: int, height: int,
        color: str = "yellow",
        duration_ms: int = 3000,
    ) -> str:
        """Draw a colored rectangle border. Returns element_id."""
        element_id = self._next_id("highlight")
        now = time.monotonic()
        elem = OverlayElement(
            element_id=element_id,
            element_type="highlight",
            params={
                "x": x, "y": y,
                "width": width, "height": height,
                "color": color,
            },
            created_at=now,
            expires_at=now + duration_ms / 1000.0 if duration_ms > 0 else 0.0,
        )
        with self._elements_lock:
            self._elements.append(elem)
        self.window.invalidate()
        return element_id

    def add_spotlight(
        self,
        x: int, y: int,
        radius: int = 60,
        duration_ms: int = 0,
    ) -> str:
        """Draw a pulsing circle. Returns element_id."""
        element_id = self._next_id("spotlight")
        now = time.monotonic()
        elem = OverlayElement(
            element_id=element_id,
            element_type="spotlight",
            params={"x": x, "y": y, "radius": radius},
            created_at=now,
            expires_at=now + duration_ms / 1000.0 if duration_ms > 0 else 0.0,
        )
        with self._elements_lock:
            self._elements.append(elem)
        self.window.invalidate()
        return element_id

    def add_arrow(
        self,
        from_x: int, from_y: int,
        to_x: int, to_y: int,
        color: str = "red",
        duration_ms: int = 3000,
    ) -> str:
        """Draw an arrow from one point to another. Returns element_id."""
        element_id = self._next_id("arrow")
        now = time.monotonic()
        elem = OverlayElement(
            element_id=element_id,
            element_type="arrow",
            params={
                "from_x": from_x, "from_y": from_y,
                "to_x": to_x, "to_y": to_y,
                "color": color,
            },
            created_at=now,
            expires_at=now + duration_ms / 1000.0 if duration_ms > 0 else 0.0,
        )
        with self._elements_lock:
            self._elements.append(elem)
        self.window.invalidate()
        return element_id

    def add_callout(
        self,
        x: int, y: int,
        text: str,
        duration_ms: int = 5000,
    ) -> str:
        """Show a text bubble anchored to a position. Returns element_id."""
        element_id = self._next_id("callout")
        now = time.monotonic()
        elem = OverlayElement(
            element_id=element_id,
            element_type="callout",
            params={"x": x, "y": y, "text": text},
            created_at=now,
            expires_at=now + duration_ms / 1000.0 if duration_ms > 0 else 0.0,
        )
        with self._elements_lock:
            self._elements.append(elem)
        self.window.invalidate()
        return element_id

    def set_cursor_ring(self, enabled: bool, color: str = "cyan"):
        """Toggle a persistent ring that follows the cursor."""
        self._cursor_ring_enabled = enabled
        self._cursor_ring_color   = self._parse_color(color)
        self.window.invalidate()

    def clear_all(self):
        """Remove all overlay elements immediately."""
        with self._elements_lock:
            self._elements.clear()
        self.window.invalidate()

    def clear_element(self, element_id: str):
        """Remove a specific element by id."""
        with self._elements_lock:
            self._elements = [e for e in self._elements if e.element_id != element_id]
        self.window.invalidate()

    def schedule_clear_all(self, delay_ms: int):
        """Schedule clear_all to run after delay_ms milliseconds."""
        threading.Timer(delay_ms / 1000.0, self.clear_all).start()

    # ------------------------------------------------------------------
    # Render loop
    # ------------------------------------------------------------------

    def _render_loop(self):
        """30 fps loop: expire stale elements and request repaints."""
        interval = 1.0 / 30.0
        while self._running:
            time.sleep(interval)
            now = time.monotonic()
            with self._elements_lock:
                # Remove any elements whose lifetime has elapsed
                self._elements = [
                    e for e in self._elements
                    if e.expires_at == 0.0 or e.expires_at > now
                ]
            self.window.invalidate()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def _paint(self, hwnd):
        """Called from WM_PAINT. Draws all active elements onto the overlay."""
        hdc, ps = win32gui.BeginPaint(hwnd)
        try:
            now = time.monotonic()

            # Fill the whole window with the transparent color key (black).
            # This erases the previous frame; black is made invisible by SetLayeredWindowAttributes.
            rect = win32gui.GetClientRect(hwnd)
            black_brush = gdi32.CreateSolidBrush(self._make_gdi_color(0, 0, 0))
            user32.FillRect(hdc, ctypes.byref(self._rect_to_struct(rect)), black_brush)
            gdi32.DeleteObject(black_brush)

            # Set transparent drawing mode so the black background shows through
            gdi32.SetBkMode(hdc, 1)  # TRANSPARENT

            with self._elements_lock:
                elements_snapshot = list(self._elements)

            for elem in elements_snapshot:
                if elem.expires_at != 0.0 and elem.expires_at <= now:
                    continue  # Stale; will be removed on next loop iteration

                elapsed  = now - elem.created_at
                lifetime = (elem.expires_at - elem.created_at) if elem.expires_at != 0.0 else 1.0
                progress = min(elapsed / lifetime, 1.0) if lifetime > 0 else 0.0

                etype = elem.element_type
                p     = elem.params

                if etype == "highlight":
                    self._draw_highlight(hdc, p, progress)
                elif etype == "spotlight":
                    self._draw_spotlight(hdc, p, elapsed)
                elif etype == "arrow":
                    self._draw_arrow(hdc, p, progress)
                elif etype == "callout":
                    self._draw_callout(hdc, p, progress)

            # Cursor ring is stateless — always drawn on top if enabled
            if self._cursor_ring_enabled:
                self._draw_cursor_ring(hdc)

        finally:
            win32gui.EndPaint(hwnd, ps)

    # ------------------------------------------------------------------
    # Per-element drawing helpers
    # ------------------------------------------------------------------

    def _draw_highlight(self, hdc, p, progress):
        """Colored rectangle border with fade-out near the end."""
        r, g, b = self._parse_color(p["color"])

        # Fade: at 80 % lifetime remaining fully opaque; fade out in the last 20 %
        fade = max(0.0, min(1.0, (1.0 - progress) / 0.2)) if progress > 0.8 else 1.0
        # Modulate brightness to simulate fade (no alpha in plain GDI)
        r2 = int(r * fade)
        g2 = int(g * fade)
        b2 = int(b * fade)

        pen_width = 3
        pen = gdi32.CreatePen(PS_SOLID, pen_width, self._make_gdi_color(r2, g2, b2))
        old_pen = gdi32.SelectObject(hdc, pen)

        # Null brush so the interior stays transparent (black = transparent)
        null_brush = gdi32.GetStockObject(5)  # NULL_BRUSH
        old_brush = gdi32.SelectObject(hdc, null_brush)

        x, y, w, h = p["x"], p["y"], p["width"], p["height"]
        gdi32.Rectangle(hdc, x, y, x + w, y + h)

        gdi32.SelectObject(hdc, old_pen)
        gdi32.SelectObject(hdc, old_brush)
        gdi32.DeleteObject(pen)

    def _draw_spotlight(self, hdc, p, elapsed):
        """Pulsing circle: radius oscillates with a sine wave."""
        x, y  = p["x"], p["y"]
        base_r = p["radius"]
        pulse  = int(math.sin(elapsed * 4.0) * 8)   # ±8 px at 4 rad/s
        r_now  = max(10, base_r + pulse)

        pen = gdi32.CreatePen(PS_SOLID, 3, self._make_gdi_color(0, 255, 255))
        old_pen = gdi32.SelectObject(hdc, pen)
        null_brush = gdi32.GetStockObject(5)
        old_brush = gdi32.SelectObject(hdc, null_brush)

        gdi32.Ellipse(hdc, x - r_now, y - r_now, x + r_now, y + r_now)

        gdi32.SelectObject(hdc, old_pen)
        gdi32.SelectObject(hdc, old_brush)
        gdi32.DeleteObject(pen)

    def _draw_arrow(self, hdc, p, progress):
        """Line with arrowhead triangle at the destination."""
        r, g, b = self._parse_color(p["color"])
        fade = max(0.0, min(1.0, (1.0 - progress) / 0.2)) if progress > 0.8 else 1.0
        color_ref = self._make_gdi_color(int(r * fade), int(g * fade), int(b * fade))

        fx, fy = p["from_x"], p["from_y"]
        tx, ty = p["to_x"],   p["to_y"]

        pen = gdi32.CreatePen(PS_SOLID, 3, color_ref)
        old_pen = gdi32.SelectObject(hdc, pen)
        null_brush = gdi32.GetStockObject(5)
        old_brush = gdi32.SelectObject(hdc, null_brush)

        # Shaft
        gdi32.MoveToEx(hdc, fx, fy, None)
        gdi32.LineTo(hdc, tx, ty)

        # Arrowhead: equilateral triangle pointing toward (tx, ty)
        angle = math.atan2(ty - fy, tx - fx)
        head_len = 18
        head_angle = math.pi / 6   # 30 degrees

        ax1 = int(tx - head_len * math.cos(angle - head_angle))
        ay1 = int(ty - head_len * math.sin(angle - head_angle))
        ax2 = int(tx - head_len * math.cos(angle + head_angle))
        ay2 = int(ty - head_len * math.sin(angle + head_angle))

        # Filled arrowhead
        solid_brush = gdi32.CreateSolidBrush(color_ref)
        old_brush2  = gdi32.SelectObject(hdc, solid_brush)

        POINT = ctypes.wintypes.POINT
        pts = (POINT * 3)(POINT(tx, ty), POINT(ax1, ay1), POINT(ax2, ay2))
        gdi32.Polygon(hdc, pts, 3)

        gdi32.SelectObject(hdc, old_pen)
        gdi32.SelectObject(hdc, old_brush2)
        gdi32.DeleteObject(pen)
        gdi32.DeleteObject(solid_brush)
        # Restore null brush
        gdi32.SelectObject(hdc, old_brush)

    def _draw_callout(self, hdc, p, progress):
        """Text bubble: background rectangle + text."""
        x, y   = p["x"], p["y"]
        text   = p["text"]

        # Fade
        fade = max(0.0, min(1.0, (1.0 - progress) / 0.2)) if progress > 0.8 else 1.0
        brightness = int(255 * fade)

        # Background: dark semi-opaque rectangle (dark grey looks close to opaque)
        bg_r = int(30  * fade)
        bg_g = int(30  * fade)
        bg_b = int(30  * fade)
        bg_brush = gdi32.CreateSolidBrush(self._make_gdi_color(bg_r, bg_g, bg_b))

        padding  = 8
        char_w   = 8   # approximate; good enough for overlay
        char_h   = 16
        lines    = text.split("\n")
        box_w    = max(len(line) for line in lines) * char_w + padding * 2
        box_h    = len(lines) * char_h + padding * 2

        # Keep callout on-screen
        screen_w = self.window.width
        screen_h = self.window.height
        bx = min(x, screen_w - box_w - 4)
        by = min(y, screen_h - box_h - 4)
        bx = max(bx, 0)
        by = max(by, 0)

        bg_rect = self._make_rect(bx, by, bx + box_w, by + box_h)
        user32.FillRect(hdc, ctypes.byref(bg_rect), bg_brush)
        gdi32.DeleteObject(bg_brush)

        # Border
        border_pen = gdi32.CreatePen(PS_SOLID, 2, self._make_gdi_color(brightness, brightness, 0))
        old_pen    = gdi32.SelectObject(hdc, border_pen)
        null_brush = gdi32.GetStockObject(5)
        old_brush  = gdi32.SelectObject(hdc, null_brush)
        gdi32.Rectangle(hdc, bx, by, bx + box_w, by + box_h)
        gdi32.SelectObject(hdc, old_pen)
        gdi32.SelectObject(hdc, old_brush)
        gdi32.DeleteObject(border_pen)

        # Text
        text_color = self._make_gdi_color(brightness, brightness, brightness)
        win32gui.SetTextColor(hdc, text_color)
        gdi32.SetBkMode(hdc, 1)  # TRANSPARENT

        text_rect = self._make_rect(
            bx + padding, by + padding,
            bx + box_w - padding, by + box_h - padding,
        )
        win32gui.DrawText(
            hdc, text, len(text), text_rect,
            DT_LEFT | DT_TOP | DT_WORDBREAK | DT_NOCLIP,
        )

    def _draw_cursor_ring(self, hdc):
        """Draw a circle around the current cursor position."""
        try:
            cx, cy = win32api.GetCursorPos()
        except Exception:
            return

        r, g, b = self._cursor_ring_color
        radius  = 20

        # Outer ring
        pen = gdi32.CreatePen(PS_SOLID, 3, self._make_gdi_color(r, g, b))
        old_pen   = gdi32.SelectObject(hdc, pen)
        null_brush = gdi32.GetStockObject(5)
        old_brush = gdi32.SelectObject(hdc, null_brush)

        gdi32.Ellipse(hdc, cx - radius, cy - radius, cx + radius, cy + radius)

        # Small crosshair dot at center
        dot_r = 3
        solid_brush = gdi32.CreateSolidBrush(self._make_gdi_color(r, g, b))
        old_brush2  = gdi32.SelectObject(hdc, solid_brush)
        gdi32.Ellipse(hdc, cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r)

        gdi32.SelectObject(hdc, old_pen)
        gdi32.SelectObject(hdc, old_brush2)
        gdi32.DeleteObject(pen)
        gdi32.DeleteObject(solid_brush)
        gdi32.SelectObject(hdc, old_brush)

    # ------------------------------------------------------------------
    # Color & geometry helpers
    # ------------------------------------------------------------------

    def _parse_color(self, color_name: str) -> Tuple[int, int, int]:
        """Convert a color name to an (R, G, B) tuple."""
        _map = {
            "yellow":  (255, 255,   0),
            "red":     (255,   0,   0),
            "cyan":    (  0, 255, 255),
            "green":   (  0, 255,   0),
            "blue":    (  0,   0, 255),
            "orange":  (255, 165,   0),
            "white":   (255, 255, 255),
        }
        return _map.get(color_name.lower(), (255, 255, 0))  # default: yellow

    def _make_gdi_color(self, r: int, g: int, b: int) -> int:
        """Convert (R, G, B) to a Windows COLORREF value (stored as 0x00BBGGRR)."""
        return r | (g << 8) | (b << 16)

    def _make_rect(self, left, top, right, bottom):
        """Return a ctypes RECT structure."""
        rc = ctypes.wintypes.RECT()
        rc.left   = left
        rc.top    = top
        rc.right  = right
        rc.bottom = bottom
        return rc

    def _rect_to_struct(self, rect_tuple):
        """Convert a (left, top, right, bottom) tuple to a ctypes RECT."""
        left, top, right, bottom = rect_tuple
        return self._make_rect(left, top, right, bottom)

    # ------------------------------------------------------------------
    # ID generation
    # ------------------------------------------------------------------

    def _next_id(self, prefix: str) -> str:
        self._element_counter += 1
        return f"{prefix}_{self._element_counter}"
