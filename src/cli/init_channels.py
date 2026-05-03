"""Channel setup wizard for ``theos init``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from src.cli.display import console
from src.cli.init_providers import INIT_CHANNELS

if TYPE_CHECKING:
    from src.config.schema import Config


def _suggest_feishu_redirect_uri(config: Config) -> tuple[str | None, str | None]:
    """Return (redirect_uri, source_label) for remote Feishu auth suggestions."""
    if config.channels.feishu.oauth_redirect_uri:
        return config.channels.feishu.oauth_redirect_uri, "configured"

    try:
        from src.ui.tailscale import detect_magicdns_name, detect_tailscale_ip

        host = detect_magicdns_name()
        if host:
            return f"http://{host}:{config.gateway.port}/feishu/oauth/callback", "MagicDNS"
        host = detect_tailscale_ip()
        if host:
            return f"http://{host}:{config.gateway.port}/feishu/oauth/callback", "Tailscale IP"
    except Exception:
        pass
    return None, None


def _configure_feishu_remote_auth(config: Config) -> None:
    """Offer remote Feishu OAuth callback setup during init."""
    current = config.channels.feishu.oauth_redirect_uri.strip()
    default_remote = bool(current)
    if not typer.confirm(
        "  Configure remote Feishu authorization (other devices/networks)?",
        default=default_remote,
    ):
        return

    suggested_uri, source = _suggest_feishu_redirect_uri(config)
    redirect_uri = current
    if suggested_uri:
        console.print(f"  [green]\u2713[/green] Detected {source}: [cyan]{suggested_uri}[/cyan]")
        if typer.confirm("  Use this callback URL?", default=True):
            redirect_uri = suggested_uri
        else:
            redirect_uri = typer.prompt(
                "  OAuth redirect URI",
                default=current or suggested_uri,
                prompt_suffix=" ",
            ).strip()
    else:
        console.print(
            "  [yellow]\u26a0[/yellow] No Tailscale/MagicDNS address detected.\n"
            "  Enter a reachable callback URL manually, or leave blank to keep local-only auth."
        )
        redirect_uri = typer.prompt(
            "  OAuth redirect URI",
            default=current,
            show_default=bool(current),
            prompt_suffix=" ",
        ).strip()

    if not redirect_uri:
        console.print("  [dim]Skipped — local/manual Feishu auth remains enabled.[/dim]")
        return

    config.channels.feishu.oauth_redirect_uri = redirect_uri
    if config.gateway.host in {"127.0.0.1", "localhost", "::1"}:
        if typer.confirm(
            "  Gateway is bound to loopback only. Bind it to 0.0.0.0 for remote callback?",
            default=True,
        ):
            config.gateway.host = "0.0.0.0"

    console.print(f"  [green]\u2713[/green] Remote OAuth callback: [cyan]{redirect_uri}[/cyan]")
    console.print("  Register this exact URI in the Feishu developer console.")


def configure_channels(config: Config) -> None:
    """Interactive channel configuration wizard."""
    console.print("\n[bold]Channel setup[/bold]\n")

    channel_status: list[tuple[str, str, bool]] = []
    for label, key in INIT_CHANNELS:
        ch = getattr(config.channels, key)
        is_cfg = ch.enabled or bool(
            getattr(ch, "token", None)
            or getattr(ch, "bot_token", None)
            or getattr(ch, "app_id", None)
            or getattr(ch, "client_id", None)
            or (getattr(ch, "bridge_url", None) and getattr(ch, "bridge_token", None))
            or getattr(ch, "imap_host", None)
        )
        channel_status.append((label, key, is_cfg))

    ch_default_indices: list[int] = []
    for i, (label, _key, is_cfg) in enumerate(channel_status, 1):
        mark = " [green]\u2713 configured[/green]" if is_cfg else ""
        console.print(f"  [{i}] {label}{mark}")
        if is_cfg:
            ch_default_indices.append(i)

    ch_default_str = ",".join(str(i) for i in ch_default_indices) if ch_default_indices else ""
    console.print()
    ch_raw = typer.prompt(
        "  Select channels (comma-separated, Enter to keep)",
        default=ch_default_str,
        show_default=True,
        prompt_suffix=" ",
    ).strip()

    ch_selected: set[int] = set()
    if ch_raw:
        for part in ch_raw.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part)
                if 1 <= idx <= len(channel_status):
                    ch_selected.add(idx)

    for idx in sorted(ch_selected):
        label, key, was_configured = channel_status[idx - 1]
        if was_configured:
            continue

        console.print(f"\n  [bold]{label}[/bold]")

        if key == "whatsapp":
            bridge_url = typer.prompt(
                "  Bridge URL", default="ws://localhost:3001", prompt_suffix=" "
            ).strip()
            bridge_token = typer.prompt(
                "  Bridge token (optional)", default="", show_default=False, prompt_suffix=" "
            ).strip()
            config.channels.whatsapp.bridge_url = bridge_url
            if bridge_token:
                config.channels.whatsapp.bridge_token = bridge_token
            config.channels.whatsapp.enabled = True
            console.print(f"  [green]\u2713[/green] {label} configured")

        elif key == "telegram":
            token = typer.prompt("  Bot token (from @BotFather)", prompt_suffix=" ").strip()
            if token:
                config.channels.telegram.token = token
                config.channels.telegram.enabled = True
            ids_raw = typer.prompt(
                "  Allowed user IDs (comma-separated, blank for none)",
                default="",
                show_default=False,
                prompt_suffix=" ",
            ).strip()
            if ids_raw:
                config.channels.telegram.allow_from = [
                    x.strip() for x in ids_raw.split(",") if x.strip()
                ]
            console.print(f"  [green]\u2713[/green] {label} configured")

        elif key == "discord":
            token = typer.prompt(
                "  Bot token (from Discord Developer Portal)", prompt_suffix=" "
            ).strip()
            if token:
                config.channels.discord.token = token
                config.channels.discord.enabled = True
            ids_raw = typer.prompt(
                "  Allowed user IDs (comma-separated, blank for none)",
                default="",
                show_default=False,
                prompt_suffix=" ",
            ).strip()
            if ids_raw:
                config.channels.discord.allow_from = [
                    x.strip() for x in ids_raw.split(",") if x.strip()
                ]
            console.print(f"  [green]\u2713[/green] {label} configured")

        elif key == "slack":
            bot_token = typer.prompt("  Bot token (xoxb-...)", prompt_suffix=" ").strip()
            app_token = typer.prompt("  App token (xapp-...)", prompt_suffix=" ").strip()
            if bot_token and app_token:
                config.channels.slack.bot_token = bot_token
                config.channels.slack.app_token = app_token
                config.channels.slack.enabled = True
                console.print(f"  [green]\u2713[/green] {label} configured")
            else:
                console.print("[yellow]  Skipped \u2014 both tokens required.[/yellow]")

        elif key == "feishu":
            app_id = typer.prompt("  App ID", prompt_suffix=" ").strip()
            app_secret = typer.prompt("  App Secret", prompt_suffix=" ").strip()
            if app_id and app_secret:
                config.channels.feishu.app_id = app_id
                config.channels.feishu.app_secret = app_secret
                config.channels.feishu.enabled = True
                _configure_feishu_remote_auth(config)
                console.print(f"  [green]\u2713[/green] {label} configured")
            else:
                console.print("[yellow]  Skipped \u2014 App ID and App Secret required.[/yellow]")

        elif key == "email":
            if not typer.confirm("  Grant consent to access mailbox data?", default=False):
                console.print("  [dim]Skipped \u2014 consent required.[/dim]")
                continue
            config.channels.email.consent_granted = True
            imap_host = typer.prompt("  IMAP host", prompt_suffix=" ").strip()
            imap_user = typer.prompt("  IMAP username", prompt_suffix=" ").strip()
            imap_pass = typer.prompt("  IMAP password", prompt_suffix=" ").strip()
            smtp_host = typer.prompt("  SMTP host", prompt_suffix=" ").strip()
            smtp_user = typer.prompt("  SMTP username", prompt_suffix=" ").strip()
            smtp_pass = typer.prompt("  SMTP password", prompt_suffix=" ").strip()
            from_addr = typer.prompt("  From address", prompt_suffix=" ").strip()
            if imap_host and smtp_host:
                config.channels.email.imap_host = imap_host
                config.channels.email.imap_username = imap_user
                config.channels.email.imap_password = imap_pass
                config.channels.email.smtp_host = smtp_host
                config.channels.email.smtp_username = smtp_user
                config.channels.email.smtp_password = smtp_pass
                config.channels.email.from_address = from_addr
                config.channels.email.enabled = True
                console.print(f"  [green]\u2713[/green] {label} configured")
            else:
                console.print("[yellow]  Skipped \u2014 IMAP and SMTP hosts required.[/yellow]")
