"""CLI command implementation for ``theos status``."""

from __future__ import annotations

import typer
from rich.markup import escape

from src import __version__
from src.cli.display import (
    console,
    make_plain_row,
    make_status_row,
    print_status_header,
)


def _print_provider_statuses(config) -> None:
    from src.auth.store import get_api_key_for_provider
    from src.providers.registry import ordered_providers

    for spec in ordered_providers():
        provider_config = getattr(config.providers, spec.name, None)
        if provider_config is None:
            continue
        if spec.is_oauth:
            console.print(make_plain_row(spec.label, "[green]ok[/] (OAuth)"))
        elif spec.is_local:
            if provider_config.api_base:
                console.print(make_plain_row(spec.label, f"[green]ok[/] {provider_config.api_base}"))
            else:
                console.print(make_plain_row(spec.label, "[dim]not set[/dim]"))
        else:
            auth_key = get_api_key_for_provider(spec.name)
            if auth_key:
                console.print(make_plain_row(spec.label, "[green]ok[/] (auth profile)"))
            elif provider_config.api_key:
                console.print(make_plain_row(spec.label, "[green]ok[/]"))
            else:
                console.print(make_plain_row(spec.label, "[dim]not set[/dim]"))


def _print_gateway_status() -> None:
    try:
        from src.daemon import resolve_service

        svc = resolve_service()
        if not svc.is_loaded():
            console.print(make_plain_row("Gateway", "[dim]not installed[/dim]"))
            return

        status = svc.status()
        pid = status.get("pid")
        if pid:
            console.print(make_plain_row("Gateway", f"[green]running[/] (PID {pid})"))
        else:
            console.print(make_plain_row("Gateway", "[yellow]loaded but not running[/]"))
    except NotImplementedError:
        pass


def status() -> None:
    """Show theos status."""
    from loguru import logger

    from src.config.loader import get_config_path, load_config

    logger.remove()

    config_path = get_config_path()
    try:
        config = load_config()
    except Exception as exc:
        print_status_header(__version__)
        console.print()
        console.print(make_status_row("Config", config_path, config_path.exists()))
        console.print(make_plain_row("Config error", f"[red]{escape(str(exc))}[/]"))
        raise typer.Exit(1) from exc

    print_status_header(__version__)
    console.print()

    workspace = config.workspace_path
    console.print(make_status_row("Config", config_path, config_path.exists()))
    console.print(make_status_row("Workspace", workspace, workspace.exists()))

    if config_path.exists():
        console.print(make_plain_row("Model", config.agents.defaults.model))
        _print_provider_statuses(config)

    _print_gateway_status()
