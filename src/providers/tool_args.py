"""Shared helpers for provider tool-call argument parsing."""

from __future__ import annotations

import json
from typing import Any

import json_repair


def parse_tool_arguments_object(
    raw: Any,
    *,
    preserve_raw: bool = False,
    repair_json: bool = True,
) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            parsed = json_repair.loads(raw) if repair_json else json.loads(raw)
        except Exception:
            return {"raw": raw} if preserve_raw else {}
    else:
        parsed = raw

    if isinstance(parsed, dict):
        return parsed
    return {"raw": raw} if preserve_raw else {}
