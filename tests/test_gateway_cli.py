"""Tests for gateway CLI subcommands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from src.cli.commands import app

runner = CliRunner()


def test_gateway_stop():
    mock_svc = MagicMock()
    mock_svc.is_loaded.return_value = True
    with patch("src.daemon.resolve_service", return_value=mock_svc):
        result = runner.invoke(app, ["gateway", "stop"])
    assert result.exit_code == 0
    mock_svc.stop.assert_called_once()


def test_gateway_stop_not_installed():
    mock_svc = MagicMock()
    mock_svc.is_loaded.return_value = False
    with patch("src.daemon.resolve_service", return_value=mock_svc):
        result = runner.invoke(app, ["gateway", "stop"])
    assert result.exit_code == 0
    assert "not installed" in result.stdout.lower()


def test_gateway_restart_calls_service():
    mock_svc = MagicMock()
    mock_svc.is_loaded.return_value = True
    with patch("src.daemon.resolve_service", return_value=mock_svc):
        result = runner.invoke(app, ["gateway", "restart"])
    assert result.exit_code == 0
    mock_svc.restart.assert_called_once()


def test_gateway_uninstall():
    mock_svc = MagicMock()
    mock_svc.is_loaded.return_value = True
    with patch("src.daemon.resolve_service", return_value=mock_svc):
        result = runner.invoke(app, ["gateway", "uninstall"])
    assert result.exit_code == 0
    mock_svc.uninstall.assert_called_once()
