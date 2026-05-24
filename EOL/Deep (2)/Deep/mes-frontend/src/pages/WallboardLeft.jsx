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
    // 2026-05-18-r7 — Y-AXIS dynamic cap, scaled per-machine idealCT.
    // Operator complaint: "ISME DEKHEGA TO SARI DOT DOT HI DIKH RHI H
    // BECAUSE KUCH BICH BICH ME CYCLE TIME BHOT BDH JATA H".  A single
    // 300s outlier was forcing the y-axis to auto-scale to 300, which
    // crushed every normal 14-18s cycle into a flat band at the bottom.
    //
    // 2026-05-20 — Earlier Y_CAP was hardcoded to 40s.  That worked for
    // 15s-ideal lines (15+25 headroom) but broke when a row's ideal CT
    // was 30s — the cap stayed at 40 so anything over 40s clamped and
    // the line looked like it was hugging the ceiling.  Operator spec:
    // "mera ct 15 tha to mnne 40 pe set kia tha, jb 30 kr dia to bhi
    // bdhna chahiye na isse +25sec ho jaye".
    //
    // Formula: Y_CAP = idealCT + 25.  Gives:
    //   ideal 15s → cap 40s  (matches old hardcoded)
    //   ideal 30s → cap 55s
    //   ideal 45s → cap 70s
    //
    // 2026-05-21 — Y_MIN must NEVER cut off real cycle data.
    // Earlier formula `idealCT - 10` worked when actual CTs ≈ ideal,
    // but Final Inspection has DB ideal=30s while real cycles run at
    // 12-15s.  That set Y_MIN=20 and clipped every cycle below the
    // chart band → graph appeared blank.  Now we ALSO consider the
    // observed minimum cycle time and use whichever is lower.
    const Y_CAP = Math.max(20, (idealCT || 15) + 25);
    const _observedCTs = cycles.map(c => c.ct).filter(v => v != null && v > 0);
    const _observedMin = _observedCTs.length ? Math.min(..._observedCTs) : (idealCT || 15);
    const Y_MIN = Math.max(
      0,
      Math.min(
        (idealCT || 15) - 10,       // proportional default
        Math.floor(_observedMin) - 2 // never crop real data below view
      )
    );
    const plotData = cycles.map(c => c.ct == null ? null : Math.min(c.ct, Y_CAP));
    // 2026-05-24 — NG dots ALWAYS red regardless of CT, so the operator
    // immediately spots a rejected part vs a slow-but-OK cycle.
    const ptColors = cycles.map(cy => cy.is_ng ? FS_BAD : _ptClr(cy.ct));
    // NG dots also bigger so they stand out + white border ring.
    const ptRadius = cycles.map(cy => cy.is_ng ? 5
                                    : (cy.ct > idealCT + CT_TOL ? 3 : 2));
    const ptHoverR = cycles.map(cy => cy.is_ng ? 11
                                    : (cy.ct > idealCT + CT_TOL ? 9 : 7));
    const ptBorder    = cycles.map(cy => cy.is_ng ? "#ffffff"
                                        : (cy.ct > Y_CAP ? "#ffffff" : "#060912"));
    const ptBorderW   = cycles.map(cy => cy.is_ng ? 2
                                        : (cy.ct > Y_CAP ? 2 : 0.5));

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
            // 2026-05-24 — Full per-cycle details on hover.  No "clamped"
            // tag — autoscale handles outliers now.
            title: i => {
              const cy = cycles[i[0].dataIndex];
              return `Cycle #${cy?.cycle_seq ?? i[0].label}`;
            },
            label: t => {
              const cy = cycles[t.dataIndex];
              if (!cy || cy.ct == null) return "—";
              const status = cy.is_ng ? "NG ⚠" : "OK ✓";
              const ts = cy.ts ? new Date(cy.ts) : null;
              const ts_s = ts ? ts.toLocaleTimeString("en-IN", {
                hour: "2-digit", minute: "2-digit", second: "2-digit",
                hour12: false,
              }) : "—";
              return [
                `Status:  ${status}`,
                `CT:      ${Number(cy.ct).toFixed(2)}s`,
                `Time:    ${ts_s}`,
                `tap for video`,
              ];
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
          // 2026-05-24 — bigger / bolder cycle-seq labels at the bottom
          // so operator can read part serial numbers from a distance.
          ticks: {
            color: "#e2e8f0",
            font: { size: 11, weight: "bold", family: "monospace" },
            maxRotation: 0, autoSkipPadding: 14,
          },
          grid: { color: "rgba(255,255,255,0.04)" },
        },
        y: {
          beginAtZero: false,
          min: Y_MIN,
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
      // 2026-05-24 — Per-dot CT label plugin.  Operator spec: "dot pe
      // ct ok sirf ng ka front pe show ho with symbol and ok".
      // Renders for EVERY visible dot:
      //   • OK cycle → green CT number ABOVE the dot
      //   • NG cycle → red ⚠ symbol + red CT number ABOVE the dot
      // Hover tooltip still shows full details (cycle seq, CT, time, status).
      const ngMarkerPlugin = {
        id: "perDotLabel",
        afterDatasetsDraw(chart) {
          const meta = chart.getDatasetMeta(0);
          if (!meta || !meta.data) return;
          const ctx2 = chart.ctx;
          ctx2.save();
          ctx2.textAlign    = "center";
          ctx2.textBaseline = "bottom";
          meta.data.forEach((pt, i) => {
            const cy = cycles[i];
            if (!cy || cy.ct == null) return;
            const x = pt.x, y = pt.y;
            const ct = Number(cy.ct).toFixed(1) + "s";
            if (cy.is_ng) {
              // ⚠ glyph above, then NG CT in red just under it
              ctx2.font      = "bold 11px 'Segoe UI Symbol', sans-serif";
              ctx2.fillStyle = "#fbbf24";
              ctx2.fillText("⚠", x, y - 18);
              ctx2.font      = "bold 10px monospace";
              ctx2.fillStyle = FS_BAD;
              ctx2.fillText(ct, x, y - 6);
            } else {
              // OK CT in green above the dot
              ctx2.font      = "bold 9px monospace";
              ctx2.fillStyle = FS_OK;
              ctx2.fillText(ct, x, y - 6);
            }
          });
          ctx2.restore();
        },
      };
      chartRef.current = new window.Chart(canvasRef.current.getContext("2d"), {
        type: "line", data, options, plugins: [ngMarkerPlugin],
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
          {/* 2026-05-24 — Show OK + NG counts separately so operator
              can see per-machine reject rate at a glance.  NG count
              shown in red, OK in green. */}
          {(() => {
            const okN = allCycles.filter(c => !c.is_ng).length;
            const ngN = allCycles.filter(c => c.is_ng).length;
            return (
              <span style={{ fontSize: 10, fontWeight: 700, color: textMut,
                              fontFamily: "monospace" }}>
                <span style={{ color: FS_OK }}>OK: {okN}</span>
                <span style={{ margin: "0 6px", color: textMut }}>·</span>
                <span style={{ color: FS_BAD }}>NG: {ngN}</span>
                <span style={{ margin: "0 6px", color: textMut }}>·</span>
                <span>ideal {idealCt}s</span>
              </span>
            );
          })()}
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
            {/* 2026-05-21 — Video loops continuously until operator
                explicitly closes the modal.  Custom big centered
                play/pause overlay button on top of native controls —
                operator on shop-floor with gloves can hit a 96 px
                target much easier than the native <30 px controls.
                Spec: "video chale to continue chale jb tk close na ho
                ... play button center me bda".
                2026-05-21-r2 — Operator added "timeline bar wapas
                laa + replay-from-beginning button".  Re-enabled
                `controls` for the native progress/scrubber bar at the
                bottom; added a custom ↺ Replay button (top-left of
                video) that resets currentTime=0 instantly without
                waiting for the natural loop boundary. */}
            <div style={{ position:"relative", width:"100%", lineHeight:0 }}>
            <video src={videoCycle.url}
                   autoPlay muted
                   controls
                   controlsList="nodownload noplaybackrate noremoteplayback"
                   data-retry="0"
                   onClick={(e) => {
                     // Tap on video toggles play/pause — same UX as the
                     // big centered button.
                     const v = e.currentTarget;
                     if (v.paused) v.play().catch(() => {});
                     else v.pause();
                   }}
                   style={{
                     width: "100%",
                     maxHeight: "70vh",
                     background: "#000",
                     borderRadius: 6,
                     border: `1px solid ${border}`,
                     cursor: "pointer",
                     display: "block",
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
            {/* 2026-05-22 — Center big-play overlay removed per operator
                request.  Native controls bottom strip + ↺ replay top-left
                cover the play/pause/seek workflow. */}
            {/* 2026-05-21-r2 — Replay-from-start button.  Operator spec:
                "replay from beginning ka bhi button aana chahiye".
                Sits in the top-left corner of the video, 40 px circle.
                Always visible (unlike big-play which hides during
                playback) so operator can re-watch any cycle instantly
                without waiting for the natural loop boundary or
                dragging the native scrubber to 0. */}
            <button
              className="wb-replay"
              title="Replay from start"
              onClick={(e) => {
                e.stopPropagation();
                const v = e.currentTarget.parentNode?.querySelector("video");
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
                {/* Circular replay arrow — counter-clockwise loop ending in arrowhead */}
                <path d="M3 12a9 9 0 1 0 3-6.7" />
                <polyline points="3 4 3 10 9 10" />
              </svg>
            </button>
            </div>
            {/* 2026-05-21 — Per-cycle Comments panel.
                Operator spec: "Final Inspection ka video khule to neeche
                comments tab ho jaha cycle-specific notes likhe ja sake".
                Renders ONLY for main-PLC cycles (isMain) — sub-machine
                slice viewers don't need this.  Keyed by part_code so
                opening the same cycle via part_code search later shows
                the same comment history. */}
            {videoCycle.isMain && videoCycle.cy.part_code && (
              <CycleCommentsPanel
                lineId={lineId}
                partCode={videoCycle.cy.part_code}
                border={border}
                text={text}
                textSub={textSub}
                textMut={textMut}
              />
            )}
            {/* 2026-05-24 — Per-process NG remarks panel for ANY NG
                cycle (main OR sub).  Sub-machine cycles don't have a
                real part_code, so we synthesise one as
                M{machine_id}-{cycle_seq}-{YYYY-MM-DD} for keying.
                Operator spec: "ng part me video k niche remarks ka
                option de hr process pe". */}
            {(() => {
              // 2026-05-24 — Remark box for EVERY cycle (OK or NG).
              // Only ONE input — for the specific machine whose cycle
              // is being viewed.  NG cycles get a red box, OK cycles
              // get a normal box.  Operator: "sabme remarks ka option
              // de ok and ng me red kr dio bs comment box ko red and
              // hr process pe apna apna remarks option bs only one
              // machine jiski cycle h vo".
              const realPC = videoCycle.cy.part_code;
              const machineId = videoCycle.machine.id
                              || videoCycle.machine.sub_id
                              || 0;
              const synthPC = realPC && String(realPC).trim()
                ? String(realPC).trim().replace(/:$/, "")
                : `M${machineId}-`
                  + `C${videoCycle.cy.cycle_seq}-`
                  + (videoCycle.cy.ts
                       ? new Date(videoCycle.cy.ts).toISOString().slice(0, 10)
                       : new Date().toISOString().slice(0, 10));
              return (
                <WbCycleRemarkPanel
                  lineId={lineId}
                  partCode={synthPC}
                  machineId={machineId}
                  machineName={videoCycle.machine.machine_name}
                  isNg={!!videoCycle.cy.is_ng}
                  border={border}
                  text={text}
                  textSub={textSub}
                  textMut={textMut}
                  bgDeep={bg}
                />
              );
            })()}
          </div>
        </div>
      ), document.body)}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// WbCycleRemarkPanel — single remark box for the specific machine
// whose cycle is being viewed.  NG cycle = red box; OK cycle = blue.
// Operator: "hr process pe apna apna remarks option bs only one
// machine jiski cycle h vo".
// ─────────────────────────────────────────────────────────────────
function WbCycleRemarkPanel({
  lineId, partCode, machineId, machineName, isNg,
  border, text, textSub, textMut, bgDeep,
}) {
  const [existing, setExisting] = useState(null);
  const [draft,    setDraft]    = useState("");
  const [saving,   setSaving]   = useState(false);
  const [savedAt,  setSavedAt]  = useState(0);
  const [error,    setError]    = useState("");
  const token = (typeof window !== "undefined"
                   && sessionStorage.getItem("mes_token")) || "";

  const load = useCallback(() => {
    if (!machineId) return;
    setError("");
    fetch(`/api/lines/${lineId}/ng-process-remarks/${encodeURIComponent(partCode)}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(d => {
        const mine = (d.remarks || []).find(r => r.machine_id === machineId);
        setExisting(mine || null);
        setDraft(mine ? mine.remark_text : "");
      })
      .catch(e => setError(String(e)));
  }, [lineId, partCode, machineId]);

  useEffect(() => { load(); }, [load]);

  const save = () => {
    const txt = draft.trim();
    if (!txt || !machineId) return;
    setSaving(true);
    fetch(`/api/lines/${lineId}/ng-process-remarks/${encodeURIComponent(partCode)}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        machine_id:   machineId,
        machine_name: machineName,
        remark_text:  txt,
      }),
    })
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(() => { setSavedAt(Date.now()); load(); })
      .catch(e => setError(String(e)))
      .finally(() => setSaving(false));
  };

  const accent      = isNg ? "#ef4444" : "#3b82f6";
  const accentBg    = isNg ? "rgba(239,68,68,0.08)" : "rgba(59,130,246,0.06)";
  const accentBd    = isNg ? "rgba(239,68,68,0.55)" : "rgba(59,130,246,0.35)";
  const recentlySaved = (Date.now() - savedAt) < 2500;

  return (
    <div style={{
      marginTop: 10, padding: 12, background: accentBg,
      border: `1.5px solid ${accentBd}`, borderRadius: 8,
    }}>
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        marginBottom: 8, fontSize: 11, fontWeight: 800,
        color: accent, letterSpacing: ".08em",
      }}>
        <span>{isNg ? "⚠ NG" : "📝 OK"} CYCLE REMARK · {machineName}</span>
        <span style={{ color: textMut, fontWeight: 600 }}>
          {existing && existing.updated_at
            ? "last: " + new Date(existing.updated_at).toLocaleString("en-IN")
            : "no remark yet"}
        </span>
      </div>
      {error && (
        <div style={{ color: "#f87171", fontSize: 11, marginBottom: 6 }}>
          {error}
        </div>
      )}
      <div style={{ display: "flex", gap: 8, alignItems: "stretch" }}>
        <textarea
          value={draft}
          onChange={e => setDraft(e.target.value)}
          placeholder={isNg
            ? `Why was this part NG'd at ${machineName}?  (e.g., "jig wear caused misalign", "operator missed torque")`
            : `Any note about this OK cycle at ${machineName}?  (optional — usually leave blank)`}
          rows={3}
          style={{
            flex: 1, padding: 8, borderRadius: 6,
            border: `1px solid ${isNg ? "rgba(239,68,68,0.5)" : border}`,
            // 2026-05-24 — force WHITE text + readable placeholder on
            // the dark wallboard background.  Operator: "text input ka
            // colour black h white kr taki dikh jaye".
            background: "rgba(0,0,0,0.35)",
            color: "#ffffff",
            fontSize: 13, resize: "vertical", fontFamily: "inherit",
            caretColor: "#ffffff",
          }}
        />
        <button
          onClick={save}
          disabled={saving || !draft.trim()}
          style={{
            padding: "0 18px", borderRadius: 6,
            border: "none", cursor: "pointer",
            background: saving ? "#6b7280"
                        : recentlySaved ? "#16a34a"
                        : accent,
            color: "#fff", fontSize: 12, fontWeight: 800,
            minWidth: 80, alignSelf: "stretch",
          }}
        >
          {saving ? "..." : recentlySaved ? "✓ saved" : "SAVE"}
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// CycleCommentsPanel
// Per-cycle notes/comments panel.  Mounts inside the video modal,
// fetches existing comments on open, allows authenticated users to
// post new ones.  Keyed by (lineId, partCode) so the same thread
// appears whether the user navigated via chart-dot click or via the
// part_code search.  Append-only — typo correction = post a new
// comment (matches the breakdown closure-notes pattern).
// ─────────────────────────────────────────────────────────────────
function CycleCommentsPanel({ lineId, partCode, border, text, textSub, textMut }) {
  const [items, setItems]     = useState([]);
  const [draft, setDraft]     = useState("");
  const [loading, setLoading] = useState(false);
  const [posting, setPosting] = useState(false);
  const [error, setError]     = useState("");
  const token = (typeof window !== "undefined"
                   && sessionStorage.getItem("mes_token")) || "";

  // Fetch on mount + when part_code changes (different cycle clicked)
  useEffect(() => {
    let stopped = false;
    setLoading(true);
    setError("");
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
    if (!token) {
      setError("Login required to post comments");
      return;
    }
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

  // Auto-grow textarea heightless on small content
  return (
    <div style={{
      marginTop: 12, padding: 12,
      background: "rgba(255,255,255,0.02)",
      border: `1px solid ${border}`, borderRadius: 8,
      fontFamily: "'Barlow',sans-serif",
    }}>
      <div style={{
        fontSize: 11, fontWeight: 700, letterSpacing: ".08em",
        color: textSub, textTransform: "uppercase", marginBottom: 8,
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <span>Comments</span>
        <span style={{ color: textMut, fontSize: 10, fontWeight: 600 }}>
          part {partCode}
        </span>
      </div>

      {/* Existing comments list */}
      {loading ? (
        <div style={{ color: textMut, fontSize: 12, padding: "6px 0" }}>
          Loading…
        </div>
      ) : items.length === 0 ? (
        <div style={{ color: textMut, fontSize: 12, fontStyle: "italic",
                       padding: "6px 0" }}>
          No comments yet for this cycle.
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

      {/* Compose */}
      <div style={{ display: "flex", gap: 6 }}>
        <textarea
          value={draft}
          onChange={e => setDraft(e.target.value)}
          placeholder="Add a comment about this cycle…"
          rows={2}
          maxLength={2000}
          style={{
            flex: 1, padding: "6px 8px", fontSize: 13,
            background: "rgba(255,255,255,0.04)",
            color: text, border: `1px solid ${border}`,
            borderRadius: 6, resize: "vertical",
            fontFamily: "'Barlow',sans-serif",
          }}
          onKeyDown={e => {
            // Ctrl/Cmd+Enter to submit — common UX
            if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
              e.preventDefault();
              submit();
            }
          }}
        />
        <button
          onClick={submit}
          disabled={posting || !draft.trim()}
          style={{
            padding: "0 14px", fontSize: 12, fontWeight: 700,
            background: (posting || !draft.trim()) ? "rgba(96,165,250,.3)" : "#2563eb",
            color: "#fff", border: "none", borderRadius: 6,
            cursor: (posting || !draft.trim()) ? "not-allowed" : "pointer",
            letterSpacing: ".04em",
          }}>
          {posting ? "…" : "POST"}
        </button>
      </div>
      {error && (
        <div style={{ marginTop: 6, fontSize: 11, color: "#ef4444" }}>
          {error}
        </div>
      )}
    </div>
  );
}
