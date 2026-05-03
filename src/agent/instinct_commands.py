"""Handler for /instinct slash commands.

Commands:
- /instinct status       — Show rule counts, last evolve, dream status
- /instinct evolve-run   — Trigger evolve.js manually
- /instinct evolve-preview — Dry-run evolve.js
- /instinct dream-run [--topic "..."] [--budget-usd N] — Run dream session
- /instinct dream-review [date] — Show dream review
- /instinct dream-apply <session-id> <artifact> — Apply dream artifact
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from src.bus.events import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from src.agent.loop import AgentLoop


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EVOLVE_SCRIPT = REPO_ROOT / "instinct" / "scripts" / "evolve.js"


def _workspace(loop: "AgentLoop") -> Path:
    return getattr(loop, "workspace", Path.home() / ".theos" / "workspace")


def _sync_index_reviewed(workspace: Path, session_id: str) -> None:
    """Update reviewed_by_user in DREAM_INDEX.jsonl for a session."""
    index_path = workspace / "memory" / "instinct" / "DREAM_INDEX.jsonl"
    if not index_path.exists():
        return
    try:
        lines = index_path.read_text().splitlines()
        updated = []
        for line in lines:
            if not line.strip():
                updated.append(line)
                continue
            try:
                entry = json.loads(line)
                if entry.get("session_id") == session_id:
                    entry["reviewed_by_user"] = True
                updated.append(json.dumps(entry, ensure_ascii=False))
            except json.JSONDecodeError:
                updated.append(line)
        index_path.write_text("\n".join(updated) + "\n")
    except Exception:
        logger.opt(exception=True).warning("Failed to sync DREAM_INDEX.jsonl for {}", session_id)


async def handle_instinct_command(loop: "AgentLoop", msg: InboundMessage) -> OutboundMessage | None:
    """Dispatch /instinct subcommands."""
    parts = msg.content.strip().split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else "help"

    if sub == "status":
        return _handle_status(loop, msg)
    elif sub == "evolve-run":
        return _handle_evolve_run(loop, msg, dry_run=False)
    elif sub == "evolve-preview":
        return _handle_evolve_run(loop, msg, dry_run=True)
    elif sub == "dream-run":
        return await _handle_dream_run(loop, msg)
    elif sub == "dream-review":
        return _handle_dream_review(loop, msg, parts)
    elif sub == "dream-apply":
        return _handle_dream_apply(loop, msg, parts)
    else:
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=_usage_text(),
        )


def _usage_text() -> str:
    return (
        "Available /instinct commands:\n"
        "- /instinct status — Show active/probation rule counts and dream status\n"
        "- /instinct evolve-run — Trigger rule evolution manually\n"
        "- /instinct evolve-preview — Preview what evolve would do (dry-run)\n"
        '- /instinct dream-run [--topic "..."] [--budget-usd N] — Run a dream session\n'
        "- /instinct dream-review [date] — Show dream review for date\n"
        "- /instinct dream-apply <session-id> <artifact> — Apply a dream artifact"
    )


# ── status ──────────────────────────────────────────────────────────


def _handle_status(loop: "AgentLoop", msg: InboundMessage) -> OutboundMessage:
    ws = _workspace(loop)
    instinct_dir = ws / "memory" / "instinct"
    rules_dir = instinct_dir / "rules"
    dreams_dir = instinct_dir / "dreams"

    # Count active rules
    active_count = 0
    active_path = rules_dir / "ACTIVE.md"
    if active_path.exists():
        active_count = sum(
            1 for line in active_path.read_text().splitlines() if line.startswith("- [")
        )

    # Count probation rules
    probation_count = 0
    probation_path = rules_dir / "PROBATION.md"
    if probation_path.exists():
        probation_count = sum(
            1 for line in probation_path.read_text().splitlines() if line.startswith("- [")
        )

    # Last evolve time
    index_path = rules_dir / "index.json"
    last_evolved = "never"
    if index_path.exists():
        try:
            idx = json.loads(index_path.read_text())
            last_evolved = idx.get("last_evolved", "never")
        except Exception:
            pass

    # Dream sessions count
    dream_count = 0
    if dreams_dir.exists():
        dream_count = sum(1 for d in dreams_dir.iterdir() if d.is_dir())

    # Events count
    events_dir = instinct_dir / "events"
    event_count = 0
    if events_dir.exists():
        event_count = sum(1 for f in events_dir.iterdir() if f.suffix == ".json")

    # Live rules count
    live_rules_path = instinct_dir / "live_rules.jsonl"
    live_rules_count = 0
    if live_rules_path.exists():
        live_rules_count = sum(
            1 for line in live_rules_path.read_text().splitlines() if line.strip()
        )

    content = (
        "Instinct Status:\n"
        f"- Active rules: {active_count}\n"
        f"- Probation rules: {probation_count}\n"
        f"- Live rule candidates: {live_rules_count}\n"
        f"- Events: {event_count}\n"
        f"- Dream sessions: {dream_count}\n"
        f"- Last evolved: {last_evolved}"
    )
    return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content)


# ── evolve ──────────────────────────────────────────────────────────


def _handle_evolve_run(loop: "AgentLoop", msg: InboundMessage, *, dry_run: bool) -> OutboundMessage:
    if not EVOLVE_SCRIPT.exists():
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=f"evolve.js not found at {EVOLVE_SCRIPT}",
        )

    cmd = ["node", str(EVOLVE_SCRIPT)]
    if dry_run:
        cmd.append("--dry-run")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "INSTINCT_EVOLVE_MIN_INTERVAL_SECONDS": "0"},
        )
        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        output = "evolve.js timed out after 30s"
    except Exception as e:
        output = f"Failed to run evolve.js: {e}"

    prefix = "Evolve preview" if dry_run else "Evolve run"
    return OutboundMessage(
        channel=msg.channel,
        chat_id=msg.chat_id,
        content=f"{prefix} result:\n```\n{output}\n```",
    )


# ── dream-run ───────────────────────────────────────────────────────


async def _handle_dream_run(loop: "AgentLoop", msg: InboundMessage) -> OutboundMessage:
    rest = msg.content.strip()
    topic = "general exploration"
    budget = 30.0

    m = re.search(r'--topic\s+"([^"]+)"', rest)
    if m:
        topic = m.group(1)
    m = re.search(r"--budget-usd\s+(\d+(?:\.\d+)?)", rest)
    if m:
        budget = float(m.group(1))

    try:
        from src.dream.runner import DreamRunner

        runner = DreamRunner(
            workspace=_workspace(loop),
            topic=topic,
            provider=loop.provider,
            base_registry=loop.tools,
            model=loop.model,
            budget_usd=budget,
        )
        result = await runner.run()
        content = (
            "Dream session completed:\n"
            f"- Session: {result.session_id}\n"
            f"- Topic: {topic}\n"
            f"- Status: {result.eval.status}\n"
            f"- Tool calls: {result.eval.tool_calls}\n"
            f"- Budget used: ${result.eval.budget_usd_used:.2f} / ${budget:.2f}\n"
            f"- Artifacts: {result.eval.artifacts_count}\n"
            f"- Output: {result.output_dir}\n\n"
            f"Use `/instinct dream-review {result.session_id}` to view the review."
        )
    except Exception as e:
        logger.opt(exception=True).error("Dream run failed")
        content = f"Dream run failed: {e}"

    return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content)


# ── dream-review ────────────────────────────────────────────────────


def _handle_dream_review(
    loop: "AgentLoop", msg: InboundMessage, parts: list[str]
) -> OutboundMessage:
    ws = _workspace(loop)
    dreams_dir = ws / "memory" / "instinct" / "dreams"

    if not dreams_dir.exists():
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="No dream sessions found.",
        )

    target = parts[2] if len(parts) > 2 else None

    if target:
        for d in dreams_dir.iterdir():
            if d.is_dir() and target in d.name:
                review_path = d / "dream-review.md"
                if review_path.exists():
                    return OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=review_path.read_text()[:4000],
                    )
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=f"Review not found for session {d.name}",
                )
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=f"No dream session matching '{target}' found.",
        )

    # List recent sessions
    sessions = sorted((d for d in dreams_dir.iterdir() if d.is_dir()), reverse=True)[:10]
    if not sessions:
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="No dream sessions found.",
        )

    lines = ["Recent dream sessions:"]
    for d in sessions:
        eval_path = d / "dream_eval.json"
        status = "unknown"
        topic = ""
        if eval_path.exists():
            try:
                data = json.loads(eval_path.read_text())
                status = data.get("status", "unknown")
                topic = data.get("topic", "")
            except Exception:
                pass
        lines.append(f"- {d.name}: {status}" + (f" — {topic}" if topic else ""))

    return OutboundMessage(
        channel=msg.channel,
        chat_id=msg.chat_id,
        content="\n".join(lines),
    )


# ── dream-apply ─────────────────────────────────────────────────────


def _handle_dream_apply(
    loop: "AgentLoop", msg: InboundMessage, parts: list[str]
) -> OutboundMessage:
    # Re-split with enough maxsplit for session-id and artifact
    full_parts = msg.content.strip().split(maxsplit=3)
    if len(full_parts) < 4:
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="Usage: /instinct dream-apply <session-id> <artifact>",
        )

    session_id = full_parts[2]
    artifact = full_parts[3]

    ws = _workspace(loop)
    dreams_dir = ws / "memory" / "instinct" / "dreams"

    target_dir = None
    if dreams_dir.exists():
        for d in dreams_dir.iterdir():
            if d.is_dir() and session_id in d.name:
                target_dir = d
                break

    if not target_dir:
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=f"Session '{session_id}' not found.",
        )

    # Verify artifact exists in sandbox
    sandbox_dir = target_dir / "sandbox"
    artifact_path = sandbox_dir / artifact if sandbox_dir.exists() else target_dir / artifact
    if not artifact_path.exists():
        # Also check directly under session dir
        alt_path = target_dir / artifact
        if alt_path.exists():
            artifact_path = alt_path
        else:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=(
                    f"Artifact '{artifact}' not found in session {session_id}.\n"
                    f"Searched: {sandbox_dir}, {target_dir}"
                ),
            )

    eval_path = target_dir / "dream_eval.json"
    if eval_path.exists():
        try:
            data = json.loads(eval_path.read_text())
            applied = data.get("applied_artifacts", [])
            if artifact not in applied:
                applied.append(artifact)
                data["applied_artifacts"] = applied
                data["reviewed_by_user"] = True
                eval_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        except Exception as e:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=f"Failed to update eval: {e}",
            )

    # Sync reviewed_by_user back to DREAM_INDEX.jsonl so L1 scoring picks it up
    _sync_index_reviewed(ws, session_id)

    return OutboundMessage(
        channel=msg.channel,
        chat_id=msg.chat_id,
        content=(
            f"Artifact '{artifact}' marked as applied for session {session_id}.\n"
            f"Path: {artifact_path}\n"
            "Review the artifact and integrate manually."
        ),
    )
