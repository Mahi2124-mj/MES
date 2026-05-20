/* ───────────────────────────────────────────────────────────────────
 * MaintenanceHistorical.jsx
 * ───────────────────────────────────────────────────────────────────
 * Maintenance Historical Data page — past breakdown slips + KPI
 * aggregates (MTTR / MTBF / LTTR) at three roll-up levels:
 *
 *   • Zone-wise   (LTTR + breakdowns count)
 *   • Line-wise   (MTBF + MTTR + breakdowns count)
 *   • Machine-wise (MTTR + MTBF + LTTR + breakdowns count, keyed off
 *                   the Production-entered machine_no / machine_name)
 *
 * The bottom half is a filterable, paginated archive of every slip
 * (OPEN / RESOLVED / CLOSED) from the chosen window.  Click any row
 * to open the slip in read-only "view" mode.
 *
 * Routing: /maintenance-historical, gated to maintenance department
 * users (and admin) via canAccess('maintenance-historical').
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { ClosureFormModal } from "./MaintenanceDashboard";

const API = "";
const api = {
  async get(path, token) {
    const r = await fetch(API + path, { headers: { Authorization: `Bearer ${token}` } });
    if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
    return r.json();
  },
};

function fmt(n, digits = 1, dash = "—") {
  if (n == null || (typeof n === "number" && isNaN(n))) return dash;
  return Number(n).toFixed(digits);
}
function fmtDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-IN", {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return "—"; }
}
function fmtDuration(seconds) {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60), rem = m % 60;
  return rem ? `${h}h ${rem}m` : `${h}h`;
}

const STATE_BADGE = {
  OPEN:     { bg: "rgba(220,38,38,.10)", color: "#dc2626", label: "Open"     },
  RESOLVED: { bg: "rgba(217,119,6,.10)", color: "#b45309", label: "Resolved" },
  CLOSED:   { bg: "rgba(22,163,74,.10)", color: "#15803d", label: "Closed"   },
};

function StateBadge({ state }) {
  const meta = STATE_BADGE[state] || { bg:"#f1f5f9", color:"#64748b", label: state };
  return (
    <span style={{ padding:"2px 9px", borderRadius:99, fontSize:10, fontWeight:700,
                    background: meta.bg, color: meta.color, whiteSpace:"nowrap" }}>
      {meta.label}
    </span>
  );
}

function Tile({ label, value, sub, color = "#1e40af" }) {
  return (
    <div style={{
      background:"#fff", border:"1px solid #e2e8f0", borderRadius:12,
      padding:"14px 18px", minWidth:140, flex:"0 0 auto",
      boxShadow:"0 1px 3px rgba(0,0,0,.04)",
    }}>
      <div style={{ fontSize:10, color:"#64748b", fontWeight:700,
                     letterSpacing:".08em", textTransform:"uppercase" }}>
        {label}
      </div>
      <div style={{ fontSize:26, fontWeight:800, color, marginTop:2,
                     fontFamily:"'Barlow Condensed',sans-serif" }}>
        {value}
      </div>
      {sub && <div style={{ fontSize:11, color:"#94a3b8", marginTop:1 }}>{sub}</div>}
    </div>
  );
}

export default function MaintenanceHistorical() {
  const { token, theme, isAdmin, user } = useAuth();
  const [days,        setDays]        = useState(30);
  // Custom date range — when either is set it OVERRIDES `days` on both
  // /history and /stats endpoints (backend prefers explicit dates).
  const [fromDate,    setFromDate]    = useState("");      // YYYY-MM-DD
  const [toDate,      setToDate]      = useState("");      // YYYY-MM-DD
  const [stateFilter, setStateFilter] = useState("");      // "" = all
  const [zoneFilter,  setZoneFilter]  = useState("");      // "" = all
  const [lineFilter,  setLineFilter]  = useState("");
  const [machineFilter, setMachineFilter] = useState("");
  const [search,      setSearch]      = useState("");

  const [stats,       setStats]       = useState({ zones: [], lines: [], machines: [] });
  const [history,     setHistory]     = useState({ rows: [], total: 0 });
  const [loading,     setLoading]     = useState(true);
  const [viewModal,   setViewModal]   = useState(null);    // ticket for read-only modal

  // Side-tab roll-up: 'zone' | 'line' | 'machine'
  const [rollup, setRollup] = useState("zone");

  // ── Page title (admin sees specific name, dept user just "Historical Data")
  useEffect(() => {
    document.title = isAdmin ? "Maintenance Historical Data" : "Historical Data";
  }, [isAdmin]);

  // ── Reload stats + history whenever any filter changes ───────────────
  const reload = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      // Build common date-window params used by BOTH /stats and /history
      // — explicit from/to dates override the days preset on the backend.
      const winParams = {};
      if (fromDate) winParams.from_date = fromDate;
      if (toDate)   winParams.to_date   = toDate;
      const winQ = new URLSearchParams({ days: String(days), ...winParams });

      const histQ = new URLSearchParams({ ...Object.fromEntries(winQ), limit: "500" });
      if (stateFilter)   histQ.set("state",      stateFilter);
      if (zoneFilter)    histQ.set("zone_id",    zoneFilter);
      if (lineFilter)    histQ.set("line_id",    lineFilter);
      if (machineFilter) histQ.set("machine_no", machineFilter);

      const [s, h] = await Promise.all([
        api.get(`/api/breakdowns/stats?${winQ.toString()}`,  token).catch(() => ({})),
        api.get(`/api/breakdowns/history?${histQ.toString()}`, token).catch(() => ({})),
      ]);
      setStats({
        zones:    Array.isArray(s.zones)    ? s.zones    : [],
        lines:    Array.isArray(s.lines)    ? s.lines    : [],
        machines: Array.isArray(s.machines) ? s.machines : [],
      });
      setHistory({
        rows:  Array.isArray(h.rows) ? h.rows : [],
        total: h.total || 0,
      });
    } finally { setLoading(false); }
  }, [token, days, fromDate, toDate, stateFilter, zoneFilter, lineFilter, machineFilter]);
  useEffect(() => { reload(); }, [reload]);

  // ── Top-level KPI tiles (aggregate over the whole window) ────────────
  const top = useMemo(() => {
    const closed = history.rows.filter(r => r.duration_seconds && r.ended_at);
    const totalCount = history.rows.length;
    const closedCount = closed.length;
    const avgMttrMin = closedCount
      ? closed.reduce((a, r) => a + (r.duration_seconds / 60), 0) / closedCount
      : null;
    const lttrMin = closedCount
      ? closed.reduce((m, r) => Math.max(m, r.duration_seconds / 60), 0)
      : null;
    // MTBF (window basis): if N >= 2, mean inter-arrival time
    let mtbfHours = null;
    if (history.rows.length >= 2) {
      const sorted = [...history.rows].sort((a,b) =>
        new Date(a.started_at).getTime() - new Date(b.started_at).getTime());
      const span = (new Date(sorted[sorted.length-1].started_at).getTime()
                 -  new Date(sorted[0].started_at).getTime()) / 3600000;
      mtbfHours = span / Math.max(1, sorted.length - 1);
    }
    return { totalCount, closedCount, avgMttrMin, lttrMin, mtbfHours };
  }, [history.rows]);

  // ── Search across history rows (client-side text filter) ─────────────
  const filteredRows = useMemo(() => {
    if (!search.trim()) return history.rows;
    const q = search.toLowerCase();
    return history.rows.filter(r =>
      [r.line_name, r.zone_name, r.shift_name, r.state, r.reason,
       r.production_data?.machine_no, r.production_data?.machine_name,
       r.production_data?.line_leader_name,
       r.maintenance_data?.bd_attended_by]
        .filter(Boolean)
        .some(v => String(v).toLowerCase().includes(q))
    );
  }, [history.rows, search]);

  // Distinct lookup lists for filters
  const zones    = useMemo(() => Array.from(new Map(history.rows
                     .filter(r => r.zone_id).map(r => [r.zone_id, { id: r.zone_id, name: r.zone_name }])).values()),
                   [history.rows]);
  const lines    = useMemo(() => Array.from(new Map(history.rows
                     .filter(r => r.line_id).map(r => [r.line_id, { id: r.line_id, name: r.line_name }])).values()),
                   [history.rows]);
  const machines = useMemo(() => Array.from(new Set(history.rows
                     .map(r => r.production_data?.machine_no).filter(Boolean))).sort(),
                   [history.rows]);

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@600;700;800&display=swap');
        .mh-root { min-height:100vh; background:#f8fafc; font-family:'Barlow',sans-serif; padding-bottom:60px; }
        .mh-topbar {
          background:#fff; border-bottom:1px solid #e2e8f0;
          padding:0 40px 0 88px; height:60px;
          display:flex; align-items:center; justify-content:space-between;
          position:sticky; top:0; z-index:100;
          box-shadow:0 1px 3px rgba(0,0,0,.06);
        }
        .mh-topbar::after { content:''; position:absolute; bottom:0; left:0; right:0;
                             height:2px; background:${theme.gradient}; }
        .mh-title { position:absolute; left:50%; transform:translateX(-50%);
                     font-family:'Barlow Condensed',sans-serif;
                     font-size:34px; font-weight:800; color:#0f172a;
                     letter-spacing:-.01em; pointer-events:none; white-space:nowrap; }
        .mh-title span { color:${theme.accent}; }
        .mh-user-pill {
          display:flex; align-items:center; gap:10px;
          padding:6px 14px; border-radius:99px;
          border:1.5px solid #e2e8f0; background:#f8fafc;
          font-size:12px; font-weight:600; color:#334155; white-space:nowrap;
        }
        .mh-user-pill b { color:#0f172a; font-weight:800; }
        .mh-body { padding:24px 40px 0; max-width:1280px; margin:0 auto; }
        .mh-section { margin-bottom:22px; }
        .mh-section h3 { margin:0 0 10px; font-family:'Barlow Condensed',sans-serif;
                          font-size:18px; font-weight:800; color:#0f172a;
                          letter-spacing:.02em; text-transform:uppercase; }
        .mh-tiles { display:flex; gap:14px; flex-wrap:wrap; margin-bottom:18px; }
        .mh-card { background:#fff; border:1px solid #e2e8f0; border-radius:12px;
                    box-shadow:0 1px 3px rgba(0,0,0,.04); overflow:hidden; }
        .mh-pillrow { display:flex; gap:6px; flex-wrap:wrap; }
        .mh-pill { padding:6px 12px; border-radius:99px; font-size:12px; font-weight:700;
                    cursor:pointer; user-select:none; border:1.5px solid #e2e8f0;
                    background:#fff; color:#475569; transition:all .12s; }
        .mh-pill.active { border-color:${theme.accent}; color:${theme.accent}; background:${theme.soft}; }
        .mh-input { padding:7px 11px; border-radius:8px; border:1.5px solid #e2e8f0;
                     font-size:13px; font-family:inherit; background:#fff;
                     color:#0f172a; outline:none; }
        .mh-input:focus { border-color:${theme.accent}; }
        .mh-th { padding:10px 14px; text-align:left; font-size:10px; font-weight:700;
                  letter-spacing:.08em; text-transform:uppercase; color:#64748b;
                  border-bottom:2px solid #e2e8f0; white-space:nowrap; }
        .mh-td { padding:11px 14px; font-size:12px; color:#0f172a; vertical-align:middle; }
      `}</style>

      <div className="mh-root">
        <div className="mh-topbar">
          <div /> {/* logo placeholder */}
          <div className="mh-title">
            {isAdmin ? "Maintenance " : ""}<span>Historical Data</span>
          </div>
          {user?.username && (
            <div className="mh-user-pill">
              Signed in as <b>{user.username}</b>
            </div>
          )}
        </div>

        <div className="mh-body">

          {/* ── KPI tiles ─────────────────────────────────── */}
          <div className="mh-tiles">
            <Tile label="Breakdowns"   value={top.totalCount}                          color="#1e40af" sub={`${top.closedCount} closed`}/>
            <Tile label="MTBF"         value={fmt(top.mtbfHours, 2)} sub="hours"        color="#16a34a"/>
            <Tile label="MTTR"         value={fmt(top.avgMttrMin, 1)} sub="minutes"     color="#b45309"/>
            <Tile label="LTTR"         value={fmt(top.lttrMin, 0)}    sub="worst minutes" color="#dc2626"/>
            <Tile label="Window"
                   value={(fromDate || toDate)
                     ? `${fromDate || "…"} → ${toDate || "today"}`
                     : `${days}d`}
                   sub={(fromDate || toDate) ? "custom range" : "from today"}
                   color="#7c3aed"/>
          </div>

          {/* ── Filter bar ────────────────────────────────── */}
          <div style={{ display:"flex", gap:10, flexWrap:"wrap", alignItems:"center",
                          marginBottom:18 }}>
            <select className="mh-input" value={days}
                    onChange={e => { setDays(Number(e.target.value)); setFromDate(""); setToDate(""); }}
                    title={(fromDate || toDate) ? "Disabled while custom date range is set" : ""}
                    style={{ opacity: (fromDate || toDate) ? 0.55 : 1 }}>
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
              <option value={180}>Last 180 days</option>
              <option value={365}>Last 1 year</option>
              <option value={730}>Last 2 years</option>
            </select>

            {/* Custom date range — overrides the preset above when either is set */}
            <div style={{ display:"flex", alignItems:"center", gap: 6 }}>
              <span style={{ fontSize:11, color:"#64748b", fontWeight:600 }}>From</span>
              <input className="mh-input" type="date"
                     value={fromDate}
                     onChange={e => setFromDate(e.target.value)}
                     style={{ width: 158 }}/>
              <span style={{ fontSize:11, color:"#64748b", fontWeight:600 }}>To</span>
              <input className="mh-input" type="date"
                     value={toDate}
                     onChange={e => setToDate(e.target.value)}
                     style={{ width: 158 }}/>
              {(fromDate || toDate) && (
                <button onClick={() => { setFromDate(""); setToDate(""); }}
                        title="Clear custom dates and use the preset window"
                        style={{ background:"#fff", color:"#64748b",
                                  border:"1.5px solid #e2e8f0",
                                  padding:"6px 10px", borderRadius:8,
                                  fontSize:11, fontWeight:700, cursor:"pointer" }}>
                  Clear
                </button>
              )}
            </div>

            <select className="mh-input" value={stateFilter} onChange={e => setStateFilter(e.target.value)}>
              <option value="">All states</option>
              <option value="OPEN">Open</option>
              <option value="RESOLVED">Resolved (pending closure)</option>
              <option value="CLOSED">Closed</option>
            </select>
            <select className="mh-input" value={stateFilter} onChange={e => setStateFilter(e.target.value)}>
              <option value="">All states</option>
              <option value="OPEN">Open</option>
              <option value="RESOLVED">Resolved (pending closure)</option>
              <option value="CLOSED">Closed</option>
            </select>
            <select className="mh-input" value={zoneFilter} onChange={e => setZoneFilter(e.target.value)}>
              <option value="">All zones</option>
              {zones.map(z => <option key={z.id} value={z.id}>{z.name || `Zone ${z.id}`}</option>)}
            </select>
            <select className="mh-input" value={lineFilter} onChange={e => setLineFilter(e.target.value)}>
              <option value="">All lines</option>
              {lines.map(l => <option key={l.id} value={l.id}>{l.name || `Line ${l.id}`}</option>)}
            </select>
            <select className="mh-input" value={machineFilter} onChange={e => setMachineFilter(e.target.value)}>
              <option value="">All machines</option>
              {machines.map(m => <option key={m} value={m}>#{m}</option>)}
            </select>
            <input className="mh-input" placeholder="🔍 Search line / shift / operator…"
                   style={{ minWidth: 260, flex: 1 }}
                   value={search} onChange={e => setSearch(e.target.value)}/>
          </div>

          {/* ── Roll-up tables (Zone / Line / Machine) ────── */}
          <div className="mh-section">
            <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between",
                            marginBottom:10, flexWrap:"wrap", gap:10 }}>
              <h3 style={{ margin:0 }}>KPI Roll-up</h3>
              <div className="mh-pillrow">
                {[
                  { k: "zone",    label: `Zones (${stats.zones.length})` },
                  { k: "line",    label: `Lines (${stats.lines.length})` },
                  { k: "machine", label: `Machines (${stats.machines.length})` },
                ].map(t => (
                  <div key={t.k}
                       className={`mh-pill${rollup === t.k ? " active" : ""}`}
                       onClick={() => setRollup(t.k)}>
                    {t.label}
                  </div>
                ))}
              </div>
            </div>

            <div className="mh-card">
              {rollup === "zone" && (
                <RollTable cols={["Zone", "Breakdowns", "LTTR (min)"]}
                           rows={stats.zones.map(z => [
                             z.zone_name || `Zone ${z.zone_id}`,
                             z.breakdowns_count ?? 0,
                             fmt(z.lttr_minutes, 1),
                           ])}
                           empty="No breakdowns recorded for any zone in this window."/>
              )}
              {rollup === "line" && (
                <RollTable cols={["Line", "Zone", "Breakdowns", "MTBF (hrs)", "MTTR (min)"]}
                           rows={stats.lines.map(l => [
                             l.line_name || `Line ${l.line_id}`,
                             l.zone_name || "—",
                             l.breakdowns_count ?? 0,
                             fmt(l.mtbf_hours, 2),
                             fmt(l.mttr_minutes, 1),
                           ])}
                           empty="No breakdowns recorded for any line in this window."/>
              )}
              {rollup === "machine" && (
                <RollTable cols={["Machine #", "Machine Name", "Line", "Zone",
                                  "Breakdowns", "MTBF (hrs)", "MTTR (min)", "LTTR (min)"]}
                           rows={stats.machines.map(m => [
                             `#${m.machine_no}`,
                             m.machine_name || "—",
                             m.line_name    || `Line ${m.line_id}`,
                             m.zone_name    || "—",
                             m.breakdowns_count ?? 0,
                             fmt(m.mtbf_hours, 2),
                             fmt(m.mttr_minutes, 1),
                             fmt(m.lttr_minutes, 1),
                           ])}
                           empty="No machine-tagged breakdowns yet — Production must enter Machine No. on the slip first."/>
              )}
            </div>
          </div>

          {/* ── Slip archive ──────────────────────────────── */}
          <div className="mh-section">
            <h3>Slip Archive
              <span style={{ fontSize:11, fontWeight:600, color:"#94a3b8",
                              letterSpacing:0, textTransform:"none", marginLeft:8 }}>
                · {filteredRows.length} of {history.total} match{filteredRows.length===1?"":"es"}
              </span>
            </h3>
            <div className="mh-card">
              {loading ? (
                <div style={{ padding: 60, textAlign:"center", color:"#94a3b8" }}>
                  Loading…
                </div>
              ) : filteredRows.length === 0 ? (
                <div style={{ padding: 60, textAlign:"center", color:"#94a3b8", fontStyle:"italic" }}>
                  No breakdown slips match the current filters.
                </div>
              ) : (
                <div style={{ overflowX:"auto" }}>
                  <table style={{ width:"100%", borderCollapse:"collapse" }}>
                    <thead>
                      <tr>
                        {["#", "Started", "Line", "Zone", "Shift", "Machine",
                          "Duration", "State", "Prod", "Maint", ""].map(h => (
                          <th key={h} className="mh-th">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {filteredRows.map(r => {
                        const machineNo   = r.production_data?.machine_no;
                        const machineName = r.production_data?.machine_name;
                        return (
                          <tr key={r.id} style={{ borderBottom:"1px solid #f1f5f9" }}>
                            <td className="mh-td" style={{ fontFamily:"monospace", color:"#94a3b8" }}>
                              #{r.id}
                            </td>
                            <td className="mh-td" style={{ whiteSpace:"nowrap", fontFamily:"monospace" }}>
                              {fmtDateTime(r.started_at)}
                            </td>
                            <td className="mh-td" style={{ fontWeight:700 }}>
                              {r.line_name || `Line ${r.line_id}`}
                            </td>
                            <td className="mh-td" style={{ color:"#475569" }}>
                              {r.zone_name || "—"}
                            </td>
                            <td className="mh-td" style={{ fontFamily:"monospace" }}>
                              {r.shift_name || "—"}{r.serial_in_shift ? `·#${r.serial_in_shift}` : ""}
                            </td>
                            <td className="mh-td" style={{ fontFamily:"monospace" }}>
                              {machineNo
                                ? <span title={machineName || ""}>#{machineNo}{machineName ? ` · ${machineName.slice(0,28)}${machineName.length>28?'…':''}` : ""}</span>
                                : <span style={{ color:"#cbd5e1" }}>—</span>}
                            </td>
                            <td className="mh-td" style={{ fontFamily:"monospace", color:"#0f172a", fontWeight:700 }}>
                              {fmtDuration(r.duration_seconds)}
                            </td>
                            <td className="mh-td"><StateBadge state={r.state}/></td>
                            <td className="mh-td">
                              {r.production_filled_at
                                ? <span style={{ color:"#16a34a", fontWeight:700, fontSize:11 }}>✓ filled</span>
                                : <span style={{ color:"#dc2626", fontWeight:700, fontSize:11 }}>pending</span>}
                            </td>
                            <td className="mh-td">
                              {r.maintenance_filled_at
                                ? <span style={{ color:"#16a34a", fontWeight:700, fontSize:11 }}>✓ filled</span>
                                : <span style={{ color:"#dc2626", fontWeight:700, fontSize:11 }}>pending</span>}
                            </td>
                            <td className="mh-td">
                              <button onClick={() => setViewModal(r)}
                                      style={{ background:"#fff", color: theme.accent,
                                                border:`1.5px solid ${theme.accent}`,
                                                padding:"4px 11px", borderRadius:7,
                                                fontWeight:700, fontSize:11,
                                                cursor:"pointer" }}>
                                View Slip
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
          </div>
        </div>
      </div>

      {/* Read-only slip viewer (reuses ClosureFormModal) */}
      {viewModal && (
        <ClosureFormModal
          ticket={viewModal}
          mode="view"
          phase="maintenance"
          token={token}
          onClose={() => setViewModal(null)}
          onSave={() => {}}
        />
      )}
    </>
  );
}

/* ── Roll-up table primitive (same look for zone / line / machine) ── */
function RollTable({ cols, rows, empty }) {
  if (!rows.length) {
    return <div style={{ padding: 50, textAlign:"center",
                          color:"#94a3b8", fontStyle:"italic" }}>{empty}</div>;
  }
  return (
    <div style={{ overflowX:"auto" }}>
      <table style={{ width:"100%", borderCollapse:"collapse" }}>
        <thead>
          <tr>{cols.map(c => <th key={c} className="mh-th">{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} style={{ borderBottom:"1px solid #f1f5f9" }}>
              {row.map((v, j) => (
                <td key={j} className="mh-td"
                     style={{ fontWeight: j===0 ? 700 : 500,
                               fontFamily: j>=2 ? "monospace" : "inherit",
                               color: j===0 ? "#0f172a" : "#475569" }}>
                  {v}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
