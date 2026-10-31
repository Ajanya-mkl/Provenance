import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

export type ThemeChoice = "system" | "light" | "dark";

interface Settings {
  theme: ThemeChoice;
  resolvedTheme: "light" | "dark";
  solid: boolean;
  setTheme: (theme: ThemeChoice) => void;
  setSolid: (solid: boolean) => void;
}

const SettingsContext = createContext<Settings | null>(null);
const STORAGE_KEY = "provenance.settings";

function readStored(): { theme: ThemeChoice; solid: boolean } {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
    const theme = ["system", "light", "dark"].includes(parsed.theme) ? parsed.theme : "system";
    return { theme, solid: parsed.solid === true };
  } catch {
    return { theme: "system", solid: false };
  }
}

function systemTheme(): "light" | "dark" {
  try {
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [stored] = useState(readStored);
  const [theme, setThemeState] = useState<ThemeChoice>(stored.theme);
  const [solid, setSolidState] = useState(stored.solid);
  const [system, setSystem] = useState(systemTheme);

  useEffect(() => {
    let query: MediaQueryList;
    try {
      query = window.matchMedia("(prefers-color-scheme: light)");
    } catch {
      return;
    }
    const update = () => setSystem(query.matches ? "light" : "dark");
    query.addEventListener?.("change", update);
    return () => query.removeEventListener?.("change", update);
  }, []);

  const resolvedTheme = theme === "system" ? system : theme;

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = resolvedTheme;
    root.dataset.solid = String(solid);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ theme, solid }));
    } catch {
      // Storage can be unavailable (private window); the settings still apply.
    }
  }, [theme, solid, resolvedTheme]);

  const setTheme = useCallback((next: ThemeChoice) => setThemeState(next), []);
  const setSolid = useCallback((next: boolean) => setSolidState(next), []);
  const value = useMemo(
    () => ({ theme, resolvedTheme, solid, setTheme, setSolid }),
    [theme, resolvedTheme, solid, setTheme, setSolid],
  );
  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useSettings(): Settings {
  const context = useContext(SettingsContext);
  if (!context) throw new Error("useSettings must be used inside SettingsProvider");
  return context;
}
