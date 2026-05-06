# src/genver/pipeline.py
"""GenVerPipeline — top-level phase sequence driver.

Replaces the monolithic GenVerLoop.run() with a phased pipeline:
  CLARIFY -> SPEC -> PLAN -> EXECUTE -> REVIEW -> REPORT

Each phase is optional and can be skipped via config or auto-detection.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from src.genver import phases as phase_runners
from src.genver.artifact_store import ArtifactStore
from src.genver.models import Phase, PhaseArtifact
from src.utils.usage import merge_usage

if TYPE_CHECKING:
    from src.agent.loop_core import AddAssistantFn, AddToolResultFn, ProgressFn
    from src.agent.tools.registry import ToolRegistry
    from src.config.schema import GenVerConfig
    from src.providers.base import LLMProvider

AskUserFn = Callable[[str], Awaitable[str | None]]

# --- Complexity classification ---

_LARGE_MARKERS = re.compile(
    r"redesign|refactor.*architect|rewrite|overhaul|migration|"
    r"重构.*架构|重新设计|全面改造|迁移",
    re.IGNORECASE,
)
_MEDIUM_MARKERS = re.compile(
    r"add.*module|new.*feature|implement.*system|integrate|" r"新增.*模块|实现.*系统|集成",
    re.IGNORECASE,
)


def classify_complexity(user_request: str) -> str:
    """Lightweight heuristic to classify task complexity."""
    if _LARGE_MARKERS.search(user_request):
        return "large"
    if _MEDIUM_MARKERS.search(user_request):
        return "medium"
    word_count = len(user_request.split())
    if word_count < 15:
        return "trivial"
    return "small"


def phases_for_complexity(complexity: str) -> list[Phase]:
    """Return the default phase list for a given complexity."""
    if complexity == "trivial":
        return [Phase.EXECUTE, Phase.REVIEW, Phase.REPORT]
    if complexity == "small":
        return [Phase.CLARIFY, Phase.EXECUTE, Phase.REVIEW, Phase.REPORT]
    if complexity == "medium":
        return [Phase.CLARIFY, Phase.PLAN, Phase.EXECUTE, Phase.REVIEW, Phase.REPORT]
    # large
    return [Phase.CLARIFY, Phase.SPEC, Phase.PLAN, Phase.EXECUTE, Phase.REVIEW, Phase.REPORT]


class GenVerPipeline:
    """Drives the phase pipeline for a single GenVer task."""

    def __init__(
        self,
        *,
        config: GenVerConfig,
        provider: LLMProvider,
        workspace: Path,
        generator_tools: ToolRegistry,
        default_model: str,
        temperature: float = 0.1,
        max_tokens: int = 16384,
        add_assistant: AddAssistantFn | None = None,
        add_tool_result: AddToolResultFn | None = None,
        on_progress: ProgressFn | None = None,
        tool_context: Any = None,
        ask_user: AskUserFn | None = None,
    ):
        self.config = config
        self.provider = provider
        self.workspace = workspace
        self.generator_tools = generator_tools
        self.default_model = default_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.add_assistant = add_assistant
        self.add_tool_result = add_tool_result
        self.on_progress = on_progress
        self.tool_context = tool_context
        self.ask_user = ask_user

        self.store = ArtifactStore(workspace / config.workspace_subdir)
        self.last_handoff: Any = None

    @property
    def generator_model(self) -> str:
        return self.config.generator_model or self.default_model

    @property
    def verifier_model(self) -> str:
        return self.config.verifier_model or self.default_model

    def _make_ver_provider(self) -> LLMProvider:
        from src.genver.loop import _make_provider_for_model

        return _make_provider_for_model(self.verifier_model, self.provider)

    def _make_gen_provider(self) -> LLMProvider:
        from src.genver.loop import _make_provider_for_model

        return _make_provider_for_model(self.generator_model, self.provider)

    def _resolve_phases(self, phase_names: list[str]) -> list[Phase]:
        return [Phase(p) for p in phase_names]

    async def run(
        self,
        messages: list[dict],
    ) -> tuple[str | None, list[str], list[dict], dict[str, int]]:
        """Execute the full phase pipeline."""
        start_time = time.monotonic()
        self.store.clear_runtime()

        user_request = self._extract_user_request(messages)
        total_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        all_tools_used: list[str] = []
        phase_artifacts: list[PhaseArtifact] = []
        review_history: list[dict] = []

        # Determine which phases to run. CLARIFY no longer rewrites the active
        # phase list at runtime; phase selection is a preflight decision.
        if self.config.auto_phase_selection:
            complexity = classify_complexity(user_request)
            active_phases = phases_for_complexity(complexity)
            logger.info(
                "[GenVer Pipeline] Auto-selected phases for complexity={}: {}",
                complexity,
                [str(p) for p in active_phases],
            )
        else:
            active_phases = self._resolve_phases(self.config.phases)

        gen_provider = self._make_gen_provider()
        ver_provider = self._make_ver_provider()

        for phase in active_phases:
            if self.on_progress:
                await self.on_progress(f"[GenVer] Starting phase: {phase.value}")

            logger.info("[GenVer Pipeline] === Phase: {} ===", phase.value)

            if phase == Phase.CLARIFY:
                await phase_runners.run_clarify(
                    user_request=user_request,
                    workspace=self.workspace,
                    provider=gen_provider,
                    model=self.generator_model,
                    tools=self.generator_tools,
                    max_iterations=self.config.spec_max_iterations,
                    store=self.store,
                    ask_user=self.ask_user,
                )
                continue

            if phase == Phase.SPEC:
                artifact = await phase_runners.run_spec(
                    user_request=user_request,
                    workspace=self.workspace,
                    gen_provider=gen_provider,
                    ver_provider=ver_provider,
                    gen_model=self.generator_model,
                    ver_model=self.verifier_model,
                    store=self.store,
                    max_iterations=self.config.spec_max_iterations,
                )
                phase_artifacts.append(artifact)
                review_history.extend(r.to_dict() for r in artifact.review_records)
                merge_usage(total_usage, artifact.tokens_used)

            elif phase == Phase.PLAN:
                artifact = await phase_runners.run_plan(
                    user_request=user_request,
                    workspace=self.workspace,
                    gen_provider=gen_provider,
                    ver_provider=ver_provider,
                    gen_model=self.generator_model,
                    ver_model=self.verifier_model,
                    store=self.store,
                    max_iterations=self.config.plan_max_iterations,
                )
                phase_artifacts.append(artifact)
                review_history.extend(r.to_dict() for r in artifact.review_records)
                merge_usage(total_usage, artifact.tokens_used)

            elif phase == Phase.EXECUTE:
                content, tools_used, messages, usage, handoff = await phase_runners.run_execute(
                    user_request=user_request,
                    workspace=self.workspace,
                    provider=self.provider,
                    gen_model=self.generator_model,
                    ver_model=self.verifier_model,
                    generator_tools=self.generator_tools,
                    store=self.store,
                    genver_config=self.config,
                    default_model=self.default_model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    add_assistant=self.add_assistant,
                    add_tool_result=self.add_tool_result,
                    on_progress=self.on_progress,
                    tool_context=self.tool_context,
                    ask_user=self.ask_user,
                    messages=messages,
                )
                self.last_handoff = handoff
                all_tools_used.extend(tools_used)
                merge_usage(total_usage, usage)

            elif phase == Phase.REVIEW:
                artifact = await phase_runners.run_review(
                    user_request=user_request,
                    workspace=self.workspace,
                    gen_provider=gen_provider,
                    ver_provider=ver_provider,
                    gen_model=self.generator_model,
                    ver_model=self.verifier_model,
                    store=self.store,
                    max_iterations=self.config.review_max_iterations,
                    verifier_commands=self.config.verifier_commands,
                    handoff=self.last_handoff,
                )
                phase_artifacts.append(artifact)
                review_history.extend(r.to_dict() for r in artifact.review_records)
                merge_usage(total_usage, artifact.tokens_used)

            elif phase == Phase.REPORT:
                phase_summaries = [
                    {
                        "phase": str(a.phase),
                        "verdict": a.final_verdict.status if a.final_verdict else "n/a",
                    }
                    for a in phase_artifacts
                ]
                artifact = await phase_runners.run_report(
                    user_request=user_request,
                    workspace=self.workspace,
                    provider=gen_provider,
                    model=self.generator_model,
                    tools=self.generator_tools,
                    store=self.store,
                    phase_summaries=phase_summaries,
                    review_history=review_history,
                    verification_result=None,
                )
                phase_artifacts.append(artifact)
                merge_usage(total_usage, artifact.tokens_used)

            # Abort-check: if REVIEW returned abort, stop before REPORT.
            if (
                phase == Phase.REVIEW
                and artifact
                and artifact.final_verdict
                and artifact.final_verdict.status == "abort"
            ):
                logger.warning("[GenVer Pipeline] REVIEW returned abort, stopping pipeline")
                break

        elapsed = time.monotonic() - start_time
        total_usage["wall_time_seconds"] = int(elapsed)

        # Build final response
        report = self.store.read_artifact("report.md")
        final_content = report or (messages[-1].get("content", "") if messages else "")

        return final_content, all_tools_used, messages, total_usage

    @staticmethod
    def _extract_user_request(messages: list[dict]) -> str:
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, str) and not content.startswith("[GenVer"):
                    return content
        return ""
