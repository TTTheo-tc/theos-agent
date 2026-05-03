"""Config-level secret encryption using existing AES-256-GCM primitives."""

from __future__ import annotations

import base64
import re
from typing import Any

from loguru import logger

from src.security.crypto import decrypt as aes_decrypt
from src.security.crypto import encrypt as aes_encrypt
from src.security.keychain import resolve_master_key

_ENCRYPTED_PREFIX = "encrypted://"
_SECRET_REF_PREFIX = "secret://"

_CAMEL_RE = re.compile(r"([a-z0-9])([A-Z])")


def _normalize_key(key: str) -> str:
    """Convert camelCase to snake_case for normalized path matching."""
    return _CAMEL_RE.sub(r"\1_\2", key).lower()


SENSITIVE_PATHS: frozenset[str] = frozenset(
    {
        "channels.whatsapp.bridge_token",
        "channels.telegram.token",
        "channels.feishu.app_secret",
        "channels.feishu.encrypt_key",
        "channels.feishu.verification_token",
        "channels.discord.token",
        "channels.matrix.access_token",
        "channels.email.imap_password",
        "channels.email.smtp_password",
        "channels.mochat.claw_token",
        "channels.slack.bot_token",
        "channels.slack.app_token",
        "channels.qq.secret",
        "channels.dingtalk.client_secret",
        # Provider API keys (all ProvidersConfig fields with ProviderConfig.api_key)
        "providers.anthropic.api_key",
        "providers.openai.api_key",
        "providers.deepseek.api_key",
        "providers.gemini.api_key",
        "providers.openrouter.api_key",
        "providers.minimax.api_key",
        "providers.groq.api_key",
        "providers.custom.api_key",
        "providers.zhipu.api_key",
        "providers.dashscope.api_key",
        "providers.vllm.api_key",
        "providers.moonshot.api_key",
        "providers.aihubmix.api_key",
        "providers.siliconflow.api_key",
        "providers.volcengine.api_key",
        "providers.openai_codex.api_key",
        "providers.github_copilot.api_key",
        # Tool API keys
        "tools.web.search.api_key",
        "tools.web.search.tavily_api_key",
        "tools.web.fetch.jina_api_key",
        "tools.web.fetch.firecrawl_api_key",
        "tools.stock.tushare_token",
        "tools.stock.tavily_api_key",
        "embedding.api_key",
    }
)


class ConfigSecretsManager:
    """Encrypt/decrypt sensitive config values using AES-256-GCM."""

    def __init__(self, master_key: bytes) -> None:
        self._master_key = master_key

    def encrypt_value(self, value: str) -> str:
        """Encrypt a plaintext value. Skips empty, secret://, and already encrypted."""
        if not value:
            return value
        if value.startswith(_SECRET_REF_PREFIX):
            return value
        if value.startswith(_ENCRYPTED_PREFIX):
            return value
        blob = aes_encrypt(value.encode("utf-8"), self._master_key)
        return _ENCRYPTED_PREFIX + base64.urlsafe_b64encode(blob).decode("ascii")

    def decrypt_value(self, value: str) -> str:
        """Decrypt an encrypted:// value. Passes through anything else."""
        if not value or not value.startswith(_ENCRYPTED_PREFIX):
            return value
        try:
            blob = base64.urlsafe_b64decode(value[len(_ENCRYPTED_PREFIX) :])
            return aes_decrypt(blob, self._master_key).decode("utf-8")
        except Exception:
            logger.error(
                "Failed to decrypt config value — SECRETS_MASTER_KEY may have changed "
                "or OS keychain master key is inconsistent. Restore the original key "
                "or re-configure the affected config secrets."
            )
            return value

    def is_encrypted_value(self, value: str | None) -> bool:
        """Check if a value has the encrypted:// prefix."""
        return isinstance(value, str) and value.startswith(_ENCRYPTED_PREFIX)

    @staticmethod
    def is_sensitive_path(path: str) -> bool:
        """Check if a dot-separated config path is a known sensitive field."""
        normalized = ".".join(_normalize_key(seg) for seg in path.split("."))
        return normalized in SENSITIVE_PATHS

    @staticmethod
    def has_sensitive_values(data: Any) -> bool:
        """Check if a config dict has any non-empty plaintext sensitive values."""

        def _scan(node: Any, prefix: str = "") -> bool:
            if isinstance(node, dict):
                for k, v in node.items():
                    child = f"{prefix}.{k}" if prefix else k
                    if _scan(v, child):
                        return True
            elif isinstance(node, str) and node:
                if node.startswith(_ENCRYPTED_PREFIX) or node.startswith(_SECRET_REF_PREFIX):
                    return False
                normalized = ".".join(_normalize_key(s) for s in prefix.split("."))
                if normalized in SENSITIVE_PATHS:
                    return True
            return False

        return _scan(data)

    @staticmethod
    def has_encrypted_values(data: Any) -> bool:
        """Check if a config dict has any encrypted:// values."""

        def _scan(node: Any) -> bool:
            if isinstance(node, dict):
                return any(_scan(v) for v in node.values())
            if isinstance(node, list):
                return any(_scan(item) for item in node)
            return isinstance(node, str) and node.startswith(_ENCRYPTED_PREFIX)

        return _scan(data)

    # ------------------------------------------------------------------
    # Config tree operations
    # ------------------------------------------------------------------

    def encrypt_config_data(self, data: Any) -> Any:
        """Walk config dict and encrypt sensitive string leaves."""
        result, _ = self._walk(data, prefix="", encrypt=True)
        return result

    def decrypt_config_data(self, data: Any) -> tuple[Any, bool]:
        """Walk config dict and decrypt encrypted:// leaves.

        Returns (decrypted_data, had_plaintext_sensitive_values).
        Thread-safe: no mutable instance state used.
        """
        return self._walk(data, prefix="", encrypt=False)

    def _walk(self, node: Any, *, prefix: str, encrypt: bool) -> tuple[Any, bool]:
        """Recursively walk config tree. Returns (processed_node, found_plaintext)."""
        found_plaintext = False
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                child_prefix = f"{prefix}.{k}" if prefix else k
                processed, child_found = self._walk(v, prefix=child_prefix, encrypt=encrypt)
                out[k] = processed
                found_plaintext = found_plaintext or child_found
            return out, found_plaintext
        if isinstance(node, list):
            out_list: list[Any] = []
            for item in node:
                processed, child_found = self._walk(item, prefix=prefix, encrypt=encrypt)
                out_list.append(processed)
                found_plaintext = found_plaintext or child_found
            return out_list, found_plaintext
        if not isinstance(node, str) or not node:
            return node, False
        if encrypt:
            if self.is_sensitive_path(prefix):
                return self.encrypt_value(node), False
            return node, False
        # Decrypt mode
        if self.is_encrypted_value(node):
            return self.decrypt_value(node), False
        if self.is_sensitive_path(prefix) and not node.startswith(_SECRET_REF_PREFIX):
            return node, True  # plaintext sensitive value detected
        return node, False


def get_config_secrets_manager() -> ConfigSecretsManager | None:
    """Create a ConfigSecretsManager using the system master key, or None.

    Returns None only when no master key is available (no env var, no keychain).
    Raises ValueError if SECRETS_MASTER_KEY is set but invalid — this is a
    configuration error, not "no key available."
    """
    import os

    from src.security.keychain import MasterKeyUnavailableError

    try:
        master_key = resolve_master_key()
        return ConfigSecretsManager(master_key)
    except MasterKeyUnavailableError:
        return None  # genuinely no key — graceful degrade
    except (ValueError, Exception) as exc:
        # SECRETS_MASTER_KEY is set but invalid, or other unexpected error.
        # If the env var is set, this is a config error — don't silently fall back.
        if os.environ.get("SECRETS_MASTER_KEY"):
            raise ValueError(
                f"SECRETS_MASTER_KEY is set but invalid: {exc}. "
                "Fix the value or unset it to use OS keychain."
            ) from exc
        return None
