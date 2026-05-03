"""Plan mode tools — enter and exit read-only plan mode."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.agent.tools.base import Tool

if TYPE_CHECKING:
    from src.agent.tools.registry import ToolRegistry


class EnterPlanModeTool(Tool):
    """Switch the tool registry to plan (read-only) mode."""

    def __init__(self, registry: "ToolRegistry") -> None:
        self._registry = registry

    @property
    def name(self) -> str:
        return "enter_plan_mode"

    @property
    def description(self) -> str:
        return (
            "Enter plan mode — restricts available tools to read-only operations "
            "for safe exploration and planning."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def parallel_safe(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        if self._registry.plan_mode:
            return "Already in plan mode."
        self._registry.enter_plan_mode()
        return (
            "Entered plan mode. Only read-only tools are available. "
            "Use exit_plan_mode to return to normal mode."
        )


class ExitPlanModeTool(Tool):
    """Exit plan mode and restore full tool access."""

    def __init__(self, registry: "ToolRegistry") -> None:
        self._registry = registry

    @property
    def name(self) -> str:
        return "exit_plan_mode"

    @property
    def description(self) -> str:
        return "Exit plan mode and restore full tool access."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def parallel_safe(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        if not self._registry.plan_mode:
            return "Not in plan mode."
        self._registry.exit_plan_mode()
        return "Exited plan mode. Full tool access restored."
