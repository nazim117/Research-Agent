# test_transcript.py — Step 10: Meeting transcript processing.
#
# Covers: TranscriptStore CRUD, extract_structured parsing, endpoint behavior,
# idempotency (re-posting same source replaces instead of duplicates).

import json
import os
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transcript import (
    TranscriptStore,
    _parse_extraction_json,
    extract_structured,
)


class TestParseExtractionJson:
    """Unit tests for _parse_extraction_json."""

    def test_parses_clean_json(self):
        raw = '{"decisions": [{"text": "Go with PostgreSQL"}], "action_items": [], "risks": []}'
        result = _parse_extraction_json(raw)
        assert result["decisions"][0]["text"] == "Go with PostgreSQL"

    def test_strips_json_fence(self):
        raw = '```json\n{"decisions": [], "action_items": [], "risks": []}\n```'
        result = _parse_extraction_json(raw)
        assert "decisions" in result

    def test_strips_json_fence_no_lang(self):
        raw = '```\n{"decisions": [], "action_items": [{"text": "Test"}], "risks": []}\n```'
        result = _parse_extraction_json(raw)
        assert len(result["action_items"]) == 1

    def test_raises_on_malformed(self):
        with pytest.raises(ValueError, match="non-JSON"):
            _parse_extraction_json("not json at all")


class TestExtractStructured:
    """Unit tests for extract_structured — calls the LLM and parses the result."""

    @pytest.mark.asyncio
    async def test_returns_all_three_keys(self):
        mock_chat = AsyncMock(
            return_value='{"decisions": [], "action_items": [], "risks": []}'
        )
        result = await extract_structured("Some meeting text", mock_chat)
        assert "decisions" in result
        assert "action_items" in result
        assert "risks" in result

    @pytest.mark.asyncio
    async def test_parses_realistic_extraction(self):
        mock_response = json.dumps(
            {
                "decisions": [{"text": "Use Qdrant for vectors"}],
                "action_items": [
                    {
                        "owner": "Alice",
                        "text": "Write Qdrant client",
                        "due_date": "2026-05-01",
                    },
                    {"owner": "Bob", "text": "Review PR", "due_date": None},
                ],
                "risks": [{"text": "Ollama may be slow on large datasets"}],
            }
        )
        mock_chat = AsyncMock(return_value=mock_response)
        result = await extract_structured("Meeting about architecture", mock_chat)

        assert len(result["decisions"]) == 1
        assert result["decisions"][0]["text"] == "Use Qdrant for vectors"
        assert len(result["action_items"]) == 2
        assert result["action_items"][0]["owner"] == "Alice"
        assert result["action_items"][1]["owner"] == "Bob"
        assert len(result["risks"]) == 1

    @pytest.mark.asyncio
    async def test_normalises_missing_keys(self):
        # Model returned only "decisions" — we should still have all three keys.
        mock_chat = AsyncMock(return_value='{"decisions": [{"text": "X"}]}')
        result = await extract_structured("text", mock_chat)
        assert result["decisions"] == [{"text": "X"}]
        assert result["action_items"] == []
        assert result["risks"] == []


class TestTranscriptStoreInit:
    """Tests for TranscriptStore.init() — creates tables and indexes."""

    @pytest.mark.asyncio
    async def test_creates_three_tables(self, tmp_path):
        store = TranscriptStore(str(tmp_path / "test.db"))
        await store.init()

        # Verify tables exist by querying sqlite_master
        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "test.db"))
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "decisions" in tables
        assert "action_items" in tables
        assert "risks" in tables

    @pytest.mark.asyncio
    async def test_creates_indexes(self, tmp_path):
        store = TranscriptStore(str(tmp_path / "test.db"))
        await store.init()

        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "test.db"))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert any("decisions" in i for i in indexes)
        assert any("action_items" in i for i in indexes)
        assert any("risks" in i for i in indexes)


class TestTranscriptStoreCrud:
    """Tests for TranscriptStore.save and list methods."""

    @pytest.mark.asyncio
    async def test_save_and_list_decisions(self, tmp_path):
        store = TranscriptStore(str(tmp_path / "test.db"))
        await store.init()

        extracted = {
            "decisions": [{"text": "Adopt FastAPI"}],
            "action_items": [],
            "risks": [],
        }
        counts = await store.save_extracted("proj-1", "meeting-1", extracted)

        assert counts["decisions"] == 1
        assert counts["action_items"] == 0
        assert counts["risks"] == 0

        decisions = await store.list_decisions("proj-1")
        assert len(decisions) == 1
        assert decisions[0].text == "Adopt FastAPI"
        assert decisions[0].project_id == "proj-1"
        assert decisions[0].source == "meeting-1"

    @pytest.mark.asyncio
    async def test_save_and_list_action_items(self, tmp_path):
        store = TranscriptStore(str(tmp_path / "test.db"))
        await store.init()

        extracted = {
            "decisions": [],
            "action_items": [
                {"owner": "Alice", "text": "Write tests", "due_date": "2026-05-15"},
                {"owner": None, "text": "Review PR", "due_date": None},
            ],
            "risks": [],
        }
        counts = await store.save_extracted("proj-1", "meeting-1", extracted)

        assert counts["action_items"] == 2

        items = await store.list_action_items("proj-1")
        assert len(items) == 2
        # Check owner handling (None vs "null" string)
        assert items[0].owner == "Alice"
        assert items[1].owner is None

    @pytest.mark.asyncio
    async def test_list_action_items_filter_by_status(self, tmp_path):
        store = TranscriptStore(str(tmp_path / "test.db"))
        await store.init()

        extracted = {
            "decisions": [],
            "action_items": [
                {"owner": "Alice", "text": "Write tests", "due_date": None},
            ],
            "risks": [],
        }
        await store.save_extracted("proj-1", "meeting-1", extracted)

        open_items = await store.list_action_items("proj-1", status="open")
        assert len(open_items) == 1
        assert open_items[0].status == "open"

        done_items = await store.list_action_items("proj-1", status="done")
        assert len(done_items) == 0

    @pytest.mark.asyncio
    async def test_save_and_list_risks(self, tmp_path):
        store = TranscriptStore(str(tmp_path / "test.db"))
        await store.init()

        extracted = {
            "decisions": [],
            "action_items": [],
            "risks": [
                {"text": "Memory leak in Qdrant client"},
            ],
        }
        counts = await store.save_extracted("proj-1", "meeting-1", extracted)

        assert counts["risks"] == 1

        risks = await store.list_risks("proj-1")
        assert len(risks) == 1
        assert risks[0].text == "Memory leak in Qdrant client"

    @pytest.mark.asyncio
    async def test_list_decisions_empty_project(self, tmp_path):
        store = TranscriptStore(str(tmp_path / "test.db"))
        await store.init()

        decisions = await store.list_decisions("nonexistent")
        assert decisions == []


class TestTranscriptStoreIdempotency:
    """Tests for idempotent re-ingest — same source replaces, not duplicates."""

    @pytest.mark.asyncio
    async def test_delete_by_source_clears_all_three_tables(self, tmp_path):
        store = TranscriptStore(str(tmp_path / "test.db"))
        await store.init()

        # Insert some data
        extracted = {
            "decisions": [{"text": "Decision A"}],
            "action_items": [{"owner": "X", "text": "Do Y", "due_date": None}],
            "risks": [{"text": "Risk Z"}],
        }
        await store.save_extracted("proj-1", "meeting-1", extracted)

        # Delete by source
        await store.delete_by_source("proj-1", "meeting-1")

        # Verify all tables are empty for this source
        decisions = await store.list_decisions("proj-1")
        assert decisions == []
        items = await store.list_action_items("proj-1")
        assert items == []
        risks = await store.list_risks("proj-1")
        assert risks == []

    @pytest.mark.asyncio
    async def test_re_ingest_replaces_not_duplicates(self, tmp_path):
        store = TranscriptStore(str(tmp_path / "test.db"))
        await store.init()

        # First ingest
        extracted1 = {
            "decisions": [{"text": "First decision"}],
            "action_items": [],
            "risks": [],
        }
        await store.save_extracted("proj-1", "meeting-1", extracted1)

        # Idempotent re-ingest (delete then save)
        await store.delete_by_source("proj-1", "meeting-1")
        extracted2 = {
            "decisions": [{"text": "Second decision"}],
            "action_items": [],
            "risks": [],
        }
        await store.save_extracted("proj-1", "meeting-1", extracted2)

        decisions = await store.list_decisions("proj-1")
        assert len(decisions) == 1
        assert decisions[0].text == "Second decision"

    @pytest.mark.asyncio
    async def test_delete_by_project_cascades(self, tmp_path):
        store = TranscriptStore(str(tmp_path / "test.db"))
        await store.init()

        extracted = {
            "decisions": [{"text": "Decision"}],
            "action_items": [],
            "risks": [],
        }
        await store.save_extracted("proj-to-delete", "meeting-1", extracted)

        await store.delete_by_project("proj-to-delete")

        decisions = await store.list_decisions("proj-to-delete")
        assert decisions == []
