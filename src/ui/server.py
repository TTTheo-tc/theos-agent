"""Starlette app factory for the dashboard UI server."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.routing import Route

from src.ui.db import DashboardReader
from src.ui.routes import collect_routes

if TYPE_CHECKING:
    from src.ui.events import UIEventBus


def _resolve_ui_dist() -> Path | None:
    """Find UI static files: installed package -> repo dev."""
    pkg_static = Path(__file__).parent / "ui_static"
    if (pkg_static / "index.html").exists():
        return pkg_static
    repo_dist = Path(__file__).parent.parent.parent / "ui" / "dist"
    if (repo_dist / "index.html").exists():
        return repo_dist
    return None


def create_ui_app(
    db_path: Path,
    static_dir: Path | None = None,
    event_bus: UIEventBus | None = None,
    app_context: dict | None = None,
) -> Starlette:
    """Create the dashboard Starlette app."""
    api_routes = collect_routes()

    app_routes = list(api_routes)

    # Mount static files for SPA with catch-all fallback
    resolved_static = static_dir or _resolve_ui_dist()
    if resolved_static and resolved_static.exists():
        static_root = resolved_static.resolve()
        _index_html = (resolved_static / "index.html").read_text()

        async def _spa_handler(request: Request):
            """Serve static file if it exists, otherwise index.html for SPA routing."""
            from starlette.responses import FileResponse, HTMLResponse, JSONResponse

            path = request.path_params.get("path", "")

            # Never serve index.html for /api/* — return proper 404
            if path.startswith("api/"):
                return JSONResponse({"error": "not found"}, status_code=404)

            # Serve actual static files (JS, CSS, images)
            if path:
                file_path = (resolved_static / path).resolve()
                # Path traversal guard — must stay within static dir
                if not file_path.is_relative_to(static_root):
                    return JSONResponse({"error": "forbidden"}, status_code=403)
                if file_path.is_file():
                    return FileResponse(str(file_path))

            # Client-side routes → index.html (React Router handles routing)
            return HTMLResponse(_index_html)

        app_routes.append(Route("/", _spa_handler))
        app_routes.append(Route("/{path:path}", _spa_handler))

    # CORS only needed when Vite dev server proxies to Python API (dev mode)
    middleware = []
    if not resolved_static:
        middleware.append(
            Middleware(
                CORSMiddleware,
                allow_origins=["http://localhost:5173"],
                allow_methods=["GET", "PUT", "POST", "DELETE"],
                allow_headers=["Content-Type"],
            )
        )

    reader = DashboardReader(db_path)

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        await reader.connect()
        yield
        await reader.close()
        # Clean up KG connection if memory routes opened one
        kg = getattr(app.state, "_kg", None)
        if kg:
            await kg.close()

    app = Starlette(routes=app_routes, middleware=middleware, lifespan=lifespan)

    app.state.db = reader
    app.state.event_bus = event_bus
    app.state.app_context = app_context or {}

    return app


async def start_ui_server(app: Starlette, host: str, port: int):
    """Start the UI server as an asyncio task. Returns a runner for cleanup.

    Raises OSError if the server fails to bind (e.g. port in use).
    """
    import asyncio

    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None

    task = asyncio.create_task(server.serve(), name="ui_server")

    # Wait briefly for startup — if the task fails (e.g. port bind error),
    # it will complete quickly with an exception.
    for _ in range(10):
        await asyncio.sleep(0.05)
        if server.started:
            break
        if task.done():
            # Server failed to start — propagate the exception
            task.result()  # raises the stored exception

    if not server.started and task.done():
        task.result()

    class _Runner:
        async def cleanup(self):
            server.should_exit = True
            await task

    return _Runner()
