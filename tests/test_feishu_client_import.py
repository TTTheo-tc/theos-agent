"""Tests for FeishuClient import_file orchestration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_client():
    with patch("src.feishu.client.make_client"), patch("src.feishu.client.get_access_token"):
        from src.feishu.client import FeishuClient

        client = FeishuClient(app_id="id", app_secret="secret")
    client.ensure_token = MagicMock()
    return client


def test_call_api_refreshes_token_and_passes_client():
    client = _make_client()
    api_func = MagicMock(return_value={"ok": True})

    result = client._call_api(api_func, "arg", option=True)

    client.ensure_token.assert_called_once()
    api_func.assert_called_once_with(client._client, "arg", option=True)
    assert result == {"ok": True}


def test_import_file_uploads_creates_task_and_returns_success():
    client = _make_client()

    with (
        patch("src.feishu.client.api_write.upload_media_for_import", return_value="media_1") as upload,
        patch("src.feishu.client.api_write.create_import_task", return_value="ticket_1") as create,
        patch(
            "src.feishu.client.api_write.get_import_task_result",
            return_value={"job_status": 0, "token": "doc_1", "type": "docx"},
        ) as poll,
    ):
        result = client.import_file(
            "/tmp/report.md",
            file_name="Report",
            target_type="docx",
            mount_key="mount1",
        )

    upload.assert_called_once_with("/tmp/report.md", "report.md", "docx", "md")
    create.assert_called_once_with("md", "media_1", "docx", "Report", "mount1")
    poll.assert_called_once_with("ticket_1")
    assert result == {"job_status": 0, "token": "doc_1", "type": "docx"}


def test_poll_import_result_retries_and_times_out():
    client = _make_client()

    with (
        patch(
            "src.feishu.client.api_write.get_import_task_result",
            return_value={"job_status": 2},
        ),
        patch("src.feishu.client.time.sleep") as sleep,
        pytest.raises(TimeoutError, match="Import timed out after 0.2s"),
    ):
        client._poll_import_result("ticket_1", poll_interval=0.1, max_polls=2)

    assert sleep.call_count == 2


def test_poll_import_result_raises_failed_status():
    client = _make_client()

    with (
        patch(
            "src.feishu.client.api_write.get_import_task_result",
            return_value={"job_status": 3, "job_error_msg": "bad"},
        ),
        pytest.raises(RuntimeError, match="Import failed with status 3"),
    ):
        client._poll_import_result("ticket_1", poll_interval=0.1, max_polls=2)


def test_import_file_moves_success_result_to_wiki_and_polls_task():
    client = _make_client()
    client.info_page = MagicMock(return_value=("", {"space_id": "space_1"}))

    with (
        patch("src.feishu.client.api_write.upload_media_for_import", return_value="media_1"),
        patch("src.feishu.client.api_write.create_import_task", return_value="ticket_1"),
        patch(
            "src.feishu.client.api_write.get_import_task_result",
            return_value={"job_status": 0, "token": "doc_1", "type": "sheet"},
        ),
        patch(
            "src.feishu.client.api_write.move_docs_to_wiki",
            return_value={"wiki_token": "wiki_new", "task_id": "task_1"},
        ) as move,
        patch(
            "src.feishu.client.api_write.get_wiki_task_result",
            side_effect=[{"status": "running"}, {"status": "done"}],
        ) as task_poll,
        patch("src.feishu.client.time.sleep") as sleep,
    ):
        result = client.import_file(
            "/tmp/report.xlsx",
            target_type="sheet",
            wiki_parent_url="https://feishu.cn/wiki/wiki_parent",
        )

    move.assert_called_once_with(client._client, "space_1", "wiki_parent", "sheet", "doc_1")
    assert task_poll.call_count == 2
    sleep.assert_called_once_with(1.0)
    assert result["wiki_token"] == "wiki_new"


def test_move_import_to_wiki_requires_space_id():
    client = _make_client()
    client.info_page = MagicMock(return_value=("", {}))

    with pytest.raises(ValueError, match="Cannot determine space_id"):
        client._move_import_to_wiki(
            {"token": "doc_1"},
            "docx",
            "https://feishu.cn/wiki/wiki_parent",
            poll_interval=0.1,
            max_polls=1,
        )


def test_move_import_to_wiki_uses_target_type_when_result_type_missing():
    client = _make_client()
    client.info_page = MagicMock(return_value=("", {"space_id": "space_1"}))

    with patch(
        "src.feishu.client.api_write.move_docs_to_wiki",
        return_value={"wiki_token": "wiki_new"},
    ) as move:
        client._move_import_to_wiki(
            {"token": "doc_1"},
            "docx",
            "https://feishu.cn/wiki/wiki_parent",
            poll_interval=0.1,
            max_polls=1,
        )

    move.assert_called_once_with(client._client, "space_1", "wiki_parent", "docx", "doc_1")


def test_poll_wiki_task_exhaustion_matches_prior_silent_return():
    client = _make_client()

    with (
        patch(
            "src.feishu.client.api_write.get_wiki_task_result",
            return_value={"status": "running"},
        ) as poll,
        patch("src.feishu.client.time.sleep") as sleep,
    ):
        result = client._poll_wiki_task("task_1", poll_interval=0.1, max_polls=2)

    assert result is None
    assert poll.call_count == 2
    assert sleep.call_count == 2


def test_move_import_to_wiki_rejects_non_wiki_url():
    client = _make_client()

    with pytest.raises(ValueError, match="Cannot parse wiki URL"):
        client._move_import_to_wiki(
            {"token": "doc_1"},
            "docx",
            "https://feishu.cn/docx/doc_1",
            poll_interval=0.1,
            max_polls=1,
        )
