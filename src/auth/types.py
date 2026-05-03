"""Auth profile types for TheOS.

Profile IDs use the format "provider:name" (e.g. "anthropic:default").
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class ApiKeyCredential(BaseModel):
    """API key credential."""

    type: Literal["api_key"] = "api_key"
    provider: str
    key: str = ""
    email: str | None = None


class TokenCredential(BaseModel):
    """Static bearer token credential (not auto-refreshed)."""

    type: Literal["token"] = "token"
    provider: str
    token: str = ""
    expires: int | None = None  # ms since epoch
    email: str | None = None


class OAuthCredential(BaseModel):
    """OAuth credential with auto-refresh support."""

    type: Literal["oauth"] = "oauth"
    provider: str
    access: str  # OAuth access token
    refresh: str  # OAuth refresh token
    expires: int  # Expiry timestamp (ms since epoch)
    scope: str | None = None
    client_id: str | None = None
    email: str | None = None
    account_id: str | None = None


# Union type — discriminated by the "type" field
AuthProfileCredential = Annotated[
    ApiKeyCredential | TokenCredential | OAuthCredential,
    Field(discriminator="type"),
]


class ProfileUsageStats(BaseModel):
    """Per-profile usage statistics."""

    last_used: int | None = None  # ms since epoch
    error_count: int = 0


class AuthProfileStore(BaseModel):
    """Root model for the auth profile store."""

    version: int = 1
    profiles: dict[str, ApiKeyCredential | TokenCredential | OAuthCredential] = Field(
        default_factory=dict
    )
    # provider → preferred profile_id
    last_good: dict[str, str] = Field(default_factory=dict)
    usage_stats: dict[str, ProfileUsageStats] = Field(default_factory=dict)
