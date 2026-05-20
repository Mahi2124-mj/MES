/* ════════════════════════════════════════════════════════════════════
 *  PDCAPage.jsx
 *  ────────────────────────────────────────────────────────────────────
 *  TPS PDCA / A3 problem-solving tracker.
 *
 *  List view (left) + detail view (right) — operator can:
 *    • Create new A3 (auto-numbered A3-YYMMDD-NNN)
 *    • Fill problem / root cause / countermeasure / check / act
 *    • Advance phase: PLAN → DO → CHECK → ACT (auto-closes on ACT)
 *    • Change status: OPEN / IN_PROGRESS / ON_HOLD / CLOSED / ESCALATED
 *    • Append notes; timeline shows every change
 * ════════════════════════════════════════════════════════════════════ */

import { useState, useEffect, useCallback, useMemo } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import AIAssistant from "../components/AIAssistant";
import PageTopbar from "../components/PageTopbar";

const PHASES = [
  { key: "PLAN",  label: "Plan",  desc: "Define + analyse",     color: "#3b82f6" },
  { key: "DO",    label: "Do",    desc: "Implement fix",        color: "#f59e0b" },
  { key: "CHECK", label: "Check", desc: "Verify outcome",       color: "#8b5cf6" },
  { key: "ACT",   label: "Act",   desc: "Standardize / close",  color: "#10b981" },
];
const STATUS_COLOR = {
  OPEN: "#3b82f6", IN_PROGRESS: "#f59e0b", ON_HOLD: "#94a3b8",
  CLOSED: "#10b981", ESCALATED: "#ef4444",
};
const SEV_COLOR = { HIGH: "#ef4444", MED: "#f59e0b", LOW: "#3b82f6" };

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

export default function PDCAPage() {
  const { token, isAdmin } = useAuth();
  const [records, setRecords] = useState([]);
  const [lines, setLines] = useState([]);
  const [summary, setSummary] = useState({});
  const [filter, setFilter] = useState({ status: "", phase: "", line_id: "" });
  const [selected, setSelected] = useState(null);     // id of opened A3
  const [showNew, setShowNew]   = useState(false);
  const [toastV, setToastV] = useState(null);
  const showToast = (msg, kind = "ok") => {
    setToastV({ msg, kind });
    setTimeout(() => setToastV(null), 3500);
  };

  const load = useCallback(async () => {
    try {
      const qs = new URLSearchParams();
      if (filter.line_id) qs.set("line_id", filter.line_id);
      if (filter.status)  qs.set("status", filter.status);
      if (filter.phase)   qs.set("phase", filter.phase);
      const [recs, ls, sum] = await Promise.all([
        api.get(`/api/pdca?${qs}`, token),
        api.get("/api/lines/", token),
        api.get("/api/pdca/summary/counts", token),
      ]);
      setRecords(recs || []);
      setLines(ls || []);
      setSummary(sum || {});
    } catch (e) { showToast(`Load failed: ${e.message}`, "err"); }
  }, [token, filter.line_id, filter.status, filter.phase]);

  useEffect(() => { load(); }, [load]);

  const selRec = useMemo(() => records.find(r => r.id === selected), [records, selected]);

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

      {/* 2026-05-13 — standardised topbar */}
      <PageTopbar leading="PDCA" accent="/ A3" />

      <div style={{ padding: "12px 48px 14px", background: "#fff", borderBottom: "1px solid #e2e8f0" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 24, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: 12, color: "#64748b" }}>
              Plan → Do → Check → Act · per-problem auto-numbered A3 · timeline + owner + due dates
            </div>
          </div>

          {/* Quick stats */}
          <div style={{ display: "flex", gap: 10 }}>
            {[
              { lbl: "Total",   v: summary.total ?? 0,   c: "#94a3b8" },
              { lbl: "Open",    v: summary.open ?? 0,    c: "#3b82f6" },
              { lbl: "Overdue", v: summary.overdue ?? 0, c: "#ef4444" },
              { lbl: "Closed",  v: summary.by_status?.CLOSED ?? 0, c: "#10b981" },
            ].map(s => (
              <div key={s.lbl} style={{
                background: `${s.c}1a`, border: `1px solid ${s.c}44`,
                padding: "10px 14px", borderRadius: 10, minWidth: 80, textAlign: "center",
              }}>
                <div style={{ fontSize: 10, color: "#64748b", letterSpacing: ".1em", textTransform: "uppercase" }}>{s.lbl}</div>
                <div style={{ fontSize: 22, fontWeight: 800, color: s.c, fontFamily: "'Barlow Condensed',sans-serif" }}>{s.v}</div>
              </div>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", gap: 10, marginTop: 18, alignItems: "end" }}>
          <Field label="Status">
            <select value={filter.status} onChange={e => setFilter({ ...filter, status: e.target.value })} style={glassSelect}>
              <option value="">All</option>
              {Object.keys(STATUS_COLOR).map(s => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
            </select>
          </Field>
          <Field label="Phase">
            <select value={filter.phase} onChange={e => setFilter({ ...filter, phase: e.target.value })} style={glassSelect}>
              <option value="">All</option>
              {PHASES.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
            </select>
          </Field>
          <Field label="Line">
            <select value={filter.line_id} onChange={e => setFilter({ ...filter, line_id: e.target.value })} style={{ ...glassSelect, minWidth: 140 }}>
              <option value="">All</option>
              {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
            </select>
          </Field>
          <div style={{ flex: 1 }} />
          <button onClick={() => setShowNew(true)} style={primaryBtn}>+ NEW A3</button>
        </div>
      </div>

      <div style={{ padding: "20px 48px", display: "grid", gridTemplateColumns: "420px 1fr", gap: 16 }}>
        {/* List */}
        <div style={cardStyle}>
          <div className="col-scroll" style={{ maxHeight: "calc(100vh - 280px)", overflowY: "auto" }}>
            {records.length === 0 ? (
              <div style={{ padding: 40, textAlign: "center", color: "#94a3b8", fontSize: 13 }}>
                No A3 records · click <b>+ NEW A3</b> to create one
              </div>
            ) : records.map(r => {
              const ph = PHASES.find(p => p.key === r.current_phase);
              return (
                <div key={r.id} onClick={() => setSelected(r.id)}
                     style={{
                       padding: "12px 14px",
                       borderBottom: "1px solid #f1f5f9",
                       background: selected === r.id ? "#ede9fe" : "transparent",
                       cursor: "pointer",
                     }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 8 }}>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ fontSize: 11, fontFamily: "monospace", color: "#64748b" }}>{r.a3_no}</div>
                      <div style={{ fontSize: 13, fontWeight: 700, color: "#0f172a", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.title}</div>
                    </div>
                    <span style={{ fontSize: 9, fontWeight: 800, padding: "2px 8px", borderRadius: 99,
                                   background: `${STATUS_COLOR[r.status]}1a`, color: STATUS_COLOR[r.status] }}>
                      {r.status?.replace("_", " ")}
                    </span>
                  </div>
                  <div style={{ display: "flex", gap: 8, marginTop: 6, fontSize: 10, color: "#64748b" }}>
                    {ph && (
                      <span style={{ padding: "1px 6px", borderRadius: 99, background: `${ph.color}1a`, color: ph.color, fontWeight: 700 }}>
                        {ph.label}
                      </span>
                    )}
                    {r.line_name && <span>{r.line_name}</span>}
                    {r.severity && (
                      <span style={{ color: SEV_COLOR[r.severity] || "#94a3b8", fontWeight: 700 }}>{r.severity}</span>
                    )}
                    <span style={{ marginLeft: "auto" }}>{r.target_close_dt || ""}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Detail */}
        <div style={cardStyle}>
          {!selRec ? (
            <div style={{ padding: 60, textAlign: "center", color: "#94a3b8" }}>
              Select an A3 from the list
            </div>
          ) : (
            <A3Detail rec={selRec} token={token} onReload={load} showToast={showToast} />
          )}
        </div>
      </div>

      {showNew && (
        <NewA3Modal lines={lines} token={token}
                     onClose={() => setShowNew(false)}
                     onSaved={() => { setShowNew(false); load(); showToast("A3 created ✓"); }}
                     onError={(m) => showToast(m, "err")} />
      )}

      {toastV && <Toast msg={toastV.msg} kind={toastV.kind} />}
      <AIAssistant />
    </div>
  );
}

function A3Detail({ rec, token, onReload, showToast }) {
  const [f, setF] = useState(rec);
  const [log, setLog] = useState([]);
  const [note, setNote] = useState("");
  useEffect(() => { setF(rec); }, [rec]);
  useEffect(() => {
    api.get(`/api/pdca/${rec.id}/log`, token).then(setLog).catch(() => {});
  }, [rec.id, token]);

  const save = async () => {
    try {
      await api.put(`/api/pdca/${rec.id}`, {
        ...f,
        line_id: f.line_id ? Number(f.line_id) : null,
        capa_id: f.capa_id ? Number(f.capa_id) : null,
      }, token);
      showToast("Saved");
      onReload();
    } catch (e) { showToast(e.message, "err"); }
  };
  const advance = async () => {
    try {
      const r = await api.post(`/api/pdca/${rec.id}/advance`, { note }, token);
      showToast(`Phase → ${r.new_phase} ✓`);
      setNote("");
      onReload();
      api.get(`/api/pdca/${rec.id}/log`, token).then(setLog).catch(() => {});
    } catch (e) { showToast(e.message, "err"); }
  };
  const setStatus = async (st) => {
    try {
      await api.post(`/api/pdca/${rec.id}/status`, { status: st, note }, token);
      showToast(`Status → ${st}`);
      setNote("");
      onReload();
      api.get(`/api/pdca/${rec.id}/log`, token).then(setLog).catch(() => {});
    } catch (e) { showToast(e.message, "err"); }
  };
  const addNote = async () => {
    if (!note.trim()) return;
    try {
      await api.post(`/api/pdca/${rec.id}/note`, { status: f.status, note }, token);
      showToast("Note added");
      setNote("");
      api.get(`/api/pdca/${rec.id}/log`, token).then(setLog).catch(() => {});
    } catch (e) { showToast(e.message, "err"); }
  };

  const phIdx = PHASES.findIndex(p => p.key === f.current_phase);

  return (
    <div style={{ padding: 18 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 14, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 11, fontFamily: "monospace", color: "#64748b" }}>{f.a3_no}</div>
          <input value={f.title} onChange={e => setF({ ...f, title: e.target.value })}
                 style={{ ...glassSelect, fontSize: 18, fontWeight: 800, marginTop: 4 }} />
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          {["OPEN", "IN_PROGRESS", "ON_HOLD", "CLOSED", "ESCALATED"].map(st => (
            <button key={st} onClick={() => setStatus(st)} style={{
              padding: "5px 10px", fontSize: 10, fontWeight: 800,
              background: f.status === st ? STATUS_COLOR[st] : "#f8fafc",
              color: f.status === st ? "#fff" : "#64748b",
              border: `1px solid ${f.status === st ? STATUS_COLOR[st] : "#e2e8f0"}`,
              borderRadius: 6, cursor: "pointer", letterSpacing: ".02em",
            }}>{st.replace("_", " ")}</button>
          ))}
        </div>
      </div>

      {/* Phase progress strip */}
      <div style={{ display: "flex", gap: 4, marginTop: 18 }}>
        {PHASES.map((p, i) => {
          const done = i < phIdx;
          const cur  = i === phIdx;
          return (
            <div key={p.key} style={{
              flex: 1, padding: "10px 12px", borderRadius: 8,
              background: cur ? p.color : done ? `${p.color}33` : "#fff",
              color: cur ? "#fff" : done ? p.color : "#94a3b8",
              border: `1px solid ${cur ? p.color : done ? `${p.color}66` : "#e2e8f0"}`,
            }}>
              <div style={{ fontSize: 10, letterSpacing: ".1em", textTransform: "uppercase", fontWeight: 700 }}>
                {i + 1}. {p.label}
              </div>
              <div style={{ fontSize: 9, opacity: 0.8 }}>{p.desc}</div>
            </div>
          );
        })}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 18 }}>
        <Field label="Line">
          <select value={f.line_id || ""} onChange={e => setF({ ...f, line_id: e.target.value })} style={glassSelect}>
            <option value="">—</option>
          </select>
        </Field>
        <Field label="Owner">
          <input value={f.owner || ""} onChange={e => setF({ ...f, owner: e.target.value })} style={glassSelect} />
        </Field>
        <Field label="Category">
          <select value={f.category || ""} onChange={e => setF({ ...f, category: e.target.value })} style={glassSelect}>
            <option value="">—</option>
            {["QUALITY", "MAINTENANCE", "PRODUCTIVITY", "SAFETY", "OTHER"].map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </Field>
        <Field label="Severity">
          <select value={f.severity || ""} onChange={e => setF({ ...f, severity: e.target.value })} style={glassSelect}>
            <option value="">—</option>
            {["HIGH", "MED", "LOW"].map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </Field>
        <Field label="Target Close Date">
          <input type="date" value={f.target_close_dt || ""}
                 onChange={e => setF({ ...f, target_close_dt: e.target.value })} style={glassSelect} />
        </Field>
        <Field label="Machine">
          <input value={f.machine_name || ""} onChange={e => setF({ ...f, machine_name: e.target.value })}
                 style={glassSelect} />
        </Field>
      </div>

      <div style={{ display: "grid", gap: 12, marginTop: 16 }}>
        {[
          { f: "problem_text",    label: "🟦 PLAN — Problem statement", color: "#3b82f6" },
          { f: "root_cause",      label: "🔍 PLAN — Root cause analysis", color: "#3b82f6" },
          { f: "countermeasure",  label: "🟧 DO — Countermeasure / Action taken", color: "#f59e0b" },
          { f: "check_result",    label: "🟪 CHECK — Verification result / Metric", color: "#8b5cf6" },
          { f: "act_standardise", label: "🟩 ACT — Standardise / Lessons", color: "#10b981" },
        ].map(s => (
          <div key={s.f} style={{ background: "#f8fafc", border: "1px solid #e2e8f0", borderLeft: `4px solid ${s.color}`, borderRadius: 10, padding: 12 }}>
            <div style={{ fontSize: 10, fontWeight: 800, color: s.color, letterSpacing: ".08em", textTransform: "uppercase", marginBottom: 6 }}>
              {s.label}
            </div>
            <textarea rows={3} value={f[s.f] || ""} onChange={e => setF({ ...f, [s.f]: e.target.value })}
                      placeholder="..."
                      style={{ ...glassSelect, resize: "vertical", minHeight: 70, background: "#fff" }} />
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
        <button onClick={save} style={glassBtn}>💾 Save</button>
        {f.current_phase !== "ACT" && (
          <button onClick={advance} style={primaryBtn}>
            ⏩ Advance to {PHASES[Math.min(phIdx + 1, PHASES.length - 1)].label}
          </button>
        )}
      </div>

      {/* Note + timeline */}
      <div style={{ marginTop: 18 }}>
        <div style={{ display: "flex", gap: 8 }}>
          <input value={note} onChange={e => setNote(e.target.value)}
                 placeholder="Add a note / phase comment..."
                 style={{ ...glassSelect, flex: 1 }} />
          <button onClick={addNote} disabled={!note.trim()} style={glassBtn}>+ Note</button>
        </div>
        <div className="col-scroll" style={{ maxHeight: 220, overflowY: "auto", marginTop: 10 }}>
          {log.length === 0 ? (
            <div style={{ padding: 14, color: "#94a3b8", fontSize: 12 }}>No activity yet</div>
          ) : log.map(l => (
            <div key={l.id} style={{
              padding: "8px 10px", borderBottom: "1px solid #f1f5f9",
              fontSize: 11, color: "#0f172a",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ fontWeight: 700, color: "#64748b" }}>
                  {l.event_type === "PHASE_DONE" && l.from_phase
                    ? `${l.from_phase} → ${l.to_phase}` : l.event_type}
                </span>
                <span style={{ color: "#94a3b8" }}>{l.event_at ? new Date(l.event_at).toLocaleString() : ""}</span>
              </div>
              <div style={{ marginTop: 2 }}>{l.note}</div>
              <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 2 }}>by {l.event_by}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function NewA3Modal({ lines, token, onClose, onSaved, onError }) {
  const [f, setF] = useState({
    title: "", line_id: "", machine_name: "", category: "QUALITY", severity: "MED",
    problem_text: "", target_close_dt: "",
  });
  const save = async () => {
    if (!f.title.trim()) { onError("Title is required"); return; }
    try {
      await api.post("/api/pdca", {
        ...f,
        line_id: f.line_id ? Number(f.line_id) : null,
      }, token);
      onSaved();
    } catch (e) { onError(e.message); }
  };
  return (
    <div onClick={onClose} style={modalBackdrop}>
      <div onClick={e => e.stopPropagation()} style={modalBox}>
        <div style={{ fontSize: 17, fontWeight: 800, marginBottom: 14, color: "#0f172a" }}>New A3 / PDCA</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <Field label="Title *" full>
            <input value={f.title} onChange={e => setF({ ...f, title: e.target.value })}
                   placeholder="e.g. Spike in NG on Final Inspection" style={glassSelect} />
          </Field>
          <Field label="Line">
            <select value={f.line_id} onChange={e => setF({ ...f, line_id: e.target.value })} style={glassSelect}>
              <option value="">—</option>
              {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
            </select>
          </Field>
          <Field label="Machine">
            <input value={f.machine_name} onChange={e => setF({ ...f, machine_name: e.target.value })} style={glassSelect} />
          </Field>
          <Field label="Category">
            <select value={f.category} onChange={e => setF({ ...f, category: e.target.value })} style={glassSelect}>
              {["QUALITY", "MAINTENANCE", "PRODUCTIVITY", "SAFETY", "OTHER"].map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </Field>
          <Field label="Severity">
            <select value={f.severity} onChange={e => setF({ ...f, severity: e.target.value })} style={glassSelect}>
              {["HIGH", "MED", "LOW"].map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </Field>
          <Field label="Target Close Date">
            <input type="date" value={f.target_close_dt} onChange={e => setF({ ...f, target_close_dt: e.target.value })} style={glassSelect} />
          </Field>
          <Field label="Problem Statement" full>
            <textarea rows={3} value={f.problem_text} onChange={e => setF({ ...f, problem_text: e.target.value })}
                      placeholder="What's wrong, where, since when..."
                      style={{ ...glassSelect, resize: "vertical" }} />
          </Field>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 18 }}>
          <button onClick={onClose} style={glassBtn}>Cancel</button>
          <button onClick={save} style={primaryBtn}>Create A3</button>
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
  padding: "10px 18px", fontSize: 12, fontWeight: 800,
  background: "linear-gradient(135deg, #6366f1, #8b5cf6)",
  color: "#fff", border: "none", borderRadius: 8,
  cursor: "pointer", letterSpacing: ".02em",
  boxShadow: "0 2px 8px rgba(99,102,241,.3)",
  fontFamily: "'Barlow',sans-serif",
};
const lblStyle = {
  fontSize: 10, fontWeight: 700, letterSpacing: ".08em",
  textTransform: "uppercase", color: "#64748b",
};
const modalBackdrop = {
  position: "fixed", inset: 0, background: "rgba(15,23,42,.4)",
  backdropFilter: "blur(4px)", zIndex: 1000,
  display: "flex", alignItems: "center", justifyContent: "center",
};
const modalBox = {
  background: "#fff",
  border: "1px solid #e2e8f0",
  borderRadius: 18, padding: 24, width: 640, maxWidth: "90vw",
  maxHeight: "85vh", overflowY: "auto",
  boxShadow: "0 24px 80px rgba(0,0,0,.18)",
};
