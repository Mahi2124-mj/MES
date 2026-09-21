/* ───────────────────────────────────────────────────────────────────
 * LogBook.jsx
 * ───────────────────────────────────────────────────────────────────
 * 2026-06-12 — Maintenance Daily Log Book.  A 1:1 digital replica of the
 * paper form (FORMAT NO. TBDI / MAINT. / F / 008) so the team can fill,
 * save and print it from the dashboard:
 *
 *   TOYOTA BOSHOKU DEVICE INDIA PVT.LTD.
 *   MACHINE MAINTENANCE / TOOL ROOM / PRESS SHOP - DAILY LOG BOOK
 *
 * Columns: S.No · Machine Name/No · Problem Reported/Found · Action Taken
 *          · Breakdown Time (Start · On · Down min) · Spares Used
 *          · Attended By · Remark
 *
 * Persistence: per (date, shift) in localStorage — purely additive, no
 * backend / collector / DB touch.  Print button uses the browser print
 * (toolbar hidden via @media print).
 * ─────────────────────────────────────────────────────────────────── */
import { useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";

const FOOTER = "FORMAT NO.:- TBDI / MAINT. / F / 008        REV. NO.:- 00        REV. DATE:- 20/03/2024";

const BLANK_ROW = () => ({
  machine: "", problem: "", action: "",
  startTime: "", onTime: "", downTime: "",
  spares: "", attendedBy: "", remark: "",
});
const BLANK_HDR = () => ({
  present: "", onLeave: "", workHours: "", overTime: "", downTime: "",
});
const INITIAL_ROWS = 16;

function todayISO() {
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export default function LogBook() {
  const { token } = useAuth();
  const [date,  setDate]  = useState(todayISO());
  const [shift, setShift] = useState("A");
  const [hdr,   setHdr]   = useState(BLANK_HDR);
  const [rows,  setRows]  = useState(() => Array.from({ length: INITIAL_ROWS }, BLANK_ROW));
  const [savedAt, setSavedAt] = useState(0);
  const [saving,  setSaving]  = useState(false);
  const [err,     setErr]     = useState("");

  // Load the sheet for the chosen date+shift FROM THE DB.
  useEffect(() => {
    let stop = false;
    setErr("");
    fetch(`/api/maintenance-logbook/sheet?date=${date}&shift=${encodeURIComponent(shift)}`,
          { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then((r) => (r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`)))
      .then((d) => {
        if (stop) return;
        setHdr({ ...BLANK_HDR(), ...(d.hdr || {}) });
        setRows(Array.isArray(d.rows) && d.rows.length
          ? d.rows.map((r) => ({ ...BLANK_ROW(), ...r }))
          : Array.from({ length: INITIAL_ROWS }, BLANK_ROW));
      })
      .catch(() => {
        if (stop) return;
        setHdr(BLANK_HDR());
        setRows(Array.from({ length: INITIAL_ROWS }, BLANK_ROW));
      });
    return () => { stop = true; };
  }, [date, shift, token]);

  const save = () => {
    setSaving(true); setErr("");
    fetch(`/api/maintenance-logbook/sheet`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ date, shift, hdr, rows }),
    })
      .then((r) => (r.ok ? r.json() : r.text().then((t) => Promise.reject(t || `HTTP ${r.status}`))))
      .then(() => setSavedAt(Date.now()))
      .catch((e) => setErr(String(e).slice(0, 200)))
      .finally(() => setSaving(false));
  };
  const setRow = (i, k, v) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  const setH   = (k, v)    => setHdr((h) => ({ ...h, [k]: v }));
  const addRow = ()  => setRows((rs) => [...rs, BLANK_ROW()]);
  const delRow = (i) => setRows((rs) => (rs.length > 1 ? rs.filter((_, j) => j !== i) : rs));

  // Auto total down-time = Σ row down-times (still shown in the header cell).
  const sumDown = useMemo(
    () => rows.reduce((a, r) => a + (parseFloat(r.downTime) || 0), 0),
    [rows]
  );

  // ── styles ────────────────────────────────────────────────────────
  const border = "1px solid #000";
  const th = {
    border, padding: "4px 5px", fontSize: 10.5, fontWeight: 800,
    textAlign: "center", verticalAlign: "middle", background: "#f3f4f6",
    color: "#111827", letterSpacing: ".01em",
  };
  const td  = { border, padding: 0, verticalAlign: "top" };
  const inp = {
    width: "100%", border: "none", outline: "none", background: "transparent",
    font: "inherit", fontSize: 11, padding: "3px 5px", boxSizing: "border-box",
    color: "#111827", resize: "none",
  };
  const hdrInp = { ...inp, borderBottom: "1px solid #94a3b8", fontWeight: 600 };
  const cell = (i, k, opts = {}) => (
    <td style={td}>
      <textarea rows={opts.rows || 1} value={rows[i][k]}
        onChange={(e) => setRow(i, k, e.target.value)}
        style={{ ...inp, textAlign: opts.center ? "center" : "left",
                 minHeight: 24, overflow: "hidden" }} />
    </td>
  );

  return (
    <div className="lb-outer" style={{ padding: 16, background: "#e5e7eb", minHeight: "100%", overflow: "auto" }}>
      {/* injected style: cell focus tint + hide toolbar on print */}
      <style>{`
        .lb-sheet textarea:focus { background:#fffbe6; }
        /* Print: auto LANDSCAPE, no page margins → sheet fills the paper edge-to-edge. */
        @page { size: A4 landscape; margin: 0; }
        @media print {
          html, body { margin:0 !important; padding:0 !important; background:#fff !important;
                       -webkit-print-color-adjust:exact; print-color-adjust:exact; }
          .lb-noprint { display:none !important; }
          .lb-outer  { padding:0 !important; background:#fff !important; overflow:visible !important; }
          .lb-sheet  { box-shadow:none !important; margin:0 !important;
                       max-width:none !important; width:100% !important; padding:2mm !important;
                       box-sizing:border-box !important;
                       display:flex !important; flex-direction:column !important;
                       min-height:205mm !important; }
          .lb-sheet > table { width:100% !important; }
          /* daily log grid stretches to fill the page height (rows distribute) → no empty bottom */
          .lb-logtable { flex:1 1 auto !important; }
        }
      `}</style>

      {/* ── toolbar (not part of the form, hidden on print) ── */}
      <div className="lb-noprint" style={{
        display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center",
        marginBottom: 12, maxWidth: 1180, marginInline: "auto",
      }}>
        <span style={{ fontSize: 15, fontWeight: 800, color: "#0f172a" }}>📒 Maintenance Log Book</span>
        <span style={{ flex: 1 }} />
        <label style={{ fontSize: 12, color: "#334155", fontWeight: 600 }}>
          Date{" "}
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)}
            style={{ padding: "4px 6px", borderRadius: 6, border: "1px solid #cbd5e1" }} />
        </label>
        <label style={{ fontSize: 12, color: "#334155", fontWeight: 600 }}>
          Shift{" "}
          <select value={shift} onChange={(e) => setShift(e.target.value)}
            style={{ padding: "4px 6px", borderRadius: 6, border: "1px solid #cbd5e1" }}>
            <option value="A">A</option><option value="B">B</option><option value="C">C</option>
          </select>
        </label>
        <button onClick={addRow} style={{
          padding: "6px 14px", borderRadius: 6, border: "1px solid #cbd5e1",
          background: "#fff", fontWeight: 700, fontSize: 12, cursor: "pointer" }}>+ Row</button>
        <button onClick={() => window.print()} style={{
          padding: "6px 14px", borderRadius: 6, border: "1px solid #cbd5e1",
          background: "#fff", fontWeight: 700, fontSize: 12, cursor: "pointer" }}>🖨 Print</button>
        <button onClick={save} disabled={saving} style={{
          padding: "6px 16px", borderRadius: 6, border: "none",
          background: saving ? "#94a3b8"
                     : savedAt && Date.now() - savedAt < 2500 ? "#16a34a" : "#2563eb",
          color: "#fff", fontWeight: 800, fontSize: 12,
          cursor: saving ? "default" : "pointer" }}>
          {saving ? "Saving…" : savedAt && Date.now() - savedAt < 2500 ? "✓ Saved" : "Save"}
        </button>
        {err && <span className="lb-noprint" style={{ color: "#dc2626", fontSize: 11, fontWeight: 700 }}>{err}</span>}
      </div>

      {/* ── the form sheet ── */}
      <div className="lb-sheet" style={{
        background: "#fff", color: "#111827", maxWidth: 1180, margin: "0 auto",
        boxShadow: "0 6px 24px rgba(0,0,0,.18)", padding: 14,
      }}>
        {/* title band */}
        <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed" }}>
          <tbody>
            <tr>
              <td style={{ border, width: 120, padding: "6px", background: "#fff", textAlign: "center" }}>
                <img src="/logo.jpg" alt="Toyota Boshoku"
                     style={{ maxWidth: "100%", maxHeight: 56, objectFit: "contain",
                              display: "block", margin: "0 auto" }} />
              </td>
              <td style={{ border, padding: "4px 8px", textAlign: "center" }}>
                <div style={{ fontSize: 18, fontWeight: 900, letterSpacing: ".02em" }}>
                  TOYOTA BOSHOKU DEVICE INDIA PVT.LTD.
                </div>
                <div style={{ fontSize: 12.5, fontWeight: 700, marginTop: 2 }}>
                  MACHINE MAINTENANCE / TOOL ROOM / PRESS SHOP - DAILY LOG BOOK
                </div>
              </td>
              <td style={{ border, width: 170, padding: "4px 8px", verticalAlign: "top", fontSize: 11 }}>
                <div style={{ textAlign: "right", fontSize: 10, color: "#475569" }}>Page 1 of 1</div>
                <div style={{ marginTop: 4, display: "flex", alignItems: "center", gap: 4 }}>
                  <b>DATE:</b>
                  <input value={date} onChange={(e) => setDate(e.target.value)} style={{ ...hdrInp, flex: 1 }} />
                </div>
                <div style={{ marginTop: 4, display: "flex", alignItems: "center", gap: 4 }}>
                  <b>SHIFT:</b>
                  <input value={shift} onChange={(e) => setShift(e.target.value)} style={{ ...hdrInp, flex: 1 }} />
                </div>
              </td>
            </tr>
          </tbody>
        </table>

        {/* employees + totals band */}
        <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed", borderTop: "none" }}>
          <tbody>
            <tr>
              <td style={{ border, padding: "4px 8px", fontSize: 11, width: "50%" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <b style={{ whiteSpace: "nowrap" }}>EMPLOYEES PRESENT:-</b>
                  <input value={hdr.present} onChange={(e) => setH("present", e.target.value)} style={{ ...hdrInp, flex: 1 }} />
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 5 }}>
                  <b style={{ whiteSpace: "nowrap" }}>EMPLOYEES ON LEAVE:-</b>
                  <input value={hdr.onLeave} onChange={(e) => setH("onLeave", e.target.value)} style={{ ...hdrInp, flex: 1 }} />
                </div>
              </td>
              <td style={{ border, padding: "4px 8px", fontSize: 11, width: "50%" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <b style={{ whiteSpace: "nowrap" }}>TOTAL WORKING HOURS:-</b>
                  <input value={hdr.workHours} onChange={(e) => setH("workHours", e.target.value)} style={{ ...hdrInp, flex: 1 }} />
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 5 }}>
                  <b style={{ whiteSpace: "nowrap" }}>TOTAL OVER TIME:-</b>
                  <input value={hdr.overTime} onChange={(e) => setH("overTime", e.target.value)} style={{ ...hdrInp, flex: 1 }} />
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 5 }}>
                  <b style={{ whiteSpace: "nowrap" }}>TOTAL DOWN TIME:-</b>
                  <input value={hdr.downTime} onChange={(e) => setH("downTime", e.target.value)}
                    placeholder={sumDown ? `${sumDown} min` : ""} style={{ ...hdrInp, flex: 1 }} />
                </div>
              </td>
            </tr>
          </tbody>
        </table>

        {/* the log table */}
        <table className="lb-logtable" style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed", borderTop: "none" }}>
          <colgroup>
            <col style={{ width: "3.5%" }} /><col style={{ width: "10%" }} />
            <col style={{ width: "15%" }} /><col style={{ width: "20%" }} />
            <col style={{ width: "6%" }} /><col style={{ width: "6%" }} /><col style={{ width: "7%" }} />
            <col style={{ width: "10%" }} /><col style={{ width: "7.5%" }} /><col style={{ width: "8.5%" }} />
            <col style={{ width: "3%" }} />
          </colgroup>
          <thead>
            <tr>
              <th style={th} rowSpan={2}>S.NO.</th>
              <th style={th} rowSpan={2}>MACHINE NAME / NO.</th>
              <th style={th} rowSpan={2}>PROBLEM REPORTED / FOUND</th>
              <th style={th} rowSpan={2}>ACTION TAKEN</th>
              <th style={th} colSpan={3}>BREAKDOWN TIME</th>
              <th style={th} rowSpan={2}>SPARES USED</th>
              <th style={th} rowSpan={2}>ATTENDED BY</th>
              <th style={th} rowSpan={2}>REMARK</th>
              <th style={{ ...th, background: "#fff", border: "none" }} className="lb-noprint" rowSpan={2}></th>
            </tr>
            <tr>
              <th style={{ ...th, fontSize: 9.5 }}>START TIME</th>
              <th style={{ ...th, fontSize: 9.5 }}>ON TIME</th>
              <th style={{ ...th, fontSize: 9.5 }}>DOWN TIME<br />(IN MINUTES)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td style={{ ...th, background: "#fff", fontWeight: 700, fontSize: 11 }}>{i + 1}</td>
                {cell(i, "machine")}
                {cell(i, "problem")}
                {cell(i, "action")}
                {cell(i, "startTime", { center: true })}
                {cell(i, "onTime", { center: true })}
                {cell(i, "downTime", { center: true })}
                {cell(i, "spares")}
                {cell(i, "attendedBy")}
                {cell(i, "remark")}
                <td style={{ border: "none", textAlign: "center" }} className="lb-noprint">
                  <button onClick={() => delRow(i)} title="Delete row"
                    style={{ border: "none", background: "transparent", color: "#dc2626",
                             cursor: "pointer", fontWeight: 800, fontSize: 14 }}>×</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {/* footer format no. */}
        <div style={{ textAlign: "center", fontSize: 11, fontWeight: 700, marginTop: 8, color: "#111827" }}>
          {FOOTER}
        </div>
      </div>
    </div>
  );
}
