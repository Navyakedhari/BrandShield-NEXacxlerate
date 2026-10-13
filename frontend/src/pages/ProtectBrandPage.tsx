import React from 'react';
import { BrandMark, Wordmark } from '../components/common/BrandMark';
import { useNavigate } from 'react-router-dom';
import {
  ArrowRight,
  Building2,
  CheckCircle2,
  Lock,
  PlusCircle,
  Shield,
  ShieldCheck,
  Sparkles,
  Zap,
} from 'lucide-react';
import './ProtectBrandPage.css';

export const ProtectBrandPage: React.FC = () => {
  const navigate = useNavigate();

  const handleProtectBrand = () => {
    navigate('/setup');
  };

  return (
    <div className="protect-brand-screen">
      {/* ── Top Header Bar ── */}
      <header className="protect-brand-topbar">
        <div className="protect-brand-logo">
          <div className="brand-logo-icon-box">
            <BrandMark size={28} />
          </div>
          <div className="brand-logo-text-group">
            <span className="brand-logo-text"><Wordmark upper /></span>
            <span className="brand-logo-sub">NEXacxlerate Platform</span>
          </div>
        </div>

        <div className="protect-brand-top-badge">
          <Shield size={13} className="badge-icon" />
          <span>Security Operations Center</span>
        </div>
      </header>

      {/* ── Center Content Container ── */}
      <main className="protect-brand-main">
        <div className="protect-brand-container">
          {/* Eyebrow & Headline */}
          <div className="protect-brand-header">
            <div className="protect-brand-eyebrow">
              <span className="eyebrow-pulsing-dot" aria-hidden="true" />
              <Sparkles size={13} className="eyebrow-sparkle" />
              <span>Brand Onboarding Workflow</span>
            </div>

            <h1 className="protect-brand-title">Protect Your Brand</h1>

            <p className="protect-brand-subtitle">
              Choose how you want to get started with BrandShield AI.
            </p>
          </div>

          {/* ── Two Prominent Action Cards ── */}
          <div className="brand-action-cards-grid">
            {/* ── CARD 1: Create a Brand (UI ONLY — NO FUNCTIONALITY) ── */}
            <div
              className="brand-action-card card-create-brand"
              role="region"
              aria-label="Create a Brand (Coming soon)"
            >
              <div className="action-card-badge-wrap">
                <span className="card-status-pill muted">New Entity</span>
              </div>

              <div className="action-card-icon-wrap icon-purple" aria-hidden="true">
                <Building2 size={28} strokeWidth={2} />
              </div>

              <div className="action-card-content">
                <h2 className="action-card-title">Create a Brand</h2>
                <p className="action-card-desc">
                  Set up a new brand profile.
                </p>

                <ul className="action-card-features">
                  <li>
                    <CheckCircle2 size={13} className="feature-check" />
                    <span>Register a new enterprise brand profile</span>
                  </li>
                  <li>
                    <CheckCircle2 size={13} className="feature-check" />
                    <span>Establish dedicated security perimeter</span>
                  </li>
                </ul>
              </div>

              <div className="action-card-footer">
                <button
                  type="button"
                  className="card-action-btn btn-disabled"
                  disabled
                  aria-disabled="true"
                  tabIndex={-1}
                >
                  <PlusCircle size={15} />
                  <span>Create a Brand</span>
                </button>
              </div>
            </div>

            {/* ── CARD 2: Protect a Brand (FUNCTIONAL — NAVIGATES TO /setup) ── */}
            <div
              className="brand-action-card card-protect-brand"
              onClick={handleProtectBrand}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  handleProtectBrand();
                }
              }}
            >
              <div className="action-card-badge-wrap">
                <span className="card-status-pill active">
                  <Zap size={10} className="pill-zap" />
                  Recommended
                </span>
              </div>

              <div className="action-card-icon-wrap icon-blue" aria-hidden="true">
                <ShieldCheck size={28} strokeWidth={2} />
              </div>

              <div className="action-card-content">
                <h2 className="action-card-title">Protect a Brand</h2>
                <p className="action-card-desc">
                  Configure protection for an existing brand.
                </p>

                <ul className="action-card-features">
                  <li>
                    <CheckCircle2 size={13} className="feature-check active" />
                    <span>Whitelist verified accounts &amp; mobile apps</span>
                  </li>
                  <li>
                    <CheckCircle2 size={13} className="feature-check active" />
                    <span>Launch autonomous detection &amp; scan pipeline</span>
                  </li>
                </ul>
              </div>

              <div className="action-card-footer">
                <button
                  type="button"
                  id="protect-brand-btn"
                  className="card-action-btn btn-active"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleProtectBrand();
                  }}
                >
                  <span>Protect a Brand</span>
                  <ArrowRight size={15} strokeWidth={2.5} className="action-arrow" />
                </button>
              </div>
            </div>
          </div>

          {/* Compliance & Audit Footer */}
          <div className="protect-brand-audit-notice">
            <Lock size={13} className="audit-lock-icon" aria-hidden="true" />
            <span>
              Protected by BrandShield zero-trust architecture. All security operations are recorded in the enterprise audit log.
            </span>
          </div>
        </div>
      </main>

      {/* Atmospheric glowing backdrop orbs */}
      <div className="protect-orbs-wrap" aria-hidden="true">
        <div className="protect-orb orb-1" />
        <div className="protect-orb orb-2" />
      </div>
    </div>
  );
};
