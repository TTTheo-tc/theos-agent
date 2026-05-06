"""Three-level autonomy model: ReadOnly / Supervised / Full."""

from __future__ import annotations

import shlex
import time
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger


class AutonomyLevel(str, Enum):
    READONLY = "readonly"
    SUPERVISED = "supervised"
    FULL = "full"


READONLY_SAFE_TOOLS = frozenset(
    {
        "read_file",
        "list_dir",
        "glob",
        "grep",
        "web_search",
        "web_fetch",
        "memory_search",
        "memory_get",
        "notebook_read",
    }
)

READONLY_BLOCKED_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "multi_edit",
        "apply_patch",
        "notebook_edit",
        "bash",
        "process",
        "http_request",
        "message",
        "agent",
        "cron",
    }
)


class ActionTracker:
    """Sliding-window rate limiter for write operations."""

    WINDOW_S = 3600.0

    def __init__(self, max_per_hour: int) -> None:
        self._max = max_per_hour
        self._timestamps: deque[float] = deque()

    def record(self) -> None:
        if self._max <= 0:
            return
        self._timestamps.append(time.monotonic())

    def is_limited(self) -> bool:
        if self._max <= 0:
            return False
        now = time.monotonic()
        while self._timestamps and now - self._timestamps[0] > self.WINDOW_S:
            self._timestamps.popleft()
        return len(self._timestamps) >= self._max


class AutonomyPolicy:
    """Centralized enforcement of autonomy level across tool execution."""

    def __init__(self, config: Any, workspace: Path) -> None:
        self._level = (
            config.level if isinstance(config.level, AutonomyLevel) else AutonomyLevel(config.level)
        )
        self._config = config
        self._workspace = workspace.resolve()
        self._tracker = ActionTracker(getattr(config, "max_actions_per_hour", 0))
        if getattr(config, "max_cost_per_day", 0) > 0:
            logger.warning("max_cost_per_day is configured but not yet enforced (Phase 2)")
        self._write_protected = {
            Path("~/.theos/config.json").expanduser().resolve(),
            Path("~/.theos/auth-profiles.enc").expanduser().resolve(),
        }

    @property
    def level(self) -> AutonomyLevel:
        return self._level

    def check_tool_allowed(self, tool_name: str, risk_level: str) -> str | None:
        del risk_level
        if self._level == AutonomyLevel.READONLY and tool_name in READONLY_BLOCKED_TOOLS:
            return f"Tool '{tool_name}' blocked: autonomy level is readonly"
        return None

    def check_path_allowed(self, path: str) -> str | None:
        resolved = Path(path).expanduser().resolve()
        if resolved in self._write_protected:
            return f"Path blocked: {path} is write-protected"
        for fp in self._config.forbidden_paths:
            forbidden = Path(fp).expanduser().resolve()
            if _is_path_within(resolved, forbidden):
                return f"Path blocked: {path} is in forbidden_paths"
        if self._config.workspace_only and not _is_path_within(resolved, self._workspace):
            return f"Path blocked: {path} is outside workspace"
        return None

    def check_command_allowed(self, command: str) -> str | None:
        if not self._config.allowed_commands:
            return None
        try:
            base_cmd = shlex.split(command)[0] if command.strip() else ""
        except ValueError:
            base_cmd = command.split()[0] if command.split() else ""
        if base_cmd not in self._config.allowed_commands:
            return f"Command blocked: '{base_cmd}' not in allowed_commands"
        return None

    def check_rate_limit(self) -> str | None:
        if self._tracker.is_limited():
            return f"Rate limited: exceeded {self._config.max_actions_per_hour} actions/hour"
        return None

    def record_action(self) -> None:
        self._tracker.record()

    def needs_approval(self, tool_name: str, risk_level: str) -> bool:
        if self._level != AutonomyLevel.SUPERVISED:
            return False
        if tool_name in getattr(self._config, "auto_approve", []):
            return False
        if tool_name in getattr(self._config, "always_ask", []):
            return True
        return risk_level in ("medium", "high", "critical")


def _is_path_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents
