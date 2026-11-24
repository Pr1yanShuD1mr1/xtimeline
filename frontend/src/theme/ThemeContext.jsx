import { createContext, useContext, useEffect, useMemo, useState } from "react";

// Day is the house edition; night is opt-in and remembered.

const KEY = "xtimeline.theme";
const ThemeContext = createContext(null);

function initialTheme() {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "day" || saved === "night") return saved;
  } catch {
    /* storage unavailable */
  }
  return "day";
}

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(initialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      /* private mode: the choice just won't persist */
    }
  }, [theme]);

  const value = useMemo(
    () => ({
      theme,
      isDay: theme === "day",
      toggle: () => setTheme((t) => (t === "day" ? "night" : "day")),
    }),
    [theme]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside <ThemeProvider>");
  return ctx;
}
