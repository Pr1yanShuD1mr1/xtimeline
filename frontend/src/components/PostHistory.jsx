import { useMemo, useState } from "react";
import Entry from "./Entry.jsx";
import { postsPerWeek, scatterBounds, shortDate, sqrtScale } from "../lib/stats.js";

// The charts describe the whole account; the cards below honour From / To.

const SORTS = [
  ["date", "Date"],
  ["likes", "Likes"],
  ["views", "Views"],
];

const WEEKS = 9;
const DAY_MS = 24 * 3600 * 1000;

const isoDay = (d) => d.toISOString().slice(0, 10);

function PostsPerWeek({ buckets }) {
  const W = 430;
  const H = 190;
  const padL = 6;
  const padB = 24;
  const padT = 18;
  const max = Math.max(...buckets.map((b) => b.count), 1);
  const bw = (W - padL * 2) / buckets.length;

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label={`Posts per week, last ${buckets.length} weeks`}>
        {buckets.map((b, i) => {
          const h = (b.count / max) * (H - padB - padT);
          const x = padL + i * bw + bw * 0.12;
          const w = bw * 0.74;
          return (
            <g key={b.key}>
              <title>{`${b.label}: ${b.count} posts`}</title>
              <rect x={x} y={H - padB - h} width={w} height={h} fill="var(--ink)" />
              <text x={x + w / 2} y={H - padB - h - 6} textAnchor="middle">{b.count}</text>
              <text x={x + w / 2} y={H - 7} textAnchor="middle">{b.label}</text>
            </g>
          );
        })}
        <line x1={padL} x2={W - padL} y1={H - padB} y2={H - padB}
              className="baseline" />
      </svg>
    </div>
  );
}

function LikesVsViews({ posts }) {
  const W = 430;
  const H = 200;
  const padL = 44;
  const padB = 26;
  const padT = 12;
  const padR = 10;

  const { maxLikes, maxViews } = scatterBounds(posts);
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const k = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n));

  // X reports zero views for most posts. A scatter against a zero axis is a
  // vertical line, so say so rather than drawing a meaningless chart.
  if (maxViews < 2) {
    return (
      <p className="empty">
        X reported no view counts for these posts, so likes cannot be plotted
        against views. The likes ranking below still holds.
      </p>
    );
  }

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img"
           aria-label="Likes against views, one dot per post, square-root scale">
        <line x1={padL} x2={padL} y1={padT} y2={H - padB} className="baseline" />
        <line x1={padL} x2={W - padR} y1={H - padB} y2={H - padB} className="baseline" />

        <text x={padL - 5} y={padT + 4} textAnchor="end">{k(maxLikes)}</text>
        <text x={padL - 5} y={H - padB} textAnchor="end">0</text>
        <text x={padL} y={H - 8}>0</text>
        <text x={W - padR} y={H - 8} textAnchor="end">{k(maxViews)} views</text>

        {posts.map((p) => (
          <circle key={p.id}
                  cx={padL + sqrtScale(p.views, maxViews) * innerW}
                  cy={H - padB - sqrtScale(p.likes, maxLikes) * innerH}
                  r="2.6" fill="var(--ink)" opacity="0.55">
            <title>{`${k(p.likes)} likes · ${k(p.views)} views — ${shortDate(p.at)}`}</title>
          </circle>
        ))}
      </svg>
      <p className="chart-caption">Both axes square-root scaled.</p>
    </div>
  );
}

export default function PostHistory({ posts, accounts, today, counts: archiveCounts }) {
  const [tab, setTab] = useState(accounts[0]?.handle || "");
  const [sortBy, setSortBy] = useState("date");
  const [dir, setDir] = useState("desc");
  const [from, setFrom] = useState(isoDay(new Date(today.getTime() - 7 * DAY_MS)));
  const [to, setTo] = useState(isoDay(today));

  // The tab count is the archive total; the cards below are only the runs whose
  // posts were loaded, and the note says so.
  const counts = useMemo(() => {
    const map = new Map();
    for (const row of archiveCounts || []) map.set(row.handle, row.posts);
    if (!map.size) {
      for (const p of posts) map.set(p.account, (map.get(p.account) || 0) + 1);
    }
    return map;
  }, [archiveCounts, posts]);

  const active = accounts.some((a) => a.handle === tab) ? tab : accounts[0]?.handle;
  const scoped = useMemo(
    () => posts.filter((p) => p.account === active),
    [posts, active]
  );

  const rows = useMemo(() => {
    const inRange = scoped.filter((p) => p.day >= from && p.day <= to);
    const value = (p) => (sortBy === "date" ? new Date(p.at).getTime() : p[sortBy] ?? 0);
    // Ties break on date so the order is deterministic and flipping direction
    // always changes something visible.
    const sorted = [...inRange].sort(
      (a, b) => value(a) - value(b) || new Date(a.at) - new Date(b.at)
    );
    return dir === "asc" ? sorted : sorted.reverse();
  }, [scoped, from, to, sortBy, dir]);

  // X reports 0 views for most posts, so "sort by views" would look broken.
  const flat = useMemo(() => {
    if (sortBy === "date" || rows.length < 2) return false;
    return new Set(rows.map((p) => p[sortBy] ?? 0)).size === 1;
  }, [rows, sortBy]);

  const weeks = useMemo(() => postsPerWeek(scoped, WEEKS, today), [scoped, today]);

  if (!accounts.length) {
    return <p className="empty">Nothing extracted yet — run a scrape from the workbench.</p>;
  }

  return (
    <div className="post-history">
      <div className="ph-tabs" role="tablist" aria-label="Accounts">
        {accounts.map((a) => (
          <button key={a.handle} type="button" role="tab"
                  aria-selected={active === a.handle}
                  onClick={() => setTab(a.handle)}>
            @{a.handle}
            <span className="n">{counts.get(a.handle) || 0}</span>
          </button>
        ))}
      </div>

      <div className="ph-charts">
        <div>
          <h3 className="ph-chart-title">Posts per week (last {WEEKS} weeks)</h3>
          <PostsPerWeek buckets={weeks} />
        </div>
        <div>
          <h3 className="ph-chart-title">Likes against views, one dot per post</h3>
          <LikesVsViews posts={scoped} />
        </div>
      </div>

      <div className="ph-sortbar">
        <label className="inline-field">
          <span>Sort by</span>
          <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
            {SORTS.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>

        <button type="button" className="dir-btn"
                onClick={() => setDir(dir === "asc" ? "desc" : "asc")}>
          {dir === "asc" ? "Ascending" : "Descending"}
        </button>

        <div className="date-range">
          <label className="inline-field">
            <span>From</span>
            <input type="date" value={from} max={to}
                   onChange={(e) => setFrom(e.target.value)} />
          </label>
          <label className="inline-field">
            <span>To</span>
            <input type="date" value={to} min={from}
                   onChange={(e) => setTo(e.target.value)} />
          </label>
        </div>
      </div>

      <div className="ph-cards"
           key={`${active}-${sortBy}-${dir}-${from}-${to}`}>
        {rows.length === 0 ? (
          <p className="empty">No posts for @{active} between those dates.</p>
        ) : (
          rows.map((post) => <Entry key={post.id} post={post} />)
        )}
      </div>

      <p className="panel-note">
        {rows.length} post{rows.length === 1 ? "" : "s"} from @{active} between{" "}
        {shortDate(from)} and {shortDate(to)} · sorted by {sortBy},{" "}
        {dir === "asc" ? "ascending" : "descending"}.
        {flat ? ` Every post here reports the same ${sortBy}, so the order is by date.` : ""}
        {" "}The charts above describe the whole account, not this range.
      </p>
    </div>
  );
}
