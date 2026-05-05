"""Jupyter notebook tools aligned with Claude Code spec: NotebookRead + NotebookEdit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.agent.tools.base import ContextAwareTool
from src.agent.tools.fs_write import WriteFileTool
from src.agent.tools.tool_security import policy_error
from src.utils.path import resolve_path as _resolve_path

if TYPE_CHECKING:
    from src.agent.tools.context import ToolContext


class NotebookReadTool(ContextAwareTool):
    """Read all cells from a Jupyter notebook (.ipynb)."""

    def __init__(self, workspace: Path | None = None, allowed_dir: Path | None = None):
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "notebook_read"

    @property
    def description(self) -> str:
        return (
            "Read a Jupyter notebook (.ipynb) and return all cells with their outputs. "
            "Shows cell number, type (code/markdown), source, and any text outputs."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "notebook_path": {
                    "type": "string",
                    "description": "Absolute path to the .ipynb file",
                },
            },
            "required": ["notebook_path"],
        }

    async def execute(
        self,
        notebook_path: str,
        _context: "ToolContext | None" = None,
        **kwargs: Any,
    ) -> str:
        session_key = _context.session_key if _context else None
        try:
            raw_policy_error = policy_error(notebook_path, kind="Notebook read")
            if raw_policy_error:
                return raw_policy_error
            fp = _resolve_path(notebook_path, self._workspace, self._allowed_dir)
            resolved_policy_error = policy_error(str(fp), kind="Notebook read")
            if resolved_policy_error:
                return resolved_policy_error
            if not fp.exists():
                return f"Error: File not found: {notebook_path}"
            if fp.suffix != ".ipynb":
                return f"Error: Not a notebook file: {notebook_path}"

            nb = json.loads(fp.read_text(encoding="utf-8"))
            cells = nb.get("cells", [])
            if not cells:
                WriteFileTool.record_read(session_key, str(fp), fp.stat().st_mtime)
                return "(empty notebook)"

            parts = []
            for i, cell in enumerate(cells):
                cell_type = cell.get("cell_type", "unknown")
                source = "".join(cell.get("source", []))
                header = f"--- Cell {i} [{cell_type}] ---"
                section = f"{header}\n{source}"

                # Include text outputs for code cells
                outputs = cell.get("outputs", [])
                if outputs:
                    out_parts = []
                    for out in outputs:
                        if out.get("output_type") == "stream":
                            out_parts.append("".join(out.get("text", [])))
                        elif out.get("output_type") in ("execute_result", "display_data"):
                            text_data = out.get("data", {}).get("text/plain", [])
                            if text_data:
                                out_parts.append("".join(text_data))
                        elif out.get("output_type") == "error":
                            tb = "\n".join(out.get("traceback", []))
                            out_parts.append(
                                f"Error: {out.get('ename', '')}: {out.get('evalue', '')}\n{tb}"
                            )
                    if out_parts:
                        section += f"\n\n[Output]\n{''.join(out_parts)}"

                parts.append(section)

            WriteFileTool.record_read(session_key, str(fp), fp.stat().st_mtime)
            return "\n\n".join(parts)
        except PermissionError as e:
            return f"Error: {e}"
        except json.JSONDecodeError:
            return f"Error: Invalid notebook JSON: {notebook_path}"
        except Exception as e:
            return f"Error reading notebook: {str(e)}"


class NotebookEditTool(ContextAwareTool):
    """Edit a specific cell in a Jupyter notebook (.ipynb)."""

    def __init__(self, workspace: Path | None = None, allowed_dir: Path | None = None):
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "notebook_edit"

    @property
    def description(self) -> str:
        return (
            "Replace, insert, or delete a cell in a Jupyter notebook. "
            "Use edit_mode='insert' to add a new cell at the given position. "
            "Use edit_mode='delete' to remove a cell."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "notebook_path": {
                    "type": "string",
                    "description": "Absolute path to the .ipynb file",
                },
                "cell_number": {
                    "type": "number",
                    "description": "0-indexed cell position",
                },
                "new_source": {
                    "type": "string",
                    "description": "New cell source content",
                },
                "cell_type": {
                    "type": "string",
                    "enum": ["code", "markdown"],
                    "description": "Cell type (defaults to current cell's type)",
                },
                "edit_mode": {
                    "type": "string",
                    "enum": ["replace", "insert", "delete"],
                    "description": "Edit operation (default: replace)",
                },
            },
            "required": ["notebook_path", "cell_number", "new_source"],
        }

    async def execute(
        self,
        notebook_path: str,
        cell_number: int = 0,
        new_source: str = "",
        cell_type: str | None = None,
        edit_mode: str = "replace",
        _context: "ToolContext | None" = None,
        **kwargs: Any,
    ) -> str:
        session_key = _context.session_key if _context else None
        try:
            raw_policy_error = policy_error(notebook_path, kind="Notebook edit")
            if raw_policy_error:
                return raw_policy_error
            fp = _resolve_path(notebook_path, self._workspace, self._allowed_dir)
            resolved_policy_error = policy_error(str(fp), kind="Notebook edit")
            if resolved_policy_error:
                return resolved_policy_error
            if not fp.exists():
                return f"Error: File not found: {notebook_path}"
            if fp.suffix != ".ipynb":
                return f"Error: Not a notebook file: {notebook_path}"

            # Enforce read-before-edit: the notebook must have been read first.
            resolved = str(fp)
            if fp.exists() and not WriteFileTool.has_read(session_key, resolved):
                return f"Error: You must read {notebook_path} with notebook_read before editing it."

            nb = json.loads(fp.read_text(encoding="utf-8"))
            cells = nb.get("cells", [])

            if edit_mode == "delete":
                if cell_number < 0 or cell_number >= len(cells):
                    return f"Error: Cell {cell_number} out of range (0-{len(cells) - 1})"
                cells.pop(cell_number)
                nb["cells"] = cells
                fp.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
                WriteFileTool.record_read(session_key, resolved, fp.stat().st_mtime)
                return f"Deleted cell {cell_number} from {fp}"

            if edit_mode == "insert":
                if not cell_type:
                    return "Error: cell_type is required for insert mode"
                new_cell = self._make_cell(cell_type, new_source)
                if cell_number > len(cells):
                    cell_number = len(cells)
                cells.insert(cell_number, new_cell)
                nb["cells"] = cells
                fp.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
                WriteFileTool.record_read(session_key, resolved, fp.stat().st_mtime)
                return f"Inserted {cell_type} cell at position {cell_number} in {fp}"

            # Replace mode
            if cell_number < 0 or cell_number >= len(cells):
                return f"Error: Cell {cell_number} out of range (0-{len(cells) - 1})"

            target_cell = cells[cell_number]
            if cell_type:
                target_cell["cell_type"] = cell_type
            target_cell["source"] = new_source.splitlines(keepends=True)
            # Clear outputs when replacing code cells
            if target_cell.get("cell_type") == "code":
                target_cell["outputs"] = []
                target_cell["execution_count"] = None

            fp.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            WriteFileTool.record_read(session_key, resolved, fp.stat().st_mtime)
            return f"Replaced cell {cell_number} in {fp}"

        except PermissionError as e:
            return f"Error: {e}"
        except json.JSONDecodeError:
            return f"Error: Invalid notebook JSON: {notebook_path}"
        except Exception as e:
            return f"Error editing notebook: {str(e)}"

    @staticmethod
    def _make_cell(cell_type: str, source: str) -> dict:
        """Create a new notebook cell dict."""
        cell = {
            "cell_type": cell_type,
            "metadata": {},
            "source": source.splitlines(keepends=True),
        }
        if cell_type == "code":
            cell["execution_count"] = None
            cell["outputs"] = []
        return cell
