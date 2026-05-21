/**
 * AIAnalysisPanel.jsx
 * ───────────────────
 * Displays the n8n AI Incident Intelligence result for a single breakdown.
 *
 * Usage in MaintenanceHistorical / MaintenanceDashboard:
 *
 *   import AIAnalysisPanel from "../components/AIAnalysisPanel";
 *
 *   // inside the breakdown row / modal:
 *   <AIAnalysisPanel breakdownId={row.id} token={token} />
 *
 * The panel lazy-fetches GET /api/n8n/analysis/{breakdownId} the first
 * time it mounts (or the user clicks "Load AI Analysis").  Shows a
 * spinner while waiting, a "not ready yet" hint if 404, and the full
 * root-cause + remediation card once available.
 */

import { useState } from "react";

const SEVERITY_COLOR = {
  critical: { bg: "rgba(220,38,38,.10)",  border: "#dc2626", text: "#dc2626" },
  high:     { bg: "rgba(234,88,12,.10)",  border: "#ea580c", text: "#ea580c" },
  medium:   { bg: "rgba(202,138,4,.10)",  border: "#ca8a04", text: "#ca8a04" },
  low:      { bg: "rgba(22,163,74,.10)",  border: "#16a34a", text: "#16a34a" },
};

function SeverityChip({ severity }) {
  const s = severity?.toLowerCase() || "low";
  const c = SEVERITY_COLOR[s] || SEVERITY_COLOR.low;
  return (
    <span style={{
      padding: "2px 10px", borderRadius: 99, fontSize: 10, fontWeight: 700,
      background: c.bg, color: c.text, border: `1px solid ${c.border}`,
      whiteSpace: "nowrap", letterSpacing: ".06em", textTransform: "uppercase",
    }}>
      {s}
    </span>
  );
}

function Section({ label, children }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{
        fontSize: 9, fontWeight: 800, letterSpacing: ".12em",
        textTransform: "uppercase", color: "#64748b", marginBottom: 4,
      }}>
        {label}
      </div>
      <div style={{ fontSize: 12, color: "#1e293b", lineHeight: 1.6 }}>
        {children}
      </div>
    </div>
  );
}

export default function AIAnalysisPanel({ breakdownId, token, autoLoad = false }) {
  const [state,    setState]    = useState(autoLoad ? "loading" : "idle");
  const [analysis, setAnalysis] = useState(null);
  const [error,    setError]    = useState(null);

  async function load() {
    if (!breakdownId) return;
    setState("loading");
    setError(null);
    try {
      const r = await fetch(`/api/n8n/analysis/${breakdownId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.status === 404) {
        setError("not_ready");
        setState("error");
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setAnalysis(await r.json());
      setState("done");
    } catch (e) {
      setError(e.message);
      setState("error");
    }
  }

  async function retrigger() {
    setState("loading");
    setError(null);
    try {
      await fetch(`/api/n8n/trigger/${breakdownId}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      // Poll after 60s
      setTimeout(load, 60_000);
      setError("triggered");
      setState("error");   // show "triggered" message
    } catch (e) {
      setError(e.message);
      setState("error");
    }
  }

  // ── Idle state — show a small button ─────────────────────────────────
  if (state === "idle") {
    return (
      <button
        onClick={load}
        style={{
          display: "inline-flex", alignItems: "center", gap: 5,
          padding: "4px 12px", borderRadius: 8, cursor: "pointer",
          background: "rgba(99,102,241,.08)", border: "1px solid rgba(99,102,241,.25)",
          color: "#4f46e5", fontSize: 11, fontWeight: 600,
        }}
      >
        <span style={{ fontSize: 13 }}>&#x2728;</span>
        Load AI Analysis
      </button>
    );
  }

  // ── Loading ───────────────────────────────────────────────────────────
  if (state === "loading") {
    return (
      <div style={{ color: "#64748b", fontSize: 12, padding: "6px 0" }}>
        &#x23F3; Fetching AI analysis…
      </div>
    );
  }

  // ── Error / not-ready states ──────────────────────────────────────────
  if (state === "error") {
    if (error === "not_ready") {
      return (
        <div style={{
          padding: "8px 12px", borderRadius: 8, fontSize: 12,
          background: "rgba(245,158,11,.08)", border: "1px solid rgba(245,158,11,.3)",
          color: "#92400e",
        }}>
          &#x23F0; AI analysis not available yet (n8n is still processing).
          <button
            onClick={retrigger}
            style={{
              marginLeft: 10, padding: "2px 8px", borderRadius: 6,
              cursor: "pointer", background: "rgba(245,158,11,.15)",
              border: "1px solid rgba(245,158,11,.4)", color: "#92400e",
              fontSize: 11, fontWeight: 600,
            }}
          >
            Re-trigger
          </button>
        </div>
      );
    }
    if (error === "triggered") {
      return (
        <div style={{
          padding: "8px 12px", borderRadius: 8, fontSize: 12,
          background: "rgba(22,163,74,.08)", border: "1px solid rgba(22,163,74,.3)",
          color: "#15803d",
        }}>
          &#x2705; AI analysis triggered — results will appear in ~60 seconds.
          <button onClick={load} style={{
            marginLeft: 10, padding: "2px 8px", borderRadius: 6,
            cursor: "pointer", background: "transparent",
            border: "1px solid #16a34a", color: "#15803d",
            fontSize: 11, fontWeight: 600,
          }}>Check now</button>
        </div>
      );
    }
    return (
      <div style={{ color: "#dc2626", fontSize: 12 }}>
        &#x274C; {error}
      </div>
    );
  }

  // ── Success ───────────────────────────────────────────────────────────
  if (!analysis) return null;

  const remText = analysis.remediation_result || analysis.recommended_action || "";
  // Show only first 600 chars of the remediation result (can be very long)
  const remPreview = remText.length > 600
    ? remText.slice(0, 600) + "…"
    : remText;

  return (
    <div style={{
      marginTop: 10, padding: "12px 14px", borderRadius: 10,
      background: "rgba(99,102,241,.04)", border: "1px solid rgba(99,102,241,.18)",
    }}>
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8, marginBottom: 10,
        paddingBottom: 8, borderBottom: "1px solid rgba(99,102,241,.12)",
      }}>
        <span style={{ fontSize: 14 }}>&#x1F916;</span>
        <span style={{ fontSize: 12, fontWeight: 700, color: "#4f46e5" }}>
          AI Incident Intelligence
        </span>
        <SeverityChip severity={analysis.severity} />
        {analysis.auto_remediated && (
          <span style={{
            padding: "2px 8px", borderRadius: 99, fontSize: 10, fontWeight: 700,
            background: "rgba(22,163,74,.10)", color: "#15803d",
            border: "1px solid rgba(22,163,74,.3)",
          }}>
            &#x2705; Auto-Remediated
          </span>
        )}
        <span style={{ marginLeft: "auto", fontSize: 10, color: "#94a3b8" }}>
          {analysis.category}
        </span>
      </div>

      {/* Root cause */}
      <Section label="Root Cause">
        {analysis.root_cause || "—"}
      </Section>

      {/* Recommended action */}
      {analysis.recommended_action && (
        <Section label="Recommended Action">
          {analysis.recommended_action}
        </Section>
      )}

      {/* Remediation result (truncated) */}
      {analysis.remediation_result && (
        <Section label="Remediation Steps">
          <pre style={{
            whiteSpace: "pre-wrap", fontFamily: "inherit",
            margin: 0, fontSize: 11, color: "#334155",
          }}>
            {remPreview}
          </pre>
        </Section>
      )}

      {/* Footer timestamps + re-trigger */}
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        marginTop: 8, paddingTop: 8, borderTop: "1px solid rgba(99,102,241,.10)",
      }}>
        <span style={{ fontSize: 10, color: "#94a3b8" }}>
          {analysis.detected_at
            ? new Date(analysis.detected_at).toLocaleString("en-IN")
            : ""}
        </span>
        <button
          onClick={retrigger}
          style={{
            padding: "2px 8px", borderRadius: 6, cursor: "pointer",
            background: "transparent", border: "1px solid rgba(99,102,241,.3)",
            color: "#6366f1", fontSize: 10, fontWeight: 600,
          }}
        >
          Re-analyse
        </button>
      </div>
    </div>
  );
}


/**
 * AIOverviewPanel
 * ───────────────
 * Shows the last N AI analyses across all breakdowns.
 * Drop this into MaintenanceHistorical's KPI section or a dedicated tab.
 *
 * <AIOverviewPanel token={token} limit={10} />
 */
export function AIOverviewPanel({ token, limit = 10 }) {
  const [state,  setState]  = useState("idle");
  const [rows,   setRows]   = useState([]);
  const [error,  setError]  = useState(null);

  async function load() {
    setState("loading");
    try {
      const r = await fetch(`/api/n8n/analyses?limit=${limit}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setRows(await r.json());
      setState("done");
    } catch (e) {
      setError(e.message);
      setState("error");
    }
  }

  if (state === "idle") {
    return (
      <button onClick={load} style={{
        padding: "6px 16px", borderRadius: 8, cursor: "pointer",
        background: "rgba(99,102,241,.08)", border: "1px solid rgba(99,102,241,.25)",
        color: "#4f46e5", fontSize: 12, fontWeight: 600,
      }}>
        &#x2728; Load AI Incident Overview
      </button>
    );
  }
  if (state === "loading") {
    return <div style={{ color: "#64748b", fontSize: 12 }}>Loading AI analyses…</div>;
  }
  if (state === "error") {
    return <div style={{ color: "#dc2626", fontSize: 12 }}>Error: {error}</div>;
  }

  return (
    <div>
      <div style={{
        fontSize: 11, fontWeight: 700, letterSpacing: ".1em",
        textTransform: "uppercase", color: "#64748b", marginBottom: 8,
      }}>
        AI Incident Analyses (last {rows.length})
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {rows.length === 0 && (
          <div style={{ color: "#94a3b8", fontSize: 12 }}>No analyses yet.</div>
        )}
        {rows.map(a => (
          <div key={a.id} style={{
            padding: "8px 12px", borderRadius: 8, fontSize: 12,
            background: "#fff", border: "1px solid #e2e8f0",
            display: "flex", gap: 10, alignItems: "flex-start",
          }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 700, color: "#1e293b" }}>
                {a.line_name || a.service}
                {a.machine_no && a.machine_no !== "unknown" &&
                  <span style={{ color: "#64748b", fontWeight: 400 }}>
                    {" "}&mdash; M/{a.machine_no}
                  </span>}
              </div>
              <div style={{ color: "#64748b", marginTop: 2 }}>
                {(a.root_cause || "").slice(0, 120)}
                {(a.root_cause || "").length > 120 ? "…" : ""}
              </div>
              {a.downtime_minutes != null && (
                <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 3 }}>
                  Downtime: {Math.round(a.downtime_minutes)}m
                  {a.mes_breakdown_id &&
                    <span> &bull; BD #{a.mes_breakdown_id}</span>}
                </div>
              )}
            </div>
            <SeverityChip severity={a.severity} />
          </div>
        ))}
      </div>
    </div>
  );
}
