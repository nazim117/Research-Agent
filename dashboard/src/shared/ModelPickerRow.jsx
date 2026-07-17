import { IconCheck, IconLoader } from '../icons.jsx';
import { useModelPicker } from './useModelPicker.js';

function formatBytes(n) {
  return `${(n / 1e6).toFixed(0)}MB`;
}

export default function ModelPickerRow({ kind, label, options, selectedModel, onInstalled }) {
  const { model, setModel, installedModels, pulling, progress, handlePull, handleUseExisting } =
    useModelPicker(kind, options, selectedModel, onInstalled);

  const alreadyInstalled = installedModels.includes(model);
  const isDone = selectedModel === model && (alreadyInstalled || progress?.downloaded === progress?.total);

  return (
    <div className="wizard-model-row" data-testid={`model-picker-${kind}`}>
      <div className="wizard-model-head">
        <span className="wizard-model-label">{label}</span>
        <select
          className="input wizard-model-select"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          disabled={pulling}
        >
          {options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        {alreadyInstalled ? (
          isDone ? (
            <button className="btn btn-secondary btn-sm" disabled>
              <IconCheck /> Installed
            </button>
          ) : (
            <button className="btn btn-secondary btn-sm" onClick={handleUseExisting}>
              Use existing
            </button>
          )
        ) : (
          <button className="btn btn-primary btn-sm" onClick={handlePull} disabled={pulling}>
            {pulling ? <><IconLoader className="spin" /> Pulling...</> : isDone ? <><IconCheck /> Installed</> : 'Pull'}
          </button>
        )}
      </div>
      {progress && pulling && (
        <>
          <div className="wizard-progress-track">
            <div
              className="wizard-progress-fill"
              style={{ width: `${(progress.downloaded / progress.total) * 100}%` }}
            />
          </div>
          <div className="wizard-progress-text">
            downloading... {formatBytes(progress.downloaded)} / {formatBytes(progress.total)}
          </div>
        </>
      )}
    </div>
  );
}
