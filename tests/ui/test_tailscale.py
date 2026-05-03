from __future__ import annotations

import json
from unittest.mock import patch

from src.ui.tailscale import build_ui_url, detect_magicdns_name, detect_tailscale_ip


def test_detect_magicdns_name_success():
    payload = {"Self": {"DNSName": "theos.tailnet.ts.net."}}
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = json.dumps(payload)
        name = detect_magicdns_name()
        assert name == "theos.tailnet.ts.net"


def test_detect_magicdns_name_missing():
    with patch("subprocess.run", side_effect=FileNotFoundError):
        name = detect_magicdns_name()
        assert name is None


def test_detect_tailscale_ip_success():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "100.68.1.2\n"
        ip = detect_tailscale_ip()
        assert ip == "100.68.1.2"


def test_detect_tailscale_ip_not_installed():
    with patch("subprocess.run", side_effect=FileNotFoundError):
        ip = detect_tailscale_ip()
        assert ip is None


def test_detect_tailscale_ip_timeout():
    import subprocess

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tailscale", 3)):
        ip = detect_tailscale_ip()
        assert ip is None


def test_build_ui_url_tailscale():
    with patch("src.ui.tailscale.detect_tailscale_ip", return_value="100.68.1.2"):
        url = build_ui_url(8080)
        assert url == "http://100.68.1.2:8080"


def test_build_ui_url_no_tailscale():
    with patch("src.ui.tailscale.detect_tailscale_ip", return_value=None):
        url = build_ui_url(8080)
        assert url.startswith("http://")
        assert ":8080" in url


def test_build_ui_url_specific_host():
    """When host is not 0.0.0.0, use it directly without detection."""
    url = build_ui_url(8080, host="127.0.0.1")
    assert url == "http://127.0.0.1:8080"


def test_build_ui_url_specific_host_skips_tailscale():
    """Tailscale detection should not run when host is specific."""
    with patch("src.ui.tailscale.detect_tailscale_ip") as mock:
        url = build_ui_url(8080, host="192.168.1.5")
        mock.assert_not_called()
        assert url == "http://192.168.1.5:8080"
