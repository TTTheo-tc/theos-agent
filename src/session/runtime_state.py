"""Helpers for summarizing durable session runtime/recovery state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.session.subagent_store import SubagentCheckpoint, SubagentStore
    from src.session.turn_store import TurnCheckpoint, TurnStore

_RECOVERABLE_TURN_STATUSES = frozenset(
    {
        "accepted",
        "building_context",
        "inferring",
        "waiting_user",
        "finalizing",
        "failed",
        "interrupted",
    }
)


@dataclass
class SessionRuntimeState:
    """Combined durable recovery state for one session."""

    session_key: str
    latest_turn: TurnCheckpoint | None
    active_background: list[SubagentCheckpoint]
    recent_background: list[SubagentCheckpoint]
    recoverable: bool
    runtime_state: str | None
    next_step: str | None


def build_session_runtime_state(
    session_key: str,
    *,
    turn_store: TurnStore | None = None,
    subagent_store: SubagentStore | None = None,
    recent_background_limit: int = 3,
) -> SessionRuntimeState:
    """Build a factual recovery summary from durable turn/background state."""
    latest_turn = turn_store.latest(session_key) if turn_store is not None else None
    active_background = (
        subagent_store.active_for_session(session_key) if subagent_store is not None else []
    )
    recent_background = (
        subagent_store.latest_for_session(session_key, limit=recent_background_limit)
        if subagent_store is not None
        else []
    )

    runtime_state = _runtime_state_label(latest_turn, active_background)
    recoverable = bool(
        active_background or (latest_turn and latest_turn.status in _RECOVERABLE_TURN_STATUSES)
    )

    return SessionRuntimeState(
        session_key=session_key,
        latest_turn=latest_turn,
        active_background=active_background,
        recent_background=recent_background,
        recoverable=recoverable,
        runtime_state=runtime_state,
        next_step=infer_resume_next_step(latest_turn, active_background),
    )


def infer_resume_next_step(
    checkpoint: TurnCheckpoint | None,
    active_background: list[SubagentCheckpoint],
) -> str | None:
    """Suggest the narrowest factual next step based on durable state."""
    if checkpoint is None:
        if active_background:
            return "Inspect the active background tasks before starting a new turn."
        return None
    if checkpoint.status == "waiting_user":
        return "Reply in the same session to answer the pending clarification."
    if checkpoint.status == "interrupted":
        interrupted_from = checkpoint.metadata.get("interrupted_from")
        if interrupted_from == "waiting_user":
            return "Restate the answer to the pending clarification or restart the task."
        if active_background:
            return "Inspect the recent background tasks before re-running the interrupted request."
        return "Re-send the last request if you want to retry from the interruption point."
    if checkpoint.status == "failed":
        return "Inspect the recorded error and decide whether to retry or start a new turn."
    if checkpoint.status in {"accepted", "building_context", "inferring", "finalizing"}:
        if active_background:
            return "Inspect the active background tasks before retrying the interrupted turn."
        return "Re-send the last request if the in-flight turn did not finish."
    if active_background:
        return "Inspect the active background tasks before starting a new turn."
    return None


def _runtime_state_label(
    checkpoint: TurnCheckpoint | None,
    active_background: list[SubagentCheckpoint],
) -> str | None:
    if checkpoint is not None:
        return checkpoint.status
    if active_background:
        return "background_active"
    return None
