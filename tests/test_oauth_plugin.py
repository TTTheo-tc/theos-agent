"""Tests for OAuthPlugin protocol."""

from __future__ import annotations

from src.auth.oauth_plugin import OAuthPlugin
from src.auth.types import OAuthCredential


class _FakePlugin:
    """Minimal concrete implementation of OAuthPlugin for testing."""

    provider_id: str = "fake"

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


def test_protocol_check():
    """A class implementing all methods passes isinstance(obj, OAuthPlugin)."""
    plugin = _FakePlugin()
    assert isinstance(plugin, OAuthPlugin)


def test_non_conforming_class_fails_protocol():
    """A class missing required methods does NOT satisfy OAuthPlugin."""

    class _Incomplete:
        provider_id: str = "bad"

    assert not isinstance(_Incomplete(), OAuthPlugin)
