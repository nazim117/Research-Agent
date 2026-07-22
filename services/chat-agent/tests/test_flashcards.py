# test_flashcards.py — issue #61: flashcard generation + spaced repetition.
#
# Covers: FlashcardStore CRUD/idempotency, generate_candidates parsing,
# schedule_review (SM-2) exhaustively, and the HTTP routes.

import json
import sys
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flashcards import (
    generate_candidates,
    _parse_generation_json,
    schedule_review,
    FlashcardStore,
)


class TestParseGenerationJson:
    def test_parses_clean_json(self):
        raw = '{"cards": [{"front": "What is RAG?", "back": "Retrieval-augmented generation"}]}'
        result = _parse_generation_json(raw)
        assert result["cards"][0]["front"] == "What is RAG?"

    def test_strips_json_fence(self):
        raw = '```json\n{"cards": []}\n```'
        result = _parse_generation_json(raw)
        assert result["cards"] == []

    def test_strips_json_fence_no_lang(self):
        raw = '```\n{"cards": [{"front": "Q", "back": "A"}]}\n```'
        result = _parse_generation_json(raw)
        assert len(result["cards"]) == 1

    def test_raises_on_malformed(self):
        with pytest.raises(ValueError, match="non-JSON"):
            _parse_generation_json("not json at all")


class TestGenerateCandidates:
    @pytest.mark.asyncio
    async def test_returns_cards_key(self):
        mock_chat = AsyncMock(
            return_value=json.dumps({"cards": [{"front": "Q1", "back": "A1"}]})
        )
        result = await generate_candidates("some study text", mock_chat)
        assert result == [{"front": "Q1", "back": "A1"}]

    @pytest.mark.asyncio
    async def test_missing_cards_key_defaults_to_empty(self):
        mock_chat = AsyncMock(return_value="{}")
        result = await generate_candidates("text", mock_chat)
        assert result == []


class TestScheduleReview:
    """Exhaustive SM-2 behavior tests — pure function, no I/O."""

    def test_quality_below_3_resets_repetitions_and_interval(self):
        result = schedule_review(
            ease_factor=2.5, interval_days=10, repetitions=4, quality=1,
        )
        assert result["repetitions"] == 0
        assert result["interval_days"] == 1

    def test_first_success_waits_one_day(self):
        result = schedule_review(
            ease_factor=2.5, interval_days=0, repetitions=0, quality=4,
        )
        assert result["repetitions"] == 1
        assert result["interval_days"] == 1

    def test_second_success_waits_six_days(self):
        result = schedule_review(
            ease_factor=2.5, interval_days=1, repetitions=1, quality=4,
        )
        assert result["repetitions"] == 2
        assert result["interval_days"] == 6

    def test_third_success_scales_by_ease_factor(self):
        result = schedule_review(
            ease_factor=2.5, interval_days=6, repetitions=2, quality=4,
        )
        assert result["repetitions"] == 3
        assert result["interval_days"] == round(6 * result["ease_factor"])

    def test_ease_factor_has_a_floor_of_1_3(self):
        ease = 1.3
        for _ in range(20):
            result = schedule_review(
                ease_factor=ease, interval_days=1, repetitions=3, quality=0,
            )
            ease = result["ease_factor"]
        assert ease >= 1.3

    def test_perfect_quality_increases_ease_factor(self):
        result = schedule_review(
            ease_factor=2.5, interval_days=1, repetitions=1, quality=5,
        )
        assert result["ease_factor"] > 2.5

    def test_due_at_and_last_reviewed_at_are_set(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = schedule_review(
            ease_factor=2.5, interval_days=0, repetitions=0, quality=4, now=now,
        )
        assert result["last_reviewed_at"] == now.isoformat()
        assert result["due_at"] == (now + timedelta(days=1)).isoformat()


class TestFlashcardStoreInit:
    @pytest.mark.asyncio
    async def test_creates_table_and_indexes(self, tmp_path):
        store = FlashcardStore(str(tmp_path / "test.db"))
        await store.init()

        import sqlite3

        conn = sqlite3.connect(str(tmp_path / "test.db"))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        conn.close()

        assert "flashcards" in tables
        assert any("flashcards" in i for i in indexes)


class TestFlashcardStoreCrud:
    @pytest.mark.asyncio
    async def test_save_generated_and_list(self, tmp_path):
        store = FlashcardStore(str(tmp_path / "test.db"))
        await store.init()

        created = await store.save_generated(
            "proj-1", "notes.md",
            [{"front": "What is SM-2?", "back": "A spaced-repetition algorithm"}],
        )
        assert len(created) == 1
        assert created[0].ease_factor == 2.5
        assert created[0].repetitions == 0

        cards = await store.list_by_project("proj-1")
        assert len(cards) == 1
        assert cards[0].front == "What is SM-2?"

    @pytest.mark.asyncio
    async def test_save_generated_skips_blank_cards(self, tmp_path):
        store = FlashcardStore(str(tmp_path / "test.db"))
        await store.init()

        created = await store.save_generated(
            "proj-1", "notes.md",
            [{"front": "", "back": "A"}, {"front": "Q", "back": ""}, {"front": "Q2", "back": "A2"}],
        )
        assert len(created) == 1
        assert created[0].front == "Q2"

    @pytest.mark.asyncio
    async def test_regenerate_is_additive_not_replacing(self, tmp_path):
        """Core behavior: generating twice for the same source must not
        touch the first batch's review progress."""
        store = FlashcardStore(str(tmp_path / "test.db"))
        await store.init()

        first = await store.save_generated(
            "proj-1", "notes.md", [{"front": "Q1", "back": "A1"}]
        )
        # Simulate real review progress on the first card.
        fields = schedule_review(2.5, 0, 0, 5)
        await store.update_review(first[0].id, fields)

        await store.save_generated(
            "proj-1", "notes.md", [{"front": "Q2", "back": "A2"}]
        )

        cards = await store.list_by_project("proj-1")
        assert len(cards) == 2
        reviewed = next(c for c in cards if c.id == first[0].id)
        assert reviewed.repetitions == 1
        assert reviewed.ease_factor == fields["ease_factor"]

    @pytest.mark.asyncio
    async def test_update_text(self, tmp_path):
        store = FlashcardStore(str(tmp_path / "test.db"))
        await store.init()
        created = await store.save_generated("proj-1", "s", [{"front": "Q", "back": "A"}])

        await store.update_text(created[0].id, "Fixed Q", "Fixed A")

        card = await store.get(created[0].id)
        assert card.front == "Fixed Q"
        assert card.back == "Fixed A"

    @pytest.mark.asyncio
    async def test_delete(self, tmp_path):
        store = FlashcardStore(str(tmp_path / "test.db"))
        await store.init()
        created = await store.save_generated("proj-1", "s", [{"front": "Q", "back": "A"}])

        await store.delete(created[0].id)

        assert await store.get(created[0].id) is None

    @pytest.mark.asyncio
    async def test_delete_by_source_only_affects_that_source(self, tmp_path):
        store = FlashcardStore(str(tmp_path / "test.db"))
        await store.init()
        await store.save_generated("proj-1", "a.md", [{"front": "Q1", "back": "A1"}])
        await store.save_generated("proj-1", "b.md", [{"front": "Q2", "back": "A2"}])

        await store.delete_by_source("proj-1", "a.md")

        remaining = await store.list_by_project("proj-1")
        assert len(remaining) == 1
        assert remaining[0].source == "b.md"

    @pytest.mark.asyncio
    async def test_delete_by_project_cascades(self, tmp_path):
        store = FlashcardStore(str(tmp_path / "test.db"))
        await store.init()
        await store.save_generated("proj-to-delete", "s", [{"front": "Q", "back": "A"}])

        await store.delete_by_project("proj-to-delete")

        assert await store.list_by_project("proj-to-delete") == []

    @pytest.mark.asyncio
    async def test_list_empty_project(self, tmp_path):
        store = FlashcardStore(str(tmp_path / "test.db"))
        await store.init()
        assert await store.list_by_project("nonexistent") == []


# ---------------------------------------------------------------------------
# Route-level tests — mock rag/chat/flashcard_store, hit the real app.
# ---------------------------------------------------------------------------

FAKE_PROJECT_ID = "proj-test-1234"


def _make_chunk(text: str, index: int):
    from rag import Chunk
    return Chunk(score=0.9, source="notes.md", chunk_index=index, text=text)


@pytest.mark.asyncio
async def test_generate_route_builds_full_text_and_saves_cards():
    with (
        patch("main._require_project", new_callable=AsyncMock),
        patch("main.rag.retrieve_by_source", new_callable=AsyncMock) as retrieve,
        patch("main.chat", new_callable=AsyncMock) as chat_fn,
        patch("main.flashcard_store") as store,
    ):
        retrieve.return_value = [_make_chunk("chunk two", 1), _make_chunk("chunk one ", 0)]
        chat_fn.return_value = json.dumps({"cards": [{"front": "Q", "back": "A"}]})

        from flashcards import Flashcard
        now = datetime.now(timezone.utc).isoformat()
        store.save_generated = AsyncMock(return_value=[
            Flashcard(
                id="card-1", project_id=FAKE_PROJECT_ID, source="notes.md",
                front="Q", back="A", ease_factor=2.5, interval_days=0,
                repetitions=0, due_at=now, last_reviewed_at=None, created_at=now,
            )
        ])

        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/projects/{FAKE_PROJECT_ID}/sources/notes.md/flashcards/generate")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["front"] == "Q"
    # Chunks are joined in chunk_index order regardless of retrieval order.
    sent_text = chat_fn.call_args.args[0][1]["content"]
    assert sent_text == "chunk one chunk two"


@pytest.mark.asyncio
async def test_generate_route_404s_when_source_has_no_chunks():
    with (
        patch("main._require_project", new_callable=AsyncMock),
        patch("main.rag.retrieve_by_source", new_callable=AsyncMock, return_value=[]),
    ):
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/projects/{FAKE_PROJECT_ID}/sources/missing.md/flashcards/generate")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_review_route_applies_sm2_and_persists():
    from flashcards import Flashcard
    now = datetime.now(timezone.utc).isoformat()
    card = Flashcard(
        id="card-1", project_id=FAKE_PROJECT_ID, source="notes.md",
        front="Q", back="A", ease_factor=2.5, interval_days=0,
        repetitions=0, due_at=now, last_reviewed_at=None, created_at=now,
    )
    reviewed_card = Flashcard(**{**card.__dict__, "repetitions": 1, "interval_days": 1})

    with patch("main.flashcard_store") as store:
        store.get = AsyncMock(side_effect=[card, reviewed_card])
        store.update_review = AsyncMock()

        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/flashcards/card-1/review", json={"quality": 4})

    assert resp.status_code == 200
    store.update_review.assert_awaited_once()
    called_card_id, called_fields = store.update_review.call_args.args
    assert called_card_id == "card-1"
    assert called_fields["repetitions"] == 1


@pytest.mark.asyncio
async def test_review_route_404s_for_unknown_card():
    with patch("main.flashcard_store") as store:
        store.get = AsyncMock(return_value=None)
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/flashcards/nope/review", json={"quality": 4})

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_review_route_rejects_out_of_range_quality():
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/flashcards/card-1/review", json={"quality": 9})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_flashcard_route():
    from flashcards import Flashcard
    now = datetime.now(timezone.utc).isoformat()
    card = Flashcard(
        id="card-1", project_id=FAKE_PROJECT_ID, source="s",
        front="Q", back="A", ease_factor=2.5, interval_days=0,
        repetitions=0, due_at=now, last_reviewed_at=None, created_at=now,
    )
    with patch("main.flashcard_store") as store:
        store.get = AsyncMock(return_value=card)
        store.delete = AsyncMock()
        from main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete("/flashcards/card-1")

    assert resp.status_code == 200
    store.delete.assert_awaited_once_with("card-1")
