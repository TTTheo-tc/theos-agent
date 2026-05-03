# tests/test_genver_models.py
"""Unit tests for GenVer phase pipeline data models."""
from __future__ import annotations

from src.genver.artifact_store import ArtifactStore
from src.genver.models import (
    Phase,
    PhaseArtifact,
    PhaseReviewRecord,
    ReviewEvidence,
    ReviewVerdict,
)


class TestPhase:
    def test_enum_values(self):
        assert Phase.CLARIFY == "clarify"
        assert Phase.SPEC == "spec"
        assert Phase.PLAN == "plan"
        assert Phase.EXECUTE == "execute"
        assert Phase.REVIEW == "review"
        assert Phase.REPORT == "report"

    def test_phase_from_string(self):
        assert Phase("spec") == Phase.SPEC


class TestReviewEvidence:
    def test_to_dict(self):
        e = ReviewEvidence(kind="file", ref="src/foo.py", summary="checked")
        assert e.to_dict() == {"kind": "file", "ref": "src/foo.py", "summary": "checked"}

    def test_from_dict(self):
        e = ReviewEvidence.from_dict({"kind": "diff", "ref": "a.py", "summary": "ok"})
        assert e.kind == "diff"
        assert e.ref == "a.py"

    def test_roundtrip(self):
        e = ReviewEvidence(kind="command", ref="pytest", summary="passed")
        assert ReviewEvidence.from_dict(e.to_dict()) == e


class TestReviewVerdict:
    def test_from_dict(self):
        d = {
            "status": "pass_with_edits",
            "issues": [
                {"severity": "blocking", "description": "missing return", "fix_applied": True}
            ],
            "files_modified": ["spec.md"],
            "summary": "Fixed one issue",
            "checks_performed": ["read spec", "check completeness"],
        }
        v = ReviewVerdict.from_dict(d)
        assert v.status == "pass_with_edits"
        assert len(v.issues) == 1
        assert v.issues[0].fix_applied is True

    def test_to_dict_roundtrip(self):
        v = ReviewVerdict(
            status="pass",
            issues=[],
            files_modified=[],
            summary="All good",
            checks_performed=["lint"],
        )
        d = v.to_dict()
        v2 = ReviewVerdict.from_dict(d)
        assert v2.status == v.status
        assert v2.summary == v.summary

    def test_is_acceptable(self):
        assert ReviewVerdict(
            status="pass", issues=[], files_modified=[], summary="", checks_performed=[]
        ).is_acceptable
        assert ReviewVerdict(
            status="pass_with_edits", issues=[], files_modified=[], summary="", checks_performed=[]
        ).is_acceptable
        assert ReviewVerdict(
            status="warning", issues=[], files_modified=[], summary="", checks_performed=[]
        ).is_acceptable
        assert not ReviewVerdict(
            status="needs_revision", issues=[], files_modified=[], summary="", checks_performed=[]
        ).is_acceptable
        assert not ReviewVerdict(
            status="abort", issues=[], files_modified=[], summary="", checks_performed=[]
        ).is_acceptable

    def test_new_evidence_fields_default_empty(self):
        v = ReviewVerdict(
            status="pass", issues=[], files_modified=[], summary="ok", checks_performed=[]
        )
        assert v.files_inspected == []
        assert v.commands_run == []
        assert v.evidence == []
        assert v.evidence_gap_reason is None

    def test_evidence_roundtrip(self):
        ev = ReviewEvidence(kind="file", ref="a.py", summary="read")
        v = ReviewVerdict(
            status="pass",
            issues=[],
            files_modified=[],
            summary="ok",
            checks_performed=[],
            files_inspected=["a.py"],
            commands_run=["pytest"],
            evidence=[ev],
            evidence_gap_reason=None,
        )
        d = v.to_dict()
        assert d["files_inspected"] == ["a.py"]
        assert d["commands_run"] == ["pytest"]
        assert len(d["evidence"]) == 1
        v2 = ReviewVerdict.from_dict(d)
        assert v2.files_inspected == ["a.py"]
        assert v2.evidence[0].kind == "file"

    def test_from_dict_backward_compat(self):
        """Old dicts without new fields should still parse."""
        d = {
            "status": "pass",
            "issues": [],
            "files_modified": [],
            "summary": "ok",
            "checks_performed": [],
        }
        v = ReviewVerdict.from_dict(d)
        assert v.files_inspected == []
        assert v.evidence == []


class TestPhaseReviewRecord:
    def test_to_dict(self):
        r = PhaseReviewRecord(
            phase=Phase.SPEC,
            step="ver_review",
            actor="ver",
            outcome="pass_with_edits",
            files_modified=["spec.md"],
            verdict=None,
        )
        d = r.to_dict()
        assert d["phase"] == "spec"
        assert d["step"] == "ver_review"

    def test_escalation_reason_in_to_dict(self):
        r = PhaseReviewRecord(
            phase=Phase.REVIEW,
            step="ver_final_review",
            actor="ver",
            outcome="warning",
            escalation_reason="bounded_review_exhausted",
        )
        d = r.to_dict()
        assert d["escalation_reason"] == "bounded_review_exhausted"


class TestPhaseArtifact:
    def test_empty(self):
        a = PhaseArtifact(phase=Phase.CLARIFY, content="requirements")
        assert a.review_records == []
        assert a.final_verdict is None
        assert a.tokens_used == {}

    def test_budget_exhausted_default(self):
        a = PhaseArtifact(phase=Phase.EXECUTE)
        assert a.budget_exhausted is False


class TestArtifactStore:
    def test_write_and_read_artifact(self, tmp_path):
        store = ArtifactStore(tmp_path / ".genver")
        store.write_artifact("spec.md", "# My Spec\n\nContent here.")
        assert store.read_artifact("spec.md") == "# My Spec\n\nContent here."

    def test_read_missing_artifact_returns_none(self, tmp_path):
        store = ArtifactStore(tmp_path / ".genver")
        assert store.read_artifact("missing.md") is None

    def test_write_and_read_round(self, tmp_path):
        store = ArtifactStore(tmp_path / ".genver")
        record = PhaseReviewRecord(
            phase=Phase.SPEC,
            step="ver_review",
            actor="ver",
            outcome="pass_with_edits",
            files_modified=["spec.md"],
            model="gpt-5.4",
            tokens={"prompt": 1000, "completion": 500},
        )
        store.write_round("spec_ver_review", record.to_dict())
        loaded = store.read_round("spec_ver_review")
        assert loaded["phase"] == "spec"
        assert loaded["model"] == "gpt-5.4"

    def test_list_rounds(self, tmp_path):
        store = ArtifactStore(tmp_path / ".genver")
        store.write_round("spec_gen_write", {"phase": "spec"})
        store.write_round("spec_ver_review", {"phase": "spec"})
        names = store.list_rounds()
        assert "spec_gen_write" in names
        assert "spec_ver_review" in names

    def test_write_runtime(self, tmp_path):
        store = ArtifactStore(tmp_path / ".genver")
        store.write_runtime("verify_report_1", {"passed": True})
        loaded = store.read_runtime("verify_report_1")
        assert loaded["passed"] is True

    def test_clear_runtime_preserves_artifacts(self, tmp_path):
        store = ArtifactStore(tmp_path / ".genver")
        store.write_artifact("spec.md", "keep this")
        store.write_round("spec_gen_write", {"keep": True})
        store.write_runtime("verify_1", {"discard": True})
        store.clear_runtime()
        assert store.read_artifact("spec.md") == "keep this"
        assert store.read_round("spec_gen_write") is not None
        assert store.read_runtime("verify_1") is None


class TestGenVerConfigExtension:
    def test_new_fields_have_defaults(self):
        from src.config.schema import GenVerConfig

        c = GenVerConfig()
        assert c.phases == ["clarify", "spec", "plan", "execute", "review", "report"]
        assert c.max_review_rounds == 1
        assert c.auto_phase_selection is True
        assert c.spec_max_iterations == 20
        assert c.plan_max_iterations == 20
        assert c.review_max_iterations == 30

    def test_existing_fields_unchanged(self):
        from src.config.schema import GenVerConfig

        c = GenVerConfig()
        assert c.generator_model == ""
        assert c.verifier_model == ""
        assert c.max_retries == 3
        assert c.generator_max_iterations == 60
        assert c.verifier_max_iterations == 30
        assert c.workspace_subdir == ".genver"


class TestPrompts:
    def test_spec_gen_prompt_contains_format(self):
        from src.genver.prompts import spec_gen_write_prompt

        p = spec_gen_write_prompt("build a REST API", "/workspace")
        assert "Problem Statement" in p
        assert "Requirements" in p
        assert "Non-Goals" in p

    def test_review_ver_prompt_contains_artifact_path(self):
        from src.genver.prompts import review_ver_prompt

        p = review_ver_prompt(
            phase="spec",
            artifact_path=".genver/artifacts/spec.md",
            artifact_content="# Spec\n\nContent",
            user_request="build API",
            workspace="/ws",
        )
        assert "spec.md" in p
        assert "build API" in p

    def test_review_gen_prompt_includes_diff(self):
        from src.genver.prompts import review_gen_prompt

        p = review_gen_prompt(
            phase="spec",
            artifact_path=".genver/artifacts/spec.md",
            artifact_content="# Spec v2",
            ver_verdict_json='{"status": "pass_with_edits"}',
            user_request="build API",
        )
        assert "pass_with_edits" in p

    def test_verdict_format_instruction(self):
        from src.genver.prompts import VERDICT_FORMAT

        assert '"status"' in VERDICT_FORMAT
        assert "pass_with_edits" in VERDICT_FORMAT

    def test_verdict_format_includes_evidence_fields(self):
        from src.genver.prompts import VERDICT_FORMAT

        assert "files_inspected" in VERDICT_FORMAT
        assert "commands_run" in VERDICT_FORMAT
        assert "evidence" in VERDICT_FORMAT
        assert "evidence_gap_reason" in VERDICT_FORMAT

    def test_review_ver_prompt_includes_evidence_requirements(self):
        from src.genver.prompts import review_ver_prompt

        prompt = review_ver_prompt(
            phase="spec",
            artifact_path="spec.md",
            artifact_content="# Spec",
            user_request="build X",
            workspace="/ws",
        )
        assert "EVIDENCE REQUIREMENTS" in prompt
        assert "advisory only" in prompt

    def test_report_prompt_contains_workspace(self):
        from src.genver.prompts import report_prompt

        p = report_prompt("build API", [], [], None, workspace="/ws")
        assert "/ws/.genver/artifacts/report.md" in p
