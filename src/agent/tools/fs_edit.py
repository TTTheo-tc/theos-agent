"""Edit file tools aligned with Claude Code spec."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.agent.tools.base import ContextAwareTool
from src.agent.tools.fs_write import WriteFileTool
from src.agent.tools.tool_security import resolve_policy_path

if TYPE_CHECKING:
    from src.agent.tools.context import ToolContext


def _resolve_edit_path(
    target: str,
    workspace: Path | None,
    allowed_dir: Path | None,
) -> tuple[Path | None, str | None]:
    fp, error = resolve_policy_path(target, workspace, allowed_dir, kind="File edit")
    if error:
        return None, error
    assert fp is not None
    if not fp.exists():
        return None, f"Error: File not found: {target}"
    return fp, None


def _read_text_with_encoding(fp: Path) -> tuple[str, str]:
    try:
        from charset_normalizer import from_bytes

        raw = fp.read_bytes()
        result = from_bytes(raw).best()
        encoding = result.encoding if result else "utf-8"
        content = str(result) if result else raw.decode("utf-8")
        return content, encoding
    except ImportError:
        return fp.read_text(encoding="utf-8"), "utf-8"


def _load_edit_target(
    target: str,
    workspace: Path | None,
    allowed_dir: Path | None,
    session_key: str | None,
) -> tuple[Path | None, str, str, str | None]:
    fp, error = _resolve_edit_path(target, workspace, allowed_dir)
    if error:
        return None, "", "", error
    assert fp is not None

    staleness = WriteFileTool.check_staleness(session_key, str(fp))
    if staleness:
        return None, "", "", staleness

    content, encoding = _read_text_with_encoding(fp)
    return fp, content, encoding, None


def _write_edit_result(fp: Path, content: str, encoding: str, session_key: str | None) -> None:
    fp.write_text(content, encoding=encoding)
    WriteFileTool.record_read(session_key, str(fp), fp.stat().st_mtime)


def _replace_content(
    content: str,
    old: str,
    new: str,
    *,
    replace_all: bool,
) -> tuple[str, int]:
    if replace_all:
        return content.replace(old, new), content.count(old)
    return content.replace(old, new, 1), 1


def _edit_values(edit: dict) -> tuple[str, str, bool]:
    old = edit.get("old_string") or edit.get("old_text", "")
    new = edit.get("new_string") or edit.get("new_text", "")
    return old, new, edit.get("replace_all", False)


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

        ctrl = FileRiskController.from_config(workspace=self._workspace, config=self._ns_config)
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
        _context: ToolContext | None = None,
        **kwargs: Any,
    ) -> str:
        del kwargs
        session_key = _context.session_key if _context else None
        target = file_path or path
        old = old_string if old_string is not None else old_text
        new = new_string if new_string is not None else new_text
        if not target:
            return "Error: file_path is required"
        if old is None or new is None:
            return "Error: old_string and new_string are required"
        try:
            fp, content, encoding, error = _load_edit_target(
                target,
                self._workspace,
                self._allowed_dir,
                session_key,
            )
            if error:
                return error
            assert fp is not None

            if old not in content:
                return self._not_found_message(old, content, target)

            count = content.count(old)
            if not replace_all and count > 1:
                return (
                    f"Warning: old_string appears {count} times. "
                    "Use replace_all=true or provide more context to make it unique."
                )

            new_content, replaced = _replace_content(content, old, new, replace_all=replace_all)
            _write_edit_result(fp, new_content, encoding, session_key)
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
        _context: ToolContext | None = None,
        **kwargs: Any,
    ) -> str:
        del kwargs
        session_key = _context.session_key if _context else None
        target = file_path or path
        if not target:
            return "Error: file_path is required"
        if not edits:
            return "Error: edits list is required"
        try:
            fp, content, encoding, error = _load_edit_target(
                target,
                self._workspace,
                self._allowed_dir,
                session_key,
            )
            if error:
                return error
            assert fp is not None

            applied = 0

            for i, edit in enumerate(edits):
                old, new, replace_all = _edit_values(edit)

                if old not in content:
                    return f"Error: edit[{i}] old_string not found (after {applied} edits applied). Aborting."
                count = content.count(old)
                if not replace_all and count > 1:
                    return f"Error: edit[{i}] old_string appears {count} times — use replace_all or make unique. Aborting."
                content, _ = _replace_content(content, old, new, replace_all=replace_all)
                applied += 1

            _write_edit_result(fp, content, encoding, session_key)
            return f"Successfully applied {applied} edit(s) to {fp}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {str(e)}"
