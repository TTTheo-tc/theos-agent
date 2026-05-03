"""Tests for ApplyPatchTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.tools.apply_patch import (
    AddHunk,
    ApplyPatchTool,
    DeleteHunk,
    PatchApplyError,
    PatchParseError,
    UpdateChunk,
    UpdateHunk,
    apply_update_hunk,
    parse_patch,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool(tmp_path: Path) -> ApplyPatchTool:
    return ApplyPatchTool(workspace=tmp_path, allowed_dir=tmp_path)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParsePatch:
    def test_add_file(self):
        patch = (
            "*** Begin Patch\n"
            "*** Add File: hello.py\n"
            "+print('hello')\n"
            "+print('world')\n"
            "*** End Patch"
        )
        hunks = parse_patch(patch)
        assert len(hunks) == 1
        h = hunks[0]
        assert isinstance(h, AddHunk)
        assert h.path == "hello.py"
        assert h.contents == "print('hello')\nprint('world')\n"

    def test_delete_file(self):
        patch = "*** Begin Patch\n" "*** Delete File: old.py\n" "*** End Patch"
        hunks = parse_patch(patch)
        assert len(hunks) == 1
        assert isinstance(hunks[0], DeleteHunk)
        assert hunks[0].path == "old.py"

    def test_update_file_simple(self):
        patch = (
            "*** Begin Patch\n"
            "*** Update File: main.py\n"
            "@@ def hello\n"
            " def hello():\n"
            "-    print('old')\n"
            "+    print('new')\n"
            "*** End Patch"
        )
        hunks = parse_patch(patch)
        assert len(hunks) == 1
        h = hunks[0]
        assert isinstance(h, UpdateHunk)
        assert h.path == "main.py"
        assert len(h.chunks) == 1
        chunk = h.chunks[0]
        assert chunk.context == "def hello"
        assert chunk.old_lines == ["def hello():", "    print('old')"]
        assert chunk.new_lines == ["def hello():", "    print('new')"]

    def test_update_with_move(self):
        patch = (
            "*** Begin Patch\n"
            "*** Update File: old_name.py\n"
            "*** Move to: new_name.py\n"
            " line1\n"
            "-old\n"
            "+new\n"
            "*** End Patch"
        )
        hunks = parse_patch(patch)
        h = hunks[0]
        assert isinstance(h, UpdateHunk)
        assert h.move_path == "new_name.py"

    def test_multiple_hunks(self):
        patch = (
            "*** Begin Patch\n"
            "*** Add File: a.py\n"
            "+# a\n"
            "*** Delete File: b.py\n"
            "*** Update File: c.py\n"
            " x\n"
            "-y\n"
            "+z\n"
            "*** End Patch"
        )
        hunks = parse_patch(patch)
        assert len(hunks) == 3
        assert isinstance(hunks[0], AddHunk)
        assert isinstance(hunks[1], DeleteHunk)
        assert isinstance(hunks[2], UpdateHunk)

    def test_empty_context_marker(self):
        patch = (
            "*** Begin Patch\n" "*** Update File: f.py\n" "@@\n" "-old\n" "+new\n" "*** End Patch"
        )
        hunks = parse_patch(patch)
        h = hunks[0]
        assert isinstance(h, UpdateHunk)
        assert h.chunks[0].context is None  # @@ alone = no context text

    def test_eof_marker(self):
        patch = (
            "*** Begin Patch\n"
            "*** Update File: f.py\n"
            "-last_line\n"
            "+new_last_line\n"
            "*** End of File\n"
            "*** End Patch"
        )
        hunks = parse_patch(patch)
        h = hunks[0]
        assert isinstance(h, UpdateHunk)
        assert h.chunks[0].is_eof is True

    def test_heredoc_wrapper(self):
        patch = (
            "<<EOF\n" "*** Begin Patch\n" "*** Add File: x.py\n" "+hello\n" "*** End Patch\n" "EOF"
        )
        hunks = parse_patch(patch)
        assert len(hunks) == 1
        assert isinstance(hunks[0], AddHunk)

    def test_error_no_begin(self):
        with pytest.raises(PatchParseError, match="Begin Patch"):
            parse_patch("*** Add File: x.py\n+hello\n*** End Patch")

    def test_error_no_end(self):
        with pytest.raises(PatchParseError, match="End Patch"):
            parse_patch("*** Begin Patch\n*** Add File: x.py\n+hello")

    def test_error_empty(self):
        with pytest.raises(PatchParseError, match="empty"):
            parse_patch("")

    def test_error_no_hunks(self):
        with pytest.raises(PatchParseError, match="No file operations"):
            parse_patch("*** Begin Patch\n*** End Patch")

    def test_error_bad_header(self):
        with pytest.raises(PatchParseError, match="Unexpected hunk header"):
            parse_patch("*** Begin Patch\ngarbage line\n*** End Patch")

    def test_multiple_update_chunks(self):
        patch = (
            "*** Begin Patch\n"
            "*** Update File: f.py\n"
            "@@ def foo\n"
            " def foo():\n"
            "-    old1\n"
            "+    new1\n"
            "@@ def bar\n"
            " def bar():\n"
            "-    old2\n"
            "+    new2\n"
            "*** End Patch"
        )
        hunks = parse_patch(patch)
        h = hunks[0]
        assert isinstance(h, UpdateHunk)
        assert len(h.chunks) == 2
        assert h.chunks[0].context == "def foo"
        assert h.chunks[1].context == "def bar"

    def test_add_file_empty_content(self):
        """Add file with no + lines creates empty file."""
        patch = "*** Begin Patch\n" "*** Add File: empty.py\n" "*** End Patch"
        hunks = parse_patch(patch)
        assert isinstance(hunks[0], AddHunk)
        assert hunks[0].contents == ""


# ---------------------------------------------------------------------------
# apply_update_hunk tests
# ---------------------------------------------------------------------------


class TestApplyUpdateHunk:
    def test_simple_replace(self):
        content = "line1\nline2\nline3\n"
        chunks = [UpdateChunk(old_lines=["line2"], new_lines=["LINE2"])]
        result = apply_update_hunk(content, chunks)
        assert result == "line1\nLINE2\nline3\n"

    def test_insert_lines(self):
        content = "a\nb\nc\n"
        chunks = [UpdateChunk(old_lines=["b"], new_lines=["b", "b2"])]
        result = apply_update_hunk(content, chunks)
        assert result == "a\nb\nb2\nc\n"

    def test_delete_lines(self):
        content = "a\nb\nc\nd\n"
        chunks = [UpdateChunk(old_lines=["b", "c"], new_lines=[])]
        result = apply_update_hunk(content, chunks)
        assert result == "a\nd\n"

    def test_context_anchor(self):
        content = "def foo():\n    pass\n\ndef bar():\n    pass\n"
        chunks = [
            UpdateChunk(
                context="def bar",
                old_lines=["    pass"],
                new_lines=["    return 42"],
            )
        ]
        result = apply_update_hunk(content, chunks)
        assert "def foo():\n    pass" in result
        assert "def bar():\n    return 42" in result

    def test_eof_anchor(self):
        content = "first\nmiddle\nlast\n"
        chunks = [
            UpdateChunk(
                old_lines=["last"],
                new_lines=["new_last"],
                is_eof=True,
            )
        ]
        result = apply_update_hunk(content, chunks)
        assert result == "first\nmiddle\nnew_last\n"

    def test_multiple_chunks(self):
        content = "a\nb\nc\nd\n"
        chunks = [
            UpdateChunk(old_lines=["a"], new_lines=["A"]),
            UpdateChunk(old_lines=["d"], new_lines=["D"]),
        ]
        result = apply_update_hunk(content, chunks)
        assert result == "A\nb\nc\nD\n"

    def test_fuzzy_whitespace_match(self):
        content = "  hello  \nworld\n"
        chunks = [UpdateChunk(old_lines=["hello"], new_lines=["HELLO"])]
        # Should match via trim fallback
        result = apply_update_hunk(content, chunks)
        assert "HELLO" in result

    def test_not_found_raises(self):
        content = "a\nb\nc\n"
        chunks = [UpdateChunk(old_lines=["xyz"], new_lines=["new"])]
        with pytest.raises(PatchApplyError, match="Failed to find"):
            apply_update_hunk(content, chunks)

    def test_trailing_newline_ensured(self):
        content = "a\nb"
        chunks = [UpdateChunk(old_lines=["b"], new_lines=["B"])]
        result = apply_update_hunk(content, chunks)
        assert result.endswith("\n")

    def test_pure_insertion(self):
        content = "a\nb\n"
        chunks = [UpdateChunk(old_lines=[], new_lines=["inserted"])]
        result = apply_update_hunk(content, chunks)
        assert "inserted" in result


# ---------------------------------------------------------------------------
# Tool: schema
# ---------------------------------------------------------------------------


class TestApplyPatchToolSchema:
    def test_name(self):
        tool = ApplyPatchTool()
        assert tool.name == "apply_patch"

    def test_risk_level(self):
        tool = ApplyPatchTool()
        assert tool.risk_level == "medium"

    def test_parameters(self):
        tool = ApplyPatchTool()
        params = tool.parameters
        assert "patch" in params["properties"]
        assert params["required"] == ["patch"]

    def test_to_schema(self):
        tool = ApplyPatchTool()
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "apply_patch"


# ---------------------------------------------------------------------------
# Tool: execute — add file
# ---------------------------------------------------------------------------


class TestToolAddFile:
    @pytest.mark.asyncio
    async def test_add_single_file(self, tmp_path: Path):
        tool = _tool(tmp_path)
        patch = "*** Begin Patch\n" "*** Add File: hello.py\n" "+print('hello')\n" "*** End Patch"
        result = await tool.execute(patch=patch)
        assert "Patch applied successfully" in result
        assert "A " in result
        assert (tmp_path / "hello.py").read_text() == "print('hello')\n"

    @pytest.mark.asyncio
    async def test_add_nested_file(self, tmp_path: Path):
        tool = _tool(tmp_path)
        patch = "*** Begin Patch\n" "*** Add File: sub/dir/new.py\n" "+# new\n" "*** End Patch"
        result = await tool.execute(patch=patch)
        assert "Patch applied successfully" in result
        assert (tmp_path / "sub" / "dir" / "new.py").exists()

    @pytest.mark.asyncio
    async def test_add_existing_file_error(self, tmp_path: Path):
        _write(tmp_path, "exists.py", "old")
        tool = _tool(tmp_path)
        patch = "*** Begin Patch\n" "*** Add File: exists.py\n" "+new\n" "*** End Patch"
        result = await tool.execute(patch=patch)
        assert "rolled back" in result
        assert "already exists" in result
        # Original file unchanged
        assert (tmp_path / "exists.py").read_text() == "old"


# ---------------------------------------------------------------------------
# Tool: execute — delete file
# ---------------------------------------------------------------------------


class TestToolDeleteFile:
    @pytest.mark.asyncio
    async def test_delete_file(self, tmp_path: Path):
        _write(tmp_path, "to_delete.py", "bye")
        tool = _tool(tmp_path)
        patch = "*** Begin Patch\n" "*** Delete File: to_delete.py\n" "*** End Patch"
        result = await tool.execute(patch=patch)
        assert "Patch applied successfully" in result
        assert "D " in result
        assert not (tmp_path / "to_delete.py").exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_error(self, tmp_path: Path):
        tool = _tool(tmp_path)
        patch = "*** Begin Patch\n" "*** Delete File: ghost.py\n" "*** End Patch"
        result = await tool.execute(patch=patch)
        assert "rolled back" in result
        assert "not found" in result


# ---------------------------------------------------------------------------
# Tool: execute — update file
# ---------------------------------------------------------------------------


class TestToolUpdateFile:
    @pytest.mark.asyncio
    async def test_simple_update(self, tmp_path: Path):
        _write(tmp_path, "main.py", "def hello():\n    print('old')\n")
        tool = _tool(tmp_path)
        patch = (
            "*** Begin Patch\n"
            "*** Update File: main.py\n"
            " def hello():\n"
            "-    print('old')\n"
            "+    print('new')\n"
            "*** End Patch"
        )
        result = await tool.execute(patch=patch)
        assert "Patch applied successfully" in result
        assert "M " in result
        content = (tmp_path / "main.py").read_text()
        assert "print('new')" in content
        assert "print('old')" not in content

    @pytest.mark.asyncio
    async def test_update_nonexistent_error(self, tmp_path: Path):
        tool = _tool(tmp_path)
        patch = (
            "*** Begin Patch\n" "*** Update File: missing.py\n" "-old\n" "+new\n" "*** End Patch"
        )
        result = await tool.execute(patch=patch)
        assert "rolled back" in result

    @pytest.mark.asyncio
    async def test_update_with_context(self, tmp_path: Path):
        _write(
            tmp_path,
            "app.py",
            "def foo():\n    pass\n\ndef bar():\n    return 1\n",
        )
        tool = _tool(tmp_path)
        # @@ context anchors the search; diff lines follow AFTER the context line
        patch = (
            "*** Begin Patch\n"
            "*** Update File: app.py\n"
            "@@ def bar\n"
            "-    return 1\n"
            "+    return 42\n"
            "*** End Patch"
        )
        result = await tool.execute(patch=patch)
        assert "Patch applied successfully" in result
        content = (tmp_path / "app.py").read_text()
        assert "return 42" in content
        assert "def foo():\n    pass" in content


# ---------------------------------------------------------------------------
# Tool: execute — move (rename) file
# ---------------------------------------------------------------------------


class TestToolMoveFile:
    @pytest.mark.asyncio
    async def test_move_file(self, tmp_path: Path):
        _write(tmp_path, "old.py", "content\n")
        tool = _tool(tmp_path)
        patch = (
            "*** Begin Patch\n"
            "*** Update File: old.py\n"
            "*** Move to: new.py\n"
            " content\n"
            "*** End Patch"
        )
        result = await tool.execute(patch=patch)
        assert "Patch applied successfully" in result
        assert not (tmp_path / "old.py").exists()
        assert (tmp_path / "new.py").exists()
        assert (tmp_path / "new.py").read_text().strip() == "content"

    @pytest.mark.asyncio
    async def test_move_with_edit(self, tmp_path: Path):
        _write(tmp_path, "src.py", "old_line\n")
        tool = _tool(tmp_path)
        patch = (
            "*** Begin Patch\n"
            "*** Update File: src.py\n"
            "*** Move to: dst.py\n"
            "-old_line\n"
            "+new_line\n"
            "*** End Patch"
        )
        result = await tool.execute(patch=patch)
        assert "Patch applied successfully" in result
        assert not (tmp_path / "src.py").exists()
        content = (tmp_path / "dst.py").read_text()
        assert "new_line" in content


# ---------------------------------------------------------------------------
# Tool: execute — atomicity / rollback
# ---------------------------------------------------------------------------


class TestToolAtomicity:
    @pytest.mark.asyncio
    async def test_rollback_on_second_hunk_failure(self, tmp_path: Path):
        """If the second operation fails, the first should be rolled back."""
        tool = _tool(tmp_path)
        patch = (
            "*** Begin Patch\n"
            "*** Add File: new_file.py\n"
            "+created\n"
            "*** Delete File: nonexistent.py\n"
            "*** End Patch"
        )
        result = await tool.execute(patch=patch)
        assert "rolled back" in result
        # The added file should have been rolled back
        assert not (tmp_path / "new_file.py").exists()

    @pytest.mark.asyncio
    async def test_rollback_preserves_original(self, tmp_path: Path):
        """Modified files should be restored on rollback."""
        _write(tmp_path, "existing.py", "original content\n")
        tool = _tool(tmp_path)
        patch = (
            "*** Begin Patch\n"
            "*** Update File: existing.py\n"
            "-original content\n"
            "+modified content\n"
            "*** Delete File: ghost.py\n"
            "*** End Patch"
        )
        result = await tool.execute(patch=patch)
        assert "rolled back" in result
        # Original file should be restored
        assert (tmp_path / "existing.py").read_text() == "original content\n"

    @pytest.mark.asyncio
    async def test_rollback_delete_then_fail(self, tmp_path: Path):
        """Deleted file should be restored on rollback."""
        _write(tmp_path, "victim.py", "precious\n")
        tool = _tool(tmp_path)
        patch = (
            "*** Begin Patch\n"
            "*** Delete File: victim.py\n"
            "*** Update File: nonexistent.py\n"
            "-x\n"
            "+y\n"
            "*** End Patch"
        )
        result = await tool.execute(patch=patch)
        assert "rolled back" in result
        assert (tmp_path / "victim.py").exists()
        assert (tmp_path / "victim.py").read_text() == "precious\n"


# ---------------------------------------------------------------------------
# Tool: execute — multi-file patch
# ---------------------------------------------------------------------------


class TestToolMultiFile:
    @pytest.mark.asyncio
    async def test_multi_file_patch(self, tmp_path: Path):
        _write(tmp_path, "a.py", "aaa\n")
        _write(tmp_path, "b.py", "bbb\n")
        tool = _tool(tmp_path)
        patch = (
            "*** Begin Patch\n"
            "*** Update File: a.py\n"
            "-aaa\n"
            "+AAA\n"
            "*** Update File: b.py\n"
            "-bbb\n"
            "+BBB\n"
            "*** Add File: c.py\n"
            "+CCC\n"
            "*** End Patch"
        )
        result = await tool.execute(patch=patch)
        assert "Patch applied successfully" in result
        assert (tmp_path / "a.py").read_text().strip() == "AAA"
        assert (tmp_path / "b.py").read_text().strip() == "BBB"
        assert (tmp_path / "c.py").read_text().strip() == "CCC"


# ---------------------------------------------------------------------------
# Tool: execute — error cases
# ---------------------------------------------------------------------------


class TestToolErrors:
    @pytest.mark.asyncio
    async def test_empty_patch(self, tmp_path: Path):
        tool = _tool(tmp_path)
        result = await tool.execute(patch="")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_malformed_patch(self, tmp_path: Path):
        tool = _tool(tmp_path)
        result = await tool.execute(patch="not a patch at all")
        assert "Error parsing patch" in result

    @pytest.mark.asyncio
    async def test_update_mismatch(self, tmp_path: Path):
        _write(tmp_path, "f.py", "actual content\n")
        tool = _tool(tmp_path)
        patch = (
            "*** Begin Patch\n"
            "*** Update File: f.py\n"
            "-completely wrong content\n"
            "+new content\n"
            "*** End Patch"
        )
        result = await tool.execute(patch=patch)
        assert "rolled back" in result
        # File unchanged
        assert (tmp_path / "f.py").read_text() == "actual content\n"


# ---------------------------------------------------------------------------
# Tool: security — path restriction
# ---------------------------------------------------------------------------


class TestToolSecurity:
    @pytest.mark.asyncio
    async def test_path_outside_allowed_dir(self, tmp_path: Path):
        tool = _tool(tmp_path)
        patch = "*** Begin Patch\n" "*** Add File: /etc/evil.py\n" "+bad\n" "*** End Patch"
        result = await tool.execute(patch=patch)
        assert "rolled back" in result or "Error" in result
        assert not Path("/etc/evil.py").exists()

    @pytest.mark.asyncio
    async def test_path_traversal(self, tmp_path: Path):
        tool = _tool(tmp_path)
        patch = "*** Begin Patch\n" "*** Add File: ../../escape.py\n" "+bad\n" "*** End Patch"
        result = await tool.execute(patch=patch)
        assert "rolled back" in result or "Error" in result
