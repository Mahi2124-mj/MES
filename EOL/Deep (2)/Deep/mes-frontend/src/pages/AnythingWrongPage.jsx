/* ════════════════════════════════════════════════════════════════════
 *  AnythingWrongPage.jsx
 *  ────────────────────────────────────────────────────────────────────
 *  TPS "Genchi Genbutsu" board — every open shop-floor problem on ONE
 *  screen.  No drilling between Maintenance / Quality / Manpower /
 *  Store / Dispatch.  Sources:
 *     • Open breakdowns
 *     • Manpower alerts (unallocated / skill mismatch / escalation)
 *     • Skill mismatches on active shift
 *     • Low / out-of-stock materials
 *     • Ready FG lots waiting > 2 hrs
 *     • Active Poka-Yoke bypasses
 *
 *  Polls /api/anything-wrong/summary + /items every 20 s.
 * ════════════════════════════════════════════════════════════════════ */

import { useState, useEffect, useMemo, useCallback } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import AIAssistant from "../components/AIAssistant";
import PageTopbar from "../components/PageTopbar";

const SEVERITY_COLOR = {
  high:   "#dc2626",
  medium: "#d97706",
  low:    "#2563eb",
};
const SECTION_META = {
  breakdowns:        { label: "Breakdowns",         icon: "🚨", color: "#dc2626" },
  manpower_alerts:   { label: "Manpower Alerts",    icon: "👥", color: "#d97706" },
  skill_mismatch:    { label: "Skill Mismatches",   icon: "⚠",  color: "#db2777" },
  low_stock:         { label: "Low / Out of Stock", icon: "📦", color: "#7c3aed" },
  ready_lots_stale:  { label: "Lots Waiting Dispatch", icon: "🚚", color: "#ea580c" },
  py_bypass:         { label: "Poka-Yoke Bypass",   icon: "🛡", color: "#b91c1c" },
};

export default function AnythingWrongPage() {
  const { token } = useAuth();
  const [lines, setLines] = useState([]);
  const [lineId, setLineId] = useState("");
  const [summary, setSummary] = useState(null);
  const [items, setItems] = useState({});
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.get("/api/lines/", token).then(setLines).catch(() => {});
  }, [token]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const qs = lineId ? `?line_id=${lineId}` : "";
      const [s, i] = await Promise.all([
        api.get(`/api/anything-wrong/summary${qs}`, token),
        api.get(`/api/anything-wrong/items${qs}&limit=100`.replace("?&", "?"), token),
      ]);
      setSummary(s);
      setItems(i || {});
    } catch (e) { console.warn("anything-wrong load failed", e); }
    finally { setLoading(false); }
  }, [token, lineId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { const t = setInterval(load, 20000); return () => clearInterval(t); }, [load]);

  const totalIssues = summary?.total || 0;
  const allItems = useMemo(() => {
    const arr = [];
    Object.entries(items || {}).forEach(([section, list]) => {
      (list || []).forEach(it => arr.push({ ...it, section }));
    });
    // Sort by severity (high > med > low), then by fired_at desc
    const sevRank = { high: 0, medium: 1, low: 2 };
    arr.sort((a, b) => {
      const s = (sevRank[a.severity] ?? 3) - (sevRank[b.severity] ?? 3);
      if (s !== 0) return s;
      return new Date(b.fired_at) - new Date(a.fired_at);
    });
    return arr;
  }, [items]);

  return (
    <div style={{
      minHeight: "100vh",
      background: "#f8fafc",
      fontFamily: "'Barlow',sans-serif", paddingBottom: 60, color: "#0f172a",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@700;800&display=swap');
        @keyframes pulse-glow {
          0%, 100% { box-shadow: 0 0 0 0 rgba(220,38,38,.5); }
          50%      { box-shadow: 0 0 0 12px rgba(220,38,38,0); }
        }
        .pulse-high { animation: pulse-glow 2s ease-in-out infinite; }
        .col-scroll::-webkit-scrollbar { width: 6px; }
        .col-scroll::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
      `}</style>

      {/* 2026-05-13 — standardised topbar (matches Production Import/Export) */}
      <PageTopbar leading="Anything" accent="Wrong?" />

      {/* Hero (title moved into PageTopbar) */}
      <div style={{ padding: "14px 48px 20px", background: "#fff", borderBottom: "1px solid #e2e8f0" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 24, flexWrap: "wrap" }}>
          <div style={{ flex: 1, minWidth: 360 }}>
            <div style={{ fontSize: 13, color: "#64748b" }}>
              {totalIssues > 0 ? `🔴 ${totalIssues} open issue${totalIssues!==1?'s':''} across the floor` : "✅ All clear"}
            </div>
            <div style={{ display: "flex", gap: 10, marginTop: 16, alignItems: "end", flexWrap: "wrap" }}>
              <div>
                <label style={lblStyle}>Filter Line</label>
                <select value={lineId} onChange={e => setLineId(e.target.value ? Number(e.target.value) : "")}
                        style={{ ...glassSelect, minWidth: 180, marginTop: 6 }}>
                  <option value="">All Lines</option>
                  {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
                </select>
              </div>
              <button onClick={load} style={glassBtn}>↻ {loading ? "…" : "Refresh"}</button>
            </div>
          </div>

          {/* Master count */}
          <div style={{
            padding: "20px 28px",
            background: totalIssues > 0
              ? "linear-gradient(135deg, #fee2e2, #fecaca)"
              : "linear-gradient(135deg, #dcfce7, #bbf7d0)",
            border: `2px solid ${totalIssues > 0 ? "#dc2626" : "#16a34a"}`,
            borderRadius: 18, textAlign: "center",
            boxShadow: totalIssues > 0
              ? "0 10px 30px rgba(220,38,38,.18)"
              : "0 10px 30px rgba(22,163,74,.18)",
            minWidth: 200,
          }} className={totalIssues > 0 ? "pulse-high" : ""}>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: ".15em", textTransform: "uppercase", color: totalIssues > 0 ? "#991b1b" : "#166534" }}>
              Open issues
            </div>
            <div style={{
              fontSize: 60, fontWeight: 900, lineHeight: 1,
              fontFamily: "'Barlow Condensed',sans-serif", marginTop: 4,
              color: totalIssues > 0 ? "#dc2626" : "#16a34a",
            }}>{totalIssues}</div>
            <div style={{ fontSize: 11, color: totalIssues > 0 ? "#991b1b" : "#166534", marginTop: 4 }}>
              {totalIssues === 0 ? "ALL CLEAR · KEEP IT UP" : "ACTION REQUIRED"}
            </div>
          </div>
        </div>

        {/* Section tiles */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 14, marginTop: 22 }}>
          {Object.entries(SECTION_META).map(([key, meta]) => {
            const n = summary?.[key === "py_bypass" ? "py_bypass" : key] ?? 0;
            return (
              <div key={key} style={{
                background: "#fff",
                border: "1px solid #e2e8f0",
                borderTop: `3px solid ${n > 0 ? meta.color : "#e2e8f0"}`,
                borderRadius: 14, padding: 16,
                boxShadow: "0 1px 3px rgba(0,0,0,.04)",
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                  <span style={{ fontSize: 11, fontWeight: 700, color: "#64748b", letterSpacing: ".08em", textTransform: "uppercase" }}>
                    {meta.label}
                  </span>
                  <span style={{ fontSize: 18 }}>{meta.icon}</span>
                </div>
                <div style={{
                  fontSize: 32, fontWeight: 800, color: n > 0 ? meta.color : "#94a3b8",
                  fontFamily: "'Barlow Condensed',sans-serif", lineHeight: 1,
                }}>{n}</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Items list */}
      <div style={{ padding: "20px 48px" }}>
        {allItems.length === 0 ? (
          <div style={{ ...cardStyle, padding: 60, textAlign: "center" }}>
            <div style={{ fontSize: 48, marginBottom: 12 }}>✓</div>
            <div style={{ fontSize: 22, fontWeight: 800, color: "#16a34a", fontFamily: "'Barlow Condensed',sans-serif" }}>
              ALL CLEAR
            </div>
            <div style={{ fontSize: 13, color: "#64748b", marginTop: 6 }}>
              No open issues across breakdown / manpower / quality / store / dispatch
            </div>
          </div>
        ) : (
          <div style={{ ...cardStyle, padding: 0 }}>
            <div className="col-scroll" style={{ maxHeight: "calc(100vh - 460px)", overflowY: "auto" }}>
              {allItems.map((it, idx) => {
                const meta = SECTION_META[it.section] || { color: "#94a3b8", icon: "•", label: it.section };
                const sevColor = SEVERITY_COLOR[it.severity] || "#94a3b8";
                return (
                  <div key={`${it.section}-${it.id}-${idx}`} style={{
                    display: "flex", alignItems: "center", gap: 14,
                    padding: "14px 20px",
                    borderBottom: idx === allItems.length - 1 ? "none" : "1px solid #f1f5f9",
                    background: idx % 2 === 0 ? "#f8fafc" : "#fff",
                  }}>
                    {/* Severity rail */}
                    <div style={{ width: 4, alignSelf: "stretch", background: sevColor, borderRadius: 2 }} />

                    {/* Source icon */}
                    <div style={{
                      width: 42, height: 42, borderRadius: 10,
                      background: `${meta.color}1a`, color: meta.color,
                      display: "flex", alignItems: "center", justifyContent: "center",
                      fontSize: 18, flexShrink: 0,
                    }}>{meta.icon}</div>

                    {/* Title + detail */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 14, fontWeight: 700, color: "#0f172a" }}>
                        {it.title}
                      </div>
                      <div style={{ fontSize: 11, color: "#64748b", marginTop: 2 }}>
                        {it.detail}
                      </div>
                    </div>

                    {/* Source pill + age */}
                    <div style={{ textAlign: "right", flexShrink: 0 }}>
                      <span style={{
                        display: "inline-block",
                        padding: "3px 10px", borderRadius: 99,
                        background: `${meta.color}1a`, color: meta.color,
                        fontSize: 10, fontWeight: 700, letterSpacing: ".05em",
                      }}>{meta.label}</span>
                      <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 4, fontFamily: "monospace" }}>
                        {it.fired_at ? new Date(it.fired_at).toLocaleString() : "—"}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      <AIAssistant />
    </div>
  );
}

const cardStyle = {
  background: "#fff",
  border: "1px solid #e2e8f0", borderRadius: 14,
  boxShadow: "0 1px 3px rgba(0,0,0,.04)",
  overflow: "hidden",
};
const glassSelect = {
  padding: "9px 12px", fontSize: 13,
  background: "#f8fafc",
  border: "1.5px solid #e2e8f0", borderRadius: 8,
  color: "#0f172a", outline: "none",
  fontFamily: "'Barlow',sans-serif",
};
const glassBtn = {
  padding: "9px 16px", fontSize: 12, fontWeight: 700,
  background: "#f8fafc", color: "#334155",
  border: "1px solid #e2e8f0", borderRadius: 8,
  cursor: "pointer", fontFamily: "'Barlow',sans-serif",
};
const lblStyle = {
  fontSize: 10, fontWeight: 700, letterSpacing: ".08em",
  textTransform: "uppercase", color: "#64748b",
};
