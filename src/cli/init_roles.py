"""Team mode role configuration wizard for ``theos init``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from src.cli.display import console
from src.cli.init_providers import build_model_choices, detect_configured_providers

if TYPE_CHECKING:
    from src.config.schema import AgentRoleConfig

# Role presets for team-mode orchestration
ROLE_PRESETS: dict[str, dict] = {
    "explorer": {
        "description": "Fast exploration agent for codebase research",
        "prompt": (
            "You are a fast exploration agent. Your job is to quickly scan codebases, "
            "search for relevant files, read documentation, and gather information. "
            "Report findings concisely. Do NOT modify any files."
        ),
        "tools": ["read_file", "list_dir", "bash", "web_search", "web_fetch"],
        "max_iterations": 60,
        "model_hint": "haiku",
    },
    "executor": {
        "description": "Implementation agent for writing and editing code",
        "prompt": (
            "You are an execution agent. Your job is to implement code changes: "
            "write new files, edit existing code, and run commands to verify your work. "
            "Follow existing code style and conventions."
        ),
        "tools": ["read_file", "write_file", "edit_file", "list_dir", "bash"],
        "max_iterations": 60,
        "model_hint": "sonnet",
    },
    "reviewer": {
        "description": "Code review agent for quality assurance",
        "prompt": (
            "You are a code reviewer. Your job is to review code changes for correctness, "
            "style, security issues, and potential bugs. Read the code carefully and provide "
            "actionable feedback. You CAN and SHOULD directly fix issues you find."
        ),
        "tools": ["read_file", "write_file", "edit_file", "list_dir", "bash"],
        "max_iterations": 60,
        "model_hint": "opus",
    },
}


def configure_roles_interactive(
    configured_providers: list[str] | None = None,
) -> dict[str, AgentRoleConfig] | None:
    """Interactive wizard to configure team-mode roles.

    If *configured_providers* is ``None``, auto-detect from auth profiles.
    Returns the built roles dict, or ``None`` if setup was skipped.
    """
    from src.config.schema import AgentRoleConfig

    if configured_providers is None:
        configured_providers = detect_configured_providers()

    models = build_model_choices(configured_providers)
    if not models:
        console.print("[yellow]  No chat models found — skipping team setup.[/yellow]")
        return None

    console.print("\n  Available models:")
    for i, (mid, mlabel) in enumerate(models, 1):
        console.print(f"    [{i}] {mlabel}  [dim]({mid})[/dim]")
    console.print("    [0] Custom (type model name)")

    def _find_default(hint: str) -> int:
        for idx, (mid, _) in enumerate(models):
            if hint in mid.lower():
                return idx + 1
        return 1

    roles_built: dict[str, AgentRoleConfig] = {}
    for role_name, preset in ROLE_PRESETS.items():
        default_idx = _find_default(preset["model_hint"])
        raw = typer.prompt(
            f"\n  {role_name.capitalize()} ({preset['description'].split(' ', 3)[-1].rstrip('.')})",
            default=str(default_idx),
            prompt_suffix=" ",
        ).strip()
        if raw == "0":
            custom = typer.prompt("  Model name", prompt_suffix=" ").strip()
            chosen_model = custom if custom else models[default_idx - 1][0]
        else:
            try:
                chosen_model = models[int(raw) - 1][0]
            except (ValueError, IndexError):
                chosen_model = models[default_idx - 1][0]

        roles_built[role_name] = AgentRoleConfig(
            description=preset["description"],
            model=chosen_model,
            prompt=preset["prompt"],
            max_iterations=preset["max_iterations"],
            tools=preset["tools"],
        )

    return roles_built
