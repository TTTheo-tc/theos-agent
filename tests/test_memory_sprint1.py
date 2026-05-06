"""Sprint 1: Retrieval quality tests."""

from __future__ import annotations

from pathlib import Path

import pytest  # noqa: F401


class TestMMR:
    def test_mmr_prefers_diverse_results(self):
        from src.memory.mmr import mmr_rerank

        results = [
            {"id": "a", "content": "python testing with pytest", "final_score": 0.9},
            {
                "id": "b",
                "content": "python testing with pytest framework",
                "final_score": 0.85,
            },
            {"id": "c", "content": "rust async programming", "final_score": 0.7},
        ]
        reranked = mmr_rerank(results, k=3, lambda_=0.5)
        ids = [r["id"] for r in reranked]
        # a should be first (highest relevance)
        # c should come before b because b is nearly identical to a
        assert ids[0] == "a"
        assert ids[1] == "c"

    def test_mmr_respects_k_limit(self):
        from src.memory.mmr import mmr_rerank

        results = [
            {
                "id": f"r{i}",
                "content": f"unique content {i}",
                "final_score": 0.5 + i * 0.1,
            }
            for i in range(5)
        ]
        reranked = mmr_rerank(results, k=3, lambda_=0.7)
        assert len(reranked) == 3

    def test_mmr_handles_empty(self):
        from src.memory.mmr import mmr_rerank

        assert mmr_rerank([], k=3) == []

    def test_mmr_handles_non_positive_k(self):
        from src.memory.mmr import mmr_rerank

        results = [
            {"id": "a", "content": "x", "final_score": 0.5},
            {"id": "b", "content": "y", "final_score": 0.4},
        ]

        assert mmr_rerank(results, k=0) == []
        assert mmr_rerank(results, k=-1) == []

    def test_mmr_handles_single(self):
        from src.memory.mmr import mmr_rerank

        result = mmr_rerank([{"id": "a", "content": "x", "final_score": 0.5}], k=3)
        assert len(result) == 1

    def test_mmr_uses_score_field_fallback(self):
        """MMR must read 'score' when 'final_score' is absent (StructuredMemoryStore path)."""
        from src.memory.mmr import mmr_rerank

        # StructuredMemoryStore.search produces entries with 'score', not 'final_score'
        results = [
            {"id": "hi_rel", "title": "python pytest guide", "score": 0.95},
            {"id": "hi_rel_dup", "title": "python pytest guide framework", "score": 0.90},
            {"id": "low_rel", "title": "rust memory safety", "score": 0.30},
        ]
        reranked = mmr_rerank(results, k=3, lambda_=0.7)
        ids = [r["id"] for r in reranked]
        # First pick should be highest-relevance item, NOT an arbitrary one
        # (bug: without field fallback, MMR would see all scores as 0 and become pure diversity)
        assert ids[0] == "hi_rel"


class TestRecallSectionScoring:
    def test_stop_words_excluded_from_overlap(self):
        """Common stop words shouldn't inflate overlap scores."""
        from src.memory.recall import _score_section

        section_relevant = ("Architecture", "the postgres database migration plan")
        section_noise = ("Random", "the the the the the the the the the the the the")
        query = "postgres migration"

        score_rel = _score_section(section_relevant, query)
        score_noise = _score_section(section_noise, query)
        assert score_rel > score_noise

    def test_section_length_normalized(self):
        """Long sections shouldn't win just because they contain more words."""
        from src.memory.recall import _score_section

        short_section = ("Decision", "use postgres for state")
        long_section = (
            "Random",
            " ".join(["filler"] * 200) + " postgres " + " ".join(["noise"] * 200),
        )
        query = "postgres"

        short_score = _score_section(short_section, query)
        long_score = _score_section(long_section, query)
        assert short_score > long_score

    def test_title_match_weighted_higher(self):
        """Title matches get 2x weight — with equivalent-length bodies, title wins."""
        from src.memory.recall import _score_section

        title_match = ("Postgres Decisions", " ".join(["filler"] * 50))
        body_match = ("Random Notes", " ".join(["filler"] * 50) + " postgres")
        query = "postgres"

        assert _score_section(title_match, query) > _score_section(body_match, query)


class TestGradedFallback:
    def test_pinned_sections_preferred_over_nothing(self):
        """When no matches, pinned sections fallback before full dump."""
        from src.memory.recall import _graded_fallback

        sections = [
            ("_preamble", "# Memory"),
            ("Decisions", "<!-- pinned -->\nalways use pytest"),
            ("Old Notes", "something unrelated"),
        ]
        result = _graded_fallback(sections, budget_chars=500)
        assert "pytest" in result
        assert "Old Notes" not in result

    def test_empty_when_no_pinned(self):
        """No pinned sections → empty string (caller chooses full or not)."""
        from src.memory.recall import _graded_fallback

        sections = [
            ("_preamble", "# Memory"),
            ("Notes", "some text"),
        ]
        result = _graded_fallback(sections, budget_chars=500)
        assert result == ""


class TestMemoryGetLineHints:
    @pytest.mark.asyncio
    async def test_memory_search_includes_line_hint_for_memory_get(self, tmp_path: Path):
        from src.agent.tools.context import ToolContext
        from src.agent.tools.memory_search import MemorySearchTool
        from src.memory.index import MemoryIndex
        from src.store.database import Database

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text(
            """# Long-term Memory

## Decisions
<!-- updated: 2026-04-14 -->
- use postgres for primary data
- use redis for ephemeral cache
- run alembic before deploy
""",
            encoding="utf-8",
        )

        db = Database(tmp_path / "test.db")
        await db.connect()
        index = MemoryIndex(db)
        await index.ensure_table()
        await index.sync_all(memory_dir)

        tool = MemorySearchTool(index_resolver=lambda _sk: index)
        output = await tool.execute(
            query="postgres deploy", _context=ToolContext(session_key="cli:test")
        )
        await db.close()

        assert "Decisions lines " in output

    @pytest.mark.asyncio
    async def test_memory_search_does_not_write_recall_journal_by_default(
        self, tmp_path: Path
    ):
        from src.agent.tools.context import ToolContext
        from src.agent.tools.memory_search import MemorySearchTool

        class _Index:
            async def search(self, *_args, **_kwargs):
                return [
                    {
                        "source": "MEMORY.md",
                        "section": "Decisions",
                        "content": "Use postgres for primary data before deploy.",
                        "score": 1.0,
                        "timestamp": "",
                    }
                ]

        tool = MemorySearchTool(
            index_resolver=lambda _sk: _Index(),
            workspace_resolver=lambda _sk: tmp_path,
        )
        output = await tool.execute(
            query="postgres deploy",
            _context=ToolContext(session_key="cli:test"),
        )

        assert "postgres" in output
        assert not (tmp_path / "memory" / "instinct" / "recall_journal.jsonl").exists()

    @pytest.mark.asyncio
    async def test_memory_search_recall_telemetry_uses_line_hint_path(self, tmp_path: Path):
        from src.agent.tools.context import ToolContext
        from src.agent.tools.memory_search import MemorySearchTool

        class _Index:
            async def search(self, *_args, **_kwargs):
                return [
                    {
                        "source": "MEMORY.md",
                        "section": "Decisions",
                        "content": "Use postgres for primary data before deploy.",
                        "score": 1.0,
                        "timestamp": "",
                    }
                ]

        tool = MemorySearchTool(
            index_resolver=lambda _sk: _Index(),
            workspace_resolver=lambda _sk: tmp_path,
            recall_telemetry_enabled=True,
        )
        output = await tool.execute(
            query="postgres deploy",
            _context=ToolContext(session_key="cli:test"),
        )

        import asyncio
        import json

        await asyncio.sleep(0)

        journal = tmp_path / "memory" / "instinct" / "recall_journal.jsonl"
        entry = json.loads(journal.read_text().strip())
        assert "postgres" in output
        assert entry["path"] == "MEMORY.md:Decisions@1-1"
