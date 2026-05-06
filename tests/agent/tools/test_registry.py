from typing import Any

from src.agent.approval import ApprovalGate, ApprovalRequest, ApprovalResponse
from src.agent.tools.base import ContextAwareTool, Tool
from src.agent.tools.context import ToolContext
from src.agent.tools.registry import ToolRegistry
from src.security.autonomy import AutonomyLevel, AutonomyPolicy


class _ContextTool(ContextAwareTool):
    def __init__(self) -> None:
        self.seen_context: ToolContext | None = None

    @property
    def name(self) -> str:
        return "context_tool"

    @property
    def description(self) -> str:
        return "A context-aware test tool."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        self.seen_context = kwargs.get("_context")
        return "ok"


class _CommandTool(Tool):
    def __init__(self, name: str = "command_tool") -> None:
        self._name = name
        self.commands: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "A command test tool."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"command": {"type": "string", "minLength": 2}},
            "required": ["command"],
        }

    async def execute(self, **kwargs: Any) -> str:
        command = kwargs["command"]
        self.commands.append(command)
        return command


async def test_execute_injects_context_for_context_aware_tool() -> None:
    registry = ToolRegistry()
    tool = _ContextTool()
    context = ToolContext(session_key="cli:direct")
    registry.register(tool)

    result = await registry.execute("context_tool", {}, context=context)

    assert result == "ok"
    assert tool.seen_context is context


async def test_approval_modified_args_are_executed() -> None:
    async def approve(_: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(approved=True, modified_args={"command": "safe"})

    registry = ToolRegistry(approval_gate=ApprovalGate(callback=approve))
    tool = _CommandTool()
    registry.register(tool)

    result = await registry.execute("command_tool", {"command": "original"})

    assert result == "safe"
    assert tool.commands == ["safe"]


async def test_approval_modified_args_are_validated_before_execute() -> None:
    async def approve(_: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(approved=True, modified_args={})

    registry = ToolRegistry(approval_gate=ApprovalGate(callback=approve))
    tool = _CommandTool()
    registry.register(tool)

    result = await registry.execute("command_tool", {"command": "original"})

    assert "Invalid parameters" in result
    assert tool.commands == []


class _AutonomyConfig:
    level = AutonomyLevel.FULL
    workspace_only = False
    forbidden_paths: list[str] = []
    allowed_commands = ["git"]
    max_actions_per_hour = 0
    max_cost_per_day = 0.0
    auto_approve: list[str] = []
    always_ask: list[str] = []


async def test_approval_modified_args_are_rechecked_by_autonomy(tmp_path) -> None:
    async def approve(_: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(approved=True, modified_args={"command": "rm -rf /"})

    registry = ToolRegistry(approval_gate=ApprovalGate(callback=approve))
    registry.set_autonomy(AutonomyPolicy(_AutonomyConfig(), tmp_path))
    tool = _CommandTool(name="bash")
    registry.register(tool)

    result = await registry.execute("bash", {"command": "git status"})

    assert "not in allowed_commands" in result
    assert tool.commands == []
