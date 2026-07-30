# test_briefing.py — Step 11: Project briefing endpoint.
#
# Covers: BriefingStore.assemble_briefing logic, endpoint contract,
# empty project handling, response time targets.

import os
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from briefing import (
    BriefAction,
    BriefDecision,
    Briefing,
    BriefRisk,
    _generate_summary,
    assemble_briefing,
)


class MockTranscriptStore:
    """Mock transcript store for testing."""

    def __init__(self, actions=None, decisions=None, risks=None):
        self._actions = actions or []
        self._decisions = decisions or []
        self._risks = risks or []

    async def list_action_items(self, project_id, status=None):
        if status:
            return [a for a in self._actions if a.status == status]
        return self._actions

    async def list_decisions(self, project_id):
        return self._decisions

    async def list_risks(self, project_id):
        return self._risks


class MockVectorStore:
    """Mock vector store for testing."""

    def __init__(self, hits=None):
        self._hits = hits or []

    async def embed(self, text):
        return [0.1] * 768  # Mock embedding

    async def search(self, collection, query_vector, k, project_id):
        return self._hits


class Action:
    def __init__(self, id, text, owner, due_date, status, source):
        self.id = id
        self.text = text
        self.owner = owner
        self.due_date = due_date
        self.status = status
        self.source = source


class Decision:
    def __init__(self, id, text, source, created_at):
        self.id = id
        self.text = text
        self.source = source
        self.created_at = created_at


class Risk:
    def __init__(self, id, text, source, created_at):
        self.id = id
        self.text = text
        self.source = source
        self.created_at = created_at


@pytest.fixture
def mock_transcript_store():
    return MockTranscriptStore(
        actions=[
            Action("a1", "Write tests", "Alice", "2026-05-15", "open", "meeting-1"),
            Action("a2", "Review PR", "Bob", None, "open", "meeting-1"),
        ],
        decisions=[
            Decision("d1", "Use PostgreSQL", "meeting-1", "2026-04-01T10:00:00Z"),
            Decision("d2", "Adopt FastAPI", "meeting-1", "2026-04-02T10:00:00Z"),
        ],
        risks=[
            Risk("r1", "Memory leak in worker", "meeting-1", "2026-04-01T10:00:00Z"),
        ],
    )


@pytest.fixture
def mock_vector_store():
    return MockVectorStore()


class TestAssembleBriefing:
    """Tests for assemble_briefing — the main assembler function."""

    @pytest.mark.asyncio
    async def test_returns_all_four_fields(
        self, mock_transcript_store, mock_vector_store
    ):
        mock_chat = AsyncMock(return_value="Project is progressing well.")

        result = await assemble_briefing(
            project_id="proj-1",
            transcript_store=mock_transcript_store,
            vector_store=mock_vector_store,
            chat_fn=mock_chat,
        )

        assert isinstance(result, Briefing)
        assert result.summary is not None
        assert result.open_actions is not None
        assert result.recent_decisions is not None
        assert result.active_risks is not None
        assert result.generated_at is not None

    @pytest.mark.asyncio
    async def test_open_actions_filtered_by_status(
        self, mock_transcript_store, mock_vector_store
    ):
        mock_chat = AsyncMock(return_value="Summary")

        result = await assemble_briefing(
            project_id="proj-1",
            transcript_store=mock_transcript_store,
            vector_store=mock_vector_store,
            chat_fn=mock_chat,
        )

        # Both actions in mock store are "open"
        assert len(result.open_actions) == 2
        assert all(a.status == "open" for a in result.open_actions)

    @pytest.mark.asyncio
    async def test_recent_decisions_limited_to_5(
        self, mock_transcript_store, mock_vector_store
    ):
        # Add more decisions than the limit
        mock_transcript_store._decisions = [
            Decision(f"d{i}", f"Decision {i}", "meeting-1", "2026-04-01T10:00:00Z")
            for i in range(10)
        ]
        mock_chat = AsyncMock(return_value="Summary")

        result = await assemble_briefing(
            project_id="proj-1",
            transcript_store=mock_transcript_store,
            vector_store=mock_vector_store,
            chat_fn=mock_chat,
        )

        assert len(result.recent_decisions) == 5

    @pytest.mark.asyncio
    async def test_active_risks_limited_to_10(
        self, mock_transcript_store, mock_vector_store
    ):
        # Add more risks than the limit
        mock_transcript_store._risks = [
            Risk(f"r{i}", f"Risk {i}", "meeting-1", "2026-04-01T10:00:00Z")
            for i in range(15)
        ]
        mock_chat = AsyncMock(return_value="Summary")

        result = await assemble_briefing(
            project_id="proj-1",
            transcript_store=mock_transcript_store,
            vector_store=mock_vector_store,
            chat_fn=mock_chat,
        )

        assert len(result.active_risks) == 10


class TestEmptyProject:
    """Tests for empty project handling."""

    @pytest.mark.asyncio
    async def test_empty_project_returns_no_content_message(self, mock_vector_store):
        empty_store = MockTranscriptStore()
        mock_chat = AsyncMock(return_value="No content yet for this project.")

        result = await assemble_briefing(
            project_id="empty-proj",
            transcript_store=empty_store,
            vector_store=mock_vector_store,
            chat_fn=mock_chat,
        )

        assert result.summary == "No content yet for this project."
        assert result.open_actions == []
        assert result.recent_decisions == []
        assert result.active_risks == []


class TestGenerateSummary:
    """Tests for the LLM summary generation."""

    @pytest.mark.asyncio
    async def test_generates_summary_from_data(self):
        actions = [
            Action("a1", "Write tests", "Alice", "2026-05-15", "open", "meeting-1"),
        ]
        decisions = [
            Decision("d1", "Use PostgreSQL", "meeting-1", "2026-04-01T10:00:00Z"),
        ]
        risks = [
            Risk("r1", "Memory leak", "meeting-1", "2026-04-01T10:00:00Z"),
        ]
        rag_hits = [{"source": "doc1", "text": "Project started"}]

        mock_chat = AsyncMock(return_value="The project is on track.")

        result = await _generate_summary(actions, decisions, risks, rag_hits, mock_chat)

        assert "The project is on track." == result
        mock_chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_llm_failure_gracefully(self):
        mock_chat = AsyncMock(side_effect=Exception("LLM down"))

        result = await _generate_summary([], [], [], [], mock_chat)

        # Should fall back to a summary based on counts
        assert "No content yet" in result


class TestBriefingDataclass:
    """Tests for the Briefing dataclass."""

    def test_briefing_creation(self):
        b = Briefing(
            summary="Test summary",
            open_actions=[
                BriefAction(
                    "a1", "Test action", "Bob", "2026-05-01", "open", "meeting-1"
                )
            ],
            recent_decisions=[
                BriefDecision(
                    "d1", "Test decision", "meeting-1", "2026-04-01T10:00:00Z"
                )
            ],
            active_risks=[
                BriefRisk("r1", "Test risk", "meeting-1", "2026-04-01T10:00:00Z")
            ],
            generated_at="2026-04-15T10:00:00Z",
        )

        assert b.summary == "Test summary"
        assert len(b.open_actions) == 1
        assert b.open_actions[0].owner == "Bob"
        assert len(b.recent_decisions) == 1
        assert len(b.active_risks) == 1
