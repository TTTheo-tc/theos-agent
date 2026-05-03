"""Todo tool: lightweight task tracking via workspace/TODO.md.

Also provides a full task management system (TaskCreate, TaskList, TaskUpdate, TaskGet)
with JSON-based storage at workspace/tasks.json.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.agent.tools.base import Tool

_TODO_FILE = "TODO.md"
_TASKS_FILE = "tasks.json"


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


# ---------------------------------------------------------------------------
# Task management system (JSON-based)
# ---------------------------------------------------------------------------


def _tasks_path(workspace: Path) -> Path:
    return workspace / _TASKS_FILE


def _load_tasks(workspace: Path) -> list[dict[str, Any]]:
    fp = _tasks_path(workspace)
    if not fp.exists():
        return []
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_tasks(workspace: Path, tasks: list[dict[str, Any]]) -> None:
    fp = _tasks_path(workspace)
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_suffix(".tmp")
    tmp.write_text(json.dumps(tasks, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, fp)


class TaskCreateTool(Tool):
    """Create a new task with subject and description."""

    def __init__(self, workspace: Path):
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "task_create"

    @property
    def description(self) -> str:
        return "Create a new task. Returns the task id and subject."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "Short task title",
                },
                "description": {
                    "type": "string",
                    "description": "Detailed task description",
                },
                "metadata": {
                    "type": "object",
                    "description": "Optional key-value metadata",
                },
            },
            "required": ["subject", "description"],
        }

    async def execute(
        self,
        subject: str = "",
        description: str = "",
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        if not subject:
            return "Error: subject is required"
        if not description:
            return "Error: description is required"

        task_id = uuid.uuid4().hex[:12]
        task = {
            "id": task_id,
            "subject": subject,
            "description": description,
            "status": "pending",
            "blocks": [],
            "blocked_by": [],
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        tasks = _load_tasks(self._workspace)
        tasks.append(task)
        _save_tasks(self._workspace, tasks)

        return json.dumps({"id": task_id, "subject": subject})


class TaskListTool(Tool):
    """List all tasks with their status and pending blockers."""

    def __init__(self, workspace: Path):
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "task_list"

    @property
    def description(self) -> str:
        return "List all tasks with id, subject, status, and pending blockers."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    @property
    def parallel_safe(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        tasks = _load_tasks(self._workspace)
        if not tasks:
            return "No tasks."

        # Build set of completed task ids for filtering blockers
        completed_ids = {t["id"] for t in tasks if t.get("status") == "completed"}

        result = []
        for t in tasks:
            pending_blockers = [bid for bid in t.get("blocked_by", []) if bid not in completed_ids]
            result.append(
                {
                    "id": t["id"],
                    "subject": t["subject"],
                    "status": t.get("status", "pending"),
                    "blocked_by": pending_blockers,
                }
            )

        return json.dumps(result, indent=2)


class TaskUpdateTool(Tool):
    """Update an existing task's fields."""

    def __init__(self, workspace: Path):
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "task_update"

    @property
    def description(self) -> str:
        return (
            "Update a task. Can change subject, description, status "
            "(pending/in_progress/completed/deleted), add blockers, or update metadata. "
            "status='deleted' removes the task entirely."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "ID of the task to update",
                },
                "subject": {
                    "type": "string",
                    "description": "New subject (optional)",
                },
                "description": {
                    "type": "string",
                    "description": "New description (optional)",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "deleted"],
                    "description": "New status (optional). 'deleted' removes the task.",
                },
                "add_blocks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task IDs that this task blocks (appended)",
                },
                "add_blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Task IDs that block this task (appended)",
                },
                "metadata": {
                    "type": "object",
                    "description": "Metadata to merge into existing metadata",
                },
            },
            "required": ["task_id"],
        }

    async def execute(
        self,
        task_id: str = "",
        subject: str | None = None,
        description: str | None = None,
        status: str | None = None,
        add_blocks: list[str] | None = None,
        add_blocked_by: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        if not task_id:
            return "Error: task_id is required"

        tasks = _load_tasks(self._workspace)
        target = None
        target_idx = -1
        for i, t in enumerate(tasks):
            if t["id"] == task_id:
                target = t
                target_idx = i
                break

        if target is None:
            return f"Error: task {task_id} not found"

        # Handle deletion
        if status == "deleted":
            tasks.pop(target_idx)
            _save_tasks(self._workspace, tasks)
            return json.dumps({"id": task_id, "deleted": True})

        updated_fields = []

        if subject is not None:
            target["subject"] = subject
            updated_fields.append("subject")

        if description is not None:
            target["description"] = description
            updated_fields.append("description")

        if status is not None:
            target["status"] = status
            updated_fields.append("status")

        if add_blocks:
            existing = target.get("blocks", [])
            for bid in add_blocks:
                if bid not in existing:
                    existing.append(bid)
            target["blocks"] = existing
            updated_fields.append("blocks")

        if add_blocked_by:
            existing = target.get("blocked_by", [])
            for bid in add_blocked_by:
                if bid not in existing:
                    existing.append(bid)
            target["blocked_by"] = existing
            updated_fields.append("blocked_by")

        if metadata is not None:
            existing_meta = target.get("metadata", {})
            existing_meta.update(metadata)
            target["metadata"] = existing_meta
            updated_fields.append("metadata")

        _save_tasks(self._workspace, tasks)
        return json.dumps({"id": task_id, "updated": updated_fields})


class TaskGetTool(Tool):
    """Get full details of a single task."""

    def __init__(self, workspace: Path):
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "task_get"

    @property
    def description(self) -> str:
        return "Get full details of a task by ID. Returns null if not found."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "ID of the task to retrieve",
                },
            },
            "required": ["task_id"],
        }

    @property
    def parallel_safe(self) -> bool:
        return True

    async def execute(self, task_id: str = "", **kwargs: Any) -> str:
        if not task_id:
            return "Error: task_id is required"

        tasks = _load_tasks(self._workspace)
        for t in tasks:
            if t["id"] == task_id:
                return json.dumps(t, indent=2)

        return "null"
