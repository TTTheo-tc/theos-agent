"""Base class for all pollers."""

from __future__ import annotations

import abc
import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger


@dataclass
class PollerEvent:
    """A new-content event emitted by a poller.

    The ``message`` is injected into the agent loop as an InboundMessage.
    ``metadata`` carries poller-specific context (e.g. tweet URL, author).
    """

    poller_name: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)


PollerEventHandler = Callable[[PollerEvent], Awaitable[None]]


class BasePoller(abc.ABC):
    """Abstract base for a high-frequency poller.

    Subclasses implement ``poll_once()`` which returns a list of
    ``PollerEvent`` (empty list = nothing new).

    The service calls ``poll_once()`` every ``interval_s`` seconds.
    No LLM calls happen here — pure Python only.
    """

    name: str = "base"
    interval_s: float = 1.0  # default: 1 second

    @abc.abstractmethod
    async def setup(self) -> None:
        """One-time initialization (e.g. login, load state)."""

    @abc.abstractmethod
    async def poll_once(self) -> list[PollerEvent]:
        """Check for new content. Return events for new items, empty list otherwise."""

    @abc.abstractmethod
    async def teardown(self) -> None:
        """Cleanup on shutdown."""

    async def run_loop(self, on_event: PollerEventHandler) -> None:
        """Main polling loop. Called by PollerService."""
        logger.info("Poller [{}] starting (interval={}s)", self.name, self.interval_s)
        logger.info("Poller [{}] calling setup()...", self.name)
        try:
            await asyncio.wait_for(self.setup(), timeout=30.0)
            logger.info("Poller [{}] setup completed OK", self.name)
        except asyncio.TimeoutError:
            logger.error("Poller [{}] setup timed out after 30s", self.name)
            return
        except Exception:
            logger.opt(exception=True).error("Poller [{}] setup failed", self.name)
            return
        logger.info("Poller [{}] entering poll loop", self.name)

        while True:
            try:
                events = await self.poll_once()
                for event in events:
                    try:
                        await on_event(event)
                    except Exception:
                        logger.opt(exception=True).warning(
                            "Poller [{}] event handler failed", self.name
                        )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.opt(exception=True).warning(
                    "Poller [{}] poll_once error, retrying in {}s",
                    self.name,
                    self.interval_s,
                )

            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                break

        try:
            await self.teardown()
        except Exception:
            logger.opt(exception=True).warning("Poller [{}] teardown error", self.name)
        logger.info("Poller [{}] stopped", self.name)
