"""Client-side Markdown-to-Feishu-Block converter.

Parses Markdown with markdown-it-py and produces Feishu block JSON
suitable for ``create_descendant_blocks()``.  This avoids the server-side
``/docx/v1/documents/blocks/convert`` endpoint which fails on complex
markdown, nested lists, large tables, and extended syntax (callouts,
formulas, etc.).

Large-table auto-split
    Feishu limits tables to 9 rows x 9 columns.  When a parsed table
    exceeds either dimension, the converter splits it into multiple table
    blocks, preserving header rows and identifier columns.
"""

from __future__ import annotations

import re
import uuid
from types import SimpleNamespace
from typing import Any

from markdown_it import MarkdownIt

# ---------------------------------------------------------------------------
# Feishu block-type constants (matches feishu2md.py)
# ---------------------------------------------------------------------------

BLOCK_TEXT = 2
BLOCK_HEADING1 = 3
BLOCK_HEADING2 = 4
BLOCK_HEADING3 = 5
BLOCK_HEADING4 = 6
BLOCK_HEADING5 = 7
BLOCK_HEADING6 = 8
BLOCK_BULLET = 12
BLOCK_ORDERED = 13
BLOCK_CODE = 14
BLOCK_TODO = 17
BLOCK_CALLOUT = 19
BLOCK_DIVIDER = 22
BLOCK_IMAGE = 27
BLOCK_TABLE = 31
BLOCK_TABLE_CELL = 32
BLOCK_QUOTE_CONTAINER = 34

# Feishu table limits
MAX_TABLE_ROWS = 9
MAX_TABLE_COLS = 9

# Language string -> Feishu language ID (reverse of feishu2md.get_language_string)
_LANG_TO_ID: dict[str, int] = {
    "": 1,
    "plaintext": 1,
    "abap": 2,
    "ada": 3,
    "apache": 4,
    "apex": 5,
    "assembly": 6,
    "bash": 7,
    "sh": 7,
    "csharp": 8,
    "cs": 8,
    "cpp": 9,
    "c++": 9,
    "c": 10,
    "cobol": 11,
    "css": 12,
    "coffeescript": 13,
    "d": 14,
    "dart": 15,
    "delphi": 16,
    "django": 17,
    "dockerfile": 18,
    "erlang": 19,
    "fortran": 20,
    "foxpro": 21,
    "go": 22,
    "golang": 22,
    "groovy": 23,
    "html": 24,
    "htmlbars": 25,
    "http": 26,
    "haskell": 27,
    "json": 28,
    "java": 29,
    "javascript": 30,
    "js": 30,
    "julia": 31,
    "kotlin": 32,
    "kt": 32,
    "latex": 33,
    "tex": 33,
    "lisp": 34,
    "logo": 35,
    "lua": 36,
    "matlab": 37,
    "makefile": 38,
    "make": 38,
    "markdown": 39,
    "md": 39,
    "nginx": 40,
    "objectivec": 41,
    "objc": 41,
    "openedge-abl": 42,
    "php": 43,
    "perl": 44,
    "postscript": 45,
    "powershell": 46,
    "ps1": 46,
    "prolog": 47,
    "protobuf": 48,
    "proto": 48,
    "python": 49,
    "py": 49,
    "r": 50,
    "rpg": 51,
    "ruby": 52,
    "rb": 52,
    "rust": 53,
    "rs": 53,
    "sas": 54,
    "scss": 55,
    "sql": 56,
    "scala": 57,
    "scheme": 58,
    "scratch": 59,
    "shell": 60,
    "swift": 61,
    "thrift": 62,
    "typescript": 63,
    "ts": 63,
    "vbscript": 64,
    "vbnet": 65,
    "vb": 65,
    "xml": 66,
    "yaml": 67,
    "yml": 67,
    "cmake": 68,
    "diff": 69,
    "gherkin": 70,
    "graphql": 71,
    "glsl": 72,
    "properties": 73,
    "solidity": 74,
    "sol": 74,
    "toml": 75,
}

# Callout keyword -> (emoji_id, background_color)
# Feishu callout backgrounds: 1=red, 2=orange, 3=yellow, 4=green,
# 5=cyan, 6=blue, 7=purple, 8=grey, 9=light_red ... etc.
_CALLOUT_MAP: dict[str, tuple[str, int]] = {
    "NOTE": ("BLUE_BOOK", 6),
    "WARNING": ("WARNING_SIGN", 3),
    "IMPORTANT": ("EXCLAMATION", 1),
    "TIP": ("BULB", 4),
    "CAUTION": ("FIRE", 2),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_id() -> str:
    """Generate a block ID (UUID without dashes for Feishu compat)."""
    return uuid.uuid4().hex


def _text_element(content: str, style: dict | None = None) -> dict:
    """Build a single text_run element."""
    el: dict[str, Any] = {"text_run": {"content": content}}
    if style:
        el["text_run"]["text_element_style"] = style
    return el


def _text_block(block_id: str, elements: list[dict]) -> dict:
    """Build a text block (type 2)."""
    return {
        "block_id": block_id,
        "block_type": BLOCK_TEXT,
        "text": {"elements": elements},
    }


def _heading_block(block_id: str, level: int, elements: list[dict]) -> dict:
    """Build a heading block (type 3–8)."""
    bt = BLOCK_HEADING1 + level - 1  # h1->3, h2->4, ...
    key = f"heading{level}"
    return {
        "block_id": block_id,
        "block_type": bt,
        key: {"elements": elements},
    }


# ---------------------------------------------------------------------------
# Inline parsing — convert markdown-it inline tokens to Feishu text elements
# ---------------------------------------------------------------------------


def _parse_inline_tokens(tokens: list) -> list[dict]:
    """Walk markdown-it inline children and produce Feishu text elements."""
    elements: list[dict] = []
    style_stack: list[str] = []
    link_href: str | None = None

    for tok in tokens:
        if tok.type == "text":
            elements.append(_styled_element(tok.content, style_stack, link_href))
        elif tok.type == "code_inline":
            elements.append(_styled_element(tok.content, [*style_stack, "code"], link_href))
        elif tok.type == "softbreak" or tok.type == "hardbreak":
            elements.append(_text_element("\n"))
        elif tok.type == "strong_open":
            style_stack.append("bold")
        elif tok.type == "strong_close":
            _remove_last(style_stack, "bold")
        elif tok.type == "em_open":
            style_stack.append("italic")
        elif tok.type == "em_close":
            _remove_last(style_stack, "italic")
        elif tok.type == "s_open":
            style_stack.append("strikethrough")
        elif tok.type == "s_close":
            _remove_last(style_stack, "strikethrough")
        elif tok.type == "link_open":
            link_href = _attr_value(tok, "href")
        elif tok.type == "link_close":
            link_href = None
        elif tok.type == "image":
            # Inline image — emit as text with URL for now
            src = _attr_value(tok, "src")
            alt = tok.content or ""
            if src:
                elements.append(_text_element(f"![{alt}]({src})"))
            elif alt:
                elements.append(_text_element(alt))
        elif tok.type == "math_inline":
            elements.append({"equation": {"content": tok.content}})
        elif tok.type == "html_inline":
            elements.append(_text_element(tok.content))
        # Ignore unknown inline tokens silently
    return elements


def _attr_value(tok: Any, name: str) -> str:
    for attr_name, attr_val in (tok.attrs or {}).items():
        if attr_name == name:
            return attr_val
    return ""


def _styled_element(content: str, styles: list[str], link_href: str | None) -> dict:
    """Build a text_run element with accumulated styles."""
    style: dict[str, Any] = {}
    if "bold" in styles:
        style["bold"] = True
    if "italic" in styles:
        style["italic"] = True
    if "strikethrough" in styles:
        style["strikethrough"] = True
    if "code" in styles:
        style["inline_code"] = True
    if link_href:
        style["link"] = {"url": link_href}
    return _text_element(content, style if style else None)


def _remove_last(lst: list[str], value: str) -> None:
    """Remove last occurrence of *value* from *lst*."""
    for i in range(len(lst) - 1, -1, -1):
        if lst[i] == value:
            lst.pop(i)
            return


# ---------------------------------------------------------------------------
# Block-level token processing
# ---------------------------------------------------------------------------


def _collect_inline_elements(token) -> list[dict]:
    """Extract Feishu text elements from a token's inline children."""
    if token.children:
        return _parse_inline_tokens(token.children)
    if token.content:
        return [_text_element(token.content)]
    return [_text_element("")]


class _Converter:
    """Stateful converter that walks markdown-it tokens and emits Feishu blocks."""

    def __init__(self) -> None:
        self.children_ids: list[str] = []  # top-level block IDs
        self.descendants: list[dict] = []  # all blocks (flat)

    def _add_top(self, block: dict) -> str:
        """Register a top-level block and return its ID."""
        bid = block["block_id"]
        self.children_ids.append(bid)
        self.descendants.append(block)
        return bid

    def _add_child(self, block: dict) -> str:
        """Register a descendant block (not top-level) and return its ID."""
        bid = block["block_id"]
        self.descendants.append(block)
        return bid

    # ------------------------------------------------------------------

    def convert(self, tokens: list) -> tuple[list[str], list[dict]]:
        """Process top-level markdown-it tokens and return (children_ids, descendants)."""
        i = 0
        while i < len(tokens):
            tok = tokens[i]

            if tok.type == "heading_open":
                level = int(tok.tag[1])  # h1 -> 1
                inline_tok = tokens[i + 1] if i + 1 < len(tokens) else None
                elements = (
                    _collect_inline_elements(inline_tok) if inline_tok else [_text_element("")]
                )
                bid = _new_id()
                self._add_top(_heading_block(bid, min(level, 6), elements))
                i += 3  # heading_open, inline, heading_close
                continue

            if tok.type == "paragraph_open":
                inline_tok = tokens[i + 1] if i + 1 < len(tokens) else None
                elements = (
                    _collect_inline_elements(inline_tok) if inline_tok else [_text_element("")]
                )
                bid = _new_id()
                self._add_top(_text_block(bid, elements))
                i += 3  # paragraph_open, inline, paragraph_close
                continue

            if tok.type == "bullet_list_open":
                end = _find_close(tokens, i, "bullet_list_open", "bullet_list_close")
                self._convert_list(tokens[i + 1 : end], BLOCK_BULLET, top_level=True)
                i = end + 1
                continue

            if tok.type == "ordered_list_open":
                end = _find_close(tokens, i, "ordered_list_open", "ordered_list_close")
                self._convert_list(tokens[i + 1 : end], BLOCK_ORDERED, top_level=True)
                i = end + 1
                continue

            if tok.type == "fence":
                self._convert_code_block(tok)
                i += 1
                continue

            if tok.type == "code_block":
                self._convert_code_block(tok)
                i += 1
                continue

            if tok.type == "hr":
                bid = _new_id()
                self._add_top(
                    {
                        "block_id": bid,
                        "block_type": BLOCK_DIVIDER,
                        "divider": {},
                    }
                )
                i += 1
                continue

            if tok.type == "blockquote_open":
                end = _find_close(tokens, i, "blockquote_open", "blockquote_close")
                self._convert_blockquote(tokens[i + 1 : end])
                i = end + 1
                continue

            if tok.type == "table_open":
                end = _find_close(tokens, i, "table_open", "table_close")
                self._convert_table(tokens[i + 1 : end])
                i = end + 1
                continue

            if tok.type == "html_block":
                # Try to handle image tags
                img_match = re.search(r'<img\s+[^>]*src="([^"]*)"', tok.content or "")
                if img_match:
                    bid = _new_id()
                    self._add_top(
                        {
                            "block_id": bid,
                            "block_type": BLOCK_IMAGE,
                            "image": {"url": img_match.group(1)},
                        }
                    )
                else:
                    # Pass through as text
                    bid = _new_id()
                    self._add_top(_text_block(bid, [_text_element(tok.content.strip())]))
                i += 1
                continue

            if tok.type == "inline":
                # Standalone inline (shouldn't happen often)
                elements = _collect_inline_elements(tok)
                bid = _new_id()
                self._add_top(_text_block(bid, elements))
                i += 1
                continue

            # Skip close tokens and other unknown tokens
            i += 1

        return self.children_ids, self.descendants

    # ------------------------------------------------------------------
    # Lists
    # ------------------------------------------------------------------

    def _convert_list(
        self,
        tokens: list,
        block_type: int,
        top_level: bool = False,
    ) -> list[str]:
        """Convert list_item tokens into bullet/ordered/todo blocks.

        Returns list of block IDs created.
        """
        ids: list[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.type == "list_item_open":
                end = _find_close(tokens, i, "list_item_open", "list_item_close")
                item_tokens = tokens[i + 1 : end]
                bid = self._convert_list_item(item_tokens, block_type, top_level)
                ids.append(bid)
                i = end + 1
            else:
                i += 1
        return ids

    def _convert_list_item(
        self,
        tokens: list,
        block_type: int,
        top_level: bool,
    ) -> str:
        """Convert a single list item's content into a block.

        Handles nested lists by creating child blocks.
        """
        # Separate inline content from nested lists
        inline_elements: list[dict] = []
        child_ids: list[str] = []
        actual_block_type = block_type
        todo_done = False

        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.type == "paragraph_open":
                inline_tok = tokens[i + 1] if i + 1 < len(tokens) else None
                if inline_tok and inline_tok.type == "inline":
                    # Check for todo checkbox
                    content = inline_tok.content or ""
                    if content.startswith(("[ ] ", "[x] ", "[X] ")):
                        actual_block_type = BLOCK_TODO
                        todo_done = content[1] in ("x", "X")
                        # Strip the checkbox prefix from content
                        inline_tok = _clone_inline_without_checkbox(inline_tok)
                    elems = _collect_inline_elements(inline_tok)
                    inline_elements.extend(elems)
                i += 3  # paragraph_open, inline, paragraph_close
                continue
            if tok.type == "bullet_list_open":
                end = _find_close(tokens, i, "bullet_list_open", "bullet_list_close")
                nested_ids = self._convert_list(tokens[i + 1 : end], BLOCK_BULLET, top_level=False)
                child_ids.extend(nested_ids)
                i = end + 1
                continue
            if tok.type == "ordered_list_open":
                end = _find_close(tokens, i, "ordered_list_open", "ordered_list_close")
                nested_ids = self._convert_list(tokens[i + 1 : end], BLOCK_ORDERED, top_level=False)
                child_ids.extend(nested_ids)
                i = end + 1
                continue
            i += 1

        if not inline_elements:
            inline_elements = [_text_element("")]

        bid = _new_id()

        if actual_block_type == BLOCK_TODO:
            block: dict[str, Any] = {
                "block_id": bid,
                "block_type": BLOCK_TODO,
                "todo": {
                    "elements": inline_elements,
                    "style": {"done": todo_done},
                },
            }
        elif actual_block_type == BLOCK_ORDERED:
            block = {
                "block_id": bid,
                "block_type": BLOCK_ORDERED,
                "ordered": {"elements": inline_elements},
            }
        else:
            block = {
                "block_id": bid,
                "block_type": BLOCK_BULLET,
                "bullet": {"elements": inline_elements},
            }

        if child_ids:
            block["children"] = child_ids

        if top_level:
            self._add_top(block)
        else:
            self._add_child(block)
        return bid

    # ------------------------------------------------------------------
    # Code blocks
    # ------------------------------------------------------------------

    def _convert_code_block(self, tok) -> None:
        lang_str = (tok.info or "").strip().lower()
        lang_id = _LANG_TO_ID.get(lang_str, 1)
        bid = _new_id()
        content = tok.content or ""
        # Strip trailing newline that markdown-it adds
        if content.endswith("\n"):
            content = content[:-1]
        self._add_top(
            {
                "block_id": bid,
                "block_type": BLOCK_CODE,
                "code": {
                    "elements": [_text_element(content)],
                    "style": {"language": lang_id},
                },
            }
        )

    # ------------------------------------------------------------------
    # Blockquotes & callouts
    # ------------------------------------------------------------------

    def _convert_blockquote(self, tokens: list) -> None:
        """Convert blockquote tokens; detect callout syntax ``[!TYPE]``."""
        # Peek at first inline to detect callout
        callout_type = None
        for tok in tokens:
            if tok.type == "inline":
                content = tok.content or ""
                m = re.match(r"^\[!(NOTE|WARNING|IMPORTANT|TIP|CAUTION)\]", content, re.IGNORECASE)
                if m:
                    callout_type = m.group(1).upper()
                break
            if tok.type == "paragraph_open":
                continue
            break

        if callout_type:
            self._convert_callout(tokens, callout_type)
        else:
            self._convert_quote_container(tokens)

    def _convert_callout(self, tokens: list, callout_type: str) -> None:
        """Emit a callout block (type 19)."""
        emoji, bg_color = _CALLOUT_MAP.get(callout_type, ("BLUE_BOOK", 6))

        # Collect child blocks for the callout body
        child_ids: list[str] = []
        first_inline_seen = False
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.type == "paragraph_open":
                inline_tok = tokens[i + 1] if i + 1 < len(tokens) else None
                if inline_tok and inline_tok.type == "inline":
                    content = inline_tok.content or ""
                    if not first_inline_seen:
                        first_inline_seen = True
                        # Strip the [!TYPE] prefix
                        content = re.sub(
                            r"^\[!(NOTE|WARNING|IMPORTANT|TIP|CAUTION)\]\s*",
                            "",
                            content,
                            flags=re.IGNORECASE,
                        )
                        if content.strip():
                            child_bid = _new_id()
                            child_ids.append(child_bid)
                            self._add_child(_text_block(child_bid, [_text_element(content)]))
                    else:
                        child_bid = _new_id()
                        child_ids.append(child_bid)
                        elements = _collect_inline_elements(inline_tok)
                        self._add_child(_text_block(child_bid, elements))
                i += 3
                continue
            i += 1

        bid = _new_id()
        block: dict[str, Any] = {
            "block_id": bid,
            "block_type": BLOCK_CALLOUT,
            "callout": {
                "emoji_id": emoji,
                "background_color": bg_color,
            },
        }
        if child_ids:
            block["children"] = child_ids
        self._add_top(block)

    def _convert_quote_container(self, tokens: list) -> None:
        """Emit a quote container block (type 34) with text children."""
        child_ids: list[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.type == "paragraph_open":
                inline_tok = tokens[i + 1] if i + 1 < len(tokens) else None
                if inline_tok and inline_tok.type == "inline":
                    elements = _collect_inline_elements(inline_tok)
                    child_bid = _new_id()
                    child_ids.append(child_bid)
                    self._add_child(_text_block(child_bid, elements))
                i += 3
                continue
            i += 1

        bid = _new_id()
        block: dict[str, Any] = {
            "block_id": bid,
            "block_type": BLOCK_QUOTE_CONTAINER,
            "quote_container": {},
        }
        if child_ids:
            block["children"] = child_ids
        self._add_top(block)

    # ------------------------------------------------------------------
    # Tables (with auto-split)
    # ------------------------------------------------------------------

    def _convert_table(self, tokens: list) -> None:
        """Parse markdown table tokens into a 2D grid, then emit table blocks.

        Automatically splits tables exceeding 9x9 Feishu limits.
        """
        rows: list[list[list[dict]]] = []  # rows -> cells -> elements
        current_row: list[list[dict]] = []

        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.type in ("thead_open", "thead_close"):
                i += 1
                continue
            if tok.type == "tbody_open" or tok.type == "tbody_close":
                i += 1
                continue
            if tok.type == "tr_open":
                current_row = []
                i += 1
                continue
            if tok.type == "tr_close":
                rows.append(current_row)
                i += 1
                continue
            if tok.type in ("th_open", "td_open"):
                # Next token should be inline
                inline_tok = tokens[i + 1] if i + 1 < len(tokens) else None
                if inline_tok and inline_tok.type == "inline":
                    elements = _collect_inline_elements(inline_tok)
                    current_row.append(elements)
                    i += 3  # th_open, inline, th_close/td_close
                else:
                    current_row.append([_text_element("")])
                    i += 2  # th_open, th_close
                continue
            i += 1

        if not rows:
            return

        has_header = True  # markdown tables always have a header row
        num_cols = max(len(r) for r in rows) if rows else 0
        num_rows = len(rows)

        # Normalize: ensure all rows have same column count
        for row in rows:
            while len(row) < num_cols:
                row.append([_text_element("")])

        # Split if needed
        if num_rows <= MAX_TABLE_ROWS and num_cols <= MAX_TABLE_COLS:
            self._emit_table_block(rows, has_header)
        else:
            self._emit_split_tables(rows, has_header)

    def _emit_split_tables(self, rows: list[list[list[dict]]], has_header: bool) -> None:
        """Split a large table and emit multiple table blocks.

        Strategy:
        1. Column split first (preserve first column as identifier)
        2. Then row split (preserve header row in each chunk)
        """
        num_cols = len(rows[0]) if rows else 0

        # Determine column groups
        col_groups = _split_column_groups(num_cols, MAX_TABLE_COLS)

        # Determine row groups (excluding header which is repeated)
        header_row = rows[0] if has_header else None
        data_rows = rows[1:] if has_header else rows

        # Max data rows per chunk: MAX_TABLE_ROWS - 1 (if header) or MAX_TABLE_ROWS
        max_data_rows = MAX_TABLE_ROWS - 1 if header_row else MAX_TABLE_ROWS
        row_groups = _split_row_groups(len(data_rows), max_data_rows)

        for col_group in col_groups:
            for row_group in row_groups:
                chunk_rows: list[list[list[dict]]] = []
                if header_row:
                    chunk_rows.append([header_row[c] for c in col_group])
                chunk_rows.extend([data_rows[ri][c] for c in col_group] for ri in row_group)
                self._emit_table_block(chunk_rows, has_header=header_row is not None)

    def _emit_table_block(self, rows: list[list[list[dict]]], has_header: bool) -> None:
        """Emit a single table block with cell children."""
        if not rows:
            return
        num_rows = len(rows)
        num_cols = len(rows[0])

        cell_ids: list[str] = []
        for row in rows:
            for cell_elements in row:
                cell_bid = _new_id()
                # Each table cell contains a text block child
                text_bid = _new_id()
                text_block = _text_block(text_bid, cell_elements)
                self._add_child(text_block)

                cell_block: dict[str, Any] = {
                    "block_id": cell_bid,
                    "block_type": BLOCK_TABLE_CELL,
                    "table_cell": {},
                    "children": [text_bid],
                }
                self._add_child(cell_block)
                cell_ids.append(cell_bid)

        table_bid = _new_id()
        table_block: dict[str, Any] = {
            "block_id": table_bid,
            "block_type": BLOCK_TABLE,
            "table": {
                "property": {
                    "row_size": num_rows,
                    "column_size": num_cols,
                    "header_row": has_header,
                },
            },
            "children": cell_ids,
        }
        self._add_top(table_block)


# ---------------------------------------------------------------------------
# Table split helpers
# ---------------------------------------------------------------------------


def _split_column_groups(num_cols: int, max_cols: int) -> list[list[int]]:
    """Split column indices into groups of at most *max_cols*.

    First column (index 0) is repeated in every group as the identifier column.
    """
    if num_cols <= max_cols:
        return [list(range(num_cols))]

    groups: list[list[int]] = []
    # First group: columns 0 .. max_cols-1
    groups.append(list(range(max_cols)))
    # Subsequent groups: column 0 + next batch
    col = max_cols
    while col < num_cols:
        end = min(col + max_cols - 1, num_cols)  # -1 because col 0 is included
        group = [0, *range(col, end)]
        groups.append(group)
        col = end
    return groups


def _split_row_groups(num_data_rows: int, max_data_rows: int) -> list[list[int]]:
    """Split data row indices into groups of at most *max_data_rows*."""
    if num_data_rows <= max_data_rows:
        return [list(range(num_data_rows))]

    groups: list[list[int]] = []
    row = 0
    while row < num_data_rows:
        end = min(row + max_data_rows, num_data_rows)
        groups.append(list(range(row, end)))
        row = end
    return groups


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def _find_close(tokens: list, start: int, open_type: str, close_type: str) -> int:
    """Find the matching close token for a given open token."""
    depth = 0
    for j in range(start, len(tokens)):
        if tokens[j].type == open_type:
            depth += 1
        elif tokens[j].type == close_type:
            depth -= 1
            if depth == 0:
                return j
    return len(tokens) - 1


def _clone_inline_without_checkbox(tok) -> Any:
    """Create a modified inline token with checkbox prefix stripped from children."""

    if not tok.children:
        return tok

    new_children = list(tok.children)
    # The first text child should contain the checkbox
    for idx, child in enumerate(new_children):
        if child.type == "text":
            content = child.content or ""
            stripped = re.sub(r"^\[[ xX]\]\s*", "", content)
            # Create a simple replacement
            new_children[idx] = type(child)(
                type=child.type,
                tag=child.tag,
                nesting=child.nesting,
                attrs=child.attrs,
                map=child.map,
                level=child.level,
                children=child.children,
                content=stripped,
                markup=child.markup,
                info=child.info,
                meta=child.meta,
                block=child.block,
                hidden=child.hidden,
            )
            break

    return SimpleNamespace(
        children=new_children,
        content=re.sub(r"^\[[ xX]\]\s*", "", tok.content or ""),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def markdown_to_feishu_blocks(markdown: str) -> tuple[list[str], list[dict]]:
    """Convert a Markdown string to Feishu block structures (client-side).

    Returns:
        ``(children_ids, descendants)`` ready for
        :func:`~src.feishu.api_write.create_descendant_blocks`.
    """
    md = MarkdownIt("commonmark", {"breaks": False}).enable("table").enable("strikethrough")
    tokens = md.parse(markdown)
    converter = _Converter()
    return converter.convert(tokens)
