import { createContext, useContext, useState, useEffect, useCallback } from "react";

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
    });
    ss.set("user_role", me.role);
    ss.set("user_id", me.id);
    if (me.department_slug) ss.set("user_dept_slug", me.department_slug);
    else ss.remove("user_dept_slug");
  };

  useEffect(() => {
    if (!token) { setLoading(false); return; }
    fetch(`${API}/api/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(_setUserFromMe)
      .catch(() => {
        setToken("");
        for (const k of AUTH_KEYS) ss.remove(k);
      })
      .finally(() => setLoading(false));
  }, []);

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
    return data;
  };

  const logout = () => {
    setToken("");
    setUser(null);
    for (const k of AUTH_KEYS) ss.remove(k);
  };

  // Role flags.  `plant_head` is admin-equivalent per spec — same powers as admin everywhere.
  const isAdmin      = user?.role === "admin" || user?.role === "plant_head";
  const isPlantHead  = user?.role === "plant_head";
  const isDepartment = user?.role === "department";
  const isProduction = user?.role === "production";
  const isOperator   = user?.role === "operator";

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

    // Role/department defaults (no explicit override)
    if (isAdmin)      return true;     // admin + plant_head
    if (isOperator)   return page === "dashboard";
    if (isProduction) {
      // Production user sees the same Production-side pages plus the
      // Production config Panel — read-only.  They can READ Plants /
      // Zones / Lines / Machines / Status / Hourly Mail but not edit;
      // the read-only enforcement is handled inside AdminPanel itself.
      // Audit Log is admin-only — intentionally NOT in this list.
      return ["dashboard", "historical", "import", "settings",
              "admin-production", "department-panel",
              "process-graphs", "shift-allocation",
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
                "quality-dashboard", "quality-deviations",
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
    return "blue";
  })();
  const theme = { ...PALETTE[themeKey], key: themeKey };

  return (
    <AuthContext.Provider value={{
      token, user, loading, login, logout,
      authHdr, isAdmin, isPlantHead, isDepartment, isProduction, isOperator,
      canAccess, canWrite, API,
      theme, themeKey,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
