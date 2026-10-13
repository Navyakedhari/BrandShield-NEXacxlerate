import React, { useEffect, useRef, useState } from 'react';
import { BrandMark, Wordmark } from '../components/common/BrandMark';
import { NavLink, useNavigate } from 'react-router-dom';
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  CircleCheck,
  Image,
  Info,
  Lock,
  Plus,
  Rocket,
  Shield,
  ShieldCheck,
  Smartphone,
  Trash2,
  Users,
  Zap,
} from 'lucide-react';
import { ApiError } from '../api/client';
import { fmtBytes, sourceStatusLabel } from '../api/format';
import { sm } from '../api/sm';
import type { OfficialAccount, OfficialApp, ScanScope } from '../api/types';
import { ErrorBox, Loading, Notice } from '../components/common/States';
import { useApi } from '../hooks/useApi';
import { refreshBrand, setBrandCache, useBrand } from '../hooks/useBrand';
import { checkLogoFile } from '../utils/logoCheck';
import './SetupPage.css';

const STEP_META = [
  {
    label: 'Brand Identity',
    heading: 'Define your brand',
    description:
      'Impersonators hijack your logos, corporate identity, and trademarked names. We use these identifiers to detect fraudulent domains, pages, and apps.',
    tip: 'Use the official legal or trademarked name for the highest scoring precision.',
    icon: Shield,
  },
  {
    label: 'Official Assets',
    heading: 'Whitelist official assets',
    description:
      'Register your genuine social accounts and verified mobile applications. Our neural engine cryptographically excludes them from threat alerts.',
    tip: 'You can add, edit, or import additional brand assets anytime from the Brand Profile page.',
    icon: CircleCheck,
  },
  {
    label: 'Engine & First Scan',
    heading: 'Configure & start scan',
    description:
      'Verify active detection vectors and initiate your baseline threat intelligence scan across global social platforms and app marketplaces.',
    tip: 'Initial detection candidates will populate on your dashboard within minutes of launch.',
    icon: Rocket,
  },
];

const INDUSTRY_OPTIONS = [
  'Financial Services & Banking',
  'E-Commerce & Retail',
  'Software, SaaS & Technology',
  'Healthcare & Pharmaceuticals',
  'Telecommunications',
  'Media & Entertainment',
  'Logistics & Supply Chain',
  'Government & Public Sector',
  'Other Enterprise',
];

const PLATFORM_OPTIONS = [
  { value: 'instagram', label: 'Instagram' },
  { value: 'x', label: 'X (Twitter)' },
  { value: 'facebook', label: 'Facebook' },
  { value: 'linkedin', label: 'LinkedIn' },
  { value: 'youtube', label: 'YouTube' },
];

export const SetupPage: React.FC = () => {
  const navigate = useNavigate();
  const { brand, loading: brandLoading } = useBrand();

  const [step, setStep] = useState(1);
  const [maxStepVisited, setMaxStepVisited] = useState(1);

  // Step 1: Brand info
  const [companyName, setCompanyName] = useState('');
  const [industry, setIndustry] = useState('');
  const [aliasesText, setAliasesText] = useState('');
  const [domainsText, setDomainsText] = useState('');
  const [pendingLogo, setPendingLogo] = useState<File | null>(null);
  const [logoError, setLogoError] = useState<string | null>(null);
  const [nameTouched, setNameTouched] = useState(false);

  // Step 2: Assets addition
  const [accPlatform, setAccPlatform] = useState('instagram');
  const [accHandle, setAccHandle] = useState('');
  const [accBusy, setAccBusy] = useState(false);
  const [accError, setAccError] = useState<string | null>(null);

  const [showAddApp, setShowAddApp] = useState(false);
  const [appName, setAppName] = useState('');
  const [appPackage, setAppPackage] = useState('');
  const [appPublisher, setAppPublisher] = useState('');
  const [appStore, setAppStore] = useState<'google_play' | 'app_store'>('google_play');
  const [appBusy, setAppBusy] = useState(false);
  const [appError, setAppError] = useState<string | null>(null);

  // Step 3: Scan scope
  const [scanScope, setScanScope] = useState<ScanScope>('all');

  // General states
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const loadedBrandId = useRef<number | null>(null);

  // Pre-fill from existing brand if re-visiting
  useEffect(() => {
    if (brand && loadedBrandId.current !== brand.id) {
      loadedBrandId.current = brand.id;
      setCompanyName(brand.name || '');
      setAliasesText((brand.aliases || []).join(', '));
      setIndustry(brand.industry || '');
      setDomainsText((brand.domains || []).join(', '));
    }
  }, [brand]);

  // Load sources for step 3
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

  /** Step 1: Save or update brand details */
  const saveBrand = async (): Promise<boolean> => {
    const name = companyName.trim();
    if (!name) {
      setNameTouched(true);
      setError('Company or brand name is required.');
      return false;
    }

    const aliases = aliasesText
      .split(',')
      .map((a) => a.trim())
      .filter(Boolean);

    const domains = domainsText
      .split(',')
      .map((d) => d.trim().toLowerCase().replace(/^https?:\/\//, '').replace(/\/.*$/, ''))
      .filter(Boolean);

    setBusy(true);
    setError(null);

    try {
      let saved = brand
        ? await sm.updateBrand(brand.id, {
            name,
            aliases,
            industry: industry.trim(),
            keywords: brand.keywords || [],
            domains,
            description: brand.description ?? undefined,
          })
        : await sm.createBrand({
            name,
            aliases,
            industry: industry.trim(),
            domains,
          });

      if (pendingLogo) {
        await sm.uploadLogo(saved.id, pendingLogo);
        saved = await sm.brand(saved.id);
        setPendingLogo(null);
      }

      setBrandCache(saved);
      return true;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not save brand identity settings.');
      return false;
    } finally {
      setBusy(false);
    }
  };

  /** Step 2: Add official account inline */
  const handleAddAccount = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!brand) return;
    const handle = accHandle.trim();
    if (!handle) {
      setAccError('Enter an account handle or profile name.');
      return;
    }

    setAccBusy(true);
    setAccError(null);
    try {
      await sm.addAccount(brand.id, accPlatform, handle);
      const updated = await sm.brand(brand.id);
      setBrandCache(updated);
      setAccHandle('');
    } catch (e) {
      setAccError(e instanceof ApiError ? e.message : 'Could not add this account.');
    } finally {
      setAccBusy(false);
    }
  };

  /** Step 2: Remove official account */
  const handleRemoveAccount = async (accountId: number) => {
    if (!brand) return;
    setBusy(true);
    try {
      await sm.removeAccount(brand.id, accountId);
      const updated = await sm.brand(brand.id);
      setBrandCache(updated);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not remove this account.');
    } finally {
      setBusy(false);
    }
  };

  /** Step 2: Add official app inline */
  const handleAddApp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!brand) return;
    const name = appName.trim();
    const pkg = appPackage.trim();
    const pub = appPublisher.trim();

    if (!name || !pkg || !pub) {
      setAppError('App name, package/bundle ID, and publisher name are required.');
      return;
    }

    setAppBusy(true);
    setAppError(null);
    try {
      await sm.addApp(brand.id, {
        name,
        package_id: pkg,
        publisher: pub,
        stores: [appStore],
      });
      const updated = await sm.brand(brand.id);
      setBrandCache(updated);
      setAppName('');
      setAppPackage('');
      setAppPublisher('');
      setShowAddApp(false);
    } catch (e) {
      setAppError(e instanceof ApiError ? e.message : 'Could not add this app.');
    } finally {
      setAppBusy(false);
    }
  };

  /** Step 2: Remove official app */
  const handleRemoveApp = async (appId: number) => {
    if (!brand) return;
    setBusy(true);
    try {
      await sm.removeApp(brand.id, appId);
      const updated = await sm.brand(brand.id);
      setBrandCache(updated);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not remove this app.');
    } finally {
      setBusy(false);
    }
  };

  /** Step 3: Launch baseline protection scan */
  const launchProtection = async () => {
    setBusy(true);
    setError(null);
    try {
      await sm.startScan(scanScope);
    } catch (e) {
      // 409 indicates scan already in progress — proceed to dashboard to observe live results
      if (!(e instanceof ApiError && e.status === 409)) {
        setError(
          `${e instanceof ApiError ? e.message : 'The scan could not be initiated.'} You can launch it from the dashboard.`
        );
        setBusy(false);
        return;
      }
    }
    void refreshBrand();
    navigate('/dashboard');
  };

  const handleContinue = async () => {
    setError(null);
    if (step === 1) {
      const ok = await saveBrand();
      if (ok) {
        setStep(2);
        setMaxStepVisited((prev) => Math.max(prev, 2));
      }
    } else if (step === 2) {
      setStep(3);
      setMaxStepVisited((prev) => Math.max(prev, 3));
    } else {
      await launchProtection();
    }
  };

  const handleBack = () => {
    if (step > 1) {
      setStep(step - 1);
      setError(null);
    }
  };

  const goToStep = (s: number) => {
    if (s <= maxStepVisited && s !== step) {
      setStep(s);
      setError(null);
    }
  };

  const meta = STEP_META[step - 1];
  const logo = brand?.logo ?? null;
  const progressPct = step === 1 ? 25 : step === 2 ? 65 : 100;
  const nameInvalid = nameTouched && companyName.trim() === '';

  const officialAccounts: OfficialAccount[] = brand?.official_accounts ?? [];
  const officialApps: OfficialApp[] = brand?.official_apps ?? [];

  return (
    <div className="setup-container">
      {/* ── Top Bar ── */}
      <header className="setup-topbar">
        <div className="setup-logo">
          <BrandMark size={28} />
          <span className="setup-logo-text"><Wordmark /></span>
          <span className="setup-env-chip">INITIAL SETUP WIZARD</span>
        </div>
        <NavLink to="/dashboard" className="skip-link" title="Skip setup and go directly to dashboard">
          <span>Skip for now</span>
          <ChevronRight size={14} />
        </NavLink>
      </header>

      {/* ── Hero & Stepper ── */}
      <div className="setup-hero">
        <h1 className="setup-title">Set up brand protection in three steps</h1>
        <p className="setup-subtitle">
          Define your authentic presence. BrandShield will monitor global networks to detect and neutralize impostors.
        </p>

        {/* Progress Bar */}
        <div
          className="setup-progress-track"
          role="progressbar"
          aria-valuenow={step}
          aria-valuemin={1}
          aria-valuemax={3}
        >
          <div className="setup-progress-fill" style={{ width: `${progressPct}%` }} />
        </div>

        {/* Stepper Navigation */}
        <nav className="setup-stepper" aria-label="Setup steps navigation">
          {STEP_META.map((s, i) => {
            const n = i + 1;
            const isActive = n === step;
            const isCompleted = n < step;
            const isClickable = n <= maxStepVisited;

            return (
              <React.Fragment key={n}>
                {i > 0 && <div className="step-connector" aria-hidden="true" />}
                <button
                  type="button"
                  onClick={() => goToStep(n)}
                  disabled={!isClickable}
                  className={`step-item ${isActive ? 'active' : ''} ${isCompleted ? 'completed' : ''} ${
                    isClickable ? 'clickable' : ''
                  }`}
                  aria-current={isActive ? 'step' : undefined}
                >
                  <span className="step-num">
                    {isCompleted ? <CheckCircle2 size={13} strokeWidth={2.5} /> : n}
                  </span>
                  <span className="step-label">{s.label}</span>
                </button>
              </React.Fragment>
            );
          })}
        </nav>
      </div>

      {/* ── Main Dual-Card Grid ── */}
      <div className="setup-card-grid">
        {/* Left Column: Contextual Guidance Card */}
        <aside className="setup-info-card">
          <div className="step-badge">
            <span className="step-badge-dot" aria-hidden="true" />
            STEP {step} OF 3
          </div>
          <h2 className="step-heading">{meta.heading}</h2>
          <p className="step-description">{meta.description}</p>

          <div className="step-callout">
            <ShieldCheck size={18} className="callout-icon" />
            <div>
              <strong>Ground-Truth Protection</strong>
              <span>Assets added to your verified profile are never flagged by automated detections.</span>
            </div>
          </div>

          <div className="step-tip-list">
            <div className="step-tip-item">
              <Zap size={14} className="step-tip-icon" />
              <span>{meta.tip}</span>
            </div>
          </div>

          {/* Checklist of configuration readiness */}
          <div className="setup-checklist-box">
            <div className="checklist-title">Setup Progress</div>
            <div className={`checklist-row ${companyName.trim() ? 'done' : ''}`}>
              <CheckCircle2 size={14} className="chk-icon" />
              <span>Brand Identity</span>
            </div>
            <div className={`checklist-row ${officialAccounts.length > 0 || officialApps.length > 0 ? 'done' : ''}`}>
              <CheckCircle2 size={14} className="chk-icon" />
              <span>Official Assets ({officialAccounts.length + officialApps.length})</span>
            </div>
            <div className={`checklist-row ${sources.data ? 'done' : ''}`}>
              <CheckCircle2 size={14} className="chk-icon" />
              <span>Intelligence Sources</span>
            </div>
          </div>
        </aside>

        {/* Right Column: Interactive Configuration Form */}
        <div className="setup-form-card">
          {/* ════════════ STEP 1: BRAND IDENTITY ════════════ */}
          {step === 1 && (
            <div className="step-form-content">
              <div className="form-section-header">
                <Shield size={18} className="section-header-icon" />
                <div>
                  <h3 className="form-section-headline">Brand Identity Configuration</h3>
                  <p className="form-section-sub">
                    Configure official trademark names, industry sector, and visual identity.
                  </p>
                </div>
              </div>

              {/* Company / Brand Name */}
              <div className="setup-field">
                <label htmlFor="companyName" className="setup-label">
                  Company or brand name <span className="required-dot" aria-hidden="true">*</span>
                </label>
                <input
                  id="companyName"
                  type="text"
                  className={`setup-input${nameInvalid ? ' field-error' : ''}`}
                  value={companyName}
                  onChange={(e) => {
                    setCompanyName(e.target.value);
                    setError(null);
                  }}
                  onBlur={() => setNameTouched(true)}
                  placeholder="e.g. Acme Corporation"
                  aria-required="true"
                  aria-invalid={nameInvalid}
                  aria-describedby={nameInvalid ? 'company-name-error' : undefined}
                />
                {nameInvalid && (
                  <span id="company-name-error" className="setup-field-error-text">
                    <AlertCircle size={12} />
                    Company or brand name is required.
                  </span>
                )}
                <span className="setup-helper">
                  The primary brand name as registered in trademarks and corporate filings.
                </span>
              </div>

              {/* Industry Selector */}
              <div className="setup-field">
                <label htmlFor="industry" className="setup-label">
                  Industry sector
                  <span className="optional-tag">(recommended)</span>
                </label>
                <select
                  id="industry"
                  className="setup-select"
                  value={industry}
                  onChange={(e) => setIndustry(e.target.value)}
                >
                  <option value="">Select industry vertical…</option>
                  {INDUSTRY_OPTIONS.map((ind) => (
                    <option key={ind} value={ind}>
                      {ind}
                    </option>
                  ))}
                </select>
                <span className="setup-helper">
                  Calibrates heuristic scoring weights to industry-specific threat patterns.
                </span>
              </div>

              {/* Aliases & Abbreviations */}
              <div className="setup-field">
                <label htmlFor="aliases" className="setup-label">
                  Brand aliases &amp; abbreviations
                  <span className="optional-tag">(optional)</span>
                </label>
                <input
                  id="aliases"
                  type="text"
                  className="setup-input"
                  value={aliasesText}
                  onChange={(e) => setAliasesText(e.target.value)}
                  placeholder="Acme, AcmeBank, Acme Global, AcmeHQ"
                />
                <span className="setup-helper">
                  Comma-separated short names or common acronyms used by customers.
                </span>
              </div>

              {/* Official Web Domains */}
              <div className="setup-field">
                <label htmlFor="domains" className="setup-label">
                  Primary web domains
                  <span className="optional-tag">(optional)</span>
                </label>
                <input
                  id="domains"
                  type="text"
                  className="setup-input"
                  value={domainsText}
                  onChange={(e) => setDomainsText(e.target.value)}
                  placeholder="acme.com, acmecorp.io"
                />
                <span className="setup-helper">
                  Comma-separated legitimate web addresses to prevent typosquatting false-alarms.
                </span>
              </div>

              {/* Official Logo Upload */}
              <div className="setup-field">
                <div className="setup-label" id="logo-upload-label">
                  Official logo asset
                  <span className="optional-tag">(optional)</span>
                </div>
                <div className="logo-upload-box" aria-labelledby="logo-upload-label">
                  <div className="logo-preview-badge" aria-hidden="true">
                    {pendingLogo ? (
                      <Image size={28} strokeWidth={1.5} />
                    ) : (
                      (companyName || brand?.name || 'A').charAt(0).toUpperCase()
                    )}
                  </div>
                  <div className="logo-preview-info">
                    <div className="logo-status-title">
                      {pendingLogo
                        ? 'New logo selected'
                        : logo
                        ? 'Official logo uploaded'
                        : 'No official logo uploaded'}
                    </div>
                    <div className={`logo-meta${!pendingLogo && !logo ? ' neutral' : ''}`}>
                      {pendingLogo
                        ? `${pendingLogo.name} · ${fmtBytes(pendingLogo.size)} · will be uploaded upon clicking continue`
                        : logo
                        ? `✓ ${logo.format.toUpperCase()} · ${logo.width} × ${logo.height} px · ${fmtBytes(logo.size_bytes)}`
                        : 'PNG, JPEG or WebP · min 256 × 256 px · roughly square · max 2 MB'}
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
                    {logo || pendingLogo ? 'Replace logo' : 'Upload logo'}
                  </button>
                </div>
                {logoError && <Notice>{logoError}</Notice>}
              </div>
            </div>
          )}

          {/* ════════════ STEP 2: OFFICIAL ASSETS WHITELIST ════════════ */}
          {step === 2 && (
            <div className="step-form-content">
              <div className="form-section-header">
                <CircleCheck size={18} className="section-header-icon" />
                <div>
                  <h3 className="form-section-headline">Official Assets &amp; Channels</h3>
                  <p className="form-section-sub">
                    Register all legitimate accounts and mobile apps. Any asset added here is guaranteed never to be flagged.
                  </p>
                </div>
              </div>

              {/* 1. Official Social Media Accounts */}
              <div className="setup-sub-section">
                <div className="sub-section-title-row">
                  <div className="sub-section-title">
                    <Users size={16} />
                    <span>Official Social Profiles ({officialAccounts.length})</span>
                  </div>
                </div>

                {/* Add Account Inline Form */}
                <form onSubmit={handleAddAccount} className="inline-add-row" noValidate>
                  <select
                    className="inline-select"
                    value={accPlatform}
                    onChange={(e) => setAccPlatform(e.target.value)}
                    disabled={accBusy}
                    aria-label="Social platform"
                  >
                    {PLATFORM_OPTIONS.map((p) => (
                      <option key={p.value} value={p.value}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                  <input
                    type="text"
                    className="setup-input inline-input"
                    placeholder="e.g. @acmebank"
                    value={accHandle}
                    onChange={(e) => {
                      setAccHandle(e.target.value);
                      setAccError(null);
                    }}
                    disabled={accBusy}
                    aria-label="Account handle"
                  />
                  <button
                    type="submit"
                    className="btn-secondary btn-sm inline-add-btn"
                    disabled={accBusy || !accHandle.trim()}
                  >
                    {accBusy ? (
                      <span className="btn-spinner" aria-hidden="true" />
                    ) : (
                      <>
                        <Plus size={14} />
                        <span>Add</span>
                      </>
                    )}
                  </button>
                </form>
                {accError && <span className="inline-error-text">{accError}</span>}

                {/* Accounts Card List */}
                <div className="asset-items-container">
                  {officialAccounts.length > 0 ? (
                    <div className="asset-cards-grid">
                      {officialAccounts.map((a) => (
                        <div key={a.id} className="asset-card">
                          <div className="asset-card-main">
                            <span className="asset-platform-tag">{a.platform}</span>
                            <span className="asset-identifier">{a.handle}</span>
                          </div>
                          <button
                            type="button"
                            className="asset-delete-btn"
                            onClick={() => handleRemoveAccount(a.id)}
                            title={`Remove ${a.handle}`}
                            aria-label={`Remove official account ${a.handle}`}
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="asset-empty-state">
                      <Users size={20} className="empty-icon" />
                      <div>No official social profiles added yet. Add your main handles above.</div>
                    </div>
                  )}
                </div>
              </div>

              {/* 2. Official Mobile Applications */}
              <div className="setup-sub-section">
                <div className="sub-section-title-row">
                  <div className="sub-section-title">
                    <Smartphone size={16} />
                    <span>Official Mobile Applications ({officialApps.length})</span>
                  </div>
                  {!showAddApp && (
                    <button
                      type="button"
                      className="link-btn add-toggle-btn"
                      onClick={() => setShowAddApp(true)}
                    >
                      <Plus size={13} />
                      Add mobile app
                    </button>
                  )}
                </div>

                {/* Add Mobile App Panel */}
                {showAddApp && (
                  <form onSubmit={handleAddApp} className="app-add-panel" noValidate>
                    <div className="app-add-grid">
                      <div className="app-field">
                        <label className="sub-label">App display name</label>
                        <input
                          type="text"
                          className="setup-input app-input"
                          placeholder="Acme Mobile Banking"
                          value={appName}
                          onChange={(e) => setAppName(e.target.value)}
                        />
                      </div>
                      <div className="app-field">
                        <label className="sub-label">Package ID / Bundle ID</label>
                        <input
                          type="text"
                          className="setup-input app-input"
                          placeholder="com.acme.banking"
                          value={appPackage}
                          onChange={(e) => setAppPackage(e.target.value)}
                        />
                      </div>
                      <div className="app-field">
                        <label className="sub-label">Publisher name</label>
                        <input
                          type="text"
                          className="setup-input app-input"
                          placeholder="Acme Corporation Ltd"
                          value={appPublisher}
                          onChange={(e) => setAppPublisher(e.target.value)}
                        />
                      </div>
                      <div className="app-field">
                        <label className="sub-label">Marketplace</label>
                        <select
                          className="setup-select app-input"
                          value={appStore}
                          onChange={(e) => setAppStore(e.target.value as 'google_play' | 'app_store')}
                        >
                          <option value="google_play">Google Play</option>
                          <option value="app_store">Apple App Store</option>
                        </select>
                      </div>
                    </div>

                    {appError && <span className="inline-error-text">{appError}</span>}

                    <div className="app-add-actions">
                      <button
                        type="button"
                        className="btn-secondary btn-sm"
                        onClick={() => setShowAddApp(false)}
                      >
                        Cancel
                      </button>
                      <button
                        type="submit"
                        className="btn-primary btn-sm"
                        disabled={appBusy || !appName.trim() || !appPackage.trim() || !appPublisher.trim()}
                      >
                        {appBusy ? 'Adding…' : 'Register App'}
                      </button>
                    </div>
                  </form>
                )}

                {/* App Cards List */}
                <div className="asset-items-container">
                  {officialApps.length > 0 ? (
                    <div className="asset-cards-grid">
                      {officialApps.map((ap) => (
                        <div key={ap.id} className="asset-card">
                          <div className="asset-card-main">
                            <span className="asset-platform-tag">
                              {ap.stores.map((s) => (s === 'google_play' ? 'Google Play' : 'App Store')).join(', ')}
                            </span>
                            <span className="asset-identifier">{ap.name}</span>
                            <span className="asset-sub-id">{ap.package_id}</span>
                          </div>
                          <button
                            type="button"
                            className="asset-delete-btn"
                            onClick={() => handleRemoveApp(ap.id)}
                            title={`Remove ${ap.name}`}
                            aria-label={`Remove official app ${ap.name}`}
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="asset-empty-state">
                      <Smartphone size={20} className="empty-icon" />
                      <div>No official mobile apps registered yet. Register packages to protect store listings.</div>
                    </div>
                  )}
                </div>
              </div>

              {/* Guiding note */}
              <div className="setup-guide-note">
                <Info size={16} className="note-icon" />
                <span>
                  You can update official assets, domains, and keywords at any time from the{' '}
                  <NavLink to="/brand">Brand Profile</NavLink> page.
                </span>
              </div>
            </div>
          )}

          {/* ════════════ STEP 3: ENGINE & FIRST SCAN ════════════ */}
          {step === 3 && (
            <div className="step-form-content">
              <div className="form-section-header">
                <Rocket size={18} className="section-header-icon" />
                <div>
                  <h3 className="form-section-headline">Threat Intelligence Engine</h3>
                  <p className="form-section-sub">
                    Verify detection coverage across digital vectors and launch your baseline enterprise scan.
                  </p>
                </div>
              </div>

              {/* Scope Selection */}
              <div className="setup-field">
                <label className="setup-label">Scan coverage scope</label>
                <div className="scope-selection-grid">
                  <div
                    className={`scope-card ${scanScope === 'all' ? 'active' : ''}`}
                    onClick={() => setScanScope('all')}
                    role="radio"
                    aria-checked={scanScope === 'all'}
                    tabIndex={0}
                    onKeyDown={(e) => (e.key === ' ' || e.key === 'Enter') && setScanScope('all')}
                  >
                    <div className="scope-radio-dot" />
                    <div className="scope-info">
                      <div className="scope-title">Comprehensive Coverage (Recommended)</div>
                      <div className="scope-desc">
                        Simultaneously scans social media platforms, app stores, and typo-squatting registries.
                      </div>
                    </div>
                  </div>

                  <div
                    className={`scope-card ${scanScope === 'social' ? 'active' : ''}`}
                    onClick={() => setScanScope('social')}
                    role="radio"
                    aria-checked={scanScope === 'social'}
                    tabIndex={0}
                    onKeyDown={(e) => (e.key === ' ' || e.key === 'Enter') && setScanScope('social')}
                  >
                    <div className="scope-radio-dot" />
                    <div className="scope-info">
                      <div className="scope-title">Social Media Only</div>
                      <div className="scope-desc">
                        Focuses detection specifically on fake executive accounts, fan pages, and profile clones.
                      </div>
                    </div>
                  </div>

                  <div
                    className={`scope-card ${scanScope === 'apps' ? 'active' : ''}`}
                    onClick={() => setScanScope('apps')}
                    role="radio"
                    aria-checked={scanScope === 'apps'}
                    tabIndex={0}
                    onKeyDown={(e) => (e.key === ' ' || e.key === 'Enter') && setScanScope('apps')}
                  >
                    <div className="scope-radio-dot" />
                    <div className="scope-info">
                      <div className="scope-title">App Store Marketplaces Only</div>
                      <div className="scope-desc">
                        Scans Google Play, Apple App Store, and third-party APK indexing services.
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              {/* Source Health & Readiness */}
              <div className="setup-scan-preview">
                {sources.loading && <Loading what="Verifying intelligence feeds" />}
                {sources.error && (
                  <ErrorBox error={sources.error} onRetry={sources.reload} compact />
                )}
                {sources.data && (
                  <>
                    <div className="setup-scan-ready-badge">
                      <CheckCircle2 size={16} strokeWidth={2.2} />
                      <span>{sources.data.summary.total} intelligence sources active and online</span>
                    </div>

                    <div className="setup-sources-grid">
                      {sources.data.items.slice(0, 8).map((s) => (
                        <div key={s.id} className="source-health-card">
                          <span className="source-name">{s.name}</span>
                          <span className="source-badge">
                            {sourceStatusLabel(s.status)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>

              {/* Security and Token Notice */}
              <div className="setup-security-notice">
                <Lock size={15} className="notice-lock" />
                <span>
                  All scan queries are dispatched via secure sandboxed orchestrators. Zero corporate credentials or
                  API keys are shared with external providers.
                </span>
              </div>
            </div>
          )}

          {/* Error Banner */}
          {error && (
            <div className="setup-error-banner" role="alert">
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
          )}

          {/* ── Footer Navigation Actions ── */}
          <div className="setup-action-footer">
            {step > 1 ? (
              <button
                type="button"
                className="setup-back-btn"
                onClick={handleBack}
                disabled={busy}
              >
                <ArrowLeft size={15} strokeWidth={2} />
                Back
              </button>
            ) : (
              <div /> /* Spacer to keep Continue aligned right */
            )}

            <button
              type="button"
              id="setup-continue-btn"
              className="btn-primary continue-btn"
              onClick={() => void handleContinue()}
              disabled={busy || (step === 1 && brandLoading)}
            >
              {busy ? (
                <>
                  <span className="btn-spinner" aria-hidden="true" />
                  <span>{step === 3 ? 'Launching scan…' : 'Saving…'}</span>
                </>
              ) : step < 3 ? (
                <>
                  <span>Continue</span>
                  <ArrowRight size={15} strokeWidth={2.5} />
                </>
              ) : (
                <>
                  <Rocket size={16} strokeWidth={2} />
                  <span>Launch Protection Dashboard</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
