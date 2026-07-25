// e2e/chat.spec.js — chat send, reply rendering, and citation display.
//
// All /api calls are intercepted by mocks.js.  No real backend needed.

import { test, expect } from '@playwright/test';
import { mockApi, FIXTURES } from './mocks.js';

test.describe('chat flow', () => {

  test.beforeEach(async ({ page }) => {
    await mockApi(page);
    await page.goto('/');
    // activeId starts null (localStorage cleared). Must select project explicitly
    // so ChatPane renders the input (it shows a placeholder when projectId is null).
    const select = page.getByTestId('project-select');
    await expect(select).toContainText(FIXTURES.project.name);
    await select.selectOption(FIXTURES.project.id);
    await expect(page.getByTestId('chat-input')).toBeVisible();
  });

  test('send a message and see the reply', async ({ page }) => {
    const input = page.getByTestId('chat-input');
    await input.fill('What is the status?');

    // Enter key sends the message (matches the onKeyDown handler in ChatPane).
    await input.press('Enter');

    // The assistant reply should appear.
    const message = page.getByTestId('chat-message').last();
    await expect(message).toContainText(FIXTURES.chatReply.reply);
  });

  test('send button triggers a message', async ({ page }) => {
    const input = page.getByTestId('chat-input');
    await input.fill('Tell me about KAN-1');
    await page.getByTestId('chat-send').click();

    await expect(page.getByTestId('chat-message').last()).toContainText(FIXTURES.chatReply.reply);
  });

  test('reply with citations shows sources panel', async ({ page }) => {
    // Default mock returns one citation: jira:KAN-1
    const input = page.getByTestId('chat-input');
    await input.fill('What did we decide about KAN-1?');
    await input.press('Enter');

    // Citations panel should be present.
    const citations = page.getByTestId('citations').last();
    await expect(citations).toBeVisible();
    await expect(citations).toContainText('Sources (1)');

    // Expand the panel.
    await citations.click();
    const item = page.getByTestId('citation-item').last();
    await expect(item).toContainText('[1] jira:KAN-1');
    await expect(item).toContainText('chunk 0');
  });

  test('reply with no citations shows no sources panel', async ({ page }) => {
    await mockApi(page, { chat: FIXTURES.chatReplyNoCitations });
    await page.goto('/');
    const select = page.getByTestId('project-select');
    await expect(select).toContainText(FIXTURES.project.name);
    await select.selectOption(FIXTURES.project.id);
    await expect(page.getByTestId('chat-input')).toBeVisible();

    const input = page.getByTestId('chat-input');
    await input.fill('Something unrelated');
    await input.press('Enter');

    await expect(page.getByTestId('chat-message').last()).toBeVisible();
    // No citations panel should exist on this message.
    const citationsCount = await page.getByTestId('citations').count();
    expect(citationsCount).toBe(0);
  });

  test('multiple messages accumulate in the thread', async ({ page }) => {
    const input = page.getByTestId('chat-input');

    await input.fill('First question');
    await input.press('Enter');
    await page.getByTestId('chat-message').last().waitFor();

    await input.fill('Second question');
    await input.press('Enter');
    // Wait for exactly 2 messages — `.last().waitFor()` would resolve immediately
    // because the first message is already visible.
    await expect(page.getByTestId('chat-message')).toHaveCount(2);

    // Two assistant messages should be present.
    const messages = await page.getByTestId('chat-message').all();
    expect(messages.length).toBe(2);
  });

});
