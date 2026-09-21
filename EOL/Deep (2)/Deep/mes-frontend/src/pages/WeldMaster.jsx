/* ───────────────────────────────────────────────────────────────────
 * WeldMaster.jsx   (Quality Panel → Weld Master)   2026-08-03
 * ───────────────────────────────────────────────────────────────────
 * One row per welding STATION (robot).  This is the single place that
 * decides what the live weld feed does, so adding a robot here is all it
 * takes — Phase2/weld_poller.py re-reads this table every 60 s and starts a
 * worker for any new active station.  No code change, no restart.
 *
 * A row says three things:
 *   1. WHERE the signal comes from — PPI/analog card IP + Modbus TCP port +
 *      unit id + which of the card's 8 channels the current shunt is on.
 *   2. WHERE the robot sits — zone → line → machine.  These are the same
 *      names the rest of MES uses, so the Weld Monitor's zone/line/machine
 *      filters line up with the production master.
 *   3. WHAT counts as good — the acceptable current (and voltage) band.
 *      The monitor draws this as the green spec area and flags welds
 *      outside it.
 *
 * Scaling note: `mV → A` converts the card's raw millivolts to amps.  For a
 * 60 mV = 600 A shunt that is 10.  Detection knobs (arc-on threshold, gap,
 * minimum weld length, sample rate) decide where one weld ends and the next
 * begins; the defaults were measured on RC-01 and suit MAG robot welding.
 */
import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";

const BLANK = {
  station: "", weld_type: "robot", zone: "", line_id: "", machine_name: "",
  card_ip: "", card_port: 502, unit_id: 1, channel: 8, base_register: 2001,
  mv_to_a: 10,
  current_min: "", current_set: "", current_max: "",
  voltage_min: "", voltage_set: "", voltage_max: "",
  on_threshold_a: 30, gap_s: 0.35, min_weld_s: 0.2, sample_hz: 50,
  is_active: true, note: "",
};

const L = { fontSize: 10, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: "#64748b" };
const I = { padding: "8px 10px", borderRadius: 8, border: "1.5px solid #e2e8f0",
            fontSize: 13, width: "100%", maxWidth: "100%", minWidth: 0,
            boxSizing: "border-box", background: "#fff" };
const TH = { textAlign: "left", padding: "10px 12px", fontSize: 10, fontWeight: 800, letterSpacing: ".08em", textTransform: "uppercase", color: "#64748b", borderBottom: "1px solid #e2e8f0", whiteSpace: "nowrap" };
const TD = { padding: "10px 12px", fontSize: 13, color: "#0f172a", borderBottom: "1px solid #f1f5f9", whiteSpace: "nowrap" };

// One form field.  `span` = how many grid columns it should occupy.
// Fields live in a CSS grid (not flex-wrap): every cell gets an equal, fixed
// share, so a long <select> can no longer stretch and shove its neighbours —
// which is what made the boxes overlap on narrower windows.
function F({ label, hint, children, span = 1 }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 5,
                  minWidth: 0, gridColumn: `span ${span}` }}>
      <label style={L}>{label}</label>
      {children}
      {hint && <span style={{ fontSize: 10, color: "#94a3b8", lineHeight: 1.3 }}>{hint}</span>}
    </div>
  );
}

// 3-across grid; collapses to fewer columns on a narrow screen.
const GRID = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
  gap: 14,
  alignItems: "start",
};

export default function WeldMaster({ toast, readOnly = false }) {
  const { token } = useAuth();
  const say = toast || (() => {});
  const [rows, setRows]   = useState([]);
  const [lines, setLines] = useState([]);
  const [machines, setMachines] = useState([]);
  const [zones, setZones] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState(false);
  const [form, setForm]   = useState(BLANK);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api.get("/api/weld/master", token);
      setRows(d.rows || []);
      setLines(d.lines || []);
      setMachines(d.machines || []);
      setZones(d.zones || []);
    } catch {
      say("Failed to load weld master", "err");
    } finally { setLoading(false); }
  }, [token, say]);

  useEffect(() => { load(); }, [load]);

  const open = (row) => {
    setForm(row ? { ...BLANK, ...row, line_id: row.line_id ?? "" } : { ...BLANK });
    setModal(true);
  };
  const set = (k, v) => setForm(p => ({ ...p, [k]: v }));

  const save = async () => {
    if (!form.station.trim()) { say("Robot / station name is required", "err"); return; }
    if (!form.card_ip.trim()) { say("Card IP is required", "err"); return; }
    setSaving(true);
    try {
      // "" → null so the backend stores a real NULL rather than an empty string
      const body = { ...form };
      ["line_id", "current_min", "current_set", "current_max",
       "voltage_min", "voltage_set", "voltage_max"].forEach(k => {
        if (body[k] === "" || body[k] === null) body[k] = null;
        else body[k] = Number(body[k]);
      });
      ["card_port", "unit_id", "channel", "base_register"].forEach(k => body[k] = Number(body[k]));
      ["mv_to_a", "on_threshold_a", "gap_s", "min_weld_s", "sample_hz"].forEach(k => body[k] = Number(body[k]));
      const r = await api.post("/api/weld/master", body, token);
      if (r && r.ok === false) { say(r.error || "Save failed", "err"); return; }
      say(`Station ${form.station} saved — the poller picks it up within a minute`, "ok");
      setModal(false);
      load();
    } catch (e) {
      say("Save failed: " + (e.message || e), "err");
    } finally { setSaving(false); }
  };

  const del = async (row) => {
    if (!window.confirm(`Delete station ${row.station}?  Its logged welds stay in history.`)) return;
    try {
      await api.delete(`/api/weld/master/${row.id}`, token);
      say(`Station ${row.station} deleted`, "ok");
      load();
    } catch (e) { say("Delete failed: " + (e.message || e), "err"); }
  };

  // Zone → Line → Machine cascade.  Choosing a zone narrows the line list to
  // that zone; choosing a line narrows the machine list to that line.  With
  // nothing chosen yet every option is offered, so the form never dead-ends.
  const zoneLines = lines.filter(l => !form.zone || l.zone_name === form.zone);
  const lineMachines = machines.filter(m => !form.line_id || String(m.line_id) === String(form.line_id));

  // Changing the zone must clear a line that no longer belongs to it (and the
  // machine with it), otherwise the form would silently keep a mismatched pair.
  const pickZone = (z) => {
    setForm(p => {
      const stillValid = lines.some(l => String(l.id) === String(p.line_id) && l.zone_name === z);
      return { ...p, zone: z, line_id: stillValid ? p.line_id : "",
               machine_name: stillValid ? p.machine_name : "" };
    });
  };
  const pickLine = (id) => {
    setForm(p => {
      const ln = lines.find(l => String(l.id) === String(id));
      return { ...p, line_id: id,
               // adopt the line's zone so the two can never disagree
               zone: ln?.zone_name || p.zone,
               machine_name: "" };
    });
  };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 800, color: "#0f172a" }}>Weld Master</div>
          <div style={{ fontSize: 12, color: "#64748b", marginTop: 2 }}>
            Welding stations, their analog card / channel, and the acceptable current band.
            The live poller reads this table — add a robot and it starts logging.
          </div>
        </div>
        {!readOnly && (
          <button onClick={() => open(null)} style={{
            marginLeft: "auto", padding: "9px 16px", borderRadius: 8, border: "none",
            background: "#ca8a04", color: "#fff", fontWeight: 700, fontSize: 13, cursor: "pointer",
          }}>+ Add Station</button>
        )}
      </div>

      <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12, overflow: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 1000 }}>
          <thead style={{ background: "#f8fafc" }}>
            <tr>
              <th style={TH}>Station</th>
              <th style={TH}>Type</th>
              <th style={TH}>Zone / Line / Machine</th>
              <th style={TH}>Card (IP:port · unit)</th>
              <th style={TH}>Ch</th>
              <th style={TH}>mV→A</th>
              <th style={TH}>Current band (A)</th>
              <th style={TH}>Active</th>
              {!readOnly && <th style={TH}></th>}
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td style={TD} colSpan={9}>Loading…</td></tr>}
            {!loading && !rows.length && (
              <tr><td style={{ ...TD, color: "#94a3b8", padding: 28 }} colSpan={9}>
                No welding stations configured yet — add one to start logging weld current.
              </td></tr>
            )}
            {rows.map(r => (
              <tr key={r.id}>
                <td style={{ ...TD, fontWeight: 700 }}>{r.station}</td>
                <td style={TD}>{r.weld_type}</td>
                <td style={TD}>
                  {[r.zone, r.line_name || r.line_code || null, r.machine_name]
                    .filter(Boolean).join(" · ") || <span style={{ color: "#94a3b8" }}>—</span>}
                </td>
                <td style={{ ...TD, fontFamily: "monospace" }}>{r.card_ip}:{r.card_port} · u{r.unit_id}</td>
                <td style={TD}>{r.channel}</td>
                <td style={TD}>{r.mv_to_a}</td>
                <td style={TD}>
                  {r.current_min != null || r.current_max != null
                    ? `${r.current_min ?? "—"} – ${r.current_max ?? "—"}${r.current_set != null ? `  (set ${r.current_set})` : ""}`
                    : <span style={{ color: "#94a3b8" }}>not set</span>}
                </td>
                <td style={TD}>
                  <span style={{
                    fontSize: 10, fontWeight: 800, padding: "3px 9px", borderRadius: 999,
                    background: r.is_active ? "rgba(22,163,74,.12)" : "#f1f5f9",
                    color: r.is_active ? "#15803d" : "#64748b",
                  }}>{r.is_active ? "LIVE" : "OFF"}</span>
                </td>
                {!readOnly && (
                  <td style={TD}>
                    <button onClick={() => open(r)} style={{ marginRight: 8, padding: "5px 11px", borderRadius: 7,
                      border: "1.5px solid #e2e8f0", background: "#fff", fontSize: 12, cursor: "pointer" }}>Edit</button>
                    <button onClick={() => del(r)} style={{ padding: "5px 11px", borderRadius: 7,
                      border: "1.5px solid #fecaca", background: "#fef2f2", color: "#b91c1c",
                      fontSize: 12, cursor: "pointer" }}>Delete</button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modal && (
        <div onClick={() => !saving && setModal(false)} style={{
          position: "fixed", inset: 0, background: "rgba(15,23,42,.5)", zIndex: 2000,
          display: "flex", alignItems: "center", justifyContent: "center", padding: 20,
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: "#f8fafc", borderRadius: 14, width: "100%", maxWidth: 860,
            maxHeight: "90vh", overflow: "auto", boxShadow: "0 20px 60px rgba(0,0,0,.3)",
          }}>
            <div style={{ padding: "18px 22px", borderBottom: "1px solid #e2e8f0", background: "#fff",
                           borderRadius: "14px 14px 0 0", position: "sticky", top: 0 }}>
              <div style={{ fontSize: 17, fontWeight: 800, color: "#0f172a" }}>
                {form.id ? `Edit ${form.station}` : "Add welding station"}
              </div>
            </div>

            <div style={{ padding: 22, display: "flex", flexDirection: "column", gap: 20 }}>
              <section>
                <div style={{ ...L, marginBottom: 10, color: "#0f172a" }}>Identity &amp; location</div>
                <div style={GRID}>
                  <F label="Robot / station *" hint="e.g. RC-01">
                    <input style={I} value={form.station} onChange={e => set("station", e.target.value)} />
                  </F>
                  <F label="Weld type">
                    <select style={I} value={form.weld_type} onChange={e => set("weld_type", e.target.value)}>
                      <option value="robot">robot</option>
                      <option value="projection">projection</option>
                    </select>
                  </F>
                  <F label="Zone">
                    <select style={I} value={form.zone || ""} onChange={e => pickZone(e.target.value)}>
                      <option value="">— all zones —</option>
                      {zones.map(z => <option key={z.id} value={z.zone_name}>{z.zone_name}</option>)}
                    </select>
                  </F>
                  <F label="Line" hint={form.zone ? `${zoneLines.length} line(s) in ${form.zone}` : null}>
                    <select style={I} value={form.line_id ?? ""} onChange={e => pickLine(e.target.value)}>
                      <option value="">— none —</option>
                      {zoneLines.map(l => (
                        <option key={l.id} value={l.id}>
                          {l.line_code}{l.line_name && l.line_name !== l.line_code ? ` · ${l.line_name}` : ""}
                        </option>
                      ))}
                    </select>
                  </F>
                  <F label="Machine"
                     hint={form.line_id ? `${lineMachines.length} machine(s) on this line`
                                        : "pick a line first"}>
                    <select style={I} value={form.machine_name || ""}
                            onChange={e => set("machine_name", e.target.value)}
                            disabled={!form.line_id}>
                      <option value="">— none —</option>
                      {lineMachines.map((m, i) => (
                        <option key={i} value={m.machine_name}>{m.machine_name}</option>
                      ))}
                    </select>
                  </F>
                </div>
              </section>

              <section>
                <div style={{ ...L, marginBottom: 10, color: "#0f172a" }}>Analog card (Modbus TCP)</div>
                <div style={GRID}>
                  <F label="Card IP *" hint="PPI / analog input card">
                    <input style={I} value={form.card_ip} onChange={e => set("card_ip", e.target.value)}
                           placeholder="192.168.31.59" />
                  </F>
                  <F label="Port">
                    <input style={I} type="number" value={form.card_port} onChange={e => set("card_port", e.target.value)} />
                  </F>
                  <F label="Unit id">
                    <input style={I} type="number" value={form.unit_id} onChange={e => set("unit_id", e.target.value)} />
                  </F>
                  <F label="Channel" hint="1–8">
                    <select style={I} value={form.channel} onChange={e => set("channel", e.target.value)}>
                      {[1,2,3,4,5,6,7,8].map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </F>
                  <F label="Base register" hint="ch1 low reg (FC4)">
                    <input style={I} type="number" value={form.base_register}
                           onChange={e => set("base_register", e.target.value)} />
                  </F>
                  <F label="mV → A" hint="60 mV = 600 A → 10">
                    <input style={I} type="number" step="0.1" value={form.mv_to_a}
                           onChange={e => set("mv_to_a", e.target.value)} />
                  </F>
                </div>
              </section>

              <section>
                <div style={{ ...L, marginBottom: 10, color: "#0f172a" }}>Acceptable range (spec band)</div>
                <div style={GRID}>
                  <F label="Current min (A)"><input style={I} type="number" value={form.current_min ?? ""} onChange={e => set("current_min", e.target.value)} /></F>
                  <F label="Current set (A)"><input style={I} type="number" value={form.current_set ?? ""} onChange={e => set("current_set", e.target.value)} /></F>
                  <F label="Current max (A)"><input style={I} type="number" value={form.current_max ?? ""} onChange={e => set("current_max", e.target.value)} /></F>
                  <F label="Voltage min (V)"><input style={I} type="number" value={form.voltage_min ?? ""} onChange={e => set("voltage_min", e.target.value)} /></F>
                  <F label="Voltage set (V)"><input style={I} type="number" value={form.voltage_set ?? ""} onChange={e => set("voltage_set", e.target.value)} /></F>
                  <F label="Voltage max (V)"><input style={I} type="number" value={form.voltage_max ?? ""} onChange={e => set("voltage_max", e.target.value)} /></F>
                </div>
                <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 8 }}>
                  Leave voltage blank when the card only carries a current shunt — the monitor
                  simply shows no voltage trend for that station.
                </div>
              </section>

              <section>
                <div style={{ ...L, marginBottom: 10, color: "#0f172a" }}>Weld detection</div>
                <div style={GRID}>
                  <F label="Arc-on (A)" hint="above this = welding"><input style={I} type="number" value={form.on_threshold_a} onChange={e => set("on_threshold_a", e.target.value)} /></F>
                  <F label="Gap (s)" hint="silence that ends a weld"><input style={I} type="number" step="0.05" value={form.gap_s} onChange={e => set("gap_s", e.target.value)} /></F>
                  <F label="Min weld (s)" hint="ignore shorter blips"><input style={I} type="number" step="0.05" value={form.min_weld_s} onChange={e => set("min_weld_s", e.target.value)} /></F>
                  <F label="Sample rate (Hz)"><input style={I} type="number" value={form.sample_hz} onChange={e => set("sample_hz", e.target.value)} /></F>
                  <F label="Status">
                    <select style={I} value={form.is_active ? "1" : "0"} onChange={e => set("is_active", e.target.value === "1")}>
                      <option value="1">Active (logging)</option>
                      <option value="0">Inactive</option>
                    </select>
                  </F>
                </div>
              </section>

              <div style={GRID}>
              <F label="Note" span={3}>
                <input style={I} value={form.note || ""} onChange={e => set("note", e.target.value)}
                       placeholder="Anything the next person should know about this station" />
              </F>
              </div>
            </div>

            <div style={{ padding: "14px 22px", borderTop: "1px solid #e2e8f0", background: "#fff",
                           display: "flex", justifyContent: "flex-end", gap: 10,
                           borderRadius: "0 0 14px 14px", position: "sticky", bottom: 0 }}>
              <button onClick={() => setModal(false)} disabled={saving} style={{
                padding: "9px 18px", borderRadius: 8, border: "1.5px solid #e2e8f0",
                background: "#fff", fontSize: 13, fontWeight: 600, cursor: "pointer" }}>Cancel</button>
              <button onClick={save} disabled={saving} style={{
                padding: "9px 20px", borderRadius: 8, border: "none", background: "#ca8a04",
                color: "#fff", fontSize: 13, fontWeight: 700, cursor: saving ? "wait" : "pointer" }}>
                {saving ? "Saving…" : "Save station"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
