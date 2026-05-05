"""LLM provider abstraction module."""

from __future__ import annotations

from src.providers.anthropic_provider import AnthropicProvider
from src.providers.base import LLMProvider, LLMResponse
from src.providers.custom_provider import OpenAICompatProvider

__all__ = [
    "AnthropicProvider",
    "LLMProvider",
    "LLMResponse",
    "OpenAICompatProvider",
    "OpenAICodexProvider",
]


def __getattr__(name: str):
    if name == "OpenAICodexProvider":
        from src.providers.openai_codex_provider import OpenAICodexProvider

        return OpenAICodexProvider
    raise AttributeError(name)
