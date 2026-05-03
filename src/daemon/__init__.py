"""Gateway daemon service — platform dispatch."""

from __future__ import annotations

import sys

from src.daemon.base import GatewayService  # noqa: F401


def resolve_service() -> GatewayService:
    """Return the platform-appropriate GatewayService implementation."""
    if sys.platform == "darwin":
        from src.daemon.launchd import LaunchdService

        return LaunchdService()
    elif sys.platform == "linux":
        from src.daemon.systemd import SystemdService

        return SystemdService()
    else:
        raise NotImplementedError(
            f"Daemon service not supported on {sys.platform}. "
            "Run 'theos gateway' manually instead."
        )
