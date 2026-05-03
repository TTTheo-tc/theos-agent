"""Tailscale and LAN IP detection for dashboard URL generation."""

from __future__ import annotations

import json
import socket
import subprocess


def detect_magicdns_name() -> str | None:
    """Return the local device MagicDNS name, or None if unavailable."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        dns_name = ((data.get("Self") or {}).get("DNSName") or "").rstrip(".")
        return dns_name or None
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def detect_tailscale_ip() -> str | None:
    """Return the Tailscale IPv4 address, or None if unavailable."""
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            ip = result.stdout.strip().split("\n")[0]
            if ip:
                return ip
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _detect_lan_ip() -> str | None:
    """Return the first non-loopback LAN IP via UDP trick."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            return ip if ip != "127.0.0.1" else None
    except OSError:
        return None


def build_ui_url(port: int, host: str = "0.0.0.0") -> str:
    """Build the best URL for accessing the dashboard.

    When host is 0.0.0.0 (all interfaces), tries Tailscale → LAN → localhost.
    When host is a specific address, uses that directly.
    """
    if host not in ("0.0.0.0", "::"):
        return f"http://{host}:{port}"
    ts_ip = detect_tailscale_ip()
    if ts_ip:
        return f"http://{ts_ip}:{port}"
    lan_ip = _detect_lan_ip()
    if lan_ip:
        return f"http://{lan_ip}:{port}"
    return f"http://localhost:{port}"
