"""LLM-powered post-task reflector (DEPRECATED for lesson writing).

Lesson writing has been unified under instinct/scripts/reflect.js (I6).
This class is kept for backward compatibility but _do_reflect() is a no-op.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from src.providers.base import LLMProvider


class Reflector:
    """Fire-and-forget reflector — lesson writing deprecated in favor of reflect.js."""

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        workspace: Path,
        *,
        enabled: bool = True,
    ):
        self._provider = provider
        self._model = model
        self._workspace = workspace
        self._enabled = enabled

    async def reflect(
        self,
        *,
        user_message: str,
        response: str,
        tools_used: list[str],
        usage: dict[str, int] | None,
        duration_ms: float,
        session_key: str,
        status: str = "success",
        error: str | None = None,
    ) -> None:
        """No-op — lesson writing now handled by reflect.js. Never raises."""
        if not self._enabled:
            return
        try:
            await self._do_reflect(
                user_message=user_message,
                response=response,
                tools_used=tools_used,
                usage=usage,
                duration_ms=duration_ms,
                session_key=session_key,
                status=status,
                error=error,
            )
        except Exception:
            logger.opt(exception=True).warning("[Reflector] reflect call failed")

    async def _do_reflect(
        self,
        *,
        user_message: str,
        response: str,
        tools_used: list[str],
        usage: dict[str, int] | None,
        duration_ms: float,
        session_key: str,
        status: str,
        error: str | None,
    ) -> None:
        # I6: Lesson writing deprecated — reflect.js is the single lesson path.
        # This method is intentionally a no-op. The Reflector class and its
        # public interface are preserved for backward compatibility.
        logger.debug(
            "[Reflector] Deprecated lesson writer called for session={}, skipping",
            session_key,
        )
