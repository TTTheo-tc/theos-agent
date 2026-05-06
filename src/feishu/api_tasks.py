"""Feishu Task API -- V2 tasks, subtasks, members, reminders.

Uses lark-oapi SDK typed bindings (task.v2) for all operations.
All functions accept a pre-built ``lark.Client`` so callers own auth configuration.
"""

from __future__ import annotations

import lark_oapi as lark
from lark_oapi.api.task.v2 import (
    AddMembersTaskRequest,
    AddMembersTaskRequestBody,
    AddRemindersTaskRequest,
    AddRemindersTaskRequestBody,
    CreateTaskRequest,
    CreateTaskSubtaskRequest,
    DeleteTaskRequest,
    Due,
    GetTaskRequest,
    InputTask,
    ListTaskRequest,
    Member,
    Origin,
    PatchTaskRequest,
    PatchTaskRequestBody,
    Reminder,
)

from src.feishu.api import _call_with_option, _check, _request_option, _unmarshal
from src.feishu.retry import with_retry

# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


def _extend_items(target: list[dict], items) -> None:
    if items:
        target.extend(_unmarshal(items))


def list_tasks(
    client: lark.Client,
    page_size: int = 50,
    completed: bool | None = None,
) -> list[dict]:
    """List tasks visible to the current user (paginated).

    Uses GET /open-apis/task/v2/tasks.

    Args:
        page_size: Max results per page.
        completed: Filter by completion status. ``None`` returns all.
    """
    option = _request_option()
    tasks: list[dict] = []
    page_token: str | None = None

    while True:
        builder = ListTaskRequest.builder().page_size(min(page_size, 100))
        if completed is not None:
            builder = builder.completed(str(completed).lower())
        if page_token:
            builder = builder.page_token(page_token)
        request = builder.build()

        response = _call_with_option(client.task.v2.task.list, request, option)
        _check(response, "list_tasks")

        data = response.data
        _extend_items(tasks, data.items)
        if not data.has_more:
            break
        page_token = data.page_token
        if not page_token:
            break

    return tasks


def get_task(client: lark.Client, task_guid: str) -> dict:
    """Get a single task's detail.

    Uses GET /open-apis/task/v2/tasks/:task_guid.
    """
    option = _request_option()
    request = GetTaskRequest.builder().task_guid(task_guid).build()
    response = _call_with_option(client.task.v2.task.get, request, option)
    _check(response, "get_task")
    return _unmarshal(response.data.task)


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def create_task(
    client: lark.Client,
    summary: str,
    description: str = "",
    due: str | None = None,
    members: list[dict] | None = None,
    origin: dict | None = None,
) -> dict:
    """Create a task.

    Uses POST /open-apis/task/v2/tasks.

    Args:
        summary: Task title/summary.
        description: Task description.
        due: Due timestamp string (epoch seconds) or ``None``.
        members: List of member dicts, e.g.
            ``[{"id": "ou_xxx", "role": "assignee"}]``.
        origin: Origin dict, e.g.
            ``{"platform_i18n_name": {"en_us": "theos"}}``.
    """
    option = _request_option()

    task_builder = InputTask.builder().summary(summary)

    if description:
        task_builder = task_builder.description(description)

    if due:
        due_obj = Due.builder().timestamp(due).build()
        task_builder = task_builder.due(due_obj)

    if members:
        member_objs = []
        for m in members:
            mb = Member.builder().id(m["id"]).role(m.get("role", "assignee"))
            if m.get("type"):
                mb = mb.type(m["type"])
            member_objs.append(mb.build())
        task_builder = task_builder.members(member_objs)

    if origin:
        ob = Origin.builder()
        if origin.get("platform_i18n_name"):
            from lark_oapi.api.task.v2 import I18nText

            i18n = origin["platform_i18n_name"]
            if isinstance(i18n, str):
                i18n = {"en_us": i18n}
            i18n_builder = I18nText.builder()
            for lang, text in i18n.items():
                setter = getattr(i18n_builder, lang, None)
                if setter:
                    i18n_builder = setter(text)
            ob = ob.platform_i18n_name(i18n_builder.build())
        task_builder = task_builder.origin(ob.build())

    request = CreateTaskRequest.builder().request_body(task_builder.build()).build()
    response = _call_with_option(client.task.v2.task.create, request, option)
    _check(response, "create_task")
    return _unmarshal(response.data.task)


def complete_task(client: lark.Client, task_guid: str) -> bool:
    """Mark a task as completed.

    Uses PATCH /open-apis/task/v2/tasks/:task_guid to set ``completed_at``
    to the current timestamp.

    Returns:
        ``True`` on success.
    """
    import time

    option = _request_option()

    task_patch = InputTask.builder().completed_at(str(int(time.time()))).build()
    body = PatchTaskRequestBody.builder().task(task_patch).update_fields(["completed_at"]).build()
    request = PatchTaskRequest.builder().task_guid(task_guid).request_body(body).build()
    response = _call_with_option(client.task.v2.task.patch, request, option)
    _check(response, "complete_task")
    return True


def delete_task(client: lark.Client, task_guid: str) -> bool:
    """Delete a task.

    Uses DELETE /open-apis/task/v2/tasks/:task_guid.

    Returns:
        ``True`` on success.
    """
    option = _request_option()
    request = DeleteTaskRequest.builder().task_guid(task_guid).build()
    response = _call_with_option(client.task.v2.task.delete, request, option)
    _check(response, "delete_task")
    return True


def create_subtask(client: lark.Client, task_guid: str, summary: str) -> dict:
    """Add a subtask to an existing task.

    Uses POST /open-apis/task/v2/tasks/:task_guid/subtasks.

    Args:
        task_guid: Parent task GUID.
        summary: Subtask title/summary.
    """
    option = _request_option()
    subtask = InputTask.builder().summary(summary).build()
    request = CreateTaskSubtaskRequest.builder().task_guid(task_guid).request_body(subtask).build()
    response = _call_with_option(client.task.v2.task_subtask.create, request, option)
    _check(response, "create_subtask")
    return _unmarshal(response.data.task)


def add_task_member(
    client: lark.Client,
    task_guid: str,
    member_id: str,
    role: str = "assignee",
) -> dict:
    """Add a member to a task.

    Uses POST /open-apis/task/v2/tasks/:task_guid/add_members.

    Args:
        task_guid: Task GUID.
        member_id: User open_id (e.g. ``ou_xxx``).
        role: ``"assignee"`` or ``"follower"``.
    """
    option = _request_option()
    member = Member.builder().id(member_id).role(role).build()
    body = AddMembersTaskRequestBody.builder().members([member]).build()
    request = AddMembersTaskRequest.builder().task_guid(task_guid).request_body(body).build()
    response = _call_with_option(client.task.v2.task.add_members, request, option)
    _check(response, "add_task_member")
    return _unmarshal(response.data.task)


def add_task_reminder(
    client: lark.Client,
    task_guid: str,
    relative_fire_minute: int,
) -> dict:
    """Add a reminder to a task.

    Uses POST /open-apis/task/v2/tasks/:task_guid/add_reminders.

    Args:
        task_guid: Task GUID.
        relative_fire_minute: Minutes before due time to fire the reminder.
    """
    option = _request_option()
    reminder = Reminder.builder().relative_fire_minute(relative_fire_minute).build()
    body = AddRemindersTaskRequestBody.builder().reminders([reminder]).build()
    request = AddRemindersTaskRequest.builder().task_guid(task_guid).request_body(body).build()
    response = _call_with_option(client.task.v2.task.add_reminders, request, option)
    _check(response, "add_task_reminder")
    return _unmarshal(response.data.task)


# ---------------------------------------------------------------------------
# Async retry-wrapped variants
# ---------------------------------------------------------------------------


async def list_tasks_with_retry(
    client: lark.Client,
    page_size: int = 50,
    completed: bool | None = None,
    **retry_kwargs,
) -> list[dict]:
    """list_tasks with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        list_tasks,
        client,
        page_size=page_size,
        completed=completed,
        action="list_tasks",
        **retry_kwargs,
    )


async def get_task_with_retry(client: lark.Client, task_guid: str, **retry_kwargs) -> dict:
    """get_task with automatic retry on transient/rate-limit errors."""
    return await with_retry(get_task, client, task_guid, action="get_task", **retry_kwargs)


async def create_task_with_retry(
    client: lark.Client,
    summary: str,
    description: str = "",
    due: str | None = None,
    members: list[dict] | None = None,
    origin: dict | None = None,
    **retry_kwargs,
) -> dict:
    """create_task with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        create_task,
        client,
        summary=summary,
        description=description,
        due=due,
        members=members,
        origin=origin,
        action="create_task",
        **retry_kwargs,
    )


async def complete_task_with_retry(client: lark.Client, task_guid: str, **retry_kwargs) -> bool:
    """complete_task with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        complete_task,
        client,
        task_guid,
        action="complete_task",
        **retry_kwargs,
    )


async def delete_task_with_retry(client: lark.Client, task_guid: str, **retry_kwargs) -> bool:
    """delete_task with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        delete_task,
        client,
        task_guid,
        action="delete_task",
        **retry_kwargs,
    )


async def create_subtask_with_retry(
    client: lark.Client,
    task_guid: str,
    summary: str,
    **retry_kwargs,
) -> dict:
    """create_subtask with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        create_subtask,
        client,
        task_guid,
        summary,
        action="create_subtask",
        **retry_kwargs,
    )


async def add_task_member_with_retry(
    client: lark.Client,
    task_guid: str,
    member_id: str,
    role: str = "assignee",
    **retry_kwargs,
) -> dict:
    """add_task_member with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        add_task_member,
        client,
        task_guid,
        member_id,
        role=role,
        action="add_task_member",
        **retry_kwargs,
    )


async def add_task_reminder_with_retry(
    client: lark.Client,
    task_guid: str,
    relative_fire_minute: int,
    **retry_kwargs,
) -> dict:
    """add_task_reminder with automatic retry on transient/rate-limit errors."""
    return await with_retry(
        add_task_reminder,
        client,
        task_guid,
        relative_fire_minute,
        action="add_task_reminder",
        **retry_kwargs,
    )
