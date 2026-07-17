import { useState } from 'react';
import * as api from '../../api.js';
import { IconCheck, IconAlert, IconLoader } from '../../icons.jsx';
import ModelPickerRow from '../../shared/ModelPickerRow.jsx';

const EMBED_MODELS = ['nomic-embed-text'];

function TestButton() {
  const [state, setState] = useState(null);

  async function handleClick() {
    setState('testing');
    setState(await api.testOllamaConnection());
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
  const [embedModel, setEmbedModel] = useState('nomic-embed-text');

  return (
    <div className="settings-section">
      <div className="wizard-step-desc">
        Embeddings always run through Ollama, regardless of which chat provider is configured.
        The active model is set via <code>OLLAMA_EMBED_MODEL</code> in your <code>.env</code>
        file — pull it below so it's available locally.
      </div>

      <TestButton />

      <div className="edit-label mt-12">Pull a model</div>
      <ModelPickerRow
        kind="embed"
        label="Embedding model"
        options={EMBED_MODELS}
        selectedModel={embedModel}
        onInstalled={(_kind, model) => setEmbedModel(model)}
      />
    </div>
  );
}
