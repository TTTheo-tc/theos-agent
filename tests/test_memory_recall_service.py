"""Tests for MemoryRecallService retrieval policy."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.config.schema import MemoryConfig, MemoryInjectionConfig
from src.memory.recall import MemoryRecallService
from src.memory.scope import MemoryScopeResolver

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


def _make_recall(
    workspace: Path,
    *,
    mode: str = "full",
    max_context_tokens: int = 1000,
    fallback_to_full: bool = True,
) -> MemoryRecallService:
    scope = MemoryScopeResolver(
        workspace=workspace,
        groups_base_dir=workspace / "groups",
        group_memory_enabled=False,
    )
    config = MemoryConfig(
        injection=MemoryInjectionConfig(
            mode=mode,
            max_context_tokens=max_context_tokens,
            fallback_to_full=fallback_to_full,
        )
    )
    return MemoryRecallService(scope=scope, memory_config=config)


def _write_memory(workspace: Path, content: str) -> None:
    memory_dir = workspace / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "MEMORY.md").write_text(content, encoding="utf-8")


# ------------------------------------------------------------------
# Full mode
# ------------------------------------------------------------------


def test_full_mode(tmp_path: Path) -> None:
    """Full mode returns entire MEMORY.md content."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_memory(workspace, SAMPLE_MEMORY)

    recall = _make_recall(workspace, mode="full")
    result = recall.get_memory_context()

    assert "Long-term Memory" in result
    assert "User Preferences" in result
    assert "Project Architecture" in result
    assert "vim keybindings" in result
    assert "asyncio message bus" in result


# ------------------------------------------------------------------
# Retrieval mode
# ------------------------------------------------------------------


def test_retrieval_mode(tmp_path: Path) -> None:
    """Retrieval mode returns only sections relevant to the query."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_memory(workspace, SAMPLE_MEMORY)

    recall = _make_recall(workspace, mode="retrieval", fallback_to_full=False)
    result = recall.get_memory_context(query="asyncio provider")

    assert "Project Architecture" in result
    assert "vim" not in result


# ------------------------------------------------------------------
# Budget truncation
# ------------------------------------------------------------------


def test_budget_truncation(tmp_path: Path) -> None:
    """Retrieval mode respects max_context_tokens budget."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_memory(
        workspace,
        """# Long-term Memory

## Asyncio Architecture
- asyncio message bus provider pipeline event loop runtime dispatch

## Asyncio Notes
- asyncio retry queue provider adapters context builder storage
""",
    )

    recall = _make_recall(workspace, mode="retrieval", max_context_tokens=8, fallback_to_full=False)
    result = recall.get_memory_context(query="asyncio provider")

    assert result.startswith("## Long-term Memory (filtered)")
    assert "Asyncio Architecture" in result
    assert "Asyncio Notes" not in result


# ------------------------------------------------------------------
# Fallback behavior
# ------------------------------------------------------------------


def test_fallback_to_full(tmp_path: Path) -> None:
    """No match + fallback=True returns full content."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_memory(workspace, SAMPLE_MEMORY)

    recall = _make_recall(workspace, mode="retrieval", fallback_to_full=True)
    result = recall.get_memory_context(query="xyznonexistent")

    assert "Long-term Memory" in result
    assert "User Preferences" in result


def test_no_match_no_fallback(tmp_path: Path) -> None:
    """No match + fallback=False returns empty string."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_memory(workspace, SAMPLE_MEMORY)

    recall = _make_recall(workspace, mode="retrieval", fallback_to_full=False)
    result = recall.get_memory_context(query="xyznonexistent")

    assert result == ""


# ------------------------------------------------------------------
# Per-call config override
# ------------------------------------------------------------------


def test_per_call_config_override(tmp_path: Path) -> None:
    """Per-call memory_config overrides the default config."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_memory(
        workspace,
        """# Long-term Memory

## Asyncio Architecture
- asyncio provider pipeline dispatch

## Vim Preferences
- prefers vim bindings
""",
    )

    # Default is full mode
    recall = _make_recall(workspace, mode="full")

    # Override to retrieval mode at call site
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


# ------------------------------------------------------------------
# Empty memory
# ------------------------------------------------------------------


def test_empty_memory(tmp_path: Path) -> None:
    """Returns empty string when MEMORY.md does not exist."""
    workspace = tmp_path / "ws"
    workspace.mkdir()

    recall = _make_recall(workspace)
    result = recall.get_memory_context()

    assert result == ""


# ------------------------------------------------------------------
# Full mode when no query provided in retrieval mode
# ------------------------------------------------------------------


def test_retrieval_mode_no_query_returns_full(tmp_path: Path) -> None:
    """Retrieval mode without a query falls back to full injection."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _write_memory(workspace, SAMPLE_MEMORY)

    recall = _make_recall(workspace, mode="retrieval")
    result = recall.get_memory_context()  # no query

    assert "Long-term Memory" in result
    assert "User Preferences" in result
    assert "Project Architecture" in result


# ------------------------------------------------------------------
# Freshness warnings
# ------------------------------------------------------------------


class TestFreshnessWarnings:
    """Tests for freshness annotation on memory sections."""

    def test_recent_section_no_warning(self, tmp_path: Path) -> None:
        """Section updated today should not have a freshness warning."""
        today = datetime.now().strftime("%Y-%m-%d")
        content = f"""\
# Long-term Memory

## Recent Notes
<!-- updated: {today} -->
- Something fresh
"""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        _write_memory(workspace, content)

        recall = _make_recall(workspace, mode="full")
        result = recall.get_memory_context()

        assert "Recent Notes" in result
        assert "\u26a0" not in result
        assert "verify" not in result.lower()

    def test_old_section_gets_warning(self, tmp_path: Path) -> None:
        """Section from 2025-01-15 should have a freshness warning."""
        content = """\
# Long-term Memory

## Old Facts
<!-- updated: 2025-01-15 -->
- Something stale
"""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        _write_memory(workspace, content)

        recall = _make_recall(workspace, mode="full")
        result = recall.get_memory_context()

        assert "Old Facts" in result
        assert "verify" in result.lower()

    def test_section_without_timestamp_gets_warning(self, tmp_path: Path) -> None:
        """Section without an updated timestamp should get a warning."""
        content = """\
# Long-term Memory

## Orphan Section
- Some facts without a date
"""
        workspace = tmp_path / "ws"
        workspace.mkdir()
        _write_memory(workspace, content)

        recall = _make_recall(workspace, mode="full")
        result = recall.get_memory_context()

        assert "Orphan Section" in result
        # The warning blockquote should contain "verify" or "No timestamp"
        assert "> \u26a0" in result
