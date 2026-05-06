"""Agent tool for launching subagents (renamed from 'spawn' to align with Claude Code)."""

from typing import TYPE_CHECKING, Any

from src.agent.tools.base import ContextAwareTool

if TYPE_CHECKING:
    from src.agent.subagent import SubagentManager


class AgentTool(ContextAwareTool):
    """Launch a subagent to handle a task autonomously."""

    @property
    def owner_only(self) -> bool:
        return True

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager

    @property
    def name(self) -> str:
        return "agent"

    @property
    def description(self) -> str:
        return (
            "Launch a new agent to handle complex, multi-step tasks autonomously. "
            "Each agent runs in its own context with a dedicated tool set. "
            "Use 'role' for specialized behavior, 'model' to override the LLM, "
            "and 'isolation: worktree' for git-isolated workspaces."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task for the agent to complete",
                },
                "label": {
                    "type": "string",
                    "description": "Optional short label for the task (for display)",
                },
                "role": {
                    "type": "string",
                    "description": "Specialized role (e.g. explorer, executor, reviewer). Leave empty for general agent.",
                },
                "model": {
                    "type": "string",
                    "description": "Override the LLM model for this agent. If omitted, inherits from role config or parent.",
                },
                "isolation": {
                    "type": "string",
                    "enum": ["worktree"],
                    "description": "Set to 'worktree' to run in an isolated git worktree copy of the repo.",
                },
                "handoff": {
                    "type": "object",
                    "description": "Optional structured handoff metadata for nested agents.",
                },
                "message_to": {
                    "type": "string",
                    "description": "Send a message to an existing agent by task ID instead of launching a new one",
                },
                "message": {
                    "type": "string",
                    "description": "The message content to send",
                },
            },
            "required": [],
        }

    async def execute(
        self,
        task: str = "",
        _context: Any = None,
        label: str | None = None,
        role: str | None = None,
        model: str | None = None,
        isolation: str | None = None,
        handoff: dict[str, Any] | None = None,
        message_to: str | None = None,
        message: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Launch a subagent or send a message to an existing one."""
        del kwargs
        from src.agent.tools.context import ToolContext

        ctx = _context or ToolContext()

        # Cross-agent messaging: send to existing agent instead of launching
        if message_to:
            return await self._manager.send_message(message_to, message or "")

        if not task:
            return "Error: 'task' is required when launching a new agent."

        return await self._manager.spawn(
            task=task,
            label=label,
            origin_channel=ctx.channel or "cli",
            origin_chat_id=ctx.chat_id or "direct",
            session_key=ctx.session_key or f"{ctx.channel}:{ctx.chat_id}",
            role=role or None,
            model_override=model,
            isolation=isolation,
            root_session_key=ctx.root_session_key,
            parent_task_id=ctx.subagent_task_id,
            depth=ctx.spawn_depth + 1,
            handoff=handoff,
        )
