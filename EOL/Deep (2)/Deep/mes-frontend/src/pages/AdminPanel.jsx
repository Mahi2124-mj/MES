import { useState, useEffect, useRef, useCallback, useMemo, Fragment } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import AIAssistant from "../components/AIAssistant";
import NetworkPanel from "./NetworkPanel";
import MachineMaster from "./MachineMaster";
import PyConfigEditor from "./PyConfigEditor";
import FaultConfigPanel from "./FaultConfigPanel";
import WeldMaster from "./WeldMaster";
import PeffDetailsPage from "./PeffDetailsPage";
import { PAGE_MODULES, moduleKey, pageHasModules } from "../utils/pageModules";
import TimerConfigPage from "./TimerConfigPage";
import PanelMapPage from "./PanelMapPage";


// ─── Shared UI helpers ────────────────────────────────────────
function PageHeading({ title, sub }) {
  return (
    <div style={{ textAlign: "center", marginBottom: 36 }}>
      <h1 style={{
        fontFamily: "'Barlow Condensed',sans-serif",
        fontSize: 38, fontWeight: 800, color: "#0f172a", letterSpacing: "-.01em",
      }}>
        {title.split(" ").map((w, i, arr) =>
          i === arr.length - 1
            ? <span key={i} style={{ color: "#2563eb" }}>{w}</span>
            : <span key={i}>{w} </span>
        )}
      </h1>
      {sub && <p style={{ fontSize: 13, color: "#94a3b8", marginTop: 6 }}>{sub}</p>}
    </div>
  );
}

function Card({ children, style }) {
  return (
    <div style={{
      background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14,
      padding: 28, boxShadow: "0 1px 3px rgba(0,0,0,.05)", ...style,
    }}>
      {children}
    </div>
  );
}

function Pill({ label, color = "blue" }) {
  const colors = {
    green:  { bg: "rgba(22,163,74,.1)",   border: "rgba(22,163,74,.25)",   text: "#16a34a" },
    red:    { bg: "rgba(220,38,38,.1)",    border: "rgba(220,38,38,.25)",   text: "#dc2626" },
    blue:   { bg: "rgba(30,64,175,.1)",    border: "rgba(30,64,175,.2)",    text: "#1e40af" },
    amber:  { bg: "rgba(217,119,6,.1)",    border: "rgba(217,119,6,.25)",   text: "#d97706" },
    gray:   { bg: "#f1f5f9",               border: "#e2e8f0",               text: "#64748b" },
  };
  const c = colors[color] || colors.blue;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: "3px 10px", borderRadius: 99,
      background: c.bg, border: `1px solid ${c.border}`,
      fontSize: 11, fontWeight: 600, color: c.text,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: c.text }} />
      {label}
    </span>
  );
}

function Btn({ children, onClick, variant = "ghost", size = "md", disabled = false, style: s }) {
  const [h, setH] = useState(false);
  const pad = size === "sm" ? "5px 12px" : "9px 18px";
  const fs  = size === "sm" ? 11 : 13;
  const styles = {
    primary: { background: h ? "#1d3fa8" : "linear-gradient(135deg,#1e40af,#2563eb)", color: "#fff", border: "none", boxShadow: "0 2px 8px rgba(30,64,175,.3)" },
    danger:  { background: h ? "rgba(220,38,38,.12)" : "rgba(220,38,38,.06)", color: "#dc2626", border: "1px solid rgba(220,38,38,.3)" },
    success: { background: h ? "rgba(22,163,74,.12)"  : "rgba(22,163,74,.06)",  color: "#16a34a", border: "1px solid rgba(22,163,74,.3)"  },
    ghost:   { background: h ? "#f1f5f9" : "#f8fafc", color: h ? "#0f172a" : "#334155", border: `1px solid ${h ? "#3b82f6" : "#e2e8f0"}` },
  };
  return (
    <button
      onClick={onClick} disabled={disabled}
      data-variant={variant}
      onMouseEnter={() => setH(true)} onMouseLeave={() => setH(false)}
      style={{
        display: "inline-flex", alignItems: "center", gap: 6,
        padding: pad, borderRadius: 8, cursor: disabled ? "not-allowed" : "pointer",
        fontSize: fs, fontWeight: 600, fontFamily: "'Barlow',sans-serif",
        transition: "all .12s", opacity: disabled ? .55 : 1,
        ...styles[variant], ...s,
      }}
    >{children}</button>
  );
}

function FF({ label, children, hint }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
      <label style={{ fontSize: 10, fontWeight: 700, letterSpacing: ".1em", textTransform: "uppercase", color: "#64748b" }}>{label}</label>
      {children}
      {hint && <span style={{ fontSize: 10, color: "#94a3b8" }}>{hint}</span>}
    </div>
  );
}

const inputStyle = {
  background: "#f8fafc", border: "1.5px solid #e2e8f0", borderRadius: 8,
  padding: "10px 12px", color: "#0f172a", fontFamily: "'Barlow',sans-serif",
  fontSize: 13, outline: "none", width: "100%", boxSizing: "border-box",
  transition: "border-color .15s, box-shadow .15s",
};

function Input({ ...props }) {
  const [f, setF] = useState(false);
  return (
    <input
      {...props}
      onFocus={() => setF(true)} onBlur={() => setF(false)}
      style={{ ...inputStyle, borderColor: f ? "#3b82f6" : "#e2e8f0", boxShadow: f ? "0 0 0 3px rgba(59,130,246,.1)" : "none", ...props.style }}
    />
  );
}

function Select({ children, ...props }) {
  const [f, setF] = useState(false);
  return (
    <select
      {...props}
      onFocus={() => setF(true)} onBlur={() => setF(false)}
      style={{ ...inputStyle, appearance: "none", borderColor: f ? "#3b82f6" : "#e2e8f0", boxShadow: f ? "0 0 0 3px rgba(59,130,246,.1)" : "none", ...props.style }}
    >{children}</select>
  );
}

function Modal({ open, onClose, title, children, wide }) {
  useEffect(() => {
    const h = e => { if (e.key === "Escape") onClose(); };
    if (open) document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div onClick={e => e.target === e.currentTarget && onClose()}
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", backdropFilter: "blur(4px)", zIndex: 500, display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "48px 16px", overflowY: "auto" }}>
      <div style={{ background: "#fff", borderRadius: 16, width: "100%", maxWidth: wide ? 860 : 560, boxShadow: "0 24px 80px rgba(0,0,0,.2)", animation: "slideUp .22s cubic-bezier(.16,1,.3,1)", marginBottom: 40, overflow: "hidden" }}>
        <div style={{ padding: "18px 24px", background: "linear-gradient(135deg,#1e40af,#2563eb)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ fontSize: 17, fontWeight: 700, color: "#fff", textTransform: "capitalize" }}>{title}</div>
          <button onClick={onClose} style={{ width: 30, height: 30, borderRadius: 7, background: "rgba(255,255,255,.15)", border: "1px solid rgba(255,255,255,.25)", color: "#fff", cursor: "pointer", fontSize: 15, display: "flex", alignItems: "center", justifyContent: "center" }}>✕</button>
        </div>
        <div style={{ padding: 24 }}>{children}</div>
      </div>
      <style>{`
        @keyframes slideUp   { from { transform:translateY(18px);opacity:0 } to { transform:none;opacity:1 } }
        @keyframes slideDown { from { transform:translateY(-18px);opacity:0 } to { transform:none;opacity:1 } }
      `}</style>
    </div>
  );
}

function ModalActions({ children }) {
  return <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 24, paddingTop: 20, borderTop: "1px solid #f1f5f9" }}>{children}</div>;
}

function Toast({ msg, type, onDone }) {
  useEffect(() => { const t = setTimeout(onDone, 3000); return () => clearTimeout(t); }, []);
  const colors = { ok: "#16a34a", err: "#dc2626", info: "#1e40af" };
  return (
    <div style={{
      // Moved to top-right so it no longer overlaps with the floating AI
      // Assistant button in the bottom-right corner.
      position: "fixed", top: 24, right: 24, zIndex: 10001,
      padding: "12px 18px", borderRadius: 9, fontSize: 13, fontWeight: 500,
      background: "#fff", borderLeft: `4px solid ${colors[type] || colors.info}`,
      boxShadow: "0 8px 30px rgba(0,0,0,.15)", color: colors[type] || colors.info,
      animation: "slideDown .2s ease", maxWidth: 340,
    }}>{msg}</div>
  );
}

export function useToast() {
  const [toast, setToast] = useState(null);
  const show = (msg, type = "ok") => setToast({ msg, type });
  const el = toast ? <Toast msg={toast.msg} type={toast.type} onDone={() => setToast(null)} /> : null;
  return [show, el];
}

function EmptyState({ icon = "⬡", text, sub }) {
  return (
    <div style={{ textAlign: "center", padding: "60px 40px", color: "#94a3b8" }}>
      <div style={{ fontSize: 44, opacity: .25, marginBottom: 14 }}>{icon}</div>
      <div style={{ fontSize: 15, fontWeight: 600, color: "#64748b" }}>{text}</div>
      {sub && <div style={{ fontSize: 13, marginTop: 6 }}>{sub}</div>}
    </div>
  );
}

function Spinner() {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: 60 }}>
      <div style={{ width: 32, height: 32, borderRadius: "50%", border: "3px solid #e2e8f0", borderTopColor: "#1e40af", animation: "spin .6s linear infinite" }} />
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
    </div>
  );
}

// ─── EXCEL IMPORT BUTTON ──────────────────────────────────────
function ExcelImportButton({ label, templateUrl, importFn, requiredCols, token }) {
  const [file,    setFile]    = useState(null);
  const [parsed,  setParsed]  = useState(null);
  const [open,    setOpen]    = useState(false);
  const [loading, setLoading] = useState(false);
  const fileRef = useRef(null);

  const loadSheetJS = () => new Promise((res, rej) => {
    if (window.XLSX) return res();
    const s = document.createElement("script");
    s.src = "/xlsx.full.min.js";  // local copy for air-gapped LAN
    s.onload = res; s.onerror = rej;
    document.head.appendChild(s);
  });

  const parse = async (f) => {
    try {
      await loadSheetJS();
      const buf = await f.arrayBuffer();
      const wb  = window.XLSX.read(buf, { type: "array" });
      const ws  = wb.Sheets[wb.SheetNames[0]];
      const raw = window.XLSX.utils.sheet_to_json(ws, { header: 1 });
      // Find header row
      let hi = 0;
      for (let i = 0; i < Math.min(raw.length, 5); i++) {
        if (requiredCols.some(c => raw[i]?.includes(c))) { hi = i; break; }
      }
      const headers = raw[hi].map(h => String(h || "").trim());
      const rows = raw.slice(hi + 1)
        .filter(r => r.some(v => v !== null && v !== undefined && v !== ""))
        .map(r => { const o = {}; headers.forEach((h, i) => o[h] = r[i] ?? ""); return o; });
      setFile(f); setParsed({ headers, rows }); setOpen(true);
    } catch (e) {
      alert("Failed to parse file: " + e.message);
    }
  };

  const doImport = async () => {
    if (!parsed) return;
    setLoading(true);
    try {
      await importFn(parsed.rows);
      setOpen(false); setFile(null); setParsed(null);
    } catch(e) {
      alert("Import failed: " + e.message);
    } finally { setLoading(false); }
  };

  const downloadTemplate = async () => {
    try {
      const res = await fetch(templateUrl, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error("Failed to download template");
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = templateUrl.split("/").pop() + ".xlsx";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch(e) { alert(e.message); }
  };

  return (
    <>
      <div style={{ display: "flex", gap: 8 }}>
        <Btn size="sm" onClick={downloadTemplate}>⬇ Template</Btn>
        <Btn size="sm" variant="primary" onClick={() => fileRef.current?.click()}>📥 {label}</Btn>
        <input
          ref={fileRef} type="file" accept=".xlsx,.csv"
          style={{ display: "none" }}
          onChange={e => { const f = e.target.files[0]; if (f) parse(f); e.target.value = ""; }}
        />
      </div>

      <Modal open={open} onClose={() => setOpen(false)} title={`Preview — ${parsed?.rows?.length || 0} rows to import`} wide>
        <p style={{ fontSize: 12, color: "#64748b", marginBottom: 16 }}>
          Review the data below before importing. This will add or update records in the database.
        </p>
        <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 10, overflow: "hidden", marginBottom: 16, maxHeight: 320, overflowY: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr>
                {parsed?.headers?.map(h => (
                  <th key={h} style={{ padding: "8px 12px", background: "#1e40af", color: "#fff", fontWeight: 700, fontSize: 10, letterSpacing: ".08em", textTransform: "uppercase", whiteSpace: "nowrap" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {parsed?.rows?.slice(0, 10).map((r, i) => (
                <tr key={i} style={{ background: i % 2 === 0 ? "#fff" : "#f8fafc", borderBottom: "1px solid #f1f5f9" }}>
                  {parsed.headers.map(h => (
                    <td key={h} style={{ padding: "8px 12px", color: "#334155", fontFamily: "monospace", fontSize: 11 }}>{String(r[h] ?? "")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {parsed?.rows?.length > 10 && (
          <p style={{ fontSize: 11, color: "#94a3b8", marginBottom: 16 }}>
            Showing 10 of {parsed.rows.length} rows — all {parsed.rows.length} will be imported
          </p>
        )}
        <ModalActions>
          <Btn onClick={() => setOpen(false)}>Cancel</Btn>
          <Btn variant="primary" onClick={doImport} disabled={loading}>
            {loading
              ? <><div style={{ width: 14, height: 14, borderRadius: "50%", border: "2px solid rgba(255,255,255,.3)", borderTopColor: "#fff", animation: "spin .6s linear infinite" }} /> Importing…</>
              : `⬆ Import ${parsed?.rows?.length || 0} Records →`
            }
          </Btn>
        </ModalActions>
      </Modal>
    </>
  );
}

// ─── PLANTS PAGE ──────────────────────────────────────────────
export function PlantsPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [plants,  setPlants]  = useState([]);
  const [loading, setLoading] = useState(true);
  const [modal,   setModal]   = useState(false);
  const [editing, setEditing] = useState(null);
  const [form,    setForm]    = useState({ plant_code: "", plant_name: "", location: "", timezone: "Asia/Kolkata" });
  const [saving,  setSaving]  = useState(false);

  const load = useCallback(async () => {
    try { setPlants(await api.get("/api/plants/", token)); }
    catch { toast("Failed to load plants", "err"); }
    finally { setLoading(false); }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  const openAdd = () => {
    setEditing(null);
    setForm({ plant_code: "", plant_name: "", location: "", timezone: "Asia/Kolkata" });
    setModal(true);
  };

  const openEdit = (p) => {
    setEditing(p);
    setForm({ plant_code: p.plant_code, plant_name: p.plant_name, location: p.location || "", timezone: p.timezone || "Asia/Kolkata" });
    setModal(true);
  };

  const save = async () => {
    if (!form.plant_code || !form.plant_name) { toast("Code and name required", "err"); return; }
    setSaving(true);
    try {
      if (editing) {
        await api.put(`/api/plants/${editing.id}`, { plant_name: form.plant_name, location: form.location, timezone: form.timezone }, token);
        toast("Plant updated ✓");
      } else {
        await api.post("/api/plants/", form, token);
        toast("Plant created ✓");
      }
      setModal(false); load();
    } catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  const deactivate = async (p) => {
    if (!confirm(`Deactivate plant "${p.plant_name}"?`)) return;
    try { await api.delete(`/api/plants/${p.id}`, token); toast("Plant deactivated"); load(); }
    catch (e) { toast(e.message, "err"); }
  };

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 20 }}>
        <Btn variant="primary" onClick={openAdd}>+ Add Plant</Btn>
      </div>
      <Card>
        {loading ? <Spinner /> : plants.length === 0 ? <EmptyState text="No plants yet" sub="Add your first plant to get started" /> : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr>{["Code", "Name", "Location", "Lines", "Status", "Actions"].map(h => (
                <th key={h} style={{ padding: "10px 14px", textAlign: "left", fontSize: 10, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: "#64748b", borderBottom: "2px solid #e2e8f0" }}>{h}</th>
              ))}</tr>
            </thead>
            <tbody>
              {plants.map(p => (
                <tr key={p.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                  <td style={{ padding: "12px 14px", fontFamily: "monospace", fontWeight: 700, color: "#1e40af" }}>{p.plant_code}</td>
                  <td style={{ padding: "12px 14px", fontWeight: 600, color: "#0f172a" }}>{p.plant_name}</td>
                  <td style={{ padding: "12px 14px", color: "#64748b" }}>{p.location || "—"}</td>
                  <td style={{ padding: "12px 14px", fontFamily: "monospace" }}>{p.total_lines || 0}</td>
                  <td style={{ padding: "12px 14px" }}><Pill label={p.is_active ? "Active" : "Inactive"} color={p.is_active ? "green" : "gray"} /></td>
                  <td style={{ padding: "12px 14px" }}>
                    <div style={{ display: "flex", gap: 8 }}>
                      <Btn size="sm" onClick={() => openEdit(p)}>Edit</Btn>
                      <Btn size="sm" variant="danger" onClick={() => deactivate(p)}>Deactivate</Btn>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Modal open={modal} onClose={() => setModal(false)} title={editing ? `Edit — ${editing.plant_name}` : "Add Plant"}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          <FF label="Plant Code *">
            <Input value={form.plant_code} onChange={e => setForm(f => ({ ...f, plant_code: e.target.value }))} placeholder="TBI-BHW" disabled={!!editing} />
          </FF>
          <FF label="Plant Name *">
            <Input value={form.plant_name} onChange={e => setForm(f => ({ ...f, plant_name: e.target.value }))} placeholder="Toyota Boshoku..." />
          </FF>
          <FF label="Location">
            <Input value={form.location} onChange={e => setForm(f => ({ ...f, location: e.target.value }))} placeholder="City, State" />
          </FF>
          <FF label="Timezone">
            <Input value={form.timezone} onChange={e => setForm(f => ({ ...f, timezone: e.target.value }))} />
          </FF>
        </div>
        <ModalActions>
          <Btn onClick={() => setModal(false)}>Cancel</Btn>
          <Btn variant="primary" onClick={save} disabled={saving}>{saving ? "Saving…" : editing ? "Save Changes" : "Create Plant"}</Btn>
        </ModalActions>
      </Modal>
    </div>
  );
}

// ─── ZONES PAGE ───────────────────────────────────────────────
// Quick-pick palette for zone colours.  Deliberately the same hues the rest of
// the panel already uses (blue / green / amber / red / violet / teal / slate)
// so a zone tint never clashes with a status colour that means something.
const ZONE_SWATCHES = ["#1e40af", "#16a34a", "#b45309", "#dc2626",
                       "#6d28d9", "#0f766e", "#475569"];

export function ZonesPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [zones,      setZones]      = useState([]);
  const [plants,     setPlants]     = useState([]);
  const [lines,      setLines]      = useState([]);
  const [loading,    setLoading]    = useState(true);

  // Wizard modal
  const [wizOpen,    setWizOpen]    = useState(false);
  const [wizZone,    setWizZone]    = useState(null);   // null = new zone being created
  const [subPage,    setSubPage]    = useState(0);
  const [saving,     setSaving]     = useState(false);
  const [subLoading, setSubLoading] = useState(false);

  // Sub-page data
  const [infoForm,   setInfoForm]   = useState({ plant_id: "", zone_code: "", zone_name: "", description: "", color: "" });
  const [selLines,   setSelLines]   = useState([]);
  const [shifts,     setShifts]     = useState([]);
  const [breaks,     setBreaks]     = useState([]);
  const [slotShift,  setSlotShift]  = useState("A");
  const [slots,      setSlots]      = useState([]);
  const [zoneModels, setZoneModels] = useState([]);
  const [machines,   setMachines]   = useState([]);

  const SUB_LABELS = ["① Zone Info", "② Lines", "③ Shifts & Breaks", "④ Hourly Slots", "⑤ Models", "⑥ Machines"];

  // ── Helpers ───────────────────────────────────────────────

  function defaultShifts() {
    return [
      { shift_name:"A",      start_time:"08:30", end_time:"17:15", crosses_midnight:false, total_plan:1860, working_minutes:465, startup_delay_min:5, is_production:true,  ot_enabled:false, ot_end_time:"" },
      { shift_name:"B",      start_time:"18:30", end_time:"03:15", crosses_midnight:true,  total_plan:1860, working_minutes:465, startup_delay_min:5, is_production:true,  ot_enabled:false, ot_end_time:"" },
      { shift_name:"GAP_AB", start_time:"17:15", end_time:"18:30", crosses_midnight:false, total_plan:0,    working_minutes:0,   startup_delay_min:0, is_production:false, ot_enabled:false, ot_end_time:"" },
      { shift_name:"GAP_BA", start_time:"03:15", end_time:"08:30", crosses_midnight:false, total_plan:0,    working_minutes:0,   startup_delay_min:0, is_production:false, ot_enabled:false, ot_end_time:"" },
    ];
  }

  function defaultBreaks() {
    return [
      { break_name:"Morning Tea Break",   start_time:"10:00", end_time:"10:10", crosses_midnight:false, applies_to_shifts:"A"   },
      { break_name:"Lunch Break",         start_time:"12:00", end_time:"12:35", crosses_midnight:false, applies_to_shifts:"A"   },
      { break_name:"Evening Tea Break",   start_time:"14:30", end_time:"14:40", crosses_midnight:false, applies_to_shifts:"A"   },
      { break_name:"Dinner Break 1",      start_time:"18:00", end_time:"18:10", crosses_midnight:false, applies_to_shifts:"B"   },
      { break_name:"Tea Break",           start_time:"20:00", end_time:"20:10", crosses_midnight:false, applies_to_shifts:"B"   },
      { break_name:"Dinner Break 2",      start_time:"22:00", end_time:"22:35", crosses_midnight:false, applies_to_shifts:"B"   },
      { break_name:"Night Tea Break",     start_time:"01:00", end_time:"01:10", crosses_midnight:true,  applies_to_shifts:"B"   },
      { break_name:"Early Morning Break", start_time:"04:00", end_time:"04:10", crosses_midnight:true,  applies_to_shifts:"B"   },
    ];
  }

  // ── Data loading ─────────────────────────────────────────

  const load = useCallback(async () => {
    try {
      const [z, p, l] = await Promise.all([
        api.get("/api/zones/", token),
        api.get("/api/plants/", token),
        api.get("/api/lines/", token),
      ]);
      setZones(Array.isArray(z) ? z : []);
      setPlants(Array.isArray(p) ? p : []);
      setLines(Array.isArray(l) ? l : []);
    } catch { toast("Failed to load", "err"); }
    finally { setLoading(false); }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  const loadShifts = async (zoneId) => {
    try {
      const s = await api.get(`/api/zones/${zoneId}/shifts`, token);
      if (Array.isArray(s) && s.length) {
        setShifts(s.map(x => ({
          shift_name: x.shift_name, start_time: x.start_time?.slice(0,5)||"",
          end_time: x.end_time?.slice(0,5)||"", crosses_midnight: x.crosses_midnight||false,
          total_plan: x.total_plan||0, working_minutes: x.working_minutes||0,
          startup_delay_min: x.startup_delay_min||5, is_production: x.is_production!==false,
          ot_enabled: x.ot_enabled||false, ot_end_time: x.ot_end_time?.slice(0,5)||"",
          ot_plan: x.ot_plan ?? "",
        })));
      } else { setShifts(defaultShifts()); }
    } catch { setShifts(defaultShifts()); }
  };

  const loadBreaks = async (zoneId) => {
    try {
      const b = await api.get(`/api/zones/${zoneId}/breaks`, token);
      if (Array.isArray(b) && b.length) {
        setBreaks(b.map(x => ({
          break_name: x.break_name, start_time: x.start_time?.slice(0,5)||"",
          end_time: x.end_time?.slice(0,5)||"", crosses_midnight: x.crosses_midnight||false,
          applies_to_shifts: x.applies_to_shifts||"A,B",
        })));
      } else { setBreaks(defaultBreaks()); }
    } catch { setBreaks(defaultBreaks()); }
  };

  const loadSlots = async (zoneId, shiftName) => {
    if (!zoneId) return;
    setSubLoading(true);
    try {
      const s = await api.get(`/api/zones/${zoneId}/hourly-slots?shift_name=${shiftName}`, token);
      setSlots(Array.isArray(s) ? s.map(x => ({
        shift_name: x.shift_name, slot_label: x.slot_label,
        start_time: x.start_time?.slice(0,5)||"", end_time: x.end_time?.slice(0,5)||"",
        crosses_midnight: x.crosses_midnight||false,
        working_minutes: x.working_minutes, plan_pieces: x.plan_pieces, slot_order: x.slot_order,
      })) : []);
    } catch { setSlots([]); }
    finally { setSubLoading(false); }
  };

  const loadModels = async (zoneId) => {
    setSubLoading(true);
    try {
      const m = await api.get(`/api/zones/${zoneId}/models`, token);
      setZoneModels(Array.isArray(m) ? m : []);
    } catch { setZoneModels([]); }
    finally { setSubLoading(false); }
  };

  const loadMachines = async (zoneId) => {
    setSubLoading(true);
    try {
      const m = await api.get(`/api/zones/${zoneId}/machines`, token);
      setMachines(Array.isArray(m) ? m : []);
    } catch { setMachines([]); }
    finally { setSubLoading(false); }
  };

  // ── Open wizard ───────────────────────────────────────────

  const openAdd = () => {
    setWizZone(null); setSubPage(0);
    setInfoForm({ plant_id: plants[0]?.id||"", zone_code:"", zone_name:"", description:"", color:"" });
    setSelLines([]); setShifts(defaultShifts()); setBreaks(defaultBreaks());
    setSlots([]); setZoneModels([]); setMachines([]);
    setWizOpen(true);
  };

  const openConfigure = (zone) => {
    setWizZone(zone); setSubPage(0);
    setInfoForm({ plant_id:zone.plant_id, zone_code:zone.zone_code, zone_name:zone.zone_name, description:zone.description||"", color:zone.color||"" });
    setSelLines(lines.filter(l => String(l.zone_id) === String(zone.id)).map(l => l.id));
    loadShifts(zone.id); loadBreaks(zone.id);
    setSlots([]); setZoneModels([]); setMachines([]);
    setWizOpen(true);
  };

  const handleSubPage = (page) => {
    setSubPage(page);
    if (!wizZone) return;
    if (page === 3) loadSlots(wizZone.id, slotShift);
    if (page === 4) loadModels(wizZone.id);
    if (page === 5) loadMachines(wizZone.id);
  };

  // ── Save functions ────────────────────────────────────────

  const saveInfo = async () => {
    if (!infoForm.zone_code || !infoForm.zone_name || !infoForm.plant_id) { toast("All fields required", "err"); return; }
    setSaving(true);
    try {
      if (wizZone) {
        // color is sent even when empty — "" is how the API is told to CLEAR it
        // (an omitted/None field means "leave unchanged").
        await api.put(`/api/zones/${wizZone.id}`, { zone_name:infoForm.zone_name, description:infoForm.description, color:infoForm.color||"" }, token);
        setWizZone(prev => ({ ...prev, zone_name:infoForm.zone_name, description:infoForm.description, color:infoForm.color||null }));
        toast("Zone updated ✓");
      } else {
        const z = await api.post("/api/zones/", { plant_id:parseInt(infoForm.plant_id), zone_code:infoForm.zone_code, zone_name:infoForm.zone_name, description:infoForm.description, color:infoForm.color||null }, token);
        setWizZone(z);
        toast("Zone created ✓");
      }
      load(); setSubPage(1);
    } catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  const saveLines = async () => {
    if (!wizZone) { toast("Create zone first", "err"); return; }
    setSaving(true);
    try {
      const current = lines.filter(l => String(l.zone_id) === String(wizZone.id)).map(l => l.id);
      for (const id of selLines) { if (!current.includes(id)) await api.post(`/api/zones/${wizZone.id}/lines/${id}`, {}, token); }
      for (const id of current)  { if (!selLines.includes(id)) await api.delete(`/api/zones/${wizZone.id}/lines/${id}`, token); }
      toast("Lines saved ✓"); load();
      await loadShifts(wizZone.id); await loadBreaks(wizZone.id);
      setSubPage(2);
    } catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  const saveShiftsBreaks = async () => {
    if (!wizZone) { toast("Create zone first", "err"); return; }
    const validShifts = shifts.filter(s => s.shift_name && s.start_time && s.end_time);
    if (!validShifts.length) { toast("Add at least one shift", "err"); return; }
    setSaving(true);
    try {
      await api.put(`/api/zones/${wizZone.id}/shifts`, validShifts.map(s => ({ ...s, ot_end_time: s.ot_end_time || null })), token);
      const validBreaks = breaks.filter(b => b.break_name && b.start_time && b.end_time);
      await api.put(`/api/zones/${wizZone.id}/breaks`, validBreaks, token);
      toast("Shifts & Breaks saved ✓");
    } catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  const toggleOT = async (shiftName, enabled, otEnd, otPlan) => {
    if (!wizZone) return;
    const plan = (otPlan === "" || otPlan == null) ? null : Math.max(0, Math.round(Number(otPlan)));
    try {
      const r = await api.put(`/api/zones/${wizZone.id}/shifts/${shiftName}/ot`,
        { ot_enabled: enabled, ot_end_time: enabled ? (otEnd || null) : null, ot_plan: enabled ? plan : null },
        token);
      toast(`OT ${enabled ? "ON (live)" : "OFF"} for Shift ${shiftName} — ${r?.lines_affected ?? 0} line(s) ✓`);
      if (wizZone) load();
    } catch (e) { toast(e.message, "err"); }
  };

  const saveSlots = async () => {
    if (!wizZone) return;
    setSaving(true);
    try {
      await api.put(`/api/zones/${wizZone.id}/hourly-slots`, slots.filter(s => s.slot_label && s.start_time && s.end_time), token);
      toast("Hourly slots saved ✓");
    } catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  const deactivate = async (z) => {
    if (!confirm(`Deactivate zone "${z.zone_name}"? All lines will be unassigned.`)) return;
    try { await api.delete(`/api/zones/${z.id}`, token); toast("Zone deactivated"); load(); }
    catch (e) { toast(e.message, "err"); }
  };

  // ── Field mutators ────────────────────────────────────────

  const setShiftFld  = (i, k, v) => setShifts(prev  => { const a=[...prev];  a[i]={...a[i],[k]:v}; return a; });
  const setBreakFld  = (i, k, v) => setBreaks(prev  => { const a=[...prev];  a[i]={...a[i],[k]:v}; return a; });
  const setSlotFld   = (i, k, v) => setSlots(prev   => { const a=[...prev];  a[i]={...a[i],[k]:v}; return a; });
  const addBreak  = () => setBreaks(prev  => [...prev,  { break_name:"", start_time:"", end_time:"", crosses_midnight:false, applies_to_shifts:"A" }]);
  const rmBreak   = (i)=> setBreaks(prev  => prev.filter((_,j)=>j!==i));
  const addSlot   = () => setSlots(prev   => [...prev,  { shift_name:slotShift, slot_label:"", start_time:"", end_time:"", crosses_midnight:false, working_minutes:60, plan_pieces:240, slot_order:prev.length+1 }]);
  const rmSlot    = (i)=> setSlots(prev   => prev.filter((_,j)=>j!==i));

  // ── Sub-page renderers ────────────────────────────────────

  const miniInp = { ...inputStyle, padding:"7px 9px", fontSize:12 };

  const renderInfo = () => (
    <div>
      <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:16 }}>
        <FF label="Plant *">
          <Select value={infoForm.plant_id} onChange={e=>setInfoForm(f=>({...f,plant_id:e.target.value}))} disabled={!!wizZone}>
            <option value="">Select plant…</option>
            {plants.map(p=><option key={p.id} value={p.id}>{p.plant_name}</option>)}
          </Select>
        </FF>
        <FF label="Zone Code *">
          <Input value={infoForm.zone_code} onChange={e=>setInfoForm(f=>({...f,zone_code:e.target.value}))} placeholder="ZONE-1" disabled={!!wizZone}/>
        </FF>
        <FF label="Zone Name *">
          <Input value={infoForm.zone_name} onChange={e=>setInfoForm(f=>({...f,zone_name:e.target.value}))} placeholder="e.g. Assembly Line A"/>
        </FF>
        <FF label="Description">
          <Input value={infoForm.description} onChange={e=>setInfoForm(f=>({...f,description:e.target.value}))} placeholder="Optional"/>
        </FF>
        <FF label="Zone Colour" hint="Used wherever screens group by zone. Leave empty for the default grey.">
          <div style={{ display:"flex", alignItems:"center", gap:10, flexWrap:"wrap" }}>
            <input type="color"
                   value={infoForm.color || "#1e40af"}
                   onChange={e=>setInfoForm(f=>({...f,color:e.target.value}))}
                   style={{ width:46, height:34, padding:2, border:"1.5px solid #e2e8f0",
                             borderRadius:7, background:"#fff", cursor:"pointer" }}/>
            <Input value={infoForm.color || ""}
                   onChange={e=>setInfoForm(f=>({...f,color:e.target.value}))}
                   placeholder="#1e40af"
                   style={{ width:120, fontFamily:"monospace" }}/>
            {ZONE_SWATCHES.map(c => (
              <button key={c} type="button" title={c}
                      onClick={()=>setInfoForm(f=>({...f,color:c}))}
                      style={{ width:22, height:22, borderRadius:6, cursor:"pointer",
                                background:c,
                                border: (infoForm.color||"").toLowerCase()===c
                                          ? "2.5px solid #0f172a" : "1.5px solid #e2e8f0" }}/>
            ))}
            {infoForm.color && (
              <button type="button" onClick={()=>setInfoForm(f=>({...f,color:""}))}
                      style={{ border:"none", background:"none", cursor:"pointer",
                                fontSize:11, color:"#64748b", textDecoration:"underline" }}>
                clear
              </button>
            )}
          </div>
        </FF>
      </div>
      <ModalActions>
        <Btn onClick={()=>setWizOpen(false)}>Cancel</Btn>
        <Btn variant="primary" onClick={saveInfo} disabled={saving}>
          {saving ? "Saving…" : wizZone ? "Update & Next →" : "Create Zone & Next →"}
        </Btn>
      </ModalActions>
    </div>
  );

  const renderLines = () => (
    <div>
      <p style={{ fontSize:13, color:"#64748b", marginBottom:16 }}>
        Select lines to assign to this zone. A line can only belong to one zone.
      </p>
      <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fill,minmax(220px,1fr))", gap:10, maxHeight:300, overflowY:"auto", marginBottom:16 }}>
        {lines.map(l => {
          const checked   = selLines.includes(l.id);
          const otherZone = l.zone_id && String(l.zone_id) !== String(wizZone?.id);
          return (
            <label key={l.id} style={{ display:"flex", alignItems:"center", gap:10, padding:"10px 14px", borderRadius:8, cursor:"pointer", background:checked?"rgba(30,64,175,.06)":"#f8fafc", border:`1px solid ${checked?"rgba(30,64,175,.25)":"#e2e8f0"}`, transition:"all .12s" }}>
              <input type="checkbox" checked={checked} onChange={()=>setSelLines(prev=>prev.includes(l.id)?prev.filter(x=>x!==l.id):[...prev,l.id])} style={{ width:15,height:15,accentColor:"#1e40af" }}/>
              <div>
                <div style={{ fontSize:13,fontWeight:600,color:"#0f172a" }}>{l.line_name}</div>
                <div style={{ fontSize:10,color:"#94a3b8" }}>
                  {l.line_code}
                  {otherZone && <span style={{ color:"#d97706",marginLeft:6 }}>· other zone</span>}
                </div>
              </div>
            </label>
          );
        })}
      </div>
      <ModalActions>
        <Btn onClick={()=>setSubPage(0)}>← Back</Btn>
        <Btn variant="primary" onClick={saveLines} disabled={saving||!wizZone}>
          {saving ? "Saving…" : "Save Lines & Next →"}
        </Btn>
      </ModalActions>
    </div>
  );

  const addShift = () => setShifts(prev => {
    const existingNames = prev.map(s => s.shift_name);
    // suggest next letter not already used
    const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("").filter(l => !existingNames.includes(l));
    const name = letters[0] || `SHIFT${prev.filter(s=>s.is_production).length + 1}`;
    return [...prev, { shift_name:name, start_time:"", end_time:"", crosses_midnight:false, total_plan:0, working_minutes:0, startup_delay_min:5, is_production:true, ot_enabled:false, ot_end_time:"" }];
  });

  const addGap = () => setShifts(prev => {
    const existingNames = prev.map(s => s.shift_name);
    const name = `GAP_${Date.now().toString().slice(-4)}`;
    return [...prev, { shift_name:name, start_time:"", end_time:"", crosses_midnight:false, total_plan:0, working_minutes:0, startup_delay_min:0, is_production:false, ot_enabled:false, ot_end_time:"" }];
  });

  const rmShift = (shiftName) => setShifts(prev => prev.filter(s => s.shift_name !== shiftName));

  const renderShiftsBreaks = () => {
    const prodShifts = shifts.filter(s=>s.is_production);
    const gapShifts  = shifts.filter(s=>!s.is_production);

    // build dynamic shift name options for break applies_to dropdown
    const shiftOptions = prodShifts.map(s=>s.shift_name);
    const shiftOptionPairs = [
      ...shiftOptions.map(n => ({ value:n, label:`Shift ${n}` })),
      ...shiftOptions.length > 1 ? [{ value: shiftOptions.join(","), label:"All Shifts" }] : [],
    ];

    return (
      <div style={{ maxHeight:"65vh", overflowY:"auto", paddingRight:4 }}>
        {/* Production Shifts header */}
        <div style={{ display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:12 }}>
          <div style={{ fontSize:10,fontWeight:700,letterSpacing:".1em",textTransform:"uppercase",color:"#64748b" }}>Production Shifts</div>
          <Btn size="sm" onClick={addShift}>+ Add Shift</Btn>
        </div>

        {prodShifts.map(sh => {
          const idx = shifts.findIndex(s=>s.shift_name===sh.shift_name);
          const isDefaultAB = sh.shift_name==="A" || sh.shift_name==="B";
          return (
            <div key={sh.shift_name} style={{ background:"#f8fafc",border:"1px solid #e2e8f0",borderRadius:10,padding:14,marginBottom:12 }}>
              {/* Header row */}
              <div style={{ display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:12 }}>
                <div style={{ display:"flex",alignItems:"center",gap:10 }}>
                  {/* Editable shift name */}
                  <div style={{ display:"flex",alignItems:"center",gap:6 }}>
                    <span style={{ fontSize:11,fontWeight:700,color:"#64748b" }}>SHIFT</span>
                    <input
                      value={sh.shift_name}
                      onChange={e => {
                        const newName = e.target.value.toUpperCase().replace(/\s/g,"");
                        // avoid duplicate names
                        if (newName && shifts.some((s,i)=>s.shift_name===newName && i!==idx)) return;
                        setShiftFld(idx,"shift_name",newName);
                      }}
                      disabled={isDefaultAB}
                      maxLength={10}
                      style={{ ...miniInp,width:70,fontWeight:800,fontSize:14,color:"#0f172a",textAlign:"center",padding:"4px 8px",...(isDefaultAB?{background:"#f1f5f9",color:"#64748b"}:{}) }}
                      title={isDefaultAB?"Default shifts A and B cannot be renamed":"Rename this shift"}
                    />
                  </div>
                  <label style={{ display:"flex",alignItems:"center",gap:5,fontSize:11,color:"#64748b" }}>
                    <input type="checkbox" checked={sh.crosses_midnight} onChange={e=>setShiftFld(idx,"crosses_midnight",e.target.checked)}/> crosses midnight
                  </label>
                </div>

                <div style={{ display:"flex",alignItems:"center",gap:10 }}>
                  {/* Remove button — only for non-default shifts */}
                  {!isDefaultAB && (
                    <button
                      onClick={()=>{ if(confirm(`Remove Shift ${sh.shift_name}?`)) rmShift(sh.shift_name); }}
                      title="Remove this shift"
                      style={{ background:"rgba(220,38,38,.08)",border:"1px solid rgba(220,38,38,.2)",color:"#dc2626",borderRadius:6,cursor:"pointer",fontSize:12,fontWeight:700,padding:"3px 8px" }}
                    >✕ Remove</button>
                  )}
                </div>
              </div>

              {/* Time / plan grid */}
              <div style={{ display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(105px,1fr))",gap:10 }}>
                <FF label="Start"><input type="time" value={sh.start_time} onChange={e=>setShiftFld(idx,"start_time",e.target.value)} style={miniInp}/></FF>
                <FF label="End"><input type="time" value={sh.end_time} onChange={e=>setShiftFld(idx,"end_time",e.target.value)} style={miniInp}/></FF>
                <FF label="Total Plan"><input type="number" min="0" value={sh.total_plan} onChange={e=>setShiftFld(idx,"total_plan",parseInt(e.target.value)||0)} style={miniInp}/></FF>
                <FF label="Working Min"><input type="number" min="0" value={sh.working_minutes} onChange={e=>setShiftFld(idx,"working_minutes",parseInt(e.target.value)||0)} style={miniInp}/></FF>
                <FF label="Startup Delay"><input type="number" min="0" value={sh.startup_delay_min} onChange={e=>setShiftFld(idx,"startup_delay_min",parseInt(e.target.value)||0)} style={miniInp}/></FF>
              </div>

              {/* ── OT (overtime) — live toggle for ALL lines in this zone ── */}
              <div style={{ marginTop:10, padding:"10px 12px", borderRadius:8, background:"rgba(217,119,6,.05)", border:"1px solid rgba(217,119,6,.25)" }}>
                <div style={{ display:"flex", alignItems:"center", gap:14, flexWrap:"wrap" }}>
                  <label style={{ display:"flex", alignItems:"center", gap:6, fontSize:12, fontWeight:700, color:"#b45309", cursor:"pointer" }}>
                    <input type="checkbox" checked={!!sh.ot_enabled} onChange={e=>setShiftFld(idx,"ot_enabled",e.target.checked)}/>
                    ⏱ OT enabled
                  </label>
                  <FF label="OT End"><input type="time" value={sh.ot_end_time||""} onChange={e=>setShiftFld(idx,"ot_end_time",e.target.value)} style={{ ...miniInp, width:100 }}/></FF>
                  <FF label="OT Target (pcs)"><input type="number" min="0" value={sh.ot_plan ?? ""} onChange={e=>setShiftFld(idx,"ot_plan",e.target.value)} style={{ ...miniInp, width:120 }} placeholder="manual"/></FF>
                  <Btn size="sm" onClick={()=>toggleOT(sh.shift_name, !!sh.ot_enabled, sh.ot_end_time, sh.ot_plan)}>Apply OT</Btn>
                </div>
                <div style={{ fontSize:10, color:"#92400e", marginTop:6 }}>
                  “Apply OT” turns OT {sh.ot_enabled ? "ON" : "OFF"} <b>live</b> for every line in this zone. OT window = shift end → OT End.
                </div>
              </div>
            </div>
          );
        })}

        {/* Gap Periods */}
        <div style={{ display:"flex",alignItems:"center",justifyContent:"space-between",margin:"16px 0 10px" }}>
          <div style={{ fontSize:10,fontWeight:700,letterSpacing:".1em",textTransform:"uppercase",color:"#64748b" }}>Gap Periods</div>
          <Btn size="sm" onClick={addGap}>+ Add Gap</Btn>
        </div>
        {gapShifts.length===0 && (
          <div style={{ fontSize:12,color:"#94a3b8",padding:"8px 0",marginBottom:8 }}>No gap periods defined.</div>
        )}
        {gapShifts.map(sh => {
          const idx = shifts.findIndex(s=>s.shift_name===sh.shift_name);
          const isDefaultGap = sh.shift_name==="GAP_AB" || sh.shift_name==="GAP_BA";
          return (
            <div key={sh.shift_name} style={{ display:"flex",alignItems:"center",gap:8,padding:"10px 14px",background:"#f8fafc",borderRadius:8,border:"1px solid #e2e8f0",marginBottom:8,flexWrap:"wrap" }}>
              {/* Editable gap name */}
              <input
                value={sh.shift_name}
                onChange={e=>{ const v=e.target.value.toUpperCase().replace(/\s/g,""); if(!shifts.some((s,i)=>s.shift_name===v&&i!==idx)) setShiftFld(idx,"shift_name",v); }}
                disabled={isDefaultGap}
                maxLength={12}
                style={{ ...miniInp,width:90,fontWeight:700,fontSize:12,color:"#64748b",textAlign:"center",...(isDefaultGap?{background:"#f1f5f9"}:{}) }}
              />
              <FF label="Start">
                <input type="time" value={sh.start_time} onChange={e=>setShiftFld(idx,"start_time",e.target.value)} style={{ ...miniInp,width:90 }}/>
              </FF>
              <FF label="End">
                <input type="time" value={sh.end_time} onChange={e=>setShiftFld(idx,"end_time",e.target.value)} style={{ ...miniInp,width:90 }}/>
              </FF>
              <label style={{ display:"flex",alignItems:"center",gap:4,fontSize:11,color:"#64748b",whiteSpace:"nowrap" }}>
                <input type="checkbox" checked={sh.crosses_midnight} onChange={e=>setShiftFld(idx,"crosses_midnight",e.target.checked)}/> midnight
              </label>
              {!isDefaultGap && (
                <button onClick={()=>rmShift(sh.shift_name)} style={{ background:"none",border:"none",color:"#dc2626",cursor:"pointer",fontSize:16,marginLeft:"auto" }}>✕</button>
              )}
            </div>
          );
        })}

        {/* Breaks */}
        <div style={{ fontSize:10,fontWeight:700,letterSpacing:".1em",textTransform:"uppercase",color:"#64748b",margin:"20px 0 10px" }}>Breaks</div>
        {breaks.map((b,i) => (
          <div key={i} style={{ display:"flex",alignItems:"center",gap:8,marginBottom:8,flexWrap:"wrap" }}>
            <input placeholder="Break name" value={b.break_name} onChange={e=>setBreakFld(i,"break_name",e.target.value)} style={{ ...miniInp,flex:"2 1 140px" }}/>
            <input type="time" value={b.start_time} onChange={e=>setBreakFld(i,"start_time",e.target.value)} style={{ ...miniInp,width:90,flex:"0 0 90px" }}/>
            <input type="time" value={b.end_time}   onChange={e=>setBreakFld(i,"end_time",e.target.value)}   style={{ ...miniInp,width:90,flex:"0 0 90px" }}/>
            <select value={b.applies_to_shifts} onChange={e=>setBreakFld(i,"applies_to_shifts",e.target.value)} style={{ ...miniInp,flex:"0 0 100px" }}>
              {shiftOptionPairs.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}
              <option value="A,B">All (A,B)</option>
            </select>
            <label style={{ display:"flex",alignItems:"center",gap:4,fontSize:11,color:"#64748b",whiteSpace:"nowrap" }}>
              <input type="checkbox" checked={b.crosses_midnight} onChange={e=>setBreakFld(i,"crosses_midnight",e.target.checked)}/> midnight
            </label>
            <button onClick={()=>rmBreak(i)} style={{ background:"none",border:"none",color:"#dc2626",cursor:"pointer",fontSize:18,padding:"0 2px" }}>✕</button>
          </div>
        ))}
        <Btn size="sm" onClick={addBreak} style={{ marginBottom:8 }}>+ Add Break</Btn>

        <ModalActions>
          <Btn onClick={()=>setSubPage(1)}>← Back</Btn>
          <Btn variant="primary" onClick={saveShiftsBreaks} disabled={saving||!wizZone}>
            {saving?"Saving…":"Save Shifts & Breaks ✓"}
          </Btn>
        </ModalActions>
      </div>
    );
  };

  const renderHourlySlots = () => (
    <div>
      <div style={{ display:"flex",alignItems:"flex-end",gap:12,marginBottom:16 }}>
        <FF label="Shift Filter">
          <Select value={slotShift} onChange={e=>{ setSlotShift(e.target.value); if(wizZone) loadSlots(wizZone.id,e.target.value); }} style={{ width:110 }}>
            <option value="A">Shift A</option>
            <option value="B">Shift B</option>
          </Select>
        </FF>
        <Btn size="sm" variant="primary" onClick={addSlot}>+ Add Slot</Btn>
      </div>

      {subLoading ? <Spinner /> : slots.length===0 ? (
        <EmptyState text="No hourly slots" sub={`No slots configured for Shift ${slotShift}. Click + Add Slot to begin.`}/>
      ) : (
        <div style={{ overflowX:"auto" }}>
          <div style={{ display:"grid",gridTemplateColumns:"1fr 90px 90px 90px 70px 60px 24px",gap:6,padding:"6px 4px",borderBottom:"2px solid #e2e8f0",marginBottom:6 }}>
            {["Slot Label","Start","End","Plan Pcs","Work Min","Order",""].map(h=>(
              <div key={h} style={{ fontSize:9,fontWeight:700,letterSpacing:".08em",textTransform:"uppercase",color:"#64748b" }}>{h}</div>
            ))}
          </div>
          {slots.map((s,i)=>(
            <div key={i} style={{ display:"grid",gridTemplateColumns:"1fr 90px 90px 90px 70px 60px 24px",gap:6,marginBottom:6,alignItems:"center" }}>
              <input value={s.slot_label} onChange={e=>setSlotFld(i,"slot_label",e.target.value)} placeholder="08:30-09:30" style={miniInp}/>
              <input type="time" value={s.start_time} onChange={e=>setSlotFld(i,"start_time",e.target.value)} style={miniInp}/>
              <input type="time" value={s.end_time}   onChange={e=>setSlotFld(i,"end_time",e.target.value)}   style={miniInp}/>
              <input type="number" min="0" value={s.plan_pieces}     onChange={e=>setSlotFld(i,"plan_pieces",parseInt(e.target.value)||0)}     style={miniInp}/>
              <input type="number" min="0" value={s.working_minutes} onChange={e=>setSlotFld(i,"working_minutes",parseInt(e.target.value)||0)} style={miniInp}/>
              <input type="number" min="0" value={s.slot_order}      onChange={e=>setSlotFld(i,"slot_order",parseInt(e.target.value)||0)}      style={miniInp}/>
              <button onClick={()=>rmSlot(i)} style={{ background:"none",border:"none",color:"#dc2626",cursor:"pointer",fontSize:16,padding:0 }}>✕</button>
            </div>
          ))}
        </div>
      )}

      <ModalActions>
        <Btn onClick={()=>setSubPage(2)}>← Back</Btn>
        <Btn variant="primary" onClick={saveSlots} disabled={saving||!wizZone}>
          {saving?"Saving…":"Save Slots ✓"}
        </Btn>
      </ModalActions>
    </div>
  );

  const renderModels = () => {
    // Group by model_number (the "bit")
    const bitMap = {};
    zoneModels.forEach(m => {
      if (!bitMap[m.model_number]) bitMap[m.model_number] = [];
      bitMap[m.model_number].push(m);
    });
    const sortedBits = Object.entries(bitMap).sort((a,b)=>parseInt(a[0])-parseInt(b[0]));

    return (
      <div>
        <p style={{ fontSize:13,color:"#64748b",marginBottom:16 }}>
          Models across all lines in this zone, grouped by bit (model number). Each bit must be unique. Duplicate bits are flagged in amber.
        </p>
        {subLoading ? <Spinner /> : sortedBits.length===0 ? (
          <EmptyState text="No models assigned" sub="Configure models on individual lines via the Lines section"/>
        ) : (
          <div style={{ overflowX:"auto" }}>
            <table style={{ width:"100%",borderCollapse:"collapse",fontSize:12 }}>
              <thead>
                <tr>{["Bit #","Model Name","Line","Status"].map(h=>(
                  <th key={h} style={{ padding:"8px 12px",textAlign:"left",fontSize:9,fontWeight:700,letterSpacing:".08em",textTransform:"uppercase",color:"#64748b",borderBottom:"2px solid #e2e8f0" }}>{h}</th>
                ))}</tr>
              </thead>
              <tbody>
                {sortedBits.flatMap(([bit,entries])=>
                  entries.map((m,i)=>(
                    <tr key={`${bit}-${i}`} style={{ background:entries.length>1?"rgba(217,119,6,.04)":(parseInt(bit)%2===0?"#fff":"#f8fafc"),borderBottom:"1px solid #f1f5f9" }}>
                      {i===0 && (
                        <td rowSpan={entries.length} style={{ padding:"8px 12px",fontFamily:"monospace",fontWeight:800,color:"#1e40af",fontSize:13,borderRight:"1px solid #f1f5f9",verticalAlign:"middle",background:entries.length>1?"rgba(217,119,6,.07)":undefined }}>
                          {bit}
                        </td>
                      )}
                      <td style={{ padding:"8px 12px",fontWeight:500,color:"#0f172a" }}>{m.model_name}</td>
                      <td style={{ padding:"8px 12px",fontFamily:"monospace",fontSize:11,color:"#64748b" }}>{m.line_code}</td>
                      <td style={{ padding:"8px 12px" }}>
                        {entries.length>1
                          ? <span style={{ padding:"2px 9px",borderRadius:99,fontSize:10,fontWeight:700,background:"rgba(217,119,6,.12)",color:"#d97706" }}>⚠ Duplicate Bit</span>
                          : <span style={{ padding:"2px 9px",borderRadius:99,fontSize:10,fontWeight:700,background:"rgba(22,163,74,.1)",color:"#16a34a" }}>✓ Unique</span>
                        }
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
            <div style={{ padding:"8px 14px",fontSize:11,color:"#94a3b8",borderTop:"1px solid #f1f5f9" }}>
              {sortedBits.length} unique bits · {zoneModels.length} total model entries
              {sortedBits.some(([,e])=>e.length>1) && <span style={{ color:"#d97706",marginLeft:10,fontWeight:600 }}>⚠ Duplicate bits detected — each bit must be unique across lines in this zone</span>}
            </div>
          </div>
        )}
        <ModalActions>
          <Btn onClick={()=>setSubPage(3)}>← Back</Btn>
          <Btn onClick={()=>handleSubPage(5)}>Next: Machines →</Btn>
        </ModalActions>
      </div>
    );
  };

  const renderMachines = () => (
    <div>
      <p style={{ fontSize:13,color:"#64748b",marginBottom:16 }}>
        Lines / machines in this zone with their PLC card configuration.
      </p>
      {subLoading ? <Spinner /> : machines.length===0 ? (
        <EmptyState text="No machines found" sub="Assign lines to this zone and configure their PLC settings"/>
      ) : (
        <div style={{ overflowX:"auto" }}>
          <table style={{ width:"100%",borderCollapse:"collapse",fontSize:12 }}>
            <thead>
              <tr>{["Line Code","Line Name","PLC IP","Port","Protocol","OK Bit","NG Bit","Ideal CT","Status"].map(h=>(
                <th key={h} style={{ padding:"8px 10px",textAlign:"left",fontSize:9,fontWeight:700,letterSpacing:".08em",textTransform:"uppercase",color:"#64748b",borderBottom:"2px solid #e2e8f0",whiteSpace:"nowrap" }}>{h}</th>
              ))}</tr>
            </thead>
            <tbody>
              {machines.map(m=>(
                <tr key={m.id} style={{ borderBottom:"1px solid #f1f5f9" }}>
                  <td style={{ padding:"10px 10px",fontFamily:"monospace",fontWeight:700,color:"#1e40af" }}>{m.line_code}</td>
                  <td style={{ padding:"10px 10px",fontWeight:500,color:"#0f172a",maxWidth:160,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap" }}>{m.line_name}</td>
                  <td style={{ padding:"10px 10px",fontFamily:"monospace",color:"#334155" }}>{m.plc_ip||"—"}</td>
                  <td style={{ padding:"10px 10px",fontFamily:"monospace",color:"#64748b" }}>{m.plc_port||"—"}</td>
                  <td style={{ padding:"10px 10px" }}>{m.protocol ? <span style={{ padding:"2px 8px",borderRadius:99,fontSize:10,fontWeight:700,background:"rgba(30,64,175,.1)",color:"#1e40af" }}>{m.protocol}</span> : "—"}</td>
                  <td style={{ padding:"10px 10px",fontFamily:"monospace",color:"#7c3aed",fontWeight:700 }}>{m.ok_bit_address||"—"}</td>
                  <td style={{ padding:"10px 10px",fontFamily:"monospace",color:"#dc2626",fontWeight:700 }}>{m.ng_bit_address||"—"}</td>
                  <td style={{ padding:"10px 10px",fontFamily:"monospace" }}>{m.ideal_cycle_time ? `${m.ideal_cycle_time}s` : "—"}</td>
                  <td style={{ padding:"10px 10px" }}><Pill label={m.collector_status||"stopped"} color={m.collector_status==="running"?"green":"gray"}/></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ padding:"8px 14px",fontSize:11,color:"#94a3b8",borderTop:"1px solid #f1f5f9" }}>
            {machines.length} machine{machines.length!==1?"s":""} configured in this zone
          </div>
        </div>
      )}
      <ModalActions>
        <Btn onClick={()=>setSubPage(4)}>← Back</Btn>
        <Btn variant="primary" onClick={()=>setWizOpen(false)}>Done ✓</Btn>
      </ModalActions>
    </div>
  );

  // ── Main render ───────────────────────────────────────────

  const subPageContent = [renderInfo, renderLines, renderShiftsBreaks, renderHourlySlots, renderModels, renderMachines];
  const canAccessAll = !!wizZone;

  return (
    <div>
      <div style={{ display:"flex",justifyContent:"flex-end",marginBottom:20 }}>
        <Btn variant="primary" onClick={openAdd}>+ Add Zone</Btn>
      </div>

      <Card>
        {loading ? <Spinner /> : zones.length===0 ? (
          <EmptyState text="No zones yet" sub="Create zones to group your production lines"/>
        ) : (
          <table style={{ width:"100%",borderCollapse:"collapse",fontSize:13 }}>
            <thead>
              <tr>{["","Code","Zone Name","Plant","Lines","Actions"].map((h,i)=>(
                <th key={h||`c${i}`} style={{ padding:"10px 14px",textAlign:"left",fontSize:10,fontWeight:700,letterSpacing:".08em",textTransform:"uppercase",color:"#64748b",borderBottom:"2px solid #e2e8f0" }}>{h}</th>
              ))}</tr>
            </thead>
            <tbody>
              {zones.map(z=>(
                <tr key={z.id} style={{ borderBottom:"1px solid #f1f5f9" }}>
                  {/* Colour chip — the whole point of the feature is telling zones
                      apart at a glance, so it leads the row. */}
                  <td style={{ padding:"12px 6px 12px 14px", width:26 }}>
                    <span title={z.color || "No colour set — using the default"}
                          style={{ display:"inline-block", width:14, height:14,
                                    borderRadius:4, verticalAlign:"middle",
                                    background: z.color || "transparent",
                                    border: z.color ? "none" : "1.5px dashed #cbd5e1" }}/>
                  </td>
                  <td style={{ padding:"12px 14px",fontFamily:"monospace",fontWeight:700,color: z.color || "#1e40af" }}>{z.zone_code}</td>
                  <td style={{ padding:"12px 14px",fontWeight:600,color:"#0f172a" }}>{z.zone_name}</td>
                  <td style={{ padding:"12px 14px",color:"#64748b" }}>{z.plant_name}</td>
                  <td style={{ padding:"12px 14px",fontFamily:"monospace" }}>{z.line_count||0}</td>
                  <td style={{ padding:"12px 14px" }}>
                    <div style={{ display:"flex",gap:8 }}>
                      <Btn size="sm" variant="primary" onClick={()=>openConfigure(z)}>Configure</Btn>
                      <Btn size="sm" variant="danger" onClick={()=>deactivate(z)}>Deactivate</Btn>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* Zone wizard modal */}
      <Modal open={wizOpen} onClose={()=>setWizOpen(false)} title={wizZone ? `Configure Zone — ${wizZone.zone_name}` : "Add New Zone"} wide>
        {/* Sub-page tab bar */}
        <div style={{ display:"flex",gap:0,borderBottom:"2px solid #e2e8f0",marginBottom:24,overflowX:"auto" }}>
          {SUB_LABELS.map((label,i)=>{
            const enabled = i===0 || canAccessAll;
            return (
              <button key={i} onClick={()=>{ if(enabled) handleSubPage(i); }}
                style={{ padding:"8px 14px",fontFamily:"'Barlow',sans-serif",fontSize:12,fontWeight:600,cursor:enabled?"pointer":"not-allowed",border:"none",background:"none",color:subPage===i?"#1e40af":enabled?"#64748b":"#cbd5e1",borderBottom:`2px solid ${subPage===i?"#1e40af":"transparent"}`,marginBottom:-2,whiteSpace:"nowrap",transition:"all .12s",opacity:enabled?1:0.45 }}>
                {label}
              </button>
            );
          })}
        </div>
        {subPageContent[subPage]?.()}
      </Modal>
    </div>
  );
}

// ─── PRODUCTION LINES PAGE ────────────────────────────────────
const BLANK_MACHINE = { machine_name:"", plc_ip:"", plc_port:5002, protocol:"MC4E", ok_bit_address:"L108", ng_bit_address:"L109", status_address:"D6005", model_address:"D6048", ideal_cycle_time:15.0, max_allowed_cycle:16.0, ok_ng_pulse_min_gap:0.5, sensor_ok_address:"", process_seq_address:"", override_address:"" };

// ── Recliner-zone ONLY ─────────────────────────────────────────
// A recliner machine runs N production PROCESSES on one PLC; each process has
// its own OK/NG register + a dedicated camera.  Machine total = sum of all
// processes (OK, NG).  Rendered inside the zone-wizard "Machines" step when the
// line's zone is "Recliner".  Reads/writes /api/recliner/... (no SS impact).
function ReclinerConfig({ line, token, toast }) {
  const blank = (n) => ({ process_no:n, process_name:"", count_mode:"register",
                          ok_register:"", ng_register:"", ideal_ct:"", camera_id:"" });
  const [procs, setProcs]     = useState([blank(1)]);
  const [ext, setExt]         = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving]   = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const d = await api.get(`/api/recliner/line/${line.id}`, token);
        if (!alive) return;
        const ps = (d.processes || []).map(p => ({
          process_no:p.process_no, process_name:p.process_name || "",
          count_mode:p.count_mode || "register",
          ok_register:p.ok_register || "", ng_register:p.ng_register || "",
          ideal_ct:p.ideal_ct == null ? "" : p.ideal_ct,
          camera_id:(p.cameras && p.cameras[0] && p.cameras[0].camera_id) || "",
        }));
        setProcs(ps.length ? ps : [blank(1)]);
        setExt((d.external_cameras && d.external_cameras[0] && d.external_cameras[0].camera_id) || "");
      } catch { if (alive) setProcs([blank(1)]); }
      if (alive) setLoading(false);
    })();
    return () => { alive = false; };
  }, [line.id, token]);

  const setP    = (i,k,v) => setProcs(ps => ps.map((p,idx) => idx===i ? {...p,[k]:v} : p));
  const addProc = ()      => setProcs(ps => [...ps, blank((ps[ps.length-1] ? ps[ps.length-1].process_no : 0) + 1)]);
  const rmProc  = (i)     => setProcs(ps => ps.length > 1 ? ps.filter((_,idx) => idx!==i) : ps);

  const save = async () => {
    setSaving(true);
    try {
      await api.put(`/api/recliner/line/${line.id}/processes`,
        procs.map(p => ({...p, ideal_ct: p.ideal_ct==="" ? null : parseFloat(p.ideal_ct)})), token);
      const d = await api.get(`/api/recliner/line/${line.id}`, token);
      const idByNo = {}; (d.processes || []).forEach(p => { idByNo[p.process_no] = p.id; });
      const cams = [];
      procs.forEach(p => {
        const cid = (p.camera_id || "").trim();
        if (cid && idByNo[p.process_no]) cams.push({ process_id:idByNo[p.process_no], camera_id:cid, role:"dedicated" });
      });
      const ec = (ext || "").trim();
      if (ec) cams.push({ camera_id:ec, role:"external" });
      await api.put(`/api/recliner/line/${line.id}/cameras`, cams, token);
      toast && toast("Recliner processes + cameras saved ✓");
    } catch (e) { toast && toast("Save failed: " + (e.message || e), "err"); }
    setSaving(false);
  };

  if (loading) return <Spinner />;
  return (
    <div style={{ marginBottom:18, background:"#fffbeb", border:"1.5px solid #fcd34d", borderRadius:12, padding:18 }}>
      <div style={{ fontSize:13, fontWeight:800, color:"#0f172a" }}>Recliner — Processes &amp; Cameras</div>
      <div style={{ fontSize:11, color:"#92400e", margin:"4px 0 14px" }}>
        This machine runs multiple processes. Machine total = sum of all processes (OK, NG). Each process has its own OK/NG register + a dedicated camera; the optional external camera covers every process.
      </div>
      {procs.map((p,i) => (
        <div key={i} style={{ background:"#fff", border:"1px solid #fde68a", borderRadius:10, padding:12, marginBottom:10 }}>
          <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:10 }}>
            <span style={{ fontWeight:700, fontSize:12, color:"#b45309" }}>Process {p.process_no}</span>
            <Btn size="sm" variant="danger" onClick={() => rmProc(i)}>Remove</Btn>
          </div>
          <div style={{ display:"grid", gridTemplateColumns:"1.4fr .9fr 1fr 1fr .8fr", gap:10 }}>
            <FF label="Process Name"><Input value={p.process_name} onChange={e=>setP(i,"process_name",e.target.value)} placeholder="e.g. Welding-1"/></FF>
            <FF label="Mode"><Select value={p.count_mode} onChange={e=>setP(i,"count_mode",e.target.value)}><option value="register">register</option><option value="bit">bit</option></Select></FF>
            <FF label="OK Register"><Input value={p.ok_register} onChange={e=>setP(i,"ok_register",e.target.value)} placeholder="D101 / L108"/></FF>
            <FF label="NG Register"><Input value={p.ng_register} onChange={e=>setP(i,"ng_register",e.target.value)} placeholder="D102 / L109"/></FF>
            <FF label="Ideal CT"><Input type="number" step="0.1" value={p.ideal_ct} onChange={e=>setP(i,"ideal_ct",e.target.value)}/></FF>
          </div>
          <div style={{ marginTop:10 }}>
            <FF label="Dedicated Camera ID" hint="bind from the camera list"><Input value={p.camera_id} onChange={e=>setP(i,"camera_id",e.target.value)} placeholder="cam_..."/></FF>
          </div>
        </div>
      ))}
      <Btn size="sm" onClick={addProc}>+ Add Process</Btn>
      <div style={{ marginTop:14 }}>
        <FF label="External Camera ID (optional — covers all processes)"><Input value={ext} onChange={e=>setExt(e.target.value)} placeholder="cam_external_..."/></FF>
      </div>
      <div style={{ marginTop:14 }}>
        <Btn variant="primary" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save Recliner Config ✓"}</Btn>
      </div>
    </div>
  );
}

export function LinesPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [lines,      setLines]      = useState([]);
  const [plants,     setPlants]     = useState([]);
  const [zones,      setZones]      = useState([]);
  const [loading,    setLoading]    = useState(true);
  const [modal,      setModal]      = useState(false);
  const [editing,    setEditing]    = useState(null);   // null=add, obj=edit
  const [subPage,    setSubPage]    = useState(0);
  const [saving,     setSaving]     = useState(false);
  const [subLoading, setSubLoading] = useState(false);

  // Sub-page 0 state
  const [basicForm, setBasicForm] = useState({ plant_id:"", line_code:"", line_name:"", db_table_name:"", zone_id:"" });
  // Sub-page 1 state
  const [zoneShiftsCfg, setZoneShiftsCfg] = useState([]);
  // 2026-08-10 — the Active Shifts tab used to render "Loading zone shifts…"
  // whenever the list was empty, so a loaded-but-empty response and a FAILED
  // fetch both looked like a spinner that never finished.  One dropped call
  // (an API restart is enough) left the tab stuck forever, because unlike
  // every other tab this one never re-fetched when you clicked it.  Track the
  // real state so the tab can say which of the three it actually is.
  const [zoneShiftsState, setZoneShiftsState] = useState("idle"); // idle|loading|ok|error
  // 2026-08-10 — Plant / Line Code / DB Table Name used to be permanently
  // read-only once a line existed, so a typo made at creation could only be
  // fixed in SQL.  They stay locked by default (changing them has real
  // consequences — see the warning in the panel) but an explicit Edit button
  // unlocks them.  Resets on every modal open so it is never left unlocked.
  const [infoUnlocked, setInfoUnlocked] = useState(false);
  const [activeShifts,  setActiveShifts]  = useState(["A","B"]);
  // Sub-page 2 state
  const [machines,    setMachines]    = useState([]);
  const [machineForm, setMachineForm] = useState(null);   // null=hidden, obj=form data
  // Sub-page 3 state
  const [dashboardPlcId, setDashboardPlcId] = useState(null);
  // Sub-page 4 state — Models are now picked from the Poka-Yoke Model Master.
  const [pyModelOptions, setPyModelOptions] = useState([]);   // all Model Master entries
  const [selectedPyIds,  setSelectedPyIds]  = useState([]);   // IDs assigned to this line
  const [ctByModel,      setCtByModel]      = useState({});   // model_number -> ideal_ct (model-aware plan CT)
  const [pyPickerOpen,   setPyPickerOpen]   = useState(false);
  // Sub-page 5 state
  const [idealCt,       setIdealCt]       = useState(15.0);
  const [plannedTakt,   setPlannedTakt]   = useState("");   // empty string = "not set"
  const [planningShifts,setPlanningShifts] = useState([]);
  // Sub-page 6 state
  const [otConfigs,     setOtConfigs]     = useState([]);
  // Sub-page 7 state — per-line hourly slots (each line can differ; not the
  // zone-common fan-out). Uses the existing per-line /api/config/slots endpoint.
  const [lineSlots,      setLineSlots]      = useState([]);   // ALL slots (both shifts)
  const [lineSlotShift,  setLineSlotShift]  = useState("A");
  const [lineShiftPlans, setLineShiftPlans] = useState({});   // {A: total_plan, B: total_plan}

  // Per-line camera Video ON/OFF (controls the CMS recorder for this line via
  // MES→CMS proxy).  videoOff = line_names whose recording is OFF.
  const [videoOff,  setVideoOff]  = useState([]);
  const [videoBusy, setVideoBusy] = useState("");   // line_name currently toggling

  // ── Loaders ──────────────────────────────────────────────────
  const load = useCallback(async () => {
    try {
      const [l, p, z] = await Promise.all([api.get("/api/lines/", token), api.get("/api/plants/", token), api.get("/api/zones/", token)]);
      setLines(Array.isArray(l) ? l : []);
      setPlants(Array.isArray(p) ? p : []);
      setZones(Array.isArray(z) ? z : []);
    } catch { toast("Failed to load", "err"); }
    finally { setLoading(false); }
    // Per-line video state from the CMS (best-effort — CMS may be down).
    try {
      const v = await api.get("/api/cms/video-lines", token);
      setVideoOff(Array.isArray(v?.disabled_lines) ? v.disabled_lines : []);
    } catch { /* CMS unreachable — leave video state as-is */ }
  }, [token]);

  const isVideoOn = (line) => !videoOff.includes(line.line_name);
  const toggleLineVideo = async (line, enabled) => {
    const name = line.line_name;
    setVideoBusy(name);
    try {
      const r = await api.post("/api/cms/video-line", { line: name, enabled }, token);
      setVideoOff(Array.isArray(r?.disabled_lines)
        ? r.disabled_lines
        : (enabled ? videoOff.filter(x => x !== name) : [...new Set([...videoOff, name])]));
      toast(`Camera video ${enabled ? "ON" : "OFF"} — ${name}`
        + ((!enabled && r?.stopped_recorders) ? ` (stopped ${r.stopped_recorders} cam)` : ""));
    } catch (e) {
      toast(e.message || "Video toggle failed (CMS reachable?)", "err");
    } finally {
      setVideoBusy("");
    }
  };

  useEffect(() => { load(); }, [load]);

  const loadZoneShifts = async (zoneId) => {
    if (!zoneId) { setZoneShiftsCfg([]); setZoneShiftsState("idle"); return; }
    setZoneShiftsState("loading");
    try {
      const s = await api.get(`/api/zones/${zoneId}/shifts`, token);
      setZoneShiftsCfg(Array.isArray(s) ? s.filter(sh => sh.is_production) : []);
      setZoneShiftsState("ok");
    } catch {
      setZoneShiftsCfg([]);
      setZoneShiftsState("error");
    }
  };

  const loadMachines = useCallback(async (lineId) => {
    if (!lineId) return;
    setSubLoading(true);
    try { const m = await api.get(`/api/lines/${lineId}/machines`, token); setMachines(Array.isArray(m) ? m : []); }
    catch { setMachines([]); }
    finally { setSubLoading(false); }
  }, [token]);

  const loadModels = useCallback(async (lineId) => {
    if (!lineId) return;
    try {
      // Keep dashboard_plc_id loading (sub-page 3 also relies on this call).
      const d = await api.get(`/api/lines/${lineId}`, token);
      setDashboardPlcId(d.dashboard_plc_id || null);
    } catch {}
    // Load the full Model Master list + this line's current selections.
    try {
      const [all, curr] = await Promise.all([
        api.get("/api/poka-yoke/models/", token).catch(()=>[]),
        api.get(`/api/config/py-models/${lineId}`, token).catch(()=>[]),
      ]);
      setPyModelOptions(Array.isArray(all) ? all : []);
      setSelectedPyIds(Array.isArray(curr) ? curr.map(m => m.id) : []);
    } catch {}
    // Per-model ideal CT (model-aware plan) — keyed by model_number.
    try {
      const ct = await api.get(`/api/lines/${lineId}/model-ct`, token).catch(()=>[]);
      const m = {};
      (Array.isArray(ct) ? ct : []).forEach(x => { m[x.model_number] = x.ideal_ct != null ? String(x.ideal_ct) : ""; });
      setCtByModel(m);
    } catch { setCtByModel({}); }
  }, [token]);

  const loadPlanning = useCallback(async (lineId) => {
    if (!lineId) return;
    setSubLoading(true);
    try {
      const p = await api.get(`/api/lines/${lineId}/planning`, token);
      setIdealCt(parseFloat(p.ideal_ct) || 15.0);
      setPlannedTakt(p.planned_takt != null ? String(p.planned_takt) : "");
      setPlanningShifts(Array.isArray(p.shifts) ? p.shifts : []);
    } catch {}
    finally { setSubLoading(false); }
  }, [token]);

  const loadOtConfig = useCallback(async (lineId) => {
    if (!lineId) return;
    setSubLoading(true);
    try {
      const cfg = await api.get(`/api/lines/${lineId}/ot-config`, token);
      setOtConfigs(Array.isArray(cfg) ? cfg.map(c => ({
        shift_name:    c.shift_name,
        ot_start_time: c.ot_start_time || "",
        ot_end_time:   c.ot_end_time   || "",
        ot_plan:       (c.ot_plan ?? "") === null ? "" : (c.ot_plan ?? ""),
      })) : []);
    } catch { setOtConfigs([]); }
    finally { setSubLoading(false); }
  }, [token]);

  // ── Per-line hourly slots (sub-page 7) ───────────────────────
  const loadHourlySlots = useCallback(async (lineId) => {
    if (!lineId) return;
    setSubLoading(true);
    try {
      const [sl, sh] = await Promise.all([
        api.get(`/api/config/slots/${lineId}`,  token).catch(()=>[]),
        api.get(`/api/config/shifts/${lineId}`, token).catch(()=>[]),
      ]);
      setLineSlots(Array.isArray(sl) ? sl.map(x => ({
        shift_name:       x.shift_name,
        slot_label:       x.slot_label || "",
        start_time:       (x.start_time || "").slice(0,5),
        end_time:         (x.end_time   || "").slice(0,5),
        crosses_midnight: !!x.crosses_midnight,
        working_minutes:  x.working_minutes || 0,
        plan_pieces:      x.plan_pieces || 0,
        db_column_prefix: x.db_column_prefix || "",
        slot_order:       x.slot_order || 0,
      })) : []);
      const plans = {};
      (Array.isArray(sh) ? sh : []).forEach(c => { plans[c.shift_name] = c.total_plan || 0; });
      setLineShiftPlans(plans);
    } catch {}
    finally { setSubLoading(false); }
  }, [token]);

  const setLineSlotFld = (realIdx, k, v) =>
    setLineSlots(prev => { const a = [...prev]; a[realIdx] = { ...a[realIdx], [k]: v }; return a; });
  const addLineSlot = () =>
    setLineSlots(prev => [...prev, {
      shift_name: lineSlotShift, slot_label: "", start_time: "", end_time: "",
      crosses_midnight: false, working_minutes: 60, plan_pieces: 0, db_column_prefix: "",
      slot_order: prev.filter(s => s.shift_name === lineSlotShift).length + 1,
    }]);
  const rmLineSlot = (realIdx) => setLineSlots(prev => prev.filter((_, i) => i !== realIdx));

  // Auto-distribute this shift's plan across its slots, weighted by working-min
  // (remainder handed to the slots with the largest fractional parts).
  const distributeLinePlan = () => {
    const total = Number(lineShiftPlans[lineSlotShift] || 0);
    const rows  = lineSlots.map((s, i) => ({ s, i })).filter(x => x.s.shift_name === lineSlotShift);
    const mins  = rows.map(x => Number(x.s.working_minutes) || 0);
    const sum   = mins.reduce((a, b) => a + b, 0);
    if (!total) { toast("Set this shift's plan in the Planning tab first", "err"); return; }
    if (!rows.length || !sum) { toast("Add slots with Work-Min first", "err"); return; }
    const raw  = mins.map(m => total * m / sum);
    const base = raw.map(x => Math.floor(x));
    let rem = total - base.reduce((a, b) => a + b, 0);
    const order = raw.map((x, i) => [i, x - base[i]]).sort((a, b) => b[1] - a[1]);
    for (let k = 0; k < rem; k++) base[order[k][0]]++;
    setLineSlots(prev => { const a = [...prev]; rows.forEach((x, j) => { a[x.i] = { ...a[x.i], plan_pieces: base[j] }; }); return a; });
    toast(`Distributed ${total} across ${rows.length} slots (Shift ${lineSlotShift})`);
  };

  const saveLineSlots = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      await api.put(`/api/config/slots/${editing.id}`, lineSlots.map(s => ({
        shift_name:       s.shift_name,
        slot_label:       s.slot_label,
        start_time:       s.start_time,
        end_time:         s.end_time,
        crosses_midnight: !!s.crosses_midnight,
        working_minutes:  Number(s.working_minutes) || 0,
        plan_pieces:      Number(s.plan_pieces) || 0,
        db_column_prefix: s.db_column_prefix || "",
        slot_order:       Number(s.slot_order) || 0,
      })), token);
      toast("Hourly slots saved ✓ — restart the collector to apply");
    } catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  // ── Open handlers ─────────────────────────────────────────────
  const openAdd = () => {
    setEditing(null); setSubPage(0); setMachineForm(null);
    setBasicForm({ plant_id: plants[0]?.id || "", line_code:"", line_name:"", db_table_name:"", zone_id:"" });
    setInfoUnlocked(false);
    setZoneShiftsCfg([]); setActiveShifts(["A","B"]);
    setMachines([]); setDashboardPlcId(null);
    setPyModelOptions([]); setSelectedPyIds([]);
    setIdealCt(15.0); setPlanningShifts([]);
    setOtConfigs([]);   // 2026-06-19 — clear stale OT config from a previously-edited line
    setLineSlots([]); setLineSlotShift("A"); setLineShiftPlans({});
    setModal(true);
  };

  const openEdit = async (l) => {
    setEditing(l); setSubPage(0); setMachineForm(null); setInfoUnlocked(false);
    setBasicForm({ plant_id: l.plant_id, line_code: l.line_code, line_name: l.line_name, db_table_name: l.db_table_name, zone_id: l.zone_id || "" });
    setActiveShifts(l.active_shifts ? l.active_shifts.split(",").map(s=>s.trim()) : ["A","B"]);
    if (l.zone_id) loadZoneShifts(l.zone_id);
    loadMachines(l.id); loadModels(l.id); loadPlanning(l.id); loadOtConfig(l.id);
    setModal(true);
  };

  const handleSubPage = (i) => {
    if (!editing && i > 0) return;
    setSubPage(i); setMachineForm(null);
    if (editing) {
      // Tab 1 was the only one that never refreshed on click, which is why a
      // single failed fetch could never recover without reopening the modal.
      if (i === 1 && basicForm.zone_id) loadZoneShifts(basicForm.zone_id);
      if (i === 2) loadMachines(editing.id);
      if (i === 3) loadMachines(editing.id);
      if (i === 4) loadModels(editing.id);
      if (i === 5) loadPlanning(editing.id);
      if (i === 6) loadOtConfig(editing.id);
      if (i === 7) loadHourlySlots(editing.id);
    }
  };

  // ── Save functions ────────────────────────────────────────────
  const saveLine = async () => {
    const { plant_id, line_code, line_name, db_table_name, zone_id } = basicForm;
    if (!plant_id || !line_code || !line_name || !db_table_name) { toast("Fill all required fields", "err"); return; }
    setSaving(true);
    try {
      if (!editing) {
        const res = await api.post("/api/lines/", { plant_id:parseInt(plant_id), line_code, line_name, db_table_name, active_shifts: activeShifts.join(",") }, token);
        if (zone_id) await api.put(`/api/lines/${res.id}`, { zone_id:parseInt(zone_id) }, token);
        setEditing({ ...res, zone_id: zone_id ? parseInt(zone_id) : null, plant_id:parseInt(plant_id), line_code, line_name, db_table_name });
        if (zone_id) loadZoneShifts(zone_id);
        load(); toast("Line created ✓ Configure remaining tabs →");
      } else {
        // Only send the locked-by-default fields when they were explicitly
        // unlocked, so an ordinary "Save Info" can never rewrite them.
        const payload = { line_name, zone_id: zone_id ? parseInt(zone_id) : null,
                          active_shifts: activeShifts.join(",") };
        if (infoUnlocked) {
          payload.line_code     = line_code;
          payload.db_table_name = db_table_name;
          if (plant_id) payload.plant_id = parseInt(plant_id);
        }
        await api.put(`/api/lines/${editing.id}`, payload, token);
        setEditing(prev => ({ ...prev, line_name, zone_id: zone_id ? parseInt(zone_id) : null,
                              ...(infoUnlocked ? { line_code, db_table_name,
                                                   plant_id: plant_id ? parseInt(plant_id) : prev.plant_id } : {}) }));
        setInfoUnlocked(false);
        if (zone_id) loadZoneShifts(zone_id);
        load(); toast("Line info saved ✓");
      }
    } catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  const saveShifts = async () => {
    if (!editing || !activeShifts.length) { toast("Select at least one shift", "err"); return; }
    setSaving(true);
    try { await api.put(`/api/lines/${editing.id}`, { active_shifts: activeShifts.join(",") }, token); load(); toast("Active shifts saved ✓"); }
    catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  const saveMachine = async () => {
    if (!editing || !machineForm) return;
    const { id, ...body } = machineForm;
    if (!body.machine_name || !body.plc_ip) { toast("Machine name and IP required", "err"); return; }
    setSaving(true);
    try {
      if (id) { await api.put(`/api/lines/${editing.id}/machines/${id}`, body, token); }
      else     { await api.post(`/api/lines/${editing.id}/machines`, body, token); }
      setMachineForm(null); loadMachines(editing.id); toast("Machine saved ✓");
    } catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  const deleteMachine = async (plcId) => {
    if (!confirm("Delete this machine?")) return;
    try { await api.delete(`/api/lines/${editing.id}/machines/${plcId}`, token); loadMachines(editing.id); toast("Machine deleted"); }
    catch (e) { toast(e.message, "err"); }
  };

  const saveDashboardPlc = async () => {
    if (!editing || !dashboardPlcId) return;
    setSaving(true);
    try { await api.put(`/api/lines/${editing.id}/dashboard-plc`, { plc_id: dashboardPlcId }, token); toast("Dashboard PLC saved ✓"); }
    catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  const saveModels = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      await api.put(`/api/config/py-models/${editing.id}`, selectedPyIds, token);
      // Per-model ideal CT (model-aware plan) — save the assigned models' CTs.
      const byId = Object.fromEntries((pyModelOptions||[]).map(m => [m.id, m]));
      const ctRows = selectedPyIds
        .map(id => byId[id]).filter(Boolean)
        .filter(m => m.bitNumber != null)
        .map(m => ({ model_number: Number(m.bitNumber),
                     ideal_ct: ctByModel[m.bitNumber] ? Number(ctByModel[m.bitNumber]) : null,
                     model_name: String(m.modelName || "").replace(/^TYPE-SERIES:\s*/i,"") }));
      if (ctRows.length) await api.put(`/api/lines/${editing.id}/model-ct`, ctRows, token).catch(()=>{});
      toast(`Saved ${selectedPyIds.length} model${selectedPyIds.length===1?"":"s"} ✓`);
    } catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  const savePlanning = async () => {
    if (!editing || !(idealCt > 0)) { toast("Ideal cycle time must be > 0", "err"); return; }
    setSaving(true);
    try {
      const body = { ideal_ct: parseFloat(idealCt), recalculate: true };
      const ptVal = plannedTakt === "" ? null : parseFloat(plannedTakt);
      if (ptVal !== null && !(ptVal > 0)) { toast("Planned takt must be > 0 if provided", "err"); setSaving(false); return; }
      body.planned_takt = ptVal;
      await api.put(`/api/lines/${editing.id}/planning`, body, token);
      loadPlanning(editing.id);
      toast("Planning saved & applied ✓");
    }
    catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  const saveOtConfig = async () => {
    if (!editing) return;
    // 2026-06-19 — client-side validation: OT end must be after OT start, and
    // a manual OT target (if entered) must be a non-negative whole number.
    // Both times must be set together (a half-configured window is rejected).
    const toMin = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
    for (const c of otConfigs) {
      const s = c.ot_start_time, e = c.ot_end_time;
      if ((s && !e) || (!s && e)) {
        toast(`Shift ${c.shift_name}: set BOTH OT start and end (or leave both blank)`, "err");
        return;
      }
      if (s && e) {
        let dur = toMin(e) - toMin(s);
        if (dur <= 0) dur += 1440;          // midnight-crossing OT (e.g. 23:30→00:30)
        if (dur > 360) {                    // > 6h is almost certainly start/end reversed
          toast(`Shift ${c.shift_name}: OT window ${s}→${e} = ${(dur/60).toFixed(1)}h — too long; check start/end`, "err");
          return;
        }
      }
      if (c.ot_plan !== "" && c.ot_plan != null && !(Number(c.ot_plan) >= 0)) {
        toast(`Shift ${c.shift_name}: OT target must be 0 or more`, "err");
        return;
      }
    }
    setSaving(true);
    try {
      await api.put(`/api/lines/${editing.id}/ot-config`,
        otConfigs.map(c => ({
          shift_name:    c.shift_name,
          ot_start_time: c.ot_start_time || null,
          ot_end_time:   c.ot_end_time   || null,
          ot_plan:       (c.ot_plan === "" || c.ot_plan == null) ? null : Math.max(0, Math.round(Number(c.ot_plan))),
        })),
        token
      );
      toast("OT config saved ✓");
    } catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  const setOtFld = (i, k, v) => setOtConfigs(prev => { const a = [...prev]; a[i] = {...a[i], [k]: v}; return a; });

  // 2026-08-12 — collector controls live in the lines list now.  The routes
  // (/provision, /stop, /restart) already existed but nothing in the UI called
  // provision or restart, so a newly-configured line silently never started —
  // saving the config does NOT start a collector, provisioning does.
  const [collBusy, setCollBusy] = useState(null);   // line id currently working

  const restartLine = async (l) => {
    setCollBusy(l.id);
    try {
      const r = await api.post(`/api/lines/${l.id}/restart`, {}, token);
      toast(`Restarted ✓ PID ${r.pid ?? "—"}`);
      load();
    } catch (e) { toast(e.message, "err"); }
    finally { setCollBusy(null); }
  };

  const provisionLine = async (l) => {
    setCollBusy(l.id);
    try { const r = await api.post(`/api/lines/${l.id}/provision`, {}, token); toast(`Started ✓ PID ${r.pid}`); load(); }
    catch (e) { toast(e.message, "err"); }
    finally { setCollBusy(null); }
  };

  // ── Helpers ───────────────────────────────────────────────────
  const toggleShift    = (n) => setActiveShifts(s => s.includes(n) ? (s.length > 1 ? s.filter(x=>x!==n) : s) : [...s, n]);
  const togglePyModel  = (id) => setSelectedPyIds(s => s.includes(id) ? s.filter(x=>x!==id) : [...s, id]);
  const setMF          = (k,v) => setMachineForm(f => ({...f,[k]:v}));
  const stopLine       = async (l) => { try { await api.post(`/api/lines/${l.id}/stop`,{},token); toast("Collector stopped"); load(); } catch(e){toast(e.message,"err");} };

  const SUB_LABELS = ["① Zone & Info","② Active Shifts","③ Machines","④ Dashboard PLC","⑤ Models","⑥ Planning","⑦ OT Config","⑧ Hourly Slots"];
  const canAll     = !!editing;
  const miniInp    = { ...inputStyle, padding:"8px 10px", fontSize:12 };
  const rowStyle   = { display:"flex", alignItems:"center", gap:8, marginBottom:8 };

  return (
    <div>
      <div style={{ display:"flex", justifyContent:"flex-end", marginBottom:20 }}>
        <Btn variant="primary" onClick={openAdd}>+ Add Line</Btn>
      </div>
      <Card>
        {loading ? <Spinner /> : lines.length===0 ? <EmptyState text="No lines yet" sub="Add a production line to begin" /> : (
          lines.map(l => {
            const running = l.collector_status === "running";
            return (
              <div key={l.id} style={{ display:"flex", alignItems:"center", gap:14, padding:"14px 0", borderBottom:"1px solid #f1f5f9" }}>
                <div style={{ width:38, height:38, borderRadius:9, flexShrink:0, background:running?"rgba(22,163,74,.1)":"#f8fafc", border:`1px solid ${running?"rgba(22,163,74,.3)":"#e2e8f0"}`, display:"flex", alignItems:"center", justifyContent:"center", fontSize:16, color:running?"#16a34a":"#94a3b8", fontWeight:700 }}>
                  {running ? "▶" : "■"}
                </div>
                <div style={{ flex:1, minWidth:0 }}>
                  <div style={{ fontWeight:700, fontSize:14, color:"#0f172a" }}>{l.line_name}</div>
                  <div style={{ fontSize:11, color:"#94a3b8", marginTop:2 }}>
                    {l.plant_name} · {l.line_code} · {l.db_table_name}
                    {l.zone_name && <span style={{ marginLeft:6, color:"#1e40af", fontWeight:600 }}>· {l.zone_name}</span>}
                    {l.active_shifts && l.active_shifts !== "A,B" && <span style={{ marginLeft:6, color:"#d97706", fontWeight:600 }}>· Shifts: {l.active_shifts}</span>}
                  </div>
                </div>
                <Pill label={l.collector_status} color={running?"green":"gray"} />
                <div style={{ display:"flex", gap:8, alignItems:"center" }}>
                  <button
                    onClick={() => toggleLineVideo(l, !isVideoOn(l))}
                    disabled={readOnly || videoBusy === l.line_name}
                    title={isVideoOn(l) ? "Camera recording ON — click to turn OFF (saves PC load)" : "Camera recording OFF — click to turn ON"}
                    style={{
                      display:"inline-flex", alignItems:"center", gap:6, padding:"6px 11px", borderRadius:7,
                      fontSize:12, fontWeight:700, whiteSpace:"nowrap",
                      cursor: (readOnly || videoBusy === l.line_name) ? "default" : "pointer",
                      border:`1.5px solid ${isVideoOn(l) ? "rgba(22,163,74,.4)" : "rgba(148,163,184,.55)"}`,
                      background: isVideoOn(l) ? "rgba(22,163,74,.10)" : "#f1f5f9",
                      color: isVideoOn(l) ? "#16a34a" : "#64748b",
                      opacity: videoBusy === l.line_name ? .6 : 1,
                    }}
                  >
                    {videoBusy === l.line_name ? "…" : isVideoOn(l) ? "🎥 Video ON" : "🚫 Video OFF"}
                  </button>
                  <Btn size="sm" onClick={() => openEdit(l)}>Configure</Btn>
                  {/* Collector controls.  "Start" runs provision, which also
                      creates the line's table + collector script the first time
                      — that is the step a freshly-configured line is missing. */}
                  {collBusy === l.id ? (
                    <Btn size="sm" disabled>working…</Btn>
                  ) : running ? (
                    <>
                      <Btn size="sm" onClick={() => restartLine(l)}
                           title="Stop, wait for the PLC lock to clear, then start again">
                        Restart
                      </Btn>
                      <Btn size="sm" variant="danger" onClick={() => stopLine(l)}>Stop</Btn>
                    </>
                  ) : (
                    <Btn size="sm" variant="primary" onClick={() => provisionLine(l)}
                         title="Create the table + collector for this line and start it">
                      Start
                    </Btn>
                  )}
                </div>
              </div>
            );
          })
        )}
      </Card>

      <Modal open={modal} onClose={() => setModal(false)} title={editing ? `Configure — ${editing.line_name}` : "Add Production Line"} wide>
        {editing && (
          <div style={{ display:"flex", alignItems:"center", gap:12, padding:"12px 14px", marginBottom:18, borderRadius:9,
                        border:`1.5px solid ${isVideoOn(editing) ? "rgba(22,163,74,.35)" : "rgba(217,119,6,.45)"}`,
                        background: isVideoOn(editing) ? "rgba(22,163,74,.06)" : "rgba(217,119,6,.06)" }}>
            <span style={{ fontSize:20 }}>{isVideoOn(editing) ? "🎥" : "🚫"}</span>
            <div style={{ flex:1, minWidth:0 }}>
              <div style={{ fontSize:13, fontWeight:800, color:"#0f172a" }}>
                Camera Recording — <span style={{ color: isVideoOn(editing) ? "#16a34a" : "#d97706" }}>{isVideoOn(editing) ? "ON" : "OFF"}</span>
              </div>
              <div style={{ fontSize:11, color:"#64748b", marginTop:2 }}>
                Turn OFF to stop this line's cameras (recording + live view) and reduce PC load. Line counting is unaffected.
              </div>
            </div>
            <button
              onClick={() => toggleLineVideo(editing, !isVideoOn(editing))}
              disabled={readOnly || videoBusy === editing.line_name}
              title={isVideoOn(editing) ? "Turn video OFF" : "Turn video ON"}
              style={{ position:"relative", width:60, height:30, borderRadius:999, border:"none", flexShrink:0,
                       cursor:(readOnly || videoBusy === editing.line_name) ? "default" : "pointer",
                       background: isVideoOn(editing) ? "#16a34a" : "#cbd5e1",
                       opacity: videoBusy === editing.line_name ? .6 : 1, transition:"background .15s" }}
            >
              <span style={{ position:"absolute", top:3, left: isVideoOn(editing) ? 33 : 3, width:24, height:24, borderRadius:"50%", background:"#fff", boxShadow:"0 1px 3px rgba(0,0,0,.3)", transition:"left .15s" }} />
            </button>
          </div>
        )}
        {/* Sub-page tab bar */}
        <div style={{ display:"flex", gap:0, borderBottom:"2px solid #e2e8f0", marginBottom:24, overflowX:"auto" }}>
          {SUB_LABELS.map((label, i) => {
            const enabled = i === 0 || canAll;
            return (
              <button key={i} onClick={() => { if (enabled) handleSubPage(i); }}
                style={{ padding:"8px 14px", fontFamily:"'Barlow',sans-serif", fontSize:12, fontWeight:600, cursor:enabled?"pointer":"not-allowed", border:"none", background:"none", color:subPage===i?"#1e40af":enabled?"#64748b":"#cbd5e1", borderBottom:`2px solid ${subPage===i?"#1e40af":"transparent"}`, marginBottom:-2, whiteSpace:"nowrap", transition:"all .12s", opacity:enabled?1:0.4 }}>
                {label}
              </button>
            );
          })}
        </div>

        {/* ── ① Zone & Info ── */}
        {subPage === 0 && (
          <div>
            {editing && (
              <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between",
                            gap:12, marginBottom:14, padding:"10px 14px", borderRadius:9,
                            background: infoUnlocked ? "rgba(217,119,6,.06)" : "rgba(100,116,139,.06)",
                            border: `1px solid ${infoUnlocked ? "rgba(217,119,6,.3)" : "rgba(100,116,139,.18)"}` }}>
                <div style={{ fontSize:12, color: infoUnlocked ? "#b45309" : "#64748b", lineHeight:1.5 }}>
                  {infoUnlocked ? (
                    <>
                      <strong>Plant, Line Code and DB Table Name are unlocked.</strong><br/>
                      Renaming <strong>DB Table Name</strong> does NOT rename the existing
                      table — the line will point at a table that may not exist, and the
                      dashboard will error until it is provisioned again. Change it only
                      if you know the new table is correct.
                    </>
                  ) : (
                    <>Plant, Line Code and DB Table Name are locked. Click Edit to change them.</>
                  )}
                </div>
                <Btn size="sm" variant={infoUnlocked ? undefined : "primary"}
                     onClick={() => setInfoUnlocked(u => !u)}>
                  {infoUnlocked ? "Lock" : "✏️ Edit"}
                </Btn>
              </div>
            )}
            <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:16, marginBottom:16 }}>
              <FF label="Plant *">
                <Select value={basicForm.plant_id} onChange={e=>setBasicForm(f=>({...f,plant_id:e.target.value}))} disabled={!!editing && !infoUnlocked}>
                  <option value="">Select plant…</option>
                  {plants.map(p => <option key={p.id} value={p.id}>{p.plant_name}</option>)}
                </Select>
              </FF>
              <FF label="Line Code *">
                <Input value={basicForm.line_code} onChange={e=>setBasicForm(f=>({...f,line_code:e.target.value}))} placeholder="YNC-L2" disabled={!!editing && !infoUnlocked}/>
              </FF>
              <FF label="Line Name *">
                <Input value={basicForm.line_name} onChange={e=>setBasicForm(f=>({...f,line_name:e.target.value}))} placeholder="e.g. Production Line 2"/>
              </FF>
              <FF label="DB Table Name *" hint="Created automatically on provision">
                <Input value={basicForm.db_table_name} onChange={e=>setBasicForm(f=>({...f,db_table_name:e.target.value}))} placeholder="ync_l2_dashboard" disabled={!!editing && !infoUnlocked}/>
              </FF>
            </div>
            <FF label="Zone Assignment" hint="This line inherits shifts, breaks and hourly slots from the assigned zone">
              <Select value={basicForm.zone_id} onChange={e=>{ setBasicForm(f=>({...f,zone_id:e.target.value})); loadZoneShifts(e.target.value); }}>
                <option value="">— No Zone —</option>
                {zones.map(z => <option key={z.id} value={z.id}>{z.zone_name} ({z.zone_code})</option>)}
              </Select>
            </FF>
            {basicForm.zone_id && (
              <div style={{ marginTop:14, background:"rgba(30,64,175,.04)", border:"1px solid rgba(30,64,175,.15)", borderRadius:9, padding:"10px 14px", fontSize:12, color:"#1e40af" }}>
                ℹ️ Shifts, break schedules and hourly slots are managed in the Zone configuration.
                Go to <strong>② Active Shifts</strong> to select which shifts this line operates in.
              </div>
            )}
            <ModalActions>
              <Btn onClick={() => setModal(false)}>Cancel</Btn>
              <Btn variant="primary" onClick={saveLine} disabled={saving}>{saving?"Saving…": editing ? "Save Info →" : "Create Line →"}</Btn>
            </ModalActions>
          </div>
        )}

        {/* ── ② Active Shifts ── */}
        {subPage === 1 && (
          <div>
            <p style={{ fontSize:12, color:"#64748b", marginBottom:20 }}>
              Select which shifts this line operates in. During unselected shifts the line will appear as <strong>Offline</strong> on the dashboard.
            </p>
            {zoneShiftsCfg.length === 0 ? (
              <div style={{ textAlign:"center", padding:32, color:"#94a3b8", fontSize:13 }}>
                {!basicForm.zone_id
                  ? "Assign a zone first (① Zone & Info tab) to see available shifts"
                  : zoneShiftsState === "loading"
                  ? "Loading zone shifts…"
                  : zoneShiftsState === "error"
                  ? (<>
                      <div style={{ color:"#dc2626", fontWeight:700, marginBottom:6 }}>
                        Could not load this zone's shifts
                      </div>
                      <div style={{ marginBottom:12 }}>
                        The request to the server failed. Nothing is wrong with this
                        line's configuration.
                      </div>
                      <Btn size="sm" onClick={() => loadZoneShifts(basicForm.zone_id)}>
                        Retry
                      </Btn>
                    </>)
                  : (<>
                      <div style={{ fontWeight:700, marginBottom:6 }}>
                        This zone has no production shifts configured
                      </div>
                      <div>
                        Add shifts in the Zone configuration (only shifts marked as
                        production appear here — GAP shifts are excluded).
                      </div>
                    </>)}
              </div>
            ) : (
              <div style={{ display:"flex", flexDirection:"column", gap:12, marginBottom:24 }}>
                {zoneShiftsCfg.map(s => {
                  const checked = activeShifts.includes(s.shift_name);
                  return (
                    <div key={s.shift_name} onClick={() => toggleShift(s.shift_name)}
                      style={{ display:"flex", alignItems:"center", gap:16, padding:"14px 18px", borderRadius:10, border:`1.5px solid ${checked?"#1e40af":"#e2e8f0"}`, background:checked?"rgba(30,64,175,.04)":"#fff", cursor:"pointer", transition:"all .12s" }}>
                      <input type="checkbox" checked={checked} onChange={() => toggleShift(s.shift_name)}
                        style={{ width:18, height:18, accentColor:"#1e40af", cursor:"pointer" }} onClick={e=>e.stopPropagation()}/>
                      <div style={{ flex:1 }}>
                        <div style={{ fontWeight:700, fontSize:14, color:checked?"#1e40af":"#0f172a" }}>Shift {s.shift_name}</div>
                        <div style={{ fontSize:12, color:"#64748b", marginTop:2 }}>
                          Active: {s.start_time?.slice(0,5)||"—"} → {s.end_time?.slice(0,5)||"—"}
                          {s.crosses_midnight && <span style={{ marginLeft:6, color:"#94a3b8" }}>(crosses midnight)</span>}
                        </div>
                      </div>
                      <span style={{ fontSize:11, fontWeight:700, color:checked?"#16a34a":"#94a3b8" }}>{checked?"✓ ACTIVE":"OFFLINE"}</span>
                    </div>
                  );
                })}
              </div>
            )}
            <ModalActions>
              <Btn onClick={() => setSubPage(0)}>← Back</Btn>
              <Btn variant="primary" onClick={saveShifts} disabled={saving}>{saving?"Saving…":"Save Active Shifts ✓"}</Btn>
            </ModalActions>
          </div>
        )}

        {/* ── ③ Machines ── */}
        {subPage === 2 && (
          <div>
            {(() => {
              const zn = (editing && (editing.zone_name || (zones.find(z => z.id === editing.zone_id) || {}).zone_name)) || "";
              return zn.trim().toLowerCase() === "recliner"
                ? <ReclinerConfig line={editing} token={token} toast={toast} /> : null;
            })()}
            <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:16 }}>
              <span style={{ fontSize:12, color:"#64748b" }}>PLC machines for this line. Each machine is a separate physical data source.</span>
              {!machineForm && <Btn size="sm" variant="primary" onClick={() => setMachineForm({...BLANK_MACHINE})}>+ Add Machine</Btn>}
            </div>

            {/* Inline machine form */}
            {machineForm && (
              <div style={{ background:"#f8fafc", border:"1.5px solid #e2e8f0", borderRadius:12, padding:18, marginBottom:18 }}>
                <div style={{ fontSize:12, fontWeight:700, color:"#0f172a", marginBottom:14 }}>{machineForm.id ? "Edit Machine" : "New Machine"}</div>
                <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:12, marginBottom:14 }}>
                  <FF label="Machine Name *" style={{ gridColumn:"span 3" }}>
                    <Input value={machineForm.machine_name} onChange={e=>setMF("machine_name",e.target.value)} placeholder="e.g. Main PLC, Robot Arm 1"/>
                  </FF>
                  <FF label="PLC IP *"><Input value={machineForm.plc_ip} onChange={e=>setMF("plc_ip",e.target.value)} placeholder="192.168.30.151"/></FF>
                  <FF label="Port"><Input type="number" value={machineForm.plc_port} onChange={e=>setMF("plc_port",parseInt(e.target.value))}/></FF>
                  <FF label="Protocol"><Select value={machineForm.protocol} onChange={e=>setMF("protocol",e.target.value)}><option>MC4E</option><option>MC3E</option></Select></FF>
                </div>
                <div style={{ fontSize:11, fontWeight:700, color:"#64748b", letterSpacing:".08em", textTransform:"uppercase", marginBottom:10 }}>Signal Registers</div>
                <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr 1fr", gap:10, marginBottom:14 }}>
                  <FF label="OK Bit"><Input value={machineForm.ok_bit_address} onChange={e=>setMF("ok_bit_address",e.target.value)}/></FF>
                  <FF label="NG Bit"><Input value={machineForm.ng_bit_address} onChange={e=>setMF("ng_bit_address",e.target.value)}/></FF>
                  <FF label="Status Word"><Input value={machineForm.status_address} onChange={e=>setMF("status_address",e.target.value)}/></FF>
                  <FF label="Model Word"><Input value={machineForm.model_address} onChange={e=>setMF("model_address",e.target.value)}/></FF>
                </div>
                <div style={{ fontSize:11, fontWeight:700, color:"#64748b", letterSpacing:".08em", textTransform:"uppercase", marginBottom:10 }}>Cycle Time</div>
                <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:10, marginBottom:14 }}>
                  <FF label="Ideal CT (sec)"><Input type="number" step="0.1" value={machineForm.ideal_cycle_time} onChange={e=>setMF("ideal_cycle_time",parseFloat(e.target.value))}/></FF>
                  <FF label="Max CT (sec)"><Input type="number" step="0.1" value={machineForm.max_allowed_cycle} onChange={e=>setMF("max_allowed_cycle",parseFloat(e.target.value))}/></FF>
                  <FF label="Pulse Gap (sec)"><Input type="number" step="0.1" value={machineForm.ok_ng_pulse_min_gap} onChange={e=>setMF("ok_ng_pulse_min_gap",parseFloat(e.target.value))}/></FF>
                </div>
                <div style={{ fontSize:11, fontWeight:700, color:"#64748b", letterSpacing:".08em", textTransform:"uppercase", marginBottom:10 }}>Poka Yoke Registers (optional)</div>
                <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:10, marginBottom:16 }}>
                  <FF label="Sensor OK"><Input value={machineForm.sensor_ok_address} onChange={e=>setMF("sensor_ok_address",e.target.value)} placeholder="e.g. L110"/></FF>
                  <FF label="Process Seq"><Input value={machineForm.process_seq_address} onChange={e=>setMF("process_seq_address",e.target.value)} placeholder="e.g. D6010"/></FF>
                  <FF label="Override Bit"><Input value={machineForm.override_address} onChange={e=>setMF("override_address",e.target.value)} placeholder="e.g. D6011"/></FF>
                </div>
                <div style={{ display:"flex", gap:8 }}>
                  <Btn variant="primary" onClick={saveMachine} disabled={saving}>{saving?"Saving…":"Save Machine ✓"}</Btn>
                  <Btn onClick={() => setMachineForm(null)}>Cancel</Btn>
                </div>
              </div>
            )}

            {/* Machine list */}
            {subLoading ? <Spinner /> : machines.length === 0 && !machineForm ? (
              <EmptyState text="No machines yet" sub="Add a PLC machine to this line"/>
            ) : (
              <div style={{ display:"flex", flexDirection:"column", gap:8 }}>
                {machines.map(m => (
                  <div key={m.id} style={{ display:"flex", alignItems:"center", gap:12, padding:"12px 16px", background:"#f8fafc", borderRadius:10, border:"1px solid #e2e8f0" }}>
                    <div style={{ width:36, height:36, borderRadius:8, background:"rgba(30,64,175,.08)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:15, color:"#1e40af", flexShrink:0 }}>⚙</div>
                    <div style={{ flex:1, minWidth:0 }}>
                      <div style={{ fontWeight:700, fontSize:13, color:"#0f172a" }}>{m.machine_name || "Unnamed"}</div>
                      <div style={{ fontSize:11, color:"#94a3b8", marginTop:2 }}>{m.plc_ip}:{m.plc_port} · {m.protocol} · Ideal CT: {m.ideal_cycle_time}s</div>
                    </div>
                    <div style={{ display:"flex", gap:6 }}>
                      <Btn size="sm" onClick={() => setMachineForm({...m})}>Edit</Btn>
                      <Btn size="sm" variant="danger" onClick={() => deleteMachine(m.id)}>Delete</Btn>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── ④ Dashboard PLC ── */}
        {subPage === 3 && (
          <div>
            <p style={{ fontSize:12, color:"#64748b", marginBottom:20 }}>
              Choose which machine's output feeds the <strong>Dashboard</strong> and <strong>Fullscreen</strong> pages.
              Only one machine can be the active data source at a time.
            </p>
            {machines.length === 0 ? (
              <div style={{ textAlign:"center", padding:32, color:"#94a3b8", fontSize:13 }}>No machines configured — add machines in the ③ Machines tab first</div>
            ) : (
              <div style={{ display:"flex", flexDirection:"column", gap:10, marginBottom:24 }}>
                {machines.map(m => {
                  const sel = dashboardPlcId === m.id;
                  return (
                    <div key={m.id} onClick={() => setDashboardPlcId(m.id)}
                      style={{ display:"flex", alignItems:"center", gap:14, padding:"14px 18px", borderRadius:10, border:`1.5px solid ${sel?"#1e40af":"#e2e8f0"}`, background:sel?"rgba(30,64,175,.05)":"#fff", cursor:"pointer", transition:"all .12s" }}>
                      <div style={{ width:20, height:20, borderRadius:"50%", border:`2px solid ${sel?"#1e40af":"#cbd5e1"}`, background:sel?"#1e40af":"#fff", flexShrink:0, display:"flex", alignItems:"center", justifyContent:"center" }}>
                        {sel && <div style={{ width:8, height:8, borderRadius:"50%", background:"#fff" }}/>}
                      </div>
                      <div style={{ flex:1, minWidth:0 }}>
                        <div style={{ fontWeight:700, fontSize:14, color:sel?"#1e40af":"#0f172a" }}>{m.machine_name || "Unnamed"}</div>
                        <div style={{ fontSize:11, color:"#94a3b8", marginTop:2 }}>{m.plc_ip}:{m.plc_port} · {m.protocol} · CT: {m.ideal_cycle_time}s</div>
                      </div>
                      {sel && <span style={{ fontSize:11, fontWeight:800, color:"#1e40af" }}>✓ DASHBOARD SOURCE</span>}
                    </div>
                  );
                })}
              </div>
            )}
            <ModalActions>
              <Btn onClick={() => setSubPage(2)}>← Machines</Btn>
              <Btn variant="primary" onClick={saveDashboardPlc} disabled={saving || !dashboardPlcId}>{saving?"Saving…":"Set Dashboard PLC ✓"}</Btn>
            </ModalActions>
          </div>
        )}

        {/* ── ⑤ Models ── */}
        {subPage === 4 && (() => {
          const byId = {};
          pyModelOptions.forEach(m => { byId[m.id] = m; });
          const assignedRows = selectedPyIds
            .map(id => byId[id])
            .filter(Boolean)
            .sort((a,b) => (a.bitNumber ?? 9999) - (b.bitNumber ?? 9999));
          const cleanName = s => String(s||"").replace(/^TYPE-SERIES:\s*/i,"");
          // Per-model CT editor: only for the model-aware line (YWD-SS).
          const showModelCT = /ywd[\s_-]*ss/i.test(editing?.line_name || "");

          return (
            <div>
              <p style={{ fontSize:12, color:"#64748b", marginBottom:16 }}>
                Iss line par jo models chlte hain unhe <b>Poka Yoke &rarr; Model Master</b> se pick karo.
                Model add/edit Model Master mein hi hota hai — yahaan sirf assign/unassign.
              </p>
              {showModelCT && (
                <p style={{ fontSize:12, color:"#0369a1", background:"rgba(2,132,199,.06)", border:"1px solid #bae6fd",
                  borderRadius:8, padding:"8px 12px", marginBottom:14 }}>
                  ⏱ <b>Model-aware plan:</b> har model ka <b>ideal CT (sec)</b> set karo — INR vs OTR alag.
                  Hourly plan aur changeover-impact isi CT se auto-calc hote hain (dedicated YWD-SS fullscreen par).
                </p>
              )}

              {/* Header + Add button */}
              <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:12,gap:10,flexWrap:"wrap"}}>
                <div style={{fontSize:12,color:"#64748b",fontWeight:700,letterSpacing:".04em"}}>
                  Assigned Models <span style={{color:"#0f172a"}}>({assignedRows.length})</span>
                </div>
                <Btn size="sm" variant="primary" onClick={()=>setPyPickerOpen(true)}
                  disabled={pyModelOptions.length === 0}>
                  + Add Models
                </Btn>
              </div>

              {/* Assigned list */}
              {pyModelOptions.length === 0 ? (
                <div style={{
                  padding:"14px 16px", borderRadius:10,
                  background:"rgba(220,38,38,.04)", border:"1px dashed #fecaca",
                  fontSize:12, color:"#991b1b",
                }}>
                  Model Master abhi khali hai. Pehle <b>Poka Yoke &rarr; Model Master</b> mein models banao.
                </div>
              ) : assignedRows.length === 0 ? (
                <div style={{
                  padding:"18px 16px", borderRadius:10,
                  background:"#f8fafc", border:"1px dashed #e2e8f0",
                  textAlign:"center", fontSize:12, color:"#94a3b8",
                }}>
                  Koi model assign nahi. <b>+ Add Models</b> se Model Master se pick karo.
                </div>
              ) : (
                <div style={{display:"flex",flexDirection:"column",gap:6}}>
                  {assignedRows.map(m => (
                    <div key={m.id} style={{
                      display:"flex",alignItems:"center",gap:10,padding:"10px 12px",
                      background:"#fff",border:"1px solid #e2e8f0",borderRadius:10,
                    }}>
                      <span style={{display:"inline-flex",alignItems:"center",justifyContent:"center",minWidth:44,height:32,padding:"0 10px",borderRadius:7,background:"linear-gradient(135deg,#7c3aed,#6d28d9)",color:"#fff",fontWeight:800,fontSize:12,fontFamily:"monospace"}}>
                        #{m.bitNumber ?? "—"}
                      </span>
                      <span style={{fontFamily:"monospace",fontWeight:700,color:"#0f172a",flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={cleanName(m.modelName)}>
                        {cleanName(m.modelName)}
                      </span>
                      {m.type && <span style={{padding:"2px 8px",borderRadius:99,fontSize:10,fontWeight:700,background:"rgba(30,64,175,.1)",color:"#1e40af",whiteSpace:"nowrap"}}>{m.type}</span>}
                      {showModelCT && (
                        <span style={{display:"inline-flex",alignItems:"center",gap:5,whiteSpace:"nowrap"}} title="Ideal cycle time for this model — drives the model-aware plan (INR vs OTR).">
                          <span style={{fontSize:10,fontWeight:800,color:"#64748b",letterSpacing:".05em"}}>CT</span>
                          <input
                            type="number" step="0.01" min="0" placeholder="sec"
                            value={ctByModel[m.bitNumber] ?? ""}
                            onChange={e => setCtByModel(prev => ({ ...prev, [m.bitNumber]: e.target.value }))}
                            style={{width:66,padding:"6px 8px",border:"1px solid #cbd5e1",borderRadius:7,fontSize:12,fontWeight:700,textAlign:"right",fontFamily:"monospace"}}
                          />
                          <span style={{fontSize:10,color:"#94a3b8"}}>s</span>
                        </span>
                      )}
                      <button
                        onClick={()=>togglePyModel(m.id)}
                        title="Unassign"
                        style={{
                          border:"1px solid #fecaca",background:"rgba(220,38,38,.06)",color:"#dc2626",
                          fontWeight:800,cursor:"pointer",fontSize:12,lineHeight:1,
                          width:26,height:26,borderRadius:6,padding:0,
                        }}
                      >✕</button>
                    </div>
                  ))}
                </div>
              )}

              <ModalActions>
                <Btn onClick={() => setModal(false)}>Close</Btn>
                <Btn variant="primary" onClick={saveModels} disabled={saving}>
                  {saving ? "Saving…" : "Save Assignments ✓"}
                </Btn>
              </ModalActions>

              {/* Picker dialog — checkbox list of all Master models */}
              <Modal
                open={pyPickerOpen}
                onClose={()=>setPyPickerOpen(false)}
                title="Select Models from Master"
                wide
              >
                <div style={{fontSize:11,color:"#64748b",marginBottom:12}}>
                  Ticked models is line par assign honge (abhi Save nahi kiya — "Done" click karke fir "Save Assignments" dabao).
                </div>
                <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10,gap:10}}>
                  <div style={{fontSize:11,color:"#64748b",fontWeight:700,letterSpacing:".06em",textTransform:"uppercase"}}>
                    {selectedPyIds.length} of {pyModelOptions.length} selected
                  </div>
                  <div style={{display:"flex",gap:6}}>
                    <Btn size="sm" onClick={()=>setSelectedPyIds(pyModelOptions.filter(m=>m.bitNumber!=null).map(m=>m.id))}>Select All</Btn>
                    <Btn size="sm" onClick={()=>setSelectedPyIds([])}>Clear</Btn>
                  </div>
                </div>
                <div style={{
                  maxHeight:400, overflowY:"auto",
                  display:"flex", flexDirection:"column", gap:6,
                  border:"1px solid #e2e8f0", borderRadius:10, padding:10, background:"#f8fafc",
                }}>
                  {[...pyModelOptions]
                    .sort((a,b) => (a.bitNumber ?? 9999) - (b.bitNumber ?? 9999))
                    .map(m => {
                      const checked  = selectedPyIds.includes(m.id);
                      const name     = cleanName(m.modelName);
                      const disabled = m.bitNumber == null;
                      return (
                        <label key={m.id} style={{
                          display:"flex",alignItems:"center",gap:10,padding:"8px 10px",
                          background: checked ? "rgba(30,64,175,.08)" : "#fff",
                          border: `1px solid ${checked ? "rgba(30,64,175,.3)" : "#e2e8f0"}`,
                          borderRadius:8, cursor: disabled ? "not-allowed" : "pointer",
                          opacity: disabled ? 0.5 : 1,
                        }} title={disabled ? "No bit number set on this model — edit in Model Master" : ""}>
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={disabled}
                            onChange={()=>togglePyModel(m.id)}
                          />
                          <span style={{display:"inline-flex",alignItems:"center",justifyContent:"center",minWidth:40,height:30,padding:"0 10px",borderRadius:7,background:disabled?"#e2e8f0":"linear-gradient(135deg,#7c3aed,#6d28d9)",color:disabled?"#94a3b8":"#fff",fontWeight:800,fontSize:12,fontFamily:"monospace"}}>
                            #{m.bitNumber ?? "—"}
                          </span>
                          <span style={{fontFamily:"monospace",fontWeight:600,color:"#0f172a",flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={name}>
                            {name}
                          </span>
                        </label>
                      );
                    })}
                </div>
                <ModalActions>
                  <Btn variant="primary" onClick={()=>setPyPickerOpen(false)}>Done</Btn>
                </ModalActions>
              </Modal>
            </div>
          );
        })()}

        {/* ── ⑦ OT Config ── */}
        {subPage === 6 && (
          <div>
            <p style={{ fontSize:12, color:"#64748b", marginBottom:20 }}>
              Configure overtime window for each production shift (per line).<br/>
              During OT the base shift plan freezes; OT output is counted in its own OT slot.
              Set a manual <b>OT Target</b> (pcs) so the OT slot shows plan / variance / efficiency.
            </p>
            {subLoading ? <Spinner /> : otConfigs.length === 0 ? (
              <div style={{ textAlign:"center", padding:30, color:"#94a3b8", fontSize:13 }}>No production shifts found. Assign this line to a zone first.</div>
            ) : (
              <div style={{ display:"flex", flexDirection:"column", gap:14, marginBottom:24 }}>
                {otConfigs.map((cfg, i) => (
                  <div key={cfg.shift_name} style={{ background:"#f8fafc", border:"1px solid #e2e8f0", borderRadius:10, padding:16 }}>
                    <div style={{ fontSize:12, fontWeight:800, color:"#0f172a", marginBottom:12 }}>Shift {cfg.shift_name} — OT Window</div>
                    <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:12 }}>
                      <FF label="OT Start Time">
                        <Input type="time" value={cfg.ot_start_time} onChange={e => setOtFld(i, "ot_start_time", e.target.value)}/>
                      </FF>
                      <FF label="OT End Time">
                        <Input type="time" value={cfg.ot_end_time} onChange={e => setOtFld(i, "ot_end_time", e.target.value)}/>
                      </FF>
                      <FF label="OT Target (pcs)">
                        <Input type="number" min="0" step="1" placeholder="manual target"
                               value={cfg.ot_plan ?? ""} onChange={e => setOtFld(i, "ot_plan", e.target.value)}/>
                      </FF>
                    </div>
                    {cfg.ot_start_time && cfg.ot_end_time && (
                      <div style={{ marginTop:10, padding:"8px 12px", borderRadius:7, background:"rgba(22,163,74,.06)", border:"1px solid rgba(22,163,74,.2)", fontSize:11, color:"#16a34a", fontWeight:600 }}>
                        OT window: {cfg.ot_start_time} → {cfg.ot_end_time}. Base plan freezes at shift end; OT output counts in the OT slot
                        {cfg.ot_plan ? ` against a target of ${cfg.ot_plan} pcs.` : " (no OT target set — set one for plan/variance/eff)."}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
            <ModalActions>
              <Btn onClick={() => setModal(false)}>Close</Btn>
              <Btn variant="primary" onClick={saveOtConfig} disabled={saving || otConfigs.length === 0}>{saving?"Saving…":"Save OT Config ✓"}</Btn>
            </ModalActions>
          </div>
        )}

        {/* ── ⑥ Planning ── */}
        {subPage === 5 && (
          <div>
            <p style={{ fontSize:12, color:"#64748b", marginBottom:20 }}>
              Set the ideal cycle time to auto-calculate production plan per shift.<br/>
              Formula: <code style={{ background:"#f1f5f9", padding:"2px 6px", borderRadius:4, fontSize:12 }}>Plan = ⌊Working Minutes × 60 ÷ Ideal CT⌋</code>
            </p>
            <div style={{ display:"grid", gridTemplateColumns:"220px 220px 1fr", gap:16, alignItems:"end", marginBottom:24 }}>
              <FF label="Ideal Cycle Time (seconds)" hint="Machine's achievable target — used to compute plan">
                <Input type="number" step="0.1" min="0.1" value={idealCt}
                  onChange={e => {
                    const ct = parseFloat(e.target.value) || 15;
                    setIdealCt(ct);
                    setPlanningShifts(s => s.map(sh => ({...sh, _calc: sh.working_minutes > 0 ? Math.floor(sh.working_minutes*60/ct) : 0 })));
                  }}/>
              </FF>
              <FF label="Planned Takt Time (seconds)" hint="Customer-demand rhythm. Shown as Plan in the Takt Time card on Fullscreen.">
                <Input type="number" step="0.01" min="0" value={plannedTakt}
                  placeholder="optional — e.g. 15.33"
                  onChange={e => setPlannedTakt(e.target.value)}/>
              </FF>
              <div/>
            </div>
            {subLoading ? <Spinner /> : planningShifts.filter(s=>!s.shift_name.startsWith("GAP")).length === 0 ? (
              <div style={{ textAlign:"center", padding:30, color:"#94a3b8", fontSize:13 }}>No shifts configured. Configure shifts in the Zone settings first.</div>
            ) : (
              <table style={{ width:"100%", borderCollapse:"collapse" }}>
                <thead>
                  <tr style={{ background:"#f8fafc" }}>
                    {["Shift","Start","End","Working Min","Current Plan","New Plan"].map(h=>(
                      <th key={h} style={{ padding:"10px 14px", textAlign:"left", fontSize:11, fontWeight:700, color:"#64748b", letterSpacing:".06em", textTransform:"uppercase", borderBottom:"2px solid #e2e8f0" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {planningShifts.filter(s=>!s.shift_name.startsWith("GAP")).map(s => {
                    const calc  = idealCt > 0 ? Math.floor(s.working_minutes * 60 / idealCt) : 0;
                    const dirty = calc !== s.total_plan;
                    return (
                      <tr key={s.shift_name} style={{ borderBottom:"1px solid #f1f5f9" }}>
                        <td style={{ padding:"12px 14px", fontWeight:700 }}>Shift {s.shift_name}</td>
                        <td style={{ padding:"12px 14px", color:"#64748b", fontFamily:"monospace" }}>{s.start_time?.slice(0,5)||"—"}</td>
                        <td style={{ padding:"12px 14px", color:"#64748b", fontFamily:"monospace" }}>{s.end_time?.slice(0,5)||"—"}</td>
                        <td style={{ padding:"12px 14px", color:"#0f172a" }}>{s.working_minutes} min</td>
                        <td style={{ padding:"12px 14px", color:"#94a3b8" }}>{s.total_plan}</td>
                        <td style={{ padding:"12px 14px" }}>
                          <span style={{ fontSize:16, fontWeight:700, color:dirty?"#1e40af":"#94a3b8" }}>{calc}</span>
                          {dirty && <span style={{ fontSize:10, color:"#16a34a", marginLeft:6, fontWeight:700 }}>← will update</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
            <ModalActions>
              <Btn onClick={() => setModal(false)}>Close</Btn>
              <Btn variant="primary" onClick={savePlanning} disabled={saving || !idealCt}>{saving?"Saving…":"Save & Apply Plan ✓"}</Btn>
            </ModalActions>
          </div>
        )}

        {/* ── ⑧ Hourly Slots (per line) ── */}
        {subPage === 7 && (() => {
          const rows       = lineSlots.map((s, i) => ({ s, i })).filter(x => x.s.shift_name === lineSlotShift);
          const shiftTotal = Number(lineShiftPlans[lineSlotShift] || 0);
          const slotSum    = rows.reduce((a, x) => a + (Number(x.s.plan_pieces) || 0), 0);
          const cols       = "1fr 88px 88px 84px 68px 150px 56px 22px";
          return (
            <div>
              <p style={{ fontSize:12, color:"#64748b", marginBottom:16 }}>
                Per-line hourly plan for <b>{editing?.line_name || "this line"}</b> — independent of the zone-common slots.
                Edit each slot's plan, or <b>Auto-distribute</b> this shift's plan across its slots by working-minutes.
              </p>
              <div style={{ display:"flex", alignItems:"flex-end", gap:12, marginBottom:14, flexWrap:"wrap" }}>
                <FF label="Shift">
                  <Select value={lineSlotShift} onChange={e=>setLineSlotShift(e.target.value)} style={{ width:110 }}>
                    <option value="A">Shift A</option><option value="B">Shift B</option>
                  </Select>
                </FF>
                <Btn size="sm" variant="primary" onClick={addLineSlot}>+ Add Slot</Btn>
                <Btn size="sm" onClick={distributeLinePlan}>⚖ Auto-distribute{shiftTotal?` (${shiftTotal})`:""}</Btn>
                <div style={{ marginLeft:"auto", fontSize:12, fontWeight:600, color: (slotSum===shiftTotal && shiftTotal) ? "#16a34a" : "#b45309" }}>
                  Σ slots {slotSum} / plan {shiftTotal || "—"}
                </div>
              </div>
              {subLoading ? <Spinner /> : rows.length === 0 ? (
                <EmptyState text={`No slots for Shift ${lineSlotShift}`} sub="Click + Add Slot, then set Work-Min and Auto-distribute the plan." />
              ) : (
                <div style={{ overflowX:"auto" }}>
                  <div style={{ display:"grid", gridTemplateColumns:cols, gap:6, padding:"6px 4px", borderBottom:"2px solid #e2e8f0", marginBottom:6, minWidth:640 }}>
                    {["Slot Label","Start","End","Plan Pcs","Work Min","DB Column","Order",""].map(h=>(
                      <div key={h} style={{ fontSize:9, fontWeight:700, letterSpacing:".08em", textTransform:"uppercase", color:"#64748b" }}>{h}</div>
                    ))}
                  </div>
                  {rows.map(({ s, i }) => (
                    <div key={i} style={{ display:"grid", gridTemplateColumns:cols, gap:6, marginBottom:6, alignItems:"center", minWidth:640 }}>
                      <input value={s.slot_label} onChange={e=>setLineSlotFld(i,"slot_label",e.target.value)} placeholder="08:30-09:30" style={miniInp}/>
                      <input type="time" value={s.start_time} onChange={e=>setLineSlotFld(i,"start_time",e.target.value)} style={miniInp}/>
                      <input type="time" value={s.end_time}   onChange={e=>setLineSlotFld(i,"end_time",e.target.value)}   style={miniInp}/>
                      <input type="number" min="0" value={s.plan_pieces}     onChange={e=>setLineSlotFld(i,"plan_pieces",parseInt(e.target.value)||0)}     style={miniInp}/>
                      <input type="number" min="0" value={s.working_minutes} onChange={e=>setLineSlotFld(i,"working_minutes",parseInt(e.target.value)||0)} style={miniInp}/>
                      <input value={s.db_column_prefix} onChange={e=>setLineSlotFld(i,"db_column_prefix",e.target.value)} placeholder="hour_0830_0930" style={miniInp}/>
                      <input type="number" min="0" value={s.slot_order}      onChange={e=>setLineSlotFld(i,"slot_order",parseInt(e.target.value)||0)}      style={miniInp}/>
                      <button onClick={()=>rmLineSlot(i)} style={{ background:"none", border:"none", color:"#dc2626", cursor:"pointer", fontSize:16, padding:0 }}>✕</button>
                    </div>
                  ))}
                </div>
              )}
              <ModalActions>
                <Btn onClick={() => setModal(false)}>Close</Btn>
                <Btn variant="primary" onClick={saveLineSlots} disabled={saving}>{saving?"Saving…":"Save Slots ✓"}</Btn>
              </ModalActions>
            </div>
          );
        })()}
      </Modal>
    </div>
  );
}

// ─── STATUS SCHEMA PAGE ───────────────────────────────────────
export function StatusPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [statuses, setStatuses] = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [modal,    setModal]    = useState(false);
  const [editing,  setEditing]  = useState(null);
  const [form,     setForm]     = useState({ status_code:"", status_name:"", color_hex:"#3b82f6", color_label:"", loss_type:"", is_production:"false" });
  const [saving,   setSaving]   = useState(false);

  const PROTECTED = [0, 1, 2];

  const load = useCallback(async () => {
    try { setStatuses(await api.get("/api/status-schema/", token)); }
    catch { toast("Failed to load", "err"); }
    finally { setLoading(false); }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  const openAdd = () => {
    setEditing(null);
    setForm({ status_code:"", status_name:"", color_hex:"#3b82f6", color_label:"", loss_type:"", is_production:"false" });
    setModal(true);
  };

  const openEdit = (s) => {
    setEditing(s);
    setForm({ status_code: s.status_code, status_name: s.status_name, color_hex: s.color_hex, color_label: s.color_label, loss_type: s.loss_type||"", is_production: String(s.is_production) });
    setModal(true);
  };

  const save = async () => {
    if (!form.status_name || !form.color_hex || !form.color_label) { toast("Name, color and label required","err"); return; }
    if (!form.color_hex.match(/^#[0-9a-fA-F]{3,6}$/)) { toast("Invalid color — use #rrggbb","err"); return; }
    setSaving(true);
    try {
      if (editing) {
        await api.put(`/api/status-schema/${editing.status_code}`, { status_name: form.status_name, color_hex: form.color_hex, color_label: form.color_label, loss_type: form.loss_type||null, is_production: form.is_production==="true" }, token);
        toast("Status updated ✓ — all lines affected");
      } else {
        if (!form.status_code) { toast("Status code required","err"); setSaving(false); return; }
        await api.post("/api/status-schema/", { status_code: parseInt(form.status_code), status_name: form.status_name, color_hex: form.color_hex, color_label: form.color_label, loss_type: form.loss_type||null, is_production: form.is_production==="true" }, token);
        toast("Status added ✓");
      }
      setModal(false); load();
    } catch (e) { toast(e.message,"err"); }
    finally { setSaving(false); }
  };

  const deactivate = async (s) => {
    if (!confirm(`Deactivate status "${s.status_name}"?`)) return;
    try { await api.delete(`/api/status-schema/${s.status_code}`, token); toast("Status deactivated"); load(); }
    catch (e) { toast(e.message,"err"); }
  };

  return (
    <div>
      <div style={{ display:"flex", justifyContent:"flex-end", marginBottom:20 }}>
        <Btn variant="primary" onClick={openAdd}>+ Add Status</Btn>
      </div>
      {statuses.length > 0 && (
        <Card style={{ marginBottom:20 }}>
          <div style={{ fontSize:11, fontWeight:700, color:"#64748b", letterSpacing:".1em", textTransform:"uppercase", marginBottom:12 }}>Live Preview — How operators see this</div>
          <div style={{ display:"flex", flexWrap:"wrap", gap:10 }}>
            {statuses.filter(s=>s.is_active).map(s=>(
              <div key={s.status_code} style={{ display:"flex", alignItems:"center", gap:8, padding:"8px 14px", borderRadius:8, background:`${s.color_hex}18`, border:`1px solid ${s.color_hex}44` }}>
                <div style={{ width:10, height:10, borderRadius:"50%", background:s.color_hex }}/>
                <span style={{ fontSize:12, fontWeight:600, color:"#0f172a" }}>{s.status_name}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
      <Card>
        {loading ? <Spinner /> : (
          <table style={{ width:"100%", borderCollapse:"collapse", fontSize:13 }}>
            <thead>
              <tr>{["Code","Color","Status Name","Machine State","Loss Category","Actions"].map(h=>(
                <th key={h} style={{ padding:"10px 14px", textAlign:"left", fontSize:10, fontWeight:700, letterSpacing:".08em", textTransform:"uppercase", color:"#64748b", borderBottom:"2px solid #e2e8f0" }}>{h}</th>
              ))}</tr>
            </thead>
            <tbody>
              {statuses.map(s=>(
                <tr key={s.status_code} style={{ borderBottom:"1px solid #f1f5f9" }}>
                  <td style={{ padding:"12px 14px", fontFamily:"monospace", fontWeight:700, color:"#1e40af" }}>{s.status_code}</td>
                  <td style={{ padding:"12px 14px" }}>
                    <div style={{ display:"flex", alignItems:"center", gap:8 }}>
                      <div style={{ width:28, height:28, borderRadius:6, background:s.color_hex, border:"1px solid #e2e8f0", flexShrink:0 }}/>
                      <span style={{ fontFamily:"monospace", fontSize:11, color:"#64748b" }}>{s.color_hex}</span>
                      <span style={{ fontSize:11, color:"#94a3b8" }}>{s.color_label}</span>
                    </div>
                  </td>
                  <td style={{ padding:"12px 14px", fontWeight:600, color:"#0f172a" }}>{s.status_name}</td>
                  <td style={{ padding:"12px 14px" }}><Pill label={s.is_production?"Production":"Stoppage"} color={s.is_production?"green":"gray"}/></td>
                  <td style={{ padding:"12px 14px", fontFamily:"monospace", fontSize:11, color:"#64748b" }}>{s.loss_type||"—"}</td>
                  <td style={{ padding:"12px 14px" }}>
                    {PROTECTED.includes(s.status_code)
                      ? <span style={{ fontSize:11, color:"#94a3b8", padding:"4px 8px" }}>Protected</span>
                      : (
                        <div style={{ display:"flex", gap:8 }}>
                          <Btn size="sm" onClick={()=>openEdit(s)}>Edit</Btn>
                          <Btn size="sm" variant="danger" onClick={()=>deactivate(s)}>Remove</Btn>
                        </div>
                      )
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Modal open={modal} onClose={()=>setModal(false)} title={editing?`Edit Status ${editing.status_code} — ${editing.status_name}`:"Add New Status Type"}>
        <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:16 }}>
          <FF label="Status Code *" hint="Integer matching PLC word value">
            <Input type="number" value={form.status_code} onChange={e=>setForm(f=>({...f,status_code:e.target.value}))} placeholder="e.g. 8" disabled={!!editing}/>
          </FF>
          <FF label="Status Name *">
            <Input value={form.status_name} onChange={e=>setForm(f=>({...f,status_name:e.target.value}))} placeholder="e.g. TRIAL RUN"/>
          </FF>
          <FF label="Color (hex) *">
            <div style={{ display:"flex", gap:8, alignItems:"center" }}>
              <Input value={form.color_hex} onChange={e=>setForm(f=>({...f,color_hex:e.target.value}))} placeholder="#3b82f6" style={{flex:1}}/>
              <div style={{ width:38, height:38, borderRadius:7, border:"1px solid #e2e8f0", background:form.color_hex, flexShrink:0, cursor:"pointer", position:"relative" }}>
                <input type="color" value={form.color_hex} onChange={e=>setForm(f=>({...f,color_hex:e.target.value}))} style={{ position:"absolute", inset:0, opacity:0, cursor:"pointer", width:"100%", height:"100%" }}/>
              </div>
            </div>
          </FF>
          <FF label="Color Label *">
            <Input value={form.color_label} onChange={e=>setForm(f=>({...f,color_label:e.target.value}))} placeholder="e.g. Blue"/>
          </FF>
          <FF label="Loss Category">
            <Select value={form.loss_type} onChange={e=>setForm(f=>({...f,loss_type:e.target.value}))}>
              <option value="">None — not a loss</option>
              <option value="breakdown">Breakdown</option>
              <option value="quality">Quality</option>
              <option value="setup">Setup</option>
              <option value="material">Material</option>
              <option value="others">Others</option>
              <option value="change_over">Change Over</option>
              <option value="speed">Speed</option>
            </Select>
          </FF>
          <FF label="Machine State">
            <Select value={form.is_production} onChange={e=>setForm(f=>({...f,is_production:e.target.value}))}>
              <option value="false">Stoppage (not producing)</option>
              <option value="true">Production (making parts)</option>
            </Select>
          </FF>
        </div>
        <ModalActions>
          <Btn onClick={()=>setModal(false)}>Cancel</Btn>
          <Btn variant="primary" onClick={save} disabled={saving}>{saving?"Saving…":"Save — applies to all lines"}</Btn>
        </ModalActions>
      </Modal>
    </div>
  );
}

// ─── LINE ASSIGN MODAL ────────────────────────────────────────
function LineAssignModal({ py, lines, zones, rules, token, toast, onClose, onReload }) {
  const assignedMap = rules
    .filter(r => r.poka_yoke_no === py.pyNo)
    .reduce((m, r) => { m[r.line_id] = r.id; return m; }, {});
  const [checked, setChecked] = useState(() => new Set(Object.keys(assignedMap).map(Number)));
  const [saving,  setSaving]  = useState(false);

  const toggle = id => setChecked(prev => {
    const n = new Set(prev);
    n.has(id) ? n.delete(id) : n.add(id);
    return n;
  });

  const save = async () => {
    setSaving(true);
    try {
      const toAdd    = lines.filter(l => checked.has(l.id) && !assignedMap[l.id]);
      const toRemove = lines.filter(l => !checked.has(l.id) && assignedMap[l.id]);
      await Promise.all([
        ...toAdd.map(l => api.post(`/api/poka-yoke/rules/${l.id}`, {
          poka_yoke_no:   py.pyNo,
          poka_yoke_name: py.description || py.pyNo,
          side:           py.typeSide   || "ALL",
          model:          "ALL",
          bit:            py.dBit        || "",
          value:          py.desiredValue ?? 1,
          machine_name:   py.machineFixture || "",
          sheet_name:     "",
          alert_level:    "WARNING",
          is_active:      true,
        }, token)),
        ...toRemove.map(l => api.delete(`/api/poka-yoke/rules/${assignedMap[l.id]}`, token)),
      ]);
      toast("Line assignment saved ✓");
      onReload();
      onClose();
    } catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  const byZone = zones.map(z => ({ zone: z, zlines: lines.filter(l => l.zone_id === z.id) }))
                      .filter(g => g.zlines.length > 0);

  return (
    <Modal open onClose={onClose} title={`Assign "${py.description || py.pyNo}" to Lines`}>
      <p style={{fontSize:12,color:"#64748b",marginBottom:14}}>
        Select which production lines should monitor this poka-yoke check (Bit: <b>{py.dBit||"—"}</b>, Value: <b>{py.desiredValue??1}</b>).
      </p>
      <div style={{maxHeight:320,overflowY:"auto",border:"1px solid #e2e8f0",borderRadius:8,padding:4}}>
        {byZone.length === 0
          ? <div style={{padding:20,textAlign:"center",color:"#94a3b8",fontSize:12}}>No lines configured</div>
          : byZone.map(({ zone, zlines }) => (
            <div key={zone.id} style={{marginBottom:4}}>
              <div style={{fontSize:10,fontWeight:700,color:zone.color||"#64748b",letterSpacing:".08em",textTransform:"uppercase",padding:"6px 10px 4px",background:zone.color?`${zone.color}14`:"#f8fafc",borderRadius:6,borderLeft:zone.color?`3px solid ${zone.color}`:"none"}}>{zone.zone_name}</div>
              {zlines.map(l => (
                <label key={l.id} style={{display:"flex",alignItems:"center",gap:10,padding:"7px 12px",borderRadius:6,cursor:"pointer",background:checked.has(l.id)?"rgba(30,64,175,.06)":"transparent",border:`1px solid ${checked.has(l.id)?"rgba(30,64,175,.25)":"transparent"}`,margin:"2px 0"}}>
                  <input type="checkbox" checked={checked.has(l.id)} onChange={()=>toggle(l.id)} style={{width:15,height:15,accentColor:"#1e40af"}}/>
                  <span style={{fontSize:13,fontWeight:checked.has(l.id)?600:400,color:checked.has(l.id)?"#1e40af":"#0f172a",flex:1}}>{l.line_name}</span>
                  {checked.has(l.id) && assignedMap[l.id] && <span style={{fontSize:10,color:"#16a34a",fontWeight:600}}>✓ Already assigned</span>}
                  {checked.has(l.id) && !assignedMap[l.id] && <span style={{fontSize:10,color:"#1e40af",fontWeight:600}}>+ New</span>}
                </label>
              ))}
            </div>
          ))
        }
      </div>
      <div style={{fontSize:11,color:"#64748b",marginTop:10}}>{checked.size} line{checked.size!==1?"s":""} selected</div>
      <ModalActions>
        <Btn onClick={onClose}>Cancel</Btn>
        <Btn variant="primary" onClick={save} disabled={saving}>{saving?"Saving…":"Save Assignment"}</Btn>
      </ModalActions>
    </Modal>
  );
}

// ─── POKA YOKE PAGE ───────────────────────────────────────────
// ─── POKA YOKE PAGE ──────────────────────────────────────────
export function PokaYokePage({ toast, readOnly = false }) {
  const { token } = useAuth();
  // Sub-tab is persisted in localStorage so a refresh keeps you on the
  // same sub-page (operator's typical flow: open Sensor Health, alt-tab
  // to PLC, refresh — landing back on Model Master would be jarring).
  const SUB_LS_KEY = "ap.pokayoke.sub";
  const [subTab,   setSubTab]   = useState(() => {
    try { return localStorage.getItem(SUB_LS_KEY) || "models"; }
    catch { return "models"; }
  });
  useEffect(() => {
    try { localStorage.setItem(SUB_LS_KEY, subTab); } catch {}
  }, [subTab]);
  // Dropdown open/close state — replaces the old horizontal tab strip.
  const [subOpen, setSubOpen] = useState(false);
  const subRef = useRef(null);
  useEffect(() => {
    const onDocDown = (e) => {
      if (subOpen && subRef.current && !subRef.current.contains(e.target)) setSubOpen(false);
    };
    document.addEventListener("mousedown", onDocDown);
    return () => document.removeEventListener("mousedown", onDocDown);
  }, [subOpen]);
  const [lines,    setLines]    = useState([]);
  const [zones,    setZones]    = useState([]);
  const [rules,    setRules]    = useState([]);
  const [pyMaster, setPyMaster] = useState([]);
  const [models,   setModels]   = useState([]);
  const [series,   setSeries]   = useState([]);
  const [assignments, setAssignments] = useState([]);
  const [events,   setEvents]   = useState([]);
  const [loading,  setLoading]  = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [l, z, m, py, asgn, sr] = await Promise.all([
        api.get("/api/lines/",             token).catch(()=>[]),
        api.get("/api/zones/",             token).catch(()=>[]),
        api.get("/api/poka-yoke/models/",  token).catch(()=>[]),
        api.get("/api/poka-yoke/master/",  token).catch(()=>[]),
        api.get("/api/poka-yoke/assignments/", token).catch(()=>[]),
        api.get("/api/poka-yoke/series/",  token).catch(()=>[]),
      ]);
      const linesArr = Array.isArray(l) ? l : [];
      setLines(linesArr);
      setZones(Array.isArray(z) ? z : []);
      setModels(Array.isArray(m) ? m : []);
      setPyMaster(Array.isArray(py) ? py : []);
      setAssignments(Array.isArray(asgn) ? asgn : []);
      setSeries(Array.isArray(sr) ? sr : []);

      let allRules=[], allEvents=[];
      await Promise.allSettled(linesArr.map(async line => {
        const [r,e] = await Promise.all([
          api.get(`/api/poka-yoke/rules/${line.id}`, token).catch(()=>[]),
          api.get(`/api/poka-yoke/events/${line.id}?unacked_only=true&limit=20`, token).catch(()=>({events:[]})),
        ]);
        allRules.push(...(Array.isArray(r)?r:[]).map(x=>({...x,line_name:line.line_name})));
        allEvents.push(...(e.events||[]).map(x=>({...x,line_name:line.line_name})));
      }));
      setRules(allRules); setEvents(allEvents);
    } catch { toast("Failed to load","err"); }
    finally { setLoading(false); }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  const SUB_TABS = [
    { key:"models", label:"🗂️ Model Master"     },
    { key:"master", label:"🔍 Poka Yoke Master" },
    { key:"config", label:"⚙️ Config"           },
    { key:"matrix", label:"📊 Matrix"          },
    { key:"health", label:"🔬 Sensor Health"   },
  ];

  const activeSub = SUB_TABS.find(t => t.key === subTab) || SUB_TABS[0];

  return (
    <div>
      {/* Sub-tab dropdown — replaces the old horizontal strip per
          operator request: "PY ab pe click karu toh dropdown de".
          Always interactive (even in read-only mode) so dept users can
          still navigate between Model Master / Master / Config /
          Matrix / Sensor Health.  Selection persists in localStorage
          → refresh keeps you on the last opened sub-page. */}
      <div ref={subRef} style={{position:"relative", marginBottom:24}}>
        <button onClick={() => setSubOpen(o => !o)}
                style={{
                  display:"flex", alignItems:"center", gap:10,
                  padding:"12px 18px", borderRadius:10,
                  border:"1.5px solid #e2e8f0", background:"#fff",
                  color:"#0f172a", fontFamily:"'Barlow',sans-serif",
                  fontSize:14, fontWeight:700, cursor:"pointer",
                  minWidth:260, justifyContent:"space-between",
                  boxShadow: subOpen ? "0 4px 18px rgba(30,64,175,.12)" : "0 1px 3px rgba(0,0,0,.04)",
                  transition:"all .12s",
                }}>
          <span style={{display:"flex", alignItems:"center", gap:10}}>
            <span style={{fontSize:11, fontWeight:700, color:"#94a3b8",
                            letterSpacing:".08em", textTransform:"uppercase"}}>
              Page
            </span>
            <span style={{color:"#1e40af"}}>{activeSub.label}</span>
          </span>
          <span style={{
            transform: subOpen ? "rotate(180deg)" : "rotate(0deg)",
            transition:"transform .15s", color:"#64748b", fontSize:14, fontWeight:800,
          }}>▾</span>
        </button>
        {subOpen && (
          <div style={{
            position:"absolute", top:"calc(100% + 4px)", left:0, zIndex:50,
            background:"#fff", border:"1px solid #e2e8f0", borderRadius:10,
            boxShadow:"0 12px 32px rgba(15,23,42,.18)",
            minWidth:260, padding:"6px",
          }}>
            {SUB_TABS.map(t => (
              <button key={t.key}
                      onClick={() => { setSubTab(t.key); setSubOpen(false); }}
                      style={{
                        display:"block", width:"100%", textAlign:"left",
                        padding:"10px 14px", borderRadius:7, border:"none",
                        background: subTab === t.key ? "rgba(30,64,175,.08)" : "transparent",
                        color: subTab === t.key ? "#1e40af" : "#334155",
                        fontWeight: subTab === t.key ? 700 : 500,
                        fontSize:13, cursor:"pointer",
                        fontFamily:"'Barlow',sans-serif",
                      }}
                      onMouseEnter={e => { if (subTab !== t.key) e.currentTarget.style.background = "#f8fafc"; }}
                      onMouseLeave={e => { if (subTab !== t.key) e.currentTarget.style.background = "transparent"; }}>
                {t.label}
                {subTab === t.key && <span style={{float:"right", color:"#1e40af"}}>✓</span>}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Sub-component output — `readOnly` is threaded down so each
          sub-component can hide its CUD UI (Add / Edit / Delete /
          Import / Template + the Actions column) entirely instead of
          just disabling them.  The fieldset wrap is a belt-and-braces
          guard so any control we missed is still natively disabled. */}
      <fieldset disabled={readOnly}
                style={{ border:0, padding:0, margin:0, minWidth:0 }}>
        {loading ? <Spinner /> : <>
          {subTab==="models" && <PYModels models={models} series={series} zones={zones} toast={toast} token={token} onReload={load} readOnly={readOnly}/>}
          {subTab==="master" && <PYMaster pyMaster={pyMaster} models={models} zones={zones} toast={toast} token={token} onReload={load} readOnly={readOnly}/>}
          {subTab==="config" && <PYConfig assignments={assignments} pyMaster={pyMaster} models={models} lines={lines} zones={zones} toast={toast} token={token} onReload={load} readOnly={readOnly}/>}
          {subTab==="matrix" && <PYMatrix assignments={assignments} events={events} lines={lines} zones={zones} rules={rules} toast={toast} token={token} onReload={load} readOnly={readOnly}/>}
          {subTab==="health" && <SensorHealthPage lines={lines} toast={toast} token={token} readOnly={readOnly}/>}
        </>}
      </fieldset>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// PYStationsPage — machine station codes + paired station PYs (2026-07)
//   Zone → Line → machines (mes_machines): assign SS_01/SS_02 codes.
//   Same code across lines = one paired station → shared PY list.
//   Backend: /api/py-stations/*
// ══════════════════════════════════════════════════════════════
const _psGhost   = { padding:"8px 16px", borderRadius:8, border:"1px solid #e2e8f0", background:"#fff", cursor:"pointer", fontWeight:700, fontSize:13 };
const _psPrimary = { padding:"8px 16px", borderRadius:8, border:"none", background:"#16a34a", color:"#fff", cursor:"pointer", fontWeight:700, fontSize:13 };
const _psMini    = { border:"1px solid #e2e8f0", background:"#fff", borderRadius:6, cursor:"pointer", fontSize:12, padding:"3px 8px", fontWeight:700 };
const _psLbl     = { fontSize:12, color:"#64748b", marginBottom:4, fontWeight:600 };
const _psInp     = { width:"100%", padding:"8px 10px", border:"1px solid #cbd5e1", borderRadius:8, fontSize:13, boxSizing:"border-box" };

function PYStationsPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [zones,    setZones]    = useState([]);
  const [zoneId,   setZoneId]   = useState(null);
  const [lines,    setLines]    = useState([]);
  const [lineId,   setLineId]   = useState(null);
  const [machines, setMachines] = useState([]);
  const [codeEdit, setCodeEdit] = useState({});
  const [groups,   setGroups]   = useState([]);
  const [station,  setStation]  = useState(null);
  const [pys,      setPys]      = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [pyOpen,   setPyOpen]   = useState(false);
  const [pyForm,   setPyForm]   = useState({ py_name:"", d_bit:"", sensing_bits:"", register_count:1, desired_value:"", desired_value_2:"" });
  const [expandPy, setExpandPy] = useState(null);   // py_id whose per-machine outputs are open
  const [pyMachs,  setPyMachs]  = useState([]);      // per-machine outputs of expandPy

  useEffect(() => {
    api.get("/api/py-stations/zones", token).then(z => {
      const a=Array.isArray(z)?z:[]; setZones(a); setZoneId(p => p || (a[0]?.zoneId || null));
    }).catch(()=>toast&&toast("Failed to load zones","err")).finally(()=>setLoading(false));
  }, [token]);

  const refreshGroups = useCallback(() => {
    if (!zoneId) return;
    api.get(`/api/py-stations/groups?zone_id=${zoneId}`, token).then(g=>setGroups(Array.isArray(g)?g:[])).catch(()=>{});
  }, [zoneId, token]);

  const loadZone = useCallback(() => {
    if (!zoneId) return;
    setLineId(null); setMachines([]); setStation(null); setPys([]);
    api.get(`/api/py-stations/lines?zone_id=${zoneId}`, token).then(l=>setLines(Array.isArray(l)?l:[])).catch(()=>{});
    refreshGroups();
  }, [zoneId, token, refreshGroups]);
  useEffect(()=>{ loadZone(); }, [loadZone]);

  const loadMachines = useCallback((id) => {
    api.get(`/api/py-stations/line/${id}/machines`, token).then(m=>{
      const a=Array.isArray(m)?m:[]; setMachines(a);
      setCodeEdit(Object.fromEntries(a.map(x=>[x.id, x.stationCode||""])));
    }).catch(()=>toast&&toast("Failed to load machines","err"));
  }, [token]);
  function pickLine(id){ setLineId(id); if(id) loadMachines(id); else setMachines([]); }

  async function saveCode(m) {
    const code=(codeEdit[m.id]||"").trim().toUpperCase();
    if (code === (m.stationCode||"")) return;
    try {
      await api.put(`/api/py-stations/machine/${m.id}/code`, { station_code: code||null }, token);
      toast&&toast(code?`${code} set ✓`:"cleared","ok");
      loadMachines(lineId); refreshGroups();
    } catch(e){ toast&&toast(e.message||"Save failed","err"); }
  }

  function pickStation(code) {
    setStation(code); setExpandPy(null); setPyMachs([]);
    api.get(`/api/py-stations/pys?zone_id=${zoneId}&station_code=${encodeURIComponent(code)}`, token)
      .then(p=>setPys(Array.isArray(p)?p:[])).catch(()=>{});
  }
  async function addPy() {
    if (!pyForm.py_name.trim()) { toast&&toast("PY name required","err"); return; }
    const b = {
      zone_id: zoneId, station_code: station, py_name: pyForm.py_name.trim(),
      d_bit: pyForm.d_bit.trim()||null, sensing_bits: pyForm.sensing_bits.trim()||null,
      register_count: Number(pyForm.register_count)||1,
      desired_value: pyForm.desired_value===""?null:Number(pyForm.desired_value),
      desired_value_2: pyForm.desired_value_2===""?null:Number(pyForm.desired_value_2),
    };
    try { await api.post(`/api/py-stations/pys`, b, token);
      toast&&toast("PY added ✓","ok"); setPyOpen(false);
      setPyForm({ py_name:"", d_bit:"", sensing_bits:"", register_count:1, desired_value:"", desired_value_2:"" });
      pickStation(station);
    } catch(e){ toast&&toast(e.message||"Add failed","err"); }
  }
  async function delPy(id) {
    if (!window.confirm("Delete this PY from the station?")) return;
    try { await api.delete(`/api/py-stations/pys/${id}`, token); toast&&toast("Deleted ✓","ok"); pickStation(station); }
    catch(e){ toast&&toast(e.message||"Delete failed","err"); }
  }
  function togglePy(p) {
    if (expandPy===p.id) { setExpandPy(null); setPyMachs([]); return; }
    setExpandPy(p.id); setPyMachs([]);
    api.get(`/api/py-stations/pys/${p.id}/machines`, token).then(r=>setPyMachs(Array.isArray(r)?r:[])).catch(()=>{});
  }
  function saveMach(pyId, machineId) {
    setPyMachs(ms => {
      const m = ms.find(x=>x.machineId===machineId);
      if (m) {
        api.put(`/api/py-stations/pys/${pyId}/machine/${machineId}`, {
          enabled: m.enabled,
          d_bit: (m.dBit==null||m.dBit==="")?null:m.dBit,
          desired_value:   (m.desiredValue==null||m.desiredValue==="")?null:Number(m.desiredValue),
          desired_value_2: (m.desiredValue2==null||m.desiredValue2==="")?null:Number(m.desiredValue2),
        }, token)
          .then(()=>api.get(`/api/py-stations/pys/${pyId}/machines`, token).then(r=>setPyMachs(Array.isArray(r)?r:[])))
          .catch(e=>toast&&toast(e.message||"Save failed","err"));
      }
      return ms;
    });
  }
  async function resetMach(pyId, machineId) {
    try { await api.delete(`/api/py-stations/pys/${pyId}/machine/${machineId}`, token);
      const r = await api.get(`/api/py-stations/pys/${pyId}/machines`, token); setPyMachs(Array.isArray(r)?r:[]);
    } catch(e){ toast&&toast(e.message||"Reset failed","err"); }
  }
  async function downloadTemplate() {
    try {
      const res = await fetch("/api/py-stations/template", { headers:{ Authorization:`Bearer ${token}` } });
      if (!res.ok) throw new Error("Template download failed");
      const blob = await res.blob(); const url = URL.createObjectURL(blob);
      const a = document.createElement("a"); a.href=url; a.download="py_stations_machines_template.xlsx"; a.click();
      URL.revokeObjectURL(url);
    } catch(e){ toast&&toast(e.message||"Template failed","err"); }
  }
  async function importExcel(file) {
    if (!file) return;
    const fd = new FormData(); fd.append("file", file);
    try {
      const res = await fetch("/api/py-stations/import", { method:"POST", headers:{ Authorization:`Bearer ${token}` }, body: fd });
      const j = await res.json().catch(()=>({}));
      if (!res.ok) throw new Error(j.detail || "Import failed");
      toast&&toast(`Import ✓ — ${j.updated} set, ${j.cleared} cleared${j.errors&&j.errors.length?`, ${j.errors.length} skipped`:""}`, "ok");
      if (j.errors && j.errors.length) console.warn("import skipped:", j.errors);
      loadZone(); if (lineId) loadMachines(lineId);
    } catch(e){ toast&&toast(e.message||"Import failed","err"); }
  }

  if (loading) return <Spinner/>;
  const chip=(a)=>({ padding:"7px 14px", borderRadius:20, cursor:"pointer", fontSize:13, fontWeight:700, fontFamily:"'Barlow',sans-serif",
    border:`1.5px solid ${a?"#16a34a":"#e2e8f0"}`, background:a?"#f0fdf4":"#fff", color:a?"#15803d":"#475569" });
  const curGroup = groups.find(g=>g.stationCode===station);

  return (
    <div>
      <div style={{display:"flex", justifyContent:"space-between", alignItems:"flex-start", gap:12, marginBottom:14}}>
        <div style={{color:"#64748b", fontSize:13, flex:1}}>
          Zone &rarr; line &rarr; us line ke machines pe <b>station code</b> (SS_01, SS_02…) do. Same code = <b>paired station</b> — uspe ek PY list jo saari paired lines pe same lagti hai.
        </div>
        {!readOnly && (
          <div style={{display:"flex", gap:8, flexShrink:0}}>
            <button onClick={downloadTemplate} style={_psGhost} title="Har machine ka data — Excel">&#8681; Template</button>
            <label style={{..._psGhost, display:"inline-flex", alignItems:"center", gap:4}} title="Filled template upload karo">
              &#128228; Import Excel
              <input type="file" accept=".xlsx,.xls" style={{display:"none"}}
                     onChange={e=>{ const f=e.target.files&&e.target.files[0]; e.target.value=""; importExcel(f); }}/>
            </label>
          </div>
        )}
      </div>

      <div style={{display:"flex", flexWrap:"wrap", gap:8, marginBottom:16}}>
        {zones.map(z => (
          <button key={z.zoneId} style={chip(z.zoneId===zoneId)} onClick={()=>setZoneId(z.zoneId)}>
            {z.zoneName} <span style={{opacity:.6, fontWeight:600}}>({z.lineCount})</span>
          </button>
        ))}
      </div>

      <div style={{display:"grid", gridTemplateColumns:"repeat(auto-fit, minmax(340px, 1fr))", gap:16, alignItems:"start"}}>
        {/* LEFT — assign codes */}
        <Card>
          <div style={{fontWeight:700, fontSize:15, color:"#0f172a", marginBottom:10}}>1&#65039;&#8419; Machines &rarr; Station code</div>
          <select value={lineId||""} onChange={e=>pickLine(e.target.value?Number(e.target.value):null)}
                  style={{width:"100%", padding:"9px 11px", border:"1px solid #cbd5e1", borderRadius:8, fontSize:14, marginBottom:12}}>
            <option value="">— Line chuno —</option>
            {lines.map(l => <option key={l.lineId} value={l.lineId}>{l.lineName}</option>)}
          </select>
          {!lineId ? <EmptyState text="Line chuno" sub="Uske machines aayenge"/> :
            machines.length===0 ? <EmptyState text="No machines" sub="mes_machines mein is line ke machines nahi mile"/> : (
            <div style={{display:"flex", flexDirection:"column", gap:6, maxHeight:460, overflowY:"auto"}}>
              {machines.map(m => (
                <div key={m.id} style={{display:"flex", alignItems:"center", gap:8, padding:"8px 10px", borderRadius:8, border:"1px solid #eef2f7"}}>
                  <div style={{flex:1, minWidth:0}}>
                    <div style={{fontSize:13, fontWeight:600, color:"#0f172a", overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap"}}>{m.machineName}</div>
                    <div style={{fontSize:11, color:"#94a3b8", fontFamily:"monospace"}}>{m.machineNo}</div>
                  </div>
                  <input value={codeEdit[m.id]??""} disabled={readOnly}
                         onChange={e=>setCodeEdit(s=>({...s,[m.id]:e.target.value}))}
                         onBlur={()=>saveCode(m)} onKeyDown={e=>{ if(e.key==="Enter"){ e.target.blur(); } }}
                         placeholder="SS_01"
                         style={{width:82, padding:"6px 8px", border:"1px solid #cbd5e1", borderRadius:6, fontSize:13, fontFamily:"monospace", fontWeight:700, textAlign:"center",
                                 color: m.stationCode?"#15803d":"#0f172a", background: m.stationCode?"#f0fdf4":"#fff"}}/>
                </div>
              ))}
            </div>
          )}
        </Card>

        {/* RIGHT — paired stations + PYs */}
        <Card>
          <div style={{fontWeight:700, fontSize:15, color:"#0f172a", marginBottom:10}}>2&#65039;&#8419; Paired stations ({groups.length})</div>
          {groups.length===0 ? <EmptyState icon="🔗" text="Koi station nahi" sub="Left se machines pe code do — same code = paired station"/> : (
            <div style={{display:"flex", flexWrap:"wrap", gap:8, marginBottom:14}}>
              {groups.map(g => {
                const sel=station===g.stationCode;
                return (
                  <button key={g.stationCode} onClick={()=>pickStation(g.stationCode)}
                          title={g.machines.map(x=>x.lineName).join(", ")}
                          style={{padding:"8px 12px", borderRadius:9, cursor:"pointer", fontSize:13, fontWeight:800, fontFamily:"monospace",
                                  border:`1.5px solid ${sel?"#16a34a":"#e2e8f0"}`, background:sel?"#f0fdf4":"#fff", color:sel?"#15803d":"#0f172a"}}>
                    {g.stationCode} <span style={{fontSize:10, color:"#64748b"}}>&times;{g.machines.length}</span>
                  </button>
                );
              })}
            </div>
          )}
          {station && (
            <div>
              <div style={{fontSize:12, color:"#64748b", marginBottom:8}}>
                <b>{station}</b> = {(curGroup?.machines||[]).map(x=>x.lineName).join(" · ")}
              </div>
              <div style={{display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:8}}>
                <div style={{fontSize:13, fontWeight:700, color:"#334155"}}>PY list ({pys.length}) — sabhi paired lines pe same</div>
                {!readOnly && <button onClick={()=>setPyOpen(true)} style={_psPrimary}>+ PY</button>}
              </div>
              {pys.length===0 ? <div style={{color:"#94a3b8", fontSize:13}}>Koi PY nahi — <b>+ PY</b> se add karo.</div> : (
                <div style={{display:"flex", flexDirection:"column", gap:6}}>
                  {pys.map(p => {
                    const open = expandPy===p.id;
                    return (
                    <div key={p.id} style={{borderRadius:8, border:"1px solid #eef2f7", overflow:"hidden"}}>
                      <div onClick={()=>togglePy(p)} style={{display:"flex", alignItems:"center", gap:8, padding:"8px 10px", cursor:"pointer", background: open?"#f8fafc":"#fff"}}>
                        <span style={{fontSize:12, color:"#94a3b8", width:10}}>{open?"▾":"▸"}</span>
                        <div style={{flex:1}}>
                          <div style={{fontSize:13, fontWeight:600, color:"#0f172a"}}>{p.pyName}</div>
                          <div style={{fontSize:11, color:"#64748b", fontFamily:"monospace"}}>
                            {p.dBit?`bit ${p.dBit}`:""}{p.sensingBits?` · X ${p.sensingBits}`:""} · {p.registerCount}-reg · default {p.desiredValue}{p.desiredValue2!=null?`/${p.desiredValue2}`:""}
                          </div>
                        </div>
                        {!readOnly && <button onClick={e=>{e.stopPropagation(); delPy(p.id);}} style={{..._psMini, color:"#dc2626"}}>Delete</button>}
                      </div>
                      {open && (
                        <div style={{padding:"8px 10px", borderTop:"1px solid #eef2f7", background:"#fbfdff"}}>
                          <div style={{fontSize:11, color:"#64748b", marginBottom:6}}>Per-line output — blank = default ({p.desiredValue}{p.desiredValue2!=null?`/${p.desiredValue2}`:""}). Checkbox off = is line pe PY nahi.</div>
                          {pyMachs.length===0 ? <div style={{fontSize:12, color:"#94a3b8"}}>Loading…</div> :
                            pyMachs.map(mm => (
                              <div key={mm.machineId} style={{display:"flex", alignItems:"center", gap:8, padding:"5px 0", opacity: mm.enabled?1:.45}}>
                                <input type="checkbox" checked={mm.enabled} disabled={readOnly}
                                       onChange={e=>{ setPyMachs(ms=>ms.map(x=>x.machineId===mm.machineId?{...x,enabled:e.target.checked}:x)); saveMach(p.id, mm.machineId); }}/>
                                <span style={{flex:1, fontSize:12, fontWeight:700, fontFamily:"monospace", color:"#0f172a"}}>{mm.lineName}</span>
                                <input value={mm.desiredValue??""} disabled={readOnly||!mm.enabled} placeholder={p.desiredValue==null?"—":String(p.desiredValue)} title="Desired value (blank = default)"
                                       onChange={e=>setPyMachs(ms=>ms.map(x=>x.machineId===mm.machineId?{...x,desiredValue:e.target.value}:x))}
                                       onBlur={()=>saveMach(p.id, mm.machineId)}
                                       style={{width:50, padding:"4px 6px", border:`1px solid ${mm.hasOverride?"#f59e0b":"#cbd5e1"}`, borderRadius:6, fontSize:12, textAlign:"center", fontFamily:"monospace"}}/>
                                {Number(p.registerCount)===2 && (
                                  <input value={mm.desiredValue2??""} disabled={readOnly||!mm.enabled} placeholder={p.desiredValue2==null?"—":String(p.desiredValue2)} title="Desired value 2"
                                         onChange={e=>setPyMachs(ms=>ms.map(x=>x.machineId===mm.machineId?{...x,desiredValue2:e.target.value}:x))}
                                         onBlur={()=>saveMach(p.id, mm.machineId)}
                                         style={{width:50, padding:"4px 6px", border:`1px solid ${mm.hasOverride?"#f59e0b":"#cbd5e1"}`, borderRadius:6, fontSize:12, textAlign:"center", fontFamily:"monospace"}}/>
                                )}
                                {mm.hasOverride && !readOnly && <button title="Reset to default" onClick={()=>resetMach(p.id, mm.machineId)} style={{..._psMini, padding:"2px 7px"}}>&#8634;</button>}
                              </div>
                            ))}
                        </div>
                      )}
                    </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </Card>
      </div>

      <Modal open={pyOpen} onClose={()=>setPyOpen(false)} title={`Add PY — station ${station||""}`}>
        <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:10}}>
          <label style={{gridColumn:"1 / -1"}}><div style={_psLbl}>PY name *</div>
            <input value={pyForm.py_name} onChange={e=>setPyForm(f=>({...f,py_name:e.target.value}))} autoFocus style={_psInp} placeholder="e.g. Rivet present check"/></label>
          <label><div style={_psLbl}>Output D-bit</div><input value={pyForm.d_bit} onChange={e=>setPyForm(f=>({...f,d_bit:e.target.value}))} style={_psInp} placeholder="D400"/></label>
          <label><div style={_psLbl}>Sensing X-bit(s)</div><input value={pyForm.sensing_bits} onChange={e=>setPyForm(f=>({...f,sensing_bits:e.target.value}))} style={_psInp} placeholder="X15"/></label>
          <label><div style={_psLbl}>Registers</div>
            <select value={pyForm.register_count} onChange={e=>setPyForm(f=>({...f,register_count:e.target.value}))} style={_psInp}>
              <option value={1}>1 register (0/1/2)</option><option value={2}>2 registers (0/1/2/3)</option></select></label>
          <label><div style={_psLbl}>Desired value</div><input type="number" value={pyForm.desired_value} onChange={e=>setPyForm(f=>({...f,desired_value:e.target.value}))} style={_psInp} placeholder="0/1/2"/></label>
          {Number(pyForm.register_count)===2 && <label><div style={_psLbl}>Desired value 2</div><input type="number" value={pyForm.desired_value_2} onChange={e=>setPyForm(f=>({...f,desired_value_2:e.target.value}))} style={_psInp} placeholder="0/1/2/3"/></label>}
        </div>
        <ModalActions>
          <button onClick={()=>setPyOpen(false)} style={_psGhost}>Cancel</button>
          <button onClick={addPy} style={_psPrimary}>Add PY</button>
        </ModalActions>
      </Modal>
    </div>
  );
}

// ── Shared: SheetJS loader ────────────────────────────────────
const loadSheetJS = () => new Promise((res,rej) => {
  if (window.XLSX) return res();
  const s=document.createElement("script");
  s.src="/xlsx.full.min.js";  // local copy for air-gapped LAN
  s.onload=res; s.onerror=rej; document.head.appendChild(s);
});

// ── Shared: parse any sheet from uploaded file ────────────────
async function parseSheet(file, sheetName) {
  await loadSheetJS();
  const buf = await file.arrayBuffer();
  const wb  = window.XLSX.read(buf, {type:"array"});
  if (!wb.SheetNames.includes(sheetName)) return null;
  const ws  = wb.Sheets[sheetName];
  return window.XLSX.utils.sheet_to_json(ws, {defval:""});
}

// ── Shared: Excel import button with column mapping ───────────
function ExcelImportBtn({ label, sheetName, expectedCols, onParsed, disabled }) {
  const fileRef = useRef(null);
  const [colMap,    setColMap]    = useState({});  // {systemCol: excelCol}
  const [colModal,  setColModal]  = useState(false);
  const [tempMap,   setTempMap]   = useState({});
  const [headers,   setHeaders]   = useState([]);  // actual excel headers found

  const handleFile = async e => {
    const file = e.target.files[0]; if (!file) return;
    e.target.value="";
    await loadSheetJS();
    const buf = await file.arrayBuffer();
    const wb  = window.XLSX.read(buf, {type:"array"});
    if (!wb.SheetNames.includes(sheetName)) {
      alert(`Sheet "${sheetName}" not found in this file.\nAvailable: ${wb.SheetNames.join(", ")}`);
      return;
    }
    const ws   = wb.Sheets[sheetName];
    const rows = window.XLSX.utils.sheet_to_json(ws, {defval:""});
    if (!rows.length) { alert("Sheet is empty"); return; }
    const hdrs = Object.keys(rows[0]);
    setHeaders(hdrs);
    onParsed(rows, colMap);
  };

  const activeMap = {...colMap};
  expectedCols.forEach(c => { if (!activeMap[c]) activeMap[c]=c; });
  const hasCustom = expectedCols.some(c=>colMap[c]&&colMap[c]!==c);

  return (
    <>
      <div style={{display:"flex",gap:8,alignItems:"center",flexWrap:"wrap"}}>
        <Btn variant="primary" size="sm" onClick={()=>fileRef.current?.click()} disabled={disabled}>
          📥 {label}
        </Btn>
        <Btn size="sm" onClick={()=>{setTempMap({...activeMap});setColModal(true);}}>
          🗂 Column Map {hasCustom&&"⚠"}
        </Btn>
        <input ref={fileRef} type="file" accept=".xlsx,.xls" style={{display:"none"}} onChange={handleFile}/>
        {hasCustom && <span style={{fontSize:10,color:"#d97706",background:"rgba(217,119,6,.08)",border:"1px solid rgba(217,119,6,.2)",borderRadius:99,padding:"3px 10px"}}>Custom mapping active</span>}
      </div>

      <Modal open={colModal} onClose={()=>setColModal(false)} title={`Column Mapping — ${sheetName}`} wide>
        <p style={{fontSize:12,color:"#64748b",marginBottom:16}}>
          Map your Excel column headers to system fields. If your Excel uses different column names, enter them here.
          {headers.length>0 && <span style={{display:"block",marginTop:6,color:"#94a3b8"}}>Detected headers: <b>{headers.join(", ")}</b></span>}
        </p>
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:14}}>
          {expectedCols.map(sysCol=>(
            <FF key={sysCol} label={`System field: ${sysCol}`}>
              <Input
                value={tempMap[sysCol]||sysCol}
                onChange={e=>setTempMap(m=>({...m,[sysCol]:e.target.value}))}
                placeholder={sysCol}
              />
            </FF>
          ))}
        </div>
        <ModalActions>
          <Btn onClick={()=>{setTempMap({});setColModal(false);}}>Reset to Default</Btn>
          <Btn onClick={()=>setColModal(false)}>Cancel</Btn>
          <Btn variant="primary" onClick={()=>{setColMap(tempMap);setColModal(false);}}>Save Mapping</Btn>
        </ModalActions>
      </Modal>
    </>
  );
}

// ─── MATRIX TAB ───────────────────────────────────────────────
function MatrixAssignModal({ modelName, items, lines, zones, rules, token, toast, onClose, onReload }) {
  // Lines that already have ALL bits of this model assigned
  const pyNos = [...new Set(items.map(a => a.pyNo))];
  const assignedLineIds = new Set(
    lines.filter(l =>
      pyNos.every(pyNo =>
        rules.some(r => r.line_id === l.id && r.poka_yoke_no === pyNo)
      )
    ).map(l => l.id)
  );
  const [selectedLine, setSelectedLine] = useState("");
  const [saving,       setSaving]       = useState(false);
  const [removing,     setRemoving]     = useState(false);

  const byZone = zones.map(z => ({ zone: z, zlines: lines.filter(l => l.zone_id === z.id) }))
                      .filter(g => g.zlines.length > 0);

  const assign = async () => {
    if (!selectedLine) { toast("Select a line first", "err"); return; }
    setSaving(true);
    const lineId = parseInt(selectedLine);
    const rulesPayload = items.map(a => ({
      poka_yoke_no:   a.pyNo,
      poka_yoke_name: a.pyName || a.pyNo,
      side:           a.typeSide || "ALL",
      model:          "ALL",   // "ALL" so rules always appear regardless of current PLC model name
      bit:            a.dBit || "",
      value:          a.desiredValue ?? 1,
      machine_name:   a.machineFixture || "",
      sheet_name:     "",
      alert_level:    "WARNING",
      is_active:      true,
    }));
    try {
      const res = await api.post(`/api/poka-yoke/rules/${lineId}/bulk`, { rules: rulesPayload }, token);
      toast(`✓ ${res.inserted} rules assigned${res.skipped > 0 ? `, ${res.skipped} already existed` : ""}`);
      onReload(); onClose();
    } catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  const remove = async () => {
    if (!selectedLine) { toast("Select a line first", "err"); return; }
    if (!confirm(`Remove all "${modelName}" poka-yoke rules from this line?`)) return;
    setRemoving(true);
    try {
      const pyNos = [...new Set(items.map(a => a.pyNo))];
      await api.post(`/api/poka-yoke/rules/${selectedLine}/bulk-delete`, { poka_yoke_nos: pyNos }, token);
      toast("Rules removed ✓"); onReload(); onClose();
    } catch (e) { toast(e.message, "err"); }
    finally { setRemoving(false); }
  };

  const selLineAlreadyAssigned = selectedLine ? assignedLineIds.has(parseInt(selectedLine)) : false;

  return (
    <Modal open onClose={onClose} title={`Assign "${modelName}" to Line`} wide>
      <p style={{ fontSize: 12, color: "#64748b", marginBottom: 14 }}>
        This will create <b>{items.length} poka-yoke rules</b> on the selected line for all bits in this model configuration.
      </p>

      {/* Already assigned indicator */}
      {assignedLineIds.size > 0 && (
        <div style={{ padding: "8px 12px", borderRadius: 8, background: "rgba(22,163,74,.06)", border: "1px solid rgba(22,163,74,.2)", fontSize: 12, color: "#16a34a", marginBottom: 14, fontWeight: 600 }}>
          ✓ Already fully assigned on: {lines.filter(l => assignedLineIds.has(l.id)).map(l => l.line_name).join(", ")}
        </div>
      )}

      {/* Bit summary */}
      <div style={{ marginBottom: 16, padding: "10px 14px", background: "#f8fafc", borderRadius: 8, border: "1px solid #e2e8f0" }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: "#64748b", textTransform: "uppercase", letterSpacing: ".06em", marginBottom: 8 }}>
          {items.length} Checks to Assign
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {items.map((a, i) => (
            <span key={i} title={`${a.pyNo}: ${a.pyName}`}
              style={{ display: "inline-flex", alignItems: "center", borderRadius: 99, border: "1px solid #e2e8f0", overflow: "hidden", fontFamily: "monospace", fontSize: 11, fontWeight: 700 }}>
              <span style={{ padding: "3px 8px", background: "#f8fafc", color: "#334155" }}>{a.dBit || "—"}</span>
              <span style={{ padding: "3px 7px", background: a.desiredValue == 1 ? "rgba(22,163,74,.1)" : "rgba(220,38,38,.1)", color: a.desiredValue == 1 ? "#16a34a" : "#dc2626" }}>{a.desiredValue ?? 1}</span>
            </span>
          ))}
        </div>
      </div>

      {/* Line selector grouped by zone */}
      <div style={{ marginBottom: 16 }}>
        <label style={{ fontSize: 11, fontWeight: 700, color: "#64748b", textTransform: "uppercase", letterSpacing: ".06em", display: "block", marginBottom: 8 }}>
          Select Production Line *
        </label>
        <div style={{ maxHeight: 240, overflowY: "auto", border: "1px solid #e2e8f0", borderRadius: 8, padding: 4 }}>
          {byZone.length === 0
            ? <div style={{ padding: 20, textAlign: "center", color: "#94a3b8", fontSize: 12 }}>No lines configured</div>
            : byZone.map(({ zone, zlines }) => (
              <div key={zone.id} style={{ marginBottom: 4 }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: zone.color || "#64748b", letterSpacing: ".08em", textTransform: "uppercase", padding: "6px 10px 4px", background: zone.color ? `${zone.color}14` : "#f8fafc", borderRadius: 6, borderLeft: zone.color ? `3px solid ${zone.color}` : "none" }}>{zone.zone_name}</div>
                {zlines.map(l => {
                  const isAssigned = assignedLineIds.has(l.id);
                  const isSelected = String(l.id) === String(selectedLine);
                  return (
                    <label key={l.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "7px 12px", borderRadius: 6, cursor: "pointer", background: isSelected ? "rgba(30,64,175,.06)" : "transparent", border: `1px solid ${isSelected ? "rgba(30,64,175,.25)" : "transparent"}`, margin: "2px 0" }}>
                      <input type="radio" name="assignLine" value={l.id} checked={isSelected} onChange={() => setSelectedLine(String(l.id))} style={{ accentColor: "#1e40af" }} />
                      <span style={{ fontSize: 13, fontWeight: isSelected ? 600 : 400, color: isSelected ? "#1e40af" : "#0f172a", flex: 1 }}>{l.line_name}</span>
                      {isAssigned && <span style={{ fontSize: 10, color: "#16a34a", fontWeight: 700, background: "rgba(22,163,74,.1)", padding: "2px 8px", borderRadius: 99 }}>✓ Assigned</span>}
                    </label>
                  );
                })}
              </div>
            ))
          }
        </div>
      </div>

      <ModalActions>
        <Btn onClick={onClose}>Cancel</Btn>
        {selLineAlreadyAssigned && (
          <Btn variant="danger" onClick={remove} disabled={removing}>{removing ? "Removing…" : "Remove from Line"}</Btn>
        )}
        <Btn variant="primary" onClick={assign} disabled={saving || !selectedLine}>
          {saving ? "Assigning…" : `Assign ${items.length} Rules`}
        </Btn>
      </ModalActions>
    </Modal>
  );
}

function PYMatrix({ assignments, events, lines, zones, rules, toast, token, onReload, readOnly = false }) {
  const [search,       setSearch]       = useState("");
  const [filterType,   setFilterType]   = useState("");
  const [filterSeries, setFilterSeries] = useState("");
  const [selected,     setSelected]     = useState(null);
  const [assignModel,  setAssignModel]  = useState(null); // { modelName, items }

  const uniqueTypes  = [...new Set(assignments.map(a=>a.modelType).filter(Boolean))].sort();
  const uniqueSeries = [...new Set(assignments.map(a=>a.modelSeries).filter(Boolean))].sort();

  const filtered = assignments.filter(a => {
    const s=search.toLowerCase();
    return (!search||Object.values(a).some(v=>String(v).toLowerCase().includes(s)))
      && (!filterType||a.modelType===filterType)
      && (!filterSeries||a.modelSeries===filterSeries);
  });

  // group by modelName — exactly like original server.js MatrixTab
  const grouped = {};
  filtered.forEach(a => {
    if (!grouped[a.modelName]) grouped[a.modelName]=[];
    grouped[a.modelName].push(a);
  });

  const crits = events.filter(e=>e.alert_level==="CRITICAL");
  const warns = events.filter(e=>e.alert_level==="WARNING");

  const bitValBg  = v=>v==0?"rgba(220,38,38,.1)":v==1?"rgba(22,163,74,.1)":"rgba(30,64,175,.1)";
  const bitValClr = v=>v==0?"#dc2626":v==1?"#16a34a":"#1e40af";
  const sideBg    = s=>s==="LH"?"rgba(30,64,175,.1)":s==="RH"?"rgba(22,163,74,.1)":"#f1f5f9";
  const sideClr   = s=>s==="LH"?"#1e40af":s==="RH"?"#16a34a":"#64748b";

  const ackEvent = async id => {
    try { await api.post(`/api/poka-yoke/events/${id}/acknowledge`,{},token); toast("Acknowledged"); onReload(); }
    catch(e) { toast(e.message,"err"); }
  };

  // Detail view — like original MatrixTab detail
  if (selected) {
    const items = assignments.filter(a=>a.modelName===selected);
    return (
      <div>
        <div style={{display:"flex",alignItems:"center",gap:12,background:"#fff",borderRadius:10,padding:"14px 18px",marginBottom:16,border:"1px solid #e2e8f0"}}>
          <button onClick={()=>setSelected(null)} style={{background:"none",border:"none",color:"#1e40af",cursor:"pointer",fontWeight:600,fontSize:13}}>← Back to Matrix</button>
          <span style={{fontWeight:700,fontSize:15,color:"#0f172a",flex:1}}>{selected}</span>
          {items[0]&&<span style={{fontSize:12,color:"#94a3b8"}}>{items[0].modelType} | Series: {items[0].modelSeries} | {items[0].oldModelNo}</span>}
        </div>
        <Card>
          <div style={{overflowX:"auto"}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
              <thead><tr>
                {["#","PY No","Poka Yoke Description","Side","D Bit (PLC)","Desired Value","Machine / Fixture"].map(h=>(
                  <th key={h} style={{padding:"9px 12px",textAlign:"left",fontSize:9,fontWeight:700,letterSpacing:".08em",textTransform:"uppercase",color:"#64748b",borderBottom:"2px solid #e2e8f0",whiteSpace:"nowrap"}}>{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {items.map((a,i)=>(
                  <tr key={a.id||i} style={{borderBottom:"1px solid #f1f5f9"}}>
                    <td style={{padding:"8px 12px",color:"#94a3b8",fontSize:11}}>{i+1}</td>
                    <td style={{padding:"8px 12px",fontFamily:"monospace",fontWeight:700,color:"#1e40af",fontSize:11}}>{a.pyNo}</td>
                    <td style={{padding:"8px 12px",color:"#0f172a"}}>{a.pyName}</td>
                    <td style={{padding:"8px 12px"}}><span style={{padding:"2px 8px",borderRadius:99,fontSize:10,fontWeight:700,background:sideBg(a.typeSide),color:sideClr(a.typeSide)}}>{a.typeSide||"—"}</span></td>
                    <td style={{padding:"8px 12px",fontFamily:"monospace",fontWeight:700,color:"#7c3aed"}}>{a.dBit||"—"}</td>
                    <td style={{padding:"8px 12px",textAlign:"center"}}>
                      {a.desiredValue!=null?<span style={{padding:"3px 10px",borderRadius:99,fontSize:11,fontWeight:700,background:bitValBg(a.desiredValue),color:bitValClr(a.desiredValue)}}>{a.desiredValue}</span>:"—"}
                    </td>
                    <td style={{padding:"8px 12px",fontSize:11,color:"#64748b"}}>{a.machineFixture||"—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{padding:"8px 14px",fontSize:11,color:"#94a3b8",borderTop:"1px solid #f1f5f9"}}>{items.length} poka yoke checks for this model</div>
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div>
      {/* Stats — identical to original */}
      <div style={{display:"flex",gap:14,marginBottom:18,flexWrap:"wrap"}}>
        {[
          {label:"Models",       val:Object.keys(grouped).length},
          {label:"Total Checks", val:filtered.length},
          {label:"Unique PY",    val:[...new Set(filtered.map(a=>a.pyNo))].length},
          {label:"Bits Used",    val:[...new Set(filtered.map(a=>a.dBit).filter(Boolean))].length},
        ].map(({label,val})=>(
          <div key={label} style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:10,padding:"12px 18px",minWidth:110}}>
            <div style={{fontSize:11,color:"#64748b",fontWeight:600,textTransform:"uppercase",letterSpacing:".06em"}}>{label}</div>
            <div style={{fontSize:26,fontWeight:800,color:"#1e40af"}}>{val}</div>
          </div>
        ))}
      </div>

      {/* Unacked events */}
      {events.length>0&&(
        <Card style={{marginBottom:18}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10}}>
            <span style={{fontSize:13,fontWeight:700,color:"#0f172a"}}>Unacknowledged Events <span style={{fontSize:11,background:"rgba(30,64,175,.1)",color:"#1e40af",padding:"2px 8px",borderRadius:4,marginLeft:6}}>{events.length}</span></span>
            {!readOnly && events.length>1 && (
              <Btn size="sm" variant="danger" onClick={()=>[...new Set(events.map(e=>e.line_id))].forEach(id=>api.post(`/api/poka-yoke/events/${id}/acknowledge-all`,{},token).then(onReload))}>Acknowledge All</Btn>
            )}
          </div>
          {events.map(e=>(
            <div key={e.id} style={{display:"flex",alignItems:"center",gap:12,padding:"8px 0",borderBottom:"1px solid #f1f5f9"}}>
              <span style={{fontSize:18}}>{e.alert_level==="CRITICAL"?"🚨":"⚠️"}</span>
              <div style={{flex:1}}>
                <div style={{fontWeight:600,fontSize:13,color:"#0f172a"}}>{e.rule_name||e.poka_yoke_name||"Event"}</div>
                <div style={{fontSize:11,color:"#94a3b8",marginTop:2}}>{e.line_name} · {new Date(e.detected_at).toLocaleString("en-IN")}</div>
              </div>
              <Pill label={e.alert_level} color={e.alert_level==="CRITICAL"?"red":"amber"}/>
              {!readOnly && <Btn size="sm" onClick={()=>ackEvent(e.id)}>Acknowledge</Btn>}
            </div>
          ))}
        </Card>
      )}

      {/* Filters + title — identical to original */}
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-end",marginBottom:14,flexWrap:"wrap",gap:10}}>
        <div style={{fontWeight:700,fontSize:15,color:"#0f172a"}}>Poka Yoke Matrix</div>
        <div style={{display:"flex",gap:10,flexWrap:"wrap",alignItems:"center"}}>
          <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search model / PY / bit..."
            style={{...inputStyle,width:220,padding:"8px 12px"}}/>
          <select value={filterType} onChange={e=>setFilterType(e.target.value)} style={{...inputStyle,padding:"8px 10px",fontSize:12,width:160}}>
            <option value="">All Types</option>{uniqueTypes.map(t=><option key={t}>{t}</option>)}
          </select>
          <select value={filterSeries} onChange={e=>setFilterSeries(e.target.value)} style={{...inputStyle,padding:"8px 10px",fontSize:12,width:140}}>
            <option value="">All Series</option>{uniqueSeries.map(s=><option key={s}>{s}</option>)}
          </select>
        </div>
      </div>

      {/* Matrix cards — identical to original */}
      {Object.keys(grouped).length===0 ? (
        <Card><EmptyState text="No data" sub="Import from Excel in Config, Poka Yoke Master, or Model Master tabs"/></Card>
      ) : (
        <div style={{display:"flex",flexDirection:"column",gap:8}}>
          {Object.entries(grouped).map(([modelName,items])=>(
            <Card key={modelName} style={{padding:0,overflow:"hidden"}}>
              <div style={{display:"flex",alignItems:"center",gap:12,padding:"11px 16px",background:"#f8fafc",borderBottom:"1px solid #e2e8f0"}}>
                <div onClick={()=>setSelected(modelName)} style={{flex:1,cursor:"pointer"}}>
                  <div style={{fontWeight:600,fontSize:14,color:"#0f172a"}}>{modelName}</div>
                  <div style={{fontSize:11,color:"#94a3b8",marginTop:2,display:"flex",gap:8}}>
                    {items[0]?.modelType&&<span style={{background:items[0].modelType?.includes("4")?"rgba(30,64,175,.1)":"rgba(124,58,237,.1)",color:items[0].modelType?.includes("4")?"#1e40af":"#7c3aed",padding:"1px 8px",borderRadius:99,fontSize:10,fontWeight:700}}>{items[0].modelType}</span>}
                    {items[0]?.modelSeries&&<span style={{background:"#f1f5f9",color:"#64748b",padding:"1px 8px",borderRadius:99,fontSize:10,fontWeight:700}}>{items[0].modelSeries}</span>}
                    {items[0]?.oldModelNo&&<span style={{color:"#94a3b8",fontSize:11}}>{items[0].oldModelNo}</span>}
                  </div>
                </div>
                <span style={{background:"#f1f5f9",color:"#64748b",padding:"2px 10px",borderRadius:99,fontSize:11,fontWeight:700}}>{items.length} checks</span>
                {!readOnly && (
                  <Btn size="sm" variant="primary" onClick={e=>{e.stopPropagation();setAssignModel({modelName,items});}}>🏭 Assign to Line</Btn>
                )}
                <span onClick={()=>setSelected(modelName)} style={{fontSize:12,color:"#1e40af",fontWeight:600,cursor:"pointer"}}>View Details →</span>
              </div>
              {/* Bit pills */}
              <div style={{padding:"10px 16px",display:"flex",flexWrap:"wrap",gap:6}}>
                {items.map((a,i)=>{
                  const bit=a.dBit||"—"; const val=a.desiredValue??1;
                  return (
                    <span key={i} title={`${a.pyNo}: ${a.pyName}`}
                      style={{display:"inline-flex",alignItems:"center",borderRadius:99,border:"1px solid #e2e8f0",overflow:"hidden",fontFamily:"monospace",fontSize:11,fontWeight:700}}>
                      <span style={{padding:"3px 8px",background:"#f8fafc",color:"#334155"}}>{bit}</span>
                      <span style={{padding:"3px 7px",background:bitValBg(val),color:bitValClr(val)}}>{val}</span>
                    </span>
                  );
                })}
              </div>
            </Card>
          ))}
        </div>
      )}

      {assignModel && (
        <MatrixAssignModal
          modelName={assignModel.modelName}
          items={assignModel.items}
          lines={lines||[]}
          zones={zones||[]}
          rules={rules||[]}
          token={token}
          toast={toast}
          onClose={()=>setAssignModel(null)}
          onReload={onReload}
        />
      )}
    </div>
  );
}

// ─── CONFIG TAB — Set desired output per model × pokayoke ────
function PYConfig({ assignments, pyMaster, models, lines, zones, toast, token, onReload, readOnly = false }) {
  const [search,      setSearch]     = useState("");
  const [filterModel, setFilterModel]= useState("");
  const [filterType,  setFilterType] = useState("");
  const [saving,      setSaving]     = useState({});  // {assignmentId: true}
  const [importing,   setImporting]  = useState(false);
  const [impResult,   setImpResult]  = useState(null);
  const [addBitFor,   setAddBitFor]  = useState(null);  // assignment row to add extra bit for
  const [newBit,      setNewBit]     = useState({dBit:"",desiredValue:"",register:""});

  const uniqueModels    =[...new Set(assignments.map(a=>a.modelName).filter(Boolean))].sort();
  const uniqueModelTypes=[...new Set(assignments.map(a=>a.modelType).filter(Boolean))].sort();
  const filtered=assignments.filter(a=>{
    const s=search.toLowerCase();
    return(!search||Object.values(a).some(v=>String(v).toLowerCase().includes(s)))&&(!filterModel||a.modelName===filterModel)&&(!filterType||a.modelType===filterType);
  });

  // Build PY lookup for bit/register display
  const pyLookup={};
  pyMaster.forEach(p=>{pyLookup[p.pyNo]=p;});

  // Build Model lookup (by modelName) so we can show bit # on each group card.
  // Index both the raw name and the legacy-prefix-stripped variant so old
  // assignments (with "TYPE-SERIES:" prefix) still match cleaned master rows.
  const stripPrefix = (s) => (s||"").replace(/^TYPE-SERIES:\s*/i,"");
  const modelLookup={};
  (models||[]).forEach(m=>{
    if (!m.modelName) return;
    modelLookup[m.modelName]       = m;
    modelLookup[stripPrefix(m.modelName)] = m;
  });
  const findModel = (name) => modelLookup[name] || modelLookup[stripPrefix(name)] || null;

  const sideBg =s=>s==="LH"?"rgba(30,64,175,.1)":s==="RH"?"rgba(22,163,74,.1)":"#f1f5f9";
  const sideClr=s=>s==="LH"?"#1e40af":s==="RH"?"#16a34a":"#64748b";
  const valBg  =v=>v==0?"rgba(22,163,74,.1)":v==1?"rgba(220,38,38,.08)":"rgba(30,64,175,.08)";
  const valClr =v=>v==0?"#16a34a":v==1?"#dc2626":"#1e40af";

  // Output options per register count
  const OUTPUT_OPTS = {
    1: [
      { code: 0, label: "PASS" },
      { code: 1, label: "OFF"  },
      { code: 2, label: "ON"   },
    ],
    2: [
      { code: 0, label: "PASS"     },
      { code: 1, label: "OFF, OFF" },
      { code: 2, label: "OFF, ON"  },
      { code: 3, label: "ON, OFF"  },
      { code: 4, label: "ON, ON"   },
    ],
  };
  const optsFor = (cnt) => OUTPUT_OPTS[cnt === 2 ? 2 : 1];

  // Inline-patch a single assignment (desired_bit[_2] or desired_value[_2]).
  const patchAssignment = async (a, patch) => {
    setSaving(s=>({...s,[a.id]:true}));
    try {
      await api.patch(`/api/poka-yoke/assignments/${a.id}`, patch, token);
      toast("Saved ✓");
      onReload();
    } catch(e) { toast(e.message,"err"); }
    finally   { setSaving(s=>{const n={...s};delete n[a.id];return n;}); }
  };
  const updateBit   = (a, key, raw) => {
    const v = raw === "" ? null : parseInt(raw);
    if (raw !== "" && (isNaN(v) || v < 0)) { toast("Enter a positive bit number","err"); return; }
    patchAssignment(a, { [key]: v });
  };
  const updateValue = (a, key, raw) => {
    const v = raw === "" ? null : parseInt(raw);
    patchAssignment(a, { [key]: v });
  };

  // Import
  const FINAL_COLS=["Poka Yoke No","Poka Yoke Name","Type Side","Model Type","Model Name","Type2","Old Model No","Model","D bit From PLC","Desired Value (0/1/2)","Machine/Fixture"];
  const doImport=async(rows,colMap)=>{
    if(!rows||!rows.length){toast("No rows found","err");return;}
    setImporting(true); setImpResult(null);
    try{
      const res=await api.post("/api/poka-yoke/import/bulk",{sheet:"final seat",rows,col_map:colMap},token);
      setImpResult(res); toast(`✓ ${res.inserted} assignments imported`,res.ok?"ok":"info");
      onReload();
    }catch(e){toast(e.message,"err");}
    finally{setImporting(false);}
  };

  const del=async id=>{
    if(!confirm("Delete this assignment?")) return;
    try{ await api.delete(`/api/poka-yoke/assignments/${id}`,token); toast("Deleted"); onReload(); }
    catch(e){toast(e.message,"err");}
  };

  const delModel=async(modelName, items)=>{
    if(!confirm(`"${modelName}" ke saare ${items.length} poka-yoke assignments delete karne hain?`)) return;
    try{
      await Promise.all(items.map(a=>api.delete(`/api/poka-yoke/assignments/${a.id}`,token)));
      toast(`${items.length} assignments deleted for ${modelName}`);
      onReload();
    }catch(e){toast(e.message,"err");}
  };

  // Add an extra desirable-bit row for the same PY+model.
  const openAddBit=(a)=>{
    setAddBitFor(a);
    setNewBit({desiredBit:"", desiredValue:""});
  };
  const saveAddBit=async()=>{
    if(newBit.desiredBit===""){ toast("Desirable bit daalo","err"); return; }
    const a=addBitFor;
    try{
      await api.post("/api/poka-yoke/assignments/",{
        pyNo:a.pyNo, pyName:a.pyName, typeSide:a.typeSide, modelType:a.modelType,
        modelName:a.modelName, type2:a.modelType, oldModelNo:a.oldModelNo||"",
        modelSeries:a.modelSeries||"",
        dBit: a.dBit || (pyLookup[a.pyNo]||{}).dBit || "",
        desiredBit:   parseInt(newBit.desiredBit),
        desiredValue: newBit.desiredValue!=="" ? parseInt(newBit.desiredValue) : null,
        machineFixture:a.machineFixture||"",
      },token);
      toast("Added ✓"); setAddBitFor(null); onReload();
    }catch(e){toast(e.message,"err");}
  };

  // Group by model_id (bit-stable) — not model_name which can be renamed.
  // Each group carries its own bitNumber + live modelName from backend.
  const grouped={};
  filtered.forEach(a=>{
    const key = a.modelId != null ? `id:${a.modelId}`
              : a.bitNumber != null ? `bit:${a.bitNumber}`
              : `name:${a.modelName || "Unknown"}`;
    if(!grouped[key]) grouped[key]=[];
    grouped[key].push(a);
  });

  return (
    <div>
      {/* Stats */}
      <div style={{display:"flex",gap:14,marginBottom:18,flexWrap:"wrap"}}>
        {[
          {label:"Total Assignments",val:assignments.length,color:"#1e40af"},
          {label:"Models Configured",val:uniqueModels.length,color:"#16a34a"},
          {label:"Unique PY",        val:[...new Set(assignments.map(a=>a.pyNo))].length,color:"#7c3aed"},
        ].map(({label,val,color})=>(
          <div key={label} style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:10,padding:"12px 18px",minWidth:120}}>
            <div style={{fontSize:11,color:"#64748b",fontWeight:600,textTransform:"uppercase",letterSpacing:".06em"}}>{label}</div>
            <div style={{fontSize:26,fontWeight:800,color}}>{val}</div>
          </div>
        ))}
      </div>

      {/* Import Card — admin only */}
      {!readOnly && (
        <Card style={{marginBottom:18}}>
          <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:12,paddingBottom:12,borderBottom:"1px solid #f1f5f9"}}>
            <span style={{fontSize:20}}>📥</span>
            <div>
              <div style={{fontSize:14,fontWeight:700,color:"#0f172a"}}>Import from Excel — "final seat" sheet</div>
              <div style={{fontSize:11,color:"#94a3b8",fontFamily:"monospace"}}>Poka Yoke No | Poka Yoke Name | Type Side | Model Type | Model Name | Type2 | Old Model No | Model | D bit From PLC | Desired Value (0/1/2) | Machine/Fixture</div>
            </div>
          </div>
          <ExcelImportBtn label={importing?"Importing…":"Upload Excel (final seat)"} sheetName="final seat" expectedCols={FINAL_COLS} onParsed={doImport} disabled={importing}/>
          {impResult&&(
            <div style={{marginTop:12,padding:"10px 14px",borderRadius:8,background:impResult.ok?"rgba(22,163,74,.06)":"rgba(220,38,38,.06)",border:`1px solid ${impResult.ok?"rgba(22,163,74,.2)":"rgba(220,38,38,.2)"}`,fontSize:12}}>
              {impResult.ok?<span style={{color:"#16a34a",fontWeight:600}}>✓ Imported {impResult.inserted} assignments{impResult.skipped>0?`, skipped ${impResult.skipped}`:""}</span>:<span style={{color:"#dc2626",fontWeight:600}}>✗ {impResult.errors?.[0]||"Import failed"}</span>}
            </div>
          )}
        </Card>
      )}

      {/* Filters */}
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-end",marginBottom:14,gap:10,flexWrap:"wrap"}}>
        <div style={{fontWeight:700,fontSize:15,color:"#0f172a"}}>Config — Set Expected Output</div>
        <div style={{display:"flex",gap:10,flexWrap:"wrap",alignItems:"center"}}>
          <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search..." style={{...inputStyle,width:180,padding:"8px 12px"}}/>
          <select value={filterType} onChange={e=>setFilterType(e.target.value)} style={{...inputStyle,padding:"8px 10px",fontSize:12,width:150}}>
            <option value="">All Types</option>{uniqueModelTypes.map(t=><option key={t}>{t}</option>)}
          </select>
          <select value={filterModel} onChange={e=>setFilterModel(e.target.value)} style={{...inputStyle,padding:"8px 10px",fontSize:12,width:220}}>
            <option value="">All Models</option>{uniqueModels.map(m=><option key={m}>{m}</option>)}
          </select>
        </div>
      </div>

      {/* Grouped by model */}
      {Object.keys(grouped).length===0?<Card><EmptyState text="No assignments" sub="Import Excel from above or add PY in PY Master tab first"/></Card>:(
        <div style={{display:"flex",flexDirection:"column",gap:12}}>
          {Object.entries(grouped).map(([key,items])=>{
            const first    = items[0];
            // Source bit # and name from the assignment row itself (comes
            // straight from the backend, joined live with the master). Falls
            // back to models[] prop lookup if something's missing.
            const bit      = first?.bitNumber ?? findModel(first?.modelName)?.bitNumber ?? null;
            const nameRaw  = first?.modelName || findModel(first?.modelName)?.modelName || "";
            const name     = String(nameRaw).replace(/^TYPE-SERIES:\s*/i,"");
            return (
              <Card key={key} style={{padding:0,overflow:"hidden"}}>
                <div style={{padding:"10px 16px",background:"#f8fafc",borderBottom:"1px solid #e2e8f0",display:"flex",alignItems:"center",gap:12,flexWrap:"wrap"}}>
                  <div style={{flex:1,display:"flex",alignItems:"center",gap:10,flexWrap:"wrap"}}>
                    {bit != null && (
                      <span style={{display:"inline-flex",alignItems:"center",justifyContent:"center",minWidth:40,height:32,padding:"0 10px",borderRadius:8,background:"linear-gradient(135deg,#7c3aed,#6d28d9)",color:"#fff",fontWeight:800,fontSize:13,fontFamily:"monospace"}}>
                        #{bit}
                      </span>
                    )}
                    <div>
                      <div style={{fontWeight:800,fontSize:14,color:"#0f172a",letterSpacing:".02em"}}>
                        MODEL No. — {bit ?? "—"}
                      </div>
                      <div style={{fontSize:11,color:"#475569",fontWeight:600,marginTop:2,fontFamily:"monospace"}}>
                        {name || "—"}
                      </div>
                      <div style={{fontSize:10,color:"#94a3b8",display:"flex",gap:8,marginTop:3,flexWrap:"wrap"}}>
                        {first?.modelType&&<span style={{background:first.modelType?.includes("4")?"rgba(30,64,175,.1)":"rgba(124,58,237,.1)",color:first.modelType?.includes("4")?"#1e40af":"#7c3aed",padding:"1px 8px",borderRadius:99,fontWeight:700,whiteSpace:"nowrap"}}>{first.modelType}</span>}
                        {first?.modelSeries&&<span style={{background:"#f1f5f9",color:"#64748b",padding:"1px 8px",borderRadius:99,fontWeight:700}}>{first.modelSeries}</span>}
                      </div>
                    </div>
                  </div>
                  <span style={{background:"#f1f5f9",color:"#64748b",padding:"2px 10px",borderRadius:99,fontSize:11,fontWeight:700}}>{items.length} checks</span>
                  {!readOnly && (
                    <Btn size="sm" variant="danger" onClick={()=>delModel(first?.modelName,items)}>Delete Model</Btn>
                  )}
                </div>
                <div style={{overflowX:"auto"}}>
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                    <thead><tr>
                      {["#","PY No","Description","Side","Bit","Register Output","Desirable Bit","Output 1","Output 2", ...(readOnly ? [] : [""])].map(h=>(
                        <th key={h} style={{padding:"7px 10px",textAlign:"left",fontSize:8,fontWeight:700,letterSpacing:".08em",textTransform:"uppercase",color:"#94a3b8",borderBottom:"1px solid #e2e8f0",whiteSpace:"nowrap"}}>{h}</th>
                      ))}
                    </tr></thead>
                    <tbody>
                      {items.map((a,i)=>{
                        const pyInfo = pyLookup[a.pyNo] || {};
                        const regCnt = pyInfo.registerCount === 2 ? 2 : 1;
                        const busy   = !!saving[a.id];

                        // Dropdown options: 0=PASS, 1=OFF, 2=ON.  null = not set.
                        const outBg  = v => v===2 ? "rgba(22,163,74,.1)"
                                          : v===1 ? "rgba(220,38,38,.08)"
                                          : v===0 ? "rgba(30,64,175,.08)"
                                          : "#fff";
                        const outClr = v => v===2 ? "#16a34a"
                                          : v===1 ? "#dc2626"
                                          : v===0 ? "#1e40af"
                                          : "#64748b";

                        const VALUE_LABEL = { 0: "PASS", 1: "OFF", 2: "ON" };
                        const outCell = (val, valKey, enabled) => (
                          <td style={{padding:"6px 10px"}}>
                            {!enabled ? (
                              <span style={{color:"#e2e8f0",fontSize:11,fontWeight:700}}>—</span>
                            ) : readOnly ? (
                              // Read-only pill — same colour scheme as the editable
                              // select but no dropdown affordance.
                              val == null ? (
                                <span style={{color:"#cbd5e1",fontSize:11,fontWeight:700}}>—</span>
                              ) : (
                                <span style={{
                                  display:"inline-block", padding:"3px 12px", borderRadius:99,
                                  fontSize:11, fontWeight:700, minWidth:60, textAlign:"center",
                                  background: outBg(val), color: outClr(val),
                                  border:"1px solid #e2e8f0",
                                }}>{VALUE_LABEL[val] ?? val}</span>
                              )
                            ) : (
                              <select
                                value={val!=null ? String(val) : ""}
                                disabled={busy}
                                onChange={e => updateValue(a, valKey, e.target.value)}
                                style={{
                                  padding:"3px 10px",fontSize:11,borderRadius:6,border:"1px solid #e2e8f0",
                                  fontWeight:700,minWidth:90,cursor:"pointer",
                                  background: outBg(val),
                                  color:      outClr(val),
                                }}
                              >
                                <option value="">— Set —</option>
                                <option value="0">PASS</option>
                                <option value="1">OFF</option>
                                <option value="2">ON</option>
                              </select>
                            )}
                          </td>
                        );

                        return (
                          <tr key={a.id||i} style={{borderBottom:"1px solid #f8fafc"}}>
                            <td style={{padding:"6px 10px",color:"#cbd5e1",fontSize:10}}>{i+1}</td>
                            <td style={{padding:"6px 10px",fontFamily:"monospace",fontWeight:700,color:"#1e40af",fontSize:10}}>{a.pyNo}</td>
                            <td style={{padding:"6px 10px",fontSize:11,maxWidth:200,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={a.pyName}>{a.pyName}</td>
                            <td style={{padding:"6px 10px",whiteSpace:"nowrap"}}><span style={{display:"inline-block",padding:"1px 6px",borderRadius:99,fontSize:9,fontWeight:700,background:sideBg(a.typeSide),color:sideClr(a.typeSide),whiteSpace:"nowrap"}}>{a.typeSide||"—"}</span></td>
                            <td style={{padding:"6px 10px",whiteSpace:"nowrap"}}>{
                              (() => {
                                const RE = /(?:D|R|M|L|F|T|C|S)\d+|(?:X|Y|W|B)[0-9A-F]+/gi;
                                const toks = String(a.dBit||pyInfo.dBit||"").toUpperCase().match(RE) || [];
                                if (!toks.length) return <span style={{color:"#cbd5e1"}}>—</span>;
                                return toks.map((b,j)=>(
                                  <span key={j} style={{display:"inline-block",padding:"1px 7px",borderRadius:99,fontSize:10,fontWeight:700,background:"rgba(124,58,237,.1)",color:"#7c3aed",fontFamily:"monospace",marginRight:4}}>{b}</span>
                                ));
                              })()
                            }</td>
                            <td style={{padding:"6px 10px",whiteSpace:"nowrap"}}>
                              <span style={{display:"inline-block",padding:"2px 9px",borderRadius:99,fontSize:10,fontWeight:700,background:regCnt===2?"rgba(234,88,12,.1)":"rgba(30,64,175,.1)",color:regCnt===2?"#c2410c":"#1e40af",whiteSpace:"nowrap"}}>
                                {regCnt} Register{regCnt===2?"s":""} Output
                              </span>
                            </td>

                            {/* Desirable Bit — single column for both reg counts */}
                            <td style={{padding:"6px 10px"}}>
                              {readOnly ? (
                                a.desiredBit != null ? (
                                  <span style={{
                                    display:"inline-block", width:72, padding:"3px 8px",
                                    fontSize:11, borderRadius:6, border:"1px solid #e2e8f0",
                                    fontWeight:700, fontFamily:"monospace",
                                    color:"#7c3aed", textAlign:"center",
                                    background:"rgba(124,58,237,.06)",
                                  }}>{a.desiredBit}</span>
                                ) : (
                                  <span style={{color:"#cbd5e1",fontSize:11,fontWeight:700}}>—</span>
                                )
                              ) : (
                                <input
                                  type="number" min="0"
                                  defaultValue={a.desiredBit!=null ? a.desiredBit : ""}
                                  disabled={busy}
                                  onBlur={e => {
                                    const raw = e.target.value.trim();
                                    const curr = a.desiredBit!=null ? String(a.desiredBit) : "";
                                    if (raw !== curr) updateBit(a, "desired_bit", raw);
                                  }}
                                  onKeyDown={e => { if (e.key === "Enter") e.target.blur(); }}
                                  placeholder="bit #"
                                  style={{
                                    width:72,padding:"3px 8px",fontSize:11,borderRadius:6,
                                    border:"1px solid #e2e8f0",fontWeight:700,fontFamily:"monospace",
                                    color:a.desiredBit!=null?"#7c3aed":"#94a3b8",textAlign:"center",
                                  }}
                                />
                              )}
                            </td>

                            {/* Output 1 — first register (always present, editable for admin) */}
                            {outCell(a.desiredValue,  "desired_value",   true)}
                            {/* Output 2 — second register (only for 2-register PYs) */}
                            {outCell(a.desiredValue2, "desired_value_2", regCnt === 2)}

                            {!readOnly && (
                              <td style={{padding:"6px 10px"}}><div style={{display:"flex",gap:4}}>
                                <Btn size="sm" variant="danger" onClick={()=>del(a.id)} style={{fontSize:9,padding:"2px 8px"}}>X</Btn>
                              </div></td>
                            )}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Card>
            );
          })}
        </div>
      )}
      <div style={{padding:"10px 0",fontSize:11,color:"#94a3b8",textAlign:"center"}}>Showing {filtered.length} of {assignments.length} total assignments</div>

      {/* Add Extra Bit Modal */}
      <Modal open={!!addBitFor} onClose={()=>setAddBitFor(null)} title="Add Another Desirable Bit for this Model">
        {addBitFor&&(
          <div>
            <div style={{background:"#f0f9ff",border:"1px solid #bae6fd",borderRadius:8,padding:"10px 14px",marginBottom:16,fontSize:12}}>
              <div style={{fontWeight:700,color:"#0c4a6e"}}>{addBitFor.pyNo} — {addBitFor.pyName}</div>
              <div style={{fontSize:11,color:"#64748b",marginTop:2}}>Model: {addBitFor.modelName}</div>
              <div style={{fontSize:11,color:"#64748b"}}>PLC Register: <b style={{fontFamily:"monospace",color:"#7c3aed"}}>{addBitFor.dBit || (pyLookup[addBitFor.pyNo]||{}).dBit || "—"}</b></div>
            </div>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:14}}>
              <FF label="Desirable Bit *">
                <Input
                  type="number" min="0"
                  value={newBit.desiredBit}
                  onChange={e=>setNewBit(f=>({...f,desiredBit:e.target.value}))}
                  placeholder="0, 1, 2, 3..."
                  style={{fontFamily:"monospace",fontWeight:700,color:"#7c3aed"}}
                />
              </FF>
              <FF label="Desirable Output *">
                <Select value={newBit.desiredValue} onChange={e=>setNewBit(f=>({...f,desiredValue:e.target.value}))}>
                  <option value="">— Set —</option>
                  <option value="0">OFF</option>
                  <option value="1">ON</option>
                </Select>
              </FF>
            </div>
            <ModalActions>
              <Btn onClick={()=>setAddBitFor(null)}>Cancel</Btn>
              <Btn variant="primary" onClick={saveAddBit} disabled={newBit.desiredBit===""}>Add</Btn>
            </ModalActions>
          </div>
        )}
      </Modal>
    </div>
  );
}

// ─── POKA YOKE MASTER TAB ─────────────────────────────────────
// ─── PY MASTER TAB ─────────────────────────────────────────────
// TYPE  : 4 Way / 6 Way
// SIDE  : depends on TYPE (4 Way → LH/RH/OTR; 6 Way → LH/RH/Otr LH/Otr RH)
// Each combination maps uniquely to one Model Master `type` value.
// ─── Reusable Excel template + import buttons (Model Master / PY Master) ──
// Given a `routePrefix` like "/api/poka-yoke/master" or "/api/poka-yoke/models",
// renders a "Download Template" button and an "Import Excel" file picker that
// hits {prefix}/template (GET, blob) and {prefix}/import (POST multipart).
// Reports the {inserted, skipped, errors[]} summary via toast.
function ExcelTools({ routePrefix, label, fileBaseName, token, toast, onDone }) {
  const [busy, setBusy] = useState(false);
  const fileRef = useRef();

  const downloadTemplate = async () => {
    setBusy(true);
    try {
      const r = await fetch(`${routePrefix}/template`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${fileBaseName}_template.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch(e) { toast(`Template download failed: ${e.message}`, "err"); }
    finally   { setBusy(false); }
  };

  const onFile = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await fetch(`${routePrefix}/import`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
      const errs = j.errors?.length ? ` · ${j.errors.length} error(s)` : "";
      toast(`Imported ${j.inserted} ${label}, skipped ${j.skipped}${errs}`, j.inserted>0?"ok":"err");
      if (j.errors?.length) {
        console.warn(`[${label} import] errors:`, j.errors);
      }
      if (onDone) onDone();
    } catch(err) { toast(`Import failed: ${err.message}`, "err"); }
    finally     {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <>
      <Btn onClick={downloadTemplate} disabled={busy} title="Download Excel template">
        ⬇ Template
      </Btn>
      <Btn onClick={()=>fileRef.current?.click()} disabled={busy} title="Upload filled Excel to bulk-import">
        {busy ? "Working…" : "📤 Import Excel"}
      </Btn>
      <input type="file" accept=".xlsx,.xls" ref={fileRef}
             style={{display:"none"}} onChange={onFile}/>
    </>
  );
}


function PYMaster({ pyMaster, models, zones = [], toast, token, onReload, readOnly = false }) {
  const TYPES = ["4 Way", "6 Way"];
  const sidesFor = (type) => {
    if (type === "4 Way") return ["ALL", "LH", "RH", "OTR"];
    if (type === "6 Way") return ["ALL", "LH", "RH", "Otr LH", "Otr RH"];
    return [];
  };
  // Map (TYPE + SIDE) → list of model.type strings stored in Model Master.
  // ALL on a given TYPE returns every variant for that TYPE.
  const modelTypesFor = (type, side) => {
    if (!type || !side) return [];
    if (type === "4 Way") {
      if (side === "ALL") return ["4 Way Inr LH", "4 Way Inr RH", "4 Way OTR"];
      if (side === "LH")  return ["4 Way Inr LH"];
      if (side === "RH")  return ["4 Way Inr RH"];
      if (side === "OTR") return ["4 Way OTR"];
    }
    if (type === "6 Way") {
      if (side === "ALL")    return ["6 Way Inr LH", "6 Way Inr RH", "6 Way Otr LH", "6 Way Otr RH"];
      if (side === "LH")     return ["6 Way Inr LH"];
      if (side === "RH")     return ["6 Way Inr RH"];
      if (side === "Otr LH") return ["6 Way Otr LH"];
      if (side === "Otr RH") return ["6 Way Otr RH"];
    }
    return [];
  };

  // Output code → label reference (shown in modal, also used in Config tab).
  const OUTPUT_MAP = {
    1: [
      { code: 0, label: "PASS" },
      { code: 1, label: "OFF"  },
      { code: 2, label: "ON"   },
    ],
    2: [
      { code: 0, label: "PASS"     },
      { code: 1, label: "OFF, OFF" },
      { code: 2, label: "OFF, ON"  },
      { code: 3, label: "ON, OFF"  },
      { code: 4, label: "ON, ON"   },
    ],
  };

  const [modal,   setModal]   = useState(false);
  const [editing, setEditing] = useState(null);
  const [saving,  setSaving]  = useState(false);
  const [search,  setSearch]  = useState("");

  const EMPTY = {
    description:   "",
    modelType:     "",   // 4 Way / 6 Way
    typeSide:      "",   // LH / RH / OTR / Otr LH / Otr RH
    dBit:          "",   // D400 — now also acts as functional primary key
    sensingBits:   "",   // X-bit(s), used by sensor health check
    zoneId:        "",   // FK → mes_zones.id
    registerCount: 1,
    assignedModelIds: [],
  };
  const [form, setForm] = useState(EMPTY);

  const openAdd = () => { setForm(EMPTY); setEditing(null); setModal(true); };
  const openEdit = (p) => {
    setForm({
      description:   p.description || "",
      modelType:     p.modelType || "",
      typeSide:      p.typeSide  || "",
      dBit:          p.dBit || p.register || "",
      sensingBits:   p.sensingBits || "",
      zoneId:        p.zoneId ?? "",
      registerCount: p.registerCount || 1,
      assignedModelIds: Array.isArray(p.assignedModelIds) ? p.assignedModelIds : [],
    });
    setEditing(p); setModal(true);
  };

  // Eligible models for current type+side, sorted by bit number.
  // When side = "ALL", merges all variants for the chosen Type.
  const eligibleModels = useMemo(() => {
    const wantedList = modelTypesFor(form.modelType, form.typeSide);
    if (wantedList.length === 0) return [];
    const wanted = new Set(wantedList);
    return models
      .filter(m => wanted.has(m.type || ""))
      .sort((a,b) => (a.bitNumber ?? 9999) - (b.bitNumber ?? 9999));
  }, [form.modelType, form.typeSide, models]);

  // When type/side changes, drop any selected IDs that no longer apply.
  useEffect(() => {
    setForm(f => {
      const keep = new Set(eligibleModels.map(m=>m.id));
      const pruned = (f.assignedModelIds || []).filter(id => keep.has(id));
      return pruned.length === (f.assignedModelIds || []).length ? f
        : { ...f, assignedModelIds: pruned };
    });
    // eslint-disable-next-line
  }, [eligibleModels.map(m=>m.id).join(",")]);

  const toggleModel = (id) => {
    setForm(f => ({ ...f,
      assignedModelIds: f.assignedModelIds.includes(id)
        ? f.assignedModelIds.filter(x=>x!==id)
        : [...f.assignedModelIds, id],
    }));
  };

  const save = async () => {
    if (!form.description.trim()){ toast("Description required","err");  return; }
    if (!form.modelType)         { toast("Type required","err");         return; }
    if (!form.typeSide)          { toast("Side required","err");         return; }
    if (!form.dBit.trim())       { toast("Output D-Bit required","err"); return; }
    if (!form.zoneId)            { toast("Zone required","err");         return; }
    if (![1,2].includes(form.registerCount)) { toast("Register count must be 1 or 2","err"); return; }

    // Normalize the register fields — extract every register token regardless
    // of separator (comma, space, mixed, none).  Accepts Mitsubishi types:
    //   D/R/M/L/F/T/C/S → decimal address (D400, M100)
    //   X/Y/W/B         → hex address     (X1E, Y10)
    const REG_RE = /(?:D|R|M|L|F|T|C|S)\d+|(?:X|Y|W|B)[0-9A-F]+/gi;
    const tokens = (form.dBit || "").toUpperCase().match(REG_RE) || [];
    const normalizedBit = tokens.join(",");
    if (!tokens.length) { toast("At least one register (e.g. D400 / X1E) required","err"); return; }

    const sensTokens = (form.sensingBits || "").toUpperCase().match(REG_RE) || [];
    const normalizedSens = sensTokens.join(",");

    const payload = {
      description:      form.description.trim(),
      modelType:        form.modelType,
      typeSide:         form.typeSide,
      dBit:             normalizedBit,
      register:         normalizedBit,
      sensingBits:      normalizedSens || null,
      zoneId:           form.zoneId ? Number(form.zoneId) : null,
      registerCount:    form.registerCount,
      assignedModelIds: form.assignedModelIds,
    };

    setSaving(true);
    try {
      if (editing) {
        await api.put(`/api/poka-yoke/master/${editing.id}`, payload, token);
        toast("Updated ✓");
      } else {
        await api.post("/api/poka-yoke/master/", payload, token);
        toast(`Added ✓ — ${form.assignedModelIds.length} model${form.assignedModelIds.length===1?"":"s"} linked`);
      }
      setModal(false); onReload();
    } catch(e) { toast(e.message, "err"); }
    finally   { setSaving(false); }
  };

  const del = async id => {
    if (!confirm("Delete this poka yoke?")) return;
    try { await api.delete(`/api/poka-yoke/master/${id}`, token); toast("Deleted"); onReload(); }
    catch(e) { toast(e.message, "err"); }
  };

  const filtered = pyMaster.filter(p =>
    !search || Object.values(p).some(v => String(v).toLowerCase().includes(search.toLowerCase()))
  );

  return (
    <div>
      {/* Stats */}
      <div style={{display:"flex",gap:14,marginBottom:18,flexWrap:"wrap"}}>
        {[
          { label:"Total PY",   val: pyMaster.length,                                           color:"#1e40af" },
          { label:"4 Way",      val: pyMaster.filter(p=>p.modelType==="4 Way").length,          color:"#1e40af" },
          { label:"6 Way",      val: pyMaster.filter(p=>p.modelType==="6 Way").length,          color:"#7c3aed" },
          { label:"2-Register", val: pyMaster.filter(p=>p.registerCount===2).length,            color:"#16a34a" },
        ].map(({label,val,color})=>(
          <div key={label} style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:10,padding:"12px 18px",minWidth:110}}>
            <div style={{fontSize:11,color:"#64748b",fontWeight:600,textTransform:"uppercase",letterSpacing:".06em"}}>{label}</div>
            <div style={{fontSize:26,fontWeight:800,color}}>{val}</div>
          </div>
        ))}
      </div>

      {/* Header */}
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:14,gap:10,flexWrap:"wrap"}}>
        <div style={{fontWeight:700,fontSize:15,color:"#0f172a"}}>Poka Yoke Master</div>
        <div style={{display:"flex",gap:10,alignItems:"center",flexWrap:"wrap"}}>
          <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search..." style={{...inputStyle,width:200,padding:"8px 12px"}}/>
          {!readOnly && (
            <>
              <ExcelTools routePrefix="/api/poka-yoke/master"
                          label="PYs" fileBaseName="py_master"
                          token={token} toast={toast} onDone={onReload}/>
              <Btn variant="primary" onClick={openAdd}>+ Add Poka Yoke</Btn>
            </>
          )}
        </div>
      </div>

      <Card>
        {filtered.length===0 ? (
          <EmptyState text="No poka yokes" sub={readOnly ? "No poka-yoke checks configured yet." : 'Click "+ Add Poka Yoke" to create one.'}/>
        ) : (
          <div style={{overflowX:"auto"}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
              <thead><tr>
                {["S.No","Zone #","Description","Type","Side","Output D-Bit","Sensing","Reg Count","Models", ...(readOnly ? [] : ["Actions"])].map(h=>(
                  <th key={h} style={{padding:"9px 12px",textAlign:"left",fontSize:9,fontWeight:700,letterSpacing:".08em",textTransform:"uppercase",color:"#64748b",borderBottom:"2px solid #e2e8f0",whiteSpace:"nowrap"}}>{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {filtered.map((p,i)=>{
                  const cnt = (p.assignedModelIds || []).length;
                  const zoneLabel = p.zoneName
                    ? `${p.zoneName} #${p.seqInZone || "?"}`
                    : "— (no zone)";
                  return (
                    <tr key={p.id} style={{borderBottom:"1px solid #f1f5f9"}}>
                      <td style={{padding:"8px 12px",color:"#94a3b8",fontSize:11,fontWeight:600}}>{i+1}</td>
                      <td style={{padding:"8px 12px",fontWeight:700,color:p.zoneName?"#1e40af":"#94a3b8",fontSize:11,whiteSpace:"nowrap"}} title={p.zoneCode}>{zoneLabel}</td>
                      <td style={{padding:"8px 12px",color:"#0f172a",maxWidth:260,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={p.description}>{p.description}</td>
                      <td style={{padding:"8px 12px",whiteSpace:"nowrap"}}>
                        <span style={{display:"inline-block",padding:"2px 9px",borderRadius:99,fontSize:10,fontWeight:700,background:p.modelType==="4 Way"?"rgba(30,64,175,.1)":"rgba(124,58,237,.1)",color:p.modelType==="4 Way"?"#1e40af":"#7c3aed",whiteSpace:"nowrap"}}>{p.modelType||"—"}</span>
                      </td>
                      <td style={{padding:"8px 12px",whiteSpace:"nowrap"}}>
                        <span style={{display:"inline-block",padding:"2px 9px",borderRadius:99,fontSize:10,fontWeight:700,background:"rgba(22,163,74,.1)",color:"#16a34a",whiteSpace:"nowrap"}}>{p.typeSide||"—"}</span>
                      </td>
                      <td style={{padding:"8px 12px",fontFamily:"monospace",fontWeight:700,color:"#7c3aed",fontSize:11}}>{p.dBit || p.register || "—"}</td>
                      <td style={{padding:"8px 12px",fontFamily:"monospace",fontWeight:700,color:p.sensingBits?"#0891b2":"#cbd5e1",fontSize:11}}>{p.sensingBits || "—"}</td>
                      <td style={{padding:"8px 12px"}}>
                        <span style={{display:"inline-block",padding:"2px 9px",borderRadius:99,fontSize:10,fontWeight:700,background:"#f1f5f9",color:"#475569"}}>{p.registerCount || 1} reg</span>
                      </td>
                      <td style={{padding:"8px 12px"}}>
                        <span style={{display:"inline-block",padding:"2px 9px",borderRadius:99,fontSize:10,fontWeight:700,background:cnt?"rgba(234,88,12,.1)":"#f1f5f9",color:cnt?"#c2410c":"#94a3b8"}}>{cnt} model{cnt===1?"":"s"}</span>
                      </td>
                      {!readOnly && (
                        <td style={{padding:"8px 12px"}}>
                          <div style={{display:"flex",gap:6}}>
                            <Btn size="sm" onClick={()=>openEdit(p)}>Edit</Btn>
                            <Btn size="sm" variant="danger" onClick={()=>del(p.id)}>Delete</Btn>
                          </div>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div style={{padding:"8px 14px",fontSize:11,color:"#94a3b8",borderTop:"1px solid #f1f5f9"}}>Showing {filtered.length} of {pyMaster.length}</div>
          </div>
        )}
      </Card>

      {/* ── Add / Edit Modal ── */}
      <Modal open={modal} onClose={()=>setModal(false)} title={editing?"Edit Poka Yoke":"Add New Poka Yoke"} wide>
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16}}>
          <FF label="Zone *">
            <Select value={form.zoneId} onChange={e=>setForm(f=>({...f,zoneId:e.target.value}))}>
              <option value="">— Select Zone —</option>
              {zones.map(z=>(
                <option key={z.id} value={z.id}>{z.zone_name} ({z.zone_code})</option>
              ))}
            </Select>
            <div style={{fontSize:10,color:"#94a3b8",marginTop:4}}>
              PY zone ke andar auto-numbered — Seat Slider #1…#25, Press Shop #1…#3, etc.
            </div>
          </FF>
          <FF label="Output D-Bit *">
            <Input
              value={form.dBit}
              onChange={e=>setForm(f=>({...f,dBit:e.target.value.toUpperCase()}))}
              placeholder="D400   OR   X1E,X1F   OR   D413,D414,D415"
              style={{fontFamily:"monospace",fontWeight:700,color:"#7c3aed"}}
            />
            <div style={{fontSize:10,color:"#94a3b8",marginTop:4}}>
              D-register (bypass ke liye) — isi se PY uniquely identify hoti hai. Supports D/R/M/L/F/T/C/S (decimal), X/Y/W/B (hex), comma-separated.
            </div>
          </FF>
          <FF label="Description *" style={{gridColumn:"1/-1"}}>
            <Input value={form.description} onChange={e=>setForm(f=>({...f,description:e.target.value}))} placeholder="Harness brkt pop rivet operation miss"/>
          </FF>
          <FF label="Sensing X-Bit(s)" style={{gridColumn:"1/-1"}}>
            <Input
              value={form.sensingBits}
              onChange={e=>setForm(f=>({...f,sensingBits:e.target.value.toUpperCase()}))}
              placeholder="X15   OR   X21,X22   (sensor health check — blank = skip health test)"
              style={{fontFamily:"monospace",fontWeight:700,color:"#0891b2"}}
            />
            <div style={{fontSize:10,color:"#94a3b8",marginTop:4}}>
              Sensor ka X-bit input — har 15 cycles mein liveness check hogi (stuck bit → HEALTH ✗ + alert).
            </div>
          </FF>

          <FF label="Type *">
            <Select value={form.modelType} onChange={e=>setForm(f=>({...f,modelType:e.target.value,typeSide:""}))}>
              <option value="">— Select Type —</option>
              {TYPES.map(t=><option key={t}>{t}</option>)}
            </Select>
          </FF>
          <FF label="Side *">
            <Select value={form.typeSide} onChange={e=>setForm(f=>({...f,typeSide:e.target.value}))} disabled={!form.modelType}>
              <option value="">— Select Side —</option>
              {sidesFor(form.modelType).map(s=><option key={s}>{s}</option>)}
            </Select>
          </FF>

          {/* Register Count */}
          <FF label="Register Output *" style={{gridColumn:"1/-1"}}>
            <div style={{display:"flex",gap:10,flexWrap:"wrap"}}>
              {[1,2].map(n=>{
                const on = form.registerCount === n;
                return (
                  <button key={n} type="button" onClick={()=>setForm(f=>({...f,registerCount:n}))} style={{
                    flex:1,minWidth:220,padding:"10px 14px",borderRadius:8,cursor:"pointer",
                    border: on ? "1.5px solid #1e40af" : "1px solid #e2e8f0",
                    background: on ? "rgba(30,64,175,.08)" : "#fff",
                    color: on ? "#1e40af" : "#475569",fontWeight:700,fontSize:12,textAlign:"left",
                  }}>
                    <div style={{fontSize:13,fontWeight:800}}>{on?"● ":"○ "}{n} Register{n===2?"s":""}</div>
                    <div style={{fontSize:10,color:"#64748b",marginTop:2,fontWeight:600,fontFamily:"monospace"}}>
                      {OUTPUT_MAP[n].map(o=>`${o.code}=${o.label}`).join("  ")}
                    </div>
                  </button>
                );
              })}
            </div>
          </FF>
        </div>

        {/* ── Applicable Models (checkbox list, filtered by type+side) ── */}
        <div style={{marginTop:16,padding:"14px 16px",background:"#fff7ed",borderRadius:10,border:"1px solid #fed7aa"}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:10,gap:10,flexWrap:"wrap"}}>
            <div>
              <div style={{fontSize:11,fontWeight:700,color:"#c2410c",letterSpacing:".08em",textTransform:"uppercase"}}>
                Applicable Models ({form.assignedModelIds.length} selected)
              </div>
              <div style={{fontSize:10,color:"#9a3412",marginTop:2}}>
                {form.modelType && form.typeSide
                  ? `Showing Model Master entries where Type ∈ ${modelTypesFor(form.modelType, form.typeSide).map(t=>`"${t}"`).join(", ")}`
                  : "Select TYPE and SIDE above to see matching models."}
              </div>
            </div>
            {eligibleModels.length > 0 && (
              <div style={{display:"flex",gap:6}}>
                <Btn size="sm" onClick={()=>setForm(f=>({...f,assignedModelIds:eligibleModels.map(m=>m.id)}))}>Select All</Btn>
                <Btn size="sm" onClick={()=>setForm(f=>({...f,assignedModelIds:[]}))}>Clear</Btn>
              </div>
            )}
          </div>

          {(!form.modelType || !form.typeSide) ? (
            <div style={{fontSize:11,color:"#9a3412",fontStyle:"italic",padding:"8px 4px"}}>
              Select Type + Side first.
            </div>
          ) : eligibleModels.length === 0 ? (
            <div style={{fontSize:11,color:"#9a3412",fontStyle:"italic",padding:"8px 4px"}}>
              No models in Model Master match <b>{modelTypesFor(form.modelType, form.typeSide).join(" / ")}</b>. Add some in the Model Master tab first.
            </div>
          ) : (
            <div style={{maxHeight:240,overflowY:"auto",display:"flex",flexDirection:"column",gap:4}}>
              {eligibleModels.map(m=>{
                const checked = form.assignedModelIds.includes(m.id);
                const name = (m.modelName||"").replace(/^TYPE-SERIES:\s*/i,"");
                return (
                  <label key={m.id} style={{
                    display:"flex",alignItems:"center",gap:10,padding:"8px 10px",
                    background: checked ? "rgba(194,65,12,.08)" : "#fff",
                    border: `1px solid ${checked ? "rgba(194,65,12,.3)" : "#e2e8f0"}`,
                    borderRadius:6,cursor:"pointer",fontSize:12,
                  }}>
                    <input type="checkbox" checked={checked} onChange={()=>toggleModel(m.id)}/>
                    <span style={{display:"inline-flex",alignItems:"center",justifyContent:"center",minWidth:32,height:24,padding:"0 8px",borderRadius:6,background:"linear-gradient(135deg,#7c3aed,#6d28d9)",color:"#fff",fontWeight:800,fontSize:11,fontFamily:"monospace"}}>
                      #{m.bitNumber ?? "—"}
                    </span>
                    <span style={{fontFamily:"monospace",fontWeight:600,color:"#0f172a",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",flex:1}} title={name}>
                      {name}
                    </span>
                  </label>
                );
              })}
            </div>
          )}
        </div>

        <ModalActions>
          <Btn onClick={()=>setModal(false)}>Cancel</Btn>
          <Btn variant="primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : editing ? "Update" : "Add Poka Yoke"}
          </Btn>
        </ModalActions>
      </Modal>
    </div>
  );
}

// ─── MAIL CONFIG (top-level tab) ──────────────────────────────
// CRUD over mes_mail_config — per-kind (bypass / health / hourly) To + Cc
// lists.  Each row shows the stored value, the env fallback, and the
// effective value currently in use.  "Send Test" verifies the full chain
// (SMTP + addresses) without waiting for a real alert.
// Page is rendered in three places now:
//   1. Admin Panel → Maintenance section → "Mail Settings"     → kindFilter=["bypass","health"]
//   2. Admin Panel → Production  section → "Hourly Report Mail" → kindFilter=["hourly"]
//   3. Department Panel (read-only) — same kind filters depending on dept
// Without `kindFilter`, ALL kinds render (legacy single-tab behavior).
const BLANK_HOURLY_CFG = {
  line_id: "", is_active: true,
  to_addresses: "",         cc_addresses: "",
  zone_to_addresses: "",    zone_cc_addresses: "",
  section_to_addresses: "", section_cc_addresses: "",
};

export function MailConfigPage({ toast, kindFilter = null, readOnly = false }) {
  const { token } = useAuth();
  const [rows,    setRows]    = useState([]);
  const [drafts,  setDrafts]  = useState({});   // { key → dirty value }
  const [saving,  setSaving]  = useState(null); // currently-saving key
  const [testing, setTesting] = useState(null);

  const load = useCallback(async () => {
    try {
      const d = await api.get("/api/poka-yoke/mail-config/", token);
      setRows(Array.isArray(d) ? d : []);
      setDrafts({});
    } catch(e) { toast(e.message || "Load failed", "err"); }
  }, [token, toast]);

  useEffect(() => { load(); }, [load]);

  const save = async (key) => {
    setSaving(key);
    try {
      await api.put(`/api/poka-yoke/mail-config/${key}`,
        { value: drafts[key] ?? "" }, token);
      toast("Saved ✓", "ok");
      await load();
    } catch(e) { toast(e.message || "Save failed", "err"); }
    finally   { setSaving(null); }
  };

  const sendTest = async (key) => {
    setTesting(key);
    try {
      const d = await api.post(`/api/poka-yoke/mail-config/${key}/test`, {}, token);
      toast(`Test sent → To: ${d.to.join(", ")}${d.cc?.length?` | Cc: ${d.cc.join(", ")}`:""}`, "ok");
    } catch(e) { toast(e.message || "Test failed", "err"); }
    finally   { setTesting(null); }
  };

  // Group rows by kind (bypass / health / hourly) for nicer layout.
  // 2026-08-14 — the optional level segment must be stripped too, or
  // "hourly_zone_to" lands in a group called "hourly_zone" that is not in
  // KIND_ORDER and therefore renders nowhere: the setting would exist in the
  // API and be invisible in the UI.
  const groups = {};
  rows.forEach(r => {
    const kind = r.key.replace(/_(zone_|section_)?(to|cc)$/, "");
    (groups[kind] = groups[kind] || []).push(r);
  });
  // Which level of the chain a key belongs to, for the row label.
  const levelOf = (key) =>
    /_section_(to|cc)$/.test(key) ? "Section head"
      : /_zone_(to|cc)$/.test(key) ? "Zone head"
      : "Line";
  const KIND_LABELS = {
    bypass:  { label:"Poka-Yoke Bypass Alerts",
               desc:"Fires immediately on every new SENSOR_BYPASS event + 15-min digest." },
    health:  { label:"Sensor Health Fail Alerts",
               desc:"Fires once when a sensor stays stuck (>15 min without a natural toggle)." },
    hourly:  { label:"Hourly Slot Report",
               desc:"Automated per-shift slot summary: plan/actual/OK/NG/losses/bypasses." },
  };

  const KIND_ORDER = ["bypass","health","hourly"];
  // When AdminPanel renders this filtered (e.g. only ["hourly"] in the
  // Production section) we strip everything else so the user sees just
  // the relevant alert type.
  const visibleKinds = kindFilter
    ? KIND_ORDER.filter(k => kindFilter.includes(k) && groups[k])
    : KIND_ORDER.filter(k => groups[k]);

  return (
    <div className={readOnly ? "ap-readonly" : ""}>
      <fieldset disabled={readOnly} style={{border:0,padding:0,margin:0,minWidth:0}}>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:14,gap:10,flexWrap:"wrap"}}>
        <div>
          <div style={{fontWeight:700,fontSize:15,color:"#0f172a"}}>Mail Configuration</div>
          <div style={{fontSize:11,color:"#64748b",marginTop:2}}>
            Recipients for each alert type.  DB value wins over <code>.env</code>; blank falls back to env / legacy var.
            Comma-separated email lists supported.
          </div>
        </div>
      </div>

      {visibleKinds.map((kind) => {
        const kindRows = groups[kind];
        const meta = KIND_LABELS[kind] || { label: kind, desc: "" };
        return (
          <Card key={kind} style={{marginBottom:16}}>
            <div style={{padding:"14px 18px",borderBottom:"1px solid #f1f5f9"}}>
              <div style={{fontSize:14,fontWeight:700,color:"#0f172a"}}>{meta.label}</div>
              {meta.desc && <div style={{fontSize:11,color:"#64748b",marginTop:2}}>{meta.desc}</div>}
            </div>
            <div style={{padding:"8px 0"}}>
              <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                <thead><tr>
                  {["Field","Value (DB)","Env fallback","Effective","Actions"].map(h=>(
                    <th key={h} style={{padding:"9px 14px",textAlign:"left",fontSize:9,fontWeight:700,letterSpacing:".08em",textTransform:"uppercase",color:"#64748b",borderBottom:"1px solid #e2e8f0",whiteSpace:"nowrap"}}>{h}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {kindRows.map(r => {
                    const isDirty = drafts[r.key] !== undefined && drafts[r.key] !== (r.value||"");
                    const fieldName = r.key.endsWith("_to") ? "TO" :
                                     r.key.endsWith("_cc") ? "CC" : r.key;
                    const lvl = levelOf(r.key);
                    return (
                      <tr key={r.key} style={{borderBottom:"1px solid #f1f5f9"}}>
                        <td style={{padding:"10px 14px",fontWeight:700,color:"#1e40af",fontFamily:"monospace",whiteSpace:"nowrap"}}>
                          {fieldName}
                          {/* Which level of the line → zone → section chain this
                              row feeds.  Without it TO/CC appears three times
                              with nothing to tell the pairs apart. */}
                          <div style={{fontSize:9,fontWeight:700,color:"#64748b",fontFamily:"'Barlow',sans-serif",
                                        letterSpacing:".04em",textTransform:"uppercase",marginTop:2}}>
                            {lvl}
                          </div>
                        </td>
                        <td style={{padding:"10px 14px",minWidth:260}}>
                          <Input
                            value={drafts[r.key] ?? r.value ?? ""}
                            onChange={e => setDrafts(d => ({ ...d, [r.key]: e.target.value }))}
                            placeholder={r.env_value || r.legacy_value || "email1@x.com, email2@y.com"}
                            style={{fontFamily:"monospace",fontSize:12}}
                          />
                          {r.updated_at && (
                            <div style={{fontSize:10,color:"#94a3b8",marginTop:4}}>
                              Last edit: {new Date(r.updated_at).toLocaleString()} by {r.updated_by || "—"}
                            </div>
                          )}
                        </td>
                        <td style={{padding:"10px 14px",fontSize:11,color:"#64748b",maxWidth:220}}>
                          <div><code style={{fontSize:10,background:"#f1f5f9",padding:"1px 5px",borderRadius:3}}>{r.env_var}</code></div>
                          <div style={{marginTop:2,fontFamily:"monospace",fontSize:10,color:"#94a3b8",wordBreak:"break-all"}}>
                            {r.env_value || "—"}
                          </div>
                          {r.legacy_var && (
                            <div style={{marginTop:4,fontSize:10}}>
                              <span style={{color:"#c2410c"}}>legacy:</span> <code style={{fontSize:10,background:"#fef3c7",padding:"1px 5px",borderRadius:3}}>{r.legacy_var}</code>
                              <div style={{marginTop:1,fontFamily:"monospace",fontSize:10,color:"#94a3b8",wordBreak:"break-all"}}>
                                {r.legacy_value || "—"}
                              </div>
                            </div>
                          )}
                        </td>
                        <td style={{padding:"10px 14px",fontFamily:"monospace",fontSize:11,color:r.effective?"#16a34a":"#ef4444",maxWidth:240,wordBreak:"break-all"}}>
                          {r.effective || <span style={{color:"#ef4444"}}>&lt;not set&gt;</span>}
                        </td>
                        <td style={{padding:"10px 14px",whiteSpace:"nowrap"}}>
                          <div style={{display:"flex",gap:6}}>
                            <Btn size="sm" variant="primary"
                              disabled={!isDirty || saving===r.key}
                              onClick={()=>save(r.key)}>
                              {saving===r.key ? "Saving…" : "Save"}
                            </Btn>
                            {r.key.endsWith("_to") && (
                              <Btn size="sm"
                                disabled={testing===r.key}
                                onClick={()=>sendTest(r.key)}>
                                {testing===r.key ? "Sending…" : "Send Test"}
                              </Btn>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        );
      })}

      {rows.length === 0 && (
        <Card><EmptyState text="No mail config rows" sub="Restart the backend to seed defaults."/></Card>
      )}
      {rows.length > 0 && visibleKinds.length === 0 && (
        <Card><EmptyState text="No matching mail config" sub={`Filter: ${(kindFilter||[]).join(", ")}`}/></Card>
      )}

      {/* Per-line routing — only meaningful for the hourly slot report, which
          is the one report that covers every line in a single mail. */}
      {visibleKinds.includes("hourly") && <HourlyPerLineMail toast={toast} readOnly={readOnly}/>}
      </fieldset>
    </div>
  );
}


// ─── HOURLY SLOT REPORT — PER-LINE RECIPIENTS ─────────────────
// The settings above are GLOBAL: one digest covering every line.  This table
// routes individual lines to their own people, the same way the shift report
// and the OEE drop alarm already do.  A line listed here gets its own mail
// with only its block; every line NOT listed stays in the global digest.
function HourlyPerLineMail({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [lines, setLines] = useState([]);
  const [cfgs,  setCfgs]  = useState([]);
  const [form,  setForm]  = useState(BLANK_HOURLY_CFG);
  const [busy,  setBusy]  = useState(false);

  const load = useCallback(async () => {
    try {
      const [ls, cs] = await Promise.all([
        api.get("/api/lines/", token),
        api.get("/api/poka-yoke/hourly-mail-config/", token).catch(() => []),
      ]);
      setLines(ls || []);
      setCfgs(Array.isArray(cs) ? cs : []);
    } catch { /* the global table above still works */ }
  }, [token]);
  useEffect(() => { load(); }, [load]);

  const edit = (c) => setForm({
    line_id: String(c.line_id),
    to_addresses:         c.to_addresses         || "",
    cc_addresses:         c.cc_addresses         || "",
    zone_to_addresses:    c.zone_to_addresses    || "",
    zone_cc_addresses:    c.zone_cc_addresses    || "",
    section_to_addresses: c.section_to_addresses || "",
    section_cc_addresses: c.section_cc_addresses || "",
    is_active: c.is_active !== false,
  });

  const save = async () => {
    if (!form.line_id) { toast?.("Pick a line first", "err"); return; }
    setBusy(true);
    try {
      await api.put("/api/poka-yoke/hourly-mail-config/",
        { ...form, line_id: Number(form.line_id) }, token);
      toast?.("Per-line hourly recipients saved ✓");
      setForm(BLANK_HOURLY_CFG); load();
    } catch (e) { toast?.(e.message || "Save failed", "err"); }
    finally { setBusy(false); }
  };

  const remove = async (lineId) => {
    if (!confirm("Remove this line's own recipients?\nIt goes back into the global digest.")) return;
    try {
      await api.delete(`/api/poka-yoke/hourly-mail-config/${lineId}`, token);
      toast?.("Removed — line returns to the global digest"); load();
    } catch (e) { toast?.(e.message, "err"); }
  };

  const dash = <em style={{ color:"#94a3b8" }}>—</em>;

  return (
    <Card>
      <div style={{ marginBottom:12 }}>
        <div style={{ fontWeight:700, fontSize:15, color:"#0f172a" }}>Per-Line Recipients</div>
        <div style={{ fontSize:11, color:"#64748b", marginTop:2 }}>
          A line listed here gets its <b>own</b> hourly mail with only its block.
          Lines not listed stay in the global digest configured above.
        </div>
      </div>

      {cfgs.length === 0 ? (
        <div style={{ fontSize:12, color:"#94a3b8", padding:"8px 0" }}>
          No per-line routing yet — every line goes into the one global digest.
        </div>
      ) : (
        <div style={{ overflowX:"auto", marginBottom:14 }}>
          <table style={{ width:"100%", borderCollapse:"collapse", fontSize:12 }}>
            <thead><tr style={{ background:"#f8fafc" }}>
              {["Line","Level","To","Cc","",""].map((h,i)=>(
                <th key={i} style={{ padding:"8px 10px", textAlign:"left", fontSize:10,
                                      fontWeight:800, letterSpacing:".05em", textTransform:"uppercase",
                                      color:"#64748b", borderBottom:"2px solid #e2e8f0" }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {cfgs.map(c => (
                <tr key={c.line_id} style={{ borderBottom:"1px solid #f1f5f9" }}>
                  <td style={{ padding:"8px 10px", fontWeight:700 }}>
                    {c.line_name}
                    <div style={{ fontSize:10, color:"#94a3b8", fontFamily:"monospace" }}>{c.line_code}</div>
                  </td>
                  <td style={{ padding:"8px 10px", fontSize:11, color:"#64748b", whiteSpace:"nowrap" }}>
                    <div style={{padding:"2px 0"}}>Line</div>
                    <div style={{padding:"2px 0"}}>Zone head</div>
                    <div style={{padding:"2px 0"}}>Section head</div>
                  </td>
                  <td style={{ padding:"8px 10px", fontSize:11, fontFamily:"monospace" }}>
                    <div style={{padding:"2px 0"}}>{c.to_addresses || dash}</div>
                    <div style={{padding:"2px 0"}}>{c.zone_to_addresses || dash}</div>
                    <div style={{padding:"2px 0"}}>{c.section_to_addresses || dash}</div>
                  </td>
                  <td style={{ padding:"8px 10px", fontSize:11, fontFamily:"monospace" }}>
                    <div style={{padding:"2px 0"}}>{c.cc_addresses || dash}</div>
                    <div style={{padding:"2px 0"}}>{c.zone_cc_addresses || dash}</div>
                    <div style={{padding:"2px 0"}}>{c.section_cc_addresses || dash}</div>
                  </td>
                  <td style={{ padding:"8px 10px" }}>
                    <span style={{ padding:"2px 8px", borderRadius:10, fontSize:10, fontWeight:700,
                                    background:c.is_active?"#dcfce7":"#fee2e2",
                                    color:c.is_active?"#166534":"#991b1b" }}>
                      {c.is_active ? "ON" : "OFF"}
                    </span>
                  </td>
                  <td style={{ padding:"8px 10px", whiteSpace:"nowrap" }}>
                    {!readOnly && <>
                      <Btn size="sm" onClick={()=>edit(c)}>Edit</Btn>{" "}
                      <Btn size="sm" variant="danger" onClick={()=>remove(c.line_id)}>Del</Btn>
                    </>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!readOnly && (
        <div style={{ borderTop:"1px solid #e2e8f0", paddingTop:12 }}>
          <div style={{ display:"flex", gap:10, alignItems:"end", flexWrap:"wrap", marginBottom:10 }}>
            <div>
              <label style={{ fontSize:10, color:"#64748b" }}>Line</label>
              <Select value={form.line_id} onChange={e=>setForm(f=>({...f, line_id:e.target.value}))}
                      style={{ minWidth:190 }}>
                <option value="">— select line —</option>
                {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
              </Select>
            </div>
            <label style={{ display:"flex", alignItems:"center", gap:6, fontSize:12, color:"#475569" }}>
              <input type="checkbox" checked={form.is_active}
                     onChange={e=>setForm(f=>({...f, is_active:e.target.checked}))}/>
              Active
            </label>
            <Btn variant="primary" onClick={save} disabled={busy}>{busy ? "…" : "Save Line"}</Btn>
          </div>

          <div style={{ display:"grid", gridTemplateColumns:"110px 1fr 1fr", gap:10, alignItems:"center" }}>
            <div/>
            <div style={{ fontSize:10, fontWeight:800, color:"#64748b", letterSpacing:".06em", textTransform:"uppercase" }}>To</div>
            <div style={{ fontSize:10, fontWeight:800, color:"#64748b", letterSpacing:".06em", textTransform:"uppercase" }}>Cc</div>
            {[
              { lvl:"Line",         to:"to_addresses",         cc:"cc_addresses",         ph:"supervisor@tbdi.com" },
              { lvl:"Zone head",    to:"zone_to_addresses",    cc:"zone_cc_addresses",    ph:"zone.head@tbdi.com" },
              { lvl:"Section head", to:"section_to_addresses", cc:"section_cc_addresses", ph:"section.head@tbdi.com" },
            ].map(rw => (
              <Fragment key={rw.lvl}>
                <div style={{ fontSize:12, fontWeight:700, color:"#0f172a" }}>{rw.lvl}</div>
                <Input value={form[rw.to]} placeholder={rw.ph}
                       onChange={e=>setForm(f=>({...f, [rw.to]:e.target.value}))}
                       style={{ fontFamily:"monospace", fontSize:12 }}/>
                <Input value={form[rw.cc]} placeholder="(optional)"
                       onChange={e=>setForm(f=>({...f, [rw.cc]:e.target.value}))}
                       style={{ fontFamily:"monospace", fontSize:12 }}/>
              </Fragment>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}


// ─── SENSOR HEALTH TAB ────────────────────────────────────────
// Polls /api/poka-yoke/sensor-sweep/{line_id} every 5 s.  Passive read-only
// view: each sensing X-bit shows its current value, when it last toggled,
// and whether it's gone stuck (>15 min without a natural toggle).  No
// force-toggle: the collector NEVER writes back to the PLC.  If a sensor
// goes stuck, an email fires once and the operator inspects physically.
export function SensorHealthPage({ lines, toast, token, readOnly = false }) {
  const [lineId,    setLineId]    = useState(() => (lines?.[0]?.id ?? null));
  const [sweep,     setSweep]     = useState({ swept_at: null, entries: [] });
  const [search,    setSearch]    = useState("");
  const [zoneFilter, setZoneFilter] = useState("");   // "" = all zones

  // 1-second wall-clock tick so every relative-time label ("13s ago",
  // "Last snapshot 2s old") re-renders smoothly without waiting for the
  // next 5-second backend poll.  Cheap — just a Date.now() bump.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  // ── Zones list for the filter dropdown ─────────────────────────────
  const [zones, setZones] = useState([]);
  useEffect(() => {
    api.get("/api/zones/", token)
      .then(z => setZones(Array.isArray(z) ? z : []))
      .catch(() => {});
  }, [token]);

  // ── Current model running on the selected line ─────────────────────
  // We poll /api/poka-yoke/live/{line_id} (already filters by current
  // model on the backend) — every row's py_master_id tells us which PYs
  // are applicable for the model that's actually running right now.
  const [liveModel, setLiveModel] = useState({
    name: null, bit: null, allowed_py_ids: null,
  });
  useEffect(() => {
    if (!lineId) {
      setLiveModel({ name: null, bit: null, allowed_py_ids: null });
      return;
    }
    const fetchLive = () => {
      api.get(`/api/poka-yoke/live/${lineId}`, token)
        .then(rows => {
          const arr = Array.isArray(rows) ? rows : [];
          const ids = new Set(
            arr.map(r => r.py_master_id).filter(v => v != null),
          );
          // Prefer the top-level resolved bit (always set when /live/
          // resolved a model); fall back to per-row JOIN bit_number.
          const bit = arr[0]?.current_model_bit ?? arr[0]?.model_bit ?? null;
          setLiveModel({
            name: arr[0]?.current_model || null,
            bit,
            allowed_py_ids: ids.size > 0 ? ids : null,
          });
        })
        .catch(() => {});
    };
    fetchLive();
    const t = setInterval(fetchLive, 10000);
    return () => clearInterval(t);
  }, [lineId, token]);

  // ── PY Master CRUD (inline edit + delete + quick-add) ───────────────
  // Drives the ✏️ / 🗑️ icons on each row and the "+ Add Sensor" button at
  // the top — all backed by the same /api/poka-yoke/master/ endpoints the
  // PY Master tab uses.  Also gives us each PY's zone so the Zone filter
  // dropdown can do a client-side join.
  const [pyMaster, setPyMaster]     = useState([]);
  const [editPy,   setEditPy]       = useState(null);   // py object being edited
  const [editForm, setEditForm]     = useState({ description: "", sensingBits: "" });
  const [savingEdit, setSavingEdit] = useState(false);
  const [showAdd,  setShowAdd]      = useState(false);
  const [addForm,  setAddForm]      = useState({
    pyNo: "", description: "", dBit: "", sensingBits: "",
    modelType: "4 Way", typeSide: "ALL", registerCount: 1, zoneId: "",
  });
  const [savingAdd, setSavingAdd]   = useState(false);

  const reloadMaster = useCallback(async () => {
    try {
      const d = await api.get("/api/poka-yoke/master/", token);
      setPyMaster(Array.isArray(d) ? d : []);
    } catch(e) { /* silent — master list is best-effort */ }
  }, [token]);

  useEffect(() => {
    if (!lineId) return;
    const fetchSweep = () => {
      api.get(`/api/poka-yoke/sensor-sweep/${lineId}`, token)
        .then(d => setSweep(d && typeof d === "object" ? d
                              : { swept_at: null, entries: [] }))
        .catch(() => {});
    };
    fetchSweep();
    reloadMaster();
    const t = setInterval(fetchSweep, 5000);
    return () => clearInterval(t);
  }, [lineId, token, reloadMaster]);

  // ── Edit X-bit / description for an existing PY ────────────────────
  const openEdit = (g) => {
    // Find the PY master row matching this group's d_bit
    const py = pyMaster.find(p =>
      (p.dBit || p.register || "").toUpperCase().includes((g.d_bit||"").toUpperCase())
      || g.py_id === p.id);
    if (!py) {
      toast("PY master row not found — load PY Master tab once", "err");
      return;
    }
    setEditPy(py);
    setEditForm({
      description: py.description || "",
      sensingBits: py.sensingBits || "",
    });
  };
  const closeEdit = () => { setEditPy(null); setEditForm({ description:"", sensingBits:"" }); };
  const saveEdit  = async () => {
    if (!editPy) return;
    setSavingEdit(true);
    try {
      await api.put(`/api/poka-yoke/master/${editPy.id}`, {
        description: editForm.description,
        sensingBits: editForm.sensingBits.toUpperCase(),
      }, token);
      toast("Updated ✓", "ok");
      closeEdit();
      reloadMaster();
    } catch(e) { toast(e.message || "Save failed", "err"); }
    finally   { setSavingEdit(false); }
  };

  // ── Delete (soft-deactivate) a PY ──────────────────────────────────
  const delSensor = async (g) => {
    const py = pyMaster.find(p =>
      (p.dBit || p.register || "").toUpperCase().includes((g.d_bit||"").toUpperCase())
      || g.py_id === p.id);
    if (!py) { toast("PY master row not found", "err"); return; }
    if (!confirm(`Delete "${py.description || py.dBit}" from PY master?\n\nIt'll stop monitoring this sensor immediately.`)) return;
    try {
      await api.delete(`/api/poka-yoke/master/${py.id}`, token);
      toast("Deleted ✓", "ok");
      reloadMaster();
    } catch(e) { toast(e.message || "Delete failed", "err"); }
  };

  // ── Quick-add a new PY straight from the Sensor Health page ────────
  const openAdd = () => {
    setAddForm({
      pyNo: "", description: "", dBit: "", sensingBits: "",
      modelType: "4 Way", typeSide: "ALL", registerCount: 1,
    });
    setShowAdd(true);
  };
  const saveAdd = async () => {
    if (!addForm.dBit.trim() || !addForm.description.trim()) {
      toast("D-Bit and Description are required", "err");
      return;
    }
    setSavingAdd(true);
    try {
      await api.post("/api/poka-yoke/master/", {
        pyNo:          addForm.pyNo.trim() || addForm.dBit.toUpperCase().trim(),
        description:   addForm.description.trim(),
        dBit:          addForm.dBit.toUpperCase().trim(),
        register:      addForm.dBit.toUpperCase().trim(),
        sensingBits:   addForm.sensingBits.toUpperCase().trim() || null,
        modelType:     addForm.modelType,
        typeSide:      addForm.typeSide,
        registerCount: addForm.registerCount,
      }, token);
      toast("Added ✓", "ok");
      setShowAdd(false);
      reloadMaster();
    } catch(e) { toast(e.message || "Add failed", "err"); }
    finally   { setSavingAdd(false); }
  };

  // py_id → zoneId / zoneName / zoneCode  (cross-ref from pyMaster).
  // The collector's snapshot doesn't include zone info, so we join here on
  // the client side using whatever's currently in PY Master.
  const pyZoneMap = {};
  pyMaster.forEach(p => {
    if (p.id != null) pyZoneMap[p.id] = {
      zoneId:   p.zoneId,
      zoneName: p.zoneName,
      zoneCode: p.zoneCode,
    };
  });

  const rawEntries = (sweep.entries || []).filter(e => {
    if (search && !`${e.bit||""} ${e.d_bit||""} ${e.py_name||""}`
                       .toLowerCase().includes(search.toLowerCase()))
      return false;
    if (zoneFilter) {
      const z = pyZoneMap[e.py_id];
      if (!z || String(z.zoneId) !== String(zoneFilter)) return false;
    }
    // Restrict to PYs actually configured for the line's CURRENT model.
    // If the live endpoint hasn't returned anything yet, allow all so the
    // user sees something while the page loads.
    if (liveModel.allowed_py_ids) {
      if (!liveModel.allowed_py_ids.has(e.py_id)) return false;
    }
    return true;
  });

  // Group by PY / D-bit so a multi-sensing-bit PY (e.g. D407 with X26+X27)
  // collapses into a single row.  Aggregate X-bits + values as comma-lists
  // and pick the BEST status — if ANY bit toggled recently the PY is
  // doing its job, so the row is "alive".  Only when EVERY bit is stuck
  // does the row read STUCK.  This matches operator intuition: an E-RING
  // PY with X12+X13 where one limit-switch is firing means the part is
  // being detected, even if the other bit hasn't seen a part yet.
  const STATUS_RANK = { alive: 0, stuck: 1 };
  const STATUS_BACK = ["alive", "stuck"];
  const groupKey = (e) => e.d_bit || `__${e.bit}`;
  const groupedMap = {};
  rawEntries.forEach(e => {
    const k = groupKey(e);
    if (!groupedMap[k]) {
      groupedMap[k] = {
        d_bit:    e.d_bit,
        py_id:    e.py_id,
        py_name:  e.py_name,
        x_bits:   [],
        x_states: [],
        best:     1,       // start at "stuck"; improves to "alive" if any bit is alive
        // Row "ago" tracks the FRESHEST toggle across all bits, not the
        // oldest — same rationale: any-bit-toggled is enough.  Field
        // name kept as `oldest_toggle_*` for back-compat with downstream
        // formatters; semantics flipped to FRESHEST.
        oldest_toggle_ago: null,
        oldest_toggle_at:  null,
      };
    }
    const g = groupedMap[k];
    g.x_bits.push(e.bit);
    g.x_states.push(e);
    const rank = STATUS_RANK[e.status] ?? 0;
    if (rank < g.best) g.best = rank;   // any "alive" (rank 0) wins
    if (e.last_toggle_ago_sec != null
        && (g.oldest_toggle_ago == null
            || e.last_toggle_ago_sec < g.oldest_toggle_ago)) {
      g.oldest_toggle_ago = e.last_toggle_ago_sec;
      g.oldest_toggle_at  = e.last_toggle_at || g.oldest_toggle_at;
    }
  });
  const entries = Object.values(groupedMap)
    .map(g => ({ ...g, status: STATUS_BACK[g.best] }));

  const total    = entries.length;
  const aliveCt  = entries.filter(e => e.status === "alive").length;
  const stuckCt  = entries.filter(e => e.status === "stuck").length;

  const fmtAgo = (sec) => {
    if (sec == null || sec < 0) return "—";
    if (sec < 60)  return `${Math.round(sec)}s ago`;
    if (sec < 3600) return `${Math.floor(sec/60)}m ${Math.round(sec%60)}s ago`;
    return `${Math.floor(sec/3600)}h ${Math.floor((sec%3600)/60)}m ago`;
  };

  // Live "ago" — reads the backend ISO timestamp and recomputes against
  // the 1-Hz wall-clock tick so the label keeps moving (13s → 14s → 15s)
  // even between backend polls.  Falls back to backend-supplied seconds
  // if the ISO string is missing.
  const liveAgo = (isoStr, fallbackSec) => {
    if (isoStr) {
      const t = new Date(isoStr).getTime();
      if (!isNaN(t)) return fmtAgo((nowMs - t) / 1000);
    }
    return fmtAgo(fallbackSec);
  };

  // Snapshot age — green if fresh (<15 s), amber if 15–30 s, red if older
  // because that means the collector probably stopped publishing.
  const snapAgeSec = sweep.swept_at
    ? (nowMs - new Date(sweep.swept_at).getTime()) / 1000
    : null;
  const snapColor =
    snapAgeSec == null ? "#94a3b8" :
    snapAgeSec < 15    ? "#16a34a" :
    snapAgeSec < 30    ? "#f59e0b" : "#ef4444";

  return (
    <div>
      {/* Stats */}
      <div style={{display:"flex",gap:14,marginBottom:18,flexWrap:"wrap"}}>
        {[
          { label:"Total tracked", val: total,   color:"#1e40af" },
          { label:"Alive",         val: aliveCt, color:"#16a34a" },
          { label:"Stuck (>15m)",  val: stuckCt, color:"#ef4444" },
        ].map(({label,val,color})=>(
          <div key={label} style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:10,padding:"12px 18px",minWidth:120}}>
            <div style={{fontSize:11,color:"#64748b",fontWeight:600,textTransform:"uppercase",letterSpacing:".06em"}}>{label}</div>
            <div style={{fontSize:26,fontWeight:800,color}}>{val}</div>
          </div>
        ))}
      </div>

      {/* Header + controls */}
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:14,gap:10,flexWrap:"wrap"}}>
        <div>
          <div style={{fontWeight:700,fontSize:15,color:"#0f172a"}}>Sensor Health — passive read-only monitor</div>
          <div style={{fontSize:11,color:"#64748b",marginTop:2}}>
            Last snapshot:&nbsp;
            <b style={{color:snapColor}}>
              {sweep.swept_at ? new Date(sweep.swept_at).toLocaleTimeString() : "—"}
            </b>
            <span style={{color:snapColor,marginLeft:6,fontWeight:600}}>
              ({snapAgeSec == null ? "no data" : `${Math.round(snapAgeSec)}s old`})
            </span>
            &nbsp; | &nbsp; X-bit polled ~1 Hz; collector NEVER writes the PLC.
            &nbsp; If no natural toggle in 15 min → STUCK + email alert fires once.
          </div>
        </div>
        <div style={{display:"flex",gap:10,alignItems:"center",flexWrap:"wrap"}}>
          <Select value={lineId || ""} onChange={e=>setLineId(Number(e.target.value)||null)} style={{minWidth:140}}>
            {(lines || []).map(l=> <option key={l.id} value={l.id}>{l.line_name}</option>)}
          </Select>
          <Select value={zoneFilter} onChange={e=>setZoneFilter(e.target.value)} style={{minWidth:160}}>
            <option value="">All Zones</option>
            {zones.map(z=>(
              <option key={z.id} value={z.id}>
                {z.zone_name}{z.zone_code ? ` (${z.zone_code})` : ""}
              </option>
            ))}
          </Select>
          <Input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search bit / PY…" style={{width:200}}/>
          {!readOnly && <Btn variant="primary" onClick={openAdd}>+ Add Sensor</Btn>}
        </div>
      </div>

      {/* Per-line current-model heading */}
      <div style={{marginBottom:16,padding:"12px 18px",background:"#fff",
                   border:"1px solid #e2e8f0",borderRadius:10,
                   display:"flex",alignItems:"center",gap:14,flexWrap:"wrap"}}>
        <div style={{fontSize:11,fontWeight:700,color:"#64748b",textTransform:"uppercase",letterSpacing:".06em"}}>
          Line
        </div>
        <div style={{fontSize:16,fontWeight:800,color:"#0f172a",fontFamily:"'Barlow Condensed',sans-serif"}}>
          {(lines || []).find(l => l.id === lineId)?.line_name || "—"}
        </div>
        <div style={{borderLeft:"2px solid #e2e8f0",height:24,margin:"0 4px"}}/>
        <div style={{fontSize:11,fontWeight:700,color:"#64748b",textTransform:"uppercase",letterSpacing:".06em"}}>
          Running Model
        </div>
        {liveModel.name ? (
          <>
            <span style={{display:"inline-block",padding:"3px 12px",borderRadius:99,fontSize:13,fontWeight:800,
                         background:"rgba(124,58,237,.12)",color:"#6d28d9",fontFamily:"monospace"}}>
              #{liveModel.bit ?? "?"}
            </span>
            <span style={{fontSize:13,fontWeight:700,color:"#0f172a"}}>
              {liveModel.name}
            </span>
          </>
        ) : (
          <span style={{fontSize:12,color:"#94a3b8",fontStyle:"italic"}}>
            no model running — showing all configured PYs
          </span>
        )}
        {liveModel.allowed_py_ids && (
          <span style={{marginLeft:"auto",fontSize:11,color:"#64748b"}}>
            <b>{liveModel.allowed_py_ids.size}</b> PY{liveModel.allowed_py_ids.size===1?"":"s"} applicable for this model
          </span>
        )}
      </div>

      <Card>
        {entries.length === 0 ? (
          <EmptyState text="No sensor data yet"
            sub={readOnly
              ? "Collector publishes every ~10 seconds — readings will appear once data flows in."
              : 'Collector publishes every ~10 seconds. Click "+ Add Sensor" to register a new PY (D-bit + X-bit) right here.'}/>
        ) : (
          <div style={{overflowX:"auto"}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
              <thead><tr>
                {["Zone","D-Bit","PY Name","X-Bit","Current","Last Toggle","Status", ...(readOnly ? [] : ["Edit"])].map(h=>(
                  <th key={h} style={{padding:"9px 12px",textAlign:"left",fontSize:9,fontWeight:700,letterSpacing:".08em",textTransform:"uppercase",color:"#64748b",borderBottom:"2px solid #e2e8f0",whiteSpace:"nowrap"}}>{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {entries.map(g => {
                  const status  = g.status || "alive";
                  const isStuck = status === "stuck";

                  // Comma-joined X-bit list and the per-bit current values
                  // shown in the same order so user can pair them up.
                  const xBitsStr = g.x_bits.join(", ");
                  const valStr   = g.x_states
                      .map(s => s.current_value == null ? "—" : s.current_value)
                      .join(", ");

                  // Zone label — pulled from PY Master cross-ref.
                  const zoneInfo  = pyZoneMap[g.py_id] || {};
                  const zoneLabel = zoneInfo.zoneName || zoneInfo.zoneCode || "—";

                  const statusNode = isStuck ? (
                    <span style={{display:"inline-block",padding:"2px 9px",borderRadius:99,fontSize:10,fontWeight:700,
                                  background:"rgba(239,68,68,.14)",color:"#b91c1c",
                                  animation:"blink 1s infinite"}}>
                      ✗ STUCK
                    </span>
                  ) : (
                    <span style={{display:"inline-block",padding:"2px 9px",borderRadius:99,fontSize:10,fontWeight:700,
                                  background:"rgba(22,163,74,.14)",color:"#15803d"}}>
                      ✓ ALIVE
                    </span>
                  );

                  const rowKey = g.d_bit || g.x_bits[0];

                  return (
                    <tr key={rowKey} style={{borderBottom:"1px solid #f1f5f9",
                          background: isStuck ? "rgba(239,68,68,.04)" : "transparent"}}>
                      <td style={{padding:"8px 12px",fontSize:11,whiteSpace:"nowrap"}}
                          title={zoneInfo.zoneCode ? `Code: ${zoneInfo.zoneCode}` : ""}>
                        {zoneInfo.zoneName ? (
                          <span style={{display:"inline-block",padding:"2px 8px",borderRadius:99,
                                        background:"rgba(30,64,175,.1)",color:"#1e40af",fontWeight:700,fontSize:10}}>
                            {zoneInfo.zoneName}
                          </span>
                        ) : <span style={{color:"#cbd5e1"}}>—</span>}
                      </td>
                      <td style={{padding:"8px 12px",fontFamily:"monospace",fontWeight:700,color:"#7c3aed"}}>{g.d_bit || "—"}</td>
                      <td style={{padding:"8px 12px",color:"#0f172a",maxWidth:240,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} title={g.py_name||""}>
                        {g.py_name || <span style={{color:"#94a3b8",fontStyle:"italic"}}>(unbound)</span>}
                      </td>
                      <td style={{padding:"8px 12px",fontFamily:"monospace",fontWeight:700,color:"#0891b2"}}
                          title={g.x_bits.length > 1 ? `${g.x_bits.length} sensing bits` : ""}>
                        {xBitsStr}
                      </td>
                      <td style={{padding:"8px 12px",fontFamily:"monospace",fontWeight:700,color:"#0f172a"}}
                          title={g.x_bits.length > 1 ? "values shown in the order of X-bits" : ""}>
                        {valStr}
                      </td>
                      <td style={{padding:"8px 12px",fontSize:11,color:"#64748b"}}
                          title={`oldest of ${g.x_bits.length} bit(s)`}>
                        {liveAgo(g.oldest_toggle_at, g.oldest_toggle_ago)}
                      </td>
                      <td style={{padding:"8px 12px"}}>{statusNode}</td>
                      {!readOnly && (
                        <td style={{padding:"8px 12px",whiteSpace:"nowrap"}}>
                          <div style={{display:"flex",gap:6}}>
                            <Btn size="sm" onClick={()=>openEdit(g)} title="Change X-bit / description">✏️</Btn>
                            <Btn size="sm" variant="danger" onClick={()=>delSensor(g)} title="Remove this PY from monitoring">🗑️</Btn>
                          </div>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div style={{padding:"8px 14px",fontSize:11,color:"#94a3b8",borderTop:"1px solid #f1f5f9"}}>
              Showing {entries.length} PYs ({rawEntries.length} total sensing X-bits).
              Multi-X-bit PYs (e.g. D406 with X21+X22) are collapsed into one row;
              if <b style={{color:"#16a34a"}}>any</b> bit toggled within 15&nbsp;min the row reads <b>ALIVE</b>;
              only when <b>every</b> bit goes stuck does the row turn <b style={{color:"#ef4444"}}>STUCK</b>.
              {!readOnly && ' ✏️ to change X-bit/description, 🗑️ to remove the sensor, "+ Add Sensor" to register a new one.'}
            </div>
          </div>
        )}
      </Card>

      {/* ── Edit X-bit / description ── */}
      {editPy && (
        <Modal open={!!editPy} onClose={closeEdit} title={`Edit ${editPy.dBit || editPy.register || editPy.pyNo}`}>
          <div style={{display:"grid",gridTemplateColumns:"1fr",gap:14}}>
            <FF label="D-Bit (read-only)">
              <Input value={editPy.dBit || editPy.register || ""} disabled
                style={{fontFamily:"monospace",color:"#7c3aed",fontWeight:700}}/>
            </FF>
            <FF label="Description">
              <Input value={editForm.description}
                onChange={e=>setEditForm(f=>({...f,description:e.target.value}))}
                placeholder="Harness brkt pop rivet operation miss"/>
            </FF>
            <FF label="Sensing X-Bit(s)">
              <Input value={editForm.sensingBits}
                onChange={e=>setEditForm(f=>({...f,sensingBits:e.target.value.toUpperCase()}))}
                placeholder="X15  OR  X21,X22"
                style={{fontFamily:"monospace",fontWeight:700,color:"#0891b2"}}/>
              <div style={{fontSize:10,color:"#94a3b8",marginTop:4}}>
                Comma-separated for PYs with multiple sensing inputs.  Blank = skip health monitoring.
              </div>
            </FF>
          </div>
          <ModalActions>
            <Btn onClick={closeEdit}>Cancel</Btn>
            <Btn variant="primary" disabled={savingEdit} onClick={saveEdit}>
              {savingEdit ? "Saving…" : "Save"}
            </Btn>
          </ModalActions>
        </Modal>
      )}

      {/* ── Quick-add a new PY ── */}
      {showAdd && (
        <Modal open={showAdd} onClose={()=>setShowAdd(false)} title="Add Sensor (new PY)" wide>
          <div style={{padding:"10px 14px",background:"#fff7ed",border:"1px solid #fed7aa",borderRadius:8,fontSize:11,color:"#9a3412",marginBottom:14}}>
            Quick-add only — for full type/side/model assignment use Poka Yoke → Master tab.
          </div>
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:14}}>
            <FF label="D-Bit *">
              <Input value={addForm.dBit}
                onChange={e=>setAddForm(f=>({...f,dBit:e.target.value.toUpperCase()}))}
                placeholder="D420"
                style={{fontFamily:"monospace",fontWeight:700,color:"#7c3aed"}}/>
            </FF>
            <FF label="Sensing X-Bit(s)">
              <Input value={addForm.sensingBits}
                onChange={e=>setAddForm(f=>({...f,sensingBits:e.target.value.toUpperCase()}))}
                placeholder="X20  OR  X21,X22"
                style={{fontFamily:"monospace",fontWeight:700,color:"#0891b2"}}/>
            </FF>
            <FF label="Description *" style={{gridColumn:"1/-1"}}>
              <Input value={addForm.description}
                onChange={e=>setAddForm(f=>({...f,description:e.target.value}))}
                placeholder="Sensor description"/>
            </FF>
            <FF label="PY No. (optional, defaults to D-bit)">
              <Input value={addForm.pyNo}
                onChange={e=>setAddForm(f=>({...f,pyNo:e.target.value}))}
                placeholder="auto"/>
            </FF>
            <FF label="Type">
              <Select value={addForm.modelType} onChange={e=>setAddForm(f=>({...f,modelType:e.target.value}))}>
                <option>4 Way</option>
                <option>6 Way</option>
              </Select>
            </FF>
            <FF label="Side">
              <Select value={addForm.typeSide} onChange={e=>setAddForm(f=>({...f,typeSide:e.target.value}))}>
                <option>ALL</option>
                <option>LH</option>
                <option>RH</option>
                <option>OTR</option>
              </Select>
            </FF>
            <FF label="Register Output">
              <Select value={addForm.registerCount}
                onChange={e=>setAddForm(f=>({...f,registerCount:Number(e.target.value)}))}>
                <option value={1}>1 register (PASS / OFF / ON)</option>
                <option value={2}>2 registers (combined codes)</option>
              </Select>
            </FF>
          </div>
          <ModalActions>
            <Btn onClick={()=>setShowAdd(false)}>Cancel</Btn>
            <Btn variant="primary" disabled={savingAdd} onClick={saveAdd}>
              {savingAdd ? "Saving…" : "Add Sensor"}
            </Btn>
          </ModalActions>
        </Modal>
      )}
    </div>
  );
}

// ─── MODEL MASTER TAB ─────────────────────────────────────────
function PYModels({ models, series, zones = [], toast, token, onReload, readOnly = false }) {
  // ── Series Master (top section) ───────────────────────────────────────────
  const [newSeries, setNewSeries] = useState("");
  const [sBusy,     setSBusy]     = useState(false);

  // ── Model Config (bottom section) ─────────────────────────────────────────
  const [modal,   setModal]   = useState(false);
  const [editing, setEditing] = useState(null);
  const [saving,  setSaving]  = useState(false);
  const [search,  setSearch]  = useState("");
  const [zoneFilter, setZoneFilter] = useState("all");   // zone-wise model master

  const MODEL_TYPES = ["4 Way Inr LH","4 Way Inr RH","4 Way OTR","6 Way Inr LH","6 Way Inr RH","6 Way Otr LH","6 Way Otr RH"];
  const EMPTY = { bitNumber:"", type:"", selSeries:[], modelName:"", zoneId:"" };
  const [form, setForm] = useState(EMPTY);

  // Bit numbers are unique WITHIN a zone, so the "already used" warning
  // only fires when the user picks a zone that already has the same bit.
  const usedBits = new Set(
    models
      .filter(m => m.bitNumber != null
                && String(m.zoneId || "") === String(form.zoneId || ""))
      .map(m => m.bitNumber)
  );

  // Types suggested ACCORDING TO the selected zone — distinct `type` values
  // from models already in that zone.  Falls back to the standard list when
  // the zone has none yet.  The field is free-text, so a new/manual type can
  // always be entered too.
  const zoneTypeOptions = useMemo(() => {
    const inZone = models.filter(m =>
      String(m.zoneId ?? "") === String(form.zoneId ?? "") && (m.type || "").trim());
    const set = [...new Set(inZone.map(m => m.type.trim()))].sort();
    return set.length ? set : MODEL_TYPES;
  }, [models, form.zoneId]);

  // {TYPE}: ({S1/S2/...})
  const buildName = (type, seriesArr) => {
    const t = (type||"").toUpperCase().trim() || "—";
    const s = (seriesArr||[]).join("/") || "—";
    return `${t}: (${s})`;
  };

  // ── Series handlers ───────────────────────────────────────────────────────
  const addSeries = async () => {
    const code = newSeries.trim().toUpperCase();
    if (!code) { toast("Series code required","err"); return; }
    if (series.some(s=>s.code===code)) { toast(`${code} already exists`,"err"); return; }
    setSBusy(true);
    try {
      await api.post("/api/poka-yoke/series/", { code }, token);
      setNewSeries(""); toast(`${code} added ✓`); onReload();
    } catch(e) { toast(e.message,"err"); }
    finally { setSBusy(false); }
  };

  const delSeries = async (s) => {
    if (!confirm(`Delete series "${s.code}"?`)) return;
    try { await api.delete(`/api/poka-yoke/series/${s.id}`, token); toast("Deleted"); onReload(); }
    catch(e) { toast(e.message,"err"); }
  };

  // ── Model handlers ────────────────────────────────────────────────────────
  const openAdd  = () => { setForm({ ...EMPTY, zoneId: zoneFilter !== "all" ? zoneFilter : "" }); setEditing(null); setModal(true); };
  const openEdit = m => {
    const seriesArr = (m.model||"").split("/").map(x=>x.trim()).filter(Boolean);
    setForm({
      bitNumber: m.bitNumber!=null ? String(m.bitNumber) : "",
      type:      m.type || "",
      selSeries: seriesArr,
      modelName: (m.modelName||"").replace(/^TYPE-SERIES:\s*/i,""),
      zoneId:    m.zoneId != null ? String(m.zoneId) : "",
    });
    setEditing(m); setModal(true);
  };

  const toggleSeries = (code) => {
    setForm(f => ({ ...f, selSeries: f.selSeries.includes(code)
      ? f.selSeries.filter(x=>x!==code)
      : [...f.selSeries, code] }));
  };

  const save = async () => {
    if (!form.bitNumber)          { toast("Bit number required","err");      return; }
    if (!form.zoneId)             { toast("Zone required","err");            return; }
    if (!form.type)               { toast("Type required","err");            return; }
    if (form.selSeries.length===0){ toast("Select at least one series","err"); return; }
    if (!form.modelName.trim())   { toast("Model Name required","err");      return; }
    const bit = parseInt(form.bitNumber);
    // Bit-conflict check is now scoped to the selected zone — same bit
    // can legitimately exist in another zone.
    const conflict = models.find(m =>
      m.bitNumber === bit
      && String(m.zoneId || "") === String(form.zoneId)
      && (!editing || m.id !== editing.id),
    );
    if (conflict) {
      toast(`Bit ${bit} already used by "${conflict.modelName}" in this zone`,"err");
      return;
    }

    const payload = {
      modelName: form.modelName.trim(),
      type:      form.type,
      model:     form.selSeries.join("/"),
      bitNumber: bit,
      zoneId:    Number(form.zoneId),
    };
    setSaving(true);
    try {
      if (editing) { await api.put(`/api/poka-yoke/models/${editing.id}`, payload, token); toast("Updated ✓"); }
      else         { await api.post("/api/poka-yoke/models/", payload, token);            toast("Added ✓"); }
      setModal(false); onReload();
    } catch(e) { toast(e.message,"err"); }
    finally { setSaving(false); }
  };

  const del = async id => {
    if (!confirm("Delete this model?")) return;
    try { await api.delete(`/api/poka-yoke/models/${id}`, token); toast("Deleted"); onReload(); }
    catch(e) { toast(e.message,"err"); }
  };

  const zoneModels = zoneFilter === "all"
    ? models
    : models.filter(m => String(m.zoneId ?? "") === String(zoneFilter));
  const filtered = zoneModels.filter(m => !search ||
    Object.values(m).some(v => String(v).toLowerCase().includes(search.toLowerCase())));

  return (
    <div>
      {/* ═════════ SECTION A — Series Master ═════════ */}
      <Card style={{ marginBottom: 20 }}>
        <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:14,paddingBottom:12,borderBottom:"1px solid #f1f5f9"}}>
          <span style={{fontSize:22}}>🏷️</span>
          <div>
            <div style={{fontSize:15,fontWeight:700,color:"#0f172a"}}>Series Master</div>
            <div style={{fontSize:11,color:"#94a3b8"}}>
              {readOnly
                ? "Series codes (YRA, YNC, YY8 …) currently assignable to models below."
                : "Add the series codes (YRA, YNC, YY8 …) that can be assigned to models below."}
            </div>
          </div>
        </div>
        {!readOnly && (
          <div style={{display:"flex",gap:10,marginBottom:14}}>
            <Input
              value={newSeries}
              onChange={e=>setNewSeries(e.target.value.toUpperCase())}
              placeholder="e.g. YNC"
              style={{maxWidth:220}}
              onKeyDown={e=>{ if(e.key==="Enter") addSeries(); }}
            />
            <Btn variant="primary" onClick={addSeries} disabled={sBusy}>+ Add Series</Btn>
          </div>
        )}
        <div style={{display:"flex",gap:8,flexWrap:"wrap",minHeight:32,alignItems:"center"}}>
          {series.length===0 ? (
            <span style={{fontSize:12,color:"#cbd5e1",fontStyle:"italic"}}>
              {readOnly ? "No series configured." : "No series yet — add your first one above."}
            </span>
          ) : series.map(s=>(
            <span key={s.id} style={{
              display:"inline-flex",alignItems:"center",gap:6,
              padding:"6px 12px 6px 12px",borderRadius:99,
              background:"rgba(22,163,74,.1)",color:"#16a34a",
              fontWeight:800,fontSize:12,letterSpacing:".04em",
            }}>
              {s.code}
              {!readOnly && (
                <button
                  onClick={()=>delSeries(s)}
                  title={`Delete ${s.code}`}
                  style={{
                    border:"none",background:"rgba(220,38,38,.12)",color:"#dc2626",
                    fontWeight:900,cursor:"pointer",fontSize:12,lineHeight:1,
                    width:18,height:18,borderRadius:"50%",padding:0,
                    display:"inline-flex",alignItems:"center",justifyContent:"center",
                    marginLeft:4,
                  }}
                >×</button>
              )}
            </span>
          ))}
        </div>
      </Card>

      {/* ═════════ SECTION B — Model Config ═════════ */}
      <div style={{display:"flex",gap:14,marginBottom:16,flexWrap:"wrap"}}>
        {[
          {label:"Total Models", val:zoneModels.length,                                        color:"#1e40af"},
          {label:"Series",       val:series.length,                                            color:"#16a34a"},
          {label:"Bits Assigned",val:zoneModels.filter(m=>m.bitNumber!=null).length,           color:"#7c3aed"},
        ].map(({label,val,color})=>(
          <div key={label} style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:10,padding:"12px 18px",minWidth:110}}>
            <div style={{fontSize:11,color:"#64748b",fontWeight:600,textTransform:"uppercase",letterSpacing:".06em"}}>{label}</div>
            <div style={{fontSize:26,fontWeight:800,color}}>{val}</div>
          </div>
        ))}
      </div>

      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:14,gap:10,flexWrap:"wrap"}}>
        <div style={{fontWeight:700,fontSize:15,color:"#0f172a"}}>Model Master</div>
        <div style={{display:"flex",gap:10,alignItems:"center",flexWrap:"wrap"}}>
          <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search..." style={{...inputStyle,width:200,padding:"8px 12px"}}/>
          {!readOnly && (
            <>
              <ExcelTools routePrefix="/api/poka-yoke/models"
                          label="models" fileBaseName="model_master"
                          token={token} toast={toast} onDone={onReload}/>
              <Btn variant="primary" onClick={openAdd}>+ Add Model</Btn>
            </>
          )}
        </div>
      </div>

      {/* Zone-wise filter — view + add/edit/delete models per zone */}
      <div style={{display:"flex",gap:8,flexWrap:"wrap",marginBottom:14,alignItems:"center"}}>
        <span style={{fontSize:11,fontWeight:700,color:"#64748b",textTransform:"uppercase",letterSpacing:".06em",marginRight:2}}>Zone:</span>
        {[{id:"all",zone_name:"All Zones"}, ...zones].map(z=>{
          const on  = String(zoneFilter)===String(z.id);
          const cnt = z.id==="all" ? models.length : models.filter(m=>String(m.zoneId??"")===String(z.id)).length;
          return (
            <button key={z.id} type="button" onClick={()=>setZoneFilter(String(z.id))}
              style={{padding:"6px 13px",borderRadius:99,fontSize:12.5,fontWeight:700,cursor:"pointer",
                border: on?"1.5px solid #1e40af":"1px solid #e2e8f0",
                background: on?"rgba(30,64,175,.1)":"#fff", color: on?"#1e40af":"#475569"}}>
              {z.zone_name||z.zone_code||z.id} <span style={{opacity:.55,fontWeight:600}}>({cnt})</span>
            </button>
          );
        })}
      </div>

      <Card>
        {filtered.length===0 ? (
          <EmptyState text="No models" sub={readOnly ? (zoneFilter==="all"?"No models configured yet.":"No models in this zone yet.") : 'Click "+ Add Model" to create your first one.'}/>
        ) : (
          <div style={{overflowX:"auto"}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
              <thead><tr>
                {["Zone","Bit #","Type","Series","Model Name", ...(readOnly ? [] : ["Actions"])].map(h=>(
                  <th key={h} style={{padding:"9px 14px",textAlign:"left",fontSize:9,fontWeight:700,letterSpacing:".08em",textTransform:"uppercase",color:"#64748b",borderBottom:"2px solid #e2e8f0"}}>{h}</th>
                ))}
              </tr></thead>
              <tbody>
                {filtered.map(m=>(
                  <tr key={m.id} style={{borderBottom:"1px solid #f1f5f9"}}>
                    <td style={{padding:"10px 14px",whiteSpace:"nowrap"}}>
                      {m.zoneName ? (
                        <span style={{display:"inline-block",padding:"2px 9px",borderRadius:99,fontSize:10,fontWeight:700,background:"rgba(30,64,175,.1)",color:"#1e40af"}}
                              title={m.zoneCode||""}>
                          {m.zoneName}
                        </span>
                      ) : <span style={{color:"#cbd5e1",fontSize:11}}>—</span>}
                    </td>
                    <td style={{padding:"10px 14px"}}>
                      {m.bitNumber!=null
                        ? <span style={{display:"inline-flex",alignItems:"center",justifyContent:"center",width:34,height:34,borderRadius:8,background:"linear-gradient(135deg,#7c3aed,#6d28d9)",color:"#fff",fontWeight:800,fontSize:14}}>{m.bitNumber}</span>
                        : <span style={{color:"#cbd5e1",fontSize:11}}>—</span>}
                    </td>
                    <td style={{padding:"10px 14px",whiteSpace:"nowrap"}}>
                      <span style={{display:"inline-block",padding:"3px 10px",borderRadius:99,fontSize:10,fontWeight:700,background:"rgba(30,64,175,.1)",color:"#1e40af",whiteSpace:"nowrap"}}>{m.type||"—"}</span>
                    </td>
                    <td style={{padding:"10px 14px"}}>
                      <div style={{display:"flex",gap:4,flexWrap:"wrap"}}>
                        {(m.model||"").split("/").map(x=>x.trim()).filter(Boolean).map(s=>(
                          <span key={s} style={{padding:"2px 9px",borderRadius:99,fontSize:10,fontWeight:700,background:"rgba(22,163,74,.1)",color:"#16a34a"}}>{s}</span>
                        ))}
                        {!m.model && <span style={{color:"#cbd5e1"}}>—</span>}
                      </div>
                    </td>
                    <td style={{padding:"10px 14px",fontFamily:"monospace",fontSize:11,color:"#475569",fontWeight:600}}>{(m.modelName||"").replace(/^TYPE-SERIES:\s*/i,"")}</td>
                    {!readOnly && (
                      <td style={{padding:"10px 14px"}}>
                        <div style={{display:"flex",gap:6}}>
                          <Btn size="sm" onClick={()=>openEdit(m)}>Edit</Btn>
                          <Btn size="sm" variant="danger" onClick={()=>del(m.id)}>Delete</Btn>
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div style={{padding:"8px 14px",fontSize:11,color:"#94a3b8",borderTop:"1px solid #f1f5f9"}}>Showing {filtered.length} of {models.length} models</div>
      </Card>

      {/* Add / Edit Modal */}
      <Modal open={modal} onClose={()=>setModal(false)} title={editing?"Edit Model":"Add New Model"} wide>
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16}}>
          <FF label="Zone *">
            <Select value={form.zoneId}
                    onChange={e=>setForm(f=>({...f,zoneId:e.target.value}))}>
              <option value="">— Select Zone —</option>
              {zones.map(z=>(
                <option key={z.id} value={z.id}>
                  {z.zone_name}{z.zone_code ? ` (${z.zone_code})` : ""}
                </option>
              ))}
            </Select>
            <div style={{fontSize:10,color:"#94a3b8",marginTop:4}}>
              Bit numbers are unique <b>within a zone</b> — same bit can repeat across zones.
            </div>
          </FF>

          <FF label="Bit Number *">
            <Input
              type="number"
              value={form.bitNumber}
              onChange={e=>setForm(f=>({...f,bitNumber:e.target.value}))}
              placeholder="1, 2, 3..."
              min="1"
              disabled={!form.zoneId}
            />
            {form.bitNumber && usedBits.has(parseInt(form.bitNumber)) && (!editing || editing.bitNumber!==parseInt(form.bitNumber) || String(editing.zoneId||"") !== String(form.zoneId)) && (
              <div style={{fontSize:10,color:"#dc2626",marginTop:4,fontWeight:600}}>Bit {form.bitNumber} already used in this zone!</div>
            )}
          </FF>

          <FF label="Type *">
            <Input
              list="pymodel-type-options"
              value={form.type}
              onChange={e=>setForm(f=>({...f,type:e.target.value}))}
              placeholder={form.zoneId ? "select or type (manual)…" : "select a zone first"}
              disabled={!form.zoneId}
            />
            <datalist id="pymodel-type-options">
              {zoneTypeOptions.map(t=><option key={t} value={t} />)}
            </datalist>
            <div style={{fontSize:10,color:"#94a3b8",marginTop:4}}>
              Is zone ke types suggest honge — ya naya type khud likh do (manual).
            </div>
          </FF>

          <FF label="Series * (pick one or more)" style={{gridColumn:"1/-1"}}>
            {series.length===0 ? (
              <div style={{fontSize:12,color:"#dc2626",padding:12,border:"1px dashed #fecaca",borderRadius:8,background:"rgba(220,38,38,.04)"}}>
                Add series in the <b>Series Master</b> section above first.
              </div>
            ) : (
              <div style={{display:"flex",gap:8,flexWrap:"wrap"}}>
                {series.map(s=>{
                  const on = form.selSeries.includes(s.code);
                  return (
                    <button
                      key={s.id}
                      type="button"
                      onClick={()=>toggleSeries(s.code)}
                      style={{
                        padding:"7px 14px",borderRadius:99,fontSize:12,fontWeight:700,cursor:"pointer",
                        transition:"all .12s",letterSpacing:".04em",
                        border: on ? "1.5px solid #16a34a" : "1px solid #e2e8f0",
                        background: on ? "rgba(22,163,74,.12)" : "#fff",
                        color: on ? "#16a34a" : "#475569",
                      }}
                    >
                      {on ? "✓ " : ""}{s.code}
                    </button>
                  );
                })}
              </div>
            )}
          </FF>

          <FF label="Model Name *" style={{gridColumn:"1/-1"}}>
            <Input
              value={form.modelName}
              onChange={e=>setForm(f=>({...f, modelName: e.target.value}))}
              placeholder="e.g. TRACK ASSY FRONT SEAT YNC 4 WAY INR LH"
              style={{fontFamily:"monospace",fontWeight:600}}
            />
            <div style={{fontSize:10,color:"#94a3b8",marginTop:4}}>
              Jab PLC is bit ko trigger karega, yehi naam Fullscreen par "Model No. {form.bitNumber||'#'}: {form.modelName||'—'}" format mein show hoga.
            </div>
          </FF>
        </div>
        <ModalActions>
          <Btn onClick={()=>setModal(false)}>Cancel</Btn>
          <Btn variant="primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : editing ? "Update Model" : "Add Model"}
          </Btn>
        </ModalActions>
      </Modal>
    </div>
  );
}

// ─── USERS PAGE ───────────────────────────────────────────────
// "3h ago" beats a timestamp in the hierarchy tree — the question there is
// "is this account still in use", not exactly when.
function fmtRel(ts) {
  if (!ts) return "never signed in";
  const diff = (Date.now() - new Date(ts).getTime()) / 1000;
  if (!isFinite(diff)) return "—";
  if (diff < 3600)   return `${Math.max(1, Math.floor(diff / 60))}m ago`;
  if (diff < 86400)  return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const ROLE_PILL = {
  admin:      { bg:"rgba(30,64,175,.10)",  fg:"#1e40af" },
  plant_head: { bg:"rgba(30,64,175,.10)",  fg:"#1e40af" },
  department: { bg:"rgba(220,38,38,.10)",  fg:"#dc2626" },
  production: { bg:"rgba(22,163,74,.10)",  fg:"#16a34a" },
  operator:   { bg:"rgba(124,58,237,.10)", fg:"#6d28d9" },
  // 2026-08-14 — the shopfloor supervisor roles.  Tinted next to the team
  // they own so the users table reads at a glance: production green,
  // quality amber, section slate.
  section_incharge:    { bg:"rgba(71,85,105,.12)",  fg:"#475569" },
  production_incharge: { bg:"rgba(5,150,105,.12)",  fg:"#047857" },
  quality_incharge:    { bg:"rgba(217,119,6,.12)",  fg:"#b45309" },
  // 2026-09-13 — shift-compile roll-up hierarchy roles.
  leader:              { bg:"rgba(124,58,237,.12)", fg:"#6d28d9" },
  shift_incharge:      { bg:"rgba(2,132,199,.12)",  fg:"#0369a1" },
};

// Human labels for the role dropdowns / pills.  Keys must match VALID_ROLES
// in Phase2/routers/users.py.
const ROLE_OPTIONS = [
  { value: "production",          label: "Production" },
  { value: "operator",            label: "Operator" },
  { value: "leader",              label: "Leader" },
  { value: "shift_incharge",      label: "Shift Incharge" },
  { value: "department",          label: "Department" },
  { value: "section_incharge",    label: "Section Incharge" },
  { value: "production_incharge", label: "Production Incharge" },
  { value: "quality_incharge",    label: "Quality Incharge" },
  { value: "plant_head",          label: "Plant Head (admin-equivalent)" },
  { value: "admin",               label: "Admin" },
];

// Master list of pages admins can grant per-user permissions on.
// Grouped by area for the permission matrix modal.  page_key MUST
// match the canAccess() keys in AuthContext.jsx so explicit overrides
// resolve correctly.
// 2026-08-14 — this registry was 21 pages while SlideNav gated 32, so twelve
// live pages (Shift Allocation, Store, Dispatch, 5S, PDCA, Network …) could be
// SEEN by a role default but never granted or revoked per user — the same gap
// that was fixed for "comments-history" on 2026-08-08, just wider.  Every nav
// key is listed below now.  Deliberately still absent: "kanban" and "heijunka",
// which App.jsx hard-redirects to /dashboard, so a toggle for them would
// control a page no one can reach.
const PAGE_PERM_GROUPS = [
  { group: "Production", items: [
    { key: "dashboard",         label: "Production Dashboard" },
    { key: "historical",        label: "Historical Data" },
    { key: "import",            label: "Import / Export" },
    { key: "process-graphs",    label: "Process Graphs" },
    { key: "shift-allocation",  label: "Shift Allocation" },
    { key: "shift-compile",     label: "Shift Compile" },
    { key: "shift-calculator",  label: "Shift Calculator" },
    { key: "operators",         label: "Employee Master" },
    { key: "prod-breakdown-slip", label: "Breakdown Slip (Production)" },
    { key: "my-escalations",    label: "My Escalations" },
    { key: "store",             label: "Store" },
    { key: "dispatch",          label: "Dispatch" },
    { key: "waiting-time",      label: "Waiting Time" },
    { key: "admin-production",  label: "Admin → Production Panel" },
  ]},
  { group: "Improvement / Shopfloor", items: [
    { key: "anything-wrong",    label: "Anything Wrong?" },
    { key: "five-s",            label: "5S Audit" },
    { key: "pdca",              label: "PDCA / A3" },
  ]},
  { group: "Maintenance", items: [
    { key: "maintenance-dashboard",  label: "Maintenance Dashboard" },
    { key: "maintenance-historical", label: "Maintenance Historical Data" },
    { key: "maintenance-capa",       label: "Maintenance CAPA" },
    { key: "maintenance-deviations", label: "Maintenance Deviations" },
    { key: "maintenance-poka-yoke",  label: "Maintenance Poka Yoke" },
    { key: "maintenance-logbook",    label: "Maintenance Log Book" },
    { key: "maintenance-pm",         label: "Preventive Maintenance" },
    { key: "andon-history",          label: "Andon History" },
    { key: "admin-maintenance",      label: "Admin → Maintenance Panel" },
  ]},
  { group: "Quality", items: [
    { key: "quality-dashboard",  label: "Quality Dashboard" },
    { key: "quality-deviations", label: "Quality Deviation" },
    // 2026-08-08 — was missing from this registry, so admins had no way to
    // grant/revoke it even though SlideNav already filters the nav item on
    // canAccess("comments-history").  Listing it here is what makes the
    // per-user toggle appear in the Page Permissions dialog.
    { key: "comments-history",   label: "Comments History" },
    { key: "sa-fi-history",      label: "SA / FI History" },
    { key: "weld-monitor",       label: "Weld Monitor" },
    { key: "admin-quality",      label: "Admin → Quality Panel" },
  ]},
  { group: "System", items: [
    { key: "department-panel",   label: "Department Panel" },
    { key: "escalation-admin",   label: "Escalation Setup (per-zone chain)" },
    { key: "settings",           label: "Settings" },
    { key: "network",            label: "Network Panel" },
    { key: "logs",              label: "Log Viewer" },
    { key: "video-coverage",    label: "Video Coverage (clips per cycle + Video Agent)" },
    { key: "py-bypass",         label: "PY Bypass (quality approve/reject + machine bit)" },
    { key: "audit",              label: "Audit Log" },
    { key: "admin",              label: "Admin Core (System Map / Departments / Users)" },
  ]},
];

const PERM_LEVELS = [
  { key: "none", label: "No Access",  bg: "#fee2e2", color: "#b91c1c" },
  { key: "read", label: "Read-only",  bg: "#fef3c7", color: "#a16207" },
  { key: "full", label: "Full CRUD",  bg: "#dcfce7", color: "#15803d" },
];


export function UsersPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [users,       setUsers]       = useState([]);
  const [lines,       setLines]       = useState([]);
  const [departments, setDepartments] = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [modal,       setModal]       = useState(false);
  const [assignModal, setAssignModal] = useState(null);
  const [form,        setForm]        = useState({
    username:"", password:"", role:"production", department_id:"",
  });
  const [saving,      setSaving]      = useState(false);
  const [selLines,    setSelLines]    = useState([]);

  // Permission matrix state — opened when admin clicks "Permissions"
  // on a user row.  permModal=null means closed; otherwise it holds
  // the user being edited.  permMap is { page_key: 'none'|'read'|'full' }.
  const [permModal,  setPermModal]  = useState(null);
  const [permMap,    setPermMap]    = useState({});
  const [permLoading,setPermLoading]= useState(false);
  const [permSaving, setPermSaving] = useState(false);

  // 2026-08-14 — line / module scope, the second half of the same dialog.
  // scopeMap is keyed "line:12" / "machine:45" -> 'none'|'read'|'full' so one
  // flat object covers both levels of the tree.  catalog is the line→machine
  // tree from /api/users/scope-catalog, fetched once and reused for every user.
  const [permTab,      setPermTab]      = useState("pages");   // 'pages' | 'scope'
  const [scopeMap,     setScopeMap]     = useState({});
  const [catalog,      setCatalog]      = useState([]);
  const [expandedLines,setExpandedLines]= useState({});

  // Department hierarchy — read-only roll-up of who sits where.
  const [hierModal,   setHierModal]   = useState(false);
  const [hierData,    setHierData]    = useState([]);
  const [hierLoading, setHierLoading] = useState(false);

  // 2026-06-18 — admin edit (username / password reset / role) + password reveal.
  // Passwords stored plaintext (password_plain) per admin's explicit record-keeping
  // requirement — bcrypt hash still drives login.
  const [editModal, setEditModal] = useState(null);
  const [editForm,  setEditForm]  = useState({ username:"", password:"", role:"production", department_id:"" });
  const [reveal,    setReveal]    = useState({});

  const load = useCallback(async () => {
    try {
      const [u,l,d] = await Promise.all([
        api.get("/api/users/", token),
        api.get("/api/lines/", token),
        api.get("/api/departments/", token),
      ]);
      setUsers(Array.isArray(u)?u:[]);
      setLines(Array.isArray(l)?l:[]);
      setDepartments(Array.isArray(d)?d:[]);
    } catch { toast("Failed to load","err"); }
    finally { setLoading(false); }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  const createUser = async () => {
    if (!form.username||!form.password) { toast("Username and password required","err"); return; }
    if (form.role === "department" && !form.department_id) {
      toast("Pick a department for this user","err"); return;
    }
    setSaving(true);
    try {
      const body = {
        username: form.username,
        password: form.password,
        role:     form.role,
        department_id: form.role === "department" ? Number(form.department_id) : null,
      };
      await api.post("/api/users/", body, token);
      toast("User created ✓");
      setModal(false);
      setForm({ username:"", password:"", role:"production", department_id:"" });
      load();
    }
    catch(e) { toast(e.message,"err"); }
    finally { setSaving(false); }
  };

  const deleteUser = async (u) => {
    if (!confirm(`Delete user "${u.username}"?`)) return;
    try { await api.delete(`/api/users/${u.id}`, token); toast("User deleted"); load(); }
    catch(e) { toast(e.message,"err"); }
  };

  const patchUser = async (u, patch) => {
    try { await api.put(`/api/users/${u.id}/role`, patch, token); toast("Updated ✓"); load(); }
    catch(e) { toast(e.message,"err"); }
  };

  const changeRole = (u, role) => {
    // Switching to 'department' needs a dept_id — pick the first available one
    // as a sensible default; admin can change immediately via the dept dropdown.
    if (role === "department") {
      if (!departments.length) {
        toast("Add a department first (Admin → Departments)","err"); return;
      }
      patchUser(u, { role, department_id: departments[0].id });
    } else {
      patchUser(u, { role });
    }
  };
  const changeDept = (u, dept_id) => {
    patchUser(u, { department_id: dept_id ? Number(dept_id) : null });
  };

  const openAssign = async (u) => {
    const assigned = await api.get(`/api/users/${u.id}/lines`, token).catch(()=>[]);
    setSelLines(Array.isArray(assigned)?assigned:[]);
    setAssignModal(u);
  };

  const saveAssign = async () => {
    if (!assignModal) return;
    setSaving(true);
    try { await api.put(`/api/users/${assignModal.id}/lines`, selLines, token); toast("Lines assigned ✓"); setAssignModal(null); }
    catch(e) { toast(e.message,"err"); }
    finally { setSaving(false); }
  };

  const toggleLine = (id) => setSelLines(p=>p.includes(id)?p.filter(x=>x!==id):[...p,id]);
  // Assign a WHOLE zone at once — ticks/unticks every line in that zone
  // together (writes the same mes_operator_lines rows as the per-line boxes).
  const toggleZone = (zoneLineIds) => setSelLines(p => {
    const allOn = zoneLineIds.length > 0 && zoneLineIds.every(id => p.includes(id));
    return allOn ? p.filter(id => !zoneLineIds.includes(id))
                 : [...new Set([...p, ...zoneLineIds])];
  });

  // ── Permission matrix handlers ──
  // Loads BOTH halves of a user's access in one go: which pages they may open
  // (permMap) and which lines / machines they may see on those pages
  // (scopeMap).  They are saved together too — an admin who sets one and not
  // the other has almost certainly not finished the thought.
  const openPerms = async (u) => {
    setPermModal(u);
    setPermTab("pages");
    setPermLoading(true);
    setPermMap({});
    setScopeMap({});
    setExpandedLines({});
    try {
      const [rows, scope, cat] = await Promise.all([
        api.get(`/api/users/${u.id}/permissions`, token),
        api.get(`/api/users/${u.id}/scope`, token).catch(()=>[]),
        catalog.length ? Promise.resolve(catalog)
                       : api.get(`/api/users/scope-catalog`, token).catch(()=>[]),
      ]);
      const m = {};
      for (const r of (Array.isArray(rows) ? rows : [])) {
        m[r.page_key] = r.perm_level;
      }
      setPermMap(m);
      const s = {};
      for (const r of (Array.isArray(scope) ? scope : [])) {
        s[`${r.scope_type}:${r.scope_id}`] = r.perm_level;
      }
      setScopeMap(s);
      if (Array.isArray(cat) && cat.length) setCatalog(cat);
    } catch { toast?.("Failed to load permissions","err"); }
    finally   { setPermLoading(false); }
  };

  const setPerm = (page_key, level) => {
    setPermMap(p => ({ ...p, [page_key]: level }));
  };

  const setAllInGroup = (groupItems, level) => {
    setPermMap(p => {
      const n = { ...p };
      for (const it of groupItems) n[it.key] = level;
      return n;
    });
  };

  // 2026-09-13 — module (sub-tab) show/hide.  Showing a module implies the
  // parent page must be accessible, so grant it Read if it isn't already.
  const setModulePerm = (page_key, mod_key, level) => {
    setPermMap(p => {
      const n = { ...p, [`${page_key}::${mod_key}`]: level };
      if (level !== "none" && n[page_key] !== "read" && n[page_key] !== "full") {
        n[page_key] = "read";
      }
      return n;
    });
  };
  // Reset a module back to inherit (remove its explicit setting).
  const clearModulePerm = (page_key, mod_key) => {
    setPermMap(p => { const n = { ...p }; delete n[`${page_key}::${mod_key}`]; return n; });
  };

  // ── Line / module scope handlers ──
  const scopeOf = (type, id) => scopeMap[`${type}:${id}`] || "none";

  // Setting a LINE cascades to its machines: picking "Full CRUD" on YCA-SS and
  // then having to click seven machines individually is how a 128-toggle screen
  // becomes unusable.  Individual machines can still be dialled back after.
  const setLineScope = (line, level) => {
    setScopeMap(p => {
      const n = { ...p, [`line:${line.id}`]: level };
      for (const mc of line.machines) n[`machine:${mc.id}`] = level;
      return n;
    });
  };

  const setMachineScope = (machine, level) => {
    setScopeMap(p => ({ ...p, [`machine:${machine.id}`]: level }));
  };

  const setAllLines = (level) => {
    setScopeMap(() => {
      const n = {};
      for (const l of catalog) {
        n[`line:${l.id}`] = level;
        for (const mc of l.machines) n[`machine:${mc.id}`] = level;
      }
      return n;
    });
  };

  // How many machines under a line are actually granted — drives the little
  // "3 / 7" counter so a collapsed line still tells you what is inside it.
  const grantedCount = (line) =>
    line.machines.filter(mc => scopeOf("machine", mc.id) !== "none").length;

  const savePerms = async () => {
    if (!permModal) return;
    setPermSaving(true);
    try {
      const payload = {
        permissions: Object.entries(permMap).map(([page_key, perm_level]) => ({
          page_key, perm_level,
        })),
      };
      await api.put(`/api/users/${permModal.id}/permissions`, payload, token);

      // Admin users are global by design and the API rejects scoping them, so
      // only send the scope for everyone else.
      if (permModal.role !== "admin") {
        const scopes = Object.entries(scopeMap).map(([k, perm_level]) => {
          const [scope_type, scope_id] = k.split(":");
          return { scope_type, scope_id: Number(scope_id), perm_level };
        });
        await api.put(`/api/users/${permModal.id}/scope`, { scopes }, token);
      }
      toast?.("Permissions saved ✓");
      setPermModal(null);
    } catch (e) { toast?.(e.message || "Save failed", "err"); }
    finally   { setPermSaving(false); }
  };

  // ── Edit user (username / password reset / role) ──
  const openEdit = (u) => {
    setEditForm({ username:u.username, password:u.password_plain||"",
                  role:u.role, department_id:u.department_id||"" });
    setEditModal(u);
  };
  const saveEdit = async () => {
    if (!editModal) return;
    const isAdmin = editModal.username === "admin";
    setSaving(true);
    try {
      const body = {};
      if (!isAdmin && editForm.username && editForm.username !== editModal.username) body.username = editForm.username.trim();
      if (editForm.password) body.password = editForm.password;
      if (!isAdmin) {
        body.role = editForm.role;
        if (editForm.role === "department") body.department_id = editForm.department_id ? Number(editForm.department_id) : null;
      }
      await api.put(`/api/users/${editModal.id}`, body, token);
      toast("User updated ✓");
      setEditModal(null);
      load();
    } catch(e){ toast(e.message,"err"); }
    finally { setSaving(false); }
  };
  const toggleReveal = (id) => setReveal(p => ({ ...p, [id]: !p[id] }));

  const openHierarchy = async () => {
    setHierModal(true);
    setHierLoading(true);
    try {
      const d = await api.get("/api/users/hierarchy", token);
      setHierData(Array.isArray(d) ? d : []);
    } catch (e) { toast?.(e.message || "Failed to load hierarchy", "err"); }
    finally { setHierLoading(false); }
  };

  return (
    <div>
      <div style={{ display:"flex", justifyContent:"flex-end", gap:10, marginBottom:20 }}>
        <Btn onClick={openHierarchy}>🏢 Department Hierarchy</Btn>
        <Btn variant="primary" onClick={()=>setModal(true)}>+ Add User</Btn>
      </div>
      <Card>
        {loading ? <Spinner /> : users.length===0 ? <EmptyState text="No users" /> : (
          <table style={{ width:"100%", borderCollapse:"collapse", fontSize:13 }}>
            <thead>
              <tr>{["ID","Username","Role","Department","Password","Last Login","Actions"].map(h=>(
                <th key={h} style={{ padding:"10px 14px", textAlign:"left", fontSize:10, fontWeight:700, letterSpacing:".08em", textTransform:"uppercase", color:"#64748b", borderBottom:"2px solid #e2e8f0" }}>{h}</th>
              ))}</tr>
            </thead>
            <tbody>
              {users.map(u=>{
                const rp = ROLE_PILL[u.role] || {};
                return (
                <tr key={u.id} style={{ borderBottom:"1px solid #f1f5f9" }}>
                  <td style={{ padding:"12px 14px", fontFamily:"monospace", color:"#64748b" }}>{u.id}</td>
                  <td style={{ padding:"12px 14px", fontWeight:600, color:"#0f172a" }}>{u.username}</td>
                  <td style={{ padding:"12px 14px" }}>
                    {u.username==="admin"
                      ? <span style={{ padding:"3px 9px", borderRadius:99, fontSize:10, fontWeight:700, background:rp.bg||"#f1f5f9", color:rp.fg||"#475569", textTransform:"uppercase", letterSpacing:".05em" }}>admin</span>
                      : (
                        <select value={u.role} onChange={e=>changeRole(u,e.target.value)}
                                style={{ ...inputStyle, padding:"4px 8px", fontSize:12, width:"auto",
                                         ...(rp.bg ? { background: rp.bg, color: rp.fg, fontWeight:700 } : {}) }}>
                          {ROLE_OPTIONS.map(r => (
                            <option key={r.value} value={r.value}>{r.label}</option>
                          ))}
                        </select>
                      )
                    }
                  </td>
                  <td style={{ padding:"12px 14px" }}>
                    {u.role === "department" ? (
                      <select value={u.department_id || ""}
                              onChange={e => changeDept(u, e.target.value)}
                              style={{ ...inputStyle, padding:"4px 8px", fontSize:11, width:"auto" }}>
                        <option value="" disabled>— pick —</option>
                        {departments.map(d => (
                          <option key={d.id} value={d.id}>{d.name}</option>
                        ))}
                      </select>
                    ) : (
                      <span style={{ color:"#cbd5e1" }}>—</span>
                    )}
                  </td>
                  <td style={{ padding:"12px 14px", fontFamily:"monospace", fontSize:12 }}>
                    {u.password_plain
                      ? (reveal[u.id]
                          ? <span><span style={{ color:"#0f172a" }}>{u.password_plain}</span>
                              <button onClick={()=>toggleReveal(u.id)} title="hide"
                                style={{ marginLeft:6, border:"none", background:"none", cursor:"pointer" }}>🙈</button></span>
                          : <span><span style={{ color:"#94a3b8", letterSpacing:2 }}>••••••</span>
                              <button onClick={()=>toggleReveal(u.id)} title="show"
                                style={{ marginLeft:6, border:"none", background:"none", cursor:"pointer" }}>👁</button></span>)
                      : <span style={{ color:"#cbd5e1", fontFamily:"'Barlow',sans-serif", fontSize:11 }}
                              title="bcrypt hash — original recover nahi hota; Edit se naya set karo">— (reset to record)</span>}
                  </td>
                  <td style={{ padding:"12px 14px", fontFamily:"monospace", fontSize:11, color:"#64748b" }}>{u.last_login?new Date(u.last_login).toLocaleString("en-IN"):"Never"}</td>
                  <td style={{ padding:"12px 14px" }}>
                    <div style={{ display:"flex", gap:8 }}>
                      <Btn size="sm" onClick={()=>openEdit(u)}>Edit</Btn>
                      {/* 2026-08-14 — was `u.role==="operator"`, which is why a
                          production / department / incharge user could not be
                          tied to a single line from this screen.  The backend
                          has allowed ANY non-admin role to be line-scoped since
                          2026-07-31 (users.py set_operator_lines) — only this
                          button was never widened, so the capability existed
                          with no way to reach it.  Admins stay global on
                          purpose: an empty assignment means "unrestricted". */}
                      {u.role!=="admin" && <Btn size="sm" onClick={()=>openAssign(u)}>Assign Lines</Btn>}
                      {u.username!=="admin" && <Btn size="sm" onClick={()=>openPerms(u)}>Permissions</Btn>}
                      {u.username!=="admin" && <Btn size="sm" variant="danger" onClick={()=>deleteUser(u)}>Delete</Btn>}
                    </div>
                  </td>
                </tr>
              );})}
            </tbody>
          </table>
        )}
      </Card>

      <Modal open={modal} onClose={()=>setModal(false)} title="Add User">
        <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:16 }}>
          <FF label="Username *"><Input value={form.username} onChange={e=>setForm(f=>({...f,username:e.target.value}))} placeholder="login id"/></FF>
          <FF label="Password *"><Input type="password" value={form.password} onChange={e=>setForm(f=>({...f,password:e.target.value}))} placeholder="password"/></FF>
          <FF label="Role *">
            <Select value={form.role} onChange={e=>setForm(f=>({...f,role:e.target.value, department_id:""}))}>
              {ROLE_OPTIONS.map(r => (
                <option key={r.value} value={r.value}>{r.label}</option>
              ))}
            </Select>
          </FF>
          {form.role === "department" && (
            <FF label="Department *" hint="Maintenance / Quality / etc.  Manage from Admin → Departments.">
              <Select value={form.department_id}
                      onChange={e=>setForm(f=>({...f,department_id:e.target.value}))}>
                <option value="">— pick a department —</option>
                {departments.map(d => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </Select>
            </FF>
          )}
        </div>
        <ModalActions>
          <Btn onClick={()=>setModal(false)}>Cancel</Btn>
          <Btn variant="primary" onClick={createUser} disabled={saving}>{saving?"Creating…":"Create User"}</Btn>
        </ModalActions>
      </Modal>

      {/* ── EDIT USER MODAL — username / password (read+reset) / role ── */}
      <Modal open={!!editModal} onClose={()=>setEditModal(null)}
             title={`Edit User — ${editModal?.username || ""}`}>
        <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:16 }}>
          <FF label="Username" hint={editModal?.username==="admin" ? "built-in admin — locked" : ""}>
            <Input value={editForm.username} disabled={editModal?.username==="admin"}
                   onChange={e=>setEditForm(f=>({...f,username:e.target.value}))}/>
          </FF>
          <FF label="Password" hint="abhi ka password (change kar sakte ho)">
            <Input value={editForm.password} placeholder="naya password set karo"
                   onChange={e=>setEditForm(f=>({...f,password:e.target.value}))}/>
          </FF>
          {editModal?.username!=="admin" && (
            <FF label="Role">
              <Select value={editForm.role}
                      onChange={e=>setEditForm(f=>({...f,role:e.target.value, department_id:""}))}>
                {ROLE_OPTIONS.map(r => (
                  <option key={r.value} value={r.value}>{r.label}</option>
                ))}
              </Select>
            </FF>
          )}
          {editModal?.username!=="admin" && editForm.role==="department" && (
            <FF label="Department *" hint="Manage from Admin → Departments.">
              <Select value={editForm.department_id}
                      onChange={e=>setEditForm(f=>({...f,department_id:e.target.value}))}>
                <option value="">— pick a department —</option>
                {departments.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
              </Select>
            </FF>
          )}
        </div>
        <ModalActions>
          <Btn onClick={()=>setEditModal(null)}>Cancel</Btn>
          <Btn variant="primary" onClick={saveEdit} disabled={saving}>{saving?"Saving…":"Save"}</Btn>
        </ModalActions>
      </Modal>

      {/* ── PERMISSION MATRIX MODAL ──────────────────────────────────
          Per-user, per-page access control.  Each row in the matrix
          is one page; admin picks None / Read-only / Full CRUD.
          Pages absent from the saved set fall back to role defaults. */}
      <Modal open={!!permModal} onClose={()=>setPermModal(null)}
              title={`Access — ${permModal?.username || ""}`} wide>
        <div style={{ display:"flex", gap:6, marginBottom:12,
                       borderBottom:"2px solid #e2e8f0" }}>
          {[{k:"pages", t:"Pages", n:Object.values(permMap).filter(v=>v!=="none").length},
            {k:"scope", t:"Lines & Modules",
             n:Object.values(scopeMap).filter(v=>v!=="none").length}].map(tb => (
            <button key={tb.k} onClick={()=>setPermTab(tb.k)}
                    style={{
                      padding:"8px 16px", border:"none", cursor:"pointer",
                      background:"none", fontSize:13,
                      fontWeight: permTab===tb.k ? 800 : 600,
                      color:      permTab===tb.k ? "#1e40af" : "#94a3b8",
                      borderBottom: permTab===tb.k ? "3px solid #1e40af" : "3px solid transparent",
                      marginBottom:-2,
                    }}>
              {tb.t}{tb.n ? ` (${tb.n})` : ""}
            </button>
          ))}
        </div>

        <div style={{ fontSize:12, color:"#475569", marginBottom:14, lineHeight:1.5 }}>
          {permTab === "pages" ? (
            <>Choose which pages this user can see and the level of access for
            each.  <b>None</b> hides the page; <b>Read-only</b> shows it but
            blocks Save / Edit / Delete; <b>Full CRUD</b> gives complete access.
            <br/><b>Once you grant any page here (Read-only or Full), this becomes
            the user's COMPLETE list</b> — every other page stays hidden, even if
            their role would show it by default.  A user with nothing granted here
            keeps their role defaults.</>
          ) : (
            <>Which <b>lines</b> and <b>machines</b> this user sees on every page
            above.  Setting a line applies its level to all machines under it —
            adjust individual machines after if needed.
            {" "}<b>Leaving everything on None means unrestricted</b> (the user
            sees all lines), which is how existing accounts behave today.</>
          )}
        </div>

        {permLoading ? <Spinner/> : permTab === "pages" ? (
          <div style={{ maxHeight:"60vh", overflowY:"auto" }}>
            {PAGE_PERM_GROUPS.map(g => (
              <div key={g.group} style={{ marginBottom:18 }}>
                <div style={{
                  display:"flex", justifyContent:"space-between", alignItems:"center",
                  padding:"6px 0", marginBottom:6,
                  borderBottom:"2px solid #e2e8f0",
                }}>
                  <div style={{ fontSize:11, fontWeight:800, letterSpacing:".08em",
                                  textTransform:"uppercase", color:"#0f172a" }}>
                    {g.group}
                  </div>
                  <div style={{ display:"flex", gap:6 }}>
                    {PERM_LEVELS.map(p => (
                      <button key={p.key}
                              onClick={() => setAllInGroup(g.items, p.key)}
                              style={{
                                fontSize:9, fontWeight:700, padding:"3px 9px",
                                borderRadius:99, border:"none",
                                background:p.bg, color:p.color, cursor:"pointer",
                              }}
                              title={`Set all ${g.group} pages to ${p.label}`}>
                        ALL → {p.label}
                      </button>
                    ))}
                  </div>
                </div>
                {g.items.map(it => {
                  const cur = permMap[it.key] || "none";
                  return (
                    <Fragment key={it.key}>
                    <div style={{
                      display:"grid",
                      gridTemplateColumns:"1fr auto auto auto",
                      gap:8, alignItems:"center",
                      padding:"6px 0",
                      borderBottom:"1px solid #f1f5f9",
                    }}>
                      <div>
                        <div style={{ fontSize:13, fontWeight:600, color:"#0f172a" }}>
                          {it.label}
                        </div>
                        <div style={{ fontSize:10, color:"#94a3b8",
                                       fontFamily:"monospace" }}>
                          {it.key}
                        </div>
                      </div>
                      {PERM_LEVELS.map(p => {
                        const sel = cur === p.key;
                        return (
                          <button key={p.key}
                                  onClick={() => setPerm(it.key, p.key)}
                                  style={{
                                    padding:"5px 12px", borderRadius:7, fontSize:11,
                                    fontWeight:700, cursor:"pointer",
                                    border: sel ? `2px solid ${p.color}` : "1.5px solid #e2e8f0",
                                    background: sel ? p.bg : "#fff",
                                    color:      sel ? p.color : "#94a3b8",
                                    minWidth: 90,
                                  }}>
                            {p.label}
                          </button>
                        );
                      })}
                    </div>
                    {pageHasModules(it.key) && (() => {
                      const mods = PAGE_MODULES[it.key];
                      const anyGrant = mods.some(m => ["read","full"].includes(permMap[moduleKey(it.key, m.key)]));
                      return mods.map(m => {
                        const mk = moduleKey(it.key, m.key);
                        const mv = permMap[mk];
                        const shown = mv ? (mv !== "none") : !anyGrant;
                        return (
                          <div key={mk} style={{ display:"grid", gridTemplateColumns:"1fr auto auto auto",
                                gap:8, alignItems:"center", padding:"4px 0 4px 22px",
                                borderBottom:"1px solid #f8fafc", background:"#fbfdff" }}>
                            <div style={{ display:"flex", alignItems:"center", gap:6, flexWrap:"wrap" }}>
                              <span style={{ color:"#cbd5e1", fontSize:13 }}>&#8627;</span>
                              <span style={{ fontSize:12.5, fontWeight:600, color:"#334155" }}>{m.label}</span>
                              {!mv && <span style={{ fontSize:9.5, color:"#94a3b8" }}>
                                ({anyGrant ? "hidden" : "shown"} · default)</span>}
                            </div>
                            <button onClick={()=>setModulePerm(it.key, m.key, "full")}
                              style={{ padding:"4px 12px", borderRadius:7, fontSize:11, fontWeight:700,
                                       cursor:"pointer", minWidth:70,
                                       border: shown ? "2px solid #15803d" : "1.5px solid #e2e8f0",
                                       background: shown ? "#dcfce7" : "#fff",
                                       color: shown ? "#15803d" : "#94a3b8" }}>Show</button>
                            <button onClick={()=>setModulePerm(it.key, m.key, "none")}
                              style={{ padding:"4px 12px", borderRadius:7, fontSize:11, fontWeight:700,
                                       cursor:"pointer", minWidth:70,
                                       border: (mv==="none") ? "2px solid #b91c1c" : "1.5px solid #e2e8f0",
                                       background: (mv==="none") ? "#fee2e2" : "#fff",
                                       color: (mv==="none") ? "#b91c1c" : "#94a3b8" }}>Hide</button>
                            <button onClick={()=>clearModulePerm(it.key, m.key)} title="Reset to default"
                              style={{ padding:"4px 8px", borderRadius:7, fontSize:11, fontWeight:600,
                                       cursor:"pointer", minWidth:70, border:"1.5px solid #e2e8f0",
                                       background:"#fff", color:"#94a3b8" }}>Default</button>
                          </div>
                        );
                      });
                    })()}
                    </Fragment>
                  );
                })}
              </div>
            ))}
          </div>
        ) : (
          <div style={{ maxHeight:"60vh", overflowY:"auto" }}>
            <div style={{ display:"flex", justifyContent:"space-between",
                           alignItems:"center", padding:"6px 0", marginBottom:8,
                           borderBottom:"2px solid #e2e8f0" }}>
              <div style={{ fontSize:11, fontWeight:800, letterSpacing:".08em",
                             textTransform:"uppercase", color:"#0f172a" }}>
                {catalog.length} Lines · {catalog.reduce((a,l)=>a+l.machines.length,0)} Machines
              </div>
              <div style={{ display:"flex", gap:6 }}>
                {PERM_LEVELS.map(p => (
                  <button key={p.key} onClick={() => setAllLines(p.key)}
                          style={{ fontSize:9, fontWeight:700, padding:"3px 9px",
                                    borderRadius:99, border:"none",
                                    background:p.bg, color:p.color, cursor:"pointer" }}
                          title={`Set every line and machine to ${p.label}`}>
                    ALL → {p.label}
                  </button>
                ))}
              </div>
            </div>

            {catalog.length === 0 && (
              <div style={{ fontSize:12, color:"#94a3b8", padding:"14px 0" }}>
                No lines found.
              </div>
            )}

            {catalog.map(line => {
              const lvl  = scopeOf("line", line.id);
              const open = !!expandedLines[line.id];
              const got  = grantedCount(line);
              return (
                <div key={line.id} style={{ marginBottom:4 }}>
                  <div style={{
                    display:"grid",
                    gridTemplateColumns:"auto 1fr auto auto auto",
                    gap:8, alignItems:"center",
                    padding:"7px 0", borderBottom:"1px solid #f1f5f9",
                  }}>
                    <button onClick={()=>setExpandedLines(p=>({...p,[line.id]:!open}))}
                            style={{ border:"none", background:"none", cursor:"pointer",
                                      fontSize:11, color:"#64748b", width:20 }}
                            title={open ? "Collapse" : "Expand machines"}>
                      {line.machines.length ? (open ? "▼" : "▶") : "·"}
                    </button>
                    <div>
                      <div style={{ fontSize:13, fontWeight:700, color:"#0f172a" }}>
                        {line.line_name}
                      </div>
                      <div style={{ fontSize:10, color:"#94a3b8", fontFamily:"monospace" }}>
                        {line.line_code} · {got}/{line.machines.length} machines
                      </div>
                    </div>
                    {PERM_LEVELS.map(p => {
                      const sel = lvl === p.key;
                      return (
                        <button key={p.key} onClick={() => setLineScope(line, p.key)}
                                style={{
                                  padding:"5px 12px", borderRadius:7, fontSize:11,
                                  fontWeight:700, cursor:"pointer",
                                  border: sel ? `2px solid ${p.color}` : "1.5px solid #e2e8f0",
                                  background: sel ? p.bg : "#fff",
                                  color:      sel ? p.color : "#94a3b8",
                                  minWidth: 90,
                                }}>
                          {p.label}
                        </button>
                      );
                    })}
                  </div>

                  {open && line.machines.map(mc => {
                    const mlvl = scopeOf("machine", mc.id);
                    return (
                      <div key={mc.id} style={{
                        display:"grid",
                        gridTemplateColumns:"auto 1fr auto auto auto",
                        gap:8, alignItems:"center",
                        padding:"5px 0 5px 0",
                        background:"#f8fafc",
                        borderBottom:"1px solid #f1f5f9",
                      }}>
                        <div style={{ width:20 }}/>
                        <div style={{ paddingLeft:14, fontSize:12, color:"#475569" }}>
                          ↳ {mc.name}
                          <span style={{ fontSize:10, color:"#cbd5e1",
                                          fontFamily:"monospace", marginLeft:6 }}>
                            #{mc.id}
                          </span>
                        </div>
                        {PERM_LEVELS.map(p => {
                          const sel = mlvl === p.key;
                          return (
                            <button key={p.key} onClick={() => setMachineScope(mc, p.key)}
                                    style={{
                                      padding:"3px 10px", borderRadius:6, fontSize:10,
                                      fontWeight:700, cursor:"pointer",
                                      border: sel ? `2px solid ${p.color}` : "1.5px solid #e2e8f0",
                                      background: sel ? p.bg : "#fff",
                                      color:      sel ? p.color : "#cbd5e1",
                                      minWidth: 90,
                                    }}>
                              {p.label}
                            </button>
                          );
                        })}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        )}

        <ModalActions>
          <Btn onClick={()=>setPermModal(null)}>Cancel</Btn>
          <Btn variant="primary" onClick={savePerms} disabled={permSaving}>
            {permSaving ? "Saving…" : "Save Permissions"}
          </Btn>
        </ModalActions>
      </Modal>

      <Modal open={hierModal} onClose={()=>setHierModal(false)}
              title="Department Hierarchy" wide>
        <div style={{ fontSize:12, color:"#475569", marginBottom:14, lineHeight:1.5 }}>
          Every department and the accounts under it — which id belongs to whom,
          what each one is scoped to, and how many pages were explicitly set.
          Accounts with no department land in <b>Unassigned</b>, which is where a
          half-configured login hides.
        </div>
        {hierLoading ? <Spinner/> : (
          <div style={{ maxHeight:"64vh", overflow:"auto", background:"#f8fafc",
                         border:"1px solid #e2e8f0", borderRadius:10, padding:"18px 20px" }}>
            {hierData.length === 0 && <EmptyState text="Nothing to show"/>}

            {/* Tree canvas.  Elbow connectors are drawn with borders on a
                spacer column rather than SVG, so the whole thing reflows with
                the text and stays readable at any zoom. */}
            {hierData.map(d => {
              const unassigned = d.id === null;
              const accent = unassigned ? "#b45309" : "#1e40af";
              return (
                <div key={d.slug} style={{ marginBottom:22 }}>
                  {/* department node */}
                  <div style={{ display:"inline-flex", alignItems:"center", gap:8,
                                 background:"#fff", border:`2px solid ${accent}`,
                                 borderRadius:10, padding:"8px 14px",
                                 boxShadow:"0 1px 3px rgba(15,23,42,.06)" }}>
                    <span style={{ fontSize:15 }}>{unassigned ? "⚠️" : "🏢"}</span>
                    <span style={{ fontSize:13, fontWeight:800, color:accent,
                                    letterSpacing:".03em", textTransform:"uppercase" }}>
                      {d.name}
                    </span>
                    <span style={{ fontSize:10, color:"#94a3b8", fontFamily:"monospace" }}>
                      {!unassigned && `#${d.id}`}
                    </span>
                    <span style={{ fontSize:10, fontWeight:700, color:"#fff", background:accent,
                                    borderRadius:99, padding:"1px 8px" }}>
                      {d.users.length}
                    </span>
                  </div>

                  {d.users.length === 0 && (
                    <div style={{ marginLeft:26, paddingLeft:20, paddingTop:8,
                                   borderLeft:"2px dashed #cbd5e1",
                                   fontSize:12, color:"#cbd5e1" }}>
                      no users
                    </div>
                  )}

                  {d.users.map((u, i) => {
                    const pill = ROLE_PILL[u.role] || { bg:"#f1f5f9", fg:"#64748b" };
                    const unrestricted = u.lines === "ALL (unrestricted)";
                    const last = i === d.users.length - 1;
                    return (
                      <div key={u.id} style={{ display:"flex", marginLeft:26 }}>
                        {/* trunk + elbow */}
                        <div style={{ width:22, position:"relative", flexShrink:0 }}>
                          <div style={{ position:"absolute", left:0, top:0,
                                         width:2, height: last ? 20 : "100%",
                                         background:"#cbd5e1" }}/>
                          <div style={{ position:"absolute", left:0, top:20,
                                         width:20, height:2, background:"#cbd5e1" }}/>
                        </div>

                        {/* user node */}
                        <div style={{ flex:1, margin:"6px 0", background:"#fff",
                                       border:"1px solid #e2e8f0", borderLeft:`3px solid ${pill.fg}`,
                                       borderRadius:8, padding:"8px 12px",
                                       display:"grid",
                                       gridTemplateColumns:"1fr auto",
                                       gap:8, alignItems:"center" }}>
                          <div>
                            <div style={{ display:"flex", alignItems:"center", gap:8, flexWrap:"wrap" }}>
                              <span style={{ fontSize:13, fontWeight:700, color:"#0f172a" }}>
                                {u.username}
                              </span>
                              <span style={{ background:pill.bg, color:pill.fg,
                                              padding:"1px 8px", borderRadius:99,
                                              fontSize:9, fontWeight:800,
                                              textTransform:"uppercase" }}>
                                {u.role}
                              </span>
                              <span style={{ fontSize:10, fontFamily:"monospace", color:"#cbd5e1" }}>
                                id={u.id}
                              </span>
                            </div>
                            <div style={{ fontSize:11, marginTop:3,
                                           color: unrestricted ? "#b45309" : "#475569",
                                           fontFamily: unrestricted ? "inherit" : "monospace" }}
                                 title={u.lines}>
                              {unrestricted ? "⚠ all lines (unrestricted)" : `▤ ${u.lines}`}
                            </div>
                          </div>
                          <div style={{ textAlign:"right", whiteSpace:"nowrap" }}>
                            <div style={{ fontSize:11, fontWeight:700,
                                           color: u.pages_set ? "#15803d" : "#cbd5e1" }}>
                              {u.pages_set ? `${u.pages_set} pages set` : "role defaults"}
                            </div>
                            <div style={{ fontSize:10, color:"#94a3b8", marginTop:2 }}>
                              {fmtRel(u.last_login)}
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        )}
        <ModalActions>
          <Btn onClick={()=>setHierModal(false)}>Close</Btn>
        </ModalActions>
      </Modal>

      <Modal open={!!assignModal} onClose={()=>setAssignModal(null)} title={`Assign Zones & Lines — ${assignModal?.username}`} wide>
        <p style={{ fontSize:13, color:"#64748b", marginBottom:16 }}>
          Tick a <b>zone</b> to assign every line in it at once, or pick individual lines.
          Assigned zones/lines are the only ones this user sees anywhere.
        </p>
        {(() => {
          // Group the (already user-visible) lines by zone so the admin can
          // assign a whole zone in one click.
          const m = new Map();
          for (const l of lines) {
            const key = l.zone_name || "— No zone —";
            if (!m.has(key)) m.set(key, []);
            m.get(key).push(l);
          }
          const groups = [...m.entries()].map(([zone, ls]) => ({ zone, ls, ids: ls.map(x=>x.id) }));
          return (
            <div style={{ maxHeight:360, overflowY:"auto", display:"flex", flexDirection:"column", gap:18 }}>
              {groups.map(g => {
                const onCount = g.ids.filter(id=>selLines.includes(id)).length;
                const allOn   = onCount === g.ids.length && g.ids.length > 0;
                const someOn  = onCount > 0 && !allOn;
                return (
                  <div key={g.zone}>
                    <label style={{ display:"flex", alignItems:"center", gap:10, marginBottom:8, cursor:"pointer" }}>
                      <input type="checkbox" checked={allOn}
                        ref={el => { if (el) el.indeterminate = someOn; }}
                        onChange={()=>toggleZone(g.ids)}
                        style={{ width:16, height:16, accentColor:"#1e40af" }}/>
                      <span style={{ fontSize:13, fontWeight:800, color:"#1e40af", textTransform:"uppercase", letterSpacing:.3 }}>{g.zone}</span>
                      <span style={{ fontSize:11, color:"#94a3b8" }}>({onCount}/{g.ids.length})</span>
                    </label>
                    <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fill,minmax(200px,1fr))", gap:10, paddingLeft:26 }}>
                      {g.ls.map(l=>{
                        const checked = selLines.includes(l.id);
                        return (
                          <label key={l.id} style={{ display:"flex", alignItems:"center", gap:10, padding:"10px 14px", borderRadius:8, cursor:"pointer", background:checked?"rgba(30,64,175,.06)":"#f8fafc", border:`1px solid ${checked?"rgba(30,64,175,.25)":"#e2e8f0"}`, transition:"all .12s" }}>
                            <input type="checkbox" checked={checked} onChange={()=>toggleLine(l.id)} style={{ width:15, height:15, accentColor:"#1e40af" }}/>
                            <div>
                              <div style={{ fontSize:13, fontWeight:600, color:"#0f172a" }}>{l.line_name}</div>
                              <div style={{ fontSize:10, color:"#94a3b8" }}>{l.line_code}</div>
                            </div>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
              {groups.length === 0 && <span style={{ color:"#94a3b8", fontSize:13 }}>No lines available.</span>}
            </div>
          );
        })()}
        <ModalActions>
          <Btn onClick={()=>setAssignModal(null)}>Cancel</Btn>
          <Btn variant="primary" onClick={saveAssign} disabled={saving}>{saving?"Saving…":"Save Assignments"}</Btn>
        </ModalActions>
      </Modal>
    </div>
  );
}

// ─── MACHINES PAGE ────────────────────────────────────────────
// parent_plc_id = null  → MAIN PLC (one per line, drives Dashboard tile + collector)
// parent_plc_id = <id>  → SUB-MACHINE (auxiliary, listed under main on Dashboard)
// nf2_camera_id        → bound NF2/CMS camera id (copy from NF2 Camera Master)
// machine_seq          → admin-chosen display number (M-1, M-2, …) shown
//                        as the big badge on Dashboard sub-machine tiles.
const BLANK_MACHINE_PLC = { machine_name:"", plc_ip:"", plc_port:5002, protocol:"MC4E", ok_bit_address:"L108", ng_bit_address:"L109", status_address:"D6005", model_address:"D6048", sensor_ok_address:"", process_seq_address:"", override_address:"", ideal_cycle_time:15.0, max_allowed_cycle:16.0, ok_ng_pulse_min_gap:0.5, parent_plc_id:null, nf2_camera_id:"", machine_seq:null,
  // 2026-05-29 - Count mode (Final Inspection main PLC only).
  // 'bit' = legacy L108/L109 rising-edge counting.
  // 'register' = poll D-register value, increment = +N OK/NG.
  count_mode:"bit", ok_data_register:"", ng_data_register:"", shift_reset_bit:"",
  // Semi-Auto data capture (sub-machine only, optional)
  sa_enabled:false, sa_fetch_bit:"", sa_part_code_addr:"", sa_part_code_len:null,
  sa_data_addr:"", sa_data_len:null, sa_time_addr:"", sa_time_len:null,
  sa_register_names:[], sa_register_scales:[],
  // Shift-wise capture: read the block off a D-bit, then hand the PLC an
  // L-bit so it clears itself before the next shift starts.
  sa_shift_data_bit:"", sa_shift_reset_bit:"",
  // Bottleneck marker — surfaces a badge on Dashboard tile + Submachine fullscreen.
  is_bottleneck:false,
  lc_result_reg:"", lc_value_reg:"", qr_reg:"", reject_bit:"",
  // Semi-Auto verdict -> Final NG trace (SEAT SLIDER, 2026-08-19).
  // Blank = off, which is how every machine starts.
  sa_ok_bit:"", sa_ng_bit:"", sa_ng_trigger_bit:"",
  sa_result_register:"", sa_result_ok_value:1, sa_result_ng_value:2,
  fi_sa_ng_bit:"", fi_sa_ng_bit_hold_sec:2, fi_fetch_bit:"" };
 
export function MachinesPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [subPage,   setSubPage]   = useState(0);
 
  // Cascade selects
  const [zones,    setZones]    = useState([]);
  const [lines,    setLines]    = useState([]);
  const [machines, setMachines] = useState([]);
  const [selZone,  setSelZone]  = useState("");
  const [selLine,  setSelLine]  = useState("");
  const [selMach,  setSelMach]  = useState(null);
 
  // PLC form
  const [plcForm,  setPlcForm]  = useState({ ...BLANK_MACHINE_PLC });
  // "Clean" baseline of the PLC form → lets the footer show Update (when
  // changed) vs Close (when unchanged), instead of Clear / Add Machine.
  const [plcOriginal, setPlcOriginal] = useState(null);
  const [saving,   setSaving]   = useState(false);
 
  // Status mappings (kept for potential future re-use)
  const [statuses, setStatuses]  = useState([]);
 
  // Bit addresses modal
  const [bitModal, setBitModal] = useState(false);
 
  // ── NEW: Monitor Config state ──────────────────────────────
  const [monCfg,        setMonCfg]        = useState(null);
  const [monLoading,    setMonLoading]    = useState(false);
  const [monSaving,     setMonSaving]     = useState(false);
  const [pollingBit,    setPollingBit]    = useState("");
  const [hasDataRegs,   setHasDataRegs]   = useState(false);
  const [dataRegs,      setDataRegs]      = useState([]);   // [{register, label, desired_value}]
  const [hasLoadcell,   setHasLoadcell]   = useState(false);
  const [loadcellRegs,  setLoadcellRegs]  = useState([]);   // [{register, label, min_value, max_value}]

  // ── NEW: Process Config state (sub-page 3) ─────────────────
  // Each machine can have N processes; admin sets per-process
  // process_no / process_name / target_value / actual_register.
  // The frontend turns this into bar graphs with a target line on
  // the per-machine Process Graphs page.
  const [procRows,      setProcRows]      = useState([]);   // [{process_no, process_name, target_value, actual_register, register_type, is_active}]
  const [procLoading,   setProcLoading]   = useState(false);
  const [procSaving,    setProcSaving]    = useState(false);

  const loadProcessConfig = useCallback(async (machineId) => {
    if (!machineId) return;
    setProcLoading(true);
    try {
      const r = await api.get(`/api/machines/${machineId}/processes`, token);
      const arr = Array.isArray(r) ? r : [];
      // Normalise field names from snake_case → component shape
      setProcRows(arr.map(p => ({
        process_no:      p.process_no,
        process_name:    p.process_name || "",
        target_value:    Number(p.target_value || 0),
        actual_register: p.actual_register || "",
        register_type:   p.register_type   || "word",
        is_active:       p.is_active !== false,
        latest_value:    p.latest_value,    // read-only display
        latest_at:       p.latest_at,
      })));
    } catch {
      setProcRows([]);
    } finally {
      setProcLoading(false);
    }
  }, [token]);

  useEffect(() => {
    if (subPage === 3 && selMach) loadProcessConfig(selMach.id);
  }, [subPage, selMach, loadProcessConfig]);

  const saveProcessConfig = async () => {
    if (!selMach) return toast?.("Select a machine first", "err");
    // Sanity check: every row needs at least process_name + actual_register
    for (const p of procRows) {
      if (!p.process_name?.trim()) {
        return toast?.("Every process needs a name", "err");
      }
      if (!p.actual_register?.trim()) {
        return toast?.(`Process "${p.process_name}" needs an actual-value PLC register`, "err");
      }
    }
    setProcSaving(true);
    try {
      await api.put(`/api/machines/${selMach.id}/processes`,
                     { processes: procRows.map((p, i) => ({
                         process_no:      p.process_no || (i + 1),
                         process_name:    p.process_name.trim(),
                         target_value:    Number(p.target_value || 0),
                         actual_register: p.actual_register.trim().toUpperCase(),
                         register_type:   p.register_type || "word",
                         is_active:       p.is_active !== false,
                       })) },
                     token);
      toast?.("Process config saved ✓");
      await loadProcessConfig(selMach.id);
      try { window.dispatchEvent(new CustomEvent("ap-config-changed")); } catch {}
    } catch (e) { toast?.(e.message || "Save failed", "err"); }
    finally { setProcSaving(false); }
  };
 
  useEffect(() => {
    api.get("/api/zones/", token).then(r => setZones(Array.isArray(r) ? r : [])).catch(()=>{});
  }, [token]);
 
  const [allLines, setAllLines] = useState([]);
  useEffect(() => {
    api.get("/api/lines/", token).then(r => setAllLines(Array.isArray(r) ? r : [])).catch(()=>{});
  }, [token]);
 
  useEffect(() => {
    if (!selZone) { setLines(allLines); setSelLine(""); setMachines([]); setSelMach(null); return; }
    setLines(allLines.filter(l => String(l.zone_id) === String(selZone)));
    setSelLine(""); setMachines([]); setSelMach(null);
  }, [selZone, allLines]);
 
  useEffect(() => {
    if (!selLine) { setMachines([]); setSelMach(null); return; }
    api.get(`/api/lines/${selLine}/machines`, token)
      .then(r => setMachines(Array.isArray(r) ? r : []))
      .catch(()=>{});
    setSelMach(null);
  }, [selLine, token]);
 
  const loadStatuses = () => {
    if (!selLine) return;
    api.get(`/api/config/status/${selLine}`, token)
      .then(r => setStatuses(Array.isArray(r) ? r : [])).catch(()=>{});
  };
 
  // ── NEW: load monitor config when machine selected & tab is open ──
  const loadMonitorConfig = useCallback(async (machineId) => {
    if (!selLine || !machineId) return;
    setMonLoading(true);
    try {
      const r = await api.get(`/api/lines/${selLine}/machines/${machineId}/monitor-config`, token);
      setMonCfg(r);
      setPollingBit(r.polling_bit || "");
      setHasDataRegs(r.has_data_registers || false);
      setDataRegs(r.data_registers || []);
      setHasLoadcell(r.has_loadcell || false);
      setLoadcellRegs(r.loadcell_registers || []);
    } catch {
      setMonCfg(null);
      setPollingBit(""); setHasDataRegs(false); setDataRegs([]);
      setHasLoadcell(false); setLoadcellRegs([]);
    } finally {
      setMonLoading(false);
    }
  }, [selLine, token]);
 
  useEffect(() => {
    if (subPage === 2 && selMach) loadMonitorConfig(selMach.id);
  }, [subPage, selMach, loadMonitorConfig]);
 
  // ── NEW: save monitor config ───────────────────────────────
  const saveMonitorConfig = async () => {
    if (!selLine || !selMach) return toast("Select a machine first", "err");
    if (!pollingBit.trim()) return toast("Polling bit is required (e.g. M99)", "err");
    setMonSaving(true);
    try {
      await api.put(
        `/api/lines/${selLine}/machines/${selMach.id}/monitor-config`,
        {
          plc_id:             selMach.id,
          polling_bit:        pollingBit.trim().toUpperCase(),
          has_data_registers: hasDataRegs,
          data_registers:     hasDataRegs ? dataRegs.filter(r => r.register) : [],
          has_loadcell:       hasLoadcell,
          loadcell_registers: hasLoadcell ? loadcellRegs.filter(r => r.register) : [],
        },
        token
      );
      toast("Monitor config saved ✓");
      await loadMonitorConfig(selMach.id); // refresh
    } catch (e) { toast(e.message || "Save failed", "err"); }
    finally { setMonSaving(false); }
  };
 
  const deleteMonitorConfig = async () => {
    if (!window.confirm("Remove all monitor config for this machine?")) return;
    try {
      await api.delete(`/api/lines/${selLine}/machines/${selMach.id}/monitor-config`, token);
      toast("Monitor config removed");
      setMonCfg(null); setPollingBit(""); setHasDataRegs(false);
      setDataRegs([]); setHasLoadcell(false); setLoadcellRegs([]);
    } catch (e) { toast(e.message, "err"); }
  };
 
  // ── NEW: row helpers ───────────────────────────────────────
  const addDataReg    = () => setDataRegs(p => [...p, { register:"", label:"", desired_value:"" }]);
  const removeDataReg = (i) => setDataRegs(p => p.filter((_,idx) => idx !== i));
  const setDataReg    = (i, field, val) => setDataRegs(p => p.map((r,idx) => idx===i ? {...r,[field]:val} : r));
 
  const addLoadcell    = () => setLoadcellRegs(p => [...p, { register:"", label:"", min_value:"", max_value:"" }]);
  const removeLoadcell = (i) => setLoadcellRegs(p => p.filter((_,idx) => idx !== i));
  const setLoadcell    = (i, field, val) => setLoadcellRegs(p => p.map((r,idx) => idx===i ? {...r,[field]:val} : r));
 
  // ── Existing helpers (unchanged) ──────────────────────────
  const selectMachine = (m) => {
    setSelMach(m);
    setPlcForm({
      machine_name: m.machine_name || "",
      plc_ip: m.plc_ip || "",
      plc_port: m.plc_port || 5002,
      protocol: m.protocol || "MC4E",
      ok_bit_address: m.ok_bit_address || "L108",
      ng_bit_address: m.ng_bit_address || "L109",
      status_address: m.status_address || "D6005",
      model_address: m.model_address || "D6048",
      sensor_ok_address: m.sensor_ok_address || "",
      process_seq_address: m.process_seq_address || "",
      override_address: m.override_address || "",
      ideal_cycle_time: m.ideal_cycle_time || 15.0,
      max_allowed_cycle: m.max_allowed_cycle || 16.0,
      ok_ng_pulse_min_gap: m.ok_ng_pulse_min_gap || 0.5,
      // Planned takt time — lives on mes_lines (per-line, customer-demand rhythm),
      // not on the PLC row.  Surfaced in this form for editing convenience when
      // admin is configuring the line's main PLC.  Loaded asynchronously below.
      planned_takt_time: null,
      // Energy per part — also lives on mes_lines.  Static admin entry
      // (kWh/part), surfaced on the Fullscreen Production card.
      energy_per_part:   null,
      // Sub-machine wiring — keep null/empty for main PLCs.
      parent_plc_id: m.parent_plc_id ?? null,
      nf2_camera_id: m.nf2_camera_id || "",
      // Display sequence (M-1, M-2 …) for Dashboard tiles.
      machine_seq:   m.machine_seq ?? null,
      // Semi-Auto data capture (sub-machine only)
      sa_enabled:        !!m.sa_enabled,
      sa_fetch_bit:      m.sa_fetch_bit || "",
      sa_part_code_addr: m.sa_part_code_addr || "",
      sa_part_code_len:  m.sa_part_code_len ?? null,
      sa_data_addr:      m.sa_data_addr || "",
      sa_data_len:       m.sa_data_len ?? null,
      sa_time_addr:      m.sa_time_addr || "",
      sa_time_len:       m.sa_time_len ?? null,
      sa_register_names: Array.isArray(m.sa_register_names) ? m.sa_register_names : [],
      sa_register_scales: Array.isArray(m.sa_register_scales) ? m.sa_register_scales : [],
      sa_shift_data_bit:  m.sa_shift_data_bit  || "",
      sa_shift_reset_bit: m.sa_shift_reset_bit || "",
      sa_ok_bit:          m.sa_ok_bit || "",
      sa_ng_bit:          m.sa_ng_bit || "",
      sa_ng_trigger_bit:  m.sa_ng_trigger_bit || "",
      sa_result_register: m.sa_result_register || "",
      sa_result_ok_value: m.sa_result_ok_value ?? 1,
      sa_result_ng_value: m.sa_result_ng_value ?? 2,
      fi_sa_ng_bit:          m.fi_sa_ng_bit || "",
      fi_sa_ng_bit_hold_sec: m.fi_sa_ng_bit_hold_sec ?? 2,
      fi_fetch_bit:          m.fi_fetch_bit || "",
      // Bottleneck flag
      is_bottleneck:     !!m.is_bottleneck,
      lc_result_reg:     m.lc_result_reg || "",
      lc_value_reg:      m.lc_value_reg  || "",
      qr_reg:            m.qr_reg        || "",
      reject_bit:        m.reject_bit    || "",
      // 2026-05-30 — Register-mirror counting.  MUST load the saved values
      // here: without them the form rebuilds with count_mode undefined →
      // shows "Bit edge" on a register machine, and Save then PUTs no
      // count_mode (defaults to bit) + NULL registers, silently wiping the
      // register config.  Loading them = zero regression on edit.
      count_mode:       m.count_mode || "bit",
      ok_data_register: m.ok_data_register || "",
      ng_data_register: m.ng_data_register || "",
      shift_reset_bit:  m.shift_reset_bit || "",
    });
    // Fetch the line-level planned takt time so the form can display
    // (and edit) it inline with the other PLC fields.
    if (selLine) {
      api.get(`/api/lines/${selLine}/planning`, token)
        .then(r => {
          const pt  = r?.planned_takt;
          const epp = r?.energy_per_part;
          const extra = {
            planned_takt_time: pt  != null ? Number(pt)  : null,
            energy_per_part:   epp != null ? Number(epp) : null,
          };
          setPlcForm(f => ({ ...f, ...extra }));
          setPlcOriginal(o => (o ? { ...o, ...extra } : o));   // keep baseline clean
        })
        .catch(() => {});
    }
  };

  // Snapshot the freshly-loaded PLC form as the clean baseline whenever a
  // different machine is opened (async takt/energy re-synced in the loader).
  useEffect(() => { setPlcOriginal(plcForm); }, [selMach?.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  const plcDirty = plcOriginal !== null &&
    JSON.stringify(plcForm) !== JSON.stringify(plcOriginal);

  const savePLC = async () => {
    if (!selLine) return toast("Select a line first", "err");
    setSaving(true);
    try {
      // planned_takt_time + energy_per_part live on mes_lines, not on
      // mes_plc_configs.  Strip both from the machine payload so the
      // PLC PUT doesn't reject them as unknown columns, then push them
      // to the line in a follow-up call.
      const { planned_takt_time, energy_per_part, ...plcPayload } = plcForm;
      if (selMach) {
        await api.put(`/api/lines/${selLine}/machines/${selMach.id}`, plcPayload, token);
        toast("Machine PLC config updated ✓");
      } else {
        if (!plcForm.plc_ip) return toast("IP address required", "err");
        await api.post(`/api/lines/${selLine}/machines`, plcPayload, token);
        toast("Machine added ✓");
        setPlcForm({ ...BLANK_MACHINE_PLC });
      }
      // Persist line-level planned takt + energy/part in ONE /planning
      // PUT so the line stays in sync with both fields together.
      const hasTakt   = planned_takt_time != null && Number(planned_takt_time) > 0;
      const hasEnergy = energy_per_part   != null && Number(energy_per_part)   >= 0;
      if (hasTakt || hasEnergy) {
        try {
          const payload = {
            ideal_ct:     Number(plcForm.ideal_cycle_time) || 15.0,
            recalculate:  false,
          };
          if (hasTakt)   payload.planned_takt    = Number(planned_takt_time);
          if (hasEnergy) payload.energy_per_part = Number(energy_per_part);
          await api.put(`/api/lines/${selLine}/planning`, payload, token);
        } catch (e) {
          toast(`Line-level save failed: ${e.message}`, "err");
        }
      }
      const r = await api.get(`/api/lines/${selLine}/machines`, token);
      setMachines(Array.isArray(r) ? r : []);
      setSelMach(null);
      setPlcForm({ ...BLANK_MACHINE_PLC });
      setPlcOriginal({ ...BLANK_MACHINE_PLC });
      setSubPage(0);                       // back to the machine list after save
    } catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  const deleteMachine = async (m) => {
    if (!window.confirm(`Delete machine "${m.machine_name || m.plc_ip}"?`)) return;
    try {
      await api.delete(`/api/lines/${selLine}/machines/${m.id}`, token);
      toast("Machine deleted");
      const r = await api.get(`/api/lines/${selLine}/machines`, token);
      setMachines(Array.isArray(r) ? r : []);
      if (selMach?.id === m.id) { setSelMach(null); setPlcForm({ ...BLANK_MACHINE_PLC }); }
    } catch (e) { toast(e.message, "err"); }
  };
 
  const mini = { ...inputStyle, padding:"8px 10px", fontSize:12 };
  const BIT_FIELDS = [
    { key:"ok_bit_address",      label:"OK Bit Address" },
    { key:"ng_bit_address",      label:"NG Bit Address" },
    { key:"status_address",      label:"Status Address" },
    { key:"model_address",       label:"Model Address" },
    { key:"sensor_ok_address",   label:"Sensor OK Address" },
    { key:"process_seq_address", label:"Process Seq Address" },
    { key:"override_address",    label:"Override Address" },
  ];
 
  return (
    <div>
 
      {/* Cascade selects */}
      <Card style={{ marginBottom:20 }}>
        <div style={{ display:"flex", gap:12, flexWrap:"wrap", alignItems:"flex-end" }}>
          <FF label="Zone">
            <select style={mini} value={selZone} onChange={e=>setSelZone(e.target.value)}>
              <option value="">— Select Zone —</option>
              {zones.map(z=><option key={z.id} value={z.id}>{z.zone_name}</option>)}
            </select>
          </FF>
          <FF label="Line">
            <select style={mini} value={selLine} onChange={e=>setSelLine(e.target.value)} disabled={!selZone}>
              <option value="">— Select Line —</option>
              {lines.map(l=><option key={l.id} value={l.id}>{l.line_name}</option>)}
            </select>
          </FF>
          <FF label="Machine">
            <select style={mini} value={selMach?.id||""} onChange={e=>{ const m=machines.find(m=>m.id===Number(e.target.value)); if(m) selectMachine(m); else { setSelMach(null); setPlcForm({...BLANK_MACHINE_PLC}); }}} disabled={!selLine}>
              <option value="">— New Machine —</option>
              {machines.map(m=><option key={m.id} value={m.id}>{m.machine_name||m.plc_ip}</option>)}
            </select>
          </FF>
        </div>
      </Card>
 
      {/* Sub-page tabs — ④ Process Config added for per-machine
          process target/actual graphs */}
      <div style={{ display:"flex", gap:0, borderBottom:"2px solid #e2e8f0", marginBottom:24 }}>
        {["① Machines","② PLC Config","③ Monitor Config","④ Process Config"].map((label,i)=>(
          <button key={i} onClick={()=>setSubPage(i)}
            style={{ padding:"8px 16px", border:"none", background:"none", fontFamily:"'Barlow',sans-serif",
              fontSize:12, fontWeight:600, cursor:"pointer",
              color:subPage===i?"#1e40af":"#64748b",
              borderBottom:`2px solid ${subPage===i?"#1e40af":"transparent"}`,
              marginBottom:-2, transition:"all .12s" }}>
            {label}
          </button>
        ))}
      </div>
 
      {/* ── Sub-page 0: Machine list (unchanged) ── */}
      {subPage === 0 && (
        <Card>
          <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:16 }}>
            <h3 style={{ fontSize:15, fontWeight:700, color:"#0f172a" }}>
              {selLine ? `Machines on ${lines.find(l=>l.id===Number(selLine))?.line_name||""}` : "Select a line to view machines"}
            </h3>
            {selLine && <Btn variant="primary" size="sm" onClick={()=>{ setSelMach(null); setPlcForm({...BLANK_MACHINE_PLC}); setPlcOriginal({...BLANK_MACHINE_PLC}); setSubPage(1); }}>+ Add Machine</Btn>}
          </div>
          {machines.length === 0 ? (
            <p style={{ color:"#94a3b8", fontSize:13 }}>{selLine ? "No machines configured for this line." : "Select a zone, then a line to see machines."}</p>
          ) : (
            machines.map(m => (
              <div key={m.id} style={{ display:"flex", alignItems:"center", gap:12, padding:"10px 14px", borderRadius:8, border:"1px solid #e2e8f0", marginBottom:8, background:"#f8fafc" }}>
                <div style={{ flex:1 }}>
                  <div style={{ fontWeight:700, fontSize:13, color:"#0f172a", display:"flex", alignItems:"center", gap:8 }}>
                    {m.machine_name || "(unnamed)"}
                    {m.parent_plc_id ? (
                      <span style={{ fontSize:9, fontWeight:700, padding:"2px 6px", borderRadius:4, background:"#dbeafe", color:"#1e40af", letterSpacing:".05em" }}>
                        SUB of #{m.parent_plc_id}
                      </span>
                    ) : (
                      <span style={{ fontSize:9, fontWeight:700, padding:"2px 6px", borderRadius:4, background:"#dcfce7", color:"#15803d", letterSpacing:".05em" }}>
                        MAIN
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize:11, color:"#64748b" }}>{m.plc_ip}:{m.plc_port} · {m.protocol}{m.nf2_camera_id ? ` · cam=${m.nf2_camera_id}` : ""}</div>
                </div>
                <Btn size="sm" onClick={()=>{ selectMachine(m); setSubPage(1); }}>Edit PLC</Btn>
                <Btn size="sm" onClick={()=>{ selectMachine(m); setSubPage(2); }}>Monitor Config</Btn>
                <Btn size="sm" variant="danger" onClick={()=>deleteMachine(m)}>Delete</Btn>
              </div>
            ))
          )}
        </Card>
      )}
 
      {/* ── Sub-page 1: PLC Config (unchanged) ── */}
      {subPage === 1 && (
        <Card>
          <h3 style={{ fontSize:15, fontWeight:700, color:"#0f172a", marginBottom:18 }}>
            {selMach ? `Edit PLC — ${selMach.machine_name||selMach.plc_ip}` : "Add New Machine"}
            {selLine && <span style={{ fontWeight:400, color:"#64748b", fontSize:12, marginLeft:8 }}>for {lines.find(l=>l.id===Number(selLine))?.line_name}</span>}
          </h3>
          <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:"10px 16px" }}>
            {[
              { k:"machine_name",      l:"Machine Name",          t:"text" },
              { k:"plc_ip",            l:"PLC IP Address",         t:"text" },
              { k:"plc_port",          l:"PLC Port",               t:"number" },
              { k:"protocol",          l:"Protocol",               t:"text" },
              { k:"ideal_cycle_time",  l:"Ideal Cycle Time (s)",   t:"number" },
              { k:"max_allowed_cycle", l:"Max Allowed Cycle (s)",  t:"number" },
              { k:"ok_ng_pulse_min_gap",l:"OK/NG Min Gap (s)",     t:"number" },
              { k:"planned_takt_time", l:"Planned Takt Time (s)",  t:"number",
                hint:"Customer-demand rhythm (line-level). Saved to the line, not the machine." },
              { k:"energy_per_part",   l:"Energy / Part (kWh)",   t:"number",
                hint:"Static admin entry — shown on Fullscreen Production card. Line-level." },
            ].map(({k,l,t,hint})=>(
              <FF key={k} label={l}>
                <input style={mini} type={t} step={t==="number"?"0.01":undefined}
                       value={plcForm[k] ?? ""}
                       onChange={e=>setPlcForm(f=>({...f,[k]:t==="number"?(e.target.value===""?null:parseFloat(e.target.value)):e.target.value}))}/>
                {hint && <div style={{ fontSize:10, color:"#94a3b8", marginTop:4 }}>{hint}</div>}
              </FF>
            ))}
          </div>
          {/* Bit addresses inline */}
          <div style={{ borderTop:"1px solid #e2e8f0", marginTop:20, paddingTop:20 }}>
            <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:12 }}>
              <h4 style={{ fontSize:13, fontWeight:700, color:"#0f172a", margin:0 }}>Bit / Register Addresses</h4>
            </div>
            <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:"10px 16px" }}>
              {BIT_FIELDS.map(({key,label})=>(
                <FF key={key} label={label}>
                  <input style={mini} type="text" value={plcForm[key]||""} onChange={e=>setPlcForm(f=>({...f,[key]:e.target.value}))}/>
                </FF>
              ))}
            </div>
            {/* 2026-05-30 — OK/NG count mode: bit edge OR D-register mirror.
                Only one logic is active at a time.  Shown for the main PLC
                AND for sub-machines, EXCEPT Semi-Auto machines (sa_enabled),
                which stay bit-only and untouched (operator: "no touch semi
                auto"). */}
            {!plcForm.sa_enabled && (
              <div style={{ marginTop:18, padding:12, borderRadius:6,
                            background:"#f8fafc", border:"1px solid #e2e8f0" }}>
                <div style={{ fontSize:12, fontWeight:700, color:"#0f172a", marginBottom:8 }}>
                  OK / NG Counting Mode <span style={{ fontSize:10, fontWeight:500, color:"#64748b" }}>(except Semi-Auto)</span>
                </div>
                <div style={{ display:"flex", gap:18, marginBottom:10 }}>
                  <label style={{ display:"flex", alignItems:"center", gap:6, fontSize:12, cursor:"pointer" }}>
                    <input type="radio" name="count_mode"
                           checked={(plcForm.count_mode || "bit") === "bit"}
                           onChange={()=>setPlcForm(f=>({...f, count_mode:"bit"}))}/>
                    <span><b>Bit edge</b> — L108 rise = +1 OK, L109 rise = +1 NG</span>
                  </label>
                  <label style={{ display:"flex", alignItems:"center", gap:6, fontSize:12, cursor:"pointer" }}>
                    <input type="radio" name="count_mode"
                           checked={plcForm.count_mode === "register"}
                           onChange={()=>setPlcForm(f=>({...f, count_mode:"register"}))}/>
                    <span><b>Register</b> — poll D-register value, increment = +N OK/NG</span>
                  </label>
                </div>
                {plcForm.count_mode === "register" && (
                  <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:"10px 16px" }}>
                    <FF label="OK Data Register">
                      <input style={mini} type="text"
                             placeholder="e.g. D5100"
                             value={plcForm.ok_data_register||""}
                             onChange={e=>setPlcForm(f=>({...f, ok_data_register:e.target.value.trim().toUpperCase()}))}/>
                      <div style={{ fontSize:10, color:"#94a3b8", marginTop:4 }}>
                        Word register holding shift OK count. Value increase → +N OK rows. Resets per shift.
                      </div>
                    </FF>
                    <FF label="NG Data Register">
                      <input style={mini} type="text"
                             placeholder="e.g. D5101"
                             value={plcForm.ng_data_register||""}
                             onChange={e=>setPlcForm(f=>({...f, ng_data_register:e.target.value.trim().toUpperCase()}))}/>
                      <div style={{ fontSize:10, color:"#94a3b8", marginTop:4 }}>
                        Word register holding shift NG count. Same logic, separate from OK.
                      </div>
                    </FF>
                    <div style={{ gridColumn:"1 / -1" }}>
                      <FF label="Shift Reset Bit">
                        <input style={mini} type="text"
                               placeholder="e.g. M5800"
                               value={plcForm.shift_reset_bit||""}
                               onChange={e=>setPlcForm(f=>({...f, shift_reset_bit:e.target.value.trim().toUpperCase()}))}/>
                        <div style={{ fontSize:10, color:"#94a3b8", marginTop:4 }}>
                          Bit that pulses ON (~2s) at shift end. On rising edge the closing OK/NG count is archived first, then the registers reset to 0. Leave blank = no auto-reset.
                        </div>
                      </FF>
                    </div>
                    {/* Semi-Auto verdict -> Final NG bit (SEAT SLIDER trace,
                        2026-08-19).  Pairs with the Verdict Register set on
                        this line's Semi-Auto.  Blank = feature off.
                        Main machines only — tested inline because `isSub` is
                        declared further down, in the Semi-Auto card's scope. */}
                    {plcForm.parent_plc_id == null && (
                      <div style={{ gridColumn:"1 / -1" }}>
                        <FF label="SA-NG Bit (Semi-Auto NG → Final)">
                          <div style={{ display:"flex", gap:6 }}>
                            <input style={{...mini, flex:2}} type="text"
                                   placeholder="e.g. M5801"
                                   value={plcForm.fi_sa_ng_bit||""}
                                   onChange={e=>setPlcForm(f=>({...f, fi_sa_ng_bit:e.target.value.trim().toUpperCase()}))}/>
                            <input style={{...mini, flex:1}} type="number" step="0.5" min="0.5"
                                   placeholder="hold s"
                                   value={plcForm.fi_sa_ng_bit_hold_sec ?? 2}
                                   onChange={e=>setPlcForm(f=>({...f, fi_sa_ng_bit_hold_sec: e.target.value===""?null:Number(e.target.value)}))}/>
                          </div>
                          <div style={{ fontSize:10, color:"#94a3b8", marginTop:4 }}>
                            When a part that the Semi-Auto marked NG reaches this machine, the collector
                            pulses this bit (default 2 s) so the ladder can reject it. Counting and OEE are
                            untouched — the event is written to Quality → SA/FI History. Blank = no bit written.
                          </div>
                        </FF>
                        {/* 2026-08-24 — Final compare-fetch bit.  Separate from
                            counting.  When set, the SA-NG reject fires on THIS
                            bit's rising edge (correct cycle), not on the count
                            commit (which lands one cycle late). */}
                        <FF label="Final Fetch Bit (compare trigger — optional)">
                          <input style={mini} type="text"
                                 placeholder="e.g. M5900"
                                 value={plcForm.fi_fetch_bit||""}
                                 onChange={e=>setPlcForm(f=>({...f, fi_fetch_bit:e.target.value.trim().toUpperCase()}))}/>
                          <div style={{ fontSize:10, color:"#94a3b8", marginTop:4 }}>
                            Separate from the counting bits. On this bit's rising edge (part present +
                            decision ready) the collector reads the Final part code and compares it to the
                            Semi-Auto NG table, pulsing the SA-NG bit above — so the reject lands on the
                            part's OWN cycle, not one cycle late. Blank = reject fires on count commit (unchanged).
                          </div>
                        </FF>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
          {/* ── Machine Type & Camera (sub-machine wiring) ─────────────
              parent_plc_id NULL → main PLC. Otherwise this row appears as
              a sub-machine tile under that main on the Dashboard. */}
          <div style={{ borderTop:"1px solid #e2e8f0", marginTop:20, paddingTop:20 }}>
            <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:12 }}>
              <h4 style={{ fontSize:13, fontWeight:700, color:"#0f172a", margin:0 }}>Machine Type</h4>
              <span style={{ fontSize:10, color:"#64748b" }}>
                Choose Main PLC for the line's primary station, or Sub-machine for an auxiliary station (M-bit pulse).
              </span>
            </div>
            <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:"10px 16px" }}>
              <FF label="Type">
                <select style={mini}
                        value={plcForm.parent_plc_id == null ? "" : String(plcForm.parent_plc_id)}
                        onChange={e=>{
                          const v = e.target.value;
                          setPlcForm(f=>({...f, parent_plc_id: v === "" ? null : Number(v)}));
                        }}>
                  <option value="">Main PLC (primary station of this line)</option>
                  {machines
                    .filter(m => !m.parent_plc_id && m.id !== selMach?.id)
                    .map(m => (
                      <option key={m.id} value={m.id}>
                        Sub-machine of: {m.machine_name || m.plc_ip}
                      </option>
                    ))}
                </select>
              </FF>
              <FF label="Machine No. (M-N badge)">
                <input style={mini}
                       type="number"
                       min="1"
                       max="99"
                       value={plcForm.machine_seq == null ? "" : plcForm.machine_seq}
                       placeholder="e.g. 1, 2, 3 …"
                       onChange={e=>{
                         const v = e.target.value;
                         setPlcForm(f=>({...f, machine_seq: v === "" ? null : parseInt(v) || null}));
                       }}/>
                <div style={{ fontSize:10, color:"#94a3b8", marginTop:4 }}>
                  Shown as the big <b>M-{plcForm.machine_seq || "N"}</b> badge on the Dashboard tile. Leave blank to skip.
                </div>
              </FF>
              <FF label="NF2 Camera ID (sub-machine only)">
                <input style={mini}
                       type="text"
                       value={plcForm.nf2_camera_id||""}
                       placeholder="e.g. cam_upper_side_greasing_1776851562"
                       disabled={plcForm.parent_plc_id == null}
                       onChange={e=>setPlcForm(f=>({...f, nf2_camera_id: e.target.value.trim()}))}/>
                <div style={{ fontSize:10, color:"#94a3b8", marginTop:4 }}>
                  Copy from NF2 → Camera Master. Leave blank if no camera bound yet.
                </div>
              </FF>
            </div>
            {/* ── Bottleneck flag — UX marker, no backend logic change ── */}
            <div style={{ marginTop:14, padding:10, borderRadius:6,
                          background: plcForm.is_bottleneck ? "rgba(220,38,38,0.08)" : "#f8fafc",
                          border: `1px solid ${plcForm.is_bottleneck ? "rgba(220,38,38,0.35)" : "#e2e8f0"}` }}>
              <label style={{ display:"flex", alignItems:"center", gap:10, cursor:"pointer" }}>
                <input type="checkbox"
                       checked={!!plcForm.is_bottleneck}
                       onChange={e=>setPlcForm(f=>({...f, is_bottleneck: e.target.checked}))}/>
                <div>
                  <div style={{ fontSize:12, fontWeight:700, color: plcForm.is_bottleneck ? "#b91c1c" : "#0f172a" }}>
                    🚧 Mark as Bottleneck Machine
                  </div>
                  <div style={{ fontSize:10, color:"#64748b", marginTop:2 }}>
                    When enabled, a red <b>BOTTLENECK</b> badge surfaces on this machine's tile in the line Dashboard and on its Sub-machine fullscreen header. Pure UX — no effect on cycle counting or video.
                  </div>
                </div>
              </label>
            </div>
          </div>

          {/* ── Semi-Auto data capture (sub-machine only) ──────────── */}
          {/* Always rendered so the operator never wonders "where did it
              go?" — but the inputs are disabled until Type=Sub-machine
              is picked, with a clear hint why. */}
          {(() => {
            const isSub = plcForm.parent_plc_id != null;
            return (
            <div style={{ marginTop:16, padding:14, borderRadius:8,
                          background: isSub ? "#fefce8" : "#f8fafc",
                          border: `1px solid ${isSub ? "#fde68a" : "#e2e8f0"}`,
                          opacity: isSub ? 1 : 0.7 }}>
              <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom:10 }}>
                <div>
                  <h4 style={{ fontSize:13, fontWeight:700, color:"#0f172a", margin:0 }}>
                    Semi-Auto Data Capture
                    {!isSub && <span style={{
                      marginLeft:8, fontSize:10, fontWeight:700, color:"#64748b",
                      background:"#e2e8f0", padding:"2px 8px", borderRadius:99,
                    }}>SUB-MACHINE ONLY</span>}
                  </h4>
                  <span style={{ fontSize:10, color: isSub ? "#92400e" : "#64748b" }}>
                    {isSub
                      ? <>On each rising edge of <b>Fetching Bit</b>, the collector reads part code + N raw data registers + PLC time and stores one row in <code>mes_submachine_data_log</code>. Video clip still extracts via the normal cycle bit — these are independent paths.</>
                      : <>Select <b>Type → Sub-machine of: …</b> above to enable this section. Semi-Auto pulls part code + N data registers from the PLC on a separate fetch bit, stored per cycle for the Part History search.</>}
                  </span>
                </div>
                <label style={{ display:"flex", alignItems:"center", gap:8,
                                cursor: isSub ? "pointer" : "not-allowed",
                                fontSize:12, fontWeight:700,
                                color: isSub ? "#92400e" : "#94a3b8" }}>
                  <input type="checkbox"
                         disabled={!isSub}
                         checked={!!plcForm.sa_enabled}
                         onChange={e=>setPlcForm(f=>({...f, sa_enabled: e.target.checked}))}/>
                  Enable
                </label>
              </div>

              {isSub && plcForm.sa_enabled && (
                <>
                  <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:"10px 16px", marginBottom:12 }}>
                    <FF label="OK Fetching Bit">
                      <input style={mini} type="text"
                             value={plcForm.sa_fetch_bit||""}
                             placeholder="e.g. M5700"
                             onChange={e=>setPlcForm(f=>({...f, sa_fetch_bit: e.target.value.trim()}))}/>
                      <div style={{ fontSize:10, color:"#94a3b8", marginTop:3 }}>
                        Rising edge on an OK part → reads the data registers + logs the row OK.
                      </div>
                    </FF>
                    <FF label="NG Fetching Bit">
                      <input style={mini} type="text"
                             value={plcForm.sa_ng_trigger_bit||""}
                             placeholder="e.g. M5710"
                             onChange={e=>setPlcForm(f=>({...f, sa_ng_trigger_bit: e.target.value.trim().toUpperCase()}))}/>
                      <div style={{ fontSize:10, color:"#94a3b8", marginTop:3 }}>
                        Its parallel for NG: rising edge on an NG part → reads the SAME registers + logs the row NG. Blank = OK-only.
                      </div>
                    </FF>
                    <FF label="Part Code Address">
                      <div style={{ display:"flex", gap:6 }}>
                        <input style={{...mini, flex:2}} type="text"
                               value={plcForm.sa_part_code_addr||""}
                               placeholder="D530"
                               onChange={e=>setPlcForm(f=>({...f, sa_part_code_addr: e.target.value.trim()}))}/>
                        <input style={{...mini, flex:1}} type="number" min="1" max="50"
                               value={plcForm.sa_part_code_len ?? ""}
                               placeholder="len 13"
                               onChange={e=>setPlcForm(f=>({...f, sa_part_code_len: e.target.value === "" ? null : parseInt(e.target.value) || null}))}/>
                      </div>
                      <div style={{ fontSize:10, color:"#94a3b8", marginTop:3 }}>
                        Byte-reversed ASCII. Leave blank to skip part-code capture.
                      </div>
                    </FF>
                    <FF label="Time Address (optional)">
                      <div style={{ display:"flex", gap:6 }}>
                        <input style={{...mini, flex:2}} type="text"
                               value={plcForm.sa_time_addr||""}
                               placeholder="D1600"
                               onChange={e=>setPlcForm(f=>({...f, sa_time_addr: e.target.value.trim()}))}/>
                        <input style={{...mini, flex:1}} type="number" min="6" max="6"
                               value={plcForm.sa_time_len ?? ""}
                               placeholder="6"
                               onChange={e=>setPlcForm(f=>({...f, sa_time_len: e.target.value === "" ? null : parseInt(e.target.value) || null}))}/>
                      </div>
                      <div style={{ fontSize:10, color:"#94a3b8", marginTop:3 }}>
                        6 regs: yr, mo, dy, hr, min, sec. Blank → use server clock.
                      </div>
                    </FF>
                  </div>

                  <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:"10px 16px", marginBottom:8 }}>
                    <FF label="Data Block Start Address">
                      <input style={mini} type="text"
                             value={plcForm.sa_data_addr||""}
                             placeholder="D5801"
                             onChange={e=>setPlcForm(f=>({...f, sa_data_addr: e.target.value.trim()}))}/>
                    </FF>
                    <FF label="Number of Registers">
                      <input style={mini} type="number" min="1" max="100"
                             value={plcForm.sa_data_len ?? ""}
                             placeholder="20"
                             onChange={e=>{
                               const v = e.target.value === "" ? null : parseInt(e.target.value) || null;
                               setPlcForm(f=>{
                                 const newLen = v;
                                 // Resize register-names + scales arrays to match
                                 const names  = Array.from({length: newLen||0}, (_,i)=> f.sa_register_names?.[i] || "");
                                 const scales = Array.from({length: newLen||0}, (_,i)=> f.sa_register_scales?.[i] ?? 1);
                                 return { ...f, sa_data_len: newLen, sa_register_names: names, sa_register_scales: scales };
                               });
                             }}/>
                    </FF>
                  </div>

                  {/* OK/NG verdict register — drives the Final NG bit + the
                      Quality > SA/FI history.  Blank = nothing logged, no bit. */}
                  <div style={{ marginTop:10, paddingTop:10, borderTop:"1px dashed #fbbf24" }}>
                    <div style={{ fontSize:11, fontWeight:700, color:"#92400e", marginBottom:2 }}>
                      OK / NG Verdict Bits &nbsp;<span style={{fontWeight:600, color:"#b45309"}}>(Seat Slider trace)</span>
                    </div>
                    <div style={{ fontSize:10, color:"#92400e", marginBottom:8 }}>
                      Two PLC bits carry this station's verdict — whichever is ON when the
                      part is captured decides it. When the <b>NG Bit</b> is ON the part is
                      logged NG and, once that part reaches Final Inspection, the collector
                      pulses Final's <b>SA-NG Bit</b>. Leave both blank to keep this machine
                      out of the trace entirely.
                    </div>
                    <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:"10px 16px" }}>
                      <FF label="OK Bit">
                        <input style={mini} type="text"
                               value={plcForm.sa_ok_bit||""}
                               placeholder="e.g. M5701"
                               onChange={e=>setPlcForm(f=>({...f, sa_ok_bit: e.target.value.trim().toUpperCase()}))}/>
                        <div style={{ fontSize:10, color:"#94a3b8", marginTop:3 }}>
                          ON = part passed at this station.
                        </div>
                      </FF>
                      <FF label="NG Bit">
                        <input style={mini} type="text"
                               value={plcForm.sa_ng_bit||""}
                               placeholder="e.g. M5702"
                               onChange={e=>setPlcForm(f=>({...f, sa_ng_bit: e.target.value.trim().toUpperCase()}))}/>
                        <div style={{ fontSize:10, color:"#94a3b8", marginTop:3 }}>
                          ON = part failed. If both are ON, NG wins.
                        </div>
                      </FF>
                    </div>
                  </div>

                  {(plcForm.sa_data_len || 0) > 0 && (
                    <div style={{ marginTop:10 }}>
                      <div style={{ fontSize:11, fontWeight:700, color:"#92400e", marginBottom:6 }}>
                        Register Labels &amp; Scaling — {plcForm.sa_data_len} register{plcForm.sa_data_len === 1 ? "" : "s"}
                      </div>
                      <div style={{ maxHeight:240, overflowY:"auto", border:"1px solid #fde68a", borderRadius:6, background:"#fff" }}>
                        <table style={{ width:"100%", borderCollapse:"collapse", fontSize:11 }}>
                          <thead>
                            <tr style={{ background:"#fef3c7" }}>
                              {["#","Register","Label","Scale (raw × scale)"].map(h => (
                                <th key={h} style={{ padding:"6px 10px", textAlign:"left", color:"#78350f", fontWeight:700 }}>{h}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {Array.from({length: plcForm.sa_data_len}).map((_, i) => {
                              const baseAddr = plcForm.sa_data_addr || "";
                              const match = baseAddr.match(/^([A-Za-z]+)(\d+)/);
                              const reg = match ? `${match[1]}${parseInt(match[2])+i}` : `${baseAddr}+${i}`;
                              return (
                                <tr key={i} style={{ borderBottom:"1px solid #fef3c7" }}>
                                  <td style={{ padding:"5px 10px", color:"#92400e", fontWeight:700 }}>{i+1}</td>
                                  <td style={{ padding:"5px 10px", fontFamily:"monospace", color:"#475569" }}>{reg}</td>
                                  <td style={{ padding:"5px 10px" }}>
                                    <input style={{...mini, width:"100%"}} type="text"
                                           value={plcForm.sa_register_names?.[i] || ""}
                                           placeholder={`data_${i+1}`}
                                           onChange={e=>setPlcForm(f=>{
                                             const arr = [...(f.sa_register_names||[])];
                                             while (arr.length <= i) arr.push("");
                                             arr[i] = e.target.value;
                                             return { ...f, sa_register_names: arr };
                                           })}/>
                                  </td>
                                  <td style={{ padding:"5px 10px" }}>
                                    <input style={{...mini, width:"100%"}} type="number" step="0.001"
                                           value={plcForm.sa_register_scales?.[i] ?? 1}
                                           onChange={e=>setPlcForm(f=>{
                                             const arr = [...(f.sa_register_scales||[])];
                                             while (arr.length <= i) arr.push(1);
                                             arr[i] = e.target.value === "" ? 1 : parseFloat(e.target.value);
                                             return { ...f, sa_register_scales: arr };
                                           })}/>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                      <div style={{ fontSize:10, color:"#92400e", marginTop:6 }}>
                        Scale 1.0 = no transform. Use e.g. 0.01 for torque values where PLC stores 2345 = 23.45 N·m.
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
            );
          })()}

          <div style={{ marginTop:16, display:"flex", justifyContent:"flex-end", gap:8 }}>
            {(() => {
              const closeForm = () => { setSelMach(null); setPlcForm({...BLANK_MACHINE_PLC}); setPlcOriginal({...BLANK_MACHINE_PLC}); setSubPage(0); };
              return plcDirty ? (
                <>
                  <Btn onClick={closeForm} disabled={saving}>Cancel</Btn>
                  <Btn variant="primary" onClick={savePLC} disabled={saving||!selLine}>{saving?"Saving…":"Update"}</Btn>
                </>
              ) : (
                <Btn onClick={closeForm}>Close</Btn>
              );
            })()}
          </div>
        </Card>
      )}
 
      {/* ── Sub-page 2: Monitor Config (NEW) ── */}
      {subPage === 2 && (
        <div style={{ maxWidth:860 }}>
 
          {!selMach ? (
            <Card>
              <p style={{ color:"#94a3b8", fontSize:13 }}>Select a machine from Sub-page ① first.</p>
            </Card>
          ) : monLoading ? (
            <Card>
              <p style={{ color:"#94a3b8", fontSize:13 }}>Loading config…</p>
            </Card>
          ) : (
            <Card>
              {/* Header */}
              <div style={{ display:"flex", justifyContent:"space-between", alignItems:"flex-start", marginBottom:22 }}>
                <div>
                  <h3 style={{ fontSize:15, fontWeight:700, color:"#0f172a", margin:0 }}>
                    Monitor Config — {selMach.machine_name || selMach.plc_ip}
                  </h3>
                  <p style={{ fontSize:12, color:"#64748b", margin:"4px 0 0" }}>
                    Polling bit, data registers, and loadcell channels read each cycle by the collector.
                  </p>
                </div>
                {monCfg && (
                  <Btn variant="danger" size="sm" onClick={deleteMonitorConfig}>Remove Config</Btn>
                )}
              </div>
 
              {/* ── Polling Bit ── */}
              <div style={{ marginBottom:24 }}>
                <div style={{ fontSize:11, fontWeight:700, color:"#1e40af", textTransform:"uppercase",
                  letterSpacing:".08em", marginBottom:8, display:"flex", alignItems:"center", gap:8 }}>
                  <span style={{ background:"#1e40af", color:"#fff", borderRadius:4,
                    padding:"1px 7px", fontSize:10 }}>REQUIRED</span>
                  Polling Bit
                </div>
                <div style={{ display:"flex", alignItems:"center", gap:12 }}>
                  <input
                    style={{ ...mini, width:150, fontFamily:"monospace", fontSize:14, fontWeight:700,
                      textTransform:"uppercase", letterSpacing:".05em" }}
                    type="text"
                    placeholder="e.g. M99"
                    value={pollingBit}
                    onChange={e => setPollingBit(e.target.value.toUpperCase())}
                  />
                  <span style={{ fontSize:12, color:"#64748b" }}>
                    PLC bit the collector reads each cycle to detect machine activity
                  </span>
                </div>
              </div>
 
              {/* ── Data Registers ── */}
              <div style={{ marginBottom:20, padding:16, borderRadius:8,
                border:`2px solid ${hasDataRegs ? "#3b82f6" : "#e2e8f0"}`,
                background: hasDataRegs ? "#f0f7ff" : "#fafafa", transition:"all .15s" }}>
 
                <label style={{ display:"flex", alignItems:"center", gap:10, cursor:"pointer", userSelect:"none" }}>
                  <input type="checkbox" checked={hasDataRegs}
                    onChange={e => { setHasDataRegs(e.target.checked); if (e.target.checked && dataRegs.length===0) addDataReg(); }}
                    style={{ width:16, height:16, accentColor:"#3b82f6", cursor:"pointer" }}/>
                  <span style={{ fontSize:13, fontWeight:700, color:"#0f172a" }}>Enable Data Registers</span>
                  <span style={{ fontSize:11, color:"#64748b" }}>— word/D-register values read each cycle (max 15)</span>
                </label>
 
                {hasDataRegs && (
                  <div style={{ marginTop:16 }}>
                    {/* Column headers */}
                    <div style={{ display:"grid", gridTemplateColumns:"130px 1fr 140px 32px",
                      gap:8, marginBottom:6, padding:"0 4px" }}>
                      {["Register","Label / Description","Desired Value",""].map((h,i)=>(
                        <span key={i} style={{ fontSize:10, fontWeight:700, color:"#475569",
                          textTransform:"uppercase", letterSpacing:".06em" }}>{h}</span>
                      ))}
                    </div>
 
                    {dataRegs.map((reg, i) => (
                      <div key={i} style={{ display:"grid", gridTemplateColumns:"130px 1fr 140px 32px",
                        gap:8, marginBottom:7, alignItems:"center" }}>
                        <input style={{ ...mini, fontFamily:"monospace", fontWeight:600, textTransform:"uppercase" }}
                          type="text" placeholder="D100" value={reg.register}
                          onChange={e => setDataReg(i,"register",e.target.value.toUpperCase())}/>
                        <input style={mini} type="text" placeholder="e.g. Torque Value" value={reg.label}
                          onChange={e => setDataReg(i,"label",e.target.value)}/>
                        <input style={{ ...mini, fontFamily:"monospace" }} type="number"
                          placeholder="e.g. 450" value={reg.desired_value ?? ""}
                          onChange={e => setDataReg(i,"desired_value",e.target.value)}/>
                        <button onClick={() => removeDataReg(i)}
                          style={{ border:"none", background:"#fee2e2", color:"#dc2626", borderRadius:6,
                            width:28, height:28, cursor:"pointer", fontSize:13, fontWeight:700,
                            display:"flex", alignItems:"center", justifyContent:"center" }}>✕</button>
                      </div>
                    ))}
 
                    <div style={{ display:"flex", alignItems:"center", gap:10, marginTop:10 }}>
                      <Btn size="sm" onClick={addDataReg} disabled={dataRegs.length >= 15}>+ Add Register</Btn>
                      {dataRegs.length >= 15 && <span style={{ fontSize:11, color:"#d97706" }}>Maximum 15 reached</span>}
                      <span style={{ fontSize:11, color:"#64748b", marginLeft:"auto" }}>
                        {dataRegs.filter(r=>r.register).length} / 15 configured
                      </span>
                    </div>
                  </div>
                )}
              </div>
 
              {/* ── Loadcell Registers ── */}
              <div style={{ marginBottom:24, padding:16, borderRadius:8,
                border:`2px solid ${hasLoadcell ? "#8b5cf6" : "#e2e8f0"}`,
                background: hasLoadcell ? "#faf5ff" : "#fafafa", transition:"all .15s" }}>
 
                <label style={{ display:"flex", alignItems:"center", gap:10, cursor:"pointer", userSelect:"none" }}>
                  <input type="checkbox" checked={hasLoadcell}
                    onChange={e => { setHasLoadcell(e.target.checked); if (e.target.checked && loadcellRegs.length===0) addLoadcell(); }}
                    style={{ width:16, height:16, accentColor:"#8b5cf6", cursor:"pointer" }}/>
                  <span style={{ fontSize:13, fontWeight:700, color:"#0f172a" }}>Enable Loadcell Monitoring</span>
                  <span style={{ fontSize:11, color:"#64748b" }}>— analog weight/force registers with min/max thresholds</span>
                </label>
 
                {hasLoadcell && (
                  <div style={{ marginTop:16 }}>
                    {/* Column headers */}
                    <div style={{ display:"grid", gridTemplateColumns:"130px 1fr 110px 110px 32px",
                      gap:8, marginBottom:6, padding:"0 4px" }}>
                      {["Register","Label / Description","Min Value","Max Value",""].map((h,i)=>(
                        <span key={i} style={{ fontSize:10, fontWeight:700, color:"#475569",
                          textTransform:"uppercase", letterSpacing:".06em" }}>{h}</span>
                      ))}
                    </div>
 
                    {loadcellRegs.map((lc, i) => (
                      <div key={i} style={{ display:"grid", gridTemplateColumns:"130px 1fr 110px 110px 32px",
                        gap:8, marginBottom:7, alignItems:"center" }}>
                        <input style={{ ...mini, fontFamily:"monospace", fontWeight:600, textTransform:"uppercase" }}
                          type="text" placeholder="D200" value={lc.register}
                          onChange={e => setLoadcell(i,"register",e.target.value.toUpperCase())}/>
                        <input style={mini} type="text" placeholder="e.g. Loadcell 1" value={lc.label}
                          onChange={e => setLoadcell(i,"label",e.target.value)}/>
                        <input style={{ ...mini, fontFamily:"monospace" }} type="number"
                          placeholder="Min" value={lc.min_value ?? ""}
                          onChange={e => setLoadcell(i,"min_value",e.target.value)}/>
                        <input style={{ ...mini, fontFamily:"monospace" }} type="number"
                          placeholder="Max" value={lc.max_value ?? ""}
                          onChange={e => setLoadcell(i,"max_value",e.target.value)}/>
                        <button onClick={() => removeLoadcell(i)}
                          style={{ border:"none", background:"#f3e8ff", color:"#7c3aed", borderRadius:6,
                            width:28, height:28, cursor:"pointer", fontSize:13, fontWeight:700,
                            display:"flex", alignItems:"center", justifyContent:"center" }}>✕</button>
                      </div>
                    ))}
 
                    <div style={{ display:"flex", alignItems:"center", gap:10, marginTop:10 }}>
                      <Btn size="sm" onClick={addLoadcell}>+ Add Loadcell Channel</Btn>
                      <span style={{ fontSize:11, color:"#64748b", marginLeft:"auto" }}>
                        {loadcellRegs.filter(r=>r.register).length} channel(s) configured
                      </span>
                    </div>
                  </div>
                )}
              </div>
 
              {/* ── Summary badges ── */}
              <div style={{ display:"flex", gap:8, marginBottom:20, flexWrap:"wrap" }}>
                <span style={{ padding:"3px 10px", borderRadius:99, fontSize:11, fontWeight:600,
                  background: pollingBit ? "#dcfce7" : "#fee2e2",
                  color: pollingBit ? "#15803d" : "#dc2626",
                  border:`1px solid ${pollingBit ? "#86efac" : "#fca5a5"}` }}>
                  {pollingBit ? `● Polling: ${pollingBit}` : "○ No polling bit set"}
                </span>
                {hasDataRegs && (
                  <span style={{ padding:"3px 10px", borderRadius:99, fontSize:11, fontWeight:600,
                    background:"#dbeafe", color:"#1d4ed8", border:"1px solid #93c5fd" }}>
                    ◈ {dataRegs.filter(r=>r.register).length} data register(s)
                  </span>
                )}
                {hasLoadcell && (
                  <span style={{ padding:"3px 10px", borderRadius:99, fontSize:11, fontWeight:600,
                    background:"#ede9fe", color:"#6d28d9", border:"1px solid #c4b5fd" }}>
                    ⊞ {loadcellRegs.filter(r=>r.register).length} loadcell channel(s)
                  </span>
                )}
              </div>
 
              {/* ── Action buttons ── */}
              <div style={{ display:"flex", justifyContent:"flex-end", gap:10 }}>
                <Btn onClick={() => {
                  setPollingBit(monCfg?.polling_bit || "");
                  setHasDataRegs(monCfg?.has_data_registers || false);
                  setDataRegs(monCfg?.data_registers || []);
                  setHasLoadcell(monCfg?.has_loadcell || false);
                  setLoadcellRegs(monCfg?.loadcell_registers || []);
                }}>Reset</Btn>
                <Btn variant="primary" onClick={saveMonitorConfig}
                  disabled={monSaving || !pollingBit.trim()}>
                  {monSaving ? "Saving…" : "Save Monitor Config"}
                </Btn>
              </div>
            </Card>
          )}
        </div>
      )}

      {/* ── Sub-page 3: Process Config ──────────────────────────────
          Per-machine process list with target value + actual-value PLC
          register.  Each row will eventually drive a bar-graph card
          (actual bars + target line) on the dedicated Process Graphs
          page.  Admin can add / remove / reorder rows freely. */}
      {subPage === 3 && (
        <div>
          <Card>
            <h3 style={{ fontSize:15, fontWeight:700, color:"#0f172a", marginBottom:6 }}>
              {selMach ? `Process Config — ${selMach.machine_name||selMach.plc_ip}` : "Process Config"}
            </h3>
            <div style={{ fontSize:12, color:"#64748b", marginBottom:18, lineHeight:1.5 }}>
              Some machines have 5 processes, some 7. Add one row per
              process, each with its own <b>name</b>, <b>target value</b>,
              and the <b>PLC register</b> where the live actual value
              comes from. The Process Graphs page renders each row as a
              bar chart (actual = bars, target = horizontal line, title = name).
            </div>

            {!selMach ? (
              <p style={{ color:"#94a3b8", fontSize:13, fontStyle:"italic" }}>
                Select a machine from sub-page ① to configure its processes.
              </p>
            ) : procLoading ? (
              <Spinner/>
            ) : (
              <>
                {/* Header row */}
                <div style={{ display:"grid",
                              gridTemplateColumns:"60px 1fr 130px 150px 110px 80px 60px",
                              gap:8, marginBottom:8,
                              padding:"6px 10px",
                              fontSize:9, fontWeight:800, letterSpacing:".08em",
                              color:"#64748b", textTransform:"uppercase",
                              background:"#f8fafc", borderRadius:8 }}>
                  <div>#</div>
                  <div>Process Name</div>
                  <div>Target Value</div>
                  <div>Actual PLC Register</div>
                  <div>Type</div>
                  <div>Active</div>
                  <div></div>
                </div>

                {/* Rows */}
                {procRows.length === 0 ? (
                  <div style={{ padding:"30px 12px", textAlign:"center",
                                 color:"#94a3b8", fontStyle:"italic", fontSize:12 }}>
                    No processes configured yet — click <b>+ Add Process</b> below.
                  </div>
                ) : procRows.map((p, idx) => (
                  <div key={idx} style={{ display:"grid",
                                           gridTemplateColumns:"60px 1fr 130px 150px 110px 80px 60px",
                                           gap:8, marginBottom:6, alignItems:"center",
                                           padding:"4px 0" }}>
                    <input style={mini} type="number" min="1"
                           value={p.process_no || idx + 1}
                           onChange={e => setProcRows(rs => {
                             const n = [...rs];
                             n[idx] = { ...n[idx], process_no: parseInt(e.target.value) || idx+1 };
                             return n;
                           })}/>
                    <input style={mini} type="text"
                           placeholder={`e.g. Pressing, Welding, …`}
                           value={p.process_name || ""}
                           onChange={e => setProcRows(rs => {
                             const n = [...rs];
                             n[idx] = { ...n[idx], process_name: e.target.value };
                             return n;
                           })}/>
                    <input style={mini} type="number" step="0.01" min="0"
                           value={p.target_value ?? 0}
                           onChange={e => setProcRows(rs => {
                             const n = [...rs];
                             n[idx] = { ...n[idx], target_value: parseFloat(e.target.value) || 0 };
                             return n;
                           })}/>
                    <input style={mini} type="text"
                           placeholder="e.g. D2000, M100, Y10"
                           value={p.actual_register || ""}
                           onChange={e => setProcRows(rs => {
                             const n = [...rs];
                             n[idx] = { ...n[idx], actual_register: e.target.value };
                             return n;
                           })}/>
                    <select style={mini}
                            value={p.register_type || "word"}
                            onChange={e => setProcRows(rs => {
                              const n = [...rs];
                              n[idx] = { ...n[idx], register_type: e.target.value };
                              return n;
                            })}>
                      <option value="word">Word</option>
                      <option value="bit">Bit</option>
                    </select>
                    <div style={{ textAlign:"center" }}>
                      <input type="checkbox"
                             checked={p.is_active !== false}
                             onChange={e => setProcRows(rs => {
                               const n = [...rs];
                               n[idx] = { ...n[idx], is_active: e.target.checked };
                               return n;
                             })}
                             style={{ width:18, height:18, cursor:"pointer", accentColor:"#1e40af" }}/>
                    </div>
                    <Btn size="sm" variant="danger"
                         onClick={() => setProcRows(rs => rs.filter((_,i) => i !== idx))}>
                      ×
                    </Btn>
                  </div>
                ))}

                <div style={{ display:"flex", gap:10, marginTop:14, paddingTop:14,
                                borderTop:"1px solid #e2e8f0", justifyContent:"space-between",
                                alignItems:"center", flexWrap:"wrap" }}>
                  <Btn onClick={() => setProcRows(rs => [...rs, {
                                process_no:      rs.length + 1,
                                process_name:    "",
                                target_value:    0,
                                actual_register: "",
                                register_type:   "word",
                                is_active:       true,
                              }])}>
                    + Add Process
                  </Btn>
                  <div style={{ display:"flex", gap:10 }}>
                    <Btn onClick={() => loadProcessConfig(selMach.id)}>Reset</Btn>
                    <Btn variant="primary" onClick={saveProcessConfig}
                         disabled={procSaving}>
                      {procSaving ? "Saving…" : `Save ${procRows.length} process${procRows.length===1?"":"es"}`}
                    </Btn>
                  </div>
                </div>

                {/* Latest values readout — useful for verifying the
                    register addresses are reading sane numbers before
                    relying on the graphs. */}
                {procRows.some(p => p.latest_value !== undefined) && (
                  <div style={{ marginTop:18, padding:14, background:"#f8fafc",
                                  border:"1px solid #e2e8f0", borderRadius:10 }}>
                    <div style={{ fontSize:11, fontWeight:700, color:"#64748b",
                                    letterSpacing:".08em", textTransform:"uppercase",
                                    marginBottom:8 }}>
                      Latest sampled values
                    </div>
                    <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fill,minmax(200px,1fr))",
                                    gap:10 }}>
                      {procRows.filter(p => p.latest_value !== undefined && p.latest_value !== null).map((p,i) => {
                        const target = Number(p.target_value || 0);
                        const actual = Number(p.latest_value || 0);
                        const ok     = target > 0 ? actual >= target : true;
                        return (
                          <div key={i} style={{
                            background:"#fff", border:`1.5px solid ${ok?"#16a34a":"#dc2626"}33`,
                            borderRadius:8, padding:"8px 10px",
                          }}>
                            <div style={{ fontSize:11, fontWeight:700, color:"#0f172a",
                                            whiteSpace:"nowrap", overflow:"hidden",
                                            textOverflow:"ellipsis" }}>
                              {p.process_name}
                            </div>
                            <div style={{ display:"flex", alignItems:"baseline",
                                            justifyContent:"space-between", marginTop:4 }}>
                              <span style={{ fontSize:18, fontWeight:800, color:ok?"#16a34a":"#dc2626" }}>
                                {actual}
                              </span>
                              <span style={{ fontSize:10, color:"#94a3b8" }}>
                                / target {target}
                              </span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </>
            )}
          </Card>
        </div>
      )}

      {/* Bit address edit modal — now inside PLC Config sub-page */}
      <Modal open={bitModal} onClose={()=>setBitModal(false)} title={`Edit Bit Addresses — ${selMach?.machine_name||""}`}>
        {selMach && (
          <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:"10px 16px" }}>
            {BIT_FIELDS.map(({key,label})=>(
              <FF key={key} label={label}>
                <input style={inputStyle} type="text" value={plcForm[key]||""} onChange={e=>setPlcForm(f=>({...f,[key]:e.target.value}))}/>
              </FF>
            ))}
          </div>
        )}
        <ModalActions>
          <Btn onClick={()=>setBitModal(false)}>Cancel</Btn>
          <Btn variant="primary" onClick={async()=>{ await savePLC(); setBitModal(false); const r=await api.get(`/api/lines/${selLine}/machines`,token); setMachines(Array.isArray(r)?r:[]); const updated=r.find?.(m=>m.id===selMach?.id); if(updated) selectMachine(updated); }} disabled={saving}>{saving?"Saving…":"Save Addresses"}</Btn>
        </ModalActions>
      </Modal>
 
    </div>
  );
}
// ─── CAMERA LIST PAGE ─────────────────────────────────────────
export function CameraListPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [grid, setGrid]       = useState([]);
  const [loading, setLoading] = useState(true);
  const [pings, setPings]     = useState({});  // { "192.168.30.115": {ok:true,ms:12} }
  const [pinging, setPinging] = useState(false);

  // ── Assign-camera modal state ─────────────────────────────────────────
  const [assignTarget, setAssignTarget] = useState(null); // row being assigned
  const [allCameras,   setAllCameras]   = useState([]);
  const [camLoading,   setCamLoading]   = useState(false);
  const [picked,       setPicked]       = useState("");
  const [saving,       setSaving]       = useState(false);

  const openAssign = async (machine) => {
    setAssignTarget(machine);
    setPicked(machine.camera_id || "");
    setCamLoading(true);
    try {
      const r = await api.get("/api/cms/cameras", token);
      const list = Array.isArray(r) ? r : (Array.isArray(r?.data) ? r.data : []);
      setAllCameras(list);
    } catch (e) { toast("Failed to load cameras list from CMS", "err"); setAllCameras([]); }
    finally { setCamLoading(false); }
  };

  const saveAssign = async () => {
    if (!assignTarget || !picked) { toast("Select a camera first","err"); return; }
    const { zone_id, line_id, machine_id } = assignTarget;
    if (!zone_id || !line_id || !machine_id) { toast("Machine is missing zone/line/id — can't assign","err"); return; }
    setSaving(true);
    try {
      await api.patch(
        `/api/cms/machines/${encodeURIComponent(zone_id)}/${encodeURIComponent(line_id)}/${encodeURIComponent(machine_id)}/camera`,
        { camera_id: picked },
        token,
      );
      toast("Camera assigned ✓");
      setAssignTarget(null);
      load();
    } catch (e) { toast(e.message || "Assign failed", "err"); }
    finally { setSaving(false); }
  };

  // Fetch camera grid from CMS backend (via /cms-api proxy)
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get("/api/cms/camera-grid", token);
      setGrid(Array.isArray(r) ? r : (Array.isArray(r?.data) ? r.data : []));
    } catch { toast("Failed to load camera grid from CMS portal", "err"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Ping all unique camera IPs
  const pingAll = useCallback(async (data) => {
    const items = data || grid;
    const ips = [...new Set(items.filter(m => m.camera_ip).map(m => m.camera_ip))];
    if (!ips.length) return;
    setPinging(true);
    const results = {};
    await Promise.allSettled(ips.map(async ip => {
      try {
        const r = await api.get(`/api/ping?ip=${encodeURIComponent(ip)}&port=554`, token);
        results[ip] = r;
      } catch { results[ip] = { ok: false, ms: 0 }; }
    }));
    setPings(results);
    setPinging(false);
  }, [grid, token]);

  // Ping on load and every 30s
  useEffect(() => {
    if (!grid.length) return;
    pingAll(grid);
    const t = setInterval(() => pingAll(), 30000);
    return () => clearInterval(t);
  }, [grid]); // eslint-disable-line

  // Group: zone → line → machines
  const grouped = {};
  grid.forEach(m => {
    const zk = m.zone_name || "Unknown Zone";
    const lk = m.line_name || "Unknown Line";
    if (!grouped[zk]) grouped[zk] = {};
    if (!grouped[zk][lk]) grouped[zk][lk] = [];
    grouped[zk][lk].push(m);
  });

  const zones = Object.keys(grouped).sort();
  const totalCams = grid.filter(m => m.has_camera).length;
  const onlineCount = Object.values(pings).filter(p => p.ok).length;
  const uniqueIPs = [...new Set(grid.filter(m => m.camera_ip).map(m => m.camera_ip))];

  return (
    <div>
      {/* Stats */}
      <div style={{display:"flex",gap:14,marginBottom:18,flexWrap:"wrap"}}>
        {[
          { label: "Machines",   val: grid.length,   color: "#1e40af" },
          { label: "With Camera",val: totalCams,      color: "#16a34a" },
          { label: "Unique IPs", val: uniqueIPs.length, color: "#7c3aed" },
          { label: "Online",     val: onlineCount,    color: "#16a34a" },
          { label: "Offline",    val: uniqueIPs.length - onlineCount, color: "#dc2626" },
        ].map(({ label, val, color }) => (
          <div key={label} style={{background:"#fff",border:"1px solid #e2e8f0",borderRadius:10,padding:"12px 18px",minWidth:100}}>
            <div style={{fontSize:11,color:"#64748b",fontWeight:600,textTransform:"uppercase",letterSpacing:".06em"}}>{label}</div>
            <div style={{fontSize:26,fontWeight:800,color}}>{val}</div>
          </div>
        ))}
        <div style={{marginLeft:"auto",display:"flex",alignItems:"center",gap:8}}>
          <Btn size="sm" onClick={() => pingAll()} disabled={pinging}>
            {pinging ? "Pinging..." : "Refresh Ping"}
          </Btn>
          <Btn size="sm" onClick={load}>Reload</Btn>
        </div>
      </div>

      {loading ? <Spinner /> : zones.length === 0 ? (
        <EmptyState text="No cameras found" sub="CMS portal returned no machine/camera data. Make sure the CMS backend is running on port 5000." />
      ) : (
        zones.map(zoneName => (
          <Card key={zoneName} style={{marginBottom:18}}>
            {/* Zone header */}
            <div style={{padding:"12px 16px",background:"#f8fafc",borderBottom:"1px solid #e2e8f0",display:"flex",alignItems:"center",gap:8}}>
              <span style={{fontSize:16}}>◎</span>
              <span style={{fontSize:14,fontWeight:800,color:"#0f172a"}}>{zoneName}</span>
              <span style={{fontSize:10,color:"#94a3b8",marginLeft:4}}>
                {Object.values(grouped[zoneName]).reduce((a, ms) => a + ms.length, 0)} machines
              </span>
            </div>

            {Object.keys(grouped[zoneName]).sort().map(lineName => {
              const machines = grouped[zoneName][lineName];
              return (
                <div key={lineName}>
                  {/* Line sub-header */}
                  <div style={{padding:"8px 16px 6px 32px",fontSize:11,fontWeight:700,color:"#1e40af",
                    letterSpacing:".06em",textTransform:"uppercase",borderBottom:"1px solid #f1f5f9"}}>
                    {lineName}
                  </div>

                  {/* Machine rows */}
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                    <thead>
                      <tr>
                        {["Machine","Camera","IP","Port","Status","Action"].map(h => (
                          <th key={h} style={{padding:"6px 14px 6px 32px",textAlign:"left",fontSize:9,fontWeight:700,
                            letterSpacing:".08em",textTransform:"uppercase",color:"#94a3b8",borderBottom:"1px solid #f1f5f9"}}>
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {machines.map((m, i) => {
                        const ping = m.camera_ip ? pings[m.camera_ip] : null;
                        const online = ping?.ok;
                        return (
                          <tr key={i} style={{borderBottom:"1px solid #f8fafc"}}>
                            <td style={{padding:"9px 14px 9px 32px",fontWeight:600,color:"#0f172a"}}>{m.machine_name || "—"}</td>
                            <td style={{padding:"9px 14px"}}>
                              {m.has_camera
                                ? <span style={{padding:"2px 9px",borderRadius:99,fontSize:10,fontWeight:700,
                                    background:"rgba(22,163,74,.1)",color:"#16a34a"}}>{m.camera_name || m.camera_id}</span>
                                : <span style={{color:"#cbd5e1",fontSize:11}}>No camera</span>}
                            </td>
                            <td style={{padding:"9px 14px",fontFamily:"monospace",fontWeight:700,color:m.camera_ip?"#7c3aed":"#cbd5e1",fontSize:11}}>
                              {m.camera_ip || "—"}
                            </td>
                            <td style={{padding:"9px 14px",fontFamily:"monospace",color:"#64748b",fontSize:11}}>
                              {m.camera_port || "—"}
                            </td>
                            <td style={{padding:"9px 14px"}}>
                              {!m.has_camera ? (
                                <span style={{display:"flex",alignItems:"center",gap:4,fontSize:10,color:"#cbd5e1"}}>
                                  <span style={{width:8,height:8,borderRadius:"50%",background:"#e2e8f0"}}/>N/A
                                </span>
                              ) : ping == null ? (
                                <span style={{fontSize:10,color:"#94a3b8"}}>...</span>
                              ) : online ? (
                                <span style={{display:"flex",alignItems:"center",gap:4,fontSize:10,fontWeight:700,color:"#16a34a"}}>
                                  <span style={{width:8,height:8,borderRadius:"50%",background:"#16a34a"}}/>Online
                                  <span style={{fontSize:9,color:"#94a3b8",fontWeight:500}}>{ping.ms}ms</span>
                                </span>
                              ) : (
                                <span style={{display:"flex",alignItems:"center",gap:4,fontSize:10,fontWeight:700,color:"#dc2626"}}>
                                  <span style={{width:8,height:8,borderRadius:"50%",background:"#dc2626"}}/>Offline
                                </span>
                              )}
                            </td>
                            <td style={{padding:"9px 14px"}}>
                              <Btn size="sm" variant={m.has_camera?"ghost":"primary"} onClick={()=>openAssign(m)}>
                                {m.has_camera ? "Change" : "Assign"}
                              </Btn>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              );
            })}
          </Card>
        ))
      )}

      {/* ── Assign Camera Modal ── */}
      <Modal
        open={!!assignTarget}
        onClose={()=>setAssignTarget(null)}
        title={`Assign Camera → ${assignTarget?.machine_name || ""}`}
      >
        {assignTarget && (
          <div>
            <div style={{fontSize:11,color:"#64748b",marginBottom:14,lineHeight:1.5}}>
              <b style={{color:"#0f172a"}}>{assignTarget.zone_name}</b> &nbsp;/&nbsp;
              <b style={{color:"#0f172a"}}>{assignTarget.line_name}</b> &nbsp;/&nbsp;
              <b style={{color:"#7c3aed",fontFamily:"monospace"}}>{assignTarget.machine_name}</b>
              {assignTarget.has_camera && (
                <div style={{marginTop:6,padding:"6px 10px",background:"rgba(22,163,74,.06)",borderRadius:6,display:"inline-block"}}>
                  Currently: <b style={{color:"#16a34a"}}>{assignTarget.camera_name || assignTarget.camera_id}</b>
                </div>
              )}
            </div>

            {camLoading ? <Spinner/> : allCameras.length === 0 ? (
              <EmptyState
                text="No cameras registered in CMS Portal"
                sub="Go to CMS Portal → Cameras → Add Camera first, then come back here."
              />
            ) : (
              <div style={{
                maxHeight:360, overflowY:"auto",
                display:"flex", flexDirection:"column", gap:6,
                border:"1px solid #e2e8f0", borderRadius:10, padding:10, background:"#f8fafc",
              }}>
                {allCameras.map(cam => {
                  const on = picked === cam.id;
                  const isCurrent = assignTarget.camera_id === cam.id;
                  return (
                    <label key={cam.id} style={{
                      display:"flex",alignItems:"center",gap:10,padding:"9px 12px",
                      background: on ? "rgba(30,64,175,.08)" : "#fff",
                      border: `1px solid ${on ? "rgba(30,64,175,.35)" : "#e2e8f0"}`,
                      borderRadius:8, cursor:"pointer",
                    }}>
                      <input type="radio" checked={on} onChange={()=>setPicked(cam.id)}/>
                      <div style={{flex:1,minWidth:0}}>
                        <div style={{fontSize:12,fontWeight:700,color:"#0f172a",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                          {cam.name}
                          {isCurrent && <span style={{marginLeft:8,fontSize:9,fontWeight:600,color:"#16a34a"}}>● current</span>}
                        </div>
                        <div style={{fontSize:10,color:"#64748b",fontFamily:"monospace",marginTop:2}}>
                          {cam.ip}:{cam.port || 554}  {cam.path ? `· ${cam.path}` : ""}
                        </div>
                      </div>
                      <span style={{fontSize:9,color:"#94a3b8",fontFamily:"monospace"}}>#{cam.id}</span>
                    </label>
                  );
                })}
              </div>
            )}
            <div style={{fontSize:10,color:"#94a3b8",marginTop:10}}>
              Select a camera and press <b>Assign</b>. Binding updates in CMS Portal
              and will be picked up by the video recorder automatically.
            </div>
            <ModalActions>
              <Btn onClick={()=>setAssignTarget(null)}>Cancel</Btn>
              <Btn variant="primary" onClick={saveAssign} disabled={saving || !picked}>
                {saving ? "Saving…" : "Assign"}
              </Btn>
            </ModalActions>
          </div>
        )}
      </Modal>
    </div>
  );
}

// ─── DEPARTMENTS PAGE ─────────────────────────────────────────
// Admin-managed master list of departments.  Seeded with Maintenance and
// Quality at install time; admin can add more (e.g. Tool Room) anytime.
// Department users (role='department') are bound to a row here via
// mes_admin.department_id and the SlideNav labels their Department Panel
// item with this row's `name` (e.g. "Maintenance Panel").
export function DepartmentsPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ name:"", slug:"", description:"" });
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get("/api/departments/", token);
      setRows(Array.isArray(r) ? r : []);
    } catch (e) { toast(e.message || "Load failed", "err"); }
    finally    { setLoading(false); }
  }, [token, toast]);
  useEffect(() => { load(); }, [load]);

  const openCreate = () => {
    setEditing(null);
    setForm({ name:"", slug:"", description:"" });
    setModal(true);
  };
  const openEdit = (row) => {
    setEditing(row);
    setForm({ name: row.name || "", slug: row.slug || "", description: row.description || "" });
    setModal(true);
  };
  const save = async () => {
    if (!form.name.trim()) { toast("Name is required", "err"); return; }
    setSaving(true);
    try {
      // Backend auto-derives slug from name if blank — let it.
      const body = {
        name: form.name.trim(),
        slug: form.slug.trim() || null,
        description: form.description.trim() || null,
      };
      if (editing) await api.put(`/api/departments/${editing.id}`, body, token);
      else         await api.post("/api/departments/", body, token);
      toast(editing ? "Department updated ✓" : "Department added ✓");
      setModal(false);
      load();
    } catch (e) { toast(e.message || "Save failed", "err"); }
    finally    { setSaving(false); }
  };
  const remove = async (r) => {
    if (!confirm(`Delete department "${r.name}"?\n\nUsers bound to this department will keep their role but lose the department link.`)) return;
    try {
      await api.delete(`/api/departments/${r.id}`, token);
      toast("Removed");
      load();
    } catch (e) { toast(e.message || "Delete failed", "err"); }
  };

  return (
    <div>
      <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:14 }}>
        <div>
          <div style={{ fontWeight:700, fontSize:15, color:"#0f172a" }}>Departments</div>
          <div style={{ fontSize:11, color:"#64748b", marginTop:2 }}>
            Master list of departments.&nbsp; Each department user is bound to one row here, and the slide-nav labels their panel as <b>"{`{Name}`} Panel"</b>.&nbsp; Add new departments (e.g. Tool Room, Stores) as needed.
          </div>
        </div>
        <Btn variant="primary" onClick={openCreate}>+ Add Department</Btn>
      </div>

      <Card>
        {loading ? <Spinner /> : rows.length === 0 ? (
          <EmptyState text="No departments yet" sub="Click + Add Department to get started." />
        ) : (
          <table style={{ width:"100%", borderCollapse:"collapse", fontSize:13 }}>
            <thead>
              <tr>{["ID","Name","Slug","Description","Created","Actions"].map(h => (
                <th key={h} style={{ padding:"10px 14px", textAlign:"left", fontSize:10, fontWeight:700, letterSpacing:".08em", textTransform:"uppercase", color:"#64748b", borderBottom:"2px solid #e2e8f0" }}>{h}</th>
              ))}</tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id} style={{ borderBottom:"1px solid #f1f5f9" }}>
                  <td style={{ padding:"12px 14px", fontFamily:"monospace", color:"#64748b" }}>{r.id}</td>
                  <td style={{ padding:"12px 14px", fontWeight:700, color:"#0f172a" }}>{r.name}</td>
                  <td style={{ padding:"12px 14px", fontFamily:"monospace", fontSize:11, color:"#475569" }}>{r.slug}</td>
                  <td style={{ padding:"12px 14px", color:"#475569", fontSize:12 }}>
                    {r.description || <span style={{ color:"#cbd5e1" }}>—</span>}
                  </td>
                  <td style={{ padding:"12px 14px", fontFamily:"monospace", fontSize:11, color:"#64748b" }}>
                    {r.created_at ? new Date(r.created_at).toLocaleDateString("en-IN") : "—"}
                  </td>
                  <td style={{ padding:"12px 14px" }}>
                    <div style={{ display:"flex", gap:8 }}>
                      <Btn size="sm" onClick={() => openEdit(r)}>Edit</Btn>
                      <Btn size="sm" variant="danger" onClick={() => remove(r)}>Delete</Btn>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Modal open={modal} onClose={() => setModal(false)}
             title={editing ? "Edit Department" : "Add Department"}>
        <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:14 }}>
          <FF label="Name *" hint="Display name shown in slide-nav (e.g. 'Maintenance', 'Tool Room').">
            <Input value={form.name}
                   onChange={e => setForm(f => ({ ...f, name:e.target.value }))}
                   placeholder="e.g. Tool Room"/>
          </FF>
          <FF label="Slug" hint="URL-safe identifier — auto-derived from Name if left blank.">
            <Input value={form.slug}
                   onChange={e => setForm(f => ({ ...f, slug:e.target.value.toLowerCase() }))}
                   placeholder="auto"/>
          </FF>
          <div style={{ gridColumn:"1 / -1" }}>
            <FF label="Description (optional)">
              <Input value={form.description}
                     onChange={e => setForm(f => ({ ...f, description:e.target.value }))}
                     placeholder="What does this department do?"/>
            </FF>
          </div>
        </div>
        <ModalActions>
          <Btn onClick={() => setModal(false)}>Cancel</Btn>
          <Btn variant="primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : editing ? "Update" : "Create"}
          </Btn>
        </ModalActions>
      </Modal>
    </div>
  );
}


// (Closure form is now hardcoded to the Toyota Boshoku BREAK DOWN SLIP
//  layout in MaintenanceDashboard.jsx — admin no longer configures fields.)


// ─── BREAKDOWN MAILS PAGE ─────────────────────────────────────
// CRUD over `mes_breakdown_mail_levels` — admin defines the escalation
// chain (Level 1 fires immediately, Level 2 after delay_minutes, etc.).
// A background worker (Phase2/routers/breakdown_mail.py) polls every
// 30 s and sends each level's mail once when its delay has elapsed —
// only as long as the breakdown is still OPEN.  When the line goes
// back to RUNNING (collector resolves the row), no more levels fire.
// 2026-06-09 — NG → Quality mail recipients, set live from this panel.
// Saves to DB (mes_quality_mail_config); the "Send to Quality" mail reads it
// first, so a change takes effect on the next report — no restart.
function NgMailConfig() {
  const lineId = 2;   // YNC-SS
  const token = (typeof window !== "undefined" && sessionStorage.getItem("mes_token")) || "";
  const [to, setTo]         = useState("");
  const [cc, setCc]         = useState("");
  const [meta, setMeta]     = useState(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg]       = useState("");

  useEffect(() => {
    fetch(`/api/lines/${lineId}/quality-mail-config`,
          { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { setTo(d.to_emails || d.env_to || ""); setCc(d.cc_emails || d.env_cc || "");
                   setMeta(d); })
      .catch(() => {});
  }, [token]);

  const saveCfg = () => {
    if (!token) { setMsg("✗ Login required"); return; }
    setSaving(true); setMsg("");
    fetch(`/api/lines/${lineId}/quality-mail-config`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ to_emails: to.trim(), cc_emails: cc.trim() }),
    })
      .then(r => r.ok ? r.json() : r.text().then(t => Promise.reject(t)))
      .then(d => { setMsg("✓ Saved successfully");
                   setMeta(m => ({ ...(m || {}), updated_by: d.updated_by,
                                   updated_at: new Date().toISOString() })); })
      .catch(e => setMsg("✗ " + String(e).slice(0, 160)))
      .finally(() => setSaving(false));
  };

  const lab = { fontSize: 10, fontWeight: 700, color: "#475569",
                textTransform: "uppercase", letterSpacing: ".05em" };
  const inp = { width: "100%", padding: "6px 10px", fontSize: 13, borderRadius: 6,
                border: "1px solid #cbd5e1", marginTop: 3, boxSizing: "border-box" };
  return (
    <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8,
                  padding: "14px 16px", marginTop: 16, boxShadow: "0 1px 4px rgba(0,0,0,0.06)" }}>
      <div style={{ fontWeight: 700, fontSize: 15, color: "#0f172a" }}>NG → Quality Mail Recipients</div>
      <div style={{ fontSize: 11, color: "#64748b", marginTop: 2, marginBottom: 10 }}>
        These addresses receive the “Send to Quality” NG report.
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8, alignItems: "end" }}>
        <div>
          <label style={lab}>To (comma-separated)</label>
          <input value={to} onChange={e => setTo(e.target.value)}
                 placeholder="quality@co.com, lead@co.com" style={inp} />
        </div>
        <div>
          <label style={lab}>Cc (optional)</label>
          <input value={cc} onChange={e => setCc(e.target.value)}
                 placeholder="optional" style={inp} />
        </div>
        <button onClick={saveCfg} disabled={saving} style={{
          padding: "8px 18px", borderRadius: 6, border: "none", background: "#16a34a",
          color: "#fff", fontSize: 13, fontWeight: 700, cursor: "pointer", height: 34 }}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
      <div style={{ fontSize: 10, marginTop: 6,
                    color: msg.startsWith("✓") ? "#16a34a"
                         : msg.startsWith("✗") ? "#dc2626" : "#94a3b8" }}>
        {msg || (meta && meta.updated_at
          ? `Last updated by ${meta.updated_by || "—"} · ${new Date(meta.updated_at).toLocaleString("en-IN")}`
          : "")}
      </div>
    </div>
  );
}

export function BreakdownMailsPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState(false);
  const [editing, setEditing] = useState(null);
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState(null);

  const EMPTY = {
    level_no: "", label: "", delay_minutes: 0,
    to_addresses: "", cc_addresses: "", is_active: true,
  };
  const [form, setForm] = useState(EMPTY);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get("/api/breakdown-mails/", token);
      setRows(Array.isArray(r) ? r : []);
    } catch (e) { toast(e.message || "Load failed", "err"); }
    finally    { setLoading(false); }
  }, [token, toast]);
  useEffect(() => { load(); }, [load]);

  const openCreate = () => {
    const nextLvl = rows.length ? Math.max(...rows.map(r => r.level_no || 0)) + 1 : 1;
    setEditing(null);
    setForm({ ...EMPTY, level_no: nextLvl,
              delay_minutes: nextLvl === 1 ? 0 : (nextLvl - 1) * 10 });
    setModal(true);
  };
  const openEdit = (r) => {
    setEditing(r);
    setForm({
      level_no:      r.level_no,
      label:         r.label || "",
      delay_minutes: r.delay_minutes ?? 0,
      to_addresses:  r.to_addresses || "",
      cc_addresses:  r.cc_addresses || "",
      is_active:     r.is_active !== false,
    });
    setModal(true);
  };

  const save = async () => {
    if (!form.level_no || form.level_no < 1) {
      toast("Level No. is required and must be ≥ 1", "err"); return;
    }
    if (form.delay_minutes < 0) {
      toast("Delay must be ≥ 0", "err"); return;
    }
    setSaving(true);
    try {
      const body = {
        level_no:      Number(form.level_no),
        label:         form.label?.trim() || null,
        delay_minutes: Number(form.delay_minutes) || 0,
        to_addresses:  form.to_addresses || "",
        cc_addresses:  form.cc_addresses || "",
        is_active:     !!form.is_active,
      };
      if (editing) await api.put(`/api/breakdown-mails/${editing.id}`, body, token);
      else         await api.post("/api/breakdown-mails/", body, token);
      toast(editing ? "Updated ✓" : "Added ✓");
      setModal(false);
      load();
    } catch (e) { toast(e.message || "Save failed", "err"); }
    finally    { setSaving(false); }
  };

  const remove = async (r) => {
    if (!confirm(`Delete escalation Level ${r.level_no}${r.label?` — ${r.label}`:""}?`)) return;
    try {
      await api.delete(`/api/breakdown-mails/${r.id}`, token);
      toast("Removed");
      load();
    } catch (e) { toast(e.message || "Delete failed", "err"); }
  };

  const sendTest = async (r) => {
    if (!confirm(`Send a test email for Level ${r.level_no} to:\n  To: ${r.to_addresses || "(none)"}\n  Cc: ${r.cc_addresses || "(none)"}`)) return;
    setTestingId(r.id);
    try {
      await api.post(`/api/breakdown-mails/${r.id}/test`, {}, token);
      toast("Test email sent ✓");
    } catch (e) { toast(e.message || "Send failed", "err"); }
    finally    { setTestingId(null); }
  };

  return (
    <div>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:14,gap:10,flexWrap:"wrap"}}>
        <div>
          <div style={{fontWeight:700,fontSize:15,color:"#0f172a"}}>Breakdown Escalation Mails</div>
          <div style={{fontSize:11,color:"#64748b",marginTop:2,maxWidth:760,lineHeight:1.5}}>
            Defines the chain of emails that fire while a line is in BREAKDOWN status.&nbsp;
            Level 1 (delay&nbsp;=&nbsp;0) fires the moment the breakdown is detected; subsequent
            levels fire after their <b>delay (minutes)</b> elapses, but only if the line is
            <i> still down</i>.&nbsp; If the line returns to RUNNING before a level fires, that
            level (and all later ones) is skipped.
          </div>
        </div>
        <Btn variant="primary" onClick={openCreate}>+ Add Level</Btn>
      </div>

      <Card>
        {loading ? <Spinner /> : rows.length === 0 ? (
          <EmptyState text="No escalation levels configured" sub="Add at least one — typically L1 immediate, L2 +10m, L3 +20m, etc." />
        ) : (
          <table style={{width:"100%",borderCollapse:"collapse",fontSize:13}}>
            <thead>
              <tr>{["Level","Label","Delay","To","Cc","Active","Actions"].map(h=>(
                <th key={h} style={{padding:"10px 14px",textAlign:"left",fontSize:10,fontWeight:700,letterSpacing:".08em",textTransform:"uppercase",color:"#64748b",borderBottom:"2px solid #e2e8f0"}}>{h}</th>
              ))}</tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id} style={{borderBottom:"1px solid #f1f5f9", opacity: r.is_active ? 1 : 0.5}}>
                  <td style={{padding:"10px 14px",fontFamily:"'Barlow Condensed',sans-serif",fontSize:18,fontWeight:800,color:"#dc2626"}}>L{r.level_no}</td>
                  <td style={{padding:"10px 14px",fontWeight:700,color:"#0f172a"}}>{r.label || <span style={{color:"#cbd5e1"}}>—</span>}</td>
                  <td style={{padding:"10px 14px"}}>
                    <span style={{padding:"3px 10px",borderRadius:99,fontSize:11,fontWeight:700,background:"rgba(217,119,6,.10)",color:"#b45309",fontFamily:"monospace"}}>
                      {r.delay_minutes === 0 ? "Immediate" : `+${r.delay_minutes}m`}
                    </span>
                  </td>
                  <td style={{padding:"10px 14px",fontFamily:"monospace",fontSize:11,color:r.to_addresses?"#16a34a":"#dc2626",maxWidth:300,wordBreak:"break-all"}}>
                    {r.to_addresses || <span style={{color:"#dc2626"}}>&lt;not set&gt;</span>}
                  </td>
                  <td style={{padding:"10px 14px",fontFamily:"monospace",fontSize:11,color:"#64748b",maxWidth:240,wordBreak:"break-all"}}>
                    {r.cc_addresses || <span style={{color:"#cbd5e1"}}>—</span>}
                  </td>
                  <td style={{padding:"10px 14px",fontWeight:700,fontSize:11,color:r.is_active?"#16a34a":"#94a3b8"}}>
                    {r.is_active ? "Yes" : "Off"}
                  </td>
                  <td style={{padding:"10px 14px"}}>
                    <div style={{display:"flex",gap:6}}>
                      <Btn size="sm" onClick={()=>openEdit(r)}>Edit</Btn>
                      <Btn size="sm" disabled={testingId===r.id || !r.to_addresses}
                            onClick={()=>sendTest(r)}>
                        {testingId===r.id ? "Sending…" : "Send Test"}
                      </Btn>
                      <Btn size="sm" variant="danger" onClick={()=>remove(r)}>Delete</Btn>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* 2026-06-09 — NG → Quality mail recipients (set live, no restart) */}
      <NgMailConfig />

      <Modal open={modal} onClose={()=>setModal(false)}
             title={editing ? `Edit Level ${editing.level_no}` : "Add Escalation Level"} wide>
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:14}}>
          <FF label="Level No. *" hint="Order in the chain — L1 fires first.">
            <Input type="number" value={form.level_no}
                   onChange={e=>setForm(f=>({...f,level_no:e.target.value}))}/>
          </FF>
          <FF label="Delay (minutes) *" hint="Minutes after breakdown started_at. 0 = fire immediately.">
            <Input type="number" value={form.delay_minutes}
                   onChange={e=>setForm(f=>({...f,delay_minutes:e.target.value}))}
                   placeholder="0"/>
          </FF>
          <div style={{ gridColumn:"1 / -1" }}>
            <FF label="Label (optional)" hint="Shown in the email subject + admin grid.">
              <Input value={form.label}
                     onChange={e=>setForm(f=>({...f,label:e.target.value}))}
                     placeholder="e.g. HOD Maintenance"/>
            </FF>
          </div>
          <div style={{ gridColumn:"1 / -1" }}>
            <FF label="To addresses *" hint="Comma-separated list (e.g. a@x.com, b@x.com)">
              <Input value={form.to_addresses}
                     onChange={e=>setForm(f=>({...f,to_addresses:e.target.value}))}
                     placeholder="hod.maint@plant.com, supervisor@plant.com"/>
            </FF>
          </div>
          <div style={{ gridColumn:"1 / -1" }}>
            <FF label="Cc addresses (optional)" hint="Comma-separated list">
              <Input value={form.cc_addresses}
                     onChange={e=>setForm(f=>({...f,cc_addresses:e.target.value}))}
                     placeholder="plant.head@plant.com"/>
            </FF>
          </div>
          <div style={{ gridColumn:"1 / -1", marginTop:4 }}>
            <label style={{ display:"flex", alignItems:"center", gap:8, fontSize:13, cursor:"pointer" }}>
              <input type="checkbox" checked={!!form.is_active}
                     onChange={e=>setForm(f=>({...f,is_active:e.target.checked}))}/>
              Active &nbsp;<span style={{color:"#94a3b8",fontSize:11,fontWeight:400}}>
                — uncheck to keep the level configured but stop it from firing.
              </span>
            </label>
          </div>
        </div>
        <ModalActions>
          <Btn onClick={()=>setModal(false)}>Cancel</Btn>
          <Btn variant="primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : editing ? "Update" : "Add Level"}
          </Btn>
        </ModalActions>
      </Modal>
    </div>
  );
}


// ─── KPI TARGETS PAGE ─────────────────────────────────────────
// CRUD over `mes_kpi_targets`.  line_id NULL = plant-wide default;
// per-line rows override the plant-wide one for that line only.
const KPI_KEYS = [
  { key: "mtbf_hours",         label: "MTBF",                unit: "hours",   direction: "higher" },
  { key: "mttr_minutes",       label: "MTTR",                unit: "minutes", direction: "lower"  },
  { key: "availability_pct",   label: "Availability",        unit: "%",       direction: "higher" },
  { key: "breakdowns_count",   label: "Total breakdowns",    unit: "count",   direction: "lower"  },
  { key: "total_downtime_min", label: "Total downtime",      unit: "minutes", direction: "lower"  },
  { key: "pending_closures",   label: "Pending closures",    unit: "count",   direction: "lower"  },
];

export function KpiTargetsPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [rows, setRows]   = useState([]);
  const [lines, setLines] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState(false);
  const [editing, setEditing] = useState(null);
  const [saving, setSaving] = useState(false);
  const EMPTY = {
    kpi_key: KPI_KEYS[0].key,
    line_id: "",            // "" = plant-wide
    target_value: "",
    unit: KPI_KEYS[0].unit,
    direction: KPI_KEYS[0].direction,
    is_active: true,
  };
  const [form, setForm] = useState(EMPTY);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [r, l] = await Promise.all([
        api.get("/api/maintenance-kpi/targets", token),
        api.get("/api/lines/", token),
      ]);
      setRows(Array.isArray(r) ? r : []);
      setLines(Array.isArray(l) ? l : []);
    } catch (e) { toast(e.message || "Load failed", "err"); }
    finally    { setLoading(false); }
  }, [token, toast]);
  useEffect(() => { load(); }, [load]);

  const openCreate = () => {
    setEditing(null); setForm(EMPTY); setModal(true);
  };
  const openEdit = (r) => {
    setEditing(r);
    setForm({
      kpi_key:      r.kpi_key,
      line_id:      r.line_id ?? "",
      target_value: r.target_value,
      unit:         r.unit || "",
      direction:    r.direction || "higher",
      is_active:    r.is_active !== false,
    });
    setModal(true);
  };

  const onPickKpi = (key) => {
    const meta = KPI_KEYS.find(k => k.key === key) || KPI_KEYS[0];
    setForm(f => ({ ...f, kpi_key: key, unit: meta.unit, direction: meta.direction }));
  };

  const save = async () => {
    if (!form.kpi_key || form.target_value === "" || form.target_value == null) {
      toast("KPI + target value are required", "err"); return;
    }
    setSaving(true);
    try {
      const body = {
        kpi_key:      form.kpi_key,
        line_id:      form.line_id === "" ? null : Number(form.line_id),
        target_value: Number(form.target_value),
        unit:         form.unit || null,
        direction:    form.direction || "higher",
        is_active:    !!form.is_active,
      };
      if (editing) await api.put(`/api/maintenance-kpi/targets/${editing.id}`, body, token);
      else         await api.post("/api/maintenance-kpi/targets", body, token);
      toast(editing ? "Updated ✓" : "Saved ✓");
      setModal(false);
      load();
    } catch (e) { toast(e.message || "Save failed", "err"); }
    finally    { setSaving(false); }
  };

  const remove = async (r) => {
    if (!confirm(`Delete target ${r.kpi_key} for ${r.line_id ? r.line_name|| "—" : "Plant-wide"}?`)) return;
    try { await api.delete(`/api/maintenance-kpi/targets/${r.id}`, token); toast("Removed"); load(); }
    catch (e) { toast(e.message || "Delete failed", "err"); }
  };

  const labelFor = (k) => (KPI_KEYS.find(x => x.key === k)?.label) || k;

  return (
    <div>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:14,gap:10,flexWrap:"wrap"}}>
        <div>
          <div style={{fontWeight:700,fontSize:15,color:"#0f172a"}}>Maintenance KPI Targets</div>
          <div style={{fontSize:11,color:"#64748b",marginTop:2,maxWidth:760,lineHeight:1.5}}>
            Targets drive the pass/fail badge on the Maintenance Dashboard's KPI cards.&nbsp;
            <b>line_id NULL</b> = plant-wide default; add a per-line row to override the plant
            default for a specific line.&nbsp;
            <b>Direction</b> tells the dashboard whether higher (e.g. MTBF) or lower
            (e.g. MTTR) is the goal.
          </div>
        </div>
        <Btn variant="primary" onClick={openCreate}>+ Add Target</Btn>
      </div>

      <Card>
        {loading ? <Spinner /> : rows.length === 0 ? (
          <EmptyState text="No KPI targets configured" sub="Add at least one — usually plant-wide defaults for MTBF / MTTR / Availability." />
        ) : (
          <table style={{width:"100%",borderCollapse:"collapse",fontSize:13}}>
            <thead>
              <tr>{["KPI","Scope","Target","Direction","Active","Actions"].map(h=>(
                <th key={h} style={{padding:"10px 14px",textAlign:"left",fontSize:10,fontWeight:700,letterSpacing:".08em",textTransform:"uppercase",color:"#64748b",borderBottom:"2px solid #e2e8f0"}}>{h}</th>
              ))}</tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id} style={{borderBottom:"1px solid #f1f5f9", opacity: r.is_active ? 1 : 0.5}}>
                  <td style={{padding:"10px 14px",fontWeight:700,color:"#0f172a"}}>{labelFor(r.kpi_key)}
                    <div style={{fontSize:10,color:"#7c3aed",fontFamily:"monospace",fontWeight:600}}>{r.kpi_key}</div>
                  </td>
                  <td style={{padding:"10px 14px"}}>
                    {r.line_id == null ? (
                      <span style={{padding:"2px 9px",borderRadius:99,fontSize:10,fontWeight:700,background:"rgba(30,64,175,.10)",color:"#1e40af"}}>PLANT</span>
                    ) : (
                      <span style={{padding:"2px 9px",borderRadius:99,fontSize:10,fontWeight:700,background:"rgba(124,58,237,.10)",color:"#6d28d9"}}>{r.line_name || "—"}</span>
                    )}
                  </td>
                  <td style={{padding:"10px 14px",fontFamily:"'Barlow Condensed',sans-serif",fontSize:18,fontWeight:800,color:"#0f172a"}}>
                    {r.target_value} <span style={{fontSize:11,fontWeight:600,color:"#64748b",fontFamily:"inherit"}}>{r.unit}</span>
                  </td>
                  <td style={{padding:"10px 14px",fontWeight:700,color: r.direction==="higher" ? "#16a34a" : "#b45309"}}>
                    {r.direction === "higher" ? "↑ higher is better" : "↓ lower is better"}
                  </td>
                  <td style={{padding:"10px 14px",fontWeight:700,fontSize:11,color:r.is_active?"#16a34a":"#94a3b8"}}>
                    {r.is_active ? "Yes" : "Off"}
                  </td>
                  <td style={{padding:"10px 14px"}}>
                    <div style={{display:"flex",gap:6}}>
                      <Btn size="sm" onClick={()=>openEdit(r)}>Edit</Btn>
                      <Btn size="sm" variant="danger" onClick={()=>remove(r)}>Delete</Btn>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Modal open={modal} onClose={()=>setModal(false)}
             title={editing ? "Edit KPI Target" : "Add KPI Target"} wide>
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:14}}>
          <FF label="KPI *" hint="Picks the unit + direction defaults.">
            <Select value={form.kpi_key} onChange={e=>onPickKpi(e.target.value)}>
              {KPI_KEYS.map(k => <option key={k.key} value={k.key}>{k.label} ({k.key})</option>)}
            </Select>
          </FF>
          <FF label="Line scope" hint="Blank = plant-wide default.">
            <Select value={form.line_id} onChange={e=>setForm(f=>({...f,line_id:e.target.value}))}>
              <option value="">Plant-wide (all lines)</option>
              {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
            </Select>
          </FF>
          <FF label="Target value *">
            <Input type="number" step="any" value={form.target_value}
                   onChange={e=>setForm(f=>({...f,target_value:e.target.value}))}/>
          </FF>
          <FF label="Unit">
            <Input value={form.unit}
                   onChange={e=>setForm(f=>({...f,unit:e.target.value}))}
                   placeholder="hours / minutes / % / count"/>
          </FF>
          <FF label="Direction *" hint="Which side beats the target.">
            <Select value={form.direction} onChange={e=>setForm(f=>({...f,direction:e.target.value}))}>
              <option value="higher">↑ Higher is better</option>
              <option value="lower">↓ Lower is better</option>
            </Select>
          </FF>
          <div style={{ alignSelf:"end", paddingBottom:6 }}>
            <label style={{ display:"flex", alignItems:"center", gap:8, fontSize:13, cursor:"pointer" }}>
              <input type="checkbox" checked={!!form.is_active}
                     onChange={e=>setForm(f=>({...f,is_active:e.target.checked}))}/>
              Active &nbsp;<span style={{color:"#94a3b8",fontSize:11,fontWeight:400}}>
                — uncheck to keep the row but stop using it for verdicts.
              </span>
            </label>
          </div>
        </div>
        <ModalActions>
          <Btn onClick={()=>setModal(false)}>Cancel</Btn>
          <Btn variant="primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : editing ? "Update" : "Save"}
          </Btn>
        </ModalActions>
      </Modal>
    </div>
  );
}


// ─── CAPA SETTINGS PAGE ───────────────────────────────────────
// Admin-editable Pareto cutoff %.  The /api/capa/pareto-config endpoint
// is the single source of truth — the same value is shown (read-only)
// on the Maintenance CAPA page's Pareto chart, and editing it here
// instantly reflects there via the global ap-config-changed event.
//
// Why split this out: the user wanted the *threshold* command to live
// in the Admin Maintenance Panel, not on the Maintenance dashboard
// itself.  Maintenance dept users never see this tab (the Maintenance
// section in their /admin/maintenance is rendered read-only), but they
// see the live value in their CAPA Pareto chart.
export function CapaSettingsPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  // All three GLOBAL knobs come from /api/capa/pareto-config — single
  // PUT updates them as a unit so admin has one save button.
  const [pct,     setPct]     = useState(80);
  const [monthly, setMonthly] = useState(120);
  const [single,  setSingle]  = useState(60);
  const [original, setOrig]   = useState({ pct:80, monthly:120, single:60 });
  const [loading, setLoading] = useState(true);
  const [saving,  setSaving]  = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api.get("/api/capa/pareto-config", token);
      setPct    (r.pareto_pct                     ?? 80);
      setMonthly(r.monthly_sum_minutes_limit      ?? 120);
      setSingle (r.single_breakdown_minutes_limit ?? 60);
      setOrig({ pct: r.pareto_pct ?? 80,
                monthly: r.monthly_sum_minutes_limit ?? 120,
                single:  r.single_breakdown_minutes_limit ?? 60 });
    } catch { toast?.("Failed to load CAPA settings", "err"); }
    finally { setLoading(false); }
  }, [token, toast]);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (pct     < 1 || pct     > 100)    { toast?.("Pareto cutoff must be 1–100", "err"); return; }
    if (monthly < 1 || monthly > 99999)  { toast?.("Monthly threshold must be 1–99999 min", "err"); return; }
    if (single  < 1 || single  > 99999)  { toast?.("Single threshold must be 1–99999 min", "err"); return; }
    setSaving(true);
    try {
      await api.put("/api/capa/pareto-config", {
        pareto_pct: pct,
        monthly_sum_minutes_limit:      monthly,
        single_breakdown_minutes_limit: single,
      }, token);
      toast?.("CAPA settings saved ✓");
      setOrig({ pct, monthly, single });
    } catch (e) { toast?.(e.message || "Save failed", "err"); }
    finally   { setSaving(false); }
  };

  const dirty = pct !== original.pct
             || monthly !== original.monthly
             || single  !== original.single;
  const reset = () => { setPct(original.pct); setMonthly(original.monthly); setSingle(original.single); };

  return (
    <div className={readOnly ? "ap-readonly" : ""}>
      <fieldset disabled={readOnly} style={{border:0, padding:0, margin:0, minWidth:0}}>
      <Card style={{ padding: 24 }}>
        <div style={{ fontSize:14, fontWeight:700, color:"#0f172a", marginBottom:6 }}>
          CAPA Thresholds &amp; Auto-Mandate Cutoff
        </div>
        <div style={{ fontSize:12, color:"#64748b", marginBottom:22, lineHeight:1.5 }}>
          Three numbers drive every CAPA in the plant.  Per-line and
          per-machine rows can override these globals from <b>POST /api/capa/thresholds</b>;
          this page edits the GLOBAL defaults that apply when no override exists.
        </div>

        {loading ? (
          <Spinner/>
        ) : (
          <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))",
                         gap:18, marginBottom:18 }}>
            {/* Monthly sum threshold */}
            <FF label="Monthly Sum Threshold (min)"
                hint="Per-machine breakdown ceiling per calendar month. Crosses this → joins the breached cohort.">
              <Input type="number" min="1" max="99999"
                     value={monthly}
                     onChange={e => setMonthly(Number(e.target.value) || 0)}
                     style={{ fontFamily:"monospace", fontWeight:700,
                              fontSize:20, textAlign:"center" }}/>
            </FF>

            {/* Single breakdown threshold */}
            <FF label="Single Breakdown Threshold (min)"
                hint="Per-event ceiling. A single closed breakdown crossing this fires an immediate SINGLE_LIMIT CAPA.">
              <Input type="number" min="1" max="99999"
                     value={single}
                     onChange={e => setSingle(Number(e.target.value) || 0)}
                     style={{ fontFamily:"monospace", fontWeight:700,
                              fontSize:20, textAlign:"center" }}/>
            </FF>

            {/* Pareto cutoff % */}
            <FF label="Pareto Cutoff %"
                hint="Of the breached cohort, the top N% by cumulative breakdown minutes MUST file CAPA.">
              <Input type="number" min="1" max="100"
                     value={pct}
                     onChange={e => setPct(Number(e.target.value) || 0)}
                     style={{ fontFamily:"monospace", fontWeight:700,
                              fontSize:20, textAlign:"center" }}/>
            </FF>
          </div>
        )}

        <div style={{ display:"flex", gap:10, alignItems:"center" }}>
          <Btn variant="primary" onClick={save} disabled={saving || !dirty}>
            {saving ? "Saving…" : dirty ? "Save Changes" : "Saved ✓"}
          </Btn>
          {dirty && <Btn onClick={reset}>Cancel</Btn>}
          {!dirty && !loading && (
            <span style={{ fontSize:11, color:"#94a3b8" }}>
              No pending changes
            </span>
          )}
        </div>

        <div style={{ marginTop:26, padding:14, background:"#f8fafc",
                       border:"1px solid #e2e8f0", borderRadius:10 }}>
          <div style={{ fontSize:11, fontWeight:700, color:"#64748b",
                         letterSpacing:".08em", textTransform:"uppercase",
                         marginBottom:8 }}>
            How it works
          </div>
          <div style={{ fontSize:12, color:"#334155", lineHeight:1.7 }}>
            <div>1.  The collector aggregates closed breakdown minutes per machine for the calendar month.</div>
            <div>2.  Each calendar month resets every machine's counter to 0 on the 1st.</div>
            <div>3.  Machines whose monthly sum &gt; <b style={{color:"#dc2626"}}>{monthly} min</b> join the breached cohort.</div>
            <div>4.  Any single closed breakdown &gt; <b style={{color:"#dc2626"}}>{single} min</b> fires an immediate SINGLE_LIMIT CAPA.</div>
            <div>5.  The cohort is sorted descending by total breakdown minutes.</div>
            <div>6.  Cumulative % is computed across the breached cohort only.</div>
            <div>7.  The top <b style={{color:"#dc2626"}}>{pct}%</b> of cumulative time → <b>must file CAPA / QPR</b>.</div>
          </div>
        </div>
      </Card>
      </fieldset>
    </div>
  );
}


// ─── BREAKDOWN SLIP RAISE THRESHOLD ───────────────────────────
// Operator's clarified ask:
//
//   "Some breakdowns get fixed in 5–10 minutes — those don't need a
//    full slip.  Set ONE threshold: if a breakdown takes LONGER than
//    X minutes to resolve, the formal slip is RAISED (full closure
//    form mandatory).  Below X minutes, only Production logs basic
//    details — no slip needed."
//
// So this page exposes a single integer (default 10 min).  The
// breakdown lifecycle endpoints will read this to decide whether to
// move a resolved breakdown straight to CLOSED (tier='MINOR') or to
// RESOLVED with a mandatory slip (tier='MAJOR').

// ════════════════════════════════════════════════════════════════════
// NewRequestsPanel — admin's audit panel for PY remarks submitted
// from the Maintenance > Poka Yoke page.
// 2026-05-21 — Operator spec: "remarks ka option if any changes are
// required so mention changes are save in audit panel name as new
// panel new requests jisme sari details ho bs mujhe vha jha k pta
// chal jaye ki whats are input from users".
//
// Endpoints used:
//   GET    /api/poka-yoke/requests?status=NEW|REVIEWED|RESOLVED&days=N
//   PUT    /api/poka-yoke/requests/{id}/resolve  body={status, note}
//   DELETE /api/poka-yoke/requests/{id}
// ════════════════════════════════════════════════════════════════════
export function NewRequestsPanel({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [rows,    setRows]    = useState([]);
  const [counts,  setCounts]  = useState({});
  const [loading, setLoading] = useState(true);
  const [filter,  setFilter]  = useState("NEW");   // NEW | REVIEWED | RESOLVED | ALL
  const [days,    setDays]    = useState(30);
  const [expanded,setExpanded]= useState({});      // {id: bool}
  const [resolveModal, setResolveModal] = useState(null);  // {req, status}
  const [noteText, setNoteText] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const qs = `days=${days}` + (filter !== "ALL" ? `&status=${filter}` : "");
      const r = await api.get(`/api/poka-yoke/requests?${qs}`, token);
      setRows(r?.rows || []);
      setCounts(r?.by_status || {});
    } catch (e) {
      if (toast) toast(`Failed to load: ${String(e).slice(0, 60)}`, "err");
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [filter, days, token, toast]);

  useEffect(() => { load(); }, [load]);

  // Auto-refresh every 30 s
  useEffect(() => {
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  const onResolveSubmit = async () => {
    if (!resolveModal) return;
    const { req, status } = resolveModal;
    try {
      await api.put(`/api/poka-yoke/requests/${req.id}/resolve`, token, {
        status, resolution_note: noteText.trim() || null,
      });
      if (toast) toast(`Request #${req.id} → ${status}`, "ok");
      setResolveModal(null);
      setNoteText("");
      load();
    } catch (e) {
      if (toast) toast(`Update failed: ${String(e).slice(0, 60)}`, "err");
    }
  };

  const onDelete = async (req) => {
    if (!confirm(`Delete request #${req.id} (PY ${req.py_no})?  Cannot be undone.`)) return;
    try {
      await api.delete(`/api/poka-yoke/requests/${req.id}`, token);
      if (toast) toast(`Deleted #${req.id}`, "ok");
      load();
    } catch (e) {
      if (toast) toast(`Delete failed: ${String(e).slice(0, 60)}`, "err");
    }
  };

  const fmt = (ts) => {
    if (!ts) return "—";
    try {
      const d = new Date(ts);
      return d.toLocaleString("en-GB", { day:"2-digit", month:"short",
                                          year:"2-digit", hour:"2-digit",
                                          minute:"2-digit" });
    } catch { return ts; }
  };

  const statusPill = (s) => {
    const colors = {
      NEW:      { bg:"#fef3c7", fg:"#92400e" },
      REVIEWED: { bg:"#dbeafe", fg:"#1e40af" },
      RESOLVED: { bg:"#d1fae5", fg:"#065f46" },
    };
    const c = colors[s] || colors.NEW;
    return (
      <span style={{
        fontSize:10, fontWeight:800, padding:"3px 9px",
        borderRadius:99, background:c.bg, color:c.fg,
        letterSpacing:".05em",
      }}>{s}</span>
    );
  };

  return (
    <div style={{ padding:"16px 40px" }}>
      <div style={{
        display:"flex", alignItems:"center", justifyContent:"space-between",
        marginBottom:14,
      }}>
        <div>
          <h2 style={{ fontSize:22, fontWeight:800, color:"#0f172a", margin:0 }}>
            📝 New Requests — Maintenance Audit
          </h2>
          <p style={{ fontSize:12, color:"#64748b", margin:"4px 0 0 0" }}>
            Operator-submitted PY remarks &amp; change requests.  Auto-refresh every 30 s.
          </p>
        </div>
        <button onClick={load}
                style={{
                  fontSize:12, padding:"6px 14px",
                  background:"#0369a1", color:"#fff",
                  border:"none", borderRadius:6, cursor:"pointer",
                  fontWeight:700,
                }}>
          ↻ Refresh
        </button>
      </div>

      {/* Filter chips */}
      <div style={{ display:"flex", gap:8, marginBottom:14, alignItems:"center",
                     flexWrap:"wrap" }}>
        {["NEW", "REVIEWED", "RESOLVED", "ALL"].map(s => {
          const active = filter === s;
          const n = s === "ALL"
            ? Object.values(counts).reduce((a,b) => a + (b || 0), 0)
            : (counts[s] || 0);
          return (
            <button key={s} onClick={() => setFilter(s)}
              style={{
                fontSize:11, fontWeight:700, padding:"6px 14px",
                background: active ? "#0f172a" : "#f1f5f9",
                color:    active ? "#fff"    : "#475569",
                border:   "none", borderRadius:99, cursor:"pointer",
                letterSpacing:".05em",
              }}>
              {s} <span style={{ opacity:.7, marginLeft:6 }}>({n})</span>
            </button>
          );
        })}
        <span style={{ marginLeft:"auto", fontSize:11, color:"#64748b" }}>
          Lookback:
          <select value={days} onChange={e => setDays(+e.target.value)}
            style={{ fontSize:11, padding:"3px 8px", marginLeft:6,
                     border:"1px solid #cbd5e1", borderRadius:4 }}>
            <option value={7}>7 days</option>
            <option value={30}>30 days</option>
            <option value={90}>90 days</option>
            <option value={365}>1 year</option>
          </select>
        </span>
      </div>

      {/* Table */}
      {loading ? (
        <div style={{ padding:40, textAlign:"center", color:"#94a3b8" }}>
          Loading…
        </div>
      ) : !rows.length ? (
        <div style={{ padding:40, textAlign:"center", color:"#94a3b8",
                       fontStyle:"italic" }}>
          No requests found for this filter.
        </div>
      ) : (
        <div style={{ background:"#fff", border:"1px solid #e2e8f0",
                       borderRadius:10, overflow:"hidden" }}>
          <table style={{ width:"100%", borderCollapse:"collapse", fontSize:12 }}>
            <thead>
              <tr style={{ background:"#f8fafc",
                            borderBottom:"2px solid #e2e8f0" }}>
                {["#", "PY", "Sensor", "Bit", "Line/Zone",
                  "Remark", "Submitted", "By", "Status", "Actions"].map(h =>
                  <th key={h} style={{
                    padding:"10px 12px", fontSize:9, fontWeight:800,
                    letterSpacing:".08em", color:"#64748b",
                    textAlign:"left", whiteSpace:"nowrap",
                  }}>{h}</th>
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r.id}>
                  <tr style={{ borderBottom:"1px solid #f1f5f9" }}>
                    <td style={{ padding:"10px 12px",
                                  fontFamily:"monospace", color:"#64748b" }}>
                      #{r.id}
                    </td>
                    <td style={{ padding:"10px 12px",
                                  fontFamily:"monospace", fontWeight:700 }}>
                      {r.py_no}
                      {r.py_name && (
                        <div style={{ fontSize:9, color:"#94a3b8",
                                       fontWeight:400, marginTop:1 }}>
                          {r.py_name}
                        </div>
                      )}
                    </td>
                    <td style={{ padding:"10px 12px",
                                  fontFamily:"monospace", color:"#0369a1",
                                  fontWeight:700 }}>
                      {r.sensing_bits || "—"}
                    </td>
                    <td style={{ padding:"10px 12px",
                                  fontFamily:"monospace", color:"#475569" }}>
                      {r.bit || "—"}
                    </td>
                    <td style={{ padding:"10px 12px", color:"#475569" }}>
                      {r.line_name || "—"}
                      {r.zone_name && (
                        <div style={{ fontSize:9, color:"#94a3b8" }}>
                          {r.zone_name}
                        </div>
                      )}
                    </td>
                    <td style={{ padding:"10px 12px", maxWidth:280 }}>
                      <div style={{
                        whiteSpace: expanded[r.id] ? "normal" : "nowrap",
                        overflow:"hidden", textOverflow:"ellipsis",
                        cursor:"pointer",
                      }}
                      onClick={() => setExpanded(e => ({...e, [r.id]: !e[r.id]}))}
                      title={r.remark}>
                        {r.remark}
                      </div>
                    </td>
                    <td style={{ padding:"10px 12px",
                                  fontFamily:"monospace", fontSize:10,
                                  color:"#64748b" }}>
                      {fmt(r.submitted_at)}
                    </td>
                    <td style={{ padding:"10px 12px", color:"#475569" }}>
                      {r.submitted_by_username || "—"}
                    </td>
                    <td style={{ padding:"10px 12px" }}>
                      {statusPill(r.status)}
                      {r.status === "RESOLVED" && r.resolution_note && (
                        <div style={{ fontSize:9, color:"#94a3b8",
                                       marginTop:4, fontStyle:"italic" }}
                             title={r.resolution_note}>
                          ↪ {r.resolution_note.slice(0, 40)}
                          {r.resolution_note.length > 40 ? "…" : ""}
                        </div>
                      )}
                    </td>
                    <td style={{ padding:"10px 12px" }}>
                      {!readOnly && (
                        <div style={{ display:"flex", gap:4 }}>
                          {r.status === "NEW" && (
                            <button onClick={() => {
                                      setResolveModal({ req:r, status:"REVIEWED" });
                                      setNoteText("");
                                    }}
                                    style={{
                                      fontSize:10, padding:"3px 8px",
                                      background:"#dbeafe", color:"#1e40af",
                                      border:"none", borderRadius:4,
                                      fontWeight:700, cursor:"pointer",
                                    }}>
                              Mark Reviewed
                            </button>
                          )}
                          {r.status !== "RESOLVED" && (
                            <button onClick={() => {
                                      setResolveModal({ req:r, status:"RESOLVED" });
                                      setNoteText(r.resolution_note || "");
                                    }}
                                    style={{
                                      fontSize:10, padding:"3px 8px",
                                      background:"#d1fae5", color:"#065f46",
                                      border:"none", borderRadius:4,
                                      fontWeight:700, cursor:"pointer",
                                    }}>
                              Resolve
                            </button>
                          )}
                          <button onClick={() => onDelete(r)}
                                  style={{
                                    fontSize:10, padding:"3px 8px",
                                    background:"#fee2e2", color:"#b91c1c",
                                    border:"none", borderRadius:4,
                                    fontWeight:700, cursor:"pointer",
                                  }}
                                  title="Delete request">
                            ✕
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                  {expanded[r.id] && (
                    <tr style={{ background:"#f8fafc" }}>
                      <td colSpan={10} style={{ padding:"10px 16px",
                                                  fontSize:11, color:"#475569" }}>
                        <strong>Full remark:</strong> {r.remark}
                        {r.machine_name && (
                          <span style={{ marginLeft:16 }}>
                            <strong>Machine:</strong> {r.machine_name}
                          </span>
                        )}
                        {r.expected && (
                          <span style={{ marginLeft:16 }}>
                            <strong>Expected:</strong> {r.expected}
                          </span>
                        )}
                        {r.resolved_by_username && (
                          <span style={{ marginLeft:16 }}>
                            <strong>Resolved by:</strong> {r.resolved_by_username}
                            {" @ "}{fmt(r.resolved_at)}
                          </span>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Resolve modal */}
      {resolveModal && (
        <div style={{
          position:"fixed", inset:0, background:"rgba(0,0,0,.5)",
          display:"flex", alignItems:"center", justifyContent:"center",
          zIndex:1000,
        }}
        onClick={() => setResolveModal(null)}>
          <div onClick={e => e.stopPropagation()}
               style={{
                 background:"#fff", padding:24, borderRadius:10,
                 maxWidth:520, width:"90%", boxShadow:"0 20px 60px rgba(0,0,0,.3)",
               }}>
            <h3 style={{ margin:"0 0 6px 0", fontSize:18, fontWeight:800 }}>
              {resolveModal.status === "RESOLVED" ? "Resolve" : "Mark Reviewed"}: Request #{resolveModal.req.id}
            </h3>
            <p style={{ fontSize:12, color:"#64748b", margin:"0 0 14px 0" }}>
              PY {resolveModal.req.py_no} · {resolveModal.req.sensing_bits || resolveModal.req.bit}
            </p>
            <div style={{ fontSize:12, color:"#475569", marginBottom:14,
                           padding:10, background:"#f8fafc", borderRadius:6,
                           borderLeft:"3px solid #cbd5e1" }}>
              <strong>Operator remark:</strong> {resolveModal.req.remark}
            </div>
            <label style={{ fontSize:11, fontWeight:700, color:"#475569",
                             display:"block", marginBottom:6 }}>
              Resolution note {resolveModal.status === "RESOLVED" ? "(recommended)" : "(optional)"}:
            </label>
            <textarea value={noteText}
                      onChange={e => setNoteText(e.target.value)}
                      rows={3}
                      placeholder="e.g. PLC ladder updated, sensor X15 now wired to LOCATE PIN"
                      style={{
                        width:"100%", fontSize:12, padding:"8px 10px",
                        border:"1px solid #cbd5e1", borderRadius:6,
                        resize:"vertical", fontFamily:"inherit",
                      }}/>
            <div style={{ display:"flex", gap:10, justifyContent:"flex-end",
                           marginTop:16 }}>
              <button onClick={() => setResolveModal(null)}
                      style={{ fontSize:12, padding:"8px 16px",
                               background:"#f1f5f9", color:"#475569",
                               border:"none", borderRadius:6,
                               fontWeight:700, cursor:"pointer" }}>
                Cancel
              </button>
              <button onClick={onResolveSubmit}
                      style={{ fontSize:12, padding:"8px 16px",
                               background:"#0369a1", color:"#fff",
                               border:"none", borderRadius:6,
                               fontWeight:700, cursor:"pointer" }}>
                {resolveModal.status === "RESOLVED" ? "Save Resolution" : "Mark Reviewed"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


// ════════════════════════════════════════════════════════════════════
// PyManualsPage — admin UI to manage per-PY visual manual:
//   • Instructions / follow-steps text
//   • Reference images (upload, delete, caption)
// Operator sees these read-only on Maintenance > Poka Yoke > 📷 icon.
// 2026-05-21 — Spec: "image set krne ka option ... maintenance panel
// jha maintenance setting hoti h ... kuch instruction ya follow steps
// bhi add krne ka option bhi dede or same py me visual".
// ════════════════════════════════════════════════════════════════════
export function PyManualsPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [lines,    setLines]    = useState([]);
  const [lineId,   setLineId]   = useState(null);
  const [pys,      setPys]      = useState([]);
  const [loading,  setLoading]  = useState(false);
  const [editor,   setEditor]   = useState(null);   // {py} when open

  // Load lines once
  useEffect(() => {
    (async () => {
      try {
        const ls = await api.get("/api/lines/", token);
        setLines(Array.isArray(ls) ? ls : []);
        if (ls?.[0]?.id) setLineId(ls[0].id);
      } catch (e) {
        if (toast) toast(`Failed to load lines: ${String(e).slice(0, 60)}`, "err");
      }
    })();
  }, [token, toast]);

  // Load PYs whenever line changes
  const loadPys = useCallback(async () => {
    if (!lineId) return;
    setLoading(true);
    try {
      const rt = await api.get(`/api/lines/${lineId}/realtime`, token).catch(() => ({}));
      const modelBit = rt?.current_model_number;
      const qs = modelBit != null && modelBit !== 0
        ? `?model_bit=${modelBit}`
        : "";
      const data = await api.get(`/api/poka-yoke/live/${lineId}${qs}`, token);
      // /live returns {pys: [...]} OR a flat array depending on version
      const list = Array.isArray(data) ? data
                 : (data?.pys || data?.checks || []);
      setPys(list);
    } catch (e) {
      if (toast) toast(`Failed to load PYs: ${String(e).slice(0, 60)}`, "err");
      setPys([]);
    } finally {
      setLoading(false);
    }
  }, [lineId, token, toast]);

  useEffect(() => { loadPys(); }, [loadPys]);

  return (
    <div style={{ padding:"16px 40px" }}>
      <div style={{ display:"flex", justifyContent:"space-between",
                     alignItems:"center", marginBottom:14 }}>
        <div>
          <h2 style={{ fontSize:22, fontWeight:800, color:"#0f172a", margin:0 }}>
            📷 PY Visual Manuals
          </h2>
          <p style={{ fontSize:12, color:"#64748b", margin:"4px 0 0 0" }}>
            Upload reference images and write follow-step instructions for each PY.
            Operators see these read-only on Maintenance &gt; Poka Yoke.
          </p>
        </div>
        <div>
          <label style={{ fontSize:11, color:"#64748b", marginRight:8 }}>
            Line:
          </label>
          <select value={lineId || ""} onChange={e => setLineId(+e.target.value)}
            style={{ fontSize:12, padding:"5px 10px",
                     border:"1px solid #cbd5e1", borderRadius:6 }}>
            {lines.map(l => (
              <option key={l.id} value={l.id}>
                {l.line_name || "—"}
              </option>
            ))}
          </select>
          <button onClick={loadPys}
            style={{ fontSize:12, padding:"5px 12px", marginLeft:8,
                     background:"#0369a1", color:"#fff",
                     border:"none", borderRadius:6, cursor:"pointer",
                     fontWeight:700 }}>
            ↻
          </button>
        </div>
      </div>

      {loading ? (
        <div style={{ padding:40, textAlign:"center", color:"#94a3b8" }}>Loading…</div>
      ) : !pys?.length ? (
        <div style={{ padding:40, textAlign:"center", color:"#94a3b8",
                       fontStyle:"italic" }}>
          No PYs for the current model on this line.
        </div>
      ) : (
        <div style={{ background:"#fff", border:"1px solid #e2e8f0",
                       borderRadius:10, overflow:"hidden" }}>
          <table style={{ width:"100%", borderCollapse:"collapse", fontSize:12 }}>
            <thead>
              <tr style={{ background:"#f8fafc",
                            borderBottom:"2px solid #e2e8f0" }}>
                {["PY No.", "Name", "Sensor", "Bit", "Side", "Manual"].map(h =>
                  <th key={h} style={{
                    padding:"10px 12px", fontSize:9, fontWeight:800,
                    letterSpacing:".08em", color:"#64748b",
                    textAlign:"left", whiteSpace:"nowrap",
                  }}>{h}</th>
                )}
              </tr>
            </thead>
            <tbody>
              {pys.map((p, i) => (
                <tr key={i} style={{ borderBottom:"1px solid #f1f5f9" }}>
                  <td style={{ padding:"10px 12px", fontFamily:"monospace",
                                fontWeight:700 }}>
                    {p.poka_yoke_no}
                  </td>
                  <td style={{ padding:"10px 12px" }}>
                    {p.poka_yoke_name || "—"}
                  </td>
                  <td style={{ padding:"10px 12px", fontFamily:"monospace",
                                color:"#0369a1", fontWeight:700 }}>
                    {p.sensing_bits || "—"}
                  </td>
                  <td style={{ padding:"10px 12px", fontFamily:"monospace",
                                color:"#475569" }}>
                    {p.bit || "—"}
                  </td>
                  <td style={{ padding:"10px 12px", color:"#64748b" }}>
                    {p.side || "ALL"}
                  </td>
                  <td style={{ padding:"10px 12px" }}>
                    <button onClick={() => setEditor({ py: p })}
                      disabled={readOnly}
                      style={{
                        fontSize:11, padding:"5px 12px",
                        background:"#0369a1", color:"#fff",
                        border:"none", borderRadius:5, cursor:"pointer",
                        fontWeight:700,
                      }}>
                      📷 Manage
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editor && (
        <PyManualEditorModal
          py={editor.py}
          lineId={lineId}
          token={token}
          toast={toast}
          onClose={() => setEditor(null)}
        />
      )}
    </div>
  );
}


// ── Editor modal (admin-only image upload + instructions edit) ──────
function PyManualEditorModal({ py, lineId, token, toast, onClose }) {
  const [images,   setImages]   = useState([]);
  const [instText, setInstText] = useState("");
  const [instId,   setInstId]   = useState(null);
  const [loading,  setLoading]  = useState(true);
  const [busy,     setBusy]     = useState(false);
  const [savedTs,  setSavedTs]  = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const qs = new URLSearchParams();
      qs.set("py_no", py.poka_yoke_no);
      if (lineId != null) qs.set("line_id", String(lineId));
      const [imgR, insR] = await Promise.all([
        fetch(`/api/poka-yoke/images?${qs}`,
              { headers: { Authorization: `Bearer ${token}` } }),
        fetch(`/api/poka-yoke/instructions?${qs}`,
              { headers: { Authorization: `Bearer ${token}` } }),
      ]);
      if (imgR.ok) {
        const d = await imgR.json();
        setImages(d.rows || []);
      }
      if (insR.ok) {
        const d = await insR.json();
        const row = (d.rows || [])[0];
        setInstText(row?.instruction_text || "");
        setInstId(row?.id || null);
      }
    } catch (e) {
      if (toast) toast(`Load failed: ${String(e).slice(0, 60)}`, "err");
    } finally {
      setLoading(false);
    }
  }, [py.poka_yoke_no, lineId, token, toast]);

  useEffect(() => { refresh(); }, [refresh]);

  const onUpload = async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    setBusy(true);
    let ok = 0, fail = 0;
    for (const f of files) {
      const fd = new FormData();
      fd.append("file", f);
      const qs = new URLSearchParams();
      qs.set("py_no", py.poka_yoke_no);
      if (lineId != null) qs.set("line_id", String(lineId));
      if (py.py_master_id) qs.set("py_master_id", String(py.py_master_id));
      try {
        const r = await fetch(`/api/poka-yoke/images?${qs}`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          body: fd,
        });
        if (r.ok) ok++; else fail++;
      } catch { fail++; }
    }
    setBusy(false);
    e.target.value = "";
    if (toast) toast(`Uploaded ${ok}/${files.length}` +
                     (fail ? ` (${fail} failed)` : ""),
                     fail ? "err" : "ok");
    refresh();
  };

  const onDeleteImg = async (img) => {
    if (!confirm(`Delete "${img.original_filename}"?`)) return;
    try {
      const r = await fetch(`/api/poka-yoke/images/${img.id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.ok) {
        if (toast) toast("Image deleted", "ok");
        refresh();
      } else {
        if (toast) toast(`Delete failed: HTTP ${r.status}`, "err");
      }
    } catch (e) {
      if (toast) toast(`Delete error: ${String(e).slice(0, 60)}`, "err");
    }
  };

  const onSaveInstructions = async () => {
    setBusy(true);
    try {
      const r = await fetch("/api/poka-yoke/instructions", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
        },
        body: JSON.stringify({
          py_no:        py.poka_yoke_no,
          line_id:      lineId,
          py_master_id: py.py_master_id || null,
          instruction_text: instText,
        }),
      });
      if (r.ok) {
        const d = await r.json();
        setInstId(d.id);
        setSavedTs(new Date().toLocaleTimeString("en-GB"));
        if (toast) toast("Instructions saved", "ok");
      } else {
        if (toast) toast(`Save failed: HTTP ${r.status}`, "err");
      }
    } catch (e) {
      if (toast) toast(`Save error: ${String(e).slice(0, 60)}`, "err");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{
      position:"fixed", inset:0, background:"rgba(0,0,0,.65)",
      display:"flex", alignItems:"center", justifyContent:"center",
      zIndex:1000,
    }}
    onClick={onClose}>
      <div onClick={e => e.stopPropagation()}
           style={{
             background:"#fff", padding:24, borderRadius:10,
             maxWidth:880, width:"92%", maxHeight:"88vh",
             overflowY:"auto",
             boxShadow:"0 20px 60px rgba(0,0,0,.4)",
           }}>
        <div style={{ display:"flex", justifyContent:"space-between",
                       alignItems:"flex-start", marginBottom:14 }}>
          <div>
            <h3 style={{ margin:0, fontSize:18, fontWeight:800, color:"#0f172a" }}>
              📷 Manage Manual: {py.poka_yoke_no}
            </h3>
            <div style={{ fontSize:11, color:"#64748b", marginTop:4 }}>
              {py.poka_yoke_name || "—"}
              {py.sensing_bits && <span> · Sensor <strong style={{ color:"#0369a1" }}>{py.sensing_bits}</strong></span>}
              {py.bit && <span> · Bit <strong>{py.bit}</strong></span>}
            </div>
          </div>
          <button onClick={onClose}
                  style={{ fontSize:18, padding:"4px 10px",
                           background:"transparent", color:"#64748b",
                           border:"none", cursor:"pointer", fontWeight:700 }}>
            ✕
          </button>
        </div>

        {/* Instructions edit */}
        <div style={{ marginBottom:18 }}>
          <label style={{ fontSize:11, fontWeight:800, color:"#475569",
                           letterSpacing:".06em", display:"block", marginBottom:6 }}>
            📋 INSTRUCTIONS / FOLLOW STEPS
          </label>
          <textarea
            value={instText}
            onChange={e => setInstText(e.target.value)}
            disabled={busy}
            rows={6}
            placeholder={`Example:\n1. Place part on jig\n2. Ensure sensor X15 reads HIGH before pressing OK\n3. If sensor not triggering, check cable B-12 and reset PLC bit M101\n4. Call maintenance if persists > 2 cycles`}
            style={{
              width:"100%", fontSize:12, padding:"10px 12px",
              border:"1px solid #cbd5e1", borderRadius:6,
              resize:"vertical", fontFamily:"inherit",
              minHeight:120, lineHeight:1.5,
            }}/>
          <div style={{ display:"flex", justifyContent:"space-between",
                         alignItems:"center", marginTop:6 }}>
            <span style={{ fontSize:10, color:"#94a3b8" }}>
              Plain text. Line breaks preserved. Operator sees this exactly.
              {savedTs && <span style={{ color:"#16a34a", marginLeft:10 }}>✓ Saved at {savedTs}</span>}
            </span>
            <button onClick={onSaveInstructions} disabled={busy}
              style={{ fontSize:12, padding:"6px 16px",
                       background:"#0369a1", color:"#fff",
                       border:"none", borderRadius:6, cursor:"pointer",
                       fontWeight:700 }}>
              {busy ? "Saving…" : "Save Instructions"}
            </button>
          </div>
        </div>

        <div style={{ borderTop:"1px solid #e2e8f0", margin:"18px 0" }}/>

        {/* Image upload */}
        <div>
          <label style={{ fontSize:11, fontWeight:800, color:"#475569",
                           letterSpacing:".06em", display:"block", marginBottom:6 }}>
            🖼  REFERENCE IMAGES
          </label>
          <div style={{
            padding:12, marginBottom:14,
            border:"2px dashed #cbd5e1", borderRadius:8,
            background:"#f8fafc", textAlign:"center",
          }}>
            <label style={{
              display:"inline-block", padding:"8px 18px",
              background:"#0369a1", color:"#fff", borderRadius:6,
              fontSize:12, fontWeight:700, cursor:"pointer",
              letterSpacing:".05em",
            }}>
              + Upload Image(s)
              <input type="file" multiple accept="image/*"
                     onChange={onUpload}
                     disabled={busy}
                     style={{ display:"none" }}/>
            </label>
            <div style={{ fontSize:10, color:"#94a3b8", marginTop:6 }}>
              PNG, JPG, GIF, WEBP, BMP · max 10 MB each · select multiple at once
            </div>
          </div>
          {loading ? (
            <div style={{ padding:20, textAlign:"center", color:"#94a3b8" }}>Loading…</div>
          ) : !images?.length ? (
            <div style={{ padding:20, textAlign:"center", color:"#94a3b8",
                           fontStyle:"italic", fontSize:11 }}>
              No images uploaded yet.
            </div>
          ) : (
            <div style={{
              display:"grid",
              gridTemplateColumns:"repeat(auto-fill, minmax(180px, 1fr))",
              gap:10,
            }}>
              {images.map(img => (
                <div key={img.id} style={{
                  border:"1px solid #e2e8f0", borderRadius:6,
                  overflow:"hidden", background:"#fff",
                }}>
                  <a href={img.url} target="_blank" rel="noreferrer">
                    <img src={img.url}
                         alt={img.original_filename}
                         style={{ width:"100%", height:140, objectFit:"cover",
                                   display:"block", background:"#f8fafc",
                                   cursor:"zoom-in" }}/>
                  </a>
                  <div style={{ padding:"6px 8px", fontSize:10 }}>
                    <div style={{ fontWeight:600, color:"#475569",
                                   overflow:"hidden", textOverflow:"ellipsis",
                                   whiteSpace:"nowrap" }}
                         title={img.original_filename}>
                      {img.original_filename}
                    </div>
                    <div style={{ display:"flex",
                                   justifyContent:"space-between",
                                   marginTop:4 }}>
                      <span style={{ fontSize:9, color:"#94a3b8" }}>
                        {img.uploaded_by_username || "—"}
                      </span>
                      <button onClick={() => onDeleteImg(img)}
                              style={{ fontSize:9, padding:"1px 6px",
                                       background:"#fee2e2", color:"#b91c1c",
                                       border:"none", borderRadius:3,
                                       cursor:"pointer", fontWeight:700 }}>
                        Delete
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


export function BreakdownSlipThresholdPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [threshold, setThreshold] = useState(10);
  const [original,  setOrig]      = useState(10);
  const [loading,   setLoading]   = useState(true);
  const [saving,    setSaving]    = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api.get("/api/breakdowns/slip-config", token);
      const v = r.slip_raise_threshold_min ?? 10;
      setThreshold(v);
      setOrig(v);
    } catch { toast?.("Failed to load slip threshold", "err"); }
    finally { setLoading(false); }
  }, [token, toast]);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (threshold < 1 || threshold > 1440) {
      toast?.("Threshold must be 1–1440 min (≤24 h)", "err"); return;
    }
    setSaving(true);
    try {
      await api.put("/api/breakdowns/slip-config", {
        slip_raise_threshold_min: threshold,
      }, token);
      toast?.("Slip threshold saved ✓");
      setOrig(threshold);
      try { window.dispatchEvent(new CustomEvent("ap-config-changed")); } catch {}
    } catch (e) { toast?.(e.message || "Save failed", "err"); }
    finally   { setSaving(false); }
  };

  const dirty = threshold !== original;
  const reset = () => setThreshold(original);

  // Human-readable formatter — converts minutes → "1 h 15 min".
  const fmtMins = (m) => {
    if (!m || m <= 0) return "—";
    const h = Math.floor(m / 60);
    const mm = m % 60;
    if (h && mm) return `${h} h ${mm} min`;
    if (h)       return `${h} h`;
    return `${mm} min`;
  };

  return (
    <div className={readOnly ? "ap-readonly" : ""}>
      <fieldset disabled={readOnly} style={{border:0, padding:0, margin:0, minWidth:0}}>
      <Card style={{ padding: 24 }}>
        <div style={{ fontSize:14, fontWeight:700, color:"#0f172a", marginBottom:6 }}>
          Breakdown Slip Raise Threshold
        </div>
        <div style={{ fontSize:12, color:"#64748b", marginBottom:22, lineHeight:1.5 }}>
          A breakdown that's attended and fixed quickly doesn't need a
          full closure slip — only the threshold matters.  Set the
          number of minutes below which a breakdown is treated as a
          <b> MINOR </b>event (Production logs basic details only, no
          formal slip).  Anything that takes longer becomes a
          <b> MAJOR </b>event and the full slip is raised
          (Production + Maintenance halves both required).
        </div>

        {loading ? (
          <Spinner/>
        ) : (
          <div style={{ display:"flex", justifyContent:"center",
                         marginBottom:18 }}>
            <div style={{ maxWidth: 360, width: "100%" }}>
              <FF label="Slip Raise Threshold (min)"
                  hint="Breakdowns < this many minutes → MINOR (Production basic log only). Breakdowns ≥ this → MAJOR (full slip raised, both halves mandatory).">
                <Input type="number" min="1" max="1440"
                       value={threshold}
                       onChange={e => setThreshold(Number(e.target.value) || 0)}
                       style={{ fontFamily:"monospace", fontWeight:800,
                                fontSize:28, textAlign:"center" }}/>
                <div style={{ fontSize:12, color:"#475569", marginTop:8,
                                textAlign:"center", fontWeight:600 }}>
                  = {fmtMins(threshold)}
                </div>
              </FF>
            </div>
          </div>
        )}

        <div style={{ display:"flex", gap:10, alignItems:"center", justifyContent:"center" }}>
          <Btn variant="primary" onClick={save} disabled={saving || !dirty}>
            {saving ? "Saving…" : dirty ? "Save Changes" : "Saved ✓"}
          </Btn>
          {dirty && <Btn onClick={reset}>Cancel</Btn>}
          {!dirty && !loading && (
            <span style={{ fontSize:11, color:"#94a3b8" }}>
              No pending changes
            </span>
          )}
        </div>

        {/* Two side-by-side panels showing the two outcomes — visual
            cheat-sheet for what each tier means */}
        <div style={{ marginTop:26, display:"grid",
                        gridTemplateColumns:"repeat(auto-fit,minmax(280px,1fr))",
                        gap:14 }}>
          {/* MINOR panel */}
          <div style={{ padding:14, background:"rgba(22,163,74,.04)",
                          border:"1.5px solid rgba(22,163,74,.25)",
                          borderRadius:10 }}>
            <div style={{ fontSize:11, fontWeight:800, color:"#15803d",
                            letterSpacing:".08em", textTransform:"uppercase",
                            marginBottom:8 }}>
              ✓ MINOR — fixed under {fmtMins(threshold)}
            </div>
            <div style={{ fontSize:12, color:"#334155", lineHeight:1.7 }}>
              <div>• Slip is <b>NOT raised</b></div>
              <div>• Production logs only basic details (line, time, brief reason)</div>
              <div>• No Maintenance closure form needed</div>
              <div>• Counts in MTBF stats but not in CAPA breach counters</div>
            </div>
          </div>

          {/* MAJOR panel */}
          <div style={{ padding:14, background:"rgba(220,38,38,.04)",
                          border:"1.5px solid rgba(220,38,38,.25)",
                          borderRadius:10 }}>
            <div style={{ fontSize:11, fontWeight:800, color:"#b91c1c",
                            letterSpacing:".08em", textTransform:"uppercase",
                            marginBottom:8 }}>
              ⚠ MAJOR — open ≥ {fmtMins(threshold)}
            </div>
            <div style={{ fontSize:12, color:"#334155", lineHeight:1.7 }}>
              <div>• Slip is <b>RAISED</b></div>
              <div>• Production half required (line/zone/machine, reported-by, received-time)</div>
              <div>• Maintenance half required (problem observed, action taken, spares, attended-by)</div>
              <div>• Counts toward CAPA breach thresholds + Pareto chart</div>
              <div>• Breakdown Mails escalation chain fires</div>
            </div>
          </div>
        </div>
      </Card>
      </fieldset>
    </div>
  );
}


// ─── SYSTEM MAP ───────────────────────────────────────────────
// Single-pane consolidated view: every Zone → Line → Machine → its PLC IP
// → its bound Camera IP, ordered top-down so an admin can verify wiring
// at a glance without bouncing across Plants/Zones/Lines/Machines/Camera
// tabs.  Read-only by design (it's a derived/joined view) — modifications
// happen in the underlying single-purpose pages.
export function SystemMapPage({ toast }) {
  const { token, theme } = useAuth();
  const [grid, setGrid] = useState([]);
  const [zones, setZones] = useState([]);
  const [lines, setLines] = useState([]);
  const [machinesByLine, setMachinesByLine] = useState({});
  const [loading, setLoading] = useState(true);
  const [pings, setPings] = useState({});
  const [lastSync, setLastSync] = useState(null);

  // `silent=true` skips the loading spinner — used for background polling
  // and focus-refetch so the table doesn't flicker every few seconds.
  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      // Four sources stitched together:
      //   1. mes_zones                        — Zone roll-ups
      //   2. mes_lines                        — Line list per zone
      //   3. mes_plc_configs (per line)       — the ACTUAL PLC IPs / ports
      //                                          / camera bindings.  This is
      //                                          where the system config lives.
      //   4. CMS camera-grid (NF2)            — camera_id → camera_ip lookup
      // We query /api/lines/{id}/machines instead of /api/machines/by-line/{id}
      // because the latter is a name-only lookup table (zones.json import) —
      // it doesn't carry plc_ip / plc_port / nf2_camera_id.
      const [g, z, l] = await Promise.all([
        api.get("/api/cms/camera-grid", token).catch(()=>[]),
        api.get("/api/zones/",          token).catch(()=>[]),
        api.get("/api/lines/",          token).catch(()=>[]),
      ]);
      setGrid(Array.isArray(g) ? g : (Array.isArray(g?.data) ? g.data : []));
      setZones(Array.isArray(z) ? z : []);
      const linesArr = Array.isArray(l) ? l : [];
      setLines(linesArr);
      const map = {};
      await Promise.allSettled(linesArr.map(async ln => {
        try {
          const r = await api.get(`/api/lines/${ln.id}/machines`, token);
          // Endpoint returns a raw array of mes_plc_configs rows.  Each row:
          //   { id, line_id, parent_plc_id, machine_name, plc_ip, plc_port,
          //     protocol, ok_bit_address, ng_bit_address, status_address,
          //     nf2_camera_id, machine_seq, ... }
          map[ln.id] = Array.isArray(r) ? r : [];
        } catch { map[ln.id] = []; }
      }));
      setMachinesByLine(map);
      setLastSync(new Date());
    } catch(e) {
      if (!silent) toast(e.message || "Load failed", "err");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [token, toast]);

  // Initial load + auto-refresh wiring.  System Map needs to react to
  // edits made in the Production Panel (Plants / Zones / Lines /
  // Machines) without forcing the user to hit Refresh.  Three triggers:
  //   1. Mount                               — fetch on first render
  //   2. Window focus                        — admin tabbed away & back
  //   3. Polling every 6 s (silent)          — picks up CRUD made in
  //                                              another tab / by another
  //                                              admin within seconds
  //   4. 'ap-config-changed' DOM event       — fired by the api/client
  //                                              wrapper on any successful
  //                                              POST / PUT / PATCH /
  //                                              DELETE so same-tab edits
  //                                              show up instantly
  useEffect(() => {
    load();
    const onFocus    = () => load(true);
    const onChange   = () => load(true);
    const onVisible  = () => { if (document.visibilityState === "visible") load(true); };
    window.addEventListener("focus", onFocus);
    window.addEventListener("ap-config-changed", onChange);
    document.addEventListener("visibilitychange", onVisible);
    const tick = setInterval(() => load(true), 6000);
    return () => {
      clearInterval(tick);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("ap-config-changed", onChange);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  // Ping unique camera + PLC IPs in one go so the admin sees what's reachable.
  useEffect(() => {
    if (!grid.length && !lines.length) return;
    const ips = new Set();
    grid.forEach(m => m.camera_ip && ips.add(m.camera_ip + "|554"));
    Object.values(machinesByLine).flat().forEach(mc => {
      if (mc.plc_ip) ips.add(mc.plc_ip + "|" + (mc.plc_port || 5002));
    });
    if (!ips.size) return;
    let alive = true;
    (async () => {
      const out = {};
      await Promise.allSettled([...ips].map(async key => {
        const [ip, port] = key.split("|");
        try {
          const r = await api.get(`/api/ping?ip=${encodeURIComponent(ip)}&port=${port}`, token);
          out[key] = r;
        } catch { out[key] = { ok:false }; }
      }));
      if (alive) setPings(out);
    })();
    return () => { alive = false; };
  }, [grid, machinesByLine, lines]); // eslint-disable-line

  // Zones to surface in the System Map — explicit allow-list per the
  // user's spec (Toyota Boshoku Bawal plant has these six functional
  // zones).  Anything else returned by /api/cms/camera-grid is ignored
  // here so the page stays uncluttered.  Matching is substring-based
  // (case-insensitive) so minor naming variations between CMS zones.json
  // and the spec — e.g. "Seat Slider" vs "Seat Slide Zone", "Thin
  // Recliner" vs "Thin Reclinor" — all resolve correctly.
  const ZONE_ALLOWLIST = [
    "seat slid",   // covers "Seat Slider" / "Seat Slide Zone" / "SEAT SLIDER"
    "sub assem",   // "Sub Assembly" / "Sub-Assembly"
    "recliner",    // "Recliner"  (also matched by "thin recliner" — see below)
    "press shop",  // "Press Shop"
    "loop pipe",   // "Loop Pipe"
    "thin recli",  // "Thin Recliner"
  ];
  const isAllowedZone = (zoneName) => {
    const n = String(zoneName || "").trim().toLowerCase();
    if (!n) return false;
    return ZONE_ALLOWLIST.some(p => n.includes(p));
  };

  // Stitch: zone → line → machine entries, driven by the CMS camera-grid
  // (NF2 zones.json), then filtered to the six allowed zones above.  PLC
  // info is overlaid from mes_plc_configs by matching (zone_name,
  // line_name, machine_name).  Camera info comes straight from the grid
  // row (blank when no binding yet).
  const tree = useMemo(() => {
    const norm = (s) => String(s || "").trim().toLowerCase();

    // Build PLC lookup keyed by (zone_name|line_name|machine_name)
    // and also (line_id|machine_name) as a fallback.
    const plcByZLM = {};   // "zone|line|machine"  → mes_plc_configs row
    const plcByLM  = {};   // "line_id|machine"    → mes_plc_configs row (stricter)
    Object.entries(machinesByLine).forEach(([lineId, mlist]) => {
      const ln = lines.find(l => String(l.id) === String(lineId));
      const lineName = norm(ln?.line_name);
      const zoneName = norm(ln?.zone_name);
      mlist.forEach(mc => {
        const mName = norm(mc.machine_name);
        if (zoneName && lineName && mName) {
          plcByZLM[`${zoneName}|${lineName}|${mName}`] = mc;
        }
        if (lineName && mName) {
          plcByLM[`${lineName}|${mName}`] = mc;
        }
      });
    });

    // Zone → Line → [machines] dictionary, populated from camera-grid.
    // Skip any zone that isn't in the explicit allow-list above.
    const zMap = {};
    grid.forEach(m => {
      const zKey = m.zone_name || `Zone ${m.zone_id ?? "?"}`;
      if (!isAllowedZone(zKey)) return;       // ← drop unwanted zones
      const lKey = m.line_name || "—";
      if (!zMap[zKey]) {
        zMap[zKey] = {
          id:        m.zone_id,
          zone_name: zKey,
          lines:     {},
        };
      }
      const z = zMap[zKey];
      if (!z.lines[lKey]) {
        z.lines[lKey] = {
          id:             m.line_id,
          line_name:      lKey,
          db_table_name:  "",
          machines:       [],
        };
      }
      const ln = z.lines[lKey];

      // Find PLC info for this (zone, line, machine) tuple.
      const k1 = `${norm(zKey)}|${norm(lKey)}|${norm(m.machine_name)}`;
      const k2 = `${norm(lKey)}|${norm(m.machine_name)}`;
      const mc = plcByZLM[k1] || plcByLM[k2] || {};

      ln.machines.push({
        // PLC details (may all be empty if MES hasn't provisioned this machine)
        machine_name:  m.machine_name,
        plc_ip:        mc.plc_ip      || "",
        plc_port:      mc.plc_port    || "",
        machine_seq:   mc.machine_seq ?? null,
        parent_plc_id: mc.parent_plc_id ?? null,
        // Camera details from CMS grid (blank when no binding)
        camera: m.camera_id ? {
          camera_id:   m.camera_id,
          camera_ip:   m.camera_ip || "",
          camera_name: m.camera_name || "",
        } : null,
      });
    });

    // Also surface mes_plc_configs rows that the CMS grid DOESN'T know
    // about — e.g. a brand-new sub-PLC admin just added that hasn't
    // been registered in the NF2 zones.json yet.  These appear at the
    // bottom of their owning line (no camera, just PLC info).
    // Same allow-list filter applies.
    Object.entries(machinesByLine).forEach(([lineId, mlist]) => {
      const ln = lines.find(l => String(l.id) === String(lineId));
      if (!ln) return;
      const zKey = ln.zone_name || `Zone ${ln.zone_id ?? "?"}`;
      if (!isAllowedZone(zKey)) return;        // ← same filter
      const lKey = ln.line_name || "—";
      if (!zMap[zKey]) zMap[zKey] = { id: ln.zone_id, zone_name: zKey, lines: {} };
      if (!zMap[zKey].lines[lKey]) {
        zMap[zKey].lines[lKey] = {
          id: ln.id, line_name: lKey, db_table_name: ln.db_table_name || "", machines: [],
        };
      }
      const lineNode = zMap[zKey].lines[lKey];
      lineNode.db_table_name = ln.db_table_name || lineNode.db_table_name;
      const have = new Set(lineNode.machines.map(x => norm(x.machine_name)));
      mlist.forEach(mc => {
        if (have.has(norm(mc.machine_name))) return;
        lineNode.machines.push({
          machine_name:  mc.machine_name,
          plc_ip:        mc.plc_ip || "",
          plc_port:      mc.plc_port || "",
          machine_seq:   mc.machine_seq ?? null,
          parent_plc_id: mc.parent_plc_id ?? null,
          camera:        null,
        });
      });
    });

    return Object.values(zMap)
      .sort((a,b) => String(a.zone_name||"").localeCompare(String(b.zone_name||"")))
      .map(z => ({
        ...z,
        lines: Object.values(z.lines)
          .sort((a,b) => String(a.line_name||"").localeCompare(String(b.line_name||"")))
          .map(l => ({
            ...l,
            machines: l.machines.sort((a,b) =>
              (a.machine_seq ?? 999) - (b.machine_seq ?? 999)
              || String(a.machine_name||"").localeCompare(String(b.machine_name||""))
            ),
          })),
      }));
  }, [zones, lines, grid, machinesByLine]);

  const pingStatus = (ip, port) => {
    if (!ip) return null;
    const p = pings[`${ip}|${port}`];
    if (!p) return <Pill label="…" color="gray" />;
    return p.ok
      ? <Pill label={`${p.ms ?? 0}ms`} color="green" />
      : <Pill label="down" color="red" />;
  };

  return (
    <div>
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:14,flexWrap:"wrap",gap:10}}>
        <div>
          <div style={{fontWeight:700,fontSize:15,color:"#0f172a"}}>System Map</div>
          <div style={{fontSize:11,color:"#64748b",marginTop:2}}>
            Read-only consolidated view: every Zone → Line → Machine, its PLC IP and bound Camera IP.
            Edits happen in the dedicated Plants / Zones / Lines / Machines pages — they reflect here automatically.
          </div>
        </div>
        <div style={{display:"flex",alignItems:"center",gap:10}}>
          <span style={{display:"inline-flex",alignItems:"center",gap:6,fontSize:11,color:"#64748b"}}>
            {/* Pulse colour follows the role theme — admin sees blue,
                others see their accent so the indicator never clashes. */}
            <span style={{width:7,height:7,borderRadius:99,background:theme.accent,
                          animation:"sm-pulse 1.6s infinite"}}/>
            Live
            {lastSync && <span style={{color:"#94a3b8",fontFamily:"monospace"}}>
              · {lastSync.toLocaleTimeString()}
            </span>}
          </span>
          <Btn onClick={() => load(false)} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</Btn>
        </div>
      </div>
      <style>{`
        @keyframes sm-pulse {
          0%   { box-shadow:0 0 0 0   ${theme.soft}; }
          70%  { box-shadow:0 0 0 6px rgba(0,0,0,0); }
          100% { box-shadow:0 0 0 0   rgba(0,0,0,0); }
        }
      `}</style>
      {loading ? <Spinner/> : tree.length === 0 ? (
        <Card><EmptyState text="No zones/lines configured" sub="Configure Plants → Zones → Lines first."/></Card>
      ) : (
        tree.map(z => (
          <Card key={z.id} style={{marginBottom:14}}>
            <div style={{padding:"12px 18px",borderBottom:"1px solid #f1f5f9",display:"flex",alignItems:"center",gap:10}}>
              <span style={{fontSize:11,fontWeight:700,letterSpacing:".15em",textTransform:"uppercase",color:"#64748b"}}>Zone</span>
              <span style={{fontSize:15,fontWeight:700,color:"#0f172a"}}>{z.zone_name || "—"}</span>
              <span style={{fontSize:11,color:"#94a3b8"}}>· {z.lines.length} line{z.lines.length===1?"":"s"}</span>
            </div>
            {z.lines.length === 0 ? (
              <div style={{padding:"14px 18px",color:"#94a3b8",fontSize:12}}>No lines</div>
            ) : z.lines.map(ln => (
              <div key={ln.id} style={{borderBottom:"1px solid #f1f5f9",padding:"10px 18px"}}>
                <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:6}}>
                  <span style={{fontSize:10,fontWeight:700,letterSpacing:".15em",textTransform:"uppercase",color:"#64748b"}}>Line</span>
                  <span style={{fontSize:13,fontWeight:700,color:"#0f172a"}}>{ln.line_name || "—"}</span>
                  <span style={{fontSize:10,color:"#94a3b8",fontFamily:"monospace"}}>{ln.db_table_name || ""}</span>
                </div>
                {ln.machines.length === 0 ? (
                  <div style={{paddingLeft:12,fontSize:11,color:"#94a3b8"}}>No machines</div>
                ) : (
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:12,marginTop:4}}>
                    <thead><tr>
                      {["#","Machine","PLC IP","PLC port","PLC ping","Camera ID","Camera IP","Cam ping"].map(h=>(
                        <th key={h} style={{padding:"6px 10px",textAlign:"left",fontSize:9,fontWeight:700,letterSpacing:".08em",textTransform:"uppercase",color:"#64748b",borderBottom:"1px solid #e2e8f0"}}>{h}</th>
                      ))}
                    </tr></thead>
                    <tbody>
                      {ln.machines.map((mc, i) => (
                        <tr key={i} style={{borderBottom:"1px solid #f8fafc"}}>
                          <td style={{padding:"7px 10px",color:"#94a3b8"}}>{mc.machine_seq ?? i+1}</td>
                          <td style={{padding:"7px 10px",fontWeight:600,color:"#0f172a"}}>{mc.machine_name || "—"}</td>
                          <td style={{padding:"7px 10px",fontFamily:"monospace",color:"#1e40af"}}>{mc.plc_ip || "—"}</td>
                          <td style={{padding:"7px 10px",fontFamily:"monospace",color:"#475569"}}>{mc.plc_port || "—"}</td>
                          <td style={{padding:"7px 10px"}}>{pingStatus(mc.plc_ip, mc.plc_port || 5002)}</td>
                          <td style={{padding:"7px 10px",fontFamily:"monospace",color:"#475569"}}>{mc.camera?.camera_id || "—"}</td>
                          <td style={{padding:"7px 10px",fontFamily:"monospace",color:"#1e40af"}}>{mc.camera?.camera_ip || "—"}</td>
                          <td style={{padding:"7px 10px"}}>{mc.camera?.camera_ip ? pingStatus(mc.camera.camera_ip, 554) : <span style={{color:"#cbd5e1"}}>—</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            ))}
          </Card>
        ))
      )}
    </div>
  );
}


// ─── ADMIN PANEL SHELL ────────────────────────────────────────
// Two-level navigation: a top-row of SECTIONS (Production / Maintenance /
// Quality / Admin) and a sub-tab strip per section. URL hash is
// "<section>/<tab>" so a refresh keeps you exactly where you were.
//
// Department users (role='department') see this same shell rendered
// inside DepartmentPanel.jsx, but with `readOnly` threaded down — they
// can READ everything but cannot Add / Edit / Delete.  Admin & plant_head
// get full write access here.
export const ADMIN_SECTIONS = [
  {
    key: "production", label: "Production", color: "#16a34a",
    tabs: [
      { key: "plants",    label: "Plants",            icon: "⬡" },
      { key: "zones",     label: "Zones",             icon: "◎" },
      { key: "lines",     label: "Production Lines",  icon: "⬡" },
      { key: "machines",  label: "Machines",          icon: "⚙" },
      { key: "processes", label: "Processes / Skill", icon: "🛠" },
      { key: "status",    label: "Status Colour",     icon: "◉" },
      { key: "hourlymail",label: "Hourly Report Mail",icon: "📧" },
      { key: "reports",   label: "Shift Reports",     icon: "📊" },
      { key: "oeealarm",  label: "OEE Drop Alarm",    icon: "⚠" },
      { key: "manpowercfg", label: "Manpower Settings", icon: "👥" },
      { key: "timercfg",    label: "Timer / Alerts",   icon: "⏲" },
      { key: "panelmap",    label: "Panel Displays",   icon: "🖥" },
      { key: "workcenter",  label: "Work Center (PEFF)", icon: "🎯" },
      { key: "peffdetails", label: "PEFF Details",       icon: "📋" },
    ],
  },
  {
    key: "maintenance", label: "Maintenance", color: "#dc2626",
    tabs: [
      // Sensor Health intentionally NOT a standalone tab here — it
      // already lives inside Poka Yoke as its 5th sub-tab, so a
      // duplicate top-level entry would double-render it.
      // Bypass Alerts also dropped — the bypass kind is already covered
      // by Mail Settings (its config) and the actual events stream is
      // visible inside Poka Yoke → Matrix; a separate empty-data tab
      // was just noise.
      { key: "pokayoke",   label: "Poka Yoke",        icon: "⚑" },
      { key: "pyconfig",   label: "PY Config",        icon: "📋" },
      { key: "pystations", label: "PY Stations",      icon: "🔗" },
      { key: "pymanuals",  label: "PY Manuals",       icon: "📷" },
      { key: "faultcfg",   label: "Fault Config",     icon: "🛠" },
      // 2026-09-14 — per operator request the Maintenance panel keeps only the
      // PY tabs + the new Fault Config. These six were removed from this tab bar
      // (their panels still exist in code, just no longer reachable here):
      //   New Requests · Mail Settings · Breakdown Mails · KPI Targets ·
      //   CAPA Settings · Slip Threshold
    ],
  },
  {
    key: "quality", label: "Quality", color: "#ca8a04",
    tabs: [
      // PY Failure escalation chain (level / delay / recipients / test
      // send) — same UI Maintenance admin uses; mirrored here so the
      // Quality Sec Head can audit / adjust the email tree without
      // hopping to the Maintenance Panel.
      { key: "pyescalation", label: "PY Escalation Mails", icon: "🚨" },
      // 2026-08-03 — welding stations: which analog card / channel each
      // robot's current shunt is wired to, where the robot sits (zone →
      // line → machine) and its acceptable current band.  The live weld
      // poller reads this master, so adding a robot here is all it takes.
      { key: "weldmaster",   label: "Weld Master",         icon: "⚡" },
    ],
  },
  {
    key: "admin", label: "Admin", color: "#1e40af",
    tabs: [
      { key: "systemmap",   label: "System Map",       icon: "🗺" },
      { key: "network",     label: "Network Builder",  icon: "🖧" },
      { key: "machinemaster", label: "Machine Master", icon: "🏭" },
      { key: "departments", label: "Departments",      icon: "🏛" },
      { key: "users",       label: "Users",            icon: "👥" },
      // 2026-09-13 — "Operators" moved OUT of the admin panel into its own
      // assignable page (/operators, OperatorMaster.jsx).  Removed here so it
      // isn't duplicated; the standalone page is authority-based via canAccess.
    ],
  },
];

// ════════════════════════════════════════════════════════════════════
//  ReportsPage  —  per-shift Excel / PDF download + email-config
// ════════════════════════════════════════════════════════════════════
//
// Backend endpoints used:
//   GET  /api/reports/shift-excel?line_id=&date=&shift=    (streams xlsx)
//   GET  /api/reports/shift-pdf?line_id=&date=&shift=      (streams pdf)
//   GET  /api/reports/email-config                         (list)
//   PUT  /api/reports/email-config                         (admin upsert)
//   POST /api/reports/email-now                            (admin manual fire)
//
// The auto-mail scheduler runs in the backend (90 s after shift end_time).
// This page is the admin's window into who gets the auto-mail and a
// manual "fire now" button for testing.
//
// Recipients are three levels deep (line / zone head / section head), each with
// its own To and Cc.  The backend merges all six when it sends.
const BLANK_REPORT_CFG = {
  line_id: "", is_active: true,
  to_addresses: "",         cc_addresses: "",
  zone_to_addresses: "",    zone_cc_addresses: "",
  section_to_addresses: "", section_cc_addresses: "",
};

export function ReportsPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [lines,    setLines]    = useState([]);
  const [configs,  setConfigs]  = useState([]);
  const [lineId,   setLineId]   = useState("");
  const [date,     setDate]     = useState(() => new Date().toISOString().slice(0, 10));
  const [shift,    setShift]    = useState("A");
  const [saving,   setSaving]   = useState(false);
  const [form,     setForm]     = useState(BLANK_REPORT_CFG);

  const load = useCallback(async () => {
    try {
      const [ls, cfgs] = await Promise.all([
        api.get("/api/lines/", token),
        api.get("/api/reports/email-config", token),
      ]);
      setLines(ls || []);
      setConfigs(cfgs || []);
      if (!lineId && ls?.length) setLineId(ls[0].id);
    } catch (e) { toast("Failed to load reports config", "err"); }
  }, [token, lineId]);

  useEffect(() => { load(); }, [load]);

  const downloadFile = async (kind) => {
    if (!lineId || !date || !shift) { toast("Pick line / date / shift", "err"); return; }
    const url = `/api/reports/shift-${kind}?line_id=${lineId}&date=${date}&shift=${encodeURIComponent(shift)}`;
    try {
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) {
        const txt = await res.text();
        toast(`Download failed: ${txt.slice(0, 100)}`, "err");
        return;
      }
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `shift_${date}_${shift}.${kind === "excel" ? "xlsx" : "pdf"}`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { toast(`Download failed: ${e.message}`, "err"); }
  };

  const emailNow = async () => {
    if (!lineId) return;
    try {
      const r = await api.post("/api/reports/email-now",
        { line_id: lineId, date, shift, kinds: ["excel", "pdf"] }, token);
      toast(`Emailed to ${(r?.to || []).join(", ")} ✓`);
    } catch (e) { toast(e.message, "err"); }
  };

  const saveCfg = async () => {
    if (!form.line_id) { toast("Pick a line first", "err"); return; }
    setSaving(true);
    try {
      await api.put("/api/reports/email-config", {
        line_id:      Number(form.line_id),
        report_kind:  "shift_end",
        to_addresses:         form.to_addresses         || "",
        cc_addresses:         form.cc_addresses         || "",
        zone_to_addresses:    form.zone_to_addresses    || "",
        zone_cc_addresses:    form.zone_cc_addresses    || "",
        section_to_addresses: form.section_to_addresses || "",
        section_cc_addresses: form.section_cc_addresses || "",
        is_active:    form.is_active,
      }, token);
      toast("Email config saved ✓");
      setForm(BLANK_REPORT_CFG);
      load();
    } catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  return (
    <div>
      {/* ── ONE-SHOT DOWNLOAD / MANUAL EMAIL ───────────────────── */}
      <Card style={{ marginBottom: 20 }}>
        <div style={{ padding: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#0f172a", marginBottom: 12 }}>
            Download or email a single shift's report
          </div>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "end" }}>
            <div>
              <label style={{ fontSize: 11, color: "#64748b" }}>Line</label>
              <select value={lineId} onChange={e => setLineId(Number(e.target.value))}
                      style={{ display: "block", padding: "6px 8px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6, minWidth: 180 }}>
                {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
              </select>
            </div>
            <div>
              <label style={{ fontSize: 11, color: "#64748b" }}>Date</label>
              <input type="date" value={date} onChange={e => setDate(e.target.value)}
                     style={{ display: "block", padding: "6px 8px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6 }} />
            </div>
            <div>
              <label style={{ fontSize: 11, color: "#64748b" }}>Shift</label>
              <select value={shift} onChange={e => setShift(e.target.value)}
                      style={{ display: "block", padding: "6px 8px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6 }}>
                <option value="A">A</option><option value="B">B</option><option value="C">C</option>
              </select>
            </div>
            <Btn onClick={() => downloadFile("excel")} variant="primary">📥 Excel</Btn>
            <Btn onClick={() => downloadFile("pdf")}                 >📄 PDF</Btn>
            {!readOnly && <Btn onClick={emailNow} variant="primary">✉ Email Now</Btn>}
          </div>
        </div>
      </Card>

      {/* ── AUTO-MAIL RECIPIENTS PER LINE ──────────────────────── */}
      <Card>
        <div style={{ padding: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#0f172a", marginBottom: 4 }}>
            Auto end-of-shift email recipients
          </div>
          <div style={{ fontSize: 11, color: "#64748b", marginBottom: 14 }}>
            Each shift's PDF + Excel auto-mails 90 s after the shift's <code>end_time</code>.
            Set per-line recipients below; leave blank to disable auto-mail for a line.
          </div>

          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ background: "#f1f5f9", textAlign: "left" }}>
                <th style={{ padding: 8 }}>Line</th>
                <th style={{ padding: 8 }}>Level</th>
                <th style={{ padding: 8 }}>To</th>
                <th style={{ padding: 8 }}>Cc</th>
                <th style={{ padding: 8 }}>Active</th>
                <th style={{ padding: 8 }}>Updated</th>
              </tr>
            </thead>
            <tbody>
              {configs.map(c => {
                const line = lines.find(l => l.id === c.line_id);
                const dash = <em style={{ color: "#94a3b8" }}>—</em>;
                return (
                  <tr key={c.id} style={{ borderTop: "1px solid #e2e8f0" }}>
                    <td style={{ padding: 8, fontWeight: 600 }}>{line?.line_name || "—"}</td>
                    {/* One row per config, three stacked levels — keeps the
                        table one-row-per-line while showing the whole chain. */}
                    <td style={{ padding: 8, fontSize:11, color:"#64748b", whiteSpace:"nowrap" }}>
                      <div style={{ padding:"2px 0" }}>Line</div>
                      <div style={{ padding:"2px 0" }}>Zone head</div>
                      <div style={{ padding:"2px 0" }}>Section head</div>
                    </td>
                    <td style={{ padding: 8, fontSize:11, fontFamily:"monospace" }}>
                      <div style={{ padding:"2px 0" }}>{c.to_addresses || dash}</div>
                      <div style={{ padding:"2px 0" }}>{c.zone_to_addresses || dash}</div>
                      <div style={{ padding:"2px 0" }}>{c.section_to_addresses || dash}</div>
                    </td>
                    <td style={{ padding: 8, fontSize:11, fontFamily:"monospace" }}>
                      <div style={{ padding:"2px 0" }}>{c.cc_addresses || dash}</div>
                      <div style={{ padding:"2px 0" }}>{c.zone_cc_addresses || dash}</div>
                      <div style={{ padding:"2px 0" }}>{c.section_cc_addresses || dash}</div>
                    </td>
                    <td style={{ padding: 8 }}>
                      <span style={{
                        display: "inline-block", padding: "2px 8px", borderRadius: 10, fontSize: 11, fontWeight: 600,
                        background: c.is_active ? "#dcfce7" : "#fee2e2",
                        color:      c.is_active ? "#166534" : "#991b1b",
                      }}>{c.is_active ? "ON" : "OFF"}</span>
                    </td>
                    <td style={{ padding: 8, color: "#64748b" }}>{c.updated_at ? new Date(c.updated_at).toLocaleString() : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {!readOnly && (
            <div style={{ marginTop: 18, paddingTop: 14, borderTop: "1px solid #e2e8f0" }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: "#475569", marginBottom: 8 }}>Add / update recipients</div>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "end" }}>
                <div>
                  <label style={{ fontSize: 10, color: "#64748b" }}>Line</label>
                  <select value={form.line_id} onChange={e => setForm({ ...form, line_id: e.target.value })}
                          style={{ display: "block", padding: "6px 8px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6, minWidth: 160 }}>
                    <option value="">— select —</option>
                    {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
                  </select>
                </div>
                <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "#475569" }}>
                  <input type="checkbox" checked={form.is_active} onChange={e => setForm({ ...form, is_active: e.target.checked })} />
                  Active
                </label>
                <Btn variant="primary" onClick={saveCfg} disabled={saving}>{saving ? "…" : "Save"}</Btn>
              </div>

              {/* 2026-08-14 — three levels, each with its own To and Cc.
                  All six are merged when the mail goes out (line → zone →
                  section, duplicates dropped), so the same person listed twice
                  is mailed once and anyone in both To and Cc stays in To. */}
              <div style={{ display:"grid", gridTemplateColumns:"110px 1fr 1fr",
                             gap:10, alignItems:"center", marginTop:12 }}>
                <div/>
                <div style={{ fontSize:10, fontWeight:800, color:"#64748b",
                               letterSpacing:".06em", textTransform:"uppercase" }}>To (comma-separated)</div>
                <div style={{ fontSize:10, fontWeight:800, color:"#64748b",
                               letterSpacing:".06em", textTransform:"uppercase" }}>Cc</div>

                {[
                  { lvl:"Line",         to:"to_addresses",         cc:"cc_addresses",
                    ph:"supervisor@tbdi.com, engineer@tbdi.com" },
                  { lvl:"Zone head",    to:"zone_to_addresses",    cc:"zone_cc_addresses",
                    ph:"zone.head@tbdi.com" },
                  { lvl:"Section head", to:"section_to_addresses", cc:"section_cc_addresses",
                    ph:"section.head@tbdi.com" },
                ].map(rw => (
                  <Fragment key={rw.lvl}>
                    <div style={{ fontSize:12, fontWeight:700, color:"#0f172a" }}>{rw.lvl}</div>
                    <input value={form[rw.to] || ""}
                           onChange={e => setForm({ ...form, [rw.to]: e.target.value })}
                           placeholder={rw.ph}
                           style={{ width:"100%", padding:"6px 8px", fontSize:13,
                                     border:"1px solid #cbd5e1", borderRadius:6,
                                     fontFamily:"monospace" }} />
                    <input value={form[rw.cc] || ""}
                           onChange={e => setForm({ ...form, [rw.cc]: e.target.value })}
                           placeholder="(optional)"
                           style={{ width:"100%", padding:"6px 8px", fontSize:13,
                                     border:"1px solid #cbd5e1", borderRadius:6,
                                     fontFamily:"monospace" }} />
                  </Fragment>
                ))}
              </div>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}


// ════════════════════════════════════════════════════════════════════
//  OEEAlarmPage  —  configure sustained-drop email alerts
// ════════════════════════════════════════════════════════════════════
export function OEEAlarmPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [lines,   setLines]   = useState([]);
  const [configs, setConfigs] = useState([]);
  const [form,    setForm]    = useState({
    line_id: "", threshold_pct: 60, sustain_minutes: 10, cooldown_minutes: 60,
    to_addresses: "", cc_addresses: "", is_active: true,
  });
  const [saving, setSaving]   = useState(false);

  const load = useCallback(async () => {
    try {
      const [ls, cfgs] = await Promise.all([
        api.get("/api/lines/", token),
        api.get("/api/oee-alarm", token),
      ]);
      setLines(ls || []);
      setConfigs(cfgs || []);
    } catch (e) { toast("Failed to load OEE alarms", "err"); }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!form.line_id) { toast("Pick a line", "err"); return; }
    if (!form.to_addresses) { toast("Add at least one recipient", "err"); return; }
    setSaving(true);
    try {
      await api.put("/api/oee-alarm", {
        line_id:          Number(form.line_id),
        threshold_pct:    Number(form.threshold_pct),
        sustain_minutes:  Number(form.sustain_minutes),
        cooldown_minutes: Number(form.cooldown_minutes),
        to_addresses:     form.to_addresses,
        cc_addresses:     form.cc_addresses,
        is_active:        form.is_active,
      }, token);
      toast("OEE alarm saved ✓");
      setForm({ line_id: "", threshold_pct: 60, sustain_minutes: 10, cooldown_minutes: 60,
                to_addresses: "", cc_addresses: "", is_active: true });
      load();
    } catch (e) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };

  const loadIntoForm = (c) => setForm({
    line_id:          String(c.line_id),
    threshold_pct:    c.threshold_pct,
    sustain_minutes:  c.sustain_minutes,
    cooldown_minutes: c.cooldown_minutes,
    to_addresses:     c.to_addresses || "",
    cc_addresses:     c.cc_addresses || "",
    is_active:        !!c.is_active,
  });

  return (
    <div>
      <Card style={{ marginBottom: 20 }}>
        <div style={{ padding: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#0f172a", marginBottom: 4 }}>
            How it works
          </div>
          <div style={{ fontSize: 11.5, color: "#475569", lineHeight: 1.7 }}>
            Background watcher samples the dashboard table every 30 s.  When a line's <code>overall_oee</code> stays
            below <b>Threshold %</b> for <b>Sustain minutes</b> continuously, one email goes out to the recipients
            below.  Within <b>Cooldown minutes</b> of a fire, no new alert is sent for that line — prevents flooding
            during a really bad shift.  The streak resets to zero as soon as OEE recovers above threshold, so the next
            dip fires fresh.
          </div>
        </div>
      </Card>

      <Card style={{ marginBottom: 20 }}>
        <div style={{ padding: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#0f172a", marginBottom: 12 }}>Active alarms</div>
          {configs.length === 0 ? <EmptyState text="No alarms configured" sub="Add one below to start watching a line" /> : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "#f1f5f9", textAlign: "left" }}>
                  <th style={{ padding: 8 }}>Line</th>
                  <th style={{ padding: 8 }}>Threshold</th>
                  <th style={{ padding: 8 }}>Sustain</th>
                  <th style={{ padding: 8 }}>Cooldown</th>
                  <th style={{ padding: 8 }}>To</th>
                  <th style={{ padding: 8 }}>Status</th>
                  <th style={{ padding: 8 }}>Last fired</th>
                  {!readOnly && <th style={{ padding: 8 }}></th>}
                </tr>
              </thead>
              <tbody>
                {configs.map(c => {
                  const line = lines.find(l => l.id === c.line_id);
                  return (
                    <tr key={c.line_id} style={{ borderTop: "1px solid #e2e8f0" }}>
                      <td style={{ padding: 8, fontWeight: 600 }}>{line?.line_name || "—"}</td>
                      <td style={{ padding: 8 }}>{Number(c.threshold_pct).toFixed(0)}%</td>
                      <td style={{ padding: 8 }}>{c.sustain_minutes} min</td>
                      <td style={{ padding: 8 }}>{c.cooldown_minutes} min</td>
                      <td style={{ padding: 8, color: "#475569", fontSize: 11 }}>{c.to_addresses || "—"}</td>
                      <td style={{ padding: 8 }}>
                        <span style={{
                          display: "inline-block", padding: "2px 8px", borderRadius: 10, fontSize: 11, fontWeight: 600,
                          background: c.is_active ? "#dcfce7" : "#fee2e2",
                          color:      c.is_active ? "#166534" : "#991b1b",
                        }}>{c.is_active ? "ON" : "OFF"}</span>
                      </td>
                      <td style={{ padding: 8, color: "#64748b", fontSize: 11 }}>
                        {c.last_fired_at ? new Date(c.last_fired_at).toLocaleString() : <em>never</em>}
                      </td>
                      {!readOnly && (
                        <td style={{ padding: 8 }}>
                          <Btn size="sm" onClick={() => loadIntoForm(c)}>Edit</Btn>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </Card>

      {!readOnly && (
        <Card>
          <div style={{ padding: 18 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "#0f172a", marginBottom: 12 }}>
              Add / update alarm
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
              <div>
                <label style={{ fontSize: 10, color: "#64748b" }}>Line</label>
                <select value={form.line_id} onChange={e => setForm({ ...form, line_id: e.target.value })}
                        style={{ width: "100%", padding: "6px 8px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6 }}>
                  <option value="">— select —</option>
                  {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
                </select>
              </div>
              <div>
                <label style={{ fontSize: 10, color: "#64748b" }}>Threshold %</label>
                <input type="number" min={0} max={100} value={form.threshold_pct}
                       onChange={e => setForm({ ...form, threshold_pct: e.target.value })}
                       style={{ width: "100%", padding: "6px 8px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6 }} />
              </div>
              <div>
                <label style={{ fontSize: 10, color: "#64748b" }}>Sustain (min)</label>
                <input type="number" min={1} value={form.sustain_minutes}
                       onChange={e => setForm({ ...form, sustain_minutes: e.target.value })}
                       style={{ width: "100%", padding: "6px 8px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6 }} />
              </div>
              <div>
                <label style={{ fontSize: 10, color: "#64748b" }}>Cooldown (min)</label>
                <input type="number" min={1} value={form.cooldown_minutes}
                       onChange={e => setForm({ ...form, cooldown_minutes: e.target.value })}
                       style={{ width: "100%", padding: "6px 8px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6 }} />
              </div>
            </div>
            <div style={{ marginTop: 12, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div>
                <label style={{ fontSize: 10, color: "#64748b" }}>To (comma-separated)</label>
                <input value={form.to_addresses} onChange={e => setForm({ ...form, to_addresses: e.target.value })}
                       placeholder="plant.head@tbdi.com"
                       style={{ width: "100%", padding: "6px 8px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6 }} />
              </div>
              <div>
                <label style={{ fontSize: 10, color: "#64748b" }}>Cc</label>
                <input value={form.cc_addresses} onChange={e => setForm({ ...form, cc_addresses: e.target.value })}
                       style={{ width: "100%", padding: "6px 8px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6 }} />
              </div>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 14 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "#475569" }}>
                <input type="checkbox" checked={form.is_active} onChange={e => setForm({ ...form, is_active: e.target.checked })} />
                Active
              </label>
              <Btn variant="primary" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save Alarm"}</Btn>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}


// ════════════════════════════════════════════════════════════════════
//  OperatorsPage  —  badge master + per-shift productivity
// ════════════════════════════════════════════════════════════════════
export function OperatorsPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [operators, setOperators] = useState([]);
  const [lines,     setLines]     = useState([]);
  const [modal,     setModal]     = useState(false);
  const [form,      setForm]      = useState({ badge_code: "", full_name: "", employee_id: "", department: "", skill_level: 1, is_active: true, zone_id: "" });
  // 2026-09-19 — Edit button + Zone column.  editId = the operator being
  // edited (null = adding).  The add route upserts on the badge, so a changed
  // badge would have created a second operator; edits go to PUT /{id}.
  const [editId,    setEditId]    = useState(null);
  const [zones,     setZones]     = useState([]);

  // Per-shift summary state
  const [sumLine,  setSumLine]  = useState("");
  const [sumDate,  setSumDate]  = useState(() => new Date().toISOString().slice(0, 10));
  const [sumShift, setSumShift] = useState("A");
  const [summary,  setSummary]  = useState([]);
  const [loadingSum, setLoadingSum] = useState(false);
  const [q, setQ] = useState("");   // 2026-09-13 — search existing operators

  const load = useCallback(async () => {
    try {
      const [ops, ls, zs] = await Promise.all([
        api.get("/api/operators", token),
        api.get("/api/lines/", token),
        api.get("/api/zones/", token).catch(() => []),
      ]);
      setOperators(ops || []);
      setLines(ls || []);
      setZones(Array.isArray(zs) ? zs : []);
      if (!sumLine && ls?.length) setSumLine(ls[0].id);
    } catch (e) { toast("Failed to load operators", "err"); }
  }, [token, sumLine]);

  useEffect(() => { load(); }, [load]);

  const openAdd = () => { setEditId(null); setForm({ badge_code: "", full_name: "", employee_id: "", department: "", skill_level: 1, is_active: true, zone_id: "" }); setModal(true); };
  const openEditOp = (op) => {
    setEditId(op.id);
    setForm({ badge_code: op.badge_code || "", full_name: op.full_name || "",
               employee_id: op.employee_id || "", department: op.department || "",
               skill_level: op.skill_level || 1, is_active: !!op.is_active,
               zone_id: op.zone_id ?? "" });
    setModal(true);
  };

  const save = async () => {
    if (!form.badge_code || !form.full_name) { toast("Badge code and name required", "err"); return; }
    const body = { ...form, zone_id: form.zone_id === "" ? null : Number(form.zone_id) };
    try {
      if (editId) await api.put(`/api/operators/${editId}`, body, token);
      else        await api.post("/api/operators", body, token);
      toast(editId ? "Operator updated ✓" : "Operator saved ✓");
      setModal(false);
      setEditId(null);
      setForm({ badge_code: "", full_name: "", employee_id: "", department: "", skill_level: 1, is_active: true, zone_id: "" });
      load();
    } catch (e) { toast(e.message, "err"); }
  };

  const remove = async (op) => {
    if (!confirm(`Delete operator "${op.full_name}"?`)) return;
    try { await api.delete(`/api/operators/${op.id}`, token); toast("Deleted"); load(); }
    catch (e) { toast(e.message, "err"); }
  };

  const loadSummary = async () => {
    if (!sumLine || !sumDate || !sumShift) return;
    setLoadingSum(true);
    try {
      const r = await api.get(`/api/operators/shift-summary?line_id=${sumLine}&date=${sumDate}&shift=${encodeURIComponent(sumShift)}`, token);
      setSummary(r || []);
    } catch (e) { toast(e.message, "err"); setSummary([]); }
    finally { setLoadingSum(false); }
  };

  const _q = q.trim().toLowerCase();
  const filtered = _q
    ? operators.filter(op =>
        [op.badge_code, op.full_name, op.employee_id, op.zone_name, op.department]
          .some(v => String(v || "").toLowerCase().includes(_q)))
    : operators;

  return (
    <div>
      {/* ── Master CRUD ─────────────────────────────────────────── */}
      <Card style={{ marginBottom: 20 }}>
        <div style={{ padding: 18 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, gap: 12, flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 700, color: "#0f172a" }}>Operators</div>
              <div style={{ fontSize: 11, color: "#64748b" }}>
                Badge → name mapping.  Floor PC scans the badge → frontend POSTs <code>/api/operators/login</code>.
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <div style={{ position: "relative" }}>
                <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "#94a3b8", fontSize: 13, pointerEvents: "none" }}>🔍</span>
                <input value={q} onChange={e => setQ(e.target.value)}
                       placeholder="Search badge / name / emp ID / zone / dept…"
                       style={{ padding: "8px 30px 8px 30px", border: "1px solid #cbd5e1", borderRadius: 8, fontSize: 13, minWidth: 260, background: "#fff", color: "#0f172a" }} />
                {q && <button onClick={() => setQ("")} title="Clear"
                              style={{ position: "absolute", right: 6, top: "50%", transform: "translateY(-50%)", border: "none", background: "none", color: "#94a3b8", cursor: "pointer", fontSize: 15, lineHeight: 1 }}>×</button>}
              </div>
              <span style={{ fontSize: 11.5, color: "#64748b", whiteSpace: "nowrap" }}>
                {_q ? `${filtered.length} / ${operators.length}` : `${operators.length}`} operator{operators.length === 1 ? "" : "s"}
              </span>
              {!readOnly && <Btn variant="primary" onClick={openAdd}>+ Add Operator</Btn>}
            </div>
          </div>

          {operators.length === 0 ? <EmptyState text="No operators yet" sub="Add badges before scanning on the floor" />
           : filtered.length === 0 ? <EmptyState text="No operator matches your search" sub={`Nothing for “${q}” — check spelling or clear the search`} /> : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "#f1f5f9", textAlign: "left" }}>
                  <th style={{ padding: 8 }}>Badge</th>
                  <th style={{ padding: 8 }}>Name</th>
                  <th style={{ padding: 8 }}>Employee ID</th>
                  <th style={{ padding: 8 }}>Zone</th>
                  <th style={{ padding: 8 }}>Department</th>
                  <th style={{ padding: 8 }}>Skill</th>
                  <th style={{ padding: 8 }}>Active</th>
                  {!readOnly && <th style={{ padding: 8 }}></th>}
                </tr>
              </thead>
              <tbody>
                {filtered.map(op => {
                  const skillColor = op.skill_level >= 4 ? "#16a34a" : op.skill_level >= 3 ? "#3b82f6" : op.skill_level >= 2 ? "#d97706" : "#94a3b8";
                  return (
                  <tr key={op.id} style={{ borderTop: "1px solid #e2e8f0" }}>
                    <td style={{ padding: 8, fontFamily: "monospace" }}>{op.badge_code}</td>
                    <td style={{ padding: 8, fontWeight: 600 }}>{op.full_name}</td>
                    <td style={{ padding: 8 }}>{op.employee_id || "—"}</td>
                    <td style={{ padding: 8, color: op.zone_name ? undefined : "#94a3b8" }}>{op.zone_name || "—"}</td>
                    <td style={{ padding: 8 }}>{op.department || "—"}</td>
                    <td style={{ padding: 8 }}>
                      <span style={{ display: "inline-block", padding: "2px 8px", borderRadius: 10, fontSize: 11, fontWeight: 700,
                                     background: `${skillColor}22`, color: skillColor }}>
                        L{op.skill_level || 1}
                      </span>
                    </td>
                    <td style={{ padding: 8 }}>
                      <span style={{
                        display: "inline-block", padding: "2px 8px", borderRadius: 10, fontSize: 11, fontWeight: 600,
                        background: op.is_active ? "#dcfce7" : "#fee2e2",
                        color:      op.is_active ? "#166534" : "#991b1b",
                      }}>{op.is_active ? "ON" : "OFF"}</span>
                    </td>
                    {!readOnly && (
                      <td style={{ padding: 8, whiteSpace: "nowrap" }}>
                        <span style={{ display: "inline-flex", gap: 6 }}>
                          <Btn size="sm" onClick={() => openEditOp(op)}>Edit</Btn>
                          <Btn size="sm" variant="danger" onClick={() => remove(op)}>Delete</Btn>
                        </span>
                      </td>
                    )}
                  </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </Card>

      {/* ── Per-shift productivity summary ──────────────────────── */}
      <Card>
        <div style={{ padding: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#0f172a", marginBottom: 4 }}>
            Per-operator shift summary
          </div>
          <div style={{ fontSize: 11, color: "#64748b", marginBottom: 12 }}>
            OK / NG cycles in each operator's session window, joined to the line's ct_log on timestamp.
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "end", marginBottom: 14 }}>
            <div>
              <label style={{ fontSize: 10, color: "#64748b" }}>Line</label>
              <select value={sumLine} onChange={e => setSumLine(Number(e.target.value))}
                      style={{ display: "block", padding: "6px 8px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6, minWidth: 160 }}>
                {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
              </select>
            </div>
            <div>
              <label style={{ fontSize: 10, color: "#64748b" }}>Date</label>
              <input type="date" value={sumDate} onChange={e => setSumDate(e.target.value)}
                     style={{ display: "block", padding: "6px 8px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6 }} />
            </div>
            <div>
              <label style={{ fontSize: 10, color: "#64748b" }}>Shift</label>
              <select value={sumShift} onChange={e => setSumShift(e.target.value)}
                      style={{ display: "block", padding: "6px 8px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6 }}>
                <option value="A">A</option><option value="B">B</option><option value="C">C</option>
              </select>
            </div>
            <Btn variant="primary" onClick={loadSummary}>{loadingSum ? "…" : "Load"}</Btn>
          </div>

          {summary.length === 0 ? <EmptyState text="No data" sub="Load a shift after operators have logged in" /> : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "#f1f5f9", textAlign: "left" }}>
                  <th style={{ padding: 8 }}>Operator</th>
                  <th style={{ padding: 8 }}>Started</th>
                  <th style={{ padding: 8 }}>Ended</th>
                  <th style={{ padding: 8 }}>Cycles</th>
                  <th style={{ padding: 8, color: "#166534" }}>OK</th>
                  <th style={{ padding: 8, color: "#991b1b" }}>NG</th>
                  <th style={{ padding: 8 }}>Avg CT (s)</th>
                </tr>
              </thead>
              <tbody>
                {summary.map(s => (
                  <tr key={s.session_id} style={{ borderTop: "1px solid #e2e8f0" }}>
                    <td style={{ padding: 8, fontWeight: 600 }}>
                      {s.full_name}
                      {s.employee_id && <span style={{ color: "#64748b", marginLeft: 6 }}>· {s.employee_id}</span>}
                    </td>
                    <td style={{ padding: 8 }}>{s.started_at ? new Date(s.started_at).toLocaleTimeString() : "—"}</td>
                    <td style={{ padding: 8 }}>{s.ended_at ? new Date(s.ended_at).toLocaleTimeString() : <em style={{color:"#06A77D"}}>active</em>}</td>
                    <td style={{ padding: 8 }}>{s.cycles}</td>
                    <td style={{ padding: 8, color: "#166534", fontWeight: 600 }}>{s.oks}</td>
                    <td style={{ padding: 8, color: "#991b1b", fontWeight: 600 }}>{s.ngs}</td>
                    <td style={{ padding: 8 }}>{Number(s.avg_ct || 0).toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Card>

      <Modal open={modal} onClose={() => { setModal(false); setEditId(null); }}
             title={editId ? "Edit Operator" : "Add Operator"}>
        <div style={{ display: "grid", gap: 12 }}>
          <div>
            <label style={{ fontSize: 11, color: "#64748b" }}>Badge code (scan once here)</label>
            <input autoFocus value={form.badge_code} onChange={e => setForm({ ...form, badge_code: e.target.value })}
                   placeholder="0012345 or RFID UID"
                   style={{ width: "100%", padding: "8px 10px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6, fontFamily: "monospace" }} />
          </div>
          <div>
            <label style={{ fontSize: 11, color: "#64748b" }}>Full name</label>
            <input value={form.full_name} onChange={e => setForm({ ...form, full_name: e.target.value })}
                   placeholder="Ramesh Kumar"
                   style={{ width: "100%", padding: "8px 10px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6 }} />
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div>
              <label style={{ fontSize: 11, color: "#64748b" }}>Employee ID</label>
              <input value={form.employee_id} onChange={e => setForm({ ...form, employee_id: e.target.value })}
                     style={{ width: "100%", padding: "8px 10px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6 }} />
            </div>
            <div>
              <label style={{ fontSize: 11, color: "#64748b" }}>Department</label>
              <input value={form.department} onChange={e => setForm({ ...form, department: e.target.value })}
                     placeholder="Production / Quality"
                     style={{ width: "100%", padding: "8px 10px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6 }} />
            </div>
          </div>
          <div>
            <label style={{ fontSize: 11, color: "#64748b" }}>Zone</label>
            <select value={form.zone_id} onChange={e => setForm({ ...form, zone_id: e.target.value })}
                    style={{ width: "100%", padding: "8px 10px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6 }}>
              <option value="">— not set —</option>
              {zones.map(z => <option key={z.id} value={z.id}>{z.zone_name}</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 11, color: "#64748b" }}>Skill Level (1=Trainee … 5=Expert)</label>
            <select value={form.skill_level}
                    onChange={e => setForm({ ...form, skill_level: Number(e.target.value) })}
                    style={{ width: "100%", padding: "8px 10px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6 }}>
              <option value={1}>L1 — Trainee</option>
              <option value={2}>L2 — Basic</option>
              <option value={3}>L3 — Skilled</option>
              <option value={4}>L4 — Multi-skilled</option>
              <option value={5}>L5 — Expert / Trainer</option>
            </select>
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "#475569" }}>
            <input type="checkbox" checked={form.is_active} onChange={e => setForm({ ...form, is_active: e.target.checked })} />
            Active
          </label>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 6 }}>
            <Btn onClick={() => { setModal(false); setEditId(null); }}>Cancel</Btn>
            <Btn variant="primary" onClick={save}>{editId ? "Update" : "Save"}</Btn>
          </div>
        </div>
      </Modal>
    </div>
  );
}


// ════════════════════════════════════════════════════════════════════
//  ProcessesPage  —  derived from Machine Master
// ════════════════════════════════════════════════════════════════════
//
// Each row is automatically created from mes_machines for the line.
// The backend GET /api/manpower/processes?line_id= seeds the table on
// every call, so renaming a machine in the Machine Master propagates
// here on the next refresh; adding a new machine inserts a fresh row
// with default L3 / 1 slot / 1 machine-per-op.
//
// Section Incharge only edits, per machine:
//   • Required skill level (L1-L5)
//   • Manpower count (slots)
//   • Machines per operator
//   • Display order
//   • Active flag
//
// process_name is owned by the Machine Master — NOT editable here.
export function ProcessesPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [lines,   setLines]   = useState([]);
  const [lineId,  setLineId]  = useState("");
  const [rows,    setRows]    = useState([]);
  const [loading, setLoading] = useState(false);
  // Per-row local edits before "Save" is hit
  const [edits, setEdits] = useState({});   // {process_id: {field: value}}

  const loadLines = useCallback(async () => {
    try {
      const ls = await api.get("/api/lines/", token);
      setLines(ls || []);
      if (!lineId && ls?.length) setLineId(ls[0].id);
    } catch (e) { toast("Failed to load lines", "err"); }
  }, [token, lineId]);

  const loadRows = useCallback(async () => {
    if (!lineId) return;
    setLoading(true);
    try {
      const r = await api.get(`/api/manpower/processes?line_id=${lineId}`, token);
      setRows(r || []);
      setEdits({});
    } catch (e) { toast(`Failed to load processes: ${e.message}`, "err"); }
    finally { setLoading(false); }
  }, [token, lineId]);

  useEffect(() => { loadLines(); }, [loadLines]);
  useEffect(() => { loadRows();  }, [loadRows]);

  const setField = (id, field, value) => {
    setEdits(prev => ({ ...prev, [id]: { ...(prev[id] || {}), [field]: value } }));
  };

  const effective = (r, field) => {
    const e = edits[r.id];
    return e && field in e ? e[field] : r[field];
  };

  const isDirty = (r) => !!edits[r.id] && Object.keys(edits[r.id]).length > 0;
  const dirtyCount = Object.keys(edits).length;

  const saveRow = async (r) => {
    try {
      await api.put(`/api/manpower/processes/${r.id}`, {
        required_skill_level:    Number(effective(r, "required_skill_level")),
        required_manpower_count: Number(effective(r, "required_manpower_count")),
        machines_covered:        Number(effective(r, "machines_covered")),
        display_order:           Number(effective(r, "display_order")),
        is_active:               !!effective(r, "is_active"),
      }, token);
      toast(`Saved · ${r.process_name}`);
      setEdits(prev => { const { [r.id]: _, ...rest } = prev; return rest; });
      loadRows();
    } catch (e) { toast(e.message, "err"); }
  };

  const saveAll = async () => {
    const dirtyIds = Object.keys(edits);
    if (dirtyIds.length === 0) return;
    let ok = 0, fail = 0;
    for (const idStr of dirtyIds) {
      const r = rows.find(x => x.id === Number(idStr));
      if (!r) continue;
      try {
        await api.put(`/api/manpower/processes/${r.id}`, {
          required_skill_level:    Number(effective(r, "required_skill_level")),
          required_manpower_count: Number(effective(r, "required_manpower_count")),
          machines_covered:        Number(effective(r, "machines_covered")),
          display_order:           Number(effective(r, "display_order")),
          is_active:               !!effective(r, "is_active"),
        }, token);
        ok += 1;
      } catch { fail += 1; }
    }
    toast(`Saved ${ok}${fail ? ` · ${fail} failed` : ""} ✓`, fail ? "err" : "ok");
    loadRows();
  };

  const skillColor = (l) => l >= 4 ? "#16a34a" : l >= 3 ? "#3b82f6" : l >= 2 ? "#d97706" : "#94a3b8";

  return (
    <div>
      <Card>
        <div style={{ padding: 18 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, flexWrap: "wrap", gap: 10 }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 700, color: "#0f172a" }}>Process / Skill — Machine Master Linked</div>
              <div style={{ fontSize: 11, color: "#64748b" }}>
                Rows are auto-derived from the Machine Master.  Set required skill, manpower count, and machines/operator per machine.
                Renaming a machine in the Machine Master will reflect here on next refresh.
              </div>
            </div>
            <div style={{ display: "flex", gap: 10, alignItems: "end" }}>
              <div>
                <label style={{ fontSize: 10, color: "#64748b" }}>Line</label>
                <select value={lineId} onChange={e => setLineId(Number(e.target.value))}
                        style={{ display: "block", padding: "6px 8px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6, minWidth: 180 }}>
                  {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
                </select>
              </div>
              <Btn onClick={loadRows}>↻ {loading ? "…" : "Reload from Master"}</Btn>
              {!readOnly && dirtyCount > 0 && (
                <Btn variant="primary" onClick={saveAll}>💾 Save All ({dirtyCount})</Btn>
              )}
            </div>
          </div>

          {rows.length === 0 ? (
            <EmptyState
              text={loading ? "Loading…" : "No machines on this line"}
              sub="Add machines via Admin → Production → Machines.  They will appear here automatically." />
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "#f1f5f9", textAlign: "left" }}>
                  <th style={{ padding: 8, width: 60 }}>M#</th>
                  <th style={{ padding: 8 }}>Machine</th>
                  <th style={{ padding: 8, width: 180 }}>Req. Skill</th>
                  <th style={{ padding: 8, width: 110 }}>Slots</th>
                  <th style={{ padding: 8, width: 130 }}>Machines / Op</th>
                  <th style={{ padding: 8, width: 100 }}>Order</th>
                  <th style={{ padding: 8, width: 80 }}>Active</th>
                  {!readOnly && <th style={{ padding: 8, width: 90 }}></th>}
                </tr>
              </thead>
              <tbody>
                {rows.map(r => {
                  const skill = Number(effective(r, "required_skill_level"));
                  const c = skillColor(skill);
                  const dirty = isDirty(r);
                  return (
                    <tr key={r.id} style={{
                      borderTop: "1px solid #e2e8f0",
                      background: dirty ? "#fef9c3" : (effective(r, "is_active") ? "transparent" : "#fafafa"),
                    }}>
                      <td style={{ padding: 8, fontFamily: "monospace", color: "#475569" }}>
                        {r.machine_no != null ? `M-${r.machine_no}` : "—"}
                      </td>
                      <td style={{ padding: 8, fontWeight: 600 }}>
                        {r.process_name}
                        {r.machine_id && (
                          <span style={{ display: "block", fontSize: 10, color: "#64748b", fontWeight: 400 }}>
                            from machine master · id #{r.machine_id}
                          </span>
                        )}
                      </td>
                      <td style={{ padding: 6 }}>
                        <select value={skill} disabled={readOnly}
                                onChange={e => setField(r.id, "required_skill_level", Number(e.target.value))}
                                style={{
                                  width: "100%", padding: "5px 6px", fontSize: 12,
                                  border: `1.5px solid ${c}55`, borderRadius: 6,
                                  background: `${c}11`, color: c, fontWeight: 700,
                                }}>
                          <option value={1}>L1 — Trainee</option>
                          <option value={2}>L2 — Basic</option>
                          <option value={3}>L3 — Skilled</option>
                          <option value={4}>L4 — Multi-skilled</option>
                          <option value={5}>L5 — Expert</option>
                        </select>
                      </td>
                      <td style={{ padding: 6 }}>
                        <input type="number" min={1} disabled={readOnly}
                               value={effective(r, "required_manpower_count")}
                               onChange={e => setField(r.id, "required_manpower_count", Math.max(1, Number(e.target.value)))}
                               style={cellInputStyle} />
                      </td>
                      <td style={{ padding: 6 }}>
                        <input type="number" min={1} disabled={readOnly}
                               value={effective(r, "machines_covered")}
                               onChange={e => setField(r.id, "machines_covered", Math.max(1, Number(e.target.value)))}
                               style={cellInputStyle} />
                      </td>
                      <td style={{ padding: 6 }}>
                        <input type="number" disabled={readOnly}
                               value={effective(r, "display_order")}
                               onChange={e => setField(r.id, "display_order", Number(e.target.value))}
                               style={cellInputStyle} />
                      </td>
                      <td style={{ padding: 8, textAlign: "center" }}>
                        <input type="checkbox" disabled={readOnly}
                               checked={!!effective(r, "is_active")}
                               onChange={e => setField(r.id, "is_active", e.target.checked)} />
                      </td>
                      {!readOnly && (
                        <td style={{ padding: 6 }}>
                          {dirty && <Btn size="sm" variant="primary" onClick={() => saveRow(r)}>Save</Btn>}
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </Card>
    </div>
  );
}

const cellInputStyle = {
  width: "100%", padding: "5px 6px", fontSize: 12,
  border: "1px solid #cbd5e1", borderRadius: 6, background: "#fff",
};


// ════════════════════════════════════════════════════════════════════
//  ManpowerConfigPage  —  per-line timings + recipient lists
// ════════════════════════════════════════════════════════════════════
//
// Backend endpoints used:
//   GET /api/manpower/config           (list all)
//   PUT /api/manpower/config           (upsert by line_id)
//
// Configures the per-line "deadline to allocate" minute window after
// shift start, the acknowledgement timeout that triggers escalation,
// and the recipient lists for Quality, Section Incharge, and the
// escalation tier.
export function ManpowerConfigPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [lines,   setLines]   = useState([]);
  const [configs, setConfigs] = useState([]);
  const [lineId,  setLineId]  = useState("");
  const [form,    setForm]    = useState({
    line_id: "",
    allocation_deadline_minutes: 60,
    ack_timeout_minutes: 30,
    quality_to_addresses: "",
    section_incharge_to_addresses: "",
    escalation_to_addresses: "",
    is_active: true,
  });

  const load = useCallback(async () => {
    try {
      const [ls, cfgs] = await Promise.all([
        api.get("/api/lines/", token),
        api.get("/api/manpower/config", token),
      ]);
      setLines(ls || []);
      setConfigs(cfgs || []);
      if (!lineId && ls?.length) setLineId(ls[0].id);
    } catch (e) { toast("Failed to load manpower config", "err"); }
  }, [token, lineId]);

  useEffect(() => { load(); }, [load]);

  // Hydrate form when the active line changes
  useEffect(() => {
    if (!lineId) return;
    const c = configs.find(c => c.line_id === Number(lineId));
    setForm({
      line_id: Number(lineId),
      allocation_deadline_minutes: c?.allocation_deadline_minutes ?? 60,
      ack_timeout_minutes:         c?.ack_timeout_minutes ?? 30,
      quality_to_addresses:        c?.quality_to_addresses ?? "",
      section_incharge_to_addresses: c?.section_incharge_to_addresses ?? "",
      escalation_to_addresses:     c?.escalation_to_addresses ?? "",
      is_active:                   c?.is_active ?? true,
    });
  }, [lineId, configs]);

  const save = async () => {
    if (!form.line_id) { toast("Pick a line", "err"); return; }
    try {
      await api.put("/api/manpower/config", form, token);
      toast("Config saved ✓");
      load();
    } catch (e) { toast(e.message, "err"); }
  };

  return (
    <div>
      <Card>
        <div style={{ padding: 18 }}>
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "#0f172a" }}>Manpower Allocation · Settings</div>
            <div style={{ fontSize: 11, color: "#64748b" }}>
              Per-line: how long the supervisor has to allocate after shift start, how long Quality + Section Incharge
              have to acknowledge before escalation, and the email recipients for each tier.
            </div>
          </div>

          <div style={{ display: "flex", gap: 10, alignItems: "end", marginBottom: 16, flexWrap: "wrap" }}>
            <div>
              <label style={{ fontSize: 10, color: "#64748b" }}>Line</label>
              <select value={lineId} onChange={e => setLineId(Number(e.target.value))}
                      style={{ display: "block", padding: "6px 8px", fontSize: 13, border: "1px solid #cbd5e1", borderRadius: 6, minWidth: 200 }}>
                {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
              </select>
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
            <FF label="Allocation Deadline (minutes from shift start)" hint="After this many minutes, unfilled process slots fire UNALLOCATED alerts.">
              <Input type="number" min={1} max={480} value={form.allocation_deadline_minutes}
                     onChange={e => setForm({ ...form, allocation_deadline_minutes: Math.max(1, Number(e.target.value)) })}
                     disabled={readOnly} />
            </FF>
            <FF label="Ack Timeout (minutes)" hint="If neither Quality nor Section Incharge acks within this window, escalation email fires.">
              <Input type="number" min={1} max={240} value={form.ack_timeout_minutes}
                     onChange={e => setForm({ ...form, ack_timeout_minutes: Math.max(1, Number(e.target.value)) })}
                     disabled={readOnly} />
            </FF>
          </div>

          <div style={{ display: "grid", gap: 14, marginTop: 14 }}>
            <FF label="Quality — TO addresses" hint="Comma-separated. Receives popup on dashboard + email.">
              <Input value={form.quality_to_addresses}
                     onChange={e => setForm({ ...form, quality_to_addresses: e.target.value })}
                     placeholder="qa1@plant.com, qa2@plant.com" disabled={readOnly} />
            </FF>
            <FF label="Section Incharge — TO addresses" hint="Comma-separated. Receives popup + email.">
              <Input value={form.section_incharge_to_addresses}
                     onChange={e => setForm({ ...form, section_incharge_to_addresses: e.target.value })}
                     placeholder="incharge@plant.com" disabled={readOnly} />
            </FF>
            <FF label="Escalation — TO addresses" hint="Comma-separated. Fired if no acknowledgement within ack-timeout.">
              <Input value={form.escalation_to_addresses}
                     onChange={e => setForm({ ...form, escalation_to_addresses: e.target.value })}
                     placeholder="plant.head@plant.com, hr@plant.com" disabled={readOnly} />
            </FF>
          </div>

          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "#475569", marginTop: 14 }}>
            <input type="checkbox" checked={form.is_active}
                   onChange={e => setForm({ ...form, is_active: e.target.checked })} disabled={readOnly} />
            Active (watcher will run for this line)
          </label>

          {!readOnly && (
            <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 16 }}>
              <Btn variant="primary" onClick={save}>Save Config</Btn>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}


// Render a tab's body.  Centralised so AdminPanel and DepartmentPanel
// stay perfectly in sync — DepartmentPanel re-uses this same dispatch.
export function renderAdminTab(sectionKey, tabKey, props) {
  const t = props || {};
  switch (`${sectionKey}/${tabKey}`) {
    // Production
    case "production/plants":     return <PlantsPage   {...t} />;
    case "production/zones":      return <ZonesPage    {...t} />;
    case "production/lines":      return <LinesPage    {...t} />;
    case "production/machines":   return <MachinesPage {...t} />;
    case "maintenance/pystations": return <PYStationsPage {...t} />;
    case "production/status":     return <StatusPage   {...t} />;
    case "production/hourlymail": return <MailConfigPage {...t} kindFilter={["hourly"]} />;
    case "production/reports":    return <ReportsPage    {...t} />;
    case "production/oeealarm":   return <OEEAlarmPage   {...t} />;
    case "production/processes":  return <ProcessesPage  {...t} />;
    case "production/manpowercfg": return <ManpowerConfigPage {...t} />;
    case "production/workcenter": return (
      <div style={{ height: "calc(100vh - 150px)", minHeight: 480, border: "1px solid #e2e8f0", borderRadius: 10, overflow: "hidden", background: "#fff" }}>
        <iframe src="/peff-wc-config.html" title="Work Center Alignment"
                style={{ width: "100%", height: "100%", border: "none" }} />
      </div>
    );
    case "production/peffdetails": return <PeffDetailsPage {...t} />;
    case "production/timercfg":    return <TimerConfigPage {...t} />;
    case "production/panelmap":    return <PanelMapPage {...t} />;
    // Maintenance — Sensor Health is reachable via Poka Yoke → Sensor
    // Health sub-tab, intentionally NOT a top-level entry here.
    case "maintenance/pokayoke":     return <PokaYokePage   {...t} />;
    case "maintenance/pyconfig":     return <PyConfigEditor {...t} />;
    case "maintenance/pymanuals":    return <PyManualsPage  {...t} />;
    case "maintenance/faultcfg":     return <FaultConfigPanel {...t} />;
    case "maintenance/newrequests":  return <NewRequestsPanel {...t} />;
    case "maintenance/pymail":       return <MailConfigPage {...t} kindFilter={["bypass","health"]} />;
    case "maintenance/bdmail":       return <BreakdownMailsPage {...t} />;
    case "maintenance/kpitarget":    return <KpiTargetsPage  {...t} />;
    case "maintenance/capacfg":      return <CapaSettingsPage {...t} />;
    case "maintenance/slipth":       return <BreakdownSlipThresholdPage {...t} />;
    // Quality
    case "quality/pyescalation":     return <BreakdownMailsPage {...t} />;
    case "quality/weldmaster":       return <WeldMaster {...t} />;
    // Admin
    case "admin/systemmap":   return <SystemMapPage   {...t} />;
    case "admin/network":     return <NetworkPanel mode={t.readOnly ? "view" : "edit"} />;
    case "admin/machinemaster": return <MachineMaster />;
    case "admin/departments": return <DepartmentsPage {...t} />;
    case "admin/users":       return <UsersPage       {...t} />;
    case "admin/operators":   return <OperatorsPage   {...t} />;
    default: return null;
  }
}

function _QualityPlaceholder() {
  return (
    <Card>
      <div style={{padding:"40px 30px",textAlign:"center"}}>
        <div style={{fontSize:48,marginBottom:14}}>🛠</div>
        <div style={{fontSize:16,fontWeight:700,color:"#0f172a",marginBottom:6}}>
          Quality Panel — coming soon
        </div>
        <div style={{fontSize:12,color:"#64748b",maxWidth:480,margin:"0 auto"}}>
          The Quality department's interaction surface (CTQ / NCR / dock-audit / 5S etc.)
          will be wired up once the workflow is finalised.
        </div>
      </div>
    </Card>
  );
}


// Legacy ADMIN_TABS export (kept for any external import) — derived from
// new sections so it stays in sync.  Order matches the original flat list.
const ADMIN_TABS = [
  { key: "plants",      label: "Plants",           icon: "⬡" },
  { key: "zones",       label: "Zones",            icon: "◎" },
  { key: "lines",       label: "Production Lines", icon: "⬡" },
  { key: "machines",    label: "Machines",         icon: "⚙" },
  { key: "pokayoke",    label: "Poka Yoke",        icon: "⚑" },
  { key: "status",      label: "Status Schema",    icon: "◉" },
  { key: "departments", label: "Departments",      icon: "🏛" },
  { key: "users",       label: "Users",            icon: "👥" },
  { key: "cameras",     label: "Camera List",      icon: "📷" },
  { key: "mail",        label: "Mail Config",      icon: "📧" },
  { key: "bdmail",      label: "Breakdown Mails",  icon: "🚨" },
  { key: "kpitarget",   label: "KPI Targets",      icon: "🎯" },
];

// Inline shared CSS used by both AdminPanel (full-write) and DepartmentPanel
// (read-only mirror).  Kept here so the two shells render identically.
export const ADMIN_PANEL_CSS = `
  @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@700;800&display=swap');
  .admin-root { min-height:100vh; background:#f8fafc; font-family:'Barlow',sans-serif; padding-bottom:60px; }
  .admin-topbar { background:#fff; border-bottom:1px solid #e2e8f0; padding:0 40px 0 88px; height:60px; display:flex; align-items:center; justify-content:space-between; position:sticky; top:0; z-index:100; box-shadow:0 1px 3px rgba(0,0,0,.06); }
  .admin-topbar::after { content:''; position:absolute; bottom:0; left:0; right:0; height:2px; }
  .admin-logo { font-family:'Barlow Condensed',sans-serif; font-size:22px; font-weight:800; color:#0f172a; }

  /* Section bar — Production / Maintenance / Quality / Admin */
  .admin-sections { background:#fff; border-bottom:1px solid #e2e8f0; padding:0 40px 0 88px; display:flex; gap:0; position:sticky; top:60px; z-index:99; }
  .admin-section-btn { padding:14px 22px; font-family:'Barlow Condensed',sans-serif; font-size:14px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; cursor:pointer; border:none; background:none; color:#94a3b8; border-bottom:3px solid transparent; margin-bottom:-1px; transition:all .12s; display:flex; align-items:center; gap:8px; white-space:nowrap; }
  .admin-section-btn:hover { color:#334155; }
  .admin-section-btn.active { color:#0f172a; }
  .admin-section-btn .pip { width:8px; height:8px; border-radius:99px; background:#cbd5e1; }
  .admin-section-btn.active .pip { background: var(--sec-color, #1e40af); }

  /* Sub-tabs strip */
  .admin-tabs { background:#fff; border-bottom:1px solid #e2e8f0; padding:0 40px 0 88px; display:flex; gap:0; overflow-x:auto; position:sticky; top:114px; z-index:98; }
  .admin-tab { padding:11px 18px; font-family:'Barlow',sans-serif; font-size:12.5px; font-weight:600; cursor:pointer; border:none; background:none; color:#64748b; border-bottom:2px solid transparent; margin-bottom:-1px; transition:all .12s; display:flex; align-items:center; gap:7px; white-space:nowrap; }
  .admin-tab:hover { color:#334155; }
  .admin-tab.active { color: var(--sec-color, #1e40af); border-bottom-color: var(--sec-color, #1e40af); }
  .admin-body { padding:30px 40px 0; max-width:1180px; margin:0 auto; }

  /* Read-only mode — Department / Production users see the same panels
     but every create/update/delete affordance is hidden so they truly
     can't trigger any mutation. Three rules cover ~all CUD UI:
       1. Btn variant primary/danger/success (Add / Save / Delete)
       2. Every button inside a tbody row (Edit / Deactivate / Acknowledge)
          — always row-level mutations in this app
       3. Header-area "Add" / "+ New" buttons in flex-end toolbars are
          variant=primary so rule 1 catches them.
     Inputs are pointer-events:none so even the rare button that slips
     through can't actually mutate; SELECT / CHECKBOX / RADIO are also
     locked.  Tab-bar and modal-close buttons (raw <button>, no Btn)
     stay clickable.                                                  */
  .ap-readonly button[data-variant="primary"],
  .ap-readonly button[data-variant="danger"],
  .ap-readonly button[data-variant="success"] { display: none !important; }
  .ap-readonly tbody button,
  .ap-readonly tbody a[role="button"],
  .ap-readonly tbody input[type="button"],
  .ap-readonly tbody input[type="submit"] { display: none !important; }
  .ap-readonly input:not([type="checkbox"]):not([type="radio"]),
  .ap-readonly select,
  .ap-readonly textarea { pointer-events: none !important; background:#f8fafc !important; color:#475569 !important; }
  .ap-readonly input[type="checkbox"],
  .ap-readonly input[type="radio"] { pointer-events: none !important; opacity: .55; }
  /* Common file-upload / Excel-import buttons render as <label> instead
     of <button> — hide those too so dept users can't push data in. */
  .ap-readonly label[role="button"],
  .ap-readonly .excel-import-btn,
  .ap-readonly .excel-import-label { display: none !important; }

  /* ── Phone layout ─────────────────────────────────────────────────────
     Trim the desktop 40px gutter and let wide config/data tables scroll
     inside their own box instead of pushing the whole page sideways
     (standard responsive-table pattern, scoped to admin panels only). */
  @media (max-width: 600px) {
    .admin-body { padding: 18px 12px 0; }
    .admin-body table { display: block; overflow-x: auto; max-width: 100%; -webkit-overflow-scrolling: touch; }
    .admin-sections { overflow-x: auto; }
    .admin-topbar, .admin-sections, .admin-tabs { padding-right: 14px; }
  }
`;

// Shared shell renderer — used by AdminPanel (full-write) and
// DepartmentPanel (read-only).  `sections` lets DepartmentPanel filter
// to just the section(s) the user's department is allowed to see.
export function AdminShell({
  title,
  accent = "#1e40af",
  sections = ADMIN_SECTIONS,
  readOnly = false,
  rightTopbar = null,
}) {
  const [showToast, toastEl] = useToast();

  // URL hash format: #<section>/<tab> e.g. "#maintenance/pokayoke"
  const parseHash = () => {
    const h = (typeof window !== "undefined" ? window.location.hash : "").replace(/^#/, "");
    const [s, t] = h.split("/");
    const sec = sections.find(x => x.key === s) || sections[0];
    const tab = sec.tabs.find(x => x.key === t) || sec.tabs[0];
    return { section: sec.key, tab: tab.key };
  };
  const [active, setActive] = useState(parseHash);

  useEffect(() => { document.title = title; }, [title]);

  useEffect(() => {
    const want = `#${active.section}/${active.tab}`;
    if (window.location.hash !== want) {
      window.history.replaceState(null, "", window.location.pathname + want);
    }
  }, [active]);

  useEffect(() => {
    const onHash = () => {
      const next = parseHash();
      setActive(prev => (prev.section !== next.section || prev.tab !== next.tab) ? next : prev);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [sections]); // eslint-disable-line

  const sec = sections.find(s => s.key === active.section) || sections[0];
  // Always prefer the caller-supplied accent (which is theme.accent —
  // role-aware) over the section's hardcoded color.  Admin's blue then
  // overrides green/red/yellow when admin views a Production /
  // Maintenance / Quality panel; dept users on their own panel still
  // get their dept colour because their theme.accent is the dept colour.
  const secColor = accent || sec.color;
  const cssVars = { "--sec-color": secColor };

  const onPickSection = (k) => {
    const newSec = sections.find(s => s.key === k);
    if (!newSec) return;
    setActive({ section: k, tab: newSec.tabs[0].key });
  };

  // When the shell is rendered with exactly ONE section we hide the
  // section bar — the slide-nav already routed the user to a dedicated
  // page (Production / Maintenance / Quality / Admin) so the second
  // level of grouping is redundant.  The sub-tabs strip stays.
  const showSectionBar = sections.length > 1;

  return (
    <>
      <style>{ADMIN_PANEL_CSS}</style>

      <div className={`admin-root${readOnly ? " ap-readonly" : ""}`} style={cssVars}>
        <div className="admin-topbar" style={{ borderBottomColor:"#e2e8f0" }}>
          <div className="admin-logo">
            <span style={{ color: secColor }}>{title}</span>
          </div>
          {rightTopbar}
          <div style={{
            position:"absolute", bottom:0, left:0, right:0, height:2,
            background:`linear-gradient(90deg, ${secColor}, ${secColor}aa, ${secColor}55)`
          }}/>
        </div>

        {showSectionBar && (
          <div className="admin-sections">
            {sections.map(s => (
              <button
                key={s.key}
                className={`admin-section-btn${active.section === s.key ? " active" : ""}`}
                style={active.section === s.key ? { "--sec-color": s.color } : undefined}
                onClick={() => onPickSection(s.key)}
              >
                <span className="pip" style={active.section === s.key ? { background:s.color } : undefined}/>
                {s.label}
              </button>
            ))}
          </div>
        )}

        <div className="admin-tabs" style={!showSectionBar ? { top: 60 } : undefined}>
          {sec.tabs.map(t => (
            <button
              key={t.key}
              className={`admin-tab${active.tab === t.key ? " active" : ""}`}
              onClick={() => setActive(a => ({ ...a, tab: t.key }))}
            >
              <span>{t.icon}</span> {t.label}
            </button>
          ))}
        </div>

        <div className="admin-body">
          {renderAdminTab(active.section, active.tab, { toast: showToast, readOnly })}
        </div>
      </div>

      {toastEl}
    </>
  );
}

// ─── Per-section dedicated panel pages ────────────────────────
// Each is its own slide-nav entry / route so the user picks the panel
// up front and lands directly on its sub-tabs.  Internally they all
// reuse AdminShell with a single section in `sections`, which hides
// the section bar (only the sub-tab strip shows).
//
// Access matrix (also enforced by canAccess + Protected):
//   admin / plant_head : sees all 4 panels, full write, blue theme
//                        (because theme.accent = blue for admin)
//   production user    : sees Production Panel, read-only, green theme
//   maintenance dept   : sees Maintenance Panel, read-only, red theme
//   quality dept       : sees Quality Panel, read-only, yellow theme
//
// The accent is taken from `theme.accent` so each role gets its own
// colouring automatically; admin's blue overrides the section's hard-
// coded color since theme always wins.

// Wrap a per-section AdminShell with role-aware theme + readOnly.
// `sectionKey` picks which slice of ADMIN_SECTIONS to render.
function _RoleScopedShell({ title, sectionKey, page }) {
  const { theme, isAdmin } = useAuth();
  const sec = ADMIN_SECTIONS.filter(s => s.key === sectionKey);
  return (
    <>
      <AdminShell
        title={title}
        accent={theme.accent}
        sections={sec}
        readOnly={!isAdmin}
        rightTopbar={!isAdmin ? (
          <span style={{
            padding:"3px 10px", background:"#fef3c7", color:"#854d0e",
            borderRadius:99, fontSize:10, fontWeight:700,
            letterSpacing:".1em", textTransform:"uppercase",
          }}>Read-only</span>
        ) : null}
      />
      <AIAssistant pageContext={{ page }} />
    </>
  );
}

export function ProductionAdminPanel() {
  return <_RoleScopedShell title="Production Panel"  sectionKey="production"  page="ProductionAdminPanel" />;
}
export function MaintenanceAdminPanel() {
  return <_RoleScopedShell title="Maintenance Panel" sectionKey="maintenance" page="MaintenanceAdminPanel" />;
}
export function QualityAdminPanel() {
  return <_RoleScopedShell title="Quality Panel"     sectionKey="quality"     page="QualityAdminPanel" />;
}

// Default export = the "Admin core" panel (System Map / Departments /
// Users).  Strictly admin-only — the route gate (requiredAccess="admin")
// already blocks non-admins, so we never show readOnly state here.
export default function AdminPanel() {
  const { theme } = useAuth();
  const sec = ADMIN_SECTIONS.filter(s => s.key === "admin");
  return (
    <>
      <AdminShell title="Admin Panel" accent={theme.accent} sections={sec} />
      <AIAssistant pageContext={{ page: "AdminPanel" }} />
    </>
  );
}
