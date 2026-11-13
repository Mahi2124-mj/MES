// StandardDataPage.jsx — Admin → Production → Standard Data.  2026-11-05.
//
// Per-line standard values for the Control Board sheets (replaces the PEFF
// Details tab here).  First set: Track Record — the cycle time and target the
// Track Record Sheet prints for each line.  One row per line: Cycle Time,
// Target, then Edit and Save.  Saved to /api/standard-data/track-record.
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const card = { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12, padding: 18, marginBottom: 16 };
const th = { padding: "9px 12px", textAlign: "left", fontSize: 11, fontWeight: 800, color: "#475569",
             textTransform: "uppercase", letterSpacing: ".05em", background: "#f8fafc",
             borderBottom: "1px solid #e2e8f0", whiteSpace: "nowrap" };
const td = { padding: "8px 12px", borderBottom: "1px solid #f1f5f9", fontSize: 13, color: "#0f172a", verticalAlign: "middle" };
const inp = (on) => ({ width: 110, padding: "6px 8px", borderRadius: 6, fontSize: 13, fontFamily: "inherit",
                       border: `1px solid ${on ? "#2563eb" : "#e2e8f0"}`, background: on ? "#fff" : "#f8fafc",
                       color: "#0f172a", textAlign: "right" });
const btn = (kind, disabled) => ({
  padding: "6px 14px", borderRadius: 6, fontSize: 12, fontWeight: 700, fontFamily: "inherit",
  cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1, marginLeft: 6,
  ...(kind === "primary" ? { background: "#16a34a", color: "#fff", border: "1px solid #16a34a" }
                         : { background: "#fff", color: "#334155", border: "1px solid #cbd5e1" }),
});
const str = (v) => (v == null ? "" : String(v));
const fmtTime = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
};

function TrackRecordTable({ token, readOnly, say }) {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState("");
  const [edit, setEdit] = useState({});          // line_id -> { cycle_time, target } while editing
  const [busy, setBusy] = useState({});

  const load = useCallback(async () => {
    try {
      const r = await api.get("/api/standard-data/track-record", token);
      setRows(Array.isArray(r) ? r : []); setErr("");
    } catch (e) {
      setErr(/404|Not Found/i.test(String(e && e.message))
        ? "This page becomes active after the next MES-API restart."
        : "Could not load the line list.");
    }
  }, [token]);
  useEffect(() => { load(); }, [load]);   // eslint-disable-line react-hooks/set-state-in-effect

  const groups = useMemo(() => {
    const g = new Map();
    (rows || []).forEach(r => {
      const z = r.zone_name || "No zone";
      if (!g.has(z)) g.set(z, []);
      g.get(z).push(r);
    });
    return [...g.entries()];
  }, [rows]);

  const startEdit = (r) => setEdit(e => ({ ...e, [r.line_id]: { cycle_time: str(r.cycle_time), target: str(r.target) } }));
  const cancel = (id) => setEdit(e => { const n = { ...e }; delete n[id]; return n; });
  const change = (id, k, v) => setEdit(e => ({ ...e, [id]: { ...e[id], [k]: v } }));

  const save = async (r) => {
    const e = edit[r.line_id];
    if (!e) return;
    const num = (v) => (String(v).trim() === "" ? null : Number(v));
    const body = { cycle_time: num(e.cycle_time), target: num(e.target) };
    for (const [k, v] of Object.entries(body)) {
      if (v != null && (!isFinite(v) || v < 0)) { say(`${k === "target" ? "Target" : "Cycle time"} must be a number of seconds`, "error"); return; }
    }
    setBusy(b => ({ ...b, [r.line_id]: true }));
    try {
      const res = await api.put(`/api/standard-data/track-record/${r.line_id}`, body, token);
      setRows(rs => rs.map(x => (x.line_id === r.line_id
        ? { ...x, cycle_time: res.cycle_time, target: res.target, updated_by: res.updated_by, updated_at: res.updated_at }
        : x)));
      cancel(r.line_id);
      say(`${r.line_name}: saved`);
    } catch (ex) {
      say(String((ex && ex.message) || "Save failed"), "error");
    } finally {
      setBusy(b => ({ ...b, [r.line_id]: false }));
    }
  };

  if (err) return <div style={{ ...card, color: "#b45309", background: "#fffbeb", borderColor: "#fde68a" }}>{err}</div>;
  if (!rows) return <div style={{ ...card, color: "#64748b" }}>Loading lines…</div>;

  return (
    <div style={{ ...card, padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "14px 18px", borderBottom: "1px solid #e2e8f0" }}>
        <div style={{ fontSize: 14, fontWeight: 800, color: "#0f172a" }}>Track Record</div>
        <div style={{ fontSize: 12, color: "#64748b" }}>
          Cycle time and target (seconds) for each line, as printed on the Track Record Sheet.
        </div>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={th}>Line</th>
              <th style={th}>Code</th>
              <th style={{ ...th, textAlign: "right" }}>Cycle Time (sec.)</th>
              <th style={{ ...th, textAlign: "right" }}>Target (sec.)</th>
              <th style={th}>Last updated</th>
              {!readOnly && <th style={{ ...th, textAlign: "right" }}>Action</th>}
            </tr>
          </thead>
          <tbody>
            {groups.map(([zone, list]) => [
              <tr key={`z-${zone}`}>
                <td colSpan={readOnly ? 5 : 6} style={{ ...td, background: "#eff6ff", color: "#1e40af", fontWeight: 800,
                                                        fontSize: 12, letterSpacing: ".04em", textTransform: "uppercase" }}>
                  {zone} <span style={{ color: "#64748b", fontWeight: 600 }}>· {list.length}</span>
                </td>
              </tr>,
              ...list.map(r => {
                const e = edit[r.line_id];
                const on = !!e;
                const dirty = on && (e.cycle_time !== str(r.cycle_time) || e.target !== str(r.target));
                return (
                  <tr key={r.line_id}>
                    <td style={{ ...td, fontWeight: 700 }}>{r.line_name}</td>
                    <td style={{ ...td, color: "#64748b" }}>{r.line_code || "—"}</td>
                    <td style={{ ...td, textAlign: "right" }}>
                      <input type="number" min="0" step="0.01" disabled={!on} placeholder="—"
                             value={on ? e.cycle_time : str(r.cycle_time)}
                             onChange={ev => change(r.line_id, "cycle_time", ev.target.value)} style={inp(on)} />
                    </td>
                    <td style={{ ...td, textAlign: "right" }}>
                      <input type="number" min="0" step="0.01" disabled={!on} placeholder="—"
                             value={on ? e.target : str(r.target)}
                             onChange={ev => change(r.line_id, "target", ev.target.value)} style={inp(on)} />
                    </td>
                    <td style={{ ...td, fontSize: 12, color: "#64748b", whiteSpace: "nowrap" }}>
                      {r.updated_at ? `${fmtTime(r.updated_at)}${r.updated_by ? ` · ${r.updated_by}` : ""}` : "—"}
                    </td>
                    {!readOnly && (
                      <td style={{ ...td, textAlign: "right", whiteSpace: "nowrap" }}>
                        {on
                          ? <button onClick={() => cancel(r.line_id)} style={btn("ghost", busy[r.line_id])}
                                    disabled={busy[r.line_id]}>Cancel</button>
                          : <button onClick={() => startEdit(r)} style={btn("ghost", false)}>Edit</button>}
                        <button onClick={() => save(r)} style={btn("primary", !dirty || busy[r.line_id])}
                                disabled={!dirty || busy[r.line_id]}>{busy[r.line_id] ? "Saving…" : "Save"}</button>
                      </td>
                    )}
                  </tr>
                );
              }),
            ])}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function StandardDataPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [open, setOpen] = useState(null);         // which data set is shown below
  const say = useCallback((m, t = "ok") => (toast ? toast(m, t) : alert(m)), [toast]);
  const sets = [{ key: "track-record", label: "Track Record", icon: "🛤" }];

  return (
    <div>
      <div style={card}>
        <div style={{ fontSize: 13, fontWeight: 800, color: "#0f172a", marginBottom: 4 }}>Standard Data</div>
        <div style={{ fontSize: 12, color: "#64748b", marginBottom: 12 }}>
          Standard values used by the Control Board sheets. Select a data set to view or edit it.
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {sets.map(s => (
            <button key={s.key} onClick={() => setOpen(o => (o === s.key ? null : s.key))} style={{
              display: "flex", alignItems: "center", gap: 8, padding: "10px 16px", borderRadius: 10,
              fontSize: 14, fontWeight: 700, cursor: "pointer", fontFamily: "inherit",
              border: `1.5px solid ${open === s.key ? "#2563eb" : "#e2e8f0"}`,
              background: open === s.key ? "#eff6ff" : "#fff", color: open === s.key ? "#1e40af" : "#0f172a",
            }}>
              <span>{s.icon}</span>{s.label}
            </button>
          ))}
        </div>
      </div>
      {open === "track-record" && <TrackRecordTable token={token} readOnly={readOnly} say={say} />}
    </div>
  );
}
