"""Tests for daemon service resolution."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.daemon.base import GatewayService, send_sighup


def test_gateway_service_is_abstract():
    with pytest.raises(TypeError):
        GatewayService()


def test_send_sighup_returns_true_when_signal_sent():
    with patch("src.daemon.base.os.kill") as mock_kill:
        assert send_sighup(12345) is True

    mock_kill.assert_called_once_with(12345, __import__("signal").SIGHUP)


def test_send_sighup_returns_false_when_process_is_gone():
    with patch("src.daemon.base.os.kill", side_effect=ProcessLookupError):
        assert send_sighup(12345) is False


def test_send_sighup_does_not_swallow_permission_errors():
    with (
        patch("src.daemon.base.os.kill", side_effect=PermissionError),
        pytest.raises(PermissionError),
    ):
        send_sighup(12345)


def test_resolve_service_darwin():
    from src.daemon import resolve_service

    with patch("sys.platform", "darwin"):
        svc = resolve_service()
    from src.daemon.launchd import LaunchdService

    assert isinstance(svc, LaunchdService)


def test_resolve_service_linux():
    from src.daemon import resolve_service

    with patch("sys.platform", "linux"):
        svc = resolve_service()
    from src.daemon.systemd import SystemdService

    assert isinstance(svc, SystemdService)


def test_resolve_service_unsupported():
    from src.daemon import resolve_service

    with patch("sys.platform", "win32"):
        with pytest.raises(NotImplementedError):
            resolve_service()
