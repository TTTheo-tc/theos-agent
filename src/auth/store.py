"""Auth profile store — load, save, and look up credentials.

File location: ~/.theos/auth-profiles.enc  (AES-256-GCM encrypted)
Legacy:        ~/.theos/auth-profiles.json  (auto-migrated on first load)

Profile ID format: "provider:name"  (e.g. "anthropic:default", "openai:work")
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from src.auth.types import (
    ApiKeyCredential,
    AuthProfileStore,
    OAuthCredential,
    ProfileUsageStats,
    TokenCredential,
)


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
    profiles: dict[str, ApiKeyCredential | TokenCredential | OAuthCredential] = {}
    for pid, cred_data in data.get("profiles", {}).items():
        ctype = cred_data.get("type", "api_key")
        try:
            if ctype == "api_key":
                profiles[pid] = ApiKeyCredential.model_validate(cred_data)
            elif ctype == "token":
                profiles[pid] = TokenCredential.model_validate(cred_data)
            elif ctype == "oauth":
                profiles[pid] = OAuthCredential.model_validate(cred_data)
            # Unknown types skipped silently
        except Exception:
            pass

    usage_stats: dict[str, ProfileUsageStats] = {}
    # Support both camelCase (openclaw compat) and snake_case
    for pid, stats in data.get("usageStats", data.get("usage_stats", {})).items():
        try:
            usage_stats[pid] = ProfileUsageStats.model_validate(stats)
        except Exception:
            pass

    return AuthProfileStore(
        version=data.get("version", 1),
        profiles=profiles,
        last_good=data.get("lastGood", data.get("last_good", {})),
        usage_stats=usage_stats,
    )


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _extract_key(
    cred: ApiKeyCredential | TokenCredential | OAuthCredential,
) -> str | None:
    if isinstance(cred, ApiKeyCredential) and cred.key:
        return cred.key
    if isinstance(cred, TokenCredential) and cred.token:
        return cred.token
    if isinstance(cred, OAuthCredential) and cred.access:
        return cred.access
    return None


def get_credential_for_provider(provider: str) -> tuple[str, str | None] | None:
    """Return ``(api_key, profile_id)`` for *provider*, or None.

    Prefer ``last_good`` profile, fall back to any matching profile.
    """
    store = load_auth_store()

    preferred_id = store.last_good.get(provider)
    if preferred_id and preferred_id in store.profiles:
        key = _extract_key(store.profiles[preferred_id])
        if key:
            return key, preferred_id

    for pid, cred in store.profiles.items():
        if cred.provider != provider:
            continue
        key = _extract_key(cred)
        if key:
            return key, pid

    return None


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
    store = load_auth_store()
    profile_id = f"{provider}:{name}"

    store.profiles[profile_id] = ApiKeyCredential(
        provider=provider,
        key=key,
        email=email,
    )
    store.last_good[provider] = profile_id
    if profile_id not in store.usage_stats:
        store.usage_stats[profile_id] = ProfileUsageStats()

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
    store.last_good[provider] = profile_id
    if profile_id not in store.usage_stats:
        store.usage_stats[profile_id] = ProfileUsageStats()

    save_auth_store(store)
    return profile_id


def remove_profile(profile_id: str) -> bool:
    """Remove a profile by ID. Returns True if it existed."""
    store = load_auth_store()
    if profile_id not in store.profiles:
        return False

    cred = store.profiles.pop(profile_id)
    store.usage_stats.pop(profile_id, None)

    # Update last_good if it pointed to the removed profile
    for provider, pid in list(store.last_good.items()):
        if pid == profile_id:
            fallback = next(
                (p for p, c in store.profiles.items() if c.provider == cred.provider),
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
    if profile_id not in store.profiles:
        return False
    store.last_good[store.profiles[profile_id].provider] = profile_id
    save_auth_store(store)
    return True
