"""Tests for runtime secret:// config resolution."""

from types import SimpleNamespace

from src.channels.manager import ChannelManager
from src.channels.registry import _resolve_dotpath
from src.config.schema import Config
from src.security.secret_refs import (
    resolve_data_secret_refs,
    resolve_inline_mapping_refs,
    resolve_inline_secret_refs,
    resolve_secret_ref,
)


def test_resolve_secret_ref_prefers_auth_store(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.auth.store.get_api_key_for_provider",
        lambda name: "resolved-key" if name == "anthropic" else None,
    )

    assert resolve_secret_ref("secret://anthropic") == "resolved-key"


def test_resolve_secret_ref_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.setattr("src.auth.store.get_api_key_for_provider", lambda _: None)
    monkeypatch.setenv("TELEGRAM_BOT", "tg-secret")

    assert resolve_secret_ref("secret://telegram-bot") == "tg-secret"


def test_resolve_inline_secret_refs_replaces_embedded_refs() -> None:
    result = resolve_inline_secret_refs(
        "Bearer secret://mcp_token and secret://missing",
        lambda name: "xyz" if name == "mcp_token" else None,
    )

    assert result == "Bearer xyz and secret://missing"


def test_resolve_inline_secret_refs_handles_full_value_and_multiple_refs() -> None:
    secrets = {"first": "one", "second": "two"}

    assert resolve_inline_secret_refs("secret://first", secrets.get) == "one"
    assert resolve_inline_secret_refs("secret://first,secret://second", secrets.get) == "one,two"


def test_resolve_inline_mapping_refs_resolves_string_mapping() -> None:
    secrets = {"token": "xyz"}

    resolved = resolve_inline_mapping_refs(
        {"Authorization": "Bearer secret://token", "Mode": "test"},
        secrets.get,
    )

    assert resolved == {"Authorization": "Bearer xyz", "Mode": "test"}


def test_resolve_data_secret_refs_preserves_model_type(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.auth.store.get_api_key_for_provider",
        lambda name: "bot-token" if name == "telegram" else None,
    )
    config = Config()
    config.channels.telegram.enabled = True
    config.channels.telegram.token = "secret://telegram"

    resolved = resolve_data_secret_refs(config.channels.telegram)

    assert resolved.__class__ is config.channels.telegram.__class__
    assert resolved.token == "bot-token"


def test_channel_registry_resolves_extra_kwargs(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.auth.store.get_api_key_for_provider",
        lambda name: "groq-key" if name == "groq" else None,
    )
    config = Config()
    config.providers.groq.api_key = "secret://groq"

    assert _resolve_dotpath(config, "providers.groq.api_key") == "groq-key"


def test_channel_manager_passes_resolved_channel_config(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.auth.store.get_api_key_for_provider",
        lambda name: "tg-token" if name == "telegram" else None,
    )

    captured: dict[str, str] = {}

    class _FakeChannel:
        def __init__(self, config, bus, **kwargs):
            del bus, kwargs
            captured["token"] = config.token
            self.is_running = False

    fake_module = SimpleNamespace(TelegramChannel=_FakeChannel)
    monkeypatch.setattr("importlib.import_module", lambda _: fake_module)

    config = Config()
    config.channels.telegram.enabled = True
    config.channels.telegram.token = "secret://telegram"

    ChannelManager(config, bus=SimpleNamespace())

    assert captured["token"] == "tg-token"
