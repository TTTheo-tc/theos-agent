"""Tests for DreamRunner with real LLM execution (mocked provider)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.dream.runner import DreamRunner


def _mock_provider():
    """Provider that returns a single response with no tool calls."""
    response = MagicMock()
    response.content = (
        "### Findings\n- Found interesting patterns\n\n"
        "### Insights\n- [Dream hypothesis] The architecture uses event sourcing\n\n"
        "### Artifacts\n- None"
    )
    response.has_tool_calls = False
    response.tool_calls = []
    response.finish_reason = "stop"
    response.usage = {"prompt_tokens": 500, "completion_tokens": 200, "total_tokens": 700}
    response.error_type = None
    response.reasoning_content = None

    provider = AsyncMock()
    provider.chat = AsyncMock(return_value=response)
    provider.supports_streaming = False
    return provider


def _mock_base_registry():
    base = MagicMock()
    base.get.return_value = None
    base.execute = AsyncMock(return_value="ok")
    return base


class TestDreamRunnerInit:
    def test_initialization(self, tmp_path):
        runner = DreamRunner(
            workspace=tmp_path,
            topic="test topic",
            provider=_mock_provider(),
            base_registry=_mock_base_registry(),
        )
        assert runner.topic == "test topic"
        assert runner.session_id.startswith("dream-")
        assert runner.budget_usd == 30.0

    def test_custom_params(self, tmp_path):
        runner = DreamRunner(
            workspace=tmp_path,
            topic="custom",
            provider=_mock_provider(),
            base_registry=_mock_base_registry(),
            model="test-model",
            budget_usd=5.0,
            max_iterations=10,
        )
        assert runner.budget_usd == 5.0
        assert runner.model == "test-model"
        assert runner.max_iterations == 10


class TestDreamRunnerRun:
    @pytest.mark.asyncio
    async def test_run_creates_output_dirs(self, tmp_path):
        runner = DreamRunner(
            workspace=tmp_path,
            topic="test",
            provider=_mock_provider(),
            base_registry=_mock_base_registry(),
        )
        result = await runner.run()
        assert result.output_dir.exists()
        assert (result.output_dir / "sandbox").exists()

    @pytest.mark.asyncio
    async def test_run_writes_eval(self, tmp_path):
        runner = DreamRunner(
            workspace=tmp_path,
            topic="test",
            provider=_mock_provider(),
            base_registry=_mock_base_registry(),
        )
        result = await runner.run()
        eval_path = result.output_dir / "dream_eval.json"
        assert eval_path.exists()
        data = json.loads(eval_path.read_text())
        assert data["status"] == "completed"
        assert data["topic"] == "test"

    @pytest.mark.asyncio
    async def test_run_writes_narrative(self, tmp_path):
        runner = DreamRunner(
            workspace=tmp_path,
            topic="test",
            provider=_mock_provider(),
            base_registry=_mock_base_registry(),
        )
        result = await runner.run()
        assert result.narrative_path is not None
        assert result.narrative_path.exists()

    @pytest.mark.asyncio
    async def test_run_writes_review(self, tmp_path):
        runner = DreamRunner(
            workspace=tmp_path,
            topic="test",
            provider=_mock_provider(),
            base_registry=_mock_base_registry(),
        )
        result = await runner.run()
        assert result.review_path is not None
        assert result.review_path.exists()

    @pytest.mark.asyncio
    async def test_run_writes_dream_index(self, tmp_path):
        runner = DreamRunner(
            workspace=tmp_path,
            topic="test topic",
            provider=_mock_provider(),
            base_registry=_mock_base_registry(),
        )
        await runner.run()
        index_path = tmp_path / "memory" / "instinct" / "DREAM_INDEX.jsonl"
        assert index_path.exists()
        entry = json.loads(index_path.read_text().strip())
        assert entry["topic"] == "test topic"
        assert entry["status"] == "completed"
        assert entry["reflux_level"] == "L1"

    @pytest.mark.asyncio
    async def test_run_extracts_insights_from_response(self, tmp_path):
        runner = DreamRunner(
            workspace=tmp_path,
            topic="test",
            provider=_mock_provider(),
            base_registry=_mock_base_registry(),
        )
        result = await runner.run()
        narrative = result.narrative_path.read_text()
        assert "[Dream hypothesis]" in narrative

    @pytest.mark.asyncio
    async def test_run_publishes_diary(self, tmp_path):
        runner = DreamRunner(
            workspace=tmp_path,
            topic="diary test",
            provider=_mock_provider(),
            base_registry=_mock_base_registry(),
        )
        await runner.run()
        dreams_md = tmp_path / "DREAMS.md"
        assert dreams_md.exists()
        assert "diary test" in dreams_md.read_text()

    @pytest.mark.asyncio
    async def test_run_includes_llm_cost_in_eval(self, tmp_path):
        runner = DreamRunner(
            workspace=tmp_path,
            topic="test",
            provider=_mock_provider(),
            base_registry=_mock_base_registry(),
        )
        result = await runner.run()
        # 500 prompt + 200 completion tokens → non-zero LLM cost
        assert result.eval.budget_usd_used > 0
        assert result.eval.narrative_tokens == 200

    @pytest.mark.asyncio
    async def test_gather_seeds_from_events(self, tmp_path):
        events_dir = tmp_path / "memory" / "instinct" / "events"
        events_dir.mkdir(parents=True)
        event = {"request": {"intent_summary": "analyze the codebase"}}
        (events_dir / "e1.json").write_text(json.dumps(event))

        runner = DreamRunner(
            workspace=tmp_path,
            topic="test",
            provider=_mock_provider(),
            base_registry=_mock_base_registry(),
        )
        seeds = await runner._gather_seeds()
        assert "analyze the codebase" in seeds


class TestParseResponse:
    def test_extracts_findings_and_insights(self):
        content = (
            "Some preamble\n"
            "### Findings\n"
            "- Found A\n"
            "- Found B\n"
            "### Insights\n"
            "- [Dream hypothesis] Insight 1\n"
            "- [Unverified exploration] Insight 2\n"
            "### Artifacts\n"
            "- file.py\n"
        )
        findings, insights = DreamRunner._parse_response(content)
        assert findings == ["Found A", "Found B"]
        assert len(insights) == 2
        assert "[Dream hypothesis]" in insights[0]

    def test_empty_content(self):
        findings, insights = DreamRunner._parse_response("")
        assert findings == []
        assert insights == []
