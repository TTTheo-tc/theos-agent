from __future__ import annotations

from pathlib import Path

from src.agent.tools.fs_list import ListDirTool


async def test_list_dir_lists_sorted_entries_and_applies_ignore(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "b.pyc").write_text("", encoding="utf-8")
    (tmp_path / "a.txt").write_text("", encoding="utf-8")

    result = await ListDirTool(workspace=tmp_path).execute(path=".", ignore=["*.pyc"])

    lines = result.splitlines()
    assert lines[0].endswith("a.txt")
    assert lines[1].endswith("src")
    assert "b.pyc" not in result


async def test_list_dir_returns_empty_message(tmp_path: Path) -> None:
    result = await ListDirTool(workspace=tmp_path).execute(path=".")

    assert result == "Directory . is empty"


async def test_list_dir_reports_missing_and_non_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    tool = ListDirTool(workspace=tmp_path)

    assert await tool.execute(path="missing") == "Error: Directory not found: missing"
    assert await tool.execute(path="file.txt") == "Error: Not a directory: file.txt"


async def test_list_dir_enforces_allowed_dir(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()

    result = await ListDirTool(workspace=tmp_path, allowed_dir=allowed).execute(path=str(outside))

    assert "outside allowed directory" in result
