// PeffSheet.jsx  (/peff-sheet)  2026-09-07
// ───────────────────────────────────────────────────────────────────────
// Hosts the standalone PEFF hourly-production check-sheet inside the MES.
// The sheet is a fully self-contained HTML document (its own CSS / JS / SVG
// form + html2canvas/jsPDF export), so it runs cleanest in an <iframe> that
// isolates its scripts and styles from the React app. Because the file is
// served from the MES origin (public/peff-sheet.html → /peff-sheet.html), its
// own JS can later call the MES /api/* directly (same-origin) to auto-fill the
// fields for a chosen line / shift / date.
import PageTopbar from "../components/PageTopbar";

export default function PeffSheet() {
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh",
                  background: "#fff", minHeight: 0 }}>
      <PageTopbar leading="PEFF" accent="Sheet" />
      <iframe
        src="/peff-sheet.html"
        title="PEFF Sheet"
        style={{ flex: 1, width: "100%", border: "none", minHeight: 0 }}
      />
    </div>
  );
}
