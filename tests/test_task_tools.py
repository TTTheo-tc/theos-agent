"""Tests for JSON-backed task tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.tools.tasks import TaskCreateTool, TaskGetTool, TaskListTool, TaskUpdateTool


def test_task_tools_remain_importable_from_todo_module() -> None:
    from src.agent.tools.todo import TaskCreateTool as TodoModuleTaskCreateTool

    assert TodoModuleTaskCreateTool is TaskCreateTool


@pytest.mark.asyncio
async def test_task_create_get_update_list_and_delete(tmp_path: Path) -> None:
    create = TaskCreateTool(tmp_path)
    get = TaskGetTool(tmp_path)
    update = TaskUpdateTool(tmp_path)
    list_tool = TaskListTool(tmp_path)

    created = json.loads(
        await create.execute(
            subject="Implement task tools",
            description="Cover JSON task behavior",
            metadata={"area": "agent-tools"},
        )
    )
    task_id = created["id"]
    assert created["subject"] == "Implement task tools"

    fetched = json.loads(await get.execute(task_id=task_id))
    assert fetched["description"] == "Cover JSON task behavior"
    assert fetched["metadata"] == {"area": "agent-tools"}

    updated = json.loads(
        await update.execute(
            task_id=task_id,
            status="in_progress",
            add_blocks=["child-1", "child-1"],
            add_blocked_by=["parent-1"],
            metadata={"priority": "high"},
        )
    )
    assert updated["updated"] == ["status", "blocks", "blocked_by", "metadata"]

    fetched = json.loads(await get.execute(task_id=task_id))
    assert fetched["status"] == "in_progress"
    assert fetched["blocks"] == ["child-1"]
    assert fetched["blocked_by"] == ["parent-1"]
    assert fetched["metadata"] == {"area": "agent-tools", "priority": "high"}

    listed = json.loads(await list_tool.execute())
    assert listed == [
        {
            "id": task_id,
            "subject": "Implement task tools",
            "status": "in_progress",
            "blocked_by": ["parent-1"],
        }
    ]

    deleted = json.loads(await update.execute(task_id=task_id, status="deleted"))
    assert deleted == {"id": task_id, "deleted": True}
    assert await get.execute(task_id=task_id) == "null"


@pytest.mark.asyncio
async def test_task_list_hides_completed_blockers(tmp_path: Path) -> None:
    create = TaskCreateTool(tmp_path)
    update = TaskUpdateTool(tmp_path)
    list_tool = TaskListTool(tmp_path)

    parent = json.loads(await create.execute(subject="Parent", description="done"))
    child = json.loads(await create.execute(subject="Child", description="blocked"))

    await update.execute(task_id=parent["id"], status="completed")
    await update.execute(task_id=child["id"], add_blocked_by=[parent["id"], "open-blocker"])

    listed = json.loads(await list_tool.execute())
    child_row = next(item for item in listed if item["id"] == child["id"])
    assert child_row["blocked_by"] == ["open-blocker"]


@pytest.mark.asyncio
async def test_task_tools_handle_missing_inputs(tmp_path: Path) -> None:
    assert await TaskCreateTool(tmp_path).execute(subject="", description="body") == (
        "Error: subject is required"
    )
    assert await TaskCreateTool(tmp_path).execute(subject="title", description="") == (
        "Error: description is required"
    )
    assert await TaskUpdateTool(tmp_path).execute(task_id="") == "Error: task_id is required"
    assert await TaskUpdateTool(tmp_path).execute(task_id="missing") == "Error: task missing not found"
    assert await TaskGetTool(tmp_path).execute(task_id="") == "Error: task_id is required"
    assert await TaskListTool(tmp_path).execute() == "No tasks."
