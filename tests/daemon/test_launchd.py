"""Tests for macOS LaunchAgent service backend."""

from __future__ import annotations

import plistlib
from unittest.mock import MagicMock, patch

import pytest

from src.daemon.launchd import LaunchdService


@pytest.fixture
def svc(tmp_path):
    """LaunchdService with plist directed to tmp_path."""
    s = LaunchdService()
    s._plist_path = tmp_path / "com.theos.gateway.plist"
    s._log_dir = tmp_path / "logs"
    return s


def test_build_plist_contains_required_keys(svc):
    plist_data = svc._build_plist(
        program_args=["/usr/bin/python3", "-m", "src", "gateway"],
        env={"PATH": "/usr/bin", "HOME": "/Users/test"},
        working_dir="/Users/test/code/theos-agent",
    )
    assert plist_data["Label"] == "com.theos.gateway"
    assert plist_data["RunAtLoad"] is True
    assert plist_data["KeepAlive"] is True
    assert plist_data["ThrottleInterval"] == 3
    assert plist_data["ProgramArguments"] == ["/usr/bin/python3", "-m", "src", "gateway"]
    assert plist_data["WorkingDirectory"] == "/Users/test/code/theos-agent"
    assert plist_data["EnvironmentVariables"]["PATH"] == "/usr/bin"


def test_build_plist_sets_log_paths(svc):
    plist_data = svc._build_plist(
        program_args=["/usr/bin/python3", "-m", "src", "gateway"],
        env={},
        working_dir="/tmp",
    )
    assert "StandardOutPath" in plist_data
    assert "StandardErrorPath" in plist_data
    assert "gateway-stdout.log" in plist_data["StandardOutPath"]
    assert "gateway-stderr.log" in plist_data["StandardErrorPath"]


def test_install_writes_plist_and_calls_launchctl(svc):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        svc.install(
            program_args=["/usr/bin/python3", "-m", "src", "gateway"],
            env={"PATH": "/usr/bin"},
            working_dir="/tmp",
        )

    assert svc._plist_path.exists()
    plist_data = plistlib.loads(svc._plist_path.read_bytes())
    assert plist_data["Label"] == "com.theos.gateway"
    assert mock_run.call_count >= 2  # bootout + bootstrap


def test_is_loaded_returns_true_when_plist_exists_and_launchctl_succeeds(svc):
    svc._plist_path.parent.mkdir(parents=True, exist_ok=True)
    svc._plist_path.write_bytes(plistlib.dumps({"Label": "com.theos.gateway"}))
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert svc.is_loaded() is True


def test_is_loaded_returns_false_when_no_plist(svc):
    assert svc.is_loaded() is False


def test_restart_prefers_sighup(svc):
    """restart() should try SIGHUP first when PID is available."""
    with (
        patch.object(
            svc, "status", return_value={"pid": 12345, "state": "running", "loaded": True}
        ),
        patch("os.kill") as mock_kill,
    ):
        svc.restart()
        mock_kill.assert_called_once_with(12345, __import__("signal").SIGHUP)


def test_restart_falls_back_to_kickstart_when_no_pid(svc):
    """restart() falls back to launchctl kickstart when no PID."""
    with (
        patch.object(svc, "status", return_value={"pid": None, "state": "waiting", "loaded": True}),
        patch.object(svc, "is_loaded", return_value=True),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        svc.restart()
        args = mock_run.call_args[0][0]
        assert "kickstart" in args
