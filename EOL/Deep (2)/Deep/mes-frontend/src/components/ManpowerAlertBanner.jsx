/* ════════════════════════════════════════════════════════════════════
 *  ManpowerAlertBanner.jsx
 *  ────────────────────────────────────────────────────────────────────
 *  Popup banner that appears at the top of Quality + Section Incharge
 *  dashboards when there's an unresolved manpower alert:
 *    • UNALLOCATED  — supervisor missed the per-line deadline
 *    • SKILL_MISMATCH — operator allocated to a process they aren't
 *                       skilled enough for
 *    • ESCALATION   — neither side acked within ack_timeout_minutes
 *
 *  Each side has its own Acknowledge button (Quality / Section Incharge).
 *  Backend marks the alert resolved once BOTH sides have acknowledged.
 *
 *  Polls /api/manpower/alerts?pending_only=true every 30 s.
 *  Auto-hides itself when the role isn't supposed to see it.
 * ════════════════════════════════════════════════════════════════════ */

import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";

const KIND_META = {
  UNALLOCATED:     { color: "#dc2626", icon: "👥", label: "Unallocated Slots" },
  SKILL_MISMATCH:  { color: "#d97706", icon: "⚠",  label: "Skill Mismatch"    },
  ESCALATION:      { color: "#7c2d12", icon: "🚨", label: "Escalated"          },
};

export default function ManpowerAlertBanner() {
  const { token, user, isAdmin, isDepartment } = useAuth();
  const slug = (user?.departmentSlug || "").toLowerCase();

  // Who sees this banner:
  //   • admin / plant_head           (always)
  //   • Quality dept user            (acks as "quality")
  //   • Maintenance/Section Incharge → in this codebase we treat the
  //     production-dept supervisor as Section Incharge; they ack as
  //     "incharge".  Admin sees both buttons.
  const isQuality   = slug === "quality";
  const isIncharge  = slug === "production" || slug === "maintenance";
  const visible     = isAdmin || isQuality || isIncharge;

  const [alerts, setAlerts] = useState([]);
  const [hiding, setHiding] = useState({});   // alert_id -> true during ack

  const load = useCallback(async () => {
    if (!visible) return;
    try {
      const r = await api.get("/api/manpower/alerts?pending_only=true", token);
      setAlerts(r || []);
    } catch { /* silent — stale banner is fine */ }
  }, [token, visible]);

  useEffect(() => {
    if (!visible) return;
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load, visible]);

  if (!visible || alerts.length === 0) return null;

  const ack = async (alert_id, side) => {
    setHiding(h => ({ ...h, [alert_id]: true }));
    try {
      await api.post("/api/manpower/alerts/ack", { alert_id, side }, token);
      load();
    } catch (e) {
      setHiding(h => ({ ...h, [alert_id]: false }));
      alert(`Ack failed: ${e.message}`);
    }
  };

  return (
    <div style={{
      background: "#fff", borderBottom: "2px solid #fecaca",
      borderTop: "2px solid #fecaca",
      padding: "10px 24px", display: "flex", flexDirection: "column", gap: 8,
      margin: "8px 0",
    }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "#991b1b", letterSpacing: ".08em", textTransform: "uppercase" }}>
        🚨 Manpower Alerts ({alerts.length}) — acknowledge to clear
      </div>
      <div style={{ display: "flex", gap: 10, overflowX: "auto", paddingBottom: 2 }}>
        {alerts.map(a => {
          const kind = a.alert_kind || a.kind;
          const meta = KIND_META[kind] || { color: "#475569", icon: "•", label: kind };
          const qAcked = !!a.ack_quality_at;
          const iAcked = !!a.ack_incharge_at;
          const fading = !!hiding[a.id];
          return (
            <div key={a.id} style={{
              minWidth: 320, maxWidth: 420,
              border: `2px solid ${meta.color}`, borderRadius: 10,
              padding: 10, background: `${meta.color}0d`,
              opacity: fading ? 0.4 : 1, transition: "opacity .2s",
              flexShrink: 0,
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <span style={{ fontSize: 12, fontWeight: 800, color: meta.color }}>
                  {meta.icon} {meta.label}
                </span>
                <span style={{ fontSize: 10, color: "#64748b" }}>
                  {a.fired_at ? new Date(a.fired_at).toLocaleString() : ""}
                </span>
              </div>
              <div style={{ fontSize: 11, color: "#334155", marginBottom: 4 }}>
                <b>Line #{a.line_id}</b> · {a.shift_date} · Shift {a.shift_name}
              </div>
              {a.process_name && (
                <div style={{ fontSize: 11, color: "#475569" }}>
                  Process: <b>{a.process_name}</b>
                </div>
              )}
              {a.operator_name && (
                <div style={{ fontSize: 11, color: "#475569" }}>
                  Operator: <b>{a.operator_name}</b>
                </div>
              )}
              {a.context_text && (
                <div style={{ fontSize: 10, color: "#64748b", marginTop: 4, fontStyle: "italic" }}>
                  {a.context_text}
                </div>
              )}
              {a.escalated_at && (
                <div style={{ fontSize: 10, color: "#7c2d12", fontWeight: 700, marginTop: 4 }}>
                  ⤴ Escalated at {new Date(a.escalated_at).toLocaleTimeString()}
                </div>
              )}
              <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                {(isAdmin || isQuality) && (
                  <button onClick={() => ack(a.id, "quality")}
                          disabled={qAcked || fading}
                          style={ackBtn(qAcked, "#ca8a04")}>
                    {qAcked
                      ? `✓ Quality acked${a.ack_quality_by ? ` (${a.ack_quality_by})` : ""}`
                      : "Ack as Quality"}
                  </button>
                )}
                {(isAdmin || isIncharge) && (
                  <button onClick={() => ack(a.id, "incharge")}
                          disabled={iAcked || fading}
                          style={ackBtn(iAcked, "#1e40af")}>
                    {iAcked
                      ? `✓ Incharge acked${a.ack_incharge_by ? ` (${a.ack_incharge_by})` : ""}`
                      : "Ack as Section Incharge"}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const ackBtn = (acked, color) => ({
  padding: "5px 10px", borderRadius: 6, border: "none",
  fontSize: 11, fontWeight: 700, cursor: acked ? "default" : "pointer",
  background: acked ? "#dcfce7" : color,
  color:      acked ? "#166534" : "#fff",
  opacity: acked ? 0.85 : 1,
});
