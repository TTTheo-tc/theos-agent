"""Provider factory — single entry point for creating LLM providers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config.schema import Config
    from src.providers.base import LLMProvider
    from src.providers.registry import ProviderSpec


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _resolve_spec_for_model(
    config: "Config",
    model: str,
    *,
    force_provider: str | None = None,
) -> tuple["ProviderSpec | None", str | None]:
    """Resolve a ProviderSpec for the given model.

    Args:
        config: Application configuration.
        model: Model identifier (e.g. "claude-sonnet-4-5").
        force_provider: If set and not "auto", use this provider name directly
            instead of auto-detecting from the model name. Used by make_provider()
            to respect config.agents.defaults.provider. Not used by
            make_provider_for_model() so explicit model callers always auto-detect.

    Returns:
        (spec, provider_name) tuple. Either or both may be None if unresolved.
    """
    from src.providers.registry import find_by_model, find_by_name, normalize_provider_name

    # Forced provider path (from config.agents.defaults.provider)
    normalized_force = normalize_provider_name(force_provider)
    if normalized_force and normalized_force != "auto":
        spec = find_by_name(normalized_force)
        return spec, normalized_force

    # Auto-detect from model name
    spec = find_by_model(model)
    if spec:
        return spec, spec.name

    return None, None


def _build_provider(
    spec: "ProviderSpec | None",
    provider_name: str | None,
    model: str,
    config: "Config",
) -> "LLMProvider":
    """Build a provider instance from a resolved spec and credentials.

    Routes by ``spec.backend``:
    - ``codex``        → OpenAICodexProvider (OAuth, bypasses everything)
    - ``anthropic``    → AnthropicProvider (native SDK)
    - ``openai_compat`` → OpenAICompatProvider (native OpenAI SDK)
    """
    from src.providers.credentials import resolve_credentials

    # Codex: OAuth-based, bypasses everything
    if _is_codex_provider(spec, provider_name, model):
        from src.providers.openai_codex_provider import OpenAICodexProvider

        return OpenAICodexProvider(default_model=model)

    # Anthropic: native SDK
    if spec and spec.backend == "anthropic":
        from src.providers.anthropic_provider import AnthropicProvider

        creds = resolve_credentials(provider_name, config, model, spec=spec)
        if not creds.api_key:
            raise ValueError(
                "No API key configured for Anthropic. "
                "Set one with: theos auth add --provider anthropic --key <key>"
            )
        return AnthropicProvider(
            api_key=creds.api_key,
            api_base=creds.api_base,
            default_model=model,
            extra_headers=creds.extra_headers,
            provider_name=provider_name,
            spec=spec,
        )

    # OpenAI-compatible: native SDK (custom gateway, OpenAI, future others)
    if spec and spec.backend == "openai_compat":
        from src.providers.custom_provider import OpenAICompatProvider

        if provider_name == "custom":
            api_key, api_base, extra_headers = _custom_openai_compat_settings(config, model)
        else:
            api_key, api_base, extra_headers = _configured_openai_compat_settings(
                spec,
                provider_name,
                model,
                config,
            )
        return OpenAICompatProvider(
            api_key=api_key or "no-key",
            api_base=api_base,
            default_model=model,
            model_prefix_to_strip=spec.model_prefix if spec else "",
            extra_headers=extra_headers,
            spec=spec,
        )

    # No matching backend — all providers should use anthropic, openai_compat, or codex.
    backend = spec.backend if spec else "unknown"
    raise ValueError(
        f"No provider implementation for backend={backend!r} (provider={provider_name}, "
        f"model={model}). All providers must use 'anthropic', 'openai_compat', or 'codex' backend."
    )


def _is_codex_provider(
    spec: "ProviderSpec | None",
    provider_name: str | None,
    model: str,
) -> bool:
    return (
        (spec and spec.backend == "codex")
        or provider_name == "openai_codex"
        or model.startswith("openai-codex/")
    )


def _custom_openai_compat_settings(
    config: "Config",
    model: str,
) -> tuple[str | None, str, dict[str, str] | None]:
    """Resolve settings for the explicit custom OpenAI-compatible endpoint."""
    from src.security.secret_refs import resolve_secret_ref

    p = getattr(config.providers, "custom", None)
    api_key = resolve_secret_ref(p.api_key, default="") if p else "no-key"
    api_base = resolve_secret_ref(config.get_api_base(model)) or "http://localhost:8000/v1"
    return api_key, api_base, None


def _configured_openai_compat_settings(
    spec: "ProviderSpec",
    provider_name: str | None,
    model: str,
    config: "Config",
) -> tuple[str | None, str, dict[str, str] | None]:
    """Resolve settings for registry-backed OpenAI-compatible providers."""
    from src.providers.credentials import resolve_credentials

    creds = resolve_credentials(provider_name, config, model, spec=spec)
    if not creds.api_key:
        _raise_missing_credentials(provider_name, spec)

    api_base = creds.api_base or spec.default_api_base or "https://api.openai.com/v1"
    return creds.api_key, api_base, creds.extra_headers


def _raise_missing_credentials(provider_name: str | None, spec: "ProviderSpec") -> None:
    provider_label = provider_name.replace("_", "-") if provider_name else "provider"
    if spec.is_oauth:
        raise ValueError(
            f"No OAuth credentials configured for {provider_label}. "
            f"Run: theos provider login {provider_label}"
        )
    raise ValueError(
        f"No API key configured for {provider_name}. "
        f"Set one with: theos auth add --provider {provider_name} --key <key>"
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def make_provider(config: "Config") -> "LLMProvider":
    """Create the appropriate LLM provider from config.

    Respects config.agents.defaults.provider (may be "auto" or a forced name).
    When ``failover_models`` is configured, wraps the primary in a
    :class:`RecoveryProvider` so all callers automatically get failover.
    Raises ValueError if no API key is configured.
    """
    from src.providers.registry import find_by_model

    model = config.agents.defaults.model
    force_provider = config.agents.defaults.provider

    # Resolve provider name: config match first, then auto-detect
    provider_name = config.get_provider_name(model)
    if not provider_name and force_provider == "auto":
        inferred = find_by_model(model)
        if inferred:
            provider_name = inferred.name

    spec, resolved_name = _resolve_spec_for_model(
        config,
        model,
        force_provider=force_provider,
    )
    # Prefer the config-matched name over spec-resolved name
    final_name = provider_name or resolved_name

    primary = _build_provider(spec, final_name, model, config)

    # Wrap with recovery provider when failover models are configured
    failover_models = config.agents.defaults.failover_models
    if not failover_models:
        return primary

    from src.providers.recovery_provider import RecoveryProvider

    fallbacks = [make_provider_for_model(config, m) for m in failover_models]
    return RecoveryProvider(primary, fallbacks)


def make_provider_for_model(config: "Config", model: str) -> "LLMProvider":
    """Create a provider for a specific model.

    Always auto-detects provider from model name — never uses forced provider.
    Raises ValueError if no provider or API key is available.
    """
    spec, provider_name = _resolve_spec_for_model(config, model)
    if not spec:
        raise ValueError(f"No provider found for model '{model}'")

    return _build_provider(spec, provider_name, model, config)
