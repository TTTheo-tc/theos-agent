# tests/test_genver_pipeline.py
"""Integration tests for GenVerPipeline."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.config.schema import GenVerConfig
from src.genver.models import Phase


def _mock_run_tool_loop_factory(store, responses: dict[int, tuple[str, str | None]]):
    """Factory: returns a mock run_tool_loop that writes artifacts and returns content by call index."""
    call_idx = 0

    async def _run(**kwargs):
        nonlocal call_idx
        call_idx += 1
        content, artifact_write = responses.get(call_idx, ("ok", None))
        if artifact_write:
            name, text = artifact_write
            store.write_artifact(name, text)
        return (
            content,
            [],
            kwargs["messages"],
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        )

    return _run


@pytest.mark.asyncio
class TestGenVerPipeline:
    async def test_trivial_skips_spec_and_plan(self, tmp_path):
        from src.genver.pipeline import GenVerPipeline

        config = GenVerConfig(phases=["execute", "review", "report"])
        pipeline = GenVerPipeline(
            config=config,
            provider=MagicMock(),
            workspace=tmp_path,
            generator_tools=MagicMock(),
            default_model="test-model",
        )
        # Verify phase selection
        phases = pipeline._resolve_phases(config.phases)
        assert Phase.SPEC not in phases
        assert Phase.PLAN not in phases
        assert Phase.EXECUTE in phases

    async def test_preflight_classifies_complexity(self, tmp_path):
        from src.genver.pipeline import classify_complexity

        assert classify_complexity("fix typo in README") in ("trivial", "small")
        assert (
            classify_complexity(
                "redesign the entire authentication system with OAuth2, SAML, and LDAP support across 15 modules"
            )
            == "large"
        )

    async def test_phase_list_from_complexity(self, tmp_path):
        from src.genver.pipeline import phases_for_complexity

        assert Phase.SPEC not in phases_for_complexity("trivial")
        assert Phase.PLAN not in phases_for_complexity("trivial")
        assert Phase.SPEC in phases_for_complexity("large")
        assert Phase.PLAN in phases_for_complexity("large")


@pytest.mark.asyncio
async def test_review_abort_stops_pipeline(tmp_path):
    """If REVIEW returns abort, REPORT should not run."""
    from unittest.mock import AsyncMock, patch

    from src.genver.models import PhaseArtifact, ReviewVerdict
    from src.genver.pipeline import GenVerPipeline

    config = GenVerConfig(phases=["execute", "review", "report"])
    pipeline = GenVerPipeline(
        config=config,
        provider=MagicMock(),
        workspace=tmp_path,
        generator_tools=MagicMock(),
        default_model="test-model",
    )

    # Mock run_execute to return a basic result
    mock_execute = AsyncMock(
        return_value=(
            "generated code",
            ["write_file"],
            [{"role": "user", "content": "fix bug"}],
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            {"some": "handoff"},
        )
    )

    # Mock run_review to return a PhaseArtifact with final_verdict.status="abort"
    abort_verdict = ReviewVerdict(
        status="abort",
        issues=[],
        files_modified=[],
        summary="Code is fundamentally broken, aborting.",
        checks_performed=["lint", "test"],
    )
    abort_artifact = PhaseArtifact(
        phase=Phase.REVIEW,
        content="review aborted",
        final_verdict=abort_verdict,
        tokens_used={"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75},
    )
    mock_review = AsyncMock(return_value=abort_artifact)

    # Mock run_report to track if it was called
    mock_report = AsyncMock(
        return_value=PhaseArtifact(
            phase=Phase.REPORT,
            content="report",
            tokens_used={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
    )

    with (
        patch("src.genver.pipeline.phase_runners.run_execute", mock_execute),
        patch("src.genver.pipeline.phase_runners.run_review", mock_review),
        patch("src.genver.pipeline.phase_runners.run_report", mock_report),
    ):
        result = await pipeline.run([{"role": "user", "content": "fix bug"}])

    # run_report should NOT have been called
    mock_report.assert_not_called()

    # Pipeline should still return a valid result (doesn't crash)
    assert result is not None
    final_content, tools_used, messages, usage = result
    assert isinstance(usage, dict)
