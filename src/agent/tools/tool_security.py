"""Shared policy helpers for tool execution gates."""

from __future__ import annotations

from pathlib import Path

from src.safety.policy import PolicyAction, PolicyEngine
from src.utils.path import resolve_path

_POLICY = PolicyEngine()


def policy_error(text: str, *, kind: str) -> str | None:
    """Return a user-facing policy error, or ``None`` when the action is allowed."""
    result = _POLICY.evaluate(text)
    if result.should_block:
        rule_ids = ", ".join(
            v.rule_id for v in result.violations if v.action == PolicyAction.BLOCK
        )
        return f"Error: {kind} blocked by security policy ({rule_ids})"
    if result.needs_review:
        rule_ids = ", ".join(
            v.rule_id for v in result.violations if v.action == PolicyAction.REVIEW
        )
        return f"Error: {kind} requires human review by security policy ({rule_ids})"
    return None


def resolve_policy_path(
    target: str,
    workspace: Path | None,
    allowed_dir: Path | None,
    *,
    kind: str,
) -> tuple[Path | None, str | None]:
    """Resolve a tool path after checking both raw and resolved policy surfaces."""
    raw_policy_error = policy_error(target, kind=kind)
    if raw_policy_error:
        return None, raw_policy_error
    try:
        resolved = resolve_path(target, workspace, allowed_dir)
    except PermissionError as e:
        return None, f"Error: {e}"
    resolved_policy_error = policy_error(str(resolved), kind=kind)
    if resolved_policy_error:
        return None, resolved_policy_error
    return resolved, None
