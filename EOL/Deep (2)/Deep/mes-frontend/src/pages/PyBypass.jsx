// PyBypass.jsx — poka-yoke bypasses waiting for a quality decision.
//
// A bypass opens a case and mails the quality team with Approve / Reject
// buttons that work inside the mail.  Approve creates a deviation; reject sets
// the configured bit on the machine.  Both close by themselves when the
// poka-yoke is working again.  This page shows the live state; the decision is
// taken in the mail.  Admins can set the per-line bit here.
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import PageTopbar from "../components/PageTopbar";

const REFRESH_MS = 20000;

const dt = (iso) =>
  iso ? new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
const ago = (iso) => {
  if (!iso) return "—";
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  return m < 60 ? `${m} min` : `${(m / 60).toFixed(1)} h`;
};

const STATUS = {
  WAITING:  ["Waiting for quality", "#b45309", "#fef3c7"],
  APPROVED: ["Approved", "#15803d", "#dcfce7"],
  REJECTED: ["Rejected", "#b91c1c", "#fee2e2"],
  CLEARED:  ["Cleared before decision", "#475569", "#e2e8f0"],
};

function Pill({ status }) {
  const [label, fg, bg] = STATUS[status] || [status, "#475569", "#e2e8f0"];
  return (
    <span style={{ background: bg, color: fg, padding: "2px 10px", borderRadius: 999,
                   fontSize: 12, fontWeight: 700, whiteSpace: "nowrap" }}>{label}</span>
  );
}

function Kpi({ label, value, tone }) {
  return (
    <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderTop: `3px solid ${tone}`,
                  borderRadius: 10, padding: "12px 14px" }}>
      <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".05em", color: "#64748b" }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 800, color: "#0f172a", marginTop: 2 }}>{value}</div>
    </div>
  );
}

export default function PyBypass() {
  //  2026-09-25 — this page was written against an axios-shaped client: it
  //  called api.get(path) with no token, read r.data, and looked for
  //  e.response.status.  api/client.jsx is a fetch wrapper: the token is the
  //  SECOND argument, the parsed body IS the return value, and a failure is a
  //  plain Error whose message holds the detail.  So every call here went out
  //  with no Authorization header, the backend answered 401, and the client's
  //  401 handler sent the user to the login screen — opening PY Bypass logged
  //  you out.
  const { user, token } = useAuth();
  const isAdmin = (user?.role || "") === "admin";
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState("open");
  const [bits, setBits] = useState(null);
  const [saving, setSaving] = useState("");

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setData(await api.get("/api/py-bypass/cases?hours=24", token));
      setErr("");
    } catch (e) {
      const msg = e?.message || "";
      setErr(msg.includes("404")
        ? "This page becomes active after the next MES-API restart."
        : (msg || "Could not load bypass cases."));
    }
  }, [token]);

  const loadBits = useCallback(async () => {
    if (!isAdmin || !token) return;
    try {
      const r = await api.get("/api/py-bypass/bits", token);
      setBits(r?.lines || []);
    } catch { /* not admin */ }
  }, [isAdmin, token]);

  useEffect(() => { load(); loadBits(); }, [load, loadBits]);   // eslint-disable-line react-hooks/set-state-in-effect
  useEffect(() => {
    const t = setInterval(() => load(), REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  const counts = data?.counts || {};
  const rows = useMemo(() => (tab === "open" ? data?.open : data?.recent) || [], [tab, data]);

  const saveBit = async (line) => {
    setSaving(line.line_id);
    try {
      await api.put("/api/py-bypass/bits", {
        line_id: line.line_id, bit_addr: line.bit_addr || "", active: line.active !== false,
      }, token);
      await loadBits();
    } catch (e) {
      alert(e?.message || "Could not save the bit.");
    }
    setSaving("");
  };

  return (
    <div style={{ padding: 16, maxWidth: 1280, margin: "0 auto" }}>
      <PageTopbar title="Poka-Yoke Bypass" />
      <div style={{ color: "#64748b", fontSize: 13, margin: "2px 0 14px" }}>
        Quality approves or rejects each bypass from the mail. Approve creates a deviation;
        reject sets the machine bit. Both clear automatically when the poka-yoke is OK again.
        {data ? ` Reminder after ${data.reminder_min} min, escalation after ${data.escalate_min} min.` : ""}
      </div>

      {err && <div style={{ background: "#fee2e2", color: "#b91c1c", padding: "10px 14px",
                            borderRadius: 8, marginBottom: 12, fontSize: 13 }}>{err}</div>}

      <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fill,minmax(150px,1fr))",
                    marginBottom: 16 }}>
        <Kpi label="Open" value={counts.open ?? "—"} tone="#1e40af" />
        <Kpi label="Waiting for quality" value={counts.waiting ?? "—"} tone="#b45309" />
        <Kpi label="Approved (24 h)" value={counts.approved ?? "—"} tone="#15803d" />
        <Kpi label="Rejected (24 h)" value={counts.rejected ?? "—"} tone="#b91c1c" />
        <Kpi label="Machine bit ON" value={counts.bit_on ?? "—"} tone="#7c3aed" />
      </div>

      <div style={{ display: "flex", gap: 18, borderBottom: "1px solid #e2e8f0", marginBottom: 12 }}>
        {[["open", `Open (${data?.open?.length ?? 0})`], ["recent", `Closed, last 24 h (${data?.recent?.length ?? 0})`]]
          .map(([k, label]) => (
            <button key={k} onClick={() => setTab(k)} style={{
              background: "none", border: "none", padding: "8px 2px", cursor: "pointer", fontSize: 14,
              fontWeight: tab === k ? 700 : 500, color: tab === k ? "#0f172a" : "#64748b",
              borderBottom: tab === k ? "2px solid #1e40af" : "2px solid transparent" }}>{label}</button>
          ))}
      </div>

      <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 10, background: "#fff" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 900 }}>
          <thead>
            <tr style={{ background: "#f8fafc", color: "#64748b", fontSize: 11, textTransform: "uppercase" }}>
              {["Line", "Poka-Yoke", "Register", "Expected / actual", "Detected", "Waiting", "Status", "Decision", "Result"]
                .map((h) => <th key={h} style={{ textAlign: "left", padding: "9px 10px", borderBottom: "1px solid #e2e8f0" }}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={9} style={{ padding: 22, textAlign: "center", color: "#15803d", fontWeight: 600 }}>
                No bypass {tab === "open" ? "open right now" : "closed in the last 24 hours"}.
              </td></tr>
            )}
            {rows.map((c) => (
              <tr key={c.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                <td style={{ padding: "9px 10px" }}>
                  <b>{c.line_name || c.line_id}</b>
                  <div style={{ color: "#94a3b8", fontSize: 11 }}>{c.zone_name}</div>
                </td>
                <td style={{ padding: "9px 10px" }}>
                  {c.py_name || "—"}
                  <div style={{ color: "#94a3b8", fontSize: 11 }}>{c.py_no}</div>
                </td>
                <td style={{ padding: "9px 10px", fontFamily: "monospace" }}>{c.register_addr || "—"}</td>
                <td style={{ padding: "9px 10px", fontFamily: "monospace", fontSize: 12 }}>
                  {(c.expected_value || "—")} → <b style={{ color: "#b91c1c" }}>{c.actual_value || "—"}</b>
                </td>
                <td style={{ padding: "9px 10px" }}>{dt(c.detected_at)}</td>
                <td style={{ padding: "9px 10px" }}>{c.decided_at ? "—" : ago(c.detected_at)}</td>
                <td style={{ padding: "9px 10px" }}><Pill status={c.status} /></td>
                <td style={{ padding: "9px 10px" }}>
                  {c.decided_by || "—"}
                  {c.decided_at && <div style={{ color: "#94a3b8", fontSize: 11 }}>{dt(c.decided_at)}</div>}
                </td>
                <td style={{ padding: "9px 10px" }}>
                  {c.deviation_no && <span style={{ color: "#15803d", fontWeight: 600 }}>{c.deviation_no}</span>}
                  {c.bit_addr && (
                    <span style={{ color: c.bit_state ? "#b91c1c" : "#64748b", fontWeight: 600 }}>
                      Bit {c.bit_addr} {c.bit_state ? "ON" : "off"}
                    </span>
                  )}
                  {!c.deviation_no && !c.bit_addr && (c.status === "REJECTED"
                    ? <span style={{ color: "#b45309" }}>No bit configured</span> : "—")}
                  {c.closed_at && <div style={{ color: "#94a3b8", fontSize: 11 }}>{c.close_reason}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {isAdmin && (
        <div style={{ marginTop: 26 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: "#0f172a", marginBottom: 4 }}>
            Bit set on reject, per line
          </div>
          <div style={{ color: "#64748b", fontSize: 12, marginBottom: 10 }}>
            The bit MES sets when quality rejects a bypass on that line, and clears when the
            poka-yoke is OK again. Leave it empty and nothing is ever written for that line.
            What the bit does on the machine is ladder logic.
          </div>
          <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 10, background: "#fff" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 620 }}>
              <thead>
                <tr style={{ background: "#f8fafc", color: "#64748b", fontSize: 11, textTransform: "uppercase" }}>
                  {["Zone", "Line", "Bit", "Active", "Updated", ""].map((h) => (
                    <th key={h} style={{ textAlign: "left", padding: "9px 10px", borderBottom: "1px solid #e2e8f0" }}>{h}</th>))}
                </tr>
              </thead>
              <tbody>
                {(bits || []).map((l, i) => (
                  <tr key={l.line_id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                    <td style={{ padding: "7px 10px", color: "#64748b" }}>{l.zone_name || "—"}</td>
                    <td style={{ padding: "7px 10px", fontWeight: 600 }}>{l.line_name}</td>
                    <td style={{ padding: "7px 10px" }}>
                      <input value={l.bit_addr || ""} placeholder="e.g. M500"
                        onChange={(e) => { const b = [...bits]; b[i] = { ...l, bit_addr: e.target.value }; setBits(b); }}
                        style={{ width: 110, padding: "5px 8px", border: "1px solid #cbd5e1", borderRadius: 6,
                                 fontFamily: "monospace", textTransform: "uppercase" }} />
                    </td>
                    <td style={{ padding: "7px 10px" }}>
                      <input type="checkbox" checked={l.active !== false}
                        onChange={(e) => { const b = [...bits]; b[i] = { ...l, active: e.target.checked }; setBits(b); }} />
                    </td>
                    <td style={{ padding: "7px 10px", color: "#94a3b8", fontSize: 11 }}>
                      {l.updated_at ? `${dt(l.updated_at)} · ${l.updated_by || ""}` : "—"}
                    </td>
                    <td style={{ padding: "7px 10px" }}>
                      <button onClick={() => saveBit(l)} disabled={saving === l.line_id}
                        style={{ background: "#1e40af", color: "#fff", border: "none", borderRadius: 6,
                                 padding: "5px 14px", cursor: "pointer", fontSize: 12, fontWeight: 600 }}>
                        {saving === l.line_id ? "Saving…" : "Save"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
