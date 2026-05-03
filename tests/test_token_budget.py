"""Tests for src/memory/token_budget.py — shared token estimation utilities."""

from src.memory.token_budget import (
    estimate_messages_tokens,
    estimate_tokens,
    resolve_context_limit,
)


class TestEstimateTokens:
    def test_basic_division(self):
        result = estimate_tokens("a" * 400, safety_margin=1.0)
        assert 50 <= result <= 200

    def test_safety_margin_applied(self):
        base = estimate_tokens("a" * 400, safety_margin=1.0)
        with_margin = estimate_tokens("a" * 400, safety_margin=1.2)
        assert with_margin > base

    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_none(self):
        assert estimate_tokens(None) == 0


class TestEstimateMessagesTokens:
    def test_sums_content_fields(self):
        messages = [
            {"role": "system", "content": "a" * 400},
            {"role": "user", "content": "b" * 400},
        ]
        result = estimate_messages_tokens(messages, safety_margin=1.0)
        assert 50 <= result <= 400


class TestResolveContextLimit:
    def test_claude_sonnet(self):
        assert resolve_context_limit("anthropic/claude-sonnet-4-6") == 200_000

    def test_claude_opus_1m(self):
        assert resolve_context_limit("anthropic/claude-opus-4-5-1m") == 1_000_000

    def test_gpt_4o(self):
        assert resolve_context_limit("openai/gpt-4o") == 128_000

    def test_deepseek(self):
        assert resolve_context_limit("deepseek/deepseek-chat") == 128_000

    def test_unknown_model(self):
        assert resolve_context_limit("unknown/model") == 128_000


class TestTiktokenIntegration:
    def test_english_precise(self):
        # "hello world" = 2 tokens in cl100k_base
        result = estimate_tokens("hello world", safety_margin=1.0)
        assert result < 10

    def test_chinese_text(self):
        result = estimate_tokens("你好世界", safety_margin=1.0)
        assert result > 0

    def test_messages_uses_tiktoken(self):
        msgs = [{"content": "hello world"}]
        result = estimate_messages_tokens(msgs, safety_margin=1.0)
        assert result < 10
