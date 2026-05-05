import json
from pathlib import Path

from typer.testing import CliRunner

from src.cli.commands import app
from src.config.loader import load_config, save_config
from src.config.schema import Config
from src.security.autonomy import AutonomyLevel

runner = CliRunner()


def _read_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_config_full_access_enables_personal_dev_permissions(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("src.config.loader.get_config_path", lambda: config_path)

    result = runner.invoke(app, ["config", "full-access"])

    assert result.exit_code == 0
    data = _read_config(config_path)
    assert data["tools"]["profile"] == "full"
    assert data["tools"]["browser"]["enabled"] is True
    assert data["security"]["networkIsolated"] is False
    assert data["security"]["autonomy"]["level"] == "full"
    assert data["security"]["autonomy"]["workspaceOnly"] is False
    assert data["security"]["autonomy"]["forbiddenPaths"] == []
    assert data["security"]["autonomy"]["alwaysAsk"] == []
    loaded = load_config(config_path)
    assert loaded.tools.restrict_to_workspace is False
    assert loaded.security.autonomy.allowed_commands == []
    assert loaded.agents.orchestrator.approval_gate.enabled is False
    assert loaded.agents.orchestrator.neuro_symbolic.enabled is False
    assert "Full-access development mode enabled" in result.stdout


def test_config_safe_restores_conservative_permissions(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("src.config.loader.get_config_path", lambda: config_path)

    config = Config()
    config.tools.profile = "full"
    config.tools.browser.enabled = True
    config.security.network_isolated = False
    config.security.autonomy.level = AutonomyLevel.FULL
    config.security.autonomy.workspace_only = False
    config.security.autonomy.forbidden_paths = []
    config.security.autonomy.always_ask = []
    config.agents.orchestrator.approval_gate.enabled = True
    config.agents.orchestrator.approval_gate.auto_approve = ["low", "medium"]
    config.agents.orchestrator.neuro_symbolic.enabled = True
    config.agents.orchestrator.neuro_symbolic.whitelist_patterns = ["*"]
    save_config(config)

    result = runner.invoke(app, ["config", "safe"])

    assert result.exit_code == 0
    data = _read_config(config_path)
    assert data == {}
    loaded = load_config(config_path)
    assert loaded.tools.profile == "minimal"
    assert loaded.tools.browser.enabled is False
    assert loaded.security.network_isolated is True
    assert loaded.security.autonomy.level == AutonomyLevel.SUPERVISED
    assert loaded.security.autonomy.workspace_only is True
    assert loaded.security.autonomy.forbidden_paths == [
        "/etc",
        "/sys",
        "/proc",
        "/boot",
        "~/.ssh",
    ]
    assert loaded.security.autonomy.always_ask == ["bash", "write_file", "edit_file"]
    assert loaded.agents.orchestrator.approval_gate.enabled is False
    assert loaded.agents.orchestrator.approval_gate.auto_approve == ["low"]
    assert loaded.agents.orchestrator.neuro_symbolic.enabled is False
    assert loaded.agents.orchestrator.neuro_symbolic.whitelist_patterns == []
    assert "Safe default mode restored" in result.stdout


def test_config_compact_rewrites_existing_config_without_changing_values(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("src.config.loader.get_config_path", lambda: config_path)

    config = Config()
    config.agents.defaults.model = "openai-codex/gpt-5.5"
    config.tools.profile = "full"
    save_config(config, config_path, compact=False)

    result = runner.invoke(app, ["config", "compact"])

    assert result.exit_code == 0
    data = _read_config(config_path)
    assert data == {
        "agents": {"defaults": {"model": "openai-codex/gpt-5.5"}},
        "tools": {"profile": "full"},
    }
    loaded = load_config(config_path)
    assert loaded.agents.defaults.model == "openai-codex/gpt-5.5"
    assert loaded.tools.profile == "full"
    assert loaded.memory.enabled is True


def test_config_features_lists_current_values(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("src.config.loader.get_config_path", lambda: config_path)

    config = Config()
    config.agents.team_enabled = True
    config.tools.profile = "full"
    save_config(config, config_path)

    result = runner.invoke(app, ["config", "features"])

    assert result.exit_code == 0
    assert "agents.teamEnabled" in result.stdout
    assert "tools.profile" in result.stdout
    assert "full" in result.stdout
    assert "TheOS Config Features" in result.stdout


def test_config_show_full_masks_sensitive_values(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("src.config.loader.get_config_path", lambda: config_path)

    config = Config()
    config.channels.telegram.enabled = True
    config.channels.telegram.token = "bot-secret-token"
    save_config(config, config_path)

    result = runner.invoke(app, ["config", "show", "--full"])

    assert result.exit_code == 0
    assert '"memory"' in result.stdout
    assert '"token": "***"' in result.stdout
    assert "bot-secret-token" not in result.stdout


def test_config_show_defaults_to_compact_output(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr("src.config.loader.get_config_path", lambda: config_path)

    config = Config()
    config.tools.profile = "full"
    save_config(config, config_path)

    result = runner.invoke(app, ["config", "show"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data == {"tools": {"profile": "full"}}
