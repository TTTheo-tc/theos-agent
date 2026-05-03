"""Dashboard API route handlers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from src.ui.db import DashboardReader


def _safe_int(value: str | None, default: int, max_val: int = 1000) -> int:
    """Parse a query param as int, clamped to [1, max_val]."""
    try:
        return max(1, min(int(value or default), max_val))
    except (ValueError, TypeError):
        return default


def _safe_bool(value: str | None) -> bool:
    """Parse a query param as a permissive boolean."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


async def sessions_list(request: Request) -> JSONResponse:
    db: DashboardReader = request.app.state.db
    limit = _safe_int(request.query_params.get("limit"), 20)
    recoverable_only = _safe_bool(request.query_params.get("recoverable_only"))
    data = await db.get_sessions(limit=limit, recoverable_only=recoverable_only)
    return JSONResponse(data)


async def session_detail(request: Request) -> JSONResponse:
    db: DashboardReader = request.app.state.db
    session_id = request.path_params["session_id"]
    session = await db.get_session(session_id)
    if not session:
        return JSONResponse({"error": "not found"}, status_code=404)
    agents = await db.get_agents_by_session(session_id)
    return JSONResponse({**session, "agents": agents})


async def agents_list(request: Request) -> JSONResponse:
    db: DashboardReader = request.app.state.db
    data = await db.get_agents(limit=50)
    return JSONResponse(data)


async def metrics(request: Request) -> JSONResponse:
    db: DashboardReader = request.app.state.db
    data = await db.get_metrics()
    return JSONResponse(data)


async def cost_metrics(request: Request) -> JSONResponse:
    db: DashboardReader = request.app.state.db
    data = await db.get_cost_metrics()
    return JSONResponse(data)


async def channels(request: Request) -> JSONResponse:
    db: DashboardReader = request.app.state.db
    data = await db.get_channel_stats()
    return JSONResponse(data)


async def search(request: Request) -> JSONResponse:
    db: DashboardReader = request.app.state.db
    q = request.query_params.get("q", "")
    if not q:
        return JSONResponse([])
    data = await db.search(q)
    return JSONResponse(data)


async def events_stream(request: Request):
    """SSE endpoint — gateway mode uses event bus, standalone polls DB."""
    import asyncio

    from starlette.responses import StreamingResponse

    db: DashboardReader = request.app.state.db
    event_bus = getattr(request.app.state, "event_bus", None)
    last_id = int(request.query_params.get("last_event_id", "0"))

    async def generate():
        nonlocal last_id

        # Send initial batch
        initial = await db.get_events(since_id=last_id, limit=50)
        for evt in reversed(initial):
            eid = evt["id"]
            yield f"id: {eid}\ndata: {_json_dumps(evt)}\n\n"
            if eid > last_id:
                last_id = eid

        if event_bus:
            # Gateway mode: direct push
            async for evt in event_bus.subscribe():
                if await request.is_disconnected():
                    break
                eid = evt["id"]
                yield f"id: {eid}\ndata: {_json_dumps(evt)}\n\n"
        else:
            # Standalone mode: poll DB
            while not await request.is_disconnected():
                await asyncio.sleep(2)
                new_events = await db.get_events(since_id=last_id, limit=20)
                for evt in reversed(new_events):
                    eid = evt["id"]
                    yield f"id: {eid}\ndata: {_json_dumps(evt)}\n\n"
                    if eid > last_id:
                        last_id = eid

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


def _json_dumps(obj: dict) -> str:
    return json.dumps(obj, default=str)
