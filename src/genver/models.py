# src/genver/models.py
"""Data models for the GenVer phase pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal


class Phase(StrEnum):
    CLARIFY = "clarify"
    SPEC = "spec"
    PLAN = "plan"
    EXECUTE = "execute"
    REVIEW = "review"
    REPORT = "report"


@dataclass
class ReviewIssue:
    severity: Literal["blocking", "suggestion"]
    description: str
    fix_applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "description": self.description,
            "fix_applied": self.fix_applied,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReviewIssue:
        return cls(
            severity=d["severity"],
            description=d["description"],
            fix_applied=d.get("fix_applied", False),
        )


@dataclass
class ReviewEvidence:
    kind: Literal["file", "diff", "command", "artifact", "repo_map"]
    ref: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "summary": self.summary}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReviewEvidence:
        return cls(kind=d["kind"], ref=d["ref"], summary=d["summary"])


@dataclass
class ReviewVerdict:
    status: Literal["pass", "pass_with_edits", "needs_revision", "warning", "abort"]
    issues: list[ReviewIssue]
    files_modified: list[str]
    summary: str
    checks_performed: list[str]
    files_inspected: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    evidence: list[ReviewEvidence] = field(default_factory=list)
    evidence_gap_reason: str | None = None

    @property
    def is_acceptable(self) -> bool:
        """True if the phase can advance (pass, pass_with_edits, warning)."""
        return self.status in ("pass", "pass_with_edits", "warning")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "status": self.status,
            "issues": [i.to_dict() for i in self.issues],
            "files_modified": self.files_modified,
            "summary": self.summary,
            "checks_performed": self.checks_performed,
            "files_inspected": self.files_inspected,
            "commands_run": self.commands_run,
            "evidence": [e.to_dict() for e in self.evidence],
        }
        if self.evidence_gap_reason is not None:
            d["evidence_gap_reason"] = self.evidence_gap_reason
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReviewVerdict:
        return cls(
            status=d["status"],
            issues=[ReviewIssue.from_dict(i) for i in d.get("issues", [])],
            files_modified=d.get("files_modified", []),
            summary=d.get("summary", ""),
            checks_performed=d.get("checks_performed", []),
            files_inspected=d.get("files_inspected", []),
            commands_run=d.get("commands_run", []),
            evidence=[ReviewEvidence.from_dict(e) for e in d.get("evidence", [])],
            evidence_gap_reason=d.get("evidence_gap_reason"),
        )


@dataclass
class PhaseReviewRecord:
    phase: Phase
    step: Literal["gen_write", "ver_review", "gen_review", "ver_final_review"]
    actor: Literal["gen", "ver"]
    outcome: Literal["pass", "pass_with_edits", "needs_revision", "warning", "abort"]
    files_modified: list[str] = field(default_factory=list)
    verdict: ReviewVerdict | None = None
    model: str = ""
    tokens: dict[str, int] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    escalation_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "phase": str(self.phase),
            "step": self.step,
            "actor": self.actor,
            "outcome": self.outcome,
            "files_modified": self.files_modified,
            "model": self.model,
            "tokens": self.tokens,
            "timestamp": self.timestamp,
        }
        if self.verdict:
            d["verdict"] = self.verdict.to_dict()
        if self.escalation_reason is not None:
            d["escalation_reason"] = self.escalation_reason
        return d


@dataclass
class PhaseArtifact:
    phase: Phase
    content: str = ""
    review_records: list[PhaseReviewRecord] = field(default_factory=list)
    final_verdict: ReviewVerdict | None = None
    tokens_used: dict[str, int] = field(default_factory=dict)
    budget_exhausted: bool = False
