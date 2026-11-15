import { defineConfig, devices } from '@playwright/test';

// Uses the system Microsoft Edge (channel "msedge"); no Playwright browser download needed.
export default defineConfig({
  testDir: 'tests/e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  outputDir: 'test-results',
  use: {
    baseURL: 'http://127.0.0.1:4173',
    channel: 'msedge',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Edge'], channel: 'msedge', viewport: { width: 1600, height: 1040 } } },
    { name: 'mobile', use: { channel: 'msedge', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
