// e2e/projects.spec.js — project create / select / delete flows.
//
// All /api calls are intercepted by mocks.js.  No real backend needed.

import { test, expect } from '@playwright/test';
import { mockApi, FIXTURES } from './mocks.js';

test.describe('project management', () => {

  test('shows existing project in selector on load', async ({ page }) => {
    await mockApi(page);
    await page.goto('/');

    // The project-select should contain the mocked project name.
    const select = page.getByTestId('project-select');
    await expect(select).toBeVisible();
    await expect(select).toContainText(FIXTURES.project.name);
  });

  test('empty state: chat pane shows placeholder when no project selected', async ({ page }) => {
    // Return empty project list so nothing is auto-selected.
    await mockApi(page, { projects: [] });
    await page.goto('/');

    await expect(page.getByText('Select or create a project to start chatting.')).toBeVisible();
  });

  test('create a new project', async ({ page }) => {
    // Start with no projects so the "no projects" sentinel is shown.
    await mockApi(page, {
      projects: [],
      // After create, return the new project in the list.
      createProject: { id: 'new-1', name: 'My New Project', external_refs: {}, created_at: '2026-01-01T00:00:00' },
    });

    // Override GET /projects response after the create call to include the new project.
    let projectsCallCount = 0;
    await page.route('**/api/projects', async (route) => {
      if (route.request().method() === 'GET') {
        projectsCallCount++;
        // First call: empty; subsequent: include new project.
        const list = projectsCallCount === 1
          ? []
          : [{ id: 'new-1', name: 'My New Project', external_refs: {}, created_at: '2026-01-01T00:00:00' }];
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(list) });
      }
      route.continue();
    });

    await page.goto('/');

    // Choose "+ New project" from the select.
    const select = page.getByTestId('project-select');
    await select.selectOption('__new__');

    // Name input should appear.
    const nameInput = page.getByTestId('new-project-name');
    await expect(nameInput).toBeVisible();

    await nameInput.fill('My New Project');
    await page.getByTestId('create-project').click();

    // After creation, project should appear in the selector.
    await expect(select).toContainText('My New Project');
  });

  test('delete active project removes it from selector', async ({ page }) => {
    await mockApi(page, {
      deleteProject: {},
    });

    // After delete, list will be empty.
    let deleteHappened = false;
    await page.route('**/api/projects', async (route) => {
      if (route.request().method() === 'GET') {
        const list = deleteHappened ? [] : [FIXTURES.project];
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(list) });
      }
      route.continue();
    });
    await page.route(`**/api/projects/${FIXTURES.project.id}`, async (route) => {
      if (route.request().method() === 'DELETE') {
        deleteHappened = true;
        return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
      }
      route.continue();
    });

    await page.goto('/');

    // Auto-confirm the delete dialog.
    page.on('dialog', dialog => dialog.accept());

    const select = page.getByTestId('project-select');
    // Select the project first — __delete__ only renders when activeId is set.
    await select.selectOption(FIXTURES.project.id);
    await select.selectOption('__delete__');

    // The empty-state placeholder should appear after deletion.
    await expect(page.getByText('Select or create a project to start chatting.')).toBeVisible();
  });

});
