# Code Style Guide

Based on [Karpathy Guidelines](https://x.com/karpathy/status/2015883857489522876): minimum code that solves the problem, nothing speculative.

## Principles

1. **Simplicity first** — no features beyond what was asked, no abstractions for single-use code
2. **Surgical changes** — touch only what you must, match existing style
3. **Explicit assumptions** — if uncertain, ask; don't guess silently
4. **Verifiable success** — every change has a test or verifiable check

## Imports

```python
from __future__ import annotations

# 1. stdlib
import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

# 2. third-party
from loguru import logger

# 3. local (src.*)
from src.bus.events import InboundMessage
from src.memory.store import MemoryStore

# 4. type-only (avoid circular imports, reduce load time)
if TYPE_CHECKING:
    from src.config.schema import GenVerConfig
```

**Rules:**
- `TYPE_CHECKING` for types only used in annotations (not runtime)
- Lazy imports inside functions for heavy modules or circular deps
- Never import a whole module just to access one class — use `from X import Y`
- Group order: stdlib → third-party → local → TYPE_CHECKING

## Error Handling

Three tiers, choose the lightest that works:

```python
# Tier 1: Let it crash (internal code, trusted inputs)
# No try-except. Trust the caller. Most code should be this.
result = store.search(query)

# Tier 2: Log and continue (non-fatal, background tasks)
try:
    await index.sync_history(path)
except Exception:
    logger.opt(exception=True).warning("FTS sync failed for {}", path)

# Tier 3: Return error (system boundary — user input, external API)
try:
    resp = await httpx.get(url)
    resp.raise_for_status()
except httpx.HTTPError as e:
    return {"error": str(e)}
```

**Rules:**
- Never bare `except:` — always `except Exception:` minimum
- Use `logger.opt(exception=True).warning()` for non-fatal failures (includes traceback)
- Use `logger.exception()` only for truly unexpected errors worth investigating
- Tools return error strings (never raise) — enables LLM recovery
- Config/factory boundaries raise `ValueError` with actionable message
- Don't add error handling for impossible scenarios

## Docstrings

```python
class MemoryStore:
    """Two-layer memory: MEMORY.md (long-term) + HISTORY.md (searchable log)."""

    def consolidate(self, session: Session, provider: LLMProvider) -> bool:
        """Archive old messages into MEMORY.md via LLM summarization.

        Returns True on success, False if LLM call failed.
        """
```

**Rules:**
- One-liner for simple classes/functions — no boilerplate
- Multi-line only when the contract isn't obvious from the signature
- Skip docstrings on trivial helpers (`_now_iso()`, property getters)
- All public methods need docstrings; private methods only if non-obvious
- No `Args:` / `Returns:` sections unless the signature is ambiguous

## Naming

```python
# Module constants
_MAX_OUTPUT = 30000
DEFAULT_GENVER_VERIFIER_COMMANDS = [...]

# Functions and methods
def resolve_task_workspace(workspace: Path, user_request: str) -> Path: ...
async def _consolidate_memory(self, session: Session) -> bool: ...

# Classes
class MemoryStore: ...
class MemoryHandler: ...

# Private prefix
_safety: SafetyLayer | None = None  # class-level lazy singleton
```

**Rules:**
- `UPPER_SNAKE` for module constants
- `snake_case` for everything else
- `PascalCase` for classes only
- Single underscore prefix for private (no double underscore)
- No abbreviations except universally known ones (`ctx`, `cfg`, `db`, `msg`, `idx`)

## Type Hints

```python
# Use modern syntax (3.10+)
def search(self, query: str, *, max_results: int = 6) -> list[dict[str, Any]]: ...
def get_provider(self) -> ProviderConfig | None: ...

# Keyword-only args after * for clarity
def record_task(self, *, session_key: str, user_message: str, response: str) -> dict: ...
```

**Rules:**
- Always annotate public function signatures
- Use `X | None` not `Optional[X]`
- Use `list[str]` not `List[str]`
- `Any` is acceptable for dict payloads and JSON blobs — don't over-type

## File Size

| Category | Target | Hard limit |
|----------|--------|------------|
| Data models, configs | < 200 lines | 400 |
| Tools, adapters | < 250 lines | 450 |
| Core logic (loop, store) | < 400 lines | 600 |
| CLI commands | < 350 lines | 550 |

These thresholds are review signals, not automatic failure conditions.

- Exceeding the target means the file is carrying refactoring pressure
- Exceeding the hard limit means the PR should explain why the file is not being split yet
- New work should avoid making already-oversized files significantly larger unless the task is actively refactoring that area
- When you do split, extract a focused module (mixin, helper, sub-module), not a speculative abstraction

## Async Patterns

```python
# Fire-and-forget (non-critical background work)
asyncio.create_task(self.hooks.run_post_chat(...))

# Awaited (result needed)
result = await store.consolidate(session, provider)

# Thread offload (blocking I/O in async context)
await asyncio.to_thread(StructuredMemoryStore(workspace).record_task, ...)

# Timeout guard
msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
```

## Testing

- `pytest-asyncio` with `asyncio_mode = "auto"`
- Test file mirrors source: `src/agent/loop_core.py` → `tests/test_loop_core.py`
- One assertion per test when possible
- Use `tmp_path` fixture for filesystem tests
- Mock external calls (LLM, HTTP), never mock internal logic

## What NOT to Do

- Don't add comments that restate the code (`# increment counter` above `counter += 1`)
- Don't add type annotations to code you didn't change
- Don't refactor adjacent code that works fine
- Don't add feature flags or backward-compat shims — just change the code
- Don't create helpers/utilities for one-time operations
- Don't add error handling for scenarios that can't happen
