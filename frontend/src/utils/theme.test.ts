import { beforeEach, describe, expect, it } from 'vitest';
import { applyTheme, getTheme } from './theme';

describe('theme', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
  });

  it('defaults to dark', () => {
    expect(getTheme()).toBe('dark');
  });

  it('applies and remembers the chosen theme', () => {
    applyTheme('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    expect(getTheme()).toBe('light');
    applyTheme('dark');
    expect(getTheme()).toBe('dark');
  });
});
