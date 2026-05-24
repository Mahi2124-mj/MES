import { useState, useEffect, useRef, useCallback } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import AIAssistant   from "../components/AIAssistant";
import OperatorBadge from "../components/OperatorBadge";
import ManpowerAlertBanner from "../components/ManpowerAlertBanner";
import { ClosureFormModal } from "./MaintenanceDashboard";

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function fmtSec(s) {
  if (!s) return "00:00:00";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return [h, m, sec].map(v => String(v).padStart(2, "0")).join(":");
}
function fmtPct(v) { return `${(v || 0).toFixed(1)}%`; }

function SectionTitle({ children }) {
  return (
    <div style={{
      fontSize: 11, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase",
      color: "#64748b", marginBottom: 14, display: "flex", alignItems: "center", gap: 8,
    }}>
      {children}
      <div style={{ flex: 1, height: 1, background: "#e2e8f0" }} />
    </div>
  );
}

function Btn({ children, onClick, variant = "ghost" }) {
  const [h, setH] = useState(false);
  const base = { flex: 1, padding: "9px 0", borderRadius: 8, cursor: "pointer", fontSize: 12, fontWeight: 600, transition: "all 0.12s", border: "none" };
  const styles = variant === "primary"
    ? { ...base, background: h ? "#1d3fa8" : "linear-gradient(135deg,#1e40af,#2563eb)", color: "#fff", boxShadow: "0 2px 8px rgba(30,64,175,0.3)", transform: h ? "translateY(-1px)" : "none" }
    : { ...base, background: h ? "rgba(30,64,175,0.05)" : "#f8fafc", border: "1px solid " + (h ? "#3b82f6" : "#e2e8f0"), color: h ? "#1e40af" : "#334155" };
  return (
    <button style={styles} onClick={onClick} onMouseEnter={() => setH(true)} onMouseLeave={() => setH(false)}>
      {children}
    </button>
  );
}

// ─── Detail Modal ─────────────────────────────────────────────────────────────
function DetailModal({ line, rt, lineDetail, onClose }) {
  const LOSS_CATS = [
    { key: "breakdown",   label: "Breakdown",   color: "#ef4444" },
    { key: "quality",     label: "Quality",     color: "#f97316" },
    { key: "material",    label: "Material",    color: "#eab308" },
    { key: "setup",       label: "Setup",       color: "#84cc16" },
    { key: "change_over", label: "Change Over", color: "#06b6d4" },
    { key: "speed",       label: "Speed Loss",  color: "#3b82f6" },
    { key: "others",      label: "Others",      color: "#8b5cf6" },
  ];

  if (!rt || !line) return null;

  const actual   = (rt.ok_count || 0) + (rt.ng_count || 0);
  const plan     = rt.shift_plan_completed || 0;
  const planPct  = plan ? Math.min(100, (actual / plan) * 100) : 0;
  const oee      = rt.overall_oee   || 0;
  const avail    = rt.availability  || 0;
  const perf     = rt.performance   || 0;
  const qual     = rt.quality_oee   || 0;
  const shift    = rt.shift_name    || "A";
  const idealCT  = lineDetail?.plc_config?.ideal_cycle_time || 15;
  const avgCT    = rt.ct_avg_20     || 0;

  let totalLoss = 0;
  const lossData = LOSS_CATS.map(c => {
    const sec = rt[`loss_${c.key}_seconds`] || 0;
    totalLoss += sec;
    return { ...c, sec };
  }).filter(c => c.sec > 0).map(c => ({
    ...c, pct: totalLoss ? ((c.sec / totalLoss) * 100).toFixed(1) : 0,
  }));

  const slots = (lineDetail?.hourly_slots || [])
    .filter(s => s.shift_name === shift)
    .sort((a, b) => a.start_time.localeCompare(b.start_time))
    .map(s => {
      const p = s.db_column_prefix;
      return {
        label:    s.slot_label,
        plan:     rt[`${p}_plan`]     || 0,
        actual:   rt[`${p}_actual`]   || 0,
        variance: rt[`${p}_variance`] || 0,
        eff: (rt[`${p}_plan`] || 0)
          ? ((rt[`${p}_actual`] || 0) / rt[`${p}_plan`] * 100).toFixed(1)
          : "0",
      };
    });

  useEffect(() => {
    const h = e => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, [onClose]);

  return (
    <div
      onClick={e => e.target === e.currentTarget && onClose()}
      style={{
        position: "fixed", inset: 0,
        background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)",
        zIndex: 500, display: "flex", alignItems: "flex-start", justifyContent: "center",
        padding: "40px 16px", overflowY: "auto",
        animation: "fadeIn .18s ease",
      }}
    >
      <style>{`
        @keyframes fadeIn  { from { opacity:0 } to { opacity:1 } }
        @keyframes slideUp { from { transform:translateY(20px);opacity:0 } to { transform:none;opacity:1 } }
      `}</style>
      <div style={{
        background: "#fff", borderRadius: 16, width: "100%", maxWidth: 820,
        boxShadow: "0 24px 80px rgba(0,0,0,0.25)",
        animation: "slideUp .25s cubic-bezier(0.16,1,0.3,1)",
        overflow: "hidden", marginBottom: 40,
      }}>
        <div style={{
          padding: "20px 24px", borderBottom: "1px solid #e2e8f0",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          background: "linear-gradient(135deg,#1e40af,#2563eb)",
        }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "#fff" }}>📊 {line.line_name}</div>
            <div style={{ fontSize: 11, color: "rgba(255,255,255,0.65)", marginTop: 2 }}>
              {line.line_code} · Shift {shift} · {rt.record_date || "—"}
            </div>
          </div>
          <button onClick={onClose} style={{
            width: 32, height: 32, borderRadius: 8,
            background: "rgba(255,255,255,0.15)", border: "1px solid rgba(255,255,255,0.25)",
            color: "#fff", fontSize: 16, cursor: "pointer",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>✕</button>
        </div>

        <div style={{ padding: 24 }}>
          <SectionTitle>OEE Components</SectionTitle>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12, marginBottom: 24 }}>
            {[
              { label: "Overall OEE",  val: oee,   color: "#1e40af" },
              { label: "Availability", val: avail, color: "#16a34a" },
              { label: "Performance",  val: perf,  color: "#d97706" },
              { label: "Quality",      val: qual,  color: "#8b5cf6" },
            ].map(({ label, val, color }) => (
              <div key={label} style={{
                background: "#f8fafc", border: "1px solid #e2e8f0",
                borderRadius: 10, padding: 16, textAlign: "center",
                borderTop: `3px solid ${color}`,
              }}>
                <div style={{ fontSize: 28, fontWeight: 700, color }}>{fmtPct(val)}</div>
                <div style={{ fontSize: 11, color: "#64748b", marginTop: 4 }}>{label}</div>
              </div>
            ))}
          </div>

          <SectionTitle>Plan vs Actual</SectionTitle>
          <div style={{ display: "flex", gap: 16, marginBottom: 24 }}>
            <div style={{ background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: 10, padding: 16, minWidth: 100 }}>
              <div style={{ fontSize: 32, fontWeight: 700, color: "#0f172a" }}>{actual}</div>
              <div style={{ fontSize: 12, color: "#64748b" }}>Actual Produced</div>
            </div>
            <div style={{ background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: 10, padding: 16, minWidth: 100 }}>
              <div style={{ fontSize: 32, fontWeight: 700, color: "#0f172a" }}>{plan}</div>
              <div style={{ fontSize: 12, color: "#64748b" }}>Plan Target</div>
            </div>
            <div style={{ flex: 1, background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: 10, padding: 16, display: "flex", flexDirection: "column", justifyContent: "center" }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "#64748b", marginBottom: 8 }}>
                <span>Achievement</span>
                <strong style={{ color: "#0f172a" }}>{planPct.toFixed(1)}%</strong>
              </div>
              <div style={{ background: "#e2e8f0", borderRadius: 6, height: 12, overflow: "hidden" }}>
                <div style={{ width: `${planPct}%`, height: "100%", borderRadius: 6, background: "linear-gradient(90deg,#16a34a,#22c55e)", transition: "width .4s ease" }} />
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                {[`Ideal CT: ${idealCT.toFixed(1)}s`, `Avg CT: ${avgCT.toFixed(1)}s`].map(t => (
                  <span key={t} style={{ padding: "3px 10px", borderRadius: 99, background: "#f1f5f9", border: "1px solid #e2e8f0", fontSize: 11, color: "#334155" }}>{t}</span>
                ))}
              </div>
            </div>
          </div>

          <SectionTitle>Loss Breakdown · Total {fmtSec(totalLoss)}</SectionTitle>
          <div style={{ marginBottom: 24 }}>
            {lossData.length === 0
              ? <div style={{ color: "#94a3b8", fontSize: 13, padding: "12px 0" }}>No losses recorded yet</div>
              : lossData.map(c => (
                <div key={c.key} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 0", borderBottom: "1px solid #f1f5f9" }}>
                  <div style={{ width: 8, height: 8, borderRadius: "50%", background: c.color, flexShrink: 0 }} />
                  <div style={{ width: 90, fontWeight: 600, fontSize: 12, color: c.color }}>{c.label}</div>
                  <div style={{ flex: 1, height: 6, background: "#f1f5f9", borderRadius: 3, overflow: "hidden" }}>
                    <div style={{ width: `${c.pct}%`, height: "100%", background: c.color, borderRadius: 3 }} />
                  </div>
                  <div style={{ fontFamily: "monospace", fontSize: 11, color: "#64748b", width: 68, textAlign: "right" }}>{fmtSec(c.sec)}</div>
                  <div style={{ fontSize: 11, fontWeight: 700, color: "#0f172a", width: 36, textAlign: "right" }}>{c.pct}%</div>
                </div>
              ))
            }
          </div>

          <SectionTitle>Hourly Slot Performance — Shift {shift}</SectionTitle>
          {slots.length === 0
            ? <div style={{ color: "#94a3b8", fontSize: 13 }}>No slot data available</div>
            : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr>
                    {["Slot", "Plan", "Actual", "Variance", "Efficiency"].map(h => (
                      <th key={h} style={{ padding: "8px 12px", textAlign: "left", fontSize: 10, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: "#64748b", borderBottom: "2px solid #e2e8f0" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {slots.map(s => (
                    <tr key={s.label} style={{ borderBottom: "1px solid #f1f5f9" }}>
                      <td style={{ padding: "10px 12px", fontFamily: "monospace", fontWeight: 600, color: "#0f172a" }}>{s.label}</td>
                      <td style={{ padding: "10px 12px", fontFamily: "monospace", color: "#334155" }}>{s.plan}</td>
                      <td style={{ padding: "10px 12px", fontFamily: "monospace", color: "#334155" }}>{s.actual}</td>
                      <td style={{ padding: "10px 12px", fontFamily: "monospace", color: s.variance < 0 ? "#dc2626" : s.variance > 0 ? "#16a34a" : "#64748b" }}>
                        {s.variance > 0 ? "+" : ""}{s.variance}
                      </td>
                      <td style={{ padding: "10px 12px", fontWeight: 700, color: parseFloat(s.eff) >= 90 ? "#16a34a" : parseFloat(s.eff) >= 70 ? "#d97706" : "#dc2626" }}>
                        {s.eff}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          }
        </div>
      </div>
    </div>
  );
}

// ─── Line Card ────────────────────────────────────────────────────────────────
// 2026-05-18 — sessionStorage key for caching the sub-machine list per line.
// Fixes the "page reloads → 2-second flicker showing only the main card before
// /api/lines/{id}/submachines comes back" bug.  The cached list is rendered
// immediately, then replaced when the live fetch resolves.
const SUBS_CACHE_KEY = (lineId) => `mes:submachines:line:${lineId}`;
function _readSubsCache(lineId) {
  try {
    const raw = sessionStorage.getItem(SUBS_CACHE_KEY(lineId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch { return []; }
}
function _writeSubsCache(lineId, list) {
  try { sessionStorage.setItem(SUBS_CACHE_KEY(lineId), JSON.stringify(list || [])); }
  catch { /* quota / disabled — silently skip */ }
}

function LineCard({ line, globalStatus, token, user, onRtUpdate, onNpdUpdate }) {
  const [rt, setRt]           = useState(null);
  const [detail, setDetail]   = useState(null);
  const [showModal, setModal] = useState(false);
  const [hovered, setHovered] = useState(false);
  // Seed sub-machines from session cache so the card lays out correctly
  // on the very first paint — eliminates the reload flicker.
  const [submachines, setSubmachines] = useState(() => _readSubsCache(line.id));
  const timerRef              = useRef(null);

  // Non-production day state
  const [npdEntries,    setNpdEntries]    = useState([]);
  const [npdLoading,    setNpdLoading]    = useState(false);
  const [showNpdForm,   setShowNpdForm]   = useState(false);
  const [npdStep,       setNpdStep]       = useState(1);
  const [npdFormDate,   setNpdFormDate]   = useState(todayISO());
  const [npdFormShift,  setNpdFormShift]  = useState("");
  const [npdFormSlots,  setNpdFormSlots]  = useState([]);
  const [npdFormReason, setNpdFormReason] = useState("");

  // OT switch state
  const [showOtMenu,  setShowOtMenu]  = useState(false);
  const [otMenuPos,   setOtMenuPos]   = useState({ top: 0, left: 0 });
  const [otLoading,   setOtLoading]   = useState(false);
  const otBtnRef = useRef(null);

  // Close OT menu on outside click / escape
  useEffect(() => {
    if (!showOtMenu) return;
    const onClick = e => {
      if (otBtnRef.current && !otBtnRef.current.contains(e.target)) {
        const pop = document.getElementById("ot-menu-popover");
        if (!pop || !pop.contains(e.target)) setShowOtMenu(false);
      }
    };
    const onEsc = e => { if (e.key === "Escape") setShowOtMenu(false); };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown",   onEsc);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown",   onEsc);
    };
  }, [showOtMenu]);

  const [now, setNow] = useState(new Date());
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []);

  const canToggleNPD = user && (user.role === "admin" || user.role === "zone");
  const canToggleOT  = user && (user.role === "admin" || user.role === "zone");

  // 2026-05-13 — OT toggle felt broken because the dashboard's rt poll
  // is on a 3 s timer.  After clicking "Shift A OT" the API call would
  // succeed but the UI wouldn't reflect the change for up to 3 s,
  // making operator think nothing happened.  Fix:
  //   1. Optimistically patch `rt.ot_active_shift` so the ⏱ OT chip
  //      flips green within ~50 ms.
  //   2. Trigger an immediate fetchData() so the authoritative server
  //      value lands shortly after and reconciles any stale fields.
  //   3. Replace silent failure with an alert that names the shift so
  //      a 403 / 500 / network error is unmistakeable.
  const activateOt = async (shiftName) => {
    if (!line?.id) {
      alert("Cannot activate OT — line id missing.  Reload the page.");
      return;
    }
    setOtLoading(true);
    try {
      await api.put(`/api/lines/${line.id}/ot-active`, { shift: shiftName }, token);
      setRt(prev => prev ? { ...prev, ot_active_shift: shiftName } : prev);
      setShowOtMenu(false);
      // Reconcile with the server's authoritative state on the very
      // next tick — covers the case where backend rejected the change
      // (e.g. role race) but still returned 200 somehow.
      fetchData();
    } catch (e) {
      alert(`OT activation failed for Shift ${shiftName}: ${e.message}`);
    } finally {
      setOtLoading(false);
    }
  };

  const deactivateOt = async () => {
    if (!line?.id) {
      alert("Cannot deactivate OT — line id missing.");
      return;
    }
    setOtLoading(true);
    try {
      await api.put(`/api/lines/${line.id}/ot-active`, { shift: null }, token);
      setRt(prev => prev ? { ...prev, ot_active_shift: null } : prev);
      setShowOtMenu(false);
      fetchData();
    } catch (e) {
      alert(`OT deactivation failed: ${e.message}`);
    } finally {
      setOtLoading(false);
    }
  };

  const fetchNPD = useCallback(async () => {
    try {
      const res = await api.get(`/api/npd/?line_id=${line.id}&date=${todayISO()}`, token);
      const entries = Array.isArray(res) ? res : [];
      setNpdEntries(entries);
      // Report NPD status up to Dashboard for summary tile
      if (onNpdUpdate) {
        const active = entries.some(e => !e.shift_name);
        onNpdUpdate(line.id, active, entries);
      }
    } catch { }
  }, [line.id, token, onNpdUpdate]);

  const fetchData = useCallback(async () => {
    try {
      const rtData = await api.get(`/api/lines/${line.id}/realtime`, token);
      setRt(rtData);
      // Use collector_status from rt as the authoritative value.
      // Fall back to line.collector_status only when rt has nothing.
      const cs = rtData.collector_status ?? line.collector_status ?? "stopped";
      if (onRtUpdate) onRtUpdate(line.id, rtData, cs);
    } catch { }
  }, [line.id, line.collector_status, token, onRtUpdate]);

  const fetchDetail = useCallback(async () => {
    try { setDetail(await api.get(`/api/lines/${line.id}`, token)); } catch { }
  }, [line.id, token]);

  const fetchSubmachines = useCallback(async () => {
    try {
      const res = await api.get(`/api/lines/${line.id}/submachines`, token);
      const list = Array.isArray(res) ? res : [];
      setSubmachines(list);
      // Persist for next reload so layout shows correct sub-count instantly.
      _writeSubsCache(line.id, list);
    } catch { /* endpoint not deployed yet or no sub-machines → silent */ }
  }, [line.id, token]);

  useEffect(() => {
    fetchData();
    fetchDetail();
    fetchNPD();
    fetchSubmachines();
    timerRef.current = setInterval(fetchData, 3000);
    // Sub-machine aggregates refresh every 10 s — tile just shows
    // today_count and avg CT; no need for 5 s polling when every card
    // on the dashboard does it in parallel.
    const subT = setInterval(fetchSubmachines, 10000);
    return () => { clearInterval(timerRef.current); clearInterval(subT); };
  }, [fetchData, fetchDetail, fetchNPD, fetchSubmachines]);

  const openNpdForm = () => {
    setNpdFormDate(todayISO());
    setNpdFormShift("");
    setNpdFormSlots([]);
    setNpdFormReason("");
    setNpdStep(1);
    setShowNpdForm(true);
  };

  const markNPD = async () => {
    setNpdLoading(true);
    try {
      const slots = npdFormSlots.length > 0 ? npdFormSlots : null;
      await api.post("/api/npd/", {
        line_id:      line.id,
        date:         npdFormDate,
        shift_name:   npdFormShift || null,
        hourly_slots: slots,
        reason:       npdFormReason || null,
      }, token);
      setShowNpdForm(false);
      fetchNPD();
    } catch (e) { alert(e.message); }
    finally { setNpdLoading(false); }
  };

  const unmarkNpdEntry = async (id) => {
    if (!confirm("Remove this non-production entry?")) return;
    setNpdLoading(true);
    try {
      await api.delete(`/api/npd/${id}`, token);
      fetchNPD();
    } catch (e) { alert(e.message); }
    finally { setNpdLoading(false); }
  };

  // Use rt when available (authoritative), fall back to line (initial load value)
  // This prevents the card from flickering between states
  const isRunning = rt
    ? rt.collector_status === "running"
    : line.collector_status === "running";

  const activeShiftsList = line.active_shifts
    ? line.active_shifts.split(",").map(s => s.trim())
    : ["A", "B"];
  const shift         = rt?.shift_name || "—";
  const isOfflineShift = rt && shift !== "—" && !activeShiftsList.includes(shift);

  const isNPD   = npdEntries.some(e => !e.shift_name || e.shift_name === shift);
  const npdEntry = npdEntries.find(e => !e.shift_name || e.shift_name === shift) || null;

  const actual  = rt ? (rt.ok_count || 0) + (rt.ng_count || 0) : 0;
  const idealCT = detail?.plc_config?.ideal_cycle_time || 15;
  const shiftCfgD = (detail?.shifts || []).find(s => s.shift_name === shift);

  // Clock-based plan — updates every second, no jump on API poll
  const plan = (isNPD || isOfflineShift) ? 0 : (() => {
    if (!rt || !shiftCfgD || !rt.record_date || !idealCT || idealCT <= 0)
      return rt?.shift_plan_completed || 0;
    const shiftTotal     = rt.shift_plan || 0;
    const [sh, sm]       = (shiftCfgD.start_time || "08:30").split(":").map(Number);
    const shiftStart     = new Date(rt.record_date + "T00:00:00");
    shiftStart.setHours(sh, sm, 0, 0);
    const shiftStartMs   = shiftStart.getTime();
    const startupDelayMs = (shiftCfgD.startup_delay_min || 5) * 60 * 1000;
    const nowMs          = now.getTime();
    const elapsedMs      = Math.max(0, nowMs - shiftStartMs - startupDelayMs);
    let breakMs = 0;
    for (const b of (detail?.breaks || [])) {
      const [bsh, bsm] = (b.start_time || "00:00").split(":").map(Number);
      const [beh, bem] = (b.end_time   || "00:00").split(":").map(Number);
      const bs = new Date(shiftStart); bs.setHours(bsh, bsm, 0, 0);
      const be = new Date(shiftStart); be.setHours(beh, bem, 0, 0);
      if (b.crosses_midnight) be.setDate(be.getDate() + 1);
      const ov0 = Math.max(shiftStartMs, bs.getTime());
      const ov1 = Math.min(nowMs, be.getTime());
      if (ov1 > ov0) breakMs += ov1 - ov0;
    }
    const workingSec = Math.max(0, (elapsedMs - breakMs) / 1000);
    const computed   = Math.floor(workingSec / idealCT);
    return shiftTotal > 0 ? Math.min(shiftTotal, computed) : computed;
  })();

  const otActiveShift = rt?.ot_active_shift || null;
  const planPct    = (!isNPD && !isOfflineShift && plan) ? Math.min(100, (actual / plan) * 100) : 0;
  const perf       = (isNPD || isOfflineShift) ? 0 : (rt?.performance || 0);
  const statusName = isOfflineShift ? "OFFLINE" : (rt?.operating_status || "IDLE");
  const statusColor = isOfflineShift ? "#94a3b8" : (globalStatus[statusName]?.color_hex || "#3b82f6");
  const totalLoss  = rt ? (
    (rt.loss_breakdown_seconds   || 0) + (rt.loss_quality_seconds     || 0) +
    (rt.loss_material_seconds    || 0) + (rt.loss_setup_seconds       || 0) +
    (rt.loss_change_over_seconds || 0) + (rt.loss_speed_seconds       || 0) +
    (rt.loss_others_seconds      || 0)
  ) : 0;

  return (
    <>
      <div
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          background: isNPD ? "rgba(251,191,36,.04)" : isOfflineShift ? "rgba(148,163,184,.04)" : "#fff",
          border: `1px solid ${isNPD ? "rgba(217,119,6,.35)" : isOfflineShift ? "#cbd5e1" : hovered ? "#3b82f6" : "#e2e8f0"}`,
          borderRadius: 14, padding: 20,
          display: "flex", flexDirection: "column", gap: 14,
          transition: "box-shadow 0.15s, border-color 0.15s, transform 0.15s",
          boxShadow: hovered ? "0 8px 30px rgba(30,64,175,0.1)" : "0 1px 3px rgba(0,0,0,0.05)",
          transform: hovered ? "translateY(-2px)" : "none",
          position: "relative", overflow: "hidden",
          // When this line has sub-machines, span the WHOLE row of the
          // parent .lines-grid (which is auto-fill 320px columns).
          // That gives the sub-machines section the full container width
          // so M-1, M-2, M-3 … sit on one wide horizontal row instead of
          // wrapping inside a narrow 320px column.
          ...(submachines.length > 0 ? { gridColumn: "1 / -1" } : null),
        }}
      >
        {/* Top accent bar */}
        <div style={{
          position: "absolute", top: 0, left: 0, right: 0, height: 3,
          background: isNPD
            ? "linear-gradient(90deg,#d97706,#fbbf24)"
            : isOfflineShift
              ? "linear-gradient(90deg,#94a3b8,#cbd5e1)"
              : isRunning
                ? "linear-gradient(90deg,#16a34a,#22c55e)"
                : "linear-gradient(90deg,#94a3b8,#cbd5e1)",
        }} />

        {/* NPD banner */}
        {isNPD && npdEntries.filter(e => !e.shift_name || e.shift_name === shift).map(e => (
          <div key={e.id} style={{ background: "rgba(217,119,6,.1)", border: "1px solid rgba(217,119,6,.3)", borderRadius: 8, padding: "6px 12px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <div style={{ minWidth: 0 }}>
              <span style={{ fontSize: 11, fontWeight: 800, color: "#b45309", textTransform: "uppercase", letterSpacing: ".06em" }}>
                ⛔ {e.shift_name ? `Shift ${e.shift_name} Non-Production` : "Non-Production Day"}
              </span>
              {e.hourly_slots?.length > 0 && (
                <span style={{ fontSize: 10, color: "#92400e", marginLeft: 6 }}>({e.hourly_slots.join(", ")})</span>
              )}
              {e.reason && <span style={{ fontSize: 11, color: "#92400e", marginLeft: 6 }}>— {e.reason}</span>}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
              <span style={{ fontSize: 10, color: "#b45309", fontWeight: 600 }}>Target: 0 · {e.created_by || "—"}</span>
              {canToggleNPD && (
                <button onClick={() => unmarkNpdEntry(e.id)} disabled={npdLoading}
                  style={{ padding: "2px 8px", borderRadius: 5, border: "1px solid rgba(217,119,6,.4)", background: "rgba(217,119,6,.15)", color: "#b45309", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
                  ✕</button>
              )}
            </div>
          </div>
        ))}

        {/* OT active banner */}
        {otActiveShift && (
          <div style={{ background: "rgba(22,163,74,.08)", border: "1px solid rgba(22,163,74,.3)", borderRadius: 8, padding: "6px 12px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <span style={{ fontSize: 11, fontWeight: 800, color: "#16a34a", textTransform: "uppercase", letterSpacing: ".06em" }}>
              ⏱ Overtime Active — Shift {otActiveShift}
            </span>
            <span style={{ fontSize: 10, color: "#16a34a", fontWeight: 600 }}>Plan frozen · Actual counting</span>
          </div>
        )}

        {/* Offline shift banner */}
        {isOfflineShift && !isNPD && (
          <div style={{ background: "rgba(148,163,184,.1)", border: "1px solid rgba(148,163,184,.4)", borderRadius: 8, padding: "6px 12px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <span style={{ fontSize: 11, fontWeight: 800, color: "#475569", textTransform: "uppercase", letterSpacing: ".06em" }}>
              ⏸ Offline — Outside Active Shifts
            </span>
            <span style={{ fontSize: 10, color: "#64748b", fontWeight: 600 }}>
              Active: Shift {activeShiftsList.join(" & ")} only · Current: Shift {shift}
            </span>
          </div>
        )}

        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 4 }}>
          <div style={{
            width: 40, height: 40, borderRadius: 10, flexShrink: 0,
            background: isNPD ? "rgba(217,119,6,.1)" : isOfflineShift ? "#f1f5f9" : isRunning ? "rgba(22,163,74,0.1)" : "#f8fafc",
            border: `1px solid ${isNPD ? "rgba(217,119,6,.3)" : isOfflineShift ? "#cbd5e1" : isRunning ? "rgba(22,163,74,0.3)" : "#e2e8f0"}`,
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 18, color: isNPD ? "#d97706" : isOfflineShift ? "#94a3b8" : isRunning ? "#16a34a" : "#94a3b8", fontWeight: 700,
          }}>
            {isNPD ? "⛔" : isOfflineShift ? "⏸" : isRunning ? "▶" : "■"}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 700, fontSize: 15, color: "#0f172a", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {line.line_name}
            </div>
            <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span>{line.line_code} · Shift {shift}</span>
              {/* Operator badge widget — chip showing current signed-in
                  operator or a "Scan badge to sign in" CTA.  USB scanner
                  on the floor PC fires Enter, the chip handles login. */}
              <OperatorBadge lineId={line.id} token={token} shift={shift} />
            </div>
          </div>
          <div style={{
            padding: "4px 10px", borderRadius: 99,
            background: `${statusColor}18`, border: `1px solid ${statusColor}44`,
            fontSize: 11, fontWeight: 700, color: statusColor, flexShrink: 0,
            textTransform: "capitalize",
          }}>
            {statusName.toLowerCase().replace(/_/g, " ")}
          </div>
        </div>

        {/* ── Main metrics row — horizontal 3-zone layout ──────────────────
            LEFT  : compact KPI tiles (Plan / Actual / Performance) — fixed width
                    so they don't stretch awkwardly when the card spans the full
                    container row in sub-machine mode.
            MIDDLE: Production Progress + Total Loss stacked, flex:1 fills the gap.
            RIGHT : (kept empty here — buttons sit on the next row to keep this
                    row visually clean and the buttons row unmistakable.)
            On narrow cards (no sub-machines) this row wraps gracefully so the
            existing single-column line tile still looks tidy. */}
        <div style={{
          display: "flex", gap: 12, flexWrap: "wrap", alignItems: "stretch",
        }}>
          {/* ─ KPI tiles ─ */}
          <div style={{ display: "flex", gap: 8, flex: "0 1 auto" }}>
            {[
              { label: "Plan",        val: (isNPD || isOfflineShift) ? "0" : rt ? plan                    : "—", mono: true              },
              { label: "Actual",      val: isOfflineShift ? "—" : rt ? actual                               : "—", mono: true, blue: !isOfflineShift },
              { label: "Performance", val: (isNPD || isOfflineShift) ? "N/A" : rt ? `${perf.toFixed(1)}%` : "—", mono: false             },
            ].map(({ label, val, mono, blue }) => (
              <div key={label} style={{
                background: "linear-gradient(180deg,#ffffff 0%,#f8fafc 100%)",
                borderRadius: 10, border: "1px solid #e2e8f0",
                padding: "12px 18px", textAlign: "center",
                minWidth: 120, flex: "1 1 120px",
                boxShadow: "0 1px 2px rgba(15,23,42,.04)",
              }}>
                <div style={{
                  fontSize: 24, fontWeight: 800,
                  fontFamily: mono ? "monospace" : "inherit",
                  color: blue ? "#1e40af" : "#0f172a",
                  lineHeight: 1.1,
                }}>{val}</div>
                <div style={{
                  fontSize: 10, color: "#64748b",
                  textTransform: "uppercase", letterSpacing: "0.08em",
                  marginTop: 5, fontWeight: 600,
                }}>{label}</div>
              </div>
            ))}
          </div>

          {/* ─ Progress + Loss stack (middle, stretches to fill) ─ */}
          <div style={{
            flex: "1 1 280px", display: "flex", flexDirection: "column", gap: 8,
            justifyContent: "center",
          }}>
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#94a3b8", marginBottom: 6 }}>
                <span style={{ fontWeight: 600 }}>Production Progress</span>
                <span style={{ fontWeight: 700, color: "#0f172a" }}>{(isNPD || isOfflineShift) ? "—" : `${planPct.toFixed(1)}%`}</span>
              </div>
              <div style={{ background: "#f1f5f9", borderRadius: 4, height: 8, overflow: "hidden" }}>
                <div style={{
                  width: (isNPD || isOfflineShift) ? "0%" : `${planPct}%`,
                  height: "100%", borderRadius: 4,
                  background: "linear-gradient(90deg,#1e40af,#3b82f6)",
                  transition: "width 0.6s ease",
                  boxShadow: "0 0 8px rgba(30,64,175,.4)",
                }} />
              </div>
            </div>

            <div style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              background: totalLoss > 0 ? "rgba(239,68,68,0.06)" : "#f8fafc",
              border: `1px solid ${totalLoss > 0 ? "rgba(239,68,68,0.2)" : "#f1f5f9"}`,
              borderRadius: 8, padding: "8px 14px",
            }}>
              <span style={{ fontSize: 11, color: "#64748b", fontWeight: 600 }}>Total Loss</span>
              <span style={{
                fontFamily: "monospace", fontSize: 14, fontWeight: 800,
                color: totalLoss > 0 ? "#dc2626" : "#94a3b8",
                letterSpacing: ".02em",
              }}>
                {fmtSec(totalLoss)}
              </span>
            </div>
          </div>
        </div>

        {/* NPD multi-step form */}
        {canToggleNPD && showNpdForm && (() => {
          const lineShifts    = (detail?.shifts || []).filter(s => !s.shift_name.startsWith("GAP"));
          const slotsForShift = (detail?.hourly_slots || []).filter(s => s.shift_name === npdFormShift);
          const allSlotLabels = slotsForShift.map(s => s.slot_label);
          const isAllSlots    = npdFormSlots.length === 0 || npdFormSlots.length === allSlotLabels.length;

          const toggleSlot = (label) => {
            setNpdFormSlots(prev => {
              const base = prev.length === 0 ? allSlotLabels : prev;
              return base.includes(label) ? base.filter(x => x !== label) : [...base, label];
            });
          };

          return (
            <div style={{ background: "rgba(217,119,6,.06)", border: "1px solid rgba(217,119,6,.25)", borderRadius: 10, padding: "14px 16px", fontSize: 12 }}>
              {/* Step indicator */}
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 12 }}>
                {["Date", "Shift", "Hours"].map((s, i) => (
                  <div key={s} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    <div style={{
                      width: 20, height: 20, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, fontWeight: 700,
                      background: npdStep >= i + 1 ? "#d97706" : "#e2e8f0",
                      color: npdStep >= i + 1 ? "#fff" : "#94a3b8",
                    }}>{i + 1}</div>
                    <span style={{ fontSize: 10, fontWeight: 600, color: npdStep === i + 1 ? "#92400e" : "#94a3b8" }}>{s}</span>
                    {i < 2 && <div style={{ width: 16, height: 1, background: "#e2e8f0" }} />}
                  </div>
                ))}
              </div>

              {/* Step 1: Date */}
              {npdStep === 1 && (
                <div>
                  <div style={{ fontWeight: 700, color: "#92400e", marginBottom: 8 }}>Select date for {line.line_name}</div>
                  <input type="date" value={npdFormDate} onChange={e => setNpdFormDate(e.target.value)}
                    style={{ width: "100%", padding: "8px 10px", border: "1.5px solid rgba(217,119,6,.4)", borderRadius: 7, fontSize: 12, outline: "none", marginBottom: 10, boxSizing: "border-box", background: "#fff" }}
                  />
                  <div style={{ display: "flex", gap: 8 }}>
                    <button onClick={() => setNpdStep(2)} disabled={!npdFormDate}
                      style={{ flex: 1, padding: "8px 0", borderRadius: 7, border: "none", background: "#d97706", color: "#fff", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
                      Next → Select Shift</button>
                    <button onClick={() => setShowNpdForm(false)}
                      style={{ padding: "8px 14px", borderRadius: 7, border: "1px solid #e2e8f0", background: "#f8fafc", fontSize: 12, fontWeight: 600, cursor: "pointer", color: "#64748b" }}>
                      Cancel</button>
                  </div>
                </div>
              )}

              {/* Step 2: Shift */}
              {npdStep === 2 && (
                <div>
                  <div style={{ fontWeight: 700, color: "#92400e", marginBottom: 8 }}>Select shift — {npdFormDate}</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10 }}>
                    {lineShifts.length === 0
                      ? <div style={{ color: "#94a3b8", fontSize: 11 }}>No shifts configured for this line</div>
                      : lineShifts.map(s => (
                        <button key={s.shift_name}
                          onClick={() => { setNpdFormShift(s.shift_name); setNpdFormSlots([]); setNpdStep(3); }}
                          style={{
                            textAlign: "left", padding: "8px 12px", borderRadius: 7,
                            border: npdFormShift === s.shift_name ? "1.5px solid #d97706" : "1.5px solid #e2e8f0",
                            background: npdFormShift === s.shift_name ? "rgba(217,119,6,.12)" : "#fff",
                            color: "#0f172a", fontSize: 12, fontWeight: 600, cursor: "pointer",
                          }}>
                          Shift {s.shift_name} &nbsp;·&nbsp; {s.start_time} – {s.end_time}
                          &nbsp;·&nbsp; Plan: {s.total_plan || 0} pcs
                        </button>
                      ))}
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button onClick={() => setNpdStep(1)}
                      style={{ padding: "8px 14px", borderRadius: 7, border: "1px solid #e2e8f0", background: "#f8fafc", fontSize: 12, fontWeight: 600, cursor: "pointer", color: "#64748b" }}>
                      ← Back</button>
                  </div>
                </div>
              )}

              {/* Step 3: Hourly slots */}
              {npdStep === 3 && (
                <div>
                  <div style={{ fontWeight: 700, color: "#92400e", marginBottom: 4 }}>
                    Select non-production hours — Shift {npdFormShift} · {npdFormDate}
                  </div>
                  <div style={{ fontSize: 10, color: "#92400e", marginBottom: 8 }}>
                    {isAllSlots ? "All hours selected = entire shift is non-production" : `${npdFormSlots.length} hour(s) selected as non-production`}
                  </div>
                  {slotsForShift.length === 0
                    ? <div style={{ color: "#94a3b8", fontSize: 11, marginBottom: 10 }}>No hourly slots configured</div>
                    : (
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginBottom: 10 }}>
                        {slotsForShift.map(s => {
                          const checked = npdFormSlots.length === 0 || npdFormSlots.includes(s.slot_label);
                          return (
                            <label key={s.slot_label} style={{
                              display: "flex", alignItems: "center", gap: 6, padding: "6px 8px", borderRadius: 6,
                              background: checked ? "rgba(217,119,6,.1)" : "#f8fafc",
                              border: checked ? "1px solid rgba(217,119,6,.3)" : "1px solid #e2e8f0",
                              cursor: "pointer", fontSize: 11,
                            }}>
                              <input type="checkbox" checked={checked} onChange={() => toggleSlot(s.slot_label)}
                                style={{ accentColor: "#d97706" }} />
                              <span style={{ fontWeight: 600 }}>{s.slot_label}</span>
                              <span style={{ color: "#94a3b8" }}>{s.plan_pieces} pcs</span>
                            </label>
                          );
                        })}
                      </div>
                    )
                  }
                  <input value={npdFormReason} onChange={e => setNpdFormReason(e.target.value)}
                    placeholder="Reason (optional) — e.g. Maintenance, Holiday…"
                    style={{ width: "100%", padding: "7px 10px", border: "1.5px solid rgba(217,119,6,.35)", borderRadius: 7, fontSize: 12, outline: "none", marginBottom: 10, boxSizing: "border-box", background: "#fff" }}
                  />
                  <div style={{ display: "flex", gap: 8 }}>
                    <button onClick={markNPD} disabled={npdLoading}
                      style={{ flex: 1, padding: "8px 0", borderRadius: 7, border: "none", background: "#d97706", color: "#fff", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
                      {npdLoading ? "Saving…" : "⛔ Confirm Non-Production"}</button>
                    <button onClick={() => setNpdStep(2)}
                      style={{ padding: "8px 14px", borderRadius: 7, border: "1px solid #e2e8f0", background: "#f8fafc", fontSize: 12, fontWeight: 600, cursor: "pointer", color: "#64748b" }}>
                      ← Back</button>
                  </div>
                </div>
              )}
            </div>
          );
        })()}

        {/* Action buttons — capped width so they stay compact even when the
            line card is full container width (sub-machine mode). flex-wrap
            still folds them to 2 rows on small cards. */}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", maxWidth: 820 }}>
          <Btn onClick={() => setModal(true)} variant="ghost">📊 Details</Btn>
          <Btn onClick={() => window.open(`/fullscreen/${line.id}`, "_blank")} variant="primary">🖥 Fullscreen</Btn>
          {/* 65" wall TV shortcut — multi-machine CT wallboard.
              The summary/histogram dashboard was folded into Fullscreen
              as the "CT Distribution" toggle, so no separate button. */}
          <Btn onClick={() => window.open(`/wallboard/left/${line.id}`, "_blank")} variant="ghost"
                title="Open the multi-machine CT wallboard (Machine CT)">
            📈 Machine CT
          </Btn>
          {canToggleNPD && (
            <button
              onClick={openNpdForm}
              style={{
                flex: 1, padding: "9px 0", borderRadius: 8,
                border: isNPD ? "1.5px solid rgba(217,119,6,.4)" : "1.5px solid #e2e8f0",
                background: isNPD ? "rgba(217,119,6,.08)" : showNpdForm ? "rgba(217,119,6,.08)" : "#f8fafc",
                color: isNPD ? "#b45309" : showNpdForm ? "#b45309" : "#64748b",
                fontSize: 12, fontWeight: 700, cursor: "pointer", transition: "all .12s",
              }}
              title="Manage non-production periods for this line"
            >⛔ {isNPD ? "Manage NPD" : "Non-Production Day"}</button>
          )}
          {canToggleOT && (
            <button
              ref={otBtnRef}
              onClick={() => {
                // Calculate menu position relative to the button's on-screen box.
                const r = otBtnRef.current?.getBoundingClientRect();
                if (r) {
                  // ~180 px-wide menu, anchor above the button, right-aligned.
                  setOtMenuPos({
                    top:  Math.max(8, r.top - 8),          // open above
                    left: Math.max(8, r.right - 200),      // right-align to button
                  });
                }
                setShowOtMenu(m => !m);
              }}
              style={{
                padding: "9px 14px", borderRadius: 8, fontSize: 12, fontWeight: 700,
                cursor: "pointer", transition: "all .12s",
                border: otActiveShift ? "1.5px solid rgba(22,163,74,.4)" : "1.5px solid #e2e8f0",
                background: otActiveShift ? "rgba(22,163,74,.1)" : showOtMenu ? "#f1f5f9" : "#f8fafc",
                color: otActiveShift ? "#16a34a" : "#64748b",
              }}
              title="Overtime switch"
            >
              ⏱ OT {otActiveShift ? `(Shift ${otActiveShift})` : ""}
            </button>
          )}
        </div>

        {/* ── Sub-machines (auxiliary PLCs on the same line) ──────────────
            Horizontal-row tile gallery sitting under the line's main PLC card.
            Each tile shows just the admin-assigned "M-N" badge, machine name,
            today's cycle count + average cycle-time, and a Fullscreen button
            that opens the dedicated sub-machine page in a new tab.
            IP / bit / ideal-CT are intentionally hidden — they're admin-only
            details and clutter the operator's view. */}
        {/* 2026-05-24 — Sub-machines section HIDDEN from main dashboard
            per operator: "main dashbard me sirf final ka data aayega
            or kisi ka BHI NHI OK".  Sub-machine counts available on
            the dedicated /submachine-fullscreen/{id} page. */}
        {false && submachines.length > 0 && (
          <div style={{
            marginTop: 14, paddingTop: 14,
            borderTop: "1px dashed #cbd5e1",
          }}>
            <div style={{
              fontSize: 10, fontWeight: 800, color: "#64748b",
              letterSpacing: ".12em", textTransform: "uppercase",
              marginBottom: 12,
            }}>
              Sub-machines · {submachines.length}
            </div>

            {/* True horizontal row — tiles stay on one line and the row
                scrolls left/right when there are more tiles than fit in the
                card width.  Fixed-width tiles keep the layout uniform; the
                paddingBottom + custom thin scrollbar make the scrollbar
                feel native instead of jarring. */}
            <div style={{
              display: "flex",
              flexWrap: "nowrap",
              gap: 10,
              overflowX: "auto",
              overflowY: "hidden",
              paddingBottom: 4,
              scrollbarWidth: "thin",
              scrollbarColor: "#cbd5e1 transparent",
            }}>
              {submachines.map((sm, idx) => {
                // Big "M-N" badge — admin-chosen via AdminPanel.  When admin
                // hasn't set one yet, fall back to the position in the list
                // (1-indexed) so every tile always shows a sensible label.
                const seqLabel = sm.machine_seq != null
                  ? `M-${sm.machine_seq}`
                  : `M-${idx + 1}`;

                const todayCount = sm.today_count || 0;
                const avgCt      = sm.today_avg_ct != null
                  ? `${Number(sm.today_avg_ct).toFixed(2)}s`
                  : "—";

                // Active = at least one cycle today.  Used to dim the avg-CT
                // colour for idle tiles and to brighten ones that are running.
                const isActive   = todayCount > 0;

                const isBottleneck = !!sm.is_bottleneck;
                // 2026-05-16 — Whole tile is now clickable (operator
                // feedback: "mujhe alg alg submachine ke fullscreen
                // dashboards prr jaana ahiu" — tap anywhere on the card,
                // not just the button at the bottom).  Middle-click /
                // Ctrl-click still opens in new tab (browser default).
                const openSubFullscreen = (ev) => {
                  // Middle-click → new tab (browser handles via window.open)
                  const newTab = ev.metaKey || ev.ctrlKey || ev.button === 1;
                  if (newTab) {
                    window.open(`/submachine-fullscreen/${sm.id}`, "_blank");
                  } else {
                    // Same tab navigation — operator can use browser back
                    window.location.href = `/submachine-fullscreen/${sm.id}`;
                  }
                };
                return (
                  <div
                    key={sm.id}
                    title={`${seqLabel} · ${sm.machine_name || "Unnamed sub-machine"}${isBottleneck ? " · BOTTLENECK" : ""} — click anywhere to open fullscreen (cycle-time chart, hourly progress, per-cycle video).`}
                    onClick={openSubFullscreen}
                    onAuxClick={(e) => { if (e.button === 1) openSubFullscreen(e); }}
                    style={{
                      // Fixed-width tile — single horizontal row that scrolls
                      // horizontally if there are more sub-machines than fit.
                      flex: "0 0 200px", width: 200,
                      background: isBottleneck
                        ? "linear-gradient(180deg, #fef2f2 0%, #fee2e2 100%)"
                        : "linear-gradient(180deg, #ffffff 0%, #f8fafc 100%)",
                      border: `${isBottleneck ? "2px" : "1px"} solid ${isBottleneck ? "#dc2626" : "#cbd5e1"}`,
                      borderRadius: 12,
                      padding: "12px 14px 14px",
                      boxShadow: isBottleneck
                        ? "0 0 0 3px rgba(220,38,38,.10), 0 1px 2px rgba(15,23,42,.04)"
                        : "0 1px 2px rgba(15,23,42,.04)",
                      transition: "transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease",
                      display: "flex", flexDirection: "column", gap: 10,
                      position: "relative",
                      cursor: "pointer",
                    }}
                    onMouseEnter={(e) => {
                      // Subtle lift on hover — eye-catching but not noisy.
                      e.currentTarget.style.transform     = "translateY(-2px)";
                      e.currentTarget.style.boxShadow     = isBottleneck
                        ? "0 0 0 3px rgba(220,38,38,.15), 0 6px 16px rgba(220,38,38,.18)"
                        : "0 6px 16px rgba(30,64,175,.12)";
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.transform     = "translateY(0)";
                      e.currentTarget.style.boxShadow     = isBottleneck
                        ? "0 0 0 3px rgba(220,38,38,.10), 0 1px 2px rgba(15,23,42,.04)"
                        : "0 1px 2px rgba(15,23,42,.04)";
                    }}
                  >
                    {isBottleneck && (
                      <div style={{
                        position: "absolute", top: -8, right: 10,
                        fontSize: 9, fontWeight: 900, letterSpacing: ".08em",
                        color: "#fff", background: "#dc2626",
                        padding: "3px 8px", borderRadius: 99,
                        boxShadow: "0 2px 6px rgba(220,38,38,.4)",
                      }}>
                        🚧 BOTTLENECK
                      </div>
                    )}
                    {/* Top: M-N badge + machine name */}
                    <div>
                      <div style={{
                        display: "inline-block",
                        fontSize: 11, fontWeight: 800,
                        letterSpacing: ".08em",
                        color: isBottleneck ? "#b91c1c" : "#1e40af",
                        background: isBottleneck ? "rgba(220,38,38,.12)" : "rgba(30,64,175,.08)",
                        padding: "3px 10px",
                        borderRadius: 999,
                      }}>
                        {seqLabel}
                      </div>
                      <div style={{
                        fontSize: 14, fontWeight: 800, color: "#0f172a",
                        marginTop: 8, lineHeight: 1.25,
                        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                      }}
                      title={sm.machine_name}>
                        {sm.machine_name || "(unnamed)"}
                      </div>
                    </div>

                    {/* KPIs: today count + average cycle time */}
                    <div style={{
                      display: "flex", gap: 14, alignItems: "baseline",
                      borderTop: "1px solid #e2e8f0", paddingTop: 10,
                    }}>
                      <div title="Cycles completed since shift start today">
                        <div style={{
                          fontSize: 18, fontWeight: 800,
                          color: isActive ? "#0f172a" : "#94a3b8",
                          lineHeight: 1,
                        }}>
                          {todayCount}
                        </div>
                        <div style={{
                          fontSize: 9, color: "#64748b", marginTop: 3,
                          textTransform: "uppercase", letterSpacing: ".06em",
                        }}>
                          shift
                        </div>
                      </div>
                      <div title="Mean cycle-time for the currently-running shift">
                        <div style={{
                          fontSize: 18, fontWeight: 800,
                          color: isActive ? "#1e40af" : "#94a3b8",
                          lineHeight: 1,
                        }}>
                          {avgCt}
                        </div>
                        <div style={{
                          fontSize: 9, color: "#64748b", marginTop: 3,
                          textTransform: "uppercase", letterSpacing: ".06em",
                        }}>
                          avg CT
                        </div>
                      </div>
                    </div>

                    {/* Fullscreen button — opens the sub-machine's page in a
                        new tab so the operator can keep the line dashboard
                        open behind it.  The PARENT tile is also clickable
                        for same-tab navigation; this button is the explicit
                        "open in new tab" affordance. */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();    // don't double-fire tile click
                        window.open(`/submachine-fullscreen/${sm.id}`, "_blank");
                      }}
                      title={`Open the dedicated full-screen page for ${sm.machine_name || "this sub-machine"} in a NEW TAB. (Click anywhere else on the tile to open in the same tab.)`}
                      style={{
                        width: "100%",
                        padding: "8px 12px",
                        background: "linear-gradient(180deg, #2563eb 0%, #1e40af 100%)",
                        color: "#ffffff",
                        border: "none",
                        borderRadius: 8,
                        fontSize: 12, fontWeight: 700,
                        cursor: "pointer",
                        boxShadow: "0 1px 3px rgba(30,64,175,.35)",
                        transition: "filter 120ms ease, transform 120ms ease",
                        letterSpacing: ".02em",
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.filter    = "brightness(1.08)";
                        e.currentTarget.style.transform = "translateY(-1px)";
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.filter    = "none";
                        e.currentTarget.style.transform = "translateY(0)";
                      }}
                    >
                      🖥 Fullscreen
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* OT popover — rendered at root with fixed positioning so card overflow
          never clips it. Anchored to the OT button's bounding rect. */}
      {showOtMenu && canToggleOT && (
        <div
          id="ot-menu-popover"
          style={{
            position: "fixed", zIndex: 10000,
            top: otMenuPos.top, left: otMenuPos.left,
            transform: "translateY(-100%)",
            background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10,
            boxShadow: "0 12px 40px rgba(0,0,0,.18)", padding: 14, minWidth: 200,
          }}
        >
          <div style={{ fontSize: 11, fontWeight: 700, color: "#64748b", marginBottom: 10, textTransform: "uppercase", letterSpacing: ".06em" }}>
            Activate OT for:
          </div>
          {["A", "B"].map(s => (
            <button key={s}
              onClick={() => activateOt(s)} disabled={otLoading}
              style={{
                display: "block", width: "100%", textAlign: "left", marginBottom: 6,
                padding: "8px 12px", borderRadius: 7, fontSize: 12, fontWeight: 700,
                cursor: "pointer", border: otActiveShift === s ? "1.5px solid #16a34a" : "1.5px solid #e2e8f0",
                background: otActiveShift === s ? "rgba(22,163,74,.1)" : "#f8fafc",
                color: otActiveShift === s ? "#16a34a" : "#0f172a",
              }}>
              {otActiveShift === s ? "✓ " : ""}Shift {s} OT
            </button>
          ))}
          {otActiveShift && (
            <button onClick={deactivateOt} disabled={otLoading}
              style={{
                display: "block", width: "100%", textAlign: "left", marginTop: 4,
                padding: "8px 12px", borderRadius: 7, fontSize: 12, fontWeight: 700,
                cursor: "pointer", border: "1.5px solid rgba(220,38,38,.3)",
                background: "rgba(220,38,38,.06)", color: "#dc2626",
              }}>
              Turn OFF OT
            </button>
          )}
        </div>
      )}

      {showModal && rt && (
        <DetailModal line={line} rt={rt} lineDetail={detail} onClose={() => setModal(false)} />
      )}
    </>
  );
}

// ─── Dashboard ────────────────────────────────────────────────────────────────
export default function Dashboard() {
  const { token, user, theme, isAdmin } = useAuth();
  // Admin sees explicit "Production Dashboard" title; non-admin (production
  // user, operator) sees just "Dashboard" — they only land on this page.
  const titleLeft  = isAdmin ? "Production " : "";
  const titleRight = "Dashboard";
  const [zones,        setZones]        = useState([]);
  const [lines,        setLines]        = useState([]);
  // Pending Production half of any open breakdown (split BREAK DOWN SLIP)
  const [pendingBd,    setPendingBd]    = useState([]);
  const [bdModal,      setBdModal]      = useState(null); // { ticket, mode, phase }
  // Which line's pending-list is expanded in the banner.  null = all
  // collapsed (one tile per line); set to a line_id to drop down that
  // line's individual breakdowns with start/end/duration.
  const [expandedBdLine, setExpandedBdLine] = useState(null);
  const [globalStatus, setGlobalStatus] = useState({});
  const [selZones,     setSelZones]     = useState([]);   // empty = all zones
  const [dropOpen,     setDropOpen]     = useState(false);
  const [loading,      setLoading]      = useState(true);
  const [summary,      setSummary]      = useState({ total: 0, running: 0, stopped: 0, avgOee: "0.0", zoneOees: [], npdCount: 0, npdLines: [] });
  const [clock,        setClock]        = useState(new Date());
  useEffect(() => { const t = setInterval(() => setClock(new Date()), 1000); return () => clearInterval(t); }, []);

  // rtMapRef holds per-line { rt, collectorStatus } — never causes re-render
  const rtMapRef       = useRef({});
  // npdMapRef holds per-line { isNPD, entries } for summary tile
  const npdMapRef      = useRef({});
  // Stable refs to avoid stale closures inside callbacks
  const linesRef       = useRef([]);
  const zonesRef       = useRef([]);   // 2026-05-13 — used by per-zone OEE breakdown in recomputeSummary
  const selZonesRef    = useRef([]);
  const dropRef        = useRef(null);
  // Debounce timer for summary recalc
  const summaryTimerRef = useRef(null);

  useEffect(() => { document.title = "Dashboard"; }, []);

  // Poll the Production-half pending list every 10 s so the banner
  // updates without manual refresh whenever the collector flips a line
  // into BREAKDOWN.
  useEffect(() => {
    if (!token) return;
    const fetchPending = () => {
      api.get("/api/breakdowns/pending-production", token)
        .then(rows => setPendingBd(Array.isArray(rows) ? rows : []))
        .catch(() => {});
    };
    fetchPending();
    const t = setInterval(fetchPending, 10000);
    return () => clearInterval(t);
  }, [token]);

  // Submit handler — Production fills only their half; closes modal on success.
  const onProductionSave = async (slice, _phase) => {
    try {
      await api.post(`/api/breakdowns/${bdModal.ticket.id}/production-fill`,
                     { production_data: slice }, token);
      setBdModal(null);
      // Refresh the pending list immediately
      api.get("/api/breakdowns/pending-production", token)
        .then(rows => setPendingBd(Array.isArray(rows) ? rows : []))
        .catch(() => {});
    } catch (e) {
      alert(e.message || "Submit failed");
      throw e;
    }
  };

  // Stable summary recomputer — reads refs so it never needs to be recreated
  const recomputeSummary = useCallback(() => {
    const sl  = selZonesRef.current;
    const lns = linesRef.current;
    const zns = zonesRef.current;
    const zoneIds = sl.length === 0 ? null
      : new Set(lns.filter(l => sl.includes(String(l.zone_id))).map(l => l.id));

    const entries = Object.entries(rtMapRef.current)
      .filter(([id]) => !zoneIds || zoneIds.has(Number(id)))
      .map(([id, v]) => ({ id: Number(id), ...v }));

    const total   = entries.length;
    const running = entries.filter(e => e.collectorStatus === "running").length;
    let totalOee = 0, oeeCount = 0;
    entries.forEach(e => {
      if (e.rt?.overall_oee > 0) { totalOee += e.rt.overall_oee; oeeCount++; }
    });

    // 2026-05-13 — operator spec: replace the single global "Avg OEE"
    // tile with a per-zone breakdown.  Group every line by its zone,
    // average the OEE of lines that have valid rt data.  Filtered-zone
    // selection respected — only those zones appear in the breakdown.
    const zoneBuckets = new Map();  // zoneId → { name, sum, count, lines, running }
    entries.forEach(e => {
      const line = lns.find(l => l.id === e.id);
      if (!line) return;
      const zid = line.zone_id;
      if (!zoneBuckets.has(zid)) {
        const z = zns.find(z => z.id === zid);
        zoneBuckets.set(zid, {
          zone_id:   zid,
          zone_name: z?.zone_name || line.zone_name || `Zone ${zid}`,
          sum:       0,
          count:     0,
          lines:     0,
          running:   0,
        });
      }
      const b = zoneBuckets.get(zid);
      b.lines += 1;
      if (e.collectorStatus === "running") b.running += 1;
      if (e.rt?.overall_oee > 0) { b.sum += e.rt.overall_oee; b.count += 1; }
    });
    const zoneOees = [...zoneBuckets.values()]
      .map(b => ({
        zone_id:   b.zone_id,
        zone_name: b.zone_name,
        oee:       b.count ? (b.sum / b.count).toFixed(1) : "0.0",
        lines:     b.lines,
        running:   b.running,
        has_data:  b.count > 0,
      }))
      .sort((a, b) => a.zone_name.localeCompare(b.zone_name));

    // NPD lines in current zone selection
    const npdPairs = Object.entries(npdMapRef.current)
      .filter(([id, v]) => v?.isNPD && (!zoneIds || zoneIds.has(Number(id))));
    const npdLineNames = npdPairs.map(([id, v]) => {
      const l = lns.find(l => l.id === Number(id));
      return { name: l?.line_name || `Line ${id}`, reason: v.reason || null };
    });

    setSummary({
      total, running,
      stopped: total - running,
      avgOee:  oeeCount ? (totalOee / oeeCount).toFixed(1) : "0.0",
      zoneOees,
      npdCount: npdLineNames.length,
      npdLines: npdLineNames,
    });
  }, []);

  // Called by each LineCard every 3s — debounced so summary tiles don't flicker
  const onRtUpdate = useCallback((lineId, rt, collectorStatus) => {
    rtMapRef.current[lineId] = { rt, collectorStatus };
    if (summaryTimerRef.current) clearTimeout(summaryTimerRef.current);
    summaryTimerRef.current = setTimeout(recomputeSummary, 800);
  }, [recomputeSummary]);

  // Called by each LineCard after NPD fetch
  const onNpdUpdate = useCallback((lineId, isNPD, entries) => {
    const reason = entries?.find(e => !e.shift_name)?.reason || null;
    npdMapRef.current[lineId] = { isNPD, reason };
    recomputeSummary();
  }, [recomputeSummary]);

  useEffect(() => {
    async function load() {
      try {
        const [zData, lData, sData] = await Promise.all([
          api.get("/api/zones/",         token).catch(() => []),
          api.get("/api/lines/",         token).catch(() => []),
          api.get("/api/status-schema/", token).catch(() => []),
        ]);
        const linesArr = Array.isArray(lData) ? lData : [];
        setZones(Array.isArray(zData) ? zData : []);
        setLines(linesArr);

        const statusMap = {};
        if (Array.isArray(sData)) sData.forEach(s => { statusMap[s.status_name] = s; });
        setGlobalStatus(statusMap);

        // Pre-populate rtMap with all lines immediately so summary.total
        // is correct from the start and doesn't jump as cards load one-by-one
        const initialMap = {};
        linesArr.forEach(l => {
          initialMap[l.id] = {
            rt:              null,
            collectorStatus: l.collector_status || "stopped",
          };
        });
        rtMapRef.current = initialMap;

        // Set stable initial summary from the lines API data
        const initialRunning = linesArr.filter(l => l.collector_status === "running").length;
        linesRef.current = linesArr;
        setSummary({
          total:   linesArr.length,
          running: initialRunning,
          stopped: linesArr.length - initialRunning,
          avgOee:  "0.0",
          zoneOees: [],
          npdCount: 0,
          npdLines: [],
        });

      } finally {
        setLoading(false);
      }
    }
    load();

    // Cleanup debounce timer on unmount
    return () => {
      if (summaryTimerRef.current) clearTimeout(summaryTimerRef.current);
    };
  }, [token]);

  // Keep stable refs in sync
  useEffect(() => { linesRef.current = lines; }, [lines]);
  useEffect(() => { zonesRef.current = zones; }, [zones]);
  useEffect(() => {
    selZonesRef.current = selZones;
    recomputeSummary();
  }, [selZones, recomputeSummary]);

  // Close zone dropdown on outside click
  useEffect(() => {
    if (!dropOpen) return;
    function handler(e) {
      if (dropRef.current && !dropRef.current.contains(e.target)) setDropOpen(false);
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [dropOpen]);

  const toggleZone = (zid) => {
    setSelZones(prev => prev.includes(zid) ? prev.filter(z => z !== zid) : [...prev, zid]);
  };

  const visibleLines = selZones.length === 0
    ? lines
    : lines.filter(l => selZones.includes(String(l.zone_id)));

  const grouped = selZones.length === 0
    ? zones
        .map(z => ({ zone: z, zoneLines: lines.filter(l => String(l.zone_id) === String(z.id)) }))
        .filter(g => g.zoneLines.length > 0)
    : selZones.map(zid => ({
        zone: zones.find(z => String(z.id) === zid) || { id: zid, zone_name: "Zone" },
        zoneLines: lines.filter(l => String(l.zone_id) === zid),
      })).filter(g => g.zoneLines.length > 0);

  const unassigned = selZones.length === 0 ? lines.filter(l => !l.zone_id) : [];

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@600;700;800&display=swap');
        .db-root { min-height:100vh; background:#f8fafc; font-family:'Barlow',sans-serif; padding-bottom:60px; }
        .db-topbar {
          background:#fff; border-bottom:1px solid #e2e8f0;
          padding:0 40px 0 88px; height:60px;
          display:flex; align-items:center; justify-content:space-between;
          position:sticky; top:0; z-index:100;
          box-shadow:0 1px 3px rgba(0,0,0,.06);
        }
        .db-topbar::after { content:''; position:absolute; bottom:0; left:0; right:0; height:2px; background:${theme.gradient}; }
        .db-logo { font-family:'Barlow Condensed',sans-serif; font-size:22px; font-weight:800; color:#0f172a; }
        .db-logo span { color:${theme.accent}; }
        .zone-drop-btn { display:flex; align-items:center; gap:6px; padding:7px 14px; border-radius:8px; border:1.5px solid #e2e8f0; background:#f8fafc; font-family:'Barlow',sans-serif; font-size:12px; font-weight:600; color:#334155; cursor:pointer; transition:all .12s; white-space:nowrap; }
        .zone-drop-btn:hover,.zone-drop-btn.open { border-color:#3b82f6; color:#1e40af; background:rgba(30,64,175,.04); }
        .zone-drop-btn.has-sel { background:#1e40af; border-color:#1e40af; color:#fff; }
        .zone-drop-menu { position:absolute; right:0; top:calc(100% + 6px); background:#fff; border:1px solid #e2e8f0; border-radius:10px; box-shadow:0 4px 20px rgba(0,0,0,.1); z-index:200; min-width:200px; padding:6px; }
        .zone-drop-item { display:flex; align-items:center; gap:10px; padding:7px 12px; border-radius:6px; cursor:pointer; font-size:13px; font-weight:500; color:#334155; transition:background .1s; }
        .zone-drop-item:hover { background:rgba(30,64,175,.06); color:#1e40af; }
        .zone-drop-item.sel { background:rgba(30,64,175,.08); color:#1e40af; font-weight:600; }
        .zone-drop-divider { height:1px; background:#f1f5f9; margin:4px 0; }
        /* 2026-05-13 — operator removed the 36px gap between topbar
           and content; body now sits flush under the topbar's blue
           underline.  Horizontal padding kept at 40px so cards don't
           touch the screen edges. */
        .db-body { padding:12px 40px 0; }
        .zone-section { margin-bottom:40px; }
        .zone-lbl { display:flex; align-items:center; gap:12px; margin-bottom:20px; }
        .zone-lbl-text { font-family:'Barlow Condensed',sans-serif; font-size:20px; font-weight:700; color:#0f172a; }
        .zone-lbl-badge { font-size:10px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; background:rgba(30,64,175,.1); color:#1e40af; border:1px solid rgba(30,64,175,.2); padding:3px 10px; border-radius:99px; }
        .zone-lbl-line { flex:1; height:1px; background:#e2e8f0; }
        .lines-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:18px; }
        .empty-state { text-align:center; padding:80px 40px; color:#94a3b8; }
        @keyframes spin { to { transform:rotate(360deg) } }
      `}</style>

      <div className="db-root">
        {/* Topbar */}
        <div className="db-topbar">
          <div className="db-logo" />
          <div style={{
            position:"absolute", left:"50%", transform:"translateX(-50%)",
            fontFamily:"'Barlow Condensed',sans-serif",
            fontSize:37, fontWeight:800, color:"#0f172a", letterSpacing:"-.01em",
            pointerEvents:"none",
          }}>
            {titleLeft}<span style={{ color: theme.accent }}>{titleRight}</span>
          </div>
          {/* Multi-select zone dropdown */}
          <div ref={dropRef} style={{ position:"relative" }}>
            <button
              className={`zone-drop-btn${dropOpen?" open":""}${selZones.length>0?" has-sel":""}`}
              onClick={() => setDropOpen(o => !o)}
            >
              {selZones.length===0 ? "All Zones"
                : selZones.length===1 ? (zones.find(z=>String(z.id)===selZones[0])?.zone_name||"1 Zone")
                : `${selZones.length} Zones`}
              <span style={{ fontSize:10, opacity:0.7 }}>▾</span>
            </button>
            {dropOpen && (
              <div className="zone-drop-menu">
                <div className={`zone-drop-item${selZones.length===0?" sel":""}`}
                  onClick={() => { setSelZones([]); setDropOpen(false); }}>
                  <span style={{ fontSize:14 }}>⊞</span> All Zones
                </div>
                <div className="zone-drop-divider"/>
                {zones.map(z => {
                  const zid = String(z.id);
                  const checked = selZones.includes(zid);
                  return (
                    <div key={z.id} className={`zone-drop-item${checked?" sel":""}`} onClick={() => toggleZone(zid)}>
                      <input type="checkbox" checked={checked} readOnly style={{ pointerEvents:"none", accentColor:"#1e40af" }} />
                      {z.zone_name||z.name}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        <div className="db-body">
          {/* Manpower alert banner — pops up for admin / quality / production
              when there's an UNALLOCATED, SKILL_MISMATCH, or ESCALATION
              row in mes_manpower_alerts. Each side acks independently. */}
          <ManpowerAlertBanner />

          {/* Pending breakdown-form banner — line-grouped.
              ─────────────────────────────────────────────────────────
              Earlier each pending breakdown rendered as its own
              "Fill Form" card → 17 separate boxes for one line was
              ugly.  Now we group by line: ONE collapsed tile per line
              with a count badge.  Click → drops down a list of that
              line's pending breakdowns with start time / end time /
              duration so the leader can pick which one to file first.
              ───────────────────────────────────────────────────────── */}
          {pendingBd.length > 0 && (() => {
            // Group by line_id (line_name fallback for legacy rows)
            const byLine = {};
            for (const b of pendingBd) {
              const k = b.line_id || b.line_name || "unknown";
              if (!byLine[k]) byLine[k] = {
                line_id: b.line_id, line_name: b.line_name,
                zone_name: b.zone_name, items: [],
              };
              byLine[k].items.push(b);
            }
            // Sort items per line by serial / started_at
            for (const k in byLine) {
              byLine[k].items.sort((a,b) => {
                const ta = a.started_at ? new Date(a.started_at).getTime() : 0;
                const tb = b.started_at ? new Date(b.started_at).getTime() : 0;
                return tb - ta;   // newest first
              });
            }
            const groups = Object.values(byLine);

            // Helper: format duration_seconds → "12 m 34 s" / "1 h 2 m"
            const fmtDur = (s) => {
              if (!s || s < 0) return "—";
              if (s < 60)   return `${s} s`;
              const m = Math.floor(s / 60);
              const ss = s % 60;
              if (m < 60) return ss ? `${m} m ${ss} s` : `${m} m`;
              const h = Math.floor(m / 60);
              const mm = m % 60;
              return mm ? `${h} h ${mm} m` : `${h} h`;
            };
            const fmtTime = (ts) => ts
              ? new Date(ts).toLocaleTimeString([], { hour:"2-digit", minute:"2-digit", second:"2-digit" })
              : "—";

            return (
              <div style={{
                marginBottom: 24, padding: "14px 18px",
                background: "linear-gradient(135deg, rgba(220,38,38,.08), rgba(234,88,12,.06))",
                border: "1px solid rgba(220,38,38,.25)", borderRadius: 12,
              }}>
                <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between",
                                gap: 12, flexWrap:"wrap", marginBottom: 10 }}>
                  <div style={{ display:"flex", alignItems:"center", gap: 10 }}>
                    <span style={{ fontSize: 22 }}>🚨</span>
                    <div>
                      <div style={{ fontFamily:"'Barlow Condensed',sans-serif",
                                     fontSize: 18, fontWeight: 800, color:"#991b1b" }}>
                        Action required — Fill BREAK DOWN SLIP 
                      </div>
                      {/* <div style={{ fontSize: 11, color:"#64748b", marginTop: 2 }}>
                        {pendingBd.length} pending breakdown{pendingBd.length>1?"s":""} across {groups.length} line{groups.length>1?"s":""}.
                        Click a line to see its pending entries.
                      </div> */}
                    </div>
                  </div>
                </div>

                {/* Line-grouped tiles */}
                <div style={{ display:"grid",
                              gridTemplateColumns:"repeat(auto-fill,minmax(300px,1fr))",
                              gap: 10 }}>
                  {groups.map(g => {
                    const isOpen = expandedBdLine === (g.line_id || g.line_name);
                    return (
                      <div key={g.line_id || g.line_name}
                           style={{ gridColumn: isOpen ? "1 / -1" : "auto" }}>
                        {/* Collapsed line header — clickable */}
                        <button
                          onClick={() => setExpandedBdLine(prev =>
                            prev === (g.line_id || g.line_name)
                              ? null
                              : (g.line_id || g.line_name)
                          )}
                          style={{
                            width:"100%", textAlign:"left", cursor:"pointer",
                            background:"#fff",
                            border: `1.5px solid ${isOpen ? "#dc2626" : "rgba(220,38,38,.20)"}`,
                            borderRadius: isOpen ? "10px 10px 0 0" : 10,
                            padding:"10px 14px",
                            display:"flex", alignItems:"center", justifyContent:"space-between",
                            gap: 10, fontFamily:"inherit",
                            transition:"all .15s",
                          }}>
                          <div style={{ minWidth: 0, flex: 1 }}>
                            <div style={{ fontWeight:800, color:"#0f172a", fontSize:14,
                                            overflow:"hidden", textOverflow:"ellipsis",
                                            whiteSpace:"nowrap" }}>
                              {g.line_name || `Line ${g.line_id}`}
                              {g.zone_name && (
                                <span style={{ fontWeight:500, color:"#64748b" }}>
                                  {" · "}{g.zone_name}
                                </span>
                              )}
                            </div>
                            <div style={{ fontSize:10, color:"#94a3b8", marginTop: 2 }}>
                              {g.items.length} pending breakdown{g.items.length>1?"s":""}
                              {" · "}click to {isOpen ? "collapse" : "expand"}
                            </div>
                          </div>
                          <span style={{
                            background:"linear-gradient(135deg,#dc2626,#b91c1c)",
                            color:"#fff", padding:"5px 12px",
                            borderRadius: 99, fontSize: 13, fontWeight: 800,
                            minWidth: 36, textAlign:"center", flexShrink:0,
                          }}>
                            {g.items.length}
                          </span>
                          <span style={{
                            fontSize:14, color:"#dc2626", fontWeight:700,
                            transform: isOpen ? "rotate(180deg)" : "none",
                            transition:"transform .15s", flexShrink:0,
                          }}>
                            ▾
                          </span>
                        </button>

                        {/* Expanded breakdown list — start / end / duration */}
                        {isOpen && (
                          <div style={{
                            background:"#fff",
                            border: "1.5px solid #dc2626", borderTop:"none",
                            borderRadius:"0 0 10px 10px", overflow:"hidden",
                          }}>
                            <div style={{ overflowX:"auto" }}>
                              <table style={{ width:"100%", borderCollapse:"collapse" }}>
                                <thead>
                                  <tr style={{ background:"rgba(220,38,38,.06)",
                                                 borderBottom:"1.5px solid rgba(220,38,38,.20)" }}>
                                    {["Shift","#","Started","Ended","Duration","Status",""]
                                      .map((h,i) => (
                                      <th key={i} style={{
                                        padding:"8px 12px", fontSize:9, fontWeight:800,
                                        letterSpacing:".08em", color:"#991b1b",
                                        textTransform:"uppercase",
                                        textAlign: i >= 2 && i <= 4 ? "left" : "left",
                                        whiteSpace:"nowrap",
                                      }}>{h}</th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {g.items.map(b => {
                                    const isOpen = b.state === "OPEN";
                                    return (
                                      <tr key={b.id} style={{ borderBottom:"1px solid #fee2e2" }}>
                                        <td style={{ padding:"10px 12px", fontSize:11,
                                                       fontWeight:700, color:"#0f172a", whiteSpace:"nowrap" }}>
                                          {b.shift_name || "—"}
                                        </td>
                                        <td style={{ padding:"10px 12px", fontFamily:"monospace",
                                                       fontSize:11, fontWeight:700, color:"#dc2626" }}>
                                          #{b.serial_in_shift || "—"}
                                        </td>
                                        <td style={{ padding:"10px 12px", fontSize:11,
                                                       fontFamily:"monospace", color:"#0f172a", whiteSpace:"nowrap" }}>
                                          {fmtTime(b.started_at)}
                                        </td>
                                        <td style={{ padding:"10px 12px", fontSize:11,
                                                       fontFamily:"monospace",
                                                       color: isOpen ? "#dc2626" : "#0f172a",
                                                       whiteSpace:"nowrap", fontWeight: isOpen ? 700 : 400 }}>
                                          {isOpen ? "ongoing" : fmtTime(b.ended_at)}
                                        </td>
                                        <td style={{ padding:"10px 12px", fontSize:11,
                                                       fontFamily:"monospace", fontWeight:700,
                                                       color:"#0f172a", whiteSpace:"nowrap" }}>
                                          {fmtDur(b.duration_seconds)}
                                        </td>
                                        <td style={{ padding:"10px 12px", whiteSpace:"nowrap" }}>
                                          <span style={{
                                            fontSize:9, fontWeight:800, padding:"3px 9px",
                                            borderRadius:99,
                                            background: isOpen ? "rgba(220,38,38,.12)" : "rgba(202,138,4,.12)",
                                            color: isOpen ? "#b91c1c" : "#a16207",
                                            letterSpacing:".05em",
                                          }}>
                                            {b.state || "OPEN"}
                                          </span>
                                        </td>
                                        <td style={{ padding:"10px 12px", textAlign:"right" }}>
                                          <button onClick={() => setBdModal({
                                                  ticket: b, mode: "fill", phase: "production" })}
                                                  style={{
                                                    background:"linear-gradient(135deg,#dc2626,#b91c1c)",
                                                    color:"#fff", border:"none",
                                                    padding:"7px 14px", borderRadius: 7,
                                                    fontSize: 11, fontWeight: 800, cursor:"pointer",
                                                    whiteSpace:"nowrap",
                                                  }}>
                                            Fill Form
                                          </button>
                                        </td>
                                      </tr>
                                    );
                                  })}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })()}

          {/* Summary Stats
              2026-05-13 — operator spec: replace the single "Avg OEE"
              tile with a per-zone OEE breakdown.  Each visible zone
              gets its own tile coloured by its OEE band (good/avg/poor).
              Total / Running / Stopped stay as before.  NPD tile is
              rendered separately just below this grid. */}
          {!loading && lines.length > 0 && (
            <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fill,minmax(160px,1fr))", gap:16, marginBottom:36 }}>
              {[
                { label: selZones.length>0 ? "Lines in View" : "Total Lines", val: summary.total,        color:"#1e40af" },
                { label: "Running Now",                                         val: summary.running,      color:"#16a34a" },
                { label: "Stopped",                                             val: summary.stopped,      color:"#dc2626" },
              ].map(({ label, val, color }) => (
                <div key={label} style={{
                  background:"#fff", border:"1px solid #e2e8f0", borderRadius:12,
                  padding:20, position:"relative", overflow:"hidden",
                  boxShadow:"0 1px 3px rgba(0,0,0,0.05)",
                }}>
                  <div style={{ position:"absolute", top:0, left:0, right:0, height:3, background:`linear-gradient(90deg,${color},${color}88)` }} />
                  <div style={{ fontSize:34, fontWeight:500, color:"#0f172a", marginBottom:4 }}>{val}</div>
                  <div style={{ fontSize:12, color:"#64748b" }}>{label}</div>
                </div>
              ))}

              {/* ── Per-zone OEE tiles ─────────────────────────────
                  One tile per zone in view.  OEE colour band:
                    ≥75% green   ≥60% amber   else red.
                  Tile shows zone name + OEE% (big) + "N lines · M running".
                  When a zone has no live data yet, OEE shows "—" so
                  the tile doesn't lie about a 0% reading. */}
              {(summary.zoneOees || []).map(z => {
                const oeeNum  = Number(z.oee);
                const oeeBand = !z.has_data ? "#94a3b8"
                              : oeeNum >= 75 ? "#16a34a"
                              : oeeNum >= 60 ? "#d97706"
                              : "#dc2626";
                return (
                  <div key={`zone-oee-${z.zone_id}`} style={{
                    background:"#fff", border:`1px solid ${oeeBand}33`, borderRadius:12,
                    padding:20, position:"relative", overflow:"hidden",
                    boxShadow:"0 1px 3px rgba(0,0,0,0.05)",
                  }}>
                    <div style={{ position:"absolute", top:0, left:0, right:0, height:3,
                      background:`linear-gradient(90deg,${oeeBand},${oeeBand}88)` }} />
                    <div style={{ fontSize:11, color:"#64748b", fontWeight:700, letterSpacing:".05em",
                      textTransform:"uppercase", marginBottom:2 }}>
                      {z.zone_name}
                    </div>
                    <div style={{ fontSize:34, fontWeight:500, color: z.has_data ? oeeBand : "#94a3b8", marginBottom:4, lineHeight:1.05 }}>
                      {z.has_data ? `${z.oee}%` : "—"}
                    </div>
                    <div style={{ fontSize:11, color:"#64748b" }}>OEE</div>
                  </div>
                );
              })}

              {/* Non-Production tile */}
              <div style={{
                background: summary.npdCount>0 ? "rgba(251,191,36,.06)" : "#fff",
                border: `1px solid ${summary.npdCount>0 ? "rgba(217,119,6,.35)" : "#e2e8f0"}`,
                borderRadius:12, padding:20, position:"relative", overflow:"hidden",
                boxShadow:"0 1px 3px rgba(0,0,0,0.05)",
                gridColumn: summary.npdLines.length>2 ? "span 2" : "span 1",
              }}>
                <div style={{ position:"absolute", top:0, left:0, right:0, height:3, background: summary.npdCount>0 ? "linear-gradient(90deg,#d97706,#fbbf24)" : "linear-gradient(90deg,#94a3b8,#cbd5e1)" }} />
                <div style={{ display:"flex", alignItems:"baseline", gap:8, marginBottom:4 }}>
                  <div style={{ fontSize:34, fontWeight:500, color: summary.npdCount>0 ? "#d97706" : "#0f172a" }}>{summary.npdCount}</div>
                  {summary.npdCount>0 && <span style={{ fontSize:16 }}>⛔</span>}
                </div>
                <div style={{ fontSize:12, color:"#64748b", marginBottom: summary.npdLines.length>0 ? 10 : 0 }}>
                  Non-Production {selZones.length>0 ? "(Zone)" : "Lines"}
                </div>
                {summary.npdLines.map((l,i) => (
                  <div key={i} style={{
                    display:"flex", alignItems:"center", gap:6,
                    fontSize:11, color:"#92400e", fontWeight:600,
                    background:"rgba(217,119,6,.1)", borderRadius:5,
                    padding:"3px 8px", marginTop:4,
                  }}>
                    <span>⛔</span>
                    {l.name}
                    {l.reason && <span style={{ fontWeight:400, color:"#b45309", marginLeft:4 }}>— {l.reason}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Lines */}
          {loading ? (
            <div className="empty-state">
              <div style={{ margin:"0 auto 16px", width:36, height:36, borderRadius:"50%", border:"3px solid #e2e8f0", borderTopColor:"#1e40af", animation:"spin .6s linear infinite" }} />
              <div style={{ fontSize:15, fontWeight:600, color:"#64748b" }}>Loading lines…</div>
            </div>
          ) : visibleLines.length === 0 ? (
            <div className="empty-state">
              <div style={{ fontSize:48, opacity:.3, marginBottom:16 }}>⬡</div>
              <div style={{ fontSize:15, fontWeight:600, color:"#64748b" }}>No lines found</div>
              <div style={{ fontSize:13, color:"#94a3b8", marginTop:6 }}>
                {lines.length===0 ? "No lines configured. Go to Admin → Production Lines." : "No lines assigned to this zone."}
              </div>
            </div>
          ) : (
            <>
              {grouped.map(({ zone, zoneLines }) => (
                zone && zoneLines.length > 0 && (
                  <div className="zone-section" key={zone.id}>
                    <div className="zone-lbl">
                      <div className="zone-lbl-text">{zone.zone_name || zone.name}</div>
                      <div className="zone-lbl-badge">{zoneLines.length} line{zoneLines.length !== 1 ? "s" : ""}</div>
                      <div className="zone-lbl-line" />
                    </div>
                    <div className="lines-grid">
                      {zoneLines.map(line => (
                        <LineCard key={line.id} line={line} globalStatus={globalStatus} token={token} user={user} onRtUpdate={onRtUpdate} onNpdUpdate={onNpdUpdate} />
                      ))}
                    </div>
                  </div>
                )
              ))}
              {unassigned.length > 0 && (
                <div className="zone-section">
                  <div className="zone-lbl">
                    <div className="zone-lbl-text" style={{ color:"#94a3b8" }}>Unassigned</div>
                    <div className="zone-lbl-badge" style={{ background:"#f1f5f9", color:"#94a3b8", borderColor:"#e2e8f0" }}>
                      {unassigned.length} line{unassigned.length !== 1 ? "s" : ""}
                    </div>
                    <div className="zone-lbl-line" />
                  </div>
                  <div className="lines-grid">
                    {unassigned.map(line => (
                      <LineCard key={line.id} line={line} globalStatus={globalStatus} token={token} user={user} onRtUpdate={onRtUpdate} onNpdUpdate={onNpdUpdate} />
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <AIAssistant pageContext={{ page: "Dashboard", lines }} />

      {/* Break-down slip modal — Production phase (upper half editable) */}
      {bdModal && (
        <ClosureFormModal
          ticket={bdModal.ticket}
          mode={bdModal.mode}
          phase={bdModal.phase}
          token={token}
          onClose={() => setBdModal(null)}
          onSave={onProductionSave}
        />
      )}
    </>
  );
}