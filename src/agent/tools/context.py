"""Tool execution context passed per-call instead of stored as mutable state."""

from dataclasses import dataclass


@dataclass
class ToolContext:
    """Context for a single tool execution."""

    channel: str = ""
    chat_id: str = ""
    message_id: str | None = None
    session_key: str | None = None
    sender_id: str = ""
    sender_is_owner: bool = True  # Default True for backward compat (CLI = owner)
    root_session_key: str | None = None
    subagent_task_id: str | None = None
    spawn_depth: int = 0
    allow_subagent_spawn: bool = False
