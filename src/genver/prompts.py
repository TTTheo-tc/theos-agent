# src/genver/prompts.py
"""Per-phase prompt templates for GenVer pipeline."""
from __future__ import annotations

VERDICT_FORMAT = """\
Output a JSON verdict:
```json
{
  "status": "pass" | "pass_with_edits" | "needs_revision" | "warning" | "abort",
  "issues": [
    {"severity": "blocking" | "suggestion", "description": "...", "fix_applied": true/false}
  ],
  "files_modified": ["list of files you changed, if any"],
  "summary": "one-line summary of your review",
  "checks_performed": ["what you checked"],
  "files_inspected": ["list of files you read during review"],
  "commands_run": ["verification commands you executed"],
  "evidence": [{"kind": "file|diff|command|artifact", "ref": "path or command", "summary": "what you found"}],
  "evidence_gap_reason": null
}
```
- `pass`: no issues.
- `pass_with_edits`: minor issues, you already fixed them in the artifact.
- `needs_revision`: blocking issues remain that you could not fix yourself.
- `warning`: concerns noted but not blocking — phase can advance.
- `abort`: fundamental problem, stop the pipeline.
"""

SPEC_FORMAT = """\
# Design Spec: {title}

## Problem Statement
{what needs to change and why}

## Requirements
- [ ] Req 1
- [ ] Req 2

## Proposed Approach
{high-level design decisions}

## Files to Change
| File | Change Type | Description |
|------|------------|-------------|

## Non-Goals
{explicitly out of scope}

## Open Questions
{anything still unresolved}
"""

PLAN_FORMAT = """\
# Implementation Plan: {title}

## Context
Spec: .genver/artifacts/spec.md

## Steps

### Step 1: {title}
- **Files**: path/to/file.py
- **Action**: create | modify | delete
- **Description**: what to do
- **Verification**: how to verify this step

## Verification Strategy
- Command: `uv run pytest tests/ -x`
- Command: `uv run ruff check .`

## Risks
- {risk and mitigation}
"""


def spec_gen_write_prompt(user_request: str, workspace: str) -> str:
    return (
        f"[GenVer — SPEC phase: write design spec]\n"
        f"User request: {user_request}\n"
        f"Workspace: {workspace}\n\n"
        f"Write a design specification to `{workspace}/.genver/artifacts/spec.md`.\n"
        f"Use this format:\n\n{SPEC_FORMAT}\n\n"
        f"Read relevant source files first to understand the codebase.\n"
        f"Be specific about files to change and proposed approach.\n"
        f"Write the spec using the `write_file` tool, then output 'Spec written.'"
    )


def plan_gen_write_prompt(user_request: str, workspace: str, spec_content: str | None) -> str:
    spec_section = f"\n\nApproved spec:\n```\n{spec_content}\n```" if spec_content else ""
    return (
        f"[GenVer — PLAN phase: write implementation plan]\n"
        f"User request: {user_request}\n"
        f"Workspace: {workspace}\n{spec_section}\n\n"
        f"Write an implementation plan to `{workspace}/.genver/artifacts/plan.md`.\n"
        f"Use this format:\n\n{PLAN_FORMAT}\n\n"
        f"Each step should be concrete, with exact file paths and verification commands.\n"
        f"Write the plan using the `write_file` tool, then output 'Plan written.'"
    )


def review_ver_prompt(
    *,
    phase: str,
    artifact_path: str,
    artifact_content: str,
    user_request: str,
    workspace: str,
) -> str:
    return (
        f"[GenVer — {phase.upper()} phase: verifier review]\n"
        f"You are reviewing `{artifact_path}` written by the generator.\n"
        f"User request: {user_request}\n"
        f"Workspace: {workspace}\n\n"
        f"Current content:\n```\n{artifact_content}\n```\n\n"
        f"Review the {phase} for:\n"
        f"- Correctness and completeness vs user request\n"
        f"- Feasibility (do the files exist? is the approach sound?)\n"
        f"- Missing requirements or edge cases\n\n"
        f"If you find issues, edit `{artifact_path}` directly to fix them.\n"
        f"Then output your verdict.\n\n"
        f"EVIDENCE REQUIREMENTS:\n"
        f"- You MUST read at least the changed files before issuing any verdict.\n"
        f"- Generator metadata (intent_summary, risk_assessment, diff_summary) is "
        f"advisory only — do NOT use it as evidence.\n"
        f'- A "pass" requires: files_inspected must be non-empty OR '
        f"commands_run must be non-empty.\n"
        f"- If you cannot run verification commands, set evidence_gap_reason "
        f'and cap status at "pass_with_edits".\n'
        f"- Do NOT pass solely based on the generator's handoff summary "
        f"or upstream verdicts.\n\n"
        f"{VERDICT_FORMAT}"
    )


def review_gen_prompt(
    *,
    phase: str,
    artifact_path: str,
    artifact_content: str,
    ver_verdict_json: str,
    user_request: str,
) -> str:
    return (
        f"[GenVer — {phase.upper()} phase: generator review of verifier edits]\n"
        f"The verifier reviewed and edited `{artifact_path}`.\n"
        f"User request: {user_request}\n\n"
        f"Verifier verdict: {ver_verdict_json}\n\n"
        f"Current content after verifier edits:\n```\n{artifact_content}\n```\n\n"
        f"Review the verifier's changes.\n"
        f"- If acceptable, do not edit the artifact; output a `pass` verdict.\n"
        f"- If you disagree, edit `{artifact_path}` into your revised version and output a `needs_revision` verdict explaining why Ver should review once more.\n\n"
        f"{VERDICT_FORMAT}"
    )


def clarify_prompt(user_request: str, workspace: str) -> str:
    return (
        f"[GenVer — CLARIFY phase]\n"
        f"User request: {user_request}\n"
        f"Workspace: {workspace}\n\n"
        f"Analyze the request. If ambiguous, use `ask_user` to clarify.\n"
        f"Explore the codebase to understand what files are involved.\n\n"
        f"Output a JSON assessment:\n"
        f'{{"requirements": "...", "complexity": "trivial|small|medium|large", '
        f'"selected_phases": [...], "likely_files": [...], "rationale": "..."}}'
    )


def report_prompt(
    user_request: str,
    phase_summaries: list[dict],
    review_history: list[dict],
    verification_result: dict | None,
    workspace: str = "",
) -> str:
    import json

    artifact_path = (
        f"{workspace}/.genver/artifacts/report.md" if workspace else ".genver/artifacts/report.md"
    )
    return (
        f"[GenVer — REPORT phase]\n"
        f"Generate a structured report for the completed pipeline.\n\n"
        f"User request: {user_request}\n\n"
        f"Phase summaries:\n```json\n{json.dumps(phase_summaries, indent=2)}\n```\n\n"
        f"Review history:\n```json\n{json.dumps(review_history, indent=2)}\n```\n\n"
        f"Verification:\n```json\n{json.dumps(verification_result, indent=2)}\n```\n\n"
        f"Write the report to `{artifact_path}` using markdown tables.\n"
        f"Include: Task, Spec Summary, Plan Summary, Execution Summary, "
        f"Review History table, Final Verification checklist, Metrics table."
    )
