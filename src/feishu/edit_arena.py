"""Transaction-like document editing with dual input modes.

EditArena provides a staging area for document edits, supporting:
- Precise mode: old_string -> new_string replacement with fuzzy matching
- Draft mode: replace entire working markdown

All edits are staged in a working copy. On commit(), the diff between
original and working markdown is mapped back to Feishu block operations
and executed in reverse-index order to avoid index shifting.

Table edits use cell-level PATCH when a block_map is available, avoiding
the destructive delete+recreate roundtrip that loses table structure.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from loguru import logger

from src.feishu.edit import (
    _find_affected_blocks,
    _strip_table_merge_info,
)
from src.feishu.fuzzy import fuzzy_count, fuzzy_find_text, normalize_for_fuzzy_match

# Block types that cannot be reconstructed via convert_markdown_to_blocks.
# Editing operations that would delete these blocks are skipped to prevent data loss.
# 18=image, 20=file, 24=bitable, 27=iframe, 31=table, 40=chat_card,
# 43=board, 49=undefined, 50=reference_synced, 51=add-on, 999=unsupported
# Note: type 31 (table) is included because the markdown roundtrip destroys table
# structure (merge_info incompatibility). Table edits go through cell-level PATCH
# when a block_map is available; otherwise they are safely skipped.
_NON_ROUNDTRIPPABLE_TYPES = frozenset({18, 20, 24, 27, 31, 40, 43, 49, 50, 51, 999})


@dataclass
class EditOp:
    """A single staged edit operation."""

    old_string: str | None  # None for draft mode
    new_string: str | None  # None for draft mode
    mode: str  # "precise" | "draft"
    old_chunk: str  # affected markdown before edit
    new_chunk: str  # affected markdown after edit
    used_fuzzy: bool  # whether fuzzy matching was used


@dataclass
class CommitResult:
    """Result of committing staged edits to the document."""

    success: bool
    applied: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    final_revision_id: int | None = None

    def to_dict(self) -> dict:
        d = {
            "success": self.success,
            "applied": self.applied,
            "failed": self.failed,
            "skipped": self.skipped,
            "final_revision_id": self.final_revision_id,
        }
        if self.success:
            d["_verification_hint"] = (
                "Use feishu_read to verify the rendered content matches your intent."
            )
        return d


class EditArena:
    """Transaction-like staging area for Feishu document edits.

    Usage:
        arena = EditArena(document_id, page_block_id, annotations, original_md)
        arena.stage("old text", "new text")       # precise mode
        arena.stage_draft(edited_markdown)          # draft mode
        print(arena.diff())                         # preview changes
        result = arena.commit()                     # apply to document
    """

    def __init__(
        self,
        document_id: str,
        page_block_id: str,
        annotations: dict,
        original_md: str,
        client=None,
        block_map: dict[str, dict] | None = None,
    ):
        self._document_id = document_id
        self._page_block_id = page_block_id
        self._original_md = original_md
        self._original_annotations = annotations
        self._working_md = original_md
        self._ops: list[EditOp] = []
        self._committed = False
        self._client = client
        # block_map: block_id → raw block dict, used for cell-level table editing.
        # When available, table edits use PATCH instead of delete+insert roundtrip.
        self._block_map: dict[str, dict] = block_map or {}

    @classmethod
    def from_url(cls, fs, url: str) -> EditArena:
        """Create an EditArena from a Feishu URL.

        Args:
            fs: FeishuSync instance (with token already set).
            url: Feishu document URL.

        Returns:
            Configured EditArena ready for staging edits.
        """
        from src.feishu.api import parse_url
        from src.feishu.utils import read_json

        fs.ensure_token()

        # Force-refresh to get latest content with annotations
        md = fs.read_page_as_markdown(url, force_refresh=True, with_annotations=True)

        # Resolve document_id
        _, info = fs.info_page(url)
        doc_id = info.get("obj_token")
        obj_type = info.get("obj_type")

        if obj_type not in {"docx", None}:
            msg = f"edit_arena only supports docx, got {obj_type}"
            raise ValueError(msg)

        parsed = parse_url(url)
        actual_doc_id = doc_id or parsed.get("doc_id")
        if not actual_doc_id:
            msg = "Could not determine document ID"
            raise ValueError(msg)

        doc_prefix = fs.output_prefix("docs", actual_doc_id)
        anno_path = f"{doc_prefix}.anno.json"
        annotations = read_json(anno_path)

        # Determine page_block_id
        page_block_id = actual_doc_id
        if annotations.get("blocks"):
            first_parent = annotations["blocks"][0].get("parent_id")
            if first_parent:
                page_block_id = first_parent

        # Build block_map from cached page content for cell-level table editing
        block_map: dict[str, dict] = {}
        content_path = f"{doc_prefix}.content.json"
        content = read_json(content_path)
        if content:
            page_blocks = content.get("page", [])
            if isinstance(page_blocks, list):
                for block in page_blocks:
                    if "block_id" in block:
                        block_map[block["block_id"]] = block

        return cls(
            document_id=actual_doc_id,
            page_block_id=page_block_id,
            annotations=annotations,
            original_md=md,
            client=fs._client,
            block_map=block_map,
        )

    # ------------------------------------------------------------------
    # Staging
    # ------------------------------------------------------------------

    def stage(self, old_string: str, new_string: str) -> EditOp | dict:
        """Stage a precise old_string -> new_string replacement.

        Uses fuzzy matching: tries exact match first, then normalized match.

        Returns:
            EditOp on success, or error dict on failure.
        """
        if self._committed:
            return {"error": "already_committed"}

        result = fuzzy_find_text(self._working_md, old_string)

        if not result.found:
            # Provide more context on why it failed
            count, used_fuzzy = fuzzy_count(self._working_md, old_string)
            if count == 0:
                return {"error": "old_string_not_found"}
            return {"error": "old_string_ambiguous", "count": count}

        content = result.content_for_replacement
        idx = result.index
        match_len = result.match_length

        # Build the replacement string in the (possibly normalized) content
        if result.used_fuzzy_match:
            norm_new = normalize_for_fuzzy_match(new_string)
            new_content = content[:idx] + norm_new + content[idx + match_len :]
        else:
            new_content = content[:idx] + new_string + content[idx + match_len :]

        # Determine old/new chunks for the op record
        old_chunk = content[idx : idx + match_len]
        new_chunk = new_string if not result.used_fuzzy_match else norm_new

        op = EditOp(
            old_string=old_string,
            new_string=new_string,
            mode="precise",
            old_chunk=old_chunk,
            new_chunk=new_chunk,
            used_fuzzy=result.used_fuzzy_match,
        )

        # Update working state
        self._working_md = new_content

        self._ops.append(op)
        return op

    def stage_draft(self, edited_md: str) -> EditOp:
        """Stage a draft edit -- replace entire working markdown.

        Args:
            edited_md: The complete edited markdown.

        Returns:
            EditOp describing the draft operation.
        """
        if self._committed:
            msg = "Cannot stage after commit"
            raise RuntimeError(msg)

        op = EditOp(
            old_string=None,
            new_string=None,
            mode="draft",
            old_chunk=self._working_md,
            new_chunk=edited_md,
            used_fuzzy=False,
        )
        self._working_md = edited_md
        self._ops.append(op)
        return op

    def unstage(self, index: int) -> None:
        """Remove a staged operation and replay remaining ops.

        Args:
            index: Index of the operation to remove.

        Raises:
            IndexError: If index is out of range.
            RuntimeError: If replay of a subsequent op fails.
        """
        if self._committed:
            msg = "Cannot unstage after commit"
            raise RuntimeError(msg)
        if index < 0 or index >= len(self._ops):
            msg = f"Op index {index} out of range [0, {len(self._ops)})"
            raise IndexError(msg)

        remaining_ops = self._ops[:index] + self._ops[index + 1 :]

        # Reset and replay
        self._working_md = self._original_md
        self._ops = []

        for i, op in enumerate(remaining_ops):
            if op.mode == "precise":
                result = self.stage(op.old_string, op.new_string)
                if isinstance(result, dict) and "error" in result:
                    msg = (
                        f"Replay failed at op {i} (original index "
                        f"{i if i < index else i + 1}): {result}"
                    )
                    raise RuntimeError(msg)
            elif op.mode == "draft":
                self.stage_draft(op.new_chunk)

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def preview_markdown(self) -> str:
        """Return the current working markdown."""
        return self._working_md

    def diff(self) -> str:
        """Return a unified diff between original and working markdown."""
        return "".join(
            difflib.unified_diff(
                self._original_md.splitlines(keepends=True),
                self._working_md.splitlines(keepends=True),
                fromfile="original",
                tofile="staged",
            )
        )

    @property
    def ops(self) -> list[EditOp]:
        """Return the list of staged operations (read-only view)."""
        return list(self._ops)

    # ------------------------------------------------------------------
    # Commit
    # ------------------------------------------------------------------

    def commit(self, *, dry_run: bool = False) -> CommitResult:
        """Commit all staged edits to the Feishu document.

        Diffs original_md vs working_md, maps changes back to block
        annotations, and executes delete+insert operations in reverse
        index order to avoid shifting.

        Table edits are handled via cell-level PATCH when block_map is
        available, avoiding the destructive delete+recreate roundtrip.

        Args:
            dry_run: If True, return what would be done without API calls.

        Returns:
            CommitResult with details of applied/failed/skipped operations.
        """
        if self._committed:
            return CommitResult(success=False, failed=[{"error": "already_committed"}])

        if self._working_md == self._original_md:
            return CommitResult(success=True)  # no changes

        # Step 1: Diff original vs working by lines
        orig_lines = self._original_md.splitlines(keepends=True)
        work_lines = self._working_md.splitlines(keepends=True)
        sm = SequenceMatcher(None, orig_lines, work_lines)

        # Step 2: Build table block line-range index for detecting table regions
        table_anno_map = _build_table_anno_map(self._original_annotations)

        raw_opcodes = [
            (tag, i1, i2, j1, j2) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"
        ]
        table_patch_ops, opcode_consumed = _build_table_patch_ops(
            orig_lines,
            work_lines,
            raw_opcodes,
            table_anno_map,
            enabled=bool(self._block_map),
        )

        # Step 2b: Process remaining (non-table) opcodes normally
        commit_ops = []
        skipped_ops = []
        for idx, (tag, i1, i2, j1, j2) in enumerate(raw_opcodes):
            if opcode_consumed[idx]:
                continue

            new_chunk = "".join(work_lines[j1:j2])

            # Pure insert: no original lines consumed, don't touch existing blocks
            if tag == "insert":
                insert_idx = _find_insert_position(self._original_annotations, i1)
                commit_ops.append(
                    {
                        "delete_start": insert_idx,
                        "delete_end": insert_idx,  # empty range = no deletion
                        "new_chunk": new_chunk,
                        "affected_blocks": [],
                    }
                )
                continue

            # replace / delete: find affected blocks in original
            affected = _find_affected_blocks(self._original_annotations, i1, i2)
            if not affected:
                logger.warning(f"Change at lines [{i1}, {i2}) has no affected blocks (title area?)")
                continue

            # Safety guardrail: skip if affected blocks contain non-roundtrippable types
            non_rt = [b for b in affected if b.get("block_type") in _NON_ROUNDTRIPPABLE_TYPES]
            if non_rt:
                types_hit = sorted({b["block_type"] for b in non_rt})
                logger.warning(
                    f"Skipping edit at lines [{i1}, {i2}): "
                    f"affected non-roundtrippable block types {types_hit}"
                )
                skipped_ops.append(
                    {
                        "lines": [i1, i2],
                        "reason": "non_roundtrippable_blocks",
                        "block_types": types_hit,
                    }
                )
                continue

            affected.sort(key=lambda b: b["child_index"])
            min_idx = affected[0]["child_index"]
            max_idx = affected[-1]["child_index"]

            # Expand to full block boundaries in original
            block_start_line = affected[0]["md_start_line"]
            block_end_line = affected[-1]["md_end_line"]

            # Extend working-side content to match full block boundaries
            if i1 > block_start_line:
                work_prefix_start = j1 - (i1 - block_start_line)
                if work_prefix_start >= 0:
                    new_chunk = "".join(work_lines[work_prefix_start:j2])

            if i2 < block_end_line:
                work_suffix_end = j2 + (block_end_line - i2)
                if work_suffix_end <= len(work_lines):
                    start = j1 - (i1 - block_start_line) if i1 > block_start_line else j1
                    start = max(start, 0)
                    new_chunk = "".join(work_lines[start:work_suffix_end])

            commit_ops.append(
                {
                    "delete_start": min_idx,
                    "delete_end": max_idx + 1,
                    "new_chunk": new_chunk,
                    "affected_blocks": affected,
                }
            )

        # Step 3: Combine table patch ops (from pre-scan) with normal ops
        table_ops = table_patch_ops
        normal_ops = _coalesce_ops(commit_ops)

        all_ops = normal_ops + table_ops
        if not all_ops:
            return CommitResult(success=True, skipped=skipped_ops)

        if dry_run:
            return CommitResult(
                success=True,
                applied=[_format_dry_run_op(op) for op in all_ops],
                skipped=skipped_ops,
            )

        # Step 4: Execute in reverse index order
        from src.feishu.api_write import (
            create_descendant_blocks,
            delete_blocks,
            markdown_to_blocks_with_fallback,
        )

        applied = []
        failed = []
        skipped = list(skipped_ops)
        final_rev = None
        has_failure = False

        # Execute table cell-level patches first (they don't shift indices)
        for op in table_ops:
            if has_failure:
                skipped.append({"type": "table_cell_patch", "reason": "skipped_after_failure"})
                continue

            op_info = {
                "type": "table_cell_patch",
                "old_chunk_preview": op["old_chunk"][:200],
                "new_chunk_preview": op["new_chunk"][:200],
            }

            try:
                patched = _apply_table_cell_patches(
                    self._document_id,
                    self._block_map,
                    op["old_chunk"],
                    op["new_chunk"],
                    op["affected_blocks"],
                )
                op_info["patched_cells"] = patched
                applied.append(op_info)
            except Exception as e:
                op_info["error"] = str(e)
                failed.append(op_info)
                has_failure = True

        # Execute normal (delete+insert) ops in reverse index order
        for op in reversed(normal_ops):
            if has_failure:
                skipped.append(
                    {
                        "delete_range": [op["delete_start"], op["delete_end"]],
                        "reason": "skipped_after_failure",
                    }
                )
                continue

            chunk = op["new_chunk"]
            op_info = {
                "delete_range": [op["delete_start"], op["delete_end"]],
                "new_chunk_preview": chunk[:200] if len(chunk) > 200 else chunk,
            }

            try:
                # P5: Sanitize markdown before conversion — fix literal \n
                # that can cause the entire content to render as a single block
                chunk = _sanitize_markdown(chunk)

                # Convert markdown chunk to blocks (client-side preferred, server-side fallback)
                if chunk.strip():
                    convert_result = markdown_to_blocks_with_fallback(self._client, chunk)
                    children_ids = convert_result.get("children_id", [])
                    descendants = convert_result.get("descendants", [])
                    descendants = _strip_table_merge_info(descendants)
                else:
                    children_ids = []
                    descendants = []

                # Delete old blocks (skip for pure inserts with empty range)
                if op["delete_start"] < op["delete_end"]:
                    delete_blocks(
                        self._client,
                        self._document_id,
                        self._page_block_id,
                        start_index=op["delete_start"],
                        end_index=op["delete_end"],
                    )
                    logger.info(
                        f"deleted blocks [{op['delete_start']}, {op['delete_end']}) "
                        f"from {self._page_block_id}"
                    )

                # Insert new blocks (if any) — retry on failure since
                # the delete above is already committed server-side.
                if children_ids:
                    _insert_attempts = 3
                    for _attempt in range(1, _insert_attempts + 1):
                        try:
                            insert_result = create_descendant_blocks(
                                self._client,
                                self._document_id,
                                self._page_block_id,
                                children_ids=children_ids,
                                descendants=descendants,
                                index=op["delete_start"],
                            )
                            break
                        except Exception:
                            if _attempt >= _insert_attempts:
                                raise
                            import time

                            logger.warning(
                                "Insert after delete failed (attempt {}/{}), retrying...",
                                _attempt,
                                _insert_attempts,
                            )
                            time.sleep(1)
                    op_info["new_block_ids"] = insert_result.get("children_id", children_ids)
                    final_rev = insert_result.get("document_revision_id", final_rev)
                    logger.info(
                        f"inserted {len(children_ids)} blocks at index "
                        f"{op['delete_start']} in {self._page_block_id}"
                    )

                applied.append(op_info)

            except Exception as e:
                status = getattr(e, "response", None)
                status_code = getattr(status, "status_code", None) if status else None
                op_info["error"] = str(e)
                op_info["status"] = status_code
                failed.append(op_info)
                has_failure = True

        self._committed = True
        success = len(failed) == 0
        return CommitResult(
            success=success,
            applied=applied,
            failed=failed,
            skipped=skipped,
            final_revision_id=final_rev,
        )


# ---------------------------------------------------------------------------
# Table cell-level patch helpers
# ---------------------------------------------------------------------------


def _build_table_anno_map(annotations: dict) -> dict[str, dict]:
    """Build a mapping from table block_id to its annotation dict."""
    result = {}
    for b in annotations.get("blocks", []):
        if b.get("block_type") == 31:
            result[b["block_id"]] = b
    return result


def _build_table_patch_ops(
    orig_lines: list[str],
    work_lines: list[str],
    raw_opcodes: list[tuple],
    table_anno_map: dict[str, dict],
    *,
    enabled: bool,
) -> tuple[list[dict], list[bool]]:
    """Build table cell PATCH ops and mark raw opcodes they consume."""
    opcode_consumed = [False] * len(raw_opcodes)
    if not enabled:
        return [], opcode_consumed

    table_ops_by_block: dict[str, list[int]] = {}
    for idx, (tag, i1, i2, _j1, _j2) in enumerate(raw_opcodes):
        for block_id, tbl_anno in table_anno_map.items():
            ts = tbl_anno["md_start_line"]
            te = tbl_anno["md_end_line"]
            if tag == "insert":
                # For inserts, only claim if strictly inside the table content
                # area (not at the boundary after </table>).
                if ts < i1 < te - 1:
                    table_ops_by_block.setdefault(block_id, []).append(idx)
                    break
            elif i1 < te and i2 > ts:
                table_ops_by_block.setdefault(block_id, []).append(idx)
                break

    table_patch_ops: list[dict] = []
    for block_id, op_indices in table_ops_by_block.items():
        tbl_anno = table_anno_map[block_id]
        ts = tbl_anno["md_start_line"]
        te = tbl_anno["md_end_line"]
        old_chunk = "".join(orig_lines[ts:te])
        new_table_lines = _rebuild_new_region(
            orig_lines,
            work_lines,
            raw_opcodes,
            op_indices,
            ts,
            te,
        )
        new_chunk = "".join(new_table_lines)

        if old_chunk != new_chunk:
            table_patch_ops.append(
                {
                    "type": "table_cell_patch",
                    "old_chunk": old_chunk,
                    "new_chunk": new_chunk,
                    "affected_blocks": [tbl_anno],
                }
            )
        for oi in op_indices:
            opcode_consumed[oi] = True

    return table_patch_ops, opcode_consumed


def _format_dry_run_op(op: dict) -> dict:
    if op.get("type") == "table_cell_patch":
        return {
            "type": "table_cell_patch",
            "old_chunk": op["old_chunk"],
            "new_chunk": op["new_chunk"],
            "dry_run": True,
        }
    return {
        "delete_range": [op["delete_start"], op["delete_end"]],
        "new_chunk": op["new_chunk"],
        "dry_run": True,
    }


def _rebuild_new_region(
    orig_lines: list[str],
    work_lines: list[str],
    all_opcodes: list[tuple],
    table_op_indices: list[int],
    region_start: int,
    region_end: int,
) -> list[str]:
    """Rebuild the new content for a table region by applying opcodes.

    Walks through the original region line by line.  For each line, checks
    if any of the table-related opcodes affect it and uses the working
    lines instead.  This correctly handles inserts, deletes, replaces,
    and line reordering within the table region.
    """
    result_lines: list[str] = []
    orig_cursor = region_start

    # Sort table opcodes by their position in the original
    sorted_ops = sorted(table_op_indices, key=lambda i: all_opcodes[i][1])

    for op_idx in sorted_ops:
        tag, i1, i2, j1, j2 = all_opcodes[op_idx]

        # Emit unchanged lines before this opcode (within table region)
        while orig_cursor < min(i1, region_end):
            result_lines.append(orig_lines[orig_cursor])
            orig_cursor += 1

        if tag == "insert":
            result_lines.extend(work_lines[j1:j2])
        elif tag == "delete":
            orig_cursor = max(orig_cursor, min(i2, region_end))
        elif tag == "replace":
            result_lines.extend(work_lines[j1:j2])
            orig_cursor = max(orig_cursor, min(i2, region_end))

    # Emit remaining unchanged lines in the table region
    while orig_cursor < region_end:
        result_lines.append(orig_lines[orig_cursor])
        orig_cursor += 1

    return result_lines


def _parse_html_table_cells(html: str) -> list[list[str]]:
    """Parse an HTML table string into a 2D grid of cell texts.

    Returns a list of rows, each row is a list of cell text strings.
    """
    rows: list[list[str]] = []
    for tr_match in re.finditer(r"<tr>(.*?)</tr>", html, re.DOTALL):
        row_html = tr_match.group(1)
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.DOTALL)
        rows.append(cells)
    return rows


def _apply_table_cell_patches(
    document_id: str,
    block_map: dict[str, dict],
    old_chunk: str,
    new_chunk: str,
    affected_blocks: list[dict],
) -> int:
    """Apply text replacements to table cells via cell-level PATCH.

    Parses the HTML table from old_chunk and new_chunk, compares cell-by-cell,
    and PATCHes only the cells that actually changed.

    For structural changes (different row/col count), logs a warning and
    patches only the overlapping region.

    Args:
        document_id: The document ID.
        block_map: block_id → raw block dict (from page content).
        old_chunk: Original HTML table region.
        new_chunk: Modified HTML table region.
        affected_blocks: List of annotation dicts for the table blocks.

    Returns:
        Number of cells patched.
    """
    import time as _time

    from src.feishu.api_write import update_block_text

    old_grid = _parse_html_table_cells(old_chunk)
    new_grid = _parse_html_table_cells(new_chunk)

    if not old_grid or not new_grid:
        logger.warning("Could not parse HTML table from old/new chunk")
        return 0

    patched = 0

    for block_anno in affected_blocks:
        block_id = block_anno.get("block_id")
        if not block_id or block_id not in block_map:
            continue
        block = block_map[block_id]
        if block.get("block_type") != 31:
            continue

        table_layout = _table_layout(block)
        if table_layout is None:
            continue
        row_size, col_size, cells = table_layout

        old_rows = len(old_grid)
        new_rows = len(new_grid)

        if old_rows != new_rows:
            logger.warning(
                f"Table row count changed ({old_rows} → {new_rows}). "
                f"Row add/delete/reorder requires edit_table, not cell patch. "
                f"Patching overlapping cells only."
            )

        for r, c, old_text, new_text in _iter_changed_table_cells(
            old_grid,
            new_grid,
            row_size,
            col_size,
        ):
            cell_idx = r * col_size + c
            if cell_idx >= len(cells):
                continue

            child_id = _first_text_child_id(block_map, cells[cell_idx])
            if child_id is None:
                continue

            new_elements = [{"text_run": {"content": new_text, "text_element_style": {}}}]

            # Throttle: pause every 3 patches to avoid rate limits
            if patched > 0 and patched % 3 == 0:
                _time.sleep(0.5)

            update_block_text(document_id, child_id, new_elements)
            patched += 1
            logger.info(
                f"patched table cell [{r},{c}] block {child_id}: "
                f"{old_text!r} → {new_text!r}"
            )

    return patched


def _table_layout(block: dict) -> tuple[int, int, list] | None:
    table_data = block.get("table", {})
    prop = table_data.get("property", {})
    row_size = prop.get("row_size", 0)
    col_size = prop.get("column_size", 0)
    if not row_size or not col_size:
        return None
    return row_size, col_size, table_data.get("cells", [])


def _iter_changed_table_cells(
    old_grid: list[list[str]],
    new_grid: list[list[str]],
    row_size: int,
    col_size: int,
):
    patch_rows = min(len(old_grid), len(new_grid), row_size)
    old_cols = len(old_grid[0]) if old_grid else 0
    new_cols = len(new_grid[0]) if new_grid else 0
    patch_cols = min(old_cols, new_cols, col_size)

    for r in range(patch_rows):
        for c in range(patch_cols):
            old_text = old_grid[r][c] if c < len(old_grid[r]) else ""
            new_text = new_grid[r][c] if c < len(new_grid[r]) else ""
            if old_text != new_text:
                yield r, c, old_text, new_text


def _first_text_child_id(block_map: dict[str, dict], cell_id: str) -> str | None:
    cell_block = block_map.get(cell_id)
    if not cell_block:
        return None
    children = cell_block.get("children", [])
    if not children:
        return None
    child_id = children[0]
    return child_id if child_id in block_map else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_markdown(md: str) -> str:
    """Sanitize markdown before sending to Feishu convert API.

    Fixes common issues:
    - Literal ``\\n`` strings (from JSON serialization) → real newlines
    - Literal ``\\t`` → real tabs
    - Excessive blank lines → max 2 consecutive
    """
    # Fix literal \n and \t that weren't properly unescaped
    # (common when LLM tool call arguments have JSON-escaped newlines)
    if "\\n" in md and "\n" not in md:
        # Entire content is on one line with literal \n — definitely needs fixing
        md = md.replace("\\n", "\n")
        md = md.replace("\\t", "\t")
    # When content already has real newlines, leave literal \\n alone.
    # It's ambiguous (could be intentional) and replacing it risks corruption.

    # Collapse excessive blank lines (>2 consecutive → 2)
    md = re.sub(r"\n{4,}", "\n\n\n", md)

    return md


def _coalesce_ops(ops: list[dict]) -> list[dict]:
    """Merge overlapping or adjacent commit operations.

    Ops are sorted by delete_start and merged when ranges overlap or touch.
    """
    if not ops:
        return []

    ops.sort(key=lambda o: o["delete_start"])
    merged = [ops[0]]

    for op in ops[1:]:
        prev = merged[-1]
        # Overlapping or adjacent
        if op["delete_start"] <= prev["delete_end"]:
            prev["delete_end"] = max(prev["delete_end"], op["delete_end"])
            prev["new_chunk"] = prev["new_chunk"] + op["new_chunk"]
            prev["affected_blocks"] = prev["affected_blocks"] + op["affected_blocks"]
        else:
            merged.append(op)

    return merged


def _find_insert_position(annotations: dict, insert_line: int) -> int:
    """Determine the child_index at which to insert new blocks.

    Finds the last annotated block whose md_end_line <= insert_line
    and returns child_index + 1 (i.e. insert *after* that block).

    Returns:
        child_index for insertion. -1 means append at end (API semantics).
    """
    blocks = annotations.get("blocks", [])
    if not blocks:
        return -1

    best = None
    for b in blocks:
        if b["md_end_line"] <= insert_line and (
            best is None or b["child_index"] > best["child_index"]
        ):
            best = b

    if best is not None:
        return best["child_index"] + 1

    # insert_line is before every block -> insert at the first block's position
    first = min(blocks, key=lambda b: b["child_index"])
    return first["child_index"]
