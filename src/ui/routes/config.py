"""Config editor API routes — read with redaction, patch-write with secret preservation."""

from __future__ import annotations

import copy
import json
import shutil

from starlette.requests import Request
from starlette.responses import JSONResponse

from src.security.config_secrets import is_sensitive_config_path

_REDACTED = "***"


def _redact(data: dict, path: str = "") -> dict:
    """Deep-redact sensitive fields in a config dict."""
    result = {}
    for key, value in data.items():
        current = f"{path}.{key}" if path else key
        if is_sensitive_config_path(current):
            result[key] = _REDACTED
        elif isinstance(value, dict):
            result[key] = _redact(value, current)
        else:
            result[key] = value
    return result


def _deep_merge(base: dict, patch: dict, path: str = "") -> dict:
    """Merge patch into base, preserving secrets when patch has *** or is missing."""
    result = copy.deepcopy(base)
    for key, value in patch.items():
        current = f"{path}.{key}" if path else key
        if value == _REDACTED and is_sensitive_config_path(current):
            continue  # Keep original
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value, current)
        else:
            result[key] = value
    return result


async def config_get(request: Request) -> JSONResponse:
    ctx = request.app.state.app_context or {}
    config = ctx.get("config")
    if not config:
        from src.config.loader import load_config

        config = load_config()
    data = config.model_dump(by_alias=True)
    return JSONResponse(_redact(data))


async def config_put(request: Request) -> JSONResponse:
    ctx = request.app.state.app_context or {}
    config_path = ctx.get("config_path")
    if not config_path:
        return JSONResponse({"error": "Requires running gateway"}, status_code=503)

    body = await request.json()
    current_raw = json.loads(config_path.read_text())
    merged = _deep_merge(current_raw, body)

    from src.config.schema import Config

    try:
        Config.model_validate(merged)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    import asyncio

    def _write():
        shutil.copy2(config_path, str(config_path) + ".bak")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)

    await asyncio.to_thread(_write)

    return JSONResponse({"ok": True})
