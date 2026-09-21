// ───────────────────────────────────────────────────────────────────────
// TimerConfigPage.jsx   (Admin → Production → Timer / Alerts)   2026-09-13
// ───────────────────────────────────────────────────────────────────────
// One screen to set the plant's time-based thresholds:
//   • Inactive-user threshold (hours) — drives the hierarchy "active/inactive".
//   • OEE-drop alarm (sustain / cooldown minutes) — global default.
//   • Shift Compile close window (minutes after scheduled end still "on time").
//   • Manpower-not-allocated alert delay — PER hierarchy level.
// Backed by /api/timer-config (GET/PUT).  Admin writes; others read-only.
// ───────────────────────────────────────────────────────────────────────
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const card = { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, padding: "18px 20px", marginBottom: 16, boxShadow: "0 1px 3px rgba(0,0,0,.04)" };
const lbl  = { fontSize: 12.5, fontWeight: 700, color: "#334155", marginBottom: 4 };
const hint = { fontSize: 11, color: "#94a3b8", marginTop: 3 };
const inp  = { width: 130, padding: "8px 10px", border: "1px solid #cbd5e1", borderRadius: 8, fontSize: 13, background: "#fff", color: "#0f172a" };
const sec  = { fontSize: 13, fontWeight: 800, color: "#0f172a", marginBottom: 12, display: "flex", alignItems: "center", gap: 8 };

const MP_LEVELS = [
  { key: "leader",           label: "Leader" },
  { key: "shift_incharge",   label: "Shift Incharge" },
  { key: "section_incharge", label: "Section Incharge" },
  { key: "plant_head",       label: "Plant Head" },
];

export default function TimerConfigPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [cfg, setCfg]   = useState(null);
  const [mp, setMp]     = useState({});
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const c = await api.get("/api/timer-config", token);
      setCfg(c);
      setMp(c.manpower_alert_minutes || {});
    } catch (e) { toast?.("Failed to load timer config", "err"); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  const setField = (k, v) => setCfg(c => ({ ...c, [k]: v }));
  const num = (v) => (v === "" || v == null ? "" : Number(v));

  const save = async () => {
    if (readOnly) return;
    setSaving(true);
    try {
      const body = {
        inactive_user_hours:        num(cfg.inactive_user_hours),
        oee_sustain_minutes:        num(cfg.oee_sustain_minutes),
        oee_cooldown_minutes:       num(cfg.oee_cooldown_minutes),
        shift_close_window_minutes: num(cfg.shift_close_window_minutes),
        manpower_alert_minutes:     Object.fromEntries(
          Object.entries(mp).map(([k, v]) => [k, Number(v) || 0])),
      };
      const r = await api.put("/api/timer-config", body, token);
      setCfg(r); setMp(r.manpower_alert_minutes || {});
      toast?.("Timer / alert settings saved ✓");
    } catch (e) { toast?.(e.message || "Save failed", "err"); }
    finally { setSaving(false); }
  };

  if (!cfg) return <div style={{ padding: 20, color: "#94a3b8" }}>Loading…</div>;

  const F = ({ k, label, hintText, min = 0 }) => (
    <div style={{ marginBottom: 14 }}>
      <div style={lbl}>{label}</div>
      <input type="number" min={min} disabled={readOnly} value={cfg[k] ?? ""}
             onChange={e => setField(k, e.target.value)} style={inp} />
      {hintText && <div style={hint}>{hintText}</div>}
    </div>
  );

  return (
    <div style={{ maxWidth: 760 }}>
      <div style={card}>
        <div style={sec}>⏲ User Activeness</div>
        <F k="inactive_user_hours" label="Inactive user threshold (hours)"
           hintText="If a user has not used the panel/app for this many hours, they are flagged INACTIVE in the hierarchy view." />
      </div>

      <div style={card}>
        <div style={sec}>📉 OEE Drop Alarm (global default)</div>
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
          <F k="oee_sustain_minutes" label="Sustain (minutes)"
             hintText="Fire the alarm if OEE stays below the threshold for this long." />
          <F k="oee_cooldown_minutes" label="Cooldown (minutes)"
             hintText="Do not repeat an alarm for this long after one fires." />
        </div>
        <div style={hint}>Per-line settings (OEE Drop Alarm tab) override this.</div>
      </div>

      <div style={card}>
        <div style={sec}>🗂 Shift Compile</div>
        <F k="shift_close_window_minutes" label="Close window (minutes after shift end)"
           hintText="Closing within this many minutes after the shift end still counts as on-time." />
      </div>

      <div style={card}>
        <div style={sec}>👥 Manpower not-allocated alert — per level</div>
        <div style={hint} >If a shift's manpower is not allocated, alert this level after this many minutes.</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(170px,1fr))", gap: 14, marginTop: 12 }}>
          {MP_LEVELS.map(l => (
            <div key={l.key}>
              <div style={lbl}>{l.label} (min)</div>
              <input type="number" min={0} disabled={readOnly}
                     value={mp[l.key] ?? ""} onChange={e => setMp(m => ({ ...m, [l.key]: e.target.value }))}
                     style={{ ...inp, width: "100%" }} />
            </div>
          ))}
        </div>
      </div>

      {!readOnly && (
        <button onClick={save} disabled={saving}
                style={{ background: "#1e40af", color: "#fff", border: "none", borderRadius: 10, padding: "11px 26px", fontSize: 14, fontWeight: 700, cursor: "pointer", opacity: saving ? .6 : 1 }}>
          {saving ? "Saving…" : "Save settings"}
        </button>
      )}
      {cfg.updated_by && <div style={{ ...hint, marginTop: 10 }}>Last saved by {cfg.updated_by}{cfg.updated_at ? ` · ${new Date(cfg.updated_at).toLocaleString()}` : ""}</div>}
    </div>
  );
}
