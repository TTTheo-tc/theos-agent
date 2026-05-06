"""AgentFS — shared file workspace for Generator-Verifier mode.

Thin wrapper around a directory for structured JSON data exchange
between Generator, Explorer, and Verifier agents.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path, PurePosixPath
from typing import Any


class AgentFS:
    """Directory-backed JSON store for inter-agent data exchange."""

    def __init__(self, workspace: Path, subdir: str = ".genver") -> None:
        self.root = workspace / subdir
        if self.root.is_symlink():
            raise ValueError(f"AgentFS root must not be a symlink: {self.root}")
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, name: str, data: Any) -> Path:
        """Write structured data as JSON. Returns the file path."""
        path = self._artifact_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def read(self, name: str) -> Any:
        """Read structured data by name. Returns None if not found."""
        path = self._artifact_path(name)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def clear(self) -> None:
        """Remove all files in the workspace."""
        if self.root.exists():
            shutil.rmtree(self.root)
            self.root.mkdir(parents=True, exist_ok=True)

    def _artifact_path(self, name: str) -> Path:
        """Return the JSON artifact path for a validated relative artifact name."""
        raw_name = name.strip()
        relative = PurePosixPath(raw_name)
        raw_parts = raw_name.split("/")
        if (
            not raw_name
            or raw_name != name
            or "\\" in raw_name
            or ":" in raw_name
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in raw_parts)
        ):
            raise ValueError(f"Invalid AgentFS artifact name: {name!r}")
        base_path = self.root.joinpath(*relative.parts)
        path = base_path.parent / f"{base_path.name}.json"
        root = self.root.resolve()
        parent = path.parent.resolve(strict=False)
        if root != parent and root not in parent.parents:
            raise ValueError(f"Invalid AgentFS artifact name: {name!r}")
        if path.is_symlink():
            raise ValueError(f"Invalid AgentFS artifact name: {name!r}")
        return path
