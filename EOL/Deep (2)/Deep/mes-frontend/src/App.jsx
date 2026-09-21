import { useEffect, useState, lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate, useLocation, useNavigate } from "react-router-dom";
import { record as recordTiming, setTimingToken } from "./api/timing";
import { AuthProvider, useAuth } from "./context/AuthContext";
import UpdateBanner from "./components/UpdateBanner";
import Layout from "./components/Layout";

// Pages
import Login      from "./pages/Login";
const Dashboard = lazy(() => import("./pages/Dashboard"));
const MaintenanceDashboard = lazy(() => import("./pages/MaintenanceDashboard"));
const MaintenanceHistorical = lazy(() => import("./pages/MaintenanceHistorical"));
const MaintenanceCAPA = lazy(() => import("./pages/MaintenanceCAPA"));
const MaintenanceDeviations = lazy(() => import("./pages/MaintenanceDeviations"));
const MaintenancePokaYoke = lazy(() => import("./pages/MaintenancePokaYoke"));
const LogBook = lazy(() => import("./pages/LogBook"));
const PMPanel = lazy(() => import("./pages/PMPanel"));
const ProcessGraphs = lazy(() => import("./pages/ProcessGraphs"));
const QualityDashboard = lazy(() => import("./pages/QualityDashboard"));
const WeldMonitor = lazy(() => import("./pages/WeldMonitor"));
const QualityDeviations = lazy(() => import("./pages/QualityDeviations"));
const SaFiQualityHistory = lazy(() => import("./pages/SaFiQualityHistory"));
const CommentsHistory = lazy(() => import("./pages/CommentsHistory"));
const Historical = lazy(() => import("./pages/Historical"));
const Audit = lazy(() => import("./pages/Audit"));
const WaitingTime = lazy(() => import("./pages/WaitingTime"));
const Settings = lazy(() => import("./pages/Settings"));
const AdminPanel = lazy(() => import("./pages/AdminPanel"));
const ProductionAdminPanel = lazy(() => import("./pages/AdminPanel").then(m => ({ default: m.ProductionAdminPanel })));
const MaintenanceAdminPanel = lazy(() => import("./pages/AdminPanel").then(m => ({ default: m.MaintenanceAdminPanel })));
const QualityAdminPanel = lazy(() => import("./pages/AdminPanel").then(m => ({ default: m.QualityAdminPanel })));
const NetworkPanel = lazy(() => import("./pages/NetworkPanel"));
const DepartmentPanel = lazy(() => import("./pages/DepartmentPanel"));
const Fullscreen = lazy(() => import("./pages/Fullscreen"));
const FullscreenYWD = lazy(() => import("./pages/FullscreenYWD"));
const SubmachineFullscreen = lazy(() => import("./pages/SubmachineFullscreen"));
const WallboardLeft = lazy(() => import("./pages/WallboardLeft"));
const ShiftAllocation = lazy(() => import("./pages/ShiftAllocation"));
const ShiftCompile = lazy(() => import("./pages/ShiftCompile"));
const OperatorMaster = lazy(() => import("./pages/OperatorMaster"));
const MyTeam = lazy(() => import("./pages/MyTeam"));
const AndonHistory = lazy(() => import("./pages/AndonHistory"));
const ProdBreakdownSlip = lazy(() => import("./pages/ProdBreakdownSlip"));
const MyEscalations = lazy(() => import("./pages/MyEscalations"));
const EscalationAdmin = lazy(() => import("./pages/EscalationAdmin"));
const DeviceRegistry = lazy(() => import("./pages/DeviceRegistry"));
const StorePage = lazy(() => import("./pages/StorePage"));
const DispatchPage = lazy(() => import("./pages/DispatchPage"));
const ShiftCalculator = lazy(() => import("./pages/ShiftCalculator"));
const KanbanPage = lazy(() => import("./pages/KanbanPage"));
const AnythingWrongPage = lazy(() => import("./pages/AnythingWrongPage"));
const HeijunkaPage = lazy(() => import("./pages/HeijunkaPage"));
const FiveSPage = lazy(() => import("./pages/FiveSPage"));
const PDCAPage = lazy(() => import("./pages/PDCAPage"));
const SixSigmaPage = lazy(() => import("./pages/SixSigmaPage"));
const BinFillingPage = lazy(() => import("./pages/BinFillingPage"));
const PeffSheet = lazy(() => import("./pages/PeffSheet"));
const LogViewer = lazy(() => import("./pages/LogViewer"));
const VideoCoverage = lazy(() => import("./pages/VideoCoverage"));
const PyBypass = lazy(() => import("./pages/PyBypass"));
import { getApkInfo } from "./api/appVersion";

// ─── Dashboard switch ──────────────────────────────────────────────────────
// `/dashboard` shows different pages based on the logged-in user:
//   • Maintenance department user → MaintenanceDashboard (ANDON, history, stats)
//   • everyone else (admin, plant_head, zone, operator, other dept users)
//     → the regular Production Dashboard
//
// SlideNav still labels the entry as just "Dashboard" — the dispatch is
// transparent so the URL is the same for every user.
function DashboardForUser() {
  const { user } = useAuth();
  if (user?.role === "department" && user?.departmentSlug === "maintenance") {
    return <MaintenanceDashboard />;
  }
  if (user?.role === "department" && user?.departmentSlug === "quality") {
    return <QualityDashboard />;
  }
  return <Dashboard />;
}

// ─── Protected Route ───────────────────────────────────────────────────────
// Redirects to /login if not authenticated
// Redirects to /dashboard if role doesn't have access to the page
//
// `bare` prop: skip <Layout> wrapper (no slide-nav).  Used for the
// shop-floor TV views (/fullscreen, /submachine-fullscreen) — they need
// the entire viewport for the dashboard, no chrome.  Auth check still
// runs the same way; only the layout wrapping differs.
// 2026-08-11 — page-open timing.  Measured from the route changing to the
// first paint after it, which is what the user experiences as "the page
// opened".  requestAnimationFrame fires after the browser has painted, so this
// includes React's render, not just the router swap.  Feeds the Waiting Time
// page; failures are silent by design.
function PageTiming() {
  const loc = useLocation();
  const { token, user } = useAuth();

  // Keep the telemetry module's copy of the token in step with the session, so
  // beacons can be attributed to a person (see api/timing.js).
  useEffect(() => { setTimingToken(token); }, [token, user?.username]);

  useEffect(() => {
    const t0 = performance.now();
    let raf2 = 0;
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => {
        recordTiming("page", loc.pathname, performance.now() - t0);
      });
    });
    // 2026-08-14 — DWELL: how long this route stayed open.  The cleanup runs on
    // every route change and on unmount, which is exactly "the user left this
    // page".  A tab closed outright is covered by the pagehide flush in
    // timing.js.  Measured from mount, not from first paint, because the
    // question being answered is "how long were they on this screen".
    const opened = performance.now();
    const here = loc.pathname;
    return () => {
      cancelAnimationFrame(raf1); cancelAnimationFrame(raf2);
      recordTiming("dwell", here, performance.now() - opened);
    };
  }, [loc.pathname]);
  return null;
}

// ── Native-app notifications (2026-09-06) ───────────────────────────────
// The installed Android app's WebView can't do Web Push (no Notification /
// PushManager API), so while logged in on the native app we POLL the server
// inbox (/api/push/pending) and fire a LOCAL notification for each new alert
// (escalations etc.). Works while the app is open or backgrounded. Browser /
// PWA keep using Web Push and skip this. Renders nothing.
function NotifyPoller() {
  const { token } = useAuth();
  useEffect(() => {
    if (!token) return;
    const cap = typeof window !== "undefined" && window.Capacitor;
    const isNative = cap && cap.isNativePlatform && cap.isNativePlatform();
    if (!isNative) return;                    // browser / PWA → Web Push instead

    // 2026-09-06 — apk v2.0.8+ ships a native foreground service (AlertService)
    // that keeps polling + posting notifications even when the app is CLOSED,
    // which the JS poller below can't (Android suspends off-screen timers). On
    // those builds we HAND OFF to it entirely — the service is the single inbox
    // consumer (no duplicate notifications), so the JS loop must NOT run. We
    // gate on the running apk version (not on the plugin proxy) so the hand-off
    // is reliable; the service is also started natively from MainActivity, and
    // this NB.start() is a best-effort nudge for the fresh-login-mid-session
    // case + the one-time battery-optimisation exemption.
    const verGte = (a, b) => {
      const pa = String(a || "0").split("."), pb = String(b || "0").split(".");
      for (let i = 0; i < 3; i++) {
        const d = (parseInt(pa[i], 10) || 0) - (parseInt(pb[i], 10) || 0);
        if (d) return d > 0;
      }
      return true;
    };
    const av = getApkInfo().version;
    if (av && verGte(av, "2.0.8")) {
      const NB = cap.Plugins && cap.Plugins.NotifBg;
      if (NB && NB.start) {
        (async () => {
          try { await NB.start(); } catch {}
          try {
            if (!localStorage.getItem("mes_batt_asked")) {
              await NB.requestBatteryExemption();
              try { localStorage.setItem("mes_batt_asked", "1"); } catch {}
            }
          } catch {}
        })();
      }
      return;                                  // native service owns alerts
    }

    // ---- Fallback (older apk without the service): JS poller — only fires
    //      while the app is open/backgrounded. ----
    const LN = cap.Plugins && cap.Plugins.LocalNotifications;
    if (!LN) return;

    let stop = false, since = 0, seq = (Date.now() % 1000000) + 1, tapSub = null;
    (async () => {
      try { await LN.requestPermissions(); } catch {}
      try {
        await LN.createChannel({
          id: "mes-alerts", name: "MES Alerts",
          description: "Alarm and escalation alerts",
          importance: 5, visibility: 1, vibration: true,
        });
      } catch {}
      try {
        tapSub = await LN.addListener("localNotificationActionPerformed", (a) => {
          const url = a && a.notification && a.notification.extra && a.notification.extra.url;
          if (url) { try { window.location.href = url; } catch {} }
        });
      } catch {}
    })();

    const poll = async () => {
      try {
        const r = await fetch(`/api/push/pending?since=${since}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!r.ok) return;
        const j = await r.json();
        if (j && j.now) since = j.now;        // advance baseline (no backlog)
        for (const it of (j && j.items) || []) {
          seq = (seq % 2000000000) + 1;
          try {
            await LN.schedule({ notifications: [{
              id: seq,
              title: it.title || "TBDI MES",
              body:  it.body  || "",
              channelId: "mes-alerts",
              extra: { url: it.url || "/my-escalations" },
            }]});
          } catch {}
        }
      } catch {}
    };
    poll();                                   // baseline call: sets `since`
    const iv = setInterval(() => { if (!stop) poll(); }, 45000);
    return () => {
      stop = true; clearInterval(iv);
      try { tapSub && tapSub.remove && tapSub.remove(); } catch {}
    };
  }, [token]);
  return null;
}

// LAN panel (65") ONLY: zoom the two shop-floor wall dashboards — Management
// (/:line/MANAGEMENT, /fullscreen) and Supervisor (/:line/SUPERVISOR,
// /wallboard/left) — to 90% so they fit the big screen better. Every OTHER page
// and every non-LAN device stays at 100%. Uses `zoom` (NOT transform) so sticky
// headers / fixed modals aren't broken — a transform re-anchors position:fixed
// and clipped the layout when tried globally.
function PanelZoom() {
  const { pathname } = useLocation();
  useEffect(() => {
    const isWallPath = (p) => (p || "").endsWith("/MANAGEMENT") || (p || "").endsWith("/SUPERVISOR") ||
                       (p || "").startsWith("/fullscreen/") || (p || "").startsWith("/ywd-fullscreen/") ||
                       (p || "").startsWith("/wallboard/left/");
    let lan = false; try { lan = getApkInfo().isLan; } catch {}
    const wall = lan && isWallPath(pathname);

    // 2026-09-13 — LAN wall: render TEXT at 90% only (layout untouched → the grid
    // still fills the panel, so NO bottom gap — unlike a page-zoom which shrank the
    // whole 100vh grid). Only elements that set their OWN font-size are scaled;
    // children with no own size inherit the already-scaled value from their nearest
    // scaled ancestor, so nothing compounds. The original size is cached in
    // data-fs0 so repeated passes (for late/newly-mounted nodes) never re-scale.
    const TEXT_SCALE = 0.9;
    const scaleText = () => {
      if (!wall) return;
      const root = document.getElementById("root");
      if (!root) return;
      const nodes = root.querySelectorAll("*");
      for (let i = 0; i < nodes.length; i++) {
        const el = nodes[i];
        if (el.dataset.fs0 === undefined) {
          const own = el.style && el.style.fontSize;   // own inline size only
          if (!own || own.indexOf("var(") !== -1) continue;
          const base = parseFloat(own);
          if (!base) continue;
          el.dataset.fs0 = String(base);
        }
        const base = parseFloat(el.dataset.fs0);
        if (base) el.style.setProperty("font-size", (base * TEXT_SCALE).toFixed(2) + "px", "important");
      }
    };

    const apply = () => {
      if (wall) scaleText();   // shrink text first so the fit measure sees it
      let z = "";
      try {
        if (wall) {
          // 2026-09-13 — FIT the wall to the panel (fill, no black gap). A fixed
          // 0.8/0.9 shrank the 100vh grid and left dead space at the bottom; here
          // we measure and scale down ONLY if the content actually overflows,
          // else zoom 1 = full fill. Works for both 9:16 and 16:9 orientations.
          document.documentElement.style.zoom = "";
          const vw = window.innerWidth, vh = window.innerHeight;
          const root = document.getElementById("root");
          const cw = Math.max(root ? root.scrollWidth : 0, document.documentElement.scrollWidth, vw);
          const ch = Math.max(root ? root.scrollHeight : 0, document.documentElement.scrollHeight, vh);
          let fit = Math.min(vw / (cw || vw), vh / (ch || vh));
          if (!isFinite(fit) || fit <= 0) fit = 1;
          fit = Math.max(0.7, Math.min(1, fit));
          z = fit >= 0.999 ? "" : String(Number(fit.toFixed(3)));
        }
      } catch {}
      try {
        document.documentElement.style.zoom = z;
        // Paint the whole background stack dark on the wall so any sub-pixel gap
        // between the dark tiles blends in (no thin white dashes). Cleared off-wall.
        const dark = wall ? "#0b1220" : "";
        document.documentElement.style.background = dark;
        if (document.body) document.body.style.background = dark;
        const root = document.getElementById("root");
        if (root) { root.style.background = dark; root.style.minHeight = wall ? "100vh" : ""; }
      } catch {}
    };

    apply();
    // Re-fit after the async charts have laid out (the grid height can settle late).
    const t1 = setTimeout(apply, 900);
    const t2 = setTimeout(apply, 2200);
    // Catch late / newly-mounted wall elements (modals, dropdowns, chart labels).
    const iv = wall ? setInterval(scaleText, 1500) : null;
    window.addEventListener("resize", apply);
    return () => { clearTimeout(t1); clearTimeout(t2); if (iv) clearInterval(iv); window.removeEventListener("resize", apply); };
  }, [pathname]);
  return null;
}

// LAN panel only: on first load, jump straight to the dashboard mapped to this
// panel's IP (Admin → Production → Panel Displays).  Fires once per session and
// only from the default landing, so the user can still navigate away freely.
function PanelAutoOpen() {
  const { token, user, API } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  useEffect(() => {
    if (!user || !token) return;
    let isLan = false; try { isLan = getApkInfo().isLan; } catch {}
    if (!isLan) return;
    try { if (sessionStorage.getItem("mes_panel_autoopened")) return; } catch {}
    if (loc.pathname !== "/" && loc.pathname !== "/dashboard") return;
    (async () => {
      try {
        const r = await fetch(`${API}/api/panel/auto-open`, { headers: { Authorization: `Bearer ${token}` } });
        if (!r.ok) return;
        const j = await r.json();
        try { sessionStorage.setItem("mes_panel_autoopened", "1"); } catch {}
        if (j && j.path && j.path !== loc.pathname) nav(j.path, { replace: true });
      } catch {}
    })();
  }, [user, token, loc.pathname]);   // eslint-disable-line
  return null;
}

// LAN panel auto-LOGIN: if this panel's IP is mapped with an auto-login user
// (Admin → Panel Displays), the server mints a token for that display account
// keyed by the panel IP, so the wall signs in and opens its dashboard with NO
// manual password — and re-recovers automatically after an API restart (which
// invalidates the persisted session). No password is stored or sent.
function PanelAutoLogin() {
  const { token, user, loading, loginWithToken, API } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  useEffect(() => {
    if (loading || token || user) return;          // wait for boot; skip if signed in
    let isLan = false; try { isLan = getApkInfo().isLan; } catch {}
    if (!isLan) return;
    if (loc.pathname !== "/" && loc.pathname !== "/login") return;
    try { if (sessionStorage.getItem("mes_panel_autologin_tried")) return; } catch {}
    try { sessionStorage.setItem("mes_panel_autologin_tried", "1"); } catch {}
    (async () => {
      try {
        const r = await fetch(`${API}/api/panel/auto-login`);
        if (!r.ok) return;
        const j = await r.json();
        if (j && j.token) {
          const ok = await loginWithToken(j.token);
          if (ok) nav(j.path || "/dashboard", { replace: true });
        }
      } catch {}
    })();
  }, [loading, token, user, loc.pathname]);   // eslint-disable-line
  return null;
}

// LAN panel (65") only: a small always-on Refresh button. The app is a
// WebView (no browser reload), so if the panel ever loses the LAN link and the
// live data freezes, there was no way to force a reload — this gives one.
function LanRefreshButton() {
  const { isLan } = getApkInfo();
  if (!isLan) return null;
  const [spin, setSpin] = useState(false);
  return (
    <button
      onClick={() => { setSpin(true); try { window.location.reload(); } catch {} }}
      title="Refresh"
      aria-label="Refresh"
      style={{
        position: "fixed", right: 12, top: 12, zIndex: 2147483000,
        width: 44, height: 44, borderRadius: 12,
        border: "1px solid rgba(255,255,255,0.28)",
        background: "rgba(15,23,42,0.72)", color: "#fff",
        fontSize: 22, lineHeight: 1, cursor: "pointer",
        display: "flex", alignItems: "center", justifyContent: "center",
        boxShadow: "0 2px 10px rgba(0,0,0,.35)",
      }}>
      <span style={{ display: "inline-block", animation: spin ? "lanrefspin .7s linear infinite" : "none" }}>↻</span>
      <style>{`@keyframes lanrefspin{to{transform:rotate(360deg)}}`}</style>
    </button>
  );
}

// LAN panel (65") only: a watchdog that auto-reloads the kiosk if live data
// freezes. It reloads ONLY when the app had been working (a server response
// arrived at least once) and then went silent for 2 min — so a genuinely
// unreachable server (cold start, never connected) never triggers a reload
// loop. Handles occasional factory-WiFi blips on an unattended screen.
function LanWatchdog() {
  const { isLan } = getApkInfo();
  useEffect(() => {
    if (!isLan) return;
    const STALE_MS = 120000;
    const iv = setInterval(() => {
      try {
        const last = window.__mesLastApiOk;
        if (last && (Date.now() - last) > STALE_MS) {
          window.location.reload();
        }
      } catch {}
    }, 30000);
    return () => clearInterval(iv);
  }, [isLan]);
  return null;
}

// 2026-09-16 — Landing pages, in the order we'd rather drop a user on.  Once an
// admin grants ANY page to a user that grant list becomes their COMPLETE
// allowlist (see canAccess), so a restricted account often has NO dashboard
// access — and the old "denied → go to /dashboard" rule then bounced
// /dashboard back to /dashboard forever and the screen stayed BLANK (operator:
// "ss wali id pe blank screen"). Landing on the first page the user CAN open
// fixes it for every restricted account, not just that one.
const LANDING_PAGES = [
  ["dashboard",            "/dashboard"],
  ["maintenance-dashboard","/maintenance-dashboard"],
  ["historical",           "/historical"],
  ["shift-compile",        "/shift-compile"],
  ["prod-breakdown-slip",  "/prod-breakdown-slip"],
  ["comments-history",     "/comments-history"],
  ["shift-allocation",     "/shift-allocation"],
  ["anything-wrong",       "/anything-wrong"],
  ["my-escalations",       "/my-escalations"],
  ["operators",            "/operators"],
];
function useFirstAllowedPath() {
  const { canAccess } = useAuth();
  for (const [key, path] of LANDING_PAGES) {
    try { if (canAccess(key)) return path; } catch { /* ignore */ }
  }
  return null;               // nothing at all → caller shows a message, never loops
}

function NoAccessScreen() {
  const { user, logout } = useAuth();
  return (
    <div style={{ height: "100vh", display: "flex", alignItems: "center",
                  justifyContent: "center", background: "#f8fafc", padding: 24 }}>
      <div style={{ textAlign: "center", maxWidth: 460 }}>
        <div style={{ fontSize: 40, marginBottom: 10 }}>🔒</div>
        <div style={{ fontSize: 18, fontWeight: 800, color: "#0f172a" }}>No pages assigned</div>
        <div style={{ fontSize: 13.5, color: "#64748b", marginTop: 8, lineHeight: 1.6 }}>
          Your account{user?.username ? ` (${user.username})` : ""} does not have access to any
          page yet. Ask an administrator to grant access in
          <b> Admin → Users → Access</b>.
        </div>
        <button onClick={() => { try { logout && logout(); } catch {} }}
                style={{ marginTop: 18, background: "#1e40af", color: "#fff", border: "none",
                         borderRadius: 8, padding: "9px 20px", fontSize: 13,
                         fontWeight: 700, cursor: "pointer" }}>
          Sign out
        </button>
      </div>
    </div>
  );
}

function Protected({ children, requiredAccess, bare = false }) {
  const { token, loading, canAccess } = useAuth();
  const location = useLocation();
  const firstAllowed = useFirstAllowedPath();

  if (loading) return (
    <div style={{
      height: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
      background: "#f8fafc", color: "#64748b", fontSize: 14,
    }}>
      <div style={{ textAlign: "center" }}>
        <div className="spinner" style={{
          width: 32, height: 32, borderRadius: "50%",
          border: "3px solid #e2e8f0", borderTopColor: "#1e40af",
          animation: "spin 0.6s linear infinite",
          margin: "0 auto 12px",
        }} />
        Loading…
      </div>
      <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>
    </div>
  );

  if (!token) return <Navigate to="/login" state={{ from: location }} replace />;

  if (requiredAccess && !canAccess(requiredAccess)) {
    // Send them to the first page they CAN open. Never redirect to the path we
    // are already on — that is what produced the blank-screen redirect loop.
    if (!firstAllowed) return <NoAccessScreen />;
    if (firstAllowed === location.pathname) return <NoAccessScreen />;
    return <Navigate to={firstAllowed} replace />;
  }

  return bare ? children : <Layout>{children}</Layout>;
}

// ─── Root redirect ──────────────────────────────────────────────────────────
// Every authenticated user lands on /dashboard.  The Dashboard route
// itself is a switch (DashboardForUser) — Maintenance department users
// see MaintenanceDashboard there, everyone else sees the regular
// Production Dashboard.  The "Maintenance Panel" (read-only PY) is
// reachable from the slide-nav, not as the default landing page.
function RootRedirect() {
  const { token, loading } = useAuth();
  const firstAllowed = useFirstAllowedPath();
  if (loading) return null;
  if (!token) return <Navigate to="/login" replace />;
  // Restricted accounts may have no dashboard grant — land them on their first
  // allowed page instead of a route that will just bounce them back.
  if (!firstAllowed) return <NoAccessScreen />;
  return <Navigate to={firstAllowed} replace />;
}

// ─── App ────────────────────────────────────────────────────────────────────
function AppRoutes() {
  return (
    <>
    {/* Records page-open time for the Waiting Time page.  Sits beside <Routes>
        (not inside it — Routes only accepts Route children) and renders null. */}
    <PageTiming />
    <NotifyPoller />
    <PanelZoom />
    <PanelAutoLogin />
    <PanelAutoOpen />
    <LanRefreshButton />
    <LanWatchdog />
    <UpdateBanner />
    <Suspense fallback={
      <div style={{ position: "fixed", inset: 0, background: "#0f172a", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 9998 }}>
        <div style={{ width: 44, height: 44, borderRadius: "50%", border: "4px solid rgba(255,255,255,.2)", borderTopColor: "#3b82f6", animation: "mesBootSpin .9s linear infinite" }} />
      </div>
    }>
    <Routes>
      {/* Public */}
      <Route path="/login" element={<Login />} />

      {/* Root → smart redirect */}
      <Route path="/" element={<RootRedirect />} />

      {/* All roles — content varies by user (see DashboardForUser) */}
      <Route path="/dashboard" element={
        <Protected requiredAccess="dashboard"><DashboardForUser /></Protected>
      } />

      {/* Fullscreen + Submachine views — also require auth.
          Earlier these were unprotected so a freshly-opened browser
          tab could hit the URL and bombard the backend with 401s.
          Now they redirect to /login if no session token exists. */}
      <Route path="/fullscreen/:lineId" element={
        <Protected bare><Fullscreen /></Protected>
      } />
      {/* 2026-07-16 — model-aware fullscreen (UI-only, YWD-SS): plan/estimation
          follow the operator-selected model (INR vs OTR different CT). */}
      <Route path="/ywd-fullscreen/:lineId" element={
        <Protected bare><FullscreenYWD /></Protected>
      } />
      <Route path="/submachine-fullscreen/:subId" element={
        <Protected bare><SubmachineFullscreen /></Protected>
      } />

      {/* 65" shop-floor wall TV — multi-machine CT (left dashboard).
          RIGHT (summary) was folded into Fullscreen.jsx as the CT
          Distribution toggle, so the standalone page was deleted. */}
      <Route path="/wallboard/left/:lineId" element={
        <Protected bare><WallboardLeft /></Protected>
      } />

      {/* 2026-08-11 — SHORT, READABLE URLs for the two shop-floor screens:
              /Y17-SS/SUPERVISOR   and   /Y17-SS/MANAGEMENT
          The line name comes straight from the URL, so the address bar says
          which line AND which screen — the long /wallboard/left/<id> form was
          unreadable on a TV or in a shared link.

          Safe next to the fixed routes above and below: the second segment is
          a LITERAL, so "/admin/production" can never match "/:lineId/SUPERVISOR"
          — React Router ranks a static segment above a dynamic one.  The old
          paths are deliberately kept so every existing wall display, bookmark
          and numeric link keeps working untouched. */}
      <Route path="/:lineId/SUPERVISOR" element={
        <Protected bare><WallboardLeft /></Protected>
      } />
      <Route path="/:lineId/MANAGEMENT" element={
        <Protected bare><Fullscreen /></Protected>
      } />

      {/* Production + Admin */}
      <Route path="/historical" element={
        <Protected requiredAccess="historical"><Historical /></Protected>
      } />
      {/* 2026-09-07 — PEFF hourly-production check-sheet, hosted in an iframe. */}
      <Route path="/peff-sheet" element={
        <Protected requiredAccess="peff-sheet"><PeffSheet /></Protected>
      } />
      {/* 2026-08-11 — admin-only wait-time telemetry (page + video open times) */}
      <Route path="/waiting-time" element={
        <Protected requiredAccess="waiting-time"><WaitingTime /></Protected>
      } />
      <Route path="/audit" element={
        <Protected requiredAccess="audit"><Audit /></Protected>
      } />

      {/* 2026-08-24 — /import page removed; export moved to Historical → Hourly Report. */}
      <Route path="/settings" element={
      <Protected requiredAccess="settings"><Settings /></Protected>
      } />

      {/* Per-department config panels.  Each is its own page; the slide
          nav routes directly here.  Access is gated by canAccess() —
          admin/plant_head get full write; production user / dept users
          get read-only (handled inside the panel via the readOnly prop
          which is derived from !isAdmin in each wrapper). */}
      <Route path="/admin/production" element={
        <Protected requiredAccess="admin-production"><ProductionAdminPanel /></Protected>
      } />
      <Route path="/admin/maintenance" element={
        <Protected requiredAccess="admin-maintenance"><MaintenanceAdminPanel /></Protected>
      } />
      <Route path="/admin/quality" element={
        <Protected requiredAccess="admin-quality"><QualityAdminPanel /></Protected>
      } />
      {/* /admin = Admin Core (System Map / Departments / Users) — strictly
          admin-only.  Department or production users hitting this URL
          get bounced to their own dashboard. */}
      <Route path="/admin" element={
        <Protected requiredAccess="admin"><AdminPanel /></Protected>
      } />
      <Route path="/admin/*" element={
        <Protected requiredAccess="admin"><AdminPanel /></Protected>
      } />

      {/* Network Panel — dedicated switch-network monitor (admin-only). */}
      <Route path="/network" element={
        <Protected requiredAccess="admin"><NetworkPanel /></Protected>
      } />
        <Route path="/logs" element={<Protected requiredAccess="logs"><LogViewer /></Protected>} />
        <Route path="/video-coverage" element={<Protected requiredAccess="video-coverage"><VideoCoverage /></Protected>} />
        <Route path="/py-bypass" element={<Protected requiredAccess="py-bypass"><PyBypass /></Protected>} />

      {/* Department user — landing page for their assigned dept.
          Admin can also reach this URL — DepartmentPanel renders the
          Maintenance read-only Poka Yoke view for them. */}
      <Route path="/department-panel" element={
        <Protected requiredAccess="department-panel"><DepartmentPanel /></Protected>
      } />

      {/* Admin direct entry to the Maintenance Dashboard (the /dashboard
          route is role-aware and shows the Production Dashboard for admin —
          this gives them a separate slide-nav entry to view ANDON / history
          / stats for the Maintenance team). */}
      <Route path="/maintenance-dashboard" element={
        <Protected requiredAccess="maintenance-dashboard"><MaintenanceDashboard /></Protected>
      } />

      {/* Maintenance Historical Data — full slip archive + zone/line/
          machine roll-up of MTTR / MTBF / LTTR.  Reachable by Maintenance
          dept users and admin. */}
      <Route path="/maintenance-historical" element={
        <Protected requiredAccess="maintenance-historical"><MaintenanceHistorical /></Protected>
      } />

      {/* Maintenance Daily Log Book — fillable + printable 1:1 replica of
          the paper form (TBDI / MAINT. / F / 008).  Saved per date+shift. */}
      <Route path="/maintenance-logbook" element={
        <Protected requiredAccess="maintenance-logbook"><LogBook /></Protected>
      } />

      {/* Preventive Maintenance check sheets — zone → machine → fill sheet,
          dashboard, admin point editing.  DB-backed (/api/pm/*). */}
      <Route path="/maintenance-pm" element={
        <Protected requiredAccess="maintenance-pm"><PMPanel /></Protected>
      } />

      {/* Maintenance CAPA — auto-detected threshold breaches + 8D-style
          Corrective / Preventive Action filings. */}
      <Route path="/maintenance-capa" element={
        <Protected requiredAccess="maintenance-capa"><MaintenanceCAPA /></Protected>
      } />

      {/* Maintenance Deviations — standalone page for the Maintenance dept
          to RAISE deviation requests + track their status (PENDING_QA →
          APPROVED / REJECTED / EXTENDED / CLOSED).  The submitted form
          lands on Quality's queue (/quality-deviations) where QA Head
          approves, rejects, or extends it.  The Quality Dashboard
          (/quality-dashboard) also fires a toast on new requests. */}
      <Route path="/maintenance-deviations" element={
        <Protected requiredAccess="maintenance-deviations"><MaintenanceDeviations /></Protected>
      } />

      {/* Process Graphs — per-machine bar charts (actual vs target)
          for each configured process.  Reachable by everyone who can
          see the dashboard. */}
      <Route path="/process-graphs" element={
        <Protected requiredAccess="process-graphs"><ProcessGraphs /></Protected>
      } />

      {/* Shift Allocation — Section Incharge has predefined per-process
          skill requirements + slots.  Operators punch via the badge widget
          on the Dashboard.  Within the per-line deadline window, the Shift
          Supervisor opens this page and allocates the punched-in pool to
          the process slots.  Skill mismatches fire instant emails to
          Quality + Section Incharge.  Unallocated slots after the deadline
          fire a popup banner on Quality + Section Incharge dashboards. */}
      <Route path="/shift-allocation" element={
        <Protected requiredAccess="shift-allocation"><ShiftAllocation /></Protected>
      } />
      <Route path="/andon-history" element={
        <Protected requiredAccess="andon-history"><AndonHistory /></Protected>
      } />
      <Route path="/prod-breakdown-slip" element={
        <Protected requiredAccess="prod-breakdown-slip"><ProdBreakdownSlip /></Protected>
      } />
      <Route path="/shift-compile" element={
        <Protected requiredAccess="shift-compile"><ShiftCompile /></Protected>
      } />
      <Route path="/operators" element={
        <Protected requiredAccess="operators"><OperatorMaster /></Protected>
      } />
      <Route path="/my-team" element={
        <Protected requiredAccess="my-team"><MyTeam /></Protected>
      } />
      <Route path="/my-escalations" element={
        <Protected requiredAccess="my-escalations"><MyEscalations /></Protected>
      } />
      <Route path="/escalation-admin" element={
        <Protected requiredAccess="escalation-admin"><EscalationAdmin /></Protected>
      } />
      <Route path="/device-registry" element={
        <Protected requiredAccess="device-registry"><DeviceRegistry /></Protected>
      } />

      {/* Store + Dispatch — material master, GRN/issue, FG lots, truck loads */}
      <Route path="/store" element={
        <Protected requiredAccess="store"><StorePage /></Protected>
      } />
      <Route path="/dispatch" element={
        <Protected requiredAccess="dispatch"><DispatchPage /></Protected>
      } />

      {/* Shift Calculator — production planning tool */}
      <Route path="/shift-calculator" element={
        <Protected requiredAccess="shift-calculator"><ShiftCalculator /></Protected>
      } />

      {/* 2026-05-14 — Kanban Dispatch + Heijunka hidden for all users;
          pages still WIP and operator will complete them later.  Both
          SlideNav entries are commented out in components/SlideNav.jsx.
          Routes here are stubbed to /dashboard so any stale URL (admin
          bookmark, deep link) lands somewhere useful instead of a
          blank page or a half-built screen. */}
      <Route path="/kanban" element={<Navigate to="/dashboard" replace />} />

      {/* TPS Anything-Wrong consolidated alert board */}
      <Route path="/anything-wrong" element={
        <Protected requiredAccess="anything-wrong"><AnythingWrongPage /></Protected>
      } />

      <Route path="/heijunka" element={<Navigate to="/dashboard" replace />} />

      {/* TPS 5S daily audit */}
      <Route path="/5s" element={
        <Protected requiredAccess="five-s"><FiveSPage /></Protected>
      } />

      {/* TPS PDCA/A3 problem-solving tracker */}
      <Route path="/pdca" element={
        <Protected requiredAccess="pdca"><PDCAPage /></Protected>
      } />

      {/* 6 Sigma — Ball Guide (Seat Slider) dual-camera clip review + 40-day retention */}
      <Route path="/six-sigma" element={
        <Protected><SixSigmaPage /></Protected>
      } />

      {/* Bin Filling — embeds the standalone BinVision app via the /api/binfilling proxy */}
      <Route path="/bin-filling" element={
        <Protected><BinFillingPage /></Protected>
      } />

      {/* Maintenance Poka Yoke — full technical drill-down (Zone →
          Line → Model → PY table with bit / machine / expected).
          Maintenance team owns the actual fix so they get every
          detail; Quality side stays simple (counts + bypass list). */}
      <Route path="/maintenance-poka-yoke" element={
        <Protected requiredAccess="maintenance-poka-yoke"><MaintenancePokaYoke /></Protected>
      } />

      {/* Quality Dashboard — zone-tile health view.  Each zone tile rolls
          up its lines' PY pass/fail; click to expand and see lines with
          hover tooltips of every PY's bypass status.  Toast pops up on
          a fresh deviation request. */}
      <Route path="/quality-dashboard" element={
        <Protected requiredAccess="quality-dashboard"><QualityDashboard /></Protected>
      } />

      {/* Quality Deviation — Maintenance-raised deviation approvals +
          4M Change Notes.  Reachable from the dashboard's pending
          banner / deviation toast as well as the slide-nav. */}
      <Route path="/quality-deviations" element={
        <Protected requiredAccess="quality-deviations"><QualityDeviations /></Protected>
      } />

      {/* 2026-08-19 — Semi-Auto ↔ Final Inspection quality trace (SEAT SLIDER):
          continuous OK/NG history for both stations, filterable + Excel export.
          Shares the quality-dashboard permission so the QA roles that already
          see quality data get it without a new access key to hand out. */}
      <Route path="/sa-fi-history" element={
        <Protected requiredAccess="quality-dashboard"><SaFiQualityHistory /></Protected>
      } />

      {/* 2026-06-18 — Weld Monitor (Quality): live robot weld current/voltage. */}
      <Route path="/weld-monitor" element={
        <Protected requiredAccess="quality-dashboard"><WeldMonitor /></Protected>
      } />

      {/* 2026-05-27 — Comments History (combined cycle comments +
          NG process remarks).
          2026-08-08 — now gated on the "comments-history" page key, the same
          key SlideNav already filters the nav item on.  Without this the URL
          stayed open to every logged-in user, so setting "No Access" in Page
          Permissions had no effect — the toggle would have lied. */}
      <Route path="/comments-history" element={
        <Protected requiredAccess="comments-history"><CommentsHistory /></Protected>
      } />

      {/* Catch-all */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </Suspense>
    </>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  );
}
