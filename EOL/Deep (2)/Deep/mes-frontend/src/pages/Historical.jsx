import { useState, useEffect, useRef, Fragment } from "react";
import { useSearchParams } from "react-router-dom";
import axios from "axios";
import AIAssistant from "../components/AIAssistant";
import VideoProgressBar from "../components/VideoProgressBar";
import PartTrace from "./PartTrace";
import PeffDetails from "../components/PeffDetails";
import { useAuth } from "../context/AuthContext";

const api = axios.create({ baseURL: "" });
api.interceptors.request.use(cfg => {
  const t = sessionStorage.getItem("mes_token");
  if (t) cfg.headers.Authorization = `Bearer ${t}`;
  return cfg;
});
// 401 → wipe session + bounce to /login.  EXCEPT on the native Android app with
// a saved session: a stray 401 there is almost always a transient blip (the
// backend keeps sessions valid ~indefinitely), so bouncing to the password
// screen is exactly the annoyance the operator asked us to stop. Let the call
// reject and the boot /me probe be the real validity gate instead.
function _appHasSavedSession() {
  try {
    if (!document.documentElement.classList.contains("cap-android")) return false;
    const raw = localStorage.getItem("mes_auth_persist");
    return !!(raw && JSON.parse(raw)?.mes_token);
  } catch { return false; }
}
api.interceptors.response.use(r => r, err => {
  if (err?.response?.status === 401 && !_appHasSavedSession()) {
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
  // Player element, so the always-visible progress bar can read its time.
  const vidRef = useRef(null);
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

  const playVideo = async (row) => {
    const jwt = sessionStorage.getItem("mes_token") || "";
    const cycQs = `date=${row.record_date}&shift=${encodeURIComponent(row.shift_name)}&cycle_seq=${row.cycle_seq}&token=${encodeURIComponent(jwt)}`;
    // 2026-06-01 — Per-cycle ARCHIVE clip FIRST.  The standalone archiver
    // saves a tight per-cycle MP4 (D:\VideoArchive) that survives the
    // shift-boundary TS wipe, so YESTERDAY's exact cycle still plays —
    // and each cycle gets its OWN clip (no "ek hi video sab pe").  We
    // probe with a 1-byte Range; 200/206 ⇒ use it, anything else ⇒ fall
    // back to the existing behaviour below (zero regression).
    const archUrl = `/api/lines/${row.line_id}/archive-video?${cycQs}`;
    try {
      // Hard-bounded probe: AbortController caps it at 4s and we cancel the
      // body the instant we have the status — so a probe can NEVER hold a
      // browser connection open (6-per-origin limit) and starve the live
      // dashboard's /realtime polling.  (2026-06-01 leak fix.)
      const ctrl = new AbortController();
      const tmo = setTimeout(() => ctrl.abort(), 4000);
      const probe = await fetch(archUrl, { headers: { Range: "bytes=0-0" }, signal: ctrl.signal });
      clearTimeout(tmo);
      try { if (probe.body) probe.body.cancel(); } catch { /* noop */ }
      if (probe.ok || probe.status === 206) {
        setVideoSrc({ src: archUrl, row });
        return;
      }
    } catch { /* archive unreachable / timed out — fall through to live path */ }
    // Fallback (unchanged): NF2 by-part MP4 if part_code, else live cut.
    const pc = (row.part_code || "").replace(/:$/, "").replace(/[^A-Za-z0-9._-]/g, "_").replace(/^_+|_+$/g, "");
    if (pc) {
      setVideoSrc({ src: `/cms-api/api/video/by-part?code=${encodeURIComponent(pc)}`, row });
    } else {
      setVideoSrc({ src: `/api/lines/${row.line_id}/cycle-video?${cycQs}`, row });
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
                                ? <span style={{padding:"2px 10px",borderRadius:99,fontSize:10,fontWeight:800,background:"rgba(220,38,38,.1)",color:"#dc2626"}}>Alarm !</span>
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
                {"  |  "}{videoSrc.row.is_ng ? <span style={{color:"#ef4444",fontWeight:900}}>Alarm !</span> : <span style={{color:"#22c55e"}}>OK</span>}
              </span>
              <button onClick={()=>setVideoSrc(null)} style={{
                background:"transparent",border:"none",cursor:"pointer",
                fontSize:22,lineHeight:1,color:"#8092af",padding:"0 4px",
              }}>×</button>
            </div>
            <video
              ref={vidRef}
              autoPlay
              onClick={e=>{e.target.paused?e.target.play():e.target.pause();}}
              onError={()=>setVideoSrc(v=>v?{...v,error:true}:v)}
              style={{width:"100%",borderRadius:8,maxHeight:"68vh",background:"#000",display:"block"}}
              src={videoSrc.error ? "" : videoSrc.src}
            />
            {/* always-visible progress + time (native strip fades out) */}
            <VideoProgressBar videoRef={vidRef} />
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
// ── Video Archive tab ───────────────────────────────────────
// 2026-08-19 — operator: "kal ki video bhi dekhni h ... back days ki video
// with shift and cycle serial no", then: "part id dal di to video direct
// search ho ke aaye ... part details bhi aajaye sath me".
//
// So this one tab answers BOTH ways an operator looks for a past clip:
//   • Part ID  — they have the part in hand; type the code and the video for
//                that exact cycle comes up, with its full manufacturing
//                detail (line, shift, CT vs ideal, OK/Alarm, per-process
//                breakdown).  No date/line/shift picking first.
//   • Browse   — they don't have a code (or want to scan a whole shift), so
//                they walk day -> line -> shift -> machine -> cycle serial.
// This replaced the separate "Part Search" tab, which could only do the first
// half and always had to fall back to a live cut when the clip had aged out.
//
// Part lookup reuses /api/lines/part-search (unchanged, already returns the
// enrichment ProcessHistoryPanel renders); playback prefers the archived clip
// from /api/clip-archive/video and falls back exactly the way the old tab did,
// so nothing an operator could play before became unplayable.
// 2026-09-17 — Per-cycle remarks inside the Video Archive player.  The
// wallboard and the management screen have had this for months; opening the
// same cycle from Historical showed the clip with no notes and no way to add
// one ("hr cycle pe uske comment bhi show ho ... vha se bhi m comment daal
// saku").  Same thread as the other two screens: the key is the synthetic
// part_code `cycle_<seq>_<YYYY-MM-DD>`, so a note written here shows on the
// wallboard and vice versa.
//
// Deliberately a local component rather than a shared import: the existing
// panel lives inside WallboardLeft.jsx together with four unexported helpers
// (suggestions, ghost textarea, chips, mic).  Extracting it would mean editing
// two screens that are working fine on a live line, so this mirrors what
// Fullscreen.jsx already did and leaves them untouched.
function ArchiveCycleComments({ lineId, partCode, shift, recordDate, machineName, isNg }) {
  const [list,    setList]    = useState([]);
  const [text,    setText]    = useState("");
  const [busy,    setBusy]    = useState(false);
  const [loading, setLoading] = useState(true);
  const [err,     setErr]     = useState("");

  const load = () => {
    if (!lineId || !partCode) return;
    setLoading(true);
    const q = shift ? `?shift=${encodeURIComponent(shift)}` : "";
    api.get(`/api/lines/${lineId}/cycles/${encodeURIComponent(partCode)}/comments${q}`)
      .then(r => setList(r.data?.comments || []))
      .catch(() => setList([]))
      .finally(() => setLoading(false));
  };
  // Reload whenever the player moves to a different cycle.
  useEffect(load, [lineId, partCode, shift]);   // eslint-disable-line

  const send = async () => {
    const txt = text.trim();
    if (!txt || busy) return;
    setBusy(true); setErr("");
    try {
      // shift + record_date are the CYCLE's own, not the wall clock — without
      // them the server stamps whichever shift is running right now, which is
      // always wrong on a back-day clip.
      await api.post(`/api/lines/${lineId}/cycles/${encodeURIComponent(partCode)}/comments`, {
        comment: txt, machine_name: machineName || null,
        shift: shift || null, record_date: recordDate || null,
      });
      setText("");
      load();
    } catch (e) {
      setErr(e?.response?.data?.detail || "Could not save the remark");
    } finally { setBusy(false); }
  };

  const when = ts => {
    try { return new Date(ts).toLocaleString("en-IN",
      { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }); }
    catch { return ""; }
  };

  return (
    <div style={{ marginTop: 10, borderTop: "1px solid #141e2e", paddingTop: 10 }}>
      <div style={{ fontSize: 11, fontWeight: 800, color: isNg ? "#fca5a5" : "#8092af",
                    letterSpacing: ".05em", textTransform: "uppercase", marginBottom: 7 }}>
        Remarks {list.length ? `(${list.length})` : ""}
      </div>

      <div style={{ maxHeight: 150, overflowY: "auto", marginBottom: 8 }}>
        {loading ? (
          <div style={{ fontSize: 12, color: "#8092af" }}>Loading…</div>
        ) : list.length === 0 ? (
          <div style={{ fontSize: 12, color: "#8092af" }}>No remarks on this cycle yet.</div>
        ) : list.map(c => (
          <div key={c.id} style={{ background: "rgba(255,255,255,0.03)", borderRadius: 7,
                                   padding: "6px 9px", marginBottom: 5 }}>
            <div style={{ fontSize: 13, color: "#e8edf5", whiteSpace: "pre-wrap" }}>{c.comment}</div>
            <div style={{ fontSize: 10.5, color: "#8092af", marginTop: 2 }}>
              <span style={{ color: "#60a5fa", fontWeight: 700 }}>{c.author || "—"}</span>
              {c.leader_name ? ` · Leader: ${c.leader_name}` : ""}
              {c.created_at ? ` · ${when(c.created_at)}` : ""}
            </div>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: 7 }}>
        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          placeholder="Add a remark for this cycle…"
          rows={2}
          style={{ flex: 1, resize: "vertical", background: "#070c14", color: "#e8edf5",
                   border: "1px solid #1d2942", borderRadius: 7, padding: "7px 9px",
                   fontSize: 13, fontFamily: "inherit" }} />
        <button onClick={send} disabled={busy || !text.trim()}
          style={{ padding: "0 16px", borderRadius: 7, border: "none", fontWeight: 800,
                   fontSize: 13, color: "#fff",
                   background: busy || !text.trim() ? "#334155" : "#2563eb",
                   cursor: busy || !text.trim() ? "not-allowed" : "pointer" }}>
          {busy ? "…" : "Save"}
        </button>
      </div>
      {err && <div style={{ fontSize: 11.5, color: "#fca5a5", marginTop: 5 }}>{err}</div>}
    </div>
  );
}


function VideoArchiveTab({ zones: zonesProp }) {
  // Player element, so the always-visible progress bar can read its time.
  const vidRef = useRef(null);
  const [mode, setMode] = useState("part");        // "part" | "browse"

  // ── shared ────────────────────────────────────────────────
  const [allLines, setAllLines] = useState([]);
  const [videoSrc, setVideoSrc] = useState(null);  // {src, clip} | {src, row}
  const [error,   setError]     = useState("");

  useEffect(() => {
    api.get("/api/lines/")
      .then(r => setAllLines(Array.isArray(r.data) ? r.data : []))
      .catch(() => setAllLines([]));
  }, []);

  // ── part-ID mode ──────────────────────────────────────────
  const [code,      setCode]      = useState("");
  const [pLineId,   setPLineId]   = useState("");
  const [pFrom,     setPFrom]     = useState(() => {
    const d = new Date(); d.setDate(d.getDate() - 7);
    return d.toISOString().slice(0, 10);
  });
  const [pTo,       setPTo]       = useState(() => new Date().toISOString().slice(0, 10));
  const [rows,      setRows]      = useState(null);
  const [pLoading,  setPLoading]  = useState(false);
  const [expanded,  setExpanded]  = useState(new Set());

  const searchPart = async () => {
    if (!code.trim()) { setError("Enter a Part ID"); return; }
    setError(""); setPLoading(true); setRows(null); setExpanded(new Set());
    try {
      const p = new URLSearchParams({ code: code.trim() });
      if (pLineId) p.append("line_id", pLineId);
      if (pFrom)   p.append("date_from", pFrom);
      if (pTo)     p.append("date_to", pTo);
      const r = await api.get(`/api/lines/part-search?${p}`);
      setRows(Array.isArray(r.data) ? r.data : (r.data?.results || []));
    } catch (e) {
      setError(e?.response?.data?.detail || "Search failed");
    } finally { setPLoading(false); }
  };

  // Archived clip first (survives the shift-boundary TS wipe and is the exact
  // cycle), then the older archive route, then a live cut — same ladder the
  // Part Search tab used, so no part that used to play stops playing.
  const playRow = async (row) => {
    const jwt = sessionStorage.getItem("mes_token") || "";
    const arch = `/api/clip-archive/video?${new URLSearchParams({
      date: row.record_date, line_id: row.line_id, shift: row.shift_name || "",
      machine: "main", cycle_seq: String(row.cycle_seq), ng: row.is_ng ? "true" : "false",
      token: jwt,
    })}`;
    const legacy = `/api/lines/${row.line_id}/archive-video?date=${row.record_date}` +
      `&shift=${encodeURIComponent(row.shift_name || "")}&cycle_seq=${row.cycle_seq}` +
      `&token=${encodeURIComponent(jwt)}`;

    for (const url of [arch, legacy]) {
      try {
        // Bounded probe: a 1-byte Range tells us if the clip exists without
        // holding a browser connection open (6-per-origin) if the box is busy.
        const ctrl = new AbortController();
        const tmo = setTimeout(() => ctrl.abort(), 4000);
        const probe = await fetch(url, { headers: { Range: "bytes=0-0" }, signal: ctrl.signal });
        clearTimeout(tmo);
        try { if (probe.body) probe.body.cancel(); } catch { /* noop */ }
        if (probe.ok || probe.status === 206) { setVideoSrc({ src: url, row }); return; }
      } catch { /* unreachable / timed out — try the next source */ }
    }
    const pc = (row.part_code || "").replace(/:$/, "")
      .replace(/[^A-Za-z0-9._-]/g, "_").replace(/^_+|_+$/g, "");
    if (pc) setVideoSrc({ src: `/cms-api/api/video/by-part?code=${encodeURIComponent(pc)}`, row });
    else setVideoSrc({ src: `/api/lines/${row.line_id}/cycle-video?date=${row.record_date}` +
      `&shift=${encodeURIComponent(row.shift_name || "")}&cycle_seq=${row.cycle_seq}` +
      `&token=${encodeURIComponent(jwt)}`, row });
  };

  // ── browse mode ───────────────────────────────────────────
  const [days,   setDays]   = useState([]);
  const [lines,  setLines]  = useState([]);
  const [shifts, setShifts] = useState([]);
  const [machines, setMachines] = useState([]);

  const [day,     setDay]     = useState("");
  const [lineId,  setLineId]  = useState("");
  const [shift,   setShift]   = useState("");
  const [machine, setMachine] = useState("");

  const [seqQ,   setSeqQ]   = useState("");
  const [partQ,  setPartQ]  = useState("");
  const [ngOnly, setNgOnly] = useState(false);
  const [page,   setPage]   = useState(1);
  const PAGE = 200;

  // 2026-09-19 — the whole shift is loaded at once and paged here, so the
  // comment / over-target filters below count and filter EVERY clip, not just
  // the 200 on the current page.  The server already reads the full shift for
  // each page request, so one large page costs it nothing extra.
  const [clips,   setClips]   = useState([]);      // every clip for the selection
  const [total,   setTotal]   = useState(0);
  // Remarks per cycle, keyed exactly like the player's thread and the
  // wallboard: `cycle_<seq>_<date>` (main) / `M<sub>-C<seq>-<date>` (sub).
  const [cmtMap,  setCmtMap]  = useState({});
  const [cmtFilter, setCmtFilter] = useState("all"); // "all" | "with" | "without"
  const [overOnly,  setOverOnly]  = useState(false);
  // 2026-09-21 — operator: zone always available; hour slot (from the zone's
  // own schedule) and a time window are optional.  Everything defaults to All,
  // and slot / time filter the loaded clips instantly — no new server call.
  const [zoneSel, setZoneSel] = useState("");   // "" = all zones
  const [slotSel, setSlotSel] = useState("");   // "" = all slots (slot id)
  const [slots,   setSlots]   = useState([]);
  const [tFrom,   setTFrom]   = useState("");
  const [tTo,     setTTo]     = useState("");
  // How many cycles actually ran for this line/shift, against how many have a
  // clip.  The archiver can only cut video while the rolling recording still
  // covers that moment (42 min), so on a busy shift most cycles never get one —
  // which made the list look like it skipped numbers at random.
  const [cycTotal, setCycTotal] = useState(0);
  const [bLoading, setBLoading] = useState(false);

  useEffect(() => {
    if (mode !== "browse" || days.length) return;
    api.get("/api/clip-archive/days")
      .then(r => {
        const d = r.data?.days || [];
        setDays(d);
        setDay(prev => prev || d[0] || "");
      })
      .catch(() => setError("Could not read the video archive"));
  }, [mode, days.length]);

  // Each level only offers what the previous level actually recorded, so a
  // combination with no clips can't be selected.
  useEffect(() => {
    setLines([]); setShifts([]); setMachines([]);
    setLineId(""); setShift(""); setMachine(""); setClips([]); setTotal(0);
    if (!day) return;
    api.get(`/api/clip-archive/lines?date=${day}`)
      .then(r => setLines(r.data?.lines || [])).catch(() => setLines([]));
  }, [day]);

  useEffect(() => {
    setShifts([]); setMachines([]); setShift(""); setMachine("");
    setClips([]); setTotal(0);
    if (!day || !lineId) return;
    api.get(`/api/clip-archive/shifts?date=${day}&line_id=${lineId}`)
      .then(r => setShifts(r.data?.shifts || [])).catch(() => setShifts([]));
  }, [day, lineId]);

  useEffect(() => {
    setMachines([]); setMachine(""); setClips([]); setTotal(0);
    if (!day || !lineId || !shift) return;
    api.get(`/api/clip-archive/machines?date=${day}&line_id=${lineId}&shift=${encodeURIComponent(shift)}`)
      .then(r => {
        const m = r.data?.machines || [];
        setMachines(m);
        setMachine(m.length ? m[0].machine : "");   // Final Inspection first
      })
      .catch(() => setMachines([]));
  }, [day, lineId, shift]);

  // Zone of every line (from /api/lines/), the zones present in this day's
  // archive, and the lines of the chosen zone.
  const zoneOf = {};
  allLines.forEach(l => { zoneOf[String(l.id)] = { id: l.zone_id, name: l.zone_name }; });
  const zoneOpts = [];
  lines.forEach(l => {
    const z = zoneOf[String(l.line_id)];
    if (z && z.id != null && !zoneOpts.some(o => String(o.id) === String(z.id)))
      zoneOpts.push({ id: z.id, name: z.name || `Zone ${z.id}` });
  });
  zoneOpts.sort((a, b) => String(a.name).localeCompare(String(b.name)));
  const zoneLines = zoneSel
    ? lines.filter(l => String(zoneOf[String(l.line_id)]?.id) === String(zoneSel))
    : lines;
  // Hour slots follow the zone's schedule: the chosen zone, else the line's zone.
  const slotZone = zoneSel || (lineId ? zoneOf[String(lineId)]?.id : "") || "";

  useEffect(() => {   // a zone with no archive on the chosen day falls back to All
    if (zoneSel && lines.length && !zoneOpts.some(o => String(o.id) === String(zoneSel))) setZoneSel("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lines]);

  useEffect(() => {   // a line outside the chosen zone is cleared
    if (lineId && zoneSel && String(zoneOf[String(lineId)]?.id) !== String(zoneSel)) setLineId("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoneSel]);

  useEffect(() => {
    setSlotSel(""); setSlots([]);
    if (!slotZone || !shift || shift === "GAP") return;
    api.get(`/api/zones/${slotZone}/hourly-slots?shift_name=${encodeURIComponent(shift)}`)
      .then(r => setSlots(Array.isArray(r.data) ? r.data : []))
      .catch(() => setSlots([]));
  }, [slotZone, shift]);

  const clipKey = c => (machine === "main"
    ? `cycle_${c.cycle_seq}_${day}`
    : `M${String(machine).replace(/^sub_/, "")}-C${c.cycle_seq}-${day}`);

  // Every remark written against this day's cycles, whenever it was typed —
  // date_to is today because a remark is often added hours (or a day) later.
  const loadComments = async () => {
    if (!day || !lineId || !shift) { setCmtMap({}); return; }
    try {
      const p = new URLSearchParams({
        date_from: day, date_to: new Date().toLocaleDateString("en-CA"),
        part_code: day, shift_name: shift,
      });
      const r = await api.get(`/api/lines/${lineId}/comments-history?${p}`);
      const m = {};
      for (const row of (r.data?.rows || [])) {           // newest first
        if (!row.part_code || !row.text) continue;
        (m[row.part_code] = m[row.part_code] || []).push(row);
      }
      setCmtMap(m);
    } catch { setCmtMap({}); }
  };

  const loadClips = async () => {
    if (!day || !lineId || !shift || !machine) return;
    setBLoading(true); setError("");
    try {
      const all = [];
      let pg = 1, tot = 0, cyc = 0;
      // 1000 per request is the server's cap; a shift rarely needs a second.
      for (;;) {
        const p = new URLSearchParams({
          date: day, line_id: lineId, shift, machine,
          page: String(pg), page_size: "1000",
        });
        if (seqQ.trim())  p.append("cycle_seq", seqQ.trim());
        if (partQ.trim()) p.append("part_code", partQ.trim());
        if (ngOnly)       p.append("ng_only", "true");
        const r = await api.get(`/api/clip-archive/clips?${p}`);
        all.push(...(r.data?.clips || []));
        tot = r.data?.total || 0;
        cyc = r.data?.cycles_total || 0;
        if (all.length >= tot || !(r.data?.clips || []).length || pg >= 20) break;
        pg += 1;
      }
      setClips(all);
      setTotal(tot);
      setCycTotal(cyc);
      setPage(1);
      loadComments();
    } catch (e) {
      setError(e?.response?.data?.detail || "Could not load clips");
      setClips([]); setTotal(0); setCycTotal(0); setCmtMap({});
    } finally { setBLoading(false); }
  };

  useEffect(() => { if (machine) loadClips(); /* eslint-disable-next-line */ }, [machine]);

  // 2026-09-17 — ticking "Alarm / NG only" now applies immediately.  The
  // checkbox only set state; the list was rebuilt on the Search button, so the
  // tick looked dead ("tick nahi ho raha") until you pressed Search as well.
  // Skipped on first render so it does not double-fetch alongside the effect
  // above when a machine is picked.
  const ngFirst = useRef(true);
  useEffect(() => {
    if (ngFirst.current) { ngFirst.current = false; return; }
    if (machine) loadClips();
    /* eslint-disable-next-line */
  }, [ngOnly]);

  const wasPlaying = useRef(false);
  useEffect(() => {
    if (videoSrc) { wasPlaying.current = true; return; }
    if (wasPlaying.current && mode === "browse" && machine) loadComments();
    wasPlaying.current = false;
    /* eslint-disable-next-line */
  }, [videoSrc]);

  const playClip = (c) => {
    const jwt = sessionStorage.getItem("mes_token") || "";
    const qs = new URLSearchParams({
      date: day, line_id: lineId, shift, machine,
      cycle_seq: String(c.cycle_seq), ng: c.ng ? "true" : "false", token: jwt,
    });
    setVideoSrc({ src: `/api/clip-archive/video?${qs}`, clip: c });
  };

  // Over target = slower than the selected LINE's ideal cycle time — the same
  // yardstick the Comments History "Over target" count uses.
  const idealCt = Number((allLines.find(l => String(l.id) === String(lineId)) || {}).ideal_cycle_time) || null;
  const isOver = c => idealCt != null && c.ct != null && Number(c.ct) > idealCt;
  const hasCmt = c => !!(cmtMap[clipKey(c)] || []).length;
  // Hour slot / time window on the clip's own timestamp.  A window whose end
  // is before its start crosses midnight (B shift), so it wraps.
  const toMin = s => { const [h, m] = String(s || "").split(":"); return (Number(h) || 0) * 60 + (Number(m) || 0); };
  const inWin = (t, a, b) => (a <= b ? t >= a && t < b : t >= a || t < b);
  const slotObj = slots.find(s => String(s.id) === String(slotSel));
  const inTime = c => {
    if (!slotObj && !tFrom && !tTo) return true;
    if (!c.ts) return false;
    const t = toMin(String(c.ts).slice(11, 16));
    if (slotObj && !inWin(t, toMin(slotObj.start_time), toMin(slotObj.end_time))) return false;
    if (tFrom || tTo) {
      const a = tFrom ? toMin(tFrom) : 0;
      const b = tTo ? toMin(tTo) + 1 : 24 * 60;          // "to" is inclusive
      if (!inWin(t, a, b)) return false;
    }
    return true;
  };
  const timeClips = clips.filter(inTime);
  const counts = {
    all: timeClips.length,
    with: timeClips.filter(hasCmt).length,
    without: timeClips.filter(c => !hasCmt(c)).length,
    over: timeClips.filter(isOver).length,
  };
  const shown = timeClips.filter(c =>
    (cmtFilter === "all" || (cmtFilter === "with" ? hasCmt(c) : !hasCmt(c)))
    && (!overOnly || isOver(c)));
  const pages = Math.max(1, Math.ceil(shown.length / PAGE));
  const pageClips = shown.slice((page - 1) * PAGE, page * PAGE);
  const machineName = machines.find(m => m.machine === machine)?.name || machine;

  // Modal header reads from whichever shape opened it (part row or browse clip).
  const v = videoSrc || {};
  const vTitle = v.row
    ? { seq: v.row.cycle_seq, where: v.row.line_name, shift: v.row.shift_name,
        date: v.row.record_date, ct: v.row.ct_value, ideal: v.row.ideal_ct,
        ng: v.row.is_ng, part: v.row.part_code }
    : { seq: v.clip?.cycle_seq, where: machineName, shift, date: day,
        ct: v.clip?.ct, ideal: null, ng: v.clip?.is_ng, part: v.clip?.part_code };

  // Remark thread for the cycle in the player.  The key must match the one the
  // wallboard and management screens build, so a note is the same note on all
  // three: `cycle_<seq>_<record date>`.
  //
  // Only MAIN-line cycles get a thread.  Sub-machines number their cycles
  // independently, so sub_3 #480 and the main line's #480 are unrelated parts —
  // sharing a key would file a remark against the wrong cycle.  The wallboard
  // makes the same call (its panel is gated on isMain).
  const vThread = v.row
    ? { lineId: v.row.line_id, seq: v.row.cycle_seq, date: v.row.record_date,
        shift: v.row.shift_name, machine: "Final Inspection", ng: v.row.is_ng }
    : (machine === "main" && v.clip
        ? { lineId: Number(lineId) || null, seq: v.clip.cycle_seq, date: day,
            shift, machine: "Final Inspection", ng: v.clip.is_ng }
        : null);

  const tabBtn = (key, label) => (
    <button key={key} onClick={() => { setMode(key); setError(""); }} style={{
      padding: "9px 22px", borderRadius: 8, cursor: "pointer", fontSize: 13, fontWeight: 800,
      border: mode === key ? "none" : "1px solid #cbd5e1",
      background: mode === key ? "#1e3a8a" : "#fff",
      color: mode === key ? "#fff" : "#475569",
    }}>{label}</button>
  );

  return (
    <>
      <div style={{ display: "flex", gap: 10, marginBottom: 16 }}>
        {tabBtn("part", "Search by Part ID")}
        {tabBtn("browse", "Browse by Date / Shift")}
      </div>

      {/* ── PART ID MODE ── */}
      {mode === "part" && (
        <>
          <div className="filter-card">
            <div className="filter-grid" style={{ gridTemplateColumns: "1fr 1fr 1fr 1fr" }}>
              <div className="ff" style={{ gridColumn: "1/-1" }}>
                <label>Part ID</label>
                <input value={code} onChange={e => setCode(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") searchPart(); }}
                  placeholder="Enter Part ID (partial match) — video seedha aa jayegi"
                  style={{ fontFamily: "monospace", fontSize: 14, fontWeight: 700, letterSpacing: ".03em" }} />
              </div>
              <div className="ff">
                <label>Line (optional)</label>
                <select value={pLineId} onChange={e => setPLineId(e.target.value)}>
                  <option value="">All Lines</option>
                  {allLines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
                </select>
              </div>
              <div className="ff">
                <label>From</label>
                <input type="date" value={pFrom} onChange={e => setPFrom(e.target.value)} />
              </div>
              <div className="ff">
                <label>To</label>
                <input type="date" value={pTo} onChange={e => setPTo(e.target.value)}
                  max={new Date().toISOString().slice(0, 10)} />
              </div>
              <div className="ff" style={{ display: "flex", alignItems: "flex-end" }}>
                <button onClick={searchPart} disabled={pLoading}
                  style={{
                    width: "100%", padding: "11px 18px", borderRadius: 8, border: "none",
                    background: "#1e3a8a", color: "#fff", fontWeight: 800, fontSize: 14,
                    cursor: pLoading ? "not-allowed" : "pointer", opacity: pLoading ? .55 : 1,
                  }}>{pLoading ? "Searching…" : "Search"}</button>
              </div>
            </div>
          </div>

          {error && <div style={{ padding: 12, color: "#b91c1c", fontWeight: 700, fontSize: 13 }}>{error}</div>}

          {rows && rows.length === 0 && (
            <p style={{ fontSize: 13, color: "#64748b", marginTop: 14 }}>
              No cycles match "{code}" in the selected date range.
            </p>
          )}

          {rows && rows.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: "#334155", marginBottom: 10 }}>
                {rows.length} cycle{rows.length > 1 ? "s" : ""} found — click any row to play its video
              </div>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr style={{ background: "#f1f5f9", textAlign: "left" }}>
                      {["Date", "Shift", "Line", "Zone", "Cycle #", "Part ID", "CT", "Ideal", "Status", ""]
                        .map((h, i) => (
                          <th key={i} style={{ padding: "9px 10px", fontWeight: 800, color: "#334155", whiteSpace: "nowrap" }}>{h}</th>
                        ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => {
                      const isOpen = expanded.has(i);
                      const slow = r.ideal_ct && r.ct_value > r.ideal_ct;
                      return (
                        <Fragment key={i}>
                          <tr style={{
                            borderBottom: "1px solid #e2e8f0", cursor: "pointer",
                            background: r.is_ng ? "#fef2f2" : "#fff",
                          }}
                            onClick={() => setExpanded(s => {
                              const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n;
                            })}>
                            <td style={{ padding: "9px 10px", whiteSpace: "nowrap" }}>{r.record_date}</td>
                            <td style={{ padding: "9px 10px", fontWeight: 700 }}>{r.shift_name}</td>
                            <td style={{ padding: "9px 10px", whiteSpace: "nowrap" }}>{r.line_name}</td>
                            <td style={{ padding: "9px 10px", color: "#64748b" }}>{r.zone_name}</td>
                            <td style={{ padding: "9px 10px", fontFamily: "monospace", fontWeight: 800 }}>#{r.cycle_seq}</td>
                            <td style={{ padding: "9px 10px", fontFamily: "monospace", color: "#3b82f6" }}>
                              {(r.part_code || "").replace(/:$/, "")}
                            </td>
                            <td style={{ padding: "9px 10px", fontWeight: 800, color: slow ? "#dc2626" : "#16a34a" }}>
                              {Number(r.ct_value).toFixed(1)}s
                            </td>
                            <td style={{ padding: "9px 10px", color: "#64748b" }}>{r.ideal_ct}s</td>
                            <td style={{ padding: "9px 10px" }}>
                              {r.is_ng
                                ? <span style={{ color: "#dc2626", fontWeight: 900 }}>ALARM</span>
                                : <span style={{ color: "#16a34a", fontWeight: 800 }}>OK</span>}
                            </td>
                            <td style={{ padding: "9px 10px" }}>
                              <button onClick={e => { e.stopPropagation(); playRow(r); }} style={{
                                padding: "5px 14px", borderRadius: 6, border: "none", background: "#1e3a8a",
                                color: "#fff", fontWeight: 800, fontSize: 12, cursor: "pointer", whiteSpace: "nowrap",
                              }}>▶ Video</button>
                            </td>
                          </tr>
                          {isOpen && (
                            <tr>
                              <td colSpan={10} style={{ padding: 0, background: "#f8fafc" }}>
                                <ProcessHistoryPanel row={r} />
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      {/* ── BROWSE MODE ── */}
      {mode === "browse" && (
        <>
          <div className="filter-card">
            <div className="filter-grid" style={{ gridTemplateColumns: "1fr 1fr 1fr 1fr" }}>
              <div className="ff">
                <label>Date (back days)</label>
                <select value={day} onChange={e => setDay(e.target.value)}>
                  {days.length === 0 && <option value="">No archive</option>}
                  {days.map(d => <option key={d} value={d}>{d}</option>)}
                </select>
              </div>
              <div className="ff">
                <label>Zone</label>
                <select value={zoneSel} onChange={e => setZoneSel(e.target.value)}>
                  <option value="">All zones</option>
                  {zoneOpts.map(z => <option key={z.id} value={z.id}>{z.name}</option>)}
                </select>
              </div>
              <div className="ff">
                <label>Line</label>
                <select value={lineId} onChange={e => setLineId(e.target.value)}>
                  <option value="">Select line</option>
                  {zoneLines.map(l => <option key={l.line_id} value={l.line_id}>{l.name}</option>)}
                </select>
              </div>
              <div className="ff">
                <label>Shift</label>
                <select value={shift} onChange={e => setShift(e.target.value)} disabled={!lineId}>
                  <option value="">Select shift</option>
                  {shifts.map(s => <option key={s} value={s}>{s === "GAP" ? "GAP (between shifts)" : `${s} Shift`}</option>)}
                </select>
              </div>

              <div className="ff">
                <label>Machine</label>
                <select value={machine} onChange={e => setMachine(e.target.value)} disabled={!shift}>
                  <option value="">Select machine</option>
                  {machines.map(m => <option key={m.machine} value={m.machine}>{m.name}{m.is_main ? " (Main)" : ""}</option>)}
                </select>
              </div>
              <div className="ff">
                <label>Hour slot (optional)</label>
                <select value={slotSel} onChange={e => { setSlotSel(e.target.value); setPage(1); }}
                        disabled={!slots.length}>
                  <option value="">All slots</option>
                  {slots.map(s => <option key={s.id} value={s.id}>{s.slot_label}</option>)}
                </select>
              </div>
              <div className="ff">
                <label>Time from (optional)</label>
                <input type="time" value={tFrom} onChange={e => { setTFrom(e.target.value); setPage(1); }} />
              </div>
              <div className="ff">
                <label>Time to (optional)</label>
                <input type="time" value={tTo} onChange={e => { setTTo(e.target.value); setPage(1); }} />
              </div>

              <div className="ff">
                <label>Cycle Serial No</label>
                <input value={seqQ} onChange={e => setSeqQ(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") loadClips(); }}
                  placeholder="e.g. 1859" inputMode="numeric"
                  style={{ fontFamily: "monospace", fontWeight: 700 }} />
              </div>
              <div className="ff">
                <label>Part ID (optional)</label>
                <input value={partQ} onChange={e => setPartQ(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") loadClips(); }}
                  placeholder="partial match"
                  style={{ fontFamily: "monospace", fontWeight: 700 }} />
              </div>
              <div className="ff" style={{ display: "flex", alignItems: "flex-end" }}>
                <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13, fontWeight: 700 }}>
                  <input type="checkbox" checked={ngOnly} onChange={e => setNgOnly(e.target.checked)}
                    style={{ width: 16, height: 16, cursor: "pointer" }} />
                  Alarm / NG only
                </label>
              </div>
              <div className="ff" style={{ display: "flex", alignItems: "flex-end" }}>
                <button onClick={() => loadClips()} disabled={!machine || bLoading}
                  style={{
                    width: "100%", padding: "11px 18px", borderRadius: 8, border: "none",
                    background: "#1e3a8a", color: "#fff", fontWeight: 800, fontSize: 14,
                    cursor: machine && !bLoading ? "pointer" : "not-allowed",
                    opacity: machine && !bLoading ? 1 : .55,
                  }}>{bLoading ? "Loading…" : "Search"}</button>
              </div>
            </div>
          </div>

          {error && <div style={{ padding: 12, color: "#b91c1c", fontWeight: 700, fontSize: 13 }}>{error}</div>}

          {/* 2026-09-19 — operator: "filter for select commented / non
              commented / over target as per line".  Comment filter and
              over-target combine, so "over target + not commented" lists the
              slow cycles nobody has explained yet. */}
          {!error && machine && clips.length > 0 && (() => {
            const chip = (on, label, n, onClick, tone = "#1e3a8a") => (
              <button onClick={onClick} style={{
                padding: "7px 14px", borderRadius: 999, cursor: "pointer", fontSize: 13,
                fontWeight: 800, border: on ? "none" : "1px solid #cbd5e1",
                background: on ? tone : "#fff", color: on ? "#fff" : "#334155",
              }}>{label} <span style={{ opacity: on ? .85 : .6, fontWeight: 700 }}>{n}</span></button>
            );
            const setC = v => { setCmtFilter(v); setPage(1); };
            return (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center",
                            margin: "4px 0 12px" }}>
                {chip(cmtFilter === "all",     "All",           counts.all,     () => setC("all"))}
                {chip(cmtFilter === "with",    "Commented",     counts.with,    () => setC("with"), "#2563eb")}
                {chip(cmtFilter === "without", "Not commented", counts.without, () => setC("without"), "#475569")}
                <span style={{ width: 1, height: 22, background: "#cbd5e1", margin: "0 4px" }} />
                {idealCt != null
                  ? chip(overOnly, `Over target (> ${idealCt}s)`, counts.over,
                         () => { setOverOnly(v => !v); setPage(1); }, "#b45309")
                  : <span style={{ fontSize: 12, color: "#94a3b8" }}>
                      Over target: this line has no ideal cycle time set</span>}
              </div>
            );
          })()}

          {!error && machine && (
            <div style={{ marginTop: 16 }}>
              <div style={{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                marginBottom: 10, fontSize: 13, fontWeight: 700, color: "#334155",
              }}>
                <span>{machineName} — {day} — {shift} Shift : <b>{total}</b> videos
                  {shown.length !== clips.length && (
                    <span style={{ color: "#1e3a8a", marginLeft: 6 }}>
                      · showing <b>{shown.length}</b> that match the filter
                    </span>
                  )}
                  {cycTotal > 0 && (
                    <span style={{ color: total * 100 / cycTotal < 50 ? "#b45309" : "#64748b",
                                   fontWeight: 600, marginLeft: 8 }}>
                      · {cycTotal} cycles ran, {Math.round(total * 100 / cycTotal)}% have video
                      {total * 100 / cycTotal < 50 &&
                        " — the rest were never recorded (footage window passed)"}
                    </span>
                  )}
                </span>
                {pages > 1 && (
                  <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <button onClick={() => setPage(page - 1)} disabled={page <= 1 || bLoading}
                      style={{
                        padding: "5px 12px", borderRadius: 6, border: "1px solid #cbd5e1",
                        background: "#fff", cursor: page > 1 ? "pointer" : "not-allowed", fontWeight: 700,
                      }}>‹ Prev</button>
                    <span>Page {page} / {pages}</span>
                    <button onClick={() => setPage(page + 1)} disabled={page >= pages || bLoading}
                      style={{
                        padding: "5px 12px", borderRadius: 6, border: "1px solid #cbd5e1",
                        background: "#fff", cursor: page < pages ? "pointer" : "not-allowed", fontWeight: 700,
                      }}>Next ›</button>
                  </span>
                )}
              </div>

              {clips.length === 0 && !bLoading && (
                <p style={{ fontSize: 13, color: "#64748b" }}>No videos for this selection.</p>
              )}
              {clips.length > 0 && shown.length === 0 && !bLoading && (
                <p style={{ fontSize: 13, color: "#64748b" }}>No videos match this filter.</p>
              )}

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(190px,1fr))", gap: 10 }}>
                {pageClips.map(c => { const cm = cmtMap[clipKey(c)] || []; const over = isOver(c); return (
                  <button key={`${c.cycle_seq}-${c.ng}`} onClick={() => playClip(c)} style={{
                    textAlign: "left", padding: "10px 12px", borderRadius: 10, cursor: "pointer",
                    border: `1px solid ${c.is_ng ? "#fecaca" : "#e2e8f0"}`,
                    background: c.is_ng ? "#fef2f2" : "#fff",
                  }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <span style={{ fontSize: 15, fontWeight: 900, color: "#0f172a", fontFamily: "monospace" }}>
                        #{c.cycle_seq}
                      </span>
                      {c.is_ng
                        ? <span style={{ fontSize: 10, fontWeight: 900, color: "#dc2626" }}>ALARM</span>
                        : <span style={{ fontSize: 10, fontWeight: 800, color: "#16a34a" }}>OK</span>}
                    </div>
                    <div style={{ fontSize: 12, color: "#475569", marginTop: 3, fontWeight: 700 }}>
                      {c.ts ? new Date(c.ts).toLocaleTimeString("en-IN", { hour12: false }) : "—"}
                      {c.ct != null && <> · <span style={over ? { color: "#b45309", fontWeight: 900 } : undefined}
                        title={over ? `Over the line's ${idealCt}s target` : undefined}>
                        {Number(c.ct).toFixed(1)}s</span></>}
                    </div>
                    <div style={{
                      fontSize: 10, color: "#94a3b8", marginTop: 2, fontFamily: "monospace",
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    }}>
                      {c.part_code || "—"}
                    </div>
                    {cm.length > 0 && (
                      <div title={cm.map(x => `${x.text}${x.author ? " — " + x.author : ""}`).join("\n")}
                        style={{
                          marginTop: 6, padding: "4px 7px", borderRadius: 6,
                          background: "#eff6ff", borderLeft: "3px solid #2563eb",
                          fontSize: 11.5, fontWeight: 600, color: "#1e3a8a", lineHeight: 1.3,
                          display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                          overflow: "hidden", wordBreak: "break-word",
                        }}>
                        {cm[0].text}
                        {cm.length > 1 && <span style={{ color: "#64748b", fontWeight: 700 }}> +{cm.length - 1} more</span>}
                      </div>
                    )}
                  </button>
                ); })}
              </div>
            </div>
          )}
        </>
      )}

      {/* ── shared player ── */}
      {videoSrc && (
        <div onClick={() => setVideoSrc(null)} style={{
          position: "fixed", inset: 0, zIndex: 9999, background: "rgba(0,0,0,.82)",
          display: "flex", alignItems: "center", justifyContent: "center",
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: "#0a0f1a", borderRadius: 12, padding: 16, maxWidth: 820, width: "90vw",
            boxShadow: "0 24px 72px rgba(0,0,0,.6)", border: "1px solid #141e2e",
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <span style={{ fontSize: 12, fontWeight: 800, color: "#e8edf5", letterSpacing: ".04em" }}>
                Cycle <span style={{ color: "#3b82f6", fontFamily: "monospace" }}>#{vTitle.seq}</span>
                {"  |  "}{vTitle.where}
                {"  |  "}{vTitle.shift} Shift
                {"  |  "}{vTitle.date}
                {vTitle.ct != null && <>{"  |  "}{Number(vTitle.ct).toFixed(1)}s</>}
                {vTitle.ideal != null && <>{"  |  "}Ideal: {vTitle.ideal}s</>}
                {"  |  "}{vTitle.ng
                  ? <span style={{ color: "#ef4444", fontWeight: 900 }}>Alarm !</span>
                  : <span style={{ color: "#22c55e" }}>OK</span>}
              </span>
              <button onClick={() => setVideoSrc(null)} style={{
                background: "transparent", border: "none", cursor: "pointer",
                fontSize: 22, lineHeight: 1, color: "#8092af", padding: "0 4px",
              }}>×</button>
            </div>
            {vTitle.part && (
              <div style={{ fontSize: 11, color: "#8092af", fontFamily: "monospace", marginBottom: 8 }}>
                Part: {String(vTitle.part).replace(/:$/, "")}
              </div>
            )}
            <video
              ref={vidRef}
              autoPlay
              onClick={e => { e.target.paused ? e.target.play() : e.target.pause(); }}
              onError={() => setVideoSrc(v2 => v2 ? { ...v2, error: true } : v2)}
              style={{ width: "100%", borderRadius: 8, maxHeight: "68vh", background: "#000", display: "block" }}
              src={videoSrc.error ? "" : videoSrc.src}
            />
            {/* always-visible progress + time (native strip fades out) */}
            <VideoProgressBar videoRef={vidRef} />
            {videoSrc.error && (
              <div style={{
                padding: "24px 16px", textAlign: "center", color: "#8092af", fontSize: 13,
                fontWeight: 600, background: "#070c14", borderRadius: 8, marginTop: 8,
              }}>
                Video not available for this cycle
              </div>
            )}
            {vThread && vThread.lineId && vThread.seq != null && vThread.date && (
              <ArchiveCycleComments
                lineId={vThread.lineId}
                partCode={`cycle_${vThread.seq}_${vThread.date}`}
                shift={vThread.shift}
                recordDate={vThread.date}
                machineName={vThread.machine}
                isNg={vThread.ng}
              />
            )}
          </div>
        </div>
      )}
    </>
  );
}


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
                    <td style={{fontWeight:700}}>{r.line_name || "—"}</td>
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


// ── Breakdown History (master mes_breakdown_log) sub-component ──────────
// 2026-06-18 — All-plant breakdown MASTER: FY25-26 historical base + manual
// entries, ONE flat format, ONE table.  All-lines = whole master; pick
// zone→line to drill.  Zone/line/machine dropdowns are derived from the
// master itself (independent of MES production lines).  "+ Add Breakdown"
// writes a new row (source='manual') that shows up here instantly.
function BreakdownLogTab() {
  const { theme } = useAuth();
  const [master, setMaster]   = useState({ zones: [], depts: [], categories: [] });
  const [fZone,  setFZone]    = useState("");
  const [fLine,  setFLine]    = useState("");
  const [fFrom,  setFFrom]    = useState("");
  const [fTo,    setFTo]      = useState("");
  const [fq,     setFq]       = useState("");
  const [rows,   setRows]     = useState([]);
  const [total,  setTotal]    = useState(0);
  const [hours,  setHours]    = useState(0);
  const [loading,setLoading]  = useState(true);
  const [addOpen,setAddOpen]  = useState(false);

  useEffect(() => {
    api.get("/api/breakdowns/log/master")
      .then(r => setMaster({ zones: r.data?.zones || [], depts: r.data?.depts || [], categories: r.data?.categories || [] }))
      .catch(() => {});
  }, []);

  const reload = () => {
    setLoading(true);
    const q = new URLSearchParams();
    if (fZone)      q.set("zone", fZone);
    if (fLine)      q.set("line", fLine);
    if (fFrom)      q.set("date_from", fFrom);
    if (fTo)        q.set("date_to", fTo);
    if (fq.trim())  q.set("q", fq.trim());
    api.get(`/api/breakdowns/log?${q.toString()}`)
      .then(r => { setRows(r.data?.rows || []); setTotal(r.data?.total || 0); setHours(r.data?.total_hours || 0); })
      .catch(() => { setRows([]); setTotal(0); setHours(0); })
      .finally(() => setLoading(false));
  };
  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [fZone, fLine, fFrom, fTo]);

  const allZones    = master.zones.map(z => z.zone);
  const linesOfZone = (z) => { const zo = master.zones.find(x => x.zone === z); return zo ? zo.lines.map(l => l.line) : []; };

  return (
    <div className="result-card">
      <div style={{display:"flex",gap:14,flexWrap:"wrap",marginBottom:18,alignItems:"flex-end"}}>
        <div className="ff" style={{minWidth:150}}>
          <label>Zone</label>
          <select value={fZone} onChange={e=>{ setFZone(e.target.value); setFLine(""); }}>
            <option value="">All zones</option>
            {allZones.map(z=><option key={z} value={z}>{z}</option>)}
          </select>
        </div>
        <div className="ff" style={{minWidth:150}}>
          <label>Line</label>
          <select value={fLine} onChange={e=>setFLine(e.target.value)} disabled={!fZone}>
            <option value="">All lines</option>
            {linesOfZone(fZone).map(l=><option key={l} value={l}>{l}</option>)}
          </select>
        </div>
        <div className="ff" style={{minWidth:140}}>
          <label>From</label>
          <input type="date" value={fFrom} onChange={e=>setFFrom(e.target.value)} />
        </div>
        <div className="ff" style={{minWidth:140}}>
          <label>To</label>
          <input type="date" value={fTo} onChange={e=>setFTo(e.target.value)} />
        </div>
        <div className="ff" style={{flex:1,minWidth:170}}>
          <label>Search</label>
          <input type="text" value={fq} placeholder="machine / problem / action / person…"
                 onChange={e=>setFq(e.target.value)} onKeyDown={e=>{ if(e.key==="Enter") reload(); }} />
        </div>
        <button onClick={reload} style={{padding:"11px 16px",borderRadius:8,border:"1.5px solid #e2e8f0",
            background:"#fff",color:"#334155",fontWeight:700,cursor:"pointer"}}>Search</button>
        <button onClick={()=>setAddOpen(true)} style={{padding:"11px 18px",borderRadius:8,border:"none",
            background:theme.accentDark,color:"#fff",fontWeight:800,cursor:"pointer"}}>+ Add Breakdown</button>
      </div>

      <div style={{display:"flex",gap:26,marginBottom:14,fontSize:13,alignItems:"baseline"}}>
        <div><b style={{fontSize:22,fontFamily:"monospace"}}>{total}</b> <span style={{color:"#64748b"}}>breakdowns</span></div>
        <div><b style={{fontSize:22,fontFamily:"monospace"}}>{hours}</b> <span style={{color:"#64748b"}}>downtime hrs</span></div>
        {(fZone||fLine||fFrom||fTo) && <div style={{color:"#94a3b8"}}>filter: {fZone||"all"}{fLine?` / ${fLine}`:""}</div>}
      </div>

      <div style={{overflowX:"auto"}}>
        <table className="slot-tbl" style={{minWidth:1120}}>
          <thead><tr>
            {["Date","Zone","Line","Machine","Sh","Problem","Action","Spares","By","Dept","Cat","Min","Src"].map(h=>
              <th key={h}>{h}</th>)}
          </tr></thead>
          <tbody>
            {loading
              ? <tr><td colSpan={13} style={{padding:24,textAlign:"center",color:"#94a3b8"}}>Loading…</td></tr>
              : rows.length===0
                ? <tr><td colSpan={13} style={{padding:24,textAlign:"center",color:"#94a3b8"}}>No breakdowns for this filter.</td></tr>
                : rows.map(r=>(
                  <tr key={r.id}>
                    <td style={{whiteSpace:"nowrap"}}>{r.bd_date||"—"}</td>
                    <td>{r.zone_code||"—"}</td>
                    <td style={{fontWeight:700}}>{r.line_code||"—"}</td>
                    <td>{r.machine_name||"—"}{r.machine_no?<span style={{color:"#94a3b8"}}> ({r.machine_no})</span>:null}</td>
                    <td>{r.shift||"—"}</td>
                    <td style={{maxWidth:230}}>{r.problem_production||r.problem_maintenance||"—"}</td>
                    <td style={{maxWidth:230}}>{r.action_taken||"—"}</td>
                    <td style={{maxWidth:130}}>{r.spares_detail||"—"}</td>
                    <td>{r.attended_by||"—"}</td>
                    <td>{r.dept||"—"}</td>
                    <td>{r.category||"—"}</td>
                    <td style={{textAlign:"right",fontFamily:"monospace"}}>{r.solve_time_min!=null?r.solve_time_min:"—"}</td>
                    <td><span style={{fontSize:9,fontWeight:700,padding:"2px 7px",borderRadius:99,
                        background:r.source==="manual"?"rgba(59,130,246,.12)":"#f1f5f9",
                        color:r.source==="manual"?"#1e40af":"#64748b"}}>
                        {r.source==="manual"?"manual":r.source==="live"?"live":"hist"}</span></td>
                  </tr>
                ))}
          </tbody>
        </table>
      </div>

      {addOpen && <BreakdownLogAddModal master={master}
                    onClose={()=>setAddOpen(false)}
                    onSaved={()=>{ setAddOpen(false); reload(); }} />}
    </div>
  );
}

// "+ Add Breakdown" form — same flat format, writes source='manual'.
// Zone/line/machine/dept/category use datalists from the master (pick existing
// OR type a brand-new one — new zones/lines auto-appear in the master next load).
function BreakdownLogAddModal({ master, onClose, onSaved }) {
  const { theme } = useAuth();
  const today = new Date().toISOString().slice(0,10);
  const [f, setF] = useState({
    zone_code:"", line_code:"", machine_no:"", machine_name:"", bd_date:today, shift:"A",
    nature_of_work:"BREAKDOWN", problem_production:"", problem_maintenance:"", action_taken:"",
    bd_start_time:"", bd_received_time:"", bd_ok_time:"", solve_time_min:"", spares_detail:"",
    attended_by:"", dept:"", handover_to:"", category:"", remarks:"",
    model_no:"", line_leader_name:"", machine_operator_name:"", bd_start_date:today, bd_end_date:today,
    problem_related_to:"", type_of_problem:"", prepared_by:"", received_by:"",
    line_leader_operator:"", quality_engineer:"",
  });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const up = (k,v)=>setF(p=>({...p,[k]:v}));

  const allZones    = master.zones.map(z=>z.zone);
  const allLines    = [...new Set(master.zones.flatMap(z=>z.lines.map(l=>l.line)))].sort();
  const allMachines = [...new Set(master.zones.flatMap(z=>z.lines.flatMap(l=>l.machines)))].sort();

  const TA = {background:"#f8fafc",border:"1.5px solid #e2e8f0",borderRadius:8,padding:"10px 12px",
    color:"#0f172a",fontFamily:"'Barlow',sans-serif",fontSize:13,outline:"none",width:"100%",resize:"vertical"};

  const save = async () => {
    if (!f.line_code.trim() && !f.machine_name.trim()) { setErr("Line ya machine to daalo."); return; }
    setSaving(true); setErr("");
    try {
      const mins = f.solve_time_min!=="" ? Number(f.solve_time_min) : null;
      await api.post("/api/breakdowns/log", {
        ...f,
        solve_time_min: mins,
        solve_time_hours: mins!=null ? Number((mins/60).toFixed(4)) : null,
        bd_date: f.bd_date || null,
      });
      onSaved();
    } catch(e){ setErr(e?.response?.data?.detail || e.message || "Save failed"); }
    finally { setSaving(false); }
  };

  const F = ({label,k,type="text",list,area}) => (
    <div className="ff" style={area?{gridColumn:"1 / -1"}:undefined}>
      <label>{label}</label>
      {area
        ? <textarea rows={2} style={TA} value={f[k]} onChange={e=>up(k,e.target.value)} />
        : <input type={type} list={list} value={f[k]} onChange={e=>up(k,e.target.value)} />}
    </div>
  );

  return (
    <div onClick={onClose} style={{position:"fixed",inset:0,background:"rgba(0,0,0,.5)",zIndex:1000,
         display:"flex",alignItems:"center",justifyContent:"center",padding:20}}>
      <div onClick={e=>e.stopPropagation()} style={{background:"#fff",borderRadius:14,padding:26,
           width:"min(920px,96vw)",maxHeight:"92vh",overflowY:"auto",boxShadow:"0 20px 60px rgba(0,0,0,.3)"}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:18}}>
          <div style={{fontSize:18,fontWeight:800,color:"#0f172a"}}>+ Add Breakdown</div>
          <button onClick={onClose} style={{border:"1px solid #e2e8f0",background:"#fff",borderRadius:8,
              padding:"4px 12px",fontSize:18,fontWeight:700,cursor:"pointer",lineHeight:1}}>×</button>
        </div>

        <datalist id="bdlog-z">{allZones.map(z=><option key={z} value={z}/>)}</datalist>
        <datalist id="bdlog-l">{allLines.map(l=><option key={l} value={l}/>)}</datalist>
        <datalist id="bdlog-m">{allMachines.map(m=><option key={m} value={m}/>)}</datalist>

        <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(200px,1fr))",gap:14}}>
          <F label="Zone"          k="zone_code"   list="bdlog-z" />
          <F label="Line"          k="line_code"   list="bdlog-l" />
          <F label="Machine No."   k="machine_no" />
          <F label="Machine Name"  k="machine_name" list="bdlog-m" />
          <F label="Breakdown Date" k="bd_date" type="date" />
          <div className="ff"><label>Shift</label>
            <select value={f.shift} onChange={e=>up("shift",e.target.value)}>
              <option value="A">A</option><option value="B">B</option></select></div>
          <div className="ff"><label>Dept</label>
            <input list="bdlog-dept" value={f.dept} onChange={e=>up("dept",e.target.value)} />
            <datalist id="bdlog-dept">{(master.depts||[]).map(d=><option key={d} value={d}/>)}</datalist></div>
          <div className="ff"><label>Category</label>
            <input list="bdlog-cat" value={f.category} onChange={e=>up("category",e.target.value)} />
            <datalist id="bdlog-cat">{(master.categories||[]).map(d=><option key={d} value={d}/>)}</datalist></div>
          <F label="Nature of Work" k="nature_of_work" />
          <F label="B/D Start Time" k="bd_start_time" />
          <F label="B/D Received Time" k="bd_received_time" />
          <F label="B/D OK Time"    k="bd_ok_time" />
          <F label="Solve Time (min)" k="solve_time_min" type="number" />
          <F label="Attended By"    k="attended_by" />
          <F label="Handover To"    k="handover_to" />
          <F label="Model No."      k="model_no" />
          <F label="Line Leader"    k="line_leader_name" />
          <F label="Machine Operator" k="machine_operator_name" />
          <F label="B/D Start Date" k="bd_start_date" type="date" />
          <F label="B/D End Date"   k="bd_end_date" type="date" />
          <F label="Problem Related To" k="problem_related_to" />
          <F label="Type of Problem"    k="type_of_problem" />
          <F label="Prepared By"    k="prepared_by" />
          <F label="Received By"    k="received_by" />
          <F label="Line Leader (Operator)" k="line_leader_operator" />
          <F label="Quality Engineer"   k="quality_engineer" />
          <F label="Problem (Production)"  k="problem_production"  area />
          <F label="Problem (Maintenance)" k="problem_maintenance" area />
          <F label="Action Taken"   k="action_taken" area />
          <F label="Spares Detail"  k="spares_detail" area />
          <F label="Remarks"        k="remarks" area />
        </div>

        {err && <div className="err-box" style={{marginTop:14}}>{err}</div>}
        <div style={{display:"flex",gap:10,justifyContent:"flex-end",marginTop:20}}>
          <button onClick={onClose} style={{padding:"11px 18px",borderRadius:8,border:"1.5px solid #e2e8f0",
              background:"#fff",color:"#334155",fontWeight:700,cursor:"pointer"}}>Cancel</button>
          <button onClick={save} disabled={saving} style={{padding:"11px 22px",borderRadius:8,border:"none",
              background:saving?"#94a3b8":theme.accentDark,color:"#fff",fontWeight:800,
              cursor:saving?"not-allowed":"pointer"}}>{saving?"Saving…":"Save Breakdown"}</button>
        </div>
      </div>
    </div>
  );
}


// ── Main Historical component ──────────────────────────────
export default function Historical() {
  const { theme, canAccessModule } = useAuth();
  const [zones,    setZones]    = useState([]);
  const [lines,    setLines]    = useState([]);
  const [slots,    setSlots]    = useState([]);
  const [selZone,  setSelZone]  = useState("");
  const [selLine,  setSelLine]  = useState("");
  const [selDate,  setSelDate]  = useState(new Date().toISOString().split("T")[0]);
  // Export date range (Hourly Report tab) — defaults to today; blank To = single day.
  const [expFrom,  setExpFrom]  = useState(new Date().toISOString().split("T")[0]);
  const [expTo,    setExpTo]    = useState(new Date().toISOString().split("T")[0]);
  const [selShift, setSelShift] = useState("A");
  const [selSlot,  setSelSlot]  = useState("");
  const [loading,  setLoading]  = useState(false);
  const [result,   setResult]   = useState(null);
  const [error,    setError]    = useState("");
  const [npdEntries, setNpdEntries] = useState([]);
  // 2026-08-24 — the active tab lives in the URL (?tab=…) instead of local
  // state, so a page REFRESH stays on the same tab (only the data reloads) and
  // the browser BACK button returns to the previous tab.  Default: hourly.
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get("tab") || "shift";
  const setActiveTab = (t) => setSearchParams(
    (prev) => { const p = new URLSearchParams(prev); p.set("tab", t); return p; },
    { replace: false },   // push → BACK returns to the previous tab
  );
  // 2026-09-13 — per-user MODULE access: an admin can assign individual tabs
  // (e.g. only "Hourly Report").  Hidden tabs drop out of the bar, and if the
  // URL points at a hidden tab we fall back to the first visible one.
  const HIST_TABS = [
    { key: "shift", label: "Hourly Report" },
    { key: "video", label: "Video Archive" },
    { key: "trace", label: "Part Traceability" },
    { key: "slips", label: "Breakdown Slips" },
    { key: "bdlog", label: "Breakdown History" },
  ];
  const visibleTabs = HIST_TABS.filter(t => canAccessModule("historical", t.key));
  const effTab = visibleTabs.some(t => t.key === activeTab)
    ? activeTab : (visibleTabs[0]?.key || activeTab);

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

  // 2026-08-24 — Export moved here from the standalone Import/Export page.
  // Exports the selected line + selected date as the operator's hourly-report
  // xlsx (one sheet per shift, transposed layout) via /api/export/data.
  const exportHourly = async (fmt = "xlsx") => {
    if (!selLine) { setError("Line select karo pehle."); return; }
    const from = expFrom, to = expTo || expFrom;
    if (!from) { setError("Choose a date range to export."); return; }
    if (from > to) { setError("Export: the From date must be on or before the To date."); return; }
    setError("");
    try {
      const jwt = sessionStorage.getItem("mes_token") || "";
      const url = `/api/export/data?line_id=${selLine}&date_from=${from}&date_to=${to}&format=${fmt}`;
      const res = await fetch(url, { headers: { Authorization: `Bearer ${jwt}` } });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Export failed" }));
        throw new Error(err.detail || "Export failed");
      }
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      const ln = lines.find(l => String(l.id) === String(selLine))?.line_name || "line";
      const base = from === to ? `HourlyReport_${ln}_${from}` : `HourlyReport_${ln}_${from}_to_${to}`;
      a.download = `${base}.${fmt}`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { setError(e.message || "Export failed"); }
  };

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

  // 2026-06-19 — the OT slot is permanent in mes_hourly_slots once OT ever runs
  // for a line, so it used to render an empty "OT" row + "OT ACTIVE" badge on
  // EVERY historical day even when OT was off.  Gate it: only show the OT slot
  // when it actually has activity that shift (actual > 0 or a manual OT target).
  const _isOtSlot   = (s) => (s.slot_label || "").toUpperCase().includes("OT");
  const _otHadData  = (data, s) => {
    const p = s.db_column_prefix;
    return ((data[`${p}_actual`] || 0) > 0) || ((data[`${p}_plan`] || 0) > 0);
  };
  const buildSlotRows = (data) => slots
    .filter(s => !_isOtSlot(s) || _otHadData(data, s))
    .map(s => {
      const p = s.db_column_prefix;
      const plan     = data[`${p}_plan`]     || 0;   // OT plan overlaid by backend from ot_plan
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
        .hist-body.hist-body-wide{max-width:none;padding:28px 32px 0;}
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

        {/* Video Archive uses the full width — a grid of clip cards gains a
            column per ~200px, and at a fixed 1000px a 1920 screen showed four
            columns with half the page empty ("side me space ki need kyu h"). */}
        <div className={effTab === "video" ? "hist-body hist-body-wide" : "hist-body"}>

          {/* Tab bar */}
          <div style={{display:"flex",gap:0,marginBottom:24,background:"#fff",borderRadius:"12px 12px 0 0",
            border:"1px solid #e2e8f0",borderBottom:"2px solid #e2e8f0",overflow:"hidden"}}>
            {visibleTabs.map(t=>(
              <button key={t.key} onClick={()=>setActiveTab(t.key)} style={{
                flex:1,padding:"13px 20px",fontFamily:"'Barlow',sans-serif",fontSize:14,fontWeight:700,
                cursor:"pointer",border:"none",background:effTab===t.key?theme.accentDark:"#fff",
                color:effTab===t.key?"#fff":"#64748b",
                transition:"all .15s",
              }}>{t.label}</button>
            ))}
          </div>

          {/* Video Archive tab — 2026-08-19: absorbed the old "Part Search"
              tab.  Part ID search now plays the ARCHIVED clip for that exact
              cycle (with the same fallback ladder) and still shows the full
              part detail + per-process breakdown, so the separate tab was
              redundant.  Browse mode covers the no-part-code case. */}
          {effTab === "video" && <VideoArchiveTab zones={zones} />}

          {/* Part Traceability tab — 2026-08-24: one part code → its whole
              life (OK/NG at Semi-Auto & Final, load values, every run with
              video, reject signal, remarks) via /api/lines/part-trace. */}
          {effTab === "trace" && <PartTrace />}

          {/* Breakdown Slips tab — Production-fill records + viewable
              slip archive.  Same /api/breakdowns/history backing as the
              Maintenance Historical page, but presented as a flat
              filterable list (no MTBF / MTTR roll-ups since those are
              maintenance-side metrics). */}
          {effTab === "slips" && <BreakdownSlipsTab />}

          {/* Breakdown History tab — master mes_breakdown_log (FY25-26 base +
              manual entries), zone/line filter + Add form, all-plant. */}
          {effTab === "bdlog" && <BreakdownLogTab />}

          {/* Shift Data tab (existing content) */}
          {effTab === "shift" && <>

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

            <div style={{ display:"flex", gap:10, flexWrap:"wrap", alignItems:"center" }}>
              <button className="fetch-btn" onClick={fetchData} disabled={loading || !selLine}
                      style={{ flex:"1 1 auto" }}>
                {loading
                  ? <><div className="spinner"/> Fetching…</>
                  : <>🔍 Fetch Data</>
                }
              </button>
              {/* Export moved from the old Import/Export page — selected line +
                  date RANGE → hourly-report xlsx (one sheet per shift). */}
              <div style={{ display:"flex", gap:6, alignItems:"center", flexWrap:"wrap",
                            flex:"1 1 auto", minWidth:0, maxWidth:"100%", boxSizing:"border-box",
                            padding:"6px 10px", background:"#f0fdf4",
                            border:"1px solid #bbf7d0", borderRadius:10 }}>
                <span style={{ fontSize:12, fontWeight:800, color:"#166534" }}>Export:</span>
                <input type="date" value={expFrom} onChange={e=>setExpFrom(e.target.value)}
                       max={new Date().toISOString().split("T")[0]}
                       style={{ padding:"7px 8px", border:"1px solid #cbd5e1", borderRadius:8, fontSize:12,
                                minWidth:0, flex:"1 1 120px" }} />
                <span style={{ fontSize:13, color:"#94a3b8" }}>→</span>
                <input type="date" value={expTo} onChange={e=>setExpTo(e.target.value)}
                       max={new Date().toISOString().split("T")[0]}
                       style={{ padding:"7px 8px", border:"1px solid #cbd5e1", borderRadius:8, fontSize:12,
                                minWidth:0, flex:"1 1 120px" }} />
                <button onClick={() => exportHourly("xlsx")} disabled={!selLine} title="Hourly Report — Excel"
                        style={{ padding:"9px 16px", fontWeight:800, fontSize:13, border:"none", borderRadius:8,
                                 cursor: selLine?"pointer":"not-allowed",
                                 background: selLine ? "#16a34a" : "#cbd5e1", color:"#fff" }}>
                  ⬇ Excel
                </button>
                <button onClick={() => exportHourly("pdf")} disabled={!selLine} title="Hourly Report — PDF (grid + loss Pareto)"
                        style={{ padding:"9px 16px", fontWeight:800, fontSize:13, border:"none", borderRadius:8,
                                 cursor: selLine?"pointer":"not-allowed",
                                 background: selLine ? "#dc2626" : "#cbd5e1", color:"#fff" }}>
                  ⬇ PDF
                </button>
              </div>
            </div>
          </div>

          {/* Results (hourly) on the LEFT, PEFF Details on the RIGHT — side by
              side; on narrow screens the right column wraps below full-width. */}
          {result && (
          <div style={{ display:"flex", gap:20, alignItems:"flex-start", flexWrap:"wrap" }}>
            <div style={{ flex:"1 1 620px", minWidth:0 }}>
            <div className="result-card" style={{ marginTop:0 }}>

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
                    <StatTile label="Alarm Count" value={result.data.ng||0}     color="#dc2626" />
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
                    {slots.some(s => _isOtSlot(s) && _otHadData(result.data || {}, s)) && (
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
                    <StatTile label="Alarm Count"     value={result.data.ng_count||0}                        color="#dc2626" />
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
                                {["Slot","Plan","Actual","Variance","OK","Alarm","Efficiency"].map(h=>(
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
            </div>

            {/* PEFF Details — man-hour breakdown for the fetched line/shift/date,
                to the RIGHT of the hourly report (values auto-fill pending). */}
            <div style={{ flex:"0 1 420px", minWidth:300, maxWidth:480, width:"100%" }}>
              <PeffDetails
                lineName={lines.find(l => String(l.id) === String(selLine))?.line_name || ""}
                date={selDate}
                shift={selShift}
              />
            </div>
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