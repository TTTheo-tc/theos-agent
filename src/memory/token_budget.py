"""Shared token estimation utilities.

Uses tiktoken cl100k_base for precise counting with character-based
fallback when tiktoken is unavailable.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

CHARS_PER_TOKEN = 4

_encoder: Any = None
_encoder_loaded = False


def _get_encoder() -> Any:
    global _encoder, _encoder_loaded
    if not _encoder_loaded:
        _encoder_loaded = True
        try:
            import tiktoken

            _encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            logger.warning("tiktoken unavailable — falling back to character heuristic")
    return _encoder


def _estimate_text_tokens(
    text: str,
    encoder: Any,
    *,
    fallback_multiplier: float = 1.0,
) -> int:
    if encoder is not None:
        return len(encoder.encode(text))
    return int(len(text) / CHARS_PER_TOKEN * fallback_multiplier)


def estimate_tokens(text: str | None, *, safety_margin: float = 1.05) -> int:
    """Estimate token count using tiktoken with character-based fallback."""
    if not text:
        return 0
    enc = _get_encoder()
    base = _estimate_text_tokens(text, enc, fallback_multiplier=1.2)
    return int(base * safety_margin)


def estimate_messages_tokens(messages: list[dict], *, safety_margin: float = 1.05) -> int:
    """Estimate total token count for a list of chat messages."""
    total = 0
    enc = _get_encoder()
    for m in messages:
        content = str(m.get("content", "") or "")
        if not content:
            continue
        total += _estimate_text_tokens(content, enc)
    return int(total * safety_margin) if total else 0


def resolve_context_limit(model: str) -> int:
    """Best-effort resolution of a model's context window size in tokens."""
    model_lower = (model or "").lower()
    if "1m" in model_lower or "1000k" in model_lower:
        return 1_000_000
    if "200k" in model_lower:
        return 200_000
    if any(k in model_lower for k in ("claude", "gemini")):
        return 200_000
    if "gpt-4o" in model_lower or "gpt-4-turbo" in model_lower:
        return 128_000
    if "deepseek" in model_lower:
        return 128_000
    return 128_000
