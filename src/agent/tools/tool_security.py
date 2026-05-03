"""Shared policy helpers for tool execution gates."""

from __future__ import annotations

from src.safety.policy import PolicyEngine

_POLICY = PolicyEngine()


def policy_error(text: str, *, kind: str) -> str | None:
    """Return a user-facing policy error, or ``None`` when the action is allowed."""
    result = _POLICY.evaluate(text)
    if result.should_block:
        rule_ids = ", ".join(v.rule_id for v in result.violations if v.action.value == "block")
        return f"Error: {kind} blocked by security policy ({rule_ids})"
    if result.needs_review:
        rule_ids = ", ".join(v.rule_id for v in result.violations if v.action.value == "review")
        return f"Error: {kind} requires human review by security policy ({rule_ids})"
    return None
