"""Strict text-based tool-call parser.

Extracts tool calls from model output that uses XML-style tags or fenced
code blocks.  Bare JSON in prose is intentionally ignored.
"""

from __future__ import annotations

import re
import uuid

from src.providers.base import ToolCallRequest
from src.providers.tool_args import parse_tool_arguments_object

# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

FALLBACK_PROVIDER_ALLOWLIST: frozenset[str] = frozenset(
    {
        "deepseek",
        "minimax",
        "groq",
        "zhipu",
        "moonshot",
        "dashscope",
        "vllm",
    }
)

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Matches <tool_call>...</tool_call> or <FunctionCall>...</FunctionCall>
# (case-insensitive, dotall so newlines are included in the payload).
_XML_PATTERN = re.compile(
    r"<(tool_call|functioncall)>(.*?)</\1>",
    re.IGNORECASE | re.DOTALL,
)

# Matches ```tool or ```tool_call fenced code blocks.
_FENCED_PATTERN = re.compile(
    r"```(?:tool|tool_call)\s*\n(.*?)\n```",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_id() -> str:
    return f"parsed_{uuid.uuid4().hex[:8]}"


def _validate_and_build(payload_text: str) -> ToolCallRequest | None:
    """Parse and validate a raw JSON payload string.

    Returns a ToolCallRequest on success, None on any validation failure.
    """
    data = parse_tool_arguments_object(payload_text)

    name = data.get("name")
    arguments = data.get("arguments")

    if not isinstance(name, str) or not name:
        return None
    if not isinstance(arguments, dict):
        return None

    return ToolCallRequest(id=_make_id(), name=name, arguments=arguments)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_tool_calls_from_text(text: str | None) -> list[ToolCallRequest]:
    """Parse tool calls from model-generated text.

    Only two wrapper forms are recognised:
    - XML tags: ``<tool_call>...</tool_call>`` / ``<FunctionCall>...</FunctionCall>``
    - Fenced code blocks labelled ``tool`` or ``tool_call``

    Plain JSON embedded in prose is never matched.

    Args:
        text: Raw text from the model response.  ``None`` is treated as empty.

    Returns:
        Ordered list of successfully parsed :class:`ToolCallRequest` objects.
        Returns an empty list when no valid tool calls are found.
    """
    if not text:
        return []

    results: list[ToolCallRequest] = []
    matches: list[tuple[int, str]] = []

    for match in _XML_PATTERN.finditer(text):
        matches.append((match.start(), match.group(2).strip()))

    for match in _FENCED_PATTERN.finditer(text):
        matches.append((match.start(), match.group(1).strip()))

    for _, payload in sorted(matches, key=lambda item: item[0]):
        tc = _validate_and_build(payload)
        if tc is not None:
            results.append(tc)

    return results
