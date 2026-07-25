import { useEffect, useState } from 'react';
import * as api from '../../api.js';
import { IconCheck, IconAlert, IconLoader } from '../../icons.jsx';

function TestButton() {
  const [state, setState] = useState(null);

  async function handleClick() {
    setState('testing');
    setState(await api.testEmbeddingsConnection());
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

export default function EmbeddingsTab() {
  const [config, setConfig] = useState(null);

  useEffect(() => {
    api.getLlmConfig().then(setConfig);
  }, []);

  return (
    <div className="settings-section">
      <div className="wizard-step-desc">
        Embeddings are served by a bundled, dedicated embedding server (not Ollama, and not
        whichever cloud provider is active for chat) so documents and conversation memory stay
        searchable no matter which chat provider you pick. The model is fixed by design — changing
        it later would invalidate every document and conversation already embedded, so there's no
        picker here.
      </div>

      {config && (
        <div className="wizard-status-row">
          <div className="wizard-status-main">
            <div className="wizard-status-name">Embedding model</div>
            <div className={`wizard-status-detail ${config.embeddings.error ? 'error' : ''}`}>
              {config.embeddings.model || config.embeddings.error || 'Unknown'}
            </div>
          </div>
        </div>
      )}

      <TestButton />
    </div>
  );
}
