"""OAuth-backed auth and provider CLI implementations."""

from __future__ import annotations

from pathlib import Path

import typer

from src.cli.display import console


def detect_oauth_token(provider_name: str) -> str | None:
    """Return a display string if an OAuth token is detected, else None."""
    if provider_name == "openai_codex":
        # Check oauth_cli_kit token store or legacy ~/.codex/auth.json
        try:
            from oauth_cli_kit import get_token

            token = get_token()
            if token and token.access:
                return token.account_id or "authenticated"
        except Exception:
            pass
        codex_path = Path.home() / ".codex" / "auth.json"
        if codex_path.exists():
            return str(codex_path)
        return None
    if provider_name == "github_copilot":
        # Check theos auth store first
        try:
            from src.auth.store import load_auth_store
            from src.auth.types import OAuthCredential

            store = load_auth_store()
            for _pid, cred in store.profiles.items():
                if isinstance(cred, OAuthCredential) and cred.provider == "github_copilot":
                    return "authenticated"
        except Exception:
            pass
        # Fallback: check known external credential locations
        copilot_hosts = Path.home() / ".config" / "github-copilot" / "hosts.json"
        litellm_token = Path.home() / ".config" / "litellm" / "github_copilot" / "access-token"
        if copilot_hosts.exists() or litellm_token.exists():
            return "device flow"
        return None
    return None


def auth_refresh(provider: str):
    """Manually refresh OAuth token for a provider."""
    if provider == "anthropic":
        console.print(
            "[red]Anthropic OAuth is disabled in TheOS.[/red] "
            "Use [cyan]theos auth add --provider anthropic --key <key>[/cyan] instead."
        )
        raise typer.Exit(1)

    from src.auth.oauth_manager import OAuthManager
    from src.auth.plugins import register_builtin_plugins
    from src.auth.store import load_auth_store

    store = load_auth_store()
    profile_id = store.last_good.get(provider, f"{provider}:default")

    plugins = register_builtin_plugins()
    mgr = OAuthManager(plugins=plugins, store_path=Path.home() / ".theos" / "auth-profiles.enc")
    result = mgr.resolve(provider, profile_id)
    if result:
        console.print(f"[green]\u2713[/green] Token refreshed for {provider}")
    else:
        console.print(f"[red]\u2717[/red] Refresh failed for {provider}")


def auth_login(provider: str):
    """Re-run OAuth authorization for a provider."""
    if provider == "anthropic":
        console.print(
            "[red]Anthropic OAuth is disabled in TheOS.[/red] "
            "Use [cyan]theos auth add --provider anthropic --key <key>[/cyan] instead."
        )
        raise typer.Exit(1)

    from src.auth.plugins import register_builtin_plugins
    from src.auth.store import add_oauth_profile

    plugins = register_builtin_plugins()
    plugin = plugins.get(provider)
    if not plugin:
        console.print(f"[red]No OAuth plugin for {provider}[/red]")
        raise typer.Exit(1)

    cred = plugin.read_external_credentials()
    if not cred:
        cred = plugin.login(redirect_uri="http://localhost:9527/callback")
    if cred:
        pid = add_oauth_profile(
            provider=cred.provider,
            access=cred.access,
            refresh=cred.refresh,
            expires=cred.expires,
            email=cred.email,
            scope=cred.scope,
            account_id=cred.account_id,
        )
        console.print(f"[green]\u2713[/green] Saved as {pid}")
    else:
        console.print(f"[red]\u2717[/red] Login failed for {provider}")
        raise typer.Exit(1)


def auth_revoke(provider: str):
    """Remove credentials for a provider."""
    from src.auth.store import load_auth_store, remove_profile

    store = load_auth_store()
    profile_id = store.last_good.get(provider, f"{provider}:default")
    if remove_profile(profile_id):
        console.print(f"[green]\u2713[/green] Removed {profile_id}")
    else:
        console.print(f"[yellow]Profile {profile_id} not found[/yellow]")


def provider_login(
    provider: str = typer.Argument(
        ..., help="OAuth provider (e.g. 'openai-codex', 'github-copilot')"
    ),
):
    """Authenticate with an OAuth provider."""
    from src import __logo__
    from src.providers.registry import oauth_providers

    key = provider.replace("-", "_")
    spec = next((s for s in oauth_providers() if s.name == key), None)
    if not spec:
        names = ", ".join(s.name.replace("_", "-") for s in oauth_providers())
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)

    handler = _LOGIN_HANDLERS.get(spec.name)
    if not handler:
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler()


_LOGIN_HANDLERS: dict[str, callable] = {}


def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn

    return decorator


@_register_login("openai_codex")
def _login_openai_codex() -> None:
    try:
        from oauth_cli_kit import get_token, login_oauth_interactive

        token = None
        try:
            token = get_token()
        except Exception:
            pass
        if not (token and token.access):
            console.print("[cyan]Starting interactive OAuth login...[/cyan]\n")
            token = login_oauth_interactive(
                print_fn=lambda s: console.print(s),
                prompt_fn=lambda s: typer.prompt(s),
            )
        if not (token and token.access):
            console.print("[red]\u2717 Authentication failed[/red]")
            raise typer.Exit(1)
        console.print(
            f"[green]\u2713 Authenticated with OpenAI Codex[/green]  [dim]{token.account_id}[/dim]"
        )
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)


@_register_login("github_copilot")
def _login_github_copilot() -> None:
    """Trigger GitHub Copilot device flow via the OAuth plugin."""
    from src.auth.plugins import register_builtin_plugins
    from src.auth.store import add_oauth_profile

    plugins = register_builtin_plugins()
    plugin = plugins.get("github_copilot")
    if not plugin:
        console.print("[red]GitHub Copilot plugin not available[/red]")
        raise typer.Exit(1)

    console.print("[cyan]Starting GitHub Copilot device flow...[/cyan]\n")
    cred = plugin.login(redirect_uri="")
    if cred:
        pid = add_oauth_profile(
            provider=cred.provider,
            access=cred.access,
            refresh=cred.refresh,
            expires=cred.expires,
        )
        console.print(f"[green]\u2713 Authenticated with GitHub Copilot[/green] (profile: {pid})")
    else:
        console.print("[red]\u2717 Authentication failed[/red]")
        raise typer.Exit(1)
