"""Tests for Anthropic OAuth direct token refresh."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import httpx

from src.auth.plugins.anthropic import AnthropicPlugin
from src.auth.types import OAuthCredential


def _cred(*, expires_offset_ms: int = 3600_000) -> OAuthCredential:
    return OAuthCredential(
        provider="anthropic",
        access="sk-ant-oat01-old",
        refresh="sk-ant-ort01-old",
        expires=int(time.time() * 1000) + expires_offset_ms,
        scope="user:inference",
        email="test@example.com",
    )


def test_refresh_posts_to_token_endpoint():
    """refresh() POSTs to Anthropic token endpoint with refresh_token grant."""
    plugin = AnthropicPlugin()
    cred = _cred()

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "access_token": "sk-ant-oat01-new",
        "refresh_token": "sk-ant-ort01-new",
        "expires_in": 3600,
    }

    with patch("httpx.post", return_value=mock_response) as mock_post:
        result = plugin.refresh(cred)

    assert result is not None
    assert result.access == "sk-ant-oat01-new"
    assert result.refresh == "sk-ant-ort01-new"
    assert result.scope == "user:inference"
    assert result.email == "test@example.com"
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert call_kwargs.kwargs["json"]["grant_type"] == "refresh_token"
    assert call_kwargs.kwargs["json"]["refresh_token"] == "sk-ant-ort01-old"


def test_refresh_preserves_old_refresh_token_if_not_returned():
    """If server omits refresh_token, keep the old one (RFC 6749 s6)."""
    plugin = AnthropicPlugin()
    cred = _cred()

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "access_token": "sk-ant-oat01-new",
        "expires_in": 3600,
    }

    with patch("httpx.post", return_value=mock_response):
        result = plugin.refresh(cred)

    assert result is not None
    assert result.refresh == "sk-ant-ort01-old"


def test_refresh_returns_none_on_http_error():
    """refresh() returns None on HTTP failure, doesn't raise."""
    plugin = AnthropicPlugin()
    cred = _cred()

    with patch(
        "httpx.post",
        side_effect=httpx.HTTPStatusError("401", request=MagicMock(), response=MagicMock()),
    ):
        result = plugin.refresh(cred)

    assert result is None


def test_refresh_returns_none_without_refresh_token():
    """refresh() returns None if credential has no refresh token."""
    plugin = AnthropicPlugin()
    cred = OAuthCredential(provider="anthropic", access="tok", refresh="", expires=0)
    result = plugin.refresh(cred)
    assert result is None


def test_refresh_applies_expires_buffer():
    """Expires is reduced by EXPIRES_BUFFER_S (300s = 5 min)."""
    plugin = AnthropicPlugin()
    cred = _cred()

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "access_token": "new",
        "expires_in": 3600,
    }

    before_ms = int(time.time() * 1000)
    with patch("httpx.post", return_value=mock_response):
        result = plugin.refresh(cred)

    assert result is not None
    expected_min = before_ms + 3300 * 1000 - 1000
    expected_max = before_ms + 3300 * 1000 + 2000
    assert expected_min <= result.expires <= expected_max
