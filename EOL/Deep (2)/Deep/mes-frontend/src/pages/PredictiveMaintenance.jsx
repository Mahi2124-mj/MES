// pages/PredictiveMaintenance.jsx — Maintenance → Predictive Maintenance (2026-11-12)
//
// Operator: "maine ek aur dashboard banaya tha, usko MES ke format ke hisaab se
// merge karna hai; Maintenance me ek tab Predictive Maintenance ke naam se".
// The Predictive Module (port 1006, service predictive-module) stays the one
// reader of the PPI analog card (Press-18 bearing temperature + vibration) and
// keeps history, alarms, baseline and forecast; this page shows it in MES,
// through /api/predictive/* (MES login, works on mes.tbdi.in too).  Same parts
// as the module's own page: tiles, signal path, channel cards, trend & forecast
// (chart / table, CSV, set baseline), events.  Settings stay on the module page.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import PageTopbar from "../components/PageTopbar";

const STATE_MS = 3000;
const RANGES = ["15m", "1h", "8h", "24h", "7d", "30d"];
const HISTORY_REFRESH = { "15m": 3000, "1h": 5000, "8h": 20000, "24h": 30000, "7d": 60000, "30d": 120000 };
const DAY = 86400000, MIN = 60000;
const C = {
  ink: "#0f172a", sub: "#475569", muted: "#64748b", faint: "#94a3b8", line: "#e2e8f0", bg: "#f8fafc",
  good: "#16a34a", warning: "#d97706", critical: "#dc2626", series: "#1e40af", wash: "rgba(30,64,175,.13)",
  grid: "#e2e8f0", axis: "#94a3b8", band: "rgba(148,163,184,.22)",
};
const STATUS = {
  normal: ["Normal", C.good, "#f0fdf4"], warning: ["Warning", C.warning, "#fffbeb"],
  alarm: ["Alarm", C.critical, "#fef2f2"], fault: ["Sensor fault", C.critical, "#fef2f2"],
  offline: ["No data", C.muted, "#f1f5f9"],
};
const EVENT_LABEL = {
  alarm: ["Alarm", C.critical], warning: ["Warning", C.warning], normal: ["Cleared", C.good],
  fault: ["Sensor fault", C.critical], "fault-cleared": ["Signal OK", C.good],
  "comm-lost": ["Link down", C.critical], "comm-ok": ["Link up", C.good], config: ["Settings", C.series],
};

// ── formatting (same rules as the module) ──────────────────────────────
const fmt = (v, span = null) => {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const a = Math.abs(v), ref = span ?? a;
  const d = ref >= 200 ? 0 : ref >= 20 ? 1 : 2;
  return v.toLocaleString("en-IN", { minimumFractionDigits: a >= 1000 ? 0 : d, maximumFractionDigits: a >= 1000 ? 0 : d });
};
const fmtSigned = (v, span) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${fmt(Math.abs(v), span)}`);
const fmtDays = (d) => (d === null || d === undefined ? "—" : d <= 0 ? "now" : d < 1 ? `${Math.max(1, Math.round(d * 24))} h`
  : `${d < 10 ? d.toFixed(1) : Math.round(d)} days`);
const pad = (n) => String(n).padStart(2, "0");
const fmtTime = (t, sec = false) => { const d = new Date(t); return `${pad(d.getHours())}:${pad(d.getMinutes())}${sec ? `:${pad(d.getSeconds())}` : ""}`; };
const fmtDate = (t) => new Date(t).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
const fmtDateTime = (t) => (new Date(t).toDateString() === new Date().toDateString() ? fmtTime(t, true) : `${fmtDate(t)} ${fmtTime(t)}`);
const ago = (t, now = Date.now()) => {
  if (!t) return "never";
  const s = Math.max(0, Math.round((now - t) / 1000));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
};
const toneOf = (v) => (v === null || v === undefined ? C.muted : v >= 70 ? C.good : v >= 40 ? C.warning : C.critical);

// ── small building blocks ──────────────────────────────────────────────
const card = { background: "#fff", border: `1px solid ${C.line}`, borderRadius: 14, boxShadow: "0 1px 3px rgba(0,0,0,.05)" };
const btn = { border: `1px solid ${C.line}`, background: "#fff", color: C.sub, borderRadius: 8, padding: "6px 12px",
              fontSize: 13, fontWeight: 700, cursor: "pointer", fontFamily: "inherit", whiteSpace: "nowrap" };
function Section({ title, sub, children, innerRef }) {
  return (
    <section ref={innerRef} style={{ marginTop: 22, scrollMarginTop: 80 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 10 }}>
        <h3 style={{ margin: 0, fontSize: 15, fontWeight: 800, color: C.ink, textTransform: "uppercase", letterSpacing: ".04em" }}>{title}</h3>
        {sub && <span style={{ fontSize: 12, color: C.muted }}>{sub}</span>}
        <span style={{ flex: 1, height: 1, background: C.line }} />
      </div>
      {children}
    </section>
  );
}
function Dot({ color, pulse }) {
  return <span style={{ display: "inline-block", width: 9, height: 9, borderRadius: 999, background: color, flex: "none",
                        boxShadow: pulse ? `0 0 0 3px ${color}33` : "none" }} />;
}
function Tile({ label, value, sub, tone }) {
  return (
    <div style={{ ...card, padding: "12px 16px", minWidth: 0 }}>
      <div style={{ fontSize: 11, fontWeight: 800, color: C.muted, textTransform: "uppercase", letterSpacing: ".05em" }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 800, color: tone || C.ink, fontFamily: "'Barlow Condensed',sans-serif", lineHeight: 1.2 }}>{value}</div>
      <div title={typeof sub === "string" ? sub : undefined}
           style={{ fontSize: 12, color: C.muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sub}</div>
    </div>
  );
}
function StatusPill({ status }) {
  const [label, color, bg] = STATUS[status] || STATUS.offline;
  return <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 800, color,
                        background: bg, border: `1px solid ${color}55`, borderRadius: 999, padding: "3px 10px",
                        whiteSpace: "nowrap" }}><Dot color={color} />{label}</span>;
}
function Stat({ k, v, title }) {
  return (
    <div title={title || undefined} style={{ minWidth: 0 }}>
      <div style={{ fontSize: 11, color: C.muted, fontWeight: 700 }}>{k}</div>
      <div style={{ fontSize: 14, color: C.ink, fontWeight: 700 }}>{v}</div>
    </div>
  );
}

// ── signal path ────────────────────────────────────────────────────────
const NODE = { ok: ["OK", C.good], fail: ["Fault", C.critical], unknown: ["Not checked", C.faint],
               idle: ["Not set", C.faint], sim: ["Simulated", C.series] };
function Node({ name, st, sub, children }) {
  const [label, color] = NODE[st] || [st, C.faint];
  return (
    <div style={{ border: `1.5px solid ${st === "fail" ? C.critical : st === "ok" ? "#bbf7d0" : C.line}`,
                  background: st === "fail" ? "#fef2f2" : "#fff", borderRadius: 12, padding: "10px 12px",
                  minWidth: 170, flex: "1 1 170px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
        <span style={{ fontWeight: 800, fontSize: 14, color: C.ink }}>{name}</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, fontWeight: 700, color }}>
          <Dot color={color} />{label}</span>
      </div>
      <div style={{ fontSize: 12, color: C.muted, marginTop: 2 }}>{sub}</div>
      {children}
    </div>
  );
}
function Wire({ label, sub, st }) {
  const color = st === "ok" ? C.good : st === "fail" ? C.critical : C.faint;
  return (
    <div aria-hidden="true" style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
                                     minWidth: 74, flex: "0 1 90px", padding: "4px 0" }}>
      <span style={{ fontSize: 11, fontWeight: 800, color: C.sub }}>{label}</span>
      <div style={{ width: "100%", borderTop: `2.5px ${st === "fail" ? "dashed" : "solid"} ${color}`, margin: "4px 0" }} />
      <span style={{ fontSize: 11, color: C.muted }}>{sub}</span>
    </div>
  );
}
function SignalPath({ s }) {
  const L = s.link, cn = s.connection;
  const sensorState = L.sensors.some(x => x.state === "fail") ? "fail"
    : L.sensors.length && L.sensors.every(x => x.state === "ok") ? "ok"
    : L.sensors.some(x => x.state === "sim") ? "sim" : "unknown";
  const proto = cn.protocol === "rtu" ? "RTU over TCP" : "Modbus TCP";
  const faults = s.channels.filter(c => c.status === "fault");
  const msg = s.mode === "live" && s.comm.state === "error"
    ? <><b>Where it breaks: </b>{s.comm.message}{s.comm.lastOkAt ? ` · last good reading ${ago(s.comm.lastOkAt, s.now)}` : ""}</>
    : faults.length ? <><b>Sensor side: </b>{faults.map(c => `CH${c.id} ${c.name} - ${c.qualityText} (raw ${c.raw ?? "—"})`).join(" · ")}</> : null;
  return (
    <div style={{ ...card, padding: 14 }}>
      <div style={{ display: "flex", alignItems: "stretch", gap: 6, flexWrap: "wrap" }}>
        <Node name="Sensors" st={sensorState} sub={`${s.channels.length} channel${s.channels.length === 1 ? "" : "s"} on the card`}>
          <ul style={{ listStyle: "none", margin: "6px 0 0", padding: 0, fontSize: 12, color: C.sub }}>
            {s.channels.map(ch => {
              const st = L.sensors.find(x => x.id === ch.id)?.state || "unknown";
              return <li key={ch.id} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <Dot color={(NODE[st] || NODE.unknown)[1]} />CH{ch.id} {ch.name}</li>;
            })}
          </ul>
        </Node>
        <Wire label="Analog" sub="4-20 mA" st={sensorState} />
        <Node name={`${s.card.make} analog card`} st={L.card} sub={`Slave ${cn.unitId} · FC${cn.functionCode} · start ${cn.baseAddress}`} />
        <Wire label="RS-485" sub="Modbus RTU" st={L.serial} />
        <Node name="LAN converter" st={L.lan === "fail" ? "fail" : L.serial === "fail" ? "ok" : L.lan}
              sub={cn.host ? `${cn.host}:${cn.port} · ${proto}` : "IP not set"} />
        <Wire label="LAN" sub={`TCP ${cn.port}`} st={L.lan} />
        <Node name="MES server" st="ok" sub="Predictive Module · port 1006" />
      </div>
      {msg && <div style={{ marginTop: 10, fontSize: 13, color: C.critical, background: "#fef2f2", borderRadius: 8,
                            padding: "8px 10px" }}>{msg}</div>}
    </div>
  );
}

// ── sparkline (last minutes, with limits) ──────────────────────────────
function Spark({ pts, ch, w }) {
  if (!pts || pts.length < 2) return <div style={{ height: 44 }} />;
  const H = 44, vals = pts.map(p => p[1]);
  const lim = [ch.warnHi, ch.alarmHi, ch.warnLo, ch.alarmLo].filter(v => v !== null && v !== undefined);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const near = lim.filter(v => v >= lo - (hi - lo) * 2 && v <= hi + (hi - lo) * 2);
  lo = Math.min(lo, ...near); hi = Math.max(hi, ...near);
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0] || t0 + 1;
  const X = (t) => 2 + ((t - t0) / Math.max(1, t1 - t0)) * (w - 4);
  const Y = (v) => 4 + (1 - (v - lo) / (hi - lo)) * (H - 8);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
  return (
    <svg width={w} height={H} viewBox={`0 0 ${w} ${H}`} style={{ display: "block", marginTop: 8 }} aria-hidden="true">
      {[[ch.warnHi, C.warning], [ch.alarmHi, C.critical], [ch.warnLo, C.warning], [ch.alarmLo, C.critical]]
        .filter(([v]) => v !== null && v !== undefined && v >= lo && v <= hi)
        .map(([v, c], i) => <line key={i} x1={0} x2={w} y1={Y(v)} y2={Y(v)} stroke={c} strokeWidth={1} strokeDasharray="4 3" />)}
      <path d={d} fill="none" stroke={C.series} strokeWidth={1.8} strokeLinejoin="round" />
      <circle cx={X(pts[pts.length - 1][0])} cy={Y(pts[pts.length - 1][1])} r={3} fill={C.series} />
    </svg>
  );
}

function ChannelCard({ ch, selected, onSelect }) {
  const ref = useRef(null);
  const [w, setW] = useState(300);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => setW(Math.max(160, el.clientWidth - 30)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const span = Math.abs(ch.engMax - ch.engMin), u = ch.unit;
  const shown = ch.status === "fault" ? null : ch.value;
  const raw = ch.raw === null || ch.raw === undefined ? "" : `raw ${fmt(ch.raw, 10)}${ch.rawMin === 4 && ch.rawMax === 20 ? " mA" : ""}`;
  const cross = ch.crossing?.alarm || ch.crossing?.warning;
  const tone = toneOf(ch.health);
  const [, sColor] = STATUS[ch.status] || STATUS.offline;
  const adv = { good: C.good, warning: C.warning, critical: C.critical, info: C.series }[ch.advice?.tone] || C.sub;
  return (
    <button ref={ref} type="button" onClick={onSelect} aria-pressed={selected}
            style={{ ...card, textAlign: "left", padding: 15, cursor: "pointer", fontFamily: "inherit", minWidth: 0,
                     borderColor: selected ? C.series : ch.status !== "normal" ? sColor : C.line,
                     boxShadow: selected ? `0 0 0 2px ${C.series}33` : card.boxShadow }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "flex-start" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 11, color: C.muted, fontWeight: 700 }}>CH {ch.id} · @{ch.address} · {ch.type}</div>
          <div style={{ fontSize: 16, fontWeight: 800, color: C.ink }}>{ch.name}</div>
          {ch.asset && <div style={{ fontSize: 12, color: C.sub, fontWeight: 600 }}>{ch.asset}</div>}
        </div>
        <StatusPill status={ch.status} />
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginTop: 6 }}>
        <span style={{ fontSize: 38, fontWeight: 800, color: C.ink, fontFamily: "'Barlow Condensed',sans-serif" }}>{fmt(shown, span)}</span>
        <span style={{ fontSize: 16, fontWeight: 700, color: C.sub }}>{u}</span>
        <span style={{ marginLeft: "auto", fontSize: 12, color: C.faint }}>{raw}</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 12, color: C.muted, fontWeight: 700 }}>Health</span>
        <div role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={ch.health ?? 0} aria-label="Health"
             style={{ flex: 1, height: 8, background: "#f1f5f9", borderRadius: 999, overflow: "hidden" }}>
          <div style={{ width: `${ch.health ?? 0}%`, height: "100%", background: tone, borderRadius: 999 }} />
        </div>
        <span style={{ fontSize: 13, fontWeight: 800, color: tone, minWidth: 38, textAlign: "right" }}>
          {ch.health === null || ch.health === undefined ? "—" : `${ch.health}%`}</span>
      </div>
      <Spark pts={ch.spark} ch={ch} w={w} />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: 8, marginTop: 10 }}>
        <Stat k="10-min avg" v={ch.avg10m === null ? "—" : `${fmt(ch.avg10m, span)} ${u}`} />
        <Stat k="Baseline" v={ch.baseline ? `${fmt(ch.baseline.mean, span)} ${u}` : ch.learning ? "learning" : "—"}
              title={ch.baseline ? (ch.baseline.source === "manual" ? "Set by user" : "Automatic: first 24 h of data") : null} />
        <Stat k="Trend / day" v={ch.trend ? (ch.trend.significant ? `${fmtSigned(ch.trend.slopePerDay, span)} ${u}` : "stable") : "—"}
              title={ch.trend ? `R² ${ch.trend.r2} over ${ch.trend.points} hourly points` : "Needs 12 h of data"} />
        <Stat k={cross?.kind === "warning" ? "Warning in" : "Alarm in"} v={cross ? `~${fmtDays(cross.days)}` : "none (90 d)"}
              title={cross ? `${fmt(cross.limit, span)} ${u} on ${fmtDate(cross.at)}` : null} />
      </div>
      {ch.advice?.text && <div style={{ marginTop: 10, fontSize: 13, fontWeight: 600, color: adv, background: `${adv}12`,
                                        borderRadius: 8, padding: "7px 10px" }}>{ch.advice.text}</div>}
    </button>
  );
}

// ── trend & forecast chart (one channel, one y-axis) ───────────────────
function niceTicks(lo, hi, count = 5) {
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const raw = (hi - lo) / count, mag = 10 ** Math.floor(Math.log10(raw)), n = raw / mag;
  const step = (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag;
  const start = Math.floor(lo / step) * step, end = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = start; v <= end + step / 2; v += step) ticks.push(Math.round(v / step) * step);
  return { lo: start, hi: end, ticks, step };
}
const X_STEPS = [MIN, 2 * MIN, 5 * MIN, 10 * MIN, 15 * MIN, 30 * MIN, 60 * MIN, 120 * MIN, 180 * MIN, 360 * MIN, 720 * MIN, DAY, 2 * DAY, 5 * DAY, 7 * DAY];
const tzOff = (t) => -new Date(t).getTimezoneOffset() * MIN;
function timeTicks(x0, x1) {
  const step = X_STEPS.find(st => (x1 - x0) / st <= 7) ?? 7 * DAY;
  const out = [];
  for (let t = x0 - ((x0 + tzOff(x0)) % step) + step; t <= x1; t += step) out.push(t);
  return { ticks: out, step };
}
function TrendChart({ data, width }) {
  const [hover, setHover] = useState(null);
  const pts = data.points || [];
  if (!pts.length) return <div style={{ padding: 30, textAlign: "center", color: C.muted, fontSize: 13 }}>No readings in this range yet.</div>;
  const W = Math.max(320, width), H = W < 600 ? 260 : 330;
  const m = { l: 50, r: W < 600 ? 70 : 100, t: 16, b: 28 };
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  const lim = data.limits || {}, unit = data.unit ? ` ${data.unit}` : "";
  const span = Math.abs((data.engMax ?? 1) - (data.engMin ?? 0));
  const rangeMs = data.to - data.from, proj = data.projection;
  const showF = Boolean(proj && data.trend?.significant && rangeMs >= DAY);
  const x0 = data.from, x1 = data.to + (showF ? rangeMs * 0.4 : 0);
  const projAt = (t) => proj.fitNow + (proj.slopePerDay * (t - data.to)) / DAY;
  const ys = [];
  pts.forEach(p => ys.push(p[2], p[3]));
  ["warnHi", "alarmHi", "warnLo", "alarmLo"].forEach(k => { if (lim[k] !== null && lim[k] !== undefined) ys.push(lim[k]); });
  if (data.baseline) ys.push(data.baseline.mean - 2 * data.baseline.std, data.baseline.mean + 2 * data.baseline.std);
  if (showF) {
    const end = projAt(x1);
    const cap = lim.alarmHi != null ? lim.alarmHi + span * 0.08 : end, floor = lim.alarmLo != null ? lim.alarmLo - span * 0.08 : end;
    ys.push(Math.max(floor, Math.min(cap, end)));
  }
  let yLo = Math.min(...ys), yHi = Math.max(...ys);
  const padY = (yHi - yLo) * 0.06 || 1;
  const yt = niceTicks(yLo - padY, yHi + padY, H < 300 ? 4 : 5);
  yLo = yt.lo; yHi = yt.hi;
  const X = (t) => m.l + ((t - x0) / (x1 - x0)) * pw;
  const Y = (v) => m.t + (1 - (v - yLo) / (yHi - yLo)) * ph;
  const xt = timeTicks(x0, x1);
  const gap = (data.bucketMs || MIN) * 3.5;
  const runs = [];
  let run = [];
  pts.forEach(p => { if (run.length && p[0] - run[run.length - 1][0] > gap) { runs.push(run); run = []; } run.push(p); });
  if (run.length) runs.push(run);
  const refs = [["alarmHi", "Alarm", C.critical], ["warnHi", "Warning", C.warning], ["warnLo", "Low warn", C.warning], ["alarmLo", "Low alarm", C.critical]];
  const used = [];
  const refEls = refs.map(([k, label, color]) => {
    const v = lim[k];
    if (v === null || v === undefined || v < yLo || v > yHi) return null;
    let ly = Y(v) + 4;
    while (used.some(u => Math.abs(u - ly) < 13)) ly += 13;
    used.push(ly);
    return (
      <g key={k}>
        <line x1={m.l} x2={m.l + pw} y1={Y(v)} y2={Y(v)} stroke={color} strokeWidth={1.5} strokeDasharray="6 4" />
        <text x={m.l + pw + 8} y={ly} fontSize={11} fontWeight={700} fill={C.sub}>{label} {fmt(v, span)}</text>
      </g>
    );
  });
  let cross = null;
  if (showF) {
    const slope = proj.slopePerDay, target = slope > 0 ? lim.alarmHi : lim.alarmLo;
    if (target !== null && target !== undefined && slope !== 0) {
      const tc = data.to + ((target - proj.fitNow) / slope) * DAY;
      if (tc > data.to && tc <= x1) cross = { tc, target, inChart: true };
      else if (tc > x1) cross = { days: (tc - data.to) / DAY, inChart: false };
    }
  }
  const last = pts[pts.length - 1];
  const onMove = (clientX, el) => {
    const r = el.getBoundingClientRect();
    const t = x0 + ((clientX - r.left) * (W / r.width) - m.l) / pw * (x1 - x0);
    if (showF && t > data.to) { setHover({ t, v: projAt(Math.min(t, x1)), f: true }); return; }
    let lo = 0, hi = pts.length - 1;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (pts[mid][0] < t) lo = mid + 1; else hi = mid; }
    if (lo > 0 && Math.abs(pts[lo - 1][0] - t) < Math.abs(pts[lo][0] - t)) lo -= 1;
    setHover({ i: lo });
  };
  const hp = hover && hover.i !== undefined ? pts[hover.i] : null;
  const hx = hp ? X(hp[0]) : hover?.f ? X(Math.min(hover.t, x1)) : null;
  const hy = hp ? Y(hp[1]) : hover?.f ? Y(hover.v) : null;
  return (
    <div style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ display: "block", touchAction: "pan-y" }}
           onMouseMove={(e) => onMove(e.clientX, e.currentTarget)} onMouseLeave={() => setHover(null)}
           onTouchStart={(e) => { if (e.touches[0]) onMove(e.touches[0].clientX, e.currentTarget); }}
           role="img" aria-label={`${data.name}: ${pts.length} points. Latest ${fmt(last[1], span)}${unit}.`}>
        <defs><clipPath id="pmclip"><rect x={m.l} y={m.t} width={pw} height={ph} /></clipPath></defs>
        {yt.ticks.map(v => (
          <g key={v}>
            <line x1={m.l} x2={m.l + pw} y1={Y(v)} y2={Y(v)} stroke={C.grid} strokeWidth={1} />
            <text x={m.l - 8} y={Y(v) + 4} textAnchor="end" fontSize={11} fill={C.muted}>{fmt(v, yt.step * 10)}</text>
          </g>
        ))}
        {xt.ticks.map(t => <text key={t} x={X(t)} y={H - 8} textAnchor="middle" fontSize={11} fill={C.muted}>
          {xt.step >= DAY ? fmtDate(t) : fmtTime(t)}</text>)}
        <line x1={m.l} x2={m.l + pw} y1={m.t + ph} y2={m.t + ph} stroke={C.axis} />
        <g clipPath="url(#pmclip)">
          {showF && <rect x={X(data.to)} y={m.t} width={X(x1) - X(data.to)} height={ph} fill="#f1f5f9" opacity={0.8} />}
          {data.baseline && (() => {
            const b = data.baseline, top = Y(b.mean + 2 * b.std), bot = Y(b.mean - 2 * b.std);
            return <g><rect x={m.l} y={top} width={pw} height={Math.max(1, bot - top)} fill={C.band} />
              <line x1={m.l} x2={m.l + pw} y1={Y(b.mean)} y2={Y(b.mean)} stroke={C.axis} /></g>;
          })()}
          {runs.map((r, i) => (
            <g key={i}>
              {r.length > 1 && r.some(p => p[3] - p[2] > 1e-9) && (
                <path fill={C.wash} d={r.map((p, j) => `${j ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[3]).toFixed(1)}`).join("")
                  + r.slice().reverse().map(p => `L${X(p[0]).toFixed(1)},${Y(p[2]).toFixed(1)}`).join("") + "Z"} />
              )}
              <path fill="none" stroke={C.series} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
                    d={r.map((p, j) => `${j ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("")} />
            </g>
          ))}
          {refEls}
          {showF && (() => {
            const ts = Math.max(x0, data.trend.fromT ?? x0);
            return <line x1={X(ts)} y1={Y(projAt(ts))} x2={X(x1)} y2={Y(projAt(x1))} stroke={C.series} strokeWidth={2}
                         strokeDasharray="2 5" strokeLinecap="round" />;
          })()}
        </g>
        {showF && <><line x1={X(data.to)} x2={X(data.to)} y1={m.t} y2={m.t + ph} stroke={C.axis} />
          <text x={X(data.to) + 6} y={m.t + 12} fontSize={10} fontWeight={800} fill={C.muted}>FORECAST</text></>}
        {data.baseline && <text x={m.l + pw + 8} y={Y(data.baseline.mean) + 4} fontSize={11} fontWeight={700} fill={C.sub}>Baseline</text>}
        {cross?.inChart && <><circle cx={X(cross.tc)} cy={Y(cross.target)} r={5} fill={C.critical} stroke="#fff" strokeWidth={2} />
          <text x={X(cross.tc)} y={Y(cross.target) - 10} textAnchor={X(cross.tc) > m.l + pw - 90 ? "end" : "middle"}
                fontSize={11} fontWeight={700} fill={C.sub}>Alarm ~{fmtDate(cross.tc)}</text></>}
        {cross && !cross.inChart && <text x={m.l + pw - 4} y={m.t + 26} textAnchor="end" fontSize={11} fontWeight={700}
                                          fill={C.sub}>alarm in ~{fmtDays(cross.days)}</text>}
        <circle cx={X(last[0])} cy={Y(last[1])} r={4} fill={C.series} stroke="#fff" strokeWidth={2} />
        {hx !== null && <><line x1={hx} x2={hx} y1={m.t} y2={m.t + ph} stroke={C.muted} />
          <circle cx={hx} cy={hy} r={4.5} fill={C.series} stroke="#fff" strokeWidth={2} /></>}
      </svg>
      {hx !== null && (
        <div style={{ position: "absolute", top: 8, left: `min(calc(${(hx / W) * 100}% + 12px), calc(100% - 210px))`,
                      background: "#0f172a", color: "#fff", borderRadius: 8, padding: "7px 10px", fontSize: 12,
                      pointerEvents: "none", minWidth: 170, boxShadow: "0 4px 14px rgba(0,0,0,.25)" }}>
          {hp ? <>
            <div style={{ fontSize: 15, fontWeight: 800 }}>{fmt(hp[1], span)}{unit}</div>
            <div>min {fmt(hp[2], span)} · max {fmt(hp[3], span)}</div>
            <div style={{ color: "#cbd5e1" }}>{fmtDate(hp[0])} {fmtTime(hp[0], (data.bucketMs || MIN) < MIN)}</div>
          </> : <>
            <div style={{ fontSize: 15, fontWeight: 800 }}>{fmt(hover.v, span)}{unit}</div>
            <div style={{ color: "#cbd5e1" }}>Forecast · {fmtDate(hover.t)} {fmtTime(hover.t)}</div>
          </>}
        </div>
      )}
    </div>
  );
}
function Keys() {
  const k = (el, label) => <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: C.sub }}>
    <svg width={22} height={10} viewBox="0 0 22 10" aria-hidden="true">{el}</svg>{label}</span>;
  return (
    <div style={{ display: "flex", gap: 14, flexWrap: "wrap", margin: "6px 0 2px" }}>
      {k(<line x1={1} x2={21} y1={5} y2={5} stroke={C.series} strokeWidth={2} />, "Average")}
      {k(<rect x={1} y={1} width={20} height={8} rx={2} fill={C.wash} />, "Min–max")}
      {k(<rect x={1} y={1} width={20} height={8} rx={2} fill={C.band} />, "Healthy baseline ±2σ")}
      {k(<line x1={1} x2={21} y1={5} y2={5} stroke={C.warning} strokeWidth={1.5} strokeDasharray="6 4" />, "Warning limit")}
      {k(<line x1={1} x2={21} y1={5} y2={5} stroke={C.critical} strokeWidth={1.5} strokeDasharray="6 4" />, "Alarm limit")}
      {k(<line x1={1} x2={21} y1={5} y2={5} stroke={C.series} strokeWidth={2} strokeDasharray="2 5" strokeLinecap="round" />, "Trend forecast (24 h+ ranges)")}
    </div>
  );
}

// ── page ────────────────────────────────────────────────────────────────
export default function PredictiveMaintenance() {
  const { token } = useAuth();
  const [s, setS] = useState(null);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(() => { try { return Number(localStorage.getItem("pm.ch")) || null; } catch { return null; } });
  const [range, setRange] = useState(() => { try { const r = localStorage.getItem("pm.range"); return RANGES.includes(r) ? r : "7d"; } catch { return "7d"; } });
  const [table, setTable] = useState(false);
  const [hist, setHist] = useState({ key: "", data: null, at: 0, err: "" });
  const [note, setNote] = useState("");
  const detailRef = useRef(null);
  const chartBox = useRef(null);
  const [chartW, setChartW] = useState(900);

  useEffect(() => { document.title = "Predictive Maintenance"; }, []);
  const load = useCallback(() => api.get("/api/predictive/state", token)
    .then(d => { setS(d); setErr(""); })
    .catch(e => setErr(/404|Not Found/i.test(String(e && e.message))
      ? "Predictive Maintenance becomes available after the next MES-API restart."
      : (e && e.message) || "Could not reach the Predictive Module.")), [token]);
  useEffect(() => {
    let alive = true, t = null;
    const tick = () => {
      if (!alive) return;
      (document.hidden ? Promise.resolve() : load()).finally(() => { if (alive) t = setTimeout(tick, STATE_MS); });
    };
    tick();
    return () => { alive = false; clearTimeout(t); };
  }, [load]);

  const channels = useMemo(() => (s ? s.channels : []), [s]);
  const ch = channels.find(c => c.id === sel) || channels[0] || null;
  const chId = ch ? ch.id : null;
  useEffect(() => { try { if (chId) localStorage.setItem("pm.ch", String(chId)); localStorage.setItem("pm.range", range); } catch { /* private */ } }, [chId, range]);

  // history of the selected channel, refreshed per range
  const hKey = chId ? `${chId}|${range}` : "";
  useEffect(() => {
    if (!hKey) return undefined;
    let alive = true, t = null;
    const pull = () => api.get(`/api/predictive/history?ch=${chId}&range=${range}`, token)
      .then(d => { if (alive) setHist({ key: hKey, data: d, at: Date.now(), err: "" }); })
      .catch(e => { if (alive) setHist(h => ({ ...h, key: hKey, err: (e && e.message) || "Could not load the history." })); })
      .finally(() => { if (alive) t = setTimeout(pull, HISTORY_REFRESH[range]); });
    pull();
    return () => { alive = false; clearTimeout(t); };
  }, [hKey, token]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const el = chartBox.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => setChartW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, [s ? 1 : 0, table]);   // eslint-disable-line react-hooks/exhaustive-deps

  const flash = (t) => { setNote(t); setTimeout(() => setNote(n => (n === t ? "" : n)), 4000); };
  const setBaseline = (action) => {
    if (!ch) return;
    if (action === "set" && !window.confirm(`Use the last 24 hours of "${ch.name}" as its healthy baseline?\n\n`
      + "Do this only when the machine is known to be in good condition, for example right after maintenance or a bearing change. Health and deviation are measured against it.")) return;
    api.post("/api/predictive/baseline", { ch: ch.id, action, hours: 24 }, token)
      .then(() => { flash(action === "set" ? "Baseline saved." : "Back to the automatic baseline."); load(); setHist(h => ({ ...h, at: 0, key: "" })); })
      .catch(e => flash((e && e.message) || "Baseline not saved."));
  };
  const downloadCsv = async () => {
    if (!ch) return;
    const r2 = range === "15m" || range === "1h" ? "24h" : range;
    try {
      const res = await fetch(`/api/predictive/export.csv?ch=${ch.id}&range=${r2}`, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(`CSV not available (${res.status})`);
      const blob = await res.blob();
      const name = (/filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "") || [])[1] || `ch${ch.id}_${r2}.csv`;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = name; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    } catch (e) { flash(e.message); }
  };

  const shell = (children) => (
    <div style={{ minHeight: "100vh", background: C.bg, fontFamily: "'Barlow',sans-serif", color: C.ink }}>
      <PageTopbar leading="Predictive" accent="Maintenance" />
      <div style={{ padding: "16px clamp(12px, 3vw, 22px) 60px", maxWidth: 1500, margin: "0 auto" }}>{children}</div>
    </div>
  );
  if (!s) {
    return shell(<div style={{ ...card, padding: 28, textAlign: "center", color: err ? C.critical : C.muted, fontSize: 14, marginTop: 12 }}>
      {err || "Loading…"}</div>);
  }

  const o = s.overall, c = s.comm, n = o.counts, live = s.mode === "live";
  const next = o.nextAlarm, nextW = o.nextWarning;
  const lastAt = Math.max(0, ...channels.map(x => x.at || 0));
  const cn = s.connection;
  const proto = cn.protocol === "rtu" ? "Modbus RTU over TCP" : "Modbus TCP";
  const linkPill = !live ? ["Demo data", C.series] : c.state === "ok" ? [`LIVE · ${cn.host}`, C.good]
    : [c.state === "error" ? "LINK DOWN" : c.state === "blocked" ? "BLOCKED" : "NOT SET", C.critical];
  const span = ch ? Math.abs(ch.engMax - ch.engMin) : 1;
  const h = hist.key === hKey ? hist.data : null;
  const lanOnly = /^(192\.168\.|10\.|127\.|localhost)/.test(window.location.hostname);

  return shell(<>
    {/* header line */}
    <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
      <div style={{ minWidth: 0, flex: "1 1 320px" }}>
        <div style={{ fontSize: 13, color: C.sub, display: "flex", gap: 14, flexWrap: "wrap" }}>
          <span><b>{s.card.make} {s.card.model}</b></span>
          <span>Converter {cn.host || "not set"}{cn.host ? `:${cn.port}` : ""}</span>
          <span>{proto} · slave {cn.unitId} · FC{cn.functionCode} @{cn.baseAddress}</span>
          <span>Poll {cn.pollMs / 1000}s</span>
        </div>
      </div>
      {note && <span style={{ fontSize: 13, fontWeight: 700, color: /not|could/i.test(note) ? C.critical : C.good }}>{note}</span>}
      <span style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 13, fontWeight: 800, color: linkPill[1],
                     background: "#fff", border: `1px solid ${linkPill[1]}55`, borderRadius: 999, padding: "5px 12px" }}>
        <Dot color={linkPill[1]} pulse={live && c.state === "ok"} />{linkPill[0]}</span>
      {lanOnly && s.module_url && (
        <a href={s.module_url} target="_blank" rel="noreferrer" style={{ ...btn, textDecoration: "none" }}
           title="Channels, limits and the converter are set on the module's own page (plant LAN only)">Module settings ↗</a>
      )}
    </div>

    {!live && <div style={{ ...card, padding: "10px 14px", marginBottom: 12, color: C.series, fontSize: 13 }}>
      <b>Demo data.</b> The module is showing simulated signals.</div>}
    {live && (c.state === "blocked" || c.state === "idle") && (
      <div style={{ ...card, padding: "10px 14px", marginBottom: 12, color: C.warning, fontSize: 13 }}>{c.message}</div>)}
    {err && <div style={{ ...card, padding: "10px 14px", marginBottom: 12, color: C.critical, fontSize: 13 }}>{err} (showing the last reading)</div>}

    {/* tiles */}
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12 }}>
      <Tile label="Overall health" value={o.health === null ? "—" : `${o.health}%`} tone={toneOf(o.health)}
            sub={o.worst ? `Weakest: ${o.worst.name}` : "Learning baselines"} />
      <Tile label="Channels" value={`${n.normal} / ${channels.length} normal`}
            tone={n.alarm + n.fault ? C.critical : n.warning ? C.warning : null}
            sub={`${n.warning} warning · ${n.alarm} alarm · ${n.fault} fault · ${n.offline} no data`} />
      <Tile label="Next predicted alarm" value={next ? `~${fmtDays(next.days)}` : "—"}
            tone={next ? (next.days < 7 ? C.critical : C.warning) : null}
            sub={next ? `${next.name} → ${fmt(next.limit)} ${next.unit} on ${fmtDate(next.at)}`
              : nextW ? `Warning in ~${fmtDays(nextW.days)}: ${nextW.name}` : "No limit crossing in the next 90 days"} />
      <Tile label="Link to card" value={!live ? "Simulated" : c.state === "ok" ? "Online" : c.state === "error" ? "Down" : "Not set"}
            tone={!live ? null : c.state === "ok" ? C.good : C.critical}
            sub={!live ? "Demo mode" : c.state === "ok" ? `${fmt(c.latencyMs)} ms reply · ${c.okRate24h ?? "—"}% reads OK (24 h)` : c.message || ""} />
      <Tile label="Last reading" value={lastAt ? fmtTime(lastAt, true) : "—"}
            sub={lastAt ? `${ago(lastAt, s.now)} · every ${cn.pollMs / 1000}s` : "Waiting for the first reading"} />
    </div>

    <Section title="Signal path" sub="sensor → card → converter → server"><SignalPath s={s} /></Section>

    <Section title="Channels" sub="click a channel to see its trend">
      {channels.length ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(340px, 100%), 1fr))", gap: 12 }}>
          {channels.map(x => <ChannelCard key={x.id} ch={x} selected={ch && x.id === ch.id}
                                          onSelect={() => { setSel(x.id); detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }} />)}
        </div>
      ) : <div style={{ ...card, padding: 20, color: C.muted, fontSize: 13 }}>No channel enabled on the module.</div>}
    </Section>

    {ch && (
      <Section title="Trend & forecast" innerRef={detailRef}>
        <div style={{ ...card, padding: 14 }}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 10, flexWrap: "wrap" }}>
            <div style={{ flex: "1 1 260px", minWidth: 0 }}>
              <div style={{ fontSize: 16, fontWeight: 800 }}>CH {ch.id} · {ch.name}</div>
              <div style={{ fontSize: 12, color: C.muted }}>
                {[ch.asset, `${ch.unit || "value"} · range ${fmt(ch.engMin)}–${fmt(ch.engMax)}`, live ? null : "demo data"].filter(Boolean).join(" · ")}</div>
            </div>
            <div role="group" aria-label="Time range" style={{ display: "flex", border: `1px solid ${C.line}`, borderRadius: 8, overflow: "hidden" }}>
              {RANGES.map(r => (
                <button key={r} type="button" aria-pressed={r === range} onClick={() => setRange(r)}
                        style={{ border: "none", padding: "6px 11px", fontSize: 13, fontWeight: 700, cursor: "pointer",
                                 fontFamily: "inherit", background: r === range ? C.series : "#fff", color: r === range ? "#fff" : C.sub }}>{r}</button>
              ))}
            </div>
            <button type="button" style={btn} onClick={() => setTable(t => !t)}>{table ? "Chart" : "Table"}</button>
            {s.can_write && <button type="button" style={btn} onClick={() => setBaseline("set")}>Set baseline</button>}
            {s.can_write && ch.baseline?.source === "manual" &&
              <button type="button" style={btn} onClick={() => setBaseline("clear")}>Auto baseline</button>}
            <button type="button" style={{ ...btn, background: "#15803d", borderColor: "#15803d", color: "#fff" }} onClick={downloadCsv}>⬇ CSV</button>
          </div>
          <div ref={chartBox} style={{ marginTop: 10 }}>
            {hist.err && hist.key === hKey && !h ? <div style={{ padding: 20, color: C.critical, fontSize: 13 }}>{hist.err}</div>
              : !h ? <div style={{ padding: 30, textAlign: "center", color: C.muted, fontSize: 13 }}>Loading…</div>
              : table ? (
                <div style={{ maxHeight: 360, overflow: "auto", border: `1px solid ${C.line}`, borderRadius: 10 }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                    <thead><tr>{["Time", `Average ${h.unit}`, "Min", "Max"].map((x, i) => (
                      <th key={x} style={{ position: "sticky", top: 0, background: "#f8fafc", textAlign: i ? "right" : "left",
                                           padding: "7px 10px", borderBottom: `1px solid ${C.line}`, fontSize: 12 }}>{x}</th>))}</tr></thead>
                    <tbody>{h.points.slice(-300).reverse().map(([t, avg, mn, mx]) => (
                      <tr key={t}>
                        <td style={{ padding: "5px 10px", borderBottom: "1px solid #f1f5f9", whiteSpace: "nowrap" }}>{fmtDate(t)} {fmtTime(t, h.bucketMs < MIN)}</td>
                        {[avg, mn, mx].map((v, i) => <td key={i} style={{ padding: "5px 10px", borderBottom: "1px solid #f1f5f9", textAlign: "right" }}>{fmt(v, span)}</td>)}
                      </tr>))}</tbody>
                  </table>
                </div>
              ) : <><TrendChart data={h} width={chartW} /><Keys /></>}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginTop: 12,
                        paddingTop: 12, borderTop: `1px solid ${C.line}` }}>
            <Stat k="Now" v={ch.value === null ? "—" : `${fmt(ch.value, span)} ${ch.unit}`} />
            <Stat k="10-min average" v={ch.avg10m === null ? "—" : `${fmt(ch.avg10m, span)} ${ch.unit}`} />
            <Stat k="Healthy baseline" v={ch.baseline ? `${fmt(ch.baseline.mean, span)} ± ${fmt(ch.baseline.std, span)} ${ch.unit}`
              : ch.learning ? `learning (${ch.learnedMinutes ?? 0}/120 min)` : "—"}
                  title={ch.baseline ? (ch.baseline.source === "manual" ? `Set by user from ${fmtDateTime(ch.baseline.from)} to ${fmtDateTime(ch.baseline.to)}` : "Automatic: first 24 h of recorded data") : null} />
            <Stat k="Deviation" v={ch.z === null || ch.z === undefined ? "—" : `${ch.z > 0 ? "+" : ""}${ch.z.toFixed(1)} σ`}
                  title="How far the 10-minute average sits from the baseline, in standard deviations. |σ| ≥ 3 = abnormal." />
            <Stat k="Trend" v={ch.trend ? `${fmtSigned(ch.trend.slopePerDay, span)} ${ch.unit}/day` : "—"}
                  title={ch.trend ? `Least-squares line through ${ch.trend.points} hourly averages (7 days). R² ${ch.trend.r2}${ch.trend.significant ? "" : " - not significant, treated as stable"}` : "Needs 12 h of data"} />
            <Stat k="Forecast" v={ch.crossing?.alarm ? `Alarm ~${fmtDate(ch.crossing.alarm.at)}` : ch.crossing?.warning ? `Warning ~${fmtDate(ch.crossing.warning.at)}` : "No crossing (90 d)"}
                  title={ch.crossing?.alarm ? `Alarm limit ${fmt(ch.crossing.alarm.limit, span)} ${ch.unit} in ~${fmtDays(ch.crossing.alarm.days)}` : null} />
          </div>
        </div>
      </Section>
    )}

    <Section title="Events" sub="alarms, sensor faults, link changes">
      <div style={{ ...card, overflow: "hidden" }}>
        {!s.events.length ? <div style={{ padding: 20, color: C.muted, fontSize: 13 }}>No events yet.</div> : (
          <div style={{ maxHeight: 420, overflow: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 640 }}>
              <thead><tr>{["Time", "Event", "Channel", "Detail"].map(x => (
                <th key={x} style={{ position: "sticky", top: 0, background: "#f8fafc", textAlign: "left", padding: "8px 12px",
                                     borderBottom: `1px solid ${C.line}`, fontSize: 12 }}>{x}</th>))}</tr></thead>
              <tbody>{s.events.map(e => {
                const [label, color] = EVENT_LABEL[e.kind] || [e.kind, C.sub];
                const nm = channels.find(x => x.id === e.ch)?.name;
                return (
                  <tr key={e.id ?? `${e.t}-${e.kind}`}>
                    <td style={{ padding: "7px 12px", borderBottom: "1px solid #f1f5f9", whiteSpace: "nowrap" }}>{fmtDateTime(e.t)}</td>
                    <td style={{ padding: "7px 12px", borderBottom: "1px solid #f1f5f9", whiteSpace: "nowrap" }}>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontWeight: 700, color }}><Dot color={color} />{label}</span></td>
                    <td style={{ padding: "7px 12px", borderBottom: "1px solid #f1f5f9", whiteSpace: "nowrap" }}>{e.ch ? `CH${e.ch}${nm ? ` ${nm}` : ""}` : "—"}</td>
                    <td style={{ padding: "7px 12px", borderBottom: "1px solid #f1f5f9" }}>{e.text}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        )}
      </div>
    </Section>

    <p style={{ fontSize: 12, color: C.muted, marginTop: 18 }}>
      Read-only: the Predictive Module only sends Modbus <b>read</b> requests to the card and never writes to it.
      MES shows its data; channels, limits and the converter are set on the module page{lanOnly ? "" : " (plant LAN only)"}.
    </p>
  </>);
}
