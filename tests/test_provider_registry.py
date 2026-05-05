"""Tests for provider registry backend field."""

from __future__ import annotations

from src.providers.registry import (
    PROVIDERS,
    core_providers,
    extended_providers,
    find_by_name,
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

    def test_provider_layers_keep_core_small(self):
        assert [spec.name for spec in core_providers()] == ["custom", "anthropic", "openai"]
        assert all(spec.name not in {"custom", "anthropic", "openai"} for spec in extended_providers())

    def test_oauth_provider_layer(self):
        assert [spec.name for spec in oauth_providers()] == ["openai_codex", "github_copilot"]

    def test_ordered_providers_puts_core_first(self):
        names = [spec.name for spec in ordered_providers()]
        assert names[:3] == ["custom", "anthropic", "openai"]
        assert set(names) == {spec.name for spec in PROVIDERS}
