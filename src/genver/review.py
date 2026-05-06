# src/genver/review.py
"""Bounded gen↔ver review protocol.

Review steps (max 3; `gen_write` happens before this protocol starts):
  1. ver_review — verifier reviews artifact, may edit
  2. gen_review — generator reviews verifier edits (only if ver returned needs_revision)
  3. ver_final_review — final verifier pass (only if gen revised in step 2)

Each step produces a PhaseReviewRecord persisted to artifacts/rounds/.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.agent.loop_core import run_tool_loop
from src.genver import prompts
from src.genver.artifact_store import ArtifactStore
from src.genver.models import (
    Phase,
    PhaseArtifact,
    PhaseReviewRecord,
    ReviewVerdict,
)
from src.utils.usage import merge_usage

if TYPE_CHECKING:
    from src.agent.tools.registry import ToolRegistry
    from src.providers.base import LLMProvider


def _parse_verdict(content: str | None) -> ReviewVerdict:
    """Extract a ReviewVerdict from LLM output, with fallbacks."""
    if not content:
        return ReviewVerdict(
            status="warning",
            issues=[],
            files_modified=[],
            summary="No verdict output from reviewer",
            checks_performed=[],
        )
    # Try markdown-fenced JSON
    for pattern in [r"```json\s*\n(.*?)\n\s*```", r"```\s*\n(.*?)\n\s*```", r"(\{[^{}]*\})"]:
        m = re.search(pattern, content, re.DOTALL)
        if m:
            try:
                d = json.loads(m.group(1))
                if "status" in d:
                    return ReviewVerdict.from_dict(d)
            except (json.JSONDecodeError, KeyError):
                continue
    # Fallback: treat as warning with the raw content as summary
    return ReviewVerdict(
        status="warning",
        issues=[],
        files_modified=[],
        summary=content[:200],
        checks_performed=[],
    )


class PhaseReviewProtocol:
    """Runs a bounded review exchange for a single phase artifact."""

    def __init__(
        self,
        *,
        phase: Phase,
        artifact_name: str,
        store: ArtifactStore,
        user_request: str,
        workspace: Path,
        gen_provider: LLMProvider,
        ver_provider: LLMProvider,
        gen_model: str,
        ver_model: str,
        max_iterations: int = 20,
        gen_tools: ToolRegistry | None = None,
        ver_tools: ToolRegistry | None = None,
    ) -> None:
        self.phase = phase
        self.artifact_name = artifact_name
        self.store = store
        self.user_request = user_request
        self.workspace = workspace
        self.gen_provider = gen_provider
        self.ver_provider = ver_provider
        self.gen_model = gen_model
        self.ver_model = ver_model
        self.max_iterations = max_iterations
        self._gen_tools = gen_tools
        self._ver_tools = ver_tools

    def _make_read_tools(self) -> ToolRegistry:
        """Create a minimal read-only tool registry for review steps."""
        from src.agent.tool_sets import register_standard_tools
        from src.agent.tools.registration import ToolRegistrationConfig
        from src.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        config = ToolRegistrationConfig(workspace=self.workspace, mode="verifier")
        register_standard_tools(registry, config)
        return registry

    def _make_write_tools(self) -> ToolRegistry:
        """Create a tool registry with write access for reviewers who can edit."""
        from src.agent.tool_sets import register_standard_tools
        from src.agent.tools.registration import ToolRegistrationConfig
        from src.agent.tools.registry import ToolRegistry

        registry = ToolRegistry()
        config = ToolRegistrationConfig(workspace=self.workspace, mode="subagent")
        register_standard_tools(registry, config)
        return registry

    async def run(
        self,
        *,
        initial_record: PhaseReviewRecord | None = None,
    ) -> PhaseArtifact:
        """Execute the bounded review protocol. Returns a PhaseArtifact."""
        records: list[PhaseReviewRecord] = []
        total_tokens: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        artifact_path = f".genver/artifacts/{self.artifact_name}"

        if initial_record is not None:
            records.append(initial_record)
            merge_usage(total_tokens, initial_record.tokens)
            ver_verdict = initial_record.verdict or ReviewVerdict(
                status="warning",
                issues=[],
                files_modified=[],
                summary="Missing initial verifier verdict",
                checks_performed=[],
            )
        else:
            # Step 1: ver_review
            ver_verdict = await self._run_step(
                step="ver_review",
                actor="ver",
                provider=self.ver_provider,
                model=self.ver_model,
                tools=self._ver_tools or self._make_write_tools(),
                prompt_fn=lambda content: prompts.review_ver_prompt(
                    phase=str(self.phase),
                    artifact_path=artifact_path,
                    artifact_content=content,
                    user_request=self.user_request,
                    workspace=str(self.workspace),
                ),
                records=records,
                total_tokens=total_tokens,
            )

        if ver_verdict.is_acceptable:
            return PhaseArtifact(
                phase=self.phase,
                content=self.store.read_artifact(self.artifact_name) or "",
                review_records=records,
                final_verdict=ver_verdict,
                tokens_used=total_tokens,
            )

        # Step 2: gen_review (only if ver returned needs_revision)
        gen_verdict = await self._run_step(
            step="gen_review",
            actor="gen",
            provider=self.gen_provider,
            model=self.gen_model,
            tools=self._gen_tools or self._make_write_tools(),
            prompt_fn=lambda content: prompts.review_gen_prompt(
                phase=str(self.phase),
                artifact_path=artifact_path,
                artifact_content=content,
                ver_verdict_json=json.dumps(ver_verdict.to_dict()),
                user_request=self.user_request,
            ),
            records=records,
            total_tokens=total_tokens,
        )

        if gen_verdict.is_acceptable:
            return PhaseArtifact(
                phase=self.phase,
                content=self.store.read_artifact(self.artifact_name) or "",
                review_records=records,
                final_verdict=gen_verdict,
                tokens_used=total_tokens,
            )

        # Step 3: ver_final_review (terminal — force advance with result)
        ver_final = await self._run_step(
            step="ver_final_review",
            actor="ver",
            provider=self.ver_provider,
            model=self.ver_model,
            tools=self._ver_tools or self._make_read_tools(),
            prompt_fn=lambda content: prompts.review_ver_prompt(
                phase=str(self.phase),
                artifact_path=artifact_path,
                artifact_content=content,
                user_request=self.user_request,
                workspace=str(self.workspace),
            ),
            records=records,
            total_tokens=total_tokens,
        )

        # Terminal: if still not acceptable, force warning to advance
        final = ver_final
        if not ver_final.is_acceptable:
            final = ReviewVerdict(
                status="warning",
                issues=ver_final.issues,
                files_modified=ver_final.files_modified,
                summary=f"Advance with warning after 3 review steps: {ver_final.summary}",
                checks_performed=ver_final.checks_performed,
            )
            # Tag the last record with escalation reason for observability
            if records:
                records[-1].escalation_reason = "bounded_review_exhausted"

        return PhaseArtifact(
            phase=self.phase,
            content=self.store.read_artifact(self.artifact_name) or "",
            review_records=records,
            final_verdict=final,
            tokens_used=total_tokens,
        )

    async def _run_step(
        self,
        *,
        step: str,
        actor: str,
        provider: Any,
        model: str,
        tools: Any,
        prompt_fn: Any,
        records: list[PhaseReviewRecord],
        total_tokens: dict[str, int],
    ) -> ReviewVerdict:
        """Run a single review step, persist record, return verdict."""
        artifact_content = self.store.read_artifact(self.artifact_name) or ""
        prompt = prompt_fn(artifact_content)

        messages = [
            {"role": "system", "content": f"You are a code reviewer in the {self.phase} phase."},
            {"role": "user", "content": prompt},
        ]

        logger.info("[GenVer:{}:{}] Running step={} model={}", self.phase, actor, step, model)

        content, tools_used, messages, usage = await run_tool_loop(
            provider=provider,
            messages=messages,
            tools=tools,
            model=model,
            temperature=0.1,
            max_tokens=16384,
            max_iterations=self.max_iterations,
        )

        verdict = _parse_verdict(content)

        # Re-read artifact in case the reviewer edited it
        updated_content = self.store.read_artifact(self.artifact_name) or ""
        files_modified = [self.artifact_name] if updated_content != artifact_content else []

        record = PhaseReviewRecord(
            phase=self.phase,
            step=step,
            actor=actor,
            outcome=verdict.status,
            files_modified=files_modified,
            verdict=verdict,
            model=model,
            tokens=usage,
        )
        records.append(record)
        self.store.write_round(f"{self.phase}_{step}", record.to_dict())

        merge_usage(total_tokens, usage)

        logger.info("[GenVer:{}:{}] step={} outcome={}", self.phase, actor, step, verdict.status)
        return verdict
