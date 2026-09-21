// ───────────────────────────────────────────────────────────────────────
// DeviceRegistry.jsx   (/device-registry)   2026-09-02
// ───────────────────────────────────────────────────────────────────────
// Admin view of every device running the MES: phone/tablet/browser, whether
// it launched the Android APK (TWA), an installed PWA, or a plain browser,
// the app version it is on, who last used it, and when it was last seen.
// Backed by /api/devices/list  (the web app self-reports via /checkin).
// ───────────────────────────────────────────────────────────────────────
import { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import PageTopbar from "../components/PageTopbar";

const th = { textAlign: "left", padding: "8px 12px", fontSize: 11, fontWeight: 700, color: "#64748b", textTransform: "uppercase", letterSpacing: ".04em", borderBottom: "1px solid #e2e8f0", whiteSpace: "nowrap" };
const td = { padding: "8px 12px", fontSize: 12.5, color: "#0f172a", borderBottom: "1px solid #f1f5f9", whiteSpace: "nowrap" };

const SRC = {
  apk:     { label: "APK",     bg: "#dcfce7", fg: "#166534", icon: "📱" },
  pwa:     { label: "PWA",     bg: "#dbeafe", fg: "#1e40af", icon: "🌐" },
  browser: { label: "Browser", bg: "#f1f5f9", fg: "#475569", icon: "💻" },
  capacitor: { label: "App", bg: "#dcfce7", fg: "#166534", icon: "📱" },
};

function ago(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60)    return "just now";
  if (s < 3600)  return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} hr ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
function fmt(ts) {
  if (!ts) return "—";
  try { return new Date(ts).toLocaleString(); } catch { return ts; }
}

export default function DeviceRegistry() {
  const { token } = useAuth();
  const [data, setData]   = useState({ devices: [], total: 0, by_source: {} });
  const [loading, setLd]  = useState(true);
  const [q, setQ]         = useState("");

  const load = useCallback(async () => {
    try { setData(await api.get("/api/devices/list", token) || { devices: [] }); }
    catch { /* keep last */ }
    finally { setLd(false); }
  }, [token]);
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);

  const devices = (data.devices || []).filter(d => {
    if (!q.trim()) return true;
    const hay = `${d.model||""} ${d.os||""} ${d.app_source||""} ${d.app_version||""} ${d.last_user||""} ${d.last_ip||""}`.toLowerCase();
    return hay.includes(q.trim().toLowerCase());
  });

  const chip = (src) => {
    const s = SRC[src] || SRC.browser;
    return <span style={{ background: s.bg, color: s.fg, padding: "2px 9px", borderRadius: 99, fontSize: 11, fontWeight: 800 }}>{s.icon} {s.label}</span>;
  };

  return (
    <div style={{ minHeight: "100vh", background: "#f8fafc" }}>
      <PageTopbar leading="Device" accent="Registry" />
      <div style={{ padding: "18px 22px", maxWidth: 1300, margin: "0 auto" }}>

        {/* Summary */}
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
          <div style={sumCard}><div style={sumNum}>{data.total || 0}</div><div style={sumLbl}>Total devices</div></div>
          {["apk", "pwa", "browser"].map(k => (
            <div key={k} style={sumCard}>
              <div style={{ ...sumNum, color: SRC[k].fg }}>{data.by_source?.[k] || 0}</div>
              <div style={sumLbl}>{SRC[k].icon} {SRC[k].label}</div>
            </div>
          ))}
          <div style={{ flex: 1 }} />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search model / user / version…"
                 style={{ padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 8, fontSize: 13, minWidth: 220, alignSelf: "center" }} />
        </div>

        {loading ? (
          <div style={{ padding: 40, textAlign: "center", color: "#94a3b8" }}>Loading…</div>
        ) : devices.length === 0 ? (
          <div style={{ padding: 40, textAlign: "center", color: "#94a3b8" }}>
            No devices yet. A device appears here after someone logs in on it.
          </div>
        ) : (
          <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12, overflow: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr>
                <th style={th}>Device</th>
                <th style={th}>OS</th>
                <th style={th}>Source</th>
                <th style={th}>App version</th>
                <th style={th}>Web build</th>
                <th style={th}>Screen</th>
                <th style={th}>Last user</th>
                <th style={th}>Last seen</th>
                <th style={{ ...th, textAlign: "right" }}>Check-ins</th>
                <th style={th}>IP</th>
              </tr></thead>
              <tbody>
                {devices.map(d => (
                  <tr key={d.device_id}>
                    <td style={{ ...td, fontWeight: 700 }}>{d.model || "Unknown device"}</td>
                    <td style={td}>{d.os || "—"}</td>
                    <td style={td}>{chip(d.app_source)}</td>
                    <td style={td}><code style={{ fontSize: 12 }}>{d.app_version || "—"}</code></td>
                    <td style={{ ...td, color: "#64748b" }}>{d.web_version || "—"}</td>
                    <td style={{ ...td, color: "#64748b" }}>{d.screen || "—"}</td>
                    <td style={td}>{d.last_user || "—"}</td>
                    <td style={td} title={fmt(d.last_seen)}>{ago(d.last_seen)}</td>
                    <td style={{ ...td, textAlign: "right", color: "#64748b" }}>{d.checkin_count}</td>
                    <td style={{ ...td, color: "#94a3b8", fontSize: 11 }}>{d.last_ip || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div style={{ marginTop: 10, fontSize: 11, color: "#94a3b8" }}>
          Auto-refreshes every 30s · APK devices report their exact version; browsers/PWA show the web build.
        </div>
      </div>
    </div>
  );
}

const sumCard = { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10, padding: "12px 18px", minWidth: 110, textAlign: "center" };
const sumNum  = { fontSize: 26, fontWeight: 900, color: "#0f172a", lineHeight: 1 };
const sumLbl  = { fontSize: 11, color: "#64748b", marginTop: 4, fontWeight: 600 };
