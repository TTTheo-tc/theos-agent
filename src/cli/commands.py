"""CLI commands for TheOS — routing hub.

Registers all Typer commands. Heavy implementations live in sub-modules:
- agent_cmd.py   — ``theos agent`` interactive loop
- cron_cmd.py    — ``theos cron`` sub-commands
- auth_cmd.py    — ``theos auth`` + ``theos provider`` sub-commands
- gateway_cmd.py — ``theos gateway``
- init_cmd.py    — ``theos init`` wizard
- channels_cmd.py — ``theos channels`` sub-commands
- feishu_auth_cmd.py — ``theos feishu-auth``
- display.py     — console + display helpers
- repl.py        — terminal / prompt_toolkit helpers
"""

import typer

from src import __logo__, __version__
from src.cli.display import console

app = typer.Typer(
    name="theos",
    help=f"{__logo__} TheOS - Personal AI Assistant",
    no_args_is_help=True,
)


def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} theos v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(None, "--version", "-v", callback=version_callback, is_eager=True),
):
    """TheOS - Personal AI Assistant."""
    pass


# ============================================================================
# Register commands from sub-modules
# ============================================================================

from src.cli.auth_cmd import auth_app, provider_app  # noqa: E402


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    markdown: bool = typer.Option(
        True, "--markdown/--no-markdown", help="Render assistant output as Markdown"
    ),
    logs: bool = typer.Option(
        False, "--logs/--no-logs", help="Show theos runtime logs during chat"
    ),
):
    """Interact with the agent directly."""
    from src.cli.agent_cmd import agent as _agent

    return _agent(message=message, session_id=session_id, markdown=markdown, logs=logs)


@app.command()
def init(
    reset: bool = typer.Option(False, "--reset", help="Reset existing data before init"),
    no_daemon: bool = typer.Option(False, "--no-daemon", help="Skip gateway daemon installation"),
):
    """Initialize TheOS: config, workspace, and provider setup."""
    from src.cli.init_cmd import init as _init

    return _init(reset=reset, no_daemon=no_daemon)


# Heavy command modules stay lazy so `theos --help` and core smoke paths do not
# load agent/init/gateway/channel/Feishu/UI/report implementations.
gateway_app = typer.Typer(
    name="gateway",
    help="Gateway daemon management",
    invoke_without_command=True,
)


@gateway_app.callback(invoke_without_command=True)
def gateway(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start the theos gateway (foreground)."""
    from src.cli.gateway_cmd import gateway as _gateway

    return _gateway(ctx=ctx, verbose=verbose)


@gateway_app.command("stop")
def gateway_stop():
    """Stop the gateway daemon."""
    from src.cli.gateway_cmd import gateway_stop as _gateway_stop

    return _gateway_stop()


@gateway_app.command("restart")
def gateway_restart_cmd():
    """Restart the gateway daemon."""
    from src.cli.gateway_cmd import gateway_restart_cmd as _gateway_restart_cmd

    return _gateway_restart_cmd()


@gateway_app.command("uninstall")
def gateway_uninstall():
    """Stop and remove the gateway daemon service."""
    from src.cli.gateway_cmd import gateway_uninstall as _gateway_uninstall

    return _gateway_uninstall()


@gateway_app.command("logs")
def gateway_logs(
    source: str = typer.Option("app", help="Log source: app, supervisor-stdout, supervisor-stderr"),
    raw: bool = typer.Option(False, "--raw", help="Output raw JSONL"),
):
    """Tail gateway logs."""
    from src.cli.gateway_cmd import gateway_logs as _gateway_logs

    return _gateway_logs(source=source, raw=raw)


@app.command(name="ui")
def ui_command(
    port: int = typer.Option(8080, "--port", "-p", help="HTTP server port"),
    host: str = typer.Option(
        "127.0.0.1", "--host", help="Bind address (use 0.0.0.0 for network access)"
    ),
):
    """Start the theos dashboard (read-only viewer)."""
    from src.cli.ui_cmd import ui as _ui

    return _ui(port=port, host=host)


cron_app = typer.Typer(help="Manage scheduled tasks")


@cron_app.command("list")
def cron_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include disabled jobs"),
):
    """List scheduled jobs."""
    from src.cli.cron_cmd import cron_list as _cron_list

    return _cron_list(all=all)


@cron_app.command("add")
def cron_add(
    name: str = typer.Option(..., "--name", "-n", help="Job name"),
    message: str = typer.Option(..., "--message", "-m", help="Message for agent"),
    every: int = typer.Option(None, "--every", "-e", help="Run every N seconds"),
    cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron expression (e.g. '0 9 * * *')"),
    tz: str | None = typer.Option(
        None, "--tz", help="IANA timezone for cron (e.g. 'America/Vancouver')"
    ),
    at: str = typer.Option(None, "--at", help="Run once at time (ISO format)"),
    deliver: bool = typer.Option(False, "--deliver", "-d", help="Deliver response to channel"),
    to: str = typer.Option(None, "--to", help="Recipient for delivery"),
    channel: str = typer.Option(
        None, "--channel", help="Channel for delivery (e.g. 'telegram', 'whatsapp')"
    ),
):
    """Add a scheduled job."""
    from src.cli.cron_cmd import cron_add as _cron_add

    return _cron_add(
        name=name,
        message=message,
        every=every,
        cron_expr=cron_expr,
        tz=tz,
        at=at,
        deliver=deliver,
        to=to,
        channel=channel,
    )


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """Remove a scheduled job."""
    from src.cli.cron_cmd import cron_remove as _cron_remove

    return _cron_remove(job_id=job_id)


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="Job ID"),
    disable: bool = typer.Option(False, "--disable", help="Disable instead of enable"),
):
    """Enable or disable a job."""
    from src.cli.cron_cmd import cron_enable as _cron_enable

    return _cron_enable(job_id=job_id, disable=disable)


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="Job ID to run"),
    force: bool = typer.Option(False, "--force", "-f", help="Run even if disabled"),
):
    """Manually run a job."""
    from src.cli.cron_cmd import cron_run as _cron_run

    return _cron_run(job_id=job_id, force=force)


report_app = typer.Typer(help="Generate activity reports from EventStore")


@report_app.command("daily")
def daily():
    """Generate a daily activity report."""
    from src.cli.report_cmd import daily as _daily

    return _daily()


@report_app.command("weekly")
def weekly():
    """Generate a weekly activity report."""
    from src.cli.report_cmd import weekly as _weekly

    return _weekly()


app.add_typer(gateway_app, name="gateway")
app.add_typer(cron_app, name="cron")
app.add_typer(auth_app, name="auth")
app.add_typer(provider_app, name="provider")
app.add_typer(report_app, name="report")


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from src.cli.channels_cmd import channels_status as _channels_status

    return _channels_status()


@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    from src.cli.channels_cmd import channels_login as _channels_login

    return _channels_login()


# ============================================================================
# Feishu Auth Command
# ============================================================================


@app.command("feishu-auth")
def feishu_auth(
    port: int = typer.Option(9527, help="Local callback server port"),
    reconfigure: bool = typer.Option(
        False, "--reconfigure", "-r", help="Update app_id and app_secret before auth"
    ),
    remote: bool = typer.Option(
        False, "--remote", help="Remote mode: send auth URL via Feishu, wait for code"
    ),
    send_to: str = typer.Option(
        None, "--send-to", help="Feishu open_id to send auth URL to (for --remote)"
    ),
    code: str = typer.Option(
        None, "--code", "-c", help="Exchange an authorization code directly (skip browser)"
    ),
):
    """Authorize Feishu user token (OAuth flow).

    Use --reconfigure / -r to update app credentials.
    Use --remote to send auth URL via Feishu bot (for phone authorization).
    Use --code to exchange a code directly without opening a browser.
    """
    from src.cli.feishu_auth_cmd import feishu_auth as _feishu_auth

    return _feishu_auth(
        port=port,
        reconfigure=reconfigure,
        remote=remote,
        send_to=send_to,
        code=code,
    )


# ============================================================================
# Status Command
# ============================================================================


@app.command()
def status():
    """Show theos status."""
    from src.auth.store import get_api_key_for_provider
    from src.config.loader import get_config_path, load_config
    from src.providers.registry import ordered_providers

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} theos Status\n")

    config_mark = "[green]✓[/green]" if config_path.exists() else "[red]✗[/red]"
    console.print(f"Config: {config_path} {config_mark}")
    ws_mark = "[green]✓[/green]" if workspace.exists() else "[red]✗[/red]"
    console.print(f"Workspace: {workspace} {ws_mark}")

    if config_path.exists():
        console.print(f"Model: {config.agents.defaults.model}")

        # Check API keys from registry (auth profiles take priority over config.json)
        for spec in ordered_providers():
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_oauth:
                console.print(f"{spec.label}: [green]\u2713 (OAuth)[/green]")
            elif spec.is_local:
                if p.api_base:
                    console.print(f"{spec.label}: [green]\u2713 {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                auth_key = get_api_key_for_provider(spec.name)
                config_key = p.api_key if p else None
                if auth_key:
                    console.print(f"{spec.label}: [green]\u2713 (auth profile)[/green]")
                elif config_key:
                    console.print(f"{spec.label}: [green]\u2713[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")

    # Gateway daemon status
    try:
        from src.daemon import resolve_service

        svc = resolve_service()
        if svc.is_loaded():
            st = svc.status()
            pid = st.get("pid")
            if pid:
                console.print(f"Gateway: [green]\u2713 running (PID {pid})[/green]")
            else:
                console.print("Gateway: [yellow]loaded but not running[/yellow]")
        else:
            console.print("Gateway: [dim]not installed[/dim]")
    except NotImplementedError:
        pass  # Omit on unsupported platforms


if __name__ == "__main__":
    app()
