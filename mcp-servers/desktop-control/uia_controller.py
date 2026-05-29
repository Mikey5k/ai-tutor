import uiautomation as auto
import json
import time
from typing import Optional


class UIAController:
    """Wraps the uiautomation library to query the UIA accessibility tree."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_tree(self, app_name: str) -> dict:
        """Query UIA accessibility tree of named application window.

        Returns a nested dict representing the full control tree rooted at
        the application's top-level window.
        """
        window = self._window_for_app(app_name)
        if window is None:
            return {"error": f"Window not found for app: {app_name}"}
        return self._serialize_control(window, max_depth=5)

    def find_element(
        self,
        app_name: str,
        element_name: str = None,
        role: str = None,
    ) -> Optional[dict]:
        """Find a specific element by name and/or role within an app window.

        Returns a dict with coordinates, state, and element info on the first
        match, or None when nothing is found.
        """
        window = self._window_for_app(app_name)
        if window is None:
            return None
        return self._find_in_tree(window, element_name, role, depth=0, max_depth=8)

    def get_text(self, app_name: str, element_name: str) -> str:
        """Return the text content of a specific named element.

        Prefers ValuePattern.Value, falls back to the control's Name property.
        """
        element_info = self.find_element(app_name, element_name=element_name)
        if element_info is None:
            return ""
        # element_info["_control"] is not serialised — re-find the raw control
        window = self._window_for_app(app_name)
        if window is None:
            return ""
        control = self._find_raw_control(window, element_name, role=None, max_depth=8)
        if control is None:
            return ""
        try:
            vp = control.GetValuePattern()
            if vp:
                return vp.Value
        except Exception:
            pass
        try:
            return control.Name or ""
        except Exception:
            return ""

    def wait_for_element(
        self,
        app_name: str,
        element_name: str,
        timeout_seconds: int = 10,
    ) -> Optional[dict]:
        """Block until the named element appears or the timeout expires.

        Polls every 0.5 seconds.  Returns element info dict or None.
        """
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            result = self.find_element(app_name, element_name=element_name)
            if result is not None:
                return result
            time.sleep(0.5)
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _window_for_app(self, app_name: str) -> Optional[auto.Control]:
        """Find the top-level WindowControl whose title contains app_name.

        Iterates all children of the root (desktop) control so we can match
        partial, case-insensitive window titles.
        """
        search = app_name.lower()
        try:
            root = auto.GetRootControl()
            for child in root.GetChildren():
                try:
                    title = (child.Name or "").lower()
                    if search in title:
                        return child
                except Exception:
                    continue
        except Exception:
            pass

        # Fallback: use uiautomation's built-in search
        try:
            window = auto.WindowControl(searchDepth=1, Name=app_name)
            if window.Exists(0, 0):
                return window
        except Exception:
            pass

        return None

    def _serialize_control(self, control: auto.Control, max_depth: int = 5) -> dict:
        """Recursively serialize a UIA control to a plain dict.

        Catches all exceptions gracefully because some controls in broken or
        transitioning applications throw on basic property reads.
        """
        node: dict = {}

        # --- basic properties ---
        try:
            node["name"] = control.Name or ""
        except Exception:
            node["name"] = ""

        try:
            node["control_type"] = control.ControlTypeName or ""
        except Exception:
            node["control_type"] = ""

        try:
            node["automation_id"] = control.AutomationId or ""
        except Exception:
            node["automation_id"] = ""

        try:
            node["class_name"] = control.ClassName or ""
        except Exception:
            node["class_name"] = ""

        # --- bounding rectangle ---
        try:
            rect = control.BoundingRectangle
            node["rect"] = {
                "x": rect.left,
                "y": rect.top,
                "width": rect.width(),
                "height": rect.height(),
            }
        except Exception:
            node["rect"] = {"x": 0, "y": 0, "width": 0, "height": 0}

        # --- state ---
        try:
            node["is_enabled"] = control.IsEnabled
        except Exception:
            node["is_enabled"] = None

        try:
            node["is_offscreen"] = control.IsOffscreen
        except Exception:
            node["is_offscreen"] = None

        # --- value (for editable controls, etc.) ---
        try:
            vp = control.GetValuePattern()
            node["value"] = vp.Value if vp else None
        except Exception:
            node["value"] = None

        # --- children ---
        node["children"] = []
        if max_depth > 0:
            try:
                for child in control.GetChildren():
                    try:
                        node["children"].append(
                            self._serialize_control(child, max_depth=max_depth - 1)
                        )
                    except Exception:
                        continue
            except Exception:
                pass

        return node

    def _find_in_tree(
        self,
        control: auto.Control,
        element_name: Optional[str],
        role: Optional[str],
        depth: int,
        max_depth: int,
    ) -> Optional[dict]:
        """DFS search through the UIA tree for a matching control.

        Returns a serialized dict (with coordinates) for the first match.
        """
        if depth > max_depth:
            return None

        name_match = True
        role_match = True

        try:
            ctrl_name = (control.Name or "").lower()
            if element_name and element_name.lower() not in ctrl_name:
                name_match = False
        except Exception:
            name_match = False

        try:
            ctrl_role = (control.ControlTypeName or "").lower()
            if role and role.lower() not in ctrl_role:
                role_match = False
        except Exception:
            role_match = False

        if name_match and role_match and (element_name or role):
            return self._serialize_control(control, max_depth=2)

        try:
            for child in control.GetChildren():
                result = self._find_in_tree(
                    child, element_name, role, depth + 1, max_depth
                )
                if result is not None:
                    return result
        except Exception:
            pass

        return None

    def _find_raw_control(
        self,
        control: auto.Control,
        element_name: Optional[str],
        role: Optional[str],
        max_depth: int,
        depth: int = 0,
    ) -> Optional[auto.Control]:
        """Like _find_in_tree but returns the raw uiautomation Control object."""
        if depth > max_depth:
            return None

        name_match = True
        role_match = True

        try:
            ctrl_name = (control.Name or "").lower()
            if element_name and element_name.lower() not in ctrl_name:
                name_match = False
        except Exception:
            name_match = False

        try:
            ctrl_role = (control.ControlTypeName or "").lower()
            if role and role.lower() not in ctrl_role:
                role_match = False
        except Exception:
            role_match = False

        if name_match and role_match and (element_name or role):
            return control

        try:
            for child in control.GetChildren():
                result = self._find_raw_control(
                    child, element_name, role, max_depth, depth + 1
                )
                if result is not None:
                    return result
        except Exception:
            pass

        return None
