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
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import PageTopbar from "../components/PageTopbar";

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
const ALL = [...FRONT.map(s => ({ ...s, side: "Front" })), ...BACK.map(s => ({ ...s, side: "Back" }))];

const isSeatSlider = (z) => /seat\s*slider/i.test(String(z || ""));
const isRecliner = (z) => /^recliner$/i.test(String(z || "").trim());

// Sheets with a real format: one or more HTML files under /control-board-docs/.
const DOC_BASE = "/control-board-docs/";
// width = the sheet's own page width (body padding + sheet), for "Fit width".
const DOCS = {
  // data: the sheet gets line / month dropdowns and its cycle time + target
  // from Admin → Standard Data → Track Record (posted in, see SheetDoc).
  "track-sheet":      [{ label: "Track Record Sheet", file: "track-record-sheet.html", width: 1710,
                         data: "track-record" }],
  "4m-change":        [{ label: "4M Change Control Sheet", file: "4m-change-control-sheet.html", width: 1690 }],
  "operator-visual":  [{ label: "Operator Status", file: "operator-status.html", width: 1790 }],
  "defect-recording": [{ label: "Standard", file: "defect-recording-sheet.html", width: 1725 },
                       { label: "Recliner", file: "defect-recording-sheet-recliner.html", width: 1725, zone: "recliner" }],
  "annual-safety":    [{ label: "Annual Safety Chart", file: "annual-safety-chart.html", width: 1445 }],
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
          SHEET {String(n).padStart(2, "0")}
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
function SheetDoc({ sheet, n, variants, zoneName, docData, onClose }) {
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
  const btn = { border: "1px solid #e2e8f0", background: "#fff", color: "#475569", borderRadius: 8,
                padding: "5px 10px", fontSize: 13, cursor: "pointer", fontFamily: "inherit", whiteSpace: "nowrap" };
  const head = (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", flexWrap: "wrap",
                  borderBottom: "1px solid #e2e8f0", background: "#f8fafc" }}>
      <span style={{ fontSize: 20 }}>{sheet.icon}</span>
      <div style={{ minWidth: 0, flex: "1 1 200px" }}>
        <div style={{ fontSize: 16, fontWeight: 800, color: "#0f172a" }}>{sheet.label}</div>
        <div style={{ fontSize: 12, color: "#64748b" }}>
          {sheet.side} side · Sheet {String(n).padStart(2, "0")}{v.view ? "" : " · entries are not saved yet"}
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
      <button onClick={() => setFull(f => !f)} style={btn}>{full ? "Exit full screen" : "Full screen"}</button>
      <button onClick={() => { setFull(false); onClose(); }} title="Close" style={btn}>✕</button>
    </div>
  );
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
  useEffect(() => {
    const onMsg = (e) => {
      const f = frameRef.current;
      if (f && e.source === f.contentWindow && e.data && e.data.type === "cb-zoom-ok") {
        clearTimeout(fallbackRef.current);
        setInner(true);
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
  const dataFor = v.data && docData ? docData[v.data] : null;
  const dataKey = dataFor ? JSON.stringify(dataFor) : "";
  const sendData = () => {
    if (!dataFor) return;
    try { frameRef.current && frameRef.current.contentWindow.postMessage({ type: "cb-data", ...dataFor }, "*"); }
    catch { /* frame gone */ }
  };
  useEffect(() => { sendData(); }, [dataKey]);   // eslint-disable-line
  const onFrameLoad = () => {
    sendData();
    sendZoom(scale);
    clearTimeout(fallbackRef.current);
    if (!v.fluid) fallbackRef.current = setTimeout(() => setInner(x => (x === null ? false : x)), 1500);
  };
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
  if (full) {
    return (
      <div style={{ position: "fixed", inset: 0, zIndex: 9500, background: "#fff", display: "flex",
                    flexDirection: "column",
                    // installed app: start below the status bar (0 on the website)
                    paddingTop: "calc(var(--cap-sat, 0px) * var(--mes-zinv, 1))" }}>
        {head}
        {frame}
      </div>
    );
  }
  return (
    <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, overflow: "hidden",
                  boxShadow: "0 1px 3px rgba(0,0,0,.05)" }}>
      {head}
      {frame}
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
  const { token } = useAuth();
  const [lines, setLines]   = useState([]);
  const [date, setDate]     = useState(todayStr);
  const [zone, setZone]     = useState("__all");
  const [lineId, setLineId] = useState("");
  // ?sheet=<key> opens that sheet directly (e.g. /control-board?sheet=track-sheet)
  const [pick, setPick]     = useState(() => {
    try { const k = new URLSearchParams(window.location.search).get("sheet"); return ALL.some(s => s.key === k) ? k : null; }
    catch { return null; }
  });
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
  const docData = {
    "track-record": stdTrack ? {
      lines: stdTrack.map(r => ({ line_id: r.line_id, line_name: r.line_name, zone_name: r.zone_name,
                                  cycle_time: r.cycle_time, target: r.target })),
      line_id: curLine ? curLine.id : null,
      month: String(date || "").slice(0, 7),
    } : null,
  };
  const docCtx = { zoneName, lineName, hasLine: !!curLine, all: zone === "__all" && !curLine,
                   isYRASS: !!curLine && /^YRA-SS$/i.test(String(lineName).trim()) };
  const docVariants = picked && DOCS[picked.key]
    ? DOCS[picked.key].filter(v => !v.match || v.match(docCtx))
    : [];

  const section = (title, list, offset) => (
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
        {list.map((s, i) => (
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
      {section("Back side", BACK, FRONT.length)}

      <div ref={demoRef} style={{ scrollMarginTop: 96 }} />
      {picked && docVariants.length > 0 ? (
        <SheetDoc sheet={picked} n={pickedNo} variants={docVariants} zoneName={zoneName}
                  docData={docData} onClose={() => setPick(null)} />
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
