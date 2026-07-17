import { useEffect, useState } from 'react';
import * as api from '../../api.js';
import { IconAlert, IconCheck, IconLoader, IconRefresh } from '../../icons.jsx';
import { useHealthChecks } from '../../shared/useHealthChecks.js';
import ServiceStatusRow from '../../shared/ServiceStatusRow.jsx';

const SERVICES = ['ollama', 'qdrant', 'docker', 'mcpServer'];
const REQUIRED_VARS = {
  jira: ['JIRA_BASE_URL', 'JIRA_EMAIL', 'JIRA_API_TOKEN'],
  github: ['GITHUB_TOKEN'],
};

function IntegrationRow({ name, label, status, onRecheck, checking }) {
  return (
    <div className="wizard-status-row" data-testid={`settings-integration-${name}`}>
      <span className={`wizard-status-icon ${status?.configured ? 'ok' : 'pending'}`}>
        {status?.configured ? <IconCheck /> : <IconAlert />}
      </span>
      <div className="wizard-status-main">
        <div className="wizard-status-name">{label}</div>
        <div className="wizard-status-detail">
          {status?.configured
            ? (name === 'jira' ? status.baseUrl : 'Connected')
            : `Not configured — set ${REQUIRED_VARS[name].join(', ')} in mcp-server's .env`}
        </div>
      </div>
      <button className="btn btn-secondary btn-sm" onClick={onRecheck} disabled={checking}>
        {checking ? <><IconLoader className="spin" /> Checking...</> : <><IconRefresh /> Recheck</>}
      </button>
    </div>
  );
}

function ServiceControls({ service, onControl }) {
  const [busy, setBusy] = useState(null); // action name while in flight
  const [log, setLog] = useState(null);

  async function run(action) {
    setBusy(action);
    setLog(null);
    try {
      const result = await onControl(service, action);
      setLog(result.log);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="row-gap-sm">
      {['start', 'stop', 'restart'].map((action) => (
        <button
          key={action}
          className="btn btn-secondary btn-sm"
          onClick={() => run(action)}
          disabled={Boolean(busy)}
          data-testid={`settings-${action}-${service}`}
        >
          {busy === action ? '...' : action[0].toUpperCase() + action.slice(1)}
        </button>
      ))}
      {log && (
        <details className="wizard-fix-log">
          <summary>View log</summary>
          {log.map((line, i) => <div key={i} className="wizard-fix-log-line">{line}</div>)}
        </details>
      )}
    </div>
  );
}

export default function IntegrationsTab() {
  const [health, setHealth] = useState(null);
  const { checking, checkError, fixState, runCheck, handleFix } = useHealthChecks(health, setHealth);

  const [integrations, setIntegrations] = useState(null);
  const [checkingIntegrations, setCheckingIntegrations] = useState(false);

  async function recheckIntegrations() {
    setCheckingIntegrations(true);
    try {
      setIntegrations(await api.getIntegrationStatus());
    } finally {
      setCheckingIntegrations(false);
    }
  }

  useEffect(() => { recheckIntegrations(); }, []);

  return (
    <>
      <div className="settings-section">
        <div className="edit-label">Jira &amp; GitHub</div>
        <div className="wizard-step-desc">
          These are configured via mcp-server's environment, not from this UI — mcp-server is the
          only process allowed to hold these credentials.
        </div>
        {integrations && (
          <>
            <IntegrationRow name="jira" label="Jira" status={integrations.jira} onRecheck={recheckIntegrations} checking={checkingIntegrations} />
            <IntegrationRow name="github" label="GitHub" status={integrations.github} onRecheck={recheckIntegrations} checking={checkingIntegrations} />
          </>
        )}
      </div>

      <div className="settings-section">
        <div className="edit-label">Services</div>
        <div className="wizard-step-desc">
          Live reachability for the services Research Agent depends on.
        </div>
        {!health && checking && (
          <div className="row-gap-sm">
            <IconLoader className="spin" /> Checking services...
          </div>
        )}

        {!health && !checking && checkError && (
          <div>
            <div className="wizard-warning">Couldn't check service status: {checkError}</div>
            <button className="btn btn-secondary btn-sm mt-8" onClick={runCheck}>
              <IconRefresh /> Retry
            </button>
          </div>
        )}

        {health && (
          <div className="settings-service-grid">
            {SERVICES.map((service) => (
              <ServiceStatusRow
                key={service}
                service={service}
                info={health[service]}
                fix={fixState[service]}
                onFix={() => handleFix(service)}
                testIdPrefix="settings"
                extraActions={<ServiceControls service={service} onControl={api.controlService} />}
              />
            ))}
          </div>
        )}
      </div>
    </>
  );
}
