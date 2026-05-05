"""SubagentKillTool — cancel a running subagent task."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from src.agent.tools.base import ContextAwareTool

if TYPE_CHECKING:
    from src.agent.delegation.executor import SubagentExecutor


class SubagentKillTool(ContextAwareTool):
    def __init__(self, executor: SubagentExecutor):
        self._executor = executor

    @property
    def name(self) -> str:
        return "subagent_kill"

    @property
    def description(self) -> str:
        return "Cancel a running subagent task. By default cascades to child tasks."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID to cancel.",
                },
                "cascade": {
                    "type": "boolean",
                    "description": "Also cancel child tasks. Default true.",
                    "default": True,
                },
            },
            "required": ["task_id"],
        }

    async def execute(
        self,
        task_id: str,
        cascade: bool = True,
        _context: Any = None,
        **kwargs,
    ) -> str:
        ok = await self._executor.kill(task_id, cascade=cascade, context=_context)
        return json.dumps({"task_id": task_id, "killed": ok, "cascade": cascade})
