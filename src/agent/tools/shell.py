"""Shell execution tool with state persistence across calls."""

from __future__ import annotations

import asyncio
import os
import pty
import re
import select
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from src.agent.tools.base import Tool
from src.agent.tools.tool_security import policy_error

# ---------------------------------------------------------------------------
# Environment sanitization
# ---------------------------------------------------------------------------

_SAFE_ENV_VARS = frozenset(
    {
        # POSIX essentials
        "PATH",
        "HOME",
        "TERM",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "USER",
        "SHELL",
        "TMPDIR",
        # Developer toolchain
        "EDITOR",
        "VISUAL",
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "NODE_PATH",
        "NPM_CONFIG_PREFIX",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "PYTHONDONTWRITEBYTECODE",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        # Windows
        "PATHEXT",
        "USERPROFILE",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "USERNAME",
    }
)


def _filter_env(env: dict[str, str], passthrough: set[str]) -> dict[str, str]:
    """Return only safe environment variables plus explicit passthrough."""
    allowed = _SAFE_ENV_VARS | passthrough
    return {k: v for k, v in env.items() if k in allowed}


# ---------------------------------------------------------------------------
# Semantic exit codes – many tools use non-zero for non-error conditions
# ---------------------------------------------------------------------------

_SEMANTIC_EXIT_CODES: dict[str, dict[int, str]] = {
    "grep": {1: "No matches found"},
    "rg": {1: "No matches found"},
    "ag": {1: "No matches found"},
    "ack": {1: "No matches found"},
    "diff": {1: "Files differ"},
    "cmp": {1: "Files differ"},
    "test": {1: "Condition is false"},
    "[": {1: "Condition is false"},
    "find": {1: "Some paths inaccessible (partial results returned)"},
}


def _semantic_exit_message(command: str, returncode: int) -> str | None:
    """Return a human-friendly exit message if the command's exit code has
    a well-known semantic meaning, otherwise ``None``."""
    # Extract the base command name (first token, ignore leading env
    # assignments like ``VAR=val cmd``).
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    base: str | None = None
    for tok in tokens:
        if "=" in tok and not tok.startswith("-"):
            continue  # skip env assignments
        base = os.path.basename(tok)
        break
    if base is None:
        return None
    meanings = _SEMANTIC_EXIT_CODES.get(base)
    if meanings and returncode in meanings:
        return f"({base}: {meanings[returncode]})"
    return None


def _format_command_result(command: str, output: str, returncode: int) -> str:
    parts = []
    if output.strip():
        parts.append(output)
    if returncode != 0:
        semantic = _semantic_exit_message(command, returncode)
        if semantic:
            parts.append(f"\n{semantic}")
        else:
            parts.append(f"\nExit code: {returncode}")
    return "\n".join(parts) if parts else "(no output)"


# ---------------------------------------------------------------------------
# Wrapper-command stripping for safety guard bypass prevention
# ---------------------------------------------------------------------------

# Each entry: (regex matching the wrapper prefix, number of capture groups to skip)
# The regex must match from the start of the (remaining) command string.
_WRAPPER_PATTERNS: list[re.Pattern[str]] = [
    # timeout [-k duration] [-s signal] duration command...
    re.compile(r"timeout\s+(?:-[ks]\s+\S+\s+)*\S+\s+"),
    # nice [-n adjustment] command...
    re.compile(r"nice\s+(?:-n\s+\S+\s+)?"),
    # nohup command...
    re.compile(r"nohup\s+"),
    # env [VAR=val ...] command...  (strip env keyword + any VAR=val pairs)
    re.compile(r"env\s+(?:\S+=\S*\s+)*"),
    # time command...
    re.compile(r"time\s+"),
    # stdbuf [-i N] [-o N] [-e N] command...
    re.compile(r"stdbuf\s+(?:-[ioe]\s+\S+\s+)*"),
]


def _strip_wrappers(command: str) -> str:
    """Recursively strip common wrapper command prefixes.

    For example ``timeout 10 nice -n 5 rm -rf /`` → ``rm -rf /``.
    """
    cmd = command.strip()
    changed = True
    while changed:
        changed = False
        for pattern in _WRAPPER_PATTERNS:
            m = pattern.match(cmd)
            if m:
                cmd = cmd[m.end() :]
                changed = True
                break  # restart from the top after each strip
    return cmd


# ---------------------------------------------------------------------------
# PTY runner (one-shot, but used with state save/restore for persistence)
# ---------------------------------------------------------------------------

_MAX_OUTPUT = 30000
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07|\x1b\(B")


async def _run_in_pty(
    command: str,
    cwd: str,
    env: dict[str, str],
    timeout: int,
    on_progress: Callable[[int, float], None] | None = None,
) -> tuple[str, int]:
    """Run *command* inside a PTY so the child sees a real terminal.

    Parameters
    ----------
    on_progress:
        Optional callback invoked every ~5 seconds with
        ``(output_bytes_so_far, elapsed_seconds)``.

    Returns ``(output, returncode)``.
    """
    loop = asyncio.get_running_loop()

    def _sync_run() -> tuple[str, int]:
        master_fd, slave_fd = pty.openpty()
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd,
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                start_new_session=True,
            )
        finally:
            os.close(slave_fd)

        output_chunks: list[bytes] = []
        total_len = 0
        start_time = time.monotonic()
        last_progress_time = start_time
        progress_interval = 5.0  # seconds between progress callbacks
        try:
            end = start_time + timeout
            while True:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except Exception:
                        proc.kill()
                    proc.wait()
                    output_chunks.append(f"\n(timed out after {timeout}s)".encode())
                    break
                ready, _, _ = select.select([master_fd], [], [], min(remaining, 0.5))
                if ready:
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    if total_len < _MAX_OUTPUT:
                        output_chunks.append(data)
                        total_len += len(data)
                elif proc.poll() is not None:
                    break

                # Progress callback
                now = time.monotonic()
                if on_progress and (now - last_progress_time) >= progress_interval:
                    elapsed = now - start_time
                    on_progress(total_len, elapsed)
                    last_progress_time = now
        finally:
            os.close(master_fd)

        proc.wait()
        raw = b"".join(output_chunks).decode("utf-8", errors="replace")
        clean = _ANSI_RE.sub("", raw)
        if total_len > _MAX_OUTPUT:
            clean = clean[:_MAX_OUTPUT] + f"\n... (truncated, {total_len - _MAX_OUTPUT} more chars)"
        return clean, proc.returncode

    return await loop.run_in_executor(None, _sync_run)


# ---------------------------------------------------------------------------
# Shell state (cwd + env) persisted across calls
# ---------------------------------------------------------------------------


class _ShellState:
    """Track cwd and exported env vars across one-shot PTY calls."""

    def __init__(self, cwd: str, base_env: dict[str, str], state_dir: str | None = None) -> None:
        self.cwd = cwd
        self.env = dict(base_env)
        self._state_dir = state_dir or tempfile.mkdtemp(prefix="sb_shell_")

    def cleanup(self) -> None:
        """Remove the temporary state directory."""
        if self._state_dir and os.path.isdir(self._state_dir):
            shutil.rmtree(self._state_dir, ignore_errors=True)

    async def run(
        self,
        command: str,
        timeout: int,
        on_progress: Callable[[int, float], None] | None = None,
    ) -> tuple[str, int]:
        """Execute command, then capture resulting cwd and env."""
        state_file = os.path.join(self._state_dir, f"state_{os.getpid()}")
        cwd_file = f"{state_file}_cwd"
        env_file = f"{state_file}_env"

        # Wrap: run command, save exit code, dump cwd and env to files
        wrapped = (
            f"{command}\n"
            f"__sb_ec=$?; "
            f"pwd > {cwd_file} 2>/dev/null; "
            f"env -0 > {env_file} 2>/dev/null; "
            f"exit $__sb_ec"
        )

        output, returncode = await _run_in_pty(
            wrapped, self.cwd, self.env, timeout, on_progress=on_progress
        )

        # Restore cwd
        try:
            with open(cwd_file, encoding="utf-8") as fh:
                new_cwd = fh.read().strip()
            if new_cwd and os.path.isdir(new_cwd):
                self.cwd = new_cwd
        except Exception:
            pass
        finally:
            with suppress(OSError):
                os.unlink(cwd_file)

        # Restore env (null-delimited KEY=VALUE pairs)
        try:
            with open(env_file, "rb") as fh:
                raw = fh.read()
            for entry in raw.split(b"\x00"):
                if b"=" in entry:
                    k, v = entry.decode("utf-8", "replace").split("=", 1)
                    if k and not k.startswith("__sb_"):
                        self.env[k] = v
        except Exception:
            pass
        finally:
            with suppress(OSError):
                os.unlink(env_file)

        return output, returncode


# ---------------------------------------------------------------------------
# ExecTool (persistent state via _ShellState)
# ---------------------------------------------------------------------------


class ExecTool(Tool):
    """Bash tool with persistent state (cwd, env vars) across calls.

    Each command runs in a fresh PTY process, but working directory and
    environment variables persist between calls — ``cd``, ``export``,
    and ``source`` effects carry over to subsequent commands.
    """

    # Default progress callback (overridden per-instance in __init__).
    _progress_callback: Callable[[int, float], None] | None = None

    def __init__(
        self,
        timeout: int = 120,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        path_append: str = "",
        env_passthrough: list[str] | None = None,
    ) -> None:
        self.timeout = timeout
        self.working_dir = working_dir or os.getcwd()
        self.deny_patterns = deny_patterns or [
            r"\brm\s+-[rf]{1,2}\b",
            r"\bdel\s+/[fq]\b",
            r"\brmdir\s+/s\b",
            r"(?:^|[;&|]\s*)format\b",
            r"\b(mkfs|diskpart)\b",
            r"\bdd\s+if=",
            r">\s*/dev/sd",
            r"\b(shutdown|reboot|poweroff)\b",
            r":\(\)\s*\{.*\};\s*:",
            r"\b(pkill|killall)\b.*\bgateway\b",
            r"\bkill\b.*\bsrc\s+gateway\b",
            r"\bsystemctl\b.*\brestart\b.*\bgateway\b",
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        self.path_append = path_append
        self._env_passthrough: set[str] = set(env_passthrough or [])
        self._state: _ShellState | None = None
        self._background_tasks: dict[str, asyncio.Task[str]] = {}
        self._background_created: dict[str, float] = {}  # task_id → monotonic time
        # Progress callback: called with (output_bytes, elapsed_seconds).
        # Must be thread-safe (invoked from executor thread, not event loop).
        self._progress_callback: Callable[[int, float], None] | None = None

    def _get_state(self) -> _ShellState:
        if self._state is None:
            env = _filter_env(os.environ.copy(), self._env_passthrough)
            if self.path_append:
                env["PATH"] = env.get("PATH", "") + os.pathsep + self.path_append
            self._state = _ShellState(self.working_dir, env)
        return self._state

    _HIGH_RISK_PATTERNS: list[str] = [
        r"\bgit\s+push\b",
        r"\brm\s",
        r"\bsudo\b",
        r"\bdocker\b",
        r"\bkubectl\s+delete\b",
        r"\bdrop\s+table\b",
    ]

    @property
    def risk_level(self) -> str:
        return "medium"

    def assess_risk(self, command: str = "", **_: Any) -> str:
        """Assess the risk level of a specific command."""
        lower = command.lower()
        for pattern in self._HIGH_RISK_PATTERNS:
            if re.search(pattern, lower):
                return "high"
        return "medium"

    @property
    def name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return (
            "Execute a bash command and return its output. "
            "Working directory and env vars persist across calls (cd, export work). "
            "Runs in a PTY so interactive commands work. "
            "Use semicolon or && to chain commands. "
            "Prefer dedicated tools (read_file, glob, grep) over cat/find/grep."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute",
                },
                "timeout": {
                    "type": "number",
                    "description": "Max execution time in milliseconds (default 120000, max 600000)",
                },
                "description": {
                    "type": "string",
                    "description": "Short (5-10 word) description of what the command does",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "Run command in background and return immediately with a task ID.",
                },
            },
            "required": ["command"],
        }

    _BG_TASK_MAX_AGE = 3600  # 1 hour

    def _sweep_stale_background_tasks(self) -> None:
        """Discard completed background tasks older than _BG_TASK_MAX_AGE."""
        now = time.monotonic()
        stale = [
            tid
            for tid, created in self._background_created.items()
            if now - created > self._BG_TASK_MAX_AGE
            and (t := self._background_tasks.get(tid)) is not None
            and t.done()
        ]
        for tid in stale:
            self._background_tasks.pop(tid, None)
            self._background_created.pop(tid, None)

    def get_background_result(self, task_id: str) -> str:
        """Return the result of a background task, or its current status."""
        self._sweep_stale_background_tasks()
        task = self._background_tasks.get(task_id)
        if task is None:
            return f"Error: unknown task id '{task_id}'"
        if not task.done():
            return f"Task {task_id} is still running."
        self._background_tasks.pop(task_id, None)
        self._background_created.pop(task_id, None)
        exc = task.exception()
        if exc is not None:
            return f"Background task {task_id} failed: {exc}"
        return task.result()

    def _effective_timeout(self, timeout_ms: int | None) -> int:
        if timeout_ms is not None:
            return min(int(timeout_ms / 1000), 600)
        return self.timeout

    async def _run_command(self, state: _ShellState, command: str, timeout: int) -> str:
        try:
            output, returncode = await state.run(
                command,
                timeout,
                on_progress=self._progress_callback,
            )
            return _format_command_result(command, output, returncode)
        except Exception as e:
            return f"Error executing command: {str(e)}"

    def _start_background_task(self, state: _ShellState, command: str, timeout: int) -> str:
        self._sweep_stale_background_tasks()
        task_id = f"bg_{uuid4().hex[:8]}"
        self._background_tasks[task_id] = asyncio.create_task(
            self._run_command(state, command, timeout)
        )
        self._background_created[task_id] = time.monotonic()
        return f"Background task started (id: {task_id}). Use task_id to check status."

    async def execute(
        self,
        command: str,
        timeout: int | None = None,
        description: str | None = None,
        working_dir: str | None = None,
        run_in_background: bool = False,
        **kwargs: Any,
    ) -> str:
        del description, kwargs
        state = self._get_state()

        # Legacy working_dir param: override state cwd for this call
        if working_dir:
            state.cwd = working_dir

        guard_error = self._guard_command(command, state.cwd)
        if guard_error:
            return guard_error

        effective_timeout = self._effective_timeout(timeout)

        if run_in_background:
            return self._start_background_task(state, command, effective_timeout)

        return await self._run_command(state, command, effective_timeout)

    def close(self) -> None:
        """Reset shell state, cancel background tasks, and clean up."""
        for task in self._background_tasks.values():
            task.cancel()
        self._background_tasks.clear()
        self._background_created.clear()
        if self._state is not None:
            self._state.cleanup()
        self._state = None

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort safety guard for potentially destructive commands."""
        cmd = command.strip()
        lower = cmd.lower()
        policy_block = policy_error(f"{cwd}\n{cmd}", kind="Command execution")
        if policy_block:
            return policy_block

        # Check deny patterns against both the original command and the
        # wrapper-stripped version so that ``timeout 10 rm -rf /`` is caught.
        stripped_lower = _strip_wrappers(lower)
        targets = [lower] if stripped_lower == lower else [lower, stripped_lower]

        for target in targets:
            for pattern in self.deny_patterns:
                if re.search(pattern, target):
                    return "Error: Command blocked by safety guard (dangerous pattern detected)"

        if self.allow_patterns and not any(re.search(p, lower) for p in self.allow_patterns):
            return "Error: Command blocked by safety guard (not in allowlist)"

        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()

            win_paths = re.findall(r"[A-Za-z]:\\[^\\\"']+", cmd)
            posix_paths = re.findall(r"(?:^|[\s|>])(/[^\s\"'>]+)", cmd)

            for raw in win_paths + posix_paths:
                try:
                    p = Path(raw.strip()).resolve()
                except Exception:
                    continue
                if p.is_absolute() and cwd_path not in p.parents and p != cwd_path:
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None


class SafeExecTool(ExecTool):
    """ExecTool restricted to a safe command whitelist for the orchestrator agent.

    Allowed commands: git, pytest, uv (run), make, cat, ls, echo, npx.
    All other commands are blocked. Subagents receive unrestricted ExecTool
    via their own role-based tool registration.
    """

    _ORCHESTRATOR_ALLOWLIST: list[str] = [
        r"^git\b",
        r"^pytest\b",
        r"^uv\b",
        r"^make\b",
        r"^cat\b",
        r"^ls\b",
        r"^echo\b",
        r"^npx\b",
    ]

    _CHAINING_DENY: list[str] = [
        r"[;&|]{2}",
        r"(?<!\|)\|(?!\|)",
        r";",
        r"\$\(",
        r"`",
    ]

    def __init__(self, **kwargs: Any) -> None:
        kwargs["allow_patterns"] = self._ORCHESTRATOR_ALLOWLIST
        super().__init__(**kwargs)
        self.deny_patterns = (self.deny_patterns or []) + self._CHAINING_DENY

    @property
    def description(self) -> str:
        return (
            "Execute orchestrator-allowed shell commands: "
            "git, pytest, uv run, make, cat, ls, echo, npx. "
            "For code modifications, use the agent tool to launch an executor instead."
        )
