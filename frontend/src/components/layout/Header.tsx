import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bell, ChevronDown, LogOut, Menu } from 'lucide-react';
import { ThemeToggle } from './ThemeToggle';
import { sm } from '../../api/sm';
import { resetSampleFlag, useIsSample } from '../../api/sample';
import { resetBrand } from '../../hooks/useBrand';
import { useMe } from '../../hooks/useMe';
import '../common/States.css';
import './Layout.css';

interface HeaderProps {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  onOpenMobileMenu?: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  title,
  subtitle,
  actions,
  onOpenMobileMenu,
}) => {
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const { user: me } = useMe();
  const sample = useIsSample();
  // Until /api/me answers (or if it cannot be reached) the chip shows no invented identity.
  const user = { name: me?.name || '', email: me?.email || '' };

  const handleSignOut = async () => {
    // Revokes the refresh token on the server and removes the session (the one real storage key) from both storages.
    await sm.logout();
    resetBrand();
    resetSampleFlag();
    navigate('/login', { replace: true });
  };

  return (
    <header className="panel-header">
      <div className="header-lead">
        <button
          type="button"
          className="mobile-hamburger"
          onClick={onOpenMobileMenu}
          aria-label="Toggle menu"
        >
          <Menu size={20} />
        </button>
        <div className="header-left">
          <h1 className="header-title" title={title}>
            {title}
          </h1>
          {subtitle && (
            <p className="header-subtitle" title={subtitle}>
              {subtitle}
            </p>
          )}
        </div>
      </div>

      <div className="header-right">
        {actions && <div className="header-actions">{actions}</div>}

        {sample && (
          <span className="sample-badge" data-testid="sample-badge" title="The data on this page is sample data, not real detections.">
            Sample data
          </span>
        )}

        <ThemeToggle />

        <button type="button" className="bell-btn" aria-label="Notifications">
          <Bell size={18} strokeWidth={1.8} />
          <span className="bell-dot" />
        </button>

        <div className="user-menu-wrap">
          <div
            className="user-chip"
            role="button"
            tabIndex={0}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((o) => !o)}
            onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && setMenuOpen((o) => !o)}
          >
            <div className="user-avatar">{(user.name || user.email || '?').charAt(0).toUpperCase()}</div>
            <div className="user-info">
              <span className="user-name">{user.name}</span>
              <span className="user-email">{user.email}</span>
            </div>
            <ChevronDown size={14} color="var(--text-dim)" />
          </div>

          {menuOpen && (
            <div className="user-menu" role="menu">
              <div className="user-menu-role">Signed in as {user.name || user.email}</div>
              <button
                type="button"
                role="menuitem"
                className="user-menu-item"
                onClick={handleSignOut}
              >
                <LogOut size={14} />
                <span>Sign out</span>
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
};
