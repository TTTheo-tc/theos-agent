"""Tests for tool-call loop detection."""

from src.agent.loop_detector import LoopDetector


def test_no_detection_below_threshold():
    d = LoopDetector(window=10, threshold=3)
    d.record("search", {"q": "hello"})
    d.record("search", {"q": "hello"})
    assert d.check() is None


def test_detect_at_threshold():
    d = LoopDetector(window=10, threshold=3)
    for _ in range(3):
        d.record("search", {"q": "hello"})
    assert d.check() == "search"


def test_different_args_not_detected():
    d = LoopDetector(window=10, threshold=3)
    d.record("search", {"q": "a"})
    d.record("search", {"q": "b"})
    d.record("search", {"q": "c"})
    assert d.check() is None


def test_reset_clears_history():
    d = LoopDetector(window=10, threshold=3)
    for _ in range(3):
        d.record("search", {"q": "hello"})
    assert d.check() == "search"
    d.reset()
    assert d.check() is None


def test_window_evicts_old_entries():
    d = LoopDetector(window=4, threshold=3)
    d.record("search", {"q": "hello"})
    d.record("search", {"q": "hello"})
    # interleave different calls to push old ones out
    d.record("read", {"path": "/tmp"})
    d.record("read", {"path": "/tmp"})
    d.record("search", {"q": "hello"})
    # only 2 "search hello" in last 4 entries, below threshold
    assert d.check() is None


def test_mixed_tools_detect_only_repeated():
    d = LoopDetector(window=10, threshold=3)
    d.record("search", {"q": "hello"})
    d.record("read", {"path": "/tmp"})
    d.record("search", {"q": "hello"})
    d.record("read", {"path": "/other"})
    d.record("search", {"q": "hello"})
    assert d.check() == "search"


def test_non_json_arguments_use_stable_string_fallback():
    class NonJson:
        def __init__(self, value: str) -> None:
            self.value = value

        def __str__(self) -> str:
            return self.value

    d = LoopDetector(window=10, threshold=2)
    d.record("read", {"path": NonJson("a")})
    d.record("read", {"path": NonJson("b")})

    assert d.check() is None

    marker = NonJson("same")
    d.record("read", {"path": marker})
    d.record("read", {"path": marker})
    assert d.check() == "read"
