"""Tests for Feishu permission CRUD (api_write) and FeishuPermTool."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.feishu.api_write import (
    list_permission_members,
    remove_permission_member,
    transfer_owner,
    update_permission_member,
)
from src.feishu.errors import FeishuAPIError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_response(data_obj):
    resp = MagicMock()
    resp.success.return_value = True
    resp.code = 0
    resp.msg = "ok"
    resp.data = data_obj
    return resp


def _mock_unmarshal(obj):
    """Return a plain dict/list instead of trying to JSON-serialize a MagicMock."""
    if isinstance(obj, (dict, list)):
        return obj
    return {"member_type": "openid", "member_id": "ou_user1", "perm": "edit"}


# ---------------------------------------------------------------------------
# list_permission_members
# ---------------------------------------------------------------------------


class TestListPermissionMembers:
    def test_returns_list(self):
        client = MagicMock()
        data = SimpleNamespace(
            items=[{"member_type": "openid", "member_id": "ou_a", "perm": "edit"}],
        )
        client.drive.v1.permission_member.list.return_value = _ok_response(data)

        with (
            patch("src.feishu.api_write._request_option", return_value=None),
            patch(
                "src.feishu.api_write._unmarshal",
                return_value=[{"member_type": "openid", "member_id": "ou_a", "perm": "edit"}],
            ),
        ):
            result = list_permission_members(client, "tok123", "docx")

        assert len(result) == 1
        assert result[0]["member_id"] == "ou_a"
        client.drive.v1.permission_member.list.assert_called_once()

    def test_returns_empty_when_no_items(self):
        client = MagicMock()
        data = SimpleNamespace(items=None)
        client.drive.v1.permission_member.list.return_value = _ok_response(data)

        with patch("src.feishu.api_write._request_option", return_value=None):
            result = list_permission_members(client, "tok123", "wiki")

        assert result == []

    def test_passes_option(self):
        client = MagicMock()
        data = SimpleNamespace(items=None)
        client.drive.v1.permission_member.list.return_value = _ok_response(data)
        mock_option = MagicMock()

        with patch("src.feishu.api_write._request_option", return_value=mock_option):
            list_permission_members(client, "tok123", "docx")

        call_args = client.drive.v1.permission_member.list.call_args
        assert len(call_args.args) == 2
        assert call_args.args[1] is mock_option

    def test_raises_on_failure(self):
        client = MagicMock()
        resp = MagicMock()
        resp.success.return_value = False
        resp.code = 403
        resp.msg = "permission denied"
        client.drive.v1.permission_member.list.return_value = resp

        with (
            patch("src.feishu.api_write._request_option", return_value=None),
            pytest.raises(FeishuAPIError, match="permission denied"),
        ):
            list_permission_members(client, "tok", "docx")


# ---------------------------------------------------------------------------
# update_permission_member
# ---------------------------------------------------------------------------


class TestUpdatePermissionMember:
    def test_calls_api(self):
        client = MagicMock()
        data = SimpleNamespace(member=MagicMock())
        client.drive.v1.permission_member.update.return_value = _ok_response(data)

        with (
            patch("src.feishu.api_write._request_option", return_value=None),
            patch("src.feishu.api_write._unmarshal", side_effect=_mock_unmarshal),
        ):
            result = update_permission_member(
                client, "tok123", "docx", "openid", "ou_user1", "edit"
            )

        client.drive.v1.permission_member.update.assert_called_once()
        assert result["perm"] == "edit"

    def test_passes_option(self):
        client = MagicMock()
        data = SimpleNamespace(member=MagicMock())
        client.drive.v1.permission_member.update.return_value = _ok_response(data)
        mock_option = MagicMock()

        with (
            patch("src.feishu.api_write._request_option", return_value=mock_option),
            patch("src.feishu.api_write._unmarshal", side_effect=_mock_unmarshal),
        ):
            update_permission_member(client, "tok", "wiki", "openid", "ou_u", "view")

        call_args = client.drive.v1.permission_member.update.call_args
        assert len(call_args[0]) == 2
        assert call_args[0][1] is mock_option


# ---------------------------------------------------------------------------
# remove_permission_member
# ---------------------------------------------------------------------------


class TestRemovePermissionMember:
    def test_returns_true(self):
        client = MagicMock()
        client.drive.v1.permission_member.delete.return_value = _ok_response(SimpleNamespace())

        with patch("src.feishu.api_write._request_option", return_value=None):
            result = remove_permission_member(client, "tok123", "docx", "openid", "ou_user1")

        assert result is True
        client.drive.v1.permission_member.delete.assert_called_once()

    def test_passes_option(self):
        client = MagicMock()
        client.drive.v1.permission_member.delete.return_value = _ok_response(SimpleNamespace())
        mock_option = MagicMock()

        with patch("src.feishu.api_write._request_option", return_value=mock_option):
            remove_permission_member(client, "tok123", "docx", "openid", "ou_user1")

        call_args = client.drive.v1.permission_member.delete.call_args
        assert len(call_args.args) == 2
        assert call_args.args[1] is mock_option

    def test_raises_on_failure(self):
        client = MagicMock()
        resp = MagicMock()
        resp.success.return_value = False
        resp.code = 404
        resp.msg = "not found"
        client.drive.v1.permission_member.delete.return_value = resp

        with (
            patch("src.feishu.api_write._request_option", return_value=None),
            pytest.raises(FeishuAPIError, match="not found"),
        ):
            remove_permission_member(client, "tok", "docx", "openid", "ou_x")


# ---------------------------------------------------------------------------
# transfer_owner
# ---------------------------------------------------------------------------


class TestTransferOwner:
    def test_returns_success(self):
        client = MagicMock()
        client.drive.v1.permission_member.transfer_owner.return_value = _ok_response(
            SimpleNamespace()
        )

        with patch("src.feishu.api_write._request_option", return_value=None):
            result = transfer_owner(client, "tok123", "docx", "ou_new", "openid")

        assert result["success"] is True
        assert result["new_owner_id"] == "ou_new"
        client.drive.v1.permission_member.transfer_owner.assert_called_once()

    def test_passes_option(self):
        client = MagicMock()
        client.drive.v1.permission_member.transfer_owner.return_value = _ok_response(
            SimpleNamespace()
        )
        mock_option = MagicMock()

        with patch("src.feishu.api_write._request_option", return_value=mock_option):
            transfer_owner(client, "tok123", "docx", "ou_new", "openid")

        call_args = client.drive.v1.permission_member.transfer_owner.call_args
        assert len(call_args.args) == 2
        assert call_args.args[1] is mock_option

    def test_raises_on_failure(self):
        client = MagicMock()
        resp = MagicMock()
        resp.success.return_value = False
        resp.code = 403
        resp.msg = "forbidden"
        client.drive.v1.permission_member.transfer_owner.return_value = resp

        with (
            patch("src.feishu.api_write._request_option", return_value=None),
            pytest.raises(FeishuAPIError, match="forbidden"),
        ):
            transfer_owner(client, "tok", "docx", "ou_new")


# ---------------------------------------------------------------------------
# FeishuPermTool schema
# ---------------------------------------------------------------------------


class TestFeishuPermToolSchema:
    def test_tool_name_and_parameters(self):
        from src.agent.tools.feishu import FeishuPermTool

        tool = FeishuPermTool(client=MagicMock())
        assert tool.name == "feishu_perm"
        assert "action" in tool.parameters["properties"]
        assert "url" in tool.parameters["properties"]
        assert set(tool.parameters["required"]) == {"action", "url"}
        assert tool.parameters["properties"]["action"]["enum"] == [
            "list",
            "add",
            "update",
            "remove",
            "transfer",
        ]


# ---------------------------------------------------------------------------
# FeishuPermTool actions
# ---------------------------------------------------------------------------


class TestFeishuPermToolActions:
    @pytest.mark.asyncio
    async def test_list_action(self):
        from src.agent.tools.feishu import FeishuPermTool

        mock_client = MagicMock()
        mock_client.perm_list.return_value = [
            {"member_id": "ou_a", "perm": "edit"},
        ]
        tool = FeishuPermTool(client=mock_client)
        result = await tool.execute(action="list", url="https://feishu.cn/wiki/abc")

        mock_client.perm_list.assert_called_once_with("https://feishu.cn/wiki/abc")
        assert "ou_a" in result

    @pytest.mark.asyncio
    async def test_add_action(self):
        from src.agent.tools.feishu import FeishuPermTool

        mock_client = MagicMock()
        mock_client.perm_add.return_value = {"member_id": "ou_u1", "perm": "full_access"}
        tool = FeishuPermTool(client=mock_client)
        result = await tool.execute(
            action="add", url="https://feishu.cn/wiki/abc", member_id="ou_u1"
        )

        mock_client.perm_add.assert_called_once_with(
            "https://feishu.cn/wiki/abc", "ou_u1", perm="full_access"
        )
        assert "ou_u1" in result

    @pytest.mark.asyncio
    async def test_add_requires_member_id(self):
        from src.agent.tools.feishu import FeishuPermTool

        tool = FeishuPermTool(client=MagicMock())
        result = await tool.execute(action="add", url="https://feishu.cn/wiki/abc")
        assert "Error" in result and "member_id" in result

    @pytest.mark.asyncio
    async def test_update_requires_perm(self):
        from src.agent.tools.feishu import FeishuPermTool

        tool = FeishuPermTool(client=MagicMock())
        result = await tool.execute(
            action="update", url="https://feishu.cn/wiki/abc", member_id="ou_u1"
        )
        assert "Error" in result and "perm" in result

    @pytest.mark.asyncio
    async def test_transfer_action(self):
        from src.agent.tools.feishu import FeishuPermTool

        mock_client = MagicMock()
        mock_client.perm_transfer.return_value = {"success": True}
        tool = FeishuPermTool(client=mock_client)
        result = await tool.execute(
            action="transfer", url="https://feishu.cn/wiki/abc", new_owner="ou_new"
        )

        mock_client.perm_transfer.assert_called_once_with("https://feishu.cn/wiki/abc", "ou_new")
        assert "success" in result


# ---------------------------------------------------------------------------
# FeishuClient permission wrappers
# ---------------------------------------------------------------------------


class TestFeishuClientPermissionWrappers:
    def _make_client(self):
        with patch("src.feishu.client.make_client"), patch("src.feishu.client.get_access_token"):
            from src.feishu.client import FeishuClient

            client = FeishuClient(app_id="id", app_secret="secret")
        client.ensure_token = MagicMock()
        client._resolve_file_params = MagicMock(return_value=("doc_token", "docx"))
        return client

    def test_perm_list_resolves_target_once(self):
        client = self._make_client()

        with patch(
            "src.feishu.client.api_write.list_permission_members",
            return_value=[{"member_id": "ou_1"}],
        ) as api_call:
            result = client.perm_list("https://feishu.cn/wiki/abc")

        client.ensure_token.assert_called_once()
        client._resolve_file_params.assert_called_once_with("https://feishu.cn/wiki/abc")
        api_call.assert_called_once_with(client._client, "doc_token", "docx")
        assert result == [{"member_id": "ou_1"}]

    def test_perm_add_resolves_member_type(self):
        client = self._make_client()

        with patch(
            "src.feishu.client.api_write.add_permission_member",
            return_value={"member_id": "oc_1"},
        ) as api_call:
            result = client.perm_add("https://feishu.cn/wiki/abc", "oc_1", perm="edit")

        client.ensure_token.assert_called_once()
        client._resolve_file_params.assert_called_once_with("https://feishu.cn/wiki/abc")
        api_call.assert_called_once_with(
            client._client,
            "doc_token",
            "docx",
            "chatid",
            "oc_1",
            perm="edit",
        )
        assert result == {"member_id": "oc_1"}

    def test_perm_update_and_remove_share_member_target_resolution(self):
        client = self._make_client()

        with (
            patch("src.feishu.client.api_write.update_permission_member", return_value={}) as upd,
            patch("src.feishu.client.api_write.remove_permission_member", return_value=True) as rem,
        ):
            client.perm_update("https://feishu.cn/wiki/abc", "ou_1", "view")
            client.perm_remove("https://feishu.cn/wiki/abc", "ou_1")

        assert client.ensure_token.call_count == 2
        assert client._resolve_file_params.call_count == 2
        upd.assert_called_once_with(client._client, "doc_token", "docx", "openid", "ou_1", "view")
        rem.assert_called_once_with(client._client, "doc_token", "docx", "openid", "ou_1")

    def test_perm_transfer_resolves_owner_type(self):
        client = self._make_client()

        with patch(
            "src.feishu.client.api_write.transfer_owner",
            return_value={"success": True},
        ) as api_call:
            result = client.perm_transfer("https://feishu.cn/wiki/abc", "ou_owner")

        client.ensure_token.assert_called_once()
        client._resolve_file_params.assert_called_once_with("https://feishu.cn/wiki/abc")
        api_call.assert_called_once_with(
            client._client,
            "doc_token",
            "docx",
            "ou_owner",
            new_owner_type="openid",
        )
        assert result == {"success": True}
