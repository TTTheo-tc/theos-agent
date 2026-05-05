"""Tests for Feishu Contacts API, FeishuClient contact methods, and FeishuContactTool."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.agent.tools.feishu import FeishuContactTool
from src.feishu import api_contacts

# ---------------------------------------------------------------------------
# Helpers
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
    """Return a mock lark.Client with contact.v3 sub-services wired up."""
    client = MagicMock()
    client.contact.v3.department.children = MagicMock()
    client.contact.v3.department.get = MagicMock()
    client.contact.v3.user.list = MagicMock()
    client.contact.v3.user.get = MagicMock()
    client.contact.v3.user.batch_get_id = MagicMock()
    return client


# ---------------------------------------------------------------------------
# api_contacts unit tests
# ---------------------------------------------------------------------------


class TestListDepartments:
    def test_list_departments(self):
        client = _mock_lark_client()
        data = SimpleNamespace(
            items=[
                {"department_id": "d1", "name": "Engineering"},
                {"department_id": "d2", "name": "Design"},
            ],
            has_more=False,
            page_token=None,
        )
        client.contact.v3.department.children.return_value = _ok_response(data)

        with patch("src.feishu.api_contacts._request_option", return_value=None):
            result = api_contacts.list_departments(client, "0")

        assert len(result) == 2
        assert result[0]["department_id"] == "d1"

    def test_list_departments_passes_request_option(self):
        client = _mock_lark_client()
        data = SimpleNamespace(items=[], has_more=False, page_token=None)
        client.contact.v3.department.children.return_value = _ok_response(data)
        option = object()

        with patch("src.feishu.api_contacts._request_option", return_value=option):
            api_contacts.list_departments(client, "0")

        assert client.contact.v3.department.children.call_args.args[1] is option

    def test_list_departments_paginated(self):
        client = _mock_lark_client()
        page1 = SimpleNamespace(
            items=[{"department_id": "d1"}],
            has_more=True,
            page_token="page2",
        )
        page2 = SimpleNamespace(
            items=[{"department_id": "d2"}],
            has_more=False,
            page_token=None,
        )
        client.contact.v3.department.children.side_effect = [
            _ok_response(page1),
            _ok_response(page2),
        ]

        with patch("src.feishu.api_contacts._request_option", return_value=None):
            result = api_contacts.list_departments(client)

        assert len(result) == 2


class TestGetDepartment:
    def test_get_department(self):
        client = _mock_lark_client()
        dept = SimpleNamespace(department_id="d1", name="Engineering", member_count=42)
        data = SimpleNamespace(department=dept)
        client.contact.v3.department.get.return_value = _ok_response(data)

        with patch("src.feishu.api_contacts._request_option", return_value=None):
            result = api_contacts.get_department(client, "d1")

        assert result["name"] == "Engineering"


class TestListDepartmentUsers:
    def test_list_department_users(self):
        client = _mock_lark_client()
        data = SimpleNamespace(
            items=[
                {"user_id": "u1", "name": "Alice"},
                {"user_id": "u2", "name": "Bob"},
            ],
            has_more=False,
            page_token=None,
        )
        client.contact.v3.user.list.return_value = _ok_response(data)

        with patch("src.feishu.api_contacts._request_option", return_value=None):
            result = api_contacts.list_department_users(client, "d1")

        assert len(result) == 2
        assert result[0]["name"] == "Alice"


class TestGetUserByEmail:
    def test_found(self):
        client = _mock_lark_client()
        data = SimpleNamespace(user_list=[{"user_id": "ou_abc", "email": "alice@example.com"}])
        client.contact.v3.user.batch_get_id.return_value = _ok_response(data)

        with patch("src.feishu.api_contacts._request_option", return_value=None):
            result = api_contacts.get_user_by_email(client, "alice@example.com")

        assert result is not None
        assert result["user_id"] == "ou_abc"

    def test_found_from_sdk_collection(self):
        client = _mock_lark_client()
        raw_user_list = object()
        data = SimpleNamespace(user_list=raw_user_list)
        client.contact.v3.user.batch_get_id.return_value = _ok_response(data)

        with (
            patch("src.feishu.api_contacts._request_option", return_value=None),
            patch(
                "src.feishu.api_contacts._unmarshal",
                return_value=[{"user_id": "ou_abc", "email": "alice@example.com"}],
            ) as unmarshal,
        ):
            result = api_contacts.get_user_by_email(client, "alice@example.com")

        assert result is not None
        assert result["user_id"] == "ou_abc"
        unmarshal.assert_called_once_with(raw_user_list)

    def test_find_by_email_passes_request_option(self):
        client = _mock_lark_client()
        data = SimpleNamespace(user_list=[{"user_id": "ou_abc"}])
        client.contact.v3.user.batch_get_id.return_value = _ok_response(data)
        option = object()

        with patch("src.feishu.api_contacts._request_option", return_value=option):
            api_contacts.get_user_by_email(client, "alice@example.com")

        assert client.contact.v3.user.batch_get_id.call_args.args[1] is option

    def test_not_found(self):
        client = _mock_lark_client()
        data = SimpleNamespace(user_list=[])
        client.contact.v3.user.batch_get_id.return_value = _ok_response(data)

        with patch("src.feishu.api_contacts._request_option", return_value=None):
            result = api_contacts.get_user_by_email(client, "nobody@example.com")

        assert result is None


class TestGetUserByPhone:
    def test_found(self):
        client = _mock_lark_client()
        data = SimpleNamespace(user_list=[{"user_id": "ou_xyz", "mobile": "+8613800138000"}])
        client.contact.v3.user.batch_get_id.return_value = _ok_response(data)

        with patch("src.feishu.api_contacts._request_option", return_value=None):
            result = api_contacts.get_user_by_phone(client, "+8613800138000")

        assert result is not None
        assert result["user_id"] == "ou_xyz"

    def test_not_found(self):
        client = _mock_lark_client()
        data = SimpleNamespace(user_list=None)
        client.contact.v3.user.batch_get_id.return_value = _ok_response(data)

        with patch("src.feishu.api_contacts._request_option", return_value=None):
            result = api_contacts.get_user_by_phone(client, "+0000000")

        assert result is None


# ---------------------------------------------------------------------------
# Tool tests
# ---------------------------------------------------------------------------


def _mock_feishu_client():
    """Return a mock FeishuClient for tool testing."""
    client = MagicMock()
    client.info_user.return_value = {"user_id": "ou_abc", "name": "Alice"}
    client.search_users.return_value = [{"user_id": "ou_abc", "name": "Alice"}]
    client.contact_departments.return_value = [{"department_id": "d1", "name": "Engineering"}]
    client.contact_department_users.return_value = [{"user_id": "ou_abc", "name": "Alice"}]
    client.contact_find_by_email.return_value = {"user_id": "ou_abc"}
    client.contact_find_by_phone.return_value = {"user_id": "ou_xyz"}
    return client


class TestFeishuContactToolSchema:
    def test_tool_name(self):
        tool = FeishuContactTool(client=MagicMock())
        assert tool.name == "feishu_contact"

    def test_tool_parameters_has_action(self):
        tool = FeishuContactTool(client=MagicMock())
        props = tool.parameters["properties"]
        assert "action" in props
        assert set(props["action"]["enum"]) == {
            "user",
            "search",
            "departments",
            "department_users",
            "find_by_email",
            "find_by_phone",
        }


class TestFeishuContactToolUserAction:
    def test_user_action(self):
        client = _mock_feishu_client()
        tool = FeishuContactTool(client=client)
        result = asyncio.run(tool.execute(action="user", user_id="ou_abc"))
        assert "Alice" in result
        client.info_user.assert_called_once_with("ou_abc")

    def test_user_requires_user_id(self):
        tool = FeishuContactTool(client=MagicMock())
        result = asyncio.run(tool.execute(action="user"))
        assert "Error" in result


class TestFeishuContactToolSearchAction:
    def test_search_action(self):
        client = _mock_feishu_client()
        tool = FeishuContactTool(client=client)
        result = asyncio.run(tool.execute(action="search", query="Alice"))
        assert "Alice" in result
        client.search_users.assert_called_once_with("Alice")

    def test_search_requires_query(self):
        tool = FeishuContactTool(client=MagicMock())
        result = asyncio.run(tool.execute(action="search"))
        assert "Error" in result


class TestFeishuContactToolDepartments:
    def test_departments_action(self):
        client = _mock_feishu_client()
        tool = FeishuContactTool(client=client)
        result = asyncio.run(tool.execute(action="departments"))
        assert "Engineering" in result
        client.contact_departments.assert_called_once_with("0")

    def test_departments_with_parent(self):
        client = _mock_feishu_client()
        tool = FeishuContactTool(client=client)
        asyncio.run(tool.execute(action="departments", department_id="d1"))
        client.contact_departments.assert_called_once_with("d1")


class TestFeishuContactToolDepartmentUsers:
    def test_department_users_action(self):
        client = _mock_feishu_client()
        tool = FeishuContactTool(client=client)
        result = asyncio.run(tool.execute(action="department_users", department_id="d1"))
        assert "Alice" in result

    def test_department_users_requires_id(self):
        tool = FeishuContactTool(client=MagicMock())
        result = asyncio.run(tool.execute(action="department_users"))
        assert "Error" in result


class TestFeishuContactToolFindByEmail:
    def test_find_by_email(self):
        client = _mock_feishu_client()
        tool = FeishuContactTool(client=client)
        result = asyncio.run(tool.execute(action="find_by_email", email="alice@example.com"))
        assert "ou_abc" in result

    def test_find_by_email_requires_email(self):
        tool = FeishuContactTool(client=MagicMock())
        result = asyncio.run(tool.execute(action="find_by_email"))
        assert "Error" in result


class TestFeishuContactToolFindByPhone:
    def test_find_by_phone(self):
        client = _mock_feishu_client()
        tool = FeishuContactTool(client=client)
        result = asyncio.run(tool.execute(action="find_by_phone", phone="+8613800138000"))
        assert "ou_xyz" in result

    def test_find_by_phone_requires_phone(self):
        tool = FeishuContactTool(client=MagicMock())
        result = asyncio.run(tool.execute(action="find_by_phone"))
        assert "Error" in result
