import { App, PluginSettingTab, Setting } from "obsidian";
import type ProjectBrainPlugin from "./main";
import { DEFAULT_SETTINGS } from "./settings";

export class ProjectBrainSettingTab extends PluginSettingTab {
  plugin: ProjectBrainPlugin;

  constructor(app: App, plugin: ProjectBrainPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    containerEl.createEl("h2", { text: "Project Brain settings" });

    new Setting(containerEl)
      .setName("Agent URL")
      .setDesc("Base URL of the running chat-agent service.")
      .addText((text) =>
        text
          .setPlaceholder(DEFAULT_SETTINGS.agentUrl)
          .setValue(this.plugin.settings.agentUrl)
          .onChange(async (value) => {
            this.plugin.settings.agentUrl = value.trim() || DEFAULT_SETTINGS.agentUrl;
            await this.plugin.saveSettings();
          })
      );

    new Setting(containerEl)
      .setName("Auto sync")
      .setDesc("Automatically sync this vault with Project Brain on a schedule.")
      .addToggle((toggle) =>
        toggle.setValue(this.plugin.settings.autoSyncEnabled).onChange(async (value) => {
          this.plugin.settings.autoSyncEnabled = value;
          await this.plugin.saveSettings();
        })
      );

    new Setting(containerEl)
      .setName("Sync interval (minutes)")
      .setDesc("How often to sync automatically, in minutes.")
      .addText((text) =>
        text
          .setPlaceholder(String(DEFAULT_SETTINGS.syncIntervalMinutes))
          .setValue(String(this.plugin.settings.syncIntervalMinutes))
          .onChange(async (value) => {
            const parsed = Number.parseInt(value, 10);
            this.plugin.settings.syncIntervalMinutes =
              Number.isFinite(parsed) && parsed > 0
                ? parsed
                : DEFAULT_SETTINGS.syncIntervalMinutes;
            await this.plugin.saveSettings();
          })
      );
  }
}
