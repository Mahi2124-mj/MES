/* ───────────────────────────────────────────────────────────────────
 * MaintenanceDashboard.jsx
 * ───────────────────────────────────────────────────────────────────
 * Dedicated dashboard for the Maintenance user (route = /dashboard
 * when user.departmentSlug === 'maintenance').  Three sections:
 *
 *   1) ANDON live table  — every breakdown still in state='OPEN'.
 *      Has a Fullscreen button that flips the table to the browser
 *      fullscreen API (uses screen aspect-ratio; sizes to viewport).
 *
 *   2) Recent History (last 2 days) — RESOLVED + CLOSED tickets.
 *      Each row has a "Fill Closure Form" action that opens the
 *      Toyota Boshoku BREAK DOWN SLIP modal (see ClosureFormModal).
 *
 *   3) Zone + Line stats — LTTR (longest repair) per zone + MTTR/MTBF
 *      per line, grouped by zone.
 *
 * Closure form is now fixed to the Toyota Boshoku format
 * (TBDI/MAINT/F/001) — admin no longer configures fields.  Auto-fills
 * the date/shift/line/start/end/duration cells from the breakdown
 * record so the user only types the manual portions.
 *
 * Not built here: the rule that auto-detects when a line goes from
 * Running → Breakdown.  For now, breakdowns are opened manually
 * (via "+ Open Breakdown" button) — the wiring to existing status
 * detection comes when the Maintenance ↔ Quality flow is finalized.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../context/AuthContext";

const API = "";

/* ── Tiny fetch helpers ───────────────────────────────────────────── */
const api = {
  async get(path, token) {
    const r = await fetch(API + path, { headers: { Authorization: `Bearer ${token}` } });
    if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
    return r.json();
  },
  async post(path, body, token) {
    const r = await fetch(API + path, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
    return r.json();
  },
};

/* ── Visual primitives ────────────────────────────────────────────── */
function Btn({ children, onClick, variant = "default", size = "md", disabled, style, title }) {
  const base = {
    border: "none", borderRadius: 8, cursor: disabled ? "not-allowed" : "pointer",
    fontFamily: "inherit", fontWeight: 700, transition: "all .15s",
    opacity: disabled ? 0.55 : 1,
    fontSize: size === "sm" ? 11 : 13,
    padding: size === "sm" ? "5px 10px" : "9px 16px",
  };
  const variants = {
    default: { background: "#fff", color: "#1e40af", border: "1px solid #cbd5e1" },
    primary: { background: "linear-gradient(135deg,#1e40af,#2563eb)", color: "#fff", boxShadow: "0 2px 8px rgba(30,64,175,.25)" },
    danger:  { background: "linear-gradient(135deg,#dc2626,#b91c1c)", color: "#fff" },
    ghost:   { background: "transparent", color: "#475569", border: "1px solid #e2e8f0" },
    success: { background: "linear-gradient(135deg,#16a34a,#15803d)", color: "#fff" },
  };
  return (
    <button onClick={onClick} disabled={disabled} title={title}
            style={{ ...base, ...variants[variant], ...style }}>
      {children}
    </button>
  );
}

function StatCard({ label, value, sub, color = "#1e40af" }) {
  return (
    <div style={{
      background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12,
      padding: "14px 18px", minWidth: 140, flex: "0 0 auto",
      boxShadow: "0 1px 3px rgba(0,0,0,.04)",
    }}>
      <div style={{ fontSize: 10, color: "#64748b", fontWeight: 700,
                     letterSpacing: ".08em", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 800, color, marginTop: 2,
                     fontFamily: "'Barlow Condensed',sans-serif" }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 1 }}>{sub}</div>}
    </div>
  );
}

/* ── Duration helpers ─────────────────────────────────────────────── */
function fmtDuration(seconds) {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min${m > 1 ? "s" : ""}`;
  const h = Math.floor(m / 60), rem = m % 60;
  return rem ? `${h}h ${rem}m` : `${h} hr${h > 1 ? "s" : ""}`;
}

function fmtClock(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch { return "—"; }
}

function fmtDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-IN", {
      day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    });
  } catch { return "—"; }
}

/* ════════════════════════════════════════════════════════════════════
 * 1) ANDON Live Table
 * ════════════════════════════════════════════════════════════════════ */
function AndonTable({ rows, fullscreenRef, isFullscreen, toggleFullscreen }) {
  // Tick at 1Hz so duration column stays live without re-fetch.
  const [, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const live = (r) => {
    if (!r.started_at) return 0;
    return Math.floor((Date.now() - new Date(r.started_at).getTime()) / 1000);
  };

  return (
    <div ref={fullscreenRef} style={{
      background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14,
      overflow: "hidden", boxShadow: "0 1px 3px rgba(0,0,0,.04)",
      // When fullscreen, fill the screen and centre vertically.
      ...(isFullscreen ? {
        height: "100vh", width: "100vw", display: "flex",
        flexDirection: "column", borderRadius: 0, border: "none",
      } : {}),
    }}>
      <div style={{
        padding: "14px 20px",
        background: "linear-gradient(135deg,#dc2626,#b91c1c)",
        color: "#fff", display: "flex", alignItems: "center",
        justifyContent: "space-between", gap: 10,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 22, animation: rows.length ? "blinkDot 1.2s infinite" : "none" }}>🔔</span>
          <div>
            <div style={{ fontFamily: "'Barlow Condensed',sans-serif",
                          fontSize: 22, fontWeight: 800, letterSpacing: ".02em" }}>
              MAINTENANCE ANDON
            </div>
            <div style={{ fontSize: 11, opacity: 0.9, fontWeight: 600 }}>
              {rows.length === 0 ? "All lines running ✓" : `${rows.length} active breakdown${rows.length>1?"s":""}`}
            </div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          {/* No manual "Open Breakdown" — entries arrive automatically
              from the collector when the line's status bit goes to
              BREAKDOWN.  Auto-resolves when status returns to RUNNING. */}
          <Btn variant="ghost" size="sm" onClick={toggleFullscreen}
               style={{ background: "rgba(255,255,255,.18)", color: "#fff", borderColor: "rgba(255,255,255,.35)" }}
               title={isFullscreen ? "Exit fullscreen" : "Fullscreen view"}>
            {isFullscreen ? "🗗 Exit Fullscreen" : "⛶ Fullscreen"}
          </Btn>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: "auto",
                     fontSize: isFullscreen ? "1.6vmin" : 13,
                     padding: isFullscreen ? "1vmin 2vmin" : 0 }}>
        <table style={{ width: "100%", borderCollapse: "collapse",
                         fontSize: isFullscreen ? "inherit" : 13 }}>
          <thead>
            <tr style={{ background: "#f8fafc" }}>
              {["S.No", "Zone", "Line Name", "Start Time", "Duration"].map((h, i) => (
                <th key={i} style={{
                  padding: isFullscreen ? "1.4vmin 1.6vmin" : "12px 16px",
                  textAlign: "left",
                  fontSize: isFullscreen ? "1.2vmin" : 10,
                  fontWeight: 800, letterSpacing: ".1em",
                  textTransform: "uppercase", color: "#64748b",
                  borderBottom: "2px solid #e2e8f0",
                  whiteSpace: "nowrap",
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={5} style={{ padding: isFullscreen ? "8vmin" : "60px 20px",
                                            textAlign: "center", color: "#94a3b8",
                                            fontStyle: "italic",
                                            fontSize: isFullscreen ? "2vmin" : 14 }}>
                  No active breakdowns — all lines running smoothly. ✨
                </td>
              </tr>
            ) : rows.map((r) => (
              <tr key={r.id} style={{
                borderBottom: "1px solid #f1f5f9",
                background: live(r) > 30*60 ? "rgba(220,38,38,.04)" : "transparent",
              }}>
                <td style={{ padding: isFullscreen ? "1.4vmin 1.6vmin" : "12px 16px",
                              fontFamily: "'Barlow Condensed',sans-serif",
                              fontSize: isFullscreen ? "2.2vmin" : 18,
                              fontWeight: 800, color: "#dc2626" }}>
                  {r.serial_in_shift ?? "—"}
                </td>
                <td style={{ padding: isFullscreen ? "1.4vmin 1.6vmin" : "12px 16px",
                              fontWeight: 600, color: "#0f172a" }}>
                  {r.zone_name ? (
                    <span style={{ display: "inline-block", padding: "3px 10px",
                                    borderRadius: 99,
                                    background: "rgba(30,64,175,.1)",
                                    color: "#1e40af", fontSize: isFullscreen ? "1.4vmin" : 11,
                                    fontWeight: 700 }}>
                      {r.zone_name}
                    </span>
                  ) : <span style={{ color: "#cbd5e1" }}>—</span>}
                </td>
                <td style={{ padding: isFullscreen ? "1.4vmin 1.6vmin" : "12px 16px",
                              fontWeight: 700, color: "#0f172a" }}>
                  {r.line_name || `Line ${r.line_id}`}
                  {r.line_code && <span style={{ marginLeft: 6, fontSize: isFullscreen ? "1.2vmin" : 10,
                                                    color: "#94a3b8", fontFamily: "monospace" }}>
                    {r.line_code}
                  </span>}
                </td>
                <td style={{ padding: isFullscreen ? "1.4vmin 1.6vmin" : "12px 16px",
                              fontFamily: "monospace", color: "#475569" }}>
                  {fmtClock(r.started_at)}
                </td>
                <td style={{ padding: isFullscreen ? "1.4vmin 1.6vmin" : "12px 16px",
                              fontFamily: "'Barlow Condensed',sans-serif",
                              fontSize: isFullscreen ? "2.2vmin" : 18,
                              fontWeight: 800,
                              color: live(r) > 30*60 ? "#dc2626" : "#b45309" }}>
                  {fmtDuration(live(r))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <style>{`
        @keyframes blinkDot { 0%,100% { opacity: 1 } 50% { opacity: 0.35 } }
      `}</style>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════
 * 2) Recent History Table
 * ════════════════════════════════════════════════════════════════════ */
function HistoryTable({ rows, onCloseTicket, onViewTicket, slipThresholdMin = 10 }) {
  // 2026-05-20 — Filter out MINOR breakdowns (duration < threshold).
  // These don't need slip closure and shouldn't clutter the maintenance
  // table.  Operator decision: anything fixed under the slip-raise
  // threshold is auto-resolved, just-log-time only — they're tracked in
  // MTBF stats but hidden from this prompt list.
  const thresholdSec = Math.max(0, (slipThresholdMin || 0) * 60);
  const visibleRows  = rows.filter(r => (r.duration_seconds || 0) >= thresholdSec);
  const minorHidden  = rows.length - visibleRows.length;
  return (
    <div style={{
      background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14,
      overflow: "hidden", boxShadow: "0 1px 3px rgba(0,0,0,.04)",
    }}>
      <div style={{
        padding: "14px 20px", borderBottom: "1px solid #e2e8f0",
        background: "#f8fafc",
        display: "flex", alignItems: "center", justifyContent: "space-between",
      }}>
        <div>
          <div style={{ fontFamily: "'Barlow Condensed',sans-serif",
                          fontSize: 18, fontWeight: 800, color: "#0f172a" }}>
            Recent Breakdowns
          </div>
          <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>
            Last 2 days + all unclosed · {visibleRows.length} ticket{visibleRows.length === 1 ? "" : "s"}
            &nbsp;·&nbsp;
            <span style={{ display:"inline-block", padding:"1px 7px", borderRadius:99,
                            background:"rgba(217,119,6,.12)", color:"#b45309",
                            fontWeight:700, fontSize:10 }}>
              {visibleRows.filter(r => r.state === "RESOLVED").length} pending closure
            </span>
            {minorHidden > 0 && (
              <>
                &nbsp;·&nbsp;
                <span title={`Auto-hidden: ${minorHidden} breakdown(s) fixed in under ${slipThresholdMin} min — slip not required (MTBF stats still include them)`}
                      style={{ display:"inline-block", padding:"1px 7px", borderRadius:99,
                                background:"rgba(100,116,139,.12)", color:"#475569",
                                fontWeight:600, fontSize:10, cursor:"help" }}>
                  {minorHidden} minor hidden (&lt; {slipThresholdMin} min)
                </span>
              </>
            )}
          </div>
        </div>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ background: "#fafbfc" }}>
              {["Line", "Zone", "Start", "End", "Duration", "Status", "Closure"].map((h) => (
                <th key={h} style={{ padding: "10px 14px", textAlign: "left",
                                       fontSize: 9, fontWeight: 800,
                                       letterSpacing: ".08em",
                                       textTransform: "uppercase",
                                       color: "#64748b",
                                       borderBottom: "1px solid #e2e8f0",
                                       whiteSpace: "nowrap" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleRows.length === 0 ? (
              <tr>
                <td colSpan={7} style={{ padding: "40px 20px", textAlign: "center",
                                            color: "#94a3b8", fontStyle: "italic" }}>
                  No pending breakdowns.
                </td>
              </tr>
            ) : visibleRows.map((r) => {
              const closed = r.state === "CLOSED";
              return (
                <tr key={r.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                  <td style={{ padding: "10px 14px", fontWeight: 700, color: "#0f172a" }}>
                    {r.line_name || `Line ${r.line_id}`}
                  </td>
                  <td style={{ padding: "10px 14px", color: "#475569" }}>
                    {r.zone_name || "—"}
                  </td>
                  <td style={{ padding: "10px 14px", fontFamily: "monospace",
                                  color: "#475569", fontSize: 11 }}>
                    {fmtDateTime(r.started_at)}
                  </td>
                  <td style={{ padding: "10px 14px", fontFamily: "monospace",
                                  color: "#475569", fontSize: 11 }}>
                    {fmtDateTime(r.ended_at)}
                  </td>
                  <td style={{ padding: "10px 14px", fontWeight: 700, color: "#b45309" }}>
                    {fmtDuration(r.duration_seconds)}
                  </td>
                  <td style={{ padding: "10px 14px" }}>
                    <span style={{ padding: "3px 10px", borderRadius: 99, fontSize: 10,
                                     fontWeight: 700, letterSpacing: ".05em",
                                     textTransform: "uppercase",
                                     background: closed ? "rgba(22,163,74,.12)" : "rgba(217,119,6,.12)",
                                     color: closed ? "#15803d" : "#b45309" }}>
                      {r.state}
                    </span>
                  </td>
                  <td style={{ padding: "10px 14px" }}>
                    {closed ? (
                      <Btn size="sm" variant="ghost" onClick={() => onViewTicket(r)}>View</Btn>
                    ) : (
                      <Btn size="sm" variant="primary" onClick={() => onCloseTicket(r)}>
                        Fill Closure Form
                      </Btn>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════
 * 2.5) Maintenance KPI panel (auto-computed + target compare + CSV
 *      download).  Sits between History and Zone&Line Stats.
 * ════════════════════════════════════════════════════════════════════ */
const KPI_PERIODS = [
  { key: "today",     label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "7d",        label: "Last 7 days" },
  { key: "30d",       label: "Last 30 days" },
  { key: "90d",       label: "Last 90 days" },
];

function KpiPanel({ token, lines }) {
  const [period,  setPeriod]  = useState("7d");
  const [lineId,  setLineId]  = useState("");          // "" = all lines
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(false);
  const [err,     setErr]     = useState(null);

  const reload = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const qs = new URLSearchParams({ period });
      if (lineId) qs.set("line_id", lineId);
      const r = await api.get(`/api/maintenance-kpi/?${qs.toString()}`, token);
      setData(r);
    } catch (e) { setErr(e.message || "Load failed"); }
    finally    { setLoading(false); }
  }, [period, lineId, token]);

  useEffect(() => { reload(); }, [reload]);
  // Refresh every 60 s — KPIs don't move every second so this is plenty.
  useEffect(() => {
    const t = setInterval(reload, 60_000);
    return () => clearInterval(t);
  }, [reload]);

  const downloadCsv = async () => {
    try {
      const qs = new URLSearchParams({ period });
      if (lineId) qs.set("line_id", lineId);
      const r = await fetch(`/api/maintenance-kpi/export.csv?${qs.toString()}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
      const blob = await r.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href = url;
      a.download = `maintenance_kpi_${period}_${new Date().toISOString().slice(0,10)}.csv`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) { alert(`Download failed: ${e.message}`); }
  };

  return (
    <div style={{
      background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14,
      overflow: "hidden", boxShadow: "0 1px 3px rgba(0,0,0,.04)",
    }}>
      {/* Header bar with filters + download */}
      <div style={{
        padding: "12px 18px", borderBottom: "1px solid #e2e8f0",
        background: "#fafbfc",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        flexWrap: "wrap", gap: 12,
      }}>
        <div>
          <div style={{ fontFamily: "'Barlow Condensed',sans-serif",
                          fontSize: 18, fontWeight: 800, color: "#0f172a" }}>
            Maintenance KPI Dashboard
          </div>
          <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>
            {data?.window?.label || "—"} ·
            {data?.line_name ? ` ${data.line_name}` : " All lines"} ·
            Targets editable in Admin Panel → KPI Targets
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <select value={period} onChange={(e) => setPeriod(e.target.value)}
                  style={kpiSelect}>
            {KPI_PERIODS.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
          <select value={lineId} onChange={(e) => setLineId(e.target.value)}
                  style={kpiSelect}>
            <option value="">All lines</option>
            {(lines || []).map(l => (
              <option key={l.id} value={l.id}>{l.line_name}</option>
            ))}
          </select>
          <Btn size="sm" onClick={reload} disabled={loading}>{loading?"…":"↻"}</Btn>
          <Btn size="sm" variant="primary" onClick={downloadCsv}>⬇ CSV</Btn>
        </div>
      </div>

      {/* Cards grid */}
      <div style={{ padding: 18 }}>
        {err ? (
          <div style={{ padding: 16, color: "#dc2626", fontSize: 13 }}>
            Failed to load KPIs: {err}
          </div>
        ) : !data ? (
          <div style={{ padding: 24, textAlign: "center", color: "#94a3b8",
                          fontStyle: "italic" }}>
            Computing…
          </div>
        ) : (
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
            gap: 14,
          }}>
            {data.kpis.map(c => <KpiCard key={c.kpi_key} card={c} />)}
          </div>
        )}
      </div>
    </div>
  );
}

const kpiSelect = {
  padding: "6px 10px", fontSize: 12, fontFamily: "inherit",
  borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff",
  cursor: "pointer", color: "#334155", fontWeight: 600,
};

function KpiCard({ card }) {
  const v = card.verdict;          // 'pass' | 'fail' | 'na'
  const accent = v === "pass" ? "#16a34a" : v === "fail" ? "#dc2626" : "#94a3b8";
  const arrow  = card.direction === "higher" ? "↑" : "↓";
  const fmtVal = (x) => {
    if (x == null) return "—";
    if (typeof x !== "number") return String(x);
    return Number.isInteger(x) ? x.toString() : x.toFixed(2);
  };
  return (
    <div style={{
      background: "#fff", border: `1px solid ${v === "fail" ? "rgba(220,38,38,.25)" : "#e2e8f0"}`,
      borderLeft: `4px solid ${accent}`,
      borderRadius: 10, padding: "14px 16px",
      boxShadow: "0 1px 2px rgba(0,0,0,.03)",
      position: "relative",
    }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: "#64748b",
                       letterSpacing: ".08em", textTransform: "uppercase" }}>
        {card.label}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginTop: 6 }}>
        <div style={{ fontFamily: "'Barlow Condensed',sans-serif",
                          fontSize: 32, fontWeight: 800, color: "#0f172a", lineHeight: 1 }}>
          {fmtVal(card.value)}
        </div>
        <div style={{ fontSize: 11, color: "#64748b", fontWeight: 600 }}>
          {card.unit}
        </div>
      </div>
      <div style={{ fontSize: 11, color: "#64748b", marginTop: 10, display: "flex",
                       alignItems: "center", gap: 6, justifyContent: "space-between" }}>
        <span>
          Target {arrow} <b style={{ color: "#334155" }}>{fmtVal(card.target)} {card.unit}</b>
        </span>
        <span style={{
          fontSize: 9, fontWeight: 800, letterSpacing: ".08em",
          textTransform: "uppercase",
          padding: "2px 8px", borderRadius: 99,
          background: v === "pass" ? "rgba(22,163,74,.10)"
                    : v === "fail" ? "rgba(220,38,38,.10)" : "#f1f5f9",
          color: accent,
        }}>
          {v === "pass" ? "✓ on target" : v === "fail" ? "✗ off target" : "—"}
        </span>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════
 * 3) Stats — per-zone summary + drill-down by line
 * ════════════════════════════════════════════════════════════════════ */
function StatsSection({ stats }) {
  const zones = stats?.zones || [];
  const lines = stats?.lines || [];

  // Group lines by their zone for the drill-down rendering.
  const linesByZone = useMemo(() => {
    const m = {};
    for (const l of lines) {
      const k = l.zone_id ?? 0;
      (m[k] = m[k] || []).push(l);
    }
    return m;
  }, [lines]);

  if (zones.length === 0 && lines.length === 0) {
    return (
      <div style={{ background: "#fff", border: "1px solid #e2e8f0",
                     borderRadius: 14, padding: "32px 24px",
                     textAlign: "center", color: "#94a3b8", fontStyle: "italic" }}>
        No breakdown stats yet — once tickets accumulate over a few days,
        zone-level LTTR and per-line MTTR / MTBF will appear here.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {zones.map((z) => {
        const zoneLines = linesByZone[z.zone_id] || [];
        return (
          <div key={z.zone_id ?? "unzoned"} style={{
            background: "#fff", border: "1px solid #e2e8f0",
            borderRadius: 14, overflow: "hidden",
            boxShadow: "0 1px 3px rgba(0,0,0,.04)",
          }}>
            <div style={{
              padding: "14px 20px",
              background: "linear-gradient(135deg,#1e3a8a,#1e40af)",
              color: "#fff",
              display: "flex", alignItems: "center", justifyContent: "space-between",
              flexWrap: "wrap", gap: 12,
            }}>
              <div style={{ fontFamily: "'Barlow Condensed',sans-serif",
                             fontSize: 22, fontWeight: 800, letterSpacing: ".02em" }}>
                {z.zone_name || "(unzoned)"}
              </div>
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                <div title="Longest repair time seen on any closed breakdown in this zone over the window">
                  <div style={{ fontSize: 9, fontWeight: 700, opacity: 0.8,
                                  letterSpacing: ".1em", textTransform: "uppercase" }}>
                    LTTR (longest)
                  </div>
                  <div style={{ fontFamily: "'Barlow Condensed',sans-serif",
                                  fontSize: 22, fontWeight: 800 }}>
                    {z.lttr_minutes != null ? `${z.lttr_minutes} min` : "—"}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 9, fontWeight: 700, opacity: 0.8,
                                  letterSpacing: ".1em", textTransform: "uppercase" }}>
                    Breakdowns
                  </div>
                  <div style={{ fontFamily: "'Barlow Condensed',sans-serif",
                                  fontSize: 22, fontWeight: 800 }}>
                    {z.breakdowns_count}
                  </div>
                </div>
              </div>
            </div>

            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ background: "#fafbfc" }}>
                    {["Line", "MTBF", "MTTR", "Breakdowns"].map((h) => (
                      <th key={h} style={{ padding: "8px 14px", textAlign: "left",
                                              fontSize: 9, fontWeight: 800,
                                              letterSpacing: ".08em",
                                              textTransform: "uppercase",
                                              color: "#64748b",
                                              borderBottom: "1px solid #e2e8f0" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {zoneLines.length === 0 ? (
                    <tr><td colSpan={4} style={{ padding: 16, textAlign: "center",
                                                    color: "#cbd5e1", fontStyle: "italic" }}>
                      No line-level breakdowns recorded yet.
                    </td></tr>
                  ) : zoneLines.map((l) => (
                    <tr key={l.line_id} style={{ borderBottom: "1px solid #f8fafc" }}>
                      <td style={{ padding: "8px 14px", fontWeight: 700, color: "#0f172a" }}>
                        {l.line_name || `Line ${l.line_id}`}
                      </td>
                      <td style={{ padding: "8px 14px",
                                      fontFamily: "'Barlow Condensed',sans-serif",
                                      fontSize: 16, fontWeight: 800, color: "#dc2626" }}>
                        {l.mtbf_hours != null ? `${l.mtbf_hours} h` : "—"}
                      </td>
                      <td style={{ padding: "8px 14px",
                                      fontFamily: "'Barlow Condensed',sans-serif",
                                      fontSize: 16, fontWeight: 800, color: "#d97706" }}>
                        {l.mttr_minutes != null ? `${l.mttr_minutes} min` : "—"}
                      </td>
                      <td style={{ padding: "8px 14px", color: "#475569",
                                      fontFamily: "monospace", fontSize: 11, fontWeight: 700 }}>
                        {l.breakdowns_count}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}

      {/* Lines without a recorded zone fall through here */}
      {(linesByZone[0] || linesByZone[null] || []).length > 0 && zones.length === 0 && (
        <div style={{ fontSize: 11, color: "#94a3b8", fontStyle: "italic" }}>
          (Lines below have no zone assigned — assign zones in Admin Panel → Lines.)
        </div>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════
 * Closure form modal — Toyota Boshoku BREAK DOWN SLIP
 * (TBDI/MAINT/F/001 · REV. 00 · 20/03/2024)
 * ════════════════════════════════════════════════════════════════════
 * Layout matches the paper form one-to-one.  Fields auto-populated from
 * the breakdown record (date / shift / line / start time / end time /
 * down time minutes) are pre-filled but stay editable in case the
 * filer wants to override.  Everything else is typed by the user.
 *
 * Submitted payload shape (stored as mes_breakdowns.closure_data JSONB):
 *   {
 *     zone, line, machine_no, machine_name, date,
 *     shift, line_leader_name, model_no, machine_operator_name,
 *     category, // 'A' | 'B' | 'C'
 *     bd_start_time, bd_received_time, bd_ok_time,
 *     bd_start_date, bd_end_date, mc_down_time_minutes,
 *     problem_reported_by_production,
 *     problem_related_to, // { maintenance: bool, tool_room: bool }
 *     actual_problem_observed,
 *     action_taken_on_problem,
 *     spares_used,
 *     bd_attended_by,
 *     prepared_by:        { name },
 *     received_by:        { name },
 *     line_leader_operator: { name },
 *     quality_engineer:   { name },
 *   }
 * (Older slips kept a `sign` sibling — preserved in JSONB, no longer
 * displayed in the form since virtual signatures aren't meaningful.)
 *
 * Auto-fill behaviour:
 *   • ZONE / LINE / DATE / SHIFT / B/D times / Down time
 *     → from the breakdown record itself.
 *   • MACHINE NAME ← lookup in mes_machines by (zone, line, machine_no).
 *     We pull the machine list once on open via
 *     /api/machines/by-line/{line_id} and match client-side, so typing
 *     is instantaneous and works offline of the lookup endpoint after
 *     the first fetch.
 */
export function ClosureFormModal({ ticket, mode, phase = "maintenance", onClose, onSave, token }) {
  // mode  : "fill" | "view"
  // phase : "production" → user can edit only the upper half (Production)
  //         "maintenance" → user can edit only the lower half (Maintenance)
  //         B/D Start/End Time + Date + Down-Time minutes are LOCKED for
  //         both phases (collector stamps these from started_at / ended_at).
  const readOnly      = mode === "view";
  const isProduction  = phase === "production" && !readOnly;
  const isMaintenance = phase === "maintenance" && !readOnly;

  // Cell-level lock helpers: each cell consults whether the active phase
  // is allowed to edit *that field*.
  const PROD_FIELDS = new Set([
    "zone", "line", "machine_no", "machine_name", "date", "shift",
    "line_leader_name", "model_no", "machine_operator_name",
    "category", "bd_received_time", "problem_reported_by_production",
  ]);
  const MAINT_FIELDS = new Set([
    "problem_related_to", "type_of_problem",
    "actual_problem_observed", "action_taken_on_problem",
    "spares_used", "bd_attended_by",
    "prepared_by", "received_by", "line_leader_operator", "quality_engineer",
  ]);
  // Always-locked: collector-stamped timestamps.  Even read-only mode
  // shows them, but no role can ever edit them.
  const LOCKED_FIELDS = new Set([
    "bd_start_time", "bd_start_date", "bd_ok_time", "bd_end_date",
    "mc_down_time_minutes",
  ]);
  const fieldEditable = (key) => {
    if (readOnly) return false;
    if (LOCKED_FIELDS.has(key)) return false;
    if (isProduction)  return PROD_FIELDS.has(key);
    if (isMaintenance) return MAINT_FIELDS.has(key);
    return false;
  };

  const [data, setData] = useState({});
  const [saving, setSaving] = useState(false);
  const [machines, setMachines] = useState([]);  // [{machine_no, machine_name}]

  // ── Auto-fill on first open from the breakdown record ─────────────
  useEffect(() => {
    if (!ticket) return;
    const start = ticket.started_at ? new Date(ticket.started_at) : null;
    const end   = ticket.ended_at   ? new Date(ticket.ended_at)   : null;
    const fmtDate = (d) => d ? d.toISOString().slice(0,10) : "";
    const fmtTime = (d) => d ? d.toTimeString().slice(0,5)  : "";
    const downMin = ticket.duration_seconds
      ? Math.round(ticket.duration_seconds / 60)
      : null;

    // Pull existing halves from the breakdown record (so re-opening shows
    // what's already filled).  In "view" mode we also fall back to the
    // legacy single closure_data blob for older rows.
    const prod  = ticket.production_data  || {};
    const maint = ticket.maintenance_data || {};
    const legacy = readOnly ? (ticket.closure_data || {}) : {};

    // Auto-locked timestamps always sourced from collector — never from
    // any saved blob — so they reflect the live record.
    setData({
      // Production half (or carried-over)
      zone:                  prod.zone               ?? legacy.zone               ?? ticket.zone_name ?? "",
      line:                  prod.line               ?? legacy.line               ?? ticket.line_name ?? "",
      machine_no:            prod.machine_no         ?? legacy.machine_no         ?? "",
      machine_name:          prod.machine_name       ?? legacy.machine_name       ?? "",
      date:                  prod.date               ?? legacy.date               ?? fmtDate(start),
      shift:                 prod.shift              ?? legacy.shift              ?? ticket.shift_name ?? "",
      line_leader_name:      prod.line_leader_name   ?? legacy.line_leader_name   ?? "",
      model_no:              prod.model_no           ?? legacy.model_no           ?? "",
      machine_operator_name: prod.machine_operator_name ?? legacy.machine_operator_name ?? "",
      category:              prod.category           ?? legacy.category           ?? "",
      bd_received_time:      prod.bd_received_time   ?? legacy.bd_received_time   ?? "",
      problem_reported_by_production:
        prod.problem_reported_by_production ?? legacy.problem_reported_by_production ?? "",

      // Always-locked (collector source)
      bd_start_time:        fmtTime(start),
      bd_ok_time:           fmtTime(end),
      bd_start_date:        fmtDate(start),
      bd_end_date:          fmtDate(end),
      mc_down_time_minutes: downMin != null ? String(downMin) : "",

      // Maintenance half (or carried-over)
      problem_related_to:      maint.problem_related_to      ?? legacy.problem_related_to      ?? { maintenance: true, tool_room: false },
      // 2026-05-20 — Multi-select (electrical and/or mechanical can both
      // be ticked, unlike problem_related_to which is single-pick).
      type_of_problem:         maint.type_of_problem         ?? legacy.type_of_problem         ?? { electrical: false, mechanical: false },
      actual_problem_observed: maint.actual_problem_observed ?? legacy.actual_problem_observed ?? "",
      action_taken_on_problem: maint.action_taken_on_problem ?? legacy.action_taken_on_problem ?? "",
      spares_used:             maint.spares_used             ?? legacy.spares_used             ?? "",
      bd_attended_by:          maint.bd_attended_by          ?? legacy.bd_attended_by          ?? "",
      prepared_by:             maint.prepared_by             ?? legacy.prepared_by             ?? { name: "" },
      received_by:             maint.received_by             ?? legacy.received_by             ?? { name: "" },
      line_leader_operator:    maint.line_leader_operator    ?? legacy.line_leader_operator    ?? { name: "" },
      quality_engineer:        maint.quality_engineer        ?? legacy.quality_engineer        ?? { name: "" },
    });
  }, [ticket?.id, readOnly, phase]);

  // ── Pull the machine master list for this line (one fetch on open) ──
  useEffect(() => {
    if (!ticket?.line_id || !token) return;
    let cancelled = false;
    api.get(`/api/machines/by-line/${ticket.line_id}`, token)
      .then(res => { if (!cancelled) setMachines(res?.machines || []); })
      .catch(() => {});  // empty list — manual entry still works
    return () => { cancelled = true; };
  }, [ticket?.line_id, token]);

  if (!ticket) return null;

  const set    = (k, v) => setData(d => ({ ...d, [k]: v }));
  const setSub = (parent, k, v) =>
    setData(d => ({ ...d, [parent]: { ...(d[parent] || {}), [k]: v } }));

  // Type-ahead: when user changes Machine No., look it up in the
  // pre-fetched machines[] and auto-fill machine_name.  Falls back to
  // the existing typed value if no match (manual entry still works).
  const setMachineNo = (raw) => {
    setData(d => {
      const next = { ...d, machine_no: raw };
      const n = parseInt(raw, 10);
      if (!isNaN(n)) {
        const hit = machines.find(m => m.machine_no === n);
        if (hit) next.machine_name = hit.machine_name;
      } else if (raw === "") {
        next.machine_name = "";
      }
      return next;
    });
  };

  // Extract just the slice of `data` that the active phase is responsible
  // for.  The parent passes this to its API call so the *other* half
  // doesn't get overwritten.
  const subsetForPhase = () => {
    const pick = (set) => Object.fromEntries(
      Object.entries(data).filter(([k]) => set.has(k)),
    );
    if (isProduction)  return pick(PROD_FIELDS);
    if (isMaintenance) return pick(MAINT_FIELDS);
    return data;
  };

  // Required-field gate: per-phase Submit button only enables once every
  // *editable* field has a non-empty value.  Locked + other-phase fields
  // are ignored.
  const phaseComplete = () => {
    const slice = subsetForPhase();
    const checkVal = (v) => {
      if (v == null) return false;
      if (typeof v === "string") return v.trim().length > 0;
      if (typeof v === "object") {
        // For radio (problem_related_to) — require one true.
        // For multi-select (type_of_problem) — require at least one true.
        // For sigs (prepared_by etc) — require a name (sign optional).
        if ("maintenance" in v && "tool_room" in v) return v.maintenance || v.tool_room;
        if ("electrical" in v && "mechanical" in v) return v.electrical || v.mechanical;
        if ("name" in v) return !!(v.name && String(v.name).trim());
        return Object.keys(v).length > 0;
      }
      return true;
    };
    return Object.values(slice).every(checkVal);
  };

  const submit = async () => {
    setSaving(true);
    try { await onSave(subsetForPhase(), phase); }
    finally { setSaving(false); }
  };

  // ── Print the slip via a hidden iframe ────────────────────────────
  // Why an iframe instead of `window.print()` directly?
  //   1. The whole app's DOM (slide nav, dashboard topbar, ANDON tables,
  //      etc.) sits inside <body>.  Even with `visibility: hidden`
  //      everywhere, those elements still occupy layout space — so the
  //      printer ends up emitting the slip on page 1 and an empty page
  //      where the rest of the app *would* be on page 2.
  //   2. window.print() uses the host page's title + URL for the browser-
  //      injected header band ("Historical Data" / "192.168.10.185:5656/…").
  //      An iframe with an empty title and no surrounding chrome side-steps
  //      both — combined with @page margin:0 we get a clean single-sheet
  //      print of just the slip.
  const printSlip = () => {
    const node = document.querySelector(".bds-modal");
    if (!node) return;
    const slipHtml = node.outerHTML;
    // Pull all the <style> blocks the host page has injected so the slip
    // looks identical inside the iframe (font, table grid, colours).
    const styles = Array.from(document.querySelectorAll("style"))
      .map(s => s.innerHTML).join("\n");

    // Build a minimal printable document.  The slip is wrapped in a
    // fixed-A4-landscape container with overflow hidden — after layout,
    // we measure the natural size and apply a CSS scale so it fits the
    // page exactly with no scroll-bars or page breaks.
    //
    // Two-layer fit strategy:
    //   1. Aggressive shrink CSS (small fonts, tight padding) — usually
    //      enough on its own.
    //   2. JS-driven `transform: scale()` fallback for outlier cases
    //      where the shrunk version still overflows the A4 page.
    const html = `
<!doctype html>
<html><head><title></title>
<style>${styles}</style>
<style>
  @page { size: A4 landscape; margin: 0; }
  html, body { margin: 0 !important; padding: 0 !important; background: #fff !important; overflow: hidden; }

  /* Page-sized container — exactly one A4 landscape sheet.  Anything
     that doesn't fit gets clipped; the JS pass below scales the inner
     down so nothing actually clips. */
  .bds-print-page {
    width: 297mm; height: 210mm;
    margin: 0; padding: 0; box-sizing: border-box;
    overflow: hidden; position: relative;
    background: #fff;
  }
  .bds-print-fit {
    transform-origin: top left;
    /* transform: scale(N) injected by JS after layout */
  }

  .bds-modal {
    position: static !important; box-shadow: none !important;
    border-radius: 0 !important; max-width: none !important;
    width: 297mm !important;
    margin: 0 !important; padding: 6mm !important; box-sizing: border-box !important;
  }
  .bds-close-x, .bds-print-btn, .bds-footer { display: none !important; }
  .bds-body { max-height: none !important; overflow: visible !important; padding: 0 !important; }

  /* ── Print-only size shrink ─────────────────────────────────────────
     Tighter padding + smaller fonts than the on-screen modal so the
     full slip fits on a single A4 landscape page.  The JS scale-to-fit
     handles edge cases where it's still slightly too tall. */
  .bds-letterhead { border-bottom-width: 1.5px !important; }
  .bds-logo { width: 90px !important; padding: 4px 6px !important; }
  .bds-logo img { max-height: 50px !important; }
  .bds-logo-sub { font-size: 7px !important; }
  .bds-company { font-size: 14px !important; }
  .bds-doc-title { font-size: 11px !important; margin-top: 1px !important; }

  .bds-cell { min-height: 22px !important; }
  .bds-cell-label { font-size: 8px !important; padding: 3px 6px !important; min-width: 110px !important; }
  .bds-cell-input input { font-size: 10px !important; padding: 2px 6px !important; min-height: 20px !important; }

  .bds-cat-head { padding: 3px 8px !important; font-size: 9px !important; }
  .bds-cat-tickdown { font-size: 7px !important; }
  .bds-cat-row { min-height: 20px !important; }
  .bds-cat-cell-code { font-size: 9px !important; padding: 3px 8px !important; }
  .bds-cat-cell-desc { font-size: 9px !important; padding: 3px 8px !important; }
  .bds-cat-cell-tick input { width: 14px !important; height: 14px !important; }

  .bds-row { min-height: 36px !important; }
  .bds-row-label { font-size: 8px !important; padding: 3px 6px !important; }
  .bds-row-input textarea { font-size: 10px !important; padding: 3px 6px !important; min-height: 36px !important; }

  .bds-divider { padding: 3px 8px !important; font-size: 9px !important; }
  .bds-relto-row { padding: 4px 8px !important; gap: 16px !important; }
  .bds-relto-label { font-size: 9px !important; }
  .bds-relto-opt { font-size: 9px !important; }
  .bds-relto-opt input { width: 13px !important; height: 13px !important; }

  .bds-sign-head { padding: 3px 8px !important; font-size: 9px !important; }
  .bds-sign-head .bds-sign-sub { font-size: 8px !important; }
  .bds-sign-cell { padding: 3px 6px !important; }
  .bds-sign-line { font-size: 8px !important; padding: 2px 0 !important; }
  .bds-sign-line input { font-size: 10px !important; padding: 1px 4px !important; }

  /* Inputs print as plain text on a thin baseline (no input boxes) */
  .bds-cell-input input, .bds-row-input textarea, .bds-sign-line input {
    border: none !important; outline: none !important;
    background: transparent !important;
    color: #000 !important; -webkit-text-fill-color: #000 !important;
    opacity: 1 !important;
  }
  .bds-cell, .bds-row, .bds-cat-row, .bds-sign-grid > * { page-break-inside: avoid; }
</style>
</head><body><div class="bds-print-page"><div class="bds-print-fit">${slipHtml}</div></div></body></html>`;

    const iframe = document.createElement("iframe");
    Object.assign(iframe.style, {
      position: "fixed", right: "0", bottom: "0",
      width: "0", height: "0", border: "0", visibility: "hidden",
    });
    document.body.appendChild(iframe);
    const doc = iframe.contentDocument || iframe.contentWindow?.document;
    if (!doc) { document.body.removeChild(iframe); return; }
    doc.open();  doc.write(html);  doc.close();

    // Give the iframe a tick to lay out, measure, fit-to-page, then print.
    setTimeout(() => {
      try {
        const win = iframe.contentWindow;
        const idoc = win?.document;
        const page = idoc?.querySelector(".bds-print-page");
        const fit  = idoc?.querySelector(".bds-print-fit");
        if (page && fit) {
          // Available landscape print area, in pixels (the iframe doc
          // sized .bds-print-page in mm — we use its measured pixel size
          // so we don't have to know the browser's DPI).
          const targetW = page.clientWidth;
          const targetH = page.clientHeight;
          const naturalW = fit.scrollWidth;
          const naturalH = fit.scrollHeight;
          // Pick the smaller axis ratio so neither dimension overflows.
          // Never scale UP (only down) — full-size content prints
          // unmodified; oversize content shrinks to fit.
          const sx = targetW / naturalW;
          const sy = targetH / naturalH;
          const s  = Math.min(sx, sy, 1);
          if (s < 0.999) fit.style.transform = `scale(${s})`;
        }
        win?.focus();
        win?.print();
      } catch {}
      // Cleanup after the print dialog returns.
      setTimeout(() => {
        if (iframe.parentNode) iframe.parentNode.removeChild(iframe);
      }, 1500);
    }, 300);
  };

  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, background: "rgba(15,23,42,.55)",
      backdropFilter: "blur(2px)", zIndex: 9000,
      display: "flex", alignItems: "flex-start", justifyContent: "center",
      overflowY: "auto", padding: "24px 12px",
    }}>
      <div className="bds-modal" onClick={(e) => e.stopPropagation()} style={{
        width: "100%", maxWidth: 980, background: "#fff",
        borderRadius: 12, boxShadow: "0 20px 60px rgba(0,0,0,.35)",
        overflow: "hidden",
      }}>
        {/* Header — matches the paper form's letterhead */}
        <div className="bds-letterhead">
          <div className="bds-logo">
              <img src="/logo.jpg" alt="logo"
                style={{ width: "70%", height: "70%", objectFit: "contain" }}
                onError={e => { e.target.style.display="none"; }}
              />            <div className="bds-logo-sub">TOYOTA BOSHOKU</div>
          </div>
          <div className="bds-letter-title">
            <div className="bds-company">TOYOTA BOSHOKU DEVICE INDIA PVT. LTD.</div>
            <div className="bds-doc-title">BREAK DOWN SLIP</div>
          </div>
          {/* Print button — only meaningful in read-only view.  Renders
              the slip into a hidden iframe and prints THAT, so we never
              show the surrounding app on paper and the browser-injected
              header/footer (date / URL / page-number band) is dropped
              by the @page margin:0 the iframe carries inside. */}
          {readOnly && (
            <div className="bds-print-btn" onClick={() => printSlip()} title="Print this slip">
              🖨 Print
            </div>
          )}
          <div className="bds-close-x" onClick={onClose} title="Close">×</div>
        </div>

        {/* Body — scrolls inside the modal */}
        <div className="bds-body">
          {/* ── Header grid (3×3) ─────────────────────────────── */}
          {/* ZONE / LINE auto-filled from the breakdown's collector tag.
              MACHINE NO. drives auto-fill of MACHINE NAME via the master
              list pulled from /api/machines/by-line/{line_id}. */}
          <div className="bds-grid bds-grid-3">
            <BdsCell label="ZONE"
                     value={data.zone}             readOnly={!fieldEditable("zone")}
                     onChange={v => set("zone", v)}/>
            <BdsCell label="MACHINE NO." type="number"
                     value={data.machine_no}       readOnly={!fieldEditable("machine_no")}
                     onChange={setMachineNo}/>
            <BdsCell label="DATE" type="date"
                     value={data.date}             readOnly={!fieldEditable("date")}
                     onChange={v => set("date", v)}/>

            <BdsCell label="LINE"
                     value={data.line}             readOnly={!fieldEditable("line")}
                     onChange={v => set("line", v)}/>
            <BdsCell label="SHIFT"
                     value={data.shift}            readOnly={!fieldEditable("shift")}
                     onChange={v => set("shift", v)}/>
            <BdsCell label="LINE LEADER NAME"
                     value={data.line_leader_name} readOnly={!fieldEditable("line_leader_name")}
                     onChange={v => set("line_leader_name", v)}/>

            <BdsCell label="MACHINE NAME"
                     value={data.machine_name}        readOnly={!fieldEditable("machine_name")}
                     onChange={v => set("machine_name", v)}/>
            <BdsCell label="MODEL NO."
                     value={data.model_no}            readOnly={!fieldEditable("model_no")}
                     onChange={v => set("model_no", v)}/>
            <BdsCell label="MACHINE OPERATOR NAME"
                     value={data.machine_operator_name} readOnly={!fieldEditable("machine_operator_name")}
                     onChange={v => set("machine_operator_name", v)}/>
          </div>

          {/* ── Break-down category ────────────────────────────── */}
          <div className="bds-cat-head">
            <div>BREAK DOWN TYPE ( CATEGORY ) :-</div>
            <div className="bds-cat-tickdown">TICK DOWN<br/>(✓)</div>
          </div>
          {[
            { code: "A", desc: "MACHINE OR LINE HAS STOPPED AND PRODUCTION LOSS DIRECTLY" },
            { code: "B", desc: "MACHINE RUNNING WITH PRODUCTION LOSS ( PRODUCTION EFFECTED )" },
            { code: "C", desc: "WORK DONE WHEN MACHINE IDEAL I.E - DURING LUNCH & AFTER SHIFT END TIME." },
          ].map(c => (
            <div key={c.code} className="bds-cat-row">
              <div className="bds-cat-cell-code">{c.code} CATEGORY B/D :-</div>
              <div className="bds-cat-cell-desc">{c.desc}</div>
              <div className="bds-cat-cell-tick">
                <input type="checkbox"
                       disabled={!fieldEditable("category")}
                       checked={data.category === c.code}
                       onChange={e => set("category", e.target.checked ? c.code : "")}/>
              </div>
            </div>
          ))}

          {/* ── Time + date + downtime row ─────────────────────── */}
          <div className="bds-grid bds-grid-3">
            <BdsCell label="B/D START TIME" type="time"
                     value={data.bd_start_time}    readOnly /* always locked — collector */
                     onChange={() => {}}/>
            <BdsCell label="B/D RECEIVED TIME" type="time"
                     value={data.bd_received_time} readOnly={!fieldEditable("bd_received_time")}
                     onChange={v => set("bd_received_time", v)}/>
            <BdsCell label="B/D OK TIME" type="time"
                     value={data.bd_ok_time}       readOnly /* always locked — collector */
                     onChange={() => {}}/>

            <BdsCell label="B/D START DATE" type="date"
                     value={data.bd_start_date}    readOnly /* always locked — collector */
                     onChange={() => {}}/>
            <BdsCell label="B/D END DATE" type="date"
                     value={data.bd_end_date}      readOnly /* always locked — collector */
                     onChange={() => {}}/>
            <BdsCell label="M/C DOWN TIME IN MINUTES" type="number"
                     value={data.mc_down_time_minutes} readOnly /* always locked — computed */
                     onChange={() => {}}/>
          </div>

          {/* ── Reported by Production ─────────────────────────── */}
          <BdsRow label="PROBLEM REPORTED BY PRODUCTION"
                  value={data.problem_reported_by_production}
                  readOnly={!fieldEditable("problem_reported_by_production")}
                  onChange={v => set("problem_reported_by_production", v)}/>

          {/* Production users only see + fill the upper half — the entire
              Maintenance / Tool Room block (divider, related-to, problem
              observed, action, spares, attended-by, signatures) is hidden
              for them.  Maintenance phase + read-only "view" mode show
              the lower half with the rules already configured above. */}
          {/* Lower half visibility — gated on the *phase* (not isProduction)
              so that view-mode callers can ALSO suppress it.  Production
              user opening a slip from Historical → Breakdown Slips passes
              phase="production" and sees only what they filled (upper
              half); maintenance + admin pass phase="maintenance" and see
              the full slip including this lower half. */}
          {phase !== "production" && <>
          {/* ── Maintenance / Tool Room block divider ──────────── */}
          <div className="bds-divider">TO BE FILLED BY MAINTENANCE/TOOL ROOM:-</div>

{/* ── Problem related to (radio-style: only one of Maintenance /
                 Tool Room can be selected at a time) ──────────────── */}
          <div className="bds-relto-row">
            <div className="bds-relto-label">PROBLEM RELATED TO ( PLEASE TICK ☑ )</div>
            <label className="bds-relto-opt">
  <input type="radio" name="problem_related_to"
                     disabled={!fieldEditable("problem_related_to")}
                     checked={!!data.problem_related_to?.maintenance}
                      onChange={() => set("problem_related_to",
                                         { maintenance: true, tool_room: false })}/>
              MAINTENANCE
            </label>
    <label className="bds-relto-opt">
              <input type="radio" name="problem_related_to"
                     disabled={!fieldEditable("problem_related_to")}
                     checked={!!data.problem_related_to?.tool_room}
 onChange={() => set("problem_related_to",
                                         { maintenance: false, tool_room: true })}/>
              TOOL ROOM
            </label>
          </div>

          {/* ── Type of problem (multi-select: electrical / mechanical
                 can BOTH be ticked at the same time, unlike the radio
                 above).  Added 2026-05-20 on operator request — slip
                 needed an explicit electrical-vs-mechanical bucket so
                 the CAPA report can group by failure category. ─── */}
          <div className="bds-relto-row">
            <div className="bds-relto-label">TYPE OF PROBLEM ( TICK ALL THAT APPLY )</div>
            <label className="bds-relto-opt">
              <input type="checkbox"
                     disabled={!fieldEditable("type_of_problem")}
                     checked={!!data.type_of_problem?.electrical}
                     onChange={e => set("type_of_problem", {
                       ...(data.type_of_problem || {}),
                       electrical: e.target.checked,
                     })}/>
              ELECTRICAL
            </label>
            <label className="bds-relto-opt">
              <input type="checkbox"
                     disabled={!fieldEditable("type_of_problem")}
                     checked={!!data.type_of_problem?.mechanical}
                     onChange={e => set("type_of_problem", {
                       ...(data.type_of_problem || {}),
                       mechanical: e.target.checked,
                     })}/>
              MECHANICAL
            </label>
          </div>

          {/* ── Investigation + action ─────────────────────────── */}
          <BdsRow label="ACTUAL PROBLEM OBSERVED BY MAINTENANCE / TOOL ROOM"
                  value={data.actual_problem_observed}
                  readOnly={!fieldEditable("actual_problem_observed")}
                  onChange={v => set("actual_problem_observed", v)}/>
          <BdsRow label="ACTION TAKEN ON PROBLEM"
                  value={data.action_taken_on_problem}
                  readOnly={!fieldEditable("action_taken_on_problem")}
                  onChange={v => set("action_taken_on_problem", v)}/>
          <BdsRow label="SPARES USED ( IF ANY )"
                  value={data.spares_used}
                  readOnly={!fieldEditable("spares_used")}
                  onChange={v => set("spares_used", v)}/>
          <BdsRow label="B/D ATTENDED BY"
                  value={data.bd_attended_by}
                  readOnly={!fieldEditable("bd_attended_by")}
                  onChange={v => set("bd_attended_by", v)}/>

          {/* ── Signatures (4 columns) ─────────────────────────── */}
          <div className="bds-sign-head">
            <div>PREPARED BY :-</div>
            <div>RECEIVED BY :-</div>
            <div>HANDOVER TO :-<br/><span className="bds-sign-sub">LINE LEADER / OPERATOR</span></div>
            <div>HANDOVER TO :-<br/><span className="bds-sign-sub">QUALITY ENGINEER</span></div>
          </div>
          <div className="bds-sign-grid">
            {[
              { key: "prepared_by",          obj: data.prepared_by },
              { key: "received_by",          obj: data.received_by },
              { key: "line_leader_operator", obj: data.line_leader_operator },
              { key: "quality_engineer",     obj: data.quality_engineer },
            ].map(({ key, obj }) => (
              <div key={key} className="bds-sign-cell">
                {/* Only NAME — there's no clean way to capture a real
                    handwritten signature in a web form, so we drop the
                    SIGN row entirely.  Older slips that already saved
                    a `sign` field are unaffected (data preserved in
                    the JSONB blob, just no longer displayed). */}
                <div className="bds-sign-line">
                  <span>NAME :-</span>
                  <input type="text" disabled={!fieldEditable(key)}
                         value={obj?.name || ""}
                         onChange={e => setSub(key, "name", e.target.value)}/>
                </div>
              </div>
            ))}
          </div>
          </>}{/* /lower-half (hidden whenever phase==="production") */}

        </div>

        {/* Footer — Cancel + Submit */}
        <div className="bds-footer">
          <div className="bds-footer-meta">
            {ticket.line_name || `Line ${ticket.line_id}`}
            {ticket.zone_name && <> · {ticket.zone_name}</>}
            <> · {fmtDateTime(ticket.started_at)} → {fmtDateTime(ticket.ended_at)} · {fmtDuration(ticket.duration_seconds)}</>
          </div>
          <div style={{ display:"flex", gap: 10 }}>
            <Btn variant="ghost" onClick={onClose}>{readOnly ? "Close" : "Cancel"}</Btn>
            {/* Maintenance can request a Deviation when the fix needs more
                than 24h.  Available in maintenance fill / view modes; the
                Quality user takes it from there. */}
            {(isMaintenance || (readOnly && phase === "maintenance")) && ticket?.id && (
              <Btn variant="ghost" onClick={() => {
                // Lazy-load Deviation form so the closure modal stays small.
                import("./DeviationForm").then(m => {
                  const DevForm = m.default;
                  // Mount as transient overlay
                  const root = document.createElement("div");
                  document.body.appendChild(root);
                  import("react-dom/client").then(({ createRoot }) => {
                    const r = createRoot(root);
                    const seed = {
                      breakdown_id: ticket.id,
                      line_id:      ticket.line_id,
                      line_name:    ticket.line_name,
                      zone_id:      ticket.zone_id,
                      zone_name:    ticket.zone_name,
                      machine_no:   ticket.production_data?.machine_no || "",
                      machine_name: ticket.production_data?.machine_name || "",
                      reason:       data.actual_problem_observed || "",
                      observation:  data.action_taken_on_problem || "",
                    };
                    const close = () => { r.unmount(); root.remove(); };
                    r.render(
                      <DevForm
                        deviation={seed}
                        token={token}
                        mode="raise"
                        onClose={close}
                        onSaved={close}
                      />
                    );
                  });
                });
              }} title="Request a deviation when the fix needs >24h">
                ⚠ Request Deviation
              </Btn>
            )}
            {!readOnly && (
              <Btn variant="primary" onClick={submit}
                   disabled={saving || !phaseComplete()}
                   title={phaseComplete()
                     ? ""
                     : "Fill every field in your half before submitting"}>
                {saving
                   ? "Submitting…"
                   : isProduction  ? "Submit Production Half"
                   : isMaintenance ? "Submit Maintenance Half"
                                   : "Submit"}
              </Btn>
            )}
          </div>
        </div>
      </div>

      {/* ── Toyota Boshoku BREAK DOWN SLIP styles ────────────────── */}
      <style>{`
        .bds-letterhead {
          display:flex; align-items:stretch;
          background:#fff; border-bottom:2px solid #0f172a;
          position:relative;
        }
        .bds-logo {
          width:120px; padding:8px 10px; border-right:1.5px solid #0f172a;
          display:flex; flex-direction:column; align-items:center; gap:2px;
          background:#fff;
        }
        .bds-logo-tb {
          font-family:'Barlow Condensed',sans-serif;
          font-size:34px; font-weight:900; color:#dc2626;
          line-height:1;
        }
        .bds-logo-sub {
          font-size:8px; font-weight:700; color:#0f172a;
          letter-spacing:.05em; text-align:center; line-height:1.2;
        }
        .bds-letter-title { flex:1; padding:6px 12px; text-align:center;
                            display:flex; flex-direction:column; justify-content:center; }
        .bds-company { font-size:18px; font-weight:800; color:#0f172a;
                       letter-spacing:.04em; }
        .bds-doc-title { font-size:14px; font-weight:700; color:#0f172a;
                         letter-spacing:.06em; margin-top:2px; }
        .bds-close-x {
          width:46px; cursor:pointer; display:flex; align-items:center;
          justify-content:center; font-size:30px; color:#64748b;
          border-left:1.5px solid #0f172a;
          font-family:Arial; line-height:1;
        }
        .bds-close-x:hover { background:#fee2e2; color:#dc2626; }

        .bds-print-btn {
          padding:0 16px; cursor:pointer; display:flex; align-items:center;
          gap:8px; font-size:12px; font-weight:700; color:#1e40af;
          border-left:1.5px solid #0f172a; background:#f8fafc;
          letter-spacing:.04em; user-select:none;
          font-family:'Barlow',sans-serif;
        }
        .bds-print-btn:hover { background:rgba(30,64,175,.08); color:#1e3a8a; }

        /* Print is handled via a sandboxed iframe by printSlip() in
           ClosureFormModal.  The host page intentionally has no @media
           print rules, so a stray Ctrl+P from the user prints the
           visible page (not the slip), avoiding two-page bugs caused
           by the surrounding app's layout boxes. */

        .bds-body {
          padding:0; max-height:74vh; overflow-y:auto;
          background:#fff;
          font-family:'Barlow',sans-serif; color:#0f172a; font-size:11px;
        }

        /* Header / time grids — 3 columns of label+input pairs */
        .bds-grid {
          display:grid; border-top:1.5px solid #0f172a;
          border-left:1.5px solid #0f172a;
        }
        .bds-grid-3 { grid-template-columns: 1fr 1fr 1fr; }

        .bds-cell {
          display:flex; align-items:stretch;
          border-right:1.5px solid #0f172a; border-bottom:1.5px solid #0f172a;
          min-height:36px;
        }
        .bds-cell-label {
          background:#f1f5f9; padding:6px 8px;
          font-size:10px; font-weight:800; color:#0f172a;
          letter-spacing:.02em; min-width:140px;
          display:flex; align-items:center;
          border-right:1px solid #cbd5e1;
        }
        .bds-cell-input { flex:1; padding:0; }
        .bds-cell-input input {
          width:100%; height:100%; min-height:34px;
          border:none; outline:none; background:transparent;
          padding:6px 10px; font-size:12px; font-weight:600;
          color:#0f172a; font-family:inherit; box-sizing:border-box;
        }
        .bds-cell-input input:disabled { color:#0f172a; opacity:1; }

        /* Category section */
        .bds-cat-head {
          display:grid; grid-template-columns: 1fr 110px;
          background:#f1f5f9;
          border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a;
          padding:6px 10px; font-weight:800; font-size:11px; color:#0f172a;
          align-items:center;
        }
        .bds-cat-tickdown { text-align:center; font-size:9px;
                             border-left:1px solid #cbd5e1; padding-left:8px; }
        .bds-cat-row {
          display:grid; grid-template-columns: 160px 1fr 110px;
          border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a;
          border-top:1.5px solid #0f172a;
          min-height:30px;
        }
        .bds-cat-cell-code {
          padding:6px 10px; font-weight:800; font-size:11px;
          background:#f8fafc; border-right:1px solid #cbd5e1;
          display:flex; align-items:center;
        }
        .bds-cat-cell-desc {
          padding:6px 10px; font-size:11px; color:#0f172a;
          border-right:1px solid #cbd5e1; display:flex; align-items:center;
        }
        .bds-cat-cell-tick {
          display:flex; align-items:center; justify-content:center;
        }
        .bds-cat-cell-tick input { width:18px; height:18px; cursor:pointer; }

        /* Full-width row (textarea) */
        .bds-row {
          display:grid; grid-template-columns: 220px 1fr;
          border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a;
          border-top:1.5px solid #0f172a;
          min-height:60px;
        }
        .bds-row-label {
          padding:6px 10px; background:#f1f5f9; font-weight:800;
          font-size:10px; color:#0f172a; letter-spacing:.02em;
          border-right:1px solid #cbd5e1;
          display:flex; align-items:center;
        }
        .bds-row-input { padding:0; }
        .bds-row-input textarea {
          width:100%; height:100%; min-height:60px;
          border:none; outline:none; background:transparent;
          padding:6px 10px; font-size:12px; font-weight:500;
          color:#0f172a; font-family:inherit; resize:vertical;
          box-sizing:border-box;
        }

        /* Maintenance/Tool Room divider */
        .bds-divider {
          padding:6px 10px; background:#fee2e2; color:#991b1b;
          font-weight:800; font-size:11px; letter-spacing:.04em;
          border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a;
          border-top:1.5px solid #0f172a;
        }
        .bds-relto-row {
          display:flex; align-items:center; gap:24px;
          padding:8px 10px;
          border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a;
          border-top:1.5px solid #0f172a;
          font-size:11px; font-weight:700;
        }
        .bds-relto-label { color:#0f172a; }
        .bds-relto-opt {
          display:flex; align-items:center; gap:6px;
          cursor:pointer; user-select:none;
        }
        .bds-relto-opt input { width:16px; height:16px; cursor:pointer; }

        /* Signatures */
        .bds-sign-head {
          display:grid; grid-template-columns: 1fr 1fr 1fr 1fr;
          background:#f1f5f9; padding:6px 10px;
          border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a;
          border-top:1.5px solid #0f172a;
          font-size:11px; font-weight:800;
        }
        .bds-sign-head > div { padding:0 6px; }
        .bds-sign-head .bds-sign-sub { font-size:9px; color:#475569; font-weight:700; }
        .bds-sign-grid {
          display:grid; grid-template-columns: 1fr 1fr 1fr 1fr;
          border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a;
          border-top:1.5px solid #0f172a; border-bottom:1.5px solid #0f172a;
        }
        .bds-sign-cell {
          padding:6px 8px;
          border-right:1px solid #cbd5e1;
        }
        .bds-sign-cell:last-child { border-right:none; }
        .bds-sign-line {
          display:flex; align-items:center; gap:6px; padding:3px 0;
          font-size:10px; font-weight:700; color:#0f172a;
        }
        .bds-sign-line span { min-width:42px; }
        .bds-sign-line input {
          flex:1; border:none; border-bottom:1px solid #94a3b8;
          padding:2px 4px; font-size:11px; font-weight:600; outline:none;
          background:transparent; font-family:inherit; color:#0f172a;
        }
        .bds-sign-line input:disabled { opacity:1; color:#0f172a; }

        .bds-format {
          padding:8px 10px; text-align:center; font-size:10px;
          color:#475569; font-weight:600;
          background:#fff;
        }

        .bds-footer {
          display:flex; align-items:center; justify-content:space-between;
          gap:14px; padding:12px 18px;
          background:#f8fafc; border-top:1px solid #e2e8f0;
          flex-wrap:wrap;
        }
        .bds-footer-meta {
          font-size:11px; color:#64748b; font-weight:600;
        }
      `}</style>
    </div>
  );
}

/* ── Single label+input cell (for the 3×3 header & time grids) ──── */
function BdsCell({ label, value, type = "text", readOnly, onChange }) {
  return (
    <div className="bds-cell">
      <div className="bds-cell-label">{label} :-</div>
      <div className="bds-cell-input">
        <input type={type}
               value={value || ""}
               disabled={readOnly}
               onChange={(e) => onChange?.(e.target.value)}/>
      </div>
    </div>
  );
}

/* ── Full-width labelled textarea (for free-text rows) ──────────── */
function BdsRow({ label, value, readOnly, onChange }) {
  return (
    <div className="bds-row">
      <div className="bds-row-label">{label}</div>
      <div className="bds-row-input">
        <textarea value={value || ""}
                  disabled={readOnly}
                  rows={2}
                  onChange={(e) => onChange?.(e.target.value)}/>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════
 * Toast
 * ════════════════════════════════════════════════════════════════════ */
function useToast() {
  const [t, setT] = useState(null);
  const show = (msg, kind = "ok") => {
    setT({ msg, kind });
    setTimeout(() => setT(null), 2800);
  };
  const node = t ? (
    <div style={{ position: "fixed", bottom: 22, right: 22, zIndex: 9999,
                    padding: "10px 16px", borderRadius: 8,
                    background: t.kind === "err"
                      ? "linear-gradient(135deg,#dc2626,#b91c1c)"
                      : "linear-gradient(135deg,#16a34a,#15803d)",
                    color: "#fff", fontSize: 12, fontWeight: 700,
                    boxShadow: "0 8px 24px rgba(0,0,0,.18)" }}>
      {t.msg}
    </div>
  ) : null;
  return [show, node];
}

/* ════════════════════════════════════════════════════════════════════
 * Page shell
 * ════════════════════════════════════════════════════════════════════ */
export default function MaintenanceDashboard() {
  const { token, user, theme, isAdmin } = useAuth();
  // Admin sees the explicit name ("Maintenance Dashboard") so they can
  // tell at a glance which dept's view they're on.  Department users only
  // see "Dashboard" — they only ever land on their own.
  const titleLeft  = isAdmin ? "Maintenance " : "";
  const titleRight = "Dashboard";
  const [active, setActive]       = useState([]);
  const [recent, setRecent]       = useState([]);
  const [stats,  setStats]        = useState({ zones: [], lines: [] });
  const [lines,  setLines]        = useState([]);  // kept so historical line lookups stay possible
  const [loading, setLoading]     = useState(true);
  // 2026-05-20 — slip-raise threshold (minutes).  Used to HIDE minor
  // breakdowns from the Recent Breakdowns table.  Operator decision:
  // anything resolved under this threshold doesn't need formal slip
  // and shouldn't clutter the maintenance dashboard either.  Default
  // 10 min matches the global threshold default.
  const [slipThresholdMin, setSlipThresholdMin] = useState(10);

  const [closureModal, setClosureModal]     = useState(null); // { ticket, mode }

  const [showToast, toastNode]    = useToast();

  // Fullscreen control for the ANDON section
  const andonRef = useRef(null);
  const [isFs, setIsFs] = useState(false);
  useEffect(() => {
    const onChange = () => setIsFs(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);
  const toggleFullscreen = () => {
    const el = andonRef.current;
    if (!el) return;
    if (document.fullscreenElement) document.exitFullscreen();
    else el.requestFullscreen?.();
  };

  // Refresh ANDON + history + stats every 10s while the page is mounted.
  //
  // 2026-05-20 — Two-fetch merge for the Recent Breakdowns table:
  //   • /recent?days=2          → last 2 days of CLOSED context
  //   • /history?days=180&state=RESOLVED → ANY pending-closure ticket
  //                                         up to 6 months old that
  //                                         hasn't been formally closed
  //                                         yet (operator: "show until
  //                                         closure form is filled").
  // The two lists are deduped by id and merged so the same ticket
  // never appears twice.  The minor-event filter in HistoryTable then
  // hides anything below the slip-raise threshold.
  const reload = useCallback(async () => {
    try {
      const [a, r, pending, s, l, cfg] = await Promise.all([
        api.get("/api/breakdowns/active", token).catch(() => []),
        api.get("/api/breakdowns/recent?days=2", token).catch(() => []),
        api.get("/api/breakdowns/history?days=180&state=RESOLVED&limit=500", token).catch(() => ({ rows: [] })),
        api.get("/api/breakdowns/stats?days=30", token).catch(() => ({ zones: [], lines: [] })),
        api.get("/api/lines/", token).catch(() => []),
        api.get("/api/breakdowns/slip-config", token).catch(() => null),
      ]);
      setActive(Array.isArray(a) ? a : []);

      // Merge recent (last 2 days, both RESOLVED + CLOSED) with all
      // long-pending RESOLVED tickets (those still awaiting closure
      // form).  Dedupe by id — pending may already be inside recent if
      // it's young enough.  Sort by started_at descending.
      const recentArr  = Array.isArray(r) ? r : [];
      const pendArr    = (pending && Array.isArray(pending.rows)) ? pending.rows : [];
      const byId       = new Map();
      [...pendArr, ...recentArr].forEach(row => { if (row && row.id != null) byId.set(row.id, row); });
      const merged = Array.from(byId.values()).sort((x, y) => {
        const xs = x.started_at || ""; const ys = y.started_at || "";
        return ys.localeCompare(xs);
      });
      setRecent(merged);

      setStats(s || { zones: [], lines: [] });
      setLines(Array.isArray(l) ? l : []);
      if (cfg && Number.isFinite(cfg.slip_raise_threshold_min)) {
        setSlipThresholdMin(cfg.slip_raise_threshold_min);
      }
    } catch {
      showToast("Failed to load dashboard", "err");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    reload();
    const t = setInterval(reload, 10000);
    return () => clearInterval(t);
  }, [reload]);

  useEffect(() => {
    document.title = "Maintenance Dashboard";
  }, []);

  // Breakdowns are opened + resolved automatically by the collector based
  // on the line's status bit — no manual buttons on this dashboard.  The
  // only manual step left is filling the closure form for a RESOLVED
  // ticket from the History table below.

  // From the History table: "Fill Closure Form" → opens the modal in
  // maintenance phase (Maintenance fills lower half).  "View" → read-only.
  const onCloseTicket = (ticket) => setClosureModal({ ticket, mode: "fill", phase: "maintenance" });
  const onViewTicket  = (ticket) => setClosureModal({ ticket, mode: "view", phase: "maintenance" });

  // ClosureFormModal calls back with (slice, phase) — slice is just the
  // half the user filled.  Production phase POSTs to /production-fill;
  // maintenance phase POSTs to /close (which also flips state to CLOSED).
  const onSubmitClosure = async (slice, phase) => {
    try {
      const id = closureModal.ticket.id;
      if (phase === "production") {
        await api.post(`/api/breakdowns/${id}/production-fill`,
                       { production_data: slice }, token);
        showToast("Production half saved ✓");
      } else {
        await api.post(`/api/breakdowns/${id}/close`,
                       { maintenance_data: slice }, token);
        showToast("Maintenance half saved ✓");
      }
      setClosureModal(null);
      reload();
    } catch (e) {
      showToast(e.message || "Submit failed", "err");
      throw e;
    }
  };

  // KPI tiles at top
  const todayCount = recent.filter((r) => {
    const d = new Date(r.started_at);
    const t = new Date();
    return d.getFullYear() === t.getFullYear() && d.getMonth() === t.getMonth() && d.getDate() === t.getDate();
  }).length;
  const pendingClosure = recent.filter((r) => r.state === "RESOLVED").length;
  const longestActive = active.reduce((max, r) => {
    if (!r.started_at) return max;
    const sec = Math.floor((Date.now() - new Date(r.started_at).getTime()) / 1000);
    return sec > max ? sec : max;
  }, 0);

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@600;700;800&display=swap');
        .md-root { min-height:100vh; background:#f8fafc; font-family:'Barlow',sans-serif; padding-bottom:60px; }
        .md-topbar {
          background:#fff; border-bottom:1px solid #e2e8f0;
          padding:0 40px 0 88px; height:60px;
          display:flex; align-items:center; justify-content:space-between;
          position:sticky; top:0; z-index:100;
          box-shadow:0 1px 3px rgba(0,0,0,.06);
        }
        .md-topbar::after { content:''; position:absolute; bottom:0; left:0; right:0; height:2px;
                            background:${theme.gradient}; }
        .md-logo { font-family:'Barlow Condensed',sans-serif; font-size:22px; font-weight:800; color:#0f172a; }
        .md-logo span { color:${theme.accent}; }
        .md-title { position:absolute; left:50%; transform:translateX(-50%);
                    font-family:'Barlow Condensed',sans-serif;
                    font-size:37px; font-weight:800; color:#0f172a; letter-spacing:-.01em;
                    pointer-events:none; white-space:nowrap; }
        .md-title span { color:${theme.accent}; }
        .md-user-pill { display:flex; align-items:center; gap:10px;
                         padding:6px 14px; border-radius:99px;
                         border:1.5px solid #e2e8f0; background:#f8fafc;
                         font-size:12px; font-weight:600; color:#334155;
                         white-space:nowrap; }
        .md-user-pill b { color:#0f172a; font-weight:800; }
        .md-body { padding:28px 40px 0; max-width:1280px; margin:0 auto; }
        .md-tiles { display:flex; gap:14px; flex-wrap:wrap; margin-bottom:18px; }
        .md-section { margin-bottom:22px; }
        .md-section h3 { margin:0 0 10px; font-family:'Barlow Condensed',sans-serif;
                          font-size:18px; font-weight:800; color:#0f172a;
                          letter-spacing:.02em; text-transform:uppercase; }
      `}</style>

      <div className="md-root">
        {/* Production-Dashboard-style topbar (red accent for Maintenance) */}
        <div className="md-topbar">
          <div className="md-logo" />
          <div className="md-title">
            {titleLeft}<span>{titleRight}</span>
          </div>
          {user?.username && (
            <div className="md-user-pill">
              Signed in as <b>{user.username}</b>
            </div>
          )}
        </div>

        <div className="md-body">
          {loading ? (
            <div style={{ padding: "60px 20px", textAlign: "center", color: "#94a3b8" }}>
              Loading maintenance dashboard…
            </div>
          ) : (
            <>
              <div className="md-tiles">
                <StatCard label="Active breakdowns"  value={active.length}              color={active.length ? "#dc2626" : "#16a34a"}/>
                <StatCard label="Today (24h)"        value={todayCount}                 color="#1e40af"/>
                <StatCard label="Pending closure"    value={pendingClosure}             color="#b45309" sub="Resolved but form pending"/>
                <StatCard label="Longest active"     value={fmtDuration(longestActive)} color="#7c3aed"/>
              </div>

              <div className="md-section">
                <AndonTable
                  rows={active}
                  fullscreenRef={andonRef}
                  isFullscreen={isFs}
                  toggleFullscreen={toggleFullscreen}
                />
              </div>

              <div className="md-section">
                <HistoryTable
                  rows={recent}
                  onCloseTicket={onCloseTicket}
                  onViewTicket={onViewTicket}
                  slipThresholdMin={slipThresholdMin}
                />
              </div>

              <div className="md-section">
                <KpiPanel token={token} lines={lines} />
              </div>

              <div className="md-section">
                <h3>Zone &amp; Line stats <span style={{ fontSize:11,fontWeight:600,color:"#94a3b8",letterSpacing:0,textTransform:"none" }}>· last 30 days</span></h3>
                <StatsSection stats={stats} />
              </div>
            </>
          )}
        </div>
      </div>

      {closureModal && (
        <ClosureFormModal
          ticket={closureModal.ticket}
          mode={closureModal.mode}
          phase={closureModal.phase || "maintenance"}
          token={token}
          onClose={() => setClosureModal(null)}
          onSave={onSubmitClosure}
        />
      )}

      {toastNode}
    </>
  );
}
