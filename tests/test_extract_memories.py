"""Tests for post-turn background memory extraction (9g)."""

from __future__ import annotations

import pytest

from src.memory.extract import extract_durable_facts, merge_extracted_facts

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeToolCall:
    def __init__(self, name: str, arguments: dict):
        self.name = name
        self.arguments = arguments


class _FakeResponse:
    def __init__(self, tool_calls: list | None = None, content: str | None = None):
        self.tool_calls = tool_calls or []
        self.content = content

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class _FakeProvider:
    """Fake LLM provider returning a canned response."""

    def __init__(self, response=None, error: Exception | None = None):
        self._response = response
        self._error = error

    async def chat(self, **kwargs):
        if self._error:
            raise self._error
        return self._response


# ---------------------------------------------------------------------------
# extract_durable_facts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extracts_facts_from_conversation():
    """Provider returns save_facts tool call → parsed into list[dict]."""
    facts_payload = [
        {"section": "Architecture Decisions", "content": "Use PostgreSQL for persistence"},
        {"section": "Config Policies", "content": "Max retry count set to 3"},
    ]
    response = _FakeResponse(tool_calls=[_FakeToolCall("save_facts", {"facts": facts_payload})])
    provider = _FakeProvider(response=response)

    result = await extract_durable_facts(
        messages=[
            {"role": "user", "content": "Let's use PostgreSQL"},
            {"role": "assistant", "content": "Good choice, I'll configure PostgreSQL."},
        ],
        provider=provider,
        model="test-model",
    )

    assert len(result) == 2
    assert result[0]["section"] == "Architecture Decisions"
    assert result[1]["content"] == "Max retry count set to 3"


@pytest.mark.asyncio
async def test_returns_empty_when_no_durable_facts():
    """Provider returns no tool call → empty list."""
    response = _FakeResponse(content="No durable facts found.")
    provider = _FakeProvider(response=response)

    result = await extract_durable_facts(
        messages=[
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
        ],
        provider=provider,
        model="test-model",
    )

    assert result == []


@pytest.mark.asyncio
async def test_handles_provider_failure_gracefully():
    """Provider raises exception → empty list, no crash."""
    provider = _FakeProvider(error=RuntimeError("API unavailable"))

    result = await extract_durable_facts(
        messages=[
            {"role": "user", "content": "important decision"},
            {"role": "assistant", "content": "noted"},
        ],
        provider=provider,
        model="test-model",
    )

    assert result == []


# ---------------------------------------------------------------------------
# merge_extracted_facts
# ---------------------------------------------------------------------------


def test_extracted_facts_merged_into_memory(tmp_path):
    """Facts merged into existing MEMORY.md with dedup and timestamps."""
    from src.memory.store import MemoryStore

    store = MemoryStore(tmp_path)
    # Pre-populate MEMORY.md with an existing section
    store.write_long_term(
        "# Long-term Memory\n\n"
        "## Architecture Decisions\n"
        "<!-- updated: 2025-01-01 -->\n"
        "- Use Redis for caching\n"
    )

    facts = [
        {"section": "Architecture Decisions", "content": "Use PostgreSQL for persistence"},
        # Duplicate (case-insensitive) — should be skipped
        {"section": "Architecture Decisions", "content": "use redis for caching"},
        # New section
        {"section": "Config Policies", "content": "Max retry count set to 3"},
    ]

    merged_count = merge_extracted_facts(store, facts)

    assert merged_count == 2  # 1 new fact in existing section + 1 new section

    content = store.read_long_term()
    assert "Use PostgreSQL for persistence" in content
    assert "Use Redis for caching" in content
    assert "Max retry count set to 3" in content
    # Only one occurrence of the Redis fact (dedup)
    assert content.lower().count("use redis for caching") == 1
    # New section created
    assert "## Config Policies" in content
