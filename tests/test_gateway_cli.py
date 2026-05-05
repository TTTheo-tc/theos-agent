"""Tests for gateway CLI subcommands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from src.cli.commands import app
from src.config.schema import Config

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


def test_startup_checks_skip_browser_dependency_when_disabled(tmp_path, monkeypatch):
    import builtins

    from src.cli.gateway_cmd import _run_startup_checks

    config = Config()
    config.agents.defaults.workspace = str(tmp_path)
    config.tools.browser.enabled = False
    config.tools.web.search.provider = "duckduckgo"

    imported: list[str] = []
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        imported.append(name)
        if name == "json_repair":
            return object()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    _run_startup_checks(config)

    assert "playwright" not in imported
