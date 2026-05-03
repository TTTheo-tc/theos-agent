"""Tests for notebook tool security policy gates."""

import json

from src.agent.tools.notebook import NotebookEditTool, NotebookReadTool


async def test_notebook_read_blocks_sensitive_path() -> None:
    tool = NotebookReadTool()
    result = await tool.execute("/etc/passwd")
    assert "security policy" in result.lower()


async def test_notebook_edit_requires_review_for_env_file(tmp_path) -> None:
    target = tmp_path / ".env.ipynb"
    target.write_text(
        json.dumps({"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}),
        encoding="utf-8",
    )
    tool = NotebookEditTool(workspace=tmp_path)
    result = await tool.execute(str(target), cell_number=0, new_source="print(1)")
    assert "requires human review" in result.lower()
