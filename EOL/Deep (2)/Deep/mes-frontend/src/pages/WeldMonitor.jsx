/* ───────────────────────────────────────────────────────────────────
 * WeldMonitor.jsx   (Quality → Weld Monitor)   2026-06-18
 * ───────────────────────────────────────────────────────────────────
 * Live weld-parameter trends, wallboard-style.  Two sections:
 *   • Robot Welding      — live Weld Current (A) + Weld Voltage (V)
 *   • Projection Welding — placeholder (no data yet)
 *
 * Data: GET /api/weld/live  (polled every 3s), backed by mes_weld_log.
 *
 * 2026-08-03 — real feed + filters.
 *   • The live source is now the robot's current shunt read off the PPI
 *     analog card over Modbus TCP (see Phase2/weld_poller.py); one row per
 *     WELD, so one chart point = one weld.
 *   • X axis is selectable: Weld # · PART COUNT · Time.  Part count is
 *     stamped on every weld from the line's live counter, which is what lets
 *     Quality line a weld up against the part it was made on.
 *   • Filter bar: date · shift · zone · line · machine · station.  Options
 *     come from /api/weld/filters (only combinations that actually exist).
 *   • The spec band per station is configured in Quality Panel → Weld Master.
 */
import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../context/AuthContext";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceArea,
} from "recharts";

const api = {
  async get(p, t) {
    const r = await fetch(p, { headers: t ? { Authorization: `Bearer ${t}` } : {} });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  },
};

function num(v, d = 1) { return v == null || isNaN(v) ? "—" : Number(v).toFixed(d); }

/* X-axis modes.  `key` is the field on each reading; `label` names the axis
 * in the tooltip.  Part count comes straight from the line counter at the
 * moment the arc dropped, so a gap in it just means the line was idle. */
const X_MODES = [
  { k: "weld_seq",   label: "Weld #",     tip: "Weld #" },
  { k: "part_count", label: "Part Count", tip: "Part" },
  { k: "ts",         label: "Time",       tip: "" },
];

function WeldChart({ title, paramKey, unit, readings, spec, color, xMode }) {
  const mode = X_MODES.find(m => m.k === xMode) || X_MODES[0];
  const data = readings.map(r => ({
    x: mode.k === "ts"
      ? (r.ts ? new Date(r.ts).toLocaleTimeString("en-GB", { hour12: false }) : "")
      : (r[mode.k] ?? ""),
    v: r[paramKey],
    ng: r.is_ng,
    peak: r.weld_peak_a,
    dur: r.weld_duration_s,
  }));
  const vals = data.map(d => d.v).filter(x => x != null);
  const latest = vals.length ? vals[vals.length - 1] : null;
  const inSpec = latest != null && spec && latest >= spec.min && latest <= spec.max;
  const pad = spec ? (spec.max - spec.min) * 0.6 + 1 : 1;
  const lo = spec ? Math.min(spec.min, ...(vals.length ? vals : [spec.min])) - pad : "auto";
  const hi = spec ? Math.max(spec.max, ...(vals.length ? vals : [spec.max])) + pad : "auto";
  const ngCount = data.filter(d => d.ng).length;

  return (
    <div style={{
      background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14,
      padding: "18px 20px", boxShadow: "0 1px 3px rgba(0,0,0,.05)", marginBottom: 20,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end",
                     marginBottom: 12, flexWrap: "wrap", gap: 10 }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 800, letterSpacing: ".06em",
                         textTransform: "uppercase", color: "#64748b" }}>{title}</div>
          <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2 }}>
            Spec: {spec ? `${spec.min}–${spec.max} ${unit}` : "—"} · set {spec ? spec.set : "—"}{unit}
            {" · x-axis: "}<b style={{ color: "#64748b" }}>{mode.label}</b>
            {ngCount > 0 && <span style={{ color: "#b91c1c", fontWeight: 700 }}> · {ngCount} out of spec</span>}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <span style={{ fontSize: 34, fontWeight: 800, fontFamily: "monospace",
                          color: latest == null ? "#94a3b8" : inSpec ? "#16a34a" : "#dc2626", lineHeight: 1 }}>
            {num(latest)}<span style={{ fontSize: 15, marginLeft: 3 }}>{unit}</span>
          </span>
          <div style={{ marginTop: 4 }}>
            <span style={{
              fontSize: 10, fontWeight: 800, padding: "2px 10px", borderRadius: 999,
              background: latest == null ? "#f1f5f9" : inSpec ? "rgba(22,163,74,.12)" : "rgba(220,38,38,.12)",
              color: latest == null ? "#64748b" : inSpec ? "#15803d" : "#b91c1c",
            }}>{latest == null ? "NO DATA" : inSpec ? "IN SPEC" : "OUT OF SPEC"}</span>
          </div>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={data} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
          <XAxis dataKey="x" tick={{ fontSize: 10, fill: "#94a3b8" }} minTickGap={24} />
          <YAxis domain={[lo, hi]} tick={{ fontSize: 10, fill: "#94a3b8" }} width={44}
                 allowDecimals={false} />
          <Tooltip
            formatter={(v, _n, p) => {
              const d = p && p.payload;
              const extra = d && d.peak != null ? `  (peak ${num(d.peak)} A, ${num(d.dur, 2)} s)` : "";
              return [`${num(v)} ${unit}${extra}`, title];
            }}
            labelFormatter={(l) => (mode.tip ? `${mode.tip} ${l}` : String(l))} />
          {spec && (
            <ReferenceArea y1={spec.min} y2={spec.max} fill="#16a34a" fillOpacity={0.07}
                           ifOverflow="extendDomain" />
          )}
          <Line type="monotone" dataKey="v" stroke={color} strokeWidth={2}
                dot={false} isAnimationActive={false} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/* 2026-09-21 — gas sensor on the PPI analog card at 192.168.32.52, channel 6
 * (Phase2/gas_poller.py reads it every 2 s into mes_gas_log).  It is its own
 * feed — not tied to a weld, station or the filters above — so the x-axis is
 * time and it shows the last 30 minutes.
 * 2026-09-22 — shown as "Gas Flow" with the sign flipped (operator): the card
 * reports flow as negative, so negative readings display positive and positive
 * readings negative.  Display only — the stored readings are unchanged. */
const flip = (v) => (v == null || v === "" || isNaN(Number(v)) ? null : (Number(v) === 0 ? 0 : -Number(v)));
function GasChart({ token }) {
  const [g, setG] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let alive = true;
    const pull = () => api.get("/api/weld/gas?minutes=30", token)
      .then(r => { if (alive) { setG(r); setErr(""); } })
      .catch(e => { if (alive) setErr(String((e && e.message) || e)); });
    pull();
    const t = setInterval(pull, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [token]);

  const unit = (g && g.unit) || "";
  const data = ((g && g.readings) || []).map(r => ({
    x: r.ts ? new Date(r.ts).toLocaleTimeString("en-GB", { hour12: false }) : "",
    v: flip(r.v),
  }));
  const latest = g ? flip(g.latest) : null;
  const live = g && g.age_s != null && g.age_s <= 30;
  const badge = latest == null ? "NO DATA" : live ? "LIVE" : "NO NEW DATA";

  return (
    <div style={{
      background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14,
      padding: "18px 20px", boxShadow: "0 1px 3px rgba(0,0,0,.05)", marginBottom: 20,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end",
                     marginBottom: 12, flexWrap: "wrap", gap: 10 }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 800, letterSpacing: ".06em",
                         textTransform: "uppercase", color: "#64748b" }}>Gas Flow · CH{(g && g.channel) || 6}</div>
          <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2 }}>
            Card {(g && g.card) || "192.168.32.52"} · every {(g && g.every_s) || 2}s · last 30 min
            {" · x-axis: "}<b style={{ color: "#64748b" }}>Time</b>
            {latest != null && !live && g.age_s != null &&
              <span style={{ color: "#b45309", fontWeight: 700 }}> · last reading {Math.round(g.age_s / 60)} min ago</span>}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <span style={{ fontSize: 34, fontWeight: 800, fontFamily: "monospace",
                          color: latest == null ? "#94a3b8" : "#0f766e", lineHeight: 1 }}>
            {num(latest, 2)}<span style={{ fontSize: 15, marginLeft: 3 }}>{unit}</span>
          </span>
          <div style={{ marginTop: 4 }}>
            <span style={{
              fontSize: 10, fontWeight: 800, padding: "2px 10px", borderRadius: 999,
              background: live ? "rgba(15,118,110,.12)" : "#f1f5f9",
              color: live ? "#0f766e" : "#64748b",
            }}>{badge}</span>
          </div>
        </div>
      </div>
      {data.length === 0 ? (
        <div style={{ height: 240, display: "flex", alignItems: "center", justifyContent: "center",
                       color: "#94a3b8", fontSize: 13, textAlign: "center" }}>
          {/404/.test(err)
            ? "The gas feed starts after the next MES-API restart."
            : err ? `Gas feed not reachable (${err}).` : "Waiting for gas readings…"}
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={data} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
            <XAxis dataKey="x" tick={{ fontSize: 10, fill: "#94a3b8" }} minTickGap={24} />
            <YAxis domain={["auto", "auto"]} tick={{ fontSize: 10, fill: "#94a3b8" }} width={44} />
            <Tooltip formatter={(v) => [`${num(v, 2)} ${unit}`.trim(), "Gas flow"]} />
            <Line type="monotone" dataKey="v" stroke="#0f766e" strokeWidth={2}
                  dot={false} isAnimationActive={false} connectNulls />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

const LBL = {
  fontSize: 10, fontWeight: 700, letterSpacing: ".1em",
  textTransform: "uppercase", color: "#64748b",
};
const SEL = {
  padding: "8px 12px", borderRadius: 8, border: "1.5px solid #e2e8f0",
  fontSize: 13, background: "#f8fafc", minWidth: 118,
};

function Field({ label, children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <label style={LBL}>{label}</label>
      {children}
    </div>
  );
}

export default function WeldMonitor() {
  const { token, theme, user } = useAuth();
  const [tab, setTab] = useState("robot");
  const [station, setStation] = useState("");
  const [xMode, setXMode] = useState("weld_seq");
  // filters — "" means "all"
  const [f, setF] = useState({ date: "", shift: "", zone: "", line_id: "", machine: "" });
  const [opts, setOpts] = useState({ stations: [], zones: [], machines: [], shifts: [], dates: [], lines: [] });
  const [live, setLive] = useState({ readings: [], spec: null, stations: [], station: null });

  useEffect(() => { document.title = "Weld Monitor"; }, []);

  // dropdown options — refreshed occasionally, they change slowly
  useEffect(() => {
    if (tab !== "robot") return;
    const pull = () => api.get("/api/weld/filters?weld_type=robot", token)
      .then(setOpts).catch(() => {});
    pull();
    const t = setInterval(pull, 60000);
    return () => clearInterval(t);
  }, [tab, token]);

  const load = useCallback(() => {
    if (tab !== "robot") return;
    const q = new URLSearchParams({ weld_type: "robot", limit: "200" });
    if (station)   q.set("station", station);
    if (f.date)    q.set("date", f.date);
    if (f.shift)   q.set("shift", f.shift);
    if (f.zone)    q.set("zone", f.zone);
    if (f.line_id) q.set("line_id", f.line_id);
    if (f.machine) q.set("machine", f.machine);
    api.get(`/api/weld/live?${q.toString()}`, token)
      .then(d => { setLive(d); if (!station && d.station) setStation(d.station); })
      .catch(() => {});
  }, [tab, station, f, token]);

  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, [load]);

  const set = (k, v) => setF(p => ({ ...p, [k]: v }));
  const clear = () => setF({ date: "", shift: "", zone: "", line_id: "", machine: "" });
  const anyFilter = Object.values(f).some(Boolean);

  const accent = (theme && theme.accent) || "#7c3aed";
  const TABS = [{ k: "robot", label: "Robot Welding" }, { k: "projection", label: "Projection Welding" }];

  return (
    <div style={{ minHeight: "100vh", background: "#f8fafc", fontFamily: "'Barlow',sans-serif", paddingBottom: 60 }}>
      <div style={{ background: "#fff", borderBottom: "1px solid #e2e8f0", padding: "0 40px 0 88px",
                     height: 60, display: "flex", alignItems: "center", position: "sticky", top: 0, zIndex: 100,
                     boxShadow: "0 1px 3px rgba(0,0,0,.06)" }}>
        <div style={{ position: "absolute", left: "50%", transform: "translateX(-50%)",
                       fontFamily: "'Barlow Condensed',sans-serif", fontSize: 32, fontWeight: 800, color: "#0f172a" }}>
          ⚡ Weld <span style={{ color: accent }}>Monitor</span>
        </div>
        {user?.username && (
          <div style={{ marginLeft: "auto", fontSize: 12, color: "#64748b" }}>
            Signed in as <b style={{ color: "#0f172a" }}>{user.username}</b>
          </div>
        )}
      </div>

      <div style={{ maxWidth: 1180, margin: "0 auto", padding: "28px 40px 0" }}>
        {/* tabs */}
        <div style={{ display: "flex", gap: 0, marginBottom: 24, background: "#fff",
                       borderRadius: "12px 12px 0 0", border: "1px solid #e2e8f0", overflow: "hidden" }}>
          {TABS.map(t => (
            <button key={t.k} onClick={() => setTab(t.k)} style={{
              flex: 1, padding: "13px 20px", fontFamily: "'Barlow',sans-serif", fontSize: 14, fontWeight: 700,
              cursor: "pointer", border: "none",
              background: tab === t.k ? accent : "#fff", color: tab === t.k ? "#fff" : "#64748b",
            }}>{t.label}</button>
          ))}
        </div>

        {tab === "robot" && (
          <>
            <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12,
                           padding: "14px 18px", marginBottom: 18, display: "flex",
                           gap: 14, alignItems: "flex-end", flexWrap: "wrap" }}>
              <Field label="Station">
                <select value={station} onChange={e => setStation(e.target.value)} style={SEL}>
                  {(live.stations || []).map(s => <option key={s} value={s}>{s}</option>)}
                  {(!live.stations || !live.stations.length) && <option value="">— none —</option>}
                </select>
              </Field>
              <Field label="Date">
                <select value={f.date} onChange={e => set("date", e.target.value)} style={SEL}>
                  <option value="">All dates</option>
                  {(opts.dates || []).map(d => <option key={d} value={d}>{d}</option>)}
                </select>
              </Field>
              <Field label="Shift">
                <select value={f.shift} onChange={e => set("shift", e.target.value)} style={SEL}>
                  <option value="">All shifts</option>
                  {(opts.shifts || []).map(s => <option key={s} value={s}>{s}</option>)}
                </select>
              </Field>
              <Field label="Zone">
                <select value={f.zone} onChange={e => set("zone", e.target.value)} style={SEL}>
                  <option value="">All zones</option>
                  {(opts.zones || []).map(z => <option key={z} value={z}>{z}</option>)}
                </select>
              </Field>
              <Field label="Line">
                <select value={f.line_id} onChange={e => set("line_id", e.target.value)} style={SEL}>
                  <option value="">All lines</option>
                  {(opts.lines || []).map(l => <option key={l.id} value={l.id}>{l.line_code}</option>)}
                </select>
              </Field>
              <Field label="Machine">
                <select value={f.machine} onChange={e => set("machine", e.target.value)} style={SEL}>
                  <option value="">All machines</option>
                  {(opts.machines || []).map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              </Field>
              <Field label="X-Axis">
                <select value={xMode} onChange={e => setXMode(e.target.value)} style={SEL}>
                  {X_MODES.map(m => <option key={m.k} value={m.k}>{m.label}</option>)}
                </select>
              </Field>
              {anyFilter && (
                <button onClick={clear} style={{
                  padding: "8px 14px", borderRadius: 8, border: "1.5px solid #fecaca",
                  background: "#fef2f2", color: "#b91c1c", fontSize: 12, fontWeight: 700,
                  cursor: "pointer",
                }}>Clear filters</button>
              )}
              <div style={{ fontSize: 12, color: "#94a3b8", marginLeft: "auto", paddingBottom: 8 }}>
                {live.count || 0} welds · live (3s) · last #{live.latest?.weld_seq ?? "—"}
                {live.latest?.part_count != null && ` · part ${live.latest.part_count}`}
              </div>
            </div>

            {(!live.readings || live.readings.length === 0) ? (
              <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14,
                             padding: 60, textAlign: "center", color: "#94a3b8" }}>
                {anyFilter ? "No welds match these filters." : "Waiting for weld readings…"}
              </div>
            ) : (
              <>
                <WeldChart title="Weld Current" paramKey="weld_current" unit="A"
                           readings={live.readings} spec={live.spec?.current} color="#2563eb"
                           xMode={xMode} />
                <WeldChart title="Weld Voltage" paramKey="weld_voltage" unit="V"
                           readings={live.readings} spec={live.spec?.voltage} color="#d97706"
                           xMode={xMode} />
              </>
            )}
            <GasChart token={token} />
          </>
        )}

        {tab === "projection" && (
          <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14,
                         padding: 60, textAlign: "center", color: "#94a3b8" }}>
            <div style={{ fontSize: 40, marginBottom: 10 }}>🔩</div>
            <div style={{ fontSize: 16, fontWeight: 700, color: "#475569" }}>Projection Welding</div>
            <div style={{ fontSize: 13, marginTop: 6 }}>
              Add a projection-welding station in Quality Panel → Weld Master and its
              live Current / Voltage charts appear here.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
