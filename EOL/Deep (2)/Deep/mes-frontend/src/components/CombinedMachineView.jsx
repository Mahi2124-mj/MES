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

function toChart(rows) {
  const mx = rows.length ? Math.max(...rows.map(r => Number(r.cycle_seq) || 0)) : 0;
  const perShift = mx > 0 && mx <= rows.length * 1.5;
  return rows.map((r, i) => ({
    no: perShift ? r.cycle_seq : i + 1,
    x:  perShift ? r.cycle_seq : i + 1,
    y:  Number(r.ct_seconds) || 0,
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

function RobotChart({ title, color, data, ideal, picked, onPick, th, height = 330 }) {
  const [viewStart, setViewStart] = useState(null);
  const maxStart = Math.max(0, data.length - WINDOW);
  const ws = viewStart !== null ? Math.min(viewStart, maxStart) : maxStart;
  const view = data.slice(ws, ws + WINDOW);
  return (
    <div style={{ ...th.card, padding: 12, height, display: "flex", flexDirection: "column", minWidth: 0, boxSizing: "border-box" }}>
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
              <YAxis tick={{ fill: th.textSub, fontSize: 10 }} width={36} />
              <Tooltip content={<Tip ideal={ideal} th={th} />} />
              <ReferenceLine y={ideal} stroke={AMBER} strokeWidth={2} strokeDasharray="6 3" ifOverflow="extendDomain"
                             label={{ value: `Target ${ideal}s`, fill: AMBER, fontSize: 10, fontWeight: 800,
                                      position: "insideTopLeft", offset: 4 }} />
              <Line type="monotone" dataKey="y" stroke={color} strokeWidth={2} isAnimationActive={false}
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
      {data.length > WINDOW && (
        <div style={{ padding: "4px 4px 0" }}>
          <input type="range" min={0} max={maxStart} value={ws}
                 onChange={(e) => setViewStart(Number(e.target.value))}
                 style={{ width: "100%", cursor: "pointer", height: 3, display: "block", accentColor: GREEN }} />
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: th.textSub, marginTop: 2 }}>
            <span>#{data[0]?.no}</span>
            <span style={{ color: th.textMut }}>
              {ws + 1}–{Math.min(ws + WINDOW, data.length)} of {data.length}
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
function usePairs(pick, charts, members) {
  return useMemo(() => {
    if (!pick) return null;
    const t0 = new Date(pick.point.ts_end).getTime();
    return members.map((m, i) => {
      if (i === pick.idx) return pick.point;
      let best = null, bestD = Infinity;
      for (const c of charts[i]) {
        const d = Math.abs(new Date(c.ts_end).getTime() - t0);
        if (d < bestD) { best = c; bestD = d; }
      }
      return best && bestD <= 10 * 60 * 1000 ? best : null;
    });
  }, [pick, charts, members]);
}

/** Management-dashboard chart card body for a line whose final is a combined
 *  machine: one CT chart per robot side by side, and a click on any cycle
 *  shows both robots' clips together over the charts. */
export function CombinedRobotCharts({ members, shift, token, th }) {
  const data = useMemberData(members, shift, token);
  const [pick, setPick] = useState(null);
  useEffect(() => { setPick(null); }, [shift]);
  const charts = useMemo(() => members.map(m => toChart(data[m.id]?.ct || [])), [members, data]);
  const pairs = usePairs(pick, charts, members);
  return (
    <div style={{ position: "relative", width: "100%", height: "100%", display: "grid",
                  gridTemplateColumns: `repeat(${members.length}, minmax(0, 1fr))`, gap: 8 }}>
      {members.map((m, i) => (
        <RobotChart key={m.id} title={`Robot ${i + 1}`} color={ROBOT_CLR[i % ROBOT_CLR.length]}
                    data={charts[i]} ideal={Number(m.ideal_ct || 0)} th={th} height="100%"
                    picked={pick && pick.idx === i ? pick.point : (pairs ? pairs[i] : null)}
                    onPick={(p) => setPick({ idx: i, point: p, _ts: Date.now() })} />
      ))}
      {pairs && (
        <div style={{ position: "absolute", inset: 0, zIndex: 5, ...th.card, padding: 10,
                      display: "flex", flexDirection: "column", boxSizing: "border-box" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
            <div style={{ fontSize: 11, fontWeight: 900, color: th.textSub, letterSpacing: ".1em", textTransform: "uppercase" }}>
              Cycle video · both robots
            </div>
            <button onClick={() => setPick(null)} title="Close"
              style={{ padding: "3px 8px", borderRadius: 6, border: `1px solid ${th.border}`,
                       background: th.bgDeep, color: th.text, cursor: "pointer", fontSize: 12 }}>✕</button>
          </div>
          <div style={{ flex: 1, minHeight: 0, overflow: "auto", display: "grid",
                        gridTemplateColumns: `repeat(${members.length}, minmax(0, 1fr))`, gap: 10 }}>
            {members.map((m, i) => (
              <RobotVideo key={m.id} title={`Robot ${i + 1}`} color={ROBOT_CLR[i % ROBOT_CLR.length]}
                          memberId={m.id} cycle={pairs[i]} ideal={Number(m.ideal_ct || 0)}
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
  const pairs = usePairs(pick, charts, members);

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
                      picked={pick && pick.idx === i ? pick.point : (pairs ? pairs[i] : null)}
                      onPick={(p) => setPick({ idx: i, point: p, _ts: Date.now() })} />
        ))}
      </div>

      {/* Both robots' clips side by side */}
      {pairs && (
        <div style={{ ...th.card, padding: 12, marginBottom: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <div style={{ fontSize: 11, fontWeight: 900, color: th.textSub, letterSpacing: ".1em", textTransform: "uppercase" }}>
              Cycle video · both robots
            </div>
            <button onClick={() => setPick(null)} title="Close"
              style={{ padding: "3px 8px", borderRadius: 6, border: `1px solid ${th.border}`,
                       background: th.bgDeep, color: th.text, cursor: "pointer", fontSize: 12 }}>✕</button>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: `repeat(${members.length}, minmax(0, 1fr))`, gap: 12 }}>
            {members.map((m, i) => (
              <RobotVideo key={m.id} title={robotLabel(i)} color={ROBOT_CLR[i % ROBOT_CLR.length]}
                          memberId={m.id} cycle={pairs[i]} ideal={Number(m.ideal_ct || ideal || 0)}
                          token={token} stamp={pick._ts} th={th} />
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
