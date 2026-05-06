"""Runtime resolution for ``secret://...`` config references."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

_SECRET_REF_PREFIX = "secret://"
_SECRET_REF_RE = re.compile(r"secret://([A-Za-z0-9_.:-]+)")
_ENV_NAME_RE = re.compile(r"[^A-Za-z0-9]+")


def is_secret_ref(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith(_SECRET_REF_PREFIX)


def resolve_secret_ref(value: str | None, *, default: str | None = None) -> str | None:
    """Resolve a secret reference from auth store first, then environment."""
    if value is None or not is_secret_ref(value):
        return value

    secret_name = value[len(_SECRET_REF_PREFIX) :].strip()
    if not secret_name:
        return default

    store_value = _resolve_from_auth_store(secret_name)
    if store_value:
        return store_value

    env_value = os.environ.get(_to_env_name(secret_name))
    if env_value:
        return env_value

    return default


def resolve_mapping_refs(mapping: dict[str, str] | None) -> dict[str, str] | None:
    """Resolve ``secret://`` values inside a string mapping."""
    if mapping is None:
        return None
    resolved: dict[str, str] = {}
    for key, value in mapping.items():
        resolved_value = resolve_secret_ref(value, default="")
        if resolved_value:
            resolved[key] = resolved_value
    return resolved


def resolve_inline_secret_refs(
    value: str,
    resolver: Callable[[str], str | None],
) -> str:
    """Resolve ``secret://name`` references embedded inside a string."""
    if not isinstance(value, str) or _SECRET_REF_PREFIX not in value:
        return value

    def _replace(match: re.Match[str]) -> str:
        secret_name = match.group(1).strip()
        if not secret_name:
            return match.group(0)
        secret = resolver(secret_name)
        return secret if secret is not None else match.group(0)

    return _SECRET_REF_RE.sub(_replace, value)


def resolve_inline_mapping_refs(
    mapping: dict[str, str] | None,
    resolver: Callable[[str], str | None],
) -> dict[str, str] | None:
    """Resolve embedded ``secret://name`` references inside a string mapping."""
    if mapping is None:
        return None
    return {key: resolve_inline_secret_refs(value, resolver) for key, value in mapping.items()}


def resolve_data_secret_refs(value: Any) -> Any:
    """Recursively resolve secret refs in lists, dicts, and pydantic models."""
    if isinstance(value, BaseModel):
        data = resolve_data_secret_refs(value.model_dump())
        return value.__class__.model_validate(data)
    if isinstance(value, dict):
        return {key: resolve_data_secret_refs(item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_data_secret_refs(item) for item in value]
    if isinstance(value, tuple):
        return tuple(resolve_data_secret_refs(item) for item in value)
    if isinstance(value, str):
        return resolve_secret_ref(value, default="")
    return value


def _resolve_from_auth_store(secret_name: str) -> str | None:
    try:
        from src.auth.store import get_api_key_for_provider

        return get_api_key_for_provider(secret_name)
    except Exception:
        return None


def _to_env_name(secret_name: str) -> str:
    normalized = _ENV_NAME_RE.sub("_", secret_name).strip("_")
    return normalized.upper()
