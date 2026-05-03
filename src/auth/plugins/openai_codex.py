"""OpenAI Codex OAuth plugin."""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from src.auth.types import OAuthCredential

try:
    from oauth_cli_kit import get_token as get_codex_token
except ImportError:
    get_codex_token = None


class OpenAICodexPlugin:
    provider_id = "openai_codex"

    def format_api_key(self, cred: OAuthCredential) -> str:
        return cred.access

    def auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def refresh(self, cred: OAuthCredential) -> OAuthCredential | None:
        """Refresh via oauth_cli_kit."""
        if get_codex_token is None:
            return None
        try:
            token = get_codex_token()
            if not token or not getattr(token, "access", None):
                return None
            expires_raw = getattr(token, "expires", 0)
            # Normalize to ms if needed
            expires_ms = expires_raw if expires_raw > 1e12 else int(expires_raw * 1000)
            return OAuthCredential(
                provider="openai_codex",
                access=token.access,
                refresh=getattr(token, "refresh", cred.refresh) or cred.refresh,
                expires=expires_ms,
                account_id=getattr(token, "account_id", None),
            )
        except Exception:
            logger.opt(exception=True).debug("Codex token refresh failed")
            return None

    def login(self, redirect_uri: str) -> OAuthCredential | None:
        """Run `codex auth` or fallback to reading auth.json."""
        import shutil
        import subprocess

        if shutil.which("codex"):
            try:
                subprocess.run(["codex", "auth"], timeout=120)
            except Exception as e:
                logger.warning("codex auth failed: {}", e)
        return self.read_external_credentials()

    def read_external_credentials(self) -> OAuthCredential | None:
        path = self._auth_json_path()
        if not path or not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # Support nested "tokens" structure (Codex CLI ≥2025) and flat layout
            tokens = data.get("tokens", data)
            access = tokens.get("access_token", "")
            if not access:
                return None
            return OAuthCredential(
                provider="openai_codex",
                access=access,
                refresh=tokens.get("refresh_token", ""),
                expires=tokens.get("expires_at", 0),
                account_id=tokens.get("account_id"),
            )
        except Exception:
            logger.opt(exception=True).debug("Failed to read Codex auth.json")
            return None

    def _auth_json_path(self) -> Path | None:
        path = Path.home() / ".codex" / "auth.json"
        return path if path.exists() else None
