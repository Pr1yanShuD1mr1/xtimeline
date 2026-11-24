// Thin wrapper around the backend API.
//
// Every call except login/health carries the bearer token. A 401 means the
// token is gone or expired: we broadcast an event so the auth context can drop
// it and the router can bounce to /login, which is what makes the protected
// routes actually protective rather than cosmetic.

const TOKEN_KEY = "xtimeline.token";

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable (private mode) - the session just won't persist */
  }
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function detailMessage(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc, msg, type}, ...]
    return detail
      .map((d) => {
        const where = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body").join(".") : "";
        return where ? `${where}: ${d.msg}` : d.msg;
      })
      .join("; ");
  }
  return JSON.stringify(detail);
}

function broadcastUnauthorized() {
  window.dispatchEvent(new CustomEvent("xtimeline:unauthorized"));
}

async function request(path, { method = "GET", body, auth = true } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (auth && token) headers.Authorization = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Backend unreachable — is the API running?", 0);
  }

  if (res.status === 401 && auth) {
    broadcastUnauthorized();
    throw new ApiError("Your session expired — please sign in again.", 401);
  }

  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const payload = await res.json();
      message = detailMessage(payload?.detail) || message;
    } catch {
      /* not JSON - keep the status line */
    }
    throw new ApiError(message, res.status);
  }

  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  health: () => request("/api/health", { auth: false }),
  login: (password) =>
    request("/api/auth/login", { method: "POST", body: { password }, auth: false }),
  me: () => request("/api/auth/me"),
  logout: () => request("/api/auth/logout", { method: "POST" }).catch(() => null),

  session: () => request("/api/session"),

  startScrape: (payload) => request("/api/scrape", { method: "POST", body: payload }),
  listJobs: () => request("/api/jobs"),
  getJob: (id) => request(`/api/jobs/${encodeURIComponent(id)}`),
  cancelJob: (id) => request(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  getResult: (id) => request(`/api/jobs/${encodeURIComponent(id)}/result`),
  stats: (days = 0) => request(`/api/stats?days=${days}`),
  checkAccounts: (handles) =>
    request("/api/accounts/check", { method: "POST", body: { handles } }),
};

// EventSource and <a download> can't send headers, so the token rides in the
// query string for those two. Everything else uses the Authorization header.
function withToken(path) {
  const token = getToken();
  if (!token) return path;
  return path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
}

export function downloadUrl(jobId) {
  return withToken(`/api/jobs/${encodeURIComponent(jobId)}/download`);
}

export function debugUrl(jobId, handle) {
  const base = `/api/jobs/${encodeURIComponent(jobId)}/debug`;
  return withToken(handle ? `${base}?handle=${encodeURIComponent(handle)}` : base);
}

// Subscribe to live progress. Returns a cleanup function.
export function subscribe(jobId, onUpdate, onEnd) {
  const es = new EventSource(withToken(`/api/jobs/${encodeURIComponent(jobId)}/events`));
  es.onmessage = (e) => {
    try {
      onUpdate(JSON.parse(e.data));
    } catch {
      /* ignore a malformed frame */
    }
  };
  es.addEventListener("end", () => {
    es.close();
    onEnd && onEnd();
  });
  es.onerror = () => es.close();
  return () => es.close();
}
