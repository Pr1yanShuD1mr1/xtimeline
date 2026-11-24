// CSV export of the posts currently shown.
//
// Built in the browser on purpose: the result set is already here, so a new
// endpoint would only add a second code path to keep in step with the filters.

const COLUMNS = [
  ["id", (t) => t.id],
  ["datetime", (t) => t.datetime],
  ["account", (t) => t.account || ""],
  ["author", (t) => t.author || ""],
  ["text", (t) => (t.text || "").replace(/\r?\n/g, " ")],
  ["url", (t) => t.url || ""],
  ["replies", (t) => t.metrics?.replies ?? 0],
  ["reposts", (t) => t.metrics?.reposts ?? 0],
  ["likes", (t) => t.metrics?.likes ?? 0],
  ["bookmarks", (t) => t.metrics?.bookmarks ?? 0],
  ["views", (t) => t.metrics?.views ?? 0],
  ["media", (t) => (t.media || []).join(" | ")],
  ["pinned", (t) => (t.pinned ? "true" : "false")],
  ["is_repost", (t) => (t.is_repost ? "true" : "false")],
];

function cell(value) {
  const s = value == null ? "" : String(value);
  // Quote everything that could break a row: commas, quotes, newlines, and
  // leading =/+/- which spreadsheets would otherwise evaluate as a formula.
  if (/[",\n\r]/.test(s) || /^[=+\-@]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

export function tweetsToCsv(tweets) {
  const head = COLUMNS.map(([name]) => name).join(",");
  const rows = tweets.map((t) => COLUMNS.map(([, get]) => cell(get(t))).join(","));
  return [head, ...rows].join("\r\n") + "\r\n";
}

export function downloadCsv(filename, csv) {
  // The BOM keeps Excel from mangling non-ASCII handles and emoji.
  const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function csvFilename(job, count) {
  const id = job?.id || "result";
  const accounts = (job?.handles || []).slice(0, 3).join("-") || "posts";
  const stamp = new Date().toISOString().slice(0, 10);
  return `xtimeline-${accounts}-${stamp}-${count}posts-${id}.csv`;
}
