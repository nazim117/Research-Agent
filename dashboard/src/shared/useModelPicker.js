import { useEffect, useState } from 'react';
import * as api from '../api.js';

export function useModelPicker(kind, options, selectedModel, onInstalled) {
  const [model, setModel] = useState(selectedModel || options[0]);
  const [installedModels, setInstalledModels] = useState([]);
  const [pulling, setPulling] = useState(false);
  const [progress, setProgress] = useState(null);

  useEffect(() => {
    api.listLocalModels().then((r) => setInstalledModels(r.installed)).catch(() => setInstalledModels([]));
  }, []);

  async function handlePull() {
    setPulling(true);
    setProgress({ downloaded: 0, total: 1 });
    try {
      await api.pullModel(model, (p) => setProgress(p));
      onInstalled(kind, model);
    } finally {
      setPulling(false);
    }
  }

  function handleUseExisting() {
    onInstalled(kind, model);
  }

  return { model, setModel, installedModels, pulling, progress, handlePull, handleUseExisting };
}
