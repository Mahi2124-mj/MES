/* ════════════════════════════════════════════════════════════════════
 *  DispatchPage.jsx
 *  ────────────────────────────────────────────────────────────────────
 *  4 tabs:
 *    1. Dashboard  — ready lots, planned loads, dispatched today
 *    2. Lots       — pack FG into lots (with configurable lot_size)
 *    3. Loads      — build truck loads (1 customer, N lots), dispatch
 *    4. Customers  — master CRUD with Excel import/export (admin)
 * ════════════════════════════════════════════════════════════════════ */

import { useState, useEffect, useCallback, useRef } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import AIAssistant from "../components/AIAssistant";
import PageTopbar from "../components/PageTopbar";

const LOT_STATUS_COLOR = (s) => ({
  READY: "#10b981", LOADED: "#3b82f6", DISPATCHED: "#8b5cf6", CANCELLED: "#94a3b8",
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
      boxShadow: "0 10px 40px rgba(0,0,0,.12)", maxWidth: 400,
    }}>
      <span style={{ color: c, marginRight: 8 }}>{kind === "err" ? "✗" : "✓"}</span>
      {msg}
    </div>
  );
}

export default function DispatchPage() {
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
        .dt-tab {
          padding: 12px 22px; font-size: 12px; font-weight: 700;
          letter-spacing: .08em; text-transform: uppercase;
          background: none; border: none; color: #64748b;
          border-bottom: 2px solid transparent; cursor: pointer;
          font-family: 'Barlow',sans-serif; transition: all .12s;
        }
        .dt-tab:hover { color: #0f172a; }
        .dt-tab.active { color: #0f172a; border-bottom-color: #16a34a; }
        .col-scroll::-webkit-scrollbar { width: 6px; }
        .col-scroll::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
      `}</style>

      {/* 2026-05-13 — standardised topbar */}
      <PageTopbar leading="Dispatch" accent="Management" />

      <div style={{ padding: "10px 48px 0", background: "#fff", borderBottom: "1px solid #e2e8f0" }}>
        <div style={{ fontSize: 12, color: "#64748b" }}>
          FG packing into lots · truck load planning · gate-pass dispatch
        </div>
        <div style={{ display: "flex", gap: 2, marginTop: 18 }}>
          {["dashboard","lots","loads","customers"].map(t => (
            <button key={t} className={`dt-tab${tab===t?" active":""}`} onClick={() => setTab(t)}>
              {{dashboard:"Dashboard", lots:"Lots (FG Pack)", loads:"Loads (Truck)", customers:"Customers"}[t]}
            </button>
          ))}
        </div>
      </div>

      <div style={{ padding: "20px 48px" }}>
        {tab === "dashboard" && <DashboardTab token={token} showToast={showToast} />}
        {tab === "lots"      && <LotsTab      token={token} showToast={showToast} />}
        {tab === "loads"     && <LoadsTab     token={token} showToast={showToast} />}
        {tab === "customers" && <CustomersTab token={token} showToast={showToast} isAdmin={isAdmin} />}
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
    api.get("/api/dispatch/dashboard", token)
       .then(setData)
       .catch(e => showToast(`Load failed: ${e.message}`, "err"));
  }, [token]);
  if (!data) return <div style={{ color: "#64748b", padding: 40 }}>Loading…</div>;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 16 }}>
      <KPITile label="Ready Lots"        value={data.ready_lots}        color="#10b981" suffix="awaiting load" />
      <KPITile label="Ready Qty"         value={Number(data.ready_qty).toFixed(0)} color="#10b981" suffix="pieces" />
      <KPITile label="Planned Loads"     value={data.planned_loads}     color="#3b82f6" suffix="not yet dispatched" />
      <KPITile label="Dispatched Today"  value={data.dispatched_today}  color="#8b5cf6" suffix="trucks left" />
      <KPITile label="Qty Today"         value={Number(data.qty_today || 0).toFixed(0)} color="#8b5cf6" suffix="pieces shipped" />
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

/* ── Lots Tab (Pack FG) ─────────────────────────────────────────── */
function LotsTab({ token, showToast }) {
  const [materials, setMaterials] = useState([]);
  const [lines, setLines] = useState([]);
  const [lots, setLots] = useState([]);
  const [filterStatus, setFilterStatus] = useState("");
  const [form, setForm] = useState({ line_id: "", material_id: "", lot_size: "", qty_packed: "", remarks: "" });

  const load = useCallback(async () => {
    try {
      const [mats, lns, lst] = await Promise.all([
        api.get("/api/store/materials?mat_type=FG", token),
        api.get("/api/lines/", token),
        api.get(`/api/dispatch/lots${filterStatus ? "?status="+filterStatus : ""}`, token),
      ]);
      setMaterials(mats || []);
      setLines(lns || []);
      setLots(lst || []);
    } catch (e) { showToast(`Load failed: ${e.message}`, "err"); }
  }, [token, filterStatus]);
  useEffect(() => { load(); }, [load]);

  // Auto-fill lot_size from material default
  const onMaterialChange = (matId) => {
    const m = materials.find(x => x.id === Number(matId));
    setForm(prev => ({
      ...prev, material_id: matId,
      lot_size: m?.lot_size || "",
      line_id:  m?.line_id || prev.line_id,
    }));
  };

  const save = async () => {
    if (!form.line_id || !form.material_id || !form.qty_packed) {
      showToast("Line, material, qty required", "err"); return;
    }
    try {
      await api.post("/api/dispatch/lots", {
        line_id: Number(form.line_id),
        material_id: Number(form.material_id),
        lot_size: form.lot_size ? Number(form.lot_size) : null,
        qty_packed: Number(form.qty_packed),
        remarks: form.remarks || null,
      }, token);
      showToast("Lot packed ✓");
      setForm({ line_id: "", material_id: "", lot_size: "", qty_packed: "", remarks: "" });
      load();
    } catch (e) { showToast(e.message, "err"); }
  };

  const cancel = async (lotId) => {
    if (!confirm("Cancel this lot? (admin only)")) return;
    try { await api.post(`/api/dispatch/lots/${lotId}/cancel`, {}, token); showToast("Cancelled"); load(); }
    catch (e) { showToast(e.message, "err"); }
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "380px 1fr", gap: 16 }}>
      <div style={cardStyle}>
        <div style={{ padding: 16, borderBottom: "1px solid #e2e8f0" }}>
          <div style={{ fontSize: 13, fontWeight: 800 }}>+ PACK NEW LOT</div>
          <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>Pack FG output into a box / pallet</div>
        </div>
        <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
          <Field label="FG Material *">
            <select value={form.material_id} onChange={e => onMaterialChange(e.target.value)} style={glassSelect}>
              <option value="">— select FG —</option>
              {materials.map(m => (
                <option key={m.id} value={m.id}>{m.code} — {m.name}</option>
              ))}
            </select>
          </Field>
          <Field label="Line *">
            <select value={form.line_id} onChange={e => setForm({...form, line_id: e.target.value})} style={glassSelect}>
              <option value="">— select —</option>
              {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
            </select>
          </Field>
          <Field label="Lot Size (pieces per box)">
            <input type="number" min={0} value={form.lot_size}
                   onChange={e => setForm({...form, lot_size: e.target.value})} style={glassSelect}
                   placeholder="from material master if blank" />
          </Field>
          <Field label="Qty Packed in this Lot *">
            <input type="number" min={0} step="0.001" value={form.qty_packed}
                   onChange={e => setForm({...form, qty_packed: e.target.value})} style={glassSelect}
                   placeholder="actual pieces in this box" />
          </Field>
          <Field label="Remarks">
            <textarea rows={2} value={form.remarks}
                      onChange={e => setForm({...form, remarks: e.target.value})}
                      style={{ ...glassSelect, resize: "vertical" }} />
          </Field>
          <button onClick={save} style={primaryBtn}>✓ Pack Lot</button>
        </div>
      </div>

      <div style={cardStyle}>
        <div style={{ padding: 16, borderBottom: "1px solid #e2e8f0", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 800 }}>Lots ({lots.length})</div>
            <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>READY → LOADED → DISPATCHED</div>
          </div>
          <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)} style={{ ...glassSelect, width: "auto" }}>
            <option value="">All Status</option>
            <option value="READY">Ready</option>
            <option value="LOADED">Loaded</option>
            <option value="DISPATCHED">Dispatched</option>
            <option value="CANCELLED">Cancelled</option>
          </select>
        </div>
        <div style={{ maxHeight: 600, overflowY: "auto" }} className="col-scroll">
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#0f172a" }}>
            <thead>
              <tr style={{ position: "sticky", top: 0, background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
                <th style={thStyle}>Lot #</th>
                <th style={thStyle}>FG</th>
                <th style={thStyle}>Line</th>
                <th style={{ ...thStyle, textAlign: "right" }}>Lot Size</th>
                <th style={{ ...thStyle, textAlign: "right" }}>Packed</th>
                <th style={thStyle}>Status</th>
                <th style={thStyle}>Load #</th>
                <th style={thStyle}>Packed At</th>
                <th style={thStyle}></th>
              </tr>
            </thead>
            <tbody>
              {lots.length === 0 ? (
                <tr><td colSpan={9} style={{ padding: 30, textAlign: "center", color: "#64748b" }}>No lots</td></tr>
              ) : lots.map(r => (
                <tr key={r.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                  <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: 10 }}>{r.lot_no}</td>
                  <td style={tdStyle}>
                    <div style={{ fontWeight: 700, color: "#0f172a" }}>{r.material_code}</div>
                    <div style={{ fontSize: 10, color: "#64748b" }}>{r.material_name}</div>
                  </td>
                  <td style={tdStyle}>{r.line_name || `#${r.line_id}`}</td>
                  <td style={{ ...tdStyle, textAlign: "right" }}>{Number(r.lot_size).toFixed(0)}</td>
                  <td style={{ ...tdStyle, textAlign: "right", fontWeight: 800, color: "#10b981" }}>
                    {Number(r.qty_packed).toFixed(1)} <span style={{ fontSize: 9, color: "#64748b" }}>{r.uom}</span>
                  </td>
                  <td style={tdStyle}>
                    <span style={{ padding: "2px 8px", borderRadius: 99, fontSize: 10, fontWeight: 800,
                                   background: `${LOT_STATUS_COLOR(r.status)}1a`, color: LOT_STATUS_COLOR(r.status) }}>
                      {r.status}
                    </span>
                  </td>
                  <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: 10 }}>{r.load_no || "—"}</td>
                  <td style={{ ...tdStyle, fontSize: 11 }}>{new Date(r.packed_at).toLocaleString()}</td>
                  <td style={tdStyle}>
                    {(r.status === "READY" || r.status === "LOADED") && (
                      <button onClick={() => cancel(r.id)} style={{ ...glassBtn, padding: "4px 10px", fontSize: 11, color: "#dc2626", borderColor: "#fecaca" }}>
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ── Loads Tab ──────────────────────────────────────────────────── */
function LoadsTab({ token, showToast }) {
  const [readyLots, setReadyLots] = useState([]);
  const [customers, setCustomers] = useState([]);
  const [loads, setLoads] = useState([]);
  const [selectedLots, setSelectedLots] = useState(new Set());
  const [filterStatus, setFilterStatus] = useState("");
  const [form, setForm] = useState({ customer_id: "", vehicle_no: "", driver_name: "", driver_phone: "", remarks: "" });

  const load = useCallback(async () => {
    try {
      const [ready, custs, lds] = await Promise.all([
        api.get("/api/dispatch/lots?status=READY", token),
        api.get("/api/dispatch/customers", token),
        api.get(`/api/dispatch/loads${filterStatus ? "?status="+filterStatus : ""}`, token),
      ]);
      setReadyLots(ready || []);
      setCustomers(custs || []);
      setLoads(lds || []);
    } catch (e) { showToast(`Load failed: ${e.message}`, "err"); }
  }, [token, filterStatus]);
  useEffect(() => { load(); }, [load]);

  const toggle = (lotId) => {
    setSelectedLots(prev => {
      const next = new Set(prev);
      if (next.has(lotId)) next.delete(lotId);
      else next.add(lotId);
      return next;
    });
  };

  const create = async () => {
    if (!form.customer_id) { showToast("Pick a customer", "err"); return; }
    if (selectedLots.size === 0) { showToast("Select at least one lot", "err"); return; }
    try {
      await api.post("/api/dispatch/loads", {
        customer_id: Number(form.customer_id),
        vehicle_no: form.vehicle_no || null,
        driver_name: form.driver_name || null,
        driver_phone: form.driver_phone || null,
        lot_ids: Array.from(selectedLots),
        remarks: form.remarks || null,
      }, token);
      showToast(`Load created with ${selectedLots.size} lot(s) ✓`);
      setForm({ customer_id: "", vehicle_no: "", driver_name: "", driver_phone: "", remarks: "" });
      setSelectedLots(new Set());
      load();
    } catch (e) { showToast(e.message, "err"); }
  };

  const dispatch = async (loadId) => {
    if (!confirm("Mark this load DISPATCHED? (gate-pass action)")) return;
    try { await api.post(`/api/dispatch/loads/${loadId}/dispatch`, {}, token); showToast("Dispatched ✓"); load(); }
    catch (e) { showToast(e.message, "err"); }
  };

  const cancelLoad = async (loadId) => {
    if (!confirm("Cancel this load? Attached lots will return to READY.")) return;
    try { await api.post(`/api/dispatch/loads/${loadId}/cancel`, {}, token); showToast("Cancelled"); load(); }
    catch (e) { showToast(e.message, "err"); }
  };

  const totalSelectedQty = Array.from(selectedLots)
    .map(id => readyLots.find(l => l.id === id))
    .filter(Boolean)
    .reduce((s, l) => s + Number(l.qty_packed || 0), 0);

  return (
    <div>
      {/* Build load section */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 340px", gap: 16, marginBottom: 20 }}>
        <div style={cardStyle}>
          <div style={{ padding: 16, borderBottom: "1px solid #e2e8f0", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 800 }}>READY LOTS — select to load</div>
              <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>{readyLots.length} ready</div>
            </div>
            <div style={{ fontSize: 11, color: selectedLots.size > 0 ? "#10b981" : "#64748b" }}>
              {selectedLots.size} selected · {totalSelectedQty.toFixed(0)} qty
            </div>
          </div>
          <div className="col-scroll" style={{ maxHeight: 360, overflowY: "auto" }}>
            {readyLots.length === 0 ? (
              <div style={{ padding: 30, textAlign: "center", color: "#64748b", fontSize: 12 }}>
                No ready lots. Pack some lots in the Lots tab first.
              </div>
            ) : readyLots.map(r => (
              <div key={r.id} onClick={() => toggle(r.id)}
                   style={{
                     padding: "10px 16px", borderBottom: "1px solid #f1f5f9",
                     background: selectedLots.has(r.id) ? "#f0fdf4" : "transparent",
                     cursor: "pointer", display: "flex", alignItems: "center", gap: 12,
                   }}>
                <input type="checkbox" readOnly checked={selectedLots.has(r.id)} style={{ accentColor: "#10b981" }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: "#0f172a" }}>
                    {r.material_code} — {r.material_name}
                  </div>
                  <div style={{ fontSize: 10, color: "#64748b", fontFamily: "monospace" }}>
                    {r.lot_no} · {r.line_name || `#${r.line_id}`}
                  </div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 14, fontWeight: 800, color: "#10b981" }}>{Number(r.qty_packed).toFixed(1)}</div>
                  <div style={{ fontSize: 9, color: "#64748b" }}>{r.uom}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div style={cardStyle}>
          <div style={{ padding: 16, borderBottom: "1px solid #e2e8f0" }}>
            <div style={{ fontSize: 13, fontWeight: 800 }}>+ CREATE LOAD</div>
            <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>Truck + customer + selected lots</div>
          </div>
          <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            <Field label="Customer *">
              <select value={form.customer_id} onChange={e => setForm({...form, customer_id: e.target.value})} style={glassSelect}>
                <option value="">— select —</option>
                {customers.map(c => <option key={c.id} value={c.id}>{c.code} — {c.name}</option>)}
              </select>
            </Field>
            <Field label="Vehicle No">
              <input value={form.vehicle_no} onChange={e => setForm({...form, vehicle_no: e.target.value.toUpperCase()})}
                     style={glassSelect} placeholder="HR-26-AB-1234" />
            </Field>
            <Field label="Driver Name">
              <input value={form.driver_name} onChange={e => setForm({...form, driver_name: e.target.value})} style={glassSelect} />
            </Field>
            <Field label="Driver Phone">
              <input value={form.driver_phone} onChange={e => setForm({...form, driver_phone: e.target.value})} style={glassSelect} />
            </Field>
            <Field label="Remarks">
              <textarea rows={2} value={form.remarks} onChange={e => setForm({...form, remarks: e.target.value})}
                        style={{ ...glassSelect, resize: "vertical" }} />
            </Field>
            <button onClick={create} disabled={selectedLots.size === 0 || !form.customer_id}
                    style={{ ...primaryBtn, opacity: (selectedLots.size === 0 || !form.customer_id) ? 0.4 : 1 }}>
              ✓ Create Load
            </button>
          </div>
        </div>
      </div>

      {/* Existing loads */}
      <div style={cardStyle}>
        <div style={{ padding: 16, borderBottom: "1px solid #e2e8f0", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 800 }}>Loads ({loads.length})</div>
          </div>
          <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)} style={{ ...glassSelect, width: "auto" }}>
            <option value="">All Status</option>
            <option value="PLANNED">Planned</option>
            <option value="DISPATCHED">Dispatched</option>
            <option value="CANCELLED">Cancelled</option>
          </select>
        </div>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#0f172a" }}>
          <thead>
            <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
              <th style={thStyle}>Load #</th>
              <th style={thStyle}>Customer</th>
              <th style={thStyle}>Vehicle</th>
              <th style={thStyle}>Driver</th>
              <th style={{ ...thStyle, textAlign: "right" }}>Lots</th>
              <th style={{ ...thStyle, textAlign: "right" }}>Total Qty</th>
              <th style={thStyle}>Status</th>
              <th style={thStyle}>Planned</th>
              <th style={thStyle}>Dispatched</th>
              <th style={thStyle}></th>
            </tr>
          </thead>
          <tbody>
            {loads.length === 0 ? (
              <tr><td colSpan={10} style={{ padding: 30, textAlign: "center", color: "#64748b" }}>No loads</td></tr>
            ) : loads.map(r => (
              <tr key={r.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: 10 }}>{r.load_no}</td>
                <td style={tdStyle}>
                  <div style={{ fontWeight: 700, color: "#0f172a" }}>{r.customer_code}</div>
                  <div style={{ fontSize: 10, color: "#64748b" }}>{r.customer_name}</div>
                </td>
                <td style={{ ...tdStyle, fontFamily: "monospace" }}>{r.vehicle_no || "—"}</td>
                <td style={tdStyle}>
                  {r.driver_name || "—"}
                  {r.driver_phone && <div style={{ fontSize: 10, color: "#64748b" }}>{r.driver_phone}</div>}
                </td>
                <td style={{ ...tdStyle, textAlign: "right", fontWeight: 700 }}>{r.lot_count}</td>
                <td style={{ ...tdStyle, textAlign: "right", fontWeight: 800, color: "#10b981" }}>{Number(r.total_qty || 0).toFixed(0)}</td>
                <td style={tdStyle}>
                  <span style={{ padding: "2px 8px", borderRadius: 99, fontSize: 10, fontWeight: 800,
                                 background: `${LOT_STATUS_COLOR(r.status === "DISPATCHED" ? "DISPATCHED" : r.status === "PLANNED" ? "LOADED" : "CANCELLED")}1a`,
                                 color: LOT_STATUS_COLOR(r.status === "DISPATCHED" ? "DISPATCHED" : r.status === "PLANNED" ? "LOADED" : "CANCELLED") }}>
                    {r.status}
                  </span>
                </td>
                <td style={{ ...tdStyle, fontSize: 11 }}>{new Date(r.planned_at).toLocaleString()}</td>
                <td style={{ ...tdStyle, fontSize: 11 }}>{r.dispatched_at ? new Date(r.dispatched_at).toLocaleString() : "—"}</td>
                <td style={{ ...tdStyle, display: "flex", gap: 6 }}>
                  {r.status === "PLANNED" && (
                    <>
                      <button onClick={() => dispatch(r.id)} style={{ ...primaryBtn, padding: "4px 10px", fontSize: 11 }}>
                        🚛 Dispatch
                      </button>
                      <button onClick={() => cancelLoad(r.id)} style={{ ...glassBtn, padding: "4px 10px", fontSize: 11, color: "#dc2626", borderColor: "#fecaca" }}>
                        Cancel
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Customers Tab ──────────────────────────────────────────────── */
function CustomersTab({ token, showToast, isAdmin }) {
  const [rows, setRows] = useState([]);
  const [modal, setModal] = useState(false);
  const [editing, setEditing] = useState(null);
  const fileRef = useRef();

  const load = useCallback(async () => {
    try { setRows(await api.get("/api/dispatch/customers", token)); }
    catch (e) { showToast(`Load failed: ${e.message}`, "err"); }
  }, [token]);
  useEffect(() => { load(); }, [load]);

  const dl = async (path, fname) => {
    try {
      const res = await fetch(path, { headers: { Authorization: `Bearer ${token}` }});
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = fname;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { showToast(e.message, "err"); }
  };
  const handleFile = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData(); fd.append("file", file);
    try {
      const res = await fetch("/api/dispatch/customers/import", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      const j = await res.json();
      if (!res.ok) throw new Error(j.detail || `HTTP ${res.status}`);
      showToast(`Imported · ${j.inserted} new, ${j.updated} updated`);
      load();
    } catch (e) { showToast(`Import failed: ${e.message}`, "err"); }
    finally { e.target.value = ""; }
  };

  return (
    <div>
      <div style={{ display: "flex", gap: 10, marginBottom: 14, justifyContent: "flex-end" }}>
        {isAdmin && (
          <>
            <button onClick={() => dl("/api/dispatch/customers/template", "customers_template.xlsx")} style={glassBtn}>📄 Template</button>
            <button onClick={() => fileRef.current?.click()} style={glassBtn}>⬆ Import</button>
            <input ref={fileRef} type="file" accept=".xlsx,.xls" onChange={handleFile} style={{ display: "none" }} />
            <button onClick={() => dl("/api/dispatch/customers/export", `customers_${new Date().toISOString().slice(0,10)}.xlsx`)} style={glassBtn}>⬇ Export</button>
            <button onClick={() => { setEditing(null); setModal(true); }} style={primaryBtn}>+ New Customer</button>
          </>
        )}
      </div>

      <div style={cardStyle}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#0f172a" }}>
          <thead>
            <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
              <th style={thStyle}>Code</th>
              <th style={thStyle}>Name</th>
              <th style={thStyle}>Contact</th>
              <th style={thStyle}>Phone</th>
              <th style={thStyle}>Email</th>
              <th style={thStyle}>Address</th>
              {isAdmin && <th style={thStyle}></th>}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={7} style={{ padding: 30, textAlign: "center", color: "#64748b" }}>
                No customers. {isAdmin && "Click + New Customer or Import."}
              </td></tr>
            ) : rows.map(r => (
              <tr key={r.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                <td style={{ ...tdStyle, fontFamily: "monospace" }}>{r.code}</td>
                <td style={{ ...tdStyle, fontWeight: 700 }}>{r.name}</td>
                <td style={tdStyle}>{r.contact || "—"}</td>
                <td style={tdStyle}>{r.phone || "—"}</td>
                <td style={tdStyle}>{r.email || "—"}</td>
                <td style={{ ...tdStyle, fontSize: 11, color: "#64748b", maxWidth: 280 }}>{r.address || "—"}</td>
                {isAdmin && (
                  <td style={tdStyle}>
                    <button onClick={() => { setEditing(r); setModal(true); }} style={{ ...glassBtn, padding: "4px 10px", fontSize: 11 }}>Edit</button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modal && <CustomerModal editing={editing} token={token}
                                onClose={() => setModal(false)}
                                onSaved={() => { setModal(false); load(); showToast("Saved ✓"); }}
                                onError={(m) => showToast(m, "err")} />}
    </div>
  );
}

function CustomerModal({ editing, token, onClose, onSaved, onError }) {
  const [f, setF] = useState(editing || {
    code: "", name: "", address: "", contact: "", phone: "", email: "", is_active: true,
  });
  const save = async () => {
    if (!f.code || !f.name) { onError("Code and name required"); return; }
    try {
      await api.post("/api/dispatch/customers", f, token);
      onSaved();
    } catch (e) { onError(e.message); }
  };
  return (
    <div onClick={onClose} style={modalBackdrop}>
      <div onClick={e => e.stopPropagation()} style={modalBox}>
        <div style={{ fontSize: 16, fontWeight: 800, marginBottom: 16, color: "#0f172a" }}>
          {editing ? "Edit Customer" : "New Customer"}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <Field label="Code *">
            <input value={f.code} onChange={e => setF({...f, code: e.target.value})} style={glassSelect} disabled={!!editing} />
          </Field>
          <Field label="Name *">
            <input value={f.name} onChange={e => setF({...f, name: e.target.value})} style={glassSelect} />
          </Field>
          <Field label="Contact Person">
            <input value={f.contact || ""} onChange={e => setF({...f, contact: e.target.value})} style={glassSelect} />
          </Field>
          <Field label="Phone">
            <input value={f.phone || ""} onChange={e => setF({...f, phone: e.target.value})} style={glassSelect} />
          </Field>
          <Field label="Email" full>
            <input value={f.email || ""} onChange={e => setF({...f, email: e.target.value})} style={glassSelect} />
          </Field>
          <Field label="Address" full>
            <textarea rows={2} value={f.address || ""} onChange={e => setF({...f, address: e.target.value})}
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
  background: "linear-gradient(135deg, #10b981, #34d399)",
  color: "#fff", border: "none", borderRadius: 8,
  cursor: "pointer", letterSpacing: ".02em",
  boxShadow: "0 2px 8px rgba(16,185,129,.3)",
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
