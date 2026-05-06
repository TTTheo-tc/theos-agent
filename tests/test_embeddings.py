"""Tests for src/memory/embeddings.py — providers and factory."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.memory.embeddings import (
    CustomEmbeddingProvider,
    OllamaEmbeddingProvider,
    OpenAIEmbeddingProvider,
    create_embedding_provider,
)

# ---------------------------------------------------------------------------
# TestOpenAIProvider
# ---------------------------------------------------------------------------


class TestOpenAIProvider:
    async def test_embed_one(self):
        provider = OpenAIEmbeddingProvider(
            model="text-embedding-3-small",
            api_key="test-key",
            dimensions=256,
        )
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await provider.embed_one("hello world")
            assert result == [0.1, 0.2, 0.3]
            mock_client.post.assert_called_once()

    async def test_embed_batch_is_sequential(self):
        """No batch API exists — embedding multiple texts calls embed_one repeatedly."""
        provider = OpenAIEmbeddingProvider(model="text-embedding-3-small", api_key="test-key")
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.1]}]}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            r1 = await provider.embed_one("text 1")
            r2 = await provider.embed_one("text 2")
            assert r1 == [0.1]
            assert r2 == [0.1]
            assert mock_client.post.call_count == 2

    def test_name_and_dimensions(self):
        provider = OpenAIEmbeddingProvider(
            model="text-embedding-3-small", api_key="key", dimensions=512
        )
        assert provider.name() == "text-embedding-3-small"
        assert provider._dimensions == 512

    def test_base_url_trailing_slash_normalized(self):
        provider = OpenAIEmbeddingProvider(
            model="text-embedding-3-small",
            api_key="key",
            base_url="https://api.example.com/",
        )
        assert provider._url == "https://api.example.com/v1/embeddings"


# ---------------------------------------------------------------------------
# TestOllamaProvider
# ---------------------------------------------------------------------------


class TestOllamaProvider:
    async def test_embed_one(self):
        provider = OllamaEmbeddingProvider(model="nomic-embed-text")
        mock_response = MagicMock()
        mock_response.json.return_value = {"embeddings": [[0.4, 0.5, 0.6]]}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await provider.embed_one("test")
            assert result == [0.4, 0.5, 0.6]

    def test_name(self):
        provider = OllamaEmbeddingProvider(model="nomic-embed-text")
        assert provider.name() == "ollama/nomic-embed-text"

    def test_base_url_trailing_slash_normalized(self):
        provider = OllamaEmbeddingProvider(
            model="nomic-embed-text",
            base_url="http://localhost:11434/",
        )
        assert provider._url == "http://localhost:11434/api/embed"


# ---------------------------------------------------------------------------
# TestCustomProvider
# ---------------------------------------------------------------------------


class TestCustomProvider:
    async def test_delegates_to_openai(self):
        provider = CustomEmbeddingProvider(
            base_url="https://custom.endpoint.com",
            api_key="custom-key",
            model="custom-model",
            dimensions=768,
        )
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"embedding": [0.7, 0.8]}]}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await provider.embed_one("test")
            assert result == [0.7, 0.8]
            # Verify the URL uses the custom base
            call_args = mock_client.post.call_args
            assert "custom.endpoint.com" in call_args[0][0]

    def test_name_returns_custom(self):
        provider = CustomEmbeddingProvider(base_url="https://example.com", api_key="k", model="m")
        assert provider.name() == "custom"


# ---------------------------------------------------------------------------
# TestFactory
# ---------------------------------------------------------------------------


class TestFactory:
    def test_none_returns_none(self):
        result = create_embedding_provider({"provider": "none"})
        assert result is None

    def test_openai_creates_provider(self):
        result = create_embedding_provider(
            {"provider": "openai", "model": "text-embedding-3-small", "api_key": "k"}
        )
        assert isinstance(result, OpenAIEmbeddingProvider)

    def test_ollama_creates_provider(self):
        result = create_embedding_provider({"provider": "ollama", "model": "nomic-embed-text"})
        assert isinstance(result, OllamaEmbeddingProvider)

    def test_custom_without_base_url_returns_none(self):
        result = create_embedding_provider({"provider": "custom", "model": "m", "api_key": "k"})
        assert result is None

    def test_custom_with_base_url_creates_provider(self):
        result = create_embedding_provider(
            {
                "provider": "custom",
                "model": "m",
                "api_key": "k",
                "base_url": "https://example.com",
            }
        )
        assert isinstance(result, CustomEmbeddingProvider)

    def test_unknown_falls_through_to_openai(self):
        """Unknown provider names are treated as OpenAI-compatible."""
        result = create_embedding_provider({"provider": "azure", "model": "m", "api_key": "k"})
        assert isinstance(result, OpenAIEmbeddingProvider)
