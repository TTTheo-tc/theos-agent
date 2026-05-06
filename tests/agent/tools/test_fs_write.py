from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.tools.fs_read import ReadFileTool
from src.agent.tools.fs_write import WriteFileTool


@pytest.fixture(autouse=True)
def clear_read_state():
    ReadFileTool.clear_read_state()
    yield
    ReadFileTool.clear_read_state()


async def test_write_file_creates_new_file_with_path_alias(tmp_path: Path) -> None:
    tool = WriteFileTool(workspace=tmp_path)

    result = await tool.execute(path="src/app.py", content="print('ok')\n")

    target = tmp_path / "src" / "app.py"
    assert target.read_text(encoding="utf-8") == "print('ok')\n"
    assert result == f"Created {target.resolve()} (12 bytes)"


async def test_write_file_requires_read_before_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("old\n", encoding="utf-8")

    result = await WriteFileTool(workspace=tmp_path).execute(file_path="app.py", content="new\n")

    assert "must read app.py before overwriting" in result
    assert target.read_text(encoding="utf-8") == "old\n"


async def test_write_file_after_read_returns_unified_diff(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("old\n", encoding="utf-8")
    await ReadFileTool(workspace=tmp_path).execute(file_path="app.py")

    result = await WriteFileTool(workspace=tmp_path).execute(file_path="app.py", content="new\n")

    assert "--- " in result
    assert "-old" in result
    assert "+new" in result
    assert target.read_text(encoding="utf-8") == "new\n"


async def test_write_file_blocks_stale_read(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("old\n", encoding="utf-8")
    resolved = str(target.resolve())
    WriteFileTool.record_read(None, resolved, target.stat().st_mtime - 10)

    result = await WriteFileTool(workspace=tmp_path).execute(file_path="app.py", content="new\n")

    assert "modified since you last read it" in result
    assert target.read_text(encoding="utf-8") == "old\n"
