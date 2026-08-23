# FastAPI application entry point.
#
# This file does three things:
#   1. Defines the app lifespan (startup / shutdown hooks + schema checks).
#   2. Declares the Pydantic request/response models for the endpoints.
#   3. Registers the route handlers: /health, /projects (CRUD), /chat,
#      /ingest, /memory/search.
#
# Data flow for POST /chat:
#   config → projects (validate) → memory (SQLite, scoped by project_id)
#          → embeddings (Ollama) → vectors (Qdrant, filtered by project_id)
#          → rag (document retrieval, scoped by project_id) → llm (DeepSeek)
#
# Each module has one responsibility:
#   config.py     — read env vars once at startup
#   projects.py   — SQLite project store
#   memory.py     — SQLite conversation store (recent history, per project)
#   embeddings.py — turn text into a 768-float vector via Ollama
#   vectors.py    — store and search vectors in Qdrant, always filtered by project_id
#   rag.py        — chunk + ingest documents; retrieve relevant chunks (per project)
#   llm.py        — send a message list to the configured LLM backend, get a reply

import json
import logging
import os
from contextlib import asynccontextmanager

import aiosqlite
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

from pydantic import BaseModel, Field, field_validator

from config import settings
from document_state import DocumentStateStore
from embeddings import embed, get_model_info as get_embeddings_model_info
import env_config
from health import check_detailed_health
from mcp_client import MCPClient, MCPError
from llm import ToolCall, chat, chat_with_tools, validate_llm_config
from memory import ConversationStore
from ollama_models import list_models, stream_pull
from projects import SCHEMA_VERSION, Project, ProjectStore
import rag
from request_context import set_request_id, new_request_id
from vectors import VectorStore
from extractors import extract_file, extract_url

logger = logging.getLogger("uvicorn.error")

project_store = ProjectStore(settings.sqlite_path)
store = ConversationStore(settings.sqlite_path)
vstore = VectorStore(url=settings.qdrant_url)
document_state_store = DocumentStateStore(settings.sqlite_path)

# Shared MCPClient — the gateway to mcp-server's tools (web search, etc.).
# Credentials (e.g. BRAVE_SEARCH_API_KEY) live on the mcp-server; this
# service never reads them.
_mcp = MCPClient(
    base_url=settings.mcp_base_url,
    timeout=settings.mcp_timeout_s,
)

# Tools offered to the agentic web-search loop (openai_compatible provider
# only — see _run_web_agent_loop below). Schemas are static per deployment,
# so we fetch them from mcp-server once and cache them.
_WEB_TOOL_NAMES = ["web_search", "web_fetch"]
_web_tool_schemas_cache: list[dict] | None = None


async def _get_web_tool_schemas() -> list[dict]:
    global _web_tool_schemas_cache
    if _web_tool_schemas_cache is None:
        _web_tool_schemas_cache = await _mcp.get_tool_schemas(names=_WEB_TOOL_NAMES)
    return _web_tool_schemas_cache

# nomic-embed-text always produces 768-dimensional vectors.
# This must match the dimension used when the Qdrant collections were created.
EMBED_DIM = 768

# Refusal filter — prevent prior LLM refusals from poisoning later prompts.
_REFUSAL_MARKERS = (
    "i do not have access",
    "i don't have access",
    "i cannot see",
    "i can't see",
    "i cannot access",
    "i can't access",
    "no access to your",
    "i'm unable to access",
    "i am unable to access",
    # Patterns from knowledge-base refusals (LLM denying data it actually has).
    "has not appeared in my knowledge",
    "not appeared in my knowledge",
    "still cannot write a comment",
    "cannot write a comment to",
    "not in my knowledge base",
    "hasn't appeared in",
    "has not appeared in",
    "cannot retrieve",
    "i cannot retrieve",
    "still not appeared",
)


def _looks_like_refusal(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _REFUSAL_MARKERS)


async def _drop_removed_feature_tables() -> None:
    """One-time cleanup for the removed briefing/standup/decisions/
    action_items/risks/flashcards feature set (replaced by the Sources
    panel showing chat citations). Deliberately separate from the
    SCHEMA_VERSION wipe below — that mechanism also wipes conversation
    history, which has nothing to do with this cleanup. DROP TABLE IF
    EXISTS is idempotent — a cheap no-op on every startup after the first."""
    async with aiosqlite.connect(settings.sqlite_path) as db:
        for table in ("decisions", "action_items", "risks", "flashcards"):
            await db.execute(f"DROP TABLE IF EXISTS {table}")
        await db.commit()


# Lifespan — startup initialisation + one-time schema wipe.

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure the projects + schema_version tables exist BEFORE we check the
    # version — otherwise current_version() would fail on a blank DB.
    await project_store.init()

    previous_version = await project_store.current_version()
    schema_mismatch = previous_version != SCHEMA_VERSION

    if schema_mismatch:
        # wipe rather than migrate.  This runs once when
        # upgrading from a pre-Step-5 DB (previous_version is None) and again
        # any future time we bump SCHEMA_VERSION.  Loud log line on purpose.
        logger.warning(
            "Schema version mismatch (stored=%r, code=%r) — wiping messages "
            "and Qdrant collections.  Project rows are preserved.",
            previous_version,
            SCHEMA_VERSION,
        )
        await store.reset()
        await vstore.reset_collection(settings.qdrant_collection, dim=EMBED_DIM)
        await vstore.reset_collection(settings.qdrant_docs_collection, dim=EMBED_DIM)
        await project_store.set_version(SCHEMA_VERSION)
    else:
        # Normal boot: just make sure everything is in place.  Idempotent.
        await store.init()
        await vstore.ensure_collection(settings.qdrant_collection, dim=EMBED_DIM)
        await vstore.ensure_collection(settings.qdrant_docs_collection, dim=EMBED_DIM)

    # DocumentStateStore's table is always safe to create — no schema
    # coupling to the version wipe above.
    await document_state_store.init()
    await _drop_removed_feature_tables()

    # Fail fast if the LLM configuration is incomplete — better to refuse to
    # start than to discover a missing API key on the first real user request.
    validate_llm_config()

    yield
    # Shutdown: no explicit cleanup needed for either store.


app = FastAPI(title="chat-agent", lifespan=lifespan)

# CORS — allow the Chrome extension (and any localhost origin) to call this API.
#
# Why allow_origins=["*"]?
#   Chrome extension IDs look like chrome-extension://abcdef123456...  The ID
#   changes every time an unpacked extension is reloaded in developer mode, so
#   we cannot hard-code it.  Since this server only listens on localhost and is
#   never exposed to the internet, allowing all origins is safe.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _check_origin(request: Request) -> None:
    """Guard for routes that read or write credentials.

    CORSMiddleware above is wildcard-open (needed for the Chrome extension's
    ever-changing origin ID — see the comment above it), so it can't be used
    to gate sensitive routes. This is a narrower, explicit allowlist check
    instead: requests with no Origin header (server-to-server calls, curl,
    tests) are allowed through; only a present-but-unrecognized Origin is
    rejected. Mirrors mcp-server's checkOrigin (cmd/server/main.go).
    """
    origin = request.headers.get("origin")
    if not origin:
        return
    allowed_raw = os.environ.get("DASHBOARD_ORIGIN", "http://localhost:5173")
    allowed = {o.strip() for o in allowed_raw.split(",") if o.strip()}
    if origin not in allowed:
        raise HTTPException(status_code=403, detail="origin not allowed")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Propagate or generate a per-request correlation id.

    Reads the incoming X-Request-ID header (so callers can set their own id,
    e.g. the Chrome extension could send one).  If absent, generates a UUID-4.
    The id is:
      1. Stored in a contextvar so structured log lines pick it up automatically.
      2. Echoed back in the X-Request-ID response header so callers can correlate
         their own logs with ours.
    """

    async def dispatch(
        self, request: StarletteRequest, call_next
    ) -> StarletteResponse:
        req_id = request.headers.get("X-Request-ID") or new_request_id()
        set_request_id(req_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response


app.add_middleware(RequestIdMiddleware)


# Project CRUD models
class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Human-readable project name.")


class ProjectUpdateRequest(BaseModel):
    # Optional — callers send only what they want to change.
    name: str | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    created_at: str

    @field_validator("created_at", mode="before")
    @classmethod
    def _normalise_created_at(cls, v: str) -> str:
        # SQLite stores datetime('now') as "YYYY-MM-DD HH:MM:SS" (no T, no Z).
        # Convert to ISO 8601 with T separator and UTC Z suffix.
        if v and " " in v and "T" not in v:
            v = v.replace(" ", "T")
        if v and not v.endswith("Z"):
            v = v + "Z"
        return v


def _project_to_out(p: Project) -> ProjectOut:
    """Small helper — turning dataclass into pydantic one place."""
    return ProjectOut(
        id=p.id,
        name=p.name,
        created_at=p.created_at,
    )


# Chat / ingest models (all now require project_id)
class ChatRequest(BaseModel):
    project_id: str  # Which project brain owns this conversation.
    session_id: str  # Identifies which conversation inside the project.
    message: str  # The user's input text.
    web_search: bool = False  # User-triggered — the LLM never decides this itself.


class Citation(BaseModel):
    ref: int  # 1-based reference number matching [N] in the reply text.
    source: str  # The source label (e.g. "notes.md") — or a URL for web results.
    chunk_index: int  # Position of this chunk in the original document (0-based); 0 for web results.


class ChatResponse(BaseModel):
    reply: str  # The assistant's reply text.
    citations: list[Citation] = []  # Chunks the reply may have drawn from.


class HistoryMessageOut(BaseModel):
    role: str     # "user" or "assistant".
    content: str  # The message text (citations are not persisted).


class IngestRequest(BaseModel):
    project_id: str  # Which project brain to add this document to.
    source: str  # A label for the document — filename, URL, or any identifier.
    text: str  # The full document text to index.


class IngestResponse(BaseModel):
    chunks: int  # How many chunks were stored in Qdrant.


class SourceOut(BaseModel):
    source: str    # Label supplied at ingest time (e.g. "notes.md").
    chunks: int    # Number of Qdrant points stored under this source label.
    enabled: bool  # Whether this source is included in RAG retrieval.


class SetSourceEnabledRequest(BaseModel):
    enabled: bool


class MemoryHit(BaseModel):
    score: float  # Cosine similarity (0–1, higher = more similar).
    role: str  # 'user' or 'assistant'.
    content: str  # The original message text.
    session_id: str  # Which conversation the hit came from.


# Helpers
async def _require_project(project_id: str) -> Project:
    """Return the project, or raise 404.

    Every scoped endpoint starts by calling this so "unknown project" fails
    loudly rather than silently returning empty results.
    """
    project = await project_store.get(project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project {project_id!r} not found.",
        )
    return project


# Routes — liveness
@app.get("/health")
async def health():
    """Liveness check.  Returns 200 OK when the service is running."""
    return {"status": "ok"}


# Routes — system health, models, config (dashboard Setup Wizard / Settings support)

@app.get("/health/detailed")
async def health_detailed():
    """Aggregate reachability for Ollama, Qdrant, Docker, and mcp-server.

    Each dependency is probed independently — one being down never masks or
    delays the others' status.  Docker and mcp-server are optional.
    """
    return await check_detailed_health(settings, _mcp)


@app.get("/models")
async def get_models():
    """List Ollama models already installed locally."""
    try:
        return await list_models(settings)
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class ModelPullRequest(BaseModel):
    name: str = Field(..., min_length=1)


@app.post("/models/pull")
async def post_models_pull(req: ModelPullRequest):
    """Stream an Ollama model pull's progress straight through to the caller
    as newline-delimited JSON, matching Ollama's own /api/pull response.
    """
    return StreamingResponse(
        stream_pull(settings, req.name),
        media_type="application/x-ndjson",
    )


@app.get("/config")
async def get_config():
    """Read-only effective LLM configuration.  Never includes secrets —
    openai_api_key is never serialized here.

    The embeddings model name is read live from the embeddings service's own
    /info endpoint (not a chat-agent-side constant) so it can never drift out
    of sync with what's actually configured there — and so it degrades to an
    error message instead of failing this whole route if that service is down.
    """
    try:
        embeddings_info = await get_embeddings_model_info()
        embeddings_model = embeddings_info.get("model_id")
        embeddings_error = None
    except HTTPException as exc:
        embeddings_model = None
        embeddings_error = str(exc.detail)

    return {
        "provider": settings.llm_provider,
        "ollama": {
            "chat_model": settings.ollama_chat_model,
            "base_url": settings.ollama_base_url,
        },
        "embeddings": {
            "model": embeddings_model,
            "error": embeddings_error,
        },
        "openai": {
            "model": settings.openai_model,
            "provider_label": settings.openai_provider_label,
            "base_url": settings.openai_base_url,
            "configured": bool(settings.openai_api_key),
        },
    }


class EnvVarUpdateRequest(BaseModel):
    value: str


@app.get("/config/env", dependencies=[Depends(_check_origin)])
async def get_config_env():
    """Combined env var state for the Settings UI's Advanced tab: this
    service's own vars plus mcp-server's (proxied). Secrets are never
    returned in full — see env_config.list_env_vars and mcp-server's
    Registry.EnvVars.

    mcp-server's portion is an independent, separately-failable probe —
    same principle as health.check_detailed_health's per-dependency
    isolation — so an unreachable mcp-server never blocks display/editing
    of this service's own vars (OPENAI_API_KEY, LLM_PROVIDER, ...), which
    have nothing to do with it.
    """
    local_vars = env_config.list_env_vars()
    try:
        remote_vars = await _mcp.get_env_vars()
        mcp_error = None
    except MCPError as exc:
        remote_vars = []
        mcp_error = str(exc)
    return {"vars": local_vars + remote_vars, "mcp_error": mcp_error}


@app.put("/config/env/{key}", dependencies=[Depends(_check_origin)])
async def put_config_env(key: str, req: EnvVarUpdateRequest):
    """Write one env var. Keys owned by this service are written locally;
    everything else is proxied to mcp-server, the only process allowed to
    hold web-search credentials — this service never persists or logs
    those values, only forwards them.
    """
    if key in env_config.OWNED_KEYS:
        try:
            env_config.set_env_var(key, req.value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        try:
            await _mcp.set_env_var(key, req.value)
        except MCPError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {"ok": True}


# Routes — project CRUD
@app.post("/projects", response_model=ProjectOut)
async def post_projects(req: ProjectCreateRequest) -> ProjectOut:
    """Create a new project brain.

    The returned id is a UUID string — stable, safe to use in URLs/JSON.
    """
    try:
        project = await project_store.create(name=req.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    logger.info("Created project %r (%s)", project.name, project.id)
    return _project_to_out(project)


@app.get("/projects", response_model=list[ProjectOut])
async def get_projects() -> list[ProjectOut]:
    """List all projects, newest first."""
    projects = await project_store.list()
    return [_project_to_out(p) for p in projects]


@app.patch("/projects/{project_id}", response_model=ProjectOut)
async def patch_project(project_id: str, req: ProjectUpdateRequest) -> ProjectOut:
    """Partial update — change name."""
    try:
        updated = await project_store.update(
            project_id,
            name=req.name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if updated is None:
        raise HTTPException(
            status_code=404, detail=f"Project {project_id!r} not found."
        )
    return _project_to_out(updated)


@app.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    """Delete a project and cascade through all of its state.

    Cascade:
      - SQLite:  projects row + messages rows   (ProjectStore.delete)
      - Qdrant:  conversations + documents points tagged with project_id
    """
    # Do the vector deletes first.  If ProjectStore.delete() succeeded but the
    # Qdrant delete failed, we'd be left with orphan vectors filtered on a
    # project id that no longer exists — confusing but not dangerous.  Doing
    # Qdrant first means a failure there prevents the SQLite delete, and the
    # user can retry.
    await vstore.delete_by_project(settings.qdrant_collection, project_id)
    await vstore.delete_by_project(settings.qdrant_docs_collection, project_id)
    await document_state_store.delete_by_project(project_id)

    deleted = await project_store.delete(project_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"Project {project_id!r} not found."
        )

    logger.info("Deleted project %s and all of its memory.", project_id)
    return {"deleted": True}


# Routes — chat + ingest + memory search (now project-scoped)
@app.post("/ingest", response_model=IngestResponse)
async def post_ingest(req: IngestRequest) -> IngestResponse:
    """Split a document into chunks, embed them, and store them in Qdrant
    under the given project.

    Args (JSON body):
        project_id: Which project brain owns this document.
        source:     A label for this document (e.g. a filename or URL).
        text:       The full document text.

    Returns:
        {"chunks": N} — the number of chunks stored.
    """
    await _require_project(req.project_id)
    n = await rag.ingest(req.project_id, req.source, req.text, vstore)
    return IngestResponse(chunks=n)


async def _process_transcript(
    project_id: str, source: str, text: str
) -> IngestResponse:
    """Chunk, embed, and store transcript text in Qdrant, replacing any prior
    content for this source.

    Unlike plain /ingest, transcript sources are idempotently replaced on
    re-post rather than accumulated — re-uploading a meeting transcript
    after edits shouldn't duplicate chunks. Shared by /ingest/transcript,
    /ingest/file?kind=transcript, and /ingest/url?kind=transcript so they
    all behave identically.
    """
    await vstore.delete_by_source(
        settings.qdrant_docs_collection, project_id, source
    )
    chunk_count = await rag.ingest(project_id, source, text, vstore)
    return IngestResponse(chunks=chunk_count)


@app.post("/ingest/transcript", response_model=IngestResponse)
async def post_ingest_transcript(req: IngestRequest) -> IngestResponse:
    """Pasted-text transcript ingest. See _process_transcript for behaviour."""
    await _require_project(req.project_id)
    return await _process_transcript(req.project_id, req.source, req.text)


@app.post("/ingest/file", response_model=IngestResponse)
async def post_ingest_file(
    project_id: str = Form(...),
    kind: str = Form("document"),
    file: UploadFile = File(...),
) -> IngestResponse:
    """Upload a file and ingest its extracted text.

    Form fields:
        project_id: Which project brain owns this content.
        kind:       "document" (chunk + embed only) or "transcript" (chunk +
                    embed, replacing any prior content for this source).
        file:       The uploaded file. Source label is the original filename.
    """
    await _require_project(project_id)
    if kind not in {"document", "transcript"}:
        raise HTTPException(400, f"Invalid kind: {kind!r}")

    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file is empty.")

    text = extract_file(file.filename or "uploaded", data)
    source = file.filename or "uploaded-file"

    if kind == "transcript":
        return await _process_transcript(project_id, source, text)

    chunks = await rag.ingest(project_id, source, text, vstore)
    return IngestResponse(chunks=chunks)


class IngestUrlRequest(BaseModel):
    project_id: str
    url: str
    kind: str = "document"  # "document" or "transcript"


@app.post("/ingest/url", response_model=IngestResponse)
async def post_ingest_url(req: IngestUrlRequest) -> IngestResponse:
    """Fetch and ingest content at a URL.

    Routes by hostname:
        youtube.com / youtu.be   → captions via youtube-transcript-api
        *.wikipedia.org          → MediaWiki action API extract
        anything else            → trafilatura main-content extraction
    """
    await _require_project(req.project_id)
    if req.kind not in {"document", "transcript"}:
        raise HTTPException(400, f"Invalid kind: {req.kind!r}")

    source, text = extract_url(req.url)
    if not text.strip():
        raise HTTPException(400, f"No content extracted from {req.url!r}")

    if req.kind == "transcript":
        return await _process_transcript(req.project_id, source, text)

    chunks = await rag.ingest(req.project_id, source, text, vstore)
    return IngestResponse(chunks=chunks)


@app.get("/projects/{project_id}/sources", response_model=list[SourceOut])
async def list_sources(project_id: str) -> list[SourceOut]:
    """Return distinct ingested sources for a project with their chunk counts."""
    await _require_project(project_id)
    enabled_map = await document_state_store.get_enabled_map(project_id)
    summaries = await rag.list_sources(
        project_id=project_id, vstore=vstore, enabled_map=enabled_map
    )
    return [
        SourceOut(source=s.source, chunks=s.chunks, enabled=s.enabled)
        for s in summaries
    ]


@app.patch("/projects/{project_id}/sources/{source}")
async def set_source_enabled(
    project_id: str, source: str, req: SetSourceEnabledRequest
):
    """Toggle whether a document is included in RAG retrieval.

    Disabling a source keeps its chunks in Qdrant but excludes it from
    /chat retrieval — use DELETE to remove a document permanently instead.
    """
    await _require_project(project_id)
    await document_state_store.set_enabled(project_id, source, req.enabled)
    return {"source": source, "enabled": req.enabled}


@app.delete("/projects/{project_id}/sources/{source}")
async def delete_source(project_id: str, source: str):
    """Permanently delete one ingested document.

    Cascade:
      - Qdrant:  all chunks tagged with this project_id + source
      - SQLite:  any stored enabled/disabled state
    """
    await _require_project(project_id)
    await vstore.delete_by_source(settings.qdrant_docs_collection, project_id, source)
    await document_state_store.delete_by_source(project_id, source)
    logger.info("Deleted source %r from project %s", source, project_id)
    return {"deleted": True}


# ─── Agentic web-search loop (openai_compatible provider only) ───────────────
#
# When req.web_search is true and the provider supports tool-calling, the
# model is offered web_search/web_fetch as real tools instead of getting one
# pre-fetched batch of results — it can search, read, and decide to search or
# fetch again before answering. Bounded by settings.chat_tool_max_rounds.

def _to_openai_tool_call(tc: ToolCall) -> dict:
    """Re-serialize a parsed ToolCall back into the OpenAI tool_calls shape
    (arguments as a JSON string) for echoing the assistant's own call back
    into message history."""
    return {
        "id": tc.id,
        "type": "function",
        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
    }


async def _execute_web_tool(tc: ToolCall, citation_map: dict[str, int], citation_offset: int) -> str:
    """Run one model-requested web_search/web_fetch call and return the text
    to feed back to the model as the tool result. Assigns/reuses a citation
    ref per URL in citation_map (mutated in place) so citations stay stable
    across multiple rounds that touch the same URL."""
    try:
        result = await _mcp.call(tc.name, tc.arguments)
    except MCPError as exc:
        # Let the model see the failure and react, instead of failing the turn.
        return f"Tool call failed: {exc}"

    def _ref_for(url: str) -> int:
        if url not in citation_map:
            citation_map[url] = citation_offset + len(citation_map) + 1
        return citation_map[url]

    if tc.name == "web_search":
        results = result.get("results", [])
        if not results:
            return "No results."
        return "\n\n".join(
            f"[{_ref_for(r['url'])}] {r['title']} — {r['url']}\n{r['snippet']}" for r in results
        )

    if tc.name == "web_fetch":
        url = tc.arguments.get("url", "")
        ref = _ref_for(url)
        body = result.get("body", "")[:4000]
        return f"[{ref}] fetched {url} (status {result.get('status')}):\n{body}"

    return json.dumps(result)


def _citations_from_map(citation_map: dict[str, int]) -> list[Citation]:
    return [
        Citation(ref=ref, source=url, chunk_index=0)
        for url, ref in sorted(citation_map.items(), key=lambda kv: kv[1])
    ]


async def _run_web_agent_loop(
    messages: list[dict], tools: list[dict], citation_offset: int
) -> tuple[str, list[Citation]]:
    """Let the model call web_search/web_fetch up to chat_tool_max_rounds
    times, then return its final answer plus any citations gathered along
    the way. `messages` is mutated in place with the tool-call/tool-result
    turns so callers can log/inspect the full transcript afterward.

    A round that comes back with no tool_calls and finish_reason="length"
    is a truncated-mid-thought response, not a real final answer — it's
    retried (with a brevity nudge) rather than returned to the user, still
    counting against chat_tool_max_rounds."""
    citation_map: dict[str, int] = {}

    for _ in range(settings.chat_tool_max_rounds):
        result = await chat_with_tools(messages, tools)
        if not result.tool_calls:
            if result.finish_reason == "length":
                # Cut off mid-thought (hit max_tokens), not actually done —
                # confirmed live: this shows up as narration like "let me
                # try X instead" with no tool_calls, indistinguishable from
                # a genuine final answer unless finish_reason is checked.
                # Don't keep the truncated assistant turn (it's incomplete,
                # not useful history) — just nudge for brevity and retry
                # this round.
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Your previous response was cut off before you finished. "
                            "Be concise: briefly state your next step, then immediately "
                            "call the tool — or if you already have enough information, "
                            "give your final answer directly."
                        ),
                    }
                )
                continue
            return result.content or "", _citations_from_map(citation_map)

        messages.append(
            {
                "role": "assistant",
                "content": result.content,
                "tool_calls": [_to_openai_tool_call(tc) for tc in result.tool_calls],
            }
        )
        for tc in result.tool_calls:
            tool_content = await _execute_web_tool(tc, citation_map, citation_offset)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_content})

    # Round cap hit: force a final answer. Still chat_with_tools (not plain
    # chat()) — messages contains prior tool-calling turns, and dropping
    # `tools` entirely here is what triggers deepseek-chat's DSML markup
    # leak (reproduced live: happens with tool_choice="none" too).
    #
    # Confirmed live: deepseek-chat routinely ignores tool_choice="none" and
    # still tries to call a tool (via DSML markup, recovered into
    # final.tool_calls by chat_with_tools). When that happens, final.content
    # is NOT a real answer — it's just the narration lead-in the model wrote
    # right before the tool call it wanted to make (e.g. "let me fetch the
    # raw markdown version"), never the actual answer. The round budget is
    # spent, so that tool call is never executed — but its presence means
    # we must not trust final.content either; only an empty tool_calls list
    # means the model actually committed to a real answer this time.
    final = await chat_with_tools(messages, tools, tool_choice="none")
    if final.tool_calls or not final.content:
        reply = (
            "I gathered some information but couldn't finish putting together a "
            "complete answer in time — try asking again, possibly with a "
            "narrower question."
        )
    else:
        reply = final.content
    return reply, _citations_from_map(citation_map)


@app.post("/chat", response_model=ChatResponse)
async def post_chat(req: ChatRequest) -> ChatResponse:
    """Accept a user message, retrieve scoped memory + documents, call DeepSeek.

    Full flow (everything scoped by project_id):
        1.  Load the last 6 messages for (project, session) from SQLite.
        2.  Persist the new user message to SQLite.
        3.  Embed the user message; search Qdrant conversations within
            the project for similar past messages.
        3b. Search Qdrant documents within the project for relevant chunks.
        4.  Upsert the user message vector into the conversations collection
            (tagged with project_id).
        5.  Build the prompt from document chunks, memory hits, recent history.
        6.  Call DeepSeek.
        7.  Persist the assistant reply to SQLite (tagged with project_id).
        8.  Upsert the assistant reply vector into conversations (tagged).
        9.  Return the reply.
    """
    await _require_project(req.project_id)

    # 1. Recent history (last 6 messages = 3 turns, oldest first).
    recent = await store.history(req.project_id, req.session_id, limit=6)

    # 2. Persist the user message to SQLite immediately.
    await store.append(req.project_id, req.session_id, "user", req.message)

    # 3. Embed + search conversations collection for similar past messages,
    #    filtered to this project only.
    query_vec = await embed(req.message)
    hits = await vstore.search(
        settings.qdrant_collection,
        project_id=req.project_id,
        vector=query_vec,
        k=settings.memory_search_k,
        score_threshold=settings.memory_score_threshold,
    )

    # 3b. Search documents collection for relevant chunks (RAG), same filter.
    #     Documents the user has toggled off are excluded from retrieval.
    _disabled_sources = await document_state_store.get_disabled_sources(req.project_id)
    doc_chunks = await rag.retrieve(
        req.project_id, req.message, k=3, vstore=vstore,
        score_threshold=settings.rag_score_threshold,
        exclude_sources=_disabled_sources,
    )

    logger.info(
        "RAG[%s]: retrieved %d doc chunks for query %r: %s",
        req.project_id,
        len(doc_chunks),
        req.message,
        [(c.source, c.chunk_index) for c in doc_chunks],
    )

    # 4. Store the user message vector in Qdrant conversations.
    await vstore.upsert(
        settings.qdrant_collection,
        project_id=req.project_id,
        vector=query_vec,
        payload={"session_id": req.session_id, "role": "user", "content": req.message},
    )

    # 5. Build the prompt.
    #
    # Deduplication for conversation hits: drop any hit already in recent history.
    # Also strip prior assistant refusals — when the model has previously claimed
    # "no access", those replies poison the next prompt by priming the model to
    # repeat its own pattern.  We drop them here so the updated system message
    # and retrieved chunks get a clean slate.
    recent_contents = {m["content"] for m in recent}
    unique_hits = [
        h
        for h in hits
        if h.session_id == req.session_id
        and h.content not in recent_contents
        and not (h.role == "assistant" and _looks_like_refusal(h.content))
    ]

    # Apply the same refusal-filter to the recent SQLite history before
    # appending it to the prompt — this is the stronger contamination path.
    sanitized_recent = [
        m
        for m in recent
        if not (m["role"] == "assistant" and _looks_like_refusal(m["content"]))
    ]

    messages: list[dict] = []

    # Inject relevant document chunks first (highest priority context).
    # Deduplicate by text content — the same chunk may appear multiple times
    # if the same document was ingested more than once.
    seen_texts: set[str] = set()
    relevant_chunks = []
    for c in doc_chunks:
        if c.text not in seen_texts:
            seen_texts.add(c.text)
            relevant_chunks.append(c)

    # 3d. If the user explicitly turned on web search for this message, give
    # the model access to it. This is always user-triggered, never model-
    # triggered — req.web_search gates whether search is available at all.
    #
    # openai_compatible: the model gets web_search/web_fetch as real tools
    # and can call them (and re-call, e.g. search then fetch) in a bounded
    # loop — see _run_web_agent_loop below.
    # ollama: function-calling support varies too much across local models,
    # so it keeps the original behavior — one fixed web_search call, folded
    # into the prompt as a static context block before the single LLM call.
    web_results: list[dict] = []
    web_search_error: str | None = None
    web_tools: list[dict] = []
    use_agentic_web = False
    if req.web_search:
        if settings.llm_provider == "openai_compatible":
            try:
                web_tools = await _get_web_tool_schemas()
                use_agentic_web = True
            except MCPError as exc:
                web_search_error = str(exc)
        else:
            try:
                web_response = await _mcp.call("web_search", {"query": req.message, "limit": 5})
                web_results = web_response.get("results", [])
            except MCPError as exc:
                # Degrade, don't fail the whole turn — the LLM can still answer
                # from RAG/memory/its own knowledge, just without live results.
                web_search_error = str(exc)

    # Build citations list for the API response. ref is 1-based and matches
    # the [N] numbers injected into the prompt below. Web results continue
    # the same numbering after doc chunks.
    citations = [
        Citation(ref=i + 1, source=c.source, chunk_index=c.chunk_index)
        for i, c in enumerate(relevant_chunks)
    ] + [
        Citation(ref=len(relevant_chunks) + i + 1, source=r["url"], chunk_index=0)
        for i, r in enumerate(web_results)
    ]

    if relevant_chunks:
        # Number each chunk so the model can cite precisely (e.g. "[1]") rather
        # than copying the full source label — shorter and less error-prone.
        doc_lines = "\n\n".join(
            f"[{i + 1}] source: {c.source} (chunk {c.chunk_index})\n{c.text}"
            for i, c in enumerate(relevant_chunks)
        )
        messages.append(
            {
                "role": "system",
                "content": (
                    "You are the project brain for a local-first personal assistant. "
                    "The excerpts below were retrieved from THIS user's own knowledge "
                    "base — documents ingested into this project by the user "
                    "themselves. They are the authoritative source of truth for "
                    "this project.\n\n"
                    "Rules:\n"
                    "1. When the user asks about tasks, status, or content covered "
                    "   by these excerpts, answer FROM these excerpts. The chunks "
                    "   ARE the data — they are not pointers to an external system.\n"
                    "2. Cite by reference number, e.g. [1], when you use information "
                    "   from a chunk. You may also include the source label for "
                    "   clarity, e.g. [1][notes.md].\n"
                    "3. CRITICAL: If a chunk is present below, that information IS "
                    "   available to you. Do NOT claim it is missing, has not "
                    "   appeared, or that you cannot access it. Read the chunk and "
                    "   answer from it.\n"
                    "4. Never say 'it has not appeared in my knowledge base' or "
                    "   'I cannot retrieve' when the chunk is listed below.\n"
                    "5. If the excerpts truly do not contain the answer, say so "
                    "   plainly.\n\n"
                    "--- PROJECT KNOWLEDGE ---\n"
                    f"{doc_lines}\n"
                    "--- END PROJECT KNOWLEDGE ---"
                ),
            }
        )

    # Inject web search results, if the user asked for them (see 3d above).
    if web_results:
        web_lines = "\n\n".join(
            f"[{len(relevant_chunks) + i + 1}] {r['title']} — {r['url']}\n{r['snippet']}"
            for i, r in enumerate(web_results)
        )
        messages.append(
            {
                "role": "system",
                "content": (
                    "The user turned on web search for this message. Live search results "
                    "are below — cite them by reference number, e.g. [N], the same way "
                    "you'd cite project knowledge.\n\n"
                    "--- WEB SEARCH RESULTS ---\n"
                    f"{web_lines}\n"
                    "--- END WEB SEARCH RESULTS ---"
                ),
            }
        )
    elif req.web_search and web_search_error:
        messages.append(
            {
                "role": "system",
                "content": (
                    f"The user turned on web search for this message, but the search "
                    f"failed: {web_search_error}. Tell them the search didn't work, "
                    "then answer as best you can without it."
                ),
            }
        )
    elif use_agentic_web:
        # No pre-fetched results here — the model calls web_search/web_fetch
        # itself (see _run_web_agent_loop). It still needs to know search is
        # on for this turn, since the user explicitly asked for it.
        messages.append(
            {
                "role": "system",
                "content": (
                    "The user turned on web search for this message. You have "
                    "web_search and web_fetch tools available — use them if the "
                    "question needs current or external information (e.g. search, "
                    "then fetch a promising result to read it in full). Cite "
                    "results by reference number, e.g. [N], the same way you'd "
                    "cite project knowledge."
                ),
            }
        )

    # Then inject relevant past conversation messages.
    if unique_hits:
        context_lines = "\n".join(f"- [{h.role}]: {h.content}" for h in unique_hits)
        messages.append(
            {
                "role": "system",
                "content": (
                    "The following messages from earlier in this conversation may be "
                    "relevant to the user's current question:\n" + context_lines
                ),
            }
        )

    # Append sanitized recent history and the new user message.
    messages += sanitized_recent + [{"role": "user", "content": req.message}]

    logger.info(
        "LLM prompt for project=%s session=%s: %d messages, roles=%s, "
        "doc_chunks=%d, memory_hits=%d, recent=%d",
        req.project_id,
        req.session_id,
        len(messages),
        [m["role"] for m in messages],
        len(relevant_chunks),
        len(unique_hits),
        len(sanitized_recent),
    )
    logger.debug("LLM full messages: %s", messages)

    # 6. Call the LLM (backend and model fixed by deploy-time config). When
    # web search is on and the provider supports tool-calling, let the model
    # drive its own search/fetch rounds instead of a single fixed call.
    if use_agentic_web:
        reply, extra_citations = await _run_web_agent_loop(
            messages, web_tools, citation_offset=len(relevant_chunks)
        )
        citations += extra_citations
    else:
        reply = await chat(messages)

    # 7. Persist the assistant reply to SQLite.
    await store.append(req.project_id, req.session_id, "assistant", reply)

    # 8. Store the assistant reply vector in Qdrant conversations.
    reply_vec = await embed(reply)
    await vstore.upsert(
        settings.qdrant_collection,
        project_id=req.project_id,
        vector=reply_vec,
        payload={"session_id": req.session_id, "role": "assistant", "content": reply},
    )

    # 9. Return.
    return ChatResponse(reply=reply, citations=citations)


@app.get("/projects/{project_id}/history", response_model=list[HistoryMessageOut])
async def get_history(
    project_id: str,
    session_id: str = Query("default", description="Which conversation within the project."),
    limit: int = Query(100, description="Max messages to return, oldest first."),
) -> list[HistoryMessageOut]:
    """Return persisted chat history for (project_id, session_id), oldest first.

    Lets the dashboard restore the conversation on page load instead of
    starting blank — messages are already durably stored in SQLite by
    /chat (store.append), this just exposes them for reading. Citations are
    not persisted, so replayed assistant messages won't show their sources.
    """
    await _require_project(project_id)
    rows = await store.history(project_id, session_id, limit=limit)
    return [HistoryMessageOut(role=r["role"], content=r["content"]) for r in rows]


@app.get("/memory/search", response_model=list[MemoryHit])
async def memory_search(
    project_id: str = Query(..., description="Restrict search to this project."),
    q: str = Query(..., description="The text to search for in vector memory."),
    k: int = Query(5, ge=1, le=20, description="Number of results to return."),
) -> list[MemoryHit]:
    """Search conversation vector memory (within one project) for messages
    semantically similar to q.

    This is a debug / inspection endpoint — it lets you see what the agent
    would retrieve as context for a given query without making a full chat call.

    Example:
        GET /memory/search?project_id=<uuid>&q=what+is+my+name&k=3
    """
    await _require_project(project_id)

    vec = await embed(q)
    hits = await vstore.search(
        settings.qdrant_collection,
        project_id=project_id,
        vector=vec,
        k=k,
    )
    return [
        MemoryHit(
            score=h.score,
            role=h.role,
            content=h.content,
            session_id=h.session_id,
        )
        for h in hits
    ]
