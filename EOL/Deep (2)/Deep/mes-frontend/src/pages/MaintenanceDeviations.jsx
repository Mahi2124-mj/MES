/* ───────────────────────────────────────────────────────────────────
 * MaintenanceDeviations.jsx
 * ───────────────────────────────────────────────────────────────────
 * Maintenance department's standalone Deviations page.
 *
 *   • Top: KPI tiles (Pending QA / Approved / Extended / Closed / Rejected)
 *   • "+ Raise Deviation" launches a blank Deviation Form modal.
 *     No breakdown link required — Maintenance can file an independent
 *     deviation any time the line needs >24 h to fix something.
 *   • Filterable list of all deviations Maintenance has raised, with
 *     live status badges (PENDING_QA → APPROVED / REJECTED / EXTENDED
 *     → CLOSED).  Click any row to View / Re-edit (re-edit allowed
 *     only while status is PENDING_QA).
 *   • Quality user sees the same row in their dashboard's Deviations
 *     tab and decides Approve / Reject from there.
 */
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";

const API = "";
const api = {
  async get(path, token) {
    const r = await fetch(API + path, { headers: { Authorization: `Bearer ${token}` } });
    if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
    return r.json();
  },
};

function fmtAgo(ts) {
  if (!ts) return "—";
  const ms = Date.now() - new Date(ts).getTime();
  const s = Math.floor(ms / 1000);
  if (s < 60)   return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60)   return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24)   return `${h}h ${m % 60}m ago`;
  const d = Math.floor(h / 24);
  return `${d}d ${h % 24}h ago`;
}

function fmtDate(ts) {
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

function StatusPill({ status }) {
  const map = {
    PENDING_QA: { bg: "rgba(202,138,4,.12)",  color: "#a16207", label: "Awaiting QA" },
    APPROVED:   { bg: "rgba(22,163,74,.12)",  color: "#15803d", label: "Approved" },
    REJECTED:   { bg: "rgba(220,38,38,.12)",  color: "#b91c1c", label: "Rejected" },
    EXTENDED:   { bg: "rgba(8,145,178,.12)",  color: "#0e7490", label: "Extended" },
    CLOSED:     { bg: "rgba(71,85,105,.12)",  color: "#475569", label: "Closed" },
  };
  const m = map[status] || { bg: "#f1f5f9", color: "#64748b", label: status || "—" };
  return (
    <span style={{ padding: "2px 9px", borderRadius: 99, fontSize: 10, fontWeight: 700,
                    background: m.bg, color: m.color, whiteSpace: "nowrap" }}>
      {m.label}
    </span>
  );
}

export default function MaintenanceDeviations() {
  const { token, theme, isAdmin, user } = useAuth();
  const [deviations, setDeviations] = useState([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch]             = useState("");
  const [loading, setLoading]           = useState(true);
  const [open, setOpen]                 = useState(null);   // current deviation in modal
  const [DeviationFormModal, setDeviationFormModal] = useState(null);

  // Lazy-load the form modal so the page paints fast.
  useEffect(() => {
    let alive = true;
    import("./DeviationForm").then(m => {
      if (alive) setDeviationFormModal(() => m.default);
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  // Reload — `silent=true` skips the loading spinner so background
  // 12-sec polls don't flash "Loading..." over the table every tick.
  // Spinner only on first mount.
  const reload = useCallback(async (silent = false) => {
    if (!token) return;
    if (!silent) setLoading(true);
    try {
      const list = await api.get("/api/quality/deviations?days=180", token).catch(() => []);
      setDeviations(Array.isArray(list) ? list : []);
    } finally { if (!silent) setLoading(false); }
  }, [token]);

  useEffect(() => {
    reload(false);                                       // first paint
    const t = setInterval(() => reload(true), 12000);    // silent refresh
    const onChange = () => reload(true);
    window.addEventListener("ap-config-changed", onChange);
    return () => { clearInterval(t); window.removeEventListener("ap-config-changed", onChange); };
  }, [reload]);

  useEffect(() => {
    document.title = isAdmin ? "Maintenance Deviations" : "Deviations";
  }, [isAdmin]);

  // Filtered list
  const visible = deviations.filter(d => {
    if (statusFilter && d.status !== statusFilter) return false;
    if (search.trim()) {
      const q = search.toLowerCase();
      const blob = [
        d.dev_no, d.line_name, d.zone_name, d.machine_no, d.machine_name,
        d.process_name, d.reason, d.requirement, d.observation,
        d.raised_by_username, d.initiated_by,
      ].map(s => String(s ?? "").toLowerCase()).join(" | ");
      if (!blob.includes(q)) return false;
    }
    return true;
  });

  // KPI counts derived from the list (saves a round-trip)
  const kpi = deviations.reduce((acc, d) => {
    if (d.status === "PENDING_QA") acc.pending++;
    if (d.status === "APPROVED")   acc.approved++;
    if (d.status === "REJECTED")   acc.rejected++;
    if (d.status === "EXTENDED")   acc.extended++;
    if (d.status === "CLOSED")     acc.closed++;
    return acc;
  }, { pending:0, approved:0, rejected:0, extended:0, closed:0 });

  // Open blank form to raise a new deviation
  const raiseNew = () => setOpen({ _new: true });

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@600;700;800&display=swap');
        .md-root  { min-height:100vh; background:#f8fafc; font-family:'Barlow',sans-serif; padding-bottom:48px; }
        .md-topbar{
          background:#fff; border-bottom:1px solid #e2e8f0;
          padding:0 40px 0 88px; height:60px;
          display:flex; align-items:center; justify-content:space-between;
          position:sticky; top:0; z-index:50; box-shadow:0 1px 3px rgba(0,0,0,.06);
        }
        .md-topbar::after{ content:''; position:absolute; bottom:0; left:0; right:0;
                            height:2px; background:${theme.gradient}; }
        .md-title { position:absolute; left:50%; transform:translateX(-50%);
                     font-family:'Barlow Condensed',sans-serif;
                     font-size:34px; font-weight:800; color:#0f172a;
                     letter-spacing:-.01em; pointer-events:none; white-space:nowrap; }
        .md-title span { color:${theme.accent}; }
        .md-pill {
          display:flex; align-items:center; gap:10px;
          padding:6px 14px; border-radius:99px;
          border:1.5px solid #e2e8f0; background:#f8fafc;
          font-size:12px; font-weight:600; color:#334155; white-space:nowrap;
        }
        .md-pill b { color:#0f172a; font-weight:800; }
        .md-body { padding:24px 40px 0; max-width:1280px; margin:0 auto; }
        .md-tiles{ display:flex; gap:14px; flex-wrap:wrap; margin-bottom:18px; }
        .md-card { background:#fff; border:1px solid #e2e8f0; border-radius:12px;
                    box-shadow:0 1px 3px rgba(0,0,0,.04); }
        .md-th   { padding:10px 14px; text-align:left; font-size:10px; font-weight:700;
                    letter-spacing:.08em; text-transform:uppercase; color:#64748b;
                    border-bottom:2px solid #e2e8f0; white-space:nowrap; background:#f8fafc; }
        .md-td   { padding:11px 14px; font-size:12px; color:#0f172a; vertical-align:middle; }
        .md-input { padding:8px 11px; border-radius:8px; border:1.5px solid #e2e8f0;
                     font-size:13px; font-family:inherit; background:#fff; outline:none; }
        .md-btn-primary { padding:9px 18px; border-radius:8px; border:none;
                          background:linear-gradient(135deg,${theme.accentDark},${theme.accent});
                          color:#fff; font-weight:800; font-size:13px; cursor:pointer;
                          box-shadow:0 4px 14px ${theme.soft}; white-space:nowrap; }
        .md-btn-view { padding:5px 12px; border-radius:7px; border:1.5px solid ${theme.accent};
                       background:#fff; color:${theme.accent}; font-weight:700; font-size:11px;
                       cursor:pointer; }
        .md-empty { padding:60px 20px; text-align:center; color:#94a3b8; font-style:italic; }
      `}</style>

      <div className="md-root">
        <div className="md-topbar">
          <div /> {/* logo placeholder */}
          <div className="md-title">
            {isAdmin ? "Maintenance " : ""}<span>Deviations</span>
          </div>
          {user?.username && (
            <div className="md-pill">Signed in as <b>{user.username}</b></div>
          )}
        </div>

        <div className="md-body">

          {/* KPI tiles + Raise button on the right */}
          <div className="md-tiles" style={{alignItems:"center"}}>
            <Tile label="Awaiting QA" value={kpi.pending}  sub="Quality Sec Head review"
                   color="#a16207"/>
            <Tile label="Approved"    value={kpi.approved} color="#16a34a"/>
            <Tile label="Extended"    value={kpi.extended} color="#0e7490"/>
            <Tile label="Closed"      value={kpi.closed}   color="#475569"/>
            <Tile label="Rejected"    value={kpi.rejected} color="#dc2626"/>
            <div style={{flex:"1 1 auto"}}/>
            <button onClick={raiseNew} className="md-btn-primary">
              + Raise Deviation
            </button>
          </div>

          {/* Filter row */}
          <div className="md-card" style={{padding:14, marginBottom:14, display:"flex",
                                            gap:12, alignItems:"flex-end", flexWrap:"wrap"}}>
            <div style={{display:"flex", flexDirection:"column", gap:5, minWidth:160}}>
              <label style={{fontSize:10, fontWeight:700, color:"#64748b",
                              letterSpacing:".08em", textTransform:"uppercase"}}>Status</label>
              <select className="md-input" value={statusFilter}
                       onChange={e => setStatusFilter(e.target.value)}>
                <option value="">All</option>
                <option value="PENDING_QA">Awaiting QA</option>
                <option value="APPROVED">Approved</option>
                <option value="EXTENDED">Extended</option>
                <option value="REJECTED">Rejected</option>
                <option value="CLOSED">Closed</option>
              </select>
            </div>
            <div style={{display:"flex", flexDirection:"column", gap:5, flex:1, minWidth:200}}>
              <label style={{fontSize:10, fontWeight:700, color:"#64748b",
                              letterSpacing:".08em", textTransform:"uppercase"}}>Search</label>
              <input className="md-input" type="text"
                      placeholder="Dev no., line, machine, problem…"
                      value={search} onChange={e => setSearch(e.target.value)}/>
            </div>
            <div style={{fontSize:11, color:"#94a3b8", marginLeft:"auto"}}>
              {visible.length} of {deviations.length} shown · last 180 days
            </div>
          </div>

          {/* List */}
          <div className="md-card">
            {loading ? <div className="md-empty">Loading…</div>
             : visible.length === 0 ? (
              <div className="md-empty">
                {deviations.length === 0
                  ? "No deviations raised yet.  Click + Raise Deviation to file the first one."
                  : "No deviations match the current filters."}
              </div>
            ) : (
              <div style={{overflowX:"auto"}}>
                <table style={{width:"100%", borderCollapse:"collapse"}}>
                  <thead><tr>
                    {["Dev No.", "Raised", "Line / Zone", "Machine",
                      "Process / Reason", "Qty / Upto",
                      "Status", "QA Note", ""].map(h =>
                      <th key={h} className="md-th">{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {visible.map(d => (
                      <tr key={d.id} style={{borderBottom:"1px solid #f1f5f9"}}>
                        <td className="md-td" style={{fontFamily:"monospace", fontWeight:700,
                                                       color: theme.accentDark}}>
                          {d.dev_no || `#${d.id}`}
                        </td>
                        <td className="md-td">
                          <div style={{fontFamily:"monospace"}}>{fmtDate(d.created_at)}</div>
                          <div style={{fontSize:10, color:"#94a3b8"}}>{fmtAgo(d.created_at)}</div>
                        </td>
                        <td className="md-td">
                          <div style={{fontWeight:700}}>{d.line_name || `Line ${d.line_id}`}</div>
                          <div style={{fontSize:10, color:"#94a3b8"}}>{d.zone_name || "—"}</div>
                        </td>
                        <td className="md-td" style={{fontFamily:"monospace"}}>
                          {d.machine_no
                            ? <>#{d.machine_no} <span style={{color:"#94a3b8"}}>· {d.machine_name || ""}</span></>
                            : "—"}
                        </td>
                        <td className="md-td" style={{maxWidth:240}}>
                          <div style={{fontWeight:600, whiteSpace:"nowrap",
                                         overflow:"hidden", textOverflow:"ellipsis"}}>
                            {d.process_name || "—"}
                          </div>
                          <div style={{fontSize:10, color:"#64748b", whiteSpace:"nowrap",
                                         overflow:"hidden", textOverflow:"ellipsis"}}
                               title={d.reason || ""}>
                            {d.reason || "—"}
                          </div>
                        </td>
                        <td className="md-td" style={{fontFamily:"monospace"}}>
                          {d.deviation_qty || 0} / {d.deviation_upto_qty || "—"}
                          {d.deviation_upto_date && (
                            <div style={{fontSize:10, color:"#94a3b8"}}>
                              till {d.deviation_upto_date}
                            </div>
                          )}
                        </td>
                        <td className="md-td"><StatusPill status={d.status}/></td>
                        <td className="md-td" style={{maxWidth:180}}>
                          <div style={{fontSize:11, color:"#475569",
                                         whiteSpace:"nowrap", overflow:"hidden",
                                         textOverflow:"ellipsis"}}
                               title={d.hod_quality_note || ""}>
                            {d.hod_quality_note || "—"}
                          </div>
                          {d.approved_by_username && (
                            <div style={{fontSize:10, color:"#94a3b8"}}>
                              {d.approved_by_username} · {fmtAgo(d.approved_at)}
                            </div>
                          )}
                        </td>
                        <td className="md-td">
                          <button className="md-btn-view"
                                   onClick={() => setOpen(d)}>
                            {d.status === "PENDING_QA" ? "Edit" : "View"}
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
      </div>

      {/* Deviation Form modal — raise / view / re-edit
          - _new=true → raise mode
          - PENDING_QA → raise mode (still editable by Maintenance)
          - APPROVED / REJECTED / EXTENDED / CLOSED → view (read-only) */}
      {open && DeviationFormModal && (
        <DeviationFormModal
          deviation={open._new ? {} : open}
          token={token}
          mode={open._new || open.status === "PENDING_QA" ? "raise" : "view"}
          onClose={() => setOpen(null)}
          onSaved={() => { setOpen(null); reload(); }}
        />
      )}
    </>
  );
}
