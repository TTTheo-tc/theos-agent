"""SubagentWaitTool — wait for a subagent task to complete."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from src.agent.tools.base import ContextAwareTool

if TYPE_CHECKING:
    from src.agent.delegation.executor import SubagentExecutor


class SubagentWaitTool(ContextAwareTool):
    def __init__(self, executor: SubagentExecutor):
        self._executor = executor

    @property
    def name(self) -> str:
        return "subagent_wait"

    @property
    def description(self) -> str:
        return (
            "Wait for a subagent task to complete and return its result. "
            "If the task is still running after timeout_seconds, returns current status."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID returned by the agent tool.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Max seconds to wait. Default 30.",
                    "default": 30,
                },
            },
            "required": ["task_id"],
        }

    async def execute(
        self,
        task_id: str,
        timeout_seconds: int = 30,
        _context: Any = None,
        **kwargs,
    ) -> str:
        result = await self._executor.wait(
            task_id,
            timeout_seconds=timeout_seconds,
            context=_context,
        )
        if result is None:
            return json.dumps({"task_id": task_id, "error": f"Unknown task: {task_id}"})
        return json.dumps(
            {
                "task_id": result.task_id,
                "status": result.status.value,
                "role": result.role,
                "result": result.result,
                "error": result.error,
                "elapsed_seconds": result.elapsed_seconds,
                "tools_used": result.tools_used,
            }
        )
