"""Thin ToolRegistry wrapper that enforces DreamToolPolicy per call."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.dream.output.artifacts import ArtifactTracker
from src.dream.output.dream_eval import DreamEval
from src.dream.sandbox.tool_policy import DreamToolPolicy

# Fixed per-call cost for web tools (v1.1 — no complex estimation).
_WEB_TOOL_COST = 0.01


class DreamToolRegistry:
    """Wraps a base ToolRegistry, gating every call through DreamToolPolicy.

    Only implements the 3 methods run_tool_loop() calls:
    get_definitions(), get(), execute().
    """

    def __init__(
        self,
        base: Any,  # ToolRegistry — use Any to avoid circular import
        policy: DreamToolPolicy,
        eval_tracker: DreamEval,
        artifacts: ArtifactTracker,
        sandbox_root: Path,
    ) -> None:
        self._base = base
        self._policy = policy
        self._eval = eval_tracker
        self._artifacts = artifacts
        self._sandbox_root = sandbox_root
        self._sandbox_snapshot: dict[str, float] = {}
        self._snapshot_sandbox()

    def _snapshot_sandbox(self) -> None:
        """Record current mtime of all files in sandbox."""
        self._sandbox_snapshot = {}
        if self._sandbox_root.exists():
            for f in self._sandbox_root.rglob("*"):
                if f.is_file():
                    self._sandbox_snapshot[str(f)] = f.stat().st_mtime

    def get_definitions(self) -> list[dict[str, Any]]:
        """Return schemas only for tools in the dream allowlist that exist in base."""
        defs = []
        for tool_name in sorted(DreamToolPolicy.ALLOWED_TOOLS):
            tool = self._base.get(tool_name)
            if tool is not None:
                defs.append(tool.to_schema())
        return defs

    def get(self, name: str) -> Any:
        """Delegate to base, return None if not in allowlist."""
        if name not in DreamToolPolicy.ALLOWED_TOOLS:
            return None
        return self._base.get(name)

    async def execute(self, name: str, params: dict[str, Any], context: Any = None) -> str:
        """Check policy, execute via base, record call, track artifacts."""
        result = self._policy.check_tool(name, params)
        if not result.allowed:
            return f"[Dream policy] Rejected: {result.reason}"

        # Force shell/python tools to run inside sandbox root, not main workspace.
        if name in ("bash", "python"):
            params = {**params, "working_dir": str(self._sandbox_root)}

        output = await self._base.execute(name, params, context=context)

        cost = _WEB_TOOL_COST if name in ("web_search", "web_fetch", "browser") else 0.0
        self._policy.record_call(name, params, cost_usd=cost)

        self._detect_new_artifacts()

        return output

    def _detect_new_artifacts(self) -> None:
        """Scan sandbox for new or modified files since last snapshot."""
        if not self._sandbox_root.exists():
            return
        for f in self._sandbox_root.rglob("*"):
            if not f.is_file():
                continue
            fstr = str(f)
            mtime = f.stat().st_mtime
            if fstr not in self._sandbox_snapshot or mtime > self._sandbox_snapshot[fstr]:
                self._artifacts.add(f, artifact_type="file", description="auto-detected")
                self._sandbox_snapshot[fstr] = mtime
