/* ════════════════════════════════════════════════════════════════════
 *  ShiftAllocation.jsx
 *  ────────────────────────────────────────────────────────────────────
 *  Daily skill-based manpower allocation — Kanban + Apple Watch rings.
 *
 *  Workflow:
 *    1. Section Incharge has predefined per-process required skill
 *       (Admin → Production → Processes / Skill, derived from
 *       Machine Master).
 *    2. Operators punch via badge (Dashboard widget) OR supervisor
 *       manually adds them via the "+ Add" button on this page.
 *    3. Supervisor drags operator chips from Pool → Process columns.
 *       Same operator can sit on multiple processes (one expert covers
 *       2 machines).  Empty processes shake with red border on save.
 *    4. Skill mismatches fire instant email + Quality / Section
 *       Incharge dashboard banners.
 *    5. Locked once shift end_time passes.
 * ════════════════════════════════════════════════════════════════════ */

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import AIAssistant from "../components/AIAssistant";
import PageTopbar from "../components/PageTopbar";

const SKILL_LABELS = {
  1: "Trainee", 2: "Basic", 3: "Skilled", 4: "Multi-skilled", 5: "Expert",
};
const SKILL_COLOR = (l) =>
  l >= 4 ? "#16a34a" : l >= 3 ? "#2563eb" : l >= 2 ? "#d97706" : "#94a3b8";

/* ── Reusable: Apple Watch style ring ─────────────────────────────── */
function Ring({ value, max = 100, size = 110, stroke = 12, color, label, suffix = "%" }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(1, max ? value / max : 0));
  const dash = c * pct;
  const display = max === 100 ? Math.round(value) : `${value}/${max}`;
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
      <div style={{ position: "relative", width: size, height: size }}>
        <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
          <circle cx={size/2} cy={size/2} r={r}
                  stroke="#e2e8f0" strokeWidth={stroke} fill="none"/>
          <circle cx={size/2} cy={size/2} r={r}
                  stroke={color} strokeWidth={stroke} fill="none"
                  strokeDasharray={`${dash} ${c}`} strokeLinecap="round"
                  style={{ transition: "stroke-dasharray .6s cubic-bezier(.4,0,.2,1)" }}/>
        </svg>
        <div style={{
          position: "absolute", inset: 0, display: "flex",
          alignItems: "center", justifyContent: "center", flexDirection: "column",
        }}>
          <div style={{ fontSize: 22, fontWeight: 800, color: "#0f172a", fontFamily: "'Barlow Condensed',sans-serif", lineHeight: 1 }}>
            {display}{max === 100 ? suffix : ""}
          </div>
        </div>
      </div>
      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: ".12em", textTransform: "uppercase", color: "#64748b" }}>{label}</div>
    </div>
  );
}

/* ── Operator avatar (initials + skill ring) ──────────────────────── */
function Avatar({ op, size = 36, ring = true }) {
  const initials = (op.full_name || "?")
    .split(/\s+/).map(s => s[0] || "").join("").slice(0, 2).toUpperCase();
  const color = SKILL_COLOR(op.skill_level || 1);
  const bgPalette = ["#0ea5e9","#8b5cf6","#ec4899","#f97316","#14b8a6","#eab308","#22c55e","#ef4444","#6366f1"];
  const bg = bgPalette[(op.id || 0) % bgPalette.length];
  return (
    <div style={{
      position: "relative", width: size, height: size,
      display: "flex", alignItems: "center", justifyContent: "center",
      flexShrink: 0,
    }}>
      {ring && (
        <div style={{
          position: "absolute", inset: 0, borderRadius: "50%",
          padding: 2,
          background: `conic-gradient(${color} ${(op.skill_level||1)*72}deg, #e2e8f0 0deg)`,
        }} />
      )}
      <div style={{
        position: "relative", width: size - (ring ? 4 : 0), height: size - (ring ? 4 : 0),
        borderRadius: "50%", background: bg,
        color: "#fff", fontSize: size * 0.36, fontWeight: 800,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontFamily: "'Barlow Condensed',sans-serif", letterSpacing: ".02em",
      }}>{initials}</div>
    </div>
  );
}

/* ── Draggable operator card ──────────────────────────────────────── */
function OperatorCard({ op, count = 0, dragging, onDragStart, onDragEnd, compact, locked }) {
  const c = SKILL_COLOR(op.skill_level || 1);
  return (
    <div
      draggable={!locked}
      onDragStart={(e) => onDragStart(e, op.id)}
      onDragEnd={onDragEnd}
      style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: compact ? "6px 8px" : "8px 10px",
        marginBottom: 6,
        background: "#fff",
        border: `1px solid ${count > 0 ? `${c}77` : "#e2e8f0"}`,
        borderRadius: 10,
        cursor: locked ? "default" : "grab",
        opacity: dragging ? 0.4 : 1,
        transition: "transform .15s, box-shadow .15s, border-color .15s",
        boxShadow: count > 0 ? `0 0 0 1px ${c}22, 0 2px 8px ${c}22` : "0 1px 2px rgba(0,0,0,.04)",
        userSelect: "none",
      }}
      onMouseDown={(e) => e.currentTarget.style.cursor = locked ? "default" : "grabbing"}
      onMouseUp={(e) => e.currentTarget.style.cursor = locked ? "default" : "grab"}
    >
      <Avatar op={op} size={compact ? 32 : 38} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: "#0f172a", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {op.full_name}
        </div>
        <div style={{ fontSize: 10, color: "#64748b", fontFamily: "monospace", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {op.badge_code}
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "end", gap: 2 }}>
        <span style={{
          fontSize: 9, fontWeight: 800, padding: "1px 6px", borderRadius: 99,
          background: `${c}1a`, color: c, letterSpacing: ".05em",
        }}>L{op.skill_level || 1}</span>
        {count > 0 && (
          <span title={`Assigned to ${count}`} style={{
            fontSize: 9, fontWeight: 800, padding: "1px 6px", borderRadius: 99,
            background: "#16a34a", color: "#fff",
          }}>×{count}</span>
        )}
      </div>
    </div>
  );
}

/* ── Toast ────────────────────────────────────────────────────────── */
function Toast({ msg, kind }) {
  if (!msg) return null;
  const c = kind === "err" ? "#dc2626" : "#16a34a";
  return (
    <div style={{
      position: "fixed", bottom: 28, right: 28, zIndex: 10000,
      background: "#fff",
      border: `1px solid ${c}`, color: "#0f172a",
      padding: "12px 18px", borderRadius: 12, fontSize: 13, fontWeight: 600,
      boxShadow: "0 10px 40px rgba(0,0,0,.12)",
      maxWidth: 400,
    }}>
      <span style={{ color: c, marginRight: 8 }}>{kind === "err" ? "✗" : "✓"}</span>
      {msg}
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════
 *  Main page
 * ════════════════════════════════════════════════════════════════════ */
export default function ShiftAllocation() {
  const { token, user, canWrite } = useAuth();
  const writable = canWrite ? canWrite("shift-allocation") : true;

  const [lines,     setLines]     = useState([]);
  const [lineId,    setLineId]    = useState("");
  const [date,      setDate]      = useState(() => new Date().toISOString().slice(0, 10));
  const [shift,     setShift]     = useState("A");
  const [processes, setProcesses] = useState([]);
  const [pool,      setPool]      = useState([]);
  const [history,   setHistory]   = useState([]);
  const [config,    setConfig]    = useState(null);
  const [locked,    setLocked]    = useState(false);
  const [loading,   setLoading]   = useState(false);
  const [saving,    setSaving]    = useState(false);
  const [toastV,    setToastV]    = useState(null);
  const [pending,   setPending]   = useState({});      // {process_id: [op_id|null,...]}
  const [shakeIds,  setShakeIds]  = useState(new Set()); // process ids that should shake
  const [historyOpen, setHistoryOpen] = useState(false);

  // Drag state
  const [dragOp, setDragOp]       = useState(null);    // {op_id, fromProcessId|null, slotIdx|null}
  const [dropHover, setDropHover] = useState(null);    // process_id or 'pool'

  const showToast = (msg, kind = "ok") => {
    setToastV({ msg, kind });
    setTimeout(() => setToastV(null), 3500);
  };

  // ── Initial load ─────────────────────────────────────────────────
  useEffect(() => {
    (async () => {
      try {
        const [ls] = await Promise.all([api.get("/api/lines/", token)]);
        setLines(ls || []);
        if (!lineId && ls?.length) setLineId(ls[0].id);
      } catch (e) { showToast(`Init failed: ${e.message}`, "err"); }
    })();
  }, [token]);

  useEffect(() => {
    if (!lineId) return;
    api.get("/api/manpower/config", token)
      .then(cfgs => setConfig((cfgs || []).find(c => c.line_id === Number(lineId)) || null))
      .catch(() => {});
  }, [lineId, token]);

  const refresh = useCallback(async () => {
    if (!lineId || !date || !shift) return;
    setLoading(true);
    try {
      const [procs, punches, allocs, hist] = await Promise.all([
        api.get(`/api/manpower/processes?line_id=${lineId}`, token),
        api.get(`/api/manpower/punches?line_id=${lineId}&date=${date}&shift=${encodeURIComponent(shift)}`, token),
        api.get(`/api/manpower/allocations?line_id=${lineId}&date=${date}&shift=${encodeURIComponent(shift)}`, token),
        api.get(`/api/manpower/allocations?line_id=${lineId}&date=${date}&shift=${encodeURIComponent(shift)}&include_history=true`, token),
      ]);
      setProcesses((procs || []).filter(p => p.is_active));
      setPool(punches || []);
      setHistory(hist || []);
      const today = new Date().toISOString().slice(0, 10);
      setLocked(date < today);

      const next = {};
      (procs || []).forEach(p => { next[p.id] = Array(p.required_manpower_count).fill(null); });
      (allocs || []).forEach(a => {
        const slot = next[a.process_id];
        if (slot) {
          const i = slot.findIndex(x => x === null);
          if (i >= 0) slot[i] = a.operator_id;
        }
      });
      setPending(next);
      setShakeIds(new Set());
    } catch (e) { showToast(`Refresh failed: ${e.message}`, "err"); }
    finally { setLoading(false); }
  }, [lineId, date, shift, token]);

  useEffect(() => { refresh(); }, [refresh]);

  // ── Derived stats ────────────────────────────────────────────────
  const assignmentCount = useMemo(() => {
    const m = new Map();
    Object.values(pending).forEach(arr => arr.forEach(id => {
      if (!id) return;
      m.set(id, (m.get(id) || 0) + 1);
    }));
    return m;
  }, [pending]);

  const stats = useMemo(() => {
    let needed = 0, filled = 0, mismatch = 0, covered = 0;
    processes.forEach(p => {
      needed += p.required_manpower_count;
      const ops = pending[p.id] || [];
      let processFilled = 0;
      ops.forEach(opId => {
        if (!opId) return;
        processFilled += 1;
        filled += 1;
        const op = pool.find(x => x.id === opId);
        if (op && op.skill_level < p.required_skill_level) mismatch += 1;
      });
      if (processFilled > 0) covered += 1;
    });
    return {
      needed, filled, mismatch, covered,
      totalProcs: processes.length,
      filledPct:  needed ? (filled / needed) * 100 : 0,
      matchPct:   filled ? ((filled - mismatch) / filled) * 100 : 100,
      coveredPct: processes.length ? (covered / processes.length) * 100 : 0,
    };
  }, [processes, pending, pool]);

  // ── Drag handlers ────────────────────────────────────────────────
  const onCardDragStart = (e, opId, fromProcessId = null, slotIdx = null) => {
    if (locked || !writable) return;
    setDragOp({ opId, fromProcessId, slotIdx });
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", String(opId)); } catch {}
  };

  const onCardDragEnd = () => { setDragOp(null); setDropHover(null); };

  const onSlotDragOver = (e) => {
    if (!dragOp) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  };

  const dropOnProcess = (e, processId) => {
    e.preventDefault();
    if (!dragOp) return;
    const { opId, fromProcessId, slotIdx } = dragOp;
    setPending(prev => {
      const next = { ...prev };
      // Remove from source slot
      if (fromProcessId != null && slotIdx != null) {
        const src = [...next[fromProcessId]];
        src[slotIdx] = null;
        next[fromProcessId] = src;
      }
      // Add to target process — first empty slot, else replace last
      const tgt = [...(next[processId] || [])];
      // Prevent duplicate in same process
      const existingIdx = tgt.findIndex(x => x === opId);
      if (existingIdx >= 0) {
        // already there — just move within process (clear other slot)
        const emptyIdx = tgt.findIndex((x, i) => x === null && i !== existingIdx);
        if (emptyIdx >= 0) { /* no-op, already in this proc */ }
      } else {
        const emptyIdx = tgt.findIndex(x => x === null);
        if (emptyIdx >= 0) tgt[emptyIdx] = opId;
        else tgt[tgt.length - 1] = opId;  // replace last if full
      }
      next[processId] = tgt;
      return next;
    });
    setDragOp(null);
    setDropHover(null);
  };

  const dropOnPool = (e) => {
    e.preventDefault();
    if (!dragOp) return;
    const { opId, fromProcessId, slotIdx } = dragOp;
    if (fromProcessId != null && slotIdx != null) {
      setPending(prev => {
        const src = [...prev[fromProcessId]];
        src[slotIdx] = null;
        return { ...prev, [fromProcessId]: src };
      });
    }
    setDragOp(null);
    setDropHover(null);
  };

  const clearSlot = (processId, idx) => {
    setPending(prev => {
      const arr = [...(prev[processId] || [])];
      arr[idx] = null;
      return { ...prev, [processId]: arr };
    });
  };

  // ── Save ─────────────────────────────────────────────────────────
  const save = async () => {
    if (locked) { showToast("Shift is locked — cannot save", "err"); return; }
    const empty = processes.filter(p => !(pending[p.id] || []).some(id => !!id));
    if (empty.length) {
      const ids = new Set(empty.map(p => p.id));
      setShakeIds(ids);
      setTimeout(() => setShakeIds(new Set()), 800);
      const names = empty.slice(0, 3).map(p => p.process_name).join(", ");
      const tail  = empty.length > 3 ? `, +${empty.length - 3} more` : "";
      showToast(`Minimum 1 operator on every process. Missing: ${names}${tail}`, "err");
      return;
    }
    setSaving(true);
    try {
      const rows = [];
      Object.entries(pending).forEach(([pid, ops]) => {
        ops.forEach(opId => {
          if (opId) rows.push({ process_id: Number(pid), operator_id: opId });
        });
      });
      const res = await api.post("/api/manpower/allocations", {
        line_id: Number(lineId), shift_date: date, shift_name: shift,
        rows, allocated_by: user?.username || "supervisor",
      }, token);
      const mismatches = res?.mismatches ?? 0;
      if (mismatches > 0) {
        showToast(`Saved · ${mismatches} skill mismatch${mismatches>1?"es":""} — Quality + Section Incharge notified`, "err");
      } else {
        showToast(`Allocation saved · ${res?.added ?? rows.length} new, ${res?.closed ?? 0} removed`);
      }
      refresh();
    } catch (e) { showToast(e.message, "err"); }
    finally { setSaving(false); }
  };

  // ── Manual punch / Add operator ──────────────────────────────────
  const [addModal, setAddModal] = useState(false);
  const [allOperators, setAllOperators] = useState([]);
  const [pickOpId, setPickOpId] = useState("");
  const [pickSearch, setPickSearch] = useState("");

  const openAddOperator = async () => {
    setPickOpId(""); setPickSearch("");
    try {
      const ops = await api.get("/api/operators", token);
      setAllOperators((ops || []).filter(o => o.is_active));
      setAddModal(true);
    } catch (e) { showToast(`Failed to load operators: ${e.message}`, "err"); }
  };

  const manualPunch = async () => {
    if (!pickOpId) { showToast("Pick an operator", "err"); return; }
    try {
      await api.post("/api/manpower/punches", {
        operator_id: Number(pickOpId),
        line_id: Number(lineId),
        shift_name: shift,
        shift_date: date,
      }, token);
      const op = allOperators.find(o => o.id === Number(pickOpId));
      showToast(`${op?.full_name || "Operator"} added to pool ✓`);
      setAddModal(false);
      refresh();
    } catch (e) { showToast(e.message, "err"); }
  };

  const poolIds = useMemo(() => new Set(pool.map(p => p.id)), [pool]);
  const addableOperators = useMemo(() => {
    const q = pickSearch.trim().toLowerCase();
    return allOperators
      .filter(o => !poolIds.has(o.id))
      .filter(o => !q
        || (o.full_name||"").toLowerCase().includes(q)
        || (o.badge_code||"").toLowerCase().includes(q)
        || (o.employee_id||"").toLowerCase().includes(q));
  }, [allOperators, poolIds, pickSearch]);

  // ── Render ───────────────────────────────────────────────────────
  return (
    <div style={{
      minHeight: "100vh",
      background: "#f8fafc",
      fontFamily: "'Barlow',sans-serif", paddingBottom: 60, color: "#0f172a",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@700;800&display=swap');
        @keyframes shake-x {
          0%, 100% { transform: translateX(0); }
          15% { transform: translateX(-8px); }
          30% { transform: translateX(8px); }
          45% { transform: translateX(-6px); }
          60% { transform: translateX(6px); }
          75% { transform: translateX(-3px); }
        }
        @keyframes pulse-red {
          0%, 100% { box-shadow: 0 0 0 0 rgba(220,38,38,.5); }
          50% { box-shadow: 0 0 0 8px rgba(220,38,38,0); }
        }
        .shake { animation: shake-x .6s ease-in-out; }
        .pulse-red { animation: pulse-red 1.4s ease-in-out infinite; }
        .col-scroll::-webkit-scrollbar { width: 6px; }
        .col-scroll::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
      `}</style>

      {/* 2026-05-13 — standardised topbar (matches Production Import/Export) */}
      <PageTopbar leading="Shift" accent="Allocation" />

      {/* ═══ HERO ─ filters + rings (title moved into PageTopbar) ════════ */}
      <div style={{
        padding: "16px 48px 22px",
        background: "#fff",
        borderBottom: "1px solid #e2e8f0",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 24, flexWrap: "wrap" }}>
          {/* Left: title + filters */}
          <div style={{ flex: 1, minWidth: 360 }}>
            {/* Title moved to <PageTopbar> above for cross-page consistency */}
            <div style={{ fontSize: 12, color: "#64748b" }}>
              Drag operators from the pool onto process columns · same operator can cover multiple machines
            </div>

            <div style={{ display: "flex", gap: 12, alignItems: "end", marginTop: 18, flexWrap: "wrap" }}>
              <FilterCell label="Line">
                <select value={lineId} onChange={e => setLineId(Number(e.target.value))} style={glassSelect}>
                  {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
                </select>
              </FilterCell>
              <FilterCell label="Date">
                <input type="date" value={date} onChange={e => setDate(e.target.value)} style={glassSelect} />
              </FilterCell>
              <FilterCell label="Shift">
                <select value={shift} onChange={e => setShift(e.target.value)} style={glassSelect}>
                  <option value="A">A</option><option value="B">B</option><option value="C">C</option>
                </select>
              </FilterCell>
              <button onClick={refresh} disabled={loading} style={glassBtn}>↻ {loading ? "…" : "Refresh"}</button>
              {!locked && writable && (
                <button onClick={save} disabled={saving} style={primaryBtn}>
                  {saving ? "Saving…" : "💾 Save Allocation"}
                </button>
              )}
            </div>

            {/* Status pills */}
            <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
              {locked && <Pill color="#dc2626">🔒 LOCKED — past shift</Pill>}
              {!locked && config && (
                <Pill color="#2563eb">⏱ Deadline {config.allocation_deadline_minutes}m · Ack {config.ack_timeout_minutes}m</Pill>
              )}
              {!locked && !config && <Pill color="#d97706">⚠ No config — defaults apply</Pill>}
              {stats.mismatch > 0 && <Pill color="#dc2626">⚠ {stats.mismatch} skill-mismatch slot{stats.mismatch>1?"s":""}</Pill>}
            </div>
          </div>

          {/* Right: rings */}
          <div style={{
            display: "flex", gap: 20, padding: "18px 24px",
            background: "#fff",
            border: "1px solid #e2e8f0", borderRadius: 18,
            boxShadow: "0 1px 3px rgba(0,0,0,.04)",
          }}>
            <Ring value={stats.filledPct}  color="#16a34a" label="Filled %"   />
            <Ring value={stats.matchPct}   color="#2563eb" label="Skill Match" />
            <Ring value={stats.coveredPct} color="#db2777" label="Coverage"   />
            <div style={{ display: "flex", flexDirection: "column", justifyContent: "center", gap: 10, paddingLeft: 12, borderLeft: "1px solid #e2e8f0" }}>
              <StatLine label="Pool"      value={pool.length} color="#64748b" />
              <StatLine label="Filled"    value={`${stats.filled}/${stats.needed}`} color="#16a34a" />
              <StatLine label="Mismatch"  value={stats.mismatch} color={stats.mismatch>0?"#dc2626":"#64748b"} />
              <StatLine label="Processes" value={`${stats.covered}/${stats.totalProcs}`} color="#2563eb" />
            </div>
          </div>
        </div>
      </div>

      {/* ═══ KANBAN BOARD ════════════════════════════════════════════ */}
      <div style={{
        display: "flex", gap: 14, padding: "20px 48px",
        overflowX: "auto", alignItems: "stretch",
      }}>
        {/* POOL COLUMN */}
        <div
          onDragOver={onSlotDragOver}
          onDragEnter={() => setDropHover("pool")}
          onDragLeave={(e) => { if (e.currentTarget === e.target) setDropHover(null); }}
          onDrop={dropOnPool}
          style={{
            flexShrink: 0, width: 280,
            background: "#fff",
            border: `1px solid ${dropHover === "pool" ? "#2563eb" : "#e2e8f0"}`,
            borderRadius: 14, display: "flex", flexDirection: "column",
            boxShadow: dropHover === "pool" ? "0 0 0 3px rgba(37,99,235,.15)" : "0 1px 3px rgba(0,0,0,.04)",
            transition: "border-color .15s, box-shadow .15s",
          }}>
          <div style={{ padding: 14, borderBottom: "1px solid #e2e8f0", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 800, color: "#0f172a", letterSpacing: ".02em" }}>OPERATOR POOL</div>
              <div style={{ fontSize: 10, color: "#64748b", marginTop: 2 }}>
                {pool.length} punched-in
              </div>
            </div>
            {!locked && writable && (
              <button onClick={openAddOperator} style={{
                padding: "6px 12px", fontSize: 11, fontWeight: 800,
                background: "linear-gradient(135deg, #3b82f6, #60a5fa)", color: "#fff",
                border: "none", borderRadius: 8, cursor: "pointer",
                boxShadow: "0 2px 8px rgba(59,130,246,.3)",
              }}>+ ADD</button>
            )}
          </div>
          <div className="col-scroll" style={{ padding: 10, flex: 1, overflowY: "auto", maxHeight: "calc(100vh - 340px)" }}>
            {pool.length === 0 ? (
              <div style={{ padding: 24, textAlign: "center", color: "#94a3b8", fontSize: 11 }}>
                Nobody punched in yet.<br/>
                {!locked && writable && <span style={{ fontSize: 10, marginTop: 6, display: "inline-block" }}>Click <b style={{ color: "#2563eb" }}>+ ADD</b> to manually add.</span>}
              </div>
            ) : (
              pool.map(op => (
                <OperatorCard
                  key={op.id} op={op}
                  count={assignmentCount.get(op.id) || 0}
                  dragging={dragOp?.opId === op.id && dragOp?.fromProcessId == null}
                  onDragStart={(e, opId) => onCardDragStart(e, opId, null, null)}
                  onDragEnd={onCardDragEnd}
                  locked={locked || !writable}
                />
              ))
            )}
          </div>
        </div>

        {/* PROCESS COLUMNS */}
        {processes.length === 0 ? (
          <div style={{
            flex: 1, minWidth: 400, display: "flex", alignItems: "center", justifyContent: "center",
            background: "#fff", border: "1px dashed #e2e8f0", borderRadius: 14,
            color: "#64748b", fontSize: 13, textAlign: "center", padding: 30,
          }}>
            No machines on this line yet.<br/>
            <span style={{ fontSize: 11 }}>Add machines under Admin → Production → Machines. They will appear here automatically.</span>
          </div>
        ) : (
          processes.map(p => {
            const ops = pending[p.id] || Array(p.required_manpower_count).fill(null);
            const hasAny = ops.some(id => !!id);
            const isShaking = shakeIds.has(p.id);
            const isHovering = dropHover === p.id;
            const reqC = SKILL_COLOR(p.required_skill_level);
            return (
              <div
                key={p.id}
                className={isShaking ? "shake" : ""}
                onDragOver={onSlotDragOver}
                onDragEnter={() => setDropHover(p.id)}
                onDragLeave={(e) => { if (e.currentTarget === e.target) setDropHover(null); }}
                onDrop={(e) => dropOnProcess(e, p.id)}
                style={{
                  flexShrink: 0, width: 280,
                  background: hasAny ? "#fff" : "#fef2f2",
                  border: `1px solid ${isHovering ? "#2563eb" : hasAny ? "#e2e8f0" : "#fecaca"}`,
                  borderRadius: 14, display: "flex", flexDirection: "column",
                  boxShadow: isHovering ? "0 0 0 3px rgba(37,99,235,.15)" : "0 1px 3px rgba(0,0,0,.04)",
                  transition: "border-color .15s, box-shadow .15s, background .15s",
                }}>
                {/* Header */}
                <div style={{ padding: 14, borderBottom: "1px solid #e2e8f0" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 8 }}>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ fontSize: 12, fontWeight: 800, color: "#0f172a", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        {p.process_name}
                      </div>
                      <div style={{ fontSize: 10, color: "#64748b", marginTop: 2, display: "flex", gap: 8 }}>
                        <span>{p.required_manpower_count} slot{p.required_manpower_count>1?"s":""}</span>
                        <span>·</span>
                        <span>{p.machines_covered} mc/op</span>
                      </div>
                    </div>
                    <span style={{
                      fontSize: 9, fontWeight: 800, padding: "2px 8px", borderRadius: 99,
                      background: `${reqC}1a`, color: reqC, whiteSpace: "nowrap",
                      border: `1px solid ${reqC}44`,
                    }}>L{p.required_skill_level} {SKILL_LABELS[p.required_skill_level]}</span>
                  </div>
                  {!hasAny && (
                    <div className="pulse-red" style={{
                      marginTop: 8, fontSize: 9, fontWeight: 800, padding: "3px 8px", borderRadius: 6,
                      background: "#dc2626", color: "#fff", textAlign: "center", letterSpacing: ".08em",
                    }}>⚠ NEEDS ≥1 OPERATOR</div>
                  )}
                </div>

                {/* Slot cells */}
                <div className="col-scroll" style={{ padding: 10, flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 8 }}>
                  {ops.map((opId, idx) => {
                    const op = opId ? pool.find(x => x.id === opId) : null;
                    const match = !op || op.skill_level >= p.required_skill_level;
                    const slotBorder = !op ? "#e2e8f0" : match ? "#86efac" : "#fecaca";
                    const slotBg = !op ? "#f8fafc" : match ? "#f0fdf4" : "#fef2f2";
                    return (
                      <div key={idx} style={{
                        border: `1px dashed ${slotBorder}`, borderRadius: 10,
                        background: slotBg, minHeight: 64, padding: 6,
                        display: "flex", flexDirection: "column", justifyContent: "center",
                      }}>
                        {op ? (
                          <div style={{ position: "relative" }}>
                            <OperatorCard
                              op={op} compact
                              count={assignmentCount.get(op.id) || 0}
                              dragging={dragOp?.opId === op.id && dragOp?.fromProcessId === p.id && dragOp?.slotIdx === idx}
                              onDragStart={(e, oid) => onCardDragStart(e, oid, p.id, idx)}
                              onDragEnd={onCardDragEnd}
                              locked={locked || !writable}
                            />
                            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0 4px" }}>
                              {!match ? (
                                <span style={{ fontSize: 9, fontWeight: 700, color: "#dc2626" }}>
                                  ⚠ needs L{p.required_skill_level} (has L{op.skill_level})
                                </span>
                              ) : <span/>}
                              {!locked && writable && (
                                <button onClick={() => clearSlot(p.id, idx)} title="Remove"
                                        style={{
                                          background: "transparent", border: "none",
                                          color: "#94a3b8", cursor: "pointer", fontSize: 14,
                                          padding: 2, lineHeight: 1,
                                        }}>×</button>
                              )}
                            </div>
                          </div>
                        ) : (
                          <div style={{
                            textAlign: "center", color: "#94a3b8", fontSize: 10,
                            fontStyle: "italic", padding: "12px 4px",
                          }}>
                            Slot {idx + 1}<br/>
                            <span style={{ fontSize: 9 }}>drop operator here</span>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* ═══ HISTORY ─ collapsible ════════════════════════════════════ */}
      <div style={{ padding: "0 48px 32px" }}>
        <div style={{
          background: "#fff",
          border: "1px solid #e2e8f0", borderRadius: 14,
          boxShadow: "0 1px 3px rgba(0,0,0,.04)",
        }}>
          <div onClick={() => setHistoryOpen(o => !o)}
               style={{
                 padding: "14px 18px", display: "flex", justifyContent: "space-between",
                 alignItems: "center", cursor: "pointer",
               }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 800, color: "#0f172a" }}>ALLOCATION HISTORY</div>
              <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>
                Append-only audit log · {history.length} entr{history.length === 1 ? "y" : "ies"}
              </div>
            </div>
            <span style={{ fontSize: 18, color: "#64748b" }}>{historyOpen ? "▾" : "▸"}</span>
          </div>
          {historyOpen && (
            <div style={{ padding: "0 18px 18px", maxHeight: 380, overflowY: "auto" }}>
              {history.length === 0 ? (
                <div style={{ padding: 30, textAlign: "center", color: "#94a3b8", fontSize: 12 }}>
                  No allocations made yet for this shift
                </div>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#0f172a" }}>
                  <thead>
                    <tr style={{ textAlign: "left", background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
                      <th style={thStyle}>Process</th>
                      <th style={thStyle}>Operator</th>
                      <th style={thStyle}>Skill</th>
                      <th style={thStyle}>Allocated</th>
                      <th style={thStyle}>By</th>
                      <th style={thStyle}>Removed</th>
                      <th style={thStyle}>Match</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map(h => {
                      const isActive = !h.removed_at;
                      const c = SKILL_COLOR(h.op_skill || 1);
                      return (
                        <tr key={h.id} style={{
                          borderBottom: "1px solid #f1f5f9",
                          opacity: isActive ? 1 : 0.55,
                        }}>
                          <td style={tdStyle}>{h.process_name}</td>
                          <td style={tdStyle}>{h.full_name}</td>
                          <td style={tdStyle}>
                            <span style={{ color: c, fontWeight: 700 }}>L{h.op_skill}</span>
                            <span style={{ color: "#94a3b8", margin: "0 4px" }}>→</span>
                            <span style={{ color: SKILL_COLOR(h.required_skill_level), fontWeight: 700 }}>L{h.required_skill_level}</span>
                          </td>
                          <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: 11, color: "#475569" }}>
                            {h.allocated_at ? new Date(h.allocated_at).toLocaleString() : "—"}
                          </td>
                          <td style={tdStyle}>{h.allocated_by || "—"}</td>
                          <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: 11, color: "#475569" }}>
                            {h.removed_at ? new Date(h.removed_at).toLocaleString() : <em style={{ color: "#16a34a" }}>active</em>}
                          </td>
                          <td style={tdStyle}>
                            {h.skill_match_flag === false
                              ? <span style={{ color: "#dc2626", fontWeight: 800 }}>✗</span>
                              : <span style={{ color: "#16a34a", fontWeight: 800 }}>✓</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ═══ ADD OPERATOR MODAL ═════════════════════════════════════════ */}
      {addModal && (
        <div onClick={() => setAddModal(false)}
             style={{
               position: "fixed", inset: 0, background: "rgba(15,23,42,.4)",
               backdropFilter: "blur(4px)", zIndex: 1000,
               display: "flex", alignItems: "center", justifyContent: "center",
             }}>
          <div onClick={e => e.stopPropagation()}
               style={{
                 background: "#fff",
                 border: "1px solid #e2e8f0",
                 borderRadius: 18, padding: 28, width: 480, maxWidth: "90vw",
                 maxHeight: "85vh", display: "flex", flexDirection: "column",
                 boxShadow: "0 24px 80px rgba(0,0,0,.18)",
               }}>
            <div style={{ marginBottom: 18 }}>
              <div style={{ fontSize: 18, fontWeight: 800, color: "#0f172a", fontFamily: "'Barlow Condensed',sans-serif", letterSpacing: ".02em" }}>
                ADD OPERATOR TO POOL
              </div>
              <div style={{ fontSize: 11, color: "#64748b", marginTop: 4 }}>
                Manual punch for operators who didn't scan their badge.
              </div>
            </div>

            <input autoFocus placeholder="Search by name, badge, or employee id..."
                   value={pickSearch} onChange={e => setPickSearch(e.target.value)}
                   style={{
                     width: "100%", padding: "10px 14px", fontSize: 13,
                     background: "#f8fafc",
                     border: "1.5px solid #e2e8f0", borderRadius: 10,
                     color: "#0f172a", marginBottom: 12, outline: "none",
                   }} />

            <div className="col-scroll" style={{ flex: 1, overflowY: "auto", marginBottom: 14 }}>
              {addableOperators.length === 0 ? (
                <div style={{ padding: 28, textAlign: "center", color: "#94a3b8", fontSize: 12 }}>
                  {pickSearch ? "No operators match your search" : "All active operators are already in the pool"}
                </div>
              ) : (
                addableOperators.map(o => (
                  <div key={o.id} onClick={() => setPickOpId(String(o.id))}
                       style={{
                         display: "flex", alignItems: "center", gap: 10,
                         padding: "8px 10px", marginBottom: 6,
                         background: pickOpId === String(o.id) ? "#dbeafe" : "#f8fafc",
                         border: `1px solid ${pickOpId === String(o.id) ? "#2563eb" : "#e2e8f0"}`,
                         borderRadius: 10, cursor: "pointer",
                         transition: "background .12s",
                       }}>
                    <Avatar op={o} size={36} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12, fontWeight: 700, color: "#0f172a" }}>{o.full_name}</div>
                      <div style={{ fontSize: 10, color: "#64748b", fontFamily: "monospace" }}>
                        {o.badge_code}{o.employee_id ? ` · ${o.employee_id}` : ""}{o.department ? ` · ${o.department}` : ""}
                      </div>
                    </div>
                    <span style={{
                      fontSize: 9, fontWeight: 800, padding: "2px 8px", borderRadius: 99,
                      background: `${SKILL_COLOR(o.skill_level||1)}1a`, color: SKILL_COLOR(o.skill_level||1),
                    }}>L{o.skill_level || 1}</span>
                  </div>
                ))
              )}
            </div>

            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: 10, color: "#94a3b8" }}>
                Need new? Admin → Admin → Operators
              </span>
              <div style={{ display: "flex", gap: 8 }}>
                <button onClick={() => setAddModal(false)} style={glassBtn}>Cancel</button>
                <button onClick={manualPunch} disabled={!pickOpId}
                        style={{ ...primaryBtn, opacity: pickOpId ? 1 : 0.4, cursor: pickOpId ? "pointer" : "not-allowed" }}>
                  Add to Pool
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {toastV && <Toast msg={toastV.msg} kind={toastV.kind} />}
      <AIAssistant />
    </div>
  );
}

/* ── Tiny helpers ──────────────────────────────────────────────────── */
function FilterCell({ label, children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <label style={{ fontSize: 9, fontWeight: 700, color: "#64748b", letterSpacing: ".12em", textTransform: "uppercase" }}>{label}</label>
      {children}
    </div>
  );
}
function StatLine({ label, value, color }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 14, alignItems: "baseline", minWidth: 130 }}>
      <span style={{ fontSize: 10, color: "#64748b", letterSpacing: ".1em", textTransform: "uppercase" }}>{label}</span>
      <span style={{ fontSize: 14, fontWeight: 800, color, fontFamily: "'Barlow Condensed',sans-serif" }}>{value}</span>
    </div>
  );
}
function Pill({ color, children }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: "5px 12px", borderRadius: 99,
      background: `${color}1a`, color, border: `1px solid ${color}44`,
      fontSize: 11, fontWeight: 600,
    }}>{children}</span>
  );
}

/* ── Style tokens ──────────────────────────────────────────────────── */
const glassSelect = {
  padding: "8px 12px", fontSize: 13,
  background: "#f8fafc",
  border: "1.5px solid #e2e8f0", borderRadius: 10,
  color: "#0f172a", outline: "none", minWidth: 130,
  fontFamily: "'Barlow',sans-serif",
};
const glassBtn = {
  padding: "8px 16px", fontSize: 12, fontWeight: 700,
  background: "#f8fafc", color: "#334155",
  border: "1px solid #e2e8f0", borderRadius: 10,
  cursor: "pointer", fontFamily: "'Barlow',sans-serif",
};
const primaryBtn = {
  padding: "8px 18px", fontSize: 12, fontWeight: 800,
  background: "linear-gradient(135deg, #3b82f6, #8b5cf6)",
  color: "#fff", border: "none", borderRadius: 10,
  cursor: "pointer", letterSpacing: ".02em",
  boxShadow: "0 2px 8px rgba(59,130,246,.3)",
  fontFamily: "'Barlow',sans-serif",
};
const thStyle = { padding: "11px 10px", fontSize: 10, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: "#64748b" };
const tdStyle = { padding: "9px 10px", fontSize: 12, color: "#0f172a" };
