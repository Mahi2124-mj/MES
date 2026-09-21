// pages/AndonHistory.jsx — Andon History (light theme, read-only)
//   Reads the PHYSICAL Andon system's call log from maintenance_db.andon_history
//   via /api/andon/*.  Filterable by date / zone / line / call-type / priority,
//   with summary KPIs (total calls, avg response, avg + total downtime) and a
//   type-wise breakdown.  This is NOT the MES breakdown "Andon" — it is the
//   standalone Andon PLC service's history.
import { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import PageTopbar from "../components/PageTopbar";
import AIAssistant from "../components/AIAssistant";

const iso = (d) => d.toISOString().slice(0, 10);
const today = () => iso(new Date());
const weekAgo = () => { const d = new Date(); d.setDate(d.getDate() - 7); return iso(d); };

// seconds → compact human string
const dur = (s) => {
  if (s == null) return "—";
  s = Number(s);
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60), r = s % 60;
  if (m < 60) return `${m}m ${r}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
};

// live ticking clock: seconds → m:ss / h:mm:ss
const hms = (s) => {
  s = Math.max(0, Math.floor(Number(s) || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const p = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${p(m)}:${p(sec)}` : `${m}:${p(sec)}`;
};

const PRIO = {
  Critical: { bg: "#fee2e2", fg: "#b91c1c", dot: "#dc2626" },
  High:     { bg: "#ffedd5", fg: "#c2410c", dot: "#ea580c" },
  Normal:   { bg: "#e0f2fe", fg: "#0369a1", dot: "#0284c7" },
};

export default function AndonHistory() {
  const { token } = useAuth();
  const [opts,   setOpts]   = useState({ zones: [], lines: [], types: [], priorities: [] });
  const [f, setF] = useState({ from: weekAgo(), to: today(), zone: "", line: "", type: "", priority: "" });
  const [data,   setData]   = useState({ rows: [], summary: {}, by_type: [], by_priority: [] });
  const [loading, setLoading] = useState(false);
  const [err,    setErr]    = useState(null);
  const [active, setActive] = useState({ rows: [], count: 0 });
  const [nowTs,  setNowTs]  = useState(Date.now());

  useEffect(() => {
    api.get("/api/andon/options", token).then(setOpts).catch(() => {});
  }, [token]);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const q = new URLSearchParams();
      if (f.from)     q.set("from", f.from);
      if (f.to)       q.set("to", f.to);
      if (f.zone)     q.set("zone", f.zone);
      if (f.line)     q.set("line", f.line);
      if (f.type)     q.set("type", f.type);
      if (f.priority) q.set("priority", f.priority);
      setData(await api.get(`/api/andon/history?${q.toString()}`, token));
    } catch (e) { setErr(e.message || "Failed to load"); setData({ rows: [], summary: {}, by_type: [], by_priority: [] }); }
    finally { setLoading(false); }
  }, [f, token]);
  useEffect(() => { load(); }, [load]);

  const loadActive = useCallback(async () => {
    try { setActive(await api.get("/api/andon/active", token)); } catch { /* keep last */ }
  }, [token]);
  useEffect(() => { loadActive(); }, [loadActive]);

  // Auto-refresh: pull the live open calls + the history every 10s.
  useEffect(() => {
    const t = setInterval(() => { loadActive(); load(); }, 10000);
    return () => clearInterval(t);
  }, [loadActive, load]);

  // 1s ticker so the live "elapsed" clocks count up between polls.
  useEffect(() => {
    const t = setInterval(() => setNowTs(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const s = data.summary || {};
  const set = (k) => (e) => setF(p => ({ ...p, [k]: e.target.value }));

  return (
    <div style={{ padding: "18px 22px 60px", color: "#0f172a" }}>
      <PageTopbar leading="Andon" accent="History" />
      <style>{`@keyframes andonPulse{0%,100%{opacity:1}50%{opacity:.3}}`}</style>

      {/* LIVE — andon calls open right now */}
      <div style={{ border: `1px solid ${active.count ? "#fecaca" : "#e5e7eb"}`, borderRadius: 14,
        background: active.count ? "#fff1f2" : "#f8fafc", padding: "12px 16px", margin: "14px 0 6px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: active.count ? 10 : 0 }}>
          <span style={{ width: 10, height: 10, borderRadius: 999,
            background: active.count ? "#dc2626" : "#94a3b8",
            animation: active.count ? "andonPulse 1.2s ease-in-out infinite" : "none" }} />
          <span style={{ fontWeight: 800, fontSize: 14, color: active.count ? "#b91c1c" : "#475569" }}>LIVE ANDON</span>
          <span style={{ fontSize: 13, color: "#64748b" }}>
            {active.count ? `${active.count} call${active.count > 1 ? "s" : ""} open now` : "No active calls right now"}</span>
          <span style={{ marginLeft: "auto", fontSize: 11, color: "#94a3b8" }}>auto-refresh 10s</span>
        </div>
        {active.count > 0 && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(280px,1fr))", gap: 10 }}>
            {active.rows.map((r) => {
              const pc = PRIO[r.priority] || { bg: "#f1f5f9", fg: "#475569", dot: "#94a3b8" };
              const elapsed = Math.floor((nowTs - new Date(r.started_at).getTime()) / 1000);
              const ackd = !!r.acknowledged_at;
              return (
                <div key={r.id} style={{ background: "#fff", border: `1px solid ${pc.bg}`,
                  borderLeft: `4px solid ${pc.dot}`, borderRadius: 10, padding: "10px 12px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                    <b style={{ fontSize: 14 }}>{r.line || "—"}
                      <span style={{ color: "#94a3b8", fontWeight: 500 }}> · {r.machine_no || ""}</span></b>
                    <span style={{ fontVariantNumeric: "tabular-nums", fontWeight: 800, fontSize: 16, color: "#b91c1c" }}>{hms(elapsed)}</span>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
                    <span style={{ background: pc.bg, color: pc.fg, borderRadius: 999, padding: "2px 10px", fontSize: 12, fontWeight: 700 }}>{r.display_name || "—"}</span>
                    <span style={{ fontSize: 12, color: pc.fg, fontWeight: 700 }}>{r.priority}</span>
                    {r.model && <span style={{ fontSize: 12, color: "#94a3b8" }}>· {r.model}</span>}
                  </div>
                  <div style={{ marginTop: 6, fontSize: 12, color: ackd ? "#16a34a" : "#b45309", fontWeight: 600 }}>
                    {ackd ? `✓ Acknowledged in ${dur(r.response_seconds)}` : "● Waiting for acknowledgement"}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* filters */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end", margin: "14px 0 16px" }}>
        <Field label="From"><input type="date" value={f.from} onChange={set("from")} style={inp} /></Field>
        <Field label="To"><input type="date" value={f.to} onChange={set("to")} style={inp} /></Field>
        <Field label="Zone"><Sel value={f.zone} onChange={set("zone")} opts={opts.zones} all="All zones" /></Field>
        <Field label="Line"><Sel value={f.line} onChange={set("line")} opts={opts.lines} all="All lines" /></Field>
        <Field label="Call type"><Sel value={f.type} onChange={set("type")} opts={opts.types} all="All types" /></Field>
        <Field label="Priority"><Sel value={f.priority} onChange={set("priority")} opts={opts.priorities} all="All" /></Field>
        <button onClick={load} style={btn}>↻ Refresh</button>
      </div>

      {/* KPIs */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
        <KPI label="Total calls" value={s.total ?? "—"} />
        <KPI label="Avg response" value={dur(s.avg_response)} accent="#0369a1" />
        <KPI label="Avg downtime" value={dur(s.avg_duration)} accent="#b45309" />
        <KPI label="Longest" value={dur(s.max_duration)} accent="#b91c1c" />
        <KPI label="Total downtime" value={dur(s.total_duration)} accent="#b45309" />
      </div>

      {/* by-type chips */}
      {(data.by_type || []).length > 0 && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 18 }}>
          {data.by_type.map((t, i) => (
            <div key={i} style={{ display: "inline-flex", alignItems: "center", gap: 8, background: "#f8fafc",
              border: "1px solid #e5e7eb", borderRadius: 999, padding: "6px 14px", fontSize: 13 }}>
              <b>{t.name || "—"}</b>
              <span style={{ color: "#64748b" }}>{t.n} calls</span>
              <span style={{ color: "#94a3b8" }}>· resp {dur(t.avg_response)} · down {dur(t.total_duration)}</span>
            </div>
          ))}
        </div>
      )}

      {err && <div style={{ color: "#b91c1c", background: "#fee2e2", border: "1px solid #fecaca",
        borderRadius: 10, padding: "10px 14px", marginBottom: 14, fontSize: 13 }}>{err}</div>}

      {/* table */}
      <div style={{ overflowX: "auto", border: "1px solid #e5e7eb", borderRadius: 14, background: "#fff" }}>
        <table style={tbl}>
          <thead><tr>
            {["Started", "Zone", "Line", "Machine", "Model", "Call type", "Priority", "Fault", "Response", "Downtime", "Ended"].map(h =>
              <th key={h} style={th}>{h}</th>)}
          </tr></thead>
          <tbody>
            {loading && <tr><td style={td} colSpan={11}><span style={{ color: "#94a3b8" }}>Loading…</span></td></tr>}
            {!loading && (data.rows || []).map((r) => {
              const pc = PRIO[r.priority] || { bg: "#f1f5f9", fg: "#475569", dot: "#94a3b8" };
              return (
                <tr key={r.id} style={{ borderTop: "1px solid #eef2f7" }}>
                  <td style={{ ...td, whiteSpace: "nowrap" }}>{r.started_at ? new Date(r.started_at).toLocaleString() : "—"}</td>
                  <td style={td}>{r.zone || "—"}</td>
                  <td style={{ ...td, fontWeight: 600 }}>{r.line || "—"}</td>
                  <td style={td}>{r.machine_no || "—"}</td>
                  <td style={td}>{r.model || "—"}</td>
                  <td style={td}>{r.display_name || "—"}</td>
                  <td style={td}>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, background: pc.bg, color: pc.fg,
                      borderRadius: 999, padding: "2px 10px", fontSize: 12, fontWeight: 700 }}>
                      <span style={{ width: 7, height: 7, borderRadius: 999, background: pc.dot }} />{r.priority || "—"}</span>
                  </td>
                  <td style={{ ...td, maxWidth: 220, color: "#334155" }}>{r.fault || <span style={{ color: "#cbd5e1" }}>—</span>}</td>
                  <td style={{ ...td, whiteSpace: "nowrap", color: "#0369a1", fontWeight: 600 }}>{dur(r.response_seconds)}</td>
                  <td style={{ ...td, whiteSpace: "nowrap", color: r.duration_seconds ? "#b45309" : "#94a3b8", fontWeight: 600 }}>{dur(r.duration_seconds)}</td>
                  <td style={{ ...td, whiteSpace: "nowrap" }}>{r.ended_at ? new Date(r.ended_at).toLocaleString() : <span style={{ color: "#16a34a", fontWeight: 600 }}>ACTIVE</span>}</td>
                </tr>
              );
            })}
            {!loading && (data.rows || []).length === 0 && !err &&
              <tr><td style={td} colSpan={11}><span style={{ color: "#94a3b8" }}>No Andon calls for these filters.</span></td></tr>}
          </tbody>
        </table>
      </div>
      <AIAssistant pageContext={{ page: "Andon History" }} />
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ fontSize: 11, fontWeight: 700, color: "#64748b" }}>{label}</span>
      {children}
    </div>
  );
}
function Sel({ value, onChange, opts, all }) {
  return (
    <select value={value} onChange={onChange} style={inp}>
      <option value="">{all}</option>
      {(opts || []).map(o => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}
function KPI({ label, value, accent }) {
  return (
    <div style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 14, padding: "12px 18px", minWidth: 130 }}>
      <div style={{ fontSize: 12, color: "#64748b", fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: accent || "#0f172a", marginTop: 2 }}>{value}</div>
    </div>
  );
}

const inp = { background: "#fff", color: "#0f172a", border: "1px solid #cbd5e1",
  borderRadius: 8, padding: "8px 12px", fontSize: 14, minWidth: 130 };
const btn = { background: "#2563eb", color: "#fff", border: "1px solid #2563eb",
  borderRadius: 8, padding: "9px 18px", fontSize: 14, fontWeight: 700, cursor: "pointer" };
const tbl = { width: "100%", borderCollapse: "collapse", fontSize: 13 };
const th = { padding: "10px 12px", fontWeight: 600, fontSize: 12, color: "#64748b",
  textAlign: "left", background: "#f8fafc", borderBottom: "1px solid #e5e7eb", whiteSpace: "nowrap" };
const td = { padding: "9px 12px", color: "#0f172a" };
