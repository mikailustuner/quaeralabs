import { useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
const KEY = "quaera.theme";

function read(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

/** Tema: varsayılan sistem tercihi (açık ya da koyu); seçim yalnızca bu tarayıcıda saklanır. */
export function useTheme(): [ThemeChoice, (t: ThemeChoice) => void] {
  const [theme, setTheme] = useState<ThemeChoice>(read);
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") delete root.dataset.theme;
    else root.dataset.theme = theme;
    try { localStorage.setItem(KEY, theme); } catch { /* gizli pencere: yalnızca bu oturum */ }
  }, [theme]);
  return [theme, setTheme];
}
