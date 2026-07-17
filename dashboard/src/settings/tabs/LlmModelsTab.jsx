import { useEffect, useState } from 'react';
import * as api from '../../api.js';
import { IconCheck, IconAlert, IconLoader } from '../../icons.jsx';
import ModelPickerRow from '../../shared/ModelPickerRow.jsx';

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

export default function LlmModelsTab() {
  const [config, setConfig] = useState(null);
  const [chatModel, setChatModel] = useState(null);

  useEffect(() => {
    api.getLlmConfig().then((c) => {
      setConfig(c);
      setChatModel(c.ollama.chatModel);
    });
  }, []);

  return (
    <div className="settings-section">
      <div className="wizard-step-desc">
        Research Agent's chat model is configured on the backend and can't be changed from here
        yet — edit <code>LLM_PROVIDER</code> / <code>OLLAMA_CHAT_MODEL</code> (or the
        <code> OPENAI_*</code> fields) in your <code>.env</code> file and restart chat-agent to
        change it. You can still pull additional models below so they're ready locally.
      </div>

      {config && (
        <div className="wizard-status-row">
          <div className="wizard-status-main">
            <div className="wizard-status-name">Provider: {config.provider === 'ollama' ? 'Ollama' : 'OpenAI-compatible'}</div>
            <div className="wizard-status-detail">
              {config.provider === 'ollama'
                ? `Active model: ${config.ollama.chatModel}`
                : `${config.openai.model || '(not set)'} at ${config.openai.baseUrl || '(not set)'}`}
            </div>
          </div>
        </div>
      )}

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
