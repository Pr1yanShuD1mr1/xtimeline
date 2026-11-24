import {
  createContext, useCallback, useContext, useMemo, useRef, useState,
} from "react";

// The run log lives above the routes, so moving between Workbench, Results and
// History never wipes it. Only a page reload (or Clear) empties it.

const MAX_LINES = 500;
const OutboxContext = createContext(null);

function clock() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function OutboxProvider({ children }) {
  const [lines, setLines] = useState([]);
  // Survives route changes: the boot report must appear once per page load,
  // not once per visit to the workbench.
  const booted = useRef(false);

  const push = useCallback((level, text) => {
    if (!text) return;
    setLines((prev) => {
      const next = [...prev, { id: prev.length + 1, level, text, at: clock() }];
      return next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next;
    });
  }, []);

  const clear = useCallback(() => setLines([]), []);

  const value = useMemo(() => ({ lines, push, clear, booted }), [lines, push, clear]);
  return <OutboxContext.Provider value={value}>{children}</OutboxContext.Provider>;
}

export function useOutbox() {
  const ctx = useContext(OutboxContext);
  if (!ctx) throw new Error("useOutbox must be used inside <OutboxProvider>");
  return ctx;
}
