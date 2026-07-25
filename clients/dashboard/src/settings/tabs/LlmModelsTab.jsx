import { useEffect, useState } from 'react';
import * as api from '../../api.js';
import { IconCheck, IconAlert, IconLoader, IconRefresh } from '../../icons.jsx';
import ModelPickerRow from '../../shared/ModelPickerRow.jsx';
import { maskHint } from '../../shared/maskHint.js';
import { CLOUD_PRESETS } from '../../shared/llmProviderPresets.js';

const CHAT_MODELS = ['llama3', 'mistral'];
const CLOUD_KEYS = ['OPENAI_PROVIDER_LABEL', 'OPENAI_BASE_URL', 'OPENAI_MODEL', 'OPENAI_API_KEY'];

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

function CloudPanel({ config, onActivated }) {
  const [envVars, setEnvVars] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [loading, setLoading] = useState(false);

  const [presetId, setPresetId] = useState('openai');
  const [label, setLabel] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [apiKeyConfigured, setApiKeyConfigured] = useState(false);
  const [apiKeyHint, setApiKeyHint] = useState(null);

  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState(null); // { type: 'error' | 'info', text }

  function load() {
    setLoading(true);
    setLoadError(null);
    api.listEnvVars()
      .then(({ vars }) => {
        const cloud = vars.filter((v) => CLOUD_KEYS.includes(v.key));
        setEnvVars(cloud);
        const byKey = Object.fromEntries(cloud.map((v) => [v.key, v]));
        const currentBaseUrl = byKey.OPENAI_BASE_URL?.hint || '';
        const matched = CLOUD_PRESETS.find((p) => p.baseUrl && p.baseUrl === currentBaseUrl);
        const preset = matched || (currentBaseUrl ? CLOUD_PRESETS.find((p) => p.id === 'custom') : CLOUD_PRESETS.find((p) => p.id === 'openai'));
        setPresetId(preset.id);
        setLabel(byKey.OPENAI_PROVIDER_LABEL?.hint || preset.label);
        setBaseUrl(currentBaseUrl || preset.baseUrl);
        setModel(byKey.OPENAI_MODEL?.hint || '');
        setApiKeyConfigured(Boolean(byKey.OPENAI_API_KEY?.configured));
        setApiKeyHint(byKey.OPENAI_API_KEY?.hint ?? null);
      })
      .catch((err) => setLoadError(err.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  function handlePresetChange(id) {
    setPresetId(id);
    const preset = CLOUD_PRESETS.find((p) => p.id === id);
    setLabel(preset.label);
    setBaseUrl(preset.baseUrl);
    setModel('');
    setMessage(null);
  }

  async function handleSave() {
    setMessage(null);
    if (!model.trim()) {
      setMessage({ type: 'error', text: 'Model is required.' });
      return;
    }
    if (!apiKey.trim() && !apiKeyConfigured) {
      setMessage({ type: 'error', text: 'An API key is required the first time you configure this provider.' });
      return;
    }

    setSaving(true);
    try {
      await api.updateEnvVar('OPENAI_PROVIDER_LABEL', label);
      await api.updateEnvVar('OPENAI_BASE_URL', baseUrl);
      await api.updateEnvVar('OPENAI_MODEL', model);
      if (apiKey.trim()) {
        await api.updateEnvVar('OPENAI_API_KEY', apiKey);
        setApiKeyConfigured(true);
        setApiKeyHint(maskHint(apiKey));
        setApiKey('');
      }
      await api.updateEnvVar('LLM_PROVIDER', 'openai_compatible');
      setMessage({ type: 'info', text: 'Saved — restart chat-agent to start using this provider.' });
      onActivated?.();
    } catch (err) {
      setMessage({ type: 'error', text: `Save failed: ${err.message}` });
    } finally {
      setSaving(false);
    }
  }

  const isActive = config?.provider === 'openai_compatible';
  const activePresetId = isActive
    ? (CLOUD_PRESETS.find((p) => p.baseUrl && p.baseUrl === config.openai.base_url)?.id ?? 'custom')
    : null;
  const isCustom = presetId === 'custom';

  if (loadError) {
    return (
      <div>
        <p className="no-data">Failed to load: {loadError}</p>
        <button className="btn btn-secondary btn-sm mt-8" onClick={load} disabled={loading}>
          {loading ? <><IconLoader className="spin" /> Retrying...</> : <><IconRefresh /> Retry</>}
        </button>
      </div>
    );
  }
  if (!envVars) return <p className="no-data">Loading...</p>;

  return (
    <div>
      <div className="wizard-step-desc">
        OpenAI, Grok, Groq, and DeepSeek all speak the same API shape chat-agent already calls;
        Claude works the same way via Anthropic's own OpenAI-compatible endpoint. Pick one to
        pre-fill its details, then add your model and API key.
      </div>

      <div className="llm-provider-chips">
        {CLOUD_PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            className={`tab ${presetId === p.id ? 'active' : ''}`}
            onClick={() => handlePresetChange(p.id)}
            data-testid={`settings-llm-preset-${p.id}`}
          >
            {p.label}
            {activePresetId === p.id && <span className="wizard-status-badge active ml-4">Active</span>}
          </button>
        ))}
      </div>

      <label className="field-label">Base URL</label>
      <input
        className="input"
        value={baseUrl}
        onChange={(e) => setBaseUrl(e.target.value)}
        disabled={!isCustom}
        placeholder="https://api.example.com/v1"
      />

      <label className="field-label mt-12">Model</label>
      <input
        className="input"
        value={model}
        onChange={(e) => setModel(e.target.value)}
        placeholder={CLOUD_PRESETS.find((p) => p.id === presetId)?.modelPlaceholder}
      />

      <label className="field-label mt-12">API key</label>
      <input
        className="input"
        type="password"
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
        placeholder="Leave blank to keep current key"
      />
      <div className="wizard-status-detail mt-8">
        {apiKeyConfigured ? `Configured — ${apiKeyHint}` : 'Not set'}
      </div>

      {message && (
        <div className={`wizard-status-detail mt-8 ${message.type === 'error' ? 'error' : ''}`}>
          {message.type === 'error' ? <IconAlert /> : <IconCheck />} {message.text}
        </div>
      )}

      <button className="btn btn-primary mt-12" onClick={handleSave} disabled={saving}>
        {saving ? <><IconLoader className="spin" /> Saving...</> : 'Save & use this provider'}
      </button>
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
      {tab === 'cloud' && <CloudPanel config={config} onActivated={loadConfig} />}
    </div>
  );
}
