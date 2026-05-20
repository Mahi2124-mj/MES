/* ───────────────────────────────────────────────────────────────────
 * ProcessGraphs.jsx
 * ───────────────────────────────────────────────────────────────────
 * Per-machine process monitoring screen.
 *
 * Each machine has N processes configured under
 *   Admin → Production → Machines → ④ Process Config
 * (process name + target value + actual-value PLC register).
 *
 * This page renders ONE bar-graph card per process:
 *
 *   ┌─────────────────────────────────────────┐
 *   │  Pressing                       62 / 80 │  ← title + actual / target
 *   │ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │  ← target line (dashed red)
 *   │  ▌ ▌ ▌    ▌ ▌    ▌ ▌ ▌                 │  ← hourly actual bars
 *   │  10 11 12 1  2  3  4  5  6              │
 *   └─────────────────────────────────────────┘
 *
 * Layout:
 *   Top bar  → page title + machine selector dropdown
 *   Body     → grid of cards (one per process)
 *
 * Data sources:
 *   GET /api/zones/                        → zone list (for line filter)
 *   GET /api/zones/{zid}/lines             → lines per zone
 *   GET /api/lines/{lid}/machines          → machines on a line
 *   GET /api/machines/{mid}/processes      → process config (+ latest value)
 *   GET /api/machines/{mid}/processes/log  → hourly buckets for graphs
 *
 * Note: the bar series populates as the collector starts logging.
 * Until then, only the latest single value (from the config endpoint)
 * shows up — no historical bars.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";

const API = "";
const api = {
  async get(path, token) {
    const r = await fetch(API + path, { headers: { Authorization: `Bearer ${token}` } });
    if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
    return r.json();
  },
};

function fmtTime(ts) {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleTimeString([], { hour:"2-digit", minute:"2-digit" });
  } catch { return "—"; }
}

// ════════════════════════════════════════════════════════════════════
// Bar-graph card — one per process
// ════════════════════════════════════════════════════════════════════
// Pure SVG, no chart-library dependency.  Two render paths:
//   BIT processes  → one spike per ON pulse, positioned on a time axis,
//                    height = ON duration (seconds). Same idea as the
//                    main cycle-time graph: each event = one bar, taller
//                    bars = longer ON time, target line crosses where
//                    "max acceptable duration" sits.
//   WORD processes → existing per-minute aggregated bars (legacy).
// Aspect ratio: 16:9 to match the main cycle-time chart.
function ProcessCard({ proc, samples, pulses, windowHours, theme }) {
  const target = Number(proc.target || 0);
  const isBit  = (proc.register_type || "").toLowerCase() === "bit";

  // BIT processes use the per-pulse path; everything else uses the
  // legacy per-minute samples path.
  const usePulses = isBit && pulses && pulses.length > 0;

  // ──────────────────────────────────────────────────────────────────
  // Legacy WORD path — per-minute aggregated bars (unchanged)
  // ──────────────────────────────────────────────────────────────────
  const hasHistory = samples && samples.length > 0;
  const bars = hasHistory
    ? samples.map(s => ({ label: fmtTime(s.bucket), value: Number(s.actual ?? 0) }))
    : (proc.latest_value !== undefined && proc.latest_value !== null && !usePulses
        ? [{ label: "now", value: Number(proc.latest_value) }]
        : []);

  // Y-axis max:
  //   BIT: max(longest pulse, target) × 1.15
  //   WORD: max(bar value, target) × 1.15
  const pulseDurMax = usePulses
    ? Math.max(...pulses.map(p => p.duration_s || 0))
    : 0;
  const dataMax = usePulses
    ? pulseDurMax
    : (bars.length ? Math.max(...bars.map(b => b.value)) : 0);
  const yMax    = Math.max(target, dataMax) * 1.15 || 10;

  // 16:9 viewBox (was 360 × 160 = 2.25:1).
  // 400 × 225 ≈ 16:9 with breathing room for axis padding.
  const W = 400, H = 225;
  const PAD_L = 38, PAD_R = 14, PAD_T = 18, PAD_B = 30;
  const plotW = W - PAD_L - PAD_R;
  const plotH = H - PAD_T - PAD_B;

  // WORD-path bar geometry (unchanged behaviour)
  const slotW  = bars.length ? plotW / bars.length : 0;
  const barW   = bars.length ? Math.max(1.5, slotW - 1) : 0;
  const yScale = (v) => PAD_T + plotH - (v / yMax) * plotH;

  // BIT-path time-axis geometry.  Each pulse spans real time on the
  // x-axis so wider spike == longer ON.  Window = last N hours.
  const windowMs = (windowHours || 8) * 3600 * 1000;
  const windowEnd   = Date.now();
  const windowStart = windowEnd - windowMs;
  const xForTs = (ts) => {
    const t = (typeof ts === "string" ? new Date(ts).getTime() : ts);
    const frac = Math.max(0, Math.min(1, (t - windowStart) / windowMs));
    return PAD_L + frac * plotW;
  };
  const widthForDurMs = (durMs) => {
    const w = (durMs / windowMs) * plotW;
    return Math.max(1.5, w);          // floor so short pulses stay visible
  };

  // Hour-tick labels for the time x-axis (BIT path).  Aim for ~8 ticks
  // — windows of 4/8 h get an hourly label, 24h gets every 3h, 72h
  // gets every 9h.
  const hourTicks = (() => {
    if (!usePulses) return [];
    const totalH = windowHours || 8;
    const stepH  = Math.max(1, Math.ceil(totalH / 8));
    const out = [];
    const endDate = new Date(windowEnd);
    // Anchor to the start of the most recent hour so labels align nicely
    endDate.setMinutes(0, 0, 0);
    for (let h = totalH; h >= 0; h -= stepH) {
      const t = endDate.getTime() - h * 3600 * 1000;
      if (t < windowStart) continue;
      out.push({ t, label: fmtTime(t) });
    }
    return out;
  })();

  // Latest actual + OK badge.
  //   BIT: "latest" = the most recent pulse's duration in seconds.
  //   WORD: existing logic.
  const latest = usePulses
    ? pulses[pulses.length - 1].duration_s
    : (hasHistory ? bars[bars.length - 1].value : (proc.latest_value ?? null));
  // For BIT-duration semantics, target is "max acceptable duration".
  // OK when latest ≤ target (the opposite of count-style WORD path).
  const ok = target > 0
    ? (usePulses ? (latest != null && latest <= target)
                  : (latest != null && latest >= target))
    : null;

  return (
    <div style={{
      background:"#fff",
      border: `2px solid ${ok === true ? "rgba(22,163,74,.30)"
                          : ok === false ? "rgba(220,38,38,.30)"
                          :                  "#e2e8f0"}`,
      borderRadius:14, padding:"14px 16px",
      boxShadow:"0 1px 3px rgba(0,0,0,.04)",
    }}>
      {/* Title + numbers */}
      <div style={{ display:"flex", alignItems:"baseline",
                    justifyContent:"space-between", marginBottom:6, gap:8 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize:9, color:"#94a3b8", fontWeight:700,
                         letterSpacing:".08em", textTransform:"uppercase" }}>
            Process #{proc.process_no}
          </div>
          <div style={{ fontSize:15, fontWeight:800, color:"#0f172a",
                         fontFamily:"'Barlow Condensed',sans-serif",
                         lineHeight:1.1, whiteSpace:"nowrap",
                         overflow:"hidden", textOverflow:"ellipsis" }}>
            {proc.process_name || `Process ${proc.process_no}`}
          </div>
        </div>
        <div style={{ textAlign:"right", flexShrink:0 }}>
          <div style={{ fontSize:22, fontWeight:800,
                         fontFamily:"'Barlow Condensed',sans-serif",
                         color: ok === true ? "#16a34a"
                              : ok === false ? "#dc2626"
                              :                  "#0f172a",
                         lineHeight:1 }}>
            {latest != null ? latest : "—"}
          </div>
          <div style={{ fontSize:10, color:"#94a3b8", marginTop:2 }}>
            target {target}
          </div>
        </div>
      </div>

      {/* SVG plot — 16:9 aspect (preserveAspectRatio respects it) */}
      <svg viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="xMidYMid meet"
            style={{ width:"100%", height:"auto", display:"block",
                      aspectRatio:"16 / 9" }}>
        {/* Y-axis grid lines (4 levels) */}
        {[0, 0.25, 0.5, 0.75, 1].map((f,i) => {
          const y = PAD_T + plotH - f * plotH;
          return (
            <g key={i}>
              <line x1={PAD_L} y1={y} x2={W - PAD_R} y2={y}
                     stroke="#f1f5f9" strokeWidth="1"/>
              <text x={PAD_L - 6} y={y + 3} textAnchor="end"
                     fontSize="9" fill="#94a3b8"
                     fontFamily="'Barlow Condensed',sans-serif">
                {usePulses
                  ? `${(yMax * f).toFixed(yMax < 5 ? 1 : 0)}${f === 1 ? "s" : ""}`
                  : Math.round(yMax * f)}
              </text>
            </g>
          );
        })}

        {/* ─── BIT path — one spike per ON pulse ─── */}
        {usePulses && pulses.map((p, i) => {
          const x = xForTs(p.started_at);
          const w = widthForDurMs(p.duration_ms);
          const y = yScale(p.duration_s);
          const h = Math.max(1, PAD_T + plotH - y);
          const overTarget = target > 0 && p.duration_s > target;
          return (
            <rect key={i}
                   x={x} y={y} width={w} height={h}
                   rx={w > 4 ? 1.5 : 0}
                   ry={w > 4 ? 1.5 : 0}
                   fill={overTarget ? "#dc2626" : "#16a34a"}
                   opacity={i === pulses.length - 1 ? 1 : 0.85}>
              <title>{`${fmtTime(p.started_at)} · ${p.duration_s}s`}</title>
            </rect>
          );
        })}

        {/* Time-axis labels for BIT path */}
        {usePulses && hourTicks.map((tk, i) => (
          <text key={`tt-${i}`}
                 x={xForTs(tk.t)} y={H - PAD_B + 14}
                 textAnchor="middle" fontSize="9" fill="#64748b"
                 fontFamily="'Barlow Condensed',sans-serif">
            {tk.label}
          </text>
        ))}

        {/* ─── WORD path — legacy per-minute aggregated bars ─── */}
        {!usePulses && (() => {
          // Aim for ≤ 8 visible labels regardless of bar count
          const labelEvery = Math.max(1, Math.ceil(bars.length / 8));
          return bars.map((b, i) => {
            const x = PAD_L + i * slotW + 0.5;
            const y = yScale(b.value);
            const h = Math.max(1.5, PAD_T + plotH - y);
            const isOk = target > 0 ? b.value >= target : true;
            const showLabel = bars.length > 0 &&
                              (i === bars.length - 1 || i % labelEvery === 0);
            return (
              <g key={i}>
                <rect x={x} y={y} width={barW} height={h}
                       rx={barW > 4 ? 2 : 0}
                       ry={barW > 4 ? 2 : 0}
                       fill={isOk ? "#16a34a" : "#dc2626"}
                       opacity={i === bars.length - 1 ? 1 : 0.7}/>
                {showLabel && (
                  <text x={x + barW/2} y={H - PAD_B + 14}
                         textAnchor="middle" fontSize="9" fill="#64748b"
                         fontFamily="'Barlow Condensed',sans-serif">
                    {b.label}
                  </text>
                )}
              </g>
            );
          });
        })()}

        {/* Target line — dashed red, full width.  Drawn AFTER bars so
            it sits on top.  For BIT, the line represents "max acceptable
            ON duration" — any spike above is the red one. */}
        {target > 0 && (
          <g>
            <line x1={PAD_L} y1={yScale(target)}
                   x2={W - PAD_R} y2={yScale(target)}
                   stroke="#dc2626" strokeWidth="2"
                   strokeDasharray="6 4"/>
            <rect x={W - PAD_R - 38} y={yScale(target) - 16}
                   width="36" height="14" rx="3"
                   fill="#dc2626"/>
            <text x={W - PAD_R - 20} y={yScale(target) - 5}
                   textAnchor="middle" fontSize="9" fill="#fff"
                   fontWeight="800"
                   fontFamily="'Barlow',sans-serif">
              TARGET
            </text>
          </g>
        )}

        {/* Empty-state hint */}
        {!usePulses && bars.length === 0 && (
          <text x={W/2} y={H/2} textAnchor="middle"
                 fontSize="11" fill="#94a3b8" fontStyle="italic">
            No data yet — collector will populate this graph.
          </text>
        )}
        {usePulses && pulses.length === 0 && (
          <text x={W/2} y={H/2} textAnchor="middle"
                 fontSize="11" fill="#94a3b8" fontStyle="italic">
            No pulses logged in this window.
          </text>
        )}
      </svg>

      {/* Footer line */}
      <div style={{ marginTop:6, fontSize:10, color:"#94a3b8",
                      display:"flex", justifyContent:"space-between" }}>
        <span>Reg: <code style={{ background:"#f1f5f9", padding:"1px 6px",
                                   borderRadius:4, fontFamily:"monospace" }}>
          {proc.actual_register || "—"}
        </code></span>
        {proc.latest_at && <span>last: {fmtTime(proc.latest_at)}</span>}
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// Main component
// ════════════════════════════════════════════════════════════════════
export default function ProcessGraphs() {
  const { token, theme, isAdmin, user } = useAuth();

  // Cascade selectors
  const [zones,    setZones]    = useState([]);
  const [lines,    setLines]    = useState([]);
  const [machines, setMachines] = useState([]);
  const [selZone,  setSelZone]  = useState("");
  const [selLine,  setSelLine]  = useState("");
  const [selMach,  setSelMach]  = useState("");

  // Data for selected machine
  const [procs,    setProcs]    = useState([]);    // [{process_no, process_name, target_value, actual_register, latest_value, latest_at}]
  const [logs,     setLogs]     = useState([]);    // [{process_id, process_name, target, samples:[{bucket, actual}]}]
  const [hours,    setHours]    = useState(8);
  const [loading,  setLoading]  = useState(false);
  // Layout: "stack" = one card per row (16:9 full width, ECG-strip style)
  //         "split" = two cards per row (16:9 each, comparison view)
  const [layout,   setLayout]   = useState(() => {
    try { return localStorage.getItem("pg_layout") || "stack"; }
    catch { return "stack"; }
  });
  useEffect(() => {
    try { localStorage.setItem("pg_layout", layout); } catch {}
  }, [layout]);

  useEffect(() => {
    document.title = "Process Graphs";
  }, []);

  // Load zones once
  useEffect(() => {
    if (!token) return;
    api.get("/api/zones/", token)
       .then(r => setZones(Array.isArray(r) ? r : []))
       .catch(() => setZones([]));
  }, [token]);

  // Lines for selected zone
  useEffect(() => {
    if (!selZone) { setLines([]); setSelLine(""); return; }
    api.get(`/api/zones/${selZone}/lines`, token)
       .then(r => setLines(Array.isArray(r) ? r : []))
       .catch(() => setLines([]));
    setSelLine(""); setSelMach("");
  }, [selZone, token]);

  // Machines for selected line
  useEffect(() => {
    if (!selLine) { setMachines([]); setSelMach(""); return; }
    api.get(`/api/lines/${selLine}/machines`, token)
       .then(r => setMachines(Array.isArray(r) ? r : []))
       .catch(() => setMachines([]));
    setSelMach("");
  }, [selLine, token]);

  // Auto-select if zones/lines/machines collapse to one each — nicer UX
  // for plants that only have a single zone.
  useEffect(() => { if (zones.length    === 1 && !selZone) setSelZone(String(zones[0].id)); },    [zones,    selZone]);
  useEffect(() => { if (lines.length    === 1 && !selLine) setSelLine(String(lines[0].id)); },    [lines,    selLine]);
  useEffect(() => { if (machines.length === 1 && !selMach) setSelMach(String(machines[0].id)); }, [machines, selMach]);

  // Refresh process config + log on machine change & every 15s.
  // `silent=true` skips the loading flicker so background polls don't
  // make the bar charts disappear and reappear every 15 sec.
  const refresh = useCallback(async (silent = false) => {
    if (!selMach || !token) { setProcs([]); setLogs([]); return; }
    if (!silent) setLoading(true);
    try {
      const [p, l] = await Promise.all([
        api.get(`/api/machines/${selMach}/processes`, token).catch(() => []),
        api.get(`/api/machines/${selMach}/processes/log?hours=${hours}`, token).catch(() => []),
      ]);
      setProcs(Array.isArray(p) ? p : []);
      setLogs (Array.isArray(l) ? l : []);
    } finally { if (!silent) setLoading(false); }
  }, [selMach, hours, token]);

  useEffect(() => {
    refresh(false);                                       // first paint
    const t = setInterval(() => refresh(true), 15000);    // silent refresh
    return () => clearInterval(t);
  }, [refresh]);

  // Pair each proc with its log series by process_id (fall back to
  // process_no for backwards compat).
  const cards = useMemo(() => procs.map(p => {
    const ls = logs.find(x => x.process_id === p.id) ||
               logs.find(x => x.process_no === p.process_no) ||
               { samples: [], pulses: [] };
    return {
      proc: {
        process_no:      p.process_no,
        process_name:    p.process_name,
        target:          p.target_value,
        actual_register: p.actual_register,
        register_type:   p.register_type || ls.register_type,
        latest_value:    p.latest_value,
        latest_at:       p.latest_at,
      },
      samples: ls.samples || [],
      pulses:  ls.pulses  || [],
    };
  }), [procs, logs]);

  const machineLabel = (() => {
    const m = machines.find(x => String(x.id) === String(selMach));
    return m ? (m.machine_name || m.plc_ip) : "";
  })();

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@600;700;800&display=swap');
        .pg-root { min-height:100vh; background:#f8fafc; font-family:'Barlow',sans-serif; padding-bottom:48px; }
        .pg-topbar {
          background:#fff; border-bottom:1px solid #e2e8f0;
          padding:0 40px 0 88px; height:60px;
          display:flex; align-items:center; justify-content:space-between;
          position:sticky; top:0; z-index:50; box-shadow:0 1px 3px rgba(0,0,0,.06);
        }
        .pg-topbar::after { content:''; position:absolute; bottom:0; left:0; right:0;
                            height:2px; background:${theme.gradient}; }
        .pg-title { position:absolute; left:50%; transform:translateX(-50%);
                    font-family:'Barlow Condensed',sans-serif; font-size:30px;
                    font-weight:800; color:#0f172a; letter-spacing:-.01em;
                    pointer-events:none; white-space:nowrap; }
        .pg-title span { color:${theme.accent}; }
        .pg-pill { display:flex; align-items:center; gap:10px;
                    padding:6px 14px; border-radius:99px;
                    border:1.5px solid #e2e8f0; background:#f8fafc;
                    font-size:12px; font-weight:600; color:#334155; white-space:nowrap; }
        .pg-pill b { color:#0f172a; font-weight:800; }
        .pg-body { padding:20px 32px 0; max-width:1500px; margin:0 auto; }
        .pg-filters { background:#fff; border:1px solid #e2e8f0; border-radius:12px;
                       padding:14px 18px; margin-bottom:18px;
                       display:flex; gap:14px; flex-wrap:wrap; align-items:flex-end;
                       box-shadow:0 1px 3px rgba(0,0,0,.04); }
        .pg-fld { display:flex; flex-direction:column; gap:5px; }
        .pg-fld label { font-size:10px; font-weight:700; color:#64748b;
                          letter-spacing:.08em; text-transform:uppercase; }
        .pg-fld select, .pg-fld input { padding:8px 11px; border-radius:8px;
                          border:1.5px solid #e2e8f0; font-size:13px;
                          font-family:inherit; background:#fff; outline:none;
                          min-width:160px; }
        .pg-grid { display:grid; gap:14px; }
        .pg-grid.stack { grid-template-columns: minmax(0, 1fr); }
        .pg-grid.split { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        @media (max-width: 900px) {
          .pg-grid.split { grid-template-columns: minmax(0, 1fr); }
        }
        .pg-layout-toggle { display:inline-flex; border:1.5px solid #e2e8f0;
                              border-radius:8px; overflow:hidden; background:#fff; }
        .pg-layout-toggle button { padding:7px 12px; font-size:11px;
                              font-weight:700; border:none; background:#fff;
                              color:#64748b; cursor:pointer; font-family:inherit;
                              display:flex; align-items:center; gap:6px; }
        .pg-layout-toggle button.active { background:${theme.accentDark};
                                            color:#fff; }
        .pg-layout-toggle button + button { border-left:1px solid #e2e8f0; }
        .pg-empty { padding:60px 20px; text-align:center;
                     color:#94a3b8; font-style:italic; font-size:13px;
                     background:#fff; border:1px solid #e2e8f0; border-radius:12px; }
      `}</style>

      <div className="pg-root">
        <div className="pg-topbar">
          <div />
          <div className="pg-title">
            Process <span>Graphs</span>
          </div>
          {user?.username && (
            <div className="pg-pill">Signed in as <b>{user.username}</b></div>
          )}
        </div>

        <div className="pg-body">
          {/* Cascade selectors — Zone → Line → Machine */}
          <div className="pg-filters">
            <div className="pg-fld">
              <label>Zone</label>
              <select value={selZone} onChange={e => setSelZone(e.target.value)}>
                <option value="">— Select Zone —</option>
                {zones.map(z => (
                  <option key={z.id} value={z.id}>{z.zone_name}</option>
                ))}
              </select>
            </div>
            <div className="pg-fld">
              <label>Line</label>
              <select value={selLine} onChange={e => setSelLine(e.target.value)}
                      disabled={!selZone}>
                <option value="">— Select Line —</option>
                {lines.map(l => (
                  <option key={l.id} value={l.id}>{l.line_name || l.line_code}</option>
                ))}
              </select>
            </div>
            <div className="pg-fld">
              <label>Machine</label>
              <select value={selMach} onChange={e => setSelMach(e.target.value)}
                      disabled={!selLine}>
                <option value="">— Select Machine —</option>
                {machines.map(m => (
                  <option key={m.id} value={m.id}>{m.machine_name || m.plc_ip}</option>
                ))}
              </select>
            </div>
            <div className="pg-fld">
              <label>Window</label>
              <select value={hours} onChange={e => setHours(parseInt(e.target.value) || 8)}>
                <option value="4">Last 4 h</option>
                <option value="8">Last 8 h (Shift)</option>
                <option value="24">Last 24 h</option>
                <option value="72">Last 3 days</option>
              </select>
            </div>
            <div className="pg-fld">
              <label>Layout</label>
              <div className="pg-layout-toggle">
                <button className={layout === "stack" ? "active" : ""}
                        onClick={() => setLayout("stack")}
                        title="Stacked — one process per row, full-width 16:9 (ECG strip)">
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                    <rect x="1.5" y="2"  width="13" height="3.5" rx="0.8"
                           fill="currentColor"/>
                    <rect x="1.5" y="6.5" width="13" height="3.5" rx="0.8"
                           fill="currentColor"/>
                    <rect x="1.5" y="11" width="13" height="3.5" rx="0.8"
                           fill="currentColor"/>
                  </svg>
                  Stack
                </button>
                <button className={layout === "split" ? "active" : ""}
                        onClick={() => setLayout("split")}
                        title="Split — two processes per row, 16:9 each (comparison)">
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                    <rect x="1.5" y="2.5" width="6"  height="11" rx="0.8"
                           fill="currentColor"/>
                    <rect x="8.5" y="2.5" width="6"  height="11" rx="0.8"
                           fill="currentColor"/>
                  </svg>
                  Split
                </button>
              </div>
            </div>
            {selMach && (
              <div style={{ marginLeft:"auto", fontSize:11, color:"#64748b" }}>
                <b style={{ color:"#0f172a" }}>{machineLabel}</b>
                {" · "}{procs.length} process{procs.length===1?"":"es"} configured
              </div>
            )}
          </div>

          {/* Body */}
          {!selMach ? (
            <div className="pg-empty">
              Pick a Zone → Line → Machine to see its process graphs.
              <br/>Configure processes under <b>Admin → Production → Machines → ④ Process Config</b>.
            </div>
          ) : loading && cards.length === 0 ? (
            <div className="pg-empty">Loading…</div>
          ) : cards.length === 0 ? (
            <div className="pg-empty">
              No processes configured for this machine.
              <br/>Add some under <b>Admin → Production → Machines → ④ Process Config</b>.
            </div>
          ) : (
            <div className={`pg-grid ${layout}`}>
              {cards.map((c, i) => (
                <ProcessCard key={i}
                              proc={c.proc}
                              samples={c.samples}
                              pulses={c.pulses}
                              windowHours={hours}
                              theme={theme}/>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
