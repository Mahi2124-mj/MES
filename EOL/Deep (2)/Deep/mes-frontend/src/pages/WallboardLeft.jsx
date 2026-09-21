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
import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import VideoProgressBar from "../components/VideoProgressBar";
import SlideNav                                     from "../components/SlideNav";
import { createPortal }                             from "react-dom";
import { startTimer }                              from "../api/timing";
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
// 2026-08-10 — Reference point for the loading cover's progress bar.
// This was the buffer GATE (playback waited until 20% of the clip was buffered).
// The gate is gone — the <video> now autoPlays on the first bytes, exactly like
// Fullscreen does, because the same cycle felt instant there and laggy here.
// The value survives only to scale the "Loading cycle video · NN%" bar, so it
// reads full about when playback takes over instead of crawling to 100%.
const VID_PLAY_AT = 0.20;

// 2026-08-10 — SPECULATIVE CLIP WARM.
// A cycle clip does not exist as a file: it is transcoded out of the camera's
// rolling .ts on demand, which is the 2-4 s wait on the first click.  The
// /wallboard-cycles poll already warms each machine's newest cycles, but a
// shift has 200+ over-target cycles per machine — far too many to render
// speculatively — so the ones the operator is actually about to open have to
// be named from here: when the over-target list opens, and on hover, a beat
// before the click.
// Fire-and-forget; `_warmed` keeps hover from re-asking for the same cycle.
const _warmed = new Set();
function warmClips(lineId, subId, seqs) {
  if (!lineId) return;
  const fresh = [];
  for (const s of seqs) {
    if (s == null) continue;
    const key = `${subId || 0}:${s}`;
    if (_warmed.has(key)) continue;
    _warmed.add(key);
    fresh.push(s);
  }
  if (!fresh.length) return;
  // Cap the memory of a wallboard left open all shift.
  if (_warmed.size > 4000) _warmed.clear();
  const qs = `seqs=${fresh.join(",")}` + (subId ? `&sub_id=${subId}` : "");
  fetch(`/api/lines/${lineId}/clip-prewarm?${qs}`).catch(() => {});
}
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
// ─────────────────────────────────────────────────────────────────
// 2026-06-06 — OVER-TARGET 30-day popup (operator: click the OVER TARGET
// badge → last-30-day over-target graph, A-shift & B-shift separate, toggle
// between % and cycle-count).  Data from GET /over-target-history.  Additive.
// ─────────────────────────────────────────────────────────────────
function OtBars({ title, rows, metric, color = "#f59e0b", onToday }) {
  const data = (rows || []).map(r => ({
    date: r.date, md: String(r.date || "").slice(5),
    val: metric === "pct" ? r.pct : r.over,
    over: r.over, total: r.total, pct: r.pct,
  }));
  const maxV = Math.max(1, ...data.map(d => Number(d.val) || 0));
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ fontSize: 11, fontWeight: 800, color, letterSpacing: ".08em", marginBottom: 4 }}>
        {title} <span style={{ color: "#64748b", fontWeight: 600 }}>· {data.length} days</span>
      </div>
      {data.length === 0 ? (
        <div style={{ fontSize: 12, color: "#64748b", fontStyle: "italic", padding: "14px 0" }}>
          No data yet for this shift.
        </div>
      ) : (
        <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 130,
                      paddingTop: 14, borderBottom: "1px solid rgba(255,255,255,0.14)" }}>
          {data.map((d, i) => {
            const isToday = (i === data.length - 1) && typeof onToday === "function";
            return (
              <div key={i}
                   onClick={isToday ? onToday : undefined}
                   title={isToday
                     ? `${d.date} — click for today's over-target cycle videos`
                     : `${d.date}\nover ${d.over} / ${d.total} = ${d.pct}%`}
                   style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column",
                            alignItems: "center", justifyContent: "flex-end", height: "100%",
                            cursor: isToday ? "pointer" : "default" }}>
                <span style={{ fontSize: 8, color, fontFamily: "monospace", fontWeight: 700, marginBottom: 2 }}>
                  {metric === "pct" ? `${d.val}%` : d.val}
                </span>
                <div style={{ width: "78%", height: `${Math.max(2, (Number(d.val) / maxV) * 100)}%`,
                              background: color, borderRadius: "2px 2px 0 0",
                              opacity: isToday ? 1 : 0.9,
                              outline: isToday ? "2px solid rgba(255,255,255,0.55)" : "none",
                              outlineOffset: isToday ? "1px" : 0 }} />
                <span style={{ fontSize: 7, color: isToday ? "#ffffff" : "#64748b",
                               fontWeight: isToday ? 800 : 400, fontFamily: "monospace",
                               marginTop: 2, whiteSpace: "nowrap" }}>{d.md}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function OtTable({ cycles, ideal, onVideo, onBack, D, lineId, isMain, machineId, shiftName }) {
  // 2026-06-09 — CT sort toggle: default = cycle_seq DESC (latest first);
  // click CT header → max→min (▼) → min→max (▲) → default.
  const [ctSort, setCtSort] = useState(null);   // null | "desc" | "asc"
  const cycleCtSort = () => setCtSort(s => s === "desc" ? "asc" : s === "asc" ? null : "desc");
  const ctArrow = ctSort === "desc" ? " ▼" : ctSort === "asc" ? " ▲" : " ⇅";

  // 2026-06-20 — Comment filter (composes WITH the CT sort): All / Commented /
  // No comment.  Lets the operator find big-CT cycles that have no note yet
  // (e.g. CT max→min + "No comment" → biggest uncommented cycles on top).
  const [cmtFilter, setCmtFilter] = useState("all");   // "all" | "filled" | "unfilled"

  // 2026-06-11 — COMMENT column: show the LATEST note added on each cycle via
  // the video floating box — works for Final/main AND sub machines, and notes
  // added from Fullscreen too (all share the same per-cycle key).  ONE batch
  // fetch of today's notes, polled live; we re-render ONLY when the data
  // actually changes (JSON guard) so the table never flickers.
  const [cmtMap, setCmtMap] = useState({});
  const cmtJsonRef = useRef("");
  useEffect(() => {
    if (!lineId) return;
    let stop = false;
    const pad = (n) => String(n).padStart(2, "0");
    const localDay = (off = 0) => {
      const d = new Date(); d.setDate(d.getDate() - off);
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    };
    const url = `/api/lines/${lineId}/comments-history`
              + `?date_from=${localDay(1)}&date_to=${localDay(0)}`;   // yesterday+today (night-shift safe)
    const load = () => {
      fetch(url)
        .then(r => (r.ok ? r.json() : Promise.reject(r.status)))
        .then(d => {
          if (stop) return;
          const m = {};
          // rows arrive newest-first → first seen per key = LATEST note.
          // 2026-09-19 — keyed by part_code AND shift.  `cycle_<seq>_<date>`
          // carries no shift, and cycle numbers restart every shift, so the
          // A-shift note on #648 was showing on B-shift #648 (operator: "comment
          // aa raha hai jabki kisi ne post nahi kiya" — YNC/YCA/YHB/Y17).  The
          // clip's own comment box was already shift-scoped; this list was not.
          for (const r of (d.rows || [])) {
            const pc = r && r.part_code;
            if (!pc) continue;
            const k = `${pc}|${r.shift_name || ""}`;
            if (!(k in m)) m[k] = r.text;
            if (!(`${pc}|*` in m)) m[`${pc}|*`] = r.text;    // any shift (only if ours is unknown)
          }
          const j = JSON.stringify(m);
          if (j !== cmtJsonRef.current) { cmtJsonRef.current = j; setCmtMap(m); }
        })
        .catch(() => {});
    };
    load();
    const t = setInterval(load, 8000);
    return () => { stop = true; clearInterval(t); };
  }, [lineId]);

  // Build the SAME per-cycle key the comment/remark boxes save under, so the
  // note matches.  Main (Final) → `cycle_<seq>_<date>` (identical to what
  // Fullscreen's video modal uses).  Sub → its real part_code, else the
  // synthesised `M<machineId>-C<seq>-<date>`.  Date = cycle ts (UTC slice),
  // exactly how the save side builds it.
  const _keyFor = (c) => {
    const day = c.ts ? new Date(c.ts).toISOString().slice(0, 10)
                     : new Date().toISOString().slice(0, 10);
    if (isMain) return `cycle_${c.cycle_seq}` + (c.ts ? `_${day}` : "");
    const realPC = c.part_code;
    return (realPC && String(realPC).trim())
      ? String(realPC).trim().replace(/:$/, "")
      : `M${machineId || 0}-C${c.cycle_seq}-${day}`;
  };
  // The note for THIS cycle: same shift as the cycle (the list only holds the
  // dashboard's current shift), else a legacy note saved without a shift —
  // exactly the rule the clip's comment box uses.  Shift unknown → any shift.
  const _cmtFor = (c) => {
    const k = _keyFor(c);
    const sh = c.shift_name || (shiftName && shiftName !== "—" ? shiftName : "");
    if (!sh) return cmtMap[`${k}|*`];
    return cmtMap[`${k}|${sh}`] || cmtMap[`${k}|`];
  };
  // Comment status uses the SAME live cmtMap the COMMENT column renders.
  const _hasCmt   = (c) => !!String(_cmtFor(c) || "").trim();
  const _overRows = (cycles || []).filter(c => c.ct != null && Number(c.ct) > ideal);
  const _totalOver = _overRows.length;
  const _filledN   = _overRows.filter(_hasCmt).length;
  const _unfilledN = _totalOver - _filledN;
  // Pipeline: over-target → comment filter → CT sort (both apply together).
  const rows = _overRows
    .filter(c => cmtFilter === "filled"   ? _hasCmt(c)
               : cmtFilter === "unfilled" ? !_hasCmt(c)
               : true)
    .slice()
    .sort((a, b) => {
      if (ctSort === "asc")  return (Number(a.ct) || 0) - (Number(b.ct) || 0);
      if (ctSort === "desc") return (Number(b.ct) || 0) - (Number(a.ct) || 0);
      return (Number(b.cycle_seq) || 0) - (Number(a.cycle_seq) || 0);
    });
  // Warm only the FIRST FEW rows on open, not the visible page.  Warming 10
  // meant paying for 9 renders nobody opened — measured as most of the CPU
  // spike that pushed the box from load 32 to 71.  The eye lands on the top of
  // the list; hover (below) covers whatever they actually pick.
  const _topSeqs = rows.slice(0, 3).map(c => c.cycle_seq).join(",");
  useEffect(() => {
    if (!_topSeqs) return;
    warmClips(lineId, isMain ? 0 : machineId, _topSeqs.split(",").map(Number));
  }, [_topSeqs, lineId, isMain, machineId]);

  const th = { textAlign: "left", fontSize: 10, fontWeight: 800, letterSpacing: ".06em",
               color: "#94a3b8", padding: "6px 10px", borderBottom: "1px solid rgba(255,255,255,0.14)" };
  const td = { fontSize: 12, padding: "6px 10px", borderBottom: "1px solid rgba(255,255,255,0.06)",
               color: D ? "#e2e8f0" : "#1e293b", fontFamily: "monospace" };
  return (
    <div style={{ marginTop: 8 }}>
      <button onClick={onBack} style={{
        background: "none", border: "1px solid rgba(245,158,11,0.5)", color: "#f59e0b",
        borderRadius: 6, padding: "3px 12px", fontSize: 11, fontWeight: 800,
        cursor: "pointer", marginBottom: 8,
      }}>← Back to graphs</button>
      <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 10, marginBottom: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 800, color: "#f59e0b" }}>
          TODAY — Over-Target Cycles{" "}
          <span style={{ color: "#64748b", fontWeight: 600 }}>
            · {rows.length}{cmtFilter !== "all" ? ` of ${_totalOver}` : ""}
          </span>
        </div>
        <div style={{ display: "flex", gap: 4, marginLeft: "auto" }}>
          {[["all", `All · ${_totalOver}`],
            ["filled", `Commented · ${_filledN}`],
            ["unfilled", `No comment · ${_unfilledN}`]].map(([k, label]) => (
            <button key={k} onClick={() => setCmtFilter(k)} style={{
              padding: "3px 10px", fontSize: 10, fontWeight: 800, letterSpacing: ".04em",
              borderRadius: 6, cursor: "pointer", whiteSpace: "nowrap",
              border: "1px solid rgba(245,158,11,0.5)",
              background: cmtFilter === k ? "#f59e0b" : "transparent",
              color: cmtFilter === k ? "#1a1205" : "#f59e0b",
            }}>{label}</button>
          ))}
        </div>
      </div>
      {rows.length === 0 ? (
        <div style={{ fontSize: 12, color: "#64748b", fontStyle: "italic", padding: "14px 0" }}>
          {_totalOver === 0 ? "No over-target cycles right now."
            : cmtFilter === "unfilled" ? "All over-target cycles have comments ✓"
            : cmtFilter === "filled"   ? "No commented over-target cycles yet."
            : "No over-target cycles right now."}
        </div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr>
            <th style={th}>CYCLE</th>
            <th style={{ ...th, cursor: "pointer", userSelect: "none", color: ctSort ? "#f59e0b" : "#94a3b8" }}
                onClick={cycleCtSort}
                title="Sort by CT — click: max→min / min→max / default">CT{ctArrow}</th>
            <th style={th}>TIME</th>
            <th style={th}>RESULT</th>
            <th style={th}>COMMENT</th>
            <th style={{ ...th, textAlign: "center" }}>VIDEO</th>
          </tr></thead>
          <tbody>
            {rows.map((c, i) => (
              <tr key={`${c.cycle_seq}-${i}`}>
                <td style={td}>#{c.cycle_seq}</td>
                <td style={{ ...td, color: "#ef4444", fontWeight: 700 }}>{Number(c.ct).toFixed(2)}s</td>
                <td style={td}>{c.ts ? new Date(c.ts).toLocaleTimeString("en-GB") : "—"}</td>
                <td style={td}>
                  {c.is_ng
                    ? <span style={{ color: "#ef4444", fontWeight: 800 }}>Alarm</span>
                    : <span style={{ color: "#22c55e", fontWeight: 800 }}>OK</span>}
                </td>
                <td style={{ ...td, fontFamily: "inherit", whiteSpace: "normal",
                             wordBreak: "break-word", maxWidth: 360, lineHeight: 1.35,
                             color: D ? "#cbd5e1" : "#334155" }}>
                  {_cmtFor(c) || <span style={{ color: "#64748b" }}>—</span>}
                </td>
                <td style={{ ...td, textAlign: "center" }}>
                  <button onClick={() => onVideo && onVideo(c)}
                    onMouseEnter={() => warmClips(lineId, isMain ? 0 : machineId,
                                                  [c.cycle_seq])}
                    style={{
                    background: "#f59e0b", border: "none", color: "#1a1205", borderRadius: 5,
                    padding: "3px 12px", fontSize: 11, fontWeight: 800, cursor: "pointer",
                  }}>▶ VIDEO</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function OtModal({ data, metric, setMetric, machineName, onClose, D, otCycles, otIdeal, onVideo, lineId, isMain, lossAll, machineId, lossSeconds, shiftName }) {
  const rows = (data && data.rows) || [];
  const [showTbl, setShowTbl] = useState(false);
  const tBtn = (m, label) => (
    <button onClick={() => setMetric(m)} style={{
      padding: "4px 14px", fontSize: 11, fontWeight: 800, letterSpacing: ".06em",
      border: "1px solid rgba(245,158,11,0.5)", borderRadius: 6, cursor: "pointer",
      background: metric === m ? "#f59e0b" : "transparent",
      color: metric === m ? "#1a1205" : "#f59e0b",
    }}>{label}</button>
  );
  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.72)", zIndex: 9000,
      display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        background: D ? "#0d1420" : "#ffffff", border: "1px solid rgba(245,158,11,0.4)",
        borderRadius: 12, padding: "16px 20px 20px", width: "min(1100px, 94vw)",
        maxHeight: "90vh", overflowY: "auto", boxShadow: "0 20px 60px rgba(0,0,0,0.6)",
        fontFamily: "'Barlow',sans-serif",
      }}>
        <div style={{ position: "relative", display: "flex",
                      justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontSize: 15, fontWeight: 900, color: "#f59e0b" }}>
            OVER TARGET — Last 30 Days
            <span style={{ color: D ? "#9fb0c9" : "#475569", fontWeight: 700, fontSize: 12 }}>
              {"  ·  "}{machineName}
            </span>
          </span>

          {/* 2026-08-07 — TOTAL LOSS, dead-centre of this popup's header.
              Same figure and formatting the header pill used to carry; it now
              lives only here.  `lossSeconds` is a live prop off the 8-second
              wallboard poll, so this keeps ticking while the popup is open. */}
          {/* 2026-08-11 — operator spec: on FINAL INSPECTION this popup lists
              EVERY machine's own gain/loss side by side ("sabka alag alag"),
              not one combined figure — the per-machine numbers are the point,
              and a sum of machines that run in parallel would not mean much
              anyway.  Every other machine's popup keeps the single centred
              figure it already had.  Both tick live off the 8 s poll. */}
          {(() => {
            const p    = (n) => String(n).padStart(2, "0");
            const hms  = (v) => {
              const t = Math.abs(Math.round(Number(v)));
              return `${p(Math.floor(t / 3600))}:${p(Math.floor((t % 3600) / 60))}:${p(t % 60)}`;
            };
            const chip = (label, v, big) => {
              const neg = Number(v) < 0;
              const clr = neg ? "#22c55e" : "#f59e0b";
              const rgb = neg ? "34,197,94" : "245,158,11";
              return (
                <div key={label} style={{
                  display: "flex", alignItems: "center", gap: big ? 10 : 10,
                  justifyContent: big ? "center" : "space-between",
                  minWidth: big ? 0 : 250,
                  background: D ? `rgba(${rgb},0.14)` : `rgba(${rgb},0.10)`,
                  border: `1px solid rgba(${rgb},0.55)`,
                  borderRadius: 999, padding: big ? "3px 16px" : "2px 10px",
                  whiteSpace: "nowrap", lineHeight: 1.15,
                }}>
                  <span style={{ fontSize: big ? 15 : 11, fontWeight: 900, color: clr,
                                 fontFamily: "'Times New Roman', Times, serif",
                                 letterSpacing: ".04em", opacity: 0.9 }}>{label}</span>
                  <span style={{ fontSize: big ? 15 : 12, fontWeight: 900, color: clr,
                                 fontFamily: "'Times New Roman', Times, serif",
                                 letterSpacing: ".04em" }}>
                    {neg ? "−" : ""}{hms(v)}
                  </span>
                </div>
              );
            };

            if (isMain && Array.isArray(lossAll) && lossAll.length) {
              // 2026-08-11 — ONE VERTICAL COLUMN, gain at the top down to the
              // biggest loss.  The wrapped row this replaced was absolutely
              // centred and rode over the title and the % / cycles toggle; in
              // normal flow the header just grows to fit and nothing overlaps.
              // Sorted ascending, so the negatives (green gains) sit first and
              // the worst loss lands at the bottom — the column reads top-down.
              const ordered = [...lossAll].sort((x, y) => Number(x.loss) - Number(y.loss));
              return (
                <div style={{
                  display: "flex", flexDirection: "column", gap: 4,
                  alignItems: "stretch", marginLeft: "auto", marginRight: 14,
                  pointerEvents: "none", flex: "0 0 auto",
                }}>
                  {ordered.map(m => chip(m.name, m.loss, false))}
                </div>
              );
            }
            if (lossSeconds == null) return null;
            return (
              <div style={{
                position: "absolute", left: "50%", top: "50%",
                transform: "translate(-50%, -50%)", pointerEvents: "none",
              }}>
                {/* 2026-08-14 — labelled "Speed", not "Total".  The number has
                    always been SUM(ct − ideal) over the running cycles — a pure
                    speed figure — but calling it "Total Loss" read as though it
                    included breakdown / setup / quality time, which it never did. */}
                {chip(Number(lossSeconds) < 0 ? "Speed Gain" : "Speed Loss", lossSeconds, true)}
              </div>
            );
          })()}

          <button onClick={onClose} style={{ background: "none", border: "none",
                   color: "#94a3b8", fontSize: 22, cursor: "pointer", lineHeight: 1 }}>×</button>
        </div>
        {showTbl ? (
          <OtTable cycles={otCycles} ideal={otIdeal} onVideo={onVideo} shiftName={shiftName}
                   onBack={() => setShowTbl(false)} D={D}
                   lineId={lineId} isMain={isMain} machineId={machineId} />
        ) : (
          <>
            <div style={{ fontSize: 10, color: "#64748b", margin: "2px 0 10px" }}>
              Cycles slower than ideal{data && data.ideal_ct ? ` (${data.ideal_ct}s)` : ""} — A &amp; B shift, toggle % / cycle count. Tap today's bar for its over-target videos.
            </div>
            <div style={{ display: "flex", gap: 8, marginBottom: 4 }}>
              {tBtn("pct", "% OVER")}
              {tBtn("count", "No. OF CYCLES")}
            </div>
            <OtBars title="A SHIFT" rows={rows.filter(r => r.shift === "A")} metric={metric} onToday={() => setShowTbl(true)} />
            <OtBars title="B SHIFT" rows={rows.filter(r => r.shift === "B")} metric={metric} onToday={() => setShowTbl(true)} />
          </>
        )}
      </div>
    </div>
  );
}

function MachineRow({ machine, idealCt, onPick, onCycleVideo, isMain = false, lossAll, chartReady, lineId, okOverride, ngOverride, shiftName }) {
  const _rawCycles = machine.cycles || [];
  // 2026-06-04 — Upper-Rail anti-swap DISPLAY safeguard.  A cycle flagged NG
  // with ~0s CT is a register-mirror PHANTOM (the collector OK<->NG swap
  // artifact — root-fixed at source 2026-06-04, but pre-fix rows remain in
  // ct_log).  It's real production mislabeled, so: (a) drop it from the chart
  // window (no real cycle-time to plot — kills the red 0.0s flood), and
  // (b) still count it as OK production via _phantomOk.  Machines with no
  // 0s-NG rows are unaffected (_phantomOk = 0, allCycles unchanged).
  const _isPhantom = (c) => c.is_ng && c.ct != null && Number(c.ct) < 0.1;
  const _phantomOk = _rawCycles.filter(_isPhantom).length;
  // 2026-06-10 — also drop shift-start "burst" phantoms: implausibly fast
  // cycles (CT < half the ideal) that the PLC register NEVER counts (08:30
  // warm-up scan-fail rows, duplicate cycle_seq).  Unlike the 0s-NG above
  // these are NOT counted as OK — just excluded from the chart + cycle counts
  // + over-target.  Applies to main + subs (each vs its own ideal).
  const _isBurstPhantom = (c) =>
    c.ct != null && idealCt > 0 && Number(c.ct) < idealCt * 0.5;
  const allCycles  = _rawCycles.filter(c => !_isPhantom(c) && !_isBurstPhantom(c));
  // 2026-05-28 — Operator: "thoda gap kr de ct to ct kuch dikh nhi rha".
  // Window halved (50 → 25) so each cycle gets ~2x horizontal space —
  // CT labels above dots no longer overlap, stats clearly readable.
  // Scrollbar at bottom still lets operator scroll through older cycles.
  const WIN = 25;

  // Window state: default = sticky to END (latest 50).
  const [winStart, setWinStart] = useState(() =>
    Math.max(0, allCycles.length - WIN));
  const [sticky, setSticky]     = useState(true);

  // 2026-06-06 — OVER TARGET 30-day popup (click the badge).
  const [otModal,  setOtModal]  = useState(null);    // {rows, ideal_ct} | null
  const [otMetric, setOtMetric] = useState("pct");   // "pct" | "count"
  const _otOpen = () => {
    const url = `/api/lines/${lineId}/over-target-history`
              + (isMain ? "" : `?sub_id=${machine.sub_id}`);
    setOtModal({ rows: [], _loading: true });
    fetch(url)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(d => setOtModal(d && Array.isArray(d.rows) ? d : { rows: [] }))
      .catch(() => setOtModal({ rows: [], _err: true }));
  };

  // When new cycles arrive and we're in sticky mode, snap to end.
  useEffect(() => {
    if (sticky) setWinStart(Math.max(0, allCycles.length - WIN));
  }, [allCycles.length, sticky]);

  const canvasRef = useRef(null);
  const chartRef  = useRef(null);

  const cycles  = allCycles.slice(winStart, winStart + WIN);
  const maxWin  = Math.max(0, allCycles.length - WIN);

  // ── TOTAL LOSS (shown inside the OVER TARGET popup) ────────────────
  // Comes from the SERVER (wallboard-cycles → loss_seconds), which sums
  // (ct − ideal) ONLY over cycles that completed while the LINE status was
  // RUNNING.  Operator spec: "koi bhi loss jisme line ruki hui hai voh count
  // nahi hoga ... sub-machines ka bhi status final ke equivalent hi maana
  // jayega" — so breakdown / material-wait never inflates it, and every
  // sub-machine follows the Final Inspection status.
  // Signed: a machine that beat its ideal all shift reads as a gain.
  // Re-read on every poll (8 s), so the popup ticks live while it is open.
  // Falls back to a client-side sum if an older API omits the field.
  const lossSec = (machine.loss_seconds != null)
    ? Number(machine.loss_seconds)
    : allCycles.reduce(
        (acc, c) => acc + (c.ct == null ? 0 : (Number(c.ct) - idealCt)), 0);

  // ── Chart.js instance (create / update / destroy) ────────────────
  useEffect(() => {
    if (!chartReady || !canvasRef.current || !window.Chart) return;
    if (cycles.length === 0) {
      // No data — tear down any existing chart so the empty-state message renders
      if (chartRef.current) { chartRef.current.destroy(); chartRef.current = null; }
      return;
    }

    const idealCT = idealCt;
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
    // 2026-06-01 — OK dots ALWAYS green now (binary, like Fullscreen.jsx).
    // Earlier OK-but-slow cycles (CT > ideal) were painted red via the
    // CT-based _ptClr, which operators misread as a reject.  Operator:
    // "dono ko green wale jaisa kr de, bs jo actual NG h uska symbol rahe".
    // Only the DOTS go binary — the LINE segment colour below stays
    // CT-vs-ideal (red above ideal / green below) per the 2026-05-28 spec.
    const ptColors = cycles.map(cy => cy.is_ng ? FS_BAD : FS_OK);
    // NG dots also bigger so they stand out + white border ring.
    const ptRadius = cycles.map(cy => cy.is_ng ? 5 : 2);
    const ptHoverR = cycles.map(cy => cy.is_ng ? 11 : 7);
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
              const status = cy.is_ng ? "Alarm ⚠" : "OK ✓";
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
          // 2026-05-27 — NG cycles get a red tooltip box (matches the
          // Final-Inspection styling).  Pulls is_ng off the cycle row
          // being hovered.  OK styling unchanged.
          backgroundColor: ctx => {
            const idx = ctx.tooltip?.dataPoints?.[0]?.dataIndex;
            const cy  = idx != null ? cycles[idx] : null;
            return cy?.is_ng ? "rgba(58,13,16,.97)" : "rgba(15,23,41,.95)";
          },
          borderColor: ctx => {
            const idx = ctx.tooltip?.dataPoints?.[0]?.dataIndex;
            const cy  = idx != null ? cycles[idx] : null;
            return cy?.is_ng ? "#ef4444" : "rgba(59,130,246,.5)";
          },
          titleColor: ctx => {
            const idx = ctx.tooltip?.dataPoints?.[0]?.dataIndex;
            const cy  = idx != null ? cycles[idx] : null;
            return cy?.is_ng ? "#fecaca" : "#e2e8f0";
          },
          bodyColor: ctx => {
            const idx = ctx.tooltip?.dataPoints?.[0]?.dataIndex;
            const cy  = idx != null ? cycles[idx] : null;
            return cy?.is_ng ? "#fecaca" : "#e2e8f0";
          },
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
            callback: v => `${Math.round(v)}s`,
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
      // 2026-05-27 — Stash latest cycles on the chart instance so the
      // ngMarkerPlugin (registered ONCE at chart creation) can read
      // the freshest data instead of its stale closure capture.
      // Earlier symptom: ⚠ markers persisted on cycles that weren't
      // NG anymore — operator saw bogus NG flags on the wallboard.
      chartRef.current._cyclesRef = cycles;
      chartRef.current.data       = data;
      chartRef.current.options    = options;
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
          // 2026-05-27 — Read cycles from the chart instance (set on
          // every update at line ~390 above) instead of the stale
          // closure capture from chart-creation time.  Falls back to
          // the captured `cycles` only on the very first render before
          // _cyclesRef has been attached.
          const liveCycles = chart._cyclesRef || cycles;
          const ctx2 = chart.ctx;
          ctx2.save();
          ctx2.textAlign    = "center";
          ctx2.textBaseline = "bottom";
          // 2026-09-07 — Anti-clutter: with a full shift of cycles the per-dot
          // CT numbers overlapped into an unreadable smear ("cycle congested").
          // NG dots ALWAYS keep their ⚠ + red CT (they matter); OK numbers are
          // only drawn when at least MIN_GAP px past the last label — so on any
          // width they thin out to a readable spacing (wider chart = more shown).
          const MIN_GAP = 26;
          let lastLabelX = -Infinity;
          meta.data.forEach((pt, i) => {
            const cy = liveCycles[i];
            if (!cy || cy.ct == null) return;
            const x = pt.x, y = pt.y;
            const ct = Number(cy.ct).toFixed(1) + "s";
            if (cy.is_ng) {
              // ⚠ glyph above, then NG CT in red just under it — always shown
              ctx2.font      = "bold 11px 'Segoe UI Symbol', sans-serif";
              ctx2.fillStyle = "#fbbf24";
              ctx2.fillText("⚠", x, y - 18);
              ctx2.font      = "bold 10px monospace";
              ctx2.fillStyle = FS_BAD;
              ctx2.fillText(ct, x, y - 6);
              lastLabelX = x;
            } else if (x - lastLabelX >= MIN_GAP) {
              // OK CT in green above the dot — skipped if it would collide
              ctx2.font      = "bold 9px monospace";
              ctx2.fillStyle = FS_OK;
              ctx2.fillText(ct, x, y - 6);
              lastLabelX = x;
            }
          });
          ctx2.restore();
        },
      };
      chartRef.current = new window.Chart(canvasRef.current.getContext("2d"), {
        type: "line", data, options, plugins: [ngMarkerPlugin],
      });
      // 2026-05-27 — Seed _cyclesRef on first creation too so the
      // plugin's very first draw also sees fresh data, not closure.
      chartRef.current._cyclesRef = cycles;
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
                    padding: "0 6px",
                    // 2026-08-06 — positioning context for the centred LOSS
                    // chip below, so it sits dead-centre of the row instead of
                    // drifting with the title / OK-NG widths.
                    position: "relative" }}>
        <div style={{
          fontSize: isMain ? 14 : 13,
          fontWeight: 900,
          // Title must never run under the centred LOSS chip.
          flex: 1, minWidth: 0, overflow: "hidden",
          textOverflow: "ellipsis", whiteSpace: "nowrap",
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

        {/* 2026-08-07 — the Total Loss pill used to sit here, centred in the
            header.  Operator moved it INSIDE the OVER TARGET popup ("uske
            andar center mein aaye"), so the graph header is back to just
            name + OK/NG.  The figure itself is unchanged — see `lossSec`
            above and the pill in OtModal. */}

        <div style={{ display: "flex", gap: 10, alignItems: "center",
                      flexShrink: 0 }}>
          {/* 2026-05-24 — Show OK + NG counts separately so operator
              can see per-machine reject rate at a glance.  NG count
              shown in red, OK in green. */}
          {(() => {
            // 2026-06-06 — Main (Final) reads the PLC register (okOverride/
            // ngOverride from summary.kpi) — the SAME source the Fullscreen reads,
            // so the two UIs ALWAYS match.  OK has a 619-stuck guard: if the
            // register carries a previous shift's count (>> this shift's real
            // cycle count) show the real count.  NG = D102 register.  Sub-machines
            // use cycle_seq (= their own D-register, per-shift, auto-reset).
            const _okCy = allCycles.filter(c => !c.is_ng);
            const _ngCy = allCycles.filter(c => c.is_ng);
            const _maxSeq   = (arr) => arr.length ? Math.max(...arr.map(x => Number(x.cycle_seq) || 0)) : 0;
            const _seqCount = (arr) => { const m = _maxSeq(arr); return (m > 0 && m <= arr.length * 1.5) ? m : arr.length; };
            const _mainOk = _seqCount(_okCy);
            const okN = !isMain ? _seqCount(_okCy)
                      : (okOverride == null ? _mainOk
                         : ((okOverride - _mainOk) > 200 ? _mainOk : okOverride));
            // Sub-machine NG (alarm) is recorded with ct=0 — the NG poller logs
            // the ng_bit event, it doesn't measure a cycle time — so the chart's
            // 0s-phantom filter (ct<0.1) wrongly dropped EVERY real sub NG and
            // the tile always showed "Alarm: 0".  Count NG from the RAW cycles so
            // real alarms show on every machine/line; the chart still hides the
            // 0s points (allCycles).  Main keeps its PLC-register ngOverride.
            const _rawNgCy = _rawCycles.filter(c => c.is_ng);
            const ngN = !isMain ? _seqCount(_rawNgCy)
                      : (ngOverride != null ? ngOverride : _seqCount(_rawNgCy));
            return (
              <span style={{ fontSize: 10, fontWeight: 700, color: textMut,
                              fontFamily: "monospace" }}>
                <span style={{ color: FS_OK }}>OK: {okN}</span>
                <span style={{ margin: "0 6px", color: textMut }}>·</span>
                <span style={{ color: FS_BAD }}>Alarm: {ngN}</span>
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
        {/* 2026-06-01 — "Over target" QTY badge.  Operator: graph ke side
            me dikhao kitne CT target (ideal) se upar gaye.  Absolute overlay
            in the top-right corner so the chart width stays unchanged.
            Counts ALL cycles this shift (OK + NG) whose ct > ideal.  Amber =
            slow-warning, NOT reject-red.  Display-only: reads allCycles that
            is already loaded for the graph — no API / collector / DB touch.
            pointerEvents:none so dot-tap (video) + panel-open pass through. */}
        {(() => {
          const over  = allCycles.filter(c => c.ct != null && c.ct > idealCt).length;
          // 2026-06-06 — denominator = the SAME register-pinned per-shift cycle
          // count as the OK/NG tags (MAX cycle_seq), for main + subs alike, so
          // "of N" always matches the OK/NG / slider count.
          const _allMax = allCycles.length ? Math.max(...allCycles.map(c => Number(c.cycle_seq) || 0)) : 0;
          const total = (_allMax > 0 && _allMax <= allCycles.length * 1.5) ? _allMax : allCycles.length;
          const pct   = total ? Math.round((over / total) * 100) : 0;
          return (
            <div
              onClick={(e) => { e.stopPropagation(); _otOpen(); }}
              title="Tap — last 30 days over-target (A / B shift)"
              style={{
              position: "absolute", top: 4, right: 6,
              background: D ? "rgba(20,14,4,0.82)" : "rgba(255,251,235,0.92)",
              border: "1px solid rgba(245,158,11,0.5)",
              borderRadius: 5, padding: "2px 7px",
              display: "flex", flexDirection: "column", alignItems: "center",
              lineHeight: 1.05, pointerEvents: "auto", cursor: "pointer",
            }}>
              <span style={{ fontSize: 8, fontWeight: 800, letterSpacing: ".06em",
                              color: "#f59e0b" }}>OVER TARGET</span>
              <span style={{ fontSize: 18, fontWeight: 900, fontFamily: "monospace",
                              color: "#f59e0b" }}>{over}</span>
              <span style={{ fontSize: 8, fontWeight: 700, fontFamily: "monospace",
                              color: textMut }}>▲ {pct}% of {total}</span>
            </div>
          );
        })()}

        {otModal && createPortal(
          <OtModal
            data={otModal} metric={otMetric} setMetric={setOtMetric}
            machineName={machine.machine_name || (isMain ? "Final Inspection" : "Machine")}
            onClose={() => setOtModal(null)} D={D}
            otCycles={allCycles} otIdeal={idealCt} shiftName={shiftName}
            lossSeconds={lossSec}
            lossAll={lossAll}
            lineId={lineId} isMain={isMain}
            machineId={machine.id || machine.sub_id || 0}
            onVideo={(cy) => { if (typeof onCycleVideo === "function") onCycleVideo(machine, cy, isMain); }}
          />, document.body)}
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
                                  letterSpacing: ".05em" }}>OK · Alarm</div>
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
  // Player element, so the always-visible progress bar can read its time.
  const vidRef = useRef(null);
  // 2026-08-10 — the URL may carry a NAME ("/wallboard/left/YHB-SS") or the
  // numeric id ("/wallboard/left/11").  A numeric URL is used verbatim, so every
  // existing wall display / bookmark keeps working with zero extra requests; a
  // named URL costs one tiny anonymous lookup before the first poll.  Every API
  // call below still uses the numeric id, so nothing else had to change.
  const { lineId: lineRef } = useParams();
  const [resolvedId, setResolvedId] = useState(
    /^\d+$/.test(lineRef || "") ? lineRef : null);

  useEffect(() => {
    if (/^\d+$/.test(lineRef || "")) { setResolvedId(lineRef); return; }
    let stop = false;
    axios.get(`/api/lines/resolve-ref/${encodeURIComponent(lineRef || "")}`)
      .then(r => { if (!stop && r.data?.line_id) setResolvedId(String(r.data.line_id)); })
      .catch(() => { /* bad name -> stays null, tiles show "no data" */ });
    return () => { stop = true; };
  }, [lineRef]);

  const lineId = resolvedId;
  const [data, setData] = useState(null);
  const [summary, setSum] = useState(null);
  // 2026-09-13 — Loop Pipe line-switcher (Loop Pipe lines only): when THIS line
  // is a Loop Pipe line the header line-NAME turns into a dropdown listing every
  // Loop Pipe line so the Supervisor wall can switch between them. Empty for
  // every other line → the line name stays plain text.
  const [loopLines, setLoopLines] = useState([]);
  const [loopMenuOpen, setLoopMenuOpen] = useState(false);
  useEffect(() => {
    if (!lineId) return;
    let alive = true;
    const isLoop = (l) =>
      String(l?.db_table_name || "").toLowerCase().startsWith("loop_pipe");
    const _tok = (typeof window !== "undefined"
                    && sessionStorage.getItem("mes_token")) || "";
    axios.get("/api/lines", { headers: _tok ? { Authorization: `Bearer ${_tok}` } : {} })
      .then(r => {
        if (!alive) return;
        const all = Array.isArray(r.data) ? r.data : [];
        const cur = all.find(l => String(l.id) === String(lineId));
        if (cur && isLoop(cur)) {
          setLoopLines(all.filter(l => isLoop(l) && l.is_active !== false)
                          .sort((a, b) => a.id - b.id));
        } else {
          setLoopLines([]);
        }
      })
      .catch(() => { /* keep last known list on transient errors */ });
    return () => { alive = false; };
  }, [lineId]);
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
  // 2026-08-10 — cover the <video> while the clip renders + buffers.  The
  // browser's own round spinner looked like a stall on the shop-floor TV;
  // this drives a calm shimmer + progress bar instead.  Cleared the moment
  // playback actually starts (see onPlaying / the 50%-buffered gate).
  const [vidLoading, setVidLoading] = useState(false);
  const [vidBuffered, setVidBuffered] = useState(0);   // 0..1
  // 2026-08-12 — kill the browser's default body margin while this wall
  // display is mounted.  There is no CSS reset in this app, so `body { margin:
  // 8px }` was letting the white page background show as a border all the way
  // round the dark dashboard on the 65" panel.  Restored on unmount so the
  // ordinary padded pages are unaffected.
  useEffect(() => {
    const b = document.body, prevM = b.style.margin, prevBg = b.style.background;
    b.style.margin = "0";
    b.style.background = "#0b0e13";
    return () => { b.style.margin = prevM; b.style.background = prevBg; };
  }, []);

  const [vidStall,    setVidStall]    = useState(false); // stalled AFTER play began
  // 2026-08-11 — show the operator how long they have actually been waiting,
  // and log it.  A ticking number reads as "working" where a frozen spinner
  // reads as "hung", and it turns "video slow hai" into a measured figure on
  // the Waiting Time page.
  const [vidWait, setVidWait] = useState(0);      // seconds, ticking
  const vidTimerRef = useRef(null);               // stopwatch -> telemetry
  const vidSrcRef   = useRef(null);               // promise of X-Clip-Source
  const vidWaitStart = useRef(0);                 // elapsed is measured, not accumulated
  // A <video> tag never exposes response headers, so ask for one byte and read
  // X-Clip-Source off that — tells the Waiting Time page whether the clip came
  // off the archive or was transcoded on the click.  Defined at component scope
  // so the <video onLoadStart> handler (in render) can call it — it used to be a
  // local const inside openCycleVideo, which threw "_probeSrc is not defined" in
  // render and meant the probe never ran.
  const _probeSrc = useCallback((u, el) => {
    try {
      // Kept as a promise as well as on the element: an archived clip starts
      // playing in ~90 ms, long before this resolves, so reading the dataset
      // synchronously labelled every fast sample "render" — the Waiting Time
      // page then showed 0 archive hits while most clicks were archive hits.
      vidSrcRef.current = fetch(u, { headers: { Range: "bytes=0-0" } })
        .then(r => {
          const s = r.headers.get("x-clip-source") || "render";
          if (el) el.dataset.clipSource = s;
          return { source: s };
        })
        .catch(() => ({ source: "render" }));
    } catch { vidSrcRef.current = null; }
  }, []);
  const openCycleVideo = useCallback((machine, cy, isMain) => {
    if (!cy || cy.ct == null) return;
    const token = sessionStorage.getItem("mes_token") || "";
    // Cycle-video endpoints differ for main line vs sub-machines — both
    // accept ?token= for HTML5 <video src=...> auth.
    // 2026-07-31 — `r=` is now the CYCLE, not Date.now().  A timestamp made
    // every re-click a brand-new URL, so the clip could never be reused from
    // the browser (or a Cloudflare edge) and was re-pulled across the site's
    // single internet uplink every single time.  Keyed on the cycle it still
    // busts correctly between different dots, and a given cycle's clip is
    // immutable so reusing it is always right.  (The retry path below appends
    // its own Date.now() when a load genuinely fails.)
    // 2026-08-11 — say WHICH row was clicked.  An Alarm row and an OK row can
    // carry the same cycle_seq, so without this the server just took the later
    // one and the wrong clip could play.  For an Alarm the server also extends
    // the clip through the NEXT cycle, returning one continuous video.  The
    // cache-buster gets an "n" for Alarms so the two never share a cached URL.
    const _ngQ = `&ng=${cy.is_ng ? 1 : 0}`;
    const _r   = `${cy.cycle_seq}${cy.is_ng ? "n" : ""}`;
    const url = isMain
      ? `/api/lines/${lineId}/cycle-video?cycle_seq=${cy.cycle_seq}`
        + _ngQ + `&token=${encodeURIComponent(token)}&r=${_r}`
      : `/api/submachines/${machine.sub_id}/cycle-video?cycle_seq=${cy.cycle_seq}`
        + _ngQ + `&token=${encodeURIComponent(token)}&r=${_r}`;
    setVidLoading(true); setVidBuffered(0); setVidStall(false); setVidWait(0);
    vidWaitStart.current = performance.now();
    vidTimerRef.current = startTimer("video",
      String(machine.machine_name || (isMain ? "Final Inspection" : `M-${machine.machine_seq}`)),
      { lineId });
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
    // lineId is null until a NAMED url resolves — don't fire /api/lines/null/…
    if (!lineId) return;
    axios.get(`/api/lines/${lineId}/wallboard-cycles`)
         .then(r => setData(r.data))
         .catch(() => {});
  }, [lineId]);
  const fetchSummary = useCallback(() => {
    // lineId is null until a NAMED url resolves — don't fire /api/lines/null/…
    if (!lineId) return;
    axios.get(`/api/lines/${lineId}/wallboard-summary`)
         .then(r => setSum(r.data))
         .catch(() => {});
  }, [lineId]);

  // 2026-08-10 — Tab title carries the LINE'S OWN NAME, not "Wallboard L ·
  // Line 27".  Operators keep a Machine CT tab open per line; a narrow tab
  // shows only the first few characters, so "Wallboard…" made every tab look
  // identical and the line id was never visible.  Name first, exactly like
  // Fullscreen.jsx already does.  Separate effect so it can re-run when the
  // name arrives from /wallboard-summary without restarting the poll timers
  // below (those are keyed on the fetchers).
  useEffect(() => {
    const nm = summary?.kpi?.line_name;
    document.title = nm ? `${nm}.SUPERVISOR` : `SUPERVISOR · Line ${lineId || lineRef}`;
  }, [summary?.kpi?.line_name, lineId]);

  useEffect(() => {
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

  // 2026-06-17 — Esc closes the per-cycle video popup.  The × button's
  // title already said "Close (Esc)" but the key was never wired.  The
  // listener mounts only while a video is open and tears down on close,
  // so it never interferes with the R-orientation shortcut otherwise.
  // Safety net: if `playing` never fires (autoplay blocked, dead decoder)
  // don't let the loading cover sit on top of the clip forever.
  useEffect(() => {
    if (!vidLoading) return;
    const t = setTimeout(() => setVidLoading(false), 30000);
    const tick = setInterval(() => {
      if (vidWaitStart.current) setVidWait((performance.now() - vidWaitStart.current) / 1000);
    }, 100);
    return () => { clearTimeout(t); clearInterval(tick); };
  }, [vidLoading, videoCycle]);

  useEffect(() => {
    if (!videoCycle) return;
    const onEsc = (e) => { if (e.key === "Escape") setVideoCycle(null); };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [videoCycle]);

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

  // 2026-08-11 — per-machine gain/loss for the Final Inspection popup, which
  // lists every machine separately instead of one combined figure.  Built from
  // the same `loss_seconds` the individual chips use, so the popup can never
  // disagree with the row it came from.  Final Inspection itself is skipped:
  // it is the line's output, not a station, and showing it beside the stations
  // reads as if it were one of them.  Rebuilt on every 8 s poll so it ticks live.
  // 2026-08-11 — Final Inspection's own figure is listed too, alongside the
  // stations, so the column shows the whole line in one place.
  const lossBreakdown = allRows
    .filter(m => m.loss_seconds != null)
    .map(m => ({
      name: String(m.machine_name || "").trim() || `M-${m.machine_seq}`,
      loss: Number(m.loss_seconds),
      isMain: !!m.is_main,
    }));
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
      {/* Slide-nav menu — portaled to <body> so it escapes this wrapper's
          rotate() transform (a transformed ancestor breaks position:fixed).
          Trigger hidden; opened by the header-logo click below via the
          "tbdi:open-nav" window event. */}
      {createPortal(<SlideNav hideTrigger raise />, document.body)}
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
                onClick={() => window.dispatchEvent(new Event("tbdi:open-nav"))}
                title="Open menu"
                style={{ width: 40, height: 40, borderRadius: 8,
                          objectFit: "contain", cursor: "pointer",
                          background: "#fff", padding: 3, flexShrink: 0 }} />
          <div style={{ minWidth: 0, position: "relative" }}>
            {/* 2026-09-13 — Loop Pipe lines only: line-NAME becomes a dropdown to
                switch between Loop Pipe lines (keeps the Supervisor view). Every
                other line keeps the plain, non-clickable name. */}
            {loopLines.length > 0 ? (
              <div
                onClick={() => setLoopMenuOpen(o => !o)}
                title="Switch between Loop Pipe lines"
                style={{ fontSize: 15, fontWeight: 900, color: text,
                         letterSpacing: ".02em", lineHeight: 1.1,
                         whiteSpace: "nowrap", cursor: "pointer",
                         display: "inline-flex", alignItems: "center", gap: 6 }}>
                <span>{summary?.kpi?.line_name || "—"}</span>
                <span style={{ fontSize: 12, opacity: .55 }}>▾</span>
              </div>
            ) : (
              <div style={{ fontSize: 15, fontWeight: 900, color: text,
                            letterSpacing: ".02em", lineHeight: 1.1,
                            whiteSpace: "nowrap" }}>
                {summary?.kpi?.line_name || "—"}
              </div>
            )}
            <div style={{ fontSize: 10, color: textMut, marginTop: 1,
                          whiteSpace: "nowrap" }}>
              {data?.record_date || new Date().toISOString().slice(0,10)}
            </div>
            {/* Loop Pipe line dropdown */}
            {loopLines.length > 0 && loopMenuOpen && (
              <div onClick={() => setLoopMenuOpen(false)}
                   style={{ position: "absolute", top: "100%", left: 0, marginTop: 4,
                     background: card, border: `1px solid ${border}`, borderRadius: 8,
                     minWidth: 220, maxHeight: 320, overflowY: "auto",
                     boxShadow: "0 12px 32px rgba(0,0,0,.55)",
                     zIndex: 9999, padding: 4 }}>
                {loopLines.map((l, idx) => {
                  const isCurrent = String(l.id) === String(lineId);
                  const ACC = "#38bdf8";
                  return (
                    <div key={l.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        setLoopMenuOpen(false);
                        if (isCurrent) return;
                        // Preserve the Supervisor view and full-reload for fresh
                        // line state. line_code resolves via resolve-ref.
                        const ref = encodeURIComponent(
                          String(l.line_code || l.line_name || l.id).trim().replace(/\s+/g, "-"));
                        const p = window.location.pathname;
                        const dest = p.endsWith("/MANAGEMENT")        ? `/${ref}/MANAGEMENT`
                                   : p.startsWith("/wallboard/left/")  ? `/wallboard/left/${l.id}`
                                   :                                     `/${ref}/SUPERVISOR`;
                        window.location.assign(dest);
                      }}
                      style={{ padding: "8px 10px", borderRadius: 5, fontSize: 12,
                        color: isCurrent ? ACC : text,
                        background: isCurrent ? `${ACC}15` : "transparent",
                        cursor: isCurrent ? "default" : "pointer",
                        display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}
                      onMouseEnter={e => { if (!isCurrent) e.currentTarget.style.background = `${ACC}10`; }}
                      onMouseLeave={e => { if (!isCurrent) e.currentTarget.style.background = "transparent"; }}>
                      <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <span style={{ minWidth: 24, height: 24, display: "inline-flex",
                          alignItems: "center", justifyContent: "center", fontSize: 13, fontWeight: 900,
                          fontFamily: "monospace",
                          color: isCurrent ? ACC : text,
                          background: isCurrent ? `${ACC}28` : border,
                          borderRadius: 6 }}>
                          {idx + 1}
                        </span>
                        <strong>{l.line_name}</strong>
                      </span>
                      {isCurrent && (
                        <span style={{ fontSize: 9, fontWeight: 800, color: ACC }}>
                          ● HERE
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
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

          {/* 2026-08-12 — jump to the MANAGEMENT view of this same line.  Mirror
              of the "Go to Supervisor" button on that screen, so the pair can be
              swapped from either side without editing the URL.  Sits after the
              MODEL pill so the identity of the line reads first and the action
              last.  Uses the line NAME (/Y17-SS/MANAGEMENT) to match the
              readable URL scheme. */}
          <button
            onClick={() => {
              const ref = String(summary?.kpi?.line_name || lineId || lineRef)
                            .trim().replace(/\s+/g, "-");
              window.location.assign(`/${encodeURIComponent(ref)}/MANAGEMENT`);
            }}
            title="Switch to the Management dashboard for this line"
            style={{ fontSize: 11, fontWeight: 800, padding: "3px 10px",
                     borderRadius: 99, cursor: "pointer",
                     background: "rgba(56,189,248,.10)",
                     border: "1px solid rgba(56,189,248,.45)",
                     color: "#38bdf8", whiteSpace: "nowrap",
                     display: "inline-flex", alignItems: "center", gap: 5 }}>
            <span style={{ fontSize: 9, opacity: .7, textTransform: "uppercase" }}>Go to</span>
            <strong>Management</strong>
          </button>
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
            {/* 2026-09-14 — manual HH:MM:SS: toLocaleTimeString wrapped the time in
                invisible directional marks that showed as stray white dashes on
                the panel's WebView. */}
            {`${String(now.getHours()).padStart(2,"0")}:${String(now.getMinutes()).padStart(2,"0")}:${String(now.getSeconds()).padStart(2,"0")}`}
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
      <style>{`@keyframes wbPulse { 0%,100% { opacity:1; } 50% { opacity:.3; } }
        @keyframes wbShimmer { 0% { transform: translateX(-100%); } 100% { transform: translateX(100%); } }
        @keyframes wbFadeIn { from { opacity:0; } to { opacity:1; } }
        @keyframes wbSlide { 0% { transform: translateX(-115%); } 100% { transform: translateX(265%); } }
        @keyframes wbBreathe { 0%,100% { opacity:.45; transform:scale(.97); } 50% { opacity:1; transform:scale(1.03); } }
      `}</style>

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
                          lossAll={lossBreakdown}
                          idealCt={m.ideal_ct || 15}
                          isMain={!!m.is_main}
                          chartReady={chartReady}
                          lineId={lineId}
                          okOverride={m.is_main ? summary?.kpi?.ok_count : undefined}
                          ngOverride={m.is_main ? summary?.kpi?.ng_count : undefined}
                          shiftName={shiftName}
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
                 // 2026-05-27 — Red frame for NG cycles (matches the
                 // Fullscreen modal styling).  Operator: "ng pe sab
                 // jagah red dikhna chahiye".  OK styling unchanged.
                 background: videoCycle.cy?.is_ng ? "#1a0a0d" : "#0a0f1a",
                 color: text,
                 border: videoCycle.cy?.is_ng
                   ? `3px solid #ef4444`
                   : `1px solid ${border}`,
                 borderRadius: 12,
                 padding: 14, maxWidth: 900, width: "90%",
                 maxHeight: "88vh",
                 display: "flex", flexDirection: "column",
                 boxShadow: videoCycle.cy?.is_ng
                   ? "0 24px 72px rgba(239,68,68,0.5)"
                   : "0 24px 72px rgba(0,0,0,0.6)",
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
            <video ref={vidRef} src={videoCycle.url}
                   muted
                   preload="auto"
                   /* 2026-08-10 — autoPlay, matching Fullscreen.  This modal used
                      to hold playback until VID_PLAY_AT of the clip was buffered;
                      Fullscreen never did, which is exactly why the same cycle
                      felt instant there and laggy here.  Operator: make both
                      start on the first bytes. */
                   autoPlay
                   controlsList="nodownload noremoteplayback"
                   data-retry="0"
                   data-started="0"
                   onLoadStart={(e) => _probeSrc(videoCycle.url, e.currentTarget)}
                   onLoadedMetadata={(e) => {
                     // 2026-05-27 — Playback rate stays 1.0 (real-time).
                     // Server-side encoder is now NVENC p1 (fastest)
                     // so the clip arrives 2-3× sooner; the perceived
                     // "slow" was render latency, not playback speed.
                   }}
                   onProgress={(e) => {
                     // 2026-07-28 — BUFFER-AHEAD before play.  Over the slow
                     // shop-floor LAN `autoPlay` started on the first few KB
                     // then stalled again and again (buffer→play→buffer =
                     // irritating stutter).  Operator spec: don't play until
                     // (Historic note: this used to hold playback until
                     // VID_PLAY_AT was buffered.  autoPlay replaced that.)
                     const v = e.currentTarget;
                     if (v.dataset.started === "1") return;
                     const dur = v.duration;
                     if (!dur || !isFinite(dur) || v.buffered.length === 0) return;
                     const bufEnd = v.buffered.end(v.buffered.length - 1);
                     // Only while the cover is up, and only on ≥2% moves:
                     // returning the SAME value makes React bail out of the
                     // re-render, so a downloading clip can't churn the whole
                     // wallboard (every chart) on each progress event.
                     if (vidLoading) {
                       const _nx = Math.min(1, bufEnd / dur);
                       setVidBuffered(p => (_nx > p + 0.01 || _nx >= VID_PLAY_AT ? _nx : p));
                     }
                     // No buffer gate any more — autoPlay above starts it.
                     // We still mark started so onWaiting can tell a genuine
                     // mid-clip re-buffer from the initial load.
                     if (!v.paused) v.dataset.started = "1";
                   }}
                   onCanPlayThrough={(e) => {
                     // Fallback: browser says it can play to the end without
                     // stalling (e.g. a cached clip that buffered instantly) —
                     // start now even if the buffer-gate tick was skipped.
                     // Safety net only: if the browser blocked autoPlay for
                     // any reason, kick it once we know it can play through.
                     const v = e.currentTarget;
                     if (v.dataset.started === "1") return;
                     v.dataset.started = "1";
                     v.play().catch(() => {});
                   }}
                   onPlaying={(e) => {
                     setVidLoading(false); setVidStall(false);
                     // `X-Clip-Source: archive` means it came off disk; anything
                     // else was transcoded on demand.  Recorded so a slow sample
                     // can be traced to a cause instead of guessed at.
                     if (vidTimerRef.current) {
                       // Elapsed time is taken now; only the source label waits
                       // for the probe (see api/timing.js).
                       vidTimerRef.current(vidSrcRef.current || {
                         source: e.currentTarget?.dataset?.clipSource || "render",
                       });
                       vidTimerRef.current = null;
                     }
                     vidWaitStart.current = 0;
                   }}
                   onWaiting={(e) => {
                     // Ran dry mid-clip.  The browser paints its round spinner
                     // here; flag it so our own strip covers that instead.
                     if (e.currentTarget.dataset.started === "1") setVidStall(true);
                   }}
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
                     setVidLoading(false);
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
            {/* always-visible progress + time (native strip fades out) */}
            <VideoProgressBar videoRef={vidRef} />
            {/* 2026-08-10 — Smooth "clip loading" cover.
                A cycle clip is rendered on demand server-side, so the first
                2-4 s show a black box with the browser's own round spinner —
                operator read that as a hang ("gol gol buffer acha nhi lg
                rha").  This covers it with a calm shimmer + film icon + a
                progress bar that goes determinate as soon as the media
                element reports buffered bytes.  pointerEvents:none so the
                ↺ button and tap-to-play underneath still work. */}
            {vidLoading && (
              <div style={{
                position:"absolute", inset:0, zIndex:5, borderRadius:6,
                display:"flex", flexDirection:"column",
                alignItems:"center", justifyContent:"center", gap:16,
                overflow:"hidden", pointerEvents:"none", lineHeight:1.4,
                background:"radial-gradient(ellipse at center, #161b23 0%, #05070a 100%)",
              }}>
                <div style={{ position:"absolute", inset:0, overflow:"hidden" }}>
                  <div style={{
                    position:"absolute", top:0, bottom:0, width:"55%",
                    background:"linear-gradient(90deg, transparent 0%, rgba(255,255,255,.055) 50%, transparent 100%)",
                    animation:"wbShimmer 1.7s ease-in-out infinite",
                  }}/>
                </div>
                <svg width="44" height="44" viewBox="0 0 24 24" fill="none"
                     stroke="#38bdf8" strokeWidth="1.5" strokeLinecap="round"
                     style={{ animation:"wbBreathe 1.9s ease-in-out infinite" }}>
                  <rect x="2" y="4" width="20" height="16" rx="2.5"/>
                  <path d="M7 4v16M17 4v16"/>
                  <path d="M2 9h5M2 15h5M17 9h5M17 15h5"/>
                </svg>
                <div style={{ fontFamily:"'Barlow',sans-serif", fontSize:12,
                              letterSpacing:1.6, textTransform:"uppercase",
                              fontWeight:600, color:"rgba(255,255,255,.7)" }}>
                  Loading cycle video · {vidWait.toFixed(1)}s{vidBuffered > 0 ? ` · ${Math.round(Math.min(1, vidBuffered / VID_PLAY_AT) * 100)}%` : ""}
                </div>
                <div style={{ width:"44%", maxWidth:300, height:4, borderRadius:99,
                              background:"rgba(255,255,255,.10)", overflow:"hidden" }}>
                  <div style={vidBuffered > 0
                    ? { height:"100%", width:`${Math.max(5, Math.min(1, vidBuffered / VID_PLAY_AT) * 100).toFixed(1)}%`,
                        borderRadius:99, background:"#38bdf8",
                        transition:"width .4s cubic-bezier(.4,0,.2,1)" }
                    : { height:"100%", width:"38%", borderRadius:99, background:"#38bdf8",
                        animation:"wbSlide 1.3s ease-in-out infinite" }}/>
                </div>
              </div>
            )}
            {/* 2026-08-10 — Mid-clip re-buffer.  Playback now starts at 20%
                so a slow moment can briefly run the buffer dry.  Keep the
                frame visible (translucent scrim, no icon) and just show a
                moving strip — enough to hide the browser's round spinner
                without the video "disappearing" mid-cycle.  Auto-clears on
                the next `playing` event; nothing to click. */}
            {vidStall && !vidLoading && (
              <div style={{
                position:"absolute", inset:0, zIndex:5, borderRadius:6,
                display:"flex", flexDirection:"column",
                alignItems:"center", justifyContent:"center", gap:12,
                overflow:"hidden", pointerEvents:"none",
                background:"rgba(5,7,10,.55)",
                animation:"wbFadeIn .25s ease-out both",
              }}>
                <div style={{ fontFamily:"'Barlow',sans-serif", fontSize:11,
                              letterSpacing:1.5, textTransform:"uppercase",
                              fontWeight:600, color:"rgba(255,255,255,.75)" }}>
                  Buffering
                </div>
                <div style={{ width:"32%", maxWidth:220, height:3, borderRadius:99,
                              background:"rgba(255,255,255,.12)", overflow:"hidden" }}>
                  <div style={{ height:"100%", width:"38%", borderRadius:99,
                                background:"#38bdf8",
                                animation:"wbSlide 1.1s ease-in-out infinite" }}/>
                </div>
              </div>
            )}
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
            {videoCycle.isMain && (
              <CycleCommentsPanel
                lineId={lineId}
                machineName="Final Inspection"
                /* 2026-06-05 — Key = cycle_seq + the cycle's DATE to MATCH
                   Fullscreen exactly AND stop a PAST day's cycle #N notes from
                   showing on today's cycle #N (cycle_seq resets daily).  Both
                   views build `cycle_<seq>_<YYYY-MM-DD>` from the same ts. */
                partCode={`cycle_${videoCycle.cy.cycle_seq}` + (videoCycle.cy.ts ? `_${new Date(videoCycle.cy.ts).toISOString().slice(0, 10)}` : "")}
                /* 2026-09-16 — the date alone is not enough: cycle_seq ALSO
                   restarts every shift, so A-shift #10 and B-shift #10 of the
                   SAME day collided.  Scope the thread to the cycle's shift. */
                shift={videoCycle.cy.shift_name || shiftName}
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
            {/* 2026-06-05 — MAIN machine ka per-machine remark (ng-process-remarks)
                hata diya: Final / YNC-SS ke liye ab sirf upar wala CycleCommentsPanel
                (cycles/comments, part_code-keyed) jo dono views me sync hota hai.
                Yeh box ab sirf SUB machines ke liye (unka apna alag remark system). */}
            {!videoCycle.isMain && (() => {
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

// ═════════════════════════════════════════════════════════════════
// CYCLE-COMMENT AUTOFILL  (2026-06-17)
// Operator spec: "sirf suggestion aaye aur TAB press ho toh fill ho
//   jaise keyboard mein autofill operate karta hai".
//
// PURE-FRONTEND, READ-ONLY.  No backend / collector / DB change — the
// box only *suggests*; the supervisor still presses TAB (or clicks a
// chip) to accept and SAVE/POST to persist, exactly as before.
//
// Suggestions are mined from the line's OWN comment history (the
// existing /comments-history endpoint) and ranked with two signals,
// grounded in the comment-history analysis:
//   • FREQUENCY   — most-typed canonical phrases (Bin Change, Waiting
//                   from previous process, Kanban put on bin, …).
//   • PERIODICITY — bin-capacity reasons recur every ~N cycles; if a
//                   reason is "due" for this cycle_seq it is boosted,
//                   and a reason that just ran on the previous cycle
//                   (sticky runs like "waiting") is boosted too.
// ═════════════════════════════════════════════════════════════════

// Normalise a comment to a grouping key (case/space/punct-insensitive).
function _normComment(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/[.,;:!。]+$/g, "")
    .trim();
}

// Parse "cycle_<seq>_<YYYY-MM-DD>" → { seq, date } (main-cycle part_code).
function _parseCyclePc(pc) {
  const m = /^cycle_(\d+)_(\d{4}-\d{2}-\d{2})$/.exec(String(pc || "").trim());
  return m ? { seq: parseInt(m[1], 10), date: m[2] } : null;
}

// Module-level history cache (shared by both panels) — 5-min TTL so a
// supervisor flicking through many cycles fetches the corpus once.
const _suggHistCache = {};   // `${lineId}|${source}` -> { ts, rows }

async function _fetchCommentHistory(lineId, source) {
  const key = `${lineId}|${source}`;
  const now = Date.now();
  const hit = _suggHistCache[key];
  if (hit && (now - hit.ts) < 5 * 60 * 1000) return hit.rows;
  const from = new Date(now - 120 * 864e5).toISOString().slice(0, 10);
  const to   = new Date(now).toISOString().slice(0, 10);
  const wantType = source === "ng_remark" ? "NG-Remark" : "Comment";
  try {
    const r = await fetch(
      `/api/lines/${lineId}/comments-history?date_from=${from}&date_to=${to}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    const rows = (Array.isArray(d.rows) ? d.rows : [])
      .filter(x => x.type === wantType && x.text && x.text.trim());
    _suggHistCache[key] = { ts: now, rows };
    return rows;
  } catch {
    return hit ? hit.rows : [];   // stale-on-error, else empty
  }
}

// median intra-date consecutive seq-gap = a reason's recurrence period.
function _medianPeriod(arr) {
  const byDate = new Map();
  for (const o of arr) {
    let g = byDate.get(o.date); if (!g) { g = []; byDate.set(o.date, g); }
    g.push(o.seq);
  }
  const gaps = [];
  for (const g of byDate.values()) {
    g.sort((a, b) => a - b);
    for (let i = 1; i < g.length; i++) { const d = g[i] - g[i - 1]; if (d > 0 && d <= 60) gaps.push(d); }
  }
  if (!gaps.length) return null;
  gaps.sort((a, b) => a - b);
  return gaps[Math.floor(gaps.length / 2)];
}

// Build a ranked suggestion list for the cycle described by `partCode`.
function _rankSuggestions(rows, partCode, machineId) {
  if (!rows || !rows.length) return [];
  const freq = new Map();   // key -> { count, forms: Map(orig->n) }
  const occ  = new Map();   // key -> [ {date, seq} ]  (for periodicity)
  for (const r of rows) {
    const key = _normComment(r.text);
    if (!key) continue;
    let e = freq.get(key);
    if (!e) { e = { count: 0, forms: new Map() }; freq.set(key, e); }
    const w = (machineId && r.machine_id && r.machine_id !== machineId) ? 0.4 : 1;
    e.count += w;
    const orig = r.text.trim();
    e.forms.set(orig, (e.forms.get(orig) || 0) + 1);
    const cyc = _parseCyclePc(r.part_code);
    if (cyc) { let a = occ.get(key); if (!a) { a = []; occ.set(key, a); } a.push(cyc); }
  }
  const cur = _parseCyclePc(partCode);
  // reason of the immediately-preceding commented cycle (same date) → run boost
  let prevKey = null, prevBest = -1;
  if (cur) {
    for (const [key, arr] of occ) {
      for (const o of arr) {
        if (o.date === cur.date && o.seq < cur.seq && o.seq > prevBest) { prevBest = o.seq; prevKey = key; }
      }
    }
  }
  const out = [];
  for (const [key, e] of freq) {
    let disp = key, dn = -1;
    for (const [orig, n] of e.forms) if (n > dn) { dn = n; disp = orig; }
    let score = Math.log(1 + e.count);          // frequency prior (diminishing)
    if (cur) {
      const arr = occ.get(key) || [];
      let lastSeq = -1;
      for (const o of arr) if (o.date === cur.date && o.seq < cur.seq && o.seq > lastSeq) lastSeq = o.seq;
      const P = _medianPeriod(arr);
      if (P && lastSeq >= 0) {
        const since = cur.seq - lastSeq;
        const phase = since % P;
        if (since >= P) score += 1.2;                                   // overdue
        if (phase === 0 || phase === P - 1 || (phase === 1 && P > 2)) score += 1.0;  // on-phase
      } else if (P && lastSeq < 0) {
        score += 0.3;                            // periodic reason not yet fired this shift
      }
      if (key === prevKey) score += 0.8;         // sticky run continuation
    }
    out.push({ text: disp, key, score, count: e.count });
  }
  out.sort((a, b) => b.score - a.score || b.count - a.count);
  const seen = new Set(), top = [];
  for (const o of out) { if (seen.has(o.key)) continue; seen.add(o.key); top.push(o.text); if (top.length >= 6) break; }
  return top;
}

// Hook: ranked suggestion strings for the given cycle (re-ranks per cycle).
function useCycleSuggest({ lineId, source, machineId, partCode }) {
  const [rows, setRows] = useState([]);
  useEffect(() => {
    let dead = false;
    _fetchCommentHistory(lineId, source).then(r => { if (!dead) setRows(r); });
    return () => { dead = true; };
  }, [lineId, source]);
  return useMemo(() => _rankSuggestions(rows, partCode, machineId),
                 [rows, partCode, machineId]);
}

// GhostTextarea — a <textarea> with inline ghost-text autocomplete.
// The top suggestion that prefix-matches the typed text is shown greyed
// behind the caret; TAB accepts it (just like shell / IDE autofill).  A
// backdrop "mirror" div renders the ghost aligned exactly under the real
// text so it lines up at any caret position.
function GhostTextarea({
  value, onChange, onKeyDown, suggestions = [],
  innerRef, style = {}, ghostColor = "rgba(255,255,255,0.34)",
  placeholder, ...rest
}) {
  const v = value == null ? "" : String(value);
  // 2026-09-19 — ghost text OFF entirely (operator: "ghost text box me visually
  // aa raha hai, hata do — only suggestion text chahiye, koi ghost comment
  // nahi").  A grey past remark inside the box ("Video not running" on YCA)
  // read as a comment that had appeared by itself.  Suggestions stay available
  // as the chips under the box (SuggestChips); nothing is drawn in the box.
  const ghost = "";
  const _UNUSED_SUGGESTIONS = suggestions;   // still taken out of ...rest so it never reaches the <textarea>
  const ghostSuffix = ghost ? (v.trim() === "" ? ghost : ghost.slice(v.length)) : "";
  const emptyGhost  = ghostSuffix && v.trim() === "";

  const accept = () => {
    if (!ghost) return;
    onChange && onChange({ target: { value: ghost } });
    const el = innerRef && innerRef.current;
    if (el) requestAnimationFrame(() => {
      try { el.selectionStart = el.selectionEnd = ghost.length; } catch {}
    });
  };

  const mirror = {
    position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
    padding: style.padding, border: "1px solid transparent",
    borderRadius: style.borderRadius,
    fontSize: style.fontSize, fontFamily: style.fontFamily,
    lineHeight: "1.45", whiteSpace: "pre-wrap", wordBreak: "break-word",
    overflow: "hidden", boxSizing: "border-box",
    background: style.background, color: "transparent", pointerEvents: "none",
  };
  return (
    <div style={{ position: "relative", flex: style.flex != null ? style.flex : 1,
                  display: "flex", minWidth: 0 }}>
      <div aria-hidden="true" style={mirror}>
        <span style={{ visibility: "hidden" }}>{v}</span>
        <span style={{ color: ghostColor }}>{ghostSuffix}</span>
      </div>
      <textarea
        ref={innerRef}
        value={value}
        onChange={onChange}
        placeholder={emptyGhost ? "" : placeholder}
        onKeyDown={(e) => {
          if (e.key === "Tab" && !e.shiftKey && ghostSuffix) { e.preventDefault(); accept(); return; }
          onKeyDown && onKeyDown(e);
        }}
        style={{ ...style, flex: 1, width: "100%", lineHeight: "1.45",
                 background: "transparent", position: "relative", zIndex: 1 }}
        {...rest}
      />
    </div>
  );
}

// SuggestChips — clickable top-3 suggestion pills shown under a compose
// box (mouse alternative to TAB; also makes "suggestion aaye" visible).
function SuggestChips({ suggestions = [], current = "", onPick, border, color }) {
  const list = (suggestions || []).filter(s => s && s.trim() !== (current || "").trim()).slice(0, 3);
  if (!list.length) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6, alignItems: "center" }}>
      <span style={{ fontSize: 10, color, opacity: 0.7, marginRight: 2 }}>⇥ Tab / pick:</span>
      {list.map((s, i) => (
        <button key={i} type="button" onClick={() => onPick && onPick(s)}
          title="Fill this comment"
          style={{
            fontSize: 11, padding: "3px 10px", borderRadius: 999,
            border: `1px solid ${border}`, background: "rgba(255,255,255,0.05)",
            color, cursor: "pointer", maxWidth: 260, whiteSpace: "nowrap",
            overflow: "hidden", textOverflow: "ellipsis", lineHeight: 1.3,
          }}>
          {s}
        </button>
      ))}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// WbCycleRemarkPanel — single remark box for the specific machine
// whose cycle is being viewed.  NG cycle = red box; OK cycle = blue.
// Operator: "hr process pe apna apna remarks option bs only one
// machine jiski cycle h vo".
// ─────────────────────────────────────────────────────────────────
// ── Voice dictation button for comment fields (2026-09-06) ──────────
// Uses the browser Web Speech API (webkitSpeechRecognition) so the operator can
// SPEAK a comment instead of typing it. Available in Chrome + the Android app
// WebView (verified). Renders nothing where speech recognition is unsupported.
// In the installed APK the WebView must GRANT the mic (MainActivity
// onPermissionRequest + RECORD_AUDIO in the manifest) — added there too.
// 2026-09-06 — LIVE dictation: while the mic is ON the text is written as you
// speak (interim results) and it keeps listening until you turn it off
// (continuous + auto-restart, since the Android WebView still auto-ends on a
// pause). Controlled: `value` = current draft, `onChange(newText)` sets it.
function MicDictateButton({ value = "", onChange, size = 34 }) {
  const [listening, setListening] = useState(false);
  const [err, setErr] = useState("");
  const recRef   = useRef(null);
  const baseRef  = useRef("");     // draft text captured when the mic turned on
  const finalRef = useRef("");     // transcript finalised so far this session
  const wantRef  = useRef(false);  // user wants it recording (survives auto-ends)
  const SR = (typeof window !== "undefined") &&
             (window.SpeechRecognition || window.webkitSpeechRecognition);
  if (!SR) return null; // unsupported → hide the button entirely

  // Full field value = pre-mic text + everything spoken this session (+ live interim).
  const compose = (interim) => {
    const base   = (baseRef.current || "").trim();
    const spoken = (finalRef.current + interim).replace(/\s+/g, " ").trim();
    if (!spoken) return baseRef.current || "";
    return base ? base + " " + spoken : spoken;
  };

  const stop = () => {
    wantRef.current = false;
    try { recRef.current && recRef.current.stop(); } catch {}
    setListening(false);
  };

  const start = () => {
    setErr("");
    // Web Speech API needs a SECURE context. http://<ip>:5656 (LAN / direct IP)
    // is NOT secure, so the browser blocks the mic with a bare "not-allowed" —
    // allowing the permission can't fix it. Tell the user the real reason.
    if (typeof window !== "undefined" && window.isSecureContext === false) {
      setErr("Mic needs HTTPS — open the app at mes.tbdi.in (or via localhost)");
      return;
    }
    let rec;
    try { rec = new SR(); } catch { return; }
    recRef.current = rec;
    rec.lang = "en-IN";
    rec.interimResults = true;     // ← write text live while speaking
    rec.continuous = true;         // ← keep listening until turned off
    rec.maxAlternatives = 1;
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) finalRef.current += r[0].transcript + " ";
        else interim += r[0].transcript;
      }
      if (typeof onChange === "function") onChange(compose(interim));
    };
    rec.onerror = (e) => {
      const code = e && e.error;
      if (code === "not-allowed" || code === "service-not-allowed") {
        wantRef.current = false;
        setErr("Mic blocked — allow microphone permission");
      } else if (code === "no-speech" || code === "aborted" || code === "network") {
        /* transient — onend will restart if the user still wants it on */
      } else {
        setErr("Voice input unavailable");
      }
    };
    rec.onend = () => {
      // Continuous recognition still auto-ends on a pause in the WebView; if the
      // user hasn't pressed stop, restart it so dictation keeps flowing.
      if (wantRef.current) {
        try { rec.start(); return; } catch {}
        try { start(); return; } catch {}
      }
      setListening(false);
    };
    try { rec.start(); setListening(true); }
    catch { wantRef.current = false; setListening(false); }
  };

  const toggle = () => {
    if (listening || wantRef.current) { stop(); return; }
    baseRef.current  = value || "";   // snapshot what's already typed
    finalRef.current = "";
    wantRef.current  = true;
    start();
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", flexShrink: 0 }}>
      <button type="button" onClick={toggle}
        title={listening ? "Stop recording" : "Speak your comment"}
        aria-label="Dictate comment by voice"
        style={{
          width: size, height: size, padding: 0, fontSize: 15, lineHeight: 1,
          background: listening ? "#dc2626" : "rgba(255,255,255,0.06)",
          color: "#fff",
          border: `1px solid ${listening ? "#dc2626" : "rgba(255,255,255,0.18)"}`,
          borderRadius: 6, cursor: "pointer", flexShrink: 0,
        }}>
        {listening ? "■" : "🎤"}
      </button>
      {err && (
        <div style={{ fontSize: 8.5, color: "#f87171", maxWidth: size + 30,
                      textAlign: "center", lineHeight: 1.1, marginTop: 2 }}>{err}</div>
      )}
    </div>
  );
}

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
  const taRef = useRef(null);

  // 2026-06-17 — autofill suggestions for THIS sub-machine's remarks
  // (mined from mes_ng_process_remarks history; frequency-ranked).
  const suggestions = useCycleSuggest({ lineId, source: "ng_remark", machineId, partCode });

  // 2026-06-17 — auto-focus the remark box the instant the video opens
  // (same supervisor-speed fix as the main comment box).
  useEffect(() => {
    const t = setTimeout(() => {
      try { taRef.current?.focus({ preventScroll: true }); } catch {}
    }, 90);
    return () => clearTimeout(t);
  }, [partCode]);

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
        <span>{isNg ? "⚠ Alarm" : "📝 OK"} CYCLE REMARK · {machineName}</span>
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
        <GhostTextarea
          innerRef={taRef}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          suggestions={suggestions}
          placeholder={isNg
            ? `Why did this part raise an Alarm at ${machineName}?  (e.g., "jig wear caused misalign", "operator missed torque")`
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
        <MicDictateButton value={draft} onChange={setDraft} />
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
      <SuggestChips suggestions={suggestions} current={draft}
        onPick={(s) => { setDraft(s); try { taRef.current?.focus(); } catch {} }}
        border={accentBd} color={textMut} />
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
function CycleCommentsPanel({ lineId, partCode, machineName, border, text, textSub, textMut, shift }) {
  const [items, setItems]     = useState([]);
  const [draft, setDraft]     = useState("");
  const [loading, setLoading] = useState(false);
  const [posting, setPosting] = useState(false);
  const [error, setError]     = useState("");
  const token = (typeof window !== "undefined"
                   && sessionStorage.getItem("mes_token")) || "";
  const taRef = useRef(null);

  // 2026-06-17 — autofill suggestions for this main-cycle comment box
  // (mined from mes_cycle_comments history; frequency + periodicity).
  const suggestions = useCycleSuggest({ lineId, source: "cycle", partCode });

  // 2026-06-17 — auto-focus the compose box the instant the video modal
  // opens (or a different cycle dot is clicked) so the supervisor can type
  // a comment immediately — no more hunting for + clicking the box each
  // time.  preventScroll keeps the video in view while the cursor lands.
  useEffect(() => {
    const t = setTimeout(() => {
      try { taRef.current?.focus({ preventScroll: true }); } catch {}
    }, 90);
    return () => clearTimeout(t);
  }, [partCode]);

  // Fetch on mount + when part_code changes (different cycle clicked)
  useEffect(() => {
    let stopped = false;
    setLoading(true);
    setError("");
    // Scope to THIS cycle's shift — cycle numbers restart every shift, so
    // A-shift #10 and B-shift #10 of the same day must not share notes.
    const _q = shift ? `?shift=${encodeURIComponent(shift)}` : "";
    fetch(`/api/lines/${lineId}/cycles/${encodeURIComponent(partCode)}/comments${_q}`,
          { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { if (!stopped) setItems(Array.isArray(d.comments) ? d.comments : []); })
      .catch(e => { if (!stopped) setError(String(e)); })
      .finally(() => { if (!stopped) setLoading(false); });
    return () => { stopped = true; };
  }, [lineId, partCode, shift, token]);

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
          // Stamp the CYCLE's own shift + date (date parsed from the part_code
          // `cycle_<seq>_<YYYY-MM-DD>`), not the wall clock.
          body: JSON.stringify({ comment: txt, machine_name: machineName,
                                 shift: shift || null,
                                 record_date: (String(partCode || "").match(/_(\d{4}-\d{2}-\d{2})$/) || [])[1] || null }),
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
        <GhostTextarea
          innerRef={taRef}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          suggestions={suggestions}
          placeholder="Add a comment about this cycle…"
          rows={2}
          maxLength={2000}
          ghostColor="rgba(255,255,255,0.28)"
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
        <MicDictateButton value={draft} onChange={setDraft} />
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
      <SuggestChips suggestions={suggestions} current={draft}
        onPick={(s) => { setDraft(s); try { taRef.current?.focus(); } catch {} }}
        border={border} color={textMut} />
      {error && (
        <div style={{ marginTop: 6, fontSize: 11, color: "#ef4444" }}>
          {error}
        </div>
      )}
    </div>
  );
}
