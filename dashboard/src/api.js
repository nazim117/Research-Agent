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

// ─── Setup wizard / Settings — system health, models, config ────────────
// Backed by real endpoints added to chat-agent (health.py, ollama_models.py,
// GET /config) and mcp-server (GET /integrations/status). See TODO(backend)
// notes below for what's still stubbed (nothing here reads/writes secrets,
// and nothing controls services — see fixService/controlService/env vars).

const fakeDelay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export const checkSystemHealth = () => request('GET', '/health/detailed');

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

export const listLocalModels = () => request('GET', '/models');

// Streams progress from Ollama's own pull API (proxied by chat-agent's
// POST /models/pull) — not a stub. Reads newline-delimited JSON as it
// arrives and reports {downloaded, total} whenever the current line
// includes those fields; resolves when a line reports status "success",
// rejects if a line carries an "error" field (Ollama reports pull failures
// like an unknown model name this way, inside an otherwise-200 stream) or
// the stream ends without ever reporting success.
export async function pullModel(modelName, onProgress) {
  const res = await fetch(BASE + '/models/pull', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: modelName }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let succeeded = false;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop(); // last element may be a partial line — carry into next chunk

    for (const line of lines) {
      if (!line.trim()) continue;
      const data = JSON.parse(line);
      if (data.error) throw new Error(data.error);
      if (data.completed !== undefined && data.total !== undefined) {
        onProgress?.({ downloaded: data.completed, total: data.total });
      }
      if (data.status === 'success') succeeded = true;
    }
  }

  if (!succeeded) throw new Error(`Model pull did not complete for "${modelName}".`);
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

export const getLlmConfig = () => request('GET', '/config');

// Lightweight reachability check reused by both the LLM Models and
// Embeddings tabs (embeddings always route through Ollama regardless of
// chat provider — see services/chat-agent/config.py). Derived from the real
// aggregate health check rather than its own endpoint.
export async function testOllamaConnection() {
  const health = await checkSystemHealth();
  const ollama = health.ollama;
  return { ok: ollama.status === 'ok', message: ollama.detail };
}

export const getIntegrationStatus = () => request('GET', '/integrations/status');

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
