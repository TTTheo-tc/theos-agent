"""Tests for provider registry backend field."""

from __future__ import annotations

from src.providers.registry import PROVIDERS, find_by_name


class TestProviderBackend:
    def test_anthropic_has_backend(self):
        spec = find_by_name("anthropic")
        assert spec is not None
        assert spec.backend == "anthropic"

    def test_openai_has_backend(self):
        spec = find_by_name("openai")
        assert spec is not None
        assert spec.backend == "openai_compat"

    def test_custom_has_backend(self):
        spec = find_by_name("custom")
        assert spec is not None
        assert spec.backend == "openai_compat"

    def test_copilot_uses_openai_compat(self):
        spec = find_by_name("github_copilot")
        assert spec is not None
        assert spec.backend == "openai_compat"
        assert spec.default_api_base == "https://api.githubcopilot.com"

    def test_all_providers_have_backend(self):
        for spec in PROVIDERS:
            assert spec.backend in (
                "anthropic",
                "openai_compat",
                "codex",
            ), f"{spec.name} has no valid backend"

    def test_model_prefix_field_exists(self):
        spec = find_by_name("openrouter")
        assert spec is not None
        assert hasattr(spec, "model_prefix")
