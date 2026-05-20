import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import Layout from "./components/Layout";

// Pages
import Login      from "./pages/Login";
import Dashboard  from "./pages/Dashboard";
import MaintenanceDashboard from "./pages/MaintenanceDashboard";
import MaintenanceHistorical from "./pages/MaintenanceHistorical";
import MaintenanceCAPA from "./pages/MaintenanceCAPA";
import MaintenanceDeviations from "./pages/MaintenanceDeviations";
import MaintenancePokaYoke from "./pages/MaintenancePokaYoke";
import ProcessGraphs from "./pages/ProcessGraphs";
import QualityDashboard from "./pages/QualityDashboard";
import QualityDeviations from "./pages/QualityDeviations";
import Historical from "./pages/Historical";
import ImportExcel from "./pages/ImportExcel";
import Audit      from "./pages/Audit";
import Settings   from "./pages/Settings";
import AdminPanel, {
  ProductionAdminPanel,
  MaintenanceAdminPanel,
  QualityAdminPanel,
} from "./pages/AdminPanel";
import DepartmentPanel from "./pages/DepartmentPanel";
import Fullscreen from "./pages/Fullscreen";
import SubmachineFullscreen from "./pages/SubmachineFullscreen";
import WallboardLeft  from "./pages/WallboardLeft";
import ShiftAllocation from "./pages/ShiftAllocation";
import StorePage from "./pages/StorePage";
import DispatchPage from "./pages/DispatchPage";
import ShiftCalculator from "./pages/ShiftCalculator";
import KanbanPage from "./pages/KanbanPage";
import AnythingWrongPage from "./pages/AnythingWrongPage";
import HeijunkaPage from "./pages/HeijunkaPage";
import FiveSPage from "./pages/FiveSPage";
import PDCAPage from "./pages/PDCAPage";

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
function Protected({ children, requiredAccess, bare = false }) {
  const { token, loading, canAccess } = useAuth();
  const location = useLocation();

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
    return <Navigate to="/dashboard" replace />;
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
  if (loading) return null;
  if (!token) return <Navigate to="/login" replace />;
  return <Navigate to="/dashboard" replace />;
}

// ─── App ────────────────────────────────────────────────────────────────────
function AppRoutes() {
  return (
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
      <Route path="/submachine-fullscreen/:subId" element={
        <Protected bare><SubmachineFullscreen /></Protected>
      } />

      {/* 65" shop-floor wall TV — multi-machine CT (left dashboard).
          RIGHT (summary) was folded into Fullscreen.jsx as the CT
          Distribution toggle, so the standalone page was deleted. */}
      <Route path="/wallboard/left/:lineId" element={
        <Protected bare><WallboardLeft /></Protected>
      } />

      {/* Production + Admin */}
      <Route path="/historical" element={
        <Protected requiredAccess="historical"><Historical /></Protected>
      } />
      <Route path="/audit" element={
        <Protected requiredAccess="audit"><Audit /></Protected>
      } />

      <Route path="/import" element={
      <Protected requiredAccess="import"><ImportExcel /></Protected>
      } />
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

      {/* Catch-all */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
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
