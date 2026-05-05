"""TurnLifecycle — unified dispatcher entry point for message processing."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from loguru import logger

from src.bus.events import OutboundMessage
from src.orchestrator.turn_record import TurnRecord

if TYPE_CHECKING:
    from src.agent.loop import AgentLoop
    from src.bus.events import InboundMessage
    from src.orchestrator.policies import ExecutionPolicy


class TurnLifecycle:
    """Single entry point for PerGroupDispatcher.

    Every message flows through ``handle_message`` which creates a ``TurnRecord``,
    runs policy hooks (before/after/retry), and publishes the response.

    When no policies are installed, failures still run the post-chat hook and
    publish a fallback error response.
    """

    def __init__(
        self,
        agent: AgentLoop,
        policies: list[ExecutionPolicy] | None = None,
    ) -> None:
        self.agent = agent
        self.policies: list[ExecutionPolicy] = policies or []

    async def handle_message(self, msg: InboundMessage) -> None:
        """Single entry point for PerGroupDispatcher.

        Runs message processing with optional policy hooks.
        """
        turn = TurnRecord(turn_id=uuid4().hex[:12], session_key=msg.session_key)

        for p in self.policies:
            await p.before_execute(turn, msg)

        turn.status = "executing"
        try:
            response = await self._execute_with_retry(turn, msg)
            turn.status = "completed"
            for p in self.policies:
                await p.after_success(turn, msg, response)
            await self._publish_response(turn, msg, response)
        except asyncio.CancelledError:
            turn.status = "cancelled"
            raise
        except Exception as exc:
            turn.status = "failed"
            turn.error = str(exc)
            if not self.policies:
                await self._run_failed_post_chat(turn, msg, exc)
            for p in self.policies:
                await p.after_failure(turn, msg, exc)
            await self._publish_error(turn, msg)
        finally:
            turn.duration_ms = (time.monotonic() - turn.created_at) * 1000
            logger.debug("Turn {} {} in {:.0f}ms", turn.turn_id, turn.status, turn.duration_ms)

    _MAX_RETRY_ATTEMPTS = 10

    async def _execute_with_retry(self, turn: TurnRecord, msg: InboundMessage) -> Any:
        """Run ``_process_message``, consulting policies for retry decisions."""
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self.agent._process_message(msg, turn_id=turn.turn_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt >= self._MAX_RETRY_ATTEMPTS:
                    logger.warning(
                        "Turn {} exceeded max retry attempts ({}), giving up",
                        turn.turn_id,
                        self._MAX_RETRY_ATTEMPTS,
                    )
                    raise
                retry_policies = [p for p in self.policies if p.should_retry(turn, exc)]
                if retry_policies:
                    for p in retry_policies:
                        await p.on_retry(turn, msg, exc, attempt)
                    continue
                raise

    async def _run_failed_post_chat(
        self, turn: TurnRecord, msg: InboundMessage, exc: Exception
    ) -> None:
        """Always-on failed hook path for the no-policy lifecycle.

        Only fires when no policies are installed — when policies are present,
        they own failure handling via ``after_failure``.
        """
        asyncio.create_task(
            self.agent.hooks.run_post_chat(
                msg.session_key,
                error=str(exc),
                status="failed",
                user_message=msg.content,
                tools_used=[],
                usage={},
                duration_ms=None,
                routing_domains=[],
                selected_primary=None,
                artifacts=[],
                tests=[],
                workspace=self.agent.workspace,
            )
        )

    async def _publish_response(self, turn: TurnRecord, msg: InboundMessage, response: Any) -> None:
        """Publish outbound response with ``turn_id`` in metadata."""
        if response is not None:
            response.metadata.setdefault("turn_id", turn.turn_id)
            await self.agent.bus.publish_outbound(response)
        elif msg.channel == "cli":
            await self.agent.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="",
                    metadata={**(msg.metadata or {}), "turn_id": turn.turn_id},
                )
            )

    async def _publish_error(self, turn: TurnRecord, msg: InboundMessage) -> None:
        """Publish error fallback with ``turn_id``."""
        await self.agent.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Sorry, I encountered an error.",
                metadata={"turn_id": turn.turn_id},
            )
        )

    async def close(self) -> None:
        """Close all policies, releasing any resources they own."""
        for p in self.policies:
            await p.close()
