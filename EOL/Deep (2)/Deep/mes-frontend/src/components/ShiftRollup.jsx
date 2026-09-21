// ───────────────────────────────────────────────────────────────────────
// ShiftRollup.jsx   (2026-09-13)
// ───────────────────────────────────────────────────────────────────────
// The COMPILED module inside Shift Compile for the supervisor hierarchy:
//   • shift_incharge   → roll-up across the lines assigned to them.
//   • section_incharge → zone-wide roll-up (every line in the zones of their
//                        assigned lines) + manpower.
//   • admin / plant_head / production_incharge → everything (oversight).
// Two parts: (1) compiled totals + per-model + per-line for the CURRENT
// shift, and (2) a historical date-to-date + shift filter with
// Excel / PDF download of all details.  Read-only.
// 2026-09-19: the page's top date + shift filter is Line Report's alone.
// Part (1) always shows the live shift; the From / To / Shift filter drives
// part (2) only, which now also lists every line.
// Zone summary (same day): per zone — lines running / stopped / not planned,
// zone OEE and major losses; a "Today" card + the same table inside part (2)
// for the filtered range.  Backed by /api/shift-compile/zone-summary.
// Backed by /api/shift-compile/{compiled,historical,compiled-export}.
// ───────────────────────────────────────────────────────────────────────
import { Fragment, useEffect, useMemo, useState, useCallback } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const card = { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, padding: "16px 18px", marginBottom: 16, boxShadow: "0 1px 3px rgba(0,0,0,.04)" };
const th = { textAlign: "left", padding: "7px 10px", fontSize: 11, fontWeight: 700, color: "#64748b", textTransform: "uppercase", letterSpacing: ".04em", borderBottom: "1px solid #e2e8f0", whiteSpace: "nowrap" };
const td = { padding: "7px 10px", fontSize: 12.5, color: "#0f172a", borderBottom: "1px solid #f1f5f9", whiteSpace: "nowrap" };
const inp = { padding: "8px 10px", border: "1px solid #cbd5e1", borderRadius: 8, fontSize: 13, background: "#fff", color: "#0f172a" };
const btn = { background: "#1e40af", color: "#fff", border: "none", borderRadius: 8, padding: "8px 14px", fontSize: 13, fontWeight: 700, cursor: "pointer" };
const btnG = { background: "#fff", color: "#334155", border: "1px solid #cbd5e1", borderRadius: 8, padding: "8px 14px", fontSize: 13, fontWeight: 600, cursor: "pointer" };
const num = (n) => (n == null ? "—" : Number(n).toLocaleString("en-IN"));
const localDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

// The shift running now, from a line's shift timings — or, between shifts,
// the one that ended last.  A shift that crosses midnight keeps the date it
// started on, as the collectors record it (B at 01:00 is yesterday's B).
// GAP rows are skipped; is_production is not, since B is non-production on
// some lines and production on others.
function currentShift(cfg, now = new Date()) {
  const shifts = (cfg || []).filter(s => s.start_time && s.end_time && !/^GAP/i.test(s.shift_name || ""));
  let running = null, last = null;
  for (const back of [0, 1]) {
    const y = now.getFullYear(), m = now.getMonth(), d = now.getDate() - back;
    for (const s of shifts) {
      const [sh, sm] = s.start_time.split(":").map(Number);
      const [eh, em] = s.end_time.split(":").map(Number);
      const st = new Date(y, m, d, sh, sm);
      const en = new Date(y, m, d + (s.crosses_midnight ? 1 : 0), eh, em);
      const c = { date: localDay(new Date(y, m, d)), shift: s.shift_name, end: en };
      if (st <= now && now < en) running = running || c;
      else if (en <= now && (!last || en > last.end)) last = c;
    }
  }
  const r = running || last;
  return r ? { date: r.date, shift: r.shift } : null;
}

function Kpi({ label, value, sub, color = "#0f172a" }) {
  return (
    <div style={{ flex: "1 1 120px", minWidth: 110, background: "#f8fafc", border: "1px solid #eef2f7", borderRadius: 12, padding: "10px 14px" }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "#64748b", textTransform: "uppercase", letterSpacing: ".04em" }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color, marginTop: 2 }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 1 }}>{sub}</div>}
    </div>
  );
}

// ── Zone summary table (Today card + inside the historical card) ────────
const ZONE_STATUS = {
  running:     ["Running", "#15803d"],
  stopped:     ["Stopped", "#b91c1c"],
  not_planned: ["Not planned", "#64748b"],
};
const mins = (v) => `${num(Math.round(v))}m`;
function zoneLosses(z) {
  if (z.top_losses?.length) return z.top_losses.map(t => `${t.label} ${mins(t.minutes)}`).join(" · ");
  return z.no_loss_data ? "No loss data" : "—";
}

function ZoneTable({ data }) {
  const [open, setOpen] = useState(null);
  if (!data?.zones?.length) return <div style={{ color: "#94a3b8", padding: 8 }}>No lines in scope.</div>;
  const labels = data.loss_labels || {};
  const tot = data.totals || {};
  return (
    <div>
      <div style={{ fontSize: 12.5, color: "#475569", marginBottom: 8 }}>
        <b>{tot.lines_total}</b> lines · <b style={{ color: "#15803d" }}>{tot.running} running</b> ·{" "}
        <b style={{ color: "#b91c1c" }}>{tot.stopped} stopped</b> · {tot.not_planned} not planned
        <span style={{ color: "#94a3b8" }}> — Running = planned and parts made · Stopped = planned, nothing made · click a zone for its lines</span>
      </div>
      <div style={{ overflowX: "auto", border: "1px solid #eef2f7", borderRadius: 10 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 880 }}>
          <thead><tr>{["Zone", "Lines", "Running", "Stopped", "Not planned", "Plan", "Actual", "Ach %", "OEE", "Major losses"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
          <tbody>
            {data.zones.map(z => {
              const isOpen = open === z.zone_name;
              const ach = z.plan ? Math.round(z.actual * 1000 / z.plan) / 10 : null;
              return (
                <Fragment key={z.zone_name}>
                  <tr onClick={() => setOpen(isOpen ? null : z.zone_name)}
                      style={{ cursor: "pointer", background: isOpen ? "#f8fafc" : undefined }}>
                    <td style={{ ...td, fontWeight: 700 }}>{isOpen ? "▾" : "▸"} {z.zone_name}</td>
                    <td style={td}>{z.lines_total}</td>
                    <td style={{ ...td, color: "#15803d", fontWeight: 700 }}>{z.running}</td>
                    <td style={{ ...td, color: z.stopped ? "#b91c1c" : "#94a3b8", fontWeight: z.stopped ? 700 : 400 }}>{z.stopped}</td>
                    <td style={{ ...td, color: "#64748b" }}>{z.not_planned}</td>
                    <td style={td}>{num(z.plan)}</td>
                    <td style={td}>{num(z.actual)}</td>
                    <td style={td}>{ach == null ? "—" : `${ach}%`}</td>
                    <td style={{ ...td, fontWeight: 700, color: "#1e40af" }}>{z.oee == null ? "—" : `${z.oee}%`}</td>
                    <td style={{ ...td, whiteSpace: "normal", minWidth: 220 }}>{zoneLosses(z)}</td>
                  </tr>
                  {isOpen && (
                    <tr>
                      <td colSpan={10} style={{ padding: "4px 10px 12px 26px", background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
                        <table style={{ width: "100%", borderCollapse: "collapse" }}>
                          <thead><tr>{["Line", "Status", "Plan", "Actual", "OEE", "Shifts ran / planned", "Top loss", "Live status", "Note"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                          <tbody>
                            {z.lines.map(l => {
                              const [lab, col] = ZONE_STATUS[l.status] || [l.status, "#334155"];
                              const top = Object.entries(l.losses || {}).sort((a, b) => b[1] - a[1])[0];
                              const note = [l.note, l.npd_shifts ? `Non-Production Day (${l.npd_shifts} shift${l.npd_shifts > 1 ? "s" : ""})` : null].filter(Boolean).join(" · ");
                              return (
                                <tr key={l.line_id}>
                                  <td style={{ ...td, fontWeight: 700 }}>{l.line_name}</td>
                                  <td style={{ ...td, color: col, fontWeight: 700 }}>{lab}</td>
                                  <td style={td}>{num(l.plan)}</td>
                                  <td style={td}>{num(l.actual)}</td>
                                  <td style={td}>{l.oee == null ? "—" : `${l.oee}%`}</td>
                                  <td style={td}>{l.ran_shifts} / {l.planned_shifts}</td>
                                  <td style={td}>{top ? `${labels[top[0]] || top[0]} ${mins(top[1])}` : "—"}</td>
                                  <td style={{ ...td, color: "#64748b" }}>{l.live_status || "—"}</td>
                                  <td style={{ ...td, color: "#64748b", whiteSpace: "normal" }}>{note || ""}</td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function ShiftRollup() {
  const { token, user } = useAuth();
  const role = user?.role || "";
  const scopeLabel = role === "section_incharge" ? "Zone-wide"
                    : role === "shift_incharge" ? "My lines" : "All lines";

  // ── current shift (part 1 only — the filter below never touches it) ────
  const [cur, setCur] = useState(null);
  useEffect(() => {
    let dead = false;
    (async () => {
      let c = null;
      try {
        const lines = await api.get("/api/lines/", token);
        const lid = (lines || [])[0]?.id;
        if (lid) c = currentShift(await api.get(`/api/config/shifts/${lid}`, token));
      } catch { /* fall back below */ }
      if (!dead) setCur(c || { date: localDay(new Date()), shift: "A" });
    })();
    return () => { dead = true; };
  }, [token]);
  const date = cur?.date, shift = cur?.shift;

  // ── zone summary for the current production day (all shifts so far) ──
  const [zToday, setZToday] = useState(null);
  useEffect(() => {
    if (!date) return;
    let dead = false;
    api.get(`/api/shift-compile/zone-summary?date_from=${date}&date_to=${date}&shift=ALL`, token)
      .then(d => { if (!dead) setZToday(d); })
      .catch(e => { if (!dead) setZToday({ error: e.message || "Load failed" }); });
    return () => { dead = true; };
  }, [date, token]);

  // ── compiled (selected date + shift) ──────────────────────────────────
  const [comp, setComp] = useState(null);
  const [loading, setLoading] = useState(false);
  const loadCompiled = useCallback(async () => {
    if (!date || !shift) return;
    setLoading(true);
    try { setComp(await api.get(`/api/shift-compile/compiled?date=${date}&shift=${shift}`, token)); }
    catch { setComp(null); }
    finally { setLoading(false); }
  }, [date, shift, token]);
  useEffect(() => { loadCompiled(); }, [loadCompiled]);

  // ── historical (date-to-date + shift filter + download) ───────────────
  const [hf, setHf] = useState(() => localDay(new Date()));
  const [ht, setHt] = useState(() => localDay(new Date()));
  const [hshift, setHshift] = useState("ALL");
  const [hist, setHist] = useState(null);
  const [zHist, setZHist] = useState(null);
  const [hloading, setHloading] = useState(false);
  const [err, setErr] = useState("");

  // Per-line rows for the loaded range: one row per line, counts summed,
  // OEE averaged, Closed = shifts closed / shifts in range.
  const histLines = useMemo(() => {
    const by = new Map();
    for (const r of hist?.rows || []) {
      let g = by.get(r.line_id);
      if (!g) {
        g = { zone_name: r.zone_name, line_name: r.line_name, models: [], ok: 0, ng: 0, plan: 0,
              present: 0, required: 0, oees: [], n: 0, closedN: 0, closed: r.closed, on_time: r.on_time };
        by.set(r.line_id, g);
      }
      if (r.model && !g.models.includes(r.model)) g.models.push(r.model);
      g.ok += r.ok || 0; g.ng += r.ng || 0; g.plan += r.plan || 0;
      g.present += r.present || 0; g.required += r.required || 0;
      if (r.oee != null) g.oees.push(r.oee);
      g.n += 1; if (r.closed) g.closedN += 1;
    }
    return [...by.values()].map(g => ({
      ...g,
      model: g.models.length > 1 ? `${g.models[0]} +${g.models.length - 1}` : (g.models[0] || null),
      oee: g.oees.length ? Math.round(g.oees.reduce((a, b) => a + b, 0) / g.oees.length * 10) / 10 : null,
    }));
  }, [hist]);

  const loadHist = async () => {
    setErr(""); setHloading(true); setHist(null); setZHist(null);
    const q = `date_from=${hf}&date_to=${ht}&shift=${hshift}`;
    try {
      const [h, z] = await Promise.all([
        api.get(`/api/shift-compile/historical?${q}`, token),
        api.get(`/api/shift-compile/zone-summary?${q}`, token).catch(() => null),
      ]);
      setHist(h); setZHist(z);
    }
    catch (e) { setErr(e.message || "Load failed"); }
    finally { setHloading(false); }
  };
  const download = async (fmt) => {
    setErr("");
    try {
      const jwt = token || sessionStorage.getItem("mes_token") || "";
      const url = `/api/shift-compile/compiled-export?format=${fmt}&date_from=${hf}&date_to=${ht}&shift=${hshift}`;
      const res = await fetch(url, { headers: { Authorization: `Bearer ${jwt}` } });
      if (!res.ok) throw new Error("Export failed (" + res.status + ")");
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `shift_compiled_${hf}_${ht}_${hshift}.${fmt === "pdf" ? "pdf" : "xlsx"}`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(a.href);
    } catch (e) { setErr(e.message || "Export failed"); }
  };

  const t = comp?.totals;

  return (
    <div>
      {/* ══ COMPILED — selected date + shift ══════════════════════════════ */}
      <div style={card}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
          <div style={{ fontSize: 15, fontWeight: 800 }}>
            Compiled roll-up
            <span style={{ color: "#94a3b8", fontWeight: 600 }}> · Current shift · {scopeLabel}{cur ? ` · ${date} · Shift ${shift}` : ""}</span>
          </div>
          {comp?.zones?.length ? (
            <span style={{ background: "#eff6ff", color: "#1e40af", border: "1px solid #bfdbfe", padding: "3px 10px", borderRadius: 999, fontSize: 12, fontWeight: 700 }}>
              {comp.zones.join(" · ")}
            </span>
          ) : null}
        </div>

        {loading || !cur ? <div style={{ color: "#94a3b8", padding: 8 }}>Loading…</div>
         : !t || t.lines === 0 ? <div style={{ color: "#94a3b8", padding: 8 }}>No lines in scope for this shift.</div>
         : (
          <>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
              <Kpi label="Lines" value={num(t.lines)} />
              <Kpi label="OK" value={num(t.ok)} color="#15803d" />
              <Kpi label="NG" value={num(t.ng)} color="#b91c1c" />
              <Kpi label="Produced" value={num(t.total)} sub={`Plan ${num(t.plan)}`} />
              <Kpi label="Avg OEE" value={t.oee == null ? "—" : `${t.oee}%`} color="#1e40af" />
              <Kpi label="Manpower" value={`${num(t.present)} / ${num(t.required)}`} sub="present / required" />
            </div>

            <div style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "flex-start" }}>
              {/* per-model totals */}
              <div style={{ flex: "1 1 300px", minWidth: 280 }}>
                <div style={{ fontSize: 12, fontWeight: 800, color: "#334155", marginBottom: 6 }}>Per-model totals ({comp.models?.length || 0})</div>
                <div style={{ overflowX: "auto", border: "1px solid #eef2f7", borderRadius: 10 }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 300 }}>
                    <thead><tr>{["Model", "OK", "NG", "Total"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                    <tbody>
                      {(comp.models || []).map((m, i) => (
                        <tr key={i}>
                          <td style={{ ...td, whiteSpace: "normal" }}>{m.model}</td>
                          <td style={{ ...td, color: "#15803d", fontWeight: 700 }}>{num(m.ok)}</td>
                          <td style={{ ...td, color: "#b91c1c" }}>{num(m.ng)}</td>
                          <td style={{ ...td, fontWeight: 700 }}>{num(m.total)}</td>
                        </tr>
                      ))}
                      {!(comp.models || []).length && <tr><td style={td} colSpan={4}><span style={{ color: "#94a3b8" }}>No model data.</span></td></tr>}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* per-line summary */}
              <div style={{ flex: "2 1 460px", minWidth: 340 }}>
                <div style={{ fontSize: 12, fontWeight: 800, color: "#334155", marginBottom: 6 }}>Per-line ({t.lines})</div>
                <div style={{ overflowX: "auto", border: "1px solid #eef2f7", borderRadius: 10 }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 640 }}>
                    <thead><tr>{["Zone", "Line", "Model", "OK", "NG", "Plan", "OEE", "MP", "Closed"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                    <tbody>
                      {(comp.lines || []).map((r, i) => (
                        <tr key={i}>
                          <td style={{ ...td, color: "#64748b" }}>{r.zone_name || "—"}</td>
                          <td style={{ ...td, fontWeight: 700 }}>{r.line_name}</td>
                          <td style={{ ...td, whiteSpace: "normal", maxWidth: 200 }}>{r.model || "—"}</td>
                          <td style={{ ...td, color: "#15803d", fontWeight: 700 }}>{num(r.ok)}</td>
                          <td style={{ ...td, color: r.ng ? "#b91c1c" : "#94a3b8" }}>{num(r.ng)}</td>
                          <td style={td}>{num(r.plan)}</td>
                          <td style={td}>{r.oee == null ? "—" : `${r.oee}%`}</td>
                          <td style={td}>{num(r.present)}/{num(r.required)}</td>
                          <td style={td}>{r.closed ? (r.on_time ? "✓ on-time" : "✓ late") : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          </>
        )}
      </div>

      {/* ══ ZONE SUMMARY — current production day ══════════════════════════ */}
      <div style={card}>
        <div style={{ fontSize: 15, fontWeight: 800, marginBottom: 10 }}>
          Zone summary
          <span style={{ color: "#94a3b8", fontWeight: 600 }}> · Today{date ? ` · ${date}` : ""} · All shifts</span>
        </div>
        {!zToday ? <div style={{ color: "#94a3b8", padding: 8 }}>Loading…</div>
         : zToday.error ? <div style={{ color: "#b91c1c", fontSize: 12.5 }}>Zone summary unavailable: {zToday.error}</div>
         : <ZoneTable data={zToday} />}
      </div>

      {/* ══ HISTORICAL — date-to-date + shift filter + download ═══════════ */}
      <div style={card}>
        <div style={{ fontSize: 15, fontWeight: 800, marginBottom: 10 }}>Historical · compiled</div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", marginBottom: 12 }}>
          <label style={{ fontSize: 12, fontWeight: 700, color: "#64748b" }}>From</label>
          <input type="date" value={hf} max={ht} onChange={e => setHf(e.target.value)} style={inp} />
          <label style={{ fontSize: 12, fontWeight: 700, color: "#64748b" }}>To</label>
          <input type="date" value={ht} min={hf} onChange={e => setHt(e.target.value)} style={inp} />
          <select value={hshift} onChange={e => setHshift(e.target.value)} style={inp}>
            <option value="ALL">All shifts</option>
            <option value="A">Shift A</option>
            <option value="B">Shift B</option>
          </select>
          <button onClick={loadHist} disabled={hloading} style={btn}>{hloading ? "Loading…" : "Load"}</button>
          <div style={{ flex: 1 }} />
          <button onClick={() => download("xlsx")} style={btnG}>⬇ Excel</button>
          <button onClick={() => download("pdf")} style={btnG}>⬇ PDF</button>
        </div>
        {err && <div style={{ color: "#b91c1c", fontSize: 12.5, marginBottom: 8 }}>{err}</div>}

        {hist && (
          <>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
              <Kpi label="Days" value={num(hist.days?.length)} />
              <Kpi label="OK" value={num(hist.totals?.ok)} color="#15803d" />
              <Kpi label="NG" value={num(hist.totals?.ng)} color="#b91c1c" />
              <Kpi label="Produced" value={num(hist.totals?.total)} sub={`Plan ${num(hist.totals?.plan)}`} />
              <Kpi label="Avg OEE" value={hist.totals?.oee == null ? "—" : `${hist.totals.oee}%`} color="#1e40af" />
            </div>
            {zHist && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 12, fontWeight: 800, color: "#334155", marginBottom: 6 }}>Zone-wise</div>
                <ZoneTable data={zHist} />
              </div>
            )}
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "flex-start" }}>
              <div style={{ flex: "1 1 300px", minWidth: 280 }}>
                <div style={{ fontSize: 12, fontWeight: 800, color: "#334155", marginBottom: 6 }}>Per-model (range total)</div>
                <div style={{ overflowX: "auto", border: "1px solid #eef2f7", borderRadius: 10 }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 300 }}>
                    <thead><tr>{["Model", "OK", "NG", "Total"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                    <tbody>
                      {(hist.models || []).map((m, i) => (
                        <tr key={i}>
                          <td style={{ ...td, whiteSpace: "normal" }}>{m.model}</td>
                          <td style={{ ...td, color: "#15803d", fontWeight: 700 }}>{num(m.ok)}</td>
                          <td style={{ ...td, color: "#b91c1c" }}>{num(m.ng)}</td>
                          <td style={{ ...td, fontWeight: 700 }}>{num(m.total)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <div style={{ flex: "2 1 420px", minWidth: 320 }}>
                <div style={{ fontSize: 12, fontWeight: 800, color: "#334155", marginBottom: 6 }}>Day-wise totals</div>
                <div style={{ overflowX: "auto", border: "1px solid #eef2f7", borderRadius: 10, maxHeight: 340 }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 520 }}>
                    <thead><tr>{["Date", "Lines", "OK", "NG", "Total", "Plan", "OEE", "MP"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                    <tbody>
                      {(hist.days || []).map((d, i) => (
                        <tr key={i}>
                          <td style={{ ...td, fontWeight: 700 }}>{d.date}</td>
                          <td style={td}>{num(d.lines)}</td>
                          <td style={{ ...td, color: "#15803d" }}>{num(d.ok)}</td>
                          <td style={{ ...td, color: d.ng ? "#b91c1c" : "#94a3b8" }}>{num(d.ng)}</td>
                          <td style={{ ...td, fontWeight: 700 }}>{num(d.total)}</td>
                          <td style={td}>{num(d.plan)}</td>
                          <td style={td}>{d.oee == null ? "—" : `${d.oee}%`}</td>
                          <td style={td}>{num(d.present)}/{num(d.required)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
            <div style={{ fontSize: 12, fontWeight: 800, color: "#334155", margin: "14px 0 6px" }}>Per-line ({histLines.length})</div>
            <div style={{ overflowX: "auto", border: "1px solid #eef2f7", borderRadius: 10 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 640 }}>
                <thead><tr>{["Zone", "Line", "Model", "OK", "NG", "Plan", "OEE", "MP", "Closed"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                <tbody>
                  {histLines.map((r, i) => (
                    <tr key={i}>
                      <td style={{ ...td, color: "#64748b" }}>{r.zone_name || "—"}</td>
                      <td style={{ ...td, fontWeight: 700 }}>{r.line_name}</td>
                      <td style={{ ...td, whiteSpace: "normal", maxWidth: 200 }} title={r.models.length > 1 ? r.models.join("\n") : undefined}>{r.model || "—"}</td>
                      <td style={{ ...td, color: "#15803d", fontWeight: 700 }}>{num(r.ok)}</td>
                      <td style={{ ...td, color: r.ng ? "#b91c1c" : "#94a3b8" }}>{num(r.ng)}</td>
                      <td style={td}>{num(r.plan)}</td>
                      <td style={td}>{r.oee == null ? "—" : `${r.oee}%`}</td>
                      <td style={td}>{num(r.present)}/{num(r.required)}</td>
                      <td style={td}>{r.n > 1 ? `${r.closedN}/${r.n}` : r.closed ? (r.on_time ? "✓ on-time" : "✓ late") : "—"}</td>
                    </tr>
                  ))}
                  {!histLines.length && <tr><td style={td} colSpan={9}><span style={{ color: "#94a3b8" }}>No lines in range.</span></td></tr>}
                </tbody>
              </table>
            </div>
            <div style={{ fontSize: 11.5, color: "#94a3b8", marginTop: 10 }}>
              Excel / PDF me poori line-wise + day-wise + per-model details aati hain.
            </div>
          </>
        )}
      </div>
    </div>
  );
}
