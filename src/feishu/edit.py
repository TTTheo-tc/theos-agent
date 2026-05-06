"""Edit helpers for Feishu documents.

Provides block-annotation utilities used by EditArena to map markdown line
ranges to Feishu block operations.
"""

from __future__ import annotations


def _find_affected_blocks(
    annotations: dict,
    edit_start_line: int,
    edit_end_line: int,
) -> list[dict]:
    """Find all annotated blocks whose line ranges overlap the edit region."""
    blocks = annotations.get("blocks", [])
    return [
        b
        for b in blocks
        if b["md_start_line"] < edit_end_line and b["md_end_line"] > edit_start_line
    ]


def _strip_table_merge_info(blocks: list[dict]) -> list[dict]:
    """Remove merge_info from table cell blocks (not supported by create API)."""
    for block in blocks:
        if block.get("block_type") == 32:  # table cell
            table_cell = block.get("table_cell", {})
            table_cell.pop("merge_info", None)
    return blocks
