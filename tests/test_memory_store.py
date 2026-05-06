"""Tests for MemoryStore.write_long_term atomic writes."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

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


def test_remember_preserves_section_metadata_and_promotes_directive(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_long_term(
        """# Long-term Memory

## Remembered Directives
<!-- updated: 2020-01-01 -->
<!-- pinned -->
- older directive
- newest directive
"""
    )

    assert store.remember("newest directive")

    section = store.read_long_term().split("## Remembered Directives", 1)[1]
    assert "<!-- pinned -->" in section
    assert section.count("- newest directive") == 1
    assert section.index("- newest directive") < section.index("- older directive")


def test_merge_bullets_creates_sections_preserves_metadata_and_skips_duplicates(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path)
    store.write_long_term(
        """# Long-term Memory

## Decisions
<!-- updated: 2020-01-01 -->
<!-- pinned -->
- Use Redis for caching
"""
    )

    merged = store.merge_bullets(
        [
            ("Decisions", "Use PostgreSQL for persistence"),
            ("Decisions", "use redis for caching"),
            ("Policies", "Run tests before committing"),
        ]
    )

    text = store.read_long_term()
    assert merged == 2
    assert "<!-- pinned -->" in text
    assert text.lower().count("use redis for caching") == 1
    assert "Use PostgreSQL for persistence" in text
    assert "## Policies" in text


def test_merge_bullets_uses_exact_normalized_duplicate_matching(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_long_term(
        """# Long-term Memory

## Decisions
<!-- updated: 2020-01-01 -->
- Use Redis for caching backups
"""
    )

    merged = store.merge_bullets([("Decisions", "Use Redis for caching")])

    text = store.read_long_term()
    assert merged == 1
    assert "- Use Redis for caching backups" in text
    assert "- Use Redis for caching" in text


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


def test_gc_max_sections_keeps_recent_sections_not_document_order(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    today = datetime.now()
    recent = today.strftime("%Y-%m-%d")
    older = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    oldest = (today - timedelta(days=60)).strftime("%Y-%m-%d")
    store.write_long_term(
        f"""# Long-term Memory

## Recent At Top
<!-- updated: {recent} -->
- keep despite position

## Old Middle
<!-- updated: {oldest} -->
- drop

## Older At End
<!-- updated: {older} -->
- keep because it is newer than middle
"""
    )

    removed = store.gc(max_age_days=365, max_sections=3)
    text = store.read_long_term()

    assert removed == 1
    assert "## Recent At Top" in text
    assert "## Older At End" in text
    assert "## Old Middle" not in text


def test_gc_max_sections_preserves_pinned_sections(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    today = datetime.now().strftime("%Y-%m-%d")
    store.write_long_term(
        f"""# Long-term Memory

## Pinned Manual Note
<!-- pinned -->
- keep even without timestamp

## Recent One
<!-- updated: {today} -->
- keep

## Recent Two
<!-- updated: {today} -->
- drop because pinned has priority
"""
    )

    removed = store.gc(max_age_days=365, max_sections=3)
    text = store.read_long_term()

    assert removed == 1
    assert "## Pinned Manual Note" in text
    assert ("## Recent One" in text) ^ ("## Recent Two" in text)


def test_memory_index_reuses_memory_store_section_parsing() -> None:
    from src.memory.index import MemoryIndex

    sections = MemoryIndex._split_sections(
        """# Long-term Memory

## Decisions
<!-- updated: 2026-04-14 -->
- use postgres

## Notes
- untimestamped
"""
    )

    assert sections == [
        ("_preamble", "# Long-term Memory", ""),
        ("Decisions", "<!-- updated: 2026-04-14 -->\n- use postgres", "2026-04-14"),
        ("Notes", "- untimestamped", ""),
    ]


@pytest.mark.asyncio
async def test_memory_index_sync_search_and_get_section_roundtrip(tmp_path: Path) -> None:
    from src.memory.index import MemoryIndex
    from src.store.database import Database

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text(
        """# Long-term Memory

## Decisions
<!-- updated: 2026-04-14 -->
- use postgres for primary data
""",
        encoding="utf-8",
    )
    db = Database(tmp_path / "test.db")
    await db.connect()
    index = MemoryIndex(db)
    await index.ensure_table()

    try:
        assert await index.sync_memory(memory_dir / "MEMORY.md") == 2
        results = await index.search("postgres primary", max_results=5)
        section = await index.get_section("Decisions")
    finally:
        await db.close()

    assert any(result["section"] == "Decisions" for result in results)
    assert section is not None
    assert "postgres" in section
