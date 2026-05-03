"""AgentFS — shared file workspace for Generator-Verifier mode.

Thin wrapper around a directory for structured JSON data exchange
between Generator, Explorer, and Verifier agents.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


class AgentFS:
    """Directory-backed JSON store for inter-agent data exchange."""

    def __init__(self, workspace: Path, subdir: str = ".genver"):
        self.root = workspace / subdir
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, name: str, data: Any) -> Path:
        """Write structured data as JSON. Returns the file path."""
        path = self.root / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def read(self, name: str) -> Any:
        """Read structured data by name. Returns None if not found."""
        path = self.root / f"{name}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def clear(self) -> None:
        """Remove all files in the workspace."""
        if self.root.exists():
            shutil.rmtree(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
