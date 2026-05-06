"""Post-start health probe for the gateway daemon."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from src.daemon.base import GatewayService


def _http_probe(url: str) -> tuple[bool, int | None]:
    """Return whether the URL responds, plus an optional reported PID."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
    except (urllib.error.URLError, OSError):
        return False, None

    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return True, None

    pid = data.get("pid")
    return True, pid if isinstance(pid, int) and pid > 0 else None


def _service_pid_alive(service: GatewayService | None) -> int | None:
    """Return the service PID if the service reports one and it's alive."""
    if service is None:
        return None
    st = service.status()
    pid = st.get("pid")
    if not pid:
        return None
    try:
        os.kill(pid, 0)
        return pid
    except (ProcessLookupError, PermissionError):
        return None


def wait_for_gateway(
    host: str,
    port: int,
    timeout_s: float = 15,
    service: GatewayService | None = None,
    path: str = "/",
    require_pid_match: bool = False,
) -> bool:
    """Wait for the gateway to become reachable.

    When ``require_pid_match`` is enabled, the HTTP endpoint must identify
    itself as the same process reported by the service backend. This is used
    to avoid false positives from unrelated processes occupying the port.

    Returns True if gateway is confirmed reachable/alive, False on timeout.
    """
    deadline = time.monotonic() + timeout_s
    path = path if path.startswith("/") else f"/{path}"
    url = f"http://{host}:{port}{path}"

    while time.monotonic() < deadline:
        http_ok, reported_pid = _http_probe(url)
        pid_alive = _service_pid_alive(service)

        if http_ok and require_pid_match:
            if pid_alive and reported_pid == pid_alive:
                return True
        elif http_ok and service is not None:
            if pid_alive:
                return True
        elif http_ok:
            return True

        time.sleep(1)

    # Final attempt: if service PID is alive, report as weak success only when
    # we are not requiring endpoint identity verification.
    return bool(not require_pid_match and _service_pid_alive(service))
