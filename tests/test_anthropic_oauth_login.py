"""Tests for Anthropic OAuth PKCE login flow."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from src.auth.plugins.anthropic import CALLBACK_PORT, AnthropicPlugin


def test_login_exchanges_code_for_tokens():
    """login() exchanges auth code for tokens via token endpoint."""
    plugin = AnthropicPlugin()

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "access_token": "sk-ant-oat01-login",
        "refresh_token": "sk-ant-ort01-login",
        "expires_in": 3600,
    }

    with (
        patch.object(plugin, "_run_callback_server") as mock_server,
        patch("httpx.post", return_value=mock_response) as mock_post,
        patch("webbrowser.open"),
    ):
        mock_server.return_value = ("test-code", "test-verifier")
        result = plugin.login(redirect_uri=f"http://localhost:{CALLBACK_PORT}/callback")

    assert result is not None
    assert result.access == "sk-ant-oat01-login"
    assert result.refresh == "sk-ant-ort01-login"
    assert result.provider == "anthropic"
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["json"]["grant_type"] == "authorization_code"
    assert call_kwargs["json"]["code"] == "test-code"
    assert call_kwargs["json"]["code_verifier"]  # internally generated PKCE verifier


def test_login_returns_none_on_no_code():
    """login() returns None if callback server returns no code."""
    plugin = AnthropicPlugin()
    with (
        patch.object(plugin, "_run_callback_server", return_value=(None, None)),
        patch("webbrowser.open"),
    ):
        result = plugin.login(redirect_uri="http://localhost:9527/callback")
    assert result is None


def test_login_returns_none_on_exchange_failure():
    """login() returns None if token exchange HTTP call fails."""
    plugin = AnthropicPlugin()
    with (
        patch.object(plugin, "_run_callback_server", return_value=("code", "verifier")),
        patch("httpx.post", side_effect=httpx.ConnectError("fail")),
        patch("webbrowser.open"),
    ):
        result = plugin.login(redirect_uri="http://localhost:9527/callback")
    assert result is None
