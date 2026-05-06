from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.tools.fs_edit import EditFileTool, MultiEditTool
from src.agent.tools.fs_read import ReadFileTool
from src.agent.tools.fs_write import WriteFileTool


@pytest.fixture(autouse=True)
def clear_read_state():
    ReadFileTool.clear_read_state()
    yield
    ReadFileTool.clear_read_state()


async def test_edit_file_supports_legacy_aliases_and_records_read_state(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("old\n", encoding="utf-8")

    result = await EditFileTool(workspace=tmp_path).execute(
        path="app.py",
        old_text="old",
        new_text="new",
    )

    assert result == f"Successfully edited {target.resolve()} (1 replacement)"
    assert target.read_text(encoding="utf-8") == "new\n"
    assert WriteFileTool.has_read(None, str(target.resolve()))


async def test_edit_file_warns_on_ambiguous_match_without_writing(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("name\nname\n", encoding="utf-8")

    result = await EditFileTool(workspace=tmp_path).execute(
        file_path="app.py",
        old_string="name",
        new_string="value",
    )

    assert "appears 2 times" in result
    assert target.read_text(encoding="utf-8") == "name\nname\n"


async def test_edit_file_replace_all_reports_replacement_count(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("name\nname\n", encoding="utf-8")

    result = await EditFileTool(workspace=tmp_path).execute(
        file_path="app.py",
        old_string="name",
        new_string="value",
        replace_all=True,
    )

    assert result == f"Successfully edited {target.resolve()} (2 replacements)"
    assert target.read_text(encoding="utf-8") == "value\nvalue\n"


async def test_edit_file_blocks_stale_read(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("old\n", encoding="utf-8")
    WriteFileTool.record_read(None, str(target.resolve()), target.stat().st_mtime - 10)

    result = await EditFileTool(workspace=tmp_path).execute(
        file_path="app.py",
        old_string="old",
        new_string="new",
    )

    assert "modified since you last read it" in result
    assert target.read_text(encoding="utf-8") == "old\n"


async def test_multi_edit_applies_edits_atomically_and_records_read_state(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    result = await MultiEditTool(workspace=tmp_path).execute(
        file_path="app.py",
        edits=[
            {"old_string": "alpha", "new_string": "one"},
            {"old_text": "beta", "new_text": "two"},
        ],
    )

    assert result == f"Successfully applied 2 edit(s) to {target.resolve()}"
    assert target.read_text(encoding="utf-8") == "one\ntwo\n"
    assert WriteFileTool.has_read(None, str(target.resolve()))


async def test_multi_edit_failure_leaves_file_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    result = await MultiEditTool(workspace=tmp_path).execute(
        file_path="app.py",
        edits=[
            {"old_string": "alpha", "new_string": "one"},
            {"old_string": "missing", "new_string": "two"},
        ],
    )

    assert "edit[1] old_string not found" in result
    assert target.read_text(encoding="utf-8") == "alpha\nbeta\n"


async def test_multi_edit_blocks_stale_read(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("alpha\n", encoding="utf-8")
    WriteFileTool.record_read(None, str(target.resolve()), target.stat().st_mtime - 10)

    result = await MultiEditTool(workspace=tmp_path).execute(
        file_path="app.py",
        edits=[{"old_string": "alpha", "new_string": "one"}],
    )

    assert "modified since you last read it" in result
    assert target.read_text(encoding="utf-8") == "alpha\n"
