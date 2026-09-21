// ───────────────────────────────────────────────────────────────────────
// PanelMapPage.jsx   (Admin → Production → Panel Displays)   2026-09-13
// ───────────────────────────────────────────────────────────────────────
// Map each LAN panel (by its IP) to one line + view. When the LAN app opens on
// that panel it jumps straight to that dashboard. Backed by /api/panel/maps.
// ───────────────────────────────────────────────────────────────────────
import { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const VIEWS = [
  { key: "management", label: "Management dashboard" },
  { key: "supervisor", label: "Supervisor wallboard" },
  { key: "fullscreen", label: "Fullscreen" },
  { key: "dashboard",  label: "Dashboard" },
];
const th = { textAlign: "left", padding: "8px 12px", fontSize: 11, fontWeight: 700, color: "#64748b", textTransform: "uppercase", letterSpacing: ".04em", borderBottom: "1px solid #e2e8f0", whiteSpace: "nowrap" };
const td = { padding: "9px 12px", fontSize: 13, color: "#0f172a", borderBottom: "1px solid #f1f5f9" };
const inp = { padding: "8px 10px", border: "1px solid #cbd5e1", borderRadius: 8, fontSize: 13, background: "#fff", color: "#0f172a" };

export default function PanelMapPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [maps, setMaps]   = useState([]);
  const [lines, setLines] = useState([]);
  const [myIp, setMyIp]   = useState("");
  const [ip, setIp]       = useState("");
  const [lineId, setLineId] = useState("");
  const [view, setView]   = useState("management");
  const [autoUser, setAutoUser] = useState("");
  const [busy, setBusy]   = useState(false);

  const load = useCallback(async () => {
    try {
      const [m, ls, who] = await Promise.all([
        api.get("/api/panel/maps", token),
        api.get("/api/lines/", token).catch(() => []),
        api.get("/api/panel/whoami", token).catch(() => ({})),
      ]);
      setMaps(m || []);
      setLines(Array.isArray(ls) ? ls : []);
      setMyIp(who?.ip || "");
    } catch (e) { toast?.("Failed to load panel maps", "err"); }
  }, [token]);
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const save = async () => {
    if (!ip.trim()) { toast?.("Enter the panel IP", "err"); return; }
    if (!lineId)    { toast?.("Pick a line", "err"); return; }
    setBusy(true);
    try {
      await api.post("/api/panel/maps", { panel_ip: ip.trim(), line_id: Number(lineId), view, auto_user: autoUser.trim() || null }, token);
      toast?.("Panel mapping saved ✓");
      setIp(""); setLineId(""); setView("management"); setAutoUser(""); await load();
    } catch (e) { toast?.(e.message || "Save failed", "err"); }
    finally { setBusy(false); }
  };
  const del = async (pip) => {
    if (!confirm(`Remove mapping for ${pip}?`)) return;
    try { await api.delete(`/api/panel/maps?panel_ip=${encodeURIComponent(pip)}`, token); await load(); }
    catch (e) { toast?.(e.message || "Delete failed", "err"); }
  };
  const edit = (m) => { setIp(m.panel_ip); setLineId(m.line_id || ""); setView(m.view || "management"); setAutoUser(m.auto_user || ""); };

  return (
    <div style={{ maxWidth: 820 }}>
      <div style={{ fontSize: 12.5, color: "#64748b", marginBottom: 14 }}>
        Map each LAN panel (by its IP) to a line + view. When the LAN app opens on that panel it jumps straight to that dashboard.
      </div>

      {!readOnly && (
        <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, padding: 16, marginBottom: 18 }}>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end" }}>
            <div>
              <div style={{ fontSize: 11.5, fontWeight: 700, color: "#334155", marginBottom: 4 }}>Panel IP</div>
              <input value={ip} onChange={e => setIp(e.target.value)} placeholder="e.g. 192.168.30.61" style={{ ...inp, width: 180 }} />
              {myIp && <button onClick={() => setIp(myIp)} style={{ marginLeft: 8, fontSize: 11, border: "1px solid #cbd5e1", background: "#f8fafc", borderRadius: 7, padding: "6px 9px", cursor: "pointer" }}>Use this device ({myIp})</button>}
            </div>
            <div>
              <div style={{ fontSize: 11.5, fontWeight: 700, color: "#334155", marginBottom: 4 }}>Line</div>
              <select value={lineId} onChange={e => setLineId(e.target.value)} style={{ ...inp, minWidth: 170 }}>
                <option value="">— pick line —</option>
                {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
              </select>
            </div>
            <div>
              <div style={{ fontSize: 11.5, fontWeight: 700, color: "#334155", marginBottom: 4 }}>View</div>
              <select value={view} onChange={e => setView(e.target.value)} style={{ ...inp, minWidth: 190 }}>
                {VIEWS.map(v => <option key={v.key} value={v.key}>{v.label}</option>)}
              </select>
            </div>
            <div>
              <div style={{ fontSize: 11.5, fontWeight: 700, color: "#334155", marginBottom: 4 }}>Auto-login as (optional)</div>
              <input value={autoUser} onChange={e => setAutoUser(e.target.value)} placeholder="e.g. ysd" style={{ ...inp, width: 150 }} />
            </div>
            <button onClick={save} disabled={busy}
                    style={{ background: "#1e40af", color: "#fff", border: "none", borderRadius: 8, padding: "9px 18px", fontSize: 13, fontWeight: 700, cursor: "pointer", opacity: busy ? .6 : 1 }}>
              {busy ? "Saving…" : "Save mapping"}
            </button>
          </div>
        </div>
      )}

      <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 12 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 560 }}>
          <thead><tr>{["Panel IP", "Line", "View", "Auto-login", ""].map((h, i) => <th key={i} style={th}>{h}</th>)}</tr></thead>
          <tbody>
            {maps.length === 0 && <tr><td colSpan={5} style={{ ...td, color: "#94a3b8", textAlign: "center" }}>No panel mappings yet.</td></tr>}
            {maps.map(m => (
              <tr key={m.panel_ip} style={{ background: m.panel_ip === myIp ? "#eff6ff" : "#fff" }}>
                <td style={{ ...td, fontFamily: "monospace", fontWeight: 700 }}>{m.panel_ip}{m.panel_ip === myIp ? "  (this device)" : ""}</td>
                <td style={td}>{m.line_name || "—"}</td>
                <td style={td}>{VIEWS.find(v => v.key === m.view)?.label || m.view}</td>
                <td style={{ ...td, fontFamily: "monospace" }}>{m.auto_user ? m.auto_user : <span style={{ color: "#94a3b8" }}>—</span>}</td>
                <td style={td}>
                  {!readOnly && (<>
                    <button onClick={() => edit(m)} style={{ fontSize: 12, border: "1px solid #cbd5e1", background: "#fff", borderRadius: 7, padding: "4px 10px", cursor: "pointer", marginRight: 6 }}>Edit</button>
                    <button onClick={() => del(m.panel_ip)} style={{ fontSize: 12, border: "1px solid #fecaca", color: "#dc2626", background: "#fff", borderRadius: 7, padding: "4px 10px", cursor: "pointer" }}>Delete</button>
                  </>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
