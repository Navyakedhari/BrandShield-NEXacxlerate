// Playwright smoke tests. They start the REAL backend (backend/scripts/e2e_server.py: sample collector, temp SQLite database,
// no Tavily key, no network) and the Vite dev server, then walk every page in a browser.
// Secrets are generated per run, passed only through the environment of the child processes, and never written to disk or printed.
import { defineConfig } from '@playwright/test';
import { randomBytes } from 'node:crypto';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

const API_PORT = Number(process.env.E2E_API_PORT || 8765);
const WEB_PORT = Number(process.env.E2E_WEB_PORT || 5174);
// This file is also loaded by the test worker: values created by the main process are inherited through the environment, never regenerated.
const tmp = process.env.E2E_TMP_DIR ?? mkdtempSync(path.join(tmpdir(), 'scammind-e2e-'));
process.env.E2E_TMP_DIR = tmp;
const serverDir = path.resolve(here, '..', 'backend');
const python = process.env.E2E_PYTHON || path.join(serverDir, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');

// Throwaway credentials for this run only. The tests read them from the environment, so they are never hard-coded or logged.
process.env.E2E_ADMIN_EMAIL ??= 'e2e-admin@example.com';
process.env.E2E_ADMIN_PASSWORD ??= randomBytes(12).toString('base64url') + 'Aa1';
process.env.E2E_VIEWER_EMAIL ??= 'e2e-viewer@example.com';
process.env.E2E_VIEWER_PASSWORD ??= randomBytes(12).toString('base64url') + 'Bb2';
process.env.E2E_DB_PATH ??= path.join(tmp, 'e2e.sqlite');
process.env.E2E_API_URL = `http://127.0.0.1:${API_PORT}`;
process.env.E2E_WEB_URL = `http://localhost:${WEB_PORT}`;

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  outputDir: 'test-results',
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    viewport: { width: 1440, height: 900 },
    // In CI keep a trace of every failing test (uploaded as an artifact); locally traces stay off.
    trace: process.env.CI ? 'retain-on-failure' : 'off',
  },
  webServer: [
    {
      command: `"${python}" scripts/e2e_server.py`,
      cwd: serverDir,
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      timeout: 60_000,
      reuseExistingServer: false,
      stdout: 'ignore',
      stderr: 'pipe',
      env: {
        JWT_SECRET: randomBytes(24).toString('hex'),
        E2E_VIEWER_EMAIL: process.env.E2E_VIEWER_EMAIL,
        E2E_VIEWER_PASSWORD: process.env.E2E_VIEWER_PASSWORD,
        ADMIN_EMAIL: process.env.E2E_ADMIN_EMAIL,
        ADMIN_PASSWORD: process.env.E2E_ADMIN_PASSWORD,
        E2E_DB_PATH: process.env.E2E_DB_PATH,
        E2E_DATA_DIR: path.join(tmp, 'data'),
        E2E_PORT: String(API_PORT),
        CORS_ORIGINS: `http://localhost:${WEB_PORT}`,
        PATH: process.env.PATH ?? '',
        SYSTEMROOT: process.env.SYSTEMROOT ?? '',
      },
    },
    {
      command: `npx vite --port ${WEB_PORT} --strictPort`,
      url: `http://localhost:${WEB_PORT}`,
      timeout: 60_000,
      reuseExistingServer: false,
      stdout: 'ignore',
      stderr: 'pipe',
      env: { VITE_API_URL: `http://127.0.0.1:${API_PORT}`, PATH: process.env.PATH ?? '', SYSTEMROOT: process.env.SYSTEMROOT ?? '' },
    },
  ],
});
