// ───────────────────────────────────────────────────────────────────────
// FaultConfigPanel.jsx   (Maintenance → Fault Config)
// ───────────────────────────────────────────────────────────────────────
// Phase 1 — CONFIG ONLY. Define a per-machine fault list, picked by
// Zone → Line → Machine (mirrors how poka-yoke / model mapping is set up).
// Each fault is a BIT or a DATA REGISTER on the machine's PLC, plus the value
// that means "this fault is active".
//
// Phase 2 (later) will have the collector READ these when a machine's NG bit
// turns on, and log detected faults to a new Fault History page. This screen
// only stores the configuration (table mes_fault_config, /api/faults).
// ───────────────────────────────────────────────────────────────────────
import { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const lbl = { fontSize: 11, fontWeight: 700, color: "#475569",
              textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 4, display: "block" };
const inp = { padding: "7px 10px", fontSize: 13, borderRadius: 6,
              border: "1px solid #cbd5e1", background: "#fff", color: "#0f172a",
              width: "100%", boxSizing: "border-box" };
const btn = { background: "#dc2626", color: "#fff", border: "none", borderRadius: 8,
              padding: "9px 20px", fontSize: 13, fontWeight: 700, cursor: "pointer" };
const th  = { textAlign: "left", padding: "8px 10px", fontSize: 11, fontWeight: 700,
              color: "#64748b", textTransform: "uppercase", borderBottom: "1px solid #e2e8f0" };
const td  = { padding: "6px 10px", borderBottom: "1px solid #f1f5f9", verticalAlign: "middle" };

const EMPTY_FAULT = { fault_name: "", source_type: "bit", address: "", trigger_value: 1, is_active: true };

export default function FaultConfigPanel({ toast }) {
  const { token } = useAuth();
  const say = toast || (() => {});

  const [zones, setZones] = useState([]);
  const [lines, setLines] = useState([]);
  const [machines, setMachines] = useState([]);

  const [zoneId, setZoneId] = useState("");
  const [lineId, setLineId] = useState("");
  const [machineId, setMachineId] = useState("");

  const [faults, setFaults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  // zones + lines once
  useEffect(() => {
    api.get("/api/zones/", token).then(d => setZones(Array.isArray(d) ? d : (d.data || d.zones || []))).catch(() => {});
    api.get("/api/lines/", token).then(d => setLines(Array.isArray(d) ? d : (d.data || d.lines || []))).catch(() => {});
  }, [token]);

  const shownLines = zoneId ? lines.filter(l => String(l.zone_id) === String(zoneId)) : lines;

  // machines for the chosen line
  useEffect(() => {
    setMachines([]); setMachineId(""); setFaults([]);
    if (!lineId) return;
    api.get(`/api/lines/${lineId}/machines`, token)
      .then(d => setMachines(Array.isArray(d) ? d : []))
      .catch(() => setMachines([]));
  }, [lineId, token]);

  // faults for the chosen machine
  const loadFaults = useCallback((mid) => {
    if (!mid) { setFaults([]); return; }
    setLoading(true);
    api.get(`/api/faults/config/${mid}`, token)
      .then(d => setFaults((d && d.faults ? d.faults : []).map(f => ({
        fault_name: f.fault_name || "",
        source_type: f.source_type || "bit",
        address: f.address || "",
        trigger_value: f.trigger_value == null ? "" : f.trigger_value,
        is_active: f.is_active !== false,
      }))))
      .catch(() => setFaults([]))
      .finally(() => setLoading(false));
  }, [token]);
  useEffect(() => { loadFaults(machineId); }, [machineId, loadFaults]);

  const setRow = (i, patch) => setFaults(fs => fs.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  const addRow = () => setFaults(fs => [...fs, { ...EMPTY_FAULT }]);
  const delRow = (i) => setFaults(fs => fs.filter((_, j) => j !== i));

  const machineObj = machines.find(m => String(m.id) === String(machineId));

  const save = async () => {
    if (!machineId) { say("Pick a machine first", "err"); return; }
    const clean = faults
      .filter(f => (f.fault_name || "").trim() && (f.address || "").trim())
      .map(f => ({
        fault_name: f.fault_name.trim(),
        source_type: f.source_type === "register" ? "register" : "bit",
        address: f.address.trim(),
        trigger_value: f.trigger_value === "" || f.trigger_value == null ? null : Number(f.trigger_value),
        is_active: f.is_active !== false,
      }));
    setSaving(true);
    try {
      await api.post(`/api/faults/config/${machineId}`, {
        zone_id: zoneId ? Number(zoneId) : (machineObj && machineObj.zone_id) || null,
        line_id: lineId ? Number(lineId) : null,
        machine_name: machineObj ? (machineObj.machine_name || `M${machineId}`) : null,
        faults: clean,
      }, token);
      say(`Saved ${clean.length} fault(s)`, "ok");
      loadFaults(machineId);
    } catch (e) {
      say(e.message || "Save failed", "err");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ padding: 4 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 4 }}>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 800, color: "#0f172a" }}>Fault Config</h2>
        <span style={{ fontSize: 12, color: "#64748b" }}>
          Per-machine fault list — read on NG bit (like Poka-Yoke). Bit or data register.
        </span>
      </div>

      {/* selectors */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(160px, 1fr))",
                    gap: 12, marginBottom: 14, maxWidth: 720 }}>
        <div>
          <label style={lbl}>Zone</label>
          <select style={inp} value={zoneId} onChange={e => { setZoneId(e.target.value); setLineId(""); }}>
            <option value="">All zones</option>
            {zones.map(z => <option key={z.id} value={z.id}>{z.zone_name || z.name || `Zone ${z.id}`}</option>)}
          </select>
        </div>
        <div>
          <label style={lbl}>Line</label>
          <select style={inp} value={lineId} onChange={e => setLineId(e.target.value)}>
            <option value="">Select line…</option>
            {shownLines.map(l => <option key={l.id} value={l.id}>{l.line_name || l.line_code || "—"}</option>)}
          </select>
        </div>
        <div>
          <label style={lbl}>Machine</label>
          <select style={inp} value={machineId} onChange={e => setMachineId(e.target.value)} disabled={!lineId}>
            <option value="">{lineId ? "Select machine…" : "Pick a line first"}</option>
            {machines.map(m => <option key={m.id} value={m.id}>{m.machine_name || `M${m.id}`}</option>)}
          </select>
        </div>
      </div>

      {/* fault list */}
      {!machineId ? (
        <div style={{ padding: 24, color: "#64748b", fontSize: 13,
                      background: "#f8fafc", border: "1px dashed #cbd5e1", borderRadius: 10 }}>
          Select a Zone → Line → Machine to configure its fault list.
        </div>
      ) : loading ? (
        <div style={{ padding: 24, color: "#64748b" }}>Loading…</div>
      ) : (
        <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10, overflow: "hidden" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead style={{ background: "#f8fafc" }}>
              <tr>
                <th style={{ ...th, width: 34 }}>#</th>
                <th style={th}>Fault name</th>
                <th style={{ ...th, width: 130 }}>Type</th>
                <th style={{ ...th, width: 150 }}>PLC address</th>
                <th style={{ ...th, width: 130 }}>Value</th>
                <th style={{ ...th, width: 50 }}></th>
              </tr>
            </thead>
            <tbody>
              {faults.length === 0 && (
                <tr><td colSpan={6} style={{ padding: 18, textAlign: "center", color: "#94a3b8" }}>
                  No faults yet — click “+ Add fault”.
                </td></tr>
              )}
              {faults.map((f, i) => (
                <tr key={i}>
                  <td style={{ ...td, color: "#94a3b8", fontWeight: 700 }}>{i + 1}</td>
                  <td style={td}>
                    <input style={inp} value={f.fault_name} placeholder="e.g. Sensor 1 not detected"
                           onChange={e => setRow(i, { fault_name: e.target.value })} />
                  </td>
                  <td style={td}>
                    <select style={inp} value={f.source_type}
                            onChange={e => setRow(i, { source_type: e.target.value })}>
                      <option value="bit">Bit</option>
                      <option value="register">Data register</option>
                    </select>
                  </td>
                  <td style={td}>
                    <input style={inp} value={f.address}
                           placeholder={f.source_type === "register" ? "e.g. D200" : "e.g. M120"}
                           onChange={e => setRow(i, { address: e.target.value })} />
                  </td>
                  <td style={td}>
                    <input style={inp} type="number" value={f.trigger_value}
                           placeholder={f.source_type === "register" ? "code #" : "ON = 1"}
                           onChange={e => setRow(i, { trigger_value: e.target.value })} />
                  </td>
                  <td style={{ ...td, textAlign: "center" }}>
                    <button onClick={() => delRow(i)} title="Remove"
                            style={{ background: "none", border: "none", cursor: "pointer",
                                     color: "#ef4444", fontSize: 18, lineHeight: 1 }}>×</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ display: "flex", gap: 10, padding: 12, borderTop: "1px solid #f1f5f9" }}>
            <button onClick={addRow}
                    style={{ ...btn, background: "#fff", color: "#dc2626", border: "1px solid #dc2626" }}>
              + Add fault
            </button>
            <button onClick={save} disabled={saving} style={{ ...btn, marginLeft: "auto" }}>
              {saving ? "Saving…" : "Save fault list"}
            </button>
          </div>
        </div>
      )}

      <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 10, maxWidth: 720 }}>
        <b>Bit</b>: address of a PLC bit; Value = the ON state (usually 1). &nbsp;
        <b>Data register</b>: address of a word (e.g. D200); Value = the code in that register that means this fault.
        On an NG bit these are read and matched (Phase 2 — Fault History).
      </div>
    </div>
  );
}
