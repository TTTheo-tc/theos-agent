"""Tests for dream output writers."""

from __future__ import annotations

import json
from pathlib import Path

from src.dream.output.artifacts import ArtifactTracker
from src.dream.output.diary_publisher import (
    _END_MARKER,
    _START_MARKER,
    publish_diary_entry,
)
from src.dream.output.dream_eval import DreamEval
from src.dream.output.dream_review import write_review
from src.dream.output.narrative import write_narrative


class TestDreamEval:
    def test_write_creates_file(self, tmp_path: Path):
        ev = DreamEval(session_id="dream-test-001", topic="test topic")
        path = ev.write(tmp_path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["session_id"] == "dream-test-001"
        assert data["topic"] == "test topic"
        assert data["status"] == "completed"

    def test_write_creates_directory(self, tmp_path: Path):
        out = tmp_path / "nested" / "dir"
        ev = DreamEval(session_id="s1", topic="t")
        path = ev.write(out)
        assert path.exists()

    def test_all_fields_serialized(self, tmp_path: Path):
        ev = DreamEval(
            session_id="s1",
            topic="t",
            budget_usd_cap=5.0,
            budget_usd_used=1.5,
            tool_calls=10,
            web_queries=3,
            status="budget_exceeded",
        )
        data = json.loads(ev.write(tmp_path).read_text())
        assert data["budget_usd_cap"] == 5.0
        assert data["budget_usd_used"] == 1.5
        assert data["tool_calls"] == 10
        assert data["status"] == "budget_exceeded"


class TestNarrative:
    def test_write_basic(self, tmp_path: Path):
        path = write_narrative(
            output_dir=tmp_path,
            topic="test exploration",
            seeds=["seed1", "seed2"],
            findings=["found X"],
            insights=["Y is interesting"],
        )
        assert path.exists()
        content = path.read_text()
        assert "test exploration" in content
        assert "seed1" in content
        assert "found X" in content
        # Insights must be labeled
        assert "[Dream hypothesis]" in content

    def test_empty_inputs(self, tmp_path: Path):
        path = write_narrative(
            output_dir=tmp_path, topic="empty", seeds=[], findings=[], insights=[]
        )
        content = path.read_text()
        assert "no seed material" in content
        assert "no findings recorded" in content
        assert "no insights generated" in content

    def test_insight_labeling_preserved(self, tmp_path: Path):
        path = write_narrative(
            output_dir=tmp_path,
            topic="t",
            seeds=[],
            findings=[],
            insights=["[Unverified exploration] something"],
        )
        content = path.read_text()
        # Should not double-label
        assert content.count("[Unverified exploration]") == 1
        assert "[Dream hypothesis]" not in content


class TestArtifactTracker:
    def test_write_empty_manifest(self, tmp_path: Path):
        tracker = ArtifactTracker(tmp_path)
        path = tracker.write_manifest()
        assert path.exists()
        data = json.loads(path.read_text())
        assert data == []

    def test_add_and_write(self, tmp_path: Path):
        # Create a dummy file
        dummy = tmp_path / "test.txt"
        dummy.write_text("hello")

        tracker = ArtifactTracker(tmp_path)
        entry = tracker.add(dummy, artifact_type="note", description="test file")
        assert entry.size_bytes == 5
        assert entry.type == "note"

        path = tracker.write_manifest()
        data = json.loads(path.read_text())
        assert len(data) == 1
        assert data[0]["path"] == str(dummy)

    def test_add_nonexistent_file(self, tmp_path: Path):
        tracker = ArtifactTracker(tmp_path)
        entry = tracker.add(tmp_path / "ghost.txt")
        assert entry.size_bytes == 0


class TestDreamReview:
    def test_write_review(self, tmp_path: Path):
        ev = DreamEval(
            session_id="dream-test",
            topic="review test",
            seed_sources=["events"],
            tool_calls=5,
            web_queries=2,
            budget_usd_cap=10.0,
            budget_usd_used=3.0,
        )
        path = write_review(
            output_dir=tmp_path,
            eval_data=ev,
            topic="review test",
            artifacts=[],
        )
        assert path.exists()
        content = path.read_text()
        assert "review test" in content
        assert "dream-test" in content
        assert "$3.00 / $10.00" in content


class TestDiaryPublisher:
    def test_creates_new_file(self, tmp_path: Path):
        path = publish_diary_entry(
            workspace=tmp_path,
            session_id="dream-001",
            topic="test dream",
            status="completed",
        )
        assert path.exists()
        content = path.read_text()
        assert _START_MARKER in content
        assert _END_MARKER in content
        assert "test dream" in content
        assert "dream-001" in content

    def test_appends_to_existing(self, tmp_path: Path):
        publish_diary_entry(
            workspace=tmp_path,
            session_id="d1",
            topic="first",
            status="completed",
        )
        publish_diary_entry(
            workspace=tmp_path,
            session_id="d2",
            topic="second",
            status="completed",
        )
        content = (tmp_path / "DREAMS.md").read_text()
        assert "first" in content
        assert "second" in content
        # Markers appear exactly once
        assert content.count(_START_MARKER) == 1
        assert content.count(_END_MARKER) == 1

    def test_adds_markers_to_existing_file_without_them(self, tmp_path: Path):
        (tmp_path / "DREAMS.md").write_text("# Old dreams\n")
        publish_diary_entry(
            workspace=tmp_path,
            session_id="d1",
            topic="new",
            status="completed",
        )
        content = (tmp_path / "DREAMS.md").read_text()
        assert _START_MARKER in content
        assert "new" in content
