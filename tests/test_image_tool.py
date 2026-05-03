"""Tests for ImageAnalyzeTool."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.tools.image import ImageAnalyzeTool, _is_url, _load_image
from src.providers.base import LLMResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider(content: str = "A cat sitting on a mat.") -> AsyncMock:
    provider = AsyncMock()
    provider.chat.return_value = LLMResponse(content=content, tool_calls=[])
    return provider


def _make_png(tmp_path: Path, name: str = "test.png", size: int = 64) -> Path:
    """Create a minimal valid-ish PNG file for testing."""
    # PNG header + minimal IHDR — enough for mime detection
    header = b"\x89PNG\r\n\x1a\n"
    p = tmp_path / name
    p.write_bytes(header + b"\x00" * size)
    return p


# ---------------------------------------------------------------------------
# Unit tests: _is_url
# ---------------------------------------------------------------------------


class TestIsUrl:
    def test_http(self):
        assert _is_url("http://example.com/img.png") is True

    def test_https(self):
        assert _is_url("https://example.com/img.png") is True

    def test_local_path(self):
        assert _is_url("/tmp/image.png") is False

    def test_relative_path(self):
        assert _is_url("images/photo.jpg") is False

    def test_empty(self):
        assert _is_url("") is False


# ---------------------------------------------------------------------------
# Unit tests: _load_image (local files)
# ---------------------------------------------------------------------------


class TestLoadImageLocal:
    @pytest.mark.asyncio
    async def test_load_local_png(self, tmp_path: Path):
        p = _make_png(tmp_path)
        b64, mime = await _load_image(str(p))
        assert mime == "image/png"
        assert base64.b64decode(b64)[:4] == b"\x89PNG"

    @pytest.mark.asyncio
    async def test_load_local_jpg(self, tmp_path: Path):
        p = tmp_path / "photo.jpg"
        p.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)
        b64, mime = await _load_image(str(p))
        assert mime == "image/jpeg"

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            await _load_image("/nonexistent/image.png")

    @pytest.mark.asyncio
    async def test_file_too_large(self, tmp_path: Path):
        p = tmp_path / "huge.png"
        p.write_bytes(b"\x89PNG" + b"\x00" * (21 * 1024 * 1024))
        with pytest.raises(ValueError, match="too large"):
            await _load_image(str(p))

    @pytest.mark.asyncio
    async def test_tilde_expansion(self, tmp_path: Path):
        p = _make_png(tmp_path)
        # Patch expanduser to return our tmp_path
        with patch("src.agent.tools.image.Path.expanduser", return_value=p):
            b64, mime = await _load_image("~/test.png")
            assert mime == "image/png"


# ---------------------------------------------------------------------------
# Tool: schema and properties
# ---------------------------------------------------------------------------


class TestImageToolSchema:
    def test_name(self):
        tool = ImageAnalyzeTool(provider=_make_provider())
        assert tool.name == "image_analyze"

    def test_description_not_empty(self):
        tool = ImageAnalyzeTool(provider=_make_provider())
        assert len(tool.description) > 20

    def test_parameters_schema(self):
        tool = ImageAnalyzeTool(provider=_make_provider())
        params = tool.parameters
        assert params["type"] == "object"
        assert "images" in params["properties"]
        assert "image" in params["properties"]
        assert "prompt" in params["properties"]

    def test_to_schema(self):
        tool = ImageAnalyzeTool(provider=_make_provider())
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "image_analyze"

    def test_risk_level(self):
        tool = ImageAnalyzeTool(provider=_make_provider())
        assert tool.risk_level == "low"

    def test_owner_only_false(self):
        tool = ImageAnalyzeTool(provider=_make_provider())
        assert tool.owner_only is False


# ---------------------------------------------------------------------------
# Tool: execute
# ---------------------------------------------------------------------------


class TestImageToolExecute:
    @pytest.mark.asyncio
    async def test_no_images_error(self):
        tool = ImageAnalyzeTool(provider=_make_provider())
        result = await tool.execute()
        assert "No images provided" in result

    @pytest.mark.asyncio
    async def test_single_image_local(self, tmp_path: Path):
        provider = _make_provider("A red car.")
        tool = ImageAnalyzeTool(provider=provider)
        p = _make_png(tmp_path)
        result = await tool.execute(image=str(p), prompt="What is this?")
        assert result == "A red car."
        # Verify provider was called with correct structure
        call_args = provider.chat.call_args
        messages = call_args.kwargs["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        content = messages[0]["content"]
        # Should have image_url block + text block
        assert any(c["type"] == "image_url" for c in content)
        assert any(c["type"] == "text" for c in content)

    @pytest.mark.asyncio
    async def test_multiple_images(self, tmp_path: Path):
        provider = _make_provider("Two images analyzed.")
        tool = ImageAnalyzeTool(provider=provider)
        p1 = _make_png(tmp_path, "a.png")
        p2 = _make_png(tmp_path, "b.png")
        result = await tool.execute(images=[str(p1), str(p2)])
        assert result == "Two images analyzed."
        content = provider.chat.call_args.kwargs["messages"][0]["content"]
        image_blocks = [c for c in content if c["type"] == "image_url"]
        assert len(image_blocks) == 2

    @pytest.mark.asyncio
    async def test_image_and_images_combined(self, tmp_path: Path):
        """Both 'image' and 'images' params should be merged and deduped."""
        provider = _make_provider("Three images.")
        tool = ImageAnalyzeTool(provider=provider)
        p1 = _make_png(tmp_path, "a.png")
        p2 = _make_png(tmp_path, "b.png")
        p3 = _make_png(tmp_path, "c.png")
        result = await tool.execute(image=str(p1), images=[str(p2), str(p3)])
        assert result == "Three images."
        content = provider.chat.call_args.kwargs["messages"][0]["content"]
        image_blocks = [c for c in content if c["type"] == "image_url"]
        assert len(image_blocks) == 3

    @pytest.mark.asyncio
    async def test_dedup_images(self, tmp_path: Path):
        """Duplicate image paths should be deduped."""
        provider = _make_provider("One image.")
        tool = ImageAnalyzeTool(provider=provider)
        p = _make_png(tmp_path)
        await tool.execute(image=str(p), images=[str(p)])
        content = provider.chat.call_args.kwargs["messages"][0]["content"]
        image_blocks = [c for c in content if c["type"] == "image_url"]
        assert len(image_blocks) == 1

    @pytest.mark.asyncio
    async def test_default_prompt(self, tmp_path: Path):
        provider = _make_provider("Description.")
        tool = ImageAnalyzeTool(provider=provider)
        p = _make_png(tmp_path)
        await tool.execute(image=str(p))
        content = provider.chat.call_args.kwargs["messages"][0]["content"]
        text_block = next(c for c in content if c["type"] == "text")
        assert "Describe" in text_block["text"]

    @pytest.mark.asyncio
    async def test_custom_prompt(self, tmp_path: Path):
        provider = _make_provider("OCR result.")
        tool = ImageAnalyzeTool(provider=provider)
        p = _make_png(tmp_path)
        await tool.execute(image=str(p), prompt="Extract all text from this image")
        content = provider.chat.call_args.kwargs["messages"][0]["content"]
        text_block = next(c for c in content if c["type"] == "text")
        assert "Extract all text" in text_block["text"]

    @pytest.mark.asyncio
    async def test_too_many_images(self):
        tool = ImageAnalyzeTool(provider=_make_provider())
        paths = [f"/fake/img_{i}.png" for i in range(25)]
        result = await tool.execute(images=paths)
        assert "Too many images" in result

    @pytest.mark.asyncio
    async def test_provider_error(self, tmp_path: Path):
        provider = AsyncMock()
        provider.chat.side_effect = RuntimeError("Model unavailable")
        tool = ImageAnalyzeTool(provider=provider)
        p = _make_png(tmp_path)
        result = await tool.execute(image=str(p))
        assert "Error calling vision model" in result
        assert "Model unavailable" in result

    @pytest.mark.asyncio
    async def test_auth_error_is_rewritten(self, tmp_path: Path):
        provider = AsyncMock()
        provider.chat.side_effect = RuntimeError(
            "Error code: 401 - {'type': 'error', 'error': {'type': "
            "'authentication_error', 'message': 'invalid x-api-key'}}"
        )
        tool = ImageAnalyzeTool(provider=provider)
        p = _make_png(tmp_path)
        result = await tool.execute(image=str(p))
        assert "credential is invalid or expired" in result
        assert "invalid x-api-key" not in result
        assert "authentication_error" not in result

    @pytest.mark.asyncio
    async def test_partial_load_failure(self, tmp_path: Path):
        """If some images fail to load, report errors but still analyze the rest."""
        provider = _make_provider("Partial result.")
        tool = ImageAnalyzeTool(provider=provider)
        p = _make_png(tmp_path)
        result = await tool.execute(images=[str(p), "/nonexistent/bad.png"])
        assert "Failed to load 1 image" in result
        assert "Partial result." in result

    @pytest.mark.asyncio
    async def test_all_images_fail(self):
        tool = ImageAnalyzeTool(provider=_make_provider())
        result = await tool.execute(images=["/bad1.png", "/bad2.png"])
        assert "Error loading images" in result

    @pytest.mark.asyncio
    async def test_max_tokens_passed(self, tmp_path: Path):
        provider = _make_provider("OK")
        tool = ImageAnalyzeTool(provider=provider)
        p = _make_png(tmp_path)
        await tool.execute(image=str(p), max_tokens=2048)
        assert provider.chat.call_args.kwargs["max_tokens"] == 2048

    @pytest.mark.asyncio
    async def test_temperature_low(self, tmp_path: Path):
        provider = _make_provider("OK")
        tool = ImageAnalyzeTool(provider=provider)
        p = _make_png(tmp_path)
        await tool.execute(image=str(p))
        assert provider.chat.call_args.kwargs["temperature"] == 0.3

    @pytest.mark.asyncio
    async def test_custom_model(self, tmp_path: Path):
        provider = _make_provider("OK")
        tool = ImageAnalyzeTool(provider=provider, model="openai/gpt-4o")
        p = _make_png(tmp_path)
        await tool.execute(image=str(p))
        assert provider.chat.call_args.kwargs["model"] == "openai/gpt-4o"

    @pytest.mark.asyncio
    async def test_whitespace_sources_ignored(self, tmp_path: Path):
        provider = _make_provider("OK")
        tool = ImageAnalyzeTool(provider=provider)
        p = _make_png(tmp_path)
        await tool.execute(images=["  ", str(p), ""])
        content = provider.chat.call_args.kwargs["messages"][0]["content"]
        image_blocks = [c for c in content if c["type"] == "image_url"]
        assert len(image_blocks) == 1


# ---------------------------------------------------------------------------
# URL loading (mocked HTTP)
# ---------------------------------------------------------------------------


class TestLoadImageUrl:
    @pytest.mark.asyncio
    async def test_load_url(self):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        mock_response = AsyncMock()
        mock_response.content = png_bytes
        mock_response.headers = {"content-type": "image/png"}
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.agent.tools.image.httpx.AsyncClient", return_value=mock_client):
            b64, mime = await _load_image("https://example.com/cat.png")
            assert mime == "image/png"
            decoded = base64.b64decode(b64)
            assert decoded[:4] == b"\x89PNG"
