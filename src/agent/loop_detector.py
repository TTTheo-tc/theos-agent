"""Detect repeated identical tool calls across recent iterations."""

from __future__ import annotations

import json


class LoopDetector:
    """Sliding-window detector for repetitive tool-call patterns.

    Records tool-call signatures after execution. Before the next iteration,
    call ``check()`` to see if the threshold has been reached.

    Also tracks permission/autonomy *denial* counts per tool name so the
    agent can be redirected when it keeps hitting the same wall.
    """

    def __init__(self, window: int = 10, threshold: int = 3) -> None:
        self._window = window
        self._threshold = threshold
        self._history: list[str] = []
        self._denial_counts: dict[str, int] = {}

    def record(self, name: str, arguments: dict) -> None:
        """Record a tool call signature after execution."""
        sig = self._signature(name, arguments)
        self._history.append(sig)
        if len(self._history) > self._window:
            self._history = self._history[-self._window :]

    def check(self) -> str | None:
        """Check if any signature has hit the threshold.

        Returns the repeated tool name if detected, None otherwise.
        """
        if len(self._history) < self._threshold:
            return None
        # Count occurrences of the most recent signature
        last = self._history[-1]
        count = sum(1 for s in self._history if s == last)
        if count >= self._threshold:
            # Extract tool name from signature
            return last.split(":", 1)[0]
        return None

    def reset(self) -> None:
        """Reset after injecting a break message."""
        self._history.clear()

    # -- Denial tracking -----------------------------------------------------

    def record_denial(self, name: str) -> None:
        """Record that *name* was denied by autonomy/approval."""
        self._denial_counts[name] = self._denial_counts.get(name, 0) + 1

    def check_denials(self, threshold: int = 3) -> str | None:
        """Return a tool name if it has been denied >= *threshold* times."""
        for name, count in self._denial_counts.items():
            if count >= threshold:
                return name
        return None

    def reset_denial(self, name: str) -> None:
        """Reset denial counter for a single tool (e.g. after user re-request)."""
        self._denial_counts.pop(name, None)

    def reset_all_denials(self) -> None:
        """Reset all denial counters."""
        self._denial_counts.clear()

    @staticmethod
    def _signature(name: str, arguments: dict) -> str:
        return f"{name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
