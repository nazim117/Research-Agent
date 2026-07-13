export interface ProjectBrainSettings {
  agentUrl: string;
  syncIntervalMinutes: number;
  autoSyncEnabled: boolean;
}

export const DEFAULT_SETTINGS: ProjectBrainSettings = {
  agentUrl: "http://localhost:8080",
  syncIntervalMinutes: 15,
  autoSyncEnabled: false,
};

/**
 * Merge persisted settings (which may be partial, stale, or corrupted) with
 * defaults, discarding any saved value that fails basic validation instead
 * of letting a bad on-disk file crash plugin load.
 */
export function mergeSettings(
  saved: Partial<ProjectBrainSettings> | null | undefined
): ProjectBrainSettings {
  const merged: ProjectBrainSettings = { ...DEFAULT_SETTINGS, ...(saved ?? {}) };

  if (!Number.isFinite(merged.syncIntervalMinutes) || merged.syncIntervalMinutes <= 0) {
    merged.syncIntervalMinutes = DEFAULT_SETTINGS.syncIntervalMinutes;
  }

  if (!merged.agentUrl || !merged.agentUrl.trim()) {
    merged.agentUrl = DEFAULT_SETTINGS.agentUrl;
  }

  return merged;
}
