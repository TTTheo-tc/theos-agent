"""Read file tool aligned with Claude Code spec."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.agent.tools.base import ContextAwareTool
from src.agent.tools.fs_write import WriteFileTool
from src.agent.tools.tool_security import policy_error
from src.utils.path import resolve_path as _resolve_path

if TYPE_CHECKING:
    from src.agent.tools.context import ToolContext

# Paths that must never be read (device / kernel pseudo-filesystems).
_BLOCKED_PATH_RE = re.compile(r"^/(dev|sys)/|^/proc/\d+/fd/")

# Default size cap – 256 KiB, same order of magnitude as Claude Code.
_DEFAULT_MAX_SIZE = 262_144  # bytes

# How many bytes to inspect for null-byte binary detection.
_BINARY_PROBE_SIZE = 8192

# Extensions that are allowed even if they contain null bytes (handled by
# dedicated tools).
_BINARY_ALLOW_EXTENSIONS = frozenset({".pdf"})


class ReadFileTool(ContextAwareTool):
    """Read file contents with optional offset/limit (line-based)."""

    # Per-session read dedup state, keyed by session_key.
    # Inner dict maps resolved file path → (mtime, offset, limit).
    #
    # The state is class-level (not per-instance) because the registered
    # ReadFileTool is a singleton shared across all sessions, AND because
    # src.agent.loop_memory._build_restoration_context() needs to read it
    # after compaction. The session_key partitioning prevents cross-session
    # leaks: session A's reads cannot suppress session B's reads, and the
    # restoration step only sees the relevant session's history.
    _read_state: dict[str | None, dict[str, tuple[float, int | None, int | None]]] = {}

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        max_size_bytes: int = _DEFAULT_MAX_SIZE,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._max_size_bytes = max_size_bytes

    # -- Tool interface -------------------------------------------------------

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file. Returns lines with line numbers (cat -n format). "
            "Use offset/limit for large files."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to read",
                },
                "offset": {
                    "type": "number",
                    "description": "Line number to start reading from (1-based)",
                },
                "limit": {
                    "type": "number",
                    "description": "Number of lines to read (default: entire file)",
                },
            },
            "required": ["file_path"],
        }

    async def execute(
        self,
        file_path: str | None = None,
        path: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
        _context: "ToolContext | None" = None,
        **kwargs: Any,
    ) -> str:
        session_key = _context.session_key if _context else None
        # Accept both file_path (Claude Code style) and path (legacy)
        target = file_path or path
        if not target:
            return "Error: file_path is required"
        try:
            # --- policy checks (raw + resolved) ---
            raw_policy_error = policy_error(target, kind="File read")
            if raw_policy_error:
                return raw_policy_error

            # --- device / system file blocking ---
            if _BLOCKED_PATH_RE.search(target):
                return f"Error: Cannot read device/system file: {target}"

            fp = _resolve_path(target, self._workspace, self._allowed_dir)

            # Check resolved path against blocked patterns too (handles symlinks).
            resolved_str = str(fp)
            if _BLOCKED_PATH_RE.search(resolved_str):
                return f"Error: Cannot read device/system file: {target}"

            resolved_policy_error = policy_error(resolved_str, kind="File read")
            if resolved_policy_error:
                return resolved_policy_error

            if not fp.exists():
                return f"Error: File not found: {target}"
            if not fp.is_file():
                return f"Error: Not a file: {target}"

            # --- size limit ---
            file_size = fp.stat().st_size
            if file_size > self._max_size_bytes:
                return (
                    f"Error: File is {file_size:,} bytes which exceeds the "
                    f"{self._max_size_bytes:,} byte limit. "
                    f"Use the offset and limit parameters to read a specific range."
                )

            # --- special file type hints (before binary detection) ---
            suffix_lower = fp.suffix.lower()
            if suffix_lower == ".pdf" and offset is None and limit is None:
                return (
                    "This is a PDF file. Use the `pdf` tool to extract text, "
                    "or specify `offset` and `limit` to read raw content."
                )
            image_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
            if suffix_lower in image_extensions:
                return (
                    f"This is an image file ({suffix_lower}). "
                    "Use the `image_analyze` tool to analyze its contents."
                )

            # --- binary detection (first 8 KB) ---
            if fp.suffix.lower() not in _BINARY_ALLOW_EXTENSIONS:
                with open(fp, "rb") as fh:
                    probe = fh.read(_BINARY_PROBE_SIZE)
                if b"\x00" in probe:
                    return (
                        f"Error: {target} appears to be a binary file. "
                        f"Use an appropriate tool for this file type."
                    )

            # --- read dedup (per-session) ---
            mtime = os.path.getmtime(fp)
            cache_key = resolved_str
            session_state = self._read_state.setdefault(session_key, {})
            prev = session_state.get(cache_key)
            if prev is not None:
                prev_mtime, prev_offset, prev_limit = prev
                if prev_mtime == mtime and prev_offset == offset and prev_limit == limit:
                    return (
                        "File unchanged since last read. "
                        "The content from the earlier read is still current."
                    )
            # Record this read.
            session_state[cache_key] = (mtime, offset, limit)
            WriteFileTool.record_read(session_key, resolved_str, mtime)

            # --- read & format ---
            content = fp.read_text(encoding="utf-8")
            lines = content.splitlines()

            # Apply offset/limit
            start = max((offset or 1) - 1, 0)
            end = start + limit if limit else len(lines)
            selected = lines[start:end]

            # Format with line numbers (cat -n style)
            numbered = []
            for i, line in enumerate(selected, start=start + 1):
                numbered.append(f"{i:>6}\t{line}")
            return "\n".join(numbered) if numbered else "(empty file)"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {str(e)}"

    # -- Session management ---------------------------------------------------

    @classmethod
    def clear_read_state(cls, session_key: str | None = None) -> None:
        """Reset the read dedup cache and write-tool read state.

        When *session_key* is given, only that session's state is cleared.
        Otherwise all sessions are cleared.  Call on session reset so that
        subsequent reads return fresh content and write/edit tools require
        re-reading files.
        """
        if session_key is None:
            cls._read_state.clear()
        else:
            cls._read_state.pop(session_key, None)
        WriteFileTool.clear_read_state(session_key)

    @classmethod
    def get_recent_reads(
        cls, session_key: str | None
    ) -> dict[str, tuple[float, int | None, int | None]]:
        """Return the recent-reads dict for *session_key* (empty if none)."""
        return cls._read_state.get(session_key, {})
