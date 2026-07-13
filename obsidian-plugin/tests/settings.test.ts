import { DEFAULT_SETTINGS, mergeSettings } from "../src/settings";

describe("mergeSettings", () => {
  it("returns defaults when nothing was saved", () => {
    expect(mergeSettings(null)).toEqual(DEFAULT_SETTINGS);
    expect(mergeSettings(undefined)).toEqual(DEFAULT_SETTINGS);
  });

  it("keeps valid saved values", () => {
    const result = mergeSettings({ agentUrl: "http://localhost:9999", syncIntervalMinutes: 30 });
    expect(result.agentUrl).toBe("http://localhost:9999");
    expect(result.syncIntervalMinutes).toBe(30);
  });

  it("falls back to the default sync interval when the saved value is invalid", () => {
    expect(mergeSettings({ syncIntervalMinutes: 0 }).syncIntervalMinutes).toBe(
      DEFAULT_SETTINGS.syncIntervalMinutes
    );
    expect(mergeSettings({ syncIntervalMinutes: -5 }).syncIntervalMinutes).toBe(
      DEFAULT_SETTINGS.syncIntervalMinutes
    );
    expect(mergeSettings({ syncIntervalMinutes: Number.NaN }).syncIntervalMinutes).toBe(
      DEFAULT_SETTINGS.syncIntervalMinutes
    );
  });

  it("falls back to the default agent URL when the saved value is blank", () => {
    expect(mergeSettings({ agentUrl: "" }).agentUrl).toBe(DEFAULT_SETTINGS.agentUrl);
    expect(mergeSettings({ agentUrl: "   " }).agentUrl).toBe(DEFAULT_SETTINGS.agentUrl);
  });

  it("preserves the autoSyncEnabled flag", () => {
    expect(mergeSettings({ autoSyncEnabled: true }).autoSyncEnabled).toBe(true);
    expect(mergeSettings({ autoSyncEnabled: false }).autoSyncEnabled).toBe(false);
  });
});
