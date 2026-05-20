/* ───────────────────────────────────────────────────────────────────
 * QualityDeviations.jsx
 * ───────────────────────────────────────────────────────────────────
 * Quality user's Deviation queue + 4M Change Notes + NCRs + Defect
 * Pareto.  Single landing page so the section head can triage
 * everything in <30 s.
 *
 * Sprint-1 expansion (2026-05-13): added NCR (Non-Conformance Report)
 * tab + Defect Pareto tab.  Every defect found on the floor is logged
 * as an NCR; Pareto auto-rolls them by defect_type for 80/20 reviews.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";

function fmtAgo(ts) {
  if (!ts) return "—";
  const ms = Date.now() - new Date(ts).getTime();
  if (ms < 0) return "just now";
  const s = Math.floor(ms / 1000);
  if (s < 60)   return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60)   return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24)   return `${h}h ${m % 60}m ago`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h ago`;
}

function fmtDt(ts) {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString("en-IN", {
      day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    });
  } catch { return "—"; }
}

function Tile({ label, value, sub, color }) {
  return (
    <div style={{
      background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12,
      padding: "14px 18px", minWidth: 150, flex: "0 0 auto",
      boxShadow: "0 1px 3px rgba(0,0,0,.04)",
    }}>
      <div style={{ fontSize: 10, color: "#64748b", fontWeight: 700,
                    letterSpacing: ".08em", textTransform: "uppercase" }}>
        {label}
      </div>
      <div style={{ fontSize: 26, fontWeight: 800, color: color || "#0f172a",
                    marginTop: 2, fontFamily: "'Barlow Condensed',sans-serif" }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 1 }}>{sub}</div>}
    </div>
  );
}

function Pill({ status }) {
  const map = {
    PENDING:          { bg: "rgba(202,138,4,.12)", color: "#a16207", label: "Pending" },
    APPROVED:         { bg: "rgba(22,163,74,.12)", color: "#15803d", label: "Approved" },
    DENIED:           { bg: "rgba(220,38,38,.12)", color: "#b91c1c", label: "Denied" },
    PENDING_QA:       { bg: "rgba(202,138,4,.12)", color: "#a16207", label: "Awaiting QA" },
    REJECTED:         { bg: "rgba(220,38,38,.12)", color: "#b91c1c", label: "Rejected" },
    EXTENDED:         { bg: "rgba(8,145,178,.12)", color: "#0e7490", label: "Extended" },
    CLOSED:           { bg: "rgba(71,85,105,.12)", color: "#475569", label: "Closed" },
    OPEN:             { bg: "rgba(202,138,4,.12)", color: "#a16207", label: "Open" },
    VOID:             { bg: "rgba(100,116,139,.12)", color: "#64748b", label: "Void" },
    REWORK:           { bg: "rgba(8,145,178,.12)", color: "#0e7490", label: "Rework" },
    SCRAP:            { bg: "rgba(220,38,38,.12)", color: "#b91c1c", label: "Scrap" },
    ACCEPT_AS_IS:     { bg: "rgba(22,163,74,.12)", color: "#15803d", label: "Accept-as-is" },
    RETURN_TO_VENDOR: { bg: "rgba(124,58,237,.12)", color: "#6d28d9", label: "Return to Vendor" },
  };
  const m = map[status] || { bg: "#f1f5f9", color: "#64748b", label: status || "—" };
  return (
    <span style={{ padding: "2px 9px", borderRadius: 99, fontSize: 10, fontWeight: 700,
                    background: m.bg, color: m.color, whiteSpace: "nowrap" }}>
      {m.label}
    </span>
  );
}

export default function QualityDeviations() {
  const { token, theme, isAdmin, user } = useAuth();
  const [tab, setTab]               = useState("deviations");
  const [kpi, setKpi]               = useState({});
  const [deviations, setDeviations] = useState([]);
  const [fourMs, setFourMs]         = useState([]);
  const [ncrs, setNcrs]             = useState([]);
  const [defectTypes, setDefectTypes] = useState([]);
  const [lines, setLines]           = useState([]);
  const [pareto, setPareto]         = useState({ buckets: [], total_qty: 0 });
  const [paretoDays, setParetoDays] = useState(30);
  const [paretoLine, setParetoLine] = useState("");
  const [ncrLineFilter, setNcrLineFilter] = useState("");
  const [ncrStatusFilter, setNcrStatusFilter] = useState("OPEN");
  const [devForm, setDevForm]       = useState(null);          // deviation row to view/edit
  const [loading, setLoading]       = useState(true);
  const [DeviationFormModal, setDeviationFormModal] = useState(null);
  const [ncrCreateOpen, setNcrCreateOpen] = useState(false);
  const [ncrCloseTarget, setNcrCloseTarget] = useState(null);  // NCR row to close
  const [ncrViewTarget, setNcrViewTarget] = useState(null);    // NCR row to inspect (closed)

  // Sprint-2: Inspection + First/Last Piece + PPM + Control Plans
  const [inspChars, setInspChars]         = useState([]);
  const [inspLog,   setInspLog]           = useState([]);
  const [inspLineFilter, setInspLineFilter] = useState("");
  const [inspStatusFilter, setInspStatusFilter] = useState("");
  const [flpRows, setFlpRows]             = useState([]);
  const [ppm, setPpm]                     = useState({ lines: [], overall_ppm: 0, total_produced: 0, total_rejected: 0 });
  const [ppmDays, setPpmDays]             = useState(30);
  const [controlPlans, setControlPlans]   = useState([]);
  const [inspCreateOpen, setInspCreateOpen] = useState(false);
  const [inspCharsOpen, setInspCharsOpen] = useState(false);
  const [flpCreateOpen, setFlpCreateOpen] = useState(false);
  const [cpUploadOpen, setCpUploadOpen]   = useState(false);
  // Inspection sub-tab: "log" (history) | "flp" (first/last piece)
  const [inspSubTab, setInspSubTab]       = useState("log");

  // Lazy-load Deviation form so first paint of the dashboard isn't
  // gated on parsing it.
  useEffect(() => {
    let alive = true;
    import("./DeviationForm").then(m => {
      if (alive) setDeviationFormModal(() => m.default);
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  // Reload — `silent=true` skips the loading spinner so background
  // 10-sec polls don't flash "Loading..." over the table every tick.
  const reload = useCallback(async (silent = false) => {
    if (!token) return;
    if (!silent) setLoading(true);
    try {
      const ncrQs  = `?days=60${ncrLineFilter ? `&line_id=${ncrLineFilter}` : ""}${ncrStatusFilter ? `&status=${ncrStatusFilter}` : ""}`;
      const inspQs = `?days=7${inspLineFilter ? `&line_id=${inspLineFilter}` : ""}${inspStatusFilter ? `&status=${inspStatusFilter}` : ""}`;
      const [k, d, f, n, dt, ls, ic, il, flp, cp] = await Promise.all([
        api.get("/api/quality/kpi", token).catch(() => ({})),
        api.get("/api/quality/deviations?days=60", token).catch(() => []),
        api.get("/api/quality/4m-changes?days=60", token).catch(() => []),
        api.get(`/api/quality/ncr${ncrQs}`, token).catch(() => []),
        api.get("/api/quality/ncr/defect-types", token).catch(() => []),
        api.get("/api/lines/", token).catch(() => []),
        api.get("/api/quality/inspection-chars", token).catch(() => []),
        api.get(`/api/quality/inspection-log${inspQs}`, token).catch(() => []),
        api.get("/api/quality/first-last-piece?days=30", token).catch(() => []),
        api.get("/api/quality/control-plans", token).catch(() => []),
      ]);
      setKpi(k || {});
      setDeviations(Array.isArray(d) ? d : []);
      setFourMs(Array.isArray(f) ? f : []);
      setNcrs(Array.isArray(n) ? n : []);
      setDefectTypes(Array.isArray(dt) ? dt : []);
      setLines(Array.isArray(ls) ? ls : []);
      setInspChars(Array.isArray(ic) ? ic : []);
      setInspLog(Array.isArray(il) ? il : []);
      setFlpRows(Array.isArray(flp) ? flp : []);
      setControlPlans(Array.isArray(cp) ? cp : []);
    } finally { if (!silent) setLoading(false); }
  }, [token, ncrLineFilter, ncrStatusFilter, inspLineFilter, inspStatusFilter]);

  // Pareto is fetched separately because its filter set (days, line) is
  // independent of the NCR-list filter set above.
  const reloadPareto = useCallback(async () => {
    if (!token) return;
    try {
      const qs = `?days=${paretoDays}${paretoLine ? `&line_id=${paretoLine}` : ""}`;
      const p = await api.get(`/api/quality/ncr/pareto${qs}`, token);
      setPareto(p || { buckets: [], total_qty: 0 });
    } catch { setPareto({ buckets: [], total_qty: 0 }); }
  }, [token, paretoDays, paretoLine]);

  // PPM — separate fetch (own day window)
  const reloadPpm = useCallback(async () => {
    if (!token) return;
    try {
      const p = await api.get(`/api/quality/ppm?days=${ppmDays}`, token);
      setPpm(p || { lines: [], overall_ppm: 0, total_produced: 0, total_rejected: 0 });
    } catch { setPpm({ lines: [], overall_ppm: 0, total_produced: 0, total_rejected: 0 }); }
  }, [token, ppmDays]);

  useEffect(() => {
    reload(false);                       // first paint — show spinner
    const t = setInterval(() => reload(true), 10000);   // silent refresh
    const onChange = () => reload(true);
    window.addEventListener("ap-config-changed", onChange);
    return () => { clearInterval(t); window.removeEventListener("ap-config-changed", onChange); };
  }, [reload]);

  useEffect(() => { reloadPareto(); }, [reloadPareto]);
  useEffect(() => { reloadPpm(); }, [reloadPpm]);

  // Auto-open a specific deviation if hash nav landed us with ?dev=NN
  useEffect(() => {
    if (!deviations.length) return;
    const params = new URLSearchParams(window.location.search);
    const devId  = parseInt(params.get("dev") || "0", 10);
    if (devId) {
      const row = deviations.find(d => d.id === devId);
      if (row) setDevForm(row);
    }
  }, [deviations]);

  useEffect(() => {
    document.title = isAdmin ? "Quality" : "Quality";
  }, [isAdmin]);

  const TABS = [
    { key: "deviations", label: `Deviations (${kpi.pending_deviations ?? 0})` },
    { key: "4m",         label: `4M Changes (${kpi.open_4m_changes ?? 0})` },
    { key: "ncr",        label: `NCR (${kpi.open_ncr ?? 0})` },
    { key: "pareto",     label: "Defect Pareto" },
    { key: "inspection", label: `Inspection (${kpi.inspections_today ?? 0})` },
    { key: "ppm",        label: "PPM" },
    { key: "control",    label: `Control Plans (${controlPlans.length})` },
  ];

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@600;700;800&display=swap');
        .qd-root { min-height:100vh; background:#f8fafc; font-family:'Barlow',sans-serif; padding-bottom:48px; }
        .qd-topbar {
          background:#fff; border-bottom:1px solid #e2e8f0;
          padding:0 40px 0 88px; height:60px;
          display:flex; align-items:center; justify-content:space-between;
          position:sticky; top:0; z-index:50; box-shadow:0 1px 3px rgba(0,0,0,.06);
        }
        .qd-topbar::after { content:''; position:absolute; bottom:0; left:0; right:0;
                            height:2px; background:${theme.gradient}; }
        .qd-title { position:absolute; left:50%; transform:translateX(-50%);
                    font-family:'Barlow Condensed',sans-serif; font-size:34px;
                    font-weight:800; color:#0f172a; letter-spacing:-.01em;
                    pointer-events:none; white-space:nowrap; }
        .qd-title span { color:${theme.accent}; }
        .qd-pill { display:flex; align-items:center; gap:10px;
                    padding:6px 14px; border-radius:99px;
                    border:1.5px solid #e2e8f0; background:#f8fafc;
                    font-size:12px; font-weight:600; color:#334155; white-space:nowrap; }
        .qd-pill b { color:#0f172a; font-weight:800; }
        .qd-body { padding:24px 40px 0; max-width:1400px; margin:0 auto; }
        .qd-tiles { display:flex; gap:14px; flex-wrap:wrap; margin-bottom:18px; }
        .qd-tabs { display:flex; gap:0; background:#fff; border:1px solid #e2e8f0;
                    border-bottom:2px solid #e2e8f0; border-radius:12px 12px 0 0;
                    overflow:hidden; margin-bottom:0; }
        .qd-tab  { flex:1; padding:13px 20px; font-family:'Barlow',sans-serif;
                    font-size:14px; font-weight:700; cursor:pointer; border:none;
                    color:#64748b; background:#fff; transition:all .15s; }
        .qd-tab.active { background:${theme.accentDark}; color:#fff; }
        .qd-card { background:#fff; border:1px solid #e2e8f0;
                    border-radius:0 0 12px 12px; box-shadow:0 1px 3px rgba(0,0,0,.04); }
        .qd-th { padding:10px 14px; text-align:left; font-size:10px; font-weight:700;
                  letter-spacing:.08em; text-transform:uppercase; color:#64748b;
                  border-bottom:2px solid #e2e8f0; white-space:nowrap; background:#f8fafc; }
        .qd-td { padding:11px 14px; font-size:12px; color:#0f172a; vertical-align:middle; }
        .qd-btn { padding:6px 14px; border-radius:7px; font-weight:700; font-size:11px;
                   cursor:pointer; border:none; white-space:nowrap; }
        .qd-btn-approve { background:linear-gradient(135deg,#16a34a,#15803d); color:#fff; }
        .qd-btn-deny    { background:#fff; color:#dc2626; border:1.5px solid #dc2626; }
        .qd-btn-view    { background:#fff; color:${theme.accent};
                            border:1.5px solid ${theme.accent}; }
        .qd-btn-primary { background:${theme.accentDark}; color:#fff; }
        .qd-empty { padding:48px 20px; text-align:center; color:#94a3b8; font-style:italic; }
        .qd-toolbar { display:flex; gap:10px; padding:14px; border-bottom:1px solid #e2e8f0;
                       flex-wrap:wrap; align-items:center; }
        .qd-input { padding:7px 11px; border:1px solid #cbd5e1; border-radius:7px;
                     font-size:12px; font-weight:600; color:#0f172a; background:#fff;
                     font-family:'Barlow',sans-serif; }
        .qd-input:focus { outline:none; border-color:${theme.accent}; }
        .qd-label { font-size:10px; color:#64748b; font-weight:700; letter-spacing:.08em;
                     text-transform:uppercase; margin-bottom:3px; }
        .qd-pareto-row { display:flex; align-items:center; padding:9px 14px;
                          border-bottom:1px solid #f1f5f9; gap:12px; }
        .qd-pareto-row:last-child { border-bottom:none; }
        .qd-bar-bg { flex:1; height:22px; background:#f1f5f9; border-radius:5px;
                      overflow:hidden; position:relative; min-width:120px; }
        .qd-bar-fg { height:100%; background:linear-gradient(90deg,${theme.accent},${theme.accentDark});
                      transition:width .3s; }
        .qd-bar-cum { position:absolute; top:0; bottom:0; width:2px;
                       background:#dc2626; pointer-events:none; }
      `}</style>

      <div className="qd-root">
        <div className="qd-topbar">
          <div /> {/* logo placeholder */}
          <div className="qd-title">
            Quality <span>Centre</span>
          </div>
          {user?.username && (
            <div className="qd-pill">Signed in as <b>{user.username}</b></div>
          )}
        </div>

        <div className="qd-body">
          {/* KPI tiles — Deviations / 4M / NCR / PY fails */}
          <div className="qd-tiles">
            <Tile label="Pending Deviations"
                   value={kpi.pending_deviations ?? "—"}
                   sub="awaiting QA approval"
                   color="#dc2626"/>
            <Tile label="Open Deviations"
                   value={kpi.open_deviations ?? "—"}
                   sub="approved + extended"
                   color="#0891b2"/>
            <Tile label="Open NCRs"
                   value={kpi.open_ncr ?? "—"}
                   sub={`${kpi.ncr_open_qty ?? 0} parts pending`}
                   color="#b91c1c"/>
            <Tile label="NCR Qty Today"
                   value={kpi.ncr_qty_today ?? "—"}
                   sub="parts rejected today"
                   color="#ea580c"/>
            <Tile label="Closed (Month)"
                   value={kpi.closed_deviations_month ?? "—"}
                   sub="deviations resolved"
                   color="#16a34a"/>
            <Tile label="PY Fails Today"
                   value={kpi.py_fails_today ?? "—"}
                   sub="sensor bypass events"
                   color="#dc2626"/>
            <Tile label="Open 4M Changes"
                   value={kpi.open_4m_changes ?? "—"}
                   color="#7c3aed"/>
            <Tile label="Inspections Today"
                   value={kpi.inspections_today ?? "—"}
                   sub={`${kpi.inspections_ng_today ?? 0} NG`}
                   color={(kpi.inspections_ng_today ?? 0) > 0 ? "#dc2626" : "#0891b2"}/>
            <Tile label="PPM (30d)"
                   value={ppm.overall_ppm ?? "—"}
                   sub={`${ppm.total_rejected ?? 0} of ${ppm.total_produced ?? 0}`}
                   color={ppm.overall_ppm > 1000 ? "#dc2626" : "#16a34a"}/>
          </div>

          {/* Tab strip */}
          <div className="qd-tabs">
            {TABS.map(t => (
              <button key={t.key}
                      className={`qd-tab${tab === t.key ? " active" : ""}`}
                      onClick={() => setTab(t.key)}>
                {t.label}
              </button>
            ))}
          </div>

          {/* TAB BODY — Deviations */}
          {tab === "deviations" && (
            <div className="qd-card">
              {loading ? <div className="qd-empty">Loading…</div>
               : deviations.length === 0 ? (
                <div className="qd-empty">
                  No deviations in the last 60 days.
                </div>
              ) : (
                <div style={{overflowX:"auto"}}>
                  <table style={{width:"100%", borderCollapse:"collapse"}}>
                    <thead><tr>
                      {["Dev No.", "Raised", "Line / Zone", "Machine",
                        "Process / Reason", "Qty / Upto", "Status",
                        "Raised By", ""].map(h =>
                        <th key={h} className="qd-th">{h}</th>)}
                    </tr></thead>
                    <tbody>
                      {deviations.map(d => (
                        <tr key={d.id} style={{borderBottom:"1px solid #f1f5f9"}}>
                          <td className="qd-td" style={{fontFamily:"monospace", fontWeight:700,
                                                          color: theme.accentDark}}>
                            {d.dev_no || `#${d.id}`}
                          </td>
                          <td className="qd-td" style={{fontFamily:"monospace"}}>
                            {fmtAgo(d.created_at)}
                          </td>
                          <td className="qd-td">
                            <div style={{fontWeight:700}}>{d.line_name || `Line ${d.line_id}`}</div>
                            <div style={{fontSize:10, color:"#94a3b8"}}>{d.zone_name || "—"}</div>
                          </td>
                          <td className="qd-td" style={{fontFamily:"monospace"}}>
                            {d.machine_no ? `#${d.machine_no} · ${d.machine_name || ""}` : "—"}
                          </td>
                          <td className="qd-td" style={{maxWidth:240}}>
                            <div style={{fontWeight:600, whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis"}}>
                              {d.process_name || "—"}
                            </div>
                            <div style={{fontSize:10, color:"#64748b", whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis"}}
                                 title={d.reason || ""}>
                              {d.reason || "—"}
                            </div>
                          </td>
                          <td className="qd-td" style={{fontFamily:"monospace"}}>
                            {d.deviation_qty || 0} / {d.deviation_upto_qty || "—"}
                          </td>
                          <td className="qd-td"><Pill status={d.status}/></td>
                          <td className="qd-td">{d.raised_by_username || d.initiated_by || "—"}</td>
                          <td className="qd-td">
                            <button className="qd-btn qd-btn-view"
                                    onClick={() => setDevForm(d)}>
                              {d.status === "PENDING_QA" ? "Review" : "View"}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* TAB BODY — 4M Changes */}
          {tab === "4m" && (
            <div className="qd-card">
              {loading ? <div className="qd-empty">Loading…</div>
               : fourMs.length === 0 ? (
                <div className="qd-empty">
                  No 4M Change Notes in the last 60 days.
                </div>
              ) : (
                <div style={{overflowX:"auto"}}>
                  <table style={{width:"100%", borderCollapse:"collapse"}}>
                    <thead><tr>
                      {["Note No.", "Issued", "Line / Zone", "Part / Model",
                        "Originator", "Changing Points", "Status", ""].map(h =>
                        <th key={h} className="qd-th">{h}</th>)}
                    </tr></thead>
                    <tbody>
                      {fourMs.map(n => {
                        const cp = n.changing_points || {};
                        const pts = Object.entries(cp).filter(([_,v]) => v).map(([k]) => k.toUpperCase()).join(", ");
                        return (
                          <tr key={n.id} style={{borderBottom:"1px solid #f1f5f9"}}>
                            <td className="qd-td" style={{fontFamily:"monospace", fontWeight:700,
                                                            color: theme.accentDark}}>
                              {n.note_no || `#${n.id}`}
                            </td>
                            <td className="qd-td" style={{fontFamily:"monospace"}}>
                              {fmtDt(n.issue_date || n.created_at)}
                            </td>
                            <td className="qd-td">
                              <div style={{fontWeight:700}}>{n.line_name || `Line ${n.line_id}`}</div>
                              <div style={{fontSize:10, color:"#94a3b8"}}>{n.zone_name || "—"}</div>
                            </td>
                            <td className="qd-td">
                              <div style={{fontWeight:600}}>{n.part_name || "—"}</div>
                              <div style={{fontSize:10, color:"#94a3b8"}}>{n.model || "—"}</div>
                            </td>
                            <td className="qd-td">{n.originator_name || "—"}</td>
                            <td className="qd-td" style={{fontSize:10, color:"#475569"}}>{pts || "—"}</td>
                            <td className="qd-td"><Pill status={n.status}/></td>
                            <td className="qd-td">
                              <button className="qd-btn qd-btn-view" disabled
                                      title="Coming soon — full 4M form module">
                                View
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* TAB BODY — NCR */}
          {tab === "ncr" && (
            <div className="qd-card">
              <div className="qd-toolbar">
                <div>
                  <div className="qd-label">Line</div>
                  <select className="qd-input"
                          value={ncrLineFilter}
                          onChange={e => setNcrLineFilter(e.target.value)}>
                    <option value="">All Lines</option>
                    {lines.map(l => (
                      <option key={l.id} value={l.id}>{l.line_name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <div className="qd-label">Status</div>
                  <select className="qd-input"
                          value={ncrStatusFilter}
                          onChange={e => setNcrStatusFilter(e.target.value)}>
                    <option value="">All</option>
                    <option value="OPEN">Open</option>
                    <option value="CLOSED">Closed</option>
                    <option value="VOID">Void</option>
                  </select>
                </div>
                <div style={{flex:1}} />
                <button className="qd-btn qd-btn-primary"
                        style={{padding:"9px 18px", fontSize:12}}
                        onClick={() => setNcrCreateOpen(true)}>
                  + New NCR
                </button>
              </div>

              {loading ? <div className="qd-empty">Loading…</div>
               : ncrs.length === 0 ? (
                <div className="qd-empty">
                  No NCRs match the current filters. Click <b>+ New NCR</b> to log a defect.
                </div>
              ) : (
                <div style={{overflowX:"auto"}}>
                  <table style={{width:"100%", borderCollapse:"collapse"}}>
                    <thead><tr>
                      {["NCR No.", "Raised", "Line / Zone", "Part",
                        "Defect", "Qty", "Disposition", "Status",
                        "Raised By", ""].map(h =>
                        <th key={h} className="qd-th">{h}</th>)}
                    </tr></thead>
                    <tbody>
                      {ncrs.map(n => (
                        <tr key={n.id} style={{borderBottom:"1px solid #f1f5f9"}}>
                          <td className="qd-td" style={{fontFamily:"monospace", fontWeight:700,
                                                          color: theme.accentDark}}>
                            {n.ncr_number}
                          </td>
                          <td className="qd-td" style={{fontFamily:"monospace"}}>
                            {fmtAgo(n.raised_at)}
                          </td>
                          <td className="qd-td">
                            <div style={{fontWeight:700}}>{n.line_name || `Line ${n.line_id}`}</div>
                            <div style={{fontSize:10, color:"#94a3b8"}}>{n.zone_name || "—"}</div>
                          </td>
                          <td className="qd-td">
                            <div style={{fontWeight:600}}>{n.part_code || "—"}</div>
                            <div style={{fontSize:10, color:"#94a3b8"}}>{n.part_name || ""}</div>
                          </td>
                          <td className="qd-td">
                            <div style={{fontWeight:600}}>{n.defect_type_name || "—"}</div>
                            <div style={{fontSize:10, color:"#94a3b8"}}>{n.defect_category || ""}</div>
                          </td>
                          <td className="qd-td" style={{fontFamily:"monospace", fontWeight:700}}>
                            {n.qty_rejected}
                          </td>
                          <td className="qd-td"><Pill status={n.disposition}/></td>
                          <td className="qd-td"><Pill status={n.status}/></td>
                          <td className="qd-td">{n.raised_by}</td>
                          <td className="qd-td" style={{whiteSpace:"nowrap"}}>
                            {n.status === "OPEN" ? (
                              <button className="qd-btn qd-btn-approve"
                                      onClick={() => setNcrCloseTarget(n)}>
                                Close
                              </button>
                            ) : (
                              <button className="qd-btn qd-btn-view"
                                      onClick={() => setNcrViewTarget(n)}>
                                View
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* TAB BODY — Defect Pareto */}
          {tab === "pareto" && (
            <div className="qd-card">
              <div className="qd-toolbar">
                <div>
                  <div className="qd-label">Window</div>
                  <select className="qd-input"
                          value={paretoDays}
                          onChange={e => setParetoDays(Number(e.target.value))}>
                    <option value={7}>Last 7 days</option>
                    <option value={30}>Last 30 days</option>
                    <option value={60}>Last 60 days</option>
                    <option value={90}>Last 90 days</option>
                    <option value={180}>Last 180 days</option>
                  </select>
                </div>
                <div>
                  <div className="qd-label">Line</div>
                  <select className="qd-input"
                          value={paretoLine}
                          onChange={e => setParetoLine(e.target.value)}>
                    <option value="">All Lines</option>
                    {lines.map(l => (
                      <option key={l.id} value={l.id}>{l.line_name}</option>
                    ))}
                  </select>
                </div>
                <div style={{flex:1}} />
                <div style={{fontSize:12, color:"#64748b"}}>
                  Total rejected: <b style={{color:"#0f172a"}}>{pareto.total_qty || 0}</b> parts
                  · <b style={{color:"#0f172a"}}>{pareto.buckets?.length || 0}</b> defect types
                </div>
              </div>

              {(!pareto.buckets || pareto.buckets.length === 0) ? (
                <div className="qd-empty">
                  No NCR data in the selected window. Log NCRs in the previous tab to build the Pareto.
                </div>
              ) : (
                <>
                  <div style={{padding:"14px 14px 0", display:"flex", gap:18, fontSize:11,
                                 color:"#64748b", fontWeight:600}}>
                    <div><span style={{display:"inline-block", width:14, height:10, background:theme.accent,
                                          marginRight:6, verticalAlign:"middle", borderRadius:2}}/>
                          Defect share</div>
                    <div><span style={{display:"inline-block", width:2, height:14, background:"#dc2626",
                                          marginRight:6, verticalAlign:"middle"}}/>
                          Cumulative %</div>
                  </div>
                  <div style={{padding:"10px 0 4px"}}>
                    {pareto.buckets.map((b, idx) => (
                      <div key={`${b.defect_type_id}-${idx}`} className="qd-pareto-row">
                        <div style={{width:24, fontFamily:"monospace", fontSize:11,
                                       color:"#94a3b8", fontWeight:700, textAlign:"right"}}>
                          {idx + 1}
                        </div>
                        <div style={{width:200, minWidth:200}}>
                          <div style={{fontSize:12, fontWeight:700, color:"#0f172a",
                                         whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis"}}>
                            {b.defect_type_name}
                          </div>
                          <div style={{fontSize:10, color:"#94a3b8"}}>{b.defect_category}</div>
                        </div>
                        <div className="qd-bar-bg">
                          <div className="qd-bar-fg" style={{width:`${b.share_pct}%`}}/>
                          <div className="qd-bar-cum" style={{left:`${b.cumulative_pct}%`}}/>
                        </div>
                        <div style={{width:90, textAlign:"right", fontFamily:"monospace",
                                       fontSize:12, fontWeight:700, color:"#0f172a"}}>
                          {b.total_qty} <span style={{color:"#94a3b8", fontWeight:500}}>
                            ({b.share_pct}%)
                          </span>
                        </div>
                        <div style={{width:70, textAlign:"right", fontFamily:"monospace",
                                       fontSize:11, fontWeight:700,
                                       color: b.cumulative_pct >= 80 ? "#dc2626" : "#64748b"}}>
                          {b.cumulative_pct}%
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          {/* TAB BODY — Inspection (In-Process + First/Last Piece) */}
          {tab === "inspection" && (
            <div className="qd-card">
              <div className="qd-toolbar" style={{borderBottom:"none", paddingBottom:0}}>
                <div style={{display:"flex", gap:0, borderRadius:8, overflow:"hidden",
                               border:"1px solid #e2e8f0"}}>
                  <button onClick={() => setInspSubTab("log")}
                          style={{padding:"7px 14px", fontSize:11, fontWeight:700,
                                   background: inspSubTab === "log" ? theme.accentDark : "#fff",
                                   color:    inspSubTab === "log" ? "#fff" : "#64748b",
                                   border:"none", cursor:"pointer"}}>
                    Hourly Patrol
                  </button>
                  <button onClick={() => setInspSubTab("flp")}
                          style={{padding:"7px 14px", fontSize:11, fontWeight:700,
                                   background: inspSubTab === "flp" ? theme.accentDark : "#fff",
                                   color:    inspSubTab === "flp" ? "#fff" : "#64748b",
                                   border:"none", cursor:"pointer"}}>
                    First / Last Piece
                  </button>
                </div>
                <div style={{flex:1}} />
                {inspSubTab === "log" && (
                  <>
                    {isAdmin && (
                      <button className="qd-btn qd-btn-view"
                              style={{padding:"9px 14px", fontSize:11}}
                              onClick={() => setInspCharsOpen(true)}>
                        Manage Characteristics
                      </button>
                    )}
                    <button className="qd-btn qd-btn-primary"
                            style={{padding:"9px 18px", fontSize:12}}
                            onClick={() => setInspCreateOpen(true)}
                            disabled={inspChars.length === 0}
                            title={inspChars.length === 0
                                    ? "Admin must add characteristics first"
                                    : ""}>
                      + Log Measurement
                    </button>
                  </>
                )}
                {inspSubTab === "flp" && (
                  <button className="qd-btn qd-btn-primary"
                          style={{padding:"9px 18px", fontSize:12}}
                          onClick={() => setFlpCreateOpen(true)}>
                    + Log First/Last Piece
                  </button>
                )}
              </div>

              {inspSubTab === "log" && (
                <div className="qd-toolbar" style={{borderTop:"1px solid #f1f5f9"}}>
                  <div>
                    <div className="qd-label">Line</div>
                    <select className="qd-input"
                            value={inspLineFilter}
                            onChange={e => setInspLineFilter(e.target.value)}>
                      <option value="">All Lines</option>
                      {lines.map(l => (
                        <option key={l.id} value={l.id}>{l.line_name}</option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <div className="qd-label">Status</div>
                    <select className="qd-input"
                            value={inspStatusFilter}
                            onChange={e => setInspStatusFilter(e.target.value)}>
                      <option value="">All</option>
                      <option value="OK">OK only</option>
                      <option value="NG">NG only</option>
                    </select>
                  </div>
                  <div style={{flex:1}} />
                  <div style={{fontSize:11, color:"#64748b"}}>
                    {inspChars.length} characteristic{inspChars.length !== 1 ? "s" : ""} configured
                  </div>
                </div>
              )}

              {inspSubTab === "log" ? (
                inspLog.length === 0 ? (
                  <div className="qd-empty">
                    No measurements logged in the last 7 days. Click <b>+ Log Measurement</b> to start.
                  </div>
                ) : (
                  <div style={{overflowX:"auto"}}>
                    <table style={{width:"100%", borderCollapse:"collapse"}}>
                      <thead><tr>
                        {["Time", "Line / Zone", "Part", "Characteristic",
                          "Measured", "Tolerance", "Status", "Inspector"].map(h =>
                          <th key={h} className="qd-th">{h}</th>)}
                      </tr></thead>
                      <tbody>
                        {inspLog.map(l => (
                          <tr key={l.id} style={{borderBottom:"1px solid #f1f5f9",
                                                  background: l.status === "NG" ? "#fef2f2" : "transparent"}}>
                            <td className="qd-td" style={{fontFamily:"monospace"}}>
                              {fmtAgo(l.ts_measured)}
                              <div style={{fontSize:10, color:"#94a3b8"}}>{fmtDt(l.ts_measured)}</div>
                            </td>
                            <td className="qd-td">
                              <div style={{fontWeight:700}}>{l.line_name || `Line ${l.line_id}`}</div>
                              <div style={{fontSize:10, color:"#94a3b8"}}>{l.zone_name || "—"}</div>
                            </td>
                            <td className="qd-td" style={{fontFamily:"monospace"}}>{l.part_code || "—"}</td>
                            <td className="qd-td">
                              <div style={{fontWeight:600}}>{l.char_name || "—"}</div>
                              <div style={{fontSize:10, color:"#94a3b8"}}>
                                target {l.target ?? "—"} {l.unit || ""}
                              </div>
                            </td>
                            <td className="qd-td" style={{fontFamily:"monospace", fontWeight:800,
                                                            color: l.status === "NG" ? "#b91c1c" : "#0f172a"}}>
                              {l.measured} <span style={{color:"#94a3b8", fontWeight:500}}>{l.unit || ""}</span>
                            </td>
                            <td className="qd-td" style={{fontFamily:"monospace", fontSize:11, color:"#64748b"}}>
                              {l.lower_tol ?? "—"} … {l.upper_tol ?? "—"}
                            </td>
                            <td className="qd-td"><Pill status={l.status === "NG" ? "REJECTED" : "APPROVED"}/></td>
                            <td className="qd-td">{l.inspector}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              ) : (
                flpRows.length === 0 ? (
                  <div className="qd-empty">
                    No First/Last piece records in the last 30 days. Click <b>+ Log First/Last Piece</b> at model change.
                  </div>
                ) : (
                  <div style={{overflowX:"auto"}}>
                    <table style={{width:"100%", borderCollapse:"collapse"}}>
                      <thead><tr>
                        {["When", "Line / Zone", "Type", "Part / Model",
                          "Status", "Inspector", "Notes"].map(h =>
                          <th key={h} className="qd-th">{h}</th>)}
                      </tr></thead>
                      <tbody>
                        {flpRows.map(r => (
                          <tr key={r.id} style={{borderBottom:"1px solid #f1f5f9",
                                                  background: r.status === "NG" ? "#fef2f2" : "transparent"}}>
                            <td className="qd-td" style={{fontFamily:"monospace"}}>
                              {fmtDt(r.ts_checked)}
                            </td>
                            <td className="qd-td">
                              <div style={{fontWeight:700}}>{r.line_name || `Line ${r.line_id}`}</div>
                              <div style={{fontSize:10, color:"#94a3b8"}}>{r.zone_name || "—"}</div>
                            </td>
                            <td className="qd-td">
                              <span style={{padding:"2px 9px", borderRadius:99, fontSize:10, fontWeight:700,
                                              background: r.piece_type === "FIRST" ? "rgba(8,145,178,.12)" : "rgba(124,58,237,.12)",
                                              color:    r.piece_type === "FIRST" ? "#0e7490" : "#6d28d9"}}>
                                {r.piece_type}
                              </span>
                            </td>
                            <td className="qd-td">
                              <div style={{fontWeight:600}}>{r.part_code || "—"}</div>
                              <div style={{fontSize:10, color:"#94a3b8"}}>{r.model || ""}</div>
                            </td>
                            <td className="qd-td">
                              <Pill status={r.status === "NG" ? "REJECTED" : "APPROVED"}/>
                            </td>
                            <td className="qd-td">{r.inspector}</td>
                            <td className="qd-td" style={{maxWidth:240, fontSize:11, color:"#475569",
                                                            whiteSpace:"nowrap", overflow:"hidden", textOverflow:"ellipsis"}}
                                title={r.notes || ""}>
                              {r.notes || "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              )}
            </div>
          )}

          {/* TAB BODY — PPM */}
          {tab === "ppm" && (
            <div className="qd-card">
              <div className="qd-toolbar">
                <div>
                  <div className="qd-label">Window</div>
                  <select className="qd-input"
                          value={ppmDays}
                          onChange={e => setPpmDays(Number(e.target.value))}>
                    <option value={7}>Last 7 days</option>
                    <option value={30}>Last 30 days</option>
                    <option value={60}>Last 60 days</option>
                    <option value={90}>Last 90 days</option>
                    <option value={180}>Last 180 days</option>
                  </select>
                </div>
                <div style={{flex:1}} />
                <div style={{display:"flex", gap:24, alignItems:"center"}}>
                  <div>
                    <div className="qd-label">Overall PPM</div>
                    <div style={{fontSize:30, fontWeight:800, fontFamily:"'Barlow Condensed',sans-serif",
                                   color: ppm.overall_ppm > 1000 ? "#dc2626" : "#16a34a"}}>
                      {ppm.overall_ppm ?? 0}
                    </div>
                  </div>
                  <div style={{height:40, width:1, background:"#e2e8f0"}}/>
                  <div style={{fontSize:11, color:"#64748b"}}>
                    Rejected: <b style={{color:"#0f172a"}}>{ppm.total_rejected ?? 0}</b><br/>
                    Produced: <b style={{color:"#0f172a"}}>{ppm.total_produced ?? 0}</b>
                  </div>
                </div>
              </div>

              {(!ppm.lines || ppm.lines.length === 0) ? (
                <div className="qd-empty">
                  No production data in the selected window.
                </div>
              ) : (
                <div style={{overflowX:"auto"}}>
                  <table style={{width:"100%", borderCollapse:"collapse"}}>
                    <thead><tr>
                      {["Line / Zone", "Produced", "Rejected", "PPM", "Trend"].map(h =>
                        <th key={h} className="qd-th">{h}</th>)}
                    </tr></thead>
                    <tbody>
                      {ppm.lines.map(l => {
                        const maxPpm = Math.max(1, ...ppm.lines.map(x => x.ppm || 0));
                        const barW = Math.min(100, (l.ppm / maxPpm) * 100);
                        return (
                          <tr key={l.line_id} style={{borderBottom:"1px solid #f1f5f9"}}>
                            <td className="qd-td">
                              <div style={{fontWeight:700}}>{l.line_name}</div>
                              <div style={{fontSize:10, color:"#94a3b8"}}>{l.zone_name || "—"}</div>
                            </td>
                            <td className="qd-td" style={{fontFamily:"monospace", fontWeight:700}}>
                              {l.produced.toLocaleString()}
                            </td>
                            <td className="qd-td" style={{fontFamily:"monospace", fontWeight:700,
                                                            color: l.rejected > 0 ? "#b91c1c" : "#16a34a"}}>
                              {l.rejected.toLocaleString()}
                            </td>
                            <td className="qd-td" style={{fontFamily:"'Barlow Condensed',sans-serif",
                                                            fontSize:18, fontWeight:800,
                                                            color: l.ppm > 1000 ? "#dc2626"
                                                                : l.ppm > 0   ? "#ea580c" : "#16a34a"}}>
                              {l.ppm}
                            </td>
                            <td className="qd-td" style={{minWidth:200}}>
                              <div className="qd-bar-bg" style={{height:14}}>
                                <div className="qd-bar-fg" style={{
                                  width:`${barW}%`,
                                  background: l.ppm > 1000
                                    ? "linear-gradient(90deg,#dc2626,#b91c1c)"
                                    : `linear-gradient(90deg,${theme.accent},${theme.accentDark})`,
                                }}/>
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* TAB BODY — Control Plans */}
          {tab === "control" && (
            <div className="qd-card">
              <div className="qd-toolbar">
                <div style={{fontSize:12, color:"#64748b"}}>
                  Per-part inspection plans (PDF). Operator can open the active version inline.
                </div>
                <div style={{flex:1}} />
                {isAdmin && (
                  <button className="qd-btn qd-btn-primary"
                          style={{padding:"9px 18px", fontSize:12}}
                          onClick={() => setCpUploadOpen(true)}>
                    + Upload Plan
                  </button>
                )}
              </div>

              {controlPlans.length === 0 ? (
                <div className="qd-empty">
                  {isAdmin
                    ? <>No control plans uploaded. Click <b>+ Upload Plan</b> to add one.</>
                    : "No control plans available. Ask admin to upload."}
                </div>
              ) : (
                <div style={{overflowX:"auto"}}>
                  <table style={{width:"100%", borderCollapse:"collapse"}}>
                    <thead><tr>
                      {["Part Code", "Part Name", "Version", "File",
                        "Size", "Uploaded", "Active", ""].map(h =>
                        <th key={h} className="qd-th">{h}</th>)}
                    </tr></thead>
                    <tbody>
                      {controlPlans.map(c => (
                        <tr key={c.id} style={{borderBottom:"1px solid #f1f5f9"}}>
                          <td className="qd-td" style={{fontFamily:"monospace", fontWeight:700,
                                                          color: theme.accentDark}}>
                            {c.part_code}
                          </td>
                          <td className="qd-td">{c.part_name || "—"}</td>
                          <td className="qd-td" style={{fontFamily:"monospace"}}>{c.version || "—"}</td>
                          <td className="qd-td" style={{maxWidth:220,
                                                          whiteSpace:"nowrap", overflow:"hidden",
                                                          textOverflow:"ellipsis"}}>
                            {c.file_name}
                          </td>
                          <td className="qd-td" style={{fontFamily:"monospace", fontSize:11}}>
                            {((c.file_size || 0) / 1024).toFixed(1)} KB
                          </td>
                          <td className="qd-td">
                            <div style={{fontSize:11}}>{fmtDt(c.uploaded_at)}</div>
                            <div style={{fontSize:10, color:"#94a3b8"}}>by {c.uploaded_by}</div>
                          </td>
                          <td className="qd-td">
                            <Pill status={c.is_active ? "APPROVED" : "CLOSED"}/>
                          </td>
                          <td className="qd-td" style={{whiteSpace:"nowrap"}}>
                            <button className="qd-btn qd-btn-view"
                                    onClick={async () => {
                                      try {
                                        const r = await fetch(`/api/quality/control-plans/${c.id}/download`,
                                          { headers: { Authorization: `Bearer ${token}` } });
                                        if (!r.ok) throw new Error(`HTTP ${r.status}`);
                                        const blob = await r.blob();
                                        const url = URL.createObjectURL(blob);
                                        window.open(url, "_blank", "noopener,noreferrer");
                                        setTimeout(() => URL.revokeObjectURL(url), 60000);
                                      } catch (e) { alert(`Open failed: ${e.message}`); }
                                    }}>
                              Open
                            </button>
                            {isAdmin && (
                              <button className="qd-btn qd-btn-deny"
                                      style={{marginLeft:6}}
                                      onClick={async () => {
                                        if (!confirm(`Delete control plan ${c.file_name}?`)) return;
                                        try {
                                          await api.delete(`/api/quality/control-plans/${c.id}`, token);
                                          reload();
                                        } catch (e) { alert(e.message); }
                                      }}>
                                Delete
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Deviation Form (review / view).  Quality user can approve / reject from here. */}
      {devForm && DeviationFormModal && (
        <DeviationFormModal
          deviation={devForm}
          token={token}
          mode={devForm.status === "PENDING_QA" ? "review" : "view"}
          onClose={() => setDevForm(null)}
          onSaved={() => { setDevForm(null); reload(); }}
        />
      )}

      {/* NCR Create modal */}
      {ncrCreateOpen && (
        <NCRCreateModal
          token={token}
          theme={theme}
          lines={lines}
          defectTypes={defectTypes}
          onClose={() => setNcrCreateOpen(false)}
          onSaved={() => { setNcrCreateOpen(false); reload(); reloadPareto(); }}
        />
      )}

      {/* NCR Close modal */}
      {ncrCloseTarget && (
        <NCRCloseModal
          token={token}
          theme={theme}
          ncr={ncrCloseTarget}
          onClose={() => setNcrCloseTarget(null)}
          onSaved={() => { setNcrCloseTarget(null); reload(); reloadPareto(); }}
        />
      )}

      {/* NCR View (read-only summary for closed / void NCRs) */}
      {ncrViewTarget && (
        <NCRViewModal
          theme={theme}
          ncr={ncrViewTarget}
          isAdmin={isAdmin}
          token={token}
          onClose={() => setNcrViewTarget(null)}
          onVoided={() => { setNcrViewTarget(null); reload(); reloadPareto(); }}
        />
      )}

      {/* Inspection — Log measurement modal */}
      {inspCreateOpen && (
        <InspectionLogModal
          token={token} theme={theme}
          lines={lines} chars={inspChars}
          onClose={() => setInspCreateOpen(false)}
          onSaved={() => { setInspCreateOpen(false); reload(); }}
        />
      )}

      {/* Inspection — Characteristics catalog manage */}
      {inspCharsOpen && (
        <InspectionCharsModal
          token={token} theme={theme}
          chars={inspChars}
          onClose={() => setInspCharsOpen(false)}
          onChanged={() => reload()}
        />
      )}

      {/* First/Last Piece create */}
      {flpCreateOpen && (
        <FLPCreateModal
          token={token} theme={theme} lines={lines}
          onClose={() => setFlpCreateOpen(false)}
          onSaved={() => { setFlpCreateOpen(false); reload(); }}
        />
      )}

      {/* Control Plan upload */}
      {cpUploadOpen && (
        <ControlPlanUploadModal
          token={token} theme={theme}
          onClose={() => setCpUploadOpen(false)}
          onSaved={() => { setCpUploadOpen(false); reload(); }}
        />
      )}
    </>
  );
}


/* ─── NCR Create Modal ───────────────────────────────────────────── */
function NCRCreateModal({ token, theme, lines, defectTypes, onClose, onSaved }) {
  const [lineId, setLineId]   = useState(lines[0]?.id || "");
  const [defectId, setDefectId] = useState(defectTypes[0]?.id || "");
  const [partCode, setPartCode] = useState("");
  const [partName, setPartName] = useState("");
  const [qty, setQty]         = useState(1);
  const [shift, setShift]     = useState("");
  const [notes, setNotes]     = useState("");
  const [saving, setSaving]   = useState(false);
  const [err, setErr]         = useState("");

  // Group defect types by category for the dropdown
  const grouped = useMemo(() => {
    const g = {};
    for (const d of defectTypes) {
      const c = d.category || "Other";
      (g[c] = g[c] || []).push(d);
    }
    return g;
  }, [defectTypes]);

  const save = async () => {
    setErr("");
    if (!lineId)   return setErr("Line is required");
    if (!defectId) return setErr("Defect type is required");
    if (!qty || qty < 1) return setErr("Qty must be ≥ 1");
    setSaving(true);
    try {
      await api.post("/api/quality/ncr", {
        line_id:        Number(lineId),
        defect_type_id: Number(defectId),
        part_code:      partCode || null,
        part_name:      partName || null,
        qty_rejected:   Number(qty),
        shift_name:     shift || null,
        notes:          notes || null,
      }, token);
      onSaved();
    } catch (e) {
      setErr(e.message || "Failed to create NCR");
    } finally { setSaving(false); }
  };

  return (
    <ModalShell title="Log New NCR" theme={theme} onClose={onClose}>
      <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:14}}>
        <Field label="Line *">
          <select className="qd-input" style={fullW}
                  value={lineId} onChange={e => setLineId(e.target.value)}>
            <option value="">— select —</option>
            {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
          </select>
        </Field>
        <Field label="Shift">
          <select className="qd-input" style={fullW}
                  value={shift} onChange={e => setShift(e.target.value)}>
            <option value="">— optional —</option>
            <option value="A">A</option>
            <option value="B">B</option>
            <option value="C">C</option>
          </select>
        </Field>
        <Field label="Part Code">
          <input className="qd-input" style={fullW}
                 value={partCode} onChange={e => setPartCode(e.target.value)}
                 placeholder="e.g. 70001-FE100" />
        </Field>
        <Field label="Part Name">
          <input className="qd-input" style={fullW}
                 value={partName} onChange={e => setPartName(e.target.value)}
                 placeholder="e.g. Front Seat Frame" />
        </Field>
        <Field label="Defect Type *">
          <select className="qd-input" style={fullW}
                  value={defectId} onChange={e => setDefectId(e.target.value)}>
            <option value="">— select —</option>
            {Object.entries(grouped).map(([cat, items]) => (
              <optgroup key={cat} label={cat}>
                {items.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
              </optgroup>
            ))}
          </select>
        </Field>
        <Field label="Qty Rejected *">
          <input className="qd-input" style={fullW} type="number" min={1}
                 value={qty} onChange={e => setQty(e.target.value)} />
        </Field>
        <div style={{gridColumn:"span 2"}}>
          <Field label="Notes (optional)">
            <textarea className="qd-input" style={{...fullW, minHeight:80, resize:"vertical"}}
                      value={notes} onChange={e => setNotes(e.target.value)}
                      placeholder="Container ID, supplier batch, immediate containment action, etc." />
          </Field>
        </div>
      </div>

      {err && (
        <div style={{marginTop:14, padding:"9px 12px", borderRadius:7,
                       background:"#fef2f2", color:"#b91c1c", fontSize:12, fontWeight:600}}>
          {err}
        </div>
      )}

      <div style={{display:"flex", justifyContent:"flex-end", gap:10, marginTop:18}}>
        <button className="qd-btn qd-btn-deny"
                style={{padding:"9px 18px", fontSize:12}}
                onClick={onClose} disabled={saving}>
          Cancel
        </button>
        <button className="qd-btn qd-btn-primary"
                style={{padding:"9px 18px", fontSize:12, background: theme.accentDark, color:"#fff"}}
                onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Log NCR"}
        </button>
      </div>
    </ModalShell>
  );
}


/* ─── NCR Close Modal ────────────────────────────────────────────── */
function NCRCloseModal({ token, theme, ncr, onClose, onSaved }) {
  const [rootCause, setRootCause] = useState("");
  const [dispo, setDispo]         = useState("REWORK");
  const [notes, setNotes]         = useState("");
  const [saving, setSaving]       = useState(false);
  const [err, setErr]             = useState("");

  const save = async () => {
    setErr("");
    if (!rootCause.trim()) return setErr("Root cause is required to close");
    setSaving(true);
    try {
      await api.post(`/api/quality/ncr/${ncr.id}/close`, {
        root_cause:  rootCause.trim(),
        disposition: dispo,
        notes:       notes || null,
      }, token);
      onSaved();
    } catch (e) {
      setErr(e.message || "Failed to close NCR");
    } finally { setSaving(false); }
  };

  return (
    <ModalShell title={`Close ${ncr.ncr_number}`} theme={theme} onClose={onClose}>
      {/* Read-only NCR header */}
      <div style={{padding:12, background:"#f8fafc", borderRadius:8, marginBottom:14,
                     border:"1px solid #e2e8f0"}}>
        <div style={{display:"grid", gridTemplateColumns:"repeat(3, 1fr)", gap:10, fontSize:12}}>
          <div>
            <div className="qd-label">Line</div>
            <div style={{fontWeight:700}}>{ncr.line_name || `Line ${ncr.line_id}`}</div>
          </div>
          <div>
            <div className="qd-label">Defect</div>
            <div style={{fontWeight:700}}>{ncr.defect_type_name}</div>
          </div>
          <div>
            <div className="qd-label">Qty Rejected</div>
            <div style={{fontWeight:700, fontFamily:"monospace"}}>{ncr.qty_rejected}</div>
          </div>
          <div>
            <div className="qd-label">Part</div>
            <div style={{fontWeight:600}}>{ncr.part_code || "—"} {ncr.part_name && `· ${ncr.part_name}`}</div>
          </div>
          <div>
            <div className="qd-label">Raised By</div>
            <div style={{fontWeight:600}}>{ncr.raised_by}</div>
          </div>
          <div>
            <div className="qd-label">Raised</div>
            <div style={{fontWeight:600, fontFamily:"monospace"}}>{fmtDt(ncr.raised_at)}</div>
          </div>
        </div>
        {ncr.notes && (
          <div style={{marginTop:10, paddingTop:10, borderTop:"1px solid #e2e8f0",
                         fontSize:11, color:"#475569"}}>
            <b style={{color:"#64748b"}}>Notes from raise:</b> {ncr.notes}
          </div>
        )}
      </div>

      <Field label="Root Cause *">
        <textarea className="qd-input" style={{...fullW, minHeight:90, resize:"vertical"}}
                  value={rootCause} onChange={e => setRootCause(e.target.value)}
                  placeholder="Why did this defect occur? (5-Why summary)" />
      </Field>

      <div style={{marginTop:14}}>
        <Field label="Disposition *">
          <select className="qd-input" style={fullW}
                  value={dispo} onChange={e => setDispo(e.target.value)}>
            <option value="REWORK">Rework — fix and re-inspect</option>
            <option value="SCRAP">Scrap — destroy / dispose</option>
            <option value="ACCEPT_AS_IS">Accept as-is (concession)</option>
            <option value="RETURN_TO_VENDOR">Return to Vendor</option>
          </select>
        </Field>
      </div>

      <div style={{marginTop:14}}>
        <Field label="Closing Notes (optional)">
          <textarea className="qd-input" style={{...fullW, minHeight:60, resize:"vertical"}}
                    value={notes} onChange={e => setNotes(e.target.value)}
                    placeholder="CAPA reference, supplier feedback, etc." />
        </Field>
      </div>

      {err && (
        <div style={{marginTop:14, padding:"9px 12px", borderRadius:7,
                       background:"#fef2f2", color:"#b91c1c", fontSize:12, fontWeight:600}}>
          {err}
        </div>
      )}

      <div style={{display:"flex", justifyContent:"flex-end", gap:10, marginTop:18}}>
        <button className="qd-btn qd-btn-deny"
                style={{padding:"9px 18px", fontSize:12}}
                onClick={onClose} disabled={saving}>
          Cancel
        </button>
        <button className="qd-btn qd-btn-approve"
                style={{padding:"9px 18px", fontSize:12}}
                onClick={save} disabled={saving}>
          {saving ? "Closing…" : "Close NCR"}
        </button>
      </div>
    </ModalShell>
  );
}


/* ─── NCR View Modal (read-only for closed) ─────────────────────── */
function NCRViewModal({ theme, ncr, isAdmin, token, onClose, onVoided }) {
  const [busy, setBusy] = useState(false);
  const voidNcr = async () => {
    if (!confirm(`Void ${ncr.ncr_number}? This excludes it from the Pareto but keeps the audit trail.`)) return;
    setBusy(true);
    try {
      await api.delete(`/api/quality/ncr/${ncr.id}`, token);
      onVoided();
    } catch (e) { alert(e.message); setBusy(false); }
  };

  return (
    <ModalShell title={ncr.ncr_number} theme={theme} onClose={onClose}>
      <div style={{display:"grid", gridTemplateColumns:"repeat(3, 1fr)", gap:14, fontSize:12}}>
        <Field label="Status"><Pill status={ncr.status}/></Field>
        <Field label="Disposition"><Pill status={ncr.disposition}/></Field>
        <Field label="Qty Rejected">
          <div style={{fontWeight:800, fontFamily:"monospace", fontSize:18}}>{ncr.qty_rejected}</div>
        </Field>
        <Field label="Line">
          <div style={{fontWeight:700}}>{ncr.line_name || `Line ${ncr.line_id}`}</div>
        </Field>
        <Field label="Defect">
          <div style={{fontWeight:700}}>{ncr.defect_type_name}</div>
          <div style={{fontSize:10, color:"#94a3b8"}}>{ncr.defect_category}</div>
        </Field>
        <Field label="Part">
          <div style={{fontWeight:600}}>{ncr.part_code || "—"}</div>
          <div style={{fontSize:10, color:"#94a3b8"}}>{ncr.part_name || ""}</div>
        </Field>
        <Field label="Raised">
          <div style={{fontWeight:600, fontFamily:"monospace"}}>{fmtDt(ncr.raised_at)}</div>
          <div style={{fontSize:10, color:"#94a3b8"}}>by {ncr.raised_by}</div>
        </Field>
        <Field label="Closed">
          <div style={{fontWeight:600, fontFamily:"monospace"}}>{fmtDt(ncr.closed_at)}</div>
          <div style={{fontSize:10, color:"#94a3b8"}}>by {ncr.closed_by || "—"}</div>
        </Field>
        <Field label="Shift">
          <div style={{fontWeight:600}}>{ncr.shift_name || "—"}</div>
        </Field>
      </div>

      {ncr.root_cause && (
        <div style={{marginTop:16}}>
          <div className="qd-label">Root Cause</div>
          <div style={{padding:10, background:"#f8fafc", borderRadius:7, border:"1px solid #e2e8f0",
                         fontSize:12, color:"#0f172a", whiteSpace:"pre-wrap"}}>
            {ncr.root_cause}
          </div>
        </div>
      )}

      {ncr.notes && (
        <div style={{marginTop:12}}>
          <div className="qd-label">Notes</div>
          <div style={{padding:10, background:"#f8fafc", borderRadius:7, border:"1px solid #e2e8f0",
                         fontSize:12, color:"#0f172a", whiteSpace:"pre-wrap"}}>
            {ncr.notes}
          </div>
        </div>
      )}

      <div style={{display:"flex", justifyContent:"space-between", marginTop:18}}>
        {isAdmin && ncr.status !== "VOID" ? (
          <button className="qd-btn qd-btn-deny"
                  style={{padding:"9px 18px", fontSize:12}}
                  onClick={voidNcr} disabled={busy}>
            {busy ? "…" : "Void NCR"}
          </button>
        ) : <div />}
        <button className="qd-btn qd-btn-view"
                style={{padding:"9px 18px", fontSize:12}}
                onClick={onClose}>
          Close
        </button>
      </div>
    </ModalShell>
  );
}


/* ─── tiny modal shell + form helpers ─────────────────────────────── */
function ModalShell({ title, theme, onClose, children }) {
  return (
    <div onClick={onClose}
         style={{position:"fixed", inset:0, background:"rgba(15,23,42,.55)",
                  zIndex:200, display:"flex", alignItems:"center", justifyContent:"center",
                  padding:"40px 20px", overflow:"auto"}}>
      <div onClick={e => e.stopPropagation()}
           style={{background:"#fff", borderRadius:12, padding:0,
                    width:"100%", maxWidth:640, boxShadow:"0 20px 60px rgba(0,0,0,.3)",
                    fontFamily:"'Barlow',sans-serif"}}>
        <div style={{padding:"16px 20px", borderBottom:"1px solid #e2e8f0",
                       display:"flex", justifyContent:"space-between", alignItems:"center",
                       background: theme.accentDark, color:"#fff",
                       borderRadius:"12px 12px 0 0"}}>
          <div style={{fontFamily:"'Barlow Condensed',sans-serif", fontSize:22, fontWeight:800,
                         letterSpacing:".02em"}}>
            {title}
          </div>
          <button onClick={onClose}
                  style={{background:"transparent", border:"none", color:"#fff",
                           fontSize:22, fontWeight:700, cursor:"pointer", lineHeight:1}}>
            ×
          </button>
        </div>
        <div style={{padding:20}}>{children}</div>
      </div>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div>
      <div className="qd-label">{label}</div>
      {children}
    </div>
  );
}

const fullW = { width: "100%", boxSizing: "border-box" };


/* ─── In-Process Inspection — Log Measurement Modal ──────────────── */
function InspectionLogModal({ token, theme, lines, chars, onClose, onSaved }) {
  const [lineId, setLineId]   = useState(lines[0]?.id || "");
  // Group chars by part_code for the dropdown
  const [partCode, setPartCode] = useState("");
  const partCodes = useMemo(() => {
    const set = new Set();
    for (const c of chars) if (c.part_code) set.add(c.part_code);
    return Array.from(set).sort();
  }, [chars]);
  const partChars = useMemo(
    () => chars.filter(c => !partCode || c.part_code === partCode),
    [chars, partCode],
  );
  const [charId, setCharId]   = useState(partChars[0]?.id || "");
  const [measured, setMeasured] = useState("");
  const [shift, setShift]     = useState("");
  const [notes, setNotes]     = useState("");
  const [saving, setSaving]   = useState(false);
  const [err, setErr]         = useState("");

  // Keep char selection in sync with current part filter
  useEffect(() => {
    if (partChars.length && !partChars.some(c => c.id === Number(charId))) {
      setCharId(partChars[0].id);
    }
  }, [partChars, charId]);

  const selChar = chars.find(c => c.id === Number(charId));

  const save = async () => {
    setErr("");
    if (!lineId)   return setErr("Line is required");
    if (!charId)   return setErr("Characteristic is required");
    if (measured === "" || isNaN(Number(measured)))
      return setErr("Measured value is required (numeric)");
    setSaving(true);
    try {
      await api.post("/api/quality/inspection-log", {
        line_id:    Number(lineId),
        char_id:    Number(charId),
        measured:   Number(measured),
        shift_name: shift || null,
        notes:      notes || null,
      }, token);
      onSaved();
    } catch (e) {
      setErr(e.message || "Failed to log measurement");
    } finally { setSaving(false); }
  };

  return (
    <ModalShell title="Log Inspection Measurement" theme={theme} onClose={onClose}>
      <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:14}}>
        <Field label="Line *">
          <select className="qd-input" style={fullW}
                  value={lineId} onChange={e => setLineId(e.target.value)}>
            <option value="">— select —</option>
            {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
          </select>
        </Field>
        <Field label="Shift">
          <select className="qd-input" style={fullW}
                  value={shift} onChange={e => setShift(e.target.value)}>
            <option value="">— optional —</option>
            <option value="A">A</option>
            <option value="B">B</option>
            <option value="C">C</option>
          </select>
        </Field>
        <Field label="Part">
          <select className="qd-input" style={fullW}
                  value={partCode} onChange={e => setPartCode(e.target.value)}>
            <option value="">— all parts —</option>
            {partCodes.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </Field>
        <Field label="Characteristic *">
          <select className="qd-input" style={fullW}
                  value={charId} onChange={e => setCharId(e.target.value)}>
            <option value="">— select —</option>
            {partChars.map(c => (
              <option key={c.id} value={c.id}>
                {c.char_name} ({c.part_code})
              </option>
            ))}
          </select>
        </Field>
        <Field label="Measured *">
          <input className="qd-input" style={fullW} type="number" step="any"
                 value={measured} onChange={e => setMeasured(e.target.value)}
                 placeholder={selChar?.target != null ? `target ${selChar.target}` : ""} />
        </Field>
        <Field label="Tolerance / Gauge">
          <div style={{padding:"7px 11px", fontSize:11, color:"#475569",
                         background:"#f8fafc", border:"1px solid #e2e8f0", borderRadius:7,
                         fontFamily:"monospace"}}>
            {selChar
              ? `${selChar.lower_tol ?? "—"} … ${selChar.upper_tol ?? "—"} ${selChar.unit || ""}${selChar.gauge ? ` · ${selChar.gauge}` : ""}`
              : "— select a characteristic —"}
          </div>
        </Field>
        <div style={{gridColumn:"span 2"}}>
          <Field label="Notes (optional)">
            <textarea className="qd-input" style={{...fullW, minHeight:60, resize:"vertical"}}
                      value={notes} onChange={e => setNotes(e.target.value)} />
          </Field>
        </div>
      </div>

      {err && (
        <div style={{marginTop:14, padding:"9px 12px", borderRadius:7,
                       background:"#fef2f2", color:"#b91c1c", fontSize:12, fontWeight:600}}>
          {err}
        </div>
      )}

      <div style={{display:"flex", justifyContent:"flex-end", gap:10, marginTop:18}}>
        <button className="qd-btn qd-btn-deny"
                style={{padding:"9px 18px", fontSize:12}}
                onClick={onClose} disabled={saving}>
          Cancel
        </button>
        <button className="qd-btn qd-btn-primary"
                style={{padding:"9px 18px", fontSize:12, background: theme.accentDark, color:"#fff"}}
                onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save Measurement"}
        </button>
      </div>
    </ModalShell>
  );
}


/* ─── Inspection Characteristics — Admin manage ─────────────────── */
function InspectionCharsModal({ token, theme, chars, onClose, onChanged }) {
  const [addOpen, setAddOpen] = useState(false);
  const [partCode, setPartCode] = useState("");
  const [charName, setCharName] = useState("");
  const [target, setTarget]   = useState("");
  const [lowerTol, setLowerTol] = useState("");
  const [upperTol, setUpperTol] = useState("");
  const [unit, setUnit]       = useState("");
  const [freq, setFreq]       = useState(1);
  const [gauge, setGauge]     = useState("");
  const [saving, setSaving]   = useState(false);
  const [err, setErr]         = useState("");

  const reset = () => {
    setPartCode(""); setCharName(""); setTarget("");
    setLowerTol(""); setUpperTol(""); setUnit("");
    setFreq(1); setGauge(""); setErr("");
  };

  const add = async () => {
    setErr("");
    if (!partCode.trim() || !charName.trim())
      return setErr("Part code and characteristic name required");
    setSaving(true);
    try {
      await api.post("/api/quality/inspection-chars", {
        part_code:  partCode.trim(),
        char_name:  charName.trim(),
        target:     target === ""   ? null : Number(target),
        lower_tol:  lowerTol === "" ? null : Number(lowerTol),
        upper_tol:  upperTol === "" ? null : Number(upperTol),
        unit:       unit || null,
        freq_hours: Math.max(1, Number(freq) || 1),
        gauge:      gauge || null,
      }, token);
      reset(); setAddOpen(false); onChanged();
    } catch (e) {
      setErr(e.message || "Failed to add characteristic");
    } finally { setSaving(false); }
  };

  const toggle = async (c) => {
    try {
      await api.put(`/api/quality/inspection-chars/${c.id}`,
                     { is_active: !c.is_active }, token);
      onChanged();
    } catch (e) { alert(e.message); }
  };

  return (
    <ModalShell title="Manage Inspection Characteristics" theme={theme} onClose={onClose}>
      {!addOpen ? (
        <>
          <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:10}}>
            <div style={{fontSize:12, color:"#64748b"}}>
              {chars.length} characteristic{chars.length !== 1 ? "s" : ""} configured
            </div>
            <button className="qd-btn qd-btn-primary"
                    style={{padding:"8px 14px", fontSize:11, background:theme.accentDark, color:"#fff"}}
                    onClick={() => setAddOpen(true)}>
              + Add New
            </button>
          </div>

          {chars.length === 0 ? (
            <div className="qd-empty">No characteristics yet. Add the first one.</div>
          ) : (
            <div style={{maxHeight:380, overflowY:"auto", border:"1px solid #e2e8f0", borderRadius:8}}>
              <table style={{width:"100%", borderCollapse:"collapse"}}>
                <thead><tr>
                  {["Part", "Characteristic", "Target / Tol", "Unit", "Freq", "Gauge", ""].map(h =>
                    <th key={h} className="qd-th">{h}</th>)}
                </tr></thead>
                <tbody>
                  {chars.map(c => (
                    <tr key={c.id} style={{borderBottom:"1px solid #f1f5f9",
                                            opacity: c.is_active ? 1 : 0.5}}>
                      <td className="qd-td" style={{fontFamily:"monospace"}}>{c.part_code}</td>
                      <td className="qd-td">{c.char_name}</td>
                      <td className="qd-td" style={{fontFamily:"monospace", fontSize:11}}>
                        {c.target ?? "—"} ({c.lower_tol ?? "—"} … {c.upper_tol ?? "—"})
                      </td>
                      <td className="qd-td">{c.unit || "—"}</td>
                      <td className="qd-td" style={{fontFamily:"monospace"}}>{c.freq_hours}h</td>
                      <td className="qd-td">{c.gauge || "—"}</td>
                      <td className="qd-td">
                        <button className="qd-btn qd-btn-view"
                                style={{padding:"4px 10px", fontSize:10}}
                                onClick={() => toggle(c)}>
                          {c.is_active ? "Disable" : "Enable"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div style={{display:"flex", justifyContent:"flex-end", marginTop:14}}>
            <button className="qd-btn qd-btn-view"
                    style={{padding:"9px 18px", fontSize:12}}
                    onClick={onClose}>
              Done
            </button>
          </div>
        </>
      ) : (
        <>
          <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:14}}>
            <Field label="Part Code *">
              <input className="qd-input" style={fullW}
                     value={partCode} onChange={e => setPartCode(e.target.value)}
                     placeholder="e.g. 70001-FE100" />
            </Field>
            <Field label="Characteristic Name *">
              <input className="qd-input" style={fullW}
                     value={charName} onChange={e => setCharName(e.target.value)}
                     placeholder="e.g. Hole Diameter A" />
            </Field>
            <Field label="Target">
              <input className="qd-input" style={fullW} type="number" step="any"
                     value={target} onChange={e => setTarget(e.target.value)} />
            </Field>
            <Field label="Unit">
              <input className="qd-input" style={fullW}
                     value={unit} onChange={e => setUnit(e.target.value)}
                     placeholder="mm / N·m / kg" />
            </Field>
            <Field label="Lower Tolerance">
              <input className="qd-input" style={fullW} type="number" step="any"
                     value={lowerTol} onChange={e => setLowerTol(e.target.value)} />
            </Field>
            <Field label="Upper Tolerance">
              <input className="qd-input" style={fullW} type="number" step="any"
                     value={upperTol} onChange={e => setUpperTol(e.target.value)} />
            </Field>
            <Field label="Frequency (hours)">
              <input className="qd-input" style={fullW} type="number" min={1}
                     value={freq} onChange={e => setFreq(e.target.value)} />
            </Field>
            <Field label="Gauge / Instrument">
              <input className="qd-input" style={fullW}
                     value={gauge} onChange={e => setGauge(e.target.value)}
                     placeholder="e.g. Vernier 0-150" />
            </Field>
          </div>

          {err && (
            <div style={{marginTop:14, padding:"9px 12px", borderRadius:7,
                           background:"#fef2f2", color:"#b91c1c", fontSize:12, fontWeight:600}}>
              {err}
            </div>
          )}

          <div style={{display:"flex", justifyContent:"flex-end", gap:10, marginTop:18}}>
            <button className="qd-btn qd-btn-deny"
                    style={{padding:"9px 18px", fontSize:12}}
                    onClick={() => { reset(); setAddOpen(false); }} disabled={saving}>
              Back
            </button>
            <button className="qd-btn qd-btn-primary"
                    style={{padding:"9px 18px", fontSize:12,
                             background: theme.accentDark, color:"#fff"}}
                    onClick={add} disabled={saving}>
              {saving ? "Saving…" : "Add Characteristic"}
            </button>
          </div>
        </>
      )}
    </ModalShell>
  );
}


/* ─── First/Last Piece create ───────────────────────────────────── */
function FLPCreateModal({ token, theme, lines, onClose, onSaved }) {
  const [lineId, setLineId]     = useState(lines[0]?.id || "");
  const [partCode, setPartCode] = useState("");
  const [partName, setPartName] = useState("");
  const [model, setModel]       = useState("");
  const [pieceType, setPieceType] = useState("FIRST");
  const [status, setStatus]     = useState("OK");
  const [shift, setShift]       = useState("");
  const [notes, setNotes]       = useState("");
  // Free-form characteristic checklist — operator types `name: value` per line
  const [charsText, setCharsText] = useState("");
  const [saving, setSaving]     = useState(false);
  const [err, setErr]           = useState("");

  const save = async () => {
    setErr("");
    if (!lineId) return setErr("Line is required");
    const checked = {};
    charsText.split("\n").forEach(line => {
      const [k, ...rest] = line.split(":");
      const key = (k || "").trim();
      if (key) checked[key] = rest.join(":").trim();
    });
    setSaving(true);
    try {
      await api.post("/api/quality/first-last-piece", {
        line_id:       Number(lineId),
        part_code:     partCode || null,
        part_name:     partName || null,
        model:         model || null,
        piece_type:    pieceType,
        status,
        shift_name:    shift || null,
        checked_chars: checked,
        notes:         notes || null,
      }, token);
      onSaved();
    } catch (e) {
      setErr(e.message || "Failed to save");
    } finally { setSaving(false); }
  };

  return (
    <ModalShell title="Log First / Last Piece Check" theme={theme} onClose={onClose}>
      <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:14}}>
        <Field label="Line *">
          <select className="qd-input" style={fullW}
                  value={lineId} onChange={e => setLineId(e.target.value)}>
            <option value="">— select —</option>
            {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
          </select>
        </Field>
        <Field label="Piece Type *">
          <select className="qd-input" style={fullW}
                  value={pieceType} onChange={e => setPieceType(e.target.value)}>
            <option value="FIRST">FIRST piece (start of model)</option>
            <option value="LAST">LAST piece (end of model)</option>
          </select>
        </Field>
        <Field label="Part Code">
          <input className="qd-input" style={fullW}
                 value={partCode} onChange={e => setPartCode(e.target.value)} />
        </Field>
        <Field label="Part Name">
          <input className="qd-input" style={fullW}
                 value={partName} onChange={e => setPartName(e.target.value)} />
        </Field>
        <Field label="Model">
          <input className="qd-input" style={fullW}
                 value={model} onChange={e => setModel(e.target.value)} />
        </Field>
        <Field label="Shift">
          <select className="qd-input" style={fullW}
                  value={shift} onChange={e => setShift(e.target.value)}>
            <option value="">— optional —</option>
            <option value="A">A</option>
            <option value="B">B</option>
            <option value="C">C</option>
          </select>
        </Field>
        <Field label="Overall Status *">
          <select className="qd-input" style={fullW}
                  value={status} onChange={e => setStatus(e.target.value)}>
            <option value="OK">OK — all characteristics within spec</option>
            <option value="NG">NG — at least one out of spec</option>
          </select>
        </Field>
        <div />
        <div style={{gridColumn:"span 2"}}>
          <Field label="Checked Characteristics (one per line, format `name: value`)">
            <textarea className="qd-input" style={{...fullW, minHeight:90, resize:"vertical",
                                                     fontFamily:"monospace"}}
                      value={charsText} onChange={e => setCharsText(e.target.value)}
                      placeholder={"Hole dia A: 10.05 mm\nTorque: 23 N·m\nWeld: OK"} />
          </Field>
        </div>
        <div style={{gridColumn:"span 2"}}>
          <Field label="Notes (optional)">
            <textarea className="qd-input" style={{...fullW, minHeight:50, resize:"vertical"}}
                      value={notes} onChange={e => setNotes(e.target.value)} />
          </Field>
        </div>
      </div>

      {err && (
        <div style={{marginTop:14, padding:"9px 12px", borderRadius:7,
                       background:"#fef2f2", color:"#b91c1c", fontSize:12, fontWeight:600}}>
          {err}
        </div>
      )}

      <div style={{display:"flex", justifyContent:"flex-end", gap:10, marginTop:18}}>
        <button className="qd-btn qd-btn-deny"
                style={{padding:"9px 18px", fontSize:12}}
                onClick={onClose} disabled={saving}>
          Cancel
        </button>
        <button className="qd-btn qd-btn-primary"
                style={{padding:"9px 18px", fontSize:12,
                         background: theme.accentDark, color:"#fff"}}
                onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save Check"}
        </button>
      </div>
    </ModalShell>
  );
}


/* ─── Control Plan upload modal ─────────────────────────────────── */
function ControlPlanUploadModal({ token, theme, onClose, onSaved }) {
  const [partCode, setPartCode] = useState("");
  const [partName, setPartName] = useState("");
  const [version, setVersion]   = useState("");
  const [notes, setNotes]       = useState("");
  const [file, setFile]         = useState(null);
  const [saving, setSaving]     = useState(false);
  const [err, setErr]           = useState("");

  const save = async () => {
    setErr("");
    if (!partCode.trim()) return setErr("Part code is required");
    if (!file)            return setErr("Please choose a PDF file");
    if (file.size > 20 * 1024 * 1024) return setErr("File must be ≤ 20 MB");
    setSaving(true);
    try {
      const fd = new FormData();
      fd.append("part_code", partCode.trim());
      if (partName) fd.append("part_name", partName);
      if (version)  fd.append("version", version);
      if (notes)    fd.append("notes", notes);
      fd.append("file", file);
      const r = await fetch("/api/quality/control-plans", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      if (!r.ok) {
        let d = `HTTP ${r.status}`;
        try { const j = await r.json(); d = j.detail || d; } catch {}
        throw new Error(d);
      }
      onSaved();
    } catch (e) {
      setErr(e.message || "Upload failed");
    } finally { setSaving(false); }
  };

  return (
    <ModalShell title="Upload Control Plan" theme={theme} onClose={onClose}>
      <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:14}}>
        <Field label="Part Code *">
          <input className="qd-input" style={fullW}
                 value={partCode} onChange={e => setPartCode(e.target.value)}
                 placeholder="e.g. 70001-FE100" />
        </Field>
        <Field label="Part Name">
          <input className="qd-input" style={fullW}
                 value={partName} onChange={e => setPartName(e.target.value)} />
        </Field>
        <Field label="Version">
          <input className="qd-input" style={fullW}
                 value={version} onChange={e => setVersion(e.target.value)}
                 placeholder="e.g. Rev 03 / 2026-05" />
        </Field>
        <Field label="PDF File *">
          <input className="qd-input" style={fullW} type="file"
                 accept=".pdf,application/pdf"
                 onChange={e => setFile(e.target.files?.[0] || null)} />
        </Field>
        <div style={{gridColumn:"span 2"}}>
          <Field label="Notes (optional)">
            <textarea className="qd-input" style={{...fullW, minHeight:60, resize:"vertical"}}
                      value={notes} onChange={e => setNotes(e.target.value)} />
          </Field>
        </div>
      </div>

      <div style={{marginTop:10, fontSize:11, color:"#64748b"}}>
        Uploading a new file automatically deactivates any prior active version for this part code.
      </div>

      {err && (
        <div style={{marginTop:14, padding:"9px 12px", borderRadius:7,
                       background:"#fef2f2", color:"#b91c1c", fontSize:12, fontWeight:600}}>
          {err}
        </div>
      )}

      <div style={{display:"flex", justifyContent:"flex-end", gap:10, marginTop:18}}>
        <button className="qd-btn qd-btn-deny"
                style={{padding:"9px 18px", fontSize:12}}
                onClick={onClose} disabled={saving}>
          Cancel
        </button>
        <button className="qd-btn qd-btn-primary"
                style={{padding:"9px 18px", fontSize:12,
                         background: theme.accentDark, color:"#fff"}}
                onClick={save} disabled={saving}>
          {saving ? "Uploading…" : "Upload"}
        </button>
      </div>
    </ModalShell>
  );
}
