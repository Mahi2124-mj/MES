// ─────────────────────────────────────────────────────────────
// FullscreenYWD.jsx  —  dedicated MODEL-AWARE fullscreen (YWD-SS)
// The plan follows the model the operator runs (INR 25.7s vs OTR
// 14.42s → different rate).  Everything model-aware is derived
// SERVER-SIDE, read-only, from the per-cycle CT log via
// GET /api/lines/{id}/model-plan — so it is fully RELOAD-PROOF and
// the collector is never touched.  Per-model CT is configured in
// Production Admin → Models.  Every OTHER line keeps the shared
// Fullscreen.jsx untouched.
// ─────────────────────────────────────────────────────────────
import { useState, useEffect, useMemo } from "react";
import { useParams } from "react-router-dom";
import axios from "axios";

const api = axios.create({ baseURL: "", timeout: 12000 });
api.interceptors.request.use(cfg => {
  const t = sessionStorage.getItem("mes_token");
  if (t) cfg.headers.Authorization = `Bearer ${t}`;
  return cfg;
});

const GRP_COLOR = (g) => (g === "INR" ? "#38bdf8" : g === "OTR" ? "#f59e0b" : "#94a3b8");
const modelGroup = (name) => {
  const n = (name || "").toUpperCase();
  if (n.startsWith("INR")) return "INR";
  if (n.startsWith("OTR")) return "OTR";
  return name || "—";
};

export default function FullscreenYWD() {
  const { lineId } = useParams();
  const [line, setLine] = useState(null);
  const [rt, setRt]     = useState(null);
  const [mp, setMp]     = useState(null);        // model-plan payload
  const [clock, setClock] = useState(() => new Date());
  const [hover, setHover] = useState(false);

  // clock
  useEffect(() => { const t = setInterval(() => setClock(new Date()), 1000); return () => clearInterval(t); }, []);

  // line meta once
  useEffect(() => { api.get(`/api/lines/${lineId}`).then(r => setLine(r.data)).catch(()=>{}); }, [lineId]);

  // realtime (3s) — live OK/status/model
  useEffect(() => {
    let alive = true;
    const poll = () => api.get(`/api/lines/${lineId}/realtime`).then(r => { if (alive) setRt(r.data); }).catch(()=>{});
    poll(); const t = setInterval(poll, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [lineId]);

  // model-plan (15s) — reload-proof, server-derived from ct_log
  const shiftName = rt?.shift_name;
  useEffect(() => {
    let alive = true;
    const poll = () => api.get(`/api/lines/${lineId}/model-plan`, { params: shiftName ? { shift: shiftName } : {} })
      .then(r => { if (alive) setMp(r.data); }).catch(()=>{});
    poll(); const t = setInterval(poll, 15000);
    return () => { alive = false; clearInterval(t); };
  }, [lineId, shiftName]);

  const enabled = mp?.enabled !== false && mp?.totals;
  const curName = rt?.current_model_name || "—";
  // current group from the live PLC model when available (leads ct_log),
  // else the ct-log-derived group; CT is always looked up FROM that group so
  // the model name and its ideal CT on the header never disagree.
  const rtGrp   = rt?.current_model_name ? modelGroup(rt.current_model_name) : null;
  const curGrp  = rtGrp || mp?.current_group || "—";
  const curCt   = ((mp?.bands || []).find(b => b.group === curGrp)?.ct) ?? mp?.current_ct ?? null;
  const grpColor = GRP_COLOR(curGrp);

  const actual   = Number(rt?.ok_count ?? mp?.totals?.actual ?? 0);
  const ng       = Number(rt?.ng_count || 0);
  const planNow  = mp?.totals?.plan_now ?? 0;
  const planFull = mp?.totals?.plan_full ?? 0;
  const variance = actual - planNow;
  const running  = (rt?.operating_status || "").toUpperCase() === "RUNNING";

  const changeovers = mp?.changeovers || [];
  const lastChange  = changeovers[changeovers.length - 1];
  const segments    = mp?.segments || [];

  const hourly = mp?.hourly || [];
  const barMax = useMemo(() => Math.max(1, ...hourly.map(h => Math.max(h.plan||0, h.actual||0))), [hourly]);

  const impact   = mp?.totals?.impact ?? 0;
  const baseline = mp?.totals?.baseline_single;
  const byGroup  = mp?.totals?.by_group || [];

  return (
    <div style={{ minHeight:"100vh", background:"#070c16", color:"#e6ecf5", fontFamily:"'Barlow',system-ui,sans-serif", padding:"20px 24px", boxSizing:"border-box" }}>
      {/* header */}
      <div style={{ display:"flex", alignItems:"center", gap:14, flexWrap:"wrap" }}>
        <div style={{ fontSize:26, fontWeight:800 }}>{line?.line_name || "YWD-SS"}</div>
        <span style={{ fontSize:12, fontWeight:800, padding:"4px 10px", borderRadius:99, background:"#0f2233", color:"#7dd3fc", border:"1px solid #164e63" }}>
          {shiftName ? `${shiftName} SHIFT` : "—"}
        </span>
        <span style={{ display:"inline-flex", alignItems:"center", gap:6, fontSize:12, fontWeight:800, padding:"4px 10px", borderRadius:99,
          background: running ? "rgba(34,197,94,.12)" : "rgba(148,163,184,.12)", color: running ? "#22c55e" : "#94a3b8",
          border:`1px solid ${running ? "#166534" : "#334155"}` }}>
          <span style={{ width:7, height:7, borderRadius:"50%", background: running ? "#22c55e" : "#94a3b8" }}/>
          {rt?.operating_status || "—"}
        </span>
        <div style={{ marginLeft:"auto", display:"flex", alignItems:"center", gap:14 }}>
          <div style={{ textAlign:"right" }}>
            <div style={{ fontSize:10, fontWeight:800, letterSpacing:".14em", color:"#64748b" }}>CURRENT MODEL</div>
            <div style={{ fontSize:22, fontWeight:900, color:grpColor, lineHeight:1 }}>{curName}</div>
          </div>
          <div style={{ padding:"8px 14px", borderRadius:12, background:`${grpColor}18`, border:`1px solid ${grpColor}55`, textAlign:"center" }}>
            <div style={{ fontSize:10, fontWeight:800, letterSpacing:".1em", color:"#94a3b8" }}>IDEAL CT</div>
            <div style={{ fontSize:22, fontWeight:900, color:grpColor, lineHeight:1.1 }}>{curCt ? `${curCt}s` : "—"}</div>
          </div>
          <div style={{ fontSize:26, fontWeight:800, fontVariantNumeric:"tabular-nums", color:"#cbd5e1" }}>
            {clock.toLocaleTimeString("en-GB")}
          </div>
        </div>
      </div>

      {/* per-model ideal-CT reference (always visible) */}
      {mp?.bands?.length > 0 && (
        <div style={{ display:"flex", gap:10, marginTop:12, flexWrap:"wrap", alignItems:"center" }}>
          <span style={{ fontSize:10, fontWeight:800, letterSpacing:".12em", color:"#64748b" }}>MODELS (ideal CT):</span>
          {mp.bands.map(b => {
            const active = b.group === curGrp;
            const col = GRP_COLOR(b.group);
            return (
              <div key={b.group} style={{ display:"flex", alignItems:"center", gap:7, padding:"6px 12px", borderRadius:10,
                background: active ? `${col}20` : "#0d1626", border:`1px solid ${active ? col : "#1c2942"}` }}>
                <span style={{ width:9, height:9, borderRadius:2, background:col }}/>
                <b style={{ color:col }}>{b.group}</b>
                <span style={{ color:"#e2ecff", fontFamily:"monospace", fontWeight:700 }}>{b.ct}s</span>
                <span style={{ color:"#64748b", fontSize:12 }}>· {Math.round(3600/b.ct)}/hr</span>
                {active && <span style={{ fontSize:10, fontWeight:800, color:col }}>● RUNNING</span>}
              </div>
            );
          })}
        </div>
      )}

      {!enabled && (
        <div style={{ marginTop:14, padding:"12px 16px", borderRadius:12, background:"rgba(245,158,11,.08)", border:"1px solid #7c5310", fontSize:13, color:"#fcd34d" }}>
          Model-aware plan ke liye Admin → Models me har model ka <b>ideal CT</b> set karo (kam se kam 2 model, alag CT).
        </div>
      )}

      {/* model-change banner */}
      {lastChange && (
        <div
          onMouseEnter={()=>setHover(true)} onMouseLeave={()=>setHover(false)}
          style={{ position:"relative", marginTop:14, padding:"12px 16px", borderRadius:12,
            background:"linear-gradient(90deg, rgba(56,189,248,.10), rgba(245,158,11,.10))",
            border:"1px solid #334155", display:"flex", alignItems:"center", gap:12, cursor:"help", flexWrap:"wrap" }}>
          <span style={{ fontSize:18 }}>▲</span>
          <span style={{ fontSize:15, fontWeight:800 }}>
            Model change: <span style={{ color:GRP_COLOR(lastChange.from) }}>{lastChange.from}</span> → <span style={{ color:GRP_COLOR(lastChange.to) }}>{lastChange.to}</span>
            <span style={{ color:"#94a3b8", fontWeight:600 }}> @ {lastChange.at}</span>
          </span>
          <span style={{ fontSize:13, color:"#cbd5e1" }}>— ab <b style={{ color:grpColor }}>{curGrp}</b> ke {curCt}s rate se plan chal raha</span>
          <span style={{ marginLeft:"auto", fontSize:11, color:"#64748b" }}>hover for timeline</span>
          {hover && segments.length > 0 && (
            <div style={{ position:"absolute", top:"100%", left:16, marginTop:6, zIndex:20, background:"#0b1524", border:"1px solid #1e2a44",
              borderRadius:10, padding:"10px 12px", boxShadow:"0 14px 34px rgba(0,0,0,.55)", minWidth:300 }}>
              <div style={{ fontSize:10, fontWeight:800, letterSpacing:".1em", color:"#64748b", marginBottom:6 }}>SHIFT MODEL TIMELINE</div>
              {segments.map((s, i) => (
                <div key={i} style={{ display:"flex", alignItems:"center", gap:8, fontSize:12, padding:"3px 0" }}>
                  <span style={{ width:8, height:8, borderRadius:2, background: GRP_COLOR(s.group) }}/>
                  <b style={{ minWidth:38, color:GRP_COLOR(s.group) }}>{s.group}</b>
                  <span style={{ color:"#94a3b8", fontFamily:"monospace" }}>{s.start}–{s.end}</span>
                  <span style={{ marginLeft:"auto", color:"#cbd5e1" }}>@ {s.ct || "?"}s · {s.cycles} pcs</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* metric cards */}
      <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(210px,1fr))", gap:14, marginTop:16 }}>
        <Card label="PLAN (model-aware, now)" value={planNow} sub={`current rate ${curCt?Math.round(3600/curCt):"—"}/hr @ ${curCt||"—"}s`} color="#3b82f6" />
        <Card label="ACTUAL (OK)" value={actual} sub={`NG ${ng}`} color="#22c55e" />
        <Card label="VARIANCE" value={(variance>=0?"+":"")+variance} sub={variance>=0?"ahead of plan":"behind plan"} color={variance>=0?"#22c55e":"#f59e0b"} />
        <Card label="PROJECTED (shift end)" value={planFull} sub={`model-mix projected`} color={grpColor} />
      </div>

      {/* changeover impact + hourly */}
      <div style={{ display:"grid", gridTemplateColumns:"minmax(300px,1fr) 1.4fr", gap:14, marginTop:16 }}>
        {/* impact */}
        <div style={{ background:"#0d1626", border:"1px solid #1c2942", borderRadius:14, padding:"16px 18px" }}>
          <div style={{ fontSize:11, fontWeight:800, letterSpacing:".1em", color:"#64748b", marginBottom:10 }}>CHANGEOVER IMPACT — PLAN PE FARQ</div>
          {byGroup.length > 0 ? (
            <>
              {byGroup.map((b, i) => (
                <div key={i} style={{ display:"flex", alignItems:"center", gap:8, fontSize:13, padding:"5px 0", borderBottom:"1px solid #14203400" }}>
                  <span style={{ width:9, height:9, borderRadius:2, background:GRP_COLOR(b.group) }}/>
                  <b style={{ minWidth:40, color:GRP_COLOR(b.group) }}>{b.group}</b>
                  <span style={{ color:"#94a3b8" }}>@ {b.ct}s · {b.working_min} min</span>
                  <span style={{ marginLeft:"auto", fontWeight:800, color:"#e2ecff" }}>{b.plan} pcs</span>
                </div>
              ))}
              <div style={{ marginTop:12, paddingTop:12, borderTop:"1px solid #1c2942", fontSize:12.5, color:"#cbd5e1", lineHeight:1.7 }}>
                {baseline && (
                  <div>Agar poora <b style={{color:GRP_COLOR(baseline.group)}}>{baseline.group}</b> chalta:
                    <b style={{ color:"#94a3b8" }}> {baseline.plan} pcs</b></div>
                )}
                <div>Model-mix projected: <b style={{ color:grpColor }}>{planFull} pcs</b></div>
                <div style={{ marginTop:6, fontSize:14 }}>
                  Changeover ka asar:&nbsp;
                  <b style={{ color: impact>=0 ? "#22c55e" : "#f59e0b" }}>{impact>=0?"+":""}{impact} pcs</b>
                  <span style={{ color:"#64748b" }}> {impact>=0 ? "(faster model → zyada plan)" : "(slower model → kam plan)"}</span>
                </div>
              </div>
            </>
          ) : (
            <div style={{ fontSize:13, color:"#64748b" }}>Abhi tak ek hi model chala — changeover hote hi yahan plan-farq dikhega.</div>
          )}
        </div>

        {/* hourly model-aware */}
        <div style={{ background:"#0d1626", border:"1px solid #1c2942", borderRadius:14, padding:"16px 18px" }}>
          <div style={{ fontSize:11, fontWeight:800, letterSpacing:".1em", color:"#64748b", marginBottom:10 }}>HOURLY — MODEL-AWARE PLAN vs ACTUAL</div>
          <div style={{ display:"flex", flexDirection:"column", gap:7 }}>
            {hourly.map((h, i) => (
              <div key={i} style={{ display:"flex", alignItems:"center", gap:10, opacity: h.started ? 1 : 0.5 }}>
                <span style={{ fontSize:11, fontFamily:"monospace", color:"#94a3b8", minWidth:92 }}>{h.start}–{h.end}</span>
                <span style={{ width:8, height:8, borderRadius:2, background:GRP_COLOR(h.group), flex:"0 0 auto" }} title={h.group||"—"}/>
                <div style={{ flex:1, position:"relative", height:18, background:"#0a1220", borderRadius:5, overflow:"hidden" }}>
                  <div style={{ position:"absolute", inset:0, width:`${Math.min(100,(h.plan/barMax)*100)}%`, background:"rgba(59,130,246,.35)" }}/>
                  <div style={{ position:"absolute", top:0, bottom:0, width:`${Math.min(100,(h.actual/barMax)*100)}%`, background:GRP_COLOR(h.group), opacity:.9 }}/>
                </div>
                <span style={{ fontSize:12, fontFamily:"monospace", color:"#e2ecff", minWidth:96, textAlign:"right" }}>
                  <b>{h.actual}</b><span style={{ color:"#64748b" }}> / {h.plan}</span>
                </span>
              </div>
            ))}
          </div>
          <div style={{ marginTop:10, fontSize:11, color:"#64748b", display:"flex", gap:14 }}>
            <span><span style={{ display:"inline-block", width:10, height:10, borderRadius:2, background:"rgba(59,130,246,.35)", marginRight:5 }}/>plan (model-CT)</span>
            <span><span style={{ display:"inline-block", width:10, height:10, borderRadius:2, background:"#38bdf8", marginRight:5 }}/>actual (model-colored)</span>
          </div>
        </div>
      </div>

      <div style={{ marginTop:14, fontSize:12, color:"#475569", lineHeight:1.6 }}>
        Plan model ke hisab se live — INR {mp?.bands?.find(b=>b.group==="INR")?.ct || 25.7}s, OTR {mp?.bands?.find(b=>b.group==="OTR")?.ct || 14.42}s (Admin → Models me set).
        Model timeline per-cycle CT-log se derive hoti hai — <b>reload-proof</b>, collector untouched. Sirf YWD-SS.
      </div>
    </div>
  );
}

function Card({ label, value, sub, color }) {
  return (
    <div style={{ background:"#0d1626", border:"1px solid #1c2942", borderLeft:`3px solid ${color}`, borderRadius:14, padding:"15px 18px" }}>
      <div style={{ fontSize:11, fontWeight:800, letterSpacing:".1em", color:"#64748b" }}>{label}</div>
      <div style={{ fontSize:36, fontWeight:900, color, lineHeight:1.1, marginTop:4, fontVariantNumeric:"tabular-nums" }}>{value}</div>
      <div style={{ fontSize:12, color:"#7c8aa5", marginTop:2 }}>{sub}</div>
    </div>
  );
}
