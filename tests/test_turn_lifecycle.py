"""TurnLifecycle + OrchestratorPolicy tests."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.bus.events import InboundMessage, OutboundMessage
from src.bus.queue import MessageBus
from src.orchestrator.policies import ExecutionPolicy, OrchestratorPolicy
from src.orchestrator.state_machine import TaskState
from src.orchestrator.turn_lifecycle import TurnLifecycle
from src.orchestrator.turn_record import TurnRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_msg(
    content: str = "hi",
    channel: str = "test",
    chat_id: str = "1",
    sender_id: str = "u1",
) -> InboundMessage:
    return InboundMessage(channel=channel, sender_id=sender_id, chat_id=chat_id, content=content)


def _make_response(
    content: str = "reply",
    channel: str = "test",
    chat_id: str = "1",
) -> OutboundMessage:
    return OutboundMessage(channel=channel, chat_id=chat_id, content=content, metadata={})


def _make_agent(bus: MessageBus | None = None, workspace: Path | None = None) -> MagicMock:
    """Build a minimal mock AgentLoop suitable for TurnLifecycle tests."""
    agent = MagicMock()
    agent.bus = bus or MessageBus()
    agent.hooks = MagicMock()
    agent.hooks.run_post_chat = AsyncMock()
    agent.workspace = workspace or Path("/tmp/test")
    agent._process_message = AsyncMock(return_value=_make_response())
    return agent


# ---------------------------------------------------------------------------
# 1. TurnRecord defaults
# ---------------------------------------------------------------------------


def test_turn_record_defaults():
    """TurnRecord has correct defaults: status, turn_id format, started_at type."""
    tr = TurnRecord(session_key="test:1")
    assert tr.status == "created"
    assert len(tr.turn_id) == 12
    assert all(c in "0123456789abcdef" for c in tr.turn_id)
    assert isinstance(tr.started_at, datetime)


# ---------------------------------------------------------------------------
# 2. Lifecycle with no policies — happy path
# ---------------------------------------------------------------------------


async def test_lifecycle_no_policies():
    """With no policies, handle_message publishes response with turn_id."""
    bus = MessageBus()
    agent = _make_agent(bus=bus)
    response = _make_response()
    agent._process_message = AsyncMock(return_value=response)

    lifecycle = TurnLifecycle(agent, policies=[])
    msg = _make_msg()
    await lifecycle.handle_message(msg)

    out = await asyncio.wait_for(bus.consume_outbound(), timeout=2.0)
    assert out.content == "reply"
    assert "turn_id" in out.metadata


# ---------------------------------------------------------------------------
# 3. Lifecycle failure with no policies — post-chat hook fires
# ---------------------------------------------------------------------------


async def test_lifecycle_failure_no_policies():
    """On failure with no policies: post-chat hook fires and error fallback published."""
    bus = MessageBus()
    agent = _make_agent(bus=bus)
    agent._process_message = AsyncMock(side_effect=RuntimeError("boom"))

    lifecycle = TurnLifecycle(agent, policies=[])
    msg = _make_msg()
    await lifecycle.handle_message(msg)

    # (a) post-chat hook fired (via create_task)
    # Give the fire-and-forget task a chance to schedule
    await asyncio.sleep(0)
    assert agent.hooks.run_post_chat.called

    # (b) error fallback published
    out = await asyncio.wait_for(bus.consume_outbound(), timeout=2.0)
    assert "error" in out.content.lower()

    # (c) turn_id in metadata
    assert "turn_id" in out.metadata


# ---------------------------------------------------------------------------
# 3b. Lifecycle failure WITH policies skips built-in hook
# ---------------------------------------------------------------------------


async def test_lifecycle_failure_with_policies_skips_builtin_hook():
    """When policies are installed, _run_failed_post_chat does NOT fire."""
    bus = MessageBus()
    agent = _make_agent(bus=bus)
    agent._process_message = AsyncMock(side_effect=RuntimeError("boom"))

    mock_policy = MagicMock(spec=ExecutionPolicy)
    mock_policy.before_execute = AsyncMock()
    mock_policy.after_failure = AsyncMock()
    mock_policy.should_retry = MagicMock(return_value=False)
    mock_policy.close = AsyncMock()

    lifecycle = TurnLifecycle(agent, policies=[mock_policy])
    msg = _make_msg()
    await lifecycle.handle_message(msg)

    # post-chat hook NOT fired (policies own failure handling)
    agent.hooks.run_post_chat.assert_not_called()

    # policy's after_failure WAS called
    mock_policy.after_failure.assert_awaited_once()


# ---------------------------------------------------------------------------
# 4. Lifecycle retry — policy triggers one retry then success
# ---------------------------------------------------------------------------


async def test_lifecycle_retry():
    """Policy returns should_retry=True once, on_retry called, second attempt succeeds."""
    bus = MessageBus()
    agent = _make_agent(bus=bus)
    response = _make_response(content="ok")

    call_count = 0

    async def _process_side_effect(msg, turn_id=None):
        nonlocal call_count
        call_count += 1
        assert turn_id is not None
        if call_count == 1:
            raise RuntimeError("transient")
        return response

    agent._process_message = AsyncMock(side_effect=_process_side_effect)

    retry_calls = []
    should_retry_results = [True, False]
    should_retry_idx = 0

    mock_policy = MagicMock(spec=ExecutionPolicy)
    mock_policy.before_execute = AsyncMock()
    mock_policy.after_success = AsyncMock()
    mock_policy.after_failure = AsyncMock()
    mock_policy.close = AsyncMock()

    def _should_retry(turn, error):
        nonlocal should_retry_idx
        result = should_retry_results[should_retry_idx]
        should_retry_idx += 1
        return result

    mock_policy.should_retry = MagicMock(side_effect=_should_retry)

    async def _on_retry(turn, msg, error, attempt):
        retry_calls.append(attempt)

    mock_policy.on_retry = AsyncMock(side_effect=_on_retry)

    lifecycle = TurnLifecycle(agent, policies=[mock_policy])
    msg = _make_msg()
    await lifecycle.handle_message(msg)

    # on_retry was called with attempt=1
    assert retry_calls == [1]
    # _process_message called twice (initial + retry)
    assert call_count == 2
    # after_success was called for the second attempt
    mock_policy.after_success.assert_awaited_once()


# ---------------------------------------------------------------------------
# 5. OrchestratorPolicy before_execute creates TaskRecord
# ---------------------------------------------------------------------------


async def test_orchestrator_policy_before_execute():
    """before_execute creates TaskRecord linked by turn_id, stored in _tasks and _active."""
    agent = _make_agent()
    policy = OrchestratorPolicy(
        max_retries=3,
        review_mode="auto",
        event_log_enabled=False,
        event_store_config=None,
        agent=agent,
    )
    turn = TurnRecord(session_key="test:1")
    msg = _make_msg()
    await policy.before_execute(turn, msg)

    # TaskRecord created and stored
    assert len(policy._tasks) == 1
    assert turn.turn_id in policy._active

    task = policy._active[turn.turn_id]
    assert task.turn_id == turn.turn_id
    assert task.state == TaskState.EXECUTING


# ---------------------------------------------------------------------------
# 6. OrchestratorPolicy on_retry — state transitions
# ---------------------------------------------------------------------------


async def test_orchestrator_policy_on_retry():
    """on_retry transitions EXEC_FAILED -> EXECUTING, increments retry_count.
    should_retry does NOT do state transitions."""
    agent = _make_agent()
    policy = OrchestratorPolicy(
        max_retries=3,
        review_mode="auto",
        event_log_enabled=False,
        event_store_config=None,
        agent=agent,
    )
    turn = TurnRecord(session_key="test:1")
    msg = _make_msg()
    await policy.before_execute(turn, msg)

    task = policy._active[turn.turn_id]
    assert task.state == TaskState.EXECUTING

    error = RuntimeError("test error")

    # should_retry is decision-only, no state transitions
    state_before = task.state
    result = policy.should_retry(turn, error)
    assert result is True
    assert task.state == state_before  # unchanged

    # on_retry does the state transitions
    await policy.on_retry(turn, msg, error, attempt=1)
    assert task.retry_count == 1
    assert task.state == TaskState.EXECUTING  # re-entered EXECUTING after EXEC_FAILED


# ---------------------------------------------------------------------------
# 7. OrchestratorPolicy concurrent sessions
# ---------------------------------------------------------------------------


async def test_orchestrator_policy_concurrent_sessions():
    """Two TurnRecords with different turn_ids get separate _active entries."""
    agent = _make_agent()
    policy = OrchestratorPolicy(
        max_retries=3,
        review_mode="auto",
        event_log_enabled=False,
        event_store_config=None,
        agent=agent,
    )
    turn_a = TurnRecord(session_key="test:1")
    turn_b = TurnRecord(session_key="test:2")
    msg_a = _make_msg(chat_id="1")
    msg_b = _make_msg(chat_id="2")

    await policy.before_execute(turn_a, msg_a)
    await policy.before_execute(turn_b, msg_b)

    assert turn_a.turn_id != turn_b.turn_id
    assert turn_a.turn_id in policy._active
    assert turn_b.turn_id in policy._active
    assert len(policy._active) == 2
    assert len(policy._tasks) == 2


# ---------------------------------------------------------------------------
# 8. OrchestratorPolicy after_success -> APPROVED
# ---------------------------------------------------------------------------


async def test_orchestrator_policy_success():
    """after_success transitions TaskRecord to APPROVED and cleans up _active."""
    agent = _make_agent()
    agent.pop_genver_handoff = MagicMock(return_value=None)
    agent._is_genver = False

    policy = OrchestratorPolicy(
        max_retries=3,
        review_mode="auto",
        event_log_enabled=False,
        event_store_config=None,
        agent=agent,
    )
    turn = TurnRecord(session_key="test:1")
    msg = _make_msg()
    await policy.before_execute(turn, msg)

    task = policy._active[turn.turn_id]
    response = _make_response()
    await policy.after_success(turn, msg, response)

    assert task.state == TaskState.APPROVED
    assert turn.turn_id not in policy._active


# ---------------------------------------------------------------------------
# 9. OrchestratorPolicy after_failure -> FAILED
# ---------------------------------------------------------------------------


async def test_orchestrator_policy_failure():
    """after_failure transitions TaskRecord to FAILED and cleans up _active."""
    agent = _make_agent()
    policy = OrchestratorPolicy(
        max_retries=3,
        review_mode="auto",
        event_log_enabled=False,
        event_store_config=None,
        agent=agent,
    )
    turn = TurnRecord(session_key="test:1")
    msg = _make_msg()
    await policy.before_execute(turn, msg)

    error = RuntimeError("terminal failure")
    await policy.after_failure(turn, msg, error)

    task = policy._tasks[list(policy._tasks.keys())[0]]
    assert task.state == TaskState.FAILED
    assert turn.turn_id not in policy._active


# ---------------------------------------------------------------------------
# 10. turn_id present in outbound response metadata
# ---------------------------------------------------------------------------


async def test_turn_id_in_outbound():
    """End-to-end: handle_message publishes OutboundMessage with turn_id in metadata."""
    bus = MessageBus()
    agent = _make_agent(bus=bus)
    response = _make_response()
    agent._process_message = AsyncMock(return_value=response)

    lifecycle = TurnLifecycle(agent, policies=[])
    msg = _make_msg()
    await lifecycle.handle_message(msg)

    out = await asyncio.wait_for(bus.consume_outbound(), timeout=2.0)
    assert "turn_id" in out.metadata
    assert len(out.metadata["turn_id"]) == 12


# ---------------------------------------------------------------------------
# 11. Lifecycle close calls policy.close()
# ---------------------------------------------------------------------------


async def test_lifecycle_close():
    """close() calls close() on all installed policies."""
    mock_policy_a = MagicMock(spec=ExecutionPolicy)
    mock_policy_a.close = AsyncMock()
    mock_policy_b = MagicMock(spec=ExecutionPolicy)
    mock_policy_b.close = AsyncMock()

    agent = _make_agent()
    lifecycle = TurnLifecycle(agent, policies=[mock_policy_a, mock_policy_b])
    await lifecycle.close()

    mock_policy_a.close.assert_awaited_once()
    mock_policy_b.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# 12. Retry hooks only run for retry-owning policies
# ---------------------------------------------------------------------------


async def test_retry_hooks_only_run_for_retry_owners():
    """PolicyA (should_retry=False) does NOT get on_retry; PolicyB (should_retry=True) does."""
    bus = MessageBus()
    agent = _make_agent(bus=bus)

    call_count = 0

    async def _process_side_effect(msg, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient")
        return _make_response()

    agent._process_message = AsyncMock(side_effect=_process_side_effect)

    policy_a = MagicMock(spec=ExecutionPolicy)
    policy_a.before_execute = AsyncMock()
    policy_a.after_success = AsyncMock()
    policy_a.after_failure = AsyncMock()
    policy_a.should_retry = MagicMock(return_value=False)
    policy_a.on_retry = AsyncMock()
    policy_a.close = AsyncMock()

    policy_b = MagicMock(spec=ExecutionPolicy)
    policy_b.before_execute = AsyncMock()
    policy_b.after_success = AsyncMock()
    policy_b.after_failure = AsyncMock()
    policy_b.should_retry = MagicMock(return_value=True)
    policy_b.on_retry = AsyncMock()
    policy_b.close = AsyncMock()

    lifecycle = TurnLifecycle(agent, policies=[policy_a, policy_b])
    msg = _make_msg()
    await lifecycle.handle_message(msg)

    # PolicyA's on_retry was NOT called
    policy_a.on_retry.assert_not_awaited()
    # PolicyB's on_retry WAS called
    policy_b.on_retry.assert_awaited_once()


# ---------------------------------------------------------------------------
# 13. OrchestratorPolicy public accessors
# ---------------------------------------------------------------------------


async def test_orchestrator_policy_public_accessors():
    """get_task() and active_tasks work after before_execute."""
    agent = _make_agent()
    policy = OrchestratorPolicy(
        max_retries=3,
        review_mode="auto",
        event_log_enabled=False,
        event_store_config=None,
        agent=agent,
    )
    turn = TurnRecord(session_key="test:1")
    msg = _make_msg()
    await policy.before_execute(turn, msg)

    task = policy._active[turn.turn_id]

    # get_task by task_id
    retrieved = policy.get_task(task.task_id)
    assert retrieved is task

    # active_tasks includes it (non-terminal)
    assert task in policy.active_tasks
    assert len(policy.active_tasks) == 1
