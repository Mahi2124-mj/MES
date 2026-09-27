// ───────────────────────────────────────────────────────────────────────
// FaultHistory.jsx   (/fault-history)   2026-09-27
// ───────────────────────────────────────────────────────────────────────
// Every fault the collectors have recorded from the bits assigned in
// Maintenance → Fault Config.  Those 158 assigned bits were never read by
// anything until now; the line's own collector reads them (it holds the only
// PLC session) and writes one row per fault: rising edge opens it, falling
// edge closes it with a duration.
//
// The page is about HOW OFTEN, not how long (operator 27-Sep: "mujhe fault ki
// timing nhi chahiye blki frequency and occurance chahiye").  So every number
// here counts OCCURRENCES: the Pareto ranks faults by how many times they
// happened, and the frequency strips show occurrences per date and per shift.
// Duration is still recorded in the table, it is simply not what this page
// reports.
//
// Filters: date range, zone, line, machine, fault, shift, still-on/cleared.
// Pareto is computed over the SAME filter, so the table and the chart never
// disagree.  Excel carries three sheets: Occurrences, Pareto, Frequency.
// ───────────────────────────────────────────────────────────────────────
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import PageTopbar from "../components/PageTopbar";

const dt = (v) => v ? new Date(v).toLocaleString("en-GB",
  { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
const todayStr = () => new Date().toISOString().slice(0, 10);

function Kpi({ label, value, tone }) {
  return (
    <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderTop: `3px solid ${tone}`,
                  borderRadius: 10, padding: "12px 14px" }}>
      <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".05em",
                    color: "#64748b" }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 800, color: "#0f172a", marginTop: 2 }}>{value}</div>
    </div>
  );
}

export default function FaultHistory() {
  const { token, canAccessModule } = useAuth();
  const canMod = (m) => (canAccessModule ? canAccessModule("fault-history", m) : true);
  const [f, setF] = useState({
    date_from: new Date(Date.now() - 6 * 864e5).toISOString().slice(0, 10),
    date_to: todayStr(), zone_id: "", line_id: "", machine_id: "",
    fault: "", shift: "", state: "",
  });
  const [d, setD] = useState(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [auto, setAuto] = useState(false);

  const qs = useCallback((pg) => {
    const p = new URLSearchParams({ page: String(pg), page_size: "200" });
    Object.entries(f).forEach(([k, v]) => { if (v !== "" && v != null) p.set(k, v); });
    return p;
  }, [f]);

  const load = useCallback(async (pg = 1) => {
    setBusy(true); setErr("");
    try {
      setD(await api.get(`/api/faults/history?${qs(pg)}`, token));
      setPage(pg);
    } catch (e) {
      setErr(String(e?.message || e).includes("404")
        ? "This page becomes active after the next MES-API restart."
        : (e?.message || "Could not load fault history."));
    }
    setBusy(false);
  }, [qs, token]);

  useEffect(() => { load(1); }, [token]);   // eslint-disable-line
  useEffect(() => {
    if (!auto) return;
    const t = setInterval(() => load(page), 20000);
    return () => clearInterval(t);
  }, [auto, page, load]);

  const download = async () => {
    const p = qs(1); p.delete("page"); p.delete("page_size");
    //  Same relative-URL + bearer pattern the other exports use.
    const jwt = token || sessionStorage.getItem("mes_token") || "";
    const r = await fetch(`/api/faults/history-excel?${p}`,
                          { headers: { Authorization: `Bearer ${jwt}` } });
    if (!r.ok) { setErr("Export failed."); return; }
    const b = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(b);
    a.download = `fault_history_${f.date_from}_${f.date_to}.xlsx`;
    a.click(); URL.revokeObjectURL(a.href);
  };

  const set = (k) => (e) => setF(x => ({
    ...x, [k]: e.target.value,
    ...(k === "zone_id" ? { line_id: "", machine_id: "" } : {}),
    ...(k === "line_id" ? { machine_id: "" } : {}),
  }));

  const zones = useMemo(() => {
    const m = new Map();
    for (const l of (d?.lines || [])) if (l.zone_id) m.set(String(l.zone_id), l.zone_name);
    return [...m.entries()];
  }, [d]);
  const lines = useMemo(() => (d?.lines || [])
    .filter(l => !f.zone_id || String(l.zone_id) === f.zone_id), [d, f.zone_id]);
  const machines = useMemo(() => (d?.machines || [])
    .filter(m => !f.line_id || String(m.line_id) === f.line_id), [d, f.line_id]);

  const k = d?.kpis || {};
  const pareto = d?.pareto || [];
  const maxHits = pareto.length ? pareto[0].hits || 1 : 1;
  const byDate = d?.by_date || [];
  const byShift = d?.by_shift || [];
  const maxDay = byDate.reduce((m, r) => Math.max(m, r.hits), 1);
  const pages = Math.max(1, Math.ceil((d?.total || 0) / 200));

  const inp = { padding: "6px 9px", border: "1px solid #cbd5e1", borderRadius: 7, fontSize: 12.5 };
  const lbl = { display: "grid", gap: 3, fontSize: 10.5, fontWeight: 700, color: "#64748b" };
  const th = { textAlign: "left", padding: "9px 10px", borderBottom: "1px solid #e2e8f0",
               fontSize: 11, textTransform: "uppercase", color: "#64748b", whiteSpace: "nowrap" };
  const td = { padding: "8px 10px", verticalAlign: "top" };

  return (
    <div style={{ padding: 16, maxWidth: 1360, margin: "0 auto" }}>
      <PageTopbar leading="Fault" accent="History" />
      <div style={{ color: "#64748b", fontSize: 13, margin: "2px 0 14px" }}>
        Faults recorded from the bits assigned in Maintenance → Fault Config. A fault opens
        when its bit turns on and closes when it turns off, so the duration is the real time
        the machine held that fault.
      </div>

      {err && <div style={{ background: "#fee2e2", color: "#b91c1c", padding: "10px 14px",
                            borderRadius: 8, marginBottom: 12, fontSize: 13 }}>{err}</div>}

      <div style={{ display: "grid", gap: 10, marginBottom: 14,
                    gridTemplateColumns: "repeat(auto-fill,minmax(150px,1fr))" }}>
        <Kpi label="Occurrences" value={k.occurrences ?? "—"} tone="#1e40af" />
        <Kpi label="Per day" value={k.per_day ?? "—"} tone="#0891b2" />
        <Kpi label="Distinct faults" value={k.distinct_faults ?? "—"} tone="#7c3aed" />
        <Kpi label="Still on now" value={k.open ?? "—"} tone={k.open ? "#b91c1c" : "#15803d"} />
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end",
                    marginBottom: 14 }}>
        <label style={lbl}>FROM<input type="date" value={f.date_from} max={f.date_to}
                                      onChange={set("date_from")} style={inp} /></label>
        <label style={lbl}>TO<input type="date" value={f.date_to} min={f.date_from}
                                    onChange={set("date_to")} style={inp} /></label>
        <label style={lbl}>ZONE
          <select value={f.zone_id} onChange={set("zone_id")} style={{ ...inp, minWidth: 130 }}>
            <option value="">All zones</option>
            {zones.map(([id, nm]) => <option key={id} value={id}>{nm}</option>)}
          </select></label>
        <label style={lbl}>LINE
          <select value={f.line_id} onChange={set("line_id")} style={{ ...inp, minWidth: 150 }}>
            <option value="">All lines</option>
            {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
          </select></label>
        <label style={lbl}>MACHINE
          <select value={f.machine_id} onChange={set("machine_id")} style={{ ...inp, minWidth: 170 }}>
            <option value="">All machines</option>
            {machines.map(m => <option key={m.machine_id} value={m.machine_id}>{m.machine_name}</option>)}
          </select></label>
        <label style={lbl}>FAULT
          <select value={f.fault} onChange={set("fault")} style={{ ...inp, minWidth: 200 }}>
            <option value="">All faults</option>
            {(d?.faults || []).map(x => <option key={x} value={x}>{x}</option>)}
          </select></label>
        <label style={lbl}>SHIFT
          <select value={f.shift} onChange={set("shift")} style={inp}>
            <option value="">All</option><option value="A">A</option><option value="B">B</option>
          </select></label>
        <label style={lbl}>STATE
          <select value={f.state} onChange={set("state")} style={inp}>
            <option value="">All</option><option value="open">Still on</option>
            <option value="closed">Cleared</option>
          </select></label>
        <button onClick={() => load(1)} disabled={busy}
                style={{ background: "#1e40af", color: "#fff", border: "none", borderRadius: 7,
                         padding: "8px 18px", fontSize: 13, fontWeight: 700, cursor: "pointer" }}>
          {busy ? "Loading…" : "Load"}</button>
        <button onClick={download} style={{ ...inp, cursor: "pointer", fontWeight: 600 }}>⬇ Excel</button>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5,
                        color: "#64748b", paddingBottom: 6 }}>
          <input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} />
          Auto-refresh
        </label>
      </div>

      {canMod("pareto") && pareto.length > 0 && (
        <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10,
                      padding: 14, marginBottom: 14 }}>
          <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 2 }}>
            Pareto — which faults happen most often</div>
          <div style={{ fontSize: 12, color: "#64748b", marginBottom: 10 }}>
            Same filter as the table below. <b>{pareto.findIndex(p => p.cum_pct >= 80) + 1 || pareto.length}</b>
            {" "}of {pareto.length} faults account for 80% of all occurrences.
          </div>
          <div style={{ display: "flex", gap: 10, fontSize: 10.5, fontWeight: 700,
                        color: "#94a3b8", textTransform: "uppercase", marginBottom: 4 }}>
            <div style={{ width: 280 }}>Fault</div><div style={{ flex: 1 }} />
            <div style={{ width: 58, textAlign: "right" }}>Times</div>
            <div style={{ width: 52, textAlign: "right" }}>Share</div>
            <div style={{ width: 58, textAlign: "right" }}>Cum</div>
            <div style={{ width: 66, textAlign: "right" }}>Per day</div>
            <div style={{ width: 58, textAlign: "right" }}>Days</div>
          </div>
          {pareto.slice(0, 15).map(p => (
            <div key={p.fault_name} style={{ display: "flex", alignItems: "center", gap: 10,
                                             marginBottom: 6, fontSize: 12.5 }}>
              <div style={{ width: 280, overflow: "hidden", textOverflow: "ellipsis",
                            whiteSpace: "nowrap" }} title={p.fault_name}>{p.fault_name}</div>
              <div style={{ flex: 1, background: "#f1f5f9", borderRadius: 4, height: 16 }}>
                <div style={{ width: `${Math.max(2, 100 * p.hits / maxHits)}%`, height: "100%",
                              background: "#1e40af", borderRadius: 4 }} />
              </div>
              <div style={{ width: 58, textAlign: "right", fontWeight: 800 }}>{p.hits}</div>
              <div style={{ width: 52, textAlign: "right", color: "#64748b" }}>{p.pct}%</div>
              <div style={{ width: 58, textAlign: "right", color: "#94a3b8" }}>Σ {p.cum_pct}%</div>
              <div style={{ width: 66, textAlign: "right", color: "#0891b2", fontWeight: 700 }}>
                {p.per_day_seen}</div>
              <div style={{ width: 58, textAlign: "right", color: "#64748b" }}>{p.on_days}</div>
            </div>))}
        </div>)}

      {canMod("pareto") && byDate.length > 0 && (
        <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10,
                      padding: 14, marginBottom: 14 }}>
          <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>
            How often — occurrences per day</div>
          <div style={{ display: "flex", gap: 3, alignItems: "flex-end", height: 90,
                        overflowX: "auto", paddingBottom: 4 }}>
            {byDate.map(r => (
              <div key={r.date} title={`${r.date} — ${r.hits}`}
                   style={{ minWidth: 26, display: "flex", flexDirection: "column",
                            alignItems: "center", gap: 3 }}>
                <div style={{ fontSize: 10.5, fontWeight: 700, color: "#0f172a" }}>{r.hits}</div>
                <div style={{ width: 20, height: `${Math.max(3, 62 * r.hits / maxDay)}px`,
                              background: "#1e40af", borderRadius: "3px 3px 0 0" }} />
                <div style={{ fontSize: 9.5, color: "#94a3b8" }}>{r.date.slice(5)}</div>
              </div>))}
          </div>
          {byShift.length > 0 && (
            <div style={{ marginTop: 10, display: "flex", gap: 14, flexWrap: "wrap",
                          fontSize: 12.5, color: "#64748b" }}>
              <b style={{ color: "#0f172a" }}>By shift:</b>
              {byShift.map(r => (
                <span key={r.shift}>shift {r.shift} — <b style={{ color: "#0f172a" }}>{r.hits}</b></span>))}
            </div>)}
        </div>)}

      {canMod("table") && (
      <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 10,
                    background: "#fff" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5, minWidth: 1040 }}>
          <thead><tr style={{ background: "#f8fafc" }}>
            {["Occurred at", "Zone / line", "Machine", "Fault", "Bit",
              "Date", "Shift", "State"].map(h => <th key={h} style={th}>{h}</th>)}
          </tr></thead>
          <tbody>
            {(d?.rows || []).length === 0 && !busy && (
              <tr><td colSpan={8} style={{ padding: 24, textAlign: "center", color: "#94a3b8" }}>
                No fault recorded in this range.
              </td></tr>)}
            {(d?.rows || []).map(r => (
              <tr key={r.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                <td style={td}>{dt(r.started_at)}</td>
                <td style={td}><b>{r.line_name || r.line_id}</b>
                  <div style={{ color: "#94a3b8", fontSize: 11 }}>{r.zone_name}</div></td>
                <td style={td}>{r.machine_name || r.machine_id}</td>
                <td style={{ ...td, maxWidth: 300 }}>{r.fault_name}</td>
                <td style={{ ...td, fontFamily: "monospace" }}>{r.address}</td>
                <td style={td}>{r.record_date || "—"}</td>
                <td style={td}>{r.shift_name || "—"}</td>
                <td style={td}>{r.ended_at
                  ? <span style={{ color: "#15803d" }}>cleared</span>
                  : <span style={{ color: "#b91c1c", fontWeight: 700 }}>STILL ON</span>}</td>
              </tr>))}
          </tbody>
        </table>
      </div>)}

      {pages > 1 && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10, fontSize: 12.5 }}>
          <button onClick={() => load(page - 1)} disabled={page <= 1 || busy}
                  style={{ ...inp, cursor: "pointer" }}>‹ Prev</button>
          <span style={{ color: "#64748b" }}>Page {page} of {pages} · {d?.total} occurrences</span>
          <button onClick={() => load(page + 1)} disabled={page >= pages || busy}
                  style={{ ...inp, cursor: "pointer" }}>Next ›</button>
        </div>)}
    </div>
  );
}
