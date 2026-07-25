import { useState } from 'react';

function trySetItem(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // ignore — state still updates in memory
  }
}

function loadInitial(key, defaultValue) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? defaultValue : JSON.parse(raw);
  } catch {
    return defaultValue;
  }
}

export function useLocalStorageState(key, defaultValue) {
  const [value, setValueState] = useState(() => loadInitial(key, defaultValue));

  function setValue(update) {
    setValueState((prev) => {
      const next = typeof update === 'function' ? update(prev) : update;
      trySetItem(key, JSON.stringify(next));
      return next;
    });
  }

  return [value, setValue];
}
