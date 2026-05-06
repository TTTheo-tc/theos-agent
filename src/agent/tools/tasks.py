"""JSON-backed task management tools."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.agent.tools.base import Tool

_TASKS_FILE = "tasks.json"


def _tasks_path(workspace: Path) -> Path:
    return workspace / _TASKS_FILE


def _load_tasks(workspace: Path) -> list[dict[str, Any]]:
    path = _tasks_path(workspace)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_tasks(workspace: Path, tasks: list[dict[str, Any]]) -> None:
    path = _tasks_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(tasks, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _find_task(tasks: list[dict[str, Any]], task_id: str) -> tuple[int, dict[str, Any] | None]:
    for idx, task in enumerate(tasks):
        if task["id"] == task_id:
            return idx, task
    return -1, None


def _append_unique(target: dict[str, Any], field: str, values: list[str] | None) -> bool:
    if not values:
        return False
    existing = target.get(field, [])
    for value in values:
        if value not in existing:
            existing.append(value)
    target[field] = existing
    return True


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

        completed_ids = {task["id"] for task in tasks if task.get("status") == "completed"}
        result = []
        for task in tasks:
            pending_blockers = [
                blocker_id for blocker_id in task.get("blocked_by", []) if blocker_id not in completed_ids
            ]
            result.append(
                {
                    "id": task["id"],
                    "subject": task["subject"],
                    "status": task.get("status", "pending"),
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
        target_idx, target = _find_task(tasks, task_id)
        if target is None:
            return f"Error: task {task_id} not found"

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

        if _append_unique(target, "blocks", add_blocks):
            updated_fields.append("blocks")

        if _append_unique(target, "blocked_by", add_blocked_by):
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
        _, task = _find_task(tasks, task_id)
        if task is not None:
            return json.dumps(task, indent=2)

        return "null"
