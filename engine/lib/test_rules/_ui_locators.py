from __future__ import annotations

import re

LOCATOR_NAMES = frozenset({
    *(prefix + "By" + kind for prefix in ("get", "query", "find", "getAll", "queryAll", "findAll")
      for kind in ("Text", "LabelText", "PlaceholderText", "AltText", "Title", "DisplayValue", "Label", "Placeholder")),
    *(prefix + "_by_" + kind for prefix in ("get", "query", "find", "get_all", "query_all", "find_all")
      for kind in ("text", "label_text", "placeholder_text", "alt_text", "title", "display_value", "label", "placeholder")),
    "linkText", "partialLinkText", "withText", "withContentDescription", "onNodeWithText", "onAllNodesWithText",
    "find_element_by_link_text", "find_element_by_partial_link_text", "find_elements_by_link_text",
    "click_on", "click_link", "click_button", "click_link_or_button", "find_link", "find_button",
})
LOCATOR_ROOTED = {"contains": frozenset({"cy", "cypress"})}
SELECTOR_TEXT_RE = re.compile(
    r"""^\s*text\s*=\s*["']?(?P<plain>[^"']+)|:(?:has-text|text|text-is|text-matches)\(\s*["'](?P<pseudo>[^"']+)["']"""
    r"""|contains\(\s*(?:text\(\)|\.)\s*,\s*["'](?P<xpath>[^"']+)["']|text\(\)\s*=\s*["'](?P<exact>[^"']+)["']""",
)
SELECTOR_CALLS = frozenset({
    "locator", "frameLocator", "frame_locator", "click", "dblclick", "fill", "hover", "tap", "check", "press", "type",
    "waitForSelector", "wait_for_selector", "querySelector", "query_selector", "$", "$$", "find_element",
    "find_elements", "findElement", "findElements", "xpath", "get", "find", "textContent", "text_content",
    "innerText", "inner_text", "isVisible", "is_visible",
})
LINK_TEXT_CONSTANTS = frozenset({"LINK_TEXT", "PARTIAL_LINK_TEXT"})


def is_locator(parts: list[str]) -> bool:
    if not parts:
        return False
    tail = parts[-1]
    if tail in LOCATOR_NAMES:
        return True
    roots = LOCATOR_ROOTED.get(tail)
    return roots is not None and len(parts) > 1 and parts[0] in roots


def selects(parts: list[str]) -> bool:
    return bool(parts) and parts[-1] in SELECTOR_CALLS


def selector_text(literal: str) -> str | None:
    found = SELECTOR_TEXT_RE.search(literal)
    if found is None:
        return None
    return next((group for group in found.groups() if group), None)
