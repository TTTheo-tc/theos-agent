"""Tests for ProcessTool and ProcessRegistry."""

from __future__ import annotations

import asyncio
import shlex
import sys

import pytest

from src.agent.tools.process import _MAX_BUFFER, ProcessRegistry, ProcessSession, ProcessTool

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry():
    """Ensure a clean registry for every test."""
    ProcessRegistry.reset()
    yield
    ProcessRegistry.reset()


def _tool() -> ProcessTool:
    return ProcessTool(working_dir="/tmp")


def _shell_quote(s: str) -> str:
    """Shell-quote a string for use in subprocess commands."""
    return shlex.quote(s)


# ---------------------------------------------------------------------------
# ProcessRegistry unit tests
# ---------------------------------------------------------------------------


class TestProcessRegistry:
    def test_singleton(self):
        r1 = ProcessRegistry.get()
        r2 = ProcessRegistry.get()
        assert r1 is r2

    def test_reset(self):
        r1 = ProcessRegistry.get()
        ProcessRegistry.reset()
        r2 = ProcessRegistry.get()
        assert r1 is not r2

    @pytest.mark.asyncio
    async def test_start_and_list(self):
        reg = ProcessRegistry.get()
        session = await reg.start("echo hello", cwd="/tmp")
        assert session.session_id.startswith("bg_")
        assert session.command == "echo hello"
        sessions = reg.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].session_id == session.session_id

    @pytest.mark.asyncio
    async def test_start_custom_id(self):
        reg = ProcessRegistry.get()
        session = await reg.start("echo hi", cwd="/tmp", session_id="my_session")
        assert session.session_id == "my_session"
        assert reg.get_session("my_session") is session

    @pytest.mark.asyncio
    async def test_get_session_missing(self):
        reg = ProcessRegistry.get()
        assert reg.get_session("nonexistent") is None

    @pytest.mark.asyncio
    async def test_remove(self):
        reg = ProcessRegistry.get()
        session = await reg.start("echo bye", cwd="/tmp")
        await session.process.wait()
        assert reg.remove(session.session_id) is True
        assert reg.remove(session.session_id) is False
        assert reg.get_session(session.session_id) is None


# ---------------------------------------------------------------------------
# ProcessSession unit tests
# ---------------------------------------------------------------------------


class TestProcessSession:
    def test_buffer_truncation_preserves_incremental_cursor(self):
        process = type("_FakeProcess", (), {"pid": 1, "returncode": None})()
        session = ProcessSession(
            session_id="test",
            command="fake",
            cwd="/tmp",
            process=process,
        )
        session._append_stdout(b"a" * _MAX_BUFFER)
        session.drain()

        session._append_stdout(b"b" * 10)

        assert session.drain() == "b" * 10

    def test_stderr_buffer_truncation_preserves_incremental_cursor(self):
        process = type("_FakeProcess", (), {"pid": 1, "returncode": None})()
        session = ProcessSession(
            session_id="test",
            command="fake",
            cwd="/tmp",
            process=process,
        )
        session._append_stderr(b"a" * _MAX_BUFFER)
        session.drain()

        session._append_stderr(b"b" * 10)

        assert session.drain() == "b" * 10

    @pytest.mark.asyncio
    async def test_drain_output(self):
        reg = ProcessRegistry.get()
        session = await reg.start("echo hello_world", cwd="/tmp")
        # Wait for process to finish
        await session.process.wait()
        await asyncio.sleep(0.1)  # Let reader tasks drain
        output = session.drain()
        assert "hello_world" in output

    @pytest.mark.asyncio
    async def test_drain_incremental(self):
        """drain() returns only new output since last call."""
        reg = ProcessRegistry.get()
        # Use a script that flushes explicitly for reliable timing
        script = (
            "import sys, time\n"
            "print('first', flush=True)\n"
            "time.sleep(0.5)\n"
            "print('second', flush=True)\n"
        )
        session = await reg.start(
            f"{sys.executable} -c {_shell_quote(script)}",
            cwd="/tmp",
        )
        # Wait until 'first' appears
        for _ in range(20):
            await asyncio.sleep(0.1)
            if b"first" in session._stdout_buf:
                break
        first = session.drain()
        assert "first" in first

        await session.process.wait()
        await asyncio.sleep(0.15)
        second = session.drain()
        assert "second" in second
        # "first" should NOT appear in the second drain
        assert "first" not in second

    @pytest.mark.asyncio
    async def test_aggregated(self):
        reg = ProcessRegistry.get()
        session = await reg.start("echo full_output", cwd="/tmp")
        await session.process.wait()
        await asyncio.sleep(0.1)
        # Drain once
        session.drain()
        # aggregated still has everything
        assert "full_output" in session.aggregated

    @pytest.mark.asyncio
    async def test_tail(self):
        reg = ProcessRegistry.get()
        script = "\n".join(f"print({i})" for i in range(20))
        session = await reg.start(f"{sys.executable} -c {_shell_quote(script)}", cwd="/tmp")
        await session.process.wait()
        await asyncio.sleep(0.15)
        tail5 = session.tail(5)
        lines = tail5.strip().splitlines()
        # Should have the "omitted" note + 5 lines
        assert "omitted" in tail5
        # Last line should be "19"
        assert lines[-1].strip() == "19"

    @pytest.mark.asyncio
    async def test_running_property(self):
        reg = ProcessRegistry.get()
        session = await reg.start("sleep 10", cwd="/tmp")
        assert session.running is True
        session.process.terminate()
        await session.process.wait()
        assert session.running is False

    @pytest.mark.asyncio
    async def test_pid(self):
        reg = ProcessRegistry.get()
        session = await reg.start("echo pid_test", cwd="/tmp")
        assert session.pid is not None
        assert isinstance(session.pid, int)
        await session.process.wait()


# ---------------------------------------------------------------------------
# Tool: schema
# ---------------------------------------------------------------------------


class TestProcessToolSchema:
    def test_name(self):
        assert _tool().name == "process"

    def test_risk_level(self):
        assert _tool().risk_level == "medium"

    def test_parameters(self):
        params = _tool().parameters
        assert "action" in params["properties"]
        assert params["required"] == ["action"]

    def test_to_schema(self):
        schema = _tool().to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "process"


# ---------------------------------------------------------------------------
# Tool: start action
# ---------------------------------------------------------------------------


class TestToolStart:
    @pytest.mark.asyncio
    async def test_start_basic(self):
        tool = _tool()
        result = await tool.execute(action="start", command="echo started")
        assert "Started background process" in result
        assert "session_id:" in result
        assert "pid:" in result

    @pytest.mark.asyncio
    async def test_start_no_command(self):
        tool = _tool()
        result = await tool.execute(action="start")
        assert "Error" in result
        assert "command" in result.lower()


# ---------------------------------------------------------------------------
# Tool: list action
# ---------------------------------------------------------------------------


class TestToolList:
    @pytest.mark.asyncio
    async def test_list_empty(self):
        tool = _tool()
        result = await tool.execute(action="list")
        assert "No tracked processes" in result

    @pytest.mark.asyncio
    async def test_list_with_processes(self):
        tool = _tool()
        await tool.execute(action="start", command="sleep 30")
        result = await tool.execute(action="list")
        assert "Tracked processes" in result
        assert "running" in result
        assert "sleep 30" in result


# ---------------------------------------------------------------------------
# Tool: poll action
# ---------------------------------------------------------------------------


class TestToolPoll:
    @pytest.mark.asyncio
    async def test_poll_running(self):
        tool = _tool()
        start_result = await tool.execute(action="start", command="sleep 30")
        sid = _extract_session_id(start_result)
        result = await tool.execute(action="poll", session_id=sid)
        assert "still running" in result

    @pytest.mark.asyncio
    async def test_poll_with_output(self):
        tool = _tool()
        start_result = await tool.execute(action="start", command="echo poll_output")
        sid = _extract_session_id(start_result)
        # Wait for process to finish
        await asyncio.sleep(0.3)
        result = await tool.execute(action="poll", session_id=sid)
        assert "poll_output" in result
        assert "exited" in result

    @pytest.mark.asyncio
    async def test_poll_with_timeout(self):
        tool = _tool()
        start_result = await tool.execute(action="start", command="echo timeout_test")
        sid = _extract_session_id(start_result)
        # Poll with timeout — should wait for process to finish
        result = await tool.execute(action="poll", session_id=sid, timeout=5000)
        assert "timeout_test" in result
        assert "exited" in result

    @pytest.mark.asyncio
    async def test_poll_no_session_id(self):
        tool = _tool()
        result = await tool.execute(action="poll")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_poll_unknown_session(self):
        tool = _tool()
        result = await tool.execute(action="poll", session_id="ghost")
        assert "Error" in result
        assert "ghost" in result


# ---------------------------------------------------------------------------
# Tool: send_input action
# ---------------------------------------------------------------------------


class TestToolSendInput:
    @pytest.mark.asyncio
    async def test_send_input(self):
        tool = _tool()
        start_result = await tool.execute(action="start", command="cat")
        sid = _extract_session_id(start_result)
        await asyncio.sleep(0.1)

        result = await tool.execute(action="send_input", session_id=sid, input="hello\n")
        assert "Sent" in result
        assert "6 bytes" in result

        # Poll to see the echoed output
        await asyncio.sleep(0.2)
        poll_result = await tool.execute(action="poll", session_id=sid)
        assert "hello" in poll_result

    @pytest.mark.asyncio
    async def test_send_input_no_session(self):
        tool = _tool()
        result = await tool.execute(action="send_input", session_id="nope", input="x")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_send_input_exited_process(self):
        tool = _tool()
        start_result = await tool.execute(action="start", command="echo done")
        sid = _extract_session_id(start_result)
        await asyncio.sleep(0.3)
        result = await tool.execute(action="send_input", session_id=sid, input="x")
        assert "Error" in result or "exited" in result


# ---------------------------------------------------------------------------
# Tool: terminate action
# ---------------------------------------------------------------------------


class TestToolTerminate:
    @pytest.mark.asyncio
    async def test_terminate_running(self):
        tool = _tool()
        start_result = await tool.execute(action="start", command="sleep 300")
        sid = _extract_session_id(start_result)
        result = await tool.execute(action="terminate", session_id=sid)
        assert "Terminated" in result

        # Should be gone from registry
        list_result = await tool.execute(action="list")
        assert sid not in list_result or "No tracked" in list_result

    @pytest.mark.asyncio
    async def test_terminate_already_exited(self):
        tool = _tool()
        start_result = await tool.execute(action="start", command="echo bye")
        sid = _extract_session_id(start_result)
        await asyncio.sleep(0.3)
        result = await tool.execute(action="terminate", session_id=sid)
        assert "already exited" in result or "Removed" in result

    @pytest.mark.asyncio
    async def test_terminate_unknown(self):
        tool = _tool()
        result = await tool.execute(action="terminate", session_id="nope")
        assert "Error" in result


# ---------------------------------------------------------------------------
# Tool: error cases
# ---------------------------------------------------------------------------


class TestToolErrors:
    @pytest.mark.asyncio
    async def test_no_action(self):
        tool = _tool()
        result = await tool.execute()
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        tool = _tool()
        result = await tool.execute(action="explode")
        assert "Error" in result
        assert "Unknown action" in result


# ---------------------------------------------------------------------------
# Integration: start → poll → terminate lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self):
        tool = _tool()
        script = "import sys, time\n" "print('alive', flush=True)\n" "time.sleep(60)\n"

        # Start
        start_result = await tool.execute(
            action="start",
            command=f"{sys.executable} -c {_shell_quote(script)}",
        )
        assert "Started" in start_result
        sid = _extract_session_id(start_result)

        # List
        list_result = await tool.execute(action="list")
        assert sid in list_result
        assert "running" in list_result

        # Poll with timeout to wait for output
        poll_result = await tool.execute(action="poll", session_id=sid, timeout=5000)
        assert "alive" in poll_result
        assert "still running" in poll_result

        # Terminate
        term_result = await tool.execute(action="terminate", session_id=sid)
        assert "Terminated" in term_result

    @pytest.mark.asyncio
    async def test_stderr_captured(self):
        tool = _tool()
        start_result = await tool.execute(
            action="start",
            command=f"{sys.executable} -c \"import sys; sys.stderr.write('err_msg\\n')\"",
        )
        sid = _extract_session_id(start_result)
        await asyncio.sleep(0.3)
        poll_result = await tool.execute(action="poll", session_id=sid)
        assert "err_msg" in poll_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_session_id(start_result: str) -> str:
    """Extract session_id from a start result string."""
    for line in start_result.splitlines():
        if "session_id:" in line:
            return line.split("session_id:")[1].strip()
    raise ValueError(f"Could not extract session_id from: {start_result}")
