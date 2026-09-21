// ───────────────────────────────────────────────────────────────────────
// MyEscalations.jsx   (/my-escalations)  →  INBOX  (2026-09-15)
// ───────────────────────────────────────────────────────────────────────
// Reworked from the old NG-escalation list into a NOTIFICATIONS INBOX: alerts,
// notifications and reminders for THIS user — OEE drop, manpower allocation
// pending, loss line continuously, shift compile successful, etc. Each row is a
// mes_push_inbox entry (see routers/push.py). NG shift-end escalations are
// deliberately NOT shown here (operator's call). Backed by /api/push/inbox.
//
// Phase 1 = this inbox UI + list/read endpoints. Phase 2 wires each alert source
// (OEE / manpower / loss-line / shift-compile) to write into the inbox.
// ───────────────────────────────────────────────────────────────────────
import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import PageTopbar from "../components/PageTopbar";
import { pushSupported, enablePush, disablePush, pushIsOn, testPush } from "../api/push";

const btnPrimary = { background: "#1e40af", color: "#fff", border: "none", borderRadius: 8, padding: "8px 14px", fontSize: 13, fontWeight: 700, cursor: "pointer" };
const btnGhost   = { background: "#fff", color: "#334155", border: "1px solid #cbd5e1", borderRadius: 8, padding: "7px 12px", fontSize: 12.5, fontWeight: 600, cursor: "pointer" };

// Per notification-type look (matches the `tag` stored by send_to_user()).
const TAG_META = {
  oee_drop:         { icon: "📉", label: "OEE Drop",          color: "#dc2626" },
  manpower_pending: { icon: "👥", label: "Manpower Pending",  color: "#d97706" },
  loss_line:        { icon: "⏳", label: "Line Loss",         color: "#dc2626" },
  shift_compile:    { icon: "📋", label: "Shift Compile",     color: "#16a34a" },
  deviation:        { icon: "📝", label: "Deviation",         color: "#7c3aed" },
  reminder:         { icon: "⏰", label: "Reminder",          color: "#2563eb" },
  test:             { icon: "🔔", label: "Test",              color: "#64748b" },
};
const metaFor = (tag) => TAG_META[tag] || { icon: "🔔", label: (tag || "Alert"), color: "#2563eb" };

function relTime(ts) {
  if (!ts) return "";
  const s = Math.max(0, (Date.now() - ts) / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60); if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60); if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24); if (d < 7) return `${d}d ago`;
  return new Date(ts).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

export default function MyEscalations() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [items, setItems] = useState([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLd]  = useState(true);
  const [toast, setToast] = useState(null);
  const [pushOn, setPushOn] = useState(pushIsOn());
  const [pushBusy, setPushBusy] = useState(false);
  const nativeApp = !!(typeof window !== "undefined" && window.Capacitor &&
                       window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform());
  const notifOn = nativeApp || pushOn;

  const flash = (m, ms = 3500) => { setToast(m); setTimeout(() => setToast(null), ms); };
  const doEnablePush = async () => {
    setPushBusy(true);
    try { await enablePush(token); setPushOn(true); flash("Notifications enabled — alerts will reach your phone."); }
    catch (e) { flash("Could not enable notifications: " + (e.message || "error"), 5000); }
    finally { setPushBusy(false); }
  };
  const doDisablePush = async () => {
    setPushBusy(true);
    try { await disablePush(token); setPushOn(false); flash("Notifications disabled."); }
    finally { setPushBusy(false); }
  };
  const doTestPush = async () => {
    setPushBusy(true);
    try { const r = await testPush(token); flash(nativeApp ? "Test notification sent — arrives in a few seconds." : (r.sent_to ? "Test notification sent." : "No active device — enable notifications first.")); await load(); }
    catch (e) { flash("Test failed: " + (e.message || "error"), 5000); }
    finally { setPushBusy(false); }
  };

  const load = useCallback(async () => {
    try {
      const r = await api.get("/api/push/inbox", token);
      setItems(Array.isArray(r?.items) ? r.items : []);
      setUnread(r?.unread || 0);
    } catch { /* keep last */ }
    finally { setLd(false); }
  }, [token]);
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [load]);

  const markRead = async (ids) => {
    try { await api.post("/api/push/inbox/read", { ids }, token); } catch { /* ignore */ }
  };
  const markAllRead = async () => {
    setItems(its => its.map(i => ({ ...i, is_read: true }))); setUnread(0);
    try { await api.post("/api/push/inbox/read", { all: true }, token); } catch { /* ignore */ }
  };
  const openItem = async (it) => {
    if (!it.is_read) {
      setItems(its => its.map(x => (x.id === it.id ? { ...x, is_read: true } : x)));
      setUnread(u => Math.max(0, u - 1));
      markRead([it.id]);
    }
    if (it.url && it.url !== "/my-escalations") {
      try { navigate(it.url); } catch { window.location.assign(it.url); }
    }
  };

  return (
    <div style={{ padding: "18px 22px 60px", color: "#0f172a" }}>
      <PageTopbar leading="Alerts" accent="Inbox" />

      {toast && <div style={{ position: "fixed", top: 16, right: 16, zIndex: 6000, background: "#0f172a", color: "#fff", padding: "10px 16px", borderRadius: 10, fontSize: 13, fontWeight: 600 }}>{toast}</div>}

      {/* Notification controls */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
                    background: notifOn ? "#f0fdf4" : "#fff7ed",
                    border: `1px solid ${notifOn ? "#bbf7d0" : "#fed7aa"}`,
                    borderRadius: 10, padding: "10px 14px", marginTop: 12 }}>
        <span style={{ fontSize: 18 }}>{notifOn ? "🔔" : "🔕"}</span>
        <div style={{ flex: 1, minWidth: 180 }}>
          <div style={{ fontSize: 13.5, fontWeight: 700 }}>{notifOn ? "Notifications ON" : "Notifications OFF"}</div>
          <div style={{ fontSize: 11.5, color: "#64748b" }}>
            {nativeApp
              ? "Alerts reach your phone even when the app is closed."
              : (pushSupported()
                  ? "Get a phone notification with vibration for new alerts."
                  : "Notifications aren't supported on this device — open it in the APK or Chrome.")}
          </div>
        </div>
        {nativeApp ? (
          <button disabled={pushBusy} onClick={doTestPush} style={btnGhost}>Test</button>
        ) : (pushSupported() && (pushOn ? (
          <>
            <button disabled={pushBusy} onClick={doTestPush} style={btnGhost}>Test</button>
            <button disabled={pushBusy} onClick={doDisablePush} style={btnGhost}>Turn off</button>
          </>
        ) : (
          <button disabled={pushBusy} onClick={doEnablePush} style={btnPrimary}>
            {pushBusy ? "…" : "Enable notifications"}
          </button>
        )))}
      </div>

      {/* Inbox header row */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "16px 2px 8px" }}>
        <div style={{ fontSize: 14, fontWeight: 800 }}>
          Inbox {unread > 0 && <span style={{ background: "#dc2626", color: "#fff", borderRadius: 999, fontSize: 11, fontWeight: 800, padding: "1px 8px", marginLeft: 4 }}>{unread} new</span>}
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button onClick={load} style={btnGhost}>↻ Refresh</button>
          {unread > 0 && <button onClick={markAllRead} style={btnGhost}>Mark all read</button>}
        </div>
      </div>

      {loading ? <div style={{ color: "#94a3b8", padding: 16 }}>Loading…</div>
       : items.length === 0 ? (
        <div style={{ textAlign: "center", padding: "48px 16px", color: "#64748b" }}>
          <div style={{ fontSize: 40, opacity: .3, marginBottom: 12 }}>📭</div>
          <div style={{ fontSize: 15, fontWeight: 600 }}>No alerts yet</div>
          <div style={{ fontSize: 12.5, color: "#94a3b8", marginTop: 6 }}>
            OEE drop, manpower pending, line loss and shift-compile alerts will show up here.
          </div>
        </div>
      ) : (
        <div style={{ display: "grid", gap: 8 }}>
          {items.map(it => {
            const m = metaFor(it.tag);
            const clickable = it.url && it.url !== "/my-escalations";
            return (
              <div key={it.id} onClick={() => openItem(it)}
                   style={{ display: "flex", gap: 12, alignItems: "flex-start",
                            background: it.is_read ? "#fff" : "#eff6ff",
                            border: "1px solid " + (it.is_read ? "#e2e8f0" : "#bfdbfe"),
                            borderLeft: `4px solid ${m.color}`,
                            borderRadius: 10, padding: "11px 14px",
                            cursor: clickable ? "pointer" : "default" }}>
                <div style={{ fontSize: 20, lineHeight: 1 }}>{m.icon}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <span style={{ fontSize: 10.5, fontWeight: 800, color: m.color, textTransform: "uppercase", letterSpacing: ".04em",
                                   background: m.color + "14", borderRadius: 5, padding: "1px 7px" }}>{m.label}</span>
                    {!it.is_read && <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#2563eb", display: "inline-block" }} />}
                    <span style={{ marginLeft: "auto", fontSize: 11, color: "#94a3b8", whiteSpace: "nowrap" }}>{relTime(it.ts)}</span>
                  </div>
                  <div style={{ fontSize: 13.5, fontWeight: 700, marginTop: 3, color: "#0f172a" }}>{it.title || m.label}</div>
                  {it.body && <div style={{ fontSize: 12.5, color: "#475569", marginTop: 2, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{it.body}</div>}
                  {clickable && <div style={{ fontSize: 11.5, color: "#2563eb", fontWeight: 700, marginTop: 4 }}>Open →</div>}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
