import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { blockRealAnalyses, GLOBE_URL, mockCompletedAnalysis, mockGeocoder } from './helpers';

test.beforeEach(async ({ page }) => {
  if (!process.env.E2E_LIVE_BACKEND) await blockRealAnalyses(page);
});

const PAGES = ['/', '/about', '/contact', '/faq'];

test.describe('navigation and layout', () => {
  for (const path of PAGES) {
    test(`page ${path} renders without horizontal overflow or console errors`, async ({ page }) => {
      const errors: string[] = [];
      page.on('pageerror', (e) => errors.push(e.message));
      await page.goto(path);
      await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible();
      await page.waitForTimeout(500);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(1);
      expect(errors).toEqual([]);
    });
  }

  test('main navigation links work', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('link', { name: 'About' }).click();
    await expect(page.getByRole('heading', { name: 'What is FireHarmonics?' })).toBeVisible();
    await page.getByRole('link', { name: 'Contact' }).click();
    await expect(page.getByRole('heading', { name: /Get in touch/ })).toBeVisible();
    await page.getByRole('link', { name: 'FAQ' }).click();
    await expect(page.getByRole('heading', { name: 'Frequently asked questions' })).toBeVisible();
    await page.getByRole('link', { name: 'Home' }).click();
    await expect(page.getByRole('heading', { name: 'FireHarmonics' })).toBeVisible();
  });
});

test.describe('home → interactive globe', () => {
  test('search suggestions appear below the field and selection opens the globe with real coordinates', async ({
    page,
  }) => {
    await mockGeocoder(page);
    await page.goto('/');
    const box = page.getByRole('combobox', { name: /Search for a location/ });
    await box.fill('Test');
    const list = page.getByRole('listbox', { name: 'Location suggestions' });
    await expect(list.getByRole('option')).toHaveCount(2);
    const inputBox = (await box.boundingBox())!;
    const listBox = (await list.boundingBox())!;
    expect(listBox.y).toBeGreaterThanOrEqual(inputBox.y + inputBox.height - 1);
    await box.press('ArrowDown');
    await box.press('Enter');
    await expect(page).toHaveURL(/\/globe\?lat=-12\.5\d*&lon=30\.1\d*/);
    await expect(page.getByTestId('location-title')).toHaveText(/Second Place/);
    await expect(page.getByText(/25 km analysis radius/)).toBeVisible();
  });

  test('typed coordinates are accepted without a geocoder', async ({ page }) => {
    await page.goto('/');
    const box = page.getByRole('combobox', { name: /Search for a location/ });
    await box.fill('37.7, -119.5');
    await expect(page.getByRole('option')).toHaveCount(1);
    await box.press('Enter');
    await expect(page).toHaveURL(/lat=37\.70000&lon=-119\.50000/);
  });

  test('clicking a country on the globe selects that place and names it after the country', async ({
    page,
    viewport,
  }) => {
    test.skip((viewport?.width ?? 0) < 820, 'desktop layout');
    await page.goto('/globe');
    const stage = page.getByTestId('globe-stage');
    await page.waitForTimeout(2500); // textures + settle
    // The default view faces 18 N, 20 E (Chad); the sphere centre is at (0.4 w, 0.567 h) on desktop (mockup 02).
    const w = viewport!.width;
    const h = viewport!.height;
    await page.mouse.click(
      Math.min(w * 0.4, (w - Math.min(Math.max(w * 0.256, 340), 470)) * 0.54),
      h * 0.567,
    );
    await expect(page).toHaveURL(/\/globe\?lat=1[5-9]\.\d+&lon=(1[5-9]|2[0-5])\.\d+&name=Chad/);
    await expect(page.getByRole('complementary', { name: 'Location analysis' })).toContainText('Chad');
    // a drag is not a click: rotating the globe must not change the selection
    const before = page.url();
    const box = (await stage.boundingBox())!;
    await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.5);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.36, box.y + box.height * 0.52, { steps: 6 });
    await page.mouse.up();
    expect(page.url()).toBe(before);
  });

  test('Enter on the focused globe selects the country in the centre of the view', async ({
    page,
    viewport,
  }) => {
    test.skip((viewport?.width ?? 0) < 820, 'desktop layout');
    await page.goto('/globe');
    await page.waitForTimeout(1500);
    await page.getByTestId('globe-stage').focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/name=Chad/);
  });

  test('live backend: the analysis request reaches the real API and its outcome is reported honestly', async ({
    page,
  }) => {
    test.skip(!process.env.E2E_LIVE_BACKEND, 'starts a real NASA analysis; run with E2E_LIVE_BACKEND=1');
    const post = page.waitForResponse(
      (r) => r.url().endsWith('/api/v1/analyses') && r.request().method() === 'POST',
    );
    await page.goto('/globe?lat=37.7&lon=-119.5&name=Live%20backend%20check');
    const res = await post;
    const body = res.request().postDataJSON();
    expect(body).toMatchObject({
      latitude: 37.7,
      longitude: -119.5,
      radius_km: 25,
      location_name: 'Live backend check',
    });
    if (res.status() >= 400) {
      // e.g. 500 "NASA_FIRMS_MAP_KEY is not configured" when no key is set — shown verbatim, no data invented
      await expect(page.getByRole('alert').first()).toBeVisible();
      await expect(page.getByTestId('panel-summary')).toHaveCount(0);
      await expect(page.getByTestId('open-report')).toBeDisabled();
    } else {
      await expect(page.getByTestId('job-status').or(page.getByTestId('panel-summary'))).toBeVisible();
    }
  });
});

test.describe('location analysis report (backend example result)', () => {
  test('forecast is presented as experimental XGBoost next to the backend-selected seasonal baseline', async ({
    page,
  }) => {
    await mockCompletedAnalysis(page);
    await page.goto(GLOBE_URL);
    const summary = page.getByTestId('panel-summary');
    await expect(summary).toBeVisible();
    const xgb = summary.getByTestId('xgb-card');
    await expect(xgb).toContainText('Experimental XGBoost Forecast');
    await expect(xgb).toContainText('Experimental');
    await expect(xgb).toContainText('Not validated');
    await expect(xgb).toContainText('May be inaccurate');
    await expect(xgb).toContainText('4.1 %');
    await expect(xgb).toContainText(/Not used by the backend/);
    const base = summary.getByTestId('baseline-card');
    await expect(base).toContainText('10.0 %');
    await expect(base).toContainText('Selected by backend');
    await expect(summary).toContainText('Backend-selected method: Seasonal baseline');
  });

  test('report opens as a dialog with all sections, keyboard close and focus return', async ({ page }) => {
    await mockCompletedAnalysis(page);
    await page.goto(GLOBE_URL);
    const open = page.getByTestId('open-report');
    await open.click();
    const dialog = page.getByRole('dialog', { name: 'Example point' });
    await expect(dialog).toBeVisible();
    for (const title of [
      'Burning Activity Calendar',
      'Historical Trends',
      'AI Pattern Discovery',
      'AI Forecast',
      'Satellite Analysis',
      'Export',
    ]) {
      await expect(dialog.getByRole('heading', { name: title, exact: true })).toBeVisible();
    }
    await expect(dialog).toContainText('satellite detection, not an individual wildfire ignition');
    await expect(dialog).toContainText('data distribution service (not a sensor)');
    // unobserved month is labelled as missing, not zero (10-year ML record view)
    await dialog.getByRole('tab', { name: /10-year ML record/ }).click();
    await expect(dialog.getByRole('gridcell', { name: /November 2026: not observed/ })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(open).toBeFocused();
  });

  test('settings are synchronised between panel and report and trigger a real-shaped API request', async ({
    page,
  }) => {
    const state = await mockCompletedAnalysis(page);
    await page.goto(GLOBE_URL);
    await expect(page.getByTestId('panel-summary')).toBeVisible();
    await page.getByTestId('open-report').click();
    const dialog = page.getByRole('dialog');
    const before = state.posts.length;
    await dialog.getByLabel('Time period').selectOption('last_90');
    await expect.poll(() => state.posts.length).toBeGreaterThan(before);
    expect(state.posts.at(-1)).toMatchObject({
      source: 'VIIRS_NOAA20_NRT',
      ml_mode: 'cache_only',
      radius_km: 25,
    });
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    const panel = page.getByRole('complementary', { name: 'Location analysis' });
    await expect(panel.getByLabel('Time period')).toHaveValue('last_90');
    await panel.getByLabel('Time period').selectOption('last_365');
    await expect.poll(() => state.posts.at(-1)?.start_date).toBeTruthy();
    const last = state.posts.at(-1)!;
    const days = (Date.parse(String(last.end_date)) - Date.parse(String(last.start_date))) / 86_400_000 + 1;
    expect(days).toBe(365);
  });

  test('satellite data offers only main FIRMS VIIRS, MODIS and the harmonized MODIS+VIIRS record', async ({
    page,
  }) => {
    await mockCompletedAnalysis(page);
    await page.goto(GLOBE_URL);
    await expect(page.getByTestId('panel-summary')).toBeVisible();
    const options = page.getByLabel('Satellite data').locator('option');
    await expect(options).toHaveCount(3);
    expect(await options.evaluateAll((els) => els.map((e) => (e as HTMLOptionElement).value))).toEqual([
      'VIIRS_NOAA20_NRT',
      'MODIS_NRT',
      '__harmonized__',
    ]);
    await expect(page.getByLabel('Satellite data').locator('option[value="__harmonized__"]')).toHaveText(
      /^Harmonized/,
    );
  });

  test('harmonized view shows the MODIS+VIIRS chain in the panel and the report', async ({ page }) => {
    await mockCompletedAnalysis(page);
    await page.goto(GLOBE_URL);
    const panel = page.getByRole('complementary', { name: 'Location analysis' });
    await expect(page.getByTestId('panel-summary')).toBeVisible();
    await panel.getByLabel('Satellite data').selectOption('__harmonized__');
    await expect(page.getByTestId('harmonized-note')).toContainText('Aqua-MODIS scale');
    await page.getByTestId('open-report').click();
    await expect(page.getByRole('dialog').getByTestId('harmonized-chart')).toContainText('90 % interval');
  });

  test('normalized index is offered where no harmonized series exists, clearly labelled', async ({
    page,
  }) => {
    await mockCompletedAnalysis(page);
    await page.goto(GLOBE_URL);
    const panel = page.getByRole('complementary', { name: 'Location analysis' });
    await expect(page.getByTestId('panel-summary')).toBeVisible();
    await page.getByTestId('open-report').click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('button', { name: 'Show normalized index' }).click();
    const chart = dialog.getByTestId('normalized-chart');
    await expect(chart).toBeVisible();
    await expect(chart).toContainText('Normalized — not harmonized, not validated');
    await expect(chart).toContainText('not a harmonization');
    await page.keyboard.press('Escape');
    await expect(panel.getByTestId('normalized-note')).toContainText('not harmonized, not validated');
    await expect(panel.getByTestId('normalized-note')).toContainText('2024-07');
  });

  test('harmonized MODIS+VIIRS calendar, its validation, season and current conditions are shown', async ({
    page,
  }) => {
    await mockCompletedAnalysis(page);
    await page.goto(GLOBE_URL);
    const summary = page.getByTestId('panel-summary');
    await expect(summary.getByTestId('now-card')).toContainText('Isolated detections');
    await expect(summary.getByTestId('season-card')).toContainText('Apr');
    await page.getByTestId('open-report').click();
    const dialog = page.getByRole('dialog');
    const heat = dialog.getByTestId('combined-heatmap');
    await expect(heat).toBeVisible();
    await expect(heat.getByRole('rowheader', { name: '2002' })).toBeVisible();
    await expect(heat.getByRole('gridcell', { name: /Aqua MODIS/ }).first()).toBeVisible();
    await expect(heat.getByRole('gridcell', { name: /S-NPP VIIRS scaled to MODIS/ }).first()).toBeVisible();
    const val = dialog.getByTestId('calibration-validation');
    await expect(val).toContainText('Continuity chain · experimental');
    await expect(val).toContainText('0.211'); // Aqua <- S-NPP day ratio from the fixture
    await expect(val).toContainText('NOAA-20');
    await expect(heat.getByRole('gridcell', { name: /unusually high/ }).first()).toBeVisible();
  });

  test('area radius selector sends the chosen radius to the API', async ({ page }) => {
    const state = await mockCompletedAnalysis(page);
    await page.goto(GLOBE_URL);
    await expect(page.getByTestId('panel-summary')).toBeVisible();
    await page
      .getByRole('complementary', { name: 'Location analysis' })
      .getByLabel('Area of interest')
      .selectOption('50');
    await expect.poll(() => state.posts.at(-1)?.radius_km).toBe(50);
    await expect(page.getByText(/50 km analysis radius/)).toBeVisible();
  });

  test('CSV and JSON exports download through the backend export endpoint', async ({ page }) => {
    await mockCompletedAnalysis(page);
    await page.goto(GLOBE_URL);
    await page.getByTestId('open-report').click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('CSV table').selectOption('ml_monthly');
    const [csv] = await Promise.all([
      page.waitForEvent('download'),
      dialog.getByRole('button', { name: 'Download CSV' }).click(),
    ]);
    expect(csv.suggestedFilename()).toMatch(/ml_monthly\.csv$/);
    const [json] = await Promise.all([
      page.waitForEvent('download'),
      dialog.getByRole('button', { name: 'Download JSON' }).click(),
    ]);
    expect(json.suggestedFilename()).toMatch(/\.json$/);
  });

  test('the side panel downloads JSON and CSV directly and the PDF through the report', async ({ page }) => {
    test.setTimeout(120_000);
    await mockCompletedAnalysis(page);
    await page.goto(GLOBE_URL);
    const exportsBox = page.getByTestId('panel-exports');
    await exportsBox.getByLabel('CSV table').selectOption('calendar');
    const [csv] = await Promise.all([
      page.waitForEvent('download'),
      exportsBox.getByRole('button', { name: 'CSV' }).click(),
    ]);
    expect(csv.suggestedFilename()).toMatch(/calendar\.csv$/);
    const [json] = await Promise.all([
      page.waitForEvent('download'),
      exportsBox.getByRole('button', { name: 'JSON' }).click(),
    ]);
    expect(json.suggestedFilename()).toMatch(/\.json$/);
    const [pdf] = await Promise.all([
      page.waitForEvent('download', { timeout: 90_000 }),
      exportsBox.getByRole('button', { name: 'PDF' }).click(),
    ]);
    expect(pdf.suggestedFilename()).toBe('fireharmonics-report-Example-point.pdf');
    await expect(page.getByRole('dialog')).toBeVisible();
  });

  test('PDF export is generated in the browser', async ({ page }) => {
    test.setTimeout(120_000);
    await mockCompletedAnalysis(page);
    await page.goto(GLOBE_URL);
    await page.getByTestId('open-report').click();
    const [pdf] = await Promise.all([
      page.waitForEvent('download', { timeout: 90_000 }),
      page.getByRole('button', { name: 'Download PDF' }).click(),
    ]);
    expect(pdf.suggestedFilename()).toBe('fireharmonics-report-Example-point.pdf');
  });
});

test.describe('about, contact, faq', () => {
  test('about tabs switch content', async ({ page }) => {
    await page.goto('/about');
    for (const tab of ['NASA Challenge', 'How It Works', 'Our Mission', 'Overview']) {
      await page.getByRole('tab', { name: tab }).click();
      await expect(page.getByRole('tab', { name: tab })).toHaveAttribute('aria-selected', 'true');
    }
    await expect(page.getByRole('heading', { name: 'Meet the Team' })).toHaveCount(0);
  });

  test('contact form validates after interaction and never fakes delivery', async ({ page }) => {
    await page.goto('/contact');
    const email = page.getByLabel('Email');
    await email.fill('not-an-email');
    await email.blur();
    await expect(page.getByText(/valid email address/)).toBeVisible();
    await page.getByLabel('First name').fill('Ada');
    await page.getByLabel('Last name').fill('Lovelace');
    await email.fill('ada@example.com');
    await page.getByLabel('Message').fill('Hello FireHarmonics team, this is a test message.');
    await page.getByRole('button', { name: 'Submit' }).click();
    await expect(page.getByRole('alert').filter({ hasText: /not configured/ })).toBeVisible();
    await expect(page.getByText(/delivered/)).toHaveCount(0);
    await expect(page.getByLabel('Message')).toHaveValue('Hello FireHarmonics team, this is a test message.');
  });

  test('faq has ten entries and allows several open at once', async ({ page }) => {
    await page.goto('/faq');
    const triggers = page.locator('.faq-item__trigger');
    await expect(triggers).toHaveCount(10);
    await triggers.nth(1).click();
    await triggers.nth(6).click();
    await expect(page.locator('.faq-item[data-state=open]')).toHaveCount(2);
    await expect(page.getByText(/passed for no region and no lead time/)).toBeVisible();
  });
});

test.describe('accessibility', () => {
  test('settings panel applies text size, contrast, palette and reduced motion', async ({ page }) => {
    await page.goto('/faq');
    await page.getByRole('button', { name: 'Accessibility settings' }).click();
    const dialog = page.getByRole('dialog', { name: 'Accessibility' });
    await dialog.getByRole('button', { name: /larger text/ }).click();
    await dialog.getByLabel('High contrast').check();
    await dialog.getByLabel('Colour-blind-friendly charts').check();
    await dialog.getByLabel('Reduce motion').check();
    const attrs = await page.evaluate(() => ({
      scale: getComputedStyle(document.documentElement).getPropertyValue('--text-scale').trim(),
      contrast: document.documentElement.dataset.contrast,
      palette: document.documentElement.dataset.palette,
      motion: document.documentElement.dataset.motion,
    }));
    expect(attrs).toEqual({ scale: '1.15', contrast: 'high', palette: 'cb', motion: 'reduce' });
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
  });

  test('skip link is the first focusable element', async ({ page }) => {
    await page.goto('/about');
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  });

  for (const path of [...PAGES, GLOBE_URL]) {
    test(`axe: no serious or critical violations on ${path}`, async ({ page }) => {
      if (path === GLOBE_URL) await mockCompletedAnalysis(page);
      await page.goto(path);
      await page.waitForTimeout(2200);
      const results = await new AxeBuilder({ page }).exclude('canvas').analyze();
      const serious = results.violations
        .filter((v) => v.impact === 'serious' || v.impact === 'critical')
        .map(
          (v) =>
            `${v.id}: ${v.nodes
              .map((n) => n.target.join(' '))
              .slice(0, 3)
              .join(' | ')}`,
        );
      expect(serious).toEqual([]);
    });
  }

  test('axe: report dialog has no serious or critical violations', async ({ page }) => {
    await mockCompletedAnalysis(page);
    await page.goto(GLOBE_URL);
    await page.getByTestId('open-report').click();
    await page.waitForTimeout(1200);
    const results = await new AxeBuilder({ page }).include('[role=dialog]').analyze();
    const serious = results.violations
      .filter((v) => v.impact === 'serious' || v.impact === 'critical')
      .map(
        (v) =>
          `${v.id}: ${v.nodes
            .map((n) => n.target.join(' '))
            .slice(0, 3)
            .join(' | ')}`,
      );
    expect(serious).toEqual([]);
  });
});

test('changing settings cancels the superseded analysis on the server', async ({ page }) => {
  const cancels: string[] = [];
  const job = (id: string, status: string) => ({
    status_code: 202,
    job_id: id,
    status,
    progress: 10,
    stage: 'downloading',
    eta_seconds: null,
    nasa_pause_seconds: null,
    cache_hit: false,
    attempt: 1,
    error: null,
    message: null,
    result_status: null,
    links: {
      status: `/api/v1/analyses/${id}`,
      events: `/api/v1/analyses/${id}/events`,
      result: `/api/v1/analyses/${id}/result`,
      retry: `/api/v1/analyses/${id}/retry`,
      export: `/api/v1/analyses/${id}/export`,
    },
  });
  let n = 0;
  await page.route('**/api/v1/analyses', (r) =>
    r.request().method() === 'POST'
      ? r.fulfill({ status: 202, json: { ...job(`job-${++n}`, 'running'), owner_token: `token-${n}` } })
      : r.continue(),
  );
  await page.route('**/api/v1/analyses/*/events', (r) =>
    r.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body: ': waiting\n\n' }),
  );
  await page.route('**/api/v1/analyses/*/cancel', (r) => {
    cancels.push(`${r.request().url().split('/').at(-2)} ${r.request().headers()['x-job-token']}`);
    return r.fulfill({ status: 200, json: job('x', 'failed') });
  });
  await page.goto('/globe?lat=45&lon=10&name=Cancel%20check');
  await expect(page.getByTestId('job-status')).toBeVisible();
  await page
    .getByRole('complementary', { name: 'Location analysis' })
    .getByLabel('Area of interest')
    .selectOption('50');
  // the cancel carries the owner token the backend issued for job-1 (other clients would get 403)
  await expect.poll(() => cancels).toEqual(['job-1 token-1']);
});
