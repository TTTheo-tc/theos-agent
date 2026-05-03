# tests/test_genver_review.py
"""Tests for the bounded gen↔ver review protocol."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.genver.artifact_store import ArtifactStore
from src.genver.models import Phase
from src.genver.review import PhaseReviewProtocol


def _make_verdict(status: str = "pass", summary: str = "ok") -> str:
    """Return LLM content containing a JSON verdict."""
    v = {
        "status": status,
        "issues": [],
        "files_modified": [],
        "summary": summary,
        "checks_performed": [],
    }
    return f"Review done.\n```json\n{json.dumps(v)}\n```"


def _mock_run_tool_loop(content: str):
    """Return a coroutine that mimics run_tool_loop's return value."""

    async def _run(**kwargs):
        return (
            content,
            [],
            kwargs["messages"],
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        )

    return _run


@pytest.mark.asyncio
class TestPhaseReviewProtocol:
    async def test_ver_passes_immediately(self, tmp_path):
        """Ver reviews and passes — no gen_review needed."""
        store = ArtifactStore(tmp_path / ".genver")
        store.write_artifact("spec.md", "# Spec\n\nOriginal")

        proto = PhaseReviewProtocol(
            phase=Phase.SPEC,
            artifact_name="spec.md",
            store=store,
            user_request="build API",
            workspace=tmp_path,
            gen_provider=MagicMock(),
            ver_provider=MagicMock(),
            gen_model="gen-model",
            ver_model="ver-model",
            max_iterations=20,
        )

        with patch(
            "src.genver.review.run_tool_loop",
            side_effect=_mock_run_tool_loop(_make_verdict("pass")),
        ):
            result = await proto.run()

        assert result.final_verdict is not None
        assert result.final_verdict.is_acceptable
        # Only 1 review record: ver_review
        assert len(result.review_records) == 1
        assert result.review_records[0].step == "ver_review"

    async def test_ver_needs_revision_triggers_gen_review(self, tmp_path):
        """Ver returns needs_revision → gen_review is triggered."""
        store = ArtifactStore(tmp_path / ".genver")
        store.write_artifact("spec.md", "# Spec\n\nOriginal")

        proto = PhaseReviewProtocol(
            phase=Phase.SPEC,
            artifact_name="spec.md",
            store=store,
            user_request="build API",
            workspace=tmp_path,
            gen_provider=MagicMock(),
            ver_provider=MagicMock(),
            gen_model="gen-model",
            ver_model="ver-model",
            max_iterations=20,
        )

        call_count = 0

        async def _side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # ver_review: needs_revision
                content = _make_verdict("needs_revision", "missing requirements")
            else:
                # gen_review: pass
                content = _make_verdict("pass", "ver changes accepted")
            return (
                content,
                [],
                kwargs["messages"],
                {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            )

        with patch("src.genver.review.run_tool_loop", side_effect=_side_effect):
            result = await proto.run()

        assert result.final_verdict.is_acceptable
        assert len(result.review_records) == 2
        assert result.review_records[0].step == "ver_review"
        assert result.review_records[1].step == "gen_review"

    async def test_max_3_review_steps(self, tmp_path):
        """Protocol never exceeds 3 review steps: ver_review → gen_review? → ver_final_review? (`gen_write` happens before protocol start)."""
        store = ArtifactStore(tmp_path / ".genver")
        store.write_artifact("spec.md", "# Spec")

        proto = PhaseReviewProtocol(
            phase=Phase.SPEC,
            artifact_name="spec.md",
            store=store,
            user_request="build API",
            workspace=tmp_path,
            gen_provider=MagicMock(),
            ver_provider=MagicMock(),
            gen_model="gen-model",
            ver_model="ver-model",
            max_iterations=20,
        )

        call_count = 0

        async def _always_needs_revision(**kwargs):
            nonlocal call_count
            call_count += 1
            content = _make_verdict("needs_revision", f"issue {call_count}")
            return (
                content,
                [],
                kwargs["messages"],
                {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            )

        with patch("src.genver.review.run_tool_loop", side_effect=_always_needs_revision):
            result = await proto.run()

        # Should be exactly 3 review steps: ver_review, gen_review, ver_final_review
        assert len(result.review_records) == 3
        assert result.review_records[2].step == "ver_final_review"
        # Final verdict is warning (advance with warning since ver_final still disagrees)
        assert result.final_verdict.status == "warning"

    async def test_bounded_review_exhausted_records_escalation_reason(self, tmp_path):
        """When all 3 review steps complete without agreement, escalation_reason is set."""
        store = ArtifactStore(tmp_path / ".genver")
        store.write_artifact("spec.md", "# Spec")

        proto = PhaseReviewProtocol(
            phase=Phase.SPEC,
            artifact_name="spec.md",
            store=store,
            user_request="build API",
            workspace=tmp_path,
            gen_provider=MagicMock(),
            ver_provider=MagicMock(),
            gen_model="gen-model",
            ver_model="ver-model",
            max_iterations=20,
        )

        call_count = 0

        async def _always_needs_revision(**kwargs):
            nonlocal call_count
            call_count += 1
            content = _make_verdict("needs_revision", f"issue {call_count}")
            return (
                content,
                [],
                kwargs["messages"],
                {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            )

        with patch("src.genver.review.run_tool_loop", side_effect=_always_needs_revision):
            result = await proto.run()

        # Should be exactly 3 review steps
        assert len(result.review_records) == 3
        # Final verdict forced to warning
        assert result.final_verdict.status == "warning"
        # Last record should have escalation_reason set
        last_record = result.review_records[-1]
        assert last_record.escalation_reason == "bounded_review_exhausted"
