import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import Workbench from "../components/Workbench.jsx";
import Accounts from "../components/Accounts.jsx";
import Log from "../components/Log.jsx";
import { useOutbox } from "../outbox/OutboxContext.jsx";
import { useAuth } from "../auth/AuthContext.jsx";
import { api, subscribe } from "../api.js";

const STORAGE_KEY = "xtimeline.accounts";

function loadAccounts() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    if (!Array.isArray(raw)) return [];
    const seen = new Set();
    const out = [];
    for (const item of raw) {
      if (typeof item !== "string") continue;
      const h = item.trim().replace(/^@+/, "");
      if (!h || seen.has(h.toLowerCase())) continue;
      seen.add(h.toLowerCase());
      out.push({ handle: h });
    }
    return out;
  } catch {
    return [];
  }
}

function suggestion(text) {
  const s = String(text || "").toLowerCase();
  if (s.includes("nothing rendered") || s.includes("no posts were mounted"))
    return "fix: retry with 'Show browser window' ticked — X often serves a headless browser an empty timeline";
  if (s.includes("session") || s.includes("login") || s.includes("knowledge"))
    return "fix: re-run `python save_session.py`, then retry";
  if (s.includes("rate") || s.includes("429") || s.includes("something went wrong"))
    return "fix: wait a minute, raise XTIMELINE_SCROLL_DELAY_MS, or shrink the range";
  if (s.includes("protected") || s.includes("suspended") || s.includes("exist"))
    return "fix: check the handle spelling — private or deleted accounts return nothing";
  if (s.includes("timeout") || s.includes("navigation"))
    return "fix: raise XTIMELINE_NAV_TIMEOUT_MS, or tick 'Show browser window'";
  if (s.includes("targetclosed") || s.includes("has been closed"))
    return "fix: the browser was closed mid-run — leave the window open, or untick it";
  return "see the terminal running run.py for the full traceback";
}

export default function WorkbenchPage() {
  const [accounts, setAccounts] = useState(loadAccounts);
  const [selected, setSelected] = useState(() => loadAccounts().map((a) => a.handle));
  const [job, setJob] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [runNo, setRunNo] = useState(0);
  // handle -> "ok" | "missing" | "unknown", filled in by /api/accounts/check
  const [checks, setChecks] = useState({});

  const { lines, push, clear, booted } = useOutbox();
  const { expiresAt } = useAuth();
  const unsubscribe = useRef(null);
  const seen = useRef({ status: null, per: {}, note: "" });


  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(accounts.map((a) => a.handle)));
    } catch {
      /* private mode */
    }
  }, [accounts]);

  // ---- boot report, once per page load ------------------------------------
  useEffect(() => {
    if (booted.current) return;
    booted.current = true;

    api.health()
      .then((h) => {
        push("ok", `api up · extractor=${h.extractor} · store=${h.store || "?"}`);
        if (h.store === "memory") {
          push("warn", "history is memory-only — it will not survive a restart");
          push("tip", "fix: start MongoDB, or set XTIMELINE_MONGO_URI, then restart");
        }
      })
      .catch(() => push("err", "api unreachable — is `python run.py` running?"));

    api.session()
      .then((s) => {
        const age = s.state_file_age_minutes;
        const cookie = s.saved ? `saved${age != null ? ` ${age} min ago` : ""}` : "missing";
        push("info", `session: cookies ${cookie}`);
        push("tip", `profile: ${s.profile ? "present" : "missing"}`);
        if (!s.saved && !s.profile) {
          push("warn", "no saved X session — every account will hit a login wall");
          push("tip", "fix: run `python save_session.py` once, then retry");
        } else if (age != null && age > 60 * 24 * 7) {
          push("warn", `cookies are ${Math.floor(age / 1440)} days old — expect expiry`);
        }
      })
      .catch(() => push("warn", "session status unavailable"));

    if (expiresAt) {
      push("info", `auth token expires ${new Date(expiresAt * 1000).toLocaleDateString()}`);
    }
  }, [push, expiresAt]);

  // ---- follow the job ------------------------------------------------------
  useEffect(() => {
    if (!job) return;
    const s = seen.current;

    if (job.status !== s.status) {
      if (job.status === "running") {
        push("info", `job ${job.id} · ${job.strategy} · ${(job.handles || []).length} account(s)`);
      } else if (job.status === "done") {
        push("ok", `done · ${job.collected} posts collected`);
      } else if (job.status === "cancelled") {
        push("warn", `cancelled · ${job.collected} posts kept`);
      } else if (job.status === "error") {
        push("err", `job failed · ${job.error || job.message}`);
        push("tip", suggestion(job.error || job.message));
      }
      s.status = job.status;
    }

    const per = job.per_account || {};
    for (const [handle, info] of Object.entries(per)) {
      const before = s.per[handle] || { status: null, collected: -1 };

      if (info.status !== before.status) {
        if (info.status === "running") push("info", `fetching @${handle} …`);
        else if (info.status === "done")
          push("ok", `@${handle} done · ${info.collected} posts${info.note ? ` · ${info.note}` : ""}`);
        else if (info.status === "cancelled")
          push("warn", `@${handle} cancelled · ${info.collected} posts kept`);
        else if (info.status === "error") {
          push("err", `@${handle} failed · ${info.error || "unknown error"}`);
          push("tip", suggestion(info.error));
        }
      } else if (info.status === "running" && info.collected !== before.collected) {
        push("info", `@${handle} · ${info.collected} posts`);
      }

      s.per[handle] = { status: info.status, collected: info.collected };
    }

    if (job.note && job.note !== s.note) {
      s.note = job.note;
      push("tip", job.note);
    }
  }, [job, push]);

  useEffect(() => () => unsubscribe.current && unsubscribe.current(), []);

  // Ask X whether each handle exists. Slow (a browser opens a profile), so it
  // runs in the background and the outbox narrates it.
  const verifyAccounts = useCallback(async (handles) => {
    if (!handles.length) return;
    push("info", `checking ${handles.map((h) => "@" + h).join(", ")} …`);
    try {
      const { results } = await api.checkAccounts(handles);
      setChecks((prev) => ({ ...prev, ...results }));
      const missing = Object.entries(results)
        .filter(([, state]) => state === "missing").map(([h]) => "@" + h);
      const unknown = Object.entries(results)
        .filter(([, state]) => state === "unknown").map(([h]) => "@" + h);
      const ok = handles.length - missing.length - unknown.length;

      if (missing.length) {
        push("err", `${missing.join(", ")} — X does not recognise these`);
        push("tip", "they are struck through in the account list; remove them before running");
      }
      if (unknown.length) {
        push("warn", `${unknown.join(", ")} — could not verify (page never settled)`);
        push("tip", "a login wall or a slow render looks like this; check the handle by hand");
      }
      if (ok) push("ok", `accounts verified · ${ok}`);
    } catch (err) {
      push("warn", `could not verify the accounts (${err.message})`);
    }
  }, [push]);

  const addAccounts = useCallback((raw) => {
    const handles = String(raw)
      .split(/[,\s;]+/)
      .map((h) => h.trim().replace(/^@+/, ""))
      .filter(Boolean);
    if (!handles.length) return;

    setAccounts((prev) => {
      const seenSet = new Set(prev.map((a) => a.handle.toLowerCase()));
      const next = [...prev];
      for (const h of handles) {
        if (seenSet.has(h.toLowerCase())) continue;
        seenSet.add(h.toLowerCase());
        next.push({ handle: h });
      }
      return next;
    });
    setSelected((prev) => {
      const next = [...prev];
      for (const h of handles) if (!next.includes(h)) next.push(h);
      return next;
    });

    verifyAccounts(handles);
  }, [verifyAccounts]);

  const removeAccount = useCallback((handle) => {
    setAccounts((prev) => prev.filter((a) => a.handle !== handle));
    setSelected((prev) => prev.filter((h) => h !== handle));
  }, []);

  const toggleAccount = useCallback((handle) => {
    setSelected((prev) =>
      prev.includes(handle) ? prev.filter((h) => h !== handle) : [...prev, handle]
    );
  }, []);

  async function handleSubmit(form) {
    const chosen = form.handles.filter((h) => selected.includes(h));
    const handles = chosen.length ? chosen : form.handles;

    setBusy(true);
    setError("");
    setJob(null);
    setRunNo((n) => n + 1);
    seen.current = { status: null, per: {}, note: "" };
    push("info", `request · ${handles.length} account(s) · ${form.strategy}`
      + ` · ${form.limit ? `max ${form.limit}/account` : "no cap"}`);

    try {
      const { job_id } = await api.startScrape({ ...form, handles });
      if (unsubscribe.current) unsubscribe.current();
      unsubscribe.current = subscribe(job_id, setJob, async () => {
        setBusy(false);
        try {
          const res = await api.getResult(job_id);
          push("ok", `results ready · ${(res.tweets || []).length} posts`);
        } catch {
          /* the snapshot already carries the error */
        }
      });
    } catch (err) {
      setError(err.message || "request failed");
      push("err", `request failed · ${err.message || "unknown"}`);
      push("tip", suggestion(err.message));
      setBusy(false);
    }
  }

  async function handleCancel() {
    if (!job) return;
    try {
      await api.cancelJob(job.id);
      push("warn", "cancel requested — finishing the current scroll");
    } catch (err) {
      setError(err.message || "could not cancel");
    }
  }

  const running = busy || (job && (job.status === "running" || job.status === "queued"));
  const finished = job && (job.status === "done" || job.status === "cancelled");
  const pct = job ? Math.max(0, Math.min(100, job.progress || 0)) : 0;

  const strip = running
    ? { cls: "running", text: job?.message || "starting…", pct }
    : job?.status === "error"
    ? { cls: "error", text: job.error || job.message || "run failed" }
    : finished
    ? { cls: "", text: `${job.collected} posts from ${(job.handles || []).length} account(s)` }
    : { cls: "", text: "ready. Nothing has been fetched in this session yet." };

  return (
    <>
      <div className="deck workbench">
        <Workbench accounts={accounts} onAdd={addAccounts} onSubmit={handleSubmit} busy={running} runNo={runNo} />
        <Accounts
          accounts={accounts}
          selected={selected}
          onToggle={toggleAccount}
          onRemove={removeAccount}
          onClear={() => { setAccounts([]); setSelected([]); setChecks({}); }}
          state={checks}
        />
        <Log lines={lines} onClear={clear} />
      </div>

      {error && <div className="banner err">{error}</div>}

      <div className={"status-strip " + strip.cls}>
        <span className="dot" />
        <span>{strip.text}</span>
        {strip.pct != null && <span className="pct">{strip.pct}%</span>}
        {running && <button type="button" className="btn" onClick={handleCancel}>Cancel</button>}
        {finished && (
          <Link className="btn" to={`/jobs/${job.id}`}>Results</Link>
        )}
        {running && (
          <div className="bar"><i style={{ width: `${pct}%` }} /></div>
        )}
      </div>
    </>
  );
}
