"""Dream artifact tracker.

Tracks files created during dream sandbox execution and writes
an artifacts manifest to artifacts.json.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class ArtifactEntry:
    """Single artifact produced during a dream session."""

    path: str
    type: str  # e.g. "code", "data", "note", "config"
    description: str
    size_bytes: int
    created_at: str


class ArtifactTracker:
    """Track and persist dream artifacts."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.entries: list[ArtifactEntry] = []

    def add(
        self,
        path: Path,
        artifact_type: str = "file",
        description: str = "",
    ) -> ArtifactEntry:
        """Register a new artifact."""
        entry = ArtifactEntry(
            path=str(path),
            type=artifact_type,
            description=description,
            size_bytes=path.stat().st_size if path.exists() else 0,
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        self.entries.append(entry)
        return entry

    def write_manifest(self) -> Path:
        """Write artifacts manifest to output directory."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / "artifacts.json"
        data = [asdict(e) for e in self.entries]
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        return path
