"""Tests for /stop task cancellation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.delegation.executor import SubagentExecutor
from src.agent.delegation.types import SubagentStatus, SubagentTaskRecord
from src.config.schema import SubagentPolicyConfig
from src.providers.base import LLMProvider, LLMResponse


def _make_loop(tmp_path=None):
    """Create a minimal AgentLoop with mocked dependencies."""
    from pathlib import Path
    from tempfile import mkdtemp

    from src.agent.loop import AgentLoop
    from src.bus.queue import MessageBus
    from src.config.schema import Config

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    ws = tmp_path or Path(mkdtemp())
    cfg = Config()
    cfg.agents.defaults.workspace = str(ws)

    with patch.object(AgentLoop, "_register_default_tools", return_value=None):
        loop = AgentLoop(bus=bus, provider=provider, config=cfg)
    loop.subagents.cancel_by_session = AsyncMock(return_value=0)
    return loop, bus


async def _cleanup_loop(loop) -> None:
    await loop.close_mcp()
    await loop._memory.close_dbs()


class TestHandleStop:
    @pytest.mark.asyncio
    async def test_stop_no_active_task(self):
        from src.bus.events import InboundMessage

        loop, bus = _make_loop()
        try:
            msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
            await loop._handle_stop(msg)
            out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert "No active task" in out.content
        finally:
            await _cleanup_loop(loop)

    @pytest.mark.asyncio
    async def test_stop_cancels_dispatcher_worker(self):
        from src.bus.events import InboundMessage

        loop, bus = _make_loop()
        try:
            loop._dispatcher.cancel_group = MagicMock(return_value=True)
            loop.subagents.cancel_by_session = AsyncMock(return_value=0)

            msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
            await loop._handle_stop(msg)

            loop._dispatcher.cancel_group.assert_called_once_with("test:c1")
            loop.subagents.cancel_by_session.assert_awaited_once_with("test:c1")
            out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert "Stopped 1 task" in out.content
        finally:
            await _cleanup_loop(loop)

    @pytest.mark.asyncio
    async def test_stop_counts_dispatcher_and_subagents(self):
        from src.bus.events import InboundMessage

        loop, bus = _make_loop()
        try:
            loop._dispatcher.cancel_group = MagicMock(return_value=True)
            loop.subagents.cancel_by_session = AsyncMock(return_value=2)

            msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="/stop")
            await loop._handle_stop(msg)

            out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert "Stopped 3 task" in out.content
        finally:
            await _cleanup_loop(loop)


class TestDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_processes_and_publishes(self):
        from src.bus.events import InboundMessage, OutboundMessage

        loop, bus = _make_loop()
        try:
            msg = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="hello")
            loop._process_message = AsyncMock(
                return_value=OutboundMessage(channel="test", chat_id="c1", content="hi")
            )

            await loop._lifecycle.handle_message(msg)
            out = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert out.content == "hi"
        finally:
            await _cleanup_loop(loop)

    @pytest.mark.asyncio
    async def test_per_group_dispatcher_serializes_messages(self):
        from src.bus.events import InboundMessage, OutboundMessage

        loop, bus = _make_loop()
        try:
            order = []

            async def mock_process(m, **kwargs):
                order.append(f"start-{m.content}")
                await asyncio.sleep(0.05)
                order.append(f"end-{m.content}")
                return OutboundMessage(channel="test", chat_id="c1", content=m.content)

            loop._process_message = mock_process
            msg1 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="a")
            msg2 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="b")

            await loop._dispatcher.dispatch(msg1)
            await loop._dispatcher.dispatch(msg2)
            await asyncio.wait_for(loop._dispatcher._queues[msg1.session_key].join(), timeout=1.0)

            first = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            second = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            assert [first.content, second.content] == ["a", "b"]
            assert order == ["start-a", "end-a", "start-b", "end-b"]
            loop._dispatcher.cancel_all()
        finally:
            await _cleanup_loop(loop)

    @pytest.mark.asyncio
    async def test_cancel_group_drains_pending_messages(self):
        from src.bus.events import InboundMessage
        from src.session.group_dispatcher import PerGroupDispatcher

        started = asyncio.Event()
        release = asyncio.Event()
        processed = []

        async def process(msg):
            started.set()
            await release.wait()
            processed.append(msg.content)

        dispatcher = PerGroupDispatcher(process)
        msg1 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="a")
        msg2 = InboundMessage(channel="test", sender_id="u1", chat_id="c1", content="b")

        await dispatcher.dispatch(msg1)
        await dispatcher.dispatch(msg2)
        await asyncio.wait_for(started.wait(), timeout=1.0)

        cancelled = dispatcher.cancel_group(msg1.session_key)
        release.set()
        await asyncio.gather(
            dispatcher._workers[msg1.session_key],
            return_exceptions=True,
        )

        assert cancelled is True
        assert dispatcher._queues[msg1.session_key].empty()
        assert processed == []


class TestSubagentCancellation:
    @pytest.mark.asyncio
    async def test_cancel_by_session(self, tmp_path):
        from src.agent.delegation.types import SubagentStatus, SubagentTaskRecord
        from src.agent.subagent import SubagentManager
        from src.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)

        cancelled = asyncio.Event()

        async def slow():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(slow())
        await asyncio.sleep(0)

        # Inject into the executor (cancel_by_session delegates there now)
        record = SubagentTaskRecord(
            task_id="sub-1",
            task="test",
            label="test",
            role=None,
            parent_task_id=None,
            root_session_key="test:c1",
            depth=0,
            origin_channel="test",
            origin_chat_id="c1",
            status=SubagentStatus.RUNNING,
        )
        mgr.executor._records["sub-1"] = record
        mgr.executor._tasks["sub-1"] = task

        count = await mgr.cancel_by_session("test:c1")
        assert count == 1
        assert cancelled.is_set()

    @pytest.mark.asyncio
    async def test_cancel_by_session_no_tasks(self, tmp_path):
        from src.agent.subagent import SubagentManager
        from src.bus.queue import MessageBus

        bus = MessageBus()
        provider = MagicMock()
        provider.get_default_model.return_value = "test-model"
        mgr = SubagentManager(provider=provider, workspace=tmp_path, bus=bus)
        assert await mgr.cancel_by_session("nonexistent") == 0


# ---------------------------------------------------------------------------
# Executor-level cascade cancel integration tests
# ---------------------------------------------------------------------------


class _SlowProvider(LLMProvider):
    """Provider that sleeps for a configurable delay before responding."""

    def __init__(self, delay: float = 10):
        super().__init__(api_key="fake")
        self._delay = delay

    def get_default_model(self):
        return "fake/model"

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7):
        await asyncio.sleep(self._delay)
        return LLMResponse(content="done")


class TestExecutorCascadeCancel:
    @pytest.fixture
    def bus(self):
        from src.bus.queue import MessageBus

        return MessageBus()

    @pytest.mark.asyncio
    async def test_cancel_sets_all_to_cancelled(self, bus, tmp_path):
        executor = SubagentExecutor(
            provider=_SlowProvider(),
            workspace=tmp_path,
            bus=bus,
            policy=SubagentPolicyConfig(max_concurrent=5),
            roles={},
        )
        for i in range(1, 4):
            await executor.spawn(
                task=f"t{i}",
                role=None,
                label=f"l{i}",
                root_session_key="cli:direct",
                origin_channel="cli",
                origin_chat_id="direct",
            )

        # Yield so spawned asyncio tasks enter _execute (status -> RUNNING)
        await asyncio.sleep(0.05)

        count = await executor.cancel_by_session("cli:direct")
        assert count == 3

        await asyncio.sleep(0.1)
        for rec in executor.list_tasks("cli:direct"):
            assert rec.status == SubagentStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_then_spawn_works(self, bus, tmp_path):
        executor = SubagentExecutor(
            provider=_SlowProvider(),
            workspace=tmp_path,
            bus=bus,
            policy=SubagentPolicyConfig(),
            roles={},
        )
        await executor.spawn(
            task="t1",
            role=None,
            label="l1",
            root_session_key="cli:direct",
            origin_channel="cli",
            origin_chat_id="direct",
        )
        await executor.cancel_by_session("cli:direct")
        await asyncio.sleep(0.1)

        # Switch to fast provider
        executor._provider = _SlowProvider(delay=0)
        result = await executor.spawn(
            task="t2",
            role=None,
            label="l2",
            root_session_key="cli:direct",
            origin_channel="cli",
            origin_chat_id="direct",
        )
        assert "started" in result.lower()

    @pytest.mark.asyncio
    async def test_parent_terminal_cancels_children(self, bus, tmp_path):
        """When parent finishes, its running children get cascade-cancelled."""
        executor = SubagentExecutor(
            provider=_SlowProvider(delay=0),
            workspace=tmp_path,
            bus=bus,
            policy=SubagentPolicyConfig(),
            roles={},
        )
        await executor.spawn(
            task="parent",
            role=None,
            label="p",
            root_session_key="cli:direct",
            origin_channel="cli",
            origin_chat_id="direct",
        )
        # Let the parent task start
        await asyncio.sleep(0.05)
        tasks = executor.list_tasks("cli:direct")
        assert len(tasks) >= 1
        parent_id = tasks[0].task_id

        # Inject a fake running child record tied to the parent
        child_rec = SubagentTaskRecord(
            task_id="fake-child",
            task="child task",
            label="child",
            role=None,
            parent_task_id=parent_id,
            root_session_key="cli:direct",
            depth=2,
            origin_channel="cli",
            origin_chat_id="direct",
            status=SubagentStatus.RUNNING,
        )
        executor._records["fake-child"] = child_rec

        # Give parent time to complete and trigger cascade-cancel in finally
        await asyncio.sleep(0.5)

        parent = executor._records.get(parent_id)
        assert parent is not None and parent.is_terminal
        assert parent.status in (
            SubagentStatus.COMPLETED,
            SubagentStatus.FAILED,
        )

    @pytest.mark.asyncio
    async def test_kill_without_cascade_leaves_children_running(self, bus, tmp_path):
        executor = SubagentExecutor(
            provider=_SlowProvider(),
            workspace=tmp_path,
            bus=bus,
            policy=SubagentPolicyConfig(),
            roles={},
        )
        await executor.spawn(
            task="parent",
            role=None,
            label="p",
            root_session_key="cli:direct",
            origin_channel="cli",
            origin_chat_id="direct",
        )
        await asyncio.sleep(0.05)
        parent_id = executor.list_tasks("cli:direct")[0].task_id

        child_task = asyncio.create_task(asyncio.sleep(10))
        executor._records["fake-child"] = SubagentTaskRecord(
            task_id="fake-child",
            task="child task",
            label="child",
            role=None,
            parent_task_id=parent_id,
            root_session_key="cli:direct",
            depth=2,
            origin_channel="cli",
            origin_chat_id="direct",
            status=SubagentStatus.RUNNING,
        )
        executor._tasks["fake-child"] = child_task

        try:
            assert await executor.kill(parent_id, cascade=False) is True
            await asyncio.sleep(0.05)
            assert child_task.cancelled() is False
            assert executor._records["fake-child"].status == SubagentStatus.RUNNING
        finally:
            child_task.cancel()
            await asyncio.gather(child_task, return_exceptions=True)
