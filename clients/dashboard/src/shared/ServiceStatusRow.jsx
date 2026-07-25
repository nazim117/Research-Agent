import { IconCheck, IconAlert } from '../icons.jsx';

const SERVICE_LABELS = {
  ollama: 'Ollama',
  qdrant: 'Qdrant',
  embeddings: 'Embeddings',
  docker: 'Docker',
  mcp_server: 'mcp-server',
  web_search: 'Web search',
};

const DOCS_LINKS = {
  ollama: 'https://ollama.com/download',
  qdrant: 'https://qdrant.tech/documentation/quick-start/',
  embeddings: 'https://github.com/huggingface/text-embeddings-inference',
  docker: 'https://docs.docker.com/desktop/',
  mcp_server: 'https://github.com',
  web_search: 'https://docs.searxng.org/admin/installation-docker.html',
};

export default function ServiceStatusRow({ service, info, extraActions, testIdPrefix = 'wizard' }) {
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
        {info.status !== 'ok' && (
          <a href={DOCS_LINKS[service]} target="_blank" rel="noreferrer" className="wizard-link">
            Manual fix instructions
          </a>
        )}
      </div>
    </div>
  );
}
