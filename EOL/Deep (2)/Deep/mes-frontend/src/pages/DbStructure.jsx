/* DB Structure (Admin) — 2026-10-29
 * A real-time tree of the database: groups -> tables -> columns, with live row
 * counts, sizes, a live insert rate (rows/s) and current DB activity.
 * Read-only.  Data: /api/admin/db/structure (once) + /api/admin/db/live (poll). */
import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useAuth } from "../context/AuthContext";
import PageTopbar from "../components/PageTopbar";

const api = async (p, t) => {
  const r = await fetch(p, { headers: t ? { Authorization: `Bearer ${t}` } : {} });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
};
const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString("en-IN"));
const C = { card: "#fff", border: "#e2e8f0", dim: "#64748b", accent: "#2563eb", ink: "#0f172a" };

export default function DbStructure() {
  const { token } = useAuth();
  const [struct, setStruct] = useState(null);
  const [live, setLive] = useState(null);
  const [rate, setRate] = useState({});          // table -> inserts/sec
  const [exact, setExact] = useState({});        // table -> exact count
  const [openG, setOpenG] = useState({});        // group -> bool
  const [openT, setOpenT] = useState({});        // table -> bool
  const [q, setQ] = useState("");
  const [auto, setAuto] = useState(true);
  const [err, setErr] = useState("");
  const prev = useRef(null);                     // {ts, tables}

  useEffect(() => { document.title = "DB Structure"; }, []);
  useEffect(() => {
    api("/api/admin/db/structure", token)
      .then(d => { setStruct(d); const g = {}; (d.groups || [])[0] && (g[d.groups[0].group] = true); setOpenG(g); })
      .catch(e => setErr(String(e.message || e)));
  }, [token]);

  const pull = useCallback(() => {
    api("/api/admin/db/live", token).then(d => {
      setLive(d);
      const now = Date.now();
      if (prev.current) {
        const dt = (now - prev.current.ts) / 1000;
        const r = {};
        for (const [name, v] of Object.entries(d.tables || {})) {
          const p = prev.current.tables[name];
          if (p && dt > 0) { const dr = (v.inserts - p.inserts) / dt; if (dr > 0.2) r[name] = dr; }
        }
        setRate(r);
      }
      prev.current = { ts: now, tables: d.tables || {} };
    }).catch(() => {});
  }, [token]);
  useEffect(() => { pull(); }, [pull]);
  useEffect(() => { if (!auto) return; const t = setInterval(pull, 4000); return () => clearInterval(t); }, [auto, pull]);

  const rowsOf = (name, est) => (exact[name] != null ? exact[name] : (live?.tables?.[name]?.est_rows ?? est));
  const sizeOf = (name, s) => live?.tables?.[name]?.size || s;

  const getExact = (name) => {
    setExact(e => ({ ...e, [name]: "…" }));
    api(`/api/admin/db/live?count=${encodeURIComponent(name)}`, token)
      .then(d => setExact(e => ({ ...e, [name]: d.exact?.rows ?? "err" })))
      .catch(() => setExact(e => ({ ...e, [name]: "err" })));
  };

  const ql = q.trim().toLowerCase();
  const groups = useMemo(() => {
    if (!struct) return [];
    if (!ql) return struct.groups;
    return struct.groups.map(g => ({
      ...g,
      tables: g.tables.filter(t => t.name.toLowerCase().includes(ql) ||
        t.columns.some(c => c.name.toLowerCase().includes(ql))),
    })).filter(g => g.tables.length);
  }, [struct, ql]);
  // when searching, auto-open matching groups
  useEffect(() => { if (ql) { const g = {}; groups.forEach(x => g[x.group] = true); setOpenG(g); } }, [ql]); // eslint-disable-line

  // table -> its group, so a relationship link can jump to a connected table
  const nameToGroup = useMemo(() => {
    const m = {};
    (struct?.groups || []).forEach(g => g.tables.forEach(t => { m[t.name] = g.group; }));
    return m;
  }, [struct]);
  const openTable = useCallback((name) => {
    const g = nameToGroup[name];
    if (!g) return;
    setOpenG(o => ({ ...o, [g]: true }));
    setOpenT(o => ({ ...o, [name]: true }));
    setTimeout(() => {
      const el = document.getElementById(`tbl-${name}`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 60);
  }, [nameToGroup]);

  const act = live?.activity;
  const Pill = ({ label, value, color }) => (
    <div style={{ background: "#f8fafc", border: `1px solid ${C.border}`, borderRadius: 10, padding: "6px 12px" }}>
      <span style={{ fontSize: 10, fontWeight: 700, color: C.dim, textTransform: "uppercase" }}>{label} </span>
      <span style={{ fontSize: 14, fontWeight: 800, color: color || C.ink, fontFamily: "monospace" }}>{value}</span>
    </div>
  );

  return (
    <div style={{ padding: "20px 24px", maxWidth: 1200, margin: "0 auto" }}>
      <PageTopbar leading="DB" accent="Structure" />
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginTop: 12 }}>
        {struct && <span style={{ fontSize: 13, color: C.dim }}>
          {struct.database} · <b>{struct.size}</b> · {struct.table_count} tables</span>}
        <label style={{ marginLeft: "auto", fontSize: 12, color: C.dim, display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} /> live (4s)
        </label>
        <button onClick={pull} style={{ padding: "7px 14px", borderRadius: 8, border: "none", background: C.accent, color: "#fff", fontWeight: 700, fontSize: 12, cursor: "pointer" }}>Refresh</button>
      </div>

      {/* live activity */}
      {act && <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "14px 0" }}>
        {Object.entries(act.by_state).map(([s, n]) =>
          <Pill key={s} label={s} value={n} color={s === "active" ? "#16a34a" : s === "idle in transaction" ? "#b45309" : C.ink} />)}
        <Pill label="lock waiters" value={act.lock_waiters} color={act.lock_waiters ? "#dc2626" : "#16a34a"} />
      </div>}
      {act?.active?.length > 0 && (
        <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 12, padding: 12, marginBottom: 16 }}>
          <div style={{ fontSize: 11, fontWeight: 800, color: C.dim, textTransform: "uppercase", marginBottom: 6 }}>Running now ({act.active.length})</div>
          {act.active.map((a, i) => (
            <div key={i} style={{ fontSize: 11.5, fontFamily: "monospace", color: C.ink, padding: "3px 0", borderTop: i ? `1px solid #f1f5f9` : "none", display: "flex", gap: 8 }}>
              <span style={{ color: a.age_s > 5 ? "#dc2626" : C.dim, flexShrink: 0, width: 46 }}>{a.age_s ?? "?"}s</span>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.query}</span>
            </div>
          ))}
        </div>
      )}

      <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search table or column…"
             style={{ width: "100%", padding: "9px 14px", borderRadius: 10, border: `1.5px solid ${C.border}`, fontSize: 13, marginBottom: 14, boxSizing: "border-box" }} />

      {err && <div style={{ color: "#dc2626", fontSize: 13 }}>{err}</div>}
      {!struct && !err && <div style={{ color: C.dim }}>Loading…</div>}

      {/* tree */}
      {groups.map(g => (
        <div key={g.group} style={{ marginBottom: 8 }}>
          <div onClick={() => setOpenG(o => ({ ...o, [g.group]: !o[g.group] }))}
               style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", padding: "9px 12px",
                        background: "#f1f5f9", borderRadius: 10, fontWeight: 800, fontSize: 14, color: C.ink }}>
            <span style={{ width: 14 }}>{openG[g.group] ? "▾" : "▸"}</span>
            {g.group}
            <span style={{ marginLeft: "auto", fontSize: 12, fontWeight: 700, color: C.dim }}>
              {g.tables.length} tables · {prettyBytes(g.bytes)}</span>
          </div>
          {openG[g.group] && g.tables.map(t => {
            const r = rate[t.name];
            return (
              <div key={t.name} id={`tbl-${t.name}`} style={{ marginLeft: 14, borderLeft: `2px solid ${C.border}` }}>
                <div onClick={() => setOpenT(o => ({ ...o, [t.name]: !o[t.name] }))}
                     style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", padding: "7px 12px", fontSize: 13 }}>
                  <span style={{ width: 12, color: C.dim }}>{openT[t.name] ? "▾" : "▸"}</span>
                  <span style={{ fontFamily: "monospace", fontWeight: 700, color: C.ink }}>{t.name}</span>
                  {r && <span style={{ fontSize: 10, fontWeight: 800, color: "#16a34a", background: "rgba(22,163,74,.1)", padding: "1px 6px", borderRadius: 999 }}>▲ {r.toFixed(r < 10 ? 1 : 0)}/s</span>}
                  <span style={{ marginLeft: "auto", fontSize: 12, color: C.dim }}>
                    <b style={{ color: C.accent, fontFamily: "monospace" }}>{fmt(rowsOf(t.name, t.est_rows))}</b>
                    {exact[t.name] == null && <span style={{ fontSize: 10 }}> est</span>} rows · {sizeOf(t.name, t.size)} · {t.n_cols} cols
                  </span>
                  <button onClick={e => { e.stopPropagation(); getExact(t.name); }}
                          style={{ fontSize: 10, padding: "2px 8px", borderRadius: 6, border: `1px solid ${C.border}`, background: "#fff", color: C.accent, cursor: "pointer", fontWeight: 700 }}>
                    {exact[t.name] === "…" ? "…" : "exact"}</button>
                </div>
                {openT[t.name] && (
                  <div style={{ marginLeft: 26, padding: "4px 0 12px", display: "flex", flexDirection: "column", gap: 10 }}>
                    {/* relationships: which tables this one connects to / is used by */}
                    {(t.refs?.length > 0 || t.ref_by?.length > 0) && (
                      <div style={{ display: "flex", gap: 18, flexWrap: "wrap", background: "#f8fafc", border: `1px solid ${C.border}`, borderRadius: 10, padding: "8px 12px" }}>
                        {t.refs?.length > 0 && (
                          <div style={{ minWidth: 210 }}>
                            <div style={{ fontSize: 10, fontWeight: 800, color: C.dim, textTransform: "uppercase", marginBottom: 4 }}>🔗 Connects to ({t.refs.length})</div>
                            {t.refs.map((r, i) => (
                              <div key={i} style={{ fontSize: 11.5, padding: "2px 0", fontFamily: "monospace" }}>
                                <span style={{ color: C.ink }}>{r.col}</span>
                                <span style={{ color: C.dim }}> → </span>
                                <span onClick={() => openTable(r.to)} title={r.inferred ? "inferred from column name" : "foreign key"}
                                      style={{ color: r.inferred ? "#94a3b8" : C.accent, cursor: "pointer", fontStyle: r.inferred ? "italic" : "normal", textDecoration: "underline" }}>
                                  {r.to}.{r.rcol}</span>
                                {r.inferred && <span style={{ fontSize: 9, color: "#94a3b8" }}> ~</span>}
                              </div>
                            ))}
                          </div>
                        )}
                        {t.ref_by?.length > 0 && (
                          <div style={{ minWidth: 210, flex: 1 }}>
                            <div style={{ fontSize: 10, fontWeight: 800, color: C.dim, textTransform: "uppercase", marginBottom: 4 }}>⬅ Used by ({t.ref_by.length})</div>
                            <div style={{ maxHeight: 150, overflowY: "auto", display: "flex", flexWrap: "wrap", gap: "3px 10px" }}>
                              {t.ref_by.map((r, i) => (
                                <span key={i} onClick={() => openTable(r.table)} title={`${r.table}.${r.col}${r.inferred ? " (inferred)" : " (foreign key)"}`}
                                      style={{ fontSize: 11, fontFamily: "monospace", color: r.inferred ? "#64748b" : C.accent, cursor: "pointer", textDecoration: r.inferred ? "underline dotted" : "underline" }}>
                                  {r.table}</span>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                    {/* columns (FK / linked columns marked) */}
                    <table style={{ borderCollapse: "collapse", fontSize: 12 }}>
                      <tbody>
                        {t.columns.map((c, i) => (
                          <tr key={i}>
                            <td style={{ padding: "2px 16px 2px 0", fontFamily: "monospace", color: C.ink, fontWeight: c.ref ? 700 : 400 }}>{c.name}</td>
                            <td style={{ padding: "2px 16px 2px 0", color: "#0f766e", fontFamily: "monospace" }}>{c.type}</td>
                            <td style={{ padding: "2px 16px 2px 0", color: "#94a3b8", fontSize: 10 }}>{c.nullable ? "null" : "not null"}</td>
                            <td style={{ padding: "2px 0", fontFamily: "monospace", fontSize: 11 }}>
                              {c.ref && <span onClick={() => openTable(c.ref.to)} title={c.ref.inferred ? "inferred link" : "foreign key"}
                                              style={{ color: c.ref.inferred ? "#94a3b8" : C.accent, cursor: "pointer", fontStyle: c.ref.inferred ? "italic" : "normal" }}>
                                🔗 {c.ref.to}</span>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function prettyBytes(b) {
  if (b == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0, n = b;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}
