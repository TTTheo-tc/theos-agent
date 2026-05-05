"""Tests for Feishu block PATCH write helpers."""

from __future__ import annotations

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


def test_update_block_text_builds_patch_body():
    with patch("src.feishu.api_write._patch_block_with_retry", return_value={"ok": True}) as patcher:
        result = api_write.update_block_text(
            "doc1",
            "block1",
            [{"type": "text_run", "text_run": {"text": "hello"}}],
            client_token="token1",
        )

    assert result == {"ok": True}
    patcher.assert_called_once_with(
        "doc1",
        "block1",
        {
            "update_text_elements": {
                "elements": [{"type": "text_run", "text_run": {"text": "hello"}}],
            },
            "document_revision_id": -1,
            "client_token": "token1",
        },
    )


@pytest.mark.parametrize(
    ("operation", "params", "expected"),
    [
        ("insert_table_row", {"row_index": 1}, {"row_index": 1}),
        (
            "delete_table_rows",
            {"row_start_index": 1, "row_end_index": 3},
            {"row_start_index": 1, "row_end_index": 3},
        ),
        ("insert_table_column", {"column_index": 2}, {"column_index": 2}),
        (
            "delete_table_columns",
            {"column_start_index": 2, "column_end_index": 4},
            {"column_start_index": 2, "column_end_index": 4},
        ),
        (
            "merge_table_cells",
            {
                "row_start_index": 0,
                "row_end_index": 1,
                "column_start_index": 2,
                "column_end_index": 3,
            },
            {
                "row_start_index": 0,
                "row_end_index": 1,
                "column_start_index": 2,
                "column_end_index": 3,
            },
        ),
        ("unmerge_table_cells", {"row_index": 4, "column_index": 5}, {"row_index": 4, "column_index": 5}),
    ],
)
def test_update_table_builds_operation_body(operation, params, expected):
    with patch("src.feishu.api_write._patch_block_with_retry", return_value={"ok": True}) as patcher:
        result = api_write.update_table("doc1", "table1", operation, **params)

    assert result == {"ok": True}
    patcher.assert_called_once_with(
        "doc1",
        "table1",
        {
            operation: expected,
            "document_revision_id": -1,
        },
    )


def test_update_table_rejects_unknown_operation():
    with pytest.raises(ValueError, match="unsupported table operation: unknown"):
        api_write.update_table("doc1", "table1", "unknown")


def test_patch_block_with_retry_retries_transient_status_and_returns_data():
    request = MagicMock(
        side_effect=[
            _Resp({"code": 0, "data": {}}, status_code=429, text="rate limited"),
            _ok({"block_id": "block1"}),
        ]
    )

    with (
        patch("src.feishu.api_write._rate_limiter.wait"),
        patch("src.feishu.api.feishu_auth_header", return_value={"Authorization": "Bearer t"}),
        patch("httpx.patch", request),
        patch("src.feishu.api_write.time.sleep") as sleep,
    ):
        result = api_write._patch_block_with_retry(
            "doc1",
            "block1",
            {"document_revision_id": -1},
            max_retries=1,
        )

    assert result == {"block_id": "block1"}
    assert request.call_count == 2
    assert request.call_args.kwargs["headers"] == {"Authorization": "Bearer t"}
    assert request.call_args.kwargs["json"] == {"document_revision_id": -1}
    sleep.assert_called_once_with(1.0)


def test_patch_block_with_retry_preserves_error_message():
    request = MagicMock(return_value=_Resp({"code": 0, "data": {}}, status_code=400, text="bad"))

    with (
        patch("src.feishu.api_write._rate_limiter.wait"),
        patch("src.feishu.api.feishu_auth_header", return_value={}),
        patch("httpx.patch", request),
        pytest.raises(RuntimeError, match="PATCH block block1 failed: 400, bad"),
    ):
        api_write._patch_block_with_retry("doc1", "block1", {}, max_retries=0)
