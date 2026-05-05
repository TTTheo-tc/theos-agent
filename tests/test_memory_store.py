"""Tests for MemoryStore.write_long_term atomic writes."""

from datetime import datetime, timedelta
from pathlib import Path

from src.memory.store import MemoryStore


def test_write_long_term_creates_file(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_long_term("# Hello")
    assert store.memory_file.exists()
    assert store.memory_file.read_text(encoding="utf-8") == "# Hello"


def test_write_long_term_replaces_content(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_long_term("# First")
    store.write_long_term("# Second")
    assert store.memory_file.read_text(encoding="utf-8") == "# Second"


def test_write_long_term_no_tmp_remains(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_long_term("# Content")
    tmp_files = list(store.memory_dir.glob("*.tmp"))
    assert tmp_files == []


def test_remember_creates_and_dedupes_directive_section(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)

    assert store.remember("以后涉及飞书权限时参考 scope.json")
    assert store.remember("以后涉及飞书权限时参考 scope.json")

    text = store.memory_file.read_text(encoding="utf-8")
    assert "## Remembered Directives" in text
    assert text.count("以后涉及飞书权限时参考 scope.json") == 1


def test_extract_section_age_days_ignores_invalid_timestamp() -> None:
    assert MemoryStore.extract_section_age_days("<!-- updated: 2026-99-99 -->") is None


def test_gc_uses_section_renderer_when_removing_old_sections(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    today = datetime.now().strftime("%Y-%m-%d")
    store.write_long_term(
        f"""# Long-term Memory

## Old
<!-- updated: 2000-01-01 -->
- remove me

## Recent
<!-- updated: {today} -->
- keep me
"""
    )

    removed = store.gc(max_age_days=90, max_sections=20)
    text = store.read_long_term()

    assert removed == 1
    assert "## Old" not in text
    assert "# Long-term Memory" in text
    assert "## Recent" in text
    assert "## _preamble" not in text


def test_gc_max_sections_keeps_preamble_and_most_recent_sections(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    today = datetime.now()
    section_dates = [
        (today - timedelta(days=idx)).strftime("%Y-%m-%d")
        for idx in range(4)
    ]
    store.write_long_term(
        f"""# Long-term Memory

## Section 0
<!-- updated: {section_dates[3]} -->
- drop by overflow

## Section 1
<!-- updated: {section_dates[2]} -->
- drop by overflow

## Section 2
<!-- updated: {section_dates[1]} -->
- keep

## Section 3
<!-- updated: {section_dates[0]} -->
- newest
"""
    )

    removed = store.gc(max_age_days=90, max_sections=3)
    text = store.read_long_term()

    assert removed == 2
    assert text.startswith("# Long-term Memory")
    assert "## Section 0" not in text
    assert "## Section 1" not in text
    assert "## Section 2" in text
    assert "## Section 3" in text
