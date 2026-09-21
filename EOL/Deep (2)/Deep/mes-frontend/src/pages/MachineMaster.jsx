/* ───────────────────────────────────────────────────────────────────
 * MachineMaster.jsx   (Admin → Machine Master)
 * ───────────────────────────────────────────────────────────────────
 * DB-backed machine master (from the user's Excel) with LIVE green/red
 * status per machine IP AND camera IP (ICMP ping, backend poller).
 * Source: GET /api/machine-master  ·  Upload: POST /api/machine-master/upload
 */
import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const CSS = `
.mm-root{font-family:'Barlow',sans-serif;color:#e2e8f0}
.mm-card{background:#111c30;border:1px solid #1e2a44;border-radius:14px}
.mm-kpi{flex:1;min-width:140px;background:#111c30;border:1px solid #1e2a44;border-radius:12px;padding:12px 14px;position:relative;overflow:hidden}
.mm-kpi::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--ac)}
.mm-pill{padding:6px 14px;border-radius:9px;font-size:12.5px;font-weight:700;cursor:pointer;border:1px solid #243150;background:#0f1830;color:#94a3b8}
.mm-pill.on{background:#1d4ed8;border-color:#1d4ed8;color:#fff}
.mm-row:hover{background:#0f1830}
.mm-dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px;vertical-align:middle}
.mm-th{text-align:left;padding:9px 12px;font-size:10px;font-weight:800;letter-spacing:.05em;color:#5b6b86;text-transform:uppercase;position:sticky;top:0;background:#0d1626}
.mm-td{padding:8px 12px;font-size:13px;color:#c7d2e4;border-bottom:1px solid #18233c;white-space:nowrap}
`;

function Dot({ s }) {
  const c = s === true ? "#22c55e" : s === false ? "#ef4444" : "#475569";
  return <span className="mm-dot" title={s === true ? "online" : s === false ? "offline" : "no data"}
               style={{ background: c, boxShadow: s == null ? "none" : `0 0 6px ${c}aa` }} />;
}

export default function MachineMaster() {
  const { token, isAdmin } = useAuth();
  const [data, setData] = useState(null);
  const [zone, setZone] = useState("All");
  const [q, setQ]       = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg]   = useState("");
  const fileRef = useRef(null);

  const load = useCallback(() => {
    api.get("/api/machine-master", token).then(setData).catch(() => setData({ _err: true }));
  }, [token]);
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);

  const machines = (data && Array.isArray(data.machines)) ? data.machines : [];
  const zones = ["All", ...Array.from(new Set(machines.map(m => (m.zone || "").trim()).filter(Boolean)))];
  const ql = q.trim().toLowerCase();
  const rows = machines.filter(m => {
    if (zone !== "All" && (m.zone || "").trim() !== zone) return false;
    if (!ql) return true;
    return [m.line, m.machine_no, m.machine_name, m.ip, m.camera_ip, m.data_register]
      .some(v => (v || "").toLowerCase().includes(ql));
  });

  const ipUp  = machines.filter(m => m.ip_up === true).length;
  const ipCfg = machines.filter(m => (m.ip || "").trim()).length;
  const camUp = machines.filter(m => m.cam_up === true).length;
  const camCfg= machines.filter(m => (m.camera_ip || "").trim()).length;

  const upload = async (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    setBusy(true); setMsg("");
    try {
      const fd = new FormData(); fd.append("file", f);
      const res = await fetch("/api/machine-master/upload", { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: fd });
      const j = await res.json();
      if (!res.ok) throw new Error(j.detail || `HTTP ${res.status}`);
      setMsg(`Imported ${j.count} machines ✓`); load();
    } catch (err) { setMsg("Upload failed: " + (err.message || err)); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  };

  const KPIS = [
    { ac: "#22c55e", val: `${ipUp}/${ipCfg}`, lab: "Machines online (IP)" },
    { ac: "#3b82f6", val: `${camUp}/${camCfg}`, lab: "Cameras online" },
    { ac: "#64748b", val: `${machines.length}`, lab: "Total machines" },
    { ac: "#f59e0b", val: `${zones.length - 1}`, lab: "Zones" },
  ];

  return (
    <div className="mm-root">
      <style>{CSS}</style>

      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 14 }}>
        <div style={{ fontSize: 19, fontWeight: 800, color: "#f8fafc" }}>🏭 Machine Master</div>
        <span style={{ fontSize: 12, color: "#7c8aa5" }}>live IP + camera status (ping every 60s)</span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          {msg && <span style={{ fontSize: 12, color: msg.includes("fail") ? "#fca5a5" : "#34d399" }}>{msg}</span>}
          <button className="mm-pill" onClick={load}>↻ Refresh</button>
          {isAdmin && <>
            <input ref={fileRef} type="file" accept=".xlsx,.xlsm" style={{ display: "none" }} onChange={upload} />
            <button className="mm-pill on" disabled={busy} onClick={() => fileRef.current && fileRef.current.click()}>
              {busy ? "uploading…" : "⬆ Upload Excel"}
            </button>
          </>}
        </div>
      </div>

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
        {KPIS.map((k, i) => (
          <div key={i} className="mm-kpi" style={{ "--ac": k.ac }}>
            <div style={{ fontSize: 23, fontWeight: 800, color: "#f1f5f9", lineHeight: 1.05 }}>{k.val}</div>
            <div style={{ fontSize: 11, color: "#7c8aa5", marginTop: 3, fontWeight: 600 }}>{k.lab}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12, alignItems: "center" }}>
        {zones.map(z => (
          <button key={z} className={`mm-pill${zone === z ? " on" : ""}`} onClick={() => setZone(z)}>{z}</button>
        ))}
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="search line / machine / ip…"
               style={{ marginLeft: "auto", minWidth: 220, padding: "7px 11px", borderRadius: 9, border: "1px solid #243150", background: "#0d1626", color: "#e2e8f0", fontSize: 13 }} />
      </div>

      <div className="mm-card" style={{ overflow: "auto", maxHeight: "62vh" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              {["Zone", "Line", "Machine No", "Machine Name", "IP", "Port", "Camera IP", "Data Register"].map(h =>
                <th key={h} className="mm-th">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((m, i) => (
              <tr key={m.id ?? i} className="mm-row">
                <td className="mm-td">{m.zone || "—"}</td>
                <td className="mm-td">{m.line || "—"}</td>
                <td className="mm-td" style={{ fontWeight: 700, color: "#e7eefc" }}>{m.machine_no || "—"}</td>
                <td className="mm-td" style={{ whiteSpace: "normal", minWidth: 220 }}>{m.machine_name || "—"}</td>
                <td className="mm-td" style={{ fontFamily: "monospace" }}><Dot s={m.ip_up} />{m.ip || "—"}</td>
                <td className="mm-td" style={{ fontFamily: "monospace" }}>{m.port || "—"}</td>
                <td className="mm-td" style={{ fontFamily: "monospace" }}><Dot s={m.cam_up} />{m.camera_ip || "—"}</td>
                <td className="mm-td" style={{ fontFamily: "monospace" }}>{m.data_register || "—"}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td className="mm-td" colSpan={8} style={{ padding: 18, color: "#5b6b86" }}>
                {data && data._err ? "Backend not reachable." : machines.length ? "No machines match the filter." : "No machines yet — Upload the Excel."}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div style={{ marginTop: 10, display: "flex", gap: 18, fontSize: 11, color: "#7c8aa5", flexWrap: "wrap" }}>
        <span><span className="mm-dot" style={{ background: "#22c55e" }} />online (ping reply)</span>
        <span><span className="mm-dot" style={{ background: "#ef4444" }} />offline</span>
        <span><span className="mm-dot" style={{ background: "#475569" }} />no IP / not checked yet</span>
        <span style={{ marginLeft: "auto" }}>showing {rows.length} of {machines.length}</span>
      </div>
    </div>
  );
}
