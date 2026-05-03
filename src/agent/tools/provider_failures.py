"""Helpers for turning provider failures into user-safe tool messages."""

from __future__ import annotations

from src.providers.errors import FailureClass, classify_failure


def get_user_safe_provider_error(exc: BaseException, *, action: str) -> str | None:
    """Return a stable user-facing message for provider failures when possible."""
    if classify_failure(exception=exc) is not FailureClass.AUTH:
        return None
    return (
        f"Error: {action} is temporarily unavailable because the configured model "
        "credential is invalid or expired. Re-authenticate the provider or switch "
        "to a valid model credential."
    )
