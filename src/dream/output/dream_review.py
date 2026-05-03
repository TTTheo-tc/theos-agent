"""Dream review document generator.

Produces a human-readable dream-review.md summarizing the dream session
for user review and action decisions (apply/discard).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.dream.output.artifacts import ArtifactEntry
from src.dream.output.dream_eval import DreamEval


def write_review(
    *,
    output_dir: Path,
    eval_data: DreamEval,
    topic: str,
    artifacts: list[ArtifactEntry],
    findings: list[str] | None = None,
    insights: list[str] | None = None,
) -> Path:
    """Generate dream-review.md for human review.

    Returns:
        Path to the written review file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "dream-review.md"
    findings = findings or []
    insights = insights or []

    lines: list[str] = []
    lines.append(f"# Dream Review: {topic}")
    lines.append("")
    lines.append(f"Session: `{eval_data.session_id}`")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Status: **{eval_data.status}**")
    lines.append("")

    # What was explored
    lines.append("## Exploration Summary")
    lines.append("")
    lines.append(f"Topic: {topic}")
    lines.append(f"Seed sources: {', '.join(eval_data.seed_sources) or 'none'}")
    lines.append("")

    # Key findings
    lines.append("## Key Findings")
    lines.append("")
    if findings:
        for f in findings:
            lines.append(f"- {f}")
    else:
        lines.append("- (no findings recorded)")
    lines.append("")

    # Insights
    if insights:
        lines.append("## Insights")
        lines.append("")
        for i in insights:
            if not any(tag in i for tag in ("[Dream hypothesis]", "[Unverified exploration]")):
                i = f"[Dream hypothesis] {i}"
            lines.append(f"- {i}")
        lines.append("")

    # Artifacts
    lines.append("## Artifacts Produced")
    lines.append("")
    if artifacts:
        for a in artifacts:
            lines.append(f"- `{a.path}` ({a.type}) — {a.description or 'no description'}")
    else:
        lines.append("- (no artifacts)")
    lines.append("")

    # Suggested actions
    lines.append("## Suggested Actions")
    lines.append("")
    if artifacts:
        for a in artifacts:
            lines.append(f"- [ ] Review and apply/discard: `{a.path}`")
    else:
        lines.append("- [ ] Review narrative for useful insights")
    lines.append("")

    # Eval summary
    lines.append("## Eval Summary")
    lines.append("")
    lines.append(f"- Tool calls: {eval_data.tool_calls}")
    lines.append(f"- Web queries: {eval_data.web_queries}")
    lines.append(f"- Budget: ${eval_data.budget_usd_used:.2f} / ${eval_data.budget_usd_cap:.2f}")
    lines.append(f"- Artifacts: {eval_data.artifacts_count}")
    lines.append("")

    path.write_text("\n".join(lines))
    return path
