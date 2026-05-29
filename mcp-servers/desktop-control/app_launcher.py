import subprocess
import time
import win32con
import win32gui
import win32process
import psutil
from typing import Optional


class AppLauncher:
    """Opens, closes, and focuses Windows applications."""

    # How long to wait for a new window to appear after launching (seconds)
    _OPEN_TIMEOUT = 10
    # Polling interval while waiting for a window
    _POLL_INTERVAL = 0.25

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open(self, app_name_or_path: str) -> dict:
        """Launch an application and wait for its main window to appear.

        Parameters
        ----------
        app_name_or_path:
            Either a friendly name (e.g. 'notepad') that will be passed to
            subprocess.Popen directly, or a full filesystem path to an
            executable.

        Returns
        -------
        dict with keys: hwnd, title, rect  — or an 'error' key on failure.
        """
        try:
            proc = subprocess.Popen(
                app_name_or_path,
                shell=True,  # allows bare names like 'notepad', 'calc', etc.
            )
        except Exception as exc:
            return {"error": f"Failed to launch '{app_name_or_path}': {exc}"}

        # Derive a search term from the path/name for window matching.
        # Strip directory and extension: "C:\Windows\notepad.exe" → "notepad"
        import os
        search_hint = os.path.splitext(os.path.basename(app_name_or_path))[0].lower()

        deadline = time.time() + self._OPEN_TIMEOUT
        found_hwnd: Optional[int] = None
        found_title: str = ""

        while time.time() < deadline:
            time.sleep(self._POLL_INTERVAL)

            def _enum_callback(hwnd, _):
                nonlocal found_hwnd, found_title
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                title = win32gui.GetWindowText(hwnd) or ""
                if search_hint in title.lower() and title.strip():
                    found_hwnd = hwnd
                    found_title = title
                    return False  # stop enumeration
                return True

            try:
                win32gui.EnumWindows(_enum_callback, None)
            except Exception:
                pass  # EnumWindows raises when callback returns False — that's ok

            if found_hwnd is not None:
                break

        if found_hwnd is None:
            return {
                "error": (
                    f"Window for '{app_name_or_path}' did not appear within "
                    f"{self._OPEN_TIMEOUT}s"
                )
            }

        try:
            rect = win32gui.GetWindowRect(found_hwnd)
        except Exception:
            rect = (0, 0, 0, 0)

        return {
            "hwnd": found_hwnd,
            "title": found_title,
            "rect": {
                "left": rect[0],
                "top": rect[1],
                "right": rect[2],
                "bottom": rect[3],
                "width": rect[2] - rect[0],
                "height": rect[3] - rect[1],
            },
        }

    def close(self, app_name: str) -> bool:
        """Close the application window gracefully by sending WM_CLOSE.

        Returns True if a matching window was found and the message was sent,
        False otherwise.
        """
        hwnd = self._find_hwnd(app_name)
        if hwnd is None:
            return False
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            return True
        except Exception:
            return False

    def focus(self, app_name: str) -> bool:
        """Bring the named application's window to the foreground.

        Returns True on success, False if no matching window was found.
        """
        hwnd = self._find_hwnd(app_name)
        if hwnd is None:
            return False
        try:
            # Restore if minimised before bringing to foreground
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception:
            return False

    def get_state(self, app_name: str) -> dict:
        """Return information about the named application's window.

        Returns a dict with: title, rect, is_minimized, is_maximized.
        Returns an 'error' key when no matching window is found.
        """
        hwnd = self._find_hwnd(app_name)
        if hwnd is None:
            return {"error": f"Window not found for app: {app_name}"}

        try:
            title = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            title = ""

        try:
            rect = win32gui.GetWindowRect(hwnd)
            rect_dict = {
                "left": rect[0],
                "top": rect[1],
                "right": rect[2],
                "bottom": rect[3],
                "width": rect[2] - rect[0],
                "height": rect[3] - rect[1],
            }
        except Exception:
            rect_dict = {}

        try:
            is_minimized = bool(win32gui.IsIconic(hwnd))
        except Exception:
            is_minimized = False

        try:
            is_maximized = bool(win32gui.IsZoomed(hwnd))
        except Exception:
            is_maximized = False

        return {
            "hwnd": hwnd,
            "title": title,
            "rect": rect_dict,
            "is_minimized": is_minimized,
            "is_maximized": is_maximized,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_hwnd(self, app_name: str) -> Optional[int]:
        """Enumerate all top-level windows and return the hwnd of the first
        whose title contains app_name (case-insensitive).

        Returns None when no match is found.
        """
        search = app_name.lower()
        found: list[int] = []

        def _callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = (win32gui.GetWindowText(hwnd) or "").lower()
            if search in title:
                found.append(hwnd)
                return False  # stop on first match
            return True

        try:
            win32gui.EnumWindows(_callback, None)
        except Exception:
            pass  # raised when callback returns False — that's expected

        return found[0] if found else None
