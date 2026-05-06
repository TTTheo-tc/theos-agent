"""PDF analysis tool — extract text with pypdf, optionally analyze with LLM."""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from src.agent.tools.base import Tool
from src.agent.tools.provider_failures import get_user_safe_provider_error

if TYPE_CHECKING:
    from src.providers.base import LLMProvider

DEFAULT_PROMPT = "Analyze this PDF document."
DEFAULT_MAX_PDFS = 10
MAX_PDF_BYTES = 20 * 1024 * 1024  # 20 MB per PDF
DEFAULT_MAX_PAGES = 50


# ---------------------------------------------------------------------------
# Page range parsing
# ---------------------------------------------------------------------------


def parse_page_range(spec: str, total_pages: int) -> list[int]:
    """Parse a page range string like ``'1-5'``, ``'1,3,5-7'`` into 0-based indices.

    Pages are 1-based in the spec, returned as 0-based indices clamped to
    ``[0, total_pages)``.
    """
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
        if m:
            start = max(1, int(m.group(1)))
            end = min(total_pages, int(m.group(2)))
            for p in range(start, end + 1):
                pages.add(p - 1)
        elif part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < total_pages:
                pages.add(idx)
        else:
            raise ValueError(f"Invalid page range segment: {part!r}")
    return sorted(pages)


# ---------------------------------------------------------------------------
# PDF loading helpers
# ---------------------------------------------------------------------------


def _is_url(s: str) -> bool:
    try:
        p = urlparse(s)
        return p.scheme in ("http", "https")
    except Exception:
        return False


async def _load_pdf_bytes(source: str) -> tuple[bytes, str]:
    """Load PDF bytes from a URL or local path.

    Returns ``(raw_bytes, filename)``.
    """
    if _is_url(source):
        async with httpx.AsyncClient(
            follow_redirects=True, max_redirects=5, timeout=60.0
        ) as client:
            r = await client.get(source)
            r.raise_for_status()
            data = r.content
            if len(data) > MAX_PDF_BYTES:
                raise ValueError(
                    f"PDF too large: {len(data) / 1024 / 1024:.1f} MB "
                    f"(max {MAX_PDF_BYTES / 1024 / 1024:.0f} MB)"
                )
            # Derive filename from URL
            url_path = urlparse(source).path
            fname = url_path.rsplit("/", 1)[-1] if "/" in url_path else "document.pdf"
            if not fname.lower().endswith(".pdf"):
                fname = "document.pdf"
            return data, fname

    # Local file
    p = Path(source).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"PDF not found: {source}")
    if p.stat().st_size > MAX_PDF_BYTES:
        raise ValueError(
            f"PDF too large: {p.stat().st_size / 1024 / 1024:.1f} MB "
            f"(max {MAX_PDF_BYTES / 1024 / 1024:.0f} MB)"
        )
    return p.read_bytes(), p.name


# ---------------------------------------------------------------------------
# Text extraction with pypdf
# ---------------------------------------------------------------------------


def _extract_text_pypdf(
    data: bytes,
    *,
    pages: str | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> tuple[str, int, int | None]:
    """Extract text from PDF bytes using pypdf.

    Returns ``(text, total_pages, extracted_page_count)``.  The final value is
    ``None`` when all pages are extracted.  Raises ``ImportError`` if pypdf is
    unavailable.
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    total = len(reader.pages)

    page_indices = _select_page_indices(total_pages=total, pages=pages, max_pages=max_pages)
    indices = page_indices if page_indices is not None else list(range(total))
    # Clamp to actual page count
    indices = [i for i in indices if 0 <= i < total]

    parts: list[str] = []
    for idx in indices:
        page_text = reader.pages[idx].extract_text() or ""
        if page_text.strip():
            parts.append(f"--- Page {idx + 1} ---\n{page_text}")

    extracted_count = len(page_indices) if page_indices is not None else None
    return "\n\n".join(parts), total, extracted_count


def _select_page_indices(
    *,
    total_pages: int,
    pages: str | None,
    max_pages: int,
) -> list[int] | None:
    if pages and total_pages > 0:
        return parse_page_range(pages, total_pages)
    if total_pages > max_pages:
        return list(range(max_pages))
    return None


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


class PdfTool(Tool):
    """Extract text from PDFs and optionally analyze with LLM."""

    name = "pdf"
    description = (
        "Analyze one or more PDF documents. Extracts text with pypdf; "
        "optionally sends extracted content to an LLM for analysis. "
        "Use 'pdf' for a single path/URL, or 'pdfs' for multiple (up to 10). "
        "Provide a prompt describing what to analyze, or omit it to get raw text."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pdf": {
                "type": "string",
                "description": "Single PDF file path or URL.",
            },
            "pdfs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Multiple PDF file paths or URLs (up to 10).",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "What to analyze in the PDF(s). "
                    "If omitted, returns extracted text only. "
                    "Examples: 'summarize this document', 'extract all tables', "
                    "'what are the key findings?'."
                ),
            },
            "pages": {
                "type": "string",
                "description": (
                    "Page range to process, e.g. '1-5', '1,3,5-7'. "
                    "Defaults to all pages (up to 50)."
                ),
            },
            "max_pages": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "description": "Maximum pages to extract (default: 50).",
            },
            "max_tokens": {
                "type": "integer",
                "minimum": 100,
                "maximum": 16384,
                "description": "Maximum tokens in the LLM response (default: 4096).",
            },
        },
        "required": [],
    }

    def __init__(self, provider: LLMProvider | None = None, model: str | None = None) -> None:
        self._provider = provider
        self._model = model

    async def execute(
        self,
        pdf: str | None = None,
        pdfs: list[str] | None = None,
        prompt: str | None = None,
        pages: str | None = None,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> str:
        # ---- collect and dedupe sources ----
        sources: list[str] = []
        seen: set[str] = set()
        for src in ([pdf] if pdf else []) + (pdfs or []):
            s = src.strip()
            if s and s not in seen:
                seen.add(s)
                sources.append(s)

        if not sources:
            return "Error: No PDF provided. Pass 'pdf' or 'pdfs' parameter."

        if len(sources) > DEFAULT_MAX_PDFS:
            return f"Error: Too many PDFs ({len(sources)}). " f"Maximum is {DEFAULT_MAX_PDFS}."

        # ---- load all PDFs ----
        loaded: list[tuple[bytes, str]] = []  # (data, filename)
        errors: list[str] = []
        for src in sources:
            try:
                data, fname = await _load_pdf_bytes(src)
                loaded.append((data, fname))
            except Exception as e:
                errors.append(f"[{src}] {e}")

        if errors and not loaded:
            return "Error loading PDFs:\n" + "\n".join(errors)

        # ---- extract text from each PDF ----
        all_texts: list[str] = []
        for data, fname in loaded:
            try:
                text, total, extracted_page_count = _extract_text_pypdf(
                    data,
                    pages=pages,
                    max_pages=max_pages,
                )
                label = f"[{fname}] ({total} pages)"
                if extracted_page_count is not None:
                    label += f" (extracted pages: {extracted_page_count})"
                if text.strip():
                    all_texts.append(f"{label}\n{text}")
                else:
                    all_texts.append(f"{label}\n(No extractable text found)")
            except ImportError:
                # pypdf not available — fall back to LLM vision if provider exists
                if self._provider is not None:
                    all_texts.append(f"[{fname}] (pypdf unavailable, using LLM vision fallback)")
                else:
                    all_texts.append(
                        f"[{fname}] Error: pypdf not installed and no LLM provider for fallback."
                    )
            except Exception as e:
                all_texts.append(f"[{fname}] Extraction error: {e}")

        extracted = "\n\n".join(all_texts)

        # Prepend load errors if any
        prefix = ""
        if errors:
            prefix = f"⚠ Failed to load {len(errors)} PDF(s):\n" + "\n".join(errors) + "\n\n"

        # ---- if no prompt, return raw text ----
        if not prompt:
            return prefix + extracted

        # ---- LLM analysis ----
        if self._provider is None:
            return prefix + extracted + "\n\n(No LLM provider available for analysis.)"

        prompt_text = prompt.strip()

        # Build message: extracted text + prompt
        content_blocks: list[dict[str, Any]] = [
            {"type": "text", "text": f"[Extracted PDF content]\n\n{extracted}"},
            {"type": "text", "text": prompt_text},
        ]

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
            safe_error = get_user_safe_provider_error(e, action="PDF analysis")
            if safe_error is not None:
                return prefix + extracted + f"\n\n{safe_error}"
            return prefix + extracted + f"\n\nError calling LLM for analysis: {e}"

        parts: list[str] = []
        if prefix:
            parts.append(prefix.rstrip())
        parts.append(result_text)
        return "\n\n".join(parts)
