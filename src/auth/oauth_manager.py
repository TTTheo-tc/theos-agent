"""OAuthManager -- resolve, refresh, and background-maintain OAuth credentials."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from loguru import logger

from src.auth.oauth_plugin import OAuthPlugin
from src.auth.types import OAuthCredential


class OAuthManager:
    """Manages OAuth credential lifecycle: resolve, refresh, background upkeep."""

    EXPIRY_BUFFER_S = 300

    def __init__(
        self,
        plugins: dict[str, OAuthPlugin],
        store_path: Path | None = None,
    ) -> None:
        self._plugins = plugins
        self._lock_path = store_path.with_suffix(".oauth.lock") if store_path else None
        self._stop_event = threading.Event()
        self._bg_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, provider: str, profile_id: str) -> tuple[str, dict[str, str]] | None:
        """Return (api_key, headers) for *profile_id*, refreshing if needed."""
        store = self._load_store()
        cred = store.profiles.get(profile_id)
        if cred is None or not isinstance(cred, OAuthCredential):
            return None

        plugin = self._plugins.get(provider)
        if plugin is None:
            logger.warning("No OAuth plugin registered for provider {}", provider)
            return None

        if not self._is_expired(cred):
            return self._format(plugin, cred)

        refreshed = self._refresh_with_lock(provider, profile_id, cred)
        if refreshed is None:
            logger.warning(
                "OAuth refresh failed for {} (profile {}), using expired token",
                provider,
                profile_id,
            )
            # Return the expired credential anyway — the API will reject it, but
            # at least the caller can propagate it for error handling rather than
            # silently falling back to nothing.
            return self._format(plugin, cred)
        return self._format(plugin, refreshed)

    def try_cached(self, provider: str, profile_id: str) -> tuple[str, dict[str, str]] | None:
        """Return cached (api_key, headers) if token is still valid.

        May still read/decrypt the auth store; the key property is that it does not
        perform token refresh, network I/O, or subprocess calls on the request hot path.
        """
        store = self._load_store()
        cred = store.profiles.get(profile_id)
        if cred is None or not isinstance(cred, OAuthCredential):
            return None
        plugin = self._plugins.get(provider)
        if plugin is None:
            return None
        if not self._is_expired(cred):
            return self._format(plugin, cred)
        return None

    def start_background_refresh(self, interval_s: int = 1800) -> None:
        """Start a daemon thread that proactively refreshes expiring tokens."""
        if self._bg_thread is not None and self._bg_thread.is_alive():
            return
        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.is_set():
                try:
                    self._refresh_all_expiring(interval_s * 2)
                except Exception:
                    logger.opt(exception=True).warning("Background OAuth refresh sweep failed")
                self._stop_event.wait(interval_s)

        self._bg_thread = threading.Thread(target=_loop, daemon=True)
        self._bg_thread.start()

    def stop_background_refresh(self) -> None:
        """Signal the background thread to stop and wait for it."""
        self._stop_event.set()
        if self._bg_thread is not None:
            self._bg_thread.join()
            self._bg_thread = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_expired(self, cred: OAuthCredential) -> bool:
        # Buffer is already baked into cred.expires at storage time
        # (plugin.refresh subtracts EXPIRES_BUFFER_S). Simple comparison here.
        return time.time() * 1000 >= cred.expires

    def _format(self, plugin: OAuthPlugin, cred: OAuthCredential) -> tuple[str, dict[str, str]]:
        api_key = plugin.format_api_key(cred)
        headers = plugin.auth_headers(api_key)
        return (api_key, headers)

    def _refresh_with_lock(
        self,
        provider: str,
        profile_id: str,
        cred: OAuthCredential,
    ) -> OAuthCredential | None:
        plugin = self._plugins.get(provider)
        if plugin is None:
            return None

        lock = None
        if self._lock_path is not None:
            from filelock import FileLock

            lock = FileLock(str(self._lock_path), timeout=30)

        try:
            if lock is not None:
                lock.acquire()

            # Double-check: another process may have refreshed while we waited
            store = self._load_store()
            fresh_cred = store.profiles.get(profile_id)
            if (
                fresh_cred is not None
                and isinstance(fresh_cred, OAuthCredential)
                and not self._is_expired(fresh_cred)
            ):
                return fresh_cred

            refreshed = plugin.refresh(cred)
            if refreshed is None:
                return None

            self._save_credential(profile_id, refreshed)
            logger.info(
                "Refreshed OAuth token for {} ({}...)",
                profile_id,
                refreshed.access[:8],
            )
            return refreshed
        except Exception:
            logger.opt(exception=True).warning("OAuth refresh error for {}", profile_id)
            return None
        finally:
            if lock is not None:
                lock.release()

    def _refresh_all_expiring(self, horizon_s: float) -> None:
        """Refresh all OAuthCredential profiles expiring within horizon."""
        store = self._load_store()
        for profile_id, cred in store.profiles.items():
            if isinstance(cred, OAuthCredential):
                remaining = (cred.expires / 1000) - time.time()
                if remaining < horizon_s:
                    self._refresh_with_lock(cred.provider, profile_id, cred)

    def _load_store(self):  # noqa: ANN202
        from src.auth.store import load_auth_store

        return load_auth_store()

    def _save_credential(self, profile_id: str, cred: OAuthCredential) -> None:
        from src.auth.store import load_auth_store, save_auth_store

        store = load_auth_store()
        store.profiles[profile_id] = cred
        save_auth_store(store)
