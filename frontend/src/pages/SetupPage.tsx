import React, { useEffect, useRef, useState } from 'react';
import { BrandMark, Wordmark } from '../components/common/BrandMark';
import { NavLink, useNavigate } from 'react-router-dom';
import { ShieldCheck } from 'lucide-react';
import { ApiError } from '../api/client';
import { fmtBytes, sourceStatusLabel } from '../api/format';
import { sm } from '../api/sm';
import { ErrorBox, Loading, Notice } from '../components/common/States';
import { useApi } from '../hooks/useApi';
import { refreshBrand, setBrandCache, useBrand } from '../hooks/useBrand';
import { checkLogoFile } from '../utils/logoCheck';
import './SetupPage.css';

export const SetupPage: React.FC = () => {
  const navigate = useNavigate();
  const { brand, loading: brandLoading } = useBrand();
  const [step, setStep] = useState(1);
  const [companyName, setCompanyName] = useState('');
  const [aliasesText, setAliasesText] = useState('');
  const [prefilled, setPrefilled] = useState(false);
  const [pendingLogo, setPendingLogo] = useState<File | null>(null);
  const [logoError, setLogoError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  // An existing brand is edited, not duplicated (one brand per workspace).
  useEffect(() => {
    if (brand && !prefilled) {
      setCompanyName(brand.name);
      setAliasesText(brand.aliases.join(', '));
      setPrefilled(true);
    }
  }, [brand, prefilled]);

  const sources = useApi((signal) => sm.sources(signal), [], step === 3);

  const pickLogo = async (file: File | undefined) => {
    setLogoError(null);
    if (!file) return;
    const res = await checkLogoFile(file);
    if (!res.ok) {
      setPendingLogo(null);
      setLogoError(res.error);
      return;
    }
    setPendingLogo(file);
  };

  /** Step 1: create the brand (or update the existing one) and upload the chosen logo. */
  const saveBrand = async (): Promise<boolean> => {
    const name = companyName.trim();
    if (!name) {
      setError('Enter your company or brand name.');
      return false;
    }
    const aliases = aliasesText
      .split(',')
      .map((a) => a.trim())
      .filter(Boolean);
    setBusy(true);
    setError(null);
    try {
      let saved = brand
        ? await sm.updateBrand(brand.id, { name, aliases, industry: brand.industry, keywords: brand.keywords, domains: brand.domains })
        : await sm.createBrand({ name, aliases });
      if (pendingLogo) {
        await sm.uploadLogo(saved.id, pendingLogo);
        saved = await sm.brand(saved.id);
        setPendingLogo(null);
      }
      setBrandCache(saved);
      return true;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not save the brand.');
      return false;
    } finally {
      setBusy(false);
    }
  };

  const launch = async () => {
    setBusy(true);
    setError(null);
    try {
      await sm.startScan('all');
    } catch (e) {
      // 409 = a scan is already running: nothing to do, the dashboard follows it.
      if (!(e instanceof ApiError && e.status === 409)) {
        setError(`${e instanceof ApiError ? e.message : 'The scan could not be started.'} You can start it from the dashboard.`);
        setBusy(false);
        return;
      }
    }
    void refreshBrand();
    navigate('/dashboard');
  };

  const handleContinue = async () => {
    if (step === 1) {
      if (await saveBrand()) setStep(2);
    } else if (step === 2) {
      setStep(3);
    } else {
      await launch();
    }
  };

  const logo = brand?.logo ?? null;

  return (
    <div className="setup-container">
      {/* Top Bar */}
      <header className="setup-topbar">
        <div className="setup-logo">
          <BrandMark />
          <span className="setup-logo-text"><Wordmark /></span>
        </div>
        <NavLink to="/dashboard" className="skip-link">
          Skip setup for now
        </NavLink>
      </header>

      {/* Hero Header */}
      <div className="setup-hero">
        <h1 className="setup-title">Set up brand protection in three steps</h1>
        <p className="setup-subtitle">
          Tell us what is official. We will find everything that is not.
        </p>

        {/* Stepper */}
        <div className="setup-stepper">
          <div className={`step-item ${step === 1 ? 'active' : step > 1 ? 'completed' : ''}`}>
            <span className="step-num">1</span>
            <span className="step-label">Brand</span>
          </div>
          <div className={`step-item ${step === 2 ? 'active' : step > 2 ? 'completed' : ''}`}>
            <span className="step-num">2</span>
            <span className="step-label">Official assets</span>
          </div>
          <div className={`step-item ${step === 3 ? 'active' : ''}`}>
            <span className="step-num">3</span>
            <span className="step-label">First scan</span>
          </div>
        </div>
      </div>

      {/* Main Dual-Card Layout */}
      <div className="setup-card-grid">
        {/* Left Side: Step Details */}
        <div className="setup-info-card">
          <div className="step-badge">STEP {step} OF 3</div>
          <h2 className="step-heading">
            {step === 1 && 'Name your brand'}
            {step === 2 && 'List official assets'}
            {step === 3 && 'Start your first scan'}
          </h2>
          <p className="step-description">
            {step === 1 &&
              'Your name and logo are what impersonators copy. We use them to spot profiles, pages and apps that look like yours.'}
            {step === 2 &&
              'Add your official social accounts, app packages and domains so our engine never flags your genuine presence.'}
            {step === 3 &&
              'Scan all supported social platforms and app marketplaces to uncover live impostors and unauthorized listings.'}
          </p>

          <div className="step-callout">
            <ShieldCheck size={20} className="callout-icon" />
            <span>Anything you list as official is never flagged.</span>
          </div>
        </div>

        {/* Right Side: Step Form */}
        <div className="setup-form-card">
          {step === 1 && (
            <div className="step-form-content">
              <div className="setup-field">
                <label htmlFor="companyName" className="setup-label">
                  Company or brand name
                </label>
                <input
                  id="companyName"
                  type="text"
                  className="setup-input"
                  value={companyName}
                  onChange={(e) => setCompanyName(e.target.value)}
                  placeholder="Acme Bank"
                />
              </div>

              <div className="setup-field">
                <label htmlFor="aliases" className="setup-label">
                  Other names people use (optional)
                </label>
                <input
                  id="aliases"
                  type="text"
                  className="setup-input"
                  value={aliasesText}
                  onChange={(e) => setAliasesText(e.target.value)}
                  placeholder="Acme, AcmeBank, Acme Bank India"
                />
              </div>

              <div className="logo-upload-box">
                <div className="logo-preview-badge">{(companyName || brand?.name || 'A').charAt(0).toUpperCase()}</div>
                <div className="logo-preview-info">
                  <div className="logo-status-title">
                    {pendingLogo ? 'New logo selected' : logo ? 'Official logo uploaded' : 'No official logo yet'}
                  </div>
                  <div className="logo-meta">
                    {pendingLogo
                      ? `${pendingLogo.name} · ${fmtBytes(pendingLogo.size)} · uploaded when you continue`
                      : logo
                      ? `✓ ${logo.format.toUpperCase()} · ${logo.width} × ${logo.height} · ${fmtBytes(logo.size_bytes)} · ${logo.square ? 'square' : 'not square'}`
                      : 'PNG, JPEG or WebP, at least 256 × 256, roughly square, max 2 MB'}
                  </div>
                </div>
                <input
                  ref={fileInput}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  hidden
                  data-testid="setup-logo-input"
                  onChange={(e) => {
                    void pickLogo(e.target.files?.[0]);
                    e.target.value = '';
                  }}
                />
                <button
                  type="button"
                  className="btn-secondary btn-sm replace-btn"
                  onClick={() => fileInput.current?.click()}
                >
                  {logo || pendingLogo ? 'Replace' : 'Upload'}
                </button>
              </div>
              {logoError && <Notice>{logoError}</Notice>}
            </div>
          )}

          {step === 2 && (
            <div className="step-form-content">
              <div className="setup-field">
                <label className="setup-label">Official Accounts Verified</label>
                <div className="mini-account-pill-list">
                  {(brand?.official_accounts ?? []).map((a) => (
                    <span key={a.id} className="mini-pill">{a.handle} ({a.platform})</span>
                  ))}
                  {!brand?.official_accounts.length && <span className="mini-pill">None added yet</span>}
                </div>
              </div>
              <div className="setup-field">
                <label className="setup-label">Official Mobile Apps</label>
                <div className="mini-account-pill-list">
                  {(brand?.official_apps ?? []).map((a) => (
                    <span key={a.id} className="mini-pill">{a.name} ({a.package_id})</span>
                  ))}
                  {!brand?.official_apps.length && <span className="mini-pill">None added yet</span>}
                </div>
              </div>
              <p style={{ color: 'var(--text-muted)', fontSize: '12.5px' }}>
                Add or remove official accounts and apps on the <NavLink to="/brand">Brand Profile</NavLink> page.
              </p>
            </div>
          )}

          {step === 3 && (
            <div className="step-form-content">
              <div className="setup-scan-preview">
                {sources.loading && <Loading what="Checking sources" />}
                {sources.error && <ErrorBox error={sources.error} onRetry={sources.reload} compact />}
                {sources.data && (
                  <>
                    <p style={{ color: 'var(--text-muted)', fontSize: '13.5px', marginBottom: '14px' }}>
                      Ready to scan {sources.data.summary.total} sources including{' '}
                      {sources.data.items.slice(0, 5).map((s) => s.name).join(', ')}.
                    </p>
                    <div className="setup-scan-sources">
                      {sources.data.items.slice(0, 3).map((s) => (
                        <span key={s.id} className="source-dot-pill">{s.name} {sourceStatusLabel(s.status)}</span>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
          )}

          {error && <Notice>{error}</Notice>}
          <div className="setup-action-footer">
            <button
              type="button"
              className="btn-primary continue-btn"
              onClick={() => void handleContinue()}
              disabled={busy || (step === 1 && brandLoading)}
            >
              {busy ? (step === 3 ? 'Starting scan…' : 'Saving…') : step < 3 ? 'Continue' : 'Launch Protection Dashboard'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
