/**
 * Rejection summary in report format  (2026-11-11)
 *
 * One Red Bin rejection as a formal, printable report (letterhead, numbered
 * sections, bordered tables, sign-off), and the summary of a group of
 * rejections (one cell of the Control Board's Defect Recording Sheet).
 * Used by Production → Line Rejection and by the Control Board.  Data comes
 * read-only from /api/line-rejection (Red Bin portal).
 *
 *   <RejectionReport id token onClose z />          one record
 *   <RejectionListReport title records context token onClose z />
 *        a group; a row opens its RejectionReport (one record opens directly)
 */
import { useEffect, useRef, useState } from "react";
import { createPortal, flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { api } from "../api/client";
import { savePdfFile } from "../utils/cbPdf";

export const fmtDT = (s) => s ? `${s.slice(8, 10)}-${s.slice(5, 7)}-${s.slice(0, 4)} ${s.slice(11, 16)}` : "—";

export const STATUS = {
  REJECTED:             { label: "Rejected at Line", tone: "red" },
  RED_BIN_ENTRY_LINE:   { label: "Red Bin Entry",    tone: "red" },
  LOCKED:               { label: "Locked",           tone: "red" },
  QUALITY_RED_BIN_AREA: { label: "Red Bin Room",     tone: "orange" },
  REWORK:               { label: "Rework",           tone: "amber" },
  WAITING_UNLOCK:       { label: "Waiting Unlock",   tone: "purple" },
  SCRAPPED:             { label: "Scrapped",         tone: "slate" },
  UNLOCKED:             { label: "Unlocked",         tone: "blue" },
  BACK_TO_LINE:         { label: "Back to Line",     tone: "green" },
  SUCCESS:              { label: "OK at EOL",        tone: "green" },
};
export const OPEN_ST   = ["REJECTED", "RED_BIN_ENTRY_LINE", "LOCKED", "QUALITY_RED_BIN_AREA"];
export const REWORK_ST = ["REWORK", "WAITING_UNLOCK"];
export const DEFECT_CAT = { A: "Maru-A", F: "Functional", G: "General" };
// status groups (state colours, always shown with their label)
export const GROUPS = [
  { key: "open",   label: "Open",         color: "#dc2626" },
  { key: "rework", label: "Under Rework", color: "#d97706" },
  { key: "scrap",  label: "Scrapped",     color: "#475569" },
  { key: "back",   label: "Back to Line", color: "#16a34a" },
];
export const groupOf = (st) => (OPEN_ST.includes(st) ? "open" : REWORK_ST.includes(st) ? "rework"
  : st === "SCRAPPED" ? "scrap" : "back");
const TONES = {
  red: ["#fee2e2", "#b91c1c"], orange: ["#ffedd5", "#c2410c"], amber: ["#fef3c7", "#b45309"],
  purple: ["#f3e8ff", "#7e22ce"], slate: ["#e2e8f0", "#334155"], blue: ["#dbeafe", "#1d4ed8"],
  green: ["#dcfce7", "#15803d"], grey: ["#f1f5f9", "#475569"],
};
const DUE = { process: "Process", child: "Child Part", handling: "Abnormal Handling" };

export function Badge({ status, text, tone }) {
  const s = status ? (STATUS[status] || { label: status, tone: "grey" }) : { label: text, tone };
  const t = TONES[s.tone] || TONES.grey;
  return (
    <span style={{ background: t[0], color: t[1], padding: "2px 9px", borderRadius: 999,
                   fontSize: 11, fontWeight: 800, whiteSpace: "nowrap" }}>{s.label}</span>
  );
}

export function ageText(at) {
  if (!at) return "";
  const t = new Date(at.replace(" ", "T") + "+05:30").getTime();
  const m = Math.max(0, Math.round((Date.now() - t) / 60000));
  const d = Math.floor(m / 1440), h = Math.floor((m % 1440) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m % 60} m` : `${m} m`;
}

/* Photos come from an auth-checked endpoint, so they are fetched as blobs. */
export function useAuthImg(id, token) {
  const [url, setUrl] = useState(null);
  useEffect(() => {
    if (!id) { setUrl(null); return undefined; }
    let u = null, dead = false;
    fetch(`/api/line-rejection/photo/${id}`,
          { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(r => (r.ok ? r.blob() : null))
      .then(b => { if (b && !dead) { u = URL.createObjectURL(b); setUrl(u); } })
      .catch(() => {});
    return () => { dead = true; if (u) URL.revokeObjectURL(u); };
  }, [id, token]);
  return url;
}

export function Photo({ id, token, size = 40, onOpen }) {
  const url = useAuthImg(id, token);
  return (
    <span onClick={onOpen ? (e) => { e.stopPropagation(); if (url) onOpen(url); } : undefined}
          style={{ display: "inline-block", width: size, height: size, borderRadius: 8,
                   background: "#f1f5f9", border: "1px solid #e2e8f0", overflow: "hidden",
                   cursor: onOpen ? "zoom-in" : "inherit", verticalAlign: "middle" }}>
      {url && <img src={url} alt="Defect" style={{ width: "100%", height: "100%", objectFit: "cover" }} />}
    </span>
  );
}

const STAGES = ["Rejected at Line", "Red Bin Room", "Decision", "Closed"];
export function stageOf(st) {
  if (["REJECTED", "RED_BIN_ENTRY_LINE", "LOCKED"].includes(st)) return 0;
  if (st === "QUALITY_RED_BIN_AREA") return 1;
  if (REWORK_ST.includes(st)) return 2;
  return 3;
}

/* ── report building blocks ───────────────────────────────────────────── */
const INK = "#0f172a";
const LINE = "1px solid #94a3b8";
// the report's own CSS (also copied into the print frame)
const REPORT_CSS = `
.rjr { background:#fff; color:${INK}; font-family:'Barlow',Arial,sans-serif; border:1.5px solid ${INK}; }
.rjr * { box-sizing:border-box; }
.rjr-head { display:grid; grid-template-columns:110px 1fr 220px; border-bottom:1.5px solid ${INK}; }
.rjr-head > div { padding:8px 10px; }
.rjr-logo { display:flex; align-items:center; justify-content:center; border-right:${LINE}; }
.rjr-logo img { max-width:100%; max-height:52px; object-fit:contain; }
.rjr-co { text-align:center; display:flex; flex-direction:column; justify-content:center; }
.rjr-co .c1 { font-size:13px; font-weight:800; letter-spacing:.02em; }
.rjr-co .c2 { font-size:18px; font-weight:900; letter-spacing:.04em; margin-top:2px; }
.rjr-doc { border-left:${LINE}; font-size:12px; display:flex; flex-direction:column; gap:3px; justify-content:center; }
.rjr-doc b { font-family:monospace; font-size:13px; word-break:break-all; }
.rjr-sec { border-bottom:${LINE}; }
.rjr-sec:last-child { border-bottom:none; }
.rjr-st { background:#e2e8f0; font-size:12px; font-weight:900; letter-spacing:.06em; padding:6px 10px;
          text-transform:uppercase; border-bottom:${LINE}; }
.rjr-sum { padding:10px 12px; font-size:13.5px; line-height:1.6; }
.rjr-kv { display:grid; grid-template-columns:150px 1fr 150px 1fr; }
.rjr-kv > div { padding:6px 10px; font-size:12.5px; border-bottom:1px solid #cbd5e1; min-height:30px; }
.rjr-kv .k { background:#f8fafc; font-weight:800; color:#334155; border-right:1px solid #cbd5e1; }
.rjr-kv .v { font-weight:600; word-break:break-word; }
.rjr-kv .mid { border-right:1px solid #cbd5e1; }
.rjr-kv .wk { grid-column:1; }
.rjr-kv .wide { grid-column:2 / span 3; }
.rjr-tbl { width:100%; border-collapse:collapse; font-size:12px; }
.rjr-tbl th { background:#f8fafc; font-weight:800; color:#334155; text-align:left; padding:6px 8px;
              border-bottom:1px solid #94a3b8; white-space:nowrap; }
.rjr-tbl td { padding:6px 8px; border-bottom:1px solid #e2e8f0; vertical-align:top; }
.rjr-tbl td + td, .rjr-tbl th + th { border-left:1px solid #e2e8f0; }
.rjr-tbl tr.tot td { font-weight:900; background:#f8fafc; border-top:1px solid #94a3b8; }
.rjr-tbl tr.link { cursor:pointer; }
.rjr-tbl tr.link:hover td { background:#eff6ff; }
.rjr-trk { display:flex; gap:4px; padding:10px 12px 4px; flex-wrap:wrap; }
.rjr-trk > div { flex:1 1 120px; min-width:110px; }
.rjr-trk .bar { height:6px; border-radius:4px; }
.rjr-trk .lab { font-size:11.5px; font-weight:800; margin-top:4px; }
.rjr-ph { display:flex; gap:10px; flex-wrap:wrap; padding:10px 12px; }
.rjr-sign { display:grid; grid-template-columns:repeat(4,1fr); }
.rjr-sign > div { padding:8px 10px 26px; font-size:11.5px; border-right:1px solid #cbd5e1; }
.rjr-sign > div:last-child { border-right:none; }
.rjr-sign b { display:block; font-size:12.5px; margin-top:3px; min-height:16px; }
.rjr-foot { font-size:10.5px; color:#64748b; padding:5px 10px; border-top:${LINE}; display:flex;
            justify-content:space-between; gap:10px; flex-wrap:wrap; }
.rjr-scroll { overflow-x:auto; }
@media (max-width: 680px) {
  .rjr-head { grid-template-columns:70px 1fr; }
  .rjr-doc { grid-column:1 / span 2; border-left:none; border-top:${LINE}; }
  .rjr-co .c2 { font-size:15px; }
  .rjr-kv { grid-template-columns:120px 1fr; }
  .rjr-kv .mid { border-right:none; }
  .rjr-kv .wide { grid-column:2; }
  .rjr-sign { grid-template-columns:repeat(2,1fr); }
}
@media print {
  .rjr { border-width:1px; }
  .rjr-tbl tr.link:hover td { background:transparent; }
}
`;

function KV({ rows }) {
  // rows: [[label, value, wide?], ...]; two pairs a row, a wide pair takes a
  // whole row (its label starts a new row); empty values print as "—"
  const cells = [];
  let col = 0;
  rows.forEach(([k, v, wide], i) => {
    const val = v === null || v === undefined || v === "" ? "—" : v;
    const left = wide || col === 0;
    cells.push(<div key={`k${i}`} className={`k${left ? " wk" : ""}`}>{k}</div>);
    cells.push(<div key={`v${i}`} className={`v${wide ? " wide" : left ? " mid" : ""}`}>{val}</div>);
    col = wide ? 0 : 1 - col;
  });
  return <div className="rjr-kv">{cells}</div>;
}

function Sec({ n, title, children }) {
  return (
    <div className="rjr-sec">
      <div className="rjr-st">{n ? `${n}. ` : ""}{title}</div>
      {children}
    </div>
  );
}

function Table({ cols, rows, onRow, total }) {
  return (
    <div className="rjr-scroll">
      <table className="rjr-tbl">
        <thead><tr>{cols.map(c => <th key={c[0]} style={c[2]}>{c[0]}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={onRow ? "link" : undefined} onClick={onRow ? () => onRow(r) : undefined}>
              {cols.map(c => <td key={c[0]} style={c[2]}>{c[1](r, i) ?? "—"}</td>)}
            </tr>
          ))}
          {total}
        </tbody>
      </table>
    </div>
  );
}

function Letterhead({ title, docRows }) {
  return (
    <div className="rjr-head">
      <div className="rjr-logo"><img src="/logo.jpg" alt="Toyota Boshoku"
                                     onError={(e) => { e.currentTarget.style.display = "none"; }} /></div>
      <div className="rjr-co">
        <div className="c1">TOYOTA BOSHOKU DEVICE INDIA PVT. LTD.</div>
        <div className="c2">{title}</div>
      </div>
      <div className="rjr-doc">
        {docRows.map(([k, v]) => <div key={k}>{k}: {v}</div>)}
      </div>
    </div>
  );
}

const nowText = () => {
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}-${p(d.getMonth() + 1)}-${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
};

/* Print only the report: a hidden frame with the report's HTML + CSS. */
function printNode(node, title) {
  if (!node) return;
  const fr = document.createElement("iframe");
  fr.setAttribute("aria-hidden", "true");
  fr.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;";
  document.body.appendChild(fr);
  const doc = fr.contentDocument;
  doc.open();
  doc.write(`<!doctype html><html><head><meta charset="utf-8"><title>${String(title || "Rejection Summary").replace(/</g, "&lt;")}</title>` +
            `<base href="${window.location.origin}/"><style>@page{size:A4;margin:10mm}body{margin:0;` +
            `-webkit-print-color-adjust:exact;print-color-adjust:exact}${REPORT_CSS}</style></head>` +
            `<body>${node.outerHTML}</body></html>`);
  doc.close();
  const go = () => {
    try { fr.contentWindow.focus(); fr.contentWindow.print(); } catch { /* print not available */ }
    setTimeout(() => fr.remove(), 60000);
  };
  const imgs = [...doc.images];
  let left = imgs.length;
  if (!left) { setTimeout(go, 100); return; }
  const done = () => { left -= 1; if (left <= 0) setTimeout(go, 100); };
  imgs.forEach(im => { if (im.complete) done(); else { im.onload = done; im.onerror = done; } });
}

/* 2026-11-12 — Save PDF: the report's HTML + CSS drawn into one A3-scaled page
   (utils/cbPdf.js, the Control Board's PDF tool) and saved to the device. */
function reportHtml(inner) {
  return `<!doctype html><html><head><meta charset="utf-8"><base href="${window.location.origin}/">` +
         // Arial in the PDF: html2canvas spaced Barlow words wrongly ("12 -11 -2026")
         `<style>body{margin:0;background:#fff}.sheet{width:960px}${REPORT_CSS}` +
         `.rjr,.rjr *{font-family:Arial,Helvetica,sans-serif!important}</style></head>` +
         `<body><div class="sheet">${inner}</div></body></html>`;
}

/* Modal shell: overlay + toolbar (Save PDF, Print, Close) + the report. */
function Shell({ z, onClose, onBack, titleBar, printTitle, token, children }) {
  const ref = useRef(null);
  const [pdf, setPdf] = useState({ busy: false, note: "" });
  const savePdf = () => {
    if (!ref.current || pdf.busy) return;
    setPdf({ busy: true, note: "" });
    savePdfFile(reportHtml(ref.current.innerHTML), printTitle || "Rejection Summary", token)
      .then(note => setPdf({ busy: false, note }))
      .catch(e => setPdf({ busy: false, note: "PDF not saved: " + ((e && e.message) || "error") }));
  };
  useEffect(() => {
    const k = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  const btn = { border: "1px solid #cbd5e1", background: "#fff", color: "#334155", borderRadius: 8,
                padding: "7px 12px", fontSize: 13, fontWeight: 700, cursor: "pointer", fontFamily: "inherit" };
  return createPortal(
    <div onClick={onClose}
         style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,.72)", zIndex: z,
                  display: "flex", justifyContent: "center", alignItems: "flex-start",
                  padding: "14px 10px 40px", overflowY: "auto", fontFamily: "'Barlow',Arial,sans-serif" }}>
      <style>{REPORT_CSS}</style>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "100%", maxWidth: 960 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap",
                      background: "#0f172a", borderRadius: 10, padding: "7px 8px 7px 12px",
                      boxShadow: "0 4px 14px rgba(0,0,0,.35)" }}>
          {onBack && <button onClick={onBack} style={btn}>← Back</button>}
          <div style={{ color: "#fff", fontWeight: 800, fontSize: 14, flex: 1, minWidth: 0,
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{titleBar}</div>
          {pdf.note && <span style={{ color: /not saved/.test(pdf.note) ? "#fca5a5" : "#86efac", fontSize: 12,
                                      fontWeight: 600 }}>{pdf.note}</span>}
          <button onClick={savePdf} disabled={pdf.busy}
                  style={{ ...btn, background: "#1e40af", borderColor: "#1e40af", color: "#fff",
                           opacity: pdf.busy ? 0.6 : 1, cursor: pdf.busy ? "wait" : "pointer" }}>
            {pdf.busy ? "Saving PDF…" : "Save PDF"}</button>
          <button onClick={() => printNode(ref.current, printTitle)} style={btn}>Print</button>
          <button onClick={onClose} aria-label="Close" style={{ ...btn, fontSize: 16, padding: "5px 12px" }}>✕</button>
        </div>
        <div ref={ref}>{children}</div>
      </div>
    </div>,
    document.body);
}

/* ── One rejection ────────────────────────────────────────────────────── */
export function RejectionReport({ id, token, onClose, onBack, z = 2100 }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  const [big, setBig] = useState(null);
  useEffect(() => {
    let dead = false;
    setD(null); setErr("");
    api.get(`/api/line-rejection/record/${id}`, token)
      .then(r => { if (!dead) setD(r); })
      .catch(e => { if (!dead) setErr(e.message || "Could not load the record."); });
    return () => { dead = true; };
  }, [id, token]);

  const r = d?.record;
  const part = r ? (r.part_name || r.part_name_free || "part") : "";
  const stage = r ? stageOf(r.status) : 0;
  const finalLabel = r?.status === "SCRAPPED" ? "Scrapped" : r && stage === 3 ? (STATUS[r.status]?.label || "Closed") : "Closed";
  const isOpen = r && (OPEN_ST.includes(r.status) || REWORK_ST.includes(r.status));
  const units = d?.units || [];
  const unitCount = (k) => units.filter(u => u.decision === k).length;
  let n = 0;

  return (
    <Shell z={z} onClose={big ? () => setBig(null) : onClose} onBack={onBack}
           titleBar={r ? `Rejection Summary — ${r.rb_no}` : "Rejection Summary"}
           printTitle={r ? `Rejection Summary ${r.rb_no}` : "Rejection Summary"} token={token}>
      {err && <div style={{ background: "#fff", borderRadius: 10, padding: 16, color: "#b91c1c", fontWeight: 700 }}>{err}</div>}
      {!r && !err && <div style={{ background: "#fff", borderRadius: 10, padding: 16, color: "#475569" }}>Loading…</div>}
      {r && (
        <div className="rjr">
          <Letterhead title="REJECTION SUMMARY REPORT" docRows={[
            ["Red Bin No.", <b key="b">{r.rb_no}</b>],
            ["Date", fmtDT(r.at)],
            ["Status", <Badge key="s" status={r.status} />],
          ]} />

          <Sec title="Summary">
            <div className="rjr-sum">
              <b>{r.qty} pc{r.qty === 1 ? "" : "s"} of {part}</b>{r.part_code ? ` (${r.part_code})` : ""} rejected
              on <b>{fmtDT(r.at)}</b>{r.shift ? `, shift ${r.shift}` : ""}, at <b>{r.zone_name}</b>
              {r.line_name ? <> · <b>{r.line_name}</b></> : null}{r.machine ? <> · {r.machine}</> : null}.{" "}
              Defect <b>{r.defect || "—"}</b>{r.reason ? <>, cause <b>{r.reason}</b> ({r.due_to || "Process"})</> : null}
              {r.why ? <> — “{r.why}”</> : null}.{" "}
              Now <b>{STATUS[r.status]?.label || r.status}</b>{r.location ? ` at ${r.location}` : ""}
              {isOpen ? <>, open for <b>{ageText(r.at)}</b></> : null}.
              {r.is_test ? <> <Badge text="Test entry" tone="grey" /></> : null}
            </div>
          </Sec>

          <Sec n={++n} title="Rejection details">
            <KV rows={[
              ["Date & time", fmtDT(r.at)], ["Shift", r.shift],
              ["Zone", r.zone_name], ["Line", r.line_name],
              ["Machine / station", r.machine], ["Sheet section", r.section],
              ["Model", r.model], ["Customer", r.customer],
              ["Part code", r.part_code], ["Part name", r.part_name || r.part_name_free],
              ["Quantity", r.qty != null ? `${r.qty} pc${r.qty === 1 ? "" : "s"}` : null],
              ["Entry", r.source === "SCAN" ? "Bar code scan" : "Manual entry"],
              ["Bar code", r.barcode], ["Batch code", r.batch_code],
              ...(r.manual_id_note ? [["ID note", r.manual_id_note, true]] : []),
            ]} />
          </Sec>

          <Sec n={++n} title="Defect and cause">
            <KV rows={[
              ["Defect", r.defect], ["Defect type", DEFECT_CAT[r.defect_cat] || r.defect_cat],
              ["Cause (4M)", r.reason], ["Due to", r.due_to],
              ["Why", r.why, true],
              ["Repeated problem", r.repeated ? "Yes" : "No"],
              ["Rejection plan", r.plan_type ? r.plan_type.charAt(0) + r.plan_type.slice(1).toLowerCase() : null],
              ...(r.plan_remark ? [["Plan remark", r.plan_remark, true]] : []),
            ]} />
          </Sec>

          <Sec n={++n} title="Current status">
            <div className="rjr-trk">
              {STAGES.map((s, i) => {
                const done = i <= stage;
                const col = i === 3 && r.status === "SCRAPPED" ? "#475569" : "#2563eb";
                return (
                  <div key={s}>
                    <div className="bar" style={{ background: done ? col : "#e2e8f0" }} />
                    <div className="lab" style={{ color: i === stage ? INK : done ? "#475569" : "#94a3b8" }}>
                      {i + 1}. {i === 3 ? finalLabel : s}{i === stage ? " ◀" : ""}
                    </div>
                  </div>
                );
              })}
            </div>
            <KV rows={[
              ["Status", STATUS[r.status]?.label || r.status], ["Location", r.location],
              [isOpen ? "Open for" : "Closed", isOpen ? ageText(r.at) : fmtDT(r.updated_at)], ["Last update", fmtDT(r.updated_at)],
              ...(units.length ? [["Pieces", [`${units.length} piece${units.length === 1 ? "" : "s"}`,
                unitCount("REWORK") ? `${unitCount("REWORK")} rework` : "",
                unitCount("SCRAP") ? `${unitCount("SCRAP")} scrap` : "",
                unitCount("PENDING") ? `${unitCount("PENDING")} pending` : ""].filter(Boolean).join(" · "), true]] : []),
            ]} />
          </Sec>

          {units.length > 0 && (
            <Sec n={++n} title={`Piece-wise decision (${units.length})`}>
              <Table rows={units} cols={[
                ["S.No.", (u, i) => i + 1], ["Piece", u => <span style={{ fontFamily: "monospace" }}>{u.unit_code}</span>],
                ["Decision", u => u.decision], ["Station", u => u.station], ["Defect found", u => u.defect],
                ["Decided", u => u.decided_at ? `${fmtDT(u.decided_at)} · ${u.decided_by || ""}` : null],
                ["Rework done", u => u.what_done], ["Final result", u => u.fi_result || u.fq_status],
              ]} />
            </Sec>
          )}

          {d.child_parts.length > 0 && (
            <Sec n={++n} title={`Child parts (${d.child_parts.length})`}>
              <Table rows={d.child_parts} cols={[
                ["S.No.", (c, i) => i + 1], ["Child part", c => c.name], ["Decision", c => c.decision || "Noted"],
                ["Qty", c => c.qty], ["Piece", c => c.unit_code], ["Station", c => c.station],
                ["Supplier", c => c.supplier], ["Remark", c => c.remark],
                ["Decided", c => c.decided_at ? `${fmtDT(c.decided_at)} · ${c.decided_by || ""}` : null],
              ]} />
            </Sec>
          )}

          {d.rework.length > 0 && (
            <Sec n={++n} title="Rework">
              <Table rows={d.rework} cols={[
                ["Rework no.", w => w.rework_no], ["Done", w => `${fmtDT(w.at)} · ${w.done_by || ""}`],
                ["What was done", w => w.what_done], ["Remarks", w => w.remarks],
                ["Final result", w => w.fi_result || w.fq_status],
              ]} />
            </Sec>
          )}

          <Sec n={++n} title={`Status history (${d.timeline.length})`}>
            {d.timeline.length
              ? <Table rows={d.timeline} cols={[
                  ["S.No.", (t, i) => i + 1], ["Date & time", t => fmtDT(t.at), { whiteSpace: "nowrap" }],
                  ["Status", t => STATUS[t.status]?.label || t.status, { whiteSpace: "nowrap" }],
                  ["By", t => t.user_name], ["Remark", t => t.note],
                ]} />
              : <div className="rjr-sum" style={{ color: "#64748b" }}>No status change recorded.</div>}
          </Sec>

          {(d.unlocks.length > 0 || d.eol_locks.length > 0 || d.qprs.length > 0) && (
            <Sec n={++n} title="Lock, unlock and QPR">
              <Table rows={[
                ...d.eol_locks.map(e => ({ kind: "Stopped at EOL", at: e.at, ref: e.scanned, by: e.reason })),
                ...d.unlocks.map(e => ({ kind: "Unlocked", at: e.at, ref: e.scanned, by: e.done_by })),
                ...d.qprs.map(q => ({ kind: `QPR ${q.status || ""}`.trim(), at: q.at, ref: q.qpr_no, by: null })),
              ]} cols={[
                ["Event", e => e.kind], ["Date & time", e => fmtDT(e.at)],
                ["Code / QPR no.", e => e.ref], ["Reason / by", e => e.by],
              ]} />
            </Sec>
          )}

          <Sec n={++n} title={`Photos (${d.photos.filter(p => p.is_image).length})`}>
            {d.photos.some(p => p.is_image)
              ? <div className="rjr-ph">
                  {d.photos.filter(p => p.is_image).map(p => (
                    <div key={p.id} style={{ textAlign: "center" }}>
                      <Photo id={p.id} token={token} size={150} onOpen={setBig} />
                      <div style={{ fontSize: 11, color: "#64748b", marginTop: 3 }}>{p.category} · {fmtDT(p.at)}</div>
                    </div>
                  ))}
                </div>
              : <div className="rjr-sum" style={{ color: "#64748b" }}>No photo attached to this record.</div>}
          </Sec>

          <div className="rjr-sec">
            <div className="rjr-sign">
              <div>Booked by<b>{r.created_by || ""}</b></div>
              <div>Operator<b>{r.operator || ""}</b></div>
              <div>Line leader<b>{r.line_leader || ""}</b></div>
              <div>Quality (sign)<b /></div>
            </div>
          </div>
          <div className="rjr-foot">
            <span>Source: Red Bin portal (read-only) · {r.source === "SCAN" ? "scanned" : "manual"} entry</span>
            <span>Generated {nowText()}</span>
          </div>
        </div>
      )}
      {big && createPortal(
        <div onClick={() => setBig(null)}
             style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.85)", zIndex: z + 100,
                      display: "flex", alignItems: "center", justifyContent: "center", cursor: "zoom-out" }}>
          <img src={big} alt="Defect" style={{ maxWidth: "94vw", maxHeight: "92vh", borderRadius: 8 }} />
        </div>, document.body)}
    </Shell>
  );
}

/* ── A group of rejections (one cell of the Defect Recording Sheet) ──────── */
export function RejectionListReport({ title, records, context = {}, token, onClose, z = 2100, sheetName }) {
  const list = (records || []).slice().sort((a, b) => String(a.at).localeCompare(String(b.at)));
  const [open, setOpen] = useState(list.length === 1 ? list[0].id : null);
  if (open) {
    return <RejectionReport id={open} token={token} z={z}
                            onBack={list.length > 1 ? () => setOpen(null) : undefined} onClose={onClose} />;
  }
  return (
    <Shell z={z} onClose={onClose} titleBar={`Rejection Summary — ${title}`} printTitle={`Rejection Summary ${title}`}
           token={token}>
      <RejectionListBody title={title} records={list} context={context} sheetName={sheetName}
                         onRow={(r) => setOpen(r.id)} />
    </Shell>
  );
}

/* 2026-11-12 — a group's PDF without opening it (Historical Data → Line
   Rejection): the same report drawn off-screen, then saved like Save PDF. */
export function saveRejectionListPdf({ title, records, context = {}, sheetName, token, fileTitle }) {
  const list = (records || []).slice().sort((a, b) => String(a.at).localeCompare(String(b.at)));
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;left:-30000px;top:0;width:960px;";
  document.body.appendChild(host);
  const root = createRoot(host);
  try {
    flushSync(() => root.render(<RejectionListBody title={title} records={list} context={context}
                                                   sheetName={sheetName} />));
    const html = reportHtml(host.innerHTML);
    return savePdfFile(html, fileTitle || `Rejection Summary ${title}`, token);
  } finally {
    root.unmount();
    host.remove();
  }
}

function RejectionListBody({ title, records, context = {}, sheetName = "Defect Recording Sheet", onRow }) {
  const list = records || [];
  const qty = list.reduce((s, r) => s + (Number(r.qty) || 0), 0);
  const days = [...new Set(list.map(r => String(r.at).slice(0, 10)))].sort();
  const dd = (s) => `${s.slice(8, 10)}-${s.slice(5, 7)}-${s.slice(0, 4)}`;
  const period = !days.length ? "—" : days.length === 1 ? dd(days[0]) : `${dd(days[0])} to ${dd(days[days.length - 1])}`;
  const shifts = [...new Set(list.map(r => r.shift).filter(Boolean))].sort().join(", ");
  const by = (key, label) => {
    const m = {};
    list.forEach(r => { const k = label(r); if (k) m[k] = (m[k] || 0) + (Number(r.qty) || 0); });
    return Object.entries(m).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v}`).join(" · ");
  };
  const grp = {};
  list.forEach(r => { const g = groupOf(r.status); grp[g] = (grp[g] || 0) + (Number(r.qty) || 0); });
  const lineText = [context.line_name, context.rb_line
    ? `(Red Bin: ${String(context.rb_zone || "").replace(/^Zone\s*\d+\s*-\s*/i, "")} ${context.rb_line})` : ""]
    .filter(Boolean).join(" ");
  return (
      <div className="rjr">
        <Letterhead title="REJECTION SUMMARY" docRows={[
          ["Sheet", sheetName], ["Period", period], ["Generated", nowText()],
        ]} />
        <Sec title="Summary">
          <div className="rjr-sum">
            <b>{qty} pc{qty === 1 ? "" : "s"}</b> rejected in <b>{list.length}</b> Red Bin record{list.length === 1 ? "" : "s"}
            {" "}for <b>{title}</b>{lineText ? <> on {lineText}</> : null}.
          </div>
          <KV rows={[
            ["Line", lineText], ["Period", period],
            ["Shift", shifts], ["Total quantity", `${qty} pc${qty === 1 ? "" : "s"}`],
            ["Defects", by("defect", r => r.defect || "(no defect name)"), true],
            ["Cause (4M)", by("reason", r => r.reason ? `${r.reason} (${DUE[r.due] || "Process"})` : null), true],
            ["Status", GROUPS.filter(g => grp[g.key]).map(g => `${g.label} ${grp[g.key]}`).join(" · "), true],
          ]} />
        </Sec>
        <Sec n={1} title={`Rejection records (${list.length})${onRow ? " — click a row for its full report" : ""}`}>
          <Table rows={list} onRow={onRow} cols={[
            ["S.No.", (r, i) => i + 1],
            ["Red Bin No.", r => <span style={{ fontFamily: "monospace", whiteSpace: "nowrap" }}>{r.rb_no}</span>],
            ["Date & time", r => fmtDT(r.at), { whiteSpace: "nowrap" }],
            ["Shift", r => r.shift],
            ["Machine / station", r => r.machine],
            ["Part", r => [r.part_code, r.part_name].filter(Boolean).join(" · ") || null],
            ["Defect", r => r.defect],
            ["Due to", r => DUE[r.due] || "Process"],
            ["Qty", r => r.qty, { textAlign: "right" }],
            ["Status", r => <Badge status={r.status} />],
          ]} total={
            <tr className="tot">
              <td colSpan={8}>Total</td>
              <td style={{ textAlign: "right" }}>{qty}</td>
              <td />
            </tr>
          } />
        </Sec>
        <div className="rjr-foot">
          <span>Source: Red Bin portal (read-only)</span>
          <span>{context.month ? `Month ${context.month}` : ""}</span>
        </div>
      </div>
  );
}
