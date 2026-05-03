"""Tests for FileRiskController — neuro-symbolic file risk assessment."""

from pathlib import Path

from src.agent.neuro_symbolic import FileRiskController


def test_workspace_path_is_low_risk(tmp_path: Path):
    ctrl = FileRiskController(workspace=tmp_path)
    assert ctrl.assess_path(str(tmp_path / "src" / "main.py")) == "low"


def test_blacklisted_env_file_is_high():
    ctrl = FileRiskController()
    assert ctrl.assess_path("/app/.env") == "high"
    assert ctrl.assess_path("/app/config.env") == "high"


def test_ssh_key_is_critical():
    ctrl = FileRiskController()
    assert ctrl.assess_path("~/.ssh/id_rsa") == "critical"
    assert ctrl.assess_path("~/.ssh/authorized_keys") == "critical"


def test_etc_shadow_is_critical():
    ctrl = FileRiskController()
    assert ctrl.assess_path("/etc/shadow") == "critical"
    assert ctrl.assess_path("/etc/passwd") == "critical"


def test_pem_file_is_high():
    ctrl = FileRiskController()
    assert ctrl.assess_path("/app/server.pem") == "high"
    assert ctrl.assess_path("/app/private.key") == "high"


def test_unknown_path_is_medium():
    ctrl = FileRiskController()
    assert ctrl.assess_path("/opt/some/random/file.txt") == "medium"


def test_whitelist_pattern():
    ctrl = FileRiskController(whitelist_patterns=["/tmp/*"])
    assert ctrl.assess_path("/tmp/scratch.txt") == "low"


def test_disabled_returns_low():
    ctrl = FileRiskController(enabled=False)
    assert ctrl.assess_path("/etc/shadow") == "low"


def test_assess_operation_write_bumps_medium_to_high():
    ctrl = FileRiskController()
    # Unknown path is medium for read, but high for write
    assert ctrl.assess_operation("read", ["/opt/x.txt"]) == "medium"
    assert ctrl.assess_operation("write", ["/opt/x.txt"]) == "high"


def test_assess_operation_uses_highest_risk():
    ctrl = FileRiskController(workspace=Path("/app"))
    paths = ["/app/safe.py", "/etc/shadow"]
    assert ctrl.assess_operation("read", paths) == "critical"


def test_custom_blacklist():
    ctrl = FileRiskController(blacklist_patterns=["*.secret"])
    assert ctrl.assess_path("/app/database.secret") == "high"
    # Default patterns not present when custom provided
    assert ctrl.assess_path("/app/.env") != "high"
