"""Write file tools aligned with Claude Code spec."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.agent.tools.base import Tool
from src.agent.tools.tool_security import policy_error
from src.utils.path import resolve_path as _resolve_path

if TYPE_CHECKING:
    from src.agent.tools.context import ToolContext


class WriteFileTool(Tool):
    """Write content to a file, creating parent directories if needed."""

    accepts_context = True

    # Per-session shared read-state, keyed by session_key.
    # Inner dict maps resolved file path → mtime at read time.
    # ReadFileTool calls record_read() after successful reads.
    # EditFileTool calls check_staleness() before edits.
    # Session keying prevents cross-session pollution on the singleton tool.
    _read_files: dict[str | None, dict[str, float]] = {}

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        neuro_symbolic_config: Any = None,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._ns_config = neuro_symbolic_config

    # ---- shared read-state API ----

    @classmethod
    def record_read(cls, session_key: str | None, file_path: str, mtime: float) -> None:
        """Record that *file_path* (resolved, absolute) was read at *mtime*."""
        cls._read_files.setdefault(session_key, {})[file_path] = mtime

    @classmethod
    def check_staleness(cls, session_key: str | None, resolved_path: str) -> str | None:
        """Return an error string if the file was modified since last read, else None."""
        p = Path(resolved_path)
        if not p.exists():
            return None
        session_files = cls._read_files.get(session_key)
        if not session_files or resolved_path not in session_files:
            return None  # staleness only checked when file was previously read
        read_mtime = session_files[resolved_path]
        current_mtime = p.stat().st_mtime
        if current_mtime != read_mtime:
            return (
                f"Error: {resolved_path} was modified since you last read it "
                f"(expected mtime {read_mtime}, current {current_mtime}). "
                "Read it again before writing."
            )
        return None

    @classmethod
    def has_read(cls, session_key: str | None, resolved_path: str) -> bool:
        """Return True if *resolved_path* has been read in *session_key*."""
        session_files = cls._read_files.get(session_key)
        return bool(session_files and resolved_path in session_files)

    @classmethod
    def clear_read_state(cls, session_key: str | None = None) -> None:
        """Clear recorded reads for a session (or all sessions if None)."""
        if session_key is None:
            cls._read_files.clear()
        else:
            cls._read_files.pop(session_key, None)

    # ---- tool metadata ----

    @property
    def risk_level(self) -> str:
        return "medium"

    def assess_risk(self, file_path: str = "", **_: Any) -> str:
        """Assess risk based on target file path."""
        from src.agent.neuro_symbolic import FileRiskController

        ns = self._ns_config
        ctrl = FileRiskController(
            workspace=self._workspace,
            whitelist_patterns=ns.whitelist_patterns if ns else None,
            blacklist_patterns=ns.blacklist_patterns if ns and ns.blacklist_patterns else None,
            enabled=ns.enabled if ns else True,
        )
        return ctrl.assess_operation("write", [file_path] if file_path else [])

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file. Creates parent directories if needed. Overwrites existing files."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to write",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["file_path", "content"],
        }

    async def execute(
        self,
        file_path: str | None = None,
        path: str | None = None,
        content: str = "",
        _context: "ToolContext | None" = None,
        **kwargs: Any,
    ) -> str:
        session_key = _context.session_key if _context else None
        target = file_path or path
        if not target:
            return "Error: file_path is required"
        try:
            raw_policy_error = policy_error(target, kind="File write")
            if raw_policy_error:
                return raw_policy_error
            fp = _resolve_path(target, self._workspace, self._allowed_dir)
            resolved_policy_error = policy_error(str(fp), kind="File write")
            if resolved_policy_error:
                return resolved_policy_error

            resolved = str(fp)

            # --- read-before-write enforcement ---
            if fp.exists() and not self.has_read(session_key, resolved):
                return (
                    f"Error: You must read {target} before overwriting it. "
                    "Use read_file first to see current contents."
                )

            # --- mtime staleness detection ---
            staleness = self.check_staleness(session_key, resolved)
            if staleness:
                return staleness

            # Read old content before writing (for diff)
            is_new = not fp.exists()
            old_content: str | None = ""
            if not is_new:
                try:
                    old_content = fp.read_text(encoding="utf-8")
                except (UnicodeDecodeError, ValueError):
                    old_content = None  # skip diff for non-UTF-8 files

            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")

            # Update read state so subsequent writes don't require re-read.
            self.record_read(session_key, resolved, fp.stat().st_mtime)

            if is_new:
                return f"Created {fp} ({len(content)} bytes)"

            if old_content is None:
                return f"Successfully wrote {fp} ({len(content)} bytes)"

            # Generate unified diff for existing files
            diff_lines = list(
                difflib.unified_diff(
                    old_content.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=str(fp),
                    tofile=str(fp),
                )
            )
            if not diff_lines:
                return f"Successfully wrote {fp} (no changes)"
            diff_text = "".join(diff_lines)
            if len(diff_text) > 2000:
                diff_text = diff_text[:2000] + "\n... (diff truncated)"
            return diff_text
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {str(e)}"


class DocWriteFileTool(WriteFileTool):
    """WriteFileTool restricted to .md files inside a docs/ directory."""

    @property
    def name(self) -> str:
        return "write_docs"

    @property
    def description(self) -> str:
        return (
            "Write content to a Markdown file inside the docs/ directory. "
            "Only .md files under a docs/ folder are permitted."
        )

    async def execute(
        self,
        file_path: str | None = None,
        path: str | None = None,
        content: str = "",
        _context: "ToolContext | None" = None,
        **kwargs: Any,
    ) -> str:
        target = file_path or path
        if not target:
            return "Error: file_path is required"
        p = Path(target)
        if p.suffix != ".md":
            return "Error: write_docs only allows writing .md files"
        try:
            resolved = _resolve_path(target, self._workspace, None)
        except Exception:
            return "Error: write_docs only allows writing files inside a docs/ directory"
        if "docs" not in resolved.parts:
            return "Error: write_docs only allows writing files inside a docs/ directory"
        return await super().execute(file_path=target, content=content, _context=_context, **kwargs)
