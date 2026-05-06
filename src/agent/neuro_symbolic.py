"""File-level risk assessment via symbolic whitelist/blacklist rules."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

_DEFAULT_BLACKLIST: tuple[str, ...] = (
    "/etc/*",
    "/var/log/*",
    "~/.ssh/*",
    "~/.gnupg/*",
    "~/.aws/*",
    "*.env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.crt",
    "*credentials*",
    "*secret*",
    "*/id_rsa*",
    "*/id_ed25519*",
    "*/.git/config",
    "*/shadow",
    "*/passwd",
)

_CRITICAL_PATTERNS: tuple[str, ...] = (
    r"/etc/(?:shadow|passwd|sudoers)",
    r"~?/\.ssh/(?:id_|authorized_keys|config)",
    r"~?/\.gnupg/",
)
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_RISK_BY_SCORE = {score: risk for risk, score in _RISK_ORDER.items()}
_WRITE_OPERATIONS = {"write", "edit", "delete"}


class FileRiskController:
    """Symbolic rule engine for file-level risk assessment.

    Evaluates file paths against configurable whitelist and blacklist patterns,
    returning a risk level string compatible with ``RiskLevel``.
    """

    def __init__(
        self,
        *,
        workspace: Path | None = None,
        whitelist_patterns: list[str] | None = None,
        blacklist_patterns: list[str] | None = None,
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        self._workspace = workspace
        self._whitelist = tuple(self._normalize(pattern) for pattern in (whitelist_patterns or ()))
        self._blacklist = tuple(self._normalize(pattern) for pattern in (blacklist_patterns or _DEFAULT_BLACKLIST))
        self._critical_re = [re.compile(p) for p in _CRITICAL_PATTERNS]

    @classmethod
    def from_config(
        cls,
        *,
        workspace: Path | None = None,
        config: Any = None,
    ) -> "FileRiskController":
        """Build a controller from the optional orchestrator neuro-symbolic config."""
        return cls(
            workspace=workspace,
            whitelist_patterns=getattr(config, "whitelist_patterns", None),
            blacklist_patterns=getattr(config, "blacklist_patterns", None) or None,
            enabled=getattr(config, "enabled", True),
        )

    def assess_path(self, path: str) -> str:
        """Evaluate a file path and return a risk level string.

        Returns one of: ``"low"``, ``"medium"``, ``"high"``, ``"critical"``.
        """
        if not self.enabled:
            return "low"

        normalized = self._normalize(path)

        for pattern in self._critical_re:
            if pattern.search(normalized):
                return "critical"

        for pattern in self._blacklist:
            if self._matches_pattern(normalized, pattern):
                return "high"

        if self._is_workspace_path(path):
            return "low"

        for pattern in self._whitelist:
            if self._matches_pattern(normalized, pattern):
                return "low"

        return "medium"

    def assess_operation(self, operation: str, paths: list[str]) -> str:
        """Assess the combined risk of a file operation across multiple paths.

        Returns the highest risk level found across all paths.
        Write operations get a risk bump compared to reads.
        """
        if not self.enabled:
            return "low"

        max_risk = 0
        for path in paths:
            level = self.assess_path(path)
            max_risk = max(max_risk, _RISK_ORDER.get(level, 0))

        if operation in _WRITE_OPERATIONS and max_risk == _RISK_ORDER["medium"]:
            max_risk = 2

        return _RISK_BY_SCORE.get(max_risk, "medium")

    def _is_workspace_path(self, path: str) -> bool:
        if not self._workspace:
            return False
        try:
            Path(path).resolve().relative_to(self._workspace.resolve())
        except (OSError, ValueError):
            return False
        return True

    @staticmethod
    def _matches_pattern(path: str, pattern: str) -> bool:
        return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern)

    @staticmethod
    def _normalize(path: str) -> str:
        """Expand ~ and normalize path for matching."""
        return str(Path(path).expanduser()) if path.startswith("~") else path
