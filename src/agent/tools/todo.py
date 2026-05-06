"""Todo tool: lightweight task tracking via workspace/TODO.md."""

from pathlib import Path
from typing import Any

from src.agent.tools.base import Tool
from src.agent.tools.tasks import TaskCreateTool, TaskGetTool, TaskListTool, TaskUpdateTool

_TODO_FILE = "TODO.md"
__all__ = [
    "TaskCreateTool",
    "TaskGetTool",
    "TaskListTool",
    "TaskUpdateTool",
    "TodoTool",
]


class TodoTool(Tool):
    """Tool to read and write a TODO task list."""

    def __init__(self, workspace: Path):
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "todo"

    @property
    def description(self) -> str:
        return (
            "Manage a TODO task list stored in workspace/TODO.md. "
            "Actions: 'read' (show all tasks), 'write' (replace entire list), "
            "'add' (append a task), 'done' (mark task done by index, 1-based)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write", "add", "done"],
                    "description": "Action to perform",
                },
                "content": {
                    "type": "string",
                    "description": "For 'write': full markdown content. For 'add': task description.",
                },
                "index": {
                    "type": "integer",
                    "description": "For 'done': 1-based task index to mark as completed.",
                    "minimum": 1,
                },
            },
            "required": ["action"],
        }

    def _todo_path(self) -> Path:
        return self._workspace / _TODO_FILE

    async def execute(
        self, action: str, content: str | None = None, index: int | None = None, **kwargs: Any
    ) -> str:
        del kwargs
        todo_path = self._todo_path()

        if action == "read":
            if not todo_path.exists():
                return "TODO list is empty."
            return todo_path.read_text(encoding="utf-8")

        if action == "write":
            if not content:
                return "Error: 'content' is required for write action."
            todo_path.write_text(content, encoding="utf-8")
            return f"TODO list updated ({len(content)} bytes)."

        if action == "add":
            if not content:
                return "Error: 'content' is required for add action."
            existing = todo_path.read_text(encoding="utf-8") if todo_path.exists() else ""
            if not existing.strip():
                existing = "# TODO\n\n"
            line = f"- [ ] {content.strip()}\n"
            todo_path.write_text(existing.rstrip("\n") + "\n" + line, encoding="utf-8")
            return f"Added task: {content.strip()}"

        if action == "done":
            if index is None:
                return "Error: 'index' is required for done action."
            if not todo_path.exists():
                return "Error: TODO list is empty."
            lines = todo_path.read_text(encoding="utf-8").splitlines(keepends=True)
            task_lines = [(i, ln) for i, ln in enumerate(lines) if ln.lstrip().startswith("- [ ]")]
            if index < 1 or index > len(task_lines):
                return f"Error: index {index} out of range (1–{len(task_lines)})."
            line_idx, ln = task_lines[index - 1]
            lines[line_idx] = ln.replace("- [ ]", "- [x]", 1)
            todo_path.write_text("".join(lines), encoding="utf-8")
            return f"Marked task {index} as done."

        return f"Error: Unknown action '{action}'. Use: read, write, add, done."
