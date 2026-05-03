"""Diary publisher — appends dream entries to DREAMS.md.

Uses managed markdown block pattern with markers:
  <!-- theos:instinct:dream-diary:start -->
  ...entries...
  <!-- theos:instinct:dream-diary:end -->
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

_START_MARKER = "<!-- theos:instinct:dream-diary:start -->"
_END_MARKER = "<!-- theos:instinct:dream-diary:end -->"


def publish_diary_entry(
    *,
    workspace: Path,
    session_id: str,
    topic: str,
    status: str,
    summary: str = "",
) -> Path:
    """Append a diary-style entry to DREAMS.md in workspace.

    Creates the file and managed block if they don't exist.

    Returns:
        Path to DREAMS.md.
    """
    dreams_path = workspace / "DREAMS.md"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    entry = f"### {ts} — {topic}\n\n"
    entry += f"Session: `{session_id}` | Status: {status}\n\n"
    if summary:
        entry += f"{summary}\n\n"
    entry += "---\n\n"

    if dreams_path.exists():
        content = dreams_path.read_text()
    else:
        content = f"# Dream Diary\n\n{_START_MARKER}\n{_END_MARKER}\n"

    if _START_MARKER not in content:
        content += f"\n{_START_MARKER}\n{_END_MARKER}\n"

    # Insert new entry just before end marker
    content = content.replace(
        _END_MARKER,
        f"{entry}{_END_MARKER}",
    )

    dreams_path.write_text(content)
    return dreams_path
