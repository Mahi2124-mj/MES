/* ════════════════════════════════════════════════════════════════════
 *  FiveSPage.jsx
 *  ────────────────────────────────────────────────────────────────────
 *  TPS 5S daily audit — per-line per-day checklist scored 0-5 across:
 *    Sort · Set in Order · Shine · Standardize · Sustain
 *
 *  Features:
 *    • Score each pillar with stars / number 0-5
 *    • Add remark per pillar
 *    • Photo upload per pillar (camera or file)
 *    • Monthly summary tab (Quality dept review)
 *    • Customisable checklist items per line
 * ════════════════════════════════════════════════════════════════════ */

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import AIAssistant from "../components/AIAssistant";
import PageTopbar from "../components/PageTopbar";

const PILLARS = [
  { key: "sort",          label: "Sort",          jp: "Seiri",     color: "#dc2626", icon: "🗑" },
  { key: "set_in_order",  label: "Set in Order",  jp: "Seiton",    color: "#d97706", icon: "📍" },
  { key: "shine",         label: "Shine",         jp: "Seiso",     color: "#0891b2", icon: "✨" },
  { key: "standardize",   label: "Standardize",   jp: "Seiketsu",  color: "#7c3aed", icon: "📋" },
  { key: "sustain",       label: "Sustain",       jp: "Shitsuke",  color: "#16a34a", icon: "🔄" },
];

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

export default function FiveSPage() {
  const { token, isAdmin } = useAuth();
  const [tab, setTab] = useState("today");
  const [toastV, setToastV] = useState(null);
  const showToast = (msg, kind = "ok") => {
    setToastV({ msg, kind });
    setTimeout(() => setToastV(null), 3500);
  };

  return (
    <div style={{
      minHeight: "100vh",
      background: "#f8fafc",
      fontFamily: "'Barlow',sans-serif", paddingBottom: 60, color: "#0f172a",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@700;800&display=swap');
        .fs-tab {
          padding: 12px 22px; font-size: 12px; font-weight: 700;
          letter-spacing: .08em; text-transform: uppercase;
          background: none; border: none; color: #64748b;
          border-bottom: 2px solid transparent; cursor: pointer;
          font-family: 'Barlow',sans-serif; transition: all .12s;
        }
        .fs-tab:hover { color: #334155; }
        .fs-tab.active { color: #0f172a; border-bottom-color: #16a34a; }
        .col-scroll::-webkit-scrollbar { width: 6px; }
        .col-scroll::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
      `}</style>

      {/* 2026-05-13 — standardised topbar */}
      <PageTopbar leading="5S" accent="Audit" />

      <div style={{ padding: "10px 48px 0", background: "#fff", borderBottom: "1px solid #e2e8f0" }}>
        <div style={{ fontSize: 12, color: "#64748b" }}>
          Sort · Set in Order · Shine · Standardize · Sustain · score 0-5 per pillar + photo
        </div>
        <div style={{ display: "flex", gap: 2, marginTop: 18 }}>
          {["today","summary","items"].map(t => (
            <button key={t} className={`fs-tab${tab===t?" active":""}`} onClick={() => setTab(t)}>
              {{today:"Today's Audit", summary:"Monthly Summary", items:"Checklist Items"}[t]}
            </button>
          ))}
        </div>
      </div>

      <div style={{ padding: "20px 48px" }}>
        {tab === "today"   && <TodayTab   token={token} showToast={showToast} />}
        {tab === "summary" && <SummaryTab token={token} showToast={showToast} />}
        {tab === "items"   && <ItemsTab   token={token} showToast={showToast} isAdmin={isAdmin} />}
      </div>

      {toastV && <Toast msg={toastV.msg} kind={toastV.kind} />}
      <AIAssistant />
    </div>
  );
}

/* ── TODAY's audit ─────────────────────────────────────────────── */
function TodayTab({ token, showToast }) {
  const [lines, setLines] = useState([]);
  const [lineId, setLineId] = useState("");
  const [auditDate, setAuditDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [scores, setScores]   = useState({});  // {pillar_key: 0-5}
  const [remarks, setRemarks] = useState({});  // {pillar_key: text}
  const [items, setItems]     = useState([]);
  const [audit, setAudit]     = useState(null);
  const [photos, setPhotos]   = useState([]);
  const fileRef = useRef();
  const [uploadPillar, setUploadPillar] = useState(null);

  useEffect(() => {
    api.get("/api/lines/", token).then(ls => {
      setLines(ls || []);
      if (!lineId && ls?.length) setLineId(ls[0].id);
    });
  }, [token]);

  const loadAudit = useCallback(async () => {
    if (!lineId) return;
    try {
      const r = await api.get(`/api/5s/audits?line_id=${lineId}&date_from=${auditDate}&date_to=${auditDate}`, token);
      const a = (r || [])[0];
      if (a) {
        setAudit(a);
        setScores({
          sort: a.sort_score, set_in_order: a.set_in_order_score,
          shine: a.shine_score, standardize: a.standardize_score, sustain: a.sustain_score,
        });
        setRemarks({
          sort: a.sort_remark || "", set_in_order: a.set_in_order_remark || "",
          shine: a.shine_remark || "", standardize: a.standardize_remark || "",
          sustain: a.sustain_remark || "",
        });
        // Load photos
        const ph = await api.get(`/api/5s/audits/${a.id}/photos`, token).catch(() => []);
        setPhotos(ph || []);
      } else {
        setAudit(null); setScores({}); setRemarks({}); setPhotos([]);
      }
      const its = await api.get(`/api/5s/items?line_id=${lineId}`, token);
      setItems(its || []);
    } catch (e) { showToast(`Load failed: ${e.message}`, "err"); }
  }, [token, lineId, auditDate]);

  useEffect(() => { loadAudit(); }, [loadAudit]);

  const save = async () => {
    try {
      const body = {
        line_id: Number(lineId),
        audit_date: auditDate,
      };
      PILLARS.forEach(p => {
        if (scores[p.key] !== undefined && scores[p.key] !== null && scores[p.key] !== "") {
          body[`${p.key}_score`] = Number(scores[p.key]);
        }
        if (remarks[p.key]) body[`${p.key}_remark`] = remarks[p.key];
      });
      const r = await api.post("/api/5s/audits", body, token);
      showToast("Saved ✓");
      loadAudit();
    } catch (e) { showToast(e.message, "err"); }
  };

  const onFilePicked = async (e) => {
    const file = e.target.files?.[0];
    if (!file || !uploadPillar || !audit?.id) {
      e.target.value = ""; return;
    }
    const fd = new FormData();
    fd.append("file", file);
    fd.append("pillar", uploadPillar);
    try {
      const res = await fetch(`/api/5s/audits/${audit.id}/photos`, {
        method: "POST", headers: { Authorization: `Bearer ${token}` }, body: fd,
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.detail || `HTTP ${res.status}`);
      }
      showToast("Photo uploaded ✓");
      loadAudit();
    } catch (e) { showToast(e.message, "err"); }
    finally { setUploadPillar(null); e.target.value = ""; }
  };

  const total = useMemo(() => {
    return PILLARS.reduce((sum, p) => sum + (Number(scores[p.key]) || 0), 0);
  }, [scores]);
  const totalPct = (total / 25) * 100;

  return (
    <div>
      <div style={{ display: "flex", gap: 12, marginBottom: 18, alignItems: "end", flexWrap: "wrap" }}>
        <Field label="Line">
          <select value={lineId} onChange={e => setLineId(Number(e.target.value))} style={{ ...glassSelect, minWidth: 180 }}>
            {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
          </select>
        </Field>
        <Field label="Date">
          <input type="date" value={auditDate} onChange={e => setAuditDate(e.target.value)} style={glassSelect} />
        </Field>
        <div style={{ flex: 1 }} />
        <div style={{
          padding: "12px 22px",
          background: total >= 20 ? "#dcfce7" : total >= 15 ? "#fef3c7" : "#fee2e2",
          border: `1px solid ${total >= 20 ? "#16a34a" : total >= 15 ? "#d97706" : "#dc2626"}`,
          borderRadius: 12, minWidth: 220, textAlign: "center",
        }}>
          <div style={{ fontSize: 10, letterSpacing: ".1em", textTransform: "uppercase", color: "#64748b", fontWeight: 700 }}>Total</div>
          <div style={{ fontSize: 32, fontWeight: 800, fontFamily: "'Barlow Condensed',sans-serif",
                         color: total >= 20 ? "#16a34a" : total >= 15 ? "#d97706" : "#dc2626", lineHeight: 1 }}>
            {total} <span style={{ fontSize: 14, color: "#64748b" }}>/ 25</span>
          </div>
          <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>{totalPct.toFixed(0)}%</div>
        </div>
        <button onClick={save} style={primaryBtn}>💾 Save Audit</button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 16 }}>
        {PILLARS.map(p => {
          const score = scores[p.key];
          const itemList = items.filter(i => i.pillar === p.key);
          const pillarPhotos = photos.filter(ph => ph.pillar === p.key);
          return (
            <div key={p.key} style={{
              ...cardStyle,
              borderTop: `3px solid ${p.color}`,
            }}>
              <div style={{ padding: 14, borderBottom: "1px solid #e2e8f0" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 800, color: p.color }}>
                      {p.icon} {p.label.toUpperCase()}
                    </div>
                    <div style={{ fontSize: 10, color: "#64748b" }}>{p.jp}</div>
                  </div>
                  <div style={{ display: "flex", gap: 4 }}>
                    {[0, 1, 2, 3, 4, 5].map(n => (
                      <button key={n}
                        onClick={() => setScores({ ...scores, [p.key]: n })}
                        style={{
                          width: 30, height: 30, borderRadius: 6,
                          border: `1px solid ${score === n ? p.color : "#e2e8f0"}`,
                          background: score === n ? p.color : "#fff",
                          color: score === n ? "#fff" : "#64748b",
                          fontSize: 12, fontWeight: 800, cursor: "pointer",
                        }}>{n}</button>
                    ))}
                  </div>
                </div>
              </div>
              <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
                {itemList.length > 0 && (
                  <div style={{ background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: 8, padding: 10 }}>
                    <div style={{ fontSize: 10, fontWeight: 700, color: "#64748b", letterSpacing: ".08em", textTransform: "uppercase", marginBottom: 6 }}>
                      Checklist
                    </div>
                    {itemList.map(it => (
                      <div key={it.id} style={{ fontSize: 11, color: "#475569", marginBottom: 4 }}>
                        • {it.item_text}
                      </div>
                    ))}
                  </div>
                )}
                <Field label="Remark">
                  <textarea rows={2} value={remarks[p.key] || ""}
                            onChange={e => setRemarks({ ...remarks, [p.key]: e.target.value })}
                            placeholder="Optional comment..."
                            style={{ ...glassSelect, resize: "vertical", minHeight: 50 }} />
                </Field>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <button onClick={() => {
                              if (!audit) { showToast("Save audit first to attach photos", "err"); return; }
                              setUploadPillar(p.key); fileRef.current?.click();
                          }}
                          style={glassBtn}>📷 Add Photo</button>
                  <span style={{ fontSize: 11, color: "#64748b" }}>
                    {pillarPhotos.length} photo{pillarPhotos.length !== 1 ? "s" : ""}
                  </span>
                </div>
                {pillarPhotos.length > 0 && (
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {pillarPhotos.map(ph => (
                      <img key={ph.id} src={`/api/5s/photos/${ph.id}`}
                           alt={ph.caption || ""}
                           style={{ width: 60, height: 60, objectFit: "cover", borderRadius: 6, border: `1px solid ${p.color}55` }} />
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <input ref={fileRef} type="file" accept="image/*" capture="environment"
             onChange={onFilePicked} style={{ display: "none" }} />
    </div>
  );
}

/* ── Monthly summary tab ───────────────────────────────────────── */
function SummaryTab({ token, showToast }) {
  const [yearMonth, setYearMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const [rows, setRows] = useState([]);

  useEffect(() => {
    api.get(`/api/5s/summary?year_month=${yearMonth}`, token)
      .then(setRows).catch(e => showToast(`Load failed: ${e.message}`, "err"));
  }, [token, yearMonth]);

  return (
    <div>
      <div style={{ display: "flex", gap: 10, marginBottom: 16, alignItems: "end" }}>
        <Field label="Month">
          <input type="month" value={yearMonth} onChange={e => setYearMonth(e.target.value)} style={glassSelect} />
        </Field>
      </div>
      <div style={cardStyle}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#0f172a" }}>
          <thead>
            <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
              <th style={thStyle}>Line</th>
              <th style={{ ...thStyle, textAlign: "right" }}>Audits</th>
              {PILLARS.map(p => (
                <th key={p.key} style={{ ...thStyle, textAlign: "right", color: p.color }}>{p.label}</th>
              ))}
              <th style={{ ...thStyle, textAlign: "right" }}>Total</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={PILLARS.length + 3} style={{ padding: 30, textAlign: "center", color: "#94a3b8" }}>
                No audits in this month
              </td></tr>
            ) : rows.map(r => {
              const tot = Number(r.total_avg || 0);
              const totColor = tot >= 20 ? "#16a34a" : tot >= 15 ? "#d97706" : "#dc2626";
              return (
                <tr key={r.line_id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                  <td style={{ ...tdStyle, fontWeight: 700 }}>{r.line_name || `Line #${r.line_id}`}</td>
                  <td style={{ ...tdStyle, textAlign: "right" }}>{r.audits}</td>
                  {PILLARS.map(p => (
                    <td key={p.key} style={{ ...tdStyle, textAlign: "right", color: p.color }}>{r[`${p.key}_avg`] ?? "—"}</td>
                  ))}
                  <td style={{ ...tdStyle, textAlign: "right", fontWeight: 800, color: totColor }}>{tot.toFixed(1)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Checklist items management tab ─────────────────────────────── */
function ItemsTab({ token, showToast, isAdmin }) {
  const [items, setItems] = useState([]);
  const [pillar, setPillar] = useState("sort");
  const [text, setText] = useState("");
  const load = useCallback(async () => {
    try { setItems(await api.get("/api/5s/items", token)); }
    catch (e) { showToast(`Load failed: ${e.message}`, "err"); }
  }, [token]);
  useEffect(() => { load(); }, [load]);

  const add = async () => {
    if (!text.trim()) { showToast("Enter item text", "err"); return; }
    try {
      await api.post("/api/5s/items", { pillar, item_text: text, display_order: items.length * 10 }, token);
      showToast("Added");
      setText(""); load();
    } catch (e) { showToast(e.message, "err"); }
  };
  const remove = async (id) => {
    if (!confirm("Remove this checklist item?")) return;
    try { await api.delete(`/api/5s/items/${id}`, token); showToast("Removed"); load(); }
    catch (e) { showToast(e.message, "err"); }
  };

  return (
    <div>
      {isAdmin && (
        <div style={{ ...cardStyle, padding: 16, marginBottom: 16, display: "grid", gridTemplateColumns: "180px 1fr auto", gap: 10, alignItems: "end" }}>
          <Field label="Pillar">
            <select value={pillar} onChange={e => setPillar(e.target.value)} style={glassSelect}>
              {PILLARS.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
            </select>
          </Field>
          <Field label="Checklist Item Text">
            <input value={text} onChange={e => setText(e.target.value)}
                   placeholder='e.g. "No oil drip on floor under conveyor"'
                   style={glassSelect} />
          </Field>
          <button onClick={add} style={primaryBtn}>+ Add</button>
        </div>
      )}
      <div style={cardStyle}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#0f172a" }}>
          <thead>
            <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
              <th style={thStyle}>Pillar</th>
              <th style={thStyle}>Item</th>
              <th style={thStyle}>Line</th>
              {isAdmin && <th style={thStyle}></th>}
            </tr>
          </thead>
          <tbody>
            {items.map(r => {
              const p = PILLARS.find(x => x.key === r.pillar);
              return (
                <tr key={r.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                  <td style={tdStyle}>
                    <span style={{ padding: "2px 8px", borderRadius: 99, fontSize: 10, fontWeight: 800,
                                   background: `${p?.color || "#94a3b8"}1a`, color: p?.color || "#94a3b8" }}>
                      {p?.icon} {p?.label}
                    </span>
                  </td>
                  <td style={tdStyle}>{r.item_text}</td>
                  <td style={tdStyle}>{r.line_id ? `Line #${r.line_id}` : "ALL"}</td>
                  {isAdmin && (
                    <td style={tdStyle}>
                      <button onClick={() => remove(r.id)}
                              style={{ ...glassBtn, padding: "3px 10px", fontSize: 10, color: "#dc2626", borderColor: "#fecaca" }}>Delete</button>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
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
  background: "linear-gradient(135deg, #10b981, #06b6d4)",
  color: "#fff", border: "none", borderRadius: 8,
  cursor: "pointer", letterSpacing: ".02em",
  boxShadow: "0 2px 8px rgba(16,185,129,.3)",
  fontFamily: "'Barlow',sans-serif",
};
const lblStyle = {
  fontSize: 10, fontWeight: 700, letterSpacing: ".08em",
  textTransform: "uppercase", color: "#64748b",
};
const thStyle = { padding: "11px 10px", fontSize: 10, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: "#64748b", background: "#f8fafc", textAlign: "left" };
const tdStyle = { padding: "9px 10px", fontSize: 12, color: "#0f172a" };
