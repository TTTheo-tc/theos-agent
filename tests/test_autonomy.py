from __future__ import annotations

from pathlib import Path

from src.security.autonomy import ActionTracker, AutonomyLevel, AutonomyPolicy


def test_autonomy_level_values():
    assert AutonomyLevel.READONLY == "readonly"
    assert AutonomyLevel.SUPERVISED == "supervised"
    assert AutonomyLevel.FULL == "full"


def test_action_tracker_not_limited_when_disabled():
    tracker = ActionTracker(max_per_hour=0)
    for _ in range(100):
        tracker.record()
    assert not tracker.is_limited()


def test_action_tracker_limits_after_max():
    tracker = ActionTracker(max_per_hour=3)
    tracker.record()
    tracker.record()
    tracker.record()
    assert tracker.is_limited()


def test_action_tracker_window_slides():
    tracker = ActionTracker(max_per_hour=2)
    tracker.record()
    tracker.record()
    assert tracker.is_limited()
    tracker._timestamps[0] -= 3700
    assert not tracker.is_limited()


class _FakeConfig:
    level = AutonomyLevel.SUPERVISED
    workspace_only = True
    forbidden_paths = ["/etc", "/sys"]
    allowed_commands: list[str] = []
    max_actions_per_hour = 0
    max_cost_per_day = 0.0
    auto_approve: list[str] = []
    always_ask = ["bash", "write_file"]


def test_readonly_blocks_write_tools(tmp_path):
    cfg = _FakeConfig()
    cfg.level = AutonomyLevel.READONLY
    policy = AutonomyPolicy(cfg, tmp_path)
    err = policy.check_tool_allowed("write_file", "medium")
    assert err is not None
    assert "readonly" in err


def test_readonly_allows_read_tools(tmp_path):
    cfg = _FakeConfig()
    cfg.level = AutonomyLevel.READONLY
    policy = AutonomyPolicy(cfg, tmp_path)
    assert policy.check_tool_allowed("read_file", "low") is None
    assert policy.check_tool_allowed("glob", "low") is None


def test_supervised_needs_approval_always_ask(tmp_path):
    cfg = _FakeConfig()
    policy = AutonomyPolicy(cfg, tmp_path)
    assert policy.needs_approval("bash", "medium") is True
    assert policy.needs_approval("write_file", "medium") is True


def test_supervised_no_approval_for_auto_approve(tmp_path):
    cfg = _FakeConfig()
    cfg.auto_approve = ["bash"]
    policy = AutonomyPolicy(cfg, tmp_path)
    assert policy.needs_approval("bash", "medium") is False


def test_full_no_approval(tmp_path):
    cfg = _FakeConfig()
    cfg.level = AutonomyLevel.FULL
    policy = AutonomyPolicy(cfg, tmp_path)
    assert policy.needs_approval("bash", "high") is False


def test_forbidden_paths_blocked(tmp_path):
    cfg = _FakeConfig()
    policy = AutonomyPolicy(cfg, tmp_path)
    err = policy.check_path_allowed("/etc/passwd")
    assert err is not None
    assert "forbidden" in err.lower()


def test_workspace_only_enforced(tmp_path):
    cfg = _FakeConfig()
    policy = AutonomyPolicy(cfg, tmp_path)
    err = policy.check_path_allowed("/tmp/outside")
    assert err is not None
    assert "outside workspace" in err.lower()


def test_workspace_path_allowed(tmp_path):
    cfg = _FakeConfig()
    policy = AutonomyPolicy(cfg, tmp_path)
    target = tmp_path / "subdir" / "file.txt"
    assert policy.check_path_allowed(str(target)) is None


def test_config_write_protected(tmp_path):
    cfg = _FakeConfig()
    policy = AutonomyPolicy(cfg, tmp_path)
    config_path = str(Path("~/.theos/config.json").expanduser())
    err = policy.check_path_allowed(config_path)
    assert err is not None
    assert "write-protected" in err.lower()


def test_command_allowlist(tmp_path):
    cfg = _FakeConfig()
    cfg.allowed_commands = ["git", "pytest"]
    policy = AutonomyPolicy(cfg, tmp_path)
    assert policy.check_command_allowed("git status") is None
    err = policy.check_command_allowed("rm -rf /")
    assert err is not None
    assert "not in allowed_commands" in err


def test_rate_limit_enforced(tmp_path):
    cfg = _FakeConfig()
    cfg.max_actions_per_hour = 2
    policy = AutonomyPolicy(cfg, tmp_path)
    policy.record_action()
    policy.record_action()
    err = policy.check_rate_limit()
    assert err is not None
    assert "rate limited" in err.lower()
