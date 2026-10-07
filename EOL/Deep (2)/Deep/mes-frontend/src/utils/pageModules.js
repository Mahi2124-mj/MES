// ───────────────────────────────────────────────────────────────────────
// pageModules.js   (2026-09-13)
// ───────────────────────────────────────────────────────────────────────
// Sub-module (tab/section) registry for per-user MODULE-level access.
// A page listed here has named modules an admin can assign/hide individually
// in Admin → Users → Access, e.g. give a user only the "Hourly Report" tab of
// Historical and hide the other four.
//
// Stored in the SAME table as page permissions (mes_user_page_permissions),
// under a compound key `${page}::${module}` (page_key is free TEXT server-side,
// so no schema change is needed).  Semantics mirror the page-level allowlist:
//   • no module of a page granted  → ALL modules of that page show (default).
//   • any module granted           → ONLY granted modules show (rest hidden).
//   • explicit 'none' on a module  → that module hidden.
// See canAccessModule() in AuthContext.jsx.
// ───────────────────────────────────────────────────────────────────────
export const PAGE_MODULES = {
  historical: [
    { key: "shift", label: "Hourly Report" },
    { key: "losses", label: "Losses" },
    { key: "video", label: "Video Archive" },
    { key: "trace", label: "Part Traceability" },
    { key: "bdlog", label: "Breakdown History" },
    { key: "ctstudy", label: "Cycle Time Study" },
  ],
  // 2026-09-14 — Comments History now has two independently-assignable modules:
  // the comment/remark table and the new Pareto analysis.
  "comments-history": [
    { key: "comments", label: "Comments Table" },
    { key: "pareto",   label: "Pareto Analysis" },
  ],
  // 2026-09-19 — Shift Compile split into two modules: the per-line shift
  // review (status board, a line's detail, slide-to-close) and the compiled
  // roll-up (multi-line totals, per-model, historical + Excel/PDF).
  // 2026-09-27 — PY Bypass page: each section can be handed out on its own,
  // e.g. quality sees Open + History while only maintenance gets the two
  // settings blocks.  The PY assignment itself is NOT touched by any of these.
  // 2026-09-27 — Fault History: the event table and the Pareto can be handed
  // out separately (a line leader may only need the table).
  "fault-history": [
    { key: "table",  label: "Fault Table" },
    { key: "pareto", label: "Pareto Analysis" },
  ],
  "py-bypass": [
    { key: "open",    label: "Open Bypasses" },
    { key: "recent",  label: "Closed (last 24 h)" },
    { key: "history", label: "History" },
    { key: "mail",    label: "Approval Mail Setup" },
    { key: "bit",     label: "Reject Bit Setup" },
  ],
  "shift-compile": [
    { key: "line",    label: "Line Report" },
    { key: "compile", label: "Compile Report" },
  ],
};

// Compound key used in the permissions map / DB.
export const moduleKey = (page, mod) => `${page}::${mod}`;

// Convenience: does a page have assignable modules?
export const pageHasModules = (page) => Array.isArray(PAGE_MODULES[page]) && PAGE_MODULES[page].length > 0;
