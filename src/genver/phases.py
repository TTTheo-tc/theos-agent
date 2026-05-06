# src/genver/phases.py
"""Per-phase runner functions for the GenVer pipeline."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.agent.loop_core import run_tool_loop
from src.genver import prompts
from src.genver.artifact_store import ArtifactStore
from src.genver.models import Phase, PhaseArtifact
from src.genver.review import PhaseReviewProtocol
from src.utils.usage import merge_usage

if TYPE_CHECKING:
    from src.agent.tools.registry import ToolRegistry
    from src.providers.base import LLMProvider


async def run_clarify(
    *,
    user_request: str,
    workspace: Path,
    provider: LLMProvider,
    model: str,
    tools: ToolRegistry,
    max_iterations: int,
    store: ArtifactStore,
    ask_user: Any = None,
) -> dict[str, Any]:
    """Phase 0: Clarify requirements. Returns parsed JSON assessment."""
    del ask_user
    prompt = prompts.clarify_prompt(user_request, str(workspace))
    messages = [
        {
            "role": "system",
            "content": "You are analyzing a task to determine scope and requirements.",
        },
        {"role": "user", "content": prompt},
    ]

    content, _, _, usage = await run_tool_loop(
        provider=provider,
        messages=messages,
        tools=tools,
        model=model,
        temperature=0.1,
        max_tokens=16384,
        max_iterations=max_iterations,
    )

    # Parse JSON from output
    result = _extract_json(content or "")
    result.setdefault("requirements", user_request)
    result.setdefault("complexity", "medium")
    result.setdefault("selected_phases", ["clarify", "execute", "review", "report"])
    result.setdefault("likely_files", [])
    result.setdefault("rationale", "")

    store.write_round("clarify", {**result, "tokens": usage})
    logger.info(
        "[GenVer:CLARIFY] complexity={} phases={}", result["complexity"], result["selected_phases"]
    )
    return result


async def run_spec(
    *,
    user_request: str,
    workspace: Path,
    gen_provider: LLMProvider,
    ver_provider: LLMProvider,
    gen_model: str,
    ver_model: str,
    store: ArtifactStore,
    max_iterations: int,
    gen_tools: ToolRegistry | None = None,
    ver_tools: ToolRegistry | None = None,
) -> PhaseArtifact:
    """Phase 1: Write and review spec."""
    return await _write_and_review(
        phase=Phase.SPEC,
        artifact_name="spec.md",
        write_prompt_fn=lambda: prompts.spec_gen_write_prompt(user_request, str(workspace)),
        user_request=user_request,
        workspace=workspace,
        gen_provider=gen_provider,
        ver_provider=ver_provider,
        gen_model=gen_model,
        ver_model=ver_model,
        store=store,
        max_iterations=max_iterations,
        gen_tools=gen_tools,
        ver_tools=ver_tools,
    )


async def run_plan(
    *,
    user_request: str,
    workspace: Path,
    gen_provider: LLMProvider,
    ver_provider: LLMProvider,
    gen_model: str,
    ver_model: str,
    store: ArtifactStore,
    max_iterations: int,
    gen_tools: ToolRegistry | None = None,
    ver_tools: ToolRegistry | None = None,
) -> PhaseArtifact:
    """Phase 2: Write and review plan."""
    spec_content = store.read_artifact("spec.md")
    return await _write_and_review(
        phase=Phase.PLAN,
        artifact_name="plan.md",
        write_prompt_fn=lambda: prompts.plan_gen_write_prompt(
            user_request, str(workspace), spec_content
        ),
        user_request=user_request,
        workspace=workspace,
        gen_provider=gen_provider,
        ver_provider=ver_provider,
        gen_model=gen_model,
        ver_model=ver_model,
        store=store,
        max_iterations=max_iterations,
        gen_tools=gen_tools,
        ver_tools=ver_tools,
    )


async def run_execute(
    *,
    user_request: str,
    workspace: Path,
    provider: LLMProvider,
    gen_model: str,
    ver_model: str,
    generator_tools: ToolRegistry,
    store: ArtifactStore,
    genver_config: Any,
    default_model: str,
    max_tokens: int = 16384,
    temperature: float = 0.1,
    add_assistant: Any = None,
    add_tool_result: Any = None,
    on_progress: Any = None,
    tool_context: Any = None,
    ask_user: Any = None,
    messages: list[dict] | None = None,
) -> tuple[str | None, list[str], list[dict], dict[str, int], Any]:
    """Phase 3: Execute using the existing GenVerLoop engine.

    Returns (content, tools_used, messages, usage, handoff).
    """
    del gen_model, ver_model
    from src.genver.loop import GenVerLoop

    spec_content = store.read_artifact("spec.md")
    plan_content = store.read_artifact("plan.md")

    # Build messages with plan/spec context injected BEFORE the user request.
    # This ensures the generator sees the approved spec/plan as guidance.
    if messages is None:
        messages = [
            {"role": "system", "content": "You are a skilled software engineer."},
            {"role": "user", "content": user_request},
        ]

    # Inject spec/plan as context message right after system message.
    # GenVerLoop.run() injects its own preamble at index 1, so we insert
    # the spec/plan context at index 1 first — it will end up at index 2.
    context_parts = []
    if spec_content:
        context_parts.append(f"[Approved Design Spec]\n```\n{spec_content}\n```")
    if plan_content:
        context_parts.append(f"[Approved Implementation Plan]\n```\n{plan_content}\n```")
    if context_parts:
        ctx_msg = {"role": "user", "content": "\n\n".join(context_parts)}
        if messages and messages[0].get("role") == "system":
            messages = [messages[0], ctx_msg, *messages[1:]]
        else:
            messages = [ctx_msg, *messages]

    loop = GenVerLoop(
        config=genver_config,
        provider=provider,
        workspace=workspace,
        generator_tools=generator_tools,
        pipeline_mode=True,
        default_model=default_model,
        temperature=temperature,
        max_tokens=max_tokens,
        add_assistant=add_assistant,
        add_tool_result=add_tool_result,
        on_progress=on_progress,
        tool_context=tool_context,
        ask_user=ask_user,
    )

    content, tools_used, messages, usage = await loop.run(messages)

    handoff = loop.last_handoff
    if handoff:
        store.write_round(
            "execute_handoff",
            {
                "intent_summary": handoff.intent_summary,
                "files_changed": handoff.files_changed,
                "risk_assessment": handoff.risk_assessment,
                "diff_summary": handoff.diff_summary,
                "test_commands": handoff.test_commands,
                "dev_log": handoff.dev_log,
                "vulnerability_focus": handoff.vulnerability_focus,
                "target_commit_hash": handoff.target_commit_hash,
            },
        )

    return content, tools_used, messages, usage, handoff


async def run_review(
    *,
    user_request: str,
    workspace: Path,
    gen_provider: LLMProvider,
    ver_provider: LLMProvider,
    gen_model: str,
    ver_model: str,
    store: ArtifactStore,
    max_iterations: int,
    verifier_commands: list[str] | None = None,
    handoff: Any = None,
    gen_tools: ToolRegistry | None = None,
    ver_tools: ToolRegistry | None = None,
) -> PhaseArtifact:
    """Phase 4: Code review with bounded protocol.

    REVIEW differs from SPEC/PLAN in that the verifier's first step operates on
    the codebase directly. But it still follows the same bounded protocol:
      ver_review -> gen_review? -> ver_final_review?
    """
    from src.agent.agentfs import AgentFS
    from src.genver.models import PhaseReviewRecord, ReviewVerdict
    from src.genver.verifier import Verifier

    agentfs = AgentFS(workspace, subdir=str(store.root.relative_to(workspace)))

    verifier = Verifier(
        workspace=workspace,
        agentfs=agentfs,
        verifier_provider=ver_provider,
        verifier_model=ver_model,
        generator_model=gen_model,
        verifier_commands=verifier_commands or [],
        verifier_max_iterations=max_iterations,
    )

    # Step 1: Ver reviews code (read-only verification)
    if handoff:
        verdict = await verifier.run_verification(handoff=handoff, user_request=user_request)
    else:
        verdict = {
            "passed": True,
            "errors": [],
            "checks_performed": [],
            "suggestions": [],
            "usage": {},
        }

    if verdict["passed"]:
        # Design doc 7.5: pass with files_modified non-empty -> force pass_with_edits
        try:
            diff_result = subprocess.run(
                ["git", "diff", "--name-only"],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=10,
            )
            has_modifications = bool(diff_result.stdout.strip())
        except Exception:
            has_modifications = False
        status = "pass_with_edits" if has_modifications else "pass"
        final_v = ReviewVerdict(
            status=status,
            issues=[],
            files_modified=[],
            summary="All verification checks passed",
            checks_performed=verdict.get("checks_performed", []),
        )
        store.write_round(
            "review_ver_review",
            {
                "phase": "review",
                "step": "ver_review",
                "actor": "ver",
                "outcome": status,
                "summary": final_v.summary,
                "tokens": verdict.get("usage", {}),
            },
        )
        return PhaseArtifact(
            phase=Phase.REVIEW,
            content="Verification passed.",
            review_records=[
                PhaseReviewRecord(
                    phase=Phase.REVIEW,
                    step="ver_review",
                    actor="ver",
                    outcome=status,
                    verdict=final_v,
                    model=ver_model,
                    tokens=verdict.get("usage", {}),
                )
            ],
            final_verdict=final_v,
            tokens_used=verdict.get("usage", {}),
        )

    # Step 2: Ver applies fixes for the blocking issues.
    repair = await verifier.run_repair(
        attempt=1,
        report_name="review_ver_review",
        error_summary="\n".join(verdict.get("errors", [])[:10]),
        user_request=user_request,
        handoff=handoff,
    )
    repair_summary = repair.get("content", "Verifier applied fixes.")
    store.write_round(
        "review_ver_review",
        {
            "phase": "review",
            "step": "ver_review",
            "actor": "ver",
            "outcome": "needs_revision",
            "summary": "; ".join(verdict.get("errors", [])[:3]),
            "tokens": verdict.get("usage", {}),
        },
    )

    # Step 1.5: Re-verify after Ver edited code
    reverify_passed, reverify_results = await _run_reverify(workspace, verifier_commands or [])
    logger.info("[GenVer REVIEW] Re-verify: {}", "PASS" if reverify_passed else "FAIL")

    # Surface verifier changes to the generator as a synthetic artifact.
    review_sections = [
        "# Review Artifact",
        "## Verifier Findings",
        "\n".join(f"- {err}" for err in verdict.get("errors", [])) or "- none",
        "## Verifier Repair Summary",
        repair_summary,
    ]
    if reverify_results:
        review_sections.append("## Re-verify Results")
        review_sections.append("\n".join(f"- {r}" for r in reverify_results))
    store.write_artifact("review.md", "\n\n".join(review_sections))

    initial_record = PhaseReviewRecord(
        phase=Phase.REVIEW,
        step="ver_review",
        actor="ver",
        outcome="needs_revision",
        verdict=ReviewVerdict(
            status="needs_revision",
            issues=[],
            files_modified=repair.get("tools_used", []),
            summary="Verifier found blocking issues and applied an initial fix set.",
            checks_performed=verdict.get("checks_performed", []),
        ),
        model=ver_model,
        tokens=verdict.get("usage", {}),
    )

    protocol = PhaseReviewProtocol(
        phase=Phase.REVIEW,
        artifact_name="review.md",
        store=store,
        user_request=user_request,
        workspace=workspace,
        gen_provider=gen_provider,
        ver_provider=ver_provider,
        gen_model=gen_model,
        ver_model=ver_model,
        max_iterations=max_iterations,
        gen_tools=gen_tools,
        ver_tools=ver_tools,
    )
    artifact = await protocol.run(initial_record=initial_record)

    merge_usage(artifact.tokens_used, verdict.get("usage", {}))
    merge_usage(artifact.tokens_used, repair.get("usage", {}))
    return artifact


async def run_report(
    *,
    user_request: str,
    workspace: Path,
    provider: LLMProvider,
    model: str,
    tools: ToolRegistry,
    store: ArtifactStore,
    phase_summaries: list[dict],
    review_history: list[dict],
    verification_result: dict | None,
) -> PhaseArtifact:
    """Phase 5: Generate structured report."""
    prompt = prompts.report_prompt(
        user_request,
        phase_summaries,
        review_history,
        verification_result,
        workspace=str(workspace),
    )
    messages = [
        {"role": "system", "content": "You are writing a summary report."},
        {"role": "user", "content": prompt},
    ]

    content, _, _, usage = await run_tool_loop(
        provider=provider,
        messages=messages,
        tools=tools,
        model=model,
        temperature=0.1,
        max_tokens=16384,
        max_iterations=20,
    )

    report = store.read_artifact("report.md") or content or ""
    return PhaseArtifact(
        phase=Phase.REPORT,
        content=report,
        tokens_used=usage,
    )


async def _run_reverify(
    workspace: Path,
    verifier_commands: list[str],
) -> tuple[bool, list[str]]:
    """Run verifier commands to check if Ver's edits introduced regressions.

    Returns (all_passed, command_results).
    """
    import asyncio

    results: list[str] = []
    all_passed = True
    for cmd in verifier_commands:
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            passed = proc.returncode == 0
            output_snippet = (stdout or b"").decode(errors="replace")[:500]
            results.append(
                f"{'PASS' if passed else 'FAIL'}: {cmd}"
                + (f"\n{output_snippet}" if not passed else "")
            )
            if not passed:
                all_passed = False
        except asyncio.TimeoutError:
            results.append(f"TIMEOUT: {cmd}")
            all_passed = False
        except Exception as e:
            results.append(f"ERROR: {cmd}: {e}")
            all_passed = False
    return all_passed, results


# --- Internal helpers ---


async def _write_and_review(
    *,
    phase: Phase,
    artifact_name: str,
    write_prompt_fn: Any,
    user_request: str,
    workspace: Path,
    gen_provider: LLMProvider,
    ver_provider: LLMProvider,
    gen_model: str,
    ver_model: str,
    store: ArtifactStore,
    max_iterations: int,
    gen_tools: ToolRegistry | None = None,
    ver_tools: ToolRegistry | None = None,
) -> PhaseArtifact:
    """Shared logic for SPEC and PLAN: gen writes, then review protocol runs."""
    from src.agent.tool_sets import register_standard_tools
    from src.agent.tools.registration import ToolRegistrationConfig
    from src.agent.tools.registry import ToolRegistry

    # Step 0: Gen writes the artifact
    if gen_tools is None:
        gen_tools = ToolRegistry()
        register_standard_tools(
            gen_tools, ToolRegistrationConfig(workspace=workspace, mode="subagent")
        )

    prompt = write_prompt_fn()
    messages = [
        {"role": "system", "content": f"You are writing a {phase} document."},
        {"role": "user", "content": prompt},
    ]

    logger.info("[GenVer:{}] Gen writing artifact={}", phase, artifact_name)
    content, _, _, usage = await run_tool_loop(
        provider=gen_provider,
        messages=messages,
        tools=gen_tools,
        model=gen_model,
        temperature=0.1,
        max_tokens=16384,
        max_iterations=max_iterations,
    )

    gen_record = {
        "phase": str(phase),
        "step": "gen_write",
        "actor": "gen",
        "outcome": "pass",
        "tokens": usage,
    }
    store.write_round(f"{phase}_gen_write", gen_record)

    # If artifact wasn't written by the tool, save the LLM content as the artifact
    if store.read_artifact(artifact_name) is None and content:
        store.write_artifact(artifact_name, content)

    # Run bounded review protocol
    protocol = PhaseReviewProtocol(
        phase=phase,
        artifact_name=artifact_name,
        store=store,
        user_request=user_request,
        workspace=workspace,
        gen_provider=gen_provider,
        ver_provider=ver_provider,
        gen_model=gen_model,
        ver_model=ver_model,
        max_iterations=max_iterations,
        gen_tools=gen_tools,
        ver_tools=ver_tools,
    )
    artifact = await protocol.run()

    # Prepend the gen_write usage
    merge_usage(artifact.tokens_used, usage)

    return artifact


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction from LLM output."""
    # Try markdown fences
    for pattern in [r"```json\s*\n(.*?)\n\s*```", r"```\s*\n(.*?)\n\s*```"]:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
    # Try raw JSON
    for m in re.finditer(r"\{[^{}]*\}", text):
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            continue
    return {}
