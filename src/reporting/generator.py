"""Report generator — turns metrics into markdown reports."""

from __future__ import annotations

from datetime import datetime
from typing import Any


class ReportGenerator:
    """Generate markdown reports from collected metrics."""

    @staticmethod
    def render(metrics: dict[str, Any], *, title: str = "TheOS Report") -> str:
        """Render a metrics dict into a markdown report string."""
        time_range = metrics.get("time_range", {})
        since = time_range.get("since", "N/A")
        until = time_range.get("until", "N/A")

        total = metrics.get("total_tasks", 0)
        completed = metrics.get("completed", 0)
        failed = metrics.get("failed", 0)
        retried = metrics.get("retried", 0)
        retry_rate = metrics.get("retry_rate", 0)
        sessions = metrics.get("sessions_active", 0)
        events = metrics.get("events_by_type", {})

        success_rate = (completed / total * 100) if total > 0 else 0

        lines = [
            f"# {title}",
            "",
            f"**Period:** {since} — {until}",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total tasks | {total} |",
            f"| Completed | {completed} |",
            f"| Failed | {failed} |",
            f"| Success rate | {success_rate:.1f}% |",
            f"| Retried | {retried} |",
            f"| Retry rate | {retry_rate:.1%} |",
            f"| Active sessions | {sessions} |",
            "",
        ]

        if events:
            lines.append("## Events by Type")
            lines.append("")
            lines.append("| Event Type | Count |")
            lines.append("|------------|-------|")
            for etype, count in sorted(events.items(), key=lambda x: -x[1]):
                lines.append(f"| {etype} | {count} |")
            lines.append("")

        lines.append(f"---\n*Generated at {datetime.now().isoformat(timespec='seconds')}*")
        return "\n".join(lines)
