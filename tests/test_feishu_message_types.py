"""Tests for Feishu expanded message types (card, image, file, post)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# FeishuClient.send_card
# ---------------------------------------------------------------------------


class TestSendCard:
    def _make_client(self):
        with patch("src.feishu.client.make_client"), patch("src.feishu.client.get_access_token"):
            from src.feishu.client import FeishuClient

            client = FeishuClient(app_id="id", app_secret="secret")
        return client

    def test_send_card(self):
        """send_card sends an interactive message with header and markdown body."""
        client = self._make_client()
        client.ensure_token = MagicMock()

        with patch("src.feishu.client.api.send_message", return_value={"message_id": "m1"}) as mock:
            result = client.send_card("ou_user1", "Title", "**bold** text")

        mock.assert_called_once()
        args = mock.call_args
        assert args[0][1] == "ou_user1"
        assert args[0][2] == "interactive"  # msg_type
        content = json.loads(args[0][3])
        assert content["header"]["title"]["content"] == "Title"
        assert any(el["tag"] == "markdown" for el in content["elements"])
        assert result["message_id"] == "m1"

    def test_send_card_with_buttons(self):
        """send_card includes action buttons when provided."""
        client = self._make_client()
        client.ensure_token = MagicMock()

        buttons = [{"text": "Click Me", "url": "https://example.com"}]
        with patch("src.feishu.client.api.send_message", return_value={"message_id": "m2"}) as mock:
            client.send_card("oc_chat1", "Alert", "content", buttons=buttons)

        content = json.loads(mock.call_args[0][3])
        action_el = [el for el in content["elements"] if el["tag"] == "action"]
        assert len(action_el) == 1
        assert action_el[0]["actions"][0]["text"]["content"] == "Click Me"
        # oc_ prefix should use chat_id
        assert mock.call_args.kwargs["receive_id_type"] == "chat_id"

    def test_send_card_chat_id_type(self):
        """send_card uses chat_id for oc_ prefix."""
        client = self._make_client()
        client.ensure_token = MagicMock()

        with patch("src.feishu.client.api.send_message", return_value={}) as mock:
            client.send_card("oc_chat1", "T", "C")

        assert mock.call_args.kwargs["receive_id_type"] == "chat_id"

    def test_send_card_resolves_email(self):
        """send_card resolves email recipients before sending."""
        client = self._make_client()
        client.ensure_token = MagicMock()
        client.contact_find_by_email = MagicMock(return_value={"user_id": "u_123"})

        with patch("src.feishu.client.api.send_message", return_value={}) as mock:
            client.send_card("alice@example.com", "T", "C")

        assert mock.call_args[0][1] == "u_123"
        assert mock.call_args.kwargs["receive_id_type"] == "user_id"

    def test_send_card_resolves_unique_name(self):
        """send_card resolves a unique user name via search."""
        client = self._make_client()
        client.ensure_token = MagicMock()
        client.search_users = MagicMock(return_value=[{"name": "Alice", "open_id": "ou_alice"}])

        with patch("src.feishu.client.api.send_message", return_value={}) as mock:
            client.send_card("Alice", "T", "C")

        assert mock.call_args[0][1] == "ou_alice"
        assert mock.call_args.kwargs["receive_id_type"] == "open_id"

    def test_send_card_rejects_ambiguous_name(self):
        """send_card rejects ambiguous display names instead of guessing."""
        client = self._make_client()
        client.ensure_token = MagicMock()
        client.search_users = MagicMock(
            return_value=[
                {"name": "Alice", "open_id": "ou_alice1"},
                {"name": "Alice", "open_id": "ou_alice2"},
            ]
        )

        with pytest.raises(ValueError, match="ambiguous"):
            client.send_card("Alice", "T", "C")

    def test_send_card_rejects_non_exact_single_match(self):
        """send_card rejects a fuzzy single search hit instead of guessing."""
        client = self._make_client()
        client.ensure_token = MagicMock()
        client.search_users = MagicMock(
            return_value=[{"name": "Alice Smith", "open_id": "ou_alice"}]
        )

        with pytest.raises(ValueError, match="No Feishu user found"):
            client.send_card("Alice", "T", "C")


# ---------------------------------------------------------------------------
# FeishuClient.send_image
# ---------------------------------------------------------------------------


class TestSendImage:
    def _make_client(self):
        with patch("src.feishu.client.make_client"), patch("src.feishu.client.get_access_token"):
            from src.feishu.client import FeishuClient

            client = FeishuClient(app_id="id", app_secret="secret")
        return client

    def test_send_image(self):
        """send_image sends an image message with correct image_key."""
        client = self._make_client()
        client.ensure_token = MagicMock()

        with patch("src.feishu.client.api.send_message", return_value={"message_id": "m3"}) as mock:
            result = client.send_image("ou_user1", "img_v2_xxx")

        mock.assert_called_once()
        assert mock.call_args[0][2] == "image"
        content = json.loads(mock.call_args[0][3])
        assert content["image_key"] == "img_v2_xxx"
        assert mock.call_args.kwargs["receive_id_type"] == "open_id"
        assert result["message_id"] == "m3"

    def test_send_image_keeps_ascii_json_default(self):
        client = self._make_client()
        client.ensure_token = MagicMock()

        with patch("src.feishu.client.api.send_message", return_value={}) as mock:
            client.send_image("ou_user1", "图像")

        assert mock.call_args[0][3] == '{"image_key": "\\u56fe\\u50cf"}'


# ---------------------------------------------------------------------------
# FeishuClient.send_file
# ---------------------------------------------------------------------------


class TestSendFile:
    def _make_client(self):
        with patch("src.feishu.client.make_client"), patch("src.feishu.client.get_access_token"):
            from src.feishu.client import FeishuClient

            client = FeishuClient(app_id="id", app_secret="secret")
        return client

    def test_send_file(self):
        """send_file sends a file message with correct file_key."""
        client = self._make_client()
        client.ensure_token = MagicMock()

        with patch("src.feishu.client.api.send_message", return_value={"message_id": "m4"}) as mock:
            result = client.send_file("ou_user1", "file_v2_xxx")

        mock.assert_called_once()
        assert mock.call_args[0][2] == "file"
        content = json.loads(mock.call_args[0][3])
        assert content["file_key"] == "file_v2_xxx"
        assert mock.call_args.kwargs["receive_id_type"] == "open_id"
        assert result["message_id"] == "m4"

    def test_send_file_keeps_ascii_json_default(self):
        client = self._make_client()
        client.ensure_token = MagicMock()

        with patch("src.feishu.client.api.send_message", return_value={}) as mock:
            client.send_file("ou_user1", "文件")

        assert mock.call_args[0][3] == '{"file_key": "\\u6587\\u4ef6"}'


# ---------------------------------------------------------------------------
# FeishuClient.send_post
# ---------------------------------------------------------------------------


class TestSendPost:
    def _make_client(self):
        with patch("src.feishu.client.make_client"), patch("src.feishu.client.get_access_token"):
            from src.feishu.client import FeishuClient

            client = FeishuClient(app_id="id", app_secret="secret")
        return client

    def test_send_post(self):
        """send_post sends a rich text message with title and content."""
        client = self._make_client()
        client.ensure_token = MagicMock()

        post_content = [
            [
                {"tag": "text", "text": "hello "},
                {"tag": "a", "href": "https://x.com", "text": "link"},
            ]
        ]

        with patch("src.feishu.client.api.send_message", return_value={"message_id": "m5"}) as mock:
            result = client.send_post("ou_user1", "Post Title", post_content)

        mock.assert_called_once()
        assert mock.call_args[0][2] == "post"
        content = json.loads(mock.call_args[0][3])
        assert content["zh_cn"]["title"] == "Post Title"
        assert len(content["zh_cn"]["content"]) == 1
        assert content["zh_cn"]["content"][0][0]["tag"] == "text"
        assert mock.call_args.kwargs["receive_id_type"] == "open_id"
        assert result["message_id"] == "m5"

    def test_send_post_keeps_unicode_json(self):
        client = self._make_client()
        client.ensure_token = MagicMock()

        with patch("src.feishu.client.api.send_message", return_value={}) as mock:
            client.send_post("ou_user1", "标题", [[{"tag": "text", "text": "你好"}]])

        assert "\\u" not in mock.call_args[0][3]
        assert "标题" in mock.call_args[0][3]
        assert "你好" in mock.call_args[0][3]


# ---------------------------------------------------------------------------
# FeishuSendTool msg_type routing
# ---------------------------------------------------------------------------


class TestToolTextDefault:
    @pytest.mark.asyncio
    async def test_tool_text_default(self):
        """FeishuSendTool defaults to text msg_type."""
        from src.agent.tools.feishu import FeishuSendTool

        mock_client = MagicMock()
        mock_client.send_message.return_value = {"message_id": "m1"}

        tool = FeishuSendTool(client=mock_client)
        result = await tool.execute(user="ou_user1", message="hello")

        mock_client.send_message.assert_called_once_with("ou_user1", "hello")
        assert "m1" in result

    @pytest.mark.asyncio
    async def test_tool_text_requires_message(self):
        """FeishuSendTool text type requires message."""
        from src.agent.tools.feishu import FeishuSendTool

        mock_client = MagicMock()
        tool = FeishuSendTool(client=mock_client)
        result = await tool.execute(user="ou_user1", msg_type="text")

        assert "Error" in result
        mock_client.send_message.assert_not_called()


class TestToolCardAction:
    @pytest.mark.asyncio
    async def test_tool_card_action(self):
        """FeishuSendTool card msg_type calls send_card."""
        from src.agent.tools.feishu import FeishuSendTool

        mock_client = MagicMock()
        mock_client.send_card.return_value = {"message_id": "m2"}

        tool = FeishuSendTool(client=mock_client)
        result = await tool.execute(
            user="ou_user1",
            msg_type="card",
            title="Alert",
            message="Something happened",
        )

        mock_client.send_card.assert_called_once()
        call_args = mock_client.send_card.call_args
        assert call_args[0][0] == "ou_user1"
        assert call_args[0][1] == "Alert"
        assert call_args[0][2] == "Something happened"
        assert "m2" in result

    @pytest.mark.asyncio
    async def test_tool_card_requires_message(self):
        """FeishuSendTool card type requires message."""
        from src.agent.tools.feishu import FeishuSendTool

        mock_client = MagicMock()
        tool = FeishuSendTool(client=mock_client)
        result = await tool.execute(user="ou_user1", msg_type="card", title="Alert")

        assert "Error" in result


class TestToolImageAction:
    @pytest.mark.asyncio
    async def test_tool_image_action(self):
        """FeishuSendTool image msg_type calls send_image."""
        from src.agent.tools.feishu import FeishuSendTool

        mock_client = MagicMock()
        mock_client.send_image.return_value = {"message_id": "m3"}

        tool = FeishuSendTool(client=mock_client)
        result = await tool.execute(user="ou_user1", msg_type="image", image_key="img_v2_xxx")

        mock_client.send_image.assert_called_once_with("ou_user1", "img_v2_xxx")
        assert "m3" in result

    @pytest.mark.asyncio
    async def test_tool_image_requires_key(self):
        """FeishuSendTool image type requires image_key."""
        from src.agent.tools.feishu import FeishuSendTool

        mock_client = MagicMock()
        tool = FeishuSendTool(client=mock_client)
        result = await tool.execute(user="ou_user1", msg_type="image")

        assert "Error" in result

    @pytest.mark.asyncio
    async def test_tool_file_action(self):
        """FeishuSendTool file msg_type calls send_file."""
        from src.agent.tools.feishu import FeishuSendTool

        mock_client = MagicMock()
        mock_client.send_file.return_value = {"message_id": "m4"}

        tool = FeishuSendTool(client=mock_client)
        result = await tool.execute(user="ou_user1", msg_type="file", file_key="file_v2_xxx")

        mock_client.send_file.assert_called_once_with("ou_user1", "file_v2_xxx")
        assert "m4" in result

    @pytest.mark.asyncio
    async def test_tool_post_action(self):
        """FeishuSendTool post msg_type calls send_post."""
        from src.agent.tools.feishu import FeishuSendTool

        mock_client = MagicMock()
        mock_client.send_post.return_value = {"message_id": "m5"}

        tool = FeishuSendTool(client=mock_client)
        result = await tool.execute(
            user="ou_user1", msg_type="post", title="Post Title", message="hello world"
        )

        mock_client.send_post.assert_called_once()
        call_args = mock_client.send_post.call_args
        assert call_args[0][0] == "ou_user1"
        assert call_args[0][1] == "Post Title"
        # Content should be auto-wrapped into post format
        assert call_args[0][2] == [[{"tag": "text", "text": "hello world"}]]
        assert "m5" in result
