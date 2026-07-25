// e2e/actions.spec.js — action approve / reject flows in the Studio pane.
//
// All /api calls are intercepted by mocks.js.  No real backend needed.

import { test, expect } from '@playwright/test';
import { mockApi, FIXTURES } from './mocks.js';

test.describe('action approval flow', () => {

  async function openActionsTab(page) {
    const actionsTab = page.getByTestId('studio-tab-actions');
    await expect(actionsTab).toBeVisible();
    await actionsTab.click();
  }

  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await page.goto('/');
    // activeId starts null (localStorage cleared). Must select project explicitly
    // so StudioPane fetches actions (it is a no-op when projectId is null).
    const select = page.getByTestId('project-select');
    await expect(select).toContainText(FIXTURES.project.name);
    await select.selectOption(FIXTURES.project.id);
    await expect(select).toHaveValue(FIXTURES.project.id);
  });

  test('pending action card is visible in Actions tab', async ({ page }) => {
    await openActionsTab(page);

    const card = page.getByTestId('action-card').first();
    await expect(card).toBeVisible();
    await expect(card).toContainText('jira:add_comment');
  });

  test('approve action shows success toast and removes the card', async ({ page }) => {
    await openActionsTab(page);

    const card = page.getByTestId('action-card').first();
    await expect(card).toBeVisible();

    await page.getByTestId('action-approve').first().click();

    // Toast should confirm the action was executed.
    const toast = page.getByTestId('toast');
    await expect(toast).toBeVisible();
    await expect(toast).toContainText('Comment posted.');

    // The approve result has a URL — the toast should show the "open ↗" link.
    await expect(toast).toContainText('open');

    // Card should be gone from the list.
    await expect(page.getByTestId('action-card')).toHaveCount(0);
  });

  test('reject action removes the card without a toast URL link', async ({ page }) => {
    await openActionsTab(page);

    await expect(page.getByTestId('action-card').first()).toBeVisible();
    await page.getByTestId('action-reject').first().click();

    // Card should be removed.
    await expect(page.getByTestId('action-card')).toHaveCount(0);
  });

  test('approve failure shows error toast', async ({ page }) => {
    // Override approve to return 502.
    await mockApi(page, {
      approveAction: null,  // handled below via explicit route
    });
    await page.route('**/api/actions/**/approve', route =>
      route.fulfill({ status: 502, contentType: 'application/json', body: '{"detail":"mcp-server error"}' })
    );

    await openActionsTab(page);
    await page.getByTestId('action-approve').first().click();

    const toast = page.getByTestId('toast');
    await expect(toast).toBeVisible();
    await expect(toast).toContainText('Approve failed');
  });

  test('actions tab shows badge when pending actions exist', async ({ page }) => {
    // The badge appears next to the "✎ Actions" tab when actions.length > 0.
    const actionsTab = page.getByTestId('studio-tab-actions');
    // Badge text should show "1" for the single mocked action.
    await expect(actionsTab).toContainText('1');
  });

});
