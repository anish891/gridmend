"use client";

import { useEffect, useState } from "react";

import { currentTheme, onThemeChange, setTheme, type Theme } from "@/lib/theme";

export default function ThemeToggle() {
  const [theme, setLocal] = useState<Theme | null>(null);

  useEffect(() => {
    setLocal(currentTheme());
    return onThemeChange(() => setLocal(currentTheme()));
  }, []);

  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      className="icon-button"
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      onClick={() => setTheme(next)}
    >
      {theme === "dark" ? (
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
          <circle cx="12" cy="12" r="4.5" fill="currentColor" />
          <g stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
          </g>
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
          <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z" fill="currentColor" />
        </svg>
      )}
    </button>
  );
}
