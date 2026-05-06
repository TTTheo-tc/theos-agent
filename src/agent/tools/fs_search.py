"""Search tools (glob + grep) aligned with Claude Code spec."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from src.agent.tools.base import Tool
from src.agent.tools.tool_security import policy_error
from src.utils.path import resolve_path as _resolve_path

log = logging.getLogger(__name__)


def _join_policy_parts(*parts: str | None) -> str:
    return "\n".join(part for part in parts if part)


def _resolve_search_base(
    raw_path: str | None,
    workspace: Path | None,
    allowed_dir: Path | None,
    *,
    kind: str,
) -> tuple[Path | None, str | None]:
    try:
        base = _resolve_path(raw_path, workspace, allowed_dir) if raw_path else (workspace or Path.cwd())
    except PermissionError as e:
        return None, f"Error: {e}"

    policy_block = policy_error(str(base), kind=kind)
    if policy_block:
        return None, policy_block
    return base, None


def _apply_window(lines: list[str], *, offset: int, head_limit: int) -> tuple[list[str], bool]:
    if offset > 0:
        lines = lines[offset:]
    if head_limit <= 0:
        return lines, False
    return lines[:head_limit], len(lines) > head_limit


def _format_lines(lines: list[str], *, offset: int, head_limit: int) -> str:
    visible, truncated = _apply_window(lines, offset=offset, head_limit=head_limit)
    result = "\n".join(visible)
    if truncated:
        result += f"\n... (truncated at {head_limit} results)"
    return result


def _is_relative_to(path: str, allowed: Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(allowed.resolve())
    except OSError:
        return False


def _rg_output_path(line: str, options: "_GrepOptions") -> str | None:
    if line == "--":
        return None
    if options.output_mode == "files_with_matches":
        return line
    if options.output_mode == "count":
        path, _, count = line.rpartition(":")
        return path if count.isdecimal() else line
    if options.show_line_numbers:
        match = re.match(r"^(?P<path>.*?)(?::|-)\d+(?::|-)", line)
        return match.group("path") if match else line
    path, _, _ = line.partition(":")
    return path or line


def _filter_rg_lines_within_allowed(
    lines: list[str],
    allowed_dir: Path | None,
    options: "_GrepOptions",
) -> list[str]:
    if allowed_dir is None:
        return lines
    allowed = allowed_dir.resolve()
    filtered = [
        line
        for line in lines
        if (path := _rg_output_path(line, options)) is None or _is_relative_to(path, allowed)
    ]
    return _normalize_rg_separators(filtered)


def _normalize_rg_separators(lines: list[str]) -> list[str]:
    normalized: list[str] = []
    for line in lines:
        if line == "--":
            if normalized and normalized[-1] != "--":
                normalized.append(line)
            continue
        normalized.append(line)
    if normalized and normalized[-1] == "--":
        normalized.pop()
    return normalized


class GlobTool(Tool):
    """Find files matching a glob pattern."""

    def __init__(self, workspace: Path | None = None, allowed_dir: Path | None = None):
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return (
            "Find files matching a glob pattern (e.g. '**/*.py', 'src/**/*.ts'). "
            "Returns matching file paths sorted by modification time."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match files",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: workspace root)",
                },
            },
            "required": ["pattern"],
        }

    async def execute(
        self,
        pattern: str,
        path: str | None = None,
        root: str | None = None,
        **kwargs: Any,
    ) -> str:
        # Accept both 'path' (Claude Code) and 'root' (legacy)
        search_path = path or root
        try:
            policy_block = policy_error(_join_policy_parts(search_path, pattern), kind="File glob")
            if policy_block:
                return policy_block
            base, error = _resolve_search_base(
                search_path,
                self._workspace,
                self._allowed_dir,
                kind="File glob",
            )
            if error:
                return error
            assert base is not None
            matches = sorted(base.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            if self._allowed_dir:
                allowed = self._allowed_dir.resolve()
                matches = [p for p in matches if p.resolve().is_relative_to(allowed)]
            if not matches:
                return f"No files found matching '{pattern}'"
            return "\n".join(str(p) for p in matches)
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {str(e)}"


# ---------------------------------------------------------------------------
# ripgrep availability detection (cached)
# ---------------------------------------------------------------------------

_RG_UNCHECKED = object()
_rg_path: str | None | object = _RG_UNCHECKED


def _find_rg() -> str | None:
    """Return the path to the rg binary, or None if unavailable."""
    global _rg_path
    if _rg_path is not _RG_UNCHECKED:
        return _rg_path if isinstance(_rg_path, str) else None

    # 1. shutil.which (covers PATH)
    found = shutil.which("rg")
    if found:
        _rg_path = found
        return found

    # 2. Common install locations
    for candidate in (
        "/opt/homebrew/bin/rg",
        "/usr/local/bin/rg",
        "/usr/bin/rg",
    ):
        if Path(candidate).is_file():
            _rg_path = candidate
            return candidate

    # 3. Ask the shell (handles shell functions / aliases as last resort)
    try:
        result = subprocess.run(
            ["bash", "-lc", "command -v rg"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        path = result.stdout.strip()
        if path and Path(path).is_file():
            _rg_path = path
            return path
    except Exception:
        pass

    _rg_path = None
    return None


# ---------------------------------------------------------------------------
# GrepTool
# ---------------------------------------------------------------------------

_VCS_DIRS = frozenset({".git", ".svn", ".hg", ".bzr"})


@dataclass(frozen=True)
class _GrepOptions:
    pattern: str
    path: str | None
    output_mode: str
    include: str | None
    file_type: str | None
    case_insensitive: bool
    show_line_numbers: bool
    before_ctx: int | None
    after_ctx: int | None
    both_ctx: int | None
    multiline: bool
    head_limit: int
    offset: int

    @classmethod
    def from_kwargs(cls, kwargs: dict[str, Any]) -> "_GrepOptions":
        both_ctx = kwargs.get("-C")
        head_limit = kwargs.get("head_limit")
        max_results = kwargs.get("max_results")
        return cls(
            pattern=kwargs.get("pattern", ""),
            path=kwargs.get("path"),
            output_mode=kwargs.get("output_mode", "content"),
            include=kwargs.get("include") or kwargs.get("glob"),
            file_type=kwargs.get("type"),
            case_insensitive=kwargs.get("-i", False) or kwargs.get("ignore_case", False),
            show_line_numbers=kwargs.get("-n", True),
            before_ctx=kwargs.get("-B"),
            after_ctx=kwargs.get("-A"),
            both_ctx=both_ctx if both_ctx is not None else kwargs.get("context"),
            multiline=kwargs.get("multiline", False),
            head_limit=(
                head_limit if head_limit is not None else max_results if max_results is not None else 250
            ),
            offset=kwargs.get("offset", 0),
        )


class GrepTool(Tool):
    """Search file contents using ripgrep (with Python re fallback)."""

    def __init__(self, workspace: Path | None = None, allowed_dir: Path | None = None):
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Search file contents using a regex pattern. "
            "Uses ripgrep as backend for speed; falls back to Python re if rg is unavailable. "
            "Supports output modes: content (matching lines), files_with_matches (paths only), "
            "count (match counts). Context lines, multiline matching, file-type filters available."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for in file contents.",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search (default: workspace root).",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_with_matches", "count"],
                    "description": (
                        "Output mode. 'content' shows matching lines (default), "
                        "'files_with_matches' shows only file paths, "
                        "'count' shows match counts per file."
                    ),
                },
                "include": {
                    "type": "string",
                    "description": "Glob pattern to filter files (e.g. '*.py'). Alias for 'glob'.",
                },
                "glob": {
                    "type": "string",
                    "description": "Glob pattern to filter files (e.g. '*.py', '*.{ts,tsx}').",
                },
                "type": {
                    "type": "string",
                    "description": "File type filter (maps to rg --type, e.g. 'py', 'js', 'rust').",
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive search. Alias for '-i'.",
                },
                "-i": {
                    "type": "boolean",
                    "description": "Case-insensitive search (default: false).",
                },
                "-n": {
                    "type": "boolean",
                    "description": "Show line numbers (default: true for content mode).",
                },
                "-B": {
                    "type": "integer",
                    "description": "Lines of context before each match.",
                    "minimum": 0,
                },
                "-A": {
                    "type": "integer",
                    "description": "Lines of context after each match.",
                    "minimum": 0,
                },
                "-C": {
                    "type": "integer",
                    "description": "Lines of context before and after each match.",
                    "minimum": 0,
                },
                "context": {
                    "type": "integer",
                    "description": "Alias for '-C'.",
                    "minimum": 0,
                },
                "multiline": {
                    "type": "boolean",
                    "description": "Enable multiline matching (pattern can span lines).",
                },
                "head_limit": {
                    "type": "integer",
                    "description": "Max results to return (default 250, 0 = unlimited).",
                    "minimum": 0,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Alias for head_limit (backward compat).",
                    "minimum": 0,
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip first N results before applying head_limit.",
                    "minimum": 0,
                },
            },
            "required": ["pattern"],
        }

    # -- public interface ----------------------------------------------------

    async def execute(self, **kwargs: Any) -> str:
        options = _GrepOptions.from_kwargs(kwargs)

        if not options.pattern:
            return "Error: pattern is required"

        # -- policy check ---------------------------------------------------
        policy_block = policy_error(
            _join_policy_parts(options.path, options.include),
            kind="File search",
        )
        if policy_block:
            return policy_block
        base, error = _resolve_search_base(
            options.path,
            self._workspace,
            self._allowed_dir,
            kind="File search",
        )
        if error:
            return error
        assert base is not None

        # -- dispatch to rg or fallback -------------------------------------
        rg = _find_rg()
        if rg:
            return await self._run_rg(rg_path=rg, base=base, options=options)

        log.info("ripgrep not found; using Python re fallback")
        return self._run_python_fallback(base=base, options=options)

    # -- ripgrep backend ----------------------------------------------------

    async def _run_rg(
        self,
        *,
        rg_path: str,
        base: Path,
        options: _GrepOptions,
    ) -> str:
        cmd = self._build_rg_command(rg_path=rg_path, base=base, options=options)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            return "Error: ripgrep search timed out after 60 seconds"
        except FileNotFoundError:
            # rg binary disappeared between check and exec; fall back
            log.warning("rg binary not found at %s; falling back to Python re", rg_path)
            return self._run_python_fallback(base=base, options=replace(options, multiline=False))

        # rg exit codes: 0 = matches found, 1 = no matches, 2 = error
        if proc.returncode == 2:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            return f"Error: {stderr_text}"

        raw = stdout_bytes.decode("utf-8", errors="replace")
        if not raw.strip():
            return f"No matches found for '{options.pattern}'"

        lines = raw.rstrip("\n").split("\n")
        lines = _filter_rg_lines_within_allowed(lines, self._allowed_dir, options)
        if not lines:
            return f"No matches found for '{options.pattern}'"

        return _format_lines(lines, offset=options.offset, head_limit=options.head_limit)

    def _build_rg_command(self, *, rg_path: str, base: Path, options: _GrepOptions) -> list[str]:
        cmd: list[str] = [rg_path]
        cmd.append("--with-filename")

        # Output mode
        if options.output_mode == "files_with_matches":
            cmd.append("--files-with-matches")
        elif options.output_mode == "count":
            cmd.append("--count")
        # "content" is default behavior

        # Hidden files (search them) + VCS exclusion
        cmd.append("--hidden")
        for vcs in _VCS_DIRS:
            cmd.extend(["--glob", f"!{vcs}"])

        # Line length limit to suppress base64 / minified noise
        cmd.extend(["--max-columns", "500"])

        # Case sensitivity
        if options.case_insensitive:
            cmd.append("--ignore-case")

        # Line numbers
        if options.output_mode == "content" and options.show_line_numbers:
            cmd.append("--line-number")
        elif options.output_mode == "content" and not options.show_line_numbers:
            cmd.append("--no-line-number")

        # Context lines (only meaningful in content mode)
        if options.output_mode == "content":
            if options.both_ctx is not None:
                cmd.extend(["--context", str(options.both_ctx)])
            else:
                if options.before_ctx is not None:
                    cmd.extend(["--before-context", str(options.before_ctx)])
                if options.after_ctx is not None:
                    cmd.extend(["--after-context", str(options.after_ctx)])

        # Multiline
        if options.multiline:
            cmd.extend(["--multiline", "--multiline-dotall"])

        # File type filter
        if options.file_type:
            cmd.extend(["--type", options.file_type])

        # Glob filter (include is alias for glob)
        if options.include:
            cmd.extend(["--glob", options.include])

        # Pattern (use -e to handle patterns starting with dash)
        cmd.extend(["-e", options.pattern])

        # Search path
        cmd.append(str(base))
        return cmd

    # -- Python re fallback -------------------------------------------------

    def _run_python_fallback(
        self,
        *,
        base: Path,
        options: _GrepOptions,
    ) -> str:
        flags = re.IGNORECASE if options.case_insensitive else 0
        if options.multiline:
            flags |= re.DOTALL
        try:
            regex = re.compile(options.pattern, flags)
        except re.error as e:
            return f"Error: Invalid regex pattern: {e}"

        collected: list[str] = []
        probe_limit = options.offset + options.head_limit if options.head_limit > 0 else None

        def _limit_reached() -> bool:
            return probe_limit is not None and len(collected) > probe_limit

        def _append_match(fp: Path, line_no: int, line: str) -> None:
            entry = (
                f"{fp}:{line_no}: {line.rstrip()}"
                if options.show_line_numbers
                else f"{fp}: {line.rstrip()}"
            )
            collected.append(entry[:500])

        def _search_file(fp: Path) -> None:
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                return

            if options.multiline:
                if regex.search(text):
                    if options.output_mode == "files_with_matches":
                        collected.append(str(fp))
                    elif options.output_mode == "count":
                        collected.append(f"{fp}:{len(regex.findall(text))}")
                    else:
                        for i, line in enumerate(text.splitlines(), 1):
                            if regex.search(line):
                                _append_match(fp, i, line)
                            if _limit_reached():
                                return
                return

            match_count = 0
            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    match_count += 1
                    if options.output_mode == "content":
                        _append_match(fp, i, line)
                        if _limit_reached():
                            return

            if options.output_mode == "files_with_matches" and match_count > 0:
                collected.append(str(fp))
            elif options.output_mode == "count" and match_count > 0:
                collected.append(f"{fp}:{match_count}")
            if _limit_reached():
                return

        def _is_vcs_dir(p: Path) -> bool:
            return any(part in _VCS_DIRS for part in p.parts)

        if base.is_file():
            _search_file(base)
        else:
            glob_pat = f"**/{options.include}" if options.include else "**/*"
            for fp in sorted(base.glob(glob_pat)):
                if fp.is_file() and not _is_vcs_dir(fp):
                    _search_file(fp)
                if _limit_reached():
                    break

        if not collected:
            return f"No matches found for '{options.pattern}'"

        return _format_lines(collected, offset=options.offset, head_limit=options.head_limit)
