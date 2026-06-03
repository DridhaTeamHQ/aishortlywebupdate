'use client';

import { useEffect, useState } from 'react';

type Theme = 'light' | 'dark';

function applyTheme(theme: Theme) {
  document.documentElement.setAttribute('data-theme', theme);
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>('light');
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    let initial: Theme = 'light';
    try {
      const stored = localStorage.getItem('shortly-theme') as Theme | null;
      if (stored === 'light' || stored === 'dark') initial = stored;
      else if (window.matchMedia?.('(prefers-color-scheme: dark)').matches) initial = 'dark';
    } catch {
      /* ignore */
    }
    setTheme(initial);
    applyTheme(initial);
    setMounted(true);
  }, []);

  const toggle = () => {
    const next: Theme = theme === 'light' ? 'dark' : 'light';
    setTheme(next);
    applyTheme(next);
    try {
      localStorage.setItem('shortly-theme', next);
    } catch {
      /* ignore */
    }
  };

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={toggle}
      aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`}
      title={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`}
    >
      <span className={`theme-toggle-track${theme === 'dark' ? ' is-dark' : ''}`}>
        <span className="theme-toggle-thumb">{mounted ? (theme === 'light' ? '☀️' : '🌙') : ''}</span>
      </span>
    </button>
  );
}
