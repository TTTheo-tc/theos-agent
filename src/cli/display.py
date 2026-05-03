"""Console instance and display helpers shared across CLI modules."""

import re

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

console = Console()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def print_agent_response(response: str, render_markdown: bool) -> None:
    """Render assistant response — clean, no banner."""
    content = _ANSI_RE.sub("", response or "")
    body = Markdown(content) if render_markdown else Text(content)
    console.print()
    console.print(body)


def print_token_usage(usage: dict[str, int] | None) -> None:
    """Display token usage in a compact line, Claude Code style."""
    if not usage:
        return
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    total = usage.get("total_tokens", 0) or (prompt + completion)

    def _fmt(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}k"
        return str(n)

    console.print(f"\n[dim]{_fmt(prompt)} → {_fmt(completion)} tokens (total: {_fmt(total)})[/dim]")

    # Per-role breakdown for GenVer mode
    breakdown = usage.get("_breakdown")
    if breakdown:
        parts = []
        for role, label in [("generator", "gen"), ("verifier", "ver")]:
            info = breakdown.get(role, {})
            model = info.get("model", "?")
            rp = info.get("prompt_tokens", 0)
            rc = info.get("completion_tokens", 0)
            if rp or rc:
                parts.append(f"{label} {model}: {_fmt(rp)} → {_fmt(rc)}")
        if parts:
            console.print(f"[dim]  {'  '.join(parts)}[/dim]")
