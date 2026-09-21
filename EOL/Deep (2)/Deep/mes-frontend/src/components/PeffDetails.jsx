// PeffDetails.jsx — the PEFF man-hour breakdown (Detail Working Hrs + Semi Core
// Job + Non Core Job + New Prod Prep Job) as a per-line DATA view, so the same
// numbers the operator writes on the paper PEFF sheet can be read off the
// dashboard. Rendered under the Production → "Work Center (PEFF)" area and beside
// the Historical hourly data.
//
// STRUCTURE is fixed (from the official sheet). VALUES are wired to a `data`
// object keyed by "<section>.<code>" — empty for now; the auto-fill calculations
// will be added later (operator will specify them) and this component will then
// show live per-line numbers without any layout change.

const SECTIONS = {
  workingHrs: {
    title: "Detail Working Hrs",
    cols: ["Hour", "Present Manpower", "Man Hour"],
    rows: [
      { code: "normal",    label: "Normal (a)" },
      { code: "overtime",  label: "Overtime (b)" },
      { code: "subtotal",  label: "Sub Total (a+b)", strong: true },
      { code: "mp_loan",   label: "MP Loan to other Section ( c ) BANTUAN" },
      { code: "wc1",       label: "*WC Code" },
      { code: "mp_borrow", label: "MP Borrow from other" },
      { code: "wc2",       label: "**WC Code" },
    ],
  },
  semiCore: {
    title: "Semi Core Job",
    rows: [
      { code: 31, label: "Line Stop Due to Special Case (factor of Customer)" },
      { code: 32, label: "Leave with Paid Hrs ( Meeting, Inventory, Health checkup... )" },
      { code: 33, label: "OJT Time (off line only) * inline OJT as cyclic job" },
      { code: 34, label: "Education Training ( QCC )" },
      { code: 35, label: "TPS Activity ( Jishuken )" },
      { code: 36, label: "TPM Activity" },
      { code: 37, label: "QMS - EHS Activity" },
      { code: 38, label: "TQM Activity" },
      { code: 39, label: "5 S Activity Planned / unplanned" },
      { code: 82, label: "Paid Break ( 0.33 Hrs )" },
    ],
  },
  nonCore: {
    title: "Non Core Job",
    rows: [
      { code: 42, label: "Support to Prototype Activity" },
      { code: 43, label: "Spare Part ( Infrequently Prod )" },
      { code: 51, label: "Supporting ( Supplier activity )" },
      { code: 61, label: "Work Support to other Dept. (not related Productivity - HR)" },
    ],
  },
  npp: {
    title: "New Prod. Prep. Job ( NPP )",
    rows: [
      { code: 91, label: "On line Trial ( Mass Production )" },
      { code: 92, label: "Team Member Training" },
      { code: 93, label: "Documentation ( new product )" },
      { code: 94, label: "Fitting Out ( Maint of New Lines )" },
      { code: 95, label: "Trial After Quality Issue" },
      { code: 96, label: "Simultaneous Engg. ( SE ) activities" },
      { code: 97, label: "Quality gate - Special insp ( IFM )" },
    ],
  },
};

const C = {
  card: { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10,
          overflow: "hidden" },
  h: { margin: 0, padding: "10px 14px", fontSize: 15, fontWeight: 800,
       color: "#0f172a", background: "linear-gradient(135deg,#eef2ff,#e0e7ff)",
       borderBottom: "1px solid #dbe4f0", display: "flex", alignItems: "center",
       justifyContent: "space-between" },
  sub: { fontSize: 12.5, fontWeight: 700, color: "#334155", padding: "8px 14px 4px" },
  table: { width: "100%", borderCollapse: "collapse", fontSize: 12 },
  th: { textAlign: "left", padding: "5px 8px", background: "#f1f5f9",
        borderBottom: "1px solid #e2e8f0", color: "#475569", fontSize: 10,
        textTransform: "uppercase", letterSpacing: ".02em", fontWeight: 700 },
  thNum: { textAlign: "right", width: 56, whiteSpace: "nowrap" },
  td: { padding: "5px 8px", borderBottom: "1px solid #eef2f6", color: "#1e293b" },
  tdNum: { textAlign: "right", fontVariantNumeric: "tabular-nums", width: 56,
           color: "#0f172a", fontWeight: 600 },
  no: { width: 26, textAlign: "center", color: "#64748b" },
  totalRow: { fontWeight: 800, background: "#f8fafc" },
};

// value lookup: data["<sectionKey>.<code>"]  (or ".<code>.<col>" for workingHrs)
const cell = (data, key) => {
  const v = data && data[key];
  return (v === 0 || v) ? String(v) : "";
};

export default function PeffDetails({ lineName, date, shift, data = {} }) {
  const ctx = [lineName, shift && `Shift ${shift}`, date].filter(Boolean).join("  ·  ");
  const jobSection = (key, sec, withTotal = true) => (
    <div style={{ ...C.card, marginBottom: 12 }}>
      <div style={C.sub}>{sec.title}</div>
      <table style={C.table}>
        <thead><tr>
          <th style={{ ...C.th, ...C.no }}>No.</th>
          <th style={C.th}>{sec.title}</th>
          <th style={{ ...C.th, ...C.thNum }}>Man Hour</th>
        </tr></thead>
        <tbody>
          {sec.rows.map(r => (
            <tr key={r.code}>
              <td style={{ ...C.td, ...C.no }}>{r.code}</td>
              <td style={C.td}>{r.label}</td>
              <td style={C.tdNum}>{cell(data, `${key}.${r.code}`)}</td>
            </tr>
          ))}
          {withTotal && (
            <tr style={C.totalRow}>
              <td style={{ ...C.td, ...C.no }}></td>
              <td style={C.td}>TOTAL</td>
              <td style={C.tdNum}>{cell(data, `${key}.total`)}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );

  const wh = SECTIONS.workingHrs;
  return (
    <div style={C.card}>
      <h3 style={C.h}>
        <span>📋 PEFF Details</span>
        <span style={{ fontSize: 12, fontWeight: 600, color: "#475569" }}>{ctx}</span>
      </h3>
      <div style={{ padding: 12 }}>
        {/* Detail Working Hrs */}
        <div style={{ ...C.card, marginBottom: 12 }}>
          <div style={C.sub}>{wh.title}</div>
          <table style={C.table}>
            <thead><tr>
              <th style={C.th}></th>
              {wh.cols.map(c => <th key={c} style={{ ...C.th, ...C.thNum }}>{c}</th>)}
            </tr></thead>
            <tbody>
              {wh.rows.map(r => (
                <tr key={r.code} style={r.strong ? C.totalRow : undefined}>
                  <td style={C.td}>{r.label}</td>
                  <td style={C.tdNum}>{cell(data, `workingHrs.${r.code}.hour`)}</td>
                  <td style={C.tdNum}>{cell(data, `workingHrs.${r.code}.present`)}</td>
                  <td style={C.tdNum}>{cell(data, `workingHrs.${r.code}.man`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {jobSection("semiCore", SECTIONS.semiCore)}
        {jobSection("nonCore", SECTIONS.nonCore)}
        {jobSection("npp", SECTIONS.npp)}
        <div style={{ fontSize: 11.5, color: "#94a3b8", marginTop: 4 }}>
          Auto-fill of the Man / Hour values is pending — calculations will be
          added next (per your inputs); the layout and per-line context above are ready.
        </div>
      </div>
    </div>
  );
}
