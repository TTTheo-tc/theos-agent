"""Hook for reflex.js dream injection.

Stub for future integration. Disabled by default —
returns None when INSTINCT_DREAM_INJECT != "true".
"""

from __future__ import annotations

import os
from typing import Any

DREAM_INJECT_ENABLED = os.environ.get("INSTINCT_DREAM_INJECT", "false") == "true"


def get_dream_injection(intent: str) -> dict[str, Any] | None:
    """Get dream content to inject into reflex context.

    Returns None when injection is disabled (default).
    """
    if not DREAM_INJECT_ENABLED:
        return None
    # Future: match intent against dream insights and return
    # relevant context for injection into the reflex pipeline.
    return None


def should_inject_dream(domains: list[str]) -> bool:
    """Check if dream content should be injected for the given domains.

    Returns False when injection is disabled (default).
    """
    if not DREAM_INJECT_ENABLED:
        return False
    # Future: check if any dream sessions have relevant content
    # for the specified domains.
    return False
