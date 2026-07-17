import { useState } from 'react';
import * as api from '../../api.js';
import { IconCheck, IconAlert, IconLoader } from '../../icons.jsx';

// NOTE: Jira/GitHub credentials are currently env-var-only, set once at
// deploy time on mcp-server (see services/chat-agent/config.py) — the
// chat-agent process never holds them. This form is forward-looking
// scaffolding for a future settings-storage mechanism; nothing entered
// here is persisted anywhere yet (testJiraConnection/testGitHubConnection
// are stubs, see api.js).

function TestButton({ onTest }) {
  const [state, setState] = useState(null); // null | 'testing' | { ok, message }

  async function handleClick() {
    setState('testing');
    const result = await onTest();
    setState(result);
  }

  return (
    <div className="row-gap-sm mt-8">
      <button className="btn btn-secondary btn-sm" onClick={handleClick} disabled={state === 'testing'}>
        {state === 'testing' ? <><IconLoader className="spin" /> Testing...</> : 'Test connection'}
      </button>
      {state && state !== 'testing' && (
        <span className={`wizard-status-detail ${state.ok ? '' : 'error'}`}>
          {state.ok ? <IconCheck /> : <IconAlert />} {state.message}
        </span>
      )}
    </div>
  );
}

export default function CredentialsStep({ credentials, onCredentialsChange }) {
  function set(field, value) {
    onCredentialsChange({ ...credentials, [field]: value });
  }

  return (
    <div>
      <div className="wizard-step-title">Connect Jira &amp; GitHub</div>
      <div className="wizard-step-desc">
        Optional — skip if you don't need to sync work items. You can add these later from
        a project's integration settings.
      </div>

      <label className="field-label">Jira URL</label>
      <input
        className="input"
        value={credentials.jiraUrl}
        onChange={(e) => set('jiraUrl', e.target.value)}
        placeholder="https://yourteam.atlassian.net"
        data-testid="wizard-jira-url"
      />
      <label className="field-label">Jira API token</label>
      <input
        className="input"
        type="password"
        value={credentials.jiraToken}
        onChange={(e) => set('jiraToken', e.target.value)}
        placeholder="••••••••"
        data-testid="wizard-jira-token"
      />
      <TestButton onTest={() => api.testJiraConnection({ url: credentials.jiraUrl, token: credentials.jiraToken })} />

      <label className="field-label mt-12">GitHub token</label>
      <input
        className="input"
        type="password"
        value={credentials.githubToken}
        onChange={(e) => set('githubToken', e.target.value)}
        placeholder="ghp_••••••••"
        data-testid="wizard-github-token"
      />
      <TestButton onTest={() => api.testGitHubConnection({ token: credentials.githubToken })} />
    </div>
  );
}
