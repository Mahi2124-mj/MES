import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { createPortal }                                       from "react-dom";
import { useParams, useNavigate } from "react-router-dom";
import axios from "axios";

const api = axios.create({ baseURL: "" });
api.interceptors.request.use(cfg => {
  const t = sessionStorage.getItem("mes_token");
  if (t) cfg.headers.Authorization = `Bearer ${t}`;
  return cfg;
});
// 401-handler: any auth-fail response → wipe session + bounce to /login.
// Stops the 401-flood that used to happen when an expired-tab kept
// polling realtime endpoints in the background.
api.interceptors.response.use(
  r => r,
  err => {
    if (err?.response?.status === 401) {
      try {
        ["mes_token","mes_username","user_role","user_id","user_dept_slug"]
          .forEach(k => sessionStorage.removeItem(k));
      } catch {}
      if (window.location.pathname !== "/login") {
        window.location.replace("/login");
      }
    }
    return Promise.reject(err);
  }
);

function useChartJS(cb, deps = []) {
  useEffect(() => {
    if (window.Chart) { cb(); return; }
    const s = document.createElement("script");
    // Local copy — shop-floor TVs run on an air-gapped LAN with no
    // internet, so the jsdelivr CDN never resolves there.  File is
    // served from /public/chart.umd.min.js by Vite.
    s.src = "/chart.umd.min.js";
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

function fmtPcs(v) {
  if (!v) return "0";
  if (v >= 100000) return (v/100000).toFixed(1)+"L";
  if (v >= 1000)   return (v/1000).toFixed(1)+"k";
  return String(v);
}

function toMin(t) {
  if (!t) return 0;
  const p = String(t).split(":").map(Number);
  return p[0]*60+(p[1]||0);
}

function oeeColor(v) {
  return v >= 85 ? "#22c55e" : v >= 65 ? "#f59e0b" : v > 0 ? "#ef4444" : "#3d4450";
}

function Gauge({ value, color, label, size = 95, textSub, isMain = false }) {
  const r = 38, cx = 50, cy = 52;
  const C      = 2 * Math.PI * r;
  const arcDeg = 220;
  const arcLen = (arcDeg / 360) * C;
  const pct    = Math.min(100, Math.max(0, value)) / 100;
  const fill   = pct * arcLen;
  // rotate(160) centers the 140° gap at the bottom (6-o'clock)
  return (
    <div style={{ display:"flex", flexDirection:"column", alignItems:"center" }}>
      <svg width={size} height={Math.round(size * 0.88)} viewBox="0 0 100 88">
        {/* track */}
        <circle cx={cx} cy={cy} r={r} fill="none"
          stroke="rgba(128,128,128,0.15)" strokeWidth={9} strokeLinecap="round"
          strokeDasharray={`${arcLen} ${C - arcLen}`}
          transform={`rotate(160 ${cx} ${cy})`} />
        {/* fill */}
        <circle cx={cx} cy={cy} r={r} fill="none"
          stroke={color} strokeWidth={9} strokeLinecap="round"
          strokeDasharray={`${fill} ${C - fill}`}
          transform={`rotate(160 ${cx} ${cy})`}
          style={{ transition:"stroke-dasharray 0.8s cubic-bezier(.4,0,.2,1)" }} />
        {/* value */}
        <text x={cx} y={cy - 3} textAnchor="middle" fill={color}
          style={{ fontSize:isMain?18:16, fontWeight:800, fontFamily:"monospace", dominantBaseline:"middle" }}>
          {value.toFixed(1)}%
        </text>
        {/* label inside */}
        {label && (
          <text x={cx} y={cy + 13} textAnchor="middle" fill={textSub || "rgba(128,128,128,0.6)"}
            style={{ fontSize:7.5, fontWeight:700, letterSpacing:".06em", dominantBaseline:"middle" }}>
            {label.toUpperCase()}
          </text>
        )}
      </svg>
    </div>
  );
}

// ── Status color map ──────────────────────────────────────────
const STATUS_CLR = {
  "IDLE":          "#94a3b8",
  "RUNNING":       "#22c55e",
  "BREAKDOWN":     "#ef4444",
  "QUALITY ISSUE": "#f97316",
  "QUALITY_ISSUE": "#f97316",
  "SETUP":         "#3b82f6",
  "MODEL_SETUP":   "#3b82f6",
  "MATERIAL WAIT": "#eab308",
  "MATERIAL_WAIT": "#eab308",
  "OTHERS":        "#a855f7",
  "OTHER_LOSS":    "#a855f7",
  "CHANGE OVER":   "#06b6d4",
  "CHANGE_OVER":   "#06b6d4",
  "SPEED":         "#22c55e",      // speed loss = green (not a stoppage)
  "SPEED_LOSS":    "#22c55e",
  "SPEED LOSS":    "#22c55e",
  "BREAK":         "#7dd3fc",
};

// Speed loss always renders green on the timeline bar
const SPEED_STATUSES = new Set(["SPEED","SPEED_LOSS","SPEED LOSS"]);

function getStatusColor(st) {
  if (!st) return STATUS_CLR["IDLE"];
  const up = st.toUpperCase();
  if (SPEED_STATUSES.has(up)) return STATUS_CLR["RUNNING"];
  return STATUS_CLR[up] || "#94a3b8";
}

// ─── CT distribution histogram (last 30-day density, 0.1 s buckets) ──
// Rendered into the chart card when `chartMode === "histogram"`.  Bars
// are coloured green/amber/red by their CT value vs ideal, and the
// global peak bucket is highlighted in yellow.  Total cycle count +
// peak label are shown in the top-right corner.
function CtDistributionChart({ data, idealCt, bgDeep, border, text, textMut, textSub, D, chartReady }) {
  const canvasRef = useRef(null);
  const chartRef  = useRef(null);

  useEffect(() => {
    if (!chartReady || !canvasRef.current || !window.Chart) return;
    if (!data || !data.buckets?.length) {
      if (chartRef.current) { chartRef.current.destroy(); chartRef.current = null; }
      return;
    }

    const buckets = data.buckets;
    const labels  = buckets.map(b => b.ct.toFixed(1));
    const counts  = buckets.map(b => b.count);

    // Bar colour per bucket — green below ideal, amber on, red above.
    // Peak bucket shows in solid yellow regardless so the operator can
    // locate the operating mode at a glance.
    const peak   = data.peak_bucket;
    const colors = buckets.map(b => {
      if (Math.abs(b.ct - peak) < 0.05) return "#fbbf24";
      if (b.ct > idealCt + 0.05)        return "rgba(239,68,68,0.85)";
      if (b.ct < idealCt - 0.05)        return "rgba(34,197,94,0.85)";
      return "rgba(245,158,11,0.85)";
    });

    const cfg = {
      type: "bar",
      data: {
        labels,
        datasets: [{
          label: "Cycles",
          data: counts,
          backgroundColor: colors,
          borderWidth: 0,
          borderRadius: 1,
          categoryPercentage: 0.95,
          barPercentage: 0.95,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        layout: { padding: { top: 8, bottom: 2, left: 4, right: 8 } },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "rgba(15,23,41,.95)",
            borderColor: "rgba(59,130,246,.5)",
            borderWidth: 1,
            padding: 8,
            titleFont: { weight: 800, size: 12 },
            bodyFont: { size: 11, family: "monospace" },
            callbacks: {
              title: i => `CT ${labels[i[0].dataIndex]}s`,
              label: t => `${t.parsed.y.toLocaleString()} cycles`,
            },
          },
          // Ideal-CT vertical guide via annotation-like fake dataset is
          // overkill — just paint it onto the canvas after render via
          // Chart.js's afterDraw plugin.
        },
        scales: {
          x: {
            ticks: {
              color: textMut, font: { size: 10, family: "monospace" },
              maxRotation: 0,
              autoSkip: true,
              autoSkipPadding: 16,
              // Show every whole-second tick label (14, 15, 16, …)
              callback(value) {
                const v = parseFloat(this.getLabelForValue(value));
                return Math.abs(v - Math.round(v)) < 0.05 ? `${Math.round(v)}s` : "";
              },
            },
            grid: { color: "rgba(255,255,255,0.03)" },
          },
          y: {
            beginAtZero: true,
            ticks: {
              color: textMut, font: { size: 10, family: "monospace" },
              callback: v => v.toLocaleString(),
            },
            grid: { color: "rgba(255,255,255,0.04)" },
          },
        },
      },
      plugins: [{
        id: "idealAndMedianLines",
        afterDraw(chart) {
          const xScale = chart.scales.x;
          const yScale = chart.scales.y;
          const ctx = chart.ctx;
          // 2026-05-18-r11 — theme-aware fill colors so the "ideal"
          // and "median" labels are readable in light mode (amber on
          // white was too pale; switched to dark amber).
          const idealColor  = D ? "#fbbf24" : "#b45309";
          const idealStroke = D ? "rgba(251,191,36,0.75)" : "rgba(180,83,9,0.85)";
          const medColor    = D ? "#a855f7" : "#7e22ce";
          const medStroke   = D ? "rgba(168,85,247,0.85)" : "rgba(126,34,206,0.9)";

          // ── Ideal CT vertical guide ──
          const idx = labels.findIndex(l => Math.abs(parseFloat(l) - idealCt) < 0.05);
          if (idx >= 0) {
            const xPx = xScale.getPixelForValue(idx);
            ctx.save();
            ctx.strokeStyle = idealStroke;
            ctx.lineWidth   = 1.4;
            ctx.setLineDash([5, 4]);
            ctx.beginPath();
            ctx.moveTo(xPx, yScale.top);
            ctx.lineTo(xPx, yScale.bottom);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.fillStyle = idealColor;
            ctx.font      = "bold 10px monospace";
            ctx.fillText(`ideal ${idealCt}s`, xPx + 4, yScale.top + 12);
            ctx.restore();
          }

          // ── 2026-05-18-r9 — Median CT vertical guide ──
          // Operator spec from sample.docx: "Monthly histographical
          // graoh ith median ct line".  Walk buckets in ct order,
          // accumulate counts, mark the bucket where cumulative count
          // crosses 50% of total — that ct is the population median.
          const total = counts.reduce((a, b) => a + b, 0);
          if (total > 0) {
            let acc = 0, medIdx = -1;
            for (let i = 0; i < buckets.length; i++) {
              acc += buckets[i].count;
              if (acc >= total / 2) { medIdx = i; break; }
            }
            if (medIdx >= 0) {
              const medCt = buckets[medIdx].ct;
              const xPxM  = xScale.getPixelForValue(medIdx);
              ctx.save();
              ctx.strokeStyle = medStroke;
              ctx.lineWidth   = 1.4;
              ctx.setLineDash([3, 3]);
              ctx.beginPath();
              ctx.moveTo(xPxM, yScale.top);
              ctx.lineTo(xPxM, yScale.bottom);
              ctx.stroke();
              ctx.setLineDash([]);
              ctx.fillStyle = medColor;
              ctx.font      = "bold 10px monospace";
              ctx.fillText(`median ${medCt.toFixed(1)}s`,
                            xPxM + 4, yScale.top + 26);
              ctx.restore();
            }
          }
        },
      }],
    };

    if (chartRef.current) {
      chartRef.current.data    = cfg.data;
      chartRef.current.options = cfg.options;
      chartRef.current.update("none");
    } else {
      chartRef.current = new window.Chart(canvasRef.current.getContext("2d"), cfg);
    }
  }, [data, idealCt, chartReady]);

  useEffect(() => () => {
    if (chartRef.current) { chartRef.current.destroy(); chartRef.current = null; }
  }, []);

  if (!data || !data.buckets?.length) {
    return (
      <div style={{
        position: "absolute", inset: 8, display: "flex",
        alignItems: "center", justifyContent: "center",
        color: textMut, fontSize: 13,
      }}>
        CT histogram — no cycle data in the last 30 days
      </div>
    );
  }

  return (
    <div style={{ position: "absolute", top: 8, left: 12,
                   width: "calc(100% - 24px)", height: "calc(100% - 18px)" }}>
      {/* Header strip — total count + peak.
          2026-05-18-r11 — theme-aware background + text colour so the
          chip is legible in light mode too (was dark-text-on-dark
          semi-transparent bg → invisible in light mode). */}
      <div style={{
        position: "absolute", top: 0, right: 4, zIndex: 2,
        fontSize: 11, color: text, fontWeight: 700,
        background: D ? "rgba(15,23,41,.7)" : "rgba(241,245,249,.92)",
        padding: "2px 8px", borderRadius: 4,
        border: `1px solid ${border}`,
      }}>
        {data.total_cycles?.toLocaleString()} cycles · peak{" "}
        <span style={{ color: D ? "#fbbf24" : "#b45309", fontWeight: 900 }}>
          {data.peak_bucket?.toFixed(1)}s
        </span>{" "}
        ({data.peak_count?.toLocaleString()})
      </div>
      <canvas ref={canvasRef} style={{ width: "100%", height: "100%" }} />
    </div>
  );
}

// ─── Mini production bar chart for the 9:16 quad grid (r9) ───────
// One reusable Chart.js wrapper for Weekly + Monthly mini-views in
// the portrait 2×2 chart panel.  `mode` controls aggregation:
//   "weekly"  — rolling last 7 days, each bar = that day's own total
//               (non-cumulative).  Maps the operator's sample.docx box
//               "Weekly Graph plan vs actual no cummulative".
//   "monthly" — current month, week-wise cumulative (W1, W1+W2, ...).
//               Maps "Monthly plan va actual cumulative ok".
// Plan = blue dashed reference line, Actual = green/red bars.
function MiniProductionChart({ mode, history, dark, chartReady }) {
  const canvasRef = useRef(null);
  const chartRef  = useRef(null);

  useEffect(() => {
    if (!chartReady || !canvasRef.current || !window.Chart) return;
    if (!history || history.length === 0) {
      if (chartRef.current) { chartRef.current.destroy(); chartRef.current = null; }
      return;
    }

    let labels = [], barData = [], planData = [];

    if (mode === "weekly") {
      const today = new Date();
      for (let i = 6; i >= 0; i--) {
        const d = new Date(today);
        d.setDate(today.getDate() - i);
        const k = d.toISOString().slice(0, 10);
        const e = history.find(r => r.record_date === k);
        labels.push(d.toLocaleDateString("en-IN", { day:"numeric", month:"short" }));
        barData.push(Number(e?.total_actual) || 0);
        planData.push(Number(e?.total_plan)   || 0);
      }
    } else if (mode === "monthly") {
      const now = new Date();
      const yr = now.getFullYear(), mo = now.getMonth();
      const monthHist = history.filter(d => {
        const dt = new Date(d.record_date);
        return dt.getFullYear() === yr && dt.getMonth() === mo;
      });
      const byWeek = {};
      for (const d of monthHist) {
        const dt = new Date(d.record_date);
        const wk = `W${Math.ceil(dt.getDate() / 7)}`;
        if (!byWeek[wk]) byWeek[wk] = { plan: 0, actual: 0 };
        byWeek[wk].plan   += Number(d.total_plan)   || 0;
        byWeek[wk].actual += Number(d.total_actual) || 0;
      }
      let cp = 0, ca = 0;
      for (const wk of Object.keys(byWeek).sort()) {
        cp += byWeek[wk].plan; ca += byWeek[wk].actual;
        labels.push(wk); planData.push(cp); barData.push(ca);
      }
    }

    // 2026-05-18-r11 — Colour palette change per operator spec:
    // "weekly monthly graph ki target line ka colour red yellow kr de
    // or actual ka green kr do".  Bars ALWAYS green (no longer
    // red-on-shortfall), and the Plan/target line switches from
    // blue → amber (#fbbf24, same yellow used for the ideal-CT
    // reference line on the histogram so the operator's eye has a
    // consistent "this is a target" cue across all four panels).
    const RUN  = "#22c55e";    // green — actual bars
    const AMB  = "#fbbf24";    // amber/yellow — plan/target line
    const txt  = dark ? "#c8d3e8" : "#1e293b";
    const txtMut = dark ? "#8092af" : "#475569";
    const grid = dark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.06)";

    // Bars uniformly green (translucent fill + solid border for crisp
    // edges).  Old behaviour painted RED when actual<plan, but the
    // operator wants the Plan line itself to be the colour-coded
    // reference — bars stay green regardless.
    const barColors  = barData.map(() => `${RUN}bb`);
    const barBorders = barData.map(() => RUN);

    // r10 — value-on-bar plugin so the operator can read each day's
    // actual count without hovering.  Tooltip kept for plan compare.
    const topLabelPlugin = {
      id: "miniTopLabels",
      afterDatasetsDraw(chart) {
        const { ctx: c } = chart;
        const meta = chart.getDatasetMeta(0);   // bar dataset
        if (!meta || meta.hidden) return;
        meta.data.forEach((bar, j) => {
          const v = barData[j];
          if (v == null || v === 0) return;
          c.save();
          c.font = "800 10px 'Segoe UI', sans-serif";
          c.fillStyle = txt;
          c.textAlign = "center";
          c.textBaseline = "bottom";
          const label = v >= 1000 ? `${(v/1000).toFixed(1)}k` : String(v);
          c.fillText(label, bar.x, bar.y - 3);
          c.restore();
        });
      },
    };

    const cfg = {
      type: "bar",
      data: {
        labels,
        datasets: [
          { type: "bar",  label: "Actual", data: barData,
            backgroundColor: barColors, borderColor: barBorders,
            borderWidth: 1.5, borderRadius: 4, order: 2,
            barPercentage: 0.85, categoryPercentage: 0.85 },
          { type: "line", label: "Plan (target)", data: planData,
            borderColor: AMB, borderWidth: 2.5, borderDash: [7, 4],
            pointRadius: 3.5, pointBackgroundColor: AMB,
            pointBorderColor: dark ? "#060912" : "#ffffff",
            pointBorderWidth: 1.2,
            tension: 0.25, fill: false, order: 1 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        layout: { padding: { top: 22, bottom: 2, left: 6, right: 8 } },
        interaction: { intersect: false, mode: "index" },
        plugins: {
          legend: {
            display: true, position: "top", align: "end",
            labels: { color: txtMut, boxWidth: 10, boxHeight: 8,
                      font: { size: 10, weight: 700 }, usePointStyle: true,
                      padding: 6 },
          },
          tooltip: {
            backgroundColor: "rgba(15,23,41,.95)",
            borderColor: "rgba(59,130,246,.5)",
            borderWidth: 1, padding: 9,
            titleFont: { weight: 800, size: 12 },
            bodyFont: { size: 11, family: "monospace" },
            callbacks: {
              title: items => items[0].label,
              label: c => {
                const v = c.parsed.y;
                const lbl = c.dataset.label;
                if (lbl === "Actual" && planData[c.dataIndex] > 0) {
                  const pct = (v / planData[c.dataIndex] * 100).toFixed(1);
                  return ` ${lbl}: ${v.toLocaleString()} pcs  (${pct}%)`;
                }
                return ` ${lbl}: ${v.toLocaleString()} pcs`;
              },
            },
          },
        },
        scales: {
          x: { ticks: { color: txt, font: { size: 10, weight: 700 }, maxRotation: 0 },
               grid: { color: grid, drawBorder: false } },
          y: { beginAtZero: true,
               ticks: { color: txt, font: { size: 10, weight: 700 },
                        callback: v => v >= 1000 ? `${(v/1000).toFixed(1)}k` : v,
                        maxTicksLimit: 5 },
               grid: { color: grid, drawBorder: false } },
        },
      },
      plugins: [topLabelPlugin],
    };

    if (chartRef.current) {
      chartRef.current.data    = cfg.data;
      chartRef.current.options = cfg.options;
      chartRef.current.update("none");
    } else {
      chartRef.current = new window.Chart(canvasRef.current.getContext("2d"), cfg);
    }
  }, [mode, history, dark, chartReady]);

  useEffect(() => () => {
    if (chartRef.current) { chartRef.current.destroy(); chartRef.current = null; }
  }, []);

  if (!history || history.length === 0) {
    return (
      <div style={{
        position: "absolute", inset: 8, display: "flex",
        alignItems: "center", justifyContent: "center",
        color: dark ? "#dde4ef" : "#1e293b", fontSize: 11,
        fontStyle: "italic",
      }}>
        {mode === "weekly" ? "Weekly" : "Monthly"} data loading…
      </div>
    );
  }

  return (
    <div style={{ position: "absolute", inset: 4 }}>
      <canvas ref={canvasRef} style={{ width: "100%", height: "100%" }} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
export default function Fullscreen() {
  const { lineId } = useParams();
  const [dark, setDark]      = useState(() => localStorage.getItem("fs_theme") !== "light");
  // Orientation — "portrait" (9:16 grid) vs "landscape" (16:9 grid).
  //
  // 2026-05-18-r8 — TWO-AXIS ORIENTATION MODEL.
  //
  // The previous revisions kept conflating two distinct ideas:
  //   (a) LAYOUT type  — which CSS grid template renders (3-col×6-row
  //       portrait vs 1-col×4-row landscape).  Picked by `isPortrait`.
  //   (b) ROTATION     — whether to CSS-rotate the entire dashboard
  //       90° so it can fill a vertically-mounted screen whose OS
  //       still publishes a landscape viewport.  Picked by
  //       `needsRotation` (further below).
  //
  // The natural mapping for (a) is: viewport portrait → portrait
  // layout, viewport landscape → landscape layout.  No rotation is
  // needed when the layout already matches the viewport's shape; the
  // grid fills the viewport naturally.
  //
  // Rotation only kicks in when there's a MISMATCH between the
  // operator-selected layout and the viewport shape — i.e. the user
  // toggled to portrait layout while their browser is still landscape
  // (because their 16:9 screen is mounted vertically and OS hasn't
  // auto-rotated the viewport).  This covers the operator's previous
  // "16:9 CALL KRU TOH POTRAIT VAALA AAYE" request without forcing
  // every desktop dev to see sideways content.
  const _detectOrient = () =>
    (typeof window !== "undefined" && window.innerHeight > window.innerWidth)
      ? "portrait" : "landscape";
  const [orientation, setOrientation] = useState(_detectOrient);
  const isPortrait = orientation === "portrait";

  // Live viewport tracking — needed to compute `needsRotation` below.
  // We track viewport-is-portrait separately because the user can
  // manually toggle `orientation` independently of the viewport shape.
  const [viewportPortrait, setViewportPortrait] = useState(() =>
    typeof window !== "undefined" && window.innerHeight > window.innerWidth);

  // Rotation flag: true only when the operator-selected layout
  // direction is the OPPOSITE of the viewport's natural orientation.
  // - viewport landscape + isPortrait=true  → rotate (vertical-mount case)
  // - viewport portrait  + isPortrait=false → rotate (manual landscape on portrait browser)
  // - everything else: layout fits viewport natively, no rotation.
  const needsRotation = isPortrait !== viewportPortrait;

  // 2026-05-18-r13 — Auto-correct orientation ONLY when the viewport
  // actually FLIPS shape (landscape↔portrait).  Earlier this ran on
  // every resize event, including same-shape resizes (scrollbar
  // appear, devtools open, browser frame nudge…), which then
  // overwrote the operator's manual toggle: "toggle krta hu to ye h
  // jata h vertical se landscape".  Now the ref tracks the last
  // observed viewport shape; orientation only auto-snaps when the
  // shape genuinely flips.  Manual toggles survive indefinitely
  // (until the operator rotates the screen / window).
  const prevVpShapeRef = useRef(
    typeof window !== "undefined" && window.innerHeight > window.innerWidth);
  useEffect(() => {
    const onResize = () => {
      const vp = window.innerHeight > window.innerWidth;
      setViewportPortrait(prev => prev === vp ? prev : vp);
      if (prevVpShapeRef.current !== vp) {
        prevVpShapeRef.current = vp;
        setOrientation(vp ? "portrait" : "landscape");
      }
    };
    onResize();
    window.addEventListener("resize", onResize);
    window.addEventListener("orientationchange", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("orientationchange", onResize);
    };
  }, []);

  // Keyboard shortcut R: toggle orientation (manual override).  Skip
  // when focus is in an input/textarea/contenteditable so admin notes
  // don't accidentally rotate the screen mid-typing.  The next resize
  // event will re-sync to viewport — this is intentional, the override
  // is one-shot.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "r" && e.key !== "R") return;
      const t = e.target;
      const tag = (t?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || t?.isContentEditable) return;
      setOrientation(o => o === "portrait" ? "landscape" : "portrait");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Loss Distribution → click → hourly-breakup floating popup state.
  // Click the Loss panel to open; click backdrop or × to close.  No
  // hover triggers, no on-screen "click" hint label.
  const [lossModalOpen, setLossModalOpen] = useState(false);
  const [lossBreakup,   setLossBreakup]   = useState(null);
  const [lossLoading,   setLossLoading]   = useState(false);

  // 2026-05-13 — Poka-Yoke turned into a compact green/red status button.
  // Click → modal that lists every PY failure event of the current shift,
  // grouped by hourly slot.  Same UX pattern as the Loss-breakup modal.
  const [pyModalOpen,    setPyModalOpen]    = useState(false);
  const [pyEvents,       setPyEvents]       = useState(null);
  const [pyEventsLoading,setPyEventsLoading]= useState(false);

  // 2026-05-13 — Sensor Status button + modal, same UX as PY.
  // Backend already publishes a passive X-bit health snapshot every 10 s
  // via /api/poka-yoke/sensor-sweep/<line_id> — see SENSOR SWEEP block in
  // routers/poka_yoke.py.  Each entry has a `status` field:
  //   "alive"  → sensor is toggling normally  (SENSING — green)
  //   "stuck"  → no toggle for >threshold     (DESENSED — red)
  // Card polls every 10 s in background; modal opens current snapshot.
  const [sensorSweep,    setSensorSweep]    = useState(null);
  const [sensorModalOpen,setSensorModalOpen]= useState(false);

  // ── Shift min/max cycle box ────────────────────────────────────
  // 2026-05-15 — Department review asked for a side box showing the
  // shift's slowest + fastest cycles with click → part_code + time +
  // video.  Backend resolves the actual cycle rows via the
  // /cycle-extremes endpoint (uses the line's ct_log table), so the
  // numbers shown here ALWAYS map to a real cycle, never stale aggregate.
  const [extremes, setExtremes] = useState(null);  // {min:{...}, max:{...}}

  // ── Header machine list (2026-05-16) ──────────────────────────
  // Operator wants the yellow header pill to show the MAIN machine's
  // name + (machine_seq), with a click to toggle/cycle through other
  // machines on this line (jump to their sub-machine fullscreen).
  // machine_seq is the M-N badge admin assigns in PLC Config.
  //
  // 2026-05-18 perf — seed from sessionStorage so the header pill paints
  // instantly on reload instead of waiting for /api/lines/{id}/machines
  // (which can be 1-2 s on a saturated LAN).  Background fetch still
  // refreshes the list, but the user never sees an empty header.
  const [machines,       setMachines]       = useState(() => {
    try {
      const raw = sessionStorage.getItem(`mes:fs:machines:line:${lineId}`);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch { return []; }
  });
  const [machineMenuOpen,setMachineMenuOpen]= useState(false);
  const navigate                            = useNavigate();

  const [rt,   setRt]        = useState(null);
  const [line, setLine]      = useState(null);
  const [connected, setConn] = useState(false);
  const [now,  setNow]       = useState(new Date());

  const chartRef    = useRef(null);
  const chartInst   = useRef(null);
  const lineRef     = useRef(null);
  const lastCtRef   = useRef(null);
  const rtRef       = useRef(null);
  const lastPokaRef = useRef(null);

  // ── CMS Camera cycle data ─────────────────────────────────────
  const [cmsData,      setCmsData]      = useState([]);
  const [cmsLoading,   setCmsLoading]   = useState(false);
  const [videoModal,   setVideoModal]   = useState(null);
  // 2026-05-21 — Slot NG list modal.  Holds {slot, date} when the user
  // clicks the NG count cell in the slot table.  Null = closed.
  const [ngListModal,  setNgListModal]  = useState(null);
  // 2026-05-22 — Loss-remark modal.  Holds {date, shift_name, slot_label,
  // loss_type, loss_label, loss_color, loss_secs} when production clicks
  // a loss cell in the Hourly Loss Breakup modal.  Null = closed.
  const [lossRemarkModal, setLossRemarkModal] = useState(null);
  // Timeline hover tooltip — rendered at root level to avoid overflow:hidden clipping
  const [tlTip, setTlTip] = useState(null); // {x, y, status, times, dur, color}
  // When the user puts the current cycle video into the browser's Picture-in-Picture
  // window, we keep the <video> element mounted (even if the modal UI is closed)
  // so that clicking another cycle simply swaps the src inside the existing PiP
  // window instead of opening a brand-new one.
  const [pipActive,    setPipActive]    = useState(false);
  const [showModalUI,  setShowModalUI]  = useState(false); // visibility of the modal chrome
  const videoElRef     = useRef(null);
  const [cmsViewStart, setCmsViewStart] = useState(null); // null = auto (latest 40)
  const [cmsSlotFilter, setCmsSlotFilter] = useState(""); // slot label (for dropdown display)
  const [cmsSlotRange,  setCmsSlotRange]  = useState(null); // {ssMin, seMin} — actual time range to filter
  // Part search moved to Historical page — removed from here
  const [ngNavIdx,      setNgNavIdx]      = useState(0);  // current NG navigation index (0-based)
  const cmsChartRef  = useRef(null);
  const cmsChartInst = useRef(null);
  const cmsScrollRef = useRef(null);
  const cmsSortedRef = useRef([]);

  // ── Filtered CMS data (slot + part search) ─────────────────
  // NOTE: depends on `allSlots` which is computed later in the render body.
  // Since allSlots is derived from `line` (stable ref), we compute a matching
  // slot range here inline rather than referencing allSlots (avoids circular dep).
  const filteredCms = useMemo(() => {
    let data = cmsData;
    // Slot filter: use pre-computed {ssMin, seMin} range (set when dropdown changes)
    if (cmsSlotRange) {
      const { ssMin, seMin } = cmsSlotRange;
      data = data.filter(cy => {
        if (!cy.ts) return false;
        const dt = new Date(cy.ts);
        const m  = dt.getHours()*60 + dt.getMinutes() + dt.getSeconds()/60;
        return seMin > ssMin ? (m >= ssMin && m < seMin) : (m >= ssMin || m < seMin);
      });
    }
    return data;
  }, [cmsData, cmsSlotRange]);

  // NG indices within the (filtered) dataset for up/down navigation
  const ngIndices = useMemo(
    () => filteredCms.map((cy, i) => cy.is_ng ? i : -1).filter(i => i >= 0),
    [filteredCms]
  );

  // ── Gap-shift freeze ─────────────────────────────────────────
  // When shift rolls over (ok_count drops to 0 + shift_name changes),
  // we freeze the last good rt so KPI numbers don't blank out.
  const frozenRtRef      = useRef(null);   // last rt snapshot with active production
  const prevShiftRef     = useRef(null);   // shift name we last saw with production
  const [gapShift, setGapShift] = useState(null); // "A→B" or null

  // ── Status log ───────────────────────────────────────────────
  // Each entry: { ts: ms, status: string, nowMinFrac: float, shift: string }
  // Primary storage: DB (mes_status_log) so all devices stay in sync.
  // localStorage is an immediate-render fallback (cleared on shift change).
  const statusLogRef  = useRef([]);
  // 2026-05-16 — bumped to v2 so any browser still holding the v1
  // localStorage payload (entries written by the now-removed frontend
  // POST path) ignores the old cache automatically.  v1 key never gets
  // touched again, so it's effectively orphaned and dies on browser
  // cache eviction.  v2 is server-fed only.
  const statusLogKey  = `mes_slog_v2_${lineId}`;
  const lastStatusRef = useRef(null);  // last recorded status (change-detector + dedup)
  // Debounce — only commit a status change after the new value has held
  // for STATUS_DEBOUNCE_MS.  Prevents the shift timeline from filling
  // with PLC-flicker slivers (transient SETUP / QUALITY_ISSUE bands
  // during a normal cycle handoff).
  const pendingStatusRef       = useRef(null);
  const pendingStatusSinceRef  = useRef(0);
  const dbFetchedRef  = useRef(null);  // shift name we last fetched DB for

  const [chartMode,   setChartMode]   = useState("ct");
  const [history,     setHistory]     = useState(null);
  const [histLoading, setHistLoading] = useState(false);
  const [pokaStatus,  setPokaStatus]  = useState([]);

  // 2026-05-18-r11 — Portrait FORCES chartMode = "ct".
  // The portrait 3-row quad layout shows CT graph + Weekly + Monthly
  // + Histogram all simultaneously — the chartMode toggle (which
  // only appears in landscape) is hidden.  If the operator was on
  // any non-"ct" mode in landscape and rotates to portrait, the
  // cmsChartInst build effect short-circuits (its guard is
  // `chartMode !== "ct"` → return), so the CT canvas in the quad's
  // top row stays empty and dot-click → video is dead.  Force "ct"
  // whenever isPortrait flips on so the chart always builds.
  useEffect(() => {
    if (isPortrait && chartMode !== "ct") {
      setChartMode("ct");
    }
  }, [isPortrait, chartMode]);

  // CT distribution histogram (last-30-day 0.1s buckets).  Populated only
  // when chartMode === "histogram" so we don't poll when the operator
  // isn't looking at it.  Refresh every 30 s when active.
  const [ctHistogram, setCtHistogram] = useState(null);

  // ── FY selector ──────────────────────────────────────────────
  const curFyStart = (() => {
    const d = new Date();
    return d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1;
  })();
  const [selectedFY, setSelectedFY] = useState(curFyStart);
  // Options: current FY + 2 previous
  // Dynamic FY options: current + up to 5 prior, limited to years present in history
  const fyOptions = (() => {
    const years = [];
    for (let i = 0; i <= 5; i++) years.push(curFyStart - i);
    if (history && history.length) {
      // Only keep years that have at least one record in history
      const dataYears = new Set(
        history.map(r => {
          const dt = new Date(r.record_date);
          // FY year starts in April: if month >= 3 (April) the FY start year = calendar year
          return dt.getMonth() >= 3 ? dt.getFullYear() : dt.getFullYear() - 1;
        })
      );
      return years
        .filter(y => y === curFyStart || dataYears.has(y))
        .map(y => ({ value: y, label: `FY${String(y).slice(-2)}-${String(y+1).slice(-2)}` }));
    }
    return years.slice(0, 3).map(y => ({ value: y, label: `FY${String(y).slice(-2)}-${String(y+1).slice(-2)}` }));
  })();

  // ── Mount: WIPE any stale localStorage cache ─────────────────────
  // 2026-05-15 17:15 — Department review aftermath.  Earlier the frontend
  // POSTed its own status guesses to mes_status_log and ALSO cached them
  // in localStorage.  Even after collector became the sole writer +
  // server data was cleaned, browsers kept rendering the stale garbage
  // from their own localStorage on next page load.  Nuke that cache on
  // every mount — server is the only source of truth now.  Also nuke
  // the legacy v1 key so the orphaned blob doesn't sit around forever
  // confusing future-me when debugging localStorage.
  useEffect(() => {
    try {
      localStorage.removeItem(statusLogKey);
      localStorage.removeItem(`mes_slog_${lineId}`);
    } catch {}
    statusLogRef.current = [];
  }, [statusLogKey, lineId]);

  // ── DB fetch: runs once per shift (re-runs when shift changes) ──
  // Now REPLACES instead of merging — collector is the authoritative
  // writer, so any in-memory entry that's not in the DB is stale.
  // During GAP_* we fetch the PREVIOUS real shift's timeline so the
  // bar doesn't blank out between shifts.
  useEffect(() => {
    if (!rt) return;
    const rawShift  = rt.shift_name || "A";
    const isGap     = rawShift.startsWith("GAP");
    let shiftName   = rawShift;
    if (isGap) {
      if (prevShiftRef.current && !prevShiftRef.current.startsWith("GAP")) {
        shiftName = prevShiftRef.current;
      } else {
        const m = /^GAP_([AB])([AB])$/.exec(rawShift);
        if (m) shiftName = m[1];
      }
    }
    const recDate   = rt.record_date || new Date().toISOString().slice(0, 10);
    if (dbFetchedRef.current === shiftName) return; // already loaded for this shift
    dbFetchedRef.current = shiftName;

    lastStatusRef.current = null;

    api.get(`/api/lines/${lineId}/status-log?date=${recDate}&shift=${encodeURIComponent(shiftName)}`)
      .then(res => {
        const rows   = Array.isArray(res.data) ? res.data : [];
        const cutoff = Date.now() - 16 * 3600 * 1000;
        statusLogRef.current = rows
          .filter(e => e.ts >= cutoff)
          .map(e => ({ ...e, shift: e.shift || shiftName }));
        try { localStorage.setItem(statusLogKey, JSON.stringify(statusLogRef.current)); } catch {}
      })
      .catch(() => {});
  }, [rt]); // eslint-disable-line

  useEffect(() => {
    document.title = line ? `${line.line_name}` : "Live Monitor";
  }, [line]);
  useEffect(() => { localStorage.setItem("fs_theme", dark?"dark":"light"); }, [dark]);
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  // ── Realtime fetch every 3s ──────────────────────────────────
  const fetchData = useCallback(async () => {
    try {
      const [rtRes, lineRes] = await Promise.all([
        api.get(`/api/lines/${lineId}/realtime`),
        lineRef.current
          ? Promise.resolve({ data: lineRef.current })
          : api.get(`/api/lines/${lineId}`),
      ]);
      setRt(rtRes.data);
      rtRef.current = rtRes.data;
      if (!lineRef.current) {
        lineRef.current = lineRes.data;
        setLine(lineRes.data);
      }
      setConn(true);
    } catch { setConn(false); }
  }, [lineId]);

  useEffect(() => {
    fetchData();
    const t = setInterval(fetchData, 3000);
    return () => clearInterval(t);
  }, [fetchData]);

  // ── Machines list (refresh every 60 s) ───────────────────────
  // Drives the header machine pill + its toggle dropdown.  60 s is
  // fine — admins don't change machine names on the fly during a
  // shift, and machine_seq edits propagate via the next poll.
  useEffect(() => {
    let alive = true;
    const fetchMachines = async () => {
      try {
        const r = await api.get(`/api/lines/${lineId}/machines`);
        const list = Array.isArray(r.data) ? r.data : [];
        if (alive) {
          setMachines(list);
          // Persist so the next reload paints instantly with the same list.
          try { sessionStorage.setItem(`mes:fs:machines:line:${lineId}`,
                                        JSON.stringify(list)); } catch {}
        }
      } catch { /* keep last known list on transient errors */ }
    };
    fetchMachines();
    const t = setInterval(fetchMachines, 60_000);
    return () => { alive = false; clearInterval(t); };
  }, [lineId]);

  // ── Shift min/max cycle (every 15s) ──────────────────────────
  // 2026-05-15 — Polled separately from /realtime because the backend
  // query is heavier (two ORDER BY scans on ct_log) and the values
  // only change when a new extreme cycle finishes — no need to fetch
  // every 3 s like the live KPI strip.
  useEffect(() => {
    let cancelled = false;
    const pullExtremes = async () => {
      const recDate = rtRef.current?.record_date || new Date().toISOString().slice(0,10);
      const shiftNm = rtRef.current?.shift_name || "";
      const qs = `date=${recDate}${shiftNm ? `&shift=${encodeURIComponent(shiftNm)}` : ""}`;
      try {
        const r = await api.get(`/api/lines/${lineId}/cycle-extremes?${qs}`);
        if (!cancelled) setExtremes(r.data || null);
      } catch { /* silent — preserve last value */ }
    };
    pullExtremes();
    const t = setInterval(pullExtremes, 15000);
    return () => { cancelled = true; clearInterval(t); };
  }, [lineId]);

  // Build a video URL for a cycle row (same pattern as in-chart click).
  // Used by min/max cards so click → same video modal experience.
  const openCycleVideo = useCallback((cy) => {
    if (!cy || cy.ct_value == null) return;
    const pc = String(cy.part_code || "")
      .replace(/:$/, "")
      .replace(/[^A-Za-z0-9._-]/g, "_")
      .replace(/^_+|_+$/g, "");
    const recDate = rtRef.current?.record_date || new Date().toISOString().slice(0,10);
    const shiftNm = rtRef.current?.shift_name || cy.shift_name || "";
    const qs = `date=${recDate}&shift=${encodeURIComponent(shiftNm)}&cycle_seq=${cy.cycle_seq || ""}`;
    // 2026-05-27 — Operator: "ek video sab pe chal rhi h".  When the
    // scanner is off / many cycles share one part_code, the by-part
    // MP4 returns the SAME file every click.  Always use the cycle_seq
    // time-window endpoint instead — each cycle's timestamp produces
    // its own unique clip from the rolling TS file.  Cache-buster
    // ensures the browser doesn't reuse an earlier cycle's stream.
    const videoSrc =
      `/api/lines/${lineId}/cycle-video?${qs}`
      + `&t=${cy.cycle_seq || Date.now()}`
      + `&token=${encodeURIComponent(sessionStorage.getItem("mes_token")||"")}`;
    const tsMs = cy.ts ? Date.parse(cy.ts) : Date.now();
    setVideoModal({
      cycle_seq: cy.cycle_seq,
      part_code: pc,
      ct_value:  cy.ct_value,
      ts:        tsMs,
      is_ng:     !!cy.is_ng,
      loading:   false,
      video_url: videoSrc,
    });
    setShowModalUI(!pipActive);
  }, [lineId, pipActive]);

  // ── Record status change → localStorage + DB ─────────────────
  // 2026-05-15 — DEPARTMENT REVIEW FIX.  Frontend NO LONGER writes
  // to mes_status_log.  The collector reads the PLC status bit at
  // 30 ms cadence and is now the SINGLE source of truth for the
  // timeline.  Multiple open dashboards (operator HMI + supervisor
  // LCD + manager laptop + etc.) each had their own 3 s poll cadence
  // + debounce + clock skew, and EACH was POSTing its interpretation
  // of `operating_status` — different tabs saw different transient
  // PLC-ladder values, so the timeline filled with phantom IDLE /
  // BREAKDOWN / BREAK chunks even while production count incremented
  // normally.
  //
  // Frontend now READS-ONLY from /api/lines/<id>/status-log on
  // initial mount; the periodic timeline refresh below (every 10 s)
  // re-pulls fresh server data so all dashboards stay in sync with
  // the collector's authoritative writes.
  const STATUS_DEBOUNCE_MS = 6000;  // kept as reference value (no longer used for writes)
  useEffect(() => {
    if (!rt || !line) return;
    // Seed the in-memory ref so the legacy debounce code that other
    // components key off of (e.g. PiP video logic) still sees a
    // current status — but we DO NOT POST anywhere.
    const rawStatus = rt.operating_status;
    if (rawStatus == null || rawStatus === "") return;
    if (lastStatusRef.current !== rawStatus) {
      lastStatusRef.current = rawStatus;
    }
  }, [rt, line]); // eslint-disable-line

  // ── Periodic timeline refresh from authoritative collector log ──
  // Every 10 s pull /status-log so segments that the collector wrote
  // during this minute appear on the bar.  Replaces the old POST-driven
  // local cache.
  useEffect(() => {
    if (!lineId) return;
    let cancelled = false;
    // During GAP_AB / GAP_BA the backend's shift_name is "GAP_*" but
    // the timeline must still display the PREVIOUS real shift's
    // entries (A or B).  Derive the underlying shift letter from
    // either prevShiftRef (set after a real shift was observed) or
    // the GAP code itself (GAP_AB → A, GAP_BA → B).
    const resolveShift = () => {
      const cur = rtRef.current?.shift_name || "";
      if (cur && !cur.startsWith("GAP")) return cur;
      if (prevShiftRef.current && !prevShiftRef.current.startsWith("GAP")) {
        return prevShiftRef.current;
      }
      // Page reload during gap → infer from gap code itself
      const m = /^GAP_([AB])([AB])$/.exec(cur);
      if (m) return m[1];      // GAP_AB → A, GAP_BA → B
      return "";
    };
    const pullTimeline = async () => {
      const d = new Date();
      const recDate = rtRef.current?.record_date || d.toISOString().slice(0, 10);
      const shiftNm = resolveShift();
      const qs = shiftNm ? `date=${recDate}&shift=${encodeURIComponent(shiftNm)}`
                         : `date=${recDate}`;
      try {
        const r = await api.get(`/api/lines/${lineId}/status-log?${qs}`);
        if (cancelled) return;
        const arr = Array.isArray(r.data) ? r.data : [];
        statusLogRef.current = arr.map(e => ({
          ts:         typeof e.ts === "number" ? e.ts : Date.parse(e.ts),
          status:     e.status,
          nowMinFrac: typeof e.nowMinFrac === "number" ? e.nowMinFrac : e.nowminfrac,
          shift:      e.shift || e.shift_name || shiftNm,
        })).filter(e => Number.isFinite(e.ts));
        try { localStorage.setItem(statusLogKey, JSON.stringify(statusLogRef.current)); } catch {}
      } catch { /* silent — keep previous snapshot */ }
    };
    pullTimeline();
    const t = setInterval(pullTimeline, 10000);
    return () => { cancelled = true; clearInterval(t); };
  }, [lineId, statusLogKey]);

  // ── Gap-shift detection ──────────────────────────────────────
  // Runs on every rt update. We freeze the previous shift's snapshot ONLY
  // while we're inside a GAP_* shift (the real between-shift window). As
  // soon as the backend reports a real shift (A/B/...), we show its live
  // data immediately — even if ok_count is 0 (setup/changeover period),
  // so the dashboard reflects the new shift at its scheduled start time.
  useEffect(() => {
    if (!rt) return;
    const curShift = rt.shift_name || "";
    const curCount = rt.ok_count   || 0;
    const isGap    = curShift.startsWith("GAP");

    if (!isGap && curShift) {
      // Real shift in progress — always show live data. Update frozen
      // snapshot while production is active so we have a good fallback
      // the next time we enter a GAP window.
      if (curCount > 0) frozenRtRef.current = rt;
      prevShiftRef.current = curShift;
      setGapShift(null);
    } else if (isGap) {
      // Between shifts — keep showing the previous shift's frozen data so
      // the screen doesn't blank out. Only enter this state if we have a
      // previous shift snapshot; otherwise show whatever the backend gives.
      if (prevShiftRef.current && prevShiftRef.current !== curShift) {
        setGapShift(`${prevShiftRef.current} → ${curShift}`);
        prevShiftRef.current = curShift;
      }
    }
  }, [rt]); // eslint-disable-line

  // ── Poka-yoke polling every 5s ───────────────────────────────
  // Primary key is model_bit (stable across name edits); model_name is a
  // secondary hint for legacy rows. Always re-fetches so bypass events
  // surfacing after first load still reach the UI.
  useEffect(() => {
    const fetchPoka = () => {
      const modelName = rtRef.current?.current_model_name || "";
      const modelBit  = rtRef.current?.current_model_number;
      lastPokaRef.current = modelName;
      const params = new URLSearchParams();
      if (modelBit != null && modelBit !== 0) params.append("model_bit",  String(modelBit));
      if (modelName)                          params.append("model_name", modelName);
      const qs  = params.toString();
      const url = `/api/poka-yoke/live/${lineId}${qs ? `?${qs}` : ""}`;
      api.get(url)
        .then(r => setPokaStatus(Array.isArray(r.data) ? r.data : []))
        .catch(() => {});
    };
    fetchPoka();
    const t = setInterval(fetchPoka, 5000);
    return () => clearInterval(t);
  }, [lineId]); // eslint-disable-line


  // ── Sensor sweep poll (10 s) ────────────────────────────────
  // 2026-05-14 — switched from raw `/sensor-sweep/` (which returned
  // whatever the collector last POSTed, often empty) to the new
  // `/sensor-health/` endpoint that ALWAYS returns one entry per
  // applicable PY of the current model, with status derived from the
  // sweep cache when present and "unknown" otherwise.  Net result: the
  // sensor modal shows the configured list immediately, the operator
  // never sees "0 sensors" again, and as soon as the collector publishes
  // its first row the statuses flip from unknown → alive/stuck.
  useEffect(() => {
    let alive = true;
    const fetchSweep = () => {
      api.get(`/api/poka-yoke/sensor-health/${lineId}`)
        .catch(err => {
          // Fallback for old backends that don't have the new endpoint
          // yet (i.e. someone deployed the frontend but forgot to restart
          // the MES API).  Pull the legacy /sensor-sweep/ shape instead
          // so the modal still shows whatever the collector has cached.
          return api.get(`/api/poka-yoke/sensor-sweep/${lineId}`)
            .then(r => {
              const legacy = r.data || r;
              return { data: { ...legacy, health: null, counts: null,
                                checks: (legacy.entries || []).map(e => ({
                                  py_no:        e.py_no,
                                  py_name:      e.py_name,
                                  sensing_bits: e.sensing_bits,
                                  status:       e.status,
                                  stuck_for_sec: e.stuck_for_sec ?? null,
                                  last_toggle_at: e.last_toggle_at,
                                  bits: [{ bit: e.bit, status: e.status,
                                            current_value: e.current_value,
                                            last_toggle_at: e.last_toggle_at,
                                            stuck_for_sec: e.stuck_for_sec ?? null }],
                                })) } };
            })
            .catch(() => null);
        })
        .then(r => {
          if (!alive || !r) return;
          const data = r.data || r;
          // Adapt to the legacy shape the rest of this file expects:
          // `swept_at` + `entries[]` with bit/py_no/py_name/status/etc.
          const entries = (data.checks || []).map(c => ({
            bit:                 (c.bits && c.bits[0] && c.bits[0].bit) || "",
            current_value:       (c.bits && c.bits[0] && c.bits[0].current_value) ?? null,
            last_toggle_at:      c.last_toggle_at,
            stuck_for_sec:       c.stuck_for_sec,
            status:              c.status,
            py_no:               c.py_no,
            py_name:             c.py_name,
            sensing_bits:        c.sensing_bits,
            d_bit:               c.register_addr,
            // Extras for richer display
            bits_detail:         c.bits,
          }));
          setSensorSweep({
            swept_at:           data.swept_at,
            entries:            entries,
            health:             data.health,
            counts:             data.counts,
            stuck_threshold_sec: data.stuck_threshold_sec,
            model_bit:          data.model_bit,
            model_name:         data.model_name,
          });
        })
        .catch(() => {});
    };
    fetchSweep();
    const t = setInterval(fetchSweep, 10000);
    return () => { alive = false; clearInterval(t); };
  }, [lineId]);


  // ── Cycle time log — auto-polls backend every 8s ─────────
  useEffect(() => {
    if (chartMode !== "ct" || !rt) return;
    const recDate = rt.record_date || new Date().toISOString().slice(0,10);
    const shift   = rt.shift_name  || "";
    // Clear old shift's data immediately so the graph doesn't show stale cycles
    setCmsData([]);
    setCmsViewStart(null);
    // Destroy any chart instance left over from the previous shift so the
    // graph area is visually empty until the new shift's first cycle.
    if (cmsChartInst.current) {
      cmsChartInst.current.destroy();
      cmsChartInst.current = null;
    }
    const fetchCt = (first) => {
      if (first) setCmsLoading(true);
      api.get(`/api/lines/${lineId}/ct-history?date=${recDate}&shift=${encodeURIComponent(shift)}`)
        .then(res => {
          setCmsData(Array.isArray(res.data) ? res.data : []);
          if (first) setCmsLoading(false);
        })
        .catch(() => { if (first) setCmsLoading(false); });
    };
    fetchCt(true);
    const t = setInterval(() => fetchCt(false), 8000);
    return () => clearInterval(t);
  }, [chartMode, rt?.shift_name, rt?.record_date, lineId]); // eslint-disable-line

  // ── Historical chart data ─────────────────────────────────────
  useEffect(() => {
    if (chartMode === "ct" || chartMode === "histogram") return;
    setHistory(null);   // clear stale data so buildChart waits for correct fetch
    const daysMap = { weekly:14, monthly:90, yearly:1200 };
    setHistLoading(true);
    api.get(`/api/lines/${lineId}/production_history?days=${daysMap[chartMode]||14}`)
      .then(r => setHistory(r.data))
      .catch(() => setHistory([]))
      .finally(() => setHistLoading(false));
  }, [chartMode, lineId]);

  // ── 2026-05-18-r9 — QUAD-PANEL history (portrait only) ────────
  // The 2×2 portrait layout shows Weekly + Monthly side-by-side, both
  // derived from the same 90-day production_history pull (Weekly slices
  // the last 7 days; Monthly groups current month by week).  Fetched
  // once per shift change so we don't double-poll the backend.
  const [quadHistory, setQuadHistory] = useState(null);
  useEffect(() => {
    if (!isPortrait) return;
    api.get(`/api/lines/${lineId}/production_history?days=90`)
      .then(r => setQuadHistory(r.data))
      .catch(() => setQuadHistory([]));
  }, [isPortrait, lineId, rt?.record_date]);

  // ── CT distribution histogram — fetched + refreshed only when active ─
  // 2026-05-18 — Moved from the (now-deleted) Summary wallboard.  Shows
  // the 0.1 s-bucket density of every cycle in the last 30 days so the
  // supervisor can see the operating range at a glance.  Backend caps
  // ct_value to 0-60 s so a 600 s outlier doesn't flatten the X axis.
  // Fires when chartMode === "histogram" (16:9 toggle) OR always in 9:16
  // portrait (where the histogram is its own card at outer-grid row 4).
  useEffect(() => {
    if (chartMode !== "histogram" && !isPortrait) return;
    let alive = true;
    const fetchHisto = () =>
      api.get(`/api/lines/${lineId}/ct-histogram?days=30`)
         .then(r => { if (alive) setCtHistogram(r.data); })
         .catch(() => {});
    fetchHisto();
    const id = setInterval(fetchHisto, 30000);
    return () => { alive = false; clearInterval(id); };
  }, [chartMode, lineId, isPortrait]);

  // ── Hourly Loss breakup — fetched on demand when modal opens, then
  // refreshed every 30 s while the modal stays open. ──
  useEffect(() => {
    if (!lossModalOpen) return;
    const recDate  = rt?.record_date;
    const shiftNm  = rt?.shift_name;
    if (!recDate || !shiftNm) return;
    let alive = true;
    const fetchBreakup = (initial = false) => {
      if (initial) setLossLoading(true);
      api.get(`/api/lines/${lineId}/hourly-loss-breakdown?date=${recDate}&shift=${encodeURIComponent(shiftNm)}`)
        .then(r => { if (alive) setLossBreakup(r.data || r); })
        .catch(() => {})
        .finally(() => { if (alive && initial) setLossLoading(false); });
    };
    fetchBreakup(true);
    const t = setInterval(() => fetchBreakup(false), 30000);
    return () => { alive = false; clearInterval(t); };
  }, [lossModalOpen, lineId, rt?.record_date, rt?.shift_name]);

  // ── PY events fetcher (only when modal is open) ─────────────
  // Pulls every recent failure event for the line and lets the
  // modal filter to today's shift on the fly.  Auto-refresh every
  // 30 s while open so live failures appear without a manual reload.
  useEffect(() => {
    if (!pyModalOpen) return;
    let alive = true;
    const fetchEvents = (initial = false) => {
      if (initial) setPyEventsLoading(true);
      // 2026-05-16 — switched to server-side episode builder.  Old
      // /events?limit=500 path:
      //   (a) capped at 500 → high-frequency PYs (e.g. D401's 300+
      //       events/shift) crowded out other PYs entirely
      //   (b) shipped raw events, frontend had to re-parse + group
      //   (c) modal showed only what /events returned in current page
      // New /bypass-episodes endpoint:
      //   (a) returns ALL episodes for current shift (no cap)
      //   (b) groups events into start/end episodes per py_no
      //   (c) attributes each episode to its hourly slot
      //   (d) persists the result to a local JSON file for audit + DR
      //   (e) honors shift boundary: at shift end the file rotates;
      //       during OT it keeps appending to the same shift's file
      const today = rt?.record_date || new Date().toISOString().slice(0,10);
      const sh    = rt?.shift_name || "";
      const qs    = `date=${today}${sh ? `&shift=${encodeURIComponent(sh)}` : ""}`;
      api.get(`/api/poka-yoke/bypass-episodes/${lineId}?${qs}`)
        .then(r => { if (alive) setPyEvents(r.data || r); })
        .catch(() => {})
        .finally(() => { if (alive && initial) setPyEventsLoading(false); });
    };
    fetchEvents(true);
    const t = setInterval(() => fetchEvents(false), 15000);
    return () => { alive = false; clearInterval(t); };
  }, [pyModalOpen, lineId]); // eslint-disable-line

  // ── Theme tokens ─────────────────────────────────────────────
  const D       = dark;
  const bg      = D ? "#060912" : "#e8eef5";
  const bgCard  = D ? "#0a0f1a" : "#ffffff";
  const bgDeep  = D ? "#070c14" : "#dde5f0";
  const border  = D ? "#141e2e" : "#b8c8dc";
  const text    = D ? "#e8edf5" : "#0f172a";
  const textSub = D ? "#8092af" : "#374151";
  const textMut = D ? "#dde4ef" : "#1e293b";

  const ideal  = line?.plc_config?.ideal_cycle_time || 15;
  const ctData = rt ? Array.from({ length:20 }, (_,i) => {
    const v = rt[`ct${i+1}`];
    return v && v > 0 ? v : null;
  }) : [];

  const [chartReady, setChartReady] = useState(false);
  useEffect(() => { if (rt && !chartReady) setChartReady(true); }, [rt]); // eslint-disable-line

  // ── Load Chart.js once on mount ──────────────────────────
  const [chartLib, setChartLib] = useState(!!window.Chart);
  useEffect(() => {
    if (window.Chart) { setChartLib(true); return; }
    const s = document.createElement("script");
    // Local copy — shop-floor TVs run on an air-gapped LAN with no
    // internet, so the jsdelivr CDN never resolves there.  File is
    // served from /public/chart.umd.min.js by Vite.
    s.src = "/chart.umd.min.js";
    s.onload = () => setChartLib(true);
    document.head.appendChild(s);
  }, []);

  // ── Main chart (Weekly / Monthly / Yearly) ────────────────
  useEffect(() => {
    if (!chartReady || !chartLib) return;
    buildChart(chartMode, history, selectedFY);
    // Defensive: TV browsers (older Samsung Tizen / LG WebOS) sometimes
    // don't fire ResizeObserver when the canvas transitions from
    // display:none → display:block.  Chart.js then keeps the 0-width
    // dimensions it captured at build time, and the bar/line chart
    // never paints.  Forcing chart.resize() — twice on consecutive
    // animation frames — picks up the real parent size after the
    // layout has truly settled.  The second resize covers the 9:16
    // case where CSS rotate(90deg) takes an extra frame to commit.
    const cleanupIds = [];
    const id1 = requestAnimationFrame(() => {
      try { chartInst.current?.resize(); } catch (_) {}
      const id2 = requestAnimationFrame(() => {
        try { chartInst.current?.resize(); } catch (_) {}
      });
      cleanupIds.push(id2);
    });
    cleanupIds.push(id1);
    return () => cleanupIds.forEach(cancelAnimationFrame);
    // include isPortrait so toggling orientation triggers a rebuild +
    // resize cycle — otherwise the canvas keeps its pre-rotation size.
  }, [dark, chartMode, history, chartReady, chartLib, selectedFY, isPortrait]); // eslint-disable-line

  // In-place CT chart update (no destroy/rebuild on every rt tick)
  useEffect(() => {
    if (chartMode !== "ct" || !chartInst.current || !rt) return;
    const valid = ctData.filter(v => v !== null);
    if (valid.length > 0) lastCtRef.current = ctData;
    const display = valid.length > 0 ? ctData : (lastCtRef.current || ctData);
    const avg = valid.length
      ? (valid.reduce((a,b) => a+b, 0) / valid.length).toFixed(1)
      : null;

    chartInst.current.data.datasets[0].data  = display;
    chartInst.current.data.datasets[0].label = avg ? `Cycle Time  (Avg: ${avg}s)` : "Cycle Time";
    chartInst.current.data.datasets[0].pointRadius =
      display.map(v => v === null ? 0 : 4);
    chartInst.current.data.datasets[0].pointBackgroundColor =
      display.map(v => v === null ? "transparent" : v <= ideal ? STATUS_CLR["RUNNING"] : STATUS_CLR["BREAKDOWN"]);

    const yScale  = chartInst.current.options.scales.y;
    const allVals = display.filter(v => v !== null);
    if (allVals.length) {
      const mn = Math.min(...allVals), mx = Math.max(...allVals);
      const pad = Math.max(3, (mx-mn)*0.25);
      yScale.suggestedMin = Math.max(0, Math.min(mn-pad, ideal-8));
      yScale.suggestedMax = Math.max(mx+pad, ideal+6);
    } else {
      yScale.suggestedMin = Math.max(0, ideal-8);
      yScale.suggestedMax = ideal + 25;
    }
    chartInst.current.update("none");
  }, [rt]); // eslint-disable-line

  const buildChart = (mode, hist, fyStartYear) => {
    if (!chartRef.current || !window.Chart) return;
    if (mode === "ct") return; // ct mode handled by CMS chart below
    if (!hist || hist.length === 0) return;
    // Destroy any chart on this canvas (handles ref-out-of-sync edge cases)
    const _existing = window.Chart.getChart?.(chartRef.current);
    if (_existing) { try { _existing.destroy(); } catch(_){} }
    if (chartInst.current) { try { chartInst.current.destroy(); } catch(_){} chartInst.current = null; }
    const ctx = chartRef.current.getContext("2d");

    if (mode === "ct") {
      const raw   = ctData.filter(v => v!==null).length > 0 ? ctData : (lastCtRef.current || ctData);
      const valid = raw.filter(v => v !== null);
      if (valid.length > 0) lastCtRef.current = raw;
      const avg = valid.length ? (valid.reduce((a,b)=>a+b,0)/valid.length).toFixed(1) : null;
      const mn  = valid.length ? Math.min(...valid) : ideal;
      const mx  = valid.length ? Math.max(...valid) : ideal;
      const pad = Math.max(3, (mx-mn)*0.25);

      chartInst.current = new window.Chart(ctx, {
        type:"line",
        data:{
          labels: Array.from({length:20},(_,i)=>i+1),
          datasets:[
            {
              label: avg ? `Cycle Time  (Avg: ${avg}s)` : "Cycle Time",
              data: raw, borderWidth:2.5, tension:0.35, fill:false,
              pointRadius: raw.map(v => v!==null ? 4 : 0),
              pointHoverRadius: 6,
              pointBackgroundColor: raw.map(v => v===null ? "transparent" : v<=ideal ? STATUS_CLR["RUNNING"] : STATUS_CLR["BREAKDOWN"]),
              pointBorderColor: D ? "#060912" : "#ffffff",
              pointBorderWidth: 1.5,
              spanGaps: false,
              segment:{
                borderColor: ctx2 => {
                  const live = ctx2.chart.data.datasets[0].data;
                  const a = live[ctx2.p0DataIndex], b = live[ctx2.p1DataIndex];
                  if (a===null||b===null) return "transparent";
                  if (a<=ideal && b<=ideal) return STATUS_CLR["RUNNING"];
                  if (a>ideal  && b>ideal)  return STATUS_CLR["BREAKDOWN"];
                  const ratio = Math.abs(a-ideal)/Math.abs(a-b);
                  const c2 = chartRef.current?.getContext("2d");
                  if (!c2) return STATUS_CLR["RUNNING"];
                  const grad = c2.createLinearGradient(ctx2.p0.x, 0, ctx2.p1.x, 0);
                  if (a > ideal) {
                    grad.addColorStop(0,                      STATUS_CLR["BREAKDOWN"]);
                    grad.addColorStop(Math.max(0,ratio-0.3),  STATUS_CLR["BREAKDOWN"]);
                    grad.addColorStop(ratio,                  STATUS_CLR["QUALITY ISSUE"]);
                    grad.addColorStop(Math.min(1,ratio+0.3),  STATUS_CLR["RUNNING"]);
                    grad.addColorStop(1,                      STATUS_CLR["RUNNING"]);
                  } else {
                    grad.addColorStop(0,                      STATUS_CLR["RUNNING"]);
                    grad.addColorStop(Math.max(0,ratio-0.3),  STATUS_CLR["RUNNING"]);
                    grad.addColorStop(ratio,                  STATUS_CLR["QUALITY ISSUE"]);
                    grad.addColorStop(Math.min(1,ratio+0.3),  STATUS_CLR["BREAKDOWN"]);
                    grad.addColorStop(1,                      STATUS_CLR["BREAKDOWN"]);
                  }
                  return grad;
                },
              },
            },
            {
              label: `Threshold ${ideal}s`,
              data: Array(20).fill(ideal),
              borderColor: "rgba(251,191,36,0.85)",
              borderWidth:2, borderDash:[6,4],
              pointRadius:0, fill:false, tension:0,
            },
          ],
        },
        options:{
          responsive:true, maintainAspectRatio:false, animation:false,
          interaction:{intersect:false, mode:"index"},
          plugins:{
            legend:{ position:"top", align:"end", labels:{ color:textSub, boxWidth:12, font:{size:11}, usePointStyle:true, padding:10 } },
            tooltip:{
              backgroundColor: D?"#0d1420":"#fff",
              titleColor:text, bodyColor:textSub, borderColor:border, borderWidth:1, padding:10,
              titleFont:{size:12}, bodyFont:{size:12},
              callbacks:{
                title: i => `Cycle #${i[0].label}`,
                label: c => {
                  if (c.datasetIndex===1) return ` Threshold: ${ideal}s`;
                  const v = c.parsed.y;
                  return v===null ? " No data" : ` ${v.toFixed(2)}s  ${v>ideal?"⚠ Above":"✓ Below"} target`;
                },
              },
            },
          },
          scales:{
            y:{
              beginAtZero:false,
              suggestedMin: Math.max(0, Math.min(mn-pad, ideal-8)),
              suggestedMax: Math.max(mx+pad, ideal+6),
              grid:{ color:D?"rgba(255,255,255,0.04)":"rgba(0,0,0,0.04)", drawBorder:false },
              border:{ display:false },
              ticks:{ color:textSub, font:{size:11}, callback:v=>`${v}s`, maxTicksLimit:7 },
            },
            x:{
              grid:{ color:D?"rgba(255,255,255,0.04)":"rgba(0,0,0,0.04)", drawBorder:false },
              border:{ display:false },
              ticks:{ color:textSub, font:{size:11} },
            },
          },
        },
      });
      return;
    }

    // ── Bar label plugin: value on top of each bar ────────────
    const topLabelPlugin = {
      id: "topLabels",
      afterDatasetsDraw(chart) {
        const { ctx: c } = chart;
        chart.data.datasets.forEach((ds, di) => {
          if (ds.type === "line") return;
          const meta = chart.getDatasetMeta(di);
          if (meta.hidden) return;
          meta.data.forEach((bar, j) => {
            const v = ds.data[j];
            if (!v) return;
            c.save();
            c.font = "700 9px 'Segoe UI',sans-serif";
            c.fillStyle = D ? "#c8d3e8" : "#374151";
            c.textAlign = "center";
            c.textBaseline = "bottom";
            c.fillText(fmtPcs(v), bar.x, bar.y - 2);
            c.restore();
          });
        });
      },
    };

    let labels = [], barData = [], planData = [], chartTitle2 = "";

    if (mode === "weekly") {
      // Rolling last 7 days — each bar = that day's own total (non-cumulative)
      const today = new Date();
      for (let i = 6; i >= 0; i--) {
        const d = new Date(today);
        d.setDate(today.getDate() - i);
        const k = d.toISOString().slice(0, 10);
        const entry = (hist || []).find(r => r.record_date === k);
        labels.push(d.toLocaleDateString("en-IN", { day:"numeric", month:"short" }));
        barData.push(Number(entry?.total_actual) || 0);
        planData.push(Number(entry?.total_plan)   || 0);
      }

    } else if (mode === "monthly") {
      // Current month only — week-wise cumulative (W1, W1+W2, …)
      const now = new Date();
      const yr = now.getFullYear(), mo = now.getMonth();
      chartTitle2 = now.toLocaleDateString("en-IN", { month:"long", year:"numeric" });
      const monthHist = (hist || []).filter(d => {
        const dt = new Date(d.record_date);
        return dt.getFullYear() === yr && dt.getMonth() === mo;
      });
      const byWeek = {};
      for (const d of monthHist) {
        const dt = new Date(d.record_date);
        const wk = `W${Math.ceil(dt.getDate() / 7)}`;
        if (!byWeek[wk]) byWeek[wk] = { plan:0, actual:0 };
        byWeek[wk].plan   += Number(d.total_plan)   || 0;
        byWeek[wk].actual += Number(d.total_actual) || 0;
      }
      let cp = 0, ca = 0;
      for (const wk of Object.keys(byWeek).sort()) {
        cp += byWeek[wk].plan; ca += byWeek[wk].actual;
        labels.push(wk); planData.push(cp); barData.push(ca);
      }

    } else if (mode === "yearly") {
      // Selected financial year (Apr–Mar) — month-wise cumulative
      const fyStart = fyStartYear ?? curFyStart;
      chartTitle2 = `FY${String(fyStart).slice(-2)}-${String(fyStart+1).slice(-2)}`;
      const fyMonths = Array.from({ length:12 }, (_, i) => {
        const m = (3 + i) % 12;
        const y = m >= 3 ? fyStart : fyStart + 1;
        return { year:y, month:m, label: new Date(y, m, 1).toLocaleDateString("en-IN", { month:"short" }) };
      });
      const byMo = {};
      for (const d of (hist || [])) {
        const dt = new Date(d.record_date);
        const k  = `${dt.getFullYear()}-${dt.getMonth()}`;
        if (!byMo[k]) byMo[k] = { plan:0, actual:0 };
        byMo[k].plan   += Number(d.total_plan)   || 0;
        byMo[k].actual += Number(d.total_actual) || 0;
      }
      let cp = 0, ca = 0;
      for (const fm of fyMonths) {
        const k = `${fm.year}-${fm.month}`;
        if (!byMo[k]) continue;           // skip future months with no data
        cp += byMo[k].plan; ca += byMo[k].actual;
        labels.push(fm.label); planData.push(cp); barData.push(ca);
      }
    }

    const barColors  = barData.map((a,i) => a >= planData[i] ? `${STATUS_CLR["RUNNING"]}88` : `${STATUS_CLR["BREAKDOWN"]}88`);
    const barBorders = barData.map((a,i) => a >= planData[i] ? STATUS_CLR["RUNNING"] : STATUS_CLR["BREAKDOWN"]);

    chartInst.current = new window.Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [
          { type:"bar",  label:"Actual", data:barData,  backgroundColor:barColors,  borderColor:barBorders, borderWidth:1.5, borderRadius:3, order:2 },
          { type:"line", label:"Plan",   data:planData, borderColor:STATUS_CLR["SETUP"], borderWidth:2.5, borderDash:[6,4], pointRadius:3, pointBackgroundColor:STATUS_CLR["SETUP"], pointBorderColor:D?"#060912":"#fff", pointBorderWidth:1.5, tension:0.3, fill:false, order:1 },
        ],
      },
      options: {
        responsive:true, maintainAspectRatio:false, animation:{duration:400},
        interaction:{intersect:false, mode:"index"},
        layout:{ padding:{ top:18 } },
        plugins:{
          title:{
            display: true,
            text: mode === "weekly"  ? "Production  ·  Weekly"
                : mode === "monthly" ? `Production  ·  ${chartTitle2 || "Monthly"}`
                :                     `Production  ·  ${chartTitle2 || "Yearly"}`,
            color: text,
            font:{ size:13, weight:900, family:"'Segoe UI',sans-serif" },
            padding:{ top:4, bottom:8 },
            align:"start",
          },
          legend:{ position:"top", align:"end", labels:{ color:textSub, boxWidth:12, font:{size:11}, usePointStyle:true, padding:10 } },
          tooltip:{
            backgroundColor:D?"#0d1420":"#fff", titleColor:text, bodyColor:textSub, borderColor:border, borderWidth:1, padding:10, titleFont:{size:12}, bodyFont:{size:12},
            callbacks:{
              title: items => chartTitle2 ? `${items[0].label}  (${chartTitle2})` : items[0].label,
              label: c => {
                // Weekly bars are per-day totals; show running cumulative in tooltip
                if (mode === "weekly") {
                  const data = c.chart.data.datasets[c.datasetIndex].data;
                  const cumul = data.slice(0, c.dataIndex + 1).reduce((s, v) => s + (v || 0), 0);
                  return ` ${c.dataset.label}: ${cumul.toLocaleString()} pcs (cumul)`;
                }
                return ` ${c.dataset.label}: ${c.parsed.y.toLocaleString()} pcs`;
              },
            },
          },
        },
        scales:{
          y:{ beginAtZero:true, grid:{color:D?"rgba(255,255,255,0.04)":"rgba(0,0,0,0.04)",drawBorder:false}, border:{display:false}, ticks:{color:textSub,font:{size:11},callback:v=>fmtPcs(v),maxTicksLimit:6} },
          x:{ grid:{color:D?"rgba(255,255,255,0.04)":"rgba(0,0,0,0.04)",drawBorder:false}, border:{display:false}, ticks:{color:textSub,font:{size:11},maxRotation:40} },
        },
      },
      plugins: [topLabelPlugin],
    });
  };

  // ── CMS Chart — ECG-style spike graph ────────────────────────
  useChartJS(() => {
    if (chartMode !== "ct" || !cmsChartRef.current || !chartReady) return;

    // Use filtered dataset (slot/part/NG filters applied via useMemo above).
    // Fall back to live ctData when no DB rows exist yet.
    const hasFull = filteredCms.length > 0;
    const fullSorted = hasFull
      ? filteredCms
      : [...ctData]
          .reverse()
          .map((v, i) => v !== null ? { ct_value: v, cycle_seq: i + 1, ts: null } : null)
          .filter(Boolean);

    cmsSortedRef.current = fullSorted;
    // Shift just changed + no cycles yet → destroy the stale chart from
    // the previous shift so the graph area goes empty instead of keeping
    // the old spikes visible until new data comes in.
    if (fullSorted.length === 0) {
      if (cmsChartInst.current) {
        cmsChartInst.current.destroy();
        cmsChartInst.current = null;
      }
      return;
    }

    // Visible window: 30 cycles. Slider controls start index.
    const WINDOW   = 30;
    const maxStart = Math.max(0, fullSorted.length - WINDOW);
    const start    = cmsViewStart !== null ? Math.min(cmsViewStart, maxStart) : maxStart;
    const visSlice = fullSorted.slice(start, start + WINDOW);

    if (cmsChartInst.current) { cmsChartInst.current.destroy(); cmsChartInst.current = null; }

    const D2      = dark;
    const idealCT = line?.plc_config?.ideal_cycle_time || 15;
    const CT_TOL  = 0.009; // anything > ideal+0.009 = spike

    // ── Hourly slot dividers ─────────────────────────────────────
    // Shift slots from refs (avoids stale closure dep issues).
    // Sort by relative position within the shift so night shifts that wrap
    // past midnight (e.g. 18:30 → 03:15) order correctly: evening slots
    // first, then post-midnight slots.
    const _curShiftName = rtRef.current?.shift_name || "A";
    const _curShiftCfg  = (lineRef.current?.shifts || []).find(s => s.shift_name === _curShiftName);
    const _sStart = toMin(_curShiftCfg?.start_time || "08:30");
    const _shiftSlots = (lineRef.current?.hourly_slots || [])
      .filter(s => s.shift_name === _curShiftName)
      .sort((a,b) => {
        const aMin = toMin(a.start_time), bMin = toMin(b.start_time);
        const aRel = aMin >= _sStart ? aMin - _sStart : aMin + 1440 - _sStart;
        const bRel = bMin >= _sStart ? bMin - _sStart : bMin + 1440 - _sStart;
        return aRel - bRel;
      });

    const hourlySlotPlugin = {
      id: "cmsHourlySlots",
      afterDraw(chart) {
        const { ctx: c, scales } = chart;
        const xScale = scales.x, yScale = scales.y;
        let prevLabel = null, slotStartX = xScale.left, slotLabel = "";
        const drawDiv = (x, label, startX) => {
          c.save();
          c.strokeStyle = D2 ? "rgba(255,255,255,0.18)" : "rgba(0,0,0,0.12)";
          c.lineWidth = 1.5; c.setLineDash([4,3]);
          c.beginPath(); c.moveTo(x, yScale.top+18); c.lineTo(x, yScale.bottom); c.stroke();
          c.setLineDash([]);
          c.font = "800 9px 'Segoe UI',sans-serif";
          c.fillStyle = D2 ? "rgba(150,180,220,0.85)" : "rgba(40,70,130,0.75)";
          c.textAlign = "center";
          c.fillText(label, (startX+x)/2, yScale.top+11);
          c.restore();
        };
        visSlice.forEach((cy, i) => {
          if (!cy.ts) return;
          const x = xScale.getPixelForValue(i);
          const dt = new Date(cy.ts);
          const cyMin = dt.getHours()*60 + dt.getMinutes();
          // Find which slot this cycle falls in
          const slot = _shiftSlots.find(s => {
            const ssM = toMin(s.start_time), seM = toMin(s.end_time);
            return cyMin >= ssM && cyMin < seM;
          });
          const lbl = slot ? slot.slot_label : `${String(dt.getHours()).padStart(2,"0")}:00`;
          if (prevLabel === null) { prevLabel = lbl; slotLabel = lbl; }
          else if (lbl !== prevLabel) {
            drawDiv(x, slotLabel, slotStartX);
            slotStartX = x; slotLabel = lbl; prevLabel = lbl;
          }
        });
        if (visSlice.length > 0 && prevLabel) {
          c.save();
          c.font = "800 9px 'Segoe UI',sans-serif";
          c.fillStyle = D2 ? "rgba(150,180,220,0.85)" : "rgba(40,70,130,0.75)";
          c.textAlign = "center";
          c.fillText(slotLabel, (slotStartX+xScale.right)/2, yScale.top+11);
          c.restore();
        }
      },
    };

    // ── Cycle number label on every dot ─────────────────────────
    // 2026-05-18-r13 — In portrait mode the chart cell is smaller, so
    // labeling EVERY cycle dot creates an unreadable wall of "33.63s
    // 33.63s 33.63s …" stacked text (operator: "ye 33.63 kha se aa
    // rha h").  Now: portrait labels only spikes (>ideal) + every
    // 5th normal cycle.  Landscape behavior unchanged.
    const cycleLabelsPlugin = {
      id: "cycleLabels",
      afterDatasetsDraw(chart) {
        const { ctx: c } = chart;
        const meta = chart.getDatasetMeta(0);
        meta.data.forEach((pt, i) => {
          const cy = visSlice[i];
          if (!cy) return;
          const isNg = !!cy.is_ng;
          // Throttle labels in portrait — only NG + every 5th normal.
          // 2026-05-27 — Dropped the >Ideal-CT spike highlighting:
          // operator: "ng k liye jo ui tha vo sab shi kr".  OK cycles
          // are now always plain green text regardless of CT length.
          if (isPortrait && !isNg && i % 5 !== 0) return;
          c.save();
          c.font = `${isNg ? 700 : 500} 8px 'Segoe UI',sans-serif`;
          c.fillStyle = isNg
            ? STATUS_CLR["BREAKDOWN"]
            : D2 ? "rgba(100,200,130,0.7)" : "rgba(20,120,60,0.65)";
          c.textAlign = "center";
          c.textBaseline = "bottom";
          c.fillText(cy.ct_value + "s" + (isNg ? "  ⚠" : ""),
                     pt.x, pt.y - (isNg ? 7 : 4));
          c.restore();
        });
      },
    };

    // 2026-05-27 — Binary: OK = green (always), NG = red (always).
    // Operator: ">ideal ct wala bhi red dikha rha tha jo confusing tha,
    // sirf real NG hi red ho".  Earlier amber/red-on-OK was based on
    // CT vs ideal, which made long-but-OK cycles look like NG.  Now
    // colour purely tells you bit_type, CT goes in the label / tooltip.
    const ptColors = visSlice.map(cy =>
      cy.is_ng ? STATUS_CLR["BREAKDOWN"] : STATUS_CLR["RUNNING"]);
    // 2026-05-27 — Radius keyed on is_ng (not CT-vs-ideal) so a long
    // OK cycle stays the same size as a regular OK, and only NG dots
    // stand out larger.
    const ptRadius = visSlice.map(cy => cy.is_ng ? 5 : 3);
    const ptHoverR = visSlice.map(cy => cy.is_ng ? 9 : 6);

    // 2026-05-18-r7 — Y-AXIS HARD CAP at 40s.  Same fix that
    // WallboardLeft.jsx got: a single 60s+ outlier was forcing the
    // auto-scaled y-axis up to 70-80s, which squashed every normal
    // 12-25s cycle into a flat band at the bottom of the chart.
    // Operator complaint (Fullscreen 9:16): "SARI DOT DOT HI DIKH RHI
    // H".  Clamp the plotted values at 40s; tooltip + dot-click stay
    // on the REAL ct_value (so video URL + numeric readouts honor
    // the true cycle time).
    const Y_CAP   = 40;
    const ptBorder  = visSlice.map(cy => cy.ct_value > Y_CAP
                                       ? "#ffffff"
                                       : (D2 ? "#060912" : "#ffffff"));
    const ptBorderW = visSlice.map(cy => cy.ct_value > Y_CAP ? 2.5 : 1);
    const plotData  = visSlice.map(cy => cy.ct_value == null
                                       ? null
                                       : Math.min(cy.ct_value, Y_CAP));

    const ctx2 = cmsChartRef.current.getContext("2d");
    cmsChartInst.current = new window.Chart(ctx2, {
      type: "line",
      data: {
        labels: visSlice.map(cy => cy.cycle_seq),
        datasets: [
          {
            label: "Cycle Time",
            // 2026-05-18-r7 — plotData clamps at Y_CAP (40s) so the
            // chart stays readable when one cycle blows past 60s.
            data: plotData,
            borderWidth: 2,
            tension: 0,
            fill: false,
            spanGaps: false,
            pointBackgroundColor: ptColors,
            pointBorderColor: ptBorder,
            pointBorderWidth: ptBorderW,
            pointRadius: ptRadius,
            pointHoverRadius: ptHoverR,
            segment: {
              borderColor: seg => {
                const aCy = visSlice[seg.p0DataIndex];
                const bCy = visSlice[seg.p1DataIndex];
                const a = aCy?.ct_value;
                const b = bCy?.ct_value;
                if (a == null || b == null) return "transparent";
                // 2026-05-27 — Segment colouring also goes binary now
                // (operator: "OK ka kuch mt chedna, sirf NG red ho").
                // A segment touching ANY NG endpoint is red; otherwise
                // plain green regardless of CT vs ideal.  The CT-vs-
                // ideal gradient code below is now dead but kept around
                // (unreachable) for easy revert later.
                if (aCy?.is_ng || bCy?.is_ng) return `${STATUS_CLR["BREAKDOWN"]}cc`;
                return `${STATUS_CLR["RUNNING"]}cc`;
                // --- legacy CT-vs-ideal logic (unreachable) ---
                const aUp = a > idealCT + CT_TOL;
                const bUp = b > idealCT + CT_TOL;
                const aDn = a < idealCT - CT_TOL;
                const bDn = b < idealCT - CT_TOL;
                // Both clearly above → solid red
                if (aUp && bUp) return `${STATUS_CLR["BREAKDOWN"]}cc`;
                // Both clearly below → solid green
                if (aDn && bDn) return `${STATUS_CLR["RUNNING"]}cc`;
                // Both in amber zone
                if (!aUp && !aDn && !bUp && !bDn) return "rgba(251,191,36,0.85)";
                // Threshold crossing — draw gradient at the exact crossing pixel
                const c2 = cmsChartRef.current?.getContext("2d");
                if (!c2 || seg.p0.x === seg.p1.x) {
                  return aUp ? `${STATUS_CLR["BREAKDOWN"]}cc` : `${STATUS_CLR["RUNNING"]}cc`;
                }
                const ratio = Math.max(0.01, Math.min(0.99,
                  Math.abs(a - idealCT) / Math.abs(a - b)
                ));
                const grad = c2.createLinearGradient(seg.p0.x, 0, seg.p1.x, 0);
                if (aUp) {
                  // falling from above → red ➜ amber ➜ green
                  grad.addColorStop(0,                           `${STATUS_CLR["BREAKDOWN"]}cc`);
                  grad.addColorStop(Math.max(0,   ratio - 0.04),`${STATUS_CLR["BREAKDOWN"]}cc`);
                  grad.addColorStop(ratio,                        "rgba(251,191,36,0.85)");
                  grad.addColorStop(Math.min(1,   ratio + 0.04),`${STATUS_CLR["RUNNING"]}cc`);
                  grad.addColorStop(1,                            `${STATUS_CLR["RUNNING"]}cc`);
                } else {
                  // rising from below → green ➜ amber ➜ red
                  grad.addColorStop(0,                            `${STATUS_CLR["RUNNING"]}cc`);
                  grad.addColorStop(Math.max(0,   ratio - 0.04),`${STATUS_CLR["RUNNING"]}cc`);
                  grad.addColorStop(ratio,                        "rgba(251,191,36,0.85)");
                  grad.addColorStop(Math.min(1,   ratio + 0.04),`${STATUS_CLR["BREAKDOWN"]}cc`);
                  grad.addColorStop(1,                            `${STATUS_CLR["BREAKDOWN"]}cc`);
                }
                return grad;
              },
            },
          },
          {
            type: "line",
            label: `Ideal ${idealCT}s`,
            data: Array(visSlice.length).fill(idealCT),
            borderColor: "rgba(251,191,36,0.7)",
            borderWidth: 1.5,
            borderDash: [5, 4],
            pointRadius: 0,
            fill: false,
            tension: 0,
          },
          // Takt = customer demand rhythm.  Surfaced when rt.takt_seconds
          // is provided AND differs from ideal by >0.1s.  Helps the
          // supervisor see whether the machine is running TO TAKT (not
          // just to its mechanical capability).
          // 2026-05-18-r13 — Suppressed in portrait so the small CT
          // cell doesn't carry an extra 33.63s horizontal line that
          // operators mistake for cycle data.  Takt info is still
          // shown in the Takt Time card to the right of the gauges.
          ...(!isPortrait && rt?.takt_seconds && Math.abs(rt.takt_seconds - idealCT) > 0.1 ? [{
            type: "line",
            label: `Takt ${Number(rt.takt_seconds).toFixed(2)}s`,
            data: Array(visSlice.length).fill(Number(rt.takt_seconds)),
            borderColor: "rgba(96,165,250,0.85)",   // cyan-blue
            borderWidth: 2,
            borderDash: [10, 4],
            pointRadius: 0,
            fill: false,
            tension: 0,
          }] : []),
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        layout: { padding: { top: 30, bottom: 2 } },
        // 2026-05-18-r12 — `intersect:false, mode:"index", axis:"x"`
        // so a click ANYWHERE along an X-column opens that cycle's
        // video.  Same fix WallboardLeft.jsx got — pixel-perfect dot
        // hit was almost impossible on a 65" wallboard, and now-also
        // on the smaller portrait-quad CT cell.  Operator complaint:
        // "video kyu nhi aati h baar same issue h".
        interaction: { intersect: false, mode: "index", axis: "x" },
        onClick: (evt, _els, chart) => {
          // Two-path hit detection:
          //   1. Chart.js `index` mode — finds the cycle index closest
          //      to the click's X position (forgiving on small dots).
          //   2. Manual xScale.getValueForPixel fallback for dead zones
          //      between bars or outside the dataset's interaction area.
          let idx = -1;
          const els = chart.getElementsAtEventForMode(
            evt.native, "index", { intersect: false, axis: "x" }, false);
          const hit = els && els.find ? els.find(e => e.datasetIndex === 0) : null;
          if (hit) {
            idx = hit.index;
          } else if (evt.native && chart.scales?.x) {
            const rect = chart.canvas.getBoundingClientRect();
            const px = evt.native.clientX != null
              ? evt.native.clientX - rect.left
              : (evt.native.offsetX || 0);
            const v = chart.scales.x.getValueForPixel(px);
            if (v != null) idx = Math.round(v);
          }
          if (idx < 0 || idx >= visSlice.length) return;
          const cy = visSlice[idx];
          if (!cy) return;
          // Fetch video from Deep backend; it looks up part_code in _ct_log
          // and proxies to the New-folder-2 camera server.
          const recDate = rtRef.current?.record_date || new Date().toISOString().slice(0,10);
          const shiftNm = rtRef.current?.shift_name || "";
          const qs = `date=${recDate}&shift=${encodeURIComponent(shiftNm)}&cycle_seq=${cy.cycle_seq}`;
          // 2026-05-27 — Always use cycle_seq time-window endpoint, not
          // by-part MP4.  Same operator complaint as above: when many
          // cycles share one part_code (scanner stuck), by-part returns
          // the same file for every click.  Time-window slices unique
          // clips per cycle from the rolling TS using each cycle's ts.
          const videoSrc =
            `/api/lines/${lineId}/cycle-video?${qs}`
            + `&t=${cy.cycle_seq || Date.now()}`
            + `&token=${encodeURIComponent(sessionStorage.getItem("mes_token")||"")}`;
          setVideoModal({ ...cy, loading: false, video_url: videoSrc });
          setShowModalUI(!pipActive);
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            // 2026-05-27 — Per-cycle tooltip styling.  Operator: NG
            // cycles ka hover box bhi red ho, OK ka mat chedna.  We
            // dynamically swap `backgroundColor` + `borderColor` based
            // on whichever cycle the cursor is over.
            backgroundColor: ctx => {
              const idx = ctx.tooltip?.dataPoints?.[0]?.dataIndex;
              const cy  = idx != null ? visSlice[idx] : null;
              if (cy?.is_ng) return D2 ? "#3a0d10" : "#fee2e2";
              return D2 ? "#0d1420" : "#fff";
            },
            titleColor: ctx => {
              const idx = ctx.tooltip?.dataPoints?.[0]?.dataIndex;
              const cy  = idx != null ? visSlice[idx] : null;
              if (cy?.is_ng) return "#fecaca";
              return D2 ? "#e8edf5" : "#0f172a";
            },
            bodyColor:  ctx => {
              const idx = ctx.tooltip?.dataPoints?.[0]?.dataIndex;
              const cy  = idx != null ? visSlice[idx] : null;
              if (cy?.is_ng) return "#fecaca";
              return D2 ? "#8092af" : "#374151";
            },
            borderColor: ctx => {
              const idx = ctx.tooltip?.dataPoints?.[0]?.dataIndex;
              const cy  = idx != null ? visSlice[idx] : null;
              if (cy?.is_ng) return "#ef4444";
              return D2 ? "#141e2e" : "#b8c8dc";
            },
            borderWidth: 1, padding: 10,
            callbacks: {
              title: items => `Cycle #${items[0].label}`,
              label: c => {
                if (c.datasetIndex === 1) return ` Ideal: ${idealCT}s`;
                // 2026-05-18-r7 — Tooltip always shows the REAL ct
                // from visSlice, not the clamped `c.parsed.y` — so a
                // 312s outlier reads as 312s here (with a clamp
                // note) instead of silently appearing as 40s.
                const cy   = visSlice[c.dataIndex];
                const real = cy?.ct_value;
                if (real == null) return " —";
                const t  = cy?.ts ? new Date(cy.ts).toLocaleTimeString("en-IN",
                  { hour:"2-digit", minute:"2-digit", second:"2-digit" }) : "";
                // 2026-05-27 — Pure binary tag now.  Operator: ">ideal
                // wala bhi red dikha tha confusing".  OK = always "OK ✓"
                // regardless of CT vs ideal; NG = always "⚠ NG".  Slow
                // cycles still show actual CT in the tooltip (e.g.
                // "CT: 15.59s") so the data is visible — just no false
                // alarm via the status text.
                const msg = cy?.is_ng ? "⚠ NG" : "✓ OK";
                const tag = real > Y_CAP ? `  (>${Y_CAP}s clamped)` : "";
                const out = [` CT: ${real}s${tag}  ${msg}`];
                if (cy?.part_code) out.push(` Part: ${cy.part_code}`);
                if (t) out.push(` Time: ${t}`);
                out.push(` 🎥 Click for video`);
                return out;
              },
            },
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            // 2026-05-27 — Operator: "graph 0 se 40s kr de bs".
            // Earlier the chart pinned at [idealCT-8, 40] which made
            // the floor wander with model (7-15s).  Fixed at 0-40 now
            // so post-NG OK rows that drop to ~5s remain visible and
            // dot positions stay comparable across models.
            min: 0,
            max: Y_CAP,
            grid:   { color: D2 ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.04)", drawBorder: false },
            border: { display: false },
            ticks:  { color: D2 ? "#8092af" : "#374151", font: { size: 11 }, callback: v => `${v}s`, maxTicksLimit: 7 },
          },
          x: {
            grid:   { display: false },
            border: { display: false },
            ticks:  { display: false },
          },
        },
        // 2026-05-18-r12 — Cursor pointer canvas-wide.  With index-mode
        // click detection, ANY click on a cycle column opens the
        // video — operator shouldn't have to guess where exactly to
        // tap, so the whole chart area shows the clickable cursor.
        onHover: (evt) => {
          if (evt?.native?.target) evt.native.target.style.cursor = "pointer";
        },
      },
      plugins: [hourlySlotPlugin, cycleLabelsPlugin],
    });
    // 2026-05-18-r9 — `isPortrait` added so the chart instance is
    // destroyed + rebuilt when the operator toggles orientation.
    // The cmsChartRef canvas lives in DIFFERENT DOM positions for
    // landscape (inside the toggle-chart card) vs portrait (inside
    // the quad panel's top-left cell) — Chart.js gets confused if
    // we don't rebuild on the swap.
  }, [dark, chartMode, filteredCms, chartReady, cmsViewStart, isPortrait]); // eslint-disable-line

  // ── Live rt update for fallback (when ct_log DB has no data yet) ─
  useEffect(() => {
    if (chartMode !== "ct" || !cmsChartInst.current || cmsData.length > 0 || !rt) return;
    const idealCT = line?.plc_config?.ideal_cycle_time || 15;
    const vals = [...ctData].reverse().filter(v => v !== null);
    if (!vals.length) return;
    const CT_TOL2 = 0.009;
    // 2026-05-18-r7 — Clamp the fallback live values at the same 40s
    // cap used for the main chart, otherwise an outlier coming
    // through the rt path would still blow up the y-axis.
    const Y_CAP2 = 40;
    cmsChartInst.current.data.datasets[0].data =
      vals.map(v => v == null ? null : Math.min(v, Y_CAP2));
    cmsChartInst.current.data.datasets[0].pointBackgroundColor =
      vals.map(v => v > idealCT + CT_TOL2 ? STATUS_CLR["BREAKDOWN"]
                  : v < idealCT - CT_TOL2 ? STATUS_CLR["RUNNING"] : "#f59e0b");
    cmsChartInst.current.data.datasets[0].pointRadius =
      vals.map(v => v > idealCT + CT_TOL2 ? 5 : 3);
    cmsChartInst.current.data.datasets[0].pointBorderColor =
      vals.map(v => v > Y_CAP2 ? "#ffffff" : (dark ? "#060912" : "#ffffff"));
    cmsChartInst.current.data.datasets[0].pointBorderWidth =
      vals.map(v => v > Y_CAP2 ? 2.5 : 1);
    cmsChartInst.current.data.datasets[1].data = Array(vals.length).fill(idealCT);
    cmsChartInst.current.update("none");
  }, [rt]); // eslint-disable-line

  const [isFS, setIsFS] = useState(!!document.fullscreenElement);
  useEffect(() => {
    const onChange = () => setIsFS(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleFS = () => {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen();
    else document.exitFullscreen();
  };

  // Video modal "expanded" mode. CSS-only — the modal fills the whole viewport.
  // We deliberately do NOT use the video element's native fullscreen API,
  // because exiting native fullscreen via Esc also drops the outer dashboard
  // fullscreen (Chrome behaviour). With Keyboard Lock below we can instead
  // intercept Esc ourselves and collapse just the video.
  const [videoExpanded, setVideoExpanded] = useState(false);

  // Keyboard Lock: when the dashboard is in browser fullscreen we need to
  // capture Esc so we can decide what it should do (close video? collapse
  // expanded video? exit dashboard fullscreen?) instead of the browser
  // unconditionally exiting fullscreen. Chrome/Edge support this; other
  // browsers silently fall back to default behaviour.
  useEffect(() => {
    if (!isFS) return;
    let locked = false;
    const kb = navigator.keyboard;
    if (kb && typeof kb.lock === "function") {
      kb.lock(["Escape"]).then(() => { locked = true; }).catch(() => {});
    }
    return () => {
      if (locked && kb && typeof kb.unlock === "function") {
        try { kb.unlock(); } catch {}
      }
    };
  }, [isFS]);

  // Global Esc handler — priority order:
  //   1. If video is expanded → collapse it (dashboard FS stays).
  //   2. Else if video modal is open → close the modal (dashboard FS stays).
  //   3. Else → let the browser handle it (exits dashboard fullscreen normally).
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      if (videoExpanded) {
        e.preventDefault();
        e.stopPropagation();
        setVideoExpanded(false);
        return;
      }
      // Dependency list below must include pipActive for fresh reads.
      if (videoModal) {
        e.preventDefault();
        e.stopPropagation();
        if (pipActive) {
          // PiP is playing — just hide the modal chrome, keep video mounted
          setShowModalUI(false);
        } else {
          if (videoModal.video_url) {
            /* blob cleanup no longer needed — video uses streaming URL */
          }
          setVideoModal(null);
          setShowModalUI(false);
        }
        return;
      }
      // otherwise: let Esc fall through and exit dashboard FS (default)
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [videoExpanded, videoModal, pipActive]);

  // If the modal closes via any path, don't leave expanded mode stuck on.
  useEffect(() => {
    if (!videoModal && videoExpanded) setVideoExpanded(false);
  }, [videoModal]); // eslint-disable-line

  if (!rt || !line) return (
    <div style={{height:"100vh",display:"flex",alignItems:"center",justifyContent:"center",
      background:D?"#060912":"#f0f4f8",flexDirection:"column",gap:12}}>
      <div style={{width:40,height:40,borderRadius:"50%",
        border:`4px solid ${D?"#141e2e":"#e2e8f0"}`,
        borderTopColor:STATUS_CLR["QUALITY ISSUE"],
        animation:"spin .7s linear infinite"}}/>
      <span style={{fontSize:14,fontWeight:600,color:"#8092af",letterSpacing:".1em"}}>CONNECTING…</span>
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
    </div>
  );

  // ── Derived values ────────────────────────────────────────────
  // During a gap-shift phase, display the frozen previous-shift snapshot
  // so the screen doesn't blank out between shifts.
  const displayRt     = (gapShift && frozenRtRef.current) ? frozenRtRef.current : rt;
  const okTotal       = displayRt.ok_count || 0;
  const ngTotal       = displayRt.ng_count || 0;
  const actual        = okTotal + ngTotal;
  const oee           = parseFloat(displayRt.overall_oee  || 0);
  const avail         = parseFloat(displayRt.availability || 0);
  const perf          = parseFloat(displayRt.performance  || 0);
  const qual          = parseFloat(displayRt.quality_oee  || 0);
  // 2026-05-18-r12 — Outlier-filtered shift average CT.
  // Operator spec: "33.43 ct aa rha h breakdown chal rha h to bhi" —
  // when a breakdown happens MID-CYCLE the L108 edge for that cycle
  // fires after the resume, so its ct_value reads (real-active-time
  // + breakdown-pause-time).  A single such cycle of 200s polluted
  // the shift average even though all other cycles were ~15s.
  //
  // Two-layer guard, mirroring the chart's Y_CAP=40s clamp:
  //   1. AbsoluteCap : drop any cycle > 40s (== the chart-clamp limit).
  //                    Anything beyond is almost certainly machine-
  //                    pause padding, not real cycle time.
  //   2. MedianFilter: of what remains, drop cycles > 2.5× the median.
  //                    Catches the "ladder bug → 60s every now and
  //                    then" pattern where 40s cap isn't enough.
  // Both filters report `excluded` count back for the operator info
  // chip rendered in the Cycle Time card body.
  const ctStats = (() => {
    if (cmsData.length === 0) {
      const actual = (displayRt.ok_count || 0) + (displayRt.ng_count || 0);
      return {
        avg: actual > 0 ? parseFloat(displayRt.ct_avg_20 || 0) : 0,
        used: 0, excluded: 0,
      };
    }
    const Y_CAP_AVG = 40;
    const raw = cmsData.map(cy => cy.ct_value).filter(v => v > 0);
    const capped = raw.filter(v => v <= Y_CAP_AVG);
    // Median of the capped subset for the 2.5× outlier guard.
    let median = 0;
    if (capped.length > 0) {
      const sorted = [...capped].sort((a,b) => a-b);
      const mid = Math.floor(sorted.length / 2);
      median = sorted.length % 2 === 0
        ? (sorted[mid-1] + sorted[mid]) / 2
        : sorted[mid];
    }
    const filtered = median > 0
      ? capped.filter(v => v <= median * 2.5)
      : capped;
    const used = filtered.length;
    const avg  = used > 0 ? filtered.reduce((a,b) => a+b, 0) / used : 0;
    return { avg, used, excluded: raw.length - used };
  })();
  const avgCT = ctStats.avg;
  const shift         = displayRt.shift_name    || "A";
  const otActiveShift = rt.ot_active_shift      || null;
  const isOtActive    = otActiveShift === shift;

  // Status: during gap show "GAP SHIFT A→B", otherwise live status
  const status      = gapShift ? `GAP SHIFT ${gapShift}` : (rt.operating_status || "IDLE");
  const statusColor = gapShift ? STATUS_CLR["IDLE"] : getStatusColor(rt.operating_status || "IDLE");

  const LOSSES = [
    { key:"breakdown",   label:"Breakdown",   color:STATUS_CLR["BREAKDOWN"]    },
    { key:"quality",     label:"Quality",     color:STATUS_CLR["QUALITY ISSUE"] },
    { key:"material",    label:"Material",    color:STATUS_CLR["MATERIAL WAIT"] },
    { key:"setup",       label:"Setup",       color:STATUS_CLR["SETUP"]         },
    { key:"change_over", label:"Change Over", color:STATUS_CLR["CHANGE OVER"]   },
    { key:"speed",       label:"Speed Loss",  color:"#06b6d4"                   },
    { key:"others",      label:"Others",      color:STATUS_CLR["OTHERS"]        },
  ];
  let totalLoss = 0;
  const lossData = LOSSES.map(c => {
    const sec = displayRt[`loss_${c.key}_seconds`] || 0;
    totalLoss += sec;
    return { ...c, sec };
  });

  const shiftCfg = (line.shifts || []).find(s => s.shift_name === shift);
  const sStart   = toMin(shiftCfg?.start_time || "08:30");
  const sEnd     = toMin(shiftCfg?.end_time   || "17:15");
  let   sDur     = shiftCfg?.crosses_midnight ? (sEnd+1440-sStart) : (sEnd-sStart);
  // ── OT extension: if OT is active for this shift and configured, extend
  // the timeline/plan horizon to the OT end time so the bar shows the OT
  // window and hourly table includes the OT slot.
  const otActive = (rt.ot_active_shift === shift);
  if (otActive && shiftCfg?.ot_end_time) {
    const otEnd = toMin(shiftCfg.ot_end_time);
    // Distance from shift start to OT end (handle midnight wrap)
    let otDur = otEnd - sStart;
    if (otDur <= 0) otDur += 1440;
    if (otDur > sDur) sDur = otDur;
  }
  const nowMin   = now.getHours()*60 + now.getMinutes();
  const nowSec   = now.getSeconds();
  // Fractional current minute-of-day (for progress marker precision)
  const nowMinFrac = now.getHours()*60 + now.getMinutes() + now.getSeconds()/60;
  const sElapsed   = Math.max(0, nowMinFrac>=sStart ? nowMinFrac-sStart : nowMinFrac+1440-sStart);
  const tlPct      = Math.min(100, (sElapsed/sDur)*100);

  const allSlots = (line.hourly_slots || [])
    .filter(s => s.shift_name === shift)
    .sort((a,b) => {
      // Sort by position within the shift, not by clock-time string.
      // A night shift like 18:30→03:15 wraps past midnight, so slots whose
      // start_time is numerically smaller than sStart (e.g. 00:30, 01:30)
      // actually come AFTER the evening slots — add 24h when wrapped.
      const aMin = toMin(a.start_time);
      const bMin = toMin(b.start_time);
      const aRel = aMin >= sStart ? aMin - sStart : aMin + 1440 - sStart;
      const bRel = bMin >= sStart ? bMin - sStart : bMin + 1440 - sStart;
      return aRel - bRel;
    });

  // ── Slot data — 100% collector-sourced ───────────────────────
  // The collector's _realtime_slot_plan() already:
  //   • increments every ideal-CT second
  //   • subtracts break / setup seconds (no phantom pieces during setup)
  //   • caps at the static slot target
  //   • writes to ${p}_plan every 2 s
  // So we just read what the collector wrote — no frontend arithmetic.
  // First pass — compute geometry + raw collector plan for every slot
  const _rawSlots = allSlots.map(s => {
    const p        = s.db_column_prefix;
    const ssMin    = toMin(s.start_time);
    const seMin    = toMin(s.end_time);
    const sdur     = seMin > ssMin ? seMin-ssMin : seMin+1440-ssMin;
    const ssRel    = ssMin >= sStart ? ssMin-sStart : ssMin+1440-sStart;
    const seRel    = ssRel + sdur;
    const isFuture  = sElapsed < ssRel;
    const isCurrent = sElapsed >= ssRel && sElapsed < seRel;
    const planDB    = isFuture ? 0 : (displayRt[`${p}_plan`]   ?? 0);
    const okDB      = isFuture ? 0 : (displayRt[`${p}_ok`]     ?? 0);
    const ngDB      = isFuture ? 0 : (displayRt[`${p}_ng`]     ?? 0);
    const actualDB  = isFuture ? 0 : (displayRt[`${p}_actual`] ?? (okDB + ngDB));
    return { label:s.slot_label, planDB, actualDB, okDB, ngDB, isFuture, isCurrent, p };
  });

  // Past slots' plans are complete — subtract them from shift_plan_completed
  // to get real-time plan for the current (in-progress) slot.
  const pastPlanSum = _rawSlots
    .filter(s => !s.isFuture && !s.isCurrent)
    .reduce((acc, s) => acc + s.planDB, 0);

  const _baseSlotData = _rawSlots.map(s => {
    const planFinal = s.isCurrent
      ? Math.max(0, (displayRt.shift_plan_completed || 0) - pastPlanSum)
      : s.planDB;
    return { label:s.label, plan:planFinal, planDB:planFinal, actualDB:s.actualDB,
             actual:s.actualDB, okDB:s.okDB, ngDB:s.ngDB, variance:s.actualDB - planFinal,
             isFuture:s.isFuture, isCurrent:s.isCurrent, p:s.p };
  });

  // ── Synthetic OT slot ────────────────────────────────────────
  // 2026-05-13 — operator spec:
  //   "OT on krte hi dashboard prr ek slot add bhi hona chaiye uska
  //    plan zero hona chaiye actual kaa data collector se aana chaiye"
  //
  // When OT is active for THIS shift AND ot_end_time is configured,
  // append an OT-window row to the hourly table.  Logic:
  //   plan   = 0  (no shift target carries into OT)
  //   ok/ng  = total - sum(regular slots)  (cycles produced after the
  //           last regular slot's end fall outside every slot window
  //           but are still counted in rt.ok_count / rt.ng_count)
  //   actual = ok + ng
  //   variance = actual (since plan is 0)
  // Time geometry follows the same minute-of-day relative-frame used
  // by every other slot.  isCurrent / isFuture wired so the "▶ NOW"
  // indicator moves into the OT slot once we cross shift_end.
  const slotData = (() => {
    const out = [..._baseSlotData];
    if (!otActive || !shiftCfg?.ot_end_time) return out;

    const sEndMin   = sEnd;
    const otEndMin  = toMin(shiftCfg.ot_end_time);
    // Distance from shift_end to ot_end_time (handle midnight wrap)
    let otDur = otEndMin - sEndMin;
    if (otDur <= 0) otDur += 1440;
    const sEndRel   = sEndMin >= sStart ? sEndMin - sStart : sEndMin + 1440 - sStart;
    const otEndRel  = sEndRel + otDur;
    const isFuture  = sElapsed < sEndRel;
    const isCurrent = sElapsed >= sEndRel && sElapsed < otEndRel;

    // OT actual = total - sum of regular slots' actuals
    // The collector still counts OK/NG into rt.ok_count / rt.ng_count
    // during the OT window, but those cycles don't land in any configured
    // hourly slot (none exist past shift_end), so the leftover IS the
    // OT count.
    const regularOk     = out.reduce((acc, s) => acc + (s.okDB || 0), 0);
    const regularNg     = out.reduce((acc, s) => acc + (s.ngDB || 0), 0);
    const regularActual = out.reduce((acc, s) => acc + (s.actualDB || 0), 0);
    const totalOk       = displayRt.ok_count || 0;
    const totalNg       = displayRt.ng_count || 0;
    const totalActual   = totalOk + totalNg;
    const otOk     = Math.max(0, totalOk - regularOk);
    const otNg     = Math.max(0, totalNg - regularNg);
    const otActual = Math.max(0, totalActual - regularActual);

    const fmtHM = (m) => {
      const mod = ((m % 1440) + 1440) % 1440;
      return `${String(Math.floor(mod/60)).padStart(2,'0')}:${String(mod%60).padStart(2,'0')}`;
    };

    out.push({
      label:    `OT ${fmtHM(sEndMin)}-${fmtHM(otEndMin)}`,
      plan:     0,
      planDB:   0,
      actual:   otActual,
      actualDB: otActual,
      okDB:     otOk,
      ngDB:     otNg,
      variance: otActual,        // plan == 0 → variance is the actual
      isFuture,
      isCurrent,
      p:        "ot_synth",
    });
    return out;
  })();

  // Total plan and actual — both straight from collector
  const tPlan   = displayRt.shift_plan_completed || 0;
  const tActual = actual;
  const tVar    = tActual - tPlan;
  const progress = tPlan ? Math.min(100, (tActual/tPlan)*100) : 0;

  const timeStr = now.toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false});

  const chartTitle = (() => {
    if (chartMode === "yearly") return `FY${String(selectedFY).slice(-2)}-${String(selectedFY+1).slice(-2)}`;
    if (chartMode === "monthly") return now.toLocaleDateString("en-IN",{month:"long",year:"numeric"});
    return "";
  })();

  const card = (e={}) => ({ background:bgCard, borderRadius:10, border:`1px solid ${border}`, overflow:"hidden", ...e });

  // 2026-05-18-r8 — Floating-modal positioning helper.
  // Modals (Loss / PY / Sensor) follow the dashboard's ROTATION
  // state, not its isPortrait state.  If the dashboard itself isn't
  // CSS-rotated (i.e. layout matches viewport), the modal opens with
  // plain `inset:0` so it covers the natural viewport.  If the
  // dashboard is rotated (mismatch case), the modal rotates 90°
  // around top-left to land in the same rotated frame.  This keeps
  // the modal text upright relative to the operator's eyeline
  // regardless of which combination of viewport + manual override is
  // in play.
  // 2026-05-19 — REVERTED on operator request.  Previous rev tried to
  // match the dashboard orientation (portrait popup when portrait
  // dashboard).  Reverted back to ALWAYS-landscape: the popup always
  // covers the natural viewport with plain `inset:0`, regardless of
  // dashboard rotation state.  Combined with the React-Portal escape
  // hatch (renders at document.body, outside the rotated dashboard's
  // transform-ancestor), this guarantees popups are upright relative
  // to the physical monitor — never inheriting the dashboard's CSS
  // rotation.  Operator quote: "VIDEO STILL ULTI HI RENDER HO RHI HAI".
  const overlayPosStyle = {
    position: "fixed",
    inset: 0,
  };
  const hdr  = (e={}) => ({ padding:"6px 12px", background:bgDeep, borderBottom:`1px solid ${border}`, display:"flex", alignItems:"center", justifyContent:"space-between", ...e });
  const lbl9 = { fontSize:11, fontWeight:800, color:textSub, letterSpacing:".1em", textTransform:"uppercase" };

  // ── Build timeline segments from status log ───────────────────
  // Carry-over logic: a status that started BEFORE this slot continues
  // painting into it until the next recorded transition.
  // Slots that are fully in the future are never called.
  // Unknown periods (no data before or within slot) are left empty.
  function buildSlotSegments(ssRel, seRel, sdur) {
    // Convert every log entry to its relative-minute position within the shift.
    // Filter to the current display shift so cross-shift data never bleeds in.
    const allEntries = statusLogRef.current
      .filter(e => e.shift === shift)
      .map(e => ({
        status:  e.status,
        relFrac: e.nowMinFrac >= sStart
          ? e.nowMinFrac - sStart
          : e.nowMinFrac + 1440 - sStart,
      }))
      .sort((a, b) => a.relFrac - b.relFrac);

    if (allEntries.length === 0) return null;

    // Carry-over: the LAST entry whose relFrac ≤ ssRel.
    // This is the status that was active when this slot began.
    let carryOver = null;
    for (let i = allEntries.length - 1; i >= 0; i--) {
      if (allEntries[i].relFrac <= ssRel) { carryOver = allEntries[i]; break; }
    }

    // Status transitions that start strictly within this slot
    const inSlot = allEntries.filter(e => e.relFrac > ssRel && e.relFrac < seRel);

    // No data at all for or before this slot → unknown, leave empty
    if (!carryOver && inSlot.length === 0) return null;

    // Build the ordered sequence covering [ssRel, seRel]
    let sequence = [];
    if (carryOver) sequence.push({ status: carryOver.status, relFrac: ssRel });
    sequence.push(...inSlot);

    // Collapse adjacent same-status entries — the collector writes periodic
    // status heartbeats, so a continuous 30-minute RUN would otherwise show
    // up as multiple hover fragments (5m, 10m, 15m…). Keep only the
    // transitions where status actually changes.
    sequence = sequence.filter((e, i) =>
      i === 0 || e.status !== sequence[i - 1].status
    );

    // For the current (live) slot don't paint beyond sElapsed;
    // for past slots fill all the way to the slot end.
    const slotIsPast = seRel <= sElapsed;
    const endCap     = slotIsPast ? seRel : Math.min(seRel, sElapsed);

    const segs = [];
    for (let i = 0; i < sequence.length; i++) {
      const e       = sequence[i];
      const nextRel = i < sequence.length - 1 ? sequence[i + 1].relFrac : endCap;

      const startRel = Math.max(e.relFrac, ssRel);
      const endRel   = Math.min(nextRel,   endCap);
      if (endRel <= startRel) continue;

      const startPct = ((startRel - ssRel) / sdur) * 100;
      const widthPct = ((endRel  - startRel) / sdur) * 100;
      if (widthPct <= 0) continue;

      // Convert relative minutes back to clock time for hover tooltip.
      // Earlier this stripped seconds, so a segment that ran 08:30:43→
      // 08:35:00 (4m 17s) showed as "08:30 – 08:35" — looking like 5 min
      // and contradicting the 4m 17s duration shown alongside.  Now we
      // include seconds so the math the user reads matches what they see.
      const toClk = (relMin) => {
        // Total seconds since midnight, wrapped to 24 h.
        const absSec = Math.round(((sStart + relMin) * 60)) % 86400;
        const hh = String(Math.floor(absSec / 3600)).padStart(2, "0");
        const mm = String(Math.floor((absSec % 3600) / 60)).padStart(2, "0");
        const ss = String(absSec % 60).padStart(2, "0");
        return `${hh}:${mm}:${ss}`;
      };
      const totalSec = Math.round((endRel - startRel) * 60);
      const durMin   = Math.floor(totalSec / 60);
      const durSec   = totalSec % 60;
      segs.push({
        startPct: Math.max(0, startPct),
        widthPct: Math.min(widthPct, 100 - Math.max(0, startPct)),
        color:    getStatusColor(e.status),
        status:   e.status,
        tooltip:  `${e.status}  ${toClk(startRel)} – ${toClk(endRel)}  (${durMin}m ${durSec}s)`,
      });
    }

    return segs.length > 0 ? segs : null;
  }

  return (
    <>
      <style>{`
        *{box-sizing:border-box;margin:0;padding:0;}
        body{overflow:hidden;background:${bg};font-family:'Segoe UI',system-ui,sans-serif;}
        ::-webkit-scrollbar{width:6px;height:6px;}
        ::-webkit-scrollbar-thumb{background:${D?"#1e293b":"#cbd5e1"};border-radius:4px;}
        @keyframes spin{to{transform:rotate(360deg)}}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
        @keyframes blink{0%,100%{opacity:1}50%{opacity:.1}}
        @keyframes tlFadeIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
        video::-webkit-media-controls-fullscreen-button{display:none!important;}
        video::-webkit-media-controls-overflow-button{display:none!important;}
      `}</style>

      {/* ── ORIENTATION ESCAPE HATCH (removed 2026-05-18) ──
          Earlier this rendered a floating circular toggle pinned at
          top:12/right:12 outside the rotated container.  Goal was to
          stay upright when portrait so operator could un-rotate.
          But it overlapped the existing fullscreen [⛶] button in the
          header (same corner), and the header's own 16:9/9:16 toggle
          was duplicated.  Reverted — the in-header toggle plus the
          `R` keyboard shortcut (registered below) cover both cases
          without UI clutter.  See git blame for full original block. */}

      {/* ── GAP-SHIFT BANNER ── */}
      {gapShift && (
        <div style={{
          position:"fixed", top:0, left:0, right:0, zIndex:999,
          background:"linear-gradient(90deg,#1e3a5f,#0f2744)",
          borderBottom:"2px solid #3b82f6",
          display:"flex", alignItems:"center", justifyContent:"center", gap:16,
          padding:"6px 20px",
          animation:"pulse 2s infinite",
        }}>
          <div style={{width:8,height:8,borderRadius:"50%",background:"#60a5fa",flexShrink:0}}/>
          <span style={{fontSize:13,fontWeight:900,color:"#93c5fd",letterSpacing:".15em"}}>
            SHIFT CHANGE · {gapShift}
          </span>
          <span style={{fontSize:11,color:"#64748b"}}>·</span>
          <span style={{fontSize:11,color:"#7dd3fc"}}>
            Previous shift data frozen — waiting for next shift production to start
          </span>
          <div style={{width:8,height:8,borderRadius:"50%",background:"#60a5fa",flexShrink:0}}/>
        </div>
      )}

      <div style={{
        // Layout stays IDENTICAL in portrait — we just CSS-rotate the
        // whole tree 90° clockwise (see the .fs-rotated wrapper above
        // these grid styles).  This way nothing reflows / overlaps —
        // a vertically-mounted screen sees the same dashboard, just
        // physically rotated.
        // 2026-05-18-r8 — dimensions follow `needsRotation`, not
        // `isPortrait`.  When the layout already matches the viewport
        // (e.g. portrait grid on a portrait browser), we want the grid
        // to fill the viewport NATIVELY — height=100vh, width=100vw —
        // not be rotated.  Rotation case still swaps the two values so
        // the 90°-rotated grid lands inside the viewport box.
        height:  needsRotation ? "100vw" : "100vh",
        width:   needsRotation ? "100vh" : "100%",
        display:"grid",
        // 2026-05-18 — operator-drawn 9:16 spec:
        //   row 1 (title)  : full width
        //   row 2 (KPI area): col1 = KPI gauges + Prod/CT/Takt left-aligned,
        //                      col2 = Loss Distribution center,
        //                      col3 = PY + Sensor side
        //   row 3 (CT graph + Daily/Weekly/Monthly toggle): full width
        //   row 4 (CT Histogram Distribution)              : full width
        //   row 5 (Timeline)                               : full width
        //   row 6 (Hourly slot table)                      : full width
        gridTemplateColumns: isPortrait ? "1fr 200px 185px" : "1fr 285px",
        gridTemplateRows:    isPortrait
          // 2026-05-18-r4 — KPI band tightened from 240px to 95px + 110px
          // split via gridAutoRows below.  Operator complaint: "kpi KI
          // PADDING BOHOT ZADA HAI ... MAIN COMPONENT KO HAMPER KRR RHI
          // HAI".  Gauges sub-row a = 90px (was floating ~95px); cards
          // sub-row b = 110px tight (Production / CT / Takt fill 100%
          // of cell height now via flex column space-around so they no
          // longer look "khali").  Total KPI band ~205px — buys 35 px
          // back for the chart + histogram rows below.
          // 2026-05-18-r9 — Portrait grid simplified from 6 rows to 5:
          //   row 1: header (auto-grow when flex-wrapped)
          //   row 2: KPI band (205px)
          //   row 3: 2×2 QUAD chart panel (1fr — fills remaining)
          //   row 4: timeline (auto)
          //   row 5: hourly  (auto)
          // The old separate histogram row (was row 4) folds INTO the
          // quad panel's bottom-right cell.  Matches operator's
          // sample.docx layout: "page box hi shi h baki kr de or
          // niche timeline and hourly same rhegi".
          ? "minmax(50px, auto) 205px 1fr auto auto"
          : "minmax(50px, auto) 1fr auto auto",
        gap:6, padding:6,
        paddingTop: gapShift ? 40 : 6,
        background:bg, color:text,
        overflow:"hidden",
        // 2026-05-18-r8 — Clockwise rotation only fires when the
        // selected layout direction differs from the viewport's
        // natural orientation (e.g. portrait layout requested on a
        // landscape viewport for vertical-mounted 16:9 screens).  In
        // all other cases the grid renders inline without rotation,
        // so a desktop dev viewing a landscape browser sees the
        // landscape dashboard upright, and a tablet user on a
        // portrait browser sees the portrait dashboard upright.
        position:    needsRotation ? "fixed" : "static",
        top:         needsRotation ? 0       : undefined,
        left:        needsRotation ? "100vw" : undefined,
        transformOrigin: needsRotation ? "top left" : undefined,
        transform:   needsRotation ? "rotate(90deg)" : undefined,
      }}>

        {/* ── 1. HEADER ──
            overflow:"visible" override so the machine-toggle dropdown
            (position:absolute inside this card) isn't clipped by the
            card()'s default overflow:hidden.  zIndex bump keeps the
            whole header above sibling KPI cards.
            2026-05-18-r8 — `flexWrap:"wrap"` + left/right groups also
            wrap so on a narrow portrait viewport (phone / tablet) the
            elements that won't fit on one row flow down to a second
            row instead of getting clipped.  Operator complaint:
            "details upr row me nhi aa rhi vo second row mw exist
            kr de".                                                  */}
        <div style={{...card(),gridColumn:"1/-1",display:"flex",alignItems:"center",
                     justifyContent:"space-between",
                     flexWrap:"wrap", rowGap: 4,
                     padding: isPortrait ? "4px 10px" : "0 16px",
                     gap: isPortrait ? 8 : undefined,
                     minHeight: 50,
                     overflow:"visible", position:"relative", zIndex:200}}>
          <div style={{display:"flex",alignItems:"center",
                       flexWrap:"wrap", rowGap: 4,
                       gap: isPortrait ? 8 : 14, minWidth: 0}}>
            <img src="/logo.jpg" alt="logo"
                  style={{
                    width: isPortrait ? 36 : 55,
                    height: isPortrait ? 36 : 55,
                    borderRadius: isPortrait ? 7 : 10,
                    objectFit:"contain",
                    flexShrink: 0,
                  }}/>
            <div style={{ minWidth: 0 }}>
              <div style={{
                fontSize: isPortrait ? 13 : 16,
                fontWeight:900,color:text,lineHeight:1.1,
                whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis",
              }}>{line.line_name}</div>
              <div style={{
                fontSize: isPortrait ? 9 : 11,
                color:textMut,marginTop:1,
                whiteSpace:"nowrap",
              }}>
                {rt.record_date||"—"}
              </div>
            </div>
            <div style={{width:1,height:22,background:border}}/>
            <span style={{padding:"3px 10px",borderRadius:99,fontSize:11,fontWeight:800,
              color:STATUS_CLR["RUNNING"],background:`${STATUS_CLR["RUNNING"]}18`,border:`1px solid ${STATUS_CLR["RUNNING"]}33`}}>
              {shift} SHIFT
            </span>
            <span style={{padding:"3px 12px",borderRadius:99,fontSize:11,fontWeight:800,
              color:statusColor,background:`${statusColor}18`,border:`1px solid ${statusColor}33`,
              letterSpacing:".06em",
              animation:status==="RUNNING"?"pulse 2s infinite":status==="BREAKDOWN"?"blink 1s infinite":"none"}}>
              {status}
            </span>
            {/* 2026-05-16 — MACHINE PILL (yellow-box position).
                Shows the line's MAIN machine name + (machine_seq) badge.
                Click → dropdown lists all machines on this line (Main +
                Subs) so operator can hop to any of them.  Main = stays
                on /fullscreen/<line_id>; Sub navigates to
                /submachine/<sub_id> for that machine's detail view. */}
            {(() => {
              const mainMachine = machines.find(m => !m.parent_plc_id);
              const machineName = mainMachine?.machine_name
                                 || line?.line_name
                                 || "Machine";
              const machineSeq  = mainMachine?.machine_seq;
              return (
                <div style={{position:"relative"}}>
                  <button
                    onClick={() => setMachineMenuOpen(o => !o)}
                    title="Switch between machines on this line"
                    style={{padding:"3px 10px", borderRadius:99, fontSize:12, fontWeight:800,
                      color:STATUS_CLR["SETUP"],
                      background:`${STATUS_CLR["SETUP"]}18`,
                      border:`1px solid ${STATUS_CLR["SETUP"]}44`,
                      cursor:"pointer", display:"inline-flex", alignItems:"center", gap:5,
                      letterSpacing:".02em"}}>
                    <span style={{fontSize:9,opacity:.7,textTransform:"uppercase"}}>Machine</span>
                    <strong>{machineName}</strong>
                    {machineSeq != null && (
                      <span style={{fontFamily:"monospace",opacity:.85}}>
                        ({machineSeq})
                      </span>
                    )}
                    <span style={{fontSize:9,opacity:.5,marginLeft:2}}>▾</span>
                  </button>
                  {machineMenuOpen && machines.length > 0 && (
                    <div onClick={() => setMachineMenuOpen(false)}
                         style={{position:"absolute",top:"100%",left:0,marginTop:4,
                           background:bgCard,border:`1px solid ${border}`,borderRadius:8,
                           minWidth:240,maxHeight:300,overflowY:"auto",
                           boxShadow:"0 12px 32px rgba(0,0,0,.55)",
                           zIndex:9999,padding:4}}>
                      {/* 2026-05-16 — Number-first layout per operator
                          spec: "front me 1 2 3 4 aise aana chahiye"
                          instead of MAIN/SUB badges + trailing (N).
                          Number = machine_seq if admin set it, else
                          falls back to position in the list (1-indexed)
                          so EVERY row always shows a sensible badge.
                          Currently-viewed row stays highlighted SETUP
                          color so operator knows where they are. */}
                      {machines.map((m, idx) => {
                        const isMain    = !m.parent_plc_id;
                        const isCurrent = isMain;     // on Fullscreen.jsx = MAIN view
                        const seqNum    = m.machine_seq != null
                                          ? m.machine_seq
                                          : (idx + 1);
                        return (
                          <div key={m.id}
                            onClick={(e) => {
                              e.stopPropagation();
                              setMachineMenuOpen(false);
                              if (isMain) return;
                              navigate(`/submachine-fullscreen/${m.id}`);
                            }}
                            style={{padding:"8px 10px",borderRadius:5,
                              fontSize:12,
                              color: isCurrent ? STATUS_CLR["SETUP"] : text,
                              background: isCurrent ? `${STATUS_CLR["SETUP"]}15` : "transparent",
                              cursor: isCurrent ? "default" : "pointer",
                              display:"flex",alignItems:"center",justifyContent:"space-between",
                              gap:8}}
                            onMouseEnter={e => { if(!isCurrent) e.currentTarget.style.background = `${STATUS_CLR["SETUP"]}10`; }}
                            onMouseLeave={e => { if(!isCurrent) e.currentTarget.style.background = "transparent"; }}>
                            <span style={{display:"flex",alignItems:"center",gap:10}}>
                              {/* Big leading number — 1, 2, 3 ... */}
                              <span style={{
                                minWidth:24, height:24,
                                display:"inline-flex", alignItems:"center", justifyContent:"center",
                                fontSize:13, fontWeight:900, fontFamily:"monospace",
                                color: isCurrent ? STATUS_CLR["SETUP"] : text,
                                background: isCurrent ? `${STATUS_CLR["SETUP"]}28` : `${border}`,
                                borderRadius:6,
                                letterSpacing:0,
                              }}>
                                {seqNum}
                              </span>
                              <strong>{m.machine_name || m.plc_ip}</strong>
                            </span>
                            {isCurrent && (
                              <span style={{fontSize:9,fontWeight:800,color:STATUS_CLR["SETUP"]}}>
                                ● HERE
                              </span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })()}
            {isOtActive && (
              <span style={{padding:"3px 10px",borderRadius:99,fontSize:11,fontWeight:800,
                color:"#16a34a",background:"rgba(22,163,74,0.15)",border:"1px solid rgba(22,163,74,0.35)",
                animation:"pulse 2s infinite"}}>
                ⏱ OT ACTIVE
              </span>
            )}
          </div>
          <div style={{display:"flex",alignItems:"center",
                       flexWrap:"wrap", rowGap: 4,
                       gap:10}}>
            {/* 2026-05-16 — MODEL NAME (red-box position).
                Moved out of the left cluster to the right cluster per
                operator spec: yellow box now shows machine, red box
                shows the running model.  Model No. is small/muted
                preface; model name strong. */}
            {rt.current_model_name && (
              <span style={{fontSize:12,color:textSub,display:"inline-flex",alignItems:"center",gap:6,
                padding:"3px 10px",borderRadius:99,
                background:bgDeep,border:`1px solid ${border}`}}>
                <span style={{fontSize:9,opacity:.7,textTransform:"uppercase",letterSpacing:".06em",color:textMut}}>
                  Model{rt.current_model_number != null ? ` ${rt.current_model_number}` : ""}
                </span>
                <strong style={{color:text}}>
                  {String(rt.current_model_name).replace(/^TYPE-SERIES:\s*/i,"")}
                </strong>
              </span>
            )}
            <div style={{display:"flex",alignItems:"center",gap:5,padding:"3px 10px",borderRadius:99,
              background:connected?`${STATUS_CLR["RUNNING"]}10`:`${STATUS_CLR["BREAKDOWN"]}10`,
              border:`1px solid ${connected?STATUS_CLR["RUNNING"]:STATUS_CLR["BREAKDOWN"]}33`}}>
              <div style={{width:5,height:5,borderRadius:"50%",
                background:connected?STATUS_CLR["RUNNING"]:STATUS_CLR["BREAKDOWN"],
                animation:"pulse 2s infinite"}}/>
              <span style={{fontSize:11,fontWeight:800,color:connected?STATUS_CLR["RUNNING"]:STATUS_CLR["BREAKDOWN"]}}>
                {connected?"Live":"Offline"}
              </span>
            </div>
            <span style={{fontFamily:"monospace",fontSize:16,fontWeight:800,color:textSub,letterSpacing:".04em"}}>{timeStr}</span>
            <button onClick={()=>setDark(d=>!d)} style={{padding:"4px 10px",borderRadius:6,
              border:`1px solid ${border}`,background:bgDeep,color:textSub,cursor:"pointer",fontSize:11,fontWeight:700}}>
              {D?"☀ LIGHT":"🌙 DARK"}
            </button>
            {/* Rotate dashboard — toggle layout for vertically-mounted
                screens.  2026-05-18-r8 — Chip displays the CURRENT
                layout (natural mapping): "9:16" while in portrait
                grid, "16:9" while in landscape grid.  Clicking
                toggles to the other layout.  Whether CSS rotation
                gets applied is decided separately by `needsRotation`
                (viewport-vs-layout mismatch).                        */}
            <button
              onClick={() => setOrientation(o => o === "portrait" ? "landscape" : "portrait")}
              title={isPortrait ? "Switch to landscape (16:9) layout" : "Switch to portrait (9:16) layout"}
              style={{padding:"3px 8px",borderRadius:6,
                border:`1px solid ${border}`,background:bgDeep,color:textSub,cursor:"pointer",
                display:"inline-flex",alignItems:"center",justifyContent:"center",gap:4,
                fontSize:11,fontWeight:700}}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
                style={{transform: isPortrait ? "rotate(90deg)" : "none",
                          transition:"transform .25s"}}>
                <rect x="3" y="6" width="18" height="12" rx="2"/>
                <path d="M21 16l-2 2"/>
                <path d="M3 8l2-2"/>
              </svg>
              {isPortrait ? "9:16" : "16:9"}
            </button>
            <button onClick={toggleFS}
              title={isFS ? "Exit fullscreen" : "Enter fullscreen"}
              style={{padding:"3px 8px",borderRadius:6,
              border:`1px solid ${border}`,background:bgDeep,color:textSub,cursor:"pointer",
              display:"inline-flex",alignItems:"center",justifyContent:"center"}}>
              {isFS ? (
                // Exit fullscreen — arrows pointing INWARD
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9 3v4a2 2 0 0 1-2 2H3"/>
                  <path d="M15 3v4a2 2 0 0 0 2 2h4"/>
                  <path d="M9 21v-4a2 2 0 0 0-2-2H3"/>
                  <path d="M15 21v-4a2 2 0 0 1 2-2h4"/>
                </svg>
              ) : (
                // Enter fullscreen — arrows pointing OUTWARD
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

        {/* ── LEFT CONTENT COLUMN ──
            16:9: column flex (KPI top, chart middle, Loss bottom).
            9:16: `display:contents` so its children participate directly
                  in the outer 3×6 grid — operator drew the layout where
                  KPI block, Loss Dist, and Chart card each live in their
                  own row/col of the outer grid (no nested wrapper).      */}
        <div style={{
          ...(isPortrait
            ? { display: "contents" }
            : { display:"flex", flexDirection:"column",
                gap:6, minHeight:0, overflow:"hidden" }),
        }}>

        {/* ── 2. KPI STRIP ──
            16:9: horizontal flex-wrap row of tiles at the top of LEFT CONTENT.
            9:16: KPI block at outer-grid row 2 col 1 — internal CSS grid
                  with 4 columns × 2 sub-rows:
                    sub-row a — OEE | Availability | Performance | Quality
                    sub-row b — Production | Cycle Time | Takt Time | —
                  Production/CT/Takt are LEFT-aligned in row b (the 4th
                  cell of row b is empty, matching operator's spec).      */}
        <div style={{
          gap:6, flexShrink:0,
          ...(isPortrait
            ? {
                display:               "grid",
                // 12-col fine-grain grid so 4 gauges span 3 cols each in
                // row a (4 x 25%) and 3 cards span 4 cols each in row b
                // (3 x 33.3%).  Cards being 33% wide instead of 25%
                // gives the Plan/Actual/OK/NG values enough room to
                // render at large font without overflowing.
                gridTemplateColumns:   "repeat(12, 1fr)",
                // 2026-05-18-r4 — Explicit row heights (was auto).  Row a
                // 90px = gauges, row b 1fr = Production/CT/Takt cards
                // fill the rest of the 205px KPI band.  Each card uses
                // flex column + height:100% so its rows distribute
                // evenly inside the box.
                gridTemplateRows:      "90px 1fr",
                gridColumn:            1,
                gridRow:               2,
                overflow:              "hidden",
                minHeight:             0,
              }
            : { display:"flex", flexWrap:"wrap" }
          ),
        }}>

          {/* Overall OEE — 9:16: row a, cols 1-3 (1/4 width) */}
          <div style={{...card(),
                width: isPortrait ? "auto" : 195,
                flexShrink: 0, display:"flex", alignItems:"center",
                gap: isPortrait ? 8 : 12,
                padding: isPortrait ? "6px 10px" : "8px 12px",
                ...(isPortrait ? { gridColumn: "1 / 4", gridRow: 1 } : {}),
              }}>
            <Gauge value={oee} color={oeeColor(oee)}
                   size={isPortrait ? 58 : 98}
                   textSub={textSub} isMain={true}/>
            <div>
              <div style={{fontSize: isPortrait ? 8 : 10,
                            fontWeight:800,color:textMut,letterSpacing:".08em",textTransform:"uppercase",marginBottom:2}}>
                OEE
              </div>
              <span style={{padding:"2px 6px",borderRadius:99,
                fontSize: isPortrait ? 8 : 10,
                fontWeight:900,
                color:oeeColor(oee),background:`${oeeColor(oee)}18`,border:`1px solid ${oeeColor(oee)}33`,
                marginTop:4,display:"inline-block"}}>{rt.oee_grade||"—"}</span>
            </div>
          </div>

          {/* Availability / Performance / Quality gauges.
              16:9: 3 individual cards rendered as flex siblings.
              9:16: 3 cards each spanning 3 cols of row a (4-7, 7-10, 10-13)
                    to evenly divide the remaining 9 cols after OEE.       */}
          {[
            { label:"Availability", val:avail, span: "4 / 7"  },
            { label:"Performance",  val:perf,  span: "7 / 10" },
            { label:"Quality",      val:qual,  span: "10 / 13"},
          ].map(g => (
            <div key={g.label} style={{...card(),
                  width: isPortrait ? "auto" : 127,
                  flexShrink: 0, display:"flex",
                  alignItems:"center", justifyContent:"center",
                  padding: isPortrait ? "4px 0" : "4px 0",
                  ...(isPortrait ? { gridColumn: g.span, gridRow: 1 } : {}),
                }}>
              <Gauge value={g.val} color={oeeColor(g.val)} label={g.label}
                     size={isPortrait ? 78 : 105}
                     textSub={textSub}/>
            </div>
          ))}

          {/* Production — 9:16: row b cols 1-4 (1/3 width) */}
          <div style={{...card(),
                width: isPortrait ? "auto" : 170,
                flexShrink: 0,
                // 2026-05-18-r4 — Fill the 1fr row b cell with flex
                // column.  Content inside (Plan / Actual / OK-NG /
                // optional kWh) distributes via space-around so the
                // card never looks "khali" anymore.
                ...(isPortrait ? {
                  gridColumn: "1 / 5", gridRow: 2,
                  display:"flex", flexDirection:"column", minHeight:0,
                } : {}),
              }}>
            <div style={hdr({padding:"4px 10px"})}>
              <span style={lbl9}>Production</span>
              <span style={{fontSize:9,color:textMut}}>Shift {shift}</span>
            </div>
            <div style={{
              padding: isPortrait ? "4px 9px" : "4px 10px",
              display:"flex", flexDirection:"column",
              gap: isPortrait ? 2 : 1,
              // Fill remaining card height so the OK/NG row sticks to
              // the bottom and Plan/Actual occupy the rest evenly.
              ...(isPortrait ? { flex: 1, justifyContent: "space-around" } : {}),
            }}>
              {[
                { l:"Plan",   v:tPlan,  c:STATUS_CLR["SETUP"],   big:true },
                { l:"Actual", v:actual, c:STATUS_CLR["RUNNING"], big:true },
              ].map(r => (
                <div key={r.l} style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                  <span style={{fontSize: isPortrait ? 10 : 11,
                                  fontWeight:700,color:textSub}}>{r.l}</span>
                  <span style={{fontFamily:"monospace",
                                  fontSize: isPortrait ? 17 : 22,
                                  fontWeight:900,color:r.c}}>{r.v.toLocaleString()}</span>
                </div>
              ))}
              <div style={{borderTop:`1px solid ${border}`,
                            marginTop:2, paddingTop:2,
                            display:"flex", justifyContent:"space-between", alignItems:"center"}}>
                <div style={{display:"flex", gap: isPortrait ? 6 : 10}}>
                  <span style={{fontSize: isPortrait ? 9 : 10,
                                  fontWeight:700,color:textMut}}>
                    OK <span style={{fontFamily:"monospace",
                                       fontSize: isPortrait ? 12 : 14,
                                       fontWeight:900,color:STATUS_CLR["RUNNING"]}}>{okTotal}</span>
                  </span>
                  <span style={{fontSize: isPortrait ? 9 : 10,
                                  fontWeight:700,color:textMut}}>
                    NG <span style={{fontFamily:"monospace",
                                       fontSize: isPortrait ? 12 : 14,
                                       fontWeight:900,color:STATUS_CLR["BREAKDOWN"]}}>{ngTotal}</span>
                  </span>
                </div>
              </div>
              {/* 2026-05-18-r12 — Per Part kWh MOVED to the Takt Time
                  card (operator spec: "ye stats ko tu takt time wale me
                  dal de").  Production card now keeps just Plan / Actual
                  / OK·NG; the energy KPI lives alongside Plan / Actual /
                  Variance in the Takt card below. */}
            </div>
          </div>

          {/* Cycle Time — 9:16: row b cols 5-8 (1/3 width) */}
          <div style={{...card(),
                width: isPortrait ? "auto" : 200,
                flexShrink: 0,
                ...(isPortrait ? {
                  gridColumn: "5 / 9", gridRow: 2,
                  display:"flex", flexDirection:"column", minHeight:0,
                } : {}),
              }}>
            <div style={hdr({padding:"4px 10px"})}><span style={lbl9}>Cycle Time</span></div>
            <div style={{padding: isPortrait ? "4px 9px" : "5px 10px",
                          display:"flex",flexDirection:"column",
                          gap: isPortrait ? 2 : 3,
                          ...(isPortrait ? { flex: 1, justifyContent: "space-around" } : {})}}>
              <div style={{display:"flex",justifyContent:"space-between"}}>
                <span style={{fontSize: isPortrait ? 9 : 10, color:textMut}}>Ideal</span>
                <span style={{fontFamily:"monospace",
                                fontSize: isPortrait ? 11 : 12,
                                fontWeight:800,color:textSub}}>{ideal.toFixed(1)}s</span>
              </div>
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"baseline"}}>
                <span style={{fontSize: isPortrait ? 9 : 10, color:textMut}}
                      title={ctStats.excluded > 0
                        ? `${ctStats.excluded} outlier cycle(s) excluded (breakdown overlap or >40s — see chart)`
                        : "Average cycle time across all cycles this shift"}>
                  {/* 2026-05-18-r13 — Operator spec: "-16 wala jo h vo
                      hta de only avg ct aaye".  Hide the explicit
                      excluded-outlier count; only the clean used-count
                      remains in parentheses.  The breakdown rationale
                      is still surfaced via the hover tooltip above. */}
                  Avg{ctStats.used > 0 ? ` (${ctStats.used})` : ""}
                </span>
                <span style={{fontFamily:"monospace",
                                fontSize: isPortrait ? 13 : 14,
                                fontWeight:900,
                  color:avgCT>ideal?STATUS_CLR["BREAKDOWN"]:STATUS_CLR["RUNNING"]}}>{avgCT.toFixed(2)}s</span>
              </div>
              <div style={{marginTop:2,borderRadius:5,
                            padding: isPortrait ? "1px 0" : "2px 0",
                background:`${statusColor}14`,border:`1px solid ${statusColor}40`,
                display:"flex",alignItems:"center",justifyContent:"center",
                animation:status==="RUNNING"?"pulse 2s infinite":"none"}}>
                <span style={{
                  fontSize: isPortrait ? 12 : 15,
                  fontWeight:900,color:statusColor,letterSpacing:".08em"}}>{status}</span>
              </div>
            </div>
          </div>

          {/* ── TAKT TIME ────────────────────────────────────────────
              2026-05-14 — operator redesign: this card is now purely a
              SECONDS view of takt vs actual cycle pace, NOT production
              counts (those live in the OK/NG card above).  Three rows:
                Plan     = rt.planned_takt_seconds (admin-configured
                            customer-demand rhythm).  Falls back to the
                            auto-derived rt.takt_seconds, then to ideal.
                Actual   = avgCT (running mean of all cycles this shift —
                            same value shown in the Cycle Time card).
                Variance = Plan − Actual, in seconds.  Positive ⇒ we're
                            faster than takt (good, green).  Negative ⇒
                            slower than takt (bad, red). */}
          {(() => {
            const takPlan = (rt?.planned_takt_seconds != null && rt.planned_takt_seconds > 0)
              ? Number(rt.planned_takt_seconds)
              : (Number(rt?.takt_seconds) || ideal);
            const takActual = Number(avgCT) || 0;
            const variance  = takPlan - takActual;             // +ve = ahead (good)
            const varColor  = variance >= 0 ? STATUS_CLR["RUNNING"] : STATUS_CLR["BREAKDOWN"];
            const actColor  = takActual <= takPlan ? STATUS_CLR["RUNNING"] : STATUS_CLR["BREAKDOWN"];
            return (
              <div style={{...card(),
                    width: isPortrait ? "auto" : 200,
                    flexShrink: 0,
                    ...(isPortrait ? {
                      gridColumn: "9 / 13", gridRow: 2,
                      display:"flex", flexDirection:"column", minHeight:0,
                    } : {}),
                  }}>
                <div style={hdr({padding:"4px 10px"})}><span style={lbl9}>Takt Time</span></div>
                <div style={{padding: isPortrait ? "4px 9px" : "5px 10px",
                              display:"flex", flexDirection:"column",
                              gap: isPortrait ? 2 : 3,
                              ...(isPortrait ? { flex: 1, justifyContent: "space-around" } : {})}}>
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"baseline"}}>
                    <span style={{fontSize: isPortrait ? 9 : 10, color:textMut}}>Plan</span>
                    <span style={{fontFamily:"monospace",
                                    fontSize: isPortrait ? 13 : 14,
                                    fontWeight:900,color:"#60a5fa"}}>
                      {takPlan.toFixed(2)}s
                    </span>
                  </div>
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"baseline"}}>
                    <span style={{fontSize: isPortrait ? 9 : 10, color:textMut}}>Actual</span>
                    <span style={{fontFamily:"monospace",
                                    fontSize: isPortrait ? 13 : 14,
                                    fontWeight:900,color:actColor}}>
                      {takActual > 0 ? `${takActual.toFixed(2)}s` : "—"}
                    </span>
                  </div>
                  <div style={{borderTop:`1px solid ${border}`,marginTop:2,paddingTop:2,
                    display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                    <span style={{fontSize: isPortrait ? 8 : 9,
                                    fontWeight:700,color:textMut,letterSpacing:".05em",textTransform:"uppercase"}}>Variance</span>
                    <span style={{fontFamily:"monospace",
                                    fontSize: isPortrait ? 12 : 13,
                                    fontWeight:900,color:varColor}}>
                      {takActual > 0
                        ? `${variance >= 0 ? "+" : ""}${variance.toFixed(2)}s`
                        : "—"}
                    </span>
                  </div>
                  {/* 2026-05-18-r12 — Per Part kWh moved here from the
                      Production card (operator spec).  Same theme-aware
                      amber pill design that was used in the Production
                      slot — bright in dark mode, deep amber in light. */}
                  {rt?.energy_per_part != null && rt.energy_per_part > 0 && (
                    <div style={{borderTop:`1px solid ${border}`,marginTop:2,paddingTop:3,
                                 display:"flex",justifyContent:"space-between",alignItems:"center"}}
                         title="Energy consumed per finished part (admin-configured on Line Master).">
                      <span style={{fontSize: isPortrait ? 8 : 9,
                                    fontWeight:800,
                                    color: D ? "#fbbf24" : "#b45309",
                                    letterSpacing:".04em",textTransform:"uppercase"}}>
                        Per Part
                      </span>
                      <span style={{fontFamily:"monospace",
                                    fontSize: isPortrait ? 11 : 12,
                                    fontWeight:900,
                                    color: D ? "#fbbf24" : "#b45309",
                                    padding:"1px 7px", borderRadius: 99,
                                    background: D ? "rgba(251,191,36,0.12)" : "rgba(180,83,9,0.10)",
                                    border: `1px solid ${D ? "rgba(251,191,36,0.35)" : "rgba(180,83,9,0.30)"}`}}>
                        {Number(rt.energy_per_part).toFixed(3)} kWh
                      </span>
                    </div>
                  )}
                </div>
              </div>
            );
          })()}

        </div>

        {/* ── 3. CHART + SIDE PANEL ──
            16:9: own 2-col sub-grid (chart 1fr + Loss Distribution 215px).
            9:16: `display:contents` so the chart card + Loss Distribution
                  card escape this wrapper and participate directly in the
                  outer 3×6 grid:
                    Chart card     → row 3, full width (gridColumn 1/-1)
                    Loss Dist card → row 2, col 2 (operator-drawn center) */}
        <div style={{
          ...(isPortrait
            ? { display: "contents" }
            : { display: "grid",
                gridTemplateColumns: "1fr 215px",
                gap: 6, flex: 1, minHeight: 0 }),
        }}>

          {/* 2026-05-18-r9 — PORTRAIT QUAD PANEL.
              Operator spec from sample.docx: in 9:16 mode the chart
              area shows a 2×2 grid of 4 always-visible mini charts:
                ┌──────────────────┬──────────────────┐
                │ CT Graph (video) │ Weekly Plan vs   │
                │                  │   Actual (daily) │
                ├──────────────────┼──────────────────┤
                │ Monthly Plan vs  │ CT Distribution  │
                │ Actual cumul OK  │  + median CT     │
                └──────────────────┴──────────────────┘
              Bottom rows (timeline + hourly) stay untouched per
              "niche timeline and hourly same rhegi".  In landscape
              the existing single chart-card with toggle stays.  */}
          {/* 2026-05-18-r10 — Quad reorganised to 3-row stack per
              operator's reference photo:
                ┌───────────────────────────────────────────────────┐
                │ Row 1 · CT Graph (Daily) — FULL WIDTH             │
                ├───────────────────────────────────────────────────┤
                │ Row 2 · Weekly Plan vs Actual — FULL WIDTH        │
                ├───────────────────────┬───────────────────────────┤
                │ Row 3a · CT histogram │ Row 3b · Monthly Plan vs  │
                │   + median line       │  Actual (cumulative)      │
                └───────────────────────┴───────────────────────────┘
              Operator quote: "cycle time and weekly graph side by
              side kr dia, aise aana chaihiye upr niche, and last ek
              sath" — so CT and Weekly stack top-to-bottom (each full
              width), and only the bottom row splits 50/50 for the
              Monthly + Histogram pair.                              */}
          {isPortrait && (
            <div style={{
              gridColumn: "1 / -1", gridRow: 3,
              display: "grid",
              gridTemplateColumns: "1fr 1fr",
              gridTemplateRows: "1.2fr 1fr 1fr",
              gap: 6, minHeight: 0,
            }}>
              {/* ── Row 1 · CT Graph (full width, click-to-video)
                  2026-05-18-r13 — Full tool strip restored (slider +
                  Min/Max chips + slot filter + NG nav) per operator
                  feedback: "ct wale sare tool nhi aa rhe".  Toggle
                  buttons stay hidden — portrait shows ALL 4 charts at
                  once so a mode switcher is meaningless here.        */}
              <div style={{...card(),
                  gridColumn: "1 / -1", gridRow: 1,
                  display:"flex", flexDirection:"column", minHeight:0, overflow:"hidden"}}>
                <div style={hdr({padding:"4px 10px", gap:6, flexWrap:"nowrap"})}>
                  <span style={lbl9}>CT Graph · tap dot for video</span>

                  {/* Tool strip — right side of header, compact */}
                  <div style={{display:"flex", alignItems:"center", gap:5,
                               fontSize:9, fontWeight:600, color:textMut,
                               marginLeft:"auto", flexShrink:0,
                               flexWrap:"wrap", rowGap:3}}>
                    {cmsData.length > 0 && (
                      <>
                        {/* Min / Max chips — click → opens video for that cycle */}
                        {(() => {
                          const eMin = extremes?.min;
                          const eMax = extremes?.max;
                          const fmtT = (iso) => {
                            if (!iso) return "—";
                            try {
                              return new Date(iso).toLocaleTimeString("en-IN",
                                { hour:"2-digit", minute:"2-digit", hour12:false });
                            } catch { return "—"; }
                          };
                          const Chip = ({ row, color, label }) => (
                            <span
                              onClick={() => row && row.ct_value != null && openCycleVideo(row)}
                              title={row && row.ct_value != null
                                     ? `${label} ${Number(row.ct_value).toFixed(2)}s · ${row.part_code || "—"} @ ${fmtT(row.ts)} · tap for video`
                                     : `${label} cycle — waiting for data`}
                              style={{
                                display:"inline-flex", alignItems:"center", gap:2,
                                padding:"1px 5px", borderRadius:99,
                                border:`1px solid ${color}55`,
                                background:`${color}14`,
                                color, fontSize:8, fontWeight:800, letterSpacing:".04em",
                                cursor: row && row.ct_value != null ? "pointer" : "default",
                                whiteSpace:"nowrap",
                              }}>
                              <span style={{textTransform:"uppercase"}}>{label}</span>
                              <span style={{fontFamily:"monospace",fontWeight:900}}>
                                {row && row.ct_value != null ? `${Number(row.ct_value).toFixed(2)}s` : "—"}
                              </span>
                            </span>
                          );
                          return (
                            <>
                              <Chip row={eMin} color={STATUS_CLR["RUNNING"]}   label="Min" />
                              <Chip row={eMax} color={STATUS_CLR["BREAKDOWN"]} label="Max" />
                            </>
                          );
                        })()}

                        {/* Slot filter */}
                        {allSlots.length > 0 && (
                          <select value={cmsSlotFilter}
                            onChange={e => {
                              const label = e.target.value;
                              setCmsSlotFilter(label);
                              if (!label) { setCmsSlotRange(null); }
                              else {
                                const s = allSlots.find(sl => sl.slot_label === label);
                                if (s) setCmsSlotRange({ ssMin: toMin(s.start_time), seMin: toMin(s.end_time) });
                              }
                              setCmsViewStart(null);
                            }}
                            style={{padding:"1px 3px",borderRadius:3,fontSize:8,fontWeight:700,
                              cursor:"pointer",border:`1px solid ${border}`,background:bgDeep,color:textSub,outline:"none"}}>
                            <option value="">All slots</option>
                            {allSlots.map((s, si) => <option key={si} value={s.slot_label}>{s.slot_label}</option>)}
                          </select>
                        )}

                        {/* NG navigator — scroll-only, with inline
                            current-NG label so operator knows which
                            dot they're pointing at without opening a modal. */}
                        <span style={{color:"#ef4444",fontWeight:800,fontSize:9}}
                              title={ngTotal !== ngIndices.length
                                       ? `Shift counter: ${ngTotal} · chart dots: ${ngIndices.length}`
                                       : `${ngTotal} NG cycles this shift`}>
                          NG:{ngTotal}
                        </span>
                        <button disabled={!ngIndices.length} onClick={() => {
                          if (!ngIndices.length) return;
                          const p = (ngNavIdx - 1 + ngIndices.length) % ngIndices.length;
                          setNgNavIdx(p);
                          setCmsViewStart(Math.max(0, ngIndices[p] - 15));
                        }} style={{padding:"0 4px",borderRadius:3,fontSize:9,fontWeight:900,
                                   cursor:ngIndices.length?"pointer":"not-allowed",
                                   border:`1px solid ${border}`,background:bgDeep,color:textSub,lineHeight:1}}>▲</button>
                        <span style={{fontWeight:700,color:textSub,minWidth:10,textAlign:"center",fontSize:9}}>
                          {ngIndices.length > 0 ? ngNavIdx+1 : "—"}
                        </span>
                        <button disabled={!ngIndices.length} onClick={() => {
                          if (!ngIndices.length) return;
                          const n = (ngNavIdx + 1) % ngIndices.length;
                          setNgNavIdx(n);
                          setCmsViewStart(Math.max(0, ngIndices[n] - 15));
                        }} style={{padding:"0 4px",borderRadius:3,fontSize:9,fontWeight:900,
                                   cursor:ngIndices.length?"pointer":"not-allowed",
                                   border:`1px solid ${border}`,background:bgDeep,color:textSub,lineHeight:1}}>▼</button>
                        {ngIndices.length > 0 && filteredCms[ngIndices[ngNavIdx]] && (
                          <button
                            onClick={() => {
                              const cy = filteredCms[ngIndices[ngNavIdx]];
                              if (cy) openCycleVideo(cy);
                            }}
                            title="Click to open NG details + video"
                            style={{fontSize:9,marginLeft:4,padding:"1px 6px",
                                      background:"rgba(239,68,68,0.15)",
                                      border:"1px solid rgba(239,68,68,0.5)",
                                      borderRadius:3,color:"#ef4444",fontWeight:700,
                                      fontFamily:"monospace",cursor:"pointer"}}>
                            #{filteredCms[ngIndices[ngNavIdx]].cycle_seq}
                            {filteredCms[ngIndices[ngNavIdx]].ct_value != null
                              ? ` · ${Number(filteredCms[ngIndices[ngNavIdx]].ct_value).toFixed(1)}s`
                              : ""}
                            <span style={{marginLeft:4,fontSize:10}}>▶</span>
                          </button>
                        )}

                        <span style={{color:textMut,fontSize:9,fontWeight:700,marginLeft:4}}>
                          ideal {ideal}s
                        </span>
                      </>
                    )}
                    {(cmsLoading) && <span style={{color:STATUS_CLR["MATERIAL WAIT"],animation:"pulse 1s infinite"}}>…</span>}
                  </div>
                </div>

                {/* Body — canvas + slider (same structure as landscape CT mode) */}
                <div style={{flex:1, position:"relative", minHeight:0, padding:"4px 8px 2px"}}>
                  <div ref={cmsScrollRef} style={{
                      position:"absolute", inset:4,
                      display:"flex", flexDirection:"column"}}>
                    <div style={{flex:1, position:"relative", minHeight:0}}>
                      <canvas ref={cmsChartRef} style={{display:"block", width:"100%", height:"100%"}}/>
                    </div>
                    {/* Cycle window slider — appears when >30 cycles in shift */}
                    {filteredCms.length > 30 && (
                      <div style={{flexShrink:0, padding:"3px 0 0"}}>
                        <input
                          type="range" min={0}
                          max={Math.max(0, filteredCms.length - 30)}
                          value={cmsViewStart !== null ? cmsViewStart : Math.max(0, filteredCms.length - 30)}
                          onChange={e => setCmsViewStart(Number(e.target.value))}
                          style={{width:"100%", cursor:"pointer",
                                  accentColor:STATUS_CLR["RUNNING"], height:3, display:"block"}}
                        />
                        <div style={{display:"flex", justifyContent:"space-between",
                                     fontSize:9, color:textSub, marginTop:1}}>
                          <span>#{filteredCms[0]?.cycle_seq}</span>
                          <span style={{color:textMut}}>
                            {(() => {
                              const s = cmsViewStart ?? Math.max(0, filteredCms.length - 30);
                              return `${filteredCms.length} · viewing ${s+1}-${Math.min(s+30, filteredCms.length)}`
                                   + (cmsSlotFilter ? ` [${cmsSlotFilter}]` : "");
                            })()}
                          </span>
                          <span>#{filteredCms[filteredCms.length-1]?.cycle_seq}</span>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* ── Row 2 · Weekly (full width, last 7 days, NON-cumulative) ── */}
              <div style={{...card(),
                  gridColumn: "1 / -1", gridRow: 2,
                  display:"flex", flexDirection:"column", minHeight:0, overflow:"hidden",
                  position:"relative"}}>
                <div style={hdr({padding:"4px 10px"})}>
                  <span style={lbl9}>Daily · Plan vs Actual </span>
                </div>
                <div style={{flex:1, position:"relative", minHeight:0, padding:"4px 8px 2px"}}>
                  <MiniProductionChart mode="weekly" history={quadHistory}
                                       dark={dark} chartReady={chartReady && chartLib}/>
                </div>
              </div>

              {/* ── Row 3a · CT histogram with median (left half) ── */}
              <div style={{...card(),
                  gridColumn: 1, gridRow: 3,
                  display:"flex", flexDirection:"column", minHeight:0, overflow:"hidden",
                  position:"relative"}}>
                <div style={hdr({padding:"4px 10px"})}>
                  <span style={lbl9}>CT Distribution · last 30 days</span>
                  <span style={{fontSize:10,color:textMut,fontWeight:700}}>
                    {ctHistogram?.total_cycles?.toLocaleString() || "—"} cycles
                    {ctHistogram?.peak_bucket != null && (
                      <> · peak <span style={{color:"#fbbf24"}}>
                        {ctHistogram.peak_bucket.toFixed(1)}s</span></>
                    )}
                  </span>
                </div>
                <div style={{flex:1, position:"relative", minHeight:0, padding:"4px 8px 2px"}}>
                  <CtDistributionChart data={ctHistogram}
                                        idealCt={Number(rt?.cycle_time_plan || 15)}
                                        bgDeep={bgDeep}
                                        border={border}
                                        text={text}
                                        textMut={textMut}
                                        textSub={textSub}
                                        D={D}
                                        chartReady={chartReady && chartLib} />
                </div>
              </div>

              {/* ── Row 3b · Monthly Plan vs Actual cumulative (right half) ── */}
              <div style={{...card(),
                  gridColumn: 2, gridRow: 3,
                  display:"flex", flexDirection:"column", minHeight:0, overflow:"hidden",
                  position:"relative"}}>
                <div style={hdr({padding:"4px 10px"})}>
                  <span style={lbl9}>Weekly · Plan vs Actual (Cumulative OK)</span>
                </div>
                <div style={{flex:1, position:"relative", minHeight:0, padding:"4px 8px 2px"}}>
                  <MiniProductionChart mode="monthly" history={quadHistory}
                                       dark={dark} chartReady={chartReady && chartLib}/>
                </div>
              </div>
            </div>
          )}

          {/* Landscape chart card — original toggle UI.  Hidden in
              portrait because the CT chart now lives inside the quad
              panel above (top-left cell).                            */}
          {!isPortrait && (
          <div style={{
            ...card(),
            display:"flex", flexDirection:"column", minHeight:0,
          }}>
            <div style={hdr({gap:6,flexWrap:"nowrap"})}>
              {/* Left: mode buttons */}
              <div style={{display:"flex",alignItems:"center",gap:4,flexShrink:0}}>
                {[
                  {id:"ct",label:"Cycle Time"},
                  {id:"weekly",label:"Daily"},
                  {id:"monthly",label:"Weekly"},
                  {id:"yearly",label:"Monthly"},
                  // CT Distribution tab only in landscape — 9:16 has a
                  // dedicated histogram card below the chart card.
                  ...(isPortrait ? [] : [{id:"histogram",label:"CT Distribution"}]),
                ].map(m=>(
                  <button key={m.id} onClick={()=>setChartMode(m.id)}
                    style={{padding:"2px 8px",borderRadius:99,
                      border:`1px solid ${chartMode===m.id?STATUS_CLR["SETUP"]:border}`,
                      background:chartMode===m.id?`${STATUS_CLR["SETUP"]}22`:bgDeep,
                      color:chartMode===m.id?STATUS_CLR["SETUP"]:textSub,
                      fontSize:9,fontWeight:800,cursor:"pointer",letterSpacing:".04em",textTransform:"uppercase"}}>
                    {m.label}
                  </button>
                ))}
                {chartMode === "yearly" && (
                  <select value={selectedFY} onChange={e => setSelectedFY(Number(e.target.value))}
                    style={{padding:"1px 4px",borderRadius:4,fontSize:9,fontWeight:800,
                      color:STATUS_CLR["SETUP"],background:bgDeep,
                      border:`1px solid ${STATUS_CLR["SETUP"]}66`,cursor:"pointer",outline:"none"}}>
                    {fyOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                )}
                {chartTitle && chartMode !== "yearly" && (
                  <span style={{fontSize:9,fontWeight:900,color:STATUS_CLR["SETUP"]}}>{chartTitle}</span>
                )}
              </div>

              {/* Right: slot + NG + legends — all one line */}
              <div style={{display:"flex",alignItems:"center",gap:6,fontSize:9,fontWeight:600,color:textMut,marginLeft:"auto",flexShrink:0}}>
                {chartMode === "ct" && cmsData.length > 0 && (
                  <>
                    {/* ── INLINE MIN / MAX CHIPS ────────────────────────
                        2026-05-15 — moved out of a side card into the
                        chart header to keep KPI strip margins clean.
                        Each chip is clickable → opens the same cycle
                        video modal that the chart dots open. */}
                    {(() => {
                      const eMin = extremes?.min;
                      const eMax = extremes?.max;
                      const fmtT = (iso) => {
                        if (!iso) return "—";
                        try {
                          return new Date(iso).toLocaleTimeString("en-IN",
                            { hour:"2-digit", minute:"2-digit", hour12:false });
                        } catch { return "—"; }
                      };
                      const Chip = ({ row, color, label }) => (
                        <span
                          onClick={() => row && row.ct_value != null && openCycleVideo(row)}
                          title={row && row.ct_value != null
                                 ? `${label} ${Number(row.ct_value).toFixed(2)}s · ${row.part_code || "—"} @ ${fmtT(row.ts)} · tap for video`
                                 : `${label} cycle — waiting for data`}
                          style={{
                            display:"inline-flex", alignItems:"center", gap:3,
                            padding:"1px 6px", borderRadius:99,
                            border:`1px solid ${color}55`,
                            background:`${color}14`,
                            color, fontSize:9, fontWeight:800, letterSpacing:".04em",
                            cursor: row && row.ct_value != null ? "pointer" : "default",
                            whiteSpace:"nowrap",
                          }}>
                          <span style={{textTransform:"uppercase"}}>{label}</span>
                          <span style={{fontFamily:"monospace",fontWeight:900}}>
                            {row && row.ct_value != null ? `${Number(row.ct_value).toFixed(2)}s` : "—"}
                          </span>
                          <span style={{color:textMut,fontWeight:700}}>{fmtT(row?.ts)}</span>
                        </span>
                      );
                      return (
                        <>
                          <Chip row={eMin} color={STATUS_CLR["RUNNING"]}   label="Min" />
                          <Chip row={eMax} color={STATUS_CLR["BREAKDOWN"]} label="Max" />
                          <span style={{width:1,height:12,background:border,flexShrink:0}}/>
                        </>
                      );
                    })()}

                    {/* Slot filter */}
                    {allSlots.length > 0 && (
                      <select value={cmsSlotFilter}
                        onChange={e => {
                          const label = e.target.value;
                          setCmsSlotFilter(label);
                          if (!label) { setCmsSlotRange(null); }
                          else {
                            const s = allSlots.find(sl => sl.slot_label === label);
                            if (s) setCmsSlotRange({ ssMin: toMin(s.start_time), seMin: toMin(s.end_time) });
                          }
                          setCmsViewStart(null);
                        }}
                        style={{padding:"1px 4px",borderRadius:3,fontSize:8,fontWeight:700,
                          cursor:"pointer",border:`1px solid ${border}`,background:bgDeep,color:textSub,outline:"none"}}>
                        <option value="">All</option>
                        {allSlots.map((s, si) => <option key={si} value={s.slot_label}>{s.slot_label}</option>)}
                      </select>
                    )}

                    {/* NG navigator — compact, scroll-only with current-NG label */}
                    <span style={{color:"#ef4444",fontWeight:800}}
                          title={ngTotal !== ngIndices.length
                                   ? `Shift counter: ${ngTotal} · chart dots: ${ngIndices.length}`
                                   : `${ngTotal} NG cycles this shift`}>
                      NG:{ngTotal}
                    </span>
                    <button disabled={!ngIndices.length} onClick={() => {
                      if (!ngIndices.length) return;
                      const p = (ngNavIdx - 1 + ngIndices.length) % ngIndices.length;
                      setNgNavIdx(p);
                      setCmsViewStart(Math.max(0, ngIndices[p] - 15));
                    }} style={{padding:"0 4px",borderRadius:3,fontSize:9,fontWeight:900,cursor:ngIndices.length?"pointer":"not-allowed",border:`1px solid ${border}`,background:bgDeep,color:textSub,lineHeight:1}}>▲</button>
                    <span style={{fontWeight:700,color:textSub,minWidth:12,textAlign:"center"}}>{ngIndices.length > 0 ? ngNavIdx+1 : "—"}</span>
                    <button disabled={!ngIndices.length} onClick={() => {
                      if (!ngIndices.length) return;
                      const n = (ngNavIdx + 1) % ngIndices.length;
                      setNgNavIdx(n);
                      setCmsViewStart(Math.max(0, ngIndices[n] - 15));
                    }} style={{padding:"0 4px",borderRadius:3,fontSize:9,fontWeight:900,cursor:ngIndices.length?"pointer":"not-allowed",border:`1px solid ${border}`,background:bgDeep,color:textSub,lineHeight:1}}>▼</button>
                    {ngIndices.length > 0 && filteredCms[ngIndices[ngNavIdx]] && (
                      <button
                        onClick={() => {
                          const cy = filteredCms[ngIndices[ngNavIdx]];
                          if (cy) openCycleVideo(cy);
                        }}
                        title="Click to open NG details + video"
                        style={{fontSize:9,marginLeft:4,padding:"1px 6px",
                                  background:"rgba(239,68,68,0.15)",
                                  border:"1px solid rgba(239,68,68,0.5)",
                                  borderRadius:3,color:"#ef4444",fontWeight:700,
                                  fontFamily:"monospace",cursor:"pointer"}}>
                        #{filteredCms[ngIndices[ngNavIdx]].cycle_seq}
                        {filteredCms[ngIndices[ngNavIdx]].ct_value != null
                          ? ` · ${Number(filteredCms[ngIndices[ngNavIdx]].ct_value).toFixed(1)}s`
                          : ""}
                        <span style={{marginLeft:4,fontSize:10}}>▶</span>
                      </button>
                    )}

                    <span style={{width:1,height:12,background:border,flexShrink:0}}/>
                  </>
                )}

                {/* Legends — compact */}
                {chartMode==="ct"
                  ? [{c:STATUS_CLR["RUNNING"],l:"<Ideal"},{c:STATUS_CLR["BREAKDOWN"],l:">Ideal"},{c:"#f59e0b",l:"=Ideal"},{c:"#ef4444",l:"NG(!)",f:true}].map(({c,l,f})=>(
                      <span key={l} style={{display:"flex",alignItems:"center",gap:2}}>
                        {f ? <span style={{fontWeight:900,color:c}}>!</span>
                           : <span style={{width:6,height:6,background:c,borderRadius:"50%"}}/>}
                        <span>{l}</span>
                      </span>))
                  : [{c:STATUS_CLR["SETUP"],l:"Plan"},{c:STATUS_CLR["RUNNING"],l:"Act≥Plan"}].map(({c,l})=>(
                      <span key={l} style={{display:"flex",alignItems:"center",gap:2}}>
                        <span style={{width:8,height:8,background:c,borderRadius:2}}/>{l}
                      </span>))}
                {(histLoading || cmsLoading) && <span style={{color:STATUS_CLR["MATERIAL WAIT"],animation:"pulse 1s infinite"}}>…</span>}
              </div>
            </div>
            <div style={{flex:1,position:"relative",minHeight:100,padding:"8px 12px 4px"}}>
              <canvas ref={chartRef} style={{
                // Hide the historical canvas when we're in CT or histogram mode
                display: (chartMode === "ct" || chartMode === "histogram") ? "none" : "block",
                position:"absolute",top:8,left:12,width:"calc(100% - 24px)",height:"calc(100% - 18px)"
              }}/>

              {/* 2026-05-18-r4 — Bar-chart empty/loading state.  Without this
                  the canvas just looks blank when (a) the production_history
                  fetch is in flight and history===null, or (b) the line has
                  no non-GAP records in the lookback window.  Operator
                  complaint: "DAILY WEEKLY MONTHLY KEE BARGRAPH JO SHOW HOTE
                  THE ABHI TKK VOH HTT GYE HAI BLANK SCREEN SHOW HORI HAI".
                  Visible only when (i) we're in a bar-chart mode and
                  (ii) there's nothing to render — the chart canvas itself
                  always sits beneath so a real bar render covers this. */}
              {chartMode !== "ct" && chartMode !== "histogram"
                && (!history || history.length === 0) && (
                <div style={{
                  position:"absolute", inset:0,
                  display:"flex", alignItems:"center", justifyContent:"center",
                  flexDirection:"column", gap:6, color: textMut,
                  pointerEvents:"none",
                }}>
                  <div style={{
                    fontSize: isPortrait ? 12 : 14,
                    fontWeight: 800, letterSpacing:".08em",
                    textTransform:"uppercase",
                  }}>
                    {histLoading ? "Loading…" : "No data in lookback window"}
                  </div>
                  {!histLoading && (
                    <div style={{
                      fontSize: isPortrait ? 9 : 10, color: textMut,
                      fontStyle:"italic",
                    }}>
                      (data populates once a non-GAP shift logs production)
                    </div>
                  )}
                </div>
              )}

              {/* CT-Distribution histogram (last 30 days, 0.1 s buckets).
                  16:9: opens via the "CT Distribution" mode tab.
                  9:16: hidden here — rendered as its own card at row 4 of
                  the outer grid (see <CtDistributionChart …/> below). */}
              {chartMode === "histogram" && !isPortrait && (
                <CtDistributionChart data={ctHistogram}
                                      idealCt={Number(rt?.cycle_time_plan || 15)}
                                      bgDeep={bgDeep}
                                      border={border}
                                      text={text}
                                      textMut={textMut}
                                      textSub={textSub}
                                      D={D}
                                      chartReady={chartReady && chartLib} />
              )}
              {chartMode === "ct" && (
                <div ref={cmsScrollRef} style={{
                  position:"absolute",top:8,left:12,right:12,bottom:4,
                  display:"flex",flexDirection:"column",
                }}>
                  {/* ECG spike graph — fixed 40-cycle window */}
                  <div style={{flex:1,position:"relative",minHeight:0}}>
                    <canvas ref={cmsChartRef} style={{display:"block"}}/>
                  </div>

                  {/* Slider: visible when > 30 cycles in shift */}
                  {filteredCms.length > 30 && (
                    <div style={{flexShrink:0,padding:"3px 0 0"}}>
                      <input
                        type="range"
                        min={0}
                        max={Math.max(0, filteredCms.length - 30)}
                        value={cmsViewStart !== null ? cmsViewStart : Math.max(0, filteredCms.length - 30)}
                        onChange={e => setCmsViewStart(Number(e.target.value))}
                        style={{width:"100%",cursor:"pointer",accentColor:STATUS_CLR["RUNNING"],height:3,display:"block"}}
                      />
                      <div style={{display:"flex",justifyContent:"space-between",fontSize:9,color:textSub,marginTop:1}}>
                        <span>#{filteredCms[0]?.cycle_seq}</span>
                        <span style={{color:textMut}}>
                          {(() => {
                            const s = cmsViewStart ?? Math.max(0, filteredCms.length - 30);
                            return `${filteredCms.length} cycles (${s+1}–${Math.min(s+30, filteredCms.length)})${cmsSlotFilter ? " [" + cmsSlotFilter + "]" : ""}`;
                          })()}
                        </span>
                        <span>#{filteredCms[filteredCms.length-1]?.cycle_seq}</span>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
          )}{/* end landscape chart card conditional (r9) */}

          {/* Loss Breakdown — click anywhere to open hourly breakup.
              Intentionally NO label/button text in the header — clean
              look, header reads exactly the same as the panel did
              before this feature shipped.  Cursor pointer + subtle
              hover shadow are the only affordance hints. */}
          <div onClick={() => setLossModalOpen(true)}
               style={{...card(), display:"flex", flexDirection:"column", minHeight:0,
                       cursor:"pointer", transition:"box-shadow .15s",
                       // 9:16 — Loss Distribution sits at outer-grid row 2
                       // col 2 (center of the KPI band, between KPI block
                       // on the left and PY/Sensor on the right).
                       ...(isPortrait ? { gridColumn: 2, gridRow: 2 } : {}),
                     }}
               onMouseEnter={e => e.currentTarget.style.boxShadow = `0 0 0 2px ${STATUS_CLR["BREAKDOWN"]}33`}
               onMouseLeave={e => e.currentTarget.style.boxShadow = ""}>
              <div style={hdr()}>
                <span style={lbl9}>Loss Distribution</span>
                <span style={{fontFamily:"monospace",fontSize:12,fontWeight:800,
                  color:totalLoss>0?STATUS_CLR["BREAKDOWN"]:textMut}}>{fmtSec(totalLoss)}</span>
              </div>
              <div style={{flex:1,overflowY:"auto",padding:"4px 0"}}>
                {lossData.map(r => {
                  const pct = totalLoss > 0 ? (r.sec/totalLoss*100) : 0;
                  return (
                    <div key={r.key} style={{padding:"3px 12px"}}>
                      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:2}}>
                        <div style={{display:"flex",alignItems:"center",gap:6}}>
                          <div style={{width:6,height:6,borderRadius:2,background:r.color,flexShrink:0}}/>
                          <span style={{fontSize:11,color:r.sec>0?textSub:textMut,fontWeight:r.sec>0?700:500}}>{r.label}</span>
                        </div>
                        <span style={{fontFamily:"monospace",fontSize:11,fontWeight:800,color:r.sec>0?r.color:textMut}}>{fmtSec(r.sec)}</span>
                      </div>
                      <div style={{background:D?"#141e2e":"#e2e8f0",borderRadius:99,height:2,overflow:"hidden"}}>
                        <div style={{width:`${pct}%`,height:"100%",background:r.color,borderRadius:99,transition:"width .6s"}}/>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

        </div>
        </div>{/* ── end LEFT CONTENT COLUMN ── */}

        {/* 2026-05-18-r9 — Standalone histogram card REMOVED.
            The CT Distribution histogram now lives in the bottom-right
            cell of the portrait quad panel (above), keeping it always
            visible alongside the CT graph, Weekly, and Monthly views.
            Row 4 of the outer grid is reclaimed; gridTemplateRows was
            shortened from 6 rows to 5 in the parent grid styles.   */}

        {/* ── RIGHT COLUMN — PY STATUS + SENSOR STATUS stacked ──────
            16:9: row 2 col 2 of outer grid (1fr × 285px layout).
            9:16: row 2 col 3 of outer grid (3-col KPI band, far right).
            Flex column splits vertical space evenly so each tile gets ~50%.
            Same UX for both: big tick / warning icon, big status text,
            tap → modal. */}
        <div style={{
          display:"flex",flexDirection:"column",gap:6,minHeight:0,overflow:"hidden",
          ...(isPortrait ? { gridColumn: 3, gridRow: 2 } : {}),
        }}>

          {/* ── POKA-YOKE STATUS BUTTON ─────────────────────────────
              2026-05-13 — operator spec: turn the long vertical PY list
              into ONE single button.
                • Green "✓ All Set OK" when every check is passing
                • Red   "⚠ N Failed"   when any check is bypassed
                • Tap   → hourly breakdown modal */}
          {(() => {
            const pyFailedCount = pokaStatus.filter(p => p.is_bypassed).length;
            const pyAllOk       = pokaStatus.length > 0 && pyFailedCount === 0;
            const pyEmpty       = pokaStatus.length === 0;
            const bgGood        = `${STATUS_CLR["RUNNING"]}14`;
            const bgBad         = `${STATUS_CLR["BREAKDOWN"]}22`;
            const bgIdle        = D ? "#141e2e" : "#e2e8f0";
            return (
              <div style={{...card(),flex:1,display:"flex",flexDirection:"column",overflow:"hidden",minHeight:0}}>
                {/* Heading strip so operators can identify the card at a
                    glance — without this the giant ✓ / ⚠ icon dominates
                    and people don't know which check it represents.    */}
                <div style={hdr({padding:"4px 10px"})}>
                  <span style={lbl9}>Poka-Yoke</span>
                  {pokaStatus.length > 0 && (
                    <span style={{fontSize:9,color:textMut,fontWeight:700}}>
                      {pokaStatus.length} check{pokaStatus.length !== 1 ? "s" : ""}
                    </span>
                  )}
                </div>
                <button
                  onClick={() => setPyModalOpen(true)}
                  disabled={pyEmpty}
                  title={pyEmpty ? "No PY checks for current model"
                                : pyAllOk ? "All poka-yoke checks passing — tap for shift history"
                                          : `${pyFailedCount} check${pyFailedCount!==1?'s':''} failed — tap for detail`}
                  style={{
                    flex:1, width:"100%", border:"none", cursor: pyEmpty ? "default" : "pointer",
                    // 2026-05-18-r4 — Smaller padding + smaller fonts in
                    // portrait so the 185px-wide column doesn't truncate.
                    // Operator: "PY BOX aur sensOR BOX KE ANDAR JO LIKH RHA
                    // USKA SIZE MINMIZE KRR DE BUT CONTANT SAARA UTNE S
                    // BOXES MEIN FIT OFF HONA CHAIYE".
                    padding: isPortrait ? "6px 8px" : "12px 14px",
                    background: pyEmpty ? bgIdle : pyAllOk ? bgGood : bgBad,
                    display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center",
                    gap: isPortrait ? 3 : 8, transition:"background .15s",
                    color: text,
                    fontFamily:"'Barlow Condensed','Barlow',sans-serif",
                    animation: (!pyEmpty && !pyAllOk) ? "pulse 1.4s infinite" : "none",
                  }}>
                  <div style={{fontSize: isPortrait ? 34 : 56, lineHeight:1,
                    color: pyEmpty ? textMut : pyAllOk ? STATUS_CLR["RUNNING"] : STATUS_CLR["BREAKDOWN"]}}>
                    {pyEmpty ? "—" : pyAllOk ? "✓" : "⚠"}
                  </div>
                  <div style={{fontSize: isPortrait ? 14 : 22, fontWeight:900,letterSpacing:".04em",textTransform:"uppercase",
                    textAlign:"center", lineHeight:1.05,
                    color: pyEmpty ? textMut : pyAllOk ? STATUS_CLR["RUNNING"] : STATUS_CLR["BREAKDOWN"]}}>
                    {pyEmpty ? "No Checks" : pyAllOk ? "All Set OK" : `${pyFailedCount} Failed`}
                  </div>
                  <div style={{fontSize: isPortrait ? 8 : 11, fontWeight:700,color:textMut,letterSpacing:".05em",textTransform:"uppercase",
                    textAlign:"center", lineHeight:1.1}}>
                    Poka-Yoke {pokaStatus.length>0 && `· ${pokaStatus.length} check${pokaStatus.length!==1?'s':''}`}
                  </div>
                  {!pyEmpty && (
                    <div style={{fontSize: isPortrait ? 8 : 10, color:textMut,fontStyle:"italic",
                      marginTop: isPortrait ? 1 : 2,
                      padding: isPortrait ? "2px 7px" : "3px 10px",
                      borderRadius:99,border:`1px solid ${border}`,background:bgCard,
                      whiteSpace:"nowrap"}}>
                      Tap for detail →
                    </div>
                  )}
                </button>
              </div>
            );
          })()}

          {/* ── SENSOR STATUS BUTTON ────────────────────────────────
              2026-05-13 — same UX as PY card.  X-bit health snapshot
              from collector via /api/poka-yoke/sensor-sweep/<line_id>.
                • Green "✓ All Sensing" — every X-bit is toggling
                • Red   "⚠ N Desensed" — N bits have not toggled for
                                          longer than the stuck-threshold
                • Tap   → modal showing a LOG of stuck periods.
                          When healthy: empty log.
                          When stuck:   PY name + bit + from-to range.
              Button is ALWAYS clickable — even with no snapshot yet,
              tapping shows "no log yet" state instead of doing nothing. */}
          {(() => {
            const entries  = sensorSweep?.entries || [];
            const counts   = sensorSweep?.counts || {};
            const health   = (sensorSweep?.health || "").toUpperCase();
            const stuck    = counts.stuck   ?? entries.filter(s => (s.status||"").toLowerCase()==="stuck").length;
            const unknown  = counts.unknown ?? entries.filter(s => (s.status||"").toLowerCase()==="unknown").length;
            const alive    = counts.alive   ?? entries.filter(s => (s.status||"").toLowerCase()==="alive").length;
            const total    = counts.total   ?? entries.length;

            // Visual mapping straight from health field
            const isCrit  = health === "CRITICAL";
            const isWarn  = health === "WARNING";
            const isOk    = health === "OK";
            const isEmpty = health === "NO_PY" || total === 0;

            const color  = isCrit ? STATUS_CLR["BREAKDOWN"]
                         : isWarn ? STATUS_CLR["MATERIAL_WAIT"]
                         : isOk   ? STATUS_CLR["RUNNING"]
                         :          textMut;
            const bg     = isCrit ? `${STATUS_CLR["BREAKDOWN"]}22`
                         : isWarn ? `${STATUS_CLR["MATERIAL_WAIT"]}18`
                         : isOk   ? `${STATUS_CLR["RUNNING"]}14`
                         :          (D ? "#141e2e" : "#e2e8f0");
            const icon   = isCrit ? "⚠" : isWarn ? "⏳" : isOk ? "✓" : "—";
            const headline = isEmpty ? "No PY Configured"
                           : isCrit  ? `Health Critical · ${stuck} Stuck`
                           : isWarn  ? `Waiting · ${unknown} Unknown`
                           :           "Health OK";
            return (
              <div style={{...card(),flex:1,display:"flex",flexDirection:"column",overflow:"hidden",minHeight:0}}>
                {/* Heading strip — same pattern as the PY card above so
                    operators can tell the cards apart at a glance.     */}
                <div style={hdr({padding:"4px 10px"})}>
                  <span style={lbl9}>Sensor Health</span>
                  {total > 0 && (
                    <span style={{fontSize:9,color:textMut,fontWeight:700}}>
                      {isCrit   ? `${stuck}/${total} stuck`
                       : isWarn ? `${unknown}/${total} unknown`
                       :         `${alive}/${total} alive`}
                    </span>
                  )}
                </div>
                <button
                  onClick={() => setSensorModalOpen(true)}
                  title={isCrit ? `${stuck} sensor(s) stuck — tap for detail`
                       : isWarn ? `${unknown} sensor(s) awaiting first toggle data`
                       : isOk   ? `All ${total} sensors toggling normally — tap for detail`
                       :          "No PYs configured for current model — tap for detail"}
                  style={{
                    flex:1, width:"100%", border:"none", cursor:"pointer",
                    // 2026-05-18-r4 — Symmetric tighten with PY card above so
                    // the 185-px col fits all content without truncation.
                    padding: isPortrait ? "6px 8px" : "12px 14px",
                    background: bg,
                    display:"flex", flexDirection:"column", alignItems:"center", justifyContent:"center",
                    gap: isPortrait ? 3 : 8, transition:"background .15s",
                    color: text,
                    fontFamily:"'Barlow Condensed','Barlow',sans-serif",
                    animation: isCrit ? "pulse 1.4s infinite" : "none",
                  }}>
                  <div style={{fontSize: isPortrait ? 34 : 56, lineHeight:1, color}}>{icon}</div>
                  <div style={{fontSize: isPortrait ? 13 : 20, fontWeight:900,letterSpacing:".04em",textTransform:"uppercase", color,
                    textAlign:"center", lineHeight:1.05}}>
                    {headline}
                  </div>
                  <div style={{fontSize: isPortrait ? 8 : 11, fontWeight:700,color:textMut,letterSpacing:".05em",textTransform:"uppercase",
                    textAlign:"center", lineHeight:1.1}}>
                    Sensor Health {total > 0 && (isCrit
                      ? `· ${stuck}/${total} stuck`
                      : isWarn
                        ? `· ${unknown}/${total} unknown`
                        : `· ${alive}/${total} alive`)}
                  </div>
                  <div style={{fontSize: isPortrait ? 8 : 10, color:textMut,fontStyle:"italic",
                    marginTop: isPortrait ? 1 : 2,
                    padding: isPortrait ? "2px 7px" : "3px 10px",
                    borderRadius:99,border:`1px solid ${border}`,background:bgCard,
                    whiteSpace:"nowrap"}}>
                    Tap for detail →
                  </div>
                </button>
              </div>
            );
          })()}

        </div>{/* ── end RIGHT COLUMN ── */}

        {/* ── 4. FULL-WIDTH TIMELINE — spans all columns ──
            16:9: row 3 (after chart+side row 2).
            9:16: row 4 of the 5-row outer grid (r9 reduced from 6 to 5
                  rows when the quad panel absorbed the histogram). */}
        <div style={{...card(),gridColumn:"1/-1",
                      padding:"6px 12px",display:"flex",flexDirection:"column",gap:5,
                      ...(isPortrait ? { gridRow: 4 } : {}),
                    }}>

          {/* Header */}
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
            <div style={{display:"flex",alignItems:"center",gap:10}}>
              <span style={{fontSize:12,fontWeight:900,color:STATUS_CLR["RUNNING"]}}>{shift} SHIFT TIMELINE</span>
              <span style={{fontSize:11,color:textMut}}>{shiftCfg?.start_time?.slice(0,5)} – {shiftCfg?.end_time?.slice(0,5)}</span>
              <div style={{display:"flex",alignItems:"center",gap:5,padding:"2px 9px",borderRadius:99,
                background:`${statusColor}15`,border:`1px solid ${statusColor}33`}}>
                <div style={{width:6,height:6,borderRadius:"50%",background:statusColor,
                  animation:status==="RUNNING"?"pulse 2s infinite":"none"}}/>
                <span style={{fontSize:10,fontWeight:800,color:statusColor}}>{status}</span>
              </div>
            </div>
            <div style={{display:"flex",alignItems:"center",gap:14}}>
              <span style={{fontSize:11,color:textMut}}>
                Actual: <strong style={{color:STATUS_CLR["RUNNING"],fontSize:13}}>{actual.toLocaleString()}</strong>
                <span style={{fontSize:10,marginLeft:4}}>(OK:<strong style={{color:STATUS_CLR["RUNNING"]}}>{okTotal}</strong> NG:<strong style={{color:STATUS_CLR["BREAKDOWN"]}}>{ngTotal}</strong>)</span>
              </span>
              <span style={{fontSize:11,color:textMut}}>
                Plan: <strong style={{color:STATUS_CLR["SETUP"],fontSize:13}}>{tPlan.toLocaleString()}</strong>
              </span>
              <span style={{fontSize:12,fontWeight:900,color:oeeColor(progress)}}>{progress.toFixed(1)}% done</span>
            </div>
          </div>

          {/* THE BAR — status-logged segments, no production math */}
          <div style={{position:"relative",height:20,background:D?"#141e2e":"#e2e8f0",borderRadius:4,overflow:"hidden"}}>
            {allSlots.map((s,i) => {
              const ss    = toMin(s.start_time);
              const se    = toMin(s.end_time);
              const sdur  = se > ss ? se-ss : se+1440-ss;
              const left  = ((ss-sStart+(ss<sStart?1440:0))/sDur)*100;
              const wPct  = (sdur/sDur)*100;
              const ssRel = ss >= sStart ? ss-sStart : ss+1440-sStart;
              const seRel = ssRel + sdur;
              const isFuture  = sElapsed < ssRel;

              // Future slot — dim, uncoloured
              if (isFuture) {
                return (
                  <div key={i} style={{
                    position:"absolute",left:`${left}%`,width:`${wPct-.1}%`,
                    height:"100%",
                    background:D?"rgba(30,41,59,0.3)":"rgba(203,213,225,0.5)",
                    borderRight:`1px solid ${bg}`,
                  }}/>
                );
              }

              // Build segments from status log for this slot's window
              const segs = buildSlotSegments(ssRel, seRel, sdur);

              // No log entries for this slot — show neutral gray (history unknown)
              // We don't assume "all green" because that hides real breakdowns.
              if (!segs || segs.length === 0) {
                return (
                  <div key={i} style={{
                    position:"absolute",left:`${left}%`,width:`${wPct-.1}%`,
                    height:"100%",borderRight:`1px solid ${bg}`,
                    background:D?"rgba(30,41,59,0.3)":"rgba(203,213,225,0.5)",
                  }}/>
                );
              }

              // Render the logged segments absolutely positioned within slot
              return (
                <div key={i} style={{
                  position:"absolute",left:`${left}%`,width:`${wPct-.1}%`,
                  height:"100%",borderRight:`1px solid ${bg}`,overflow:"hidden",
                }}>
                  {segs.map((seg,si) => {
                    const [segStatus, segTimes, segDur] = (seg.tooltip||"").split("  ");
                    return (
                      <div key={si} style={{
                        position:"absolute",
                        left:`${Math.min(seg.startPct,99.9)}%`,
                        width:`${Math.max(0.1, seg.widthPct)}%`,
                        height:"100%",
                        background:`${seg.color}cc`,
                        cursor:"default",
                      }}
                        onMouseEnter={e => setTlTip({
                          x: e.clientX, y: e.clientY,
                          status: segStatus, times: segTimes, dur: segDur, color: seg.color,
                        })}
                        onMouseMove={e => setTlTip(t => t ? {...t, x:e.clientX, y:e.clientY} : t)}
                        onMouseLeave={() => setTlTip(null)}
                      />
                    );
                  })}
                </div>
              );
            })}

            {/* ── OT segment ───────────────────────────────────────
                2026-05-13 — operator spec: when OT is active, the
                shift-timeline bar must extend past shift_end into the
                OT window and paint actual status segments inside it
                (RUNNING / BREAKDOWN / IDLE etc) so the team sees what
                happened during overtime.  Geometry uses the same
                relative-frame as the regular slots (sStart-based,
                sDur-extended).  Status segments reuse buildSlotSegments
                — same status log, same color mapping. */}
            {otActive && shiftCfg?.ot_end_time && (() => {
              const sEndMin   = sEnd;
              const otEndMin  = toMin(shiftCfg.ot_end_time);
              let   otDur     = otEndMin - sEndMin;
              if (otDur <= 0) otDur += 1440;
              const ssRel = sEndMin >= sStart ? sEndMin - sStart : sEndMin + 1440 - sStart;
              const seRel = ssRel + otDur;
              const left  = (ssRel / sDur) * 100;
              const wPct  = (otDur / sDur) * 100;
              const isFuture = sElapsed < ssRel;

              // Future OT window — striped to visually distinguish from
              // regular future slots (admin activated OT but operator
              // hasn't reached that time yet).
              if (isFuture) {
                return (
                  <div key="ot-future" style={{
                    position:"absolute",left:`${left}%`,width:`${wPct-.1}%`,height:"100%",
                    background:`repeating-linear-gradient(45deg, ${D?"rgba(34,197,94,0.18)":"rgba(34,197,94,0.15)"} 0 6px, transparent 6px 12px)`,
                    borderLeft:`1px dashed ${STATUS_CLR["RUNNING"]}66`,
                    borderRight:`1px solid ${bg}`,
                  }} title={`OT window: ${String(Math.floor(sEndMin/60)).padStart(2,'0')}:${String(sEndMin%60).padStart(2,'0')} – ${String(Math.floor(otEndMin/60)).padStart(2,'0')}:${String(otEndMin%60).padStart(2,'0')}`}/>
                );
              }

              // Past/current OT — render status segments from log
              const segs = buildSlotSegments(ssRel, seRel, otDur);
              if (!segs || segs.length === 0) {
                return (
                  <div key="ot-unknown" style={{
                    position:"absolute",left:`${left}%`,width:`${wPct-.1}%`,height:"100%",
                    background:D?"rgba(30,41,59,0.3)":"rgba(203,213,225,0.5)",
                    borderLeft:`1px dashed ${STATUS_CLR["RUNNING"]}66`,
                    borderRight:`1px solid ${bg}`,
                  }} title="OT window — no status events yet"/>
                );
              }
              return (
                <div key="ot-active" style={{
                  position:"absolute",left:`${left}%`,width:`${wPct-.1}%`,height:"100%",
                  borderLeft:`1px dashed ${STATUS_CLR["RUNNING"]}99`,
                  borderRight:`1px solid ${bg}`,
                  overflow:"hidden",
                }} title={`OT window — ${segs.length} status segment${segs.length!==1?'s':''}`}>
                  {segs.map((seg,si) => (
                    <div key={si} style={{
                      position:"absolute",
                      left:`${Math.min(seg.startPct,99.9)}%`,
                      width:`${Math.max(0.2, seg.widthPct)}%`,
                      height:"100%",
                      background:STATUS_CLR[seg.status] || STATUS_CLR["IDLE"],
                    }} title={seg.tooltip || seg.status}/>
                  ))}
                </div>
              );
            })()}

            {/* Now marker */}
            <div style={{position:"absolute",left:`${tlPct}%`,top:0,bottom:0,width:2,background:text,zIndex:4}}>
              <div style={{position:"absolute",top:-2,left:"50%",transform:"translateX(-50%)",
                width:6,height:6,borderRadius:"50%",background:text,border:`1px solid ${bg}`}}/>
            </div>
          </div>

          {/* Hour ticks */}
          <div style={{position:"relative",height:12}}>
            {(() => {
              const ticks=[];
              for (let i=0; i<=sDur; i+=60) {
                const m=(sStart+i)%1440, h=Math.floor(m/60), mn=m%60;
                ticks.push({ pct:(i/sDur)*100, label:`${String(h).padStart(2,"0")}:${String(mn).padStart(2,"0")}` });
              }
              return ticks.map((t,i) => (
                <span key={i} style={{position:"absolute",left:`${t.pct}%`,transform:"translateX(-50%)",
                  fontSize:9,color:textMut,whiteSpace:"nowrap"}}>{t.label}</span>
              ));
            })()}
          </div>

          {/* Legend */}
          <div style={{display:"flex",gap:14,flexWrap:"wrap"}}>
            {[
              { l:"RUNNING",     c:STATUS_CLR["RUNNING"]       },
              { l:"BREAKDOWN",   c:STATUS_CLR["BREAKDOWN"]     },
              { l:"QUAL ISSUE",  c:STATUS_CLR["QUALITY ISSUE"] },
              { l:"MATERIAL",    c:STATUS_CLR["MATERIAL WAIT"] },
              { l:"SETUP",       c:STATUS_CLR["SETUP"]         },
              { l:"CHANGE OVER", c:STATUS_CLR["CHANGE OVER"]   },
              { l:"OTHERS",      c:STATUS_CLR["OTHERS"]        },
              { l:"BREAK",       c:STATUS_CLR["BREAK"]         },
            ].map(({l,c}) => (
              <div key={l} style={{display:"flex",alignItems:"center",gap:4}}>
                <div style={{width:10,height:10,background:c,borderRadius:2,flexShrink:0}}/>
                <span style={{fontSize:9,fontWeight:700,color:textMut}}>{l}</span>
              </div>
            ))}
          </div>
        </div>

        {/* ── 5. HOURLY TABLE — spans all columns ──
            16:9: row 4 (last row).  9:16: row 5 (last row of 5 after
                  r9's histogram fold into the quad).                  */}
        <div style={{...card(),gridColumn:"1/-1",overflow:"hidden",display:"flex",flexDirection:"column",
                      ...(isPortrait ? { gridRow: 5 } : {}),
                    }}>
          <div style={{flex:1,overflowX:"auto",overflowY:"hidden"}}>
            <table style={{width:"100%",borderCollapse:"collapse",tableLayout:"fixed"}}>
              <thead>
                <tr style={{background:bgDeep}}>
                  <th style={{padding:"6px 8px",textAlign:"left",fontWeight:900,fontSize:11,
                    color:textSub,borderRight:`1px solid ${border}`,width:85}}>METRIC</th>
                  {slotData.map(s => (
                    <th key={s.label} style={{padding:"6px 4px",textAlign:"center",fontWeight:800,fontSize:11,
                      color:s.isFuture?textMut:text,borderRight:`1px solid ${border}`,
                      opacity:s.isFuture?0.4:1}}>
                      {s.label}
                      {s.isCurrent && <div style={{fontSize:8,color:STATUS_CLR["RUNNING"],fontWeight:900}}>▶ NOW</div>}
                    </th>
                  ))}
                  <th style={{padding:"6px 8px",textAlign:"center",fontWeight:900,fontSize:11,
                    color:STATUS_CLR["QUALITY ISSUE"],width:85}}>TOTAL</th>
                </tr>
              </thead>
              <tbody>
                {/* PLAN row — reads directly from collector (rt[prefix_plan]) */}
                <tr style={{borderTop:`1px solid ${border}`}}>
                  <td style={{padding:"8px 8px",fontWeight:800,fontSize:11,color:textMut,borderRight:`1px solid ${border}`}}>PLAN</td>
                  {slotData.map((s,i) => (
                    <td key={i} style={{padding:"8px 4px",textAlign:"center",fontFamily:"monospace",fontSize:16,fontWeight:800,
                      color:s.isFuture?"rgba(100,100,100,0.3)":s.planDB>0?STATUS_CLR["SETUP"]:textMut,
                      borderRight:`1px solid ${border}`}}>
                      {s.planDB}
                    </td>
                  ))}
                  <td style={{padding:"8px",textAlign:"center",fontFamily:"monospace",fontSize:18,fontWeight:900,
                    color:STATUS_CLR["QUALITY ISSUE"]}}>{tPlan}</td>
                </tr>
                {/* OK/NG combined row */}
                <tr style={{borderTop:`1px solid ${border}`}}>
                  <td style={{padding:"2px 8px",fontWeight:800,fontSize:10,color:textMut,borderRight:`1px solid ${border}`}}>
                    <span style={{color:STATUS_CLR["RUNNING"]}}>OK</span>/<span style={{color:STATUS_CLR["BREAKDOWN"]}}>NG</span>
                  </td>
                  {slotData.map((s,i) => (
                    <td key={i} style={{padding:"2px 4px",textAlign:"center",borderRight:`1px solid ${border}`}}>
                      <div style={{fontFamily:"monospace",fontSize:13,fontWeight:800,lineHeight:1.1}}>
                        <span style={{color:s.isFuture?"rgba(100,100,100,0.3)":s.okDB>0?STATUS_CLR["RUNNING"]:textMut}}>{s.okDB}</span>
                        <span style={{color:textMut,margin:"0 2px"}}>/</span>
                        {/* 2026-05-21 — NG cell clickable: opens modal
                            with table of all NG parts in this slot. */}
                        <span
                          onClick={(s.ngDB > 0 && !s.isFuture)
                                    ? () => setNgListModal({ slot: s.label, date: rtRef.current?.record_date || new Date().toISOString().slice(0,10) })
                                    : undefined}
                          style={{
                            color: s.isFuture ? "rgba(100,100,100,0.3)"
                                              : s.ngDB > 0 ? STATUS_CLR["BREAKDOWN"] : textMut,
                            cursor: (s.ngDB > 0 && !s.isFuture) ? "pointer" : "default",
                            textDecoration: (s.ngDB > 0 && !s.isFuture) ? "underline dotted" : "none",
                            textUnderlineOffset: 2,
                          }}
                          title={(s.ngDB > 0 && !s.isFuture) ? "Click to see NG parts" : ""}
                        >
                          {s.ngDB}
                        </span>
                      </div>
                    </td>
                  ))}
                  <td style={{padding:"2px 8px",textAlign:"center"}}>
                    <div style={{fontFamily:"monospace",fontSize:14,fontWeight:900,lineHeight:1.1}}>
                      <span style={{color:okTotal>0?STATUS_CLR["RUNNING"]:textMut}}>{okTotal}</span>
                      <span style={{color:textMut,margin:"0 2px"}}>/</span>
                      <span style={{color:ngTotal>0?STATUS_CLR["BREAKDOWN"]:textMut}}>{ngTotal}</span>
                    </div>
                  </td>
                </tr>
                {/* TOTAL (OK+NG) row */}
                <tr style={{borderTop:`2px solid ${border}`,background:bgDeep}}>
                  <td style={{padding:"5px 8px",fontWeight:800,fontSize:11,color:textSub,borderRight:`1px solid ${border}`}}>ACTUAL</td>
                  {slotData.map((s,i) => (
                    <td key={i} style={{padding:"5px 4px",textAlign:"center",borderRight:`1px solid ${border}`}}>
                      <div style={{fontFamily:"monospace",fontSize:16,fontWeight:800,
                        color:s.isFuture?"rgba(100,100,100,0.3)":s.actualDB>0?STATUS_CLR["RUNNING"]:textMut}}>
                        {s.actualDB}
                      </div>
                      {!s.isFuture && (
                        <div style={{fontFamily:"monospace",fontSize:10,marginTop:1,fontWeight:700,
                          color:s.variance>0?STATUS_CLR["RUNNING"]:s.variance<0?STATUS_CLR["BREAKDOWN"]:textMut}}>
                          ({s.variance>0?"+":""}{s.variance})
                        </div>
                      )}
                    </td>
                  ))}
                  <td style={{padding:"5px 8px",textAlign:"center"}}>
                    <div style={{fontFamily:"monospace",fontSize:18,fontWeight:900,
                      color:actual>0?STATUS_CLR["RUNNING"]:textMut}}>{actual}</div>
                    <div style={{fontFamily:"monospace",fontSize:11,marginTop:1,fontWeight:800,
                      color:tVar>0?STATUS_CLR["RUNNING"]:tVar<0?STATUS_CLR["BREAKDOWN"]:textMut}}>
                      ({tVar>0?"+":""}{tVar})
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>


      </div>

      {/* ── Timeline hover tooltip ── */}
      {tlTip && (
        <div style={{
          position:"fixed", left:tlTip.x+12, top:tlTip.y-60,
          pointerEvents:"none", zIndex:9998,
          background: D ? "#0d1420" : "#fff",
          border:`1px solid ${border}`,
          borderRadius:8, padding:"8px 12px",
          boxShadow:"0 8px 24px rgba(0,0,0,0.35)",
          whiteSpace:"nowrap",
          animation:"tlFadeIn .12s ease",
        }}>
          <div style={{fontSize:11,fontWeight:800,color:tlTip.color,letterSpacing:".04em"}}>{tlTip.status}</div>
          <div style={{fontSize:10,color:textSub,marginTop:2}}>{tlTip.times}</div>
          <div style={{fontSize:9,color:textMut,marginTop:1}}>{tlTip.dur}</div>
        </div>
      )}

      {/* ── Video Modal ── */}
      {/* The modal stays mounted as long as there's a cycle loaded OR an
          active PiP window, so that clicking a different cycle just swaps
          the src inside the same <video> element (and therefore the same
          PiP window) instead of creating a new one. When PiP is active but
          the user has closed the modal UI, we hide the overlay with CSS
          rather than unmounting.

          2026-05-19 — REVERTED r17.  Operator wants the popup to be
          ALWAYS landscape (upright on the physical monitor), NOT
          rotated with the dashboard.  overlayPosStyle is now plain
          `position:fixed; inset:0`; React-Portal still escapes the
          rotated dashboard's transform-ancestor, so the popup covers
          the natural viewport without inheriting any CSS rotation. */}
      {videoModal && createPortal((
        <div
          onClick={() => {
            // Close click on the backdrop: if PiP is playing, keep the video
            // mounted (invisible) so the PiP window continues. Otherwise tear down.
            if (pipActive) {
              setShowModalUI(false);
            } else {
              if (videoModal?.video_url) {
                /* blob cleanup no longer needed — video uses streaming URL */
              }
              setVideoModal(null);
              setShowModalUI(false);
            }
          }}
          style={{
            ...overlayPosStyle,
            zIndex:9999,
            background:"rgba(0,0,0,0.82)",
            display: showModalUI ? "flex" : "none",
            alignItems:"center",justifyContent:"center",
          }}
        >
          <div
            onClick={e => e.stopPropagation()}
            style={videoExpanded
              ? {
                  // 2026-05-23 — YouTube-style true fullscreen.
                  // Black background, zero padding, no border — video
                  // owns 100vw × 100vh and the header chrome is hidden
                  // (see header div below: `display:none` when expanded).
                  // Floating expand/× buttons overlay the top-right corner.
                  background: "#000",
                  borderRadius:0, padding:0,
                  width:"100vw", height:"100vh", maxWidth:"none",
                  border:"none",
                  display:"flex", flexDirection:"column",
                  position:"relative",
                }
              : {
                  // 2026-05-27 — NG cycle: red frame so the operator
                  // can tell at a glance whether the modal they just
                  // opened is an NG part.  OK cycle uses the default
                  // border colour (unchanged).
                  background: videoModal?.is_ng
                    ? (D ? "#1a0a0d" : "#fef2f2")
                    : (D ? "#0a0f1a" : "#ffffff"),
                  borderRadius:12,padding:12,
                  maxWidth:820,width:"90vw",
                  maxHeight:"92vh",
                  display:"flex", flexDirection:"column",
                  boxShadow: videoModal?.is_ng
                    ? "0 24px 72px rgba(239,68,68,0.5)"
                    : "0 24px 72px rgba(0,0,0,0.6)",
                  border: videoModal?.is_ng
                    ? `3px solid #ef4444`
                    : `1px solid ${border}`,
                }}
          >
            {/* 2026-05-23 — Header chrome hidden in fullscreen mode so
                only the video is visible (operator complaint: "video ko
                fullscreen krne prr comments hata jaye jaise youtube ka
                fullscreen").  Buttons re-rendered as a floating overlay
                inside the expanded mode below. */}
            <div style={{
              display: videoExpanded ? "none" : "flex",
              justifyContent:"space-between",alignItems:"center",marginBottom:10,flexShrink:0,
            }}>
              <span style={{fontSize:12,fontWeight:800,color:text,letterSpacing:".04em"}}>
                Part #{videoModal.cycle_seq}
                {videoModal.part_code && (
                  <> {"  |  "}ID: <span style={{fontFamily:"monospace",color:"#3b82f6"}}>{String(videoModal.part_code).replace(/:$/, "")}</span></>
                )}
                {/* 2026-05-27 — Always show the cycle's stored CT (from
                    DB).  Earlier this preferred `_video_duration` from
                    the HTML5 player, but with fragmented-MP4 stream
                    copy the browser reports duration as 2.00 s (size
                    of the first fragment) until the full file buffers
                    — so the header read "2.00s" for every clip even
                    when the video was clearly 9 s long.  The DB CT is
                    the cycle measurement we trust everywhere else
                    (chart, tooltip), so use that here too. */}
                {"  |  "}
                {(() => {
                  const dbCt = Number(videoModal.ct_value) || 0;
                  return dbCt ? `${dbCt.toFixed(2)}s` : "—";
                })()}
                {"  |  "}Ideal: {ideal}s
                {rt?.takt_seconds && Math.abs(rt.takt_seconds - ideal) > 0.1 && (
                  <> {"  |  "}Takt: <span style={{ color: "#60a5fa" }}>{Number(rt.takt_seconds).toFixed(2)}s</span></>
                )}
                {"  |  "}{videoModal.ts ? new Date(videoModal.ts).toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit",second:"2-digit"}) : ""}
              </span>
              <div style={{display:"flex",gap:4,alignItems:"center"}}>
                <button
                  onClick={() => setVideoExpanded(v => !v)}
                  title={videoExpanded ? "Minimise video (Esc)" : "Expand video"}
                  style={{
                    background:"transparent",border:`1px solid ${border}`,cursor:"pointer",
                    borderRadius:6,padding:"3px 8px",color:textSub,
                    display:"inline-flex",alignItems:"center",justifyContent:"center",
                  }}
                >
                  {videoExpanded ? (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                      stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M9 3v4a2 2 0 0 1-2 2H3"/>
                      <path d="M15 3v4a2 2 0 0 0 2 2h4"/>
                      <path d="M9 21v-4a2 2 0 0 0-2-2H3"/>
                      <path d="M15 21v-4a2 2 0 0 1 2-2h4"/>
                    </svg>
                  ) : (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                      stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M3 9V5a2 2 0 0 1 2-2h4"/>
                      <path d="M21 9V5a2 2 0 0 0-2-2h-4"/>
                      <path d="M3 15v4a2 2 0 0 0 2 2h4"/>
                      <path d="M21 15v4a2 2 0 0 1-2 2h-4"/>
                    </svg>
                  )}
                </button>
                <button
                  onClick={() => {
                    if (pipActive) {
                      // Keep the PiP window alive; just hide the modal chrome
                      setShowModalUI(false);
                    } else {
                      if (videoModal?.video_url) {
                        /* blob cleanup no longer needed — video uses streaming URL */
                      }
                      setVideoModal(null);
                      setShowModalUI(false);
                    }
                  }}
                  style={{
                    background:"transparent",border:"none",cursor:"pointer",
                    fontSize:22,lineHeight:1,color:textSub,padding:"0 4px",
                  }}
                >×</button>
              </div>
            </div>
            {/* 2026-05-23 — Floating control overlay in fullscreen mode.
                Operator wanted YouTube-style true fullscreen: header
                chrome hidden, only the video visible, with collapse +
                close buttons floating over the top-right corner. */}
            {videoExpanded && (
              <div style={{
                position:"absolute", top:14, right:14, zIndex:10,
                display:"flex", gap:8, alignItems:"center",
                background:"rgba(0,0,0,0.55)",
                borderRadius:8, padding:"4px 8px",
                backdropFilter:"blur(4px)",
              }}>
                <button
                  onClick={() => setVideoExpanded(false)}
                  title="Minimise video (Esc)"
                  style={{
                    background:"transparent",border:"1px solid rgba(255,255,255,0.4)",
                    cursor:"pointer", borderRadius:6, padding:"3px 8px",
                    color:"#fff",
                    display:"inline-flex", alignItems:"center", justifyContent:"center",
                  }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                    stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M9 3v4a2 2 0 0 1-2 2H3"/>
                    <path d="M15 3v4a2 2 0 0 0 2 2h4"/>
                    <path d="M9 21v-4a2 2 0 0 0-2-2H3"/>
                    <path d="M15 21v-4a2 2 0 0 1 2-2h4"/>
                  </svg>
                </button>
                <button
                  onClick={() => {
                    if (pipActive) {
                      setShowModalUI(false);
                    } else {
                      setVideoModal(null);
                      setShowModalUI(false);
                    }
                    setVideoExpanded(false);
                  }}
                  title="Close video"
                  style={{
                    background:"transparent",border:"none",cursor:"pointer",
                    fontSize:22, lineHeight:1, color:"#fff", padding:"0 6px",
                  }}
                >×</button>
              </div>
            )}
            {/* 2026-05-22 — Scrollable body so video + NG details + comments
                all fit in one 92vh modal even on smaller screens.
                2026-05-23 — In fullscreen mode the body bypasses scroll
                and the panels below the video are hidden (only video
                renders) so the experience matches a YouTube fullscreen. */}
            <div style={{
              flex: 1, minHeight: 0,
              overflowY: videoExpanded ? "hidden" : "auto",
              paddingRight: videoExpanded ? 0 : 4,
              display:"flex", flexDirection:"column",
            }}>
            {videoModal.loading ? (
              <div style={{
                padding:"32px 16px",textAlign:"center",
                color:textSub,fontSize:13,fontWeight:600,
                background:bgDeep,borderRadius:8,
              }}>
                Loading video…
              </div>
            ) : videoModal.video_url ? (
              <>
              <div style={{ position:"relative", width:"100%", lineHeight:0,
                             ...(videoExpanded ? { flex:1, minHeight:0, display:"flex" } : {}) }}>
              <video
                ref={videoElRef}
                autoPlay
                muted
                controls
                preload="auto"
                onLoadedMetadata={e => {
                  // Stash the video's actual duration so the header can
                  // surface a CT/DB mismatch (see "Part #N | ... s" block
                  // above).  Older collector bug: when an OK pulse was
                  // missed, the next caught pulse stored ct = real * 2/3/4.
                  // The video is extracted on a separate edge so its
                  // duration is the truth.  Showing both teaches the
                  // operator to trust the video.
                  const d = e.target?.duration;
                  if (d && isFinite(d) && d > 0) {
                    setVideoModal(m => m ? { ...m, _video_duration: d } : m);
                  }
                  // 2026-05-27 — Playback rate stays 1.0 (real-time).
                  // The earlier "slow" feeling was actually the
                  // server-side render latency, which is now fixed by
                  // switching NVENC to p1 preset on the backend.
                }}
                onLoadedData={e => { e.target.muted = false; e.target.play().catch(()=>{}); }}
                onClick={e => { e.target.paused ? e.target.play() : e.target.pause(); }}
                controlsList="nofullscreen nodownload"
                onError={e => {
                  // Video extraction on NF2 takes 10-15s after cycle end.
                  // If user clicks immediately, file may not exist yet.
                  // Retry up to 5 times with 3s gap before giving up.
                  //
                  // Log full error info for TV-floor diagnosis — older
                  // Samsung Tizen / LG WebOS browsers sometimes fail
                  // silently with no console output, so we capture the
                  // MediaError code on the modal state itself for the
                  // operator to read.
                  const me   = e.target?.error;
                  const code = me?.code;
                  const codeStr = ({1:"ABORTED",2:"NETWORK",3:"DECODE",4:"SRC_NOT_SUPPORTED"}[code]) || `err${code}`;
                  console.warn("[CT-VIDEO] error", { code, codeStr, msg: me?.message, src: e.target?.src });
                  const retries = (videoModal?._retries || 0);
                  if (retries < 5) {
                    const src = videoModal?.video_url;
                    setTimeout(() => {
                      setVideoModal(m => m ? { ...m, _retries: retries + 1,
                        _lastErr: codeStr,
                        video_url: src ? src + (src.includes("&r=") ? "" : "") + "&r=" + Date.now() : src } : m);
                    }, 3000);
                  } else {
                    // After 5 retries — surface the actual reason so the
                    // operator can decide between "not ready yet" (retry
                    // later) vs "codec/browser issue" (open in PC browser).
                    setVideoModal(m => m ? {
                      ...m,
                      video_url: null,
                      error: code === 4
                        ? "This TV browser can't decode the video (try on a PC)"
                        : code === 2
                          ? "Network error reaching camera server — check LAN"
                          : code === 3
                            ? "Video file is corrupt or being written — try again"
                            : "Video not available for this cycle",
                      _lastUrl: m.video_url,
                    } : m);
                  }
                }}
                onEnterPictureInPicture={() => setPipActive(true)}
                onLeavePictureInPicture={() => {
                  setPipActive(false);
                  // If the user had closed the modal and was relying on PiP
                  // to keep watching, dispose of everything when PiP ends.
                  setVideoModal(m => {
                    if (!showModalUI) {
                      /* blob cleanup no longer needed — video uses streaming URL */
                      return null;
                    }
                    return m;
                  });
                }}
                style={videoExpanded
                  ? { width:"100%", height:"100%", flex:1, minHeight:0, borderRadius:0, background:"#000", display:"block", objectFit:"contain" }
                  : { width:"100%", borderRadius:8, maxHeight:"42vh", background:"#000", display:"block", objectFit:"contain" }}
                src={videoModal.video_url}
              />
              {/* 2026-05-21 — Big centered play/pause overlay.
                  Video loops automatically (loop attr); operator can
                  tap anywhere on video OR this 96 px target to toggle
                  play/pause.  Modal close = X button or backdrop click. */}
              {/* 2026-05-22 — Center big-play overlay removed per
                  operator request "video pe hover karne pe center me jo
                  aata hai isko hata".  Native controls strip at the
                  bottom + ↺ replay button at top-left cover the
                  play/pause/seek workflow cleanly. */}
              {/* 2026-05-21-r2 — Replay-from-start button.  Operator
                  spec: "replay from beginning ka bhi button aana
                  chahiye".  Top-left corner of the video, always
                  visible so operator can re-watch any cycle instantly
                  without waiting for the natural loop boundary or
                  dragging the native scrubber to 0. */}
              <button
                className="fs-replay"
                title="Replay from start"
                onClick={(e) => {
                  e.stopPropagation();
                  const v = videoElRef.current;
                  if (!v) return;
                  try { v.currentTime = 0; } catch {}
                  v.play().catch(() => {});
                }}
                style={{
                  position:"absolute", top:12, left:12,
                  width:44, height:44, borderRadius:"50%",
                  border:"2px solid rgba(255,255,255,0.7)",
                  background:"rgba(0,0,0,0.55)",
                  color:"#fff", cursor:"pointer",
                  display:"flex", alignItems:"center", justifyContent:"center",
                  pointerEvents:"auto",
                  backdropFilter:"blur(4px)",
                  zIndex:6,
                  transition:"opacity .2s, transform .15s",
                  opacity:0.85,
                }}
                onMouseEnter={(e)=>{ e.currentTarget.style.opacity="1"; e.currentTarget.style.transform="scale(1.08)"; }}
                onMouseLeave={(e)=>{ e.currentTarget.style.opacity="0.85"; e.currentTarget.style.transform="scale(1)"; }}
              >
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" strokeWidth="2.4"
                     strokeLinecap="round" strokeLinejoin="round">
                  {/* Circular replay arrow */}
                  <path d="M3 12a9 9 0 1 0 3-6.7" />
                  <polyline points="3 4 3 10 9 10" />
                </svg>
              </button>
              </div>
              {/* 2026-05-21 — NG details panel.  Only renders when the
                  current cycle is NG (videoModal.is_ng).  Shows the
                  machine-derived reason (auto from cycle CT + sub-machine
                  logs) AND lets the line leader save their exact remark
                  after physical inspection.  Sits between video and the
                  general comments thread.
                  2026-05-23 — Hidden in fullscreen mode (YouTube style). */}
              {!videoExpanded && videoModal.is_ng && videoModal.part_code && (
                <FsNgDetailsPanel
                  lineId={lineId}
                  partCode={String(videoModal.part_code).replace(/:$/, "")}
                  border={border}
                  text={text}
                  textSub={textSub}
                  textMut={textMut}
                  bgDeep={bgDeep}
                />
              )}
              {/* 2026-05-24 — Process-wise NG remarks panel REMOVED from
                  Fullscreen.  Operator: "main fullscreen me sirf final
                  ki details rkhni h, process wise remarks ka wallboard
                  wale page me dalne ko bola tha".  Panel still lives in
                  WallboardLeft.jsx for sub-machine NG video modal. */}
              {/* 2026-05-21 — Per-cycle Comments panel (same as
                  WallboardLeft).  Fullscreen modal always shows main-PLC
                  (Final Inspection) cycles, so no isMain gate needed —
                  every video here gets a comments thread.  Keyed by
                  part_code so part-code search surfaces the same notes.
                  2026-05-23 — Hidden in fullscreen mode (YouTube style).
                  2026-05-27 — Operator: "remarks ka option sab part me
                  lga".  Removed the `videoModal.part_code` gate so the
                  panel renders for every cycle, even when the scanner
                  hasn't populated a part_code (test pattern, scanner
                  failure, etc.).  Falls back to `cycle_<seq>` as the
                  storage key so each cycle still has a stable address. */}
              {!videoExpanded && (
                <FsCycleCommentsPanel
                  lineId={lineId}
                  partCode={String(videoModal.part_code || `cycle_${videoModal.cycle_seq}`).replace(/:$/, "")}
                  isFallbackKey={!videoModal.part_code}
                  isNg={!!videoModal.is_ng}
                  border={border}
                  text={text}
                  textSub={textSub}
                  textMut={textMut}
                />
              )}
              </>
            ) : (
              <div style={{
                padding:"32px 16px",textAlign:"center",
                color:textSub,fontSize:13,fontWeight:600,
                background:bgDeep,borderRadius:8,
              }}>
                <div>{videoModal.error || "Video not available for this cycle"}</div>
                {/* Diagnostic + fallback for shop-floor TV operators —
                    let them open the raw video URL in a new tab on a PC
                    when the TV browser can't decode it. */}
                {videoModal._lastUrl && (
                  <a href={videoModal._lastUrl} target="_blank" rel="noreferrer"
                     style={{ display:"inline-block", marginTop:10,
                              fontSize:11, color:STATUS_CLR["SETUP"],
                              textDecoration:"underline" }}>
                    Open video URL in new tab
                  </a>
                )}
              </div>
            )}
            </div>{/* end scrollable body */}
          </div>
        </div>
      ), document.body)}

      {/* ── HOURLY LOSS BREAKUP MODAL ──
          Triggered by clicking the Loss Distribution panel.  Renders a
          grid: rows = hourly slots, columns = loss buckets, cells =
          minutes spent in that loss type during that slot.  Plus a
          totals row matching the panel's shift-wide values. */}
      {/* 2026-05-21 — NG LIST MODAL.  Triggered by clicking the NG
          count cell in the slot table.  Renders a table of all NG
          parts in that slot with per-row editable leader remark. */}
      {/* 2026-05-22 — Loss-Remark editor.  Pops up over the Hourly
          Loss Breakup modal when production clicks a loss-time cell. */}
      {lossRemarkModal && (
        <LossRemarkModal
          lineId={lineId}
          payload={lossRemarkModal}
          onClose={() => setLossRemarkModal(null)}
          border={border}
          bgDeep={bgDeep}
          text={text}
          textSub={textSub}
          textMut={textMut}
          overlayPosStyle={overlayPosStyle}
        />
      )}

      {ngListModal && (
        <NgListModal
          lineId={lineId}
          date={ngListModal.date}
          slotLabel={ngListModal.slot}
          onClose={() => setNgListModal(null)}
          onPlayVideo={(row) => openCycleVideo({
            cycle_seq: row.cycle_seq,
            part_code: row.part_code,
            ct_value:  row.ct_value,
            ts:        row.ts,
            is_ng:     true,
          })}
          border={border}
          bgDeep={bgDeep}
          text={text}
          textSub={textSub}
          textMut={textMut}
          overlayPosStyle={overlayPosStyle}
        />
      )}

      {lossModalOpen && (
        <div
          onClick={() => setLossModalOpen(false)}
          style={{
            ...overlayPosStyle, zIndex:9999,
            background:"rgba(0,0,0,.65)", backdropFilter:"blur(3px)",
            display:"flex", alignItems:"center", justifyContent:"center",
            padding:20, animation:"fadeIn .15s ease",
          }}
        >
          <div onClick={e => e.stopPropagation()}
               style={{
                 background:bgCard, color:text,
                 border:`1px solid ${border}`, borderRadius:14,
                 padding:"18px 22px", width:"100%", maxWidth:1100,
                 maxHeight:"88vh", display:"flex", flexDirection:"column",
                 boxShadow:"0 24px 70px rgba(0,0,0,.6)",
                 fontFamily:"'Barlow',sans-serif",
               }}>
            <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:14}}>
              <div>
                <div style={{fontSize:9,color:textMut,fontWeight:800,letterSpacing:".1em",textTransform:"uppercase"}}>
                  Hourly Loss Breakup
                </div>
                <div style={{fontSize:18,fontWeight:800,color:text,fontFamily:"'Barlow Condensed',sans-serif"}}>
                  {line?.line_name} · {rt?.shift_name||"—"} Shift · {rt?.record_date||""}
                </div>
              </div>
              <button onClick={() => setLossModalOpen(false)}
                      style={{background:"transparent",border:`1px solid ${border}`,
                              color:textSub,fontSize:18,padding:"4px 12px",
                              borderRadius:6,cursor:"pointer",fontWeight:700}}
                      title="Close">
                ×
              </button>
            </div>

            {lossLoading && !lossBreakup ? (
              <div style={{padding:"48px 0",textAlign:"center",color:textMut,fontStyle:"italic"}}>
                Loading hourly breakup…
              </div>
            ) : !lossBreakup || (lossBreakup.slots||[]).length === 0 ? (
              <div style={{padding:"48px 0",textAlign:"center",color:textMut,fontStyle:"italic"}}>
                No hourly slots configured or no status events for this shift yet.
              </div>
            ) : (
              <div style={{flex:1, overflowY:"auto", overflowX:"auto"}}>
                <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                  <thead>
                    <tr style={{borderBottom:`2px solid ${border}`}}>
                      <th style={{padding:"8px 12px",textAlign:"left",fontSize:9,fontWeight:800,
                                  letterSpacing:".08em",color:textMut,textTransform:"uppercase",position:"sticky",left:0,background:bgCard,zIndex:1}}>
                        Slot
                      </th>
                      {LOSSES.map(c => (
                        <th key={c.key} style={{padding:"8px 10px",textAlign:"right",fontSize:9,fontWeight:800,
                                                letterSpacing:".08em",color:c.color,textTransform:"uppercase",whiteSpace:"nowrap"}}>
                          <span style={{display:"inline-flex",alignItems:"center",gap:4,justifyContent:"flex-end"}}>
                            <span style={{width:6,height:6,borderRadius:2,background:c.color,display:"inline-block"}}/>
                            {c.label}
                          </span>
                        </th>
                      ))}
                      <th style={{padding:"8px 12px",textAlign:"right",fontSize:9,fontWeight:800,
                                  letterSpacing:".08em",color:textMut,textTransform:"uppercase",whiteSpace:"nowrap"}}>
                        Total
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {lossBreakup.slots.map((s, i) => (
                      <tr key={i} style={{borderBottom:`1px solid ${border}`}}>
                        {/* 2026-05-22 — Removed duplicate small "HH:MM – HH:MM"
                            line below slot_label.  slot_label already encodes
                            the range (e.g. "08:30-09:30") so the second line
                            was visual noise. */}
                        <td style={{padding:"8px 12px",fontWeight:700,color:text,whiteSpace:"nowrap",
                                    position:"sticky",left:0,background:bgCard}}>
                          {s.slot_label}
                        </td>
                        {LOSSES.map(c => {
                          const sec = s[`loss_${c.key}`] || 0;
                          const clickable = sec > 0;
                          return (
                            <td key={c.key}
                                onClick={clickable
                                          ? () => setLossRemarkModal({
                                              date:       lossBreakup.date || new Date().toISOString().slice(0,10),
                                              shift_name: lossBreakup.shift_name || "",
                                              slot_label: s.slot_label,
                                              loss_type:  c.key,
                                              loss_label: c.label,
                                              loss_color: c.color,
                                              loss_secs:  sec,
                                            })
                                          : undefined}
                                title={clickable ? "Click to add/edit remark for this loss" : ""}
                                style={{padding:"8px 10px",textAlign:"right",
                                         fontFamily:"monospace",
                                         color:sec>0?c.color:textMut,
                                         fontWeight:sec>0?700:400,
                                         cursor: clickable ? "pointer" : "default",
                                         textDecoration: clickable ? "underline dotted" : "none",
                                         textUnderlineOffset: 2}}>
                              {sec>0 ? fmtSec(sec) : "—"}
                            </td>
                          );
                        })}
                        <td style={{padding:"8px 12px",textAlign:"right",
                                    fontFamily:"monospace",fontWeight:800,
                                    color:s.total_loss>0?STATUS_CLR["BREAKDOWN"]:textMut}}>
                          {s.total_loss>0 ? fmtSec(s.total_loss) : "—"}
                        </td>
                      </tr>
                    ))}
                    {/* Totals row — should match the panel's shift values */}
                    <tr style={{borderTop:`2px solid ${border}`,background:D?"#0a1322":"#f1f5f9"}}>
                      <td style={{padding:"10px 12px",fontWeight:800,color:text,whiteSpace:"nowrap",
                                  position:"sticky",left:0,background:D?"#0a1322":"#f1f5f9"}}>
                        TOTAL
                      </td>
                      {LOSSES.map(c => {
                        const sec = lossBreakup.totals?.[`loss_${c.key}`] || 0;
                        return (
                          <td key={c.key} style={{padding:"10px 10px",textAlign:"right",
                                                   fontFamily:"monospace",fontWeight:800,
                                                   color:sec>0?c.color:textMut}}>
                            {sec>0 ? fmtSec(sec) : "—"}
                          </td>
                        );
                      })}
                      <td style={{padding:"10px 12px",textAlign:"right",
                                  fontFamily:"monospace",fontWeight:900,fontSize:13,
                                  color:STATUS_CLR["BREAKDOWN"]}}>
                        {fmtSec(lossBreakup.totals?.total_loss || 0)}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            )}

          </div>
        </div>
      )}

      {/* ── POKA-YOKE HOURLY BREAKDOWN MODAL ─────────────────────
          Mirror of the Loss modal — same overlay pattern, same
          slot-rows table.  Cells show every PY failure logged in
          that slot's window today.  No failures = "✓ All set".
          Backdrop / × button closes. */}
      {pyModalOpen && (() => {
        // ── Server already builds episodes scoped to today's shift ──
        // /bypass-episodes returns { episodes, by_slot, slot_summary,
        //   shift_start, shift_end, merge_gap_sec }.
        // Server-side merge consolidates fragments < merge_gap_sec apart
        // (default 60 s) so collector polling noise doesn't paint a
        // 1-min real bypass as 10 tiny rows.
        const todayStr     = pyEvents?.date || rt?.record_date || new Date().toISOString().slice(0,10);
        const serverBySlot = pyEvents?.by_slot || {};
        const slotSummary  = pyEvents?.slot_summary || {};

        // Group events by hourly slot + classify slot relative to NOW
        // so future slots don't masquerade as "✓ All set" (operator
        // feedback 2026-05-16: a 14:05-15:05 row showing All set at
        // 08:35 makes no sense — slot hasn't even started).
        //
        // 2026-05-16 (afternoon) — Operator hit a contradiction: the
        // POKA card on the dashboard showed "⚠ 3 FAILED" but every
        // slot in this modal said "✓ All set".  Root cause: the card
        // reads `pokaStatus` (live `/poka-yoke/live/{lineId}` state
        // — bypassed events in last 8 h) while the modal reads from
        // `pyEvents.events` (historical events log filtered by today's
        // shift window).  When backend filtering differs even slightly
        // (rule_type quirks, timezone, ack flag), the two diverge.
        // Fix: ALSO fold live `pokaStatus` bypasses into the slot they
        // started in (using `last_bypass_at`) so the modal is always
        // consistent with the card.
        const slotsWithEvents = allSlots.map(s => {
          const ssMin     = toMin(s.start_time);
          const seMin     = toMin(s.end_time);
          const slotWraps = seMin <= ssMin;
          const ssRel = ssMin >= sStart ? ssMin - sStart : ssMin + 1440 - sStart;
          const seRel = ssRel + (slotWraps ? seMin + 1440 - ssMin : seMin - ssMin);
          const isFuture  = sElapsed <  ssRel;
          const isCurrent = sElapsed >= ssRel && sElapsed < seRel;

          // Server-built episodes (real py_name + start/end + slot already
          // attributed by /bypass-episodes endpoint).
          const episodes = (serverBySlot[s.slot_label] || []).map(ep => ({
            ...ep,
            key: `${ep.py_no}_${ep.start_at}`,
          }));

          // Fold in LIVE bypassed PYs from `pokaStatus` for the CURRENT
          // slot — these may not be in the server response yet if the
          // collector hasn't flushed to DB.  Skip ones already open in
          // the server episode list.
          if (isCurrent) {
            (pokaStatus || []).forEach(p => {
              if (!p.is_bypassed) return;
              const py_no = p.poka_yoke_no || p.py_no || p.id;
              const alreadyOpen = episodes.some(
                ep => ep.py_no === py_no && ep.end_at == null
              );
              if (alreadyOpen) return;
              const startIso = p.last_bypass_at
                              || p.bypassed_at
                              || new Date().toISOString();
              const ctxName = p.bypass_context?.py_name;
              const py_name = ctxName
                           || p.poka_yoke_name
                           || p.py_name
                           || p.description
                           || py_no
                           || "Unknown PY";
              episodes.push({
                key:       `live_${py_no}`,
                py_no,
                py_name,
                alert:     "CRITICAL",
                start_at:  startIso,
                end_at:    null,
                hit_count: 1,
                live:      true,
                slot:      s.slot_label,
              });
            });
          }

          episodes.sort((a, b) => new Date(a.start_at) - new Date(b.start_at));

          return {
            slot:       s,
            episodes,
            distinctPy: new Set(episodes.map(e => e.py_no)).size,
            total:      episodes.length,
            phase:      isFuture ? "future" : isCurrent ? "current" : "past",
          };
        });

        const shiftFailTotal = slotsWithEvents.reduce((acc,s) => acc + s.total, 0);

        return (
          <div
            onClick={() => setPyModalOpen(false)}
            style={{
              ...overlayPosStyle, zIndex:9999,
              background:"rgba(0,0,0,.65)", backdropFilter:"blur(3px)",
              display:"flex", alignItems:"center", justifyContent:"center",
              padding:20, animation:"fadeIn .15s ease",
            }}
          >
            <div onClick={e => e.stopPropagation()}
                 style={{
                   background:bgCard, color:text,
                   border:`1px solid ${border}`, borderRadius:14,
                   padding:"18px 22px", width:"100%", maxWidth:1100,
                   maxHeight:"88vh", display:"flex", flexDirection:"column",
                   boxShadow:"0 24px 70px rgba(0,0,0,.6)",
                   fontFamily:"'Barlow',sans-serif",
                 }}>
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:14}}>
                <div>
                  <div style={{fontSize:9,color:textMut,fontWeight:800,letterSpacing:".1em",textTransform:"uppercase"}}>
                    Hourly Poka-Yoke Failures
                  </div>
                  <div style={{fontSize:18,fontWeight:800,color:text,fontFamily:"'Barlow Condensed',sans-serif"}}>
                    {line?.line_name} · {rt?.shift_name||"—"} Shift · {todayStr}
                  </div>
                  <div style={{fontSize:11,color:textMut,marginTop:2}}>
                    {shiftFailTotal === 0
                      ? <span style={{color:STATUS_CLR["RUNNING"],fontWeight:700}}>✓ Zero failures this shift</span>
                      : <>Total failure events: <strong style={{color:STATUS_CLR["BREAKDOWN"]}}>{shiftFailTotal}</strong></>}
                  </div>
                </div>
                <button onClick={() => setPyModalOpen(false)}
                        style={{background:"transparent",border:`1px solid ${border}`,
                                color:textSub,fontSize:18,padding:"4px 12px",
                                borderRadius:6,cursor:"pointer",fontWeight:700}}
                        title="Close">
                  ×
                </button>
              </div>

              {pyEventsLoading && !pyEvents ? (
                <div style={{padding:"48px 0",textAlign:"center",color:textMut,fontStyle:"italic"}}>
                  Loading hourly events…
                </div>
              ) : slotsWithEvents.length === 0 ? (
                <div style={{padding:"48px 0",textAlign:"center",color:textMut,fontStyle:"italic"}}>
                  No hourly slots configured for this shift.
                </div>
              ) : (
                <div style={{flex:1, overflowY:"auto", overflowX:"auto"}}>
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                    <thead>
                      <tr style={{borderBottom:`2px solid ${border}`}}>
                        <th style={{padding:"8px 12px",textAlign:"left",fontSize:9,fontWeight:800,
                                    letterSpacing:".08em",color:textMut,textTransform:"uppercase",
                                    position:"sticky",left:0,background:bgCard,zIndex:1,width:160}}>
                          Slot
                        </th>
                        <th style={{padding:"8px 10px",textAlign:"center",fontSize:9,fontWeight:800,
                                    letterSpacing:".08em",color:textMut,textTransform:"uppercase",width:80}}>
                          Status
                        </th>
                        <th style={{padding:"8px 10px",textAlign:"left",fontSize:9,fontWeight:800,
                                    letterSpacing:".08em",color:STATUS_CLR["BREAKDOWN"],textTransform:"uppercase"}}>
                          Failed Poka-Yoke (read-only)
                        </th>
                        <th style={{padding:"8px 10px",textAlign:"right",fontSize:9,fontWeight:800,
                                    letterSpacing:".08em",color:textMut,textTransform:"uppercase",width:140}}
                            title="Total bypass downtime in this slot · longest single episode · ongoing count">
                          Slot Analysis
                        </th>
                        <th style={{padding:"8px 12px",textAlign:"right",fontSize:9,fontWeight:800,
                                    letterSpacing:".08em",color:textMut,textTransform:"uppercase",width:70}}>
                          Episodes
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {slotsWithEvents.map((s,i) => {
                        const clean    = s.total === 0;
                        const isFuture = s.phase === "future";
                        const isCurrent= s.phase === "current";
                        const rowBg = isFuture
                            ? "transparent"
                            : !clean ? `${STATUS_CLR["BREAKDOWN"]}0a`
                            : isCurrent ? `${STATUS_CLR["RUNNING"]}0d`
                            : "transparent";
                        const labelColor = isFuture ? textMut : text;
                        const opacity    = isFuture ? 0.55 : 1;
                        // Format HH:MM:SS for an ISO timestamp
                        const fmtClock = (iso) => {
                          if (!iso) return "—";
                          const t = new Date(iso);
                          if (isNaN(t.getTime())) return "—";
                          return t.toLocaleTimeString("en-IN",
                            {hour12:false,hour:"2-digit",minute:"2-digit",second:"2-digit"});
                        };
                        // Duration helper "Xm Ys" or "Hh Mm"
                        const fmtDur = (startIso, endIso) => {
                          const start = startIso ? new Date(startIso) : null;
                          const end   = endIso   ? new Date(endIso)   : new Date();
                          if (!start || isNaN(start.getTime())) return "—";
                          const sec = Math.max(0, Math.floor((end - start) / 1000));
                          const h = Math.floor(sec/3600);
                          const m = Math.floor((sec%3600)/60);
                          const sc = sec % 60;
                          if (h > 0) return `${h}h ${m}m`;
                          if (m > 0) return `${m}m ${sc}s`;
                          return `${sc}s`;
                        };
                        return (
                          <tr key={i} style={{borderBottom:`1px solid ${border}`,
                            background: rowBg, opacity, verticalAlign:"top"}}>
                            <td style={{padding:"10px 12px",fontWeight:700,color:labelColor,whiteSpace:"nowrap",
                                        position:"sticky",left:0,background: rowBg === "transparent" ? bgCard : rowBg}}>
                              {s.slot.slot_label}
                              {isCurrent && (
                                <span style={{marginLeft:8,fontSize:9,fontWeight:800,
                                  color:STATUS_CLR["RUNNING"],letterSpacing:".08em"}}>
                                  ● NOW
                                </span>
                              )}
                            </td>
                            <td style={{padding:"10px 10px",textAlign:"center"}}>
                              {isFuture ? (
                                <span style={{fontSize:11,fontWeight:700,color:textMut,fontStyle:"italic"}}>
                                  Not yet
                                </span>
                              ) : clean ? (
                                <span style={{fontSize:11,fontWeight:700,color:STATUS_CLR["RUNNING"]}}>
                                  ✓ {isCurrent ? "All set so far" : "All set"}
                                </span>
                              ) : (
                                <span style={{fontSize:11,fontWeight:800,color:STATUS_CLR["BREAKDOWN"]}}>
                                  ⚠ {s.distinctPy} PY
                                </span>
                              )}
                            </td>
                            <td style={{padding:"10px 10px"}}>
                              {isFuture || clean ? (
                                <span style={{color:textMut,fontStyle:"italic",fontSize:11}}>—</span>
                              ) : (
                                <div style={{display:"flex",flexDirection:"column",gap:4}}>
                                  {s.episodes.map((ep) => {
                                    // SPAN-AWARE view: each episode entry now carries
                                    // slot_segment_from / slot_segment_to which is the
                                    // intersection of the episode with THIS slot's
                                    // window — so a 13:03→ongoing bypass shows in
                                    // 11:30-13:05 as "13:03→13:05 (2m, started here)"
                                    // and ALSO in 14:05-15:05 as
                                    // "14:05→ongoing (since 13:03)".
                                    const segFrom   = ep.slot_segment_from || ep.start_at;
                                    const segTo     = ep.slot_segment_to;
                                    const ongoingNow= ep.ongoing_now || (ep.end_at == null && !ep.ends_here);
                                    const startsHere= ep.starts_here !== false;
                                    const endsHere  = ep.ends_here   === true;
                                    const isOngoing = ep.end_at == null;
                                    const isCritical= ep.alert === "CRITICAL";
                                    const color = ongoingNow || isCritical
                                      ? STATUS_CLR["BREAKDOWN"]
                                      : STATUS_CLR["MATERIAL WAIT"];
                                    const segDur = (() => {
                                      try {
                                        const a = new Date(segFrom);
                                        const b = segTo ? new Date(segTo) : new Date();
                                        const sec = Math.max(0, Math.floor((b - a)/1000));
                                        const m = Math.floor(sec/60);
                                        const sc = sec % 60;
                                        if (m >= 60) {
                                          const h = Math.floor(m/60);
                                          return `${h}h ${m%60}m`;
                                        }
                                        if (m > 0) return `${m}m ${sc}s`;
                                        return `${sc}s`;
                                      } catch { return "—"; }
                                    })();
                                    const stateLabel = ongoingNow ? "ongoing"
                                                      : endsHere   ? fmtClock(ep.end_at)
                                                      : fmtClock(segTo);
                                    return (
                                      <div key={ep.key + "_" + (segFrom || "")}
                                        title={`${ep.py_name}\nThis slot: ${fmtClock(segFrom)} → ${stateLabel} (${segDur})\nFull episode: ${fmtClock(ep.start_at)} → ${isOngoing ? "ongoing" : fmtClock(ep.end_at)}${ep.hit_count > 1 ? `\n${ep.hit_count} raw detections (merged)` : ""}`}
                                        style={{
                                          display:"flex", alignItems:"center", gap:8,
                                          padding:"4px 10px", borderRadius:6,
                                          background: `${color}18`,
                                          border:`1px solid ${color}55`,
                                          fontSize:11, lineHeight:1.3,
                                        }}>
                                        <span style={{fontSize:13}}>{ongoingNow ? "🔴" : "⚠"}</span>
                                        <span style={{fontWeight:800, color, minWidth:140, flexShrink:0}}>
                                          {ep.py_name}
                                        </span>
                                        <span style={{color:textSub, fontFamily:"monospace", fontSize:10.5}}>
                                          {fmtClock(segFrom)}
                                          <span style={{color:textMut, margin:"0 4px"}}>→</span>
                                          {ongoingNow
                                            ? <span style={{color:STATUS_CLR["BREAKDOWN"], fontWeight:800}}>ongoing</span>
                                            : fmtClock(segTo)}
                                        </span>
                                        <span style={{color:textMut, fontSize:10}}>
                                          ({segDur})
                                        </span>
                                        {!startsHere && (
                                          <span style={{color:STATUS_CLR["MATERIAL WAIT"], fontSize:9,
                                            padding:"1px 6px", borderRadius:3,
                                            background:`${STATUS_CLR["MATERIAL WAIT"]}22`,
                                            border:`1px solid ${STATUS_CLR["MATERIAL WAIT"]}44`}}>
                                            since {fmtClock(ep.start_at)}
                                          </span>
                                        )}
                                        {/* Raw detection count (×N) removed per operator
                                            request 2026-05-16 — duration already tells the
                                            story; hit_count was just polling noise. */}
                                      </div>
                                    );
                                  })}
                                </div>
                              )}
                            </td>
                            {/* SLOT ANALYSIS — total bypass downtime + longest episode */}
                            <td style={{padding:"10px 10px", textAlign:"right",
                                        fontFamily:"monospace", fontSize:11}}>
                              {(() => {
                                if (isFuture || clean) {
                                  return <span style={{color:textMut, fontStyle:"italic"}}>—</span>;
                                }
                                const sum = slotSummary[s.slot.slot_label] || {};
                                const totalSec = sum.total_bypass_sec || 0;
                                const longSec  = sum.longest_sec      || 0;
                                const ongoing  = sum.ongoing_count    || 0;
                                const distinct = sum.distinct_py      || 0;
                                const fmtDurShort = (sec) => {
                                  const m = Math.floor(sec/60);
                                  const sc = Math.round(sec % 60);
                                  if (m >= 60) {
                                    const h = Math.floor(m/60);
                                    const mm = m % 60;
                                    return `${h}h ${mm}m`;
                                  }
                                  if (m > 0) return `${m}m ${sc}s`;
                                  return `${sc}s`;
                                };
                                return (
                                  <div style={{display:"flex", flexDirection:"column", gap:2, alignItems:"flex-end"}}>
                                    <div style={{color:STATUS_CLR["BREAKDOWN"], fontWeight:900, fontSize:12}}
                                         title="Total bypass downtime in this slot">
                                      {fmtDurShort(totalSec)}
                                    </div>
                                    <div style={{color:textMut, fontSize:9}}
                                         title="Longest single bypass episode in this slot">
                                      longest {fmtDurShort(longSec)}
                                    </div>
                                    {ongoing > 0 && (
                                      <div style={{color:STATUS_CLR["BREAKDOWN"], fontSize:9, fontWeight:800}}
                                           title="Ongoing bypasses (no PASS recovery yet)">
                                        🔴 {ongoing} ongoing
                                      </div>
                                    )}
                                  </div>
                                );
                              })()}
                            </td>
                            <td style={{padding:"10px 12px",textAlign:"right",
                                        fontFamily:"monospace",fontWeight:800,fontSize:13,
                                        color: isFuture ? textMut
                                             : clean   ? textMut
                                             : STATUS_CLR["BREAKDOWN"]}}>
                              {isFuture ? "—" : clean ? "—" : s.total}
                            </td>
                          </tr>
                        );
                      })}
                      {/* Total row */}
                      <tr style={{borderTop:`2px solid ${border}`,background:D?"#0a1322":"#f1f5f9"}}>
                        <td style={{padding:"10px 12px",fontWeight:800,color:text,whiteSpace:"nowrap",
                                    position:"sticky",left:0,background:D?"#0a1322":"#f1f5f9"}}>
                          TOTAL
                        </td>
                        <td style={{padding:"10px 10px",textAlign:"center"}}/>
                        <td style={{padding:"10px 10px",fontSize:11,color:textMut,fontStyle:"italic"}}>
                          {shiftFailTotal === 0 ? "Zero PY failures this shift" : "Aggregated failures across all slots"}
                        </td>
                        {/* Shift-wide bypass downtime aggregate */}
                        <td style={{padding:"10px 10px",textAlign:"right",
                                    fontFamily:"monospace",fontWeight:900,fontSize:13,
                                    color: STATUS_CLR["BREAKDOWN"]}}>
                          {(() => {
                            const totalSec = Object.values(slotSummary)
                              .reduce((acc, b) => acc + (b.total_bypass_sec || 0), 0);
                            if (totalSec <= 0) return <span style={{color:textMut}}>—</span>;
                            const h = Math.floor(totalSec/3600);
                            const m = Math.floor((totalSec%3600)/60);
                            const sc = Math.round(totalSec % 60);
                            return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${sc}s` : `${sc}s`;
                          })()}
                        </td>
                        <td style={{padding:"10px 12px",textAlign:"right",
                                    fontFamily:"monospace",fontWeight:900,fontSize:13,
                                    color: shiftFailTotal === 0 ? STATUS_CLR["RUNNING"] : STATUS_CLR["BREAKDOWN"]}}>
                          {shiftFailTotal}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              )}

            </div>
          </div>
        );
      })()}

      {/* ── SENSOR STATUS MODAL — ALL SENSORS for current model ─
          2026-05-13 (v2) — operator updated spec:
            "saare sensors jo uss model mein work krta hai unka
             status dikhe like they are working or not"
          So the modal now shows EVERY sensor configured for the
          currently-running model with a clear green/red status pill.
          Stuck (desensed) rows appear first in red with last-toggle
          time + how long they've been stuck.  Working (sensing) rows
          appear below in green.
          Auto-refreshes every 10 s with the parent's sensor poll. */}
      {sensorModalOpen && (() => {
        const entries = sensorSweep?.entries || [];
        const stuck   = entries.filter(s => (s.status || "").toLowerCase() === "stuck");
        const unknown = entries.filter(s => (s.status || "").toLowerCase() === "unknown");
        const alive   = entries.filter(s => (s.status || "").toLowerCase() === "alive");
        const health  = (sensorSweep?.health || "").toUpperCase();
        const sweptAt = sensorSweep?.swept_at;
        const sweptAtStr = sweptAt
          ? new Date(sweptAt).toLocaleTimeString("en-IN",{hour12:false})
          : "—";
        const nowStr = new Date().toLocaleTimeString("en-IN",{hour12:false,hour:"2-digit",minute:"2-digit",second:"2-digit"});

        // Format an ISO timestamp into HH:MM:SS + "Xh Ym Zs" duration since then
        const fmtFromTo = (iso) => {
          if (!iso) return null;
          const t = new Date(iso);
          if (isNaN(t.getTime())) return null;
          const nowDt = new Date();
          const diffSec = Math.max(0, Math.floor((nowDt - t) / 1000));
          const time = t.toLocaleTimeString("en-IN",{hour12:false,hour:"2-digit",minute:"2-digit",second:"2-digit"});
          const h = Math.floor(diffSec/3600);
          const m = Math.floor((diffSec%3600)/60);
          const s = diffSec%60;
          let dur;
          if (h > 0)      dur = `${h}h ${m}m ${s}s`;
          else if (m > 0) dur = `${m}m ${s}s`;
          else            dur = `${s}s`;
          return { time, dur };
        };

        return (
          <div
            onClick={() => setSensorModalOpen(false)}
            style={{
              ...overlayPosStyle, zIndex:9999,
              background:"rgba(0,0,0,.65)", backdropFilter:"blur(3px)",
              display:"flex", alignItems:"center", justifyContent:"center",
              padding:20, animation:"fadeIn .15s ease",
            }}
          >
            <div onClick={e => e.stopPropagation()}
                 style={{
                   background:bgCard, color:text,
                   border:`1px solid ${border}`, borderRadius:14,
                   padding:"18px 22px", width:"100%", maxWidth:1100,
                   maxHeight:"88vh", display:"flex", flexDirection:"column",
                   boxShadow:"0 24px 70px rgba(0,0,0,.6)",
                   fontFamily:"'Barlow',sans-serif",
                 }}>
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:14}}>
                <div>
                  <div style={{fontSize:9,color:textMut,fontWeight:800,letterSpacing:".1em",textTransform:"uppercase"}}>
                    Sensor Health — Current Model
                  </div>
                  <div style={{fontSize:18,fontWeight:800,color:text,fontFamily:"'Barlow Condensed',sans-serif"}}>
                    {line?.line_name}
                    {rt?.current_model_name && <span style={{ color:textMut, fontWeight:500 }}> · {rt.current_model_name}</span>}
                    {" · "}{entries.length} sensor{entries.length!==1?'s':''}
                  </div>
                  <div style={{fontSize:11,color:textMut,marginTop:2}}>
                    {entries.length === 0 ? (
                      <span style={{fontStyle:"italic"}}>
                        No Poka-Yokes are configured for the running model. Add one in Maintenance Panel → Poka Yoke.
                      </span>
                    ) : (
                      <span style={{display:"inline-flex", gap:14, flexWrap:"wrap"}}>
                        {alive.length > 0 && (
                          <span><strong style={{color:STATUS_CLR["RUNNING"]}}>✓ {alive.length}</strong> alive</span>
                        )}
                        {stuck.length > 0 && (
                          <span><strong style={{color:STATUS_CLR["BREAKDOWN"]}}>⚠ {stuck.length}</strong> stuck</span>
                        )}
                        {unknown.length > 0 && (
                          <span><strong style={{color:STATUS_CLR["MATERIAL_WAIT"]}}>⏳ {unknown.length}</strong> waiting</span>
                        )}
                        {" — "}
                        <span style={{fontWeight:800,
                                       color: health === "OK"       ? STATUS_CLR["RUNNING"]
                                            : health === "CRITICAL" ? STATUS_CLR["BREAKDOWN"]
                                            : health === "WARNING"  ? STATUS_CLR["MATERIAL_WAIT"]
                                            :                          textMut}}>
                          {health === "OK"       ? "Health OK"
                          : health === "CRITICAL" ? "Health Critical"
                          : health === "WARNING"  ? "Collector warming up"
                          :                          "—"}
                        </span>
                      </span>
                    )}
                  </div>
                  <div style={{fontSize:10,color:textMut,marginTop:1,fontFamily:"monospace"}}>
                    Last snapshot: {sweptAtStr}
                  </div>
                </div>
                <button onClick={() => setSensorModalOpen(false)}
                        style={{background:"transparent",border:`1px solid ${border}`,
                                color:textSub,fontSize:18,padding:"4px 12px",
                                borderRadius:6,cursor:"pointer",fontWeight:700}}
                        title="Close">
                  ×
                </button>
              </div>

              {/* Body — full list of every configured sensor with
                  status pill.  Stuck rows first (red bg), then working
                  rows (default bg) — operator can scan top-to-bottom
                  and act on the red ones first. */}
              {entries.length === 0 ? (
                <div style={{padding:"56px 0",textAlign:"center",color:textMut,fontStyle:"italic"}}>
                  Waiting for sensor sweep from collector…
                  <div style={{fontSize:11,marginTop:8,opacity:.7}}>
                    If this persists for more than ~30 s, restart the MES-Collector cmd window.
                  </div>
                </div>
              ) : (
                <div style={{flex:1, overflowY:"auto", overflowX:"auto"}}>
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                    <thead>
                      <tr style={{borderBottom:`2px solid ${border}`}}>
                        <th style={{padding:"8px 12px",textAlign:"center",fontSize:9,fontWeight:800,
                                    letterSpacing:".08em",color:textMut,textTransform:"uppercase",width:115}}>
                          Status
                        </th>
                        <th style={{padding:"8px 12px",textAlign:"left",fontSize:9,fontWeight:800,
                                    letterSpacing:".08em",color:textMut,textTransform:"uppercase",width:80}}>
                          Bit
                        </th>
                        <th style={{padding:"8px 10px",textAlign:"left",fontSize:9,fontWeight:800,
                                    letterSpacing:".08em",color:textMut,textTransform:"uppercase"}}>
                          Poka-Yoke
                        </th>
                        <th style={{padding:"8px 10px",textAlign:"center",fontSize:9,fontWeight:800,
                                    letterSpacing:".08em",color:textMut,textTransform:"uppercase",width:70}}>
                          Value
                        </th>
                        <th style={{padding:"8px 10px",textAlign:"left",fontSize:9,fontWeight:800,
                                    letterSpacing:".08em",color:textMut,textTransform:"uppercase",width:175}}>
                          Last Toggle
                        </th>
                        <th style={{padding:"8px 12px",textAlign:"right",fontSize:9,fontWeight:800,
                                    letterSpacing:".08em",color:textMut,textTransform:"uppercase",width:110}}>
                          Stuck For
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {/* Stuck first (red), then unknown (amber), then alive (green) */}
                      {[...stuck, ...unknown, ...alive].map((s, i) => {
                        const st = (s.status || "").toLowerCase();
                        const isStuck   = st === "stuck";
                        const isUnknown = st === "unknown";
                        const ft = fmtFromTo(s.last_toggle_at);
                        const accent = isStuck   ? STATUS_CLR["BREAKDOWN"]
                                     : isUnknown ? STATUS_CLR["MATERIAL_WAIT"]
                                     :             STATUS_CLR["RUNNING"];
                        const rowBg  = isStuck ? `${STATUS_CLR["BREAKDOWN"]}0a`
                                     : isUnknown ? `${STATUS_CLR["MATERIAL_WAIT"]}08`
                                     :             "transparent";
                        const pillLabel = isStuck   ? "⚠ Not working"
                                        : isUnknown ? "⏳ Waiting"
                                        :             "✓ Working";
                        return (
                          <tr key={`${s.bit}-${i}`} style={{
                            borderBottom:`1px solid ${border}`,
                            background: rowBg,
                          }}>
                            <td style={{padding:"10px 12px",textAlign:"center"}}>
                              <span style={{
                                display:"inline-flex",alignItems:"center",gap:6,
                                padding:"4px 12px",borderRadius:99,fontSize:11,fontWeight:800,
                                background: `${accent}22`, color: accent,
                                border:`1px solid ${accent}55`,
                                textTransform:"uppercase",letterSpacing:".05em",
                              }}>
                                {pillLabel}
                              </span>
                            </td>
                            <td style={{padding:"10px 12px",fontWeight:800,fontFamily:"monospace",
                                        color: isStuck ? STATUS_CLR["BREAKDOWN"] : text, fontSize:13}}>
                              {s.bit || "—"}
                            </td>
                            <td style={{padding:"10px 10px",fontSize:12,color:text,fontWeight:600}}>
                              {s.poka_yoke_name || s.py_name || s.machine_name || <span style={{color:textMut,fontStyle:"italic"}}>—</span>}
                              {s.py_no && (
                                <span style={{fontSize:9,color:textMut,marginLeft:6,fontFamily:"monospace",
                                  padding:"1px 6px",borderRadius:99,background:bgDeep}}>
                                  {s.py_no}
                                </span>
                              )}
                            </td>
                            <td style={{padding:"10px 10px",textAlign:"center",
                                        fontFamily:"monospace",fontSize:13,fontWeight:900,
                                        color: s.current_value ? STATUS_CLR["RUNNING"] : textMut}}>
                              {s.current_value !== undefined && s.current_value !== null ? String(s.current_value) : "—"}
                            </td>
                            <td style={{padding:"10px 10px",fontFamily:"monospace",fontSize:11,color:textSub}}>
                              {ft ? (
                                <>
                                  <span style={{color: isStuck ? STATUS_CLR["BREAKDOWN"] : text, fontWeight:700}}>
                                    {ft.time}
                                  </span>
                                  {isStuck && <>
                                    <span style={{color:textMut,margin:"0 6px"}}>→</span>
                                    <span style={{color:text,fontWeight:700}}>{nowStr}</span>
                                  </>}
                                </>
                              ) : <span style={{color:textMut,fontStyle:"italic"}}>—</span>}
                            </td>
                            <td style={{padding:"10px 12px",textAlign:"right",
                                        fontFamily:"monospace",fontSize:13,fontWeight:900,
                                        color: isStuck ? STATUS_CLR["BREAKDOWN"] : textMut}}>
                              {isStuck && ft ? ft.dur : "—"}
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
        );
      })()}
    </>
  );
}

// ─────────────────────────────────────────────────────────────────
// FsCycleCommentsPanel
// Per-cycle comments panel mounted inside Fullscreen.jsx video modal.
// Mirrors CycleCommentsPanel in WallboardLeft.jsx (same backend, same
// keying by part_code) so notes are consistent across both views.
// ─────────────────────────────────────────────────────────────────
function FsCycleCommentsPanel({ lineId, partCode, isFallbackKey, isNg, border, text, textSub, textMut }) {
  const [items, setItems]     = useState([]);
  const [draft, setDraft]     = useState("");
  const [loading, setLoading] = useState(false);
  const [posting, setPosting] = useState(false);
  const [error, setError]     = useState("");
  const [savedTick, setSavedTick] = useState(0);
  const token = (typeof window !== "undefined"
                   && sessionStorage.getItem("mes_token")) || "";

  useEffect(() => {
    let stopped = false;
    setLoading(true); setError("");
    fetch(`/api/lines/${lineId}/cycles/${encodeURIComponent(partCode)}/comments`,
          { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { if (!stopped) setItems(Array.isArray(d.comments) ? d.comments : []); })
      .catch(e => { if (!stopped) setError(String(e)); })
      .finally(() => { if (!stopped) setLoading(false); });
    return () => { stopped = true; };
  }, [lineId, partCode, token]);

  const submit = useCallback(async () => {
    const txt = draft.trim();
    if (!txt) return;
    if (!token) { setError("Login required to post comments"); return; }
    setPosting(true); setError("");
    try {
      const r = await fetch(
        `/api/lines/${lineId}/cycles/${encodeURIComponent(partCode)}/comments`,
        {
          method: "POST",
          headers: {
            "Content-Type":  "application/json",
            "Authorization": `Bearer ${token}`,
          },
          body: JSON.stringify({ comment: txt }),
        }
      );
      if (!r.ok) {
        const msg = await r.text().catch(() => `HTTP ${r.status}`);
        throw new Error(msg.slice(0, 200));
      }
      const row = await r.json();
      setItems(prev => [...prev, {
        id:         row.id,
        comment:    row.comment,
        author:     row.author,
        created_at: row.created_at,
      }]);
      setDraft("");
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setPosting(false);
    }
  }, [lineId, partCode, draft, token]);

  // 2026-05-27 — NG-aware styling so the panel matches the modal frame
  // colour the operator just opened.  OK cycles keep the default look.
  const panelBg     = isNg ? "rgba(239,68,68,0.06)" : "rgba(255,255,255,0.02)";
  const panelBorder = isNg ? "#ef4444"              : border;
  const titleColor  = isNg ? "#fecaca"              : textSub;
  return (
    <div style={{
      marginTop: 8, padding: 10,
      background: panelBg,
      border: `1px solid ${panelBorder}`, borderRadius: 8,
      fontFamily: "'Barlow',sans-serif",
    }}>
      <div style={{
        fontSize: 11, fontWeight: 700, letterSpacing: ".08em",
        color: titleColor, textTransform: "uppercase", marginBottom: 8,
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <span>{isNg ? "⚠ Remarks (NG cycle)" : "Remarks"}</span>
        <span style={{ color: textMut, fontSize: 10, fontWeight: 600 }}>
          {isFallbackKey
            ? `cycle #${String(partCode).replace(/^cycle_/, "")}`
            : `part ${partCode}`}
        </span>
      </div>

      {loading ? (
        <div style={{ color: textMut, fontSize: 12, padding: "6px 0" }}>
          Loading…
        </div>
      ) : items.length === 0 ? (
        <div style={{ color: textMut, fontSize: 12, fontStyle: "italic",
                       padding: "6px 0" }}>
          No remarks yet for this cycle.
        </div>
      ) : (
        <div style={{ maxHeight: 160, overflowY: "auto", marginBottom: 8 }}>
          {items.map(c => (
            <div key={c.id} style={{
              padding: "6px 0",
              borderTop: `1px dashed ${border}`,
            }}>
              <div style={{ fontSize: 11, color: textMut, marginBottom: 2 }}>
                <span style={{ color: "#60a5fa", fontWeight: 700 }}>
                  {c.author || "operator"}
                </span>
                <span style={{ margin: "0 6px" }}>·</span>
                {c.created_at ? new Date(c.created_at).toLocaleString("en-GB", {
                  day: "2-digit", month: "short",
                  hour: "2-digit", minute: "2-digit",
                }) : ""}
              </div>
              <div style={{ fontSize: 13, color: text, whiteSpace: "pre-wrap",
                             wordBreak: "break-word" }}>
                {c.comment}
              </div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: 6 }}>
        <textarea
          value={draft}
          onChange={e => setDraft(e.target.value)}
          placeholder={isNg ? "Why was this part marked NG? Describe the defect, root cause, action taken…" : "Add a remark about this cycle…"}
          rows={2}
          maxLength={2000}
          style={{
            flex: 1, padding: "6px 8px", fontSize: 13,
            // 2026-05-27 — White textarea + dark text so operator typed
            // content is high-contrast against panel background (matches
            // wallboard side).  Save button below explicitly says SAVE.
            background: "#ffffff",
            color: "#0f172a",
            border: `1px solid ${isNg ? "#ef4444" : border}`,
            borderRadius: 6, resize: "vertical",
            fontFamily: "'Barlow',sans-serif",
          }}
          onKeyDown={e => {
            if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
              e.preventDefault();
              submit().then(() => setSavedTick(t => t + 1));
            }
          }}
        />
        <button
          onClick={() => submit().then(() => setSavedTick(t => t + 1))}
          disabled={posting || !draft.trim()}
          style={{
            padding: "0 18px", fontSize: 12, fontWeight: 800,
            background: (posting || !draft.trim())
              ? "rgba(96,165,250,.3)"
              : (isNg ? "#ef4444" : "#16a34a"),
            color: "#fff", border: "none", borderRadius: 6,
            cursor: (posting || !draft.trim()) ? "not-allowed" : "pointer",
            letterSpacing: ".08em",
            minWidth: 78,
          }}>
          {posting ? "Saving…" : "SAVE"}
        </button>
      </div>
      {savedTick > 0 && !posting && !error && (
        <div style={{ marginTop: 6, fontSize: 11, color: "#16a34a", fontWeight: 700 }}>
          ✓ Saved
        </div>
      )}
      {error && (
        <div style={{ marginTop: 6, fontSize: 11, color: "#ef4444" }}>
          {error}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// FsNgDetailsPanel
// Two-section NG panel inside the video modal:
//   (A) Machine reason — auto-derived from cycle CT vs ideal + any
//       sub-machine bit traces.  Read-only.
//   (B) Leader remark — line leader writes the exact physical reason
//       after inspection.  Editable, UPSERT-style (one remark per
//       part_code, old remarks preserved in audit_trail).
// Keyed by (lineId, partCode) so the same NG entry is reachable
// whether the user navigates by chart click, NG arrow, or part-code
// search later.
// ─────────────────────────────────────────────────────────────────
function FsNgDetailsPanel({ lineId, partCode, border, text, textSub, textMut, bgDeep }) {
  const [data, setData]       = useState(null);
  const [draft, setDraft]     = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving]   = useState(false);
  const [error, setError]     = useState("");
  const [savedAt, setSavedAt] = useState(null);
  const token = (typeof window !== "undefined"
                   && sessionStorage.getItem("mes_token")) || "";

  const reload = useCallback(() => {
    setLoading(true); setError("");
    fetch(`/api/lines/${lineId}/ng-details/${encodeURIComponent(partCode)}`,
          { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => {
        setData(d);
        setDraft(d?.leader?.leader_remark || "");
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false));
  }, [lineId, partCode, token]);

  useEffect(() => { reload(); }, [reload]);

  const save = useCallback(async () => {
    const txt = draft.trim();
    if (!txt) return;
    if (!token) { setError("Login required to save remark"); return; }
    setSaving(true); setError("");
    try {
      const r = await fetch(
        `/api/lines/${lineId}/ng-details/${encodeURIComponent(partCode)}`,
        {
          method: "POST",
          headers: {
            "Content-Type":  "application/json",
            "Authorization": `Bearer ${token}`,
          },
          body: JSON.stringify({ leader_remark: txt }),
        }
      );
      if (!r.ok) {
        const msg = await r.text().catch(() => `HTTP ${r.status}`);
        throw new Error(msg.slice(0, 200));
      }
      const row = await r.json();
      setData(prev => ({
        ...(prev || {}),
        leader: {
          leader_remark: row.leader_remark,
          entered_by:    row.entered_by,
          audit_trail:   row.audit_trail || [],
          created_at:    row.created_at,
          updated_at:    row.updated_at,
        },
      }));
      setSavedAt(new Date().toLocaleTimeString("en-GB"));
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setSaving(false);
    }
  }, [lineId, partCode, draft, token]);

  const mr  = data?.machine_reason || {};
  const ldr = data?.leader || {};
  const trail = Array.isArray(ldr.audit_trail) ? ldr.audit_trail : [];

  return (
    <div style={{
      marginTop: 8, padding: 10,
      background: "linear-gradient(180deg, rgba(239,68,68,0.08), rgba(239,68,68,0.02))",
      border: "1px solid rgba(239,68,68,0.4)", borderRadius: 8,
      fontFamily: "'Barlow',sans-serif",
    }}>
      <div style={{
        fontSize: 11, fontWeight: 800, letterSpacing: ".08em",
        color: "#ef4444", textTransform: "uppercase", marginBottom: 10,
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <span>⚠ NG Part — Details</span>
        <span style={{ color: textMut, fontSize: 10, fontWeight: 600 }}>
          part {partCode}
        </span>
      </div>

      {loading ? (
        <div style={{ color: textMut, fontSize: 12, padding: "6px 0" }}>Loading NG details…</div>
      ) : (
        <>
          {/* 2026-05-24 — "Machine-detected reason" section removed.
              Operator: "ye tujhe kisne btya ki ye part itne ct se upr
              gya to ng h, khud se kuch bhi bna rha h kya tu".  Auto-
              guess based on CT vs ideal is meaningless — slow cycle
              isn't always NG.  Only L109 + operator remarks define NG. */}

          {/* ───── Line leader remark (manual, editable) ───── */}
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: ".06em",
                           color: textSub, marginBottom: 4, textTransform: "uppercase",
                           display: "flex", justifyContent: "space-between" }}>
              <span>② Line leader's exact reason</span>
              {ldr.updated_at && (
                <span style={{ color: textMut, fontSize: 10, fontWeight: 600 }}>
                  by <span style={{ color: "#60a5fa" }}>{ldr.entered_by || "—"}</span>
                  · {new Date(ldr.updated_at).toLocaleString("en-GB", {
                      day: "2-digit", month: "short",
                      hour: "2-digit", minute: "2-digit",
                  })}
                </span>
              )}
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <textarea
                value={draft}
                onChange={e => setDraft(e.target.value)}
                placeholder="Write the exact physical reason (e.g., 'lock bar misalignment caused by jig wear', 'operator missed bolt torque', etc.)"
                rows={3}
                maxLength={2000}
                style={{
                  flex: 1, padding: "6px 8px", fontSize: 13,
                  background: "rgba(255,255,255,0.04)",
                  color: text, border: `1px solid ${border}`,
                  borderRadius: 6, resize: "vertical",
                  fontFamily: "'Barlow',sans-serif",
                }}
                onKeyDown={e => {
                  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                    e.preventDefault();
                    save();
                  }
                }}
              />
              <button
                onClick={save}
                disabled={saving || !draft.trim() || draft === (ldr.leader_remark || "")}
                style={{
                  padding: "0 14px", fontSize: 12, fontWeight: 700,
                  background: (saving || !draft.trim() || draft === (ldr.leader_remark || ""))
                                ? "rgba(239,68,68,.3)" : "#ef4444",
                  color: "#fff", border: "none", borderRadius: 6,
                  cursor: (saving || !draft.trim() || draft === (ldr.leader_remark || ""))
                              ? "not-allowed" : "pointer",
                  letterSpacing: ".04em",
                }}>
                {saving ? "…" : "SAVE"}
              </button>
            </div>
            {savedAt && (
              <div style={{ marginTop: 4, fontSize: 10, color: "#22c55e" }}>
                Saved at {savedAt}
              </div>
            )}
            {/* Edit history */}
            {trail.length > 0 && (
              <details style={{ marginTop: 8 }}>
                <summary style={{
                  cursor: "pointer", fontSize: 10, color: textMut,
                  fontWeight: 700, letterSpacing: ".04em",
                }}>
                  Previous remarks ({trail.length})
                </summary>
                <div style={{ marginTop: 6, maxHeight: 120, overflowY: "auto" }}>
                  {trail.map((t, i) => (
                    <div key={i} style={{
                      fontSize: 11, padding: "4px 0",
                      borderTop: `1px dashed ${border}`, color: textSub,
                    }}>
                      <div style={{ fontSize: 10, color: textMut }}>
                        {t.entered_by || "—"} · replaced {t.replaced_at
                          ? new Date(t.replaced_at).toLocaleString("en-GB", {
                              day: "2-digit", month: "short",
                              hour: "2-digit", minute: "2-digit"})
                          : ""}
                      </div>
                      <div style={{ whiteSpace: "pre-wrap", color: text }}>{t.remark}</div>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        </>
      )}

      {error && (
        <div style={{ marginTop: 8, fontSize: 11, color: "#ef4444" }}>
          {error}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// FsNgProcessRemarksPanel (2026-05-24)
// One remark input per process/machine on this line for the current
// NG part.  Each remark saves to mes_ng_process_remarks (UPSERT by
// (part_code, machine_id)).  Quality dashboard pulls the summary.
// ─────────────────────────────────────────────────────────────────
function FsNgProcessRemarksPanel({ lineId, partCode, border, text, textSub, textMut, bgDeep }) {
  const [data,    setData]    = useState(null);  // { machines:[], remarks:[] }
  const [drafts,  setDrafts]  = useState({});    // machine_id -> text
  const [saving,  setSaving]  = useState({});    // machine_id -> bool
  const [savedAt, setSavedAt] = useState({});    // machine_id -> ts
  const [error,   setError]   = useState("");
  const token = (typeof window !== "undefined"
                   && sessionStorage.getItem("mes_token")) || "";

  const load = useCallback(() => {
    setError("");
    fetch(`/api/lines/${lineId}/ng-process-remarks/${encodeURIComponent(partCode)}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(d => {
        setData(d);
        // pre-populate drafts from existing remarks
        const dr = {};
        (d.remarks || []).forEach(r => { dr[r.machine_id] = r.remark_text; });
        setDrafts(dr);
      })
      .catch(e => setError(String(e)));
  }, [lineId, partCode]);

  useEffect(() => { load(); }, [load]);

  const save = (m) => {
    const txt = (drafts[m.id] || "").trim();
    if (!txt) return;
    setSaving(s => ({ ...s, [m.id]: true }));
    fetch(`/api/lines/${lineId}/ng-process-remarks/${encodeURIComponent(partCode)}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        machine_id:   m.id,
        machine_name: m.machine_name,
        remark_text:  txt,
      }),
    })
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(() => { setSavedAt(s => ({ ...s, [m.id]: Date.now() })); load(); })
      .catch(e => setError(String(e)))
      .finally(() => setSaving(s => ({ ...s, [m.id]: false })));
  };

  if (!data) {
    return (
      <div style={{
        marginTop: 10, padding: 10, background: bgDeep,
        border: `1px solid ${border}`, borderRadius: 8,
        color: textMut, fontSize: 11,
      }}>Loading process remarks…</div>
    );
  }

  const machines = data.machines || [];
  const existingByMid = Object.fromEntries(
    (data.remarks || []).map(r => [r.machine_id, r]));

  return (
    <div style={{
      marginTop: 10, padding: 12, background: bgDeep,
      border: `1px solid ${border}`, borderRadius: 8,
    }}>
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        marginBottom: 8, fontSize: 11, fontWeight: 800,
        color: "#f87171", letterSpacing: ".08em",
      }}>
        <span>🛠 PROCESS-WISE NG REMARKS</span>
        <span style={{ color: textMut, fontWeight: 600 }}>
          PART {partCode}
        </span>
      </div>
      {error && (
        <div style={{ color: "#f87171", fontSize: 11, marginBottom: 6 }}>
          {error}
        </div>
      )}
      <div style={{ display: "grid", gap: 6 }}>
        {machines.map(m => {
          const ex = existingByMid[m.id];
          const saved = savedAt[m.id];
          return (
            <div key={m.id} style={{
              display: "grid",
              gridTemplateColumns: "160px 1fr auto",
              gap: 8, alignItems: "start",
              padding: "6px 0", borderTop: `1px dashed ${border}`,
            }}>
              <div>
                <div style={{ fontSize: 12, fontWeight: 800, color: text }}>
                  {m.is_main ? "★ " : ""}{m.machine_name}
                </div>
                {ex && (
                  <div style={{ fontSize: 9, color: textMut, marginTop: 2 }}>
                    last: {ex.updated_at
                      ? new Date(ex.updated_at).toLocaleString("en-IN")
                      : "—"}
                  </div>
                )}
              </div>
              <textarea
                value={drafts[m.id] || ""}
                onChange={e => setDrafts(d => ({ ...d, [m.id]: e.target.value }))}
                placeholder={`Remark for ${m.machine_name}…`}
                rows={2}
                style={{
                  width: "100%", boxSizing: "border-box",
                  padding: 6, borderRadius: 6,
                  border: `1px solid ${border}`,
                  background: "transparent", color: text,
                  fontSize: 12, resize: "vertical",
                  fontFamily: "inherit",
                }}
              />
              <button
                onClick={() => save(m)}
                disabled={!!saving[m.id] || !(drafts[m.id] || "").trim()}
                style={{
                  padding: "6px 12px", borderRadius: 6,
                  border: "none", cursor: "pointer",
                  background: saving[m.id] ? "#6b7280"
                              : saved && (Date.now() - saved < 2500) ? "#16a34a"
                              : "#b91c1c",
                  color: "#fff", fontSize: 11, fontWeight: 800,
                  minWidth: 64,
                }}
              >
                {saving[m.id] ? "..." :
                 saved && (Date.now() - saved < 2500) ? "✓ saved" : "SAVE"}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// NgListModal
// Opens when operator clicks the NG count cell in the slot table.
// Renders a table of every NG part in that slot with 4 columns:
//   Part Code | Time | Machine alarm | Leader remark (editable)
// Inline save on each row — no submit-and-reload flow needed.
// ─────────────────────────────────────────────────────────────────
function NgListModal({ lineId, date, slotLabel, onClose, onPlayVideo, border, bgDeep, text, textSub, textMut, overlayPosStyle }) {
  const [rows,    setRows]    = useState([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState("");
  const [drafts,  setDrafts]  = useState({});     // part_code -> textarea value
  const [saving,  setSaving]  = useState({});     // part_code -> bool
  const token = (typeof window !== "undefined"
                   && sessionStorage.getItem("mes_token")) || "";

  useEffect(() => {
    let stopped = false;
    setLoading(true); setError("");
    const url = `/api/lines/${lineId}/ng-list?date=${encodeURIComponent(date)}&slot_label=${encodeURIComponent(slotLabel)}`;
    fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => {
        if (stopped) return;
        const rs = Array.isArray(d.rows) ? d.rows : [];
        setRows(rs);
        const initialDrafts = {};
        rs.forEach(r => { initialDrafts[r.part_code] = r.leader_remark || ""; });
        setDrafts(initialDrafts);
      })
      .catch(e => { if (!stopped) setError(String(e)); })
      .finally(() => { if (!stopped) setLoading(false); });
    return () => { stopped = true; };
  }, [lineId, date, slotLabel, token]);

  const saveRemark = async (partCode) => {
    const txt = (drafts[partCode] || "").trim();
    if (!txt) return;
    if (!token) { setError("Login required"); return; }
    setSaving(prev => ({ ...prev, [partCode]: true }));
    try {
      const r = await fetch(
        `/api/lines/${lineId}/ng-details/${encodeURIComponent(partCode)}`,
        {
          method: "POST",
          headers: {
            "Content-Type":  "application/json",
            "Authorization": `Bearer ${token}`,
          },
          body: JSON.stringify({ leader_remark: txt }),
        }
      );
      if (!r.ok) {
        const msg = await r.text().catch(() => `HTTP ${r.status}`);
        throw new Error(msg.slice(0, 200));
      }
      const row = await r.json();
      setRows(prev => prev.map(rr =>
        rr.part_code === partCode
          ? { ...rr,
              leader_remark:  row.leader_remark,
              entered_by:     row.entered_by,
              remark_updated: row.updated_at }
          : rr));
    } catch (e) {
      setError(`${partCode}: ${e.message || e}`);
    } finally {
      setSaving(prev => ({ ...prev, [partCode]: false }));
    }
  };

  return (
    <div
      onClick={onClose}
      style={{
        ...overlayPosStyle, zIndex: 9999,
        background: "rgba(0,0,0,.65)", backdropFilter: "blur(3px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 20, animation: "fadeIn .15s ease",
      }}
    >
      <div onClick={e => e.stopPropagation()}
           style={{
             background: bgDeep, color: text,
             border: `1px solid ${border}`, borderRadius: 10,
             padding: 16, width: "90vw", maxWidth: 1100,
             maxHeight: "85vh", display: "flex", flexDirection: "column",
             fontFamily: "'Barlow',sans-serif",
             boxShadow: "0 20px 60px rgba(0,0,0,.6)",
           }}>
        {/* Header */}
        <div style={{ display: "flex", justifyContent: "space-between",
                       alignItems: "center", marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 11, color: textMut, fontWeight: 700,
                           letterSpacing: ".08em", textTransform: "uppercase" }}>
              NG Parts · {date}
            </div>
            <div style={{ fontSize: 16, fontWeight: 800, color: "#ef4444" }}>
              ⚠ Slot {slotLabel} · {rows.length} NG part{rows.length === 1 ? "" : "s"}
            </div>
          </div>
          <button onClick={onClose} style={{
            background: "transparent", border: `1px solid ${border}`,
            color: textSub, padding: "4px 14px", borderRadius: 6,
            fontSize: 16, fontWeight: 700, cursor: "pointer", lineHeight: 1,
          }}>×</button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
          {loading ? (
            <div style={{ padding: 24, textAlign: "center", color: textMut }}>
              Loading NG parts…
            </div>
          ) : rows.length === 0 ? (
            <div style={{ padding: 24, textAlign: "center", color: textMut,
                           fontStyle: "italic" }}>
              No NG parts recorded in this slot.
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse",
                             fontSize: 12 }}>
              <thead>
                <tr style={{ background: "rgba(239,68,68,0.08)",
                              borderBottom: `2px solid ${border}` }}>
                  <th style={{ padding: "8px 6px", textAlign: "left",
                                fontWeight: 800, fontSize: 10,
                                letterSpacing: ".06em", color: textSub,
                                textTransform: "uppercase" }}>Part Code</th>
                  <th style={{ padding: "8px 6px", textAlign: "left",
                                fontWeight: 800, fontSize: 10, color: textSub,
                                textTransform: "uppercase" }}>Time</th>
                  <th style={{ padding: "8px 6px", textAlign: "center",
                                fontWeight: 800, fontSize: 10, color: textSub,
                                textTransform: "uppercase",
                                width: 60 }}>Video</th>
                  {/* 2026-05-26 — "Machine Alarm" column kept as a
                      placeholder.  Operator will populate it from a
                      future PY-style alarm-name config; auto "CT vs
                      ideal" guess removed because CT has NO relation
                      to NG (a slow cycle is not necessarily NG). */}
                  <th style={{ padding: "8px 6px", textAlign: "left",
                                fontWeight: 800, fontSize: 10, color: textSub,
                                textTransform: "uppercase" }}>Machine Alarm</th>
                  <th style={{ padding: "8px 6px", textAlign: "left",
                                fontWeight: 800, fontSize: 10, color: textSub,
                                textTransform: "uppercase" }}>Line Leader Remark</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const dirty = (drafts[r.part_code] || "") !== (r.leader_remark || "");
                  const sv = !!saving[r.part_code];
                  return (
                    <tr key={r.part_code} style={{
                      borderBottom: `1px solid ${border}`,
                      verticalAlign: "top",
                    }}>
                      <td style={{ padding: "8px 6px", fontFamily: "monospace",
                                    fontSize: 11, color: "#60a5fa" }}>
                        {r.part_code}
                        {r.cycle_seq != null && (
                          <div style={{ fontSize: 10, color: textMut, marginTop: 2 }}>
                            #{r.cycle_seq} · {r.ct_value != null ? `${r.ct_value.toFixed(2)}s` : "—"}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: "8px 6px", fontFamily: "monospace",
                                    fontSize: 11, color: textSub, whiteSpace: "nowrap" }}>
                        {r.ts ? new Date(r.ts).toLocaleTimeString("en-GB") : "—"}
                      </td>
                      <td style={{ padding: "8px 6px", textAlign: "center" }}>
                        <button
                          onClick={() => onPlayVideo && onPlayVideo(r)}
                          title="Open video for this NG part"
                          style={{
                            padding: "4px 8px", fontSize: 14, fontWeight: 700,
                            background: "rgba(96,165,250,0.15)",
                            border: "1px solid rgba(96,165,250,0.5)",
                            color: "#60a5fa", borderRadius: 4,
                            cursor: "pointer", lineHeight: 1,
                          }}>
                          ▶
                        </button>
                      </td>
                      {/* 2026-05-26 — Machine Alarm cell: empty
                          placeholder until PY-style alarm-name config
                          is wired in.  CT has NO relation to NG so we
                          never auto-fill this from cycle data. */}
                      <td style={{ padding: "8px 6px", fontSize: 11,
                                    color: textMut, lineHeight: 1.4,
                                    fontStyle: "italic" }}>
                        —
                      </td>
                      <td style={{ padding: "8px 6px", minWidth: 280 }}>
                        <div style={{ display: "flex", gap: 4 }}>
                          <textarea
                            value={drafts[r.part_code] || ""}
                            onChange={e => setDrafts(prev => ({
                              ...prev, [r.part_code]: e.target.value,
                            }))}
                            rows={2}
                            maxLength={2000}
                            placeholder="Type exact reason here…"
                            style={{
                              flex: 1, padding: "4px 6px", fontSize: 12,
                              background: "rgba(255,255,255,0.04)",
                              color: text, border: `1px solid ${border}`,
                              borderRadius: 4, resize: "vertical",
                              fontFamily: "'Barlow',sans-serif",
                            }}
                            onKeyDown={e => {
                              if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                                e.preventDefault();
                                saveRemark(r.part_code);
                              }
                            }}
                          />
                          <button
                            onClick={() => saveRemark(r.part_code)}
                            disabled={!dirty || sv || !(drafts[r.part_code] || "").trim()}
                            style={{
                              padding: "0 10px", fontSize: 10, fontWeight: 800,
                              background: (!dirty || sv || !(drafts[r.part_code] || "").trim())
                                            ? "rgba(239,68,68,.3)" : "#ef4444",
                              color: "#fff", border: "none", borderRadius: 4,
                              cursor: (!dirty || sv || !(drafts[r.part_code] || "").trim())
                                          ? "not-allowed" : "pointer",
                              letterSpacing: ".04em", whiteSpace: "nowrap",
                            }}>
                            {sv ? "…" : "SAVE"}
                          </button>
                        </div>
                        {r.entered_by && (
                          <div style={{ fontSize: 9, color: textMut, marginTop: 2 }}>
                            by <span style={{ color: "#60a5fa" }}>{r.entered_by}</span>
                            {r.remark_updated && (
                              <> · {new Date(r.remark_updated).toLocaleString("en-GB", {
                                day: "2-digit", month: "short",
                                hour: "2-digit", minute: "2-digit",
                              })}</>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {error && (
            <div style={{ marginTop: 10, padding: 8, fontSize: 11,
                           color: "#ef4444",
                           background: "rgba(239,68,68,0.1)",
                           border: "1px solid rgba(239,68,68,0.3)",
                           borderRadius: 4 }}>
              {error}
            </div>
          )}
        </div>

        {/* (Loss-remark modal appended at file end after this component) */}
        {/* Footer — Send to Quality button + hint */}
        <div style={{ marginTop: 12, display:"flex",
                       justifyContent:"space-between", alignItems:"center" }}>
          <button
            onClick={async () => {
              if (rows.length === 0) return;
              const proceed = window.confirm(
                `Email ${rows.length} NG part${rows.length === 1 ? "" : "s"} `
                + `to Quality team?\n\n`
                + `Top 3 worst-CT videos will be attached.\n`
                + `Slot: ${slotLabel} · Date: ${date}`
              );
              if (!proceed) return;
              try {
                const r = await fetch(
                  `/api/lines/${lineId}/send-ng-mail`,
                  {
                    method: "POST",
                    headers: {
                      "Content-Type":  "application/json",
                      ...(token ? { Authorization: `Bearer ${token}` } : {}),
                    },
                    body: JSON.stringify({
                      date,
                      slot_label:    slotLabel,
                      attach_videos: true,
                    }),
                  }
                );
                if (!r.ok) {
                  const msg = await r.text().catch(() => `HTTP ${r.status}`);
                  throw new Error(msg.slice(0, 300));
                }
                const data = await r.json();
                alert(
                  `Mail sent ✓\n\n`
                  + `To: ${(data.to || []).join(", ")}\n`
                  + `NG parts reported: ${data.ng_count}\n`
                  + `Videos attached: ${(data.videos_attached || []).length}\n`
                  + (data.videos_skipped && data.videos_skipped.length
                      ? `Skipped (missing file or size cap): ${data.videos_skipped.length}` : "")
                );
              } catch (e) {
                alert("Mail failed:\n\n" + (e.message || e));
              }
            }}
            disabled={rows.length === 0 || loading}
            style={{
              padding: "6px 14px", fontSize: 12, fontWeight: 800,
              background: rows.length === 0 || loading
                            ? "rgba(34,197,94,.3)" : "#16a34a",
              color: "#fff", border: "none", borderRadius: 6,
              cursor: rows.length === 0 || loading ? "not-allowed" : "pointer",
              letterSpacing: ".04em",
            }}>
            📧 SEND TO QUALITY
          </button>
          <div style={{ fontSize: 10, color: textMut,
                         textAlign: "right", fontStyle: "italic" }}>
            Ctrl/Cmd + Enter to save remark · click outside to close
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// LossRemarkModal
// Per-cell remark editor for Hourly Loss Breakup.  Production clicks
// a non-zero loss time cell (e.g. "00:06:46" under BREAKDOWN at
// slot 08:30-09:30) → this modal opens with a textarea pre-filled
// with the existing remark.  Type → SAVE → posts to
// /api/lines/{id}/loss-remarks.  UPSERT — one remark per
// (line, date, shift, slot, loss_type); previous remarks preserved
// in audit_trail JSONB.
// ─────────────────────────────────────────────────────────────────
function LossRemarkModal({ lineId, payload, onClose, border, bgDeep, text, textSub, textMut, overlayPosStyle }) {
  const { date, shift_name, slot_label, loss_type, loss_label,
          loss_color, loss_secs } = payload || {};
  const [data,   setData]    = useState(null);
  const [draft,  setDraft]   = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving]  = useState(false);
  const [error,  setError]   = useState("");
  const [savedAt, setSavedAt] = useState(null);
  const token = (typeof window !== "undefined"
                   && sessionStorage.getItem("mes_token")) || "";

  // Format seconds → HH:MM:SS for display
  const fmt = (sec) => {
    const s = Math.max(0, Math.round(sec || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
    return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(ss).padStart(2,"0")}`;
  };

  useEffect(() => {
    let stopped = false;
    setLoading(true); setError("");
    const qs = new URLSearchParams({
      date, shift_name: shift_name || "",
      slot_label, loss_type,
    }).toString();
    fetch(`/api/lines/${lineId}/loss-remarks?${qs}`,
          { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => {
        if (stopped) return;
        setData(d);
        setDraft(d?.remark || "");
      })
      .catch(e => { if (!stopped) setError(String(e)); })
      .finally(() => { if (!stopped) setLoading(false); });
    return () => { stopped = true; };
  }, [lineId, date, shift_name, slot_label, loss_type, token]);

  const save = useCallback(async () => {
    const txt = draft.trim();
    if (!txt) return;
    if (!token) { setError("Login required to save remark"); return; }
    setSaving(true); setError("");
    try {
      const r = await fetch(
        `/api/lines/${lineId}/loss-remarks`,
        {
          method: "POST",
          headers: {
            "Content-Type":  "application/json",
            "Authorization": `Bearer ${token}`,
          },
          body: JSON.stringify({
            date, shift_name, slot_label, loss_type,
            remark: txt,
          }),
        }
      );
      if (!r.ok) {
        const msg = await r.text().catch(() => `HTTP ${r.status}`);
        throw new Error(msg.slice(0, 200));
      }
      const row = await r.json();
      setData(row);
      setSavedAt(new Date().toLocaleTimeString("en-GB"));
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setSaving(false);
    }
  }, [lineId, date, shift_name, slot_label, loss_type, draft, token]);

  const trail = Array.isArray(data?.audit_trail) ? data.audit_trail : [];

  return (
    <div
      onClick={onClose}
      style={{
        ...overlayPosStyle, zIndex: 10000,
        background: "rgba(0,0,0,.7)", backdropFilter: "blur(4px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 20, animation: "fadeIn .15s ease",
      }}
    >
      <div onClick={e => e.stopPropagation()}
           style={{
             background: bgDeep, color: text,
             border: `1px solid ${border}`, borderRadius: 10,
             padding: 16, width: "min(560px, 92vw)",
             maxHeight: "85vh", display: "flex", flexDirection: "column",
             fontFamily: "'Barlow',sans-serif",
             boxShadow: "0 20px 60px rgba(0,0,0,.6)",
           }}>
        <div style={{ display: "flex", justifyContent: "space-between",
                       alignItems: "flex-start", marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 10, color: textMut, fontWeight: 700,
                           letterSpacing: ".08em", textTransform: "uppercase" }}>
              Loss Remark · {date}
            </div>
            <div style={{ fontSize: 16, fontWeight: 800,
                           color: loss_color || "#ef4444", marginTop: 2 }}>
              {loss_label || loss_type} · {fmt(loss_secs)}
            </div>
            <div style={{ fontSize: 11, color: textSub, marginTop: 3 }}>
              {shift_name ? `${shift_name} shift · ` : ""}slot {slot_label}
            </div>
          </div>
          <button onClick={onClose} style={{
            background: "transparent", border: `1px solid ${border}`,
            color: textSub, padding: "4px 12px", borderRadius: 6,
            fontSize: 16, fontWeight: 700, cursor: "pointer", lineHeight: 1,
          }}>×</button>
        </div>

        {loading ? (
          <div style={{ padding: 24, textAlign: "center", color: textMut }}>
            Loading…
          </div>
        ) : (
          <>
            <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: ".06em",
                           color: textSub, marginBottom: 4, textTransform: "uppercase" }}>
              Production team remark
            </div>
            <textarea
              value={draft}
              onChange={e => setDraft(e.target.value)}
              placeholder={`Explain why ${(loss_label || loss_type).toLowerCase()} occurred in this slot…`}
              rows={5}
              maxLength={2000}
              style={{
                width: "100%", padding: "8px 10px", fontSize: 13,
                background: "rgba(255,255,255,0.04)",
                color: text, border: `1px solid ${border}`,
                borderRadius: 6, resize: "vertical",
                fontFamily: "'Barlow',sans-serif",
                boxSizing: "border-box",
              }}
              onKeyDown={e => {
                if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                  e.preventDefault();
                  save();
                }
              }}
            />
            <div style={{ marginTop: 8, display: "flex",
                           justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ fontSize: 10, color: textMut }}>
                {data?.entered_by && (
                  <>by <span style={{ color: "#60a5fa", fontWeight: 700 }}>
                    {data.entered_by}
                  </span>
                  {data.updated_at && (
                    <> · {new Date(data.updated_at).toLocaleString("en-GB", {
                      day: "2-digit", month: "short",
                      hour: "2-digit", minute: "2-digit",
                    })}</>
                  )}</>
                )}
                {savedAt && (
                  <span style={{ marginLeft: 8, color: "#22c55e" }}>
                    ✓ saved at {savedAt}
                  </span>
                )}
              </div>
              <button
                onClick={save}
                disabled={saving || !draft.trim() || draft === (data?.remark || "")}
                style={{
                  padding: "6px 16px", fontSize: 12, fontWeight: 800,
                  background: (saving || !draft.trim() || draft === (data?.remark || ""))
                                ? "rgba(34,197,94,.3)" : "#16a34a",
                  color: "#fff", border: "none", borderRadius: 6,
                  cursor: (saving || !draft.trim() || draft === (data?.remark || ""))
                              ? "not-allowed" : "pointer",
                  letterSpacing: ".04em",
                }}>
                {saving ? "…" : "SAVE"}
              </button>
            </div>
            {trail.length > 0 && (
              <details style={{ marginTop: 10 }}>
                <summary style={{
                  cursor: "pointer", fontSize: 10, color: textMut,
                  fontWeight: 700, letterSpacing: ".04em",
                }}>
                  Previous remarks ({trail.length})
                </summary>
                <div style={{ marginTop: 6, maxHeight: 140, overflowY: "auto" }}>
                  {trail.map((t, i) => (
                    <div key={i} style={{
                      fontSize: 11, padding: "5px 0",
                      borderTop: `1px dashed ${border}`, color: textSub,
                    }}>
                      <div style={{ fontSize: 10, color: textMut }}>
                        {t.entered_by || "—"} · replaced {t.replaced_at
                          ? new Date(t.replaced_at).toLocaleString("en-GB", {
                              day: "2-digit", month: "short",
                              hour: "2-digit", minute: "2-digit"})
                          : ""}
                      </div>
                      <div style={{ whiteSpace: "pre-wrap", color: text }}>{t.remark}</div>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </>
        )}
        {error && (
          <div style={{ marginTop: 8, padding: 6, fontSize: 11,
                         color: "#ef4444",
                         background: "rgba(239,68,68,0.1)",
                         border: "1px solid rgba(239,68,68,0.3)",
                         borderRadius: 4 }}>
            {error}
          </div>
        )}
        <div style={{ marginTop: 10, fontSize: 10, color: textMut,
                       fontStyle: "italic", textAlign: "right" }}>
          Ctrl/Cmd + Enter to save · click outside to close
        </div>
      </div>
    </div>
  );
}