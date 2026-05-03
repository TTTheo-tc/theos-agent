"""Dream session evaluation writer."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class DreamEval:
    """Evaluation data for a dream session."""

    session_id: str
    topic: str
    seed_sources: list[str] = field(default_factory=list)
    budget_usd_cap: float = 30.0
    budget_usd_used: float = 0.0
    tool_calls: int = 0
    web_queries: int = 0
    artifacts_count: int = 0
    narrative_tokens: int = 0
    status: str = "completed"  # completed|budget_exceeded|loop_guard_stopped|failed
    reviewed_by_user: bool = False
    applied_artifacts: list[str] = field(default_factory=list)
    verified_insights: list[str] = field(default_factory=list)
    next_day_retrieval_hits: int = 0
    seven_day_reuse_hits: int = 0

    def write(self, output_dir: Path) -> Path:
        """Write eval data to output directory."""
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "dream_eval.json"
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n")
        return path
