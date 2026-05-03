"""Dashboard API route handlers — package root."""

from __future__ import annotations

from starlette.routing import Route

from src.ui.routes.config import config_get, config_put
from src.ui.routes.cron import (
    cron_job_create,
    cron_job_delete,
    cron_job_run,
    cron_job_update,
    cron_jobs_list,
)
from src.ui.routes.health import health
from src.ui.routes.logs import logs_list, logs_stream
from src.ui.routes.memory import (
    memory_markdown,
    memory_node_detail,
    memory_nodes_list,
    memory_search,
)
from src.ui.routes.sessions import (
    agents_list,
    channels,
    cost_metrics,
    events_stream,
    metrics,
    search,
    session_detail,
    sessions_list,
)
from src.ui.routes.settings import settings_get, settings_put
from src.ui.routes.tools import tools_list, tools_profiles


def collect_routes() -> list[Route]:
    """Return all API routes. New feature modules append here."""
    return [
        Route("/api/health", health),
        Route("/api/sessions", sessions_list),
        Route("/api/sessions/{session_id}", session_detail),
        Route("/api/agents", agents_list),
        Route("/api/metrics", metrics),
        Route("/api/metrics/cost", cost_metrics),
        Route("/api/channels", channels),
        Route("/api/search", search),
        Route("/api/events", events_stream),
        Route("/api/memory/nodes", memory_nodes_list),
        Route("/api/memory/search", memory_search),
        Route("/api/memory/nodes/{node_id}", memory_node_detail),
        Route("/api/memory/markdown", memory_markdown),
        Route("/api/cron/jobs", cron_jobs_list, methods=["GET"]),
        Route("/api/cron/jobs", cron_job_create, methods=["POST"]),
        Route("/api/cron/jobs/{job_id}", cron_job_update, methods=["PUT"]),
        Route("/api/cron/jobs/{job_id}", cron_job_delete, methods=["DELETE"]),
        Route("/api/cron/jobs/{job_id}/run", cron_job_run, methods=["POST"]),
        Route("/api/logs", logs_list),
        Route("/api/logs/stream", logs_stream),
        Route("/api/config", config_get, methods=["GET"]),
        Route("/api/config", config_put, methods=["PUT"]),
        Route("/api/settings", settings_get, methods=["GET"]),
        Route("/api/settings", settings_put, methods=["PUT"]),
        Route("/api/tools", tools_list),
        Route("/api/tools/profiles", tools_profiles),
    ]
