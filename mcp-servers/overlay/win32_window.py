import ctypes
import ctypes.wintypes
import threading
import time
import win32con
import win32gui
import win32api

# Windows extended style constants
WS_EX_LAYERED   = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST   = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

# Window style constants
WS_POPUP    = 0x80000000
WS_VISIBLE  = 0x10000000

# Layered window attribute flags
LWA_COLORKEY = 0x00000001
LWA_ALPHA    = 0x00000002

# Class style constants
CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001


class OverlayWindow:
    def __init__(self):
        self.hwnd = None
        self.hdc = None
        self.width = 0
        self.height = 0
        self._thread = None
        self._ready = threading.Event()
        self._running = False
        self.on_paint: callable = None  # Set by Renderer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create(self):
        """Create the overlay window in a background thread. Block until ready."""
        self._running = True
        self._thread = threading.Thread(target=self._window_thread, daemon=True)
        self._thread.start()
        self._ready.wait()  # Block until the window is fully created and shown

    def destroy(self):
        """Destroy the overlay window."""
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, win32con.WM_DESTROY, 0, 0)
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)

    def invalidate(self):
        """Force a repaint of the entire window."""
        if self.hwnd:
            win32gui.InvalidateRect(self.hwnd, None, True)

    def get_screen_size(self) -> tuple:
        """Return (width, height) of the primary monitor."""
        return (
            win32api.GetSystemMetrics(0),  # SM_CXSCREEN
            win32api.GetSystemMetrics(1),  # SM_CYSCREEN
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _register_class(self) -> str:
        """Register the window class. Returns the class name."""
        class_name = "TutorOverlay"

        wc = win32gui.WNDCLASS()
        wc.lpszClassName = class_name
        wc.lpfnWndProc   = self._wnd_proc
        wc.hbrBackground = None          # No background — we paint black which becomes transparent
        wc.style         = CS_HREDRAW | CS_VREDRAW

        try:
            win32gui.RegisterClass(wc)
        except Exception:
            # Class may already be registered from a previous run in the same process
            pass

        return class_name

    def _window_thread(self):
        """Window message loop thread — runs for the lifetime of the overlay."""
        class_name = self._register_class()

        self.width, self.height = self.get_screen_size()

        # Combined extended style: layered + click-through + always-on-top +
        # no activation steal + hidden from taskbar/alt-tab
        ex_style = (
            WS_EX_LAYERED
            | WS_EX_TRANSPARENT
            | WS_EX_TOPMOST
            | WS_EX_NOACTIVATE
            | WS_EX_TOOLWINDOW
        )

        hwnd = win32gui.CreateWindowEx(
            ex_style,
            class_name,
            "TutorOverlay",          # Window title (not visible)
            WS_POPUP | WS_VISIBLE,
            0, 0,                    # Position: top-left of screen
            self.width, self.height, # Full-screen size
            0,                       # No parent
            0,                       # No menu
            0,                       # hInstance (0 = current)
            None,
        )
        self.hwnd = hwnd

        # Make black (0x000000) the transparent color key.
        # Anything painted black becomes invisible / click-through.
        win32gui.SetLayeredWindowAttributes(hwnd, 0x000000, 255, LWA_COLORKEY)

        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.UpdateWindow(hwnd)

        # Signal that the window is ready before entering the message loop
        self._ready.set()

        # Blocking message pump — returns when WM_QUIT is posted
        win32gui.PumpMessages()

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        """Window procedure: handle WM_PAINT; delegate everything else."""
        if msg == win32con.WM_PAINT:
            if self.on_paint is not None:
                try:
                    self.on_paint(hwnd)
                except Exception:
                    pass  # Never let a paint error crash the message loop
            return 0

        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0

        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)
