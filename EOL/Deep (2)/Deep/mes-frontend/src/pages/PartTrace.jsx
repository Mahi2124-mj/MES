/**
 * Part Traceability (2026-08-24) — one part code, its whole life.
 *
 * Operator spec: "part search karu to sab details show kare — OK/NG, video,
 * kab kahan kitni baar run hua, load ka data, kitni baar NG".  Backend
 * /api/lines/part-trace returns a dossier: summary + Semi-Auto/Final OK-NG
 * verdicts + load captures + every cycle run (with a video handle) + remarks.
 *
 * Uses the shared api client (token as 2nd arg → returns JSON directly), the
 * same pattern the Quality history page uses, so a missing token can't 401 the
 * whole page.  Video plays inline via the existing per-cycle-clip endpoint.
 */
import { useRef, useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import VideoProgressBar from "../components/VideoProgressBar";

const tok = () => sessionStorage.getItem("mes_token") || "";

const card = {
  background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10,
  padding: "14px 16px",
};
const th = {
  textAlign: "left", padding: "8px 10px", fontSize: 11, fontWeight: 800,
  color: "#475569", background: "#f1f5f9", whiteSpace: "nowrap",
  position: "sticky", top: 0,
};
const td = { padding: "7px 10px", fontSize: 12, borderBottom: "1px solid #eef2f7" };

function Pill({ ok }) {
  return (
    <span style={{
      fontSize: 11, fontWeight: 900, padding: "2px 8px", borderRadius: 99,
      background: ok === "NG" ? "#fee2e2" : ok === "OK" ? "#dcfce7" : "#e2e8f0",
      color:      ok === "NG" ? "#b91c1c" : ok === "OK" ? "#15803d" : "#64748b",
    }}>{ok || "—"}</span>
  );
}

function Stat({ label, value, tone }) {
  const c = tone === "bad" ? "#b91c1c" : tone === "good" ? "#15803d" : "#0f172a";
  return (
    <div style={{ ...card, minWidth: 120, flex: "1 1 120px" }}>
      <div style={{ fontSize: 22, fontWeight: 900, color: c, lineHeight: 1.1 }}>{value}</div>
      <div style={{ fontSize: 11, color: "#64748b", marginTop: 3 }}>{label}</div>
    </div>
  );
}

export default function PartTrace() {
  // The searched part code lives in the URL (?part=…) so a page refresh keeps
  // it (and re-runs the trace) instead of clearing the search.
  const [searchParams, setSearchParams] = useSearchParams();
  const [code, setCode]       = useState(
    () => searchParams.get("part") || sessionStorage.getItem("mes_parttrace_code") || "");
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState("");
  const [videoUrl, setVideoUrl] = useState(null);
  const vRef = useRef(null);

  // On mount (incl. refresh): restore the last searched part (URL first, then
  // sessionStorage) and re-run the trace, so a refresh keeps the part + data.
  useEffect(() => {
    const p = searchParams.get("part") || sessionStorage.getItem("mes_parttrace_code") || "";
    if (p) { setCode(p); search(p); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const search = async (c) => {
    const q = (c ?? code).trim();
    if (!q) { setError("Enter Part code"); return; }
    setCode(q);
    // Persist the code so a refresh restores it: sessionStorage (bulletproof)
    // + the URL (?part=, keeping ?tab=trace) for shareable links / back-nav.
    try { sessionStorage.setItem("mes_parttrace_code", q); } catch { /* private mode */ }
    setSearchParams((prev) => {
      const s = new URLSearchParams(prev); s.set("part", q); return s;
    }, { replace: true });
    setLoading(true); setError(""); setData(null); setVideoUrl(null);
    try {
      const r = await api.get(`/api/lines/part-trace?code=${encodeURIComponent(q)}`, tok());
      setData(r);
      if (!r?.summary?.found) setError("Is part code ka koi record nahi mila.");
    } catch (e) {
      setError(e?.message || "Search fail hua");
    } finally { setLoading(false); }
  };

  const playVideo = (run) => {
    const url = `/api/lines/${run.line_id}/cycle-video`
      + `?date=${run.record_date}&shift=${encodeURIComponent(run.shift_name)}`
      + `&cycle_seq=${run.cycle_seq}&token=${encodeURIComponent(tok())}`;
    setVideoUrl(url);
    setTimeout(() => { try { vRef.current?.load(); vRef.current?.play?.().catch(() => {}); } catch { /* */ } }, 60);
  };

  const s = data?.summary;

  return (
    <div>
      {/* Search bar */}
      <div style={{ ...card, display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap", marginBottom: 16 }}>
        <div style={{ flex: "1 1 320px" }}>
          <label style={{ fontSize: 12, fontWeight: 700, color: "#334155" }}>Part Code</label>
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search()}
            placeholder="Part code e.g. 00227N60824-0128605130035"
            style={{ width: "100%", marginTop: 4, padding: "9px 11px", fontSize: 13,
                     border: "1px solid #cbd5e1", borderRadius: 8, fontFamily: "monospace" }}
          />
        </div>
        <button onClick={() => search()} disabled={loading} style={{
          padding: "10px 22px", fontSize: 13, fontWeight: 800, border: "none",
          borderRadius: 8, background: "#2563eb", color: "#fff", cursor: "pointer",
        }}>{loading ? "…" : "Trace"}</button>
      </div>

      {error && <div style={{ ...card, color: "#b91c1c", marginBottom: 16 }}>{error}</div>}

      {s && s.found && (
        <>
          {/* Summary */}
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 8 }}>
            <Stat label="Total runs" value={s.total_runs} />
            <Stat label="Semi-Auto OK" value={s.semi_ok} tone="good" />
            <Stat label="Semi-Auto NG" value={s.semi_ng} tone={s.semi_ng ? "bad" : undefined} />
            <Stat label="Final NG" value={s.final_ng} tone={s.final_ng ? "bad" : undefined} />
            <Stat label="Reject fired" value={s.rejected_at_final ? "YES" : "no"} tone={s.rejected_at_final ? "bad" : undefined} />
          </div>
          <div style={{ ...card, marginBottom: 18, fontSize: 12, color: "#475569" }}>
            <b style={{ fontFamily: "monospace", color: "#0f172a" }}>{s.part_code}</b>
            {"  ·  "}Stations: <b>{(s.stations || []).join(", ") || "—"}</b>
            {"  ·  "}First: {s.first_seen || "—"}{"  ·  "}Last: {s.last_seen || "—"}
          </div>

          {/* Quality verdicts */}
          <Section title={`Quality — OK / NG (${data.quality.length})`}>
            {data.quality.length === 0 ? <Empty text="Koi Semi-Auto / Final verdict nahi" /> : (
              <Table head={["Time", "Station", "Result", "Reject bit", "Line", "Machine"]}>
                {data.quality.map((q, i) => (
                  <tr key={i} style={{ background: q.result === "NG" ? "#fff5f5" : "#fff" }}>
                    <td style={td}>{q.ts}</td>
                    <td style={td}><b>{q.station}</b></td>
                    <td style={td}><Pill ok={q.result} /></td>
                    <td style={td}>{q.bit_written ? <span style={{ color: "#b45309", fontWeight: 800 }}>SENT</span> : "—"}</td>
                    <td style={td}>{q.line_name}</td>
                    <td style={{ ...td, color: "#64748b" }}>{q.machine_name}</td>
                  </tr>
                ))}
              </Table>
            )}
          </Section>

          {/* Load captures */}
          <Section title={`Load / Data captures (${data.loads.length})`}>
            {data.loads.length === 0 ? <Empty text="Koi load capture nahi" /> : (
              <Table head={["Time", "Machine", "Model", "Values (register → value)"]}>
                {data.loads.map((l, i) => (
                  <tr key={i}>
                    <td style={td}>{l.ts}</td>
                    <td style={td}>{l.machine_name}</td>
                    <td style={{ ...td, color: "#64748b" }}>{l.model_name || "—"}</td>
                    <td style={{ ...td, fontFamily: "monospace", fontSize: 11 }}>
                      {Array.isArray(l.data_values)
                        ? l.data_values.map(d => `${d.label ?? d.register}=${d.scaled ?? d.raw}`).join("  ·  ")
                        : "—"}
                    </td>
                  </tr>
                ))}
              </Table>
            )}
          </Section>

          {/* Runs */}
          <Section title={`Every run — kab / kahan / kitni baar (${data.runs.length})`}>
            {data.runs.length === 0 ? <Empty text="Koi run record nahi" /> : (
              <Table head={["Time", "Station", "Type", "Shift", "CT (s)", "Status", "Video"]}>
                {data.runs.map((r, i) => (
                  <tr key={i} style={{ background: r.is_ng ? "#fff5f5" : "#fff" }}>
                    <td style={td}>{r.ts}</td>
                    <td style={td}><b>{r.station}</b></td>
                    <td style={td}>
                      <span style={{ fontSize: 10, fontWeight: 800, padding: "1px 6px", borderRadius: 99,
                        background: r.kind === "FINAL" ? "#dbeafe" : "#fef3c7",
                        color:      r.kind === "FINAL" ? "#1e40af" : "#92400e" }}>{r.kind}</span>
                    </td>
                    <td style={td}>{r.shift_name || "—"}</td>
                    <td style={{ ...td, fontFamily: "monospace" }}>{r.ct_value != null ? r.ct_value.toFixed(1) : "—"}</td>
                    <td style={td}><Pill ok={r.is_ng ? "NG" : "OK"} /></td>
                    <td style={td}>
                      {r.has_video
                        ? <button onClick={() => playVideo(r)} style={{
                            fontSize: 11, fontWeight: 800, border: "none", borderRadius: 6,
                            background: "#0f172a", color: "#fff", padding: "3px 10px", cursor: "pointer" }}>▶</button>
                        : "—"}
                    </td>
                  </tr>
                ))}
              </Table>
            )}
          </Section>

          {/* Remarks */}
          {data.remarks && data.remarks.length > 0 && (
            <Section title={`Remarks / NG notes (${data.remarks.length})`}>
              <Table head={["Time", "Note", "Source"]}>
                {data.remarks.map((r, i) => (
                  <tr key={i}><td style={td}>{r.ts}</td><td style={td}>{r.text}</td>
                    <td style={{ ...td, color: "#94a3b8", fontSize: 11 }}>{r.src}</td></tr>
                ))}
              </Table>
            </Section>
          )}
        </>
      )}

      {/* Video modal */}
      {videoUrl && (
        <div onClick={() => setVideoUrl(null)} style={{
          position: "fixed", inset: 0, background: "rgba(15,23,42,.78)", zIndex: 9999,
          display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
          <div onClick={(e) => e.stopPropagation()} style={{ background: "#0a0f1a", borderRadius: 12, padding: 12, maxWidth: 860, width: "100%" }}>
            <video ref={vRef} src={videoUrl} style={{ width: "100%", borderRadius: 8, background: "#000" }} autoPlay playsInline />
            <VideoProgressBar videoRef={vRef} />
            <div style={{ textAlign: "right", marginTop: 8 }}>
              <button onClick={() => setVideoUrl(null)} style={{
                fontSize: 12, fontWeight: 800, border: "none", borderRadius: 6,
                background: "#334155", color: "#fff", padding: "6px 16px", cursor: "pointer" }}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ fontSize: 13, fontWeight: 800, color: "#0f172a", marginBottom: 8 }}>{title}</div>
      <div style={{ ...card, padding: 0, overflow: "auto", maxHeight: 340 }}>{children}</div>
    </div>
  );
}
function Table({ head, children }) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead><tr>{head.map((h, i) => <th key={i} style={th}>{h}</th>)}</tr></thead>
      <tbody>{children}</tbody>
    </table>
  );
}
function Empty({ text }) {
  return <div style={{ padding: 18, textAlign: "center", color: "#94a3b8", fontSize: 12 }}>{text}</div>;
}
