import json
import os

from src.config.loader import _apply_proxy_env, _migrate_config, load_config
from src.config.schema import Config


def test_migrate_hooks_dir_to_hooks():
    data = {"hooksDir": "/tmp/.hook"}

    out = _migrate_config(data)

    assert out["hooks"] == "/tmp/.hook"
    assert "hooksDir" not in out


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


def test_ui_dashboard_defaults_to_loopback():
    config = Config()

    assert config.gateway.ui.host == "127.0.0.1"


def test_slim_runtime_defaults():
    config = Config()

    assert config.agents.mode == "single"
    assert config.agents.reflector.enabled is False
    assert config.knowledge_graph.enabled is False
    assert config.gateway.heartbeat.enabled is False
    assert config.gateway.ui.enabled is False
    assert config.tools.browser.enabled is False
    assert config.tools.profile == "minimal"
    assert config.memory.enabled is True
    assert config.memory.flush.enabled is False
    assert config.memory.gc.enabled is False
    assert config.memory.telemetry.recall_enabled is False
