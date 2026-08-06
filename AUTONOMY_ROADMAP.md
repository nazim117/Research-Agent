# Autonomy roadmap — current state and next steps

Context: goal is to move the research agent from "answers when asked" toward
autonomous operation. This note tracks what's built, what's missing, and
what to build next. See also `MEMORY_PLAN.md` (memory-type taxonomy) and
`tickets/` (individual feature decisions).

## What's built

| Capability | Status | Where |
|---|---|---|
| Multi-step tool-calling loop | Have | `deep_research.py` — opt-in (`ChatRequest.deep_research=True`), text-marker tool calls (`<<TOOL_CALL>>...<<END>>`), hard step cap (`settings.deep_research_max_steps`, default 5), repeat-call guard |
| Read tools (auto-executed) | Have | `web_search`, `web_fetch`, `file_read`, `file_list`, `memory_get`, `memory_list` (`deep_research.py`'s `READ_TOOLS`) |
| Write tools (approval-gated) | Have | `memory_set`, `file_write`, `http_request` (`WRITE_TOOLS`) — requesting one creates a pending row via `actions.py`'s `ActionStore`, never auto-executes. Human approves via `POST /projects/{id}/actions/{id}/approve`, which calls `execute_action()` → real `mcp.call()`. Reject is terminal. No auto-approve path exists anywhere. |
| Per-tool learned stats | Have (logging only) | `toolbox.py` — `tool_calls` table, `get_stats()`/`get_stats_for_tool()` aggregate success rate/avg duration per project. Surfaced into `/chat` prompt as descriptive text; nothing consumes it for decisions yet. |
| Static procedural memory | Have | `workflow.py` — hand-authored name/trigger/steps, keyword-matched into the `/chat` prompt as read-only guidance. Not derived from tool-call history; nothing auto-executes. |
| Durable task/goal persistence | Have | `tasks.py` — `agent_tasks`/`agent_task_steps`, replaces the old ephemeral `scratchpad.py` (deleted). `run_research_loop` creates a task (`status='running'`) the instant a `deep_research=true` request starts, durably logs every step, and marks it `done`/`failed` on exit — never cleared, survives restarts. `parent_task_id` is a forward-compat hook for future sub-agents (unused today). `GET /projects/{id}/tasks`, `GET /projects/{id}/tasks/{id}` expose it read-only. |

## What's missing

1. **Background/scheduled execution.** No cron/APScheduler/Celery/
   `BackgroundTasks` anywhere in the repo (confirmed by search). Every
   agent action today runs inside a single `/chat` request/response cycle —
   nothing fires on its own. This is the core piece that makes the system
   "runs when asked" rather than autonomous.

2. **Native tool-calling instead of text markers.** `deep_research.py` uses
   a regex convention (`<<TOOL_CALL>>{...}<<END>>`) because `llm.py` sends
   no `tools` field to either backend. Fragile — a malformed or missing
   marker is indistinguishable from a normal reply without the parser.
   OpenAI-compatible providers (`LLM_PROVIDER=openai_compatible`) support
   real function-calling; worth switching to it there.

3. **Toolbox stats not used for decisions.** `toolbox.py` logs and
   aggregates success/failure per tool but nothing reads it to skip
   consistently-failing tools or bias tool choice. Pure display today.

4. **Fixed, unintelligent step cap.** `deep_research_max_steps` is one
   constant (default 5) regardless of task complexity or cost budget. A
   real autonomous loop needs a smarter budget — e.g. per-task-type caps or
   a cost/time ceiling — rather than one number for every request.

5. **No auto-approval policy.** The write-action gate requires a human to
   click approve for every single write, with no exceptions. Real autonomy
   needs a *policy* layer (e.g. auto-approve low-risk/rate-limited/
   cost-capped actions) instead of an always-manual gate — this is a
   trust/safety decision, not just an engineering task, and needs explicit
   user sign-off before building.

6. **Workflows aren't learned.** `workflow.py` steps are 100% hand-authored;
   nothing derives a reusable procedure from a `deep_research` run that
   actually succeeded. Explicit gap noted in `MEMORY_PLAN.md`.

7. **Sub-agents.** `deep_research.py` runs one sequential loop per request.
   A future architecture could spin off parallel/specialized research
   sub-loops (e.g. one sub-task per research angle) for speed and depth.
   `tasks.py`'s `agent_tasks.parent_task_id` column exists as a forward-compat
   hook for this — a child task would link to its coordinating task — but no
   code creates one yet. Orthogonal to persistence; not required for any
   other item on this list.

## Suggested build order

1. ~~**Task/goal persistence**~~ — done. `tasks.py`.
2. **Background/scheduled execution** (in-process APScheduler) — run
   workflows/tasks on a cron trigger without a user-initiated chat turn.
   Builds directly on `tasks.py`: a scheduled run creates an `agent_tasks`
   row the same way `deep_research.py` does today.
3. **Toolbox-informed loop control** — use `get_stats()` to skip/deprioritize
   poorly-performing tools inside `deep_research.py`. Small, low-risk,
   no new tables.
4. **Native function-calling** for the `openai_compatible` backend —
   replaces the text-marker convention where the provider supports it,
   `ollama` keeps the marker convention as fallback.
5. **Auto-approval policy** — deliberately last; requires explicit
   user decision on risk tiers before any code changes.
6. **Workflow auto-derivation** from successful `deep_research` traces —
   nice-to-have, no urgency.
7. **Sub-agents** — no urgency; revisit once single-loop research quality
   is the actual bottleneck.

## Database engine

Stays SQLite, same as `MEMORY_PLAN.md`'s reasoning — local-first,
single-user, no concurrent-writer problem to justify Postgres.
