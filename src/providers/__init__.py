"""LLM provider abstraction module."""

from __future__ import annotations

from src.providers.anthropic_provider import AnthropicProvider
from src.providers.base import LLMProvider, LLMResponse
from src.providers.custom_provider import CustomProvider, OpenAICompatProvider

__all__ = [
    "AnthropicProvider",
    "CustomProvider",
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
