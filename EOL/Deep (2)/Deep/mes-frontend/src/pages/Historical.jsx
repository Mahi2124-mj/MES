import { useState, useEffect, useRef, Fragment } from "react";
import axios from "axios";
import AIAssistant from "../components/AIAssistant";
import { useAuth } from "../context/AuthContext";

const api = axios.create({ baseURL: "" });
api.interceptors.request.use(cfg => {
  const t = sessionStorage.getItem("mes_token");
  if (t) cfg.headers.Authorization = `Bearer ${t}`;
  return cfg;
});
// 401 → wipe session + bounce to /login.
api.interceptors.response.use(r => r, err => {
  if (err?.response?.status === 401) {
    try {
      ["mes_token","mes_username","user_role","user_id","user_dept_slug"]
        .forEach(k => sessionStorage.removeItem(k));
    } catch {}
    if (window.location.pathname !== "/login") window.location.replace("/login");
  }
  return Promise.reject(err);
});

function fmtSec(s) {
  if (!s && s !== 0) return "—";
  s = parseInt(s);
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), sec = s%60;
  return [h,m,sec].map(v=>String(v).padStart(2,"0")).join(":");
}

const LOSS_CATS = [
  { key:"breakdown",   label:"Breakdown", color:"#ef4444" },
  { key:"quality",     label:"Quality",   color:"#f97316" },
  { key:"material",    label:"Material",  color:"#eab308" },
  { key:"setup",       label:"Setup",     color:"#84cc16" },
  { key:"change_over", label:"C/O",       color:"#06b6d4" },
  { key:"speed",       label:"Speed",     color:"#3b82f6" },
  { key:"others",      label:"Others",    color:"#8b5cf6" },
];

// ── Process History Panel ───────────────────────────────────
// Renders the full per-process detail for a single main-line cycle.
// 2026-05-18 — Surfaces every sub-machine cycle, PY event, and SA
// data capture that ran in the SAME wall-clock window as the main
// cycle so operator can audit "for this part, what happened at each
// station".  Backend (lines.py /part-search) does the windowing and
// hands us pre-grouped arrays per row.
function ProcessHistoryPanel({ row }) {
  const fmt = (iso) => {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleTimeString("en-IN",
        { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch { return "—"; }
  };
  const sub = row.sub_cycles || [];
  const py  = row.py_events  || [];
  const sa  = row.sa_data    || [];

  // Group sub-machine cycles by machine (one section per machine)
  const subByMachine = {};
  sub.forEach(s => {
    const k = s.machine_name || "Unknown";
    if (!subByMachine[k]) subByMachine[k] = { seq: s.machine_seq, rows: [] };
    subByMachine[k].rows.push(s);
  });

  // Light reusable styles
  const labelStyle = { fontSize:10, fontWeight:800, color:"#1e40af",
                       letterSpacing:".08em", textTransform:"uppercase",
                       marginBottom:6 };
  const cardStyle  = { background:"#fff", border:"1px solid #e2e8f0",
                       borderRadius:8, padding:"10px 12px" };
  const cellMono   = { fontFamily:"monospace", fontSize:11 };

  return (
    <div style={{ display:"grid", gap:10,
                  gridTemplateColumns:"repeat(auto-fit, minmax(280px, 1fr))" }}>
      {/* Sub-machine cycles — grouped per machine */}
      <div style={cardStyle}>
        <div style={labelStyle}>🛠 Sub-Machine Cycles ({sub.length})</div>
        {sub.length === 0 ? (
          <div style={{ fontSize:11, color:"#94a3b8", fontStyle:"italic" }}>
            No sub-machine activity in this cycle's window.
          </div>
        ) : (
          Object.entries(subByMachine).map(([name, info]) => (
            <div key={name} style={{ marginBottom:8 }}>
              <div style={{ fontSize:11, fontWeight:700, color:"#0f172a",
                            display:"flex", alignItems:"center", gap:6 }}>
                {info.seq != null && (
                  <span style={{ fontSize:10, padding:"1px 6px",
                                 borderRadius:99, fontWeight:800,
                                 color:"#3b82f6", background:"rgba(59,130,246,.12)",
                                 border:"1px solid rgba(59,130,246,.3)" }}>
                    M-{info.seq}
                  </span>
                )}
                {name}
              </div>
              <table style={{ width:"100%", marginTop:4, fontSize:11 }}>
                <tbody>
                  {info.rows.map((s, idx) => (
                    <tr key={idx} style={{ borderTop:idx>0?"1px solid #f1f5f9":"none" }}>
                      <td style={{ ...cellMono, padding:"2px 6px", color:"#64748b", width:50 }}>#{s.cycle_seq}</td>
                      <td style={{ ...cellMono, padding:"2px 6px", color:"#0f172a" }}>{fmt(s.ts_start)} → {fmt(s.ts_end)}</td>
                      <td style={{ ...cellMono, padding:"2px 6px", fontWeight:800,
                                   color: s.ct_seconds > (row.ideal_ct || 15) ? "#dc2626" : "#16a34a",
                                   textAlign:"right" }}>
                        {Number(s.ct_seconds).toFixed(2)}s
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))
        )}
      </div>

      {/* Poka-yoke events */}
      <div style={cardStyle}>
        <div style={{ ...labelStyle, color: py.length > 0 ? "#dc2626" : "#16a34a" }}>
          {py.length > 0 ? "⚠" : "✓"} Poka-Yoke ({py.length})
        </div>
        {py.length === 0 ? (
          <div style={{ fontSize:11, color:"#94a3b8", fontStyle:"italic" }}>
            All PYs OK during this cycle.
          </div>
        ) : (
          <table style={{ width:"100%", fontSize:11 }}>
            <tbody>
              {py.map((p, idx) => (
                <tr key={idx} style={{ borderTop: idx>0 ? "1px solid #f1f5f9" : "none" }}>
                  <td style={{ padding:"3px 6px", fontSize:14 }}>
                    {p.alert_level === "CRITICAL" ? "🚨" : "⚠"}
                  </td>
                  <td style={{ padding:"3px 6px" }}>
                    <div style={{ fontWeight:700, color:"#0f172a" }}>{p.py_name || p.py_no}</div>
                    <div style={cellMono} className="muted">
                      <span style={{ color:"#dc2626" }}>{p.actual}</span>
                      <span style={{ color:"#94a3b8" }}> / expected </span>
                      <span style={{ color:"#16a34a" }}>{p.expected}</span>
                    </div>
                  </td>
                  <td style={{ ...cellMono, padding:"3px 6px", color:"#64748b",
                               whiteSpace:"nowrap", textAlign:"right" }}>
                    {fmt(p.detected_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Semi-Auto data captures */}
      <div style={cardStyle}>
        <div style={labelStyle}>📊 Semi-Auto Data ({sa.length})</div>
        {sa.length === 0 ? (
          <div style={{ fontSize:11, color:"#94a3b8", fontStyle:"italic" }}>
            No Semi-Auto captures in this cycle's window.
          </div>
        ) : (
          sa.map((s, idx) => {
            const names = s.register_names || [];
            const values = Array.isArray(s.values) ? s.values : [];
            return (
              <div key={idx} style={{ marginBottom:8, paddingBottom:6,
                                       borderBottom: idx < sa.length-1 ? "1px solid #f1f5f9" : "none" }}>
                <div style={{ display:"flex", justifyContent:"space-between",
                              alignItems:"baseline", marginBottom:3 }}>
                  <span style={{ fontSize:11, fontWeight:700, color:"#0f172a" }}>
                    {s.machine_name}
                  </span>
                  <span style={{ ...cellMono, color:"#64748b" }}>{fmt(s.ts_plc)}</span>
                </div>
                {s.part_code && (
                  <div style={{ ...cellMono, color:"#1e40af", fontWeight:600, marginBottom:3 }}>
                    {s.part_code}
                  </div>
                )}
                {/* Show first 6 register name+value pairs to stay compact */}
                {values.length > 0 && (
                  <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr",
                                gap:"1px 8px", fontSize:10 }}>
                    {values.slice(0, 8).map((v, vi) => (
                      <div key={vi} style={{ display:"flex", justifyContent:"space-between" }}>
                        <span style={{ color:"#64748b" }}>{names[vi] || `R${vi+1}`}</span>
                        <span style={cellMono}>{v}</span>
                      </div>
                    ))}
                    {values.length > 8 && (
                      <div style={{ gridColumn:"1/-1", color:"#94a3b8", fontStyle:"italic" }}>
                        +{values.length - 8} more registers
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

// ── Part Search sub-component ──────────────────────────────
function PartSearch({ zones: zonesProp }) {
  const [code, setCode]         = useState("");
  const [lineId, setLineId]     = useState("");
  const [dateFrom, setDateFrom] = useState(() => {
    const d = new Date(); d.setDate(d.getDate()-7); return d.toISOString().slice(0,10);
  });
  const [dateTo, setDateTo]     = useState(() => new Date().toISOString().slice(0,10));
  const [results, setResults]   = useState(null);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState("");
  const [lines, setLines]       = useState([]);
  const [videoSrc, setVideoSrc] = useState(null); // {src, row}
  // Track which result rows have their detail panel expanded.
  // 2026-05-18 — Operator spec: "historical part me search krne pe
  // history hr process ki with details aani chahiye".  Backend now
  // enriches each row with sub_cycles, py_events, sa_data — this
  // state controls per-row expansion in the UI.
  const [expandedIdx, setExpandedIdx] = useState(new Set());

  // Load lines
  useEffect(() => {
    api.get("/api/lines/").then(r => setLines(Array.isArray(r.data)?r.data:[])).catch(()=>{});
  }, []);

  const search = async () => {
    if (!code.trim()) { setError("Enter a Part ID to search"); return; }
    setLoading(true); setError(""); setResults(null);
    try {
      const params = new URLSearchParams({ code: code.trim() });
      if (lineId) params.append("line_id", lineId);
      if (dateFrom) params.append("date_from", dateFrom);
      if (dateTo) params.append("date_to", dateTo);
      const r = await api.get(`/api/lines/part-search?${params}`);
      setResults(Array.isArray(r.data) ? r.data : []);
    } catch (e) {
      setError(e?.response?.data?.detail || "Search failed");
    } finally { setLoading(false); }
  };

  const playVideo = (row) => {
    // Direct to NF2 via /cms-api proxy (single hop, no buffering)
    const pc = (row.part_code || "").replace(/:$/, "").replace(/[^A-Za-z0-9._-]/g, "_").replace(/^_+|_+$/g, "");
    if (pc) {
      setVideoSrc({ src: `/cms-api/api/video/by-part?code=${encodeURIComponent(pc)}`, row });
    } else {
      const jwt = sessionStorage.getItem("mes_token") || "";
      const qs = `date=${row.record_date}&shift=${encodeURIComponent(row.shift_name)}&cycle_seq=${row.cycle_seq}&token=${encodeURIComponent(jwt)}`;
      setVideoSrc({ src: `/api/lines/${row.line_id}/cycle-video?${qs}`, row });
    }
  };

  return (
    <>
      {/* Search controls */}
      <div className="filter-card">
        <div className="filter-grid" style={{gridTemplateColumns:"1fr 1fr 1fr 1fr 1fr"}}>
          <div className="ff" style={{gridColumn:"1/-1"}}>
            <label>Part ID</label>
            <input value={code} onChange={e=>setCode(e.target.value)}
              onKeyDown={e=>{if(e.key==="Enter")search();}}
              placeholder="Enter Part ID (partial match)"
              style={{fontFamily:"monospace",fontSize:14,fontWeight:700,letterSpacing:".03em"}}/>
          </div>
          <div className="ff">
            <label>Line (optional)</label>
            <select value={lineId} onChange={e=>setLineId(e.target.value)}>
              <option value="">All Lines</option>
              {lines.map(l=><option key={l.id} value={l.id}>{l.line_name}</option>)}
            </select>
          </div>
          <div className="ff">
            <label>From</label>
            <input type="date" value={dateFrom} onChange={e=>setDateFrom(e.target.value)}/>
          </div>
          <div className="ff">
            <label>To</label>
            <input type="date" value={dateTo} onChange={e=>setDateTo(e.target.value)}
              max={new Date().toISOString().slice(0,10)}/>
          </div>
          <div className="ff" style={{justifyContent:"flex-end"}}>
            <button className="fetch-btn" onClick={search} disabled={loading}>
              {loading ? <><div className="spinner"/>Searching...</> : <>Search</>}
            </button>
          </div>
        </div>
      </div>

      {error && <div className="err-box">{error}</div>}

      {/* Results */}
      {results !== null && (
        <div className="result-card">
          {results.length === 0 ? (
            <div className="no-data-box">
              <div className="icon">🔍</div>
              <h3>No Records Found</h3>
              <p style={{fontSize:13}}>No cycles match "{code}" in the selected date range.</p>
            </div>
          ) : (
            <>
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:16}}>
                <div className="sec-title" style={{margin:0}}>
                  {results.length} result{results.length!==1?"s":""} for "{code}"
                </div>
              </div>
              <div style={{overflowX:"auto"}}>
                <table className="slot-tbl">
                  <thead><tr>
                    {["", "Part ID","Date","Shift","Zone","Line","Cycle#","CT (s)","Ideal","Status","Video"].map((h,hi)=>(
                      <th key={hi}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {results.map((r,i) => {
                      const over = r.ct_value > r.ideal_ct;
                      const isOpen = expandedIdx.has(i);
                      const subN = (r.sub_cycles || []).length;
                      const pyN  = (r.py_events  || []).length;
                      const saN  = (r.sa_data    || []).length;
                      const hasAny = subN + pyN + saN > 0;
                      const toggle = () => {
                        setExpandedIdx(prev => {
                          const next = new Set(prev);
                          if (next.has(i)) next.delete(i); else next.add(i);
                          return next;
                        });
                      };
                      return (
                        <Fragment key={i}>
                          <tr style={hasAny ? { cursor:"pointer" } : {}} onClick={hasAny ? toggle : undefined}>
                            <td style={{width:30,textAlign:"center",color:"#64748b",fontWeight:700}}>
                              {hasAny ? (isOpen ? "▾" : "▸") : ""}
                            </td>
                            <td style={{fontFamily:"monospace",fontWeight:700,color:"#1e40af",fontSize:11,maxWidth:200,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={r.part_code}>
                              {(r.part_code||"").replace(/:$/,"")}
                            </td>
                            <td>{r.record_date}</td>
                            <td><span style={{padding:"2px 8px",borderRadius:99,fontSize:10,fontWeight:700,
                              background:r.shift_name==="A"?"rgba(30,64,175,.1)":"rgba(124,58,237,.1)",
                              color:r.shift_name==="A"?"#1e40af":"#7c3aed"}}>{r.shift_name}</span></td>
                            <td>{r.zone_name||"—"}</td>
                            <td>{r.line_name||"—"}</td>
                            <td style={{fontFamily:"monospace",fontWeight:700}}>#{r.cycle_seq}</td>
                            <td style={{fontFamily:"monospace",fontWeight:800,color:over?"#dc2626":"#16a34a"}}>
                              {r.ct_value}s {over && <span style={{fontSize:9,opacity:.7}}>+{(r.ct_value-r.ideal_ct).toFixed(1)}s</span>}
                            </td>
                            <td style={{fontFamily:"monospace",color:"#64748b"}}>{r.ideal_ct}s</td>
                            <td>
                              {r.is_ng
                                ? <span style={{padding:"2px 10px",borderRadius:99,fontSize:10,fontWeight:800,background:"rgba(220,38,38,.1)",color:"#dc2626"}}>NG !</span>
                                : <span style={{padding:"2px 10px",borderRadius:99,fontSize:10,fontWeight:800,background:"rgba(22,163,74,.1)",color:"#16a34a"}}>OK</span>}
                            </td>
                            <td>
                              <button onClick={(e)=>{ e.stopPropagation(); playVideo(r); }} style={{
                                padding:"4px 10px",borderRadius:6,border:"1px solid #e2e8f0",
                                background:"#f8fafc",cursor:"pointer",fontSize:11,fontWeight:700,
                                color:"#1e40af",display:"flex",alignItems:"center",gap:4,
                              }}>
                                ▶ Play
                              </button>
                            </td>
                          </tr>
                          {/* Sub-row counts hint (always visible when collapsed and there's data) */}
                          {hasAny && !isOpen && (
                            <tr><td colSpan={11} style={{padding:"4px 12px 8px 42px",fontSize:11,color:"#64748b",borderTop:"none"}}>
                              <span style={{marginRight:14}}>🛠 {subN} sub-machine cycles</span>
                              <span style={{marginRight:14}}>{pyN > 0 ? "⚠" : "✓"} {pyN} PY events</span>
                              <span>📊 {saN} Semi-Auto captures</span>
                              <span style={{marginLeft:14,color:"#1e40af",fontWeight:600,cursor:"pointer"}} onClick={toggle}>· click to expand</span>
                            </td></tr>
                          )}
                          {hasAny && isOpen && (
                            <tr><td colSpan={11} style={{padding:"4px 12px 14px 42px",background:"#f8fafc",borderTop:"none"}}>
                              <ProcessHistoryPanel row={r} />
                            </td></tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}

      {/* Video modal */}
      {videoSrc && (
        <div onClick={()=>setVideoSrc(null)} style={{
          position:"fixed",inset:0,zIndex:9999,background:"rgba(0,0,0,.82)",
          display:"flex",alignItems:"center",justifyContent:"center",
        }}>
          <div onClick={e=>e.stopPropagation()} style={{
            background:"#0a0f1a",borderRadius:12,padding:16,maxWidth:820,width:"90vw",
            boxShadow:"0 24px 72px rgba(0,0,0,.6)",border:"1px solid #141e2e",
          }}>
            <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
              <span style={{fontSize:12,fontWeight:800,color:"#e8edf5",letterSpacing:".04em"}}>
                Part: <span style={{color:"#3b82f6",fontFamily:"monospace"}}>{(videoSrc.row.part_code||"").replace(/:$/,"")}</span>
                {"  |  "}{videoSrc.row.ct_value}s
                {"  |  "}Ideal: {videoSrc.row.ideal_ct}s
                {"  |  "}{videoSrc.row.shift_name} Shift
                {"  |  "}{videoSrc.row.record_date}
                {"  |  "}{videoSrc.row.is_ng ? <span style={{color:"#ef4444",fontWeight:900}}>NG !</span> : <span style={{color:"#22c55e"}}>OK</span>}
              </span>
              <button onClick={()=>setVideoSrc(null)} style={{
                background:"transparent",border:"none",cursor:"pointer",
                fontSize:22,lineHeight:1,color:"#8092af",padding:"0 4px",
              }}>×</button>
            </div>
            <video
              controls autoPlay
              onClick={e=>{e.target.paused?e.target.play():e.target.pause();}}
              onError={()=>setVideoSrc(v=>v?{...v,error:true}:v)}
              style={{width:"100%",borderRadius:8,maxHeight:"68vh",background:"#000",display:"block"}}
              src={videoSrc.error ? "" : videoSrc.src}
            />
            {videoSrc.error && (
              <div style={{padding:"24px 16px",textAlign:"center",color:"#8092af",fontSize:13,fontWeight:600,background:"#070c14",borderRadius:8,marginTop:8}}>
                Video not available for this cycle
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

// ── Breakdown Slips sub-component ──────────────────────────
// Shows past breakdown slips Production has filled (or any slip in the
// chosen date window).  Production user gets read-only access via this
// tab — admin can also view the same here, and the writable side stays
// inside the Maintenance Dashboard.  Reuses ClosureFormModal so the
// rendered slip looks identical to the Toyota Boshoku BREAK DOWN SLIP.
function BreakdownSlipsTab() {
  const { token, theme, isAdmin, isProduction, user } = useAuth();
  // Production-side viewers (role='production' OR department user with
  // slug='production') only see what THEY filled — the upper half of
  // the slip.  Maintenance status of a slip is not their concern, so
  // we drop the "Maint" column and pass phase="production" to the
  // ClosureFormModal so its lower half stays hidden.
  // Admin + maintenance dept see the full slip.
  const deptSlug = (user?.departmentSlug || "").toLowerCase();
  const isProductionView = !isAdmin && (isProduction || deptSlug === "production");
  const [days,     setDays]     = useState(30);
  const [fromDate, setFromDate] = useState("");
  const [toDate,   setToDate]   = useState("");
  const [stateF,   setStateF]   = useState("");
  const [search,   setSearch]   = useState("");
  const [rows,     setRows]     = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [viewModal,setViewModal]= useState(null);
  const [modal, setModal] = useState(null);

  // Lazy-import ClosureFormModal — keep it out of the top-level imports
  // so production user's bundle doesn't pay the modal cost until they
  // actually click "View Slip".
  useEffect(() => {
    let alive = true;
    import("./MaintenanceDashboard").then(m => {
      if (alive) setModal(() => m.ClosureFormModal);
    }).catch(() => {});
    return () => { alive = false; };
  }, []);

  const reload = async () => {
    if (!token) return;
    setLoading(true);
    try {
      const q = new URLSearchParams({ days: String(days), limit: "500" });
      if (fromDate) q.set("from_date", fromDate);
      if (toDate)   q.set("to_date",   toDate);
      if (stateF)   q.set("state",     stateF);
      const r = await api.get(`/api/breakdowns/history?${q.toString()}`);
      setRows(Array.isArray(r.data?.rows) ? r.data.rows : []);
    } catch { setRows([]); }
    finally { setLoading(false); }
  };
  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [days, fromDate, toDate, stateF]);

  const filtered = rows.filter(r => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return [
      r.line_name, r.zone_name, r.shift_name,
      r.production_data?.machine_no, r.production_data?.machine_name,
      r.production_data?.problem_description,
    ].some(v => String(v ?? "").toLowerCase().includes(q));
  });

  const fmtTs = (iso) => {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleString("en-IN", {
        day: "2-digit", month: "short", year: "numeric",
        hour: "2-digit", minute: "2-digit",
      });
    } catch { return "—"; }
  };
  const fmtDur = (s) => {
    if (s == null) return "—";
    const sec = Math.max(0, Math.floor(s));
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60), rem = m % 60;
    return rem ? `${h}h ${rem}m` : `${h}h`;
  };
  const stateBadge = (st) => {
    const meta = {
      OPEN:     { bg:"rgba(220,38,38,.10)", color:"#dc2626", label:"Open" },
      RESOLVED: { bg:"rgba(217,119,6,.10)", color:"#b45309", label:"Resolved" },
      CLOSED:   { bg:"rgba(22,163,74,.10)", color:"#15803d", label:"Closed" },
    }[st] || { bg:"#f1f5f9", color:"#64748b", label: st || "—" };
    return (
      <span style={{ padding:"2px 9px", borderRadius:99, fontSize:10, fontWeight:700,
                      background:meta.bg, color:meta.color, whiteSpace:"nowrap" }}>
        {meta.label}
      </span>
    );
  };

  return (
    <div className="result-card">
      {/* Filter row */}
      <div style={{display:"flex",gap:14,flexWrap:"wrap",marginBottom:18,alignItems:"flex-end"}}>
        <div className="ff" style={{flex:"0 0 auto",minWidth:120}}>
          <label>Window</label>
          <select value={days} onChange={e=>{ setDays(Number(e.target.value)); }}
                  disabled={!!fromDate || !!toDate}>
            <option value={1}>Today</option>
            <option value={7}>7 days</option>
            <option value={30}>30 days</option>
            <option value={90}>90 days</option>
            <option value={180}>180 days</option>
          </select>
        </div>
        <div className="ff" style={{flex:"0 0 auto",minWidth:140}}>
          <label>From</label>
          <input type="date" value={fromDate} onChange={e=>setFromDate(e.target.value)}/>
        </div>
        <div className="ff" style={{flex:"0 0 auto",minWidth:140}}>
          <label>To</label>
          <input type="date" value={toDate} onChange={e=>setToDate(e.target.value)}/>
        </div>
        <div className="ff" style={{flex:"0 0 auto",minWidth:140}}>
          <label>State</label>
          <select value={stateF} onChange={e=>setStateF(e.target.value)}>
            <option value="">All</option>
            <option value="OPEN">Open</option>
            <option value="RESOLVED">Resolved</option>
            <option value="CLOSED">Closed</option>
          </select>
        </div>
        <div className="ff" style={{flex:1,minWidth:180}}>
          <label>Search</label>
          <input type="text" placeholder="Line, machine, problem…"
                 value={search} onChange={e=>setSearch(e.target.value)}/>
        </div>
        {(fromDate || toDate || stateF || search) && (
          <button onClick={()=>{ setFromDate(""); setToDate(""); setStateF(""); setSearch(""); }}
                  style={{padding:"10px 16px",borderRadius:8,border:`1.5px solid ${theme.accent}`,
                          background:"#fff",color:theme.accent,fontWeight:700,cursor:"pointer",fontSize:12}}>
            Clear
          </button>
        )}
      </div>

      <div style={{fontSize:11,color:"#64748b",marginBottom:10}}>
        {filtered.length} slip{filtered.length===1?"":"s"} found · click any row to view the slip
      </div>

      {loading ? (
        <div style={{padding:60,textAlign:"center",color:"#94a3b8"}}>Loading…</div>
      ) : filtered.length === 0 ? (
        <div style={{padding:60,textAlign:"center",color:"#94a3b8",fontStyle:"italic"}}>
          No breakdown slips match the current filters.
        </div>
      ) : (
        <div style={{overflowX:"auto"}}>
          <table className="slot-tbl" style={{minWidth:900}}>
            <thead>
              <tr>
                {(isProductionView
                  ? ["#","Started","Line","Zone","Shift","Machine","Duration","State","Prod",""]
                  : ["#","Started","Line","Zone","Shift","Machine","Duration","State","Prod","Maint",""]
                ).map(h=>(
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map(r => {
                const mNo   = r.production_data?.machine_no;
                const mName = r.production_data?.machine_name;
                return (
                  <tr key={r.id}>
                    <td style={{fontFamily:"monospace",color:"#94a3b8"}}>#{r.id}</td>
                    <td style={{fontFamily:"monospace",whiteSpace:"nowrap"}}>{fmtTs(r.started_at)}</td>
                    <td style={{fontWeight:700}}>{r.line_name || `Line ${r.line_id}`}</td>
                    <td style={{color:"#475569"}}>{r.zone_name || "—"}</td>
                    <td style={{fontFamily:"monospace"}}>
                      {r.shift_name || "—"}{r.serial_in_shift ? `·#${r.serial_in_shift}` : ""}
                    </td>
                    <td style={{fontFamily:"monospace"}}>
                      {mNo
                        ? <span title={mName||""}>#{mNo}{mName ? ` · ${mName.slice(0,22)}${mName.length>22?'…':''}` : ""}</span>
                        : <span style={{color:"#cbd5e1"}}>—</span>}
                    </td>
                    <td style={{fontFamily:"monospace",fontWeight:700}}>{fmtDur(r.duration_seconds)}</td>
                    <td>{stateBadge(r.state)}</td>
                    <td>
                      {r.production_filled_at
                        ? <span style={{color:"#16a34a",fontWeight:700,fontSize:11}}>✓ filled</span>
                        : <span style={{color:"#dc2626",fontWeight:700,fontSize:11}}>pending</span>}
                    </td>
                    {!isProductionView && (
                      <td>
                        {r.maintenance_filled_at
                          ? <span style={{color:"#16a34a",fontWeight:700,fontSize:11}}>✓ filled</span>
                          : <span style={{color:"#dc2626",fontWeight:700,fontSize:11}}>pending</span>}
                      </td>
                    )}
                    <td>
                      <button onClick={()=>setViewModal(r)}
                              style={{background:"#fff",color:theme.accent,
                                      border:`1.5px solid ${theme.accent}`,
                                      padding:"4px 11px",borderRadius:7,
                                      fontWeight:700,fontSize:11,cursor:"pointer"}}>
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

      {/* Read-only slip viewer.  ClosureFormModal is loaded lazily — until
          it resolves the View button still works (button click stages the
          ticket; modal renders as soon as `modal` becomes available). */}
      {viewModal && modal && (() => {
        const Modal = modal;
        return (
          <Modal
            ticket={viewModal}
            mode="view"
            // Production viewers only see their upper half (their fill);
            // admin + maintenance dept see the full slip including the
            // maintenance lower half.
            phase={isProductionView ? "production" : "maintenance"}
            token={token}
            onClose={() => setViewModal(null)}
            onSave={() => {}}
          />
        );
      })()}
    </div>
  );
}


// ── Main Historical component ──────────────────────────────
export default function Historical() {
  const { theme } = useAuth();
  const [zones,    setZones]    = useState([]);
  const [lines,    setLines]    = useState([]);
  const [slots,    setSlots]    = useState([]);
  const [selZone,  setSelZone]  = useState("");
  const [selLine,  setSelLine]  = useState("");
  const [selDate,  setSelDate]  = useState(new Date().toISOString().split("T")[0]);
  const [selShift, setSelShift] = useState("A");
  const [selSlot,  setSelSlot]  = useState("");
  const [loading,  setLoading]  = useState(false);
  const [result,   setResult]   = useState(null);
  const [error,    setError]    = useState("");
  const [npdEntries, setNpdEntries] = useState([]);
  const [activeTab, setActiveTab]   = useState("shift"); // "shift" | "part" | "slips"

  useEffect(() => { document.title = "Historical Data"; }, []);

  // Load zones on mount
  useEffect(() => {
    const t = sessionStorage.getItem("mes_token");
    if (!t) return;
    api.get("/api/zones/")
      .then(r => setZones(Array.isArray(r.data) ? r.data : []))
      .catch(() => setZones([]));
  }, []);

  // Load lines when zone changes
  useEffect(() => {
    setSelLine(""); setSlots([]); setSelSlot(""); setResult(null);
    const t = sessionStorage.getItem("mes_token");
    if (!t) return;
    api.get("/api/lines/")
      .then(r => {
        const all = Array.isArray(r.data) ? r.data : [];
        setLines(selZone ? all.filter(l => String(l.zone_id) === String(selZone)) : all);
      })
      .catch(() => setLines([]));
  }, [selZone]);

  // Load slots when line or shift changes
  useEffect(() => {
    setSlots([]); setSelSlot(""); setResult(null);
    if (!selLine) return;
    api.get(`/api/lines/${selLine}`)
      .then(r => {
        const d = r.data;
        const f = (d.hourly_slots || []).filter(s => s.shift_name === selShift);
        f.sort((a,b) => a.start_time.localeCompare(b.start_time));
        setSlots(f);
      })
      .catch(() => {});
  }, [selLine, selShift]);

  const fetchData = async () => {
    if (!selLine || !selDate) { setError("Please select a line and date."); return; }
    setError(""); setLoading(true); setResult(null); setNpdEntries([]);
    try {
      let url = `/api/lines/historical?line_id=${selLine}&date=${selDate}&shift_name=${selShift}`;
      if (selSlot) url += `&hour_slot=${encodeURIComponent(selSlot)}`;
      const [res, npdRes] = await Promise.allSettled([
        api.get(url),
        api.get(`/api/npd/?line_id=${selLine}&date=${selDate}`),
      ]);
      if (res.status === "fulfilled") {
        const data = res.value.data;
        if (data.error) setError(data.error);
        else setResult({ data, slot: selSlot });
      } else {
        setError(res.reason?.response?.data?.detail || res.reason?.message || "Failed to fetch data.");
      }
      if (npdRes.status === "fulfilled") {
        const entries = Array.isArray(npdRes.value.data) ? npdRes.value.data : [];
        setNpdEntries(entries.filter(e => !e.shift_name || e.shift_name === selShift));
      }
    } finally {
      setLoading(false);
    }
  };

  const buildSlotRows = (data) => slots.map(s => {
    const p = s.db_column_prefix;
    const plan     = data[`${p}_plan`]     || 0;
    const actual   = data[`${p}_actual`]   || 0;
    const variance = data[`${p}_variance`] || 0;
    const ok       = data[`${p}_ok`]       || 0;
    const ng       = data[`${p}_ng`]       || 0;
    const eff      = plan ? ((actual/plan)*100).toFixed(1) : "0.0";
    return { label:s.slot_label, plan, actual, variance, ok, ng, eff };
  });

  const totalLoss = result?.data ? (
    (result.data.loss_breakdown_seconds   || 0) +
    (result.data.loss_quality_seconds     || 0) +
    (result.data.loss_material_seconds    || 0) +
    (result.data.loss_setup_seconds       || 0) +
    (result.data.loss_change_over_seconds || 0) +
    (result.data.loss_speed_seconds       || 0) +
    (result.data.loss_others_seconds      || 0)
  ) : 0;

  const oeeColor = v => v>=85 ? "#16a34a" : v>=65 ? "#d97706" : "#dc2626";

  const StatTile = ({ label, value, color="#0f172a", sub=null }) => (
    <div style={{
      background:"#fff", border:"1px solid #e2e8f0",
      borderRadius:12, padding:"16px 18px",
      position:"relative", overflow:"hidden",
      boxShadow:"0 1px 3px rgba(0,0,0,.05)",
    }}>
      <div style={{ position:"absolute", top:0, left:0, right:0, height:3, background:color }} />
      <div style={{ fontSize:26, fontWeight:800, color, fontFamily:"monospace", marginBottom:4, lineHeight:1 }}>{value}</div>
      <div style={{ fontSize:11, color:"#64748b", fontWeight:500, marginTop:6 }}>{label}</div>
      {sub && <div style={{ fontSize:10, color:"#94a3b8", marginTop:3 }}>{sub}</div>}
    </div>
  );

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@700;800&display=swap');
        *{box-sizing:border-box;}
        .hist-root{min-height:100vh;background:#f8fafc;font-family:'Barlow',sans-serif;padding-bottom:60px;}
        .hist-topbar{background:#fff;border-bottom:1px solid #e2e8f0;padding:0 40px 0 88px;height:60px;display:flex;align-items:center;position:sticky;top:0;z-index:100;box-shadow:0 1px 3px rgba(0,0,0,.06);}
        .hist-topbar::after{content:'';position:absolute;bottom:0;left:0;right:0;height:2px;background:${theme.gradient};}
        .hist-logo{font-family:'Barlow Condensed',sans-serif;font-size:22px;font-weight:800;color:#0f172a;}
        .hist-body{padding:36px 40px 0;max-width:1000px;margin:0 auto;}
        .filter-card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:28px;margin-bottom:28px;box-shadow:0 1px 3px rgba(0,0,0,.05);}
        .filter-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:16px;margin-bottom:20px;}
        .ff{display:flex;flex-direction:column;gap:6px;}
        .ff label{font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#64748b;}
        .ff select,.ff input{background:#f8fafc;border:1.5px solid #e2e8f0;border-radius:8px;padding:10px 12px;color:#0f172a;font-family:'Barlow',sans-serif;font-size:13px;outline:none;transition:border-color .15s,box-shadow .15s;appearance:none;}
        .ff select:focus,.ff input:focus{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.1);}
        .ff select:disabled{color:#94a3b8;cursor:not-allowed;}
        .fetch-btn{width:100%;padding:13px;background:linear-gradient(135deg,${theme.accentDark},${theme.accent});border:none;border-radius:10px;color:#fff;font-family:'Barlow',sans-serif;font-size:14px;font-weight:700;cursor:pointer;box-shadow:0 4px 16px ${theme.soft};transition:all .15s;display:flex;align-items:center;justify-content:center;gap:8px;}
        .fetch-btn:hover:not(:disabled){filter:brightness(1.08);transform:translateY(-1px);}
        .fetch-btn:disabled{opacity:.6;cursor:not-allowed;}
        .spinner{width:16px;height:16px;border-radius:50%;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;animation:spin .6s linear infinite;}
        @keyframes spin{to{transform:rotate(360deg)}}
        .err-box{padding:12px 16px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.25);border-radius:8px;color:#dc2626;font-size:13px;margin-bottom:20px;}
        .info-box{padding:12px 16px;background:rgba(59,130,246,.06);border:1px solid rgba(59,130,246,.2);border-radius:8px;color:#1e40af;font-size:13px;margin-bottom:20px;}
        .stats-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:14px;margin-bottom:24px;}
        .sec-title{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#64748b;margin-bottom:14px;display:flex;align-items:center;gap:8px;}
        .sec-title::after{content:'';flex:1;height:1px;background:#e2e8f0;}
        .slot-tbl{width:100%;border-collapse:collapse;font-size:13px;}
        .slot-tbl th{padding:10px 14px;text-align:left;font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#64748b;border-bottom:2px solid #e2e8f0;background:#f8fafc;}
        .slot-tbl td{padding:11px 14px;border-bottom:1px solid #f1f5f9;color:#334155;}
        .slot-tbl tr:hover td{background:#f8fafc;}
        .eff-hi{color:#16a34a;font-weight:700;}
        .eff-md{color:#d97706;font-weight:700;}
        .eff-lo{color:#dc2626;font-weight:700;}
        .result-card{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:28px;box-shadow:0 1px 3px rgba(0,0,0,.05);}
        .no-data-box{text-align:center;padding:48px 20px;color:#64748b;}
        .no-data-box .icon{font-size:48px;margin-bottom:16px;}
        .no-data-box h3{font-size:18px;font-weight:700;color:#0f172a;margin-bottom:8px;}
      `}</style>

      <div className="hist-root">
        {/* Topbar */}
        <div className="hist-topbar">
          <div className="hist-logo" />
          <div style={{
            position:"absolute", left:"50%", transform:"translateX(-50%)",
            fontFamily:"'Barlow Condensed',sans-serif",
            fontSize:34, fontWeight:800, color:"#0f172a",
            pointerEvents:"none",
          }}>
            Production <span style={{ color: theme.accent }}>Historical Data</span>
          </div>
        </div>

        <div className="hist-body">

          {/* Tab bar */}
          <div style={{display:"flex",gap:0,marginBottom:24,background:"#fff",borderRadius:"12px 12px 0 0",
            border:"1px solid #e2e8f0",borderBottom:"2px solid #e2e8f0",overflow:"hidden"}}>
            {[
              {key:"shift", label:"Shift Data"},
              {key:"part",  label:"Part Search"},
              {key:"slips", label:"Breakdown Slips"},
            ].map(t=>(
              <button key={t.key} onClick={()=>setActiveTab(t.key)} style={{
                flex:1,padding:"13px 20px",fontFamily:"'Barlow',sans-serif",fontSize:14,fontWeight:700,
                cursor:"pointer",border:"none",background:activeTab===t.key?theme.accentDark:"#fff",
                color:activeTab===t.key?"#fff":"#64748b",
                transition:"all .15s",
              }}>{t.label}</button>
            ))}
          </div>

          {/* Part Search tab */}
          {activeTab === "part" && <PartSearch zones={zones} />}

          {/* Breakdown Slips tab — Production-fill records + viewable
              slip archive.  Same /api/breakdowns/history backing as the
              Maintenance Historical page, but presented as a flat
              filterable list (no MTBF / MTTR roll-ups since those are
              maintenance-side metrics). */}
          {activeTab === "slips" && <BreakdownSlipsTab />}

          {/* Shift Data tab (existing content) */}
          {activeTab === "shift" && <>

          {/* Filter Card */}
          <div className="filter-card">
            <div className="filter-grid">
              {/* Zone */}
              <div className="ff">
                <label>Zone</label>
                <select value={selZone} onChange={e => setSelZone(e.target.value)}>
                  <option value="">All Zones</option>
                  {zones.map(z => <option key={z.id} value={z.id}>{z.zone_name}</option>)}
                </select>
              </div>

              {/* Line */}
              <div className="ff">
                <label>Line *</label>
                <select value={selLine} onChange={e => setSelLine(e.target.value)}>
                  <option value="">Select line…</option>
                  {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
                </select>
              </div>

              {/* Date */}
              <div className="ff">
                <label>Date *</label>
                <input type="date" value={selDate} onChange={e => setSelDate(e.target.value)}
                  max={new Date().toISOString().split("T")[0]} />
              </div>

              {/* Shift */}
              <div className="ff">
                <label>Shift</label>
                <select value={selShift} onChange={e => setSelShift(e.target.value)}>
                  <option value="A">A Shift (08:30 – 17:15)</option>
                  <option value="B">B Shift (18:30 – 03:15)</option>
                </select>
              </div>

              {/* Slot */}
              <div className="ff">
                <label>Hourly Slot</label>
                <select value={selSlot} onChange={e => setSelSlot(e.target.value)} disabled={!selLine}>
                  <option value="">All Slots</option>
                  {slots.map(s => <option key={s.slot_label} value={s.slot_label}>{s.slot_label}</option>)}
                </select>
              </div>
            </div>

            {error && <div className="err-box">⚠ {error}</div>}

            <button className="fetch-btn" onClick={fetchData} disabled={loading || !selLine}>
              {loading
                ? <><div className="spinner"/> Fetching…</>
                : <>🔍 Fetch Data</>
              }
            </button>
          </div>

          {/* Results */}
          {result && (
            <div className="result-card">

              {/* Result header */}
              <div style={{ marginBottom:24, paddingBottom:16, borderBottom:"1px solid #f1f5f9", display:"flex", justifyContent:"space-between", alignItems:"flex-start" }}>
                <div>
                  <div style={{ fontFamily:"'Barlow Condensed',sans-serif", fontSize:22, fontWeight:800, color:"#0f172a", display:"flex", alignItems:"center", gap:10, flexWrap:"wrap" }}>
                    {lines.find(l => String(l.id) === String(selLine))?.line_name || "Line"} —{" "}
                    {result.slot ? `Slot: ${result.slot}` : `Shift ${selShift}`}
                    {npdEntries.length > 0 && (
                      <span style={{ display:"inline-flex", alignItems:"center", gap:5, padding:"2px 12px", borderRadius:99, background:"rgba(217,119,6,.12)", border:"1px solid rgba(217,119,6,.35)", fontSize:12, fontWeight:700, color:"#d97706" }}>
                        🚫 Non-Production Day
                        {npdEntries[0]?.reason && <span style={{ fontWeight:400, fontSize:11, color:"#a16207" }}>— {npdEntries[0].reason}</span>}
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize:12, color:"#94a3b8", marginTop:4 }}>
                    {selDate} · {selShift === "A" ? "08:30 – 17:15" : "18:30 – 03:15"}
                    {result.data.current_model_name && (
                      <span style={{ marginLeft:10, color:"#0f172a", fontWeight:600 }}>
                        Model: {result.data.current_model_name}
                        <span style={{ fontFamily:"monospace", color:"#94a3b8", marginLeft:4 }}>
                          #{result.data.current_model_number}
                        </span>
                      </span>
                    )}
                  </div>
                </div>
                {!result.slot && (
                  <div style={{
                    display:"flex", alignItems:"center", gap:6, padding:"4px 14px",
                    borderRadius:99,
                    background:`${oeeColor(result.data.overall_oee||0)}12`,
                    border:`1px solid ${oeeColor(result.data.overall_oee||0)}33`,
                  }}>
                    <span style={{ fontSize:15, fontWeight:800, color:oeeColor(result.data.overall_oee||0), fontFamily:"monospace" }}>
                      {(result.data.overall_oee||0).toFixed(1)}%
                    </span>
                    <span style={{ fontSize:10, color:"#94a3b8" }}>OEE</span>
                    {result.data.oee_grade && (
                      <span style={{
                        fontSize:10, fontWeight:700,
                        color:oeeColor(result.data.overall_oee||0),
                        background:`${oeeColor(result.data.overall_oee||0)}18`,
                        borderRadius:99, padding:"1px 8px",
                      }}>
                        {result.data.oee_grade}
                      </span>
                    )}
                  </div>
                )}
              </div>

              {/* No data state */}
              {result.data.operating_status === "NO_DATA" ? (
                <div className="no-data-box">
                  <div className="icon">📭</div>
                  <h3>No Data Found</h3>
                  <p>No records exist for <strong>{selDate}</strong> — Shift {selShift} on this line.</p>
                  <p style={{ marginTop:8, fontSize:12, color:"#94a3b8" }}>
                    The collector must be running during the shift for data to be recorded.
                  </p>
                </div>
              ) : result.slot ? (
                /* Single slot view */
                <>
                  <div className="sec-title">Slot Summary</div>
                  <div className="stats-grid">
                    <StatTile label="Plan"     value={result.data.plan||0}   color="#1e40af" />
                    <StatTile label="Actual"   value={result.data.actual||0} color="#16a34a" />
                    <StatTile label="OK Count" value={result.data.ok||0}     color="#16a34a" />
                    <StatTile label="NG Count" value={result.data.ng||0}     color="#dc2626" />
                    <StatTile
                      label="Variance"
                      value={`${(result.data.variance||0)>0?"+":""}${result.data.variance||0}`}
                      color={(result.data.variance||0)>=0?"#16a34a":"#dc2626"}
                    />
                  </div>
                </>
              ) : (
                /* Full shift view */
                <>
                  <div className="sec-title" style={{display:"flex",alignItems:"center",gap:10}}>
                    <span>Shift Summary</span>
                    {slots.some(s => (s.slot_label || "").toUpperCase().includes("OT")) && (
                      <span style={{fontSize:10,fontWeight:800,padding:"2px 10px",borderRadius:99,
                        background:"rgba(217,119,6,.15)",color:"#d97706",
                        border:"1px solid rgba(217,119,6,.4)",letterSpacing:".06em"}}>
                        ⏱ OT ACTIVE
                      </span>
                    )}
                  </div>
                  <div className="stats-grid">
                    <StatTile label="Overall OEE"  value={`${(result.data.overall_oee||0).toFixed(1)}%`}  color={oeeColor(result.data.overall_oee||0)} />
                    <StatTile label="Availability" value={`${(result.data.availability||0).toFixed(1)}%`} color="#2563eb" />
                    <StatTile label="Performance"  value={`${(result.data.performance||0).toFixed(1)}%`}  color="#7c3aed" />
                    <StatTile label="Quality"      value={`${(result.data.quality_oee||0).toFixed(1)}%`}  color="#0891b2" />
                    <StatTile label="Plan Target"  value={result.data.shift_plan_completed||0}            color="#d97706" />
                    <StatTile label="Actual"       value={(result.data.ok_count||0)+(result.data.ng_count||0)} color="#0f172a" />
                    <StatTile label="OK Count"     value={result.data.ok_count||0}                        color="#16a34a" />
                    <StatTile label="NG Count"     value={result.data.ng_count||0}                        color="#dc2626" />
                    <StatTile
                      label="Avg Cycle Time"
                      value={`${(result.data.ct_avg_20||0).toFixed(1)}s`}
                      color={(result.data.ct_avg_20||0)>(result.data.cycle_time_plan||15)?"#dc2626":"#334155"}
                      sub={`Plan: ${result.data.cycle_time_plan||15}s`}
                    />
                    <StatTile label="Total Loss" value={fmtSec(totalLoss)} color="#f97316"
                      sub={`${Math.round(totalLoss/60)} min lost`}
                    />
                  </div>

                  {/* Plan achievement bar */}
                  {(() => {
                    const actual = result.data.ok_count||0;
                    const plan   = result.data.shift_plan_completed||0;
                    const pct    = plan ? Math.min(100,(actual/plan)*100).toFixed(1) : 0;
                    const c      = pct>=90?"#16a34a":pct>=70?"#d97706":"#dc2626";
                    return (
                      <div style={{ marginBottom:28 }}>
                        <div style={{ display:"flex", justifyContent:"space-between", fontSize:12, color:"#64748b", marginBottom:8 }}>
                          <span>Plan Achievement</span>
                          <strong style={{ color:c }}>{pct}%</strong>
                        </div>
                        <div style={{ background:"#e2e8f0", borderRadius:6, height:10, overflow:"hidden" }}>
                          <div style={{ width:`${pct}%`, height:"100%", borderRadius:6, background:`linear-gradient(90deg,${c}cc,${c})`, transition:"width .4s" }}/>
                        </div>
                      </div>
                    );
                  })()}

                  {/* Loss distribution */}
                  {totalLoss > 0 && (
                    <div style={{ marginBottom:28 }}>
                      <div className="sec-title">Loss Distribution</div>
                      <div style={{ display:"flex", gap:3, height:26, borderRadius:8, overflow:"hidden", marginBottom:10 }}>
                        {LOSS_CATS.map(c => {
                          const sec = result.data[`loss_${c.key}_seconds`] || 0;
                          const pct = totalLoss > 0 ? (sec/totalLoss*100) : 0;
                          if (pct < 1) return null;
                          return (
                            <div key={c.key}
                              title={`${c.label}: ${fmtSec(sec)} (${pct.toFixed(1)}%)`}
                              style={{ width:`${pct}%`, background:c.color, borderRadius:4, cursor:"help", minWidth:4 }}
                            />
                          );
                        })}
                      </div>
                      <div style={{ display:"flex", flexWrap:"wrap", gap:14 }}>
                        {LOSS_CATS.map(c => {
                          const sec = result.data[`loss_${c.key}_seconds`] || 0;
                          if (!sec) return null;
                          const pct = totalLoss>0?(sec/totalLoss*100).toFixed(1):"0";
                          return (
                            <div key={c.key} style={{ display:"flex", alignItems:"center", gap:5 }}>
                              <div style={{ width:8, height:8, borderRadius:2, background:c.color }}/>
                              <span style={{ fontSize:10, color:"#64748b" }}>
                                {c.label}: <strong style={{ color:"#0f172a", fontFamily:"monospace" }}>{fmtSec(sec)}</strong>
                                <span style={{ color:"#94a3b8", marginLeft:3 }}>({pct}%)</span>
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* Hourly slots table */}
                  {slots.length > 0 && (() => {
                    const rows = buildSlotRows(result.data);
                    const tp = rows.reduce((s,r)=>s+r.plan,0);
                    const ta = rows.reduce((s,r)=>s+r.actual,0);
                    const tv = rows.reduce((s,r)=>s+r.variance,0);
                    const tok = rows.reduce((s,r)=>s+r.ok,0);
                    const tng = rows.reduce((s,r)=>s+r.ng,0);
                    const te  = tp?((ta/tp)*100).toFixed(1):"0.0";
                    return (
                      <>
                        <div className="sec-title">Hourly Slot Performance</div>
                        <div style={{ overflowX:"auto" }}>
                          <table className="slot-tbl">
                            <thead>
                              <tr>
                                {["Slot","Plan","Actual","Variance","OK","NG","Efficiency"].map(h=>(
                                  <th key={h}>{h}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {rows.map(s => (
                                <tr key={s.label}>
                                  <td style={{ fontFamily:"monospace", fontWeight:600, color:"#0f172a" }}>{s.label}</td>
                                  <td style={{ fontFamily:"monospace" }}>{s.plan}</td>
                                  <td style={{ fontFamily:"monospace", color:"#16a34a", fontWeight:600 }}>{s.actual}</td>
                                  <td style={{ fontFamily:"monospace", fontWeight:600, color:s.variance<0?"#dc2626":s.variance>0?"#16a34a":"#64748b" }}>
                                    {s.variance>0?"+":""}{s.variance}
                                  </td>
                                  <td style={{ fontFamily:"monospace", color:"#16a34a" }}>{s.ok}</td>
                                  <td style={{ fontFamily:"monospace", color:s.ng>0?"#dc2626":"#64748b", fontWeight:s.ng>0?700:400 }}>{s.ng}</td>
                                  <td className={parseFloat(s.eff)>=90?"eff-hi":parseFloat(s.eff)>=70?"eff-md":"eff-lo"}>{s.eff}%</td>
                                </tr>
                              ))}
                            </tbody>
                            <tfoot>
                              <tr style={{ borderTop:"2px solid #e2e8f0", background:"#f8fafc" }}>
                                <td style={{ fontWeight:700, color:"#0f172a", padding:"11px 14px" }}>Total</td>
                                <td style={{ fontFamily:"monospace", fontWeight:700, padding:"11px 14px" }}>{tp}</td>
                                <td style={{ fontFamily:"monospace", fontWeight:700, padding:"11px 14px", color:"#16a34a" }}>{ta}</td>
                                <td style={{ fontFamily:"monospace", fontWeight:700, padding:"11px 14px", color:tv<0?"#dc2626":tv>0?"#16a34a":"#64748b" }}>
                                  {tv>0?"+":""}{tv}
                                </td>
                                <td style={{ fontFamily:"monospace", fontWeight:700, padding:"11px 14px", color:"#16a34a" }}>{tok}</td>
                                <td style={{ fontFamily:"monospace", fontWeight:700, padding:"11px 14px", color:tng>0?"#dc2626":"#64748b" }}>{tng}</td>
                                <td className={parseFloat(te)>=90?"eff-hi":parseFloat(te)>=70?"eff-md":"eff-lo"} style={{ padding:"11px 14px" }}>{te}%</td>
                              </tr>
                            </tfoot>
                          </table>
                        </div>
                      </>
                    );
                  })()}
                </>
              )}
            </div>
          )}
          </>}
        </div>
      </div>
      <AIAssistant pageContext={{
           page: "Historical",
           lines: lines,           // your existing state
              }} />
    </>
  );
}