// sidepanel.js — UI logic for the Research Agent side panel.
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
let projects = [];        // [{id, name, created_at}, ...]
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

  renderProjectSelect();
  clearTranscript();
  deleteProjectBtn.disabled = !projectId;
  transcriptBtn.disabled    = !projectId;
  if (projectId) {
    setStatus("");
  } else {
    setStatus("Create a project to get started.");
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
  } catch (err) {
    appendBubble("assistant", `⚠ Error: ${err.message}`);
    setStatus("Make sure the chat-agent server is running on port 8080.");
  } finally {
    setBusy(false);
    inputEl.focus();
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