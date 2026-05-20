/* ════════════════════════════════════════════════════════════════════
 *  HeijunkaPage.jsx
 *  ────────────────────────────────────────────────────────────────────
 *  TPS Heijunka — production-leveling board.
 *
 *  Operator sees per-line × per-day a grid of shift slots showing
 *  WHICH model is scheduled to run WHEN, with achieved vs target.
 *
 *  Features:
 *    - Day-week-month switcher
 *    - Auto-suggest: distribute monthly plan across working days
 *    - Add / edit / delete slot
 *    - Live achieved tracking from ct_log (pro-rata)
 * ════════════════════════════════════════════════════════════════════ */

import { useState, useEffect, useCallback, useMemo } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import AIAssistant from "../components/AIAssistant";
import PageTopbar from "../components/PageTopbar";

const SHIFT_COLOR = { A: "#60a5fa", B: "#a78bfa", C: "#f472b6" };

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

export default function HeijunkaPage() {
  const { token, isAdmin } = useAuth();
  const [lines, setLines] = useState([]);
  const [parts, setParts] = useState([]);
  const [lineId, setLineId] = useState("");
  const [onDate, setOnDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [view, setView] = useState("day");           // day | week | month
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [toastV, setToastV] = useState(null);
  const showToast = (msg, kind = "ok") => {
    setToastV({ msg, kind });
    setTimeout(() => setToastV(null), 3500);
  };
  const [modal, setModal] = useState(null);          // {line_id, plan_date, shift_name, slot_seq, fg_part_id, qty_target}

  useEffect(() => {
    api.get("/api/lines/", token).then(ls => {
      setLines(ls || []);
      if (!lineId && ls?.length) setLineId(ls[0].id);
    });
  }, [token]);

  useEffect(() => {
    if (!lineId) return;
    api.get(`/api/kanban/parts?line_id=${lineId}`, token).then(setParts).catch(() => {});
  }, [token, lineId]);

  const [dFrom, dTo] = useMemo(() => {
    const d = new Date(onDate);
    if (view === "day") return [onDate, onDate];
    if (view === "week") {
      const day = d.getDay();
      const monOffset = (day === 0 ? -6 : 1 - day);
      const start = new Date(d); start.setDate(d.getDate() + monOffset);
      const end   = new Date(start); end.setDate(start.getDate() + 6);
      return [start.toISOString().slice(0, 10), end.toISOString().slice(0, 10)];
    }
    // month
    const start = new Date(d.getFullYear(), d.getMonth(), 1);
    const end   = new Date(d.getFullYear(), d.getMonth() + 1, 0);
    return [start.toISOString().slice(0, 10), end.toISOString().slice(0, 10)];
  }, [onDate, view]);

  const load = useCallback(async () => {
    if (!lineId) return;
    setLoading(true);
    try {
      const url = view === "day"
        ? `/api/heijunka/board?line_id=${lineId}&on_date=${onDate}`
        : `/api/heijunka/plan?line_id=${lineId}&date_from=${dFrom}&date_to=${dTo}`;
      const r = await api.get(url, token);
      setRows(r || []);
    } catch (e) { showToast(`Load failed: ${e.message}`, "err"); }
    finally { setLoading(false); }
  }, [token, lineId, onDate, view, dFrom, dTo]);

  useEffect(() => { load(); }, [load]);

  const autoSuggest = async () => {
    if (!confirm("Auto-suggest will distribute this month's plan across working days.\nExisting slots will NOT be overwritten.")) return;
    try {
      const ym = onDate.slice(0, 7);
      const r = await api.post("/api/heijunka/auto-suggest",
        { line_id: Number(lineId), year_month: ym, overwrite: false }, token);
      showToast(`Auto-suggest · ${r.inserted} slots created, ${r.skipped} skipped`);
      load();
    } catch (e) { showToast(e.message, "err"); }
  };

  const removeSlot = async (id) => {
    if (!confirm("Delete this slot?")) return;
    try { await api.delete(`/api/heijunka/plan/${id}`, token); showToast("Removed"); load(); }
    catch (e) { showToast(e.message, "err"); }
  };

  /* ── DAY VIEW (board with achieved) ─────────────────────────────── */
  const dayBoard = useMemo(() => {
    if (view !== "day") return null;
    const byShift = {};
    rows.forEach(r => { (byShift[r.shift_name] = byShift[r.shift_name] || []).push(r); });
    return byShift;
  }, [rows, view]);

  /* ── WEEK / MONTH grid ──────────────────────────────────────────── */
  const grid = useMemo(() => {
    if (view === "day") return null;
    // unique dates
    const dates = Array.from(new Set(rows.map(r => r.plan_date.slice(0, 10)))).sort();
    // group by date
    const byDate = {};
    rows.forEach(r => {
      const k = r.plan_date.slice(0, 10);
      (byDate[k] = byDate[k] || []).push(r);
    });
    return { dates, byDate };
  }, [rows, view]);

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
      <PageTopbar leading="Heijunka" accent="Schedule" />

      <div style={{ padding: "10px 48px 0", background: "#fff", borderBottom: "1px solid #e2e8f0" }}>
        <div style={{ fontSize: 12, color: "#64748b" }}>
          Production leveling — smoothed model-mix per shift × per day · live achieved tracking
        </div>

        <div style={{ display: "flex", gap: 12, marginTop: 18, alignItems: "end", flexWrap: "wrap" }}>
          <Field label="Line">
            <select value={lineId} onChange={e => setLineId(Number(e.target.value))} style={{ ...glassSelect, minWidth: 180 }}>
              {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
            </select>
          </Field>
          <Field label="Date">
            <input type="date" value={onDate} onChange={e => setOnDate(e.target.value)} style={glassSelect} />
          </Field>
          <div>
            <label style={lblStyle}>View</label>
            <div style={{ display: "flex", gap: 4, marginTop: 6 }}>
              {["day","week","month"].map(v => (
                <button key={v} onClick={() => setView(v)} style={{
                  padding: "8px 14px", fontSize: 11, fontWeight: 800,
                  background: view === v ? "linear-gradient(135deg,#06b6d4,#3b82f6)" : "#f8fafc",
                  color: view === v ? "#fff" : "#64748b",
                  border: view === v ? "1px solid #0891b2" : "1px solid #e2e8f0",
                  borderRadius: 8, cursor: "pointer", textTransform: "uppercase",
                }}>{v}</button>
              ))}
            </div>
          </div>
          <button onClick={load} style={glassBtn}>↻ {loading ? "…" : "Refresh"}</button>
          {isAdmin && (
            <>
              <button onClick={autoSuggest} style={primaryBtn}>🪄 Auto-Suggest Month</button>
              <button onClick={() => setModal({ line_id: Number(lineId), plan_date: onDate, shift_name: "A", slot_seq: 1, fg_part_id: "", qty_target: 0 })}
                      style={primaryBtn}>+ Add Slot</button>
            </>
          )}
        </div>
      </div>

      <div style={{ padding: "20px 48px" }}>
        {view === "day" ? (
          /* ── DAY BOARD ────────────────────────────────────────── */
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16 }}>
            {["A", "B", "C"].filter(sh => dayBoard?.[sh]?.length).map(sh => {
              const slots = dayBoard[sh];
              const totalPlan = slots.reduce((s, x) => s + (x.qty_target || 0), 0);
              const totalAch  = slots.reduce((s, x) => s + (x.achieved || 0), 0);
              const pct = totalPlan > 0 ? (totalAch / totalPlan) * 100 : 0;
              return (
                <div key={sh} style={{
                  ...cardStyle,
                  borderTop: `3px solid ${SHIFT_COLOR[sh]}`,
                }}>
                  <div style={{ padding: 14, borderBottom: "1px solid #e2e8f0" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div style={{ fontSize: 14, fontWeight: 800, color: SHIFT_COLOR[sh] }}>SHIFT {sh}</div>
                      <div style={{ fontSize: 11, color: "#64748b" }}>
                        {slots.length} slot{slots.length !== 1 ? "s" : ""}
                      </div>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6 }}>
                      <div style={{ fontSize: 11, color: "#64748b" }}>Plan vs Actual</div>
                      <div style={{ fontSize: 12, fontWeight: 700, color: pct >= 100 ? "#10b981" : pct >= 80 ? "#f59e0b" : pct > 0 ? "#3b82f6" : "#64748b" }}>
                        {totalAch.toLocaleString()} / {totalPlan.toLocaleString()}  ({pct.toFixed(0)}%)
                      </div>
                    </div>
                    <div style={{ height: 4, background: "#e2e8f0", borderRadius: 2, marginTop: 6, overflow: "hidden" }}>
                      <div style={{
                        height: "100%", width: `${Math.min(100, pct)}%`,
                        background: pct >= 100 ? "#10b981" : pct >= 80 ? "#f59e0b" : "#3b82f6",
                        transition: "width .4s",
                      }} />
                    </div>
                  </div>
                  <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
                    {slots.map(s => {
                      const sPct = s.qty_target > 0 ? (s.achieved / s.qty_target) * 100 : 0;
                      return (
                        <div key={s.id} style={{
                          padding: 10, background: "#f8fafc",
                          border: "1px solid #e2e8f0", borderRadius: 8,
                        }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                            <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700 }}>SLOT {s.slot_seq}</div>
                            <div style={{ fontSize: 11, color: SHIFT_COLOR[sh], fontWeight: 800 }}>
                              {s.achieved} / {s.qty_target}  ({sPct.toFixed(0)}%)
                            </div>
                          </div>
                          <div style={{ fontSize: 13, fontWeight: 700, color: "#0f172a" }}>
                            {s.tbdi_part_no}
                          </div>
                          <div style={{ fontSize: 11, color: "#64748b", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                            {s.description}
                          </div>
                          {isAdmin && (
                            <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                              <button onClick={() => setModal({ ...s, fg_part_id: s.fg_part_id })}
                                      style={{ ...glassBtn, padding: "3px 10px", fontSize: 10 }}>Edit</button>
                              <button onClick={() => removeSlot(s.id)}
                                      style={{ ...glassBtn, padding: "3px 10px", fontSize: 10, color: "#dc2626", borderColor: "#fecaca" }}>Delete</button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                    {slots.length === 0 && (
                      <div style={{ textAlign: "center", padding: 18, color: "#94a3b8", fontSize: 12 }}>
                        No slots — add manually or run Auto-Suggest
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
            {(!dayBoard || Object.keys(dayBoard).length === 0) && (
              <div style={{ ...cardStyle, padding: 40, textAlign: "center", color: "#64748b", gridColumn: "1 / -1" }}>
                No plan for this day · run <b>🪄 Auto-Suggest Month</b> to seed from monthly plan
              </div>
            )}
          </div>
        ) : (
          /* ── WEEK / MONTH GRID ────────────────────────────────── */
          <div style={cardStyle}>
            <div className="col-scroll" style={{ maxHeight: "calc(100vh - 280px)", overflowY: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#0f172a" }}>
                <thead style={{ position: "sticky", top: 0, background: "#f8fafc", zIndex: 1 }}>
                  <tr style={{ borderBottom: "1px solid #e2e8f0" }}>
                    <th style={thStyle}>Date</th>
                    <th style={thStyle}>Shift</th>
                    <th style={thStyle}>Slot</th>
                    <th style={thStyle}>FG Part</th>
                    <th style={thStyle}>Description</th>
                    <th style={{ ...thStyle, textAlign: "right" }}>Plan Qty</th>
                    {isAdmin && <th style={thStyle}></th>}
                  </tr>
                </thead>
                <tbody>
                  {grid?.dates.length === 0 ? (
                    <tr><td colSpan={isAdmin ? 7 : 6} style={{ padding: 30, textAlign: "center", color: "#94a3b8" }}>
                      No plan rows for selected range
                    </td></tr>
                  ) : (
                    grid?.dates.flatMap(d => (grid.byDate[d] || []).map(r => (
                      <tr key={r.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                        <td style={tdStyle}>{d}</td>
                        <td style={tdStyle}>
                          <span style={{ padding: "2px 8px", borderRadius: 99, fontSize: 10, fontWeight: 800,
                                         background: `${SHIFT_COLOR[r.shift_name]}1a`, color: SHIFT_COLOR[r.shift_name] }}>
                            {r.shift_name}
                          </span>
                        </td>
                        <td style={tdStyle}>{r.slot_seq}</td>
                        <td style={{ ...tdStyle, fontFamily: "monospace" }}>{r.tbdi_part_no}</td>
                        <td style={{ ...tdStyle, fontSize: 11, color: "#64748b", maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {r.description}
                        </td>
                        <td style={{ ...tdStyle, textAlign: "right", fontWeight: 700 }}>{Number(r.qty_target || 0).toLocaleString()}</td>
                        {isAdmin && (
                          <td style={tdStyle}>
                            <button onClick={() => setModal({ ...r, plan_date: d })}
                                    style={{ ...glassBtn, padding: "3px 10px", fontSize: 10 }}>Edit</button>
                          </td>
                        )}
                      </tr>
                    )))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {modal && (
        <SlotModal
          modal={modal} parts={parts} token={token}
          onClose={() => setModal(null)}
          onSaved={() => { setModal(null); load(); showToast("Saved"); }}
          onError={(m) => showToast(m, "err")}
        />
      )}

      {toastV && <Toast msg={toastV.msg} kind={toastV.kind} />}
      <AIAssistant />
    </div>
  );
}

function SlotModal({ modal, parts, token, onClose, onSaved, onError }) {
  const [f, setF] = useState(modal);
  const save = async () => {
    if (!f.line_id || !f.plan_date || !f.shift_name || !f.fg_part_id) {
      onError("Pick line, date, shift, and FG part"); return;
    }
    try {
      await api.post("/api/heijunka/plan", {
        ...f,
        line_id: Number(f.line_id),
        fg_part_id: Number(f.fg_part_id),
        slot_seq: Number(f.slot_seq || 1),
        qty_target: Number(f.qty_target || 0),
      }, token);
      onSaved();
    } catch (e) { onError(e.message); }
  };
  return (
    <div onClick={onClose} style={modalBackdrop}>
      <div onClick={e => e.stopPropagation()} style={modalBox}>
        <div style={{ fontSize: 17, fontWeight: 800, marginBottom: 14, color: "#0f172a" }}>
          {f.id ? "Edit Slot" : "Add Slot"}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <Field label="Date">
            <input type="date" value={f.plan_date}
                   onChange={e => setF({ ...f, plan_date: e.target.value })} style={glassSelect} />
          </Field>
          <Field label="Shift">
            <select value={f.shift_name} onChange={e => setF({ ...f, shift_name: e.target.value })} style={glassSelect}>
              <option value="A">A</option><option value="B">B</option><option value="C">C</option>
            </select>
          </Field>
          <Field label="Slot #">
            <input type="number" min={1} value={f.slot_seq}
                   onChange={e => setF({ ...f, slot_seq: e.target.value })} style={glassSelect} />
          </Field>
          <Field label="Qty Target">
            <input type="number" min={0} value={f.qty_target}
                   onChange={e => setF({ ...f, qty_target: e.target.value })} style={glassSelect} />
          </Field>
          <Field label="FG Part" full>
            <select value={f.fg_part_id} onChange={e => setF({ ...f, fg_part_id: e.target.value })} style={glassSelect}>
              <option value="">— pick FG —</option>
              {parts.map(p => (
                <option key={p.id} value={p.id}>
                  {p.tbdi_part_no} · {(p.description || "").slice(0, 40)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Notes" full>
            <textarea rows={2} value={f.notes || ""}
                      onChange={e => setF({ ...f, notes: e.target.value })}
                      style={{ ...glassSelect, resize: "vertical" }} />
          </Field>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 18 }}>
          <button onClick={onClose} style={glassBtn}>Cancel</button>
          <button onClick={save} style={primaryBtn}>Save</button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children, full }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, gridColumn: full ? "1 / -1" : undefined }}>
      <label style={lblStyle}>{label}</label>
      {children}
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
  padding: "8px 12px", fontSize: 13,
  background: "#f8fafc",
  border: "1.5px solid #e2e8f0", borderRadius: 8,
  color: "#0f172a", outline: "none", width: "100%",
  fontFamily: "'Barlow',sans-serif",
};
const glassBtn = {
  padding: "8px 14px", fontSize: 12, fontWeight: 700,
  background: "#f8fafc", color: "#334155",
  border: "1px solid #e2e8f0", borderRadius: 8,
  cursor: "pointer", fontFamily: "'Barlow',sans-serif",
};
const primaryBtn = {
  padding: "9px 16px", fontSize: 12, fontWeight: 800,
  background: "linear-gradient(135deg, #06b6d4, #3b82f6)",
  color: "#fff", border: "none", borderRadius: 8,
  cursor: "pointer", letterSpacing: ".02em",
  boxShadow: "0 2px 8px rgba(59,130,246,.3)",
  fontFamily: "'Barlow',sans-serif",
};
const lblStyle = {
  fontSize: 10, fontWeight: 700, letterSpacing: ".08em",
  textTransform: "uppercase", color: "#64748b",
};
const thStyle = { padding: "11px 10px", fontSize: 10, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: "#64748b", background: "#f8fafc", textAlign: "left" };
const tdStyle = { padding: "9px 10px", fontSize: 12, color: "#0f172a" };
const modalBackdrop = {
  position: "fixed", inset: 0, background: "rgba(15,23,42,.4)",
  backdropFilter: "blur(4px)", zIndex: 1000,
  display: "flex", alignItems: "center", justifyContent: "center",
};
const modalBox = {
  background: "#fff",
  border: "1px solid #e2e8f0",
  borderRadius: 18, padding: 24, width: 580, maxWidth: "90vw",
  maxHeight: "85vh", overflowY: "auto",
  boxShadow: "0 24px 80px rgba(0,0,0,.18)",
  color: "#0f172a",
};
