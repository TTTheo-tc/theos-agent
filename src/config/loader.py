"""Configuration loading utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from src.config.schema import Config


def get_config_path() -> Path:
    """Get the default configuration file path."""
    return Path.home() / ".theos" / "config.json"


def get_data_dir() -> Path:
    """Get the TheOS data directory."""
    from src.utils.helpers import get_data_path

    return get_data_path()


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.

    Args:
        config_path: Optional path to config file. Uses default if not provided.

    Returns:
        Loaded configuration object.
    """
    path = config_path or get_config_path()

    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data = _migrate_config(data)
            data, had_plaintext = _decrypt_config_data(data)

            config = Config.model_validate(data)
            _apply_proxy_env(config)

            # Auto-migrate plaintext secrets to encrypted form
            if had_plaintext:
                try:
                    save_config(config, path)
                    logger.info("Migrated plaintext config secrets to encrypted form")
                except Exception:
                    logger.debug("Could not auto-migrate plaintext config secrets")

            return config
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")

    config = Config()
    _apply_proxy_env(config)
    return config


def save_config(config: Config, config_path: Path | None = None, *, compact: bool = True) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
        compact: When true, write only values that differ from schema defaults.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(by_alias=True, exclude_defaults=compact)
    data = _encrypt_config_data(data)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")

    # Rename root hooksDir/hooks_dir -> hooks
    if "hooks" not in data:
        if "hooksDir" in data:
            data["hooks"] = data.pop("hooksDir")
        elif "hooks_dir" in data:
            data["hooks"] = data.pop("hooks_dir")
    return data


def _decrypt_config_data(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Decrypt encrypted config values and report whether plaintext secrets were found."""
    try:
        from src.security.config_secrets import (
            ConfigSecretsManager,
            get_config_secrets_manager,
        )

        mgr = get_config_secrets_manager()
        if mgr:
            data, had_plaintext = mgr.decrypt_config_data(data)
            if ConfigSecretsManager.has_encrypted_values(data):
                raise RuntimeError(
                    "Config decryption failed — SECRETS_MASTER_KEY or OS keychain "
                    "master key may have changed. Restore the original key or "
                    "re-configure the affected config secrets."
                )
            return data, had_plaintext
        if ConfigSecretsManager.has_encrypted_values(data):
            raise RuntimeError(
                "Config contains encrypted values but no master key is available. "
                "Set SECRETS_MASTER_KEY or restore the OS keychain master key."
            )
    except RuntimeError:
        raise
    except Exception:
        logger.debug("Config secrets decryption unavailable")
    return data, False


def _encrypt_config_data(data: dict[str, Any]) -> dict[str, Any]:
    """Encrypt sensitive config values when a master key is available."""
    try:
        from src.security.config_secrets import ConfigSecretsManager, get_config_secrets_manager

        mgr = get_config_secrets_manager()
        if mgr:
            return mgr.encrypt_config_data(data)
        if ConfigSecretsManager.has_sensitive_values(data):
            logger.warning(
                "No master key available — config secrets saved as plaintext. "
                "Set SECRETS_MASTER_KEY or install a keychain to enable encryption."
            )
    except Exception:
        logger.warning("Config secrets encryption failed, saving plaintext")
    return data


def _apply_proxy_env(config: Config) -> None:
    """Expose the saved proxy through env vars for HTTP clients that honor them."""
    from src.utils.proxy import apply_http_proxy_env

    apply_http_proxy_env(config.proxy)
