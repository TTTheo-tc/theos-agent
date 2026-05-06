"""Read file tool aligned with Claude Code spec."""

from __future__ import annotations

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
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})


def _line_window(offset: int | None, limit: int | None) -> tuple[int, int | None]:
    start = max((offset or 1) - 1, 0)
    end = start + limit if limit else None
    return start, end


def _truncate_text(text: str, max_bytes: int | None) -> tuple[str, bool]:
    if max_bytes is None:
        return text, False
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _format_numbered_lines(
    lines: list[str],
    *,
    start_line: int,
    truncated: bool = False,
    max_output_bytes: int | None = None,
) -> str:
    if not lines:
        return "(empty file)"
    result = "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(lines, start=start_line))
    result, output_truncated = _truncate_text(result, max_output_bytes)
    truncated = truncated or output_truncated
    if truncated:
        result += "\n... (truncated at read byte limit)"
    return result


def _read_selected_lines(
    fp: Path,
    *,
    offset: int | None,
    limit: int | None,
    stream: bool,
    max_output_bytes: int | None = None,
) -> tuple[list[str], int, bool]:
    start, end = _line_window(offset, limit)
    if not stream:
        lines = fp.read_text(encoding="utf-8").splitlines()
        return lines[start:end], start + 1, False

    selected: list[str] = []
    total_bytes = 0
    truncated = False
    with fp.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if line_no <= start:
                continue
            if end is not None and line_no > end:
                break
            clean_line = line.rstrip("\r\n")
            line_bytes = len(clean_line.encode("utf-8", errors="replace"))
            if max_output_bytes is not None and total_bytes + line_bytes > max_output_bytes:
                remaining = max(max_output_bytes - total_bytes, 0)
                if remaining > 0:
                    selected.append(clean_line.encode("utf-8")[:remaining].decode("utf-8", "ignore"))
                truncated = True
                break
            selected.append(clean_line)
            total_bytes += line_bytes
    return selected, start + 1, truncated


def _size_limit_error(file_size: int, max_size_bytes: int) -> str:
    return (
        f"Error: File is {file_size:,} bytes which exceeds the "
        f"{max_size_bytes:,} byte limit. "
        f"Use the offset and limit parameters to read a specific range."
    )


def _special_file_hint(fp: Path, offset: int | None, limit: int | None) -> str | None:
    suffix = fp.suffix.lower()
    if suffix == ".pdf" and offset is None and limit is None:
        return (
            "This is a PDF file. Use the `pdf` tool to extract text, "
            "or specify `offset` and `limit` to read raw content."
        )
    if suffix in _IMAGE_EXTENSIONS:
        return f"This is an image file ({suffix}). Use the `image_analyze` tool to analyze its contents."
    return None


def _appears_binary(fp: Path) -> bool:
    if fp.suffix.lower() in _BINARY_ALLOW_EXTENSIONS:
        return False
    with open(fp, "rb") as fh:
        return b"\x00" in fh.read(_BINARY_PROBE_SIZE)


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
        target = file_path or path
        if not target:
            return "Error: file_path is required"
        try:
            fp, error = self._resolve_file(target)
            if error:
                return error
            assert fp is not None
            stat = fp.stat()

            stream_range, size_error = self._stream_range_or_error(stat.st_size, limit)
            if size_error:
                return size_error

            resolved_str = str(fp)
            if preflight_error := self._read_preflight_error(
                fp,
                target=target,
                session_key=session_key,
                resolved_path=resolved_str,
                mtime=stat.st_mtime,
                offset=offset,
                limit=limit,
            ):
                return preflight_error

            selected, start_line, truncated = _read_selected_lines(
                fp,
                offset=offset,
                limit=limit,
                stream=stream_range,
                max_output_bytes=self._max_size_bytes if stream_range else None,
            )
            self._record_successful_read(
                session_key,
                resolved_str,
                stat.st_mtime,
                offset,
                limit,
                mark_writable=not stream_range,
            )
            return _format_numbered_lines(
                selected,
                start_line=start_line,
                truncated=truncated,
                max_output_bytes=self._max_size_bytes if stream_range else None,
            )
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {str(e)}"

    def _read_preflight_error(
        self,
        fp: Path,
        *,
        target: str,
        session_key: str | None,
        resolved_path: str,
        mtime: float,
        offset: int | None,
        limit: int | None,
    ) -> str | None:
        if file_hint := _special_file_hint(fp, offset, limit):
            return file_hint

        if _appears_binary(fp):
            return (
                f"Error: {target} appears to be a binary file. "
                f"Use an appropriate tool for this file type."
            )

        if self._is_duplicate_read(session_key, resolved_path, mtime, offset, limit):
            return (
                "File unchanged since last read. "
                "The content from the earlier read is still current."
            )
        return None

    def _stream_range_or_error(self, file_size: int, limit: int | None) -> tuple[bool, str | None]:
        if file_size <= self._max_size_bytes:
            return False, None
        if not limit or limit < 0:
            return False, _size_limit_error(file_size, self._max_size_bytes)
        return True, None

    def _resolve_file(self, target: str) -> tuple[Path | None, str | None]:
        raw_policy_error = policy_error(target, kind="File read")
        if raw_policy_error:
            return None, raw_policy_error
        if _BLOCKED_PATH_RE.search(target):
            return None, f"Error: Cannot read device/system file: {target}"

        try:
            fp = _resolve_path(target, self._workspace, self._allowed_dir)
        except PermissionError as e:
            return None, f"Error: {e}"
        resolved_str = str(fp)
        if _BLOCKED_PATH_RE.search(resolved_str):
            return None, f"Error: Cannot read device/system file: {target}"

        resolved_policy_error = policy_error(resolved_str, kind="File read")
        if resolved_policy_error:
            return None, resolved_policy_error
        if not fp.exists():
            return None, f"Error: File not found: {target}"
        if not fp.is_file():
            return None, f"Error: Not a file: {target}"
        return fp, None

    @classmethod
    def _is_duplicate_read(
        cls,
        session_key: str | None,
        resolved_path: str,
        mtime: float,
        offset: int | None,
        limit: int | None,
    ) -> bool:
        prev = cls._read_state.setdefault(session_key, {}).get(resolved_path)
        return prev == (mtime, offset, limit)

    @classmethod
    def _record_successful_read(
        cls,
        session_key: str | None,
        resolved_path: str,
        mtime: float,
        offset: int | None,
        limit: int | None,
        *,
        mark_writable: bool = True,
    ) -> None:
        cls._read_state.setdefault(session_key, {})[resolved_path] = (mtime, offset, limit)
        if mark_writable:
            WriteFileTool.record_read(session_key, resolved_path, mtime)

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
