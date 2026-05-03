"""Tests for Linux systemd user service backend."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.daemon.systemd import SystemdService


@pytest.fixture
def svc(tmp_path):
    """SystemdService with unit file directed to tmp_path."""
    s = SystemdService()
    s._unit_path = tmp_path / "theos-gateway.service"
    return s


def test_build_unit_contains_required_sections(svc):
    unit_text = svc._build_unit(
        program_args=["/usr/bin/python3", "-m", "src", "gateway"],
        env={"PATH": "/usr/bin", "HOME": "/home/test"},
        working_dir="/home/test/code/theos-agent",
    )
    assert "[Unit]" in unit_text
    assert "[Service]" in unit_text
    assert "[Install]" in unit_text
    assert "Restart=always" in unit_text
    assert "RestartSec=3" in unit_text
    assert "WantedBy=default.target" in unit_text
    assert '"/usr/bin/python3" "-m" "src" "gateway"' in unit_text
    assert 'WorkingDirectory="/home/test/code/theos-agent"' in unit_text


def test_build_unit_includes_env_vars(svc):
    unit_text = svc._build_unit(
        program_args=["/usr/bin/python3", "-m", "src", "gateway"],
        env={"PATH": "/usr/bin", "MY_KEY": "secret"},
        working_dir="/tmp",
    )
    assert 'Environment="PATH=/usr/bin"' in unit_text
    assert 'Environment="MY_KEY=secret"' in unit_text


def test_build_unit_quotes_spaces_and_quotes(svc):
    unit_text = svc._build_unit(
        program_args=["/opt/My Python/bin/python3", "-m", "src", "gateway"],
        env={"APP_NAME": 'He said "hi"', "PATH": "/tmp/My Bin"},
        working_dir="/home/test/My Projects/theos-agent",
    )

    assert 'ExecStart="/opt/My Python/bin/python3" "-m" "src" "gateway"' in unit_text
    assert 'WorkingDirectory="/home/test/My Projects/theos-agent"' in unit_text
    assert 'Environment="APP_NAME=He said \\"hi\\""' in unit_text
    assert 'Environment="PATH=/tmp/My Bin"' in unit_text


def test_install_writes_unit_and_calls_systemctl(svc):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        svc.install(
            program_args=["/usr/bin/python3", "-m", "src", "gateway"],
            env={"PATH": "/usr/bin"},
            working_dir="/tmp",
        )

    assert svc._unit_path.exists()
    content = svc._unit_path.read_text()
    assert "ExecStart" in content
    assert mock_run.call_count >= 2  # daemon-reload + enable --now


def test_is_loaded_checks_systemctl(svc):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="enabled\n")
        assert svc.is_loaded() is True

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="disabled\n")
        assert svc.is_loaded() is False


def test_restart_prefers_sighup(svc):
    """restart() should try SIGHUP first when PID is available."""
    with (
        patch.object(svc, "status", return_value={"pid": 12345, "state": "active", "loaded": True}),
        patch("os.kill") as mock_kill,
    ):
        svc.restart()
        mock_kill.assert_called_once_with(12345, __import__("signal").SIGHUP)


def test_restart_falls_back_to_systemctl_when_no_pid(svc):
    """restart() falls back to systemctl restart when no PID."""
    with (
        patch.object(
            svc, "status", return_value={"pid": None, "state": "inactive", "loaded": True}
        ),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        svc.restart()
        calls = [c[0][0] for c in mock_run.call_args_list]
        assert any("restart" in cmd for cmd in calls)
