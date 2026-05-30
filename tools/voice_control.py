#!/usr/bin/env python3
"""
Voice Control Panel — Win32 native floating control panel for the AI tutor voice system.

Polls http://localhost:9103 for status and sends control commands via HTTP GET.
Translucent when idle (alpha=70), nearly solid on hover (alpha=235), with smooth
animated transition.

Usage:
  python E:/ai-tutor/tools/voice_control.py
"""

import ctypes
import ctypes.wintypes
import json
import math
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field

import win32api
import win32con
import win32gui

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------
WS_EX_LAYERED    = 0x00080000
WS_EX_TOPMOST    = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_POPUP         = 0x80000000
WS_VISIBLE       = 0x10000000
LWA_COLORKEY     = 0x00000001
LWA_ALPHA        = 0x00000002
CS_HREDRAW       = 0x0002
CS_VREDRAW       = 0x0001
PS_SOLID         = 0
NULL_BRUSH        = 5
NULL_PEN          = 8
WM_USER           = 0x0400
WM_ANIM           = WM_USER + 1
TMEV              = ctypes.c_uint32

gdi32  = ctypes.windll.gdi32
user32 = ctypes.windll.user32

# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
W, H   = 300, 260
CORNER = 16

# ---------------------------------------------------------------------------
# Colors (R, G, B) — avoid pure black which is the transparent key
# ---------------------------------------------------------------------------
BG          = (18, 18, 18)
BORDER      = (65, 65, 65)
SEP         = (55, 55, 55)
TRANSPARENT = 0x000000   # black = invisible via LWA_COLORKEY

DOT_COLORS = {
    "offline":  (210, 45,  45),
    "loading":  (255, 160, 0),
    "ready":    (45,  210, 75),
    "speaking": (45,  130, 225),
    "mic_off":  (90,  90,  90),
}
STATUS_LABELS = {
    "offline":  "Offline",
    "loading":  "Loading...",
    "ready":    "Listening",
    "speaking": "Speaking",
    "mic_off":  "Mic Off",
}

# Translucency levels
ALPHA_IDLE  = 70
ALPHA_HOVER = 235
ALPHA_STEP  = 15

VOICE_URL = "http://localhost:9103"

# ---------------------------------------------------------------------------
# TrackMouseEvent
# ---------------------------------------------------------------------------
class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [
        ("cbSize",      ctypes.c_uint32),
        ("dwFlags",     ctypes.c_uint32),
        ("hwndTrack",   ctypes.c_void_p),
        ("dwHoverTime", ctypes.c_uint32),
    ]

TME_LEAVE = 0x00000002


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def rgb(r, g, b) -> int:
    """Pack (R,G,B) into a Windows COLORREF (0x00BBGGRR)."""
    return r | (g << 8) | (b << 16)


def _to_rect(t):
    rc = ctypes.wintypes.RECT()
    rc.left, rc.top, rc.right, rc.bottom = t
    return rc


def _make_font(height: int = -13, weight: int = 400, face: str = "Segoe UI"):
    """Create a GDI font. Returns HFONT."""
    return gdi32.CreateFontW(
        height, 0, 0, 0, weight,
        0, 0, 0,   # italic, underline, strikeout
        0,         # ANSI_CHARSET
        0, 0,      # OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS
        5,         # CLEARTYPE_QUALITY
        0,         # DEFAULT_PITCH | FF_DONTCARE
        ctypes.create_unicode_buffer(face),
    )


def _pt_in_rect(x, y, rect) -> bool:
    """Return True if (x,y) is inside rect (x1,y1,x2,y2)."""
    x1, y1, x2, y2 = rect
    return x1 <= x < x2 and y1 <= y < y2


# ---------------------------------------------------------------------------
# Button data class
# ---------------------------------------------------------------------------
@dataclass
class Button:
    label:    str
    rect:     tuple   # (x1, y1, x2, y2)
    action:   str     # URL path+query to GET
    hovered:  bool = False
    flashing: bool = False


# ---------------------------------------------------------------------------
# Main panel class
# ---------------------------------------------------------------------------
class VoiceControlPanel:
    CLASS = "VoiceControlPanel"

    def __init__(self):
        self.hwnd               = None
        self._state             = "offline"
        self._mic_on            = True
        self._level             = 0.0
        self._progress          = 0
        self._topic             = ""
        self._pace              = "normal"
        self._session_secs      = 0
        self._alpha             = ALPHA_IDLE
        self._alpha_target      = ALPHA_IDLE
        self._mouse_tracking    = False
        self._hfont_small       = None   # 8pt
        self._hfont_normal      = None   # 9pt
        self._hfont_bold        = None   # 10pt bold
        self._close_hovered     = False
        self._buttons           = []
        self._last_spoken_preview = ""
        self._t0                = time.monotonic()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self):
        self._create_window()
        self._hfont_small  = _make_font(height=-11, weight=400, face="Segoe UI")
        self._hfont_normal = _make_font(height=-12, weight=400, face="Segoe UI")
        self._hfont_bold   = _make_font(height=-13, weight=700, face="Segoe UI")
        self._setup_buttons()
        threading.Thread(target=self._poll_status,   daemon=True).start()
        threading.Thread(target=self._poll_level,    daemon=True).start()
        threading.Thread(target=self._anim_loop,     daemon=True).start()
        threading.Thread(target=self._session_timer, daemon=True).start()
        threading.Thread(target=self._alpha_animator, daemon=True).start()
        win32gui.PumpMessages()

    # ------------------------------------------------------------------
    # Button setup
    # ------------------------------------------------------------------

    def _setup_buttons(self):
        # Row 1: y=190..222, three buttons 86px wide, 6px gaps, starting x=10
        row1_y1, row1_y2 = 190, 222
        # Row 2: y=228..258
        row2_y1, row2_y2 = 228, 258
        bw = 86
        gap = 6
        x0 = 10

        self._buttons = [
            # Row 1
            Button("Repeat Last", (x0,              row1_y1, x0 + bw,        row1_y2), "/repeat"),
            Button("Slower",      (x0 + bw + gap,   row1_y1, x0 + 2*bw+gap,  row1_y2), "/set_pace?pace=slow"),
            Button("Faster",      (x0 + 2*bw+2*gap, row1_y1, x0 + 3*bw+2*gap,row1_y2), "/set_pace?pace=fast"),
            # Row 2
            Button("Normal",      (x0,              row2_y1, x0 + bw,        row2_y2), "/set_pace?pace=normal"),
            Button("Mute Mic",    (x0 + bw + gap,   row2_y1, x0 + 2*bw+gap,  row2_y2), ""),  # special: toggle
            Button("Stop Audio",  (x0 + 2*bw+2*gap, row2_y1, x0 + 3*bw+2*gap,row2_y2), "/stop"),
        ]

    # ------------------------------------------------------------------
    # Window creation
    # ------------------------------------------------------------------

    def _create_window(self):
        wc = win32gui.WNDCLASS()
        wc.lpszClassName = self.CLASS
        wc.lpfnWndProc   = self._wnd_proc
        wc.hbrBackground = gdi32.GetStockObject(NULL_BRUSH)
        wc.hCursor       = win32api.LoadCursor(0, win32con.IDC_ARROW)
        wc.style         = CS_HREDRAW | CS_VREDRAW
        try:
            win32gui.RegisterClass(wc)
        except Exception:
            pass

        sw = win32api.GetSystemMetrics(0)  # screen width
        sh = win32api.GetSystemMetrics(1)  # screen height
        x  = sw - W - 14
        y  = sh - H - 60   # sit above taskbar

        ex_style = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW

        self.hwnd = win32gui.CreateWindowEx(
            ex_style,
            self.CLASS,
            "Voice Control",
            WS_POPUP | WS_VISIBLE,
            x, y, W, H,
            0, 0, 0, None,
        )

        # Black pixels transparent; start at ALPHA_IDLE
        win32gui.SetLayeredWindowAttributes(
            self.hwnd, TRANSPARENT, ALPHA_IDLE, LWA_COLORKEY | LWA_ALPHA
        )
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE)
        win32gui.UpdateWindow(self.hwnd)

    # ------------------------------------------------------------------
    # Window procedure
    # ------------------------------------------------------------------

    def _wnd_proc(self, hwnd, msg, wparam, lparam):

        if msg == win32con.WM_PAINT:
            self._paint(hwnd)
            return 0

        elif msg == WM_ANIM:
            win32gui.InvalidateRect(hwnd, None, False)
            return 0

        elif msg == win32con.WM_NCHITTEST:
            sx = ctypes.c_short(lparam & 0xFFFF).value
            sy = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            r  = win32gui.GetWindowRect(hwnd)
            cx = sx - r[0]
            cy = sy - r[1]
            # Close button region — handle clicks in client
            if _pt_in_rect(cx, cy, (270, 7, 292, 29)):
                return win32con.HTCLIENT
            # Title bar — let OS handle drag
            if cy < 36:
                return win32con.HTCAPTION
            return win32con.HTCLIENT

        elif msg == win32con.WM_LBUTTONDOWN:
            cx = ctypes.c_short(lparam & 0xFFFF).value
            cy = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            self._handle_click(hwnd, cx, cy)
            return 0

        elif msg == win32con.WM_MOUSEMOVE:
            cx = ctypes.c_short(lparam & 0xFFFF).value
            cy = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            self._handle_mouse_move(hwnd, cx, cy)
            return 0

        elif msg == win32con.WM_MOUSELEAVE:
            self._mouse_tracking = False
            self._alpha_target   = ALPHA_IDLE
            self._close_hovered  = False
            for btn in self._buttons:
                btn.hovered = False
            win32gui.InvalidateRect(hwnd, None, False)
            return 0

        elif msg == win32con.WM_RBUTTONUP:
            self._context_menu(hwnd)
            return 0

        elif msg == win32con.WM_DESTROY:
            self._delete_fonts()
            win32gui.PostQuitMessage(0)
            return 0

        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    # ------------------------------------------------------------------
    # Mouse helpers
    # ------------------------------------------------------------------

    def _handle_mouse_move(self, hwnd, cx, cy):
        """Update hover states and target alpha; start tracking for WM_MOUSELEAVE."""
        self._alpha_target = ALPHA_HOVER

        # Start mouse leave tracking if not already
        if not self._mouse_tracking:
            tme = TRACKMOUSEEVENT()
            tme.cbSize    = ctypes.sizeof(TRACKMOUSEEVENT)
            tme.dwFlags   = TME_LEAVE
            tme.hwndTrack = hwnd
            tme.dwHoverTime = 0
            user32.TrackMouseEvent(ctypes.byref(tme))
            self._mouse_tracking = True

        # Update close-button hover
        close_rect  = (270, 7, 292, 29)
        new_close_h = _pt_in_rect(cx, cy, close_rect)
        changed     = (new_close_h != self._close_hovered)
        self._close_hovered = new_close_h

        # Update action-button hovers
        for btn in self._buttons:
            was = btn.hovered
            btn.hovered = _pt_in_rect(cx, cy, btn.rect)
            if btn.hovered != was:
                changed = True

        # MIC / STOP button hovers
        mic_rect  = (198, 43, 240, 73)
        stop_rect = (244, 43, 286, 73)
        # Store hover state for inline buttons via attributes
        new_mic_h  = _pt_in_rect(cx, cy, mic_rect)
        new_stop_h = _pt_in_rect(cx, cy, stop_rect)
        if getattr(self, "_mic_btn_hovered", False) != new_mic_h:
            changed = True
        if getattr(self, "_stop_btn_hovered", False) != new_stop_h:
            changed = True
        self._mic_btn_hovered  = new_mic_h
        self._stop_btn_hovered = new_stop_h

        if changed:
            win32gui.InvalidateRect(hwnd, None, False)

    def _handle_click(self, hwnd, cx, cy):
        """Handle left mouse-button down."""
        # Close button
        if _pt_in_rect(cx, cy, (270, 7, 292, 29)):
            win32gui.PostMessage(hwnd, win32con.WM_DESTROY, 0, 0)
            return

        # MIC button (toggle)
        if _pt_in_rect(cx, cy, (198, 43, 240, 73)):
            self._mic_on = not self._mic_on
            path = f"/set_listening?enabled={'true' if self._mic_on else 'false'}"
            self._fire(path)
            win32gui.InvalidateRect(hwnd, None, False)
            return

        # STOP button
        if _pt_in_rect(cx, cy, (244, 43, 286, 73)):
            self._fire("/stop")
            return

        # Action buttons
        for i, btn in enumerate(self._buttons):
            if _pt_in_rect(cx, cy, btn.rect):
                # Button index 4 = "Mute Mic" (toggle)
                if i == 4:
                    self._mic_on = not self._mic_on
                    path = f"/set_listening?enabled={'true' if self._mic_on else 'false'}"
                    # Update label
                    btn.label = "Unmute Mic" if not self._mic_on else "Mute Mic"
                    self._fire(path)
                else:
                    self._fire(btn.action)
                # Flash feedback
                self._flash_button(btn)
                return

    def _flash_button(self, btn):
        """Flash a button briefly for visual feedback."""
        def _do_flash():
            btn.flashing = True
            if self.hwnd:
                win32gui.InvalidateRect(self.hwnd, None, False)
            time.sleep(0.12)
            btn.flashing = False
            if self.hwnd:
                win32gui.InvalidateRect(self.hwnd, None, False)
        threading.Thread(target=_do_flash, daemon=True).start()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def _paint(self, hwnd):
        hdc, ps = win32gui.BeginPaint(hwnd)
        try:
            t = time.monotonic() - self._t0

            # --- Fill entire window with transparent black first ---
            rc = win32gui.GetClientRect(hwnd)
            black_brush = gdi32.CreateSolidBrush(0)
            user32.FillRect(hdc, ctypes.byref(_to_rect(rc)), black_brush)
            gdi32.DeleteObject(black_brush)

            # --- Draw pill/panel background (rounded rect) ---
            bg_brush = gdi32.CreateSolidBrush(rgb(*BG))
            bd_pen   = gdi32.CreatePen(PS_SOLID, 1, rgb(*BORDER))
            old_pen   = gdi32.SelectObject(hdc, bd_pen)
            old_brush = gdi32.SelectObject(hdc, bg_brush)
            gdi32.RoundRect(hdc, 1, 1, W - 1, H - 1, CORNER, CORNER)
            gdi32.SelectObject(hdc, old_pen)
            gdi32.SelectObject(hdc, old_brush)
            gdi32.DeleteObject(bg_brush)
            gdi32.DeleteObject(bd_pen)

            gdi32.SetBkMode(hdc, 1)   # TRANSPARENT text background

            # === Section 1: Title bar ===
            self._paint_title_bar(hdc)

            # === Section 2: Status + buttons ===
            self._paint_status(hdc, t)

            # === Section 3: Level meter ===
            self._paint_level_meter(hdc)

            # === Section 4: Session info ===
            self._paint_session(hdc)

            # === Section 5: Quick actions ===
            self._paint_quick_actions(hdc)

        finally:
            win32gui.EndPaint(hwnd, ps)

    # --- Section painters ---

    def _paint_title_bar(self, hdc):
        # Fill title bar area with BG (already done by rounded rect, just need text)
        # Title text
        title = "Voice Control"
        if self._hfont_normal:
            old_font = gdi32.SelectObject(hdc, self._hfont_normal)
        win32gui.SetTextColor(hdc, rgb(180, 180, 180))
        gdi32.SetBkMode(hdc, 1)
        tr = (14, 0, 260, 36)
        win32gui.DrawText(
            hdc, title, len(title), tr,
            win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
        )
        if self._hfont_normal:
            gdi32.SelectObject(hdc, old_font)

        # Close button (270,7,292,29)
        close_rect = (270, 7, 292, 29)
        if self._close_hovered:
            # Filled red rounded rect
            cr_brush = gdi32.CreateSolidBrush(rgb(200, 60, 60))
            np_ = gdi32.GetStockObject(NULL_PEN)
            old_pen   = gdi32.SelectObject(hdc, np_)
            old_brush = gdi32.SelectObject(hdc, cr_brush)
            gdi32.RoundRect(hdc, close_rect[0], close_rect[1], close_rect[2], close_rect[3], 5, 5)
            gdi32.SelectObject(hdc, old_pen)
            gdi32.SelectObject(hdc, old_brush)
            gdi32.DeleteObject(cr_brush)
            win32gui.SetTextColor(hdc, rgb(255, 255, 255))
        else:
            win32gui.SetTextColor(hdc, rgb(150, 150, 150))

        x_label = "×"  # Unicode × character
        win32gui.DrawText(
            hdc, x_label, len(x_label), close_rect,
            win32con.DT_CENTER | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
        )

        # Horizontal separator at y=36
        sep_pen = gdi32.CreatePen(PS_SOLID, 1, rgb(*SEP))
        old_pen = gdi32.SelectObject(hdc, sep_pen)
        gdi32.MoveToEx(hdc, 0, 36, None)
        gdi32.LineTo(hdc, W, 36)
        gdi32.SelectObject(hdc, old_pen)
        gdi32.DeleteObject(sep_pen)

    def _paint_status(self, hdc, t):
        # Pulsing status dot: cx=24, cy=59, r=8
        cx, cy, r = 24, 59, 8
        dr, dg, db = DOT_COLORS.get(self._state, DOT_COLORS["offline"])
        if self._state in ("ready", "speaking"):
            p      = (math.sin(t * 3.5 * math.pi) + 1) / 2   # ~1.75 Hz
            factor = 0.65 + 0.35 * p
            dr = int(dr * factor)
            dg = int(dg * factor)
            db = int(db * factor)

        dot_brush = gdi32.CreateSolidBrush(rgb(dr, dg, db))
        np_       = gdi32.GetStockObject(NULL_PEN)
        old_pen   = gdi32.SelectObject(hdc, np_)
        old_brush = gdi32.SelectObject(hdc, dot_brush)
        gdi32.Ellipse(hdc, cx - r, cy - r, cx + r + 1, cy + r + 1)
        gdi32.SelectObject(hdc, old_pen)
        gdi32.SelectObject(hdc, old_brush)
        gdi32.DeleteObject(dot_brush)

        # Status label (10pt bold)
        label = STATUS_LABELS.get(self._state, self._state.title())
        if self._hfont_bold:
            old_font = gdi32.SelectObject(hdc, self._hfont_bold)
        win32gui.SetTextColor(hdc, rgb(220, 220, 220))
        gdi32.SetBkMode(hdc, 1)
        tr = (40, 36, 190, 82)
        win32gui.DrawText(
            hdc, label, len(label), tr,
            win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
        )
        if self._hfont_bold:
            gdi32.SelectObject(hdc, old_font)

        # MIC button: rect(198,43,240,73)
        mic_rect = (198, 43, 240, 73)
        mic_text = "MIC ON" if self._mic_on else "MIC OFF"
        mic_border_color = rgb(45, 180, 65) if self._mic_on else rgb(180, 65, 45)
        self._draw_bordered_button(hdc, mic_rect, mic_text, mic_border_color,
                                   hovered=getattr(self, "_mic_btn_hovered", False))

        # STOP button: rect(244,43,286,73)
        stop_rect = (244, 43, 286, 73)
        self._draw_bordered_button(hdc, stop_rect, "STOP", rgb(70, 70, 70),
                                   hovered=getattr(self, "_stop_btn_hovered", False))

        # Separator at y=82
        sep_pen = gdi32.CreatePen(PS_SOLID, 1, rgb(*SEP))
        old_pen = gdi32.SelectObject(hdc, sep_pen)
        gdi32.MoveToEx(hdc, 0, 82, None)
        gdi32.LineTo(hdc, W, 82)
        gdi32.SelectObject(hdc, old_pen)
        gdi32.DeleteObject(sep_pen)

    def _draw_bordered_button(self, hdc, rect, text, border_color, hovered=False):
        """Draw a button with rounded rect border."""
        x1, y1, x2, y2 = rect
        bg_c = rgb(50, 50, 50) if hovered else rgb(28, 28, 28)
        bg_brush = gdi32.CreateSolidBrush(bg_c)
        bd_pen   = gdi32.CreatePen(PS_SOLID, 1, border_color)
        old_pen   = gdi32.SelectObject(hdc, bd_pen)
        old_brush = gdi32.SelectObject(hdc, bg_brush)
        gdi32.RoundRect(hdc, x1, y1, x2, y2, 5, 5)
        gdi32.SelectObject(hdc, old_pen)
        gdi32.SelectObject(hdc, old_brush)
        gdi32.DeleteObject(bg_brush)
        gdi32.DeleteObject(bd_pen)

        if self._hfont_small:
            old_font = gdi32.SelectObject(hdc, self._hfont_small)
        win32gui.SetTextColor(hdc, rgb(200, 200, 200))
        gdi32.SetBkMode(hdc, 1)
        win32gui.DrawText(
            hdc, text, len(text), rect,
            win32con.DT_CENTER | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
        )
        if self._hfont_small:
            gdi32.SelectObject(hdc, old_font)

    def _paint_level_meter(self, hdc):
        # Background rect RGB(28,28,28) x=10..290, y=86..100
        bg_brush = gdi32.CreateSolidBrush(rgb(28, 28, 28))
        np_       = gdi32.GetStockObject(NULL_PEN)
        old_pen   = gdi32.SelectObject(hdc, np_)
        old_brush = gdi32.SelectObject(hdc, bg_brush)
        gdi32.Rectangle(hdc, 10, 86, 290, 100)
        gdi32.SelectObject(hdc, old_pen)
        gdi32.SelectObject(hdc, old_brush)
        gdi32.DeleteObject(bg_brush)

        # Level fill
        level = max(0.0, min(1.0, self._level))
        fill_w = int(280 * level)
        if fill_w > 0:
            if level < 0.5:
                fill_c = rgb(45, 200, 75)
            elif level < 0.75:
                fill_c = rgb(220, 190, 45)
            else:
                fill_c = rgb(210, 60, 45)

            fill_brush = gdi32.CreateSolidBrush(fill_c)
            np_        = gdi32.GetStockObject(NULL_PEN)
            old_pen    = gdi32.SelectObject(hdc, np_)
            old_brush  = gdi32.SelectObject(hdc, fill_brush)
            gdi32.Rectangle(hdc, 10, 86, 10 + fill_w, 100)
            gdi32.SelectObject(hdc, old_pen)
            gdi32.SelectObject(hdc, old_brush)
            gdi32.DeleteObject(fill_brush)

        # Separator at y=106
        sep_pen = gdi32.CreatePen(PS_SOLID, 1, rgb(*SEP))
        old_pen = gdi32.SelectObject(hdc, sep_pen)
        gdi32.MoveToEx(hdc, 0, 106, None)
        gdi32.LineTo(hdc, W, 106)
        gdi32.SelectObject(hdc, old_pen)
        gdi32.DeleteObject(sep_pen)

    def _paint_session(self, hdc):
        # "SESSION" label 8pt at x=14, y=116
        if self._hfont_small:
            old_font = gdi32.SelectObject(hdc, self._hfont_small)
        win32gui.SetTextColor(hdc, rgb(110, 110, 110))
        gdi32.SetBkMode(hdc, 1)
        lbl = "SESSION"
        win32gui.DrawText(hdc, lbl, len(lbl), (14, 116, 200, 132),
                          win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_SINGLELINE)

        # Topic text 9pt at x=14, y=132
        if self._hfont_normal:
            gdi32.SelectObject(hdc, self._hfont_normal)
        win32gui.SetTextColor(hdc, rgb(200, 200, 200))
        topic = self._topic if self._topic else "—"
        if len(topic) > 34:
            topic = topic[:34] + "…"
        win32gui.DrawText(hdc, topic, len(topic), (14, 132, 240, 150),
                          win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_SINGLELINE)

        # Timer MM:SS at x=248, y=132, right-aligned
        secs  = self._session_secs
        timer = f"{secs // 60:02d}:{secs % 60:02d}"
        win32gui.SetTextColor(hdc, rgb(150, 150, 150))
        win32gui.DrawText(hdc, timer, len(timer), (200, 132, 290, 150),
                          win32con.DT_RIGHT | win32con.DT_TOP | win32con.DT_SINGLELINE)

        # Progress bar: rect(14,148,194,158)
        prog_bg = gdi32.CreateSolidBrush(rgb(38, 38, 38))
        np_     = gdi32.GetStockObject(NULL_PEN)
        old_pen   = gdi32.SelectObject(hdc, np_)
        old_brush = gdi32.SelectObject(hdc, prog_bg)
        gdi32.Rectangle(hdc, 14, 148, 194, 158)
        gdi32.SelectObject(hdc, old_pen)
        gdi32.SelectObject(hdc, old_brush)
        gdi32.DeleteObject(prog_bg)

        progress = max(0, min(100, self._progress))
        fill_w   = int(180 * (progress / 100))
        if fill_w > 0:
            prog_fill = gdi32.CreateSolidBrush(rgb(65, 130, 230))
            np_       = gdi32.GetStockObject(NULL_PEN)
            old_pen   = gdi32.SelectObject(hdc, np_)
            old_brush = gdi32.SelectObject(hdc, prog_fill)
            gdi32.Rectangle(hdc, 14, 148, 14 + fill_w, 158)
            gdi32.SelectObject(hdc, old_pen)
            gdi32.SelectObject(hdc, old_brush)
            gdi32.DeleteObject(prog_fill)

        # Progress % text 8pt at x=200, y=148
        if self._hfont_small:
            gdi32.SelectObject(hdc, self._hfont_small)
        win32gui.SetTextColor(hdc, rgb(140, 140, 140))
        pct_text = f"{progress}%"
        win32gui.DrawText(hdc, pct_text, len(pct_text), (200, 148, 290, 165),
                          win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_SINGLELINE)

        if self._hfont_small:
            gdi32.SelectObject(hdc, old_font)

        # Separator at y=168
        sep_pen = gdi32.CreatePen(PS_SOLID, 1, rgb(*SEP))
        old_pen = gdi32.SelectObject(hdc, sep_pen)
        gdi32.MoveToEx(hdc, 0, 168, None)
        gdi32.LineTo(hdc, W, 168)
        gdi32.SelectObject(hdc, old_pen)
        gdi32.DeleteObject(sep_pen)

    def _paint_quick_actions(self, hdc):
        # "QUICK ACTIONS" label 8pt at x=14, y=178
        if self._hfont_small:
            old_font = gdi32.SelectObject(hdc, self._hfont_small)
        win32gui.SetTextColor(hdc, rgb(110, 110, 110))
        gdi32.SetBkMode(hdc, 1)
        lbl = "QUICK ACTIONS"
        win32gui.DrawText(hdc, lbl, len(lbl), (14, 178, 290, 192),
                          win32con.DT_LEFT | win32con.DT_TOP | win32con.DT_SINGLELINE)

        # Draw each action button
        for btn in self._buttons:
            x1, y1, x2, y2 = btn.rect
            if btn.flashing:
                bg_c = rgb(22, 22, 22)
            elif btn.hovered:
                bg_c = rgb(55, 55, 55)
            else:
                bg_c = rgb(35, 35, 35)

            bg_brush = gdi32.CreateSolidBrush(bg_c)
            bd_pen   = gdi32.CreatePen(PS_SOLID, 1, rgb(72, 72, 72))
            old_pen   = gdi32.SelectObject(hdc, bd_pen)
            old_brush = gdi32.SelectObject(hdc, bg_brush)
            gdi32.RoundRect(hdc, x1, y1, x2, y2, 5, 5)
            gdi32.SelectObject(hdc, old_pen)
            gdi32.SelectObject(hdc, old_brush)
            gdi32.DeleteObject(bg_brush)
            gdi32.DeleteObject(bd_pen)

            win32gui.SetTextColor(hdc, rgb(200, 200, 200))
            win32gui.DrawText(
                hdc, btn.label, len(btn.label), btn.rect,
                win32con.DT_CENTER | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
            )

        if self._hfont_small:
            gdi32.SelectObject(hdc, old_font)

    # ------------------------------------------------------------------
    # HTTP fire-and-forget
    # ------------------------------------------------------------------

    def _fire(self, url_path: str):
        """Send a GET request in a daemon thread; ignore any errors."""
        def _do():
            try:
                urllib.request.urlopen(
                    "http://localhost:9103" + url_path, timeout=3
                )
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()

    # ------------------------------------------------------------------
    # Background threads
    # ------------------------------------------------------------------

    def _poll_status(self):
        """Poll /health every 1s and update state fields."""
        while True:
            try:
                with urllib.request.urlopen(f"{VOICE_URL}/health", timeout=2) as resp:
                    data = json.loads(resp.read())
                ready    = data.get("ready", False)
                speaking = data.get("speaking", False)
                mic_en   = data.get("mic_enabled", True)
                vad      = data.get("vad_active", False)
                self._mic_on  = mic_en
                self._topic   = data.get("session_topic", "") or ""
                prog          = data.get("session_progress", 0)
                self._progress = int(prog) if prog else 0
                self._last_spoken_preview = data.get("last_spoken_preview", "") or ""
                if not ready:
                    new_state = "loading"
                elif speaking:
                    new_state = "speaking"
                elif not mic_en:
                    new_state = "mic_off"
                else:
                    new_state = "ready"
            except Exception:
                new_state = "offline"

            if new_state != self._state:
                self._state = new_state
                if self.hwnd:
                    win32gui.InvalidateRect(self.hwnd, None, False)

            time.sleep(1.0)

    def _poll_level(self):
        """Poll /mic_level every 150ms."""
        while True:
            try:
                with urllib.request.urlopen(f"{VOICE_URL}/mic_level", timeout=1) as resp:
                    data = json.loads(resp.read())
                self._level = float(data.get("level", 0.0))
            except Exception:
                self._level = 0.0
            if self.hwnd:
                win32gui.InvalidateRect(self.hwnd, None, False)
            time.sleep(0.15)

    def _anim_loop(self):
        """Post WM_ANIM at ~20fps to drive pulsing dot animation."""
        while True:
            if self.hwnd and self._state in ("ready", "speaking"):
                win32api.PostMessage(self.hwnd, WM_ANIM, 0, 0)
            time.sleep(0.05)

    def _session_timer(self):
        """Increment session timer every second."""
        while True:
            time.sleep(1.0)
            self._session_secs += 1
            if self.hwnd:
                win32gui.InvalidateRect(self.hwnd, None, False)

    def _alpha_animator(self):
        """Smoothly move _alpha toward _alpha_target by at most ALPHA_STEP per 30ms tick."""
        while True:
            time.sleep(0.03)
            if not self.hwnd:
                continue
            target = self._alpha_target
            current = self._alpha
            if current == target:
                continue
            diff  = target - current
            delta = min(abs(diff), ALPHA_STEP) * (1 if diff > 0 else -1)
            self._alpha = current + delta
            try:
                win32gui.SetLayeredWindowAttributes(
                    self.hwnd, TRANSPARENT, self._alpha, LWA_COLORKEY | LWA_ALPHA
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _context_menu(self, hwnd):
        IDM_EXIT        = 1
        IDM_RESET_TIMER = 2
        hm = win32gui.CreatePopupMenu()
        win32gui.AppendMenu(hm, win32con.MF_STRING, IDM_EXIT, "Exit")
        win32gui.AppendMenu(hm, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(hm, win32con.MF_STRING, IDM_RESET_TIMER, "Reset Timer")
        x, y = win32api.GetCursorPos()
        win32gui.SetForegroundWindow(hwnd)
        cmd = win32gui.TrackPopupMenu(
            hm, win32con.TPM_LEFTALIGN | win32con.TPM_RETURNCMD,
            x, y, 0, hwnd, None,
        )
        win32gui.DestroyMenu(hm)
        if cmd == IDM_EXIT:
            win32gui.PostMessage(hwnd, win32con.WM_DESTROY, 0, 0)
        elif cmd == IDM_RESET_TIMER:
            self._session_secs = 0
            if self.hwnd:
                win32gui.InvalidateRect(self.hwnd, None, False)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _delete_fonts(self):
        for attr in ("_hfont_small", "_hfont_normal", "_hfont_bold"):
            hf = getattr(self, attr, None)
            if hf:
                gdi32.DeleteObject(hf)
                setattr(self, attr, None)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    panel = VoiceControlPanel()
    panel.run()
