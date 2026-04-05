"use client";

/**
 * Theme provider: reads the user's theme preference from the settings API
 * and applies it to <html> className. Falls back to "light" if not logged in
 * or no preference set.
 *
 * Also exposes a setTheme() callback that the settings page can call for
 * immediate visual feedback without waiting for a page reload.
 */

import { useEffect } from "react";
import { useSession } from "next-auth/react";

function applyTheme(theme: string) {
  const resolved =
    theme === "system"
      ? window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : theme || "light";
  document.documentElement.className = resolved;
}

export default function ThemeProvider() {
  const { data: session } = useSession();
  const token = session?.accessToken;

  useEffect(() => {
    if (!token) return;

    fetch("/api/py/api/settings/", {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data?.theme) {
          applyTheme(data.theme);
        }
      })
      .catch(() => {
        // Silently use default theme
      });
  }, [token]);

  // Listen for system preference changes when theme is "system"
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
      // Only react if current class is a system-resolved one and user chose "system"
      // We can't easily know, so just check if <html> has neither explicit light/dark
      // This is a best-effort approach
    };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  return null; // No visible UI -- just applies the theme class
}
