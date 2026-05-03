"""Channel manager for coordinating chat channels."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from src.bus.queue import MessageBus
from src.channels.base import BaseChannel
from src.config.schema import Config

if TYPE_CHECKING:
    from src.store.dashboard_writer import DashboardWriter


class ChannelManager:
    """
    Manages chat channels and coordinates message routing.

    Responsibilities:
    - Initialize enabled channels (Telegram, WhatsApp, etc.)
    - Start/stop channels
    - Route outbound messages
    """

    def __init__(self, config: Config, bus: MessageBus, dashboard: "DashboardWriter | None" = None):
        self.config = config
        self.bus = bus
        self.dashboard = dashboard
        self.channels: dict[str, BaseChannel] = {}
        self._dispatch_task: asyncio.Task | None = None
        self._restart_cb: Callable[[], None] | None = None
        self._inflight_sends = 0

        self._init_channels()

    def _init_channels(self) -> None:
        """Initialize channels based on config."""
        import importlib

        from src.channels.registry import CHANNELS, _resolve_dotpath
        from src.security.secret_refs import resolve_data_secret_refs

        for spec in CHANNELS:
            ch_config = resolve_data_secret_refs(getattr(self.config.channels, spec.config_attr))
            if not ch_config.enabled:
                continue
            try:
                mod = importlib.import_module(spec.module)
                cls = getattr(mod, spec.class_name)
                kwargs: dict[str, Any] = {}
                for kwarg_name, dotpath in spec.extra_kwargs:
                    kwargs[kwarg_name] = _resolve_dotpath(self.config, dotpath)
                self.channels[spec.name] = cls(
                    ch_config, self.bus, **kwargs, owner_ids=self.config.channels.owner_ids
                )
                logger.info("{} channel enabled", spec.name.capitalize())
            except ImportError as e:
                logger.warning("{} channel not available: {}", spec.name.capitalize(), e)

    async def _start_channel(self, name: str, channel: BaseChannel) -> None:
        """Start a channel and log any exceptions."""
        try:
            await channel.start()
        except Exception:
            logger.opt(exception=True).warning("Failed to start channel {}", name)

    async def start_all(self) -> None:
        """Start all channels and the outbound dispatcher."""
        if not self.channels:
            logger.warning("No channels enabled")
            return

        # Mark channels online in dashboard
        if self.dashboard:
            for name in self.channels:
                asyncio.ensure_future(self.dashboard.upsert_channel_stat(name, online=True))

        # Start outbound dispatcher
        self._dispatch_task = asyncio.create_task(self._dispatch_outbound())

        # Start channels
        tasks = []
        for name, channel in self.channels.items():
            channel.resume_inbound()
            logger.info("Starting {} channel...", name)
            tasks.append(asyncio.create_task(self._start_channel(name, channel)))

        # Wait for all to complete (they should run forever)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_all(self) -> None:
        """Stop all channels and the dispatcher."""
        # Mark channels offline in dashboard
        if self.dashboard:
            for name in self.channels:
                asyncio.ensure_future(self.dashboard.upsert_channel_stat(name, online=False))

        logger.info("Stopping all channels...")

        # Stop dispatcher
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass

        # Stop all channels
        for name, channel in self.channels.items():
            try:
                await channel.stop()
                logger.info("Stopped {} channel", name)
            except Exception:
                logger.opt(exception=True).warning("Error stopping {}", name)

    def set_restart_callback(self, callback: Callable[[], None] | None) -> None:
        """Register a callback invoked after a restart marker message is sent."""
        self._restart_cb = callback

    def pause_inbound(self) -> None:
        """Quiesce channels: stop accepting new inbound messages."""
        for channel in self.channels.values():
            channel.pause_inbound()

    async def wait_outbound_idle(self, timeout: float = 5.0) -> bool:
        """Wait until the outbound queue is drained and no sends are in flight."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            if self.bus.outbound_size == 0 and self._inflight_sends == 0:
                return True
            if asyncio.get_running_loop().time() >= deadline:
                logger.warning(
                    "Timed out waiting for outbound idle (queue={}, inflight={})",
                    self.bus.outbound_size,
                    self._inflight_sends,
                )
                return False
            await asyncio.sleep(0.05)

    async def _dispatch_outbound(self) -> None:
        """Dispatch outbound messages to the appropriate channel."""
        logger.info("Outbound dispatcher started")

        while True:
            try:
                msg = await asyncio.wait_for(self.bus.consume_outbound(), timeout=1.0)
                channel = self.channels.get(msg.channel)

                if channel is None:
                    logger.warning("Unknown channel: {}", msg.channel)
                    continue

                if msg.metadata.get("_progress"):
                    # Always skip streaming deltas — channels get the final response
                    if msg.metadata.get("_progress_kind") == "stream":
                        continue
                    if msg.metadata.get("_tool_hint") and not self.config.channels.send_tool_hints:
                        continue
                    if (
                        not msg.metadata.get("_tool_hint")
                        and not self.config.channels.send_progress
                    ):
                        continue
                    if not channel.supports_internal_progress:
                        msg = channel.transform_progress_message(msg)
                        if msg is None:
                            continue

                try:
                    self._inflight_sends += 1
                    await channel.send(msg)
                    if msg.metadata.get("_restart_after_send") and self._restart_cb is not None:
                        logger.info(
                            "Restart marker delivered on channel={} chat_id={}",
                            msg.channel,
                            msg.chat_id,
                        )
                        self._restart_cb()
                except Exception:
                    has_media = bool(msg.media)
                    content_chars = len(msg.content or "")
                    logger.opt(exception=True).warning(
                        "Outbound send failed | channel={} chat_id={} has_media={} content_chars={}",
                        msg.channel,
                        msg.chat_id,
                        has_media,
                        content_chars,
                    )
                finally:
                    if self._inflight_sends > 0:
                        self._inflight_sends -= 1

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def get_channel(self, name: str) -> BaseChannel | None:
        """Get a channel by name."""
        return self.channels.get(name)

    def get_status(self) -> dict[str, Any]:
        """Get status of all channels."""
        return {
            name: {"enabled": True, "running": channel.is_running}
            for name, channel in self.channels.items()
        }

    @property
    def enabled_channels(self) -> list[str]:
        """Get list of enabled channel names."""
        return list(self.channels.keys())
