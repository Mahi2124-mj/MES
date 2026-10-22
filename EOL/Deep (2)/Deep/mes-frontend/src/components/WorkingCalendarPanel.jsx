// components/WorkingCalendarPanel.jsx — Admin → Production → Annual Calendar (2026-10-08)
//   The company FY working calendar, imported from D-QMP, with MES's own
//   per-day edits on top (a non-working day that the plant decides to run, or
//   the other way round).  The same calendar decides Holiday / Regular on the
//   PEFF sheet and the "last working day" on Plant Overview.
//   Who can edit comes from the server (admin, plant head, production incharge).
import { useEffect, useState, useCallback, useMemo } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
// one colour per kind of day — legend order = how often people look for it
const KIND = {
  regular:        { bg: "#ffffff", fg: "#0f172a", label: "Working day" },
  weekly_off:     { bg: "#e2e8f0", fg: "#475569", label: "Weekly off" },
  holiday:        { bg: "#fee2e2", fg: "#b91c1c", label: "Holiday" },
  additional:     { bg: "#ffe4e6", fg: "#be123c", label: "Additional holiday" },
  shutdown:       { bg: "#ffedd5", fg: "#c2410c", label: "Plant shutdown" },
  sunday_working: { bg: "#dcfce7", fg: "#15803d", label: "Sunday working" },
  working_day:    { bg: "#dcfce7", fg: "#15803d", label: "Working day (calendar)" },
  event:          { bg: "#dbeafe", fg: "#1d4ed8", label: "Company event" },
  mes_working:    { bg: "#bbf7d0", fg: "#14532d", label: "Working (MES edit)" },
  mes_off:        { bg: "#fecaca", fg: "#7f1d1d", label: "Non-working (MES edit)" },
};
const fyOf = (d) => { const y = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1; return `${y}-${String((y + 1) % 100).padStart(2, "0")}`; };
const longDay = (iso) => new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" });

export default function WorkingCalendarPanel({ toast }) {
  const { token } = useAuth();
  const say = useCallback((m, t = "ok") => (toast ? toast(m, t) : window.alert(m)), [toast]);
  const [fy, setFy] = useState(fyOf(new Date()));
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(null);         // selected ISO date
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api.get(`/api/working-calendar?fy=${encodeURIComponent(fy)}`, token);
      setData(d); setErr("");
    } catch (e) { setErr(e.message || "Could not load the calendar"); }
  }, [fy, token]);
  useEffect(() => { load(); }, [load]);

  const byDate = useMemo(() => Object.fromEntries((data?.days || []).map(d => [d.date, d])), [data]);
  const months = useMemo(() => {
    if (!data?.period) return [];
    const out = [];
    const s = new Date(data.period.start + "T00:00:00");
    for (let i = 0; i < 12; i++) out.push(new Date(s.getFullYear(), s.getMonth() + i, 1));
    return out;
  }, [data]);
  const day = sel ? byDate[sel] : null;

  const save = async (working) => {
    setBusy(true);
    try {
      await api.put("/api/working-calendar/day", { date: sel, working, reason }, token);
      say(`${longDay(sel)} is now ${working ? "a working day" : "a non-working day"}`);
      setReason(""); await load();
    } catch (e) { say(e.message || "Could not save", "error"); }
    finally { setBusy(false); }
  };
  const undo = async () => {
    setBusy(true);
    try {
      await api.delete(`/api/working-calendar/day?date=${sel}`, token);
      say(`${longDay(sel)} follows the D-QMP calendar again`);
      await load();
    } catch (e) { say(e.message || "Could not undo", "error"); }
    finally { setBusy(false); }
  };
  const reimport = async () => {
    setBusy(true);
    try {
      const r = await api.post(`/api/working-calendar/import?fy=${encodeURIComponent(fy)}`, {}, token);
      say(`Imported from D-QMP: ${Object.entries(r.imported || {}).map(([k, v]) => `${k} (${v} entries)`).join(", ")}. MES edits kept.`);
      await load();
    } catch (e) { say(e.message || "Import failed", "error"); }
    finally { setBusy(false); }
  };

  const hdr = data?.header;
  const fys = [...new Set([...(data?.all_fys || []), fy])].sort();
  const btn = (bg, fg, border) => ({ background: bg, color: fg, border: `1px solid ${border}`, borderRadius: 8,
                                     padding: "7px 12px", fontWeight: 700, fontSize: 12.5, cursor: busy ? "wait" : "pointer" });

  return (
    <div style={{ color: "#0f172a" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 800 }}>Annual Calendar</div>
          <div style={{ fontSize: 12.5, color: "#64748b" }}>
            {hdr ? hdr.title : `FY ${fy}`} · imported from D-QMP · decides Holiday / Regular on the PEFF sheet and the last working day on Plant Overview
          </div>
        </div>
        <select value={fy} onChange={e => { setFy(e.target.value); setSel(null); }}
                style={{ marginLeft: "auto", padding: "7px 10px", borderRadius: 8, border: "1px solid #cbd5e1", fontWeight: 700 }}>
          {fys.map(f => <option key={f} value={f}>FY {f}</option>)}
        </select>
        {data?.can_reimport && (
          <button onClick={reimport} disabled={busy} style={btn("#fff", "#1d4ed8", "#bfdbfe")}
                  title="Copy this FY from D-QMP again. Days you edited in MES stay as they are.">⟳ Re-import from D-QMP</button>
        )}
      </div>

      {err && <div style={{ color: "#b91c1c", background: "#fee2e2", borderRadius: 10, padding: "8px 12px", marginBottom: 12, fontSize: 13 }}>{err}</div>}

      {data && (
        <>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
            <div style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 12, padding: "8px 14px" }}>
              <div style={{ fontSize: 11, color: "#64748b", fontWeight: 800 }}>WORKING DAYS IN FY {fy}</div>
              <div style={{ fontSize: 22, fontWeight: 800 }}>{data.working_days}
                {hdr?.total_working_days != null && hdr.total_working_days !== data.working_days &&
                  <span style={{ fontSize: 12, color: "#b45309", fontWeight: 700 }}> · D-QMP says {hdr.total_working_days}</span>}
              </div>
            </div>
            <div style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 12, padding: "8px 14px" }}>
              <div style={{ fontSize: 11, color: "#64748b", fontWeight: 800 }}>CHANGED IN MES</div>
              <div style={{ fontSize: 22, fontWeight: 800 }}>{data.mes_edits.length}</div>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 12px", alignItems: "center", fontSize: 12, color: "#475569" }}>
              {Object.entries(KIND).map(([k, v]) => (
                <span key={k} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                  <i style={{ width: 12, height: 12, borderRadius: 3, background: v.bg, border: "1px solid #cbd5e1", display: "inline-block" }} />{v.label}
                </span>))}
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: sel ? "minmax(0,1fr) 300px" : "1fr", gap: 14, alignItems: "start" }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 12 }}>
              {months.map(m => {
                const first = (m.getDay() + 6) % 7;                 // Monday-first grid
                const n = new Date(m.getFullYear(), m.getMonth() + 1, 0).getDate();
                const cells = [...Array(first).fill(null), ...Array.from({ length: n }, (_, i) => i + 1)];
                const iso = (dd) => `${m.getFullYear()}-${String(m.getMonth() + 1).padStart(2, "0")}-${String(dd).padStart(2, "0")}`;
                const worked = Array.from({ length: n }, (_, i) => byDate[iso(i + 1)]).filter(x => x?.working).length;
                return (
                  <div key={m.toISOString()} style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 12, padding: 10 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 800, fontSize: 13, marginBottom: 6 }}>
                      <span>{MONTHS[m.getMonth()]} {m.getFullYear()}</span>
                      <span style={{ color: "#64748b", fontWeight: 700, fontSize: 12 }}>{worked} working</span>
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 3 }}>
                      {WD.map(w => <div key={w} style={{ fontSize: 9.5, color: "#94a3b8", textAlign: "center", fontWeight: 700 }}>{w[0]}</div>)}
                      {cells.map((dd, i) => {
                        if (!dd) return <div key={`e${i}`} />;
                        const d = byDate[iso(dd)];
                        const k = KIND[d?.kind] || KIND.regular;
                        const isSel = sel === iso(dd);
                        return (
                          <button key={dd} onClick={() => { setSel(iso(dd)); setReason(""); }}
                                  title={d ? `${longDay(d.date)} — ${d.label}${d.name ? ` · ${d.name}` : ""}` : ""}
                                  style={{ background: k.bg, color: k.fg, border: isSel ? "2px solid #1d4ed8" : d?.source === "mes" ? "2px dashed #0f172a" : "1px solid #e2e8f0",
                                           borderRadius: 6, fontSize: 11.5, fontWeight: d?.working ? 600 : 800, padding: "4px 0", cursor: "pointer",
                                           fontVariantNumeric: "tabular-nums" }}>{dd}</button>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>

            {sel && day && (
              <div style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 12, padding: 14, position: "sticky", top: 70 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 8 }}>
                  <div style={{ fontWeight: 800, fontSize: 14 }}>{longDay(sel)}</div>
                  <button onClick={() => setSel(null)} style={{ border: "none", background: "none", fontSize: 18, cursor: "pointer", color: "#64748b" }} aria-label="Close">×</button>
                </div>
                <div style={{ margin: "8px 0 4px" }}>
                  <span style={{ background: (KIND[day.kind] || KIND.regular).bg, color: (KIND[day.kind] || KIND.regular).fg,
                                 borderRadius: 999, padding: "3px 10px", fontSize: 12, fontWeight: 800 }}>
                    {day.working ? "Working" : "Non-working"} · {day.label}
                  </span>
                </div>
                {day.name && <div style={{ fontSize: 13, color: "#334155" }}>{day.name}</div>}
                <div style={{ fontSize: 12, color: "#64748b", marginTop: 4 }}>
                  {day.source === "mes" ? `Changed in MES${day.updated_by ? ` by ${day.updated_by}` : ""}`
                    : day.source === "dqmp" ? "From the D-QMP calendar"
                    : day.source === "week_off" ? "Weekly off in this FY" : "Normal working day"}
                </div>

                {data.can_edit ? (
                  <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 8 }}>
                    <label style={{ fontSize: 12, fontWeight: 700, color: "#334155" }}>Reason (shown on the calendar)
                      <input value={reason} onChange={e => setReason(e.target.value)} maxLength={200}
                             placeholder={day.working ? "e.g. Plant closed for audit" : "e.g. Sunday working for backlog"}
                             style={{ width: "100%", boxSizing: "border-box", marginTop: 4, padding: "7px 9px", borderRadius: 8, border: "1px solid #cbd5e1" }} />
                    </label>
                    {!day.working && <button disabled={busy} onClick={() => save(true)} style={btn("#15803d", "#fff", "#15803d")}>Make it a working day</button>}
                    {day.working && <button disabled={busy} onClick={() => save(false)} style={btn("#b91c1c", "#fff", "#b91c1c")}>Make it a non-working day</button>}
                    {day.source === "mes" && <button disabled={busy} onClick={undo} style={btn("#fff", "#334155", "#cbd5e1")}>Undo MES change (back to D-QMP)</button>}
                  </div>
                ) : (
                  <div style={{ marginTop: 12, fontSize: 12, color: "#94a3b8" }}>Admin, plant head or production incharge can change a day.</div>
                )}
              </div>
            )}
          </div>

          {data.mes_edits.length > 0 && (
            <div style={{ marginTop: 14, background: "#fff", border: "1px solid #e5e7eb", borderRadius: 12, padding: 12 }}>
              <div style={{ fontWeight: 800, fontSize: 13, marginBottom: 6 }}>Days changed in MES</div>
              {data.mes_edits.map(e => (
                <div key={e.id} onClick={() => setSel(e.cal_date)} style={{ display: "flex", gap: 10, fontSize: 12.5, padding: "4px 0", cursor: "pointer", flexWrap: "wrap" }}>
                  <b style={{ minWidth: 190 }}>{longDay(e.cal_date)}</b>
                  <span style={{ color: e.is_working ? "#15803d" : "#b91c1c", fontWeight: 700 }}>{e.is_working ? "Working" : "Non-working"}</span>
                  <span style={{ color: "#475569" }}>{e.name}</span>
                  <span style={{ color: "#94a3b8", marginLeft: "auto" }}>{e.updated_by}</span>
                </div>))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
