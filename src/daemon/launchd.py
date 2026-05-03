"""macOS LaunchAgent backend for the gateway daemon."""

from __future__ import annotations

import os
import plistlib
import signal
import subprocess
from pathlib import Path

from src.daemon.base import GatewayService

_DEFAULT_PLIST_DIR = Path.home() / "Library" / "LaunchAgents"
_DEFAULT_LOG_DIR = Path.home() / ".theos" / "logs"


class LaunchdService(GatewayService):
    """Manage the gateway via macOS launchd LaunchAgent."""

    LABEL = "com.theos.gateway"

    def __init__(self) -> None:
        self._plist_path = _DEFAULT_PLIST_DIR / f"{self.LABEL}.plist"
        self._log_dir = _DEFAULT_LOG_DIR

    @property
    def _domain_target(self) -> str:
        return f"gui/{os.getuid()}"

    @property
    def _service_target(self) -> str:
        return f"{self._domain_target}/{self.LABEL}"

    def _build_plist(
        self,
        program_args: list[str],
        env: dict[str, str],
        working_dir: str,
    ) -> dict:
        log_dir = str(self._log_dir)
        return {
            "Label": self.LABEL,
            "ProgramArguments": program_args,
            "WorkingDirectory": working_dir,
            "EnvironmentVariables": env,
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 3,
            "StandardOutPath": f"{log_dir}/gateway-stdout.log",
            "StandardErrorPath": f"{log_dir}/gateway-stderr.log",
        }

    def install(self, program_args, env, working_dir):
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._plist_path.parent.mkdir(parents=True, exist_ok=True)

        plist_data = self._build_plist(program_args, env, working_dir)
        self._plist_path.write_bytes(plistlib.dumps(plist_data))

        # Clean stale registration (ignore errors)
        subprocess.run(
            ["launchctl", "bootout", self._service_target],
            capture_output=True,
        )
        # Register and start
        subprocess.run(
            ["launchctl", "bootstrap", self._domain_target, str(self._plist_path)],
            capture_output=True,
            check=True,
        )

    def uninstall(self):
        self.stop()
        if self._plist_path.exists():
            self._plist_path.unlink()

    def stop(self):
        subprocess.run(
            ["launchctl", "bootout", self._service_target],
            capture_output=True,
        )

    def restart(self):
        # Prefer SIGHUP for graceful restart
        st = self.status()
        pid = st.get("pid")
        if pid:
            try:
                os.kill(pid, signal.SIGHUP)
                return
            except ProcessLookupError:
                pass
        # Fallback: kickstart
        if self.is_loaded():
            subprocess.run(
                ["launchctl", "kickstart", "-k", self._service_target],
                capture_output=True,
            )

    def is_loaded(self) -> bool:
        if not self._plist_path.exists():
            return False
        result = subprocess.run(
            ["launchctl", "print", self._service_target],
            capture_output=True,
        )
        return result.returncode == 0

    def status(self) -> dict:
        if not self.is_loaded():
            return {"pid": None, "state": "not_installed", "loaded": False}
        result = subprocess.run(
            ["launchctl", "print", self._service_target],
            capture_output=True,
            text=True,
        )
        pid = None
        state = "unknown"
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("pid ="):
                try:
                    pid = int(line.split("=", 1)[1].strip())
                except ValueError:
                    pass
            elif line.startswith("state ="):
                state = line.split("=", 1)[1].strip()
        return {"pid": pid, "state": state, "loaded": True}
