import { useEffect, useState } from 'react';
import * as api from '../api.js';

export function useHealthChecks(health, onHealthChange) {
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState(null);

  async function runCheck() {
    setChecking(true);
    setCheckError(null);
    try {
      const result = await api.checkSystemHealth();
      onHealthChange(result);
    } catch (err) {
      setCheckError(err.message || 'Health check failed.');
    } finally {
      setChecking(false);
    }
  }

  useEffect(() => {
    if (!health) runCheck();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { checking, checkError, runCheck };
}
