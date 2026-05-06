"""List directory tool aligned with Claude Code spec."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from src.agent.tools.base import Tool
from src.agent.tools.tool_security import policy_error
from src.utils.path import resolve_path as _resolve_path


def _resolve_directory(
    path: str,
    workspace: Path | None,
    allowed_dir: Path | None,
) -> tuple[Path | None, str | None]:
    raw_policy_error = policy_error(path, kind="Directory listing")
    if raw_policy_error:
        return None, raw_policy_error
    try:
        dir_path = _resolve_path(path, workspace, allowed_dir)
    except PermissionError as e:
        return None, f"Error: {e}"
    resolved_policy_error = policy_error(str(dir_path), kind="Directory listing")
    if resolved_policy_error:
        return None, resolved_policy_error
    if not dir_path.exists():
        return None, f"Error: Directory not found: {path}"
    if not dir_path.is_dir():
        return None, f"Error: Not a directory: {path}"
    return dir_path, None


def _is_ignored(name: str, patterns: list[str] | None) -> bool:
    return bool(patterns and any(fnmatch.fnmatch(name, pattern) for pattern in patterns))


def _format_item(item: Path) -> str:
    prefix = "📁 " if item.is_dir() else "📄 "
    return f"{prefix}{item.name}"


class ListDirTool(Tool):
    """List directory contents with optional ignore patterns."""

    def __init__(self, workspace: Path | None = None, allowed_dir: Path | None = None):
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "List the contents of a directory."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the directory to list",
                },
                "ignore": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Glob patterns to exclude (e.g. ['node_modules', '*.pyc'])",
                },
            },
            "required": ["path"],
        }

    async def execute(self, path: str, ignore: list[str] | None = None, **kwargs: Any) -> str:
        try:
            dir_path, error = _resolve_directory(path, self._workspace, self._allowed_dir)
            if error:
                return error
            assert dir_path is not None

            items = [
                _format_item(item)
                for item in sorted(dir_path.iterdir())
                if not _is_ignored(item.name, ignore)
            ]

            if not items:
                return f"Directory {path} is empty"
            return "\n".join(items)
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory: {str(e)}"
