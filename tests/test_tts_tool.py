"""Tests for TtsTool."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.agent.tools.tts import (
    MAX_TEXT_LENGTH,
    TtsTool,
    _detect_backend,
    _local_extension,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    return tmp_path


def _tool(workspace: Path, **kwargs: Any) -> TtsTool:
    return TtsTool(workspace=workspace, **kwargs)


# ---------------------------------------------------------------------------
# Schema / metadata
# ---------------------------------------------------------------------------


class TestSchema:
    def test_name(self, tmp_workspace: Path):
        assert _tool(tmp_workspace).name == "tts"

    def test_risk_level(self, tmp_workspace: Path):
        assert _tool(tmp_workspace).risk_level == "low"

    def test_parameters_has_text(self, tmp_workspace: Path):
        params = _tool(tmp_workspace).parameters
        assert "text" in params["properties"]
        assert params["required"] == ["text"]

    def test_to_schema(self, tmp_workspace: Path):
        schema = _tool(tmp_workspace).to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "tts"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestValidation:
    @pytest.mark.asyncio
    async def test_empty_text(self, tmp_workspace: Path):
        result = await _tool(tmp_workspace).execute(text="")
        assert "Error" in result
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_whitespace_only(self, tmp_workspace: Path):
        result = await _tool(tmp_workspace).execute(text="   ")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_text_too_long(self, tmp_workspace: Path):
        result = await _tool(tmp_workspace).execute(text="x" * (MAX_TEXT_LENGTH + 1))
        assert "Error" in result
        assert "too long" in result.lower()

    @pytest.mark.asyncio
    async def test_unknown_backend(self, tmp_workspace: Path):
        result = await _tool(tmp_workspace).execute(text="hello", backend="unknown")
        assert "Error" in result
        assert "Unknown backend" in result


# ---------------------------------------------------------------------------
# Local backend: macOS say
# ---------------------------------------------------------------------------


class TestSayBackend:
    @pytest.mark.asyncio
    async def test_say_generates_audio(self, tmp_workspace: Path):
        if not shutil.which("say"):
            pytest.skip("say not available")
        tool = _tool(tmp_workspace, openai_api_key="")
        result = await tool.execute(text="Hello world", backend="say")
        assert "Audio saved to" in result
        assert "backend: say" in result
        assert "aiff" in result

        # Verify file exists
        tts_dir = tmp_workspace / "runtime" / "tts"
        files = list(tts_dir.glob("*.aiff"))
        assert len(files) == 1
        assert files[0].stat().st_size > 0

    @pytest.mark.asyncio
    async def test_say_with_voice(self, tmp_workspace: Path):
        if not shutil.which("say"):
            pytest.skip("say not available")
        tool = _tool(tmp_workspace, openai_api_key="")
        result = await tool.execute(text="Hello", backend="say", voice="Samantha")
        assert "Audio saved to" in result
        assert "voice: Samantha" in result

    @pytest.mark.asyncio
    async def test_say_not_found(self, tmp_workspace: Path):
        with patch("shutil.which", return_value=None):
            tool = _tool(tmp_workspace, openai_api_key="")
            result = await tool.execute(text="hello", backend="say")
            assert "Error" in result
            assert "not found" in result


# ---------------------------------------------------------------------------
# Local backend: espeak
# ---------------------------------------------------------------------------


class TestEspeakBackend:
    @pytest.mark.asyncio
    async def test_espeak_not_found(self, tmp_workspace: Path):
        with patch("shutil.which", return_value=None):
            tool = _tool(tmp_workspace, openai_api_key="")
            result = await tool.execute(text="hello", backend="espeak")
            assert "Error" in result
            assert "not found" in result


# ---------------------------------------------------------------------------
# OpenAI backend
# ---------------------------------------------------------------------------


class TestOpenAIBackend:
    @pytest.mark.asyncio
    async def test_openai_no_key(self, tmp_workspace: Path):
        tool = _tool(tmp_workspace, openai_api_key="")
        result = await tool.execute(text="hello", backend="openai")
        assert "Error" in result
        assert "OPENAI_API_KEY" in result

    @pytest.mark.asyncio
    async def test_openai_success(self, tmp_workspace: Path):
        fake_audio = b"\xff\xfb\x90\x00" + b"\x00" * 100  # fake mp3 header

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.content = fake_audio
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.agent.tools.tts.httpx.AsyncClient", return_value=mock_client):
            tool = _tool(tmp_workspace, openai_api_key="sk-test-key")
            result = await tool.execute(text="hello", backend="openai")

        assert "Audio saved to" in result
        assert "backend: openai" in result
        assert "format: mp3" in result

        # Verify the API was called correctly
        call_kwargs = mock_client.post.call_args
        assert "audio/speech" in call_kwargs.args[0]
        body = call_kwargs.kwargs["json"]
        assert body["input"] == "hello"
        assert body["model"] == "tts-1"
        assert body["voice"] == "alloy"

    @pytest.mark.asyncio
    async def test_openai_custom_voice_and_model(self, tmp_workspace: Path):
        fake_audio = b"\x00" * 50

        mock_response = AsyncMock()
        mock_response.content = fake_audio
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.agent.tools.tts.httpx.AsyncClient", return_value=mock_client):
            tool = _tool(tmp_workspace, openai_api_key="sk-test")
            result = await tool.execute(text="hi", backend="openai", voice="nova", model="tts-1-hd")

        assert "voice: nova" in result
        assert "model: tts-1-hd" in result

        body = mock_client.post.call_args.kwargs["json"]
        assert body["voice"] == "nova"
        assert body["model"] == "tts-1-hd"

    @pytest.mark.asyncio
    async def test_openai_api_error(self, tmp_workspace: Path):
        import httpx as _httpx

        request = _httpx.Request("POST", "http://x")
        real_response = _httpx.Response(429, text="Rate limit exceeded", request=request)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=real_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.agent.tools.tts.httpx.AsyncClient", return_value=mock_client):
            tool = _tool(tmp_workspace, openai_api_key="sk-test")
            result = await tool.execute(text="hello", backend="openai")

        assert "Error" in result
        assert "429" in result


# ---------------------------------------------------------------------------
# Auto backend selection
# ---------------------------------------------------------------------------


class TestAutoBackend:
    @pytest.mark.asyncio
    async def test_auto_prefers_openai(self, tmp_workspace: Path):
        """When API key is set and OpenAI succeeds, auto uses OpenAI."""
        fake_audio = b"\x00" * 50

        mock_response = AsyncMock()
        mock_response.content = fake_audio
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("src.agent.tools.tts.httpx.AsyncClient", return_value=mock_client):
            tool = _tool(tmp_workspace, openai_api_key="sk-test")
            result = await tool.execute(text="hello")

        assert "backend: openai" in result

    @pytest.mark.asyncio
    async def test_auto_falls_back_to_local(self, tmp_workspace: Path):
        """When no API key, auto falls back to local backend."""
        if not shutil.which("say") and not shutil.which("espeak"):
            pytest.skip("No local TTS backend available")

        tool = _tool(tmp_workspace, openai_api_key="")
        result = await tool.execute(text="hello")
        assert "Audio saved to" in result
        # Should use local backend
        assert "backend: say" in result or "backend: espeak" in result

    @pytest.mark.asyncio
    async def test_auto_no_backend_available(self, tmp_workspace: Path):
        """When no API key and no local backend, returns error."""
        with patch("src.agent.tools.tts._detect_backend", return_value="none"):
            tool = _tool(tmp_workspace, openai_api_key="")
            result = await tool.execute(text="hello")
        assert "Error" in result
        assert "No TTS backend" in result


# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------


class TestOutputDir:
    @pytest.mark.asyncio
    async def test_creates_output_dir(self, tmp_workspace: Path):
        if not shutil.which("say"):
            pytest.skip("say not available")
        tts_dir = tmp_workspace / "runtime" / "tts"
        assert not tts_dir.exists()

        tool = _tool(tmp_workspace, openai_api_key="")
        await tool.execute(text="hello", backend="say")
        assert tts_dir.is_dir()


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_detect_backend_say(self):
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/say" if x == "say" else None):
            assert _detect_backend() == "say"

    def test_detect_backend_espeak(self):
        with patch(
            "shutil.which",
            side_effect=lambda x: "/usr/bin/espeak" if x == "espeak" else None,
        ):
            assert _detect_backend() == "espeak"

    def test_detect_backend_none(self):
        with patch("shutil.which", return_value=None):
            assert _detect_backend() == "none"

    def test_local_extension_say(self):
        assert _local_extension("say") == ".aiff"

    def test_local_extension_espeak(self):
        assert _local_extension("espeak") == ".wav"
