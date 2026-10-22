/* Air Flow (Maintenance) — 2026-10-10
 * A self-contained air-flow monitor, moved out of Weld Monitor at the
 * operator's request.  Two tabs:
 *   • Live   — one live chart per configured air sensor (served by the same
 *              /api/weld/gas feed the gas chart uses; sensors are mes_weld_master
 *              rows with signal='sensor').
 *   • Master — add / edit / delete those sensors (card, channel, scale, …)
 *              through /api/weld/master, exactly like the Weld Master.
 * The analog card gives raw mA; L/min = (mA-4) × F.S./16, stored as
 * value = raw × scale + offset  (scale = F.S./16, offset = -F.S./4).
 */
import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../context/AuthContext";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceArea,
} from "recharts";

const api = {
  async get(p, t) {
    const r = await fetch(p, { headers: t ? { Authorization: `Bearer ${t}` } : {} });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  },
  async send(method, p, body, t) {
    const r = await fetch(p, {
      method,
      headers: { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) },
      body: body != null ? JSON.stringify(body) : undefined,
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  },
};

const num = (v, d = 1) => (v == null || isNaN(Number(v)) ? "—" : Number(v).toFixed(d));
const AVG_S = 30;

// each plotted point = mean of the readings in the last AVG_S seconds, flow-on
// only (a reading at/under 0 is "off"), so the fast on/off pulse averages to the
// flow actually going through the line.
function rollingAvg(series, windowS) {
  const out = [];
  let lo = 0;
  for (let hi = 0; hi < series.length; hi++) {
    const t = series[hi].t;
    if (t == null) { out.push(series[hi].v); continue; }
    while (lo < hi && series[lo].t != null && series[lo].t < t - windowS * 1000) lo++;
    let sum = 0, n = 0, seen = false;
    for (let k = lo; k <= hi; k++) {
      const v = series[k].v;
      if (v == null) continue;
      seen = true;
      if (v <= 0.5) continue;
      sum += v; n++;
    }
    out.push(n ? sum / n : seen ? 0 : null);
  }
  return out;
}

/* ── one live chart for a sensor station ─────────────────────────────────── */
function SensorChart({ token, station, machine, band }) {
  const [g, setG] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let alive = true;
    const pull = () => api.get(`/api/weld/gas?station=${encodeURIComponent(station)}&minutes=30`, token)
      .then(r => { if (alive) { setG(r); setErr(""); } })
      .catch(e => { if (alive) setErr(String((e && e.message) || e)); });
    pull();
    const t = setInterval(pull, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [token, station]);

  const unit = (g && g.unit) || "L/min";
  const toNum = (v) => (v == null || v === "" || isNaN(Number(v)) ? null : Number(v));
  const series = ((g && g.readings) || []).map(r => ({
    t: r.ts ? new Date(r.ts).getTime() : null,
    x: r.ts ? new Date(r.ts).toLocaleTimeString("en-GB", { hour12: false }) : "",
    v: toNum(r.v),
  }));
  const avg = rollingAvg(series, AVG_S);
  const lo = band && band.lo != null && band.lo !== "" ? Number(band.lo) : null;
  const hi = band && band.hi != null && band.hi !== "" ? Number(band.hi) : null;
  const isOut = (v) => v != null && ((lo != null && v < lo) || (hi != null && v > hi));
  const flags = avg.map(isOut);
  // vOut carries the value only where it's OUTSIDE the band (below lo or above
  // hi); the points next to a crossing are bridged in so the red segment joins
  // the blue line cleanly.  A second red <Line> draws just those regions.
  const data = series.map((d, i) => ({
    x: d.x, v: avg[i],
    vOut: (flags[i] || flags[i - 1] || flags[i + 1]) ? avg[i] : null,
  }));
  const latest = g ? toNum(g.latest) : null;
  const avgLatest = avg.length ? avg[avg.length - 1] : null;
  const live = g && g.age_s != null && g.age_s <= 30;
  const badge = latest == null ? "NO DATA" : live ? "LIVE" : "NO NEW DATA";
  const outOfBand = avgLatest != null && avgLatest > 0 && isOut(avgLatest);

  return (
    <div style={{
      background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14,
      padding: "18px 20px", boxShadow: "0 1px 3px rgba(0,0,0,.05)", marginBottom: 20,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end",
                     marginBottom: 12, flexWrap: "wrap", gap: 10 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 800, color: "#0f172a" }}>{station}</div>
          <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2 }}>
            {machine ? machine + " · " : ""}Card {(g && g.card) || "—"} · CH{(g && g.channel) || "—"}
            {" · every "}{(g && g.every_s) || 1}s · last 30 min · <b style={{ color: "#1d4ed8" }}>{AVG_S}s avg</b>
            {lo != null || hi != null
              ? <> · healthy: <b style={{ color: "#16a34a" }}>{lo ?? "—"}–{hi ?? "—"} {unit}</b></>
              : null}
            {latest != null && !live && g.age_s != null &&
              <span style={{ color: "#b45309", fontWeight: 700 }}> · last reading {Math.round(g.age_s / 60)} min ago</span>}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <span style={{ fontSize: 34, fontWeight: 800, fontFamily: "monospace", lineHeight: 1,
                          color: avgLatest == null ? "#94a3b8" : outOfBand ? "#dc2626" : "#1d4ed8" }}>
            {num(avgLatest, 1)}<span style={{ fontSize: 15, marginLeft: 3 }}>{unit}</span>
          </span>
          <div style={{ marginTop: 4 }}>
            <span style={{
              fontSize: 10, fontWeight: 800, padding: "2px 10px", borderRadius: 999,
              background: live ? "rgba(29,78,216,.12)" : "#f1f5f9",
              color: live ? "#1d4ed8" : "#64748b",
            }}>{badge}</span>
          </div>
          {latest != null && (
            <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 4, fontFamily: "monospace" }}>
              now {num(latest, 1)} {unit}
            </div>
          )}
        </div>
      </div>
      {data.length === 0 ? (
        <div style={{ height: 240, display: "flex", alignItems: "center", justifyContent: "center",
                       color: "#94a3b8", fontSize: 13, textAlign: "center" }}>
          {err ? `Feed not reachable (${err}).` : "Waiting for readings…"}
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={data} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
            <XAxis dataKey="x" tick={{ fontSize: 10, fill: "#94a3b8" }} minTickGap={24} />
            <YAxis domain={[0, (max) => Math.max(10, Math.ceil((Math.max(max || 0, hi || 0)) / 10) * 10)]}
                   tick={{ fontSize: 10, fill: "#94a3b8" }} width={44} />
            {lo != null && hi != null &&
              <ReferenceArea y1={lo} y2={hi} fill="#16a34a" fillOpacity={0.12}
                             stroke="#16a34a" strokeOpacity={0.35} strokeDasharray="4 3" ifOverflow="extendDomain" />}
            <Tooltip formatter={(v) => [`${num(v, 1)} ${unit}`.trim(), `${AVG_S}s average`]} />
            <Line type="monotone" dataKey="v" stroke="#1d4ed8" strokeWidth={2}
                  dot={false} isAnimationActive={false} connectNulls />
            {(lo != null || hi != null) &&
              <Line type="monotone" dataKey="vOut" stroke="#dc2626" strokeWidth={2.6}
                    dot={false} isAnimationActive={false} connectNulls={false} />}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

/* ── Live tab ────────────────────────────────────────────────────────────── */
function LiveTab({ token, sensors }) {
  if (!sensors.length) {
    return (
      <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14,
                     padding: 50, textAlign: "center", color: "#94a3b8" }}>
        <div style={{ fontSize: 40, marginBottom: 10 }}>💨</div>
        <div style={{ fontSize: 15, fontWeight: 700, color: "#475569" }}>No air-flow sensors yet</div>
        <div style={{ fontSize: 13, marginTop: 6 }}>Add one in the <b>Master</b> tab and its live chart appears here.</div>
      </div>
    );
  }
  return sensors.map(s => (
    <SensorChart key={s.id} token={token} station={s.station} machine={s.machine_name}
                 band={{ lo: s.band_lo, hi: s.band_hi }} />
  ));
}

/* ── Master tab ──────────────────────────────────────────────────────────── */
const BLANK = {
  id: null, station: "", machine_name: "", card_ip: "", card_port: 502, unit_id: 1,
  channel: 1, base_register: 2001, fs: 500, scale: 31.25, offset_val: -125,
  unit: "L/min", sample_s: 1, band_lo: "", band_hi: "", is_active: true,
};

function MasterTab({ token, sensors, lines, machines, onChanged }) {
  const [form, setForm] = useState(null);     // null = not editing
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const edit = (s) => setForm({
    ...BLANK, ...s,
    fs: s.scale ? Math.round(Number(s.scale) * 16) : 500,
    band_lo: s.band_lo ?? "", band_hi: s.band_hi ?? "",
  });
  const add = () => setForm({ ...BLANK });
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));
  const setFS = (v) => {
    const fs = Number(v);
    setForm(f => ({ ...f, fs: v, ...(isNaN(fs) ? {} : { scale: +(fs / 16).toFixed(5), offset_val: +(-fs / 4).toFixed(5) }) }));
  };

  const save = async () => {
    if (!form.station.trim()) { setMsg("Station name is required."); return; }
    if (!form.card_ip.trim()) { setMsg("Card IP is required."); return; }
    setBusy(true); setMsg("");
    try {
      const body = {
        id: form.id || undefined, signal: "sensor", weld_type: "sensor",
        station: form.station.trim(), machine_name: form.machine_name || null,
        card_ip: form.card_ip.trim(), card_port: Number(form.card_port) || 502,
        unit_id: Number(form.unit_id) || 1, channel: Number(form.channel) || 1,
        base_register: Number(form.base_register) || 2001,
        scale: Number(form.scale), offset_val: Number(form.offset_val),
        unit: form.unit || "L/min", sample_s: Number(form.sample_s) || 1,
        band_lo: form.band_lo === "" || form.band_lo == null ? null : Number(form.band_lo),
        band_hi: form.band_hi === "" || form.band_hi == null ? null : Number(form.band_hi),
        is_active: !!form.is_active,
      };
      const r = await api.send("POST", "/api/weld/master", body, token);
      if (r.ok === false) { setMsg(r.error || "Save failed."); setBusy(false); return; }
      setForm(null); onChanged();
    } catch (e) { setMsg(String((e && e.message) || e)); }
    setBusy(false);
  };

  const del = async (s) => {
    if (!window.confirm(`Delete air sensor "${s.station}"? Its live reading stops; stored history stays.`)) return;
    setBusy(true);
    try { await api.send("DELETE", `/api/weld/master/${s.id}`, null, token); onChanged(); }
    catch (e) { setMsg(String((e && e.message) || e)); }
    setBusy(false);
  };

  const TH = { textAlign: "left", padding: "8px 10px", fontSize: 11, fontWeight: 800,
               color: "#64748b", borderBottom: "2px solid #e2e8f0", textTransform: "uppercase", letterSpacing: ".04em" };
  const TD = { padding: "8px 10px", fontSize: 13, borderBottom: "1px solid #f1f5f9", color: "#0f172a" };
  const INP = { padding: "8px 10px", borderRadius: 8, border: "1.5px solid #e2e8f0", fontSize: 13, width: "100%", background: "#f8fafc" };
  const Field = ({ label, hint, children }) => (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <label style={{ fontSize: 11, fontWeight: 700, color: "#64748b" }}>{label}</label>
      {children}
      {hint && <span style={{ fontSize: 10, color: "#94a3b8" }}>{hint}</span>}
    </div>
  );

  return (
    <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, padding: 20 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
        <div style={{ fontSize: 14, fontWeight: 800, color: "#0f172a" }}>Air-flow sensors</div>
        {!form && <button onClick={add} style={{ padding: "8px 16px", borderRadius: 8, border: "none",
          background: "#1d4ed8", color: "#fff", fontWeight: 700, fontSize: 13, cursor: "pointer" }}>+ Add sensor</button>}
      </div>

      {form ? (
        <div style={{ border: "1px solid #e2e8f0", borderRadius: 12, padding: 16, background: "#f8fafc", marginBottom: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 800, marginBottom: 12, color: "#0f172a" }}>
            {form.id ? `Edit "${form.station}"` : "New air-flow sensor"}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 12 }}>
            <Field label="Station name *"><input style={INP} value={form.station} onChange={e => set("station", e.target.value)} placeholder="Y17-SS FI Air" /></Field>
            <Field label="Machine">
              <input style={INP} list="airflow-machines" value={form.machine_name || ""} onChange={e => set("machine_name", e.target.value)} placeholder="Final Inspection M/c" />
              <datalist id="airflow-machines">{(machines || []).map((m, i) => <option key={i} value={m.machine_name} />)}</datalist>
            </Field>
            <Field label="Card IP *"><input style={INP} value={form.card_ip} onChange={e => set("card_ip", e.target.value)} placeholder="192.168.30.177" /></Field>
            <Field label="Card port"><input style={INP} type="number" value={form.card_port} onChange={e => set("card_port", e.target.value)} /></Field>
            <Field label="Unit id"><input style={INP} type="number" value={form.unit_id} onChange={e => set("unit_id", e.target.value)} /></Field>
            <Field label="Channel (1–8)"><input style={INP} type="number" min={1} max={8} value={form.channel} onChange={e => set("channel", e.target.value)} /></Field>
            <Field label="Base register" hint="ch1 low reg (FC4)"><input style={INP} type="number" value={form.base_register} onChange={e => set("base_register", e.target.value)} /></Field>
            <Field label="Full scale (F.S.)" hint="L/min at 20 mA → sets scale & offset"><input style={INP} type="number" value={form.fs} onChange={e => setFS(e.target.value)} /></Field>
            <Field label="Scale" hint="value = mA × scale + offset"><input style={INP} type="number" step="any" value={form.scale} onChange={e => set("scale", e.target.value)} /></Field>
            <Field label="Offset"><input style={INP} type="number" step="any" value={form.offset_val} onChange={e => set("offset_val", e.target.value)} /></Field>
            <Field label="Unit"><input style={INP} value={form.unit} onChange={e => set("unit", e.target.value)} /></Field>
            <Field label="Healthy low" hint="green band lower (blank = none)"><input style={INP} type="number" step="any" value={form.band_lo} onChange={e => set("band_lo", e.target.value)} placeholder="e.g. 85" /></Field>
            <Field label="Healthy high" hint="green band upper (blank = none)"><input style={INP} type="number" step="any" value={form.band_hi} onChange={e => set("band_hi", e.target.value)} placeholder="e.g. 110" /></Field>
            <Field label="Sample (s)"><input style={INP} type="number" step="any" value={form.sample_s} onChange={e => set("sample_s", e.target.value)} /></Field>
            <Field label="Active">
              <select style={INP} value={form.is_active ? "1" : "0"} onChange={e => set("is_active", e.target.value === "1")}>
                <option value="1">Yes — reading</option><option value="0">No — paused</option>
              </select>
            </Field>
          </div>
          <div style={{ fontSize: 11, color: "#64748b", marginTop: 10 }}>
            4–20 mA sensor: L/min = (mA − 4) × F.S./16. Set <b>Full scale</b> to the sensor's analog
            F.S. (e.g. 500) and Scale / Offset fill in. The card must pass raw mA (Range Low/High = 4/20).
          </div>
          {msg && <div style={{ color: "#dc2626", fontSize: 12, marginTop: 10 }}>{msg}</div>}
          <div style={{ display: "flex", gap: 10, marginTop: 14 }}>
            <button disabled={busy} onClick={save} style={{ padding: "9px 20px", borderRadius: 8, border: "none",
              background: "#16a34a", color: "#fff", fontWeight: 700, fontSize: 13, cursor: "pointer", opacity: busy ? .6 : 1 }}>
              {busy ? "Saving…" : "Save"}</button>
            <button disabled={busy} onClick={() => { setForm(null); setMsg(""); }} style={{ padding: "9px 20px", borderRadius: 8,
              border: "1.5px solid #e2e8f0", background: "#fff", color: "#475569", fontWeight: 700, fontSize: 13, cursor: "pointer" }}>Cancel</button>
          </div>
        </div>
      ) : null}

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 760 }}>
          <thead><tr>
            <th style={TH}>Station</th><th style={TH}>Machine</th><th style={TH}>Card</th>
            <th style={TH}>CH</th><th style={TH}>Scale</th><th style={TH}>Offset</th>
            <th style={TH}>Unit</th><th style={TH}>Every</th><th style={TH}>Status</th><th style={TH}></th>
          </tr></thead>
          <tbody>
            {sensors.length === 0 && <tr><td style={{ ...TD, color: "#94a3b8" }} colSpan={10}>No air-flow sensors configured.</td></tr>}
            {sensors.map(s => (
              <tr key={s.id}>
                <td style={{ ...TD, fontWeight: 700 }}>{s.station}</td>
                <td style={TD}>{s.machine_name || "—"}</td>
                <td style={{ ...TD, fontFamily: "monospace", fontSize: 12 }}>{s.card_ip}</td>
                <td style={TD}>{s.channel}</td>
                <td style={TD}>{s.scale}</td>
                <td style={TD}>{s.offset_val}</td>
                <td style={TD}>{s.unit || "—"}</td>
                <td style={TD}>{s.sample_s || 1}s</td>
                <td style={TD}>
                  <span style={{ fontSize: 11, fontWeight: 700, padding: "2px 9px", borderRadius: 999,
                    background: s.is_active ? "rgba(22,163,74,.12)" : "#f1f5f9", color: s.is_active ? "#16a34a" : "#64748b" }}>
                    {s.is_active ? "Reading" : "Paused"}</span>
                </td>
                <td style={{ ...TD, whiteSpace: "nowrap" }}>
                  <button onClick={() => edit(s)} style={{ marginRight: 8, padding: "5px 12px", borderRadius: 7,
                    border: "1.5px solid #e2e8f0", background: "#fff", color: "#1d4ed8", fontWeight: 700, fontSize: 12, cursor: "pointer" }}>Edit</button>
                  <button onClick={() => del(s)} style={{ padding: "5px 12px", borderRadius: 7, border: "1.5px solid #fecaca",
                    background: "#fff", color: "#dc2626", fontWeight: 700, fontSize: 12, cursor: "pointer" }}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Cycles tab — per-cycle air flow, by date + shift ────────────────────── */
const cardBox = { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, boxShadow: "0 1px 3px rgba(0,0,0,.05)" };
const lbl = { display: "flex", flexDirection: "column", gap: 6, fontSize: 11, fontWeight: 700, color: "#64748b" };
function Stat({ label, value, color }) {
  return (
    <div style={{ ...cardBox, padding: "10px 16px", minWidth: 110 }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: "#94a3b8", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: color || "#0f172a", fontFamily: "monospace" }}>{value}</div>
    </div>
  );
}

function CyclesTab({ token, sensors }) {
  const [station, setStation] = useState(sensors[0]?.station || "");
  const [date, setDate] = useState("");
  const [shift, setShift] = useState("");
  const [d, setD] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => { if (!station && sensors[0]) setStation(sensors[0].station); }, [sensors, station]);

  const load = useCallback(() => {
    if (!station) return;
    setLoading(true);
    const q = new URLSearchParams({ station });
    if (date) q.set("date", date);
    if (shift) q.set("shift", shift);
    api.get(`/api/weld/air-cycles?${q.toString()}`, token)
      .then(r => { setD(r); if (!date && r.date) setDate(r.date); })
      .catch(() => setD({ rows: [], dates: [] }))
      .finally(() => setLoading(false));
  }, [station, date, shift, token]);
  useEffect(() => { load(); }, [load]);

  if (!sensors.length) {
    return <div style={{ ...cardBox, padding: 50, textAlign: "center", color: "#94a3b8" }}>
      Add an air-flow sensor in the <b>Master</b> tab first.</div>;
  }

  const rows = (d && d.rows) || [];
  const unit = (d && d.unit) || "L/min";
  const dates = (d && d.dates) || [];
  const okCnt = rows.filter(r => !r.is_ng).length;
  const ngCnt = rows.length - okCnt;
  const avgAir = (() => { const v = rows.map(r => r.air_avg).filter(x => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; })();

  const TH = { textAlign: "left", padding: "8px 10px", fontSize: 11, fontWeight: 800, color: "#64748b",
               borderBottom: "2px solid #e2e8f0", textTransform: "uppercase", whiteSpace: "nowrap" };
  const TD = { padding: "7px 10px", fontSize: 12.5, borderBottom: "1px solid #f1f5f9", color: "#0f172a" };
  const SEL = { padding: "8px 12px", borderRadius: 8, border: "1.5px solid #e2e8f0", fontSize: 13, background: "#f8fafc" };

  return (
    <div>
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 16 }}>
        {sensors.length > 1 &&
          <label style={lbl}>Sensor<select style={SEL} value={station} onChange={e => { setStation(e.target.value); setDate(""); setD(null); }}>
            {sensors.map(s => <option key={s.id} value={s.station}>{s.station}</option>)}</select></label>}
        <label style={lbl}>Date<select style={SEL} value={date} onChange={e => setDate(e.target.value)}>
          {dates.length === 0 && <option value="">—</option>}
          {dates.map(dt => <option key={dt} value={dt}>{dt}</option>)}</select></label>
        <label style={lbl}>Shift<select style={SEL} value={shift} onChange={e => setShift(e.target.value)}>
          <option value="">All</option><option value="A">A</option><option value="B">B</option></select></label>
        <button onClick={load} style={{ padding: "9px 16px", borderRadius: 8, border: "none", background: "#1d4ed8",
          color: "#fff", fontWeight: 700, fontSize: 13, cursor: "pointer" }}>Refresh</button>
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
        <Stat label="Cycles" value={rows.length} />
        <Stat label="OK" value={okCnt} color="#16a34a" />
        <Stat label="NG" value={ngCnt} color={ngCnt ? "#dc2626" : "#64748b"} />
        <Stat label={`Avg air (${unit})`} value={avgAir == null ? "—" : avgAir.toFixed(1)} color="#1d4ed8" />
      </div>

      <div style={{ ...cardBox, overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 720 }}>
          <thead><tr>
            <th style={TH}>Time</th><th style={TH}>Part code</th><th style={TH}>Shift</th>
            <th style={TH}>CT (s)</th><th style={TH}>Air avg</th><th style={TH}>Air min</th>
            <th style={TH}>Air max</th><th style={TH}>OK/NG</th>
          </tr></thead>
          <tbody>
            {loading && <tr><td style={{ ...TD, color: "#94a3b8" }} colSpan={8}>Loading…</td></tr>}
            {!loading && rows.length === 0 && <tr><td style={{ ...TD, color: "#94a3b8" }} colSpan={8}>No cycles for this date / shift.</td></tr>}
            {!loading && rows.map((r, i) => (
              <tr key={i} style={{ background: r.is_ng ? "rgba(220,38,38,.05)" : "transparent" }}>
                <td style={{ ...TD, fontFamily: "monospace", whiteSpace: "nowrap" }}>{r.ts ? r.ts.slice(11, 19) : "—"}</td>
                <td style={{ ...TD, fontFamily: "monospace", fontSize: 11 }}>{r.part_code || "—"}</td>
                <td style={TD}>{r.shift || "—"}</td>
                <td style={TD}>{r.ct == null ? "—" : r.ct}</td>
                <td style={{ ...TD, fontWeight: 700, color: "#1d4ed8" }}>{r.air_avg == null ? "—" : r.air_avg}</td>
                <td style={TD}>{r.air_min == null ? "—" : r.air_min}</td>
                <td style={TD}>{r.air_max == null ? "—" : r.air_max}</td>
                <td style={TD}>{r.is_ng
                  ? <span style={{ color: "#dc2626", fontWeight: 800 }}>NG</span>
                  : <span style={{ color: "#16a34a", fontWeight: 700 }}>OK</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > 0 && <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 8 }}>
        Air avg / min / max = flow over each cycle's window (cycle start → end). Newest first, up to 300 cycles.</div>}
    </div>
  );
}

/* ── page ────────────────────────────────────────────────────────────────── */
export default function AirFlow() {
  const { token } = useAuth();
  const [tab, setTab] = useState("live");
  const [data, setData] = useState({ rows: [], lines: [], machines: [] });

  useEffect(() => { document.title = "Air Flow"; }, []);
  const load = useCallback(() => {
    api.get("/api/weld/master", token)
      .then(d => setData({ rows: d.rows || [], lines: d.lines || [], machines: d.machines || [] }))
      .catch(() => {});
  }, [token]);
  useEffect(() => { load(); }, [load]);

  const sensors = (data.rows || []).filter(r => String(r.signal || "").toLowerCase() === "sensor");

  const TabBtn = ({ k, label }) => (
    <button onClick={() => setTab(k)} style={{
      padding: "10px 22px", border: "none", borderBottom: tab === k ? "3px solid #1d4ed8" : "3px solid transparent",
      background: "none", fontWeight: 700, fontSize: 14, cursor: "pointer", color: tab === k ? "#1d4ed8" : "#64748b",
    }}>{label}</button>
  );

  return (
    <div style={{ padding: "22px 26px", maxWidth: 1100, margin: "0 auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
        <span style={{ fontSize: 22 }}>💨</span>
        <h1 style={{ fontSize: 22, fontWeight: 800, color: "#0f172a", margin: 0 }}>Air Flow</h1>
      </div>
      <div style={{ fontSize: 13, color: "#94a3b8", marginBottom: 16 }}>
        Live air-flow from analog flow sensors (SMC PFMB-series, 4–20 mA), and their configuration.
      </div>
      <div style={{ display: "flex", borderBottom: "1px solid #e2e8f0", marginBottom: 20 }}>
        <TabBtn k="live" label="Live" />
        <TabBtn k="cycles" label="Cycles" />
        <TabBtn k="master" label="Master" />
      </div>
      {tab === "live" ? <LiveTab token={token} sensors={sensors} />
        : tab === "cycles" ? <CyclesTab token={token} sensors={sensors} />
        : <MasterTab token={token} sensors={sensors} lines={data.lines} machines={data.machines} onChanged={load} />}
    </div>
  );
}
