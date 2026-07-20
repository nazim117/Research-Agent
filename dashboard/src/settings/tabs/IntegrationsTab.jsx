import { useEffect, useState } from 'react';
import * as api from '../../api.js';
import { IconAlert, IconCheck, IconLoader, IconRefresh } from '../../icons.jsx';
import { useHealthChecks } from '../../shared/useHealthChecks.js';
import ServiceStatusRow from '../../shared/ServiceStatusRow.jsx';

const SERVICES = ['ollama', 'qdrant', 'docker', 'mcp_server'];
const REQUIRED_VARS = {
  jira: ['JIRA_BASE_URL', 'JIRA_EMAIL', 'JIRA_API_TOKEN'],
  github: ['GITHUB_TOKEN'],
};

function IntegrationRow({ name, label, status, onRecheck, checking, onGoToAdvanced }) {
  return (
    <div className="wizard-status-row" data-testid={`settings-integration-${name}`}>
      <span className={`wizard-status-icon ${status?.configured ? 'ok' : 'pending'}`}>
        {status?.configured ? <IconCheck /> : <IconAlert />}
      </span>
      <div className="wizard-status-main">
        <div className="wizard-status-name">{label}</div>
        <div className="wizard-status-detail">
          {status?.configured ? (
            name === 'jira' ? status.base_url : 'Connected'
          ) : (
            <>
              Not configured — set {REQUIRED_VARS[name].join(', ')} in{' '}
              <button type="button" className="link-btn" onClick={onGoToAdvanced}>
                Advanced
              </button>
            </>
          )}
        </div>
      </div>
      <button className="btn btn-secondary btn-sm" onClick={onRecheck} disabled={checking}>
        {checking ? <><IconLoader className="spin" /> Checking...</> : <><IconRefresh /> Recheck</>}
      </button>
    </div>
  );
}

export default function IntegrationsTab({ onGoToAdvanced }) {
  const [health, setHealth] = useState(null);
  const { checking, checkError, runCheck } = useHealthChecks(health, setHealth);

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
          Credentials are set in the Advanced tab and stored in mcp-server's environment —
          mcp-server is the only process allowed to hold them. Changes there need a service
          restart to take effect.
        </div>
        {integrations && (
          <>
            <IntegrationRow name="jira" label="Jira" status={integrations.jira} onRecheck={recheckIntegrations} checking={checkingIntegrations} onGoToAdvanced={onGoToAdvanced} />
            <IntegrationRow name="github" label="GitHub" status={integrations.github} onRecheck={recheckIntegrations} checking={checkingIntegrations} onGoToAdvanced={onGoToAdvanced} />
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
                testIdPrefix="settings"
              />
            ))}
          </div>
        )}
      </div>
    </>
  );
}
