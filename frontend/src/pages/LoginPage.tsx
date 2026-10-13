import React, { useState } from 'react';
import { BrandMark, Wordmark } from '../components/common/BrandMark';
import { useLocation, useNavigate } from 'react-router-dom';
import {
  AlertCircle,
  Check,
  Eye,
  EyeOff,
  Lock,
  Mail,
  Shield,
  ShieldCheck,
  Sparkles,
  Target,
  Users,
  FileCheck2,
} from 'lucide-react';
import { ApiError } from '../api/client';
import { sm } from '../api/sm';
import '../components/common/States.css';
import './LoginPage.css';

const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export const LoginPage: React.FC = () => {
  const navigate = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from;
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [keepSignedIn, setKeepSignedIn] = useState(true);
  const [busy, setBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [emailTouched, setEmailTouched] = useState(false);
  const [passwordTouched, setPasswordTouched] = useState(false);

  // Field validation
  const getEmailError = (): string | null => {
    if (!emailTouched) return null;
    if (!email.trim()) return 'Work email is required.';
    if (!EMAIL_REGEX.test(email.trim())) return 'Enter a valid work email address.';
    return null;
  };

  const getPasswordError = (): string | null => {
    if (!passwordTouched) return null;
    if (!password) return 'Password is required.';
    return null;
  };

  const emailError = getEmailError();
  const passwordError = getPasswordError();
  const emailInvalid = Boolean(emailError);
  const passwordInvalid = Boolean(passwordError);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setEmailTouched(true);
    setPasswordTouched(true);
    setErrorMsg(null);

    const trimmedEmail = email.trim();
    if (!trimmedEmail || !password) {
      setErrorMsg('Enter your work email and password.');
      return;
    }

    setBusy(true);
    try {
      await sm.login(trimmedEmail, password, keepSignedIn);
      navigate('/protect-brand', { replace: true });
    } catch (err) {
      setErrorMsg(err instanceof ApiError ? err.message : 'Failed to sign in. Please try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-screen">
      {/* ── Left Hero Pane (Cybersecurity Showcase) ── */}
      <div className="login-hero-pane">
        <div className="login-brand-header">
          <div className="login-brand-logo-wrap">
            <BrandMark size={32} />
          </div>
          <div className="login-brand-title-wrap">
            <span className="login-brand-name"><Wordmark upper /></span>
            <span className="login-brand-tag">NEXacxlerate Platform</span>
          </div>
        </div>

        <div className="login-hero-copy">
          <div className="login-hero-eyebrow" aria-label="Enterprise Digital Risk Intelligence">
            <span className="login-hero-eyebrow-dot" aria-hidden="true" />
            <Shield size={12} className="login-eyebrow-icon" />
            <span>Digital Threat Defense</span>
          </div>
          <h1 className="login-hero-title">
            Stop imposters<br />
            before they{' '}
            <span className="login-hero-title-accent">strike</span>
          </h1>
          <p className="login-hero-subtitle">
            Autonomous threat intelligence uncovering fraudulent social profiles, look-alike mobile
            apps, and phishing websites across the surface and deep web.
          </p>
        </div>

        {/* Core Pillars */}
        <div className="login-pillars-grid">
          <div className="login-pillar-card">
            <div className="pillar-icon-box pillar-blue" aria-hidden="true">
              <Target size={18} strokeWidth={2.2} />
            </div>
            <div className="pillar-heading">Explainable Scoring</div>
            <div className="pillar-detail">
              Neural visual matching and phonetic homoglyph weights with fully transparent scoring rationale.
            </div>
          </div>

          <div className="login-pillar-card">
            <div className="pillar-icon-box pillar-green" aria-hidden="true">
              <Users size={18} strokeWidth={2.2} />
            </div>
            <div className="pillar-heading">Human Verification</div>
            <div className="pillar-detail">
              Rigorous analyst workflow with dual-authorization takedown enforcement. No unverified actions.
            </div>
          </div>

          <div className="login-pillar-card">
            <div className="pillar-icon-box pillar-purple" aria-hidden="true">
              <FileCheck2 size={18} strokeWidth={2.2} />
            </div>
            <div className="pillar-heading">Forensic Evidence</div>
            <div className="pillar-detail">
              Cryptographically signed captures, DOM snapshots, reverse WHOIS, and instant takedown packages.
            </div>
          </div>
        </div>

        {/* Live Protection Metrics */}
        <div className="login-metrics-bar">
          <div className="login-metric-item">
            <span className="metric-val">99.4%</span>
            <span className="metric-lbl">Model Precision</span>
          </div>
          <div className="metric-divider" aria-hidden="true" />
          <div className="login-metric-item">
            <span className="metric-val">&lt; 3m</span>
            <span className="metric-lbl">Alert Latency</span>
          </div>
          <div className="metric-divider" aria-hidden="true" />
          <div className="login-metric-item">
            <span className="metric-val">Zero</span>
            <span className="metric-lbl">Verified False+</span>
          </div>
        </div>

        {/* Atmospheric lighting effects */}
        <div className="hero-orbs-wrap" aria-hidden="true">
          <div className="hero-orb orb-1" />
          <div className="hero-orb orb-2" />
          <div className="hero-orb orb-3" />
        </div>
      </div>

      {/* ── Right Form Pane ── */}
      <div className="login-form-pane">
        <div className="login-form-box">
          <div className="login-form-header">
            <div className="login-header-chip">
              <Sparkles size={13} className="header-chip-icon" />
              <span>Enterprise SOC Access</span>
            </div>
            <h2 className="login-form-title">Welcome back</h2>
            <p className="login-form-subtitle">
              Sign in to your brand protection workspace.
            </p>
          </div>

          <form onSubmit={handleSubmit} className="auth-form" noValidate>
            {/* Contextual notice when redirected from protected setup */}
            {from === '/setup' && !errorMsg && (
              <div className="state-inline info" role="status">
                <ShieldCheck size={16} className="state-icon" />
                <span>Sign in as an administrator to set up your brand profile.</span>
              </div>
            )}

            {/* Server Error Alert */}
            {errorMsg && (
              <div className="auth-error-alert" role="alert">
                <AlertCircle size={16} className="auth-error-icon" />
                <div className="auth-error-body">
                  <strong>Authentication Failed</strong>
                  <span>{errorMsg}</span>
                </div>
              </div>
            )}

            {/* Email Field */}
            <div className="form-group">
              <label htmlFor="email" className="form-label required">
                Work email
              </label>
              <div className={`form-input-wrap${emailInvalid ? ' wrap-error' : ''}`}>
                <Mail size={16} className="input-leading-icon" aria-hidden="true" />
                <input
                  id="email"
                  type="email"
                  className={`form-input has-left-icon${emailInvalid ? ' field-error' : ''}`}
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value);
                    setErrorMsg(null);
                  }}
                  onBlur={() => setEmailTouched(true)}
                  placeholder="analyst@enterprise.com"
                  autoComplete="email"
                  aria-describedby={emailInvalid ? 'email-error' : undefined}
                  aria-invalid={emailInvalid}
                  disabled={busy}
                />
              </div>
              {emailInvalid && (
                <span id="email-error" className="field-error-text" role="alert">
                  <AlertCircle size={12} aria-hidden="true" />
                  {emailError}
                </span>
              )}
            </div>

            {/* Password Field */}
            <div className="form-group">
              <div className="form-label-row">
                <label htmlFor="password" className="form-label required">
                  Password
                </label>
                <button
                  type="button"
                  className="link-btn"
                  onClick={() =>
                    alert(
                      'Password reset is managed by your enterprise administrator or single sign-on provider. Please contact your security team.'
                    )
                  }
                >
                  Forgot password?
                </button>
              </div>
              <div className={`form-input-wrap${passwordInvalid ? ' wrap-error' : ''}`}>
                <Lock size={16} className="input-leading-icon" aria-hidden="true" />
                <input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  className={`form-input has-left-icon has-right-btn${passwordInvalid ? ' field-error' : ''}`}
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value);
                    setErrorMsg(null);
                  }}
                  onBlur={() => setPasswordTouched(true)}
                  placeholder="••••••••••••"
                  autoComplete="current-password"
                  aria-describedby={passwordInvalid ? 'password-error' : undefined}
                  aria-invalid={passwordInvalid}
                  disabled={busy}
                />
                <button
                  type="button"
                  className="pw-reveal-btn"
                  onClick={() => setShowPassword(!showPassword)}
                  aria-label={showPassword ? 'Hide password' : 'Show password'}
                  tabIndex={0}
                >
                  {showPassword ? (
                    <EyeOff size={16} strokeWidth={2} aria-hidden="true" />
                  ) : (
                    <Eye size={16} strokeWidth={2} aria-hidden="true" />
                  )}
                </button>
              </div>
              {passwordInvalid && (
                <span id="password-error" className="field-error-text" role="alert">
                  <AlertCircle size={12} aria-hidden="true" />
                  {passwordError}
                </span>
              )}
            </div>

            {/* Keep Signed In Checkbox */}
            <div
              className="checkbox-field"
              onClick={() => setKeepSignedIn(!keepSignedIn)}
              role="checkbox"
              aria-checked={keepSignedIn}
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === ' ' || e.key === 'Enter') {
                  e.preventDefault();
                  setKeepSignedIn(!keepSignedIn);
                }
              }}
            >
              <div className={`custom-checkbox ${keepSignedIn ? 'checked' : ''}`} aria-hidden="true">
                {keepSignedIn && <Check size={12} strokeWidth={3} />}
              </div>
              <div className="checkbox-text-group">
                <span className="checkbox-text">Keep me signed in on this device</span>
                <span className="checkbox-subtext">Session active for 14 days</span>
              </div>
            </div>

            {/* Submit Button */}
            <button
              type="submit"
              id="login-submit-btn"
              className="btn-primary auth-submit-btn"
              disabled={busy}
              aria-label={busy ? 'Signing in, please wait' : 'Sign in'}
            >
              {busy ? (
                <>
                  <span className="btn-spinner" aria-hidden="true" />
                  <span>Signing in…</span>
                </>
              ) : (
                <>
                  <Lock size={15} className="btn-icon" aria-hidden="true" />
                  <span>Sign in</span>
                </>
              )}
            </button>

            {/* Security Compliance Notice */}
            <div className="auth-audit-notice">
              <Lock size={13} className="notice-lock-icon" aria-hidden="true" />
              <span>
                Protected by multi-factor cryptographic tokens. All analyst actions are logged to the immutable audit trail.
              </span>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
};
