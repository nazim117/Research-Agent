// e2e/mocks.js — shared Playwright API mock layer.
//
// Why mock the backend?
//   The e2e tests drive the React/Vite dashboard UI (http://localhost:5173).
//   The real chat-agent backend (port 8080) depends on Qdrant, Ollama, and
//   an LLM — spinning all of that up for every UI test is slow and fragile.
//   Instead, Playwright's page.route() intercepts every /api/** request in
//   the browser and returns canned JSON.  This tests the UI logic (state
//   management, rendering, citation display, toast wiring, action approvals)
//   without the backend running.
//
// Usage in a spec:
//   import { mockApi, FIXTURES } from './mocks.js';
//   test.beforeEach(async ({ page }) => {
//     await mockApi(page);
//   });
//   // Override one endpoint for a specific test:
//   await mockApi(page, {
//     chat: { reply: 'custom reply', citations: [] },
//   });

// ─── Shared test fixtures ─────────────────────────────────────────────────────

export const FIXTURES = {
  project: { id: 'proj-1', name: 'Test Project', external_refs: {}, created_at: '2026-01-01T00:00:00' },
  project2: { id: 'proj-2', name: 'Another Project', external_refs: {}, created_at: '2026-01-02T00:00:00' },

  chatReply: {
    reply: 'Based on the documentation, the answer is yes.',
    citations: [{ ref: 1, source: 'jira:KAN-1', chunk_index: 0 }],
    session_id: 'default',
  },

  chatReplyNoCitations: {
    reply: 'I have no relevant sources for that question.',
    citations: [],
    session_id: 'default',
  },

  sources: [{ source: 'https://example.com/doc', chunks: 4 }],

  ingestUrl: { chunks: 4, source: 'https://example.com/doc' },

  action: {
    id: 'act-1',
    action_type: 'jira:add_comment',
    status: 'pending',
    payload: { item_id: 'KAN-1', body: 'Deploy to staging?', ref_key: 'jira_project_key' },
    created_at: '2026-01-01T00:00:00',
  },

  approveResult: { id: 'act-1', status: 'executed', result: { url: 'https://jira.example.com/browse/KAN-1?focusedCommentId=1' } },

  briefing: {
    summary: 'Project is on track.',
    open_actions: [],
    recent_decisions: [],
    active_risks: [],
    generated_at: '2026-01-01T00:00:00',
  },

  standup: {
    summary: 'On track.',
    done: ['Deployed v1'],
    today: ['Fix tests'],
    blockers: [],
    generated_at: '2026-01-01T00:00:00',
  },
};

// ─── Route dispatcher ─────────────────────────────────────────────────────────

/**
 * Register Playwright route intercepts for all /api/** calls.
 *
 * @param {import('@playwright/test').Page} page
 * @param {Object} overrides  Per-endpoint response overrides keyed by name:
 *   { projects, chat, sources, ingestUrl, actions, approveAction, rejectAction,
 *     decisions, risks, briefing, standup }
 *   Each value replaces the entire default response body for that endpoint.
 */
export async function mockApi(page, overrides = {}) {
  // Clear localStorage before each test so project selection doesn't bleed.
  await page.addInitScript(() => { localStorage.clear(); });

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    // Strip the /api prefix that the Vite proxy adds.
    const path = url.pathname.replace(/^\/api/, '');

    const respond = (body, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

    // ── Projects ─────────────────────────────────────────────────────────────
    if (method === 'GET' && path === '/projects') {
      return respond(overrides.projects ?? [FIXTURES.project]);
    }
    if (method === 'POST' && path === '/projects') {
      // Echo back a project shaped from the request body.
      const body = route.request().postDataJSON() ?? {};
      return respond(overrides.createProject ?? { ...FIXTURES.project, name: body.name || 'New Project' });
    }
    if (method === 'PATCH' && /^\/projects\/[^/]+$/.test(path)) {
      return respond(overrides.patchProject ?? FIXTURES.project);
    }
    if (method === 'DELETE' && /^\/projects\/[^/]+$/.test(path)) {
      return respond(overrides.deleteProject ?? {});
    }

    // ── Chat ─────────────────────────────────────────────────────────────────
    if (method === 'POST' && path === '/chat') {
      return respond(overrides.chat ?? FIXTURES.chatReply);
    }

    // ── Sources / ingest ─────────────────────────────────────────────────────
    if (method === 'GET' && /\/sources$/.test(path)) {
      return respond(overrides.sources ?? FIXTURES.sources);
    }
    if (method === 'POST' && /\/ingest\/url$/.test(path)) {
      return respond(overrides.ingestUrl ?? FIXTURES.ingestUrl);
    }
    if (method === 'POST' && /\/ingest\/file$/.test(path)) {
      return respond(overrides.ingestFile ?? FIXTURES.ingestUrl);
    }
    if (method === 'POST' && /\/ingest\/transcript$/.test(path)) {
      return respond(overrides.ingestTranscript ?? { chunks: 2, decisions: 1, action_items: 0, risks: 0 });
    }
    if (method === 'POST' && path === '/ingest') {
      return respond(overrides.ingest ?? FIXTURES.ingestUrl);
    }

    // ── Actions ──────────────────────────────────────────────────────────────
    if (method === 'GET' && /\/actions$/.test(path)) {
      return respond(overrides.actions ?? [FIXTURES.action]);
    }
    if (method === 'POST' && /\/actions$/.test(path)) {
      return respond(overrides.proposeAction ?? FIXTURES.action);
    }
    if (method === 'POST' && /\/actions\/[^/]+\/approve$/.test(path)) {
      return respond(overrides.approveAction ?? FIXTURES.approveResult);
    }
    if (method === 'POST' && /\/actions\/[^/]+\/reject$/.test(path)) {
      return respond(overrides.rejectAction ?? { id: 'act-1', status: 'rejected' });
    }

    // ── Studio data ───────────────────────────────────────────────────────────
    if (method === 'GET' && /\/decisions$/.test(path)) {
      return respond(overrides.decisions ?? []);
    }
    if (method === 'GET' && /\/risks$/.test(path)) {
      return respond(overrides.risks ?? []);
    }
    if (method === 'GET' && /\/action-items$/.test(path)) {
      return respond(overrides.actionItems ?? []);
    }
    if (method === 'GET' && /\/briefing$/.test(path)) {
      return respond(overrides.briefing ?? FIXTURES.briefing);
    }
    if (method === 'GET' && /\/standup$/.test(path)) {
      return respond(overrides.standup ?? FIXTURES.standup);
    }
    if (method === 'GET' && /\/sync$/.test(path)) {
      return respond(overrides.sync ?? []);
    }
    if (method === 'POST' && /\/sync$/.test(path)) {
      return respond(overrides.syncPost ?? { synced: 0 });
    }
    if (method === 'GET' && path === '/memory/search') {
      return respond(overrides.memorySearch ?? { results: [] });
    }

    // Fall-through: unexpected calls get a clear 404.
    console.warn(`[mock] unhandled: ${method} ${path}`);
    return route.fulfill({ status: 404, body: '{"detail":"not mocked"}' });
  });
}
