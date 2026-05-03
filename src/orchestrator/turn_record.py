"""Always-on lightweight turn record for telemetry and correlation."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TurnRecord:
    """Tracks a single message turn through the agent pipeline.

    Every message gets one regardless of whether orchestrator policies are enabled.
    """

    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_key: str = ""
    status: str = "created"
    started_at: datetime = field(default_factory=datetime.now)
    created_at: float = field(default_factory=time.monotonic)
    duration_ms: float | None = None
    error: str | None = None
