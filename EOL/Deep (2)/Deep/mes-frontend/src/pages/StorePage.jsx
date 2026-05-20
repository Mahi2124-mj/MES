/* ════════════════════════════════════════════════════════════════════
 *  StorePage.jsx
 *  ────────────────────────────────────────────────────────────────────
 *  Single-page Store module with 4 tabs:
 *    1. Dashboard   — stock health, low/high alerts, today's in/out
 *    2. Stock       — current balance for every material with status
 *    3. GRN         — log incoming material from supplier
 *    4. Issue       — issue material to a production line
 *    5. Materials   — master CRUD with Excel import/export (admin)
 *
 *  Stock balance is computed by the backend as SUM(grn) − SUM(issues),
 *  so this page never tries to keep a denormalised balance in sync.
 * ════════════════════════════════════════════════════════════════════ */

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import AIAssistant from "../components/AIAssistant";
import PageTopbar from "../components/PageTopbar";

const MAT_TYPES = [
  { v: "RM",   label: "Raw Material" },
  { v: "FG",   label: "Finished Good" },
  { v: "PKG",  label: "Packaging" },
  { v: "CONS", label: "Consumable" },
];
const TYPE_COLOR = (t) => ({
  RM: "#3b82f6", FG: "#10b981", PKG: "#f59e0b", CONS: "#8b5cf6",
}[t] || "#94a3b8");
const STATUS_COLOR = (s) => ({
  OK: "#10b981", LOW: "#f59e0b", OUT: "#ef4444", HIGH: "#3b82f6",
}[s] || "#94a3b8");

function Toast({ msg, kind }) {
  if (!msg) return null;
  const c = kind === "err" ? "#dc2626" : "#16a34a";
  return (
    <div style={{
      position: "fixed", bottom: 28, right: 28, zIndex: 10000,
      background: "#fff",
      border: `1px solid ${c}`, color: "#0f172a",
      padding: "12px 18px", borderRadius: 12, fontSize: 13, fontWeight: 600,
      boxShadow: "0 10px 40px rgba(0,0,0,.12)",
      maxWidth: 400,
    }}>
      <span style={{ color: c, marginRight: 8 }}>{kind === "err" ? "✗" : "✓"}</span>
      {msg}
    </div>
  );
}

export default function StorePage() {
  const { token, user, isAdmin } = useAuth();
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
        .store-tab {
          padding: 12px 22px; font-size: 12px; font-weight: 700;
          letter-spacing: .08em; text-transform: uppercase;
          background: none; border: none; color: #64748b;
          border-bottom: 2px solid transparent; cursor: pointer;
          font-family: 'Barlow',sans-serif; transition: all .12s;
        }
        .store-tab:hover { color: #0f172a; }
        .store-tab.active { color: #0f172a; border-bottom-color: #1e40af; }
        .col-scroll::-webkit-scrollbar { width: 6px; }
        .col-scroll::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
      `}</style>

      {/* 2026-05-13 — standardised topbar */}
      <PageTopbar leading="Store" accent="Management" />

      <div style={{ padding: "10px 48px 0", background: "#fff", borderBottom: "1px solid #e2e8f0" }}>
        <div style={{ fontSize: 12, color: "#64748b" }}>
          Material master · stock balance · GRN (in) · Issue to line (out)
        </div>
        <div style={{ display: "flex", gap: 2, marginTop: 18 }}>
          {["dashboard","stock","grn","issue","materials"].map(t => (
            <button key={t} className={`store-tab${tab===t?" active":""}`} onClick={() => setTab(t)}>
              {{dashboard:"Dashboard", stock:"Stock", grn:"GRN In", issue:"Issue", materials:"Materials"}[t]}
            </button>
          ))}
        </div>
      </div>

      <div style={{ padding: "20px 48px" }}>
        {tab === "dashboard" && <DashboardTab token={token} showToast={showToast} />}
        {tab === "stock"     && <StockTab     token={token} showToast={showToast} />}
        {tab === "grn"       && <GRNTab       token={token} user={user} showToast={showToast} />}
        {tab === "issue"     && <IssueTab     token={token} user={user} showToast={showToast} />}
        {tab === "materials" && <MaterialsTab token={token} showToast={showToast} isAdmin={isAdmin} />}
      </div>

      {toastV && <Toast msg={toastV.msg} kind={toastV.kind} />}
      <AIAssistant />
    </div>
  );
}

/* ── Dashboard ──────────────────────────────────────────────────── */
function DashboardTab({ token, showToast }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    api.get("/api/store/dashboard", token)
       .then(setData)
       .catch(e => showToast(`Load failed: ${e.message}`, "err"));
  }, [token]);

  if (!data) return <div style={{ color: "#64748b", padding: 40 }}>Loading…</div>;

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 16, marginBottom: 24 }}>
        <KPITile label="Materials"     value={data.total_materials}            color="#3b82f6" />
        <KPITile label="GRN Today"     value={data.in_today.toFixed(0)}        color="#10b981" suffix="qty in" />
        <KPITile label="Issued Today"  value={data.out_today.toFixed(0)}       color="#f59e0b" suffix="qty out" />
        <KPITile label="Low / Out"     value={data.low_count}                  color={data.low_count>0?"#ef4444":"#94a3b8"} suffix="alerts" />
        <KPITile label="Above Max"     value={data.high_count}                 color={data.high_count>0?"#3b82f6":"#94a3b8"} suffix="overstock" />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <AlertCard title="Low / Out of Stock" rows={data.low_materials || []} type="low" />
        <AlertCard title="Overstock" rows={data.high_materials || []} type="high" />
      </div>
    </div>
  );
}

function KPITile({ label, value, color, suffix }) {
  return (
    <div style={{
      background: "#fff",
      border: "1px solid #e2e8f0",
      borderTop: `3px solid ${color}`,
      borderRadius: 14, padding: 18,
      boxShadow: "0 1px 3px rgba(0,0,0,.04)",
    }}>
      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: ".12em", textTransform: "uppercase", color: "#64748b" }}>{label}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginTop: 6 }}>
        <span style={{ fontSize: 32, fontWeight: 800, color, fontFamily: "'Barlow Condensed',sans-serif", lineHeight: 1 }}>{value}</span>
        {suffix && <span style={{ fontSize: 11, color: "#94a3b8" }}>{suffix}</span>}
      </div>
    </div>
  );
}

function AlertCard({ title, rows, type }) {
  const color = type === "low" ? "#ef4444" : "#3b82f6";
  return (
    <div style={{
      background: "#fff",
      border: "1px solid #e2e8f0", borderRadius: 14,
      boxShadow: "0 1px 3px rgba(0,0,0,.04)",
    }}>
      <div style={{ padding: 14, borderBottom: "1px solid #e2e8f0" }}>
        <div style={{ fontSize: 13, fontWeight: 800, color: "#0f172a" }}>{title}</div>
        <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>{rows.length} material{rows.length !== 1 ? "s" : ""}</div>
      </div>
      <div className="col-scroll" style={{ padding: 10, maxHeight: 320, overflowY: "auto" }}>
        {rows.length === 0 ? (
          <div style={{ padding: 20, textAlign: "center", color: "#94a3b8", fontSize: 12 }}>All clear ✓</div>
        ) : (
          rows.map(r => (
            <div key={r.id} style={{
              padding: "8px 10px", borderRadius: 8, marginBottom: 5,
              background: "#f8fafc", border: `1px solid ${color}33`,
              display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8,
            }}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: "#0f172a", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {r.code} — {r.name}
                </div>
                <div style={{ fontSize: 10, color: "#64748b" }}>
                  Min {r.min_stock} · Max {r.max_stock} · {r.uom}
                </div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 14, fontWeight: 800, color, fontFamily: "'Barlow Condensed',sans-serif" }}>
                  {Number(r.balance || 0).toFixed(1)}
                </div>
                <div style={{ fontSize: 9, color, fontWeight: 700 }}>{r.status}</div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

/* ── Stock Tab ──────────────────────────────────────────────────── */
function StockTab({ token, showToast }) {
  const [rows, setRows] = useState([]);
  const [filterType, setFilterType] = useState("");
  const [search, setSearch] = useState("");

  const load = useCallback(() => {
    const q = filterType ? `?mat_type=${filterType}` : "";
    api.get(`/api/store/stock${q}`, token)
       .then(setRows)
       .catch(e => showToast(`Load failed: ${e.message}`, "err"));
  }, [token, filterType]);

  useEffect(() => { load(); }, [load]);

  const filtered = rows.filter(r => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (r.code || "").toLowerCase().includes(q) || (r.name || "").toLowerCase().includes(q);
  });

  return (
    <div>
      <div style={{ display: "flex", gap: 10, marginBottom: 14, alignItems: "center" }}>
        <select value={filterType} onChange={e => setFilterType(e.target.value)} style={glassSelect}>
          <option value="">All Types</option>
          {MAT_TYPES.map(t => <option key={t.v} value={t.v}>{t.label}</option>)}
        </select>
        <input placeholder="Search by code or name..."
               value={search} onChange={e => setSearch(e.target.value)}
               style={{ ...glassSelect, flex: 1, maxWidth: 400 }} />
        <button onClick={load} style={glassBtn}>↻ Refresh</button>
      </div>

      <div style={cardStyle}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#0f172a" }}>
          <thead>
            <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
              <th style={thStyle}>Code</th>
              <th style={thStyle}>Material</th>
              <th style={thStyle}>Type</th>
              <th style={thStyle}>UOM</th>
              <th style={{ ...thStyle, textAlign: "right" }}>In</th>
              <th style={{ ...thStyle, textAlign: "right" }}>Out</th>
              <th style={{ ...thStyle, textAlign: "right" }}>Balance</th>
              <th style={{ ...thStyle, textAlign: "right" }}>Min / Max</th>
              <th style={thStyle}>Status</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr><td colSpan={9} style={{ padding: 30, textAlign: "center", color: "#94a3b8" }}>No materials</td></tr>
            ) : filtered.map(r => (
              <tr key={r.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                <td style={{ ...tdStyle, fontFamily: "monospace" }}>{r.code}</td>
                <td style={{ ...tdStyle, fontWeight: 700 }}>{r.name}</td>
                <td style={tdStyle}>
                  <span style={{ padding: "2px 8px", borderRadius: 99, fontSize: 10, fontWeight: 800,
                                 background: `${TYPE_COLOR(r.mat_type)}1a`, color: TYPE_COLOR(r.mat_type) }}>
                    {r.mat_type}
                  </span>
                </td>
                <td style={tdStyle}>{r.uom}</td>
                <td style={{ ...tdStyle, textAlign: "right", color: "#10b981" }}>{Number(r.in_qty || 0).toFixed(1)}</td>
                <td style={{ ...tdStyle, textAlign: "right", color: "#f59e0b" }}>{Number(r.out_qty || 0).toFixed(1)}</td>
                <td style={{ ...tdStyle, textAlign: "right", fontWeight: 800, fontSize: 14, color: STATUS_COLOR(r.status) }}>
                  {Number(r.balance || 0).toFixed(1)}
                </td>
                <td style={{ ...tdStyle, textAlign: "right", fontSize: 11, color: "#64748b" }}>
                  {Number(r.min_stock || 0).toFixed(0)} / {Number(r.max_stock || 0).toFixed(0)}
                </td>
                <td style={tdStyle}>
                  <span style={{ padding: "2px 8px", borderRadius: 99, fontSize: 10, fontWeight: 800,
                                 background: `${STATUS_COLOR(r.status)}1a`, color: STATUS_COLOR(r.status) }}>
                    {r.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── GRN Tab ────────────────────────────────────────────────────── */
function GRNTab({ token, user, showToast }) {
  const [materials, setMaterials] = useState([]);
  const [recent, setRecent] = useState([]);
  const [form, setForm] = useState({ material_id: "", qty: "", supplier: "", grn_no: "", remarks: "" });

  const load = useCallback(async () => {
    try {
      const [mats, list] = await Promise.all([
        api.get("/api/store/materials", token),
        api.get("/api/store/grn", token),
      ]);
      setMaterials(mats || []);
      setRecent(list || []);
    } catch (e) { showToast(`Load failed: ${e.message}`, "err"); }
  }, [token]);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!form.material_id || !form.qty) { showToast("Material and qty required", "err"); return; }
    try {
      await api.post("/api/store/grn", {
        material_id: Number(form.material_id),
        qty: Number(form.qty),
        supplier: form.supplier || null,
        grn_no: form.grn_no || null,
        remarks: form.remarks || null,
      }, token);
      showToast("GRN recorded ✓");
      setForm({ material_id: "", qty: "", supplier: "", grn_no: "", remarks: "" });
      load();
    } catch (e) { showToast(e.message, "err"); }
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "380px 1fr", gap: 16 }}>
      {/* Form */}
      <div style={cardStyle}>
        <div style={{ padding: 16, borderBottom: "1px solid #e2e8f0" }}>
          <div style={{ fontSize: 13, fontWeight: 800, color: "#0f172a" }}>+ NEW GRN</div>
          <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>Log incoming material</div>
        </div>
        <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
          <Field label="Material *">
            <select value={form.material_id} onChange={e => setForm({...form, material_id: e.target.value})} style={glassSelect}>
              <option value="">— select —</option>
              {materials.filter(m => m.mat_type !== "FG").map(m => (
                <option key={m.id} value={m.id}>{m.code} — {m.name} ({m.uom})</option>
              ))}
            </select>
          </Field>
          <Field label="Quantity *">
            <input type="number" min={0} step="0.001" value={form.qty}
                   onChange={e => setForm({...form, qty: e.target.value})} style={glassSelect} placeholder="0" />
          </Field>
          <Field label="Supplier">
            <input value={form.supplier} onChange={e => setForm({...form, supplier: e.target.value})}
                   style={glassSelect} placeholder="Optional" />
          </Field>
          <Field label="GRN No (auto if blank)">
            <input value={form.grn_no} onChange={e => setForm({...form, grn_no: e.target.value})}
                   style={glassSelect} placeholder="GRN-26051200001" />
          </Field>
          <Field label="Remarks">
            <textarea rows={2} value={form.remarks}
                      onChange={e => setForm({...form, remarks: e.target.value})}
                      style={{ ...glassSelect, resize: "vertical" }} />
          </Field>
          <button onClick={save} style={primaryBtn}>✓ Record GRN</button>
        </div>
      </div>

      {/* Recent list */}
      <div style={cardStyle}>
        <div style={{ padding: 16, borderBottom: "1px solid #e2e8f0" }}>
          <div style={{ fontSize: 13, fontWeight: 800, color: "#0f172a" }}>Recent GRNs ({recent.length})</div>
        </div>
        <div style={{ maxHeight: 600, overflowY: "auto" }} className="col-scroll">
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#0f172a" }}>
            <thead>
              <tr style={{ position: "sticky", top: 0, background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
                <th style={thStyle}>GRN #</th>
                <th style={thStyle}>Material</th>
                <th style={{ ...thStyle, textAlign: "right" }}>Qty</th>
                <th style={thStyle}>Supplier</th>
                <th style={thStyle}>Received</th>
                <th style={thStyle}>By</th>
              </tr>
            </thead>
            <tbody>
              {recent.length === 0 ? (
                <tr><td colSpan={6} style={{ padding: 30, textAlign: "center", color: "#94a3b8" }}>No GRN yet</td></tr>
              ) : recent.map(r => (
                <tr key={r.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                  <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: 11 }}>{r.grn_no}</td>
                  <td style={tdStyle}>
                    <div style={{ fontWeight: 700, color: "#0f172a" }}>{r.material_code}</div>
                    <div style={{ fontSize: 10, color: "#64748b" }}>{r.material_name}</div>
                  </td>
                  <td style={{ ...tdStyle, textAlign: "right", fontWeight: 800, color: "#10b981" }}>
                    {Number(r.qty).toFixed(1)} <span style={{ fontSize: 9, color: "#94a3b8" }}>{r.uom}</span>
                  </td>
                  <td style={tdStyle}>{r.supplier || "—"}</td>
                  <td style={{ ...tdStyle, fontSize: 11 }}>{new Date(r.received_at).toLocaleString()}</td>
                  <td style={tdStyle}>{r.received_by || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ── Issue Tab ──────────────────────────────────────────────────── */
function IssueTab({ token, user, showToast }) {
  const [materials, setMaterials] = useState([]);
  const [lines, setLines] = useState([]);
  const [recent, setRecent] = useState([]);
  const [form, setForm] = useState({ material_id: "", line_id: "", qty: "", remarks: "" });

  const load = useCallback(async () => {
    try {
      const [mats, lns, list] = await Promise.all([
        api.get("/api/store/materials", token),
        api.get("/api/lines/", token),
        api.get("/api/store/issues", token),
      ]);
      setMaterials(mats || []);
      setLines(lns || []);
      setRecent(list || []);
    } catch (e) { showToast(`Load failed: ${e.message}`, "err"); }
  }, [token]);
  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!form.material_id || !form.line_id || !form.qty) {
      showToast("Material, line and qty required", "err"); return;
    }
    try {
      await api.post("/api/store/issues", {
        material_id: Number(form.material_id),
        line_id: Number(form.line_id),
        qty: Number(form.qty),
        remarks: form.remarks || null,
      }, token);
      showToast("Issue recorded ✓");
      setForm({ material_id: "", line_id: "", qty: "", remarks: "" });
      load();
    } catch (e) { showToast(e.message, "err"); }
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "380px 1fr", gap: 16 }}>
      <div style={cardStyle}>
        <div style={{ padding: 16, borderBottom: "1px solid #e2e8f0" }}>
          <div style={{ fontSize: 13, fontWeight: 800, color: "#0f172a" }}>+ ISSUE TO LINE</div>
          <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>Outgoing material to production</div>
        </div>
        <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
          <Field label="Material *">
            <select value={form.material_id} onChange={e => setForm({...form, material_id: e.target.value})} style={glassSelect}>
              <option value="">— select —</option>
              {materials.filter(m => m.mat_type !== "FG").map(m => (
                <option key={m.id} value={m.id}>{m.code} — {m.name} ({m.uom})</option>
              ))}
            </select>
          </Field>
          <Field label="Line *">
            <select value={form.line_id} onChange={e => setForm({...form, line_id: e.target.value})} style={glassSelect}>
              <option value="">— select —</option>
              {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
            </select>
          </Field>
          <Field label="Quantity *">
            <input type="number" min={0} step="0.001" value={form.qty}
                   onChange={e => setForm({...form, qty: e.target.value})} style={glassSelect} placeholder="0" />
          </Field>
          <Field label="Remarks">
            <textarea rows={2} value={form.remarks}
                      onChange={e => setForm({...form, remarks: e.target.value})}
                      style={{ ...glassSelect, resize: "vertical" }} />
          </Field>
          <button onClick={save} style={primaryBtn}>✓ Issue to Line</button>
        </div>
      </div>

      <div style={cardStyle}>
        <div style={{ padding: 16, borderBottom: "1px solid #e2e8f0" }}>
          <div style={{ fontSize: 13, fontWeight: 800, color: "#0f172a" }}>Recent Issues ({recent.length})</div>
        </div>
        <div style={{ maxHeight: 600, overflowY: "auto" }} className="col-scroll">
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#0f172a" }}>
            <thead>
              <tr style={{ position: "sticky", top: 0, background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
                <th style={thStyle}>Material</th>
                <th style={thStyle}>Line</th>
                <th style={{ ...thStyle, textAlign: "right" }}>Qty</th>
                <th style={thStyle}>Issued</th>
                <th style={thStyle}>By</th>
                <th style={thStyle}>Remarks</th>
              </tr>
            </thead>
            <tbody>
              {recent.length === 0 ? (
                <tr><td colSpan={6} style={{ padding: 30, textAlign: "center", color: "#94a3b8" }}>No issues yet</td></tr>
              ) : recent.map(r => (
                <tr key={r.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                  <td style={tdStyle}>
                    <div style={{ fontWeight: 700, color: "#0f172a" }}>{r.material_code}</div>
                    <div style={{ fontSize: 10, color: "#64748b" }}>{r.material_name}</div>
                  </td>
                  <td style={tdStyle}>{r.line_name || `#${r.line_id}`}</td>
                  <td style={{ ...tdStyle, textAlign: "right", fontWeight: 800, color: "#f59e0b" }}>
                    {Number(r.qty).toFixed(1)} <span style={{ fontSize: 9, color: "#94a3b8" }}>{r.uom}</span>
                  </td>
                  <td style={{ ...tdStyle, fontSize: 11 }}>{new Date(r.issued_at).toLocaleString()}</td>
                  <td style={tdStyle}>{r.issued_by || "—"}</td>
                  <td style={{ ...tdStyle, fontSize: 11, color: "#64748b" }}>{r.remarks || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ── Materials Master Tab ───────────────────────────────────────── */
function MaterialsTab({ token, showToast, isAdmin }) {
  const [rows, setRows] = useState([]);
  const [lines, setLines] = useState([]);
  const [modal, setModal] = useState(false);
  const [editing, setEditing] = useState(null);
  const [filterType, setFilterType] = useState("");
  const fileRef = useRef();

  const load = useCallback(async () => {
    try {
      const [mats, ls] = await Promise.all([
        api.get("/api/store/materials", token),
        api.get("/api/lines/", token),
      ]);
      setRows(mats || []);
      setLines(ls || []);
    } catch (e) { showToast(`Load failed: ${e.message}`, "err"); }
  }, [token]);
  useEffect(() => { load(); }, [load]);

  const filtered = filterType ? rows.filter(r => r.mat_type === filterType) : rows;

  const openNew = () => {
    setEditing(null);
    setModal(true);
  };
  const openEdit = (r) => { setEditing(r); setModal(true); };
  const remove = async (r) => {
    if (!confirm(`Delete material "${r.code}"? (soft delete)`)) return;
    try { await api.delete(`/api/store/materials/${r.id}`, token); showToast("Deleted"); load(); }
    catch (e) { showToast(e.message, "err"); }
  };

  const dlTemplate = async () => {
    try {
      const res = await fetch("/api/store/materials/template", { headers: { Authorization: `Bearer ${token}` }});
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "materials_template.xlsx";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { showToast(e.message, "err"); }
  };
  const dlExport = async () => {
    try {
      const res = await fetch("/api/store/materials/export", { headers: { Authorization: `Bearer ${token}` }});
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `materials_${new Date().toISOString().slice(0,10)}.xlsx`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { showToast(e.message, "err"); }
  };
  const handleFile = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData(); fd.append("file", file);
    try {
      const res = await fetch("/api/store/materials/import", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      const j = await res.json();
      if (!res.ok) throw new Error(j.detail || `HTTP ${res.status}`);
      showToast(`Imported · ${j.inserted} new, ${j.updated} updated, ${j.skipped} skipped`);
      if (j.errors?.length) console.warn("Import errors:", j.errors);
      load();
    } catch (e) { showToast(`Import failed: ${e.message}`, "err"); }
    finally { e.target.value = ""; }
  };

  return (
    <div>
      <div style={{ display: "flex", gap: 10, marginBottom: 14, alignItems: "center", flexWrap: "wrap" }}>
        <select value={filterType} onChange={e => setFilterType(e.target.value)} style={glassSelect}>
          <option value="">All Types</option>
          {MAT_TYPES.map(t => <option key={t.v} value={t.v}>{t.label}</option>)}
        </select>
        <div style={{ flex: 1 }} />
        {isAdmin && (
          <>
            <button onClick={dlTemplate} style={glassBtn}>📄 Template</button>
            <button onClick={() => fileRef.current?.click()} style={glassBtn}>⬆ Import</button>
            <input ref={fileRef} type="file" accept=".xlsx,.xls" onChange={handleFile} style={{ display: "none" }} />
            <button onClick={dlExport} style={glassBtn}>⬇ Export</button>
            <button onClick={openNew} style={primaryBtn}>+ New Material</button>
          </>
        )}
      </div>

      <div style={cardStyle}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#0f172a" }}>
          <thead>
            <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
              <th style={thStyle}>Code</th>
              <th style={thStyle}>Name</th>
              <th style={thStyle}>Type</th>
              <th style={thStyle}>UOM</th>
              <th style={{ ...thStyle, textAlign: "right" }}>Min / Max</th>
              <th style={{ ...thStyle, textAlign: "right" }}>Lot Size</th>
              <th style={thStyle}>Line</th>
              <th style={thStyle}>Supplier</th>
              {isAdmin && <th style={thStyle}></th>}
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr><td colSpan={9} style={{ padding: 30, textAlign: "center", color: "#94a3b8" }}>
                No materials. {isAdmin && "Click + New Material or use Import."}
              </td></tr>
            ) : filtered.map(r => (
              <tr key={r.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                <td style={{ ...tdStyle, fontFamily: "monospace" }}>{r.code}</td>
                <td style={{ ...tdStyle, fontWeight: 700 }}>{r.name}</td>
                <td style={tdStyle}>
                  <span style={{ padding: "2px 8px", borderRadius: 99, fontSize: 10, fontWeight: 800,
                                 background: `${TYPE_COLOR(r.mat_type)}1a`, color: TYPE_COLOR(r.mat_type) }}>
                    {r.mat_type}
                  </span>
                </td>
                <td style={tdStyle}>{r.uom}</td>
                <td style={{ ...tdStyle, textAlign: "right" }}>{Number(r.min_stock || 0).toFixed(0)} / {Number(r.max_stock || 0).toFixed(0)}</td>
                <td style={{ ...tdStyle, textAlign: "right", fontWeight: 700, color: "#10b981" }}>{Number(r.lot_size || 0).toFixed(0)}</td>
                <td style={tdStyle}>{r.line_name || "—"}</td>
                <td style={tdStyle}>{r.supplier || "—"}</td>
                {isAdmin && (
                  <td style={{ ...tdStyle, display: "flex", gap: 6 }}>
                    <button onClick={() => openEdit(r)} style={{ ...glassBtn, padding: "4px 10px", fontSize: 11 }}>Edit</button>
                    <button onClick={() => remove(r)} style={{ ...glassBtn, padding: "4px 10px", fontSize: 11, color: "#dc2626", borderColor: "#fecaca" }}>Delete</button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modal && <MaterialModal editing={editing} lines={lines} token={token}
                                onClose={() => setModal(false)}
                                onSaved={() => { setModal(false); load(); showToast("Saved ✓"); }}
                                onError={(m) => showToast(m, "err")} />}
    </div>
  );
}

function MaterialModal({ editing, lines, token, onClose, onSaved, onError }) {
  const [f, setF] = useState(editing || {
    code: "", name: "", mat_type: "RM", uom: "PCS",
    min_stock: 0, max_stock: 0, lot_size: 0,
    line_id: "", supplier: "", is_active: true,
  });
  const save = async () => {
    if (!f.code || !f.name) { onError("Code and name required"); return; }
    try {
      await api.post("/api/store/materials", {
        ...f,
        line_id: f.line_id ? Number(f.line_id) : null,
        min_stock: Number(f.min_stock) || 0,
        max_stock: Number(f.max_stock) || 0,
        lot_size:  Number(f.lot_size)  || 0,
      }, token);
      onSaved();
    } catch (e) { onError(e.message); }
  };
  return (
    <div onClick={onClose} style={modalBackdrop}>
      <div onClick={e => e.stopPropagation()} style={modalBox}>
        <div style={{ fontSize: 16, fontWeight: 800, marginBottom: 16, color: "#0f172a" }}>
          {editing ? "Edit Material" : "New Material"}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <Field label="Code *">
            <input value={f.code} onChange={e => setF({...f, code: e.target.value})} style={glassSelect} disabled={!!editing} />
          </Field>
          <Field label="Type *">
            <select value={f.mat_type} onChange={e => setF({...f, mat_type: e.target.value})} style={glassSelect}>
              {MAT_TYPES.map(t => <option key={t.v} value={t.v}>{t.label}</option>)}
            </select>
          </Field>
          <Field label="Name *" full>
            <input value={f.name} onChange={e => setF({...f, name: e.target.value})} style={glassSelect} />
          </Field>
          <Field label="UOM">
            <input value={f.uom} onChange={e => setF({...f, uom: e.target.value.toUpperCase()})} style={glassSelect} placeholder="PCS, KG, M..." />
          </Field>
          <Field label="Line (for FG)">
            <select value={f.line_id || ""} onChange={e => setF({...f, line_id: e.target.value})} style={glassSelect}>
              <option value="">— none —</option>
              {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
            </select>
          </Field>
          <Field label="Min Stock">
            <input type="number" value={f.min_stock} onChange={e => setF({...f, min_stock: e.target.value})} style={glassSelect} />
          </Field>
          <Field label="Max Stock">
            <input type="number" value={f.max_stock} onChange={e => setF({...f, max_stock: e.target.value})} style={glassSelect} />
          </Field>
          <Field label="Lot Size (FG box / pallet)">
            <input type="number" value={f.lot_size} onChange={e => setF({...f, lot_size: e.target.value})} style={glassSelect} placeholder="50" />
          </Field>
          <Field label="Supplier">
            <input value={f.supplier || ""} onChange={e => setF({...f, supplier: e.target.value})} style={glassSelect} />
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
      <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: "#64748b" }}>{label}</label>
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
  color: "#0f172a", outline: "none",
  fontFamily: "'Barlow',sans-serif", width: "100%",
};
const glassBtn = {
  padding: "8px 14px", fontSize: 12, fontWeight: 700,
  background: "#f8fafc", color: "#334155",
  border: "1px solid #e2e8f0", borderRadius: 8,
  cursor: "pointer", fontFamily: "'Barlow',sans-serif",
};
const primaryBtn = {
  padding: "9px 18px", fontSize: 12, fontWeight: 800,
  background: "linear-gradient(135deg, #1e40af, #2563eb)",
  color: "#fff", border: "none", borderRadius: 8,
  cursor: "pointer", letterSpacing: ".02em",
  boxShadow: "0 2px 8px rgba(30,64,175,.3)",
  fontFamily: "'Barlow',sans-serif",
};
const modalBackdrop = {
  position: "fixed", inset: 0, background: "rgba(15,23,42,.4)",
  backdropFilter: "blur(4px)", zIndex: 1000,
  display: "flex", alignItems: "center", justifyContent: "center",
};
const modalBox = {
  background: "#fff",
  border: "1px solid #e2e8f0",
  borderRadius: 18, padding: 24, width: 600, maxWidth: "90vw",
  maxHeight: "85vh", overflowY: "auto",
  boxShadow: "0 24px 80px rgba(0,0,0,.18)",
};
const thStyle = { padding: "10px 8px", fontSize: 10, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: "#64748b", textAlign: "left" };
const tdStyle = { padding: "8px 8px", fontSize: 12, color: "#0f172a" };
