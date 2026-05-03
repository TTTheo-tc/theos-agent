"""Per-secret encryption using AES-256-GCM with HKDF-SHA256 key derivation.

Each secret gets a random salt; HKDF derives a unique data-encryption key
from the master key + salt. The ciphertext format is:

    salt (32 bytes) || nonce (12 bytes) || ciphertext+tag

Reference: ironclaw/src/secrets/crypto.rs
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_SALT_BYTES = 32
_NONCE_BYTES = 12
_KEY_BYTES = 32
_INFO = b"theos-secrets-v1"


def encrypt(plaintext: bytes, master_key: bytes) -> bytes:
    """Encrypt *plaintext* with a fresh per-secret derived key.

    Returns ``salt || nonce || ciphertext+tag``.
    """
    if len(master_key) < _KEY_BYTES:
        raise ValueError(f"Master key too short: {len(master_key)} bytes, need >= {_KEY_BYTES}")

    salt = os.urandom(_SALT_BYTES)
    derived = _derive_key(master_key, salt)
    nonce = os.urandom(_NONCE_BYTES)
    ct = AESGCM(derived).encrypt(nonce, plaintext, None)
    return salt + nonce + ct


def decrypt(blob: bytes, master_key: bytes) -> bytes:
    """Decrypt a blob produced by :func:`encrypt`.

    Raises ``cryptography.exceptions.InvalidTag`` if the key is wrong
    or the ciphertext was tampered with.
    """
    if len(master_key) < _KEY_BYTES:
        raise ValueError(f"Master key too short: {len(master_key)} bytes, need >= {_KEY_BYTES}")

    min_len = _SALT_BYTES + _NONCE_BYTES + 16  # 16 = GCM tag
    if len(blob) < min_len:
        raise ValueError(f"Ciphertext too short ({len(blob)} bytes)")

    salt = blob[:_SALT_BYTES]
    nonce = blob[_SALT_BYTES : _SALT_BYTES + _NONCE_BYTES]
    ct = blob[_SALT_BYTES + _NONCE_BYTES :]
    derived = _derive_key(master_key, salt)
    return AESGCM(derived).decrypt(nonce, ct, None)


def _derive_key(master_key: bytes, salt: bytes) -> bytes:
    """HKDF-SHA256 key derivation: master_key + salt → 32-byte derived key."""
    return HKDF(
        algorithm=SHA256(),
        length=_KEY_BYTES,
        salt=salt,
        info=_INFO,
    ).derive(master_key[:_KEY_BYTES])
