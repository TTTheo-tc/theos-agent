"""Tests for MemoryRecallService.get_memory_context() behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config.schema import MemoryConfig, MemoryInjectionConfig
from src.memory.recall import MemoryRecallService
from src.memory.scope import MemoryScopeResolver
from src.memory.store import MemoryStore

SAMPLE_MEMORY = """\
# Long-term Memory

## User Preferences
<!-- updated: 2026-03-20 -->
- Prefers Python over JavaScript
- Uses vim keybindings

## Project Architecture
<!-- updated: 2026-03-19 -->
- TheOS uses asyncio message bus
- OpenAI SDK + Anthropic SDK wraps 20+ providers
"""


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path)


@pytest.fixture()
def store_with_memory(store: MemoryStore) -> MemoryStore:
    store.memory_file.write_text(SAMPLE_MEMORY, encoding="utf-8")
    return store


def _recall(workspace: Path, memory_config: MemoryConfig | None = None) -> MemoryRecallService:
    scope = MemoryScopeResolver(
        workspace=workspace,
        groups_base_dir=workspace / "groups",
        group_memory_enabled=False,
    )
    return MemoryRecallService(scope=scope, memory_config=memory_config)


def test_with_memory(store_with_memory: MemoryStore) -> None:
    result = _recall(store_with_memory.memory_dir.parent).get_memory_context()
    assert "Long-term Memory" in result
    assert "User Preferences" in result
    assert "Project Architecture" in result


def test_memory_disabled_skips_context_injection(store_with_memory: MemoryStore) -> None:
    result = _recall(store_with_memory.memory_dir.parent).get_memory_context(
        memory_config=MemoryConfig(enabled=False)
    )

    assert result == ""


def test_without_memory(store: MemoryStore) -> None:
    result = _recall(store.memory_dir.parent).get_memory_context()
    assert result == ""


def test_retrieval_mode_selects_relevant_sections(store_with_memory: MemoryStore) -> None:
    config = MemoryConfig(
        injection=MemoryInjectionConfig(
            mode="retrieval",
            max_context_tokens=1000,
            fallback_to_full=False,
        )
    )
    result = _recall(store_with_memory.memory_dir.parent).get_memory_context(
        query="asyncio provider", memory_config=config
    )
    assert "Project Architecture" in result
    assert "vim" not in result


def test_retrieval_mode_respects_budget(store: MemoryStore) -> None:
    store.memory_file.write_text(
        """# Long-term Memory

## Asyncio Architecture
- asyncio message bus provider pipeline event loop runtime dispatch

## Asyncio Notes
- asyncio retry queue provider adapters context builder storage
""",
        encoding="utf-8",
    )
    config = MemoryConfig(
        injection=MemoryInjectionConfig(
            mode="retrieval",
            max_context_tokens=8,
            fallback_to_full=False,
        )
    )

    result = _recall(store.memory_dir.parent).get_memory_context(
        query="asyncio provider", memory_config=config
    )

    assert result.startswith("## Long-term Memory (filtered)")
    assert "Asyncio Architecture" in result
    assert "Asyncio Notes" not in result


def test_retrieval_no_match_fallback(store_with_memory: MemoryStore) -> None:
    # fallback_to_full=True  -> returns full content
    config_fallback = MemoryConfig(
        injection=MemoryInjectionConfig(
            mode="retrieval",
            max_context_tokens=1000,
            fallback_to_full=True,
        )
    )
    result = _recall(store_with_memory.memory_dir.parent).get_memory_context(
        query="xyznonexistent", memory_config=config_fallback
    )
    assert "Long-term Memory" in result
    assert "User Preferences" in result

    # fallback_to_full=False -> returns empty string
    config_no_fallback = MemoryConfig(
        injection=MemoryInjectionConfig(
            mode="retrieval",
            max_context_tokens=1000,
            fallback_to_full=False,
        )
    )
    result = _recall(store_with_memory.memory_dir.parent).get_memory_context(
        query="xyznonexistent", memory_config=config_no_fallback
    )
    assert result == ""


def test_recall_service_allows_per_call_memory_config_override(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    memory_dir = workspace / "memory"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text(
        """# Long-term Memory

## Asyncio Architecture
- asyncio provider pipeline dispatch

## Vim Preferences
- prefers vim bindings
""",
        encoding="utf-8",
    )

    scope = MemoryScopeResolver(
        workspace=workspace,
        groups_base_dir=workspace / "groups",
        group_memory_enabled=False,
    )
    default_config = MemoryConfig(
        injection=MemoryInjectionConfig(
            mode="full",
            max_context_tokens=1000,
            fallback_to_full=True,
        )
    )
    recall = MemoryRecallService(scope=scope, memory_config=default_config)
    override_config = MemoryConfig(
        injection=MemoryInjectionConfig(
            mode="retrieval",
            max_context_tokens=1000,
            fallback_to_full=False,
        )
    )

    result = recall.get_memory_context(
        query="asyncio provider",
        memory_config=override_config,
    )

    assert "Asyncio Architecture" in result
    assert "vim bindings" not in result
