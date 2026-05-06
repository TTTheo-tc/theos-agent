"""Prompt injection detection via multi-pattern matching.

Scans text for known injection patterns (instruction override, role
manipulation, system message injection, special tokens) and returns
a sanitized result with warnings.

Applied at:
  - User input before LLM call
  - MCP tool return values
  - Web fetch content

Reference: ironclaw/src/safety/sanitizer.rs
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

try:
    import ahocorasick  # type: ignore[import-untyped]

    _HAS_AHO = True
except ImportError:
    _HAS_AHO = False

# ── Exact-match patterns (Aho-Corasick) ──────────────────────────────────

_EXACT_PATTERNS: list[tuple[str, str]] = [
    # Instruction override
    ("ignore previous", "instruction_override"),
    ("ignore all previous", "instruction_override"),
    ("ignore above", "instruction_override"),
    ("disregard previous", "instruction_override"),
    ("forget everything", "instruction_override"),
    ("forget all instructions", "instruction_override"),
    ("override instructions", "instruction_override"),
    ("new instructions:", "instruction_override"),
    ("忽略之前", "instruction_override"),
    ("忽略以上", "instruction_override"),
    ("无视之前", "instruction_override"),
    # Role manipulation
    ("you are now", "role_manipulation"),
    ("act as", "role_manipulation"),
    ("pretend to be", "role_manipulation"),
    ("pretend you are", "role_manipulation"),
    ("roleplay as", "role_manipulation"),
    ("from now on you", "role_manipulation"),
    ("你现在是", "role_manipulation"),
    ("假装你是", "role_manipulation"),
    # System message injection
    ("system:", "system_injection"),
    ("[system]", "system_injection"),
    ("<<SYS>>", "system_injection"),
    ("<</SYS>>", "system_injection"),
    # Special tokens
    ("<|im_start|>", "special_token"),
    ("<|im_end|>", "special_token"),
    ("<|endoftext|>", "special_token"),
    ("[INST]", "special_token"),
    ("[/INST]", "special_token"),
    ("```system", "code_block_injection"),
]

# ── Regex patterns (for harder-to-match structures) ──────────────────────

_REGEX_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?:assistant|user|system)\s*:\s*\n",
            re.IGNORECASE,
        ),
        "role_prefix_injection",
    ),
    (
        re.compile(r"<\|[a-z_]+\|>", re.IGNORECASE),
        "special_token",
    ),
]


@dataclass
class SanitizedOutput:
    """Result of sanitization: the (possibly modified) content and any warnings."""

    content: str
    warnings: list[str] = field(default_factory=list)
    was_modified: bool = False


class Sanitizer:
    """Detect and flag prompt injection attempts.

    Does NOT modify content by default — returns warnings so the caller
    can decide whether to block, redact, or proceed with caution.
    """

    def __init__(self, *, block: bool = False) -> None:
        self._block = block
        self._automaton: ahocorasick.Automaton | None = None

        if _HAS_AHO:
            self._automaton = ahocorasick.Automaton()
            for pattern, category in _EXACT_PATTERNS:
                self._automaton.add_word(pattern.lower(), (pattern, category))
            self._automaton.make_automaton()

    def scan(self, text: str) -> SanitizedOutput:
        """Scan *text* for injection patterns.

        Returns a :class:`SanitizedOutput` with any detected warnings.
        If ``block=True`` was set at init, flagged content is replaced with
        ``[BLOCKED: prompt injection detected]``.
        """
        if not text:
            return SanitizedOutput(content=text)

        warnings: list[str] = []
        lower = text.lower()

        warnings.extend(self._iter_exact_warnings(lower))
        warnings.extend(_iter_regex_warnings(text))

        if not warnings:
            return SanitizedOutput(content=text)

        if self._block:
            return SanitizedOutput(
                content="[BLOCKED: prompt injection detected]",
                warnings=warnings,
                was_modified=True,
            )
        return SanitizedOutput(content=text, warnings=warnings)

    def _iter_exact_warnings(self, lower: str) -> list[str]:
        """Return warnings for exact-pattern matches in lower-cased text."""
        warnings: list[str] = []
        if self._automaton is not None:
            for _end_idx, (pattern, category) in self._automaton.iter(lower):
                warnings.append(f"{category}: matched '{pattern}'")
            return warnings

        for pattern, category in _EXACT_PATTERNS:
            if pattern.lower() in lower:
                warnings.append(f"{category}: matched '{pattern}'")
        return warnings


def _iter_regex_warnings(text: str) -> list[str]:
    """Return warnings for regex-based injection matches."""
    warnings: list[str] = []
    for regex, category in _REGEX_PATTERNS:
        if regex.search(text):
            warnings.append(f"{category}: regex match")
    return warnings
