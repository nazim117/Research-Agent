// sidepanel.js — UI logic for the Project Brain side panel.
//
// This script runs in the side panel's own page context (not in a tab, not in
// the background worker).  It can use:
//   - chrome.storage.local          — to persist the selected project + its session id
//   - chrome.runtime.sendMessage    — to ask background.js to read the active tab
//   - fetch()                       — to call the agent server directly
//
// Storage shape (chrome.storage.local):
//   {
//     currentProjectId: "<uuid>",
//     sessionByProject: { "<uuid>": "<session-uuid>", ... }
//   }
// Each project has its own session id so conversations do not mix even inside
// the extension's own UI.
//
// The agent server URL is hardcoded to the default port.  If you change
// settings.port in config.py, update AGENT_URL here too.

const AGENT_URL = "http://localhost:8080";

// ---------------------------------------------------------------------------
// Local state — mirrors chrome.storage.local, refreshed on load.
// ---------------------------------------------------------------------------
let projects = [];        // [{id, name, created_at, external_refs}, ...]
let currentProjectId = null;
let sessionByProject = {};  // projectId -> sessionId

// ---------------------------------------------------------------------------
// Storage helpers — thin Promise wrappers around the callback-based API
// ---------------------------------------------------------------------------
function storageGet(keys) {
  return new Promise((resolve) => chrome.storage.local.get(keys, resolve));
}
function storageSet(values) {
  return new Promise((resolve) => chrome.storage.local.set(values, resolve));
}

// ---------------------------------------------------------------------------
// DOM handles
// ---------------------------------------------------------------------------
const projectSelect   = document.getElementById("project-select");
const newProjectBtn   = document.getElementById("new-project-btn");
const deleteProjectBtn = document.getElementById("delete-project-btn");
const settingsBtn     = document.getElementById("settings-btn");
const settingsPanel   = document.getElementById("settings-panel");
const jiraKeyInput    = document.getElementById("jira-key-input");
const githubRepoInput = document.getElementById("github-repo-input");
const settingsSaveBtn = document.getElementById("settings-save-btn");
const syncBtn         = document.getElementById("sync-btn");
const actionsBtn      = document.getElementById("actions-btn");
const actionsBadge    = document.getElementById("actions-badge");
const actionsPanel    = document.getElementById("actions-panel");
const actionsList     = document.getElementById("actions-list");
const transcript      = document.getElementById("transcript");
const transcriptBtn   = document.getElementById("transcript-btn");
const transcriptModal = document.getElementById("transcript-modal");
const transcriptSource = document.getElementById("transcript-source");
const transcriptText = document.getElementById("transcript-text");
const transcriptCancel = document.getElementById("transcript-cancel");
const transcriptSubmit = document.getElementById("transcript-submit");
const statusEl        = document.getElementById("status");
const inputEl         = document.getElementById("input");
const sendBtn         = document.getElementById("send-btn");
const usePageBtn      = document.getElementById("use-page-btn");

function setStatus(text) {
  statusEl.textContent = text;
}

function appendBubble(role, text) {
  const div = document.createElement("div");
  div.className = `bubble ${role}`;
  div.textContent = text;
  transcript.appendChild(div);
  transcript.scrollTop = transcript.scrollHeight;
}

function appendCitations(citations) {
  const details = document.createElement("details");
  details.className = "sources-panel";

  const summary = document.createElement("summary");
  summary.textContent = `Sources (${citations.length})`;
  details.appendChild(summary);

  const ul = document.createElement("ul");
  ul.className = "sources-list";
  for (const c of citations) {
    const li = document.createElement("li");
    li.textContent = `[${c.ref}] ${c.source} · chunk ${c.chunk_index}`;
    ul.appendChild(li);
  }
  details.appendChild(ul);
  transcript.appendChild(details);
  transcript.scrollTop = transcript.scrollHeight;
}

function clearTranscript() {
  transcript.innerHTML = "";
}

function setBusy(busy) {
  sendBtn.disabled          = busy;
  usePageBtn.disabled       = busy;
  transcriptBtn.disabled    = busy;
  inputEl.disabled          = busy;
  projectSelect.disabled    = busy;
  newProjectBtn.disabled    = busy;
  deleteProjectBtn.disabled = busy || !currentProjectId;
  settingsBtn.disabled      = busy || !currentProjectId;
  settingsSaveBtn.disabled  = busy;
  syncBtn.disabled          = busy || !currentProjectId;
  actionsBtn.disabled       = busy || !currentProjectId;
}

// ---------------------------------------------------------------------------
// Projects — fetch the list from the agent and reflect it in the dropdown
// ---------------------------------------------------------------------------
async function loadProjects() {
  const response = await fetch(`${AGENT_URL}/projects`);
  if (!response.ok) throw new Error(`GET /projects returned ${response.status}`);
  projects = await response.json();
  renderProjectSelect();
}

function renderProjectSelect() {
  projectSelect.innerHTML = "";
  if (projects.length === 0) {
    const opt = document.createElement("option");
    opt.textContent = "(no projects — click +)";
    opt.disabled = true;
    opt.selected = true;
    projectSelect.appendChild(opt);
    deleteProjectBtn.disabled = true;
    return;
  }
  for (const p of projects) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.name;
    if (p.id === currentProjectId) opt.selected = true;
    projectSelect.appendChild(opt);
  }
  deleteProjectBtn.disabled = !currentProjectId;
}

async function createProject() {
  const name = prompt("Project name:");
  if (!name || !name.trim()) return;
  setBusy(true);
  setStatus("Creating project…");
  try {
    const response = await fetch(`${AGENT_URL}/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    });
    if (!response.ok) throw new Error(`Server returned ${response.status}`);
    const project = await response.json();
    projects.unshift(project);  // newest first
    await selectProject(project.id);
    setStatus(`✓ Created project "${project.name}"`);
  } catch (err) {
    setStatus(`⚠ Create failed: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

async function deleteCurrentProject() {
  if (!currentProjectId) return;
  const current = projects.find((p) => p.id === currentProjectId);
  if (!current) return;
  const ok = confirm(
    `Delete project "${current.name}" and all of its memory?\n` +
    `This wipes its conversation history and ingested documents.`
  );
  if (!ok) return;

  setBusy(true);
  setStatus("Deleting…");
  try {
    const response = await fetch(`${AGENT_URL}/projects/${currentProjectId}`, {
      method: "DELETE",
    });
    if (!response.ok) throw new Error(`Server returned ${response.status}`);

    projects = projects.filter((p) => p.id !== currentProjectId);
    delete sessionByProject[currentProjectId];
    await storageSet({ sessionByProject });

    // Fall back to the next project, or nothing.
    const next = projects[0]?.id ?? null;
    await selectProject(next);
    setStatus(`✓ Deleted "${current.name}"`);
  } catch (err) {
    setStatus(`⚠ Delete failed: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

async function selectProject(projectId) {
  currentProjectId = projectId;
  await storageSet({ currentProjectId });

  // Create a session id for this project on first switch.
  if (projectId && !sessionByProject[projectId]) {
    sessionByProject[projectId] = crypto.randomUUID();
    await storageSet({ sessionByProject });
  }

  // Close both panels when switching projects.
  settingsPanel.classList.remove("visible");
  actionsPanel.classList.remove("visible");

  renderProjectSelect();
  clearTranscript();
  deleteProjectBtn.disabled = !projectId;
  settingsBtn.disabled      = !projectId;
  syncBtn.disabled          = !projectId;
  actionsBtn.disabled       = !projectId;
  transcriptBtn.disabled    = !projectId;
  if (projectId) {
    setStatus("");
    loadPendingActions(projectId);
  } else {
    setStatus("Create a project to get started.");
    updateActionsBadge(0);
  }
}

// ---------------------------------------------------------------------------
// Chat — send a message to the agent and display the reply
// ---------------------------------------------------------------------------
async function sendMessage() {
  const message = inputEl.value.trim();
  if (!message) return;
  if (!currentProjectId) {
    setStatus("⚠ Create or select a project first.");
    return;
  }

  // Handle /briefing slash command (Step 11)
  if (message === "/briefing") {
    inputEl.value = "";
    setBusy(true);
    setStatus("Loading briefing…");
    try {
      const response = await fetch(`${AGENT_URL}/projects/${currentProjectId}/briefing`);
      if (!response.ok) throw new Error(`Server returned ${response.status}`);
      const b = await response.json();
      appendBubble("assistant", `📋 **Project Briefing**\n\n${b.summary}`);
      if (b.open_actions.length > 0) {
        const actionsList = b.open_actions.map(a => `• ${a.text}${a.owner ? ` (@${a.owner})` : ''}${a.due_date ? ` due ${a.due_date}` : ''}`).join('\n');
        appendBubble("assistant", `**Open Actions:**\n${actionsList}`);
      }
      if (b.recent_decisions.length > 0) {
        const decisionsList = b.recent_decisions.map(d => `• ${d.text}`).join('\n');
        appendBubble("assistant", `**Recent Decisions:**\n${decisionsList}`);
      }
      if (b.active_risks.length > 0) {
        const risksList = b.active_risks.map(r => `• ${r.text}`).join('\n');
        appendBubble("assistant", `**Active Risks:**\n${risksList}`);
      }
      setStatus("");
    } catch (err) {
      appendBubble("assistant", `⚠ Briefing failed: ${err.message}`);
      setStatus("Make sure the chat-agent server is running on port 8080.");
    } finally {
      setBusy(false);
      inputEl.focus();
    }
    return;
  }

  const sessionId = sessionByProject[currentProjectId];

  inputEl.value = "";
  appendBubble("user", message);
  setBusy(true);
  setStatus("Thinking…");

  try {
    const response = await fetch(`${AGENT_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: currentProjectId,
        session_id: sessionId,
        message,
      }),
    });

    if (!response.ok) {
      throw new Error(`Server returned ${response.status}`);
    }

    const data = await response.json();
    appendBubble("assistant", data.reply);
    if (data.citations && data.citations.length > 0) {
      appendCitations(data.citations);
    }
    setStatus("");
    // Refresh the pending-actions badge — the LLM may have drafted a new action.
    loadPendingActions(currentProjectId);
  } catch (err) {
    appendBubble("assistant", `⚠ Error: ${err.message}`);
    setStatus("Make sure the chat-agent server is running on port 8080.");
  } finally {
    setBusy(false);
    inputEl.focus();
  }
}

// ---------------------------------------------------------------------------
// Settings — attach / update external_refs (Jira key, GitHub repo) for the
// current project.  The panel toggles open/closed via the ⚙ button.
// ---------------------------------------------------------------------------
function populateSettings(projectId) {
  const project = projects.find((p) => p.id === projectId);
  const refs = (project && project.external_refs) || {};
  jiraKeyInput.value    = refs.jira_project_key || "";
  githubRepoInput.value = refs.github_repo      || "";
}

function toggleSettings() {
  settingsPanel.classList.toggle("visible");
  if (settingsPanel.classList.contains("visible") && currentProjectId) {
    populateSettings(currentProjectId);
  }
}

async function saveProjectSettings() {
  if (!currentProjectId) return;

  const external_refs = {};
  const jiraKey = jiraKeyInput.value.trim();
  const githubRepo = githubRepoInput.value.trim();
  if (jiraKey)    external_refs.jira_project_key = jiraKey;
  if (githubRepo) external_refs.github_repo      = githubRepo;

  setBusy(true);
  setStatus("Saving settings…");
  try {
    const response = await fetch(`${AGENT_URL}/projects/${currentProjectId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ external_refs }),
    });
    if (!response.ok) throw new Error(`Server returned ${response.status}`);
    const updated = await response.json();
    // Reflect the saved refs back into our local list.
    const idx = projects.findIndex((p) => p.id === currentProjectId);
    if (idx !== -1) projects[idx] = updated;
    setStatus("✓ Settings saved");
    settingsPanel.classList.remove("visible");
  } catch (err) {
    setStatus(`⚠ Save failed: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

// ---------------------------------------------------------------------------
// Sync — pull items from Jira / GitHub into the current project's RAG store
// ---------------------------------------------------------------------------
async function syncCurrentProject() {
  if (!currentProjectId) {
    setStatus("⚠ Create or select a project first.");
    return;
  }

  setBusy(true);
  setStatus("Syncing PM integrations…");

  try {
    const response = await fetch(
      `${AGENT_URL}/projects/${currentProjectId}/sync`,
      { method: "POST" }
    );

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `Server returned ${response.status}`);
    }

    const data = await response.json();
    if (data.synced_items === 0) {
      setStatus("✓ Sync complete — no new items since last sync");
    } else {
      setStatus(
        `✓ Sync complete — ${data.synced_items} item(s), ${data.chunks_stored} chunks ingested`
      );
    }
  } catch (err) {
    setStatus(`⚠ Sync failed: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

// ---------------------------------------------------------------------------
// Pending actions — approve / reject agent-drafted PM writes
// ---------------------------------------------------------------------------

function updateActionsBadge(count) {
  if (count > 0) {
    actionsBadge.textContent = count;
    actionsBadge.classList.remove("hidden");
  } else {
    actionsBadge.classList.add("hidden");
  }
}

async function loadPendingActions(projectId) {
  if (!projectId) return;
  try {
    const resp = await fetch(
      `${AGENT_URL}/projects/${projectId}/actions`
    );
    if (!resp.ok) return;
    const actions = await resp.json();
    const actionable = actions.filter(a => a.status === 'pending' || a.status === 'failed');
    updateActionsBadge(actionable.length);
    renderActionsList(actionable);
  } catch (_) {
    // Silently ignore if the server is unreachable — badge stays at 0.
  }
}

function renderActionsList(actions) {
  actionsList.innerHTML = "";
  if (actions.length === 0) {
    const empty = document.createElement("div");
    empty.style.cssText = "font-size:12px;color:#888;padding:4px 0;";
    empty.textContent = "No pending actions.";
    actionsList.appendChild(empty);
    return;
  }
  for (const action of actions) {
    const row = document.createElement("div");
    row.className = "action-row";

    const meta = document.createElement("div");
    meta.className = "action-meta";
    meta.textContent = `${action.action_type}  ·  ${action.payload.item_id}  [${action.status}]`;

    const body = document.createElement("div");
    body.className = "action-body";
    const bodyText = action.payload.body || "";
    body.textContent = bodyText.length > 120 ? bodyText.slice(0, 120) + "…" : bodyText;

    if (action.status === 'failed' && action.payload.error) {
      const errEl = document.createElement("div");
      errEl.style.cssText = "font-size:11px;color:#f85149;margin-bottom:4px;font-family:monospace;";
      errEl.textContent = action.payload.error;
      row.appendChild(errEl);
    }

    const btns = document.createElement("div");
    btns.className = "action-btns";

    if (action.status === 'pending') {
      const approveBtn = document.createElement("button");
      approveBtn.className = "approve-btn";
      approveBtn.textContent = "✓ Approve";
      approveBtn.addEventListener("click", () => approveAction(action.id));

      const rejectBtn = document.createElement("button");
      rejectBtn.className = "reject-btn";
      rejectBtn.textContent = "✕ Reject";
      rejectBtn.addEventListener("click", () => rejectAction(action.id));

      btns.appendChild(approveBtn);
      btns.appendChild(rejectBtn);
    } else if (action.status === 'failed') {
      const retryBtn = document.createElement("button");
      retryBtn.className = "approve-btn";
      retryBtn.textContent = "↻ Retry";
      retryBtn.addEventListener("click", () => retryAction(action.id));
      btns.appendChild(retryBtn);
    }
    row.appendChild(meta);
    row.appendChild(body);
    row.appendChild(btns);
    actionsList.appendChild(row);
  }
}

function toggleActionsPanel() {
  actionsPanel.classList.toggle("visible");
  if (actionsPanel.classList.contains("visible") && currentProjectId) {
    loadPendingActions(currentProjectId);
  }
}

async function approveAction(actionId) {
  setBusy(true);
  setStatus("Approving action…");
  try {
    const resp = await fetch(`${AGENT_URL}/actions/${actionId}/approve`, {
      method: "POST",
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `Server returned ${resp.status}`);
    }
    const data = await resp.json();
    const url = data.result?.url;
    if (url) {
      setStatus(`✓ Comment posted — ${url}`);
      // Open the URL in a new tab on click.
      const statusEl = document.getElementById("status");
      statusEl.style.cursor = "pointer";
      statusEl.onclick = () => { chrome.tabs.create({ url }); statusEl.onclick = null; statusEl.style.cursor = ""; };
    } else {
      setStatus("✓ Action executed");
    }
    await loadPendingActions(currentProjectId);
  } catch (err) {
    setStatus(`⚠ Approve failed: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

async function rejectAction(actionId) {
  setBusy(true);
  setStatus("Rejecting action…");
  try {
    const resp = await fetch(`${AGENT_URL}/actions/${actionId}/reject`, {
      method: "POST",
    });
    if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
    setStatus("✕ Action rejected — nothing was written.");
    await loadPendingActions(currentProjectId);
  } catch (err) {
    setStatus(`⚠ Reject failed: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

async function retryAction(actionId) {
  setBusy(true);
  setStatus("Resetting action…");
  try {
    const resp = await fetch(`${AGENT_URL}/actions/${actionId}/retry`, {
      method: "POST",
    });
    if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
    setStatus("↻ Action reset to pending — approve to retry.");
    await loadPendingActions(currentProjectId);
  } catch (err) {
    setStatus(`⚠ Retry failed: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

// ---------------------------------------------------------------------------
// Use current page — extract the tab's text, ingest into the current project
// ---------------------------------------------------------------------------
async function useCurrentPage() {
  if (!currentProjectId) {
    setStatus("⚠ Create or select a project first.");
    return;
  }

  setBusy(true);
  setStatus("Reading page…");

  let pageData;
  try {
    pageData = await new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type: "GET_PAGE_TEXT" }, (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
        } else if (response && response.error) {
          reject(new Error(response.error));
        } else {
          resolve(response);
        }
      });
    });
  } catch (err) {
    setStatus(`⚠ Could not read page: ${err.message}`);
    setBusy(false);
    return;
  }

  setStatus(`Ingesting "${pageData.title}"…`);

  try {
    const response = await fetch(`${AGENT_URL}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: currentProjectId,
        source: pageData.url,
        text: pageData.text,
      }),
    });

    if (!response.ok) {
      throw new Error(`Server returned ${response.status}`);
    }

    const data = await response.json();
    setStatus(`✓ Page ready (${data.chunks} chunks) — ask me anything about it`);
  } catch (err) {
    setStatus(`⚠ Ingest failed: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------
sendBtn.addEventListener("click", sendMessage);

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

usePageBtn.addEventListener("click", useCurrentPage);
newProjectBtn.addEventListener("click", createProject);
deleteProjectBtn.addEventListener("click", deleteCurrentProject);
settingsBtn.addEventListener("click", toggleSettings);
settingsSaveBtn.addEventListener("click", saveProjectSettings);
syncBtn.addEventListener("click", syncCurrentProject);
actionsBtn.addEventListener("click", toggleActionsPanel);

projectSelect.addEventListener("change", (e) => {
  selectProject(e.target.value);
});

// ---------------------------------------------------------------------------
// Transcript modal (Step 10)
// ---------------------------------------------------------------------------
function toggleTranscriptModal() {
  transcriptModal.classList.toggle("visible");
  if (transcriptModal.classList.contains("visible")) {
    transcriptSource.value = "";
    transcriptText.value = "";
    transcriptSource.focus();
  }
}

async function handleTranscriptSubmit() {
  const source = transcriptSource.value.trim();
  const text = transcriptText.value.trim();
  if (!source || !text) {
    setStatus("Please fill in both source and text.");
    return;
  }
  if (!currentProjectId) {
    setStatus("Select a project first.");
    return;
  }

  setBusy(true);
  setStatus("Processing transcript…");
  toggleTranscriptModal();

  try {
    const response = await fetch(`${AGENT_URL}/ingest/transcript`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: currentProjectId,
        source,
        text,
      }),
    });

    if (!response.ok) {
      throw new Error(`Server returned ${response.status}`);
    }

    const data = await response.json();
    setStatus(`✓ Processed — ${data.decisions} decisions, ${data.action_items} actions, ${data.risks} risks`);
  } catch (err) {
    setStatus(`⚠ Process failed: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

transcriptBtn.addEventListener("click", toggleTranscriptModal);
transcriptCancel.addEventListener("click", toggleTranscriptModal);
transcriptSubmit.addEventListener("click", handleTranscriptSubmit);

// ---------------------------------------------------------------------------
// Initialise — run once when the side panel loads
// ---------------------------------------------------------------------------
(async () => {
  try {
    const stored = await storageGet(["currentProjectId", "sessionByProject"]);
    currentProjectId = stored.currentProjectId ?? null;
    sessionByProject = stored.sessionByProject ?? {};

    await loadProjects();

    // If the stored project no longer exists on the server, clear it.
    if (currentProjectId && !projects.find((p) => p.id === currentProjectId)) {
      currentProjectId = null;
    }
    // If no project is selected but some exist, pick the first one.
    if (!currentProjectId && projects.length > 0) {
      await selectProject(projects[0].id);
    } else {
      await selectProject(currentProjectId);
    }

    inputEl.focus();
  } catch (err) {
    setStatus(`⚠ Cannot reach agent at ${AGENT_URL}. Is the server running?`);
  }
})();