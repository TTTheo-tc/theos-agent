"""Tests for Feishu Chat/Group management API and FeishuChatTool."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.feishu import api_chat

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


def _mock_lark_client():
    """Return a mock lark.Client with im.v1 sub-services wired up."""
    client = MagicMock()
    client.im.v1.chat.create = MagicMock()
    client.im.v1.chat.get = MagicMock()
    client.im.v1.chat.update = MagicMock()
    client.im.v1.chat_members.get = MagicMock()
    client.im.v1.chat_members.create = MagicMock()
    client.im.v1.chat_members.delete = MagicMock()
    client.im.v1.message.list = MagicMock()
    client.im.v1.pin.create = MagicMock()
    client.im.v1.message_reaction.create = MagicMock()
    return client


# ---------------------------------------------------------------------------
# api_chat unit tests
# ---------------------------------------------------------------------------


class TestCreateChat:
    def test_create_chat(self):
        client = _mock_lark_client()
        data = SimpleNamespace(chat_id="oc_123", name="Test Chat")
        client.im.v1.chat.create.return_value = _ok_response(data)

        with (
            patch("src.feishu.api_chat._request_option", return_value=None),
            patch(
                "src.feishu.api_chat._unmarshal",
                return_value={"chat_id": "oc_123", "name": "Test Chat"},
            ),
        ):
            result = api_chat.create_chat(client, "Test Chat", description="desc")

        assert result["chat_id"] == "oc_123"
        client.im.v1.chat.create.assert_called_once()

    def test_create_chat_with_members(self):
        client = _mock_lark_client()
        data = SimpleNamespace(chat_id="oc_456")
        client.im.v1.chat.create.return_value = _ok_response(data)

        with (
            patch("src.feishu.api_chat._request_option", return_value=None),
            patch("src.feishu.api_chat._unmarshal", return_value={"chat_id": "oc_456"}),
        ):
            result = api_chat.create_chat(client, "Group", user_ids=["ou_a", "ou_b"])

        assert result["chat_id"] == "oc_456"

    def test_create_chat_passes_request_option(self):
        client = _mock_lark_client()
        data = SimpleNamespace(chat_id="oc_789")
        client.im.v1.chat.create.return_value = _ok_response(data)
        option = object()

        with (
            patch("src.feishu.api_chat._request_option", return_value=option),
            patch("src.feishu.api_chat._unmarshal", return_value={"chat_id": "oc_789"}),
        ):
            api_chat.create_chat(client, "Group")

        assert client.im.v1.chat.create.call_args.args[1] is option


class TestGetChat:
    def test_get_chat(self):
        client = _mock_lark_client()
        data = SimpleNamespace(chat_id="oc_123", name="My Chat", owner_id="ou_owner")
        client.im.v1.chat.get.return_value = _ok_response(data)

        with (
            patch("src.feishu.api_chat._request_option", return_value=None),
            patch(
                "src.feishu.api_chat._unmarshal",
                return_value={"chat_id": "oc_123", "name": "My Chat"},
            ),
        ):
            result = api_chat.get_chat(client, "oc_123")

        assert result["name"] == "My Chat"


class TestListChatMembers:
    def test_single_page(self):
        client = _mock_lark_client()
        data = SimpleNamespace(
            items=[{"member_id": "ou_a"}, {"member_id": "ou_b"}],
            has_more=False,
            page_token=None,
        )
        client.im.v1.chat_members.get.return_value = _ok_response(data)

        with (
            patch("src.feishu.api_chat._request_option", return_value=None),
            patch(
                "src.feishu.api_chat._unmarshal",
                return_value=[{"member_id": "ou_a"}, {"member_id": "ou_b"}],
            ),
        ):
            result = api_chat.list_chat_members(client, "oc_123")

        assert len(result) == 2

    def test_single_page_uses_one_arg_when_no_option(self):
        client = _mock_lark_client()
        data = SimpleNamespace(items=[], has_more=False, page_token=None)
        client.im.v1.chat_members.get.return_value = _ok_response(data)

        with patch("src.feishu.api_chat._request_option", return_value=None):
            api_chat.list_chat_members(client, "oc_123")

        assert len(client.im.v1.chat_members.get.call_args.args) == 1

    def test_paginated(self):
        client = _mock_lark_client()
        page1 = SimpleNamespace(
            items=[{"member_id": "ou_a"}],
            has_more=True,
            page_token="page2",
        )
        page2 = SimpleNamespace(
            items=[{"member_id": "ou_b"}],
            has_more=False,
            page_token=None,
        )
        client.im.v1.chat_members.get.side_effect = [
            _ok_response(page1),
            _ok_response(page2),
        ]

        with (
            patch("src.feishu.api_chat._request_option", return_value=None),
            patch(
                "src.feishu.api_chat._unmarshal",
                side_effect=[
                    [{"member_id": "ou_a"}],
                    [{"member_id": "ou_b"}],
                ],
            ),
        ):
            result = api_chat.list_chat_members(client, "oc_123")

        assert len(result) == 2
        assert client.im.v1.chat_members.get.call_count == 2


class TestAddChatMembers:
    def test_add_members(self):
        client = _mock_lark_client()
        data = SimpleNamespace()
        client.im.v1.chat_members.create.return_value = _ok_response(data)

        with (
            patch("src.feishu.api_chat._request_option", return_value=None),
            patch("src.feishu.api_chat._unmarshal", return_value={"invalid_id_list": []}),
        ):
            result = api_chat.add_chat_members(client, "oc_123", ["ou_a", "ou_b"])

        client.im.v1.chat_members.create.assert_called_once()
        assert result == {"invalid_id_list": []}


class TestRemoveChatMembers:
    def test_remove_members_passes_request_option(self):
        client = _mock_lark_client()
        data = SimpleNamespace()
        client.im.v1.chat_members.delete.return_value = _ok_response(data)
        option = object()

        with (
            patch("src.feishu.api_chat._request_option", return_value=option),
            patch("src.feishu.api_chat._unmarshal", return_value={"invalid_id_list": []}),
        ):
            api_chat.remove_chat_members(client, "oc_123", ["ou_a"])

        assert client.im.v1.chat_members.delete.call_args.args[1] is option


class TestListChatMessages:
    def test_list_messages(self):
        client = _mock_lark_client()
        data = SimpleNamespace(
            items=[{"message_id": "m1", "msg_type": "text"}],
            has_more=False,
            page_token=None,
        )
        client.im.v1.message.list.return_value = _ok_response(data)

        with (
            patch("src.feishu.api_chat._request_option", return_value=None),
            patch(
                "src.feishu.api_chat._unmarshal",
                return_value=[{"message_id": "m1", "msg_type": "text"}],
            ),
        ):
            result = api_chat.list_chat_messages(client, "oc_123")

        assert len(result) == 1
        assert result[0]["message_id"] == "m1"

    def test_list_messages_passes_request_option(self):
        client = _mock_lark_client()
        data = SimpleNamespace(items=[], has_more=False, page_token=None)
        client.im.v1.message.list.return_value = _ok_response(data)
        option = object()

        with patch("src.feishu.api_chat._request_option", return_value=option):
            api_chat.list_chat_messages(client, "oc_123")

        assert client.im.v1.message.list.call_args.args[1] is option


class TestPinMessage:
    def test_pin(self):
        client = _mock_lark_client()
        client.im.v1.pin.create.return_value = _ok_response(SimpleNamespace())

        with patch("src.feishu.api_chat._request_option", return_value=None):
            result = api_chat.pin_message(client, "msg_123")

        assert result is True
        client.im.v1.pin.create.assert_called_once()


class TestAddReaction:
    def test_react(self):
        client = _mock_lark_client()
        client.im.v1.message_reaction.create.return_value = _ok_response(SimpleNamespace())

        with patch("src.feishu.api_chat._request_option", return_value=None):
            result = api_chat.add_reaction(client, "msg_123", "THUMBSUP")

        assert result is True
        client.im.v1.message_reaction.create.assert_called_once()


# ---------------------------------------------------------------------------
# FeishuChatTool schema
# ---------------------------------------------------------------------------


class TestFeishuChatToolSchema:
    def test_tool_name_and_parameters(self):
        from src.agent.tools.feishu import FeishuChatTool

        tool = FeishuChatTool(client=MagicMock())
        assert tool.name == "feishu_chat"
        assert "action" in tool.parameters["properties"]
        assert "chat_id" in tool.parameters["properties"]
        assert tool.parameters["required"] == ["action"]
        actions = tool.parameters["properties"]["action"]["enum"]
        assert "create" in actions
        assert "messages" in actions
        assert "pin" in actions
        assert "react" in actions


# ---------------------------------------------------------------------------
# FeishuChatTool actions
# ---------------------------------------------------------------------------


class TestFeishuChatToolActions:
    @pytest.mark.asyncio
    async def test_create_action(self):
        from src.agent.tools.feishu import FeishuChatTool

        mock_client = MagicMock()
        mock_client.chat_create.return_value = {"chat_id": "oc_new", "name": "Test"}
        tool = FeishuChatTool(client=mock_client)
        result = await tool.execute(action="create", name="Test", user_ids=["ou_a"])

        mock_client.chat_create.assert_called_once_with("Test", description="", user_ids=["ou_a"])
        assert "oc_new" in result

    @pytest.mark.asyncio
    async def test_create_requires_name(self):
        from src.agent.tools.feishu import FeishuChatTool

        tool = FeishuChatTool(client=MagicMock())
        result = await tool.execute(action="create")
        assert "Error" in result and "name" in result

    @pytest.mark.asyncio
    async def test_messages_action(self):
        from src.agent.tools.feishu import FeishuChatTool

        mock_client = MagicMock()
        mock_client.chat_messages.return_value = [{"message_id": "m1"}]
        tool = FeishuChatTool(client=mock_client)
        result = await tool.execute(action="messages", chat_id="oc_123")

        mock_client.chat_messages.assert_called_once_with("oc_123")
        assert "m1" in result

    @pytest.mark.asyncio
    async def test_messages_requires_chat_id(self):
        from src.agent.tools.feishu import FeishuChatTool

        tool = FeishuChatTool(client=MagicMock())
        result = await tool.execute(action="messages")
        assert "Error" in result and "chat_id" in result

    @pytest.mark.asyncio
    async def test_info_action(self):
        from src.agent.tools.feishu import FeishuChatTool

        mock_client = MagicMock()
        mock_client.chat_info.return_value = {"chat_id": "oc_123", "name": "My Chat"}
        tool = FeishuChatTool(client=mock_client)
        result = await tool.execute(action="info", chat_id="oc_123")

        mock_client.chat_info.assert_called_once_with("oc_123")
        assert "My Chat" in result

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        from src.agent.tools.feishu import FeishuChatTool

        tool = FeishuChatTool(client=MagicMock())
        result = await tool.execute(action="invalid")
        assert "Error" in result and "unknown action" in result
