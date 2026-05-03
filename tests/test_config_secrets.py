"""Tests for ConfigSecretsManager."""

from __future__ import annotations

from src.security.config_secrets import ConfigSecretsManager

_TEST_KEY = b"\x01" * 32


def test_encrypt_value_produces_tagged_string():
    mgr = ConfigSecretsManager(_TEST_KEY)
    encrypted = mgr.encrypt_value("my-secret-token")
    assert encrypted.startswith("encrypted://")
    assert encrypted != "encrypted://my-secret-token"


def test_decrypt_value_roundtrips():
    mgr = ConfigSecretsManager(_TEST_KEY)
    original = "sk-ant-api-test-123"
    encrypted = mgr.encrypt_value(original)
    decrypted = mgr.decrypt_value(encrypted)
    assert decrypted == original


def test_decrypt_value_passes_through_non_encrypted():
    mgr = ConfigSecretsManager(_TEST_KEY)
    assert mgr.decrypt_value("plain-value") == "plain-value"


def test_is_encrypted_value():
    mgr = ConfigSecretsManager(_TEST_KEY)
    assert mgr.is_encrypted_value("encrypted://abc123") is True
    assert mgr.is_encrypted_value("secret://anthropic") is False
    assert mgr.is_encrypted_value("plain-value") is False
    assert mgr.is_encrypted_value(None) is False
    assert mgr.is_encrypted_value("") is False


def test_encrypt_value_skips_empty_string():
    mgr = ConfigSecretsManager(_TEST_KEY)
    assert mgr.encrypt_value("") == ""


def test_encrypt_value_skips_secret_ref():
    mgr = ConfigSecretsManager(_TEST_KEY)
    assert mgr.encrypt_value("secret://anthropic") == "secret://anthropic"


def test_encrypt_value_skips_already_encrypted():
    mgr = ConfigSecretsManager(_TEST_KEY)
    encrypted = mgr.encrypt_value("my-token")
    double = mgr.encrypt_value(encrypted)
    assert double == encrypted


def test_is_sensitive_path_camel_and_snake_equivalent():
    """Both camelCase and snake_case normalize to the same path."""
    assert ConfigSecretsManager.is_sensitive_path("channels.feishu.appSecret") is True
    assert ConfigSecretsManager.is_sensitive_path("channels.feishu.app_secret") is True
    assert ConfigSecretsManager.is_sensitive_path("channels.slack.botToken") is True
    assert ConfigSecretsManager.is_sensitive_path("channels.slack.bot_token") is True
    assert ConfigSecretsManager.is_sensitive_path("providers.anthropic.apiKey") is True
    assert ConfigSecretsManager.is_sensitive_path("providers.anthropic.api_key") is True
    # Non-sensitive fields
    assert ConfigSecretsManager.is_sensitive_path("channels.feishu.tokenDir") is False
    assert ConfigSecretsManager.is_sensitive_path("channels.feishu.appId") is False
    assert ConfigSecretsManager.is_sensitive_path("agents.defaults.model") is False


def test_encrypt_config_data_encrypts_sensitive_fields():
    mgr = ConfigSecretsManager(_TEST_KEY)
    data = {
        "channels": {
            "telegram": {"enabled": True, "token": "bot123:abc"},
            "feishu": {"appId": "cli_xxx", "appSecret": "secret-val"},
        }
    }
    encrypted = mgr.encrypt_config_data(data)
    assert encrypted["channels"]["telegram"]["token"].startswith("encrypted://")
    assert encrypted["channels"]["telegram"]["enabled"] is True
    assert encrypted["channels"]["feishu"]["appId"] == "cli_xxx"
    assert encrypted["channels"]["feishu"]["appSecret"].startswith("encrypted://")


def test_decrypt_config_data_decrypts_and_detects_plaintext():
    mgr = ConfigSecretsManager(_TEST_KEY)
    encrypted_token = mgr.encrypt_value("bot123:abc")
    data = {
        "channels": {
            "telegram": {"token": encrypted_token},
            "discord": {"token": "plain-discord-token"},
        }
    }
    decrypted, had_plaintext = mgr.decrypt_config_data(data)
    assert decrypted["channels"]["telegram"]["token"] == "bot123:abc"
    assert decrypted["channels"]["discord"]["token"] == "plain-discord-token"
    assert had_plaintext is True


def test_encrypt_config_data_skips_secret_refs():
    mgr = ConfigSecretsManager(_TEST_KEY)
    data = {"channels": {"telegram": {"token": "secret://telegram"}}}
    encrypted = mgr.encrypt_config_data(data)
    assert encrypted["channels"]["telegram"]["token"] == "secret://telegram"


def test_encrypt_config_data_handles_provider_api_keys():
    mgr = ConfigSecretsManager(_TEST_KEY)
    data = {"providers": {"anthropic": {"apiKey": "sk-ant-api-test"}}}
    encrypted = mgr.encrypt_config_data(data)
    assert encrypted["providers"]["anthropic"]["apiKey"].startswith("encrypted://")


def test_roundtrip_encrypt_decrypt():
    mgr = ConfigSecretsManager(_TEST_KEY)
    original = {
        "channels": {
            "telegram": {"enabled": True, "token": "bot-tok"},
            "slack": {"botToken": "xoxb-123", "appToken": "xapp-456"},
            "email": {"imapPassword": "pass1", "smtpPassword": "pass2"},
        },
        "providers": {"openai": {"apiKey": "sk-test"}},
        "agents": {"defaults": {"model": "claude-sonnet-4-5"}},
    }
    encrypted = mgr.encrypt_config_data(original)
    decrypted, _ = mgr.decrypt_config_data(encrypted)
    assert decrypted == original


def test_decrypt_config_data_no_plaintext_returns_false():
    mgr = ConfigSecretsManager(_TEST_KEY)
    encrypted_token = mgr.encrypt_value("bot-tok")
    data = {"channels": {"telegram": {"token": encrypted_token}}}
    _, had_plaintext = mgr.decrypt_config_data(data)
    assert had_plaintext is False
