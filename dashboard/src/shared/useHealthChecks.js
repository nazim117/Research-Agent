import { useEffect, useState } from 'react';
import * as api from '../api.js';

export function useHealthChecks(health, onHealthChange) {
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState(null);
  const [fixState, setFixState] = useState({}); // { [service]: { fixing, log, failed } }

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

  async function handleFix(service) {
    setFixState((s) => ({ ...s, [service]: { fixing: true, log: [], failed: false } }));
    try {
      const result = await api.fixService(service);
      setFixState((s) => ({ ...s, [service]: { fixing: false, log: result.log, failed: !result.success } }));
      if (result.success) {
        const updated = await api.checkSystemHealth();
        onHealthChange(updated);
      }
    } catch {
      setFixState((s) => ({ ...s, [service]: { fixing: false, log: [], failed: true } }));
    }
  }

  return { checking, checkError, fixState, runCheck, handleFix };
}
