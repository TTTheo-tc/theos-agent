"""Credential leak detection — scans text for exposed secrets.

Two-point scanning:
  1. Before outbound HTTP requests (prevent exfiltration)
  2. Agent output before user delivery (prevent accidental exposure)

Reference: ironclaw/src/safety/leak_detector.rs
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    import ahocorasick  # type: ignore[import-untyped]

    _HAS_AHO = True
except ImportError:
    _HAS_AHO = False


class LeakAction(Enum):
    """What to do when a leak is detected."""

    BLOCK = "block"  # Reject entirely
    REDACT = "redact"  # Replace with [REDACTED]
    WARN = "warn"  # Log but allow


@dataclass
class LeakMatch:
    """A single detected leak."""

    pattern_name: str
    matched_text: str  # Truncated for safety
    action: LeakAction


@dataclass
class LeakScanResult:
    """Result of a leak scan."""

    clean: bool
    matches: list[LeakMatch] = field(default_factory=list)
    redacted_text: str | None = None

    @property
    def should_block(self) -> bool:
        return any(m.action == LeakAction.BLOCK for m in self.matches)


# ── Leak patterns ────────────────────────────────────────────────────────

_PREFIX_PATTERNS: list[tuple[str, str, LeakAction]] = [
    # API keys — longer prefixes first to avoid short-prefix shadowing
    ("sk-ant-", "anthropic_api_key", LeakAction.BLOCK),
    ("sk-proj-", "openai_project_key", LeakAction.BLOCK),
    ("sk-or-v1-", "openrouter_key", LeakAction.BLOCK),
    ("xoxb-", "slack_bot_token", LeakAction.BLOCK),
    ("xoxp-", "slack_user_token", LeakAction.BLOCK),
    ("xapp-", "slack_app_token", LeakAction.BLOCK),
    ("ghp_", "github_pat", LeakAction.BLOCK),
    ("gho_", "github_oauth", LeakAction.BLOCK),
    ("ghs_", "github_server", LeakAction.BLOCK),
    ("glpat-", "gitlab_pat", LeakAction.BLOCK),
    ("AKIA", "aws_access_key", LeakAction.BLOCK),
    # PEM / private keys
    ("-----BEGIN RSA PRIVATE KEY-----", "rsa_private_key", LeakAction.BLOCK),
    ("-----BEGIN PRIVATE KEY-----", "private_key", LeakAction.BLOCK),
    ("-----BEGIN EC PRIVATE KEY-----", "ec_private_key", LeakAction.BLOCK),
    ("-----BEGIN OPENSSH PRIVATE KEY-----", "openssh_private_key", LeakAction.BLOCK),
    ("-----BEGIN PGP PRIVATE KEY BLOCK-----", "pgp_private_key", LeakAction.BLOCK),
]

_REGEX_PATTERNS: list[tuple[re.Pattern[str], str, LeakAction]] = [
    # JWT tokens (3 base64 segments)
    (
        re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
        "jwt_token",
        LeakAction.REDACT,
    ),
    # Generic Bearer tokens in text
    (
        re.compile(r"Bearer\s+[A-Za-z0-9_.~+/=-]{20,}", re.IGNORECASE),
        "bearer_token",
        LeakAction.REDACT,
    ),
    # Database connection strings
    (
        re.compile(
            r"(?:postgres|mysql|mongodb|redis)://\S+:\S+@\S+",
            re.IGNORECASE,
        ),
        "db_connection_string",
        LeakAction.BLOCK,
    ),
]


class LeakDetector:
    """Scan text for credential leaks."""

    def __init__(self, *, entropy_sensitivity: float = 0.0) -> None:
        self._automaton: "ahocorasick.Automaton | None" = None
        self._entropy_sensitivity = entropy_sensitivity

        if _HAS_AHO:
            self._automaton = ahocorasick.Automaton()
            for prefix, name, action in _PREFIX_PATTERNS:
                self._automaton.add_word(prefix, (prefix, name, action))
            self._automaton.make_automaton()

    def scan(self, text: str) -> LeakScanResult:
        """Scan *text* for credential patterns.

        Returns a :class:`LeakScanResult` with matches and optionally redacted text.
        """
        if not text:
            return LeakScanResult(clean=True)

        matches: list[LeakMatch] = []

        matches.extend(_iter_prefix_matches(text, self._automaton))

        # Regex patterns
        for regex, name, action in _REGEX_PATTERNS:
            for m in regex.finditer(text):
                matched = m.group()[:30]
                matches.append(LeakMatch(name, matched + "...", action))

        # High-entropy token detection (opt-in via sensitivity > 0)
        entropy_hits: list[tuple[int, int, str]] = []
        if self._entropy_sensitivity > 0:
            entropy_hits = _check_high_entropy(text, self._entropy_sensitivity)
            for start, end, _tag in entropy_hits:
                token = text[start:end]
                matches.append(
                    LeakMatch("high_entropy_token", token[:30] + "...", LeakAction.REDACT)
                )

        if not matches:
            return LeakScanResult(clean=True)

        redacted = _redact_known_patterns(text)

        # Redact high-entropy tokens (apply in reverse order to preserve offsets)
        for start, end, tag in sorted(entropy_hits, key=lambda h: h[0], reverse=True):
            redacted = redacted[:start] + tag + redacted[end:]

        return LeakScanResult(clean=False, matches=matches, redacted_text=redacted)


def redact(value: str, visible: int = 4) -> str:
    """Show first *visible* chars + ``***``. Safe for multi-byte."""
    if len(value) <= visible:
        return "***"
    return value[:visible] + "***"


_SENSITIVE_KV_RE = re.compile(
    r"(?i)(token|api[_-]?key|password|secret|user[_-]?key|bearer|credential|authorization)"
    r"""(["']?\s*[:=]\s*)(?:"([^"]{8,})"|'([^']{8,})'|([a-zA-Z0-9_\-\.]{8,}))"""
)


def scrub_credentials(text: str) -> str:
    """Redact key-value credential patterns with partial masking.

    Matches patterns like ``api_key="sk-abc..."`` or ``password=secret123``
    and replaces the value with first-4-chars + ``***``.
    """

    def _replace(m: re.Match) -> str:
        key = m.group(1)
        sep = m.group(2)
        val = m.group(3) or m.group(4) or m.group(5)
        masked = redact(val)
        if m.group(3):
            return f'{key}{sep}"{masked}"'
        elif m.group(4):
            return f"{key}{sep}'{masked}'"
        return f"{key}{sep}{masked}"

    return _SENSITIVE_KV_RE.sub(_replace, text)


def _iter_prefix_matches(text: str, automaton: Any | None) -> list[LeakMatch]:
    """Find all configured secret prefixes in *text*."""
    matches: list[LeakMatch] = []
    if automaton is not None:
        for end_idx, (prefix, name, action) in automaton.iter(text):
            start = end_idx - len(prefix) + 1
            matches.append(LeakMatch(name, _match_preview(text, start, prefix), action))
        return matches

    for prefix, name, action in _PREFIX_PATTERNS:
        start = text.find(prefix)
        while start != -1:
            matches.append(LeakMatch(name, _match_preview(text, start, prefix), action))
            start = text.find(prefix, start + len(prefix))
    return matches


def _match_preview(text: str, start: int, prefix: str) -> str:
    end = min(start + len(prefix) + 8, len(text))
    return text[start:end] + "..."


def _redact_known_patterns(text: str) -> str:
    """Redact configured prefix and regex secrets from *text*."""
    redacted = text
    # Process longer prefixes first to avoid short-prefix shadowing.
    for prefix, _name, _action in sorted(_PREFIX_PATTERNS, key=lambda p: len(p[0]), reverse=True):
        redacted = _redact_after_prefix(redacted, prefix)
    for regex, _name, _action in _REGEX_PATTERNS:
        redacted = regex.sub("[REDACTED]", redacted)
    return redacted


_HIGH_ENTROPY_RE = re.compile(r"[a-zA-Z0-9_\-]{24,}")
_URL_RE = re.compile(r"https?://\S+")
_SAFE_PATTERNS = re.compile(
    r"^[0-9a-f]{32,}$" r"|^[0-9a-f-]{36}$" r"|.*[=]{1,2}$",
    re.I,
)


def _shannon_entropy(s: str) -> float:
    """Compute Shannon entropy of *s* in bits per character."""
    if not s:
        return 0.0
    freq = Counter(s)
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in freq.values())


def _check_high_entropy(text: str, sensitivity: float = 0.7) -> list[tuple[int, int, str]]:
    """Return ``[(start, end, tag)]`` for high-entropy tokens in *text*."""
    ignored_spans = [(m.start(), m.end()) for m in _URL_RE.finditer(text)]
    threshold = 3.5 + sensitivity * 1.25
    hits: list[tuple[int, int, str]] = []
    for m in _HIGH_ENTROPY_RE.finditer(text):
        if any(s < m.end() and m.start() < e for s, e in ignored_spans):
            continue
        token = m.group()
        if _SAFE_PATTERNS.match(token):
            continue
        if _shannon_entropy(token) >= threshold:
            hits.append((m.start(), m.end(), "[REDACTED_HIGH_ENTROPY_TOKEN]"))
    return hits


def _redact_after_prefix(text: str, prefix: str) -> str:
    """Replace characters after *prefix* until whitespace with [REDACTED]."""
    result = []
    i = 0
    while i < len(text):
        idx = text.find(prefix, i)
        if idx == -1:
            result.append(text[i:])
            break
        result.append(text[i:idx])
        # Find end of token (next whitespace or end)
        end = idx + len(prefix)
        while end < len(text) and not text[end].isspace():
            end += 1
        result.append(f"{prefix}[REDACTED]")
        i = end
    return "".join(result)
