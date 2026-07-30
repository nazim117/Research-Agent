# transcript.py — meeting-transcript ingest + structured extraction (Step 10).
#
# Two responsibilities:
#   1. Persist raw transcript text into RAG (same path as POST /ingest) so
#      semantic search picks it up alongside other ingested documents.
#   2. Run a separate LLM call that returns three structured lists —
#      decisions, action items, risks — and store them in dedicated SQLite
#      tables.  The structured tables answer questions that pure RAG cannot:
#      "what action items are still open?" needs an exact filter, not a fuzzy
#      vector search.
#
# Why two phases?
#   Phase 1 (RAG ingest) makes the transcript searchable in chat.
#   Phase 2 (LLM extraction) turns the freeform text into rows you can query
#   with SQL.  The two phases use different prompts and different storage and
#   serve different downstream consumers — so they live in separate code
#   paths even though both run from the same /ingest/transcript request.
#
# Naming note:
#   Transcript action items (commitments made in a meeting) live in their own
#   table called `action_items`, distinct from any other use of the word
#   "action" elsewhere in the codebase.
#
# Idempotency:
#   POST /ingest/transcript with the same `source` replaces all prior content
#   for that source — both vector chunks (vstore.delete_by_source) and the
#   structured rows (delete_by_source on each table).  Re-posting a transcript
#   does not duplicate rows.

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

import aiosqlite

logger = logging.getLogger("uvicorn.error")


# ---------------------------------------------------------------------------
# Dataclasses — one per structured-row type.
# ---------------------------------------------------------------------------
@dataclass
class Decision:
    id: str
    project_id: str
    source: str
    text: str
    created_at: str


@dataclass
class ActionItem:
    id: str
    project_id: str
    source: str
    owner: str | None       # who is responsible (free-form name, may be None)
    text: str               # what they committed to
    due_date: str | None    # ISO date string, may be None
    status: str             # 'open' or 'done'
    created_at: str


@dataclass
class Risk:
    id: str
    project_id: str
    source: str
    text: str
    created_at: str


# ---------------------------------------------------------------------------
# Extraction prompt — LLM is instructed to return strict JSON.
#
# We ask for JSON (not free prose) so parsing is mechanical: the response is
# either valid JSON we can `json.loads()` or it is malformed and we surface a
# 502.  No regex-scraping of natural language.
# ---------------------------------------------------------------------------
EXTRACTION_SYSTEM_PROMPT = (
    "You extract structured information from meeting transcripts.\n"
    "Return ONLY a JSON object with exactly three keys: \"decisions\", "
    "\"action_items\", \"risks\". No prose, no markdown fence, no commentary.\n\n"
    "Schema:\n"
    "{\n"
    '  "decisions": [{"text": "..."}],\n'
    '  "action_items": [{"owner": "...|null", "text": "...", "due_date": "YYYY-MM-DD|null"}],\n'
    '  "risks":     [{"text": "..."}]\n'
    "}\n\n"
    "Rules:\n"
    "1. A decision is a concrete choice the meeting agreed on (not a topic).\n"
    "2. An action item is a commitment by a specific person (or unassigned). "
    "   Owner is the person's name as it appears in the transcript, or null.\n"
    "3. A risk is a flagged uncertainty, blocker, or threat to the work.\n"
    "4. Empty list is fine if a category has no entries. Always include all "
    "   three keys.\n"
    "5. Output must be valid JSON parseable by Python's json.loads."
)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
ChatFn = Callable[[list[dict]], Awaitable[str]]


def _parse_extraction_json(raw: str) -> dict:
    """Strip optional ```json fences then json.loads. Raise ValueError on failure.

    Some models wrap JSON in ```json ... ``` despite the prompt asking otherwise,
    so we strip a leading/trailing fence before parsing.
    """
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Extraction LLM returned non-JSON: {exc}") from exc


async def extract_structured(text: str, chat_fn: ChatFn) -> dict:
    """Call the LLM and return {"decisions": [...], "action_items": [...], "risks": [...]}.

    Each item is a dict matching the schema in EXTRACTION_SYSTEM_PROMPT.
    Caller is responsible for assigning ids and persisting.
    """
    messages = [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {"role": "user",   "content": text},
    ]
    raw_reply = await chat_fn(messages)
    parsed = _parse_extraction_json(raw_reply)

    # Defensive normalisation — guarantee all three keys exist as lists even if
    # the model omitted one.  Downstream code never has to defend against missing
    # keys.
    return {
        "decisions":    list(parsed.get("decisions") or []),
        "action_items": list(parsed.get("action_items") or []),
        "risks":        list(parsed.get("risks") or []),
    }


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
class TranscriptStore:
    """SQLite-backed store for the three structured-extraction tables.

    All three tables share the same shape: (id, project_id, source, ..., created_at).
    The `source` column lets us implement idempotent re-ingest by deleting all
    prior rows for the same (project_id, source) before inserting new ones.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        """Create the three tables + indexes if they do not yet exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_decisions_project "
                "ON decisions(project_id, source)"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS action_items (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    owner TEXT,
                    text TEXT NOT NULL,
                    due_date TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_action_items_project "
                "ON action_items(project_id, source)"
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS risks (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_risks_project "
                "ON risks(project_id, source)"
            )
            await db.commit()

    # -- delete helpers --------------------------------------------------------
    async def delete_by_source(self, project_id: str, source: str) -> None:
        """Wipe all rows for one (project_id, source).  Used before re-ingest."""
        async with aiosqlite.connect(self.db_path) as db:
            for table in ("decisions", "action_items", "risks"):
                await db.execute(
                    f"DELETE FROM {table} WHERE project_id = ? AND source = ?",
                    (project_id, source),
                )
            await db.commit()

    async def delete_by_project(self, project_id: str) -> None:
        """Wipe all rows for one project — cascade on project delete."""
        async with aiosqlite.connect(self.db_path) as db:
            for table in ("decisions", "action_items", "risks"):
                await db.execute(
                    f"DELETE FROM {table} WHERE project_id = ?",
                    (project_id,),
                )
            await db.commit()

    # -- save helpers ----------------------------------------------------------
    async def save_extracted(
        self,
        project_id: str,
        source: str,
        extracted: dict,
    ) -> dict:
        """Persist the dict returned by extract_structured(). Returns counts.

        Caller has already deleted prior rows for (project_id, source) via
        delete_by_source if running an idempotent re-ingest.
        """
        now = datetime.now(timezone.utc).isoformat()
        counts = {"decisions": 0, "action_items": 0, "risks": 0}

        async with aiosqlite.connect(self.db_path) as db:
            for d in extracted.get("decisions", []):
                text = (d.get("text") or "").strip()
                if not text:
                    continue
                await db.execute(
                    "INSERT INTO decisions (id, project_id, source, text, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), project_id, source, text, now),
                )
                counts["decisions"] += 1

            for a in extracted.get("action_items", []):
                text = (a.get("text") or "").strip()
                if not text:
                    continue
                owner = a.get("owner")
                due = a.get("due_date")
                # Normalise the string "null" the model sometimes emits.
                if owner in (None, "null", ""):
                    owner = None
                if due in (None, "null", ""):
                    due = None
                await db.execute(
                    "INSERT INTO action_items (id, project_id, source, owner, "
                    "text, due_date, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), project_id, source, owner, text, due,
                     "open", now),
                )
                counts["action_items"] += 1

            for r in extracted.get("risks", []):
                text = (r.get("text") or "").strip()
                if not text:
                    continue
                await db.execute(
                    "INSERT INTO risks (id, project_id, source, text, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), project_id, source, text, now),
                )
                counts["risks"] += 1

            await db.commit()

        return counts

    # -- read helpers ----------------------------------------------------------
    async def list_decisions(self, project_id: str) -> list[Decision]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, project_id, source, text, created_at FROM decisions "
                "WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [Decision(*r) for r in rows]

    async def list_action_items(
        self, project_id: str, status: str | None = None,
    ) -> list[ActionItem]:
        sql = (
            "SELECT id, project_id, source, owner, text, due_date, status, created_at "
            "FROM action_items WHERE project_id = ?"
        )
        params: tuple = (project_id,)
        if status:
            sql += " AND status = ?"
            params = (project_id, status)
        sql += " ORDER BY created_at DESC"
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(sql, params) as cur:
                rows = await cur.fetchall()
        return [ActionItem(*r) for r in rows]

    async def list_risks(self, project_id: str) -> list[Risk]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, project_id, source, text, created_at FROM risks "
                "WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [Risk(*r) for r in rows]
