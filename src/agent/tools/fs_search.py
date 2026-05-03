"""Search tools (glob + grep) aligned with Claude Code spec."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from src.agent.tools.base import Tool
from src.agent.tools.tool_security import policy_error
from src.utils.path import resolve_path as _resolve_path

log = logging.getLogger(__name__)


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
            policy_input = "\n".join(part for part in [search_path or "", pattern] if part)
            policy_block = policy_error(policy_input, kind="File glob")
            if policy_block:
                return policy_block
            base = (
                _resolve_path(search_path, self._workspace, self._allowed_dir)
                if search_path
                else (self._workspace or Path.cwd())
            )
            resolved_policy_error = policy_error(str(base), kind="File glob")
            if resolved_policy_error:
                return resolved_policy_error
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

_rg_path: str | None | bool = False  # False = not yet checked


def _find_rg() -> str | None:
    """Return the path to the rg binary, or None if unavailable."""
    global _rg_path
    if _rg_path is not False:
        return _rg_path  # type: ignore[return-value]

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
        pattern: str = kwargs.get("pattern", "")
        path: str | None = kwargs.get("path")
        output_mode: str = kwargs.get("output_mode", "content")
        include: str | None = kwargs.get("include") or kwargs.get("glob")
        file_type: str | None = kwargs.get("type")
        case_insensitive: bool = kwargs.get("-i", False) or kwargs.get("ignore_case", False)
        show_line_numbers: bool = kwargs.get("-n", True)
        before_ctx: int | None = kwargs.get("-B")
        after_ctx: int | None = kwargs.get("-A")
        _c = kwargs.get("-C")
        both_ctx: int | None = _c if _c is not None else kwargs.get("context")
        multiline: bool = kwargs.get("multiline", False)
        # head_limit: 0 means unlimited, None means not provided -> use default 250
        _hl = kwargs.get("head_limit")
        _mr = kwargs.get("max_results")
        head_limit: int = _hl if _hl is not None else (_mr if _mr is not None else 250)
        offset: int = kwargs.get("offset", 0)

        if not pattern:
            return "Error: pattern is required"

        # -- policy check ---------------------------------------------------
        try:
            policy_input = "\n".join(part for part in [path or "", include or ""] if part)
            policy_block = policy_error(policy_input, kind="File search")
            if policy_block:
                return policy_block

            base = (
                _resolve_path(path, self._workspace, self._allowed_dir)
                if path
                else (self._workspace or Path.cwd())
            )
            resolved_policy_err = policy_error(str(base), kind="File search")
            if resolved_policy_err:
                return resolved_policy_err
        except PermissionError as e:
            return f"Error: {e}"

        # -- dispatch to rg or fallback -------------------------------------
        rg = _find_rg()
        if rg:
            return await self._run_rg(
                rg_path=rg,
                pattern=pattern,
                base=base,
                output_mode=output_mode,
                include=include,
                file_type=file_type,
                case_insensitive=case_insensitive,
                show_line_numbers=show_line_numbers,
                before_ctx=before_ctx,
                after_ctx=after_ctx,
                both_ctx=both_ctx,
                multiline=multiline,
                head_limit=head_limit,
                offset=offset,
            )
        else:
            log.info("ripgrep not found; using Python re fallback")
            return self._run_python_fallback(
                pattern=pattern,
                base=base,
                output_mode=output_mode,
                include=include,
                case_insensitive=case_insensitive,
                show_line_numbers=show_line_numbers,
                multiline=multiline,
                head_limit=head_limit,
                offset=offset,
            )

    # -- ripgrep backend ----------------------------------------------------

    async def _run_rg(
        self,
        *,
        rg_path: str,
        pattern: str,
        base: Path,
        output_mode: str,
        include: str | None,
        file_type: str | None,
        case_insensitive: bool,
        show_line_numbers: bool,
        before_ctx: int | None,
        after_ctx: int | None,
        both_ctx: int | None,
        multiline: bool,
        head_limit: int,
        offset: int,
    ) -> str:
        cmd: list[str] = [rg_path]

        # Output mode
        if output_mode == "files_with_matches":
            cmd.append("--files-with-matches")
        elif output_mode == "count":
            cmd.append("--count")
        # "content" is default behavior

        # Hidden files (search them) + VCS exclusion
        cmd.append("--hidden")
        for vcs in _VCS_DIRS:
            cmd.extend(["--glob", f"!{vcs}"])

        # Line length limit to suppress base64 / minified noise
        cmd.extend(["--max-columns", "500"])

        # Case sensitivity
        if case_insensitive:
            cmd.append("--ignore-case")

        # Line numbers
        if output_mode == "content" and show_line_numbers:
            cmd.append("--line-number")
        elif output_mode == "content" and not show_line_numbers:
            cmd.append("--no-line-number")

        # Context lines (only meaningful in content mode)
        if output_mode == "content":
            if both_ctx is not None:
                cmd.extend(["--context", str(both_ctx)])
            else:
                if before_ctx is not None:
                    cmd.extend(["--before-context", str(before_ctx)])
                if after_ctx is not None:
                    cmd.extend(["--after-context", str(after_ctx)])

        # Multiline
        if multiline:
            cmd.extend(["--multiline", "--multiline-dotall"])

        # File type filter
        if file_type:
            cmd.extend(["--type", file_type])

        # Glob filter (include is alias for glob)
        if include:
            cmd.extend(["--glob", include])

        # Pattern (use -e to handle patterns starting with dash)
        cmd.extend(["-e", pattern])

        # Search path
        cmd.append(str(base))

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
            return self._run_python_fallback(
                pattern=pattern,
                base=base,
                output_mode=output_mode,
                include=include,
                case_insensitive=case_insensitive,
                show_line_numbers=show_line_numbers,
                multiline=False,
                head_limit=head_limit,
                offset=offset,
            )

        # rg exit codes: 0 = matches found, 1 = no matches, 2 = error
        if proc.returncode == 2:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            return f"Error: {stderr_text}"

        raw = stdout_bytes.decode("utf-8", errors="replace")
        if not raw.strip():
            return f"No matches found for '{pattern}'"

        # Apply offset and head_limit
        lines = raw.rstrip("\n").split("\n")
        if offset > 0:
            lines = lines[offset:]
        if head_limit > 0:
            truncated = len(lines) > head_limit
            lines = lines[:head_limit]
        else:
            truncated = False

        # Enforce allowed_dir on output paths
        if self._allowed_dir:
            allowed = str(self._allowed_dir.resolve())
            lines = [ln for ln in lines if ln.startswith(allowed) or ln.startswith("--")]

        result = "\n".join(lines)
        if truncated:
            result += f"\n... (truncated at {head_limit} results)"
        return result

    # -- Python re fallback -------------------------------------------------

    def _run_python_fallback(
        self,
        *,
        pattern: str,
        base: Path,
        output_mode: str,
        include: str | None,
        case_insensitive: bool,
        show_line_numbers: bool,
        multiline: bool,
        head_limit: int,
        offset: int,
    ) -> str:
        flags = re.IGNORECASE if case_insensitive else 0
        if multiline:
            flags |= re.DOTALL
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return f"Error: Invalid regex pattern: {e}"

        collected: list[str] = []

        def _search_file(fp: Path) -> bool:
            """Search a single file. Returns True to signal 'stop early'."""
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                return False

            if multiline:
                if regex.search(text):
                    if output_mode == "files_with_matches":
                        collected.append(str(fp))
                    elif output_mode == "count":
                        collected.append(f"{fp}:{len(regex.findall(text))}")
                    else:
                        for i, line in enumerate(text.splitlines(), 1):
                            if regex.search(line):
                                entry = (
                                    f"{fp}:{i}: {line.rstrip()}"
                                    if show_line_numbers
                                    else f"{fp}: {line.rstrip()}"
                                )
                                collected.append(entry[:500])
                return False

            match_count = 0
            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    match_count += 1
                    if output_mode == "content":
                        entry = (
                            f"{fp}:{i}: {line.rstrip()}"
                            if show_line_numbers
                            else f"{fp}: {line.rstrip()}"
                        )
                        collected.append(entry[:500])

            if output_mode == "files_with_matches" and match_count > 0:
                collected.append(str(fp))
            elif output_mode == "count" and match_count > 0:
                collected.append(f"{fp}:{match_count}")

            return False

        def _is_vcs_dir(p: Path) -> bool:
            return any(part in _VCS_DIRS for part in p.parts)

        if base.is_file():
            _search_file(base)
        else:
            glob_pat = f"**/{include}" if include else "**/*"
            for fp in sorted(base.glob(glob_pat)):
                if fp.is_file() and not _is_vcs_dir(fp):
                    _search_file(fp)

        if not collected:
            return f"No matches found for '{pattern}'"

        # Apply offset and head_limit
        if offset > 0:
            collected = collected[offset:]
        if head_limit > 0:
            truncated = len(collected) > head_limit
            collected = collected[:head_limit]
        else:
            truncated = False

        result = "\n".join(collected)
        if truncated:
            result += f"\n... (truncated at {head_limit} results)"
        return result
