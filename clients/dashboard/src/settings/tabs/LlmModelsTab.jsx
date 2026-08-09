import { useEffect, useState } from 'react';
import * as api from '../../api.js';
import { IconCheck, IconAlert, IconLoader } from '../../icons.jsx';
import ModelPickerRow from '../../shared/ModelPickerRow.jsx';
import CloudProviderPanel from '../../shared/CloudProviderPanel.jsx';

const CHAT_MODELS = ['llama3', 'mistral'];

function TestButton() {
  const [state, setState] = useState(null); // null | 'testing' | { ok, message }

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

function LocalPanel({ config, chatModel, setChatModel, onUseOllama, switching }) {
  const isActive = config?.provider === 'ollama';

  return (
    <div>
      <div className="wizard-status-row">
        <div className="wizard-status-main">
          <div className="wizard-status-name">Ollama (local)</div>
          <div className="wizard-status-detail">
            {config ? `Chat model: ${config.ollama.chat_model}` : 'Loading...'}
          </div>
        </div>
        {isActive ? (
          <span className="wizard-status-badge active">Active</span>
        ) : (
          <button className="btn btn-secondary btn-sm" onClick={onUseOllama} disabled={switching}>
            {switching ? <><IconLoader className="spin" /> Switching...</> : 'Use Ollama'}
          </button>
        )}
      </div>

      <TestButton />

      <div className="edit-label mt-12">Pull a model</div>
      <ModelPickerRow
        kind="chat"
        label="Chat model"
        options={CHAT_MODELS}
        selectedModel={chatModel}
        onInstalled={(_kind, model) => setChatModel(model)}
      />
    </div>
  );
}

export default function LlmModelsTab() {
  const [tab, setTab] = useState('local');
  const [config, setConfig] = useState(null);
  const [chatModel, setChatModel] = useState(null);
  const [switchingProvider, setSwitchingProvider] = useState(false);

  function loadConfig() {
    return api.getLlmConfig().then((c) => {
      setConfig(c);
      setChatModel(c.ollama.chat_model);
    });
  }

  useEffect(() => { loadConfig(); }, []);

  async function handleUseOllama() {
    setSwitchingProvider(true);
    try {
      await api.updateEnvVar('LLM_PROVIDER', 'ollama');
      await loadConfig();
    } finally {
      setSwitchingProvider(false);
    }
  }

  return (
    <div className="settings-section">
      <div className="wizard-step-desc">
        Embeddings always run through Ollama regardless of which chat provider is active.
        Switching the active chat provider needs a chat-agent restart to take effect.
      </div>

      <div className="tabs mt-12">
        <button className={`tab ${tab === 'local' ? 'active' : ''}`} onClick={() => setTab('local')} data-testid="settings-llm-tab-local">
          Local (Ollama)
        </button>
        <button className={`tab ${tab === 'cloud' ? 'active' : ''}`} onClick={() => setTab('cloud')} data-testid="settings-llm-tab-cloud">
          Cloud provider
        </button>
      </div>

      {tab === 'local' && (
        <LocalPanel
          config={config}
          chatModel={chatModel}
          setChatModel={setChatModel}
          onUseOllama={handleUseOllama}
          switching={switchingProvider}
        />
      )}
      {tab === 'cloud' && <CloudProviderPanel config={config} onActivated={loadConfig} />}
    </div>
  );
}
