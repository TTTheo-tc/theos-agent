"""Embedding provider abstraction for hybrid search."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx


def _endpoint_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


class EmbeddingProvider(ABC):
    """Async interface for computing text embeddings."""

    @abstractmethod
    async def embed_one(self, text: str) -> list[float]: ...

    @abstractmethod
    def name(self) -> str: ...


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible /v1/embeddings endpoint."""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com",
        dimensions: int = 1536,
    ) -> None:
        self._model = model
        self._dimensions = dimensions
        self._url = _endpoint_url(base_url, "v1/embeddings")
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def embed_one(self, text: str) -> list[float]:
        payload: dict[str, Any] = {"input": text, "model": self._model}
        if self._dimensions:
            payload["dimensions"] = self._dimensions
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(self._url, json=payload, headers=self._headers)
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]

    def name(self) -> str:
        return self._model


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Local Ollama /api/embed endpoint."""

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
    ) -> None:
        self._model = model
        self._url = _endpoint_url(base_url, "api/embed")

    async def embed_one(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(self._url, json={"model": self._model, "input": text})
            resp.raise_for_status()
            data = resp.json()
            # Ollama returns {"embeddings": [[...]]}
            return data["embeddings"][0]

    def name(self) -> str:
        return f"ollama/{self._model}"


class CustomEmbeddingProvider(EmbeddingProvider):
    """Any OpenAI-compatible embedding endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int = 1536,
    ) -> None:
        self._delegate = OpenAIEmbeddingProvider(
            api_key=api_key,
            model=model,
            base_url=base_url,
            dimensions=dimensions,
        )

    def name(self) -> str:
        return "custom"

    def dimensions(self) -> int:
        return self._delegate._dimensions

    async def embed_one(self, text: str) -> list[float]:
        return await self._delegate.embed_one(text)


def _cfg(config: Any, key: str, default: Any = "") -> Any:
    """Get a config value from a dict or Pydantic model."""
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def create_embedding_provider(config: Any) -> EmbeddingProvider | None:
    """Build an EmbeddingProvider from a config dict or Pydantic model.

    Expected keys: provider, model, base_url, api_key, dimensions.
    Returns None when provider is "none" or config is empty.
    """
    provider = (_cfg(config, "provider") or "none").lower()
    if provider == "none":
        return None

    model = _cfg(config, "model", "")
    base_url = _cfg(config, "base_url", "")
    api_key = _cfg(config, "api_key", "")
    dims = int(_cfg(config, "dimensions", 1536) or 1536)

    if provider == "ollama":
        return OllamaEmbeddingProvider(
            model=model,
            base_url=base_url or "http://localhost:11434",
        )

    if provider == "custom":
        if not base_url:
            from loguru import logger

            logger.warning("Custom embedding provider requires base_url")
            return None
        return CustomEmbeddingProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
            dimensions=dims,
        )

    # "openai" or any other OpenAI-compatible provider
    return OpenAIEmbeddingProvider(
        model=model,
        api_key=api_key,
        base_url=base_url or "https://api.openai.com",
        dimensions=dims,
    )
