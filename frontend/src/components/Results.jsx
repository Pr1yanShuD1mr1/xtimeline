import { useMemo, useState } from "react";
import Entry from "./Entry.jsx";
import { downloadUrl, debugUrl } from "../api.js";
import { bare } from "../lib/format.js";
import { csvFilename, downloadCsv, tweetsToCsv } from "../lib/csv.js";

const TYPES = [
  ["all", "Everything"],
  ["original", "Original posts"],
  ["reposts", "Reposts"],
  ["pinned", "Pinned"],
];

function finished(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

export default function Results({ job, tweets, note = "" }) {
  const [picked, setPicked] = useState([]);
  const [type, setType] = useState("all");
  const [q, setQ] = useState("");
  const [csvNote, setCsvNote] = useState("");

  const counts = useMemo(() => {
    const m = new Map();
    for (const t of tweets) {
      const a = bare(t.account || t.author || "—");
      m.set(a, (m.get(a) || 0) + 1);
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [tweets]);

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return tweets.filter((t) => {
      const a = bare(t.account || t.author || "—");
      if (picked.length && !picked.includes(a)) return false;
      if (type === "original" && t.is_repost) return false;
      if (type === "reposts" && !t.is_repost) return false;
      if (type === "pinned" && !t.pinned) return false;
      if (needle && !(t.text || "").toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [tweets, picked, type, q]);

  const failed = Object.entries(job.per_account || {})
    .filter(([, v]) => v.status === "error")
    .map(([h]) => h);

  function toggle(handle) {
    setPicked((prev) =>
      prev.includes(handle) ? prev.filter((h) => h !== handle) : [...prev, handle]
    );
  }

  function exportCsv() {
    if (!visible.length) return;
    downloadCsv(csvFilename(job, visible.length), tweetsToCsv(visible));
    setCsvNote(`wrote ${visible.length}`);
    setTimeout(() => setCsvNote(""), 2000);
  }

  return (
    <div className="deck results">
      <div className="col">
        <label className="lbl" htmlFor="post-type">Post type</label>
        <select id="post-type" value={type} onChange={(e) => setType(e.target.value)}>
          {TYPES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>

        <div style={{ marginTop: 12 }}>
          <button
            type="button"
            className="btn"
            onClick={exportCsv}
            disabled={!visible.length}
            style={{ width: "100%" }}
            title={`Download the ${visible.length} posts currently shown as CSV`}
          >
            {csvNote || "Download CSV"}
          </button>
        </div>

        <dl className="kv">
          <dt>Job</dt><dd>{job.id}</dd>
          <dt>Page</dt><dd>{job.strategy || "—"}</dd>
          <dt>Posts</dt><dd>{tweets.length}</dd>
          <dt>Finished</dt><dd>{finished(job.finished_at)}</dd>
        </dl>

        {counts.length > 0 && (
          <>
            <h3 className="rail-title">
              Accounts
              <span className="right">Pick any</span>
            </h3>
            {counts.map(([handle, count]) => (
              <label className="acct-line" key={handle} style={{ cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={!picked.length || picked.includes(handle)}
                  onChange={() => toggle(handle)}
                />
                <span className="h">@{handle}</span>
                <span className="n">{count}</span>
              </label>
            ))}
          </>
        )}

        <div style={{ marginTop: 16 }}>
          <a className="btn" href={downloadUrl(job.id)} style={{ display: "block", textAlign: "center" }}>
            Download JSON
          </a>
        </div>
      </div>

      <div className="col">
        {note && <div className="banner warn">{note}</div>}

        <div className="feed-head">
          <span>
            <span className="strong">{visible.length}</span> of {tweets.length} posts
          </span>
          <span>·</span>
          <span>
            <span className="strong">{counts.length}</span> accounts
          </span>
          <span className="tools">
            <input
              type="text"
              placeholder="filter text…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              aria-label="Filter post text"
            />
            <button type="button" className="btn" onClick={() => setQ("")}>Reset</button>
          </span>
        </div>

        {failed.length > 0 && (
          <div className="banner err">
            <span>
              {failed.map((h) => `@${h}`).join(", ")} failed —{" "}
              <a href={debugUrl(job.id, failed[0])} target="_blank" rel="noreferrer">
                see what the browser saw
              </a>
            </span>
          </div>
        )}

        {visible.length === 0 ? (
          <p className="empty">
            {tweets.length === 0
              ? "No posts in this range."
              : "Nothing matches that filter."}
          </p>
        ) : (
          visible.map((t) => <Entry key={`${t.account}-${t.id}`} post={t} />)
        )}
      </div>
    </div>
  );
}
