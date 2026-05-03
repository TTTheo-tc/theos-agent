"""Tests for session orchestration tools."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agent.tools.context import ToolContext
from src.agent.tools.sessions import (
    SessionsHistoryTool,
    SessionsListTool,
    SessionsSendTool,
    SubagentsListTool,
)
from src.bus.events import InboundMessage
from src.bus.queue import MessageBus
from src.session.manager import SessionManager
from src.session.subagent_store import SubagentStore
from src.session.turn_store import TurnStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sm(tmp_path: Path) -> SessionManager:
    return SessionManager(tmp_path)


@pytest.fixture
def turns(tmp_path: Path) -> TurnStore:
    return TurnStore(tmp_path)


@pytest.fixture
def subagents(tmp_path: Path) -> SubagentStore:
    return SubagentStore(tmp_path)


@pytest.fixture
def bus() -> MessageBus:
    return MessageBus()


def _make_subagent_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.executor = None  # No executor — triggers legacy fallback path
    mgr._running_tasks = {}
    mgr._session_tasks = {}
    return mgr


# ---------------------------------------------------------------------------
# SessionsListTool
# ---------------------------------------------------------------------------


class TestSessionsListTool:
    def test_schema(self, sm: SessionManager):
        tool = SessionsListTool(session_manager=sm)
        assert tool.name == "sessions_list"
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "sessions_list"

    @pytest.mark.asyncio
    async def test_empty(self, sm: SessionManager):
        tool = SessionsListTool(session_manager=sm)
        result = json.loads(await tool.execute())
        assert result["count"] == 0
        assert result["sessions"] == []

    @pytest.mark.asyncio
    async def test_lists_sessions(
        self,
        sm: SessionManager,
        turns: TurnStore,
        subagents: SubagentStore,
    ):
        s1 = sm.get_or_create("cli:direct")
        s1.add_message("user", "hello")
        s1.add_message("assistant", "hi")
        sm.save(s1)
        turns.record("cli:direct", "turn-1", "waiting_user", question="Need clarification")
        subagents.record("cli:direct", "sub-1", "running", label="explore repo", role="explorer")

        s2 = sm.get_or_create("telegram:123")
        s2.add_message("user", "test")
        sm.save(s2)

        tool = SessionsListTool(session_manager=sm, turn_store=turns, subagent_store=subagents)
        result = json.loads(await tool.execute())
        assert result["count"] == 2

        keys = {s["key"] for s in result["sessions"]}
        assert "cli:direct" in keys
        assert "telegram:123" in keys

        # Check message counts
        by_key = {s["key"]: s for s in result["sessions"]}
        assert by_key["cli:direct"]["message_count"] == 2
        assert by_key["telegram:123"]["message_count"] == 1
        assert by_key["cli:direct"]["latest_turn_status"] == "waiting_user"
        assert by_key["cli:direct"]["pending_question"] == "Need clarification"
        assert by_key["cli:direct"]["background_task_count"] == 1
        assert by_key["cli:direct"]["recoverable"] is True
        assert by_key["cli:direct"]["runtime_state"] == "waiting_user"
        assert "Reply in the same session" in by_key["cli:direct"]["next_step"]
        assert by_key["cli:direct"]["recent_background_tasks"][0]["label"] == "explore repo"

    @pytest.mark.asyncio
    async def test_limit(self, sm: SessionManager):
        for i in range(5):
            s = sm.get_or_create(f"cli:test{i}")
            s.add_message("user", f"msg {i}")
            sm.save(s)

        tool = SessionsListTool(session_manager=sm)
        result = json.loads(await tool.execute(limit=3))
        assert result["count"] == 3

    @pytest.mark.asyncio
    async def test_recoverable_only_filters_sessions(
        self,
        sm: SessionManager,
        turns: TurnStore,
    ):
        recoverable = sm.get_or_create("cli:recoverable")
        recoverable.add_message("user", "hello")
        sm.save(recoverable)
        turns.record("cli:recoverable", "turn-1", "failed", error="boom")

        plain = sm.get_or_create("cli:plain")
        plain.add_message("user", "hi")
        sm.save(plain)

        tool = SessionsListTool(session_manager=sm, turn_store=turns)
        result = json.loads(await tool.execute(recoverable_only=True))
        assert result["count"] == 1
        assert result["sessions"][0]["key"] == "cli:recoverable"
        assert result["sessions"][0]["next_step"].startswith("Inspect the recorded error")

    def test_risk_level(self, sm: SessionManager):
        assert SessionsListTool(session_manager=sm).risk_level == "low"


# ---------------------------------------------------------------------------
# SessionsHistoryTool
# ---------------------------------------------------------------------------


class TestSessionsHistoryTool:
    def test_schema(self, sm: SessionManager):
        tool = SessionsHistoryTool(session_manager=sm)
        assert tool.name == "sessions_history"
        params = tool.parameters
        assert "session_key" in params["properties"]
        assert "session_key" in params["required"]

    @pytest.mark.asyncio
    async def test_missing_key(self, sm: SessionManager):
        tool = SessionsHistoryTool(session_manager=sm)
        result = json.loads(await tool.execute(session_key=""))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_empty_session(self, sm: SessionManager, turns: TurnStore):
        turns.record("cli:nonexistent", "turn-1", "failed", error="boom")
        tool = SessionsHistoryTool(session_manager=sm, turn_store=turns)
        result = json.loads(await tool.execute(session_key="cli:nonexistent"))
        assert result["count"] == 0
        assert result["messages"] == []
        assert result["recoverable"] is True
        assert result["runtime_state"] == "failed"

    @pytest.mark.asyncio
    async def test_returns_messages(
        self,
        sm: SessionManager,
        turns: TurnStore,
        subagents: SubagentStore,
    ):
        s = sm.get_or_create("cli:direct")
        s.add_message("user", "hello")
        s.add_message("assistant", "world")
        sm.save(s)
        turns.record("cli:direct", "turn-1", "interrupted", interrupted_from="inferring")
        subagents.record(
            "cli:direct", "sub-1", "interrupted", label="explore repo", role="explorer"
        )

        tool = SessionsHistoryTool(
            session_manager=sm,
            turn_store=turns,
            subagent_store=subagents,
        )
        result = json.loads(await tool.execute(session_key="cli:direct"))
        assert result["count"] == 2
        assert result["total"] == 2
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][0]["content"] == "hello"
        assert result["messages"][1]["role"] == "assistant"
        assert result["latest_turn"]["status"] == "interrupted"
        assert result["latest_turn"]["interrupted_from"] == "inferring"
        assert result["background_tasks"]["active_count"] == 0
        assert result["background_tasks"]["recent"][0]["status"] == "interrupted"
        assert result["recoverable"] is True
        assert result["runtime_state"] == "interrupted"
        assert "Re-send the last request" in result["next_step"]

    @pytest.mark.asyncio
    async def test_limit(self, sm: SessionManager):
        s = sm.get_or_create("cli:direct")
        for i in range(10):
            s.add_message("user", f"msg {i}")
        sm.save(s)

        tool = SessionsHistoryTool(session_manager=sm)
        result = json.loads(await tool.execute(session_key="cli:direct", limit=3))
        assert result["count"] == 3
        assert result["total"] == 10
        # Should return the last 3 messages
        assert result["messages"][0]["content"] == "msg 7"

    @pytest.mark.asyncio
    async def test_truncates_long_content(self, sm: SessionManager):
        s = sm.get_or_create("cli:direct")
        s.add_message("user", "x" * 5000)
        sm.save(s)

        tool = SessionsHistoryTool(session_manager=sm)
        result = json.loads(await tool.execute(session_key="cli:direct"))
        msg = result["messages"][0]
        assert msg["truncated"] is True
        assert len(msg["content"]) < 5000
        assert "truncated" in msg["content"]


# ---------------------------------------------------------------------------
# SessionsSendTool
# ---------------------------------------------------------------------------


class TestSessionsSendTool:
    def test_schema(self, bus: MessageBus):
        tool = SessionsSendTool(bus=bus)
        assert tool.name == "sessions_send"
        params = tool.parameters
        assert "session_key" in params["required"]
        assert "message" in params["required"]

    def test_owner_only(self, bus: MessageBus):
        assert SessionsSendTool(bus=bus).owner_only is True

    @pytest.mark.asyncio
    async def test_missing_session_key(self, bus: MessageBus):
        tool = SessionsSendTool(bus=bus)
        result = json.loads(await tool.execute(session_key="", message="hi"))
        assert result["status"] == "error"
        assert "session_key" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_message(self, bus: MessageBus):
        tool = SessionsSendTool(bus=bus)
        result = json.loads(await tool.execute(session_key="cli:direct", message=""))
        assert result["status"] == "error"
        assert "message" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_session_key_format(self, bus: MessageBus):
        tool = SessionsSendTool(bus=bus)
        result = json.loads(await tool.execute(session_key="nocolon", message="hi"))
        assert result["status"] == "error"
        assert "Invalid session_key" in result["error"]

    @pytest.mark.asyncio
    async def test_sends_message(self, bus: MessageBus):
        tool = SessionsSendTool(bus=bus)
        ctx = ToolContext(
            channel="cli",
            chat_id="direct",
            sender_id="owner",
            session_key="cli:direct",
        )
        result = json.loads(
            await tool.execute(
                session_key="telegram:456",
                message="hello there",
                _context=ctx,
            )
        )
        assert result["status"] == "sent"
        assert result["session_key"] == "telegram:456"
        assert result["message_length"] == len("hello there")

        # Verify the message was published to the bus
        assert bus.inbound_size == 1
        msg = bus.inbound.get_nowait()
        assert isinstance(msg, InboundMessage)
        assert msg.channel == "telegram"
        assert msg.chat_id == "456"
        assert msg.content == "hello there"
        assert msg.sender_id == "owner"
        assert msg.sender_is_owner is True
        assert msg.metadata["source"] == "sessions_send"

    @pytest.mark.asyncio
    async def test_sends_without_context(self, bus: MessageBus):
        tool = SessionsSendTool(bus=bus)
        result = json.loads(await tool.execute(session_key="cli:direct", message="test"))
        assert result["status"] == "sent"

        msg = bus.inbound.get_nowait()
        assert msg.channel == "cli"
        assert msg.chat_id == "direct"
        assert msg.sender_id == "sessions_send"  # fallback
        assert msg.sender_is_owner is True


# ---------------------------------------------------------------------------
# SubagentsListTool
# ---------------------------------------------------------------------------


class TestSubagentsListTool:
    def test_schema(self):
        mgr = _make_subagent_manager()
        tool = SubagentsListTool(manager=mgr)
        assert tool.name == "subagents_list"
        schema = tool.to_schema()
        assert schema["function"]["name"] == "subagents_list"

    @pytest.mark.asyncio
    async def test_empty(self):
        mgr = _make_subagent_manager()
        tool = SubagentsListTool(manager=mgr)
        result = json.loads(await tool.execute())
        assert result["count"] == 0
        assert result["running"] == 0
        assert result["tasks"] == []

    @pytest.mark.asyncio
    async def test_with_running_tasks(self):
        mgr = _make_subagent_manager()

        # Simulate running tasks
        loop = asyncio.get_event_loop()
        future1 = loop.create_future()
        task1 = asyncio.ensure_future(future1)
        future2 = loop.create_future()
        task2 = asyncio.ensure_future(future2)

        mgr._running_tasks = {"abc123": task1, "def456": task2}
        mgr._session_tasks = {"cli:direct": {"abc123", "def456"}}

        tool = SubagentsListTool(manager=mgr)
        result = json.loads(await tool.execute())

        assert result["count"] == 2
        assert result["running"] == 2

        task_ids = {t["task_id"] for t in result["tasks"]}
        assert "abc123" in task_ids
        assert "def456" in task_ids

        for t in result["tasks"]:
            assert t["done"] is False
            assert t["cancelled"] is False

        assert "cli:direct" in result["session_tasks"]

        # Cleanup
        future1.set_result(None)
        future2.set_result(None)
        await asyncio.gather(task1, task2, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_with_done_task(self):
        mgr = _make_subagent_manager()

        async def noop():
            pass

        task = asyncio.ensure_future(noop())
        await task  # let it complete

        mgr._running_tasks = {"done1": task}

        tool = SubagentsListTool(manager=mgr)
        result = json.loads(await tool.execute())

        assert result["count"] == 1
        assert result["running"] == 0
        assert result["tasks"][0]["done"] is True

    def test_risk_level(self):
        mgr = _make_subagent_manager()
        assert SubagentsListTool(manager=mgr).risk_level == "low"

    def test_owner_only_is_false(self):
        """SubagentsListTool is read-only, not owner_only."""
        mgr = _make_subagent_manager()
        assert SubagentsListTool(manager=mgr).owner_only is False


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_tools_registered_in_tool_sets(self, tmp_path: Path):
        """Verify session tools are registered when dependencies are provided."""
        from src.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        sm = SessionManager(tmp_path)
        b = MessageBus()
        mgr = _make_subagent_manager()
        mgr.executor = MagicMock()

        from src.agent.tool_sets import register_standard_tools
        from src.agent.tools.registration import ToolRegistrationConfig

        register_standard_tools(
            registry,
            ToolRegistrationConfig(
                workspace=tmp_path,
                executor=mgr.executor,
                session_manager=sm,
                bus=b,
                subagent_manager=mgr,
                bus_publish=b.publish_outbound,
            ),
        )

        # These are deferred tools (not in ALWAYS_ON_TOOLS); check both pools.
        assert registry.has("sessions_list")
        assert registry.has("sessions_history")
        assert registry.has("sessions_send")
        assert registry.has("subagent_wait")
        assert registry.has("subagent_kill")
        assert registry.has("subagents_list")

    def test_tools_not_registered_without_deps(self, tmp_path: Path):
        """Session tools should not be registered when deps are missing."""
        from src.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()

        from src.agent.tool_sets import register_standard_tools
        from src.agent.tools.registration import ToolRegistrationConfig

        register_standard_tools(registry, ToolRegistrationConfig(workspace=tmp_path))

        names = {t.name for t in registry._tools.values()}
        assert "sessions_list" not in names
        assert "sessions_history" not in names
        assert "sessions_send" not in names
        assert "subagents_list" not in names

    def test_tools_in_comms_group(self):
        """Session tools should be in group:comms."""
        from src.agent.tools.tool_profiles import TOOL_GROUPS

        comms = TOOL_GROUPS["group:comms"]
        assert "sessions_list" in comms
        assert "sessions_history" in comms
        assert "sessions_send" in comms
        assert "subagents_list" in comms


# ---------------------------------------------------------------------------
# SubagentsListTool — executor path
# ---------------------------------------------------------------------------


class TestSubagentsListToolExtended:
    @pytest.mark.asyncio
    async def test_with_executor_records(self):
        from src.agent.delegation.types import SubagentStatus, SubagentTaskRecord

        mgr = MagicMock()
        mgr._running_tasks = {}
        mgr._session_tasks = {}

        executor = MagicMock()
        records = [
            SubagentTaskRecord(
                task_id="t1",
                task="find files",
                label="explorer",
                role="explorer",
                parent_task_id=None,
                root_session_key="cli:direct",
                depth=1,
                origin_channel="cli",
                origin_chat_id="direct",
                status=SubagentStatus.COMPLETED,
            ),
            SubagentTaskRecord(
                task_id="t2",
                task="review code",
                label="reviewer",
                role="reviewer",
                parent_task_id="t1",
                root_session_key="cli:direct",
                depth=2,
                origin_channel="cli",
                origin_chat_id="direct",
                status=SubagentStatus.RUNNING,
            ),
        ]
        executor.list_tasks.return_value = records
        executor.get_running_count.return_value = 1
        mgr.executor = executor

        tool = SubagentsListTool(manager=mgr)
        result = json.loads(await tool.execute())

        assert result["count"] == 2
        assert result["running"] == 1

        by_id = {t["task_id"]: t for t in result["tasks"]}
        assert by_id["t1"]["status"] == "completed"
        assert by_id["t1"]["role"] == "explorer"
        assert by_id["t1"]["parent_task_id"] is None
        assert by_id["t1"]["depth"] == 1

        assert by_id["t2"]["status"] == "running"
        assert by_id["t2"]["parent_task_id"] == "t1"
        assert by_id["t2"]["depth"] == 2
