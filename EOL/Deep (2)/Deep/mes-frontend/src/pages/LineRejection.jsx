/**
 * Production → Line Rejection  (2026-11-11)
 *
 * Zone- and line-wise view of the rejections booked in the Red Bin portal
 * (redbin."RedBinRecord", read-only via /api/line-rejection).  The main page
 * keeps to the major details (zone → line qty, the rejection list); a click on
 * a record opens its Rejection Summary Report (components/RejectionSummary:
 * summary, details, defect and cause, status, pieces, child parts, rework,
 * status history, lock / QPR, photos, sign-off; printable).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import PageTopbar from "../components/PageTopbar";
import { RejectionReport, Badge, Photo, GROUPS, OPEN_ST, REWORK_ST, fmtDT } from "../components/RejectionSummary";

const pad2     = (n) => String(n).padStart(2, "0");
const localYmd = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
const daysAgo  = (n) => { const d = new Date(); d.setDate(d.getDate() - n); return localYmd(d); };
const zoneShort = (n) => (n || "").split(" - ").slice(1).join(" - ") || n || "—";

const lbl = { fontSize: 11, fontWeight: 800, color: "#475569", display: "block",
              marginBottom: 4, letterSpacing: ".04em" };
const inp = { width: "100%", padding: "7px 10px", borderRadius: 8, border: "1px solid #cbd5e1",
              fontSize: 13, background: "#fff", boxSizing: "border-box", height: 38 };
const field = { flex: "1 1 140px", minWidth: 130, maxWidth: 210 };
const card = { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12, padding: 14 };
const th = { padding: "9px 10px", fontWeight: 800, color: "#334155", whiteSpace: "nowrap",
             textAlign: "left", background: "#f1f5f9", borderBottom: "1px solid #e2e8f0",
             position: "sticky", top: 0, zIndex: 1 };
const td = { padding: "8px 10px", whiteSpace: "nowrap", borderBottom: "1px solid #f1f5f9" };
const h3 = { fontSize: 14, fontWeight: 900, color: "#0f172a", margin: "0 0 10px" };

/* Four-state bar (open / rework / scrap / back), 2px gaps, hover title. */
function StateBar({ b, height = 8 }) {
  const tot = GROUPS.reduce((s, g) => s + (b[g.key] || 0), 0);
  if (!tot) return <div style={{ height, borderRadius: 4, background: "#f1f5f9" }} />;
  return (
    <div style={{ display: "flex", gap: 2, height }}>
      {GROUPS.filter(g => b[g.key]).map(g => (
        <div key={g.key} title={`${g.label}: ${b[g.key]}`}
             style={{ flex: b[g.key], background: g.color, borderRadius: 4, minWidth: 4 }} />
      ))}
    </div>
  );
}

function Legend() {
  return (
    <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "#475569", fontWeight: 700 }}>
      {GROUPS.map(g => (
        <span key={g.key} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 10, height: 10, borderRadius: 3, background: g.color }} />{g.label}
        </span>
      ))}
    </div>
  );
}

function Kpi({ label, value, color }) {
  return (
    <div style={{ ...card, padding: "12px 14px", flex: "1 1 130px", minWidth: 120 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, fontWeight: 800,
                    color: "#64748b", letterSpacing: ".04em" }}>
        {color && <span style={{ width: 9, height: 9, borderRadius: 3, background: color }} />}
        {label}
      </div>
      <div style={{ fontSize: 26, fontWeight: 900, color: "#0f172a", marginTop: 2 }}>{value ?? "—"}</div>
    </div>
  );
}

/* ── Zone → line board ─────────────────────────────────────────────── */
const natural = (a, b) => String(a).localeCompare(String(b), undefined, { numeric: true });
const ZERO = { records: 0, qty: 0, open: 0, rework: 0, scrap: 0, back: 0 };

/* Every zone with every line (from the masters), counts from the summary. */
function boardZones(meta, sum) {
  const byZone = Object.fromEntries((sum?.zones || []).map(z => [z.id, z]));
  const out = meta.zones.map(mz => {
    const sz = byZone[mz.id] || { ...ZERO, lines: [] };
    const byLine = Object.fromEntries(sz.lines.map(l => [l.id, l]));
    const lines = mz.lines.map(l => ({ ...ZERO, ...byLine[l.id], id: l.id, name: l.name }));
    for (const l of sz.lines) if (!mz.lines.some(x => x.id === l.id)) lines.push(l);
    lines.sort((a, b) => natural(a.name, b.name));
    return { ...ZERO, ...sz, id: mz.id, code: mz.code, name: mz.name, lines };
  });
  for (const z of sum?.zones || []) if (!meta.zones.some(m => m.id === z.id)) out.push(z);
  return out;
}

function LineTile({ l, sel, onClick }) {
  const zero = !l.qty;
  const openN = l.open + l.rework;
  return (
    <div onClick={onClick}
         title={zero ? `${l.name}: no rejection` :
                `${l.name}: ${l.qty} pcs in ${l.records} record(s) · ` + GROUPS.map(g => `${g.label} ${l[g.key]}`).join(", ")}
         style={{ border: `1.5px solid ${sel ? "#2563eb" : "#e2e8f0"}`, borderRadius: 10,
                  padding: "9px 10px 8px", cursor: "pointer", background: sel ? "#eff6ff" : zero ? "#f8fafc" : "#fff",
                  boxShadow: sel ? "0 0 0 2px #bfdbfe" : "none" }}>
      <div style={{ fontSize: 12.5, fontWeight: 800, color: zero ? "#94a3b8" : "#334155",
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{l.name}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 5, margin: "2px 0 6px" }}>
        <span style={{ fontSize: 24, fontWeight: 900, color: zero ? "#cbd5e1" : "#0f172a", lineHeight: 1.1 }}>{l.qty}</span>
        <span style={{ fontSize: 11, color: "#64748b", fontWeight: 700 }}>pcs</span>
        {openN > 0 && <span title="Open + under rework" style={{ marginLeft: "auto", fontSize: 10.5, fontWeight: 800, color: "#b91c1c" }}>{openN} pending</span>}
      </div>
      <StateBar b={l} height={6} />
    </div>
  );
}

function ZoneBoard({ zones, zone, line, onPick }) {
  const shown = zones.filter(z => z.qty > 0 || z.id === zone);
  const empty = zones.filter(z => !(z.qty > 0) && z.id !== zone);
  return (
    <>
      {!shown.length && <div style={{ ...card, color: "#64748b", fontSize: 13 }}>No rejection in this period.</div>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(min(100%,430px),1fr))", gap: 12 }}>
        {shown.map(z => {
          const zSel = zone === z.id;
          return (
            <div key={z.id} style={{ ...card, borderColor: zSel ? "#2563eb" : "#e2e8f0" }}>
              <div onClick={() => onPick(zSel && !line ? "" : z.id, "")}
                   style={{ display: "flex", alignItems: "center", gap: 10, cursor: "pointer", marginBottom: 8 }}>
                <span style={{ background: "#0f172a", color: "#fff", fontSize: 11, fontWeight: 900,
                               borderRadius: 6, padding: "3px 7px" }}>{z.code}</span>
                <span style={{ fontWeight: 900, color: "#0f172a", fontSize: 15, flex: 1, minWidth: 0,
                               overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{zoneShort(z.name)}</span>
                <span style={{ fontSize: 12, color: "#475569", whiteSpace: "nowrap" }}>
                  <b style={{ fontSize: 20, color: "#0f172a" }}>{z.qty}</b> pcs · {z.records} rec
                </span>
              </div>
              <StateBar b={z} height={8} />
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(118px,1fr))", gap: 8, marginTop: 10 }}>
                {z.lines.map(l => (
                  <LineTile key={l.id || l.name} l={l} sel={!!line && line === l.id}
                            onClick={() => onPick(z.id, line === l.id ? "" : l.id)} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
      {empty.length > 0 && (
        <div style={{ fontSize: 12, color: "#64748b", marginTop: 8 }}>
          No rejection: {empty.map(z => zoneShort(z.name)).join(" · ")}
        </div>
      )}
    </>
  );
}

/* ── Page ──────────────────────────────────────────────────────────── */
const startOfWeek = () => { const d = new Date(); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return localYmd(d); };
const startOfMonth = () => { const d = new Date(); d.setDate(1); return localYmd(d); };
const RANGES = [
  { key: "today", label: "Today",        from: () => localYmd(new Date()) },
  { key: "week",  label: "This Week",    from: startOfWeek },
  { key: "month", label: "This Month",   from: startOfMonth },
  { key: "30",    label: "Last 30 Days", from: () => daysAgo(29) },
];

// 2026-11-12 — `embedded`: opened inside Control Board → Back side (no page
// header or full-height background of its own); the /line-rejection route
// still renders the full page.
export default function LineRejection({ embedded = false }) {
  const { token } = useAuth();
  const [meta, setMeta] = useState({ zones: [], shifts: [], statuses: [] });
  const [from, setFrom] = useState(daysAgo(29));
  const [to, setTo] = useState(localYmd(new Date()));
  const [zone, setZone] = useState("");
  const [line, setLine] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [qDeb, setQDeb] = useState("");
  const [test, setTest] = useState(false);
  const [sum, setSum] = useState(null);
  const [rows, setRows] = useState(null);
  const [more, setMore] = useState(false);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(null);
  const [big, setBig] = useState(null);

  useEffect(() => {
    api.get("/api/line-rejection/filters", token).then(setMeta).catch(e => setErr(e.message));
  }, [token]);
  useEffect(() => { const t = setTimeout(() => setQDeb(q), 400); return () => clearTimeout(t); }, [q]);

  const qs = useMemo(() => {
    const p = new URLSearchParams();
    if (from) p.set("date_from", from);
    if (to) p.set("date_to", to);
    if (zone) p.set("zone_id", zone);
    if (line) p.set("line_id", line);
    if (status) p.set("status", status);
    if (qDeb.trim()) p.set("q", qDeb.trim());
    if (test) p.set("include_test", "1");
    return p.toString();
  }, [from, to, zone, line, status, qDeb, test]);

  const load = useCallback(() => {
    setLoading(true); setErr("");
    Promise.all([
      api.get(`/api/line-rejection/summary?${qs}`, token),
      api.get(`/api/line-rejection/records?${qs}`, token),
    ]).then(([s, r]) => { setSum(s); setRows(r.rows); setMore(r.more); })
      .catch(e => setErr(e.message))
      .finally(() => setLoading(false));
  }, [qs, token]);
  useEffect(() => { load(); }, [load]);

  const zones = useMemo(() => boardZones(meta, sum), [meta, sum]);
  const zoneLines = useMemo(() => (meta.zones.find(z => z.id === zone)?.lines) || [], [meta, zone]);
  const pick = (z, l) => { setZone(z); setLine(l); };
  const today = localYmd(new Date());
  const range = RANGES.find(r => r.from() === from && to === today)?.key;
  const k = sum?.kpi || {};
  const selZone = meta.zones.find(z => z.id === zone);
  const selLine = selZone?.lines.find(l => l.id === line);
  const listTitle = selZone ? `${zoneShort(selZone.name)}${selLine ? ` · ${selLine.name}` : ""}` : "All zones";

  return (
    <div style={embedded ? {} : { background: "#f8fafc", minHeight: "100vh" }}>
      {!embedded && <PageTopbar leading="Line" accent="Rejection" />}
      <div style={embedded ? { padding: "14px 16px 18px" }
                           : { padding: "14px 18px 30px", maxWidth: 1500, margin: "0 auto" }}>

        {/* Filters */}
        <div style={{ ...card, marginBottom: 12 }}>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
            {RANGES.map(r => (
              <button key={r.key} onClick={() => { setFrom(r.from()); setTo(today); }}
                      style={{ padding: "6px 12px", borderRadius: 999, fontSize: 12.5, fontWeight: 800, cursor: "pointer",
                               border: `1px solid ${range === r.key ? "#2563eb" : "#cbd5e1"}`,
                               background: range === r.key ? "#2563eb" : "#fff",
                               color: range === r.key ? "#fff" : "#334155" }}>{r.label}</button>
            ))}
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
            <div style={field}><label style={lbl}>FROM</label>
              <input type="date" value={from} max={to} onChange={e => setFrom(e.target.value)} style={inp} /></div>
            <div style={field}><label style={lbl}>TO</label>
              <input type="date" value={to} min={from} onChange={e => setTo(e.target.value)} style={inp} /></div>
            <div style={field}><label style={lbl}>ZONE</label>
              <select value={zone} onChange={e => { setZone(e.target.value); setLine(""); }} style={inp}>
                <option value="">All zones</option>
                {meta.zones.map(z => <option key={z.id} value={z.id}>{z.name}</option>)}
              </select></div>
            <div style={field}><label style={lbl}>LINE</label>
              <select value={line} onChange={e => setLine(e.target.value)} style={inp} disabled={!zone}>
                <option value="">{zone ? "All lines" : "Select zone"}</option>
                {zoneLines.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
              </select></div>
            <div style={field}><label style={lbl}>STATUS</label>
              <select value={status} onChange={e => setStatus(e.target.value)} style={inp}>
                <option value="">All</option>
                <option value={OPEN_ST.join(",")}>Open</option>
                <option value={REWORK_ST.join(",")}>Under Rework</option>
                <option value="SCRAPPED">Scrapped</option>
                <option value="UNLOCKED,BACK_TO_LINE,SUCCESS">Back to Line</option>
              </select></div>
            <div style={{ ...field, maxWidth: 260, flexBasis: 200 }}><label style={lbl}>SEARCH</label>
              <input value={q} onChange={e => setQ(e.target.value)} style={inp}
                     placeholder="Red Bin no., bar code, part, defect…" /></div>
            <button onClick={load} disabled={loading}
                    style={{ padding: "8px 16px", borderRadius: 8, border: "none", background: "#2563eb",
                             color: "#fff", fontWeight: 800, fontSize: 13, cursor: "pointer", height: 38 }}>
              {loading ? "Loading…" : "Refresh"}
            </button>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700,
                            color: "#64748b", height: 38, cursor: "pointer", marginLeft: "auto" }}>
              <input type="checkbox" checked={test} onChange={e => setTest(e.target.checked)} /> Show test entries
            </label>
          </div>
        </div>
        {err && <div style={{ ...card, background: "#fef2f2", borderColor: "#fecaca", color: "#b91c1c",
                              fontWeight: 700, fontSize: 13, marginBottom: 12 }}>{err}</div>}

        {/* KPIs */}
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
          <Kpi label="REJECTED QTY" value={k.qty} />
          <Kpi label="RECORDS" value={k.records} />
          {GROUPS.map(g => <Kpi key={g.key} label={g.label.toUpperCase()} value={k[g.key]} color={g.color} />)}
        </div>

        {/* Zone → line */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center",
                      flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
          <div style={{ ...h3, margin: 0, fontSize: 16 }}>Zone & Line wise Rejection</div>
          <Legend />
        </div>
        {sum && <ZoneBoard zones={zones} zone={zone} line={line} onPick={pick} />}

        {/* Records */}
        <div style={{ ...card, marginTop: 16, padding: 0, overflow: "hidden" }}>
          <div style={{ ...h3, margin: 0, padding: "12px 14px", display: "flex", gap: 10,
                        alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ fontSize: 16 }}>Rejections · {listTitle} {rows ? `(${rows.length}${more ? "+" : ""})` : ""}</span>
            {zone && (
              <button onClick={() => pick("", "")}
                      style={{ border: "1px solid #cbd5e1", background: "#fff", borderRadius: 999, padding: "3px 10px",
                               fontSize: 12, fontWeight: 800, color: "#334155", cursor: "pointer" }}>Show all ×</button>
            )}
            <span style={{ marginLeft: "auto", fontSize: 12, fontWeight: 600, color: "#64748b" }}>
              {more ? "Latest 500 shown — narrow the filter" : "Click a record for full detail"}
            </span>
          </div>
          <div style={{ overflow: "auto", maxHeight: "70vh" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead><tr>
                {["Date & Time", "Red Bin No.", "Zone / Line", "Part", "Defect", "Qty", "Status", "Photo"]
                  .map(h => <th key={h} style={th}>{h}</th>)}
              </tr></thead>
              <tbody>
                {rows && !rows.length && (
                  <tr><td colSpan={8} style={{ ...td, color: "#64748b", padding: 16 }}>No rejection</td></tr>
                )}
                {(rows || []).map(r => (
                  <tr key={r.id} onClick={() => setOpen(r.id)} style={{ cursor: "pointer" }}
                      onMouseEnter={e => (e.currentTarget.style.background = "#f8fafc")}
                      onMouseLeave={e => (e.currentTarget.style.background = "")}>
                    <td style={td}>{fmtDT(r.at)}{r.shift ? <span style={{ color: "#94a3b8" }}> · {r.shift}</span> : null}</td>
                    <td style={{ ...td, fontFamily: "monospace", fontWeight: 700, color: "#1d4ed8" }}>
                      {r.rb_no}{r.is_test ? <span style={{ marginLeft: 6 }}><Badge text="Test" tone="grey" /></span> : null}
                    </td>
                    <td style={td} title={r.zone_name}>
                      {zoneShort(r.zone_name)}<span style={{ color: "#94a3b8" }}> · </span><b>{r.line_name || "—"}</b>
                    </td>
                    <td style={{ ...td, whiteSpace: "normal", minWidth: 150 }}>{r.part_name || "—"}</td>
                    <td style={{ ...td, whiteSpace: "normal", minWidth: 150 }}>{r.defect || "—"}</td>
                    <td style={{ ...td, fontWeight: 900, fontSize: 14 }}>{r.qty}</td>
                    <td style={td}><Badge status={r.status} /></td>
                    <td style={td}>{r.photo_id ? <Photo id={r.photo_id} token={token} onOpen={setBig} /> : <span style={{ color: "#cbd5e1" }}>—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
      {open && <RejectionReport id={open} token={token} onClose={() => setOpen(null)} />}
      {big && (
        <div onClick={() => setBig(null)}
             style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.85)", zIndex: 2200,
                      display: "flex", alignItems: "center", justifyContent: "center", cursor: "zoom-out" }}>
          <img src={big} alt="Defect" style={{ maxWidth: "94vw", maxHeight: "92vh", borderRadius: 8 }} />
        </div>
      )}
    </div>
  );
}
