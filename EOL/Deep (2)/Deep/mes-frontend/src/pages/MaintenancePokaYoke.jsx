/* ───────────────────────────────────────────────────────────────────
 * MaintenancePokaYoke.jsx
 * ───────────────────────────────────────────────────────────────────
 * Maintenance team's deep PY view — full technical detail at every
 * level.  Quality only sees counts + bypass log; Maintenance owns
 * the actual fix so they get bit numbers, machine names, expected
 * vs. actual values, model assignments, etc.
 *
 *   Level 1  ZONES        ← grid of zone tiles with PY counters
 *     │  click a zone tile
 *     ▼
 *   Level 2  LINES        ← all lines in the chosen zone
 *     │  click a line tile
 *     ▼
 *   Level 3  MODELS       ← all models configured on the chosen line
 *                           (each model expands to show its PY table
 *                            with PY no / name / side / machine /
 *                            bit / expected value / live OK-vs-BYPASS)
 *
 * Data sources:
 *   GET /api/zones/                              zone list
 *   GET /api/zones/{zone_id}/lines               lines per zone
 *   GET /api/lines/{line_id}/realtime            running model + status
 *   GET /api/config/py-models/{line_id}          all models on line
 *   GET /api/poka-yoke/live/{line_id}            current model PY+bypass
 *   GET /api/poka-yoke/live/{line_id}?model_bit=N PY for any model
 *
 * Polling: 10 s (only the live PY + realtime; structural data
 * fetched once on level entry).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";

const API = "";
const api = {
  async get(path, token) {
    const r = await fetch(API + path, { headers: { Authorization: `Bearer ${token}` } });
    if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
    return r.json();
  },
};

function fmtAgo(ts) {
  if (!ts) return "—";
  const ms = Date.now() - new Date(ts).getTime();
  if (ms < 0) return "just now";
  const s = Math.floor(ms / 1000);
  if (s < 60)   return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60)   return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24)   return `${h}h ${m % 60}m ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function PYCounters({ total, active, inactive, bypass, compact }) {
  const items = [
    { label: "TOTAL",    value: total,    color: "#0f172a" },
    { label: "ACTIVE",   value: active,   color: "#16a34a" },
    { label: "INACTIVE", value: inactive, color: "#94a3b8" },
    { label: "BYPASS",   value: bypass,   color: bypass > 0 ? "#dc2626" : "#0f172a" },
  ];
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(4, 1fr)",
      gap: compact ? 6 : 10,
      width: "100%",
    }}>
      {items.map(i => (
        <div key={i.label} style={{
          background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8,
          padding: compact ? "6px 8px" : "10px 12px", textAlign: "center",
        }}>
          <div style={{ fontSize: compact ? 9 : 10, fontWeight: 700,
                         letterSpacing: ".08em", color: "#64748b" }}>
            {i.label}
          </div>
          <div style={{ fontSize: compact ? 18 : 22, fontWeight: 800,
                         color: i.color, fontFamily: "'Barlow Condensed',sans-serif",
                         lineHeight: 1, marginTop: 2 }}>
            {i.value}
          </div>
        </div>
      ))}
    </div>
  );
}

function computePYStats(pys) {
  const total  = pys.length;
  const bypass = pys.filter(p => p.is_bypassed).length;
  const active = total - bypass;
  return { total, active, bypass };
}

function Breadcrumb({ crumbs }) {
  return (
    <div style={{
      display:"flex", alignItems:"center", gap:8, marginBottom:14,
      fontSize:12, color:"#64748b",
    }}>
      {crumbs.map((c, i) => {
        const isLast = i === crumbs.length - 1;
        return (
          <span key={i} style={{ display:"flex", alignItems:"center", gap:8 }}>
            {c.onClick && !isLast ? (
              <button onClick={c.onClick}
                      style={{
                        background:"transparent", border:"none", padding:0,
                        cursor:"pointer", color:"#dc2626", fontWeight:600,
                        fontSize:12, fontFamily:"inherit",
                      }}>
                {c.label}
              </button>
            ) : (
              <span style={{ color:"#0f172a", fontWeight:700 }}>{c.label}</span>
            )}
            {!isLast && <span style={{ color:"#cbd5e1" }}>/</span>}
          </span>
        );
      })}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// LEVEL 1 — Zones grid
// ════════════════════════════════════════════════════════════════════
function ZonesView({ zones, statsByZone, onPick, theme }) {
  if (zones.length === 0) {
    return (
      <div style={{
        background:"#fff", border:"1px solid #e2e8f0", borderRadius:12,
        padding:"48px 20px", textAlign:"center",
        color:"#94a3b8", fontStyle:"italic", fontSize:13,
      }}>
        No zones configured.
      </div>
    );
  }
  return (
    <div className="mp-zone-grid">
      {zones.map(z => {
        const s = statsByZone[z.id] || { total:0, active:0, inactive:0, bypass:0, lineCount:0 };
        const tileColor = s.bypass > 0 ? "#dc2626"
                        : s.active > 0 ? "#16a34a"
                        :                "#94a3b8";
        const tileBg    = s.bypass > 0 ? "rgba(220,38,38,.06)"
                        : s.active > 0 ? "rgba(22,163,74,.06)"
                        :                "#f8fafc";
        return (
          <button key={z.id} onClick={() => onPick(z)}
                  className="mp-card-btn"
                  style={{ borderColor: tileColor, background: tileBg }}>
            <div style={{ display:"flex", alignItems:"center", gap:14, marginBottom:14 }}>
              <span style={{
                width:14, height:14, borderRadius:"50%", background: tileColor,
                boxShadow: s.bypass > 0 ? "0 0 0 4px rgba(220,38,38,.18)" : "none",
                flexShrink:0,
              }}/>
              <div style={{ flex:1, minWidth:0 }}>
                <div style={{
                  fontSize:18, fontWeight:800, color:"#0f172a",
                  fontFamily:"'Barlow Condensed',sans-serif", letterSpacing:".01em",
                  lineHeight:1.1,
                }}>
                  {z.zone_name}
                </div>
                <div style={{ fontSize:11, color:"#64748b", marginTop:3 }}>
                  {z.zone_code}{z.plant_name ? ` · ${z.plant_name}` : ""}
                </div>
              </div>
              <div style={{
                fontSize:11, fontWeight:700, color:"#64748b",
                background:"#fff", border:"1px solid #e2e8f0",
                borderRadius:99, padding:"3px 10px", whiteSpace:"nowrap",
              }}>
                {s.lineCount} {s.lineCount === 1 ? "line" : "lines"}
              </div>
            </div>
            <PYCounters total={s.total} active={s.active}
                         inactive={s.inactive} bypass={s.bypass}/>
            <div style={{
              fontSize:11, fontWeight:700, color:theme.accent, marginTop:14,
              textAlign:"right",
            }}>
              Click to see lines →
            </div>
          </button>
        );
      })}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// LEVEL 2 — Lines in selected zone
// ════════════════════════════════════════════════════════════════════
function LinesView({ zone, lines, statsByLine, onPick, onBack, theme }) {
  return (
    <>
      <Breadcrumb crumbs={[
        { label: "Zones", onClick: onBack },
        { label: zone.zone_name },
      ]}/>
      {lines.length === 0 ? (
        <div style={{
          background:"#fff", border:"1px solid #e2e8f0", borderRadius:12,
          padding:"48px 20px", textAlign:"center",
          color:"#94a3b8", fontStyle:"italic", fontSize:13,
        }}>
          No lines assigned to this zone.
        </div>
      ) : (
        <div className="mp-zone-grid">
          {lines.map(l => {
            const s = statsByLine[l.id] || { total:0, active:0, inactive:0, bypass:0,
                                              currentModelName:"—", modelCount:0 };
            const tileColor = s.bypass > 0 ? "#dc2626"
                            : s.active > 0 ? "#16a34a"
                            :                "#94a3b8";
            const tileBg    = s.bypass > 0 ? "rgba(220,38,38,.06)"
                            : s.active > 0 ? "rgba(22,163,74,.06)"
                            :                "#f8fafc";
            return (
              <button key={l.id} onClick={() => onPick(l)}
                      className="mp-card-btn"
                      style={{ borderColor: tileColor, background: tileBg }}>
                <div style={{ display:"flex", alignItems:"center", gap:14, marginBottom:10 }}>
                  <span style={{
                    width:14, height:14, borderRadius:"50%", background: tileColor,
                    boxShadow: s.bypass > 0 ? "0 0 0 4px rgba(220,38,38,.18)" : "none",
                    flexShrink:0,
                  }}/>
                  <div style={{ flex:1, minWidth:0 }}>
                    <div style={{
                      fontSize:16, fontWeight:800, color:"#0f172a",
                      fontFamily:"'Barlow Condensed',sans-serif", lineHeight:1.1,
                    }}>
                      {l.line_name || `Line ${l.id}`}
                    </div>
                    <div style={{ fontSize:11, color:"#64748b", marginTop:3 }}>
                      {l.line_code || "—"}{l.plant_name ? ` · ${l.plant_name}` : ""}
                    </div>
                  </div>
                </div>
                <div style={{
                  background:"#fff", border:"1px solid #e2e8f0", borderRadius:8,
                  padding:"8px 10px", marginBottom:10,
                }}>
                  <div style={{ fontSize:9, fontWeight:700, letterSpacing:".08em",
                                 color:"#64748b" }}>
                    NOW RUNNING
                  </div>
                  <div style={{ fontSize:13, fontWeight:700, color:"#0f172a",
                                 marginTop:2, whiteSpace:"nowrap", overflow:"hidden",
                                 textOverflow:"ellipsis" }}>
                    {s.currentModelName || "— no model —"}
                  </div>
                </div>
                <PYCounters total={s.total} active={s.active}
                             inactive={s.inactive} bypass={s.bypass}/>
                <div style={{
                  fontSize:11, fontWeight:700, color:theme.accent, marginTop:14,
                  display:"flex", justifyContent:"space-between", alignItems:"center",
                }}>
                  <span style={{ color:"#64748b" }}>{s.modelCount} models configured</span>
                  <span>Click for models →</span>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </>
  );
}

// ════════════════════════════════════════════════════════════════════
// LEVEL 3 — Models with full PY tables
// ════════════════════════════════════════════════════════════════════
function ModelCard({ model, isRunning, isExpanded, onToggle, pys, loading, error, theme }) {
  const stats     = computePYStats(pys || []);
  const tileColor = !isRunning ? "#94a3b8"
                  : stats.bypass > 0 ? "#dc2626"
                  : stats.total  > 0 ? "#16a34a"
                  :                    "#94a3b8";
  return (
    <div style={{
      background:"#fff",
      border: `2px solid ${isExpanded ? tileColor : "#e2e8f0"}`,
      borderRadius:14, overflow:"hidden",
      boxShadow: isExpanded ? "0 6px 20px rgba(0,0,0,.10)" : "0 1px 3px rgba(0,0,0,.04)",
      transition:"all .18s ease",
    }}>
      <button onClick={onToggle}
              style={{
                width:"100%", border:"none",
                background: isRunning ? "rgba(22,163,74,.04)" : "#fff",
                padding:"14px 18px", cursor:"pointer", textAlign:"left",
              }}>
        <div style={{ display:"flex", alignItems:"center", gap:12, marginBottom:10 }}>
          <span style={{
            width:12, height:12, borderRadius:"50%", background:tileColor,
            boxShadow: stats.bypass > 0 ? "0 0 0 3px rgba(220,38,38,.18)" : "none",
            flexShrink:0,
          }}/>
          <div style={{ flex:1, minWidth:0 }}>
            <div style={{
              fontSize:14, fontWeight:800, color:"#0f172a",
              fontFamily:"'Barlow Condensed',sans-serif", lineHeight:1.15,
            }}>
              {model.modelName || `Model #${model.bitNumber}`}
            </div>
            <div style={{ fontSize:10, color:"#64748b", marginTop:2 }}>
              bit {model.bitNumber ?? "—"}
              {model.type   ? ` · ${model.type}`   : ""}
              {model.model  ? ` · ${model.model}`  : ""}
            </div>
          </div>
          {isRunning && (
            <span style={{
              fontSize:10, fontWeight:800, padding:"3px 9px", borderRadius:99,
              background:"rgba(22,163,74,.14)", color:"#15803d",
              whiteSpace:"nowrap", letterSpacing:".05em",
            }}>
              RUNNING
            </span>
          )}
        </div>
        <PYCounters total={stats.total} active={stats.active}
                     inactive={0} bypass={stats.bypass} compact/>
        <div style={{
          fontSize:11, fontWeight:700, color: theme.accent,
          marginTop:10, textAlign:"right",
        }}>
          {isExpanded ? "click to collapse ▲" : "click to see PY list ▼"}
        </div>
      </button>

      {isExpanded && (
        <div style={{ borderTop:`1px solid ${tileColor}22`, padding:"10px 0" }}>
          {loading ? (
            <div style={{ padding:"20px", textAlign:"center",
                           color:"#94a3b8", fontStyle:"italic", fontSize:12 }}>
              Loading PY list…
            </div>
          ) : error ? (
            <div style={{ padding:"20px", textAlign:"center",
                           color:"#dc2626", fontSize:12 }}>
              Failed: {String(error).slice(0, 80)}
            </div>
          ) : !pys?.length ? (
            <div style={{ padding:"20px", textAlign:"center",
                           color:"#94a3b8", fontStyle:"italic", fontSize:12 }}>
              No PY assignments for this model.
            </div>
          ) : (
            <div style={{ overflowX:"auto" }}>
              <table style={{ width:"100%", borderCollapse:"collapse" }}>
                <thead>
                  <tr style={{ borderBottom:"2px solid #e2e8f0", background:"#f8fafc" }}>
                    {["", "PY No.", "Name", "Side", "Machine / Fixture",
                       "Bit", "Expected", "Live"].map((h,i) =>
                      <th key={i} style={{
                        padding:"8px 12px", fontSize:9, fontWeight:700,
                        letterSpacing:".08em", color:"#64748b",
                        textAlign: i >= 5 ? "center" : "left", whiteSpace:"nowrap",
                      }}>{h}</th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {pys.map((p, i) => {
                    const isBy = !!p.is_bypassed;
                    return (
                      <tr key={i} style={{ borderBottom:"1px solid #f1f5f9" }}>
                        <td style={{ padding:"8px 12px", width:14 }}>
                          <span style={{
                            display:"inline-block", width:10, height:10, borderRadius:"50%",
                            background: isBy ? "#dc2626" : "#16a34a",
                          }}/>
                        </td>
                        <td style={{ padding:"8px 12px", fontFamily:"monospace",
                                       fontSize:11, fontWeight:700 }}>
                          {p.poka_yoke_no}
                        </td>
                        <td style={{ padding:"8px 12px", fontSize:12 }}>
                          {p.poka_yoke_name || "—"}
                        </td>
                        <td style={{ padding:"8px 12px", fontSize:11, color:"#64748b" }}>
                          {p.side || "ALL"}
                        </td>
                        <td style={{ padding:"8px 12px", fontSize:11, color:"#475569" }}>
                          {p.machine_name || "—"}
                        </td>
                        <td style={{ padding:"8px 12px", textAlign:"center",
                                       fontSize:11, fontFamily:"monospace" }}>
                          {p.bit ?? "—"}
                        </td>
                        <td style={{ padding:"8px 12px", textAlign:"center",
                                       fontSize:11, fontFamily:"monospace" }}>
                          {String(p.value ?? "—")}
                        </td>
                        <td style={{ padding:"8px 12px", textAlign:"center" }}>
                          {isBy ? (
                            <div>
                              <span style={{
                                fontSize:10, fontWeight:800, padding:"3px 9px",
                                borderRadius:99, background:"rgba(220,38,38,.12)",
                                color:"#b91c1c", whiteSpace:"nowrap",
                              }}>
                                BYPASS
                              </span>
                              {p.last_bypass_at && (
                                <div style={{ fontSize:9, color:"#94a3b8", marginTop:2 }}>
                                  {fmtAgo(p.last_bypass_at)}
                                </div>
                              )}
                              {p.last_plc_value !== null && p.last_plc_value !== undefined && (
                                <div style={{ fontSize:9, color:"#94a3b8" }}>
                                  read: {String(p.last_plc_value)}
                                </div>
                              )}
                            </div>
                          ) : (
                            <span style={{
                              fontSize:10, fontWeight:800, padding:"3px 9px",
                              borderRadius:99, background:"rgba(22,163,74,.12)",
                              color:"#15803d", whiteSpace:"nowrap",
                            }}>
                              OK
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ModelsView({ zone, line, models, currentModelBit, modelPYs,
                      loadingModelIds, errorModelIds,
                      expandedModelId, onToggleModel, onBack, onBackToZones, theme }) {
  return (
    <>
      <Breadcrumb crumbs={[
        { label: "Zones",        onClick: onBackToZones },
        { label: zone.zone_name, onClick: onBack },
        { label: line.line_name || `Line ${line.id}` },
      ]}/>
      {models.length === 0 ? (
        <div style={{
          background:"#fff", border:"1px solid #e2e8f0", borderRadius:12,
          padding:"48px 20px", textAlign:"center",
          color:"#94a3b8", fontStyle:"italic", fontSize:13,
        }}>
          No PY models configured for this line.
        </div>
      ) : (
        <div className="mp-zone-grid" style={{ gridTemplateColumns:"1fr" }}>
          {models.map(m => (
            <ModelCard key={m.id}
                       model={m}
                       isRunning={Number(m.bitNumber) === Number(currentModelBit)}
                       isExpanded={expandedModelId === m.id}
                       onToggle={() => onToggleModel(m)}
                       pys={modelPYs[m.id]}
                       loading={loadingModelIds.has(m.id)}
                       error={errorModelIds[m.id]}
                       theme={theme}/>
          ))}
        </div>
      )}
    </>
  );
}

// ════════════════════════════════════════════════════════════════════
// Main component
// ════════════════════════════════════════════════════════════════════
export default function MaintenancePokaYoke() {
  const { token, theme, isAdmin, user } = useAuth();

  // Navigation state
  const [level, setLevel]               = useState("zones");
  const [pickedZone, setPickedZone]     = useState(null);
  const [pickedLine, setPickedLine]     = useState(null);

  // Cache
  const [zones, setZones]               = useState([]);
  const [linesByZone, setLinesByZone]   = useState({});
  const [livePYByLine, setLivePYByLine] = useState({});
  const [realtimeByLine, setRealtimeByLine] = useState({});
  const [modelsByLine, setModelsByLine] = useState({});

  // Level 3 lazy caches
  const [modelPYs, setModelPYs]         = useState({});
  const [loadingModelIds, setLoadingModelIds] = useState(new Set());
  const [errorModelIds, setErrorModelIds]   = useState({});
  const [expandedModelId, setExpandedModelId] = useState(null);

  useEffect(() => {
    document.title = isAdmin ? "Maintenance · Poka Yoke" : "Poka Yoke";
  }, [isAdmin]);

  // Initial load — zones + lines per zone
  useEffect(() => {
    if (!token) return;
    let alive = true;
    (async () => {
      try {
        const zs = await api.get("/api/zones/", token);
        if (!alive) return;
        const list = Array.isArray(zs) ? zs : [];
        setZones(list);
        const linesEntries = await Promise.all(list.map(async (z) => {
          try {
            const ls = await api.get(`/api/zones/${z.id}/lines`, token);
            return [z.id, Array.isArray(ls) ? ls : []];
          } catch { return [z.id, []]; }
        }));
        if (!alive) return;
        const map = {};
        for (const [zid, ls] of linesEntries) map[zid] = ls;
        setLinesByZone(map);
      } catch (e) {
        console.warn("[MaintenancePokaYoke] zones load failed:", e);
      }
    })();
    return () => { alive = false; };
  }, [token]);

  // Live PY + realtime poll (10s)
  // Realtime fetched first to get current model_bit / model_name —
  // those are then passed explicitly to /api/poka-yoke/live so we
  // bypass the backend's flaky auto-detect (matches Fullscreen).
  const refreshLive = useCallback(async () => {
    if (!token) return;
    const allLines = Object.values(linesByZone).flat();
    if (!allLines.length) return;

    // Step 1: realtime (model bit + name)
    const rtResults = await Promise.all(allLines.map(async (l) => {
      try {
        const data = await api.get(`/api/lines/${l.id}/realtime`, token);
        return [l.id, data || {}];
      } catch { return [l.id, {}]; }
    }));
    const rtMap = Object.fromEntries(rtResults);
    setRealtimeByLine(rtMap);

    // Step 2: live PY with model context
    const pyResults = await Promise.all(allLines.map(async (l) => {
      try {
        const rt        = rtMap[l.id] || {};
        const modelBit  = rt.current_model_number;
        const modelName = rt.current_model_name || "";
        const params    = new URLSearchParams();
        if (modelBit != null && modelBit !== 0) params.append("model_bit",  String(modelBit));
        if (modelName)                          params.append("model_name", modelName);
        const qs   = params.toString();
        const url  = `/api/poka-yoke/live/${l.id}${qs ? `?${qs}` : ""}`;
        const data = await api.get(url, token);
        return [l.id, Array.isArray(data) ? data : []];
      } catch { return [l.id, []]; }
    }));
    setLivePYByLine(Object.fromEntries(pyResults));
  }, [token, linesByZone]);

  useEffect(() => {
    refreshLive();
    const t = setInterval(refreshLive, 10000);
    return () => clearInterval(t);
  }, [refreshLive]);

  // Lazy: fetch models for a line on Level 3 entry
  useEffect(() => {
    if (level !== "models" || !pickedLine) return;
    if (modelsByLine[pickedLine.id]) return;
    let alive = true;
    (async () => {
      try {
        const ms = await api.get(`/api/config/py-models/${pickedLine.id}`, token);
        if (!alive) return;
        setModelsByLine(prev => ({ ...prev, [pickedLine.id]: Array.isArray(ms) ? ms : [] }));
      } catch (e) {
        if (!alive) return;
        setModelsByLine(prev => ({ ...prev, [pickedLine.id]: [] }));
      }
    })();
    return () => { alive = false; };
  }, [level, pickedLine, modelsByLine, token]);

  // Lazy: fetch model-specific PYs on expand
  const handleToggleModel = (model) => {
    if (expandedModelId === model.id) { setExpandedModelId(null); return; }
    setExpandedModelId(model.id);
    if (modelPYs[model.id]) return;

    setLoadingModelIds(prev => new Set([...prev, model.id]));
    setErrorModelIds(prev => { const n = { ...prev }; delete n[model.id]; return n; });
    api.get(`/api/poka-yoke/live/${pickedLine.id}?model_bit=${model.bitNumber}`, token)
      .then(data => {
        setModelPYs(prev => ({ ...prev, [model.id]: Array.isArray(data) ? data : [] }));
      })
      .catch(e => {
        setErrorModelIds(prev => ({ ...prev, [model.id]: e.message || String(e) }));
      })
      .finally(() => {
        setLoadingModelIds(prev => { const n = new Set(prev); n.delete(model.id); return n; });
      });
  };

  // Stats roll-up
  const statsByLine = useMemo(() => {
    const out = {};
    for (const ls of Object.values(linesByZone)) {
      for (const l of ls) {
        const pys = livePYByLine[l.id] || [];
        const rt  = realtimeByLine[l.id] || {};
        const total  = pys.length;
        const bypass = pys.filter(p => p.is_bypassed).length;
        out[l.id] = {
          total, active: total - bypass, inactive: 0, bypass,
          currentModelBit: rt.current_model_number ?? null,
          currentModelName: rt.current_model_name || "—",
          modelCount: (modelsByLine[l.id] || []).length || 0,
        };
      }
    }
    return out;
  }, [linesByZone, livePYByLine, realtimeByLine, modelsByLine]);

  const statsByZone = useMemo(() => {
    const out = {};
    for (const z of zones) {
      const ls = linesByZone[z.id] || [];
      let total=0, active=0, inactive=0, bypass=0;
      for (const l of ls) {
        const s = statsByLine[l.id] || {};
        total    += s.total    || 0;
        active   += s.active   || 0;
        inactive += s.inactive || 0;
        bypass   += s.bypass   || 0;
      }
      out[z.id] = { total, active, inactive, bypass, lineCount: ls.length };
    }
    return out;
  }, [zones, linesByZone, statsByLine]);

  // Drill-down handlers
  const goZones = () => { setLevel("zones");  setPickedZone(null); setPickedLine(null); setExpandedModelId(null); };
  const goLines = (z) => { setLevel("lines");  setPickedZone(z);   setPickedLine(null); setExpandedModelId(null); };
  const goModels= (l) => { setLevel("models"); setPickedLine(l);   setExpandedModelId(null); };

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@600;700;800&display=swap');
        .mp-root { min-height:100vh; background:#f8fafc; font-family:'Barlow',sans-serif; padding-bottom:48px; }
        .mp-topbar {
          background:#fff; border-bottom:1px solid #e2e8f0;
          padding:0 40px 0 88px; height:60px;
          display:flex; align-items:center; justify-content:space-between;
          position:sticky; top:0; z-index:50; box-shadow:0 1px 3px rgba(0,0,0,.06);
        }
        .mp-topbar::after { content:''; position:absolute; bottom:0; left:0; right:0;
                            height:2px; background:${theme.gradient}; }
        .mp-title { position:absolute; left:50%; transform:translateX(-50%);
                    font-family:'Barlow Condensed',sans-serif; font-size:34px;
                    font-weight:800; color:#0f172a; letter-spacing:-.01em;
                    pointer-events:none; white-space:nowrap; }
        .mp-title span { color:${theme.accent}; }
        .mp-pill { display:flex; align-items:center; gap:10px;
                    padding:6px 14px; border-radius:99px;
                    border:1.5px solid #e2e8f0; background:#f8fafc;
                    font-size:12px; font-weight:600; color:#334155; white-space:nowrap; }
        .mp-pill b { color:#0f172a; font-weight:800; }
        .mp-body { padding:20px 32px 0; max-width:1500px; margin:0 auto; }
        .mp-zone-grid { display:grid;
                         grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
                         gap:16px; align-items:start; }
        .mp-card-btn {
          width:100%; text-align:left; cursor:pointer;
          border:2px solid #e2e8f0; border-radius:14px;
          padding:18px 20px; font-family:'Barlow',sans-serif;
          background:#fff; box-shadow:0 1px 3px rgba(0,0,0,.04);
          transition:all .15s ease;
        }
        .mp-card-btn:hover { transform: translateY(-2px);
                              box-shadow: 0 8px 22px rgba(0,0,0,.10); }
      `}</style>

      <div className="mp-root">
        <div className="mp-topbar">
          <div />
          <div className="mp-title">
            {isAdmin ? "Maintenance " : ""}<span>Poka Yoke</span>
          </div>
          {user?.username && (
            <div className="mp-pill">Signed in as <b>{user.username}</b></div>
          )}
        </div>

        <div className="mp-body">
          {level === "zones" && (
            <ZonesView zones={zones} statsByZone={statsByZone}
                       onPick={goLines} theme={theme}/>
          )}
          {level === "lines" && pickedZone && (
            <LinesView zone={pickedZone}
                       lines={linesByZone[pickedZone.id] || []}
                       statsByLine={statsByLine}
                       onPick={goModels}
                       onBack={goZones}
                       theme={theme}/>
          )}
          {level === "models" && pickedZone && pickedLine && (
            <ModelsView zone={pickedZone}
                        line={pickedLine}
                        models={modelsByLine[pickedLine.id] || []}
                        currentModelBit={
                          (realtimeByLine[pickedLine.id] || {}).current_model_number
                        }
                        modelPYs={modelPYs}
                        loadingModelIds={loadingModelIds}
                        errorModelIds={errorModelIds}
                        expandedModelId={expandedModelId}
                        onToggleModel={handleToggleModel}
                        onBack={() => goLines(pickedZone)}
                        onBackToZones={goZones}
                        theme={theme}/>
          )}
        </div>
      </div>
    </>
  );
}
