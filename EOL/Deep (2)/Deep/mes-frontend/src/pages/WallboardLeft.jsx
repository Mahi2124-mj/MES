/**
 * WallboardLeft.jsx
 * =================
 * 65" portrait shop-floor TV — LEFT dashboard.
 *
 * Layout (top → bottom):
 *   header bar (line code · shift · clock · 16:9 toggle · ⛶ fullscreen)
 *   N stacked CT charts — ONE per sub-machine, all share the same X axis
 *      cycle # so spikes line up visually
 *   right-side dock — hover/click any machine row → side panel slides in
 *      with per-machine hourly slot counts
 *
 * Data source:
 *   GET /api/lines/{lineId}/wallboard-cycles    (refreshes every 8s)
 *   GET /api/lines/{lineId}/wallboard-summary   (hourly slots, 30s)
 *
 * Portrait-first sizing: the page height is mapped to viewport HEIGHT
 * when landscape and viewport WIDTH when portrait via a `transform:
 * rotate(90deg)` wrapper — same pattern used in Fullscreen.jsx so the
 * 16:9/9:16 toggle plus the R keyboard shortcut switch orientation
 * without DOM reflow.
 */
import { useEffect, useState, useCallback, useRef } from "react";
import { createPortal }                             from "react-dom";
import { useParams }                                from "react-router-dom";
import axios                                        from "axios";

axios.defaults.baseURL = "";  // same-origin

// ── Load Chart.js once (air-gapped LAN safe — local /public/chart.umd.min.js)
function useChartJS(cb, deps = []) {
  useEffect(() => {
    if (window.Chart) { cb(); return; }
    const s = document.createElement("script");
    s.src = "/chart.umd.min.js";
    s.onload = () => cb();
    document.head.appendChild(s);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

// Tolerance band around ideal CT (matches Fullscreen.jsx logic exactly).
const CT_TOL = 0.009;     // > ideal+0.009 = spike,  < ideal-0.009 = fast
// Match Fullscreen STATUS_CLR
const FS_OK    = "#22c55e";   // RUNNING green
const FS_BAD   = "#ef4444";   // BREAKDOWN red
const FS_WARN  = "#f59e0b";   // amber (exactly on threshold)

// ── Theme ────────────────────────────────────────────────────────
// 2026-05-18-r13 — `D` (dark flag) reads localStorage on module load
// so the Light/Dark header toggle can persist + apply on reload.
// Default is dark — 65" shop-floor TVs are easier on operator eyes
// in dark mode + most rooms are dimly lit.
const D = (() => {
  try {
    return localStorage.getItem("wb_theme") !== "light";
  } catch {
    return true;
  }
})();
const bg       = D ? "#0a0e1a" : "#f1f5f9";
const card     = D ? "#0f1729" : "#ffffff";
const border   = D ? "rgba(255,255,255,.06)" : "rgba(0,0,0,.08)";
const text     = D ? "#e2e8f0" : "#0f172a";
const textMut  = D ? "#64748b" : "#94a3b8";
const textSub  = D ? "#94a3b8" : "#475569";
const okClr     = "#22c55e";   // < ideal × 0.97
const warnClr   = "#f97316";   // ideal × 0.97 .. 1.03 (at threshold)
const badClr    = "#ef4444";   // > ideal × 1.03
const idealClr  = "#fbbf24";
const warnClrTxt= "#fbbf24";

/** 3-tier color logic.  Below threshold → green, at threshold → orange,
 *  above → red.  Threshold band is ±3% around ideal_ct.  Returns the
 *  fill colour (and stroke darker shade for ring around dots).        */
function ctColor(ct, idealCt) {
  if (ct == null || idealCt == null || idealCt <= 0) return okClr;
  const ratio = ct / idealCt;
  if (ratio < 0.97) return okClr;
  if (ratio > 1.03) return badClr;
  return warnClr;
}
function ctStroke(color) {
  return color === okClr  ? "#14532d"
       : color === badClr ? "#7f1d1d"
       : "#7c2d12";        // dark orange
}

// ── Helper: Chart.js-backed CT chart (identical to Fullscreen.jsx) ───
function MachineRow({ machine, idealCt, onPick, onCycleVideo, isMain = false, chartReady, lineId }) {
  const allCycles = machine.cycles || [];
  const WIN = 50;

  // Window state: default = sticky to END (latest 50).
  const [winStart, setWinStart] = useState(() =>
    Math.max(0, allCycles.length - WIN));
  const [sticky, setSticky]     = useState(true);

  // When new cycles arrive and we're in sticky mode, snap to end.
  useEffect(() => {
    if (sticky) setWinStart(Math.max(0, allCycles.length - WIN));
  }, [allCycles.length, sticky]);

  const canvasRef = useRef(null);
  const chartRef  = useRef(null);

  const cycles  = allCycles.slice(winStart, winStart + WIN);
  const maxWin  = Math.max(0, allCycles.length - WIN);

  // ── Chart.js instance (create / update / destroy) ────────────────
  useEffect(() => {
    if (!chartReady || !canvasRef.current || !window.Chart) return;
    if (cycles.length === 0) {
      // No data — tear down any existing chart so the empty-state message renders
      if (chartRef.current) { chartRef.current.destroy(); chartRef.current = null; }
      return;
    }

    const idealCT = idealCt;
    // Point color: same rules as Fullscreen.jsx
    const _ptClr = v =>
      v > idealCT + CT_TOL ? FS_BAD
      : v < idealCT - CT_TOL ? FS_OK
      : FS_WARN;
    // 2026-05-18-r7 — Y-AXIS HARD CAP at 40s for visual stability.
    // Operator complaint: "ISME DEKHEGA TO SARI DOT DOT HI DIKH RHI H
    // BECAUSE KUCH BICH BICH ME CYCLE TIME BHOT BDH JATA H".  A single
    // 300s outlier was forcing the y-axis to auto-scale to 300, which
    // crushed every normal 14-18s cycle into a flat band at the
    // bottom — operator couldn't read the real distribution at all.
    // Fix:
    //   • plotData clamps each value at Y_CAP, so the rendered line +
    //     dots never escape the visible 40s band.
    //   • Tooltip + dot-click handler use the REAL `cycles[i].ct` so
    //     the operator sees the true CT value (and video URL still
    //     uses the real cycle data — server has no clamp).
    //   • Clamped points get a thick black border so the operator can
    //     visually distinguish "this was actually >40s" at a glance.
    const Y_CAP = 40;
    const plotData = cycles.map(c => c.ct == null ? null : Math.min(c.ct, Y_CAP));
    const ptColors = cycles.map(cy => _ptClr(cy.ct));
    // 2026-05-18-r16 — Slimmer default dots per operator spec.
    // Operator: "dot dot dikh rhi h normal do ho not as thigh thigh
    // hogi jb vo hover + click kre" — keep base dots small (2 px
    // normal, 3 px for spikes so spikes still stand out), then
    // BALLOON on hover / active so the operator gets a strong visual
    // affordance that the dot is interactive.
    const ptRadius = cycles.map(cy => cy.ct > idealCT + CT_TOL ? 3 : 2);
    const ptHoverR = cycles.map(cy => cy.ct > idealCT + CT_TOL ? 9 : 7);
    // Clamped-point markers: thicker white border so the operator
    // can spot which dots are pinned to the cap.
    // 2026-05-18-r16 — Thinner default border (was 1px) so normal
    // dots don't look bulky.  Outliers still get a 2px white halo
    // for visual emphasis.
    const ptBorder    = cycles.map(cy => cy.ct > Y_CAP ? "#ffffff" : "#060912");
    const ptBorderW   = cycles.map(cy => cy.ct > Y_CAP ? 2 : 0.5);

    const data = {
      labels: cycles.map(c => c.cycle_seq),
      datasets: [
        {
          label: "Cycle Time",
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
            // Same threshold-aware gradient logic as Fullscreen.jsx so the
            // line shades from green → amber → red across the ideal line.
            borderColor: seg => {
              const a = cycles[seg.p0DataIndex]?.ct;
              const b = cycles[seg.p1DataIndex]?.ct;
              if (a == null || b == null) return "transparent";
              const aUp = a > idealCT + CT_TOL;
              const bUp = b > idealCT + CT_TOL;
              const aDn = a < idealCT - CT_TOL;
              const bDn = b < idealCT - CT_TOL;
              if (aUp && bUp) return `${FS_BAD}cc`;
              if (aDn && bDn) return `${FS_OK}cc`;
              if (!aUp && !aDn && !bUp && !bDn) return "rgba(245,158,11,0.85)";
              const c2 = canvasRef.current?.getContext("2d");
              if (!c2 || seg.p0.x === seg.p1.x) {
                return aUp ? `${FS_BAD}cc` : `${FS_OK}cc`;
              }
              const ratio = Math.max(0.01, Math.min(0.99,
                Math.abs(a - idealCT) / Math.abs(a - b)));
              const grad = c2.createLinearGradient(seg.p0.x, 0, seg.p1.x, 0);
              if (aUp) {
                grad.addColorStop(0,                              `${FS_BAD}cc`);
                grad.addColorStop(Math.max(0,   ratio - 0.04),    `${FS_BAD}cc`);
                grad.addColorStop(ratio,                          "rgba(245,158,11,0.85)");
                grad.addColorStop(Math.min(1,   ratio + 0.04),    `${FS_OK}cc`);
                grad.addColorStop(1,                              `${FS_OK}cc`);
              } else {
                grad.addColorStop(0,                              `${FS_OK}cc`);
                grad.addColorStop(Math.max(0,   ratio - 0.04),    `${FS_OK}cc`);
                grad.addColorStop(ratio,                          "rgba(245,158,11,0.85)");
                grad.addColorStop(Math.min(1,   ratio + 0.04),    `${FS_BAD}cc`);
                grad.addColorStop(1,                              `${FS_BAD}cc`);
              }
              return grad;
            },
          },
        },
        // Ideal-CT reference line (dashed amber)
        {
          type: "line",
          label: `Ideal ${idealCT}s`,
          data: Array(cycles.length).fill(idealCT),
          borderColor: "rgba(251,191,36,0.7)",
          borderWidth: 1.5,
          borderDash: [5, 4],
          pointRadius: 0,
          fill: false,
          tension: 0,
        },
      ],
    };

    // 2026-05-18-r7 — Hard-fix y-axis upper bound at Y_CAP (40s).  No
    // more "Math.max(..., observed_max)" — outliers stay clamped, the
    // normal-cycle resolution stays readable.
    const yMax = Y_CAP;
    const options = {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      layout: { padding: { top: 12, bottom: 2, left: 4, right: 8 } },
      // 2026-05-18-r5 — `intersect:false, mode:"index"` so a click
      // ANYWHERE along an X-column opens that cycle's video.  Previous
      // `intersect:true` required pixel-perfect hit on a 4-6px dot,
      // which is brutal on a 65" wallboard from operator distance.
      interaction: { intersect: false, mode: "index", axis: "x" },
      // 2026-05-18-r5 — Click a dot (or anywhere along its X-column)
      // = open per-cycle video popup.  Operator spec:
      // "BHAI CYCLE TIME KE JO POINTS AI UNKE ONCLICK PRR VIDEO AANI
      // CHASIYE".  Two fallback paths so the click is never silently
      // dropped:
      //   1. Chart.js `activeElements` — works when the click is inside
      //      the dataset interaction zone.
      //   2. Manual X→index mapping via evt.native.offsetX → cycle
      //      lookup.  Catches clicks in dead zones between dots.
      onClick: (evt, els /*, chart param shadows outer */) => {
        let idx = -1;
        const hit = els && els.find ? els.find(e => e.datasetIndex === 0) : null;
        if (hit) {
          idx = hit.index;
        } else if (evt?.native && chartRef.current) {
          // Manual nearest-X fallback: project the click X-pixel back
          // to a data index via Chart.js's x-scale.
          const rect = chartRef.current.canvas.getBoundingClientRect();
          const px   = (evt.native.clientX != null)
            ? evt.native.clientX - rect.left
            : (evt.native.offsetX || 0);
          const xScale = chartRef.current.scales?.x;
          if (xScale && typeof xScale.getValueForPixel === "function") {
            const v = xScale.getValueForPixel(px);
            if (v != null) idx = Math.round(v);
          }
        }
        if (idx < 0 || idx >= cycles.length) return;
        const cy = cycles[idx];
        if (!cy || cy.ct == null) return;
        if (typeof onCycleVideo === "function") onCycleVideo(machine, cy, isMain);
        if (evt?.native?.stopPropagation) evt.native.stopPropagation();
      },
      onHover: (evt) => {
        // Cursor pointer over the whole canvas — dots and column
        // gaps are both clickable now thanks to the index-mode hit.
        if (evt?.native?.target) evt.native.target.style.cursor = "pointer";
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          enabled: true,
          // 2026-05-18-r14 — Filter out the dashed Ideal-reference
          // dataset (dataIndex 1) so the tooltip doesn't double-print
          // the CT value.  `intersect:false, mode:"index"` fires the
          // label callback for ALL datasets at the hovered X column;
          // without this filter the operator saw `30.24s · tap for
          // video` twice (once for CT line, once for Ideal line —
          // both labeled with the same parsed.y from cycles[idx].ct).
          filter: (tooltipItem) => tooltipItem.datasetIndex === 0,
          callbacks: {
            title: i => `Cycle #${i[0].label}`,
            // 2026-05-18-r7 — Tooltip shows REAL ct (un-clamped) so
            // operator sees the true 312s outlier, not the visual 40s
            // clamp.  Append a " (>40s, clamped)" tag when the dot
            // pinned to the cap so the chart legibility is preserved
            // without hiding the truth.
            label: t => {
              const real = cycles[t.dataIndex]?.ct;
              if (real == null) return "—";
              const tag  = real > Y_CAP ? `  ·  >${Y_CAP}s clamped` : "";
              return `${real.toFixed(2)}s${tag}  ·  tap for video`;
            },
          },
          backgroundColor: "rgba(15,23,41,.95)",
          borderColor: "rgba(59,130,246,.5)",
          borderWidth: 1,
          padding: 8,
          titleFont: { weight: 800, size: 12 },
          bodyFont: { size: 11, family: "monospace" },
        },
      },
      scales: {
        x: {
          ticks: {
            color: textMut, font: { size: 10, family: "monospace" },
            maxRotation: 0, autoSkipPadding: 18,
          },
          grid: { color: "rgba(255,255,255,0.04)" },
        },
        y: {
          beginAtZero: false,
          min: Math.max(0, Math.floor(idealCT * 0.4)),
          max: Math.ceil(yMax),
          ticks: {
            color: textMut, font: { size: 10, family: "monospace" },
            callback: v => `${v}s`,
          },
          grid: { color: "rgba(255,255,255,0.04)" },
        },
      },
    };

    if (chartRef.current) {
      // 2026-05-18-r5 — Re-assign data + options, then call update().
      // Chart.js holds the click handler in a sticky reference, but as
      // long as `cycles` is the same array slice we built this effect
      // run with (closure capture), the latest data lands.  The
      // double-update (`reset` keeps animation off) makes the new
      // handler functions take effect even if Chart.js cached the old.
      chartRef.current.data    = data;
      chartRef.current.options = options;
      chartRef.current.update("none");
    } else {
      chartRef.current = new window.Chart(canvasRef.current.getContext("2d"), {
        type: "line", data, options,
      });
    }
  }, [cycles, idealCt, chartReady]);

  // Destroy chart on unmount to avoid memory leak between line reloads
  useEffect(() => () => {
    if (chartRef.current) { chartRef.current.destroy(); chartRef.current = null; }
  }, []);

  if (!allCycles.length) {
    return (
      <div style={{
        height: "100%",
        background: card,
        border: `1px solid ${border}`,
        borderRadius: 6,
        display: "flex", alignItems: "center", justifyContent: "center",
        color: textMut, fontSize: 14,
      }}>
        {machine.machine_name} — no cycles this shift yet
      </div>
    );
  }

  // 2026-05-18 — Y-axis FIXED RANGE = 0 → 3×ideal_ct.  Earlier we used
  // `max(ideal*2, observed_max)` which let a single 300s outlier
  // compress every other cycle into a thin line at the bottom of the
  // chart — operator couldn't see spikes against the ideal line.
  // Now the visible range is 0..3×ideal (≈ 0-45s for ideal=15); anything
  // taller clamps to the top edge.  An "outlier counter" badge in the
  // header tells the operator how many clipped points exist so they
  // can drill in via Historical when needed.
  // Latest CT (big number on right) — color follows 3-tier rule
  const last      = cycles[cycles.length - 1];
  const lastColor = ctColor(last.ct, idealCt);

  return (
    <div
      style={{
        background: card,
        border: `1px solid ${border}`,
        borderRadius: 6,
        padding: "4px 6px 2px",
        height: "100%",
        display: "flex", flexDirection: "column",
        position: "relative",
        overflow: "hidden",
      }}
    >
      {/* Header strip */}
      <div style={{ display: "flex", alignItems: "center",
                    justifyContent: "space-between", height: 22,
                    padding: "0 6px" }}>
        <div style={{
          fontSize: isMain ? 14 : 13,
          fontWeight: 900,
          // 2026-05-18-r16 — Final Inspection title text matches the
          // other machine rows (operator: "blue colour kyu aa rha h
          // usko thik kr").  The ★ prefix still distinguishes the
          // main row visually, no need for a blue tint.
          color: text,
          letterSpacing: ".02em",
        }}>
          {isMain
            ? `★ ${machine.machine_name}`
            : `M-${machine.machine_seq || "?"} · ${machine.machine_name}`}
          <span style={{ marginLeft: 8, fontSize: 9, fontWeight: 700,
                          color: textMut, letterSpacing: ".05em" }}>
            (tap dot for video)
          </span>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <span style={{ fontSize: 10, color: textMut, fontWeight: 600 }}>
            #{allCycles.length} total · viewing {winStart + 1}-{winStart + cycles.length} · ideal {idealCt}s
          </span>
          <span style={{ fontSize: 16, fontWeight: 900,
                          color: lastColor,
                          fontFamily: "monospace",
                          minWidth: 60, textAlign: "right" }}>
            {last.ct.toFixed(2)}s
          </span>
        </div>
      </div>

      {/* Chart.js canvas — same look + threshold-gradient line as Fullscreen.jsx */}
      <div style={{ flex: 1, minHeight: 0, position: "relative",
                    padding: "2px 4px 0" }}>
        <canvas ref={canvasRef} style={{ width: "100%", height: "100%" }} />
      </div>

      {/* Slider — 50-cycle window scrubber.
          Click the chart container = onPick fires; we stopPropagation on the
          slider so dragging it doesn't accidentally open the panel.       */}
      {maxWin > 0 && (
        <div onClick={e => e.stopPropagation()}
              style={{ padding: "2px 8px 0", display: "flex", alignItems: "center",
                       gap: 8, height: 16 }}>
          <span style={{ fontSize: 9, color: textMut, fontFamily: "monospace",
                          width: 32, textAlign: "right" }}>
            #{allCycles[winStart].cycle_seq}
          </span>
          <input
            type="range" min={0} max={maxWin} step={1}
            value={winStart}
            onChange={(e) => {
              const v = Number(e.target.value);
              setWinStart(v);
              setSticky(v >= maxWin - 1);   // released near end = re-enable live tracking
            }}
            style={{ flex: 1, accentColor: "#60a5fa", height: 4 }}
          />
          <span style={{ fontSize: 9, color: textMut, fontFamily: "monospace",
                          width: 32 }}>
            #{allCycles[Math.min(allCycles.length - 1, winStart + WIN - 1)].cycle_seq}
          </span>
          <button
            onClick={() => { setWinStart(maxWin); setSticky(true); }}
            title="Jump to latest"
            disabled={sticky && winStart === maxWin}
            style={{
              fontSize: 9, fontWeight: 700,
              padding: "1px 6px", borderRadius: 3,
              border: `1px solid ${sticky ? "rgba(34,197,94,.4)" : border}`,
              background: sticky ? "rgba(34,197,94,.12)" : "transparent",
              color: sticky ? okClr : textSub, cursor: "pointer",
            }}>
            {sticky ? "● LIVE" : "↦ LIVE"}
          </button>
        </div>
      )}
    </div>
  );
}

// ── Floating panel (hourly slots for hovered machine) ─────────────
// 2026-05-18 — Operator wanted this as a floating window over the
// content, not a fixed side column.  Positioned absolutely at the
// top-right of the chart area with backdrop blur + shadow so it
// reads as a popover.  Clicking outside or the ✕ closes it.
function HourlyPanel({ machine, slots, onClose }) {
  if (!machine) return null;
  return (
    <div style={{
      position: "absolute",
      top: 12, right: 12,
      width: 320,
      maxHeight: "calc(100% - 24px)",
      background: "rgba(15,23,41,.92)",
      backdropFilter: "blur(10px)",
      WebkitBackdropFilter: "blur(10px)",
      border: `1px solid rgba(59,130,246,.4)`,
      borderRadius: 10,
      padding: "12px 14px",
      overflow: "auto",
      boxShadow: "0 12px 32px rgba(0,0,0,.55), 0 0 0 1px rgba(255,255,255,.04) inset",
      display: "flex", flexDirection: "column", gap: 6,
      zIndex: 50,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "flex-start", marginBottom: 4 }}>
        <div>
          <div style={{ fontSize: 10, color: "#3b82f6", fontWeight: 800,
                        letterSpacing: ".1em", textTransform: "uppercase",
                        marginBottom: 2 }}>
            Machine · M-{machine.machine_seq || "?"}
          </div>
          <div style={{ fontSize: 15, fontWeight: 900, color: text }}>
            {machine.machine_name}
          </div>
        </div>
        <button onClick={onClose} style={{
          background: "rgba(255,255,255,.06)", border: "1px solid rgba(255,255,255,.1)",
          color: textMut, cursor: "pointer", width: 26, height: 26,
          borderRadius: 6, fontSize: 14, padding: 0, lineHeight: 1,
        }}>×</button>
      </div>
      <div style={{ fontSize: 10, color: textMut, textTransform: "uppercase",
                    letterSpacing: ".1em", marginTop: 6, fontWeight: 700 }}>
        Hourly slots · today
      </div>
      {slots.length === 0 && (
        <div style={{ fontSize: 12, color: textMut, fontStyle: "italic",
                      padding: "10px 0" }}>
          No slot data available
        </div>
      )}
      {/* Slot card style mirrors the Production Dashboard's hourly table:
            PLAN  /  OK ·NG  /  ACTUAL (+variance)
          For sub-machine clicks the payload only has `count` + `plan`, so
          we fall back gracefully when ok/ng/actual aren't supplied.       */}
      {slots.map((s, i) => {
        const plan    = s.plan ?? 0;
        const actual  = s.actual ?? s.count ?? 0;
        const ok      = s.ok    ?? null;
        const ng      = s.ng    ?? null;
        const variance= actual - plan;
        const pct     = plan > 0 ? Math.min(100, (actual / plan) * 100) : 0;
        const isHit   = actual >= plan && plan > 0;
        const barClr  = isHit ? okClr : (actual > 0 ? "#fbbf24" : "#475569");
        return (
          <div key={i} style={{
            background: "rgba(255,255,255,.04)",
            border: "1px solid rgba(255,255,255,.06)",
            borderRadius: 6, padding: "7px 10px",
          }}>
            <div style={{ display: "flex", justifyContent: "space-between",
                          alignItems: "center", marginBottom: 5,
                          fontFamily: "monospace" }}>
              <span style={{ fontSize: 12, color: text, fontWeight: 800 }}>
                {s.label}
              </span>
              <span style={{ fontSize: 9, color: textMut, fontWeight: 700,
                              letterSpacing: ".06em" }}>
                {s.start && s.end ? `${s.start}-${s.end}` : ""}
              </span>
            </div>
            <div style={{
              display: "grid",
              gridTemplateColumns: ok != null ? "1fr 1fr 1fr" : "1fr 1fr",
              gap: 6, marginBottom: 5,
            }}>
              <div>
                <div style={{ fontSize: 9, color: textMut, fontWeight: 700,
                                letterSpacing: ".05em" }}>PLAN</div>
                <div style={{ fontSize: 14, fontFamily: "monospace",
                                fontWeight: 900, color: "#60a5fa" }}>
                  {plan}
                </div>
              </div>
              {ok != null && (
                <div>
                  <div style={{ fontSize: 9, color: textMut, fontWeight: 700,
                                  letterSpacing: ".05em" }}>OK · NG</div>
                  <div style={{ fontSize: 14, fontFamily: "monospace",
                                  fontWeight: 900 }}>
                    <span style={{ color: okClr }}>{ok}</span>
                    <span style={{ color: textMut, margin: "0 3px" }}>·</span>
                    <span style={{ color: ng > 0 ? badClr : textMut }}>{ng}</span>
                  </div>
                </div>
              )}
              <div>
                <div style={{ fontSize: 9, color: textMut, fontWeight: 700,
                                letterSpacing: ".05em" }}>ACTUAL</div>
                <div style={{ fontSize: 14, fontFamily: "monospace",
                                fontWeight: 900, color: barClr }}>
                  {actual}
                  <span style={{
                    fontSize: 10, marginLeft: 4, fontWeight: 700,
                    color: variance > 0 ? okClr
                          : variance < 0 ? badClr : textMut,
                  }}>
                    ({variance > 0 ? "+" : ""}{variance})
                  </span>
                </div>
              </div>
            </div>
            <div style={{ height: 4, background: "rgba(255,255,255,.06)",
                          borderRadius: 2, overflow: "hidden" }}>
              <div style={{
                width: `${pct}%`, height: "100%",
                background: barClr,
                transition: "width .3s",
                boxShadow: isHit ? "0 0 5px rgba(34,197,94,.4)" : "none",
              }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────
export default function WallboardLeft() {
  const { lineId }      = useParams();
  const [data, setData] = useState(null);
  const [summary, setSum] = useState(null);
  // 2026-05-18-r5 — `pickedMachine` / hourly-panel wiring removed per
  // operator spec.  Chart-dot clicks now go straight to the video
  // popup; the per-machine slot summary panel is dead code (kept in
  // the file for easy re-enable later).
  const [chartReady, setChartReady] = useState(!!window.Chart);
  useChartJS(() => setChartReady(true), []);

  // 2026-05-18-r5 — Cycle-video popup state.  Opened when the operator
  // clicks a dot on any machine row's CT chart.  Stays mounted until
  // explicitly closed (backdrop click / × button) so seek-back is OK.
  const [videoCycle, setVideoCycle] = useState(null); // {machine, cycle, url, isMain}
  const openCycleVideo = useCallback((machine, cy, isMain) => {
    if (!cy || cy.ct == null) return;
    const token = sessionStorage.getItem("mes_token") || "";
    // Cycle-video endpoints differ for main line vs sub-machines — both
    // accept ?token= for HTML5 <video src=...> auth.  Cache-buster `r=`
    // keeps fresh re-clicks of the same dot from showing a stale clip.
    const url = isMain
      ? `/api/lines/${lineId}/cycle-video?cycle_seq=${cy.cycle_seq}`
        + `&token=${encodeURIComponent(token)}&r=${Date.now()}`
      : `/api/submachines/${machine.sub_id}/cycle-video?cycle_seq=${cy.cycle_seq}`
        + `&token=${encodeURIComponent(token)}&r=${Date.now()}`;
    setVideoCycle({ machine, cy, url, isMain });
  }, [lineId]);
  const [now, setNow]   = useState(new Date());

  // 2026-05-18-r8 — TWO-AXIS ORIENTATION MODEL (mirrors Fullscreen.jsx).
  //   • orientation  : which CSS grid layout to render (portrait vs landscape).
  //                    Default = match viewport shape; manual button + R-key
  //                    can override.
  //   • viewportPortrait : live tracker of innerHeight>innerWidth so we know
  //                    whether the viewport itself is tall.
  //   • needsRotation : true only when the operator-picked layout differs
  //                    from the viewport's natural shape (e.g. portrait
  //                    layout requested while browser viewport is landscape
  //                    — the vertical-mounted 16:9 panel case).  Only in
  //                    that mismatch case do we CSS-rotate 90°.
  // This stops desktop users from seeing sideways content on landscape
  // browsers while still letting shop-floor TVs run rotated layouts.
  const _detectOrient = () =>
    (typeof window !== "undefined" && window.innerHeight > window.innerWidth)
      ? "portrait" : "landscape";
  const [orientation, setOrientation] = useState(_detectOrient);
  const isPortrait = orientation === "portrait";
  const [viewportPortrait, setViewportPortrait] = useState(() =>
    typeof window !== "undefined" && window.innerHeight > window.innerWidth);
  const needsRotation = isPortrait !== viewportPortrait;

  // 2026-05-18-r13 — Auto-correct orientation ONLY when viewport
  // shape actually FLIPS (mirrors Fullscreen.jsx).  Same-shape
  // resizes (scrollbar appear/disappear, etc.) used to wipe the
  // operator's manual 9:16 toggle the moment they clicked it.  Ref
  // remembers the last shape so we know when an honest flip occurs.
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

  // Auto-refresh cycles every 8s, summary every 30s
  const fetchCycles = useCallback(() => {
    axios.get(`/api/lines/${lineId}/wallboard-cycles`)
         .then(r => setData(r.data))
         .catch(() => {});
  }, [lineId]);
  const fetchSummary = useCallback(() => {
    axios.get(`/api/lines/${lineId}/wallboard-summary`)
         .then(r => setSum(r.data))
         .catch(() => {});
  }, [lineId]);

  useEffect(() => {
    document.title = `Wallboard L · Line ${lineId}`;
    fetchCycles(); fetchSummary();
    const c = setInterval(fetchCycles, 8000);
    const s = setInterval(fetchSummary, 30000);
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => { clearInterval(c); clearInterval(s); clearInterval(t); };
  }, [fetchCycles, fetchSummary, lineId]);

  // R keyboard shortcut → toggle orientation (skip while typing)
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

  // 2026-05-18-r13 — Reverse seq order per operator spec.  Backend
  // returns subs sorted by machine_seq ASC (M-1, M-2, …); operator
  // wants display order: Final Inspection (main), then Semi-Auto (M-6),
  // Ball Guide-02 (M-5), Ball Guide-01 (M-4), Lower Rail, Lock Bar,
  // Upper Rail (M-1) — i.e. subs DESC by machine_seq.
  const subs       = (data?.machines || []).slice().sort((a, b) =>
    (b.machine_seq || 0) - (a.machine_seq || 0)
  );
  const mainRow    = data?.main || null;
  // Combined list: main row FIRST, then sub-machines
  const allRows    = mainRow ? [mainRow, ...subs] : subs;
  const shiftName  = data?.shift_name || "—";

  // (hourlySlots derivation removed — slot panel no longer rendered.)

  const toggleFS = () => {
    const el = document.documentElement;
    if (!document.fullscreenElement) el.requestFullscreen?.();
    else document.exitFullscreen?.();
  };

  // 2026-05-19 — REVERTED to ALWAYS-landscape popup.  Previous rev
  // matched dashboard orientation (portrait dashboard → portrait
  // popup); operator wanted popup to ALWAYS render upright relative
  // to the physical screen.  React-Portal escapes the rotated
  // dashboard's transform-ancestor so `position:fixed; inset:0`
  // covers the natural viewport without inheriting any rotation.
  const overlayPosStyle = {
    position: "fixed",
    inset: 0,
  };

  return (
    <div style={{
      // 2026-05-18-r8 — rotation controlled by `needsRotation`, NOT
      // `isPortrait`.  Layout itself is portrait/landscape based on
      // viewport shape; CSS rotation only kicks in when the layout
      // direction doesn't match the viewport (vertical-mounted 16:9
      // panel case).
      height:  needsRotation ? "100vw" : "100vh",
      width:   needsRotation ? "100vh" : "100%",
      background: bg, color: text,
      fontFamily: "'Barlow',sans-serif",
      overflow: "hidden",
      display: "flex", flexDirection: "column",
      position: needsRotation ? "fixed" : "static",
      top:      needsRotation ? 0       : undefined,
      left:     needsRotation ? "100vw" : undefined,
      transformOrigin: needsRotation ? "top left" : undefined,
      transform: needsRotation ? "rotate(90deg)" : undefined,
    }}>
      {/* Header bar — 2026-05-18-r13 — Rebuilt to match Fullscreen.jsx
          header style (operator spec: "second screenshot wale me
          light dark ka option nhi aaya or ye line 2 kya h same head
          kr de ye last wale screenshot jaisa").  Adds: line name +
          date, RUNNING status pill, Final Inspection pill, model
          info, live indicator, LIGHT/DARK toggle.  Flex-wraps onto
          two rows when narrow (same trick we use in Fullscreen).   */}
      <div style={{
        minHeight: 50, padding: "4px 14px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        flexWrap: "wrap", rowGap: 4, gap: 8,
        borderBottom: `1px solid ${border}`, background: card,
        flexShrink: 0,
      }}>
        {/* Left cluster — logo + line name + date + shift + status + machine pills */}
        <div style={{ display: "flex", alignItems: "center", gap: 10,
                      flexWrap: "wrap", rowGap: 4, minWidth: 0 }}>
          <img src="/logo.jpg" alt="Toyota Boshoku"
                style={{ width: 40, height: 40, borderRadius: 8,
                          objectFit: "contain",
                          background: "#fff", padding: 3, flexShrink: 0 }} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 15, fontWeight: 900, color: text,
                          letterSpacing: ".02em", lineHeight: 1.1,
                          whiteSpace: "nowrap" }}>
              {summary?.kpi?.line_name || `Line ${lineId}`}
            </div>
            <div style={{ fontSize: 10, color: textMut, marginTop: 1,
                          whiteSpace: "nowrap" }}>
              {data?.record_date || new Date().toISOString().slice(0,10)}
            </div>
          </div>

          <div style={{ width: 1, height: 22, background: border }}/>

          {/* Shift pill — green */}
          <span style={{ fontSize: 11, fontWeight: 800,
                          padding: "3px 10px", borderRadius: 99,
                          background: `${okClr}18`,
                          border: `1px solid ${okClr}33`,
                          color: okClr,
                          whiteSpace: "nowrap" }}>
            {shiftName} SHIFT
          </span>

          {/* RUNNING / status pill — colour from summary KPI if present */}
          {(() => {
            const st = (summary?.kpi?.operating_status || "RUNNING").toUpperCase();
            const sc = st === "RUNNING"   ? okClr
                     : st === "BREAKDOWN" ? badClr
                     : st === "IDLE"      ? "#94a3b8"
                     : warnClr;
            return (
              <span style={{ fontSize: 11, fontWeight: 800,
                              padding: "3px 12px", borderRadius: 99,
                              background: `${sc}18`,
                              border: `1px solid ${sc}33`,
                              color: sc,
                              letterSpacing: ".06em",
                              whiteSpace: "nowrap" }}>
                {st}
              </span>
            );
          })()}

          {/* 2026-05-18-r14 — "MACHINE Final Inspection" pill removed
              per operator spec: "ye final inspection kha se aa gyi".
              The wallboard view shows ALL machines simultaneously, so
              labelling the HEADER with one specific machine name was
              misleading.  Final Inspection still tags its own chart
              row (the star ★ row at the top) — the header just shows
              line + shift + status + model now.                       */}

          {/* Model pill — muted grey w/ accent for model name */}
          {summary?.kpi?.current_model_name && (
            <span style={{ fontSize: 11, color: textSub,
                            display: "inline-flex", alignItems: "center", gap: 6,
                            padding: "3px 10px", borderRadius: 99,
                            background: D ? "#0a1322" : "#e2e8f0",
                            border: `1px solid ${border}`,
                            whiteSpace: "nowrap" }}>
              <span style={{ fontSize: 8, opacity: 0.7, color: textMut,
                              textTransform: "uppercase", letterSpacing: ".06em" }}>
                Model{summary?.kpi?.current_model_number != null
                  ? ` ${summary.kpi.current_model_number}` : ""}
              </span>
              <strong style={{ color: text }}>
                {String(summary.kpi.current_model_name).replace(/^TYPE-SERIES:\s*/i,"")}
              </strong>
            </span>
          )}
        </div>

        {/* Right cluster — live indicator + clock + LIGHT/DARK + 9:16 + fullscreen */}
        <div style={{ display: "flex", alignItems: "center", gap: 8,
                      flexWrap: "wrap", rowGap: 4 }}>
          {/* Live indicator */}
          <div style={{ display: "flex", alignItems: "center", gap: 5,
                          padding: "3px 10px", borderRadius: 99,
                          background: `${okClr}10`,
                          border: `1px solid ${okClr}33` }}>
            <div style={{ width: 5, height: 5, borderRadius: "50%",
                            background: okClr,
                            animation: "wbPulse 2s infinite" }}/>
            <span style={{ fontSize: 11, fontWeight: 800, color: okClr }}>
              Live
            </span>
          </div>

          {/* Clock */}
          <span style={{ fontSize: 14, fontFamily: "monospace",
                          fontWeight: 700, color: text, letterSpacing: ".04em" }}>
            {now.toLocaleTimeString("en-GB")}
          </span>

          {/* 2026-05-18-r13 — LIGHT/DARK toggle.  No actual theme
              refactor of the chart rows yet (they stay dark for
              operator-eye comfort on the 65" TV) — but the button is
              wired so a future swap is one localStorage flip away. */}
          <button onClick={() => {
                    const next = D ? "light" : "dark";
                    try { localStorage.setItem("wb_theme", next); } catch {}
                    // Reload to apply since theme constants are module-level.
                    window.location.reload();
                  }}
                  title={D ? "Switch to light theme" : "Switch to dark theme"}
                  style={{ padding: "4px 10px", border: `1px solid ${border}`,
                            background: card, color: textSub, borderRadius: 4,
                            cursor: "pointer", fontSize: 11, fontWeight: 700,
                            display: "inline-flex", alignItems: "center", gap: 4 }}>
            {D ? "☀ LIGHT" : "🌙 DARK"}
          </button>

          <button onClick={() => setOrientation(o => o === "portrait" ? "landscape" : "portrait")}
                  title="R = toggle orientation"
                  style={{ padding: "4px 10px", border: `1px solid ${border}`,
                           background: card, color: textSub, borderRadius: 4,
                           cursor: "pointer", fontSize: 11, fontWeight: 700 }}>
            {isPortrait ? "9:16" : "16:9"}
          </button>
          <button onClick={toggleFS}
                  title="Fullscreen"
                  style={{ padding: "4px 10px", border: `1px solid ${border}`,
                           background: card, color: textSub, borderRadius: 4,
                           cursor: "pointer" }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2.4"
                 strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 9V5a2 2 0 0 1 2-2h4"/>
              <path d="M21 9V5a2 2 0 0 0-2-2h-4"/>
              <path d="M3 15v4a2 2 0 0 0 2 2h4"/>
              <path d="M21 15v4a2 2 0 0 1-2 2h-4"/>
            </svg>
          </button>
        </div>
      </div>
      {/* Live-indicator pulse keyframes (Wallboard scope). */}
      <style>{`@keyframes wbPulse { 0%,100% { opacity:1; } 50% { opacity:.3; } }`}</style>

      {/* Body: stacked CT rows.  Floating panel is positioned absolutely
          over this area so it doesn't shrink the chart width. */}
      <div style={{ flex: 1, position: "relative", padding: 8,
                    minHeight: 0, overflow: "hidden" }}>
        {/* CT rows — main line on top (taller), then sub-machines (equal share below) */}
        <div style={{ height: "100%", display: "flex", flexDirection: "column",
                      gap: 6 }}>
          {allRows.length === 0 && (
            <div style={{ flex: 1, display: "flex", alignItems: "center",
                          justifyContent: "center", color: textMut,
                          fontSize: 16 }}>
              No machines configured (or no cycles yet today)
            </div>
          )}
          {allRows.map((m) => (
            <div key={m.sub_id || "main"}
                  style={{
                    // Main line gets ~1.6x the height of each sub row so
                    // it visibly stands out as the line aggregate.
                    flex: m.is_main ? "1.6 1 0" : "1 1 0",
                    minHeight: 0,
                  }}>
              <MachineRow machine={m}
                          idealCt={m.ideal_ct || 15}
                          isMain={!!m.is_main}
                          chartReady={chartReady}
                          lineId={lineId}
                          onCycleVideo={openCycleVideo} />
            </div>
          ))}
        </div>

        {/* 2026-05-18-r5 — Hourly-panel rendering removed.  Operator spec:
            "CLICK TO HOURLY HTA DE AB VIDEO AAYEGI HR CYCLE KI".  Row-level
            clicks no longer open the per-machine slot panel; chart dots
            now own the click event and open the cycle-video popup instead.
            The HourlyPanel component is left defined above as dead code
            in case the slot view is wanted back later — easy to re-enable
            by restoring onPick / pickedMachine wiring. */}
      </div>

      {/* ── PER-CYCLE VIDEO POPUP ───────────────────────────────────
          Triggered by clicking any dot on any machine's CT chart.
          Streams from the line's /cycle-video proxy (main line) or
          /api/submachines/{id}/cycle-video (sub machines).

          2026-05-18-r7 — Portal'd to document.body to escape the
          rotated dashboard's transform-ancestor containing block.
          2026-05-19 — REVERTED r17.  Popup is now ALWAYS landscape
          (upright relative to the physical monitor).  overlayPosStyle
          is plain `position:fixed; inset:0`; combined with the Portal
          escape, the popup covers the natural viewport regardless of
          dashboard rotation state.                                 */}
      {videoCycle && createPortal((
        <div
          onClick={() => setVideoCycle(null)}
          style={{
            ...overlayPosStyle,
            zIndex: 9999,
            background: "rgba(0,0,0,0.82)",
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: 20,
          }}
        >
          <div onClick={e => e.stopPropagation()}
               style={{
                 background: "#0a0f1a", color: text,
                 border: `1px solid ${border}`, borderRadius: 12,
                 padding: 14, maxWidth: 900, width: "90%",
                 maxHeight: "88vh",
                 display: "flex", flexDirection: "column",
                 boxShadow: "0 24px 72px rgba(0,0,0,0.6)",
               }}>
            {/* Header strip */}
            <div style={{ display:"flex", justifyContent:"space-between",
                          alignItems:"center", marginBottom: 10,
                          fontFamily:"'Barlow',sans-serif" }}>
              <div style={{ fontSize: 13, fontWeight: 800, color: text,
                            letterSpacing: ".02em" }}>
                <span style={{ color: videoCycle.isMain ? "#60a5fa" : "#fbbf24" }}>
                  {videoCycle.isMain ? "★ Final Inspection" : `M-${videoCycle.machine.machine_seq||"?"}`}
                </span>
                <span style={{ color: textMut, margin: "0 8px" }}>·</span>
                {videoCycle.machine.machine_name}
                <span style={{ color: textMut, margin: "0 8px" }}>·</span>
                Cycle #{videoCycle.cy.cycle_seq}
                <span style={{ color: textMut, margin: "0 8px" }}>·</span>
                <span style={{
                  fontFamily:"monospace",
                  color: ctColor(videoCycle.cy.ct, videoCycle.machine.ideal_ct || idealCt),
                }}>
                  {Number(videoCycle.cy.ct).toFixed(2)}s
                </span>
                {videoCycle.cy.ts && (
                  <>
                    <span style={{ color: textMut, margin: "0 8px" }}>·</span>
                    <span style={{ fontSize: 11, color: textSub }}>
                      {new Date(videoCycle.cy.ts).toLocaleTimeString("en-GB")}
                    </span>
                  </>
                )}
              </div>
              <button onClick={() => setVideoCycle(null)}
                      title="Close (Esc)"
                      style={{
                        background:"transparent", border:`1px solid ${border}`,
                        color: textSub, fontSize: 18, padding: "2px 12px",
                        borderRadius: 6, cursor:"pointer", fontWeight: 700,
                        lineHeight: 1,
                      }}>
                ×
              </button>
            </div>
            {/* Video element — `controls` for play/pause, auto-loads.
                If the server returns 404 / 401, the browser fires onError.

                2026-05-19 — Auto-retry on error.  Diagnosis from operator
                log: when the operator clicks a freshly-finished cycle dot,
                the cycle's MP4 is still being extracted by the recorder
                (10-20 s for long cycles).  The first /cycle-video proxy
                hit returns 404 because the MP4 isn't on disk yet, the
                browser fires `error`, and we'd show "No video available"
                forever even though the file would arrive a few seconds
                later.

                New behavior: on error, retry the load up to RETRY_MAX
                times at RETRY_DELAY_MS spacing (5 s × 4 = 20 s coverage,
                comfortably wider than any observed extraction).  Each
                retry uses a fresh cache-buster so the browser doesn't
                serve the cached 404 / partial response.  Only after the
                retry budget is exhausted do we show the inline error. */}
            <video src={videoCycle.url}
                   controls autoPlay
                   data-retry="0"
                   style={{
                     width: "100%",
                     maxHeight: "70vh",
                     background: "#000",
                     borderRadius: 6,
                     border: `1px solid ${border}`,
                   }}
                   onError={(e) => {
                     const RETRY_MAX      = 4;
                     const RETRY_DELAY_MS = 5000;
                     const el = e.target;
                     const parent = el.parentNode;
                     if (!parent) return;
                     const tries = parseInt(el.dataset.retry || "0", 10);
                     if (tries < RETRY_MAX) {
                       el.dataset.retry = String(tries + 1);
                       // Fresh URL: replace the `r=` cache-buster so the
                       // browser doesn't reuse the cached failure response.
                       const base = videoCycle.url.replace(/[&?]r=\d+/, "");
                       const sep  = base.includes("?") ? "&" : "?";
                       setTimeout(() => {
                         try {
                           el.src = base + sep + "r=" + Date.now();
                           el.load();
                         } catch { /* element may have been unmounted */ }
                       }, RETRY_DELAY_MS);
                       return;
                     }
                     // Retry budget exhausted — show inline error.
                     el.style.display = "none";
                     if (parent.querySelector(".wb-video-err")) return;
                     const div = document.createElement("div");
                     div.className = "wb-video-err";
                     div.style.cssText = "padding:24px;color:#ef4444;font-size:13px;"
                       + "background:#1a0a0a;border:1px solid rgba(239,68,68,.4);"
                       + "border-radius:6px;font-family:'Barlow',sans-serif;";
                     div.textContent = "No video available for this cycle";
                     parent.appendChild(div);
                   }} />
          </div>
        </div>
      ), document.body)}
    </div>
  );
}
