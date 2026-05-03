"""CLI command — ``theos ui``.

Starts the dashboard HTTP server in standalone mode (read-only, no agent).
"""

from __future__ import annotations

import typer

from src import __logo__
from src.cli.display import console


def ui(
    port: int = typer.Option(8080, "--port", "-p", help="HTTP server port"),
    host: str = typer.Option(
        "127.0.0.1", "--host", help="Bind address (use 0.0.0.0 for network access)"
    ),
):
    """Start the theos dashboard (read-only viewer)."""

    from src.config.loader import load_config
    from src.ui.server import create_ui_app
    from src.ui.tailscale import build_ui_url

    config = load_config()
    db_path = config.workspace_path / "data" / "dashboard.db"

    if not db_path.exists():
        console.print(
            "[red]Dashboard DB not found.[/red] "
            "Start the gateway first: [cyan]theos gateway[/cyan]"
        )
        raise typer.Exit(1)

    app = create_ui_app(db_path=db_path)

    console.print(f"{__logo__} Starting dashboard...")
    console.print(f"  Local:   http://localhost:{port}")
    if host == "0.0.0.0":
        url = build_ui_url(port)
        if "localhost" not in url:
            console.print(f"  Network: {url}")
    console.print()

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")
