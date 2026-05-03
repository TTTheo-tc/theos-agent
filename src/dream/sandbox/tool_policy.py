"""Two-layer tool policy for dream sandbox execution.

Dream sessions use restricted tool access with 4 runtime guards:
- Path guard: all writes must be under sandbox root
- Cost guard: cumulative USD budget cap
- Loop guard: identical tool calls blocked after threshold
- Network rate guard: web query count cap
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar


@dataclass
class ToolPolicyResult:
    """Result of a tool policy check."""

    allowed: bool
    reason: str = ""


@dataclass
class DreamToolPolicy:
    """Enforces dream-specific tool restrictions on top of role allowlist."""

    # Tools with path parameters that need sandbox validation.
    # read_file accepts both "file_path" (primary) and "path" (legacy alias).
    _PATH_PARAM_TOOLS: ClassVar[dict[str, tuple[str, ...]]] = {
        "read_file": ("file_path", "path"),
        "list_dir": ("path",),
        "glob": ("path",),
        "grep": ("path",),
    }

    ALLOWED_TOOLS: ClassVar[frozenset[str]] = frozenset(
        {
            "read_file",
            "list_dir",
            "glob",
            "grep",
            "memory_search",
            "memory_get",
            "web_search",
            "web_fetch",
            "browser",
            "bash",
            "python",
        }
    )

    BLOCKED_TOOLS: ClassVar[frozenset[str]] = frozenset(
        {
            "message",
            "sessions_send",
            "email",
            "webhook",
            "deploy",
            "write_file",
            "edit_file",
            "multi_edit",
            "apply_patch",
            "cron",
            "http_request",
            "notebook_edit",
        }
    )

    sandbox_root: Path
    budget_usd: float = 30.0
    max_web_queries: int = 50
    loop_threshold: int = 5

    # Runtime state
    _cost_used: float = field(default=0.0, init=False)
    _web_query_count: int = field(default=0, init=False)
    _call_hashes: dict[str, int] = field(default_factory=dict, init=False)
    _total_calls: int = field(default=0, init=False)
    stop_reason: str = field(default="", init=False)

    def _set_stop_reason(self, reason: str) -> None:
        if not self.stop_reason:
            self.stop_reason = reason

    def check_tool(self, tool_name: str, params: dict[str, Any]) -> ToolPolicyResult:
        """Two-layer check: allowlist then dream-specific guards."""
        # Layer 1: Tool name check
        if tool_name in self.BLOCKED_TOOLS:
            return ToolPolicyResult(False, f"Tool '{tool_name}' is blocked in dream sandbox")
        if tool_name not in self.ALLOWED_TOOLS:
            return ToolPolicyResult(False, f"Tool '{tool_name}' is not in dream allowlist")

        # Layer 2: Runtime guards

        # Path guard — validate all path parameters are under sandbox_root
        path_params = self._PATH_PARAM_TOOLS.get(tool_name)
        if path_params:
            for param_name in path_params:
                raw_path = params.get(param_name, "")
                if raw_path:
                    result = self._check_path(raw_path)
                    if not result.allowed:
                        self._set_stop_reason("path_violation")
                        return result

        if tool_name in ("bash", "python"):
            cmd = params.get("command", params.get("code", ""))
            if ".." in cmd:
                self._set_stop_reason("path_violation")
                return ToolPolicyResult(False, "Path traversal (..) not allowed in dream sandbox")
            # Block absolute paths outside sandbox in shell commands
            result = self._check_shell_paths(cmd)
            if not result.allowed:
                self._set_stop_reason("path_violation")
                return result

        # Cost guard
        if self._cost_used >= self.budget_usd:
            self._set_stop_reason("budget_exceeded")
            return ToolPolicyResult(
                False,
                f"Budget exceeded: ${self._cost_used:.2f} >= ${self.budget_usd:.2f}",
            )

        # Network rate guard
        if (
            tool_name in ("web_search", "web_fetch")
            and self._web_query_count >= self.max_web_queries
        ):
            self._set_stop_reason("network_limit")
            return ToolPolicyResult(
                False,
                f"Web query limit exceeded: {self._web_query_count} >= {self.max_web_queries}",
            )

        # Loop guard
        call_hash = self._hash_call(tool_name, params)
        if self._call_hashes.get(call_hash, 0) >= self.loop_threshold:
            self._set_stop_reason("loop_guard_stopped")
            return ToolPolicyResult(
                False,
                f"Loop detected: identical call repeated {self.loop_threshold}+ times",
            )

        return ToolPolicyResult(True)

    def record_call(self, tool_name: str, params: dict[str, Any], cost_usd: float = 0.0) -> None:
        """Track call for guards."""
        self._total_calls += 1
        self._cost_used += cost_usd
        if tool_name in ("web_search", "web_fetch"):
            self._web_query_count += 1
        call_hash = self._hash_call(tool_name, params)
        self._call_hashes[call_hash] = self._call_hashes.get(call_hash, 0) + 1

    @property
    def stats(self) -> dict[str, Any]:
        """Current usage statistics."""
        return {
            "total_calls": self._total_calls,
            "cost_used": self._cost_used,
            "web_queries": self._web_query_count,
            "budget_usd": self.budget_usd,
            "budget_remaining": max(0, self.budget_usd - self._cost_used),
        }

    def _check_path(self, raw_path: str) -> ToolPolicyResult:
        """Validate a file path is under sandbox_root."""
        try:
            resolved = Path(raw_path).resolve()
            sandbox = self.sandbox_root.resolve()
            if not resolved.is_relative_to(sandbox):
                return ToolPolicyResult(
                    False,
                    f"Path '{raw_path}' is outside dream sandbox ({sandbox})",
                )
        except (ValueError, OSError):
            return ToolPolicyResult(False, f"Invalid path: '{raw_path}'")
        return ToolPolicyResult(True)

    def _check_shell_paths(self, cmd: str) -> ToolPolicyResult:
        """Check for absolute path escapes in shell/python commands."""
        import re

        sandbox = self.sandbox_root.resolve()
        # Find absolute paths in the command
        for match in re.finditer(r"(?:^|\s)(/[a-zA-Z][^\s;|&]*)", cmd):
            abs_path = match.group(1)
            try:
                resolved = Path(abs_path).resolve()
            except (ValueError, OSError):
                resolved = None
            if resolved is not None:
                # Allow paths under sandbox root
                if resolved.is_relative_to(sandbox):
                    continue
                # Allow safe system paths for read-only commands
                safe_prefixes = (
                    Path("/usr/bin"),
                    Path("/usr/local/bin"),
                    Path("/bin"),
                    Path("/dev/null"),
                )
                if any(resolved.is_relative_to(p) for p in safe_prefixes):
                    continue
            return ToolPolicyResult(
                False,
                f"Absolute path '{abs_path}' is outside dream sandbox",
            )
        return ToolPolicyResult(True)

    @staticmethod
    def _hash_call(tool_name: str, params: dict[str, Any]) -> str:
        raw = f"{tool_name}:{sorted(params.items())}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
