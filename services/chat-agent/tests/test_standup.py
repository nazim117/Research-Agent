# test_standup.py — standup assembler unit tests.
#
# Uses a fake TranscriptStore (in-memory lists) and a fake chat_fn so
# no real DB or LLM is required.  Covers window filtering, bucket
# assignment, action sorting, empty-project handling, and LLM failure.

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from standup import assemble_standup, _within_window, _sort_open_actions, Standup


# ---------------------------------------------------------------------------
# Helpers — shared test fixtures
# ---------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _recent() -> str:
    """An ISO timestamp 1 hour ago — within the 24h window."""
    return _iso(datetime.now(timezone.utc) - timedelta(hours=1))


def _old() -> str:
    """An ISO timestamp 48 hours ago — outside the 24h window."""
    return _iso(datetime.now(timezone.utc) - timedelta(hours=48))


class FakeDecision:
    def __init__(self, id, text, created_at):
        self.id = id
        self.text = text
        self.source = "meeting-1"
        self.created_at = created_at


class FakeActionItem:
    def __init__(self, id, text, status, due_date=None, created_at=None):
        self.id = id
        self.text = text
        self.status = status
        self.due_date = due_date
        self.owner = None
        self.source = "meeting-1"
        self.created_at = created_at or _recent()


class FakeRisk:
    def __init__(self, id, text, created_at=None):
        self.id = id
        self.text = text
        self.source = "meeting-1"
        self.created_at = created_at or _recent()


class FakeTranscriptStore:
    def __init__(self, decisions=None, action_items=None, risks=None):
        self._decisions = decisions or []
        self._action_items = action_items or []
        self._risks = risks or []

    async def list_decisions(self, project_id):
        return self._decisions

    async def list_action_items(self, project_id, status=None):
        if status is None:
            return self._action_items
        return [a for a in self._action_items if a.status == status]

    async def list_risks(self, project_id):
        return self._risks


# ---------------------------------------------------------------------------
# _within_window helper
# ---------------------------------------------------------------------------

class TestWithinWindow:
    def test_recent_timestamp_is_within(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        assert _within_window(_recent(), cutoff) is True

    def test_old_timestamp_is_outside(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        assert _within_window(_old(), cutoff) is False

    def test_parse_failure_includes_row(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        assert _within_window("not-a-date", cutoff) is True

    def test_z_suffix_handled(self):
        ts = "2099-01-01T00:00:00Z"
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        assert _within_window(ts, cutoff) is True

    def test_naive_datetime_treated_as_utc(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None).isoformat()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        assert _within_window(future, cutoff) is True


# ---------------------------------------------------------------------------
# _sort_open_actions helper
# ---------------------------------------------------------------------------

class TestSortOpenActions:
    def test_due_dated_before_undated(self):
        items = [
            FakeActionItem("a1", "No due date", "open", due_date=None),
            FakeActionItem("a2", "Has due date", "open", due_date="2026-06-01"),
        ]
        sorted_items = _sort_open_actions(items)
        assert sorted_items[0].id == "a2"
        assert sorted_items[1].id == "a1"

    def test_earlier_due_date_first(self):
        items = [
            FakeActionItem("a1", "Later", "open", due_date="2026-07-01"),
            FakeActionItem("a2", "Earlier", "open", due_date="2026-06-01"),
        ]
        sorted_items = _sort_open_actions(items)
        assert sorted_items[0].id == "a2"

    def test_empty_list_returns_empty(self):
        assert _sort_open_actions([]) == []


# ---------------------------------------------------------------------------
# assemble_standup
# ---------------------------------------------------------------------------

class TestAssembleStandup:
    @pytest.mark.asyncio
    async def test_empty_project_returns_nothing_logged(self):
        store = FakeTranscriptStore()
        mock_chat = AsyncMock(return_value="Nothing logged in the last 24h.")

        result = await assemble_standup("proj-1", store, mock_chat)

        assert isinstance(result, Standup)
        assert result.done == []
        assert result.today == []
        assert result.blockers == []
        assert "Nothing logged in the last 24h." in result.summary
        # LLM should NOT be called for an empty project
        mock_chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_recent_decision_lands_in_done(self):
        store = FakeTranscriptStore(
            decisions=[FakeDecision("d1", "Use FastAPI", _recent())]
        )
        mock_chat = AsyncMock(return_value="Good progress.")

        result = await assemble_standup("proj-1", store, mock_chat)

        assert any("Decided: Use FastAPI" in item for item in result.done)

    @pytest.mark.asyncio
    async def test_old_decision_excluded_from_done(self):
        store = FakeTranscriptStore(
            decisions=[FakeDecision("d1", "Old decision", _old())]
        )
        mock_chat = AsyncMock(return_value="Summary.")

        result = await assemble_standup("proj-1", store, mock_chat)

        assert result.done == []

    @pytest.mark.asyncio
    async def test_recently_closed_action_lands_in_done(self):
        store = FakeTranscriptStore(
            action_items=[
                FakeActionItem("a1", "Write tests", "done", created_at=_recent())
            ]
        )
        mock_chat = AsyncMock(return_value="Done.")

        result = await assemble_standup("proj-1", store, mock_chat)

        assert any("Closed: Write tests" in item for item in result.done)

    @pytest.mark.asyncio
    async def test_open_action_lands_in_today(self):
        store = FakeTranscriptStore(
            action_items=[
                FakeActionItem("a1", "Review PR", "open", due_date="2026-06-01")
            ]
        )
        mock_chat = AsyncMock(return_value="Summary.")

        result = await assemble_standup("proj-1", store, mock_chat)

        assert any("Review PR" in item for item in result.today)
        assert any("due 2026-06-01" in item for item in result.today)

    @pytest.mark.asyncio
    async def test_open_action_without_due_date_no_suffix(self):
        store = FakeTranscriptStore(
            action_items=[FakeActionItem("a1", "No deadline task", "open")]
        )
        mock_chat = AsyncMock(return_value="Summary.")

        result = await assemble_standup("proj-1", store, mock_chat)

        assert result.today == ["No deadline task"]

    @pytest.mark.asyncio
    async def test_recent_risk_lands_in_blockers(self):
        store = FakeTranscriptStore(
            risks=[FakeRisk("r1", "DB migration risk", created_at=_recent())]
        )
        mock_chat = AsyncMock(return_value="Summary.")

        result = await assemble_standup("proj-1", store, mock_chat)

        assert "DB migration risk" in result.blockers

    @pytest.mark.asyncio
    async def test_no_recent_risks_falls_back_to_all_risks(self):
        """If no risks within window, all active risks are shown as blockers."""
        store = FakeTranscriptStore(
            risks=[FakeRisk("r1", "Old risk", created_at=_old())]
        )
        mock_chat = AsyncMock(return_value="Summary.")

        result = await assemble_standup("proj-1", store, mock_chat)

        assert "Old risk" in result.blockers

    @pytest.mark.asyncio
    async def test_due_dated_actions_sorted_before_undated(self):
        store = FakeTranscriptStore(
            action_items=[
                FakeActionItem("a1", "No date task", "open", due_date=None),
                FakeActionItem("a2", "Due soon", "open", due_date="2026-06-01"),
            ]
        )
        mock_chat = AsyncMock(return_value="Summary.")

        result = await assemble_standup("proj-1", store, mock_chat)

        assert result.today[0].startswith("Due soon")

    @pytest.mark.asyncio
    async def test_llm_failure_returns_fallback_summary(self):
        store = FakeTranscriptStore(
            decisions=[FakeDecision("d1", "Some decision", _recent())]
        )
        mock_chat = AsyncMock(side_effect=Exception("LLM down"))

        result = await assemble_standup("proj-1", store, mock_chat)

        # Should not raise; summary should be a count-based fallback
        assert "done" in result.summary or "action" in result.summary or "blocker" in result.summary

    @pytest.mark.asyncio
    async def test_generated_at_is_iso_string(self):
        store = FakeTranscriptStore()
        mock_chat = AsyncMock(return_value="Nothing.")

        result = await assemble_standup("proj-1", store, mock_chat)

        # Should parse without error
        datetime.fromisoformat(result.generated_at.replace("Z", "+00:00"))
