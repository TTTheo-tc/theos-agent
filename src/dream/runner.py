"""Dream session runner — sandboxed exploration via LLM + tool loop."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from src.dream.output.artifacts import ArtifactTracker
from src.dream.output.diary_publisher import publish_diary_entry
from src.dream.output.dream_eval import DreamEval
from src.dream.output.dream_review import write_review
from src.dream.output.narrative import write_narrative
from src.dream.sandbox.tool_policy import DreamToolPolicy
from src.dream.tool_registry import DreamToolRegistry

# Rough cost rates for post-run LLM cost estimation ($/million tokens).
_INPUT_COST_PER_M = 15.0
_OUTPUT_COST_PER_M = 75.0


class DreamResult:
    """Result of a completed dream session."""

    def __init__(
        self,
        session_id: str,
        output_dir: Path,
        eval_data: DreamEval,
        narrative_path: Path | None = None,
        review_path: Path | None = None,
    ) -> None:
        self.session_id = session_id
        self.output_dir = output_dir
        self.eval = eval_data
        self.narrative_path = narrative_path
        self.review_path = review_path


class DreamRunner:
    """Execute a sandboxed dream exploration session.

    Dream is NOT a more powerful agent — it's a more restricted, longer-running,
    but default-untrusted sandboxed explorer.
    """

    def __init__(
        self,
        workspace: Path,
        topic: str,
        provider: Any,
        base_registry: Any,
        model: str = "",
        budget_usd: float = 30.0,
        max_web_queries: int = 50,
        max_iterations: int = 20,
        seed_sources: list[str] | None = None,
    ) -> None:
        self.workspace = workspace
        self.topic = topic
        self.provider = provider
        self.base_registry = base_registry
        self.model = model or getattr(provider, "default_model", "")
        self.budget_usd = budget_usd
        self.max_iterations = max_iterations
        ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        self.session_id = f"dream-{ts}"
        self.output_dir = workspace / "memory" / "instinct" / "dreams" / self.session_id
        self.sandbox_root = self.output_dir / "sandbox"
        self.seed_sources = seed_sources or ["events", "history"]

        self.policy = DreamToolPolicy(
            sandbox_root=self.sandbox_root,
            budget_usd=budget_usd,
            max_web_queries=max_web_queries,
        )
        self.artifacts = ArtifactTracker(self.output_dir)
        self._eval = DreamEval(
            session_id=self.session_id,
            topic=topic,
            seed_sources=self.seed_sources,
            budget_usd_cap=budget_usd,
        )

    async def run(self) -> DreamResult:
        """Execute the dream exploration. Returns result without auto-injecting."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.sandbox_root.mkdir(parents=True, exist_ok=True)

        seeds: list[str] = []
        final_content: str | None = None
        usage: dict[str, int] = {}

        try:
            seeds = await self._gather_seeds()

            system_prompt = self._build_system_prompt(seeds)
            messages: list[dict] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Explore this topic: {self.topic}"},
            ]

            dream_registry = DreamToolRegistry(
                base=self.base_registry,
                policy=self.policy,
                eval_tracker=self._eval,
                artifacts=self.artifacts,
                sandbox_root=self.sandbox_root,
            )

            from src.agent.loop_core import run_tool_loop

            final_content, tools_used, messages, usage = await run_tool_loop(
                provider=self.provider,
                messages=messages,
                tools=dream_registry,
                model=self.model,
                temperature=0.7,
                max_tokens=4096,
                max_iterations=self.max_iterations,
            )

            logger.info(
                "Dream session {} finished: tools_used={}, iterations={}",
                self.session_id,
                len(tools_used),
                len([m for m in messages if m.get("role") == "assistant"]),
            )

            # Map stop_reason to declared status values
            # (completed|budget_exceeded|loop_guard_stopped|failed)
            stop = self.policy.stop_reason
            if not stop:
                self._eval.status = "completed"
            elif stop in ("budget_exceeded", "loop_guard_stopped"):
                self._eval.status = stop
            else:
                self._eval.status = "failed"

        except Exception as e:
            self._eval.status = "failed"
            logger.error("Dream session {} failed: {}", self.session_id, e)

        # Extract findings/insights from LLM final response
        findings, insights = self._parse_response(final_content or "")

        # Populate eval from policy stats + LLM usage
        stats = self.policy.stats
        self._eval.tool_calls = stats["total_calls"]
        self._eval.web_queries = stats["web_queries"]
        self._eval.budget_usd_used = stats["cost_used"]
        self._eval.artifacts_count = len(self.artifacts.entries)

        # Post-run LLM cost estimation (reporting only, not in budget guard)
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        llm_cost = (
            prompt_tokens * _INPUT_COST_PER_M / 1_000_000
            + completion_tokens * _OUTPUT_COST_PER_M / 1_000_000
        )
        self._eval.budget_usd_used += llm_cost
        self._eval.narrative_tokens = completion_tokens

        # Write outputs
        self._eval.write(self.output_dir)

        narrative_path = write_narrative(
            output_dir=self.output_dir,
            topic=self.topic,
            seeds=seeds,
            findings=findings,
            insights=insights,
        )

        review_path = write_review(
            output_dir=self.output_dir,
            eval_data=self._eval,
            topic=self.topic,
            artifacts=self.artifacts.entries,
        )

        self.artifacts.write_manifest()

        publish_diary_entry(
            workspace=self.workspace,
            session_id=self.session_id,
            topic=self.topic,
            status=self._eval.status,
        )

        # Append to DREAM_INDEX.jsonl
        self._write_index_entry(findings, insights)

        return DreamResult(
            session_id=self.session_id,
            output_dir=self.output_dir,
            eval_data=self._eval,
            narrative_path=narrative_path,
            review_path=review_path,
        )

    def _build_system_prompt(self, seeds: list[str]) -> str:
        seed_text = "\n".join(f"- {s}" for s in seeds) if seeds else "- (no seeds)"
        return (
            "You are a dream explorer. Your task is to deeply investigate the "
            "following topic through research, analysis, and experimentation.\n\n"
            f"## Topic\n{self.topic}\n\n"
            f"## Seed Material\n{seed_text}\n\n"
            "## Constraints\n"
            "- All file writes must be under the sandbox directory.\n"
            "- Focus on generating actionable insights and artifacts.\n"
            "- Label each insight as [Dream hypothesis] or [Unverified exploration].\n"
            "- When you have exhausted productive exploration, summarize your findings.\n\n"
            "## Output Format\n"
            "End your final message with a structured summary:\n"
            "### Findings\n- ...\n"
            "### Insights\n- [Dream hypothesis] ...\n"
            "### Artifacts\n- ...\n"
        )

    @staticmethod
    def _parse_response(content: str) -> tuple[list[str], list[str]]:
        """Extract findings and insights from the LLM's final response."""
        findings: list[str] = []
        insights: list[str] = []
        current: list[str] | None = None

        for line in content.split("\n"):
            stripped = line.strip()
            if re.match(r"^###?\s+Findings", stripped, re.IGNORECASE):
                current = findings
            elif re.match(r"^###?\s+Insights", stripped, re.IGNORECASE):
                current = insights
            elif re.match(r"^###?\s+Artifacts", stripped, re.IGNORECASE):
                current = None
            elif current is not None and stripped.startswith("- "):
                current.append(stripped[2:].strip())

        return findings, insights

    def _write_index_entry(self, findings: list[str], insights: list[str]) -> None:
        """Append an entry to DREAM_INDEX.jsonl."""
        index_path = self.workspace / "memory" / "instinct" / "DREAM_INDEX.jsonl"
        index_path.parent.mkdir(parents=True, exist_ok=True)

        summary = findings[0] if findings else f"Dream exploration: {self.topic}"
        entry = {
            "session_id": self.session_id,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "topic": self.topic,
            "tags": [],
            "summary": summary,
            "insights": insights[:5],
            "review_path": str(self.output_dir.relative_to(self.workspace) / "dream-review.md"),
            "eval_path": str(self.output_dir.relative_to(self.workspace) / "dream_eval.json"),
            "reflux_level": "L1",
            "status": self._eval.status,
            "reviewed_by_user": False,
        }

        with open(index_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def _gather_seeds(self) -> list[str]:
        """Gather seed material from configured sources."""
        seeds: list[str] = []
        events_dir = self.workspace / "memory" / "instinct" / "events"
        if events_dir.exists() and "events" in self.seed_sources:
            for f in sorted(events_dir.iterdir())[-20:]:
                if f.suffix == ".json":
                    try:
                        data = json.loads(f.read_text())
                        summary = data.get("request", {}).get("intent_summary", "")
                        if summary:
                            seeds.append(summary)
                    except Exception:
                        pass
        return seeds
