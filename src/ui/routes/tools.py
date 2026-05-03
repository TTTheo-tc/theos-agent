"""Tools viewer API routes."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse


async def tools_list(request: Request) -> JSONResponse:
    """List all tools, either from live registry (gateway mode) or static profiles (standalone)."""
    ctx = request.app.state.app_context or {}
    registry = ctx.get("tool_registry")

    if registry:
        tools = []
        for name in registry.tool_names:
            tool = registry.get(name)
            if tool:
                tools.append(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "risk_level": tool.risk_level,
                        "owner_only": tool.owner_only,
                        "schema": tool.to_schema(),
                    }
                )
        return JSONResponse({"tools": tools, "mode": "live"})

    from src.agent.tools.tool_profiles import PROFILES, TOOL_GROUPS

    return JSONResponse(
        {
            "profiles": {k: sorted(v) if v else None for k, v in PROFILES.items()},
            "groups": {k: sorted(v) for k, v in TOOL_GROUPS.items()},
            "mode": "static",
        }
    )


async def tools_profiles(request: Request) -> JSONResponse:
    """Get tool profiles and groups."""
    from src.agent.tools.tool_profiles import PROFILES, TOOL_GROUPS

    return JSONResponse(
        {
            "profiles": {k: sorted(v) if v else None for k, v in PROFILES.items()},
            "groups": {k: sorted(v) for k, v in TOOL_GROUPS.items()},
        }
    )
