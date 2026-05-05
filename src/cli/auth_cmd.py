"""CLI auth sub-commands and OAuth login handlers."""

from __future__ import annotations

import typer

from src.cli.display import console

auth_app = typer.Typer(help="Manage provider authentication.")

# ---------------------------------------------------------------------------
# Auth profile commands
# ---------------------------------------------------------------------------


@auth_app.command("list")
def auth_list():
    """List all saved auth profiles."""
    from rich.table import Table

    from src.auth.store import load_auth_store
    from src.providers.registry import oauth_providers

    store = load_auth_store()

    table = Table(title="Auth Profiles")
    table.add_column("Profile ID", style="cyan")
    table.add_column("Provider")
    table.add_column("Type")
    table.add_column("Email")
    table.add_column("Default", justify="center")

    for pid, cred in store.profiles.items():
        is_default = store.last_good.get(cred.provider) == pid
        table.add_row(
            pid,
            cred.provider,
            cred.type,
            cred.email or "",
            "[green]\u2713[/green]" if is_default else "",
        )

    # Append OAuth providers with detected tokens
    from src.cli.auth_oauth_cmd import detect_oauth_token

    for spec in oauth_providers():
        token_status = detect_oauth_token(spec.name)
        if token_status:
            table.add_row(
                spec.name.replace("_", "-"),
                spec.display_name,
                "oauth",
                token_status,
                "[green]\u2713[/green]",
            )

    if not table.rows:
        console.print("[dim]No auth profiles saved.[/dim]")
        console.print(
            "Add one: [cyan]theos auth add --provider anthropic --key sk-ant-...[/cyan]"
        )
        return

    console.print(table)


@auth_app.command("add")
def auth_add(
    provider: str = typer.Option(
        ..., "--provider", "-p", help="Provider name (e.g. anthropic, openai)"
    ),
    key: str = typer.Option(..., "--key", "-k", help="API key"),
    name: str = typer.Option("default", "--name", "-n", help="Profile name (default: 'default')"),
    email: str = typer.Option(None, "--email", "-e", help="Optional email for this account"),
):
    """Save an API key as a named auth profile."""
    from src.auth.store import add_api_key_profile

    profile_id = add_api_key_profile(provider, key, name=name, email=email or None)
    console.print(f"[green]\u2713[/green] Saved profile [cyan]{profile_id}[/cyan]")


@auth_app.command("remove")
def auth_remove(
    profile_id: str = typer.Argument(..., help="Profile ID to remove (e.g. anthropic:default)"),
):
    """Remove an auth profile."""
    from src.auth.store import remove_profile

    if remove_profile(profile_id):
        console.print(f"[green]\u2713[/green] Removed profile [cyan]{profile_id}[/cyan]")
    else:
        console.print(f"[red]Profile not found: {profile_id}[/red]")
        raise typer.Exit(1)


@auth_app.command("use")
def auth_use(
    profile_id: str = typer.Argument(
        ..., help="Profile ID to set as default (e.g. anthropic:work)"
    ),
):
    """Set an auth profile as the default for its provider."""
    from src.auth.store import set_default_profile

    if set_default_profile(profile_id):
        console.print(f"[green]\u2713[/green] Set [cyan]{profile_id}[/cyan] as default")
    else:
        console.print(f"[red]Profile not found: {profile_id}[/red]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# OAuth-aware status / refresh / login / revoke
# ---------------------------------------------------------------------------


@auth_app.command("status")
def auth_status():
    """Show authentication status for all providers."""
    import time as _time

    from src.auth.store import load_auth_store
    from src.auth.types import ApiKeyCredential, OAuthCredential, TokenCredential

    store = load_auth_store()
    if not store.profiles:
        console.print("[dim]No auth profiles configured. Run `theos init`.[/dim]")
        return

    console.print("\n[bold]Auth Profiles[/bold]\n")
    for pid, cred in sorted(store.profiles.items()):
        if isinstance(cred, OAuthCredential):
            remaining = (cred.expires / 1000) - _time.time()
            if remaining > 0:
                hours = remaining / 3600
                status = f"[green]valid[/green] (expires in {hours:.1f}h)"
            else:
                status = "[red]expired[/red]"
            console.print(f"  {pid}: [cyan]oauth[/cyan] {status}")
        elif isinstance(cred, ApiKeyCredential):
            masked = f"{cred.key[:12]}..." if cred.key else "(empty)"
            console.print(f"  {pid}: [cyan]api_key[/cyan] [green]valid[/green] {masked}")
        elif isinstance(cred, TokenCredential):
            console.print(f"  {pid}: [cyan]token[/cyan]")
    console.print()


@auth_app.command("refresh")
def auth_refresh(provider: str):
    """Manually refresh OAuth token for a provider."""
    from src.cli.auth_oauth_cmd import auth_refresh as _auth_refresh

    return _auth_refresh(provider)


@auth_app.command("login")
def auth_login(provider: str):
    """Re-run OAuth authorization for a provider."""
    from src.cli.auth_oauth_cmd import auth_login as _auth_login

    return _auth_login(provider)


@auth_app.command("revoke")
def auth_revoke(provider: str):
    """Remove credentials for a provider."""
    from src.cli.auth_oauth_cmd import auth_revoke as _auth_revoke

    return _auth_revoke(provider)


# ---------------------------------------------------------------------------
# OAuth provider login
# ---------------------------------------------------------------------------

provider_app = typer.Typer(help="Manage providers")


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(
        ..., help="OAuth provider (e.g. 'openai-codex', 'github-copilot')"
    ),
):
    """Authenticate with an OAuth provider."""
    from src.cli.auth_oauth_cmd import provider_login as _provider_login

    return _provider_login(provider)
