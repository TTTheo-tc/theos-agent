"""Tests for delegation runtime data types."""

import time

from src.agent.delegation.types import (
    HandoffSpec,
    SubagentResult,
    SubagentStatus,
    SubagentTaskRecord,
    is_terminal_status,
)
from src.agent.tools.context import ToolContext


class TestSubagentStatus:
    def test_enum_values(self):
        assert SubagentStatus.PENDING == "pending"
        assert SubagentStatus.RUNNING == "running"
        assert SubagentStatus.COMPLETED == "completed"
        assert SubagentStatus.FAILED == "failed"
        assert SubagentStatus.TIMED_OUT == "timed_out"
        assert SubagentStatus.CANCELLED == "cancelled"

    def test_terminal_states(self):
        terminal = {
            SubagentStatus.COMPLETED,
            SubagentStatus.FAILED,
            SubagentStatus.TIMED_OUT,
            SubagentStatus.CANCELLED,
        }
        non_terminal = {SubagentStatus.PENDING, SubagentStatus.RUNNING}
        assert terminal & non_terminal == set()
        assert all(is_terminal_status(status) for status in terminal)
        assert not any(is_terminal_status(status) for status in non_terminal)


class TestHandoffSpec:
    def test_defaults(self):
        h = HandoffSpec()
        assert h.context == ""
        assert h.constraints is None
        assert h.acceptance_criteria is None
        assert h.not_in_scope is None

    def test_from_dict(self):
        h = HandoffSpec(
            context="parent context",
            constraints={"max_tokens": 1000},
            acceptance_criteria="must return JSON",
            not_in_scope="do not modify tests",
        )
        assert h.constraints == {"max_tokens": 1000}


class TestSubagentTaskRecord:
    def test_required_fields(self):
        before = time.time()
        rec = SubagentTaskRecord(
            task_id="t1",
            task="do something",
            label="test",
            role="explorer",
            parent_task_id=None,
            root_session_key="cli:direct",
            depth=0,
            origin_channel="cli",
            origin_chat_id="direct",
        )
        assert rec.status == SubagentStatus.PENDING
        assert rec.result is None
        assert rec.error is None
        assert rec.handoff is None
        assert rec.created_at >= before
        assert rec.started_at is None
        assert rec.finished_at is None

    def test_is_terminal(self):
        rec = SubagentTaskRecord(
            task_id="t1",
            task="x",
            label="x",
            role=None,
            parent_task_id=None,
            root_session_key="k",
            depth=0,
            origin_channel="c",
            origin_chat_id="c",
            status=SubagentStatus.COMPLETED,
        )
        assert rec.is_terminal

    def test_is_not_terminal(self):
        rec = SubagentTaskRecord(
            task_id="t1",
            task="x",
            label="x",
            role=None,
            parent_task_id=None,
            root_session_key="k",
            depth=0,
            origin_channel="c",
            origin_chat_id="c",
            status=SubagentStatus.RUNNING,
        )
        assert not rec.is_terminal


class TestSubagentResult:
    def test_observability_fields(self):
        r = SubagentResult(
            task_id="t1",
            status=SubagentStatus.COMPLETED,
            role="explorer",
            parent_task_id=None,
            depth=0,
            result="found 3 files",
            elapsed_seconds=2.5,
            tools_used=["read_file", "glob"],
            token_usage={"prompt_tokens": 100, "completion_tokens": 50},
        )
        assert r.elapsed_seconds == 2.5
        assert "glob" in r.tools_used
        assert r.token_usage["prompt_tokens"] == 100

    def test_defaults(self):
        r = SubagentResult(
            task_id="t1",
            status=SubagentStatus.FAILED,
            role=None,
            parent_task_id=None,
            depth=0,
        )
        assert r.result is None
        assert r.elapsed_seconds is None
        assert r.tools_used is None
        assert r.token_usage is None


class TestToolContextExtension:
    def test_new_fields_default(self):
        ctx = ToolContext()
        assert ctx.root_session_key is None
        assert ctx.subagent_task_id is None
        assert ctx.spawn_depth == 0
        assert ctx.allow_subagent_spawn is False

    def test_existing_fields_unchanged(self):
        ctx = ToolContext(channel="cli", chat_id="123", sender_is_owner=True)
        assert ctx.channel == "cli"
        assert ctx.sender_is_owner is True

    def test_subagent_context(self):
        ctx = ToolContext(
            channel="system",
            sender_is_owner=False,
            root_session_key="cli:direct",
            subagent_task_id="task-abc",
            spawn_depth=1,
            allow_subagent_spawn=True,
        )
        assert ctx.root_session_key == "cli:direct"
        assert ctx.spawn_depth == 1
        assert ctx.allow_subagent_spawn is True
