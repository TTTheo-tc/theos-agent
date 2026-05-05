"""Tests for provider/runtime secret ref resolution."""

from types import SimpleNamespace
from unittest.mock import patch

from src.config.schema import Config
from src.providers.factory import make_provider as _make_provider


def test_make_provider_resolves_secret_ref_config_and_headers():
    config = Config()
    config.agents.defaults.model = "anthropic/claude-sonnet-4-5"
    config.providers.anthropic.api_key = "secret://anthropic"
    config.providers.anthropic.extra_headers = {"X-Api-Key": "secret://header_key"}

    def _resolve(name: str):
        key = {"anthropic": "sk-secret-ref", "header_key": "header-secret"}.get(name)
        return (key, f"{name}:default") if key else None

    with (
        patch("src.auth.store.get_static_credential_for_provider", side_effect=_resolve),
        patch(
            "src.auth.store.get_api_key_for_provider",
            side_effect=lambda name: (_resolve(name) or (None, None))[0],
        ),
        patch(
            "src.providers.anthropic_provider.AnthropicProvider",
            return_value=SimpleNamespace(),
        ) as mock_provider,
    ):
        _make_provider(config)

    kwargs = mock_provider.call_args.kwargs
    assert kwargs["api_key"] == "sk-secret-ref"
    assert kwargs["extra_headers"] == {"X-Api-Key": "header-secret"}


def test_config_get_api_base_uses_provider_default_without_env_setup():
    config = Config()
    config.agents.defaults.model = "moonshot/kimi-k2.5"
    config.providers.moonshot.api_key = "secret://moonshot"

    with patch("src.auth.store.get_api_key_for_provider", return_value="moonshot-key"):
        assert config.get_api_base("moonshot/kimi-k2.5") == "https://api.moonshot.ai/v1"


def test_config_get_provider_keys_includes_all_configured_registry_providers():
    config = Config()
    config.providers.openrouter.api_key = "sk-or-test"
    config.providers.moonshot.api_key = "secret://moonshot"

    with patch(
        "src.auth.store.get_api_key_for_provider",
        side_effect=lambda name: "moonshot-key" if name == "moonshot" else None,
    ):
        keys = config.get_provider_keys()

    assert keys["openrouter"] == "sk-or-test"
    assert keys["moonshot"] == "moonshot-key"
