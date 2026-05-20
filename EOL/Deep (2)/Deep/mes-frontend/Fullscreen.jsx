import { useState, useEffect, useRef, useCallback } from "react";
import { useParams } from "react-router-dom";
import axios from "axios";

 
const api = axios.create({ baseURL: "" });
api.interceptors.request.use(cfg => {
  const t = localStorage.getItem("mes_token");
  if (t) cfg.headers.Authorization = `Bearer ${t}`;
  return cfg;
});
 
function useChartJS(cb, deps = []) {
  useEffect(() => {
    if (window.Chart) { cb(); return; }
    const s = document.createElement("script");
    s.src = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js";
    s.onload = () => cb();
    document.head.appendChild(s);
  }, deps);
}
 
function fmtSec(s) {
  if (!s) return "00:00:00";
  s = parseInt(s);
  return [Math.floor(s/3600), Math.floor((s%3600)/60), s%60]
    .map(v => String(v).padStart(2,"0")).join(":");
}
function toMin(t) {
  if (!t) return 0;
  const p = String(t).split(":").map(Number);
  return p[0]*60+(p[1]||0);
}
function oeeColor(v) {
  return v >= 85 ? "#22c55e" : v >= 65 ? "#f59e0b" : v > 0 ? "#ef4444" : "#3d4450";
}
 
function Gauge({ value, color, label, size = 70, textSub }) {
  const r = 26, cx = 35, cy = 35;
  const circ = 2 * Math.PI * r;
  const pct  = Math.min(100, Math.max(0, value)) / 100;
  const dash = pct * circ;
  const rot  = -90;
  return (
    <div style={{ display:"flex", flexDirection:"column", alignItems:"center", gap:2 }}>
      <svg width={size} height={size} viewBox="0 0 70 70">
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="rgba(128,128,128,0.12)" strokeWidth={6}
          strokeLinecap="round" />
        <circle cx={cx} cy={cy} r={r} fill="none" stroke={color} strokeWidth={6}
          strokeDasharray={`${dash} ${circ - dash}`}
          strokeLinecap="round" transform={`rotate(${rot} ${cx} ${cy})`}
          style={{ transition:"stroke-dasharray 0.8s cubic-bezier(.4,0,.2,1)", filter:`drop-shadow(0 0 4px ${color}88)` }} />
        <text x={cx} y={cy+1} textAnchor="middle" fill={color}
          style={{ fontSize:11, fontWeight:800, fontFamily:"monospace" }}>
          {value.toFixed(1)}%
        </text>
      </svg>
      <span style={{ fontSize:8, fontWeight:700, color: textSub || "rgba(128,128,128,0.6)", letterSpacing:".1em", textTransform:"uppercase" }}>{label}</span>
    </div>
  );
}
 
export default function Fullscreen() {
  const { lineId } = useParams();
  const [dark, setDark]      = useState(() => localStorage.getItem("fs_theme") !== "light");
  const [rt, setRt]          = useState(null);
  const [line, setLine]      = useState(null);
  const [connected, setConn] = useState(false);
  const [now, setNow]        = useState(new Date());
  const chartRef   = useRef(null);
  const chartInst  = useRef(null);
  const lineRef    = useRef(null);
  const [chartMode,  setChartMode]  = useState("ct");   // "ct"|"daily"|"weekly"|"monthly"
  const [history,    setHistory]    = useState(null);
  const [histLoading,setHistLoading]= useState(false);
  useEffect(() => {
  document.title = line ? `MES — ${line.line_name}` : "MES — Live Monitor";
  }, [line]);
 
  useEffect(() => { localStorage.setItem("fs_theme", dark?"dark":"light"); }, [dark]);
  useEffect(() => { const t = setInterval(()=>setNow(new Date()),1000); return ()=>clearInterval(t); }, []);
 
  const fetchData = useCallback(async () => {
    try {
      const [rtRes, lineRes] = await Promise.all([
        api.get(`/api/lines/${lineId}/realtime`),
        lineRef.current ? Promise.resolve({data:lineRef.current}) : api.get(`/api/lines/${lineId}`),
      ]);
      setRt(rtRes.data);
      if (!lineRef.current) { lineRef.current = lineRes.data; setLine(lineRes.data); }
      setConn(true);
    } catch { setConn(false); }
  }, [lineId]);
 
  useEffect(() => {
    fetchData();
    const t = setInterval(fetchData, 3000);
    return () => clearInterval(t);
  }, [fetchData]);

  // Fetch production history whenever mode changes to a cumulative view
  useEffect(() => {
    if (chartMode === "ct") return;
    const daysMap = { daily: 42, weekly: 91, monthly: 365 };
    setHistLoading(true);
    api.get(`/api/lines/${lineId}/production_history?days=${daysMap[chartMode]}`)
      .then(r => setHistory(r.data))
      .catch(() => setHistory([]))
      .finally(() => setHistLoading(false));
  }, [chartMode, lineId]);
 
  const D       = dark;
  const bg      = D ? "#060912" : "#e8eef5";
  const bgCard  = D ? "#0a0f1a" : "#ffffff";
  const bgDeep  = D ? "#070c14" : "#dde5f0";
  const border  = D ? "#141e2e" : "#b8c8dc";
  const text    = D ? "#e8edf5" : "#0f172a";
  const textSub = D ? "#6b7a94" : "#374151";
  const textMut = D ? "#dde4ef" : "#1e293b";
  const GREEN   = "#22c55e";
  const RED     = "#ef4444";
  const AMBER   = "#f59e0b";
  const BLUE    = "#3b82f6";
  const ORANGE  = "#f97316";
  const PURPLE  = "#a855f7";
 
  const ideal = line?.plc_config?.ideal_cycle_time || 15;
 
  const ctData = rt ? Array.from({length:20}, (_,i) => {
    const v = rt[`ct${i+1}`];
    return v && v > 0 ? v : null;
  }) : [];
 
  // Rebuild chart when mode/theme/history change
  useChartJS(() => { buildChart(chartMode, history); }, [dark, chartMode, history]);

  // In-place update for CT chart when only rt changes (no destroy/rebuild = no jitter)
  useEffect(() => {
    if (chartMode !== "ct" || !chartInst.current || !rt) return;
    const valid = ctData.filter(v => v !== null);
    const avg   = valid.length ? (valid.reduce((a,b)=>a+b,0)/valid.length).toFixed(1) : null;
    chartInst.current.data.datasets[0].data  = ctData;
    chartInst.current.data.datasets[0].label = avg ? `Cycle Time  (Avg: ${avg}s)` : "Cycle Time";
    chartInst.current.data.datasets[0].pointBackgroundColor = ctData.map(v => v===null?"transparent":v<=ideal?GREEN:RED);
    chartInst.current.update("none");
  }, [rt]); // eslint-disable-line react-hooks/exhaustive-deps

  const buildChart = (mode, hist) => {
    if (!chartRef.current || !window.Chart) return;
    if (chartInst.current) { chartInst.current.destroy(); chartInst.current = null; }
    const ctx = chartRef.current.getContext("2d");

    // ── Cycle Time chart (default) ───────────────────────────
    if (mode === "ct") {
      const valid = ctData.filter(v => v !== null);
      const avg   = valid.length ? (valid.reduce((a,b)=>a+b,0)/valid.length).toFixed(1) : null;
      chartInst.current = new window.Chart(ctx, {
        type: "line",
        data: {
          labels: Array.from({length:20},(_,i)=>i+1),
          datasets: [
            {
              label: avg ? `Cycle Time  (Avg: ${avg}s)` : "Cycle Time",
              data: ctData,
              borderWidth: 2.5, tension: 0.35, fill: false,
              pointRadius: ctData.map(v => v!==null ? 5 : 0),
              pointHoverRadius: 8,
              pointBackgroundColor: ctData.map(v => v===null?"transparent":v<=ideal?GREEN:RED),
              pointBorderColor: D?"#060912":"#ffffff",
              pointBorderWidth: 2,
              spanGaps: false,
              segment: {
                borderColor: ctx2 => {
                  const a = ctData[ctx2.p0DataIndex], b = ctData[ctx2.p1DataIndex];
                  if (a===null||b===null) return "transparent";
                  if (a<=ideal && b<=ideal) return GREEN;
                  if (a>ideal  && b>ideal)  return RED;
                  const ratio = Math.abs(a-ideal) / Math.abs(a-b);
                  const c2 = chartRef.current?.getContext("2d");
                  if (!c2) return GREEN;
                  const grad = c2.createLinearGradient(ctx2.p0.x, 0, ctx2.p1.x, 0);
                  if (a > ideal) {
                    grad.addColorStop(0,                      RED);
                    grad.addColorStop(Math.max(0,ratio-0.3),  RED);
                    grad.addColorStop(ratio,                  AMBER);
                    grad.addColorStop(Math.min(1,ratio+0.3),  GREEN);
                    grad.addColorStop(1,                      GREEN);
                  } else {
                    grad.addColorStop(0,                      GREEN);
                    grad.addColorStop(Math.max(0,ratio-0.3),  GREEN);
                    grad.addColorStop(ratio,                  AMBER);
                    grad.addColorStop(Math.min(1,ratio+0.3),  RED);
                    grad.addColorStop(1,                      RED);
                  }
                  return grad;
                },
              },
            },
            {
              label: `Threshold ${ideal}s`,
              data: Array(20).fill(ideal),
              borderColor: "rgba(251,191,36,0.85)",
              borderWidth: 1.5, borderDash: [8,5],
              pointRadius: 0, fill: false, tension: 0,
            },
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          interaction: { intersect:false, mode:"index" },
          plugins: {
            legend: { position:"top", align:"end", labels:{ color:textSub, boxWidth:14, font:{size:10}, usePointStyle:true, padding:14 } },
            tooltip: {
              backgroundColor: D?"#0d1420":"#fff",
              titleColor:text, bodyColor:textSub, borderColor:border, borderWidth:1, padding:10,
              callbacks: {
                title: i => `Cycle #${i[0].label}`,
                label: c => {
                  if (c.datasetIndex===1) return ` Threshold: ${ideal}s`;
                  const v = c.parsed.y;
                  if (v===null) return " No data";
                  return ` ${v.toFixed(2)}s  ${v>ideal?"⚠ Above":"✓ Below"} target`;
                },
              },
            },
          },
          scales: {
            y: {
              beginAtZero: false,
              suggestedMin: valid.length ? Math.max(0,Math.min(...valid)-3) : 0,
              suggestedMax: valid.length ? Math.max(...valid)+4 : ideal+10,
              grid: { color:D?"rgba(255,255,255,0.04)":"rgba(0,0,0,0.04)", drawBorder:false },
              border: { display:false },
              ticks: { color:textSub, font:{size:10}, callback:v=>`${v}s`, maxTicksLimit:7 },
            },
            x: {
              grid: { color:D?"rgba(255,255,255,0.04)":"rgba(0,0,0,0.04)", drawBorder:false },
              border: { display:false },
              ticks: { color:textSub, font:{size:10} },
            },
          },
        },
      });
      return;
    }

    // ── Cumulative production chart ───────────────────────────
    if (!hist || hist.length === 0) return;

    // Build grouped + cumulative data based on mode
    let groups = [];
    if (mode === "daily") {
      // Last 7 days, non-cumulative per day + cumulative line
      const last = hist.slice(-14);
      groups = last.map(d => {
        const dt = new Date(d.record_date);
        return {
          label: dt.toLocaleDateString("en-IN",{day:"numeric",month:"short"}),
          plan: Number(d.total_plan) || 0,
          actual: Number(d.total_actual) || 0,
        };
      });
    } else if (mode === "weekly") {
      // Group by ISO week
      const byWeek = {};
      for (const d of hist) {
        const dt = new Date(d.record_date);
        const mon = new Date(dt); mon.setDate(dt.getDate()-((dt.getDay()+6)%7));
        const key = mon.toISOString().slice(0,10);
        if (!byWeek[key]) byWeek[key] = { plan:0, actual:0, label:"W/E "+mon.toLocaleDateString("en-IN",{day:"numeric",month:"short"}) };
        byWeek[key].plan   += Number(d.total_plan)   || 0;
        byWeek[key].actual += Number(d.total_actual) || 0;
      }
      groups = Object.values(byWeek).slice(-12);
    } else if (mode === "monthly") {
      // Group by month
      const byMonth = {};
      for (const d of hist) {
        const dt  = new Date(d.record_date);
        const key = `${dt.getFullYear()}-${String(dt.getMonth()+1).padStart(2,"0")}`;
        if (!byMonth[key]) byMonth[key] = { plan:0, actual:0, label: dt.toLocaleDateString("en-IN",{month:"short",year:"2-digit"}) };
        byMonth[key].plan   += Number(d.total_plan)   || 0;
        byMonth[key].actual += Number(d.total_actual) || 0;
      }
      groups = Object.values(byMonth).slice(-12);
    }

    // Build cumulative arrays only
    const labels = [], cumulPlan = [], cumulActual = [];
    let cp = 0, ca = 0;
    for (const g of groups) {
      cp += g.plan; ca += g.actual;
      labels.push(g.label);
      cumulPlan.push(cp);
      cumulActual.push(ca);
    }

    // Color bars: green if cumulative actual >= cumulative plan, red otherwise
    const barColors = cumulActual.map((a,i) => a >= cumulPlan[i] ? `${GREEN}88` : `${RED}88`);
    const barBorder = cumulActual.map((a,i) => a >= cumulPlan[i] ? GREEN : RED);

    chartInst.current = new window.Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            type: "bar",
            label: "Cumulative Actual",
            data: cumulActual,
            backgroundColor: barColors,
            borderColor: barBorder,
            borderWidth: 1.5,
            borderRadius: 3,
            order: 2,
          },
          {
            type: "line",
            label: "Cumulative Plan",
            data: cumulPlan,
            borderColor: BLUE,
            borderWidth: 2.5,
            borderDash: [6,4],
            pointRadius: 3,
            pointBackgroundColor: BLUE,
            pointBorderColor: D?"#060912":"#fff",
            pointBorderWidth: 2,
            tension: 0.3,
            fill: false,
            order: 1,
          },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: { duration: 400 },
        interaction: { intersect:false, mode:"index" },
        plugins: {
          legend: { position:"top", align:"end", labels:{ color:textSub, boxWidth:12, font:{size:10}, usePointStyle:true, padding:12 } },
          tooltip: {
            backgroundColor: D?"#0d1420":"#fff",
            titleColor:text, bodyColor:textSub, borderColor:border, borderWidth:1, padding:10,
            callbacks: {
              label: c => ` ${c.dataset.label}: ${c.parsed.y.toLocaleString()} pcs`,
            },
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            grid: { color:D?"rgba(255,255,255,0.04)":"rgba(0,0,0,0.04)", drawBorder:false },
            border: { display:false },
            ticks: { color:textSub, font:{size:9}, callback:v=>v.toLocaleString(), maxTicksLimit:6 },
          },
          x: {
            grid: { color:D?"rgba(255,255,255,0.04)":"rgba(0,0,0,0.04)", drawBorder:false },
            border: { display:false },
            ticks: { color:textSub, font:{size:9}, maxRotation:40 },
          },
        },
      },
    });
  };
 
  const toggleFS = () => {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen();
    else document.exitFullscreen();
  };
 
  if (!rt || !line) return (
    <div style={{height:"100vh",display:"flex",alignItems:"center",justifyContent:"center",
      background:D?"#060912":"#f0f4f8",flexDirection:"column",gap:12}}>
      <div style={{width:32,height:32,borderRadius:"50%",
        border:`3px solid ${D?"#141e2e":"#e2e8f0"}`,borderTopColor:ORANGE,
        animation:"spin .7s linear infinite"}}/>
      <span style={{fontSize:11,color:"#6b7a94",letterSpacing:".1em"}}>CONNECTING…</span>
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
    </div>
  );
 
  const actual   = (rt.ok_count||0)+(rt.ng_count||0);
  const plan     = rt.shift_plan_completed||0;
  const progress = plan ? Math.min(100,(actual/plan)*100) : 0;
  const oee      = parseFloat(rt.overall_oee||0);
  const avail    = parseFloat(rt.availability||0);
  const perf     = parseFloat(rt.performance||0);
  const qual     = parseFloat(rt.quality_oee||0);
  const avgCT    = parseFloat(rt.ct_avg_20||0);
  const shift    = rt.shift_name||"A";
  const status   = rt.operating_status||"IDLE";
  const statusColor = status==="RUNNING"?GREEN:status==="BREAKDOWN"?RED:status==="BREAK"?AMBER:textMut;
 
  const LOSSES = [
    {key:"breakdown",   label:"Breakdown",  color:RED   },
    {key:"quality",     label:"Quality",    color:ORANGE},
    {key:"material",    label:"Material",   color:AMBER },
    {key:"setup",       label:"Setup",      color:BLUE  },
    {key:"change_over", label:"Change Over",color:PURPLE},
    {key:"speed",       label:"Speed Loss", color:"#06b6d4"},
    {key:"others",      label:"Others",     color:"#84cc16"},
  ];
  let totalLoss = 0;
  const lossData = LOSSES.map(c => {
    const sec = rt[`loss_${c.key}_seconds`]||0;
    totalLoss += sec;
    return {...c, sec};
  });
 
  const shiftCfg = (line.shifts||[]).find(s=>s.shift_name===shift);
  const sStart   = toMin(shiftCfg?.start_time||"08:30");
  const sEnd     = toMin(shiftCfg?.end_time||"17:15");
  const sDur     = shiftCfg?.crosses_midnight ? (sEnd+1440-sStart) : (sEnd-sStart);
  const nowMin   = now.getHours()*60+now.getMinutes();
  const sElapsed = Math.max(0, nowMin>=sStart ? nowMin-sStart : nowMin+1440-sStart);
  const tlPct    = Math.min(100,(sElapsed/sDur)*100);
 
  const allSlots = (line.hourly_slots||[])
    .filter(s=>s.shift_name===shift)
    .sort((a,b)=>a.start_time.localeCompare(b.start_time));
  const slotData = allSlots.map(s => {
    const p=s.db_column_prefix;
    const sp=rt[`${p}_plan`]||0, sa=rt[`${p}_actual`]||0;
    return {label:s.slot_label, plan:sp, actual:sa, variance:sa-sp};
  });
  const tPlan   = slotData.reduce((s,r)=>s+r.plan,0);
  const tActual = slotData.reduce((s,r)=>s+r.actual,0);
  const tVar    = tActual-tPlan;
 
  const timeStr = now.toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false});
 
  const card  = (e={}) => ({ background:bgCard, borderRadius:8, border:`1px solid ${border}`, overflow:"hidden", ...e });
  const hdr   = (e={}) => ({ padding:"4px 10px", background:bgDeep, borderBottom:`1px solid ${border}`, display:"flex", alignItems:"center", justifyContent:"space-between", ...e });
  const lbl9  = { fontSize:9, fontWeight:800, color:textSub, letterSpacing:".1em", textTransform:"uppercase" };
 
  return (
    <>
      <style>{`
        *{box-sizing:border-box;margin:0;padding:0;}
        body{overflow:hidden;background:${bg};}
        ::-webkit-scrollbar{width:3px;height:3px;}
        ::-webkit-scrollbar-thumb{background:${D?"#1e293b":"#cbd5e1"};border-radius:2px;}
        @keyframes spin{to{transform:rotate(360deg)}}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
        @keyframes blink{0%,100%{opacity:1}50%{opacity:.1}}
      `}</style>
 
      <div style={{
        height:"100vh",
        display:"grid",
        gridTemplateRows:"44px 110px minmax(0,1fr) minmax(0,1.4fr)",
        gap:3, padding:4,
        background:bg, color:text,
        fontFamily:"'Segoe UI',system-ui,sans-serif",
        overflow:"hidden",
      }}>
 
        {/* ── HEADER ── */}
        <div style={{...card(),display:"flex",alignItems:"center",
          justifyContent:"space-between",padding:"0 12px"}}>
          <div style={{display:"flex",alignItems:"center",gap:10}}>
            <img src="/logo.jpg" alt="logo" style={{width:26,height:26,borderRadius:6,objectFit:"contain"}}/>
            <div>
              <div style={{fontSize:14,fontWeight:800,color:text,lineHeight:1}}>{line.line_name}</div>
              <div style={{fontSize:9,color:textMut,marginTop:1}}>
                {rt.record_date||"—"} · {shiftCfg?.start_time?.slice(0,5)} – {shiftCfg?.end_time?.slice(0,5)}
              </div>
            </div>
            <div style={{width:1,height:20,background:border}}/>
            <span style={{padding:"2px 8px",borderRadius:99,fontSize:9,fontWeight:700,
              color:GREEN,background:`${GREEN}18`,border:`1px solid ${GREEN}33`}}>
              {shift} SHIFT
            </span>
            <span style={{padding:"2px 10px",borderRadius:99,fontSize:10,fontWeight:800,
              color:statusColor,background:`${statusColor}18`,border:`1px solid ${statusColor}33`,
              letterSpacing:".06em",
              animation:status==="RUNNING"?"pulse 2s infinite":status==="BREAKDOWN"?"blink 1s infinite":"none"}}>
              {status}
            </span>
            {rt.current_model_name && (
              <span style={{fontSize:10,color:textSub}}>
                Model: <strong style={{color:text}}>{rt.current_model_name}</strong>
              </span>
            )}
          </div>
          <div style={{display:"flex",alignItems:"center",gap:8}}>
            <div style={{display:"flex",alignItems:"center",gap:5,padding:"2px 8px",
              borderRadius:99,background:connected?`${GREEN}10`:`${RED}10`,
              border:`1px solid ${connected?GREEN:RED}33`}}>
              <div style={{width:5,height:5,borderRadius:"50%",background:connected?GREEN:RED,animation:"pulse 2s infinite"}}/>
              <span style={{fontSize:9,fontWeight:700,color:connected?GREEN:RED}}>{connected?"Live":"Offline"}</span>
            </div>
            <span style={{fontFamily:"monospace",fontSize:13,fontWeight:700,color:textSub,letterSpacing:".04em"}}>{timeStr}</span>
            <button onClick={()=>setDark(d=>!d)} style={{padding:"3px 10px",borderRadius:6,
              border:`1px solid ${border}`,background:bgDeep,color:textSub,cursor:"pointer",fontSize:10,fontWeight:600}}>
              {D?"☀ Light":"🌙 Dark"}
            </button>
            <button onClick={toggleFS} style={{padding:"3px 8px",borderRadius:6,
              border:`1px solid ${border}`,background:bgDeep,color:textSub,cursor:"pointer",fontSize:13}}>⛶</button>
          </div>
        </div>
 
        {/* ── KPI STRIP ── */}
        <div style={{display:"grid",gridTemplateColumns:"180px 1fr 1fr 1fr 158px 148px",gap:3}}>
          {/* Big OEE */}
          <div style={{...card(),display:"flex",alignItems:"center",gap:10,padding:"8px 14px"}}>
            <Gauge value={oee} color={oeeColor(oee)} label="Overall OEE" size={72} textSub={textSub}/>
            <div>
              <div style={{fontSize:9,fontWeight:700,color:textMut,letterSpacing:".1em",textTransform:"uppercase",marginBottom:2}}>Overall OEE</div>
              <div style={{fontSize:30,fontWeight:900,color:oeeColor(oee),lineHeight:1}}>{oee.toFixed(1)}<span style={{fontSize:14}}>%</span></div>
              <span style={{padding:"1px 7px",borderRadius:99,fontSize:9,fontWeight:800,
                color:oeeColor(oee),background:`${oeeColor(oee)}18`,border:`1px solid ${oeeColor(oee)}33`,
                marginTop:4,display:"inline-block"}}>
                {rt.oee_grade||"—"}
              </span>
            </div>
          </div>
          {/* APQ */}
          {[
            {label:"Availability", val:avail, color:GREEN },
            {label:"Performance",  val:perf,  color:BLUE  },
            {label:"Quality",      val:qual,  color:PURPLE},
          ].map(g=>(
            <div key={g.label} style={{...card(),display:"flex",flexDirection:"column",
              alignItems:"center",justifyContent:"center",padding:"6px 10px"}}>
              <Gauge value={g.val} color={oeeColor(g.val)} label={g.label} size={68} textSub={textSub}/>
            </div>
          ))}
          {/* Plan vs Actual */}
          <div style={{...card()}}>
            <div style={hdr()}><span style={lbl9}>Production</span><span style={{fontSize:9,color:textMut}}>Shift {shift}</span></div>
            <div style={{padding:"6px 12px",display:"flex",flexDirection:"column",gap:5}}>
              {[{l:"Plan",v:plan,c:BLUE},{l:"Actual",v:actual,c:GREEN}].map(r=>(
                <div key={r.l} style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                  <span style={{fontSize:10,color:textSub}}>{r.l}</span>
                  <span style={{fontFamily:"monospace",fontSize:18,fontWeight:900,color:r.c}}>{r.v.toLocaleString()}</span>
                </div>
              ))}
              <div>
                <div style={{display:"flex",justifyContent:"space-between",fontSize:9,color:textMut,marginBottom:3}}>
                  <span>Progress</span>
                  <span style={{fontWeight:700,color:oeeColor(progress)}}>{progress.toFixed(1)}%</span>
                </div>
                <div style={{background:D?"#141e2e":"#e2e8f0",borderRadius:99,height:5,overflow:"hidden"}}>
                  <div style={{width:`${progress}%`,height:"100%",borderRadius:99,
                    background:`linear-gradient(90deg,${oeeColor(progress)}80,${oeeColor(progress)})`,
                    transition:"width .8s ease"}}/>
                </div>
              </div>
            </div>
          </div>
          {/* CT + Status */}
          <div style={{...card()}}>
            <div style={hdr()}><span style={lbl9}>Cycle Time</span></div>
            <div style={{padding:"6px 12px",display:"flex",flexDirection:"column",gap:4}}>
              <div style={{display:"flex",justifyContent:"space-between"}}>
                <span style={{fontSize:9,color:textMut}}>Ideal</span>
                <span style={{fontFamily:"monospace",fontSize:13,fontWeight:700,color:textSub}}>{ideal.toFixed(1)}s</span>
              </div>
              <div style={{display:"flex",justifyContent:"space-between"}}>
                <span style={{fontSize:9,color:textMut}}>Avg (20)</span>
                <span style={{fontFamily:"monospace",fontSize:13,fontWeight:800,color:avgCT>ideal?RED:GREEN}}>{avgCT.toFixed(2)}s</span>
              </div>
              <div style={{marginTop:2,borderRadius:6,padding:"5px 0",
                background:`${statusColor}14`,border:`1.5px solid ${statusColor}40`,
                display:"flex",alignItems:"center",justifyContent:"center",
                animation:status==="RUNNING"?"pulse 2s infinite":"none"}}>
                <span style={{fontSize:13,fontWeight:900,color:statusColor,letterSpacing:".1em"}}>{status}</span>
              </div>
            </div>
          </div>
        </div>
 
        {/* ── CHART + LOSS ── */}
        <div style={{display:"grid",gridTemplateColumns:"1fr 210px",gap:3,minHeight:0}}>
          {/* Chart */}
          <div style={{...card(),display:"flex",flexDirection:"column",minHeight:0}}>
            <div style={hdr()}>
              <div style={{display:"flex",alignItems:"center",gap:8}}>
                {/* Chart mode toggle */}
                {[
                  {id:"ct",     label:"Cycle Time"},
                  {id:"daily",  label:"Daily"},
                  {id:"weekly", label:"Weekly"},
                  {id:"monthly",label:"Monthly"},
                ].map(m=>(
                  <button key={m.id} onClick={()=>setChartMode(m.id)}
                    style={{padding:"2px 9px",borderRadius:99,border:`1px solid ${chartMode===m.id?BLUE:border}`,
                      background:chartMode===m.id?`${BLUE}22`:bgDeep,
                      color:chartMode===m.id?BLUE:textSub,
                      fontSize:9,fontWeight:700,cursor:"pointer",letterSpacing:".06em",textTransform:"uppercase"}}>
                    {m.label}
                  </button>
                ))}
                {chartMode==="ct" && (
                  <span style={{padding:"1px 7px",borderRadius:99,fontSize:9,fontWeight:600,
                    background:D?"#141e2e":"#f1f5f9",color:textSub,border:`1px solid ${border}`}}>
                    Threshold: {ideal}s
                  </span>
                )}
              </div>
              <div style={{display:"flex",alignItems:"center",gap:8,fontSize:9,color:textMut}}>
                {chartMode==="ct"
                  ? [{c:GREEN,l:"≤ Target"},{c:RED,l:"> Target"},{c:"rgba(251,191,36,0.9)",l:"Threshold"}].map(({c,l})=>(
                      <span key={l} style={{display:"flex",alignItems:"center",gap:4}}>
                        <span style={{width:14,height:3,background:c,display:"inline-block",borderRadius:2}}/>{l}
                      </span>
                    ))
                  : [{c:BLUE,l:"Cumul. Plan"},{c:GREEN,l:"Actual ≥ Plan"},{c:RED,l:"Actual < Plan"}].map(({c,l})=>(
                      <span key={l} style={{display:"flex",alignItems:"center",gap:4}}>
                        <span style={{width:10,height:10,background:c,display:"inline-block",borderRadius:2}}/>{l}
                      </span>
                    ))
                }
                {histLoading && <span style={{fontSize:9,color:AMBER,animation:"pulse 1s infinite"}}>Loading…</span>}
              </div>
            </div>
            <div style={{flex:1,position:"relative",minHeight:0,padding:"4px 8px 6px"}}>
              <canvas ref={chartRef} style={{position:"absolute",top:4,left:8,
                width:"calc(100% - 16px)",height:"calc(100% - 10px)"}}/>
            </div>
          </div>
          {/* Loss */}
          <div style={{...card(),display:"flex",flexDirection:"column",minHeight:0}}>
            <div style={hdr()}>
              <span style={lbl9}>Loss Breakdown</span>
              <span style={{fontFamily:"monospace",fontSize:10,fontWeight:700,color:totalLoss>0?RED:textMut}}>
                {fmtSec(totalLoss)}
              </span>
            </div>
            <div style={{flex:1,overflowY:"auto",padding:"3px 0"}}>
              {lossData.map(r=>{
                const pct = totalLoss>0?(r.sec/totalLoss*100):0;
                return (
                  <div key={r.key} style={{padding:"4px 10px"}}>
                    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:3}}>
                      <div style={{display:"flex",alignItems:"center",gap:5}}>
                        <div style={{width:7,height:7,borderRadius:2,background:r.color,flexShrink:0}}/>
                        <span style={{fontSize:10,color:r.sec>0?textSub:textMut,fontWeight:r.sec>0?600:400}}>{r.label}</span>
                      </div>
                      <span style={{fontFamily:"monospace",fontSize:9,fontWeight:700,color:r.sec>0?r.color:textMut}}>
                        {fmtSec(r.sec)}
                      </span>
                    </div>
                    <div style={{background:D?"#141e2e":"#e2e8f0",borderRadius:99,height:3,overflow:"hidden"}}>
                      <div style={{width:`${pct}%`,height:"100%",
                        background:`linear-gradient(90deg,${r.color}80,${r.color})`,
                        borderRadius:99,transition:"width .6s",
                        boxShadow:D?`0 0 6px ${r.color}60`:"none"}}/>
                    </div>
                  </div>
                );
              })}
            </div>
            <div style={{borderTop:`1px solid ${ORANGE}33`,padding:"5px 10px",
              background:D?"#0f0c06":"#fffbf0",
              display:"flex",justifyContent:"space-between",alignItems:"center"}}>
              <span style={{fontSize:10,fontWeight:800,color:ORANGE}}>TOTAL LOSS</span>
              <span style={{fontFamily:"monospace",fontSize:11,fontWeight:800,color:totalLoss>0?RED:textMut}}>
                {fmtSec(totalLoss)}
              </span>
            </div>
          </div>
        </div>
 
        {/* ── TIMELINE + HOURLY TABLE ── */}
        <div style={{display:"grid",gridTemplateColumns:"300px 1fr",gap:3}}>
          {/* Timeline */}
          <div style={{...card(),padding:"6px 10px",display:"flex",flexDirection:"column",gap:4}}>
            <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
              <div style={{display:"flex",alignItems:"center",gap:8}}>
                <span style={{fontSize:10,fontWeight:800,color:GREEN}}>{shift} SHIFT</span>
                <span style={{fontSize:8,color:textMut}}>{shiftCfg?.start_time?.slice(0,5)} – {shiftCfg?.end_time?.slice(0,5)}</span>
              </div>
              <span style={{fontSize:11,fontWeight:800,color:oeeColor(progress)}}>
                {progress.toFixed(1)}% <span style={{fontSize:8,color:textMut,fontWeight:400}}>done</span>
              </span>
            </div>
            <div style={{position:"relative",height:16,background:D?"#141e2e":"#e2e8f0",borderRadius:5,overflow:"hidden"}}>
              {allSlots.map((s,i)=>{
                const ss   = toMin(s.start_time);
                const se   = toMin(s.end_time);
                const sdur = se > ss ? se - ss : se + 1440 - ss;
                const left = ((ss - sStart + (ss < sStart ? 1440 : 0)) / sDur) * 100;
                const wPct = (sdur / sDur) * 100;

                // Determine if past, current, or future slot relative to now
                // Normalise all times relative to shift start
                const nowRel = nowMin >= sStart ? nowMin - sStart : nowMin + 1440 - sStart;
                const ssRel  = ss    >= sStart ? ss    - sStart : ss    + 1440 - sStart;
                const seRel  = ssRel + sdur;
                const isFuture  = nowRel < ssRel;
                const isCurrent = nowRel >= ssRel && nowRel < seRel;
                // isPast = else

                if (isFuture) {
                  // Unstarted slot — dim
                  return <div key={i} style={{position:"absolute",left:`${left}%`,width:`${wPct-.3}%`,
                    height:"100%",background:D?"rgba(30,41,59,0.35)":"rgba(226,232,240,0.5)",
                    borderRight:`1px solid ${border}`}}/>;
                }

                if (isCurrent) {
                  // Active slot — split into green (produced) / loss (idle/breakdown) / future (remaining)
                  const elapsedMin    = Math.min(nowRel - ssRel, sdur);
                  const actualPieces  = slotData[i]?.actual || 0;
                  const greenMin      = Math.min((actualPieces * ideal) / 60, elapsedMin);
                  const lossMin       = Math.max(0, elapsedMin - greenMin);
                  const futureMin     = Math.max(0, sdur - elapsedMin);
                  const gP  = (greenMin  / sdur) * 100;
                  const lP  = (lossMin   / sdur) * 100;
                  const fP  = (futureMin / sdur) * 100;
                  const lossClr = status === "RUNNING"       ? AMBER
                                : status === "BREAKDOWN"     ? RED
                                : status === "QUALITY_ISSUE" ? ORANGE
                                : status === "MATERIAL_WAIT" ? AMBER
                                : RED;
                  return (
                    <div key={i} style={{position:"absolute",left:`${left}%`,width:`${wPct-.3}%`,
                      height:"100%",display:"flex",borderRight:`1px solid ${border}`}}>
                      <div style={{width:`${gP}%`,height:"100%",background:`${GREEN}88`,flexShrink:0}}/>
                      {lP > 0 && <div style={{width:`${lP}%`,height:"100%",background:`${lossClr}88`,flexShrink:0}}/>}
                      {fP > 0 && <div style={{width:`${fP}%`,height:"100%",background:D?"rgba(30,41,59,0.3)":"rgba(226,232,240,0.4)",flexShrink:0}}/>}
                    </div>
                  );
                }

                // Past completed slot — efficiency colour
                const eff = slotData[i]?.plan > 0 ? slotData[i].actual / slotData[i].plan : 0;
                const sc  = eff >= 0.9 ? GREEN : eff >= 0.6 ? AMBER : eff > 0 ? RED : (D?"#1e293b":"#e2e8f0");
                return <div key={i} style={{position:"absolute",left:`${left}%`,width:`${wPct-.3}%`,
                  height:"100%",background:`${sc}55`,borderRight:`1px solid ${border}`}}/>;
              })}
              <div style={{position:"absolute",left:`${tlPct}%`,top:0,bottom:0,width:2,background:text,zIndex:3}}>
                <div style={{position:"absolute",top:-1,left:"50%",transform:"translateX(-50%)",
                  width:6,height:6,borderRadius:"50%",background:text,border:`2px solid ${bg}`}}/>
              </div>
            </div>
            <div style={{position:"relative",height:10}}>
              {(()=>{
                const ticks=[];
                for(let i=0;i<=sDur;i+=60){
                  const m=(sStart+i)%1440,h=Math.floor(m/60),mn=m%60;
                  ticks.push({pct:(i/sDur)*100,label:`${String(h).padStart(2,"0")}:${String(mn).padStart(2,"0")}`});
                }
                return ticks.map((t,i)=>(
                  <span key={i} style={{position:"absolute",left:`${t.pct}%`,transform:"translateX(-50%)",
                    fontSize:7,color:textMut,whiteSpace:"nowrap"}}>{t.label}</span>
                ));
              })()}
            </div>
            <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
              {[{l:"RUN",c:GREEN},{l:"BRKD",c:RED},{l:"QUAL",c:ORANGE},
                {l:"MAT",c:AMBER},{l:"SETUP",c:BLUE},{l:"OTHER",c:PURPLE}].map(({l,c})=>(
                <div key={l} style={{display:"flex",alignItems:"center",gap:3}}>
                  <div style={{width:7,height:7,background:c,borderRadius:2}}/>
                  <span style={{fontSize:7,color:textMut}}>{l}</span>
                </div>
              ))}
            </div>
          </div>
 
          {/* Hourly Table */}
          <div style={{...card(),overflow:"hidden"}}>
            <div style={{overflowX:"auto",height:"100%"}}>
              <table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
                <thead>
                  <tr style={{background:bgDeep}}>
                    <th style={{padding:"5px 10px",textAlign:"left",fontWeight:800,fontSize:9,
                      color:textSub,borderRight:`1px solid ${border}`,whiteSpace:"nowrap",
                      letterSpacing:".08em",width:60}}>METRIC</th>
                    {slotData.map(s=>(
                      <th key={s.label} style={{padding:"5px 6px",textAlign:"center",fontWeight:700,
                        fontSize:9,color:text,borderRight:`1px solid ${border}`,whiteSpace:"nowrap"}}>
                        {s.label}
                      </th>
                    ))}
                    <th style={{padding:"5px 8px",textAlign:"center",fontWeight:800,
                      fontSize:9,color:ORANGE,whiteSpace:"nowrap",minWidth:52}}>TOTAL</th>
                  </tr>
                </thead>
                <tbody>
                  <tr style={{borderTop:`1px solid ${border}`}}>
                    <td style={{padding:"5px 10px",fontWeight:700,fontSize:9,color:textMut,borderRight:`1px solid ${border}`}}>PLAN</td>
                    {slotData.map((s,i)=>(
                      <td key={i} style={{padding:"5px 6px",textAlign:"center",fontFamily:"monospace",
                        fontSize:13,fontWeight:700,color:s.plan>0?BLUE:textMut,borderRight:`1px solid ${border}`}}>
                        {s.plan}
                      </td>
                    ))}
                    <td style={{padding:"5px 8px",textAlign:"center",fontFamily:"monospace",fontSize:13,fontWeight:800,color:ORANGE}}>{tPlan}</td>
                  </tr>
                  <tr style={{borderTop:`1px solid ${border}`}}>
                    <td style={{padding:"5px 10px",fontWeight:700,fontSize:9,color:textMut,borderRight:`1px solid ${border}`}}>ACTUAL</td>
                    {slotData.map((s,i)=>(
                      <td key={i} style={{padding:"3px 6px",textAlign:"center",borderRight:`1px solid ${border}`}}>
                        <div style={{fontFamily:"monospace",fontSize:13,fontWeight:700,color:s.actual>0?GREEN:textMut}}>{s.actual}</div>
                        <div style={{fontFamily:"monospace",fontSize:8,
                          color:s.variance>0?GREEN:s.variance<0?RED:textMut,fontWeight:600}}>
                          ({s.variance>0?"+":""}{s.variance})
                        </div>
                      </td>
                    ))}
                    <td style={{padding:"3px 8px",textAlign:"center"}}>
                      <div style={{fontFamily:"monospace",fontSize:13,fontWeight:800,color:tActual>0?GREEN:textMut}}>{tActual}</div>
                      <div style={{fontFamily:"monospace",fontSize:8,
                        color:tVar>0?GREEN:tVar<0?RED:textMut,fontWeight:600}}>
                        ({tVar>0?"+":""}{tVar})
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
 
      </div>
    </>
  );
}