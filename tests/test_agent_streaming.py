"""Tests for AgentLoop streaming wiring — throttled _progress path."""

from __future__ import annotations

import time as _time

import pytest

from src.bus.events import OutboundMessage

# ---------------------------------------------------------------------------
# Test the streaming delta / flush closures in isolation
# ---------------------------------------------------------------------------


class _FakeBus:
    """Minimal bus mock that records publish_outbound calls."""

    def __init__(self):
        self.published: list[OutboundMessage] = []

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        self.published.append(msg)


def _make_msg_metadata() -> dict:
    return {"request_id": "test-123"}


def _build_stream_closures(bus: _FakeBus, channel: str = "cli", chat_id: str = "u1"):
    """Recreate the _stream_delta / _flush_stream_buffer closures from _process_message.

    This mirrors the exact logic in loop.py so we can test throttling and
    flushing without instantiating the full AgentLoop.
    """
    metadata: dict = _make_msg_metadata()
    _stream_buffer: list[str] = []
    _stream_last_flush = _time.monotonic()
    _stream_flush_interval = 0.15

    async def _stream_delta(text: str) -> None:
        nonlocal _stream_last_flush
        _stream_buffer.append(text)
        now = _time.monotonic()
        if now - _stream_last_flush >= _stream_flush_interval:
            chunk = "".join(_stream_buffer)
            _stream_buffer.clear()
            _stream_last_flush = now
            meta = dict(metadata)
            meta["_progress"] = True
            meta["_progress_kind"] = "stream"
            await bus.publish_outbound(
                OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content=chunk,
                    metadata=meta,
                )
            )

    async def _flush_stream_buffer() -> None:
        if _stream_buffer:
            chunk = "".join(_stream_buffer)
            _stream_buffer.clear()
            meta = dict(metadata)
            meta["_progress"] = True
            meta["_progress_kind"] = "stream"
            await bus.publish_outbound(
                OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content=chunk,
                    metadata=meta,
                )
            )

    return _stream_delta, _flush_stream_buffer, _stream_buffer


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStreamDeltaPublish:
    """Stream deltas are published via bus with correct metadata."""

    @pytest.mark.asyncio
    async def test_delta_published_after_interval(self):
        """After the flush interval elapses, buffered text is published."""
        bus = _FakeBus()
        delta, flush, _ = _build_stream_closures(bus)

        # First delta — interval just started, should NOT publish immediately
        await delta("hello")
        assert len(bus.published) == 0

        # Simulate time passing beyond the 0.15s interval.
        # We do this by calling delta after a small sleep.
        import asyncio

        await asyncio.sleep(0.16)
        await delta(" world")

        assert len(bus.published) == 1
        msg = bus.published[0]
        assert msg.content == "hello world"
        assert msg.metadata["_progress"] is True
        assert msg.metadata["_progress_kind"] == "stream"


class TestStreamThrottling:
    """Throttling batches rapid deltas into fewer publishes."""

    @pytest.mark.asyncio
    async def test_rapid_deltas_are_batched(self):
        """Many deltas sent without sleeping should NOT each trigger a publish."""
        bus = _FakeBus()
        delta, flush, _ = _build_stream_closures(bus)

        # Send 20 rapid deltas (no sleep between)
        for i in range(20):
            await delta(f"t{i}")

        # Most should be buffered, not published (monotonic clock barely moves)
        # At most 1 publish on the very first call if the clock happens to tick
        assert len(bus.published) <= 1

        # Flush should release whatever is left
        await flush()
        total_content = "".join(m.content for m in bus.published)
        expected = "".join(f"t{i}" for i in range(20))
        assert total_content == expected


class TestStreamBufferFlush:
    """Buffer is flushed after inference completes."""

    @pytest.mark.asyncio
    async def test_flush_publishes_remaining(self):
        """Flush publishes whatever is left in the buffer."""
        bus = _FakeBus()
        delta, flush, _ = _build_stream_closures(bus)

        await delta("partial")
        assert len(bus.published) == 0

        await flush()
        assert len(bus.published) == 1
        assert bus.published[0].content == "partial"
        assert bus.published[0].metadata["_progress_kind"] == "stream"

    @pytest.mark.asyncio
    async def test_flush_noop_when_empty(self):
        """Flush does nothing if the buffer is already empty."""
        bus = _FakeBus()
        _, flush, _ = _build_stream_closures(bus)

        await flush()
        assert len(bus.published) == 0


class TestSignatureWiring:
    """Verify that _run_agent_loop and _run_inference accept on_content_delta."""

    def test_run_agent_loop_accepts_on_content_delta(self):
        """_run_agent_loop signature includes on_content_delta parameter."""
        import inspect

        from src.agent.loop import AgentLoop

        sig = inspect.signature(AgentLoop._run_agent_loop)
        assert "on_content_delta" in sig.parameters
        param = sig.parameters["on_content_delta"]
        assert param.default is None


class TestDirectProgressStreaming:
    """Direct-call progress callbacks should also receive stream deltas."""

    @pytest.mark.asyncio
    async def test_explicit_on_progress_receives_stream_chunks(self):
        calls: list[str] = []

        async def _progress(text: str, *, tool_hint: bool = False) -> None:
            calls.append(text)

        async def _stream_delta(text: str) -> None:
            raise AssertionError("placeholder should be replaced")

        async def _flush_stream_buffer() -> None:
            raise AssertionError("placeholder should be replaced")

        # Mirror the callback-selection branch in AgentLoop._process_message().
        if _progress is not None:

            async def _stream_delta(text: str) -> None:
                await _progress(text)

            async def _flush_stream_buffer() -> None:
                return None

        await _stream_delta("hel")
        await _stream_delta("lo")
        await _flush_stream_buffer()

        assert calls == ["hel", "lo"]

    def test_run_inference_accepts_on_content_delta(self):
        """_run_inference signature includes on_content_delta parameter."""
        import inspect

        from src.agent.loop import AgentLoop

        sig = inspect.signature(AgentLoop._run_inference)
        assert "on_content_delta" in sig.parameters
        param = sig.parameters["on_content_delta"]
        assert param.default is None
