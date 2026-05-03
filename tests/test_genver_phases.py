# tests/test_genver_phases.py
"""Tests for individual phase runners."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.genver.artifact_store import ArtifactStore
from src.genver.models import Phase


def _mock_tool_loop_writes_file(artifact_store, filename, content_to_write, llm_output="Done."):
    """Mock run_tool_loop that writes an artifact as a side effect."""

    async def _run(**kwargs):
        artifact_store.write_artifact(filename, content_to_write)
        return (
            llm_output,
            [],
            kwargs["messages"],
            {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        )

    return _run


def _mock_verdict(status="pass"):
    v = {
        "status": status,
        "issues": [],
        "files_modified": [],
        "summary": "ok",
        "checks_performed": [],
    }
    return f"```json\n{json.dumps(v)}\n```"


@pytest.mark.asyncio
class TestRunClarify:
    async def test_extracts_requirements_json(self, tmp_path):
        from src.genver.phases import run_clarify

        clarify_output = '{"requirements": "build REST API", "complexity": "medium", "selected_phases": ["clarify", "plan", "execute", "review", "report"], "likely_files": ["src/api.py"], "rationale": "multi-file change"}'

        async def _run(**kwargs):
            return (
                clarify_output,
                [],
                kwargs["messages"],
                {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            )

        with patch("src.genver.phases.run_tool_loop", side_effect=_run):
            result = await run_clarify(
                user_request="build REST API",
                workspace=tmp_path,
                provider=MagicMock(),
                model="gen-model",
                tools=MagicMock(),
                max_iterations=20,
                store=ArtifactStore(tmp_path / ".genver"),
            )

        assert result["complexity"] == "medium"
        assert "plan" in result["selected_phases"]


@pytest.mark.asyncio
class TestRunSpec:
    async def test_writes_spec_and_runs_review(self, tmp_path):
        from src.genver.phases import run_spec

        store = ArtifactStore(tmp_path / ".genver")

        call_count = 0

        async def _side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Gen writes spec
                store.write_artifact("spec.md", "# Spec\n\nContent")
                return (
                    "Spec written.",
                    [],
                    kwargs["messages"],
                    {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                )
            else:
                # Ver reviews and passes
                return (
                    _mock_verdict("pass"),
                    [],
                    kwargs["messages"],
                    {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                )

        with (
            patch("src.genver.phases.run_tool_loop", side_effect=_side_effect),
            patch("src.genver.review.run_tool_loop", side_effect=_side_effect),
        ):
            artifact = await run_spec(
                user_request="build API",
                workspace=tmp_path,
                gen_provider=MagicMock(),
                ver_provider=MagicMock(),
                gen_model="gen",
                ver_model="ver",
                store=store,
                max_iterations=20,
            )

        assert artifact.phase == Phase.SPEC
        assert "Spec" in artifact.content


@pytest.mark.asyncio
class TestRunExecute:
    async def test_persists_handoff_artifact(self, tmp_path):
        """run_execute writes execute_handoff.json when the loop produces a handoff."""
        from src.genver.handoff import HandoffPayload
        from src.genver.phases import run_execute

        store = ArtifactStore(tmp_path / ".genver")

        fake_handoff = HandoffPayload(
            intent_summary="Added REST endpoint",
            files_changed=["src/api.py"],
            risk_assessment="low",
            diff_summary="Added GET /health route",
            test_commands=["pytest tests/test_api.py"],
            dev_log=["ran pytest — all green"],
            vulnerability_focus=["input validation"],
            target_commit_hash="abc123",
        )

        # Mock GenVerLoop so that loop.run() returns content and loop.last_handoff is set
        mock_loop_instance = MagicMock()
        mock_loop_instance.last_handoff = fake_handoff

        async def _mock_run(msgs):
            return (
                "Implementation complete.",
                ["write_file"],
                msgs,
                {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300},
            )

        mock_loop_instance.run = _mock_run

        with patch("src.genver.loop.GenVerLoop", return_value=mock_loop_instance):
            content, tools_used, messages, usage, handoff = await run_execute(
                user_request="add health endpoint",
                workspace=tmp_path,
                provider=MagicMock(),
                gen_model="gen",
                ver_model="ver",
                generator_tools=MagicMock(),
                store=store,
                genver_config=MagicMock(),
                default_model="gen",
            )

        assert handoff is fake_handoff

        # Verify the handoff was persisted to the artifact store
        persisted = store.read_round("execute_handoff")
        assert persisted is not None
        assert persisted["intent_summary"] == "Added REST endpoint"
        assert persisted["files_changed"] == ["src/api.py"]
        assert persisted["risk_assessment"] == "low"
        assert persisted["diff_summary"] == "Added GET /health route"
        assert persisted["test_commands"] == ["pytest tests/test_api.py"]
        assert persisted["dev_log"] == ["ran pytest — all green"]
        assert persisted["vulnerability_focus"] == ["input validation"]
        assert persisted["target_commit_hash"] == "abc123"

    async def test_no_handoff_skips_persist(self, tmp_path):
        """run_execute does not write execute_handoff.json when no handoff exists."""
        from src.genver.phases import run_execute

        store = ArtifactStore(tmp_path / ".genver")

        mock_loop_instance = MagicMock()
        mock_loop_instance.last_handoff = None

        async def _mock_run(msgs):
            return (
                "Done.",
                [],
                msgs,
                {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75},
            )

        mock_loop_instance.run = _mock_run

        with patch("src.genver.loop.GenVerLoop", return_value=mock_loop_instance):
            _, _, _, _, handoff = await run_execute(
                user_request="add health endpoint",
                workspace=tmp_path,
                provider=MagicMock(),
                gen_model="gen",
                ver_model="ver",
                generator_tools=MagicMock(),
                store=store,
                genver_config=MagicMock(),
                default_model="gen",
            )

        assert handoff is None
        assert store.read_round("execute_handoff") is None


@pytest.mark.asyncio
class TestRunReport:
    async def test_writes_report(self, tmp_path):
        from src.genver.phases import run_report

        store = ArtifactStore(tmp_path / ".genver")

        async def _run(**kwargs):
            store.write_artifact("report.md", "# GenVer Report\n\n## Task\nbuild API")
            return (
                "Report written.",
                [],
                kwargs["messages"],
                {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            )

        with patch("src.genver.phases.run_tool_loop", side_effect=_run):
            artifact = await run_report(
                user_request="build API",
                workspace=tmp_path,
                provider=MagicMock(),
                model="gen",
                tools=MagicMock(),
                store=store,
                phase_summaries=[],
                review_history=[],
                verification_result=None,
            )

        assert "Report" in artifact.content


@pytest.mark.asyncio
class TestRunReview:
    async def test_passes_clean_verification(self, tmp_path):
        """run_review returns pass when verifier passes on first try."""
        from unittest.mock import AsyncMock

        from src.genver.handoff import HandoffPayload
        from src.genver.phases import run_review

        store = ArtifactStore(tmp_path / ".genver")

        handoff = HandoffPayload(
            intent_summary="Added endpoint",
            files_changed=["src/api.py"],
            risk_assessment="low",
        )

        mock_verifier = MagicMock()
        mock_verifier.run_verification = AsyncMock(
            return_value={
                "passed": True,
                "errors": [],
                "checks_performed": ["lint", "tests"],
                "suggestions": [],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            }
        )

        with patch("src.genver.verifier.Verifier", return_value=mock_verifier):
            artifact = await run_review(
                user_request="add health endpoint",
                workspace=tmp_path,
                gen_provider=MagicMock(),
                ver_provider=MagicMock(),
                gen_model="gen",
                ver_model="ver",
                store=store,
                max_iterations=20,
                handoff=handoff,
            )

        assert artifact.phase == Phase.REVIEW
        assert artifact.final_verdict is not None
        assert artifact.final_verdict.status == "pass"
        assert len(artifact.review_records) == 1
        assert artifact.review_records[0].step == "ver_review"
        assert artifact.review_records[0].outcome == "pass"

    async def test_passes_with_edits_when_files_modified(self, tmp_path):
        """run_review coerces pass to pass_with_edits when git detects changes."""
        from unittest.mock import AsyncMock

        from src.genver.handoff import HandoffPayload
        from src.genver.phases import run_review

        store = ArtifactStore(tmp_path / ".genver")

        handoff = HandoffPayload(
            intent_summary="Added endpoint",
            files_changed=["src/api.py"],
            risk_assessment="low",
        )

        mock_verifier = MagicMock()
        mock_verifier.run_verification = AsyncMock(
            return_value={
                "passed": True,
                "errors": [],
                "checks_performed": ["lint"],
                "suggestions": [],
                "usage": {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75},
            }
        )

        # Mock subprocess.run to simulate git reporting modified files
        mock_diff = MagicMock()
        mock_diff.stdout = "src/api.py\n"

        with (
            patch("src.genver.verifier.Verifier", return_value=mock_verifier),
            patch("subprocess.run", return_value=mock_diff),
        ):
            artifact = await run_review(
                user_request="add endpoint",
                workspace=tmp_path,
                gen_provider=MagicMock(),
                ver_provider=MagicMock(),
                gen_model="gen",
                ver_model="ver",
                store=store,
                max_iterations=20,
                handoff=handoff,
            )

        assert artifact.final_verdict is not None
        assert artifact.final_verdict.status == "pass_with_edits"
        assert artifact.review_records[0].outcome == "pass_with_edits"

    async def test_no_handoff_returns_pass(self, tmp_path):
        """run_review returns pass immediately when no handoff is provided."""
        from src.genver.phases import run_review

        store = ArtifactStore(tmp_path / ".genver")

        artifact = await run_review(
            user_request="add endpoint",
            workspace=tmp_path,
            gen_provider=MagicMock(),
            ver_provider=MagicMock(),
            gen_model="gen",
            ver_model="ver",
            store=store,
            max_iterations=20,
            handoff=None,
        )

        assert artifact.final_verdict is not None
        assert artifact.final_verdict.status == "pass"


@pytest.mark.asyncio
class TestRunReverify:
    async def test_run_reverify_passes_clean_commands(self, tmp_path):
        """_run_reverify returns True when all commands pass."""
        from src.genver.phases import _run_reverify

        passed, results = await _run_reverify(tmp_path, ["echo hello", "true"])
        assert passed is True
        assert len(results) == 2

    async def test_run_reverify_detects_failure(self, tmp_path):
        """_run_reverify returns False when any command fails."""
        from src.genver.phases import _run_reverify

        passed, results = await _run_reverify(tmp_path, ["true", "false"])
        assert passed is False
        assert "FAIL" in results[1]

    async def test_run_reverify_empty_commands(self, tmp_path):
        """_run_reverify with no commands returns True."""
        from src.genver.phases import _run_reverify

        passed, results = await _run_reverify(tmp_path, [])
        assert passed is True
        assert len(results) == 0
