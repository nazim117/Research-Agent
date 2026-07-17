import ModelPickerRow from '../../shared/ModelPickerRow.jsx';

const CHAT_MODELS = ['llama3', 'mistral'];
const EMBED_MODELS = ['nomic-embed-text'];

export default function ModelsStep({ models, onModelsChange }) {
  function handleInstalled(kind, model) {
    onModelsChange({ ...models, [kind]: model });
  }

  return (
    <div>
      <div className="wizard-step-title">Choose your models</div>
      <div className="wizard-step-desc">
        Research Agent runs models locally through Ollama. Pull one chat model and one embedding
        model to continue.
      </div>
      <ModelPickerRow
        kind="chat"
        label="Chat model"
        options={CHAT_MODELS}
        selectedModel={models.chat}
        onInstalled={handleInstalled}
      />
      <ModelPickerRow
        kind="embed"
        label="Embedding model"
        options={EMBED_MODELS}
        selectedModel={models.embed}
        onInstalled={handleInstalled}
      />
    </div>
  );
}
