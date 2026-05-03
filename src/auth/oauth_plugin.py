"""OAuthPlugin protocol -- implement this for each provider."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.auth.types import OAuthCredential


@runtime_checkable
class OAuthPlugin(Protocol):
    """Each OAuth provider implements this protocol.

    All methods are synchronous (httpx for HTTP, subprocess for CLI).
    Async callers wrap with run_in_executor().
    """

    provider_id: str

    def format_api_key(self, cred: OAuthCredential) -> str:
        """Format the credential for API use. Default: return cred.access."""
        ...

    def auth_headers(self, token: str) -> dict[str, str]:
        """Return provider-specific auth headers for the given token."""
        ...

    def refresh(self, cred: OAuthCredential) -> OAuthCredential | None:
        """Exchange refresh_token for new tokens. Returns None on failure."""
        ...

    def login(self, redirect_uri: str) -> OAuthCredential | None:
        """Full OAuth authorization flow. Returns None on failure/cancel."""
        ...

    def read_external_credentials(self) -> OAuthCredential | None:
        """Read credentials from external source. Returns None if unavailable."""
        ...
