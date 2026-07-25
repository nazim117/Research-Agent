import { useEffect, useState } from 'react';
import * as api from '../api.js';

export function useModelPicker(kind, options, selectedModel, onInstalled) {
  const [model, setModel] = useState(selectedModel || options[0]);
  const [installedModels, setInstalledModels] = useState([]);
  const [pulling, setPulling] = useState(false);
  const [progress, setProgress] = useState(null);
  const [pullError, setPullError] = useState(null);

  useEffect(() => {
    api.listLocalModels().then((r) => setInstalledModels(r.installed)).catch(() => setInstalledModels([]));
  }, []);

  async function handlePull() {
    setPulling(true);
    setPullError(null);
    setProgress({ downloaded: 0, total: 1 });
    try {
      await api.pullModel(model, (p) => setProgress(p));
      onInstalled(kind, model);
    } catch (err) {
      setPullError(err.message || 'Pull failed.');
    } finally {
      setPulling(false);
    }
  }

  function handleUseExisting() {
    onInstalled(kind, model);
  }

  return { model, setModel, installedModels, pulling, progress, pullError, handlePull, handleUseExisting };
}
