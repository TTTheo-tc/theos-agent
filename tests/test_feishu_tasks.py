"""Tests for Feishu Task API, FeishuClient task methods, and FeishuTaskTool."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent.tools.feishu import FeishuTaskTool, _resolve_due_date
from src.feishu import api_tasks

# ---------------------------------------------------------------------------
# Helpers: build mock lark client with task.v2 stubs
# ---------------------------------------------------------------------------


def _ok_response(data_obj):
    """Build a mock SDK response that passes _check()."""
    resp = MagicMock()
    resp.success.return_value = True
    resp.code = 0
    resp.msg = "ok"
    resp.data = data_obj
    return resp


def _mock_lark_client():
    """Return a mock lark.Client with task.v2 sub-services wired up."""
    client = MagicMock()
    client.task.v2.task.list = MagicMock()
    client.task.v2.task.get = MagicMock()
    client.task.v2.task.create = MagicMock()
    client.task.v2.task.patch = MagicMock()
    client.task.v2.task.delete = MagicMock()
    client.task.v2.task.add_members = MagicMock()
    client.task.v2.task.add_reminders = MagicMock()
    client.task.v2.task_subtask.create = MagicMock()
    return client


# ---------------------------------------------------------------------------
# api_tasks unit tests
# ---------------------------------------------------------------------------


class TestListTasks:
    def test_list_tasks(self):
        client = _mock_lark_client()
        data = SimpleNamespace(
            items=[
                {"guid": "t_1", "summary": "Buy milk"},
                {"guid": "t_2", "summary": "Review PR"},
            ],
            has_more=False,
            page_token=None,
        )
        client.task.v2.task.list.return_value = _ok_response(data)

        with patch("src.feishu.api_tasks._request_option", return_value=None):
            result = api_tasks.list_tasks(client)

        assert len(result) == 2
        assert result[0]["guid"] == "t_1"

    def test_list_tasks_passes_request_option(self):
        client = _mock_lark_client()
        data = SimpleNamespace(items=[], has_more=False, page_token=None)
        client.task.v2.task.list.return_value = _ok_response(data)
        option = object()

        with patch("src.feishu.api_tasks._request_option", return_value=option):
            api_tasks.list_tasks(client)

        assert client.task.v2.task.list.call_args.args[1] is option

    def test_list_tasks_paginated(self):
        client = _mock_lark_client()
        page1 = SimpleNamespace(
            items=[{"guid": "t_1"}],
            has_more=True,
            page_token="page2",
        )
        page2 = SimpleNamespace(
            items=[{"guid": "t_2"}],
            has_more=False,
            page_token=None,
        )
        client.task.v2.task.list.side_effect = [
            _ok_response(page1),
            _ok_response(page2),
        ]

        with patch("src.feishu.api_tasks._request_option", return_value=None):
            result = api_tasks.list_tasks(client)

        assert len(result) == 2

    def test_list_tasks_empty(self):
        client = _mock_lark_client()
        data = SimpleNamespace(items=None, has_more=False, page_token=None)
        client.task.v2.task.list.return_value = _ok_response(data)

        with patch("src.feishu.api_tasks._request_option", return_value=None):
            result = api_tasks.list_tasks(client)

        assert result == []


class TestGetTask:
    def test_get_task(self):
        client = _mock_lark_client()
        task_data = SimpleNamespace(
            task={"guid": "t_1", "summary": "Buy milk", "completed_at": "0"}
        )
        client.task.v2.task.get.return_value = _ok_response(task_data)

        with patch("src.feishu.api_tasks._request_option", return_value=None):
            result = api_tasks.get_task(client, "t_1")

        assert result["guid"] == "t_1"
        assert result["summary"] == "Buy milk"

    def test_get_task_uses_one_arg_when_no_option(self):
        client = _mock_lark_client()
        task_data = SimpleNamespace(task={"guid": "t_1"})
        client.task.v2.task.get.return_value = _ok_response(task_data)

        with patch("src.feishu.api_tasks._request_option", return_value=None):
            api_tasks.get_task(client, "t_1")

        assert len(client.task.v2.task.get.call_args.args) == 1


class TestCreateTask:
    def test_create_task_minimal(self):
        client = _mock_lark_client()
        created = SimpleNamespace(task={"guid": "t_new", "summary": "New task"})
        client.task.v2.task.create.return_value = _ok_response(created)

        with patch("src.feishu.api_tasks._request_option", return_value=None):
            result = api_tasks.create_task(client, summary="New task")

        assert result["guid"] == "t_new"
        client.task.v2.task.create.assert_called_once()

    def test_create_task_with_due_and_members(self):
        client = _mock_lark_client()
        created = SimpleNamespace(task={"guid": "t_full", "summary": "Full task"})
        client.task.v2.task.create.return_value = _ok_response(created)

        with patch("src.feishu.api_tasks._request_option", return_value=None):
            result = api_tasks.create_task(
                client,
                summary="Full task",
                description="Important stuff",
                due="1711353600",
                members=[{"id": "ou_abc", "role": "assignee"}],
                origin={"platform_i18n_name": "theos"},
            )

        assert result["guid"] == "t_full"


class TestCompleteTask:
    def test_complete_task(self):
        client = _mock_lark_client()
        patched = SimpleNamespace(task={"guid": "t_1", "completed_at": "1711353600"})
        client.task.v2.task.patch.return_value = _ok_response(patched)

        with patch("src.feishu.api_tasks._request_option", return_value=None):
            result = api_tasks.complete_task(client, "t_1")

        assert result is True
        client.task.v2.task.patch.assert_called_once()


class TestDeleteTask:
    def test_delete_task(self):
        client = _mock_lark_client()
        client.task.v2.task.delete.return_value = _ok_response(SimpleNamespace())

        with patch("src.feishu.api_tasks._request_option", return_value=None):
            result = api_tasks.delete_task(client, "t_1")

        assert result is True


class TestCreateSubtask:
    def test_create_subtask(self):
        client = _mock_lark_client()
        subtask_data = SimpleNamespace(task={"guid": "t_sub", "summary": "Subtask 1"})
        client.task.v2.task_subtask.create.return_value = _ok_response(subtask_data)

        with patch("src.feishu.api_tasks._request_option", return_value=None):
            result = api_tasks.create_subtask(client, "t_parent", "Subtask 1")

        assert result["guid"] == "t_sub"
        assert result["summary"] == "Subtask 1"

    def test_create_subtask_passes_request_option(self):
        client = _mock_lark_client()
        subtask_data = SimpleNamespace(task={"guid": "t_sub"})
        client.task.v2.task_subtask.create.return_value = _ok_response(subtask_data)
        option = object()

        with patch("src.feishu.api_tasks._request_option", return_value=option):
            api_tasks.create_subtask(client, "t_parent", "Subtask 1")

        assert client.task.v2.task_subtask.create.call_args.args[1] is option


class TestTaskMemberAndReminder:
    def test_add_task_member(self):
        client = _mock_lark_client()
        task_data = SimpleNamespace(task={"guid": "t_1"})
        client.task.v2.task.add_members.return_value = _ok_response(task_data)

        with patch("src.feishu.api_tasks._request_option", return_value=None):
            result = api_tasks.add_task_member(client, "t_1", "ou_member", role="follower")

        assert result["guid"] == "t_1"
        assert len(client.task.v2.task.add_members.call_args.args) == 1

    def test_add_task_reminder_passes_request_option(self):
        client = _mock_lark_client()
        task_data = SimpleNamespace(task={"guid": "t_1"})
        client.task.v2.task.add_reminders.return_value = _ok_response(task_data)
        option = object()

        with patch("src.feishu.api_tasks._request_option", return_value=option):
            result = api_tasks.add_task_reminder(client, "t_1", 30)

        assert result["guid"] == "t_1"
        assert client.task.v2.task.add_reminders.call_args.args[1] is option


# ---------------------------------------------------------------------------
# Due date parsing tests
# ---------------------------------------------------------------------------


class TestResolveDueDate:
    def test_today(self):
        result = _resolve_due_date("today")
        # Should be a numeric string (epoch seconds)
        assert result.isdigit()
        ts = int(result)
        assert ts > 0

    def test_tomorrow(self):
        result = _resolve_due_date("tomorrow")
        assert result.isdigit()
        today_ts = int(_resolve_due_date("today"))
        tomorrow_ts = int(result)
        # Tomorrow should be ~86400 seconds later
        assert 80000 < (tomorrow_ts - today_ts) < 180000

    def test_next_monday(self):
        result = _resolve_due_date("next monday")
        assert result.isdigit()

    def test_next_friday(self):
        result = _resolve_due_date("next friday")
        assert result.isdigit()

    def test_rfc3339(self):
        result = _resolve_due_date("2026-03-25T18:00:00+08:00")
        assert result.isdigit()

    def test_epoch_passthrough(self):
        result = _resolve_due_date("1711353600")
        assert result == "1711353600"


# ---------------------------------------------------------------------------
# Tool schema + execute tests
# ---------------------------------------------------------------------------


class TestFeishuTaskToolSchema:
    def test_tool_schema(self):
        tool = FeishuTaskTool(client=MagicMock())
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "feishu_task"
        params = schema["function"]["parameters"]
        assert "action" in params["properties"]
        assert params["properties"]["action"]["enum"] == [
            "list",
            "get",
            "create",
            "complete",
            "delete",
            "subtask",
        ]
        assert "action" in params["required"]

    def test_risk_level(self):
        tool = FeishuTaskTool(client=MagicMock())
        assert tool.risk_level == "medium"


class TestFeishuTaskToolExecute:
    def _make_tool(self):
        mock_client = MagicMock()
        mock_client.task_list.return_value = [{"guid": "t_1", "summary": "Task 1"}]
        mock_client.task_get.return_value = {"guid": "t_1", "summary": "Task 1"}
        mock_client.task_create.return_value = {"guid": "t_new", "summary": "New task"}
        mock_client.task_complete.return_value = True
        mock_client.task_delete.return_value = True
        mock_client.task_add_subtask.return_value = {"guid": "t_sub", "summary": "Sub"}
        return FeishuTaskTool(client=mock_client), mock_client

    def test_execute_list(self):
        tool, mock_client = self._make_tool()
        result = asyncio.run(tool.execute(action="list"))
        assert "t_1" in result
        mock_client.task_list.assert_called_once()

    def test_execute_list_completed(self):
        tool, mock_client = self._make_tool()
        result = asyncio.run(tool.execute(action="list", completed=True))
        assert "t_1" in result
        mock_client.task_list.assert_called_once_with(completed=True)

    def test_execute_get(self):
        tool, mock_client = self._make_tool()
        result = asyncio.run(tool.execute(action="get", task_id="t_1"))
        assert "t_1" in result
        mock_client.task_get.assert_called_once_with("t_1")

    def test_execute_get_missing_id(self):
        tool, _ = self._make_tool()
        result = asyncio.run(tool.execute(action="get"))
        assert "Error" in result

    def test_execute_create(self):
        tool, mock_client = self._make_tool()
        result = asyncio.run(
            tool.execute(
                action="create",
                summary="New task",
                description="Details",
                due="tomorrow",
                assignee="ou_abc",
            )
        )
        assert "t_new" in result
        mock_client.task_create.assert_called_once()
        call_kwargs = mock_client.task_create.call_args.kwargs
        assert call_kwargs["summary"] == "New task"
        assert call_kwargs["description"] == "Details"
        assert call_kwargs["assignee"] == "ou_abc"
        # due should be resolved to epoch
        assert call_kwargs["due"].isdigit()

    def test_execute_create_missing_summary(self):
        tool, _ = self._make_tool()
        result = asyncio.run(tool.execute(action="create"))
        assert "Error" in result

    def test_execute_complete(self):
        tool, mock_client = self._make_tool()
        result = asyncio.run(tool.execute(action="complete", task_id="t_1"))
        assert "true" in result.lower() or "True" in result
        mock_client.task_complete.assert_called_once_with("t_1")

    def test_execute_complete_missing_id(self):
        tool, _ = self._make_tool()
        result = asyncio.run(tool.execute(action="complete"))
        assert "Error" in result

    def test_execute_delete(self):
        tool, mock_client = self._make_tool()
        result = asyncio.run(tool.execute(action="delete", task_id="t_1"))
        assert "true" in result.lower() or "True" in result
        mock_client.task_delete.assert_called_once_with("t_1")

    def test_execute_delete_missing_id(self):
        tool, _ = self._make_tool()
        result = asyncio.run(tool.execute(action="delete"))
        assert "Error" in result

    def test_execute_subtask(self):
        tool, mock_client = self._make_tool()
        result = asyncio.run(tool.execute(action="subtask", task_id="t_1", summary="Sub"))
        assert "t_sub" in result
        mock_client.task_add_subtask.assert_called_once_with("t_1", "Sub")

    def test_execute_subtask_missing_id(self):
        tool, _ = self._make_tool()
        result = asyncio.run(tool.execute(action="subtask", summary="Sub"))
        assert "Error" in result

    def test_execute_subtask_missing_summary(self):
        tool, _ = self._make_tool()
        result = asyncio.run(tool.execute(action="subtask", task_id="t_1"))
        assert "Error" in result

    def test_execute_unknown_action(self):
        tool, _ = self._make_tool()
        result = asyncio.run(tool.execute(action="unknown_action"))
        assert "Error" in result
        assert "unknown_action" in result
