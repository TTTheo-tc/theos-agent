"""Tests for the consolidated credential resolution module."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from src.config.schema import Config
from src.providers.credentials import ProviderCredentials, resolve_credentials


def test_auth_profile_takes_priority_over_config_key():
    """Tier 1 (auth profile) wins over tier 2 (config file)."""
    config = Config()
    config.providers.anthropic.api_key = "sk-config-key"

    with patch(
        "src.auth.store.get_credential_for_provider",
        return_value=("sk-auth-profile", "anthropic:default"),
    ):
        creds = resolve_credentials("anthropic", config, model="claude-sonnet-4-5")

    assert isinstance(creds, ProviderCredentials)
    assert creds.api_key == "sk-auth-profile"


def test_config_key_used_when_no_auth_profile():
    """Tier 2 (config file) used when tier 1 returns None."""
    config = Config()
    config.providers.anthropic.api_key = "sk-config-key"

    with patch("src.auth.store.get_credential_for_provider", return_value=None):
        creds = resolve_credentials("anthropic", config, model="claude-sonnet-4-5")

    assert creds.api_key == "sk-config-key"


def test_no_key_returns_none_api_key():
    """When no credentials found anywhere, api_key is None."""
    config = Config()
    config.providers.anthropic.api_key = ""

    with patch("src.auth.store.get_credential_for_provider", return_value=None):
        creds = resolve_credentials("anthropic", config)

    assert creds.api_key is None


def test_extra_headers_resolved():
    """Extra headers from config are resolved."""
    config = Config()
    config.providers.anthropic.api_key = "sk-test"
    config.providers.anthropic.extra_headers = {"X-Custom": "val"}

    with patch(
        "src.auth.store.get_credential_for_provider", return_value=("sk-test", "anthropic:default")
    ):
        creds = resolve_credentials("anthropic", config)

    assert creds.extra_headers == {"X-Custom": "val"}


def test_api_base_from_config():
    """api_base from provider config is used."""
    config = Config()
    config.providers.anthropic.api_key = "sk-test"
    config.providers.anthropic.api_base = "https://custom.api/v1"

    with patch(
        "src.auth.store.get_credential_for_provider", return_value=("sk-test", "anthropic:default")
    ):
        creds = resolve_credentials("anthropic", config)

    assert creds.api_base == "https://custom.api/v1"


def test_api_base_from_spec_default():
    """api_base falls back to spec.default_api_base when config has none."""
    from types import SimpleNamespace

    config = Config()
    config.providers.moonshot.api_key = "sk-test"

    spec = SimpleNamespace(default_api_base="https://api.moonshot.ai/v1")

    with patch(
        "src.auth.store.get_credential_for_provider", return_value=("sk-test", "anthropic:default")
    ):
        creds = resolve_credentials("moonshot", config, spec=spec)

    assert creds.api_base == "https://api.moonshot.ai/v1"


def test_none_provider_name_returns_empty_credentials():
    """When provider_name is None, returns credentials with no key."""
    config = Config()

    with patch("src.auth.store.get_credential_for_provider", return_value=None):
        creds = resolve_credentials(None, config)

    assert creds.api_key is None


class TestTier3EnvVar:
    def test_env_key_used_when_tiers_1_2_empty(self):
        spec = MagicMock()
        spec.env_key = "TEST_PROVIDER_API_KEY"
        spec.env_extras = ()
        spec.default_api_base = ""
        spec.is_oauth = False

        config = MagicMock()
        p_config = MagicMock()
        p_config.api_key = ""
        p_config.api_base = ""
        p_config.extra_headers = None
        config.providers.test_provider = p_config
        config.get_api_base.return_value = ""

        with (
            patch("src.auth.store.get_credential_for_provider", return_value=None),
            patch(
                "src.security.secret_refs.resolve_secret_ref", side_effect=lambda x, **kw: x or None
            ),
            patch("src.security.secret_refs.resolve_mapping_refs", return_value=None),
            patch.dict(os.environ, {"TEST_PROVIDER_API_KEY": "sk-from-env"}),
        ):
            creds = resolve_credentials("test_provider", config, spec=spec)
            assert creds.api_key == "sk-from-env"

    def test_env_extras_used_as_fallback(self):
        spec = MagicMock()
        spec.env_key = "MISSING_KEY"
        spec.env_extras = (("ALT_API_KEY", "{api_key}"),)
        spec.default_api_base = ""
        spec.is_oauth = False

        config = MagicMock()
        p_config = MagicMock()
        p_config.api_key = ""
        p_config.api_base = ""
        p_config.extra_headers = None
        config.providers.test_provider = p_config
        config.get_api_base.return_value = ""

        with (
            patch("src.auth.store.get_credential_for_provider", return_value=None),
            patch(
                "src.security.secret_refs.resolve_secret_ref", side_effect=lambda x, **kw: x or None
            ),
            patch("src.security.secret_refs.resolve_mapping_refs", return_value=None),
            patch.dict(os.environ, {"ALT_API_KEY": "sk-alt"}),
        ):
            creds = resolve_credentials("test_provider", config, spec=spec)
            assert creds.api_key == "sk-alt"

    def test_tier1_takes_priority_over_env(self):
        spec = MagicMock()
        spec.env_key = "SHOULD_NOT_USE"
        spec.env_extras = ()
        spec.default_api_base = ""
        spec.is_oauth = False

        config = MagicMock()
        p_config = MagicMock()
        p_config.api_key = ""
        p_config.api_base = ""
        p_config.extra_headers = None
        config.providers.test_provider = p_config
        config.get_api_base.return_value = ""

        with (
            patch(
                "src.auth.store.get_credential_for_provider",
                return_value=("sk-from-store", "test:default"),
            ),
            patch(
                "src.security.secret_refs.resolve_secret_ref", side_effect=lambda x, **kw: x or None
            ),
            patch("src.security.secret_refs.resolve_mapping_refs", return_value=None),
            patch.dict(os.environ, {"SHOULD_NOT_USE": "sk-env"}),
        ):
            creds = resolve_credentials("test_provider", config, spec=spec)
            assert creds.api_key == "sk-from-store"  # Tier 1 wins
