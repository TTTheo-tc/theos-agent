"""Memory scope resolution: session_key -> scope_key -> workspace.

Single owner for group/global/genver workspace rules.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from src.utils.helpers import ensure_dir, safe_filename


class MemoryScopeResolver:
    """Resolves memory workspace and scope from session context."""

    def __init__(
        self, workspace: Path, groups_base_dir: Path, group_memory_enabled: bool
    ) -> None:
        self._workspace = workspace
        self._groups_base_dir = groups_base_dir
        self._group_memory_enabled = group_memory_enabled

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def group_memory_enabled(self) -> bool:
        return self._group_memory_enabled

    def get_group_workspace(self, session_key: str) -> Path:
        """Return per-group workspace path, creating if needed."""
        safe_key = safe_filename(session_key.replace(":", "_"))
        group_dir = ensure_dir(self._groups_base_dir / safe_key)
        ensure_dir(group_dir / "memory")
        return group_dir

    def resolve_scope(self, session_key: str | None = None) -> tuple[str, Path]:
        """Resolve index scope key and workspace path."""
        if self._group_memory_enabled and session_key:
            return session_key, self.get_group_workspace(session_key)
        return "__global__", self._workspace

    def resolve_structured_workspace(
        self,
        session_key: str | None,
        *,
        genver_workspace_resolver: Callable[[str], Path | None] | None = None,
    ) -> Path:
        """Resolve workspace for structured memory tools."""
        if session_key and genver_workspace_resolver is not None:
            gw = genver_workspace_resolver(session_key)
            if gw is not None:
                return gw
        if self._group_memory_enabled and session_key:
            return self.get_group_workspace(session_key)
        return self._workspace
