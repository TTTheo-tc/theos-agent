"""Abstract base class for gateway daemon service backends."""

from __future__ import annotations

from abc import ABC, abstractmethod


class GatewayService(ABC):
    """Platform-specific gateway daemon lifecycle manager."""

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
