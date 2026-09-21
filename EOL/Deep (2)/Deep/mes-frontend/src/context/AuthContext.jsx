import { createContext, useContext, useState, useEffect, useCallback } from "react";
import { deviceCheckin } from "../api/deviceCheckin";
import { autoResubscribe } from "../api/push";

const AuthContext = createContext(null);

const API = "";

// ── Auth storage = sessionStorage (per-tab) ────────────────────────
// Operator's policy: "har naya browser tab → fresh login mandatory.
// URL-only access without id/password should NEVER reach a page."
//
// sessionStorage isolates the token to ONE browser tab.  Closing the
// tab kills the session; opening a new tab → no token → Protected
// route bounces to /login.  This blocks the URL-only-access path that
// localStorage allowed (any tab on the same browser inherited the
// token).  Old localStorage keys are cleared on first run for a clean
// migration.
const AUTH_KEYS = ["mes_token","mes_username","user_role","user_id","user_dept_slug"];
(function migrateOldLocalStorage() {
  try {
    for (const k of AUTH_KEYS) {
      if (localStorage.getItem(k) !== null) localStorage.removeItem(k);
    }
  } catch {}
})();

const ss = {
  get:    (k) => { try { return sessionStorage.getItem(k); } catch { return null; } },
  set:    (k,v) => { try { sessionStorage.setItem(k, v); } catch {} },
  remove: (k) => { try { sessionStorage.removeItem(k); } catch {} },
};

// ── Native session persistence (Capacitor Preferences), 2026-09-06 ─────────
// An APK UPDATE wipes the WebView's localStorage (verified with a marker test),
// so the persistent-login mirror in localStorage (index.html) does NOT survive
// an update — the operator had to log in again after every version bump.
// Capacitor Preferences writes to NATIVE storage under the app's data dir,
// which DOES survive an update. So: on login we also write the session here;
// on cold start, if both sync stores are empty (post-update), we rehydrate from
// it. Once a user logs in on a build that has this plugin, EVERY later update
// keeps them logged in. Pure no-op on desktop / web (no Capacitor).
const NATIVE_BLOB = "mes_auth_persist";
function _prefs() {
  try {
    const C = typeof window !== "undefined" && window.Capacitor;
    if (!C || !C.isNativePlatform || !C.isNativePlatform()) return null;
    return (C.Plugins && C.Plugins.Preferences) || null;
  } catch { return null; }
}
async function nativeSave() {
  const P = _prefs(); if (!P) return;
  try {
    const o = {};
    for (const k of AUTH_KEYS) { const v = ss.get(k); if (v != null) o[k] = v; }
    if (o.mes_token) await P.set({ key: NATIVE_BLOB, value: JSON.stringify(o) });
  } catch {}
}
async function nativeClear() {
  const P = _prefs(); if (!P) return;
  try { await P.remove({ key: NATIVE_BLOB }); } catch {}
}
// Rehydrate sessionStorage (+ localStorage) from native storage when the sync
// stores are empty. Returns the recovered token, or null.
async function nativeRestore() {
  const P = _prefs(); if (!P) return null;
  try {
    const { value } = await P.get({ key: NATIVE_BLOB });
    if (!value) return null;
    const o = JSON.parse(value);
    if (!o || !o.mes_token) return null;
    for (const k of AUTH_KEYS) { if (o[k] != null) ss.set(k, o[k]); }
    try { localStorage.setItem(NATIVE_BLOB, JSON.stringify(o)); } catch {}
    return o.mes_token;
  } catch { return null; }
}

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => ss.get("mes_token") || "");
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const authHdr = useCallback(() => ({
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  }), [token]);

  const _setUserFromMe = (me) => {
    setUser({
      id:             me.id,
      username:       me.username,
      role:           me.role,
      departmentId:   me.department_id || null,
      departmentName: me.department_name || null,
      departmentSlug: me.department_slug || null,
      // Explicit per-page permission overrides set by admin from
      // Admin → Users → "Page Permissions".  Shape: { page_key: 'none'|'read'|'full' }
      // When a page isn't in this map, fall back to the role/dept defaults
      // baked into canAccess() below.
      permissions:    me.permissions || {},
      // 2026-07-31 — lines this login is scoped to (mes_operator_lines).
      // [{id, lineCode, lineName}].  EMPTY = unrestricted (admins / anyone
      // with no assignment), so existing logins behave exactly as before.
      // Consumers: the line list is filtered to these and the dashboard opens
      // on the first one, so a YNC-SS user never loads 16 other lines.
      assignedLines:  Array.isArray(me.assigned_lines) ? me.assigned_lines : [],
      // 2026-08-14 — the ACCESS LEVEL behind assignedLines, plus per-machine
      // scope, set from Admin → Users → Access → "Lines & Modules".
      // Shape: { lines: {"12":"full"}, machines: {"45":"read"} }.
      // EMPTY = unrestricted, matching assignedLines — see canAccessLine().
      scope:          (me.scope && typeof me.scope === "object")
                        ? { lines: me.scope.lines || {}, machines: me.scope.machines || {} }
                        : { lines: {}, machines: {} },
    });
    ss.set("user_role", me.role);
    ss.set("user_id", me.id);
    if (me.department_slug) ss.set("user_dept_slug", me.department_slug);
    else ss.remove("user_dept_slug");
  };

  // 2026-08-24 — resilient session restore.  On a page REFRESH the token is
  // still in sessionStorage, so we re-validate it against /api/auth/me.  The
  // old code logged the user out on ANY failure — including a transient one
  // (a network blip or the backend mid-restart) — which bounced the operator
  // off whatever page they refreshed on.  Now: only a real 401/403 (bad/expired
  // token) logs out; a 5xx or network error is RETRIED for ~12 s so a refresh
  // during a backend restart keeps the session and the page.
  useEffect(() => {
    let cancelled = false;
    const validate = (tok, attempt = 0) => {
      fetch(`${API}/api/auth/me`, { headers: { Authorization: `Bearer ${tok}` } })
        .then(async (r) => {
          if (r.ok) {
            const me = await r.json();
            if (!cancelled) { _setUserFromMe(me); nativeSave(); setLoading(false); }
            return;
          }
          if (r.status === 401 || r.status === 403) {   // token genuinely bad
            if (!cancelled) {
              setToken("");
              for (const k of AUTH_KEYS) ss.remove(k);
              // /me is the authoritative validity gate (a transient error is a
              // 5xx/network, retried above — never a 401/403). So a definitively
              // dead token is the one case where BOTH persist mirrors must be
              // dropped, otherwise every reopen would restore-and-fail. This is
              // what lets a real revoke/expiry actually reach the login screen
              // while ordinary blips never do.
              try { window.__mesPersistClear && window.__mesPersistClear(); } catch {}
              nativeClear();
              setLoading(false);
            }
            return;
          }
          throw new Error("transient");                 // 5xx → retry
        })
        .catch(() => {
          if (cancelled) return;
          if (attempt < 8) setTimeout(() => validate(tok, attempt + 1), 1500);
          else setLoading(false);                       // keep token; stop spinner
        });
    };
    const boot = async () => {
      let tok = ss.get("mes_token");
      if (!tok) {
        // Both sync stores are empty — most likely an APK update wiped the
        // WebView storage. Fall back to the durable NATIVE store (Capacitor
        // Preferences) before giving up and showing the login screen. `loading`
        // stays true across this await, so Protected shows the spinner (not a
        // /login flash) until we know whether a session was recovered.
        tok = await nativeRestore();
        if (tok && !cancelled) setToken(tok);
      }
      if (!tok) { if (!cancelled) setLoading(false); return; }  // no session → /login
      validate(tok);
    };
    boot();
    return () => { cancelled = true; };
  }, []);

  // Device telemetry — once the user is known, report this device (phone /
  // tablet / browser + app source + version) to the registry, then refresh
  // "last seen" every 10 min while the app stays open (wallboards / TWA).
  useEffect(() => {
    if (!user) return;
    deviceCheckin(ss.get("mes_token"));
    autoResubscribe(ss.get("mes_token"));   // keep push subscription live if enabled
    const t = setInterval(() => deviceCheckin(ss.get("mes_token")), 10 * 60 * 1000);
    return () => clearInterval(t);
  }, [user]);

  const login = async (username, password) => {
    const fd = new FormData();
    fd.append("username", username);
    fd.append("password", password);
    const res = await fetch(`${API}/api/auth/login`, { method: "POST", body: fd });
    if (!res.ok) {
      let msg = "Invalid credentials";
      try { const j = await res.json(); msg = j.detail || msg; } catch {}
      throw new Error(msg);
    }
    const data = await res.json();
    setToken(data.access_token);
    // Login response only carries id/username/role; department info comes
    // from /me — fetch it eagerly so the slide-nav can render the right
    // "{DeptName} Panel" label on the very first render.
    let me = null;
    try {
      const r = await fetch(`${API}/api/auth/me`, {
        headers: { Authorization: `Bearer ${data.access_token}` },
      });
      if (r.ok) me = await r.json();
    } catch {}
    if (me) _setUserFromMe(me);
    else setUser({ id: data.user_id, username: data.username, role: data.role,
                   departmentId: null, departmentName: null, departmentSlug: null });
    ss.set("mes_token",    data.access_token);
    ss.set("mes_username", data.username);
    ss.set("user_role",    data.role);
    ss.set("user_id",      data.user_id);
    // Persist to native storage too so this login survives even an APK update
    // (localStorage/[index.html] mirror alone is wiped by an update).
    nativeSave();
    return data;
  };

  // Set the session directly from a server-issued token (no password). Used by
  // the LAN panel auto-login: the server mints a token for the mapped display
  // user, keyed by panel IP, and the wall opens with no manual sign-in.
  const loginWithToken = async (tok) => {
    if (!tok) return false;
    setToken(tok);
    let me = null;
    try {
      const r = await fetch(`${API}/api/auth/me`, { headers: { Authorization: `Bearer ${tok}` } });
      if (r.ok) me = await r.json();
    } catch {}
    if (!me) { setToken(""); return false; }
    _setUserFromMe(me);
    ss.set("mes_token",    tok);
    ss.set("mes_username", me.username || "");
    ss.set("user_role",    me.role || "");
    if (me.id != null) ss.set("user_id", me.id);
    nativeSave();
    return true;
  };

  const logout = () => {
    // 2026-07-03 — best-effort server-side session invalidation: tell the API
    // to drop THIS token's jti so it can never be reused (sessions no longer
    // time-expire; they end on deliberate logout or a server restart).  Fire-
    // and-forget so logout stays instant even if the network is slow/offline.
    if (token) {
      try {
        fetch(`${API}/api/auth/logout`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        }).catch(() => {});
      } catch {}
    }
    setToken("");
    setUser(null);
    for (const k of AUTH_KEYS) ss.remove(k);
    // Android app: this is a DELIBERATE logout, so also drop BOTH durable
    // mirrors (localStorage via index.html, and native Preferences). Automatic
    // 401s must NOT reach here — they keep the mirrors so a reopen logs straight
    // back in.
    try { window.__mesPersistClear && window.__mesPersistClear(); } catch {}
    nativeClear();
  };

  // Role flags.  `plant_head` is admin-equivalent per spec — same powers as admin everywhere.
  const isAdmin      = user?.role === "admin" || user?.role === "plant_head";
  const isPlantHead  = user?.role === "plant_head";
  const isDepartment = user?.role === "department";
  const isProduction = user?.role === "production";
  const isOperator   = user?.role === "operator";
  // 2026-08-14 — shopfloor supervisor roles.  These are DEFAULTS only: the
  // per-user matrix in Admin → Users → Permissions overrides any page either
  // way, and Assign Lines scopes any of them to a single line.
  const isSectionIncharge    = user?.role === "section_incharge";
  const isProductionIncharge = user?.role === "production_incharge";
  const isQualityIncharge    = user?.role === "quality_incharge";
  // 2026-09-13 — Shift Compile roll-up hierarchy: operator → leader →
  // shift_incharge → section_incharge.  leader mirrors operator; shift_incharge
  // gets a compiled multi-line view inside Shift Compile.
  const isLeader        = user?.role === "leader";
  const isShiftIncharge = user?.role === "shift_incharge";
  const isIncharge = isSectionIncharge || isProductionIncharge || isQualityIncharge;

  // ── Per-page permissions ────────────────────────────────────────
  // Admin can override role defaults from Admin → Users → "Page
  // Permissions".  Three explicit levels:
  //   none  – page hidden / blocked even if role default would allow
  //   read  – page visible; admin sub-panels render readOnly
  //   full  – full CRUD access regardless of role
  // If a page isn't listed in user.permissions, fall through to the
  // role/department defaults below.
  const explicitPerm = (page) => {
    const p = user?.permissions?.[page];
    if (p === "none" || p === "read" || p === "full") return p;
    return null;
  };

  const canAccess = (page) => {
    // Explicit override always wins
    const ep = explicitPerm(page);
    if (ep === "none") return false;
    if (ep === "read" || ep === "full") return true;

    // Escalation (2026-09-01): anyone in a zone's chain may hold an
    // escalation at their level; the "My Escalations" page self-filters to the
    // current user, so it's safe to expose to everyone.  The chain SETUP page
    // is admin-only (backend PUT is admin-gated too).
    if (page === "my-escalations")   return true;
    if (page === "escalation-admin") return user?.role === "admin";
    // My Team (2026-09-13): seniors see their juniors' activeness down the leg.
    // Self-filtering (juniors only), so gate to roles that actually have a team.
    if (page === "my-team")
      return isAdmin || isSectionIncharge || isProductionIncharge || isShiftIncharge;
    // Employee Master (2026-09-13): shift incharge & above always reach it (for
    // Leader Allocation), regardless of the allowlist.  An explicit 'none' set
    // by admin (checked above) still hides it; everyone else needs a grant.
    if (page === "operators" &&
        (isAdmin || isSectionIncharge || isProductionIncharge || isShiftIncharge))
      return true;

    // Role/department defaults (no explicit override)
    if (isAdmin)      return true;     // admin + plant_head

    // 2026-09-13 — EXPLICIT ALLOWLIST (fixes the "panel says No Access but the
    // user still sees the page" mismatch).  Once an admin has GRANTED at least
    // one page to a non-admin user (read/full in Admin → Users → Access), that
    // set is the user's COMPLETE list: any page not granted stays hidden — the
    // role defaults below no longer leak in.  A user with only 'none' entries
    // (the old "hide these" pattern) or nothing set keeps the role defaults, so
    // no existing account is silently locked out.  Explicit read/full already
    // returned true, and explicit none already returned false, above.
    const _perms = user?.permissions || {};
    const _hasGrants = Object.values(_perms).some(v => v === "read" || v === "full");
    if (_hasGrants) return false;      // granted-list mode ⇒ ungranted page hidden
    // Operator + Leader: same shopfloor surface — dashboard, breakdown slip, and
    // the per-line Shift Compile review (2026-09-13: leader mirrors operator).
    if (isOperator || isLeader)
      return ["dashboard", "prod-breakdown-slip", "shift-compile"].includes(page);
    // Shift Incharge: compiled roll-up across their assigned lines (same Shift
    // Compile page, extra module) plus the standard supervisor read pages.
    if (isShiftIncharge)
      return ["dashboard", "historical", "process-graphs", "shift-allocation",
              "shift-compile", "shift-calculator", "waiting-time",
              "anything-wrong", "five-s", "pdca", "settings"].includes(page);

    // Andon History (physical Andon call log from maintenance_db) — default
    // visible to the maintenance department, the shopfloor / quality incharges
    // and production.  Any user can still be granted/revoked it per-user.
    if (page === "andon-history")
      return isDepartment || isSectionIncharge || isProductionIncharge || isQualityIncharge || isProduction;

    // Production Breakdown Slip — production fills the andon auto-slip.
    if (page === "prod-breakdown-slip")
      return isProduction || isSectionIncharge || isProductionIncharge || isOperator;

    // ── Shopfloor incharge roles (2026-08-14) ──────────────────────────
    // Starting points chosen to mirror the team each one owns.  Nothing is
    // locked in: Admin → Users → Permissions can set ANY page to
    // none/read/full per user, and Assign Lines narrows any of these to a
    // single line so an incharge who runs one line sees only that line.
    if (isSectionIncharge) {
      // Line supervisor.  Their own line's numbers plus the shopfloor tools
      // they raise issues in.  No import, no config panels.
      return ["dashboard", "historical", "process-graphs",
              "shift-allocation", "shift-compile", "shift-calculator", "waiting-time",
              "anything-wrong", "five-s", "pdca", "settings"].includes(page);
    }
    if (isProductionIncharge) {
      // Owns the production flow end to end, so everything the Production
      // role sees plus the material pages and the production config panel.
      return ["dashboard", "historical", "import", "process-graphs",
              "shift-allocation", "shift-compile", "shift-calculator", "waiting-time",
              "store", "dispatch",
              "anything-wrong", "five-s", "pdca",
              "admin-production", "department-panel", "settings"].includes(page);
    }
    if (isQualityIncharge) {
      // Owns the quality flow; reads production so they can trace a defect
      // back to the cycle that produced it.
      return ["dashboard", "historical", "process-graphs",
              "quality-dashboard", "quality-deviations", "comments-history",
              "sa-fi-history",
              "weld-monitor", "admin-quality",
              "anything-wrong", "five-s", "pdca",
              "department-panel", "settings"].includes(page);
    }
    if (isProduction) {
      // Production user sees the same Production-side pages plus the
      // Production config Panel — read-only.  They can READ Plants /
      // Zones / Lines / Machines / Status / Hourly Mail but not edit;
      // the read-only enforcement is handled inside AdminPanel itself.
      // Audit Log is admin-only — intentionally NOT in this list.
      return ["dashboard", "historical", "import", "settings",
              "admin-production", "department-panel",
              "process-graphs", "shift-allocation", "shift-compile",
              "store", "dispatch", "shift-calculator", "kanban", "anything-wrong", "five-s", "pdca"].includes(page);
    }
    if (isDepartment) {
      // Per-department access lists.  Each department user gets read-only
      // access to its own admin panel section (admin-maintenance /
      // admin-quality) so they can READ Poka Yoke / Mail Settings /
      // KPI Targets etc. without being able to mutate them.
      const slug = (user?.departmentSlug || "").toLowerCase();
      if (slug === "maintenance") {
        return ["dashboard", "department-panel", "admin-maintenance",
                "maintenance-historical", "maintenance-capa",
                "maintenance-deviations", "maintenance-poka-yoke",
                "maintenance-logbook", "maintenance-pm",
                "process-graphs", "settings"].includes(page);
      }
      if (slug === "quality") {
        // Quality dept user lands on QualityDashboard at /dashboard
        // (DashboardForUser switch in App.jsx routes them there).
        //   /quality-dashboard  → zone-tile health view (live PY status)
        //   /quality-deviations → Deviation approvals + 4M Change Notes
        //   /shift-allocation   → Quality has read-access so they can
        //                          inspect allocations when an alert
        //                          banner pops up; ack happens via the
        //                          dashboard banner (see ManpowerAlertBanner).
        return ["dashboard", "department-panel", "admin-quality",
                "quality-dashboard", "quality-deviations", "weld-monitor",
                "sa-fi-history",
                "shift-allocation", "settings"].includes(page);
      }
      if (slug === "production") {
        return ["dashboard", "department-panel", "admin-production",
                "historical", "import", "settings",
                "process-graphs", "shift-allocation",
                "store", "dispatch", "shift-calculator", "kanban", "anything-wrong", "heijunka", "five-s", "pdca"].includes(page);
      }
      return ["dashboard", "historical", "import", "settings", "department-panel"].includes(page);
    }
    return false;
  };

  // ── Module (sub-tab) access (2026-09-13) ─────────────────────────────
  // Some pages have named sub-modules (e.g. Historical's 5 tabs) that an admin
  // can assign individually.  Mirrors the page allowlist: with no module of a
  // page granted, ALL its modules show; once ANY is granted, only granted ones
  // show.  Keys are `${page}::${module}` in the same permissions map.
  const canAccessModule = (page, mod) => {
    if (isAdmin) return true;
    if (!canAccess(page)) return false;            // must have the page first
    const perms = user?.permissions || {};
    const k = `${page}::${mod}`;
    const ep = perms[k];
    if (ep === "none") return false;
    if (ep === "read" || ep === "full") return true;
    // No explicit setting for this module → allowlist: if the admin granted
    // ANY module under this page, hide the ones they didn't; otherwise show all.
    const prefix = `${page}::`;
    const anyGrant = Object.keys(perms).some(
      key => key.startsWith(prefix) && (perms[key] === "read" || perms[key] === "full"));
    return !anyGrant;
  };

  // ── Line / machine scope (2026-08-14) ────────────────────────────────
  // The invariant everything here rests on: an EMPTY scope means UNRESTRICTED,
  // not "denied".  Every account that predates this feature has an empty scope,
  // so all of them keep seeing everything until an admin deliberately narrows
  // them.  Admins are always unrestricted — the API refuses to scope them.
  const _scopeLevel = (bucket, id) => {
    if (isAdmin) return "full";
    const map = user?.scope?.[bucket] || {};
    if (!Object.keys(map).length) return "full";     // never scoped → wide open
    return map[String(id)] || "none";
  };

  // Can this user see line / machine <id> at all?
  const canAccessLine    = (id) => _scopeLevel("lines", id)    !== "none";
  const canAccessMachine = (id) => _scopeLevel("machines", id) !== "none";
  // Can they change things on it?  Needs BOTH the page and the line to be full:
  // read-only on a line must not be escalated by a full-CRUD page grant.
  const canWriteLine     = (id) => _scopeLevel("lines", id)    === "full";
  const canWriteMachine  = (id) => _scopeLevel("machines", id) === "full";
  // Convenience for list screens: keep only the lines this login may see.
  // Accepts rows shaped {id} or {line_id}.
  const filterLines = (rows) => Array.isArray(rows)
    ? rows.filter(r => canAccessLine(r?.id ?? r?.line_id))
    : rows;

  // canWrite(page) — does this user have FULL CRUD on the given page?
  // Admin/plant_head always yes.  For everyone else:
  //   • explicit 'full' permission → yes
  //   • explicit 'read' / 'none'   → no (read-only or hidden)
  //   • no explicit permission     → fall back to role-based default
  //                                   (production user editing config
  //                                   pages = read-only; etc.)
  const canWrite = (page) => {
    if (isAdmin) return true;
    const ep = explicitPerm(page);
    if (ep === "full") return true;
    if (ep === "read" || ep === "none") return false;
    // Shift Allocation is supervisor-facing — Production users + the
    // Production dept get write access by default so they can run the
    // daily allocation flow without an explicit per-user override.
    if (page === "shift-allocation") {
      if (isProduction) return true;
      if (isDepartment && (user?.departmentSlug || "").toLowerCase() === "production") return true;
      if (isProductionIncharge) return true;
    }
    // Shift Compile — the line leader (section_incharge) signs off their shift;
    // production roles may too.  Per-LINE gating is enforced server-side
    // (can_close), so this only opens the page-level write capability.
    if (page === "shift-compile") {
      if (isSectionIncharge || isProductionIncharge || isProduction) return true;
      if (isDepartment && (user?.departmentSlug || "").toLowerCase() === "production") return true;
    }
    // 2026-08-14 — incharge write defaults.  Each writes the flow it OWNS;
    // config/admin panels stay read-only until an admin grants 'full' on that
    // page explicitly, which matches how the production role already behaves.
    if (isIncharge) {
      // Shopfloor reporting is where all three are expected to act.
      if (["anything-wrong", "five-s", "pdca"].includes(page)) return true;
      if (isProductionIncharge)
        return ["import", "store", "dispatch", "shift-calculator"].includes(page);
      if (isQualityIncharge)
        return ["quality-deviations", "comments-history", "weld-monitor"].includes(page);
      return false;                      // section incharge: report, don't edit
    }
    // No explicit perm — historical default: only admins write,
    // department / production / operator users are read-only.
    return false;
  };

  // ── Theme color (per-role) ─────────────────────────────────────────
  // Production-default = blue, but each user's UI gets tinted by their
  // role / department:
  //   admin / plant_head      → blue   (universal — admin sees every
  //                             page in blue regardless of which dept's
  //                             page they're viewing)
  //   department:maintenance  → red
  //   department:quality      → yellow / amber
  //   department:<other>      → blue (fallback until that dept's flow
  //                             is finalised)
  //   production              → green
  //   operator                → blue
  // The picked theme exposes both a single `accent` colour and a
  // matching gradient — components consume via `theme` from useAuth().
  const PALETTE = {
    blue:   { accent: "#2563eb", accentDark: "#1e40af",
              gradient: "linear-gradient(90deg,#1e40af,#2563eb,#60a5fa)",
              soft: "rgba(30,64,175,.08)" },
    red:    { accent: "#dc2626", accentDark: "#b91c1c",
              gradient: "linear-gradient(90deg,#dc2626,#ea580c,#f59e0b)",
              soft: "rgba(220,38,38,.08)" },
    yellow: { accent: "#ca8a04", accentDark: "#a16207",
              gradient: "linear-gradient(90deg,#a16207,#ca8a04,#fbbf24)",
              soft: "rgba(202,138,4,.10)" },
    green:  { accent: "#16a34a", accentDark: "#15803d",
              gradient: "linear-gradient(90deg,#15803d,#16a34a,#4ade80)",
              soft: "rgba(22,163,74,.08)" },
  };
  const themeKey = (() => {
    // Admin + plant_head always blue (universal — they see every panel
    // in blue regardless of which dept's section is currently shown).
    if (isAdmin) return "blue";
    if (isDepartment) {
      const slug = (user?.departmentSlug || "").toLowerCase();
      if (slug === "maintenance") return "red";
      if (slug === "quality")     return "yellow";
      if (slug === "production")  return "green";
      return "blue";
    }
    if (isProduction) return "green";   // role='production' (legacy non-dept)
    // 2026-08-14 — incharge roles take the palette of the team they own, so
    // the UI reads the same as the matching department login.
    if (isProductionIncharge || isSectionIncharge) return "green";
    if (isQualityIncharge)                         return "yellow";
    return "blue";
  })();
  const theme = { ...PALETTE[themeKey], key: themeKey };

  return (
    <AuthContext.Provider value={{
      token, user, loading, login, loginWithToken, logout,
      authHdr, isAdmin, isPlantHead, isDepartment, isProduction, isOperator,
      isSectionIncharge, isProductionIncharge, isQualityIncharge, isIncharge,
      canAccess, canAccessModule, canWrite, API,
      canAccessLine, canAccessMachine, canWriteLine, canWriteMachine, filterLines,
      theme, themeKey,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
