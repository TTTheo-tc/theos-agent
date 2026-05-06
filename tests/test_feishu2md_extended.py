"""Tests for extended block types in src.feishu.feishu2md."""

from __future__ import annotations

import pytest

from src.feishu.feishu2md import FeishuParser


def _make_parser(blocks: list[dict]) -> FeishuParser:
    """Create a parser and populate its block_map from a flat list of blocks."""
    parser = FeishuParser()
    for b in blocks:
        if "block_id" in b:
            parser.block_map[b["block_id"]] = b
    return parser


# ---------------------------------------------------------------------------
# Divider (type 22)
# ---------------------------------------------------------------------------


class TestDividerBlock:
    def test_renders_horizontal_rule(self):
        block = {"block_id": "div1", "block_type": 22}
        parser = _make_parser([block])
        assert parser.parse_block(block).strip() == "---"


# ---------------------------------------------------------------------------
# Heading range (types 3-11)
# ---------------------------------------------------------------------------


class TestHeadingBlocks:
    @pytest.mark.parametrize(
        "block_type,key,expected",
        [
            (3, "heading1", "# H1"),
            (11, "heading9", "######### H9"),
        ],
    )
    def test_heading_range_mapping(self, block_type, key, expected):
        block = {
            "block_id": f"h{block_type}",
            "block_type": block_type,
            key: {"elements": [{"text_run": {"content": expected.rsplit(" ", 1)[-1]}}]},
        }
        parser = _make_parser([block])
        assert parser.parse_block(block) == expected


# ---------------------------------------------------------------------------
# ChatCard (type 20)
# ---------------------------------------------------------------------------


class TestChatCardBlock:
    def test_with_name_and_url(self):
        block = {
            "block_id": "cc1",
            "block_type": 20,
            "chat_card": {
                "chat_id": "oc_abc",
                "name": "Engineering",
                "url": "https://example.com/chat",
            },
        }
        parser = _make_parser([block])
        assert parser.parse_block(block) == "[Chat: Engineering](https://example.com/chat)"

    def test_with_name_only(self):
        block = {
            "block_id": "cc2",
            "block_type": 20,
            "chat_card": {"name": "Product Team"},
        }
        parser = _make_parser([block])
        assert parser.parse_block(block) == "[Chat: Product Team]"

    def test_with_chat_id_only(self):
        block = {
            "block_id": "cc3",
            "block_type": 20,
            "chat_card": {"chat_id": "oc_xyz"},
        }
        parser = _make_parser([block])
        assert parser.parse_block(block) == "[Feishu Chat: oc_xyz]"

    def test_fallback_block_id(self):
        block = {"block_id": "cc4", "block_type": 20}
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "cc4" in result
        assert "Feishu Chat Group" in result


# ---------------------------------------------------------------------------
# Diagram (type 21)
# ---------------------------------------------------------------------------


class TestDiagramBlock:
    def test_with_token(self):
        block = {
            "block_id": "d1",
            "block_type": 21,
            "diagram": {"diagram_type": 1, "token": "diag_abc"},
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "Flowchart" in result
        assert "diag_abc" in result

    def test_with_children(self):
        child = {
            "block_id": "d1c1",
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": "Step A -> Step B"}}]},
        }
        block = {
            "block_id": "d1",
            "block_type": 21,
            "diagram": {"diagram_type": 2},
            "children": ["d1c1"],
        }
        parser = _make_parser([block, child])
        result = parser.parse_block(block)
        assert "UML" in result
        assert "Step A -> Step B" in result

    def test_fallback(self):
        block = {"block_id": "d2", "block_type": 21, "diagram": {}}
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "Diagram" in result
        assert "d2" in result


# ---------------------------------------------------------------------------
# Grid / Grid Column (types 24-25)
# ---------------------------------------------------------------------------


class TestGridBlocks:
    def test_grid_renders_children_sequentially(self):
        col1_child = {
            "block_id": "gc1c1",
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": "Left column"}}]},
        }
        col2_child = {
            "block_id": "gc2c1",
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": "Right column"}}]},
        }
        col1 = {"block_id": "gc1", "block_type": 25, "children": ["gc1c1"]}
        col2 = {"block_id": "gc2", "block_type": 25, "children": ["gc2c1"]}
        grid = {"block_id": "g1", "block_type": 24, "children": ["gc1", "gc2"]}
        parser = _make_parser([grid, col1, col2, col1_child, col2_child])
        result = parser.parse_block(grid)
        assert "Left column" in result
        assert "Right column" in result
        # Should NOT contain HTML-like grid tags
        assert "<grid>" not in result
        assert "<grid_column>" not in result

    def test_grid_and_column_keep_existing_separators(self):
        left = {
            "block_id": "gc1c1",
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": "Left"}}]},
        }
        right = {
            "block_id": "gc2c1",
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": "Right"}}]},
        }
        col1 = {"block_id": "gc1", "block_type": 25, "children": ["gc1c1"]}
        col2 = {"block_id": "gc2", "block_type": 25, "children": ["gc2c1"]}
        grid = {"block_id": "g1", "block_type": 24, "children": ["gc1", "missing", "gc2"]}
        parser = _make_parser([grid, col1, col2, left, right])

        assert parser.parse_block(col1) == "Left\n"
        assert parser.parse_block(grid) == "Left\n\n\nRight\n\n"

    def test_empty_grid(self):
        grid = {"block_id": "g2", "block_type": 24, "children": []}
        parser = _make_parser([grid])
        result = parser.parse_block(grid)
        assert result == ""


# ---------------------------------------------------------------------------
# View (type 33)
# ---------------------------------------------------------------------------


class TestViewBlock:
    def test_view_keeps_existing_child_separator(self):
        first = {
            "block_id": "v_c1",
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": "First"}}]},
        }
        second = {
            "block_id": "v_c2",
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": "Second"}}]},
        }
        block = {"block_id": "v1", "block_type": 33, "children": ["v_c1", "missing", "v_c2"]}
        parser = _make_parser([block, first, second])
        assert parser.parse_block(block) == "First\n\nSecond\n"


# ---------------------------------------------------------------------------
# Iframe (type 26)
# ---------------------------------------------------------------------------


class TestIframeBlock:
    def test_with_url(self):
        block = {
            "block_id": "if1",
            "block_type": 26,
            "iframe": {"url": "https://example.com/embed"},
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "https://example.com/embed" in result

    def test_empty_iframe(self):
        block = {"block_id": "if2", "block_type": 26, "iframe": {}}
        parser = _make_parser([block])
        assert parser.parse_block(block) == ""


# ---------------------------------------------------------------------------
# ISV Widget (type 28)
# ---------------------------------------------------------------------------


class TestISVBlock:
    def test_with_source_code(self):
        block = {
            "block_id": "isv1",
            "block_type": 28,
            "isv": {
                "source": "graph TD\n  A-->B",
                "language": "mermaid",
            },
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "```mermaid" in result
        assert "graph TD" in result

    def test_with_app_name(self):
        block = {
            "block_id": "isv2",
            "block_type": 28,
            "isv": {"app_name": "DrawIO"},
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "DrawIO" in result

    def test_with_app_id(self):
        block = {
            "block_id": "isv3",
            "block_type": 28,
            "isv": {"app_id": "cli_abc123"},
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "cli_abc123" in result

    def test_fallback(self):
        block = {"block_id": "isv4", "block_type": 28}
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "Feishu Widget" in result
        assert "isv4" in result


# ---------------------------------------------------------------------------
# Mindnote (type 29)
# ---------------------------------------------------------------------------


class TestMindnoteBlock:
    def test_with_token_and_title(self):
        block = {
            "block_id": "mn1",
            "block_type": 29,
            "mindnote": {"token": "mn_abc", "title": "Project Overview"},
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "Project Overview" in result
        assert "mn_abc" in result

    def test_with_token_only(self):
        block = {
            "block_id": "mn2",
            "block_type": 29,
            "mindnote": {"token": "mn_xyz"},
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "Mindmap" in result
        assert "mn_xyz" in result

    def test_fallback(self):
        block = {"block_id": "mn3", "block_type": 29, "mindnote": {}}
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "Mindmap" in result
        assert "mn3" in result


# ---------------------------------------------------------------------------
# Addons (type 40)
# ---------------------------------------------------------------------------


class TestAddonsBlock:
    def test_with_content(self):
        block = {
            "block_id": "ad1",
            "block_type": 40,
            "addons": {"content": "Survey Results"},
        }
        parser = _make_parser([block])
        assert parser.parse_block(block) == "[Addon: Survey Results]"

    def test_with_token(self):
        block = {
            "block_id": "ad2",
            "block_type": 40,
            "addons": {"token": "tok_xyz"},
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "tok_xyz" in result
        assert "Feishu Addon" in result

    def test_fallback(self):
        block = {"block_id": "ad3", "block_type": 40, "addons": {}}
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "Feishu Addon" in result
        assert "ad3" in result


# ---------------------------------------------------------------------------
# Jira Issue (type 41) — already well-implemented, verify it works
# ---------------------------------------------------------------------------


class TestJiraIssueBlock:
    def test_with_key_and_url(self):
        block = {
            "block_id": "j1",
            "block_type": 41,
            "jira_issue": {
                "key": "PROJ-123",
                "summary": "Fix login bug",
                "url": "https://jira.example.com/PROJ-123",
            },
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "PROJ-123" in result
        assert "Fix login bug" in result
        assert "https://jira.example.com/PROJ-123" in result

    def test_with_key_only(self):
        block = {
            "block_id": "j2",
            "block_type": 41,
            "jira_issue": {"key": "BUG-456"},
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "BUG-456" in result


# ---------------------------------------------------------------------------
# Wiki Catalog (type 42)
# ---------------------------------------------------------------------------


class TestWikiCatalogBlock:
    def test_with_wiki_token(self):
        block = {
            "block_id": "wc1",
            "block_type": 42,
            "wiki_catalog": {"wiki_token": "wiki_abc"},
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "wiki_abc" in result
        assert "Wiki Catalog" in result

    def test_with_children(self):
        child = {
            "block_id": "wc1c1",
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": "Getting Started"}}]},
        }
        block = {
            "block_id": "wc1",
            "block_type": 42,
            "wiki_catalog": {},
            "children": ["wc1c1"],
        }
        parser = _make_parser([block, child])
        result = parser.parse_block(block)
        assert "Getting Started" in result

    def test_fallback(self):
        block = {"block_id": "wc2", "block_type": 42}
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "Wiki Catalog" in result


# ---------------------------------------------------------------------------
# Board (type 43) — already well-implemented, verify it works
# ---------------------------------------------------------------------------


class TestBoardBlock:
    def test_with_content_nodes(self):
        block = {
            "block_id": "b1",
            "block_type": 43,
            "board": {
                "token": "board_abc",
                "content": {
                    "nodes": [
                        {"id": "n1", "type": "rect", "text": {"text": "Start"}},
                        {"id": "n2", "type": "rect", "text": {"text": "End"}},
                    ]
                },
            },
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "```board" in result
        assert "Start" in result
        assert "End" in result

    def test_without_content(self):
        block = {
            "block_id": "b2",
            "block_type": 43,
            "board": {"token": "board_xyz"},
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "board_xyz" in result


# ---------------------------------------------------------------------------
# Agenda (types 44-47)
# ---------------------------------------------------------------------------


class TestAgendaBlocks:
    def test_agenda_with_items(self):
        title_block = {
            "block_id": "at1",
            "block_type": 46,
            "agenda_item_title": {
                "elements": [{"text_run": {"content": "Q1 Review"}}],
            },
        }
        content_block = {
            "block_id": "ac1",
            "block_type": 47,
            "agenda_item_content": {
                "elements": [{"text_run": {"content": "Discuss Q1 metrics"}}],
            },
        }
        item_block = {
            "block_id": "ai1",
            "block_type": 45,
            "children": ["at1", "ac1"],
        }
        agenda_block = {
            "block_id": "ag1",
            "block_type": 44,
            "children": ["ai1"],
        }
        parser = _make_parser([agenda_block, item_block, title_block, content_block])
        result = parser.parse_block(agenda_block)
        assert "Q1 Review" in result
        assert "Discuss Q1 metrics" in result


# ---------------------------------------------------------------------------
# Link Preview (type 48)
# ---------------------------------------------------------------------------


class TestLinkPreviewBlock:
    def test_with_title_and_url(self):
        block = {
            "block_id": "lp1",
            "block_type": 48,
            "link_preview": {
                "title": "GitHub PR #42",
                "url": "https://github.com/org/repo/pull/42",
            },
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "GitHub PR #42" in result
        assert "https://github.com/org/repo/pull/42" in result

    def test_with_url_only(self):
        block = {
            "block_id": "lp2",
            "block_type": 48,
            "link_preview": {"url": "https://example.com/page"},
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "https://example.com/page" in result


# ---------------------------------------------------------------------------
# OKR blocks (types 36-39)
# ---------------------------------------------------------------------------


class TestOKRBlocks:
    def test_okr_with_objective_and_kr(self):
        kr_block = {
            "block_id": "kr1",
            "block_type": 38,
            "okr_key_result": {"content": "Ship v2.0 by March"},
        }
        obj_block = {
            "block_id": "obj1",
            "block_type": 37,
            "okr_objective": {"content": "Improve product quality"},
            "children": ["kr1"],
        }
        okr_block = {
            "block_id": "okr1",
            "block_type": 36,
            "children": ["obj1"],
        }
        parser = _make_parser([okr_block, obj_block, kr_block])
        result = parser.parse_block(okr_block)
        assert "OKR" in result
        assert "Improve product quality" in result
        assert "Ship v2.0 by March" in result

    def test_okr_progress(self):
        block = {
            "block_id": "p1",
            "block_type": 39,
            "okr_progress": {"percent": 75},
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "75%" in result


# ---------------------------------------------------------------------------
# Task (type 35)
# ---------------------------------------------------------------------------


class TestTaskBlock:
    def test_with_summary(self):
        block = {
            "block_id": "t1",
            "block_type": 35,
            "task": {"task_id": "task_abc", "summary": "Update docs"},
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "Update docs" in result
        assert "[ ]" in result

    def test_with_task_id_only(self):
        block = {
            "block_id": "t2",
            "block_type": 35,
            "task": {"task_id": "task_xyz"},
        }
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "task_xyz" in result


# ---------------------------------------------------------------------------
# AI Template (type 52)
# ---------------------------------------------------------------------------


class TestAITemplateBlock:
    def test_with_children(self):
        child = {
            "block_id": "ait_c1",
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": "Generated summary"}}]},
        }
        block = {
            "block_id": "ait1",
            "block_type": 52,
            "children": ["ait_c1"],
        }
        parser = _make_parser([block, child])
        result = parser.parse_block(block)
        assert "Generated summary" in result

    def test_fallback(self):
        block = {"block_id": "ait2", "block_type": 52}
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "AI Template" in result
        assert "ait2" in result


# ---------------------------------------------------------------------------
# Unknown block type — no raw <notice:...>
# ---------------------------------------------------------------------------


class TestUnknownBlock:
    def test_unknown_with_children(self):
        child = {
            "block_id": "unk_c1",
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": "child text"}}]},
        }
        block = {
            "block_id": "unk1",
            "block_type": 9999,
            "children": ["unk_c1"],
        }
        parser = _make_parser([block, child])
        result = parser.parse_block(block)
        assert "child text" in result
        assert "<notice:" not in result

    def test_unknown_without_children(self):
        block = {"block_id": "unk2", "block_type": 9999}
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "9999" in result
        assert "unk2" in result
        assert "<notice:" not in result


# ---------------------------------------------------------------------------
# Undefined (type 999) — no raw <notice:...>
# ---------------------------------------------------------------------------


class TestUndefinedBlock:
    def test_fallback(self):
        block = {"block_id": "undef1", "block_type": 999}
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "undef1" in result
        assert "<notice:" not in result


# ---------------------------------------------------------------------------
# Synced blocks (types 49-50) — verify existing behavior
# ---------------------------------------------------------------------------


class TestSourceSyncedBlock:
    def test_with_children(self):
        child = {
            "block_id": "ss_c1",
            "block_type": 2,
            "text": {"elements": [{"text_run": {"content": "synced content"}}]},
        }
        block = {
            "block_id": "ss1",
            "block_type": 49,
            "children": ["ss_c1"],
        }
        parser = _make_parser([block, child])
        result = parser.parse_block(block)
        assert "synced content" in result
        assert result == '<div id="ss1" class="source_synced">synced content\n</div>'


# ---------------------------------------------------------------------------
# No <notice: ...> leaks anywhere
# ---------------------------------------------------------------------------


class TestNoNoticeLeaks:
    """Ensure no block type produces output starting with '<notice:'."""

    # Block types that we can test with minimal/empty data
    MINIMAL_BLOCKS = [
        (20, {}),  # chatcard
        (21, {"diagram": {}}),  # diagram
        (28, {}),  # isv
        (29, {"mindnote": {}}),  # mindnote
        (40, {"addons": {}}),  # addons
        (42, {}),  # wiki catalog
        (52, {}),  # ai template
        (999, {}),  # undefined
    ]

    @pytest.mark.parametrize("block_type,extra", MINIMAL_BLOCKS)
    def test_no_notice_prefix(self, block_type, extra):
        block = {"block_id": f"test_{block_type}", "block_type": block_type, **extra}
        parser = _make_parser([block])
        result = parser.parse_block(block)
        assert "<notice:" not in result, f"type {block_type} still emits <notice:...>"
