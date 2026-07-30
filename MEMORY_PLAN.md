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
| Toolbox | Partial | mcp-server `registry.go` tool list — static config, not learned/adaptive |

## What's missing

- **Semantic cache** — no caching of repeated/near-identical embedding or LLM calls.
- **Entity memory** — no structured per-entity (person/stakeholder/ticket) store; only document chunks.
- **Persona** — no user/agent persona or preference state stored.
- **Workflow** — no stored "steps taken to do X" for reuse/replay.
- **Toolbox (real version)** — current tool list is static; no memory of which tool worked, in what context, with what result.

## Priority given the stated goal (deep research + tool usage + workflows)

1. **Workflow** (procedural) — top priority, directly requested. New table:
   steps, tool calls per step, `project_id`, trigger condition. Lets the
   agent replay/adapt known procedures (e.g. "weekly PM briefing") instead of
   re-deriving them each time.
2. **Toolbox** (procedural) — top priority, directly requested. Table:
   tool name, args pattern, success/failure, `project_id`. Lets the agent
   learn which tool sequences work for a given query shape, not just call
   from a fixed list.
3. **Working memory (extended)** — needed as scaffolding for multi-step
   research/tool use: a scratchpad across steps within one research task,
   distinct from the existing conversation history and from finalized
   RAG-ingested content.
4. **Semantic cache** — high leverage for deep research: multi-step research
   reissues similar sub-queries; caching by normalized query hash cuts cost
   and latency.
5. **Entity memory** — supports deep research: structured tracking of
   research subjects/sources across a session so findings tie back to
   consistent entities instead of scattered citations.

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
