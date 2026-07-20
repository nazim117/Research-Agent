import { useEffect, useState } from 'react';
import * as api from '../../api.js';
import { IconLoader, IconRefresh } from '../../icons.jsx';
import EnvVarRow from '../../shared/EnvVarRow.jsx';
import { maskHint } from '../../shared/maskHint.js';

const KEYS = ['JIRA_BASE_URL', 'JIRA_EMAIL', 'JIRA_API_TOKEN', 'GITHUB_TOKEN'];

// Self-contained, same real save path as Settings > Advanced (api.listEnvVars/
// updateEnvVar -> chat-agent's /config/env, proxied to mcp-server for these
// keys). No local wizard state — credentials are saved directly to .env as
// soon as each field's Save button is clicked, so there's nothing to lose if
// the wizard is closed before finishing.
export default function CredentialsStep() {
  const [envVars, setEnvVars] = useState(null);
  const [mcpError, setMcpError] = useState(null);
  const [edits, setEdits] = useState({});
  const [savingKey, setSavingKey] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [loading, setLoading] = useState(false);

  function load() {
    setLoading(true);
    setLoadError(null);
    api.listEnvVars()
      .then(({ vars, mcp_error }) => {
        setEnvVars(vars.filter((v) => KEYS.includes(v.key)));
        setMcpError(mcp_error);
      })
      .catch((err) => setLoadError(err.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

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
    } catch {
      // Surfaced inline via the row's own disabled/save state — the wizard
      // has no toast host, unlike Settings.
    } finally {
      setSavingKey(null);
    }
  }

  return (
    <div>
      <div className="wizard-step-title">Connect Jira &amp; GitHub</div>
      <div className="wizard-step-desc">
        Optional — skip if you don't need to sync work items. You can also add these later
        from Settings &gt; Advanced. Saved values are written to mcp-server's <code>.env</code>{' '}
        immediately, but it needs a restart to pick them up.
      </div>

      {loadError && (
        <div>
          <div className="wizard-warning" data-testid="wizard-credentials-error">
            Couldn't load: {loadError}
          </div>
          <button className="btn btn-secondary btn-sm mt-8" onClick={load} disabled={loading}>
            {loading ? <><IconLoader className="spin" /> Retrying...</> : <><IconRefresh /> Retry</>}
          </button>
        </div>
      )}
      {!envVars && !loadError && <p className="no-data">Loading...</p>}

      {mcpError && (
        <div className="wizard-warning mt-8" data-testid="wizard-credentials-mcp-error">
          Couldn't reach mcp-server, so these can't be saved right now: {mcpError}
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
        />
      ))}
    </div>
  );
}
