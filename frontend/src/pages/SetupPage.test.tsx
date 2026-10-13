import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { SetupPage } from './SetupPage';
import { jsonResponse, mockFetch, signIn, USER } from '../test/helpers';
import { resetBrand } from '../hooks/useBrand';

const BRAND = {
  id: 1,
  name: 'Acme Security',
  aliases: ['Acme', 'AcmeSec'],
  industry: 'Software, SaaS & Technology',
  keywords: [],
  domains: ['acme.com'],
  official_assets: 1,
  official_accounts: [
    { id: 10, platform: 'x', handle: '@acmesec', url: 'https://x.com/acmesec', status: 'active', source: 'manual' },
  ],
  official_apps: [
    { id: 20, name: 'Acme Authenticator', package_id: 'com.acme.auth', publisher: 'Acme Sec', stores: ['google_play'], status: 'active', source: 'manual' },
  ],
  logo: null,
};

const SOURCES_RESPONSE = {
  summary: { total: 4, healthy: 4, degraded: 0, unavailable: 0 },
  items: [
    { id: 'instagram', name: 'Instagram', status: 'healthy', kind: 'social' },
    { id: 'x', name: 'X (Twitter)', status: 'healthy', kind: 'social' },
    { id: 'google_play', name: 'Google Play', status: 'healthy', kind: 'app_store' },
    { id: 'app_store', name: 'Apple App Store', status: 'healthy', kind: 'app_store' },
  ],
};

function renderSetup() {
  return render(
    <MemoryRouter initialEntries={['/setup']}>
      <Routes>
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/dashboard" element={<div>DASHBOARD PAGE</div>} />
        <Route path="/brand" element={<div>BRAND PAGE</div>} />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetBrand();
  signIn();
});

afterEach(() => vi.unstubAllGlobals());

describe('SetupPage', () => {
  it('renders Step 1 with brand fields and validates empty company name', async () => {
    mockFetch((call) => {
      if (call.url.includes('/api/me')) {
        return jsonResponse(200, { ...USER, default_brand_id: null });
      }
      if (call.url.includes('/api/brands')) {
        return jsonResponse(200, []);
      }
      return jsonResponse(404, { detail: 'Not found' });
    });

    renderSetup();

    expect(screen.getByText(/Set up brand protection in three steps/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Company or brand name/i)).toBeInTheDocument();

    const user = userEvent.setup();
    const continueBtn = await screen.findByRole('button', { name: /continue/i });
    await waitFor(() => expect(continueBtn).not.toBeDisabled());
    await user.click(continueBtn);

    expect(await screen.findByRole('alert')).toHaveTextContent(/Company or brand name is required/i);
  });

  it('submits Step 1 and advances to Step 2', async () => {
    mockFetch((call) => {
      if (call.url.includes('/api/me')) {
        return jsonResponse(200, { ...USER, default_brand_id: null });
      }
      if (call.url.includes('/api/brands') && call.init.method === 'POST') {
        return jsonResponse(201, BRAND);
      }
      if (call.url.includes('/api/brands')) {
        return jsonResponse(200, []);
      }
      return jsonResponse(404, { detail: 'Not found' });
    });

    renderSetup();
    const user = userEvent.setup();

    const nameInput = await screen.findByLabelText(/Company or brand name/i);
    await user.type(nameInput, 'Acme Security');

    const continueBtn = screen.getByRole('button', { name: /continue/i });
    await user.click(continueBtn);

    // Should transition to Step 2
    expect(await screen.findByText(/Whitelist official assets/i)).toBeInTheDocument();
    expect(screen.getByText(/Official Social Profiles/i)).toBeInTheDocument();
  });

  it('allows navigating through steps, viewing sources on Step 3, and launching scan', async () => {
    mockFetch((call) => {
      if (call.url.includes('/api/me')) {
        return jsonResponse(200, USER);
      }
      if (call.url.includes('/api/brands/1')) {
        return jsonResponse(200, BRAND);
      }
      if (call.url.includes('/api/brands') && call.init.method === 'PUT') {
        return jsonResponse(200, BRAND);
      }
      if (call.url.includes('/api/brands')) {
        return jsonResponse(200, [BRAND]);
      }
      if (call.url.includes('/api/sources')) {
        return jsonResponse(200, SOURCES_RESPONSE);
      }
      if (call.url.includes('/api/scan') && call.init.method === 'POST') {
        return jsonResponse(202, { job_id: 'job-123', status: 'started' });
      }
      return jsonResponse(200, {});
    });

    renderSetup();
    const user = userEvent.setup();

    // Wait for brand to populate into the input
    await screen.findByDisplayValue('Acme Security');

    // In step 1: click Continue
    const continueBtn1 = screen.getByRole('button', { name: /continue/i });
    await user.click(continueBtn1);

    // In step 2: click Continue to Step 3
    const continueBtn2 = await screen.findByRole('button', { name: /continue/i });
    await user.click(continueBtn2);

    // In step 3: sources should be loaded
    expect(await screen.findByText(/Configure & start scan/i)).toBeInTheDocument();
    expect(await screen.findByText(/4 intelligence sources active and online/i)).toBeInTheDocument();

    // Click Launch Protection Dashboard
    const launchBtn = screen.getByRole('button', { name: /launch protection dashboard/i });
    await user.click(launchBtn);

    // Should redirect to Dashboard
    await waitFor(() => {
      expect(screen.getByText('DASHBOARD PAGE')).toBeInTheDocument();
    });
  });

  it('supports back button navigation from Step 2 to Step 1', async () => {
    mockFetch((call) => {
      if (call.url.includes('/api/me')) {
        return jsonResponse(200, USER);
      }
      if (call.url.includes('/api/brands/1')) {
        return jsonResponse(200, BRAND);
      }
      if (call.url.includes('/api/brands') && call.init.method === 'PUT') {
        return jsonResponse(200, BRAND);
      }
      if (call.url.includes('/api/brands')) {
        return jsonResponse(200, [BRAND]);
      }
      return jsonResponse(200, {});
    });

    renderSetup();
    const user = userEvent.setup();

    // Wait for brand to populate
    await screen.findByDisplayValue('Acme Security');

    // Continue to Step 2
    const continueBtn = screen.getByRole('button', { name: /continue/i });
    await user.click(continueBtn);
    expect(await screen.findByText(/Whitelist official assets/i)).toBeInTheDocument();

    // Click Back
    const backBtn = screen.getByRole('button', { name: /back/i });
    await user.click(backBtn);

    // Should be back on Step 1
    expect(screen.getByText(/Define your brand/i)).toBeInTheDocument();
  });
});
