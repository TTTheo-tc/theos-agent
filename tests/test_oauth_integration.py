"""End-to-end OAuth integration tests."""

import time
from unittest.mock import MagicMock, patch

from src.auth.oauth_manager import OAuthManager
from src.auth.plugins.anthropic import AnthropicPlugin
from src.auth.types import ApiKeyCredential, AuthProfileStore, OAuthCredential


def _now_ms() -> int:
    return int(time.time() * 1000)


def test_e2e_anthropic_oauth_resolve():
    """Valid OAuth token resolves to API key with x-api-key header."""
    cred = OAuthCredential(
        provider="anthropic",
        access="sk-ant-oat01-test",
        refresh="sk-ant-ort01-test",
        expires=_now_ms() + 3600_000,
    )
    store = AuthProfileStore(
        profiles={"anthropic:default": cred},
        last_good={"anthropic": "anthropic:default"},
    )
    plugin = AnthropicPlugin()
    mgr = OAuthManager(plugins={"anthropic": plugin}, store_path=None)

    with patch.object(mgr, "_load_store", return_value=store):
        result = mgr.resolve("anthropic", "anthropic:default")

    assert result is not None
    api_key, headers = result
    assert api_key == "sk-ant-oat01-test"
    # auth_headers returns x-api-key for proper token propagation after refresh
    assert headers == {"x-api-key": "sk-ant-oat01-test"}


def test_e2e_expired_token_refreshes():
    """Expired token triggers plugin.refresh and returns fresh token."""
    old_cred = OAuthCredential(
        provider="anthropic",
        access="sk-ant-oat01-old",
        refresh="sk-ant-ort01-old",
        expires=_now_ms() - 60_000,
    )
    fresh_cred = OAuthCredential(
        provider="anthropic",
        access="sk-ant-oat01-fresh",
        refresh="sk-ant-ort01-fresh",
        expires=_now_ms() + 3600_000,
    )
    store = AuthProfileStore(
        profiles={"anthropic:default": old_cred},
    )

    plugin = MagicMock(spec=AnthropicPlugin)
    plugin.provider_id = "anthropic"
    plugin.refresh.return_value = fresh_cred
    plugin.format_api_key.return_value = fresh_cred.access
    plugin.auth_headers.return_value = {"Authorization": f"Bearer {fresh_cred.access}"}

    mgr = OAuthManager(plugins={"anthropic": plugin}, store_path=None)
    with (
        patch.object(mgr, "_load_store", return_value=store),
        patch.object(mgr, "_save_credential"),
    ):
        result = mgr.resolve("anthropic", "anthropic:default")

    assert result is not None
    assert result[0] == "sk-ant-oat01-fresh"
    plugin.refresh.assert_called_once()


def test_e2e_api_key_not_affected():
    """Standard API key credentials are not touched by OAuthManager."""
    store = AuthProfileStore(
        profiles={
            "openai:default": ApiKeyCredential(provider="openai", key="sk-abc"),
        },
    )
    mgr = OAuthManager(plugins={}, store_path=None)
    with patch.object(mgr, "_load_store", return_value=store):
        result = mgr.resolve("openai", "openai:default")
    assert result is None  # not an OAuth credential


def test_e2e_register_builtin_plugins():
    """Built-in plugin registry excludes Anthropic but keeps Codex."""
    from src.auth.plugins import register_builtin_plugins

    plugins = register_builtin_plugins()
    assert "anthropic" not in plugins
    assert "openai_codex" in plugins
    # Verify they satisfy the protocol
    from src.auth.oauth_plugin import OAuthPlugin

    for p in plugins.values():
        assert isinstance(p, OAuthPlugin)


def test_try_cached_returns_valid_token():
    """try_cached() returns token if not expired."""
    cred = OAuthCredential(
        provider="anthropic",
        access="sk-ant-oat01-valid",
        refresh="ref",
        expires=_now_ms() + 3600_000,
    )
    store = AuthProfileStore(profiles={"anthropic:default": cred})
    plugin = AnthropicPlugin()
    mgr = OAuthManager(plugins={"anthropic": plugin}, store_path=None)

    with patch.object(mgr, "_load_store", return_value=store):
        result = mgr.try_cached("anthropic", "anthropic:default")

    assert result is not None
    assert result[0] == "sk-ant-oat01-valid"


def test_try_cached_returns_none_when_expired():
    """try_cached() returns None for expired token — no refresh attempted."""
    cred = OAuthCredential(
        provider="anthropic",
        access="sk-ant-oat01-old",
        refresh="ref",
        expires=_now_ms() - 60_000,
    )
    store = AuthProfileStore(profiles={"anthropic:default": cred})
    plugin = MagicMock(spec=AnthropicPlugin)
    plugin.provider_id = "anthropic"
    mgr = OAuthManager(plugins={"anthropic": plugin}, store_path=None)

    with patch.object(mgr, "_load_store", return_value=store):
        result = mgr.try_cached("anthropic", "anthropic:default")

    assert result is None
    plugin.refresh.assert_not_called()
