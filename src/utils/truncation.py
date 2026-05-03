"""Truncation utilities for tool-call arguments.

Extracted from session/manager.py so that truncation logic can be reused
without depending on the session subsystem.
"""

from __future__ import annotations

import json
from typing import Any


def _truncate_value(v: Any, max_len: int = 200) -> Any:
    """Truncate a single value if it's a long string, preserving type."""
    if isinstance(v, str) and len(v) > max_len:
        return v[:max_len] + "... [truncated]"
    return v


def truncate_tool_call_arguments(
    tool_calls: list[dict[str, Any]] | None, max_chars: int
) -> list[dict[str, Any]] | None:
    """Return a copy of tool calls with oversized function arguments truncated.

    Preserves valid JSON by truncating individual values within the parsed
    arguments dict, rather than slicing the raw JSON string.
    """
    if not isinstance(tool_calls, list):
        return tool_calls

    truncated: list[dict[str, Any]] = []
    limit = max(1, max_chars)

    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            truncated.append(tool_call)
            continue

        entry = dict(tool_call)
        fn = tool_call.get("function")
        if isinstance(fn, dict):
            fn_entry = dict(fn)
            arguments = fn.get("arguments")
            args_text = None
            if isinstance(arguments, str):
                args_text = arguments
            elif arguments is not None:
                args_text = json.dumps(arguments, ensure_ascii=False)

            if args_text is not None and len(args_text) > limit:
                try:
                    parsed = json.loads(args_text) if isinstance(arguments, str) else arguments
                    if isinstance(parsed, dict):
                        parsed = {k: _truncate_value(v) for k, v in parsed.items()}
                    fn_entry["arguments"] = json.dumps(parsed, ensure_ascii=False)
                except (json.JSONDecodeError, TypeError):
                    fn_entry["arguments"] = json.dumps(
                        {"_note": "tool arguments too large to include"}
                    )
            entry["function"] = fn_entry
        truncated.append(entry)

    return truncated
