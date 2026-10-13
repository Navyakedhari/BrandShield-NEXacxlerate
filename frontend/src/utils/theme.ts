// Dark / light theme. The choice is a per-browser preference (like the other scammind.ui.* prefs); default is dark.
export type Theme = 'dark' | 'light';
const KEY = 'scammind.ui.theme';

export function getTheme(): Theme {
  try {
    return localStorage.getItem(KEY) === 'light' ? 'light' : 'dark';
  } catch {
    return 'dark';
  }
}

export function applyTheme(theme: Theme) {
  document.documentElement.setAttribute('data-theme', theme);
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* private mode: the theme still applies for this session */
  }
}
