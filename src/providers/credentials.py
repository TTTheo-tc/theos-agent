"""Consolidated credential resolution for LLM providers.

Resolution order:
1. Auth profile store (~/.theos/auth-profiles.enc) -- last_good or first match
2. Config file (providers.<name>.apiKey) -- resolved via resolve_secret_ref
3. Environment variables -- fallback
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
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
    from src.auth.store import get_oauth_credential_for_provider, get_static_credential_for_provider
    from src.providers.registry import normalize_provider_name
    from src.security.secret_refs import resolve_mapping_refs, resolve_secret_ref

    provider_name = normalize_provider_name(provider_name)
    p = getattr(config.providers, provider_name, None) if provider_name else None
    extra_headers = resolve_mapping_refs(p.extra_headers) if p else None

    oauth_credentials = _resolve_oauth_credentials(
        provider_name=provider_name,
        provider_config=p,
        spec=spec,
        extra_headers=extra_headers,
        get_oauth_credential_for_provider=get_oauth_credential_for_provider,
    )
    if oauth_credentials is not None:
        return oauth_credentials

    # --- Tiers 1-3: static auth profile, config value, then environment ---
    api_key = _resolve_api_key(
        provider_name=provider_name,
        provider_config=p,
        spec=spec,
        get_static_credential_for_provider=get_static_credential_for_provider,
        resolve_secret_ref=resolve_secret_ref,
    )

    # Resolve api_base: config > spec default
    api_base, had_provider_base = _configured_api_base(p, spec)
    if not api_base and not had_provider_base and model:
        api_base = resolve_secret_ref(config.get_api_base(model))

    return ProviderCredentials(
        api_key=api_key,
        api_base=api_base,
        extra_headers=extra_headers,
    )


def _resolve_oauth_credentials(
    *,
    provider_name: str | None,
    provider_config: Any | None,
    spec: Any | None,
    extra_headers: dict[str, str] | None,
    get_oauth_credential_for_provider: Any,
) -> ProviderCredentials | None:
    """Resolve OAuth-backed credentials, preserving provider-specific headers."""
    if not provider_name or not spec or not getattr(spec, "is_oauth", False):
        return None

    oauth_result = get_oauth_credential_for_provider(provider_name)
    if not oauth_result:
        return None

    _cred, profile_id = oauth_result
    from src.auth.oauth_manager import OAuthManager
    from src.auth.plugins import register_builtin_plugins

    mgr = OAuthManager(
        plugins=register_builtin_plugins(),
        store_path=Path.home() / ".theos" / "auth-profiles.enc",
    )
    resolved = mgr.resolve(provider_name, profile_id)
    if not resolved:
        return None

    api_key, oauth_headers = resolved
    return ProviderCredentials(
        api_key=api_key,
        api_base=_configured_api_base(provider_config, spec)[0],
        extra_headers={**oauth_headers, **(extra_headers or {})},
    )


def _resolve_api_key(
    *,
    provider_name: str | None,
    provider_config: Any | None,
    spec: Any | None,
    get_static_credential_for_provider: Any,
    resolve_secret_ref: Any,
) -> str | None:
    """Resolve API key by auth profile, config value, then environment."""
    api_key: str | None = None

    if provider_name:
        result = get_static_credential_for_provider(provider_name)
        if result:
            api_key = result[0]

    if not api_key and provider_config:
        api_key = resolve_secret_ref(provider_config.api_key) or None

    if not api_key:
        api_key = _resolve_env_api_key(spec)

    return api_key


def _resolve_env_api_key(spec: Any | None) -> str | None:
    if spec and getattr(spec, "env_key", None):
        api_key = os.environ.get(spec.env_key, "") or None
        if api_key:
            return api_key

    if spec:
        # env_extras is tuple of tuples: (("ENV_VAR_NAME", "{api_key}"), ...)
        for env_var_name, _ in getattr(spec, "env_extras", ()):
            val = os.environ.get(env_var_name, "")
            if val:
                return val
    return None


def _configured_api_base(provider_config: Any | None, spec: Any | None) -> tuple[str | None, bool]:
    """Resolve provider-specific API base and whether config supplied one."""
    from src.security.secret_refs import resolve_secret_ref

    if provider_config and provider_config.api_base:
        return resolve_secret_ref(provider_config.api_base), True
    if spec and getattr(spec, "default_api_base", ""):
        return spec.default_api_base, False
    return None, False
