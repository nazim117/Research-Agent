# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**Research Agent** — a local-first AI assistant for project managers. It keeps
project-scoped memory, ingests documents/transcripts, retrieves context with
RAG, and syncs Jira/GitHub work items into a private per-project knowledge
base. The repo name ("Cost-aware AI Agent execution engine") reflects an
earlier project; that original execution-engine architecture (`gateway`,
`policy-engine`, `agent-executor`) has been removed from the repo. The active
system is the stack described below.

## Active stack

| Component | Path | Language | Port |
|---|---|---|---|
| chat-agent | `services/chat-agent/` | Python / FastAPI | 8080 |
| mcp-server | `services/mcp-server/` | Go | 8083 |
| qdrant | Docker image `qdrant/qdrant` | — | 6333 |
| embeddings | Docker image `ghcr.io/huggingface/text-embeddings-inference` | — | 8082 |
| searxng | Docker image `searxng/searxng` | — | 8085 |
| dashboard | `dashboard/` | React / Vite | 5173 (dev) |
| extension | `extension/` | Chrome MV3 side panel | — |
| ollama | host service (optional — local chat only, not embeddings) | — | 11434 |

`docker-compose.yml` defines `mcp-server`, `dashboard`, `chat-agent`, `qdrant`,
`embeddings`, and `searxng` — there is no legacy service to accidentally start
anymore, but the `dashboard` compose service still has a broken `nginx.conf`;
always run the dashboard with `npm run dev`, not via Docker Compose.
`chat-agent`/`mcp-server`'s `env_file: .env` is `required: false` — a fresh
clone with no `.env` (only `.env.example`) still starts fine, since every var
has a working default.

## Commands

### chat-agent (Python)

```bash
cd services/chat-agent
python -m venv venv && venv\Scripts\activate      # venv/bin/activate on macOS/Linux
pip install -r requirements.txt

uvicorn main:app --host 0.0.0.0 --port 8080        # run the service

ruff check .                                       # lint (CI default, no pyproject config)

pytest                                              # all tests
pytest tests/ -m "not integration and not e2e"      # unit tier only (CI default)
pytest tests/ -m integration                        # integration tier (needs Qdrant/Ollama/mcp-server; self-skips if unreachable)
pytest tests/test_llm.py::test_name -v               # single test
```

Test tiers are controlled by pytest markers (`services/chat-agent/pytest.ini`):
`integration` tests each hit exactly one real dependency (Qdrant, Ollama, or
mcp-server) and call `pytest.skip()` via fixtures in `tests/conftest.py`
(`qdrant_up`, `ollama_up`, etc.) when that dependency isn't reachable, so
`pytest` stays green without any infra running.

### mcp-server (Go)

```bash
cd services/mcp-server
go build ./...
go vet ./...
gofmt -l .            # CI fails if this lists any files
go test ./... -race -v
go test ./internal/mcp/... -run TestName -v   # single test
```

### dashboard (React)

```bash
cd dashboard
npm install
npm run dev            # http://localhost:5173, proxies /api -> localhost:8080
npm run lint
npm run build
npm run test:e2e       # Playwright; all /api calls are mocked, no backend needed
npm run test:e2e:ui
```

### Local infra

```bash
cp .env.example .env                                    # first time only; every var has a working default
docker compose up qdrant embeddings searxng mcp-server -d  # start what's needed; mcp-server holds Jira/GitHub creds
ollama pull llama3                                       # only if LLM_PROVIDER=ollama (embeddings need no pull — TEI handles its own model)
```

## Architecture

### Credential boundary: mcp-server is the only thing that talks to Jira/GitHub

`chat-agent` never holds `JIRA_*` / `GITHUB_TOKEN` credentials directly (see
the note in `services/chat-agent/config.py`) — those fields exist only so a
shared `.env` doesn't break pydantic-settings' `extra="ignore"` parsing. All
vendor API calls go through `services/chat-agent/mcp_client.py` →
`services/mcp-server` (`internal/tools/jira.go`, `internal/tools/github.go`),
which is the only process that reads those secrets. When touching sync,
actions, or PM integrations, this boundary should stay intact.

### Project isolation

Every SQLite row and every Qdrant point payload carries a `project_id`. There
is a single SQLite DB (`chat.db`) and two Qdrant collections
(`conversations`, `documents`) shared across all projects — isolation is
enforced entirely by filtering on `project_id` at the query layer
(`vectors.py`'s `_project_filter`), not by separate collections/DBs per
project. Any new read/write path must follow this pattern or it will leak
data across projects.

### Request flow for `POST /chat` (`main.py`)

1. Load recent history from SQLite (`memory.py`), scoped by `project_id` + `session_id`.
2. Embed the user message (`embeddings.py`, via the bundled embeddings
   service — Hugging Face Text Embeddings Inference, `EMBEDDINGS_BASE_URL`,
   not Ollama) and search Qdrant `conversations` (memory) and `documents`
   (RAG), both filtered by `project_id`.
3. If the message names a Jira/GitHub key explicitly (regex match), pin those
   chunks by exact source label in addition to semantic search — semantic
   search alone misses action-oriented queries like "comment on KAN-8".
4. If the request has `web_search: true` (a UI toggle — the LLM never decides
   this itself), call mcp-server's `web_search` tool and fold results into
   the prompt as another citation-numbered context block, same mechanism as
   doc chunks. Degrades gracefully (a note in the prompt, not a 500) if the
   search backend is unreachable.
5. Build the prompt (doc chunks → web results → memory hits → recent history
   → user message) and call the configured LLM backend (`llm.py`: `ollama`
   or `openai_compatible`, fixed by the `LLM_PROVIDER` env var at deploy
   time — `openai_compatible` covers OpenAI, Claude via Anthropic's own
   OpenAI-compatible endpoint, Grok, Groq, DeepSeek, or any other
   OpenAI-compatible API, distinguished only by `OPENAI_BASE_URL`).
6. If the reply contains a `<<DRAFT_ACTION>>{...}<<END>>` block, parse it and
   create a pending row in `actions.py` instead of executing anything —
   external writes always require human approval via
   `POST /actions/{id}/approve`, which calls through `mcp_client.py`.

### Settings & Setup Wizard (`dashboard/src/settings/`, `dashboard/src/wizard/`)

The dashboard has a first-run Setup Wizard and an ongoing Settings page, both
backed by real endpoints (not stubs) added to `main.py`: `GET /health/detailed`,
`GET /models` + `POST /models/pull` (Ollama model management), `GET /config`
(read-only effective LLM/embeddings config, never secrets), `GET /integrations/status`
(Jira/GitHub configured state, proxied from mcp-server), and `GET`/`PUT
/config/env[/​{key}]` (env var read/write). The env-var endpoints are
**write-only for secrets** — a value is never echoed back, only
`configured: bool` + a masked hint — and are origin-checked
(`DASHBOARD_ORIGIN`, default `http://localhost:5173`) since chat-agent's
general CORS policy is deliberately wide open (needed for the Chrome
extension's ever-changing origin) and can't be used to gate credential
routes. `services/chat-agent/env_config.py` holds chat-agent's own
allowlisted vars (`LLM_PROVIDER`, `OLLAMA_*`, `OPENAI_*`); mcp-server's
equivalent (`internal/tools/registry.go`'s `envVarAllowlist`) holds
`JIRA_*`/`GITHUB_TOKEN`/`BRAVE_SEARCH_API_KEY`/`SEARXNG_BASE_URL` — chat-agent
proxies writes to mcp-server for those, never storing them itself. Fake
"Fix"/service-restart/connection-test affordances were deliberately removed
from this UI rather than left stubbed — every remaining action in
Settings/Wizard does something real.

### Sync pipeline (`sync.py`)

`POST /projects/{id}/sync` walks each `external_refs` entry
(`jira_project_key` / `github_repo`), fetches items through `mcp_client.py`
(list, then hydrate each item individually), deletes any existing Qdrant
chunks for that source, and re-ingests via `rag.py`. `sync_state` tracks
`last_synced_at` per ref so repeat syncs are incremental.

### Transcript pipeline (`transcript.py`)

Two-phase: (1) chunk + embed the raw transcript into `documents` like any
other ingest, (2) send the full text to the LLM to extract structured
`decisions` / `action_items` / `risks` rows into SQLite. Both phases delete
old rows/chunks for the same `source` before re-writing, so re-ingesting a
transcript replaces rather than duplicates.

### Module responsibilities (`services/chat-agent/`)

`main.py` is route wiring only — business logic lives in per-concern modules:
`projects.py` (SQLite project store + schema version gate), `memory.py`
(conversation history), `vectors.py` (Qdrant wrapper + project filtering),
`embeddings.py`, `rag.py` (chunk/ingest/retrieve), `llm.py` (provider
dispatch), `sync.py`, `actions.py` (approval lifecycle), `transcript.py`,
`briefing.py` / `standup.py` (LLM-generated summaries), `extractors.py`
(file/audio/YouTube/Wikipedia/URL text extraction), `mcp_client.py` (the only
HTTP client that talks to mcp-server).

### mcp-server (`services/mcp-server/`)

Thin Go HTTP service: `GET /tools` lists tool definitions, `POST
/tools/call` dispatches by name (`internal/tools/registry.go`). Each vendor
integration (`jira.go`, `github.go`, `web.go`, `files.go`, `memory.go`) is a
self-contained tool implementation. `web.go`'s `web_search` dispatches to
SearXNG (if `SEARXNG_BASE_URL` set) → Brave (if `BRAVE_SEARCH_API_KEY` set) →
scraping `lite.duckduckgo.com` as a last resort — SearXNG is the bundled
default (see `docker-compose.yml`'s `searxng` service). It still has its own
independent OTEL tracing setup (`internal/observability/`) — this is separate
from and unrelated to chat-agent, which has no tracing/metrics
instrumentation.

## Known implementation gaps

- `briefing.py` does a best-effort RAG lookup against a `VectorStore`
  interface that doesn't fully match the current `vectors.py` API; structured
  briefing data (decisions/actions/risks) still works correctly.
- The extension has UI for retrying failed actions, but
  `POST /actions/{action_id}/retry` is not implemented server-side.
- Google Drive ingestion is not implemented.
