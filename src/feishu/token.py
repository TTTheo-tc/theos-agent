"""
Feishu OAuth token management.

Token lifecycle:
    1. User opens the authorization URL in a browser and obtains an authorization code.
    2. Exchange the code for a refresh_token + access_token (saved to local files with expiry).
    3. On subsequent calls, check the access_token TTL; if expired (or about to expire),
       use the refresh_token to obtain a new pair via the Feishu API.
    4. If the refresh_token itself is expired, the user must re-authorize.

All credentials are passed as function parameters (no hardcoded defaults).
Token files are stored under a configurable directory (default: ~/.theos/feishu_tokens).
"""

from __future__ import annotations

import json
import os
import select
import sys
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event, Thread
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from filelock import FileLock
from loguru import logger

# ---------------------------------------------------------------------------
# Default token storage directory
# ---------------------------------------------------------------------------
DEFAULT_TOKEN_DIR = "~/.theos/feishu_tokens"


# ---------------------------------------------------------------------------
# Token persistence helpers
# ---------------------------------------------------------------------------


def _token_path(token_dir: str, filename: str) -> Path:
    return Path(token_dir).expanduser() / filename


def _token_payload(token_key: str, token: str, expires_epoch: int) -> dict[str, object]:
    return {
        token_key: token,
        "timestamp": datetime.now().isoformat(),
        "expires_epoch": expires_epoch,
        "expires_datetime": datetime.fromtimestamp(expires_epoch).isoformat(),
    }


def _write_token_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.chmod(path, 0o600)


def save_refresh_token(
    refresh_token: str,
    expires_epoch: int,
    token_dir: str = DEFAULT_TOKEN_DIR,
) -> None:
    """Persist *refresh_token* to ``<token_dir>/refresh_token.json``."""
    path = _token_path(token_dir, "refresh_token.json")
    logger.info(f"saving refresh token to {path}, " f"token: {refresh_token[:4]}...*")
    _write_token_json(path, _token_payload("refresh_token", refresh_token, expires_epoch))


def get_refresh_token(
    token_dir: str = DEFAULT_TOKEN_DIR,
    ttl_threshold: int = 0,
) -> tuple[str, int]:
    """Return ``(refresh_token, ttl_seconds)`` from the stored file.

    Raises ``ValueError`` when the file is missing or the token is expired.
    """
    path = _token_path(token_dir, "refresh_token.json")
    if not path.exists():
        msg = f"Refresh token file {path} not found. " "Run init_token() to authorize first."
        raise ValueError(msg)

    data = json.loads(path.read_text(encoding="utf-8"))
    ttl = data["expires_epoch"] - int(time.time())
    if ttl < ttl_threshold:
        msg = f"Refresh token is expired (TTL: {ttl:,}s). " "Run init_token() to re-authorize."
        raise ValueError(msg)

    token = data["refresh_token"]
    logger.info(
        f"loaded refresh token from {path}, "
        f"token: {token[:4]}...*, "
        f"TTL: {ttl:,}s ({ttl / 60:.1f} min)"
    )
    return token, ttl


def save_access_token(
    access_token: str,
    expires_epoch: int,
    token_dir: str = DEFAULT_TOKEN_DIR,
) -> None:
    """Persist *access_token* to ``<token_dir>/access_token.json``."""
    path = _token_path(token_dir, "access_token.json")
    logger.info(f"saving access token to {path}, " f"token: {access_token[:4]}...*")
    _write_token_json(path, _token_payload("access_token", access_token, expires_epoch))


def save_oauth_tokens(
    data: dict,
    *,
    token_dir: str = DEFAULT_TOKEN_DIR,
    epoch: int | None = None,
    require_refresh_token: bool = False,
) -> tuple[str, int, int, bool]:
    """Persist access/refresh tokens from a Feishu OAuth response.

    Returns ``(access_token, access_token_ttl, refresh_token_ttl, refresh_saved)``.
    """
    if require_refresh_token:
        required = {"access_token", "expires_in", "refresh_token", "refresh_token_expires_in"}
        missing = required.difference(data)
        if missing:
            msg = f"OAuth response missing required fields: {', '.join(sorted(missing))}"
            raise KeyError(msg)

    epoch_now = int(time.time()) if epoch is None else epoch
    access_token = data["access_token"]
    access_token_ttl = data.get("expires_in", 7200)
    refresh_token_ttl = data.get("refresh_token_expires_in", 2592000)

    save_access_token(
        access_token,
        epoch_now + access_token_ttl,
        token_dir=token_dir,
    )

    refresh_saved = "refresh_token" in data
    if refresh_saved:
        save_refresh_token(
            data["refresh_token"],
            epoch_now + refresh_token_ttl,
            token_dir=token_dir,
        )

    return access_token, access_token_ttl, refresh_token_ttl, refresh_saved


def _read_valid_access_token(token_path: Path, min_ttl: int) -> str | None:
    if not token_path.exists():
        return None
    data = json.loads(token_path.read_text(encoding="utf-8"))
    ttl = data["expires_epoch"] - int(time.time())
    if ttl <= min_ttl:
        return None

    access_token = data["access_token"]
    logger.info(
        f"loaded access token: {access_token[:4]}...* "
        f"from {token_path}, TTL: {ttl:,}s ({ttl / 60:.1f} min)"
    )
    return access_token


# ---------------------------------------------------------------------------
# API call: refresh token
# ---------------------------------------------------------------------------


def refresh_token_from_api(
    refresh_token: str,
    app_id: str,
    app_secret: str,
    timeout: float = 60.0,
) -> dict:
    """Call the Feishu token endpoint to exchange a refresh token for a new pair.

    Returns the raw JSON response containing *access_token*, *refresh_token*,
    *expires_in*, and *refresh_token_expires_in*.

    .. warning::

       The refresh_token is **one-time-use**.  If the request times out or
       fails after the server consumed it, the old token is invalidated and
       re-authorization is required.
    """
    url = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "refresh_token": refresh_token,
    }
    try:
        res = httpx.post(url, data=payload, timeout=timeout)
    except httpx.TimeoutException as e:
        msg = (
            f"Timeout ({timeout}s) while refreshing token from {url}. "
            "The refresh_token may have been consumed by the server but the "
            "response was lost. You may need to run init_token() to re-authorize."
        )
        raise RuntimeError(msg) from e
    except httpx.RequestError as e:
        msg = (
            f"Network error while refreshing token from {url}: {e}. "
            "The refresh_token may have been consumed by the server but the "
            "response was lost. You may need to run init_token() to re-authorize."
        )
        raise RuntimeError(msg) from e

    if res.status_code != 200:
        msg = f"Failed to refresh token from {url}, " f"status={res.status_code}, error: {res.text}"
        raise RuntimeError(msg)

    resp = res.json()
    if resp.get("code") != 0:
        msg = f"Invalid token response from {url}: {resp}"
        raise RuntimeError(msg)
    # v2 API nests tokens under "data"
    return resp.get("data") or resp


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def get_access_token(
    app_id: str,
    app_secret: str,
    token_dir: str = DEFAULT_TOKEN_DIR,
    min_ttl: int = 30,
) -> str:
    """Return a valid user access token, refreshing automatically if needed.

    Uses a file-lock so concurrent processes don't race on the refresh.

    Parameters
    ----------
    app_id:
        Feishu app (client) ID.
    app_secret:
        Feishu app (client) secret.
    token_dir:
        Directory where token JSON files are stored.
    min_ttl:
        Minimum remaining lifetime (seconds) for the token to be considered
        valid.  If the stored token has less than *min_ttl* left it will be
        refreshed.

    Returns
    -------
    str
        A valid Feishu user access token.
    """
    token_path = _token_path(token_dir, "access_token.json")

    # --- fast path (no lock) ---
    access_token = _read_valid_access_token(token_path, min_ttl)
    if access_token:
        return access_token

    # --- slow path: acquire lock and refresh ---
    lock_path = _token_path(token_dir, "token.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with FileLock(str(lock_path)):
        # double-check after acquiring the lock (another process may have refreshed)
        access_token = _read_valid_access_token(token_path, min_ttl)
        if access_token:
            return access_token

        # actually refresh
        epoch_now = int(time.time())
        old_refresh, _ = get_refresh_token(token_dir=token_dir)
        data = refresh_token_from_api(
            old_refresh,
            app_id=app_id,
            app_secret=app_secret,
        )

        access_token = data["access_token"]
        save_refresh_token(
            data["refresh_token"],
            epoch_now + data["refresh_token_expires_in"],
            token_dir=token_dir,
        )
        save_access_token(
            access_token,
            epoch_now + data["expires_in"],
            token_dir=token_dir,
        )

        ttl = data["expires_in"]
        logger.info(
            f"refreshed access token: {access_token[:4]}...*, "
            f"TTL: {ttl:,}s ({ttl / 60:.1f} min)"
        )
        return access_token


def init_token(
    app_id: str,
    app_secret: str,
    redirect_uri: str,
    token_dir: str = DEFAULT_TOKEN_DIR,
    scope: str = "",
    enable_autofill: bool = False,
) -> str:
    """Run the OAuth authorization-code flow and persist tokens.

    Opens a browser for the user to authorize, then exchanges the resulting
    code for access + refresh tokens.

    Parameters
    ----------
    app_id:
        Feishu app (client) ID.
    app_secret:
        Feishu app (client) secret.
    redirect_uri:
        OAuth redirect URI (must be registered in the Feishu developer console).
    token_dir:
        Directory where token JSON files are stored.
    scope:
        Space-separated OAuth scopes.  Pass an empty string to use the
        server's default.
    enable_autofill:
        When ``True``, start a local HTTP server on ``localhost:9527`` to
        receive the callback automatically (overrides *redirect_uri*).

    Returns
    -------
    str
        The newly obtained access token.
    """
    effective_redirect = redirect_uri
    if enable_autofill:
        effective_redirect = "http://localhost:9527/callback"

    query: dict[str, str] = {
        "client_id": app_id,
        "redirect_uri": effective_redirect,
        "response_type": "code",
    }
    if scope:
        query["scope"] = scope

    authorize_url = "https://accounts.feishu.cn/open-apis/authen/v1/authorize?" + urlencode(query)
    logger.opt(colors=True).info(
        f"Open the <red>authorization URL</red> and obtain the code: "
        f"<blue><u>{authorize_url}</u></blue>"
    )

    if enable_autofill:
        code = _wait_for_authorization_code(effective_redirect, authorize_url)
    else:
        code = input("Enter the authorization code: ").strip()

    # Exchange authorization code for tokens
    token_url = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
    payload = {
        "grant_type": "authorization_code",
        "client_id": app_id,
        "client_secret": app_secret,
        "code": code,
        "redirect_uri": effective_redirect,
    }
    epoch = int(time.time())
    res = httpx.post(token_url, data=payload, timeout=60.0)
    if res.status_code != 200:
        msg = (
            f"Failed to get tokens from {token_url}, "
            f"status={res.status_code}, error: {res.text}"
        )
        raise RuntimeError(msg)

    resp = res.json()
    # v2 API may nest tokens under "data" or return flat
    if resp.get("code") is not None and resp.get("code") != 0:
        msg = f"Invalid token response: {resp}"
        raise RuntimeError(msg)
    data = resp.get("data") or resp

    access_token, access_token_ttl, _refresh_token_ttl, refresh_saved = save_oauth_tokens(
        data,
        token_dir=token_dir,
        epoch=epoch,
    )

    if refresh_saved:
        logger.info("Token initialized with refresh_token")
    else:
        logger.warning(
            "No refresh_token in response (scope: {}). "
            "Token will expire in {}s and require re-auth.",
            data.get("scope", "?"),
            access_token_ttl,
        )

    return access_token


def check_token_valid(
    token_dir: str = DEFAULT_TOKEN_DIR,
) -> bool:
    """Return ``True`` if a stored access token exists and has not expired."""
    path = _token_path(token_dir, "access_token.json")
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ttl = data["expires_epoch"] - int(time.time())
        return ttl > 0
    except (json.JSONDecodeError, KeyError):
        return False


# ---------------------------------------------------------------------------
# Internal: local callback server for autofill flow
# ---------------------------------------------------------------------------


def _wait_for_authorization_code(
    redirect_uri: str,
    auth_url: str,
    timeout: int = 90,
    allow_manual_input: bool = True,
) -> str:
    """Start a temporary HTTP server, open the browser, and wait for the OAuth
    callback.  The user may also paste the code manually while waiting.
    """
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 9527

    authorization_code: str | None = None
    error_message: str | None = None

    server: HTTPServer | None = None
    stop_event = Event()

    class _CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal authorization_code, error_message
            parsed_path = urlparse(self.path)
            query_params = parse_qs(parsed_path.query)
            if "code" in query_params:
                authorization_code = query_params["code"][0]
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write("Authorization successful! You may close this page.".encode())
            else:
                error_message = f"Authorization failed: {query_params}"
                self.send_response(400)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write((error_message or "Unknown error").encode("utf-8"))

        def log_message(self, format, *args):
            logger.debug(f"HTTP: {format % args}")

    logger.info(f"Starting callback server on {host}:{port}")
    server = HTTPServer((host, port), _CallbackHandler)
    server.timeout = 1  # short poll so we can check stop_event

    def _serve_loop():
        while not stop_event.is_set() and authorization_code is None:
            try:
                server.handle_request()  # type: ignore[union-attr]
            except Exception as e:
                logger.debug(f"Callback handler error: {e}")

    webbrowser.open(auth_url)

    t_server = Thread(target=_serve_loop, daemon=True)
    t_server.start()

    red = "\033[31m"
    reset = "\033[0m"
    sys.stdout.write(f"{red}Enter authorization code: {reset}")
    sys.stdout.flush()

    start = time.time()
    while authorization_code is None and (time.time() - start) < timeout:
        if allow_manual_input:
            try:
                rlist, _, _ = select.select([sys.stdin], [], [], 0.2)
                if rlist:
                    line = sys.stdin.readline()
                    if line:
                        authorization_code = line.strip()
                        break
            except KeyboardInterrupt:
                stop_event.set()
                if server:
                    server.server_close()
                sys.exit("\nCanceled by user.")
        else:
            time.sleep(0.1)

    stop_event.set()
    if server:
        server.server_close()

    if not authorization_code:
        sys.exit("\nTimed out waiting for authorization code. " "Please run init_token() again.")

    logger.info(f"Authorization code received: {authorization_code[:4]}...")
    return authorization_code
