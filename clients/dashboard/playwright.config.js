// playwright.config.js — Playwright end-to-end test configuration.
//
// Tests live in clients/dashboard/e2e/ and drive the React/Vite dashboard at
// http://localhost:5173. All /api calls are intercepted by Playwright's
// route handler (see e2e/mocks.js) and fulfilled with canned JSON —
// the real chat-agent backend does NOT need to be running.
//
// Run:
//   npm run test:e2e           (headless, CI)
//   npm run test:e2e:ui        (interactive debug mode)
//   npx playwright test --headed  (visible browser)

import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',

  // Fail the suite if any test leaves a dangling assertion.
  fullyParallel: true,

  // Retry once on CI to absorb flaky timing issues.
  retries: process.env.CI ? 1 : 0,

  // One worker: tests share a single browser process so route mocks are
  // never set up in a different context than the page that fires the request.
  // The suite is small (~17 tests, ~30 s) so parallelism is not worth the
  // added complexity.
  workers: 1,

  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
  ],

  // Per-test ceiling: 4 min on Windows (cold Vite + Chromium startup can be slow).
  timeout: 600_000,

  // Default timeout for expect() assertions (toBeVisible, toContainText, etc.).
  // Overrides the Playwright default of 5000ms.
  expect: { timeout: 600_000 },

  use: {
    // Dashboard dev server URL.
    baseURL: 'http://localhost:5173',

    // Navigation gets its own generous timeout — separate from the test timeout.
    navigationTimeout: 600_000,

    // Timeout for individual actions (click, fill, etc.).
    actionTimeout: 600_000,

    // Capture screenshot and trace on first failure for debugging.
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  // Playwright auto-starts the Vite dev server before running tests.
  // reuseExistingServer: true means a server already on :5173 is reused
  // in local dev (faster iteration). In CI, a fresh server is always started.
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    // Reuse a running dev server locally (fast iteration after cache clear).
    // CI always starts fresh (no pre-existing server).
    reuseExistingServer: !process.env.CI,
    // 60 s: Vite cold-start on Windows can be slow.
    timeout: 600_000,
  },
});
