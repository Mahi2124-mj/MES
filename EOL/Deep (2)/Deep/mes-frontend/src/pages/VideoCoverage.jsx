// VideoCoverage.jsx — how many cycles got a video clip, how many did not, and why.
//
// Every cycle is checked 45 minutes after it ends (the clip archiver's window)
// against the camera state recorded at that time.  Camera Status is live: the
// last 20 minutes of cycles against the clip archive, per zone / line / camera.
// Read-only page.  The Camera Status tab appears once its API is available.
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import PageTopbar from "../components/PageTopbar";

const localDay = (d = new Date()) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const num  = (n) => (n == null ? "—" : Number(n).toLocaleString("en-IN"));
const pct  = (a, b) => (b ? Math.round((a * 1000) / b) / 10 : null);
const hhmm = (iso) => (iso ? new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "—");
const hms  = (iso) => (iso ? new Date(iso).toLocaleTimeString("en-GB") : "—");
const dt   = (iso) => (iso ? new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const dur  = (m) => (m == null ? "—" : m < 60 ? `${Math.round(m)} min` : `${(m / 60).toFixed(1)} h`);

const REASON = {
  clip_failed:    ["Clip not cut (camera was recording)", "#ca8a04"],
  camera_hung:    ["Camera hung (ping OK, no video)", "#ea580c"],
  camera_wrong_ip: ["Wrong camera address (IP is a PLC)", "#be185d"],
  camera_offline: ["Camera offline (no ping)", "#dc2626"],
  network_down:   ["Network / switch down", "#9333ea"],
  cms_down:       ["CMS down", "#b91c1c"],
  shift_wipe:     ["Footage deleted at shift change", "#0284c7"],
  no_camera:      ["No camera configured", "#64748b"],
  unknown:        ["Not tracked", "#94a3b8"],
};
const STATE = {
  recording:      ["Online", "#16a34a"],
  camera_hung:    ["Hung", "#ea580c"],
  camera_wrong_ip: ["Wrong address", "#be185d"],
  camera_offline: ["Offline", "#dc2626"],
  cms_down:       ["CMS down", "#b91c1c"],
};
const STATUS = {
  cutting:     ["Cutting", "#16a34a"],
  not_cutting: ["Not cutting", "#dc2626"],
  idle:        ["Idle", "#94a3b8"],
};
const covColor = (p) => (p == null ? "#94a3b8" : p >= 95 ? "#16a34a" : p >= 80 ? "#65a30d" : p >= 50 ? "#d97706" : "#dc2626");

export default function VideoCoverage() {
  const { token, theme } = useAuth();
  const dark = theme === "dark";
  const C = {
    bg: dark ? "#0b1220" : "#f1f5f9", fg: dark ? "#e6edf7" : "#0f172a", sub: dark ? "#94a3b8" : "#64748b",
    line: dark ? "#243049" : "#e2e8f0", card: dark ? "#131c30" : "#fff", soft: dark ? "#0f1729" : "#f8fafc",
    zone: dark ? "#16213a" : "#eef2f7", accent: "#1e40af",
  };
  const card = { background: C.card, border: `1px solid ${C.line}`, borderRadius: 12, padding: 14, marginBottom: 14,
                 boxShadow: dark ? "none" : "0 1px 2px rgba(15,23,42,.04)" };
  const inp  = { padding: "7px 10px", borderRadius: 8, fontSize: 13, border: `1px solid ${dark ? "#243049" : "#cbd5e1"}`,
                 background: dark ? "#0f1729" : "#fff", color: C.fg };
  const btn  = { ...inp, cursor: "pointer", fontWeight: 600 };
  const th   = { textAlign: "left", padding: "8px 10px", fontSize: 11, fontWeight: 700, color: C.sub, textTransform: "uppercase",
                 letterSpacing: ".04em", borderBottom: `1px solid ${C.line}`, whiteSpace: "nowrap", position: "sticky", top: 0,
                 background: C.card, zIndex: 1 };
  const thR  = { ...th, textAlign: "right" };
  const td   = { padding: "7px 10px", fontSize: 12.5, borderBottom: `1px solid ${dark ? "#1a2540" : "#f1f5f9"}`, whiteSpace: "nowrap" };
  const tdR  = { ...td, textAlign: "right", fontVariantNumeric: "tabular-nums" };
  const h3   = { fontSize: 13, fontWeight: 800, margin: "0 0 10px" };
  const kpiRow = { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(150px,1fr))", gap: 10, marginBottom: 14 };

  // ── filters + data ─────────────────────────────────────────────────────
  const [from, setFrom]     = useState(localDay());
  const [to, setTo]         = useState(localDay());
  const [shift, setShift]   = useState("ALL");
  const [lineId, setLineId] = useState("");
  const [lines, setLines]   = useState([]);
  const [tab, setTab]       = useState("summary");
  const [busy, setBusy]     = useState(false);
  const [err, setErr]       = useState("");
  const [auto, setAuto]     = useState(false);

  const [sum, setSum]         = useState(null);
  const [openLine, setOpenLine] = useState({});     // line_id -> machines[] | "loading"
  const [miss, setMiss]       = useState(null);
  const [mReason, setMReason] = useState("");
  const [mMachine, setMMachine] = useState("");
  const [mSearch, setMSearch] = useState("");
  const [page, setPage]       = useState(1);
  const [cams, setCams]       = useState(null);
  const [camState, setCamState] = useState("");
  const [agent, setAgent]     = useState(null);
  const [agentAll, setAgentAll] = useState(false);

  // Camera Status (appears once /camera-status answers)
  const [csAvail, setCsAvail] = useState(null);
  const [camSt, setCamSt]     = useState(null);
  const [csOpen, setCsOpen]   = useState({});
  const [csStatus, setCsStatus] = useState("");
  const [csState, setCsState] = useState("");
  const [csZone, setCsZone]   = useState("");
  const [csSearch, setCsSearch] = useState("");
  const [logView, setLogView] = useState("camera");
  const [camLog, setCamLog]   = useState(null);
  const [lineLog, setLineLog] = useState(null);
  const [logState, setLogState] = useState("");
  const [logQ, setLogQ]       = useState("");
  const [logPage, setLogPage] = useState(1);

  useEffect(() => {
    api.get("/api/lines/", token).then(d => setLines(Array.isArray(d) ? d : [])).catch(() => setLines([]));
  }, [token]);

  const q = useCallback((extra = {}) => {
    const p = new URLSearchParams({ date_from: from, date_to: to, shift });
    if (lineId) p.set("line_id", lineId);
    Object.entries(extra).forEach(([k, v]) => { if (v !== "" && v != null) p.set(k, String(v)); });
    return p.toString();
  }, [from, to, shift, lineId]);

  const run = async (fn) => {
    setBusy(true); setErr("");
    try { await fn(); } catch (e) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };
  const loadSummary = useCallback(() => run(async () => { setOpenLine({}); setSum(await api.get(`/api/video-coverage/summary?${q()}`, token)); }),
    [q, token]);
  const loadMissing = useCallback((pg = page, reason = mReason, machine = mMachine) => run(async () =>
    setMiss(await api.get(`/api/video-coverage/missing?${q({ reason, machine_key: machine, page: pg, page_size: 100 })}`, token))),
    [q, token, page, mReason, mMachine]);
  const loadCams  = useCallback(() => run(async () => setCams(await api.get("/api/video-coverage/cameras", token))),
    [token]);
  const loadAgent = useCallback(async () => {
    try { setAgent(await api.get("/api/video-coverage/agent", token)); } catch { /* shown by the tab */ }
  }, [token]);
  const loadCamStatus = useCallback(async (quiet = false) => {
    try {
      const d = await api.get("/api/video-coverage/camera-status", token);
      setCamSt(d); setCsAvail(true);
    } catch (e) {
      const m = String(e.message || e);
      if (/404|not found/i.test(m)) setCsAvail(false);
      else if (!quiet) setErr(m);
    }
  }, [token]);
  const loadLogs = useCallback(async (view = logView, pg = logPage, st = logState, qq = logQ) => {
    try {
      if (view === "camera") {
        const p = new URLSearchParams({ date_from: from, date_to: to, page: String(pg), page_size: "200" });
        if (lineId) p.set("line_id", lineId);
        if (st) p.set("state", st);
        if (qq.trim()) p.set("q", qq.trim());
        setCamLog(await api.get(`/api/video-coverage/camera-log?${p}`, token));
      } else {
        const p = new URLSearchParams({ date_from: from, date_to: to });
        if (lineId) p.set("line_id", lineId);
        setLineLog(await api.get(`/api/video-coverage/line-log?${p}`, token));
      }
    } catch (e) { setErr(String(e.message || e)); }
  }, [from, to, lineId, token, logView, logPage, logState, logQ]);

  const loadTab = useCallback((t = tab) => {
    if (t === "summary") loadSummary();
    else if (t === "missing") loadMissing(1);
    else if (t === "cameras") loadCams();
    else if (t === "agent") loadAgent();
    else if (t === "camstatus") { run(() => loadCamStatus()); loadLogs(); }
  }, [tab, loadSummary, loadMissing, loadCams, loadAgent, loadCamStatus, loadLogs]);

  useEffect(() => { loadAgent(); loadCamStatus(true); }, [loadAgent, loadCamStatus]);
  // first load, and a new line filter, reload what is on screen
  useEffect(() => {
    if (tab === "summary") loadSummary();
    else if (tab === "missing") { setPage(1); loadMissing(1); }
    else if (tab === "camstatus") { setLogPage(1); loadLogs(logView, 1); }
    // eslint-disable-next-line
  }, [lineId]);
  useEffect(() => {
    if (!auto) return;
    const t = setInterval(() => loadTab(), 60000);
    return () => clearInterval(t);
  }, [auto, loadTab]);

  const go = (t) => { setTab(t); loadTab(t); };
  const openMissing = (reason = "", machine = "", line = null) => {
    setMReason(reason); setMMachine(machine); setMSearch(""); setPage(1); setTab("missing");
    if (line != null && String(line) !== String(lineId)) setLineId(String(line));   // effect reloads
    else loadMissing(1, reason, machine);
  };
  const toggleLine = async (lid) => {
    if (openLine[lid]) { setOpenLine(o => { const n = { ...o }; delete n[lid]; return n; }); return; }
    setOpenLine(o => ({ ...o, [lid]: "loading" }));
    try {
      const p = new URLSearchParams({ date_from: from, date_to: to, shift, line_id: String(lid) });
      const d = await api.get(`/api/video-coverage/summary?${p}`, token);
      setOpenLine(o => ({ ...o, [lid]: d.machines || [] }));
    } catch { setOpenLine(o => ({ ...o, [lid]: [] })); }
  };

  const download = async () => {
    setErr("");
    try {
      const jwt = token || sessionStorage.getItem("mes_token") || "";
      const url = tab === "camstatus"
        ? `/api/video-coverage/camera-log/export?${new URLSearchParams({ date_from: from, date_to: to,
            ...(lineId ? { line_id: lineId } : {}), ...(logState ? { state: logState } : {}) })}`
        : `/api/video-coverage/export?${q({ reason: mReason })}`;
      const res = await fetch(url, { headers: { Authorization: `Bearer ${jwt}` } });
      if (!res.ok) throw new Error("Export failed (" + res.status + ")");
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = tab === "camstatus" ? `camera_status_${from}_${to}.xlsx` : `video_coverage_${from}_${to}_${shift}.xlsx`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(a.href);
    } catch (e) { setErr(String(e.message || e)); }
  };

  // ── derived ─────────────────────────────────────────────────────────────
  const k = sum?.kpis;
  const zonesSum = useMemo(() => {
    const z = {};
    (sum?.lines || []).forEach(l => {
      const key = l.zone_name || "—";
      const a = (z[key] = z[key] || { zone: key, cycles: 0, clips: 0, missing: 0, lines: [] });
      a.cycles += l.cycles; a.clips += l.clips; a.missing += l.missing; a.lines.push(l);
    });
    return Object.values(z).sort((a, b) => a.zone.localeCompare(b.zone));
  }, [sum]);
  const hours = useMemo(() => {
    const hs = [...new Set((sum?.hourly || []).map(h => h.hour))].sort();
    const by = {};
    (sum?.hourly || []).forEach(h => { (by[h.line_id] = by[h.line_id] || {})[h.hour] = h; });
    return { hs, by };
  }, [sum]);
  const missRows = useMemo(() => {
    const s = mSearch.trim().toLowerCase();
    if (!s) return miss?.rows || [];
    return (miss?.rows || []).filter(r => [r.cycle_seq, r.part_code, r.camera_ip, r.machine_name, r.line_name]
      .some(v => String(v ?? "").toLowerCase().includes(s)));
  }, [miss, mSearch]);
  const openFindings = (agent?.findings || []).filter(f => !f.closed_at).length;

  const csFiltered = useMemo(() => {
    if (!camSt) return [];
    const s = csSearch.trim().toLowerCase();
    const want = (c) => (!csStatus || c.status === csStatus) && (!csState || c.state === csState)
      && (!s || [c.ip, c.camera_id, ...(c.machines || [])].some(v => String(v || "").toLowerCase().includes(s)));
    return camSt.zones.filter(z => !csZone || z.zone_name === csZone).map(z => ({
      ...z, lines: z.lines.map(L => ({ ...L, shown: L.cameras_list.filter(want) }))
                          .filter(L => L.shown.length || !(csStatus || csState || s)),
    })).filter(z => z.lines.length);
  }, [camSt, csStatus, csState, csZone, csSearch]);
  const csFiltering = !!(csStatus || csState || csSearch.trim());

  // ── small UI pieces ─────────────────────────────────────────────────────
  const Kpi = ({ label, value, color, sub, bar }) => (
    <div style={{ minWidth: 0, background: C.card, border: `1px solid ${C.line}`, borderRadius: 12,
                  padding: "10px 14px", borderTop: `3px solid ${color || C.line}` }}>
      <div style={{ fontSize: 11, color: C.sub, fontWeight: 700, textTransform: "uppercase", letterSpacing: ".05em" }}>{label}</div>
      <div style={{ fontSize: 23, fontWeight: 800, color: color || C.fg, lineHeight: 1.25 }}>{value}</div>
      {bar != null && <div style={{ height: 5, borderRadius: 3, background: C.zone, marginTop: 4, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${Math.max(0, Math.min(100, bar))}%`, background: covColor(bar) }} /></div>}
      {sub && <div style={{ fontSize: 11.5, color: C.sub, marginTop: 3 }}>{sub}</div>}
    </div>
  );
  const Bar = ({ p, w = 80 }) => (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, justifyContent: "flex-end" }}>
      <span style={{ width: w, height: 7, borderRadius: 4, background: C.zone, overflow: "hidden" }}>
        <span style={{ display: "block", height: "100%", width: `${Math.max(0, Math.min(100, p || 0))}%`, background: covColor(p) }} />
      </span>
      <b style={{ color: covColor(p), minWidth: 44, textAlign: "right" }}>{p == null ? "—" : `${p}%`}</b>
    </span>
  );
  const Pill = ({ text, color }) => (
    <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 999, background: `${color}14`, color, fontWeight: 700,
                   border: `1px solid ${color}40`, whiteSpace: "nowrap" }}>{text}</span>
  );
  const Chips = ({ reasons, onPick }) => (
    <span style={{ display: "inline-flex", gap: 4, flexWrap: "wrap" }}>
      {Object.entries(reasons || {}).sort((a, b) => b[1] - a[1]).map(([rk, n]) => {
        const [lb, col] = REASON[rk] || [rk, C.sub];
        return <span key={rk} onClick={onPick ? (e) => { e.stopPropagation(); onPick(rk); } : undefined} title={lb}
                     style={{ fontSize: 11, padding: "1px 7px", borderRadius: 999, cursor: onPick ? "pointer" : "default",
                              border: `1px solid ${col}55`, color: col }}>{lb.split(" (")[0]} · {num(n)}</span>;
      })}
    </span>
  );
  const Tab = ({ id, label, badge, badgeColor }) => (
    <button onClick={() => go(id)}
            style={{ padding: "10px 16px", border: "none", background: "none", cursor: "pointer", whiteSpace: "nowrap",
                     fontSize: 13.5, fontWeight: tab === id ? 800 : 600, color: tab === id ? C.accent : C.sub,
                     borderBottom: tab === id ? `3px solid ${C.accent}` : "3px solid transparent", marginBottom: -1 }}>
      {label}{badge != null && badge !== "" && (
        <span style={{ marginLeft: 6, fontSize: 11, padding: "1px 7px", borderRadius: 999, fontWeight: 800,
                       background: badgeColor || C.zone, color: badgeColor ? "#fff" : C.sub }}>{badge}</span>)}
    </button>
  );
  const Empty = ({ children }) => <div style={{ padding: 18, textAlign: "center", color: C.sub, fontSize: 13 }}>{children}</div>;

  return (
    <div style={{ minHeight: "100vh", background: C.bg, padding: "16px 16px 40px", color: C.fg }}>
      <PageTopbar leading="Video" accent="Coverage" />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "12px 0", fontSize: 12, color: C.sub }}>
        <span>Cycles are checked {sum?.mature_min || 45} min after they end</span>
        {agent?.status?.tracking_since && <span>· Tracking since {dt(agent.status.tracking_since)}</span>}
        {sum?.last_eval && <span>· Last check {hhmm(sum.last_eval)}</span>}
      </div>

      {/* filters */}
      <div style={{ ...card, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
        {[["From", <input type="date" value={from} max={to} onChange={e => setFrom(e.target.value)} style={inp} />],
          ["To", <input type="date" value={to} min={from} onChange={e => setTo(e.target.value)} style={inp} />],
          ["Shift", <select value={shift} onChange={e => setShift(e.target.value)} style={inp}>
                      <option value="ALL">All shifts</option><option value="A">Shift A</option><option value="B">Shift B</option></select>],
          ["Line", <select value={lineId} onChange={e => setLineId(e.target.value)} style={{ ...inp, minWidth: 170 }}>
                     <option value="">All lines</option>{lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}</select>],
        ].map(([lb, el]) => (
          <label key={lb} style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 11, fontWeight: 700, color: C.sub,
                                   textTransform: "uppercase", letterSpacing: ".04em" }}>{lb}{el}</label>
        ))}
        <button onClick={() => loadTab()} disabled={busy}
                style={{ ...btn, background: C.accent, color: "#fff", border: "none", fontWeight: 800, minWidth: 80 }}>
          {busy ? "Loading…" : "Load"}</button>
        <button onClick={download} style={btn}>⬇ Excel</button>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, color: C.sub, paddingBottom: 7 }}>
          <input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} /> Auto-refresh
        </label>
      </div>

      {/* tabs */}
      <div style={{ display: "flex", gap: 2, borderBottom: `1px solid ${C.line}`, marginBottom: 14, overflowX: "auto", overflowY: "hidden" }}>
        <Tab id="summary" label="Summary" />
        {csAvail && <Tab id="camstatus" label="Camera Status"
                         badge={camSt ? camSt.kpis.not_cutting || "" : ""} badgeColor={camSt?.kpis.not_cutting ? "#dc2626" : null} />}
        <Tab id="missing" label="Missing cycles" badge={k ? num(k.missing) : ""} />
        {!csAvail && <Tab id="cameras" label="Cameras" />}
        <Tab id="agent" label="Video Agent" badge={openFindings || ""} badgeColor={openFindings ? "#ea580c" : null} />
      </div>

      {err && <div style={{ ...card, borderLeft: "4px solid #dc2626", color: "#dc2626" }}>{err}</div>}

      {/* ── SUMMARY ─────────────────────────────────────────────────── */}
      {tab === "summary" && sum && (
        <>
          <div style={kpiRow}>
            <Kpi label="Cycles checked" value={num(k.cycles)} color={C.accent} />
            <Kpi label="With clip" value={num(k.clips)} color="#16a34a" sub={k.cycles ? `${pct(k.clips, k.cycles)}% of cycles` : null} />
            <Kpi label="Missing" value={num(k.missing)} color="#dc2626" sub={k.cycles ? `${pct(k.missing, k.cycles)}% of cycles` : null} />
            <Kpi label="Coverage" value={k.coverage == null ? "—" : `${k.coverage}%`} color={covColor(k.coverage)} bar={k.coverage} />
            <Kpi label="Pending" value={num(k.pending)} color="#94a3b8" sub={`newer than ${sum.mature_min} min`} />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(320px,1fr))", gap: 14 }}>
            <div style={card}>
              <div style={h3}>Missing clip reasons</div>
              {!sum.reasons.length ? <Empty>No missing clips.</Empty> : (() => {
                const tot = sum.reasons.reduce((a, r) => a + r.count, 0) || 1;
                return sum.reasons.map(r => {
                  const [lb, col] = REASON[r.key] || [r.label, C.sub];
                  return (
                    <div key={r.key} onClick={() => openMissing(r.key)} title="Show these cycles"
                         style={{ display: "grid", gridTemplateColumns: "minmax(210px,1.6fr) 2fr 64px 40px", alignItems: "center",
                                  gap: 10, padding: "5px 0", cursor: "pointer", fontSize: 12.5 }}>
                      <span style={{ display: "flex", alignItems: "center", gap: 7 }}>
                        <span style={{ width: 9, height: 9, borderRadius: 3, background: col, flex: "none" }} />{lb}</span>
                      <span style={{ height: 9, background: C.zone, borderRadius: 5, overflow: "hidden" }}>
                        <span style={{ display: "block", height: "100%", width: `${(r.count * 100) / tot}%`, background: col }} /></span>
                      <b style={{ textAlign: "right" }}>{num(r.count)}</b>
                      <span style={{ textAlign: "right", color: C.sub }}>{Math.round((r.count * 100) / tot)}%</span>
                    </div>);
                });
              })()}
            </div>
            <div style={card}>
              <div style={h3}>Zone coverage</div>
              {!zonesSum.length ? <Empty>No checked cycles yet.</Empty> : zonesSum.map(z => (
                <div key={z.zone} style={{ display: "grid", gridTemplateColumns: "minmax(110px,1fr) 1.6fr 120px", alignItems: "center",
                                           gap: 10, padding: "6px 0", fontSize: 12.5, borderBottom: `1px solid ${C.zone}` }}>
                  <b>{z.zone}</b>
                  <Bar p={pct(z.clips, z.cycles)} w={140} />
                  <span style={{ textAlign: "right", color: C.sub }}>{num(z.clips)} / {num(z.cycles)}</span>
                </div>
              ))}
            </div>
          </div>

          <div style={{ ...card, padding: 0, overflow: "auto" }}>
            <div style={{ ...h3, padding: "12px 14px 0" }}>Line-wise <span style={{ color: C.sub, fontWeight: 500 }}>· click a line for machines</span></div>
            <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 820 }}>
              <thead><tr>
                <th style={th}>Line</th><th style={thR}>Cycles</th><th style={thR}>With clip</th><th style={thR}>Missing</th>
                <th style={thR}>Coverage</th><th style={th}>Reasons</th></tr></thead>
              <tbody>
                {zonesSum.map(z => (
                  <Fragment key={z.zone}>
                    <tr style={{ background: C.zone }}>
                      <td style={{ ...td, fontWeight: 800 }}>{z.zone}</td>
                      <td style={{ ...tdR, fontWeight: 700 }}>{num(z.cycles)}</td>
                      <td style={{ ...tdR, fontWeight: 700, color: "#16a34a" }}>{num(z.clips)}</td>
                      <td style={{ ...tdR, fontWeight: 700, color: z.missing ? "#dc2626" : C.sub }}>{num(z.missing)}</td>
                      <td style={tdR}><Bar p={pct(z.clips, z.cycles)} /></td><td style={td} />
                    </tr>
                    {z.lines.map(l => {
                      const open = openLine[l.line_id];
                      return (
                        <Fragment key={l.line_id}>
                          <tr onClick={() => toggleLine(l.line_id)} style={{ cursor: "pointer" }}>
                            <td style={{ ...td, paddingLeft: 22, fontWeight: 700 }}>{open ? "▾" : "▸"} {l.line_name}</td>
                            <td style={tdR}>{num(l.cycles)}</td>
                            <td style={{ ...tdR, color: "#16a34a" }}>{num(l.clips)}</td>
                            <td style={{ ...tdR, color: l.missing ? "#dc2626" : C.sub, fontWeight: l.missing ? 700 : 400 }}>{num(l.missing)}</td>
                            <td style={tdR}><Bar p={l.coverage} /></td>
                            <td style={{ ...td, whiteSpace: "normal" }}><Chips reasons={l.reasons} onPick={(rk) => openMissing(rk, "", l.line_id)} /></td>
                          </tr>
                          {open && (
                            <tr><td colSpan={6} style={{ padding: "4px 10px 10px 40px", background: C.soft }}>
                              {open === "loading" ? <Empty>Loading…</Empty> : !open.length ? <Empty>No machines.</Empty> : (
                                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                                  <thead><tr><th style={{ ...th, background: C.soft }}>Machine</th><th style={{ ...th, background: C.soft }}>Camera</th>
                                    <th style={{ ...thR, background: C.soft }}>Cycles</th><th style={{ ...thR, background: C.soft }}>With clip</th>
                                    <th style={{ ...thR, background: C.soft }}>Missing</th><th style={{ ...thR, background: C.soft }}>Coverage</th>
                                    <th style={{ ...th, background: C.soft }}>Reasons</th></tr></thead>
                                  <tbody>{open.map(m => (
                                    <tr key={m.machine_key} onClick={() => openMissing("", m.machine_key, l.line_id)} style={{ cursor: "pointer" }} title="Show missing cycles">
                                      <td style={{ ...td, fontWeight: 600 }}>{m.machine_key === "main" ? "★ " : ""}{m.machine_name}</td>
                                      <td style={{ ...td, color: C.sub }}>{m.camera_ip || "—"}</td>
                                      <td style={tdR}>{num(m.cycles)}</td>
                                      <td style={{ ...tdR, color: "#16a34a" }}>{num(m.clips)}</td>
                                      <td style={{ ...tdR, color: m.missing ? "#dc2626" : C.sub }}>{num(m.missing)}</td>
                                      <td style={tdR}><Bar p={m.coverage} w={60} /></td>
                                      <td style={{ ...td, whiteSpace: "normal" }}><Chips reasons={m.reasons} onPick={(rk) => openMissing(rk, m.machine_key, l.line_id)} /></td>
                                    </tr>))}</tbody>
                                </table>)}
                            </td></tr>)}
                        </Fragment>);
                    })}
                  </Fragment>
                ))}
                {!zonesSum.length && <tr><td colSpan={6}><Empty>No checked cycles in this range yet.</Empty></td></tr>}
              </tbody>
            </table>
          </div>

          {hours.hs.length > 0 && (
            <div style={{ ...card, padding: 0, overflow: "auto" }}>
              <div style={{ ...h3, padding: "12px 14px 0" }}>Hourly coverage</div>
              <table style={{ borderCollapse: "collapse" }}>
                <thead><tr><th style={th}>Line</th>{hours.hs.map(h => <th key={h} style={{ ...th, textAlign: "center" }}>{hhmm(h)}</th>)}</tr></thead>
                <tbody>
                  {zonesSum.flatMap(z => z.lines).map(l => (
                    <tr key={l.line_id}>
                      <td style={{ ...td, fontWeight: 700 }}>{l.line_name}</td>
                      {hours.hs.map(h => {
                        const c = hours.by[l.line_id]?.[h];
                        const p = c && c.cycles ? Math.round((c.clips * 100) / c.cycles) : null;
                        return <td key={h} title={c ? `${c.clips}/${c.cycles} with clip` : "no cycles"}
                                   style={{ ...td, textAlign: "center", fontWeight: 700, padding: "6px 8px",
                                            color: p == null ? C.sub : "#fff", background: p == null ? "transparent" : covColor(p) }}>
                                 {p == null ? "·" : `${p}%`}</td>;
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {/* ── CAMERA STATUS ───────────────────────────────────────────── */}
      {tab === "camstatus" && camSt && (() => {
        const K = camSt.kpis;
        const cell = (v, col) => <td style={{ ...tdR, fontWeight: v ? 700 : 400, color: v ? col : C.sub }}>{num(v)}</td>;
        const mix = (o) => {        // online / hung / offline stacked bar
          const t = (o.online || 0) + (o.hung || 0) + (o.wrong_ip || 0) + (o.offline || 0) + (o.cms_down || 0) || 1;
          return (
            <span style={{ display: "inline-flex", width: 90, height: 8, borderRadius: 4, overflow: "hidden", background: C.zone }}>
              {[["online", "#16a34a"], ["hung", "#ea580c"], ["wrong_ip", "#be185d"], ["offline", "#dc2626"],
                ["cms_down", "#b91c1c"]].map(([f, col]) =>
                o[f] ? <span key={f} style={{ width: `${(o[f] * 100) / t}%`, background: col }} /> : null)}
            </span>);
        };
        const zones = [...new Set(camSt.zones.map(z => z.zone_name))];
        return (
          <>
            <div style={{ fontSize: 12, color: C.sub, marginBottom: 10 }}>
              Live · cycles ended {hhmm(camSt.window.from)}–{hhmm(camSt.window.to)}
            </div>
            <div style={kpiRow}>
              <Kpi label="Total cameras" value={num(K.total_cameras)} color={C.accent} sub={`${K.unbound} not on a machine`} />
              <Kpi label="Online" value={num(K.online)} color="#16a34a" sub={`of ${num(K.cameras)} on machines`} />
              <Kpi label="Hung" value={num(K.hung)} color="#ea580c" sub="ping OK, no video — power-cycle" />
              <Kpi label="Wrong address" value={num(K.wrong_ip)} color="#be185d" sub="IP is a PLC — fix in Camera Master" />
              <Kpi label="Offline" value={num(K.offline)} color="#dc2626" />
              <Kpi label="Clips cutting" value={num(K.cutting)} color="#16a34a" />
              <Kpi label="Not cutting" value={num(K.not_cutting)} color="#dc2626" />
              <Kpi label="Idle" value={num(K.idle)} color="#94a3b8" sub="machine not running" />
            </div>

            <div style={{ ...card, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", padding: 10 }}>
              {[["", "All"], ["not_cutting", "Not cutting"], ["cutting", "Cutting"], ["idle", "Idle"]].map(([v, lb]) => (
                <button key={v || "all"} onClick={() => setCsStatus(v)}
                        style={{ ...btn, fontWeight: csStatus === v ? 800 : 500, background: csStatus === v ? C.accent : inp.background,
                                 color: csStatus === v ? "#fff" : (STATUS[v]?.[1] || C.fg), border: csStatus === v ? "none" : inp.border }}>{lb}</button>
              ))}
              <select value={csState} onChange={e => setCsState(e.target.value)} style={inp}>
                <option value="">Any camera state</option>
                {Object.entries(STATE).map(([s, [lb]]) => <option key={s} value={s}>{lb}</option>)}
              </select>
              <select value={csZone} onChange={e => setCsZone(e.target.value)} style={inp}>
                <option value="">All zones</option>{zones.map(z => <option key={z} value={z}>{z}</option>)}
              </select>
              <input value={csSearch} onChange={e => setCsSearch(e.target.value)} placeholder="Search IP / machine" style={{ ...inp, minWidth: 170 }} />
              <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                <button onClick={() => setCsOpen(Object.fromEntries(camSt.zones.flatMap(z => z.lines.map(L => [L.line_id, true]))))} style={btn}>Expand all</button>
                <button onClick={() => setCsOpen({})} style={btn}>Collapse all</button>
              </span>
            </div>

            <div style={{ ...card, padding: 0, overflow: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 980 }}>
                <thead><tr>
                  <th style={th}>Zone / Line</th><th style={thR}>Cameras</th><th style={th}>State mix</th><th style={thR}>Online</th>
                  <th style={thR}>Hung</th><th style={thR}>Wrong address</th><th style={thR}>Offline</th><th style={thR}>Cutting</th><th style={thR}>Not cutting</th><th style={thR}>Idle</th></tr></thead>
                <tbody>
                  {csFiltered.map(z => (
                    <Fragment key={z.zone_name}>
                      <tr style={{ background: C.zone }}>
                        <td style={{ ...td, fontWeight: 800 }}>{z.zone_name}</td>
                        {cell(z.cameras, C.fg)}<td style={td}>{mix(z)}</td>
                        {cell(z.online, "#16a34a")}{cell(z.hung, "#ea580c")}{cell(z.wrong_ip, "#be185d")}{cell(z.offline, "#dc2626")}
                        {cell(z.cutting, "#16a34a")}{cell(z.not_cutting, "#dc2626")}{cell(z.idle, C.sub)}
                      </tr>
                      {z.lines.map(L => {
                        const open = csOpen[L.line_id] || (csFiltering && L.shown.length > 0);
                        return (
                          <Fragment key={L.line_id}>
                            <tr style={{ cursor: "pointer" }} onClick={() => setCsOpen(o => ({ ...o, [L.line_id]: !o[L.line_id] }))}>
                              <td style={{ ...td, paddingLeft: 22, fontWeight: 700 }}>{open ? "▾" : "▸"} {L.line_name}</td>
                              {cell(L.cameras, C.fg)}<td style={td}>{mix(L)}</td>
                              {cell(L.online, "#16a34a")}{cell(L.hung, "#ea580c")}{cell(L.wrong_ip, "#be185d")}{cell(L.offline, "#dc2626")}
                              {cell(L.cutting, "#16a34a")}{cell(L.not_cutting, "#dc2626")}{cell(L.idle, C.sub)}
                            </tr>
                            {open && (
                              <tr><td colSpan={10} style={{ padding: "4px 10px 10px 40px", background: C.soft }}>
                                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                                  <thead><tr>{["Camera IP", "Machine", "State", "Since", "Cycles", "Clips", "Clip %", "Status", "Reason"].map(h =>
                                    <th key={h} style={{ ...(["Cycles", "Clips", "Clip %"].includes(h) ? thR : th), background: C.soft }}>{h}</th>)}</tr></thead>
                                  <tbody>
                                    {(csFiltering ? L.shown : L.cameras_list).map(c => {
                                      const [slb, scol] = STATE[c.state] || ["Not tracked", C.sub];
                                      const [stl, stc] = STATUS[c.status];
                                      return (
                                        <tr key={c.camera_id}>
                                          <td style={{ ...td, fontWeight: 700 }}>{c.main ? "★ " : ""}{c.ip || "—"}</td>
                                          <td style={{ ...td, whiteSpace: "normal", maxWidth: 260 }}>{c.machines.join(", ")}</td>
                                          <td style={td}><Pill text={slb + (c.subnet_down ? " · subnet" : "")} color={scol} /></td>
                                          <td style={{ ...td, color: C.sub }}>{dt(c.since)}</td>
                                          <td style={tdR}>{num(c.cycles)}</td>
                                          <td style={tdR}>{num(c.clips)}</td>
                                          <td style={tdR}>{c.clip_pct == null ? "—" : `${c.clip_pct}%`}</td>
                                          <td style={td}><Pill text={stl} color={stc} /></td>
                                          <td style={{ ...td, color: C.sub, whiteSpace: "normal" }}>{c.reason || ""}</td>
                                        </tr>);
                                    })}
                                  </tbody>
                                </table>
                              </td></tr>)}
                          </Fragment>);
                      })}
                    </Fragment>
                  ))}
                  {!csFiltered.length && <tr><td colSpan={10}><Empty>No cameras match this filter.</Empty></td></tr>}
                </tbody>
              </table>
            </div>

            {camSt.unassigned?.length > 0 && (
              <div style={{ ...card, padding: 0, overflow: "auto" }}>
                <div style={{ ...h3, padding: "12px 14px 0" }}>Not on any machine <span style={{ color: C.sub, fontWeight: 500 }}>· {camSt.unassigned.length} cameras</span></div>
                <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 600 }}>
                  <thead><tr><th style={th}>Camera IP</th><th style={th}>Camera</th><th style={th}>State</th><th style={th}>Since</th></tr></thead>
                  <tbody>{camSt.unassigned.map(c => {
                    const [slb, scol] = STATE[c.state] || ["Not tracked", C.sub];
                    return (<tr key={c.camera_id}><td style={{ ...td, fontWeight: 700 }}>{c.ip || "—"}</td><td style={{ ...td, color: C.sub }}>{c.camera_id}</td>
                      <td style={td}><Pill text={slb} color={scol} /></td><td style={{ ...td, color: C.sub }}>{dt(c.since)}</td></tr>);
                  })}</tbody>
                </table>
              </div>
            )}

            <div style={card}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
                <b style={{ fontSize: 13, marginRight: 4 }}>Log</b>
                {[["camera", "Camera log"], ["line", "Line log (5 min)"]].map(([v, lb]) => (
                  <button key={v} onClick={() => { setLogView(v); setLogPage(1); loadLogs(v, 1); }}
                          style={{ ...btn, fontWeight: logView === v ? 800 : 500, background: logView === v ? C.accent : inp.background,
                                   color: logView === v ? "#fff" : C.fg, border: logView === v ? "none" : inp.border }}>{lb}</button>
                ))}
                {logView === "camera" && (<>
                  <select value={logState} onChange={e => { setLogState(e.target.value); setLogPage(1); loadLogs("camera", 1, e.target.value); }} style={inp}>
                    <option value="">All states</option>
                    {Object.entries(STATE).map(([s, [lb]]) => <option key={s} value={s}>{lb}</option>)}
                  </select>
                  <input value={logQ} onChange={e => setLogQ(e.target.value)} placeholder="Camera IP"
                         onKeyDown={e => { if (e.key === "Enter") { setLogPage(1); loadLogs("camera", 1, logState, e.currentTarget.value); } }}
                         style={{ ...inp, width: 140 }} />
                </>)}
              </div>
              {logView === "camera" && camLog && (
                <>
                  <div style={{ overflow: "auto", maxHeight: 440, border: `1px solid ${C.line}`, borderRadius: 8 }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 900 }}>
                      <thead><tr>{["From", "To", "Duration", "State", "Camera IP", "Used by"].map(h => <th key={h} style={h === "Duration" ? thR : th}>{h}</th>)}</tr></thead>
                      <tbody>
                        {camLog.rows.map((r, i) => {
                          const [lb, col] = STATE[r.state] || [r.state, C.sub];
                          return (
                            <tr key={i}>
                              <td style={td}>{dt(r.from)}</td><td style={td}>{dt(r.to)}</td><td style={tdR}>{dur(r.minutes)}</td>
                              <td style={td}><Pill text={lb + (r.subnet_down ? " · subnet" : "")} color={col} /></td>
                              <td style={{ ...td, fontWeight: 600 }}>{r.ip || "—"}</td>
                              <td style={{ ...td, whiteSpace: "normal", color: C.sub }}>{r.bound_to.join(", ") || "—"}</td>
                            </tr>);
                        })}
                        {!camLog.rows.length && <tr><td colSpan={6}><Empty>No camera states for this filter.</Empty></td></tr>}
                      </tbody>
                    </table>
                  </div>
                  {camLog.total > camLog.page_size && (
                    <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12.5, marginTop: 8 }}>
                      <button disabled={logPage <= 1} onClick={() => { const p = logPage - 1; setLogPage(p); loadLogs("camera", p); }} style={btn}>‹ Prev</button>
                      <span>Page {logPage} of {Math.ceil(camLog.total / camLog.page_size)} · {num(camLog.total)} periods</span>
                      <button disabled={logPage * camLog.page_size >= camLog.total} onClick={() => { const p = logPage + 1; setLogPage(p); loadLogs("camera", p); }} style={btn}>Next ›</button>
                    </div>)}
                </>
              )}
              {logView === "line" && lineLog && (
                <div style={{ overflow: "auto", maxHeight: 440, border: `1px solid ${C.line}`, borderRadius: 8 }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 900 }}>
                    <thead><tr>{["Time", "Zone", "Line", "Cameras", "Online", "Ping OK, no video", "Offline", "CMS down", "Cutting", "Not cutting", "Idle"].map((h, i) =>
                      <th key={h} style={i > 2 ? thR : th}
                          title={h === "Ping OK, no video" ? "Hung + wrong address" : undefined}>{h}</th>)}</tr></thead>
                    <tbody>
                      {lineLog.rows.map((r, i) => (
                        <tr key={i}>
                          <td style={td}>{dt(r.ts)}</td><td style={{ ...td, color: C.sub }}>{r.zone_name}</td><td style={{ ...td, fontWeight: 700 }}>{r.line_name}</td>
                          {cell(r.cameras, C.fg)}{cell(r.online, "#16a34a")}{cell(r.hung, "#ea580c")}{cell(r.offline, "#dc2626")}
                          {cell(r.cms_down, "#b91c1c")}{cell(r.cutting, "#16a34a")}{cell(r.not_cutting, "#dc2626")}{cell(r.idle, C.sub)}
                        </tr>
                      ))}
                      {!lineLog.rows.length && <tr><td colSpan={11}><Empty>No snapshots yet.</Empty></td></tr>}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        );
      })()}

      {/* ── MISSING CYCLES ──────────────────────────────────────────── */}
      {tab === "missing" && (
        <>
          <div style={{ ...card, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", padding: 10 }}>
            <select value={mReason} onChange={e => { setMReason(e.target.value); setPage(1); loadMissing(1, e.target.value, mMachine); }} style={inp}>
              <option value="">All reasons</option>
              {Object.entries(REASON).map(([rk, [lb]]) => <option key={rk} value={rk}>{lb}</option>)}
            </select>
            {mMachine && <button onClick={() => { setMMachine(""); loadMissing(1, mReason, ""); }} style={btn}>
              Machine: {miss?.rows?.find(r => r.machine_key === mMachine)?.machine_name || mMachine} ✕</button>}
            <input value={mSearch} onChange={e => setMSearch(e.target.value)} placeholder="Search cycle # / part code / IP" style={{ ...inp, minWidth: 220 }} />
            {miss && <span style={{ fontSize: 12.5, color: C.sub, marginLeft: "auto" }}>{num(miss.total)} cycles without a clip</span>}
          </div>
          <div style={{ ...card, padding: 0, overflow: "auto", maxHeight: "calc(100vh - 330px)" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 1180 }}>
              <thead><tr>
                <th style={th}>Time</th><th style={th}>Line</th><th style={th}>Machine</th><th style={thR}>Cycle #</th>
                <th style={th}>Reason</th><th style={th}>Result</th><th style={thR}>CT (s)</th><th style={th}>Shift</th>
                <th style={th}>Camera IP</th><th style={th}>Part code</th><th style={th}>Detail</th></tr></thead>
              <tbody>
                {missRows.map((r, i) => {
                  const [lb, col] = REASON[r.reason] || [r.reason_label, C.sub];
                  return (
                    <tr key={i}>
                      <td style={td}>{r.record_date.slice(8, 10)}/{r.record_date.slice(5, 7)} {hms(r.ts_end)}</td>
                      <td style={{ ...td, fontWeight: 700 }}>{r.line_name}</td>
                      <td style={{ ...td, whiteSpace: "normal", minWidth: 150, maxWidth: 230 }}>{r.machine_key === "main" ? "★ " : ""}{r.machine_name}</td>
                      <td style={tdR}>{r.cycle_seq}</td>
                      <td style={td}><Pill text={lb} color={col} /></td>
                      <td style={{ ...td, color: r.is_ng ? "#dc2626" : "#16a34a", fontWeight: 700 }}>{r.is_ng ? "NG" : "OK"}</td>
                      <td style={tdR}>{r.ct == null ? "—" : r.ct.toFixed(1)}</td>
                      <td style={td}>{r.shift}</td>
                      <td style={{ ...td, color: C.sub }}>{r.camera_ip || "—"}</td>
                      <td style={{ ...td, color: C.sub }}>{r.part_code || "—"}</td>
                      <td style={{ ...td, color: C.sub, whiteSpace: "normal", minWidth: 200, maxWidth: 300 }}>{r.detail}</td>
                    </tr>);
                })}
                {miss && !missRows.length && <tr><td colSpan={11}><Empty>No missing clips for this filter.</Empty></td></tr>}
              </tbody>
            </table>
          </div>
          {miss && miss.total > miss.page_size && (
            <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12.5 }}>
              <button disabled={page <= 1} onClick={() => { const p = page - 1; setPage(p); loadMissing(p); }} style={btn}>‹ Prev</button>
              <span>Page {page} of {Math.ceil(miss.total / miss.page_size)}</span>
              <button disabled={page * miss.page_size >= miss.total} onClick={() => { const p = page + 1; setPage(p); loadMissing(p); }} style={btn}>Next ›</button>
            </div>
          )}
        </>
      )}

      {/* ── CAMERAS (until Camera Status is available) ─────────────── */}
      {tab === "cameras" && cams && (
        <>
          <div style={{ ...card, display: "flex", gap: 6, flexWrap: "wrap", padding: 10 }}>
            <button onClick={() => setCamState("")} style={{ ...btn, fontWeight: camState === "" ? 800 : 500 }}>All · {cams.cameras.length}</button>
            {Object.entries(STATE).map(([s, [lb, col]]) => {
              const n = cams.cameras.filter(c => c.state === s).length;
              return n ? <button key={s} onClick={() => setCamState(s)}
                                 style={{ ...btn, borderColor: col, color: col, fontWeight: camState === s ? 800 : 500 }}>{lb} · {n}</button> : null;
            })}
          </div>
          <div style={{ ...card, padding: 0, overflow: "auto", maxHeight: "calc(100vh - 330px)" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 860 }}>
              <thead><tr>{["State", "Camera IP", "Used by", "Since", "Last sample"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
              <tbody>
                {cams.cameras.filter(c => !camState || c.state === camState).map(c => {
                  const [lb, col] = STATE[c.state] || [c.state, C.sub];
                  return (
                    <tr key={c.camera_id}>
                      <td style={td}><Pill text={lb + (c.subnet_down ? " · subnet" : "")} color={col} /></td>
                      <td style={{ ...td, fontWeight: 700 }}>{c.ip || "—"}</td>
                      <td style={{ ...td, whiteSpace: "normal" }}>{c.bound_to.join(", ") || "—"}</td>
                      <td style={{ ...td, color: C.sub }}>{dt(c.since)}</td>
                      <td style={{ ...td, color: C.sub }}>{hhmm(c.last_seen)}</td>
                    </tr>);
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* ── VIDEO AGENT ─────────────────────────────────────────────── */}
      {tab === "agent" && agent && (
        <>
          <div style={kpiRow}>
            <Kpi label="Open findings" value={num(openFindings)} color={openFindings ? "#ea580c" : "#16a34a"} />
            <Kpi label="Tracking since" value={<span style={{ fontSize: 16 }}>{dt(agent.status.tracking_since)}</span>} color={C.accent} />
            <Kpi label="Camera sample" value={<span style={{ fontSize: 16 }}>{hms(agent.status.last_sample)}</span>} color={C.accent} sub="every minute" />
            <Kpi label="Cycle check" value={<span style={{ fontSize: 16 }}>{hms(agent.status.last_eval)}</span>} color={C.accent} sub="every 2 minutes" />
            <Kpi label="Admin notice" value={<span style={{ fontSize: 16 }}>{dt(agent.status.last_notify)}</span>} color={C.accent} sub="max every 15 min" />
          </div>
          {agent.status.last_error && <div style={{ ...card, borderLeft: "4px solid #dc2626", fontSize: 12.5 }}>Last error: {agent.status.last_error}</div>}
          <div style={{ ...card, padding: 0, overflow: "auto" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 14px 8px", flexWrap: "wrap" }}>
              <b style={{ fontSize: 13 }}>Findings</b>
              <button onClick={() => setAgentAll(false)} style={{ ...btn, fontWeight: !agentAll ? 800 : 500 }}>Open</button>
              <button onClick={() => setAgentAll(true)} style={{ ...btn, fontWeight: agentAll ? 800 : 500 }}>Last 24 h</button>
              <span style={{ marginLeft: "auto", fontSize: 12, color: C.sub }}>Read-only · never restarts or changes anything</span>
            </div>
            <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 900 }}>
              <thead><tr>{["Status", "Kind", "Line", "Finding", "Opened", "Last seen", "Closed"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
              <tbody>
                {agent.findings.filter(f => agentAll || !f.closed_at).map(f => (
                  <tr key={f.id}>
                    <td style={td}><Pill text={f.closed_at ? "Resolved" : "Open"} color={f.closed_at ? "#16a34a" : "#ea580c"} /></td>
                    <td style={{ ...td, fontWeight: 600 }}>{f.kind.replace("_", " ").toLowerCase().replace(/^\w/, ch => ch.toUpperCase())}</td>
                    <td style={td}>{f.line_name || "—"}</td>
                    <td style={{ ...td, whiteSpace: "normal" }}>{f.message}</td>
                    <td style={{ ...td, color: C.sub }}>{dt(f.opened_at)}</td>
                    <td style={{ ...td, color: C.sub }}>{hhmm(f.last_seen)}</td>
                    <td style={{ ...td, color: C.sub }}>{f.closed_at ? dt(f.closed_at) : "—"}</td>
                  </tr>
                ))}
                {!agent.findings.filter(f => agentAll || !f.closed_at).length && <tr><td colSpan={7}><Empty>No findings.</Empty></td></tr>}
              </tbody>
            </table>
          </div>
          <div style={{ ...card, fontSize: 12, color: C.sub }}>
            <div style={{ ...h3, color: C.fg }}>Rules</div>
            <ul style={{ margin: 0, paddingLeft: 18 }}>{agent.rules.map(r => <li key={r} style={{ margin: "3px 0" }}>{r}</li>)}</ul>
          </div>
        </>
      )}
    </div>
  );
}
