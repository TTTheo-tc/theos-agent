import json
import os
from pathlib import Path

from src.config.loader import _apply_proxy_env, _migrate_config, load_config, save_config
from src.config.schema import Config


def test_migrate_hooks_dir_to_hooks():
    data = {"hooksDir": "/tmp/.hook"}

    out = _migrate_config(data)

    assert out["hooks"] == "/tmp/.hook"
    assert "hooksDir" not in out


def test_migrate_config_does_not_mutate_input():
    data = {
        "hooksDir": "/tmp/.hook",
        "tools": {"exec": {"restrictToWorkspace": True, "timeout": 5}},
    }

    out = _migrate_config(data)

    assert out["hooks"] == "/tmp/.hook"
    assert out["tools"]["restrictToWorkspace"] is True
    assert data == {
        "hooksDir": "/tmp/.hook",
        "tools": {"exec": {"restrictToWorkspace": True, "timeout": 5}},
    }


def test_migrate_config_keeps_explicit_current_fields() -> None:
    data = {
        "hooks": "/current/hooks",
        "hooksDir": "/legacy/hooks",
        "tools": {
            "restrictToWorkspace": False,
            "exec": {"restrictToWorkspace": True},
        },
    }

    out = _migrate_config(data)

    assert out["hooks"] == "/current/hooks"
    assert out["tools"]["restrictToWorkspace"] is False


def test_migrate_hooks_dir_snake_case_to_hooks():
    data = {"hooks_dir": "/tmp/.hook"}

    out = _migrate_config(data)

    assert out["hooks"] == "/tmp/.hook"
    assert "hooks_dir" not in out


def test_apply_proxy_env_sets_missing_env_vars(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)

    class _Config:
        proxy = "http://127.0.0.1:7890"

    _apply_proxy_env(_Config())

    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert os.environ["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert os.environ["https_proxy"] == "http://127.0.0.1:7890"
    assert os.environ["http_proxy"] == "http://127.0.0.1:7890"


def test_load_config_applies_saved_proxy_to_env(tmp_path, monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"proxy": "http://127.0.0.1:7890"}), encoding="utf-8")

    config = load_config(config_path)

    assert config.proxy == "http://127.0.0.1:7890"
    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:7890"


def test_load_config_does_not_apply_socks_proxy_to_http_env(tmp_path, monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"proxy": "socks5h://127.0.0.1:7890"}), encoding="utf-8")

    config = load_config(config_path)

    assert config.proxy == "socks5h://127.0.0.1:7890"
    assert "HTTPS_PROXY" not in os.environ
    assert "HTTP_PROXY" not in os.environ
    assert "https_proxy" not in os.environ
    assert "http_proxy" not in os.environ


def test_ui_dashboard_defaults_to_loopback():
    config = Config()

    assert config.gateway.ui.host == "127.0.0.1"


def test_slim_runtime_defaults():
    config = Config()

    assert config.agents.mode == "single"
    assert config.agents.team_enabled is False
    assert config.agents.genver_enabled is False
    assert config.knowledge_graph.enabled is False
    assert config.gateway.heartbeat.enabled is False
    assert config.gateway.ui.enabled is False
    assert config.learning.enabled is False
    assert config.tools.browser.enabled is False
    assert config.tools.profile == "minimal"
    assert config.memory.enabled is True
    assert config.memory.flush.enabled is False
    assert config.memory.gc.enabled is False
    assert config.memory.telemetry.recall_enabled is False


def test_theos_env_prefix_overrides_defaults(monkeypatch) -> None:
    monkeypatch.setenv("THEOS_AGENTS__DEFAULTS__MODEL", "openai-codex/gpt-5.5")

    config = Config()

    assert config.agents.defaults.model == "openai-codex/gpt-5.5"


def test_save_config_writes_only_non_default_values_by_default(tmp_path: Path):
    config_path = tmp_path / "config.json"
    config = Config()
    config.agents.defaults.model = "openai-codex/gpt-5.5"
    config.tools.profile = "full"
    config.proxy = "http://127.0.0.1:7890"

    save_config(config, config_path)

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert raw == {
        "agents": {"defaults": {"model": "openai-codex/gpt-5.5"}},
        "tools": {"profile": "full"},
        "proxy": "http://127.0.0.1:7890",
    }
    loaded = load_config(config_path)
    assert loaded.agents.defaults.model == "openai-codex/gpt-5.5"
    assert loaded.tools.profile == "full"
    assert loaded.memory.enabled is True


def test_save_config_can_write_full_schema(tmp_path: Path):
    config_path = tmp_path / "config.json"

    save_config(Config(), config_path, compact=False)

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert raw["agents"]["mode"] == "single"
    assert raw["tools"]["profile"] == "minimal"
    assert raw["memory"]["enabled"] is True
