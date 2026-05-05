"""Configuration preset commands."""

from __future__ import annotations

import json
from typing import Any

from src.cli.display import console
from src.config.schema import Config
from src.security.autonomy import AutonomyLevel
from src.security.config_secrets import ConfigSecretsManager

_FEATURE_ROWS: tuple[tuple[str, str, str], ...] = (
    ("Runtime", "agents.mode", "Agent runtime mode: single, team, genver, or auto."),
    ("Runtime", "agents.teamEnabled", "Allow switching into team/subagent mode."),
    ("Runtime", "agents.genverEnabled", "Allow switching into GenVer mode."),
    ("Runtime", "agents.orchestrator.enabled", "Enable lifecycle/retry/review policy wrapper."),
    ("Runtime", "agents.orchestrator.eventStore.enabled", "Persist orchestrator event records."),
    ("Runtime", "agents.orchestrator.approvalGate.enabled", "Require approval for risky tools."),
    ("Runtime", "agents.orchestrator.neuroSymbolic.enabled", "Enable path-based file risk scoring."),
    ("Memory", "memory.enabled", "Enable markdown memory and retrieval context."),
    ("Memory", "memory.search.enabled", "Enable memory search tools."),
    ("Memory", "memory.search.hybrid.enabled", "Enable hybrid text/vector memory search."),
    ("Memory", "memory.flush.enabled", "Flush important context before compaction."),
    ("Memory", "memory.gc.enabled", "Run memory garbage collection/time decay."),
    ("Memory", "memory.telemetry.recallEnabled", "Record recall telemetry and maintenance data."),
    ("Memory", "agents.orchestrator.memoryTiers.enabled", "Enable experimental three-tier memory."),
    ("Memory", "knowledgeGraph.enabled", "Enable structured knowledge graph memory."),
    ("Learning", "learning.enabled", "Enable hooks, instinct commands, and learning context."),
    ("Tools", "tools.profile", "Tool profile: minimal, coding, messaging, readonly, or full."),
    ("Tools", "tools.browser.enabled", "Register browser automation when profile allows it."),
    ("Tools", "tools.stock.enabled", "Enable stock analysis tool and schedule support."),
    ("Gateway", "gateway.heartbeat.enabled", "Enable scheduled heartbeat prompts."),
    ("Gateway", "gateway.ui.enabled", "Start dashboard UI with the gateway."),
    ("Gateway", "gateway.pollers.x.enabled", "Enable X/Twitter polling automation."),
    ("Cache", "responseCache.enabled", "Enable response cache for repeated LLM calls."),
    ("Channels", "channels.telegram.enabled", "Enable Telegram channel."),
    ("Channels", "channels.feishu.enabled", "Enable Feishu channel."),
    ("Channels", "channels.discord.enabled", "Enable Discord channel."),
    ("Channels", "channels.slack.enabled", "Enable Slack channel."),
    ("Channels", "channels.matrix.enabled", "Enable Matrix channel."),
    ("Channels", "channels.email.enabled", "Enable email channel."),
)


def _save_config(config: Config) -> None:
    from src.config.loader import get_config_path, save_config

    save_config(config)
    console.print(f"[green]✓[/green] Config updated: {get_config_path()}")


def _print_restart_hint() -> None:
    console.print("[dim]Next `theos agent` run will use this immediately.[/dim]")
    console.print("[dim]For the running gateway, apply it with `theos gateway restart`.[/dim]")


def _lookup(data: dict[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _mask_sensitive_values(node: Any, prefix: str = "") -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            child = f"{prefix}.{key}" if prefix else key
            if ConfigSecretsManager.is_sensitive_path(child) and isinstance(value, str) and value:
                out[key] = "***"
            else:
                out[key] = _mask_sensitive_values(value, child)
        return out
    if isinstance(node, list):
        return [_mask_sensitive_values(item, prefix) for item in node]
    return node


def apply_full_access(config: Config) -> None:
    """Enable a personal-machine development profile with all guardrails opened."""
    config.tools.profile = "full"
    config.tools.restrict_to_workspace = False
    config.tools.browser.enabled = True

    config.security.network_isolated = False
    config.security.autonomy.level = AutonomyLevel.FULL
    config.security.autonomy.workspace_only = False
    config.security.autonomy.forbidden_paths = []
    config.security.autonomy.allowed_commands = []
    config.security.autonomy.max_actions_per_hour = 0
    config.security.autonomy.max_cost_per_day = 0.0
    config.security.autonomy.auto_approve = []
    config.security.autonomy.always_ask = []

    config.agents.orchestrator.approval_gate.enabled = False
    config.agents.orchestrator.neuro_symbolic.enabled = False


def apply_safe_defaults(config: Config) -> None:
    """Restore the conservative default profile and local guardrails."""
    default = Config()
    config.tools.profile = default.tools.profile
    config.tools.restrict_to_workspace = default.tools.restrict_to_workspace
    config.tools.browser.enabled = default.tools.browser.enabled

    config.security.network_isolated = default.security.network_isolated
    config.security.autonomy.level = default.security.autonomy.level
    config.security.autonomy.workspace_only = default.security.autonomy.workspace_only
    config.security.autonomy.forbidden_paths = list(default.security.autonomy.forbidden_paths)
    config.security.autonomy.allowed_commands = list(default.security.autonomy.allowed_commands)
    config.security.autonomy.max_actions_per_hour = default.security.autonomy.max_actions_per_hour
    config.security.autonomy.max_cost_per_day = default.security.autonomy.max_cost_per_day
    config.security.autonomy.auto_approve = list(default.security.autonomy.auto_approve)
    config.security.autonomy.always_ask = list(default.security.autonomy.always_ask)

    config.agents.orchestrator.approval_gate.enabled = (
        default.agents.orchestrator.approval_gate.enabled
    )
    config.agents.orchestrator.approval_gate.auto_approve = list(
        default.agents.orchestrator.approval_gate.auto_approve
    )
    config.agents.orchestrator.approval_gate.timeout_seconds = (
        default.agents.orchestrator.approval_gate.timeout_seconds
    )
    config.agents.orchestrator.neuro_symbolic.enabled = (
        default.agents.orchestrator.neuro_symbolic.enabled
    )
    config.agents.orchestrator.neuro_symbolic.whitelist_patterns = list(
        default.agents.orchestrator.neuro_symbolic.whitelist_patterns
    )
    config.agents.orchestrator.neuro_symbolic.blacklist_patterns = list(
        default.agents.orchestrator.neuro_symbolic.blacklist_patterns
    )


def full_access() -> None:
    """Enable full local-development tool access.

    Intended for a trusted personal machine. This turns on the full tool profile,
    removes workspace/path/approval guardrails, and enables browser tool
    registration when its optional dependency is installed.
    """
    from src.config.loader import load_config

    config = load_config()
    apply_full_access(config)
    _save_config(config)
    console.print("[yellow]Full-access development mode enabled.[/yellow]")
    console.print(
        "[dim]Opened: tools.profile=full, autonomy=full, workspaceOnly=false, "
        "approvalGate=false.[/dim]"
    )
    _print_restart_hint()


def safe() -> None:
    """Restore conservative default tool permissions."""
    from src.config.loader import load_config

    config = load_config()
    apply_safe_defaults(config)
    _save_config(config)
    console.print("[green]Safe default mode restored.[/green]")
    console.print("[dim]Restored: tools.profile=minimal, autonomy=supervised.[/dim]")
    _print_restart_hint()


def compact() -> None:
    """Rewrite config.json in compact human-editable form without changing values."""
    from src.config.loader import load_config

    config = load_config()
    _save_config(config)
    console.print("[green]Compact config written.[/green]")
    console.print("[dim]Only non-default values are persisted; defaults are filled at load time.[/dim]")


def features() -> None:
    """List feature flags, current values, defaults, and config paths."""
    from src.config.loader import load_config

    current = load_config().model_dump(by_alias=True)
    defaults = Config().model_dump(by_alias=True)

    console.print("TheOS Config Features\n")
    previous_area = ""
    for area, path, description in _FEATURE_ROWS:
        if area != previous_area:
            if previous_area:
                console.print()
            console.print(f"[cyan]{area}[/cyan]")
            previous_area = area
        now = _format_value(_lookup(current, path))
        default = _format_value(_lookup(defaults, path))
        console.print(f"  [green]{path}[/green] = {now} [dim](default: {default})[/dim]")
        console.print(f"    [dim]{description}[/dim]")
    console.print("[dim]Use `theos config show --full` to inspect the merged runtime config.[/dim]")


def show(*, full: bool = False) -> None:
    """Print the current config as JSON, masking sensitive values."""
    from src.config.loader import load_config

    data = load_config().model_dump(by_alias=True, exclude_defaults=not full)
    console.print(json.dumps(_mask_sensitive_values(data), indent=2, ensure_ascii=False))
