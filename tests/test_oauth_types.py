"""Tests for OAuthCredential type and store integration."""

from __future__ import annotations

from unittest.mock import patch

from src.auth.store import (
    _coerce_store,
    add_oauth_profile,
    get_api_key_for_provider,
    get_oauth_credential_for_provider,
    get_static_credential_for_provider,
)
from src.auth.types import (
    ApiKeyCredential,
    AuthProfileStore,
    OAuthCredential,
    ProfileUsageStats,
)


def test_oauth_credential_roundtrip():
    """Create, dump, and validate an OAuthCredential."""
    cred = OAuthCredential(
        provider="google",
        access="ya29.access-token",
        refresh="1//refresh-token",
        expires=1742400000000,
        scope="openid email",
        client_id="123.apps.googleusercontent.com",
        email="user@gmail.com",
        account_id="acct-42",
    )
    data = cred.model_dump()
    assert data["type"] == "oauth"
    assert data["access"] == "ya29.access-token"
    assert data["refresh"] == "1//refresh-token"
    assert data["expires"] == 1742400000000

    restored = OAuthCredential.model_validate(data)
    assert restored == cred
    assert restored.scope == "openid email"
    assert restored.client_id == "123.apps.googleusercontent.com"
    assert restored.email == "user@gmail.com"
    assert restored.account_id == "acct-42"


def test_auth_profile_store_accepts_oauth():
    """AuthProfileStore can hold a mix of credential types."""
    store = AuthProfileStore(
        profiles={
            "anthropic:default": ApiKeyCredential(provider="anthropic", key="sk-ant-xxx"),
            "google:default": OAuthCredential(
                provider="google",
                access="ya29.token",
                refresh="1//ref",
                expires=9999999999999,
            ),
        }
    )
    assert len(store.profiles) == 2
    assert isinstance(store.profiles["anthropic:default"], ApiKeyCredential)
    assert isinstance(store.profiles["google:default"], OAuthCredential)

    # Roundtrip through JSON
    json_str = store.model_dump_json()
    restored = AuthProfileStore.model_validate_json(json_str)
    assert isinstance(restored.profiles["google:default"], OAuthCredential)
    assert restored.profiles["google:default"].access == "ya29.token"


def test_coerce_store_handles_oauth_type():
    """_coerce_store correctly parses OAuth credentials from raw dicts."""
    raw = {
        "version": 1,
        "profiles": {
            "anthropic:default": {
                "type": "api_key",
                "provider": "anthropic",
                "key": "sk-xxx",
            },
            "google:default": {
                "type": "oauth",
                "provider": "google",
                "access": "ya29.tok",
                "refresh": "1//ref",
                "expires": 1742400000000,
                "scope": "email",
            },
        },
        "last_good": {"google": "google:default"},
        "usage_stats": {},
    }
    store = _coerce_store(raw)
    assert isinstance(store.profiles["google:default"], OAuthCredential)
    assert store.profiles["google:default"].access == "ya29.tok"
    assert store.profiles["google:default"].scope == "email"
    assert isinstance(store.profiles["anthropic:default"], ApiKeyCredential)


def test_get_api_key_for_provider_returns_oauth_access():
    """get_api_key_for_provider returns cred.access for OAuth profiles."""
    fake_store = AuthProfileStore(
        profiles={
            "google:default": OAuthCredential(
                provider="google",
                access="ya29.my-access",
                refresh="1//ref",
                expires=9999999999999,
            ),
        },
        last_good={"google": "google:default"},
    )
    with patch("src.auth.store.load_auth_store", return_value=fake_store):
        result = get_api_key_for_provider("google")
    assert result == "ya29.my-access"


def test_get_api_key_for_provider_oauth_fallback():
    """get_api_key_for_provider falls back to OAuth when no last_good set."""
    fake_store = AuthProfileStore(
        profiles={
            "google:work": OAuthCredential(
                provider="google",
                access="ya29.fallback",
                refresh="1//ref",
                expires=9999999999999,
            ),
        },
        last_good={},
    )
    with patch("src.auth.store.load_auth_store", return_value=fake_store):
        result = get_api_key_for_provider("google")
    assert result == "ya29.fallback"


def test_static_credential_lookup_excludes_oauth_profiles():
    fake_store = AuthProfileStore(
        profiles={
            "google:oauth": OAuthCredential(
                provider="google",
                access="ya29.oauth",
                refresh="1//ref",
                expires=9999999999999,
            ),
            "google:key": ApiKeyCredential(provider="google", key="sk-static"),
        },
        last_good={"google": "google:oauth"},
    )
    with patch("src.auth.store.load_auth_store", return_value=fake_store):
        result = get_static_credential_for_provider("google")

    assert result == ("sk-static", "google:key")


def test_oauth_credential_lookup_normalizes_hyphen_provider_name():
    cred = OAuthCredential(
        provider="github_copilot",
        access="copilot-token",
        refresh="github-token",
        expires=9999999999999,
    )
    fake_store = AuthProfileStore(
        profiles={"github_copilot:default": cred},
        last_good={"github_copilot": "github_copilot:default"},
    )
    with patch("src.auth.store.load_auth_store", return_value=fake_store):
        result = get_oauth_credential_for_provider("github-copilot")

    assert result == (cred, "github_copilot:default")


def test_oauth_lookup_reads_legacy_hyphenated_default():
    cred = OAuthCredential(
        provider="github-copilot",
        access="legacy-copilot-token",
        refresh="legacy-github-token",
        expires=9999999999999,
    )
    fake_store = AuthProfileStore(
        profiles={"github-copilot:manual": cred},
        last_good={"github-copilot": "github-copilot:manual"},
    )
    with patch("src.auth.store.load_auth_store", return_value=fake_store):
        result = get_oauth_credential_for_provider("github_copilot")

    assert result == (cred, "github-copilot:manual")


def test_static_lookup_reads_legacy_hyphenated_profiles():
    fake_store = AuthProfileStore(
        profiles={
            "github-copilot:manual": ApiKeyCredential(
                provider="github-copilot",
                key="legacy-key",
            ),
        },
        last_good={"github-copilot": "github-copilot:manual"},
    )
    with patch("src.auth.store.load_auth_store", return_value=fake_store):
        result = get_static_credential_for_provider("github_copilot")

    assert result == ("legacy-key", "github-copilot:manual")


def test_add_oauth_profile():
    """add_oauth_profile creates a profile and sets last_good."""
    fake_store = AuthProfileStore()

    with (
        patch("src.auth.store.load_auth_store", return_value=fake_store),
        patch("src.auth.store.save_auth_store") as mock_save,
    ):
        pid = add_oauth_profile(
            provider="google",
            access="ya29.new-access",
            refresh="1//new-refresh",
            expires=1742400000000,
            name="work",
            email="user@corp.com",
            scope="openid",
            client_id="client-123",
            account_id="acct-99",
        )

    assert pid == "google:work"
    mock_save.assert_called_once()

    saved_store: AuthProfileStore = mock_save.call_args[0][0]
    cred = saved_store.profiles["google:work"]
    assert isinstance(cred, OAuthCredential)
    assert cred.access == "ya29.new-access"
    assert cred.refresh == "1//new-refresh"
    assert cred.expires == 1742400000000
    assert cred.email == "user@corp.com"
    assert cred.scope == "openid"
    assert cred.client_id == "client-123"
    assert cred.account_id == "acct-99"
    assert saved_store.last_good["google"] == "google:work"
    assert "google:work" in saved_store.usage_stats
    assert isinstance(saved_store.usage_stats["google:work"], ProfileUsageStats)
