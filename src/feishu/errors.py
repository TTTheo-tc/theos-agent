"""Feishu API error types."""

from __future__ import annotations

from typing import Any


class FeishuAPIError(Exception):
    """Structured error from a Feishu API response.

    Carries the numeric error code and optional raw response so that
    retry/classification logic can inspect them without string parsing.
    """

    def __init__(self, message: str, code: int | None = None, response: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.response = response
