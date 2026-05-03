# src/genver/artifact_store.py
"""Persistent storage for GenVer phase artifacts and review records.

Directory layout under the genver root:
    artifacts/
        spec.md, plan.md, report.md    — phase output documents
        rounds/
            spec_gen_write.json         — per-step review records
            spec_ver_review.json
            ...
    runtime/
        verify_report_1.json           — ephemeral verification data (cleared per run)
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


class ArtifactStore:
    """Manages .genver/artifacts/ and .genver/runtime/ directories."""

    def __init__(self, genver_root: Path) -> None:
        self.root = genver_root
        self._artifacts = genver_root / "artifacts"
        self._rounds = self._artifacts / "rounds"
        self._runtime = genver_root / "runtime"
        # Ensure dirs exist
        self._artifacts.mkdir(parents=True, exist_ok=True)
        self._rounds.mkdir(parents=True, exist_ok=True)
        self._runtime.mkdir(parents=True, exist_ok=True)

    # --- Artifact documents (spec.md, plan.md, report.md) ---

    def write_artifact(self, name: str, content: str) -> Path:
        path = self._artifacts / name
        path.write_text(content, encoding="utf-8")
        return path

    def read_artifact(self, name: str) -> str | None:
        path = self._artifacts / name
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    # --- Round records (JSON per review step) ---

    def write_round(self, name: str, data: dict[str, Any]) -> Path:
        path = self._rounds / f"{name}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def read_round(self, name: str) -> dict[str, Any] | None:
        path = self._rounds / f"{name}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_rounds(self) -> list[str]:
        return sorted(p.stem for p in self._rounds.glob("*.json"))

    # --- Runtime data (ephemeral, cleared per pipeline run) ---

    def write_runtime(self, name: str, data: dict[str, Any]) -> Path:
        path = self._runtime / f"{name}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def read_runtime(self, name: str) -> dict[str, Any] | None:
        path = self._runtime / f"{name}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def clear_runtime(self) -> None:
        """Remove all runtime data but preserve artifacts and rounds."""
        if self._runtime.exists():
            shutil.rmtree(self._runtime)
        self._runtime.mkdir(parents=True, exist_ok=True)
