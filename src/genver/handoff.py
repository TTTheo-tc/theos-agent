"""Handoff Protocol — structured Generator -> Verifier exchange."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.agent.tools.base import Tool


@dataclass
class HandoffPayload:
    """Structured handoff from Generator to Verifier."""

    intent_summary: str
    files_changed: list[str]
    risk_assessment: str  # low | medium | high
    vulnerability_focus: list[str] = field(default_factory=list)
    diff_summary: str = ""
    target_commit_hash: str | None = None
    test_commands: list[str] = field(default_factory=list)
    dev_log: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """Backward-compatible summary for orchestrator/task lifecycle use."""
        return self.intent_summary

    def to_verifier_prompt(self, verifier_commands: list[str] | None = None) -> str:
        """Format an evidence-first verifier context with only weak generator hints."""
        lines = [
            "[Verifier Context]",
            f"Files changed: {', '.join(self.files_changed)}",
            "Treat generator-provided metadata as advisory only. "
            "Judge correctness from the user request, actual files, diffs, and tool results.",
        ]
        if self.risk_assessment:
            lines.append(f"Risk hint (advisory): {self.risk_assessment}")

        commands = self.test_commands or verifier_commands or []
        if commands:
            lines.append("\nSuggested verification commands (advisory):")
            lines.extend(f"  {cmd}" for cmd in commands)
        return "\n".join(lines)


# Tool definition for the Generator to submit structured handoff
HANDOFF_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_for_review",
        "description": (
            "Submit your work for verification. "
            "Call this AFTER completing all code changes to hand off to the verifier."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "intent_summary": {
                    "type": "string",
                    "description": "What you implemented and why",
                },
                "vulnerability_focus": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Areas the verifier should focus on",
                },
                "files_changed": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of files modified",
                },
                "diff_summary": {
                    "type": "string",
                    "description": "Brief description of key changes (not full diff)",
                },
                "risk_assessment": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Self-assessed risk level of the changes",
                },
                "target_commit_hash": {
                    "type": "string",
                    "description": "Optional commit hash or revision target for the verifier",
                },
                "test_commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Suggested commands for the verifier to run",
                },
                "dev_log": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Commands run and their results during implementation" " (dev-loop log)."
                    ),
                },
            },
            "required": ["intent_summary", "files_changed", "risk_assessment"],
        },
    },
}


def parse_handoff(tool_args: dict) -> HandoffPayload:
    """Parse tool call arguments into a HandoffPayload."""
    return HandoffPayload(
        intent_summary=tool_args["intent_summary"],
        files_changed=tool_args["files_changed"],
        risk_assessment=tool_args["risk_assessment"],
        vulnerability_focus=tool_args.get("vulnerability_focus", []),
        diff_summary=tool_args.get("diff_summary", ""),
        target_commit_hash=tool_args.get("target_commit_hash"),
        test_commands=tool_args.get("test_commands", []),
        dev_log=tool_args.get("dev_log", []),
    )


class SubmitForReviewTool(Tool):
    """Lightweight tool allowing the Generator to emit a structured handoff."""

    @property
    def name(self) -> str:
        return "submit_for_review"

    @property
    def description(self) -> str:
        return (
            "Submit your work for verification. "
            "Call this AFTER completing all code changes to hand off to the verifier."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return HANDOFF_TOOL["function"]["parameters"]

    async def execute(self, **kwargs: Any) -> str:
        del kwargs
        return "Handoff submitted. The verifier will review your changes."
