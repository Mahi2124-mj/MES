// Central API client — wraps fetch with auth headers
// Usage: import { api } from "../api/client"
// Then: api.get("/api/plants/", token)  or  api.post("/api/plants/", body, token)

const BASE = "";

// Build headers.  When `token` is falsy (anonymous Fullscreen TV poll, or
// a logged-out tab), DON'T send `Authorization: Bearer undefined` — that
// header gets rejected by the backend as 401.  Send no Authorization
// header at all so optional-auth endpoints (e.g. /api/lines/{id}/realtime)
// can return data anonymously.
function headers(token) {
  const h = { "Content-Type": "application/json" };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

// Methods that mutate state.  Successful responses fan out a global
// 'ap-config-changed' DOM event so live-views (e.g. Admin Panel →
// System Map) can refresh themselves without coordination — the views
// just listen on `window.addEventListener("ap-config-changed", ...)`.
const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

// Paths whose 401 response should NOT trigger a redirect to /login.
// Login attempts and the /me probe inherently 401 when creds are bad —
// the caller handles the error message.  Public Fullscreen polls go
// through the same client but should never 401 (backend now returns
// 200 anonymously); if they ever do, redirecting from the TV display
// would be wrong.
function shouldRedirectOn401(path) {
  if (path.startsWith("/api/auth/login")) return false;
  if (path.startsWith("/api/auth/me"))    return false;
  // Fullscreen / SubmachineFullscreen create their own axios instance,
  // so this client is never used by them.  Any 401 reaching here is
  // therefore from a logged-out admin/dashboard tab → redirect.
  return true;
}

let _redirectingTo401 = false;
function redirectToLogin() {
  if (_redirectingTo401) return;          // dedupe burst of parallel 401s
  _redirectingTo401 = true;
  try {
    // Clear sessionStorage auth keys so the next render of Protected
    // routes treats us as logged-out.
    const KEYS = ["mes_token","mes_username","user_role","user_id","user_dept_slug"];
    for (const k of KEYS) {
      try { sessionStorage.removeItem(k); } catch {}
    }
    if (typeof window !== "undefined" && window.location) {
      // Use replace() so the broken page isn't in history (back button
      // would just throw the user back into a 401 loop).
      window.location.replace("/login");
    }
  } finally {
    // Allow re-arming after a real navigation has happened.  Browser
    // tears down the JS context on navigate so this rarely matters.
    setTimeout(() => { _redirectingTo401 = false; }, 2000);
  }
}

async function request(method, path, body, token) {
  const opts = {
    method,
    headers: headers(token),
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(`${BASE}${path}`, opts);
  if (!res.ok) {
    // Auto-redirect on 401 from auth-required endpoints — kills the
    // forever-401 polling loop that floods the backend log when a tab
    // sits on a Dashboard with an expired/missing token.
    if (res.status === 401 && shouldRedirectOn401(path)) {
      redirectToLogin();
    }
    let detail = `HTTP ${res.status}`;
    try { const j = await res.json(); detail = j.detail || detail; } catch {}
    throw new Error(detail);
  }

  // Broadcast successful mutations.  We dispatch *after* parsing the
  // body so listeners run in the same micro-task; harmless for any
  // page that doesn't subscribe.  Skipped on GETs (read-only) and on
  // /api/auth/* and /api/ping (auth + health are not config changes).
  if (MUTATING.has(method)
      && !path.startsWith("/api/auth/")
      && !path.startsWith("/api/ping")) {
    try {
      window.dispatchEvent(new CustomEvent("ap-config-changed", {
        detail: { path, method },
      }));
    } catch {}
  }

  // 204 No Content
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  get:    (path, token)        => request("GET",    path, undefined, token),
  post:   (path, body, token)  => request("POST",   path, body,      token),
  put:    (path, body, token)  => request("PUT",    path, body,      token),
  patch:  (path, body, token)  => request("PATCH",  path, body,      token),
  delete: (path, token)        => request("DELETE",  path, undefined, token),
};
export default api;
