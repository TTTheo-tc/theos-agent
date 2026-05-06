"""Auth profile store — load, save, and look up credentials.

File location: ~/.theos/auth-profiles.enc  (AES-256-GCM encrypted)
Legacy:        ~/.theos/auth-profiles.json  (auto-migrated on first load)

Profile ID format: "provider:name"  (e.g. "anthropic:default", "openai:work")
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import suppress
from pathlib import Path
from typing import TypeVar

from loguru import logger

from src.auth.types import (
    ApiKeyCredential,
    AuthProfileStore,
    OAuthCredential,
    ProfileUsageStats,
    TokenCredential,
)

Credential = ApiKeyCredential | TokenCredential | OAuthCredential
T = TypeVar("T")

_CREDENTIAL_TYPES: dict[str, type[Credential]] = {
    "api_key": ApiKeyCredential,
    "token": TokenCredential,
    "oauth": OAuthCredential,
}


def _normalize_provider(provider: str) -> str:
    """Normalize provider IDs used in auth profile keys."""
    return provider.strip().replace("-", "_")


def _normalize_profile_id(profile_id: str) -> str:
    """Normalize only the provider side of ``provider:name`` profile IDs."""
    if ":" not in profile_id:
        return profile_id
    provider, name = profile_id.split(":", 1)
    return f"{_normalize_provider(provider)}:{name}"


def _legacy_profile_id(profile_id: str) -> str:
    """Return the hyphenated provider variant of ``provider:name``."""
    if ":" not in profile_id:
        return profile_id
    provider, name = profile_id.split(":", 1)
    return f"{_normalize_provider(provider).replace('_', '-')}:{name}"


def _resolve_existing_profile_id(store: AuthProfileStore, profile_id: str) -> str | None:
    """Return the canonical or legacy profile ID present in *store*."""
    for candidate in (
        _normalize_profile_id(profile_id),
        profile_id,
        _legacy_profile_id(profile_id),
    ):
        if candidate in store.profiles:
            return candidate
    return None


def _preferred_profile_id(store: AuthProfileStore, provider: str) -> str | None:
    """Return last_good for normalized or legacy hyphenated provider keys."""
    legacy_provider = provider.replace("_", "-")
    return store.last_good.get(provider) or store.last_good.get(legacy_provider)


def _auth_store_path() -> Path:
    return Path.home() / ".theos" / "auth-profiles.enc"


def _legacy_store_path() -> Path:
    return Path.home() / ".theos" / "auth-profiles.json"


def load_auth_store() -> AuthProfileStore:
    """Load the store from disk, or return an empty store on first use.

    On first load, auto-migrates legacy plaintext JSON to encrypted format.

    Error semantics:
      - No store file exists → empty store (legitimate first run)
      - Encrypted file exists but master key unavailable → raise (not silent)
      - Encrypted file exists but corrupt/wrong key → raise (not silent)
      - Legacy file exists but migration fails → keep legacy, raise
    """
    from src.security.keychain import MasterKeyUnavailableError

    path = _auth_store_path()
    legacy = _legacy_store_path()

    # Try encrypted store first
    if path.exists():
        try:
            data = json.loads(_decrypt_file(path))
            return _coerce_store(data)
        except MasterKeyUnavailableError:
            raise  # Don't swallow — caller must handle
        except Exception as exc:
            raise RuntimeError(
                f"Failed to decrypt auth store at {path}. "
                "If the master key changed, set SECRETS_MASTER_KEY or restore the keychain. "
                f"Original error: {exc}"
            ) from exc

    # Migrate from legacy plaintext
    if legacy.exists():
        data = json.loads(legacy.read_text(encoding="utf-8"))
        store = _coerce_store(data)
        try:
            save_auth_store(store)
        except MasterKeyUnavailableError:
            # Can't encrypt — keep legacy file, return the store unencrypted
            logger.warning(
                "Master key unavailable — auth-profiles.json kept as plaintext. "
                "Set SECRETS_MASTER_KEY or install keyring to enable encryption."
            )
            return store
        except Exception:
            # Encryption failed for other reasons — keep legacy, log, return
            logger.opt(exception=True).warning(
                "Migration to encrypted auth store failed — keeping plaintext"
            )
            return store
        # Migration succeeded — remove legacy file
        legacy.unlink()
        logger.info("Migrated auth-profiles.json → auth-profiles.enc (encrypted)")
        return store

    # No store file at all — legitimate first run
    return AuthProfileStore()


def save_auth_store(store: AuthProfileStore) -> None:
    """Persist the store to disk (encrypted)."""
    path = _auth_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    plaintext = store.model_dump_json(indent=2, exclude_none=True).encode("utf-8")
    _encrypt_file(path, plaintext)


def _encrypt_file(path: Path, plaintext: bytes) -> None:
    """Encrypt and atomically write *plaintext* to *path*."""
    from src.security.crypto import encrypt
    from src.security.keychain import resolve_master_key

    blob = encrypt(plaintext, resolve_master_key())
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(blob)
    tmp.rename(path)


def _decrypt_file(path: Path) -> str:
    """Read and decrypt *path*, returning the plaintext string."""
    from src.security.crypto import decrypt
    from src.security.keychain import resolve_master_key

    blob = path.read_bytes()
    return decrypt(blob, resolve_master_key()).decode("utf-8")


def _coerce_store(data: dict) -> AuthProfileStore:
    """Parse raw JSON into AuthProfileStore, coercing credential discriminant."""
    return AuthProfileStore(
        version=data.get("version", 1),
        profiles=_coerce_profiles(data.get("profiles", {})),
        last_good=data.get("lastGood", data.get("last_good", {})),
        usage_stats=_coerce_usage_stats(data.get("usageStats", data.get("usage_stats", {}))),
    )


def _coerce_profiles(raw_profiles: dict) -> dict[str, Credential]:
    profiles: dict[str, Credential] = {}
    for pid, cred_data in raw_profiles.items():
        model = _CREDENTIAL_TYPES.get(cred_data.get("type", "api_key"))
        if model is None:
            continue
        with suppress(Exception):
            profiles[pid] = model.model_validate(cred_data)
    return profiles


def _coerce_usage_stats(raw_stats: dict) -> dict[str, ProfileUsageStats]:
    usage_stats: dict[str, ProfileUsageStats] = {}
    for pid, stats in raw_stats.items():
        with suppress(Exception):
            usage_stats[pid] = ProfileUsageStats.model_validate(stats)
    return usage_stats


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _extract_key(
    cred: Credential,
) -> str | None:
    if isinstance(cred, ApiKeyCredential) and cred.key:
        return cred.key
    if isinstance(cred, TokenCredential) and cred.token:
        return cred.token
    if isinstance(cred, OAuthCredential) and cred.access:
        return cred.access
    return None


def _extract_static_key(cred: Credential) -> str | None:
    if isinstance(cred, ApiKeyCredential) and cred.key:
        return cred.key
    if isinstance(cred, TokenCredential) and cred.token:
        return cred.token
    return None


def _iter_lookup_candidates(
    store: AuthProfileStore,
    provider: str,
) -> Iterator[tuple[str, Credential]]:
    preferred_id = _preferred_profile_id(store, provider)
    seen: set[str] = set()
    if preferred_id:
        cred = store.profiles.get(preferred_id)
        if cred is not None:
            seen.add(preferred_id)
            yield preferred_id, cred

    for pid, cred in store.profiles.items():
        if pid in seen or _normalize_provider(cred.provider) != provider:
            continue
        yield pid, cred


def _lookup_profile(
    provider: str,
    extract: Callable[[Credential], T | None],
) -> tuple[T, str] | None:
    provider = _normalize_provider(provider)
    store = load_auth_store()
    for pid, cred in _iter_lookup_candidates(store, provider):
        value = extract(cred)
        if value:
            return value, pid
    return None


def _mark_profile_default(store: AuthProfileStore, provider: str, profile_id: str) -> None:
    provider = _normalize_provider(provider)
    store.last_good[provider] = profile_id
    legacy_provider = provider.replace("_", "-")
    if legacy_provider != provider:
        store.last_good.pop(legacy_provider, None)
    store.usage_stats.setdefault(profile_id, ProfileUsageStats())


def get_credential_for_provider(provider: str) -> tuple[str, str | None] | None:
    """Return ``(api_key, profile_id)`` for *provider*, or None.

    Prefer ``last_good`` profile, fall back to any matching profile.
    """
    return _lookup_profile(provider, _extract_key)


def get_static_credential_for_provider(provider: str) -> tuple[str, str | None] | None:
    """Return an API-key/token credential for *provider*, excluding OAuth profiles."""
    return _lookup_profile(provider, _extract_static_key)


def get_oauth_credential_for_provider(provider: str) -> tuple[OAuthCredential, str] | None:
    """Return an OAuth credential and profile ID for *provider*, if one exists."""
    return _lookup_profile(
        provider,
        lambda cred: cred if isinstance(cred, OAuthCredential) else None,
    )


def get_api_key_for_provider(provider: str) -> str | None:
    """Return the best API key for *provider* from the auth store, or None."""
    result = get_credential_for_provider(provider)
    return result[0] if result else None


def add_api_key_profile(
    provider: str,
    key: str,
    name: str = "default",
    email: str | None = None,
) -> str:
    """Save an API key as a named profile and mark it as the provider default.

    Returns the profile ID (e.g. "anthropic:default").
    """
    provider = _normalize_provider(provider)
    store = load_auth_store()
    profile_id = f"{provider}:{name}"

    store.profiles[profile_id] = ApiKeyCredential(
        provider=provider,
        key=key,
        email=email,
    )
    _mark_profile_default(store, provider, profile_id)

    save_auth_store(store)
    return profile_id


def add_oauth_profile(
    provider: str,
    access: str,
    refresh: str,
    expires: int,
    name: str = "default",
    email: str | None = None,
    scope: str | None = None,
    client_id: str | None = None,
    account_id: str | None = None,
) -> str:
    """Save an OAuth credential and mark it as the provider default.

    Returns the profile ID (e.g. "google:default").
    """
    provider = _normalize_provider(provider)
    store = load_auth_store()
    profile_id = f"{provider}:{name}"

    store.profiles[profile_id] = OAuthCredential(
        provider=provider,
        access=access,
        refresh=refresh,
        expires=expires,
        email=email,
        scope=scope,
        client_id=client_id,
        account_id=account_id,
    )
    _mark_profile_default(store, provider, profile_id)

    save_auth_store(store)
    return profile_id


def remove_profile(profile_id: str) -> bool:
    """Remove a profile by ID. Returns True if it existed."""
    store = load_auth_store()
    profile_id = _resolve_existing_profile_id(store, profile_id)
    if profile_id is None:
        return False

    cred = store.profiles.pop(profile_id)
    store.usage_stats.pop(profile_id, None)

    # Update last_good if it pointed to the removed profile
    for provider, pid in list(store.last_good.items()):
        if pid == profile_id:
            fallback = next(
                (
                    p
                    for p, c in store.profiles.items()
                    if _normalize_provider(c.provider) == _normalize_provider(cred.provider)
                ),
                None,
            )
            if fallback:
                store.last_good[provider] = fallback
            else:
                del store.last_good[provider]

    save_auth_store(store)
    return True


def set_default_profile(profile_id: str) -> bool:
    """Set *profile_id* as the default for its provider. Returns False if not found."""
    store = load_auth_store()
    profile_id = _resolve_existing_profile_id(store, profile_id)
    if profile_id is None:
        return False
    _mark_profile_default(store, store.profiles[profile_id].provider, profile_id)
    save_auth_store(store)
    return True
