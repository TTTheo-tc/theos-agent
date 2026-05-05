"""Fuzzy match utilities for Feishu document editing.

Handles Unicode normalization differences between AI-generated text
(ASCII quotes, hyphens) and Feishu-exported markdown (smart quotes,
em-dashes, special spaces).

Inspired by pi-mono's normalizeForFuzzyMatch approach.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Unicode smart quotes → ASCII
_SMART_QUOTE_MAP = {
    "\u2018": "'",  # left single
    "\u2019": "'",  # right single
    "\u201c": '"',  # left double
    "\u201d": '"',  # right double
    "\u00ab": '"',  # «
    "\u00bb": '"',  # »
    "\u2039": "'",  # ‹
    "\u203a": "'",  # ›
}

# Unicode dashes → ASCII hyphen
_DASH_MAP = {
    "\u2013": "-",  # en-dash
    "\u2014": "-",  # em-dash
    "\u2015": "-",  # horizontal bar
    "\u2012": "-",  # figure dash
    "\u2010": "-",  # hyphen
}

# Unicode special spaces → regular space
_SPACE_MAP = {
    "\u00a0": " ",  # NBSP
    "\u2002": " ",  # en space
    "\u2003": " ",  # em space
    "\u2009": " ",  # thin space
    "\u200a": " ",  # hair space
    "\u202f": " ",  # narrow NBSP
    "\u205f": " ",  # medium mathematical space
    "\u3000": " ",  # ideographic space
}

# Combined replacement table
_NORMALIZE_TABLE = str.maketrans({**_SMART_QUOTE_MAP, **_DASH_MAP, **_SPACE_MAP})

# Regex to strip trailing whitespace per line
_TRAILING_WS_RE = re.compile(r"[ \t]+$", re.MULTILINE)


@dataclass
class FuzzyMatchResult:
    """Result of a fuzzy text search."""

    found: bool
    index: int  # character offset in the (possibly normalized) content
    match_length: int  # length of the matched text in content
    used_fuzzy_match: bool
    content_for_replacement: str  # the content string where match was found


def normalize_for_fuzzy_match(text: str) -> str:
    """Progressive normalization for fuzzy matching.

    1. Strip trailing whitespace per line
    2. Unicode smart quotes → ASCII
    3. Unicode dashes → ASCII hyphen
    4. Unicode special spaces → regular space
    """
    result = _TRAILING_WS_RE.sub("", text)
    return result.translate(_NORMALIZE_TABLE)


def _not_found(content: str) -> FuzzyMatchResult:
    return FuzzyMatchResult(
        found=False,
        index=-1,
        match_length=0,
        used_fuzzy_match=False,
        content_for_replacement=content,
    )


def _unique_index(content: str, text: str) -> int | None:
    first = content.find(text)
    if first == -1:
        return None
    if content.find(text, first + 1) != -1:
        return None
    return first


def fuzzy_find_text(content: str, old_text: str) -> FuzzyMatchResult:
    """Find old_text in content, trying exact match first, then fuzzy.

    Level 1: Exact match via str.find
    Level 2: Normalize both sides, find in normalized space

    Validates uniqueness (must appear exactly once).

    Returns:
        FuzzyMatchResult with match details, or found=False on failure.
    """
    # Level 1: Exact match
    first = _unique_index(content, old_text)
    if first is not None:
        return FuzzyMatchResult(
            found=True,
            index=first,
            match_length=len(old_text),
            used_fuzzy_match=False,
            content_for_replacement=content,
        )
    if content.find(old_text) != -1:
        return _not_found(content)  # ambiguous — caller should handle

    # Level 2: Fuzzy match (normalize both)
    norm_content = normalize_for_fuzzy_match(content)
    norm_old = normalize_for_fuzzy_match(old_text)

    first = _unique_index(norm_content, norm_old)
    if first is None:
        return _not_found(content)

    return FuzzyMatchResult(
        found=True,
        index=first,
        match_length=len(norm_old),
        used_fuzzy_match=True,
        content_for_replacement=norm_content,
    )


def fuzzy_count(content: str, text: str) -> tuple[int, bool]:
    """Count occurrences of text in content (exact first, fuzzy fallback).

    Returns:
        (count, used_fuzzy) — count of occurrences and whether fuzzy was used.
    """
    exact_count = content.count(text)
    if exact_count > 0:
        return exact_count, False

    norm_content = normalize_for_fuzzy_match(content)
    norm_text = normalize_for_fuzzy_match(text)
    fuzzy_ct = norm_content.count(norm_text)
    return fuzzy_ct, fuzzy_ct > 0
