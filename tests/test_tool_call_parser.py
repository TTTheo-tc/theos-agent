"""Tests for the strict tool-call parser."""

from src.providers.base import ToolCallRequest
from src.providers.tool_call_parser import FALLBACK_PROVIDER_ALLOWLIST, parse_tool_calls_from_text

# ---------------------------------------------------------------------------
# Accepted formats
# ---------------------------------------------------------------------------


def test_xml_tool_call_tag():
    text = '<tool_call>{"name": "search", "arguments": {"q": "hello"}}</tool_call>'
    result = parse_tool_calls_from_text(text)
    assert len(result) == 1
    tc = result[0]
    assert isinstance(tc, ToolCallRequest)
    assert tc.name == "search"
    assert tc.arguments == {"q": "hello"}
    assert tc.id.startswith("parsed_")


def test_xml_function_call_tag():
    text = '<FunctionCall>{"name": "read_file", "arguments": {"path": "/tmp/a"}}</FunctionCall>'
    result = parse_tool_calls_from_text(text)
    assert len(result) == 1
    tc = result[0]
    assert tc.name == "read_file"
    assert tc.arguments == {"path": "/tmp/a"}
    assert tc.id.startswith("parsed_")


def test_fenced_tool_tag():
    text = '```tool\n{"name": "list_files", "arguments": {"dir": "/tmp"}}\n```'
    result = parse_tool_calls_from_text(text)
    assert len(result) == 1
    assert result[0].name == "list_files"
    assert result[0].arguments == {"dir": "/tmp"}


def test_fenced_tool_call_tag():
    text = '```tool_call\n{"name": "write_file", "arguments": {"path": "/a", "content": "x"}}\n```'
    result = parse_tool_calls_from_text(text)
    assert len(result) == 1
    assert result[0].name == "write_file"
    assert result[0].arguments == {"path": "/a", "content": "x"}


def test_multiple_tool_calls_in_one_text():
    text = (
        '<tool_call>{"name": "search", "arguments": {"q": "foo"}}</tool_call>\n'
        "Some prose in between.\n"
        '<FunctionCall>{"name": "read_file", "arguments": {"path": "/b"}}</FunctionCall>'
    )
    result = parse_tool_calls_from_text(text)
    assert len(result) == 2
    assert result[0].name == "search"
    assert result[1].name == "read_file"


def test_multiple_fenced_blocks():
    text = (
        '```tool\n{"name": "a", "arguments": {}}\n```\n'
        '```tool_call\n{"name": "b", "arguments": {"x": 1}}\n```'
    )
    result = parse_tool_calls_from_text(text)
    assert len(result) == 2
    assert result[0].name == "a"
    assert result[1].name == "b"


def test_mixed_wrapper_formats_preserve_source_order():
    text = (
        '```tool\n{"name": "first", "arguments": {}}\n```\n'
        '<tool_call>{"name": "second", "arguments": {}}</tool_call>\n'
        '```tool_call\n{"name": "third", "arguments": {}}\n```'
    )
    result = parse_tool_calls_from_text(text)
    assert [tc.name for tc in result] == ["first", "second", "third"]


def test_unique_ids_per_call():
    text = (
        '<tool_call>{"name": "x", "arguments": {}}</tool_call>'
        '<tool_call>{"name": "y", "arguments": {}}</tool_call>'
    )
    result = parse_tool_calls_from_text(text)
    assert result[0].id != result[1].id


def test_xml_tag_case_insensitive_tool_call():
    # TOOL_CALL and Tool_Call should also be matched (case-insensitive)
    text = '<TOOL_CALL>{"name": "ping", "arguments": {}}</TOOL_CALL>'
    result = parse_tool_calls_from_text(text)
    assert len(result) == 1
    assert result[0].name == "ping"


# ---------------------------------------------------------------------------
# Rejected formats
# ---------------------------------------------------------------------------


def test_plain_json_in_prose_rejected():
    text = 'You could try {"name": "search", "arguments": {"q": "x"}} to find it.'
    result = parse_tool_calls_from_text(text)
    assert result == []


def test_unrelated_xml_tag_rejected():
    text = '<response>{"name": "search", "arguments": {"q": "x"}}</response>'
    result = parse_tool_calls_from_text(text)
    assert result == []


def test_missing_name_field_rejected():
    text = '<tool_call>{"arguments": {"q": "hello"}}</tool_call>'
    result = parse_tool_calls_from_text(text)
    assert result == []


def test_missing_arguments_field_rejected():
    text = '<tool_call>{"name": "search"}</tool_call>'
    result = parse_tool_calls_from_text(text)
    assert result == []


def test_empty_text_rejected():
    result = parse_tool_calls_from_text("")
    assert result == []


def test_none_rejected():
    result = parse_tool_calls_from_text(None)
    assert result == []


def test_arguments_not_dict_rejected():
    text = '<tool_call>{"name": "search", "arguments": "not-a-dict"}</tool_call>'
    result = parse_tool_calls_from_text(text)
    assert result == []


def test_name_not_string_rejected():
    text = '<tool_call>{"name": 42, "arguments": {}}</tool_call>'
    result = parse_tool_calls_from_text(text)
    assert result == []


def test_fenced_block_wrong_language_rejected():
    text = '```json\n{"name": "search", "arguments": {}}\n```'
    result = parse_tool_calls_from_text(text)
    assert result == []


def test_mixed_valid_and_invalid_returns_only_valid():
    text = (
        '<tool_call>{"name": "good", "arguments": {"k": "v"}}</tool_call>\n'
        '<tool_call>{"arguments": {"k": "v"}}</tool_call>\n'  # missing name
    )
    result = parse_tool_calls_from_text(text)
    assert len(result) == 1
    assert result[0].name == "good"


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


def test_allowlist_includes_expected_providers():
    expected_in = {"deepseek", "minimax", "groq", "zhipu", "moonshot", "dashscope", "vllm"}
    for provider in expected_in:
        assert provider in FALLBACK_PROVIDER_ALLOWLIST, f"{provider!r} should be in allowlist"


def test_allowlist_excludes_native_providers():
    expected_out = {"anthropic", "openai", "custom"}
    for provider in expected_out:
        assert (
            provider not in FALLBACK_PROVIDER_ALLOWLIST
        ), f"{provider!r} should NOT be in allowlist"
