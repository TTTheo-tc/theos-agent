"""Tests for gateway health check."""

from __future__ import annotations

import http.server
import threading
from unittest.mock import MagicMock, patch

from src.daemon.health import wait_for_gateway


def test_health_check_succeeds_when_http_responds_without_service():
    """HTTP responding + no service backend = success (no cross-check possible)."""
    handler = http.server.BaseHTTPRequestHandler
    handler.log_message = lambda *_args: None
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()

    result = wait_for_gateway("127.0.0.1", port, timeout_s=5, service=None)
    srv.server_close()
    assert result is True


def test_health_check_returns_false_on_timeout():
    with patch("src.daemon.health._http_probe", return_value=(False, None)):
        result = wait_for_gateway("127.0.0.1", 1, timeout_s=1)
    assert result is False


def test_health_check_falls_back_to_pid_check():
    """When HTTP fails but service reports a valid PID, return True (weak)."""
    mock_service = MagicMock()
    mock_service.status.return_value = {"pid": 12345, "state": "running", "loaded": True}

    with patch("src.daemon.health.os.kill") as mock_kill:
        mock_kill.return_value = None
        result = wait_for_gateway("127.0.0.1", 1, timeout_s=1, service=mock_service)

    assert result is True


def test_health_check_rejects_port_without_service_pid():
    """HTTP responds but service has no PID = false positive, should fail."""
    handler = http.server.BaseHTTPRequestHandler
    handler.log_message = lambda *_args: None
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    # Keep serving for the duration of the test
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    mock_service = MagicMock()
    mock_service.status.return_value = {"pid": None, "state": "waiting", "loaded": True}

    result = wait_for_gateway("127.0.0.1", port, timeout_s=2, service=mock_service)
    srv.shutdown()
    srv.server_close()
    assert result is False


def test_health_check_succeeds_when_http_and_pid_both_ok():
    """HTTP responds AND service PID alive = confirmed success."""
    handler = http.server.BaseHTTPRequestHandler
    handler.log_message = lambda *_args: None
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    mock_service = MagicMock()
    mock_service.status.return_value = {"pid": 12345, "state": "running", "loaded": True}

    with patch("src.daemon.health.os.kill") as mock_kill:
        mock_kill.return_value = None
        result = wait_for_gateway("127.0.0.1", port, timeout_s=5, service=mock_service)

    srv.shutdown()
    srv.server_close()
    assert result is True


def test_health_check_requires_matching_pid_when_enabled():
    """Health endpoint must identify itself as the service PID."""
    mock_service = MagicMock()
    mock_service.status.return_value = {"pid": 12345, "state": "running", "loaded": True}

    with (
        patch("src.daemon.health._http_probe", return_value=(True, 99999)),
        patch("src.daemon.health.os.kill", return_value=None),
    ):
        result = wait_for_gateway(
            "127.0.0.1",
            8080,
            timeout_s=1,
            service=mock_service,
            path="/api/health",
            require_pid_match=True,
        )

    assert result is False
