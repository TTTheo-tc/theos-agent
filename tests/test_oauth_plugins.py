import json
from unittest.mock import patch

from src.auth.plugins import register_builtin_plugins
from src.auth.plugins.github_copilot import GitHubCopilotPlugin
from src.auth.plugins.openai_codex import OpenAICodexPlugin
from src.auth.types import OAuthCredential

# --- Codex ---


def test_codex_auth_headers() -> None:
    headers = OpenAICodexPlugin().auth_headers("tok-abc")
    assert headers["Authorization"] == "Bearer tok-abc"


def test_codex_format_api_key() -> None:
    cred = OAuthCredential(provider="openai_codex", access="tok", refresh="ref", expires=0)
    assert OpenAICodexPlugin().format_api_key(cred) == "tok"


def test_codex_read_external_credentials(tmp_path) -> None:
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


def test_codex_read_missing() -> None:
    plugin = OpenAICodexPlugin()
    with patch.object(plugin, "_auth_json_path", return_value=None):
        assert plugin.read_external_credentials() is None


# --- GitHub Copilot ---


def test_github_copilot_refresh_preserves_github_access_token() -> None:
    plugin = GitHubCopilotPlugin()
    cred = OAuthCredential(
        provider="github_copilot",
        access="old-copilot-api-key",
        refresh="github-access-token",
        expires=0,
    )

    with patch.object(
        plugin,
        "_exchange_for_api_key",
        return_value={"token": "new-copilot-api-key", "expires_at": 1234},
    ):
        refreshed = plugin.refresh(cred)

    assert refreshed is not None
    assert refreshed.access == "new-copilot-api-key"
    assert refreshed.refresh == "github-access-token"
    assert refreshed.expires == 1234000


def test_github_copilot_read_litellm_store_maps_cached_api_key(tmp_path) -> None:
    token_dir = tmp_path / "github_copilot"
    token_dir.mkdir()
    (token_dir / "access-token").write_text("github-access-token\n", encoding="utf-8")
    (token_dir / "api-key.json").write_text(
        json.dumps({"token": "cached-copilot-api-key", "expires_at": 9999999999}),
        encoding="utf-8",
    )

    cred = GitHubCopilotPlugin()._read_litellm_store(token_dir)

    assert cred is not None
    assert cred.access == "cached-copilot-api-key"
    assert cred.refresh == "github-access-token"
    assert cred.expires == 9999999999000


def test_github_copilot_read_hosts_json_exchanges_oauth_token(tmp_path) -> None:
    hosts_path = tmp_path / "hosts.json"
    hosts_path.write_text(
        json.dumps({"github.com": {"oauth_token": "github-oauth-token"}}),
        encoding="utf-8",
    )
    plugin = GitHubCopilotPlugin()

    with patch.object(
        plugin,
        "_exchange_for_api_key",
        return_value={"token": "host-copilot-api-key", "expires_at": 4321},
    ):
        cred = plugin._read_hosts_json(hosts_path)

    assert cred is not None
    assert cred.access == "host-copilot-api-key"
    assert cred.refresh == "github-oauth-token"
    assert cred.expires == 4321000


# --- Registry ---


def test_register_builtin_plugins() -> None:
    plugins = register_builtin_plugins()
    assert "anthropic" not in plugins
    assert "openai_codex" in plugins
