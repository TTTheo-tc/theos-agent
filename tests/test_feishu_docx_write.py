"""Tests for Feishu wiki/docx write operations in api_write."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import lark_oapi as lark

from src.feishu import api_write


def _ok_response(data_obj):
    resp = MagicMock()
    resp.success.return_value = True
    resp.code = 0
    resp.msg = "ok"
    resp.data = data_obj
    return resp


class TestWikiNodeWrite:
    def test_create_wiki_node_passes_option_and_request_shape(self):
        client = MagicMock()
        node = object()
        client.wiki.v2.space_node.create.return_value = _ok_response(
            SimpleNamespace(node=node)
        )
        option = object()

        with (
            patch("src.feishu.api_write._rate_limiter.wait"),
            patch("src.feishu.api_write._request_option", return_value=option),
            patch("src.feishu.api_write._unmarshal", return_value={"node_token": "nt1"}),
        ):
            result = api_write.create_wiki_node(
                client,
                "sp1",
                parent_node_token="parent1",
                title="Title",
                obj_type="docx",
            )

        call_args = client.wiki.v2.space_node.create.call_args
        assert len(call_args.args) == 2
        assert call_args.args[1] is option
        request = call_args.args[0]
        assert request.space_id == "sp1"
        assert request.request_body.obj_type == "docx"
        assert request.request_body.node_type == "origin"
        assert request.request_body.parent_node_token == "parent1"
        assert request.request_body.title == "Title"
        assert result == {"node_token": "nt1"}

    def test_delete_wiki_node_uses_raw_request_option_and_user_token(self):
        client = MagicMock()
        response = MagicMock()
        response.success.return_value = True
        response.raw.content = json.dumps({"data": {"node_token": "nt1"}}).encode()
        client.request.return_value = response
        option = object()

        with (
            patch("src.feishu.api_write._rate_limiter.wait"),
            patch("src.feishu.api_write._request_option", return_value=option),
            patch("src.feishu.api_write.ctx_current_token") as mock_ctx,
        ):
            mock_ctx.get.return_value = "user-token"
            result = api_write.delete_wiki_node(client, "sp1", "nt1")

        call_args = client.request.call_args
        assert len(call_args.args) == 2
        assert call_args.args[1] is option
        request = call_args.args[0]
        assert request.http_method == lark.HttpMethod.DELETE
        assert request.uri == "/open-apis/wiki/v2/spaces/sp1/nodes/nt1"
        assert request.token_types == {lark.AccessTokenType.USER}
        assert result == {"node_token": "nt1"}


class TestDocxBlockWrite:
    def test_create_descendant_blocks_passes_option_and_request_shape(self):
        client = MagicMock()
        client.docx.v1.document_block_descendant.create.return_value = _ok_response(
            SimpleNamespace()
        )
        option = object()
        children_ids = ["b1"]
        descendants = [{"block_id": "b1"}]

        with (
            patch("src.feishu.api_write._rate_limiter.wait"),
            patch("src.feishu.api_write._request_option", return_value=option),
            patch("src.feishu.api_write._unmarshal", return_value={"ok": True}),
        ):
            result = api_write.create_descendant_blocks(
                client, "doc1", "root", children_ids, descendants, index=3
            )

        call_args = client.docx.v1.document_block_descendant.create.call_args
        assert len(call_args.args) == 2
        assert call_args.args[1] is option
        request = call_args.args[0]
        assert request.document_id == "doc1"
        assert request.block_id == "root"
        assert request.document_revision_id == -1
        assert request.request_body.children_id == children_ids
        assert request.request_body.descendants == descendants
        assert request.request_body.index == 3
        assert result == {"ok": True}

    def test_create_block_children_without_option_uses_one_arg(self):
        client = MagicMock()
        client.docx.v1.document_block_children.create.return_value = _ok_response(
            SimpleNamespace()
        )
        children = [{"block_id": "b1"}]

        with (
            patch("src.feishu.api_write._rate_limiter.wait"),
            patch("src.feishu.api_write._request_option", return_value=None),
            patch("src.feishu.api_write._unmarshal", return_value={"ok": True}),
        ):
            result = api_write.create_block_children(
                client, "doc1", "root", children, index=2
            )

        call_args = client.docx.v1.document_block_children.create.call_args
        assert len(call_args.args) == 1
        request = call_args.args[0]
        assert request.document_id == "doc1"
        assert request.block_id == "root"
        assert request.document_revision_id == -1
        assert request.request_body.children == children
        assert request.request_body.index == 2
        assert result == {"ok": True}

    def test_delete_blocks_passes_option_and_request_shape(self):
        client = MagicMock()
        client.docx.v1.document_block_children.batch_delete.return_value = _ok_response(
            SimpleNamespace()
        )
        option = object()

        with (
            patch("src.feishu.api_write._rate_limiter.wait"),
            patch("src.feishu.api_write._request_option", return_value=option),
            patch("src.feishu.api_write._unmarshal", return_value={"deleted": True}),
        ):
            result = api_write.delete_blocks(client, "doc1", "root", 1, 4)

        call_args = client.docx.v1.document_block_children.batch_delete.call_args
        assert len(call_args.args) == 2
        assert call_args.args[1] is option
        request = call_args.args[0]
        assert request.document_id == "doc1"
        assert request.block_id == "root"
        assert request.document_revision_id == -1
        assert request.request_body.start_index == 1
        assert request.request_body.end_index == 4
        assert result == {"deleted": True}
