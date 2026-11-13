// pages/HistoricalBoard.jsx — Historical Data tabs for the Control Board
// sheets and Line Rejection (2026-11-12).
//
// Operator: "historical data page me control board page ke 4M change, operator
// visual, track record sheet, defect record sheet ke tab bana de jinme inka
// monthly data auto save hota rahe aur pdf download ka button bhi dena and line
// rejection daily basis pe save honi chahiye; filter sab tab me (zone, line)".
//
// The copies are saved by the MES-API every hour (routers/cb_history.py):
// each line's current month of the four sheets, and the Red Bin records per
// day.  A saved sheet opens in the same format as the Control Board (SheetDoc
// with `history`: read-only, Save PDF as usual).
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { SheetDoc, DOCS, ALL } from "./ControlBoard";
import { RejectionListReport, saveRejectionListPdf, Badge, fmtDT } from "../components/RejectionSummary";

const SHEET_OF = {          // history sheet → Control Board sheet key
  "4m": "4m-change", ops: "operator-visual", track: "track-sheet", defect: "defect-recording",
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monLabel = (m) => (m ? `${MONTHS[Number(m.slice(5, 7)) - 1]} ${m.slice(0, 4)}` : "");
const thisMonth = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
};
const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const dmy = (s) => (s ? `${s.slice(8, 10)}-${s.slice(5, 7)}-${s.slice(0, 4)}` : "");
const savedText = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}-${p(d.getMonth() + 1)}-${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
};

const lbl = { fontSize: 12, color: "#475569" };
const inp = { padding: "8px 10px", border: "1px solid #cbd5e1", borderRadius: 8, fontSize: 14,
              fontFamily: "inherit", background: "#fff", color: "#0f172a" };
const btnStyle = (bg, dis) => ({ ...inp, background: bg, color: "#fff", border: "none", fontWeight: 700,
                                 cursor: dis ? "default" : "pointer", opacity: dis ? 0.5 : 1 });
const note = { color: "#64748b", fontSize: 13, padding: 20, textAlign: "center", background: "#fff",
               border: "1.5px dashed #cbd5e1", borderRadius: 12 };

// ── the four monthly sheets ─────────────────────────────────────────────
export function BoardSheetHistory({ kind }) {
  const { token } = useAuth();
  const [lines, setLines] = useState([]);
  const [zone, setZone] = useState("ALL");
  const [lineId, setLineId] = useState("");
  const [month, setMonth] = useState(thisMonth);
  const [months, setMonths] = useState([]);
  const [doc, setDoc] = useState({ key: "", data: null, savedAt: null, busy: false, err: "" });

  useEffect(() => {
    let alive = true;
    api.get("/api/lines/", token)
      .then(ls => { if (alive) setLines(Array.isArray(ls) ? ls : []); })
      .catch(() => { if (alive) setLines([]); });
    return () => { alive = false; };
  }, [token]);
  const zoneOf = (l) => l?.zone_name || "No zone";
  const zones = useMemo(() => [...new Set(lines.map(zoneOf))].sort((a, b) => a.localeCompare(b)), [lines]);
  const shown = useMemo(() => lines.filter(l => zone === "ALL" || zoneOf(l) === zone)
    .slice().sort((a, b) => zoneOf(a).localeCompare(zoneOf(b))
      || String(a.line_name || "").localeCompare(String(b.line_name || ""))), [lines, zone]);
  // a sheet needs a line: keep one picked (the first of the zone) so the tab never opens empty
  useEffect(() => {
    if (!shown.length) return;
    if (!shown.some(l => String(l.id) === String(lineId))) setLineId(String(shown[0].id));
  }, [shown]);   // eslint-disable-line react-hooks/exhaustive-deps
  const line = lines.find(l => String(l.id) === String(lineId));
  const lineName = line ? (line.line_name || line.line_code || `Line ${line.id}`) : "";

  // saved months of this line
  useEffect(() => {
    if (!lineId) { setMonths([]); return undefined; }
    let alive = true;
    api.get(`/api/cb-history/months?sheet=${kind}&line_id=${lineId}`, token)
      .then(r => { if (alive) setMonths(Array.isArray(r) ? r : []); })
      .catch(() => { if (alive) setMonths([]); });
    return () => { alive = false; };
  }, [kind, lineId, token]);

  // the saved copy
  const want = lineId && month ? `${kind}|${lineId}|${month}` : "";
  useEffect(() => {
    if (!want) return undefined;
    let alive = true;
    setDoc(d => ({ ...d, key: want, busy: true, err: "" }));
    api.get(`/api/cb-history/sheet?sheet=${kind}&line_id=${lineId}&month=${month}`, token)
      .then(r => { if (alive) setDoc({ key: want, data: r && r.data, savedAt: r && r.saved_at, busy: false, err: "" }); })
      .catch(e => {
        if (!alive) return;
        const m = /404|Not Found/i.test(String(e && e.message))
          ? "Saved sheets appear after the next MES-API restart." : "Could not load the saved sheet.";
        setDoc({ key: want, data: null, savedAt: null, busy: false, err: m });
      });
    return () => { alive = false; };
  }, [want]);   // eslint-disable-line react-hooks/exhaustive-deps

  const sheet = ALL.find(s => s.key === SHEET_OF[kind]);
  const n = sheet ? ALL.indexOf(sheet) + 1 : 0;
  const zoneName = line ? zoneOf(line) : "";
  const lineOpts = useMemo(() => lines
    .map(l => ({ line_id: l.id, line_name: l.line_name || l.line_code || `Line ${l.id}`, zone_name: zoneOf(l) }))
    .sort((a, b) => a.zone_name.localeCompare(b.zone_name) || a.line_name.localeCompare(b.line_name)), [lines]);
  const data = doc.key === want ? doc.data : null;
  const std = (data && data.std) || {};
  const docData = {
    "line-month": { lines: lineOpts, line_id: line ? line.id : null, month },
    "track-record": {
      lines: lineOpts.map(o => (String(o.line_id) === String(lineId)
        ? { ...o, cycle_time: std.cycle_time ?? null, target: std.target ?? null }
        : { ...o, cycle_time: null, target: null })),
      line_id: line ? line.id : null, month,
      sheet: kind === "track" && data ? data.sheet : null, loading: false, error: null,
    },
  };
  // a line / month picked inside the sheet follows the tab's filters
  const onDataSelect = (_k, msg) => {
    if (msg && msg.line_id && String(msg.line_id) !== String(lineId)) {
      const l = lines.find(x => String(x.id) === String(msg.line_id));
      if (l && zone !== "ALL" && zoneOf(l) !== zone) setZone("ALL");
      setLineId(String(msg.line_id));
    }
    if (msg && msg.month && /^\d{4}-\d{2}$/.test(msg.month) && msg.month !== month) setMonth(msg.month);
  };
  const history = data ? {
    m4: kind === "4m" ? data : null,
    ops: kind === "ops" ? (data.sheets || {}) : null,
    rej: kind === "defect" ? data.rej : null,
    sign: kind === "defect" ? data.sign : null,
    // entries typed into the live sheet at the time of the copy (auto-save, 12-Nov)
    cellsByDoc: kind === "track" ? { "track-record": data.cells || {} } : kind === "defect" ? (data.cells || {}) : {},
    savedAt: savedText(doc.savedAt),
  } : null;
  const variants = sheet ? (DOCS[sheet.key] || []) : [];
  const fileTag = `${lineName} - ${monLabel(month)}`;

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
        <label style={lbl}>Zone<br />
          <select value={zone} onChange={e => setZone(e.target.value)} style={inp}>
            <option value="ALL">All zones</option>
            {zones.map(z => <option key={z} value={z}>{z}</option>)}
          </select></label>
        <label style={lbl}>Line<br />
          <select value={lineId} onChange={e => setLineId(e.target.value)} style={{ ...inp, minWidth: 190 }}>
            {shown.map(l => <option key={l.id} value={l.id}>{l.line_name || l.line_code || `Line ${l.id}`}</option>)}
          </select></label>
        <label style={lbl}>Month<br />
          <input type="month" value={month} onChange={e => e.target.value && setMonth(e.target.value)} style={inp} /></label>
        <div style={{ fontSize: 12, color: "#64748b", paddingBottom: 9 }}>
          Saved automatically every hour · use Save PDF on the sheet to download it
        </div>
      </div>

      {months.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: "#475569", marginRight: 4 }}>Saved months:</span>
          {months.map(m => (
            <button key={m.month} onClick={() => setMonth(m.month)} title={`Saved ${savedText(m.saved_at)}`}
                    style={{ border: `1px solid ${m.month === month ? "#1e40af" : "#cbd5e1"}`, borderRadius: 999,
                             padding: "3px 11px", fontSize: 12, fontWeight: 700, cursor: "pointer",
                             fontFamily: "inherit", background: m.month === month ? "#1e40af" : "#fff",
                             color: m.month === month ? "#fff" : "#334155" }}>{monLabel(m.month)}</button>
          ))}
        </div>
      )}

      {doc.err ? <div style={{ ...note, color: "#b91c1c" }}>{doc.err}</div>
        : !lineId ? <div style={note}>{lines.length ? "Select a line." : "No lines available for this login."}</div>
        : doc.busy && doc.key === want ? <div style={note}>Loading…</div>
        : !data ? (
          <div style={note}>
            No saved copy of {lineName} for {monLabel(month)}. Copies are saved every hour from 12-Nov-2026
            (current month; the previous month once).
          </div>
        ) : kind === "ops" ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {variants.map(v => (
              <SheetDoc key={`h:${v.ops}`} sheet={{ ...sheet, label: `${sheet.label} — ${v.label}` }} n={n}
                        variants={[v]} zoneName={zoneName} fileTag={fileTag} token={token} docData={docData}
                        onDataSelect={onDataSelect} onClose={() => {}} history={history} noClose />
            ))}
          </div>
        ) : (
          <SheetDoc key={`h:${kind}`} sheet={sheet} n={n} variants={variants} zoneName={zoneName}
                    fileTag={fileTag} token={token} docData={docData} onDataSelect={onDataSelect}
                    onClose={() => {}} history={history} noClose />
        )}
    </div>
  );
}

// ── Line Rejection, saved per day ───────────────────────────────────────
const th = { padding: "8px 10px", fontSize: 12, fontWeight: 800, color: "#334155", background: "#f8fafc",
             textAlign: "left", borderBottom: "1px solid #e2e8f0", whiteSpace: "nowrap" };
const td = { padding: "7px 10px", fontSize: 13, borderBottom: "1px solid #f1f5f9", verticalAlign: "top" };
const DUE = { process: "Process", child: "Child Part", handling: "Abnormal Handling" };
const zoneShort = (z) => String(z || "").replace(/^Zone\s*\d+\s*-\s*/i, "") || "—";

export function RejectionHistory() {
  const { token } = useAuth();
  const today = new Date();
  const [from, setFrom] = useState(() => ymd(new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6)));
  const [to, setTo] = useState(() => ymd(today));
  const [zone, setZone] = useState("");
  const [line, setLine] = useState("");
  const [meta, setMeta] = useState({ pairs: [], saved_at: null, first_day: null });
  const [res, setRes] = useState({ days: null, busy: false, err: "" });
  const [open, setOpen] = useState(null);           // a day's report
  const [pdf, setPdf] = useState({ day: "", note: "" });

  useEffect(() => {
    let alive = true;
    api.get("/api/cb-history/rejection-filters", token)
      .then(r => { if (alive && r) setMeta(r); })
      .catch(() => {});
    return () => { alive = false; };
  }, [token]);
  const zones = useMemo(() => [...new Set(meta.pairs.map(p => p.zone).filter(Boolean))].sort(), [meta]);
  const lineList = useMemo(() => [...new Set(meta.pairs.filter(p => !zone || p.zone === zone)
    .map(p => p.line).filter(Boolean))].sort(), [meta, zone]);

  const load = () => {
    if (!from || !to) return;
    if (from > to) { setRes({ days: null, busy: false, err: "The From date must be on or before the To date." }); return; }
    setRes(r => ({ ...r, busy: true, err: "" }));
    const qs = `date_from=${from}&date_to=${to}` + (zone ? `&zone=${encodeURIComponent(zone)}` : "")
      + (line ? `&line=${encodeURIComponent(line)}` : "");
    api.get(`/api/cb-history/rejections?${qs}`, token)
      .then(r => setRes({ days: (r && r.days) || [], busy: false, err: "" }))
      .catch(e => setRes({ days: null, busy: false,
        err: /404|Not Found/i.test(String(e && e.message))
          ? "Saved rejections appear after the next MES-API restart." : "Could not load the saved rejections." }));
  };
  useEffect(() => { load(); }, [from, to, zone, line]);   // eslint-disable-line react-hooks/exhaustive-deps

  const scope = line ? `${line}${zone ? ` (${zoneShort(zone)})` : ""}` : zone ? zoneShort(zone) : "All lines";
  const ctx = { line_name: scope };
  const saveDay = (d) => {
    setPdf({ day: d.date, note: "Saving PDF…" });
    saveRejectionListPdf({ title: dmy(d.date), records: d.records, context: ctx,
                           sheetName: "Line Rejection (saved daily)", token,
                           fileTitle: `Line Rejection ${dmy(d.date)} - ${scope}` })
      .then(n => setPdf({ day: d.date, note: n }))
      .catch(e => setPdf({ day: d.date, note: "PDF not saved: " + ((e && e.message) || "error") }));
  };
  const allRecords = (res.days || []).flatMap(d => d.records);
  const saveRange = () => {
    setPdf({ day: "all", note: "Saving PDF…" });
    saveRejectionListPdf({ title: `${dmy(from)} to ${dmy(to)}`, records: allRecords, context: ctx,
                           sheetName: "Line Rejection (saved daily)", token,
                           fileTitle: `Line Rejection ${dmy(from)} to ${dmy(to)} - ${scope}` })
      .then(n => setPdf({ day: "all", note: n }))
      .catch(e => setPdf({ day: "all", note: "PDF not saved: " + ((e && e.message) || "error") }));
  };
  const totalQty = allRecords.reduce((s, r) => s + (Number(r.qty) || 0), 0);

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
        <label style={lbl}>From<br />
          <input type="date" value={from} onChange={e => setFrom(e.target.value)} style={inp} /></label>
        <label style={lbl}>To<br />
          <input type="date" value={to} onChange={e => setTo(e.target.value)} style={inp} /></label>
        <label style={lbl}>Zone<br />
          <select value={zone} onChange={e => { setZone(e.target.value); setLine(""); }} style={inp}>
            <option value="">All zones</option>
            {zones.map(z => <option key={z} value={z}>{zoneShort(z)}</option>)}
          </select></label>
        <label style={lbl}>Line<br />
          <select value={line} onChange={e => setLine(e.target.value)} style={{ ...inp, minWidth: 170 }}>
            <option value="">All lines</option>
            {lineList.map(l => <option key={l} value={l}>{l}</option>)}
          </select></label>
        <button onClick={saveRange} disabled={!allRecords.length || pdf.note === "Saving PDF…"}
                style={btnStyle("#1e40af", !allRecords.length)}>⬇ PDF (all shown)</button>
        {pdf.day === "all" && pdf.note && <span style={{ fontSize: 12, fontWeight: 600,
          color: /not saved/.test(pdf.note) ? "#b91c1c" : "#15803d", paddingBottom: 9 }}>{pdf.note}</span>}
        <div style={{ marginLeft: "auto", fontSize: 12, color: "#64748b", paddingBottom: 9, textAlign: "right" }}>
          Red Bin portal records, saved per day every hour{meta.saved_at ? ` · last saved ${savedText(meta.saved_at)}` : ""}
        </div>
      </div>

      {res.err && <div style={{ ...note, color: "#b91c1c" }}>{res.err}</div>}
      {res.busy && !res.days && <div style={note}>Loading…</div>}
      {res.days && !res.days.length && !res.busy && (
        <div style={note}>No rejections saved for {scope} from {dmy(from)} to {dmy(to)}.</div>
      )}
      {res.days && res.days.length > 0 && (
        <div style={{ fontSize: 13, color: "#334155" }}>
          <b>{res.days.length}</b> day{res.days.length === 1 ? "" : "s"} · <b>{allRecords.length}</b> record
          {allRecords.length === 1 ? "" : "s"} · <b>{totalQty}</b> pcs rejected
        </div>
      )}
      {(res.days || []).map(d => (
        <div key={d.date} style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12, overflow: "hidden" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", flexWrap: "wrap",
                        background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
            <div style={{ fontSize: 15, fontWeight: 800, color: "#0f172a" }}>{dmy(d.date)}</div>
            <div style={{ fontSize: 13, color: "#475569" }}>
              {d.records.length} record{d.records.length === 1 ? "" : "s"} · {d.qty} pcs
            </div>
            <div style={{ fontSize: 12, color: "#94a3b8" }}>saved {savedText(d.saved_at)}</div>
            <span style={{ flex: 1 }} />
            {pdf.day === d.date && pdf.note && <span style={{ fontSize: 12, fontWeight: 600,
              color: /not saved/.test(pdf.note) ? "#b91c1c" : "#15803d" }}>{pdf.note}</span>}
            <button onClick={() => setOpen(d)} style={{ ...inp, padding: "5px 12px", fontSize: 13, fontWeight: 700,
                                                       cursor: "pointer" }}>Report</button>
            <button onClick={() => saveDay(d)} disabled={pdf.day === d.date && pdf.note === "Saving PDF…"}
                    style={{ ...btnStyle("#1e40af", false), padding: "5px 12px", fontSize: 13 }}>⬇ PDF</button>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 900 }}>
              <thead><tr>
                {["Time", "Shift", "Zone", "Line", "Machine / station", "Part", "Defect", "Due to", "Qty", "Status"]
                  .map(h => <th key={h} style={h === "Qty" ? { ...th, textAlign: "right" } : th}>{h}</th>)}
              </tr></thead>
              <tbody>
                {d.records.map(r => (
                  <tr key={r.id}>
                    <td style={{ ...td, whiteSpace: "nowrap" }}>{fmtDT(r.at).slice(11)}</td>
                    <td style={td}>{r.shift || "—"}</td>
                    <td style={td}>{zoneShort(r.zone_name)}</td>
                    <td style={td}>{r.line_name || "—"}</td>
                    <td style={td}>{r.machine || "—"}</td>
                    <td style={td}>{[r.part_code, r.part_name].filter(Boolean).join(" · ") || "—"}</td>
                    <td style={td}>{r.defect || "—"}</td>
                    <td style={td}>{DUE[r.due] || "Process"}</td>
                    <td style={{ ...td, textAlign: "right", fontWeight: 700 }}>{r.qty}</td>
                    <td style={td}><Badge status={r.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
      {open && (
        <RejectionListReport title={dmy(open.date)} records={open.records} context={ctx}
                             sheetName="Line Rejection (saved daily)" token={token} z={10500}
                             onClose={() => setOpen(null)} />
      )}
    </div>
  );
}
