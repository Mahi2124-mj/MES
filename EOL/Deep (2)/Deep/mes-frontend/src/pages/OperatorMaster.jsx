// ───────────────────────────────────────────────────────────────────────
// OperatorMaster.jsx   (/operators)   2026-09-13
// ───────────────────────────────────────────────────────────────────────
// Standalone, authority-based page (Admin → Users → Access grants it) with two
// modules:
//   • Operators        — badge master + per-shift productivity (existing).
//   • Leader Allocation — assign leaders to lines + capability factor. Shown
//                         only to shift-incharge & above.
// canWrite("operators") drives view vs edit on the Operators module.
// ───────────────────────────────────────────────────────────────────────
import { useState } from "react";
import PageTopbar from "../components/PageTopbar";
import { useAuth } from "../context/AuthContext";
import { OperatorsPage, ADMIN_PANEL_CSS } from "./AdminPanel";
import LeaderAllocation from "../components/LeaderAllocation";

const LEADER_ROLES = ["admin", "plant_head", "section_incharge",
                      "shift_incharge", "production_incharge"];

export default function OperatorMaster() {
  const { canWrite, user } = useAuth();
  const readOnly = !canWrite("operators");
  const showLeaders = LEADER_ROLES.includes(user?.role);
  const [tab, setTab] = useState("operators");
  const [toast, setToast] = useState(null);
  const showToast = (m, kind) => { setToast({ m, kind }); setTimeout(() => setToast(null), 3000); };

  const tabs = [{ key: "operators", label: "Operators" }];
  if (showLeaders) tabs.push({ key: "leaders", label: "Leaders" });
  const active = tabs.some(t => t.key === tab) ? tab : "operators";

  return (
    <div style={{ padding: "18px 22px 60px", color: "#0f172a" }}>
      <style>{ADMIN_PANEL_CSS}</style>
      <PageTopbar leading="Employee" accent="Master" />

      {toast && (
        <div style={{ position: "fixed", top: 16, right: 16, zIndex: 6000,
                      background: toast.kind === "err" ? "#b91c1c" : "#0f172a",
                      color: "#fff", padding: "10px 16px", borderRadius: 10,
                      fontSize: 13, fontWeight: 600 }}>{toast.m}</div>
      )}

      {showLeaders && (
        <div style={{ display: "flex", gap: 6, margin: "12px 0 16px", borderBottom: "2px solid #e2e8f0" }}>
          {tabs.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)}
                    style={{ padding: "9px 18px", border: "none", background: "none", cursor: "pointer",
                             fontSize: 13.5, fontWeight: active === t.key ? 800 : 600,
                             color: active === t.key ? "#1e40af" : "#94a3b8",
                             borderBottom: active === t.key ? "3px solid #1e40af" : "3px solid transparent",
                             marginBottom: -2 }}>
              {t.label}
            </button>
          ))}
        </div>
      )}

      {active === "leaders" ? (
        <LeaderAllocation />
      ) : (
        <>
          {readOnly && (
            <div style={{ margin: "10px 0", fontSize: 12.5, color: "#a16207",
                          background: "#fef3c7", border: "1px solid #fde68a",
                          borderRadius: 8, padding: "8px 12px", fontWeight: 600 }}>
              Read-only access — you can view operators but not add or edit.
            </div>
          )}
          <div style={{ marginTop: 4 }}>
            <OperatorsPage toast={showToast} readOnly={readOnly} />
          </div>
        </>
      )}
    </div>
  );
}
