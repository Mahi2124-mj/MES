/* ════════════════════════════════════════════════════════════════════
 *  KanbanPage.jsx
 *  ────────────────────────────────────────────────────────────────────
 *  TPS Kanban dispatch dashboard.  4 tabs:
 *    1. Dashboard       — live today snapshot (parts × 3 windows + month-to-date)
 *    2. Monthly Plan    — per-part per-month plan grid
 *    3. Model Links     — map PLC model_number → FG part (admin)
 *    4. FG Parts        — master CRUD + Excel import (admin)
 *
 *  Auto-fire watcher in the backend writes kanban_log entries at:
 *    12:00 PM, Shift A end, Shift B end
 *  using cycles produced × packing_std_qty.  Operator no longer enters
 *  kanban cards manually — system reads from PLC + applies math.
 * ════════════════════════════════════════════════════════════════════ */

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import AIAssistant from "../components/AIAssistant";
import PageTopbar from "../components/PageTopbar";

const WINDOWS = [
  { key: "12pm",    label: "12 PM",      api: "12PM"    },
  { key: "shift_a", label: "Shift A",    api: "SHIFT_A" },
  { key: "shift_b", label: "Shift B",    api: "SHIFT_B" },
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

export default function KanbanPage() {
  const { token, isAdmin } = useAuth();
  const [tab, setTab] = useState("dashboard");
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
        .kb-tab {
          padding: 12px 22px; font-size: 12px; font-weight: 700;
          letter-spacing: .08em; text-transform: uppercase;
          background: none; border: none; color: #64748b;
          border-bottom: 2px solid transparent; cursor: pointer;
          font-family: 'Barlow',sans-serif; transition: all .12s;
        }
        .kb-tab:hover { color: #334155; }
        .kb-tab.active { color: #0f172a; border-bottom-color: #d97706; }
        .col-scroll::-webkit-scrollbar { width: 6px; }
        .col-scroll::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
      `}</style>

      {/* 2026-05-13 — standardised topbar (matches Production Import/Export) */}
      <PageTopbar leading="Kanban" accent="Dispatch" />

      <div style={{ padding: "10px 48px 0", background: "#fff", borderBottom: "1px solid #e2e8f0" }}>
        <div style={{ fontSize: 12, color: "#64748b" }}>
          Auto-fire from production count · 3 windows/day · plan vs achieved · linewise dashboard
        </div>
        <div style={{ display: "flex", gap: 2, marginTop: 18 }}>
          {["dashboard","plan","links","parts"].map(t => (
            <button key={t} className={`kb-tab${tab===t?" active":""}`} onClick={() => setTab(t)}>
              {{dashboard:"Dashboard", plan:"Monthly Plan", links:"Model Links", parts:"FG Parts"}[t]}
            </button>
          ))}
        </div>
      </div>

      <div style={{ padding: "20px 48px" }}>
        {tab === "dashboard" && <DashboardTab token={token} showToast={showToast} />}
        {tab === "plan"      && <PlanTab      token={token} showToast={showToast} />}
        {tab === "links"     && <LinksTab     token={token} showToast={showToast} isAdmin={isAdmin} />}
        {tab === "parts"     && <PartsTab     token={token} showToast={showToast} isAdmin={isAdmin} />}
      </div>

      {toastV && <Toast msg={toastV.msg} kind={toastV.kind} />}
      <AIAssistant />
    </div>
  );
}

/* ── Dashboard tab ─────────────────────────────────────────────────── */
function DashboardTab({ token, showToast }) {
  const [lines, setLines] = useState([]);
  const [lineId, setLineId] = useState("");
  const [onDate, setOnDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!lineId) return;
    setLoading(true);
    try {
      const r = await api.get(`/api/kanban/dashboard?line_id=${lineId}&on_date=${onDate}`, token);
      setRows(r || []);
    } catch (e) { showToast(`Load failed: ${e.message}`, "err"); }
    finally { setLoading(false); }
  }, [token, lineId, onDate]);

  useEffect(() => {
    api.get("/api/lines/", token).then(ls => {
      setLines(ls || []);
      if (!lineId && ls?.length) setLineId(ls[0].id);
    });
  }, [token]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);

  const totals = useMemo(() => {
    const t = { plan: 0, dispatched: 0, today: 0, parts: rows.length };
    rows.forEach(r => {
      t.plan += Number(r.total_plan || 0);
      t.dispatched += Number(r.dispatched_mtd || 0);
      t.today += Number(r.total_today || 0);
    });
    t.pct = t.plan > 0 ? (t.dispatched / t.plan) * 100 : 0;
    return t;
  }, [rows]);

  return (
    <div>
      <div style={{ display: "flex", gap: 12, marginBottom: 16, alignItems: "end", flexWrap: "wrap" }}>
        <Field label="Line">
          <select value={lineId} onChange={e => setLineId(Number(e.target.value))} style={{ ...glassSelect, minWidth: 180 }}>
            {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
          </select>
        </Field>
        <Field label="Date">
          <input type="date" value={onDate} onChange={e => setOnDate(e.target.value)} style={glassSelect} />
        </Field>
        <button onClick={load} style={glassBtn}>↻ {loading ? "…" : "Refresh"}</button>
        <button onClick={async () => {
          try {
            const r = await api.post(`/api/kanban/fire-now?line_id=${lineId}`, {}, token);
            showToast(`Fired ${r.fired} window${r.fired !== 1 ? "s" : ""} ✓`);
            load();
          } catch (e) { showToast(e.message, "err"); }
        }} style={primaryBtn}>🔥 Fire Now</button>
        <div style={{ flex: 1 }} />
      </div>

      {/* KPI row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 14, marginBottom: 18 }}>
        <KPI label="FG Parts"          value={totals.parts}             color="#3b82f6" />
        <KPI label="Month Plan"        value={totals.plan.toLocaleString()} color="#f59e0b" />
        <KPI label="Dispatched MTD"    value={totals.dispatched.toLocaleString()} color="#10b981" hint={`${totals.pct.toFixed(1)}% achieved`} />
        <KPI label="Today Total"       value={totals.today.toLocaleString()} color="#8b5cf6" suffix="pcs" />
      </div>

      <div style={cardStyle}>
        <div style={{ maxHeight: "calc(100vh - 380px)", overflowY: "auto" }} className="col-scroll">
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#0f172a" }}>
            <thead style={{ position: "sticky", top: 0, background: "#f8fafc", zIndex: 1 }}>
              <tr style={{ borderBottom: "1px solid #e2e8f0" }}>
                <th style={thStyle}>Model</th>
                <th style={thStyle}>TBDI No.</th>
                <th style={thStyle}>Customer</th>
                <th style={{ ...thStyle, textAlign: "right" }}>Pack Qty</th>
                <th style={{ ...thStyle, textAlign: "right" }}>Month Plan</th>
                <th style={{ ...thStyle, textAlign: "right", color: "#db2777" }}>12 PM</th>
                <th style={{ ...thStyle, textAlign: "right", color: "#2563eb" }}>Shift A</th>
                <th style={{ ...thStyle, textAlign: "right", color: "#7c3aed" }}>Shift B</th>
                <th style={{ ...thStyle, textAlign: "right", color: "#059669" }}>Today</th>
                <th style={{ ...thStyle, textAlign: "right" }}>MTD</th>
                <th style={{ ...thStyle, textAlign: "right" }}>%</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={11} style={{ padding: 50, textAlign: "center", color: "#64748b" }}>
                  No FG parts for this line · Import Excel from FG Parts tab
                </td></tr>
              ) : rows.map(r => {
                const pct = r.total_plan > 0 ? (r.dispatched_mtd / r.total_plan) * 100 : 0;
                const pctColor = pct >= 100 ? "#10b981" : pct >= 80 ? "#f59e0b" : pct > 0 ? "#3b82f6" : "#64748b";
                return (
                  <tr key={r.fg_part_id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                    <td style={{ ...tdStyle, fontWeight: 700, color: "#0f172a" }}>{r.model || "—"}</td>
                    <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: 11 }}>
                      <div>{r.tbdi_part_no}</div>
                      {r.description && <div style={{ fontSize: 10, color: "#64748b", maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.description}</div>}
                    </td>
                    <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: 11, color: "#64748b" }}>{r.customer_part_no || "—"}</td>
                    <td style={{ ...tdStyle, textAlign: "right", color: "#64748b" }}>{r.packing_std_qty}</td>
                    <td style={{ ...tdStyle, textAlign: "right", fontWeight: 700, color: "#f59e0b" }}>{Number(r.total_plan || 0).toLocaleString()}</td>
                    <td style={{ ...tdStyle, textAlign: "right", color: r.dispatch_12pm > 0 ? "#db2777" : "#94a3b8" }}>{r.dispatch_12pm || "—"}</td>
                    <td style={{ ...tdStyle, textAlign: "right", color: r.dispatch_a > 0 ? "#2563eb" : "#94a3b8" }}>{r.dispatch_a || "—"}</td>
                    <td style={{ ...tdStyle, textAlign: "right", color: r.dispatch_b > 0 ? "#7c3aed" : "#94a3b8" }}>{r.dispatch_b || "—"}</td>
                    <td style={{ ...tdStyle, textAlign: "right", fontWeight: 700, color: r.total_today > 0 ? "#10b981" : "#94a3b8" }}>
                      {r.total_today > 0 ? r.total_today.toLocaleString() : "—"}
                    </td>
                    <td style={{ ...tdStyle, textAlign: "right", fontWeight: 700, color: "#0f172a" }}>{Number(r.dispatched_mtd || 0).toLocaleString()}</td>
                    <td style={{ ...tdStyle, textAlign: "right", fontWeight: 800, color: pctColor }}>{pct.toFixed(1)}%</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div style={{ marginTop: 14, fontSize: 11, color: "#64748b" }}>
        🤖 Auto-fires every minute at window boundaries (12:00, Shift A end, Shift B end) · kanban_count = cycles ÷ packing_qty
      </div>
    </div>
  );
}

/* ── Plan tab ──────────────────────────────────────────────────────── */
function PlanTab({ token, showToast }) {
  const [yearMonth, setYearMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const [lines, setLines] = useState([]);
  const [lineId, setLineId] = useState("");
  const [rows, setRows] = useState([]);

  useEffect(() => {
    api.get("/api/lines/", token).then(ls => {
      setLines(ls || []);
      if (!lineId && ls?.length) setLineId(ls[0].id);
    });
  }, [token]);

  const load = useCallback(async () => {
    if (!lineId) return;
    try {
      const r = await api.get(`/api/kanban/monthly-plan?year_month=${yearMonth}&line_id=${lineId}`, token);
      setRows(r || []);
    } catch (e) { showToast(`Load failed: ${e.message}`, "err"); }
  }, [token, lineId, yearMonth]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div style={{ display: "flex", gap: 10, marginBottom: 16, alignItems: "end" }}>
        <Field label="Line">
          <select value={lineId} onChange={e => setLineId(Number(e.target.value))} style={{ ...glassSelect, minWidth: 180 }}>
            {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
          </select>
        </Field>
        <Field label="Month">
          <input type="month" value={yearMonth} onChange={e => setYearMonth(e.target.value)} style={glassSelect} />
        </Field>
      </div>

      <div style={cardStyle}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#0f172a" }}>
          <thead>
            <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
              <th style={thStyle}>Model</th>
              <th style={thStyle}>TBDI No.</th>
              <th style={thStyle}>Description</th>
              <th style={{ ...thStyle, textAlign: "right" }}>Pack</th>
              <th style={{ ...thStyle, textAlign: "right" }}>Shift A</th>
              <th style={{ ...thStyle, textAlign: "right" }}>Shift B</th>
              <th style={{ ...thStyle, textAlign: "right" }}>Total</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={7} style={{ padding: 40, textAlign: "center", color: "#64748b" }}>No plan for this month/line · Import Excel</td></tr>
            ) : rows.map(r => (
              <tr key={r.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                <td style={{ ...tdStyle, fontWeight: 700 }}>{r.model || "—"}</td>
                <td style={{ ...tdStyle, fontFamily: "monospace" }}>{r.tbdi_part_no}</td>
                <td style={{ ...tdStyle, fontSize: 11, color: "#475569", maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.description}</td>
                <td style={{ ...tdStyle, textAlign: "right", color: "#64748b" }}>{r.packing_std_qty}</td>
                <td style={{ ...tdStyle, textAlign: "right", color: "#2563eb" }}>{Number(r.shift_a_plan || 0).toLocaleString()}</td>
                <td style={{ ...tdStyle, textAlign: "right", color: "#7c3aed" }}>{Number(r.shift_b_plan || 0).toLocaleString()}</td>
                <td style={{ ...tdStyle, textAlign: "right", fontWeight: 700, color: "#f59e0b" }}>{Number(r.total_plan || 0).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Model Links tab ───────────────────────────────────────────────── */
function LinksTab({ token, showToast, isAdmin }) {
  const [lines, setLines] = useState([]);
  const [lineId, setLineId] = useState("");
  const [models, setModels] = useState([]);
  const [parts, setParts] = useState([]);
  const [links, setLinks] = useState([]);
  const [modelN, setModelN] = useState("");
  const [partId, setPartId] = useState("");

  const load = useCallback(async () => {
    if (!lineId) return;
    try {
      const [ms, ps, ls] = await Promise.all([
        api.get(`/api/lines/${lineId}/models`, token).catch(() => []),
        api.get(`/api/kanban/parts?line_id=${lineId}`, token),
        api.get(`/api/kanban/model-links?line_id=${lineId}`, token),
      ]);
      setModels(Array.isArray(ms) ? ms : []);
      setParts(ps || []);
      setLinks(ls || []);
    } catch (e) { showToast(`Load failed: ${e.message}`, "err"); }
  }, [token, lineId]);

  useEffect(() => {
    api.get("/api/lines/", token).then(ls => {
      setLines(ls || []);
      if (!lineId && ls?.length) setLineId(ls[0].id);
    });
  }, [token]);

  useEffect(() => { load(); }, [load]);

  const link = async () => {
    if (!lineId || !modelN || !partId) { showToast("Pick line, model & FG part", "err"); return; }
    try {
      await api.post("/api/kanban/model-links", {
        line_id: Number(lineId),
        model_number: Number(modelN),
        fg_part_id: Number(partId),
      }, token);
      showToast("Linked ✓");
      setModelN(""); setPartId("");
      load();
    } catch (e) { showToast(e.message, "err"); }
  };

  const unlink = async (id) => {
    if (!confirm("Remove this model link?")) return;
    try { await api.delete(`/api/kanban/model-links/${id}`, token); showToast("Removed"); load(); }
    catch (e) { showToast(e.message, "err"); }
  };

  return (
    <div>
      <div style={{ display: "flex", gap: 10, marginBottom: 16, alignItems: "end" }}>
        <Field label="Line">
          <select value={lineId} onChange={e => setLineId(Number(e.target.value))} style={{ ...glassSelect, minWidth: 180 }}>
            {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
          </select>
        </Field>
      </div>

      {isAdmin && (
        <div style={{ ...cardStyle, padding: 16, marginBottom: 16, display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 10, alignItems: "end" }}>
          <Field label="PLC Model #">
            <select value={modelN} onChange={e => setModelN(e.target.value)} style={glassSelect}>
              <option value="">— select —</option>
              {models.map(m => <option key={m.model_number || m.id} value={m.model_number || m.id}>
                {(m.model_number ?? m.id)}: {m.model_name || m.name || ""}
              </option>)}
              {/* fallback: free input via numbers 1-50 if endpoint had nothing */}
              {models.length === 0 && Array.from({ length: 30 }, (_, i) => i + 1).map(n => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </Field>
          <Field label="FG Part">
            <select value={partId} onChange={e => setPartId(e.target.value)} style={glassSelect}>
              <option value="">— select FG part —</option>
              {parts.map(p => <option key={p.id} value={p.id}>
                {p.tbdi_part_no} · {p.description?.slice(0, 40)}
              </option>)}
            </select>
          </Field>
          <button onClick={link} style={primaryBtn}>+ Link</button>
        </div>
      )}

      <div style={cardStyle}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#0f172a" }}>
          <thead>
            <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
              <th style={thStyle}>Model #</th>
              <th style={thStyle}>Model Name (PLC)</th>
              <th style={thStyle}>FG Part</th>
              <th style={thStyle}>Description</th>
              <th style={{ ...thStyle, textAlign: "right" }}>Pack Qty</th>
              {isAdmin && <th style={thStyle}></th>}
            </tr>
          </thead>
          <tbody>
            {links.length === 0 ? (
              <tr><td colSpan={isAdmin ? 6 : 5} style={{ padding: 30, textAlign: "center", color: "#64748b" }}>
                No links yet. Link a PLC model to an FG part so the auto-fire watcher knows what to count.
              </td></tr>
            ) : links.map(l => (
              <tr key={l.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                <td style={{ ...tdStyle, fontWeight: 800, fontSize: 14 }}>{l.model_number}</td>
                <td style={tdStyle}>{l.model_name || "—"}</td>
                <td style={{ ...tdStyle, fontFamily: "monospace" }}>{l.tbdi_part_no}</td>
                <td style={{ ...tdStyle, fontSize: 11, color: "#64748b", maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{l.description}</td>
                <td style={{ ...tdStyle, textAlign: "right" }}>{l.packing_std_qty}</td>
                {isAdmin && (
                  <td style={tdStyle}>
                    <button onClick={() => unlink(l.id)} style={{ ...glassBtn, padding: "4px 10px", fontSize: 11, color: "#dc2626", borderColor: "#fecaca" }}>Remove</button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── FG Parts tab ──────────────────────────────────────────────────── */
function PartsTab({ token, showToast, isAdmin }) {
  const [rows, setRows] = useState([]);
  const [filter, setFilter] = useState("");
  const fileRef = useRef();
  const [sheets, setSheets] = useState("APR-26,MAY-26");
  const [lineName, setLineName] = useState("YNC-SS");

  const load = useCallback(async () => {
    try { setRows(await api.get("/api/kanban/parts", token)); }
    catch (e) { showToast(`Load failed: ${e.message}`, "err"); }
  }, [token]);
  useEffect(() => { load(); }, [load]);

  const filtered = filter
    ? rows.filter(r => {
        const q = filter.toLowerCase();
        return (r.tbdi_part_no || "").toLowerCase().includes(q)
            || (r.description || "").toLowerCase().includes(q)
            || (r.customer_part_no || "").toLowerCase().includes(q)
            || (r.model || "").toLowerCase().includes(q);
      })
    : rows;

  const dlTemplate = async () => {
    try {
      const res = await fetch("/api/kanban/parts/template", { headers: { Authorization: `Bearer ${token}` } });
      const blob = await res.blob();
      const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
      a.download = "kanban_parts_template.xlsx"; a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { showToast(e.message, "err"); }
  };
  const handleImport = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData(); fd.append("file", file);
    const qs = new URLSearchParams();
    if (sheets) qs.set("sheets", sheets);
    if (lineName) qs.set("line_name", lineName);
    try {
      const res = await fetch(`/api/kanban/import?${qs}`, {
        method: "POST", headers: { Authorization: `Bearer ${token}` }, body: fd,
      });
      const j = await res.json();
      if (!res.ok) throw new Error(j.detail || `HTTP ${res.status}`);
      showToast(`Imported · ${j.inserted} new, ${j.updated} updated, ${j.plan_rows} plan rows`);
      if (j.errors?.length) console.warn("Import errors:", j.errors);
      load();
    } catch (e) { showToast(`Import failed: ${e.message}`, "err"); }
    finally { e.target.value = ""; }
  };

  return (
    <div>
      <div style={{ display: "flex", gap: 10, marginBottom: 14, alignItems: "center", flexWrap: "wrap" }}>
        <input placeholder="Search by part no / description / customer / model..."
               value={filter} onChange={e => setFilter(e.target.value)}
               style={{ ...glassSelect, flex: 1, minWidth: 300 }} />
        {isAdmin && (
          <>
            <Field label="Sheets">
              <input value={sheets} onChange={e => setSheets(e.target.value)}
                     placeholder="APR-26,MAY-26" style={{ ...glassSelect, width: 180 }} />
            </Field>
            <Field label="Line for new parts">
              <input value={lineName} onChange={e => setLineName(e.target.value)}
                     placeholder="YNC-SS" style={{ ...glassSelect, width: 140 }} />
            </Field>
            <button onClick={dlTemplate} style={glassBtn}>📄 Template</button>
            <button onClick={() => fileRef.current?.click()} style={glassBtn}>⬆ Import Excel</button>
            <input ref={fileRef} type="file" accept=".xlsx,.xls" onChange={handleImport} style={{ display: "none" }} />
          </>
        )}
      </div>

      <div style={{ fontSize: 11, color: "#64748b", marginBottom: 10 }}>
        {filtered.length} of {rows.length} FG parts shown
      </div>

      <div style={cardStyle}>
        <div style={{ maxHeight: "calc(100vh - 320px)", overflowY: "auto" }} className="col-scroll">
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#0f172a" }}>
            <thead style={{ position: "sticky", top: 0, background: "#f8fafc" }}>
              <tr style={{ borderBottom: "1px solid #e2e8f0" }}>
                <th style={thStyle}>Model</th>
                <th style={thStyle}>TBDI Part</th>
                <th style={thStyle}>New Part</th>
                <th style={thStyle}>Customer Part</th>
                <th style={thStyle}>Description</th>
                <th style={{ ...thStyle, textAlign: "right" }}>Pack Qty</th>
                <th style={thStyle}>Line</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(r => (
                <tr key={r.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                  <td style={{ ...tdStyle, fontWeight: 700 }}>{r.model || "—"}</td>
                  <td style={{ ...tdStyle, fontFamily: "monospace" }}>{r.tbdi_part_no}</td>
                  <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: 11, color: "#64748b" }}>{r.tbdi_new_part_no || "—"}</td>
                  <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: 11, color: "#64748b" }}>{r.customer_part_no || "—"}</td>
                  <td style={{ ...tdStyle, fontSize: 11, color: "#475569", maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.description}</td>
                  <td style={{ ...tdStyle, textAlign: "right", fontWeight: 700, color: "#10b981" }}>{r.packing_std_qty}</td>
                  <td style={tdStyle}>{r.line_name || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ── helpers ──────────────────────────────────────────────────────── */
function Field({ label, children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <label style={lblStyle}>{label}</label>
      {children}
    </div>
  );
}
function KPI({ label, value, color, hint, suffix }) {
  return (
    <div style={{
      background: "#fff",
      border: "1px solid #e2e8f0",
      borderTop: `3px solid ${color}`,
      borderRadius: 12, padding: 14,
      boxShadow: "0 1px 3px rgba(0,0,0,.04)",
    }}>
      <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".12em", textTransform: "uppercase", color: "#64748b" }}>{label}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 4, marginTop: 4 }}>
        <span style={{ fontSize: 26, fontWeight: 800, color, fontFamily: "'Barlow Condensed',sans-serif", lineHeight: 1.1 }}>{value}</span>
        {suffix && <span style={{ fontSize: 11, color: "#64748b" }}>{suffix}</span>}
      </div>
      {hint && <div style={{ fontSize: 10, color: "#64748b", marginTop: 2 }}>{hint}</div>}
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
  color: "#0f172a", outline: "none",
  fontFamily: "'Barlow',sans-serif",
};
const glassBtn = {
  padding: "8px 14px", fontSize: 12, fontWeight: 700,
  background: "#f8fafc", color: "#334155",
  border: "1px solid #e2e8f0", borderRadius: 8,
  cursor: "pointer", fontFamily: "'Barlow',sans-serif",
};
const primaryBtn = {
  padding: "10px 16px", fontSize: 12, fontWeight: 800,
  background: "linear-gradient(135deg, #f59e0b, #fb923c)",
  color: "#fff", border: "none", borderRadius: 8,
  cursor: "pointer", letterSpacing: ".02em",
  boxShadow: "0 2px 8px rgba(245,158,11,.3)",
  fontFamily: "'Barlow',sans-serif",
};
const lblStyle = {
  fontSize: 10, fontWeight: 700, letterSpacing: ".08em",
  textTransform: "uppercase", color: "#64748b",
};
const thStyle = { padding: "11px 10px", fontSize: 10, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: "#64748b", background: "#f8fafc", textAlign: "left" };
const tdStyle = { padding: "9px 10px", fontSize: 12, color: "#0f172a" };
