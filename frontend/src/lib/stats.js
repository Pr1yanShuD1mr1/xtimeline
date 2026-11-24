// Aggregation for the dashboard. Pure functions over the normalised post list
// (see lib/archive.js), so every figure on the page traces back to a post.

const DAY_MS = 24 * 3600 * 1000;

export function cutoff(days, today) {
  if (!days) return null;
  return new Date(today.getTime() - days * DAY_MS);
}

export function inWindow(posts, days, today) {
  const from = cutoff(days, today);
  if (!from) return posts;
  return posts.filter((p) => new Date(p.at) >= from);
}

export function dailySeries(posts, days, today) {
  const span = days || 30;
  const buckets = new Map();
  for (let i = span - 1; i >= 0; i -= 1) {
    const key = new Date(today.getTime() - i * DAY_MS).toISOString().slice(0, 10);
    buckets.set(key, { day: key, total: 0, byAccount: {}, hasData: false });
  }
  for (const post of posts) {
    const bucket = buckets.get(post.day);
    if (!bucket) continue;
    bucket.total += 1;
    bucket.byAccount[post.account] = (bucket.byAccount[post.account] || 0) + 1;
    bucket.hasData = true;
  }
  return [...buckets.values()];
}

// The server returns only the days that have posts; the chart needs every day
// in the window so the gaps stay visible.
export function dailySeriesFromStats(daily, days, today) {
  const span = days || 30;
  const byDay = new Map((daily || []).map((d) => [d.day, d]));
  const out = [];
  for (let i = span - 1; i >= 0; i -= 1) {
    const key = new Date(today.getTime() - i * DAY_MS).toISOString().slice(0, 10);
    const hit = byDay.get(key);
    out.push({
      day: key,
      total: hit?.total || 0,
      byAccount: hit?.byAccount || {},
      hasData: Boolean(hit?.total),
    });
  }
  return out;
}

export function postsPerWeek(posts, weeks, today) {
  const out = [];
  for (let i = weeks - 1; i >= 0; i -= 1) {
    const end = new Date(today.getTime() - i * 7 * DAY_MS);
    const start = new Date(end.getTime() - 7 * DAY_MS);
    const count = posts.filter((p) => {
      const at = new Date(p.at);
      return at > start && at <= end;
    }).length;
    out.push({ key: i, label: i === 0 ? "Now" : `${i}w`, count });
  }
  return out;
}

export function scatterBounds(posts) {
  return {
    maxLikes: Math.max(...posts.map((p) => p.likes), 1),
    maxViews: Math.max(...posts.map((p) => p.views), 1),
  };
}

// Runs and posts must come from the same slice of the archive, or the box
// disagrees with the tabs beside it.
export function accountUsage(jobs, posts) {
  const runs = new Map();
  for (const job of jobs) {
    for (const handle of job.handles || []) {
      runs.set(handle, (runs.get(handle) || 0) + 1);
    }
  }

  const counts = new Map();
  const last = new Map();
  for (const post of posts) {
    counts.set(post.account, (counts.get(post.account) || 0) + 1);
    if (!last.has(post.account) || post.at > last.get(post.account)) {
      last.set(post.account, post.at);
    }
  }

  const handles = new Set([...runs.keys(), ...counts.keys()]);
  return [...handles]
    .map((handle) => ({
      handle,
      runs: runs.get(handle) || 0,
      posts: counts.get(handle) || 0,
      last: last.get(handle) || null,
    }))
    .sort((a, b) => b.runs - a.runs || b.posts - a.posts);
}

// Square-root scale: keeps small values visible without letting one viral post
// flatten everything else against the axis.
export function sqrtScale(value, max) {
  if (!max) return 0;
  return Math.sqrt(Math.max(0, value)) / Math.sqrt(max);
}

export function compact(n) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export function fmt(n) {
  return typeof n === "number" ? n.toLocaleString() : "—";
}

export function shortDate(iso) {
  return new Date(iso).toLocaleDateString(undefined, {
    day: "2-digit",
    month: "short",
  });
}

export function clock(iso) {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}
