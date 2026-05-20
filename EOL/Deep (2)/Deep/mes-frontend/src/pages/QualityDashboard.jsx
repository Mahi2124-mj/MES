/* ───────────────────────────────────────────────────────────────────
 * QualityDashboard.jsx
 * ───────────────────────────────────────────────────────────────────
 * Quality user's home — high-level PY health + bypass log.
 *
 * Operator wanted Quality kept SIMPLE: no bit numbers, machine
 * names, expected-vs-actual, model assignments etc — that lives on
 * the Maintenance "Poka Yoke" page.  Quality just sees:
 *
 *   • Top counters: Total / Active / Bypass plant-wide
 *   • Pending Deviation banner (clickable)
 *   • Zone tiles with the same 3 counters (no drill-down clutter)
 *      └─ click → expand inline to show LINES with their counts
 *           └─ if any bypass live: PY name + since-when +
 *              mailed-to / cc list (TO + CC from mail config)
 *
 * Toast popup:
 *   When Maintenance raises a fresh deviation, a clickable card
 *   slides in bottom-right.  Click → /quality-deviations?dev=<id>.
 *
 * Data sources:
 *   GET /api/zones/                         zones
 *   GET /api/zones/{zone_id}/lines          lines per zone
 *   GET /api/poka-yoke/live/{line_id}       PY status (we use just
 *                                            count + bypass + name +
 *                                            last_bypass_at fields)
 *   GET /api/poka-yoke/mail-config/         mailing list (bypass_to / cc)
 *   GET /api/quality/deviations?days=2      deviation toast feed
 *
 * Polling: 10 s.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import ManpowerAlertBanner from "../components/ManpowerAlertBanner";

const API = "";
const api = {
  async get(path, token) {
    const r = await fetch(API + path, { headers: { Authorization: `Bearer ${token}` } });
    if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
    return r.json();
  },
};

const SEEN_KEY = "quality_seen_deviation_ids";
function loadSeenIds() {
  try {
    const raw = JSON.parse(localStorage.getItem(SEEN_KEY) || "[]");
    return Array.isArray(raw) ? new Set(raw) : new Set();
  } catch { return new Set(); }
}
function saveSeenIds(set) {
  try { localStorage.setItem(SEEN_KEY, JSON.stringify([...set])); } catch {}
}

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

// Compact 3-cell counter row (no inactive — Quality side simpler)
function PYCounters({ total, active, bypass, compact }) {
  const items = [
    { label: "TOTAL",  value: total,  color: "#0f172a" },
    { label: "OK",     value: active, color: "#16a34a" },
    { label: "BYPASS", value: bypass, color: bypass > 0 ? "#dc2626" : "#0f172a" },
  ];
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "repeat(3, 1fr)",
      gap: compact ? 6 : 10, width: "100%",
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

// ════════════════════════════════════════════════════════════════════
// Toast for new deviation request
// ════════════════════════════════════════════════════════════════════
function DeviationToast({ deviation, onDismiss, onClick, theme }) {
  return (
    <div
      onClick={onClick}
      style={{
        background: "#fff",
        border: `2px solid ${theme.accent}`,
        borderRadius: 12,
        padding: "12px 16px",
        boxShadow: "0 12px 28px rgba(0,0,0,.18)",
        cursor: "pointer",
        minWidth: 320, maxWidth: 380,
        animation: "qd-toast-in .25s ease",
        position: "relative",
      }}
    >
      <button
        onClick={e => { e.stopPropagation(); onDismiss(); }}
        style={{
          position: "absolute", top: 6, right: 8,
          background: "transparent", border: "none", cursor: "pointer",
          fontSize: 16, color: "#94a3b8", lineHeight: 1, padding: 4,
        }}
        aria-label="Dismiss"
      >×</button>
      <div style={{
        fontSize: 10, fontWeight: 800, letterSpacing: ".08em",
        textTransform: "uppercase", color: theme.accent, marginBottom: 4,
      }}>
        ⚠ New Deviation Request
      </div>
      <div style={{ fontSize: 14, fontWeight: 800, color: "#0f172a", marginBottom: 4 }}>
        {deviation.dev_no || `#${deviation.id}`} · {deviation.line_name || `Line ${deviation.line_id}`}
      </div>
      <div style={{ fontSize: 11, color: "#475569", lineHeight: 1.4,
                    overflow: "hidden", textOverflow: "ellipsis",
                    display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
        {deviation.reason || deviation.requirement || "Maintenance has raised a deviation request."}
      </div>
      <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 6, display: "flex", justifyContent: "space-between" }}>
        <span>{deviation.raised_by_username || deviation.initiated_by || "—"}</span>
        <span>{fmtAgo(deviation.created_at)}</span>
      </div>
      <div style={{ fontSize: 10, color: theme.accentDark, fontWeight: 700, marginTop: 6 }}>
        Click to review →
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// Single bypass row (PY name + since when + mailed-to)
// ════════════════════════════════════════════════════════════════════
function BypassRow({ py, lineName, mailTo, mailCc }) {
  const allMails = [
    ...(mailTo || []).map(e => ({ addr: e, kind: "TO" })),
    ...(mailCc || []).map(e => ({ addr: e, kind: "CC" })),
  ];
  return (
    <div style={{
      background: "rgba(220,38,38,.04)",
      border: "1px solid rgba(220,38,38,.20)",
      borderRadius: 10, padding: "12px 14px", marginTop: 8,
      display: "grid",
      gridTemplateColumns: "10px 1fr auto",
      gap: 12, alignItems: "center",
    }}>
      <span style={{
        width: 10, height: 10, borderRadius: "50%",
        background: "#dc2626",
        boxShadow: "0 0 0 3px rgba(220,38,38,.18)",
      }}/>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 800, color: "#0f172a",
                       whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {py.poka_yoke_name || py.poka_yoke_no || "—"}
        </div>
        <div style={{ fontSize: 10, color: "#64748b", marginTop: 2 }}>
          {lineName} · since {fmtAgo(py.last_bypass_at)}
        </div>
        {allMails.length > 0 && (
          <div style={{ fontSize: 10, color: "#475569", marginTop: 4,
                         display: "flex", flexWrap: "wrap", gap: 4 }}>
            <span style={{ fontWeight: 700, color: "#64748b" }}>Mailed:</span>
            {allMails.map((m, i) => (
              <span key={i} style={{
                padding: "1px 7px", borderRadius: 99,
                background: m.kind === "TO" ? "rgba(202,138,4,.10)" : "#f1f5f9",
                color: m.kind === "TO" ? "#a16207" : "#475569",
                fontWeight: 600,
              }}>
                {m.addr} <span style={{ fontSize: 9, opacity: .6 }}>({m.kind})</span>
              </span>
            ))}
          </div>
        )}
        {allMails.length === 0 && (
          <div style={{ fontSize: 10, color: "#dc2626", marginTop: 4, fontStyle: "italic" }}>
            ⚠ No mail recipients configured (Admin → Quality → Mail Config)
          </div>
        )}
      </div>
      <span style={{
        fontSize: 10, fontWeight: 800, padding: "4px 12px", borderRadius: 99,
        background: "rgba(220,38,38,.12)", color: "#b91c1c",
        whiteSpace: "nowrap", letterSpacing: ".05em",
      }}>
        BYPASS
      </span>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// Single zone tile (collapsed or expanded)
// ════════════════════════════════════════════════════════════════════
function ZoneTile({ zone, lines, livePYByLine, isExpanded, onToggle, mailTo, mailCc, theme }) {
  // roll-up
  let total=0, bypass=0;
  const linesData = lines.map(l => {
    const pys = livePYByLine[l.id] || [];
    const t   = pys.length;
    const b   = pys.filter(p => p.is_bypassed).length;
    total += t; bypass += b;
    return { line: l, pys, total: t, bypass: b };
  });
  const active    = total - bypass;
  const tileColor = bypass > 0 ? "#dc2626"
                  : total  > 0 ? "#16a34a"
                  :              "#94a3b8";
  const tileBg    = bypass > 0 ? "rgba(220,38,38,.06)"
                  : total  > 0 ? "rgba(22,163,74,.06)"
                  :              "#f8fafc";

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
                width:"100%", border:"none", background: tileBg,
                padding:"16px 20px", cursor:"pointer", textAlign:"left",
              }}>
        <div style={{ display:"flex", alignItems:"center", gap:14, marginBottom:14 }}>
          <span style={{
            width:14, height:14, borderRadius:"50%", background: tileColor,
            boxShadow: bypass > 0 ? "0 0 0 4px rgba(220,38,38,.18)" : "none",
            flexShrink:0,
          }}/>
          <div style={{ flex:1, minWidth:0 }}>
            <div style={{
              fontSize:18, fontWeight:800, color:"#0f172a",
              fontFamily:"'Barlow Condensed',sans-serif", lineHeight:1.1,
            }}>
              {zone.zone_name}
            </div>
            <div style={{ fontSize:11, color:"#64748b", marginTop:3 }}>
              {zone.zone_code}{zone.plant_name ? ` · ${zone.plant_name}` : ""}
            </div>
          </div>
          <div style={{
            fontSize:11, fontWeight:700, color:"#64748b",
            background:"#fff", border:"1px solid #e2e8f0",
            borderRadius:99, padding:"3px 10px", whiteSpace:"nowrap",
          }}>
            {lines.length} {lines.length === 1 ? "line" : "lines"}
          </div>
        </div>
        <PYCounters total={total} active={active} bypass={bypass}/>
        <div style={{ fontSize:11, fontWeight:700, color:theme.accent, marginTop:14,
                        textAlign:"right" }}>
          {isExpanded ? "click to collapse ▲" : "click to see lines ▼"}
        </div>
      </button>

      {isExpanded && (
        <div style={{ padding:"12px 16px 16px", background:"#fff",
                        borderTop:`1px solid ${tileColor}33` }}>
          {lines.length === 0 ? (
            <div style={{ padding:"20px", textAlign:"center",
                            color:"#94a3b8", fontStyle:"italic", fontSize:12 }}>
              No lines assigned to this zone.
            </div>
          ) : linesData.map(({ line, pys, total: lt, bypass: lb }) => {
            const lActive = lt - lb;
            return (
              <div key={line.id} style={{
                padding:"10px 0", borderTop:"1px solid #f1f5f9",
              }}>
                <div style={{ display:"grid",
                              gridTemplateColumns:"10px 1fr auto auto auto",
                              gap:12, alignItems:"center" }}>
                  <span style={{
                    width:10, height:10, borderRadius:"50%",
                    background: lb > 0 ? "#dc2626" : (lt > 0 ? "#16a34a" : "#94a3b8"),
                  }}/>
                  <div style={{ minWidth:0 }}>
                    <div style={{ fontSize:13, fontWeight:700, color:"#0f172a",
                                    whiteSpace:"nowrap", overflow:"hidden",
                                    textOverflow:"ellipsis" }}>
                      {line.line_name || `Line ${line.id}`}
                    </div>
                    <div style={{ fontSize:10, color:"#94a3b8", marginTop:1 }}>
                      {line.line_code || "—"}
                    </div>
                  </div>
                  <span style={{ fontSize:10, color:"#0f172a", fontWeight:700,
                                  background:"#f1f5f9", padding:"3px 10px",
                                  borderRadius:99, whiteSpace:"nowrap" }}>
                    {lt} total
                  </span>
                  <span style={{ fontSize:10, color:"#15803d", fontWeight:700,
                                  background:"rgba(22,163,74,.10)", padding:"3px 10px",
                                  borderRadius:99, whiteSpace:"nowrap" }}>
                    {lActive} OK
                  </span>
                  <span style={{ fontSize:10, fontWeight:800,
                                  background: lb > 0 ? "rgba(220,38,38,.12)" : "#f1f5f9",
                                  color: lb > 0 ? "#b91c1c" : "#475569",
                                  padding:"3px 10px", borderRadius:99, whiteSpace:"nowrap" }}>
                    {lb} BYPASS
                  </span>
                </div>

                {/* List the actual bypassed PYs (only the bypassed ones) */}
                {pys.filter(p => p.is_bypassed).map((p, i) => (
                  <BypassRow key={i}
                              py={p}
                              lineName={line.line_name || `Line ${line.id}`}
                              mailTo={mailTo}
                              mailCc={mailCc}/>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// Main
// ════════════════════════════════════════════════════════════════════
export default function QualityDashboard() {
  const { token, theme, isAdmin, user } = useAuth();
  const navigate = useNavigate();

  const [zones, setZones]               = useState([]);
  const [linesByZone, setLinesByZone]   = useState({});
  const [livePYByLine, setLivePYByLine] = useState({});
  const [realtimeByLine, setRealtimeByLine] = useState({});  // per-line realtime: needed for current model_bit
  const [expandedZoneId, setExpandedZoneId] = useState(null);

  const [pendingDevs, setPendingDevs]   = useState([]);
  const [toasts, setToasts]             = useState([]);
  const seenIdsRef                      = useRef(loadSeenIds());

  // Mail recipients (TO + CC) for bypass alerts
  const [mailTo, setMailTo] = useState([]);
  const [mailCc, setMailCc] = useState([]);

  useEffect(() => {
    document.title = isAdmin ? "Quality Dashboard" : "Dashboard";
  }, [isAdmin]);

  // ── Load zones + lines ──
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
        console.warn("[QualityDashboard] zones load failed:", e);
      }
    })();
    return () => { alive = false; };
  }, [token]);

  // ── Load mail config (bypass_to / bypass_cc) ──
  // Refreshed on the same 10s tick as PY data so admin edits propagate
  // without a page reload.
  const refreshMailConfig = useCallback(async () => {
    if (!token) return;
    try {
      const cfg = await api.get("/api/poka-yoke/mail-config/", token);
      // Endpoint returns array of {key, value}; pick bypass_to + bypass_cc.
      const arr = Array.isArray(cfg) ? cfg : [];
      const find = (k) => {
        const r = arr.find(x => (x.key || "").toLowerCase() === k);
        return r ? (r.value || "") : "";
      };
      const split = (s) => (s || "").split(",").map(e => e.trim()).filter(Boolean);
      setMailTo(split(find("bypass_to")));
      setMailCc(split(find("bypass_cc")));
    } catch (e) {
      // Endpoint may differ; silently ignore so dashboard still works.
      console.warn("[QualityDashboard] mail-config load failed:", e);
    }
  }, [token]);

  // ── Live PY poll ──
  // Two-step per line:
  //   1) /api/lines/{id}/realtime  → current_model_number / _name
  //   2) /api/poka-yoke/live/{id}?model_bit=N&model_name=...
  // The live endpoint can auto-detect the model from the shift table,
  // BUT that lookup quietly fails when current_shift_row_id is stale
  // or the dashboard table doesn't have a fresh row.  Passing the
  // model context explicitly (same as Fullscreen) is rock-solid.
  const refreshLive = useCallback(async () => {
    if (!token) return;
    const allLines = Object.values(linesByZone).flat();
    if (!allLines.length) return;

    // Step 1: realtime (gives us model bit + name)
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
    refreshMailConfig();
    const t = setInterval(() => { refreshLive(); refreshMailConfig(); }, 10000);
    return () => clearInterval(t);
  }, [refreshLive, refreshMailConfig]);

  // ── Header roll-up ──
  const headerStats = useMemo(() => {
    let total=0, bypass=0, lineRed=0, lineTotal=0;
    for (const ls of Object.values(linesByZone)) {
      for (const l of ls) {
        const pys = livePYByLine[l.id] || [];
        total += pys.length;
        const b = pys.filter(p => p.is_bypassed).length;
        bypass += b;
        lineTotal++;
        if (b > 0) lineRed++;
      }
    }
    return { total, active: total - bypass, bypass, lineRed, lineTotal };
  }, [linesByZone, livePYByLine]);

  // ── Deviation toast ──
  const refreshDeviations = useCallback(async () => {
    if (!token) return;
    try {
      const list = await api.get("/api/quality/deviations?days=2", token);
      const arr  = Array.isArray(list) ? list : [];
      const pending = arr.filter(d => d.status === "PENDING_QA");
      setPendingDevs(pending);

      const seen = seenIdsRef.current;
      const fresh = pending.filter(d => !seen.has(d.id));
      if (fresh.length) {
        setToasts(prev => {
          const exist = new Set(prev.map(t => t.deviation.id));
          const adds  = fresh.filter(d => !exist.has(d.id))
                              .map(d => ({ deviation:d, key:d.id }));
          return [...prev, ...adds];
        });
        for (const d of fresh) seen.add(d.id);
        saveSeenIds(seen);
      }
    } catch (e) {
      console.warn("[QualityDashboard] deviations load failed:", e);
    }
  }, [token]);

  useEffect(() => {
    refreshDeviations();
    const t = setInterval(refreshDeviations, 10000);
    return () => clearInterval(t);
  }, [refreshDeviations]);

  useEffect(() => {
    if (!toasts.length) return;
    const timers = toasts.map(t =>
      setTimeout(() => setToasts(prev => prev.filter(x => x.key !== t.key)), 30000)
    );
    return () => timers.forEach(clearTimeout);
  }, [toasts]);

  const handleToastClick = (deviation) => {
    setToasts(prev => prev.filter(t => t.key !== deviation.id));
    navigate(`/quality-deviations?dev=${deviation.id}`);
  };

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@600;700;800&display=swap');
        @keyframes qd-toast-in {
          from { opacity: 0; transform: translateY(20px) scale(.96); }
          to   { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes qd-pulse-red {
          0%,100% { box-shadow: 0 0 0 0 rgba(220,38,38,.5); }
          50%     { box-shadow: 0 0 0 8px rgba(220,38,38,0); }
        }
        .qd-root { min-height:100vh; background:#f8fafc; font-family:'Barlow',sans-serif; padding-bottom:48px; }
        .qd-topbar {
          background:#fff; border-bottom:1px solid #e2e8f0;
          padding:0 40px 0 88px; height:60px;
          display:flex; align-items:center; justify-content:space-between;
          position:sticky; top:0; z-index:50; box-shadow:0 1px 3px rgba(0,0,0,.06);
        }
        .qd-topbar::after { content:''; position:absolute; bottom:0; left:0; right:0;
                            height:2px; background:${theme.gradient}; }
        .qd-title { position:absolute; left:50%; transform:translateX(-50%);
                    font-family:'Barlow Condensed',sans-serif; font-size:34px;
                    font-weight:800; color:#0f172a; letter-spacing:-.01em;
                    pointer-events:none; white-space:nowrap; }
        .qd-title span { color:${theme.accent}; }
        .qd-pill { display:flex; align-items:center; gap:10px;
                    padding:6px 14px; border-radius:99px;
                    border:1.5px solid #e2e8f0; background:#f8fafc;
                    font-size:12px; font-weight:600; color:#334155; white-space:nowrap; }
        .qd-pill b { color:#0f172a; font-weight:800; }
        .qd-body { padding:20px 32px 0; max-width:1500px; margin:0 auto; }
        .qd-summary { display:flex; gap:14px; flex-wrap:wrap; align-items:stretch;
                      margin-bottom:18px; }
        .qd-summary-tile { background:#fff; border:1px solid #e2e8f0; border-radius:12px;
                           padding:14px 18px; min-width:170px; flex:0 0 auto;
                           box-shadow:0 1px 3px rgba(0,0,0,.04); }
        .qd-summary-tile .lab { font-size:10px; color:#64748b; font-weight:700;
                                letter-spacing:.08em; text-transform:uppercase; }
        .qd-summary-tile .val { font-size:26px; font-weight:800; color:#0f172a;
                                margin-top:2px; font-family:'Barlow Condensed',sans-serif; }
        .qd-summary-tile .sub { font-size:11px; color:#94a3b8; margin-top:1px; }
        .qd-banner {
          flex:1; min-width:280px;
          background:linear-gradient(135deg, #fef3c7, #fde68a);
          border:1.5px solid #fbbf24; border-radius:12px;
          padding:14px 18px; cursor:pointer;
          display:flex; align-items:center; gap:14px;
          transition:transform .15s, box-shadow .15s;
        }
        .qd-banner:hover { transform: translateY(-1px);
                           box-shadow: 0 6px 18px rgba(0,0,0,.10); }
        .qd-banner.zero { background:#f8fafc; border-color:#e2e8f0; cursor:default; }
        .qd-banner-icon { width:38px; height:38px; border-radius:50%;
                           background:#dc2626; color:#fff; display:flex;
                           align-items:center; justify-content:center;
                           font-size:18px; font-weight:800;
                           animation:qd-pulse-red 1.6s infinite; flex-shrink:0; }
        .qd-banner.zero .qd-banner-icon { background:#16a34a; animation:none; }
        .qd-zone-grid { display:grid;
                         grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
                         gap:16px; align-items:start; }
        .qd-toast-stack {
          position:fixed; bottom:24px; right:24px; z-index:200;
          display:flex; flex-direction:column; gap:12px;
          max-height: calc(100vh - 80px); overflow:auto;
        }
      `}</style>

      <div className="qd-root">
        <div className="qd-topbar">
          <div />
          <div className="qd-title">
            {isAdmin ? "Quality " : ""}<span>Dashboard</span>
          </div>
          {user?.username && (
            <div className="qd-pill">Signed in as <b>{user.username}</b></div>
          )}
        </div>

        <div className="qd-body">
          {/* Manpower alerts (UNALLOCATED / SKILL_MISMATCH / ESCALATION) —
              Quality needs to acknowledge as their side; banner polls
              every 30 s and auto-hides when both sides have acked. */}
          <ManpowerAlertBanner />

          {/* Top — plant-wide PY counts + deviation banner */}
          <div className="qd-summary">
            <div className="qd-summary-tile">
              <div className="lab">Total PY</div>
              <div className="val">{headerStats.total}</div>
              <div className="sub">across {headerStats.lineTotal} lines</div>
            </div>
            <div className="qd-summary-tile">
              <div className="lab">OK</div>
              <div className="val" style={{ color:"#16a34a" }}>{headerStats.active}</div>
              <div className="sub">currently watching</div>
            </div>
            <div className="qd-summary-tile">
              <div className="lab">Bypass</div>
              <div className="val" style={{ color: headerStats.bypass>0 ? "#dc2626" : "#0f172a" }}>
                {headerStats.bypass}
              </div>
              <div className="sub">
                {headerStats.lineRed > 0 ? `${headerStats.lineRed} line(s) red` : "all clean"}
              </div>
            </div>

            <div
              className={`qd-banner${pendingDevs.length === 0 ? " zero" : ""}`}
              onClick={() => pendingDevs.length > 0 && navigate("/quality-deviations")}
              role={pendingDevs.length > 0 ? "button" : undefined}
              tabIndex={pendingDevs.length > 0 ? 0 : -1}
            >
              <div className="qd-banner-icon">
                {pendingDevs.length > 0 ? "⚠" : "✓"}
              </div>
              <div style={{ flex:1, minWidth:0 }}>
                <div style={{ fontSize:14, fontWeight:800, color:"#0f172a" }}>
                  {pendingDevs.length > 0
                    ? `${pendingDevs.length} Deviation${pendingDevs.length > 1 ? "s" : ""} Awaiting Review`
                    : "No Pending Deviations"}
                </div>
                <div style={{ fontSize:11, color:"#475569", marginTop:2 }}>
                  {pendingDevs.length > 0
                    ? "Click to open the Quality Deviation queue →"
                    : "All Maintenance requests reviewed."}
                </div>
              </div>
            </div>
          </div>

          {/* Zone tiles */}
          {zones.length === 0 ? (
            <div style={{
              background:"#fff", border:"1px solid #e2e8f0", borderRadius:12,
              padding:"48px 20px", textAlign:"center",
              color:"#94a3b8", fontStyle:"italic", fontSize:13,
            }}>
              No zones configured.
            </div>
          ) : (
            <div className="qd-zone-grid">
              {zones.map(z => (
                <ZoneTile key={z.id}
                          zone={z}
                          lines={linesByZone[z.id] || []}
                          livePYByLine={livePYByLine}
                          isExpanded={expandedZoneId === z.id}
                          onToggle={() => setExpandedZoneId(prev => prev === z.id ? null : z.id)}
                          mailTo={mailTo}
                          mailCc={mailCc}
                          theme={theme}/>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Toast stack */}
      <div className="qd-toast-stack">
        {toasts.map(t => (
          <DeviationToast
            key={t.key}
            deviation={t.deviation}
            theme={theme}
            onDismiss={() => setToasts(prev => prev.filter(x => x.key !== t.key))}
            onClick={() => handleToastClick(t.deviation)}
          />
        ))}
      </div>
    </>
  );
}
