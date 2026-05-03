"""CLI commands for TheOS — routing hub.

Registers all Typer commands. Heavy implementations live in sub-modules:
- agent_cmd.py   — ``theos agent`` interactive loop
- cron_cmd.py    — ``theos cron`` sub-commands
- auth_cmd.py    — ``theos auth`` + ``theos provider`` sub-commands
- gateway_cmd.py — ``theos gateway``
- init_cmd.py    — ``theos init`` wizard
- display.py     — console + display helpers
- repl.py        — terminal / prompt_toolkit helpers
"""

import os
from pathlib import Path

import typer
from rich.table import Table

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

from src.cli.agent_cmd import agent  # noqa: E402
from src.cli.auth_cmd import auth_app, provider_app  # noqa: E402
from src.cli.cron_cmd import cron_app  # noqa: E402
from src.cli.gateway_cmd import gateway_app  # noqa: E402
from src.cli.init_cmd import init  # noqa: E402
from src.cli.report_cmd import report_app  # noqa: E402
from src.cli.ui_cmd import ui as ui_command  # noqa: E402

app.command()(agent)
app.command()(init)
app.add_typer(gateway_app, name="gateway")
app.command(name="ui")(ui_command)
app.add_typer(cron_app, name="cron")
app.add_typer(auth_app, name="auth")
app.add_typer(provider_app, name="provider")
app.add_typer(report_app, name="report")


# ============================================================================
# Channel Commands (kept here — small, tightly coupled to display)
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from src.config.loader import load_config

    config = load_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    # WhatsApp
    wa = config.channels.whatsapp
    table.add_row("WhatsApp", "\u2713" if wa.enabled else "\u2717", wa.bridge_url)

    dc = config.channels.discord
    table.add_row("Discord", "\u2713" if dc.enabled else "\u2717", dc.gateway_url)

    # Feishu
    fs = config.channels.feishu
    fs_config = f"app_id: {fs.app_id[:10]}..." if fs.app_id else "[dim]not configured[/dim]"
    table.add_row("Feishu", "\u2713" if fs.enabled else "\u2717", fs_config)

    # Mochat
    mc = config.channels.mochat
    mc_base = mc.base_url or "[dim]not configured[/dim]"
    table.add_row("Mochat", "\u2713" if mc.enabled else "\u2717", mc_base)

    # Telegram
    tg = config.channels.telegram
    tg_config = f"token: {tg.token[:10]}..." if tg.token else "[dim]not configured[/dim]"
    table.add_row("Telegram", "\u2713" if tg.enabled else "\u2717", tg_config)

    # Slack
    slack = config.channels.slack
    slack_config = "socket" if slack.app_token and slack.bot_token else "[dim]not configured[/dim]"
    table.add_row("Slack", "\u2713" if slack.enabled else "\u2717", slack_config)

    # DingTalk
    dt = config.channels.dingtalk
    dt_config = (
        f"client_id: {dt.client_id[:10]}..." if dt.client_id else "[dim]not configured[/dim]"
    )
    table.add_row("DingTalk", "\u2713" if dt.enabled else "\u2717", dt_config)

    # QQ
    qq = config.channels.qq
    qq_config = f"app_id: {qq.app_id[:10]}..." if qq.app_id else "[dim]not configured[/dim]"
    table.add_row("QQ", "\u2713" if qq.enabled else "\u2717", qq_config)

    # Email
    em = config.channels.email
    em_config = em.imap_host if em.imap_host else "[dim]not configured[/dim]"
    table.add_row("Email", "\u2713" if em.enabled else "\u2717", em_config)

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess

    # User's bridge location
    user_bridge = Path.home() / ".theos" / "bridge"

    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    # Check for npm
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # theos/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall theos-agent")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")

    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]\u2713[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    import subprocess

    from src.config.loader import load_config
    from src.security.secret_refs import resolve_secret_ref

    config = load_config()
    bridge_dir = _get_bridge_dir()

    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")

    env = {**os.environ}
    bridge_token = resolve_secret_ref(config.channels.whatsapp.bridge_token) or ""
    if bridge_token:
        env["BRIDGE_TOKEN"] = bridge_token

    try:
        subprocess.run(["npm", "start"], cwd=bridge_dir, check=True, env=env)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")


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
    from src.config.loader import load_config, save_config
    from src.feishu.token import check_token_valid, init_token

    config = load_config()
    fs = config.channels.feishu

    # --reconfigure: prompt for new credentials
    if reconfigure or not fs.app_id or not fs.app_secret:
        console.print("Configure Feishu app credentials:\n")
        if fs.app_id:
            console.print(f"  Current App ID: [dim]{fs.app_id[:10]}...[/dim]")
        new_id = typer.prompt("  App ID", default=fs.app_id or "", prompt_suffix=" ").strip()
        new_secret = typer.prompt(
            "  App Secret", default=fs.app_secret or "", prompt_suffix=" "
        ).strip()
        if not new_id or not new_secret:
            console.print("[red]App ID and App Secret are required.[/red]")
            raise typer.Exit(1)
        fs.app_id = new_id
        fs.app_secret = new_secret
        fs.enabled = True
        save_config(config)
        console.print("[green]Credentials saved.[/green]\n")

    token_dir = fs.token_dir or "~/.theos/feishu_tokens"

    # --code: direct code exchange (no browser needed)
    if code:
        from src.feishu.remote_auth import exchange_auth_code

        result = exchange_auth_code(
            code=code,
            app_id=fs.app_id,
            app_secret=fs.app_secret,
            token_dir=token_dir,
        )
        if result["ok"]:
            console.print(
                f"[green]✓ Authorized successfully.[/green] "
                f"refresh_token TTL={result['refresh_token_ttl'] / 86400:.1f}d"
            )
        else:
            console.print(f"[red]✗ Authorization failed: {result['error']}[/red]")
            raise typer.Exit(1)
        return

    # --remote: send auth URL via Feishu bot message
    if remote:
        from src.feishu.oauth_callback import register_oauth_state
        from src.feishu.remote_auth import (
            generate_auth_url,
            get_gateway_redirect_uri,
            is_callback_server_alive,
        )

        gateway_uri = get_gateway_redirect_uri()
        auto_exchange = bool(gateway_uri and is_callback_server_alive(gateway_uri))

        # Prefer gateway callback URL (auto-exchange) over localhost
        if auto_exchange and gateway_uri:
            redirect_uri = gateway_uri
            console.print(f"[green]Using gateway callback (auto-exchange): {redirect_uri}[/green]")
        else:
            redirect_uri = f"http://localhost:{port}/callback"
            if gateway_uri:
                console.print(
                    "[yellow]Gateway callback is not reachable from this machine. "
                    "Falling back to manual code exchange.[/yellow]"
                )
        auth_url, state = generate_auth_url(app_id=fs.app_id, redirect_uri=redirect_uri)
        if auto_exchange:
            try:
                register_oauth_state(state, token_dir=token_dir, redirect_uri=redirect_uri)
            except Exception as exc:
                console.print(
                    f"[yellow]Could not register callback state ({exc}). "
                    "Falling back to manual code exchange.[/yellow]"
                )
                auto_exchange = False
                redirect_uri = f"http://localhost:{port}/callback"
                auth_url, state = generate_auth_url(app_id=fs.app_id, redirect_uri=redirect_uri)

        console.print("[cyan]Auth URL generated.[/cyan]\n")
        console.print(f"  {auth_url}\n")

        # Try to send via Feishu bot
        target = send_to or (config.channels.owner_ids[0] if config.channels.owner_ids else None)
        if target:
            try:
                import json as _json

                from lark_oapi.api.im.v1 import (
                    CreateMessageRequest,
                    CreateMessageRequestBody,
                )

                from src.feishu.api import make_client

                client = make_client(fs.app_id, fs.app_secret)
                receive_id_type = "chat_id" if target.startswith("oc_") else "open_id"
                card = {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "title": {"tag": "plain_text", "content": "🔑 飞书授权"},
                        "template": "orange",
                    },
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": (
                                (
                                    "Token 已过期，需要重新授权。\n\n"
                                    "**步骤：**\n"
                                    "1. 点击下方按钮授权\n"
                                    "2. 授权完成后 token 会自动保存\n"
                                    "3. 无需复制 code\n"
                                )
                                if auto_exchange
                                else (
                                    "Token 已过期，需要重新授权。\n\n"
                                    "**步骤：**\n"
                                    "1. 点击下方按钮授权\n"
                                    "2. 授权后页面会跳转（可能显示无法访问）\n"
                                    "3. 复制地址栏中 `code=` 后面的内容\n"
                                    "4. 发给我即可完成授权"
                                )
                            ),
                        },
                        {
                            "tag": "action",
                            "actions": [
                                {
                                    "tag": "button",
                                    "text": {"tag": "plain_text", "content": "🔗 点击授权"},
                                    "type": "primary",
                                    "multi_url": {
                                        "url": auth_url,
                                        "pc_url": auth_url,
                                        "android_url": auth_url,
                                        "ios_url": auth_url,
                                    },
                                }
                            ],
                        },
                    ],
                }
                request = (
                    CreateMessageRequest.builder()
                    .receive_id_type(receive_id_type)
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(target)
                        .msg_type("interactive")
                        .content(_json.dumps(card, ensure_ascii=False))
                        .build()
                    )
                    .build()
                )
                response = client.im.v1.message.create(request)
                if response.success():
                    console.print(f"[green]✓ Auth URL sent to {target}[/green]")
                else:
                    console.print(
                        f"[yellow]Failed to send: code={response.code}, msg={response.msg}[/yellow]"
                    )
                    console.print("Copy the URL above and open it manually.")
            except Exception as e:
                console.print(f"[yellow]Could not send via Feishu: {e}[/yellow]")
                console.print("Copy the URL above and open it manually.")
        else:
            console.print(
                "[yellow]No --send-to or owner_ids configured. Copy the URL above.[/yellow]"
            )

        if auto_exchange:
            console.print(
                "[green]Open the link on any device that can reach your Tailscale/private URL. "
                "After the success page appears, the token is already saved.[/green]"
            )
        else:
            # Wait for code input
            console.print()
            auth_code = typer.prompt("Enter the authorization code", prompt_suffix=" ").strip()
            if not auth_code:
                console.print("[red]No code provided.[/red]")
                raise typer.Exit(1)

            from src.feishu.remote_auth import exchange_auth_code

            result = exchange_auth_code(
                code=auth_code,
                app_id=fs.app_id,
                app_secret=fs.app_secret,
                redirect_uri=redirect_uri,
                token_dir=token_dir,
            )
            if result["ok"]:
                console.print(
                    f"\n[green]✓ Authorized successfully.[/green] "
                    f"refresh_token TTL={result['refresh_token_ttl'] / 86400:.1f}d"
                )
            else:
                console.print(f"\n[red]✗ Authorization failed: {result['error']}[/red]")
                raise typer.Exit(1)
        return

    # Default: local browser flow
    if check_token_valid(token_dir):
        console.print("[green]Current user token is valid.[/green]")
        if not typer.confirm("Re-authorize anyway?", default=False):
            raise typer.Exit()

    console.print(f"Starting OAuth flow on port {port}...")
    console.print(
        f"[yellow]Ensure redirect URI is set to "
        f"http://localhost:{port}/callback in Feishu developer console.[/yellow]\n"
    )
    from src.feishu.remote_auth import FEISHU_OAUTH_SCOPES

    try:
        token = init_token(
            app_id=fs.app_id,
            app_secret=fs.app_secret,
            redirect_uri=f"http://localhost:{port}/callback",
            scope=FEISHU_OAUTH_SCOPES,
            enable_autofill=True,
        )
        console.print(f"\n[green]Authorized successfully. Token: {token[:8]}...[/green]")
    except Exception as e:
        console.print(f"\n[red]Authorization failed: {e}[/red]")
        raise typer.Exit(1)


# ============================================================================
# Status Command
# ============================================================================


@app.command()
def status():
    """Show theos status."""
    from src.auth.store import get_api_key_for_provider
    from src.config.loader import get_config_path, load_config
    from src.providers.registry import PROVIDERS

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
        for spec in PROVIDERS:
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
