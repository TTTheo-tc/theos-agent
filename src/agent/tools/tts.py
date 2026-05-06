"""Text-to-speech tool — convert text to audio files.

Tries OpenAI TTS API first (if an API key is available), then falls back
to the macOS ``say`` command or ``espeak`` on Linux.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Any

import httpx

from src.agent.tools.base import Tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
OPENAI_DEFAULT_MODEL = "tts-1"
OPENAI_DEFAULT_VOICE = "alloy"
MAX_TEXT_LENGTH = 4096  # OpenAI limit


# ---------------------------------------------------------------------------
# TTS backends
# ---------------------------------------------------------------------------


async def _openai_tts(
    text: str,
    output_path: Path,
    api_key: str,
    *,
    model: str = OPENAI_DEFAULT_MODEL,
    voice: str = OPENAI_DEFAULT_VOICE,
    base_url: str | None = None,
) -> None:
    """Call OpenAI TTS API and write mp3 to *output_path*."""
    url = f"{(base_url or OPENAI_TTS_URL).rstrip('/')}"
    if not url.endswith("/audio/speech"):
        url = f"{url}/audio/speech"

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "input": text,
                "voice": voice,
                "response_format": "mp3",
            },
        )
        r.raise_for_status()
        output_path.write_bytes(r.content)


async def _say_tts(text: str, output_path: Path, voice: str | None = None) -> None:
    """macOS ``say`` command → AIFF → output file."""
    cmd = ["say"]
    if voice:
        cmd.extend(["-v", voice])
    cmd.extend(["-o", str(output_path), "--", text])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"say exited with code {proc.returncode}: {stderr.decode(errors='replace').strip()}"
        )


async def _espeak_tts(text: str, output_path: Path, voice: str | None = None) -> None:
    """Linux ``espeak`` fallback → wav output."""
    cmd = ["espeak"]
    if voice:
        cmd.extend(["-v", voice])
    cmd.extend(["-w", str(output_path), "--", text])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"espeak exited with code {proc.returncode}: {stderr.decode(errors='replace').strip()}"
        )


def _detect_backend() -> str:
    """Return the best available local TTS backend name."""
    if shutil.which("say"):
        return "say"
    if shutil.which("espeak"):
        return "espeak"
    return "none"


def _local_extension(backend: str) -> str:
    """File extension for local backend output."""
    if backend == "say":
        return ".aiff"
    return ".wav"


# ---------------------------------------------------------------------------
# TtsTool
# ---------------------------------------------------------------------------


class TtsTool(Tool):
    """Convert text to speech audio files."""

    def __init__(
        self,
        workspace: Path,
        *,
        openai_api_key: str | None = None,
        openai_base_url: str | None = None,
    ) -> None:
        self._workspace = workspace
        self._api_key = openai_api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = openai_base_url
        self._output_dir = workspace / "runtime" / "tts"

    @property
    def name(self) -> str:
        return "tts"

    @property
    def description(self) -> str:
        return (
            "Convert text to speech. Returns the path to the generated audio file. "
            "Uses OpenAI TTS API when available, otherwise falls back to system TTS "
            "(macOS say / Linux espeak)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to convert to speech.",
                },
                "voice": {
                    "type": "string",
                    "description": (
                        "Voice name. For OpenAI: alloy, ash, ballad, coral, echo, "
                        "fable, nova, onyx, sage, shimmer. For macOS say: any "
                        "installed voice name."
                    ),
                },
                "model": {
                    "type": "string",
                    "description": "TTS model (OpenAI only). Default: tts-1.",
                },
                "backend": {
                    "type": "string",
                    "enum": ["openai", "say", "espeak", "auto"],
                    "description": (
                        "TTS backend to use. 'auto' (default) tries OpenAI first, "
                        "then local system TTS."
                    ),
                },
            },
            "required": ["text"],
        }

    @property
    def risk_level(self) -> str:
        return "low"

    async def execute(
        self,
        text: str = "",
        voice: str | None = None,
        model: str | None = None,
        backend: str | None = None,
        **kwargs: Any,
    ) -> str:
        del kwargs
        if not text or not text.strip():
            return "Error: 'text' is required and must not be empty."

        text = text.strip()
        if len(text) > MAX_TEXT_LENGTH:
            return (
                f"Error: Text too long ({len(text)} chars). "
                f"Maximum is {MAX_TEXT_LENGTH} characters."
            )

        self._output_dir.mkdir(parents=True, exist_ok=True)

        chosen = (backend or "auto").lower()

        if chosen == "auto":
            return await self._auto(text, voice, model)
        elif chosen == "openai":
            return await self._via_openai(text, voice, model)
        elif chosen == "say":
            return await self._via_local("say", text, voice)
        elif chosen == "espeak":
            return await self._via_local("espeak", text, voice)
        else:
            return f"Error: Unknown backend '{chosen}'. Use: openai, say, espeak, auto."

    # --- backend dispatch ---

    async def _auto(self, text: str, voice: str | None, model: str | None) -> str:
        # Try OpenAI first if key is available
        if self._api_key:
            try:
                return await self._via_openai(text, voice, model)
            except Exception:
                pass  # Fall through to local

        local = _detect_backend()
        if local == "none":
            if self._api_key:
                # OpenAI failed and no local backend
                return await self._via_openai(text, voice, model)
            return (
                "Error: No TTS backend available. Set OPENAI_API_KEY for "
                "OpenAI TTS, or install 'say' (macOS) / 'espeak' (Linux)."
            )
        return await self._via_local(local, text, voice)

    async def _via_openai(self, text: str, voice: str | None, model: str | None) -> str:
        if not self._api_key:
            return "Error: OPENAI_API_KEY is required for OpenAI TTS."

        filename = f"tts_{int(time.time() * 1000)}.mp3"
        output_path = self._output_dir / filename

        try:
            await _openai_tts(
                text,
                output_path,
                self._api_key,
                model=model or OPENAI_DEFAULT_MODEL,
                voice=voice or OPENAI_DEFAULT_VOICE,
                base_url=self._base_url,
            )
        except httpx.HTTPStatusError as e:
            return (
                f"Error: OpenAI TTS API returned {e.response.status_code}: {e.response.text[:200]}"
            )
        except Exception as e:
            return f"Error: OpenAI TTS failed: {e}"

        size_kb = output_path.stat().st_size / 1024
        return (
            f"Audio saved to: {output_path}\n"
            f"  backend: openai\n"
            f"  model: {model or OPENAI_DEFAULT_MODEL}\n"
            f"  voice: {voice or OPENAI_DEFAULT_VOICE}\n"
            f"  size: {size_kb:.1f} KB\n"
            f"  format: mp3"
        )

    async def _via_local(self, backend: str, text: str, voice: str | None) -> str:
        if not shutil.which(backend):
            return f"Error: '{backend}' command not found on this system."

        ext = _local_extension(backend)
        filename = f"tts_{int(time.time() * 1000)}{ext}"
        output_path = self._output_dir / filename

        try:
            if backend == "say":
                await _say_tts(text, output_path, voice)
            else:
                await _espeak_tts(text, output_path, voice)
        except asyncio.TimeoutError:
            return f"Error: {backend} timed out after 120 seconds."
        except Exception as e:
            return f"Error: {backend} failed: {e}"

        if not output_path.exists() or output_path.stat().st_size == 0:
            return f"Error: {backend} produced no output."

        size_kb = output_path.stat().st_size / 1024
        return (
            f"Audio saved to: {output_path}\n"
            f"  backend: {backend}\n"
            f"  voice: {voice or '(default)'}\n"
            f"  size: {size_kb:.1f} KB\n"
            f"  format: {ext.lstrip('.')}"
        )
