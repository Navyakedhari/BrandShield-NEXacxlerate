import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ProtectBrandPage } from './ProtectBrandPage';

function renderProtectBrand() {
  return render(
    <MemoryRouter initialEntries={['/protect-brand']}>
      <Routes>
        <Route path="/protect-brand" element={<ProtectBrandPage />} />
        <Route path="/setup" element={<div>EXISTING SETUP PAGE</div>} />
      </Routes>
    </MemoryRouter>
  );
}

describe('ProtectBrandPage', () => {
  it('renders "Protect Your Brand" heading and subtitle with BRANDSHIELD branding', () => {
    renderProtectBrand();

    expect(screen.getByRole('heading', { level: 1, name: /protect your brand/i })).toBeInTheDocument();
    expect(screen.getByText(/Choose how you want to get started with BrandShield AI/i)).toBeInTheDocument();
    expect(screen.getAllByText(/BRANDSHIELD/i).length).toBeGreaterThan(0);
  });

  it('renders "Create a Brand" as a UI-only card with no click navigation', async () => {
    renderProtectBrand();
    const user = userEvent.setup();

    // Check that Create a Brand is visible
    expect(screen.getByRole('heading', { name: /create a brand/i })).toBeInTheDocument();
    expect(screen.getByText(/Set up a new brand profile/i)).toBeInTheDocument();

    // The button inside is disabled
    const createBtn = screen.getByRole('button', { name: /create a brand/i });
    expect(createBtn).toBeDisabled();

    // Clicking it does nothing - should not navigate
    await user.click(createBtn);
    expect(screen.queryByText('EXISTING SETUP PAGE')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: /protect your brand/i })).toBeInTheDocument();
  });

  it('renders "Protect a Brand" and navigates to the existing setup page when clicked', async () => {
    renderProtectBrand();
    const user = userEvent.setup();

    // Check that Protect a Brand is visible
    expect(screen.getByRole('heading', { name: /protect a brand/i })).toBeInTheDocument();
    expect(screen.getByText(/Configure protection for an existing brand/i)).toBeInTheDocument();

    // Click Protect a Brand button
    const protectBtn = screen.getByRole('button', { name: /^protect a brand/i });
    await user.click(protectBtn);

    // Navigates to existing setup page
    expect(screen.getByText('EXISTING SETUP PAGE')).toBeInTheDocument();
  });
});
