"""HumanEscapeHatch — risk-aware approval gate for tool execution."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from loguru import logger


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ApprovalRequest:
    tool_name: str
    arguments: dict[str, Any]
    risk_level: RiskLevel
    reason: str
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_key: str | None = None


@dataclass
class ApprovalResponse:
    approved: bool
    modified_args: dict[str, Any] | None = None
    reason: str = ""


ApprovalCallback = Callable[[ApprovalRequest], Awaitable[ApprovalResponse]]


class ApprovalGate:
    """Intercepts tool execution and routes through human approval when risk is elevated."""

    def __init__(
        self,
        *,
        callback: ApprovalCallback | None = None,
        auto_approve_levels: set[RiskLevel] | None = None,
        timeout: float = 300.0,
        enabled: bool = True,
    ) -> None:
        self._callback = callback
        self._auto_approve = auto_approve_levels or set()
        self._timeout = timeout
        self.enabled = enabled

    async def check(
        self,
        tool_name: str,
        args: dict[str, Any],
        risk_level: RiskLevel,
        *,
        reason: str = "",
        session_key: str | None = None,
    ) -> ApprovalResponse:
        """Evaluate whether a tool call should proceed.

        Returns an ``ApprovalResponse``; callers should inspect ``.approved``.
        """
        if not self.enabled:
            return ApprovalResponse(approved=True)

        if risk_level in self._auto_approve:
            return ApprovalResponse(approved=True)

        if self._callback is None:
            logger.warning(
                "No approval callback — auto-denying {} (risk={})", tool_name, risk_level.value
            )
            return ApprovalResponse(approved=False, reason="no approval callback registered")

        request = ApprovalRequest(
            tool_name=tool_name,
            arguments=args,
            risk_level=risk_level,
            reason=reason,
            session_key=session_key,
        )

        try:
            return await asyncio.wait_for(self._callback(request), timeout=self._timeout)
        except asyncio.TimeoutError:
            logger.warning("Approval timed out for {} (risk={})", tool_name, risk_level.value)
            return ApprovalResponse(approved=False, reason="approval timed out")
