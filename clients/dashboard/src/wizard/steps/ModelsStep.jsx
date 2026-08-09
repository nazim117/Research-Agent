import { useEffect, useState } from 'react';
import * as api from '../../api.js';
import ModelPickerRow from '../../shared/ModelPickerRow.jsx';
import CloudProviderPanel from '../../shared/CloudProviderPanel.jsx';

const CHAT_MODELS = ['llama3', 'mistral'];

export default function ModelsStep({ models, onModelsChange }) {
  const [config, setConfig] = useState(null);

  function loadConfig() {
    return api.getLlmConfig().then(setConfig);
  }

  useEffect(() => { loadConfig(); }, []);

  function handleTabChange(provider) {
    onModelsChange({ ...models, provider });
  }

  function handleInstalled(kind, model) {
    onModelsChange({ ...models, provider: 'local', [kind]: model });
  }

  async function handleCloudActivated() {
    await loadConfig();
    onModelsChange({ ...models, provider: 'cloud', cloudConfigured: true });
  }

  return (
    <div>
      <div className="wizard-step-title">Choose your chat model</div>
      <div className="wizard-step-desc">
        Research Agent can run chat through a cloud provider (OpenAI, Claude, Grok, Groq,
        DeepSeek, or any OpenAI-compatible endpoint) or fully locally through Ollama. Embeddings
        are handled by a separate bundled service with nothing to configure here — the previous
        Health Check step already confirmed it's running.
      </div>

      <div className="tabs mt-12">
        <button
          className={`tab ${models.provider === 'cloud' ? 'active' : ''}`}
          onClick={() => handleTabChange('cloud')}
          data-testid="wizard-llm-tab-cloud"
        >
          Cloud provider
        </button>
        <button
          className={`tab ${models.provider === 'local' ? 'active' : ''}`}
          onClick={() => handleTabChange('local')}
          data-testid="wizard-llm-tab-local"
        >
          Local (Ollama)
        </button>
      </div>

      {models.provider === 'cloud' && (
        <CloudProviderPanel config={config} onActivated={handleCloudActivated} />
      )}
      {models.provider === 'local' && (
        <ModelPickerRow
          kind="chat"
          label="Chat model"
          options={CHAT_MODELS}
          selectedModel={models.chat}
          onInstalled={handleInstalled}
        />
      )}
    </div>
  );
}
