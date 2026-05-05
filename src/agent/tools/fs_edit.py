"""Edit file tools aligned with Claude Code spec."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.agent.tools.base import ContextAwareTool
from src.agent.tools.fs_write import WriteFileTool
from src.agent.tools.tool_security import policy_error
from src.utils.path import resolve_path as _resolve_path

if TYPE_CHECKING:
    from src.agent.tools.context import ToolContext


class EditFileTool(ContextAwareTool):
    """Edit a file by replacing old_string with new_string."""

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        neuro_symbolic_config: Any = None,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._ns_config = neuro_symbolic_config

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
        return ctrl.assess_operation("edit", [file_path] if file_path else [])

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing old_string with new_string. "
            "The old_string must exist exactly in the file. "
            "Use replace_all=true to replace all occurrences."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to modify",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact text to find and replace",
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement text (must differ from old_string)",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default: false)",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    async def execute(
        self,
        file_path: str | None = None,
        path: str | None = None,
        old_string: str | None = None,
        new_string: str | None = None,
        old_text: str | None = None,
        new_text: str | None = None,
        replace_all: bool = False,
        _context: "ToolContext | None" = None,
        **kwargs: Any,
    ) -> str:
        session_key = _context.session_key if _context else None
        # Accept both Claude Code style (old_string) and legacy (old_text)
        target = file_path or path
        old = old_string if old_string is not None else old_text
        new = new_string if new_string is not None else new_text
        if not target:
            return "Error: file_path is required"
        if old is None or new is None:
            return "Error: old_string and new_string are required"
        try:
            raw_policy_error = policy_error(target, kind="File edit")
            if raw_policy_error:
                return raw_policy_error
            fp = _resolve_path(target, self._workspace, self._allowed_dir)
            resolved_policy_error = policy_error(str(fp), kind="File edit")
            if resolved_policy_error:
                return resolved_policy_error
            if not fp.exists():
                return f"Error: File not found: {target}"

            # --- mtime staleness detection ---
            staleness = WriteFileTool.check_staleness(session_key, str(fp))
            if staleness:
                return staleness

            # --- encoding detection ---
            try:
                from charset_normalizer import from_bytes

                raw = fp.read_bytes()
                result = from_bytes(raw).best()
                encoding = result.encoding if result else "utf-8"
                content = str(result) if result else raw.decode("utf-8")
            except ImportError:
                encoding = "utf-8"
                content = fp.read_text(encoding="utf-8")

            if old not in content:
                return self._not_found_message(old, content, target)

            count = content.count(old)
            if not replace_all and count > 1:
                return (
                    f"Warning: old_string appears {count} times. "
                    "Use replace_all=true or provide more context to make it unique."
                )

            if replace_all:
                new_content = content.replace(old, new)
            else:
                new_content = content.replace(old, new, 1)

            fp.write_text(new_content, encoding=encoding)

            # Update read state so subsequent edits/writes see current mtime.
            WriteFileTool.record_read(session_key, str(fp), fp.stat().st_mtime)

            replaced = count if replace_all else 1
            return f"Successfully edited {fp} ({replaced} replacement{'s' if replaced > 1 else ''})"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error editing file: {str(e)}"

    @staticmethod
    def _not_found_message(old_text: str, content: str, path: str) -> str:
        """Build a helpful error when old_text is not found."""
        lines = content.splitlines(keepends=True)
        old_lines = old_text.splitlines(keepends=True)
        window = len(old_lines)

        best_ratio, best_start = 0.0, 0
        for i in range(max(1, len(lines) - window + 1)):
            ratio = difflib.SequenceMatcher(None, old_lines, lines[i : i + window]).ratio()
            if ratio > best_ratio:
                best_ratio, best_start = ratio, i

        if best_ratio > 0.5:
            diff = "\n".join(
                difflib.unified_diff(
                    old_lines,
                    lines[best_start : best_start + window],
                    fromfile="old_string (provided)",
                    tofile=f"{path} (actual, line {best_start + 1})",
                    lineterm="",
                )
            )
            return f"Error: old_string not found in {path}.\nBest match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
        return f"Error: old_string not found in {path}. No similar text found."


class MultiEditTool(ContextAwareTool):
    """Apply multiple edits to a file in one atomic call."""

    def __init__(self, workspace: Path | None = None, allowed_dir: Path | None = None):
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "multi_edit"

    @property
    def description(self) -> str:
        return (
            "Apply multiple find-and-replace edits to a single file atomically. "
            "Edits are applied in order. Use replace_all per edit if needed."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to modify",
                },
                "edits": {
                    "type": "array",
                    "description": "List of edits to apply in order",
                    "items": {
                        "type": "object",
                        "properties": {
                            "old_string": {
                                "type": "string",
                                "description": "Exact text to find",
                            },
                            "new_string": {
                                "type": "string",
                                "description": "Text to replace with",
                            },
                            "replace_all": {
                                "type": "boolean",
                                "description": "Replace all occurrences (default: false)",
                            },
                        },
                        "required": ["old_string", "new_string"],
                    },
                },
            },
            "required": ["file_path", "edits"],
        }

    async def execute(
        self,
        file_path: str | None = None,
        path: str | None = None,
        edits: list[dict] | None = None,
        _context: "ToolContext | None" = None,
        **kwargs: Any,
    ) -> str:
        session_key = _context.session_key if _context else None
        target = file_path or path
        if not target:
            return "Error: file_path is required"
        if not edits:
            return "Error: edits list is required"
        try:
            raw_policy_error = policy_error(target, kind="File edit")
            if raw_policy_error:
                return raw_policy_error
            fp = _resolve_path(target, self._workspace, self._allowed_dir)
            resolved_policy_error = policy_error(str(fp), kind="File edit")
            if resolved_policy_error:
                return resolved_policy_error
            if not fp.exists():
                return f"Error: File not found: {target}"

            # --- mtime staleness detection ---
            staleness = WriteFileTool.check_staleness(session_key, str(fp))
            if staleness:
                return staleness

            # --- encoding detection ---
            try:
                from charset_normalizer import from_bytes

                raw = fp.read_bytes()
                result = from_bytes(raw).best()
                encoding = result.encoding if result else "utf-8"
                content = str(result) if result else raw.decode("utf-8")
            except ImportError:
                encoding = "utf-8"
                content = fp.read_text(encoding="utf-8")

            applied = 0

            for i, edit in enumerate(edits):
                old = edit.get("old_string") or edit.get("old_text", "")
                new = edit.get("new_string") or edit.get("new_text", "")
                do_all = edit.get("replace_all", False)

                if old not in content:
                    return f"Error: edit[{i}] old_string not found (after {applied} edits applied). Aborting."
                count = content.count(old)
                if not do_all and count > 1:
                    return f"Error: edit[{i}] old_string appears {count} times — use replace_all or make unique. Aborting."
                if do_all:
                    content = content.replace(old, new)
                else:
                    content = content.replace(old, new, 1)
                applied += 1

            fp.write_text(content, encoding=encoding)

            # Update read state so subsequent edits/writes see current mtime.
            WriteFileTool.record_read(session_key, str(fp), fp.stat().st_mtime)

            return f"Successfully applied {applied} edit(s) to {fp}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {str(e)}"
