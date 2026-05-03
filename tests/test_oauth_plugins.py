import json
from unittest.mock import MagicMock, patch

from src.auth.plugins import register_builtin_plugins
from src.auth.plugins.anthropic import AnthropicPlugin
from src.auth.plugins.openai_codex import OpenAICodexPlugin
from src.auth.types import OAuthCredential

# --- Anthropic ---


def test_anthropic_auth_headers():
    plugin = AnthropicPlugin()
    headers = plugin.auth_headers("sk-ant-oat01-abc")
    # Returns x-api-key so callers can override stale keys after refresh
    assert headers == {"x-api-key": "sk-ant-oat01-abc"}


def test_anthropic_format_api_key():
    plugin = AnthropicPlugin()
    cred = OAuthCredential(provider="anthropic", access="tok", refresh="ref", expires=0)
    assert plugin.format_api_key(cred) == "tok"


def test_anthropic_read_external_returns_none():
    """read_external_credentials returns None (no keychain dependency)."""
    assert AnthropicPlugin().read_external_credentials() is None


def test_anthropic_refresh_calls_token_endpoint():
    """refresh() calls the Anthropic token endpoint, not keychain."""
    old = OAuthCredential(provider="anthropic", access="old", refresh="ref", expires=0)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "access_token": "new-tok",
        "refresh_token": "new-ref",
        "expires_in": 3600,
    }
    with patch("httpx.post", return_value=mock_resp):
        result = AnthropicPlugin().refresh(old)
    assert result is not None
    assert result.access == "new-tok"


# --- Codex ---


def test_codex_auth_headers():
    headers = OpenAICodexPlugin().auth_headers("tok-abc")
    assert headers["Authorization"] == "Bearer tok-abc"


def test_codex_format_api_key():
    cred = OAuthCredential(provider="openai_codex", access="tok", refresh="ref", expires=0)
    assert OpenAICodexPlugin().format_api_key(cred) == "tok"


def test_codex_read_external_credentials(tmp_path):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "access_token": "codex-tok",
                "refresh_token": "codex-ref",
                "expires_at": 1773791071982,
                "account_id": "acct-123",
            }
        )
    )
    plugin = OpenAICodexPlugin()
    with patch.object(plugin, "_auth_json_path", return_value=auth_file):
        cred = plugin.read_external_credentials()
    assert cred is not None
    assert cred.access == "codex-tok"
    assert cred.account_id == "acct-123"


def test_codex_read_missing():
    plugin = OpenAICodexPlugin()
    with patch.object(plugin, "_auth_json_path", return_value=None):
        assert plugin.read_external_credentials() is None


# --- Registry ---


def test_register_builtin_plugins():
    plugins = register_builtin_plugins()
    assert "anthropic" not in plugins
    assert "openai_codex" in plugins
