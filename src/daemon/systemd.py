"""Linux systemd user service backend for the gateway daemon."""

from __future__ import annotations

import subprocess
from contextlib import suppress
from pathlib import Path

from src.daemon.base import GatewayService

_DEFAULT_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"


def _quote_systemd_value(value: str) -> str:
    """Quote a systemd unit value so spaces and quotes survive parsing."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


class SystemdService(GatewayService):
    """Manage the gateway via systemd user service."""

    UNIT_NAME = "theos-gateway"

    def __init__(self) -> None:
        self._unit_path = _DEFAULT_UNIT_DIR / f"{self.UNIT_NAME}.service"

    def _build_unit(
        self,
        program_args: list[str],
        env: dict[str, str],
        working_dir: str,
    ) -> str:
        exec_start = " ".join(_quote_systemd_value(arg) for arg in program_args)
        env_lines = "\n".join(
            f"Environment={_quote_systemd_value(f'{k}={v}')}" for k, v in env.items()
        )
        return f"""\
[Unit]
Description=TheOS Gateway Service
After=network.target

[Service]
Type=simple
ExecStart={exec_start}
WorkingDirectory={_quote_systemd_value(working_dir)}
{env_lines}
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""

    def _systemctl(self, *args: str, check: bool = False) -> subprocess.CompletedProcess:
        cmd = ["systemctl", "--user", *args]
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=check,
            )
        except FileNotFoundError as exc:
            if check:
                raise RuntimeError(
                    "systemctl --user is not available; run 'theos gateway' manually instead."
                ) from exc
            return subprocess.CompletedProcess(cmd, 127, "", str(exc))

    def install(self, program_args, env, working_dir):
        self._unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit_text = self._build_unit(program_args, env, working_dir)
        was_running = self.is_loaded()
        self._unit_path.write_text(unit_text)

        self._systemctl("daemon-reload")
        if was_running:
            # Service already registered — restart to pick up new unit config
            self._systemctl("restart", self.UNIT_NAME, check=True)
        else:
            self._systemctl("enable", "--now", self.UNIT_NAME, check=True)

    def uninstall(self):
        self._systemctl("disable", "--now", self.UNIT_NAME)
        if self._unit_path.exists():
            self._unit_path.unlink()
        self._systemctl("daemon-reload")

    def stop(self):
        self._systemctl("stop", self.UNIT_NAME)

    def restart(self):
        # Prefer SIGHUP for graceful restart
        if self._try_sighup_restart():
            return
        # Fallback: systemctl restart
        self._systemctl("restart", self.UNIT_NAME, check=True)

    def is_loaded(self) -> bool:
        result = self._systemctl("is-enabled", self.UNIT_NAME)
        return result.returncode == 0

    def status(self) -> dict:
        result = self._systemctl("show", self.UNIT_NAME, "--property=MainPID,ActiveState")
        if result.returncode != 0:
            return {"pid": None, "state": "not_installed", "loaded": False}

        pid = None
        state = "unknown"
        for line in result.stdout.splitlines():
            if line.startswith("MainPID="):
                with suppress(ValueError):
                    p = int(line.split("=", 1)[1])
                    if p > 0:
                        pid = p
            elif line.startswith("ActiveState="):
                state = line.split("=", 1)[1]
        return {"pid": pid, "state": state, "loaded": self.is_loaded()}
