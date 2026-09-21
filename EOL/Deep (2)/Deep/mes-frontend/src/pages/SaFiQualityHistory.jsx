/**
 * Quality → SA / FI History  (SEAT SLIDER zone only)
 *
 * Operator spec (2026-08-19): every Semi-Auto and Final Inspection OK/NG,
 * kept continuously (no retention/purge), filterable, downloadable as Excel.
 *
 * Final rows come from each line's existing cycle log, so the full history is
 * here from day one; Semi-Auto rows appear once that machine's Verdict
 * Register is configured (Admin → Machines → Semi-Auto Data Capture).  A Final
 * row whose part was NG at Semi-Auto shows that verdict plus whether the
 * collector managed to pulse Final's SA-NG bit.
 */
import { useEffect, useState, useRef } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const pad2     = (n) => String(n).padStart(2, "0");
const localYmd = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
const todayStr = () => localYmd(new Date());          // local (IST) today, not UTC
const ymd      = (y, m, d) => `${y}-${pad2(m)}-${pad2(d)}`;

// ── Indian financial year helpers (FY = Apr 1 → Mar 31, named by start year:
//    2026 → "2026-27"). FY + Month are quick pickers that fill the date range. ──
const fyStartOf = (d = new Date()) => (d.getMonth() + 1 >= 4 ? d.getFullYear() : d.getFullYear() - 1);
const fyLabel   = (startYear) => `${startYear}-${String(startYear + 1).slice(2)}`;
const fyRange   = (startYear) => {
  const t = todayStr(); let to = ymd(startYear + 1, 3, 31);
  return { from: ymd(startYear, 4, 1), to: to > t ? t : to };
};
const monthRange = (fyStartYear, m) => {         // m = 1..12
  const year = m >= 4 ? fyStartYear : fyStartYear + 1;   // Apr–Dec first year, Jan–Mar next
  const last = new Date(year, m, 0).getDate();
  const t = todayStr(); const to = ymd(year, m, last);
  return { from: ymd(year, m, 1), to: to > t ? t : to };
};
const MONTHS = ["All months", "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"];

export default function SaFiQualityHistory() {
  // This client takes the token as its SECOND argument and returns the parsed
  // JSON directly (no axios `.data`).  Calling it without the token 401s, and
  // its 401 handler redirects to /login — which is exactly what made this page
  // look like it "logged the user out" when it was first wired up.
  const { token } = useAuth();
  const [meta,    setMeta]    = useState(null);
  const [rows,    setRows]    = useState([]);
  const [total,   setTotal]   = useState(0);
  const [capped,  setCapped]  = useState(false);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState("");
  const [busyXl,  setBusyXl]  = useState(false);
  // Search progress % — starts the moment Search is clicked and climbs toward
  // 90% while the request is in flight, then snaps to 100% when it returns.
  const [progress, setProgress] = useState(0);
  const progRef = useRef(null);
  const startProgress = () => {
    clearInterval(progRef.current);
    setProgress(8);
    progRef.current = setInterval(() => {
      setProgress(p => (p >= 90 ? p : p + Math.max(0.5, (90 - p) * 0.12)));
    }, 120);
  };
  const endProgress = () => {
    clearInterval(progRef.current);
    setProgress(100);
    setTimeout(() => setProgress(0), 500);
  };
  useEffect(() => () => clearInterval(progRef.current), []);  // cleanup on unmount

  const CUR_FY = fyStartOf();
  // Default view = last 4 hours (small window → fast query). FY/Month/dates are
  // pre-set to the latest FY / current month / today for when the user switches
  // to date-range mode, but they are NOT queried until then.
  const [timeWin,  setTimeWin]  = useState("today");    // "today" | "1|4|8|12|24" hrs | "date"
  const [fy,       setFy]       = useState(String(CUR_FY));
  const [month,    setMonth]    = useState(String(new Date().getMonth() + 1)); // current
  const [dateFrom, setDateFrom] = useState(todayStr());
  const [dateTo,   setDateTo]   = useState(todayStr());
  const [lineId,   setLineId]   = useState("");
  const [station,  setStation]  = useState("ALL");
  const [result,   setResult]   = useState("ALL");
  const [partCode, setPartCode] = useState("");
  const [page,     setPage]     = useState(1);
  const PAGE = 100;
  const FY_LIST = Array.from({ length: 5 }, (_, i) => CUR_FY - i);   // latest first

  // FY / Month are quick date-range pickers; using either one switches the view
  // out of "last N hours" into date-range mode and fills From/To accordingly.
  const applyFy = (v) => {
    setFy(v); setTimeWin("date");
    const r = month === "0" ? fyRange(+v) : monthRange(+v, +month);
    setDateFrom(r.from); setDateTo(r.to);
  };
  const applyMonth = (v) => {
    setMonth(v); setTimeWin("date");
    const r = v === "0" ? fyRange(+fy) : monthRange(+fy, +v);
    setDateFrom(r.from); setDateTo(r.to);
  };
  const setDate = (which, v) => {          // manual date edit → date-range mode
    setTimeWin("date");
    which === "from" ? setDateFrom(v) : setDateTo(v);
  };

  useEffect(() => {
    if (!token) return;
    api.get("/api/quality/sa-fi/meta", token)
      .then(r => setMeta(r || { lines: [] }))
      .catch(() => setMeta({ lines: [] }));
  }, [token]);

  const params = (extra = {}) => {
    const p = new URLSearchParams({ station, result, ...extra });
    if (timeWin === "date")       { p.set("date_from", dateFrom); p.set("date_to", dateTo); }
    else if (timeWin === "today") { const t = todayStr(); p.set("date_from", t); p.set("date_to", t); }
    else                          { p.set("hours", timeWin); }   // last N hours → fast
    if (lineId)          p.append("line_id", lineId);
    if (partCode.trim()) p.append("part_code", partCode.trim());
    return p;
  };

  const load = async (toPage = 1) => {
    setLoading(true); setError(""); startProgress();
    try {
      const p = params({ page: String(toPage), page_size: String(PAGE) });
      const r = await api.get(`/api/quality/sa-fi/log?${p}`, token);
      setRows(r?.rows || []);
      setTotal(r?.total || 0);
      setCapped(!!r?.capped);
      setPage(toPage);
    } catch (e) {
      setError(e?.message || "Could not load history");
      setRows([]); setTotal(0);
    } finally { setLoading(false); endProgress(); }
  };

  useEffect(() => { if (token) load(1); /* eslint-disable-next-line */ }, [token]);

  // The shared client always parses JSON, so the .xlsx body has to be fetched
  // directly with the same Authorization header it would have sent.
  const downloadExcel = async () => {
    setBusyXl(true); setError("");
    try {
      const res = await fetch(`/api/quality/sa-fi/export?${params()}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      const tag = timeWin === "date" ? `${dateFrom}_to_${dateTo}` : `last${timeWin}h`;
      a.download = `SA-FI_Quality_${tag}.xlsx`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch {
      setError("Excel download failed");
    } finally { setBusyXl(false); }
  };

  const pages = Math.max(1, Math.ceil(total / PAGE));
  const notConfigured = (meta?.lines || []).filter(l => !l.semi_configured);

  const lbl = { fontSize: 11, fontWeight: 800, color: "#475569",
                display: "block", marginBottom: 4, letterSpacing: ".04em" };
  const inp = { width: "100%", padding: "8px 10px", borderRadius: 8,
                border: "1px solid #cbd5e1", fontSize: 13, background: "#fff",
                boxSizing: "border-box", height: 38 };
  // Uniform, capped-width field so the row is neat — never over-stretched on a
  // wide screen, never two controls crammed together (consistent gap + basis).
  const field   = { flex: "1 1 150px", minWidth: 140, maxWidth: 210 };
  const dateGrp  = (on) => ({ ...field, opacity: on ? 1 : 0.5 });
  const th  = { padding: "9px 10px", fontWeight: 800, color: "#334155",
                whiteSpace: "nowrap", textAlign: "left" };
  const td  = { padding: "8px 10px", whiteSpace: "nowrap" };

  return (
    <div style={{ padding: "18px 22px", maxWidth: 1500, margin: "0 auto" }}>
      <h2 style={{ fontSize: 20, fontWeight: 900, color: "#0f172a", margin: "0 0 3px" }}>
        Semi-Auto ↔ Final Inspection — Quality History
      </h2>
      <div style={{ fontSize: 12, color: "#64748b", marginBottom: 16 }}>
        SEAT SLIDER zone · every cycle's OK/NG kept continuously (no auto-delete) ·
        filter and download as Excel
      </div>

      <div style={{
        background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12,
        padding: 14, marginBottom: 16,
      }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
          {/* TIME WINDOW — the default (Last 4 hours) keeps the query fast.
              Pick "By date range" (or touch a FY/Month/date) for history. */}
          <div style={field}>
            <label style={lbl}>TIME WINDOW</label>
            <select style={inp} value={timeWin} onChange={e => setTimeWin(e.target.value)}>
              <option value="today">Today (full day)</option>
              <option value="1">Last 1 hour</option>
              <option value="4">Last 4 hours</option>
              <option value="8">Last 8 hours</option>
              <option value="12">Last 12 hours</option>
              <option value="24">Last 24 hours</option>
              <option value="date">By date range</option>
            </select>
          </div>
          <div style={dateGrp(timeWin === "date")}>
            <label style={lbl}>FINANCIAL YEAR</label>
            <select style={inp} value={fy} onChange={e => applyFy(e.target.value)}>
              {FY_LIST.map(y => <option key={y} value={y}>{fyLabel(y)}</option>)}
            </select>
          </div>
          <div style={dateGrp(timeWin === "date")}>
            <label style={lbl}>MONTH</label>
            <select style={inp} value={month} onChange={e => applyMonth(e.target.value)}>
              {MONTHS.map((m, i) => <option key={i} value={i === 0 ? "0" : i}>{m}</option>)}
            </select>
          </div>
          <div style={dateGrp(timeWin === "date")}>
            <label style={lbl}>FROM</label>
            <input style={inp} type="date" value={dateFrom} max={todayStr()}
                   onChange={e => setDate("from", e.target.value)} />
          </div>
          <div style={dateGrp(timeWin === "date")}>
            <label style={lbl}>TO</label>
            <input style={inp} type="date" value={dateTo} max={todayStr()}
                   onChange={e => setDate("to", e.target.value)} />
          </div>
          <div style={field}>
            <label style={lbl}>ZONE</label>
            <select style={inp} value="SS" disabled title="This page covers the Seat Slider zone">
              <option value="SS">{meta?.zone || "SEAT SLIDER"}</option>
            </select>
          </div>
          <div style={field}>
            <label style={lbl}>LINE</label>
            <select style={inp} value={lineId} onChange={e => setLineId(e.target.value)}>
              <option value="">All Seat Slider lines</option>
              {(meta?.lines || []).map(l => (
                <option key={l.line_id} value={l.line_id}>{l.line_name}</option>
              ))}
            </select>
          </div>
          <div style={field}>
            <label style={lbl}>STATION</label>
            <select style={inp} value={station} onChange={e => setStation(e.target.value)}>
              <option value="ALL">Both</option>
              <option value="SEMI">Semi-Auto</option>
              <option value="FINAL">Final Inspection</option>
            </select>
          </div>
          <div style={field}>
            <label style={lbl}>RESULT</label>
            <select style={inp} value={result} onChange={e => setResult(e.target.value)}>
              <option value="ALL">OK + NG</option>
              <option value="NG">NG only</option>
              <option value="OK">OK only</option>
            </select>
          </div>
          <div style={field}>
            <label style={lbl}>PART ID</label>
            <input style={{ ...inp, fontFamily: "monospace" }} value={partCode}
                   placeholder="partial match"
                   onChange={e => setPartCode(e.target.value)}
                   onKeyDown={e => { if (e.key === "Enter") load(1); }} />
          </div>
          <div style={{ flex: "1 1 200px", minWidth: 190, display: "flex",
                        alignItems: "flex-end", gap: 8 }}>
            <button onClick={() => load(1)} disabled={loading} style={{
              flex: 1, padding: "9px 14px", borderRadius: 8, border: "none", height: 38,
              background: "#1e3a8a", color: "#fff", fontWeight: 800, fontSize: 13,
              cursor: loading ? "not-allowed" : "pointer", opacity: loading ? .85 : 1,
            }}>{loading ? `${Math.round(progress)}%` : "Search"}</button>
            <button onClick={downloadExcel} disabled={busyXl} title="Download filtered rows as Excel"
                    style={{
              padding: "9px 14px", borderRadius: 8, border: "1px solid #16a34a", height: 38,
              background: busyXl ? "#dcfce7" : "#16a34a",
              color: busyXl ? "#166534" : "#fff", fontWeight: 800, fontSize: 13,
              cursor: busyXl ? "wait" : "pointer", whiteSpace: "nowrap",
            }}>{busyXl ? "…" : "⤓ Excel"}</button>
          </div>
        </div>
      </div>

      {/* Search progress — appears the moment Search is clicked, climbs while
          the query runs, snaps to 100% when the rows arrive. */}
      {progress > 0 && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ display: "flex", justifyContent: "space-between",
                        fontSize: 12, fontWeight: 800, color: "#1e3a8a", marginBottom: 5 }}>
            <span>{progress >= 100 ? "Done" : "Loading…"}</span>
            <span>{Math.round(progress)}%</span>
          </div>
          <div style={{ height: 8, background: "#e2e8f0", borderRadius: 99, overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${progress}%`,
                          background: "linear-gradient(90deg,#1e3a8a,#3b82f6)",
                          borderRadius: 99, transition: "width .18s ease" }} />
          </div>
        </div>
      )}

      {error && (
        <div style={{ padding: 12, color: "#b91c1c", fontWeight: 700, fontSize: 13 }}>{error}</div>
      )}

      {/* Semi-Auto rows only exist for machines whose Verdict Register is set,
          so say which lines are still pending instead of looking broken. */}
      {meta && notConfigured.length > 0 && (
        <div style={{
          background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 10,
          padding: "10px 14px", marginBottom: 14, fontSize: 12, color: "#92400e",
        }}>
          <b>Semi-Auto verdict not configured yet:</b>{" "}
          {notConfigured.map(l => l.line_name).join(", ")}.{" "}
          Set the <b>OK / NG Verdict Bits</b> (and Final's <b>SA-NG Bit</b>) in
          Admin → Production → Machines. Semi-Auto rows still show below with their
          part and load/force values — only the OK/NG column stays blank until then.
        </div>
      )}

      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        marginBottom: 8, fontSize: 13, fontWeight: 700, color: "#334155",
      }}>
        <span>
          {total} row{total === 1 ? "" : "s"}
          {capped && <span style={{ color: "#b45309" }}> · showing the newest — narrow the dates for more</span>}
        </span>
        {pages > 1 && (
          <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button onClick={() => load(page - 1)} disabled={page <= 1 || loading}
                    style={{ padding: "4px 11px", borderRadius: 6, border: "1px solid #cbd5e1",
                             background: "#fff", cursor: page > 1 ? "pointer" : "not-allowed", fontWeight: 700 }}>‹</button>
            <span>Page {page} / {pages}</span>
            <button onClick={() => load(page + 1)} disabled={page >= pages || loading}
                    style={{ padding: "4px 11px", borderRadius: 6, border: "1px solid #cbd5e1",
                             background: "#fff", cursor: page < pages ? "pointer" : "not-allowed", fontWeight: 700 }}>›</button>
          </span>
        )}
      </div>

      <div style={{ overflowX: "auto", background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ background: "#f1f5f9" }}>
              {["Date", "Time", "Shift", "Line", "Station", "Machine", "Part ID",
                "Result",
                // "Semi-Auto" (the SA verdict echoed onto a Final row) and
                // "NG Bit" (whether Final's SA-NG bit was pulsed) only carry
                // data on FINAL rows.  In the Semi-Auto view they are always
                // blank, so hide them — the operator saw three verdict columns
                // where only "Result" was ever filled.
                ...(station === "SEMI" ? [] : ["Semi-Auto", "NG Bit"]),
                "Cycle #", "Raw",
                "Load / Force"].map((h, i) => (
                <th key={i} style={th}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && !loading && (
              <tr><td colSpan={station === "SEMI" ? 11 : 13} style={{ padding: 26, textAlign: "center", color: "#94a3b8" }}>
                No rows for these filters.
              </td></tr>
            )}
            {rows.map((r, i) => {
              const bad = r.result === "NG" || r.sa_result === "NG";
              return (
                <tr key={i} style={{
                  borderBottom: "1px solid #e2e8f0",
                  background: bad ? "#fef2f2" : "#fff",
                }}>
                  <td style={td}>{r.record_date}</td>
                  <td style={td}>{(r.ts || "").slice(11, 19)}</td>
                  <td style={{ ...td, fontWeight: 700 }}>{r.shift_name || "—"}</td>
                  <td style={td}>{r.line_name}</td>
                  <td style={td}>
                    <span style={{
                      fontSize: 10, fontWeight: 900, padding: "2px 7px", borderRadius: 99,
                      background: r.station === "SEMI" ? "#fef3c7" : "#dbeafe",
                      color:      r.station === "SEMI" ? "#92400e" : "#1e40af",
                    }}>{r.station === "SEMI" ? "SEMI-AUTO" : "FINAL"}</span>
                  </td>
                  <td style={{ ...td, color: "#64748b" }}>{r.machine_name}</td>
                  <td style={{ ...td, fontFamily: "monospace", color: "#3b82f6" }}>{r.part_code || "—"}</td>
                  <td style={td}>
                    {/* A Semi-Auto row has no verdict until that machine's
                        Verdict Register is configured — show it as unknown
                        rather than letting a null read as a green OK. */}
                    {r.result === "NG"
                      ? <span style={{ color: "#dc2626", fontWeight: 900 }}>NG</span>
                      : r.result === "OK"
                        ? <span style={{ color: "#16a34a", fontWeight: 800 }}>OK</span>
                        : <span style={{ color: "#cbd5e1" }}>—</span>}
                  </td>
                  {/* SA-trace columns — only meaningful on Final rows, hidden
                      in the Semi-Auto view (see header note). */}
                  {station !== "SEMI" && (
                    <>
                      <td style={td}>
                        {r.sa_result === "NG"
                          ? <span style={{ color: "#dc2626", fontWeight: 900 }}>NG</span>
                          : r.sa_result === "OK"
                            ? <span style={{ color: "#16a34a" }}>OK</span>
                            : <span style={{ color: "#cbd5e1" }}>—</span>}
                      </td>
                      <td style={td}>
                        {r.bit_written
                          ? <span style={{ color: "#b45309", fontWeight: 800 }}>SENT</span>
                          : <span style={{ color: "#cbd5e1" }}>—</span>}
                      </td>
                    </>
                  )}
                  <td style={{ ...td, fontFamily: "monospace", fontWeight: 700 }}>
                    {r.cycle_seq ?? "—"}
                  </td>
                  <td style={{ ...td, color: "#94a3b8" }}>{r.raw_value ?? "—"}</td>
                  <td style={{ ...td, fontFamily: "monospace", fontSize: 11,
                               color: "#475569", whiteSpace: "nowrap" }}>
                    {r.load || "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
