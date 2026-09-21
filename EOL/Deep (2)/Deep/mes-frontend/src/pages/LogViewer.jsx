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
    </div>
  );
}
