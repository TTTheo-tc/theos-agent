"""End-to-end OAuth integration tests."""

import time
from unittest.mock import MagicMock, patch

from src.auth.oauth_manager import OAuthManager
from src.auth.types import ApiKeyCredential, AuthProfileStore, OAuthCredential


def _now_ms() -> int:
    return int(time.time() * 1000)


class FakeOAuthPlugin:
    provider_id = "fake_oauth"

    def format_api_key(self, cred: OAuthCredential) -> str:
        return cred.access

    def auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def refresh(self, cred: OAuthCredential) -> OAuthCredential | None:
        del cred
        return None

    def login(self, redirect_uri: str) -> OAuthCredential | None:
        del redirect_uri
        return None

    def read_external_credentials(self) -> OAuthCredential | None:
        return None


def test_e2e_oauth_resolve() -> None:
    """Valid OAuth token resolves to API key and plugin headers."""
    cred = OAuthCredential(
        provider="fake_oauth",
        access="tok-test",
        refresh="ref-test",
        expires=_now_ms() + 3600_000,
    )
    store = AuthProfileStore(
        profiles={"fake_oauth:default": cred},
        last_good={"fake_oauth": "fake_oauth:default"},
    )
    plugin = FakeOAuthPlugin()
    mgr = OAuthManager(plugins={"fake_oauth": plugin}, store_path=None)

    with patch.object(mgr, "_load_store", return_value=store):
        result = mgr.resolve("fake_oauth", "fake_oauth:default")

    assert result is not None
    api_key, headers = result
    assert api_key == "tok-test"
    assert headers == {"Authorization": "Bearer tok-test"}


def test_e2e_expired_token_refreshes() -> None:
    """Expired token triggers plugin.refresh and returns fresh token."""
    old_cred = OAuthCredential(
        provider="fake_oauth",
        access="tok-old",
        refresh="ref-old",
        expires=_now_ms() - 60_000,
    )
    fresh_cred = OAuthCredential(
        provider="fake_oauth",
        access="tok-fresh",
        refresh="ref-fresh",
        expires=_now_ms() + 3600_000,
    )
    store = AuthProfileStore(
        profiles={"fake_oauth:default": old_cred},
    )

    plugin = MagicMock(spec=FakeOAuthPlugin)
    plugin.provider_id = "fake_oauth"
    plugin.refresh.return_value = fresh_cred
    plugin.format_api_key.return_value = fresh_cred.access
    plugin.auth_headers.return_value = {"Authorization": f"Bearer {fresh_cred.access}"}

    mgr = OAuthManager(plugins={"fake_oauth": plugin}, store_path=None)
    with (
        patch.object(mgr, "_load_store", return_value=store),
        patch.object(mgr, "_save_credential"),
    ):
        result = mgr.resolve("fake_oauth", "fake_oauth:default")

    assert result is not None
    assert result[0] == "tok-fresh"
    plugin.refresh.assert_called_once()


def test_e2e_api_key_not_affected() -> None:
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


def test_e2e_register_builtin_plugins() -> None:
    """Built-in plugin registry excludes Anthropic but keeps Codex."""
    from src.auth.plugins import register_builtin_plugins

    plugins = register_builtin_plugins()
    assert "anthropic" not in plugins
    assert "openai_codex" in plugins
    # Verify they satisfy the protocol
    from src.auth.oauth_plugin import OAuthPlugin

    for p in plugins.values():
        assert isinstance(p, OAuthPlugin)


def test_try_cached_returns_valid_token() -> None:
    """try_cached() returns token if not expired."""
    cred = OAuthCredential(
        provider="fake_oauth",
        access="tok-valid",
        refresh="ref",
        expires=_now_ms() + 3600_000,
    )
    store = AuthProfileStore(profiles={"fake_oauth:default": cred})
    plugin = FakeOAuthPlugin()
    mgr = OAuthManager(plugins={"fake_oauth": plugin}, store_path=None)

    with patch.object(mgr, "_load_store", return_value=store):
        result = mgr.try_cached("fake_oauth", "fake_oauth:default")

    assert result is not None
    assert result[0] == "tok-valid"


def test_try_cached_returns_none_when_expired() -> None:
    """try_cached() returns None for expired token — no refresh attempted."""
    cred = OAuthCredential(
        provider="fake_oauth",
        access="tok-old",
        refresh="ref",
        expires=_now_ms() - 60_000,
    )
    store = AuthProfileStore(profiles={"fake_oauth:default": cred})
    plugin = MagicMock(spec=FakeOAuthPlugin)
    plugin.provider_id = "fake_oauth"
    mgr = OAuthManager(plugins={"fake_oauth": plugin}, store_path=None)

    with patch.object(mgr, "_load_store", return_value=store):
        result = mgr.try_cached("fake_oauth", "fake_oauth:default")

    assert result is None
    plugin.refresh.assert_not_called()
