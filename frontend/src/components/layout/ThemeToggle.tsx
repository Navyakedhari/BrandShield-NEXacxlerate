import { useState } from 'react';
import { Moon, Sun } from 'lucide-react';
import { applyTheme, getTheme, type Theme } from '../../utils/theme';

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(getTheme());
  const next: Theme = theme === 'dark' ? 'light' : 'dark';
  return (
    <button
      type="button"
      className="bell-btn"
      data-testid="theme-toggle"
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      onClick={() => {
        applyTheme(next);
        setTheme(next);
      }}
    >
      {theme === 'dark' ? <Sun size={18} strokeWidth={1.8} /> : <Moon size={18} strokeWidth={1.8} />}
    </button>
  );
}
