"""Console instance and display helpers shared across CLI modules."""

import re
from pathlib import Path
from typing import Sequence

from rich import box
from rich.align import Align
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

THEOS_ACCENT = "#7EE7D4"
THEOS_GOLD = "#F4B860"
THEOS_MUTED = "#7F8EA3"
THEOS_GREEN = "#3DDC97"
THEOS_RED = "#FF6B6B"

THEOS_WORDMARK = """
[bold #7EE7D4]╔╦╗╦ ╦╔═╗╔═╗╔═╗[/]
[bold #7EE7D4] ║ ╠═╣║╣ ║ ║╚═╗[/]
[bold #F4B860] ╩ ╩ ╩╚═╝╚═╝╚═╝[/]
""".strip()

COMMAND_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Start",
        (
            ("theos agent", "Interactive terminal agent"),
            ("theos agent -m \"...\"", "One-shot answer"),
            ("theos init", "Config, workspace, provider setup"),
        ),
    ),
    (
        "Operate",
        (
            ("theos status", "Local config, auth, and gateway state"),
            ("theos gateway", "Foreground gateway"),
            ("theos gateway restart", "Restart service"),
            ("theos ui", "Read-only dashboard"),
        ),
    ),
    (
        "Configure",
        (
            ("theos auth", "Provider credentials"),
            ("theos provider", "Model provider login"),
            ("theos config", "Runtime presets and config view"),
            ("theos channels", "Messaging channel setup"),
        ),
    ),
    (
        "Automate",
        (
            ("theos cron list", "Scheduled jobs"),
            ("theos cron add", "Add a job"),
            ("theos report daily", "Activity report"),
        ),
    ),
)


def _short_path(path: str | Path | None, *, max_len: int = 58) -> str:
    if path is None:
        return "-"
    text = str(path)
    home = str(Path.home())
    if text == home:
        text = "~"
    elif text.startswith(f"{home}/"):
        text = f"~/{text[len(home) + 1:]}"
    if len(text) <= max_len:
        return text
    return f"...{text[-(max_len - 3):]}"


def _status_mark(ok: bool | None) -> str:
    if ok is True:
        return f"[bold {THEOS_GREEN}]ok[/]"
    if ok is False:
        return f"[bold {THEOS_RED}]missing[/]"
    return f"[dim {THEOS_MUTED}]unknown[/]"


def make_status_row(label: str, value: str | Path, ok: bool | None = None) -> str:
    """Return a styled status row while preserving plain ``Label:`` text."""
    suffix = f"  {_status_mark(ok)}" if ok is not None else ""
    return f"[bold {THEOS_ACCENT}]{label}:[/] {_short_path(value)}{suffix}"


def make_plain_row(label: str, value: str | Path) -> str:
    """Return a styled key/value row while preserving plain ``Label:`` text."""
    text = str(value)
    display_value = text if text.startswith("[") else _short_path(text)
    return f"[bold {THEOS_ACCENT}]{label}:[/] {display_value}"


def print_cli_home(version: str) -> None:
    """Render the root command overview."""
    narrow = console.width < 104
    command_table = Table.grid(padding=(0, 3 if not narrow else 1), expand=True)

    columns: list[Table] = []
    for title, commands in COMMAND_GROUPS:
        group = Table.grid(padding=(0, 1))
        group.add_column(justify="left", width=22 if narrow else 18, no_wrap=True)
        group.add_column(justify="left", ratio=1)
        group.add_row(f"[bold {THEOS_GOLD}]{title}[/]", "")
        for command, description in commands:
            group.add_row(f"[{THEOS_ACCENT}]{command}[/]", f"[dim]{description}[/]")
        columns.append(group)

    if narrow:
        command_table.add_column(justify="left", ratio=1)
        for column in columns:
            command_table.add_row(column)
            command_table.add_row("")
    else:
        command_table.add_column(justify="left", ratio=1)
        command_table.add_column(justify="left", ratio=1)
        command_table.add_row(columns[0], columns[1])
        command_table.add_row(columns[2], columns[3])

    layout = Table.grid(expand=True, padding=(0, 2))
    if narrow:
        layout.add_column(ratio=1)
        layout.add_row(
            Align.center(
                f"{THEOS_WORDMARK}\n[dim {THEOS_MUTED}]agentic operating system[/]",
                vertical="middle",
            )
        )
        layout.add_row(command_table)
    else:
        layout.add_column(justify="center", width=24)
        layout.add_column(ratio=1)
        layout.add_row(
            Align.center(
                f"{THEOS_WORDMARK}\n[dim {THEOS_MUTED}]agentic operating system[/]",
                vertical="middle",
            ),
            command_table,
        )

    panel = Panel(
        layout,
        title=f"[bold {THEOS_ACCENT}]theos[/] [dim]v{version}[/]",
        subtitle=f"[dim {THEOS_MUTED}]Run [bold]theos <command> --help[/bold] for details[/]",
        border_style=THEOS_ACCENT,
        box=box.ROUNDED,
        padding=(1, 2),
    )
    console.print()
    console.print(panel)


def print_agent_banner(
    *,
    model: str,
    mode: str,
    tools: int,
    workspace: str | Path,
    session_id: str,
    logs: bool = False,
    tool_names: Sequence[str] | None = None,
    details: Sequence[str] | None = None,
    page: bool = False,
) -> None:
    """Render the interactive agent header."""
    model_short = model.split("/")[-1] if "/" in model else model
    if page:
        console.rule(f"[bold {THEOS_ACCENT}]TheOS Agent[/]", style=THEOS_ACCENT)
        console.print(
            f"[bold {THEOS_ACCENT}]{model_short}[/] "
            f"[dim]│ {mode} │ {tools} tools │ {_short_path(workspace)}[/]"
        )
        console.print(
            f"[dim {THEOS_MUTED}]session {session_id} │ /help │ /model │ /agent │ Ctrl+C quit[/]"
        )
        if details:
            for detail in details:
                console.print(f"[dim {THEOS_MUTED}]  {detail}[/]")
        if logs and tool_names:
            tool_line = ", ".join(tool_names)
            if len(tool_line) > 96:
                tool_line = f"{tool_line[:93]}..."
            console.print(f"[dim {THEOS_MUTED}]  tools: {tool_line}[/]")
        console.rule(style=THEOS_MUTED)
        return

    summary = (
        f"[bold {THEOS_ACCENT}]TheOS[/] "
        f"[dim]{model_short} · {mode} · {tools} tools · {_short_path(workspace)}[/]"
    )
    console.print()
    console.print(summary)
    console.print(
        f"[dim {THEOS_MUTED}]session {session_id} · /help · /model · /agent · Ctrl+C to quit[/]"
    )
    if details:
        for detail in details:
            console.print(f"[dim {THEOS_MUTED}]  {detail}[/]")
    if logs and tool_names:
        tool_line = ", ".join(tool_names)
        if len(tool_line) > 96:
            tool_line = f"{tool_line[:93]}..."
        console.print(f"[dim {THEOS_MUTED}]  tools: {tool_line}[/]")


def format_agent_toolbar(
    *,
    model: str,
    mode: str,
    tools: int,
    session_usage: dict | None = None,
) -> str:
    """Return a compact bottom toolbar for the interactive agent page."""
    model_short = model.split("/")[-1] if "/" in model else model
    session_total = _usage_counts(session_usage)[2]
    usage_label = f"{_format_token_count(session_total)} tok" if session_total > 0 else "ready"
    return (
        f"TheOS {model_short} │ {mode} │ {tools} tools │ {usage_label} "
        "│ Esc stop │ /help /model /agent"
    )


def print_status_header(version: str) -> None:
    panel = Panel(
        Align.center(
            f"{THEOS_WORDMARK}\n[dim {THEOS_MUTED}]theos Status · v{version}[/]",
            vertical="middle",
        ),
        border_style=THEOS_ACCENT,
        box=box.ROUNDED,
        padding=(1, 2),
    )
    console.print(panel)


def print_agent_response(response: str, render_markdown: bool) -> None:
    """Render assistant response — clean, no banner."""
    content = _ANSI_RE.sub("", response or "")
    body = Markdown(content) if render_markdown else Text(content)
    console.print()
    console.print(body)


def _format_token_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _usage_counts(usage: dict | None) -> tuple[int, int, int]:
    if not usage:
        return 0, 0, 0
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
    completion = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    total = usage.get("total_tokens", 0) or (prompt + completion)
    return int(prompt), int(completion), int(total)


def format_token_usage_line(usage: dict | None, session_usage: dict | None = None) -> str | None:
    """Return a compact token usage summary for one CLI turn."""
    prompt, completion, total = _usage_counts(usage)
    if prompt <= 0 and completion <= 0 and total <= 0:
        return None

    parts = [
        f"in {_format_token_count(prompt)}",
        f"out {_format_token_count(completion)}",
    ]
    if usage:
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        cache_write = usage.get("cache_creation_input_tokens", 0) or 0
        if cache_read or cache_write:
            parts.append(
                f"cache {_format_token_count(cache_read)} read/{_format_token_count(cache_write)} write"
            )
    parts.append(f"turn {_format_token_count(total)}")

    session_total = _usage_counts(session_usage)[2]
    if session_total > 0:
        parts.append(f"session {_format_token_count(session_total)}")
    return "usage │ " + " │ ".join(parts)


def print_token_usage(usage: dict | None, session_usage: dict | None = None) -> None:
    """Display token usage in a compact line, Claude Code style."""
    line = format_token_usage_line(usage, session_usage=session_usage)
    if not line:
        return

    label, _, rest = line.partition(" │ ")
    console.print(
        f"\n[dim {THEOS_MUTED}]╰─[/] [bold {THEOS_ACCENT}]{label}[/] "
        f"[dim]│ {rest}[/dim]"
    )

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
                parts.append(
                    f"{label} {model}: {_format_token_count(rp)} → {_format_token_count(rc)}"
                )
        if parts:
            console.print(f"[dim]  {'  '.join(parts)}[/dim]")
