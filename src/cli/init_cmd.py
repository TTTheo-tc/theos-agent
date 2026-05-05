"""Init wizard — ``theos init`` command implementation.

Sub-wizards live in:
- init_providers.py  — provider detection, model fetching
- init_roles.py      — team-mode role configuration
- init_genver.py     — generator-verifier configuration
- init_channels.py   — channel setup wizard
- init_soul.py       — personality preset setup
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import typer
from loguru import logger

from src import __logo__
from src.cli.display import console
from src.config.schema import Config
from src.utils.helpers import sync_workspace_templates
from src.utils.proxy import (
    ALL_PROXY_ENV_KEYS,
    PROXY_ENV_KEYS,
    apply_http_proxy_env,
    first_supported_proxy_env,
    has_supported_http_proxy_env,
    is_socks_proxy,
)


def configure_channels(config: Config) -> None:
    from src.cli.init_channels import configure_channels as _configure_channels

    return _configure_channels(config)


def configure_genver_interactive(configured_providers: list[str] | None = None):
    from src.cli.init_genver import configure_genver_interactive as _configure_genver_interactive

    return _configure_genver_interactive(configured_providers)


def configure_roles_interactive(configured_providers: list[str] | None = None):
    from src.cli.init_roles import configure_roles_interactive as _configure_roles_interactive

    return _configure_roles_interactive(configured_providers)


def configure_soul(workspace: Path) -> None:
    from src.cli.init_soul import configure_soul as _configure_soul

    return _configure_soul(workspace)


def _auth_profile_paths() -> list[Path]:
    """Return current auth profile file locations."""
    return [
        Path.home() / ".theos" / "auth-profiles.enc",
        Path.home() / ".theos" / "auth-profiles.json",
    ]


def _resolve_workspace_for_reset(config_path: Path) -> Path:
    """Resolve the current workspace path before destructive reset actions."""
    from src.config.loader import load_config
    from src.utils.helpers import get_workspace_path

    if config_path.exists():
        try:
            return load_config().workspace_path
        except Exception:
            pass
    return get_workspace_path()


def _detect_existing_state(config_path: Path, workspace: Path) -> dict[str, bool]:
    """Check what TheOS data currently exists on disk."""
    auth_paths = _auth_profile_paths()
    return {
        "config": config_path.exists(),
        "auth": any(p.exists() for p in auth_paths),
        "workspace": workspace.exists(),
    }


def _prompt_reset_mode(state: dict[str, bool]) -> str:
    """Show reset menu with live status indicators. Only shows items that exist."""
    console.print("\n[bold]Reset scope[/bold]\n")

    options: list[tuple[str, str]] = []
    if state["auth"]:
        options.append(("auth", "  Auth profiles (API keys, OAuth tokens)"))
    if state["workspace"]:
        options.append(("workspace", "  Workspace (memory, soul, templates)"))
    if state["config"]:
        options.append(("config", "  Config (config.json)"))

    for i, (_key, label) in enumerate(options, 1):
        console.print(f"  [{i}] {label}")
    all_idx = len(options) + 1
    console.print(f"  [{all_idx}] All of the above")
    console.print()

    raw = typer.prompt("  What to reset (number)", default=str(all_idx), prompt_suffix=" ").strip()
    try:
        idx = int(raw)
    except ValueError:
        return "all"
    if idx == all_idx:
        return "all"
    if 1 <= idx <= len(options):
        return options[idx - 1][0]
    return "all"


def _apply_reset(reset_mode: str, config_path: Path, workspace: Path) -> None:
    """Apply the requested reset scope."""
    auth_paths = _auth_profile_paths()

    if reset_mode in {"auth", "all"}:
        for auth_path in auth_paths:
            if auth_path.exists():
                backup = auth_path.with_suffix(auth_path.suffix + ".bak")
                shutil.copy2(auth_path, backup)
                auth_path.unlink()
                console.print(
                    f"[yellow]\u2717[/yellow] Auth profiles removed (backup: {backup.name})"
                )

    if reset_mode in {"workspace", "all"} and workspace.exists():
        shutil.rmtree(workspace)
        console.print(f"[yellow]\u2717[/yellow] Workspace removed: {workspace}")

    if reset_mode in {"config", "all"} and config_path.exists():
        config_path.unlink()
        console.print(f"[yellow]\u2717[/yellow] Config removed: {config_path}")


def _ensure_data_dir(data_dir: Path) -> None:
    """Ensure the config data directory exists and is writable."""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        console.print(
            f"[red]✗[/red] Cannot create {data_dir} — permission denied.\n"
            f"  Fix: [cyan]mkdir -p {data_dir}[/cyan]"
        )
        raise typer.Exit(1)
    if not os.access(data_dir, os.W_OK):
        console.print(
            f"[red]✗[/red] {data_dir} is not writable.\n"
            f"  Fix: [cyan]chmod u+w {data_dir}[/cyan]"
        )
        raise typer.Exit(1)


def _ensure_local_instruction_symlinks(repo_root: Path) -> list[str]:
    """Create local CLI instruction symlinks to BOT.md when missing."""
    bot_path = repo_root / "BOT.md"
    if not bot_path.exists():
        return []

    created: list[str] = []
    for name in ("CLAUDE.md", "GEMINI.md", "AGENTS.md"):
        target = repo_root / name
        if target.exists() or target.is_symlink():
            continue
        target.symlink_to(bot_path.name)
        created.append(name)
    return created


def _compute_daemon_args() -> tuple[list[str], dict[str, str], str]:
    """Compute program args, env, and working dir for daemon install."""
    import sys

    repo_root = str(Path(__file__).resolve().parents[2])
    program_args = [sys.executable, "-m", "src", "gateway"]
    env: dict[str, str] = {}
    # Core runtime
    for key in ("PATH", "HOME", "VIRTUAL_ENV"):
        val = os.environ.get(key)
        if val:
            env[key] = val
    # Proxy
    has_http_proxy = has_supported_http_proxy_env()
    for key in PROXY_ENV_KEYS:
        val = os.environ.get(key)
        if val:
            if is_socks_proxy(val):
                if key in ALL_PROXY_ENV_KEYS and has_http_proxy:
                    continue
                console.print(
                    f"[yellow]⚠ {key}={val} uses SOCKS protocol which is not "
                    f"supported by httpx. Skipping — please set it to an HTTP "
                    f"proxy (e.g. http://127.0.0.1:7890) instead.[/yellow]"
                )
                continue
            env[key] = val
    # Explicitly supported secret/bootstrap variables needed by daemonized runs.
    for key in (
        "SECRETS_MASTER_KEY",
        "ANTHROPIC_OAUTH_CLIENT_ID",
    ):
        val = os.environ.get(key)
        if val:
            env.setdefault(key, val)
    # API keys / secrets — providers and tools may fall back to env vars
    for key, val in os.environ.items():
        if key.endswith(
            (
                "_API_KEY",
                "_API_SECRET",
                "_SECRET_KEY",
                "_TOKEN",
                "_CLIENT_ID",
                "_CLIENT_SECRET",
            )
        ):
            env.setdefault(key, val)
    return program_args, env, repo_root


def init(
    reset: bool = typer.Option(False, "--reset", help="Reset existing data before init"),
    no_daemon: bool = typer.Option(False, "--no-daemon", help="Skip gateway daemon installation"),
):
    """Initialize TheOS: config, workspace, and provider setup."""
    from src.config.loader import get_config_path, load_config, save_config
    from src.utils.helpers import get_workspace_path

    logger.disable("src")
    console.print(f"\n{__logo__} theos init\n")
    try:
        config_path = get_config_path()
        # -- Step 0: ensure config data directory --------------------------------
        _ensure_data_dir(config_path.parent)

        if reset:
            workspace_for_reset = _resolve_workspace_for_reset(config_path)
            state = _detect_existing_state(config_path, workspace_for_reset)

            if not any(state.values()):
                console.print("[dim]  Nothing to reset (fresh install)[/dim]")
            else:
                reset_mode = _prompt_reset_mode(state)
                _apply_reset(reset_mode, config_path, workspace_for_reset)

        # -- Step 1: config + auto-detection (no user interaction) -------------
        if config_path.exists():
            config = load_config()
            save_config(config)  # refresh schema (adds any new fields)
            console.print(f"[green]\u2713[/green] Config: {config_path} (refreshed)")
        else:
            config = Config()
            save_config(config)
            console.print(f"[green]\u2713[/green] Config: {config_path} (created)")

        # Symlinks: CLAUDE.md / GEMINI.md / AGENTS.md -> BOT.md
        repo_root = Path(__file__).resolve().parents[2]
        created_links = _ensure_local_instruction_symlinks(repo_root)
        if created_links:
            console.print(f"[green]\u2713[/green] Links: {', '.join(created_links)} -> BOT.md")

        # Hooks
        repo_hooks = repo_root / "hooks"
        if config.learning.enabled and repo_hooks.is_dir() and (repo_hooks / "pre-chat").exists():
            config.hooks = str(repo_hooks)
            save_config(config)
            console.print(f"[green]\u2713[/green] Hooks: {repo_hooks}")
        elif config.hooks:
            console.print(f"[green]\u2713[/green] Hooks: {config.hooks} (existing)")

        # Proxy
        _proxy_env = first_supported_proxy_env()
        if _proxy_env:
            config.proxy = _proxy_env
            save_config(config)
            console.print(f"[green]\u2713[/green] Proxy: {_proxy_env}")
        elif config.proxy:
            if apply_http_proxy_env(config.proxy):
                console.print(f"[green]\u2713[/green] Proxy: {config.proxy} (from config)")
            else:
                console.print(f"[yellow]⚠[/yellow] Proxy skipped: {config.proxy} is SOCKS")

        # -- Step 2: workspace + soul (interactive) ----------------------------
        workspace = get_workspace_path()
        workspace.mkdir(parents=True, exist_ok=True)
        sync_workspace_templates(workspace)
        console.print(f"[green]\u2713[/green] Workspace: {workspace}")
        configure_soul(workspace)

        # -- Step 3: provider setup --------------------------------------------
        from src.cli.init_providers import configure_providers

        configured_providers = configure_providers(config)

        # -- Step 4: channel setup ---------------------------------------------
        configure_channels(config)
        save_config(config)

        # -- Step 4b: web search setup -------------------------------------------
        console.print("\n[bold]Web search[/bold]\n")
        console.print("  Default provider: [cyan]DuckDuckGo[/cyan] (free, no API key)")
        console.print("  Optional upgrades:")
        console.print("    [1] DuckDuckGo (default, free)")
        console.print("    [2] Brave Search (faster, more relevant)")
        console.print("    [3] Tavily (optimized for AI agents)\n")
        ws_choice = typer.prompt(
            "  Choose provider (number)", default="1", prompt_suffix=" "
        ).strip()

        if ws_choice == "2":
            config.tools.web.search.provider = "brave"
            console.print(
                "\n  Get your Brave API key at: " "[cyan]https://brave.com/search/api/[/cyan]"
            )
            console.print("  Free tier: 2,000 queries/month\n")
            brave_key = typer.prompt(
                "  Brave API key (Enter to skip)",
                default=config.tools.web.search.api_key or "",
                show_default=False,
                prompt_suffix=" ",
            ).strip()
            if brave_key:
                config.tools.web.search.api_key = brave_key
                console.print("[green]\u2713[/green] Brave Search configured")
            else:
                console.print("  [dim]Skipped — will fall back to DuckDuckGo[/dim]")
        elif ws_choice == "3":
            config.tools.web.search.provider = "tavily"
            console.print("\n  Get your Tavily API key at: " "[cyan]https://tavily.com/[/cyan]")
            console.print("  Free tier: 1,000 queries/month\n")
            tavily_key = typer.prompt(
                "  Tavily API key (Enter to skip)",
                default=config.tools.web.search.tavily_api_key or "",
                show_default=False,
                prompt_suffix=" ",
            ).strip()
            if tavily_key:
                config.tools.web.search.tavily_api_key = tavily_key
                console.print("[green]\u2713[/green] Tavily Search configured")
            else:
                console.print("  [dim]Skipped — will fall back to DuckDuckGo[/dim]")
        else:
            config.tools.web.search.provider = "duckduckgo"
            console.print("[green]\u2713[/green] DuckDuckGo (no key needed)")

        save_config(config)

        # -- Step 5: agent orchestration ---------------------------------------
        console.print("\n[bold]Agent orchestration[/bold]\n")
        console.print("  [1] Single")
        console.print("  [2] Team")
        console.print("  [3] GenVer\n")
        orch_raw = typer.prompt("  Choose (number)", default="1", prompt_suffix=" ").strip()

        if orch_raw == "2" and configured_providers:
            roles_built = configure_roles_interactive(configured_providers)
            if roles_built:
                config.agents.roles = roles_built
                save_config(config)
                console.print()
                for rname, rcfg in roles_built.items():
                    console.print(f"[green]\u2713[/green] {rname}: [cyan]{rcfg.model}[/cyan]")
        elif orch_raw == "3" and configured_providers:
            genver_cfg = configure_genver_interactive(configured_providers)
            if genver_cfg:
                config.agents.genver = genver_cfg
                config.agents.mode = "genver"
                save_config(config)
                console.print()
                console.print(
                    f"[green]\u2713[/green] generator: [cyan]{genver_cfg.generator_model}[/cyan]"
                )
                console.print(
                    f"[green]\u2713[/green] verifier:  [cyan]{genver_cfg.verifier_model}[/cyan]"
                )

        # -- Step 6: install gateway daemon service ----------------------------
        if not no_daemon:
            try:
                from src.daemon import resolve_service
                from src.daemon.health import wait_for_gateway

                svc = resolve_service()
                program_args, env, working_dir = _compute_daemon_args()

                was_loaded = svc.is_loaded()
                svc.install(program_args, env, working_dir)
                action = "updated" if was_loaded else "installed"

                ui_cfg = config.gateway.ui
                probe_host = "127.0.0.1"
                probe_port = ui_cfg.port if ui_cfg.enabled else config.gateway.port
                probe_path = "/api/health" if ui_cfg.enabled else "/"
                require_pid_match = ui_cfg.enabled
                if wait_for_gateway(
                    probe_host,
                    probe_port,
                    timeout_s=15,
                    service=svc,
                    path=probe_path,
                    require_pid_match=require_pid_match,
                ):
                    st = svc.status()
                    pid = st.get("pid", "?")
                    console.print(f"[green]\u2713[/green] Gateway: {action} (PID {pid})")
                else:
                    console.print(
                        f"[yellow]\u26a0[/yellow] Gateway: {action} but not yet reachable"
                    )
                    console.print("  Check logs: [cyan]theos gateway logs[/cyan]")
            except NotImplementedError:
                console.print("[dim]  Gateway daemon not supported on this platform.[/dim]")
                console.print("  Run manually: [cyan]theos gateway[/cyan]")
            except Exception as exc:
                console.print(f"[yellow]\u26a0[/yellow] Gateway daemon install failed: {exc}")
                console.print("  Run manually: [cyan]theos gateway[/cyan]")

        # -- Done --------------------------------------------------------------
        console.print(f"\n{__logo__} theos is ready!\n")
        console.print("Start chatting:")
        console.print("  [cyan]theos agent[/cyan]")
        console.print("  [cyan]theos gateway[/cyan]   \u2190 Telegram / Discord / etc.\n")
    finally:
        logger.enable("src")
