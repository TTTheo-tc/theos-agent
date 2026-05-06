from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.tools.context import ToolContext
from src.agent.tools.fs_read import ReadFileTool
from src.agent.tools.fs_write import WriteFileTool


@pytest.fixture(autouse=True)
def clear_read_state():
    ReadFileTool.clear_read_state()
    yield
    ReadFileTool.clear_read_state()


async def test_read_file_supports_path_alias_offset_and_limit(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    tool = ReadFileTool(workspace=tmp_path)
    result = await tool.execute(path="notes.txt", offset=2, limit=2)

    assert result.splitlines() == ["     2\ttwo", "     3\tthree"]


async def test_read_file_dedupes_per_session_and_records_write_state(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("print('ok')\n", encoding="utf-8")
    context = ToolContext(session_key="session-a")

    tool = ReadFileTool(workspace=tmp_path)
    first = await tool.execute(file_path="app.py", _context=context)
    second = await tool.execute(file_path="app.py", _context=context)

    resolved = str(target.resolve())
    assert "print('ok')" in first
    assert "File unchanged since last read" in second
    assert WriteFileTool.has_read("session-a", resolved)
    assert not WriteFileTool.has_read("session-b", resolved)


async def test_large_file_requires_limit_but_can_stream_limited_range(tmp_path: Path) -> None:
    target = tmp_path / "large.txt"
    target.write_text("\n".join(f"line{i}" for i in range(1, 20)) + "\n", encoding="utf-8")

    tool = ReadFileTool(workspace=tmp_path, max_size_bytes=40)
    full = await tool.execute(file_path="large.txt")
    limited = await tool.execute(file_path="large.txt", limit=2)

    assert "exceeds the 40 byte limit" in full
    assert limited.splitlines() == ["     1\tline1", "     2\tline2"]


async def test_large_file_range_read_does_not_authorize_write(tmp_path: Path) -> None:
    target = tmp_path / "large.txt"
    target.write_text("\n".join(f"line{i}" for i in range(1, 20)) + "\n", encoding="utf-8")

    tool = ReadFileTool(workspace=tmp_path, max_size_bytes=20)
    result = await tool.execute(file_path="large.txt", limit=2)

    assert "line1" in result
    assert not WriteFileTool.has_read(None, str(target.resolve()))


async def test_large_file_range_read_enforces_output_byte_cap(tmp_path: Path) -> None:
    target = tmp_path / "one-line.txt"
    target.write_text("x" * 100 + "\n", encoding="utf-8")

    tool = ReadFileTool(workspace=tmp_path, max_size_bytes=20)
    result = await tool.execute(file_path="one-line.txt", limit=1)

    assert "     1\tx" in result
    assert "x" * 30 not in result
    assert "truncated at read byte limit" in result


async def test_large_file_range_read_caps_formatted_output_for_many_short_lines(
    tmp_path: Path,
) -> None:
    target = tmp_path / "many-lines.txt"
    target.write_text("\n".join("x" for _ in range(100)) + "\n", encoding="utf-8")

    tool = ReadFileTool(workspace=tmp_path, max_size_bytes=20)
    result = await tool.execute(file_path="many-lines.txt", limit=100)

    assert "     1\tx" in result
    assert "     10\tx" not in result
    assert "truncated at read byte limit" in result


async def test_read_file_hints_special_file_types_and_blocks_binary(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    image = tmp_path / "image.png"
    binary = tmp_path / "data.bin"
    pdf.write_bytes(b"%PDF-1.7")
    image.write_bytes(b"not-really-an-image")
    binary.write_bytes(b"abc\x00def")

    tool = ReadFileTool(workspace=tmp_path)

    assert "Use the `pdf` tool" in await tool.execute(file_path="doc.pdf")
    assert "image_analyze" in await tool.execute(file_path="image.png")
    assert "appears to be a binary file" in await tool.execute(file_path="data.bin")
