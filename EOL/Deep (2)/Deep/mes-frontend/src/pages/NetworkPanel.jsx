/* ───────────────────────────────────────────────────────────────────
 * NetworkPanel.jsx   (/network)   2026-06-21  (v5 — full editable canvas)
 * ───────────────────────────────────────────────────────────────────
 * NOC dark dashboard for the seat-slider DGS-1210-28P switches.
 * Pulls live data from GET /api/network/status (backend SNMP poller).
 *
 * Floor Plan tab = a FULLY EDITABLE CANVAS:
 *   • + Switch / + Area  → add nodes
 *   • drag a node        → move it (saved on drop)
 *   • click a node       → edit name / IP / SNMP / PoE / size (Save / Delete)
 *   • Connect            → click 2 switches → draw a cable (live-monitored)
 *   • click a cable line → delete it
 * Everything persists to network_devices.json / network_cables.json via
 * PUT and is monitored live (SNMP) for any switch that has an IP.
 */
import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const POE_BUDGET = 193;

// demo fallback (only used when the API is unreachable / empty)
const DEMO_DEVICES = [
  { id: "ysd", name: "YSD", kind: "switch", x: 80,  y: 60,  status: "up",   ip: "—", vlan: "10", portsUp: 14, portsTotal: 24, poe: 95,  poe_budget: POE_BUDGET },
  { id: "yca", name: "YCA", kind: "switch", x: 260, y: 60,  status: "warn", ip: "—", vlan: "20", portsUp: 12, portsTotal: 24, poe: 110, poe_budget: POE_BUDGET },
  { id: "ync", name: "YNC", kind: "switch", x: 440, y: 60,  status: "up",   ip: "—", vlan: "30", portsUp: 16, portsTotal: 24, poe: 130, poe_budget: POE_BUDGET },
  { id: "y17", name: "Y17", kind: "switch", x: 620, y: 60,  status: "up",   ip: "—", vlan: "40", portsUp: 10, portsTotal: 24, poe: 88,  poe_budget: POE_BUDGET },
];
const DEMO_CABLES = [
  { id: "C-01", from: "ysd", to: "yca", media: "CAT-7", len: 48 },
  { id: "C-02", from: "yca", to: "ync", media: "CAT-7", len: 52 },
  { id: "C-03", from: "ync", to: "y17", media: "CAT-7", len: 40 },
];

const NW = 122, NH = 66;
const SC = { up: "#22c55e", warn: "#f59e0b", down: "#ef4444", core: "#64748b", unconfigured: "#475569" };
const sc = (d) => d.role === "core" && d.status !== "down" ? SC.core : (SC[d.status] || SC.unconfigured);
const poeCol = (p) => p >= 90 ? SC.down : p >= 70 ? SC.warn : SC.up;
const byId = (list, id) => list.find(d => d.id === id);
const devName = (list, id) => { const d = list.find(x => x.id === id); return d ? d.name : id; };
const cableStatus = (devices, cb) => {
  const a = byId(devices, cb.from), b = byId(devices, cb.to);
  if (cb.status && (!a || !b)) return cb.status;             // demo
  if (!a || !b) return "unconfigured";
  if (a.status === "down" || b.status === "down") return "down";
  if (a.status === "unconfigured" || b.status === "unconfigured") return "unconfigured";
  if (a.status === "warn" || b.status === "warn") return "warn";
  return "up";
};

const CSS = `
.nw-root{background:#0a1120;min-height:100vh;margin:-16px -24px;padding:20px 26px 60px;font-family:'Barlow',sans-serif;color:#e2e8f0}
@media (max-width:600px){.nw-root{margin-left:0;margin-right:0;padding-left:14px;padding-right:14px}}
.nw-card{background:#111c30;border:1px solid #1e2a44;border-radius:16px}
.nw-dot{display:inline-block;width:9px;height:9px;border-radius:50%}
.nw-live .nw-dot{animation:nwpulse 1.6s ease-in-out infinite}
@keyframes nwpulse{0%,100%{box-shadow:0 0 0 0 currentColor;opacity:1}50%{box-shadow:0 0 0 5px transparent;opacity:.55}}
.nw-kpi{flex:1;min-width:150px;background:#111c30;border:1px solid #1e2a44;border-radius:14px;padding:14px 16px;position:relative;overflow:hidden}
.nw-kpi::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--ac)}
.nw-tab{padding:8px 16px;border-radius:9px;font-size:13px;font-weight:700;cursor:pointer;border:1px solid #243150;background:#0f1830;color:#94a3b8;display:inline-flex;align-items:center;gap:7px;transition:all .15s}
.nw-tab:hover{border-color:#3b5896;color:#cbd5e1}
.nw-tab.on{background:#1d4ed8;border-color:#1d4ed8;color:#fff;box-shadow:0 4px 14px rgba(29,78,216,.4)}
.nw-node{position:absolute;border-radius:13px;padding:9px 10px;cursor:pointer;background:#0f1a2e;border:1px solid var(--c);box-shadow:0 0 0 1px var(--c)22,0 6px 18px rgba(0,0,0,.45);transition:transform .15s,box-shadow .15s}
.nw-node:hover{transform:translateY(-3px);box-shadow:0 0 0 1px var(--c)55,0 0 22px var(--c)44,0 10px 24px rgba(0,0,0,.5)}
.nw-node.sel{box-shadow:0 0 0 2px var(--c),0 0 26px var(--c)66}
.nw-node.unc{opacity:.55;border-style:dashed}
.nw-bar{height:5px;border-radius:4px;background:#1e293b;overflow:hidden}
.nw-bar>span{display:block;height:100%}
.nw-flow{stroke-dasharray:7 6;animation:nwflow 1s linear infinite}
@keyframes nwflow{to{stroke-dashoffset:-13}}
.nw-row:hover{background:#0f1830}
.nw-pill{font-size:10px;font-weight:800;padding:3px 9px;border-radius:999px;display:inline-flex;align-items:center;gap:5px;letter-spacing:.03em}
`;

export default function NetworkPanel({ mode = "view" }) {
  const { token } = useAuth();
  const editable = mode === "edit";
  const [view, setView] = useState("floor");
  const [sel, setSel]   = useState(null);
  const [now, setNow]   = useState(new Date());
  const [snap, setSnap] = useState(null);
  // True while a switch/cable editor form is open in the floor plan.  The 15s
  // background poll is PAUSED while editing so setSnap can't yank `devices`
  // out from under an open form (which made the next save persist a stale
  // full registry and delete/revert devices).
  const editingRef = useRef(false);

  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []);

  const load = useCallback(() => {
    // MUST return the promise — putDevices/putCables do `await reload()` and
    // rely on snap being refreshed before they resolve.
    return api.get("/api/network/status", token)
      .then(d => setSnap(d))
      // On a transient poll failure KEEP the last-good snapshot (mark it stale)
      // instead of dropping to {_err} → DEMO_DEVICES, which used to yank the
      // whole floor plan to 4 demo switches for 15s on any dropped poll.
      .catch(() => setSnap(prev =>
        (prev && Array.isArray(prev.devices) && prev.devices.length && !prev.demo)
          ? { ...prev, _stale: true } : { _err: true }));
  }, [token]);
  // Skip the interval poll while an editor is open (the explicit reload() after
  // a save is NOT gated, so saves still refresh immediately).
  useEffect(() => { load(); const t = setInterval(() => { if (!editingRef.current) load(); }, 15000); return () => clearInterval(t); }, [load]);

  const isLive   = !!(snap && Array.isArray(snap.devices) && snap.devices.length && !snap.demo);
  const devices  = isLive ? snap.devices : DEMO_DEVICES;
  const cables   = isLive ? (Array.isArray(snap.cables) ? snap.cables : []) : DEMO_CABLES;
  const switches = devices.filter(d => (d.kind || "switch") === "switch");
  const pcs      = devices.filter(d => d.kind === "pc");
  const selDev   = sel ? byId(switches, sel) : null;

  const poeDevs = switches.filter(d => d.poe_budget);
  const poeUsed = poeDevs.reduce((a, d) => a + (Number(d.poe) || 0), 0);
  const poeMax  = poeDevs.reduce((a, d) => a + (d.poe_budget || 0), 0) || POE_BUDGET;
  const poePct  = poeMax ? Math.round((poeUsed / poeMax) * 100) : 0;
  const configured = switches.filter(d => d.status !== "unconfigured");
  const up      = configured.filter(d => d.status === "up" || d.status === "warn").length;
  const down    = switches.filter(d => d.status === "down").length;
  const uncfg   = switches.filter(d => d.status === "unconfigured").length;

  const alerts = isLive ? deriveAlerts(switches) : DEMO_ALERTS;

  const TABS = [
    ["floor", "▤", "Floor Plan"], ["topology", "⬡", "Topology"], ["wire", "↔", "Wire Route"],
    ["registry", "≡", "Devices & Cables"], ["alerts", "!", `Alerts ${alerts.length}`],
    ["conflicts", "⚠", "IP Conflicts"],
  ];
  const KPIS = [
    { ac: down ? SC.warn : SC.up, val: `${up}/${switches.length || 0}`, lab: "Switches online", note: down ? `${down} down` : (uncfg ? `${uncfg} not configured` : "all reachable"), noteCol: down ? SC.down : SC.warn },
    { ac: SC.up, val: `${cables.length}`, lab: "Cables mapped", note: cables.length ? "live-monitored" : "draw in Floor Plan", noteCol: "#7c8aa5" },
    { ac: poeCol(poePct), val: `${poeUsed} W`, lab: `PoE load · ${poePct}% of ${poeMax} W`, bar: poePct, barCol: poeCol(poePct) },
    { ac: SC.core, val: `${switches.length}`, lab: "Switches placed", note: `${configured.length} configured`, noteCol: "#7c8aa5" },
    { ac: alerts.length ? SC.down : SC.up, val: `${alerts.length}`, lab: "Active alerts", note: down ? "incl. unreachable" : "ok", noteCol: SC.warn },
  ];

  return (
    <div className="nw-root">
      <style>{CSS}</style>
      <div style={{ textAlign: "center", marginBottom: 18 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 12 }}>
          <div style={{ width: 40, height: 40, borderRadius: 11, background: "linear-gradient(135deg,#1d4ed8,#3b82f6)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, boxShadow: "0 4px 16px rgba(59,130,246,.45)" }}>🖧</div>
          <div style={{ fontSize: 24, fontWeight: 800, color: "#f8fafc", lineHeight: 1.1 }}>{editable ? "Network Builder" : "Network Monitor"}</div>
        </div>
        {editable && <div style={{ fontSize: 12, color: "#7c8aa5", marginTop: 5 }}>Seat Slider · D-Link DGS-1210-28P · {switches.length} switches{pcs.length ? ` · ${pcs.length} PC` : ""}</div>}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10, marginTop: 8, flexWrap: "wrap" }}>
          <span className="nw-live" style={{ display: "inline-flex", alignItems: "center", gap: 8, color: "#22c55e", fontSize: 12, fontWeight: 800 }}>
            <span className="nw-dot" style={{ background: "#22c55e", color: "#22c55e" }} /> LIVE
            <span style={{ color: "#5b6b86", fontWeight: 600, fontFamily: "monospace" }}>{now.toLocaleTimeString("en-GB")}</span>
          </span>
          <span className="nw-pill" style={{ background: isLive ? "rgba(34,197,94,.14)" : "rgba(245,158,11,.14)", color: isLive ? "#34d399" : "#fbbf24", border: `1px solid ${isLive ? "rgba(34,197,94,.3)" : "rgba(245,158,11,.3)"}` }}>
            {isLive ? `LIVE SNMP · updated ${snap.generated_at || "now"}` : "DEMO data — SNMP wiring pending"}
          </span>
        </div>
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 18 }}>
        {KPIS.map((k, i) => (
          <div key={i} className="nw-kpi" style={{ "--ac": k.ac }}>
            <div style={{ fontSize: 26, fontWeight: 800, color: "#f1f5f9", lineHeight: 1.05 }}>{k.val}</div>
            <div style={{ fontSize: 11, color: "#7c8aa5", marginTop: 3, fontWeight: 600 }}>{k.lab}</div>
            {k.bar != null
              ? <div className="nw-bar" style={{ marginTop: 8 }}><span style={{ width: `${k.bar}%`, background: k.barCol }} /></div>
              : <div style={{ fontSize: 11, color: k.noteCol, marginTop: 6, fontWeight: 700 }}>{k.note}</div>}
          </div>
        ))}
      </div>

      {editable && (
        <div style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
          {TABS.map(([k, ic, label]) => (
            <button key={k} className={`nw-tab${view === k ? " on" : ""}`} onClick={() => setView(k)}>
              <span style={{ opacity: .8 }}>{ic}</span> {label}
            </button>
          ))}
        </div>
      )}

      {view === "floor"    && <FloorPlan devices={devices} cables={cables} token={token} reload={load} editable={editable} live={isLive} editingRef={editingRef} />}
      {view === "topology" && <Topology devices={switches} cables={cables} sel={sel} setSel={setSel} selDev={selDev} />}
      {view === "wire"     && <WireRoute devices={switches} cables={cables} />}
      {view === "registry" && <Registry devices={switches} cables={cables} />}
      {view === "alerts"   && <AlertsView alerts={alerts} />}
      {view === "conflicts" && <ConflictsView token={token} />}
    </div>
  );
}

const DEMO_ALERTS = [
  { sev: "warn", dev: "demo", msg: "DEMO data — backend SNMP not reachable", t: "—" },
];
function deriveAlerts(devices) {
  const out = [];
  for (const d of devices) {
    if (d.status === "down") out.push({ sev: "down", dev: d.name, msg: `Switch unreachable (${d.ip || "?"})`, t: "live" });
    else if (d.status === "warn") out.push({ sev: "warn", dev: d.name, msg: `PoE ${d.poe || 0} / ${d.poe_budget || POE_BUDGET} W — near budget`, t: "live" });
  }
  return out;
}

// ── IP-Conflict scanner view (2026-08-01) ─────────────────────────────
// Config-level (same IP on 2+ configured devices) + live wire-level (an IP
// answering on 2+ MACs, or an IP whose MAC keeps flipping = ARP flux).
function ConflictsView({ token }) {
  const [data, setData]         = useState(null);
  const [scanning, setScanning] = useState(false);
  const [err, setErr]           = useState(null);
  const scan = useCallback(async () => {
    setScanning(true); setErr(null);
    try { setData(await api.get("/api/network/ip-conflicts", token)); }
    catch (e) { setErr(e.message || String(e)); }
    finally { setScanning(false); }
  }, [token]);
  useEffect(() => { scan(); }, [scan]);

  const conflicts = data?.conflicts || [];
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <button onClick={scan} disabled={scanning} className="nw-tab"
                style={{ cursor: "pointer", borderColor: "#1d4ed8", color: "#93c5fd" }}
                title="Re-scan configs + live ARP table for IP conflicts">
          🔍 {scanning ? "scanning…" : "Scan IP conflicts"}
        </button>
        {data && (
          <span style={{ fontSize: 12, color: "#7c8aa5", fontWeight: 700 }}>
            <b style={{ color: data.count ? "#f87171" : "#22c55e", fontSize: 15 }}>{data.count}</b> conflict(s)
            {" · "}config {data.config} · live {data.live} · flux {data.flux}
          </span>
        )}
      </div>
      {err && <div className="nw-card" style={{ padding: 16, color: "#fca5a5", borderLeft: "4px solid #ef4444" }}>Scan failed: {err}</div>}
      {!err && data && !conflicts.length &&
        <div className="nw-card" style={{ padding: 24, color: "#22c55e" }}>No IP conflicts found ✓ — every device has a unique address.</div>}
      {conflicts.map((c, i) => {
        const col   = SC[c.sev] || SC.warn;
        const badge = c.type === "config" ? "CONFIG" : c.type === "live" ? "LIVE" : "FLUX";
        return (
          <div key={i} className="nw-card" style={{ padding: "13px 16px", borderLeft: `4px solid ${col}`, display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <span style={{ fontSize: 10, fontWeight: 800, padding: "3px 8px", borderRadius: 6, background: `${col}22`, color: col, minWidth: 54, textAlign: "center" }}>{badge}</span>
            <span style={{ fontWeight: 800, color: "#f8fafc", fontFamily: "monospace", minWidth: 118 }}>{c.ip}</span>
            <span style={{ color: "#aab8d0", fontSize: 13 }}>{c.msg}</span>
          </div>
        );
      })}
      <div style={{ fontSize: 11, color: "#5b6b86", marginTop: 4, lineHeight: 1.6 }}>
        <b style={{ color: "#fbbf24" }}>CONFIG</b> = same IP set on 2+ devices in the config ·
        <b style={{ color: "#f87171" }}> LIVE</b> = IP answering on 2+ MACs right now ·
        <b style={{ color: "#fbbf24" }}> FLUX</b> = IP's MAC changed within 15 min (two devices fighting for one address).
        Read-only — nothing is changed on the network.
      </div>
    </div>
  );
}

function StatusPill({ s }) {
  const c = SC[s] || SC.unconfigured;
  return <span className="nw-pill" style={{ background: c + "1f", color: c }}>
    <span style={{ width: 6, height: 6, borderRadius: 3, background: c }} />{(s || "").toUpperCase()}
  </span>;
}

/* ════════════════════════════════════════════════════════════════════
 * FLOOR PLAN — fully editable canvas (add/move/edit/delete nodes + cables)
 * ════════════════════════════════════════════════════════════════════ */
const FLOOR_SCALE_EDIT = 0.82;
// 2026-08-11 — canvas size is now RESIZABLE (see the W/H controls in the
// toolbar).  These stay as the defaults / reset target.  The plant keeps adding
// switches and areas and 1340x800 had run out of room, so nodes were being
// stacked on top of each other with nowhere to drop them.
// Node coordinates are stored absolute, so growing the canvas never moves
// anything that is already placed — it only adds empty space.
const CANVAS_W_DEF = 1340, CANVAS_H_DEF = 800;
const CANVAS_MIN_W = 800,  CANVAS_MAX_W = 6000;
const CANVAS_MIN_H = 500,  CANVAS_MAX_H = 4000;
const CANVAS_LS_KEY = "nw_canvas_size";
const SWW = 134, SWH = 50;
const AREA_COLORS = ["#16233c", "#1b2b18", "#2b1d2e", "#2a2412", "#13283a", "#241327", "#1d2233"];

// Optional one-click plant-room template (areas the user can then edit/move/delete)
const ROOM_TEMPLATE = [
  { x: 60,  y: 250, w: 130, h: 180, name: "TOOL ROOM" },
  { x: 210, y: 250, w: 300, h: 180, name: "STORE MATERIAL" },
  { x: 530, y: 250, w: 70,  h: 180, name: "GANGWAY" },
  { x: 620, y: 250, w: 90,  h: 60,  name: "TEA AREA" },
  { x: 620, y: 320, w: 90,  h: 110, name: "SITTING AREA" },
  { x: 60,  y: 470, w: 1180,h: 26,  name: "GANGWAY" },
];

function FloorPlan({ devices, cables, token, reload, editable = true, live = true, editingRef }) {
  const switches = devices.filter(d => (d.kind || "switch") !== "area");
  const areas    = devices.filter(d => d.kind === "area");

  const [mode, setMode]      = useState("select");   // "select" | "connect"
  const [sel, setSel]        = useState(null);
  // 2026-07-15 — multi-select: a Set of device ids selected together so the
  // user can relocate many areas/switches in ONE drag instead of one-by-one.
  // Build it via the "Select all" button or Shift+click; drag any member to
  // move the whole group; plain click / Esc clears it.
  const [selSet, setSelSet]  = useState(() => new Set());
  const [form, setForm]      = useState(null);
  const [cableForm, setCableForm] = useState(null);  // editing a cable (number/ports)
  const [cFrom, setCFrom]    = useState(null);
  const [livePos, setLive]   = useState(null);       // {id,x,y} during drag
  const [busy, setBusy]      = useState(false);
  const [disc, setDisc]      = useState(null);       // discovered devices (network scan)
  const [scanning, setScanning] = useState(false);

  const canvasRef = useRef(null);
  const wrapRef   = useRef(null);
  const dragRef   = useRef(null);
  const devRef    = useRef(devices); devRef.current = devices;
  const selRef    = useRef(selSet);  selRef.current = selSet;
  const modeRef   = useRef(mode);    modeRef.current = mode;
  const connectRef = useRef(null);
  const editRef    = useRef(null);
  // Fresh mirror of `live` — the drag mouse-up listener is bound once at mount
  // (empty deps) and closes over putDevices, which closed over the mount-time
  // `live` (false while snap was still loading).  Reading liveRef.current keeps
  // the save-guard honest after data loads, so a drag actually persists.
  const liveRef    = useRef(live);   liveRef.current = live;

  // View screen: scale the canvas to fill the full width (no drag here).
  // Builder keeps the fixed edit scale so drag math stays simple.
  // Persisted so a resized floor plan survives a reload / other tabs.
  const [canvasSize, setCanvasSize] = useState(() => {
    try {
      const raw = JSON.parse(localStorage.getItem(CANVAS_LS_KEY) || "null");
      if (raw && Number(raw.w) && Number(raw.h)) {
        return {
          w: Math.min(CANVAS_MAX_W, Math.max(CANVAS_MIN_W, Number(raw.w))),
          h: Math.min(CANVAS_MAX_H, Math.max(CANVAS_MIN_H, Number(raw.h))),
        };
      }
    } catch { /* corrupt entry — fall back to the default */ }
    return { w: CANVAS_W_DEF, h: CANVAS_H_DEF };
  });
  const CANVAS_W = canvasSize.w, CANVAS_H = canvasSize.h;
  const resizeCanvas = (dw, dh) => setCanvasSize(p => {
    const n = {
      w: Math.min(CANVAS_MAX_W, Math.max(CANVAS_MIN_W, p.w + dw)),
      h: Math.min(CANVAS_MAX_H, Math.max(CANVAS_MIN_H, p.h + dh)),
    };
    try { localStorage.setItem(CANVAS_LS_KEY, JSON.stringify(n)); } catch { /* private mode */ }
    return n;
  });
  const setCanvasExact = (w, h) => setCanvasSize(() => {
    const n = {
      w: Math.min(CANVAS_MAX_W, Math.max(CANVAS_MIN_W, Number(w) || CANVAS_W_DEF)),
      h: Math.min(CANVAS_MAX_H, Math.max(CANVAS_MIN_H, Number(h) || CANVAS_H_DEF)),
    };
    try { localStorage.setItem(CANVAS_LS_KEY, JSON.stringify(n)); } catch { /* private mode */ }
    return n;
  });

  // View mode fits the FULL content, not just the configured canvas: nodes can
  // sit beyond CANVAS_W (a resized / re-edited plan), so fitting to CANVAS_W
  // would scale the plan UP and overflow the screen (nodes fall off both edges,
  // the header legend gets clipped). Fit to the real content edge (max node
  // right / bottom) so the whole topology lands on one screen.
  let _cw = CANVAS_W, _ch = CANVAS_H;
  for (const _d of devices) {
    const _w = _d.kind === "area" ? (Number(_d.w) || 200) : (Number(_d.w) || SWW);
    const _h = _d.kind === "area" ? (Number(_d.h) || 120) : (Number(_d.h) || SWH);
    _cw = Math.max(_cw, (Number(_d.x) || 0) + _w + 40);
    _ch = Math.max(_ch, (Number(_d.y) || 0) + _h + 40);
  }
  const contentW = _cw, contentH = _ch;

  const [viewScale, setViewScale] = useState(FLOOR_SCALE_EDIT);
  // Fit the rendered canvas to the container width in BOTH modes so it fills
  // the screen instead of leaving empty space on the right. Edit mode fits the
  // configured canvas (CANVAS_W); view mode fits the real content edge.
  const fitW = editable ? CANVAS_W : contentW;
  useEffect(() => {
    const el = wrapRef.current; if (!el) return;
    const upd = () => setViewScale(Math.min(1.7, Math.max(0.35, (el.clientWidth - 30) / fitW)));
    upd();
    const ro = new ResizeObserver(upd); ro.observe(el);
    window.addEventListener("resize", upd);
    return () => { ro.disconnect(); window.removeEventListener("resize", upd); };
  }, [fitW]);   // re-fit when the canvas OR its content grows
  const FLOOR_SCALE = viewScale;
  const RENDER_W = editable ? CANVAS_W : contentW;
  const RENDER_H = editable ? CANVAS_H : contentH;

  // livePos is a map { id: {x,y} } holding the live position of EVERY node
  // being dragged this gesture (1 for a single drag, N for a group drag).
  const nx = (d) => (livePos && livePos[d.id]) ? livePos[d.id].x : (d.x ?? 40);
  const ny = (d) => (livePos && livePos[d.id]) ? livePos[d.id].y : (d.y ?? 40);
  // Switches carry an optional per-node w/h too (resizable like areas); fall
  // back to the default SWW×SWH chip size when unset.
  const dimsOf  = (d) => d.kind === "area" ? [Number(d.w) || 200, Number(d.h) || 120] : [Number(d.w) || SWW, Number(d.h) || SWH];
  const centerOf = (d) => { const [w, h] = dimsOf(d); return [nx(d) + w / 2, ny(d) + h / 2]; };
  const byIdLocal = (id) => devices.find(d => d.id === id);
  const statusColor = (d) => d.status === "down" ? SC.down : d.status === "warn" ? SC.warn : d.status === "up" ? SC.up : SC.unconfigured;

  // ── persistence ──
  const putDevices = async (next) => {
    // Guard: never save while showing DEMO/offline data — the whole-registry
    // replace would overwrite the real switch list with demo devices.
    if (!liveRef.current) { alert("Live network data load nahi hua (demo/offline) — abhi edit save nahi ho sakta. Page refresh karo."); return; }
    setBusy(true);
    try { await api.put("/api/network/devices", { devices: next }, token); await reload(); }
    catch (e) { alert("Save failed: " + (e.message || e)); }
    finally { setBusy(false); }
  };
  const putCables = async (next) => {
    if (!liveRef.current) { alert("Live network data load nahi hua (demo/offline) — abhi save nahi ho sakta. Page refresh karo."); return; }
    setBusy(true);
    try { await api.put("/api/network/cables", { cables: next }, token); await reload(); }
    catch (e) { alert("Cable save failed: " + (e.message || e)); }
    finally { setBusy(false); }
  };

  // ── editor ──
  const openEditor = (id) => {
    const d = devRef.current.find(x => x.id === id); if (!d) return;
    setSel(id);
    setForm(d.kind === "area"
      ? { id: d.id, kind: "area", name: d.name || "AREA", w: d.w || 220, h: d.h || 130, color: d.color || AREA_COLORS[0] }
      : d.kind === "pc"
      ? { id: d.id, kind: "pc", name: d.name || "PC", ip: d.ip || "", mac: d.mac || "", note: d.note || "" }
      : { id: d.id, kind: "switch", name: d.name || d.id, ip: d.ip || "", community: d.community || "public",
          poe_budget: d.poe_budget || POE_BUDGET, vlan: d.vlan || "", zone: d.zone || "",
          w: d.w || SWW, h: d.h || SWH,
          portmap: { ...(d.portmap || {}) }, _ports: d.ports || [] });
  };
  editRef.current = openEditor;

  // ── connect ──
  const handleConnect = (id) => {
    const d = byIdLocal(id);
    if (d && d.kind === "area") { setCFrom(null); return; }     // only switches connect
    if (!cFrom) { setCFrom(id); return; }
    if (cFrom === id) { setCFrom(null); return; }
    if (cables.some(c => (c.from === cFrom && c.to === id) || (c.from === id && c.to === cFrom))) { setCFrom(null); return; }
    const used = new Set((cables || []).map(c => String(c.id)));   // collision-proof id
    let cid = "C-" + String(Date.now()).slice(-5);
    while (used.has(cid)) cid += Math.floor(Math.random() * 10);
    const nc = { id: cid, from: cFrom, to: id, fromPort: "", toPort: "", media: "CAT-7", len: 0 };
    setCFrom(null);
    putCables([...cables, nc]).then(() => openCable(nc));   // open cable editor to set its number/ports
  };
  connectRef.current = handleConnect;

  // ── drag (mount-once listeners; read live state via refs) ──
  useEffect(() => {
    const move = (e) => {
      const dr = dragRef.current; if (!dr) return;
      const rect = canvasRef.current && canvasRef.current.getBoundingClientRect(); if (!rect) return;
      // Move EVERY node in the group by the same pointer delta (in canvas units)
      // from where each started, so their relative layout is preserved.
      const ddx = (e.clientX - rect.left) / FLOOR_SCALE - dr.px;
      const ddy = (e.clientY - rect.top) / FLOOR_SCALE - dr.py;
      if (!dr.moved && (Math.abs(e.clientX - dr.sx) > 3 || Math.abs(e.clientY - dr.sy) > 3)) dr.moved = true;
      const pos = {};
      dr.ids.forEach(id => {
        const o = dr.orig[id]; if (!o) return;
        pos[id] = { x: Math.max(0, Math.round(o.x + ddx)), y: Math.max(0, Math.round(o.y + ddy)) };
      });
      dr.pos = pos;
      setLive(pos);
    };
    const up = () => {
      const dr = dragRef.current; if (!dr) return;
      dragRef.current = null; setLive(null);
      if (dr.moved) {
        putDevices(devRef.current.map(d => dr.pos[d.id] ? { ...d, x: dr.pos[d.id].x, y: dr.pos[d.id].y } : d));
      } else {
        // A click (no drag): plain click clears any multi-selection and
        // opens/connects the one node; Shift was handled in startDrag.
        setSelSet(new Set());
        if (modeRef.current === "connect") connectRef.current(dr.ids[0]);
        else editRef.current(dr.ids[0]);
      }
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
  }, []);   // eslint-disable-line

  // Esc clears the multi-selection.
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") setSelSet(new Set()); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Pause the parent's 15s poll while an editor form is open — otherwise a
  // refresh mid-edit swaps `devices` and the next save persists a stale array.
  useEffect(() => {
    if (editingRef) editingRef.current = !!(form || cableForm);
    return () => { if (editingRef) editingRef.current = false; };
  }, [form, cableForm, editingRef]);

  const startDrag = (e, d) => {
    if (e.button !== 0) return;
    e.preventDefault(); e.stopPropagation();
    // Shift+click toggles this node in the multi-selection (no drag/edit).
    if (e.shiftKey) {
      setSelSet(prev => { const n = new Set(prev); n.has(d.id) ? n.delete(d.id) : n.add(d.id); return n; });
      return;
    }
    const rect = canvasRef.current.getBoundingClientRect();
    // Group = the current multi-selection IF this node is part of it (>1),
    // else just this one node. Capture each member's start position.
    const curSel = selRef.current;
    const ids = (curSel.has(d.id) && curSel.size > 1) ? [...curSel] : [d.id];
    const orig = {};
    ids.forEach(id => { const nd = devRef.current.find(x => x.id === id); if (nd) orig[id] = { x: nd.x ?? 40, y: nd.y ?? 40 }; });
    dragRef.current = {
      ids, orig, pos: { ...orig },
      px: (e.clientX - rect.left) / FLOOR_SCALE,
      py: (e.clientY - rect.top) / FLOOR_SCALE,
      sx: e.clientX, sy: e.clientY, moved: false,
    };
  };

  // ── add / template / clear ──
  const addSwitch = async () => {
    const id = "sw-" + Date.now().toString().slice(-6);
    await putDevices([...devRef.current, { id, kind: "switch", x: 60, y: 60, name: "NEW SWITCH", ip: "", community: "public", poe_budget: POE_BUDGET, vlan: "", zone: "" }]);
    setSel(id); setForm({ id, kind: "switch", name: "NEW SWITCH", ip: "", community: "public", poe_budget: POE_BUDGET, vlan: "", zone: "" });
  };
  const addArea = async () => {
    const id = "area-" + Date.now().toString().slice(-6);
    await putDevices([...devRef.current, { id, kind: "area", x: 90, y: 90, w: 240, h: 150, name: "AREA", color: AREA_COLORS[0] }]);
    setSel(id); setForm({ id, kind: "area", name: "AREA", w: 240, h: 150, color: AREA_COLORS[0] });
  };
  const addPC = async () => {
    const id = "pc-" + Date.now().toString().slice(-6);
    await putDevices([...devRef.current, { id, kind: "pc", x: 70, y: 70, name: "PC", ip: "", mac: "" }]);
    setSel(id); setForm({ id, kind: "pc", name: "PC", ip: "", mac: "", note: "" });
  };
  const loadRooms = async () => {
    if (areas.length && !confirm("Add the plant-room template as editable areas?")) return;
    const keep = devRef.current.filter(d => !(d.kind === "area" && String(d.id).startsWith("room-")));
    const rooms = ROOM_TEMPLATE.map((r, i) => ({ id: "room-" + i, kind: "area", x: r.x, y: r.y, w: r.w, h: r.h, name: r.name, color: AREA_COLORS[i % AREA_COLORS.length] }));
    await putDevices([...keep, ...rooms]);
  };
  const clearAll = async () => {
    if (!confirm("Remove ALL switches, areas and cables and start with a blank canvas?\n(This also clears saved IPs — you'll re-add them.)")) return;
    await putCables([]);
    await putDevices([]);
    setSel(null); setForm(null);
  };

  // ── Discover: scan the network, list devices (name + IP auto) ──
  const existsIp = (ip) => devices.some(d => (d.ip || "") === ip);
  const runDiscover = async () => {
    setScanning(true);
    try { const r = await api.get("/api/network/discover", token); setDisc(r.devices || []); }
    catch (e) { alert("Discover failed: " + (e.message || e)); }
    finally { setScanning(false); }
  };
  const addDiscovered = async (dev) => {
    if (existsIp(dev.ip)) return;
    const n = devices.length;
    const id = (dev.kind === "switch" ? "sw-" : "pc-") + dev.ip.replace(/\./g, "-");
    const base = { id, kind: dev.kind, x: 60 + (n % 8) * 150, y: 300 + Math.floor(n / 8) * 70, name: dev.name || dev.ip, ip: dev.ip };
    const node = dev.kind === "switch"
      ? { ...base, community: "public", poe_budget: POE_BUDGET, vlan: "", zone: "" }
      : { ...base, mac: dev.mac || "" };
    await putDevices([...devices, node]);
  };

  // ── save / delete ──
  // Drop transient live/SNMP fields so they are NEVER baked into the config
  // JSON (they used to freeze a stale "up · PoE 95%" into the saved file).
  const _LIVE = new Set(["status","live","poe","portsUp","portsTotal","ports","snmp","uptime","powered","learned","_ports","descr","demo","_stale","_err"]);
  const configOnly = (d) => { const o = {}; for (const k in (d || {})) if (!_LIVE.has(k)) o[k] = d[k]; return o; };
  const numOr = (v, dflt) => { const n = Number(v); return Number.isFinite(n) ? n : dflt; };   // keeps a legit 0
  const save = async () => {
    if (!form) return;
    const prev = configOnly(devRef.current.find(d => d.id === form.id));
    const merged = form.kind === "area"
      ? { ...prev, id: form.id, kind: "area", name: form.name || "AREA", w: numOr(form.w, 220), h: numOr(form.h, 130), color: form.color || AREA_COLORS[0] }
      : form.kind === "pc"
      ? { ...prev, id: form.id, kind: "pc", name: form.name || "PC", ip: (form.ip || "").trim(), mac: (form.mac || "").trim(), note: form.note || "" }
      : { ...prev, id: form.id, kind: "switch", name: form.name || form.id, ip: (form.ip || "").trim(),
          community: form.community || "public", poe_budget: numOr(form.poe_budget, POE_BUDGET),
          vlan: form.vlan || "", zone: form.zone || "",
          w: numOr(form.w, SWW), h: numOr(form.h, SWH),
          portmap: Object.fromEntries(Object.entries(form.portmap || {})
                     .filter(([, v]) => v && ((v.dev || "").trim() || (v.ip || "").trim()))
                     .map(([k, v]) => [String(k), { dev: (v.dev || "").trim(), ip: (v.ip || "").trim() }])) };   // string keys
    await putDevices(devRef.current.filter(d => d.id !== form.id).concat(merged));
  };
  const removeNode = async () => {
    if (!form) return;
    if (!confirm("Delete this " + (form.kind === "area" ? "area" : "switch") + "?")) return;
    const id = form.id;
    await putDevices(devRef.current.filter(d => d.id !== id));
    if (cables.some(c => c.from === id || c.to === id))
      await putCables(cables.filter(c => c.from !== id && c.to !== id));
    setSel(null); setForm(null);
  };
  const removeCable = (cid) => { if (confirm("Remove this cable?")) putCables(cables.filter(c => c.id !== cid)); };

  // ── cable editor (number + A/B ports + media/length) ──
  const openCable = (cb) => {
    setForm(null); setSel(null);
    setCableForm({ _orig: cb.id, id: cb.id, fromPort: cb.fromPort || "", toPort: cb.toPort || "", media: cb.media || "CAT-7", len: cb.len || "" });
  };
  const saveCable = async () => {
    const f = cableForm; if (!f) return;
    if (f.id !== f._orig && cables.some(c => c.id === f.id)) { alert("That cable number already exists."); return; }
    const next = cables.map(c => c.id === f._orig
      ? { ...c, id: f.id || c.id, fromPort: f.fromPort, toPort: f.toPort, media: f.media || "CAT-7", len: Number(f.len) || 0 }
      : c);
    await putCables(next); setCableForm({ ...f, _orig: f.id || f._orig });
  };
  const deleteCable = async () => {
    if (!cableForm) return;
    if (!confirm("Delete this cable?")) return;
    await putCables(cables.filter(c => c.id !== cableForm._orig));
    setCableForm(null);
  };

  const myCables = form && form.kind === "switch" ? cables.filter(c => c.from === form.id || c.to === form.id) : [];

  return (
    <div>
      {/* toolbar (edit mode only) */}
      {editable && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
          <button onClick={addSwitch} className="nw-tab" style={{ cursor: "pointer" }}>🖧 + Switch</button>
          <button onClick={addPC} className="nw-tab" style={{ cursor: "pointer" }}>🖥 + PC</button>
          <button onClick={addArea} className="nw-tab" style={{ cursor: "pointer" }}>▢ + Area</button>
          <button onClick={() => { setMode(m => m === "connect" ? "select" : "connect"); setCFrom(null); }} className={`nw-tab${mode === "connect" ? " on" : ""}`} style={{ cursor: "pointer" }}>
            🔌 {mode === "connect" ? (cFrom ? "pick 2nd switch…" : "pick 1st switch") : "Connect cables"}
          </button>
          <button onClick={runDiscover} disabled={scanning} className="nw-tab" style={{ cursor: "pointer", borderColor: "#1d4ed8", color: "#93c5fd" }} title="Scan the network — auto-find devices (name + IP)">🔍 {scanning ? "scanning…" : "Discover"}</button>
          <button onClick={loadRooms} className="nw-tab" style={{ cursor: "pointer" }} title="Seed the plant rooms as editable areas">🏭 Load rooms</button>
          <button onClick={() => setSelSet(new Set(devRef.current.map(d => d.id)))} className="nw-tab" style={{ cursor: "pointer" }} title="Select EVERY area + switch — then drag any one to move them all together">▣ Select all</button>
          <button onClick={() => setSelSet(new Set(areas.map(a => a.id)))} className="nw-tab" style={{ cursor: "pointer" }} title="Select all areas only">▢ Select areas</button>
          {selSet.size > 0 && (
            <button onClick={() => setSelSet(new Set())} className="nw-tab" style={{ cursor: "pointer", borderColor: "#16a34a", color: "#86efac" }} title="Clear selection (or press Esc)">✓ {selSet.size} selected · clear</button>
          )}
          {/* 2026-08-11 — canvas size controls.  The floor plan was locked at
              1340x800 and the plant kept outgrowing it, so nodes had nowhere
              left to be dropped.  Absolute node coordinates mean growing the
              canvas only adds empty space — nothing already placed moves.
              Size is remembered in localStorage. */}
          {editable && (
            <span style={{ display:"inline-flex", alignItems:"center", gap:6,
                           marginLeft:6, padding:"4px 10px", borderRadius:9,
                           background:"#111c30", border:"1px solid #1e2a44",
                           fontSize:12, whiteSpace:"nowrap" }}
                  title="Floor-plan canvas size (px). Grow it when you run out of room.">
              <span style={{ color:"#94a3b8", fontWeight:700, letterSpacing:".04em" }}>CANVAS</span>
              <input type="number" value={CANVAS_W} step={100}
                     min={CANVAS_MIN_W} max={CANVAS_MAX_W}
                     onChange={(e) => setCanvasExact(e.target.value, CANVAS_H)}
                     style={{ width:66, background:"#0a1120", color:"#e2e8f0",
                              border:"1px solid #1e2a44", borderRadius:6,
                              padding:"3px 6px", fontSize:12 }}/>
              <span style={{ color:"#64748b" }}>×</span>
              <input type="number" value={CANVAS_H} step={100}
                     min={CANVAS_MIN_H} max={CANVAS_MAX_H}
                     onChange={(e) => setCanvasExact(CANVAS_W, e.target.value)}
                     style={{ width:60, background:"#0a1120", color:"#e2e8f0",
                              border:"1px solid #1e2a44", borderRadius:6,
                              padding:"3px 6px", fontSize:12 }}/>
              <button onClick={() => resizeCanvas(200, 0)} className="nw-tab"
                      style={{ cursor:"pointer", padding:"2px 7px", fontSize:12 }}
                      title="Wider (+200 px)">W+</button>
              <button onClick={() => resizeCanvas(-200, 0)} className="nw-tab"
                      style={{ cursor:"pointer", padding:"2px 7px", fontSize:12 }}
                      title="Narrower (-200 px)">W−</button>
              <button onClick={() => resizeCanvas(0, 200)} className="nw-tab"
                      style={{ cursor:"pointer", padding:"2px 7px", fontSize:12 }}
                      title="Taller (+200 px)">H+</button>
              <button onClick={() => resizeCanvas(0, -200)} className="nw-tab"
                      style={{ cursor:"pointer", padding:"2px 7px", fontSize:12 }}
                      title="Shorter (-200 px)">H−</button>
              <button onClick={() => setCanvasExact(CANVAS_W_DEF, CANVAS_H_DEF)}
                      className="nw-tab"
                      style={{ cursor:"pointer", padding:"2px 8px", fontSize:12 }}
                      title={`Reset to ${CANVAS_W_DEF}x${CANVAS_H_DEF}`}>Reset</button>
            </span>
          )}
          <button onClick={clearAll} className="nw-tab" style={{ cursor: "pointer", borderColor: "#7f1d1d", color: "#fca5a5" }}>🗑 Clear all</button>
          {busy && <span style={{ color: "#7c8aa5", fontSize: 12 }}>saving…</span>}
          <span style={{ marginLeft: "auto", fontSize: 11, color: "#5b6b86" }}>
            Drag = move · Click = edit · Shift+click = add to selection · drag a selected node = move the whole group
          </span>
        </div>
      )}

      <div className="nw-card" ref={wrapRef} style={{ padding: 14, overflow: "auto" }}>
        <div ref={canvasRef} style={{
          position: "relative", width: RENDER_W * FLOOR_SCALE, height: RENDER_H * FLOOR_SCALE, minWidth: editable ? 900 : 0,
          backgroundColor: "#0b1322",
          backgroundImage: "linear-gradient(#152138 1px,transparent 1px),linear-gradient(90deg,#152138 1px,transparent 1px)",
          backgroundSize: `${26 * FLOOR_SCALE}px ${26 * FLOOR_SCALE}px`,
          borderRadius: 10, border: "1px solid #1e2a44", userSelect: "none",
        }}>
          {/* areas (behind) */}
          {areas.map(d => {
            const [w, h] = dimsOf(d);
            return (
              <div key={d.id} {...(editable ? { onMouseDown: e => startDrag(e, d) } : {})}
                   style={{
                     position: "absolute", left: nx(d) * FLOOR_SCALE, top: ny(d) * FLOOR_SCALE, width: w * FLOOR_SCALE, height: h * FLOOR_SCALE,
                     background: (d.color || AREA_COLORS[0]) + "d9", border: `1.5px dashed ${selSet.has(d.id) ? "#22c55e" : sel === d.id ? "#3b82f6" : "#3a4a66"}`,
                     borderRadius: 8, cursor: editable ? "move" : "default", zIndex: 1, boxShadow: selSet.has(d.id) ? "0 0 0 2px #22c55e" : sel === d.id ? "0 0 0 2px #3b82f6" : "none",
                     display: "flex", alignItems: "flex-start", justifyContent: "center", paddingTop: 4, boxSizing: "border-box", overflow: "hidden",
                   }}>
                <span style={{ fontSize: 11, fontWeight: 800, color: "#aab8d0", letterSpacing: ".04em", textAlign: "center", whiteSpace: "normal", wordBreak: "break-word" }}>{d.name}</span>
              </div>
            );
          })}

          {/* cables (above areas, below switches) */}
          <svg width={RENDER_W * FLOOR_SCALE} height={RENDER_H * FLOOR_SCALE} style={{ position: "absolute", inset: 0, zIndex: 2, pointerEvents: "none" }}>
            {cables.map(cb => {
              const a = byIdLocal(cb.from), b = byIdLocal(cb.to);
              if (!a || !b) return null;
              const [ax, ay] = centerOf(a), [bx, by] = centerOf(b);
              const st = cableStatus(devices, cb);
              const col = SC[st] || "#3a4a66";
              return (
                <g key={cb.id} style={{ pointerEvents: editable ? "all" : "none", cursor: editable ? "pointer" : "default" }} onClick={editable ? () => openCable(cb) : undefined}>
                  <line x1={ax * FLOOR_SCALE} y1={ay * FLOOR_SCALE} x2={bx * FLOOR_SCALE} y2={by * FLOOR_SCALE} stroke="transparent" strokeWidth="12" />
                  <line x1={ax * FLOOR_SCALE} y1={ay * FLOOR_SCALE} x2={bx * FLOOR_SCALE} y2={by * FLOOR_SCALE} stroke={col} strokeWidth="2.5"
                        className={st === "up" ? "nw-flow" : ""} strokeLinecap="round"
                        strokeDasharray={(st === "down" || st === "unconfigured") ? "5 5" : undefined} />
                  <text x={(ax + bx) / 2 * FLOOR_SCALE} y={(ay + by) / 2 * FLOOR_SCALE - 3} textAnchor="middle"
                        fontSize="9" fontWeight="800" fill="#cbd5e1" stroke="#0b1322" strokeWidth="3" paintOrder="stroke"
                        style={{ pointerEvents: "none" }}>{cb.id}</text>
                </g>
              );
            })}
          </svg>

          {/* switch nodes (top) */}
          {switches.map(d => {
            const col = statusColor(d);
            const [sww, swh] = dimsOf(d);
            const poePct = d.poe != null && d.poe_budget ? Math.round((d.poe / d.poe_budget) * 100) : null;
            const isFrom = cFrom === d.id;
            return (
              <div key={d.id}
                   {...(editable ? { onMouseDown: e => startDrag(e, d) } : { onClick: () => setSel(s => s === d.id ? null : d.id) })}
                   title={`${d.name} ${d.ip || "(no ip)"}`}
                   style={{
                     position: "absolute", left: nx(d) * FLOOR_SCALE, top: ny(d) * FLOOR_SCALE, width: sww * FLOOR_SCALE, height: swh * FLOOR_SCALE,
                     zIndex: 3, boxSizing: "border-box", borderRadius: 9, padding: "5px 8px",
                     background: "#0f1a2e", cursor: editable ? (mode === "connect" ? "crosshair" : "move") : "pointer",
                     border: `1.5px solid ${selSet.has(d.id) ? "#22c55e" : isFrom ? "#3b82f6" : col}`,
                     boxShadow: selSet.has(d.id) ? "0 0 0 2px #22c55e, 0 0 16px #22c55e66" : (sel === d.id || isFrom) ? `0 0 0 2px ${isFrom ? "#3b82f6" : col}, 0 0 16px ${col}66` : `0 0 10px ${col}33`,
                     display: "flex", flexDirection: "column", justifyContent: "center",
                   }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span className={d.status === "up" ? "nw-live" : ""} style={{ display: "inline-flex" }}>
                    <span className="nw-dot" style={{ width: 8, height: 8, background: col, color: col }} />
                  </span>
                  <span style={{ fontSize: 12.5, fontWeight: 800, color: "#f1f6ff", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{d.kind === "pc" ? "🖥 " : ""}{d.name}</span>
                  {d.kind === "pc"
                    ? <span style={{ marginLeft: "auto", fontSize: 9, fontWeight: 800, color: "#a78bfa" }}>PC</span>
                    : (d.vlan && <span style={{ marginLeft: "auto", fontSize: 9, fontWeight: 700, color: "#64748b" }}>v{d.vlan}</span>)}
                </div>
                <div style={{ fontSize: 10, color: col, fontWeight: 700, marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {d.ip ? d.ip : "no ip — click to set"}{poePct != null ? ` · PoE ${poePct}%` : ""}{d.status === "down" ? " · offline" : (d.snmp === false && d.ip ? " · no SNMP" : "")}
                </div>
              </div>
            );
          })}

          {(switches.length + areas.length) === 0 && (
            <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 6, color: "#5b6b86", pointerEvents: "none" }}>
              <div style={{ fontSize: 16, fontWeight: 800, color: "#7c8aa5" }}>Blank canvas</div>
              <div style={{ fontSize: 13 }}>“+ Switch” / “+ Area” se apna plant banao · “🏭 Load rooms” se template lao</div>
            </div>
          )}
        </div>
        <Legend />
      </div>

      {/* discovered devices (network scan) */}
      {disc && (
        <div className="nw-card" style={{ marginTop: 14, padding: 18, overflowX: "auto" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
            <span style={{ fontSize: 15, fontWeight: 800, color: "#f8fafc" }}>🔍 Discovered · {disc.length}</span>
            <span style={{ fontSize: 11, color: "#5b6b86" }}>SNMP switches + ping/DNS hosts · “Add” → node banega (name + IP auto)</span>
            <button onClick={() => setDisc(null)} style={{ marginLeft: "auto", border: "none", background: "none", fontSize: 20, color: "#5b6b86", cursor: "pointer" }}>×</button>
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
            <thead><tr style={{ borderBottom: "1px solid #243150" }}>{["Type", "Name", "IP", "MAC", "Info", ""].map(h => <th key={h} style={thd()}>{h}</th>)}</tr></thead>
            <tbody>
              {disc.map(dv => {
                const on = existsIp(dv.ip);
                return (
                  <tr key={dv.ip} className="nw-row" style={{ borderBottom: "1px solid #18233c" }}>
                    <td style={tdd()}>{dv.kind === "switch" ? "🖧 switch" : "🖥 pc"}</td>
                    <td style={tdd(true)}>{dv.name}</td>
                    <td style={tdd(false, true)}>{dv.ip}</td>
                    <td style={tdd(false, true)}>{dv.mac || "—"}</td>
                    <td style={tdd()} title={dv.descr || ""}>{(dv.descr || "").slice(0, 36)}</td>
                    <td style={tdd()}>{on ? <span style={{ color: "#5b6b86" }}>on map</span> : <button onClick={() => addDiscovered(dv)} className="nw-tab" style={{ padding: "3px 12px", cursor: "pointer" }}>＋ Add</button>}</td>
                  </tr>
                );
              })}
              {disc.length === 0 && <tr><td colSpan={6} style={{ padding: 12, color: "#5b6b86" }}>Kuch nahi mila — subnet/community check karo.</td></tr>}
            </tbody>
          </table>
        </div>
      )}

      {/* editor */}
      {form && (
        <div className="nw-card" style={{ marginTop: 14, padding: 18 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
            <span style={{ fontSize: 15, fontWeight: 800, color: "#f8fafc" }}>
              {form.kind === "area" ? "▢ Area" : form.kind === "pc" ? "🖥 PC" : "🖧 Switch"} · {form.name || form.id}
            </span>
            <button onClick={() => { setSel(null); setForm(null); }} style={{ marginLeft: "auto", border: "none", background: "none", fontSize: 20, color: "#5b6b86", cursor: "pointer" }}>×</button>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(160px,1fr))", gap: 12 }}>
            <Field label="Name"><input style={inp} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></Field>
            {form.kind === "switch" ? <>
              <Field label="Mgmt IP"><input style={inp} value={form.ip} placeholder="192.168.10.x" onChange={e => setForm({ ...form, ip: e.target.value })} /></Field>
              <Field label="SNMP community"><input style={inp} value={form.community} onChange={e => setForm({ ...form, community: e.target.value })} /></Field>
              <Field label="PoE budget (W)"><input type="number" style={inp} value={form.poe_budget} onChange={e => setForm({ ...form, poe_budget: e.target.value })} /></Field>
              <Field label="VLAN"><input style={inp} value={form.vlan} placeholder="10" onChange={e => setForm({ ...form, vlan: e.target.value })} /></Field>
              <Field label="Zone"><input style={inp} value={form.zone} placeholder="SS-1" onChange={e => setForm({ ...form, zone: e.target.value })} /></Field>
              <Field label={`Width (default ${SWW})`}><input type="number" style={inp} value={form.w} onChange={e => setForm({ ...form, w: e.target.value })} /></Field>
              <Field label={`Height (default ${SWH})`}><input type="number" style={inp} value={form.h} onChange={e => setForm({ ...form, h: e.target.value })} /></Field>
            </> : form.kind === "pc" ? <>
              <Field label="IP"><input style={inp} value={form.ip} placeholder="192.168.10.x" onChange={e => setForm({ ...form, ip: e.target.value })} /></Field>
              <Field label="MAC (optional)"><input style={inp} value={form.mac} placeholder="aa:bb:cc:dd:ee:ff" onChange={e => setForm({ ...form, mac: e.target.value })} /></Field>
              <Field label="Note"><input style={inp} value={form.note} placeholder="owner / location" onChange={e => setForm({ ...form, note: e.target.value })} /></Field>
            </> : <>
              <Field label="Width"><input type="number" style={inp} value={form.w} onChange={e => setForm({ ...form, w: e.target.value })} /></Field>
              <Field label="Height"><input type="number" style={inp} value={form.h} onChange={e => setForm({ ...form, h: e.target.value })} /></Field>
              <Field label="Color">
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {AREA_COLORS.map(c => (
                    <span key={c} onClick={() => setForm({ ...form, color: c })} style={{ width: 22, height: 22, borderRadius: 6, background: c, cursor: "pointer", border: `2px solid ${form.color === c ? "#3b82f6" : "#243150"}` }} />
                  ))}
                </div>
              </Field>
            </>}
          </div>

          {form.kind === "switch" && myCables.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "#5b6b86", letterSpacing: ".06em", marginBottom: 6 }}>Cables on this switch</div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {myCables.map(c => {
                  const other = c.from === form.id ? c.to : c.from;
                  return (
                    <span key={c.id} style={{ display: "inline-flex", alignItems: "center", gap: 8, padding: "5px 10px", borderRadius: 8, background: "#0f1830", border: "1px solid #243150", fontSize: 12, color: "#cbd5e1" }}>
                      ↔ {devName(devices, other)}
                      <button onClick={() => removeCable(c.id)} style={{ border: "none", background: "none", color: "#fca5a5", cursor: "pointer", fontWeight: 800 }}>×</button>
                    </span>
                  );
                })}
              </div>
            </div>
          )}

          {form.kind === "switch" && (() => {
            const liveP = form._ports || [];
            const meta  = (n) => liveP.find(p => p.port === n) || {};
            const shown = [...new Set([...liveP.filter(p => p.up).map(p => p.port), ...Object.keys(form.portmap || {}).map(Number)])].sort((a, b) => a - b);
            const setPM = (n, k, v) => setForm(f => ({ ...f, portmap: { ...(f.portmap || {}), [n]: { ...((f.portmap || {})[n] || {}), [k]: v } } }));
            return (
              <div style={{ marginTop: 16 }}>
                <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "#5b6b86", letterSpacing: ".06em", marginBottom: 6 }}>
                  Port mapping — kis port pe kya device + IP (up/down + PoE auto from SNMP)
                </div>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                  <thead><tr style={{ borderBottom: "1px solid #243150" }}>{["Port", "Link", "PoE", "Device", "IP"].map(h => <th key={h} style={thd()}>{h}</th>)}</tr></thead>
                  <tbody>
                    {shown.map(n => {
                      const m = meta(n), pm = (form.portmap || {})[n] || {};
                      return (
                        <tr key={n} style={{ borderBottom: "1px solid #18233c" }}>
                          <td style={tdd(true, true)}>{n}{m.sfp ? " (SFP)" : ""}</td>
                          <td style={tdd()}>{m.up ? <span style={{ color: SC.up, fontWeight: 700 }}>up{m.speed ? ` ${m.speed}M` : ""}</span> : <span style={{ color: "#5b6b86" }}>down</span>}</td>
                          <td style={tdd()}>{m.powered ? "⚡" : (m.poe ? "PoE" : "—")}</td>
                          <td style={{ padding: "4px 6px" }}><input style={inp} value={pm.dev || ""} placeholder="device / camera" onChange={e => setPM(n, "dev", e.target.value)} /></td>
                          <td style={{ padding: "4px 6px" }}><input style={inp} value={pm.ip || ""} placeholder="192.168.10.x" onChange={e => setPM(n, "ip", e.target.value)} /></td>
                        </tr>
                      );
                    })}
                    {shown.length === 0 && <tr><td colSpan={5} style={{ padding: 10, color: "#5b6b86" }}>Abhi koi up port nahi — IP/community set + agle poll ke baad up ports yahan aa jayenge. Ya neeche se port number add karo.</td></tr>}
                  </tbody>
                </table>
                <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
                  <input id={"addport-" + form.id} style={{ ...inp, width: 90 }} placeholder="port #" />
                  <button className="nw-tab" style={{ padding: "4px 12px" }} onClick={() => { const v = document.getElementById("addport-" + form.id).value.trim(); const n = parseInt(v, 10); if (n) setForm(f => ({ ...f, portmap: { ...(f.portmap || {}), [n]: (f.portmap || {})[n] || { dev: "", ip: "" } } })); }}>＋ add port</button>
                  <span style={{ fontSize: 11, color: "#5b6b86" }}>down port bhi map karna ho to number daal ke add karo · Save se store hoga</span>
                </div>
              </div>
            );
          })()}

          <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
            <button onClick={save} disabled={busy} className="nw-tab on" style={{ cursor: "pointer" }}>💾 Save</button>
            <button onClick={removeNode} disabled={busy} className="nw-tab" style={{ borderColor: "#7f1d1d", color: "#fca5a5" }}>🗑 Delete</button>
            {form.kind === "switch" && <span style={{ fontSize: 11, color: "#5b6b86", alignSelf: "center" }}>IP + community set karte hi switch live SNMP se poll hone lagega.</span>}
            {form.kind === "pc" && <span style={{ fontSize: 11, color: "#5b6b86", alignSelf: "center" }}>IP set karoge to PC ICMP ping se up/down monitor hoga · switch port se Connect karo to us port pe ye PC dikhega.</span>}
          </div>
        </div>
      )}

      {/* cable editor (edit mode) — number + A/B ports + media/length */}
      {editable && cableForm && (() => {
        const orig = cables.find(c => c.id === cableForm._orig);
        return (
          <div className="nw-card" style={{ marginTop: 14, padding: 18 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
              <span style={{ fontSize: 15, fontWeight: 800, color: "#f8fafc" }}>
                🔌 Cable {orig ? `· ${devName(devices, orig.from)} ↔ ${devName(devices, orig.to)}` : ""}
              </span>
              <button onClick={() => setCableForm(null)} style={{ marginLeft: "auto", border: "none", background: "none", fontSize: 20, color: "#5b6b86", cursor: "pointer" }}>×</button>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 12 }}>
              <Field label="Cable number"><input style={inp} value={cableForm.id} placeholder="e.g. CAM-12" onChange={e => setCableForm({ ...cableForm, id: e.target.value })} /></Field>
              <Field label={`A-end port${orig ? " (" + devName(devices, orig.from) + ")" : ""}`}><input style={inp} value={cableForm.fromPort} placeholder="e.g. 3" onChange={e => setCableForm({ ...cableForm, fromPort: e.target.value })} /></Field>
              <Field label={`B-end port${orig ? " (" + devName(devices, orig.to) + ")" : ""}`}><input style={inp} value={cableForm.toPort} placeholder="e.g. 25" onChange={e => setCableForm({ ...cableForm, toPort: e.target.value })} /></Field>
              <Field label="Media"><input style={inp} value={cableForm.media} onChange={e => setCableForm({ ...cableForm, media: e.target.value })} /></Field>
              <Field label="Length (m)"><input type="number" style={inp} value={cableForm.len} onChange={e => setCableForm({ ...cableForm, len: e.target.value })} /></Field>
            </div>
            <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
              <button onClick={saveCable} disabled={busy} className="nw-tab on" style={{ cursor: "pointer" }}>💾 Save</button>
              <button onClick={deleteCable} disabled={busy} className="nw-tab" style={{ borderColor: "#7f1d1d", color: "#fca5a5" }}>🗑 Delete</button>
              <span style={{ fontSize: 11, color: "#5b6b86", alignSelf: "center" }}>Port set karoge to monitor me us port pe ye device + IP dikhega.</span>
            </div>
          </div>
        );
      })()}

      {/* view mode — click a switch → port detail */}
      {!editable && sel && (() => {
        const d = switches.find(x => x.id === sel);
        if (!d) return null;
        return d.kind === "pc"
          ? <PcDetail d={d} devices={devices} cables={cables} onClose={() => setSel(null)} />
          : <SwitchDetail d={d} devices={devices} cables={cables} onClose={() => setSel(null)} />;
      })()}
    </div>
  );
}

/* Switch detail with live per-port grid (view/monitor mode) */
function SwitchDetail({ d, devices, cables, onClose }) {
  const c = sc(d);
  const ports = Array.isArray(d.ports) ? d.ports : [];
  const total = ports.length || d.portsTotal || 28;
  const grid = ports.length ? ports : Array.from({ length: total }, (_, i) => ({ port: i + 1, up: false, sfp: i + 1 >= 25 }));
  const upCount = grid.filter(p => p.up).length;
  const poeCount = grid.filter(p => p.poe).length;
  const poweredCount = grid.filter(p => p.powered).length;

  // port -> connected device.  Source 1 = manual per-port map (Builder),
  // source 2 = cable mapping (switch-to-switch links with from/to ports).
  const conn = {};
  for (const cb of cables) {
    if (cb.from === d.id && cb.fromPort) conn[String(cb.fromPort)] = { cable: cb.id, dev: byId(devices, cb.to) };
    if (cb.to === d.id && cb.toPort) conn[String(cb.toPort)] = { cable: cb.id, dev: byId(devices, cb.from) };
  }
  const learnedByPort = {};
  for (const p of grid) if (p.learned && p.learned.length) learnedByPort[p.port] = p.learned;
  const pm = d.portmap || {};
  const info = (n) => {
    const m = pm[n] || pm[String(n)];
    const cc = conn[String(n)];
    if (m && (m.dev || m.ip)) return { name: m.dev || "device", ip: m.ip || "", cable: cc ? cc.cable : "" };
    if (cc) return { name: cc.dev ? cc.dev.name : "—", ip: cc.dev ? (cc.dev.ip || "") : "", cable: cc.cable };
    const lr = learnedByPort[n];
    if (lr && lr.length) {
      if (lr.length === 1) return { name: lr[0].host || "🔍 auto", ip: lr[0].ip || lr[0].mac, cable: cc ? cc.cable : "", auto: true };
      return { name: `🔍 ${lr.length} devices`, ip: "uplink", cable: cc ? cc.cable : "", auto: true };
    }
    return null;
  };
  const upPorts = grid.filter(p => p.up);

  return (
    <div className="nw-card" style={{ marginTop: 14, padding: 18, borderLeft: `4px solid ${c}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12, flexWrap: "wrap" }}>
        <span style={{ fontSize: 17, fontWeight: 800, color: "#f8fafc" }}>{d.name}</span>
        <StatusPill s={d.status} />
        <span style={{ fontSize: 12, color: "#94a3b8" }}>{d.ip || "no IP"}{d.vlan ? ` · vlan ${d.vlan}` : ""}{d.zone ? ` · ${d.zone}` : ""}</span>
        <button onClick={onClose} style={{ marginLeft: "auto", border: "none", background: "none", fontSize: 20, color: "#5b6b86", cursor: "pointer" }}>×</button>
      </div>

      <div style={{ display: "flex", gap: 22, flexWrap: "wrap", marginBottom: 14 }}>
        {[["Ports up", `${upCount} / ${total}`, upCount ? SC.up : "#94a3b8"],
          ["PoE ports", `${poeCount}`, SC.warn],
          ["Delivering PoE", `${poweredCount}`, poweredCount ? SC.up : "#94a3b8"]].map(([k, v, col]) => (
          <div key={k}>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "#5b6b86", letterSpacing: ".06em" }}>{k}</div>
            <div style={{ fontSize: 18, fontWeight: 800, color: col, marginTop: 2 }}>{v}</div>
          </div>
        ))}
      </div>

      {/* port grid */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginBottom: 6, maxWidth: 760 }}>
        {grid.map(p => {
          const ci = info(p.port);
          return (
            <div key={p.port} title={`Port ${p.port}${p.sfp ? " (SFP)" : ""} · ${p.up ? "UP" : "down"}${p.speed ? ` · ${p.speed}M` : ""}${p.poe ? " · PoE" : ""}${p.powered ? " (powered)" : ""}${ci ? ` · ${ci.name} ${ci.ip || ""}` : ""}`}
                 style={{
                   width: 36, height: 32, borderRadius: 6, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
                   fontSize: 10, fontWeight: 800,
                   background: p.up ? "rgba(34,197,94,.16)" : "#0e1626",
                   color: p.up ? "#86efac" : "#5b6b86",
                   border: `1px solid ${ci ? "#3b82f6" : (p.up ? SC.up : "#243150")}`,
                 }}>
              <span>{p.port}</span>
              <span style={{ fontSize: 7, color: p.sfp ? "#a78bfa" : (p.powered ? "#fbbf24" : (p.poe ? "#64748b" : "#3a4a66")) }}>
                {p.sfp ? "SFP" : (p.powered ? "⚡" : (p.poe ? "poe" : "—"))}
              </span>
            </div>
          );
        })}
      </div>

      {/* up-ports detail */}
      <div style={{ fontSize: 12, fontWeight: 800, color: "#f1f5f9", marginBottom: 8 }}>Active ports ({upCount})</div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
        <thead><tr style={{ borderBottom: "1px solid #243150" }}>
          {["Port", "Speed", "PoE", "Connected device", "IP", "Cable"].map(h => <th key={h} style={thd()}>{h}</th>)}
        </tr></thead>
        <tbody>
          {upPorts.map(p => {
            const ci = info(p.port);
            return (
              <tr key={p.port} className="nw-row" style={{ borderBottom: "1px solid #18233c" }}>
                <td style={tdd(true, true)}>{p.port}{p.sfp ? " (SFP)" : ""}</td>
                <td style={tdd(false, true)}>{p.speed ? p.speed + " M" : "—"}</td>
                <td style={tdd()}>{p.powered ? "⚡ powered" : p.poe ? "PoE" : "non-PoE"}</td>
                <td style={tdd()}>{ci ? ci.name : <span style={{ color: "#5b6b86" }}>not mapped</span>}</td>
                <td style={tdd(false, true)}>{ci && ci.ip ? ci.ip : "—"}</td>
                <td style={tdd(false, true)}>{ci && ci.cable ? ci.cable : "—"}</td>
              </tr>
            );
          })}
          {upPorts.length === 0 && <tr><td colSpan={6} style={{ padding: 14, color: "#5b6b86" }}>{d.snmp === false ? "Reachable via ping — SNMP not enabled on this switch, so no per-port detail." : (ports.length ? "No ports up." : "Port data pending next poll / switch has no IP.")}</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

/* PC / endpoint detail (view/monitor mode) */
function PcDetail({ d, devices, cables, onClose }) {
  const c = sc(d);
  const links = cables.filter(cb => cb.from === d.id || cb.to === d.id).map(cb => {
    const otherId = cb.from === d.id ? cb.to : cb.from;
    const port = cb.from === d.id ? cb.toPort : cb.fromPort;   // port on the switch end
    return { cable: cb.id, other: byId(devices, otherId), port };
  });
  const rows = [
    ["Mgmt IP", d.ip || "not set"],
    ["MAC", d.mac || "—"],
    ["Status", d.status === "up" ? "online" : d.status === "down" ? "offline" : "not checked"],
    ["Note", d.note || "—"],
  ];
  return (
    <div className="nw-card" style={{ marginTop: 14, padding: 18, borderLeft: `4px solid ${c}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14, flexWrap: "wrap" }}>
        <span style={{ fontSize: 17, fontWeight: 800, color: "#f8fafc" }}>🖥 {d.name}</span>
        <StatusPill s={d.status} />
        <button onClick={onClose} style={{ marginLeft: "auto", border: "none", background: "none", fontSize: 20, color: "#5b6b86", cursor: "pointer" }}>×</button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 12 }}>
        {rows.map(([k, v]) => (
          <div key={k}>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "#5b6b86", letterSpacing: ".06em" }}>{k}</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: "#e2e8f0", marginTop: 3 }}>{v}</div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 16, fontSize: 12, fontWeight: 800, color: "#f1f5f9", marginBottom: 8 }}>Connected to</div>
      {links.length ? (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {links.map(l => (
            <span key={l.cable} style={{ display: "inline-flex", alignItems: "center", gap: 8, padding: "6px 11px", borderRadius: 8, background: "#0f1830", border: "1px solid #243150", fontSize: 12.5, color: "#cbd5e1" }}>
              ↔ {l.other ? l.other.name : "?"}{l.port ? ` · port ${l.port}` : ""} <span style={{ color: "#5b6b86" }}>({l.cable})</span>
            </span>
          ))}
        </div>
      ) : <div style={{ fontSize: 12, color: "#5b6b86" }}>No cable yet — Builder me Connect se switch port se jodo.</div>}
    </div>
  );
}
const inp = { width: "100%", boxSizing: "border-box", padding: "8px 10px", borderRadius: 8, border: "1px solid #243150", background: "#0d1626", color: "#e2e8f0", fontSize: 13 };
function Field({ label, children }) {
  return <div><div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "#5b6b86", letterSpacing: ".06em", marginBottom: 4 }}>{label}</div>{children}</div>;
}

/* ════════════════════════════════════════════════════════════════════ */

function Topology({ devices, cables, sel, setSel, selDev }) {
  return (
    <div>
      <div className="nw-card" style={{ padding: 18, overflowX: "auto" }}>
        <div style={{ position: "relative", width: 1010, height: 320, minWidth: 760 }}>
          <svg viewBox="0 0 1010 320" width="1010" height="320" style={{ position: "absolute", inset: 0 }}>
            {cables.map(cb => {
              const a = byId(devices, cb.from), b = byId(devices, cb.to);
              if (!a || a.x == null || !b || b.x == null) return null;
              const st = cableStatus(devices, cb);
              return <line key={cb.id} x1={a.x + NW / 2} y1={a.y + NH / 2} x2={b.x + NW / 2} y2={b.y + NH / 2}
                           stroke={SC[st]} strokeWidth="2.6" className={st === "up" ? "nw-flow" : ""}
                           strokeDasharray={(st === "down" || st === "unconfigured") ? "5 5" : undefined}
                           strokeLinecap="round" opacity={(st === "down" || st === "unconfigured") ? .55 : 1} />;
            })}
          </svg>
          {devices.filter(d => d.x != null && d.y != null).map(d => {
            const c = sc(d), on = sel === d.id, unc = d.status === "unconfigured";
            const poePct = d.poe != null && d.poe_budget ? Math.round((d.poe / d.poe_budget) * 100) : null;
            const sub = unc ? "no IP yet" : d.status === "down" ? "offline" : `${d.portsUp ?? "?"}/${d.portsTotal ?? "?"} ports`;
            return (
              <div key={d.id} className={`nw-node${on ? " sel" : ""}${unc ? " unc" : ""}`} style={{ "--c": c, left: d.x, top: d.y, width: NW, height: NH }}
                   onClick={() => setSel(on ? null : d.id)}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span className={d.status === "up" ? "nw-live" : ""} style={{ display: "inline-flex" }}>
                    <span className="nw-dot" style={{ background: c, color: c }} />
                  </span>
                  <span style={{ fontSize: 15, fontWeight: 800, color: "#f8fafc" }}>{d.name}</span>
                  {d.vlan && <span style={{ marginLeft: "auto", fontSize: 9, fontWeight: 700, color: "#64748b" }}>v{d.vlan}</span>}
                </div>
                <div style={{ fontSize: 10.5, color: c, fontWeight: 700, marginTop: 2 }}>{sub}</div>
                {poePct != null && <div className="nw-bar" style={{ marginTop: 6 }}><span style={{ width: `${poePct}%`, background: poeCol(poePct) }} /></div>}
              </div>
            );
          })}
        </div>
        <Legend />
        <div style={{ marginTop: 10, fontSize: 11, color: "#5b6b86" }}>
          Topology uses each switch's saved position from the Floor Plan. Move switches in <b>Floor Plan</b> to rearrange.
        </div>
      </div>
      {selDev && <DeviceDetail d={selDev} onClose={() => setSel(null)} />}
    </div>
  );
}

function DeviceDetail({ d, onClose }) {
  const c = sc(d);
  const poePct = d.poe != null && d.poe_budget ? Math.round((d.poe / d.poe_budget) * 100) : null;
  const rows = [
    ["Model", "DGS-1210-28P"], ["Mgmt IP", d.ip || "not set"],
    ["VLAN / Zone", `vlan ${d.vlan || "—"}${d.zone ? ` · ${d.zone}` : ""}`],
    ["Ports up", d.status === "unconfigured" ? "—" : `${d.portsUp ?? "?"} / ${d.portsTotal ?? "?"}`],
  ];
  return (
    <div className="nw-card" style={{ marginTop: 14, padding: 18, borderLeft: `4px solid ${c}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
        <span style={{ fontSize: 17, fontWeight: 800, color: "#f8fafc" }}>{d.name}</span>
        <StatusPill s={d.status} />
        <button onClick={onClose} style={{ marginLeft: "auto", border: "none", background: "none", fontSize: 20, color: "#5b6b86", cursor: "pointer" }}>×</button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 12 }}>
        {rows.map(([k, v]) => (
          <div key={k}>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", color: "#5b6b86", letterSpacing: ".06em" }}>{k}</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: "#e2e8f0", marginTop: 3 }}>{v}</div>
          </div>
        ))}
      </div>
      {poePct != null && (
        <div style={{ marginTop: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, fontWeight: 700, color: "#94a3b8", marginBottom: 5 }}>
            <span>PoE budget</span><span style={{ color: poeCol(poePct) }}>{d.poe} / {d.poe_budget} W · {poePct}%</span>
          </div>
          <div className="nw-bar" style={{ height: 9 }}><span style={{ width: `${poePct}%`, background: poeCol(poePct) }} /></div>
        </div>
      )}
    </div>
  );
}

function WireRoute({ devices, cables }) {
  return (
    <div className="nw-card" style={{ padding: 18, overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead><tr style={{ borderBottom: "1px solid #243150" }}>
          {["Cable", "A-end", "B-end", "Media", "Length", "Status"].map(h => <th key={h} style={thd()}>{h}</th>)}
        </tr></thead>
        <tbody>
          {cables.map(cb => {
            const st = cableStatus(devices, cb);
            return (
              <tr key={cb.id} className="nw-row" style={{ borderBottom: "1px solid #18233c" }}>
                <td style={tdd(true, true)}>{cb.id}</td>
                <td style={tdd(false, true)}>{devName(devices, cb.from)}</td>
                <td style={tdd(false, true)}>{devName(devices, cb.to)}</td>
                <td style={tdd()}>{cb.media}</td>
                <td style={tdd(false, true)}>{cb.len ? cb.len + " m" : "—"}</td>
                <td style={tdd()}><StatusPill s={st} /></td>
              </tr>
            );
          })}
          {cables.length === 0 && <tr><td colSpan={6} style={{ padding: 16, color: "#5b6b86" }}>No cables yet — draw them in <b>Floor Plan</b> (Connect mode).</td></tr>}
        </tbody>
      </table>
      <div style={{ marginTop: 12, fontSize: 11, color: "#5b6b86" }}>
        CAT-7 copper runs at 1 G (≤100 m). For backbone &gt; 100 m or across zones with different grounds, use SFP fiber.
      </div>
    </div>
  );
}

function Registry({ devices, cables }) {
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div className="nw-card" style={{ padding: 18, overflowX: "auto" }}>
        <div style={{ fontSize: 14, fontWeight: 800, color: "#f1f5f9", marginBottom: 12 }}>Switches · {devices.length}</div>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead><tr style={{ borderBottom: "1px solid #243150" }}>
            {["Name", "Mgmt IP", "VLAN", "Zone", "Ports", "PoE (W)", "Status"].map(h => <th key={h} style={thd()}>{h}</th>)}
          </tr></thead>
          <tbody>
            {devices.map(d => (
              <tr key={d.id} className="nw-row" style={{ borderBottom: "1px solid #18233c" }}>
                <td style={tdd(true)}>{d.name}</td>
                <td style={tdd(false, true)}>{d.ip || "—"}</td><td style={tdd()}>{d.vlan || "—"}</td>
                <td style={tdd()}>{d.zone || "—"}</td>
                <td style={tdd(false, true)}>{d.status === "unconfigured" ? "—" : `${d.portsUp ?? "?"}/${d.portsTotal ?? "?"}`}</td>
                <td style={tdd(false, true)}>{d.poe == null ? "—" : `${d.poe}/${d.poe_budget || POE_BUDGET}`}</td>
                <td style={tdd()}><StatusPill s={d.status} /></td>
              </tr>
            ))}
            {devices.length === 0 && <tr><td colSpan={7} style={{ padding: 16, color: "#5b6b86" }}>No switches yet — add them in <b>Floor Plan</b>.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="nw-card" style={{ padding: 18, overflowX: "auto" }}>
        <div style={{ fontSize: 14, fontWeight: 800, color: "#f1f5f9", marginBottom: 12 }}>Cable schedule · {cables.length}</div>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead><tr style={{ borderBottom: "1px solid #243150" }}>
            {["Cable", "From", "To", "Media", "Len"].map(h => <th key={h} style={thd()}>{h}</th>)}
          </tr></thead>
          <tbody>
            {cables.map(cb => (
              <tr key={cb.id} className="nw-row" style={{ borderBottom: "1px solid #18233c" }}>
                <td style={tdd(true, true)}>{cb.id}</td><td style={tdd()}>{devName(devices, cb.from)}</td>
                <td style={tdd()}>{devName(devices, cb.to)}</td><td style={tdd()}>{cb.media}</td>
                <td style={tdd(false, true)}>{cb.len ? cb.len + " m" : "—"}</td>
              </tr>
            ))}
            {cables.length === 0 && <tr><td colSpan={5} style={{ padding: 16, color: "#5b6b86" }}>No cables yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AlertsView({ alerts }) {
  if (!alerts.length) return <div className="nw-card" style={{ padding: 24, color: "#22c55e" }}>No active alerts ✓</div>;
  return (
    <div style={{ display: "grid", gap: 10 }}>
      {alerts.map((a, i) => {
        const c = SC[a.sev] || SC.warn;
        return (
          <div key={i} className="nw-card" style={{ padding: "13px 16px", borderLeft: `4px solid ${c}`, display: "flex", alignItems: "center", gap: 12 }}>
            <StatusPill s={a.sev} />
            <span style={{ fontWeight: 800, color: "#f8fafc", minWidth: 46 }}>{a.dev}</span>
            <span style={{ color: "#aab8d0", fontSize: 13 }}>{a.msg}</span>
            <span style={{ marginLeft: "auto", color: "#5b6b86", fontSize: 11, fontFamily: "monospace" }}>{a.t}</span>
          </div>
        );
      })}
    </div>
  );
}

function Legend() {
  const items = [["up", SC.up], ["warning", SC.warn], ["down", SC.down], ["no IP", SC.unconfigured]];
  return (
    <div style={{ display: "flex", gap: 18, marginTop: 14, flexWrap: "wrap" }}>
      {items.map(([label, color]) => (
        <span key={label} style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 11, color: "#7c8aa5", fontWeight: 600 }}>
          <span style={{ width: 11, height: 11, borderRadius: 3, background: color, boxShadow: `0 0 8px ${color}66` }} /> {label}
        </span>
      ))}
    </div>
  );
}
function thd() { return { textAlign: "left", padding: "9px 10px", fontSize: 10, fontWeight: 800, letterSpacing: ".05em", color: "#5b6b86", textTransform: "uppercase" }; }
function tdd(bold = false, mono = false) { return { padding: "9px 10px", fontWeight: bold ? 800 : 500, color: bold ? "#f1f5f9" : "#c7d2e4", fontFamily: mono ? "monospace" : "inherit" }; }
