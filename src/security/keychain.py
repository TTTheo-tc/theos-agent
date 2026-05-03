"""OS keychain integration for master key storage.

Resolves the master key used for encrypting secrets at rest:
  1. SECRETS_MASTER_KEY environment variable
  2. OS keychain (macOS Keychain / Linux Secret Service)
  3. Generate + store in keychain (only if keychain is writable)

A master key is NEVER returned unless it can be persisted — either via
env var or OS keychain. This prevents silent data loss where a random
key encrypts data that becomes unreadable after restart.

Reference: ironclaw/src/secrets/keychain.rs
"""

from __future__ import annotations

import os
import secrets

from loguru import logger

_SERVICE = "theos"
_ACCOUNT = "master_key"
_KEY_BYTES = 32  # AES-256


class MasterKeyUnavailableError(RuntimeError):
    """No persistent master key source available."""


def resolve_master_key() -> bytes:
    """Resolve the master encryption key, generating one on first use.

    Priority:
      1. ``SECRETS_MASTER_KEY`` env var (hex-encoded, >=32 bytes)
      2. OS keychain (read existing)
      3. Generate + store in keychain (only if write succeeds)

    Raises :class:`MasterKeyUnavailableError` if no key can be resolved
    or persisted. This is intentional — a non-persistent random key
    would cause silent data loss on restart.
    """
    # 1. Environment variable
    env_hex = os.environ.get("SECRETS_MASTER_KEY")
    if env_hex:
        key = bytes.fromhex(env_hex)
        if len(key) < _KEY_BYTES:
            raise ValueError(
                f"SECRETS_MASTER_KEY too short: {len(key)} bytes, need >= {_KEY_BYTES}"
            )
        return key[:_KEY_BYTES]

    # 2. OS keychain — read existing key
    key = _keychain_get()
    if key is not None:
        return key

    # 3. Generate + persist — ONLY if keychain write succeeds
    key = secrets.token_bytes(_KEY_BYTES)
    if _keychain_set(key):
        logger.info("Generated and stored new master key in OS keychain")
        return key

    # No persistent storage available — refuse to return an ephemeral key
    raise MasterKeyUnavailableError(
        "Cannot resolve or persist a master encryption key. "
        "Either set SECRETS_MASTER_KEY env var (64 hex chars) "
        "or install the 'keyring' package: pip install keyring"
    )


def _keychain_get() -> bytes | None:
    """Read master key from OS keychain. Returns None if unavailable."""
    try:
        import keyring  # type: ignore[import-untyped]

        stored = keyring.get_password(_SERVICE, _ACCOUNT)
        if stored:
            key = bytes.fromhex(stored)
            if len(key) >= _KEY_BYTES:
                return key[:_KEY_BYTES]
            logger.warning("Keychain key too short ({} bytes), ignoring", len(key))
    except ImportError:
        logger.debug("keyring package not installed, skipping OS keychain")
    except Exception as exc:
        logger.debug("OS keychain read failed: {}", exc)
    return None


def _keychain_set(key: bytes) -> bool:
    """Store master key in OS keychain. Returns True on success."""
    try:
        import keyring  # type: ignore[import-untyped]

        keyring.set_password(_SERVICE, _ACCOUNT, key.hex())
        # Verify write by reading back
        stored = keyring.get_password(_SERVICE, _ACCOUNT)
        if stored and bytes.fromhex(stored) == key:
            return True
        logger.warning("Keychain write succeeded but read-back verification failed")
        return False
    except ImportError:
        logger.warning(
            "keyring package not installed — cannot persist master key. "
            "Install with: pip install keyring"
        )
        return False
    except Exception as exc:
        logger.warning("Failed to store master key in OS keychain: {}", exc)
        return False


def delete_master_key() -> bool:
    """Remove master key from OS keychain. Returns True if deleted."""
    try:
        import keyring  # type: ignore[import-untyped]

        keyring.delete_password(_SERVICE, _ACCOUNT)
        return True
    except Exception:
        return False
