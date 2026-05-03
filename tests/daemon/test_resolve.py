"""Tests for daemon service resolution."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.daemon.base import GatewayService


def test_gateway_service_is_abstract():
    with pytest.raises(TypeError):
        GatewayService()


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
