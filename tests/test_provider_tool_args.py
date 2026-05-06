"""Tests for provider tool-call argument parsing helpers."""

from __future__ import annotations

from src.providers.tool_args import parse_tool_arguments_object


def test_parse_tool_arguments_accepts_dict() -> None:
    assert parse_tool_arguments_object({"path": "/tmp/a.txt"}) == {"path": "/tmp/a.txt"}


def test_parse_tool_arguments_repairs_json_object() -> None:
    assert parse_tool_arguments_object('{"path": "/tmp/a.txt",}') == {"path": "/tmp/a.txt"}


def test_parse_tool_arguments_returns_empty_dict_for_non_object() -> None:
    assert parse_tool_arguments_object('["not", "object"]') == {}
    assert parse_tool_arguments_object("not json") == {}
    assert parse_tool_arguments_object(None) == {}


def test_parse_tool_arguments_can_preserve_raw_without_repair() -> None:
    assert parse_tool_arguments_object(
        '["not", "object"]',
        preserve_raw=True,
        repair_json=False,
    ) == {"raw": '["not", "object"]'}
    assert parse_tool_arguments_object(
        "not json",
        preserve_raw=True,
        repair_json=False,
    ) == {"raw": "not json"}
