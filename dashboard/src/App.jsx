import { useState, useEffect, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import * as api from './api.js';
import {
  IconX, IconSettings, IconRefresh, IconUpload, IconLoader,
  IconSearch, IconBook, IconUser, IconBot, IconClipboard, IconSun, IconEdit, IconCheck, IconAlert,
} from './icons.jsx';

// ─── Toast ────────────────────────────────────────────────────────────────

function Toast({ message, url, onDismiss }) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 6000);
    return () => clearTimeout(t);
  }, [onDismiss]);

  return (
    <div className={`toast ${url ? 'success' : ''}`} data-testid="toast">
      <span className="toast-msg">{message}</span>
      {url && (
        <a href={url} target="_blank" rel="noreferrer" className="toast-link">
          open ↗
        </a>
      )}
      <button className="toast-close" onClick={onDismiss} aria-label="Dismiss notification">
        <IconX />
      </button>
    </div>
  );
}

// ─── Integration edit modal ────────────────────────────────────────────────

function IntegrationModal({ project, onSave, onClose }) {
  const [refs, setRefs] = useState({
    jira_project_key: project?.external_refs?.jira_project_key || '',
    github_repo: project?.external_refs?.github_repo || '',
  });

  async function handleSave() {
    const patch = {};
    if (refs.jira_project_key || refs.github_repo) {
      patch.external_refs = {};
      if (refs.jira_project_key) patch.external_refs.jira_project_key = refs.jira_project_key.trim();
      if (refs.github_repo) patch.external_refs.github_repo = refs.github_repo.trim();
    }
    await onSave(project.id, patch);
    onClose();
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <span className="modal-title">
            <IconSettings /> Integrations — {project?.name}
          </span>
          <button className="modal-close" onClick={onClose} aria-label="Close dialog">
            <IconX />
          </button>
        </div>
        <div className="modal-body">
          <div className="edit-label">External refs</div>
          <label className="field-label">Jira project key</label>
          <input
            className="input"
            value={refs.jira_project_key}
            onChange={e => setRefs(r => ({ ...r, jira_project_key: e.target.value }))}
            placeholder="e.g. KAN"
          />
          <label className="field-label">GitHub repo</label>
          <input
            className="input"
            value={refs.github_repo}
            onChange={e => setRefs(r => ({ ...r, github_repo: e.target.value }))}
            placeholder="e.g. org/repo"
          />
          <div className="row-gap-sm mt-12">
            <button className="btn btn-primary btn-sm" onClick={handleSave}>Save</button>
            <button className="btn btn-secondary btn-sm" onClick={onClose}>Cancel</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── TopBar ────────────────────────────────────────────────────────────────

function TopBar({ projects, activeId, onSelect, onRefresh, setToast, onEditIntegrations }) {
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [showNewInput, setShowNewInput] = useState(false);

  function handleChange(e) {
    const val = e.target.value;
    if (val === '__new__') {
      setShowNewInput(true);
    } else if (val === '__delete__') {
      handleDelete();
    } else if (val === '__edit__') {
      onEditIntegrations();
    } else {
      onSelect(val);
    }
  }

  async function handleDelete() {
    const project = projects.find(p => p.id === activeId);
    if (!project) return;
    if (!window.confirm(`Delete project "${project.name}"? This removes all its messages and memory.`)) return;
    try {
      await api.deleteProject(activeId);
      await onRefresh();
    } catch (err) {
      setToast({ message: `Delete failed: ${err.message}` });
    }
  }

  async function handleCreate(e) {
    e.preventDefault();
    if (!newName.trim()) return;
    setCreating(true);
    try {
      await api.createProject(newName.trim());
      setNewName('');
      setShowNewInput(false);
      await onRefresh();
    } catch (err) {
      setToast({ message: `Create failed: ${err.message}` });
    } finally {
      setCreating(false);
    }
  }

  return (
    <header className="topbar">
      <span className="topbar-logo" aria-hidden="true"><IconBot /></span>
      <span className="topbar-title">Research Agent</span>

      <select
        className="project-select"
        data-testid="project-select"
        value={activeId || ''}
        onChange={handleChange}
      >
        {projects.length === 0 && (
          <option value="" disabled>No projects</option>
        )}
        {projects.map(p => (
          <option key={p.id} value={p.id}>{p.name}</option>
        ))}
        <option value="" disabled>──────</option>
        <option value="__new__">+ New project</option>
        {activeId && <option value="__edit__">Edit integrations</option>}
        {activeId && <option value="__delete__">Delete project</option>}
      </select>

      {showNewInput && (
        <form onSubmit={handleCreate} className="row-gap-sm">
          <input
            className="input input-sm"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            placeholder="Project name…"
            autoFocus
            data-testid="new-project-name"
          />
          <button type="submit" className="btn btn-primary btn-sm" disabled={creating || !newName.trim()} data-testid="create-project">
            Create
          </button>
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => { setShowNewInput(false); setNewName(''); }}>
            Cancel
          </button>
        </form>
      )}
    </header>
  );
}

// ─── Left pane ─────────────────────────────────────────────────────────────

// Single drop zone — auto-routes by file extension / URL host.
const ALL_ACCEPT = '.txt,.md,.pdf,.docx,.mp3,.wav,.m4a';
const AUDIO_EXTS = ['.mp3', '.wav', '.m4a'];
const YT_HOST_RE = /(^|\.)((m|www)\.)?(youtube\.com|youtu\.be)$/i;

function kindForFile(file) {
  const name = (file.name || '').toLowerCase();
  return AUDIO_EXTS.some(ext => name.endsWith(ext)) ? 'transcript' : 'document';
}

function kindForUrl(url) {
  try {
    const host = new URL(url).hostname;
    return YT_HOST_RE.test(host) ? 'transcript' : 'document';
  } catch {
    return 'document';
  }
}

function DropZone({ busy, url, setUrl, onFile, onUrl }) {
  const [hover, setHover] = useState(false);
  const inputRef = useRef(null);

  function handleDrop(e) {
    e.preventDefault();
    setHover(false);
    if (busy) return;
    const file = e.dataTransfer.files?.[0];
    if (file) onFile(file);
  }

  function handlePick(e) {
    const file = e.target.files?.[0];
    if (file) onFile(file);
    e.target.value = '';
  }

  return (
    <div className="section">
      <div className="section-title"><IconUpload /> Add sources</div>

      <div
        className={`dropzone ${hover ? 'hover' : ''} ${busy ? 'busy' : ''}`}
        onClick={() => !busy && inputRef.current?.click()}
        onDragOver={e => { e.preventDefault(); if (!busy) setHover(true); }}
        onDragLeave={() => setHover(false)}
        onDrop={handleDrop}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ALL_ACCEPT}
          onChange={handlePick}
          className="visually-hidden-input"
        />
        <div className="dropzone-icon">{busy ? <IconLoader className="spin" /> : <IconUpload />}</div>
        <div className="dropzone-text">
          {busy ? 'Processing…' : 'Click or drop file here'}
        </div>
        <div className="dropzone-hint">
          Documents: .txt .md .pdf .docx · Meetings: .mp3 .wav .m4a · URLs: articles, Wikipedia, YouTube
        </div>
      </div>

      <form
        onSubmit={e => { e.preventDefault(); onUrl(); }}
        className="row-gap-sm mt-8"
      >
        <input
          className="input flex-1"
          value={url}
          onChange={e => setUrl(e.target.value)}
          placeholder="…or paste URL"
          disabled={busy}
          data-testid="url-input"
        />
        <button
          type="submit"
          className="btn btn-secondary btn-sm"
          disabled={busy || !url.trim()}
          data-testid="url-add"
        >
          Add
        </button>
      </form>
    </div>
  );
}

function LeftPane({ projectId, sourcesKey, onSourcesChange, setToast }) {
  const [syncing, setSyncing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState('');
  const [searchQ, setSearchQ] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [sources, setSources] = useState([]);

  useEffect(() => {
    if (!projectId) { setSources([]); return; }
  }, [projectId]);

  useEffect(() => {
    if (!projectId) { setSources([]); return; }
    api.listSources(projectId).then(setSources).catch(() => setSources([]));
  }, [projectId, sourcesKey]);

  useEffect(() => {
    setSearchResults([]);
    setSearchQ('');
  }, [projectId]);

  async function handleSync() {
    setSyncing(true);
    try {
      await api.syncProject(projectId);
      setToast({ message: 'Sync complete.' });
      onSourcesChange();
    } catch (err) {
      setToast({ message: `Sync failed: ${err.message}` });
    } finally {
      setSyncing(false);
    }
  }

  async function handleFile(file) {
    if (!file || busy) return;
    const kind = kindForFile(file);
    setBusy(true);
    try {
      const res = await api.ingestFile(projectId, file, kind);
      const extras = kind === 'transcript'
        ? ` · ${res.decisions} decisions, ${res.action_items} actions, ${res.risks} risks`
        : '';
      setToast({ message: `Ingested ${res.chunks} chunks from "${file.name}"${extras}.` });
      onSourcesChange();
    } catch (err) {
      setToast({ message: `Upload failed: ${err.message}` });
    } finally {
      setBusy(false);
    }
  }

  async function handleUrl() {
    const trimmed = url.trim();
    if (!trimmed || busy) return;
    const kind = kindForUrl(trimmed);
    setBusy(true);
    try {
      const res = await api.ingestUrl(projectId, trimmed, kind);
      const extras = kind === 'transcript'
        ? ` · ${res.decisions} decisions, ${res.action_items} actions, ${res.risks} risks`
        : '';
      setToast({ message: `Ingested ${res.chunks} chunks from URL${extras}.` });
      setUrl('');
      onSourcesChange();
    } catch (err) {
      setToast({ message: `URL ingest failed: ${err.message}` });
    } finally {
      setBusy(false);
    }
  }

  async function handleSearch(e) {
    e.preventDefault();
    if (!searchQ.trim()) return;
    setSearching(true);
    try {
      const res = await api.memorySearch(projectId, searchQ.trim(), 50);
      setSearchResults(res.results || []);
    } catch (err) {
      setToast({ message: `Search failed: ${err.message}` });
    } finally {
      setSearching(false);
    }
  }

  if (!projectId) return <aside className="left-pane" />;

  return (
    <aside className="left-pane">
      <div className="left-scroll">

        <div className="section">
          <button className="btn btn-secondary btn-block" onClick={handleSync} disabled={syncing}>
            {syncing ? <><IconLoader className="spin" /> Syncing…</> : <><IconRefresh /> Sync now</>}
          </button>
        </div>

        <DropZone
          busy={busy}
          url={url}
          setUrl={setUrl}
          onFile={handleFile}
          onUrl={handleUrl}
        />

        <div className="section">
          <div className="section-title"><IconSearch /> Memory Search</div>
          <form onSubmit={handleSearch} className="col-gap-sm">
            <input
              className="input"
              value={searchQ}
              onChange={e => setSearchQ(e.target.value)}
              placeholder="Search query…"
            />
            <button
              type="submit"
              className="btn btn-secondary btn-block"
              disabled={searching || !searchQ.trim()}
            >
              {searching ? 'Searching…' : 'Search'}
            </button>
          </form>

          {searchResults.length > 0 && (
            <div className="search-results">
              {searchResults.map((hit, i) => (
                <div key={i} className="search-hit">
                  <div className="search-hit-meta">{hit.source} · {hit.score?.toFixed(3)}</div>
                  <div className="search-hit-text">{hit.text}</div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="section">
          <div className="section-title" data-testid="sources-count"><IconBook /> Sources ({sources.length})</div>
          {sources.length === 0 ? (
            <p className="no-data">No sources ingested yet.</p>
          ) : (
            sources.map(s => (
              <div key={s.source} className="source-item" data-testid="source-item">
                <span className="source-name" title={s.source}>{s.source}</span>
                <span className="source-chunks">{s.chunks} chunks</span>
              </div>
            ))
          )}
        </div>

      </div>
    </aside>
  );
}

// ─── Chat ─────────────────────────────────────────────────────────────────

function ChatPane({ projectId, onActionDrafted }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => { setMessages([]); }, [projectId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  async function send() {
    const text = input.trim();
    if (!text || busy || !projectId) return;
    setInput('');
    setBusy(true);
    setMessages(prev => [...prev, { role: 'user', content: text }]);
    try {
      const res = await api.chat(projectId, 'default', text);
      setMessages(prev => [...prev, { role: 'assistant', content: res.reply, citations: res.citations ?? [] }]);
      if (res.reply?.includes('Drafted action')) onActionDrafted();
    } catch (err) {
      setMessages(prev => [...prev, { role: 'assistant', content: `⚠ Error: ${err.message}` }]);
    } finally {
      setBusy(false);
    }
  }

  function handleKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  }

  if (!projectId) {
    return (
      <div className="chat-pane chat-empty-state">
        <div className="chat-empty-icon"><IconBot /></div>
        <div className="chat-empty-text">Select or create a project to start chatting.</div>
      </div>
    );
  }

  return (
    <div className="chat-pane">
      <div className="chat-messages">
        {messages.map((m, i) => (
          <div key={i} className={`message ${m.role === 'user' ? 'user' : ''}`}>
            <div className={`avatar ${m.role === 'user' ? 'user' : 'ai'}`}>
              {m.role === 'user' ? <IconUser /> : <IconBot />}
            </div>
            <div className={`bubble ${m.role === 'user' ? 'user' : 'ai'}`} data-testid={m.role === 'user' ? 'chat-message-user' : 'chat-message'}>
              <div className="prose">
                <ReactMarkdown>{m.content}</ReactMarkdown>
              </div>
              {m.citations?.length > 0 && (
                <details className="sources-panel" data-testid="citations">
                  <summary className="sources-summary">Sources ({m.citations.length})</summary>
                  <ul className="sources-list">
                    {m.citations.map(c => (
                      <li key={c.ref} className="sources-item" data-testid="citation-item">
                        [{c.ref}] {c.source} · chunk {c.chunk_index}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          </div>
        ))}

        {busy && (
          <div className="message">
            <div className="avatar ai"><IconBot /></div>
            <div className="typing-indicator">
              <div className="dot" />
              <div className="dot" />
              <div className="dot" />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="chat-input-area">
        <textarea
          className="chat-textarea"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Ask anything… (Enter to send, Shift+Enter for newline)"
          rows={1}
          disabled={busy}
          data-testid="chat-input"
        />
        <button
          className={`btn send-btn ${busy || !input.trim() ? 'btn-secondary' : 'btn-primary'}`}
          onClick={send}
          disabled={busy || !input.trim()}
          data-testid="chat-send"
        >
          {busy ? '…' : 'Send →'}
        </button>
      </div>
    </div>
  );
}

// ─── Studio pane ───────────────────────────────────────────────────────────

const STUDIO_TABS = [
  { label: 'Briefing', Icon: IconClipboard },
  { label: 'Standup', Icon: IconSun },
  { label: 'Actions', Icon: IconEdit },
  { label: 'Decisions', Icon: IconCheck },
  { label: 'Risks', Icon: IconAlert },
];

function StudioPane({ projectId, actionsKey, setToast }) {
  const [tab, setTab] = useState(() => {
    const saved = localStorage.getItem('studioTab');
    return saved !== null ? +saved : 0;
  });
  const [briefing, setBriefing] = useState(null);
  const [briefingLoading, setBriefingLoading] = useState(false);
  const [standup, setStandup] = useState(null);
  const [standupLoading, setStandupLoading] = useState(false);
  const [actions, setActions] = useState([]);
  const [decisions, setDecisions] = useState([]);
  const [risks, setRisks] = useState([]);

  function selectTab(i) {
    setTab(i);
    localStorage.setItem('studioTab', String(i));
  }

  useEffect(() => {
    if (!projectId) { setActions([]); setBriefing(null); setStandup(null); return; }
    api.listActions(projectId, 'pending').then(setActions).catch(() => setActions([]));
  }, [projectId, actionsKey]);

  useEffect(() => {
    if (!projectId) { setDecisions([]); setActions([]); setRisks([]); return; }
    api.listDecisions(projectId).then(setDecisions).catch(() => setDecisions([]));
    api.listRisks(projectId).then(setRisks).catch(() => setRisks([]));
  }, [projectId]);

  async function loadBriefing() {
    if (!projectId || briefingLoading) return;
    setBriefingLoading(true);
    try {
      const b = await api.getBriefing(projectId);
      setBriefing(b);
    } catch (err) {
      setToast({ message: `Briefing failed: ${err.message}` });
    } finally {
      setBriefingLoading(false);
    }
  }

  async function loadStandup() {
    if (!projectId || standupLoading) return;
    setStandupLoading(true);
    try {
      const s = await api.getStandup(projectId);
      setStandup(s);
    } catch (err) {
      setToast({ message: `Standup failed: ${err.message}` });
    } finally {
      setStandupLoading(false);
    }
  }

  async function handleApprove(actionId) {
    try {
      const res = await api.approveAction(actionId);
      const url = res?.result?.url;
      setToast({ message: 'Comment posted.', url });
      setActions(prev => prev.filter(a => a.id !== actionId));
    } catch (err) {
      setToast({ message: `Approve failed: ${err.message}` });
    }
  }

  async function handleReject(actionId) {
    try {
      await api.rejectAction(actionId);
      setActions(prev => prev.filter(a => a.id !== actionId));
    } catch (err) {
      setToast({ message: `Reject failed: ${err.message}` });
    }
  }

  if (!projectId) return <aside className="studio-pane" />;

  return (
    <aside className="studio-pane">
      <div className="studio-tabs">
        {STUDIO_TABS.map((tabInfo, i) => {
          const { label, Icon } = tabInfo;
          return (
          <button
            key={label}
            className={`studio-tab ${tab === i ? 'active' : ''}`}
            data-testid={i === 2 ? 'studio-tab-actions' : undefined}
            onClick={() => {
              selectTab(i);
              if (i === 0 && !briefing) loadBriefing();
              if (i === 1 && !standup) loadStandup();
            }}
          >
            <Icon /> {label}
            {i === 2 && actions.length > 0 && (
              <span className="section-badge ml-4">{actions.length}</span>
            )}
          </button>
          );
        })}
      </div>

      <div className="studio-scroll">

        {tab === 0 && (
          <div>
            {!briefing && !briefingLoading && (
              <button className="btn btn-secondary btn-block" onClick={loadBriefing}>
                Generate Briefing
              </button>
            )}
            {briefingLoading && <p className="no-data">Generating…</p>}
            {briefing && (
              <div>
                <div className="briefing-summary">{briefing.summary}</div>

                {briefing.open_actions.length > 0 && (
                  <details className="briefing-section" open>
                    <summary>Open Actions ({briefing.open_actions.length})</summary>
                    {briefing.open_actions.map(a => (
                      <div key={a.id} className="briefing-item">
                        <span className="briefing-item-text">{a.text}</span>
                        {a.owner && <span className="briefing-item-meta">@{a.owner}</span>}
                        {a.due_date && <span className="briefing-item-due">due {a.due_date}</span>}
                      </div>
                    ))}
                  </details>
                )}

                {briefing.recent_decisions.length > 0 && (
                  <details className="briefing-section">
                    <summary>Recent Decisions ({briefing.recent_decisions.length})</summary>
                    {briefing.recent_decisions.map(d => (
                      <div key={d.id} className="briefing-item">
                        <span className="briefing-item-text">{d.text}</span>
                        <span className="briefing-item-meta">{d.source}</span>
                      </div>
                    ))}
                  </details>
                )}

                {briefing.active_risks.length > 0 && (
                  <details className="briefing-section">
                    <summary>Active Risks ({briefing.active_risks.length})</summary>
                    {briefing.active_risks.map(r => (
                      <div key={r.id} className="briefing-item">
                        <span className="briefing-item-text">{r.text}</span>
                        <span className="briefing-item-meta">{r.source}</span>
                      </div>
                    ))}
                  </details>
                )}

                <div className="briefing-meta">Generated: {new Date(briefing.generated_at).toLocaleString()}</div>
                <button
                  className="btn btn-secondary btn-sm mt-12"
                  onClick={() => { setBriefing(null); loadBriefing(); }}
                >
                  Refresh
                </button>
              </div>
            )}
          </div>
        )}

        {tab === 1 && (
          <div>
            {!standup && !standupLoading && (
              <button className="btn btn-secondary btn-block" onClick={loadStandup}>
                Generate Standup
              </button>
            )}
            {standupLoading && <p className="no-data">Generating…</p>}
            {standup && (
              <div>
                <div className="briefing-summary">{standup.summary}</div>

                {standup.done.length > 0 && (
                  <details className="briefing-section" open>
                    <summary>Done ({standup.done.length})</summary>
                    {standup.done.map((item, i) => (
                      <div key={i} className="briefing-item">
                        <span className="briefing-item-text">{item}</span>
                      </div>
                    ))}
                  </details>
                )}

                {standup.today.length > 0 && (
                  <details className="briefing-section" open>
                    <summary>Today / Next ({standup.today.length})</summary>
                    {standup.today.map((item, i) => (
                      <div key={i} className="briefing-item">
                        <span className="briefing-item-text">{item}</span>
                      </div>
                    ))}
                  </details>
                )}

                {standup.blockers.length > 0 && (
                  <details className="briefing-section" open>
                    <summary>Blockers ({standup.blockers.length})</summary>
                    {standup.blockers.map((item, i) => (
                      <div key={i} className="briefing-item">
                        <span className="briefing-item-text">{item}</span>
                      </div>
                    ))}
                  </details>
                )}

                {standup.done.length === 0 && standup.today.length === 0 && standup.blockers.length === 0 && (
                  <p className="no-data">Nothing logged in the last 24h.</p>
                )}

                <div className="briefing-meta">Generated: {new Date(standup.generated_at).toLocaleString()}</div>
                <button
                  className="btn btn-secondary btn-sm mt-12"
                  onClick={() => { setStandup(null); loadStandup(); }}
                >
                  Refresh
                </button>
              </div>
            )}
          </div>
        )}

        {tab === 2 && (
          <div>
            {actions.length === 0 ? (
              <p className="no-actions">No pending actions.</p>
            ) : (
              actions.map(a => (
                <div key={a.id} className="action-card" data-testid="action-card">
                  <div className="action-type">{a.action_type}</div>
                  <div className="action-meta">{a.payload?.item_id || a.payload?.project_key || ''} · {a.payload?.ref_key}</div>
                  <div className="action-body">{a.payload?.body || a.payload?.summary || ''}{a.payload?.description ? ` — ${a.payload.description}` : ''}</div>
                  <div className="action-btns">
                    <button className="btn-approve" onClick={() => handleApprove(a.id)} data-testid="action-approve">Approve</button>
                    <button className="btn-reject" onClick={() => handleReject(a.id)} data-testid="action-reject">Reject</button>
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {tab === 3 && (
          <div className="transcript-results">
            {decisions.length === 0 ? (
              <p className="no-data">No decisions extracted yet.</p>
            ) : (
              decisions.map(d => (
                <div key={d.id} className="transcript-item">
                  <div className="transcript-meta">{d.source}</div>
                  <div className="transcript-text">{d.text}</div>
                </div>
              ))
            )}
          </div>
        )}

        {tab === 4 && (
          <div className="transcript-results">
            {risks.length === 0 ? (
              <p className="no-data">No risks extracted yet.</p>
            ) : (
              risks.map(r => (
                <div key={r.id} className="transcript-item">
                  <div className="transcript-meta">{r.source}</div>
                  <div className="transcript-text">{r.text}</div>
                </div>
              ))
            )}
          </div>
        )}

      </div>
    </aside>
  );
}

// ─── App ──────────────────────────────────────────────────────────────────

export default function App() {
  const [projects, setProjects] = useState([]);
  const [activeId, setActiveId] = useState(() => localStorage.getItem('projectId') || null);
  const [toast, setToast] = useState(null);
  const [actionsKey, setActionsKey] = useState(0);
  const [sourcesKey, setSourcesKey] = useState(0);
  const [editProject, setEditProject] = useState(null);

  const refreshProjects = useCallback(async () => {
    const list = await api.listProjects();
    setProjects(list);
    if (activeId && !list.find(p => p.id === activeId)) {
      setActiveId(null);
      localStorage.removeItem('projectId');
    }
  }, [activeId]);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { refreshProjects(); }, [refreshProjects]);

  function selectProject(id) {
    setActiveId(id);
    localStorage.setItem('projectId', id);
  }

  async function handleSaveIntegrations(id, patch) {
    try {
      await api.patchProject(id, patch);
      await refreshProjects();
    } catch (err) {
      setToast({ message: `Save failed: ${err.message}` });
    }
  }

  const activeProject = projects.find(p => p.id === activeId) || null;

  return (
    <div className="app">
      <TopBar
        projects={projects}
        activeId={activeId}
        onSelect={selectProject}
        onRefresh={refreshProjects}
        setToast={setToast}
        onEditIntegrations={() => setEditProject(activeProject)}
      />
      <LeftPane
        projectId={activeId}
        sourcesKey={sourcesKey}
        onSourcesChange={() => setSourcesKey(k => k + 1)}
        setToast={setToast}
      />
      <ChatPane
        projectId={activeId}
        onActionDrafted={() => setActionsKey(k => k + 1)}
      />
      <StudioPane
        projectId={activeId}
        actionsKey={actionsKey}
        setToast={setToast}
      />

      {toast && (
        <Toast
          message={toast.message}
          url={toast.url}
          onDismiss={() => setToast(null)}
        />
      )}

      {editProject && (
        <IntegrationModal
          project={editProject}
          onSave={handleSaveIntegrations}
          onClose={() => setEditProject(null)}
        />
      )}
    </div>
  );
}
