"""Tests for Feishu comment write operations (add, resolve, delete, reply)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import lark_oapi as lark
import pytest

from src.feishu.api_write import add_comment, delete_comment, resolve_comment
from src.feishu.errors import FeishuAPIError

# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------


class TestAddComment:
    def test_add_comment(self):
        """Verify add_comment sends POST to correct endpoint."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.raw.content = json.dumps(
            {"data": {"comment_id": "c1", "content": "hello"}}
        ).encode()
        mock_client.request.return_value = mock_response

        with patch("src.feishu.api_write._request_option", return_value=None):
            result = add_comment(mock_client, "doc_token_1", "docx", "hello")

        mock_client.request.assert_called_once()
        req = mock_client.request.call_args[0][0]
        assert "/comments" in req.uri
        assert req.http_method == lark.HttpMethod.POST
        assert result["comment_id"] == "c1"

    def test_add_comment_with_reply_id(self):
        """Verify add_comment includes reply_id when provided."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.raw.content = json.dumps(
            {"data": {"comment_id": "c2", "reply_id": "c1"}}
        ).encode()
        mock_client.request.return_value = mock_response

        with patch("src.feishu.api_write._request_option", return_value=None):
            result = add_comment(mock_client, "doc_token_1", "docx", "reply text", reply_id="c1")

        req = mock_client.request.call_args[0][0]
        assert req.body["reply_id"] == "c1"
        assert result["reply_id"] == "c1"

    def test_add_comment_raises_on_failure(self):
        """Verify add_comment raises FeishuAPIError on API failure."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = False
        mock_response.code = 400
        mock_response.msg = "bad request"
        mock_client.request.return_value = mock_response

        with (
            patch("src.feishu.api_write._request_option", return_value=None),
            pytest.raises(FeishuAPIError, match="bad request"),
        ):
            add_comment(mock_client, "doc_token_1", "docx", "hello")


# ---------------------------------------------------------------------------
# resolve_comment
# ---------------------------------------------------------------------------


class TestResolveComment:
    def test_resolve_comment(self):
        """Verify resolve_comment sends PATCH with is_solved=True."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.raw.content = json.dumps(
            {"data": {"comment_id": "c1", "is_solved": True}}
        ).encode()
        mock_client.request.return_value = mock_response

        with patch("src.feishu.api_write._request_option", return_value=None):
            result = resolve_comment(mock_client, "doc_token_1", "docx", "c1", is_solved=True)

        req = mock_client.request.call_args[0][0]
        assert req.http_method == lark.HttpMethod.PATCH
        assert req.body["is_solved"] is True
        assert "c1" in req.uri
        assert result["is_solved"] is True


# ---------------------------------------------------------------------------
# delete_comment
# ---------------------------------------------------------------------------


class TestDeleteComment:
    def test_delete_comment(self):
        """Verify delete_comment sends DELETE to correct endpoint."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.raw.content = json.dumps({"data": {}}).encode()
        mock_client.request.return_value = mock_response

        with patch("src.feishu.api_write._request_option", return_value=None):
            result = delete_comment(mock_client, "doc_token_1", "docx", "c1")

        req = mock_client.request.call_args[0][0]
        assert req.http_method == lark.HttpMethod.DELETE
        assert "c1" in req.uri
        assert result is True

    def test_delete_comment_raises_on_failure(self):
        """Verify delete_comment raises on API failure."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = False
        mock_response.code = 404
        mock_response.msg = "not found"
        mock_client.request.return_value = mock_response

        with (
            patch("src.feishu.api_write._request_option", return_value=None),
            pytest.raises(FeishuAPIError, match="not found"),
        ):
            delete_comment(mock_client, "doc_token_1", "docx", "c_nonexistent")


# ---------------------------------------------------------------------------
# reply_to_comment (via add_comment with reply_id)
# ---------------------------------------------------------------------------


class TestReplyToComment:
    def test_reply_to_comment(self):
        """Verify replying to a comment passes reply_id in body."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.success.return_value = True
        mock_response.raw.content = json.dumps(
            {"data": {"comment_id": "c3", "reply_id": "c1"}}
        ).encode()
        mock_client.request.return_value = mock_response

        with patch("src.feishu.api_write._request_option", return_value=None):
            result = add_comment(mock_client, "doc_token_1", "docx", "my reply", reply_id="c1")

        req = mock_client.request.call_args[0][0]
        assert req.body["reply_id"] == "c1"
        assert result["comment_id"] == "c3"


# ---------------------------------------------------------------------------
# FeishuCommentsTool actions
# ---------------------------------------------------------------------------


class TestCommentToolAddAction:
    @pytest.mark.asyncio
    async def test_tool_add_action(self):
        """FeishuCommentsTool add action calls client.add_comment."""
        from src.agent.tools.feishu import FeishuCommentsTool

        mock_client = MagicMock()
        mock_client.add_comment.return_value = {"comment_id": "c1"}

        tool = FeishuCommentsTool(client=mock_client)
        result = await tool.execute(
            url="https://feishu.cn/wiki/abc", action="add", content="new comment"
        )

        mock_client.add_comment.assert_called_once_with("https://feishu.cn/wiki/abc", "new comment")
        assert "c1" in result

    @pytest.mark.asyncio
    async def test_tool_add_requires_content(self):
        """FeishuCommentsTool add action requires content."""
        from src.agent.tools.feishu import FeishuCommentsTool

        mock_client = MagicMock()
        tool = FeishuCommentsTool(client=mock_client)
        result = await tool.execute(url="https://feishu.cn/wiki/abc", action="add")

        assert "Error" in result
        mock_client.add_comment.assert_not_called()


class TestCommentToolResolveAction:
    @pytest.mark.asyncio
    async def test_tool_resolve_action(self):
        """FeishuCommentsTool resolve action calls client.resolve_comment."""
        from src.agent.tools.feishu import FeishuCommentsTool

        mock_client = MagicMock()
        mock_client.resolve_comment.return_value = {"comment_id": "c1", "is_solved": True}

        tool = FeishuCommentsTool(client=mock_client)
        result = await tool.execute(
            url="https://feishu.cn/wiki/abc", action="resolve", comment_id="c1"
        )

        mock_client.resolve_comment.assert_called_once_with("https://feishu.cn/wiki/abc", "c1")
        assert "is_solved" in result

    @pytest.mark.asyncio
    async def test_tool_resolve_requires_comment_id(self):
        """FeishuCommentsTool resolve action requires comment_id."""
        from src.agent.tools.feishu import FeishuCommentsTool

        mock_client = MagicMock()
        tool = FeishuCommentsTool(client=mock_client)
        result = await tool.execute(url="https://feishu.cn/wiki/abc", action="resolve")

        assert "Error" in result


class TestCommentToolDefaultReadCompat:
    @pytest.mark.asyncio
    async def test_tool_default_read_compat(self):
        """FeishuCommentsTool defaults to read when no action specified."""
        from src.agent.tools.feishu import FeishuCommentsTool

        mock_client = MagicMock()
        mock_client.read_comments.return_value = [{"comment_id": "c1", "content": "hi"}]

        tool = FeishuCommentsTool(client=mock_client)
        result = await tool.execute(url="https://feishu.cn/wiki/abc")

        mock_client.read_comments.assert_called_once_with("https://feishu.cn/wiki/abc")
        assert "c1" in result

    @pytest.mark.asyncio
    async def test_tool_explicit_read(self):
        """FeishuCommentsTool explicit read action works."""
        from src.agent.tools.feishu import FeishuCommentsTool

        mock_client = MagicMock()
        mock_client.read_comments.return_value = []

        tool = FeishuCommentsTool(client=mock_client)
        await tool.execute(url="https://feishu.cn/wiki/abc", action="read")

        mock_client.read_comments.assert_called_once()
