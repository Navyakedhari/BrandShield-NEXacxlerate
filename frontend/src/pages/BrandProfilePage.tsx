import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Check,
  AlertCircle,
  Plus,
  Minus,
  ShieldCheck,
} from 'lucide-react';
import { ScoreRing } from '../components/charts/ScoreRing';
import { renderAccountLabel } from '../components/monitor/MonitorParts';
import { Header } from '../components/layout/Header';
import { ErrorBox, Loading, NoBrand, Notice } from '../components/common/States';
import { ApiError } from '../api/client';
import { fmtBytes, platformLabel, SOURCE_LABEL } from '../api/format';
import { sm } from '../api/sm';
import type { Brand } from '../api/types';
import { refreshBrand, setBrandCache, useBrand } from '../hooks/useBrand';
import { useMe } from '../hooks/useMe';
import { checkLogoFile } from '../utils/logoCheck';
import './BrandProfilePage.css';

const ACCOUNT_PLATFORMS: { value: string; label: string }[] = [
  { value: 'instagram', label: 'Instagram' },
  { value: 'x', label: 'X' },
  { value: 'facebook', label: 'Facebook' },
  { value: 'linkedin', label: 'LinkedIn' },
  { value: 'youtube', label: 'YouTube' },
];

const message = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

export const BrandProfilePage: React.FC = () => {
  const { brand, loading, missing, error, reload } = useBrand();
  const { canWrite } = useMe();

  const [companyName, setCompanyName] = useState('');
  const [industry, setIndustry] = useState('');
  const [description, setDescription] = useState('');
  const [aliases, setAliases] = useState<string[]>([]);
  const [newAlias, setNewAlias] = useState('');
  const [keywords, setKeywords] = useState<string[]>([]);
  const [newKeyword, setNewKeyword] = useState('');
  const [domains, setDomains] = useState<string[]>([]);
  const [newDomain, setNewDomain] = useState('');

  // Add account inputs
  const [accPlatform, setAccPlatform] = useState('instagram');
  const [accHandle, setAccHandle] = useState('');
  const [accError, setAccError] = useState<string | null>(null);
  const [accWarning, setAccWarning] = useState<string | null>(null);
  const [accBusy, setAccBusy] = useState(false);

  // Add app inputs
  const [showAddApp, setShowAddApp] = useState(false);
  const [appName, setAppName] = useState('');
  const [appPackage, setAppPackage] = useState('');
  const [appPublisher, setAppPublisher] = useState('');
  const [appStore, setAppStore] = useState('google_play');
  const [appError, setAppError] = useState<string | null>(null);
  const [appBusy, setAppBusy] = useState(false);

  const [saving, setSaving] = useState(false);
  const [savedSuccess, setSavedSuccess] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [logoError, setLogoError] = useState<string | null>(null);
  const [logoBusy, setLogoBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const loadedFor = useRef<number | null>(null);

  const fill = (b: Brand) => {
    setCompanyName(b.name);
    setIndustry(b.industry);
    setDescription(b.description ?? '');
    setAliases(b.aliases);
    setKeywords(b.keywords);
    setDomains(b.domains);
  };

  // Fill the form once per brand; later refreshes (adding an account, uploading a logo) must not wipe unsaved edits.
  useEffect(() => {
    if (brand && loadedFor.current !== brand.id) {
      loadedFor.current = brand.id;
      fill(brand);
    }
  }, [brand]);

  const addTag = (value: string, list: string[], setList: (v: string[]) => void, clear: () => void) => {
    const v = value.trim();
    if (v && !list.some((x) => x.toLowerCase() === v.toLowerCase())) setList([...list, v]);
    clear();
  };

  const makeAdder = (value: string, list: string[], setList: (v: string[]) => void, clear: () => void) => (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && value.trim()) {
      e.preventDefault();
      addTag(value, list, setList, clear);
    }
  };

  const handleSaveProfile = async () => {
    if (!brand) return;
    setSaving(true);
    setSaveError(null);
    try {
      // A tag typed but not confirmed with Enter is still part of what the person sees: include it.
      const withPending = (list: string[], pending: string) =>
        pending.trim() && !list.some((x) => x.toLowerCase() === pending.trim().toLowerCase()) ? [...list, pending.trim()] : list;
      const saved = await sm.updateBrand(brand.id, {
        name: companyName,
        industry,
        description,
        aliases: withPending(aliases, newAlias),
        keywords: withPending(keywords, newKeyword),
        domains: withPending(domains, newDomain),
      });
      setBrandCache(saved);
      fill(saved);
      setNewAlias('');
      setNewKeyword('');
      setNewDomain('');
      setSavedSuccess(true);
      setTimeout(() => setSavedSuccess(false), 3000);
    } catch (e) {
      setSaveError(message(e, 'Could not save the brand profile.'));
    } finally {
      setSaving(false);
    }
  };

  const handleLogoFile = async (file: File | undefined) => {
    if (!file || !brand) return;
    setLogoError(null);
    const res = await checkLogoFile(file); // same rules as the server, so the person knows before waiting
    if (!res.ok) {
      setLogoError(res.error);
      return;
    }
    setLogoBusy(true);
    try {
      await sm.uploadLogo(brand.id, file);
      setBrandCache({ ...(await sm.brand(brand.id)) });
    } catch (e) {
      setLogoError(message(e, 'Could not upload the logo.')); // the server's own message (413, 415, 422)
    } finally {
      setLogoBusy(false);
    }
  };

  const handleCreateAccount = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!brand || !accHandle.trim()) return;
    setAccBusy(true);
    setAccError(null);
    setAccWarning(null);
    try {
      const acc = await sm.addAccount(brand.id, accPlatform, accHandle.trim());
      setAccHandle('');
      if (acc.status !== 'verified') {
        setAccWarning(acc.warning || `${acc.handle} was added, but its URL could not be reached.`);
      }
      await refreshBrand();
    } catch (err) {
      setAccError(message(err, 'Could not add the account.')); // 409 duplicate, 422 handle format
    } finally {
      setAccBusy(false);
    }
  };

  const handleRemoveAccount = async (id: number) => {
    if (!brand) return;
    setActionError(null);
    try {
      await sm.removeAccount(brand.id, id);
      await refreshBrand();
    } catch (err) {
      setActionError(message(err, 'Could not remove the account.'));
    }
  };

  const handleCreateApp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!brand || !appName.trim() || !appPackage.trim() || !appPublisher.trim()) return;
    setAppBusy(true);
    setAppError(null);
    try {
      await sm.addApp(brand.id, { name: appName.trim(), package_id: appPackage.trim(), publisher: appPublisher.trim(), stores: [appStore] });
      setAppName('');
      setAppPackage('');
      setShowAddApp(false);
      await refreshBrand();
    } catch (err) {
      setAppError(message(err, 'Could not add the app.'));
    } finally {
      setAppBusy(false);
    }
  };

  const handleRemoveApp = async (id: number) => {
    if (!brand) return;
    setActionError(null);
    try {
      await sm.removeApp(brand.id, id);
      await refreshBrand();
    } catch (err) {
      setActionError(message(err, 'Could not remove the app.'));
    }
  };

  const header = <Header title="Brand Profile" subtitle="Tell us what is official. Everything else is compared against it." />;

  if (missing) {
    return (
      <>
        {header}
        <div className="panel-content">
          <NoBrand />
        </div>
      </>
    );
  }
  if (!brand) {
    return (
      <>
        {header}
        <div className="panel-content">{error ? <ErrorBox error={error} onRetry={() => void reload()} /> : loading ? <Loading what="Loading brand profile" /> : null}</div>
      </>
    );
  }

  const logo = brand.logo;
  const health = brand.profile_health;
  const officialAccounts = brand.official_accounts;
  const officialApps = brand.official_apps;

  return (
    <>
      {header}

      <div className="panel-content">
        <div className="brand-profile-page">
          {error && <ErrorBox error={error} onRetry={() => void reload()} compact />}
          {/* Main 2-column layout */}
          <div className="profile-layout-grid">
            {/* Left Column: Sections 1, 2, 3 */}
            <div className="profile-sections-col">
              {/* SECTION 1: Brand identity */}
              <div className="card profile-section-card">
                <div className="section-head">
                  <h2 className="section-title">1. Brand identity</h2>
                  <p className="section-desc">
                    Names and keywords are used to find look-alikes. Official domains are used for link checks.
                  </p>
                </div>

                <div className="two-field-row">
                  <div className="form-field">
                    <label className="field-lbl" htmlFor="bp-name">Company or brand name</label>
                    <input
                      id="bp-name"
                      type="text"
                      className="field-input"
                      value={companyName}
                      onChange={(e) => setCompanyName(e.target.value)}
                    />
                  </div>
                  <div className="form-field">
                    <label className="field-lbl" htmlFor="bp-industry">Industry (helps avoid false positives)</label>
                    <input
                      id="bp-industry"
                      type="text"
                      className="field-input"
                      value={industry}
                      onChange={(e) => setIndustry(e.target.value)}
                    />
                  </div>
                </div>

                <div className="form-field">
                  <label className="field-lbl" htmlFor="bp-description">Brand description (what you publicly say about yourself)</label>
                  <textarea
                    id="bp-description"
                    className="field-input"
                    rows={3}
                    maxLength={2000}
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                  />
                </div>

                {/* Aliases */}
                <div className="form-field">
                  <label className="field-lbl">Aliases and spellings people use</label>
                  <div className="tags-input-container">
                    {aliases.map((alias, i) => (
                      <span key={i} className="interactive-tag-pill">
                        {alias}
                        <button
                          type="button"
                          className="tag-remove"
                          onClick={() => setAliases(aliases.filter((_, idx) => idx !== i))}
                        >
                          ×
                        </button>
                      </span>
                    ))}
                    <input
                      type="text"
                      className="inline-tag-input"
                      placeholder="Add an alias and press Enter"
                      aria-label="Add an alias"
                      value={newAlias}
                      onChange={(e) => setNewAlias(e.target.value)}
                      onKeyDown={makeAdder(newAlias, aliases, setAliases, () => setNewAlias(''))}
                    />
                  </div>
                </div>

                {/* Keywords & Domains */}
                <div className="two-field-row">
                  <div className="form-field">
                    <label className="field-lbl">Keywords to watch</label>
                    <div className="tags-input-container">
                      {keywords.map((kw, i) => (
                        <span key={i} className="interactive-tag-pill pill-neutral">
                          {kw}
                          <button
                            type="button"
                            className="tag-remove"
                            onClick={() => setKeywords(keywords.filter((_, idx) => idx !== i))}
                          >
                            ×
                          </button>
                        </span>
                      ))}
                      <input
                        type="text"
                        className="inline-tag-input"
                        placeholder="Add keyword"
                        aria-label="Add a keyword"
                        value={newKeyword}
                        onChange={(e) => setNewKeyword(e.target.value)}
                        onKeyDown={makeAdder(newKeyword, keywords, setKeywords, () => setNewKeyword(''))}
                      />
                    </div>
                  </div>

                  <div className="form-field">
                    <label className="field-lbl">Official domains</label>
                    <div className="tags-input-container">
                      {domains.map((dom, i) => (
                        <span key={i} className="interactive-tag-pill pill-green">
                          {dom}
                          <button
                            type="button"
                            className="tag-remove"
                            onClick={() => setDomains(domains.filter((_, idx) => idx !== i))}
                          >
                            ×
                          </button>
                        </span>
                      ))}
                      <input
                        type="text"
                        className="inline-tag-input"
                        placeholder="Add domain"
                        aria-label="Add a domain"
                        value={newDomain}
                        onChange={(e) => setNewDomain(e.target.value)}
                        onKeyDown={makeAdder(newDomain, domains, setDomains, () => setNewDomain(''))}
                      />
                    </div>
                  </div>
                </div>

                {/* Official Logo Box */}
                <div className="official-logo-box">
                  <div className="logo-preview-square">{brand.name.charAt(0).toUpperCase()}</div>
                  <div className="logo-box-info">
                    <div className="logo-box-title">Official logo</div>
                    <div className="logo-box-desc">
                      Used for logo matching on profile pictures and app icons.
                    </div>
                    <div className="logo-box-specs" data-testid="logo-specs">
                      {logo
                        ? `✓ ${logo.format.toUpperCase()} ✓ ${logo.width} × ${logo.height} px ✓ ${fmtBytes(logo.size_bytes)} (max 2 MB) ${logo.square ? '✓ Square' : '✗ Not square'}`
                        : 'No logo uploaded yet. PNG, JPEG or WebP, at least 256 × 256 px, roughly square, max 2 MB.'}
                    </div>
                  </div>
                  <input
                    ref={fileInput}
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    hidden
                    data-testid="logo-input"
                    onChange={(e) => {
                      void handleLogoFile(e.target.files?.[0]);
                      e.target.value = '';
                    }}
                  />
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => fileInput.current?.click()}
                    disabled={logoBusy || !canWrite}
                  >
                    {logoBusy ? 'Uploading…' : logo ? 'Replace logo' : 'Upload logo'}
                  </button>
                </div>
                {logoError && <Notice onClose={() => setLogoError(null)}>{logoError}</Notice>}
              </div>

              {/* SECTION 2: Official social accounts */}
              <div className="card profile-section-card">
                <div className="section-head-with-badge">
                  <div>
                    <h2 className="section-title">2. Official social accounts</h2>
                    <p className="section-desc">
                      These accounts are excluded from every scan. Matching is by exact handle and URL, never by name alone.
                    </p>
                  </div>
                  <span className="count-allowlist-badge">
                    {officialAccounts.length} accounts on the allowlist
                  </span>
                </div>

                <div className="official-accounts-list">
                  {officialAccounts.length === 0 && <div className="subform-hint">No official accounts yet.</div>}
                  {officialAccounts.map((acc) => (
                    <div key={acc.id} className="official-account-item">
                      <div className="account-item-left">
                        <div className="platform-avatar">
                          {platformLabel(acc.platform).charAt(0)}
                        </div>
                        <div className="account-handle-info">
                          <span className="account-handle-title">
                            {renderAccountLabel(`${acc.handle} on ${platformLabel(acc.platform)}`)}
                          </span>
                          <span className="account-url-sub">{acc.url}</span>
                        </div>
                      </div>

                      <div className="account-item-right">
                        <span
                          className={`chip ${
                            acc.status === 'verified' ? 'chip-green' : 'chip-orange'
                          }`}
                        >
                          {acc.status === 'verified' ? 'Verified' : 'Unreachable'}
                        </span>
                        <button
                          type="button"
                          className="item-delete-btn"
                          onClick={() => void handleRemoveAccount(acc.id)}
                          title="Remove from allowlist"
                          aria-label={`Remove ${acc.handle} from allowlist`}
                          disabled={!canWrite}
                        >
                          <Minus size={14} />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
                {actionError && <Notice onClose={() => setActionError(null)}>{actionError}</Notice>}

                {/* Add account row */}
                <div className="add-account-subform">
                  <div className="subform-title">Add an official account</div>
                  <form onSubmit={(e) => void handleCreateAccount(e)} className="add-account-row">
                    <div className="add-field-group">
                      <label className="sub-lbl" htmlFor="acc-platform">Platform</label>
                      <select
                        id="acc-platform"
                        className="field-select"
                        value={accPlatform}
                        onChange={(e) => setAccPlatform(e.target.value)}
                      >
                        {ACCOUNT_PLATFORMS.map((p) => (
                          <option key={p.value} value={p.value}>{p.label}</option>
                        ))}
                      </select>
                    </div>

                    <div className="add-field-group flex-fill">
                      <label className="sub-lbl" htmlFor="acc-handle">Handle</label>
                      <input
                        id="acc-handle"
                        type="text"
                        className="field-input"
                        placeholder="@yourbrand"
                        value={accHandle}
                        onChange={(e) => setAccHandle(e.target.value)}
                      />
                    </div>

                    <button type="submit" className="btn-secondary add-btn" disabled={accBusy || !canWrite}>
                      {accBusy ? 'Adding…' : 'Add to allowlist'}
                    </button>
                  </form>
                  <p className="subform-hint">
                    Enter the exact handle your organization owns.
                  </p>
                  {accError && <Notice onClose={() => setAccError(null)}>{accError}</Notice>}
                  {accWarning && <Notice kind="info" onClose={() => setAccWarning(null)}>{accWarning}</Notice>}
                </div>
              </div>

              {/* SECTION 3: Official mobile apps */}
              <div className="card profile-section-card">
                <div className="section-head">
                  <h2 className="section-title">3. Official mobile apps</h2>
                  <p className="section-desc">
                    The publisher name is what separates your app from a copy. Apps from any other publisher are scored as suspicious.
                  </p>
                </div>

                <div className="official-apps-table-wrap">
                  <table className="apps-table">
                    <thead>
                      <tr>
                        <th>App</th>
                        <th>Package or bundle ID</th>
                        <th>Publisher</th>
                        <th>Status</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {officialApps.length === 0 && (
                        <tr>
                          <td colSpan={5} className="subform-hint">No official apps yet.</td>
                        </tr>
                      )}
                      {officialApps.map((app) => (
                        <tr key={app.id}>
                          <td className="app-name-cell">
                            <div className="app-icon-badge">
                              {app.name.charAt(0)}
                            </div>
                            <div>
                              <div className="app-main-title">{app.name}</div>
                              <div className="app-stores-sub">
                                {app.stores.map((s) => (SOURCE_LABEL as Record<string, string>)[s] ?? s).join(' · ')}
                              </div>
                            </div>
                          </td>
                          <td className="package-id-cell">{app.package_id}</td>
                          <td className="publisher-cell">{app.publisher}</td>
                          <td>
                            <span className="chip chip-green">Verified</span>
                          </td>
                          <td>
                            <button
                              type="button"
                              className="item-delete-btn"
                              onClick={() => void handleRemoveApp(app.id)}
                              title="Remove app"
                              aria-label={`Remove ${app.name}`}
                              disabled={!canWrite}
                            >
                              <Minus size={14} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {showAddApp && (
                  <form onSubmit={(e) => void handleCreateApp(e)} className="add-app-popup-box">
                    <div className="two-field-row">
                      <div className="form-field">
                        <label className="field-lbl" htmlFor="app-name">App Name</label>
                        <input
                          id="app-name"
                          type="text"
                          className="field-input"
                          placeholder="Acme Pay"
                          value={appName}
                          onChange={(e) => setAppName(e.target.value)}
                          required
                        />
                      </div>
                      <div className="form-field">
                        <label className="field-lbl" htmlFor="app-package">Package or Bundle ID</label>
                        <input
                          id="app-package"
                          type="text"
                          className="field-input"
                          placeholder="com.acme.pay"
                          value={appPackage}
                          onChange={(e) => setAppPackage(e.target.value)}
                          required
                        />
                      </div>
                    </div>
                    <div className="two-field-row">
                      <div className="form-field">
                        <label className="field-lbl" htmlFor="app-publisher">Publisher</label>
                        <input
                          id="app-publisher"
                          type="text"
                          className="field-input"
                          placeholder="Acme Bank Ltd"
                          value={appPublisher}
                          onChange={(e) => setAppPublisher(e.target.value)}
                          required
                        />
                      </div>
                      <div className="form-field">
                        <label className="field-lbl" htmlFor="app-store">Store</label>
                        <select
                          id="app-store"
                          className="field-select"
                          value={appStore}
                          onChange={(e) => setAppStore(e.target.value)}
                        >
                          <option value="google_play">Google Play</option>
                          <option value="app_store">Apple App Store</option>
                        </select>
                      </div>
                    </div>
                    {appError && <Notice onClose={() => setAppError(null)}>{appError}</Notice>}
                    <div className="add-app-actions">
                      <button
                        type="button"
                        className="btn-secondary btn-sm"
                        onClick={() => {
                          setShowAddApp(false);
                          setAppError(null);
                        }}
                      >
                        Cancel
                      </button>
                      <button type="submit" className="btn-primary btn-sm" disabled={appBusy || !canWrite}>
                        {appBusy ? 'Adding…' : 'Confirm App'}
                      </button>
                    </div>
                  </form>
                )}

                <div className="apps-action-row">
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => setShowAddApp(true)}
                  >
                    <Plus size={14} />
                    <span>Add an official app</span>
                  </button>
                  <button
                    type="button"
                    className="btn-secondary btn-sm"
                    onClick={() => setInfo('Importing from a store listing is not available yet. Enter the app name, package ID and publisher by hand.')}
                  >
                    Import from store listing URL
                  </button>
                </div>
                {info && <Notice kind="info" onClose={() => setInfo(null)}>{info}</Notice>}
              </div>

              {/* Bottom Save Bar */}
              {saveError && <Notice>{saveError}</Notice>}
              <div className="profile-bottom-save-bar">
                <span className="save-note-text">
                  Changes are applied to the next scan after you save.
                </span>
                <div className="save-btn-group">
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => {
                      fill(brand);
                      setSaveError(null);
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={() => void handleSaveProfile()}
                    disabled={saving || !canWrite}
                  >
                    {saving ? 'Saving…' : savedSuccess ? 'Saved successfully ✓' : 'Save brand profile'}
                  </button>
                </div>
              </div>
            </div>

            {/* Right Column: Health, Allowlist stats, Detection guide */}
            <div className="profile-side-col">
              {/* Profile health card */}
              <div className="card side-panel-card">
                <h3 className="side-card-title">Profile health</h3>
                <div className="health-gauge-row">
                  <ScoreRing score={health.percent} text={`${health.percent}%`} caption="" color="#34d399" size={92} stroke={8} testId="health-pct" />
                  <div className="health-gauge-text">
                    {health.message}
                  </div>
                </div>

                <div className="health-checklist">
                  {health.checks.map((c) =>
                    c.ok ? (
                      <div key={c.key} className="checklist-item ok">
                        <Check size={14} className="chk-icon" />
                        <span>{c.label}</span>
                      </div>
                    ) : (
                      <div key={c.key} className="checklist-item warn" title={c.why ?? undefined}>
                        <AlertCircle size={14} className="chk-icon-warn" />
                        <span>{c.label}</span>
                      </div>
                    )
                  )}
                </div>
              </div>

              {/* Verification status: an attestation by a person, not an automatic check */}
              <VerificationCard brand={brand} canWrite={canWrite} onChanged={() => void refreshBrand()} />

              {/* Allowlist: never flagged */}
              <div className="card side-panel-card allowlist-card">
                <h3 className="side-card-title with-icon"><ShieldCheck size={17} aria-hidden="true" />Allowlist: never flagged</h3>
                <div className="allowlist-tri-stats">
                  <div className="allowlist-stat">
                    <span className="allowlist-num">{brand.allowlist.accounts}</span>
                    <span className="allowlist-lbl">Accounts</span>
                  </div>
                  <div className="allowlist-stat">
                    <span className="allowlist-num">{brand.allowlist.apps}</span>
                    <span className="allowlist-lbl">Apps</span>
                  </div>
                  <div className="allowlist-stat">
                    <span className="allowlist-num">{brand.allowlist.domains}</span>
                    <span className="allowlist-lbl">Domains</span>
                  </div>
                </div>
                <p className="allowlist-explanation">
                  Every detection is checked against this list before it is scored, so your own accounts and apps never reach the review queue.
                </p>
              </div>

              {/* How this profile guides detection */}
              <div className="card side-panel-card">
                <h3 className="side-card-title">How this profile guides detection</h3>
                <div className="guide-steps-list">
                  <div className="guide-step">
                    <span className="guide-step-idx">1</span>
                    <span className="guide-step-text">
                      <strong>Search</strong> platforms and stores for your names, aliases and keywords.
                    </span>
                  </div>
                  <div className="guide-step">
                    <span className="guide-step-idx">2</span>
                    <span className="guide-step-text">
                      <strong>Exclude</strong> anything on the allowlist by exact handle, URL or package ID.
                    </span>
                  </div>
                  <div className="guide-step">
                    <span className="guide-step-idx">3</span>
                    <span className="guide-step-text">
                      <strong>Compare</strong> names, logos, publishers and descriptions with your official ones.
                    </span>
                  </div>
                  <div className="guide-step">
                    <span className="guide-step-idx">4</span>
                    <span className="guide-step-text">
                      <strong>Score</strong> each match with reasons, then queue it for a person to review.
                    </span>
                  </div>
                </div>

                <Link to="/lookalike" className="guide-bottom-link">
                  Test name variations in the Look-alike Lab →
                </Link>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
};

// Verification status of official identifiers. An attestation by a person (how, when, by whom); nothing is checked automatically.
const METHODS = ['dns_txt', 'website_link', 'store_listing', 'platform_badge', 'manual_review', 'other'];

const VerificationCard: React.FC<{ brand: Brand; canWrite: boolean; onChanged: () => void }> = ({ brand, canWrite, onChanged }) => {
  const [method, setMethod] = useState('manual_review');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const assets = [
    ...brand.domains.map((d) => ({ type: 'domain', key: d.toLowerCase(), label: d })),
    ...brand.official_accounts.map((a) => ({ type: 'account', key: `${a.platform}:${a.handle.replace(/^@/, '').toLowerCase()}`, label: `${a.handle} (${platformLabel(a.platform)})` })),
    ...brand.official_apps.map((a) => ({ type: 'app', key: a.package_id.toLowerCase(), label: a.name })),
  ];
  const status = (type: string, key: string) => (brand.verifications ?? []).find((v) => v.asset_type === type && v.asset_key === key);

  const toggle = async (type: string, key: string, verified: boolean) => {
    setBusy(`${type}:${key}`);
    setError(null);
    try {
      await sm.setVerification(brand.id, { asset_type: type, asset_key: key, status: verified ? 'unverified' : 'verified', method: verified ? undefined : method });
      onChanged();
    } catch (e) {
      setError(message(e, 'Could not save the verification status.'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="card side-panel-card" data-testid="verification-card">
      <h3 className="side-card-title">Verification status</h3>
      {assets.length === 0 && <p className="allowlist-explanation">Add official domains, accounts or apps to record whether each one was verified.</p>}
      {assets.length > 0 && (
        <label className="field-lbl" style={{ display: 'block', marginBottom: 6 }}>
          Method used when verifying
          <select className="field-input" value={method} onChange={(e) => setMethod(e.target.value)} disabled={!canWrite}>
            {METHODS.map((m) => (
              <option key={m} value={m}>{m.replace(/_/g, ' ')}</option>
            ))}
          </select>
        </label>
      )}
      {assets.map((a) => {
        const v = status(a.type, a.key);
        const verified = v?.status === 'verified';
        return (
          <div key={`${a.type}:${a.key}`} className="checklist-item" style={{ justifyContent: 'space-between', gap: 8 }}>
            <span title={verified && v ? `${v.method ?? ''} by ${v.verified_by ?? ''}` : undefined}>
              {a.label}: <strong>{verified ? 'verified' : 'unverified'}</strong>
            </span>
            <button type="button" className="btn-secondary btn-sm" disabled={!canWrite || busy === `${a.type}:${a.key}`} onClick={() => void toggle(a.type, a.key, verified)}>
              {verified ? 'Unverify' : 'Mark verified'}
            </button>
          </div>
        );
      })}
      {error && <Notice onClose={() => setError(null)}>{error}</Notice>}
      <p className="allowlist-explanation">This is a record of a person's check. The app does not verify DNS or stores itself.</p>
    </div>
  );
};
