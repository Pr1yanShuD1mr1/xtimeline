import { useState } from "react";

const HANDLE_RE = /^[A-Za-z0-9_]{1,15}$/;

const STRATEGIES = [
  ["search", "Search page — X filters by date"],
  ["profile", "Profile timeline — scroll back from today"],
  ["auto", "Auto — search, then profile"],
];

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}

const today = new Date();
const weekAgo = new Date(today.getTime() - 7 * 24 * 3600 * 1000);

export default function Workbench({ accounts, onAdd, onSubmit, busy, runNo = 0 }) {
  const [username, setUsername] = useState("");
  const [from, setFrom] = useState(isoDate(weekAgo));
  const [to, setTo] = useState(isoDate(today));
  const [limit, setLimit] = useState(500);
  const [scrolls, setScrolls] = useState(60);
  const [strategy, setStrategy] = useState("search");
  const [headed, setHeaded] = useState(false);
  const [reposts, setReposts] = useState(false);
  const [error, setError] = useState("");

  function add() {
    if (!username.trim()) return;
    onAdd(username);
    setUsername("");
    setError("");
  }

  function submit(event) {
    event.preventDefault();

    // Fold whatever is still typed into the list before validating.
    let all = accounts;
    if (username.trim()) {
      onAdd(username);
      all = [...accounts, ...username.split(/[,\s;]+/).map((h) => h.replace(/^@+/, "")).filter(Boolean)];
      setUsername("");
    }

    if (all.length === 0) {
      setError("Add at least one username.");
      return;
    }
    const bad = all.filter((h) => !HANDLE_RE.test(h.handle || h));
    if (bad.length) {
      setError(`Not a valid username: ${bad.map((b) => b.handle || b).join(", ")}`);
      return;
    }
    if (to < from) {
      setError("The end date is before the start date.");
      return;
    }

    setError("");
    onSubmit({
      handles: all.map((a) => a.handle || a),
      start: new Date(from + "T00:00:00Z").toISOString(),
      end: new Date(to + "T23:59:59Z").toISOString(),
      strategy,
      headed,
      include_retweets: reposts,
      max_scrolls: Number(scrolls),
      limit: Number(limit),
    });
  }

  return (
    <div className="col">
      <h2 className="col-title">
        New extraction
        <span className="right">No. {String(runNo + 1).padStart(2, "0")}</span>
      </h2>

      <form className="form-grid" onSubmit={submit}>
        <div>
          <label className="lbl" htmlFor="username-input">Username</label>
          <div className="username-row">
            <input
              id="username-input"
              type="text"
              value={username}
              placeholder=""
              autoComplete="off"
              onChange={(e) => setUsername(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } }}
              onPaste={(e) => {
                const text = e.clipboardData.getData("text");
                if (/[,\s;]/.test(text.trim())) {
                  e.preventDefault();
                  onAdd(text);
                }
              }}
            />
            <button type="button" className="btn" onClick={add} disabled={!username.trim()}>
              Add
            </button>
          </div>
          <p className="note">One handle at a time, or paste a comma-separated list.</p>
        </div>

        <div className="pair">
          <div>
            <label className="lbl" htmlFor="from">From</label>
            <input id="from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div>
            <label className="lbl" htmlFor="to">To</label>
            <input id="to" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
        </div>

        <div className="pair">
          <div>
            <label className="lbl" htmlFor="limit">Max posts / account</label>
            <input id="limit" type="number" min="0" max="5000" value={limit}
                   onChange={(e) => setLimit(e.target.value)} />
            <p className="note">0 = no cap</p>
          </div>
          <div>
            <label className="lbl" htmlFor="scrolls">Max scrolls</label>
            <input id="scrolls" type="number" min="1" max="1000" value={scrolls}
                   onChange={(e) => setScrolls(e.target.value)} />
            <p className="note">safety cap per account</p>
          </div>
        </div>

        <div>
          <label className="lbl" htmlFor="strategy">Page to drive</label>
          <select id="strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            {STRATEGIES.map(([v, label]) => (
              <option key={v} value={v}>{label}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="check">
            <input type="checkbox" checked={headed} onChange={(e) => setHeaded(e.target.checked)} />
            Show the browser window <span className="hint">— slower, but you can watch</span>
          </label>
          <label className="check">
            <input type="checkbox" checked={reposts} onChange={(e) => setReposts(e.target.checked)} />
            Include reposts <span className="hint">— other people&apos;s posts</span>
          </label>
        </div>

        {error && <div className="login-error">{error}</div>}

        <div>
          <button type="submit" className="btn btn-dark" disabled={busy}>
            {busy ? "Extracting…" : "Extract"}
          </button>
        </div>
      </form>
    </div>
  );
}
