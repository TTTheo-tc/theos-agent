"""Integration tests for config load/save with encryption."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from src.config.loader import load_config, save_config
from src.config.schema import Config

_PATCH_KEY = "src.security.config_secrets.resolve_master_key"


def test_save_config_encrypts_sensitive_fields(tmp_path: Path):
    """save_config writes encrypted values for sensitive fields."""
    config_path = tmp_path / "config.json"
    config = Config()
    config.channels.telegram.token = "bot123:secret"
    config.channels.telegram.enabled = True

    with patch(_PATCH_KEY, return_value=b"\x01" * 32):
        save_config(config, config_path)

    raw = json.loads(config_path.read_text())
    assert raw["channels"]["telegram"]["token"].startswith("encrypted://")
    assert raw["channels"]["telegram"]["enabled"] is True


def test_load_config_decrypts_encrypted_fields(tmp_path: Path):
    """load_config returns decrypted runtime values."""
    from src.security.config_secrets import ConfigSecretsManager

    mgr = ConfigSecretsManager(b"\x01" * 32)
    data = {"channels": {"telegram": {"token": mgr.encrypt_value("bot123:secret")}}}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(data))

    with patch(_PATCH_KEY, return_value=b"\x01" * 32):
        config = load_config(config_path)

    assert config.channels.telegram.token == "bot123:secret"


def test_load_config_auto_migrates_plaintext(tmp_path: Path):
    """Plaintext sensitive values are rewritten as encrypted after load."""
    data = {"channels": {"telegram": {"token": "plain-token"}}}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(data))

    with patch(_PATCH_KEY, return_value=b"\x01" * 32):
        config = load_config(config_path)

    assert config.channels.telegram.token == "plain-token"
    raw = json.loads(config_path.read_text())
    assert raw["channels"]["telegram"]["token"].startswith("encrypted://")


def test_load_config_preserves_secret_refs(tmp_path: Path):
    """secret:// references pass through unchanged."""
    data = {"channels": {"telegram": {"token": "secret://telegram"}}}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(data))

    with patch(_PATCH_KEY, return_value=b"\x01" * 32):
        config = load_config(config_path)
        save_config(config, config_path)

    raw = json.loads(config_path.read_text())
    assert raw["channels"]["telegram"]["token"] == "secret://telegram"


def test_save_config_warns_without_master_key_when_sensitive(tmp_path: Path):
    """Without master key, save_config writes plaintext with WARNING."""
    from src.security.keychain import MasterKeyUnavailableError

    config_path = tmp_path / "config.json"
    config = Config()
    config.channels.telegram.token = "plain-token"

    with patch(_PATCH_KEY, side_effect=MasterKeyUnavailableError("no key")):
        save_config(config, config_path)  # should not raise

    raw = json.loads(config_path.read_text())
    assert raw["channels"]["telegram"]["token"] == "plain-token"


def test_save_config_ok_without_master_key_when_no_sensitive(tmp_path: Path):
    """Without master key, save_config works if no sensitive values present."""
    from src.security.keychain import MasterKeyUnavailableError

    config_path = tmp_path / "config.json"
    config = Config()  # default config, no secrets set

    with patch(_PATCH_KEY, side_effect=MasterKeyUnavailableError("no key")):
        save_config(config, config_path)  # should not raise

    assert config_path.exists()


def test_load_config_raises_with_encrypted_values_but_no_key(tmp_path: Path):
    """load_config raises if config has encrypted:// values but no master key."""
    import pytest

    from src.security.keychain import MasterKeyUnavailableError

    data = {"channels": {"telegram": {"token": "encrypted://abc123fake"}}}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(data))

    with (
        patch(_PATCH_KEY, side_effect=MasterKeyUnavailableError("no key")),
        pytest.raises(RuntimeError, match="no master key"),
    ):
        load_config(config_path)


def test_load_config_raises_with_invalid_master_key_env(tmp_path: Path, monkeypatch):
    """Invalid SECRETS_MASTER_KEY is a config error, not a silent plaintext fallback."""
    import pytest

    data = {"agents": {"defaults": {"model": "anthropic/claude-sonnet-4"}}}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(data))
    monkeypatch.setenv("SECRETS_MASTER_KEY", "not-hex")

    with pytest.raises(RuntimeError, match="SECRETS_MASTER_KEY"):
        load_config(config_path)


def test_save_config_raises_with_invalid_master_key_env(tmp_path: Path, monkeypatch):
    """Invalid SECRETS_MASTER_KEY should not make save_config write plaintext."""
    import pytest

    config_path = tmp_path / "config.json"
    config = Config()
    config.channels.telegram.token = "plain-token"
    monkeypatch.setenv("SECRETS_MASTER_KEY", "not-hex")

    with pytest.raises(RuntimeError, match="SECRETS_MASTER_KEY"):
        save_config(config, config_path)

    assert not config_path.exists()


def test_load_config_raises_with_wrong_master_key(tmp_path: Path):
    """load_config raises if encrypted values can't be decrypted (wrong key)."""
    import pytest

    from src.security.config_secrets import ConfigSecretsManager

    # Encrypt with key A
    mgr_a = ConfigSecretsManager(b"\x01" * 32)
    data = {"channels": {"telegram": {"token": mgr_a.encrypt_value("bot-secret")}}}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(data))

    # Load with key B — decryption fails, encrypted:// values remain
    with (
        patch(_PATCH_KEY, return_value=b"\x02" * 32),
        pytest.raises(RuntimeError, match="decryption failed"),
    ):
        load_config(config_path)


def test_roundtrip_save_load(tmp_path: Path):
    """Config survives save -> load roundtrip with encryption."""
    config_path = tmp_path / "config.json"
    config = Config()
    config.channels.telegram.token = "bot-tok-123"
    config.channels.slack.bot_token = "xoxb-test"
    config.channels.email.imap_password = "imap-pass"

    master_key = b"\x02" * 32
    with patch(_PATCH_KEY, return_value=master_key):
        save_config(config, config_path)
        loaded = load_config(config_path)

    assert loaded.channels.telegram.token == "bot-tok-123"
    assert loaded.channels.slack.bot_token == "xoxb-test"
    assert loaded.channels.email.imap_password == "imap-pass"
