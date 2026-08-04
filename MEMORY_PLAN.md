# Memory types — current state and plan

Context: this repo is a local-first research agent. Goal is to add deep
research, tool usage, and workflow capabilities. This note maps the standard
agent-memory taxonomy (short term / long term → procedural, semantic,
episodic) against what the codebase already has, what's missing, and what to
build first given that goal.

## Taxonomy

- **Short term**
  - Semantic cache
  - Working memory
- **Long term — Procedural**
  - Workflow
  - Toolbox
- **Long term — Semantic**
  - Entity memory
  - Knowledge base
  - Persona
- **Long term — Episodic**
  - Summaries
  - Conversational

## What the project already has

| Type | Status | Where |
|---|---|---|
| Working memory | Have | `memory.py` + SQLite `conversations`, scoped by `project_id`+`session_id` |
| Knowledge base | Have | `rag.py` + Qdrant `documents` collection (docs/transcripts/synced items) |
| Conversational (episodic) | Have | Qdrant `conversations` collection, semantic search over past chat |
| Summaries (episodic) | Have | `briefing.py`/`standup.py` (LLM summaries), `transcript.py` (decisions/action_items/risks) |
| Toolbox | Have | `toolbox.py` logs every tool call (`request_id`-tagged via `request_context.py`'s contextvar) and aggregates per-tool success/failure via `get_stats`/`get_stats_for_tool`. `GET /projects/{id}/toolbox/stats` exposes it read-only; the `/chat` KNOWN PROCEDURE block annotates each workflow step with its real track record, e.g. "web_search(...) [7/8 succeeded]" |
| Workflow | Partial | `workflow.py` — explicitly-authored named step sequences, project-scoped, keyword-matched into the `/chat` prompt as read-only guidance. Not derived from tool-call history (no multi-step tool loop exists yet to derive from) and steps don't auto-execute (no approval layer since `actions.py` was removed) |

## What's missing

- **Semantic cache** — no caching of repeated/near-identical embedding or LLM calls.
- **Entity memory** — no structured per-entity (person/stakeholder/ticket) store; only document chunks.
- **Persona** — no user/agent persona or preference state stored.
- **Toolbox (real/learned version)** — `toolbox.py` logs calls, but nothing
  yet reads that log to learn which tool sequences work for a given query
  shape.

## Priority given the stated goal (deep research + tool usage + workflows)

1. ~~**Workflow** (procedural)~~ — done. `workflow.py`: `workflows` +
   `workflow_steps` tables, `project_id`-scoped, explicit creation via
   `POST /projects/{id}/workflows` (steps aren't derived from tool-call
   history — the `/chat` flow only ever makes one tool call per turn today,
   so there's nothing to auto-derive from). `find_matching()` does
   keyword-substring matching against a workflow's `trigger` field and, on a
   hit, folds the stored steps into the `/chat` prompt as a read-only
   "KNOWN PROCEDURE" guidance block — same mechanism as doc chunks/web
   results. Nothing auto-executes; there's no approval layer in this
   codebase to gate that (`actions.py` was removed in `d5038c9e`).
2. ~~**Toolbox** (procedural)~~ — done. `tool_calls` gained a `request_id`
   column (reusing `request_context.py`'s existing per-request contextvar as
   the correlation key, no new plumbing through `call()`/`main.py`).
   `ToolboxStore.get_stats()`/`get_stats_for_tool()` aggregate success rate +
   avg duration per tool, project-scoped. Consumed in `/chat`: each
   KNOWN PROCEDURE step gets annotated with its real track record.
3. ~~**Working memory (extended)**~~ — done, paired with a real writer for
   it. `scratchpad.py`: `scratchpad_entries` table, `project_id`+`session_id`
   scoped, upsert-by-key. `deep_research.py`: opt-in (`ChatRequest.
   deep_research=True`) multi-step tool-calling loop for `/chat` — the model
   requests a tool via a `<<TOOL_CALL>>{...}<<END>>` text marker (llm.py has
   no native function-calling), restricted to read-only tools only
   (`web_search`, `web_fetch`, `file_read`, `file_list`, `memory_get`,
   `memory_list` — no approval gate exists in this codebase since
   `actions.py` was removed in `d5038c9e`, so write tools stay off-limits to
   the autonomous loop). Guarded against runaway loops by a hard step cap
   (`settings.deep_research_max_steps`, default 5) and a repeat-call guard
   (same `(tool, args)` pair twice forces one final non-tool-calling answer
   instead of continuing). Every step is written to the scratchpad; it's
   loop-only working memory for this pass — cleared at the start of each
   research task, not surfaced into later plain chat turns, inspectable via
   `GET /projects/{id}/scratchpad?session_id=...`.
4. ~~**Semantic cache**~~ — done, scoped to `embed()` only (not `llm.chat()`
   replies — reply content depends on the full assembled prompt, which
   varies turn to turn, so "similar query" isn't a safe cache key without a
   real similarity threshold and staleness policy; deferred).
   `semantic_cache.py`: `SemanticCacheStore` (`embedding_cache` table,
   `UNIQUE(project_id, text_hash)`) + `embed_cached()` orchestration
   (cache-miss calls the real `embed()`, then stores the result). Cache key
   is `sha256(text.strip())` — exact match only, no fuzzy/similarity lookup
   (embedding a query to check the embedding cache would be circular).
   Wired into `main.py`'s three `embed()` call sites and `rag.py`'s
   `retrieve()` (`cache` param). Concrete win: `/chat` was embedding
   `req.message` twice per turn (once directly, once inside
   `rag.retrieve()`) — now the second call is a guaranteed cache hit, no
   call-site refactor needed. See TICKET-0003.
5. ~~**Entity memory**~~ — done. `entity.py`: `entities` table
   (`project_id`, `name`, `type`, `attributes`, `sources` (JSON list),
   `created_at`, `updated_at`), unique on `(project_id, name, type)`.
   `EntityStore.upsert_entity()` merges on repeat sightings — unions the new
   `source` into `sources`, overwrites `attributes` only when the new text
   is non-empty (last-write-wins prose) — one consistent row per entity
   instead of a scattered per-source log. Extraction (`extract_entities()`)
   is a standalone LLM call independent of `transcript.py`'s
   decisions/action_items/risks extraction, since entities come from both
   transcripts *and* plain documents while those three only ever come from
   transcripts; wired into all four ingest paths (`POST /ingest`,
   `/ingest/transcript`, `/ingest/file`, `/ingest/url`) via
   `_process_document_entities()` in `main.py`, non-fatal on extraction
   failure (chunk/embed storage already succeeded by that point).
   `EntityStore.find_matching()` does the same case-insensitive
   substring-match heuristic as `WorkflowStore.find_matching`, folding hits
   into a non-cited "--- KNOWN ENTITIES ---" `/chat` system block (background
   context, not a citable source) — passive only, no `entity_get`/
   `entity_list` tools added to `deep_research.py`'s `ALLOWED_TOOLS` (its
   dispatch routes 100% of tools through `mcp.call(...)`; adding a
   chat-agent-local tool would be the first local-dispatch branch in an
   otherwise fully mcp-proxied loop — deferred until a concrete need
   emerges). `GET /projects/{id}/entities` exposes read-only listing.

Deprioritized: **Persona** (no stated need); **Conversational/Summaries**
(already covered by existing `conversations` collection + `briefing.py`/`standup.py`).

## Database engine

Stay on SQLite. All new tables (workflow, toolbox, entity memory, semantic
cache) are relational and `project_id`-scoped, same shape as existing
`conversations`/`documents`/`decisions` tables. This is a local-first,
single-user tool — write volume is nowhere near SQLite's ceiling, and
switching to Postgres would add a new Docker service, connection pooling, and
migration tooling for a concurrency problem this project doesn't have.
SQLite's FTS5 covers fuzzy/full-text lookup if the semantic cache needs it.
Reconsider only if the project moves to multi-user/hosted deployment with
concurrent writers.

## Memory manager

Not needed yet, but will be once workflow/toolbox/entity/cache land. Today
`main.py`'s chat flow inlines calls to `memory.py` (conversation history) and
`vectors.py`/`rag.py` (RAG) directly — manageable with only two memory
sources. Adding four more on top makes that inline orchestration unwieldy,
and it's the exact spot `project_id` scoping bugs would creep in (this repo's
core invariant per CLAUDE.md — every read/write must filter by project).

A memory manager module (e.g. `memory_manager.py`) centralizing "given a
query + project_id, fetch relevant working/episodic/entity/cache hits, and
expose write paths for workflow/toolbox logging" would:

- keep `project_id` filtering enforced in one place instead of 5+ call sites
- keep `main.py`'s prompt-building declarative instead of growing another
  200 lines of retrieval logic
- make it easy to add/remove memory types later without touching route code

Tradeoff: it's an abstraction layer before there's concrete usage patterns
for the new types — premature to build now. Build workflow + toolbox first,
see how `main.py` wants to consume them, then extract the manager once the
inline version actually gets messy rather than designing it upfront.
