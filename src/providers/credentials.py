"""Consolidated credential resolution for LLM providers.

Resolution order:
1. Auth profile store (~/.theos/auth-profiles.enc) -- last_good or first match
2. Config file (providers.<name>.apiKey) -- resolved via resolve_secret_ref
3. Environment variables -- fallback
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config.schema import Config


@dataclass
class ProviderCredentials:
    """Resolved credentials for an LLM provider."""

    api_key: str | None = None
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None


def resolve_credentials(
    provider_name: str | None,
    config: "Config",
    model: str | None = None,
    *,
    spec: Any | None = None,
) -> ProviderCredentials:
    """Resolve credentials for a provider using the three-tier cascade.

    Args:
        provider_name: Registry provider name (e.g. "anthropic", "openrouter").
        config: Application configuration.
        model: Model identifier (used for api_base resolution).
        spec: Optional ProviderSpec for default_api_base fallback.

    Returns:
        ProviderCredentials with resolved api_key, api_base, extra_headers.

    Raises:
        ValueError: If no API key can be resolved for a provider that requires one
                    (non-OAuth, non-Bedrock).
    """
    from src.auth.store import get_credential_for_provider
    from src.security.secret_refs import resolve_mapping_refs, resolve_secret_ref

    p = getattr(config.providers, provider_name, None) if provider_name else None

    # --- Tier 1: Auth profile store ---
    api_key: str | None = None
    if provider_name:
        result = get_credential_for_provider(provider_name)
        if result:
            api_key = result[0]

    # --- Tier 2: Config file ---
    if not api_key and p:
        api_key = resolve_secret_ref(p.api_key) or None

    # --- Tier 3: Environment variable ---
    if not api_key and spec and getattr(spec, "env_key", None):
        api_key = os.environ.get(spec.env_key, "") or None
    if not api_key and spec:
        # env_extras is tuple of tuples: (("ENV_VAR_NAME", "{api_key}"), ...)
        for env_var_name, _ in getattr(spec, "env_extras", ()):
            val = os.environ.get(env_var_name, "")
            if val:
                api_key = val
                break

    # Resolve api_base: config > spec default
    api_base: str | None = None
    if p and p.api_base:
        api_base = resolve_secret_ref(p.api_base)
    elif spec and spec.default_api_base:
        api_base = spec.default_api_base
    elif model:
        api_base = resolve_secret_ref(config.get_api_base(model))

    # Resolve extra_headers
    extra_headers = resolve_mapping_refs(p.extra_headers) if p else None

    return ProviderCredentials(
        api_key=api_key,
        api_base=api_base,
        extra_headers=extra_headers,
    )
