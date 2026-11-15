import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import type { Page, Route } from '@playwright/test';

/**
 * TEST-ONLY fixture: the backend's own committed example result
 * (workspace/backend/FireHarmonics/docs/examples/analysis_result.synthetic.json), produced by the real engine
 * from synthetic detections. It is served through route interception to exercise the report UI while the
 * live backend has no NASA key. It is never bundled into the application.
 */
export function loadExampleResult(): Record<string, unknown> {
  const path = fileURLToPath(
    new URL('../../../backend/FireHarmonics/docs/examples/analysis_result.synthetic.json', import.meta.url),
  );
  const raw = JSON.parse(readFileSync(path, 'utf8')) as Record<string, unknown>;
  const result = ((raw.result as Record<string, unknown>) ?? raw) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  // The committed example predates views.normalized; add the block produced by the backend's own
  // normalized_view() for this example (fixtures/normalized.synthetic.json, generated, test-only).
  const extra = JSON.parse(readFileSync(fileURLToPath(new URL('./fixtures/normalized.synthetic.json', import.meta.url)), 'utf8'));
  result.satellite_analysis.views.normalized ??= extra.normalized;
  // unified_calendar / current_conditions produced by FireLens code (see fixtures/calendar.example-point.json _note).
  const cal = JSON.parse(readFileSync(fileURLToPath(new URL('./fixtures/calendar.example-point.json', import.meta.url)), 'utf8'));
  result.ml_analysis.unified_calendar ??= cal.unified_calendar;
  result.ml_analysis.current_conditions ??= cal.current_conditions;
  return result;
}

export const JOB_ID = 'e2e-synthetic-0001';

export interface MockState {
  posts: Record<string, unknown>[];
}

export async function mockCompletedAnalysis(page: Page): Promise<MockState> {
  const result = loadExampleResult();
  const state: MockState = { posts: [] };
  const job = {
    status_code: 202,
    job_id: JOB_ID,
    status: 'completed',
    progress: 100,
    stage: 'completed',
    eta_seconds: null,
    nasa_pause_seconds: null,
    cache_hit: true,
    attempt: 1,
    error: null,
    message: null,
    result_status: result.status,
    links: {
      status: `/api/v1/analyses/${JOB_ID}`,
      events: `/api/v1/analyses/${JOB_ID}/events`,
      result: `/api/v1/analyses/${JOB_ID}/result`,
      retry: `/api/v1/analyses/${JOB_ID}/retry`,
      export: `/api/v1/analyses/${JOB_ID}/export`,
    },
  };
  await page.route('**/api/v1/analyses', async (route: Route) => {
    if (route.request().method() !== 'POST') return route.continue();
    state.posts.push(route.request().postDataJSON() as Record<string, unknown>);
    return route.fulfill({ status: 202, json: job });
  });
  await page.route(`**/api/v1/analyses/${JOB_ID}/result`, (route) =>
    route.fulfill({ status: 200, json: { status_code: 200, job_id: JOB_ID, status: 'completed', result } }),
  );
  await page.route(`**/api/v1/analyses/${JOB_ID}/export**`, (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('format') === 'csv') {
      return route.fulfill({
        status: 200,
        headers: {
          'Content-Type': 'text/csv',
          'Content-Disposition': `attachment; filename="fireharmonics-${JOB_ID}-${url.searchParams.get('table')}.csv"`,
        },
        body: 'month,detections\n2024-01,1\n',
      });
    }
    return route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'application/json', 'Content-Disposition': `attachment; filename="fireharmonics-${JOB_ID}.json"` },
      body: JSON.stringify({ job_id: JOB_ID, request: {}, result }),
    });
  });
  return state;
}

/** Deterministic geocoder response so tests do not depend on the external place-search service. */
export async function mockGeocoder(page: Page) {
  await page.route('https://geocoding-api.open-meteo.com/**', (route) =>
    route.fulfill({
      status: 200,
      json: {
        results: [
          { id: 1, name: 'Testville', latitude: 37.7, longitude: -119.5, country: 'Testland', admin1: 'Test Region' },
          { id: 2, name: 'Second Place', latitude: -12.5, longitude: 30.1, country: 'Testland' },
        ],
      },
    }),
  );
}

export const GLOBE_URL = '/globe?lat=45&lon=10&name=Example%20point';

/**
 * Tests must never start real analyses: with a NASA key configured, every POST would queue a real NASA download
 * job on the developer's backend. This blocks POST /api/v1/analyses unless a test installs its own mock
 * (Playwright runs the most recently registered route first). Opt in to the live check with E2E_LIVE_BACKEND=1.
 */
export async function blockRealAnalyses(page: Page) {
  await page.route('**/api/v1/analyses', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({
          status: 503,
          json: { status_code: 503, detail: 'Analyses are blocked in automated tests.', recoverable: false },
        })
      : route.continue(),
  );
}
