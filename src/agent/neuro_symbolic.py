"""Neuro-symbolic controller — file-level risk assessment via whitelist/blacklist rules."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

# Default blacklist: sensitive system/credential paths
_DEFAULT_BLACKLIST: list[str] = [
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
]

# High-risk patterns that warrant CRITICAL level
_CRITICAL_PATTERNS: list[str] = [
    r"/etc/(?:shadow|passwd|sudoers)",
    r"~?/\.ssh/(?:id_|authorized_keys|config)",
    r"~?/\.gnupg/",
]


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
        self._whitelist = whitelist_patterns or []
        self._blacklist = blacklist_patterns or list(_DEFAULT_BLACKLIST)
        self._critical_re = [re.compile(p) for p in _CRITICAL_PATTERNS]

    def assess_path(self, path: str) -> str:
        """Evaluate a file path and return a risk level string.

        Returns one of: ``"low"``, ``"medium"``, ``"high"``, ``"critical"``.
        """
        if not self.enabled:
            return "low"

        normalized = self._normalize(path)

        # Critical patterns first
        for pat in self._critical_re:
            if pat.search(normalized):
                return "critical"

        # Blacklist check
        for pattern in self._blacklist:
            pattern = self._normalize(pattern)
            if fnmatch.fnmatch(normalized, pattern):
                return "high"
            # Also match basename for non-path patterns like *.env
            if fnmatch.fnmatch(Path(normalized).name, pattern):
                return "high"

        # Whitelist: workspace paths are low risk
        if self._workspace:
            try:
                ws = str(self._workspace.resolve())
                resolved = str(Path(path).resolve())
                if resolved.startswith(ws):
                    return "low"
            except (OSError, ValueError):
                pass

        # Explicit whitelist patterns
        for pattern in self._whitelist:
            pattern = self._normalize(pattern)
            if fnmatch.fnmatch(normalized, pattern):
                return "low"

        # Default: medium for unknown paths
        return "medium"

    def assess_operation(self, operation: str, paths: list[str]) -> str:
        """Assess the combined risk of a file operation across multiple paths.

        Returns the highest risk level found across all paths.
        Write operations get a risk bump compared to reads.
        """
        if not self.enabled:
            return "low"

        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        reverse = {v: k for k, v in risk_order.items()}

        max_risk = 0
        for p in paths:
            level = self.assess_path(p)
            max_risk = max(max_risk, risk_order.get(level, 0))

        # Write/edit operations bump medium → high
        if operation in ("write", "edit", "delete") and max_risk == 1:
            max_risk = 2

        return reverse.get(max_risk, "medium")

    @staticmethod
    def _normalize(path: str) -> str:
        """Expand ~ and normalize path for matching."""
        return str(Path(path).expanduser()) if path.startswith("~") else path
