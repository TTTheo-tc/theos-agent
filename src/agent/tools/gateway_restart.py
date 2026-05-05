"""Safe gateway self-restart tool.

Schedules a delayed kill so the agent has time to finish its response
and persist the session before the process exits. This prevents the
restart-loop problem where conversation context triggers repeated kills.

A notification file is written before the kill so that the new gateway
process can send a "restart complete" message to the user.
"""

from __future__ import annotations

import json
import os
import signal
import threading
from pathlib import Path
from typing import Any

from src.agent.tools.base import ContextAwareTool

_RESTART_DELAY_S = 3
_NOTIFY_FILE = Path.home() / ".theos" / "restart-notify.json"


class GatewayRestartTool(ContextAwareTool):
    """Restart the gateway process safely with a delayed kill."""

    @property
    def name(self) -> str:
        return "gateway_restart"

    @property
    def description(self) -> str:
        return (
            "Restart the gateway process. The restart is delayed by a few "
            "seconds so you MUST send your reply to the user first. After "
            "calling this tool, do NOT call any more tools — just return your "
            "final message. The process will exit automatically and systemd "
            "will restart it with the latest code. A confirmation message "
            "will be sent to the user after the restart completes."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why the restart is needed (logged for debugging).",
                },
            },
            "required": [],
        }

    @property
    def risk_level(self) -> str:
        return "high"

    @property
    def owner_only(self) -> bool:
        return True

    async def execute(self, *, _context=None, **kwargs: Any) -> str:
        reason = kwargs.get("reason", "user requested")

        # Save notification so the new process can confirm restart to the user.
        if _context and _context.channel and _context.chat_id:
            _NOTIFY_FILE.parent.mkdir(parents=True, exist_ok=True)
            _NOTIFY_FILE.write_text(
                json.dumps(
                    {
                        "channel": _context.channel,
                        "chat_id": _context.chat_id,
                        "reason": reason,
                    }
                )
            )

        def _delayed_kill() -> None:
            from loguru import logger

            logger.warning(
                "Gateway self-restart in {}s (reason: {})",
                _RESTART_DELAY_S,
                reason,
            )
            os.kill(os.getpid(), signal.SIGHUP)

        timer = threading.Timer(_RESTART_DELAY_S, _delayed_kill)
        timer.daemon = True
        timer.start()

        return (
            f"Gateway restart scheduled in {_RESTART_DELAY_S}s. "
            "Send your reply to the user NOW — the process will exit shortly."
        )


async def send_restart_notification(bus) -> None:
    """Check for a pending restart notification and send it via the message bus.

    Called once during gateway startup. Waits a few seconds for channels
    to finish initializing before sending the message.
    """
    import asyncio

    if not _NOTIFY_FILE.exists():
        return

    try:
        data = json.loads(_NOTIFY_FILE.read_text())

        # Wait for channels to finish connecting (WebSocket handshake, etc.)
        await asyncio.sleep(8)

        from src.bus.events import OutboundMessage

        await bus.publish_outbound(
            OutboundMessage(
                channel=data["channel"],
                chat_id=data["chat_id"],
                content="Gateway 已重启完成 ✅",
            )
        )
    except Exception:
        from loguru import logger

        logger.opt(exception=True).debug("Failed to send restart notification")
    finally:
        _NOTIFY_FILE.unlink(missing_ok=True)
