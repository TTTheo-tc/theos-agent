"""Integration test: factory → provider → chat."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.providers.anthropic_provider import AnthropicProvider
from src.providers.custom_provider import OpenAICompatProvider
from src.providers.factory import _build_provider


class TestFactoryRouting:
    def test_anthropic_backend_creates_anthropic_provider(self):
        spec = MagicMock(
            backend="anthropic",
            name="anthropic",
            model_prefix="",
            is_oauth=False,
            supports_prompt_caching=True,
        )
        with patch("src.providers.credentials.resolve_credentials") as mock_creds:
            mock_creds.return_value = MagicMock(
                api_key="sk-test",
                api_base=None,
                extra_headers=None,
            )
            provider = _build_provider(spec, "anthropic", "claude-sonnet-4-6", MagicMock())
        assert isinstance(provider, AnthropicProvider)

    def test_openai_backend_creates_compat_provider(self):
        spec = MagicMock(
            backend="openai_compat",
            name="openai",
            model_prefix="",
            default_api_base="https://api.openai.com/v1",
            is_oauth=False,
        )
        with patch("src.providers.credentials.resolve_credentials") as mock_creds:
            mock_creds.return_value = MagicMock(
                api_key="sk-test",
                api_base=None,
                extra_headers=None,
            )
            provider = _build_provider(spec, "openai", "gpt-4o", MagicMock())
        assert isinstance(provider, OpenAICompatProvider)

    def test_openai_backend_requires_api_key(self):
        spec = MagicMock(
            backend="openai_compat",
            name="openai",
            model_prefix="",
            default_api_base="https://api.openai.com/v1",
            is_oauth=False,
        )
        with patch("src.providers.credentials.resolve_credentials") as mock_creds:
            mock_creds.return_value = MagicMock(
                api_key=None,
                api_base=None,
                extra_headers=None,
            )
            with pytest.raises(ValueError, match="theos auth add --provider openai"):
                _build_provider(spec, "openai", "gpt-4o", MagicMock())

    def test_oauth_openai_compat_backend_requires_login(self):
        spec = MagicMock(
            backend="openai_compat",
            name="github_copilot",
            model_prefix="github_copilot",
            default_api_base="https://api.githubcopilot.com",
            is_oauth=True,
        )
        with patch("src.providers.credentials.resolve_credentials") as mock_creds:
            mock_creds.return_value = MagicMock(
                api_key=None,
                api_base=None,
                extra_headers=None,
            )
            with pytest.raises(ValueError, match="theos provider login github-copilot"):
                _build_provider(
                    spec,
                    "github_copilot",
                    "github_copilot/gpt-5.3-codex",
                    MagicMock(),
                )

    def test_custom_backend_uses_config(self):
        spec = MagicMock(backend="openai_compat", name="custom", model_prefix="")
        config = MagicMock()
        config.providers.custom.api_key = "sk-custom"
        config.get_api_base.return_value = "http://gateway/v1"
        with patch(
            "src.security.secret_refs.resolve_secret_ref",
            side_effect=lambda x, **kw: x or kw.get("default", ""),
        ):
            provider = _build_provider(spec, "custom", "my-model", config)
        assert isinstance(provider, OpenAICompatProvider)
        assert provider.api_key == "sk-custom"
        assert provider.api_base == "http://gateway/v1"
        assert provider._extra_headers == {}

    def test_oauth_openai_compat_backend_passes_token_headers(self):
        spec = MagicMock(
            backend="openai_compat",
            name="github_copilot",
            model_prefix="github_copilot",
            default_api_base="https://api.githubcopilot.com",
            is_oauth=True,
        )
        with patch("src.providers.credentials.resolve_credentials") as mock_creds:
            mock_creds.return_value = MagicMock(
                api_key="copilot-token",
                api_base="https://api.githubcopilot.com",
                extra_headers={
                    "Authorization": "Bearer copilot-token",
                    "x-app": "theos",
                },
            )
            provider = _build_provider(
                spec,
                "github_copilot",
                "github_copilot/gpt-5.3-codex",
                MagicMock(),
            )

        assert isinstance(provider, OpenAICompatProvider)
        assert provider.api_key == "copilot-token"
        assert provider.api_base == "https://api.githubcopilot.com"
        assert provider._extra_headers == {
            "Authorization": "Bearer copilot-token",
            "x-app": "theos",
        }

    def test_unknown_backend_raises(self):
        spec = MagicMock(
            backend="unknown_backend",
            name="fake",
            model_prefix="",
            is_oauth=False,
        )
        with pytest.raises(ValueError, match="No provider implementation"):
            _build_provider(spec, "fake", "fake-model", MagicMock())

    def test_codex_backend(self):
        spec = MagicMock(backend="codex", name="openai_codex")
        from src.providers.openai_codex_provider import OpenAICodexProvider

        provider = _build_provider(spec, "openai_codex", "openai-codex/model", MagicMock())
        assert isinstance(provider, OpenAICodexProvider)
