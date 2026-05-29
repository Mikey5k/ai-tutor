import re
from typing import List, Dict, Any


class DOMParser:
    """Strips raw DOM/HTML noise before returning elements to Claude Code."""

    # Common navigation/boilerplate words to filter out
    _NAV_WORDS = {
        "home", "about", "contact", "login", "sign in", "sign up",
        "register", "logout", "sign out", "cookie", "privacy", "terms",
        "menu", "navigation", "skip to content", "back to top",
        "all rights reserved", "copyright", "subscribe", "follow us",
        "facebook", "twitter", "instagram", "linkedin", "youtube",
        "search", "toggle", "close", "open", "accept", "decline",
    }

    def parse_elements(
        self, raw_elements: List[dict], return_fields: List[str]
    ) -> List[dict]:
        """Filter and clean DOM elements. Remove empties, limit to 100 results.

        Args:
            raw_elements: Raw list of element dicts from querySelectorAll evaluation.
            return_fields: List of field names to include in output.

        Returns:
            Filtered list of element dicts containing only the requested fields.
        """
        results: List[dict] = []

        for element in raw_elements:
            if len(results) >= 100:
                break

            parsed: Dict[str, Any] = {}
            has_content = False

            for field in return_fields:
                value = element.get(field)

                # Normalize strings
                if isinstance(value, str):
                    value = value.strip()
                    if value:
                        has_content = True

                # Keep booleans and numbers even if "falsy"
                elif isinstance(value, (bool, int, float)):
                    has_content = True

                if value is not None and value != "":
                    parsed[field] = value

            # Skip elements with no meaningful content
            if not has_content:
                continue

            # If the only content is textContent and it's empty after strip, skip
            if return_fields == ["textContent"] and not parsed.get("textContent", "").strip():
                continue

            results.append(parsed)

        return results

    def extract_page_text(self, raw_text: str) -> str:
        """Clean page text: collapse whitespace, remove nav boilerplate lines.

        Args:
            raw_text: Raw innerText from the page.

        Returns:
            Cleaned, readable page text.
        """
        if not raw_text:
            return ""

        lines = raw_text.splitlines()
        cleaned_lines: List[str] = []

        for line in lines:
            stripped = line.strip()

            # Drop completely empty strings (will handle double-newlines later)
            if not stripped:
                cleaned_lines.append("")
                continue

            # Drop boilerplate navigation lines
            if self._is_boilerplate(stripped):
                continue

            cleaned_lines.append(stripped)

        # Collapse runs of 3+ blank lines into 2
        result_lines: List[str] = []
        blank_run = 0
        for line in cleaned_lines:
            if line == "":
                blank_run += 1
                if blank_run <= 2:
                    result_lines.append(line)
            else:
                blank_run = 0
                result_lines.append(line)

        text = "\n".join(result_lines).strip()

        # Collapse multiple spaces within a line
        text = re.sub(r"[^\S\n]{2,}", " ", text)

        return text

    def _is_boilerplate(self, text: str) -> bool:
        """Detect navigation/cookie/footer boilerplate to filter out.

        Args:
            text: A single stripped line of text.

        Returns:
            True if the line looks like boilerplate navigation content.
        """
        # Very short lines are often nav items (but not always useful content)
        if len(text) > 80:
            return False

        lower = text.lower()

        # Exact or near-exact match to known nav words
        if lower in self._NAV_WORDS:
            return True

        # Lines that are purely a nav word optionally followed by punctuation
        cleaned = re.sub(r"[|\-•·/\\]", " ", lower).strip()
        words = cleaned.split()
        if words and all(w in self._NAV_WORDS for w in words):
            return True

        # Cookie/GDPR banners
        if re.search(
            r"(we use cookies|cookie policy|gdpr|this (site|website) uses cookies)",
            lower,
        ):
            return True

        # Copyright footers
        if re.search(r"©|\bcopyright\b.*\d{4}", lower):
            return True

        return False
