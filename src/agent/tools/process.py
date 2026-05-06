"""Process management tool — list, poll, send input, and terminate background processes.

Provides a global process registry that tracks asyncio subprocesses.
Other tools (or the agent itself) can start background processes via
``ProcessRegistry.start()``, then manage them through this tool.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.agent.tools.base import Tool
from src.agent.tools.tool_security import policy_error

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_BUFFER = 256 * 1024  # 256 KiB per stream
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07|\x1b\(B")
_DEFAULT_TAIL_LINES = 200


def _clean_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


# ---------------------------------------------------------------------------
# Process session
# ---------------------------------------------------------------------------


@dataclass
class ProcessSession:
    """One tracked background process."""

    session_id: str
    command: str
    cwd: str
    process: asyncio.subprocess.Process
    started_at: float = field(default_factory=time.monotonic)

    # Accumulated output (ring-buffered to _MAX_BUFFER)
    _stdout_buf: bytearray = field(default_factory=bytearray, repr=False)
    _stderr_buf: bytearray = field(default_factory=bytearray, repr=False)

    # Incremental drain cursors
    _stdout_cursor: int = 0
    _stderr_cursor: int = 0

    # Reader tasks
    _readers: list[asyncio.Task[None]] = field(default_factory=list, repr=False)

    # Exit info
    exit_code: int | None = None
    exit_signal: str | None = None
    ended_at: float | None = None

    @property
    def pid(self) -> int | None:
        return self.process.pid

    @property
    def running(self) -> bool:
        return self.process.returncode is None

    @property
    def runtime_s(self) -> float:
        end = self.ended_at if self.ended_at else time.monotonic()
        return end - self.started_at

    # --- output helpers ---

    def _append_stream(self, buffer: bytearray, cursor_name: str, data: bytes) -> None:
        buffer.extend(data)
        if len(buffer) <= _MAX_BUFFER:
            return
        excess = len(buffer) - _MAX_BUFFER
        del buffer[:excess]
        setattr(self, cursor_name, max(0, getattr(self, cursor_name) - excess))

    def _append_stdout(self, data: bytes) -> None:
        self._append_stream(self._stdout_buf, "_stdout_cursor", data)

    def _append_stderr(self, data: bytes) -> None:
        self._append_stream(self._stderr_buf, "_stderr_cursor", data)

    def drain(self) -> str:
        """Return new output since last drain, then advance cursors."""
        output = _combined_output(
            self._stdout_buf[self._stdout_cursor :],
            self._stderr_buf[self._stderr_cursor :],
        )
        self._stdout_cursor = len(self._stdout_buf)
        self._stderr_cursor = len(self._stderr_buf)
        return output

    @property
    def aggregated(self) -> str:
        """Full accumulated output."""
        return _combined_output(self._stdout_buf, self._stderr_buf)

    def tail(self, n: int = _DEFAULT_TAIL_LINES) -> str:
        """Last *n* lines of aggregated output."""
        lines = self.aggregated.splitlines()
        if len(lines) <= n:
            return "\n".join(lines)
        return f"[… {len(lines) - n} earlier lines omitted]\n" + "\n".join(lines[-n:])

    async def _mark_exited(self) -> None:
        """Called when the process exits."""
        rc = self.process.returncode
        self.ended_at = time.monotonic()
        if rc is not None and rc < 0:
            try:
                self.exit_signal = signal.Signals(-rc).name
            except (ValueError, AttributeError):
                self.exit_signal = str(rc)
            self.exit_code = rc
        else:
            self.exit_code = rc


def _stream_text(data: bytes | bytearray) -> str:
    return _clean_ansi(bytes(data).decode("utf-8", errors="replace")).rstrip()


def _combined_output(stdout: bytes | bytearray, stderr: bytes | bytearray) -> str:
    parts = [_stream_text(stdout), _stream_text(stderr)]
    return "\n".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# Global process registry
# ---------------------------------------------------------------------------

_next_id = 0


def _gen_id() -> str:
    global _next_id
    _next_id += 1
    return f"bg_{_next_id}"


async def _read_stream(
    stream: asyncio.StreamReader | None,
    append_fn: Callable[[bytes], None],
) -> None:
    if stream is None:
        return
    while True:
        data = await stream.read(4096)
        if not data:
            break
        append_fn(data)


async def _wait_for_exit(session: ProcessSession, proc: asyncio.subprocess.Process) -> None:
    await proc.wait()
    for reader in session._readers:
        try:
            await asyncio.wait_for(reader, timeout=2.0)
        except (asyncio.TimeoutError, Exception):
            pass
    await session._mark_exited()


class ProcessRegistry:
    """Singleton registry of background processes."""

    _instance: ProcessRegistry | None = None

    def __init__(self) -> None:
        self._sessions: dict[str, ProcessSession] = {}

    @classmethod
    def get(cls) -> ProcessRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for tests)."""
        if cls._instance is not None:
            for s in cls._instance._sessions.values():
                if s.running:
                    try:
                        s.process.kill()
                    except ProcessLookupError:
                        pass
            cls._instance._sessions.clear()
        cls._instance = None

    async def start(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        session_id: str | None = None,
    ) -> ProcessSession:
        """Start a background process and register it."""
        sid = session_id or _gen_id()
        effective_cwd = cwd or os.getcwd()
        effective_env = env or os.environ.copy()

        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=effective_cwd,
            env=effective_env,
            start_new_session=True,
        )

        session = ProcessSession(
            session_id=sid,
            command=command,
            cwd=effective_cwd,
            process=proc,
        )

        loop = asyncio.get_running_loop()
        if proc.stdout:
            t1 = loop.create_task(_read_stream(proc.stdout, session._append_stdout))
            session._readers.append(t1)
        if proc.stderr:
            t2 = loop.create_task(_read_stream(proc.stderr, session._append_stderr))
            session._readers.append(t2)

        loop.create_task(_wait_for_exit(session, proc))

        self._sessions[sid] = session
        return session

    def get_session(self, session_id: str) -> ProcessSession | None:
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[ProcessSession]:
        return list(self._sessions.values())

    def remove(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None


# ---------------------------------------------------------------------------
# ProcessTool
# ---------------------------------------------------------------------------


class ProcessTool(Tool):
    """Manage background processes: list, poll, send_input, terminate."""

    def __init__(self, working_dir: str | None = None) -> None:
        self._working_dir = working_dir or os.getcwd()

    @property
    def name(self) -> str:
        return "process"

    @property
    def description(self) -> str:
        return (
            "Manage background processes. Actions: "
            "'start' (launch a command in background), "
            "'list' (show all tracked processes), "
            "'poll' (read new output from a process), "
            "'send_input' (write to a process's stdin), "
            "'terminate' (kill a process)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["start", "list", "poll", "send_input", "terminate"],
                    "description": "Action to perform.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Session ID (required for poll, send_input, terminate).",
                },
                "command": {
                    "type": "string",
                    "description": "Shell command to run (for 'start').",
                },
                "input": {
                    "type": "string",
                    "description": "Text to send to stdin (for 'send_input').",
                },
                "timeout": {
                    "type": "number",
                    "description": (
                        "For 'poll': wait up to this many milliseconds for "
                        "new output before returning (default 0, max 120000)."
                    ),
                },
            },
            "required": ["action"],
        }

    @property
    def risk_level(self) -> str:
        return "medium"

    async def execute(self, **kwargs: Any) -> str:
        action = kwargs.get("action", "")
        if not action:
            return "Error: 'action' is required."

        registry = ProcessRegistry.get()

        if action == "start":
            return await self._start(registry, kwargs)
        elif action == "list":
            return self._list(registry)
        elif action == "poll":
            return await self._poll(registry, kwargs)
        elif action == "send_input":
            return await self._send_input(registry, kwargs)
        elif action == "terminate":
            return await self._terminate(registry, kwargs)
        else:
            return (
                f"Error: Unknown action '{action}'. Use: start, list, poll, send_input, terminate."
            )

    # --- action implementations ---

    async def _start(self, registry: ProcessRegistry, kwargs: dict[str, Any]) -> str:
        command = kwargs.get("command", "")
        if not command:
            return "Error: 'command' is required for 'start'."

        # Security check
        err = policy_error(command, kind="Process start")
        if err:
            return err

        try:
            session = await registry.start(
                command=command,
                cwd=self._working_dir,
            )
        except Exception as e:
            return f"Error starting process: {e}"

        return (
            f"Started background process.\n"
            f"  session_id: {session.session_id}\n"
            f"  pid: {session.pid}\n"
            f"  command: {command}"
        )

    def _list(self, registry: ProcessRegistry) -> str:
        sessions = registry.list_sessions()
        if not sessions:
            return "No tracked processes."

        lines: list[str] = []
        for s in sorted(sessions, key=lambda x: x.started_at, reverse=True):
            status = "running" if s.running else f"exited({s.exit_code})"
            runtime = f"{s.runtime_s:.1f}s"
            cmd_display = s.command[:80] + ("…" if len(s.command) > 80 else "")
            lines.append(f"  {s.session_id}  {status:<14} {runtime:>8}  {cmd_display}")
        return f"Tracked processes ({len(sessions)}):\n" + "\n".join(lines)

    async def _poll(self, registry: ProcessRegistry, kwargs: dict[str, Any]) -> str:
        session_id = kwargs.get("session_id", "")
        if not session_id:
            return "Error: 'session_id' is required for 'poll'."

        session = registry.get_session(session_id)
        if session is None:
            return f"Error: No session found for '{session_id}'."

        # Optional wait
        timeout_ms = kwargs.get("timeout", 0)
        if isinstance(timeout_ms, (int, float)) and timeout_ms > 0:
            wait_s = min(timeout_ms / 1000.0, 120.0)
            deadline = time.monotonic() + wait_s
            while session.running and time.monotonic() < deadline:
                await asyncio.sleep(min(0.25, deadline - time.monotonic()))

        output = session.drain()
        return _format_poll_result(session, output)

    async def _send_input(self, registry: ProcessRegistry, kwargs: dict[str, Any]) -> str:
        session_id = kwargs.get("session_id", "")
        if not session_id:
            return "Error: 'session_id' is required for 'send_input'."

        session = registry.get_session(session_id)
        if session is None:
            return f"Error: No session found for '{session_id}'."

        if not session.running:
            return f"Error: Process '{session_id}' has already exited."

        stdin = session.process.stdin
        if stdin is None or stdin.is_closing():
            return f"Error: stdin is not writable for '{session_id}'."

        data = kwargs.get("input", "")
        try:
            stdin.write(data.encode("utf-8"))
            await stdin.drain()
        except Exception as e:
            return f"Error writing to stdin: {e}"

        return f"Sent {len(data)} bytes to '{session_id}'."

    async def _terminate(self, registry: ProcessRegistry, kwargs: dict[str, Any]) -> str:
        session_id = kwargs.get("session_id", "")
        if not session_id:
            return "Error: 'session_id' is required for 'terminate'."

        session = registry.get_session(session_id)
        if session is None:
            return f"Error: No session found for '{session_id}'."

        if not session.running:
            registry.remove(session_id)
            return f"Process '{session_id}' already exited (code {session.exit_code}). Removed."

        pid = session.pid
        try:
            _send_signal(session, signal.SIGTERM)
            try:
                await asyncio.wait_for(session.process.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                _send_signal(session, signal.SIGKILL)
                try:
                    await asyncio.wait_for(session.process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
        except ProcessLookupError:
            pass

        registry.remove(session_id)
        return f"Terminated process '{session_id}' (pid {pid})."


def _format_poll_result(session: ProcessSession, output: str) -> str:
    body = output or "(no new output)"
    if session.running:
        return body + "\n\nProcess still running."
    return body + f"\n\nProcess exited with {_exit_info(session)}."


def _exit_info(session: ProcessSession) -> str:
    if session.exit_signal and session.exit_code is not None and session.exit_code < 0:
        return f"signal {session.exit_signal}"
    return f"code {session.exit_code}"


def _send_signal(session: ProcessSession, sig: signal.Signals) -> None:
    pid = session.pid
    if not pid:
        _send_process_signal(session, sig)
        return

    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError):
        _send_process_signal(session, sig)


def _send_process_signal(session: ProcessSession, sig: signal.Signals) -> None:
    if sig == signal.SIGTERM:
        session.process.terminate()
    elif sig == signal.SIGKILL:
        session.process.kill()
