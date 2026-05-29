from typing import Optional, Dict, Any, List


class AXTreeParser:
    """Parses Chrome AX tree (from Playwright accessibility.snapshot()) into
    clean JSON for Claude Code."""

    # Roles to skip as they add noise
    NOISE_ROLES = {"none", "generic", "presentation", "group"}

    def parse(self, raw_tree: Optional[dict], max_depth: int = 8) -> dict:
        """Parse AX tree, remove noise nodes, return clean JSON.

        Args:
            raw_tree: The raw accessibility snapshot dict from Playwright.
            max_depth: Maximum recursion depth to traverse.

        Returns:
            A clean dict representing the filtered AX tree.
        """
        if raw_tree is None:
            return {"role": "WebArea", "name": "", "children": []}

        parsed = self._parse_node(raw_tree, depth=0, max_depth=max_depth)
        if parsed is None:
            return {"role": "WebArea", "name": "", "children": []}
        return parsed

    def _parse_node(
        self, node: dict, depth: int, max_depth: int
    ) -> Optional[dict]:
        """Recursively parse a node. Return None if node should be filtered.

        Args:
            node: Raw AX node dict from Playwright.
            depth: Current recursion depth.
            max_depth: Maximum allowed depth.

        Returns:
            Cleaned node dict, or None if the node should be omitted.
        """
        if depth > max_depth:
            return None

        role = node.get("role", "")

        # Filter noise roles (unless it's the root WebArea)
        if role in self.NOISE_ROLES and depth > 0:
            # Still recurse into children to surface useful descendants
            children = self._parse_children(node, depth, max_depth)
            if not children:
                return None
            # Promote children by returning a transparent wrapper — but to
            # keep the tree clean we return None and let the caller handle it.
            return None

        # Parse children first so we can decide if this node is useful
        children = self._parse_children(node, depth, max_depth)

        # Build the cleaned node
        cleaned: Dict[str, Any] = {}

        if role:
            cleaned["role"] = role

        # Scalar properties — only include if truthy or explicitly meaningful
        for key in ("name", "value", "description"):
            val = node.get(key)
            if val is not None and val != "":
                cleaned[key] = val

        # Boolean / state properties — include only when True (or non-default)
        for key in ("focusable", "checked", "expanded", "required", "multiselectable"):
            val = node.get(key)
            if val is True:
                cleaned[key] = True

        # level (headings etc.) — include if present
        level = node.get("level")
        if level is not None:
            cleaned["level"] = level

        if children:
            cleaned["children"] = children

        # Filter nodes with no useful content whatsoever
        if not self._is_useful(cleaned):
            return None

        return cleaned

    def _parse_children(
        self, node: dict, depth: int, max_depth: int
    ) -> List[dict]:
        """Parse all children of a node, skipping Nones.

        Args:
            node: Raw AX node dict.
            depth: Current depth (children will be depth+1).
            max_depth: Maximum allowed depth.

        Returns:
            List of successfully parsed child dicts.
        """
        raw_children = node.get("children") or []
        results: List[dict] = []
        for child in raw_children:
            parsed_child = self._parse_node(child, depth + 1, max_depth)
            if parsed_child is not None:
                results.append(parsed_child)
        return results

    def _is_useful(self, node: dict) -> bool:
        """Return True if node has meaningful content.

        A node is considered useful if it has:
        - A non-empty name, value, or description, OR
        - At least one child node, OR
        - Is the root WebArea (always kept), OR
        - Has state properties set (focusable, checked, expanded, etc.)

        Args:
            node: Already-cleaned node dict.

        Returns:
            True if the node should be kept in the output tree.
        """
        # Always keep the root
        if node.get("role") == "WebArea":
            return True

        # Keep if it has text content
        if node.get("name") or node.get("value") or node.get("description"):
            return True

        # Keep if it has children (even if no own text)
        if node.get("children"):
            return True

        # Keep if it has meaningful state properties
        for key in ("focusable", "checked", "expanded", "required", "multiselectable", "level"):
            if key in node:
                return True

        return False
