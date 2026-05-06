"""Utility functions for TheOS."""

import re
from pathlib import Path


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists, return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_path() -> Path:
    """~/.theos data directory."""
    return ensure_dir(Path.home() / ".theos")


def get_workspace_path(workspace: str | None = None) -> Path:
    """Resolve and ensure workspace path. Defaults to ~/.theos/workspace."""
    path = Path(workspace).expanduser() if workspace else Path.home() / ".theos" / "workspace"
    return ensure_dir(path)


_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')


def safe_filename(name: str) -> str:
    """Replace unsafe path characters with underscores."""
    return _UNSAFE_CHARS.sub("_", name).strip()


def sync_workspace_templates(workspace: Path, silent: bool = False) -> list[str]:
    """Sync bundled templates to workspace. Only creates missing files."""
    from importlib.resources import files as pkg_files
    from importlib.resources.abc import Traversable

    from loguru import logger

    try:
        tpl = pkg_files("src") / "templates"
    except Exception as e:
        logger.warning("Failed to locate bundled templates: {}", e)
        return []
    if not tpl.is_dir():
        logger.warning("Templates directory not found in package data")
        return []

    added: list[str] = []

    def _write(src: Traversable | None, dest: Path) -> None:
        if dest.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8") if src else "", encoding="utf-8")
        added.append(str(dest.relative_to(workspace)))

    bootstrap_templates = {"AGENTS.md", "SOUL.md", "IDENTITY.md", "HEARTBEAT.md"}
    for item in tpl.iterdir():
        if item.name in bootstrap_templates:
            _write(item, workspace / item.name)
    _write(tpl / "memory" / "MEMORY.md", workspace / "memory" / "MEMORY.md")
    _write(None, workspace / "memory" / "HISTORY.md")
    (workspace / "skills").mkdir(exist_ok=True)
    (workspace / "reference").mkdir(exist_ok=True)

    if added and not silent:
        from rich.console import Console

        console = Console()
        for name in added:
            console.print(f"  [dim]Created {name}[/dim]")
    return added
