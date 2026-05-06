"""Post-turn background memory extraction — **narrative-memory lane**.

After each successful turn, extract durable facts (decisions, config changes,
policies, architecture choices) from recent conversation and merge them into
MEMORY.md.  Runs as fire-and-forget — failures are logged but never propagate.

This module writes directly to MEMORY.md (the human-readable narrative layer).
It is intentionally separate from the **structured-memory lane**
(``StructuredMemoryStore`` / ``KnowledgeGraph``) which persists typed
task/rule/research nodes via ``persist_structured_memory()``.  The two lanes
serve different purposes:

- **Narrative lane** (this module): project-level knowledge, decisions, policies
  — things a human or future session should know in natural language.
- **Structured lane** (``structured.py``): machine-queryable nodes with
  importance scores, embeddings, temporal decay, and domain-aware retrieval.

Do NOT treat extracted facts as structured-memory nodes.  If you need to bridge
the two, do so explicitly via a normalization step, not by having this module
write to the KG.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from src.memory.store import MemoryStore
    from src.providers.base import LLMProvider

_EXTRACT_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_facts",
            "description": "Save durable facts extracted from the conversation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "facts": {
                        "type": "array",
                        "description": "List of durable facts to persist.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "section": {
                                    "type": "string",
                                    "description": "MEMORY.md section heading (e.g. 'Architecture Decisions').",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "The fact to record, as a concise bullet point.",
                                },
                            },
                            "required": ["section", "content"],
                        },
                    },
                },
                "required": ["facts"],
            },
        },
    }
]

_EXTRACT_PROMPT = """\
Review the following conversation excerpt and extract **durable facts** worth
remembering long-term.  Durable facts include:

- Decisions made (architecture, tooling, config)
- Policy or convention agreements
- Configuration values chosen
- Important constraints or requirements discovered

Do NOT extract:
- Ephemeral questions or greetings
- Resolved debugging steps
- Transient status updates
- Facts that are obvious from the codebase itself

If there are durable facts, call the save_facts tool.  If nothing is worth
persisting, respond with a short message and do NOT call the tool.

## Conversation
{conversation}"""


async def extract_durable_facts(
    messages: list[dict[str, Any]],
    provider: "LLMProvider",
    model: str,
    *,
    max_messages: int = 20,
) -> list[dict[str, Any]]:
    """Extract durable facts from recent messages via LLM tool call.

    Returns a list of ``{"section": ..., "content": ...}`` dicts, or ``[]``
    on failure or when no durable facts are found.
    """
    recent = messages[-max_messages:]
    lines: list[str] = []
    for m in recent:
        content = m.get("content", "")
        if not isinstance(content, str) or not content:
            continue
        role = m.get("role", "unknown").upper()
        lines.append(f"{role}: {content[:500]}")

    if not lines:
        return []

    conversation_text = "\n".join(lines)
    prompt = _EXTRACT_PROMPT.format(conversation=conversation_text)

    try:
        response = await provider.chat(
            messages=[
                {
                    "role": "system",
                    "content": "You extract durable facts from conversations. "
                    "Call the save_facts tool if you find any.",
                },
                {"role": "user", "content": prompt},
            ],
            tools=_EXTRACT_TOOL,
            model=model,
            max_tokens=1024,
        )
    except Exception:
        logger.opt(exception=True).debug("Memory extraction LLM call failed")
        return []

    if not response.has_tool_calls:
        return []

    args = response.tool_calls[0].arguments
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(args, dict):
        return []

    facts = args.get("facts")
    if not isinstance(facts, list):
        return []

    return [
        {"section": str(f["section"]), "content": str(f["content"])}
        for f in facts
        if isinstance(f, dict) and f.get("section") and f.get("content")
    ]


def merge_extracted_facts(store: "MemoryStore", facts: list[dict[str, Any]]) -> int:
    """Merge extracted facts into MEMORY.md.

    Deduplicates (case-insensitive), adds ``<!-- updated: YYYY-MM-DD -->``
    timestamps, and creates new sections as needed.

    Returns the count of facts actually merged (excluding duplicates).
    """
    if not facts:
        return 0

    return store.merge_bullets(
        [(str(fact["section"]), str(fact["content"])) for fact in facts]
    )
