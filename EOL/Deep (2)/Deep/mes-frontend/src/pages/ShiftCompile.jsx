// pages/ShiftCompile.jsx — Shift Compile (light theme)
//   Zone buttons → Line buttons (drill-down).  For the chosen line + shift:
//   full hourly data with per-hour losses (speed / breakdown) + operator loss
//   comments, production summary, manpower per machine, and a slide-to-close
//   sign-off for the line leader.  Heads see every line's close status at a
//   glance via the coloured dot on each line button.
// Backed by /api/shift-compile/* (separate sign-off table; counting untouched).
import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import PageTopbar from "../components/PageTopbar";
import AIAssistant from "../components/AIAssistant";
import ShiftRollup from "../components/ShiftRollup";

// Roles that get the COMPILED roll-up module (multi-line / zone totals +
// historical + download).  Operator + leader keep the per-line view only.
const ROLLUP_ROLES = ["shift_incharge", "section_incharge", "admin",
                      "plant_head", "production_incharge"];

const today = () => new Date().toISOString().slice(0, 10);

const STATUS = {
  running:       { label: "Running",     fg: "#1d4ed8", bg: "#eff6ff", dot: "#3b82f6" },
  pending:       { label: "Not closed",  fg: "#b91c1c", bg: "#fef2f2", dot: "#ef4444" },
  closed_ontime: { label: "Closed · on-time", fg: "#15803d", bg: "#f0fdf4", dot: "#16a34a" },
  closed_late:   { label: "Closed · LATE",     fg: "#b45309", bg: "#fffbeb", dot: "#f59e0b" },
};

// After a shift's scheduled end passes we hold AMBER for CLOSE_GRACE_MIN minutes
// ("shift ended — close it") and only THEN escalate to RED, deepening the red
// every hour it stays un-closed.  Operator spec: "jab A shift nikal gayi to
// dikhaye ki close nahi hui, hour-wise red ho jaye kuch time baad".
const CLOSE_GRACE_MIN = 30;
// Live overdue state for an un-closed row (computed from scheduled_end vs now).
// null = closed / still running / no schedule → not overdue.
function overdueState(row, nowMs) {
  if (!row || row.closed || !row.scheduled_end) return null;
  const end = new Date(row.scheduled_end).getTime();
  if (isNaN(end) || nowMs < end) return null;            // shift still in progress
  const min = Math.floor((nowMs - end) / 60000);
  const hrs = min / 60;
  if (min <= CLOSE_GRACE_MIN)                              // grace window → amber
    return { min, hrs, red: false, color: "#f59e0b", bg: "#fffbeb", fg: "#b45309", label: "Ended — not closed" };
  // escalate: deeper red each hour it stays open
  const color = hrs >= 3 ? "#7f1d1d" : hrs >= 2 ? "#991b1b" : hrs >= 1 ? "#dc2626" : "#ef4444";
  return { min, hrs, red: true, color, bg: hrs >= 2 ? "#fee2e2" : "#fef2f2", fg: color, label: "NOT CLOSED — overdue" };
}
function fmtOverdue(min) {
  if (min < 60) return `${min}m overdue`;
  return `${Math.floor(min / 60)}h ${min % 60}m overdue`;
}

function Badge({ status }) {
  const s = STATUS[status] || STATUS.running;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6,
      background: s.bg, color: s.fg, padding: "3px 10px", borderRadius: 999,
      fontSize: 12, fontWeight: 700, whiteSpace: "nowrap" }}>
      <span style={{ width: 7, height: 7, borderRadius: 999, background: s.dot }} />
      {s.label}
    </span>
  );
}

function KPI({ label, value, sub, accent }) {
  return (
    <div style={{ background: "#f8fafc", border: "1px solid #e5e7eb",
      borderRadius: 12, padding: "12px 14px", minWidth: 108 }}>
      <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase",
        letterSpacing: .5, fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 800, color: accent || "#0f172a", lineHeight: 1.2 }}>
        {value ?? "—"}</div>
      {sub != null && <div style={{ fontSize: 11, color: "#94a3b8" }}>{sub}</div>}
    </div>
  );
}

/* ── Slide-to-close (light) ─────────────────────────────────────────── */
function SlideToClose({ onConfirm, disabled }) {
  const trackRef = useRef(null);
  const [x, setX] = useState(0);
  const [done, setDone] = useState(false);
  const dragging = useRef(false);
  const KNOB = 46;
  const maxX = () => Math.max(0, (trackRef.current?.clientWidth || 320) - KNOB - 6);
  const pmove = useCallback((e) => {
    if (!dragging.current || !trackRef.current) return;
    const rect = trackRef.current.getBoundingClientRect();
    setX(Math.min(maxX(), Math.max(0, e.clientX - rect.left - KNOB / 2)));
  }, []);
  const up = useCallback(() => {
    if (!dragging.current) return;
    dragging.current = false;
    setX((cur) => { if (cur >= maxX() * 0.9) { setDone(true); onConfirm?.(); return maxX(); } return 0; });
    window.removeEventListener("pointermove", pmove);
    window.removeEventListener("pointerup", up);
  }, [pmove, onConfirm]);
  const down = () => {
    if (disabled || done) return;
    dragging.current = true;
    window.addEventListener("pointermove", pmove);
    window.addEventListener("pointerup", up);
  };
  useEffect(() => () => {
    window.removeEventListener("pointermove", pmove);
    window.removeEventListener("pointerup", up);
  }, [pmove, up]);
  const pct = maxX() ? x / maxX() : 0;
  return (
    <div ref={trackRef} style={{ position: "relative", height: 52, borderRadius: 999,
      background: done ? "#f0fdf4" : "#f1f5f9", border: `1px solid ${done ? "#86efac" : "#cbd5e1"}`,
      overflow: "hidden", userSelect: "none", touchAction: "none",
      opacity: disabled ? 0.5 : 1, maxWidth: 420 }}>
      <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center",
        justifyContent: "center", color: done ? "#15803d" : "#64748b", fontWeight: 700,
        fontSize: 14, opacity: 1 - pct * 0.9 }}>
        {done ? "✓ Shift closed" : "Slide to close shift  →"}</div>
      <div onPointerDown={down}
        style={{ position: "absolute", top: 3, left: 3, width: KNOB, height: KNOB,
          borderRadius: 999, background: done ? "#16a34a" : "#2563eb",
          transform: `translateX(${x}px)`, transition: dragging.current ? "none" : "transform .18s ease",
          display: "flex", alignItems: "center", justifyContent: "center",
          color: "#fff", fontSize: 20, cursor: disabled ? "not-allowed" : "grab",
          boxShadow: "0 2px 6px rgba(0,0,0,.2)" }}>{done ? "✓" : "⟩"}</div>
    </div>
  );
}

export default function ShiftCompile() {
  const { token, user, canAccessModule } = useAuth();
  const [alarmVid, setAlarmVid] = useState(null);   // NG part {cycle_seq, part_code} → clip modal
  const [lines, setLines]   = useState([]);
  const [zoneSel, setZoneSel] = useState("");
  const [lineId, setLineId] = useState("");
  const [date, setDate]     = useState(today);
  const [shift, setShift]   = useState("A");
  const [boardShift, setBoardShift] = useState("A");   // top filter for the shift-status board
  // 2026-09-19 — two modules on this page, each assignable in Admin → Users →
  // Access (utils/pageModules.js).  Line Report = everything a line leader
  // uses; Compile Report = the roll-up, still limited to ROLLUP_ROLES as before.
  const [scTab, setScTab] = useState("line");
  const [nowMs, setNowMs]   = useState(Date.now());    // live clock → overdue escalation
  useEffect(() => { const t = setInterval(() => setNowMs(Date.now()), 30000); return () => clearInterval(t); }, []);
  const [ov, setOv]         = useState({ rows: [] });
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving]   = useState(false);
  const [toast, setToast]     = useState(null);

  const flash = (msg, kind = "ok") => { setToast({ msg, kind }); setTimeout(() => setToast(null), 3500); };

  // zones grouped from lines
  const zones = useMemo(() => {
    const m = new Map();
    lines.forEach(l => {
      const z = l.zone_name || "— No zone —";
      if (!m.has(z)) m.set(z, []);
      m.get(z).push(l);
    });
    return [...m.entries()].map(([name, ls]) => ({ name, lines: ls }));
  }, [lines]);

  const zoneLines = zones.find(z => z.name === zoneSel)?.lines || [];

  // shift options for the selected line come from the overview rows
  const lineShifts = useMemo(
    () => (ov.rows || []).filter(r => String(r.line_id) === String(lineId)),
    [ov, lineId]);
  // keep `shift` valid for the chosen line (default = first production shift)
  useEffect(() => {
    if (!lineShifts.length) return;
    if (!lineShifts.some(s => s.shift_name === shift)) {
      const prod = lineShifts.find(s => s.type === "prod") || lineShifts[0];
      setShift(prod.shift_name);
    }
  }, [lineShifts]);   // eslint-disable-line
  // line-button dot = that line's first production shift status
  const lineStatus = useCallback((lid) => {
    const rs = (ov.rows || []).filter(r => String(r.line_id) === String(lid));
    return (rs.find(r => r.type === "prod") || rs[0])?.status || "running";
  }, [ov]);
  const shiftLabel = lineShifts.find(s => s.shift_name === shift)?.label || shift;

  // ── Shift-status BOARD (all lines for the chosen date + shift) ──────────
  // Production shifts only (A/B…) drive the board filter — OT/non-prod windows
  // stay in the per-line detail below.
  const boardShiftOpts = useMemo(() => {
    const seen = new Map();
    (ov.rows || []).forEach(r => {
      if (r.type === "prod" && !seen.has(r.shift_name)) seen.set(r.shift_name, r.label || r.shift_name);
    });
    return [...seen.entries()].map(([shift_name, label]) => ({ shift_name, label }))
      .sort((a, b) => a.shift_name.localeCompare(b.shift_name));
  }, [ov.rows]);
  useEffect(() => {
    if (boardShiftOpts.length && !boardShiftOpts.some(s => s.shift_name === boardShift))
      setBoardShift(boardShiftOpts[0].shift_name);
  }, [boardShiftOpts]);   // eslint-disable-line
  const boardRows = useMemo(() => {
    const rows = (ov.rows || []).filter(r => r.type === "prod" && r.shift_name === boardShift);
    const rank = (r) => r.closed ? 2 : (overdueState(r, nowMs) ? 0 : 1);   // overdue → running → closed
    return rows.slice().sort((a, b) => {
      const ra = rank(a), rb = rank(b);
      if (ra !== rb) return ra - rb;
      return (overdueState(b, nowMs)?.min || 0) - (overdueState(a, nowMs)?.min || 0);   // most overdue first
    });
  }, [ov.rows, boardShift, nowMs]);
  const overdueCount = useMemo(() => boardRows.filter(r => overdueState(r, nowMs)).length, [boardRows, nowMs]);
  // click a board card → drill into that line + shift detail below
  const openLine = (r) => {
    const z = zones.find(zz => zz.lines.some(l => String(l.id) === String(r.line_id)));
    if (z) setZoneSel(z.name);
    setLineId(String(r.line_id));
    setShift(r.shift_name);
  };

  useEffect(() => {
    (async () => {
      try {
        const ls = await api.get("/api/lines/", token);
        setLines(ls || []);
        if (!zoneSel && ls?.length) {
          const z = ls[0].zone_name || "— No zone —";
          setZoneSel(z);
          setLineId(String(ls[0].id));
        }
      } catch (e) { flash("Lines load failed: " + e.message, "err"); }
    })();
  }, [token]);

  const loadOv = useCallback(async () => {
    try { setOv(await api.get(`/api/shift-compile/overview?date=${date}`, token) || { rows: [] }); }
    catch { /* silent */ }
  }, [date, token]);
  useEffect(() => { loadOv(); }, [loadOv]);

  const loadDetail = useCallback(async () => {
    if (!lineId) { setDetail(null); return; }
    setLoading(true);
    try {
      const d = await api.get(`/api/shift-compile/detail?line_id=${lineId}&date=${date}&shift=${shift}`, token);
      setDetail(d);
    } catch (e) { flash("Detail failed: " + e.message, "err"); setDetail(null); }
    finally { setLoading(false); }
  }, [lineId, date, shift, token]);
  useEffect(() => { loadDetail(); }, [loadDetail]);

  const doClose = async () => {
    if (!detail) return;
    setSaving(true);
    try {
      const r = await api.post("/api/shift-compile/close",
        { line_id: Number(lineId), record_date: date, shift_name: detail.shift_name }, token);
      flash(r.on_time ? "Shift closed — on time ✓" : "Shift closed (late)", r.on_time ? "ok" : "warn");
      await loadDetail(); await loadOv();
    } catch (e) { flash("Close failed: " + (e.message || "error"), "err"); }
    finally { setSaving(false); }
  };
  const reopen = async () => {
    try {
      await api.post("/api/shift-compile/reopen",
        { line_id: detail.line_id, record_date: date, shift_name: detail.shift_name }, token);
      flash("Reopened"); await loadDetail(); await loadOv();
    } catch (e) { flash("Reopen failed: " + e.message, "err"); }
  };

  // ── ASSIGN actions: per-line OT + Non-Production Day ──────────────────
  const toggleOT = async () => {
    if (!detail) return;
    const turningOn = !detail.ot_active;
    let end = null;
    if (turningOn) {
      end = window.prompt("OT end time for this shift (HH:MM, 24-hour):", "");
      if (end === null) return;                 // cancelled
    } else if (!window.confirm("Turn OFF overtime for this line's shift?")) {
      return;
    }
    setSaving(true);
    try {
      await api.post("/api/shift-compile/ot",
        { line_id: Number(lineId), shift_name: detail.shift_name,
          enable: turningOn, ot_end_time: end || null }, token);
      flash(turningOn ? "OT turned ON ✓" : "OT turned OFF");
      await loadDetail(); await loadOv();
    } catch (e) { flash("OT failed: " + (e.message || "error"), "err"); }
    finally { setSaving(false); }
  };
  const toggleNPD = async () => {
    if (!detail) return;
    const turningOn = !(detail.npd && detail.npd.marked);
    let reason = null;
    if (turningOn) {
      reason = window.prompt("Reason for marking this a Non-Production Day:", "");
      if (reason === null) return;
    } else if (!window.confirm("Remove the Non-Production Day mark for this line/date?")) {
      return;
    }
    setSaving(true);
    try {
      await api.post("/api/shift-compile/npd",
        { line_id: Number(lineId), record_date: date, shift_name: null,
          enable: turningOn, reason: reason || null }, token);
      flash(turningOn ? "Marked Non-Production Day ✓" : "Non-Production Day removed");
      await loadDetail(); await loadOv();
    } catch (e) { flash("NPD update failed: " + (e.message || "error"), "err"); }
    finally { setSaving(false); }
  };

  const p = detail?.production || {};
  const totLoss = (detail?.hourly || []).reduce((a, h) => a + (h.loss_min || 0), 0);

  return (
    <div style={{ padding: "18px 22px 60px", color: "#0f172a" }}>
      <PageTopbar leading="Shift" accent="Compile" />

      <style>{`@keyframes scPulse{0%,100%{box-shadow:0 0 0 0 rgba(220,38,38,0)}50%{box-shadow:0 0 0 4px rgba(220,38,38,.18)}}`}</style>

      {/* ── COMPILED ROLL-UP (shift/section incharge, heads) ─────────────────
          Multi-line / zone totals, per-model breakdown, manpower, and the
          historical date-to-date + shift filter with Excel/PDF download.
          Operator + leader don't see this — they use the per-line detail. */}
      {(() => {
        const canMod = m => (canAccessModule ? canAccessModule("shift-compile", m) : true);
        const showCompile = ROLLUP_ROLES.includes(user?.role) && canMod("compile");
        const showLine = canMod("line");
        const tab = !showLine ? "compile" : (!showCompile ? "line" : scTab);
        const tabBtn = (key, label) => (
          <button key={key} onClick={() => setScTab(key)}
                  style={{ padding: "9px 18px", border: "none", background: "none", cursor: "pointer",
                           fontSize: 13.5, fontWeight: tab === key ? 800 : 600,
                           color: tab === key ? "#1e40af" : "#94a3b8",
                           borderBottom: tab === key ? "3px solid #1e40af" : "3px solid transparent",
                           marginBottom: -2 }}>{label}</button>
        );
        return (<>
          {/* Tabs only when this user has both modules — anyone with one sees
              the page exactly as before, with no tab bar. */}
          {showCompile && showLine && (
            <div style={{ display: "flex", gap: 6, margin: "6px 0 14px", borderBottom: "2px solid #e2e8f0" }}>
              {tabBtn("line", "Line Report")}
              {tabBtn("compile", "Compile Report")}
            </div>
          )}
          {/* Both stay mounted and are only hidden, so switching tabs keeps each
              module's own filters and loaded data exactly as they were. */}
          {/* Compile Report runs on its own From / To / Shift filter; the date +
              shift filter below belongs to Line Report only. */}
          {showCompile && (
            <div style={{ display: tab === "compile" ? undefined : "none" }}>
              <ShiftRollup />
            </div>
          )}
          {!showLine && !showCompile && (
            <div style={{ ...card, color: "#64748b", fontSize: 13 }}>
              No Shift Compile module is assigned to you — ask an admin (Users → Access).
            </div>
          )}
          <div style={{ display: showLine && tab === "line" ? undefined : "none" }}>

      {/* date + shift filter (drives the status board) */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", margin: "14px 0 8px" }}>
        <input type="date" value={date} onChange={e => setDate(e.target.value)} style={inp} />
        <span style={{ fontSize: 12, fontWeight: 700, color: "#64748b", marginLeft: 4 }}>SHIFT</span>
        {boardShiftOpts.map(s => (
          <button key={s.shift_name} onClick={() => setBoardShift(s.shift_name)} style={pill(boardShift === s.shift_name)}>{s.label}</button>
        ))}
      </div>

      {/* ── SHIFT-STATUS BOARD — every line for the chosen date + shift ──────
          Un-closed shifts whose scheduled end has passed escalate: amber for
          the first CLOSE_GRACE_MIN, then red, deepening each hour overdue. */}
      <div style={{ ...card, marginBottom: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
          <div style={{ fontSize: 14, fontWeight: 800 }}>
            Shift status · {date} · {boardShiftOpts.find(s => s.shift_name === boardShift)?.label || boardShift}
          </div>
          {overdueCount > 0
            ? <span style={{ background: "#fef2f2", color: "#b91c1c", border: "1px solid #fecaca", padding: "3px 10px", borderRadius: 999, fontSize: 12, fontWeight: 800 }}>⚠ {overdueCount} not closed</span>
            : <span style={{ background: "#f0fdf4", color: "#15803d", border: "1px solid #bbf7d0", padding: "3px 10px", borderRadius: 999, fontSize: 12, fontWeight: 700 }}>All accounted for</span>}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(230px,1fr))", gap: 10 }}>
          {boardRows.map(r => {
            const od = overdueState(r, nowMs);
            const st = r.closed ? (r.on_time ? STATUS.closed_ontime : STATUS.closed_late) : STATUS.running;
            const color = od ? od.color : st.dot;
            const on = String(lineId) === String(r.line_id) && shift === r.shift_name;
            return (
              <button key={r.line_id} onClick={() => openLine(r)} title="Open this line's shift detail"
                style={{ textAlign: "left", cursor: "pointer", background: od ? od.bg : st.bg,
                         border: `1px solid ${color}55`, borderLeft: `4px solid ${color}`,
                         outline: on ? `2px solid ${color}` : "none", borderRadius: 12, padding: "10px 12px",
                         animation: od && od.hrs >= 2 ? "scPulse 1.4s ease-in-out infinite" : undefined }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                  <span style={{ fontWeight: 800, fontSize: 14 }}>{r.line_name}</span>
                  <span style={{ width: 9, height: 9, borderRadius: 999, background: color }} />
                </div>
                <div style={{ fontSize: 12, fontWeight: 700, color: od ? od.fg : st.fg, marginTop: 3 }}>
                  {r.closed ? `Closed ${r.on_time ? "· on-time" : "· LATE"}` : od ? `${od.label} · ${fmtOverdue(od.min)}` : "Running"}
                </div>
                <div style={{ fontSize: 11, color: "#64748b", marginTop: 4 }}>
                  {r.closed && r.closed_by ? `by ${r.closed_by}`
                    : r.scheduled_end ? `${od ? "ended" : "ends"} ${new Date(r.scheduled_end).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : ""}
                  {r.ok != null ? ` · OK ${r.ok}${r.ng ? ` / NG ${r.ng}` : ""}` : ""}
                </div>
              </button>
            );
          })}
          {boardRows.length === 0 && <span style={{ color: "#94a3b8", fontSize: 13 }}>No lines for this shift.</span>}
        </div>
      </div>

      {/* ZONE buttons */}
      <div style={{ fontSize: 12, fontWeight: 700, color: "#64748b", margin: "10px 0 6px" }}>ZONE</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        {zones.map(z => (
          <button key={z.name} onClick={() => { setZoneSel(z.name); if (z.lines[0]) setLineId(String(z.lines[0].id)); }}
            style={pill(zoneSel === z.name)}>{z.name} <span style={{ opacity: .6 }}>({z.lines.length})</span></button>
        ))}
        {zones.length === 0 && <span style={{ color: "#94a3b8" }}>Loading…</span>}
      </div>

      {/* LINE buttons (with status dot) */}
      <div style={{ fontSize: 12, fontWeight: 700, color: "#64748b", margin: "6px 0 6px" }}>LINE</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 18 }}>
        {zoneLines.map(l => {
          const st = STATUS[lineStatus(l.id)] || STATUS.running;
          const on = String(lineId) === String(l.id);
          return (
            <button key={l.id} onClick={() => setLineId(String(l.id))}
              style={{ ...pill(on), display: "inline-flex", alignItems: "center", gap: 7 }}>
              <span style={{ width: 8, height: 8, borderRadius: 999, background: st.dot }} />
              {l.line_name}
            </button>
          );
        })}
      </div>

      {/* SHIFT buttons for the chosen line — production, OT, non-production */}
      {lineShifts.length > 0 && (<>
        <div style={{ fontSize: 12, fontWeight: 700, color: "#64748b", margin: "6px 0 6px" }}>SHIFT</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 18 }}>
          {lineShifts.map(s => {
            const on = s.shift_name === shift;
            const st = STATUS[s.status] || STATUS.running;
            const c = SHIFT_TYPE[s.type] || SHIFT_TYPE.prod;
            return (
              <button key={s.shift_name} onClick={() => setShift(s.shift_name)}
                style={{ ...pillC(on, c), display: "inline-flex", alignItems: "center", gap: 7 }}>
                <span style={{ width: 8, height: 8, borderRadius: 999, background: st.dot }} />
                {s.label}
              </button>
            );
          })}
        </div>
      </>)}

      {loading && <div style={{ color: "#94a3b8", padding: 16 }}>Loading…</div>}

      {detail && !loading && (
        <div style={card}>
          {/* header + close */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
            flexWrap: "wrap", gap: 10, marginBottom: 14 }}>
            <div style={{ fontSize: 17, fontWeight: 800 }}>
              {detail.line_name} <span style={{ color: "#94a3b8", fontWeight: 600 }}>· {shiftLabel} · {detail.record_date}</span>
            </div>
            <Badge status={detail.close.closed ? (detail.close.on_time ? "closed_ontime" : "closed_late")
              : (detail.scheduled_end && new Date() < new Date(detail.scheduled_end) ? "running" : "pending")} />
          </div>

          {/* KPIs */}
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 18 }}>
            <KPI label="OK" value={p.ok ?? "—"} accent="#16a34a" />
            <KPI label="NG" value={p.ng ?? "—"} accent={p.ng ? "#dc2626" : "#0f172a"} />
            <KPI label="Plan" value={p.plan ?? "—"}
              sub={p.plan && p.ok != null ? `${Math.round((p.ok / p.plan) * 100)}% done` : null} />
            <KPI label="OEE" value={p.oee != null ? p.oee.toFixed(1) + "%" : "—"}
              sub={p.availability != null ? `A${p.availability.toFixed(0)} P${p.performance?.toFixed(0)} Q${p.quality?.toFixed(0)}` : null} />
            <KPI label="Total loss" value={totLoss ? totLoss.toFixed(0) + "m" : "0"} accent={totLoss ? "#b45309" : "#0f172a"} />
            <KPI label="Manpower" value={detail.manpower_total} sub={`${detail.punched_in} punched-in`} />
            <KPI label="Comments" value={detail.comment_total ?? 0}
              sub={`${detail.comment_counts?.length || 0} machine${(detail.comment_counts?.length || 0) === 1 ? "" : "s"}`}
              accent={detail.comment_total ? "#7c3aed" : "#0f172a"} />
          </div>

          {/* HOURLY table with losses + comments */}
          <div style={secTitle}>Hourly data — production &amp; losses</div>
          <div style={{ overflowX: "auto", marginBottom: 20 }}>
            <table style={tbl}>
              <thead><tr>
                {["Hour", "Plan", "Actual", "Gap", "Speed loss", "Breakdown", "Loss comments"].map(h =>
                  <th key={h} style={th}>{h}</th>)}
              </tr></thead>
              <tbody>
                {(detail.hourly || []).map((h, i) => (
                  <tr key={i} style={{ borderTop: "1px solid #eef2f7" }}>
                    <td style={{ ...td, fontWeight: 600, whiteSpace: "nowrap" }}>{h.time}</td>
                    <td style={td}>{h.plan}</td>
                    <td style={{ ...td, fontWeight: 700 }}>{h.actual}</td>
                    <td style={{ ...td, color: h.gap > 0 ? "#dc2626" : "#16a34a" }}>{h.gap > 0 ? "-" + h.gap : h.gap === 0 ? "0" : "+" + (-h.gap)}</td>
                    <td style={{ ...td, color: h.speed_loss_min ? "#b45309" : "#cbd5e1" }}>{h.speed_loss_min ? h.speed_loss_min + "m" : "—"}</td>
                    <td style={{ ...td, color: h.breakdown_loss_min ? "#dc2626" : "#cbd5e1" }}>{h.breakdown_loss_min ? h.breakdown_loss_min + "m" : "—"}</td>
                    <td style={{ ...td, color: "#334155", maxWidth: 360 }}>{h.remarks || <span style={{ color: "#cbd5e1" }}>—</span>}</td>
                  </tr>
                ))}
                {(!detail.hourly || detail.hourly.length === 0) &&
                  <tr><td style={td} colSpan={7}><span style={{ color: "#94a3b8" }}>No hourly data for this shift yet.</span></td></tr>}
              </tbody>
            </table>
          </div>

          {/* MODEL runs */}
          <div style={secTitle}>Model runs — which model ran, when &amp; how many parts</div>
          <div style={{ overflowX: "auto", marginBottom: 20 }}>
            <table style={tbl}>
              <thead><tr>{["Model", "From", "To", "Duration", "OK", "NG"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
              <tbody>
                {(detail.model_runs || []).map((m, i) => (
                  <tr key={i} style={{ borderTop: "1px solid #eef2f7" }}>
                    <td style={{ ...td, fontWeight: 600 }}>{m.model_name || ("Model " + m.model_number)}</td>
                    <td style={{ ...td, whiteSpace: "nowrap" }}>{m.from}</td>
                    <td style={{ ...td, whiteSpace: "nowrap" }}>{m.to}</td>
                    <td style={{ ...td, whiteSpace: "nowrap" }}>{m.duration_min}m</td>
                    <td style={{ ...td, color: "#16a34a", fontWeight: 700 }}>{m.ok}</td>
                    <td style={{ ...td, color: m.ng ? "#dc2626" : "#94a3b8" }}>{m.ng}</td>
                  </tr>
                ))}
                {(!detail.model_runs || detail.model_runs.length === 0) &&
                  <tr><td style={td} colSpan={6}><span style={{ color: "#94a3b8" }}>No model-run data for this shift.</span></td></tr>}
              </tbody>
            </table>
          </div>

          {/* losses summary — shift total by category */}
          <div style={secTitle}>Losses — shift total by category</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 20 }}>
            {[["Breakdown", "breakdown", "#dc2626"], ["Speed", "speed", "#2563eb"],
              ["Quality", "quality", "#7c3aed"], ["Material", "material", "#0e7490"],
              ["Setup", "setup", "#b45309"], ["Change-over", "change_over", "#0891b2"],
              ["Others", "others", "#64748b"]].map(([lbl, k, c]) => {
              const v = detail.losses?.[k] || 0;
              return (
                <div key={k} style={{ border: `1px solid ${v ? c : "#e5e7eb"}`, borderRadius: 10,
                  padding: "8px 14px", minWidth: 92, background: v ? "#fff" : "#f8fafc" }}>
                  <div style={{ fontSize: 11, color: "#64748b", fontWeight: 700 }}>{lbl}</div>
                  <div style={{ fontSize: 18, fontWeight: 800, color: v ? c : "#94a3b8" }}>{v ? v + "m" : "—"}</div>
                </div>
              );
            })}
            <div style={{ border: "1px solid #0f172a", borderRadius: 10, padding: "8px 14px",
              minWidth: 92, background: "#0f172a" }}>
              <div style={{ fontSize: 11, color: "#cbd5e1", fontWeight: 700 }}>Total loss</div>
              <div style={{ fontSize: 18, fontWeight: 800, color: "#fff" }}>{detail.losses?.total ? detail.losses.total + "m" : "0"}</div>
            </div>
          </div>

          {/* breakdowns this shift — from andon */}
          <div style={secTitle}>Breakdowns — this shift (andon calls)</div>
          <div style={{ overflowX: "auto", marginBottom: 20 }}>
            <table style={tbl}>
              <thead><tr>{["Machine", "Type", "Priority", "Down (min)", "Start", "End", "Fault", "Model"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
              <tbody>
                {(detail.breakdowns || []).map((b, i) => (
                  <tr key={i} style={{ borderTop: "1px solid #eef2f7" }}>
                    <td style={{ ...td, fontWeight: 600 }}>{b.machine}</td>
                    <td style={td}>{b.type || "—"}</td>
                    <td style={td}>{b.priority || "—"}</td>
                    <td style={{ ...td, color: b.downtime_min ? "#dc2626" : "#94a3b8", fontWeight: 700 }}>{b.downtime_min ?? "—"}</td>
                    <td style={{ ...td, whiteSpace: "nowrap" }}>{b.start || "—"}</td>
                    <td style={{ ...td, whiteSpace: "nowrap" }}>{b.ongoing ? <span style={{ color: "#16a34a", fontWeight: 700 }}>ONGOING</span> : (b.ok || "—")}</td>
                    <td style={{ ...td, maxWidth: 220, color: "#334155" }}>{b.fault || "—"}</td>
                    <td style={{ ...td, color: "#64748b" }}>{b.model || "—"}</td>
                  </tr>
                ))}
                {(!detail.breakdowns || detail.breakdowns.length === 0) &&
                  <tr><td style={td} colSpan={8}><span style={{ color: "#94a3b8" }}>Is shift me koi breakdown nahi.</span></td></tr>}
              </tbody>
            </table>
          </div>

          {/* Alarm / NG parts — details + video (2026-09-01) */}
          <div style={secTitle}>Alarm / NG parts — this shift</div>
          {detail.alarm_summary && (
            <div style={{ fontSize: 12.5, color: "#7f1d1d", background: "#fef2f2",
              border: "1px solid #fecaca", borderRadius: 8, padding: "8px 12px", marginBottom: 8 }}>
              {detail.alarm_summary}
            </div>
          )}
          <div style={{ overflowX: "auto", marginBottom: 20 }}>
            <table style={tbl}>
              <thead><tr>{["Time", "Cycle #", "Part code", "CT (s)", "Remark", "Video"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
              <tbody>
                {(detail.alarms || []).map((a, i) => (
                  <tr key={i} style={{ borderTop: "1px solid #eef2f7" }}>
                    <td style={{ ...td, whiteSpace: "nowrap" }}>{a.time || "—"}</td>
                    <td style={{ ...td, fontFamily: "monospace", fontWeight: 700, color: "#dc2626" }}>{a.cycle_seq ?? "—"}</td>
                    <td style={{ ...td, fontFamily: "monospace" }}>{a.part_code || "—"}</td>
                    <td style={td}>{a.ct != null ? a.ct.toFixed(1) : "—"}</td>
                    <td style={{ ...td, maxWidth: 260, color: a.remark ? "#334155" : "#cbd5e1" }}>{a.remark || "— (pending)"}</td>
                    <td style={td}>
                      {a.cycle_seq != null
                        ? <button onClick={() => setAlarmVid(a)} style={{ border: "1px solid #dc2626", background: "#fff", color: "#dc2626", borderRadius: 7, padding: "3px 10px", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>▶ Video</button>
                        : <span style={{ color: "#cbd5e1" }}>—</span>}
                    </td>
                  </tr>
                ))}
                {(!detail.alarms || detail.alarms.length === 0) &&
                  <tr><td style={td} colSpan={6}><span style={{ color: "#94a3b8" }}>Is shift me koi alarm/NG part nahi.</span></td></tr>}
              </tbody>
            </table>
          </div>

          {/* NG-clip video modal */}
          {alarmVid && (
            <div onClick={e => e.target === e.currentTarget && setAlarmVid(null)}
                 style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,.6)", zIndex: 5000,
                          display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
              <div style={{ background: "#000", borderRadius: 12, overflow: "hidden", maxWidth: "90vw", boxShadow: "0 20px 60px rgba(0,0,0,.5)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 12px", background: "#111", color: "#fff", gap: 12 }}>
                  <span style={{ fontSize: 13, fontWeight: 700 }}>NG cycle #{alarmVid.cycle_seq}{alarmVid.part_code ? ` · ${alarmVid.part_code}` : ""}</span>
                  <button onClick={() => setAlarmVid(null)} style={{ border: "none", background: "#dc2626", color: "#fff", width: 28, height: 28, borderRadius: 7, cursor: "pointer", fontSize: 16 }}>✕</button>
                </div>
                <video controls autoPlay playsInline style={{ display: "block", maxWidth: "88vw", maxHeight: "78vh", background: "#000" }}
                       src={`/api/lines/${detail.line_id}/cycle-video?cycle_seq=${alarmVid.cycle_seq}&ng=1&token=${encodeURIComponent(token || "")}`} />
              </div>
            </div>
          )}

          {/* manpower */}
          <div style={secTitle}>Manpower per machine</div>
          <div style={{ overflowX: "auto", marginBottom: 20 }}>
            <table style={tbl}>
              <thead><tr>{["Process / Machine", "Req", "Operators", "Skill"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
              <tbody>
                {(detail.manpower || []).map((m, i) => (
                  <tr key={i} style={{ borderTop: "1px solid #eef2f7" }}>
                    <td style={td}>{m.process_name}{m.machines_covered ? <span style={{ color: "#94a3b8" }}> · {m.machines_covered}</span> : null}</td>
                    <td style={td}>{m.required ?? "—"}</td>
                    <td style={td}>{m.operators.map(o => o.name).join(", ") || <span style={{ color: "#cbd5e1" }}>—</span>}</td>
                    <td style={td}>{m.operators.map((o, j) => <span key={j} style={{ color: o.skill_ok === false ? "#dc2626" : "#16a34a", marginRight: 6, fontWeight: 600 }}>L{o.skill ?? "?"}</span>)}</td>
                  </tr>
                ))}
                {(!detail.manpower || detail.manpower.length === 0) &&
                  <tr><td style={td} colSpan={4}><span style={{ color: "#94a3b8" }}>No manpower allocated.</span></td></tr>}
              </tbody>
            </table>
          </div>

          {/* comments this shift — per machine (video comments added on the
              management dashboard).  Sits beside the manpower/punch-in data. */}
          <div style={secTitle}>Comments this shift</div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
            {(() => {
              const tp = detail.total_parts || 0, ot = detail.over_target || 0, cc = detail.comment_total || 0;
              const pct = (n) => tp ? `${(n / tp * 100).toFixed(1)}%` : "—";
              const chip = (label, val, sub, color) => (
                <div style={{ background: "#f8fafc", border: "1px solid #e5e7eb", borderRadius: 10,
                  padding: "8px 14px", minWidth: 118 }}>
                  <div style={{ fontSize: 10, color: "#64748b", textTransform: "uppercase",
                    letterSpacing: .4, fontWeight: 700 }}>{label}</div>
                  <div style={{ fontSize: 20, fontWeight: 800, color, lineHeight: 1.2 }}>{val}</div>
                  {sub && <div style={{ fontSize: 11, color: "#94a3b8" }}>{sub}</div>}
                </div>
              );
              return <>
                {chip("Total parts", tp, "cycles this shift", "#0f172a")}
                {chip("Over target", ot, `${pct(ot)} of parts · >${detail.target_ct}s`, ot ? "#dc2626" : "#0f172a")}
                {chip("Comments", cc, `${pct(cc)} of parts`, cc ? "#7c3aed" : "#0f172a")}
              </>;
            })()}
          </div>
          <div style={{ fontSize: 12, fontWeight: 700, color: "#334155", margin: "6px 0 8px" }}>Comments per machine</div>
          <div style={{ overflowX: "auto", marginBottom: 20 }}>
            <table style={tbl}>
              <thead><tr>{["Machine", "Comments"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
              <tbody>
                {(detail.comment_counts || []).map((c, i) => (
                  <tr key={i} style={{ borderTop: "1px solid #eef2f7" }}>
                    <td style={td}>{c.machine}</td>
                    <td style={{ ...td, fontWeight: 700, color: "#7c3aed" }}>{c.count}</td>
                  </tr>
                ))}
                {(!detail.comment_counts || detail.comment_counts.length === 0) &&
                  <tr><td style={td} colSpan={2}><span style={{ color: "#94a3b8" }}>No comments this shift.</span></td></tr>}
              </tbody>
            </table>
          </div>

          {/* cycle-time distribution — VERTICAL bars, coloured vs the line's
              target CT, with a dashed target line.  ≥40 s all fold into "40+". */}
          <div style={secTitle}>Cycle-time distribution — cycles per 2s
            {detail.target_ct != null &&
              <span style={{ color: "#94a3b8", fontWeight: 600 }}> · target {detail.target_ct}s</span>}
          </div>
          {(detail.ct_buckets && detail.ct_buckets.length > 0) ? (
            <div style={{ marginBottom: 20, overflowX: "auto", paddingBottom: 2 }}>
              {(() => {
                const bk    = detail.ct_buckets;
                const maxN  = Math.max(...bk.map(b => b.count), 1);
                const totN  = bk.reduce((s, b) => s + b.count, 0);
                const tgt   = Number(detail.target_ct) || null;
                const colW = 46, gap = 4, pitch = colW + gap, barW = 28;
                const chartH = 150, labelH = 18;
                // target-line x: proportional position inside the matching bucket
                let tX = null;
                if (tgt != null) {
                  for (let j = 0; j < bk.length; j++) {
                    const lo = bk[j].lo, hi = bk[j].hi == null ? lo + 2 : bk[j].hi;
                    if (tgt < lo) { tX = j * pitch; break; }
                    if (tgt >= lo && tgt < hi) { tX = j * pitch + ((tgt - lo) / (hi - lo)) * colW; break; }
                  }
                  if (tX == null) tX = bk.length * pitch;
                }
                return (
                  <div style={{ position: "relative", display: "flex", alignItems: "flex-end",
                    gap, minWidth: bk.length * pitch, height: chartH + 16 + labelH, paddingTop: 16 }}>
                    {tX != null && (
                      <div style={{ position: "absolute", top: 0, height: chartH + 16, left: tX,
                        borderLeft: "2px dashed #dc2626", zIndex: 3, pointerEvents: "none" }}>
                        <span style={{ position: "absolute", top: -1, left: 3, fontSize: 10,
                          fontWeight: 800, color: "#dc2626", whiteSpace: "nowrap" }}>◄ {tgt}s</span>
                      </div>
                    )}
                    {bk.map((b, i) => {
                      const lbl  = b.hi == null ? `${b.lo}+` : `${b.lo}-${b.hi}`;
                      const good = tgt != null && b.hi != null && b.hi <= tgt;  // fully within target
                      const over = tgt != null && b.lo >= tgt;                  // fully over target
                      const color = tgt == null ? "#2563eb" : good ? "#16a34a" : over ? "#dc2626" : "#f59e0b";
                      const h = Math.round((b.count / maxN) * chartH);
                      return (
                        <div key={i} style={{ width: colW, flex: "0 0 auto", display: "flex",
                          flexDirection: "column", alignItems: "center", justifyContent: "flex-end" }}>
                          <span style={{ fontSize: 10, fontWeight: 700, color: "#334155", lineHeight: 1 }}>{b.count}</span>
                          <div title={`${lbl}s · ${b.count} (${totN ? Math.round(b.count / totN * 100) : 0}%)`}
                            style={{ width: barW, height: Math.max(h, b.count ? 3 : 0), marginTop: 2,
                            background: color, borderRadius: "3px 3px 0 0" }} />
                          <span style={{ fontSize: 9, color: "#64748b", marginTop: 4, height: labelH,
                            whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>{lbl}</span>
                        </div>
                      );
                    })}
                  </div>
                );
              })()}
              <div style={{ display: "flex", gap: 14, marginTop: 6, fontSize: 10, color: "#64748b", flexWrap: "wrap" }}>
                <span><span style={{ display: "inline-block", width: 9, height: 9, background: "#16a34a", borderRadius: 2, marginRight: 4 }} />within target</span>
                <span><span style={{ display: "inline-block", width: 9, height: 9, background: "#f59e0b", borderRadius: 2, marginRight: 4 }} />straddles target</span>
                <span><span style={{ display: "inline-block", width: 9, height: 9, background: "#dc2626", borderRadius: 2, marginRight: 4 }} />over target</span>
                <span style={{ color: "#dc2626", fontWeight: 700 }}>┆ {detail.target_ct}s target</span>
              </div>
            </div>
          ) : (
            <div style={{ color: "#94a3b8", fontSize: 13, marginBottom: 20 }}>No cycle-time data for this shift.</div>
          )}

          {/* ASSIGN controls — per-line OT + Non-Production Day (heads / line leader) */}
          {detail.can_close && (
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center",
              padding: "12px 14px", background: "#f8fafc", border: "1px solid #e5e7eb",
              borderRadius: 12, marginBottom: 18 }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: "#64748b" }}>ASSIGN</span>
              {detail.shift_type !== "nonprod" && (
                <button onClick={toggleOT} disabled={saving} style={assignBtn(detail.ot_active, "#d97706")}>
                  {detail.ot_active ? "OT is ON — turn OFF" : "Assign OT"}
                </button>
              )}
              <button onClick={toggleNPD} disabled={saving} style={assignBtn(detail.npd?.marked, "#475569")}>
                {detail.npd?.marked ? "Non-Production Day ON — remove" : "Mark Non-Production Day"}
              </button>
              {detail.ot_active &&
                <span style={{ fontSize: 12, color: "#b45309", fontWeight: 600 }}>OT active · {detail.base_shift}</span>}
              {detail.npd?.marked &&
                <span style={{ fontSize: 12, color: "#475569", fontWeight: 600 }}>Non-production{detail.npd.reason ? " · " + detail.npd.reason : ""}</span>}
            </div>
          )}

          {/* close control */}
          {detail.close.closed ? (
            <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap",
              background: "#f0fdf4", border: "1px solid #bbf7d0", borderRadius: 12, padding: "12px 16px" }}>
              <span style={{ fontSize: 20 }}>✓</span>
              <div>
                <div style={{ fontWeight: 700, color: "#15803d" }}>
                  Shift closed {detail.close.on_time ? "on time" : "LATE"} by {detail.close.closed_by || "—"}</div>
                <div style={{ fontSize: 12, color: "#94a3b8" }}>{new Date(detail.close.closed_at).toLocaleString()}</div>
              </div>
              {["admin", "plant_head"].includes(user?.role) &&
                <button onClick={reopen} style={{ marginLeft: "auto", ...btnGhost }}>Reopen</button>}
            </div>
          ) : detail.can_close ? (
            <div>
              <div style={{ fontSize: 12, color: "#64748b", marginBottom: 8 }}>
                Review the data above, then slide to close &amp; sign off your shift.
                {detail.scheduled_end && new Date() > new Date(detail.scheduled_end) &&
                  <span style={{ color: "#b45309", fontWeight: 600 }}> (scheduled end passed — will mark LATE)</span>}
              </div>
              <SlideToClose onConfirm={doClose} disabled={saving} />
            </div>
          ) : (
            <div style={{ fontSize: 13, color: "#94a3b8" }}>View-only — only this line's leader (or admin) can close the shift.</div>
          )}
        </div>
      )}

          </div>
        </>);
      })()}

      {toast && (
        <div style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)",
          background: toast.kind === "err" ? "#dc2626" : toast.kind === "warn" ? "#d97706" : "#16a34a",
          color: "#fff", padding: "10px 18px", borderRadius: 10, fontWeight: 600,
          boxShadow: "0 6px 20px rgba(0,0,0,.18)", zIndex: 50 }}>{toast.msg}</div>
      )}
      <AIAssistant pageContext={{ page: "Shift Compile" }} />
    </div>
  );
}

const inp = { background: "#fff", color: "#0f172a", border: "1px solid #cbd5e1",
  borderRadius: 8, padding: "8px 12px", fontSize: 14 };
const pill = (on) => ({ background: on ? "#2563eb" : "#fff", color: on ? "#fff" : "#334155",
  border: `1px solid ${on ? "#2563eb" : "#cbd5e1"}`, borderRadius: 999, padding: "7px 16px",
  fontSize: 14, fontWeight: 600, cursor: "pointer" });
// shift-button colours by type: production (blue), OT (amber), non-production (grey)
const SHIFT_TYPE = {
  prod:    { on: "#2563eb", off: "#eff6ff", border: "#93c5fd", text: "#1d4ed8" },
  ot:      { on: "#d97706", off: "#fffbeb", border: "#fcd34d", text: "#b45309" },
  nonprod: { on: "#64748b", off: "#f8fafc", border: "#cbd5e1", text: "#475569" },
};
const pillC = (on, c) => ({ background: on ? c.on : c.off, color: on ? "#fff" : c.text,
  border: `1px solid ${on ? c.on : c.border}`, borderRadius: 999, padding: "7px 16px",
  fontSize: 14, fontWeight: 600, cursor: "pointer" });
const card = { background: "#fff", border: "1px solid #e5e7eb", borderRadius: 16,
  padding: "18px 20px", marginBottom: 18, boxShadow: "0 1px 3px rgba(0,0,0,.04)" };
const secTitle = { fontSize: 13, fontWeight: 700, color: "#334155", margin: "4px 0 8px" };
const tbl = { width: "100%", borderCollapse: "collapse", fontSize: 13 };
const th = { padding: "8px 10px", fontWeight: 600, fontSize: 12, color: "#64748b",
  textAlign: "left", background: "#f8fafc", borderBottom: "1px solid #e5e7eb", whiteSpace: "nowrap" };
const td = { padding: "8px 10px", color: "#0f172a" };
const btnGhost = { background: "#fff", color: "#2563eb", border: "1px solid #bfdbfe",
  borderRadius: 8, padding: "6px 12px", fontSize: 13, cursor: "pointer", fontWeight: 600 };
// ASSIGN action button — filled when the state is ON, outlined when OFF
const assignBtn = (on, c) => ({ background: on ? c : "#fff", color: on ? "#fff" : c,
  border: `1px solid ${c}`, borderRadius: 8, padding: "7px 14px", fontSize: 13,
  fontWeight: 700, cursor: "pointer" });
