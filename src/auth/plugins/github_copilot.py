"""GitHub Copilot OAuth plugin — device flow authentication."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
from loguru import logger

from src.auth.types import OAuthCredential

# Constants (from LiteLLM's authenticator)
GITHUB_CLIENT_ID = "Iv1.b507a08c87ecfe98"
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_KEY_URL = "https://api.github.com/copilot_internal/v2/token"

_HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "editor-version": "theos/1.0",
    "editor-plugin-version": "theos-agent/1.0",
    "user-agent": "TheOSAgent/1.0",
    "accept-encoding": "gzip,deflate,br",
}


class GitHubCopilotPlugin:
    provider_id = "github_copilot"

    def format_api_key(self, cred: OAuthCredential) -> str:
        return cred.access  # The API key (not the GitHub access token)

    def auth_headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "editor-version": "theos/1.0",
            "editor-plugin-version": "theos-agent/1.0",
            "user-agent": "TheOSAgent/1.0",
            "x-github-api-version": "2025-04-01",
            "openai-intent": "conversation-panel",
        }

    def refresh(self, cred: OAuthCredential) -> OAuthCredential | None:
        """Use the stored GitHub access token (cred.refresh) to get a new API key."""
        if not cred.refresh:
            return None
        try:
            api_key_info = self._exchange_for_api_key(cred.refresh)
            if not api_key_info:
                return None
            return OAuthCredential(
                provider="github_copilot",
                access=api_key_info["token"],
                refresh=cred.refresh,  # GitHub access token is reused
                expires=int(api_key_info["expires_at"]) * 1000,  # to ms
            )
        except Exception:
            logger.opt(exception=True).debug("GitHub Copilot API key refresh failed")
            return None

    def login(self, redirect_uri: str) -> OAuthCredential | None:
        """Device flow: obtain GitHub access token, then exchange for API key."""
        try:
            # Step 1: Get device code
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    GITHUB_DEVICE_CODE_URL,
                    headers=_HEADERS,
                    json={"client_id": GITHUB_CLIENT_ID, "scope": "read:user"},
                )
                resp.raise_for_status()
                device_info = resp.json()

            device_code = device_info["device_code"]
            user_code = device_info["user_code"]
            verification_uri = device_info["verification_uri"]

            # Step 2: Prompt user
            print(  # noqa: T201
                f"\nPlease visit {verification_uri} and enter code: {user_code}\n",
                flush=True,
            )

            # Step 3: Poll for access token (max 12 attempts x 5s = 60s)
            access_token = self._poll_for_access_token(device_code)
            if not access_token:
                logger.warning("GitHub Copilot: timed out waiting for user authorization")
                return None

            # Step 4: Exchange access token for API key
            api_key_info = self._exchange_for_api_key(access_token)
            if not api_key_info:
                logger.warning("GitHub Copilot: failed to exchange access token for API key")
                return None

            return OAuthCredential(
                provider="github_copilot",
                access=api_key_info["token"],
                refresh=access_token,  # Store the GitHub access token for future refresh
                expires=int(api_key_info["expires_at"]) * 1000,
            )
        except Exception:
            logger.opt(exception=True).warning("GitHub Copilot device flow failed")
            return None

    def read_external_credentials(self) -> OAuthCredential | None:
        """Try to read from LiteLLM's token store or github-copilot hosts.json."""
        # Try LiteLLM token store: ~/.config/litellm/github_copilot/
        litellm_dir = Path.home() / ".config" / "litellm" / "github_copilot"
        cred = self._read_litellm_store(litellm_dir)
        if cred:
            return cred

        # Fallback: ~/.config/github-copilot/hosts.json
        hosts_path = Path.home() / ".config" / "github-copilot" / "hosts.json"
        cred = self._read_hosts_json(hosts_path)
        if cred:
            return cred

        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _poll_for_access_token(self, device_code: str) -> str | None:
        """Poll GitHub for the access token after user authorizes the device."""
        max_attempts = 12
        with httpx.Client(timeout=30) as client:
            for attempt in range(max_attempts):
                try:
                    resp = client.post(
                        GITHUB_ACCESS_TOKEN_URL,
                        headers=_HEADERS,
                        json={
                            "client_id": GITHUB_CLIENT_ID,
                            "device_code": device_code,
                            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    if "access_token" in data:
                        logger.info("GitHub Copilot: authentication successful")
                        return data["access_token"]

                    error = data.get("error", "")
                    if error == "authorization_pending":
                        logger.debug(
                            "Authorization pending (attempt {}/{})", attempt + 1, max_attempts
                        )
                    elif error in ("slow_down", ""):
                        pass  # retry
                    else:
                        logger.warning("GitHub device flow error: {}", data)
                        return None
                except Exception:
                    logger.opt(exception=True).debug("Error polling for access token")
                    return None

                time.sleep(5)

        return None

    def _exchange_for_api_key(self, access_token: str) -> dict | None:
        """Exchange a GitHub access token for a Copilot API key."""
        headers = {
            **_HEADERS,
            "authorization": f"token {access_token}",
        }
        max_retries = 3
        with httpx.Client(timeout=30) as client:
            for attempt in range(max_retries):
                try:
                    resp = client.get(GITHUB_API_KEY_URL, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    if "token" in data:
                        return data
                    logger.warning("API key response missing token: {}", data)
                except httpx.HTTPStatusError as e:
                    logger.debug(
                        "HTTP error getting API key (attempt {}/{}): {}",
                        attempt + 1,
                        max_retries,
                        e,
                    )
                except Exception:
                    logger.opt(exception=True).debug("Error exchanging for API key")
                    break
        return None

    def _read_litellm_store(self, token_dir: Path) -> OAuthCredential | None:
        """Read credentials from LiteLLM's GitHub Copilot token store."""
        access_token_file = token_dir / "access-token"
        api_key_file = token_dir / "api-key.json"

        access_token = ""
        if access_token_file.exists():
            try:
                access_token = access_token_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass

        if not access_token:
            return None

        # Try reading cached API key
        if api_key_file.exists():
            try:
                api_key_info = json.loads(api_key_file.read_text(encoding="utf-8"))
                token = api_key_info.get("token", "")
                expires_at = api_key_info.get("expires_at", 0)
                if token and expires_at > time.time():
                    return OAuthCredential(
                        provider="github_copilot",
                        access=token,
                        refresh=access_token,
                        expires=int(expires_at) * 1000,
                    )
            except Exception:
                pass

        # API key expired or missing — try to refresh with the access token
        api_key_info = self._exchange_for_api_key(access_token)
        if api_key_info:
            return OAuthCredential(
                provider="github_copilot",
                access=api_key_info["token"],
                refresh=access_token,
                expires=int(api_key_info["expires_at"]) * 1000,
            )
        return None

    def _read_hosts_json(self, hosts_path: Path) -> OAuthCredential | None:
        """Read GitHub access token from github-copilot hosts.json."""
        if not hosts_path.exists():
            return None
        try:
            data = json.loads(hosts_path.read_text(encoding="utf-8"))
            # hosts.json has format: {"github.com": {"oauth_token": "...", "user": "..."}}
            gh_entry = data.get("github.com", {})
            oauth_token = gh_entry.get("oauth_token", "")
            if not oauth_token:
                return None
            # Exchange for API key
            api_key_info = self._exchange_for_api_key(oauth_token)
            if api_key_info:
                return OAuthCredential(
                    provider="github_copilot",
                    access=api_key_info["token"],
                    refresh=oauth_token,
                    expires=int(api_key_info["expires_at"]) * 1000,
                )
        except Exception:
            logger.opt(exception=True).debug("Failed to read github-copilot hosts.json")
        return None
