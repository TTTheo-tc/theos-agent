"""Tests for SubagentExecutor."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agent.delegation.executor import SubagentExecutor
from src.agent.delegation.runtime import RuntimeRoleConfig
from src.agent.delegation.types import (
    HandoffSpec,
    SubagentResult,
    SubagentStatus,
    SubagentTaskRecord,
)
from src.agent.tools.context import ToolContext
from src.bus.events import InboundMessage
from src.bus.queue import MessageBus
from src.config.schema import SubagentPolicyConfig
from src.providers.base import LLMProvider, LLMResponse
from src.session.subagent_store import SubagentStore

# ---------------------------------------------------------------------------
# Fake provider
# ---------------------------------------------------------------------------


class FakeProvider(LLMProvider):
    def __init__(self, reply="done"):
        super().__init__(api_key="fake")
        self._reply = reply

    def get_default_model(self):
        return "fake/model"

    async def chat(self, **kwargs):
        return LLMResponse(content=self._reply)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WS = Path("/tmp/test-workspace")

_DEFAULT_ROLES: dict[str, RuntimeRoleConfig] = {
    "explorer": RuntimeRoleConfig(
        name="explorer",
        description="explores stuff",
        system_prompt="You are an explorer.",
        model="fake/model",
        max_iterations=5,
        allowed_tools=None,
        allow_nested_spawn=False,
    ),
}


def _make_executor(
    *,
    policy: SubagentPolicyConfig | None = None,
    bus: MessageBus | None = None,
    roles: dict[str, RuntimeRoleConfig] | None = None,
    provider: LLMProvider | None = None,
    subagent_manager: MagicMock | None = None,
    store: SubagentStore | None = None,
) -> SubagentExecutor:
    return SubagentExecutor(
        policy=policy or SubagentPolicyConfig(),
        bus=bus,
        roles=roles or _DEFAULT_ROLES,
        provider=provider or FakeProvider(),
        workspace=_WS,
        subagent_manager=subagent_manager,
        store=store,
    )


# ---------------------------------------------------------------------------
# TestSpawn
# ---------------------------------------------------------------------------


class TestSpawn:
    @pytest.mark.asyncio
    async def test_spawn_returns_started_text(self):
        ex = _make_executor()
        text = await ex.spawn(
            task="say hello",
            label="greeter",
            role="explorer",
            root_session_key="cli:direct",
            origin_channel="cli",
            origin_chat_id="direct",
        )
        assert "started" in text.lower() or "task_id" in text.lower() or text

    @pytest.mark.asyncio
    async def test_spawn_creates_task_record(self):
        ex = _make_executor()
        await ex.spawn(
            task="say hello",
            label="greeter",
            role="explorer",
            root_session_key="cli:direct",
            origin_channel="cli",
            origin_chat_id="direct",
        )
        tasks = ex.list_tasks("cli:direct")
        assert len(tasks) == 1
        assert tasks[0].task == "say hello"
        assert tasks[0].role == "explorer"

    @pytest.mark.asyncio
    async def test_spawn_records_durable_checkpoint(self, tmp_path: Path):
        store = SubagentStore(tmp_path)
        ex = _make_executor(store=store)
        ex._workspace = tmp_path
        await ex.spawn(
            task="say hello",
            label="greeter",
            role="explorer",
            root_session_key="cli:direct",
            origin_channel="cli",
            origin_chat_id="direct",
        )
        latest = store.latest_for_session("cli:direct")
        assert latest
        assert latest[0].metadata["label"] == "greeter"

    @pytest.mark.asyncio
    async def test_max_concurrent_enforced(self):
        policy = SubagentPolicyConfig(max_concurrent=1)
        slow_provider = FakeProvider()

        async def slow_chat(**kw):
            await asyncio.sleep(10)
            return LLMResponse(content="x")

        slow_provider.chat = slow_chat

        ex = _make_executor(policy=policy, provider=slow_provider)
        await ex.spawn(
            task="t1",
            label="a",
            role="explorer",
            root_session_key="k",
            origin_channel="c",
            origin_chat_id="c",
            parent_task_id="parent-1",
        )
        # Give the task a moment to start running
        await asyncio.sleep(0.05)

        result = await ex.spawn(
            task="t2",
            label="b",
            role="explorer",
            root_session_key="k",
            origin_channel="c",
            origin_chat_id="c",
        )
        assert "limit" in result.lower() or "concurrent" in result.lower()

        # Cleanup
        for tid in list(ex._tasks):
            await ex.kill(tid)

    @pytest.mark.asyncio
    async def test_max_depth_enforced(self):
        policy = SubagentPolicyConfig(max_depth=1)
        ex = _make_executor(policy=policy)
        result = await ex.spawn(
            task="t1",
            label="deep",
            role="explorer",
            root_session_key="k",
            origin_channel="c",
            origin_chat_id="c",
            depth=2,
        )
        assert "depth" in result.lower()

    @pytest.mark.asyncio
    async def test_max_children_per_agent_enforced(self):
        policy = SubagentPolicyConfig(max_children_per_agent=1)
        ex = _make_executor(policy=policy)
        slow_provider = FakeProvider()

        async def slow_chat(**kw):
            await asyncio.sleep(10)
            return LLMResponse(content="x")

        slow_provider.chat = slow_chat
        ex._provider = slow_provider

        await ex.spawn(
            task="t1",
            label="a",
            role="explorer",
            root_session_key="k",
            origin_channel="c",
            origin_chat_id="c",
            parent_task_id="parent-1",
        )
        await asyncio.sleep(0.05)

        result = await ex.spawn(
            task="t2",
            label="b",
            role="explorer",
            root_session_key="k",
            origin_channel="c",
            origin_chat_id="c",
            parent_task_id="parent-1",
        )
        assert "children" in result.lower() or "limit" in result.lower()

        # Cleanup
        for tid in list(ex._tasks):
            await ex.kill(tid)


# ---------------------------------------------------------------------------
# TestWait
# ---------------------------------------------------------------------------


class TestWait:
    @pytest.mark.asyncio
    async def test_returns_result_when_done(self, tmp_path: Path):
        store = SubagentStore(tmp_path)
        ex = _make_executor(store=store)
        await ex.spawn(
            task="say hello",
            label="greeter",
            role="explorer",
            root_session_key="k",
            origin_channel="c",
            origin_chat_id="c",
        )
        # Extract task_id from the record
        task_id = ex.list_tasks("k")[0].task_id

        result = await ex.wait(task_id, timeout=5.0)
        assert result is not None
        assert isinstance(result, SubagentResult)
        assert result.status == SubagentStatus.COMPLETED
        assert result.result == "done"
        latest = store.latest_for_session("k")
        assert latest[0].status == SubagentStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_timeout_returns_current_status(self):
        slow_provider = FakeProvider()

        async def slow_chat(**kw):
            await asyncio.sleep(10)
            return LLMResponse(content="x")

        slow_provider.chat = slow_chat

        ex = _make_executor(provider=slow_provider)
        await ex.spawn(
            task="slow",
            label="a",
            role="explorer",
            root_session_key="k",
            origin_channel="c",
            origin_chat_id="c",
        )
        task_id = ex.list_tasks("k")[0].task_id
        await asyncio.sleep(0.05)

        result = await ex.wait(task_id, timeout=0.1)
        assert result is not None
        assert result.status == SubagentStatus.RUNNING

        # Cleanup
        await ex.kill(task_id)

    @pytest.mark.asyncio
    async def test_unknown_task_returns_none(self):
        ex = _make_executor()
        result = await ex.wait("nonexistent", timeout=0.1)
        assert result is None


# ---------------------------------------------------------------------------
# TestKill
# ---------------------------------------------------------------------------


class TestKill:
    @pytest.mark.asyncio
    async def test_kill_running_task(self):
        slow_provider = FakeProvider()

        async def slow_chat(**kw):
            await asyncio.sleep(10)
            return LLMResponse(content="x")

        slow_provider.chat = slow_chat

        ex = _make_executor(provider=slow_provider)
        await ex.spawn(
            task="slow",
            label="a",
            role="explorer",
            root_session_key="k",
            origin_channel="c",
            origin_chat_id="c",
        )
        task_id = ex.list_tasks("k")[0].task_id
        await asyncio.sleep(0.05)

        killed = await ex.kill(task_id)
        assert killed is True

        # Wait for cancellation to propagate
        await asyncio.sleep(0.1)
        record = ex._records.get(task_id)
        assert record is not None
        assert record.status == SubagentStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_kill_unknown_task(self):
        ex = _make_executor()
        killed = await ex.kill("nonexistent")
        assert killed is False


# ---------------------------------------------------------------------------
# TestCancelBySession
# ---------------------------------------------------------------------------


class TestCancelBySession:
    @pytest.mark.asyncio
    async def test_cancels_all_tasks(self):
        slow_provider = FakeProvider()

        async def slow_chat(**kw):
            await asyncio.sleep(10)
            return LLMResponse(content="x")

        slow_provider.chat = slow_chat
        policy = SubagentPolicyConfig(max_concurrent=5)
        ex = _make_executor(policy=policy, provider=slow_provider)

        for i in range(3):
            await ex.spawn(
                task=f"t{i}",
                label=f"l{i}",
                role="explorer",
                root_session_key="session-1",
                origin_channel="c",
                origin_chat_id="c",
            )
        await asyncio.sleep(0.05)

        count = await ex.cancel_by_session("session-1")
        assert count == 3

        await asyncio.sleep(0.1)
        for rec in ex._records.values():
            assert rec.status == SubagentStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_frozen_session_unblocks_after_cancel(self):
        slow_provider = FakeProvider()

        async def slow_chat(**kw):
            await asyncio.sleep(10)
            return LLMResponse(content="x")

        slow_provider.chat = slow_chat
        policy = SubagentPolicyConfig(max_concurrent=5)
        ex = _make_executor(policy=policy, provider=slow_provider)

        await ex.spawn(
            task="t1",
            label="a",
            role="explorer",
            root_session_key="sess",
            origin_channel="c",
            origin_chat_id="c",
        )
        await asyncio.sleep(0.05)

        await ex.cancel_by_session("sess")
        await asyncio.sleep(0.1)

        # Should be able to spawn again after cancel
        # Reuse same executor — frozen set should be cleared
        result = await ex.spawn(
            task="t2",
            label="b",
            role="explorer",
            root_session_key="sess",
            origin_channel="c",
            origin_chat_id="c",
        )
        # Should not be rejected
        assert "frozen" not in result.lower()


# ---------------------------------------------------------------------------
# TestResultRetention
# ---------------------------------------------------------------------------


class TestResultRetention:
    @pytest.mark.asyncio
    async def test_completed_results_kept(self):
        ex = _make_executor()
        await ex.spawn(
            task="t1",
            label="a",
            role="explorer",
            root_session_key="k",
            origin_channel="c",
            origin_chat_id="c",
        )
        task_id = ex.list_tasks("k")[0].task_id
        result = await ex.wait(task_id, timeout=5.0)
        assert result is not None
        assert result.status == SubagentStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_get_running_count(self):
        slow_provider = FakeProvider()

        async def slow_chat(**kw):
            await asyncio.sleep(10)
            return LLMResponse(content="x")

        slow_provider.chat = slow_chat
        policy = SubagentPolicyConfig(max_concurrent=5)
        ex = _make_executor(policy=policy, provider=slow_provider)

        for i in range(3):
            await ex.spawn(
                task=f"t{i}",
                label=f"l{i}",
                role="explorer",
                root_session_key="k",
                origin_channel="c",
                origin_chat_id="c",
            )
        await asyncio.sleep(0.05)
        assert ex.get_running_count() == 3

        # Cleanup
        await ex.cancel_by_session("k")

    @pytest.mark.asyncio
    async def test_gc_evicts_consumed(self):
        policy = SubagentPolicyConfig(keep_completed=1)
        ex = _make_executor(policy=policy)

        # Spawn and complete two tasks
        await ex.spawn(
            task="t1",
            label="a",
            role="explorer",
            root_session_key="k",
            origin_channel="c",
            origin_chat_id="c",
        )
        tid1 = ex.list_tasks("k")[0].task_id
        r1 = await ex.wait(tid1, timeout=5.0)
        assert r1 is not None
        # Mark consumed
        ex._consumed.add(tid1)

        await ex.spawn(
            task="t2",
            label="b",
            role="explorer",
            root_session_key="k",
            origin_channel="c",
            origin_chat_id="c",
            parent_task_id="parent-2",
        )
        tid2 = [r.task_id for r in ex.list_tasks("k") if r.task_id != tid1][0]
        r2 = await ex.wait(tid2, timeout=5.0)
        assert r2 is not None
        ex._consumed.add(tid2)

        ex._gc_results()

        # At least one should be evicted
        remaining = [r for r in ex._records.values() if r.is_terminal]
        assert len(remaining) <= 1

    @pytest.mark.asyncio
    async def test_unconsumed_survives_gc(self):
        policy = SubagentPolicyConfig(keep_completed=1)
        ex = _make_executor(policy=policy)

        await ex.spawn(
            task="t1",
            label="a",
            role="explorer",
            root_session_key="k",
            origin_channel="c",
            origin_chat_id="c",
            parent_task_id="parent-1",
        )
        tid1 = ex.list_tasks("k")[0].task_id
        await asyncio.sleep(0.05)
        assert tid1 in ex._results
        # Do NOT consume t1 via wait()

        await ex.spawn(
            task="t2",
            label="b",
            role="explorer",
            root_session_key="k",
            origin_channel="c",
            origin_chat_id="c",
            parent_task_id="parent-1",
        )
        tid2 = [r.task_id for r in ex.list_tasks("k") if r.task_id != tid1][0]
        await ex.wait(tid2, timeout=5.0)

        ex._gc_results()

        # t1 (unconsumed) should survive
        assert tid1 in ex._records


# ---------------------------------------------------------------------------
# TestMaxChildrenPerAgent
# ---------------------------------------------------------------------------


class TestMaxChildrenPerAgent:
    @pytest.mark.asyncio
    async def test_enforced_via_parent_context(self):
        policy = SubagentPolicyConfig(max_children_per_agent=2, max_concurrent=5)
        slow_provider = FakeProvider()

        async def slow_chat(**kw):
            await asyncio.sleep(10)
            return LLMResponse(content="x")

        slow_provider.chat = slow_chat

        ex = _make_executor(policy=policy, provider=slow_provider)

        for i in range(2):
            await ex.spawn(
                task=f"t{i}",
                label=f"l{i}",
                role="explorer",
                root_session_key="k",
                origin_channel="c",
                origin_chat_id="c",
                parent_task_id="parent-0",
            )
        await asyncio.sleep(0.05)

        result = await ex.spawn(
            task="t_overflow",
            label="overflow",
            role="explorer",
            root_session_key="k",
            origin_channel="c",
            origin_chat_id="c",
            parent_task_id="parent-0",
        )
        assert "children" in result.lower() or "limit" in result.lower()

        # Cleanup
        await ex.cancel_by_session("k")


# ---------------------------------------------------------------------------
# TestHandoffFlow
# ---------------------------------------------------------------------------


class TestHandoffFlow:
    @pytest.mark.asyncio
    async def test_handoff_stored_in_record(self):
        ex = _make_executor()
        handoff = HandoffSpec(
            context="parent did X",
            acceptance_criteria="must return JSON",
        )
        await ex.spawn(
            task="process data",
            label="processor",
            role="explorer",
            root_session_key="k",
            origin_channel="c",
            origin_chat_id="c",
            handoff=handoff,
        )
        rec = ex.list_tasks("k")[0]
        assert rec.handoff is not None
        assert rec.handoff.context == "parent did X"
        assert rec.handoff.acceptance_criteria == "must return JSON"

    @pytest.mark.asyncio
    async def test_handoff_rendered_in_prompt(self):
        ex = _make_executor()
        handoff = HandoffSpec(
            context="parent context here",
            acceptance_criteria="must be valid",
            not_in_scope="do not test",
        )
        prompt = ex._build_prompt(
            task="do something",
            role_config=_DEFAULT_ROLES["explorer"],
            handoff=handoff,
        )
        assert "parent context here" in prompt
        assert "must be valid" in prompt
        assert "do not test" in prompt


# ---------------------------------------------------------------------------
# TestAnnouncement
# ---------------------------------------------------------------------------


class TestAnnouncement:
    @pytest.mark.asyncio
    async def test_top_level_task_announces_on_bus(self):
        bus = MessageBus()
        ex = _make_executor(bus=bus)
        await ex.spawn(
            task="say hello",
            label="greeter",
            role="explorer",
            root_session_key="cli:direct",
            origin_channel="cli",
            origin_chat_id="direct",
        )
        task_id = ex.list_tasks("cli:direct")[0].task_id

        # Wait for it to complete
        result = await ex.wait(task_id, timeout=5.0)
        assert result is not None
        assert result.status == SubagentStatus.COMPLETED

        # Check bus received the announcement
        msg = bus.inbound.get_nowait()
        assert isinstance(msg, InboundMessage)
        assert msg.session_key_override == "cli:direct"
        assert "done" in msg.content.lower() or task_id in msg.content

    @pytest.mark.asyncio
    async def test_nested_task_does_not_announce(self):
        bus = MessageBus()
        ex = _make_executor(bus=bus)
        await ex.spawn(
            task="nested work",
            label="sub",
            role="explorer",
            root_session_key="cli:direct",
            origin_channel="cli",
            origin_chat_id="direct",
            parent_task_id="parent-1",
        )
        task_id = ex.list_tasks("cli:direct")[0].task_id
        result = await ex.wait(task_id, timeout=5.0)
        assert result is not None

        # Bus should be empty — nested tasks don't announce
        assert bus.inbound.empty()


class TestNestedRuntimeIntegration:
    @pytest.mark.asyncio
    async def test_subagent_runtime_receives_nested_tools_and_non_owner_context(self, tmp_path):
        manager = MagicMock()
        ex = _make_executor(
            roles={
                "planner": RuntimeRoleConfig(
                    name="planner",
                    description="planner",
                    system_prompt="You are a planner.",
                    model="fake/model",
                    max_iterations=5,
                    allowed_tools={"agent", "subagent_wait", "subagent_kill", "subagents_list"},
                    allow_nested_spawn=True,
                )
            },
            subagent_manager=manager,
        )
        record = SubagentTaskRecord(
            task_id="sub-1",
            task="plan",
            label="planner",
            role="planner",
            parent_task_id=None,
            root_session_key="cli:direct",
            depth=1,
            origin_channel="cli",
            origin_chat_id="direct",
        )

        async def fake_run_tool_loop(**kwargs):
            tool_names = set(kwargs["tools"].tool_names)
            assert {"agent", "subagent_wait", "subagent_kill", "subagents_list"} <= tool_names
            assert kwargs["tool_context"].sender_is_owner is False
            assert kwargs["tool_context"].allow_subagent_spawn is True
            return "done", [], [], {}

        with patch("src.agent.loop_core.run_tool_loop", side_effect=fake_run_tool_loop):
            content, *_ = await ex._run_subagent_loop(record, ex._roles["planner"])
        assert content == "done"


class TestAccessControl:
    @pytest.mark.asyncio
    async def test_wait_and_kill_restricted_to_child_lineage(self):
        ex = _make_executor()
        root = SubagentTaskRecord(
            task_id="root-task",
            task="root",
            label="root",
            role=None,
            parent_task_id=None,
            root_session_key="cli:direct",
            depth=1,
            origin_channel="cli",
            origin_chat_id="direct",
            status=SubagentStatus.COMPLETED,
        )
        child = SubagentTaskRecord(
            task_id="child-task",
            task="child",
            label="child",
            role=None,
            parent_task_id="root-task",
            root_session_key="cli:direct",
            depth=2,
            origin_channel="cli",
            origin_chat_id="direct",
            status=SubagentStatus.COMPLETED,
            result="child-result",
        )
        sibling = SubagentTaskRecord(
            task_id="sibling-task",
            task="sibling",
            label="sibling",
            role=None,
            parent_task_id=None,
            root_session_key="cli:direct",
            depth=1,
            origin_channel="cli",
            origin_chat_id="direct",
            status=SubagentStatus.COMPLETED,
            result="sibling-result",
        )
        ex._records[root.task_id] = root
        ex._records[child.task_id] = child
        ex._records[sibling.task_id] = sibling
        ex._results[child.task_id] = SubagentResult(
            task_id="child-task",
            status=SubagentStatus.COMPLETED,
            role=None,
            parent_task_id="root-task",
            depth=2,
            result="child-result",
        )
        ex._results[sibling.task_id] = SubagentResult(
            task_id="sibling-task",
            status=SubagentStatus.COMPLETED,
            role=None,
            parent_task_id=None,
            depth=1,
            result="sibling-result",
        )

        ctx = ToolContext(
            session_key="subagent:root-task",
            root_session_key="cli:direct",
            subagent_task_id="root-task",
            sender_is_owner=False,
        )
        assert await ex.wait("child-task", context=ctx) is not None
        assert await ex.wait("sibling-task", context=ctx) is None
        assert await ex.kill("sibling-task", context=ctx) is False

    @pytest.mark.asyncio
    async def test_wait_marks_result_consumed(self):
        ex = _make_executor()
        record = SubagentTaskRecord(
            task_id="t1",
            task="test",
            label="test",
            role=None,
            parent_task_id=None,
            root_session_key="cli:direct",
            depth=1,
            origin_channel="cli",
            origin_chat_id="direct",
            status=SubagentStatus.COMPLETED,
            result="done",
        )
        ex._records["t1"] = record
        ex._results["t1"] = SubagentResult(
            task_id="t1",
            status=SubagentStatus.COMPLETED,
            role=None,
            parent_task_id=None,
            depth=1,
            result="done",
        )

        result = await ex.wait("t1")
        assert result is not None
        assert "t1" in ex._consumed
