"""Session orchestration tools — list, inspect, and message sessions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from src.agent.tools.base import ContextAwareTool, Tool
from src.session.runtime_state import build_session_runtime_state

if TYPE_CHECKING:
    from src.agent.subagent import SubagentManager
    from src.bus.queue import MessageBus
    from src.session.manager import SessionManager
    from src.session.subagent_store import SubagentStore
    from src.session.turn_store import TurnStore


# ---------------------------------------------------------------------------
# SessionsListTool
# ---------------------------------------------------------------------------


class SessionsListTool(Tool):
    """List all active conversation sessions."""

    def __init__(
        self,
        session_manager: "SessionManager",
        turn_store: "TurnStore | None" = None,
        subagent_store: "SubagentStore | None" = None,
    ) -> None:
        self._sm = session_manager
        self._turns = turn_store
        self._subagents = subagent_store

    @property
    def name(self) -> str:
        return "sessions_list"

    @property
    def description(self) -> str:
        return (
            "List active conversation sessions with key, channel, "
            "message count, and last update time."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum number of sessions to return (default: 20).",
                },
                "recoverable_only": {
                    "type": "boolean",
                    "description": "When true, only return sessions with recoverable turn or background state.",
                },
            },
            "required": [],
        }

    async def execute(self, limit: int = 20, recoverable_only: bool = False, **kwargs: Any) -> str:
        raw = self._sm.list_sessions()
        rows = []
        for info in raw:
            key = info.get("key", "")
            session = self._sm.get_or_create(key)
            runtime = build_session_runtime_state(
                key,
                turn_store=self._turns,
                subagent_store=self._subagents,
                recent_background_limit=3,
            )
            if recoverable_only and not runtime.recoverable:
                continue
            rows.append(
                {
                    "key": key,
                    "message_count": len(session.messages),
                    "created_at": info.get("created_at"),
                    "updated_at": info.get("updated_at"),
                    **self._latest_turn_payload(runtime),
                    **self._background_payload(runtime),
                    "recoverable": runtime.recoverable,
                    "runtime_state": runtime.runtime_state,
                    "next_step": runtime.next_step,
                }
            )
            if len(rows) >= limit:
                break

        if not rows:
            return json.dumps({"count": 0, "sessions": []})

        return json.dumps({"count": len(rows), "sessions": rows}, ensure_ascii=False)

    def _latest_turn_payload(self, runtime: Any) -> dict[str, Any]:
        latest = runtime.latest_turn
        if latest is None:
            return {}
        payload: dict[str, Any] = {
            "latest_turn_id": latest.turn_id,
            "latest_turn_status": latest.status,
            "latest_turn_timestamp": latest.timestamp,
        }
        if "question" in latest.metadata:
            payload["pending_question"] = latest.metadata["question"]
        return payload

    def _background_payload(self, runtime: Any) -> dict[str, Any]:
        if self._subagents is None:
            return {}
        active = runtime.active_background
        recent = runtime.recent_background
        payload: dict[str, Any] = {
            "background_task_count": len(active),
        }
        if recent:
            payload["recent_background_tasks"] = [
                {
                    "task_id": cp.task_id,
                    "status": cp.status,
                    "label": cp.metadata.get("label"),
                    "role": cp.metadata.get("role"),
                }
                for cp in recent
            ]
        return payload


# ---------------------------------------------------------------------------
# SessionsHistoryTool
# ---------------------------------------------------------------------------


class SessionsHistoryTool(Tool):
    """Fetch message history for a specific session."""

    def __init__(
        self,
        session_manager: "SessionManager",
        turn_store: "TurnStore | None" = None,
        subagent_store: "SubagentStore | None" = None,
    ) -> None:
        self._sm = session_manager
        self._turns = turn_store
        self._subagents = subagent_store

    @property
    def name(self) -> str:
        return "sessions_history"

    @property
    def description(self) -> str:
        return "Fetch message history for a session by its key."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_key": {
                    "type": "string",
                    "description": "Session key (e.g. 'telegram:12345' or 'cli:direct').",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum number of messages to return (default: 50).",
                },
            },
            "required": ["session_key"],
        }

    _MAX_CONTENT_CHARS = 2000

    async def execute(self, session_key: str = "", limit: int = 50, **kwargs: Any) -> str:
        if not session_key:
            return json.dumps({"error": "session_key is required"})

        session = self._sm.get_or_create(session_key)
        runtime = build_session_runtime_state(
            session_key,
            turn_store=self._turns,
            subagent_store=self._subagents,
            recent_background_limit=5,
        )
        if not session.messages:
            return json.dumps(
                {
                    "session_key": session_key,
                    "count": 0,
                    "total": 0,
                    "messages": [],
                    "latest_turn": self._latest_turn_payload(runtime),
                    "background_tasks": self._background_payload(runtime),
                    "recoverable": runtime.recoverable,
                    "runtime_state": runtime.runtime_state,
                    "next_step": runtime.next_step,
                },
                ensure_ascii=False,
            )

        msgs = session.messages[-limit:]
        sanitized = []
        for m in msgs:
            entry: dict[str, Any] = {
                "role": m.get("role", ""),
            }
            content = m.get("content", "")
            if isinstance(content, str) and len(content) > self._MAX_CONTENT_CHARS:
                entry["content"] = content[: self._MAX_CONTENT_CHARS] + "\n…(truncated)"
                entry["truncated"] = True
            else:
                entry["content"] = content
            if "timestamp" in m:
                entry["timestamp"] = m["timestamp"]
            sanitized.append(entry)

        return json.dumps(
            {
                "session_key": session_key,
                "count": len(sanitized),
                "total": len(session.messages),
                "messages": sanitized,
                "latest_turn": self._latest_turn_payload(runtime),
                "background_tasks": self._background_payload(runtime),
                "recoverable": runtime.recoverable,
                "runtime_state": runtime.runtime_state,
                "next_step": runtime.next_step,
            },
            ensure_ascii=False,
        )

    def _latest_turn_payload(self, runtime: Any) -> dict[str, Any] | None:
        latest = runtime.latest_turn
        if latest is None:
            return None
        payload: dict[str, Any] = {
            "turn_id": latest.turn_id,
            "status": latest.status,
            "timestamp": latest.timestamp,
        }
        payload.update(latest.metadata)
        return payload

    def _background_payload(self, runtime: Any) -> dict[str, Any] | None:
        if self._subagents is None:
            return None
        active = runtime.active_background
        recent = runtime.recent_background
        return {
            "active_count": len(active),
            "recent": [
                {
                    "task_id": cp.task_id,
                    "status": cp.status,
                    "label": cp.metadata.get("label"),
                    "role": cp.metadata.get("role"),
                    "task": cp.metadata.get("task"),
                    "timestamp": cp.timestamp,
                }
                for cp in recent
            ],
        }


# ---------------------------------------------------------------------------
# SessionsSendTool
# ---------------------------------------------------------------------------


class SessionsSendTool(ContextAwareTool):
    """Send a message into another session via the message bus."""

    def __init__(self, bus: "MessageBus") -> None:
        self._bus = bus

    @property
    def owner_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "sessions_send"

    @property
    def description(self) -> str:
        return (
            "Send a message into another session. The message is injected "
            "via the internal message bus and processed by the agent loop."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "session_key": {
                    "type": "string",
                    "description": "Target session key (e.g. 'telegram:12345').",
                },
                "message": {
                    "type": "string",
                    "description": "Message content to send.",
                },
            },
            "required": ["session_key", "message"],
        }

    async def execute(
        self,
        session_key: str = "",
        message: str = "",
        _context: Any = None,
        **kwargs: Any,
    ) -> str:
        if not session_key:
            return json.dumps({"status": "error", "error": "session_key is required"})
        if not message:
            return json.dumps({"status": "error", "error": "message is required"})

        from src.agent.tools.context import ToolContext
        from src.bus.events import InboundMessage

        ctx = _context or ToolContext()

        # Parse target session key into channel:chat_id
        parts = session_key.split(":", 1)
        if len(parts) != 2:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"Invalid session_key format: {session_key!r}. Expected 'channel:chat_id'.",
                }
            )

        target_channel, target_chat_id = parts

        msg = InboundMessage(
            channel=target_channel,
            sender_id=ctx.sender_id or "sessions_send",
            chat_id=target_chat_id,
            content=message,
            metadata={"source": "sessions_send", "origin_session": ctx.session_key},
            sender_is_owner=ctx.sender_is_owner,
        )

        try:
            await self._bus.publish_inbound(msg)
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})

        return json.dumps(
            {
                "status": "sent",
                "session_key": session_key,
                "message_length": len(message),
            }
        )


# ---------------------------------------------------------------------------
# SubagentsListTool
# ---------------------------------------------------------------------------


class SubagentsListTool(ContextAwareTool):
    """List currently active subagent tasks."""

    def __init__(self, manager: "SubagentManager") -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "subagents_list"

    @property
    def description(self) -> str:
        return "List currently running subagent tasks with their IDs and status."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, _context: Any = None, **kwargs: Any) -> str:
        executor = getattr(self._manager, "executor", None)
        records = executor.list_tasks(context=_context) if executor is not None else []
        running = sum(1 for r in records if not r.is_terminal)
        tasks = []
        session_map: dict[str, list[str]] = {}
        for r in records:
            tasks.append(
                {
                    "task_id": r.task_id,
                    "role": r.role,
                    "status": r.status.value,
                    "parent_task_id": r.parent_task_id,
                    "depth": r.depth,
                    "done": r.is_terminal,
                    "cancelled": r.status.value == "cancelled",
                    "has_result": r.result is not None,
                }
            )
            session_map.setdefault(r.root_session_key, []).append(r.task_id)
        return json.dumps(
            {
                "count": len(tasks),
                "running": running,
                "tasks": tasks,
                "session_tasks": session_map,
            }
        )
