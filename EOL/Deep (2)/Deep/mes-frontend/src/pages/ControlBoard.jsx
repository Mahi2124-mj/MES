// pages/ControlBoard.jsx — Control Board (2026-11-04)
//
// The line-side control board documents as one page: a button per sheet
// (front side + back side of the board) with Date / Zone / Line filters.
// UI demo only (operator: "pehle ye ui me sari sheets k demo box bna de") —
// each sheet opens a placeholder sheet area; the real formats and data come
// later.  Nothing is saved; the only API read is the line list for the filter.
// 2026-11-04 — the operator's HTML formats (EOL/Control_Board_Docs, copied to
// public/control-board-docs/) open in a sandboxed frame for the sheets that
// have one; the rest keep the placeholder.  Entries in a format are not saved.
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import PageTopbar from "../components/PageTopbar";
import { snapshotPdf, saveFile, appDownload, inWebViewApp, safeName } from "../utils/cbPdf";
import { RejectionListReport } from "../components/RejectionSummary";

const FRONT = [
  { key: "layout-yamazumi",   icon: "📐", label: "Layout / Yamazumi Chart" },
  { key: "4m-change",         icon: "🔁", label: "4M Change Control Sheet" },
  { key: "operator-visual",   icon: "👁",  label: "Operator Visualization Sheet" },
  { key: "track-sheet",       icon: "🛤",  label: "Track Record Sheet" },
  { key: "annual-safety",     icon: "🦺", label: "Annual Safety Chart" },
  { key: "defect-recording",  icon: "❗", label: "Defect Recording Sheet" },
  { key: "dwm-jkk-leader",    icon: "📝", label: "DWM JKK Leader" },
  { key: "manpower-alloc",    icon: "👥", label: "Allocation of Manpower" },
  { key: "skill-matrix",      icon: "🎓", label: "Skill Matrix" },
  { key: "q-chart",           icon: "📈", label: "Q Chart" },
  { key: "abnormal-handling", icon: "⚠",  label: "Abnormal Handling Rule" },
];
const BACK = [
  { key: "5s-daily",          icon: "✨", label: "5S Daily Check Sheet" },
  { key: "std-work-monitor",  icon: "⏱",  label: "Standard Work Monitoring Check Sheet" },
  { key: "oits",              icon: "📋", label: "OITS" },
];
export const ALL = [...FRONT.map(s => ({ ...s, side: "Front" })), ...BACK.map(s => ({ ...s, side: "Back" }))];
// 2026-11-12 — pages that open from the Back side but are not board sheets
// (not numbered, not in ALL, so Control Board View is unchanged).  Line
// Rejection moved here off the main menu (operator); shown to the logins that
// hold the "line-rejection" page access.
const LineRejection = lazy(() => import("./LineRejection"));
// 2026-11-13 — a 4M row filled from a breakdown slip opens that slip
const BreakdownSlipPaper = lazy(() => import("../components/BreakdownSlipPaper"));
const BACK_PAGES = [
  { key: "line-rejection", icon: "🟥", label: "Line Rejection", tag: "RED BIN PORTAL", access: "line-rejection" },
];

const isSeatSlider = (z) => /seat\s*slider/i.test(String(z || ""));
const isRecliner = (z) => /^recliner$/i.test(String(z || "").trim());

// Sheets with a real format: one or more HTML files under /control-board-docs/.
const DOC_BASE = "/control-board-docs/";
// width = the sheet's own page width (body padding + sheet), for "Fit width".
export const DOCS = {
  // data: the sheet gets line / month dropdowns and its cycle time + target
  // from Admin → Standard Data → Track Record (posted in, see SheetDoc).
  // 2026-11-12 — `cells` / `cellScope`: what the user types or clicks is auto-saved
  // (/api/control-board/cells) for the line + period: "sheet" = the sheet's own
  // line / month picks; "year" / "fy" / "month" = the page's Line filter + Date.
  "track-sheet":      [{ label: "Track Record Sheet", file: "track-record-sheet.html", width: 1710,
                         data: "track-record", cells: "track-record", cellScope: "sheet" }],
  // 2026-11-06 — line / month dropdowns in every sheet that has those boxes
  // ("line-month": the line list only); Operator Status also gets its rows and
  // day marks from Shift Allocation (until 10-Nov; now filled by the user, see `ops`).
  // Operator Status = TWO sheets a month, one per shift crew, one under the
  // other (`stack`), each with its own crew.  No shift on them: a crew's shift
  // changes every week (operator) — Sheet 1 / Sheet 2.
  // 2026-11-10 — `m4`: saved (circles + detail rows, /api/control-board/4m); see SheetDoc
  "4m-change":        [{ label: "4M Change Control Sheet", file: "4m-change-control-sheet.html", width: 1690,
                         data: "line-month", m4: true }],
  // 2026-11-10 — `ops`: filled by the user and SAVED (/api/control-board/ops),
  // no auto-fill from Shift Allocation; same shift-time limits as the 4M sheet
  "operator-visual":  [{ label: "Sheet 1", file: "operator-status.html", width: 1790, stack: true,
                         data: "line-month", ops: 1 },
                       { label: "Sheet 2", file: "operator-status.html", width: 1790, stack: true,
                         data: "line-month", ops: 2 }],
  // 2026-11-11 — `rej`: filled from the Red Bin portal rejections of the line's
  // month (/api/line-rejection/sheet); a filled quantity opens their summary
  "defect-recording": [{ label: "Standard", file: "defect-recording-sheet.html", width: 1725, data: "line-month", rej: true,
                         cells: "defect-std", cellScope: "sheet" },
                       { label: "Recliner", file: "defect-recording-sheet-recliner.html", width: 1725, zone: "recliner",
                         data: "line-month", rej: true, cells: "defect-rec", cellScope: "sheet" }],
  "annual-safety":    [{ label: "Annual Safety Chart", file: "annual-safety-chart.html", width: 1445,
                         cells: "annual-safety", cellScope: "year" }],
  // 2026-11-12 — the operator's formats for Sheet 10 and Sheet 12 (entries not saved)
  "q-chart":          [{ label: "Q Chart", file: "q-chart.html", width: 1140, cells: "q-chart", cellScope: "fy" }],
  "5s-daily":         [{ label: "5S Daily Check Sheet", file: "5s-daily-check-sheet.html", width: 1660,
                         cells: "5s-daily", cellScope: "month" }],
  // Line layouts — the operator's Excel drawings ("SS Line Layout(1).xlsx",
  // "Rec line layout.xlsx") converted to SVG by
  // Control_Board_Docs/_convert/xlsx_layout_to_html.py.  Fluid: each page scales
  // itself to the frame (no Fit width).  `match` = which zone / line it is for.
  "layout-yamazumi":  [
    { label: "Without Advance Bending", file: "seat-slider-line-layout.html", fluid: true, view: true,
      match: (c) => c.all || (isSeatSlider(c.zoneName) && !c.isYRASS) },
    { label: "With Advance Bending", file: "seat-slider-line-layout-advance-bending.html", fluid: true, view: true,
      match: (c) => c.all || (isSeatSlider(c.zoneName) && !c.isYRASS) },
    { label: "YRA-SS", file: "yra-ss-line-layout.html", fluid: true, view: true,
      match: (c) => c.all || (isSeatSlider(c.zoneName) && (!c.hasLine || c.isYRASS)) },
    { label: "Recliner", file: "recliner-line-layout.html", fluid: true, view: true,
      match: (c) => c.all || isRecliner(c.zoneName) },
  ],
};
// Formats added only for some zones: variants carry `match(ctx)`; when none
// matches the selected zone / line the placeholder shows this note instead.
// With no zone / line picked every variant shows (4-Nov: requiring a pick first
// left the operator looking at the placeholder, "ye nhi aa rha").
const DOC_NOTES = {
  "layout-yamazumi": "Layout added for the Seat Slider and Recliner lines. Not added for this line yet.",
};

const todayStr = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const fmtDate = (s) => {
  const [y, m, d] = String(s || "").split("-");
  return y && m && d ? `${d}-${m}-${y}` : "—";
};

const lbl = { display: "block", fontSize: 11, fontWeight: 700, color: "#64748b", textTransform: "uppercase",
              letterSpacing: ".06em", marginBottom: 5 };
const inp = { background: "#fff", color: "#0f172a", border: "1px solid #cbd5e1", borderRadius: 8,
              padding: "8px 12px", fontSize: 14, minWidth: 150, fontFamily: "inherit" };

function SheetButton({ sheet, n, active, onClick }) {
  return (
    <button onClick={onClick} style={{
      display: "flex", alignItems: "center", gap: 12, textAlign: "left", width: "100%",
      padding: "12px 14px", borderRadius: 12, cursor: "pointer", fontFamily: "inherit",
      border: `1.5px solid ${active ? "#2563eb" : "#e2e8f0"}`,
      background: active ? "#eff6ff" : "#fff",
      boxShadow: active ? "0 0 0 3px rgba(37,99,235,.12)" : "0 1px 2px rgba(0,0,0,.04)",
      transition: "border-color .12s, background .12s",
    }}>
      <span style={{ width: 38, height: 38, borderRadius: 10, flexShrink: 0, display: "flex",
                     alignItems: "center", justifyContent: "center", fontSize: 19,
                     background: active ? "#dbeafe" : "#f1f5f9" }}>{sheet.icon}</span>
      <span style={{ minWidth: 0 }}>
        <span style={{ display: "block", fontSize: 10, fontWeight: 700, color: "#94a3b8", letterSpacing: ".06em" }}>
          {sheet.tag || `SHEET ${String(n).padStart(2, "0")}`}
        </span>
        <span style={{ display: "block", fontSize: 14, fontWeight: 700, color: active ? "#1e40af" : "#0f172a",
                       lineHeight: 1.25 }}>{sheet.label}</span>
      </span>
      {DOCS[sheet.key] && (
        <span title="Sheet format added" style={{ marginLeft: "auto", flexShrink: 0, fontSize: 10, fontWeight: 700,
                       color: "#15803d", background: "#f0fdf4", border: "1px solid #bbf7d0",
                       borderRadius: 999, padding: "2px 8px" }}>Format</span>
      )}
    </button>
  );
}

// A sheet that has a real format: the operator's HTML in a sandboxed frame
// (scripts + print/confirm dialogs only — no access to the MES page, its
// login or storage).  Variants (e.g. Defect Recording: Standard / Recliner)
// switch with tabs; the Recliner one is picked first when the Recliner zone
// is selected.
// 2026-11-06 — Save PDF (A3, saved straight to the device).  The sheet frame is
// sandboxed: it may not download, and html2canvas cannot run in it.  So the
// sheet (cb-pdf.js) posts a snapshot — its HTML with the typed values and the
// graph as an image, no scripts — and this page draws it in a hidden frame
// that cannot run scripts (sandbox allow-same-origin only), turns it into one
// A3 page (control-board-docs/cb-pdf-render.js: html2canvas + jsPDF) and saves
// it.  The Android app's WebView cannot save a file the page makes, but it
// hands server downloads to the phone's browser (MainActivity DownloadListener,
// as for "Update now"): there the PDF is uploaded to /api/control-board/pdf and
// its 10-minute link opened, and the browser saves it to Downloads.
// 2026-11-12 — the helpers moved to utils/cbPdf.js (also used by the Line
// Rejection report and Historical Data).

// 2026-11-12 — `history`: a SAVED copy (Historical Data, /api/cb-history):
// { m4, ops: {"1","2"}, rej, sign, savedAt } replace the live fetches, saves
// are refused (read-only) and the header says when it was saved.  `noClose`
// hides the ✕ (a Historical tab has nothing to close to).
export function SheetDoc({ sheet, n, variants, zoneName, fileTag, token, docData, onDataSelect, onClose,
                           history = null, noClose = false, pageSel = null }) {
  // 2026-11-13 (operator): the admin login may edit every sheet on any day (server-checked)
  const { user: authUser } = useAuth();
  const cbAdmin = (authUser && authUser.role) === "admin";
  const pref = Math.max(0, variants.findIndex(v => v.zone && String(zoneName).toLowerCase().includes(v.zone)));
  const [vi, setVi] = useState(pref);
  const [full, setFull] = useState(false);
  const vKey = variants.map(x => x.file).join("|");
  useEffect(() => { setVi(pref); }, [sheet.key, pref, vKey]);
  useEffect(() => {
    if (!full) return undefined;
    const onKey = (e) => { if (e.key === "Escape") setFull(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [full]);
  const v = variants[Math.min(vi, variants.length - 1)];
  // Fit width: the sheets are 1450-1790 px wide; on a narrower screen the frame
  // is drawn at the sheet's own width and scaled down to the box (default on).
  const boxRef = useRef(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [fit, setFit] = useState(true);
  useEffect(() => {
    const el = boxRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const upd = () => setBox({ w: el.clientWidth, h: el.clientHeight });
    upd();
    const ro = new ResizeObserver(upd);
    ro.observe(el);
    return () => ro.disconnect();
  }, [full]);
  const natW = v.fluid ? 0 : (v.width || 1700);
  const narrow = natW > 0 && box.w > 0 && box.w < natW;
  const scale = fit && narrow ? box.w / natW : 1;
  // Save PDF (see saveFile above): state shown on the header button
  const [pdf, setPdf] = useState({ state: "", note: "" });
  const pdfBusy = useRef(false);
  const tokenRef = useRef(token);
  useEffect(() => { tokenRef.current = token; }, [token]);
  const noteTimer = useRef(null);
  const pdfNote = (state, note) => {
    setPdf({ state, note });
    clearTimeout(noteTimer.current);
    if (note) noteTimer.current = setTimeout(() => setPdf(p => (p.state === "busy" ? p : { state: "", note: "" })), 6000);
  };
  useEffect(() => () => clearTimeout(noteTimer.current), []);
  const pdfName = safeName([sheet.label, variants.length > 1 ? v.label : "", fileTag].filter(Boolean).join(" - "));
  const pdfNameRef = useRef(pdfName);
  useEffect(() => { pdfNameRef.current = pdfName; }, [pdfName]);
  const savePdf = () => {
    try { frameRef.current && frameRef.current.contentWindow.postMessage({ type: "cb-pdf-request" }, "*"); }
    catch { pdfNote("error", "PDF not saved"); }
  };
  const btn = { border: "1px solid #e2e8f0", background: "#fff", color: "#475569", borderRadius: 8,
                padding: "5px 10px", fontSize: 13, cursor: "pointer", fontFamily: "inherit", whiteSpace: "nowrap" };
  // 2026-11-05 — the sheet zooms ITSELF to the frame (postMessage "cb-zoom",
  // script added by Control_Board_Docs/_convert/install_sheets.py): a CSS
  // transform of the whole frame made Firefox drop 1 px table lines (Track
  // Record Sheet, columns 7-10 merged on the operator's laptop).  A sheet that
  // does not answer "cb-zoom-ok" within 1.5 s of loading (browser without CSS
  // zoom) falls back to the transform.
  const frameRef = useRef(null);
  const fallbackRef = useRef(null);
  const [inner, setInner] = useState(null);      // null = asked, true = sheet zooms, false = transform
  useEffect(() => { setInner(null); }, [v.file]);
  // the sheet reports its own line / month pick ("cb-select") so the page can
  // fetch that line's MES data for it
  const selectRef = useRef(null);
  selectRef.current = (msg) => { if (v.data && onDataSelect) onDataSelect(v.data, msg); };
  useEffect(() => {
    const onMsg = (e) => {
      const f = frameRef.current;
      if (!f || e.source !== f.contentWindow || !e.data) return;
      if (e.data.type === "cb-zoom-ok") {
        clearTimeout(fallbackRef.current);
        setInner(true);
      } else if (e.data.type === "cb-select") {
        selectRef.current && selectRef.current(e.data);
      } else if (e.data.type === "cb-rj-open" && Array.isArray(e.data.ids)) {
        setRjOpen({ ids: e.data.ids.map(String), title: String(e.data.title || "Rejections") });
      } else if (e.data.type === "cb-slip-open" && e.data.id) {
        setSlipView({ src: e.data.src === "toolroom" ? "AUTO-TOOLROOM" : "AUTO-MAINT",
                      id: Number(e.data.id), stage: String(e.data.stage || "") });
      } else if (e.data.type === "cb-4m-save" || e.data.type === "cb-ops-save") {
        save4mRef.current && save4mRef.current(e.data);
      } else if (e.data.type === "cb-cells-save") {
        saveCellsRef.current && saveCellsRef.current(e.data);
      } else if (e.data.type === "cb-cells-status") {
        setCellNote({ text: String(e.data.text || ""), state: String(e.data.state || ""), at: Date.now() });
      } else if (e.data.type === "cb-pdf-status") {
        if (e.data.state === "busy") pdfNote("busy", "");
        else if (e.data.state === "error") pdfNote("error", "PDF not saved: " + (e.data.error || "error"));
      } else if (e.data.type === "cb-pdf-snapshot" && e.data.html) {
        if (pdfBusy.current) return;
        const app = inWebViewApp();
        let name = (e.data.own && e.data.name) ? safeName(e.data.name) : `${pdfNameRef.current}.pdf`;
        if (!name.toLowerCase().endsWith(".pdf")) name += ".pdf";
        pdfBusy.current = true;
        pdfNote("busy", "");
        snapshotPdf(e.data.html, name.replace(/\.pdf$/i, ""))
          .then(buf => (app
            ? appDownload(buf, name, tokenRef.current).then(() => pdfNote("done", "PDF opened in the browser — saved to Downloads"))
            : (saveFile(buf, name), pdfNote("done", `Saved ${name}`))))
          .catch(err => pdfNote("error", "PDF not saved: " + ((err && err.message) || "error")))
          .finally(() => { pdfBusy.current = false; });
      }
    };
    window.addEventListener("message", onMsg);
    return () => { window.removeEventListener("message", onMsg); clearTimeout(fallbackRef.current); };
  }, []);
  const sendZoom = (z) => {
    if (v.fluid) return;
    try { frameRef.current && frameRef.current.contentWindow.postMessage({ type: "cb-zoom", zoom: z }, "*"); }
    catch { /* frame gone */ }
  };
  useEffect(() => { if (inner !== false) sendZoom(scale); }, [scale, inner]);   // eslint-disable-line
  // Standard data for sheets that use it (Track Record: lines, cycle time,
  // target, plus the page's line and month) — sent on load and on every change.
  const baseFor = v.data && docData ? docData[v.data] : null;
  // 2026-11-10 — 4M Change Control Sheet is SAVED.  This frame loads the
  // month's circles + detail rows for the sheet's line and month
  // (/api/control-board/4m), posts them in with the line list, and saves every
  // circle / cell the sheet reports ("cb-4m-save", answered with "cb-4m-ack").
  // Refreshed every minute: OK / NG and the supervisor arrive at shift end.
  // 2026-11-10 — the Operator Visualization Sheet (v.ops = sheet 1 / 2) works the same way
  const saved = !!(v.m4 || v.ops);
  const m4Line = saved && baseFor && !history ? baseFor.line_id : null;
  const m4Month = saved && baseFor ? baseFor.month : null;
  const m4Want = m4Line && m4Month ? `${m4Line}|${m4Month}` : "";
  const [m4, setM4] = useState({ key: "", data: null, loading: false, error: null });
  const [m4Tick, setM4Tick] = useState(0);
  useEffect(() => {
    if (!m4Want) { setM4({ key: "", data: null, loading: false, error: null }); return undefined; }
    let alive = true;
    setM4(s => (s.key === m4Want ? s : { key: m4Want, data: null, loading: true, error: null }));
    api.get(v.ops ? `/api/control-board/ops?line_id=${m4Line}&month=${m4Month}&sheet=${v.ops}`
                  : `/api/control-board/4m?line_id=${m4Line}&month=${m4Month}`, tokenRef.current)
      .then(r => { if (alive) setM4({ key: m4Want, data: r, loading: false, error: null }); })
      .catch(e => {
        if (!alive) return;
        const msg = /404|Not Found/i.test(String(e && e.message))
          ? "Saving starts after the next MES-API restart." : "Could not load the saved sheet.";
        setM4(s => ({ ...s, key: m4Want, loading: false, error: msg }));
      });
    return () => { alive = false; };
  }, [m4Want, m4Tick, v.ops]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!m4Want) return undefined;
    const t = setInterval(() => setM4Tick(x => x + 1), 60000);
    return () => clearInterval(t);
  }, [m4Want]);
  // 2026-11-11 — Defect Recording Sheet (v.rej): the Red Bin rejections of the
  // sheet's line and month, posted in as `rej`; refreshed every 2 minutes.  A
  // click on a filled cell ("cb-rj-open", record ids) opens their summary.
  const rjLine = v.rej && baseFor && !history ? baseFor.line_id : null;
  const rjMonth = v.rej && baseFor ? baseFor.month : null;
  const rjWant = rjLine && rjMonth ? `${rjLine}|${rjMonth}` : "";
  const [rj, setRj] = useState({ key: "", data: null, loading: false, error: null });
  const [rjTick, setRjTick] = useState(0);
  const [rjOpen, setRjOpen] = useState(null);
  const [slipView, setSlipView] = useState(null);
  useEffect(() => {
    if (!rjWant) { setRj({ key: "", data: null, loading: false, error: null }); return undefined; }
    let alive = true;
    setRj(s => (s.key === rjWant ? s : { key: rjWant, data: null, loading: true, error: null }));
    api.get(`/api/line-rejection/sheet?line_id=${rjLine}&month=${rjMonth}`, tokenRef.current)
      .then(r => { if (alive) setRj({ key: rjWant, data: r, loading: false, error: null }); })
      .catch(e => {
        if (!alive) return;
        const msg = /404|Not Found/i.test(String(e && e.message))
          ? "Red Bin rejections appear after the next MES-API restart." : "Could not load the Red Bin rejections.";
        setRj(s => ({ ...s, key: rjWant, loading: false, error: msg }));
      });
    return () => { alive = false; };
  }, [rjWant, rjTick]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!rjWant) return undefined;
    const t = setInterval(() => setRjTick(x => x + 1), 120000);
    return () => clearInterval(t);
  }, [rjWant]);
  const rjData = history ? (history.rej || null) : rj.key === rjWant ? rj.data : null;
  // 2026-11-12 — the line leader's signature per day / shift on the Defect
  // Recording Sheet: the Track Record Sheet's own data (leader in Shift
  // Allocation of each shift that ran, signature from Employee Master), so both
  // sheets carry the same signatures.  Only leader ids, names and images are kept.
  const [rjSign, setRjSign] = useState({ key: "", data: null });
  useEffect(() => {
    if (!rjWant) { setRjSign({ key: "", data: null }); return undefined; }
    let alive = true;
    api.get(`/api/standard-data/track-record/sheet?line_id=${rjLine}&month=${rjMonth}`, tokenRef.current)
      .then(r => {
        if (!alive) return;
        const shifts = {};
        Object.entries((r && r.shifts) || {}).forEach(([sh, days]) => {
          shifts[sh] = {};
          Object.entries(days || {}).forEach(([d, x]) => {
            if (x && (x.S1 || x.S1_name)) shifts[sh][d] = { S1: x.S1 || null, S1_name: x.S1_name || null };
          });
        });
        setRjSign({ key: rjWant, data: { line_id: r.line_id, month: r.month, shifts, leaders: r.leaders || {} } });
      })
      .catch(() => { if (alive) setRjSign({ key: rjWant, data: null }); });
    return () => { alive = false; };
  }, [rjWant, rjTick]);   // eslint-disable-line react-hooks/exhaustive-deps
  // 2026-11-12 — auto-save of typed / clicked entries (see DOCS `cells`)
  const cellDoc = v.cells || null;
  const fyOf = (d) => { const y = Number(String(d).slice(0, 4)), m = Number(String(d).slice(5, 7));
    if (!y || !m) return null; const s0 = m >= 4 ? y : y - 1; return `${s0}-${String((s0 + 1) % 100).padStart(2, "0")}`; };
  const cellLine = !cellDoc ? null : v.cellScope === "sheet" ? (baseFor ? baseFor.line_id : null) : (pageSel ? pageSel.line_id : null);
  const cellPeriod = !cellDoc ? null
    : v.cellScope === "sheet" ? (baseFor ? baseFor.month : null)
    : !pageSel || !pageSel.date ? null
    : v.cellScope === "year" ? String(pageSel.date).slice(0, 4)
    : v.cellScope === "fy" ? fyOf(pageSel.date)
    : String(pageSel.date).slice(0, 7);
  const cellWant = cellDoc && cellLine && cellPeriod ? `${cellDoc}|${cellLine}|${cellPeriod}` : "";
  // 2026-11-13: + `set` (production day each value was entered), `today` (the
  // line's production day) and `open` (period contains today) for the today-only lock
  const [cells, setCells] = useState({ key: "", data: {}, set: {}, today: "", open: true });
  const [cellNote, setCellNote] = useState({ text: "", state: "", at: 0 });
  useEffect(() => {
    if (!cellNote.text || cellNote.state === "busy") return undefined;
    const t = setTimeout(() => setCellNote(n => (n.at === cellNote.at ? { text: "", state: "", at: 0 } : n)), 4000);
    return () => clearTimeout(t);
  }, [cellNote]);
  const [cellTick, setCellTick] = useState(0);
  useEffect(() => {
    if (history || !cellWant) { setCells({ key: "", data: {}, set: {}, today: "", open: true }); return undefined; }
    let alive = true;
    api.get(`/api/control-board/cells?doc=${cellDoc}&line_id=${cellLine}&period=${cellPeriod}`, tokenRef.current)
      .then(r => { if (alive) setCells({ key: cellWant, data: (r && r.cells) || {}, set: (r && r.set) || {},
                                         today: (r && r.today) || "", open: !(r && r.open === false),
                                         admin: !!(r && r.admin) }); })
      .catch(e => {
        if (!alive) return;
        setCells({ key: cellWant, data: {}, set: {}, today: "", open: true });
        if (/404|Not Found/i.test(String(e && e.message))) setCellNote({ text: "Auto-save starts after the next MES-API restart", state: "error", at: Date.now() });
      });
    return () => { alive = false; };
  }, [cellWant, cellTick, history]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {                     // other users' entries: refresh every minute
    if (history || !cellWant) return undefined;
    const t = setInterval(() => setCellTick(x => x + 1), 60000);
    return () => clearInterval(t);
  }, [cellWant, history]);
  const histCells = history && cellDoc
    ? ((history.cellsByDoc || {})[cellDoc] || {}) : null;
  const cellData = history ? histCells : (cells.key === cellWant ? cells.data : null);
  const cellsKey = history ? `h|${cellDoc}|${JSON.stringify(histCells)}`
    : `${cellWant}|${cells.key === cellWant ? `${cells.today}|${cells.open}|${cells.admin}|${JSON.stringify(cells.data)}` : "-"}`;
  const sendCells = () => {
    if (!cellDoc || cellData === null) return;
    const mine = !history && !!cellWant && cells.key === cellWant;
    try {
      frameRef.current && frameRef.current.contentWindow.postMessage({ type: "cb-cells", cells: cellData,
        canSave: !history && !!cellWant, scope: history ? `h|${cellDoc}` : cellWant,
        // today-only lock: a saved copy and "no line picked" are fully locked
        today: mine ? cells.today : (history || !cellWant ? "1970-01-01" : ""),
        set: mine ? cells.set : {}, open: mine ? cells.open : false, admin: mine ? !!cells.admin : false,
        period: cellPeriod || "", pkind: v.cellScope === "year" ? "year" : v.cellScope === "fy" ? "fy" : "month" }, "*");
    } catch { /* frame gone */ }
  };
  const saveCellsRef = useRef(null);
  saveCellsRef.current = (m) => {
    const reply = (x) => {
      try { frameRef.current && frameRef.current.contentWindow.postMessage({ type: "cb-cells-ack", seq: m.seq, ...x }, "*"); }
      catch { /* frame gone */ }
    };
    if (history || !cellWant || String(m.scope || "") !== cellWant) { reply({ ok: false, error: "read-only" }); return; }
    api.put("/api/control-board/cells", { doc: cellDoc, line_id: Number(cellLine), period: cellPeriod,
                                          changes: m.changes || {}, dates: m.dates || {}, clear: !!m.clear }, tokenRef.current)
      .then(r => {
        reply({ ok: true, saved_at: r && r.saved_at });
        setCells(c => {
          if (c.key !== cellWant) return c;
          const set = { ...(c.set || {}) };
          Object.entries(m.changes || {}).forEach(([k, val]) => {
            if (String(val == null ? "" : val).trim()) { if (!set[k] && c.today) set[k] = c.today; } else delete set[k];
          });
          return { ...c, data: m.clear ? {} : { ...c.data, ...(m.changes || {}) }, set };
        });
        if (m.clear) setTimeout(() => sendData(), 0);       // MES values back in after a Clear
      })
      .catch(err => {
        const msg = String((err && err.message) || "error");
        reply({ ok: false, error: /404|Not Found/i.test(msg) ? "auto-save starts after the next MES-API restart" : msg,
                // 2026-11-13: the today-only rule refused it: the sheet puts the saved value back
                locked: /today|earlier days|cannot be changed|current week|another period|Clear is not allowed/i.test(msg) });
      });
  };
  const save4mRef = useRef(null);
  save4mRef.current = (m) => {
    const ackType = m.type === "cb-ops-save" ? "cb-ops-ack" : "cb-4m-ack";
    const reply = (x) => {
      try { frameRef.current && frameRef.current.contentWindow.postMessage({ type: ackType, seq: m.seq, ...x }, "*"); }
      catch { /* frame gone */ }
    };
    if (history) { reply({ ok: false, error: "Saved copy: read-only" }); return; }
    if (m.type === "cb-ops-save") {
      if (!v.ops) return;
      api.put("/api/control-board/ops/cell", { line_id: Number(m.line_id), month: m.month, sheet: v.ops,
                                               key: m.key, value: m.value == null ? "" : String(m.value) }, tokenRef.current)
        .then(r => { reply({ ok: true, saved_at: r && r.saved_at }); setM4Tick(x => x + 1); })
        .catch(err => reply({ ok: false, error: (err && err.message) || "error" }));
      return;
    }
    const kind = m.kind === "mark" ? "mark" : "row";
    const base = { line_id: Number(m.line_id), date: m.date, shift: m.shift, param: m.param };
    api.put(`/api/control-board/4m/${kind}`,
            kind === "mark" ? { ...base, state: m.state } : { ...base, slip_key: m.slip_key || "", fields: m.fields || {} },
            tokenRef.current)
      .then(r => { reply({ ok: true, saved_at: r && r.saved_at }); if (kind === "mark") setM4Tick(x => x + 1); })
      .catch(err => reply({ ok: false, error: (err && err.message) || "error" }));
  };
  const dataFor = baseFor && v.m4 && history
    ? { ...baseFor, m4: true, sheet4m: history.m4 || null, loading: false, error: null }
    : baseFor && v.ops && history
    ? { ...baseFor, ops: true, sheet_no: v.ops, sheetOps: (history.ops || {})[String(v.ops)] || null,
        loading: false, error: null }
    : baseFor && v.rej && history
    ? { ...baseFor, rej: rjData, rejLoading: false, rejError: null, sign: history.sign || null }
    : baseFor && v.m4
    ? { ...baseFor, m4: true, sheet4m: m4.key === m4Want ? m4.data : null,
        loading: m4.loading, error: m4.error }
    : baseFor && v.ops
    ? { ...baseFor, ops: true, sheet_no: v.ops, sheetOps: m4.key === m4Want ? m4.data : null,
        loading: m4.loading, error: m4.error }
    : baseFor && v.rej
    ? { ...baseFor, rej: rjData, rejLoading: rj.loading, rejError: rj.error,
        sign: rjSign.key === rjWant ? rjSign.data : null }
    : baseFor;
  const dataKey = dataFor ? JSON.stringify(dataFor) : "";
  const sendData = () => {
    if (!dataFor) return;
    try { frameRef.current && frameRef.current.contentWindow.postMessage({ type: "cb-data", ...dataFor }, "*"); }
    catch { /* frame gone */ }
  };
  useEffect(() => { sendData(); sendCells(); }, [dataKey]);   // eslint-disable-line
  useEffect(() => { sendCells(); }, [cellsKey]);              // eslint-disable-line
  const onFrameLoad = () => {
    sendData();
    sendCells();
    sendZoom(scale);
    clearTimeout(fallbackRef.current);
    if (!v.fluid) fallbackRef.current = setTimeout(() => setInner(x => (x === null ? false : x)), 1500);
  };
  const head = (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", flexWrap: "wrap",
                  borderBottom: "1px solid #e2e8f0", background: "#f8fafc" }}>
      <span style={{ fontSize: 20 }}>{sheet.icon}</span>
      <div style={{ minWidth: 0, flex: "1 1 200px" }}>
        <div style={{ fontSize: 16, fontWeight: 800, color: "#0f172a" }}>{sheet.label}</div>
        <div style={{ fontSize: 12, color: "#64748b" }}>
          {sheet.side} side · Sheet {String(n).padStart(2, "0")}{history
            ? ` · saved copy${history.savedAt ? `, saved ${history.savedAt}` : ""} · read-only`
            : v.view ? "" : (v.m4 || v.ops) ? ` · entries are saved${cbAdmin ? " · admin: every day can be edited" : " · only today's entries can be changed"}`
            : v.cells ? (cellWant ? ` · entries are saved automatically${v.rej ? " · Red Bin rejections filled in" : ""}`
                                    + (cbAdmin ? " · admin: every day can be edited" : " · only today's entries can be changed")
                                  : " · pick a line in the filter to enter data")
            : v.rej ? " · rejections from the Red Bin portal · click a quantity for its summary" : " · entries are not saved yet"}
        </div>
      </div>
      {variants.length > 1 && (
        <div style={{ display: "flex", border: "1px solid #cbd5e1", borderRadius: 8, overflow: "hidden" }}>
          {variants.map((x, i) => (
            <button key={x.file} onClick={() => setVi(i)} style={{
              border: "none", padding: "6px 12px", fontSize: 13, fontWeight: 700, cursor: "pointer",
              fontFamily: "inherit", background: i === vi ? "#1e40af" : "#fff",
              color: i === vi ? "#fff" : "#334155" }}>{x.label}</button>
          ))}
        </div>
      )}
      {narrow && (
        <button onClick={() => setFit(f => !f)} style={btn}>{fit ? "Actual size" : "Fit width"}</button>
      )}
      <button onClick={savePdf} disabled={pdf.state === "busy"} title="Save this sheet as an A3 PDF"
              style={{ ...btn, background: "#1e40af", borderColor: "#1e40af", color: "#fff", fontWeight: 700,
                       opacity: pdf.state === "busy" ? 0.6 : 1, cursor: pdf.state === "busy" ? "wait" : "pointer" }}>
        {pdf.state === "busy" ? "Saving PDF…" : "Save PDF"}
      </button>
      {pdf.note && <span style={{ fontSize: 12, fontWeight: 600, color: pdf.state === "error" ? "#b91c1c" : "#15803d" }}>
        {pdf.note}</span>}
      {!pdf.note && cellNote.text && <span style={{ fontSize: 12, fontWeight: 600,
        color: cellNote.state === "error" ? "#b91c1c" : cellNote.state === "busy" ? "#64748b" : "#15803d" }}>{cellNote.text}</span>}
      <button onClick={() => setFull(f => !f)} style={btn}>{full ? "Exit full screen" : "Full screen"}</button>
      {!noClose && <button onClick={() => { setFull(false); onClose(); }} title="Close" style={btn}>✕</button>}
    </div>
  );
  const useTransform = scale < 1 && inner === false;
  const frame = (
    <div ref={boxRef} style={{ position: "relative", overflow: "hidden", background: "#e9ecef",
                               flex: full ? 1 : undefined, minHeight: 0,
                               height: full ? undefined : "max(620px, calc(100vh - 180px))" }}>
      <iframe key={v.file} ref={frameRef} onLoad={onFrameLoad}
              title={`${sheet.label} — ${v.label}`} src={DOC_BASE + v.file}
              sandbox="allow-scripts allow-modals"
              style={useTransform
                ? { position: "absolute", left: 0, top: 0, border: "none", width: natW,
                    height: box.h / scale, transform: `scale(${scale})`, transformOrigin: "0 0" }
                : { position: "absolute", inset: 0, border: "none", width: "100%", height: "100%" }} />
    </div>
  );
  const rjModal = rjOpen && (
    <RejectionListReport title={rjOpen.title} token={token} z={10500}
                         records={(rjData?.records || []).filter(r => rjOpen.ids.includes(String(r.id)))}
                         context={rjData || {}} onClose={() => setRjOpen(null)} />
  );
  const slipModal = slipView && (
    <div style={{ position: "fixed", inset: 0, zIndex: 10500 }}>
      <Suspense fallback={null}>
        <BreakdownSlipPaper row={slipView} onClose={() => setSlipView(null)} />
      </Suspense>
    </div>
  );
  if (full) {
    return (
      <div style={{ position: "fixed", inset: 0, zIndex: 9500, background: "#fff", display: "flex",
                    flexDirection: "column",
                    // installed app: start below the status bar (0 on the website)
                    paddingTop: "calc(var(--cap-sat, 0px) * var(--mes-zinv, 1))" }}>
        {head}
        {frame}
        {rjModal}
        {slipModal}
      </div>
    );
  }
  return (
    <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, overflow: "hidden",
                  boxShadow: "0 1px 3px rgba(0,0,0,.05)" }}>
      {head}
      {frame}
      {rjModal}
      {slipModal}
    </div>
  );
}

function SheetDemo({ sheet, n, date, zoneName, lineName, note, onClose }) {
  const cols = ["No.", "Item", "Standard", "Actual", "Remarks", "Sign"];
  return (
    <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, overflow: "hidden",
                  boxShadow: "0 1px 3px rgba(0,0,0,.05)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px",
                    borderBottom: "1px solid #e2e8f0", background: "#f8fafc" }}>
        <span style={{ fontSize: 20 }}>{sheet.icon}</span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontSize: 16, fontWeight: 800, color: "#0f172a" }}>{sheet.label}</div>
          <div style={{ fontSize: 12, color: "#64748b" }}>
            {sheet.side} side · Sheet {String(n).padStart(2, "0")}
          </div>
        </div>
        <span style={{ fontSize: 11, fontWeight: 700, color: "#b45309", background: "#fffbeb",
                       border: "1px solid #fde68a", borderRadius: 999, padding: "3px 10px" }}>Demo</span>
        <button onClick={onClose} title="Close" style={{
          border: "1px solid #e2e8f0", background: "#fff", color: "#475569", borderRadius: 8,
          padding: "5px 10px", fontSize: 13, cursor: "pointer", fontFamily: "inherit" }}>✕</button>
      </div>

      {/* Placeholder sheet: letterhead, the filter values, and an empty grid */}
      <div style={{ padding: 16 }}>
        {note && (
          <div style={{ marginBottom: 12, padding: "9px 12px", borderRadius: 8, background: "#eff6ff",
                        border: "1px solid #bfdbfe", color: "#1e3a8a", fontSize: 13, fontWeight: 600 }}>{note}</div>
        )}
        <div style={{ border: "1.5px solid #0f172a", borderRadius: 4, overflow: "hidden" }}>
          <div style={{ display: "flex", alignItems: "center", borderBottom: "1.5px solid #0f172a" }}>
            <div style={{ width: 90, alignSelf: "stretch", display: "flex", alignItems: "center",
                          justifyContent: "center", borderRight: "1.5px solid #0f172a", padding: 6 }}>
              <img src="/logo.jpg" alt="" style={{ maxWidth: "100%", maxHeight: 40, objectFit: "contain" }}
                   onError={(e) => { e.currentTarget.style.display = "none"; }} />
            </div>
            <div style={{ flex: 1, textAlign: "center", padding: "8px 10px" }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#334155" }}>TOYOTA BOSHOKU DEVICE INDIA PVT. LTD.</div>
              <div style={{ fontSize: 17, fontWeight: 800, color: "#0f172a", textTransform: "uppercase" }}>{sheet.label}</div>
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                        borderBottom: "1.5px solid #0f172a", fontSize: 12 }}>
            {[["Date", fmtDate(date)], ["Zone", zoneName], ["Line", lineName]].map(([k, v], i) => (
              <div key={k} style={{ padding: "7px 10px", borderLeft: i ? "1px solid #0f172a" : "none" }}>
                <span style={{ color: "#64748b", fontWeight: 700 }}>{k}: </span>
                <span style={{ color: "#0f172a", fontWeight: 700 }}>{v}</span>
              </div>
            ))}
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, minWidth: 520 }}>
              <thead>
                <tr>
                  {cols.map((c, i) => (
                    <th key={c} style={{ padding: "7px 8px", background: "#f1f5f9", color: "#334155",
                                         textAlign: "left", fontWeight: 700, borderBottom: "1px solid #0f172a",
                                         borderLeft: i ? "1px solid #cbd5e1" : "none",
                                         width: i === 0 ? 46 : i === 1 ? "30%" : undefined }}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: 8 }, (_, r) => (
                  <tr key={r}>
                    {cols.map((c, i) => (
                      <td key={c} style={{ height: 30, padding: "0 8px", color: "#94a3b8",
                                           borderTop: "1px solid #e2e8f0",
                                           borderLeft: i ? "1px solid #e2e8f0" : "none" }}>
                        {i === 0 ? r + 1 : ""}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div style={{ marginTop: 10, fontSize: 12, color: "#64748b" }}>
          Demo layout. The actual sheet format will be added here.
        </div>
      </div>
    </div>
  );
}

export default function ControlBoard() {
  const { token, canAccess } = useAuth();
  const backPages = BACK_PAGES.filter(p => !p.access || typeof canAccess !== "function" || canAccess(p.access));
  const [lines, setLines]   = useState([]);
  const [date, setDate]     = useState(todayStr);
  const [zone, setZone]     = useState("__all");
  const [lineId, setLineId] = useState("");
  // ?sheet=<key> opens that sheet directly (e.g. /control-board?sheet=track-sheet)
  const [pick, setPick]     = useState(() => {
    try {
      const k = new URLSearchParams(window.location.search).get("sheet");
      return ALL.some(s => s.key === k) || BACK_PAGES.some(p => p.key === k) ? k : null;
    } catch { return null; }
  });
  const pickedPage = backPages.find(p => p.key === pick) || null;
  const demoRef = useRef(null);

  useEffect(() => { document.title = "Control Board"; }, []);
  // The sheet opens below the buttons — bring it into view.
  useEffect(() => {
    if (pick && demoRef.current) demoRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [pick]);
  useEffect(() => {
    let alive = true;
    api.get("/api/lines/", token)
      .then(ls => { if (alive) setLines(Array.isArray(ls) ? ls : []); })
      .catch(() => {});
    return () => { alive = false; };
  }, [token]);

  const zoneOf = (l) => l?.zone_name || "No zone";
  const zones = useMemo(() => [...new Set(lines.map(zoneOf))].sort((a, b) => a.localeCompare(b)), [lines]);
  const shownLines = useMemo(
    () => (zone === "__all" ? lines : lines.filter(l => zoneOf(l) === zone))
      .slice().sort((a, b) => String(a.line_name || "").localeCompare(String(b.line_name || ""))),
    [lines, zone]);
  const curLine = lines.find(l => String(l.id) === String(lineId));
  const pickZone = (z) => {
    setZone(z);
    if (curLine && z !== "__all" && zoneOf(curLine) !== z) setLineId("");
  };
  const zoneName = curLine ? zoneOf(curLine) : (zone === "__all" ? "All zones" : zone);
  const lineName = curLine ? (curLine.line_name || curLine.line_code || `Line ${curLine.id}`) : "All lines";
  const picked = ALL.find(s => s.key === pick) || null;
  const pickedNo = picked ? ALL.indexOf(picked) + 1 : 0;
  const [stdTrack, setStdTrack] = useState(null);
  useEffect(() => {
    if (pick !== "track-sheet") return undefined;
    let alive = true;
    api.get("/api/standard-data/track-record", token)
      .then(r => { if (alive) setStdTrack(Array.isArray(r) ? r : []); })
      .catch(() => { if (alive) setStdTrack([]); });
    return () => { alive = false; };
  }, [pick, token]);
  // Sheets with line / month dropdowns: line and month follow the filters until
  // picked inside a sheet (one pick for every sheet); the MES data of a sheet
  // that has some is fetched for that line and month and posted in.
  const [sheetSel, setSheetSel] = useState({ line_id: null, month: null });
  useEffect(() => {
    setSheetSel({ line_id: curLine ? curLine.id : null, month: String(date || "").slice(0, 7) });
  }, [curLine ? curLine.id : null, date]);   // eslint-disable-line react-hooks/exhaustive-deps
  const loadErr = (e) => (/404|Not Found/i.test(String(e && e.message))
    ? "MES data becomes available after the next MES-API restart." : "Could not load MES data.");
  // Track Record Sheet: man-hour, parts, losses, leader per day
  const [trackSheet, setTrackSheet] = useState({ data: null, loading: false, error: null });
  useEffect(() => {
    if (pick !== "track-sheet" || !sheetSel.line_id || !sheetSel.month) return undefined;
    let alive = true;
    setTrackSheet(s => ({ ...s, loading: true, error: null }));
    api.get(`/api/standard-data/track-record/sheet?line_id=${sheetSel.line_id}&month=${sheetSel.month}`, token)
      .then(r => { if (alive) setTrackSheet({ data: r, loading: false, error: null }); })
      .catch(e => { if (alive) setTrackSheet({ data: null, loading: false, error: loadErr(e) }); });
    return () => { alive = false; };
  }, [pick, sheetSel.line_id, sheetSel.month, token]);
  const onDataSelect = (kind, msg) => {
    const lid = msg && msg.line_id ? Number(msg.line_id) : null;
    const mon = (msg && msg.month) || sheetSel.month;
    setSheetSel(s => (s.line_id === lid && s.month === mon) ? s : { line_id: lid, month: mon });
  };
  // line list of the dropdowns (the lines this login may see), zone by zone
  const lineOpts = useMemo(() => lines
    .map(l => ({ line_id: l.id, line_name: l.line_name || l.line_code || `Line ${l.id}`, zone_name: zoneOf(l) }))
    .sort((a, b) => a.zone_name.localeCompare(b.zone_name) || a.line_name.localeCompare(b.line_name)),
  [lines]);
  const docData = {
    "track-record": stdTrack ? {
      lines: stdTrack.map(r => ({ line_id: r.line_id, line_name: r.line_name, zone_name: r.zone_name,
                                  cycle_time: r.cycle_time, target: r.target })),
      line_id: sheetSel.line_id,
      month: sheetSel.month,
      sheet: trackSheet.data, loading: trackSheet.loading, error: trackSheet.error,
    } : null,
    "line-month": { lines: lineOpts, line_id: sheetSel.line_id, month: sheetSel.month },
  };
  const docCtx = { zoneName, lineName, hasLine: !!curLine, all: zone === "__all" && !curLine,
                   isYRASS: !!curLine && /^YRA-SS$/i.test(String(lineName).trim()) };
  const docVariants = picked && DOCS[picked.key]
    ? DOCS[picked.key].filter(v => !v.match || v.match(docCtx))
    : [];

  const section = (title, list, offset, pages = []) => (
    <div style={{ marginBottom: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <span style={{ fontSize: 13, fontWeight: 800, color: "#0f172a", letterSpacing: ".04em",
                       textTransform: "uppercase" }}>{title}</span>
        <span style={{ fontSize: 11, fontWeight: 700, color: "#1e40af", background: "rgba(30,64,175,.08)",
                       border: "1px solid rgba(30,64,175,.18)", borderRadius: 999, padding: "1px 9px" }}>
          {list.length} sheets
        </span>
        <span style={{ flex: 1, height: 1, background: "#e2e8f0" }} />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 10 }}>
        {[...list, ...pages].map((s, i) => (
          <SheetButton key={s.key} sheet={s} n={offset + i + 1} active={pick === s.key}
                       onClick={() => setPick(p => (p === s.key ? null : s.key))} />
        ))}
      </div>
    </div>
  );

  return (
    <div style={{ padding: "18px 22px 60px", color: "#0f172a", minHeight: "100vh", background: "#f8fafc",
                  fontFamily: "'Barlow',sans-serif" }}>
      <PageTopbar leading="Control" accent="Board" />

      {/* Filters */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end", margin: "16px 0 18px",
                    background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, padding: "14px 16px" }}>
        <div>
          <label style={lbl}>Date</label>
          <input type="date" value={date} onChange={e => setDate(e.target.value)} style={inp} />
        </div>
        <div>
          <label style={lbl}>Zone</label>
          <select value={zone} onChange={e => pickZone(e.target.value)} style={inp}>
            <option value="__all">All zones</option>
            {zones.map(z => <option key={z} value={z}>{z}</option>)}
          </select>
        </div>
        <div>
          <label style={lbl}>Line</label>
          <select value={lineId} onChange={e => setLineId(e.target.value)} style={{ ...inp, minWidth: 200 }}>
            <option value="">All lines</option>
            {shownLines.map(l => <option key={l.id} value={l.id}>{l.line_name || l.line_code || `Line ${l.id}`}</option>)}
          </select>
        </div>
        <div style={{ marginLeft: "auto", fontSize: 12, color: "#64748b", paddingBottom: 9 }}>
          {fmtDate(date)} · {zoneName} · {lineName}
        </div>
      </div>

      {section("Front side", FRONT, 0)}
      {section("Back side", BACK, FRONT.length, backPages)}

      <div ref={demoRef} style={{ scrollMarginTop: 96 }} />
      {pickedPage ? (
        <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, overflow: "hidden",
                      boxShadow: "0 1px 3px rgba(0,0,0,.05)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px",
                        borderBottom: "1px solid #e2e8f0", background: "#f8fafc" }}>
            <span style={{ fontSize: 20 }}>{pickedPage.icon}</span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ fontSize: 16, fontWeight: 800, color: "#0f172a" }}>{pickedPage.label}</div>
              <div style={{ fontSize: 12, color: "#64748b" }}>Back side · Red Bin portal rejections, zone and line wise</div>
            </div>
            <button onClick={() => setPick(null)} title="Close" style={{
              border: "1px solid #e2e8f0", background: "#fff", color: "#475569", borderRadius: 8,
              padding: "5px 10px", fontSize: 13, cursor: "pointer", fontFamily: "inherit" }}>✕</button>
          </div>
          <div style={{ background: "#f8fafc" }}>
            <Suspense fallback={<div style={{ padding: 24, color: "#64748b", fontSize: 14 }}>Loading…</div>}>
              <LineRejection embedded />
            </Suspense>
          </div>
        </div>
      ) : picked && docVariants.length > 0 && docVariants.every(v => v.stack) ? (
        // several sheets of one format, one under the other (Operator Status: Sheet 1 / Sheet 2)
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {docVariants.map(v => (
            <SheetDoc key={`${v.data}:${v.ops || v.label}`} sheet={{ ...picked, label: `${picked.label} — ${v.label}` }} n={pickedNo}
                      variants={[v]} zoneName={zoneName} fileTag={`${curLine ? lineName : zoneName} ${date}`}
                      token={token} docData={docData} onDataSelect={onDataSelect} onClose={() => setPick(null)}
                      pageSel={{ line_id: curLine ? curLine.id : null, date }} />
          ))}
        </div>
      ) : picked && docVariants.length > 0 ? (
        <SheetDoc sheet={picked} n={pickedNo} variants={docVariants} zoneName={zoneName}
                  fileTag={`${curLine ? lineName : zoneName} ${date}`} token={token}
                  docData={docData} onDataSelect={onDataSelect} onClose={() => setPick(null)}
                  pageSel={{ line_id: curLine ? curLine.id : null, date }} />
      ) : picked ? (
        <SheetDemo sheet={picked} n={pickedNo} date={date} zoneName={zoneName} lineName={lineName}
                   note={DOCS[picked.key] ? (DOC_NOTES[picked.key] || "Format not added for this line yet.") : null}
                   onClose={() => setPick(null)} />
      ) : (
        <div style={{ border: "1.5px dashed #cbd5e1", borderRadius: 14, padding: "28px 16px", textAlign: "center",
                      color: "#64748b", fontSize: 14, background: "#fff" }}>
          Select a sheet to open it.
        </div>
      )}
    </div>
  );
}
