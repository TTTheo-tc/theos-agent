"""Gateway command — ``theos gateway``.

Managed as a launchd/systemd service via the ``src.daemon`` package.
"""

import asyncio
import time
from pathlib import Path

import typer
from loguru import logger

from src import __logo__
from src.channels.registry import CHANNELS
from src.cli.display import console
from src.memory.rule_cleanup import run_structured_rule_cleanup_event
from src.utils.helpers import sync_workspace_templates

gateway_app = typer.Typer(
    name="gateway",
    help="Gateway daemon management",
    invoke_without_command=True,
)


def _print_log_line(line: str) -> None:
    """Parse a JSONL log line and print formatted output."""
    import json

    if not line:
        return
    try:
        record = json.loads(line)
        text = record.get("text", "")
        if text:
            # loguru serialize=True puts the formatted text in "text"
            print(text.rstrip())
            return
        # Fallback: extract fields manually
        ts = record.get("record", {}).get("time", {}).get("repr", "")
        level = record.get("record", {}).get("level", {}).get("name", "INFO")
        msg = record.get("record", {}).get("message", line)
        print(f"{ts} | {level:<8} | {msg}")
    except (json.JSONDecodeError, KeyError):
        print(line)


def _resolve_service():
    """Import and return the platform service (lazy to avoid import errors on unsupported OS)."""
    from src.daemon import resolve_service

    return resolve_service()


@gateway_app.command("stop")
def gateway_stop():
    """Stop the gateway daemon."""
    svc = _resolve_service()
    if not svc.is_loaded():
        console.print("[yellow]Gateway daemon is not installed.[/yellow]")
        return
    svc.stop()
    console.print("[green]\u2713[/green] Gateway daemon stopped.")


@gateway_app.command("restart")
def gateway_restart_cmd():
    """Restart the gateway daemon."""
    svc = _resolve_service()
    if not svc.is_loaded():
        console.print("[yellow]Gateway daemon is not installed.[/yellow]")
        return
    try:
        svc.restart()
    except Exception as exc:
        console.print(f"[red]Gateway daemon restart failed: {exc}[/red]")
        raise typer.Exit(1)
    console.print("[green]\u2713[/green] Gateway daemon restarting.")


@gateway_app.command("uninstall")
def gateway_uninstall():
    """Stop and remove the gateway daemon service."""
    svc = _resolve_service()
    if not svc.is_loaded():
        console.print("[yellow]Gateway daemon is not installed.[/yellow]")
        return
    svc.uninstall()
    console.print("[green]\u2713[/green] Gateway daemon uninstalled.")


@gateway_app.command("logs")
def gateway_logs(
    source: str = typer.Option("app", help="Log source: app, supervisor-stdout, supervisor-stderr"),
    raw: bool = typer.Option(False, "--raw", help="Output raw JSONL"),
):
    """Tail gateway logs."""
    import subprocess as _sp

    from src.config.loader import load_config

    config = load_config()

    if source == "app":
        log_file = config.workspace_path / "logs" / "gateway.log"
    elif source == "supervisor-stdout":
        log_file = Path.home() / ".theos" / "logs" / "gateway-stdout.log"
    elif source == "supervisor-stderr":
        log_file = Path.home() / ".theos" / "logs" / "gateway-stderr.log"
    else:
        console.print(f"[red]Unknown source: {source}[/red]")
        raise typer.Exit(1)

    if not log_file.exists():
        console.print(f"[yellow]Log file not found: {log_file}[/yellow]")
        raise typer.Exit(1)

    if raw or source != "app":
        # Raw JSONL or supervisor logs: just tail
        _sp.run(["tail", "-f", "-n", "50", str(log_file)])
    else:
        # Default: format JSONL app log into human-readable output
        # Print last 50 lines formatted, then follow
        try:
            lines = log_file.read_text().splitlines()[-50:]
        except Exception:
            lines = []
        for line in lines:
            _print_log_line(line)
        # Follow new lines
        with open(log_file) as f:
            f.seek(0, 2)  # seek to end
            while True:
                line = f.readline()
                if line:
                    _print_log_line(line.rstrip())
                else:
                    import time

                    time.sleep(0.3)


def _run_startup_checks(config) -> None:
    """P4: Run environment self-checks at gateway startup.

    Checks critical dependencies, skill integrity, and API key configuration.
    Logs warnings for missing items but does not block startup.
    """
    checks_passed = 0
    checks_warned = 0

    # 1. Check critical Python dependencies
    critical_deps = {
        "json_repair": "JSON parsing for tool calls",
    }
    if getattr(config.channels.feishu, "enabled", False):
        critical_deps["lark_oapi"] = "Feishu/Lark API integration"
    optional_deps = {
        "playwright": "Browser automation (browser tool)",
    }
    for mod, desc in critical_deps.items():
        try:
            __import__(mod)
            checks_passed += 1
        except ImportError:
            logger.warning("Startup check: missing critical dependency '{}' ({})", mod, desc)
            checks_warned += 1
    for mod, desc in optional_deps.items():
        try:
            __import__(mod)
            checks_passed += 1
        except ImportError:
            logger.warning("Startup check: optional dependency '{}' not installed ({})", mod, desc)
            checks_warned += 1

    # 2. Check Feishu client importability only when that channel is enabled.
    if getattr(config.channels.feishu, "enabled", False):
        try:
            from src.feishu.client import FeishuClient  # noqa: F401

            checks_passed += 1
        except ImportError as e:
            logger.warning("Startup check: cannot import FeishuClient: {}", e)
            checks_warned += 1

    # 3. Check skill directory integrity (both workspace and builtin)
    from src.agent.skills import BUILTIN_SKILLS_DIR

    skill_dirs_to_check = []
    # Builtin skills (the primary skill source)
    if BUILTIN_SKILLS_DIR.exists():
        skill_dirs_to_check.append(("builtin", BUILTIN_SKILLS_DIR))
    # Workspace skills (user-installed)
    ws_skills = config.workspace_path / "skills" if hasattr(config, "workspace_path") else None
    if ws_skills and ws_skills.exists():
        skill_dirs_to_check.append(("workspace", ws_skills))

    for label, skills_dir in skill_dirs_to_check:
        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir() and not skill_dir.name.startswith("."):
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    logger.warning(
                        "Startup check: {} skill '{}' missing SKILL.md",
                        label,
                        skill_dir.name,
                    )
                    checks_warned += 1
                else:
                    checks_passed += 1

    # 4. Check API key configuration
    from src.security.secret_refs import resolve_secret_ref as _resolve

    api_checks = {
        "LLM provider": bool(config.get_provider_keys()),
    }
    # Check web search — DDG is always available, Brave/Tavily optional
    web_provider = config.tools.web.search.provider
    if web_provider in ("brave", "tavily"):
        try:
            web_key = _resolve(config.tools.web.search.api_key) or _resolve(
                config.tools.web.search.tavily_api_key
            )
            api_checks[f"Web search ({web_provider})"] = bool(web_key)
        except Exception:
            api_checks[f"Web search ({web_provider})"] = False
    else:
        api_checks["Web search (DuckDuckGo)"] = True

    # Check Feishu credentials only when that channel is enabled.
    try:
        fs_cfg = config.channels.feishu
        if fs_cfg.enabled:
            api_checks["Feishu"] = bool(fs_cfg.app_id and fs_cfg.app_secret)
    except Exception:
        pass

    for name, ok in api_checks.items():
        if ok:
            checks_passed += 1
        else:
            logger.warning("Startup check: {} API key not configured", name)
            checks_warned += 1

    if checks_warned:
        logger.info("Startup checks: {} passed, {} warnings", checks_passed, checks_warned)
    else:
        logger.info("Startup checks: all {} checks passed", checks_passed)


def _warn_missing_owner_ids(config) -> None:
    """Warn when channels are enabled but no owner_ids are configured."""
    ch = config.channels
    enabled_channels = [
        spec.name
        for spec in CHANNELS
        if getattr(getattr(ch, spec.config_attr, None), "enabled", False)
    ]
    if enabled_channels and not ch.owner_ids:
        logger.warning(
            "Channels enabled ({}) but channels.owner_ids is empty — "
            "no channel user will be able to invoke owner-only tools "
            "(message, agent, cron). "
            "Note: allow_from / group allowlists do NOT grant owner privileges. "
            "CLI remains owner by default.",
            ", ".join(enabled_channels),
        )


@gateway_app.callback(invoke_without_command=True)
def gateway(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start the theos gateway (foreground)."""
    if ctx.invoked_subcommand is not None:
        return
    from src.agent.loop import AgentLoop
    from src.bus.queue import MessageBus
    from src.channels.manager import ChannelManager
    from src.config.loader import get_config_path, get_data_dir, load_config
    from src.cron.heartbeat import HeartbeatService
    from src.cron.service import CronService
    from src.cron.types import CronJob
    from src.session.manager import SessionManager

    if verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    _t_start = time.monotonic()

    def _phase(label: str) -> None:
        elapsed = time.monotonic() - _t_start
        logger.info("Startup | {:<30s} {:.1f}s", label, elapsed)

    console.print(f"{__logo__} Starting theos gateway...")

    config = load_config()
    _phase("config loaded")

    # Structured log file for dashboard viewer
    log_dir = config.workspace_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(log_dir / "gateway.log"),
        rotation="10 MB",
        retention="7 days",
        level="DEBUG",
        serialize=True,
    )

    _run_startup_checks(config)
    _warn_missing_owner_ids(config)
    sync_workspace_templates(config.workspace_path)
    bus = MessageBus()

    # Dashboard writer
    from src.store.dashboard_writer import DashboardWriter

    dashboard_db_path = config.workspace_path / "data" / "dashboard.db"
    dashboard = DashboardWriter(dashboard_db_path)
    from src.providers.factory import make_provider

    try:
        provider = make_provider(config)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    _phase("provider created")
    session_manager = SessionManager(config.workspace_path, config=config)

    # Create cron service first (callback set after agent creation)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    # Create agent with cron service
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        config=config,
        cron_service=cron,
        session_manager=session_manager,
        dashboard=dashboard,
    )
    _phase("agent created")

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        if job.payload.kind == "system_event":
            if job.payload.message == "feishu_token_refresh":
                from src.feishu.token_refresh import handle_token_refresh_event

                return handle_token_refresh_event(config, bus=bus)
            return run_structured_rule_cleanup_event(config.workspace_path, job.payload.message)
        response = await agent.process_direct(
            job.payload.message,
            session_key=f"cron:{job.id}",
            channel=job.payload.channel or "cli",
            chat_id=job.payload.to or "direct",
        )
        if job.payload.deliver and job.payload.to:
            from src.bus.events import OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    channel=job.payload.channel or "cli",
                    chat_id=job.payload.to,
                    content=response or "",
                )
            )
        return response

    cron.on_job = on_cron_job

    if config.knowledge_graph.enabled:
        from src.memory.rule_cleanup import ensure_structured_rule_cleanup_job

        ensure_structured_rule_cleanup_job(cron)

    # Register Feishu token auto-refresh (every 6 hours)
    if config.channels.feishu.enabled:
        from src.feishu.token_refresh import ensure_token_refresh_job

        ensure_token_refresh_job(cron)

    # Create channel manager
    channels = ChannelManager(config, bus, dashboard=dashboard)
    _phase("channels created")

    def _pick_heartbeat_target() -> tuple[str, str]:
        """Pick a routable channel/chat target for heartbeat-triggered messages."""
        enabled = set(channels.enabled_channels)
        # Prefer the most recently updated non-internal session on an enabled channel.
        for item in session_manager.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system"}:
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        # Fallback keeps prior behavior but remains explicit.
        return "cli", "direct"

    # Create heartbeat service
    async def on_heartbeat_execute(tasks: str) -> str:
        """Phase 2: execute heartbeat tasks through the full agent loop."""
        channel, chat_id = _pick_heartbeat_target()

        async def _silent(*_args, **_kwargs):
            pass

        return await agent.process_direct(
            tasks,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
        )

    async def on_heartbeat_notify(response: str) -> None:
        """Deliver a heartbeat response to the user's channel."""
        from src.bus.events import OutboundMessage

        channel, chat_id = _pick_heartbeat_target()
        if channel == "cli":
            return  # No external channel available to deliver to
        await bus.publish_outbound(
            OutboundMessage(channel=channel, chat_id=chat_id, content=response)
        )

    hb_cfg = config.gateway.heartbeat
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        provider=provider,
        model=agent.model,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
    )

    # --- Poller service (high-frequency monitors, zero token cost) ---
    from src.poller.service import PollerService

    poller_service = PollerService(bus=bus)

    # Register X/Twitter poller if configured
    x_cfg = config.gateway.pollers.x
    if x_cfg.enabled and x_cfg.usernames:
        from src.poller.x_poller import XPoller

        x_poller = XPoller(
            usernames=x_cfg.usernames,
            interval_s=x_cfg.interval_s,
            cookies={"auth_token": x_cfg.auth_token, "ct0": x_cfg.ct0},
            notify_channel=x_cfg.notify_channel,
            notify_chat_id=x_cfg.notify_chat_id
            or (config.channels.owner_ids[0] if config.channels.owner_ids else ""),
        )
        poller_service.register(x_poller)
        console.print(
            f"[green]\u2713[/green] X Poller: monitoring {', '.join(x_cfg.usernames)} "
            f"(every {x_cfg.interval_s}s)"
        )

    if channels.enabled_channels:
        console.print(
            f"[green]\u2713[/green] Channels enabled: {', '.join(channels.enabled_channels)}"
        )
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]\u2713[/green] Cron: {cron_status['jobs']} scheduled jobs")

    console.print(f"[green]\u2713[/green] Heartbeat: every {hb_cfg.interval_s}s")

    # --- OAuth callback server (方案 1: phone authorization) ---
    oauth_runner = None
    fs_cfg = config.channels.feishu
    if fs_cfg.enabled and fs_cfg.app_id and fs_cfg.app_secret:
        from src.feishu.oauth_callback import create_oauth_app

        gw = config.gateway
        # Determine the redirect_uri: prefer explicit config, else derive from gateway
        redirect_uri = fs_cfg.oauth_redirect_uri
        if not redirect_uri:
            from src.feishu.oauth_callback import build_callback_url

            redirect_uri = build_callback_url(gw.host, gw.port)

        # Pick a notify target (first owner_id)
        notify_target = config.channels.owner_ids[0] if config.channels.owner_ids else None

        oauth_app = create_oauth_app(
            app_id=fs_cfg.app_id,
            app_secret=fs_cfg.app_secret,
            token_dir=fs_cfg.token_dir or "~/.theos/feishu_tokens",
            redirect_uri=redirect_uri,
            bus=bus,
            notify_chat_id=notify_target,
        )
        console.print(f"[green]\u2713[/green] OAuth callback: {redirect_uri}")

    # --- UI dashboard server ---
    ui_runner = None
    _log_sink_id = None
    ui_cfg = config.gateway.ui
    if ui_cfg.enabled:
        from src.ui.events import UIEventBus
        from src.ui.log_events import LogEventBus
        from src.ui.server import create_ui_app, start_ui_server

        ui_event_bus = UIEventBus()
        log_event_bus = LogEventBus()
        ui_app = create_ui_app(
            db_path=dashboard_db_path,
            event_bus=ui_event_bus,
            app_context={
                "workspace": config.workspace_path,
                "cron_service": cron,
                "tool_registry": agent.tools,
                "config": config,
                "config_path": get_config_path(),
                "cron_store_path": cron_store_path,
            },
        )
        ui_app.state.log_event_bus = log_event_bus

    _phase("setup complete")

    # --- Graceful restart via SIGHUP / post-send restart marker ---
    _restart_requested = False

    async def run() -> bool:
        nonlocal oauth_runner, ui_runner, _restart_requested

        import signal

        loop = asyncio.get_running_loop()
        agent_task: asyncio.Task | None = None
        channels_task: asyncio.Task | None = None
        restart_wait_task: asyncio.Task | None = None
        cleaned_up = False
        restart_event = asyncio.Event()

        def _request_restart() -> None:
            nonlocal _restart_requested
            if _restart_requested:
                return
            _restart_requested = True
            logger.info("Gateway restart requested")
            restart_event.set()

        async def _shutdown(*, wait_outbound: bool) -> None:
            nonlocal cleaned_up
            if cleaned_up:
                return
            cleaned_up = True

            channels.set_restart_callback(None)
            if wait_outbound:
                channels.pause_inbound()
            await poller_service.stop()
            heartbeat.stop()
            cron.stop()
            agent.stop()
            if wait_outbound:
                await channels.wait_outbound_idle(timeout=5.0)
            await channels.stop_all()
            if agent_task is not None or channels_task is not None:
                await asyncio.gather(
                    *(task for task in (agent_task, channels_task) if task is not None),
                    return_exceptions=True,
                )
            await agent.close()
            await dashboard.close()
            if oauth_runner:
                await oauth_runner.cleanup()
            if ui_runner:
                if _log_sink_id is not None:
                    logger.remove(_log_sink_id)
                await ui_runner.cleanup()

        def _on_sighup() -> None:
            logger.info("SIGHUP received — scheduling graceful restart")
            _request_restart()

        try:
            loop.add_signal_handler(signal.SIGHUP, _on_sighup)
        except (NotImplementedError, OSError):
            pass  # Windows or restricted environment

        channels.set_restart_callback(_request_restart)

        try:
            await dashboard.connect()
            await cron.start()
            await heartbeat.start()
            await poller_service.start()

            # Start OAuth callback server if configured
            if fs_cfg.enabled and fs_cfg.app_id and fs_cfg.app_secret:
                from src.feishu.oauth_callback import start_oauth_server

                gw = config.gateway
                try:
                    oauth_runner = await start_oauth_server(oauth_app, host=gw.host, port=gw.port)
                except OSError as exc:
                    logger.warning(
                        "OAuth callback server failed to start on {}:{} — {}"
                        " (Feishu in-chat re-auth will be unavailable)",
                        gw.host,
                        gw.port,
                        exc,
                    )

            if ui_cfg.enabled:
                try:
                    if ui_cfg.host not in {"127.0.0.1", "localhost", "::1"}:
                        logger.warning(
                            "Dashboard UI is listening on {}:{} without built-in auth. "
                            "Prefer 127.0.0.1 or put it behind an authenticated proxy.",
                            ui_cfg.host,
                            ui_cfg.port,
                        )
                    ui_runner = await start_ui_server(ui_app, host=ui_cfg.host, port=ui_cfg.port)
                    dashboard.set_event_callback(ui_event_bus.publish)
                    from src.ui.tailscale import build_ui_url

                    ui_url = build_ui_url(ui_cfg.port, host=ui_cfg.host)
                    console.print(f"[green]\u2713[/green] Dashboard: {ui_url}")

                    # Push log lines to LogEventBus for SSE streaming
                    import asyncio as _aio

                    def _log_sink(message):
                        record = message.record
                        entry = {
                            "level": record["level"].name,
                            "message": record["message"],
                            "timestamp": str(record["time"]),
                            "logger": record["name"] or "",
                        }
                        try:
                            loop = _aio.get_running_loop()
                            loop.create_task(log_event_bus.publish(entry))
                        except RuntimeError:
                            pass

                    _log_sink_id = logger.add(_log_sink, level="INFO")
                except OSError as exc:
                    logger.warning(
                        "UI server failed to start on {}:{} — {}",
                        ui_cfg.host,
                        ui_cfg.port,
                        exc,
                    )

            agent_task = asyncio.create_task(agent.run(), name="agent.run")
            channels_task = asyncio.create_task(channels.start_all(), name="channels.start_all")

            # Send "restart complete" notification if this is a post-restart boot.
            from src.agent.tools.gateway_restart import send_restart_notification

            asyncio.create_task(send_restart_notification(bus))

            restart_wait_task = asyncio.create_task(
                restart_event.wait(), name="gateway.restart_wait"
            )

            done, pending = await asyncio.wait(
                {agent_task, channels_task, restart_wait_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if restart_wait_task in done:
                logger.info("Restart event observed — entering quiesce/shutdown")
                await _shutdown(wait_outbound=True)
                return True

            for task in done:
                if task is restart_wait_task:
                    continue
                if task.cancelled():
                    logger.warning("Gateway runtime task cancelled unexpectedly")
                    continue
                exc = task.exception()
                if exc is not None:
                    logger.opt(exception=exc).warning("Gateway runtime task failed")
                else:
                    logger.warning("Gateway runtime task exited unexpectedly")
            await _shutdown(wait_outbound=False)
            return False
        except KeyboardInterrupt:
            console.print("\nShutting down...")
            await _shutdown(wait_outbound=False)
            return False
        finally:
            channels.set_restart_callback(None)
            if restart_wait_task is not None and not restart_wait_task.done():
                restart_wait_task.cancel()
                try:
                    await restart_wait_task
                except asyncio.CancelledError:
                    pass

    should_restart = asyncio.run(run())

    # After event loop exits: if SIGHUP triggered restart, exec a fresh process
    if should_restart and _restart_requested:
        import os
        import sys

        logger.info("Restarting gateway (exec)...")
        console.print("[yellow]♻ Restarting...[/yellow]")
        os.execv(sys.executable, [sys.executable, *sys.argv])
