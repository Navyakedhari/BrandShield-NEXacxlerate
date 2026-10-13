// End-to-end smoke tests against the REAL backend (sample collector, temp database, no network) and the Vite dev server.
// Tests run in order and share one database: the first ones create the brand, the later ones use the data.
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { makePng } from './png';

test.describe.configure({ mode: 'serial' });

const ADMIN = { email: process.env.E2E_ADMIN_EMAIL as string, password: process.env.E2E_ADMIN_PASSWORD as string };
const VIEWER = { email: process.env.E2E_VIEWER_EMAIL as string, password: process.env.E2E_VIEWER_PASSWORD as string };

/** Collects console errors and uncaught page errors. */
function watch(page: Page) {
  const errors: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`console: ${m.text()}`);
  });
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  return errors;
}

/** A browser-reported failed HTTP status is an expected "console error" in tests that provoke 4xx/5xx on purpose. */
const withoutHttpStatusNoise = (errors: string[]) => errors.filter((e) => !/Failed to load resource: the server responded with a status of (4|5)\d\d/.test(e));

async function offlineFonts(page: Page) {
  // index.html loads Manrope from Google Fonts: keep the tests free of third-party network access.
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
}

async function login(page: Page, who = ADMIN) {
  await page.goto('/login');
  await page.locator('#email').fill(who.email);
  await page.locator('#password').fill(who.password);
  await page.getByRole('button', { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
}

async function nav(page: Page, name: string, url: RegExp) {
  await page.getByRole('link', { name, exact: true }).first().click();
  await expect(page).toHaveURL(url);
}

test.beforeEach(async ({ page }) => {
  await offlineFonts(page);
});

test('login: wrong password shows the real error and never enters the app; guarded routes redirect', async ({ page }) => {
  const errors = watch(page);
  await page.goto('/dashboard');
  await expect(page).toHaveURL(/\/login$/);
  await page.locator('#email').fill(ADMIN.email);
  await page.locator('#password').fill('definitely-not-the-password');
  await page.getByRole('button', { name: /sign in/i }).click();
  await expect(page.getByRole('alert')).toHaveText('Incorrect email or password');
  await expect(page).toHaveURL(/\/login$/);
  expect(await page.evaluate(() => localStorage.getItem('scammind.auth'))).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem('scammind.offline-session'))).toBeNull();
  await page.goto('/setup');
  await expect(page).toHaveURL(/\/login$/);
  expect(withoutHttpStatusNoise(errors)).toEqual([]);
});

test('setup: admin signs in, creates the brand, pre-checks and uploads a logo, starts the first scan', async ({ page }) => {
  const errors = watch(page);
  await login(page);
  // No brand yet: the dashboard says so instead of showing invented numbers.
  await expect(page.getByText('No brand profile yet.')).toBeVisible();
  await page.getByRole('link', { name: 'Set up your brand profile' }).first().click();
  await expect(page).toHaveURL(/\/setup$/);

  await page.locator('#companyName').fill('Acme Bank');
  await page.locator('#aliases').fill('Acme, AcmeBank, Acme Bank India');

  // client-side pre-check: too small
  await page.getByTestId('setup-logo-input').setInputFiles({ name: 'small.png', mimeType: 'image/png', buffer: makePng(100, 100) });
  await expect(page.getByRole('alert')).toContainText('at least 256 x 256 pixels');
  // not an image at all
  await page.getByTestId('setup-logo-input').setInputFiles({ name: 'logo.svg', mimeType: 'image/svg+xml', buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"/>') });
  await expect(page.getByRole('alert')).toContainText('PNG, JPEG or WebP');
  // a valid logo
  await page.getByTestId('setup-logo-input').setInputFiles({ name: 'logo.png', mimeType: 'image/png', buffer: makePng(512, 512) });
  await expect(page.getByText('New logo selected')).toBeVisible();

  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByText('STEP 2 OF 3')).toBeVisible();
  await expect(page.getByText('None added yet').first()).toBeVisible();
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByText('STEP 3 OF 3')).toBeVisible();
  await expect(page.getByText(/Ready to scan 8 sources/)).toBeVisible();
  await page.getByRole('button', { name: 'Launch Protection Dashboard' }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole('heading', { name: /Protect Acme Bank across social media and app stores/ })).toBeVisible();
  expect(withoutHttpStatusNoise(errors)).toEqual([]);
});

test('brand profile: logo from the server, accounts (duplicate, bad handle, unreachable warning), apps, save, health', async ({ page }) => {
  const errors = watch(page);
  await login(page);
  await nav(page, 'Brand Registry', /\/brand$/);
  await expect(page.getByTestId('logo-specs')).toContainText('PNG');
  await expect(page.getByTestId('logo-specs')).toContainText('512 × 512 px');
  const pctBefore = await page.getByTestId('health-pct').innerText();

  // client-side pre-check and server errors for the logo
  await page.getByTestId('logo-input').setInputFiles({ name: 'wide.png', mimeType: 'image/png', buffer: makePng(600, 300) });
  await expect(page.getByRole('alert')).toContainText('roughly square');

  // official account: ok, duplicate (409), handle format (422), unreachable (warning)
  await page.locator('#acc-handle').fill('acmebank');
  await page.getByRole('button', { name: 'Add to allowlist' }).click();
  await expect(page.getByText('@acmebank on Instagram')).toBeVisible();
  await page.locator('#acc-handle').fill('acmebank');
  await page.getByRole('button', { name: 'Add to allowlist' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'already an official instagram account' })).toBeVisible();
  await page.locator('#acc-handle').fill('a!');
  await page.getByRole('button', { name: 'Add to allowlist' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Handle must be 3 to 30 characters' })).toBeVisible();
  await page.locator('#acc-platform').selectOption('x');
  await page.locator('#acc-handle').fill('unreachable_acme');
  await page.getByRole('button', { name: 'Add to allowlist' }).click();
  await expect(page.getByText('@unreachable_acme on X')).toBeVisible();
  await expect(page.getByText('Unreachable').first()).toBeVisible();
  await expect(page.getByText(/could not be reached/i).first()).toBeVisible();

  // official app
  await page.getByRole('button', { name: 'Add an official app' }).click();
  await page.locator('#app-name').fill('Acme Bank Mobile');
  await page.locator('#app-package').fill('com.acmebank.mobile');
  await page.locator('#app-publisher').fill('Acme Bank Ltd');
  await page.getByRole('button', { name: 'Confirm App' }).click();
  await expect(page.getByText('com.acmebank.mobile')).toBeVisible();

  // identity: keyword + domain + save
  await page.getByLabel('Add a keyword').fill('acme');
  await page.getByLabel('Add a keyword').press('Enter');
  await page.getByLabel('Add a domain').fill('acmebank.com');
  await page.getByLabel('Add a domain').press('Enter');
  await page.getByRole('button', { name: 'Save brand profile' }).click();
  await expect(page.getByRole('button', { name: /Saved successfully/ })).toBeVisible();
  // a bad domain comes back as the server's message
  await page.getByLabel('Add a domain').fill('not a domain');
  await page.getByLabel('Add a domain').press('Enter');
  await page.getByRole('button', { name: 'Save brand profile' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'not a domain' })).toBeVisible();
  await page.getByRole('button', { name: 'Cancel' }).click();

  await page.reload();
  await expect(page.getByText('@acmebank on Instagram')).toBeVisible();
  const pctAfter = await page.getByTestId('health-pct').innerText();
  expect(parseInt(pctAfter)).toBeGreaterThan(parseInt(pctBefore));
  await expect(page.getByText(/Active brand · 3 official assets/)).toBeVisible();
  expect(withoutHttpStatusNoise(errors)).toEqual([]);
});

test('dashboard: scan in sample mode (progress, result), sample badge, real numbers, deep links', async ({ page }) => {
  const errors = watch(page);
  await login(page);
  await expect(page.getByTestId('hero-status')).toContainText('Last scan'); // the first scan was started by the setup wizard
  await page.getByRole('button', { name: /Run scan now/ }).click();
  await expect(page.getByText(/Scan finished: \d+ new detections?/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('sample-badge')).toBeVisible();
  await expect(page.getByTestId('sample-note')).toContainText('Sample data for the fictional brand Acme Bank');
  await expect(page.getByTestId('hero-status')).toContainText('Last scan');
  await expect(page.getByTestId('hero-status')).toContainText('8 of 8 sources online');
  await expect(page.getByText('Total detections')).toBeVisible();
  expect(await page.locator('.review-item-row').count()).toBeGreaterThan(0);
  expect(await page.locator('.dash-data-table tbody tr').count()).toBeGreaterThan(0);
  await expect(page.locator('.source-mini-row')).toHaveCount(6);
  // a row opens that detection on the right page
  await page.locator('.dash-data-table tbody tr').first().click();
  await expect(page).toHaveURL(/\/(social|apps)\?detection=\d+$/);
  await expect(page.getByTestId('detection-detail')).toBeVisible();
  expect(withoutHttpStatusNoise(errors)).toEqual([]);
});

test('social: sample-badged detections, review actions persist on the server, rollback on failure, mark as official', async ({ page }) => {
  const errors = watch(page);
  await login(page);
  await nav(page, 'Social Threats', /\/social$/);
  await expect(page.getByTestId('detection-row').first()).toBeVisible();
  await expect(page.getByTestId('detail-sample-badge')).toBeVisible();
  await expect(page.getByTestId('sample-badge')).toBeVisible();
  await expect(page.getByRole('button', { name: /^All \d+$/ })).toBeVisible();
  // sorted by risk (descending)
  const scores = (await page.locator('.detection-row-card .score-val').allInnerTexts()).map(Number);
  expect(scores).toEqual([...scores].sort((a, b) => b - a));
  // reasons with points that add up to the raw score
  const pts = (await page.locator('.signal-pts').allInnerTexts()).map((t) => Number(t.replace('+', '')));
  expect(pts.length).toBeGreaterThan(0);

  // simulate a server failure: the optimistic change must roll back and say why
  await page.route('**/api/detections/*/status', (r) => r.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'Simulated failure' }) }));
  await page.getByRole('button', { name: 'Confirm impersonation' }).click();
  await expect(page.getByRole('alert')).toContainText('Simulated failure');
  await expect(page.getByTestId('detail-status')).toHaveText('New');
  await page.unroute('**/api/detections/*/status');

  // the real confirm
  await page.getByRole('button', { name: 'Confirm impersonation' }).click();
  await expect(page.getByTestId('detail-status')).toHaveText('Confirmed');
  await expect(page.locator('.detection-row-card.active .chip:not([data-testid="severity-chip"])')).toHaveText('Confirmed');
  await page.reload();
  await expect(page.locator('.detection-row-card.active .chip:not([data-testid="severity-chip"])')).toHaveText('Confirmed'); // persisted by the server, not the browser

  // keep reviewing the second row, dismiss the third
  await page.getByTestId('detection-row').nth(1).click();
  await page.getByRole('button', { name: 'Keep reviewing' }).click();
  await expect(page.getByTestId('detail-status')).toHaveText('Reviewing');
  await page.getByTestId('detection-row').nth(2).click();
  await page.getByRole('button', { name: 'Dismiss as false positive' }).click();
  await expect(page.getByTestId('detail-status')).toHaveText('Dismissed');

  // tabs filter by type and keep their counts
  const tab = page.locator('.filter-tab', { hasText: /^Scam accounts/ });
  await tab.click();
  expect(await page.locator('.chip-purple').first().innerText()).toBe('Scam account');

  // mark as official: leaves the list, joins the excluded list
  await page.locator('.filter-tab', { hasText: /^All/ }).click();
  const before = Number(((await page.locator('.filter-tab', { hasText: /^All/ }).innerText()).match(/\d+/) as RegExpMatchArray)[0]);
  await expect(page.getByTestId('detection-row')).toHaveCount(before);
  const excludedBefore = await page.locator('.bottom-dual-card').first().locator('.excluded-row').count();
  await page.getByTestId('detection-row').last().click();
  await page.getByRole('button', { name: 'Mark as official' }).click();
  await expect(page.getByTestId('detection-row')).toHaveCount(before - 1);
  await expect(page.locator('.bottom-dual-card').first().locator('.excluded-row')).toHaveCount(excludedBefore + 1);

  // bottom panels come from the server
  await expect(page.getByText('False-positive guard')).toBeVisible();
  expect(await page.locator('.bottom-dual-card').nth(1).locator('.excluded-row').count()).toBeGreaterThan(0);

  // scan from this page
  await page.getByRole('button', { name: /Scan social platforms now/ }).click();
  await expect(page.getByText(/Social scan finished/)).toBeVisible({ timeout: 30_000 });
  expect(withoutHttpStatusNoise(errors)).toEqual([]);
});

test('apps: store tabs with counts, publisher banner, confirm, retry banner absent when all sources are online', async ({ page }) => {
  const errors = watch(page);
  await login(page);
  await nav(page, 'App Threats', /\/apps$/);
  await expect(page.getByTestId('detection-row').first()).toBeVisible();
  await expect(page.getByTestId('detail-sample-badge')).toBeVisible();
  await expect(page.getByRole('button', { name: /^All stores \d+$/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Google Play \d+$/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /^App Store \d+$/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Third-party APK \d+$/ })).toBeVisible();
  await expect(page.getByText('Publisher differs from the official one.')).toBeVisible();
  await page.getByRole('button', { name: 'Confirm fake app' }).click();
  await expect(page.getByTestId('detail-status')).toHaveText('Confirmed');
  // official side of the comparison and the excluded list (the app added on the Brand Profile page)
  await expect(page.locator('.official-vs')).toContainText('Acme Bank Mobile');
  await expect(page.locator('.bottom-dual-card').first()).toContainText('com.acmebank.mobile');
  await page.getByRole('button', { name: /Scan app stores now/ }).click();
  await expect(page.getByText(/App store scan finished/)).toBeVisible({ timeout: 30_000 });
  expect(withoutHttpStatusNoise(errors)).toEqual([]);
});

test('look-alike lab: live matcher, honest verdicts, real test-case run', async ({ page }) => {
  const errors = watch(page);
  await login(page);
  await nav(page, 'Look-alike Lab', /\/lookalike$/);
  await expect(page.getByTestId('verdict-headline')).toContainText('Flagged as a look-alike of Acme Bank');
  await expect(page.getByTestId('pass-rate')).toContainText('not run yet'); // no invented "12 of 12"
  await expect(page.getByText('Name match 94%')).toHaveCount(0); // the old hard-coded number is gone

  const input = page.locator('.test-input-field');
  await input.fill('Acme Bakery');
  await expect(page.getByTestId('verdict-chip')).toHaveText('Not flagged');
  await input.fill('@acmebank');
  await expect(page.getByTestId('verdict-chip')).toHaveText('Official');
  await input.fill('acm3bank');
  await expect(page.getByTestId('verdict-chip')).toHaveText('Flagged');
  await expect(page.locator('.step-timeline-row')).toHaveCount(5);

  await page.getByRole('button', { name: 'Run test cases' }).click();
  await expect(page.getByTestId('pass-rate')).toHaveText('12 of 12 pass');
  await expect(page.locator('.test-cases-table tbody tr')).toHaveCount(12);
  expect(withoutHttpStatusNoise(errors)).toEqual([]);
});

test('source health: sources, KPIs, monitor toggle persists, credit meter, events', async ({ page }) => {
  const errors = watch(page);
  await login(page);
  await nav(page, 'Source Health', /\/sources$/);
  await expect(page.getByTestId('kpi-online')).toHaveText('8');
  await expect(page.locator('.source-detail-row')).toHaveCount(8);
  await expect(page.getByText('Apple App Store', { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId('credit-meter')).toContainText('credits used');
  await expect(page.getByTestId('credit-meter')).toContainText('no live searches are made');
  await expect(page.getByText('Recent source events')).toBeVisible();
  await expect(page.locator('.event-row').first()).toContainText('Scan');

  const box = page.getByLabel('Monitor Telegram');
  await expect(box).toBeChecked();
  await box.uncheck();
  await expect(box).not.toBeChecked();
  await page.reload();
  await expect(page.getByLabel('Monitor Telegram')).not.toBeChecked(); // saved on the server
  await page.getByLabel('Monitor Telegram').check();
  await expect(page.getByLabel('Monitor Telegram')).toBeChecked();
  expect(withoutHttpStatusNoise(errors)).toEqual([]);
});

test('reports: draft from detections, preview, CSV and HTML downloads with auth, approve, app names not package ids', async ({ page }) => {
  const errors = watch(page);
  await login(page);
  await nav(page, 'Reports', /\/reports$/);
  await expect(page.getByTestId('preview-sample-chip')).toBeVisible();
  await expect(page.locator('.preview-table tbody tr').first()).toBeVisible();
  // rows are sorted by score, social rows show handles, app rows show display names
  const rowScores = (await page.locator('.row-score-cell').allInnerTexts()).map(Number);
  expect(rowScores).toEqual([...rowScores].sort((a, b) => b - a));
  const names = await page.locator('.row-name-cell').allInnerTexts();
  expect(names.some((n) => n.startsWith('@'))).toBe(true);
  expect(names.some((n) => /^com\.[a-z0-9_.]+$/.test(n))).toBe(false);
  expect(await page.locator('.preview-table').innerText()).not.toMatch(/fake brand_page/);

  // filters change the selection
  await page.locator('#rp-status').selectOption('confirmed');
  await expect(page.locator('.preview-table tbody tr').first()).toBeVisible();
  const confirmedRows = await page.locator('.preview-table tbody tr').count();
  await page.locator('#rp-status').selectOption('all');
  await expect.poll(async () => page.locator('.preview-table tbody tr').count()).toBeGreaterThan(confirmedRows);

  await page.getByRole('button', { name: 'CSV data' }).click();
  await page.getByRole('button', { name: 'Generate report' }).click();
  await expect(page.getByText(/Draft created: .*\.csv/)).toBeVisible();
  await expect(page.getByTestId('report-item').first()).toContainText('Draft');

  const [csv] = await Promise.all([page.waitForEvent('download'), page.getByTestId('report-item').first().getByRole('button', { name: 'Download', exact: true }).click()]);
  expect(csv.suggestedFilename()).toMatch(/\.csv$/);
  const csvText = await (await import('node:fs/promises')).readFile(await csv.path(), 'utf8');
  expect(csvText.split('\n')[0]).toContain('id,name');

  await page.getByRole('button', { name: 'Printable report' }).click();
  await page.getByRole('button', { name: 'Generate report' }).click();
  await expect(page.getByText(/Draft created: .*\.html/)).toBeVisible();
  const first = page.getByTestId('report-item').first();
  const [html] = await Promise.all([page.waitForEvent('download'), first.getByRole('button', { name: 'Download', exact: true }).click()]);
  expect(html.suggestedFilename()).toMatch(/\.html$/);
  await first.getByRole('button', { name: 'Preview' }).click();
  await expect(page.frameLocator('iframe[title="Report preview"]').getByText('Brand Protection Report').first()).toBeVisible();
  await first.getByRole('button', { name: 'Approve' }).click();
  await expect(first).toContainText('Approved');
  expect(withoutHttpStatusNoise(errors)).toEqual([]);
});

test('settings: values persist after Save, notifications are labelled not delivered, data mode switch, evaluation view', async ({ page }) => {
  const errors = watch(page);
  await login(page);
  await nav(page, 'Settings', /\/settings$/);
  await expect(page.getByTestId('sensitivity-summary')).toContainText(/\d+ of the \d+ candidates/);
  await expect(page.getByTestId('settings-save')).toBeDisabled(); // nothing changed yet

  await page.locator('#st-schedule').selectOption('1h');
  await page.getByLabel('Detection sensitivity').fill('85');
  await page.getByLabel('Instagram').uncheck();
  await page.getByLabel('Add an impersonation word').fill('winner');
  await page.getByLabel('Add an impersonation word').press('Enter');
  await page.getByLabel('Digits and symbols for letters').uncheck();
  await expect(page.getByTestId('sensitivity-summary')).toContainText(/at least|would be sent/);
  await page.getByTestId('settings-save').click();
  await expect(page.getByRole('button', { name: /Saved successfully/ })).toBeVisible();

  await page.reload();
  await expect(page.locator('#st-schedule')).toHaveValue('1h');
  await expect(page.getByLabel('Detection sensitivity')).toHaveValue('85');
  await expect(page.getByLabel('Instagram')).not.toBeChecked();
  await expect(page.locator('.impersonation-word-pill', { hasText: 'winner' })).toBeVisible();
  await expect(page.getByLabel('Digits and symbols for letters')).not.toBeChecked();
  // the monitored flag is the same state as the Source Health toggle
  await nav(page, 'Source Health', /\/sources$/);
  await expect(page.getByLabel('Monitor Instagram')).not.toBeChecked();
  await nav(page, 'Settings', /\/settings$/);

  // notifications: stored, honestly not delivered
  await page.locator('.settings-tab-btn', { hasText: 'Notifications' }).click();
  await expect(page.getByTestId('not-delivered')).toContainText('nothing is delivered yet');
  await page.getByLabel('Webhook dispatch to a Slack or other channel').check();
  await page.getByLabel('Webhook URL').fill('http://not-https.example');
  await page.getByTestId('settings-save').click();
  await expect(page.getByRole('alert')).toContainText('https://');
  await page.getByLabel('Webhook dispatch to a Slack or other channel').uncheck();
  await page.getByLabel('Send email alerts for risk scores above 85').check();
  await page.getByTestId('settings-save').click();
  await expect(page.getByRole('button', { name: /Saved successfully/ })).toBeVisible();

  // account tab: data mode (admin can switch), badge follows the effective mode
  await page.locator('.settings-tab-btn', { hasText: 'Account' }).click();
  await expect(page.getByTestId('account-line')).toContainText(ADMIN.email);
  await expect(page.getByTestId('effective-mode')).toContainText('sample');
  await expect(page.getByTestId('sample-badge')).toBeVisible();
  await page.getByRole('radio', { name: 'Live' }).click();
  await expect(page.getByTestId('effective-mode')).toContainText('live');
  await expect(page.getByTestId('sample-badge')).toHaveCount(0);
  await page.getByRole('radio', { name: 'Auto' }).click();
  await expect(page.getByTestId('effective-mode')).toContainText('sample');
  await expect(page.getByTestId('sample-badge')).toBeVisible();

  // evaluation view
  await page.getByTestId('evaluation-link').click();
  await expect(page).toHaveURL(/\/evaluation$/);
  await expect(page.getByTestId('eval-label')).toContainText('Synthetic numbers are optimistic');
  await expect(page.getByTestId('eval-overall-precision')).toHaveText(/%$/);
  await expect(page.getByTestId('eval-overall-recall')).toHaveText(/%$/);
  await expect(page.getByTestId('eval-overall-f1')).toHaveText(/%$/);
  expect(await page.locator('[data-testid="eval-technique-table"] tbody tr').count()).toBeGreaterThan(5);
  await expect(page.getByTestId('eval-cases-summary')).toHaveText(/\d+ of \d+ pass/);
  await expect(page.getByText('Real results: reviewer decisions')).toBeVisible();
  expect(withoutHttpStatusNoise(errors)).toEqual([]);
});

test('viewer: read-only (no scan, no settings changes, no data-mode switch); sign-out clears the session', async ({ page }) => {
  const errors = watch(page);
  await login(page, VIEWER);
  await expect(page.getByRole('button', { name: /Run scan now/ })).toBeDisabled();
  await expect(page.getByText('E2E Viewer').first()).toBeVisible();
  await nav(page, 'Settings', /\/settings$/);
  await expect(page.getByTestId('settings-save')).toBeDisabled();
  await expect(page.getByText('Only administrators can change settings')).toBeVisible();
  await page.locator('.settings-tab-btn', { hasText: 'Account' }).click();
  await expect(page.getByText('Read only: only administrators can change the data mode')).toBeVisible();
  await expect(page.getByRole('radio', { name: 'Live' })).toBeDisabled();

  // sign out: the real key is removed from both storages and the guard sends the user back to the login page
  await page.locator('.user-chip').click();
  await page.getByRole('menuitem', { name: /sign out/i }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect(await page.evaluate(() => ({ l: localStorage.getItem('scammind.auth'), s: sessionStorage.getItem('scammind.auth'), u: localStorage.getItem('scammind.user') }))).toEqual({ l: null, s: null, u: null });
  await page.goto('/dashboard');
  await expect(page).toHaveURL(/\/login$/);
  expect(withoutHttpStatusNoise(errors)).toEqual([]);
});

test('an expired access token is refreshed with rotation and the page still loads', async ({ page }) => {
  const errors = watch(page);
  await login(page);
  const tokens = await page.evaluate(() => JSON.parse(localStorage.getItem('scammind.auth') as string) as { access: string; refresh: string });
  await page.evaluate(() => {
    const t = JSON.parse(localStorage.getItem('scammind.auth') as string);
    localStorage.setItem('scammind.auth', JSON.stringify({ ...t, access: 'expired.invalid.token' }));
  });
  await page.goto('/brand');
  await expect(page.locator('h1.header-title')).toHaveText('Brand Profile');
  await expect(page.getByTestId('health-pct')).toBeVisible();
  const rotated = await page.evaluate(() => JSON.parse(localStorage.getItem('scammind.auth') as string) as { access: string; refresh: string });
  expect(rotated.access).not.toBe('expired.invalid.token');
  expect(rotated.refresh).not.toBe(tokens.refresh); // the refresh token was rotated
  // the browser reports the intentional 401 as a console message; nothing else may be reported
  expect(withoutHttpStatusNoise(errors)).toEqual([]);
});

test('no domain data in browser storage and no console errors on a full walk of every page', async ({ page }) => {
  const errors = watch(page);
  await login(page);

  const pages: [string, RegExp, string][] = [
    ['/dashboard', /Brand risk overview for Acme Bank/, 'Dashboard'],
    ['/brand', /Tell us what is official/, 'Brand Profile'],
    ['/social', /Fake profiles, fake brand pages and scam accounts using the Acme Bank identity/, 'Social Media Monitoring'],
    ['/apps', /Apps that copy the Acme Bank name, icon, publisher or description/, 'App Store Monitoring'],
    ['/lookalike', /See how names are normalized and matched/, 'Look-alike Lab'],
    ['/sources', /Where we look for impersonations/, 'Source Health'],
    ['/reports', /Export detections with reasons and evidence/, 'Reports'],
    ['/settings', /Control what is monitored/, 'Settings'],
    ['/evaluation', /How well the look-alike matcher finds impersonations/, 'Evaluation'],
  ];
  for (const [url, subtitle, title] of pages) {
    await page.goto(url);
    await expect(page.locator('h1.header-title')).toHaveText(title);
    await expect(page.getByText(subtitle).first()).toBeVisible();
    await expect(page.locator('.state-error')).toHaveCount(0);
  }
  await page.goto('/setup');
  await expect(page.getByRole('heading', { name: 'Set up brand protection in three steps' })).toBeVisible();
  await page.goto('/login'); // the login page itself
  await expect(page.getByText('Welcome back')).toBeVisible();

  const keys = await page.evaluate(() => Object.keys(localStorage));
  expect(keys.filter((k) => k.startsWith('scammind_v2_'))).toEqual([]); // the old domain-data keys are gone
  expect(keys.every((k) => k === 'scammind.auth' || k === 'scammind.user' || k.startsWith('scammind.ui.'))).toBe(true);
  expect(errors).toEqual([]); // no console errors at all (and no uncaught page errors)
});
