import { useState, useEffect, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import * as api from './api.js';
import {
  IconX, IconSettings, IconUpload, IconLoader,
  IconSearch, IconBook, IconBot, IconTrash,
} from './icons.jsx';
import SetupWizard, { STATUS_KEY as WIZARD_STATUS_KEY } from './wizard/SetupWizard.jsx';
import SettingsPage from './settings/SettingsPage.jsx';

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

// ─── TopBar ────────────────────────────────────────────────────────────────

function TopBar({ projects, activeId, onSelect, onRefresh, setToast, onboardingIncomplete, onOpenWizard, onOpenSettings }) {
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [showNewInput, setShowNewInput] = useState(false);

  function handleChange(e) {
    const val = e.target.value;
    if (val === '__new__') {
      setShowNewInput(true);
    } else if (val === '__delete__') {
      handleDelete();
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
        {activeId && <option value="__delete__">- Delete project</option>}
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

      <button className="icon-btn ml-auto" onClick={onOpenSettings} aria-label="Open settings" data-testid="open-settings">
        <IconSettings />
      </button>

      {onboardingIncomplete && (
        <button className="finish-setup-pill" onClick={onOpenWizard} data-testid="finish-setup-pill">
          Finish setup
        </button>
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

  async function handleFile(file) {
    if (!file || busy) return;
    const kind = kindForFile(file);
    setBusy(true);
    try {
      const res = await api.ingestFile(projectId, file, kind);
      setToast({ message: `Ingested ${res.chunks} chunks from "${file.name}".` });
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
      setToast({ message: `Ingested ${res.chunks} chunks from URL.` });
      setUrl('');
      onSourcesChange();
    } catch (err) {
      setToast({ message: `URL ingest failed: ${err.message}` });
    } finally {
      setBusy(false);
    }
  }

  async function handleToggleSource(source, enabled) {
    try {
      await api.setSourceEnabled(projectId, source, enabled);
      onSourcesChange();
    } catch (err) {
      setToast({ message: `Update failed: ${err.message}` });
    }
  }

  async function handleDeleteSource(s) {
    if (!window.confirm(
      `Delete "${s.source}"? This removes its ${s.chunks} chunk(s).`
    )) return;
    try {
      await api.deleteSource(projectId, s.source);
      onSourcesChange();
    } catch (err) {
      setToast({ message: `Delete failed: ${err.message}` });
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
              <div
                key={s.source}
                className={`source-item${s.enabled === false ? ' source-item-disabled' : ''}`}
                data-testid="source-item"
              >
                <input
                  type="checkbox"
                  checked={s.enabled !== false}
                  onChange={e => handleToggleSource(s.source, e.target.checked)}
                  title={s.enabled === false ? 'Excluded from chat — click to include' : 'Included in chat — click to exclude'}
                  data-testid="source-enabled-toggle"
                />
                <span className="source-name" title={s.source}>{s.source}</span>
                <span className="source-chunks">{s.chunks} chunks</span>
                <button
                  type="button"
                  className="source-delete-btn"
                  onClick={() => handleDeleteSource(s)}
                  title={`Delete "${s.source}"`}
                  data-testid="source-delete-btn"
                >
                  <IconTrash />
                </button>
              </div>
            ))
          )}
        </div>

      </div>
    </aside>
  );
}

// ─── Chat ─────────────────────────────────────────────────────────────────

const CHAT_SESSION_ID = 'default';

function ChatPane({ projectId, onCitationsChange }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    onCitationsChange([]);
    if (!projectId) { setMessages([]); return; }
    let cancelled = false;
    api.getHistory(projectId, CHAT_SESSION_ID)
      .then(history => { if (!cancelled) setMessages(history ?? []); })
      .catch(() => { if (!cancelled) setMessages([]); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

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
      const res = await api.chat(projectId, CHAT_SESSION_ID, text, webSearchEnabled);
      setMessages(prev => [...prev, { role: 'assistant', content: res.reply, citations: res.citations ?? [] }]);
      onCitationsChange(res.citations ?? []);
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
        {messages.length === 0 && !busy && (
          <div className="chat-empty-state chat-empty-inline">
            <div className="chat-empty-icon"><IconBot /></div>
            <div className="chat-empty-text">Hey! Ask me anything about this project.</div>
            <div className="chat-empty-subtext">I can pull from your ingested docs and meeting notes.</div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`message ${m.role === 'user' ? 'user' : ''}`}>
            <div className={`bubble ${m.role === 'user' ? 'user' : 'ai'}`} data-testid={m.role === 'user' ? 'chat-message-user' : 'chat-message'}>
              <div className="prose">
                <ReactMarkdown>{m.content}</ReactMarkdown>
              </div>
            </div>
          </div>
        ))}

        {busy && (
          <div className="message">
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
        <button
          type="button"
          className={`btn btn-sm web-search-toggle ${webSearchEnabled ? 'btn-primary' : 'btn-secondary'}`}
          onClick={() => setWebSearchEnabled(v => !v)}
          aria-pressed={webSearchEnabled}
          title={webSearchEnabled ? 'Web search is on for the next message' : 'Turn on web search for the next message'}
          data-testid="chat-web-search-toggle"
        >
          🌐 Web search
        </button>
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
//
// Shows the sources (citations) behind the most recent chat answer — lifted
// up from ChatPane via onCitationsChange (see App) rather than rendered
// inline under each message, so the reader can keep it in view alongside
// the conversation instead of expanding a details block per message.

function StudioPane({ projectId, citations }) {
  if (!projectId) return <aside className="studio-pane" />;

  return (
    <aside className="studio-pane">
      <div className="studio-header"><IconBook /> Sources</div>
      <div className="studio-scroll">
        {citations.length === 0 ? (
          <p className="no-data">
            No sources yet — ask something that draws on this project's
            ingested documents.
          </p>
        ) : (
          <ul className="sources-list" data-testid="citations">
            {citations.map(c => (
              <li key={c.ref} className="sources-item" data-testid="citation-item">
                {c.source.startsWith('http') ? (
                  <>[{c.ref}] <a href={c.source} target="_blank" rel="noreferrer">{c.source}</a></>
                ) : (
                  <>[{c.ref}] {c.source} · chunk {c.chunk_index}</>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}

// ─── Pane resizer ───────────────────────────────────────────────────────────

const PANE_MIN_WIDTH = 220;
const PANE_MAX_WIDTH = 560;
const DEFAULT_LEFT_WIDTH = 320;
const DEFAULT_RIGHT_WIDTH = 360;

function clampPaneWidth(w) {
  return Math.min(PANE_MAX_WIDTH, Math.max(PANE_MIN_WIDTH, w));
}

function PaneResizer({ side, onResize, onResizeEnd, active }) {
  function handleMouseDown(e) {
    e.preventDefault();
    document.body.classList.add('pane-resizing');

    function handleMouseMove(ev) {
      const width = side === 'left' ? ev.clientX : window.innerWidth - ev.clientX;
      onResize(clampPaneWidth(width));
    }
    function handleMouseUp() {
      document.body.classList.remove('pane-resizing');
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
      onResizeEnd();
    }
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
  }

  return (
    <div
      className={`pane-resizer ${side === 'left' ? 'pane-resizer-left' : 'pane-resizer-right'} ${active ? 'dragging' : ''}`}
      onMouseDown={handleMouseDown}
      role="separator"
      aria-orientation="vertical"
      aria-label={side === 'left' ? 'Resize sources panel' : 'Resize studio panel'}
    />
  );
}

// ─── App ──────────────────────────────────────────────────────────────────

export default function App() {
  const [projects, setProjects] = useState([]);
  const [activeId, setActiveId] = useState(() => {
    // Prefer the "default project" set in Settings (JSON-encoded, via
    // useLocalStorageState) over the last-selected project, if one is set.
    try {
      const raw = localStorage.getItem('defaultProjectId');
      const defaultId = raw ? JSON.parse(raw) : null;
      if (defaultId) return defaultId;
    } catch {
      // ignore — fall through to last-selected project
    }
    return localStorage.getItem('projectId') || null;
  });
  const [toast, setToast] = useState(null);
  const [sourcesKey, setSourcesKey] = useState(0);
  const [latestCitations, setLatestCitations] = useState([]);

  const [showWizard, setShowWizard] = useState(() => localStorage.getItem(WIZARD_STATUS_KEY) === null);
  const [onboardingStatus, setOnboardingStatus] = useState(() => localStorage.getItem(WIZARD_STATUS_KEY));
  const [showSettings, setShowSettings] = useState(false);

  function handleWizardExit() {
    setShowWizard(false);
    setOnboardingStatus(localStorage.getItem(WIZARD_STATUS_KEY));
  }

  const [leftWidth, setLeftWidth] = useState(() => {
    const saved = Number(localStorage.getItem('leftPaneWidth'));
    return saved ? clampPaneWidth(saved) : DEFAULT_LEFT_WIDTH;
  });
  const [rightWidth, setRightWidth] = useState(() => {
    const saved = Number(localStorage.getItem('rightPaneWidth'));
    return saved ? clampPaneWidth(saved) : DEFAULT_RIGHT_WIDTH;
  });
  const [resizingSide, setResizingSide] = useState(null);
  const leftWidthRef = useRef(leftWidth);
  const rightWidthRef = useRef(rightWidth);
  useEffect(() => { leftWidthRef.current = leftWidth; }, [leftWidth]);
  useEffect(() => { rightWidthRef.current = rightWidth; }, [rightWidth]);

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

  async function handleWizardProjectCreated(project) {
    await refreshProjects();
    if (project?.id) selectProject(project.id);
  }

  function handleLeftResizeEnd() {
    localStorage.setItem('leftPaneWidth', String(leftWidthRef.current));
    setResizingSide(null);
  }

  function handleRightResizeEnd() {
    localStorage.setItem('rightPaneWidth', String(rightWidthRef.current));
    setResizingSide(null);
  }

  return (
    <div className="app" style={{ '--left-w': `${leftWidth}px`, '--right-w': `${rightWidth}px` }}>
      <TopBar
        projects={projects}
        activeId={activeId}
        onSelect={selectProject}
        onRefresh={refreshProjects}
        setToast={setToast}
        onboardingIncomplete={onboardingStatus !== 'completed'}
        onOpenWizard={() => setShowWizard(true)}
        onOpenSettings={() => setShowSettings(true)}
      />
      <LeftPane
        projectId={activeId}
        sourcesKey={sourcesKey}
        onSourcesChange={() => setSourcesKey(k => k + 1)}
        setToast={setToast}
      />
      <PaneResizer
        side="left"
        active={resizingSide === 'left'}
        onResize={w => { setResizingSide('left'); setLeftWidth(w); }}
        onResizeEnd={handleLeftResizeEnd}
      />
      <ChatPane
        projectId={activeId}
        onCitationsChange={setLatestCitations}
      />
      <PaneResizer
        side="right"
        active={resizingSide === 'right'}
        onResize={w => { setResizingSide('right'); setRightWidth(w); }}
        onResizeEnd={handleRightResizeEnd}
      />
      <StudioPane
        projectId={activeId}
        citations={latestCitations}
      />

      {toast && (
        <Toast
          message={toast.message}
          url={toast.url}
          onDismiss={() => setToast(null)}
        />
      )}

      {showWizard && (
        <SetupWizard onExit={handleWizardExit} onProjectCreated={handleWizardProjectCreated} />
      )}

      {showSettings && (
        <SettingsPage
          onClose={() => setShowSettings(false)}
          projects={projects}
          activeId={activeId}
          onSelectProject={selectProject}
          onRefreshProjects={refreshProjects}
          setToast={setToast}
        />
      )}
    </div>
  );
}
