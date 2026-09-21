// ───────────────────────────────────────────────────────────────────────
// MyTeam.jsx   (/my-team)   2026-09-13
// ───────────────────────────────────────────────────────────────────────
// Every senior sees the JUNIORS down their leg (per the escalation chain +
// the users on their zones' lines): each one's role, how many lines they run,
// their zones, and whether they're ACTIVE (recent panel/app use) or idle past
// the Timer module's inactive-user threshold.
// Backed by /api/hierarchy/team.
// ───────────────────────────────────────────────────────────────────────
import { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import PageTopbar from "../components/PageTopbar";

const card = { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, padding: "16px 18px", marginBottom: 16, boxShadow: "0 1px 3px rgba(0,0,0,.04)" };
const th = { textAlign: "left", padding: "8px 12px", fontSize: 11, fontWeight: 700, color: "#64748b", textTransform: "uppercase", letterSpacing: ".04em", borderBottom: "1px solid #e2e8f0", whiteSpace: "nowrap" };
const td = { padding: "9px 12px", fontSize: 13, color: "#0f172a", borderBottom: "1px solid #f1f5f9" };

const ROLE_LABEL = {
  operator: "Operator", leader: "Leader", shift_incharge: "Shift Incharge",
  section_incharge: "Section Incharge", production_incharge: "Production Incharge",
  quality_incharge: "Quality Incharge", production: "Production",
  department: "Department", plant_head: "Plant Head", admin: "Admin",
};
const idleText = (s) => {
  if (s == null) return "never";
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};

export default function MyTeam() {
  const { token } = useAuth();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try { setData(await api.get("/api/hierarchy/team", token)); }
    catch { setData(null); }
    finally { setLoading(false); }
  }, [token]);
  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t); }, [load]);

  const members = data?.members || [];
  const activeN = members.filter(m => m.active).length;

  return (
    <div style={{ padding: "18px 22px 60px", color: "#0f172a" }}>
      <PageTopbar leading="My" accent="Team" />

      {loading ? <div style={{ color: "#94a3b8", padding: 16 }}>Loading…</div>
       : members.length === 0 ? (
        <div style={{ textAlign: "center", padding: "48px 16px", color: "#64748b" }}>
          <div style={{ fontSize: 40, opacity: .3, marginBottom: 12 }}>👥</div>
          <div style={{ fontSize: 15, fontWeight: 600 }}>No team members under you</div>
          <div style={{ fontSize: 12.5, color: "#94a3b8", marginTop: 6 }}>
            Juniors appear here once you're in a zone's escalation chain or lines are assigned in your zone.
          </div>
        </div>
      ) : (
        <>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", margin: "12px 0 16px" }}>
            <div style={{ ...card, margin: 0, flex: "0 0 auto", padding: "10px 16px" }}>
              <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700, textTransform: "uppercase" }}>Team</div>
              <div style={{ fontSize: 22, fontWeight: 800 }}>{members.length}</div>
            </div>
            <div style={{ ...card, margin: 0, flex: "0 0 auto", padding: "10px 16px" }}>
              <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700, textTransform: "uppercase" }}>Active now</div>
              <div style={{ fontSize: 22, fontWeight: 800, color: "#15803d" }}>{activeN}</div>
            </div>
            <div style={{ ...card, margin: 0, flex: "0 0 auto", padding: "10px 16px" }}>
              <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700, textTransform: "uppercase" }}>Inactive</div>
              <div style={{ fontSize: 22, fontWeight: 800, color: "#b91c1c" }}>{members.length - activeN}</div>
              <div style={{ fontSize: 10, color: "#94a3b8" }}>&gt; {data.inactive_hours}h idle</div>
            </div>
          </div>

          <div style={{ ...card, padding: 0, overflow: "hidden" }}>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 640 }}>
                <thead><tr>{["", "User", "Role", "Lines", "Zones", "Last active"].map((h, i) => <th key={i} style={th}>{h}</th>)}</tr></thead>
                <tbody>
                  {members.map(m => (
                    <tr key={m.id}>
                      <td style={{ ...td, width: 30 }}>
                        <span title={m.active ? "Active" : (m.never_seen ? "Never used" : "Inactive")}
                              style={{ display: "inline-block", width: 10, height: 10, borderRadius: "50%",
                                       background: m.active ? "#16a34a" : (m.never_seen ? "#cbd5e1" : "#dc2626") }} />
                      </td>
                      <td style={{ ...td, fontWeight: 700 }}>{m.username}</td>
                      <td style={td}><span style={{ fontSize: 12, color: "#475569" }}>{ROLE_LABEL[m.role] || m.role}</span></td>
                      <td style={td}>{m.role === "leader" || m.lines ? <b>{m.lines}</b> : "—"}</td>
                      <td style={{ ...td, color: "#64748b", fontSize: 12 }}>{(m.zones || []).join(", ") || "—"}</td>
                      <td style={{ ...td, color: m.active ? "#15803d" : "#94a3b8", fontWeight: m.active ? 700 : 500 }}>
                        {idleText(m.idle_seconds)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div style={{ fontSize: 11.5, color: "#94a3b8", marginTop: 10 }}>
            🟢 active · 🔴 idle &gt; {data.inactive_hours}h · ⚪ never used. Threshold is set in Admin → Production → Timer / Alerts.
          </div>
        </>
      )}
    </div>
  );
}
