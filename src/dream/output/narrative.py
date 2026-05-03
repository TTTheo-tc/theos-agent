"""Dream narrative markdown writer.

Writes exploration narratives to memory/instinct/dreams/<session>/narrative.md.
All insights are labeled [Dream hypothesis] or [Unverified exploration].
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def write_narrative(
    *,
    output_dir: Path,
    topic: str,
    seeds: list[str],
    findings: list[str],
    insights: list[str],
) -> Path:
    """Write dream narrative markdown to the output directory.

    Args:
        output_dir: Dream session output directory.
        topic: Exploration topic.
        seeds: Seed material summaries.
        findings: Key findings (bullet points).
        insights: Insights — each is labeled as hypothesis/unverified.

    Returns:
        Path to the written narrative file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "narrative.md"

    lines: list[str] = []
    lines.append(f"# Dream Narrative: {topic}")
    lines.append("")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # Seed sources
    lines.append("## Seed Sources")
    lines.append("")
    if seeds:
        for seed in seeds:
            lines.append(f"- {seed}")
    else:
        lines.append("- (no seed material)")
    lines.append("")

    # Key findings
    lines.append("## Key Findings")
    lines.append("")
    if findings:
        for finding in findings:
            lines.append(f"- {finding}")
    else:
        lines.append("- (no findings recorded)")
    lines.append("")

    # Insights — must be labeled per v1.1 spec
    lines.append("## Insights")
    lines.append("")
    if insights:
        for insight in insights:
            # Ensure labeling
            if not any(
                tag in insight for tag in ("[Dream hypothesis]", "[Unverified exploration]")
            ):
                insight = f"[Dream hypothesis] {insight}"
            lines.append(f"- {insight}")
    else:
        lines.append("- (no insights generated)")
    lines.append("")

    # Questions raised
    lines.append("## Questions Raised")
    lines.append("")
    lines.append("- (to be populated by dream session)")
    lines.append("")

    path.write_text("\n".join(lines))
    return path
