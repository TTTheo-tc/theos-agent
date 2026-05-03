"""Tests for security crypto module (AES-256-GCM + HKDF)."""

from __future__ import annotations

import os

import pytest

from src.security.crypto import decrypt, encrypt


@pytest.fixture
def master_key() -> bytes:
    return os.urandom(32)


class TestEncryptDecrypt:
    def test_roundtrip(self, master_key: bytes) -> None:
        plaintext = b"hello world secret"
        blob = encrypt(plaintext, master_key)
        assert decrypt(blob, master_key) == plaintext

    def test_different_salts_produce_different_ciphertexts(self, master_key: bytes) -> None:
        plaintext = b"same plaintext"
        blob1 = encrypt(plaintext, master_key)
        blob2 = encrypt(plaintext, master_key)
        # Salts differ → ciphertexts differ
        assert blob1 != blob2
        # But both decrypt to the same plaintext
        assert decrypt(blob1, master_key) == plaintext
        assert decrypt(blob2, master_key) == plaintext

    def test_tampered_ciphertext_fails(self, master_key: bytes) -> None:
        blob = encrypt(b"secret data", master_key)
        # Flip a byte in the ciphertext portion
        tampered = bytearray(blob)
        tampered[-5] ^= 0xFF
        with pytest.raises(Exception):  # InvalidTag
            decrypt(bytes(tampered), master_key)

    def test_wrong_key_fails(self, master_key: bytes) -> None:
        blob = encrypt(b"secret data", master_key)
        wrong_key = os.urandom(32)
        with pytest.raises(Exception):  # InvalidTag
            decrypt(blob, wrong_key)

    def test_short_master_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            encrypt(b"data", b"short")

    def test_short_ciphertext_rejected(self, master_key: bytes) -> None:
        with pytest.raises(ValueError, match="too short"):
            decrypt(b"too_short", master_key)

    def test_empty_plaintext(self, master_key: bytes) -> None:
        blob = encrypt(b"", master_key)
        assert decrypt(blob, master_key) == b""

    def test_large_plaintext(self, master_key: bytes) -> None:
        plaintext = os.urandom(100_000)
        blob = encrypt(plaintext, master_key)
        assert decrypt(blob, master_key) == plaintext
