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
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse
from typing import Any

from pydantic import BaseModel, Field, field_validator

import httpx

from actions import ActionStore, Action, execute_action, VALID_ACTION_TYPES, validate_payload
from config import settings
from embeddings import embed
from mcp_client import MCPClient, MCPError
from llm import chat, validate_llm_config
from memory import ConversationStore
from projects import SCHEMA_VERSION, Project, ProjectStore
import rag
from request_context import set_request_id, new_request_id
from sync import SyncStore, sync_project
from transcript import (
    TranscriptStore,
    extract_structured,
)
from vectors import VectorStore
import briefing
import standup
from extractors import extract_file, extract_url

logger = logging.getLogger("uvicorn.error")

project_store = ProjectStore(settings.sqlite_path)
store = ConversationStore(settings.sqlite_path)
vstore = VectorStore(url=settings.qdrant_url)
sync_store = SyncStore(settings.sqlite_path)
action_store = ActionStore(settings.sqlite_path)
transcript_store = TranscriptStore(settings.sqlite_path)

# Shared MCPClient — the single gateway to all PM vendor APIs.
# Credentials (JIRA_*, GITHUB_TOKEN) live on the mcp-server; this service
# never reads them.  If the mcp-server is unreachable or a tool is not
# configured, sync/approve return HTTP 502 with a clear error message.
_mcp = MCPClient(
    base_url=settings.mcp_base_url,
    timeout=settings.mcp_timeout_s,
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

    # SyncStore and TranscriptStore tables are always safe to create — no
    # schema coupling to the version wipe above.
    await sync_store.init()
    await transcript_store.init()

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
    # Optional bag of external references (Jira key, GitHub repo, ...).
    external_refs: dict = Field(default_factory=dict)


class ProjectUpdateRequest(BaseModel):
    # Both fields optional — callers send only what they want to change.
    name: str | None = None
    external_refs: dict | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    created_at: str
    external_refs: dict[str, Any] | None = None

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
        external_refs=p.external_refs,
    )


# Chat / ingest models (all now require project_id)
class ChatRequest(BaseModel):
    project_id: str  # Which project brain owns this conversation.
    session_id: str  # Identifies which conversation inside the project.
    message: str  # The user's input text.


class Citation(BaseModel):
    ref: int  # 1-based reference number matching [N] in the reply text.
    source: str  # The source label (e.g. "jira:KAN-1", "notes.md").
    chunk_index: int  # Position of this chunk in the original document (0-based).


class ChatResponse(BaseModel):
    reply: str  # The assistant's reply text.
    citations: list[Citation] = []  # Chunks the reply may have drawn from.


class IngestRequest(BaseModel):
    project_id: str  # Which project brain to add this document to.
    source: str  # A label for the document — filename, URL, or any identifier.
    text: str  # The full document text to index.


class IngestResponse(BaseModel):
    chunks: int  # How many chunks were stored in Qdrant.


class IngestTranscriptRequest(BaseModel):
    project_id: str  # Which project brain owns this transcript.
    source: str  # Label for the meeting (e.g. "meeting-2026-05-12").
    text: str  # Raw transcript text.


class IngestTranscriptResponse(BaseModel):
    chunks: int  # RAG chunks stored in Qdrant.
    decisions: int  # Structured decisions extracted + stored.
    action_items: int  # Structured action items extracted + stored.
    risks: int  # Structured risks extracted + stored.


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
    source: str   # Label supplied at ingest time (e.g. "notes.md", "jira:KAN-1").
    chunks: int   # Number of Qdrant points stored under this source label.


class MemoryHit(BaseModel):
    score: float  # Cosine similarity (0–1, higher = more similar).
    role: str  # 'user' or 'assistant'.
    content: str  # The original message text.
    session_id: str  # Which conversation the hit came from.


class ProposeActionRequest(BaseModel):
    action_type: str  # Must be in VALID_ACTION_TYPES.
    payload: dict  # {"item_id": "...", "body": "...", "ref_key": "..."}


class ActionOut(BaseModel):
    id: str
    project_id: str
    action_type: str
    status: str
    payload: dict
    created_at: str
    completed_at: str | None


def _action_to_out(a: Action) -> ActionOut:
    return ActionOut(
        id=a.id,
        project_id=a.project_id,
        action_type=a.action_type,
        status=a.status,
        payload=a.payload,
        created_at=a.created_at,
        completed_at=a.completed_at,
    )


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


# Routes — project CRUD
@app.post("/projects", response_model=ProjectOut)
async def post_projects(req: ProjectCreateRequest) -> ProjectOut:
    """Create a new project brain.

    The returned id is a UUID string — stable, safe to use in URLs/JSON.
    """
    try:
        project = await project_store.create(
            name=req.name,
            external_refs=req.external_refs,
        )
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
    """Partial update — change name and/or external_refs."""
    try:
        updated = await project_store.update(
            project_id,
            name=req.name,
            external_refs=req.external_refs,
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
    await sync_store.delete_by_project(project_id)
    # action_store.delete_by_project is also safe to call here even though
    # sync_store already deleted the same rows — the second DELETE is a no-op.
    await action_store.delete_by_project(project_id)
    await transcript_store.delete_by_project(project_id)

    deleted = await project_store.delete(project_id)
    if not deleted:
        raise HTTPException(
            status_code=404, detail=f"Project {project_id!r} not found."
        )

    logger.info("Deleted project %s and all of its memory.", project_id)
    return {"deleted": True}


# Routes — PM sync

@app.post("/projects/{project_id}/sync")
async def post_sync(project_id: str):
    """Fetch items from all configured PM integrations for this project and
    ingest them into the project's RAG document store.

    Idempotent: items already ingested are overwritten with the same content,
    so re-running sync does not grow the vector store unboundedly.  The
    last_synced_at timestamp per external ref means only items updated since
    the last sync are fetched on subsequent runs (incremental).

    Requires the project to have at least one entry in external_refs whose
    key matches a configured integration (jira_project_key or github_repo).
    The matching credentials must be set as environment variables:
      Jira:   JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN
      GitHub: GITHUB_TOKEN
    """
    project = await _require_project(project_id)

    if not project.external_refs:
        raise HTTPException(
            status_code=400,
            detail="Project has no external_refs — attach a jira_project_key or github_repo first.",
        )

    try:
        results = await sync_project(
            project_id=project_id,
            external_refs=project.external_refs,
            sync_store=sync_store,
            vstore=vstore,
            mcp=_mcp,
        )
    except MCPError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"PM API returned {exc.response.status_code}: {exc.response.text[:200]}",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach PM API: {exc}",
        ) from exc

    return {
        "synced_items": sum(r.items_fetched for r in results),
        "chunks_stored": sum(r.chunks_stored for r in results),
        "details": [
            {
                "ref_key": r.ref_key,
                "ref_value": r.ref_value,
                "items": r.items_fetched,
                "chunks": r.chunks_stored,
            }
            for r in results
        ],
    }


@app.get("/projects/{project_id}/sync")
async def get_sync_status(project_id: str):
    """Return the last-synced timestamp per external ref for this project.

    Useful for displaying sync status in the UI without triggering a sync.
    Returns an empty refs list if the project has never been synced.
    """
    await _require_project(project_id)
    status = await sync_store.get_sync_status(project_id)
    return {"refs": status}


# Routes — pending actions

@app.post("/projects/{project_id}/actions", response_model=ActionOut)
async def propose_action(project_id: str, req: ProposeActionRequest) -> ActionOut:
    """Create a pending action for human approval.

    The agent (via the DRAFT_ACTION chat post-processor) or a human in the
    extension form calls this endpoint.  Nothing is written to Jira/GitHub
    until /actions/{id}/approve is called.
    """
    await _require_project(project_id)
    if req.action_type not in VALID_ACTION_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action_type {req.action_type!r}. "
            f"Allowed: {sorted(VALID_ACTION_TYPES)}",
        )
    missing = validate_payload(req.action_type, req.payload)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"payload must include non-empty '{missing}'",
        )
    action_id = await action_store.create_pending(
        project_id, req.action_type, req.payload
    )
    action = await action_store.get(action_id)
    return _action_to_out(action)


@app.get("/projects/{project_id}/actions", response_model=list[ActionOut])
async def list_actions(
    project_id: str,
    status: str | None = Query(None, description="Filter by status (e.g. 'pending')."),
) -> list[ActionOut]:
    """List actions for a project, newest first. Pass ?status=pending to filter."""
    await _require_project(project_id)
    actions = await action_store.list_for_project(project_id, status=status)
    return [_action_to_out(a) for a in actions]


@app.post("/actions/{action_id}/approve")
async def approve_action(action_id: str):
    """Approve a pending action and execute it immediately.

    On success: returns {status: "executed", result: {id, url, created_at}}.
    On integration failure: marks the action as 'failed' and returns 502.
    """
    action = await action_store.get(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail=f"Action {action_id!r} not found.")
    if action.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Action {action_id!r} is {action.status!r}, not 'pending'.",
        )

    await action_store.approve(action_id)
    # Re-fetch with updated status so execute_action sees it correctly.
    action = await action_store.get(action_id)

    try:
        result = await execute_action(action, _mcp, project_store)
    except Exception as exc:
        error_msg = str(exc)
        await action_store.mark_failed(action_id, error_msg)
        logger.error("Action %s failed: %s", action_id, error_msg)
        raise HTTPException(
            status_code=502,
            detail=f"Integration call failed: {error_msg}",
        ) from exc

    await action_store.mark_executed(action_id, result)
    logger.info("Action %s executed: %s", action_id, result.get("url", ""))
    return {"status": "executed", "result": result}


@app.post("/actions/{action_id}/reject")
async def reject_action(action_id: str):
    """Reject a pending action. No write is made to the PM system."""
    action = await action_store.get(action_id)
    if action is None:
        raise HTTPException(status_code=404, detail=f"Action {action_id!r} not found.")
    try:
        await action_store.reject(action_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "rejected"}


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
    logger.info(
        "Transcript[%s] source=%r: %d chunks, %d decisions, %d action_items, %d risks",
        project_id,
        source,
        chunk_count,
        counts["decisions"],
        counts["action_items"],
        counts["risks"],
    )
    return IngestTranscriptResponse(
        chunks=chunk_count,
        decisions=counts["decisions"],
        action_items=counts["action_items"],
        risks=counts["risks"],
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
    return IngestTranscriptResponse(
        chunks=chunks, decisions=0, action_items=0, risks=0
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
    return IngestTranscriptResponse(
        chunks=chunks, decisions=0, action_items=0, risks=0
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
    summaries = await rag.list_sources(project_id=project_id, vstore=vstore)
    return [SourceOut(source=s.source, chunks=s.chunks) for s in summaries]


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
    )

    # 3b. Search documents collection for relevant chunks (RAG), same filter.
    doc_chunks = await rag.retrieve(req.project_id, req.message, k=3, vstore=vstore)

    # 3c. If the message explicitly names ticket keys (e.g. "KAN-8", "#42"),
    # fetch those chunks by exact source label and prepend them.  Semantic
    # search alone misses these when the query is action-oriented ("write a
    # comment to KAN-8") rather than content-oriented.
    _jira_keys = re.findall(r'\b([A-Z][A-Z0-9]+-\d+)\b', req.message)
    _gh_nums   = re.findall(r'#(\d+)', req.message)
    _pinned_sources = (
        [f"jira:{k}" for k in _jira_keys]
        + [f"github:{n}" for n in _gh_nums]
    )
    _seen_sources = {c.source for c in doc_chunks}
    for _src in _pinned_sources:
        if _src not in _seen_sources:
            _pinned = await rag.retrieve_by_source(req.project_id, _src, vstore=vstore)
            if _pinned:
                doc_chunks = _pinned + doc_chunks
                _seen_sources.add(_src)

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

    # Build citations list for the API response. ref is 1-based and matches
    # the [N] numbers injected into the prompt below.
    citations = [
        Citation(ref=i + 1, source=c.source, chunk_index=c.chunk_index)
        for i, c in enumerate(relevant_chunks)
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
                    "base — ingested documents and PM tickets (Jira / GitHub) synced "
                    "into this project by the user themselves. They are the "
                    "authoritative source of truth for this project.\n\n"
                    "Rules:\n"
                    "1. When the user asks about tickets, issues, tasks, status, or "
                    "   assignees, answer FROM these excerpts. The chunks ARE the "
                    "   data — they are not pointers to an external system.\n"
                    "2. Cite by reference number, e.g. [1], when you use information "
                    "   from a chunk. You may also include the source label for "
                    "   clarity, e.g. [1][jira:KAN-1].\n"
                    "3. CRITICAL: If a chunk is present below (e.g. [1] source: "
                    "   jira:KAN-8), that ticket's data IS available to you. Do NOT "
                    "   claim the ticket is missing, has not appeared, or that you "
                    "   cannot access it. Read the chunk and answer from it.\n"
                    "4. Never say 'it has not appeared in my knowledge base' or "
                    "   'I cannot retrieve' when the chunk is listed below.\n"
                    "5. If the excerpts truly do not contain the answer, say so "
                    "   plainly and suggest the user run Sync.\n\n"
                    "--- PROJECT KNOWLEDGE ---\n"
                    f"{doc_lines}\n"
                    "--- END PROJECT KNOWLEDGE ---"
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

    # Inject the TOOLS block when PM integrations are live for this project.
    # Only shown when the project has at least one ref matching a configured
    # integration — avoids cluttering prompts for projects with no PM links.
    project_for_tools = await project_store.get(req.project_id)
    live_refs = {
        k
        for k in (project_for_tools.external_refs if project_for_tools else {})
        if k in ("jira_project_key", "github_repo")
    }
    if live_refs:
        messages.append(
            {
                "role": "system",
                "content": (
                    "TOOLS YOU CAN PROPOSE\n"
                    "You may propose at most one write action per reply by emitting "
                    "this exact pattern on its own line:\n"
                    "<<DRAFT_ACTION>>{...}<<END>>\n"
                    "The user will approve or reject in the Pending Actions panel before "
                    "any write reaches the external system.\n\n"
                    "Supported action_type values and their payload shapes:\n"
                    '  jira:add_comment    — {"action_type":"jira:add_comment","payload":{"item_id":"PROJ-12","body":"...","ref_key":"jira_project_key"}}\n'
                    '  jira:create_issue   — {"action_type":"jira:create_issue","payload":{"ref_key":"jira_project_key","summary":"...","issue_type":"Task","description":"..."}}\n'
                    '  jira:update_issue   — {"action_type":"jira:update_issue","payload":{"item_id":"PROJ-12","ref_key":"jira_project_key","summary":"...","description":"..."}}\n'
                    '  jira:close_issue    — {"action_type":"jira:close_issue","payload":{"item_id":"PROJ-12","ref_key":"jira_project_key","status":"Done"}}\n'
                    '  github:add_comment  — {"action_type":"github:add_comment","payload":{"item_id":"42","body":"...","ref_key":"github_repo"}}\n\n'
                    "Rules: propose only when the user explicitly asks for a Jira or GitHub write. "
                    "issue_type and description are optional for create. "
                    "status is optional for close (defaults to Done). "
                    "For update, include at least summary or description."
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

    # 6. Call the LLM (backend and model fixed by deploy-time config).
    reply = await chat(messages)

    # Post-process the reply: extract <<DRAFT_ACTION>>{...}<<END>> if present.
    # On success, create a pending action and replace the tag with a human-
    # readable marker.  On any parse/validation error, strip the tag silently
    # so the raw JSON never reaches the user's transcript.
    draft_pattern = re.compile(r"<<DRAFT_ACTION>>(.*?)<<END>>", re.DOTALL)
    match = draft_pattern.search(reply)
    if match:
        raw_json = match.group(1).strip()
        try:
            draft = json.loads(raw_json)
            action_type = draft.get("action_type", "")
            payload = draft.get("payload", {})
            if (
                action_type in VALID_ACTION_TYPES
                and validate_payload(action_type, payload) is None
            ):
                action_id = await action_store.create_pending(
                    req.project_id, action_type, payload
                )
                replacement = (
                    f"\n[Drafted action #{action_id[:8]}… — "
                    "approve in the Pending Actions panel]\n"
                )
                logger.info(
                    "Drafted action %s (type=%s item=%s) for project %s",
                    action_id,
                    action_type,
                    payload.get("item_id"),
                    req.project_id,
                )
            else:
                replacement = ""
                logger.warning(
                    "DRAFT_ACTION block had missing or invalid fields — stripped. "
                    "action_type=%r payload keys=%s",
                    action_type,
                    list(payload.keys()),
                )
        except (json.JSONDecodeError, TypeError) as exc:
            replacement = ""
            logger.warning("Could not parse DRAFT_ACTION block: %s", exc)
        reply = draft_pattern.sub(replacement, reply).strip()

    # 7. Persist the assistant reply to SQLite (cleaned of any DRAFT_ACTION tags).
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
