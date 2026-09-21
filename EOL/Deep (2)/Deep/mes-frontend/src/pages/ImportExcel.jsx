import { useState, useEffect, useRef } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import AIAssistant from "../components/AIAssistant";


// Required columns for import validation
const REQUIRED_COLS = ["Date", "Shift", "OK Count", "NG Count", "Plan Completed"];

function fmtBytes(b) {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MB`;
}

export default function ImportExcel() {
  const { token, theme } = useAuth();

  // Export state
  const [zones,       setZones]     = useState([]);
  const [lines,       setLines]     = useState([]);
  const [expZone,     setExpZone]   = useState("");
  const [expLine,     setExpLine]   = useState("");
  const [expFrom,     setExpFrom]   = useState(() => {
    const d = new Date(); d.setDate(d.getDate() - 7);
    return d.toISOString().split("T")[0];
  });
  const [expTo,       setExpTo]     = useState(new Date().toISOString().split("T")[0]);
  const [expFmt,      setExpFmt]    = useState("xlsx");
  const [exporting,   setExporting] = useState(false);
  const [expError,    setExpError]  = useState("");
  useEffect(() => { document.title = "Import / Export"; }, []);

  // Import state
  const [file,        setFile]      = useState(null);
  const [impLine,     setImpLine]   = useState("");
  const [parsing,     setParsing]   = useState(false);
  const [parsed,      setParsed]    = useState(null);   // { headers, rows }
  const [validation,  setValidation]= useState(null);   // { ok, errors, warnings }
  const [importing,   setImporting] = useState(false);
  const [impResult,   setImpResult] = useState(null);
  const [dragOver,    setDragOver]  = useState(false);
  const fileRef = useRef(null);

  // Toast
  const [toast, setToast] = useState(null);
  const showToast = (msg, type = "ok") => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 4000);
  };

  // Load zones + lines
  useEffect(() => {
    api.get("/api/zones/", token).then(d => setZones(Array.isArray(d) ? d : [])).catch(() => {});
    api.get("/api/lines/", token).then(d => setLines(Array.isArray(d) ? d : [])).catch(() => {});
  }, [token]);

  // Filter lines by selected export zone
  const expLines = expZone ? lines.filter(l => String(l.zone_id) === String(expZone)) : lines;

  // ── EXPORT ──────────────────────────────────────────────────
  const handleExport = async () => {
    if (!expLine) { setExpError("Please select a line"); return; }
    if (!expFrom || !expTo) { setExpError("Please select date range"); return; }
    if (expFrom > expTo) { setExpError("From date must be before To date"); return; }
    setExpError(""); setExporting(true);

    try {
      const url = `/api/export/data?line_id=${expLine}&date_from=${expFrom}&date_to=${expTo}&format=${expFmt}`;
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Export failed" }));
        throw new Error(err.detail || "Export failed");
      }

      // Trigger download
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      const lineName = lines.find(l => String(l.id) === String(expLine))?.line_code || "line";
      a.download = `${lineName}_${expFrom}_to_${expTo}.${expFmt}`;
      a.click();
      URL.revokeObjectURL(a.href);
      showToast(`Exported successfully as .${expFmt} ✓`);
    } catch (e) {
      setExpError(e.message);
    } finally {
      setExporting(false);
    }
  };

  // ── IMPORT — Parse file ──────────────────────────────────────
  const parseFile = async (f) => {
    if (!f) return;
    setFile(f); setParsed(null); setValidation(null); setImpResult(null);
    setParsing(true);

    try {
      const ext = f.name.split(".").pop().toLowerCase();

      if (ext === "csv") {
        const text = await f.text();
        const lines_raw = text.split("\n").filter(l => l.trim());

        // Skip meta rows (first 3 rows are title, info, blank in our export format)
        // Find the header row — it's the row containing "Date" and "Shift"
        let headerIdx = 0;
        for (let i = 0; i < Math.min(lines_raw.length, 6); i++) {
          if (lines_raw[i].includes("Date") && lines_raw[i].includes("Shift")) {
            headerIdx = i; break;
          }
        }

        const headers = lines_raw[headerIdx].split(",").map(h => h.trim().replace(/^"|"$/g, ""));
        const rows = lines_raw.slice(headerIdx + 1).map(line => {
          const vals = line.split(",").map(v => v.trim().replace(/^"|"$/g, ""));
          const obj = {};
          headers.forEach((h, i) => { obj[h] = vals[i] || ""; });
          return obj;
        }).filter(r => r["Date"] || r["Shift"]);

        setParsed({ headers, rows, ext });
        validateData(headers, rows);

      } else if (ext === "xlsx") {
        // Use SheetJS via CDN
        await loadSheetJS();
        const buf = await f.arrayBuffer();
        const wb  = window.XLSX.read(buf, { type: "array" });
        const ws  = wb.Sheets[wb.SheetNames[0]];
        const raw = window.XLSX.utils.sheet_to_json(ws, { header: 1 });

        // Find header row
        let headerIdx = 0;
        for (let i = 0; i < Math.min(raw.length, 6); i++) {
          if (raw[i]?.includes("Date") && raw[i]?.includes("Shift")) {
            headerIdx = i; break;
          }
        }

        const headers = raw[headerIdx].map(h => String(h || "").trim());
        const rows = raw.slice(headerIdx + 1)
          .filter(r => r.length > 0 && (r[0] || r[1]))
          .map(r => {
            const obj = {};
            headers.forEach((h, i) => { obj[h] = r[i] !== undefined ? r[i] : ""; });
            return obj;
          });

        setParsed({ headers, rows, ext });
        validateData(headers, rows);
      } else {
        throw new Error("Unsupported file type. Use .xlsx or .csv");
      }
    } catch (e) {
      showToast(e.message, "err");
      setFile(null);
    } finally {
      setParsing(false);
    }
  };

  const loadSheetJS = () => new Promise((res, rej) => {
    if (window.XLSX) return res();
    const s = document.createElement("script");
    s.src = "/xlsx.full.min.js";  // local copy for air-gapped LAN
    s.onload = res; s.onerror = rej;
    document.head.appendChild(s);
  });

  const validateData = (headers, rows) => {
    const errors = [];
    const warnings = [];

    // Check required columns
    const missing = REQUIRED_COLS.filter(c => !headers.includes(c));
    if (missing.length > 0) {
      errors.push(`Missing required columns: ${missing.join(", ")}`);
    }

    // Check rows have data
    if (rows.length === 0) {
      errors.push("File contains no data rows");
    }

    // Check date format
    const badDates = rows.filter(r => r["Date"] && !/^\d{4}-\d{2}-\d{2}$/.test(String(r["Date"]))).length;
    if (badDates > 0) {
      warnings.push(`${badDates} rows have non-standard date format (expected YYYY-MM-DD)`);
    }

    // Check shift values
    const badShifts = rows.filter(r => r["Shift"] && !["A","B"].includes(String(r["Shift"]).trim())).length;
    if (badShifts > 0) {
      warnings.push(`${badShifts} rows have unexpected shift values (expected A or B)`);
    }

    // Check numeric columns
    const numCols = ["OK Count", "NG Count", "Plan Completed"];
    numCols.forEach(col => {
      if (!headers.includes(col)) return;
      const bad = rows.filter(r => r[col] !== "" && isNaN(Number(r[col]))).length;
      if (bad > 0) warnings.push(`${bad} rows have non-numeric values in "${col}"`);
    });

    setValidation({ ok: errors.length === 0, errors, warnings });
  };

  // ── IMPORT — Submit ──────────────────────────────────────────
  const handleImport = async () => {
    if (!impLine) { showToast("Please select a line to import into", "err"); return; }
    if (!parsed || !validation?.ok) return;
    setImporting(true);

    try {
      // Map our export column names back to API field names
      const mapped = parsed.rows.map(r => ({
        record_date:           String(r["Date"] || ""),
        shift_name:            String(r["Shift"] || "").trim(),
        ok_count:              parseInt(r["OK Count"] || 0) || 0,
        ng_count:              parseInt(r["NG Count"] || 0) || 0,
        shift_plan_completed:  parseInt(r["Plan Completed"] || 0) || 0,
        overall_oee:           parseFloat(r["Overall OEE (%)"] || 0) || 0,
        availability:          parseFloat(r["Availability (%)"] || 0) || 0,
        performance:           parseFloat(r["Performance (%)"] || 0) || 0,
        quality_oee:           parseFloat(r["Quality (%)"] || 0) || 0,
      }));

      const res = await api.post("/api/import/excel", {
        line_id: parseInt(impLine),
        data: mapped,
        source: "import_page",
      }, token);

      setImpResult({ success: true, imported: mapped.length, message: res.message });
      showToast(`${mapped.length} records imported successfully ✓`);
      setParsed(null); setFile(null); setValidation(null);
    } catch (e) {
      setImpResult({ success: false, message: e.message });
      showToast(e.message, "err");
    } finally {
      setImporting(false);
    }
  };

  const resetImport = () => {
    setFile(null); setParsed(null); setValidation(null);
    setImpResult(null);
    if (fileRef.current) fileRef.current.value = "";
  };

  // ── Styles ───────────────────────────────────────────────────
  const inputSt = {
    background: "#f8fafc", border: "1.5px solid #e2e8f0", borderRadius: 8,
    padding: "10px 12px", color: "#0f172a", fontFamily: "'Barlow',sans-serif",
    fontSize: 13, outline: "none", width: "100%",
    transition: "border-color .15s",
  };

  const sectionTitle = (text) => (
    <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: ".1em", textTransform: "uppercase", color: "#64748b", marginBottom: 14, display: "flex", alignItems: "center", gap: 8 }}>
      {text}
      <div style={{ flex: 1, height: 1, background: "#e2e8f0" }} />
    </div>
  );

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@700;800&display=swap');
        * { box-sizing: border-box; }
        .imp-input:focus { border-color: #3b82f6 !important; box-shadow: 0 0 0 3px rgba(59,130,246,.1); }
        .fmt-btn { padding: 8px 18px; border-radius: 8px; border: 1.5px solid #e2e8f0; background: #f8fafc; font-family: 'Barlow',sans-serif; font-size: 13px; font-weight: 600; color: #64748b; cursor: pointer; transition: all .12s; }
        .fmt-btn.active { background: ${theme.accentDark}; border-color: ${theme.accentDark}; color: #fff; box-shadow: 0 2px 8px ${theme.soft}; }
        .fmt-btn:hover:not(.active) { border-color: ${theme.accent}; color: ${theme.accent}; }
        @keyframes slideUp { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:none} }
        @keyframes spin { to{transform:rotate(360deg)} }
      `}</style>

      {/* Toast */}
      {toast && (
        <div style={{
          position: "fixed", bottom: 24, right: 24, zIndex: 999,
          background: "#fff",
          borderLeft: `4px solid ${toast.type === "ok" ? "#16a34a" : "#dc2626"}`,
          border: `1px solid ${toast.type === "ok" ? "rgba(22,163,74,.25)" : "rgba(220,38,38,.25)"}`,
          borderRadius: 10, padding: "12px 18px",
          boxShadow: "0 8px 30px rgba(0,0,0,.12)",
          fontSize: 13, fontWeight: 600,
          color: toast.type === "ok" ? "#16a34a" : "#dc2626",
          animation: "slideUp .2s ease", minWidth: 260,
        }}>{toast.msg}</div>
      )}

      <div style={{ minHeight: "100vh", background: "#f8fafc", fontFamily: "'Barlow',sans-serif", paddingBottom: 60 }}>

        {/* Topbar */}
        <div style={{ background: "#fff", borderBottom: "1px solid #e2e8f0", padding: "0 40px 0 88px", height: 60, display: "flex", alignItems: "center", position: "sticky", top: 0, zIndex: 100, boxShadow: "0 1px 3px rgba(0,0,0,.06)" }}>
          <div style={{ fontFamily: "'Barlow Condensed',sans-serif", fontSize: 22, fontWeight: 800, color: "#0f172a" }} />
          <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 2, background: theme.gradient }} />
          <div style={{
            position:"absolute", left:"50%", transform:"translateX(-50%)",
            fontFamily:"'Barlow Condensed',sans-serif",
            fontSize:37, fontWeight:800, color:"#0f172a", letterSpacing:"-.01em",
            pointerEvents:"none",
            }}>
            Production <span style={{ color: theme.accent }}>Import/Export</span>
          </div>
        </div>

        <div style={{ padding: "36px 40px 0", maxWidth: 1000, margin: "0 auto" }}>
          {/* Heading */}
          <div style={{ textAlign: "center", marginBottom: 36 }}>
          </div>

          {/* ── EXPORT SECTION ── */}
          <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, padding: 28, marginBottom: 24, boxShadow: "0 1px 3px rgba(0,0,0,.05)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 24, paddingBottom: 16, borderBottom: "1px solid #f1f5f9" }}>
              <span style={{ fontSize: 22 }}>📤</span>
              <div>
                <div style={{ fontSize: 16, fontWeight: 700, color: "#0f172a" }}>Export Data</div>
                <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 2 }}>Download production records as Excel or CSV</div>
              </div>
            </div>

            {/* Filters row */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr 1fr", gap: 14, marginBottom: 20 }}>
              {/* Zone */}
              <div>
                <label style={{ display: "block", fontSize: 10, fontWeight: 700, letterSpacing: ".1em", textTransform: "uppercase", color: "#64748b", marginBottom: 7 }}>Zone</label>
                <select className="imp-input" value={expZone} onChange={e => { setExpZone(e.target.value); setExpLine(""); }} style={inputSt}>
                  <option value="">All Zones</option>
                  {zones.map(z => <option key={z.id} value={z.id}>{z.zone_name}</option>)}
                </select>
              </div>

              {/* Line */}
              <div>
                <label style={{ display: "block", fontSize: 10, fontWeight: 700, letterSpacing: ".1em", textTransform: "uppercase", color: "#64748b", marginBottom: 7 }}>Line *</label>
                <select className="imp-input" value={expLine} onChange={e => setExpLine(e.target.value)} style={inputSt}>
                  <option value="">Select line…</option>
                  {expLines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
                </select>
              </div>

              {/* From */}
              <div>
                <label style={{ display: "block", fontSize: 10, fontWeight: 700, letterSpacing: ".1em", textTransform: "uppercase", color: "#64748b", marginBottom: 7 }}>From Date *</label>
                <input type="date" className="imp-input" value={expFrom} onChange={e => setExpFrom(e.target.value)} style={inputSt} />
              </div>

              {/* To */}
              <div>
                <label style={{ display: "block", fontSize: 10, fontWeight: 700, letterSpacing: ".1em", textTransform: "uppercase", color: "#64748b", marginBottom: 7 }}>To Date *</label>
                <input type="date" className="imp-input" value={expTo} onChange={e => setExpTo(e.target.value)} style={inputSt} />
              </div>

              {/* Format */}
              <div>
                <label style={{ display: "block", fontSize: 10, fontWeight: 700, letterSpacing: ".1em", textTransform: "uppercase", color: "#64748b", marginBottom: 7 }}>Format</label>
                <div style={{ display: "flex", gap: 6 }}>
                  <button className={`fmt-btn${expFmt === "xlsx" ? " active" : ""}`} onClick={() => setExpFmt("xlsx")}>📊 XLSX</button>
                  <button className={`fmt-btn${expFmt === "csv" ? " active" : ""}`} onClick={() => setExpFmt("csv")}>📄 CSV</button>
                </div>
              </div>
            </div>

            {expError && (
              <div style={{ padding: "10px 14px", background: "rgba(220,38,38,.06)", border: "1px solid rgba(220,38,38,.2)", borderRadius: 8, color: "#dc2626", fontSize: 12, marginBottom: 16 }}>
                ⚠ {expError}
              </div>
            )}

            {/* What gets exported info box */}
            <div style={{ background: "rgba(30,64,175,.04)", border: "1px solid rgba(30,64,175,.15)", borderRadius: 10, padding: "12px 16px", marginBottom: 20, fontSize: 12, color: "#334155" }}>
              <strong style={{ color: theme.accentDark }}>📋 Export includes:</strong> Shift summary (OK/NG counts, OEE components, all loss categories, cycle times) + Hourly slot performance (Plan, Actual, Variance, Efficiency per slot)
            </div>

            <button
              onClick={handleExport}
              disabled={exporting || !expLine}
              style={{
                padding: "12px 32px",
                background: exporting || !expLine ? "#f1f5f9" : `linear-gradient(135deg, ${theme.accentDark}, ${theme.accent})`,
                color: exporting || !expLine ? "#94a3b8" : "#fff",
                border: "none", borderRadius: 10,
                fontSize: 14, fontWeight: 700,
                cursor: exporting || !expLine ? "not-allowed" : "pointer",
                boxShadow: !exporting && expLine ? "0 4px 16px rgba(30,64,175,.3)" : "none",
                transition: "all .15s",
                display: "flex", alignItems: "center", gap: 8,
              }}
            >
              {exporting
                ? <><div style={{ width: 16, height: 16, borderRadius: "50%", border: "2px solid rgba(255,255,255,.3)", borderTopColor: "#fff", animation: "spin .6s linear infinite" }} /> Generating…</>
                : <>⬇ Download {expFmt.toUpperCase()}</>
              }
            </button>
          </div>

          {/* ── IMPORT SECTION ── */}
          <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, padding: 28, boxShadow: "0 1px 3px rgba(0,0,0,.05)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 24, paddingBottom: 16, borderBottom: "1px solid #f1f5f9" }}>
              <span style={{ fontSize: 22 }}>📥</span>
              <div>
                <div style={{ fontSize: 16, fontWeight: 700, color: "#0f172a" }}>Import Data</div>
                <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 2 }}>Upload an Excel or CSV file exported from this system</div>
              </div>
            </div>

            {/* Target line selector */}
            <div style={{ marginBottom: 20, maxWidth: 320 }}>
              <label style={{ display: "block", fontSize: 10, fontWeight: 700, letterSpacing: ".1em", textTransform: "uppercase", color: "#64748b", marginBottom: 7 }}>Import Into Line *</label>
              <select className="imp-input" value={impLine} onChange={e => setImpLine(e.target.value)} style={inputSt}>
                <option value="">Select target line…</option>
                {lines.map(l => <option key={l.id} value={l.id}>{l.line_name} ({l.line_code})</option>)}
              </select>
            </div>

            {/* Drop zone */}
            {!file && (
              <div
                onDragOver={e => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={e => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files[0]; if (f) parseFile(f); }}
                onClick={() => fileRef.current?.click()}
                style={{
                  border: `2px dashed ${dragOver ? "#3b82f6" : "#e2e8f0"}`,
                  borderRadius: 12, padding: "48px 24px",
                  textAlign: "center", cursor: "pointer",
                  background: dragOver ? "rgba(59,130,246,.04)" : "#f8fafc",
                  transition: "all .15s",
                }}
              >
                <div style={{ fontSize: 40, marginBottom: 12, opacity: .5 }}>📂</div>
                <div style={{ fontSize: 15, fontWeight: 600, color: "#334155", marginBottom: 6 }}>
                  Click or drag & drop file here
                </div>
                <div style={{ fontSize: 12, color: "#94a3b8" }}>
                  Supports .xlsx and .csv files exported from this system
                </div>
                <input
                  ref={fileRef} type="file" accept=".xlsx,.csv"
                  style={{ display: "none" }}
                  onChange={e => { const f = e.target.files[0]; if (f) parseFile(f); }}
                />
              </div>
            )}

            {/* Parsing indicator */}
            {parsing && (
              <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "20px 0", color: "#64748b", fontSize: 13 }}>
                <div style={{ width: 20, height: 20, borderRadius: "50%", border: "2px solid #e2e8f0", borderTopColor: theme.accentDark, animation: "spin .6s linear infinite" }} />
                Parsing file…
              </div>
            )}

            {/* File info + validation */}
            {file && !parsing && (
              <div style={{ animation: "slideUp .2s ease" }}>
                {/* File card */}
                <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "14px 18px", background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: 10, marginBottom: 16 }}>
                  <span style={{ fontSize: 28 }}>{file.name.endsWith(".xlsx") ? "📊" : "📄"}</span>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 700, fontSize: 14, color: "#0f172a" }}>{file.name}</div>
                    <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2 }}>
                      {fmtBytes(file.size)} · {parsed?.rows?.length || 0} data rows · {parsed?.headers?.length || 0} columns
                    </div>
                  </div>
                  <button onClick={resetImport} style={{ background: "none", border: "1px solid #e2e8f0", borderRadius: 7, padding: "5px 12px", cursor: "pointer", color: "#94a3b8", fontSize: 12 }}>
                    ✕ Remove
                  </button>
                </div>

                {/* Validation results */}
                {validation && (
                  <div style={{ marginBottom: 20 }}>
                    {/* Status banner */}
                    <div style={{
                      padding: "12px 16px", borderRadius: 10, marginBottom: 12,
                      background: validation.ok ? "rgba(22,163,74,.06)" : "rgba(220,38,38,.06)",
                      border: `1px solid ${validation.ok ? "rgba(22,163,74,.25)" : "rgba(220,38,38,.25)"}`,
                      display: "flex", alignItems: "center", gap: 10,
                      fontSize: 13, fontWeight: 600,
                      color: validation.ok ? "#16a34a" : "#dc2626",
                    }}>
                      {validation.ok ? "✓ File structure is valid — ready to import" : "✗ Validation failed — fix errors before importing"}
                    </div>

                    {/* Errors */}
                    {validation.errors.map((e, i) => (
                      <div key={i} style={{ display: "flex", gap: 8, padding: "8px 12px", background: "rgba(220,38,38,.04)", border: "1px solid rgba(220,38,38,.15)", borderRadius: 8, marginBottom: 6, fontSize: 12, color: "#dc2626" }}>
                        <span>✗</span> {e}
                      </div>
                    ))}

                    {/* Warnings */}
                    {validation.warnings.map((w, i) => (
                      <div key={i} style={{ display: "flex", gap: 8, padding: "8px 12px", background: "rgba(217,119,6,.04)", border: "1px solid rgba(217,119,6,.15)", borderRadius: 8, marginBottom: 6, fontSize: 12, color: "#d97706" }}>
                        <span>⚠</span> {w}
                      </div>
                    ))}
                  </div>
                )}

                {/* Preview table */}
                {parsed && parsed.rows.length > 0 && (
                  <div style={{ marginBottom: 20 }}>
                    {sectionTitle(`Preview — first ${Math.min(5, parsed.rows.length)} of ${parsed.rows.length} rows`)}
                    <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 10, overflow: "hidden" }}>
                      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                        <thead>
                          <tr>
                            {/* Show only key columns in preview */}
                            {["Date", "Shift", "OK Count", "NG Count", "Plan Completed", "Overall OEE (%)"].filter(h => parsed.headers.includes(h)).map(h => (
                              <th key={h} style={{ padding: "9px 12px", textAlign: "left", fontSize: 10, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: "#fff", background: theme.accentDark, whiteSpace: "nowrap" }}>{h}</th>
                            ))}
                            <th style={{ padding: "9px 12px", fontSize: 10, fontWeight: 700, color: "#fff", background: theme.accentDark }}>…{parsed.headers.length - 6} more cols</th>
                          </tr>
                        </thead>
                        <tbody>
                          {parsed.rows.slice(0, 5).map((row, i) => (
                            <tr key={i} style={{ borderBottom: "1px solid #f1f5f9", background: i % 2 === 0 ? "#fff" : "#f8fafc" }}>
                              {["Date", "Shift", "OK Count", "NG Count", "Plan Completed", "Overall OEE (%)"].filter(h => parsed.headers.includes(h)).map(h => (
                                <td key={h} style={{ padding: "9px 12px", fontFamily: "monospace", color: "#334155" }}>{row[h]}</td>
                              ))}
                              <td style={{ padding: "9px 12px", color: "#94a3b8", fontStyle: "italic", fontSize: 11 }}>…</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Import button */}
                {validation?.ok && (
                  <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                    <button
                      onClick={handleImport}
                      disabled={importing || !impLine}
                      style={{
                        padding: "12px 32px",
                        background: importing || !impLine ? "#f1f5f9" : "linear-gradient(135deg,#16a34a,#22c55e)",
                        color: importing || !impLine ? "#94a3b8" : "#fff",
                        border: "none", borderRadius: 10,
                        fontSize: 14, fontWeight: 700,
                        cursor: importing || !impLine ? "not-allowed" : "pointer",
                        boxShadow: !importing && impLine ? "0 4px 16px rgba(22,163,74,.3)" : "none",
                        display: "flex", alignItems: "center", gap: 8,
                        transition: "all .15s",
                      }}
                    >
                      {importing
                        ? <><div style={{ width: 16, height: 16, borderRadius: "50%", border: "2px solid rgba(255,255,255,.3)", borderTopColor: "#fff", animation: "spin .6s linear infinite" }} /> Importing…</>
                        : <>⬆ Import {parsed.rows.length} Records</>
                      }
                    </button>
                    {!impLine && <span style={{ fontSize: 12, color: "#d97706" }}>⚠ Select a target line first</span>}
                  </div>
                )}
              </div>
            )}

            {/* Import result */}
            {impResult && (
              <div style={{
                marginTop: 16, padding: "16px 20px",
                background: impResult.success ? "rgba(22,163,74,.06)" : "rgba(220,38,38,.06)",
                border: `1px solid ${impResult.success ? "rgba(22,163,74,.25)" : "rgba(220,38,38,.25)"}`,
                borderRadius: 10, animation: "slideUp .2s ease",
              }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: impResult.success ? "#16a34a" : "#dc2626", marginBottom: 4 }}>
                  {impResult.success ? `✓ Successfully imported ${impResult.imported} records` : "✗ Import failed"}
                </div>
                <div style={{ fontSize: 12, color: "#64748b" }}>{impResult.message}</div>
              </div>
            )}
          </div>
        </div>
      </div>
      <AIAssistant pageContext={{ page: "Import Export" }} />

    </>
  );
}
