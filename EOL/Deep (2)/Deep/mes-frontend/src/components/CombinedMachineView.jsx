/**
 * CombinedMachineView.jsx — 2026-10-10
 * ────────────────────────────────────
 * Body of the sub-machine page for a COMBINED machine (mes_combined_machines):
 * YRA-SA-6WAY "Final Machine" = the two Mag Weld robots added up.
 *   • overview strip — shift target / actual / variance, and each robot's count
 *   • one cycle-time chart per robot (each against its own ideal CT)
 *   • click any cycle → both robots' clips side by side (the clicked cycle,
 *     and the other robot's cycle closest in time)
 *   • hourly table — combined target/actual plus a row per robot
 * Each robot keeps its own page; this only reads their data.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  LineChart, Line, ReferenceLine, LabelList,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import VideoProgressBar from "./VideoProgressBar";
import { api } from "../api/client";

const GREEN = "#22c55e", RED = "#ef4444", BLUE = "#3b82f6", AMBER = "#fbbf24", ORANGE = "#f97316";
const ROBOT_CLR = ["#3b82f6", "#a855f7", "#06b6d4", "#f59e0b"];
const WINDOW = 30;
// 2026-11-03 — robot CT runs ~23-27 s (ideal 32 s), so 0-40 s left the line
// pressed against the top.  Both robot graphs (management dashboard and the
// combined machine page) use 10-50 s; values outside are drawn on the edge.
const Y_MIN = 10, Y_CAP = 50;
const clampY = (v) => Math.max(Y_MIN, Math.min(v, Y_CAP));

function toChart(rows) {
  const mx = rows.length ? Math.max(...rows.map(r => Number(r.cycle_seq) || 0)) : 0;
  const perShift = mx > 0 && mx <= rows.length * 1.5;
  return rows.map((r, i) => ({
    no: perShift ? r.cycle_seq : i + 1,
    x:  perShift ? r.cycle_seq : i + 1,
    y:  Number(r.ct_seconds) || 0,
    yc: clampY(Number(r.ct_seconds) || 0),
    ts_start: r.ts_start, ts_end: r.ts_end,
    model_name: r.model_name, cycle_seq: r.cycle_seq,
  }));
}

function Dot({ cx, cy, payload, ideal, th, picked, onPick }) {
  if (typeof cx !== "number" || typeof cy !== "number") return null;
  const fill = (payload?.y ?? 0) > ideal ? RED : GREEN;
  const isPicked = picked && picked.cycle_seq === payload?.cycle_seq;
  return (
    <g style={{ cursor: "pointer" }}
       onClick={(e) => { e.stopPropagation(); onPick({ ...payload, _ts: Date.now() }); }}>
      <circle cx={cx} cy={cy} r={12} fill="transparent" />
      {isPicked && <circle cx={cx} cy={cy} r={11} fill="none" stroke={fill} strokeWidth={2} opacity={0.6} />}
      <circle cx={cx} cy={cy} r={isPicked ? 6 : 4} fill={fill}
              stroke={th.D ? "#060912" : "#fff"} strokeWidth={isPicked ? 2 : 1} />
    </g>
  );
}

function Tip({ active, payload, ideal, th }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div style={{ background: th.bgDeep, border: `1px solid ${th.border}`, padding: "8px 10px",
                  borderRadius: 6, fontSize: 11, color: th.text }}>
      <div style={{ fontWeight: 800, marginBottom: 4 }}>Cycle #{d.no}</div>
      <div>CT: <span style={{ color: d.y > ideal ? RED : GREEN, fontWeight: 800 }}>{d.y.toFixed(2)}s</span></div>
      <div style={{ color: th.textMut }}>{new Date(d.ts_end).toLocaleTimeString()}</div>
      <div style={{ color: th.textMut, fontSize: 10, marginTop: 4 }}>click for video</div>
    </div>
  );
}

// 2026-10-27 — on a narrow chart (the portrait panel puts the two robots side
// by side in ~430 px each) 30 cycles crowd the CT labels.  Below 600 px the
// window shrinks to ~28 px per cycle (min 10); wider charts keep 30.
function useFitWindow(ref) {
  const [n, setN] = useState(WINDOW);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const fit = () => {
      const w = el.clientWidth || 0;
      setN(w > 0 && w < 600 ? Math.max(10, Math.min(WINDOW, Math.floor(w / 28))) : WINDOW);
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return n;
}

// 2026-10-27 — operator: "CT graph ki theme same rakh, comment box full screen
// pe; dashboard same rahega, bas 2 machine".  Each robot is drawn with the
// Management CT graph's own Chart.js styling (green/red line against ideal,
// green dots, CT labels, dashed ideal, 10-50 s, slot dividers, click anywhere in
// a column) and a click hands the cycle to the dashboard's full-screen video +
// comments modal through onCycleVideo.  Alarm rows (ct 0) are not plotted.
const CLR_OK = "#22c55e", CLR_NG = "#ef4444";
const _toMin = (t) => { const [h, m] = String(t || "0:0").split(":").map(Number); return (h || 0) * 60 + (m || 0); };
const outY = (v) => v > Y_CAP || v < Y_MIN;
const robotSeries = (vis, idealCT, dark) => ({
  labels:    vis.map(c => c.cycle_seq),
  ct:        vis.map(c => clampY(c.ct_value)),
  ptBg:      vis.map(() => CLR_OK),
  ptBorder:  vis.map(c => outY(c.ct_value) ? "#ffffff" : (dark ? "#060912" : "#ffffff")),
  ptBorderW: vis.map(c => outY(c.ct_value) ? 2.5 : 1),
  ideal:     vis.map(() => idealCT),
});

function RobotCtChartJs({ title, color, rows, ideal, dark, slots, ready, onCycleVideo, th }) {
  const boxRef = useRef(null);
  const canvasRef = useRef(null);
  const instRef = useRef(null);
  const win = useFitWindow(boxRef);
  const [viewStart, setViewStart] = useState(null);
  const all = useMemo(() => (rows || [])
    .filter(r => Number(r.ct_seconds) > 0.1)
    .map(r => ({ cycle_seq: r.cycle_seq, ct_value: Math.round(Number(r.ct_seconds) * 100) / 100,
                 ts: r.ts_end, part_code: r.part_code, shift_name: r.shift_name }))
    .sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts)), [rows]);
  const maxStart = Math.max(0, all.length - win);
  const ws = viewStart !== null ? Math.min(viewStart, maxStart) : maxStart;
  const vis = useMemo(() => all.slice(ws, ws + win), [all, ws, win]);
  const visKey = vis.map(c => `${c.cycle_seq}:${c.ct_value}`).join(",");
  const cbRef = useRef(onCycleVideo);
  cbRef.current = onCycleVideo;
  // 2026-11-03 — the management dashboard re-renders every second (its clock)
  // and passes a new `slots` array each time.  With `slots` in the deps both
  // robot charts were destroyed and rebuilt every second, and under the LAN
  // panel zoom each new chart first drew at the zoomed size, so both graphs
  // blinked (panel 30.20, YRA-SA-6WAY).  Now the chart is rebuilt only when
  // the slot list, theme or ideal really change; new cycles update it in place.
  const visRef = useRef(vis);
  visRef.current = vis;
  const slotsRef = useRef(slots);
  slotsRef.current = slots;
  const slotsKey = (slots || []).map(s => `${s.slot_label}|${s.start_time}|${s.end_time}`).join(";");
  const idealCT = Number(ideal) || 15;
  const hasData = vis.length > 0;

  useEffect(() => {
    const Chart = window.Chart;
    const cv = canvasRef.current;
    if (!Chart || !cv || !visRef.current.length) return undefined;
    const D2 = dark;
    const hourlySlotPlugin = {
      id: "robotHourlySlots",
      afterDraw(chart) {
        const { ctx: c, scales } = chart;
        const xScale = scales.x, yScale = scales.y;
        const vis = visRef.current, slotList = slotsRef.current || [];
        let prevLabel = null, slotStartX = xScale.left, slotLabel = "";
        const put = (x, label, startX, divider) => {
          c.save();
          if (divider) {
            c.strokeStyle = D2 ? "rgba(255,255,255,0.18)" : "rgba(0,0,0,0.12)";
            c.lineWidth = 1.5; c.setLineDash([4, 3]);
            c.beginPath(); c.moveTo(x, yScale.top + 18); c.lineTo(x, yScale.bottom); c.stroke();
            c.setLineDash([]);
          }
          c.font = "800 9px 'Segoe UI',sans-serif";
          c.fillStyle = D2 ? "rgba(150,180,220,0.85)" : "rgba(40,70,130,0.75)";
          c.textAlign = "center";
          c.fillText(label, (startX + x) / 2, yScale.top + 11);
          c.restore();
        };
        vis.forEach((cy, i) => {
          if (!cy.ts) return;
          const x = xScale.getPixelForValue(i);
          const dt = new Date(cy.ts);
          const m = dt.getHours() * 60 + dt.getMinutes();
          const sl = slotList.find(s => m >= _toMin(s.start_time) && m < _toMin(s.end_time));
          const lbl = sl ? sl.slot_label : `${String(dt.getHours()).padStart(2, "0")}:00`;
          if (prevLabel === null) { prevLabel = lbl; slotLabel = lbl; }
          else if (lbl !== prevLabel) { put(x, slotLabel, slotStartX, true); slotStartX = x; slotLabel = lbl; prevLabel = lbl; }
        });
        if (prevLabel) put(xScale.right, slotLabel, slotStartX, false);
      },
    };
    const cycleLabelsPlugin = {
      id: "robotCycleLabels",
      afterDatasetsDraw(chart) {
        const { ctx: c } = chart;
        const vis = visRef.current;
        chart.getDatasetMeta(0).data.forEach((pt, i) => {
          const cy = vis[i];
          if (!cy) return;
          c.save();
          c.font = "500 8px 'Segoe UI',sans-serif";
          c.fillStyle = D2 ? "rgba(100,200,130,0.7)" : "rgba(20,120,60,0.65)";
          c.textAlign = "center"; c.textBaseline = "bottom";
          c.fillText(cy.ct_value + "s", pt.x, pt.y - 4);
          c.restore();
        });
      },
    };
    const seg = (s) => {
      const vis = visRef.current;
      const a = vis[s.p0DataIndex]?.ct_value, b = vis[s.p1DataIndex]?.ct_value;
      if (a == null || b == null) return "transparent";
      const aUp = a > idealCT, bUp = b > idealCT;
      if (aUp && bUp) return `${CLR_NG}cc`;
      if (!aUp && !bUp) return `${CLR_OK}cc`;
      const c2 = cv.getContext("2d");
      if (!c2 || s.p0.x === s.p1.x) return aUp ? `${CLR_NG}cc` : `${CLR_OK}cc`;
      const ratio = Math.max(0.01, Math.min(0.99, Math.abs(a - idealCT) / Math.abs(a - b)));
      const g = c2.createLinearGradient(s.p0.x, 0, s.p1.x, 0);
      const [c0, c1] = aUp ? [CLR_NG, CLR_OK] : [CLR_OK, CLR_NG];
      g.addColorStop(0, `${c0}cc`); g.addColorStop(Math.max(0, ratio - 0.01), `${c0}cc`);
      g.addColorStop(Math.min(1, ratio + 0.01), `${c1}cc`); g.addColorStop(1, `${c1}cc`);
      return g;
    };
    const pick = (evt, chart) => {
      let idx = -1;
      const els = chart.getElementsAtEventForMode(evt.native, "index", { intersect: false, axis: "x" }, false);
      const hit = els && els.find ? els.find(e => e.datasetIndex === 0) : null;
      if (hit) idx = hit.index;
      else if (evt.native && chart.scales?.x) {
        const rect = chart.canvas.getBoundingClientRect();
        const px = evt.native.clientX != null ? evt.native.clientX - rect.left : (evt.native.offsetX || 0);
        const v = chart.scales.x.getValueForPixel(px);
        if (v != null) idx = Math.round(v);
      }
      const vis = visRef.current;
      if (idx >= 0 && idx < vis.length && cbRef.current) cbRef.current(vis[idx]);
    };
    const sr = robotSeries(visRef.current, idealCT, D2);
    if (instRef.current) { instRef.current.destroy(); instRef.current = null; }
    instRef.current = new Chart(cv.getContext("2d"), {
      type: "line",
      data: {
        labels: sr.labels,
        datasets: [
          { label: "Cycle Time", data: sr.ct,
            borderWidth: 2, tension: 0, fill: false, spanGaps: false,
            pointBackgroundColor: sr.ptBg,
            pointBorderColor: sr.ptBorder,
            pointBorderWidth: sr.ptBorderW,
            pointRadius: 3, pointHoverRadius: 6, segment: { borderColor: seg } },
          { type: "line", label: `Ideal ${idealCT}s`, data: sr.ideal, stepped: "middle",
            borderColor: "rgba(251,191,36,0.7)", borderWidth: 1.5, borderDash: [5, 4],
            pointRadius: 0, fill: false, tension: 0 },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        layout: { padding: { top: 30, bottom: 2 } },
        interaction: { intersect: false, mode: "index", axis: "x" },
        onClick: (evt, _els, chart) => pick(evt, chart),
        onHover: (evt) => { if (evt?.native?.target) evt.native.target.style.cursor = "pointer"; },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: D2 ? "#0d1420" : "#fff",
            titleColor: D2 ? "#e8edf5" : "#0f172a",
            bodyColor: D2 ? "#8092af" : "#374151",
            borderColor: D2 ? "#141e2e" : "#b8c8dc",
            borderWidth: 1, padding: 10,
            callbacks: {
              title: items => `Cycle #${items[0].label}`,
              label: c => {
                if (c.datasetIndex === 1) return ` Ideal: ${idealCT}s`;
                const cy = visRef.current[c.dataIndex];
                if (!cy) return " —";
                const t = cy.ts ? new Date(cy.ts).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
                const clip = cy.ct_value > Y_CAP ? `  (>${Y_CAP}s clamped)` : cy.ct_value < Y_MIN ? `  (<${Y_MIN}s clamped)` : "";
                const out = [` CT: ${cy.ct_value}s${clip}  ✓ OK`];
                if (cy.part_code) out.push(` Part: ${cy.part_code}`);
                if (t) out.push(` Time: ${t}`);
                out.push(" 🎥 Click for video");
                return out;
              },
            },
          },
        },
        scales: {
          y: { beginAtZero: false, min: Y_MIN, max: Y_CAP,
               grid: { color: D2 ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.04)", drawBorder: false },
               border: { display: false },
               ticks: { color: D2 ? "#8092af" : "#374151", font: { size: 11 }, callback: v => `${Math.round(v)}s`, stepSize: 10 } },
          x: { grid: { display: false }, border: { display: false }, ticks: { display: false } },
        },
      },
      plugins: [hourlySlotPlugin, cycleLabelsPlugin],
    });
    // Same as the main CT graph (Fullscreen, 31-Oct): under CSS zoom a new
    // chart sizes itself from the zoomed rect and its ResizeObserver corrects
    // it ~100 ms later; give it the layout size straight away.
    try {
      const box = cv.parentElement;
      if (Math.abs(box.getBoundingClientRect().width - box.offsetWidth) > 0.5
          && box.clientWidth > 0 && box.clientHeight > 0) {
        instRef.current.resize(box.clientWidth, box.clientHeight);
      }
    } catch (_) {}
    return () => { if (instRef.current) { instRef.current.destroy(); instRef.current = null; } };
  }, [hasData, dark, idealCT, slotsKey, ready]);   // eslint-disable-line

  // New cycles: swap the data in place, no rebuild.
  useEffect(() => {
    const ch = instRef.current;
    if (!ch) return;
    const sr = robotSeries(visRef.current, idealCT, dark);
    const [d0, d1] = ch.data.datasets;
    ch.data.labels = sr.labels;
    d0.data = sr.ct; d0.pointBackgroundColor = sr.ptBg;
    d0.pointBorderColor = sr.ptBorder; d0.pointBorderWidth = sr.ptBorderW;
    d1.data = sr.ideal;
    ch.update("none");
  }, [visKey]);   // eslint-disable-line

  const last = all[all.length - 1];
  return (
    <div ref={boxRef} style={{ ...th.card, padding: "6px 8px 4px", height: "100%", display: "flex",
                               flexDirection: "column", minWidth: 0, minHeight: 0, overflow: "hidden",
                               boxSizing: "border-box" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6, marginBottom: 2 }}>
        <span style={{ fontSize: 10, fontWeight: 900, color, letterSpacing: ".1em", textTransform: "uppercase",
                       whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {title} · CT Graph · tap dot for video
        </span>
        <span style={{ fontSize: 10, color: th.textMut, fontFamily: "monospace", whiteSpace: "nowrap" }}>
          {all.length ? `${all.length} cycles · last #${last?.cycle_seq} · ideal ${Number(ideal) || 15}s` : ""}
        </span>
      </div>
      {/* 2026-10-27 — the canvas is absolute (as on the main CT graph): in flow
          it fed its own height back into the card and grew several times taller
          than the card, so only the 30-40 s band showed and the line looked
          like it jumped (operator photo, 13:57). */}
      <div style={{ flex: 1, position: "relative", minHeight: 0, overflow: "hidden" }}>
        {all.length === 0
          ? <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center",
                          color: th.textMut, fontSize: 13 }}>Waiting for cycle data…</div>
          : <canvas ref={canvasRef} style={{ position: "absolute", inset: 0, display: "block" }} />}
      </div>
      {all.length > win && (
        <div style={{ padding: "4px 4px 0" }}>
          <input type="range" min={0} max={maxStart} value={ws}
                 onChange={(e) => setViewStart(Number(e.target.value))}
                 style={{ width: "100%", cursor: "pointer", height: 3, display: "block", accentColor: CLR_OK }} />
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: th.textSub, marginTop: 2 }}>
            <span>#{all[0]?.cycle_seq}</span>
            <span style={{ color: th.textMut }}>
              {ws + 1}–{Math.min(ws + win, all.length)} of {all.length}
              {viewStart !== null && (
                <button onClick={() => setViewStart(null)}
                  style={{ marginLeft: 8, padding: "1px 6px", borderRadius: 4, border: `1px solid ${th.border}`,
                           background: "transparent", color: th.textSub, cursor: "pointer", fontSize: 9 }}>latest ▶</button>
              )}
            </span>
            <span>#{last?.cycle_seq}</span>
          </div>
        </div>
      )}
    </div>
  );
}

function RobotChart({ title, color, data, ideal, picked, onPick, th, height = 330 }) {
  const [viewStart, setViewStart] = useState(null);
  const boxRef = useRef(null);
  const win = useFitWindow(boxRef);
  const maxStart = Math.max(0, data.length - win);
  const ws = viewStart !== null ? Math.min(viewStart, maxStart) : maxStart;
  const view = data.slice(ws, ws + win);
  return (
    <div ref={boxRef} style={{ ...th.card, padding: 12, height, display: "flex", flexDirection: "column", minWidth: 0, boxSizing: "border-box" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <div style={{ fontSize: 11, fontWeight: 900, color, letterSpacing: ".1em", textTransform: "uppercase" }}>
          {title} · Cycle time
        </div>
        <div style={{ fontSize: 11, color: th.textMut, fontFamily: "monospace" }}>
          {data.length ? `${data.length} cycles · last #${data[data.length - 1]?.no}` : ""}
        </div>
      </div>
      {view.length === 0 ? (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
                      color: th.textMut, fontSize: 13 }}>Waiting for cycle data…</div>
      ) : (
        <div style={{ flex: 1, minHeight: 0 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={view} margin={{ top: 16, right: 16, bottom: 6, left: 0 }}
              onClick={(e) => { const p = e?.activePayload?.[0]?.payload; if (p) onPick({ ...p, _ts: Date.now() }); }}>
              <CartesianGrid stroke={th.D ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.06)"} strokeDasharray="2 4" />
              <XAxis dataKey="x" tick={{ fill: th.textSub, fontSize: 10 }} />
              <YAxis tick={{ fill: th.textSub, fontSize: 10 }} width={36} domain={[Y_MIN, Y_CAP]}
                     ticks={[10, 20, 30, 40, 50]} allowDataOverflow />
              <Tooltip content={<Tip ideal={ideal} th={th} />} />
              <ReferenceLine y={ideal} stroke={AMBER} strokeWidth={2} strokeDasharray="6 3" ifOverflow="extendDomain"
                             label={{ value: `Target ${ideal}s`, fill: AMBER, fontSize: 10, fontWeight: 800,
                                      position: "insideTopLeft", offset: 4 }} />
              <Line type="monotone" dataKey="yc" stroke={color} strokeWidth={2} isAnimationActive={false}
                    dot={(p) => <Dot key={`${p.index}-${p.payload?.cycle_seq}`} {...p} ideal={ideal} th={th}
                                     picked={picked} onPick={onPick} />}>
                <LabelList dataKey="y" content={({ x, y, value }) => (value == null || !isFinite(value)) ? null : (
                  <text x={x} y={(y || 0) - 8} textAnchor="middle" fill={Number(value) > ideal ? RED : GREEN}
                        style={{ fontSize: 9, fontWeight: 800 }}>{Number(value).toFixed(1)}s</text>
                )} />
              </Line>
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
      {data.length > win && (
        <div style={{ padding: "4px 4px 0" }}>
          <input type="range" min={0} max={maxStart} value={ws}
                 onChange={(e) => setViewStart(Number(e.target.value))}
                 style={{ width: "100%", cursor: "pointer", height: 3, display: "block", accentColor: GREEN }} />
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: th.textSub, marginTop: 2 }}>
            <span>#{data[0]?.no}</span>
            <span style={{ color: th.textMut }}>
              {ws + 1}–{Math.min(ws + win, data.length)} of {data.length}
              {viewStart !== null && (
                <button onClick={() => setViewStart(null)}
                  style={{ marginLeft: 8, padding: "1px 6px", borderRadius: 4, border: `1px solid ${th.border}`,
                           background: "transparent", color: th.textSub, cursor: "pointer", fontSize: 9 }}>latest ▶</button>
              )}
            </span>
            <span>#{data[data.length - 1]?.no}</span>
          </div>
        </div>
      )}
    </div>
  );
}

function RobotVideo({ title, color, memberId, cycle, ideal, token, stamp, th }) {
  const ref = useRef(null);
  const [err, setErr] = useState("");
  useEffect(() => { setErr(""); }, [memberId, cycle?.cycle_seq, stamp]);
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6 }}>
        <div style={{ fontSize: 12, fontWeight: 900, color }}>{title}</div>
        {cycle && (
          <div style={{ fontSize: 11, color: th.textSub }}>
            Cycle #{cycle.no} ·{" "}
            <span style={{ color: cycle.y > ideal ? RED : GREEN, fontWeight: 800 }}>{cycle.y.toFixed(2)}s</span>
            {" "}· {new Date(cycle.ts_end).toLocaleTimeString()}
          </div>
        )}
      </div>
      {!cycle || err ? (
        <div style={{ width: "100%", aspectRatio: "16/9", background: "#000", borderRadius: 6,
                      display: "flex", alignItems: "center", justifyContent: "center",
                      color: "#fca5a5", fontSize: 13, padding: 20, textAlign: "center", boxSizing: "border-box" }}>
          {!cycle ? "No cycle from this robot near that time." : err}
        </div>
      ) : (
        <>
          <video ref={ref} key={`${memberId}-${cycle.cycle_seq}-${stamp}`} autoPlay muted
                 style={{ width: "100%", aspectRatio: "16/9", background: "#000", borderRadius: 6, objectFit: "contain" }}
                 src={`/api/submachines/${memberId}/cycle-video?cycle_seq=${cycle.cycle_seq}&token=${encodeURIComponent(token)}&v=${stamp}`}
                 onError={(e) => {
                   const code = e?.target?.error?.code;
                   if (code === 4) setErr("Video not available for this cycle. The robot has no camera linked yet, or the clip is still being written.");
                   else if (code === 2) setErr("Network error — backend not reachable.");
                   else setErr("Video failed to load.");
                 }} />
          <VideoProgressBar videoRef={ref} />
        </>
      )}
    </div>
  );
}

// Polls every member's ct-history + hourly every 5 s → {memberId: {ct, hourly}}
function useMemberData(members, shift, token) {
  const [data, setData] = useState({});
  const membersKey = members.map(m => m.id).join(",");
  useEffect(() => {
    if (!members.length) return undefined;
    let alive = true;
    const load = async () => {
      const sq = shift ? `shift=${encodeURIComponent(shift)}` : "";
      const res = await Promise.all(members.map(m => Promise.all([
        api.get(`/api/submachines/${m.id}/ct-history?limit=5000${sq ? `&${sq}` : ""}`, token).catch(() => null),
        api.get(`/api/submachines/${m.id}/hourly${sq ? `?${sq}` : ""}`, token).catch(() => null),
      ])));
      if (!alive) return;
      setData(prev => {
        const o = { ...prev };
        res.forEach(([ct, h], i) => {
          const id = members[i].id;
          o[id] = { ct: Array.isArray(ct) ? ct : (o[id]?.ct || []), hourly: h || o[id]?.hourly };
        });
        return o;
      });
    };
    load();
    const t = setInterval(load, 5000);
    return () => { alive = false; clearInterval(t); };
  }, [membersKey, shift, token]);   // eslint-disable-line
  return data;
}

// clicked cycle on one robot + the other robots' cycles closest in time

/** Management-dashboard chart card body for a line whose final is a combined
 *  machine: one CT chart per robot side by side.  A click on a cycle plays
 *  THAT robot's clip only (27-Oct, operator), on its own side of the card. */
export function CombinedRobotCharts({ members, shift, token, th, onCycleVideo, dark, slots, chartReady }) {
  const data = useMemberData(members, shift, token);
  const [pick, setPick] = useState(null);
  useEffect(() => { setPick(null); }, [shift]);
  const charts = useMemo(() => members.map(m => toChart(data[m.id]?.ct || [])), [members, data]);
  if (onCycleVideo) {
    // Management dashboard: same CT-graph look + its own full-screen video modal
    return (
      <div style={{ position: "relative", width: "100%", height: "100%", display: "grid",
                    gridTemplateColumns: `repeat(${members.length}, minmax(0, 1fr))`,
                    gridTemplateRows: "minmax(0, 1fr)", gap: 8 }}>
        {members.map((m, i) => (
          <RobotCtChartJs key={m.id} title={`Robot ${i + 1}`} color={ROBOT_CLR[i % ROBOT_CLR.length]}
                          rows={data[m.id]?.ct || []} ideal={Number(m.ideal_ct || 0)} dark={dark}
                          slots={slots} ready={chartReady} th={th}
                          onCycleVideo={(cy) => onCycleVideo(m, i, cy)} />
        ))}
      </div>
    );
  }
  return (
    <div style={{ position: "relative", width: "100%", height: "100%", display: "grid",
                  gridTemplateColumns: `repeat(${members.length}, minmax(0, 1fr))`, gap: 8 }}>
      {members.map((m, i) => (
        <RobotChart key={m.id} title={`Robot ${i + 1}`} color={ROBOT_CLR[i % ROBOT_CLR.length]}
                    data={charts[i]} ideal={Number(m.ideal_ct || 0)} th={th} height="100%"
                    picked={pick && pick.idx === i ? pick.point : null}
                    onPick={(p) => setPick({ idx: i, point: p, _ts: Date.now() })} />
      ))}
      {pick && (
        // covers only the clicked robot's chart, so the other robot stays
        // visible and a click there switches the video to that robot
        <div style={{ position: "absolute", top: 0, bottom: 0, zIndex: 5, ...th.card, padding: 10,
                      left: `calc(${pick.idx} * (100% + 8px) / ${members.length})`,
                      width: `calc((100% - ${(members.length - 1) * 8}px) / ${members.length})`,
                      display: "flex", flexDirection: "column", boxSizing: "border-box" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
            <div style={{ fontSize: 11, fontWeight: 900, color: th.textSub, letterSpacing: ".1em", textTransform: "uppercase" }}>
              Cycle video · Robot {pick.idx + 1}
            </div>
            <button onClick={() => setPick(null)} title="Close"
              style={{ padding: "3px 8px", borderRadius: 6, border: `1px solid ${th.border}`,
                       background: th.bgDeep, color: th.text, cursor: "pointer", fontSize: 12 }}>✕</button>
          </div>
          <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
            {members.map((m, i) => i !== pick.idx ? null : (
              <RobotVideo key={m.id} title={`Robot ${i + 1}`} color={ROBOT_CLR[i % ROBOT_CLR.length]}
                          memberId={m.id} cycle={pick.point} ideal={Number(m.ideal_ct || 0)}
                          token={token} stamp={pick._ts} th={th} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function CombinedMachineView({ members, hourly, shift, ideal, token, th }) {
  const data = useMemberData(members, shift, token);
  const [pick, setPick] = useState(null);         // {idx, point, _ts}
  useEffect(() => { setPick(null); }, [shift]);
  const charts = useMemo(() => members.map(m => toChart(data[m.id]?.ct || [])), [members, data]);

  const shiftBuckets = (hourly?.buckets || []).filter(b => !shift || b.shift_name === shift);
  const memberBuckets = members.map(m =>
    (data[m.id]?.hourly?.buckets || []).filter(b => !shift || b.shift_name === shift));
  const sum = (arr, k) => arr.reduce((a, b) => a + (b[k] || 0), 0);
  const target = sum(shiftBuckets, "target");
  const actual = sum(shiftBuckets, "actual");
  const variance = actual - target;
  const robotLabel = (i) => `Robot ${i + 1}`;
  const robotStats = members.map((m, i) => {
    const ys = charts[i].map(c => c.y).filter(y => y > 0);
    return { count: sum(memberBuckets[i], "actual"),
             avg: ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : 0 };
  });

  const kpi = (label, value, color, sub) => (
    <div style={{ ...th.card, padding: "10px 14px", minWidth: 0 }}>
      <div style={{ fontSize: 10, fontWeight: 800, color: th.textSub, letterSpacing: ".1em", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontFamily: "monospace", fontSize: 26, fontWeight: 900, color, lineHeight: 1.2 }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: th.textMut }}>{sub}</div>}
    </div>
  );
  const cell = { padding: "6px 4px", textAlign: "center", borderRight: `1px solid ${th.border}` };

  return (
    <>
      {/* Overview strip */}
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${4 + members.length}, minmax(0, 1fr))`,
                    gap: 10, marginBottom: 10 }}>
        {kpi("Shift target", target, BLUE, shift ? `${shift} shift · both robots` : "both robots")}
        {kpi("Actual", actual, actual > 0 ? GREEN : th.textMut, "both robots added")}
        {kpi("Variance", `${variance > 0 ? "+" : ""}${variance}`, variance >= 0 ? GREEN : RED)}
        {kpi("Achievement", target > 0 ? `${Math.round((actual / target) * 100)}%` : "—",
             target > 0 && actual >= target ? GREEN : ORANGE)}
        {members.map((m, i) => kpi(robotLabel(i), robotStats[i].count, ROBOT_CLR[i % ROBOT_CLR.length],
             `${m.plc_ip || ""}${robotStats[i].avg ? ` · avg CT ${robotStats[i].avg.toFixed(1)}s` : ""}`))}
      </div>

      {/* One chart per robot */}
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${members.length}, minmax(0, 1fr))`,
                    gap: 10, marginBottom: 10 }}>
        {members.map((m, i) => (
          <RobotChart key={m.id} title={robotLabel(i)} color={ROBOT_CLR[i % ROBOT_CLR.length]}
                      data={charts[i]} ideal={Number(m.ideal_ct || ideal || 0)} th={th}
                      picked={pick && pick.idx === i ? pick.point : null}
                      onPick={(p) => setPick({ idx: i, point: p, _ts: Date.now() })} />
        ))}
      </div>

      {/* The clicked robot's clip (only that robot, 27-Oct) */}
      {pick && (
        <div style={{ ...th.card, padding: 12, marginBottom: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <div style={{ fontSize: 11, fontWeight: 900, color: th.textSub, letterSpacing: ".1em", textTransform: "uppercase" }}>
              Cycle video · Robot {pick.idx + 1}
            </div>
            <button onClick={() => setPick(null)} title="Close"
              style={{ padding: "3px 8px", borderRadius: 6, border: `1px solid ${th.border}`,
                       background: th.bgDeep, color: th.text, cursor: "pointer", fontSize: 12 }}>✕</button>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: `repeat(${members.length}, minmax(0, 1fr))`, gap: 12 }}>
            {members.map((m, i) => i !== pick.idx ? null : (
              <div key={m.id} style={{ gridColumn: i + 1, minWidth: 0 }}>
                <RobotVideo title={robotLabel(i)} color={ROBOT_CLR[i % ROBOT_CLR.length]}
                            memberId={m.id} cycle={pick.point} ideal={Number(m.ideal_ct || ideal || 0)}
                            token={token} stamp={pick._ts} th={th} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Hourly — combined plus each robot */}
      <div style={{ ...th.card, overflow: "hidden" }}>
        <div style={{ padding: "8px 12px", borderBottom: `1px solid ${th.border}`,
                      display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ fontSize: 11, fontWeight: 900, color: th.textSub, letterSpacing: ".1em", textTransform: "uppercase" }}>
            Hourly target vs actual · both robots
          </div>
          <div style={{ fontSize: 10, color: th.textMut }}>
            target = sum of each robot's target · shift plan {hourly?.shift_plan || 0}
          </div>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed" }}>
            <thead>
              <tr style={{ background: th.bgDeep }}>
                <th style={{ ...cell, textAlign: "left", padding: "6px 8px", fontSize: 11, fontWeight: 900, color: th.textSub, width: 85 }}>METRIC</th>
                {shiftBuckets.map(s => (
                  <th key={s.slot_label} style={{ ...cell, fontSize: 11, fontWeight: 800, color: th.text }}>{s.slot_label}</th>
                ))}
                <th style={{ ...cell, borderRight: "none", fontSize: 11, fontWeight: 900, color: ORANGE, width: 85 }}>TOTAL</th>
              </tr>
            </thead>
            <tbody>
              <tr style={{ borderTop: `1px solid ${th.border}` }}>
                <td style={{ ...cell, textAlign: "left", padding: "8px", fontSize: 11, fontWeight: 800, color: th.textMut }}>TARGET</td>
                {shiftBuckets.map((s, i) => (
                  <td key={i} style={{ ...cell, fontFamily: "monospace", fontSize: 16, fontWeight: 800,
                                       color: s.target > 0 ? BLUE : th.textMut }}>{s.target}</td>
                ))}
                <td style={{ ...cell, borderRight: "none", fontFamily: "monospace", fontSize: 18, fontWeight: 900, color: ORANGE }}>{target}</td>
              </tr>
              <tr style={{ borderTop: `2px solid ${th.border}`, background: th.bgDeep }}>
                <td style={{ ...cell, textAlign: "left", padding: "5px 8px", fontSize: 11, fontWeight: 800, color: th.textSub }}>ACTUAL</td>
                {shiftBuckets.map((s, i) => {
                  const v = (s.actual || 0) - (s.target || 0);
                  return (
                    <td key={i} style={cell}>
                      <div style={{ fontFamily: "monospace", fontSize: 16, fontWeight: 800, color: s.actual > 0 ? GREEN : th.textMut }}>{s.actual}</div>
                      <div style={{ fontFamily: "monospace", fontSize: 10, fontWeight: 700,
                                    color: v > 0 ? GREEN : v < 0 ? RED : th.textMut }}>({v > 0 ? "+" : ""}{v})</div>
                    </td>
                  );
                })}
                <td style={{ ...cell, borderRight: "none" }}>
                  <div style={{ fontFamily: "monospace", fontSize: 18, fontWeight: 900, color: actual > 0 ? GREEN : th.textMut }}>{actual}</div>
                  <div style={{ fontFamily: "monospace", fontSize: 11, fontWeight: 800,
                                color: variance > 0 ? GREEN : variance < 0 ? RED : th.textMut }}>({variance > 0 ? "+" : ""}{variance})</div>
                </td>
              </tr>
              {members.map((m, mi) => {
                const bySlot = Object.fromEntries(memberBuckets[mi].map(b => [b.slot_label, b.actual || 0]));
                return (
                  <tr key={m.id} style={{ borderTop: `1px solid ${th.border}` }}>
                    <td style={{ ...cell, textAlign: "left", padding: "5px 8px", fontSize: 11, fontWeight: 800,
                                 color: ROBOT_CLR[mi % ROBOT_CLR.length] }}>{robotLabel(mi).toUpperCase()}</td>
                    {shiftBuckets.map((s, i) => (
                      <td key={i} style={{ ...cell, fontFamily: "monospace", fontSize: 14, fontWeight: 700,
                                           color: bySlot[s.slot_label] ? th.text : th.textMut }}>{bySlot[s.slot_label] ?? 0}</td>
                    ))}
                    <td style={{ ...cell, borderRight: "none", fontFamily: "monospace", fontSize: 15, fontWeight: 800,
                                 color: ROBOT_CLR[mi % ROBOT_CLR.length] }}>{robotStats[mi].count}</td>
                  </tr>
                );
              })}
              {shiftBuckets.length === 0 && (
                <tr><td colSpan={3} style={{ padding: 20, textAlign: "center", color: th.textMut, fontSize: 12 }}>
                  {shift ? "No hourly data yet" : "Loading main-line slots…"}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
