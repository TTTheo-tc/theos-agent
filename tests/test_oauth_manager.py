"""Tests for OAuthManager."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from src.auth.oauth_manager import OAuthManager
from src.auth.oauth_plugin import OAuthPlugin
from src.auth.types import (
    ApiKeyCredential,
    AuthProfileStore,
    OAuthCredential,
)


def _make_cred(
    *,
    provider: str = "google",
    access: str = "ya29.valid-token",
    refresh: str = "1//refresh",
    expired: bool = False,
) -> OAuthCredential:
    expires = (
        int((time.time() - 60) * 1000)  # 60s in the past
        if expired
        else int((time.time() + 3600) * 1000)  # 1h in the future
    )
    return OAuthCredential(
        provider=provider,
        access=access,
        refresh=refresh,
        expires=expires,
    )


def _make_plugin() -> MagicMock:
    plugin = MagicMock(spec=OAuthPlugin)
    plugin.provider_id = "google"
    plugin.format_api_key = MagicMock(side_effect=lambda cred: cred.access)
    plugin.auth_headers = MagicMock(side_effect=lambda token: {"Authorization": f"Bearer {token}"})
    return plugin


def test_resolve_returns_token_and_headers_when_valid():
    """Valid (non-expired) cred -> returns (api_key, headers)."""
    cred = _make_cred()
    store = AuthProfileStore(profiles={"google:default": cred})
    plugin = _make_plugin()
    mgr = OAuthManager(plugins={"google": plugin})

    with patch.object(mgr, "_load_store", return_value=store):
        result = mgr.resolve("google", "google:default")

    assert result is not None
    api_key, headers = result
    assert api_key == "ya29.valid-token"
    assert headers == {"Authorization": "Bearer ya29.valid-token"}
    plugin.refresh.assert_not_called()


def test_resolve_returns_none_for_non_oauth():
    """ApiKeyCredential in store -> returns None."""
    store = AuthProfileStore(
        profiles={"anthropic:default": ApiKeyCredential(provider="anthropic", key="sk-xxx")}
    )
    plugin = _make_plugin()
    mgr = OAuthManager(plugins={"anthropic": plugin})

    with patch.object(mgr, "_load_store", return_value=store):
        result = mgr.resolve("anthropic", "anthropic:default")

    assert result is None


def test_resolve_refreshes_expired_token():
    """Expired cred + successful refresh -> returns new token."""
    old_cred = _make_cred(expired=True)
    new_cred = _make_cred(access="ya29.refreshed-token")
    store = AuthProfileStore(profiles={"google:default": old_cred})

    plugin = _make_plugin()
    plugin.refresh.return_value = new_cred

    mgr = OAuthManager(plugins={"google": plugin})

    with (
        patch.object(mgr, "_load_store", return_value=store),
        patch.object(mgr, "_save_credential"),
    ):
        result = mgr.resolve("google", "google:default")

    assert result is not None
    api_key, headers = result
    assert api_key == "ya29.refreshed-token"
    assert headers == {"Authorization": "Bearer ya29.refreshed-token"}
    plugin.refresh.assert_called_once_with(old_cred)


def test_resolve_returns_expired_cred_when_refresh_fails():
    """Expired cred + plugin.refresh returns None -> returns expired cred anyway."""
    old_cred = _make_cred(expired=True)
    store = AuthProfileStore(profiles={"google:default": old_cred})

    plugin = _make_plugin()
    plugin.refresh.return_value = None

    mgr = OAuthManager(plugins={"google": plugin})

    with (
        patch.object(mgr, "_load_store", return_value=store),
        patch.object(mgr, "_save_credential"),
    ):
        result = mgr.resolve("google", "google:default")

    # Returns expired credential so caller can propagate proper error
    assert result is not None
    api_key, headers = result
    assert api_key == old_cred.access


def test_resolve_returns_none_when_no_plugin():
    """OAuth cred in store but no matching plugin -> returns None."""
    cred = _make_cred()
    store = AuthProfileStore(profiles={"google:default": cred})

    mgr = OAuthManager(plugins={})  # no plugins registered

    with patch.object(mgr, "_load_store", return_value=store):
        result = mgr.resolve("google", "google:default")

    assert result is None


def test_protocol_check():
    """Verify a class with all required methods satisfies OAuthPlugin."""
    from tests.test_oauth_plugin import _FakePlugin

    assert isinstance(_FakePlugin(), OAuthPlugin)
