import ModelPickerRow from '../../shared/ModelPickerRow.jsx';

const CHAT_MODELS = ['llama3', 'mistral'];

export default function ModelsStep({ models, onModelsChange }) {
  function handleInstalled(kind, model) {
    onModelsChange({ ...models, [kind]: model });
  }

  return (
    <div>
      <div className="wizard-step-title">Choose your chat model</div>
      <div className="wizard-step-desc">
        Research Agent runs chat locally through Ollama by default (you can switch to a cloud
        provider later in Settings). Pull one chat model to continue — embeddings are handled by
        a separate bundled service with nothing to configure here; the previous Health Check step
        already confirmed it's running.
      </div>
      <ModelPickerRow
        kind="chat"
        label="Chat model"
        options={CHAT_MODELS}
        selectedModel={models.chat}
        onInstalled={handleInstalled}
      />
    </div>
  );
}
