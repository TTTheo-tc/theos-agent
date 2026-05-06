"""Abstract base class for gateway daemon service backends."""

from __future__ import annotations

import os
import signal
from abc import ABC, abstractmethod


def send_sighup(pid: int | None) -> bool:
    """Send SIGHUP to a running service PID. Return False if it disappeared."""
    if not pid:
        return False
    try:
        os.kill(pid, signal.SIGHUP)
        return True
    except ProcessLookupError:
        return False


class GatewayService(ABC):
    """Platform-specific gateway daemon lifecycle manager."""

    def _try_sighup_restart(self) -> bool:
        """Try graceful restart for the currently reported service PID."""
        return send_sighup(self.status().get("pid"))

    @abstractmethod
    def install(
        self,
        program_args: list[str],
        env: dict[str, str],
        working_dir: str,
    ) -> None:
        """Write service config, register with OS, and start the daemon."""

    @abstractmethod
    def uninstall(self) -> None:
        """Stop the daemon and remove the service config."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the running daemon."""

    @abstractmethod
    def restart(self) -> None:
        """Restart the daemon. Prefers SIGHUP graceful restart when possible."""

    @abstractmethod
    def is_loaded(self) -> bool:
        """Return True if the service is registered with the OS."""

    @abstractmethod
    def status(self) -> dict:
        """Return {"pid": int|None, "state": str, "loaded": bool}."""
