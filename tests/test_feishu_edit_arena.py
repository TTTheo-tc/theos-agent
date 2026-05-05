"""Tests for Feishu EditArena helpers."""

from __future__ import annotations

from unittest.mock import patch

from src.feishu.edit_arena import (
    _apply_table_cell_patches,
    _coalesce_ops,
    _first_text_child_id,
    _iter_changed_table_cells,
    _parse_html_table_cells,
    _table_layout,
)


def _table_block_map() -> dict[str, dict]:
    block_map = {
        "table1": {
            "block_type": 31,
            "table": {
                "property": {"row_size": 2, "column_size": 2},
                "cells": ["cell00", "cell01", "cell10", "cell11"],
            },
        }
    }
    for cell_id, text_id in {
        "cell00": "text00",
        "cell01": "text01",
        "cell10": "text10",
        "cell11": "text11",
    }.items():
        block_map[cell_id] = {"children": [text_id]}
        block_map[text_id] = {"block_type": 2}
    return block_map


def test_table_layout_returns_row_col_and_cells():
    table = _table_block_map()["table1"]
    assert _table_layout(table) == (2, 2, ["cell00", "cell01", "cell10", "cell11"])
    assert _table_layout({"table": {"property": {"row_size": 0, "column_size": 2}}}) is None


def test_first_text_child_id_requires_existing_child_block():
    block_map = _table_block_map()
    assert _first_text_child_id(block_map, "cell01") == "text01"
    assert _first_text_child_id(block_map, "missing") is None
    assert _first_text_child_id({"cell": {"children": ["missing"]}}, "cell") is None


def test_parse_and_iter_changed_table_cells():
    old_grid = _parse_html_table_cells("<tr><td>A</td><td>B</td></tr>")
    new_grid = _parse_html_table_cells("<tr><td>A</td><td>B2</td></tr>")

    assert old_grid == [["A", "B"]]
    assert list(_iter_changed_table_cells(old_grid, new_grid, row_size=1, col_size=2)) == [
        (0, 1, "B", "B2")
    ]


def test_apply_table_cell_patches_updates_changed_cells_and_throttles():
    old_chunk = (
        "<tr><td>A</td><td>B</td></tr>"
        "<tr><td>C</td><td>D</td></tr>"
    )
    new_chunk = (
        "<tr><td>A1</td><td>B1</td></tr>"
        "<tr><td>C1</td><td>D1</td></tr>"
    )
    calls: list[tuple[str, list[dict]]] = []

    def _record_update(_document_id: str, block_id: str, elements: list[dict]):
        calls.append((block_id, elements))
        return {"ok": True}

    with (
        patch("src.feishu.api_write.update_block_text", side_effect=_record_update),
        patch("time.sleep") as sleep,
    ):
        patched = _apply_table_cell_patches(
            "doc1",
            _table_block_map(),
            old_chunk,
            new_chunk,
            [{"block_id": "table1"}],
        )

    assert patched == 4
    assert [block_id for block_id, _ in calls] == ["text00", "text01", "text10", "text11"]
    assert calls[0][1] == [{"text_run": {"content": "A1", "text_element_style": {}}}]
    sleep.assert_called_once_with(0.5)


def test_coalesce_ops_merges_overlapping_ranges():
    ops = [
        {"delete_start": 0, "delete_end": 2, "new_chunk": "a", "affected_blocks": ["b1"]},
        {"delete_start": 2, "delete_end": 4, "new_chunk": "b", "affected_blocks": ["b2"]},
        {"delete_start": 6, "delete_end": 7, "new_chunk": "c", "affected_blocks": ["b3"]},
    ]

    assert _coalesce_ops(ops) == [
        {
            "delete_start": 0,
            "delete_end": 4,
            "new_chunk": "ab",
            "affected_blocks": ["b1", "b2"],
        },
        {
            "delete_start": 6,
            "delete_end": 7,
            "new_chunk": "c",
            "affected_blocks": ["b3"],
        },
    ]
