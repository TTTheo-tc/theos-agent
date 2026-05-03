"""Generator-Verifier configuration wizard for ``theos init``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from src.cli.display import console
from src.cli.init_providers import (
    build_model_choices,
    detect_configured_providers,
    validate_model_choice,
)
from src.config.schema import DEFAULT_GENVER_VERIFIER_COMMANDS

if TYPE_CHECKING:
    from src.config.schema import GenVerConfig

# Genver role presets
GENVER_ROLE_PRESETS: dict[str, dict[str, str]] = {
    "generator": {
        "label": "Generator (plans + writes code)",
        "model_hint": "opus",
    },
    "verifier": {
        "label": "Verifier (runs tests, checks quality)",
        "model_hint": "codex",
    },
    "explorer": {
        "label": "Explorer (fast read-only codebase scan)",
        "model_hint": "minimax",
    },
}


def configure_genver_interactive(
    configured_providers: list[str] | None = None,
) -> "GenVerConfig | None":
    """Interactive wizard to configure Generator-Verifier models.

    Returns a ``GenVerConfig`` with chosen models, or ``None`` if skipped.
    """
    from src.config.schema import GenVerConfig

    if configured_providers is None:
        configured_providers = detect_configured_providers()

    models = build_model_choices(configured_providers)
    if not models:
        console.print("[yellow]  No models found — skipping genver setup.[/yellow]")
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

    chosen: dict[str, str] = {}
    for role_key, preset in GENVER_ROLE_PRESETS.items():
        default_idx = _find_default(preset["model_hint"])
        while True:
            raw = typer.prompt(
                f"\n  {preset['label']}",
                default=str(default_idx),
                prompt_suffix=" ",
            ).strip()
            if raw == "0":
                custom = typer.prompt("  Model name", prompt_suffix=" ").strip()
                if not custom:
                    chosen[role_key] = models[default_idx - 1][0]
                    break
                ok, validation_msg, verified_live = validate_model_choice(
                    custom, configured_providers
                )
                if not ok:
                    console.print(f"[red]\u2717[/red] {validation_msg}")
                    continue
                if verified_live:
                    console.print(f"[green]\u2713[/green] {validation_msg}")
                    chosen[role_key] = custom
                    break
                console.print(f"[yellow]\u26a0[/yellow] {validation_msg}")
                if typer.confirm("  Keep this model anyway?", default=False):
                    chosen[role_key] = custom
                    break
                continue
            try:
                chosen[role_key] = models[int(raw) - 1][0]
            except (ValueError, IndexError):
                chosen[role_key] = models[default_idx - 1][0]
            break

    # Verifier commands
    default_verifier_commands = ", ".join(DEFAULT_GENVER_VERIFIER_COMMANDS)
    raw_cmds = typer.prompt(
        "\n  Verifier commands (comma-separated)",
        default=default_verifier_commands,
        prompt_suffix=" ",
    ).strip()
    verifier_commands = [c.strip() for c in raw_cmds.split(",") if c.strip()]

    return GenVerConfig(
        generator_model=chosen.get("generator", ""),
        verifier_model=chosen.get("verifier", ""),
        explorer_model=chosen.get("explorer", ""),
        verifier_commands=verifier_commands or list(DEFAULT_GENVER_VERIFIER_COMMANDS),
    )
