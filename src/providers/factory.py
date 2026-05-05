"""Provider factory — single entry point for creating LLM providers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.auth.oauth_manager import OAuthManager
    from src.config.schema import Config
    from src.providers.base import LLMProvider
    from src.providers.registry import ProviderSpec

_oauth_manager: "OAuthManager | None" = None


def _get_oauth_manager() -> "OAuthManager | None":
    """Lazily create a singleton OAuthManager with built-in plugins."""
    global _oauth_manager
    if _oauth_manager is not None:
        return _oauth_manager
    try:
        from pathlib import Path

        from src.auth.oauth_manager import OAuthManager
        from src.auth.plugins import register_builtin_plugins

        plugins = register_builtin_plugins()
        if not plugins:
            return None
        _oauth_manager = OAuthManager(
            plugins=plugins,
            store_path=Path.home() / ".theos" / "auth-profiles.enc",
        )
        _oauth_manager.start_background_refresh(interval_s=1800)
        return _oauth_manager
    except Exception:
        return None


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
    from src.providers.registry import find_by_model, find_by_name

    # Forced provider path (from config.agents.defaults.provider)
    if force_provider and force_provider != "auto":
        spec = find_by_name(force_provider)
        return spec, force_provider

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
    from src.security.secret_refs import resolve_secret_ref

    # Codex: OAuth-based, bypasses everything
    if (
        (spec and spec.backend == "codex")
        or provider_name == "openai_codex"
        or model.startswith("openai-codex/")
    ):
        from src.providers.openai_codex_provider import OpenAICodexProvider

        return OpenAICodexProvider(default_model=model)

    # Anthropic: native SDK
    if spec and spec.backend == "anthropic":
        from src.providers.anthropic_provider import AnthropicProvider

        creds = resolve_credentials(provider_name, config, model, spec=spec)
        if not creds.api_key and not (spec and spec.is_oauth):
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
            oauth_manager=creds.oauth_manager,
            oauth_profile_id=creds.profile_id,
            spec=spec,
        )

    # OpenAI-compatible: native SDK (custom gateway, OpenAI, future others)
    if spec and spec.backend == "openai_compat":
        from src.providers.custom_provider import OpenAICompatProvider

        if provider_name == "custom":
            # Custom uses config.providers.custom directly (existing pattern)
            p = getattr(config.providers, "custom", None)
            api_key = resolve_secret_ref(p.api_key, default="") if p else "no-key"
            api_base = resolve_secret_ref(config.get_api_base(model)) or "http://localhost:8000/v1"
            extra_headers = None
        else:
            creds = resolve_credentials(provider_name, config, model, spec=spec)
            api_key = creds.api_key
            api_base = (
                creds.api_base
                or (spec.default_api_base if spec else "")
                or "https://api.openai.com/v1"
            )
            extra_headers = creds.extra_headers
            if not api_key:
                raise ValueError(
                    f"No API key configured for {provider_name}. "
                    f"Set one with: theos auth add --provider {provider_name} --key <key>"
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
