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

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

import briefing
import deep_research
import env_config
import rag
import standup
from config import settings
from document_state import DocumentStateStore
from embeddings import get_model_info as get_embeddings_model_info
from entity import EntityStore, extract_entities
from extractors import extract_file, extract_url
from flashcards import FlashcardStore, generate_candidates, schedule_review
from health import check_detailed_health
from llm import chat, validate_llm_config
from mcp_client import MCPClient, MCPError
from memory import ConversationStore
from ollama_models import list_models, stream_pull
from projects import SCHEMA_VERSION, Project, ProjectStore
from request_context import new_request_id, set_request_id
from scratchpad import ScratchpadStore
from semantic_cache import SemanticCacheStore, embed_cached
from toolbox import ToolboxStore
from transcript import (
    TranscriptStore,
    extract_structured,
)
from vectors import VectorStore
from workflow import WorkflowStore

logger = logging.getLogger("uvicorn.error")

project_store = ProjectStore(settings.sqlite_path)
store = ConversationStore(settings.sqlite_path)
vstore = VectorStore(url=settings.qdrant_url)
transcript_store = TranscriptStore(settings.sqlite_path)
entity_store = EntityStore(settings.sqlite_path)
document_state_store = DocumentStateStore(settings.sqlite_path)
flashcard_store = FlashcardStore(settings.sqlite_path)
toolbox_store = ToolboxStore(settings.sqlite_path)
workflow_store = WorkflowStore(settings.sqlite_path)
scratchpad_store = ScratchpadStore(settings.sqlite_path)
semantic_cache_store = SemanticCacheStore(settings.sqlite_path)

# Shared MCPClient — the gateway to mcp-server's tools (web search, etc.).
# Credentials (e.g. BRAVE_SEARCH_API_KEY) live on the mcp-server; this
# service never reads them.
_mcp = MCPClient(
    base_url=settings.mcp_base_url,
    timeout=settings.mcp_timeout_s,
    toolbox=toolbox_store,
)

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

    # TranscriptStore and DocumentStateStore tables are always safe to
    # create — no schema coupling to the version wipe above.
    await transcript_store.init()
    await entity_store.init()
    await document_state_store.init()
    await flashcard_store.init()
    await toolbox_store.init()
    await workflow_store.init()
    await scratchpad_store.init()
    await semantic_cache_store.init()

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
    deep_research: bool = False  # User-triggered — opts into the multi-step
    # tool-calling research loop for this turn (see deep_research.py). The
    # user decides whether to start it; the model decides whether/when to
    # stop once started.


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
    entities: int = 0  # Entities extracted + upserted from this document.


class IngestTranscriptRequest(BaseModel):
    project_id: str  # Which project brain owns this transcript.
    source: str  # Label for the meeting (e.g. "meeting-2026-05-12").
    text: str  # Raw transcript text.


class IngestTranscriptResponse(BaseModel):
    chunks: int  # RAG chunks stored in Qdrant.
    decisions: int  # Structured decisions extracted + stored.
    action_items: int  # Structured action items extracted + stored.
    risks: int  # Structured risks extracted + stored.
    entities: int = 0  # Entities extracted + upserted.


class DecisionOut(BaseModel):
    id: str
    source: str
    text: str
    created_at: str


class ActionItemOut(BaseModel):
    id: str
    source: str
    owner: str | None
    text: str
    due_date: str | None
    status: str
    created_at: str


class RiskOut(BaseModel):
    id: str
    source: str
    text: str
    created_at: str


class FlashcardOut(BaseModel):
    id: str
    source: str
    front: str
    back: str
    ease_factor: float
    interval_days: int
    repetitions: int
    due_at: str
    last_reviewed_at: str | None
    created_at: str


class FlashcardReviewRequest(BaseModel):
    quality: int = Field(..., ge=0, le=5, description="0=Again, 3=Hard, 4=Good, 5=Easy")


class FlashcardUpdateRequest(BaseModel):
    front: str = Field(..., min_length=1)
    back: str = Field(..., min_length=1)


class WorkflowStepIn(BaseModel):
    tool_name: str = Field(..., min_length=1)
    arguments: dict = Field(default_factory=dict)
    description: str | None = None


class WorkflowCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    trigger: str = Field(..., min_length=1, description="Comma-separated keywords/phrases")
    description: str | None = None
    steps: list[WorkflowStepIn] = Field(..., min_length=1)


class WorkflowStepOut(BaseModel):
    step_order: int
    tool_name: str
    arguments: dict
    description: str | None


class WorkflowOut(BaseModel):
    name: str
    trigger: str
    description: str | None
    created_at: str
    steps: list[WorkflowStepOut]


class EntityOut(BaseModel):
    id: str
    name: str
    type: str
    attributes: str | None
    sources: list[str]
    created_at: str
    updated_at: str


class ToolStatsOut(BaseModel):
    tool_name: str
    call_count: int
    success_count: int
    failure_count: int
    success_rate: float
    avg_duration_ms: float | None
    last_used_at: str


class CacheStatsOut(BaseModel):
    entry_count: int
    total_hits: int


class ScratchpadEntryOut(BaseModel):
    key: str
    value: str
    created_at: str
    updated_at: str


class BriefActionOut(BaseModel):
    id: str
    text: str
    owner: str | None
    due_date: str | None
    status: str
    source: str


class BriefDecisionOut(BaseModel):
    id: str
    text: str
    source: str
    created_at: str


class BriefRiskOut(BaseModel):
    id: str
    text: str
    source: str
    created_at: str


class BriefingOut(BaseModel):
    summary: str
    open_actions: list[BriefActionOut]
    recent_decisions: list[BriefDecisionOut]
    active_risks: list[BriefRiskOut]
    generated_at: str


class StandupOut(BaseModel):
    summary: str
    done: list[str]
    today: list[str]
    blockers: list[str]
    generated_at: str


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
      - SQLite:  transcript/entity/document_state/flashcards/toolbox/workflow/scratchpad rows
    """
    # Do the vector deletes first.  If ProjectStore.delete() succeeded but the
    # Qdrant delete failed, we'd be left with orphan vectors filtered on a
    # project id that no longer exists — confusing but not dangerous.  Doing
    # Qdrant first means a failure there prevents the SQLite delete, and the
    # user can retry.
    await vstore.delete_by_project(settings.qdrant_collection, project_id)
    await vstore.delete_by_project(settings.qdrant_docs_collection, project_id)
    await transcript_store.delete_by_project(project_id)
    await entity_store.delete_by_project(project_id)
    await document_state_store.delete_by_project(project_id)
    await flashcard_store.delete_by_project(project_id)
    await toolbox_store.delete_by_project(project_id)
    await workflow_store.delete_by_project(project_id)
    await scratchpad_store.delete_by_project(project_id)
    await semantic_cache_store.delete_by_project(project_id)

    deleted = await project_store.delete(project_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"Project {project_id!r} not found."
        )

    logger.info("Deleted project %s and all of its memory.", project_id)
    return {"deleted": True}


async def _process_document_entities(project_id: str, source: str, text: str) -> int:
    """Extract entities from any ingested text and upsert them.

    Shared by every ingest path (plain /ingest, transcripts, and document-kind
    /ingest/file /ingest/url) — unlike transcript.py's decisions/action_items/
    risks, entities are relevant to any document, not just meeting transcripts.

    Failure here must not fail the ingest itself: chunk/embed storage already
    succeeded by the time this runs, and a document without extractable
    entities (or a flaky extraction call) is a normal, non-fatal outcome — so
    log and return 0 instead of raising.
    """
    try:
        extracted = await extract_entities(text, chat)
    except ValueError as exc:
        logger.warning(
            "Entity extraction failed for project=%s source=%r: %s",
            project_id, source, exc,
        )
        return 0

    count = 0
    for item in extracted:
        name = (item.get("name") or "").strip()
        entity_type = (item.get("type") or "other").strip() or "other"
        if not name:
            continue
        attributes = item.get("attributes")
        if attributes in (None, "null"):
            attributes = None
        await entity_store.upsert_entity(project_id, name, entity_type, attributes, source)
        count += 1
    return count


# Routes — chat + ingest + memory search (now project-scoped)
@app.post("/ingest", response_model=IngestResponse)
async def post_ingest(req: IngestRequest) -> IngestResponse:
    """Split a document into chunks, embed them, and store them in Qdrant
    under the given project. Also extracts and upserts any named entities.

    Args (JSON body):
        project_id: Which project brain owns this document.
        source:     A label for this document (e.g. a filename or URL).
        text:       The full document text.

    Returns:
        {"chunks": N, "entities": M}.
    """
    await _require_project(req.project_id)
    n = await rag.ingest(req.project_id, req.source, req.text, vstore)
    entity_count = await _process_document_entities(req.project_id, req.source, req.text)
    return IngestResponse(chunks=n, entities=entity_count)


async def _process_transcript(
    project_id: str, source: str, text: str
) -> IngestTranscriptResponse:
    """Run the two-phase transcript pipeline and return the response model.

    Shared by /ingest/transcript, /ingest/file?kind=transcript, and
    /ingest/url?kind=transcript so they all behave identically.
    """
    # Phase 1 — delete old vector chunks for this source, then re-ingest.
    await vstore.delete_by_source(
        settings.qdrant_docs_collection, project_id, source
    )
    chunk_count = await rag.ingest(project_id, source, text, vstore)

    # Phase 2 — delete old structured rows, then re-extract.
    await transcript_store.delete_by_source(project_id, source)
    try:
        extracted = await extract_structured(text, chat)
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Structured extraction failed: {exc}",
        ) from exc

    counts = await transcript_store.save_extracted(project_id, source, extracted)
    entity_count = await _process_document_entities(project_id, source, text)
    logger.info(
        "Transcript[%s] source=%r: %d chunks, %d decisions, %d action_items, "
        "%d risks, %d entities",
        project_id,
        source,
        chunk_count,
        counts["decisions"],
        counts["action_items"],
        counts["risks"],
        entity_count,
    )
    return IngestTranscriptResponse(
        chunks=chunk_count,
        decisions=counts["decisions"],
        action_items=counts["action_items"],
        risks=counts["risks"],
        entities=entity_count,
    )


@app.post("/ingest/transcript", response_model=IngestTranscriptResponse)
async def post_ingest_transcript(
    req: IngestTranscriptRequest,
) -> IngestTranscriptResponse:
    """Pasted-text transcript ingest. See _process_transcript for behaviour."""
    await _require_project(req.project_id)
    return await _process_transcript(req.project_id, req.source, req.text)


@app.post("/ingest/file", response_model=IngestTranscriptResponse)
async def post_ingest_file(
    project_id: str = Form(...),
    kind: str = Form("document"),
    file: UploadFile = File(...),
) -> IngestTranscriptResponse:
    """Upload a file and ingest its extracted text.

    Form fields:
        project_id: Which project brain owns this content.
        kind:       "document" (chunk + embed only) or "transcript"
                    (also extract decisions / action items / risks).
        file:       The uploaded file. Source label is the original filename.

    Always returns IngestTranscriptResponse so the frontend gets a uniform
    shape; for documents the decisions / action_items / risks fields are 0.
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
    entity_count = await _process_document_entities(project_id, source, text)
    return IngestTranscriptResponse(
        chunks=chunks, decisions=0, action_items=0, risks=0, entities=entity_count
    )


class IngestUrlRequest(BaseModel):
    project_id: str
    url: str
    kind: str = "document"  # "document" or "transcript"


@app.post("/ingest/url", response_model=IngestTranscriptResponse)
async def post_ingest_url(req: IngestUrlRequest) -> IngestTranscriptResponse:
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
    entity_count = await _process_document_entities(req.project_id, source, text)
    return IngestTranscriptResponse(
        chunks=chunks, decisions=0, action_items=0, risks=0, entities=entity_count
    )


@app.get("/projects/{project_id}/decisions", response_model=list[DecisionOut])
async def get_decisions(project_id: str) -> list[DecisionOut]:
    """List all decisions extracted from transcripts for this project, newest first."""
    await _require_project(project_id)
    rows = await transcript_store.list_decisions(project_id)
    return [
        DecisionOut(id=r.id, source=r.source, text=r.text, created_at=r.created_at)
        for r in rows
    ]


@app.get("/projects/{project_id}/action-items", response_model=list[ActionItemOut])
async def get_action_items(
    project_id: str,
    status: str | None = Query(None, description="Filter by status: 'open' or 'done'."),
) -> list[ActionItemOut]:
    """List transcript action items. Pass ?status=open to see only open items."""
    await _require_project(project_id)
    if status and status not in ("open", "done"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status {status!r}. Use 'open' or 'done'.",
        )
    rows = await transcript_store.list_action_items(project_id, status=status)
    return [
        ActionItemOut(
            id=r.id,
            source=r.source,
            owner=r.owner,
            text=r.text,
            due_date=r.due_date,
            status=r.status,
            created_at=r.created_at,
        )
        for r in rows
    ]


@app.get("/projects/{project_id}/risks", response_model=list[RiskOut])
async def get_risks(project_id: str) -> list[RiskOut]:
    """List all risks extracted from transcripts for this project, newest first."""
    await _require_project(project_id)
    rows = await transcript_store.list_risks(project_id)
    return [
        RiskOut(id=r.id, source=r.source, text=r.text, created_at=r.created_at)
        for r in rows
    ]


def _workflow_out(w) -> WorkflowOut:
    return WorkflowOut(
        name=w.name,
        trigger=w.trigger,
        description=w.description,
        created_at=w.created_at,
        steps=[
            WorkflowStepOut(
                step_order=s.step_order,
                tool_name=s.tool_name,
                arguments=json.loads(s.arguments),
                description=s.description,
            )
            for s in w.steps
        ],
    )


@app.post("/projects/{project_id}/workflows", response_model=WorkflowOut)
async def save_workflow(project_id: str, req: WorkflowCreateRequest) -> WorkflowOut:
    """Create or replace a stored workflow by name.

    Explicit, hand-authored procedure storage — steps aren't derived from
    live tool-call history (see workflow.py header). Re-saving the same name
    replaces its steps rather than duplicating them.
    """
    await _require_project(project_id)
    workflow = await workflow_store.save_workflow(
        project_id,
        req.name,
        req.trigger,
        [s.model_dump() for s in req.steps],
        description=req.description,
    )
    return _workflow_out(workflow)


@app.get("/projects/{project_id}/workflows", response_model=list[WorkflowOut])
async def list_workflows(project_id: str) -> list[WorkflowOut]:
    """List all stored workflows for a project, newest first."""
    await _require_project(project_id)
    rows = await workflow_store.list_by_project(project_id)
    return [_workflow_out(w) for w in rows]


@app.delete("/projects/{project_id}/workflows/{name}")
async def delete_workflow(project_id: str, name: str):
    await _require_project(project_id)
    await workflow_store.delete_by_name(project_id, name)
    return {"status": "deleted"}


@app.get("/projects/{project_id}/entities", response_model=list[EntityOut])
async def list_entities(project_id: str) -> list[EntityOut]:
    """List all tracked entities for a project, most recently updated first.

    Read-only inspection of entity memory — populated by ingest-time
    extraction (see _process_document_entities), not created directly here.
    """
    await _require_project(project_id)
    rows = await entity_store.list_by_project(project_id)
    return [
        EntityOut(
            id=e.id,
            name=e.name,
            type=e.type,
            attributes=e.attributes,
            sources=json.loads(e.sources),
            created_at=e.created_at,
            updated_at=e.updated_at,
        )
        for e in rows
    ]


@app.get("/projects/{project_id}/toolbox/stats", response_model=list[ToolStatsOut])
async def get_toolbox_stats(project_id: str) -> list[ToolStatsOut]:
    """Per-tool success/failure track record for this project, derived from
    the tool_calls log (toolbox.py). Read-only visibility — the /chat route
    also draws on this to annotate matched workflow steps.
    """
    await _require_project(project_id)
    rows = await toolbox_store.get_stats(project_id)
    return [
        ToolStatsOut(
            tool_name=r.tool_name,
            call_count=r.call_count,
            success_count=r.success_count,
            failure_count=r.failure_count,
            success_rate=r.success_rate,
            avg_duration_ms=r.avg_duration_ms,
            last_used_at=r.last_used_at,
        )
        for r in rows
    ]


@app.get("/projects/{project_id}/cache/stats", response_model=CacheStatsOut)
async def get_cache_stats(project_id: str) -> CacheStatsOut:
    """Embedding-cache entry count + cumulative hit count for this project,
    derived from the embedding_cache table (semantic_cache.py). Read-only
    visibility into whether the cache is actually absorbing repeated embeds.
    """
    await _require_project(project_id)
    stats = await semantic_cache_store.get_stats(project_id)
    return CacheStatsOut(entry_count=stats.entry_count, total_hits=stats.total_hits)


@app.get("/projects/{project_id}/scratchpad", response_model=list[ScratchpadEntryOut])
async def get_scratchpad(
    project_id: str,
    session_id: str = Query(..., description="Which conversation's scratchpad to read."),
) -> list[ScratchpadEntryOut]:
    """Read-only inspection of the deep-research scratchpad for a session.

    Debuggability only — no create/update route, since only the loop itself
    (deep_research.py) writes entries.
    """
    await _require_project(project_id)
    rows = await scratchpad_store.list_by_session(project_id, session_id)
    return [
        ScratchpadEntryOut(
            key=r.key, value=r.value, created_at=r.created_at, updated_at=r.updated_at
        )
        for r in rows
    ]


def _flashcard_out(c) -> FlashcardOut:
    return FlashcardOut(
        id=c.id, source=c.source, front=c.front, back=c.back,
        ease_factor=c.ease_factor, interval_days=c.interval_days,
        repetitions=c.repetitions, due_at=c.due_at,
        last_reviewed_at=c.last_reviewed_at, created_at=c.created_at,
    )


@app.post(
    "/projects/{project_id}/sources/{source}/flashcards/generate",
    response_model=list[FlashcardOut],
)
async def generate_flashcards_for_source(project_id: str, source: str) -> list[FlashcardOut]:
    """Generate flashcards from an already-ingested source's full text.

    Additive only — re-running this for a source you've already generated
    cards for adds more candidates, it never touches or replaces existing
    cards (which may have real spaced-repetition progress). Remove a card
    you don't want via DELETE /flashcards/{id} instead.
    """
    await _require_project(project_id)
    chunks = await rag.retrieve_by_source(project_id, source, vstore)
    if not chunks:
        raise HTTPException(status_code=404, detail=f"No ingested content found for source {source!r}")
    text = "".join(c.text for c in sorted(chunks, key=lambda c: c.chunk_index))

    try:
        candidates = await generate_candidates(text, chat)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    created = await flashcard_store.save_generated(project_id, source, candidates)
    return [_flashcard_out(c) for c in created]


@app.get("/projects/{project_id}/flashcards", response_model=list[FlashcardOut])
async def list_flashcards(project_id: str) -> list[FlashcardOut]:
    """List all flashcards for a project, soonest-due first."""
    await _require_project(project_id)
    rows = await flashcard_store.list_by_project(project_id)
    return [_flashcard_out(c) for c in rows]


@app.post("/flashcards/{card_id}/review", response_model=FlashcardOut)
async def review_flashcard(card_id: str, req: FlashcardReviewRequest) -> FlashcardOut:
    """Record a review and reschedule the card's next due date via SM-2."""
    card = await flashcard_store.get(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Flashcard not found")
    fields = schedule_review(
        card.ease_factor, card.interval_days, card.repetitions, req.quality,
    )
    await flashcard_store.update_review(card_id, fields)
    updated = await flashcard_store.get(card_id)
    return _flashcard_out(updated)


@app.patch("/flashcards/{card_id}", response_model=FlashcardOut)
async def update_flashcard(card_id: str, req: FlashcardUpdateRequest) -> FlashcardOut:
    """Manually correct a generated card's front/back text."""
    card = await flashcard_store.get(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Flashcard not found")
    await flashcard_store.update_text(card_id, req.front, req.back)
    updated = await flashcard_store.get(card_id)
    return _flashcard_out(updated)


@app.delete("/flashcards/{card_id}")
async def delete_flashcard(card_id: str):
    card = await flashcard_store.get(card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Flashcard not found")
    await flashcard_store.delete(card_id)
    return {"deleted": True}


@app.get("/projects/{project_id}/briefing", response_model=BriefingOut)
async def get_briefing(project_id: str) -> BriefingOut:
    """Return a concise status snapshot for the project: open actions, recent decisions, active risks, and an AI-generated summary."""
    await _require_project(project_id)

    b = await briefing.assemble_briefing(
        project_id=project_id,
        transcript_store=transcript_store,
        vector_store=vstore,
        chat_fn=chat,
    )

    return BriefingOut(
        summary=b.summary,
        open_actions=[
            BriefActionOut(
                id=a.id,
                text=a.text,
                owner=a.owner,
                due_date=a.due_date,
                status=a.status,
                source=a.source,
            )
            for a in b.open_actions
        ],
        recent_decisions=[
            BriefDecisionOut(
                id=d.id, text=d.text, source=d.source, created_at=d.created_at
            )
            for d in b.recent_decisions
        ],
        active_risks=[
            BriefRiskOut(id=r.id, text=r.text, source=r.source, created_at=r.created_at)
            for r in b.active_risks
        ],
        generated_at=b.generated_at,
    )


@app.get("/projects/{project_id}/standup", response_model=StandupOut)
async def get_standup(project_id: str) -> StandupOut:
    """Daily Yesterday/Today/Blockers standup for the project, scoped to the last 24h."""
    await _require_project(project_id)
    s = await standup.assemble_standup(
        project_id=project_id,
        transcript_store=transcript_store,
        chat_fn=chat,
    )
    return StandupOut(
        summary=s.summary,
        done=s.done,
        today=s.today,
        blockers=s.blockers,
        generated_at=s.generated_at,
    )


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
      - SQLite:  any transcript-derived decisions/action_items/risks and any
                 generated flashcards for this source, and any stored
                 enabled/disabled state
    """
    await _require_project(project_id)
    await vstore.delete_by_source(settings.qdrant_docs_collection, project_id, source)
    await transcript_store.delete_by_source(project_id, source)
    await document_state_store.delete_by_source(project_id, source)
    await flashcard_store.delete_by_source(project_id, source)
    logger.info("Deleted source %r from project %s", source, project_id)
    return {"deleted": True}


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
    query_vec = await embed_cached(semantic_cache_store, req.project_id, req.message)
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
        cache=semantic_cache_store,
    )

    logger.info(
        "RAG[%s]: retrieved %d doc chunks for query %r: %s",
        req.project_id,
        len(doc_chunks),
        req.message,
        [(c.source, c.chunk_index) for c in doc_chunks],
    )

    # 3c. Look up a stored workflow whose trigger keywords match this
    # message — read-only guidance, nothing executes automatically (see
    # workflow.py header / MEMORY_PLAN.md).
    matched_workflow = await workflow_store.find_matching(req.project_id, req.message)

    # 3e. Look up tracked entities (people/stakeholders/tickets/orgs) named
    # in this message — passive background context, same read-only guidance
    # mechanism as the matched workflow above.
    matched_entities = await entity_store.find_matching(req.project_id, req.message)

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

    # 3d. If the user explicitly turned on web search for this message, search
    # now (before the single LLM call below) and fold results into the same
    # citation numbering as doc chunks. This is user-triggered, not model-
    # triggered — the LLM never decides to search on its own — so no second
    # LLM round-trip is needed; the results are just another context block.
    web_results: list[dict] = []
    web_search_error: str | None = None
    if req.web_search:
        try:
            web_response = await _mcp.call(
                "web_search", {"query": req.message, "limit": 5}, project_id=req.project_id
            )
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

    # Inject tracked entities named in this message — background context,
    # not a citable source, so it gets a plain (non-numbered) system block
    # rather than participating in the [N] citation numbering above.
    if matched_entities:
        entity_lines = "\n".join(
            f"- {e.name} ({e.type}): {e.attributes or 'no notes'} "
            f"[seen in: {', '.join(json.loads(e.sources))}]"
            for e in matched_entities
        )
        messages.append(
            {
                "role": "system",
                "content": (
                    "The following entities (people, stakeholders, tickets, "
                    "organizations) tracked for this project may be relevant "
                    "to the user's message:\n\n"
                    "--- KNOWN ENTITIES ---\n"
                    f"{entity_lines}\n"
                    "--- END KNOWN ENTITIES ---"
                ),
            }
        )

    # Inject a matched workflow, if one's trigger keywords hit this message.
    # This is reference guidance only — a description of how this kind of
    # request was handled before, not something the model can execute; this
    # codebase has no LLM-driven tool-calling loop for it to invoke.
    if matched_workflow:
        step_lines_list = []
        for s in matched_workflow.steps:
            line = f"{s.step_order + 1}. {s.tool_name}({s.arguments})"
            if s.description:
                line += f" — {s.description}"
            # Annotate with the tool's real track record for this project,
            # when there's any logged history — turns toolbox.py's write-only
            # tool_calls log into something the prompt actually draws on.
            stats = await toolbox_store.get_stats_for_tool(req.project_id, s.tool_name)
            if stats and stats.call_count > 0:
                line += f" [{stats.success_count}/{stats.call_count} succeeded]"
            step_lines_list.append(line)
        step_lines = "\n".join(step_lines_list)
        messages.append(
            {
                "role": "system",
                "content": (
                    "The user has a stored procedure that may be relevant to this "
                    "request, based on similar past requests. This is reference "
                    "guidance for how this kind of task was handled before — "
                    "describe or follow the approach in your reply, but you cannot "
                    "execute these tool calls yourself.\n\n"
                    f"--- KNOWN PROCEDURE: {matched_workflow.name} ---\n"
                    f"{step_lines}\n"
                    "--- END KNOWN PROCEDURE ---"
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

    # 6. Call the LLM — either a single plain call, or the opt-in multi-step
    # tool-calling research loop (see deep_research.py). Backend and model
    # are fixed by deploy-time config either way.
    if req.deep_research:
        # Fresh scratchpad per research task-invocation (see scratchpad.py
        # header) — clear before this task's steps get written.
        await scratchpad_store.clear_session(req.project_id, req.session_id)
        research_result = await deep_research.run_research_loop(
            messages,
            project_id=req.project_id,
            session_id=req.session_id,
            mcp=_mcp,
            scratchpad=scratchpad_store,
            chat_fn=chat,
        )
        reply = research_result.reply
        logger.info(
            "Deep research[%s/%s]: %d steps taken",
            req.project_id,
            req.session_id,
            len(research_result.steps),
        )
    else:
        reply = await chat(messages)

    # 7. Persist the assistant reply to SQLite.
    await store.append(req.project_id, req.session_id, "assistant", reply)

    # 8. Store the assistant reply vector in Qdrant conversations.
    #
    # Degrade, don't fail — the LLM has already produced a real answer, so an
    # embedding error here (e.g. the reply exceeds the embeddings model's max
    # input length, more likely with web_search since replies run longer)
    # must not turn a successful turn into a 502. Same "degrade don't fail"
    # pattern as the web_search failure path above; conversation-memory
    # search just won't find this one reply, which is a lesser cost than
    # discarding the reply entirely.
    try:
        reply_vec = await embed_cached(semantic_cache_store, req.project_id, reply)
        await vstore.upsert(
            settings.qdrant_collection,
            project_id=req.project_id,
            vector=reply_vec,
            payload={"session_id": req.session_id, "role": "assistant", "content": reply},
        )
    except HTTPException as exc:
        logger.warning(
            "Failed to embed/store assistant reply vector for project=%s session=%s: %s",
            req.project_id, req.session_id, exc.detail,
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

    vec = await embed_cached(semantic_cache_store, project_id, q)
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
