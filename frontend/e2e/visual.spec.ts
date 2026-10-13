// Captures every page (1440 px wide: the design is a desktop layout, narrower widths switch to the responsive one) into e2e/screenshots/ (git-ignored) for a manual comparison with
// "ScamMind AI Screens.pdf". It asserts nothing about pixels: it only proves each page renders with data.
// Runs after smoke.spec.ts (alphabetical order, one worker) and so uses the data that file created.
import { expect, test } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const OUT = path.join(path.dirname(fileURLToPath(import.meta.url)), 'screenshots');

const PAGES: [string, string, string][] = [
  ['/login', '01-login', 'Welcome back'],
  ['/setup', '02-setup', 'Set up brand protection in three steps'],
  ['/dashboard', '04-dashboard', 'Total detections'],
  ['/brand', '05-brand', 'Profile health'],
  ['/social', '07-social', 'Excluded as official'],
  ['/apps', '08-apps', 'Excluded as official'],
  ['/lookalike', '09-lookalike', 'Test cases'],
  ['/sources', '10-sources', 'Recent source events'],
  ['/reports', '11-reports', 'Recent exports'],
  ['/settings', '12-settings', 'Detection sensitivity'],
  ['/evaluation', '13-evaluation', 'By technique'],
];

test('capture every page for the visual comparison', async ({ page }) => {
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
  await page.setViewportSize({ width: 1440, height: 900 });
  for (const [url, name, marker] of PAGES) {
    if (name === '02-setup') {
      await page.goto('/login');
      await page.locator('#email').fill(process.env.E2E_ADMIN_EMAIL as string);
      await page.locator('#password').fill(process.env.E2E_ADMIN_PASSWORD as string);
      await page.getByRole('button', { name: /sign in/i }).click();
      await expect(page).toHaveURL(/\/dashboard$/);
    }
    if (name === '01-login') {
      await page.goto('/login');
    } else {
      await page.goto(url);
    }
    await expect(page.getByText(marker).first()).toBeVisible();
    await page.waitForTimeout(400); // let charts and lists settle
    await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
  }
});
