"""Tests for DocWriteFileTool path restrictions."""

import pytest

from src.agent.tools.fs_edit import EditFileTool
from src.agent.tools.fs_read import ReadFileTool
from src.agent.tools.fs_write import DocWriteFileTool, WriteFileTool


@pytest.fixture
def tmp_workspace(tmp_path):
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    return tmp_path


async def test_doc_write_allows_docs_md(tmp_workspace):
    tool = DocWriteFileTool(workspace=tmp_workspace)
    result = await tool.execute("docs/plans/test.md", "# hello")
    assert "Created" in result or "Successfully wrote" in result


async def test_doc_write_blocks_non_md(tmp_workspace):
    tool = DocWriteFileTool(workspace=tmp_workspace)
    result = await tool.execute("docs/plans/test.py", "code")
    assert "Error" in result
    assert ".md" in result


async def test_doc_write_blocks_outside_docs(tmp_workspace):
    tool = DocWriteFileTool(workspace=tmp_workspace)
    result = await tool.execute("src/agent/evil.md", "bad")
    assert "Error" in result
    assert "docs" in result.lower()


async def test_doc_write_tool_name():
    tool = DocWriteFileTool()
    assert tool.name == "write_docs"


async def test_doc_write_blocks_path_traversal(tmp_workspace):
    """docs/../src/evil.md must not bypass the docs restriction."""
    tool = DocWriteFileTool(workspace=tmp_workspace)
    result = await tool.execute("docs/../src/evil.md", "bad")
    assert "Error" in result
    assert "docs" in result.lower()


async def test_read_file_blocks_sensitive_system_path():
    tool = ReadFileTool()
    result = await tool.execute(file_path="/etc/passwd")
    assert "security policy" in result.lower()


async def test_write_file_requires_review_for_env_file(tmp_workspace):
    tool = WriteFileTool(workspace=tmp_workspace)
    result = await tool.execute(file_path=str(tmp_workspace / ".env"), content="SECRET=1")
    assert "requires human review" in result.lower()


async def test_edit_file_requires_review_for_env_file(tmp_workspace):
    target = tmp_workspace / ".env"
    target.write_text("SECRET=1\n", encoding="utf-8")
    tool = EditFileTool(workspace=tmp_workspace)
    result = await tool.execute(file_path=str(target), old_string="SECRET=1", new_string="SECRET=2")
    assert "requires human review" in result.lower()
