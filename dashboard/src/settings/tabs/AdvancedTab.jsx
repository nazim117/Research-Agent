import { useEffect, useState } from 'react';
import * as api from '../../api.js';
import { IconLoader, IconRefresh } from '../../icons.jsx';
import EnvVarRow from '../../shared/EnvVarRow.jsx';
import { maskHint } from '../../shared/maskHint.js';
import { LLM_PROVIDER_OPTIONS } from '../../shared/llmProviderPresets.js';

export default function AdvancedTab({ onDirtyChange, setToast }) {
  const [envVars, setEnvVars] = useState(null);
  const [mcpError, setMcpError] = useState(null);
  const [edits, setEdits] = useState({}); // { [key]: draftValue }
  const [savingKey, setSavingKey] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [loading, setLoading] = useState(false);

  function load() {
    setLoading(true);
    setLoadError(null);
    api.listEnvVars()
      .then(({ vars, mcp_error }) => {
        setEnvVars(vars);
        setMcpError(mcp_error);
      })
      .catch((err) => setLoadError(err.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  useEffect(() => {
    onDirtyChange(Object.keys(edits).length > 0);
  }, [edits, onDirtyChange]);

  function handleChange(key, value) {
    setEdits((e) => ({ ...e, [key]: value }));
  }

  async function handleSave(key) {
    setSavingKey(key);
    try {
      const value = edits[key];
      await api.updateEnvVar(key, value);
      setEnvVars((vars) =>
        vars.map((v) =>
          v.key === key
            ? { ...v, configured: value !== '', hint: v.secret ? maskHint(value) : value }
            : v
        )
      );
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
        Changes here are written to your <code>.env</code> file immediately, but the
        affected service needs a restart to pick them up. Secret values are never sent back
        to this page — only whether they're configured.
      </div>

      {loadError && (
        <div>
          <p className="no-data">Failed to load: {loadError}</p>
          <button className="btn btn-secondary btn-sm mt-8" onClick={load} disabled={loading}>
            {loading ? <><IconLoader className="spin" /> Retrying...</> : <><IconRefresh /> Retry</>}
          </button>
        </div>
      )}
      {!envVars && !loadError && <p className="no-data">Loading...</p>}

      {mcpError && (
        <div className="wizard-warning mt-8" data-testid="settings-advanced-mcp-error">
          Couldn't reach mcp-server — Jira/GitHub/web-search settings are unavailable until
          it's back: {mcpError}
        </div>
      )}

      {envVars && envVars.map((v) => (
        <EnvVarRow
          key={v.key}
          envVar={v}
          draft={edits[v.key]}
          onChange={handleChange}
          onSave={handleSave}
          saving={savingKey === v.key}
          options={v.key === 'LLM_PROVIDER' ? LLM_PROVIDER_OPTIONS : undefined}
        />
      ))}
    </div>
  );
}
