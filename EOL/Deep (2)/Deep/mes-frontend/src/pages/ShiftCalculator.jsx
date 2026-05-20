/* ════════════════════════════════════════════════════════════════════
 *  ShiftCalculator.jsx
 *  ────────────────────────────────────────────────────────────────────
 *  Production-dept tool: "I need to make N parts — how many shifts + OT
 *  do I need?"  Or reverse: "I have N days — how many can I make?"
 *
 *  Inputs:
 *    • Line             — picker
 *    • Mode             — Forward (target → time)  /  Reverse (time → output)
 *    • Target qty       — forward only
 *    • Days available   — reverse only
 *    • OT allowed       — checkbox
 *    • Quality %        — configurable (default 98)
 *    • Rate source      — Historical (7-day avg) | Theoretical (capacity)
 *
 *  Output:
 *    • Plain summary line
 *    • Day-wise schedule table (Day | Shift | Normal | OT | Total)
 *    • Notes / warnings (unreachable target, historical fallback, etc.)
 * ════════════════════════════════════════════════════════════════════ */

import { useState, useEffect, useCallback, useMemo } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import AIAssistant from "../components/AIAssistant";
import PageTopbar from "../components/PageTopbar";

function Toast({ msg, kind }) {
  if (!msg) return null;
  const c = kind === "err" ? "#dc2626" : "#16a34a";
  return (
    <div style={{
      position: "fixed", bottom: 28, right: 28, zIndex: 10000,
      background: "#fff",
      border: `1px solid ${c}`, color: "#0f172a",
      padding: "12px 18px", borderRadius: 12, fontSize: 13, fontWeight: 600,
      boxShadow: "0 10px 40px rgba(0,0,0,.12)", maxWidth: 400,
    }}>
      <span style={{ color: c, marginRight: 8 }}>{kind === "err" ? "✗" : "✓"}</span>
      {msg}
    </div>
  );
}

export default function ShiftCalculator() {
  const { token } = useAuth();
  const [lines, setLines]   = useState([]);
  const [toastV, setToastV] = useState(null);
  const showToast = (msg, kind = "ok") => {
    setToastV({ msg, kind });
    setTimeout(() => setToastV(null), 3500);
  };

  // Inputs
  const [lineId,     setLineId]     = useState("");
  const [mode,       setMode]       = useState("forward");
  const [targetQty,  setTargetQty]  = useState(5000);
  const [daysAvail,  setDaysAvail]  = useState(3);
  const [allowOt,    setAllowOt]    = useState(true);
  const [qualityPct, setQualityPct] = useState(98);
  const [rateSource, setRateSource] = useState("historical");

  // Result
  const [result,  setResult]  = useState(null);
  const [loading, setLoading] = useState(false);

  // Load lines
  useEffect(() => {
    api.get("/api/shift-calc/lines", token)
      .then(rs => {
        setLines(rs || []);
        if (!lineId && rs?.length) setLineId(rs[0].id);
      })
      .catch(e => showToast(`Failed to load lines: ${e.message}`, "err"));
  }, [token]);

  const compute = async () => {
    if (!lineId) { showToast("Pick a line", "err"); return; }
    if (mode === "forward" && (!targetQty || targetQty <= 0)) {
      showToast("Enter target quantity > 0", "err"); return;
    }
    if (mode === "reverse" && (!daysAvail || daysAvail <= 0)) {
      showToast("Enter days > 0", "err"); return;
    }
    setLoading(true);
    try {
      const r = await api.post("/api/shift-calc/compute", {
        line_id: Number(lineId),
        mode,
        target_qty:  mode === "forward" ? Number(targetQty) : null,
        days_avail:  mode === "reverse" ? Number(daysAvail) : null,
        allow_ot:    allowOt,
        quality_pct: Number(qualityPct),
        rate_source: rateSource,
      }, token);
      setResult(r);
    } catch (e) { showToast(e.message, "err"); setResult(null); }
    finally { setLoading(false); }
  };

  // Auto-compute when key inputs change
  useEffect(() => {
    if (lineId) compute();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lineId, mode, allowOt, qualityPct, rateSource]);

  const groupedSchedule = useMemo(() => {
    if (!result?.schedule) return [];
    const byDay = {};
    result.schedule.forEach(s => {
      if (!byDay[s.day]) byDay[s.day] = [];
      byDay[s.day].push(s);
    });
    return Object.entries(byDay).map(([day, items]) => ({
      day: Number(day),
      items,
      day_total: items.reduce((sum, x) => sum + x.parts_total, 0),
      day_ot_min: items.reduce((sum, x) => sum + (x.ot_minutes || 0), 0),
    }));
  }, [result]);

  return (
    <div style={{
      minHeight: "100vh",
      background: "#f8fafc",
      fontFamily: "'Barlow',sans-serif", paddingBottom: 60, color: "#0f172a",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@700;800&display=swap');
        .col-scroll::-webkit-scrollbar { width: 6px; }
        .col-scroll::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
      `}</style>

      {/* 2026-05-13 — standardised topbar (matches Production Import/Export) */}
      <PageTopbar leading="Shift" accent="Calculator" />
      <div style={{ padding: "8px 48px 0", background: "#fff", borderBottom: "1px solid #e2e8f0" }}>
        <div style={{ fontSize: 12, color: "#64748b", paddingBottom: 10 }}>
          Plan production for any target · historical or theoretical rate · auto-applies quality % + OT capacity
        </div>
      </div>

      <div style={{ padding: "24px 48px", display: "grid", gridTemplateColumns: "380px 1fr", gap: 24 }}>
        {/* ── LEFT: Inputs ─────────────────────────────────────── */}
        <div style={cardStyle}>
          <div style={{ padding: 18, borderBottom: "1px solid #e2e8f0" }}>
            <div style={{ fontSize: 13, fontWeight: 800, letterSpacing: ".05em", color: "#0f172a" }}>INPUTS</div>
          </div>
          <div style={{ padding: 18, display: "flex", flexDirection: "column", gap: 14 }}>

            <Field label="Production Line">
              <select value={lineId} onChange={e => setLineId(Number(e.target.value))} style={glassSelect}>
                {lines.map(l => (
                  <option key={l.id} value={l.id}>
                    {l.line_name} · CT {Number(l.ideal_cycle_time || 15).toFixed(1)}s
                    {l.has_ot ? " · OT" : ""}
                  </option>
                ))}
              </select>
            </Field>

            <div>
              <label style={lblStyle}>Mode</label>
              <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                {[
                  {v:"forward",  label:"🎯 Target → Time"},
                  {v:"reverse",  label:"📅 Time → Output"},
                ].map(m => (
                  <button key={m.v} onClick={() => setMode(m.v)}
                    style={{
                      flex: 1, padding: "8px 12px", fontSize: 11, fontWeight: 800,
                      background: mode === m.v ? "linear-gradient(135deg,#3b82f6,#8b5cf6)" : "#f8fafc",
                      color: mode === m.v ? "#fff" : "#64748b",
                      border: mode === m.v ? "1px solid #3b82f6" : "1px solid #e2e8f0",
                      borderRadius: 8, cursor: "pointer",
                      letterSpacing: ".02em", transition: "all .15s",
                    }}>{m.label}</button>
                ))}
              </div>
            </div>

            {mode === "forward" ? (
              <Field label="Target Quantity (OK parts needed)">
                <input type="number" min={1} value={targetQty}
                       onChange={e => setTargetQty(e.target.value)}
                       onBlur={compute}
                       style={{ ...glassSelect, fontSize: 18, fontWeight: 800 }} />
              </Field>
            ) : (
              <Field label="Days Available">
                <input type="number" min={1} max={365} value={daysAvail}
                       onChange={e => setDaysAvail(e.target.value)}
                       onBlur={compute}
                       style={{ ...glassSelect, fontSize: 18, fontWeight: 800 }} />
              </Field>
            )}

            <Field label="Quality % (factors in NG)">
              <input type="number" min={1} max={100} step="0.1" value={qualityPct}
                     onChange={e => setQualityPct(e.target.value)}
                     style={glassSelect} />
            </Field>

            <Field label="Rate Source">
              <select value={rateSource} onChange={e => setRateSource(e.target.value)} style={glassSelect}>
                <option value="historical">Historical (last 7 days actual avg)</option>
                <option value="theoretical">Theoretical (working_min ÷ ideal CT)</option>
              </select>
            </Field>

            <label style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer", fontSize: 13 }}>
              <input type="checkbox" checked={allowOt} onChange={e => setAllowOt(e.target.checked)}
                     style={{ width: 18, height: 18, accentColor: "#16a34a" }} />
              <span style={{ fontWeight: 700, color: "#0f172a" }}>
                Allow OT
              </span>
              <span style={{ fontSize: 11, color: "#64748b" }}>
                — use overtime hours from shift config
              </span>
            </label>

            <button onClick={compute} disabled={loading} style={primaryBtn}>
              {loading ? "Computing…" : "🔄 Recompute"}
            </button>
          </div>
        </div>

        {/* ── RIGHT: Output ────────────────────────────────────── */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {!result ? (
            <div style={{ ...cardStyle, padding: 50, textAlign: "center", color: "#64748b" }}>
              Adjust inputs to compute
            </div>
          ) : (
            <>
              {/* Summary bar */}
              <div style={{
                background: result.achievable !== false ? "#dcfce7" : "#fee2e2",
                border: `1px solid ${result.achievable !== false ? "#16a34a" : "#dc2626"}`,
                borderRadius: 14, padding: 18,
              }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: "#64748b", letterSpacing: ".12em", textTransform: "uppercase" }}>
                  Answer
                </div>
                <div style={{ fontSize: 22, fontWeight: 800, color: "#0f172a", marginTop: 6, fontFamily: "'Barlow Condensed',sans-serif" }}>
                  {result.plain}
                </div>
              </div>

              {/* KPI tiles */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 12 }}>
                {result.mode === "forward" ? (
                  <>
                    <KPI label="Target (OK)"        value={result.target_qty?.toLocaleString()} color="#3b82f6" />
                    <KPI label="Effective Target"   value={result.effective_target?.toLocaleString()} color="#8b5cf6"
                         hint={`@ ${result.quality_pct}% quality`} />
                    <KPI label="Normal Shifts"      value={result.shifts_used} color="#10b981" hint={`${result.parts_per_shift?.toLocaleString()}/shift`} />
                    {result.ot_hours_total > 0 && (
                      <KPI label="OT Hours"         value={result.ot_hours_total} color="#f59e0b"
                           hint={`${result.ot_parts_total} parts`} />
                    )}
                    <KPI label="Days Needed"        value={result.days_needed} color="#ec4899" />
                    {result.shortage > 0 && (
                      <KPI label="Shortage"         value={result.shortage?.toLocaleString()} color="#ef4444" hint="not achievable" />
                    )}
                  </>
                ) : (
                  <>
                    <KPI label="Days Available"     value={result.days_avail} color="#3b82f6" />
                    <KPI label="Max Output (raw)"   value={result.max_output_raw?.toLocaleString()} color="#10b981" />
                    <KPI label="Max OK Output"      value={result.max_output_ok?.toLocaleString()} color="#8b5cf6"
                         hint={`@ ${result.quality_pct}% quality`} />
                    <KPI label="Per Day Capacity"   value={result.parts_per_day_max?.toLocaleString()} color="#f59e0b" />
                  </>
                )}
              </div>

              {/* Meta */}
              <div style={{ fontSize: 11, color: "#64748b", padding: "0 4px" }}>
                <span style={{ color: "#0f172a", fontWeight: 700 }}>{result.line_name}</span>
                <span style={{ margin: "0 8px" }}>·</span>
                Rate source: <span style={{ color: "#0f172a" }}>{result.rate_source}</span>
                {result.history_avg_hint && (
                  <>
                    <span style={{ margin: "0 8px" }}>·</span>
                    7-day avg: <span style={{ color: "#0f172a" }}>{result.history_avg_hint}/shift</span>
                  </>
                )}
                {result.parts_per_ot_min && (
                  <>
                    <span style={{ margin: "0 8px" }}>·</span>
                    OT rate: <span style={{ color: "#0f172a" }}>{Number(result.parts_per_ot_min).toFixed(2)}/min</span>
                  </>
                )}
              </div>

              {/* Day-wise schedule */}
              <div style={cardStyle}>
                <div style={{ padding: 14, borderBottom: "1px solid #e2e8f0" }}>
                  <div style={{ fontSize: 13, fontWeight: 800, color: "#0f172a" }}>Day-wise Schedule</div>
                  <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>
                    {groupedSchedule.length} day{groupedSchedule.length !== 1 ? "s" : ""}
                  </div>
                </div>
                <div className="col-scroll" style={{ maxHeight: 460, overflowY: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#0f172a" }}>
                    <thead>
                      <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
                        <th style={thStyle}>Day</th>
                        <th style={thStyle}>Shift</th>
                        <th style={{ ...thStyle, textAlign: "right" }}>Normal</th>
                        <th style={{ ...thStyle, textAlign: "right" }}>OT (min)</th>
                        <th style={{ ...thStyle, textAlign: "right" }}>OT Parts</th>
                        <th style={{ ...thStyle, textAlign: "right" }}>Total</th>
                      </tr>
                    </thead>
                    <tbody>
                      {groupedSchedule.length === 0 ? (
                        <tr><td colSpan={6} style={{ padding: 30, textAlign: "center", color: "#94a3b8" }}>—</td></tr>
                      ) : groupedSchedule.flatMap(g => [
                        ...g.items.map((s, idx) => (
                          <tr key={`${g.day}-${s.shift}-${idx}`} style={{ borderBottom: "1px solid #f1f5f9" }}>
                            <td style={{ ...tdStyle, fontWeight: 700, color: "#0f172a" }}>
                              {idx === 0 ? `Day ${g.day}` : ""}
                            </td>
                            <td style={tdStyle}>
                              <span style={{ padding: "2px 8px", borderRadius: 99, fontSize: 10, fontWeight: 800,
                                             background: "#dbeafe", color: "#1e40af" }}>
                                {s.shift}
                              </span>
                            </td>
                            <td style={{ ...tdStyle, textAlign: "right" }}>{s.parts_normal.toLocaleString()}</td>
                            <td style={{ ...tdStyle, textAlign: "right", color: s.ot_minutes > 0 ? "#d97706" : "#94a3b8" }}>
                              {s.ot_minutes > 0 ? `${s.ot_minutes}` : "—"}
                            </td>
                            <td style={{ ...tdStyle, textAlign: "right", color: s.parts_ot > 0 ? "#d97706" : "#94a3b8" }}>
                              {s.parts_ot > 0 ? s.parts_ot.toLocaleString() : "—"}
                            </td>
                            <td style={{ ...tdStyle, textAlign: "right", fontWeight: 800, color: "#16a34a" }}>
                              {s.parts_total.toLocaleString()}
                            </td>
                          </tr>
                        )),
                        <tr key={`${g.day}-total`} style={{ background: "#f8fafc", borderBottom: "2px solid #e2e8f0" }}>
                          <td colSpan={3} style={{ ...tdStyle, fontWeight: 700, color: "#64748b", fontSize: 11 }}>
                            Day {g.day} total
                          </td>
                          <td style={{ ...tdStyle, textAlign: "right", color: "#d97706", fontWeight: 700 }}>
                            {g.day_ot_min > 0 ? `${g.day_ot_min} min` : "—"}
                          </td>
                          <td/>
                          <td style={{ ...tdStyle, textAlign: "right", fontWeight: 800, color: "#16a34a" }}>
                            {g.day_total.toLocaleString()}
                          </td>
                        </tr>
                      ])}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Hints / warnings */}
              {result.mode === "forward" && !result.achievable && (
                <div style={{
                  background: "#fee2e2", border: "1px solid #dc2626",
                  borderRadius: 12, padding: 14, fontSize: 12, color: "#991b1b",
                }}>
                  ⚠ Target unreachable within 365 days at this rate.  Try a higher OT budget,
                  multiple lines, or improving CT.
                </div>
              )}
              {result.mode === "forward" && result.achievable && !allowOt && result.shifts_used > 6 && (
                <div style={{
                  background: "#fef3c7", border: "1px solid #d97706",
                  borderRadius: 12, padding: 14, fontSize: 12, color: "#92400e",
                }}>
                  💡 Tip: Enable OT — could reduce days needed significantly.
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {toastV && <Toast msg={toastV.msg} kind={toastV.kind} />}
      <AIAssistant />
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div>
      <label style={lblStyle}>{label}</label>
      <div style={{ marginTop: 6 }}>{children}</div>
    </div>
  );
}

function KPI({ label, value, color, hint }) {
  return (
    <div style={{
      background: "#fff",
      border: "1px solid #e2e8f0",
      borderTop: `3px solid ${color}`,
      borderRadius: 12, padding: 14,
      boxShadow: "0 1px 3px rgba(0,0,0,.04)",
    }}>
      <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".12em", textTransform: "uppercase", color: "#64748b" }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 800, color, fontFamily: "'Barlow Condensed',sans-serif", lineHeight: 1.1, marginTop: 4 }}>
        {value ?? "—"}
      </div>
      {hint && <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 2 }}>{hint}</div>}
    </div>
  );
}

const cardStyle = {
  background: "#fff",
  border: "1px solid #e2e8f0", borderRadius: 14,
  boxShadow: "0 1px 3px rgba(0,0,0,.04)",
  overflow: "hidden",
};
const glassSelect = {
  padding: "10px 12px", fontSize: 13,
  background: "#f8fafc",
  border: "1.5px solid #e2e8f0", borderRadius: 8,
  color: "#0f172a", outline: "none", width: "100%",
  fontFamily: "'Barlow',sans-serif",
};
const primaryBtn = {
  padding: "12px 18px", fontSize: 13, fontWeight: 800,
  background: "linear-gradient(135deg, #3b82f6, #8b5cf6)",
  color: "#fff", border: "none", borderRadius: 10,
  cursor: "pointer", letterSpacing: ".02em",
  boxShadow: "0 2px 8px rgba(59,130,246,.3)",
  fontFamily: "'Barlow',sans-serif",
};
const lblStyle = {
  fontSize: 10, fontWeight: 700, letterSpacing: ".08em",
  textTransform: "uppercase", color: "#64748b",
};
const thStyle = { padding: "11px 10px", fontSize: 10, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: "#64748b", textAlign: "left" };
const tdStyle = { padding: "9px 10px", fontSize: 12, color: "#0f172a" };
