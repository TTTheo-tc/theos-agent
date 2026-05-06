"""Image analysis tool — uses LLM vision to analyze images."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from src.agent.tools.base import Tool
from src.agent.tools.media_common import collect_sources
from src.agent.tools.media_common import is_http_url as _is_url
from src.agent.tools.provider_failures import get_user_safe_provider_error

if TYPE_CHECKING:
    from src.providers.base import LLMProvider

DEFAULT_PROMPT = "Describe this image in detail."
DEFAULT_MAX_IMAGES = 20
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB per image


async def _load_image(source: str) -> tuple[str, str]:
    """Load an image from a URL or local path.

    Returns (base64_data, mime_type).
    """
    if _is_url(source):
        async with httpx.AsyncClient(
            follow_redirects=True, max_redirects=5, timeout=30.0
        ) as client:
            r = await client.get(source)
            r.raise_for_status()
            data = r.content
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError(
                    f"Image too large: {len(data) / 1024 / 1024:.1f} MB "
                    f"(max {MAX_IMAGE_BYTES / 1024 / 1024:.0f} MB)"
                )
            ct = r.headers.get("content-type", "")
            mime = ct.split(";")[0].strip() if ct else "image/png"
            if not mime.startswith("image/"):
                mime = "image/png"
            return base64.b64encode(data).decode(), mime

    # Local file
    p = Path(source).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Image not found: {source}")
    if p.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError(
            f"Image too large: {p.stat().st_size / 1024 / 1024:.1f} MB "
            f"(max {MAX_IMAGE_BYTES / 1024 / 1024:.0f} MB)"
        )
    mime, _ = mimetypes.guess_type(str(p))
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    return base64.b64encode(p.read_bytes()).decode(), mime


class ImageAnalyzeTool(Tool):
    """Analyze images using LLM vision capabilities."""

    name = "image_analyze"
    description = (
        "Analyze one or more images with a vision model. "
        "Accepts image URLs or local file paths. "
        "Provide a prompt describing what to analyze (e.g. 'describe this image', "
        "'extract text', 'what objects are in this image')."
    )
    parameters = {
        "type": "object",
        "properties": {
            "images": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "List of image URLs or local file paths to analyze. "
                    "Supports http/https URLs and absolute/relative file paths."
                ),
            },
            "image": {
                "type": "string",
                "description": "Single image URL or local file path (convenience alias for images).",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "What to analyze in the image(s). "
                    "Examples: 'describe this image', 'extract all text', "
                    "'identify objects', 'what is the mood of this photo'."
                ),
            },
            "max_tokens": {
                "type": "integer",
                "minimum": 100,
                "maximum": 16384,
                "description": "Maximum tokens in the response (default: 4096).",
            },
        },
        "required": [],
    }

    def __init__(self, provider: LLMProvider, model: str | None = None) -> None:
        self._provider = provider
        self._model = model

    async def execute(
        self,
        images: list[str] | None = None,
        image: str | None = None,
        prompt: str | None = None,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> str:
        sources = collect_sources(image, images)

        if not sources:
            return "Error: No images provided. Pass 'image' or 'images' parameter."

        if len(sources) > DEFAULT_MAX_IMAGES:
            return f"Error: Too many images ({len(sources)}). " f"Maximum is {DEFAULT_MAX_IMAGES}."

        prompt_text = (prompt or DEFAULT_PROMPT).strip()

        # Load all images
        content_blocks: list[dict[str, Any]] = []
        errors: list[str] = []
        for src in sources:
            try:
                b64, mime = await _load_image(src)
                content_blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    }
                )
            except Exception as e:
                errors.append(f"[{src}] {e}")

        if errors and not content_blocks:
            return "Error loading images:\n" + "\n".join(errors)

        # Build multimodal message
        content_blocks.append({"type": "text", "text": prompt_text})

        messages = [{"role": "user", "content": content_blocks}]

        try:
            response = await self._provider.chat(
                messages=messages,
                tools=None,
                model=self._model,
                max_tokens=max_tokens,
                temperature=0.3,
            )
            result_text = response.content or ""
        except Exception as e:
            safe_error = get_user_safe_provider_error(e, action="Image analysis")
            if safe_error is not None:
                return safe_error
            return f"Error calling vision model: {e}"

        # Prepend any partial load errors
        parts: list[str] = []
        if errors:
            parts.append(f"⚠ Failed to load {len(errors)} image(s):\n" + "\n".join(errors))
        parts.append(result_text)
        return "\n\n".join(parts)
