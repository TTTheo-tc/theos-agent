import os
from types import SimpleNamespace

from src.utils.proxy import apply_http_proxy_env, first_supported_proxy_env, is_socks_proxy
from src.utils.text import split_message, strip_think, tool_hint
from src.utils.tokenize import is_ascii_term, tokenize_query
from src.utils.truncation import truncate_tool_call_arguments
from src.utils.usage import merge_usage


def test_strip_think_removes_model_reasoning_blocks():
    assert strip_think("hello <think>hidden</think> world") == "hello  world"
    assert strip_think("<think>hidden</think>") is None
    assert strip_think(None) is None


def test_tool_hint_formats_first_string_argument():
    calls = [
        SimpleNamespace(name="read_file", arguments={"path": "src/main.py"}),
        SimpleNamespace(name="bash", arguments={"cmd": "x" * 50}),
        SimpleNamespace(name="noop", arguments={}),
    ]

    assert tool_hint(calls) == 'read_file("src/main.py"), bash("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx…"), noop'


def test_split_message_prefers_newlines_then_spaces():
    assert split_message("alpha\nbeta gamma", max_len=8) == ["alpha", "beta", "gamma"]
    assert split_message("abcdefghij", max_len=4) == ["abcd", "efgh", "ij"]
    assert split_message("\nabcdef", max_len=4) == ["\nabc", "def"]


def test_tokenize_query_dedupes_ascii_and_cjk_terms():
    tokens = tokenize_query("Use asyncio asyncio 架构设计")

    assert "asyncio" in tokens
    assert tokens.count("asyncio") == 1
    assert "架构设计" in tokens
    assert "架构" in tokens
    assert "use" not in tokens
    assert is_ascii_term("src/utils.py") is True
    assert is_ascii_term("Src/Utils.py") is True
    assert is_ascii_term("架构") is False


def test_truncate_tool_call_arguments_preserves_json_shape():
    calls = [{"function": {"name": "write_file", "arguments": {"content": "x" * 250}}}]

    result = truncate_tool_call_arguments(calls, max_chars=10)

    assert result is not None
    args = result[0]["function"]["arguments"]
    assert '"content"' in args
    assert "[truncated]" in args


def test_merge_usage_accumulates_known_usage_keys():
    target = {"prompt_tokens": 3}

    merge_usage(target, {"prompt_tokens": 2, "completion_tokens": 4, "ignored": 99})

    assert target == {"prompt_tokens": 5, "completion_tokens": 4}


def test_proxy_helpers_ignore_socks_values(monkeypatch):
    monkeypatch.setenv("all_proxy", "socks5h://127.0.0.1:7890")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:7890")

    assert is_socks_proxy("socks5h://127.0.0.1:7890") is True
    assert first_supported_proxy_env() == "http://127.0.0.1:7890"


def test_apply_http_proxy_env_sets_missing_http_keys(monkeypatch):
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.delenv(key, raising=False)

    assert apply_http_proxy_env("http://127.0.0.1:7890") is True
    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert apply_http_proxy_env("socks5h://127.0.0.1:7890") is False
