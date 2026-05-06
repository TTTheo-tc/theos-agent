"""Tests for provider registry backend field."""

from __future__ import annotations

from src.providers.registry import (
    PROVIDERS,
    core_providers,
    extended_providers,
    find_by_name,
    iter_model_matches,
    oauth_providers,
    ordered_providers,
)


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

    def test_find_by_name_accepts_hyphenated_provider_names(self):
        spec = find_by_name("github-copilot")
        assert spec is not None
        assert spec.name == "github_copilot"

    def test_all_providers_have_backend(self):
        for spec in PROVIDERS:
            assert spec.backend in (
                "anthropic",
                "openai_compat",
                "codex",
            ), f"{spec.name} has no valid backend"

    def test_provider_names_are_unique(self):
        names = [spec.name for spec in PROVIDERS]
        assert len(names) == len(set(names))

    def test_default_field_intent_is_pinned_for_plain_openai_provider(self):
        spec = find_by_name("openai")
        assert spec is not None
        assert spec.backend == "openai_compat"
        assert spec.model_prefix == ""
        assert spec.default_api_base == ""
        assert spec.is_gateway is False
        assert spec.is_local is False
        assert spec.is_oauth is False
        assert spec.strip_model_prefix is False
        assert spec.supports_prompt_caching is False

    def test_model_prefix_field_exists(self):
        spec = find_by_name("openrouter")
        assert spec is not None
        assert hasattr(spec, "model_prefix")

    def test_provider_layers_keep_core_small(self):
        assert [spec.name for spec in core_providers()] == ["custom", "anthropic", "openai"]
        assert all(spec.name not in {"custom", "anthropic", "openai"} for spec in extended_providers())

    def test_oauth_provider_layer(self):
        assert [spec.name for spec in oauth_providers()] == ["openai_codex", "github_copilot"]

    def test_ordered_providers_puts_core_first(self):
        names = [spec.name for spec in ordered_providers()]
        assert names[:3] == ["custom", "anthropic", "openai"]
        assert set(names) == {spec.name for spec in PROVIDERS}

    def test_iter_model_matches_can_include_gateway_prefix(self):
        names = [
            spec.name
            for spec in iter_model_matches(
                "openrouter/anthropic/claude-sonnet-4-6",
                include_gateways=True,
            )
        ]

        assert names[0] == "openrouter"
        assert "anthropic" in names
