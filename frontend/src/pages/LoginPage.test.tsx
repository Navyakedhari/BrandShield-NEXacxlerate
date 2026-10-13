import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { LoginPage } from './LoginPage';
import { jsonResponse, mockFetch, USER } from '../test/helpers';

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/dashboard" element={<div>DASHBOARD PAGE</div>} />
      </Routes>
    </MemoryRouter>
  );
}

async function fillAndSubmit(email = 'a@b.co', password = 'secret-pass') {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText('Work email'), email);
  await user.type(screen.getByLabelText('Password'), password);
  await user.click(screen.getByRole('button', { name: /sign in/i }));
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());

describe('LoginPage', () => {
  it('shows "Incorrect email or password" on 401 and does not enter the app (no offline bypass)', async () => {
    mockFetch(() => jsonResponse(401, { detail: 'Incorrect email or password' }));
    renderLogin();
    await fillAndSubmit();
    expect(await screen.findByRole('alert')).toHaveTextContent('Incorrect email or password');
    expect(screen.queryByText('DASHBOARD PAGE')).not.toBeInTheDocument();
    expect(localStorage.getItem('scammind.auth')).toBeNull();
    expect(localStorage.getItem('scammind.offline-session')).toBeNull();
    expect(sessionStorage.length).toBe(0);
  });

  it('shows the rate-limit message on 429', async () => {
    mockFetch(() => jsonResponse(429, { detail: 'Too many failed attempts. Try again later.' }, { 'Retry-After': '900' }));
    renderLogin();
    await fillAndSubmit();
    expect(await screen.findByRole('alert')).toHaveTextContent('Too many failed attempts. Try again later.');
    expect(screen.queryByText('DASHBOARD PAGE')).not.toBeInTheDocument();
  });

  it('shows a network error when the server cannot be reached', async () => {
    mockFetch(() => {
      throw new TypeError('Failed to fetch');
    });
    renderLogin();
    await fillAndSubmit();
    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot reach the server');
    expect(screen.queryByText('DASHBOARD PAGE')).not.toBeInTheDocument();
  });

  it('shows 422 validation messages from the server', async () => {
    mockFetch(() => jsonResponse(422, { detail: [{ loc: ['body', 'email'], msg: 'value is not a valid email address', type: 'value_error' }] }));
    renderLogin();
    await fillAndSubmit('a@b.co', 'x');
    expect(await screen.findByRole('alert')).toHaveTextContent('value is not a valid email address');
  });

  it('signs in, stores the session under scammind.auth and opens the dashboard', async () => {
    const { calls } = mockFetch(() =>
      jsonResponse(200, { access_token: 'acc', refresh_token: 'ref', token_type: 'bearer', expires_in: 900, user: USER })
    );
    renderLogin();
    await fillAndSubmit('me@example.test', 'pw-12345');
    await waitFor(() => expect(screen.getByText('DASHBOARD PAGE')).toBeInTheDocument());
    expect(JSON.parse(calls[0].init.body as string)).toEqual({ email: 'me@example.test', password: 'pw-12345' });
    expect(JSON.parse(localStorage.getItem('scammind.auth') as string)).toEqual({ access: 'acc', refresh: 'ref' });
  });

  it('keep-me-signed-in off keeps the session in sessionStorage only', async () => {
    mockFetch(() => jsonResponse(200, { access_token: 'acc', refresh_token: 'ref', token_type: 'bearer', expires_in: 900, user: USER }));
    renderLogin();
    await userEvent.setup().click(screen.getByRole('checkbox'));
    await fillAndSubmit();
    await waitFor(() => expect(screen.getByText('DASHBOARD PAGE')).toBeInTheDocument());
    expect(localStorage.getItem('scammind.auth')).toBeNull();
    expect(sessionStorage.getItem('scammind.auth')).not.toBeNull();
  });
});
