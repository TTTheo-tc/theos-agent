"""Credential leak detection — scans text for exposed secrets.

Two-point scanning:
  1. Before outbound HTTP requests (prevent exfiltration)
  2. Agent output before user delivery (prevent accidental exposure)

Reference: ironclaw/src/safety/leak_detector.rs
"""

from __future__ import annotations

import base64
import binascii
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
        self._automaton: ahocorasick.Automaton | None = None
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
                action = _encoded_secret_action(token, self._entropy_sensitivity) or LeakAction.REDACT
                matches.append(
                    LeakMatch("high_entropy_token", token[:30] + "...", action)
                )

        if not matches:
            return LeakScanResult(clean=True)

        known_spans = _known_secret_spans(text)

        # Redact high-entropy token spans after removing known-secret spans.
        # Apply in reverse order to preserve offsets against the original text.
        redacted = text
        entropy_segments = [
            (segment_start, segment_end, tag)
            for start, end, tag in entropy_hits
            for segment_start, segment_end in _subtract_spans(start, end, known_spans)
        ]
        for start, end, tag in sorted(entropy_segments, key=lambda h: h[0], reverse=True):
            redacted = redacted[:start] + tag + redacted[end:]
        redacted = _redact_known_patterns(redacted)

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
_SENSITIVE_PHRASE_RE = re.compile(
    r"(?i)\b(token|api[\s_-]?key|password|secret|user[\s_-]?key|bearer|credential|authorization)\b"
    r"(?:(?:\W+)|(?:\s+(?:is|are|was|were|equals?|value|for|as|to)\b)){1,6}"
    r"\s*[A-Za-z0-9_.~+/=-]{16,}"
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


def _known_secret_spans(text: str) -> list[tuple[int, int]]:
    """Return spans covered by configured prefix and regex secrets."""
    spans: list[tuple[int, int]] = []
    for prefix, _name, _action in _PREFIX_PATTERNS:
        start = text.find(prefix)
        while start != -1:
            end = start + len(prefix)
            while end < len(text) and not text[end].isspace():
                end += 1
            spans.append((start, end))
            start = text.find(prefix, start + len(prefix))
    for regex, _name, _action in _REGEX_PATTERNS:
        spans.extend(match.span() for match in regex.finditer(text))
    return spans


def _subtract_spans(
    start: int,
    end: int,
    spans: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Return portions of [start, end) not covered by any span."""
    segments = [(start, end)]
    for span_start, span_end in spans:
        next_segments: list[tuple[int, int]] = []
        for seg_start, seg_end in segments:
            if span_end <= seg_start or seg_end <= span_start:
                next_segments.append((seg_start, seg_end))
                continue
            if seg_start < span_start:
                next_segments.append((seg_start, min(span_start, seg_end)))
            if span_end < seg_end:
                next_segments.append((max(span_end, seg_start), seg_end))
        segments = next_segments
        if not segments:
            break
    return [(seg_start, seg_end) for seg_start, seg_end in segments if seg_start < seg_end]


_HIGH_ENTROPY_RE = re.compile(r"[A-Za-z0-9_+\-]{24,}")
_URL_RE = re.compile(r"https?://\S+")
_HEX_RE = re.compile(r"^[0-9a-f]{32,}$", re.I)
_UUID_RE = re.compile(r"^[0-9a-f-]{36}$", re.I)
_BASE64_RE = re.compile(r"^[A-Za-z0-9_+\-/]+={0,2}$")
_BASE64_PADDED_RE = re.compile(r"^[A-Za-z0-9_+\-/]+={1,2}$")
_BASE64_PADDED_CANDIDATE_RE = re.compile(
    r"(?<![A-Za-z0-9_+\-/])[A-Za-z0-9_+\-/]{24,}={1,2}(?![A-Za-z0-9_+\-/=])"
)
_BASE64_UNPADDED_CANDIDATE_RE = re.compile(
    r"(?<![A-Za-z0-9_+\-/])[A-Za-z0-9_+\-/]{24,}(?![A-Za-z0-9_+\-/])"
)
_PATH_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,8}(?=$|:\d+(?::\d+)?)")
_PATH_PREFIXES = {
    "assets",
    "build",
    "dist",
    "lib",
    "node_modules",
    "opt",
    "private",
    "public",
    "src",
    "static",
    "tmp",
    "Users",
    "var",
    "vendor",
}


def _shannon_entropy(s: str) -> float:
    """Compute Shannon entropy of *s* in bits per character."""
    if not s:
        return 0.0
    freq = Counter(s)
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in freq.values())


def _check_high_entropy(
    text: str,
    sensitivity: float = 0.7,
    *,
    inspect_padded_base64: bool = True,
    inspect_encoded_text: bool = True,
) -> list[tuple[int, int, str]]:
    """Return ``[(start, end, tag)]`` for high-entropy tokens in *text*."""
    ignored_spans = [(m.start(), m.end()) for m in _URL_RE.finditer(text)]
    sensitivity = max(0.0, min(sensitivity, 1.0))
    threshold = 4.75 - sensitivity * 1.25
    hits: list[tuple[int, int, str]] = []
    base64_candidates = (
        _padded_base64_candidates(text) if inspect_padded_base64 else []
    )
    base64_spans = [(start, end) for start, end, _token in base64_candidates]

    for start, end, token in base64_candidates:
        if any(s < end and start < e for s, e in ignored_spans):
            continue
        decoded = _decode_base64_text(token, require_padding=True)
        if decoded is not None:
            if _decoded_secret_action(decoded, sensitivity) is not None:
                hits.append((start, end, "[REDACTED_HIGH_ENTROPY_TOKEN]"))
            elif _looks_like_natural_text(decoded):
                continue
            elif _shannon_entropy(token) >= threshold:
                hits.append((start, end, "[REDACTED_HIGH_ENTROPY_TOKEN]"))
            continue
        if _shannon_entropy(token) >= threshold:
            hits.append((start, end, "[REDACTED_HIGH_ENTROPY_TOKEN]"))

    if inspect_encoded_text:
        for start, end, token in _unpadded_base64_candidates(text):
            if any(s < end and start < e for s, e in ignored_spans):
                continue
            if any(s <= start and end <= e for s, e in base64_spans):
                continue
            path_like = _looks_like_path_token(token)
            path_has_ext = path_like and _path_span_has_file_extension(text, end)
            decoded = _decode_base64_text(token, require_padding=False)
            if decoded is not None and _decoded_secret_action(decoded, sensitivity) is not None:
                hits.append((start, end, "[REDACTED_HIGH_ENTROPY_TOKEN]"))
                base64_spans.append((start, end))
                continue
            if decoded is not None and _looks_like_natural_text(decoded):
                base64_spans.append((start, end))
                continue
            if path_has_ext and _looks_like_skippable_file_path(token):
                base64_spans.append((start, end))
                continue
            path_segments = _path_entropy_segments(token, file_extension=path_has_ext) if path_like else []
            for segment_offset, segment in path_segments:
                if _shannon_entropy(segment) >= threshold:
                    segment_start = start + segment_offset
                    hits.append((segment_start, end, "[REDACTED_HIGH_ENTROPY_TOKEN]"))
                    base64_spans.append((segment_start, end))
                    break
            if path_like:
                continue
            if _shannon_entropy(token) >= threshold:
                hits.append((start, end, "[REDACTED_HIGH_ENTROPY_TOKEN]"))
                base64_spans.append((start, end))

    for start, end, token in _regular_entropy_candidates(text):
        if any(s < end and start < e for s, e in ignored_spans):
            continue
        if any(s <= start and end <= e for s, e in base64_spans):
            continue
        if _is_safe_entropy_token(token):
            continue
        decoded = _decode_base64_text(token, require_padding=False) if inspect_encoded_text else None
        if decoded is not None and _decoded_secret_action(decoded, sensitivity) is not None:
            hits.append((start, end, "[REDACTED_HIGH_ENTROPY_TOKEN]"))
            continue
        if _shannon_entropy(token) >= threshold:
            hits.append((start, end, "[REDACTED_HIGH_ENTROPY_TOKEN]"))
    return hits


def _regular_entropy_candidates(text: str) -> list[tuple[int, int, str]]:
    return [_entropy_candidate(text, match) for match in _HIGH_ENTROPY_RE.finditer(text)]


def _padded_base64_candidates(text: str) -> list[tuple[int, int, str]]:
    return [
        (match.start(), match.end(), match.group())
        for match in _BASE64_PADDED_CANDIDATE_RE.finditer(text)
        if _has_valid_base64_padding(match.group())
    ]


def _unpadded_base64_candidates(text: str) -> list[tuple[int, int, str]]:
    return [
        (match.start(), match.end(), match.group())
        for match in _BASE64_UNPADDED_CANDIDATE_RE.finditer(text)
        if "/" in match.group()
    ]


def _entropy_candidate(text: str, match: re.Match[str]) -> tuple[int, int, str]:
    """Return candidate span, including base64 padding only at token boundaries."""
    start, end = match.start(), match.end()
    pad_end = end
    while pad_end < len(text) and text[pad_end] == "=":
        pad_end += 1
    pad_count = pad_end - end
    if (
        0 < pad_count <= 2
        and (pad_end == len(text) or not _is_entropy_body_char(text[pad_end]))
        and _has_valid_base64_padding(text[start:pad_end])
    ):
        end = pad_end
    return start, end, text[start:end]


def _is_entropy_body_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in "_+-/")


def _is_safe_entropy_token(token: str) -> bool:
    return bool(_HEX_RE.match(token) or _UUID_RE.match(token))


def _looks_like_path_token(token: str) -> bool:
    if "/" not in token:
        return False
    if token.startswith(("/", "./", "../", "~/")):
        return True
    segments = [segment for segment in token.split("/") if segment]
    if not segments:
        return False
    return segments[0] in _PATH_PREFIXES or any(_PATH_EXT_RE.search(segment) for segment in segments)


def _path_span_has_file_extension(text: str, end: int) -> bool:
    return bool(_PATH_EXT_RE.match(text[end:]))


def _looks_like_build_asset_path(token: str) -> bool:
    segments = [segment for segment in token.split("/") if segment]
    if len(segments) < 2 or segments[0] not in {"assets", "build", "dist", "public", "static"}:
        return False
    leaf = segments[-1].lower()
    return leaf.startswith(("app-", "bundle-", "chunk-", "index-", "main-", "vendor-"))


def _looks_like_skippable_file_path(token: str) -> bool:
    return _looks_like_build_asset_path(token)


def _path_entropy_segments(token: str, *, file_extension: bool) -> list[tuple[int, str]]:
    segments: list[tuple[int, str]] = []
    offset = 0
    for segment in token.split("/"):
        segment_start = offset
        offset += len(segment) + 1
        segments.append((segment_start, segment))

    candidates: list[tuple[int, str]] = []
    meaningful = [
        (start, segment)
        for start, segment in segments
        if segment not in {"", ".", "..", "~"} and segment not in _PATH_PREFIXES
    ]
    if not meaningful:
        return []

    if file_extension:
        leaf_start, leaf = meaningful[-1]
        if _looks_like_entropy_path_segment(leaf):
            candidates.append((leaf_start, leaf))

        run_start: int | None = None
        run_end = 0
        for start, segment in meaningful[:-1]:
            if _looks_like_entropy_path_segment(segment):
                if run_start is None:
                    run_start = start
                run_end = start + len(segment)
                continue
            if run_start is not None:
                candidates.append((run_start, token[run_start:run_end]))
                run_start = None
        if run_start is not None:
            candidates.append((run_start, token[run_start:run_end]))
        return candidates

    first_start, _first_segment = meaningful[0]
    return [(first_start, token[first_start:])]


def _looks_like_entropy_path_segment(segment: str) -> bool:
    if len(segment) < 16:
        return False
    classes = sum(
        (
            any(ch.islower() for ch in segment),
            any(ch.isupper() for ch in segment),
            any(ch.isdigit() for ch in segment),
            any(ch in "_+-" for ch in segment),
        )
    )
    return classes >= 2


def _has_valid_base64_padding(token: str) -> bool:
    if not _BASE64_PADDED_RE.match(token):
        return False
    padding = len(token) - len(token.rstrip("="))
    body = token[:-padding]
    return bool(body) and len(token) % 4 == 0


def _decode_base64_text(token: str, *, require_padding: bool) -> str | None:
    if require_padding and not _has_valid_base64_padding(token):
        return None
    if not _BASE64_RE.match(token):
        return None
    body = token.rstrip("=")
    if not body:
        return None
    padded = body + "=" * (-len(body) % 4)
    try:
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        return None
    return _decode_printable_text(raw)


def _decode_printable_text(raw: bytes) -> str | None:
    if not raw:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    printable = sum(ch.isprintable() or ch.isspace() for ch in text)
    if printable / len(text) < 0.95:
        return None
    return text


def _looks_like_natural_text(text: str) -> bool:
    return any(ch.isspace() for ch in text)


def _encoded_secret_action(token: str, sensitivity: float) -> LeakAction | None:
    decoded = _decode_base64_text(token, require_padding=False)
    if decoded is None:
        return None
    return _decoded_secret_action(decoded, sensitivity)


def _decoded_secret_action(text: str, sensitivity: float) -> LeakAction | None:
    action = _known_secret_action(text)
    if action is not None:
        return action
    if _SENSITIVE_KV_RE.search(text) or _SENSITIVE_PHRASE_RE.search(text):
        return LeakAction.REDACT
    if _contains_slash_high_entropy_token(text, sensitivity):
        return LeakAction.REDACT
    if _check_high_entropy(
        text,
        sensitivity,
        inspect_padded_base64=False,
        inspect_encoded_text=False,
    ):
        return LeakAction.REDACT
    return None


def _contains_slash_high_entropy_token(text: str, sensitivity: float) -> bool:
    threshold = 4.75 - max(0.0, min(sensitivity, 1.0)) * 1.25
    for _start, end, token in _unpadded_base64_candidates(text):
        if _path_span_has_file_extension(text, end) and _looks_like_skippable_file_path(token):
            continue
        path_segments = (
            _path_entropy_segments(
                token,
                file_extension=_path_span_has_file_extension(text, end),
            )
            if _looks_like_path_token(token)
            else []
        )
        entropy_texts = [segment for _offset, segment in path_segments] or [token]
        if any(_shannon_entropy(entropy_text) >= threshold for entropy_text in entropy_texts):
            return True
    return False


def _known_secret_action(text: str) -> LeakAction | None:
    action: LeakAction | None = None
    for prefix, _name, prefix_action in _PREFIX_PATTERNS:
        if prefix in text:
            if prefix_action == LeakAction.BLOCK:
                return LeakAction.BLOCK
            action = prefix_action
    for regex, _name, regex_action in _REGEX_PATTERNS:
        if regex.search(text):
            if regex_action == LeakAction.BLOCK:
                return LeakAction.BLOCK
            action = regex_action
    return action


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
