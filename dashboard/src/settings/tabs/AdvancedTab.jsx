import { useEffect, useState } from 'react';
import * as api from '../../api.js';
import { IconEye, IconEyeOff, IconLoader } from '../../icons.jsx';

function EnvVarRow({ envVar, draft, revealed, onToggleReveal, onChange, onSave, saving }) {
  const dirty = draft !== undefined && draft !== envVar.value;
  const displayValue = draft !== undefined ? draft : envVar.value;
  const inputType = envVar.secret && !revealed ? 'password' : 'text';

  return (
    <div className="settings-envvar-row" data-testid={`settings-envvar-${envVar.key}`}>
      <span className="settings-envvar-key">{envVar.key}</span>
      <input
        className="input settings-envvar-value"
        type={inputType}
        value={displayValue}
        onChange={(e) => onChange(envVar.key, e.target.value)}
        placeholder={envVar.secret ? '(not set)' : ''}
      />
      <div className="settings-envvar-actions">
        {envVar.secret && (
          <button
            className="icon-btn"
            onClick={() => onToggleReveal(envVar.key)}
            aria-label={revealed ? 'Hide value' : 'Reveal value'}
            title={revealed ? 'Hide value' : 'Reveal value'}
          >
            {revealed ? <IconEyeOff /> : <IconEye />}
          </button>
        )}
        <button
          className="btn btn-secondary btn-sm"
          onClick={() => onSave(envVar.key)}
          disabled={!dirty || saving}
        >
          {saving ? <IconLoader className="spin" /> : 'Save'}
        </button>
      </div>
    </div>
  );
}

export default function AdvancedTab({ onDirtyChange, setToast }) {
  const [envVars, setEnvVars] = useState(null);
  const [edits, setEdits] = useState({}); // { [key]: draftValue }
  const [revealed, setRevealed] = useState({}); // { [key]: bool }
  const [savingKey, setSavingKey] = useState(null);

  useEffect(() => {
    api.listEnvVars().then(setEnvVars);
  }, []);

  useEffect(() => {
    onDirtyChange(Object.keys(edits).length > 0);
  }, [edits, onDirtyChange]);

  function handleChange(key, value) {
    setEdits((e) => ({ ...e, [key]: value }));
  }

  function handleToggleReveal(key) {
    setRevealed((r) => ({ ...r, [key]: !r[key] }));
  }

  async function handleSave(key) {
    setSavingKey(key);
    try {
      await api.updateEnvVar(key, edits[key]);
      setEnvVars((vars) => vars.map((v) => (v.key === key ? { ...v, value: edits[key] } : v)));
      setEdits((e) => {
        const next = { ...e };
        delete next[key];
        return next;
      });
      setToast?.({ message: `${key} updated (not yet live — restart required).` });
    } catch (err) {
      setToast?.({ message: `Save failed: ${err.message}` });
    } finally {
      setSavingKey(null);
    }
  }

  return (
    <div className="settings-section">
      <div className="wizard-warning" data-testid="settings-advanced-banner">
        These aren't wired to a real backend yet — environment variables are managed via your
        <code> .env</code> file today, and any service that reads one needs a restart to pick up
        a change. Edits here don't persist.
      </div>

      {!envVars && <p className="no-data">Loading...</p>}

      {envVars && envVars.map((v) => (
        <EnvVarRow
          key={v.key}
          envVar={v}
          draft={edits[v.key]}
          revealed={Boolean(revealed[v.key])}
          onToggleReveal={handleToggleReveal}
          onChange={handleChange}
          onSave={handleSave}
          saving={savingKey === v.key}
        />
      ))}
    </div>
  );
}
