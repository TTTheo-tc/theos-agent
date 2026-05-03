"""Gateway health route for daemon readiness checks."""

from __future__ import annotations

import os

from starlette.requests import Request
from starlette.responses import JSONResponse


async def health(_request: Request) -> JSONResponse:
    """Return a lightweight process identity payload for readiness probes."""
    return JSONResponse({"ok": True, "pid": os.getpid()})
