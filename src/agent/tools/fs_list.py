"""List directory tool aligned with Claude Code spec."""

import fnmatch
from pathlib import Path
from typing import Any

from src.agent.tools.base import Tool
from src.agent.tools.tool_security import policy_error
from src.utils.path import resolve_path as _resolve_path


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
            raw_policy_error = policy_error(path, kind="Directory listing")
            if raw_policy_error:
                return raw_policy_error
            dir_path = _resolve_path(path, self._workspace, self._allowed_dir)
            resolved_policy_error = policy_error(str(dir_path), kind="Directory listing")
            if resolved_policy_error:
                return resolved_policy_error
            if not dir_path.exists():
                return f"Error: Directory not found: {path}"
            if not dir_path.is_dir():
                return f"Error: Not a directory: {path}"

            # Collect ignore matches
            ignored: set[str] = set()
            if ignore:
                for item in dir_path.iterdir():
                    for pat in ignore:
                        if fnmatch.fnmatch(item.name, pat):
                            ignored.add(item.name)

            items = []
            for item in sorted(dir_path.iterdir()):
                if item.name in ignored:
                    continue
                prefix = "📁 " if item.is_dir() else "📄 "
                items.append(f"{prefix}{item.name}")

            if not items:
                return f"Directory {path} is empty"
            return "\n".join(items)
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory: {str(e)}"
