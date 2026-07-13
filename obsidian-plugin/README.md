# Project Brain — Obsidian Plugin

Bidirectional sync between your Obsidian vault and [Project Brain](../README.md)'s
AI-powered project memory. This plugin is developed and installed locally —
it is not published to the Obsidian community plugin directory.

## Status

This is the initial plugin scaffold (framework, settings UI, logging,
command palette registration). The sync engine itself is not implemented
yet — the "Sync now" command currently just logs a warning.

## Development setup

```bash
cd obsidian-plugin
npm install
npm run dev     # esbuild in watch mode, rebuilds main.js on save
```

To load the plugin in Obsidian, copy or symlink this folder (containing
`manifest.json` and the built `main.js`) into your vault's
`.obsidian/plugins/project-brain/` directory, then enable it under
Settings → Community plugins.

## Commands

```bash
npm run build    # type-check + production build (main.js, no source maps)
npm test         # run the Jest unit test suite
```

## Project layout

```
src/
  main.ts         Plugin entry point (onload/onunload, command registration)
  settings.ts     Settings shape + pure default/merge logic (no Obsidian dependency)
  settingsTab.ts  Settings UI (PluginSettingTab) — depends on the Obsidian runtime
  logger.ts       Prefixed console logger shared across the plugin
tests/            Jest unit tests for the framework-independent modules
```

`settings.ts` and `logger.ts` have no dependency on the Obsidian runtime, so
they're unit tested directly. `main.ts` and `settingsTab.ts` call real
Obsidian APIs (`Plugin`, `PluginSettingTab`, `Setting`, `this.app`) that only
exist inside the Obsidian app itself; testing those needs mocked Obsidian
globals, which is separate follow-up work.
