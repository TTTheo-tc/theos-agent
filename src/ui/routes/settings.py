"""UI settings API routes — read/write UI preferences."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from starlette.requests import Request
from starlette.responses import JSONResponse


class UISettings(BaseModel):
    """Settings for dashboard UI."""

    model_config = ConfigDict(extra="forbid")

    theme: str = "dark"
    refresh_interval_ms: int = 10000
    logs_auto_scroll: bool = True
    logs_default_level: str = "INFO"
    sidebar_collapsed: bool = False


def _settings_path(request: Request) -> Path | None:
    """Resolve workspace path from app context."""
    ctx = request.app.state.app_context or {}
    workspace = ctx.get("workspace")
    if not workspace:
        return None
    return Path(workspace) / "data" / "ui-settings.json"


def _load_settings(path: Path | None) -> UISettings:
    """Load settings from file or return defaults."""
    if path and path.exists():
        data = json.loads(path.read_text())
        return UISettings.model_validate(data)
    return UISettings()


async def settings_get(request: Request) -> JSONResponse:
    """GET /api/settings — retrieve current settings."""
    path = _settings_path(request)
    settings = _load_settings(path)
    return JSONResponse(settings.model_dump())


async def settings_put(request: Request) -> JSONResponse:
    """PUT /api/settings — merge incoming fields with current settings."""
    path = _settings_path(request)
    if not path:
        return JSONResponse({"error": "no workspace"}, status_code=503)

    current = _load_settings(path)
    body = await request.json()

    # Merge incoming fields with current settings
    merged = {**current.model_dump(), **body}
    settings = UISettings.model_validate(merged)

    # Write to disk
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings.model_dump(), indent=2))

    return JSONResponse(settings.model_dump())
