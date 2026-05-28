# standup.py — daily standup assembler.
#
# Produces a Yesterday/Today/Blockers view for a single project, scoped to the
# last WINDOW_HOURS hours.  Draws only on the structured SQLite tables from
# transcript extraction (no RAG) so it stays fast on local hardware.
#
# Why no RAG here?
#   Briefing (Step 11) already handles the "fuzzy context restore" use case.
#   A standup is time-bounded and action-oriented: the right data is already
#   in the decisions/action_items/risks tables, where SQL time-filtering is
#   exact.  Pulling document chunks would add latency without adding precision.
#
# Window note:
#   "Recently done" action items are those with status='done' AND created_at
#   within the window — not a closed_at stamp (that column doesn't exist yet).
#   This is a known approximation: an action created weeks ago but just closed
#   today won't appear in the Done bucket.  Acceptable for the daily standup.

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger("uvicorn.error")

WINDOW_HOURS = 24


@dataclass
class Standup:
    summary: str
    done: list[str]      # decisions made + actions closed within the window
    today: list[str]     # open action items, due-dated first
    blockers: list[str]  # risks in window; falls back to all risks if none
    generated_at: str


ChatFn = Callable[[list[dict]], Awaitable[str]]

STANDUP_SYSTEM_PROMPT = (
    "You are a project assistant writing a daily standup summary for a project manager.\n"
    "You are given three lists: done items, today's open tasks, and active blockers.\n"
    "Write 1-2 plain-English sentences that tie these together as a standup lead-in.\n"
    "Be concise and direct — this is a status update, not an essay.\n"
    "If all lists are empty, write exactly: 'Nothing logged in the last 24h.'\n\n"
    "Output ONLY the summary sentences, no markdown, no preamble."
)


def _within_window(created_at: str, cutoff: datetime) -> bool:
    """Return True if created_at (ISO string) is >= cutoff.

    Treats parse failures as "include" so no rows are silently dropped.
    Handles both +00:00 and Z suffix for Python < 3.11 compatibility.
    """
    try:
        ts = created_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff
    except (ValueError, TypeError):
        return True


def _sort_open_actions(items: list[Any]) -> list[Any]:
    """Due-dated items first (earliest due date), undated last."""
    return sorted(items, key=lambda a: (0, a.due_date) if a.due_date else (1, ""))


async def assemble_standup(
    project_id: str,
    transcript_store: Any,
    chat_fn: ChatFn,
) -> Standup:
    """Assemble the three-bucket standup for the last 24h."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_HOURS)

    all_decisions = await transcript_store.list_decisions(project_id)
    all_items = await transcript_store.list_action_items(project_id, status=None)
    all_risks = await transcript_store.list_risks(project_id)

    # Done bucket: decisions + closed actions, both within the window.
    done: list[str] = []
    for d in all_decisions:
        if _within_window(d.created_at, cutoff):
            done.append(f"Decided: {d.text}")
    for a in all_items:
        if a.status == "done" and _within_window(a.created_at, cutoff):
            done.append(f"Closed: {a.text}")

    # Today bucket: all open actions, due-dated sorted first.
    open_items = [a for a in all_items if a.status == "open"]
    today: list[str] = []
    for a in _sort_open_actions(open_items):
        if a.due_date:
            today.append(f"{a.text} (due {a.due_date})")
        else:
            today.append(a.text)

    # Blockers bucket: risks in window; fall back to ALL active risks so the
    # bucket is never silently empty when there are known risks.
    recent_risks = [r for r in all_risks if _within_window(r.created_at, cutoff)]
    blocker_source = recent_risks if recent_risks else all_risks
    blockers: list[str] = [r.text for r in blocker_source]

    has_content = done or today or blockers

    if not has_content:
        return Standup(
            summary="Nothing logged in the last 24h.",
            done=[],
            today=[],
            blockers=[],
            generated_at=now.isoformat(),
        )

    summary = await _generate_summary(done, today, blockers, chat_fn)

    return Standup(
        summary=summary,
        done=done,
        today=today,
        blockers=blockers,
        generated_at=now.isoformat(),
    )


async def _generate_summary(
    done: list[str],
    today: list[str],
    blockers: list[str],
    chat_fn: ChatFn,
) -> str:
    """Ask the LLM for a 1-2 sentence standup lead-in."""
    parts: list[str] = []
    if done:
        parts.append("Done: " + "; ".join(done[:5]))
    if today:
        parts.append("Today/Next: " + "; ".join(today[:5]))
    if blockers:
        parts.append("Blockers: " + "; ".join(blockers[:3]))

    user_prompt = "Standup data:\n---\n" + "\n".join(parts) + "\n---\nWrite the standup lead-in."

    messages = [
        {"role": "system", "content": STANDUP_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        summary = await chat_fn(messages)
        return summary.strip()
    except Exception as e:
        logger.warning(f"LLM standup summary failed: {e}")
        return (
            f"{len(done)} done, {len(today)} open action(s), {len(blockers)} blocker(s) in the last 24h."
        )
