"""Tests for master key resolution and auth store encryption lifecycle."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.security.keychain import MasterKeyUnavailableError, resolve_master_key


class TestResolveFromEnv:
    def test_env_var_hex(self) -> None:
        key_hex = "aa" * 32  # 32 bytes
        with patch.dict(os.environ, {"SECRETS_MASTER_KEY": key_hex}):
            key = resolve_master_key()
            assert key == bytes.fromhex(key_hex)

    def test_env_var_too_short(self) -> None:
        with patch.dict(os.environ, {"SECRETS_MASTER_KEY": "aa" * 10}):
            with pytest.raises(ValueError, match="too short"):
                resolve_master_key()


class TestResolveFromKeychain:
    def test_keychain_read(self) -> None:
        stored_hex = "bb" * 32
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("src.security.keychain._keychain_get", return_value=bytes.fromhex(stored_hex)),
        ):
            os.environ.pop("SECRETS_MASTER_KEY", None)
            key = resolve_master_key()
            assert key == bytes.fromhex(stored_hex)


class TestKeychainUnavailable:
    def test_no_keychain_no_env_raises(self) -> None:
        """Without env var or keychain, must raise — not return random key."""
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("src.security.keychain._keychain_get", return_value=None),
            patch("src.security.keychain._keychain_set", return_value=False),
        ):
            os.environ.pop("SECRETS_MASTER_KEY", None)
            with pytest.raises(MasterKeyUnavailableError):
                resolve_master_key()

    def test_generate_and_persist_succeeds(self) -> None:
        """When keychain write succeeds, key is returned."""
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("src.security.keychain._keychain_get", return_value=None),
            patch("src.security.keychain._keychain_set", return_value=True),
        ):
            os.environ.pop("SECRETS_MASTER_KEY", None)
            key = resolve_master_key()
            assert len(key) == 32


class TestAuthStoreLifecycle:
    """End-to-end tests for encrypted auth store with error semantics."""

    def test_decrypt_failure_raises(self, tmp_path: Path) -> None:
        """Encrypted file with wrong key must raise, not return empty store."""
        from src.auth.store import load_auth_store, save_auth_store
        from src.auth.types import ApiKeyCredential, AuthProfileStore

        enc_path = tmp_path / "auth.enc"
        legacy_path = tmp_path / "auth.json"

        with (
            patch("src.auth.store._auth_store_path", return_value=enc_path),
            patch("src.auth.store._legacy_store_path", return_value=legacy_path),
        ):
            # Save with one key
            key_a = "aa" * 32
            with patch.dict(os.environ, {"SECRETS_MASTER_KEY": key_a}):
                store = AuthProfileStore(
                    profiles={"test:default": ApiKeyCredential(provider="test", key="secret123")}
                )
                save_auth_store(store)
                assert enc_path.exists()

            # Try to load with a different key
            key_b = "bb" * 32
            with patch.dict(os.environ, {"SECRETS_MASTER_KEY": key_b}):
                with pytest.raises(RuntimeError, match="Failed to decrypt"):
                    load_auth_store()

    def test_legacy_migration_success(self, tmp_path: Path) -> None:
        """Legacy JSON is migrated to .enc and deleted."""
        from src.auth.store import load_auth_store

        enc_path = tmp_path / "auth.enc"
        legacy_path = tmp_path / "auth.json"
        legacy_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "profiles": {
                        "anthropic:default": {
                            "type": "api_key",
                            "provider": "anthropic",
                            "key": "sk-test",
                        }
                    },
                }
            )
        )

        key_hex = "cc" * 32
        with (
            patch("src.auth.store._auth_store_path", return_value=enc_path),
            patch("src.auth.store._legacy_store_path", return_value=legacy_path),
            patch.dict(os.environ, {"SECRETS_MASTER_KEY": key_hex}),
        ):
            store = load_auth_store()
            assert store.profiles["anthropic:default"].key == "sk-test"
            assert enc_path.exists()
            assert not legacy_path.exists()

    def test_legacy_kept_when_encryption_unavailable(self, tmp_path: Path) -> None:
        """Legacy JSON is preserved when no master key is available."""
        from src.auth.store import load_auth_store

        enc_path = tmp_path / "auth.enc"
        legacy_path = tmp_path / "auth.json"
        legacy_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "profiles": {"test:x": {"type": "api_key", "provider": "test", "key": "k123"}},
                }
            )
        )

        with (
            patch("src.auth.store._auth_store_path", return_value=enc_path),
            patch("src.auth.store._legacy_store_path", return_value=legacy_path),
            patch.dict(os.environ, {}, clear=False),
            patch("src.security.keychain._keychain_get", return_value=None),
            patch("src.security.keychain._keychain_set", return_value=False),
        ):
            os.environ.pop("SECRETS_MASTER_KEY", None)
            store = load_auth_store()
            # Store is returned (from plaintext fallback)
            assert store.profiles["test:x"].key == "k123"
            # Legacy file is preserved
            assert legacy_path.exists()
            # Encrypted file was NOT created
            assert not enc_path.exists()

    def test_no_files_returns_empty(self, tmp_path: Path) -> None:
        """No auth files at all → empty store (first run)."""
        from src.auth.store import load_auth_store

        with (
            patch("src.auth.store._auth_store_path", return_value=tmp_path / "nope.enc"),
            patch("src.auth.store._legacy_store_path", return_value=tmp_path / "nope.json"),
        ):
            store = load_auth_store()
            assert len(store.profiles) == 0

    def test_roundtrip_encrypted(self, tmp_path: Path) -> None:
        """Save → load roundtrip through encryption."""
        from src.auth.store import load_auth_store, save_auth_store
        from src.auth.types import ApiKeyCredential, AuthProfileStore

        enc_path = tmp_path / "auth.enc"
        key_hex = "dd" * 32

        with (
            patch("src.auth.store._auth_store_path", return_value=enc_path),
            patch("src.auth.store._legacy_store_path", return_value=tmp_path / "x.json"),
            patch.dict(os.environ, {"SECRETS_MASTER_KEY": key_hex}),
        ):
            store = AuthProfileStore(
                profiles={
                    "anthropic:default": ApiKeyCredential(provider="anthropic", key="sk-ant-real")
                }
            )
            save_auth_store(store)

            loaded = load_auth_store()
            assert loaded.profiles["anthropic:default"].key == "sk-ant-real"

            # Verify file is not readable as JSON
            raw = enc_path.read_bytes()
            with pytest.raises(Exception):
                json.loads(raw)
