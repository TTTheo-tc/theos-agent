"""Ask-user tool for Generator requirement clarification."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from src.agent.tools.base import Tool

AskUserFn = Callable[[str], Awaitable[str | None]]


class AskUserTool(Tool):
    """Lets the Generator ask the user a clarification question."""

    def __init__(self, ask_fn: AskUserFn | None = None):
        self._ask_fn = ask_fn

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return (
            "Ask the user a clarification question about their requirements. "
            "Use this when the request is ambiguous, missing details, or self-contradictory. "
            "Do NOT use this to report progress — only for genuine questions."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The clarification question to ask the user.",
                },
            },
            "required": ["question"],
        }

    async def execute(self, **kwargs: Any) -> str:
        question = kwargs.get("question", "")
        if not question:
            return "(empty question — skipped)"
        if self._ask_fn:
            answer = await self._ask_fn(question)
            return answer or "(user did not respond)"
        return "(ask_user not available)"
