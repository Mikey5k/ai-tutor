import mss
import mss.tools
import tempfile
import time
import os
import shutil
from pathlib import Path
from typing import Optional


class ScreenshotCapture:
    def __init__(self, temp_dir: str = None):
        self.temp_dir = temp_dir or tempfile.gettempdir()
        self._session_dir = None

    def setup_session(self) -> str:
        """Create a session temp directory for screenshots."""
        timestamp = int(time.time())
        session_path = Path(self.temp_dir) / f"tutor_screenshots_{timestamp}"
        session_path.mkdir(parents=True, exist_ok=True)
        self._session_dir = str(session_path)
        return self._session_dir

    def capture(self, region: Optional[dict] = None) -> str:
        """Take screenshot. region = {left, top, width, height} or None for full screen.
        Returns file path."""
        if self._session_dir is None:
            self.setup_session()

        timestamp_ms = int(time.time() * 1000)
        filename = str(Path(self._session_dir) / f"screen_{timestamp_ms}.png")

        with mss.mss() as sct:
            if region is not None:
                grab_region = {
                    "left": int(region["left"]),
                    "top": int(region["top"]),
                    "width": int(region["width"]),
                    "height": int(region["height"]),
                }
                screenshot = sct.grab(grab_region)
            else:
                screenshot = sct.grab(sct.monitors[0])

            mss.tools.to_png(screenshot.rgb, screenshot.size, output=filename)

        return filename

    def cleanup_session(self):
        """Delete all screenshots from current session."""
        if self._session_dir is not None and os.path.isdir(self._session_dir):
            shutil.rmtree(self._session_dir, ignore_errors=True)
        self._session_dir = None
