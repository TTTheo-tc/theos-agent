"""Tests for Feishu file import HTTP write helpers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.feishu import api_write


class _Resp:
    def __init__(self, payload: dict, *, status_code: int = 200, text: str = "resp"):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self) -> dict:
        return self._payload


def _ok(data: dict) -> _Resp:
    return _Resp({"code": 0, "data": data})


def test_upload_media_for_import_posts_multipart_without_content_type(tmp_path):
    file_path = tmp_path / "report.md"
    file_path.write_text("# report", encoding="utf-8")
    request = MagicMock(return_value=_ok({"file_token": "file_tok"}))

    with (
        patch("src.feishu.api_write._rate_limiter.wait"),
        patch(
            "src.feishu.api.feishu_auth_header",
            return_value={"Authorization": "Bearer t", "Content-Type": "application/json"},
        ),
        patch("httpx.request", request),
    ):
        result = api_write.upload_media_for_import(
            str(file_path),
            "report.md",
            "docx",
            "md",
        )

    assert result == "file_tok"
    call = request.call_args
    assert call.args[:2] == (
        "POST",
        "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all",
    )
    assert call.kwargs["headers"] == {"Authorization": "Bearer t"}
    assert call.kwargs["data"]["file_name"] == "report.md"
    assert call.kwargs["data"]["size"] == str(len("# report"))
    assert json.loads(call.kwargs["data"]["extra"]) == {
        "obj_type": "docx",
        "file_extension": "md",
    }
    assert call.kwargs["files"] == {"file": ("report.md", b"# report")}


def test_create_import_task_posts_expected_body():
    request = MagicMock(return_value=_ok({"ticket": "ticket_1"}))

    with (
        patch("src.feishu.api_write._rate_limiter.wait"),
        patch("src.feishu.api.feishu_auth_header", return_value={"Authorization": "Bearer t"}),
        patch("httpx.request", request),
    ):
        result = api_write.create_import_task(
            "md",
            "file_tok",
            "docx",
            "Report",
            mount_key="mount1",
        )

    assert result == "ticket_1"
    call = request.call_args
    assert call.args[:2] == (
        "POST",
        "https://open.feishu.cn/open-apis/drive/v1/import_tasks",
    )
    assert call.kwargs["headers"] == {"Authorization": "Bearer t"}
    assert call.kwargs["json"] == {
        "file_extension": "md",
        "file_token": "file_tok",
        "type": "docx",
        "file_name": "Report",
        "point": {"mount_type": 1, "mount_key": "mount1"},
    }


def test_get_import_task_result_returns_result_payload():
    request = MagicMock(return_value=_ok({"result": {"job_status": 0, "token": "doc_tok"}}))

    with (
        patch("src.feishu.api_write._rate_limiter.wait"),
        patch("src.feishu.api.feishu_auth_header", return_value={"Authorization": "Bearer t"}),
        patch("httpx.request", request),
    ):
        result = api_write.get_import_task_result("ticket_1")

    assert result == {"job_status": 0, "token": "doc_tok"}
    assert request.call_args.args[:2] == (
        "GET",
        "https://open.feishu.cn/open-apis/drive/v1/import_tasks/ticket_1",
    )


def test_move_docs_to_wiki_posts_expected_body():
    request = MagicMock(return_value=_ok({"wiki_token": "wiki_1", "task_id": "task_1"}))
    client = MagicMock()

    with (
        patch("src.feishu.api_write._rate_limiter.wait"),
        patch("src.feishu.api.feishu_auth_header", return_value={"Authorization": "Bearer t"}),
        patch("httpx.request", request),
    ):
        result = api_write.move_docs_to_wiki(
            client,
            "space_1",
            "parent_1",
            "docx",
            "doc_1",
        )

    assert result == {"wiki_token": "wiki_1", "task_id": "task_1"}
    call = request.call_args
    assert call.args[:2] == (
        "POST",
        "https://open.feishu.cn/open-apis/wiki/v2/spaces/space_1/nodes/move_docs_to_wiki",
    )
    assert call.kwargs["json"] == {
        "parent_wiki_token": "parent_1",
        "obj_type": "docx",
        "obj_token": "doc_1",
    }


def test_get_wiki_task_result_passes_task_type_param():
    request = MagicMock(return_value=_ok({"status": "done", "wiki_token": "wiki_1"}))

    with (
        patch("src.feishu.api_write._rate_limiter.wait"),
        patch("src.feishu.api.feishu_auth_header", return_value={"Authorization": "Bearer t"}),
        patch("httpx.request", request),
    ):
        result = api_write.get_wiki_task_result("task_1")

    assert result == {"status": "done", "wiki_token": "wiki_1"}
    assert request.call_args.args[:2] == (
        "GET",
        "https://open.feishu.cn/open-apis/wiki/v2/tasks/task_1",
    )
    assert request.call_args.kwargs["params"] == {"task_type": "move"}


def test_http_helper_preserves_existing_error_messages():
    request = MagicMock(return_value=_Resp({"code": 0, "data": {}}, status_code=500, text="bad"))

    with (
        patch("src.feishu.api.feishu_auth_header", return_value={}),
        patch("httpx.request", request),
        pytest.raises(RuntimeError, match="create_import_task HTTP 500: bad"),
    ):
        api_write._feishu_http_json("POST", "https://example.invalid", "create_import_task")

    request.return_value = _Resp({"code": 999, "data": {}}, text="api bad")
    with (
        patch("src.feishu.api.feishu_auth_header", return_value={}),
        patch("httpx.request", request),
        pytest.raises(RuntimeError, match="create_import_task API error: code=999, api bad"),
    ):
        api_write._feishu_http_json("POST", "https://example.invalid", "create_import_task")
