// e2e/ingest.spec.js — URL ingest flow and sources list update.
//
// All /api calls are intercepted by mocks.js.  No real backend needed.

import { test, expect } from '@playwright/test';
import { mockApi, FIXTURES } from './mocks.js';

test.describe('ingest flow', () => {

  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await page.goto('/');
    // activeId starts null (localStorage cleared). Must select project explicitly
    // so LeftPane renders DropZone (it returns an empty aside when projectId is null).
    const select = page.getByTestId('project-select');
    await expect(select).toContainText(FIXTURES.project.name);
    await select.selectOption(FIXTURES.project.id);
    await expect(page.getByTestId('url-input')).toBeVisible();
  });

  test('ingest a URL shows success toast', async ({ page }) => {
    const urlInput = page.getByTestId('url-input');
    await urlInput.fill('https://example.com/some-page');
    await page.getByTestId('url-add').click();

    // Success toast should appear with chunk count.
    const toast = page.getByTestId('toast');
    await expect(toast).toBeVisible();
    await expect(toast).toContainText('chunks');
  });

  test('sources list updates after URL ingest', async ({ page }) => {
    // Initially one source from the mock; after ingest the list has the same one
    // (the mock just returns the same list on the next GET /sources).
    const sourcesCount = page.getByTestId('sources-count');
    await expect(sourcesCount).toContainText('Sources (1)');

    await page.getByTestId('url-input').fill('https://example.com/another');
    await page.getByTestId('url-add').click();

    // Wait for the toast to confirm the ingest completed.
    await expect(page.getByTestId('toast')).toBeVisible();

    // Source items should be present.
    const items = page.getByTestId('source-item');
    await expect(items.first()).toBeVisible();
    await expect(items.first()).toContainText('example.com');
  });

  test('ingest an empty URL is rejected (add button stays disabled)', async ({ page }) => {
    const addBtn = page.getByTestId('url-add');
    // With no URL typed the button is disabled.
    await expect(addBtn).toBeDisabled();

    await page.getByTestId('url-input').fill('  ');
    // Whitespace-only input also keeps button disabled.
    await expect(addBtn).toBeDisabled();
  });

});
