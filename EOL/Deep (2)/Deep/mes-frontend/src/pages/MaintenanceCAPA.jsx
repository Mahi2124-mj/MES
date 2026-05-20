/* ───────────────────────────────────────────────────────────────────
 * MaintenanceCAPA.jsx
 * ───────────────────────────────────────────────────────────────────
 * CAPA (Corrective Action / Preventive Action) for Maintenance.
 *
 * Layout:
 *   • KPI tiles (open / pending / closed-this-month)
 *   • "Pending CAPA" — auto-detected breaches needing a new CAPA, with
 *     "Start CAPA" buttons that open the form pre-filled with the
 *     trigger context (machine, threshold, breach value).
 *   • Filter bar
 *   • CAPA archive table (filterable) — every filing with View / Edit
 *     buttons.  Maintenance can edit until status='CLOSED'.
 *
 * Form: 8D-ish default template.  Admin can refine when the actual
 * plant template is shared.
 *
 * Trigger kinds:
 *   SINGLE_LIMIT  : a single breakdown crossed `single_breakdown_minutes_limit`.
 *   MONTHLY_LIMIT : machine's month-to-date sum crossed `monthly_sum_minutes_limit`.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";

const API = "";
const api = {
  async get(path, token) {
    const r = await fetch(API + path, { headers: { Authorization: `Bearer ${token}` } });
    if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
    return r.json();
  },
  async post(path, body, token) {
    const r = await fetch(API + path, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
    return r.json();
  },
  async put(path, body, token) {
    const r = await fetch(API + path, {
      method: "PUT",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
    return r.json();
  },
};

function fmtDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-IN", {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return "—"; }
}

const STATUS_BADGE = {
  OPEN:        { bg:"rgba(220,38,38,.10)", color:"#dc2626", label:"Open" },
  IN_PROGRESS: { bg:"rgba(217,119,6,.10)", color:"#b45309", label:"In Progress" },
  CLOSED:      { bg:"rgba(22,163,74,.10)", color:"#15803d", label:"Closed" },
};
function StatusBadge({ status }) {
  const m = STATUS_BADGE[status] || { bg:"#f1f5f9", color:"#64748b", label: status };
  return (
    <span style={{ padding:"2px 9px", borderRadius:99, fontSize:10, fontWeight:700,
                    background: m.bg, color: m.color, whiteSpace:"nowrap" }}>
      {m.label}
    </span>
  );
}

function Tile({ label, value, sub, color = "#1e40af" }) {
  return (
    <div style={{
      background:"#fff", border:"1px solid #e2e8f0", borderRadius:12,
      padding:"14px 18px", minWidth:160, flex:"0 0 auto",
      boxShadow:"0 1px 3px rgba(0,0,0,.04)",
    }}>
      <div style={{ fontSize:10, color:"#64748b", fontWeight:700,
                     letterSpacing:".08em", textTransform:"uppercase" }}>{label}</div>
      <div style={{ fontSize:26, fontWeight:800, color, marginTop:2,
                     fontFamily:"'Barlow Condensed',sans-serif" }}>{value}</div>
      {sub && <div style={{ fontSize:11, color:"#94a3b8", marginTop:1 }}>{sub}</div>}
    </div>
  );
}

// Toyota Boshoku QPR (Quality Problem Report) layout — replicated from
// the official QPR FORMAT.xlsx the plant uses on paper.  The form is
// stored as one structured JSONB blob (mes_capa.capa_data) so we can
// replay it 1:1 in view mode and print A4 hard-copies.
const DETECTED_AT_BOXES = [
  "Assembly", "SQA", "Weld Shop", "Customer",
  "Maintenance", "Store", "Logistics", "NPD/Engg.", "Others",
];
const STOCK_SORT_LOCATIONS = [
  "Customer PDI", "Customer Assy line", "Customer Stores",
  "Transit to Customer", "Bonded", "Assembly",
  "Main Stores", "Receiving Stores", "Supplier",
  "Transit from Supplier",
];
const STANDARDIZATION_DOCS = [
  "Control Plan", "PFMEA", "OS/WI", "MIS-P", "Poka Yoke List",
  "Drawings", "Process Check Sheet", "Master samples",
  "Horizontal deployment actions", "Risk Register ( System )",
  "Others, pl. specify", "PMC",
];

// Required fields gate the "Save & Close" button — keep this list in
// sync with the visual ones marked with a red asterisk in the form.
const QPR_REQUIRED_KEYS = [
  "qpr_no", "qpr_date", "reported_problem",
  "interim_containment",
  "analysis_start_date",
  "team_members",
  "analysis_completion_date",
];

// Build the empty QPR structure when the user opens a fresh form.  When
// editing an existing CAPA we merge any persisted values on top so old
// drafts open with whatever was already saved.
function buildEmptyQPR(prev = {}) {
  const seed = (count, item) => Array.from({ length: count }, () => ({ ...item }));
  return {
    // ── Header ─────────────────────────────────────────────────
    qpr_no:                 prev.qpr_no                 || "",
    qpr_date:               prev.qpr_date               || "",
    reporting_time:         prev.reporting_time         || "",
    detected_at:            prev.detected_at            || {},
    oem_customer:           prev.oem_customer           || "",
    product_name:           prev.product_name           || "",
    product_no:             prev.product_no             || "",
    part_name:              prev.part_name              || "",
    part_no:                prev.part_no                || "",
    model:                  prev.model                  || "",
    rejected_batch:         prev.rejected_batch         || "",
    given_for_analysis:     prev.given_for_analysis     || "",
    is_repeated:            prev.is_repeated            || "",
    qty_rejected:           prev.qty_rejected           || "",
    qpr_raised_by:          prev.qpr_raised_by          || "",
    raised_dept:            prev.raised_dept            || "",
    raised_date:            prev.raised_date            || "",
    raised_sign:            prev.raised_sign            || "",
    qpr_recd_by:            prev.qpr_recd_by            || "",
    recd_dept:              prev.recd_dept              || "",
    recd_date:              prev.recd_date              || "",
    recd_sign:              prev.recd_sign              || "",
    recommended_reply_date: prev.recommended_reply_date || "",
    // ── Reported / Defined problem ─────────────────────────────
    reported_problem:       prev.reported_problem       || "",
    fivew2h:                prev.fivew2h                || { what:"", where:"", how:"", when:"", who:"", why:"", how_much:"" },
    // ── Containment (3 hours) ─────────────────────────────────
    defect_confirmation:    prev.defect_confirmation    || "",
    sketch:                 prev.sketch                 || "",
    interim_containment:    prev.interim_containment    || "",
    notification:           prev.notification           || { required:"", responsibility:"", target_date:"", impl_date:"" },
    stock_sort:             Array.isArray(prev.stock_sort) && prev.stock_sort.length === STOCK_SORT_LOCATIONS.length
                              ? prev.stock_sort
                              : STOCK_SORT_LOCATIONS.map(loc => ({ location: loc, date:"", resp:"", qty_checked:"", b_code:"", ok:"", ng:"", id_mark:"", remarks:"" })),
    // ── Corrective (3 days) ───────────────────────────────────
    analysis_start_date:    prev.analysis_start_date    || "",
    fishbone:               prev.fishbone               || { man:"", machine:"", environment:"", abnormality:"", material:"", method:"", measurement:"" },
    team_members:           prev.team_members           || "",
    data_validation:        Array.isArray(prev.data_validation) && prev.data_validation.length
                              ? prev.data_validation
                              : seed(6, { cause:"", method:"", result:"", remarks:"" }),
    // ── Root cause (Why-Why) ──────────────────────────────────
    root_occurrence:        prev.root_occurrence        || { w1:"", w2:"", w3:"", w4:"", w5:"", w6:"" },
    root_flowout:           prev.root_flowout           || { w1:"", w2:"", w3:"", w4:"", w5:"", remarks:"" },
    // ── Countermeasure ────────────────────────────────────────
    analysis_completion_date: prev.analysis_completion_date || "",
    cm_occurrence:          Array.isArray(prev.cm_occurrence) && prev.cm_occurrence.length
                              ? prev.cm_occurrence
                              : seed(5, { action:"", resp:"", tgt:"", impl:"", batch:"" }),
    cm_flowout:             Array.isArray(prev.cm_flowout) && prev.cm_flowout.length
                              ? prev.cm_flowout
                              : seed(5, { action:"", resp:"", tgt:"", impl:"", batch:"" }),
    // ── Horizontal deployment (3 weeks) ───────────────────────
    horizontal_deployment:  Array.isArray(prev.horizontal_deployment) && prev.horizontal_deployment.length
                              ? prev.horizontal_deployment
                              : seed(5, { action:"", resp:"", tgt:"", impl:"", remarks:"" }),
    effectiveness:          prev.effectiveness          || {
                              wk1: { qty:"", date:"", status:"", sign:"" },
                              wk2: { qty:"", date:"", status:"", sign:"" },
                              wk3: { qty:"", date:"", status:"", sign:"" },
                              remarks: "",
                            },
    // ── Standardization check ─────────────────────────────────
    standardization_check: Array.isArray(prev.standardization_check) && prev.standardization_check.length === STANDARDIZATION_DOCS.length
                              ? prev.standardization_check
                              : STANDARDIZATION_DOCS.map(doc => ({ activity:doc, reviewed:"", revision_required:"", revision_details:"", remarks:"" })),
    // ── Sign off ──────────────────────────────────────────────
    sign_off:              prev.sign_off               || {
                              prepared_by:"",   verified_by:"",   approved_by:"",
                              prepared_date:"", verified_date:"", approved_date:"",
                              prepared_sign:"", verified_sign:"", approved_sign:"",
                            },
  };
}

export default function MaintenanceCAPA() {
  const { token, theme, isAdmin, user } = useAuth();
  const [pending, setPending] = useState({ single_limit_breaches: [], monthly_limit_breaches: [], month_year: "" });
  const [list,    setList]    = useState([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("");
  const [editing, setEditing] = useState(null);   // capa row being edited / created
  // Tab state — "pareto" (chart + breaches) or "qpr" (filings archive).
  // URL hash persists across reloads so admin landing on a deep link
  // doesn't snap back to default.
  const [activeTab, setActiveTab] = useState(() => {
    const h = (typeof window !== "undefined" ? window.location.hash : "").replace(/^#/, "");
    return ["pareto", "qpr"].includes(h) ? h : "pareto";
  });
  useEffect(() => {
    const want = "#" + activeTab;
    if (window.location.hash !== want) {
      window.history.replaceState(null, "", window.location.pathname + want);
    }
  }, [activeTab]);

  useEffect(() => {
    document.title = isAdmin ? "Maintenance CAPA" : "CAPA";
  }, [isAdmin]);

  const reload = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const q = new URLSearchParams({ days: "365" });
      if (statusFilter) q.set("status", statusFilter);
      const [p, l] = await Promise.all([
        api.get("/api/capa/pending", token).catch(() => ({})),
        api.get(`/api/capa/?${q.toString()}`, token).catch(() => []),
      ]);
      setPending({
        single_limit_breaches:  Array.isArray(p.single_limit_breaches)  ? p.single_limit_breaches  : [],
        monthly_limit_breaches: Array.isArray(p.monthly_limit_breaches) ? p.monthly_limit_breaches : [],
        month_year:             p.month_year || "",
      });
      setList(Array.isArray(l) ? l : []);
    } finally { setLoading(false); }
  }, [token, statusFilter]);
  useEffect(() => { reload(); }, [reload]);

  // KPI tile values
  const tiles = useMemo(() => {
    const open    = list.filter(c => c.status === "OPEN").length;
    const inProg  = list.filter(c => c.status === "IN_PROGRESS").length;
    const closed  = list.filter(c => c.status === "CLOSED").length;
    const pendingTotal = pending.single_limit_breaches.length + pending.monthly_limit_breaches.length;
    return { open, inProg, closed, pendingTotal };
  }, [list, pending]);

  // ── Open the form for a NEW CAPA (from a pending breach row) ──
  const startFromSingle = (b) => setEditing({
    _new: true,
    trigger_kind:           "SINGLE_LIMIT",
    breakdown_id:           b.breakdown_id,
    trigger_value_minutes:  b.trigger_value_minutes,
    threshold_minutes:      b.threshold_minutes,
    line_id:                b.line_id,
    line_name:              b.line_name,
    zone_id:                b.zone_id,
    zone_name:              b.zone_name,
    machine_no:             b.machine_no,
    machine_name:           b.machine_name,
    capa_data:              {},
  });
  const startFromMonthly = (b) => setEditing({
    _new: true,
    trigger_kind:           "MONTHLY_LIMIT",
    trigger_value_minutes:  b.trigger_value_minutes,
    threshold_minutes:      b.threshold_minutes,
    line_id:                b.line_id,
    line_name:              b.line_name,
    zone_id:                b.zone_id,
    zone_name:              b.zone_name,
    machine_no:             b.machine_no,
    machine_name:           b.machine_name,
    month_year:             b.month_year,
    capa_data:              {},
  });

  // ── Open the form for an EXISTING CAPA (from the archive) ─────
  const openExisting = (c) => setEditing({ ...c, _new: false });

  // ── Open a fresh, blank QPR form with no trigger context.  Used
  //    when admin / maintenance wants to file a QPR independent of
  //    any auto-detected breakdown threshold breach (e.g. a quality
  //    issue raised by the customer).
  const startBlankQPR = () => setEditing({
    _new: true,
    trigger_kind:           "MANUAL",
    trigger_value_minutes:  null,
    threshold_minutes:      null,
    line_id:                null,
    line_name:              "",
    zone_id:                null,
    zone_name:              "",
    machine_no:             "",
    machine_name:           "",
    capa_data:              {},
  });

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@600;700;800&display=swap');
        .ca-root { min-height:100vh; background:#f8fafc; font-family:'Barlow',sans-serif; padding-bottom:60px; }
        .ca-topbar {
          background:#fff; border-bottom:1px solid #e2e8f0;
          padding:0 40px 0 88px; height:60px;
          display:flex; align-items:center; justify-content:space-between;
          position:sticky; top:0; z-index:100;
          box-shadow:0 1px 3px rgba(0,0,0,.06);
        }
        .ca-topbar::after { content:''; position:absolute; bottom:0; left:0; right:0;
                             height:2px; background:${theme.gradient}; }
        .ca-title { position:absolute; left:50%; transform:translateX(-50%);
                     font-family:'Barlow Condensed',sans-serif;
                     font-size:34px; font-weight:800; color:#0f172a;
                     letter-spacing:-.01em; pointer-events:none; white-space:nowrap; }
        .ca-title span { color:${theme.accent}; }
        .ca-user-pill {
          display:flex; align-items:center; gap:10px;
          padding:6px 14px; border-radius:99px;
          border:1.5px solid #e2e8f0; background:#f8fafc;
          font-size:12px; font-weight:600; color:#334155; white-space:nowrap;
        }
        .ca-user-pill b { color:#0f172a; font-weight:800; }
        .ca-body { padding:24px 40px 0; max-width:1280px; margin:0 auto; }
        .ca-tiles { display:flex; gap:14px; flex-wrap:wrap; margin-bottom:18px; }
        .ca-section { margin-bottom:22px; }
        .ca-section h3 { margin:0 0 10px; font-family:'Barlow Condensed',sans-serif;
                          font-size:18px; font-weight:800; color:#0f172a;
                          letter-spacing:.02em; text-transform:uppercase; }
        .ca-card { background:#fff; border:1px solid #e2e8f0; border-radius:12px;
                    box-shadow:0 1px 3px rgba(0,0,0,.04); overflow:hidden; }
        .ca-pending {
          background:linear-gradient(135deg, rgba(220,38,38,.06), rgba(234,88,12,.04));
          border:1px solid rgba(220,38,38,.20);
        }
        .ca-input { padding:7px 11px; border-radius:8px; border:1.5px solid #e2e8f0;
                     font-size:13px; font-family:inherit; background:#fff;
                     color:#0f172a; outline:none; }
        .ca-th { padding:10px 14px; text-align:left; font-size:10px; font-weight:700;
                  letter-spacing:.08em; text-transform:uppercase; color:#64748b;
                  border-bottom:2px solid #e2e8f0; white-space:nowrap; }
        .ca-td { padding:11px 14px; font-size:12px; color:#0f172a; vertical-align:middle; }
        .ca-btn-primary { background: ${theme.accent}; color:#fff; border:none;
                          padding:7px 13px; border-radius:8px; font-weight:700;
                          font-size:12px; cursor:pointer; white-space:nowrap; }
        .ca-btn-primary:hover { filter: brightness(1.1); }
        .ca-btn-ghost { background:#fff; color: ${theme.accent}; border:1.5px solid ${theme.accent};
                        padding:5px 12px; border-radius:7px; font-weight:700;
                        font-size:11px; cursor:pointer; }
      `}</style>

      <div className="ca-root">
        <div className="ca-topbar">
          <div /> {/* logo placeholder */}
          <div className="ca-title">
            {isAdmin ? "Maintenance " : ""}<span>CAPA</span>
          </div>
          {user?.username && (
            <div className="ca-user-pill">
              Signed in as <b>{user.username}</b>
            </div>
          )}
        </div>

        <div className="ca-body">

          {/* ── Tab bar (Pareto chart + auto-mandate vs QPR archive) ── */}
          <div style={{display:"flex", gap:0, marginBottom:18, background:"#fff",
                        borderRadius:"12px 12px 0 0", border:"1px solid #e2e8f0",
                        borderBottom:"2px solid #e2e8f0", overflow:"hidden"}}>
            {[
              {key:"pareto", label:"📊 Pareto (Auto-Mandate)"},
              {key:"qpr",    label:"📋 QPR Filings"},
            ].map(t => (
              <button key={t.key} onClick={() => setActiveTab(t.key)}
                      style={{flex:1, padding:"13px 20px", fontFamily:"'Barlow',sans-serif",
                              fontSize:14, fontWeight:700, cursor:"pointer", border:"none",
                              background: activeTab === t.key ? theme.accentDark : "#fff",
                              color: activeTab === t.key ? "#fff" : "#64748b",
                              transition:"all .15s"}}>
                {t.label}
              </button>
            ))}
          </div>

          {/* ── PARETO TAB ──────────────────────────────── */}
          {activeTab === "pareto" && <>

          {/* ── KPI tiles ─────────────────────────────────── */}
          <div className="ca-tiles" style={{display:"flex", alignItems:"center", flexWrap:"wrap"}}>
            <Tile label="Pending"      value={tiles.pendingTotal} sub="machines need CAPA" color="#dc2626"/>
            <Tile label="Open CAPAs"   value={tiles.open}                                  color="#b45309"/>
            <Tile label="In Progress"  value={tiles.inProg}                                color="#0891b2"/>
            <Tile label="Closed"       value={tiles.closed}        sub="last 365 days"     color="#16a34a"/>
            {pending.month_year && (
              <Tile label="Window" value={pending.month_year} sub="month-to-date" color="#7c3aed"/>
            )}
            <div style={{flex:"1 1 auto"}}/>
            {/* Manual QPR launcher — always visible so the user doesn't
                have to wait for a threshold-breach trigger to file one.  */}
            <button onClick={startBlankQPR}
                    style={{ padding:"12px 22px", borderRadius:10, border:"none",
                             background:`linear-gradient(135deg,${theme.accentDark},${theme.accent})`,
                             color:"#fff", fontWeight:800, fontSize:13, cursor:"pointer",
                             boxShadow:`0 4px 14px ${theme.soft}`, whiteSpace:"nowrap" }}>
              + New QPR
            </button>
          </div>

          {/* ── Pending CAPA (auto-detected breaches) ──────── */}
          {(pending.single_limit_breaches.length > 0 || pending.monthly_limit_breaches.length > 0) && (
            <div className="ca-section">
              <h3>🚨 Pending CAPA — threshold breaches</h3>

              {/* Single-limit breaches */}
              {pending.single_limit_breaches.length > 0 && (
                <div className="ca-card ca-pending" style={{ marginBottom: 12 }}>
                  <div style={{ padding:"10px 14px", borderBottom:"1px solid rgba(220,38,38,.15)",
                                  fontWeight:700, fontSize:12, color:"#991b1b" }}>
                    Single-breakdown limit exceeded ({pending.single_limit_breaches.length})
                  </div>
                  <div style={{ overflowX:"auto" }}>
                    <table style={{ width:"100%", borderCollapse:"collapse" }}>
                      <thead>
                        <tr>{["Line", "Zone", "Machine", "Started", "Duration", "Limit", ""].map(h =>
                          <th key={h} className="ca-th">{h}</th>)}</tr>
                      </thead>
                      <tbody>
                        {pending.single_limit_breaches.map(b => (
                          <tr key={b.breakdown_id} style={{ borderBottom:"1px solid #fef3f2" }}>
                            <td className="ca-td" style={{ fontWeight:700 }}>{b.line_name || `Line ${b.line_id}`}</td>
                            <td className="ca-td">{b.zone_name || "—"}</td>
                            <td className="ca-td" style={{ fontFamily:"monospace" }}>
                              #{b.machine_no} · {b.machine_name || "—"}
                            </td>
                            <td className="ca-td" style={{ fontFamily:"monospace" }}>{fmtDate(b.started_at)}</td>
                            <td className="ca-td" style={{ fontFamily:"monospace", fontWeight:700, color:"#dc2626" }}>
                              {b.dur_min} min
                            </td>
                            <td className="ca-td" style={{ fontFamily:"monospace", color:"#94a3b8" }}>
                              {b.threshold_minutes} min
                            </td>
                            <td className="ca-td">
                              <button className="ca-btn-primary" onClick={() => startFromSingle(b)}>
                                Start CAPA
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Monthly-limit breaches */}
              {pending.monthly_limit_breaches.length > 0 && (
                <div className="ca-card ca-pending">
                  <div style={{ padding:"10px 14px", borderBottom:"1px solid rgba(220,38,38,.15)",
                                  fontWeight:700, fontSize:12, color:"#991b1b" }}>
                    Monthly-sum limit exceeded ({pending.monthly_limit_breaches.length})
                    {pending.month_year && <span style={{ marginLeft:8, fontWeight:500, color:"#64748b" }}>· {pending.month_year}</span>}
                  </div>
                  <div style={{ overflowX:"auto" }}>
                    <table style={{ width:"100%", borderCollapse:"collapse" }}>
                      <thead>
                        <tr>{["Line", "Zone", "Machine", "Events (mtd)", "Sum (min)", "Limit", ""].map(h =>
                          <th key={h} className="ca-th">{h}</th>)}</tr>
                      </thead>
                      <tbody>
                        {pending.monthly_limit_breaches.map((b, i) => (
                          <tr key={`${b.line_id}-${b.machine_no}-${i}`} style={{ borderBottom:"1px solid #fef3f2" }}>
                            <td className="ca-td" style={{ fontWeight:700 }}>{b.line_name || `Line ${b.line_id}`}</td>
                            <td className="ca-td">{b.zone_name || "—"}</td>
                            <td className="ca-td" style={{ fontFamily:"monospace" }}>
                              #{b.machine_no} · {b.machine_name || "—"}
                            </td>
                            <td className="ca-td" style={{ fontFamily:"monospace" }}>{b.event_count}</td>
                            <td className="ca-td" style={{ fontFamily:"monospace", fontWeight:700, color:"#dc2626" }}>
                              {b.sum_min} min
                            </td>
                            <td className="ca-td" style={{ fontFamily:"monospace", color:"#94a3b8" }}>
                              {b.threshold_minutes} min
                            </td>
                            <td className="ca-td">
                              <button className="ca-btn-primary" onClick={() => startFromMonthly(b)}>
                                Start CAPA
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ── Pareto chart — zone-wise breakdown ────────── */}
          <ParetoChart
            token={token}
            theme={theme}
            isAdmin={isAdmin}
            onStartCapa={(m) => setEditing({
              _new: true,
              trigger_kind:           "PARETO",
              line_id:                m.line_id,
              line_name:              m.line_name,
              zone_id:                m.zone_id,
              zone_name:              m.zone_name,
              machine_no:             m.machine_no,
              machine_name:           m.machine_name,
              trigger_value_minutes:  m.breakdown_minutes,
              threshold_minutes:      m.threshold_minutes,
              month_year:             m.month_year,
              capa_data:              {},
            })}
          />
          </>}

          {/* ── QPR FILINGS TAB ─────────────────────────── */}
          {activeTab === "qpr" && <>

          {/* ── Filter ──────────────────────────────────── */}
          <div style={{ display:"flex", gap:10, alignItems:"center", marginBottom: 14 }}>
            <select className="ca-input" value={statusFilter}
                    onChange={e => setStatusFilter(e.target.value)}>
              <option value="">All statuses</option>
              <option value="OPEN">Open</option>
              <option value="IN_PROGRESS">In Progress</option>
              <option value="CLOSED">Closed</option>
            </select>
            <span style={{ fontSize:11, color:"#94a3b8" }}>last 365 days · {list.length} filing(s)</span>
            <div style={{flex:"1 1 auto"}}/>
            <button onClick={startBlankQPR}
                    style={{ padding:"9px 18px", borderRadius:8, border:"none",
                             background:`linear-gradient(135deg,${theme.accentDark},${theme.accent})`,
                             color:"#fff", fontWeight:800, fontSize:12, cursor:"pointer",
                             boxShadow:`0 4px 14px ${theme.soft}` }}>
              + New QPR
            </button>
          </div>

          {/* ── CAPA archive ─────────────────────────────── */}
          <div className="ca-section">
            <h3>QPR Filings</h3>
            <div className="ca-card">
              {loading ? (
                <div style={{ padding:60, textAlign:"center", color:"#94a3b8" }}>Loading…</div>
              ) : list.length === 0 ? (
                <div style={{ padding:60, textAlign:"center", color:"#94a3b8", fontStyle:"italic" }}>
                  No CAPAs filed yet.  Start one from a Pending row above when a threshold gets crossed.
                </div>
              ) : (
                <div style={{ overflowX:"auto" }}>
                  <table style={{ width:"100%", borderCollapse:"collapse" }}>
                    <thead>
                      <tr>
                        {["#", "Trigger", "Line", "Machine", "Value/Limit", "Opened", "Status", ""].map(h =>
                          <th key={h} className="ca-th">{h}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {list.map(c => (
                        <tr key={c.id} style={{ borderBottom:"1px solid #f1f5f9" }}>
                          <td className="ca-td" style={{ fontFamily:"monospace", color:"#94a3b8" }}>#{c.id}</td>
                          <td className="ca-td">
                            <span style={{ padding:"2px 9px", borderRadius:99, fontSize:10, fontWeight:700,
                                            background: c.trigger_kind === "MONTHLY_LIMIT" ? "rgba(124,58,237,.10)" : "rgba(217,119,6,.10)",
                                            color:      c.trigger_kind === "MONTHLY_LIMIT" ? "#6d28d9" : "#b45309" }}>
                              {c.trigger_kind === "MONTHLY_LIMIT" ? "Monthly" : "Single"}
                            </span>
                            {c.month_year && <span style={{ marginLeft:6, fontSize:10, color:"#94a3b8" }}>{c.month_year}</span>}
                          </td>
                          <td className="ca-td" style={{ fontWeight:700 }}>{c.line_name || `Line ${c.line_id}`}</td>
                          <td className="ca-td" style={{ fontFamily:"monospace" }}>
                            {c.machine_no ? <>#{c.machine_no} · {c.machine_name || "—"}</> : "—"}
                          </td>
                          <td className="ca-td" style={{ fontFamily:"monospace" }}>
                            <span style={{ color:"#dc2626", fontWeight:700 }}>{c.trigger_value_minutes}</span>
                            <span style={{ color:"#94a3b8" }}> / {c.threshold_minutes} min</span>
                          </td>
                          <td className="ca-td" style={{ fontFamily:"monospace" }}>{fmtDate(c.opened_at)}</td>
                          <td className="ca-td"><StatusBadge status={c.status}/></td>
                          <td className="ca-td">
                            <button className="ca-btn-ghost" onClick={() => openExisting(c)}>
                              {c.status === "CLOSED" ? "View" : "Edit"}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
          </>}{/* /qpr tab */}

        </div>
      </div>

      {editing && (
        <CapaFormModal
          ctx={editing}
          token={token}
          theme={theme}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); reload(); }}
        />
      )}
    </>
  );
}


/* ════════════════════════════════════════════════════════════════════
 * CAPA Form Modal — 8D-ish template
 * ════════════════════════════════════════════════════════════════════ */
// ── QPR FORM MODAL ────────────────────────────────────────────
// Toyota Boshoku QPR FORMAT.xlsx replicated 1:1 — every section, every
// row, every label.  Data persists into the existing mes_capa.capa_data
// JSONB column so we don't need a backend migration.
function CapaFormModal({ ctx, token, theme, onClose, onSaved }) {
  const isNew    = !!ctx._new;
  const readOnly = !isNew && ctx.status === "CLOSED";
  const [data, setData]     = useState(() => buildEmptyQPR(ctx.capa_data || {}));
  const [status, setStatus] = useState(ctx.status || "OPEN");
  const [saving, setSaving] = useState(false);

  // Setter helpers — `set` for top-level keys, `setSub` for nested objects,
  // `setArr` for the table rows.  All produce a fresh `data` reference so
  // React state updates correctly even with deeply-nested edits.
  const set    = (k, v)        => setData(d => ({ ...d, [k]: v }));
  const setSub = (group, k, v) => setData(d => ({ ...d, [group]: { ...(d[group] || {}), [k]: v } }));
  const setArr = (group, i, k, v) => setData(d => {
    const arr = [...(d[group] || [])];
    arr[i] = { ...(arr[i] || {}), [k]: v };
    return { ...d, [group]: arr };
  });
  const toggleBox = (key) => setData(d => ({
    ...d, detected_at: { ...(d.detected_at || {}), [key]: !d.detected_at?.[key] },
  }));

  // Required-field check gates the "Save & Close" button.  Mirrors the
  // QPR_REQUIRED_KEYS list above so the UI red-asterisks and the gate
  // stay in sync.
  const requiredFilled = QPR_REQUIRED_KEYS.every(k => {
    const v = data[k];
    return v != null && String(v).trim().length > 0;
  });

  const save = async (newStatus) => {
    setSaving(true);
    try {
      if (isNew) {
        await api.post("/api/capa/", {
          trigger_kind:           ctx.trigger_kind,
          breakdown_id:           ctx.breakdown_id,
          trigger_value_minutes:  ctx.trigger_value_minutes,
          threshold_minutes:      ctx.threshold_minutes,
          line_id:                ctx.line_id,
          line_name:              ctx.line_name,
          zone_id:                ctx.zone_id,
          zone_name:              ctx.zone_name,
          machine_no:             ctx.machine_no,
          machine_name:           ctx.machine_name,
          month_year:             ctx.month_year,
          capa_data:              data,
        }, token);
      } else {
        await api.put(`/api/capa/${ctx.id}`, {
          status:    newStatus || status,
          capa_data: data,
        }, token);
      }
      onSaved();
    } catch (e) { alert(e.message || "Save failed"); }
    finally    { setSaving(false); }
  };

  const close = async () => {
    if (!requiredFilled) {
      alert("Fill every required field before closing the QPR.");
      return;
    }
    setSaving(true);
    try {
      if (!isNew) {
        await api.put(`/api/capa/${ctx.id}`, { capa_data: data }, token);
        await api.post(`/api/capa/${ctx.id}/close`, {}, token);
      } else {
        const created = await api.post("/api/capa/", {
          trigger_kind:           ctx.trigger_kind,
          breakdown_id:           ctx.breakdown_id,
          trigger_value_minutes:  ctx.trigger_value_minutes,
          threshold_minutes:      ctx.threshold_minutes,
          line_id:                ctx.line_id,
          line_name:              ctx.line_name,
          zone_id:                ctx.zone_id,
          zone_name:              ctx.zone_name,
          machine_no:             ctx.machine_no,
          machine_name:           ctx.machine_name,
          month_year:             ctx.month_year,
          capa_data:              data,
        }, token);
        await api.post(`/api/capa/${created.id}/close`, {}, token);
      }
      onSaved();
    } catch (e) { alert(e.message || "Close failed"); }
    finally    { setSaving(false); }
  };

  // Print only the QPR sheet (hide overlay chrome, header bar, footer).
  // Same technique used in the breakdown closure form.
  const printQPR = () => {
    const sheet = document.getElementById("qpr-sheet");
    if (!sheet) return window.print();
    const w = window.open("", "_blank", "width=1200,height=900");
    w.document.write(`
      <html><head><title>QPR ${data.qpr_no || ""}</title>
      <style>
        @page { size: A4 portrait; margin: 8mm; }
        * { box-sizing: border-box; font-family: Arial, sans-serif; }
        body { margin: 0; color: #000; font-size: 9px; }
        .qpr-sec   { border: 1.2px solid #000; }
        .qpr-row   { display: flex; }
        .qpr-cell  { border-right: 1px solid #000; border-bottom: 1px solid #000;
                     padding: 2px 4px; font-size: 9px; min-height: 18px; flex: 1; }
        .qpr-cell:last-child { border-right: none; }
        .qpr-row:last-child .qpr-cell { border-bottom: none; }
        .qpr-cell.lbl { background: #f3f4f6; font-weight: 700; }
        .qpr-cell.title { background: #1f2937; color: #fff; font-weight: 800;
                          text-align: center; font-size: 11px; padding: 4px;
                          letter-spacing: .04em; text-transform: uppercase; }
        .qpr-cell.subtitle { background: #fef3c7; font-weight: 800;
                             text-align: center; padding: 3px; }
        input, textarea { border: none; background: transparent; font: inherit;
                          color: #000; padding: 0; width: 100%; resize: none; }
        textarea { min-height: 18px; }
        table { width: 100%; border-collapse: collapse; }
        td, th { border: 1px solid #000; padding: 2px 4px; font-size: 8.5px;
                 vertical-align: top; }
        th { background: #f3f4f6; font-weight: 700; text-align: center; }
        .ck { display: inline-block; width: 10px; height: 10px;
              border: 1.2px solid #000; margin-right: 4px; vertical-align: middle; }
        .ck.on::before { content: "✓"; font-weight: 700; font-size: 11px;
                          line-height: 8px; display: block; text-align: center; }
        h2 { margin: 6px 0 3px; font-size: 11px; }
      </style></head><body>${sheet.outerHTML}</body></html>`);
    w.document.close();
    setTimeout(() => { w.focus(); w.print(); }, 350);
  };

  return (
    <div onClick={onClose} style={{
      position:"fixed", inset:0, background:"rgba(15,23,42,.55)",
      backdropFilter:"blur(2px)", zIndex:9000,
      display:"flex", alignItems:"flex-start", justifyContent:"center",
      overflowY:"auto", padding:"24px 12px",
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        width:"100%", maxWidth:1100, background:"#fff",
        borderRadius:10, boxShadow:"0 20px 60px rgba(0,0,0,.35)", overflow:"hidden",
        display:"flex", flexDirection:"column", maxHeight:"95vh",
      }}>
        {/* Header bar (overlay chrome — hidden in print) */}
        <div style={{ display:"flex", alignItems:"stretch", borderBottom:"2px solid #0f172a", flexShrink:0 }}>
          <div style={{ flex:1, padding:"12px 18px" }}>
            <div style={{ fontFamily:"'Barlow Condensed',sans-serif", fontSize:20, fontWeight:800, color:"#0f172a" }}>
              {isNew ? "Open New QPR" : `QPR #${ctx.id}`}
            </div>
            <div style={{ fontSize:11, color:"#64748b", marginTop:2 }}>QUALITY PROBLEM REPORT — Toyota Boshoku</div>
          </div>
          <div onClick={onClose} title="Close"
               style={{ width:42, cursor:"pointer", display:"flex", alignItems:"center", justifyContent:"center",
                         fontSize:28, color:"#64748b", borderLeft:"1.5px solid #0f172a" }}>×</div>
        </div>

        {/* Trigger context (read-only) */}
        <div style={{ padding:"10px 18px", background:"rgba(220,38,38,.04)", borderBottom:"1px solid #e2e8f0", fontSize:12, flexShrink:0 }}>
          <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit, minmax(180px, 1fr))", gap:10 }}>
            <Field label="Trigger" value={ctx.trigger_kind === "MONTHLY_LIMIT" ? "Monthly sum limit" : "Single breakdown limit"}/>
            <Field label="Line"    value={ctx.line_name || "—"}/>
            <Field label="Zone"    value={ctx.zone_name || "—"}/>
            <Field label="Machine" value={ctx.machine_no ? `#${ctx.machine_no} · ${ctx.machine_name || ""}` : "—"}/>
            <Field label="Value / Limit" valueColor="#dc2626"
                   value={`${ctx.trigger_value_minutes ?? "—"} / ${ctx.threshold_minutes ?? "—"} min`}/>
            {ctx.month_year && <Field label="Month" value={ctx.month_year}/>}
            {!isNew && <Field label="Status" value={status}/>}
          </div>
        </div>

        {/* QPR sheet — scrollable inside the modal, printable as a single doc */}
        <div id="qpr-sheet" style={{
          padding:"14px 18px", overflowY:"auto", flex:1, background:"#fff",
          fontFamily:"Arial,sans-serif", fontSize:11, color:"#0f172a",
        }}>
          <style>{`
            .qpr-sec   { border: 1.4px solid #0f172a; margin-bottom: 4px; }
            .qpr-row   { display: flex; }
            .qpr-cell  { border-right: 1px solid #0f172a; border-bottom: 1px solid #0f172a;
                         padding: 3px 6px; font-size: 11px; min-height: 24px; flex: 1; display: flex; align-items: center; }
            .qpr-cell:last-child { border-right: none; }
            .qpr-row:last-child .qpr-cell { border-bottom: none; }
            .qpr-cell.lbl { background: #f3f4f6; font-weight: 700; color:#0f172a; }
            .qpr-cell.title { background: #0f172a; color: #fff; font-weight: 800;
                              text-align: center; padding: 6px; font-size: 12px;
                              letter-spacing: .05em; text-transform: uppercase;
                              justify-content: center; }
            .qpr-cell.subtitle { background: #fef3c7; font-weight: 800; padding: 4px;
                                 justify-content: center; color:#854d0e;
                                 text-transform: uppercase; letter-spacing: .04em; font-size: 10px; }
            .qpr-cell input, .qpr-cell textarea { border: none; background: transparent;
                                                  font: inherit; color: #0f172a;
                                                  padding: 0; width: 100%; outline: none;
                                                  resize: vertical; }
            .qpr-cell textarea { min-height: 22px; padding: 2px 0; }
            .qpr-cell input:disabled, .qpr-cell textarea:disabled { color: #475569; }
            .qpr-tbl { width: 100%; border-collapse: collapse; }
            .qpr-tbl th, .qpr-tbl td { border: 1px solid #0f172a; padding: 3px 5px;
                                       font-size: 10px; vertical-align: middle; }
            .qpr-tbl th { background: #f3f4f6; font-weight: 700; text-align: center; }
            .qpr-tbl td input, .qpr-tbl td textarea { width: 100%; border: none;
                                                     background: transparent; font: inherit;
                                                     padding: 0; outline: none; resize: none; }
            .qpr-ck { display: inline-flex; align-items: center; gap: 5px; font-size: 10.5px; }
            .qpr-ck input { width: 12px; height: 12px; }
          `}</style>

          {/* ═══════════ TOP HEADER BLOCK ═══════════ */}
          <div className="qpr-sec">
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:1.1, fontSize:13, justifyContent:"center", textAlign:"center"}}>
                TOYOTA BOSHOKU DEVICE INDIA PRIVATE LIMITED
              </div>
              <div className="qpr-cell lbl" style={{flex:0.45}}>QPR Date :-</div>
              <div className="qpr-cell" style={{flex:0.45}}>
                <input type="date" value={data.qpr_date} disabled={readOnly}
                       onChange={e=>set("qpr_date", e.target.value)}/>
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:1.1, fontSize:14, justifyContent:"center", textAlign:"center", fontWeight:800}}>
                QUALITY PROBLEM REPORT (QPR)
              </div>
              <div className="qpr-cell lbl" style={{flex:0.45}}>Reporting Time :</div>
              <div className="qpr-cell" style={{flex:0.45}}>
                <input type="time" value={data.reporting_time} disabled={readOnly}
                       onChange={e=>set("reporting_time", e.target.value)}/>
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:0.3}}>Location-Bawal</div>
              <div className="qpr-cell" style={{flex:0.8, fontSize:10}}>
                ORIGINAL : TO KEEP IN RECORD WHO IS RAISING QPR
              </div>
              <div className="qpr-cell lbl" style={{flex:0.45}}>QPR No. :</div>
              <div className="qpr-cell" style={{flex:0.45}}>
                <input type="text" value={data.qpr_no} disabled={readOnly}
                       onChange={e=>set("qpr_no", e.target.value)}
                       placeholder="QPR-####"/>
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell" style={{flex:1.1, fontSize:10, fontStyle:"italic", color:"#475569"}}>
                (To be filled by the Department who is raising the QPR)
              </div>
              <div className="qpr-cell lbl subtitle" style={{flex:0.9, fontSize:11}}>
                Problem Detected At :-
              </div>
            </div>
            {/* 9 detected-at checkboxes laid out 3×3 */}
            <div className="qpr-row">
              <div className="qpr-cell" style={{flex:2, padding:"6px 10px", flexDirection:"column", alignItems:"flex-start"}}>
                <div style={{display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:"6px 16px", width:"100%"}}>
                  {DETECTED_AT_BOXES.map(b => (
                    <label key={b} className="qpr-ck">
                      <input type="checkbox" disabled={readOnly}
                             checked={!!data.detected_at?.[b]}
                             onChange={() => toggleBox(b)}/>
                      {b}
                    </label>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* ═══════════ TIMELINE + RECORD DETAILS ═══════════ */}
          <div className="qpr-sec">
            <div className="qpr-row">
              <div className="qpr-cell title" style={{flex:0.4, fontSize:10}}>TIMELINE FOR ACTION</div>
              <div className="qpr-cell lbl" style={{flex:1.6, fontSize:10, fontWeight:600}}>
                RECORD THE FOLLOWING DETAILS &amp; DISTRIBUTE QPR THROUGH MAIL / HARD COPY
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:0.4}}>OEM Customer:-</div>
              <div className="qpr-cell" style={{flex:0.6}}>
                <input type="text" value={data.oem_customer} disabled={readOnly}
                       onChange={e=>set("oem_customer", e.target.value)}/>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.5}}>TBDI Assembly Part Details:-</div>
              <div className="qpr-cell" style={{flex:0.5}}/>
              <div className="qpr-cell lbl" style={{flex:0.5}}>TBDI Child Part Details:-</div>
              <div className="qpr-cell" style={{flex:0.5}}/>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:0.4}}>Model:-</div>
              <div className="qpr-cell" style={{flex:0.6}}>
                <input type="text" value={data.model} disabled={readOnly}
                       onChange={e=>set("model", e.target.value)}/>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.25}}>Product Name:-</div>
              <div className="qpr-cell" style={{flex:0.25}}>
                <input type="text" value={data.product_name} disabled={readOnly}
                       onChange={e=>set("product_name", e.target.value)}/>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.25}}>Product No.:-</div>
              <div className="qpr-cell" style={{flex:0.25}}>
                <input type="text" value={data.product_no} disabled={readOnly}
                       onChange={e=>set("product_no", e.target.value)}/>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.25}}>Part Name:-</div>
              <div className="qpr-cell" style={{flex:0.25}}>
                <input type="text" value={data.part_name} disabled={readOnly}
                       onChange={e=>set("part_name", e.target.value)}/>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.25}}>Part No.:-</div>
              <div className="qpr-cell" style={{flex:0.25}}>
                <input type="text" value={data.part_no} disabled={readOnly}
                       onChange={e=>set("part_no", e.target.value)}/>
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:0.4}}>Rejected part/s Batch Code:-</div>
              <div className="qpr-cell" style={{flex:0.5}}>
                <input type="text" value={data.rejected_batch} disabled={readOnly}
                       onChange={e=>set("rejected_batch", e.target.value)}/>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.4}}>Part/s given for analysis :-</div>
              <div className="qpr-cell" style={{flex:0.3, gap:8}}>
                <label className="qpr-ck">
                  <input type="radio" name="given_for_analysis" disabled={readOnly}
                         checked={data.given_for_analysis === "Yes"}
                         onChange={() => set("given_for_analysis", "Yes")}/>Yes
                </label>
                <label className="qpr-ck">
                  <input type="radio" name="given_for_analysis" disabled={readOnly}
                         checked={data.given_for_analysis === "No"}
                         onChange={() => set("given_for_analysis", "No")}/>No
                </label>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.3}}>QPR Raised by:-</div>
              <div className="qpr-cell" style={{flex:0.4}}>
                <input type="text" value={data.qpr_raised_by} disabled={readOnly}
                       onChange={e=>set("qpr_raised_by", e.target.value)}/>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.3}}>QPR Recd. By:-</div>
              <div className="qpr-cell" style={{flex:0.4}}>
                <input type="text" value={data.qpr_recd_by} disabled={readOnly}
                       onChange={e=>set("qpr_recd_by", e.target.value)}/>
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:0.4}}>Is it a Repeated Problem:-</div>
              <div className="qpr-cell" style={{flex:0.5, gap:10}}>
                <label className="qpr-ck">
                  <input type="radio" name="is_repeated" disabled={readOnly}
                         checked={data.is_repeated === "Yes"}
                         onChange={() => set("is_repeated", "Yes")}/>Yes
                </label>
                <label className="qpr-ck">
                  <input type="radio" name="is_repeated" disabled={readOnly}
                         checked={data.is_repeated === "No"}
                         onChange={() => set("is_repeated", "No")}/>No
                </label>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.4}}>Department:-</div>
              <div className="qpr-cell" style={{flex:0.6}}>
                <input type="text" value={data.raised_dept} disabled={readOnly}
                       onChange={e=>set("raised_dept", e.target.value)}/>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.3}}>Department:-</div>
              <div className="qpr-cell" style={{flex:0.4}}>
                <input type="text" value={data.recd_dept} disabled={readOnly}
                       onChange={e=>set("recd_dept", e.target.value)}/>
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:0.4}}>Qty. Rejected (How many):-</div>
              <div className="qpr-cell" style={{flex:0.5}}>
                <input type="text" value={data.qty_rejected} disabled={readOnly}
                       onChange={e=>set("qty_rejected", e.target.value)}/>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.2}}>Date:-</div>
              <div className="qpr-cell" style={{flex:0.2}}>
                <input type="date" value={data.raised_date} disabled={readOnly}
                       onChange={e=>set("raised_date", e.target.value)}/>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.2}}>Sign.:-</div>
              <div className="qpr-cell" style={{flex:0.2}}>
                <input type="text" value={data.raised_sign} disabled={readOnly}
                       onChange={e=>set("raised_sign", e.target.value)}/>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.2}}>Date:-</div>
              <div className="qpr-cell" style={{flex:0.2}}>
                <input type="date" value={data.recd_date} disabled={readOnly}
                       onChange={e=>set("recd_date", e.target.value)}/>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.2}}>Sign.:-</div>
              <div className="qpr-cell" style={{flex:0.2}}>
                <input type="text" value={data.recd_sign} disabled={readOnly}
                       onChange={e=>set("recd_sign", e.target.value)}/>
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell" style={{flex:1.4}}/>
              <div className="qpr-cell lbl" style={{flex:0.4}}>Recommended Reply Date:-</div>
              <div className="qpr-cell" style={{flex:0.5}}>
                <input type="date" value={data.recommended_reply_date} disabled={readOnly}
                       onChange={e=>set("recommended_reply_date", e.target.value)}/>
              </div>
            </div>
          </div>

          {/* ═══════════ REPORTED + DEFINED PROBLEM (5W2H) ═══════════ */}
          <div className="qpr-sec">
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:0.25}}>Reported Problem :-</div>
              <div className="qpr-cell" style={{flex:0.75}}>
                <textarea rows={2} value={data.reported_problem} disabled={readOnly}
                          onChange={e=>set("reported_problem", e.target.value)}/>
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell subtitle">Defined Problem (Through 5W2H) :-</div>
            </div>
            {[
              ["What?", "what",      "Where?",   "where"],
              ["When?", "when",      "Who?",     "who"],
              ["Why?",  "why",       "How?",     "how"],
              ["How Much?", "how_much", "",      ""],
            ].map(([l1,k1,l2,k2], i) => (
              <div className="qpr-row" key={i}>
                <div className="qpr-cell lbl" style={{flex:0.15}}>{l1}</div>
                <div className="qpr-cell" style={{flex:0.35}}>
                  <input type="text" value={data.fivew2h?.[k1] || ""} disabled={readOnly}
                         onChange={e=>setSub("fivew2h", k1, e.target.value)}/>
                </div>
                <div className="qpr-cell lbl" style={{flex:0.15}}>{l2}</div>
                <div className="qpr-cell" style={{flex:0.35}}>
                  {k2 && <input type="text" value={data.fivew2h?.[k2] || ""} disabled={readOnly}
                                 onChange={e=>setSub("fivew2h", k2, e.target.value)}/>}
                </div>
              </div>
            ))}
          </div>

          {/* ═══════════ CONTAINMENT (3 HOURS) ═══════════ */}
          <div className="qpr-sec">
            <div className="qpr-row"><div className="qpr-cell title">CONTAINMENT ACTION IS TO BE TAKEN WITHIN 3 HOURS OF PROBLEM REPORTED</div></div>
            <div className="qpr-row">
              <div className="qpr-cell" style={{fontSize:10, fontStyle:"italic", color:"#475569"}}>
                Below fields to be filled by the Department receiving the QPR
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:0.25}}>Defect Confirmation (HOW) :-</div>
              <div className="qpr-cell" style={{flex:0.45}}>
                <textarea rows={3} value={data.defect_confirmation} disabled={readOnly}
                          onChange={e=>set("defect_confirmation", e.target.value)}/>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.3}}>
                Sketch / Photograph (Recd. Part with Observations)
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:0.25}}>Interim Containment Action :</div>
              <div className="qpr-cell" style={{flex:0.75}}>
                <textarea rows={2} value={data.interim_containment} disabled={readOnly}
                          onChange={e=>set("interim_containment", e.target.value)}/>
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:0.5}}>
                Notification to Customer (Internal/External) required ( Please Tick ):-
              </div>
              <div className="qpr-cell" style={{flex:0.15, gap:8}}>
                <label className="qpr-ck">
                  <input type="radio" name="notify_required" disabled={readOnly}
                         checked={data.notification?.required === "Yes"}
                         onChange={()=>setSub("notification","required","Yes")}/>Yes
                </label>
                <label className="qpr-ck">
                  <input type="radio" name="notify_required" disabled={readOnly}
                         checked={data.notification?.required === "No"}
                         onChange={()=>setSub("notification","required","No")}/>No
                </label>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.13}}>If Yes, Resp.:-</div>
              <div className="qpr-cell" style={{flex:0.18}}>
                <input type="text" value={data.notification?.responsibility || ""} disabled={readOnly}
                       onChange={e=>setSub("notification","responsibility",e.target.value)}/>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.1}}>Tgt. Date:-</div>
              <div className="qpr-cell" style={{flex:0.12}}>
                <input type="date" value={data.notification?.target_date || ""} disabled={readOnly}
                       onChange={e=>setSub("notification","target_date",e.target.value)}/>
              </div>
              <div className="qpr-cell lbl" style={{flex:0.1}}>Imp. Date:-</div>
              <div className="qpr-cell" style={{flex:0.12}}>
                <input type="date" value={data.notification?.impl_date || ""} disabled={readOnly}
                       onChange={e=>setSub("notification","impl_date",e.target.value)}/>
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell subtitle">
                Stock Sort details as below: (TO BE INITIATED WITHIN 3 HRS OF PROBLEM RECEIVING)
              </div>
            </div>
            <div style={{padding:"4px"}}>
              <table className="qpr-tbl">
                <thead>
                  <tr>
                    {["S.No.","Parts Checked Location","Date","Resp.","Qty. Checked","B'Code","O.K","N.G.","Identification Mark","Remarks"].map(h => <th key={h}>{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {data.stock_sort.map((r, i) => (
                    <tr key={i}>
                      <td style={{textAlign:"center", fontWeight:700}}>{i+1}</td>
                      <td style={{fontWeight:600}}>{r.location}</td>
                      <td><input type="date" value={r.date} disabled={readOnly} onChange={e=>setArr("stock_sort", i, "date", e.target.value)}/></td>
                      <td><input type="text" value={r.resp} disabled={readOnly} onChange={e=>setArr("stock_sort", i, "resp", e.target.value)}/></td>
                      <td><input type="text" value={r.qty_checked} disabled={readOnly} onChange={e=>setArr("stock_sort", i, "qty_checked", e.target.value)}/></td>
                      <td><input type="text" value={r.b_code} disabled={readOnly} onChange={e=>setArr("stock_sort", i, "b_code", e.target.value)}/></td>
                      <td><input type="text" value={r.ok} disabled={readOnly} onChange={e=>setArr("stock_sort", i, "ok", e.target.value)}/></td>
                      <td><input type="text" value={r.ng} disabled={readOnly} onChange={e=>setArr("stock_sort", i, "ng", e.target.value)}/></td>
                      <td><input type="text" value={r.id_mark} disabled={readOnly} onChange={e=>setArr("stock_sort", i, "id_mark", e.target.value)}/></td>
                      <td><input type="text" value={r.remarks} disabled={readOnly} onChange={e=>setArr("stock_sort", i, "remarks", e.target.value)}/></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* ═══════════ CORRECTIVE / FISHBONE (3 DAYS) ═══════════ */}
          <div className="qpr-sec">
            <div className="qpr-row"><div className="qpr-cell title">CORRECTIVE ACTION IS TO BE TAKEN WITHIN 3 DAYS OF PROBLEM REPORTED</div></div>
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:0.25}}>Analysis Start Date:-</div>
              <div className="qpr-cell" style={{flex:0.75}}>
                <input type="date" value={data.analysis_start_date} disabled={readOnly}
                       onChange={e=>set("analysis_start_date", e.target.value)}/>
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell subtitle">Data Analysis (Using Fish Bone Diagram Approach)</div>
            </div>
            {/* Fishbone 4M+1E+Method+Measurement = 7 categories laid out 4+3 */}
            <div style={{padding:"4px"}}>
              <table className="qpr-tbl">
                <thead>
                  <tr>
                    <th>Man</th><th>Machine</th><th>Environment</th><th>Abnormality Handling</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    {["man","machine","environment","abnormality"].map(k => (
                      <td key={k}>
                        <textarea rows={4} value={data.fishbone?.[k] || ""} disabled={readOnly}
                                  onChange={e=>setSub("fishbone", k, e.target.value)}/>
                      </td>
                    ))}
                  </tr>
                </tbody>
                <thead>
                  <tr>
                    <th>Material</th><th>Method</th><th>Measurement</th><th style={{background:"#fff", border:"none"}}/>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    {["material","method","measurement"].map(k => (
                      <td key={k}>
                        <textarea rows={4} value={data.fishbone?.[k] || ""} disabled={readOnly}
                                  onChange={e=>setSub("fishbone", k, e.target.value)}/>
                      </td>
                    ))}
                    <td style={{border:"none"}}/>
                  </tr>
                </tbody>
              </table>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:0.3}}>Team members Involved :</div>
              <div className="qpr-cell" style={{flex:0.7}}>
                <input type="text" value={data.team_members} disabled={readOnly}
                       onChange={e=>set("team_members", e.target.value)}/>
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell" style={{fontSize:10, fontStyle:"italic", color:"#475569"}}>
                * Use Ranking Methodology to prioritize the possible causes (See Annexure-A)
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell subtitle">Data Validation</div>
            </div>
            <div style={{padding:"4px"}}>
              <table className="qpr-tbl">
                <thead>
                  <tr>
                    <th style={{width:"5%"}}>Sr. No.</th>
                    <th style={{width:"30%"}}>Possible cause</th>
                    <th style={{width:"30%"}}>Verification method (Gemba / Inspection / Statistical test / Experiment)</th>
                    <th style={{width:"15%"}}>Result</th>
                    <th style={{width:"20%"}}>Remarks</th>
                  </tr>
                </thead>
                <tbody>
                  {data.data_validation.map((r, i) => (
                    <tr key={i}>
                      <td style={{textAlign:"center"}}>{i+1}</td>
                      <td><input type="text" value={r.cause} disabled={readOnly} onChange={e=>setArr("data_validation", i, "cause", e.target.value)}/></td>
                      <td><input type="text" value={r.method} disabled={readOnly} onChange={e=>setArr("data_validation", i, "method", e.target.value)}/></td>
                      <td><input type="text" value={r.result} disabled={readOnly} onChange={e=>setArr("data_validation", i, "result", e.target.value)}/></td>
                      <td><input type="text" value={r.remarks} disabled={readOnly} onChange={e=>setArr("data_validation", i, "remarks", e.target.value)}/></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* ═══════════ ROOT CAUSE — WHY-WHY ═══════════ */}
          <div className="qpr-sec">
            <div className="qpr-row"><div className="qpr-cell subtitle">Root Cause (Using Why-Why Approach)</div></div>
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:0.12, fontWeight:800}}>For Occurrence</div>
              <div className="qpr-cell" style={{flex:0.88, padding:0}}>
                <table className="qpr-tbl" style={{height:"100%"}}>
                  <thead>
                    <tr>{["1st Why","2nd Why","3rd Why","4th Why","5th Why","6th Why"].map(h=><th key={h}>{h}</th>)}</tr>
                  </thead>
                  <tbody>
                    <tr>
                      {["w1","w2","w3","w4","w5","w6"].map(k => (
                        <td key={k}>
                          <textarea rows={3} value={data.root_occurrence?.[k] || ""} disabled={readOnly}
                                    onChange={e=>setSub("root_occurrence", k, e.target.value)}/>
                        </td>
                      ))}
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:0.12, fontWeight:800}}>For Flow Out</div>
              <div className="qpr-cell" style={{flex:0.88, padding:0}}>
                <table className="qpr-tbl" style={{height:"100%"}}>
                  <thead>
                    <tr>{["1st Why","2nd Why","3rd Why","4th Why","5th Why","Remarks"].map(h=><th key={h}>{h}</th>)}</tr>
                  </thead>
                  <tbody>
                    <tr>
                      {["w1","w2","w3","w4","w5","remarks"].map(k => (
                        <td key={k}>
                          <textarea rows={3} value={data.root_flowout?.[k] || ""} disabled={readOnly}
                                    onChange={e=>setSub("root_flowout", k, e.target.value)}/>
                        </td>
                      ))}
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          {/* ═══════════ COUNTERMEASURE TAKEN ═══════════ */}
          <div className="qpr-sec">
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:0.4}}>Analysis Completion Date:-</div>
              <div className="qpr-cell" style={{flex:0.6}}>
                <input type="date" value={data.analysis_completion_date} disabled={readOnly}
                       onChange={e=>set("analysis_completion_date", e.target.value)}/>
              </div>
            </div>
            <div className="qpr-row"><div className="qpr-cell title">COUNTERMEASURE (C/M) TAKEN</div></div>
            <div className="qpr-row"><div className="qpr-cell subtitle">Corrective Action — For Occurrence</div></div>
            <div style={{padding:"4px"}}>
              <table className="qpr-tbl">
                <thead>
                  <tr>{["S.No","Countermeasures","Resp.","Tgt. Date","Impl Dt","Effective Batch Code"].map(h=><th key={h}>{h}</th>)}</tr>
                </thead>
                <tbody>
                  {data.cm_occurrence.map((r,i) => (
                    <tr key={i}>
                      <td style={{textAlign:"center"}}>{i+1}</td>
                      <td><input type="text" value={r.action} disabled={readOnly} onChange={e=>setArr("cm_occurrence",i,"action",e.target.value)}/></td>
                      <td><input type="text" value={r.resp}   disabled={readOnly} onChange={e=>setArr("cm_occurrence",i,"resp",e.target.value)}/></td>
                      <td><input type="date" value={r.tgt}    disabled={readOnly} onChange={e=>setArr("cm_occurrence",i,"tgt",e.target.value)}/></td>
                      <td><input type="date" value={r.impl}   disabled={readOnly} onChange={e=>setArr("cm_occurrence",i,"impl",e.target.value)}/></td>
                      <td><input type="text" value={r.batch}  disabled={readOnly} onChange={e=>setArr("cm_occurrence",i,"batch",e.target.value)}/></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="qpr-row"><div className="qpr-cell subtitle">Corrective Action — For Flow Out</div></div>
            <div style={{padding:"4px"}}>
              <table className="qpr-tbl">
                <thead>
                  <tr>{["S.No","Countermeasures","Resp.","Tgt. Date","Impl Dt","Effective Batch Code"].map(h=><th key={h}>{h}</th>)}</tr>
                </thead>
                <tbody>
                  {data.cm_flowout.map((r,i) => (
                    <tr key={i}>
                      <td style={{textAlign:"center"}}>{i+1}</td>
                      <td><input type="text" value={r.action} disabled={readOnly} onChange={e=>setArr("cm_flowout",i,"action",e.target.value)}/></td>
                      <td><input type="text" value={r.resp}   disabled={readOnly} onChange={e=>setArr("cm_flowout",i,"resp",e.target.value)}/></td>
                      <td><input type="date" value={r.tgt}    disabled={readOnly} onChange={e=>setArr("cm_flowout",i,"tgt",e.target.value)}/></td>
                      <td><input type="date" value={r.impl}   disabled={readOnly} onChange={e=>setArr("cm_flowout",i,"impl",e.target.value)}/></td>
                      <td><input type="text" value={r.batch}  disabled={readOnly} onChange={e=>setArr("cm_flowout",i,"batch",e.target.value)}/></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* ═══════════ STANDARDIZATION / HD (3 WEEKS) ═══════════ */}
          <div className="qpr-sec">
            <div className="qpr-row"><div className="qpr-cell title">STANDARDIZATION / HORIZONTAL DEPLOYMENT IS TO BE DONE WITHIN 3 WEEKS OF PROBLEM REPORTED</div></div>
            <div style={{padding:"4px"}}>
              <table className="qpr-tbl">
                <thead>
                  <tr>{["S.No","Action Taken for Horizontal Deployment","Resp.","Tgt. Date","Impl Dt","Remarks"].map(h=><th key={h}>{h}</th>)}</tr>
                </thead>
                <tbody>
                  {data.horizontal_deployment.map((r,i) => (
                    <tr key={i}>
                      <td style={{textAlign:"center"}}>{i+1}</td>
                      <td><input type="text" value={r.action} disabled={readOnly} onChange={e=>setArr("horizontal_deployment",i,"action",e.target.value)}/></td>
                      <td><input type="text" value={r.resp}   disabled={readOnly} onChange={e=>setArr("horizontal_deployment",i,"resp",e.target.value)}/></td>
                      <td><input type="date" value={r.tgt}    disabled={readOnly} onChange={e=>setArr("horizontal_deployment",i,"tgt",e.target.value)}/></td>
                      <td><input type="date" value={r.impl}   disabled={readOnly} onChange={e=>setArr("horizontal_deployment",i,"impl",e.target.value)}/></td>
                      <td><input type="text" value={r.remarks} disabled={readOnly} onChange={e=>setArr("horizontal_deployment",i,"remarks",e.target.value)}/></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="qpr-row"><div className="qpr-cell title">COUNTERMEASURES EFFECTIVENESS CHECK</div></div>
            <div style={{padding:"4px"}}>
              <table className="qpr-tbl">
                <thead>
                  <tr>
                    <th style={{width:"15%"}}> </th>
                    <th>WK1</th><th>WK2</th><th>WK3</th>
                    <th style={{width:"25%"}}>Remarks</th>
                  </tr>
                </thead>
                <tbody>
                  {[["Qty","qty"],["DATE","date"],["STATUS","status"],["SIGN.","sign"]].map(([lbl, k]) => (
                    <tr key={k}>
                      <td style={{fontWeight:700, background:"#f3f4f6"}}>{lbl}</td>
                      {["wk1","wk2","wk3"].map(wk => (
                        <td key={wk}>
                          <input type={k === "date" ? "date" : "text"}
                                 value={data.effectiveness?.[wk]?.[k] || ""} disabled={readOnly}
                                 onChange={e => setData(d => ({
                                   ...d, effectiveness: {
                                     ...(d.effectiveness || {}),
                                     [wk]: { ...((d.effectiveness || {})[wk] || {}), [k]: e.target.value }
                                   }
                                 }))}/>
                        </td>
                      ))}
                      {k === "qty" && (
                        <td rowSpan={4}>
                          <textarea rows={6} value={data.effectiveness?.remarks || ""} disabled={readOnly}
                                    onChange={e=>setSub("effectiveness", "remarks", e.target.value)}/>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* ═══════════ STANDARDIZATION CHECK ═══════════ */}
          <div className="qpr-sec">
            <div className="qpr-row"><div className="qpr-cell title">STANDARDIZATION CHECK</div></div>
            <div style={{padding:"4px"}}>
              <table className="qpr-tbl">
                <thead>
                  <tr>
                    <th style={{width:"5%"}}>S.No</th>
                    <th style={{width:"22%"}}>Activities</th>
                    <th style={{width:"13%"}}>Reviewed (Yes/No)</th>
                    <th style={{width:"13%"}}>Revision Reqd. (Yes/No)</th>
                    <th style={{width:"32%"}}>Revision Details</th>
                    <th style={{width:"15%"}}>Remarks</th>
                  </tr>
                </thead>
                <tbody>
                  {data.standardization_check.map((r,i) => (
                    <tr key={i}>
                      <td style={{textAlign:"center"}}>{i+1}</td>
                      <td style={{fontWeight:600}}>{r.activity}</td>
                      <td><input type="text" value={r.reviewed} disabled={readOnly} onChange={e=>setArr("standardization_check",i,"reviewed",e.target.value)} placeholder="Yes / No"/></td>
                      <td><input type="text" value={r.revision_required} disabled={readOnly} onChange={e=>setArr("standardization_check",i,"revision_required",e.target.value)} placeholder="Yes / No"/></td>
                      <td><input type="text" value={r.revision_details} disabled={readOnly} onChange={e=>setArr("standardization_check",i,"revision_details",e.target.value)}/></td>
                      <td><input type="text" value={r.remarks} disabled={readOnly} onChange={e=>setArr("standardization_check",i,"remarks",e.target.value)}/></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* ═══════════ SIGN-OFF ═══════════ */}
          <div className="qpr-sec">
            <div className="qpr-row">
              <div className="qpr-cell lbl" style={{flex:1, justifyContent:"center"}}>Prepared By:-</div>
              <div className="qpr-cell lbl" style={{flex:1, justifyContent:"center"}}>Verified By:-</div>
              <div className="qpr-cell lbl" style={{flex:1, justifyContent:"center"}}>Approved By:- (Internal Customer)</div>
            </div>
            {[
              ["Name", "by"],
              ["Date", "date"],
              ["Sign", "sign"],
            ].map(([label, suffix]) => (
              <div className="qpr-row" key={suffix}>
                <div className="qpr-cell lbl" style={{flex:0.12}}>{label}:-</div>
                <div className="qpr-cell" style={{flex:0.55}}>
                  <input type={suffix === "date" ? "date" : "text"}
                         value={data.sign_off?.[`prepared_${suffix === "by" ? "by" : suffix}`] || ""}
                         disabled={readOnly}
                         onChange={e=>setSub("sign_off", `prepared_${suffix === "by" ? "by" : suffix}`, e.target.value)}/>
                </div>
                <div className="qpr-cell lbl" style={{flex:0.12}}>{label}:-</div>
                <div className="qpr-cell" style={{flex:0.55}}>
                  <input type={suffix === "date" ? "date" : "text"}
                         value={data.sign_off?.[`verified_${suffix === "by" ? "by" : suffix}`] || ""}
                         disabled={readOnly}
                         onChange={e=>setSub("sign_off", `verified_${suffix === "by" ? "by" : suffix}`, e.target.value)}/>
                </div>
                <div className="qpr-cell lbl" style={{flex:0.12}}>{label}:-</div>
                <div className="qpr-cell" style={{flex:0.55}}>
                  <input type={suffix === "date" ? "date" : "text"}
                         value={data.sign_off?.[`approved_${suffix === "by" ? "by" : suffix}`] || ""}
                         disabled={readOnly}
                         onChange={e=>setSub("sign_off", `approved_${suffix === "by" ? "by" : suffix}`, e.target.value)}/>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Footer (chrome — never printed) */}
        <div style={{ padding:"10px 18px", background:"#f8fafc", borderTop:"1px solid #e2e8f0",
                       display:"flex", alignItems:"center", justifyContent:"space-between",
                       gap:10, flexWrap:"wrap", flexShrink:0 }}>
          <div style={{ fontSize:11, color:"#64748b" }}>
            {isNew      ? "Saving will open a new QPR in OPEN state." :
             readOnly   ? "This QPR is closed and read-only." :
             requiredFilled ? "All required fields filled — ready to close." :
                              "Fill QPR No / Date / Reported Problem / Interim Containment / Analysis dates / Team members."}
          </div>
          <div style={{ display:"flex", gap:8 }}>
            <button onClick={printQPR}
                    style={{ background:"#fff", color:"#0f172a", border:"1.5px solid #0f172a",
                             padding:"7px 14px", borderRadius:8, fontWeight:700, fontSize:12, cursor:"pointer" }}>
              🖨 Print
            </button>
            <button onClick={onClose}
                    style={{ background:"#fff", color:"#475569", border:"1.5px solid #e2e8f0",
                             padding:"7px 14px", borderRadius:8, fontWeight:700, fontSize:12, cursor:"pointer" }}>
              {readOnly ? "Close" : "Cancel"}
            </button>
            {!readOnly && (
              <>
                <button onClick={() => save("IN_PROGRESS")} disabled={saving}
                        style={{ background:"#fff", color: theme.accent, border:`1.5px solid ${theme.accent}`,
                                 padding:"7px 14px", borderRadius:8, fontWeight:700, fontSize:12, cursor:"pointer" }}>
                  {saving ? "Saving…" : isNew ? "Save & Continue" : "Save Draft"}
                </button>
                <button onClick={close} disabled={saving || !requiredFilled}
                        style={{ background: requiredFilled ? "linear-gradient(135deg,#16a34a,#15803d)" : "#cbd5e1",
                                 color:"#fff", border:"none",
                                 padding:"7px 14px", borderRadius:8, fontWeight:700, fontSize:12,
                                 cursor: requiredFilled ? "pointer" : "not-allowed" }}>
                  {saving ? "Closing…" : "Save & Close QPR"}
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Field({ label, value, valueColor }) {
  return (
    <div>
      <div style={{ fontSize:9, fontWeight:700, color:"#64748b",
                     letterSpacing:".06em", textTransform:"uppercase" }}>{label}</div>
      <div style={{ fontSize:12, fontWeight:700, color: valueColor || "#0f172a", marginTop:1 }}>
        {value || "—"}
      </div>
    </div>
  );
}


/* ════════════════════════════════════════════════════════════════════
 * Pareto Chart — zone-wise breakdown bar chart for the CAPA tab
 * ════════════════════════════════════════════════════════════════════
 * Shows every machine's monthly breakdown minutes as a horizontal bar,
 * sorted descending.  Bars over the per-machine threshold are red,
 * within-threshold are gray.  A cumulative-% line marks where the
 * configured `pareto_pct` cutoff lands — every machine inside the
 * cutoff MUST file a CAPA, and the row gets a "Start CAPA" /
 * "Already filed" pill accordingly.
 *
 * Filter bar:
 *   • Zone dropdown  (defaults to first zone the user can see)
 *   • Month picker   (YYYY-MM, default current month)
 *
 * Auto-refresh: same 6 s polling + ap-config-changed listener as
 * SystemMap so a freshly-closed breakdown reflects without manual
 * refresh.
 */
function ParetoChart({ token, theme, isAdmin, onStartCapa }) {
  const [zones,    setZones]    = useState([]);
  const [zoneId,   setZoneId]   = useState("");
  const [month,    setMonth]    = useState(() => new Date().toISOString().slice(0, 7));
  const [data,     setData]     = useState({ machines: [], pareto_pct: 80, breached_total: 0 });
  const [loading,  setLoading]  = useState(true);
  // Admin-editable inline copies of the three GLOBAL knobs.  Maintenance
  // dept users see the live values but the inputs are hidden / disabled
  // (admin is enforced both server-side and visually here).
  const [paretoCfg,    setParetoCfg]    = useState(80);
  const [monthlyCfg,   setMonthlyCfg]   = useState(120);
  const [singleCfg,    setSingleCfg]    = useState(60);

  // Load zones list once
  useEffect(() => {
    if (!token) return;
    api.get("/api/zones/", token).then(d => {
      const z = Array.isArray(d) ? d : [];
      setZones(z);
      if (z.length && !zoneId) setZoneId(String(z[0].id));
    }).catch(() => setZones([]));
    // eslint-disable-next-line
  }, [token]);

  const reload = useCallback(async (silent = false) => {
    if (!token) return;
    if (!silent) setLoading(true);
    try {
      const q = new URLSearchParams({ month_year: month });
      if (zoneId) q.set("zone_id", zoneId);
      const [d, cfg] = await Promise.all([
        api.get(`/api/capa/pareto?${q.toString()}`, token).catch(() => ({ machines: [] })),
        api.get("/api/capa/pareto-config", token).catch(() => ({ pareto_pct: 80, monthly_sum_minutes_limit: 120, single_breakdown_minutes_limit: 60 })),
      ]);
      setData({
        machines:        Array.isArray(d.machines) ? d.machines : [],
        pareto_pct:      d.pareto_pct ?? 80,
        breached_total:  d.breached_total ?? 0,
        month_year:      d.month_year || month,
      });
      setParetoCfg (cfg.pareto_pct                     ?? 80);
      setMonthlyCfg(cfg.monthly_sum_minutes_limit      ?? 120);
      setSingleCfg (cfg.single_breakdown_minutes_limit ?? 60);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [token, zoneId, month]);

  // Initial + filter-driven reload, plus polling + change-event hooks.
  useEffect(() => {
    reload();
    const onChange = () => reload(true);
    const onFocus  = () => reload(true);
    window.addEventListener("ap-config-changed", onChange);
    window.addEventListener("focus", onFocus);
    const tick = setInterval(() => reload(true), 6000);
    return () => {
      clearInterval(tick);
      window.removeEventListener("ap-config-changed", onChange);
      window.removeEventListener("focus", onFocus);
    };
  }, [reload]);

  // Save admin-edited GLOBAL CAPA config (all three knobs at once).
  const savePareto = async () => {
    try {
      await api.put("/api/capa/pareto-config", {
        pareto_pct:                     paretoCfg,
        monthly_sum_minutes_limit:      monthlyCfg,
        single_breakdown_minutes_limit: singleCfg,
      }, token);
      reload();
    } catch (e) { alert(e.message || "Save failed"); }
  };

  const machines  = data.machines || [];
  const maxMin    = Math.max(1, ...machines.map(m => Number(m.breakdown_minutes) || 0));
  const breached  = machines.filter(m => m.breached);

  return (
    <div className="ca-section">
      <h3 style={{display:"flex", alignItems:"center", gap:10, flexWrap:"wrap"}}>
        📊 Pareto — Zone-wise Breakdown
        <span style={{fontSize:11, fontWeight:600, color:"#94a3b8", letterSpacing:0, textTransform:"none"}}>
          · Top {data.pareto_pct}% of breakdown time MUST file CAPA
        </span>
      </h3>

      {/* Filter row */}
      <div className="ca-card" style={{padding:14, marginBottom:14, display:"flex",
                                        gap:12, alignItems:"flex-end", flexWrap:"wrap"}}>
        <div style={{display:"flex", flexDirection:"column", gap:5, minWidth:160}}>
          <label style={{fontSize:10, fontWeight:700, color:"#64748b",
                          letterSpacing:".08em", textTransform:"uppercase"}}>Zone</label>
          <select className="ca-input" value={zoneId} onChange={e=>setZoneId(e.target.value)}>
            <option value="">All zones</option>
            {zones.map(z => <option key={z.id} value={z.id}>{z.zone_name}</option>)}
          </select>
        </div>
        <div style={{display:"flex", flexDirection:"column", gap:5}}>
          <label style={{fontSize:10, fontWeight:700, color:"#64748b",
                          letterSpacing:".08em", textTransform:"uppercase"}}>Month</label>
          <input type="month" className="ca-input" value={month}
                 onChange={e => setMonth(e.target.value)}/>
        </div>

        {/* Admin-only inline editors for the 3 GLOBAL knobs.
            Maintenance dept users see the live values but the inputs
            stay hidden — they can change them only from the Admin
            Maintenance Panel → CAPA Settings tab.  Same backend, same
            row, single source of truth. */}
        <div style={{display:"flex", gap:14, alignItems:"flex-end",
                      marginLeft:"auto", flexWrap:"wrap"}}>
          {[
            { label:"Monthly Limit (min)",  val:monthlyCfg, set:setMonthlyCfg, live:data.machines[0]?.threshold_minutes ?? monthlyCfg },
            { label:"Single Limit (min)",   val:singleCfg,  set:setSingleCfg,  live:singleCfg },
            { label:"Pareto Cutoff %",      val:paretoCfg,  set:setParetoCfg,  live:data.pareto_pct, suffix:"%" },
          ].map(f => (
            <div key={f.label} style={{display:"flex", flexDirection:"column", gap:5}}>
              <label style={{fontSize:10, fontWeight:700, color:"#64748b",
                              letterSpacing:".08em", textTransform:"uppercase"}}>
                {f.label}
              </label>
              {isAdmin ? (
                <input type="number" min="1" className="ca-input"
                       style={{width:90, fontFamily:"monospace", fontWeight:700,
                                textAlign:"center"}}
                       value={f.val}
                       onChange={e => f.set(Number(e.target.value) || 0)}/>
              ) : (
                <div style={{padding:"7px 11px", fontFamily:"monospace", fontWeight:800,
                              fontSize:14, color:theme.accent, textAlign:"center"}}>
                  {f.live}{f.suffix || ""}
                </div>
              )}
            </div>
          ))}
          {isAdmin && (
            <button onClick={savePareto}
                    style={{padding:"9px 14px", borderRadius:8,
                            background:theme.accent, color:"#fff", border:"none",
                            fontWeight:700, fontSize:12, cursor:"pointer",
                            alignSelf:"flex-end"}}>
              Save
            </button>
          )}
        </div>
      </div>

      {/* Chart */}
      <div className="ca-card" style={{padding:14}}>
        {loading ? (
          <div style={{padding:60, textAlign:"center", color:"#94a3b8"}}>Loading…</div>
        ) : machines.length === 0 ? (
          <div style={{padding:60, textAlign:"center", color:"#94a3b8", fontStyle:"italic"}}>
            No breakdowns recorded for {data.month_year || month}
            {zoneId && zones.find(z => String(z.id) === String(zoneId))
              ? ` in ${zones.find(z => String(z.id) === String(zoneId)).zone_name}` : ""}
            . The chart auto-rebuilds the moment a breakdown ticket is closed.
          </div>
        ) : (
          <div style={{display:"flex", flexDirection:"column", gap:6}}>
            {/* Header row */}
            <div style={{display:"grid",
                          gridTemplateColumns:"180px 1fr 80px 90px 110px",
                          gap:10, padding:"4px 8px", fontSize:10, fontWeight:700,
                          letterSpacing:".08em", textTransform:"uppercase",
                          color:"#64748b", borderBottom:"1px solid #e2e8f0",
                          marginBottom:6}}>
              <div>Machine</div>
              <div>Breakdown (min) →</div>
              <div style={{textAlign:"right"}}>Total</div>
              <div style={{textAlign:"right"}}>Cum %</div>
              <div style={{textAlign:"right"}}>Action</div>
            </div>
            {machines.map((m, i) => {
              const widthPct = (Number(m.breakdown_minutes) / maxMin) * 100;
              const barColor = !m.breached
                ? "#94a3b8"  // gray — within threshold
                : m.must_file_capa
                  ? "#dc2626"  // red — must file CAPA
                  : "#f59e0b"; // amber — breached but below cumulative cutoff
              return (
                <div key={`${m.line_id}-${m.machine_no}-${i}`}
                     style={{display:"grid",
                              gridTemplateColumns:"180px 1fr 80px 90px 110px",
                              gap:10, alignItems:"center",
                              padding:"6px 8px", fontSize:12,
                              borderBottom:"1px solid #f1f5f9"}}>
                  <div style={{minWidth:0, overflow:"hidden", textOverflow:"ellipsis",
                                whiteSpace:"nowrap"}}>
                    <div style={{fontWeight:700, color:"#0f172a", fontSize:12}}
                         title={`${m.line_name || ""} · ${m.machine_name || ""}`}>
                      #{m.machine_no} {m.machine_name ? `· ${m.machine_name}` : ""}
                    </div>
                    <div style={{fontSize:10, color:"#94a3b8"}}>
                      {m.line_name || `Line ${m.line_id}`}{m.zone_name ? ` · ${m.zone_name}` : ""}
                    </div>
                  </div>
                  <div style={{position:"relative", height:18, background:"#f1f5f9",
                                borderRadius:4, overflow:"hidden"}}>
                    <div style={{position:"absolute", inset:0, width: `${widthPct}%`,
                                  background: barColor,
                                  transition:"width .25s, background .25s"}}/>
                    {/* threshold marker */}
                    {m.threshold_minutes > 0 && (
                      <div title={`Threshold ${m.threshold_minutes} min`}
                           style={{position:"absolute", top:-2, bottom:-2,
                                    left: `${(m.threshold_minutes / maxMin) * 100}%`,
                                    width:2, background:"#0f172a"}}/>
                    )}
                  </div>
                  <div style={{textAlign:"right", fontFamily:"monospace", fontWeight:700,
                                color: m.breached ? "#dc2626" : "#475569"}}>
                    {m.breakdown_minutes}
                  </div>
                  <div style={{textAlign:"right", fontFamily:"monospace",
                                color:"#475569", fontSize:11}}>
                    {m.cumulative_pct != null ? `${m.cumulative_pct}%` : "—"}
                  </div>
                  <div style={{textAlign:"right"}}>
                    {!m.must_file_capa ? (
                      <span style={{fontSize:10, color: m.breached ? "#b45309" : "#94a3b8",
                                     fontWeight:700}}>
                        {m.breached ? "Below cutoff" : "OK"}
                      </span>
                    ) : m.capa_status === "CLOSED" ? (
                      <span style={{padding:"2px 9px", borderRadius:99, fontSize:10,
                                     fontWeight:700, background:"rgba(22,163,74,.10)",
                                     color:"#15803d"}}>✓ Filed</span>
                    ) : m.capa_status ? (
                      <span style={{padding:"2px 9px", borderRadius:99, fontSize:10,
                                     fontWeight:700, background:"rgba(217,119,6,.10)",
                                     color:"#b45309"}}>{m.capa_status}</span>
                    ) : (
                      <button onClick={() => onStartCapa({ ...m, month_year: data.month_year || month })}
                              style={{background:"linear-gradient(135deg,#dc2626,#b91c1c)",
                                      color:"#fff", border:"none",
                                      padding:"4px 10px", borderRadius:6,
                                      fontWeight:800, fontSize:11, cursor:"pointer",
                                      whiteSpace:"nowrap"}}>
                        Must File CAPA
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
            <div style={{padding:"8px 8px 0", fontSize:11, color:"#64748b",
                          display:"flex", gap:18, flexWrap:"wrap"}}>
              <span><span style={{display:"inline-block", width:10, height:10,
                                    background:"#dc2626", borderRadius:2, marginRight:5,
                                    verticalAlign:"middle"}}/> Must file CAPA (top {data.pareto_pct}%)</span>
              <span><span style={{display:"inline-block", width:10, height:10,
                                    background:"#f59e0b", borderRadius:2, marginRight:5,
                                    verticalAlign:"middle"}}/> Breached threshold</span>
              <span><span style={{display:"inline-block", width:10, height:10,
                                    background:"#94a3b8", borderRadius:2, marginRight:5,
                                    verticalAlign:"middle"}}/> Within threshold</span>
              <span><span style={{display:"inline-block", width:2, height:12,
                                    background:"#0f172a", marginRight:5,
                                    verticalAlign:"middle"}}/> Per-machine threshold</span>
              <span style={{marginLeft:"auto", fontWeight:700}}>
                {breached.length} machine(s) breached · {data.breached_total} total min
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
