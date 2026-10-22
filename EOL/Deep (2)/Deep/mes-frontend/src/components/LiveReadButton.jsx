// components/LiveReadButton.jsx — "Read live" for one machine (2026-10-09)
//   Reads every configured register of the machine (OK/NG, status, model,
//   count registers, SA registers, reject / fetch bits …) through the line's
//   own collector: these PLCs take one session, so MES never connects itself.
//   Read-only — nothing is written to the PLC.
import { useState, useCallback } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const fmtVal = (v) => (v == null ? "—" : Array.isArray(v) ? v.join(", ") : String(v));

export default function LiveReadButton({ machine, size = "sm" }) {
  const { token } = useAuth();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");

  const read = useCallback(async () => {
    setBusy(true); setErr(""); setRes(null);
    try {
      let r = await api.post(`/api/live-read/${machine.id}`, {}, token);
      // collector slow to answer: keep asking for up to ~30 s more
      for (let i = 0; i < 15 && r && !["done", "error"].includes(r.status); i++) {
        await new Promise(ok => setTimeout(ok, 2000));
        const g = await api.get(`/api/live-read/result/${r.request_id}`, token);
        r = { ...r, ...g, error: g.status === "pending" || g.status === "taken" ? r.error : g.error };
      }
      setRes(r);
    } catch (e) { setErr(e.message || "Read failed"); }
    finally { setBusy(false); }
  }, [machine.id, token]);

  const btn = { padding: size === "sm" ? "5px 10px" : "7px 14px", fontSize: 12, fontWeight: 700, borderRadius: 7,
                border: "1px solid #bfdbfe", background: "#eff6ff", color: "#1d4ed8", cursor: "pointer", whiteSpace: "nowrap" };
  const th = { textAlign: "left", fontSize: 11, color: "#64748b", padding: "6px 8px", borderBottom: "1px solid #e2e8f0", whiteSpace: "nowrap" };
  const td = { fontSize: 12.5, padding: "6px 8px", borderBottom: "1px solid #f1f5f9", fontVariantNumeric: "tabular-nums", verticalAlign: "top" };

  return (
    <>
      <button type="button" style={btn} onClick={() => { setOpen(true); read(); }}
              title="Read every register of this machine through its collector">⚡ Read live</button>
      {open && (
        <div onClick={() => !busy && setOpen(false)}
             style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,.45)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
          <div onClick={e => e.stopPropagation()}
               style={{ background: "#fff", borderRadius: 14, width: "min(760px, 100%)", maxHeight: "86vh", overflow: "auto", padding: 18, boxShadow: "0 20px 50px rgba(0,0,0,.25)" }}>
            <div style={{ display: "flex", alignItems: "start", gap: 10, marginBottom: 10 }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 800, fontSize: 15, color: "#0f172a" }}>Live values · {machine.machine_name || "Machine"}</div>
                <div style={{ fontSize: 12, color: "#64748b" }}>
                  {machine.plc_ip}:{machine.plc_port} · read by the line's collector on its own PLC session · nothing is written
                </div>
              </div>
              <button type="button" style={btn} disabled={busy} onClick={read}>{busy ? "Reading…" : "↻ Read again"}</button>
              <button type="button" onClick={() => setOpen(false)} disabled={busy}
                      style={{ border: "none", background: "none", fontSize: 20, cursor: "pointer", color: "#64748b" }} aria-label="Close">×</button>
            </div>
            {busy && <div style={{ fontSize: 13, color: "#1d4ed8", padding: "10px 0" }}>Asking the collector… (usually 1–3 s)</div>}
            {err && <div style={{ background: "#fee2e2", color: "#b91c1c", borderRadius: 8, padding: "8px 10px", fontSize: 13 }}>{err}</div>}
            {res && res.status !== "done" && (
              <div style={{ background: "#fef3c7", color: "#92400e", borderRadius: 8, padding: "8px 10px", fontSize: 13, marginBottom: 8 }}>
                {res.error || `No answer yet (${res.status}).`}
              </div>
            )}
            {res?.values && (
              <>
                <table style={{ borderCollapse: "collapse", width: "100%" }}>
                  <thead><tr>{["Register", "Address", "Type", "Value", ""].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
                  <tbody>
                    {res.values.map((v, i) => (
                      <tr key={i}>
                        <td style={{ ...td, fontWeight: 700 }}>{v.label}</td>
                        <td style={{ ...td, fontFamily: "monospace" }}>{v.addr}{v.len > 1 ? ` ×${v.len}` : ""}</td>
                        <td style={{ ...td, color: "#64748b" }}>{v.kind}</td>
                        <td style={{ ...td, fontFamily: "monospace", fontWeight: 800, color: v.error ? "#94a3b8" : "#0f172a" }}>{fmtVal(v.value)}</td>
                        <td style={{ ...td, color: "#b91c1c", fontSize: 11.5 }}>{v.error || ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div style={{ fontSize: 11.5, color: "#94a3b8", marginTop: 8 }}>
                  Read at {res.read_at ? new Date(res.read_at).toLocaleTimeString("en-GB") : "—"}{res.took_ms != null ? ` · ${(res.took_ms / 1000).toFixed(1)} s` : ""}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
