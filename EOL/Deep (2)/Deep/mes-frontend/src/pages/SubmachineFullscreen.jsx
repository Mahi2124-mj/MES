/**
 * SubmachineFullscreen.jsx
 * ────────────────────────
 * Minimal fullscreen for a single sub-machine, themed to match the main
 * Fullscreen.jsx (same bg / card / text / status colours, same hourly
 * table layout).  Only three panels:
 *   • Header (logo, machine name, shift, status, clock, theme toggle)
 *   • Cycle-time line chart (click a point → on-demand cycle video)
 *   • Hourly target vs actual table (target = THIS sub-machine's
 *     own ideal_ct applied to MAIN line's slot boundaries — so per-
 *     machine plan auto-scales with admin's ideal_cycle_time config)
 *
 * Shared state (model, shift, OT) comes from the parent line's main
 * Fullscreen, so we don't duplicate OEE / poka / plan logic here.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  LineChart, Line, ReferenceLine, LabelList,
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer,
} from "recharts";
import { api } from "../api/client";

// ─── Shared theme/colour constants (match Fullscreen.jsx) ────────
const STATUS_CLR = {
  IDLE:            "#94a3b8",
  RUNNING:         "#22c55e",
  BREAKDOWN:       "#ef4444",
  QUALITY_ISSUE:   "#f97316",
  SETUP:           "#3b82f6",
  MATERIAL_WAIT:   "#eab308",
  OTHER_LOSS:      "#a855f7",
  CHANGE_OVER:     "#06b6d4",
  SPEED:           "#22c55e",
  BREAK:           "#7dd3fc",
};

export default function SubmachineFullscreen() {
  const { subId } = useParams();
  const navigate  = useNavigate();
  const token     = sessionStorage.getItem("mes_token") || "";

  // Theme follows the same persistence key as Fullscreen.jsx
  const [dark, setDark] = useState(() => localStorage.getItem("fs_theme") !== "light");
  useEffect(() => { localStorage.setItem("fs_theme", dark ? "dark" : "light"); }, [dark]);

  const D       = dark;
  const bg      = D ? "#060912" : "#e8eef5";
  const bgCard  = D ? "#0a0f1a" : "#ffffff";
  const bgDeep  = D ? "#070c14" : "#dde5f0";
  const border  = D ? "#141e2e" : "#b8c8dc";
  const text    = D ? "#e8edf5" : "#0f172a";
  const textSub = D ? "#8092af" : "#374151";
  const textMut = D ? "#dde4ef" : "#1e293b";

  // Data state
  const [ctRows,   setCtRows]   = useState([]);
  const [hourly,   setHourly]   = useState({ buckets: [], ideal_ct: 15, total_target: 0, total_actual: 0, shift_plan: 0 });
  const [meta,     setMeta]     = useState(null);   // { machine_name, plc_ip, count_bit, ideal_ct, line_name }
  const [parentRt, setParentRt] = useState(null);   // parent line realtime for shift/status header
  // 2026-05-16 — Machine toggle (same pattern as Fullscreen.jsx header).
  // Operator wants to hop between every machine on this line WITHOUT
  // going back to Dashboard.  Dropdown lists all machines (Main + Subs)
  // and navigates to each one's dedicated fullscreen page.
  const [allMachines,    setAllMachines]    = useState([]);
  const [machineMenuOpen,setMachineMenuOpen]= useState(false);
  const [now,      setNow]      = useState(new Date());
  const [err,      setErr]      = useState("");
  // Slider: which 30-cycle window is visible. null = auto-track latest.
  const [viewStart, setViewStart] = useState(null);
  // Picked point — shows a persistent floating info box + Play button.
  // Click on a point sets this; another click or Play button opens video.
  const [picked,    setPicked]    = useState(null); // {cycle_seq,y,ts_end,model_name}
  // Video load error message inside the floating box ("video not available" etc)
  const [vidErr,    setVidErr]    = useState("");

  const timerRef    = useRef(null);
  const clockRef    = useRef(null);
  const parentRtRef = useRef(null);
  useEffect(() => { parentRtRef.current = parentRt; }, [parentRt]);

  // ── Semi-Auto Part-History panel state ──────────────────────────
  // Opens on the "Part History" button in the header.  Operator can
  // search by partial part_code, narrow to a date / shift / explicit
  // time range, and download CSV of the filtered set.  Only relevant
  // when this sub-machine has SA data capture configured — but the
  // panel itself works whenever the endpoint returns rows.
  const [phOpen,      setPhOpen]      = useState(false);
  const [phRows,      setPhRows]      = useState([]);
  const [phRegNames,  setPhRegNames]  = useState([]);
  const [phLoading,   setPhLoading]   = useState(false);
  const [phErr,       setPhErr]       = useState("");
  const [phPartCode,  setPhPartCode]  = useState("");
  const [phFrom,      setPhFrom]      = useState("");    // datetime-local
  const [phTo,        setPhTo]        = useState("");
  const [phLimit,     setPhLimit]     = useState(500);

  const loadPartHistory = async () => {
    setPhLoading(true); setPhErr("");
    try {
      const qs = new URLSearchParams();
      if (phFrom)     qs.set("from", new Date(phFrom).toISOString());
      if (phTo)       qs.set("to",   new Date(phTo).toISOString());
      if (phPartCode) qs.set("part_code", phPartCode);
      qs.set("limit", String(phLimit || 500));
      const r = await api.get(`/api/submachines/${subId}/data-log?${qs.toString()}`, token);
      setPhRows(Array.isArray(r?.rows) ? r.rows : []);
      setPhRegNames(Array.isArray(r?.register_names) ? r.register_names : []);
    } catch (e) {
      setPhErr(e.message || "Failed to load history");
      setPhRows([]); setPhRegNames([]);
    } finally { setPhLoading(false); }
  };

  const downloadPartHistoryCsv = () => {
    const qs = new URLSearchParams();
    if (phFrom)     qs.set("from", new Date(phFrom).toISOString());
    if (phTo)       qs.set("to",   new Date(phTo).toISOString());
    if (phPartCode) qs.set("part_code", phPartCode);
    qs.set("format", "csv");
    qs.set("limit", String(Math.max(phLimit || 500, 5000)));
    // Use fetch with auth header → blob → trigger download (can't set
    // headers on <a download>).
    fetch(`/api/submachines/${subId}/data-log?${qs.toString()}`,
          { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.ok ? r.blob() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(blob => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `semi_auto_${subId}.csv`;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
      })
      .catch(e => setPhErr(`CSV download failed: ${e.message}`));
  };

  // Native browser fullscreen toggle (same UX as main Fullscreen.jsx)
  const [isFS, setIsFS] = useState(!!document.fullscreenElement);
  useEffect(() => {
    const on = () => setIsFS(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);
  const toggleFS = () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else document.documentElement.requestFullscreen().catch(() => {});
  };

  // ── Data fetchers ───────────────────────────────────────────
  const fetchCt = async (shift) => {
    try {
      // Filter by current parent shift so cycle # always restarts at 1
      // when a new shift begins — matches the main-PLC dashboard.
      //
      // 2026-05-13 — operator wants the FULL shift's cycles on the
      // graph, not just the last 200.  A typical shift runs ~600-1000
      // cycles; 5000 is a safe cap that comfortably handles fast
      // sub-machines (e.g. presses) without ever truncating the data.
      // The backend already supports the `limit` query (default 500),
      // and Postgres can serve 5k rows in <50 ms via the existing
      // (sub_plc_id, record_date) index.
      const qs = shift ? `&shift=${encodeURIComponent(shift)}` : "";
      const rows = await api.get(
        `/api/submachines/${subId}/ct-history?limit=5000${qs}`, token);
      setCtRows(Array.isArray(rows) ? rows : []);
      setErr("");
    } catch (e) {
      setErr(e.message || "Failed to load CT history");
    }
  };

  const fetchHourly = async (shift) => {
    try {
      const qs = shift ? `?shift=${encodeURIComponent(shift)}` : "";
      const h = await api.get(`/api/submachines/${subId}/hourly${qs}`, token);
      setHourly(h || { buckets: [], ideal_ct: 15, total_target: 0, total_actual: 0, shift_plan: 0 });
    } catch { /* non-fatal */ }
  };

  // Resolve metadata + parent-line realtime
  const fetchMeta = async () => {
    try {
      const lines = await api.get("/api/lines/", token);
      for (const ln of (lines || [])) {
        const subs = await api.get(`/api/lines/${ln.id}/submachines`, token).catch(() => []);
        const m = (subs || []).find(s => String(s.id) === String(subId));
        if (m) {
          setMeta({ ...m, line_id: ln.id, line_name: ln.line_name });
          return;
        }
      }
    } catch { /* ignore */ }
  };

  const fetchParentRt = async (lineId) => {
    if (!lineId) return;
    try {
      const r = await api.get(`/api/lines/${lineId}/realtime`, token);
      setParentRt(r);
    } catch { /* ignore */ }
  };

  // Pull every machine on this line (Main + Subs) for the header
  // toggle dropdown.  Runs once meta.line_id is known; refreshes every
  // 60 s so admin changes (machine renames, machine_seq edits) show up.
  const fetchAllMachines = async (lineId) => {
    if (!lineId) return;
    try {
      const r = await api.get(`/api/lines/${lineId}/machines`, token);
      setAllMachines(Array.isArray(r) ? r : []);
    } catch { /* keep last known */ }
  };

  // ── Lifecycle ───────────────────────────────────────────────
  useEffect(() => {
    document.title = "loading…";
    fetchMeta();
    fetchCt();
    fetchHourly();
    // 5 s instead of 3 s — cycles are usually 10–20 s apart, so 5 s is
    // fast enough to feel live but ~40 % less backend load.
    timerRef.current = setInterval(() => {
      const sh = parentRtRef.current?.shift_name;
      fetchCt(sh);
      fetchHourly(sh);
      const lineId = parentRtRef.current?.line_id || meta?.line_id;
      if (lineId) fetchParentRt(lineId);
    }, 5000);
    clockRef.current = setInterval(() => setNow(new Date()), 1000);
    return () => {
      clearInterval(timerRef.current);
      clearInterval(clockRef.current);
    };
  }, [subId]);   // eslint-disable-line

  // When the parent shift transitions, immediately re-fetch the graph
  // (and clear the picked point) so the user sees cycle #1 of the new
  // shift instantly instead of waiting up to 5 s for the next poll.
  useEffect(() => {
    if (parentRt?.shift_name) {
      setCtRows([]);
      setPicked(null);
      setViewStart(null);
      fetchCt(parentRt.shift_name);
    }
  }, [parentRt?.shift_name]);   // eslint-disable-line

  // Once meta resolves → first parent-rt fetch + machines list + page title.
  // machines list refreshes every 60 s for header dropdown freshness.
  useEffect(() => {
    if (meta?.line_id) {
      fetchParentRt(meta.line_id);
      fetchAllMachines(meta.line_id);
    }
    if (meta?.machine_name) {
      document.title = `${meta.machine_name}`;
    }
  }, [meta]);   // eslint-disable-line

  // Periodic refresh for the header machine dropdown (admin renames /
  // adds machines without page reload).
  useEffect(() => {
    if (!meta?.line_id) return;
    const t = setInterval(() => fetchAllMachines(meta.line_id), 60_000);
    return () => clearInterval(t);
  }, [meta?.line_id]);   // eslint-disable-line

  // When parent shift changes, re-fetch hourly buckets so the table
  // follows the main PLC (including into OT).
  useEffect(() => {
    if (parentRt?.shift_name) fetchHourly(parentRt.shift_name);
  }, [parentRt?.shift_name]);   // eslint-disable-line

  // No hardcoded fallbacks — everything comes from the PLC collector's
  // realtime data / DB config. Empty string = "we don't know yet, don't paint".
  const ideal  = Number(meta?.ideal_ct ?? hourly.ideal_ct ?? 0);
  const shift  = parentRt?.shift_name || "";
  const status = parentRt?.operating_status || "";
  const statusColor = status
    ? (STATUS_CLR[status.replace(" ", "_").toUpperCase()] || STATUS_CLR.IDLE)
    : STATUS_CLR.IDLE;

  // Shift-filtered buckets (main table)
  const shiftBuckets = useMemo(() => {
    if (!shift) return hourly.buckets || [];
    return (hourly.buckets || []).filter(b => b.shift_name === shift);
  }, [hourly, shift]);

  const shiftTotals = useMemo(() => {
    const t = shiftBuckets.reduce(
      (a, b) => ({ target: a.target + (b.target || 0), actual: a.actual + (b.actual || 0) }),
      { target: 0, actual: 0 },
    );
    return { target: t.target, actual: t.actual, variance: t.actual - t.target };
  }, [shiftBuckets]);

  // Full chart dataset (all cycles)
  const chartDataAll = useMemo(
    () => ctRows.map(r => ({
      x: r.cycle_seq,
      y: Number(r.ct_seconds) || 0,
      ts_start: r.ts_start,
      ts_end:   r.ts_end,
      model_name: r.model_name,
      cycle_seq:  r.cycle_seq,
    })),
    [ctRows],
  );

  // Window — 30 cycles visible at a time. Auto-tracks latest if viewStart null.
  const WINDOW = 30;
  const maxStart   = Math.max(0, chartDataAll.length - WINDOW);
  const windowStart = viewStart !== null ? Math.min(viewStart, maxStart) : maxStart;
  const chartData = chartDataAll.slice(windowStart, windowStart + WINDOW);

  const todayCount = shiftBuckets.reduce((a, b) => a + (b.actual || 0), 0);

  // ── Styles reused like Fullscreen ────────────────────────────
  const card = (extra = {}) => ({
    background: bgCard, border: `1px solid ${border}`, borderRadius: 8,
    ...extra,
  });

  return (
    <div style={{
      // 2026-05-13 — operator reported the page felt "edge-to-edge".
      // Outer padding bumped from 12 → 24 horizontal so cards have
      // breathing room from the screen edge.  Top/bottom stays
      // 16 so the chart fits in a 1080p TV without scroll.
      minHeight: "100vh", background: bg,
      color: text, padding: "16px 24px", boxSizing: "border-box",
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif',
    }}>
      {/* ── HEADER (matches Fullscreen.jsx style) ───────────────
          overflow:"visible" + zIndex override so the machine-toggle
          dropdown isn't clipped by the card()'s default overflow:hidden. */}
      <div style={{
        ...card({ padding: "0 16px", marginBottom: 10 }),
        height: 60,
        display: "flex", alignItems: "center", justifyContent: "space-between",
        overflow: "visible", position: "relative", zIndex: 200,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <img src="/logo.jpg" alt="logo"
               style={{ width: 48, height: 48, borderRadius: 10, objectFit: "contain" }} />
          <div>
            <div style={{ fontSize: 16, fontWeight: 900, color: text, lineHeight: 1.1 }}>
              {meta?.machine_name || "Loading…"}
            </div>
            {/* Line / Zone breadcrumb — tells the floor operator at a
                glance WHICH line this sub-machine belongs to.  Without
                it the page just shows the machine name and you'd have
                to remember which line you opened. */}
            {meta?.line_name && (
              <div style={{ fontSize: 11, color: textMut, marginTop: 3,
                            letterSpacing: ".04em" }}>
                {meta.zone_name ? `${meta.zone_name} · ` : ""}{meta.line_name}
                {meta.line_code && meta.line_code !== meta.line_name && (
                  <span style={{ marginLeft: 4, opacity: 0.7 }}>({meta.line_code})</span>
                )}
              </div>
            )}
          </div>
          <div style={{ width: 1, height: 22, background: border, margin: "0 4px" }} />

          {/* MACHINE TOGGLE PILL (M-N badge + dropdown).
              2026-05-16 — Operator complaint: "bhai yhn se alg prr nhi
              submachien vaale se bhi toh jaana chaiye sabhi machines ke
              dashboard mein hona chaiye naa".  This pill is now
              clickable; opens a dropdown with every machine on the line
              (Main + all Subs).  Click a sibling → navigate straight to
              its dedicated fullscreen page — no Dashboard round-trip.
              Same UX pattern as the pill on Fullscreen.jsx. */}
          <div style={{ position: "relative" }}>
            <button
              onClick={() => setMachineMenuOpen(o => !o)}
              title="Switch between machines on this line"
              style={{
                padding: "3px 10px", borderRadius: 99,
                fontSize: 11, fontWeight: 800,
                color: "#3b82f6", background: "rgba(59,130,246,.15)",
                border: "1px solid rgba(59,130,246,.4)",
                cursor: "pointer", letterSpacing: ".05em",
                display: "inline-flex", alignItems: "center", gap: 6,
              }}>
              {meta?.machine_seq != null
                ? `M-${meta.machine_seq}`
                : (meta?.machine_name || "MACHINE")}
              <span style={{ fontSize: 9, opacity: .6 }}>▾</span>
            </button>
            {machineMenuOpen && allMachines.length > 0 && (
              <div onClick={() => setMachineMenuOpen(false)}
                   style={{
                     position: "absolute", top: "100%", left: 0, marginTop: 4,
                     background: bgCard,
                     border: `1px solid ${border}`,
                     borderRadius: 8,
                     minWidth: 260, maxHeight: 320, overflowY: "auto",
                     boxShadow: "0 12px 32px rgba(0,0,0,.55)",
                     zIndex: 9999, padding: 4,
                   }}>
                {/* 2026-05-16 — Number-first layout (operator spec).
                    Leading bold number badge instead of MAIN/SUB tag.
                    Fall back to position index when machine_seq isn't
                    set so every row gets a sensible badge. */}
                {allMachines.map((m, idx) => {
                  const isMain    = !m.parent_plc_id;
                  const isCurrent = !isMain && String(m.id) === String(subId);
                  const seqNum    = m.machine_seq != null
                                    ? m.machine_seq
                                    : (idx + 1);
                  return (
                    <div key={m.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        setMachineMenuOpen(false);
                        if (isCurrent) return;
                        if (isMain) {
                          navigate(`/fullscreen/${meta?.line_id}`);
                        } else {
                          navigate(`/submachine-fullscreen/${m.id}`);
                        }
                      }}
                      style={{
                        padding: "8px 10px", borderRadius: 5,
                        fontSize: 12,
                        color: isCurrent ? "#3b82f6" : text,
                        background: isCurrent ? "rgba(59,130,246,.15)" : "transparent",
                        cursor: isCurrent ? "default" : "pointer",
                        display: "flex", alignItems: "center", justifyContent: "space-between",
                        gap: 8,
                      }}
                      onMouseEnter={e => { if (!isCurrent) e.currentTarget.style.background = "rgba(59,130,246,.10)"; }}
                      onMouseLeave={e => { if (!isCurrent) e.currentTarget.style.background = "transparent"; }}>
                      <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        {/* Big leading number — 1, 2, 3 ... */}
                        <span style={{
                          minWidth: 24, height: 24,
                          display: "inline-flex", alignItems: "center", justifyContent: "center",
                          fontSize: 13, fontWeight: 900, fontFamily: "monospace",
                          color: isCurrent ? "#3b82f6" : text,
                          background: isCurrent ? "rgba(59,130,246,.28)" : border,
                          borderRadius: 6,
                          letterSpacing: 0,
                        }}>
                          {seqNum}
                        </span>
                        <strong>{m.machine_name || m.plc_ip}</strong>
                      </span>
                      {isCurrent && (
                        <span style={{ fontSize: 9, fontWeight: 800, color: "#3b82f6" }}>
                          ● HERE
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* BOTTLENECK badge — admin toggle in machine config.  Red
              pulsing pill so floor team can spot the constraining
              station at a glance.  No effect on collector / counting. */}
          {meta?.is_bottleneck && (
            <span style={{
              padding: "3px 12px", borderRadius: 99, fontSize: 11, fontWeight: 900,
              color: "#fff", background: "#dc2626",
              border: "1px solid #b91c1c",
              boxShadow: "0 0 0 3px rgba(220,38,38,.18)",
              letterSpacing: ".08em",
              animation: "pulse 2s infinite",
            }}>🚧 BOTTLENECK</span>
          )}

          {/* Shift pill — live from main PLC's realtime data */}
          {shift && (
            <span style={{
              padding: "3px 10px", borderRadius: 99, fontSize: 11, fontWeight: 800,
              color: STATUS_CLR.RUNNING, background: `${STATUS_CLR.RUNNING}18`,
              border: `1px solid ${STATUS_CLR.RUNNING}33`,
            }}>{shift} SHIFT</span>
          )}

          {/* Status pill hidden — only the live shift matters on this page.
              Sub-machine inherits status from main PLC; showing it here
              just added clutter. */}

          {/* OT badge mirrors main PLC's OT state */}
          {parentRt?.ot_active_shift && (
            <span style={{
              padding: "3px 10px", borderRadius: 99, fontSize: 11, fontWeight: 800,
              color: "#16a34a", background: "rgba(22,163,74,0.15)",
              border: "1px solid rgba(22,163,74,0.35)",
              animation: "pulse 2s infinite",
            }}>⏱ OT ACTIVE</span>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {/* Part History only relevant when Semi-Auto data capture is
              enabled on this sub-machine — that's the source of the
              parameter rows.  For plain video-clip sub-machines the
              button would open an empty modal, so hide it entirely. */}
          {meta?.sa_enabled && (
            <button onClick={() => { setPhOpen(true); loadPartHistory(); }}
              title="Search Semi-Auto part history with all captured parameters"
              style={{
                padding: "4px 12px", borderRadius: 6,
                border: `1px solid ${border}`,
                background: D ? "rgba(59,130,246,0.18)" : "rgba(59,130,246,0.12)",
                color: "#3b82f6", cursor: "pointer",
                fontSize: 11, fontWeight: 800, letterSpacing: ".04em",
              }}>📊 PART HISTORY</button>
          )}
          <span style={{ fontFamily: "monospace", fontSize: 16, fontWeight: 800,
                          color: textSub, letterSpacing: ".04em" }}>
            {now.toLocaleTimeString()}
          </span>
          <button onClick={() => setDark(d => !d)}
            style={{
              padding: "4px 10px", borderRadius: 6,
              border: `1px solid ${border}`, background: bgDeep,
              color: textSub, cursor: "pointer", fontSize: 11, fontWeight: 700,
            }}>{dark ? "☀ LIGHT" : "🌙 DARK"}</button>
          <button
            onClick={toggleFS}
            title={isFS ? "Exit fullscreen" : "Enter fullscreen"}
            style={{
              padding: "3px 8px", borderRadius: 6,
              border: `1px solid ${border}`, background: bgDeep,
              color: textSub, cursor: "pointer",
              display: "inline-flex", alignItems: "center", justifyContent: "center",
            }}>
            {isFS ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 3v4a2 2 0 0 1-2 2H3"/>
                <path d="M15 3v4a2 2 0 0 0 2 2h4"/>
                <path d="M9 21v-4a2 2 0 0 0-2-2H3"/>
                <path d="M15 21v-4a2 2 0 0 1 2-2h4"/>
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 9V5a2 2 0 0 1 2-2h4"/>
                <path d="M21 9V5a2 2 0 0 0-2-2h-4"/>
                <path d="M3 15v4a2 2 0 0 0 2 2h4"/>
                <path d="M21 15v4a2 2 0 0 1-2 2h-4"/>
              </svg>
            )}
          </button>
        </div>
      </div>

      {/* Fullscreen-style animations + kill the stock white page margins
          that were leaking around the edges of the card in dark mode */}
      <style>{`
        html, body, #root { margin: 0; padding: 0; background: ${bg}; }
        @keyframes pulse {0%,100%{opacity:1}50%{opacity:.35}}
        @keyframes blink {0%,100%{opacity:1}50%{opacity:.15}}
      `}</style>

      {/* Error banner */}
      {err && (
        <div style={{
          padding: 10, background: STATUS_CLR.BREAKDOWN + "18",
          border: `1px solid ${STATUS_CLR.BREAKDOWN}55`,
          borderRadius: 6, color: STATUS_CLR.BREAKDOWN,
          marginBottom: 10, fontSize: 12,
        }}>{err}</div>
      )}

      {/* ── CYCLE-TIME CHART ────────────────────────────────── */}
      <div style={{
        ...card({ padding: 12, marginBottom: 10 }),
        height: 360,
      }}>
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          marginBottom: 8,
        }}>
          <div style={{ fontSize: 11, fontWeight: 900, color: textSub, letterSpacing: ".1em", textTransform: "uppercase" }}>
            Cycle time
          </div>
          <div style={{ fontSize: 11, color: textMut, fontFamily: "monospace" }}>
            {ctRows.length ? `${ctRows.length} cycles · last #${ctRows[ctRows.length-1]?.cycle_seq}` : ""}
          </div>
        </div>
        {chartData.length === 0 ? (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center",
                        height: "calc(100% - 24px)", color: textMut, fontSize: 13 }}>
            Waiting for cycle data…
          </div>
        ) : (
          <div style={{ height: "calc(100% - 60px)", position: "relative" }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}
                margin={{ top: 10, right: 20, bottom: 10, left: 0 }}
                // Click anywhere on chart → recharts gives nearest point
                // in activePayload. Both LineChart.onClick AND Line.onClick
                // wired so click registers reliably whether the user hits
                // the line, an active dot, or the chart area.
                onClick={(e) => {
                  const p = e?.activePayload?.[0]?.payload;
                  if (p) { setPicked({ ...p, _ts: Date.now() }); setVidErr(""); }
                }}>
                <CartesianGrid stroke={D ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.06)"}
                               strokeDasharray="2 4" />
                <XAxis dataKey="x" tick={{ fill: textSub, fontSize: 11 }}
                       label={{ value: "Cycle #", fill: textSub, position: "insideBottom", offset: -2, fontSize: 11 }} />
                <YAxis tick={{ fill: textSub, fontSize: 11 }}
                       label={{ value: "CT (s)", angle: -90, fill: textSub, position: "insideLeft", fontSize: 11 }} />
                <Tooltip content={<CtTooltip ideal={ideal} bgDeep={bgDeep} border={border} text={text} textMut={textMut} />} />
                {/* Target / Ideal CT reference line.  Beefed up 2026-05-14 —
                    on a Y axis that auto-fits a 775 s outlier, the prior thin
                    dashed-blue line at y=15 was invisible.  Bright amber +
                    thick stroke + bold left-label so the operator always
                    sees "Target Xs" even when the chart is zoomed out for
                    spike inspection. */}
                <ReferenceLine y={ideal} stroke="#fbbf24" strokeWidth={2.5}
                               strokeDasharray="6 3" ifOverflow="extendDomain"
                               label={{
                                 value: `Target ${ideal}s`,
                                 fill: "#fbbf24",
                                 fontSize: 11,
                                 fontWeight: 800,
                                 position: "insideTopLeft",
                                 offset: 6,
                               }} />
                <Line type="monotone" dataKey="y" stroke={STATUS_CLR.SETUP} strokeWidth={2}
                      dot={(props) => <CtDot {...props} ideal={ideal} D={D}
                                              picked={picked}
                                              onPick={setPicked} onErr={setVidErr} />}
                      activeDot={{
                        r: 8, cursor: "pointer",
                        stroke: D ? "#060912" : "#fff", strokeWidth: 2,
                        onClick: (e, d) => {
                          // d.payload has the cycle data (recharts ≥2.x)
                          const p = d?.payload || d;
                          if (p) { setPicked({ ...p, _ts: Date.now() }); setVidErr(""); }
                        },
                      }}
                      isAnimationActive={false}>
                  {/* Per-point CT label — sits above each dot so operator
                      can read the exact seconds without hovering.  Colored
                      red when over ideal, green otherwise, matching the
                      dot colour scheme. */}
                  <LabelList dataKey="y" position="top" offset={8}
                             formatter={(val) => `${Number(val).toFixed(1)}s`}
                             style={{
                               fontSize: 10,
                               fontWeight: 800,
                               fontFamily: "'Barlow Condensed',sans-serif",
                             }}
                             content={(props) => {
                               const { x, y, value } = props;
                               if (value == null || !isFinite(value)) return null;
                               const over = Number(value) > ideal;
                               return (
                                 <text x={x} y={(y || 0) - 8}
                                       textAnchor="middle"
                                       fill={over ? "#ef4444" : "#22c55e"}
                                       style={{ fontSize: 10, fontWeight: 800 }}>
                                   {Number(value).toFixed(1)}s
                                 </text>
                               );
                             }} />
                </Line>
              </LineChart>
            </ResponsiveContainer>

            {/* Floating box — opens on point click with the assigned
                sub-machine camera's cycle video playing INLINE.
                No separate modal — stays anchored over the graph. */}
            {picked && (
              <div
                onClick={(e) => e.stopPropagation()}
                style={{
                  position: "absolute", top: 8, right: 8, zIndex: 5,
                  background: bgCard, border: `1px solid ${border}`,
                  borderRadius: 8, padding: 10,
                  boxShadow: "0 10px 28px rgba(0,0,0,.45)",
                  width: 460, maxWidth: "calc(100% - 16px)",
                }}>
                <div style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  marginBottom: 8,
                }}>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 800, color: text }}>
                      Cycle #{picked.cycle_seq}
                      <span style={{
                        marginLeft: 8,
                        color: picked.y > ideal ? "#ef4444" : "#22c55e",
                        fontWeight: 700,
                      }}>{picked.y.toFixed(2)}s</span>
                      <span style={{ color: textSub, fontSize: 10, marginLeft: 4 }}>
                        / ideal {ideal}s
                      </span>
                    </div>
                    <div style={{ fontSize: 10, color: textMut, marginTop: 2 }}>
                      {new Date(picked.ts_end).toLocaleTimeString()}
                    </div>
                  </div>
                  <button
                    onClick={() => setPicked(null)}
                    title="Close"
                    style={{
                      padding: "3px 8px", borderRadius: 6,
                      border: `1px solid ${border}`, background: bgDeep,
                      color: text, cursor: "pointer", fontSize: 12,
                    }}>✕</button>
                </div>
                {vidErr ? (
                  <div style={{
                    width: "100%", aspectRatio: "16/9",
                    background: "#000", borderRadius: 6,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    color: "#fca5a5", fontSize: 13, padding: 20, textAlign: "center",
                  }}>
                    {vidErr}
                  </div>
                ) : (
                  <video
                    // 2026-05-18 — key combines cycle_seq AND a per-pick
                    // timestamp so the element FULLY remounts every
                    // click (even if user clicks the same dot twice).
                    // Without ts in the key, React would reuse the same
                    // <video> node and Chrome would replay its cached
                    // buffer instead of re-fetching.
                    key={`${picked.cycle_seq}-${picked._ts || ''}`}
                    controls autoPlay
                    style={{
                      // Camera sub-stream is 704x576 (~4:3); when forced
                      // into a 16:9 frame the picture gets horizontally
                      // stretched and looks fuzzy / wrong.  Reserve a
                      // 16:9 BOX with object-fit:contain so the native
                      // frame letterboxes inside without distortion.
                      width: "100%", aspectRatio: "16/9",
                      background: "#000", borderRadius: 6,
                      objectFit: "contain",
                    }}
                    // ?v= cache-buster makes each click a unique URL,
                    // so the browser cache never serves a stale clip
                    // from a previously-clicked cycle even if the
                    // backend's Cache-Control header was lost in transit.
                    src={`/api/submachines/${subId}/cycle-video?cycle_seq=${picked.cycle_seq}&token=${encodeURIComponent(token)}&v=${picked._ts || picked.cycle_seq}`}
                    onError={(e) => {
                      // Use the <video>'s own error code — no HEAD probe
                      // (some proxies / FastAPI routes refuse HEAD with 405).
                      // 1=ABORTED 2=NETWORK 3=DECODE 4=SRC_NOT_SUPPORTED
                      const code = e?.target?.error?.code;
                      if (code === 4)      setVidErr("Video not available for this cycle yet — try clicking again in a few seconds (camera recorder may still be writing the slice).");
                      else if (code === 2) setVidErr("Network error — backend not reachable.");
                      else if (code === 3) setVidErr("Video decode error.");
                      else                  setVidErr("Video failed to load.");
                    }}
                  />
                )}
              </div>
            )}
          </div>
        )}

        {/* Scroll slider — only when more than one window of cycles exists */}
        {chartDataAll.length > WINDOW && (
          <div style={{ padding: "6px 4px 0" }}>
            <input
              type="range"
              min={0}
              max={maxStart}
              value={windowStart}
              onChange={(e) => { setViewStart(Number(e.target.value)); setPicked(null); }}
              style={{
                width: "100%", cursor: "pointer", height: 3, display: "block",
                accentColor: STATUS_CLR.RUNNING,
              }}
            />
            <div style={{
              display: "flex", justifyContent: "space-between",
              fontSize: 10, color: textSub, marginTop: 2,
            }}>
              <span>#{chartDataAll[0]?.cycle_seq}</span>
              <span style={{ color: textMut }}>
                showing {windowStart + 1}–{Math.min(windowStart + WINDOW, chartDataAll.length)} of {chartDataAll.length}
                {viewStart !== null && (
                  <button
                    onClick={() => setViewStart(null)}
                    style={{
                      marginLeft: 8, padding: "1px 6px", borderRadius: 4,
                      border: `1px solid ${border}`, background: "transparent",
                      color: textSub, cursor: "pointer", fontSize: 9,
                    }}>latest ▶</button>
                )}
              </span>
              <span>#{chartDataAll[chartDataAll.length - 1]?.cycle_seq}</span>
            </div>
          </div>
        )}
      </div>

      {/* ── HOURLY TABLE (matches Fullscreen.jsx layout) ────── */}
      <div style={{ ...card({ overflow: "hidden" }) }}>
        <div style={{ padding: "8px 12px", borderBottom: `1px solid ${border}`,
                      display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ fontSize: 11, fontWeight: 900, color: textSub, letterSpacing: ".1em", textTransform: "uppercase" }}>
            Hourly target vs actual
          </div>
          <div style={{ fontSize: 10, color: textMut }}>
            target = machine ideal CT {hourly.ideal_ct ? `${hourly.ideal_ct.toFixed(1)}s` : "—"} · shift plan {hourly.shift_plan || 0}
          </div>
        </div>

        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed" }}>
            <thead>
              <tr style={{ background: bgDeep }}>
                <th style={{ padding: "6px 8px", textAlign: "left", fontWeight: 900, fontSize: 11,
                             color: textSub, borderRight: `1px solid ${border}`, width: 85 }}>METRIC</th>
                {shiftBuckets.map(s => (
                  <th key={s.slot_label} style={{
                    padding: "6px 4px", textAlign: "center", fontWeight: 800, fontSize: 11,
                    color: text, borderRight: `1px solid ${border}`,
                  }}>{s.slot_label}</th>
                ))}
                <th style={{ padding: "6px 8px", textAlign: "center", fontWeight: 900, fontSize: 11,
                             color: STATUS_CLR.QUALITY_ISSUE, width: 85 }}>TOTAL</th>
              </tr>
            </thead>
            <tbody>
              {/* TARGET row */}
              <tr style={{ borderTop: `1px solid ${border}` }}>
                <td style={{ padding: "8px 8px", fontWeight: 800, fontSize: 11, color: textMut,
                             borderRight: `1px solid ${border}` }}>TARGET</td>
                {shiftBuckets.map((s, i) => (
                  <td key={i} style={{
                    padding: "8px 4px", textAlign: "center", fontFamily: "monospace", fontSize: 16, fontWeight: 800,
                    color: s.target > 0 ? STATUS_CLR.SETUP : textMut,
                    borderRight: `1px solid ${border}`,
                  }}>{s.target}</td>
                ))}
                <td style={{ padding: "8px", textAlign: "center", fontFamily: "monospace",
                             fontSize: 18, fontWeight: 900, color: STATUS_CLR.QUALITY_ISSUE }}>
                  {shiftTotals.target}
                </td>
              </tr>

              {/* ACTUAL row with variance */}
              <tr style={{ borderTop: `2px solid ${border}`, background: bgDeep }}>
                <td style={{ padding: "5px 8px", fontWeight: 800, fontSize: 11, color: textSub,
                             borderRight: `1px solid ${border}` }}>ACTUAL</td>
                {shiftBuckets.map((s, i) => {
                  const variance = (s.actual || 0) - (s.target || 0);
                  return (
                    <td key={i} style={{ padding: "5px 4px", textAlign: "center",
                                         borderRight: `1px solid ${border}` }}>
                      <div style={{ fontFamily: "monospace", fontSize: 16, fontWeight: 800,
                                    color: s.actual > 0 ? STATUS_CLR.RUNNING : textMut }}>
                        {s.actual}
                      </div>
                      <div style={{ fontFamily: "monospace", fontSize: 10, marginTop: 1, fontWeight: 700,
                                    color: variance > 0 ? STATUS_CLR.RUNNING
                                         : variance < 0 ? STATUS_CLR.BREAKDOWN : textMut }}>
                        ({variance > 0 ? "+" : ""}{variance})
                      </div>
                    </td>
                  );
                })}
                <td style={{ padding: "5px 8px", textAlign: "center" }}>
                  <div style={{ fontFamily: "monospace", fontSize: 18, fontWeight: 900,
                                color: shiftTotals.actual > 0 ? STATUS_CLR.RUNNING : textMut }}>
                    {shiftTotals.actual}
                  </div>
                  <div style={{ fontFamily: "monospace", fontSize: 11, marginTop: 1, fontWeight: 800,
                                color: shiftTotals.variance > 0 ? STATUS_CLR.RUNNING
                                     : shiftTotals.variance < 0 ? STATUS_CLR.BREAKDOWN : textMut }}>
                    ({shiftTotals.variance > 0 ? "+" : ""}{shiftTotals.variance})
                  </div>
                </td>
              </tr>

              {shiftBuckets.length === 0 && (
                <tr><td colSpan={3} style={{ padding: "20px", textAlign: "center", color: textMut, fontSize: 12 }}>
                  {shift ? "No hourly data yet" : "Loading main-line slots…"}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Video plays inline inside the floating-box over the graph now;
          no separate full-screen modal needed. */}

      {/* ── Part History modal (Semi-Auto data search) ────────────── */}
      {phOpen && (
        <div onClick={() => setPhOpen(false)}
          style={{
            position: "fixed", inset: 0, zIndex: 80,
            background: "rgba(0,0,0,0.7)", backdropFilter: "blur(2px)",
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: 24,
          }}>
          <div onClick={e => e.stopPropagation()}
            style={{
              background: bgCard, border: `1px solid ${border}`,
              borderRadius: 12, width: "min(1400px, 96vw)",
              maxHeight: "90vh", display: "flex", flexDirection: "column",
              boxShadow: "0 24px 72px rgba(0,0,0,0.6)",
            }}>
            {/* Header */}
            <div style={{
              display: "flex", justifyContent: "space-between", alignItems: "center",
              padding: "12px 18px", borderBottom: `1px solid ${border}`,
            }}>
              <div>
                <div style={{ fontSize: 14, fontWeight: 800, color: text, letterSpacing: ".02em" }}>
                  📊 Part History — {meta?.machine_name || `Sub ${subId}`}
                </div>
                <div style={{ fontSize: 11, color: textMut, marginTop: 2 }}>
                  Semi-Auto data log with all captured parameters. Search by part code or time range. CSV download for any filter.
                </div>
              </div>
              <button onClick={() => setPhOpen(false)}
                style={{
                  background: "transparent", border: "none", color: textSub,
                  fontSize: 22, fontWeight: 700, cursor: "pointer", lineHeight: 1,
                  padding: "0 6px",
                }}>×</button>
            </div>

            {/* Filter bar */}
            <div style={{
              display: "flex", gap: 10, padding: "12px 18px",
              borderBottom: `1px solid ${border}`, flexWrap: "wrap", alignItems: "flex-end",
            }}>
              <div>
                <div style={{ fontSize: 9, color: textMut, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", marginBottom: 3 }}>Part Code</div>
                <input value={phPartCode} onChange={e => setPhPartCode(e.target.value)}
                  placeholder="partial match…"
                  style={{ padding: "6px 10px", borderRadius: 6, fontSize: 12, fontFamily: "monospace",
                    background: bgDeep, color: text, border: `1px solid ${border}`, outline: "none", minWidth: 220 }}/>
              </div>
              <div>
                <div style={{ fontSize: 9, color: textMut, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", marginBottom: 3 }}>From</div>
                <input type="datetime-local" value={phFrom} onChange={e => setPhFrom(e.target.value)}
                  style={{ padding: "6px 10px", borderRadius: 6, fontSize: 12,
                    background: bgDeep, color: text, border: `1px solid ${border}`, outline: "none" }}/>
              </div>
              <div>
                <div style={{ fontSize: 9, color: textMut, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", marginBottom: 3 }}>To</div>
                <input type="datetime-local" value={phTo} onChange={e => setPhTo(e.target.value)}
                  style={{ padding: "6px 10px", borderRadius: 6, fontSize: 12,
                    background: bgDeep, color: text, border: `1px solid ${border}`, outline: "none" }}/>
              </div>
              <div>
                <div style={{ fontSize: 9, color: textMut, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", marginBottom: 3 }}>Limit</div>
                <select value={phLimit} onChange={e => setPhLimit(Number(e.target.value))}
                  style={{ padding: "6px 10px", borderRadius: 6, fontSize: 12,
                    background: bgDeep, color: text, border: `1px solid ${border}`, outline: "none" }}>
                  <option value={100}>100 rows</option>
                  <option value={500}>500 rows</option>
                  <option value={2000}>2000 rows</option>
                  <option value={10000}>10000 rows</option>
                </select>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button onClick={loadPartHistory} disabled={phLoading}
                  style={{
                    padding: "8px 16px", borderRadius: 6, fontSize: 12, fontWeight: 800,
                    border: "1px solid #3b82f6", background: "#3b82f6", color: "#fff",
                    cursor: phLoading ? "not-allowed" : "pointer",
                  }}>{phLoading ? "Loading…" : "Search"}</button>
                <button onClick={downloadPartHistoryCsv} disabled={phLoading}
                  style={{
                    padding: "8px 16px", borderRadius: 6, fontSize: 12, fontWeight: 800,
                    border: `1px solid ${border}`, background: bgDeep, color: textSub,
                    cursor: "pointer",
                  }}>⬇ CSV</button>
                <button onClick={() => { setPhPartCode(""); setPhFrom(""); setPhTo(""); setPhLimit(500); }}
                  style={{
                    padding: "8px 12px", borderRadius: 6, fontSize: 11, fontWeight: 700,
                    border: `1px solid ${border}`, background: "transparent", color: textMut,
                    cursor: "pointer",
                  }}>Clear</button>
              </div>
              <div style={{ marginLeft: "auto", fontSize: 11, color: textMut }}>
                {phErr && <span style={{ color: "#ef4444", fontWeight: 700 }}>{phErr}</span>}
                {!phErr && phRows.length > 0 && <span><b style={{ color: text }}>{phRows.length}</b> row{phRows.length === 1 ? "" : "s"}</span>}
              </div>
            </div>

            {/* Results table */}
            <div style={{ flex: 1, overflow: "auto", padding: "0 18px 18px" }}>
              {phLoading ? (
                <div style={{ padding: 40, textAlign: "center", color: textMut, fontSize: 13 }}>Loading…</div>
              ) : phRows.length === 0 ? (
                <div style={{ padding: 40, textAlign: "center", color: textMut, fontSize: 13, fontStyle: "italic" }}>
                  {phErr ? "No results." : "Use filters above and click Search. Empty filter = last 24 hours."}
                </div>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, marginTop: 10 }}>
                  <thead style={{ position: "sticky", top: 0, background: bgCard, zIndex: 2 }}>
                    <tr>
                      {["Time", "Cycle", "Shift", "Part Code", "Model"].map(h => (
                        <th key={h} style={{ padding: "8px 10px", textAlign: "left", color: textMut, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", fontSize: 10, borderBottom: `2px solid ${border}`, whiteSpace: "nowrap" }}>{h}</th>
                      ))}
                      {phRegNames.map((n, i) => (
                        <th key={`reg-${i}`} style={{ padding: "8px 10px", textAlign: "right", color: textMut, fontWeight: 700, fontSize: 10, borderBottom: `2px solid ${border}`, whiteSpace: "nowrap" }}>
                          {n}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {phRows.map((r, idx) => {
                      const dv = Array.isArray(r.data_values) ? r.data_values : [];
                      return (
                        <tr key={r.id || idx}
                            style={{ borderBottom: `1px solid ${border}`,
                                      background: idx % 2 ? (D ? "rgba(255,255,255,0.02)" : "rgba(0,0,0,0.02)") : "transparent" }}>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace", color: text, whiteSpace: "nowrap" }}>
                            {r.ts_server ? new Date(r.ts_server).toLocaleString() : "—"}
                          </td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace", color: textSub, fontWeight: 700 }}>{r.cycle_seq ?? "—"}</td>
                          <td style={{ padding: "6px 10px", color: textSub }}>{r.shift_name || "—"}</td>
                          <td style={{ padding: "6px 10px", fontFamily: "monospace", color: "#3b82f6", fontWeight: 700, whiteSpace: "nowrap" }}>{r.part_code || "—"}</td>
                          <td style={{ padding: "6px 10px", color: textSub, whiteSpace: "nowrap" }}>{r.model_name || (r.model_number != null ? `#${r.model_number}` : "—")}</td>
                          {phRegNames.map((_, i) => {
                            const v = dv[i];
                            const scaled = v ? v.scaled : null;
                            return (
                              <td key={`v-${i}`}
                                  title={v ? `raw=${v.raw} reg=${v.register}` : ""}
                                  style={{ padding: "6px 10px", textAlign: "right",
                                            fontFamily: "monospace", color: text, whiteSpace: "nowrap" }}>
                                {scaled != null ? scaled : (v?.raw ?? "—")}
                              </td>
                            );
                          })}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Dot & tooltip helpers ────────────────────────────────────
function CtDot(props) {
  const { cx, cy, payload, ideal, D, picked, onPick, onErr } = props;
  if (typeof cx !== "number" || typeof cy !== "number") return null;
  const bad      = (payload?.y ?? 0) > ideal;
  const fill     = bad ? "#ef4444" : "#22c55e";
  const isPicked = picked && picked.cycle_seq === payload?.cycle_seq;
  // Per-dot click handler — guarantees the click lands no matter what
  // recharts' internal hit-testing does. The transparent 12 px circle
  // is the actual hit area; the visible dot is drawn on top.
  const handleClick = (e) => {
    e.stopPropagation();
    // Stamp _ts on every pick so React's `key` always changes — covers
    // the "click same cycle twice" case where cycle_seq stays the same
    // but the user wants a fresh fetch.
    if (onPick) onPick({ ...payload, _ts: Date.now() });
    if (onErr) onErr("");
  };
  return (
    <g style={{ cursor: "pointer" }} onClick={handleClick}>
      {/* Invisible big hit-target so finger/mouse clicks land easily */}
      <circle cx={cx} cy={cy} r={12} fill="transparent" />
      {isPicked && (
        <circle cx={cx} cy={cy} r={11} fill="none"
                stroke={fill} strokeWidth={2} opacity={0.6}>
          <animate attributeName="r" values="9;13;9" dur="1.4s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="0.7;0.2;0.7" dur="1.4s" repeatCount="indefinite" />
        </circle>
      )}
      <circle cx={cx} cy={cy} r={isPicked ? 6 : 4}
              fill={fill}
              stroke={D ? "#060912" : "#fff"}
              strokeWidth={isPicked ? 2 : 1} />
    </g>
  );
}

function CtTooltip({ active, payload, ideal, bgDeep, border, text, textMut }) {
  if (!active || !payload?.length) return null;
  const d   = payload[0].payload;
  const bad = d.y > ideal;
  return (
    <div style={{
      background: bgDeep, border: `1px solid ${border}`,
      padding: "8px 10px", borderRadius: 6, fontSize: 11, color: text,
    }}>
      <div style={{ fontWeight: 800, marginBottom: 4 }}>Cycle #{d.cycle_seq}</div>
      <div>CT: <span style={{ color: bad ? "#ef4444" : "#22c55e", fontWeight: 800 }}>
        {d.y.toFixed(2)}s
      </span></div>
      <div style={{ color: textMut }}>{new Date(d.ts_end).toLocaleTimeString()}</div>
      <div style={{ color: textMut, fontSize: 10, marginTop: 4 }}>click for video</div>
    </div>
  );
}
