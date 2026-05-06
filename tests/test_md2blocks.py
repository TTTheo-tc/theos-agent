"""Tests for client-side Markdown -> Feishu Block converter."""

from __future__ import annotations

import pytest

from src.feishu.md2blocks import (
    BLOCK_BULLET,
    BLOCK_CALLOUT,
    BLOCK_CODE,
    BLOCK_DIVIDER,
    BLOCK_HEADING1,
    BLOCK_HEADING2,
    BLOCK_HEADING3,
    BLOCK_IMAGE,
    BLOCK_ORDERED,
    BLOCK_QUOTE_CONTAINER,
    BLOCK_TABLE,
    BLOCK_TABLE_CELL,
    BLOCK_TEXT,
    BLOCK_TODO,
    MAX_TABLE_COLS,
    MAX_TABLE_ROWS,
    _split_column_groups,
    _split_row_groups,
    markdown_to_feishu_blocks,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _blocks_by_type(descendants: list[dict], block_type: int) -> list[dict]:
    return [b for b in descendants if b.get("block_type") == block_type]


def _top_blocks(children_ids: list[str], descendants: list[dict]) -> list[dict]:
    id_set = set(children_ids)
    return [b for b in descendants if b["block_id"] in id_set]


def _table_rows(table: dict, descendants: list[dict]) -> list[list[str]]:
    by_id = {b["block_id"]: b for b in descendants}
    col_size = table["table"]["property"]["column_size"]
    cells = []
    for cell_id in table["children"]:
        cell = by_id[cell_id]
        text_block = by_id[cell["children"][0]]
        cells.append(
            "".join(
                e["text_run"]["content"]
                for e in text_block["text"]["elements"]
                if "text_run" in e
            )
        )
    return [cells[i : i + col_size] for i in range(0, len(cells), col_size)]


# ---------------------------------------------------------------------------
# Paragraph
# ---------------------------------------------------------------------------


class TestParagraph:
    def test_simple_paragraph(self):
        ids, desc = markdown_to_feishu_blocks("Hello world")
        assert len(ids) == 1
        top = _top_blocks(ids, desc)
        assert top[0]["block_type"] == BLOCK_TEXT
        elements = top[0]["text"]["elements"]
        assert any("Hello world" in e["text_run"]["content"] for e in elements)

    def test_multiple_paragraphs(self):
        ids, desc = markdown_to_feishu_blocks("Para one\n\nPara two")
        assert len(ids) == 2
        top = _top_blocks(ids, desc)
        assert all(b["block_type"] == BLOCK_TEXT for b in top)


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------


class TestHeadings:
    @pytest.mark.parametrize(
        "md,expected_type",
        [
            ("# H1", BLOCK_HEADING1),
            ("## H2", BLOCK_HEADING2),
            ("### H3", BLOCK_HEADING3),
        ],
    )
    def test_heading_levels(self, md, expected_type):
        ids, desc = markdown_to_feishu_blocks(md)
        assert len(ids) == 1
        top = _top_blocks(ids, desc)
        assert top[0]["block_type"] == expected_type

    def test_heading_content(self):
        ids, desc = markdown_to_feishu_blocks("## My Title")
        top = _top_blocks(ids, desc)
        heading = top[0]
        elements = heading["heading2"]["elements"]
        texts = [e["text_run"]["content"] for e in elements if "text_run" in e]
        assert "My Title" in " ".join(texts)


# ---------------------------------------------------------------------------
# Inline styles
# ---------------------------------------------------------------------------


class TestInlineStyles:
    def test_bold(self):
        ids, desc = markdown_to_feishu_blocks("**bold text**")
        top = _top_blocks(ids, desc)
        elements = top[0]["text"]["elements"]
        bold_els = [
            e for e in elements if e.get("text_run", {}).get("text_element_style", {}).get("bold")
        ]
        assert len(bold_els) >= 1
        assert "bold text" in bold_els[0]["text_run"]["content"]

    def test_italic(self):
        ids, desc = markdown_to_feishu_blocks("*italic text*")
        top = _top_blocks(ids, desc)
        elements = top[0]["text"]["elements"]
        italic_els = [
            e for e in elements if e.get("text_run", {}).get("text_element_style", {}).get("italic")
        ]
        assert len(italic_els) >= 1

    def test_inline_code(self):
        ids, desc = markdown_to_feishu_blocks("`code`")
        top = _top_blocks(ids, desc)
        elements = top[0]["text"]["elements"]
        code_els = [
            e
            for e in elements
            if e.get("text_run", {}).get("text_element_style", {}).get("inline_code")
        ]
        assert len(code_els) >= 1
        assert "code" in code_els[0]["text_run"]["content"]

    def test_strikethrough(self):
        ids, desc = markdown_to_feishu_blocks("~~struck~~")
        top = _top_blocks(ids, desc)
        elements = top[0]["text"]["elements"]
        st_els = [
            e
            for e in elements
            if e.get("text_run", {}).get("text_element_style", {}).get("strikethrough")
        ]
        assert len(st_els) >= 1

    def test_link(self):
        ids, desc = markdown_to_feishu_blocks("[click](https://example.com)")
        top = _top_blocks(ids, desc)
        elements = top[0]["text"]["elements"]
        link_els = [
            e for e in elements if e.get("text_run", {}).get("text_element_style", {}).get("link")
        ]
        assert len(link_els) >= 1
        assert link_els[0]["text_run"]["text_element_style"]["link"]["url"] == "https://example.com"
        assert "click" in link_els[0]["text_run"]["content"]


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


class TestUnorderedList:
    def test_simple_list(self):
        md = "- item one\n- item two\n- item three"
        ids, desc = markdown_to_feishu_blocks(md)
        bullets = _blocks_by_type(desc, BLOCK_BULLET)
        assert len(bullets) == 3

    def test_bullet_content(self):
        md = "- hello world"
        ids, desc = markdown_to_feishu_blocks(md)
        bullets = _blocks_by_type(desc, BLOCK_BULLET)
        assert len(bullets) == 1
        elements = bullets[0]["bullet"]["elements"]
        texts = [e["text_run"]["content"] for e in elements if "text_run" in e]
        assert "hello world" in " ".join(texts)


class TestOrderedList:
    def test_simple_ordered(self):
        md = "1. first\n2. second\n3. third"
        ids, desc = markdown_to_feishu_blocks(md)
        ordered = _blocks_by_type(desc, BLOCK_ORDERED)
        assert len(ordered) == 3

    def test_ordered_content(self):
        md = "1. hello"
        ids, desc = markdown_to_feishu_blocks(md)
        ordered = _blocks_by_type(desc, BLOCK_ORDERED)
        elements = ordered[0]["ordered"]["elements"]
        texts = [e["text_run"]["content"] for e in elements if "text_run" in e]
        assert "hello" in " ".join(texts)


class TestNestedList:
    def test_nested_bullet(self):
        md = "- parent\n  - child\n  - child2"
        ids, desc = markdown_to_feishu_blocks(md)
        bullets = _blocks_by_type(desc, BLOCK_BULLET)
        assert len(bullets) == 3
        # Parent should have children
        parent = [b for b in bullets if b.get("children")]
        assert len(parent) >= 1

    def test_nested_ordered_in_bullet(self):
        md = "- parent\n  1. child one\n  2. child two"
        ids, desc = markdown_to_feishu_blocks(md)
        bullets = _blocks_by_type(desc, BLOCK_BULLET)
        ordered = _blocks_by_type(desc, BLOCK_ORDERED)
        assert len(bullets) >= 1
        assert len(ordered) == 2


# ---------------------------------------------------------------------------
# Code block
# ---------------------------------------------------------------------------


class TestCodeBlock:
    def test_code_block(self):
        md = "```python\nprint('hello')\n```"
        ids, desc = markdown_to_feishu_blocks(md)
        code_blocks = _blocks_by_type(desc, BLOCK_CODE)
        assert len(code_blocks) == 1
        code = code_blocks[0]
        assert code["code"]["style"]["language"] == 49  # Python
        elements = code["code"]["elements"]
        assert "print('hello')" in elements[0]["text_run"]["content"]

    def test_code_block_no_lang(self):
        md = "```\nsome code\n```"
        ids, desc = markdown_to_feishu_blocks(md)
        code_blocks = _blocks_by_type(desc, BLOCK_CODE)
        assert len(code_blocks) == 1
        assert code_blocks[0]["code"]["style"]["language"] == 1  # PlainText


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------


class TestTable:
    def test_simple_table(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
        ids, desc = markdown_to_feishu_blocks(md)
        tables = _blocks_by_type(desc, BLOCK_TABLE)
        assert len(tables) == 1
        t = tables[0]["table"]
        assert t["property"]["row_size"] == 3  # header + 2 data rows
        assert t["property"]["column_size"] == 2
        cells = _blocks_by_type(desc, BLOCK_TABLE_CELL)
        assert len(cells) == 6  # 3 rows * 2 cols

    def test_table_cell_content(self):
        md = "| Name | Value |\n|------|-------|\n| foo  | bar   |"
        ids, desc = markdown_to_feishu_blocks(md)
        # Check that cell text blocks contain expected content
        text_blocks = _blocks_by_type(desc, BLOCK_TEXT)
        all_text = " ".join(
            e["text_run"]["content"]
            for b in text_blocks
            for e in b["text"]["elements"]
            if "text_run" in e
        )
        assert "Name" in all_text
        assert "Value" in all_text
        assert "foo" in all_text
        assert "bar" in all_text


# ---------------------------------------------------------------------------
# Large table auto-split
# ---------------------------------------------------------------------------


class TestLargeTableRowSplit:
    """Table with 15 rows (1 header + 14 data) -> split into chunks of <=9 rows."""

    def test_row_split(self):
        header = "| " + " | ".join(f"H{i}" for i in range(3)) + " |"
        sep = "| " + " | ".join("---" for _ in range(3)) + " |"
        data_rows = ["| " + " | ".join(f"r{r}c{c}" for c in range(3)) + " |" for r in range(14)]
        md = "\n".join([header, sep, *data_rows])
        ids, desc = markdown_to_feishu_blocks(md)
        tables = _blocks_by_type(desc, BLOCK_TABLE)
        # 15 total rows, max 9 per table -> 2 tables
        # Table 1: 1 header + 8 data = 9 rows
        # Table 2: 1 header + 6 data = 7 rows
        assert len(tables) == 2
        sizes = sorted(t["table"]["property"]["row_size"] for t in tables)
        assert sizes == [7, 9]
        # All tables should have 3 columns
        for t in tables:
            assert t["table"]["property"]["column_size"] == 3


class TestLargeTableColSplit:
    """Table with 12 columns -> split into 2 tables."""

    def test_col_split(self):
        ncols = 12
        header = "| " + " | ".join(f"H{i}" for i in range(ncols)) + " |"
        sep = "| " + " | ".join("---" for _ in range(ncols)) + " |"
        row1 = "| " + " | ".join(f"d{i}" for i in range(ncols)) + " |"
        md = "\n".join([header, sep, row1])
        ids, desc = markdown_to_feishu_blocks(md)
        tables = _blocks_by_type(desc, BLOCK_TABLE)
        # 12 cols, max 9 -> group 1: cols 0-8 (9), group 2: col 0 + cols 9-11 (4)
        assert len(tables) == 2
        col_sizes = sorted(t["table"]["property"]["column_size"] for t in tables)
        assert col_sizes == [4, 9]
        rows = [_table_rows(table, desc) for table in tables]
        assert rows[0] == [
            [f"H{i}" for i in range(9)],
            [f"d{i}" for i in range(9)],
        ]
        assert rows[1] == [
            ["H0", "H9", "H10", "H11"],
            ["d0", "d9", "d10", "d11"],
        ]


class TestLargeTableCompoundSplit:
    """Table with 15 rows x 12 cols -> 4 tables (2 col groups x 2 row groups)."""

    def test_compound_split(self):
        ncols = 12
        header = "| " + " | ".join(f"H{i}" for i in range(ncols)) + " |"
        sep = "| " + " | ".join("---" for _ in range(ncols)) + " |"
        data_rows = ["| " + " | ".join(f"r{r}c{c}" for c in range(ncols)) + " |" for r in range(14)]
        md = "\n".join([header, sep, *data_rows])
        ids, desc = markdown_to_feishu_blocks(md)
        tables = _blocks_by_type(desc, BLOCK_TABLE)
        # 2 col groups x 2 row groups = 4 tables
        assert len(tables) == 4
        # Verify all tables are within limits
        for t in tables:
            assert t["table"]["property"]["row_size"] <= MAX_TABLE_ROWS
            assert t["table"]["property"]["column_size"] <= MAX_TABLE_COLS


# ---------------------------------------------------------------------------
# Split helpers (unit tests)
# ---------------------------------------------------------------------------


class TestSplitHelpers:
    def test_split_column_groups_no_split(self):
        groups = _split_column_groups(5, 9)
        assert groups == [list(range(5))]

    def test_split_column_groups_split(self):
        groups = _split_column_groups(12, 9)
        assert len(groups) == 2
        assert groups[0] == list(range(9))
        assert groups[1][0] == 0  # first col repeated
        assert set(groups[1][1:]) == {9, 10, 11}

    def test_split_row_groups_no_split(self):
        groups = _split_row_groups(5, 8)
        assert groups == [list(range(5))]

    def test_split_row_groups_split(self):
        groups = _split_row_groups(14, 8)
        assert len(groups) == 2
        assert groups[0] == list(range(8))
        assert groups[1] == list(range(8, 14))


# ---------------------------------------------------------------------------
# Blockquote
# ---------------------------------------------------------------------------


class TestBlockquote:
    def test_simple_quote(self):
        md = "> quoted text"
        ids, desc = markdown_to_feishu_blocks(md)
        qc = _blocks_by_type(desc, BLOCK_QUOTE_CONTAINER)
        assert len(qc) == 1
        # Should have text children
        assert "children" in qc[0]
        child_ids = qc[0]["children"]
        assert len(child_ids) >= 1

    def test_multiline_quote(self):
        md = "> line one\n> line two"
        ids, desc = markdown_to_feishu_blocks(md)
        qc = _blocks_by_type(desc, BLOCK_QUOTE_CONTAINER)
        assert len(qc) >= 1


# ---------------------------------------------------------------------------
# Callout
# ---------------------------------------------------------------------------


class TestCallout:
    def test_note_callout(self):
        md = "> [!NOTE]\n> This is a note"
        ids, desc = markdown_to_feishu_blocks(md)
        callouts = _blocks_by_type(desc, BLOCK_CALLOUT)
        assert len(callouts) == 1
        assert callouts[0]["callout"]["background_color"] == 6  # blue

    def test_warning_callout(self):
        md = "> [!WARNING]\n> Be careful"
        ids, desc = markdown_to_feishu_blocks(md)
        callouts = _blocks_by_type(desc, BLOCK_CALLOUT)
        assert len(callouts) == 1
        assert callouts[0]["callout"]["background_color"] == 3  # yellow

    def test_important_callout(self):
        md = "> [!IMPORTANT]\n> Critical info"
        ids, desc = markdown_to_feishu_blocks(md)
        callouts = _blocks_by_type(desc, BLOCK_CALLOUT)
        assert len(callouts) == 1
        assert callouts[0]["callout"]["background_color"] == 1  # red

    def test_tip_callout(self):
        md = "> [!TIP]\n> Helpful hint"
        ids, desc = markdown_to_feishu_blocks(md)
        callouts = _blocks_by_type(desc, BLOCK_CALLOUT)
        assert len(callouts) == 1
        assert callouts[0]["callout"]["background_color"] == 4  # green

    def test_callout_body_content(self):
        md = "> [!NOTE]\n> This is the body"
        ids, desc = markdown_to_feishu_blocks(md)
        callouts = _blocks_by_type(desc, BLOCK_CALLOUT)
        assert "children" in callouts[0]
        # Child text block should contain body text
        child_ids = set(callouts[0]["children"])
        child_blocks = [b for b in desc if b["block_id"] in child_ids]
        all_text = " ".join(
            e["text_run"]["content"]
            for b in child_blocks
            for e in b.get("text", {}).get("elements", [])
            if "text_run" in e
        )
        assert "This is the body" in all_text


# ---------------------------------------------------------------------------
# Divider
# ---------------------------------------------------------------------------


class TestDivider:
    def test_divider(self):
        md = "---"
        ids, desc = markdown_to_feishu_blocks(md)
        dividers = _blocks_by_type(desc, BLOCK_DIVIDER)
        assert len(dividers) == 1


# ---------------------------------------------------------------------------
# Image
# ---------------------------------------------------------------------------


class TestImage:
    def test_image_html(self):
        md = '<img src="https://example.com/img.png">'
        ids, desc = markdown_to_feishu_blocks(md)
        images = _blocks_by_type(desc, BLOCK_IMAGE)
        assert len(images) == 1
        assert images[0]["image"]["url"] == "https://example.com/img.png"


# ---------------------------------------------------------------------------
# Mixed content
# ---------------------------------------------------------------------------


class TestMixedContent:
    def test_mixed(self):
        md = """# Title

Some text with **bold** and *italic*.

- bullet one
- bullet two

1. ordered one
2. ordered two

```python
print("hello")
```

---

> a quote

| A | B |
|---|---|
| 1 | 2 |
"""
        ids, desc = markdown_to_feishu_blocks(md)

        # Should have heading, text, bullets, ordered, code, divider, quote, table
        top = _top_blocks(ids, desc)
        types = [b["block_type"] for b in top]

        assert BLOCK_HEADING1 in types
        assert BLOCK_TEXT in types
        assert BLOCK_BULLET in types or any(b["block_type"] == BLOCK_BULLET for b in desc)
        assert BLOCK_ORDERED in types or any(b["block_type"] == BLOCK_ORDERED for b in desc)
        assert BLOCK_CODE in types
        assert BLOCK_DIVIDER in types
        assert BLOCK_QUOTE_CONTAINER in types
        assert BLOCK_TABLE in types

    def test_output_structure(self):
        """Verify children_ids and descendants have correct relationship."""
        md = "Hello\n\n## Title\n\n- item"
        ids, desc = markdown_to_feishu_blocks(md)
        # All children_ids should be present in descendants
        desc_ids = {b["block_id"] for b in desc}
        for cid in ids:
            assert cid in desc_ids

    def test_empty_input(self):
        ids, desc = markdown_to_feishu_blocks("")
        assert ids == []
        assert desc == []

    def test_whitespace_only(self):
        ids, desc = markdown_to_feishu_blocks("   \n\n  ")
        assert ids == []
        assert desc == []


# ---------------------------------------------------------------------------
# Todo / task list
# ---------------------------------------------------------------------------


class TestTodoList:
    def test_unchecked_todo(self):
        md = "- [ ] unchecked task"
        ids, desc = markdown_to_feishu_blocks(md)
        todos = _blocks_by_type(desc, BLOCK_TODO)
        assert len(todos) == 1
        assert todos[0]["todo"]["style"]["done"] is False
        elements = todos[0]["todo"]["elements"]
        texts = [e["text_run"]["content"] for e in elements if "text_run" in e]
        assert "unchecked task" in " ".join(texts)

    def test_checked_todo(self):
        md = "- [x] checked task"
        ids, desc = markdown_to_feishu_blocks(md)
        todos = _blocks_by_type(desc, BLOCK_TODO)
        assert len(todos) == 1
        assert todos[0]["todo"]["style"]["done"] is True
