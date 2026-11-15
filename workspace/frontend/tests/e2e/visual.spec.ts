import { expect, test } from '@playwright/test';
import { blockRealAnalyses, GLOBE_URL, mockCompletedAnalysis } from './helpers';

test.beforeEach(async ({ page }) => {
  await blockRealAnalyses(page);
});

/**
 * Captures each page at the mockup size (1600 x 1040) into test-results/visual-final/ for side-by-side
 * comparison with /design/mockups/01..05. Run: npx playwright test visual --project=desktop
 */
const SHOTS: [string, string][] = [
  ['01-home', '/'],
  ['02-interactive-globe', GLOBE_URL],
  ['03-about', '/about'],
  ['04-contact', '/contact'],
  ['05-faq', '/faq'],
];

test.describe('visual capture at mockup size', () => {
  test.skip(({ viewport }) => viewport?.width !== 1600, 'desktop only');
  for (const [name, path] of SHOTS) {
    test(name, async ({ page }) => {
      if (path === GLOBE_URL) await mockCompletedAnalysis(page);
      await page.goto(path);
      await page.waitForTimeout(4500); // intro animations + globe transition
      await expect(page.getByRole('navigation', { name: 'Main' })).toBeVisible();
      await page.screenshot({ path: `test-results/visual-final/${name}.png` });
    });
  }
});

test.describe('visual capture of report details', () => {
  test.skip(({ viewport }) => viewport?.width !== 1600, 'desktop only');
  test('06-panel-and-calendar', async ({ page }) => {
    await mockCompletedAnalysis(page);
    await page.goto(GLOBE_URL);
    await page.getByTestId('panel-summary').waitFor();
    await page.waitForTimeout(2500);
    await page.locator('.analysis-panel').screenshot({ path: 'test-results/visual-final/06-panel.png' });
    await page.getByTestId('open-report').click();
    await page.waitForTimeout(800);
    await page.locator('#r-calendar').scrollIntoViewIfNeeded();
    await page.locator('#r-calendar').screenshot({ path: 'test-results/visual-final/07-calendar.png' });
    await page.locator('[data-testid=calibration-validation]').screenshot({ path: 'test-results/visual-final/08-validation.png' });
  });
});
