const BASE = '/api';

async function request(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(BASE + path, opts);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

export const listProjects = () => request('GET', '/projects');
export const createProject = (name, externalRefs = {}) =>
  request('POST', '/projects', { name, external_refs: externalRefs });
export const patchProject = (id, patch) => request('PATCH', `/projects/${id}`, patch);
export const deleteProject = (id) => request('DELETE', `/projects/${id}`);

// Rename hits the real PATCH /projects/{id} endpoint — not a stub.
export const renameProject = (id, name) => patchProject(id, { name });

export const chat = (projectId, sessionId, message) =>
  request('POST', '/chat', { project_id: projectId, session_id: sessionId, message });

export const getHistory = (projectId, sessionId = 'default') =>
  request('GET', `/projects/${projectId}/history?session_id=${encodeURIComponent(sessionId)}`);

export const memorySearch = (projectId, q, k = 5) =>
  request('GET', `/memory/search?project_id=${encodeURIComponent(projectId)}&q=${encodeURIComponent(q)}&k=${k}`);

export const syncProject = (id) => request('POST', `/projects/${id}/sync`);

export const listActions = (projectId, status) => {
  const qs = status ? `?status=${status}` : '';
  return request('GET', `/projects/${projectId}/actions${qs}`);
};
export const approveAction = (actionId) => request('POST', `/actions/${actionId}/approve`);
export const rejectAction = (actionId) => request('POST', `/actions/${actionId}/reject`);

// Step 10: Transcript processing
export const ingestTranscript = (projectId, source, text) =>
  request('POST', '/ingest/transcript', { project_id: projectId, source, text });

export const listDecisions = (projectId) =>
  request('GET', `/projects/${projectId}/decisions`);

export const listRisks = (projectId) =>
  request('GET', `/projects/${projectId}/risks`);

// Step 11: Project briefing
export const getBriefing = (projectId) =>
  request('GET', `/projects/${projectId}/briefing`);

// Standup: daily Yesterday/Today/Blockers view
export const getStandup = (projectId) =>
  request('GET', `/projects/${projectId}/standup`);

export const listSources = (projectId) =>
  request('GET', `/projects/${projectId}/sources`);

export const setSourceEnabled = (projectId, source, enabled) =>
  request('PATCH', `/projects/${projectId}/sources/${encodeURIComponent(source)}`, { enabled });

export const deleteSource = (projectId, source) =>
  request('DELETE', `/projects/${projectId}/sources/${encodeURIComponent(source)}`);

// File upload (multipart) — kind is "document" or "transcript".
export async function ingestFile(projectId, file, kind) {
  const fd = new FormData();
  fd.append('project_id', projectId);
  fd.append('kind', kind);
  fd.append('file', file);
  const res = await fetch(BASE + '/ingest/file', { method: 'POST', body: fd });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  return res.json();
}

export const ingestUrl = (projectId, url, kind) =>
  request('POST', '/ingest/url', { project_id: projectId, url, kind });

// ─── Setup wizard ───────────────────────────────────────────────────────
// TODO(backend): none of the functions below have a real endpoint yet.
// `GET /health` (main.py) is a bare liveness check with no per-dependency
// status. There is no model-pull, model-list, or credential-test endpoint
// anywhere in chat-agent or mcp-server. These stubs return realistic-shaped
// fake data with a small delay so the wizard UI is fully functional on its
// own; swap the bodies for real `fetch` calls once the backend exists —
// component code should not need to change.

const fakeDelay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// TODO(backend): replace with a real aggregate health endpoint, e.g.
// `GET /health/detailed`, that reports each dependency's reachability.
export async function checkSystemHealth() {
  await fakeDelay(700);
  return {
    ollama: { status: 'ok', detail: 'v0.3.12 — reachable at localhost:11434', required: true },
    qdrant: { status: 'ok', detail: 'v1.13.1 — reachable at localhost:6333', required: true },
    docker: { status: 'ok', detail: 'Docker Desktop running', required: false },
    // mcp-server only brokers Jira/GitHub sync — everything else (chat, RAG,
    // memory) works without it, so it's optional here rather than a blocker.
    mcpServer: { status: 'ok', detail: 'v0.1.0 — reachable at localhost:8083', required: false },
  };
}

// TODO(backend): replace with a real fix action (e.g. `docker compose up -d`,
// or an OS-level service restart) exposed via a future admin endpoint.
export async function fixService(serviceName) {
  await fakeDelay(1200);
  return {
    success: true,
    log: [
      `Attempting to start ${serviceName}...`,
      `${serviceName} responded after restart.`,
    ],
  };
}

// TODO(backend): replace with a real "list locally-installed Ollama models"
// call (e.g. proxied through chat-agent to `GET /api/tags` on Ollama).
export async function listLocalModels() {
  await fakeDelay(400);
  return { installed: ['nomic-embed-text'] };
}

// TODO(backend): replace with a real model pull, streaming progress from
// Ollama's `POST /api/pull` (which already reports done/total bytes) through
// to the caller instead of faking it here.
export async function pullModel(modelName, onProgress) {
  const total = 2_000_000_000;
  let downloaded = 0;
  while (downloaded < total) {
    await fakeDelay(200);
    downloaded = Math.min(total, downloaded + total / 8);
    onProgress?.({ downloaded, total });
  }
  return { ok: true };
}

// TODO(backend): replace with a real Jira connection test, proxied through
// mcp-server (the only process allowed to hold JIRA_* credentials).
export async function testJiraConnection({ url, token }) {
  await fakeDelay(600);
  if (!url || !token) return { ok: false, message: 'URL and token are required.' };
  return { ok: true, message: 'Connected successfully.' };
}

// TODO(backend): replace with a real GitHub connection test, proxied
// through mcp-server (the only process allowed to hold GITHUB_TOKEN).
export async function testGitHubConnection({ token }) {
  await fakeDelay(600);
  if (!token) return { ok: false, message: 'Token is required.' };
  return { ok: true, message: 'Connected successfully.' };
}

// ─── Settings page ──────────────────────────────────────────────────────
// TODO(backend): all functions below are stubs. `LLM_PROVIDER`, model names,
// and JIRA_*/GITHUB_TOKEN are deploy-time-only env vars today (see
// services/chat-agent/config.py) — there is no runtime read or write path
// anywhere in chat-agent or mcp-server. These return fake-but-realistic data
// so the Settings UI is demoable; the UI treats this data as read-only /
// informational rather than something a "Save" button submits, since there
// is nowhere real for it to go yet.

// TODO(backend): replace with a real `GET /config`-style endpoint once one
// exists. Until then this is illustrative only.
export async function getLlmConfig() {
  await fakeDelay(300);
  return {
    provider: 'ollama',
    ollama: { chatModel: 'llama3' },
    openai: { baseUrl: '', apiKey: '', model: '', configured: false },
  };
}

// Lightweight reachability check reused by both the LLM Models and
// Embeddings tabs (embeddings always route through Ollama regardless of
// chat provider — see services/chat-agent/config.py).
export async function testOllamaConnection() {
  await fakeDelay(500);
  return { ok: true, message: 'Ollama reachable at localhost:11434.' };
}

// TODO(backend): mcp-server already computes jiraIsConfigured()/
// githubIsConfigured() internally (services/mcp-server/internal/tools/
// jira.go, github.go) but doesn't expose them via any route. Replace with a
// real status endpoint once one exists.
export async function getIntegrationStatus() {
  await fakeDelay(400);
  return {
    jira: { configured: false, baseUrl: null },
    github: { configured: false },
  };
}

// TODO(backend): replace with a real start/stop/restart action. No process
// control endpoint exists on chat-agent or mcp-server today; docker-compose.yml
// is a static dev file, not something the app can invoke.
export async function controlService(serviceName, action) {
  await fakeDelay(1000);
  return {
    success: true,
    log: [`${action}: ${serviceName}...`, `${serviceName} ${action} completed.`],
  };
}

// TODO(backend): replace with a real settings/env read endpoint. Key names
// mirror .env.example; values below are illustrative placeholders, not real
// user data (there is nothing real to read yet).
export async function listEnvVars() {
  await fakeDelay(300);
  return [
    { key: 'LLM_PROVIDER', value: 'ollama', secret: false },
    { key: 'OLLAMA_CHAT_MODEL', value: 'llama3', secret: false },
    { key: 'OPENAI_BASE_URL', value: '', secret: false },
    { key: 'OPENAI_API_KEY', value: '', secret: true },
    { key: 'OPENAI_MODEL', value: '', secret: false },
    { key: 'OPENAI_PROVIDER_LABEL', value: '', secret: false },
    { key: 'OLLAMA_BASE_URL', value: 'http://localhost:11434', secret: false },
    { key: 'OLLAMA_EMBED_MODEL', value: 'nomic-embed-text', secret: false },
    { key: 'QDRANT_URL', value: 'http://localhost:6333', secret: false },
    { key: 'JIRA_BASE_URL', value: '', secret: false },
    { key: 'JIRA_EMAIL', value: '', secret: false },
    { key: 'JIRA_API_TOKEN', value: '', secret: true },
    { key: 'GITHUB_TOKEN', value: '', secret: true },
    { key: 'BRAVE_SEARCH_API_KEY', value: '', secret: true },
    { key: 'MCP_BASE_URL', value: 'http://localhost:8083', secret: false },
  ];
}

// TODO(backend): replace with a real settings/env write endpoint. Faking
// success here doesn't persist anything — the Advanced tab's banner makes
// that explicit to the user.
export async function updateEnvVar(key, value) {
  await fakeDelay(400);
  return { ok: true, key, value };
}
