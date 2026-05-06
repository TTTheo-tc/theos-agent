"""X/Twitter poller — monitors accounts for new posts.

Pure-Python polling with zero token cost.  Only triggers the agent loop
when a genuinely new tweet is detected.

State persistence: last-seen tweet IDs are stored in a JSON file so
restarts don't re-process old tweets.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from src.poller.base import BasePoller, PollerEvent


class XPoller(BasePoller):
    """Monitor X/Twitter accounts for new posts via twscrape."""

    name = "x_monitor"

    def __init__(
        self,
        usernames: list[str],
        *,
        interval_s: float = 60.0,
        state_path: Path | None = None,
        cookies: dict[str, str] | None = None,
        notify_channel: str = "feishu",
        notify_chat_id: str = "",
    ):
        """
        Args:
            usernames: X usernames to monitor (without @).
            interval_s: Polling interval in seconds.
            state_path: Path to persist last-seen tweet IDs.
            cookies: X auth cookies (auth_token, ct0).
            notify_channel: Channel to deliver notifications to.
            notify_chat_id: Chat ID for notifications.
        """
        self.interval_s = interval_s
        self.usernames = [u.lstrip("@").lower() for u in usernames]
        self.state_path = state_path or Path.home() / ".theos" / "data" / "x_poller_state.json"
        self.cookies = cookies or {}
        self.notify_channel = notify_channel
        self.notify_chat_id = notify_chat_id

        # Runtime state
        self._seen: dict[str, set[str]] = {}  # username -> seen tweet IDs
        self._user_ids: dict[str, int] = {}  # username -> numeric user ID (cached)
        self._api: Any = None  # twscrape API instance

    async def setup(self) -> None:
        """Initialize twscrape and load persisted state."""
        # Load persisted state
        self._load_state()

        # Initialize twscrape
        try:
            from twscrape import API, AccountsPool

            logger.info("XPoller: twscrape imported OK")

            db_path = self.state_path.parent / "twscrape.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            # Always start fresh to avoid stale aiosqlite connections
            # from previous process (causes "Event loop is closed" errors)
            if db_path.exists():
                db_path.unlink()
                logger.info("XPoller: removed stale twscrape.db")
            logger.info("XPoller: creating API with db={}", db_path)
            self._api = API(pool=AccountsPool(str(db_path)))

            # Add account from cookies if provided
            if self.cookies.get("auth_token") and self.cookies.get("ct0"):
                cookie_str = json.dumps(
                    {
                        "auth_token": self.cookies["auth_token"],
                        "ct0": self.cookies["ct0"],
                    }
                )
                logger.info("XPoller: adding account with cookies...")
                try:
                    await self._api.pool.add_account(
                        username="monitor_account",
                        password="unused",
                        email="unused",
                        email_password="unused",
                        cookies=cookie_str,
                    )
                    logger.info("XPoller: twscrape account added from cookies")
                except Exception as e:
                    logger.warning("XPoller: add_account note: {}", e)

                # Verify account is active
                stats = await self._api.pool.stats()
                logger.info("XPoller: pool stats = {}", stats)
            else:
                logger.warning(
                    "XPoller: no cookies provided, twscrape may not work. "
                    "Set gateway.pollers.x.cookies in config."
                )

            # Pre-resolve username -> user ID to avoid per-poll API calls
            for username in self.usernames:
                try:
                    user = await self._api.user_by_login(username)
                    if user:
                        self._user_ids[username] = user.id
                        logger.info("XPoller: resolved @{} -> id={}", username, user.id)
                    else:
                        logger.warning("XPoller: user @{} not found, skipping", username)
                except Exception:
                    logger.opt(exception=True).warning("XPoller: failed to resolve @{}", username)

            logger.info(
                "XPoller: setup complete — monitoring {} accounts: {}",
                len(self._user_ids),
                ", ".join(self._user_ids.keys()),
            )
        except ImportError:
            logger.error("XPoller: twscrape not installed. Run: uv add twscrape")
            raise
        except Exception:
            logger.opt(exception=True).error("XPoller: setup failed unexpectedly")
            raise

    async def poll_once(self) -> list[PollerEvent]:
        """Check all monitored accounts for new tweets."""
        if not self._api:
            return []

        events: list[PollerEvent] = []

        for username in self.usernames:
            try:
                new_tweets = await self._check_user(username)
                events.extend(self._tweet_event(username, tweet) for tweet in new_tweets)
            except Exception:
                logger.opt(exception=True).debug("XPoller: error checking @{}", username)

        if events:
            self._save_state()

        return events

    async def _check_user(self, username: str) -> list[Any]:
        """Fetch recent tweets for a user and return only unseen ones."""
        user_id = self._user_ids.get(username)
        if user_id is None:
            return []

        seen = self._seen.setdefault(username, set())
        new_tweets = []

        try:
            async for tweet in self._api.user_tweets(user_id, limit=5):
                tweet_id = str(tweet.id)
                if tweet_id not in seen:
                    seen.add(tweet_id)
                    new_tweets.append(tweet)
        except Exception:
            logger.opt(exception=True).debug("XPoller: failed to fetch tweets for @{}", username)

        return new_tweets

    def _tweet_event(self, username: str, tweet: Any) -> PollerEvent:
        tweet_url = f"https://x.com/{username}/status/{tweet.id}"
        return PollerEvent(
            poller_name=self.name,
            message=self._format_tweet_message(username, tweet),
            metadata={
                "tweet_id": str(tweet.id),
                "username": username,
                "tweet_url": tweet_url,
                "notify_channel": self.notify_channel,
                "notify_chat_id": self.notify_chat_id,
            },
        )

    def _format_tweet_message(self, username: str, tweet: Any) -> str:
        """Format a tweet into a structured message for the agent."""
        url = f"https://x.com/{username}/status/{tweet.id}"
        text = tweet.rawContent if hasattr(tweet, "rawContent") else str(tweet)

        return f"[X Monitor] New post from @{username}\n\n" f"{text}\n\n" f"URL: {url}"

    def _load_state(self) -> None:
        """Load persisted seen-tweet-IDs from disk."""
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                self._seen = {k: set(v) for k, v in data.items()}
                total = sum(len(v) for v in self._seen.values())
                logger.info("XPoller: loaded state ({} seen tweets)", total)
            except Exception:
                logger.opt(exception=True).warning("XPoller: failed to load state")
                self._seen = {}
        else:
            self._seen = {}

    def _save_state(self) -> None:
        """Persist seen-tweet-IDs to disk."""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            data = {k: sorted(v) for k, v in self._seen.items()}
            self.state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            logger.opt(exception=True).warning("XPoller: failed to save state")

    async def teardown(self) -> None:
        """Save state on shutdown."""
        self._save_state()
        logger.info("XPoller: state saved, shutting down")
