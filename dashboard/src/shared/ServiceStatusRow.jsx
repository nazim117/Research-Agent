import { IconCheck, IconAlert, IconLoader } from '../icons.jsx';

const SERVICE_LABELS = {
  ollama: 'Ollama',
  qdrant: 'Qdrant',
  docker: 'Docker',
  mcp_server: 'mcp-server',
};

const DOCS_LINKS = {
  ollama: 'https://ollama.com/download',
  qdrant: 'https://qdrant.tech/documentation/quick-start/',
  docker: 'https://docs.docker.com/desktop/',
  mcp_server: 'https://github.com',
};

export default function ServiceStatusRow({ service, info, fix, onFix, extraActions, testIdPrefix = 'wizard' }) {
  const label = SERVICE_LABELS[service] || service;

  return (
    <div data-testid={`${testIdPrefix}-health-${service}`}>
      <div className="wizard-status-row">
        <span className={`wizard-status-icon ${info.status === 'ok' ? 'ok' : 'error'}`}>
          {info.status === 'ok' ? <IconCheck /> : <IconAlert />}
        </span>
        <div className="wizard-status-main">
          <div className="wizard-status-name">{label}</div>
          <div className={`wizard-status-detail ${info.status === 'ok' ? '' : 'error'}`}>
            {info.detail}
          </div>
        </div>
        {!info.required && (
          <span className="wizard-status-badge optional">optional</span>
        )}
        {extraActions}
        {info.status !== 'ok' && !fix?.failed && (
          <button
            className="btn btn-secondary btn-sm"
            onClick={onFix}
            disabled={fix?.fixing}
            data-testid={`${testIdPrefix}-fix-${service}`}
          >
            {fix?.fixing ? <><IconLoader className="spin" /> Fixing...</> : 'Fix'}
          </button>
        )}
        {fix?.failed && (
          <a href={DOCS_LINKS[service]} target="_blank" rel="noreferrer" className="wizard-link">
            Manual fix instructions
          </a>
        )}
      </div>
      {fix?.log?.length > 0 && (
        <details className="wizard-fix-log">
          <summary>View log</summary>
          {fix.log.map((line, i) => (
            <div key={i} className="wizard-fix-log-line">{line}</div>
          ))}
        </details>
      )}
    </div>
  );
}
