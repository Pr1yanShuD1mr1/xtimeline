// Small presentation helpers shared by the results view and the outbox.

const AVATAR_COLORS = [
  "#f91880", // pink
  "#ff7a00", // orange
  "#00ba7c", // green
  "#1d9bf0", // blue
  "#7856ff", // violet
  "#00c2c7", // teal
];

export function avatarColor(seed) {
  const s = String(seed || "?");
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

export function initial(handle) {
  const s = String(handle || "?").replace(/^@+/, "");
  return (s[0] || "?").toUpperCase();
}

// "@Smitha_codes" -> "Smitha_codes" (the mock shows the bare handle as name).
export function bare(handle) {
  return String(handle || "").replace(/^@+/, "");
}

export function n(value) {
  if (typeof value !== "number" || Number.isNaN(value)) return "0";
  if (value >= 1000000) return `${(value / 1000000).toFixed(1).replace(/\.0$/, "")}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1).replace(/\.0$/, "")}K`;
  return String(value);
}

// "7h", "3d" — the compact form X uses, with an absolute fallback.
export function relTime(iso) {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso || "";
  const secs = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.round(hours / 24);
  if (days < 8) return `${days}d`;
  return new Date(then).toLocaleDateString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export function fullTime(iso) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

// The line X prints under a post: time · date · views.
export function stamp(iso, views) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso || "";
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const date = d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
  const base = `${time} · ${date}`;
  return views ? `${base} · ${views.toLocaleString()} Views` : base;
}

export function clockNow() {
  const d = new Date();
  const pad = (v) => String(v).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
