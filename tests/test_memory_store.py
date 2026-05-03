"""Tests for MemoryStore.write_long_term atomic writes."""

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
