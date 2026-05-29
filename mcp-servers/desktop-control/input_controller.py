import pyautogui
import time
import math
from typing import Literal

# Safety: move mouse to corner to abort; add a small pause between actions
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05


class InputController:
    """Controls mouse and keyboard using PyAutoGUI."""

    def __init__(self):
        self.screen_width, self.screen_height = pyautogui.size()

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------

    def mouse_move(self, x: int, y: int, smooth: bool = True) -> None:
        """Move mouse to absolute screen coordinates.

        Validates that (x, y) are within screen bounds before moving.
        When smooth=True uses a 0.3 s linear duration; otherwise instant.
        """
        x = self._clamp(x, 0, self.screen_width - 1)
        y = self._clamp(y, 0, self.screen_height - 1)

        if smooth:
            pyautogui.moveTo(x, y, duration=0.3)
        else:
            pyautogui.moveTo(x, y)

    def mouse_click(self, x: int, y: int, button: str = "left") -> None:
        """Click at the specified screen coordinates.

        Moves to the target first, then performs the click so the cursor
        position is always reflected visually before the click fires.
        Supported button values: 'left', 'right', 'middle'.
        """
        x = self._clamp(x, 0, self.screen_width - 1)
        y = self._clamp(y, 0, self.screen_height - 1)

        valid_buttons = {"left", "right", "middle"}
        if button not in valid_buttons:
            button = "left"

        self.mouse_move(x, y, smooth=True)
        pyautogui.click(x, y, button=button)

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def keyboard_type(self, text: str, interval_ms: int = 50) -> None:
        """Type a string with realistic keystroke timing.

        PyAutoGUI's typewrite() only handles printable ASCII.  Characters
        outside that range (accents, unicode symbols, etc.) are inserted via
        pyautogui.write() which handles a broader set, and for anything that
        still fails we fall back to a per-character hotkey approach.
        """
        interval_sec = max(0.0, interval_ms / 1000.0)

        # Split into runs of pure printable ASCII vs everything else so we
        # can use the faster typewrite() path where possible.
        ascii_printable = set(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "0123456789"
            r" `~!@#$%^&*()-_=+[{]}\|;:'\",<.>/?"
        )

        buffer = ""
        for ch in text:
            if ch in ascii_printable:
                buffer += ch
            else:
                # Flush buffered ASCII first
                if buffer:
                    pyautogui.typewrite(buffer, interval=interval_sec)
                    buffer = ""
                # Handle the non-ASCII character
                try:
                    pyautogui.write(ch, interval=interval_sec)
                except Exception:
                    # Last resort: paste via clipboard (requires pyperclip)
                    try:
                        import pyperclip
                        pyperclip.copy(ch)
                        pyautogui.hotkey("ctrl", "v")
                        time.sleep(interval_sec)
                    except Exception:
                        pass  # Give up on this character

        # Flush any remaining buffered ASCII
        if buffer:
            pyautogui.typewrite(buffer, interval=interval_sec)

    def keyboard_press(self, key: str) -> None:
        """Press a single key or a '+'-separated key combination.

        Examples
        --------
        keyboard_press('enter')
        keyboard_press('ctrl+s')
        keyboard_press('ctrl+shift+esc')
        """
        key = key.strip()

        if "+" in key:
            # Split on '+' but handle edge-cases like 'ctrl++'  (pressing '+')
            parts = key.split("+")
            parts = [p.strip() for p in parts if p.strip()]
            if len(parts) == 1:
                # e.g. input was '+' which splits to ['', ''] → single '+'
                pyautogui.press("+")
            else:
                pyautogui.hotkey(*parts)
        else:
            pyautogui.press(key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clamp(value: int, lo: int, hi: int) -> int:
        return max(lo, min(hi, value))
