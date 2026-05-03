"""Tests for ContextBuilder._compress_image."""

from pathlib import Path

from PIL import Image

from src.agent.context import ContextBuilder


def _make_image(tmp_path: Path, width: int, height: int, fmt: str = "PNG") -> Path:
    """Create a test image file and return its path."""
    img = Image.new("RGB", (width, height), color="red")
    ext = fmt.lower()
    path = tmp_path / f"test.{ext}"
    img.save(path, format=fmt)
    return path


def test_small_image_returned_unchanged(tmp_path: Path):
    path = _make_image(tmp_path, 100, 100)
    raw = path.read_bytes()
    result_bytes, mime = ContextBuilder._compress_image(path, max_bytes=len(raw) + 1000)
    assert result_bytes == raw
    assert mime == "image/png"


def test_oversized_image_is_compressed(tmp_path: Path):
    path = _make_image(tmp_path, 2000, 2000)
    raw = path.read_bytes()
    max_bytes = len(raw) // 2
    result_bytes, mime = ContextBuilder._compress_image(path, max_bytes=max_bytes)
    assert len(result_bytes) <= max_bytes
    assert mime == "image/jpeg"


def test_rgba_image_is_converted(tmp_path: Path):
    img = Image.new("RGBA", (2000, 2000), color=(255, 0, 0, 128))
    path = tmp_path / "test.png"
    img.save(path, format="PNG")
    raw = path.read_bytes()
    max_bytes = len(raw) // 2
    result_bytes, mime = ContextBuilder._compress_image(path, max_bytes=max_bytes)
    assert len(result_bytes) <= max_bytes
    assert mime == "image/jpeg"


def test_very_tight_limit_still_returns_data(tmp_path: Path):
    path = _make_image(tmp_path, 3000, 3000)
    # Very tight limit — should hit last-resort path
    result_bytes, mime = ContextBuilder._compress_image(path, max_bytes=1000)
    assert len(result_bytes) > 0
    assert mime == "image/jpeg"
