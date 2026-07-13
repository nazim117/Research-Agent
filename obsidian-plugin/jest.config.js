/** @type {import('jest').Config} */
module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
  testMatch: ["<rootDir>/tests/**/*.test.ts"],
  // main.ts and settingsTab.ts touch the live Obsidian runtime API, which has
  // no implementation outside the Obsidian app itself — they need mocked
  // Obsidian globals to unit test, which is a dedicated later piece of work.
  // Coverage here is scoped to the framework-independent modules.
  collectCoverageFrom: ["src/**/*.ts", "!src/main.ts", "!src/settingsTab.ts"],
};
