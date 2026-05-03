"""Logs viewer API routes."""

from __future__ import annotations

import json
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse

from src.safety.leak_detector import scrub_credentials


def _log_path(request: Request) -> Path | None:
    workspace = (request.app.state.app_context or {}).get("workspace")
    if not workspace:
        return None
    p = Path(workspace) / "logs" / "gateway.log"
    return p if p.exists() else None


def _parse_log_line(line: str) -> dict | None:
    """Parse a loguru serialize=True JSON line."""
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
        record = data.get("record", {})
        return {
            "level": record.get("level", {}).get("name", "INFO"),
            "message": scrub_credentials(record.get("message", "")),
            "timestamp": record.get("time", {}).get("repr", ""),
            "logger": record.get("name", ""),
        }
    except (json.JSONDecodeError, KeyError):
        return None


async def logs_list(request: Request) -> JSONResponse:
    path = _log_path(request)
    if not path:
        return JSONResponse([])

    level = request.query_params.get("level")
    q = request.query_params.get("q", "").lower()
    limit = min(int(request.query_params.get("limit", "200")), 1000)

    import asyncio

    def _read_and_filter():
        entries = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            entry = _parse_log_line(line)
            if not entry:
                continue
            if level and entry["level"] != level:
                continue
            if q and q not in entry["message"].lower():
                continue
            entries.append(entry)
        return entries[-limit:]

    entries = await asyncio.to_thread(_read_and_filter)
    return JSONResponse(entries)


async def logs_stream(request: Request):
    """SSE endpoint for real-time log streaming."""
    import asyncio

    from starlette.responses import StreamingResponse

    log_bus = getattr(request.app.state, "log_event_bus", None)

    async def generate():
        if log_bus:
            async for entry in log_bus.subscribe():
                yield f"data: {json.dumps(entry, default=str)}\n\n"
        else:
            path = _log_path(request)
            if not path:
                yield "data: {}\n\n"
                return
            try:
                last_pos = path.stat().st_size
            except FileNotFoundError:
                yield "data: {}\n\n"
                return
            while not await request.is_disconnected():
                await asyncio.sleep(1)
                try:
                    cur_size = path.stat().st_size
                except FileNotFoundError:
                    last_pos = 0
                    continue
                if cur_size < last_pos:
                    # Log rotation detected — file was replaced
                    last_pos = 0
                if cur_size > last_pos:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        f.seek(last_pos)
                        for line in f:
                            entry = _parse_log_line(line)
                            if entry:
                                yield f"data: {json.dumps(entry)}\n\n"
                    last_pos = cur_size

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
