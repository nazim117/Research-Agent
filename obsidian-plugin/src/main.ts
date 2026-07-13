import { Plugin } from "obsidian";
import { Logger } from "./logger";
import { mergeSettings, type ProjectBrainSettings } from "./settings";
import { ProjectBrainSettingTab } from "./settingsTab";

export default class ProjectBrainPlugin extends Plugin {
  settings: ProjectBrainSettings = mergeSettings(null);
  logger: Logger = new Logger("Project Brain");

  async onload(): Promise<void> {
    this.logger.info("Loading plugin");

    try {
      await this.loadSettings();
    } catch (err) {
      // A corrupted or unreadable data.json should never stop the plugin
      // from loading — fall back to defaults and let the user re-save.
      this.logger.error("Failed to load settings, falling back to defaults", err);
      this.settings = mergeSettings(null);
    }

    this.addSettingTab(new ProjectBrainSettingTab(this.app, this));

    this.addCommand({
      id: "project-brain-sync-now",
      name: "Sync now",
      callback: () => {
        // The actual sync engine lands in a later piece of work — for now
        // the command exists so the palette entry and settings UI can be
        // exercised end-to-end.
        this.logger.warn("Sync is not implemented yet.");
      },
    });

    this.logger.info("Plugin loaded");
  }

  onunload(): void {
    this.logger.info("Unloading plugin");
  }

  async loadSettings(): Promise<void> {
    const data = (await this.loadData()) as Partial<ProjectBrainSettings> | null;
    this.settings = mergeSettings(data);
  }

  async saveSettings(): Promise<void> {
    try {
      await this.saveData(this.settings);
    } catch (err) {
      this.logger.error("Failed to save settings", err);
      throw err;
    }
  }
}
