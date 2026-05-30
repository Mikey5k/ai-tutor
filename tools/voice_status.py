#!/usr/bin/env python3
"""
Voice status widget — Win32 native floating pill.

Shows the state of the voice server with a pulsing colored dot.
Click the mic icon on the right to toggle listening on/off.
Drag the pill to reposition. Right-click to exit.

States:
  Red    — voice server offline
  Orange — server up, models loading
  Green  — ready and listening (pulsing)
  Blue   — AI is speaking (pulsing)
  Gray   — mic disabled

Usage:
  python E:/ai-tutor/tools/voice_status.py
"""
import ctypes
import ctypes.wintypes
import math
import sys
import threading
import time
import urllib.request
import json
import logging

import win32api
import win32con
import win32gui

logging.basicConfig(level=logging.WARNING)

# ---------------------------------------------------------------------------
# Win32 constants not in win32con
# ---------------------------------------------------------------------------
WS_EX_LAYERED    = 0x00080000
WS_EX_TOPMOST    = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_POPUP         = 0x80000000
WS_VISIBLE       = 0x10000000
LWA_COLORKEY     = 0x00000001
CS_HREDRAW       = 0x0002
CS_VREDRAW       = 0x0001
PS_SOLID         = 0
NULL_BRUSH        = 5
NULL_PEN          = 8
WM_USER           = 0x0400
WM_ANIM           = WM_USER + 1   # custom message to trigger repaint

gdi32  = ctypes.windll.gdi32
user32 = ctypes.windll.user32

# ---------------------------------------------------------------------------
# Pill geometry
# ---------------------------------------------------------------------------
W          = 200    # window width
H          = 50     # window height
CORNER     = 18     # RoundRect ellipse size
DOT_CX     = 24     # dot center x
DOT_CY     = H // 2
DOT_R      = 9      # dot radius
TEXT_X     = DOT_CX + DOT_R + 10
MIC_X      = W - 38  # mic button left edge
MIC_W      = 32     # mic button width
INSET      = 2      # pill inset from window edge

# Colors  (R, G, B) — avoid pure black (0,0,0) which is the transparent key
BG         = (18, 18, 18)
BORDER     = (65, 65, 65)
TEXT_C     = (215, 215, 215)
MIC_ON     = (170, 170, 170)
MIC_OFF    = (75,  75,  75)

DOT_COLORS = {
    "offline":  (210, 45,  45),
    "loading":  (255, 160, 0),
    "ready":    (45,  210, 75),
    "speaking": (45,  130, 225),
    "mic_off":  (90,  90,  90),
}
LABELS = {
    "offline":  "Offline",
    "loading":  "Loading...",
    "ready":    "Listening",
    "speaking": "Speaking",
    "mic_off":  "Mic off",
}

TRANSPARENT = 0x000000  # black = invisible via LWA_COLORKEY

VOICE_URL = "http://localhost:9103"

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
    buf = ctypes.create_unicode_buffer(face)
    return gdi32.CreateFontW(
        height, 0, 0, 0, weight,
        0, 0, 0,   # italic, underline, strikeout
        0,         # ANSI_CHARSET
        0, 0,      # OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS
        5,         # CLEARTYPE_QUALITY
        0,         # DEFAULT_PITCH | FF_DONTCARE
        buf,
    )


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

class VoiceStatusWidget:
    CLASS = "VoiceStatusPill"

    def __init__(self):
        self.hwnd       = None
        self._state     = "offline"
        self._mic_on    = True
        self._t0        = time.monotonic()
        self._drag_pt   = None   # (client_x, client_y) at drag start
        self._hfont     = None
        self._poll_thread = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self):
        self._create_window()
        self._hfont = _make_font()
        # Poll thread reads voice server status
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        # Animation thread posts WM_ANIM to drive pulse repaints
        threading.Thread(target=self._anim_loop, daemon=True).start()
        # Block in message loop
        win32gui.PumpMessages()

    # ------------------------------------------------------------------
    # Window creation
    # ------------------------------------------------------------------

    def _create_window(self):
        wc = win32gui.WNDCLASS()
        wc.lpszClassName = self.CLASS
        wc.lpfnWndProc   = self._wnd_proc
        wc.hbrBackground = None
        wc.hCursor       = win32api.LoadCursor(0, win32con.IDC_ARROW)
        wc.style         = CS_HREDRAW | CS_VREDRAW
        try:
            win32gui.RegisterClass(wc)
        except Exception:
            pass

        sw = win32api.GetSystemMetrics(0)  # screen width
        sh = win32api.GetSystemMetrics(1)  # screen height
        x  = sw - W - 14
        y  = sh - H - 58   # sit above taskbar

        ex = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW

        self.hwnd = win32gui.CreateWindowEx(
            ex, self.CLASS, "Voice Status",
            WS_POPUP | WS_VISIBLE,
            x, y, W, H,
            0, 0, 0, None,
        )

        # Black pixels → transparent
        win32gui.SetLayeredWindowAttributes(self.hwnd, TRANSPARENT, 0, LWA_COLORKEY)
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE)
        win32gui.UpdateWindow(self.hwnd)

    # ------------------------------------------------------------------
    # Message procedure
    # ------------------------------------------------------------------

    def _wnd_proc(self, hwnd, msg, wparam, lparam):

        if msg == win32con.WM_PAINT:
            self._paint(hwnd)
            return 0

        elif msg == WM_ANIM:
            # Repaint for pulsing animation
            win32gui.InvalidateRect(hwnd, None, False)
            return 0

        elif msg == win32con.WM_NCHITTEST:
            # Screen coords → client coords
            sx = ctypes.c_short(lparam & 0xFFFF).value
            sy = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            r  = win32gui.GetWindowRect(hwnd)
            cx = sx - r[0]
            cy = sy - r[1]
            if MIC_X <= cx <= MIC_X + MIC_W:
                return win32con.HTCLIENT   # handle clicks ourselves
            return win32con.HTCAPTION      # OS handles dragging

        elif msg == win32con.WM_LBUTTONUP:
            # Mic button clicked
            sx = ctypes.c_short(lparam & 0xFFFF).value
            if sx >= MIC_X:
                self._toggle_mic()
            return 0

        elif msg == win32con.WM_RBUTTONUP:
            self._context_menu(hwnd)
            return 0

        elif msg == win32con.WM_DESTROY:
            if self._hfont:
                gdi32.DeleteObject(self._hfont)
            win32gui.PostQuitMessage(0)
            return 0

        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def _paint(self, hwnd):
        hdc, ps = win32gui.BeginPaint(hwnd)
        try:
            t   = time.monotonic() - self._t0
            state = self._state

            # 1. Fill entire window with transparent black
            rc = win32gui.GetClientRect(hwnd)
            bb = gdi32.CreateSolidBrush(0)
            user32.FillRect(hdc, ctypes.byref(_to_rect(rc)), bb)
            gdi32.DeleteObject(bb)

            # 2. Draw pill background (dark, just above black so it's visible)
            bg_brush  = gdi32.CreateSolidBrush(rgb(*BG))
            bd_pen    = gdi32.CreatePen(PS_SOLID, 1, rgb(*BORDER))
            old_pen   = gdi32.SelectObject(hdc, bd_pen)
            old_brush = gdi32.SelectObject(hdc, bg_brush)
            gdi32.RoundRect(hdc, INSET, INSET, W - INSET, H - INSET, CORNER, CORNER)
            gdi32.SelectObject(hdc, old_pen)
            gdi32.SelectObject(hdc, old_brush)
            gdi32.DeleteObject(bg_brush)
            gdi32.DeleteObject(bd_pen)

            # 3. Status dot with pulse for ready/speaking
            dr, dg, db = DOT_COLORS.get(state, DOT_COLORS["offline"])
            if state in ("ready", "speaking"):
                p  = (math.sin(t * 3.5) + 1) / 2   # 0‥1 at ~1.75 Hz
                factor = 0.65 + 0.35 * p
                dr = int(dr * factor)
                dg = int(dg * factor)
                db = int(db * factor)

            dot_brush = gdi32.CreateSolidBrush(rgb(dr, dg, db))
            np_       = gdi32.GetStockObject(NULL_PEN)
            old_pen   = gdi32.SelectObject(hdc, np_)
            old_brush = gdi32.SelectObject(hdc, dot_brush)
            gdi32.Ellipse(
                hdc,
                DOT_CX - DOT_R, DOT_CY - DOT_R,
                DOT_CX + DOT_R + 1, DOT_CY + DOT_R + 1,
            )
            gdi32.SelectObject(hdc, old_pen)
            gdi32.SelectObject(hdc, old_brush)
            gdi32.DeleteObject(dot_brush)

            # 4. Status label
            label = LABELS.get(state, state)
            if self._hfont:
                old_font = gdi32.SelectObject(hdc, self._hfont)
            win32gui.SetTextColor(hdc, rgb(*TEXT_C))
            gdi32.SetBkMode(hdc, 1)   # TRANSPARENT
            tr = (TEXT_X, 0, MIC_X - 2, H)
            win32gui.DrawText(
                hdc, label, len(label), tr,
                win32con.DT_LEFT | win32con.DT_VCENTER | win32con.DT_SINGLELINE,
            )
            if self._hfont:
                gdi32.SelectObject(hdc, old_font)

            # 5. Mic icon
            self._draw_mic(hdc)

        finally:
            win32gui.EndPaint(hwnd, ps)

    def _draw_mic(self, hdc):
        mc  = MIC_ON if self._mic_on else MIC_OFF
        cr  = rgb(*mc)
        cx  = MIC_X + MIC_W // 2
        cy  = H // 2

        mic_brush = gdi32.CreateSolidBrush(cr)
        mic_pen   = gdi32.CreatePen(PS_SOLID, 2, cr)
        nb        = gdi32.GetStockObject(NULL_BRUSH)

        # Mic capsule body
        old_pen   = gdi32.SelectObject(hdc, mic_pen)
        old_brush = gdi32.SelectObject(hdc, mic_brush)
        gdi32.RoundRect(hdc, cx - 5, cy - 11, cx + 6, cy + 2, 6, 6)
        gdi32.SelectObject(hdc, old_brush)
        gdi32.SelectObject(hdc, nb)

        # Mic stand arc
        gdi32.Arc(hdc, cx - 9, cy - 5, cx + 10, cy + 10,
                  cx + 10, cy + 2, cx - 9, cy + 2)
        # Stem
        gdi32.MoveToEx(hdc, cx, cy + 10, None)
        gdi32.LineTo(hdc,  cx, cy + 14)
        # Base
        gdi32.MoveToEx(hdc, cx - 6, cy + 14, None)
        gdi32.LineTo(hdc,  cx + 7, cy + 14)

        gdi32.SelectObject(hdc, old_pen)
        gdi32.DeleteObject(mic_pen)
        gdi32.DeleteObject(mic_brush)

        # Red diagonal strike-through when mic is off
        if not self._mic_on:
            sp = gdi32.CreatePen(PS_SOLID, 2, rgb(210, 45, 45))
            old_pen = gdi32.SelectObject(hdc, sp)
            gdi32.MoveToEx(hdc, cx - 9, cy - 13, None)
            gdi32.LineTo(hdc,  cx + 9, cy + 15)
            gdi32.SelectObject(hdc, old_pen)
            gdi32.DeleteObject(sp)

    # ------------------------------------------------------------------
    # Context menu (right-click)
    # ------------------------------------------------------------------

    def _context_menu(self, hwnd):
        IDM_EXIT   = 1
        IDM_RESTART = 2
        hm = win32gui.CreatePopupMenu()
        win32gui.AppendMenu(hm, win32con.MF_STRING, IDM_RESTART, "Restart voice server")
        win32gui.AppendMenu(hm, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(hm, win32con.MF_STRING, IDM_EXIT, "Exit")
        x, y = win32api.GetCursorPos()
        win32gui.SetForegroundWindow(hwnd)
        cmd = win32gui.TrackPopupMenu(
            hm, win32con.TPM_LEFTALIGN | win32con.TPM_RETURNCMD,
            x, y, 0, hwnd, None,
        )
        win32gui.DestroyMenu(hm)
        if cmd == IDM_EXIT:
            win32gui.PostMessage(hwnd, win32con.WM_DESTROY, 0, 0)
        elif cmd == IDM_RESTART:
            self._restart_server()

    # ------------------------------------------------------------------
    # Mic toggle
    # ------------------------------------------------------------------

    def _toggle_mic(self):
        self._mic_on = not self._mic_on
        try:
            url = f"{VOICE_URL}/set_listening?enabled={'true' if self._mic_on else 'false'}"
            urllib.request.urlopen(url, timeout=2)
        except Exception:
            pass
        win32gui.InvalidateRect(self.hwnd, None, False)

    # ------------------------------------------------------------------
    # Restart voice server
    # ------------------------------------------------------------------

    def _restart_server(self):
        import subprocess, sys
        subprocess.Popen(
            [sys.executable, "E:/ai-tutor/mcp-servers/voice/server.py"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    # ------------------------------------------------------------------
    # Background threads
    # ------------------------------------------------------------------

    def _poll_loop(self):
        """Poll /health every second and update _state."""
        while True:
            try:
                with urllib.request.urlopen(f"{VOICE_URL}/health", timeout=2) as r:
                    data = json.loads(r.read())
                ready   = data.get("ready", False)
                speaking = data.get("speaking", False)
                mic_en  = data.get("mic_enabled", True)
                self._mic_on = mic_en
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

            time.sleep(1)

    def _anim_loop(self):
        """Post repaint messages at ~20 fps for smooth pulsing."""
        while True:
            if self.hwnd and self._state in ("ready", "speaking"):
                win32api.PostMessage(self.hwnd, WM_ANIM, 0, 0)
            time.sleep(0.05)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    widget = VoiceStatusWidget()
    widget.run()
