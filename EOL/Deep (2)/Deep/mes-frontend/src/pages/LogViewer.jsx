// LogViewer.jsx — read every log in the stack, filtered.
//
// Operator: "ek log view page bhi bana de for all log by filters, also by code
// 400 402 404 etc — aur ye page assignable ho baaki pages ki tarah."
//
// Two things this page is careful about, both learned the hard way on this box:
//   * the LIVE logs are in logs/, while Phase2/logs/ still holds stale copies
//     from July — the file list shows how old each file is so nobody reads a
//     two-month-old error and chases it;
//   * MES-API.log has been 144 GB.  The server only ever reads the tail, and
//     this page says how much it scanned, so "12 matches" is never mistaken
//     for "only 12 exist".
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";

//  2026-09-27 — operator: *"collector ki log viewer me ek tab or add kr de
//  jisse m collector ki live read value dekh pau sab collectors ki"*.
//
//  Every collector already prints a COLLECTOR STATUS block with the values it
//  just read off the PLC, so the server parses that instead of anything new
//  being added to the collectors.  What you see here is what the collector
//  acted on — not a fresh read by the API, which could not happen anyway: the
//  PLC allows one session and the collector holds it.
function LiveReads({ token, dark, card, inp }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [only, setOnly] = useState("");     // "" | problems
  const [auto, setAuto] = useState(true);

  const load = useCallback(async () => {
    try { setD(await api.get("/api/logs/collector-reads", token)); setErr(""); }
    catch (e) {
      setErr(String(e?.message || e).includes("404")
        ? "Available after the next MES-API restart." : (e?.message || "Could not load."));
    }
  }, [token]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!auto) return;
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [auto, load]);

  const cols = useMemo(() => (d?.collectors || []).filter(c => {
    if (q.trim()) {
      const hay = `${c.collector} ${c.line_name} ${(c.machines || [])
        .map(m => m.machine).join(" ")}`.toLowerCase();
      if (!hay.includes(q.trim().toLowerCase())) return false;
    }
    if (only === "problems") {
      const bad = (c.machines || []).some(m => !m.plc_ok || !m.db_ok);
      return bad || c.age_s > 120 || c.error || c.andon?.open;
    }
    return true;
  }), [d, q, only]);

  const sub = dark ? "#94a3b8" : "#64748b";
  const line = dark ? "#243049" : "#e2e8f0";
  const th = { textAlign: "left", padding: "6px 8px", fontSize: 10.5, fontWeight: 700,
               textTransform: "uppercase", color: sub, borderBottom: `1px solid ${line}`,
               whiteSpace: "nowrap" };
  const td = { padding: "5px 8px", fontSize: 12, borderBottom: `1px solid ${line}`,
               whiteSpace: "nowrap" };
  const flag = (ok) => (
    <span style={{ color: ok ? "#15803d" : "#b91c1c", fontWeight: 800 }}>{ok ? "OK" : "DOWN"}</span>);

  const totalMachines = (d?.collectors || []).reduce((n, c) => n + (c.machines || []).length, 0);
  const downMachines = (d?.collectors || []).reduce(
    (n, c) => n + (c.machines || []).filter(m => !m.plc_ok).length, 0);

  return (
    <div>
      <div style={{ ...card, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <input value={q} onChange={e => setQ(e.target.value)}
               placeholder="Search collector / line / machine"
               style={{ ...inp, minWidth: 240 }} />
        <select value={only} onChange={e => setOnly(e.target.value)} style={inp}>
          <option value="">All collectors</option>
          <option value="problems">Only problems</option>
        </select>
        <button onClick={load} style={{ ...inp, cursor: "pointer", fontWeight: 700 }}>Refresh</button>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, color: sub }}>
          <input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} />
          Auto every 10 s
        </label>
        <span style={{ marginLeft: "auto", fontSize: 12.5, color: sub }}>
          {d?.count ?? 0} collectors · {totalMachines} machines
          {downMachines > 0 && <b style={{ color: "#b91c1c" }}> · {downMachines} PLC down</b>}
        </span>
      </div>

      {err && <div style={{ ...card, borderLeft: "4px solid #dc2626", color: "#dc2626" }}>{err}</div>}
      {d && <div style={{ fontSize: 11.5, color: sub, margin: "0 0 10px" }}>{d.note}</div>}
      {d && (
        <div style={{ fontSize: 11.5, color: sub, margin: "-6px 0 10px" }}>
          <b>IDLE</b> means the machine's own status register says so — it is not a fault.
          The <b>reason</b> a line is stopped is raised on the Andon, shown beside each line
          when there is one ({d.andon_lines ?? 0} lines reported an Andon in the last 24 h).
        </div>)}

      {cols.map(c => (
        <div key={c.log} style={{ ...card, padding: 0, overflow: "hidden" }}>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap",
                        padding: "8px 12px", borderBottom: `1px solid ${line}` }}>
            <b style={{ fontSize: 13.5 }}>{c.line_name || c.collector}</b>
            <span style={{ fontSize: 11.5, color: sub }}>{c.collector}</span>
            {c.shift && <span style={{ fontSize: 11.5, color: sub }}>shift {c.shift}</span>}
            <span style={{ fontSize: 11.5, color: c.age_s > 120 ? "#b45309" : sub }}>
              printed {c.printed_at || "—"}{c.age_s != null ? ` · log ${Math.round(c.age_s)}s old` : ""}
            </span>
            {c.andon && (
              <span title={`Andon raised on the maintenance side${c.andon.started_at
                              ? " at " + new Date(c.andon.started_at).toLocaleString("en-GB",
                                  { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short" })
                              : ""}`}
                    style={{ fontSize: 11.5, fontWeight: 800, padding: "2px 9px", borderRadius: 999,
                             background: c.andon.open ? "#fee2e2" : "#f1f5f9",
                             color: c.andon.open ? "#b91c1c"
                                    : (dark ? "#94a3b8" : "#64748b") }}>
                {c.andon.open ? "ANDON NOW" : "last andon"} · {c.andon.call}
                {c.andon.priority ? ` (${c.andon.priority})` : ""}
                {c.andon.today ? ` · ${c.andon.today} in 24 h` : ""}
              </span>)}
            {c.fault_watch && (
              <span style={{ fontSize: 11.5, color: "#1e40af", fontWeight: 700 }}>{c.fault_watch}</span>)}
            {c.error && <span style={{ fontSize: 11.5, color: "#b91c1c" }}>{c.error}</span>}
          </div>
          {(c.machines || []).length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 940 }}>
                <thead><tr>
                  {["Machine", "PLC", "DB", "Count reg", "Value", "NG reg", "NG val",
                    "Status", "Plan", "Actual", "CT", "Speed loss", "Model", "Part code"]
                    .map(h => <th key={h} style={th}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {c.machines.map((m, i) => (
                    <tr key={i}>
                      <td style={{ ...td, fontWeight: 600 }}>{m.machine}</td>
                      <td style={td}>{flag(m.plc_ok)}</td>
                      <td style={td}>{flag(m.db_ok)}</td>
                      <td style={{ ...td, fontFamily: "monospace" }}>{m.reg}</td>
                      <td style={{ ...td, fontFamily: "monospace", fontWeight: 800 }}>{m.value}</td>
                      <td style={{ ...td, fontFamily: "monospace" }}>{m.ngreg}</td>
                      <td style={{ ...td, fontFamily: "monospace", fontWeight: 800 }}>{m.ngval}</td>
                      <td style={{ ...td, color: m.status === "RUNNING" ? "#15803d"
                                          : m.status === "IDLE" ? sub : "#b45309",
                                  fontWeight: 700 }}>{m.status}</td>
                      <td style={td}>{m.plan}</td>
                      <td style={td}>{m.actual}</td>
                      <td style={td}>{m.ct}</td>
                      <td style={td}>{m.spdloss}</td>
                      <td style={td}>{m.model}</td>
                      <td style={td}>{m.partcode}</td>
                    </tr>))}
                </tbody>
              </table>
            </div>)}
        </div>))}

      {d && cols.length === 0 && (
        <div style={{ ...card, textAlign: "center", color: sub }}>
          No collector matches this filter.
        </div>)}
    </div>
  );
}

//  2026-09-27 — operator: *"port and their status and their services in a new
//  tab called ports jisme mere system k sare ports ho with description and
//  activeness time"*.  Read-only by their choice: nothing here can stop or
//  start a service.
//
//  "Uptime" is how long the PROCESS holding the port has been running, so a
//  service that was restarted an hour ago reads 1h even though the machine has
//  been up for days.
function Ports({ token, dark, card, inp }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [only, setOnly] = useState("");     // "" | mes | lan | down
  const [auto, setAuto] = useState(false);

  const load = useCallback(async () => {
    try { setD(await api.get("/api/logs/ports", token)); setErr(""); }
    catch (e) {
      setErr(String(e?.message || e).includes("404")
        ? "Available after the next MES-API restart." : (e?.message || "Could not load ports."));
    }
  }, [token]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!auto) return;
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [auto, load]);

  const upFmt = (s) => {
    if (s == null) return "—";
    if (s < 60) return `${Math.round(s)}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
    return `${(s / 86400).toFixed(1)}d`;
  };

  const rows = useMemo(() => (d?.ports || []).filter(r => {
    if (only === "mes"  && !r.is_mes)   return false;
    if (only === "lan"  && !r.lan_open) return false;
    if (only === "down" && r.responding) return false;
    if (q.trim()) {
      const hay = `${r.port} ${r.service} ${r.process || ""} ${r.description || ""}`.toLowerCase();
      if (!hay.includes(q.trim().toLowerCase())) return false;
    }
    return true;
  }), [d, q, only]);

  const sub  = dark ? "#94a3b8" : "#64748b";
  const line = dark ? "#243049" : "#e2e8f0";
  const th = { textAlign: "left", padding: "7px 10px", fontSize: 10.5, fontWeight: 700,
               textTransform: "uppercase", color: sub, borderBottom: `1px solid ${line}`,
               whiteSpace: "nowrap" };
  const td = { padding: "7px 10px", fontSize: 12.5, borderBottom: `1px solid ${line}`,
               verticalAlign: "top" };

  return (
    <div>
      <div style={{ ...card, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <input value={q} onChange={e => setQ(e.target.value)}
               placeholder="Search port / service / process"
               style={{ ...inp, minWidth: 250 }} />
        <select value={only} onChange={e => setOnly(e.target.value)} style={inp}>
          <option value="">All ports</option>
          <option value="mes">MES / plant services</option>
          <option value="lan">Open to the plant network</option>
          <option value="down">Not responding</option>
        </select>
        <button onClick={load} style={{ ...inp, cursor: "pointer", fontWeight: 700 }}>Refresh</button>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, color: sub }}>
          <input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} />
          Auto every 15 s
        </label>
        <span style={{ marginLeft: "auto", fontSize: 12.5, color: sub }}>
          {d?.count ?? 0} listening · <b style={{ color: "#b45309" }}>{d?.lan_open ?? 0}</b> open to the LAN
          {d && ` · ${d.identified} identified`}
        </span>
      </div>

      {err && <div style={{ ...card, borderLeft: "4px solid #dc2626", color: "#dc2626" }}>{err}</div>}
      {d && <div style={{ fontSize: 11.5, color: sub, margin: "0 0 10px" }}>{d.note}</div>}

      <div style={{ ...card, padding: 0, overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 900 }}>
          <thead><tr>
            {["Port", "Service", "Status", "Reachable from", "Process", "Uptime", "What it is"]
              .map(h => <th key={h} style={th}>{h}</th>)}
          </tr></thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={7} style={{ padding: 22, textAlign: "center", color: sub }}>
                No port matches this filter.</td></tr>)}
            {rows.map(r => (
              <tr key={r.port} style={r.is_mes ? { background: dark ? "#0f1729" : "#f8fafc" } : undefined}>
                <td style={{ ...td, fontFamily: "monospace", fontWeight: 800 }}>{r.port}</td>
                <td style={{ ...td, fontWeight: 700 }}>
                  {r.service}
                  {r.is_mes && <div style={{ fontSize: 10, color: "#1e40af", fontWeight: 700 }}>MES / PLANT</div>}
                </td>
                <td style={td}>
                  <span style={{ fontWeight: 800, color: r.responding ? "#15803d" : "#b91c1c" }}>
                    {r.responding ? "UP" : "NOT ANSWERING"}</span>
                </td>
                <td style={td}>
                  {r.lan_open
                    ? <span style={{ color: "#b45309", fontWeight: 700 }}
                            title="Bound to 0.0.0.0 — any PC on the plant network can reach it">
                        whole plant network</span>
                    : <span style={{ color: "#15803d", fontWeight: 700 }}
                            title="Bound to a loopback address">this server only</span>}
                  <div style={{ fontSize: 10.5, color: sub, fontFamily: "monospace" }}>
                    {(r.bind || []).join(", ")}</div>
                </td>
                <td style={{ ...td, fontFamily: "monospace", fontSize: 11.5 }}>
                  {r.process || <span style={{ color: sub, fontFamily: "inherit" }}>not identified</span>}
                  {r.pid && <div style={{ color: sub }}>pid {r.pid}</div>}
                </td>
                <td style={{ ...td, fontWeight: 700 }}>{upFmt(r.uptime_s)}</td>
                <td style={{ ...td, color: sub, whiteSpace: "normal", maxWidth: 360 }}>{r.description}</td>
              </tr>))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const LEVELS = [
  ["", "All levels"],
  ["error", "Errors only"],
  ["warning", "Warnings"],
  ["info", "Info"],
];
const WINDOWS = [
  ["", "All in tail"],
  ["15", "Last 15 min"],
  ["60", "Last 1 hour"],
  ["360", "Last 6 hours"],
  ["1440", "Last 24 hours"],
];

export default function LogViewer() {
  const { token, theme } = useAuth();
  const [files, setFiles]   = useState([]);
  const [file, setFile]     = useState("");
  const [lines, setLines]   = useState([]);
  const [codes, setCodes]   = useState([]);
  const [meta, setMeta]     = useState(null);
  const [q, setQ]           = useState("");
  const [level, setLevel]   = useState("");
  const [code, setCode]     = useState("");
  const [mins, setMins]     = useState("");
  const [limit, setLimit]   = useState(300);
  const [auto, setAuto]     = useState(false);
  const [busy, setBusy]     = useState(false);
  const [err, setErr]       = useState("");
  //  2026-09-27 — two views now: the log files, and the collectors' own
  //  live PLC reads.
  const [view, setView]     = useState("logs");

  const dark = theme === "dark";
  const bg   = dark ? "#0b1220" : "#f8fafc";
  const card = { background: dark ? "#131c30" : "#fff",
                 border: `1px solid ${dark ? "#243049" : "#e2e8f0"}`,
                 borderRadius: 10, padding: 12, marginBottom: 12 };
  const inp  = { padding: "7px 9px", borderRadius: 7, fontSize: 13,
                 border: `1px solid ${dark ? "#243049" : "#cbd5e1"}`,
                 background: dark ? "#0f1729" : "#fff",
                 color: dark ? "#e6edf7" : "#0f172a" };

  useEffect(() => {
    api.get("/api/logs/files", token)
      .then(d => {
        const f = d.files || [];
        setFiles(f);
        if (!file && f.length) setFile(f[0].id);
      })
      .catch(e => setErr(String(e.message || e)));
    // eslint-disable-next-line
  }, [token]);

  // `over` lets a caller pass a filter value that state has not caught up to
  // yet.  Clicking a code chip used to call load() straight after setCode(),
  // which still closed over the OLD code — the chip highlighted but the list
  // did not filter.
  const load = useCallback(async (over = {}) => {
    if (!file) return;
    const _q     = over.q     !== undefined ? over.q     : q;
    const _level = over.level !== undefined ? over.level : level;
    const _code  = over.code  !== undefined ? over.code  : code;
    const _mins  = over.mins  !== undefined ? over.mins  : mins;
    setBusy(true); setErr("");
    try {
      const p = new URLSearchParams({ file, lines: String(limit) });
      if (_q.trim()) p.set("q", _q.trim());
      if (_level)    p.set("level", _level);
      if (_code)     p.set("code", _code.trim());
      if (_mins)     p.set("minutes", _mins);
      const d = await api.get(`/api/logs/tail?${p}`, token);
      setLines(d.lines || []);
      setMeta(d);
    } catch (e) { setErr(String(e.message || e)); setLines([]); }
    finally { setBusy(false); }
  }, [file, q, level, code, mins, limit, token]);

  // The code breakdown is what makes "which page is failing" a one-look answer.
  const loadCodes = useCallback(async () => {
    if (!file) return;
    try { setCodes((await api.get(`/api/logs/codes?file=${encodeURIComponent(file)}`, token)).codes || []); }
    catch { setCodes([]); }
  }, [file, token]);

  useEffect(() => { load(); loadCodes(); /* eslint-disable-next-line */ }, [file]);
  useEffect(() => {
    if (!auto) return;
    const t = setInterval(() => load(), 5000);
    return () => clearInterval(t);
  }, [auto, load]);

  const colour = (l) => {
    if (/\b(ERROR|CRITICAL|FATAL|Traceback|Exception)\b/i.test(l)) return "#ef4444";
    if (/\b(WARN|WARNING)\b/i.test(l)) return "#f59e0b";
    if (/\s5\d\d(\s|$)/.test(l)) return "#ef4444";
    if (/\s4\d\d(\s|$)/.test(l)) return "#f59e0b";
    return dark ? "#cbd5e1" : "#334155";
  };

  const current = useMemo(() => files.find(f => f.id === file), [files, file]);

  return (
    <div style={{ minHeight: "100vh", background: bg, padding: 16,
                  color: dark ? "#e6edf7" : "#0f172a" }}>
      <h2 style={{ margin: "0 0 12px", fontSize: 19, fontWeight: 800 }}>Log Viewer</h2>

      <div style={{ display: "flex", gap: 2, marginBottom: 14,
                    borderBottom: `1px solid ${dark ? "#243049" : "#e2e8f0"}` }}>
        {[["logs", "Log Files"], ["reads", "Collector Live Reads"], ["ports", "Ports"]].map(([k, lb]) => (
          <button key={k} onClick={() => setView(k)}
                  style={{ padding: "9px 16px", border: "none", background: "none",
                           cursor: "pointer", fontSize: 13.5,
                           fontWeight: view === k ? 800 : 600,
                           color: view === k ? "#1e40af" : (dark ? "#94a3b8" : "#64748b"),
                           borderBottom: view === k ? "3px solid #1e40af" : "3px solid transparent",
                           marginBottom: -1 }}>{lb}</button>))}
      </div>

      {view === "reads" && <LiveReads token={token} dark={dark} card={card} inp={inp} />}
      {view === "ports" && <Ports token={token} dark={dark} card={card} inp={inp} />}

      {view === "logs" && (<>
      <div style={card}>
        <div style={{ display: "grid", gap: 8,
                      gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))" }}>
          <div>
            <label style={{ fontSize: 11, opacity: .7 }}>LOG FILE</label>
            <select value={file} onChange={e => setFile(e.target.value)}
                    style={{ ...inp, width: "100%" }}>
              {files.map(f => (
                <option key={f.id} value={f.id}>
                  {f.id} — {f.size_mb} MB{f.age_min > 120 ? `  (stale, ${Math.round(f.age_min / 60)}h old)` : ""}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 11, opacity: .7 }}>SEARCH TEXT</label>
            <input value={q} onChange={e => setQ(e.target.value)}
                   onKeyDown={e => e.key === "Enter" && load()}
                   placeholder="e.g. Traceback, cycle-video" style={{ ...inp, width: "100%" }} />
          </div>
          <div>
            <label style={{ fontSize: 11, opacity: .7 }}>HTTP CODE</label>
            <input value={code} onChange={e => setCode(e.target.value)}
                   onKeyDown={e => e.key === "Enter" && load()}
                   placeholder="404, 500, or 4xx" style={{ ...inp, width: "100%" }} />
          </div>
          <div>
            <label style={{ fontSize: 11, opacity: .7 }}>LEVEL</label>
            <select value={level} onChange={e => setLevel(e.target.value)}
                    style={{ ...inp, width: "100%" }}>
              {LEVELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 11, opacity: .7 }}>TIME</label>
            <select value={mins} onChange={e => setMins(e.target.value)}
                    style={{ ...inp, width: "100%" }}>
              {WINDOWS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 11, opacity: .7 }}>MAX LINES</label>
            <select value={limit} onChange={e => setLimit(Number(e.target.value))}
                    style={{ ...inp, width: "100%" }}>
              {[100, 300, 1000, 3000].map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap",
                      alignItems: "center" }}>
          <button onClick={() => load()} disabled={busy}
                  style={{ ...inp, background: "#1e3a8a", color: "#fff",
                           border: "none", fontWeight: 800, cursor: "pointer" }}>
            {busy ? "Loading…" : "Search"}
          </button>
          <button onClick={() => { setQ(""); setCode(""); setLevel(""); setMins("");
                                   load({ q: "", code: "", level: "", mins: "" }); }}
                  style={{ ...inp, cursor: "pointer" }}>Clear filters</button>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5 }}>
            <input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} />
            Auto-refresh (5s)
          </label>
          {codes.length > 0 && (
            <div style={{ display: "flex", gap: 6, marginLeft: "auto", flexWrap: "wrap" }}>
              {codes.slice(0, 7).map(c => (
                <button key={c.code} onClick={() => { setCode(c.code); load({ code: c.code }); }}
                        title={`Show only HTTP ${c.code}`}
                        style={{ ...inp, padding: "3px 9px", fontSize: 11.5, cursor: "pointer",
                                 borderColor: c.code[0] === "5" ? "#ef4444"
                                            : c.code[0] === "4" ? "#f59e0b" : undefined }}>
                  {c.code} · {c.count.toLocaleString("en-IN")}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {err && <div style={{ ...card, borderLeft: "4px solid #ef4444" }}>{err}</div>}

      {meta && (
        <div style={{ fontSize: 12, opacity: .75, marginBottom: 6 }}>
          {meta.returned} lines shown · scanned {Number(meta.scanned).toLocaleString("en-IN")} lines
          of a {meta.size_mb} MB file
          {meta.truncated && " · only the newest part of the file was read"}
          {meta.undated > 0 &&
            ` · ⚠ ${meta.undated} of these lines carry no timestamp, so the time filter could not judge them`}
          {current && current.age_min > 120 &&
            ` · ⚠ this file has not been written for ${Math.round(current.age_min / 60)} h — it may be a stale copy`}
        </div>
      )}

      <div style={{ ...card, padding: 0, overflow: "hidden" }}>
        <div style={{ maxHeight: "calc(100vh - 300px)", overflow: "auto",
                      background: dark ? "#070c14" : "#fff" }}>
          {lines.length === 0 ? (
            <div style={{ padding: 22, textAlign: "center", opacity: .6, fontSize: 13 }}>
              {busy ? "Loading…" : "Koi line nahi mili — filter badal kar dekhiye."}
            </div>
          ) : lines.map((l, i) => (
            <div key={i} style={{
              fontFamily: "ui-monospace, Menlo, Consolas, monospace", fontSize: 11.8,
              padding: "2px 10px", whiteSpace: "pre-wrap", wordBreak: "break-word",
              color: colour(l),
              borderBottom: `1px solid ${dark ? "#111a2b" : "#f1f5f9"}` }}>
              {l}
            </div>
          ))}
        </div>
      </div>
      </>)}
    </div>
  );
}
