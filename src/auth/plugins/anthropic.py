"""Anthropic OAuth plugin — direct token refresh via Anthropic API."""

from __future__ import annotations

import os
import time

import httpx
from loguru import logger

from src.auth.types import OAuthCredential

TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
_DEFAULT_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
SCOPES = (
    "org:create_api_key user:profile user:inference "
    "user:sessions:claude_code user:mcp_servers user:file_upload"
)
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 9527
EXPIRES_BUFFER_S = 300
LOGIN_TIMEOUT_S = 180


def _get_client_id() -> str:
    return os.environ.get("ANTHROPIC_OAUTH_CLIENT_ID", _DEFAULT_CLIENT_ID)


def _coerce_expires(expires_in_seconds: int | float, now_ms: int) -> int:
    """Convert expires_in (seconds) to absolute ms timestamp with 5-min buffer.

    Matches openclaw's coerceExpiresAt: subtract buffer, enforce 30s floor.
    """
    value = now_ms + max(0, int(expires_in_seconds)) * 1000 - EXPIRES_BUFFER_S * 1000
    return max(value, now_ms + 30_000)


class AnthropicPlugin:
    provider_id = "anthropic"

    def format_api_key(self, cred: OAuthCredential) -> str:
        return cred.access

    def auth_headers(self, token: str) -> dict[str, str]:
        return {"x-api-key": token} if token else {}

    def refresh(self, cred: OAuthCredential) -> OAuthCredential | None:
        """Exchange refresh_token for new tokens via Anthropic token endpoint."""
        if not cred.refresh:
            return None
        try:
            resp = httpx.post(
                TOKEN_URL,
                json={
                    "grant_type": "refresh_token",
                    "client_id": _get_client_id(),
                    "refresh_token": cred.refresh,
                },
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            now_ms = int(time.time() * 1000)
            expires_ms = _coerce_expires(data.get("expires_in", 0), now_ms)
            return OAuthCredential(
                provider="anthropic",
                access=data["access_token"],
                refresh=data.get("refresh_token", cred.refresh),
                expires=expires_ms,
                scope=cred.scope,
                email=cred.email,
                account_id=cred.account_id,
            )
        except Exception:
            logger.opt(exception=True).warning(
                "Anthropic OAuth token refresh failed. "
                "Anthropic OAuth is disabled in TheOS; use a standard API key instead."
            )
            return None

    def login(self, redirect_uri: str) -> OAuthCredential | None:
        """PKCE OAuth login via local callback server."""
        import base64
        import hashlib
        import secrets
        import webbrowser
        from urllib.parse import urlencode

        verifier = secrets.token_urlsafe(32)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )

        callback_uri = redirect_uri or f"http://localhost:{CALLBACK_PORT}/callback"
        params = urlencode(
            {
                "client_id": _get_client_id(),
                "response_type": "code",
                "redirect_uri": callback_uri,
                "scope": SCOPES,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": verifier,
            }
        )
        auth_url = f"{AUTHORIZE_URL}?{params}"

        print(f"\nOpen this URL to authenticate:\n{auth_url}\n")
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

        code, state = self._run_callback_server(verifier)
        if not code:
            return None

        try:
            resp = httpx.post(
                TOKEN_URL,
                json={
                    "grant_type": "authorization_code",
                    "client_id": _get_client_id(),
                    "code": code,
                    "state": state,
                    "redirect_uri": callback_uri,
                    "code_verifier": verifier,
                },
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            now_ms = int(time.time() * 1000)
            expires_ms = _coerce_expires(data.get("expires_in", 0), now_ms)
            return OAuthCredential(
                provider="anthropic",
                access=data["access_token"],
                refresh=data.get("refresh_token", ""),
                expires=expires_ms,
                scope=SCOPES,
            )
        except Exception:
            logger.opt(exception=True).warning("Anthropic OAuth token exchange failed")
            return None

    def _run_callback_server(self, expected_state: str) -> tuple[str | None, str | None]:
        """Start a local HTTP server and wait for the OAuth callback."""
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        result: dict[str, str | None] = {"code": None, "state": None}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                from urllib.parse import parse_qs, urlparse

                query = parse_qs(urlparse(self.path).query)
                code = query.get("code", [None])[0]
                state = query.get("state", [None])[0]
                if code and state == expected_state:
                    result["code"] = code
                    result["state"] = state
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<h1>Authenticated. You can close this tab.</h1>")
                else:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"State mismatch or missing code.")
                threading.Thread(target=self.server.shutdown, daemon=True).start()

            def log_message(self, format, *args):  # noqa: A002
                pass

        try:
            server = HTTPServer((CALLBACK_HOST, CALLBACK_PORT), Handler)
        except OSError as e:
            logger.warning(
                "Cannot start callback server on {}:{} — {}",
                CALLBACK_HOST,
                CALLBACK_PORT,
                e,
            )
            return None, None

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        thread.join(timeout=LOGIN_TIMEOUT_S)
        server.shutdown()

        return result["code"], result["state"]

    def read_external_credentials(self) -> OAuthCredential | None:
        return None
