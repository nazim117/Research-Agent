import { useState } from 'react';
import * as api from '../../api.js';
import { IconEdit, IconTrash, IconCheck } from '../../icons.jsx';
import { useLocalStorageState } from '../../shared/useLocalStorageState.js';

function ProjectRow({ project, isActive, isDefault, onSelect, onSetDefault, onRename, onDelete }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(project.name);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    const trimmed = name.trim();
    if (!trimmed || trimmed === project.name) { setEditing(false); setName(project.name); return; }
    setSaving(true);
    try {
      await onRename(project.id, trimmed);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  if (editing) {
    return (
      <div className="settings-project-row" data-testid="settings-project-row">
        <input
          className="input flex-1"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
          disabled={saving}
        />
        <div className="settings-project-actions">
          <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={saving || !name.trim()}>Save</button>
          <button className="btn btn-secondary btn-sm" onClick={() => { setEditing(false); setName(project.name); }} disabled={saving}>Cancel</button>
        </div>
      </div>
    );
  }

  return (
    <div className="settings-project-row" data-testid="settings-project-row">
      <span
        className={`settings-project-name ${isActive ? 'active' : ''}`}
        onClick={() => onSelect(project.id)}
        role="button"
        tabIndex={0}
        title="Switch to this project"
      >
        {project.name}
      </span>
      {isDefault && <span className="wizard-status-badge optional">default</span>}
      <div className="settings-project-actions">
        <button
          className="icon-btn"
          onClick={() => onSetDefault(isDefault ? null : project.id)}
          title={isDefault ? 'Unset as default project' : 'Set as default project'}
          data-testid="settings-project-set-default"
        >
          <IconCheck />
        </button>
        <button className="icon-btn" onClick={() => setEditing(true)} title="Rename" data-testid="settings-project-rename">
          <IconEdit />
        </button>
        <button className="icon-btn danger" onClick={() => onDelete(project)} title="Delete" data-testid="settings-project-delete">
          <IconTrash />
        </button>
      </div>
    </div>
  );
}

export default function GeneralTab({ projects, activeId, onSelectProject, onRefreshProjects, setToast }) {
  const [defaultProjectId, setDefaultProjectId] = useLocalStorageState('defaultProjectId', null);

  async function handleRename(id, name) {
    try {
      await api.renameProject(id, name);
      await onRefreshProjects();
    } catch (err) {
      setToast({ message: `Rename failed: ${err.message}` });
    }
  }

  async function handleDelete(project) {
    if (!window.confirm(`Delete project "${project.name}"? This removes all its messages and memory.`)) return;
    try {
      await api.deleteProject(project.id);
      if (defaultProjectId === project.id) setDefaultProjectId(null);
      await onRefreshProjects();
    } catch (err) {
      setToast({ message: `Delete failed: ${err.message}` });
    }
  }

  return (
    <div className="settings-section">
      <div className="wizard-step-desc">
        Switch between projects, rename or delete them, and choose which one opens by default.
      </div>

      {projects.length === 0 ? (
        <p className="no-data">No projects yet.</p>
      ) : (
        projects.map((p) => (
          <ProjectRow
            key={p.id}
            project={p}
            isActive={p.id === activeId}
            isDefault={p.id === defaultProjectId}
            onSelect={onSelectProject}
            onSetDefault={setDefaultProjectId}
            onRename={handleRename}
            onDelete={handleDelete}
          />
        ))
      )}
    </div>
  );
}
