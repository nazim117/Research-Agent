# flashcards.py — flashcard generation + spaced repetition (issue #61).
#
# Two responsibilities, same "generate structured data from source text" shape
# transcript.py already established:
#   1. Generate candidate front/back flashcards from an already-ingested
#      source's full text via one LLM call, returning strict JSON.
#   2. Track each card's spaced-repetition state (SM-2 algorithm) and persist
#      it in SQLite, separate from the Qdrant-backed RAG chunks.
#
# Deliberate difference from transcript.py's re-ingest convention:
#   Re-ingesting a transcript deletes-then-replaces its structured rows,
#   because those rows have no independent state worth preserving. Flashcards
#   are the opposite — a card accumulates real review history (ease factor,
#   interval, repetitions) that must never be silently destroyed. Regenerating
#   for an already-carded source is purely additive; removing a card is a
#   separate, explicit user action (DELETE).

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import aiosqlite

logger = logging.getLogger("uvicorn.error")


@dataclass
class Flashcard:
    id: str
    project_id: str
    source: str
    front: str
    back: str
    ease_factor: float
    interval_days: int
    repetitions: int
    due_at: str
    last_reviewed_at: str | None
    created_at: str


# ---------------------------------------------------------------------------
# Generation prompt — same JSON-only convention as transcript.py's
# EXTRACTION_SYSTEM_PROMPT, so parsing stays mechanical.
# ---------------------------------------------------------------------------
GENERATION_SYSTEM_PROMPT = (
    "You write spaced-repetition flashcards from study material.\n"
    "Return ONLY a JSON object with exactly one key: \"cards\". No prose, no "
    "markdown fence, no commentary.\n\n"
    "Schema:\n"
    "{\n"
    '  "cards": [{"front": "...", "back": "..."}]\n'
    "}\n\n"
    "Rules:\n"
    "1. Each card tests ONE concept, fact, or definition — not a whole topic.\n"
    "2. \"front\" is a short question or prompt; \"back\" is the concise answer.\n"
    "3. Prefer active-recall phrasing (\"What is...\", \"Why does...\") over "
    "   fill-in-the-blank restatements of the source text.\n"
    "4. Generate at most 15 cards — the most important, testable points, not "
    "   every sentence.\n"
    "5. Output must be valid JSON parseable by Python's json.loads."
)

ChatFn = Callable[[list[dict]], Awaitable[str]]


def _parse_generation_json(raw: str) -> dict:
    """Strip optional ```json fences then json.loads. Raise ValueError on failure."""
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Flashcard generation LLM returned non-JSON: {exc}") from exc


async def generate_candidates(text: str, chat_fn: ChatFn) -> list[dict]:
    """Call the LLM and return [{"front": ..., "back": ...}, ...].

    Caller is responsible for assigning ids/SM-2 defaults and persisting.
    """
    messages = [
        {"role": "system", "content": GENERATION_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    raw_reply = await chat_fn(messages)
    parsed = _parse_generation_json(raw_reply)
    return list(parsed.get("cards") or [])


# ---------------------------------------------------------------------------
# SM-2 spaced-repetition scheduling — pure function, no I/O.
#
# quality is 0-5 (Again=0, Hard=3, Good=4, Easy=5 in the dashboard's UI).
# Below 3 means the recall failed: reset the streak and review again tomorrow.
# 3+ advances the streak: first success waits 1 day, second waits 6 days,
# every success after that multiplies the previous interval by the ease
# factor, which itself drifts based on how hard each review felt.
# ---------------------------------------------------------------------------
def schedule_review(
    ease_factor: float,
    interval_days: int,
    repetitions: int,
    quality: int,
    now: datetime | None = None,
) -> dict:
    """Return the updated {ease_factor, interval_days, repetitions, due_at,
    last_reviewed_at} for a card just reviewed with the given quality (0-5).
    """
    now = now or datetime.now(timezone.utc)

    if quality < 3:
        repetitions = 0
        interval_days = 1
    else:
        repetitions += 1
        if repetitions == 1:
            interval_days = 1
        elif repetitions == 2:
            interval_days = 6
        else:
            interval_days = round(interval_days * ease_factor)

    ease_factor = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    ease_factor = max(1.3, ease_factor)

    due_at = now + timedelta(days=interval_days)
    return {
        "ease_factor": ease_factor,
        "interval_days": interval_days,
        "repetitions": repetitions,
        "due_at": due_at.isoformat(),
        "last_reviewed_at": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
class FlashcardStore:
    """SQLite-backed store for flashcards and their spaced-repetition state."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS flashcards (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    front TEXT NOT NULL,
                    back TEXT NOT NULL,
                    ease_factor REAL NOT NULL DEFAULT 2.5,
                    interval_days INTEGER NOT NULL DEFAULT 0,
                    repetitions INTEGER NOT NULL DEFAULT 0,
                    due_at TEXT NOT NULL,
                    last_reviewed_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_flashcards_project_source "
                "ON flashcards(project_id, source)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_flashcards_project_due "
                "ON flashcards(project_id, due_at)"
            )
            await db.commit()

    async def save_generated(
        self, project_id: str, source: str, candidates: list[dict]
    ) -> list[Flashcard]:
        """Persist generated candidates as new cards, due immediately.

        Additive only — never touches existing cards for this source, so
        re-generating never destroys another card's review progress.
        """
        now = datetime.now(timezone.utc).isoformat()
        created: list[Flashcard] = []

        async with aiosqlite.connect(self.db_path) as db:
            for c in candidates:
                front = (c.get("front") or "").strip()
                back = (c.get("back") or "").strip()
                if not front or not back:
                    continue
                card = Flashcard(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    source=source,
                    front=front,
                    back=back,
                    ease_factor=2.5,
                    interval_days=0,
                    repetitions=0,
                    due_at=now,
                    last_reviewed_at=None,
                    created_at=now,
                )
                await db.execute(
                    "INSERT INTO flashcards (id, project_id, source, front, back, "
                    "ease_factor, interval_days, repetitions, due_at, "
                    "last_reviewed_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        card.id, card.project_id, card.source, card.front, card.back,
                        card.ease_factor, card.interval_days, card.repetitions,
                        card.due_at, card.last_reviewed_at, card.created_at,
                    ),
                )
                created.append(card)
            await db.commit()

        return created

    async def list_by_project(self, project_id: str) -> list[Flashcard]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, project_id, source, front, back, ease_factor, "
                "interval_days, repetitions, due_at, last_reviewed_at, created_at "
                "FROM flashcards WHERE project_id = ? ORDER BY due_at ASC",
                (project_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [Flashcard(*r) for r in rows]

    async def get(self, card_id: str) -> Flashcard | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, project_id, source, front, back, ease_factor, "
                "interval_days, repetitions, due_at, last_reviewed_at, created_at "
                "FROM flashcards WHERE id = ?",
                (card_id,),
            ) as cur:
                row = await cur.fetchone()
        return Flashcard(*row) if row else None

    async def update_review(self, card_id: str, fields: dict) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE flashcards SET ease_factor = ?, interval_days = ?, "
                "repetitions = ?, due_at = ?, last_reviewed_at = ? WHERE id = ?",
                (
                    fields["ease_factor"], fields["interval_days"],
                    fields["repetitions"], fields["due_at"],
                    fields["last_reviewed_at"], card_id,
                ),
            )
            await db.commit()

    async def update_text(self, card_id: str, front: str, back: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE flashcards SET front = ?, back = ? WHERE id = ?",
                (front, back, card_id),
            )
            await db.commit()

    async def delete(self, card_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM flashcards WHERE id = ?", (card_id,))
            await db.commit()

    async def delete_by_source(self, project_id: str, source: str) -> None:
        """Used when a source document is deleted entirely — unlike
        regenerate (additive), removing the source itself should take its
        cards with it, same as transcript.py's decisions/action_items/risks.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM flashcards WHERE project_id = ? AND source = ?",
                (project_id, source),
            )
            await db.commit()

    async def delete_by_project(self, project_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM flashcards WHERE project_id = ?", (project_id,)
            )
            await db.commit()
