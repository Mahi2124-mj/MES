/**
 * Historical → Cycle Time Study  (2026-10-07)
 *
 * Part-to-part cycle time of one machine over any window (hour precision),
 * optionally for one model / part code.  Real recorded values — nothing is
 * capped.  The prediction panel turns the CT into output: parts possible in
 * N hours, and the time / CT needed for a target quantity.
 */
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const pad2 = (n) => String(n).padStart(2, "0");
const toLocal = (d) =>
  `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
const hoursAgo = (h) => { const d = new Date(); d.setMinutes(0, 0, 0); return new Date(d.getTime() - h * 3600e3); };
const fmtS = (v) => (v == null ? "—" : `${Number(v).toFixed(2)} s`);
const fmtN = (v) => (v == null ? "—" : Number(v).toLocaleString("en-IN"));
const fmtHM = (sec) => {
  if (sec == null || !isFinite(sec)) return "—";
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h ? `${h} h ${pad2(m)} min` : `${m} min`;
};

function Kpi({ label, value, sub, color }) {
  return (
    <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12,
                  padding: "12px 14px", minWidth: 130, flex: "1 1 130px" }}>
      <div style={{ fontSize: 10.5, fontWeight: 800, color: "#64748b", letterSpacing: ".06em" }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 900, color: color || "#0f172a", fontFamily: "monospace",
                    marginTop: 3 }}>{value}</div>
      {sub ? <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2 }}>{sub}</div> : null}
    </div>
  );
}

export default function CycleTimeStudy() {
  const { token } = useAuth();
  const [zones, setZones] = useState([]);
  const [lines, setLines] = useState([]);
  const [machines, setMachines] = useState([]);
  const [shifts, setShifts] = useState([]);           // line's production shifts
  const [shiftSel, setShiftSel] = useState("");       // "" = custom From/To
  const [shiftDate, setShiftDate] = useState(() => toLocal(new Date()).slice(0, 10));
  const [zoneId, setZoneId] = useState("");
  const [lineId, setLineId] = useState("");
  const [machine, setMachine] = useState("main");
  const [model, setModel] = useState("");
  const [partCode, setPartCode] = useState("");
  const [serialFrom, setSerialFrom] = useState("");
  const [serialTo, setSerialTo] = useState("");
  const [from, setFrom] = useState(toLocal(hoursAgo(8)));
  const [to, setTo] = useState(toLocal(new Date(Date.now() + 60e3)));
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  // prediction inputs
  const [basis, setBasis] = useState("avg");
  const [customCt, setCustomCt] = useState("");
  const [predHours, setPredHours] = useState("8");
  const [target, setTarget] = useState("");
  const [showRows, setShowRows] = useState(300);

  useEffect(() => {
    api.get("/api/zones/", token).then(r => setZones(Array.isArray(r) ? r : [])).catch(() => setZones([]));
    api.get("/api/lines/", token).then(r => setLines(Array.isArray(r) ? r : [])).catch(() => setLines([]));
  }, [token]);

  useEffect(() => {
    setMachines([]); setMachine("main"); setModel(""); setData(null); setShifts([]);
    if (!lineId) return;
    api.get(`/api/ct-study/machines?line_id=${lineId}`, token)
      .then(r => { setMachines(r?.machines || []); setShifts(r?.shifts || []); })
      .catch(e => setErr(e?.message || "Could not load machines"));
  }, [lineId, token]);

  const zoneLines = lines.filter(l => !zoneId || String(l.zone_id) === String(zoneId));

  // Date + shift -> From/To = that shift's own start..end (a shift that crosses
  // midnight ends on the next day).
  const applyShift = (name, day) => {
    setShiftSel(name);
    if (!name) return;
    const sh = shifts.find(x => x.name === name);
    if (!sh || !sh.start || !sh.end || !day) return;
    setFrom(`${day}T${sh.start}`);
    let endDay = day;
    if (sh.crosses_midnight) {
      const d = new Date(`${day}T00:00`); d.setDate(d.getDate() + 1);
      endDay = toLocal(d).slice(0, 10);
    }
    setTo(`${endDay}T${sh.end}`);
  };

  const run = async () => {
    if (!lineId) { setErr("Select a line"); return; }
    setLoading(true); setErr(""); setShowRows(300);
    try {
      const p = new URLSearchParams({ line_id: lineId, machine, dt_from: from, dt_to: to });
      if (model) p.set("model", model);
      if (partCode.trim()) p.set("part_code", partCode.trim());
      if (serialFrom.trim()) p.set("serial_from", serialFrom.trim());
      if (serialTo.trim()) p.set("serial_to", serialTo.trim());
      const r = await api.get(`/api/ct-study?${p}`, token);
      if ((serialFrom.trim() || serialTo.trim()) && !("serial_range" in (r || {}))) {
        setData(null);
        setErr("Serial range starts working after the next MES server restart.");
        return;
      }
      setData(r);
    } catch (e) {
      setData(null);
      setErr(/not found|404/i.test(e?.message || "")
        ? "Cycle Time Study starts working after the next MES server restart."
        : (e?.message || "Could not load cycle data"));
    } finally { setLoading(false); }
  };

  const preset = (key) => {
    setShiftSel("");
    const now = new Date();
    if (key === "today") { const d = new Date(); d.setHours(0, 0, 0, 0); setFrom(toLocal(d)); setTo(toLocal(new Date(now.getTime() + 60e3))); }
    else if (key === "yesterday") { const a = new Date(); a.setHours(0, 0, 0, 0); const b = new Date(a.getTime() - 864e5); setFrom(toLocal(b)); setTo(toLocal(a)); }
    else if (key === "7d") { setFrom(toLocal(hoursAgo(24 * 7))); setTo(toLocal(new Date(now.getTime() + 60e3))); }
    else { setFrom(toLocal(hoursAgo(Number(key)))); setTo(toLocal(new Date(now.getTime() + 60e3))); }
  };

  const s = data?.stats;
  const okRatio = s && s.cycles ? s.ok / s.cycles : 1;
  const ct = useMemo(() => {
    if (!s) return null;
    if (basis === "median") return s.median;
    if (basis === "avg_ok") return s.avg_ok;
    if (basis === "custom") return Number(customCt) > 0 ? Number(customCt) : null;
    return s.avg;
  }, [s, basis, customCt]);
  const hrs = Number(predHours) > 0 ? Number(predHours) : null;
  const tgt = Number(target) > 0 ? Math.floor(Number(target)) : null;
  const partsPossible = ct && hrs ? Math.floor((hrs * 3600) / ct) : null;
  const okPossible = partsPossible != null ? Math.floor(partsPossible * okRatio) : null;
  const timeForTarget = ct && tgt ? tgt * ct : null;
  const ctNeeded = hrs && tgt ? (hrs * 3600) / tgt : null;

  const histMax = Math.max(1, ...(data?.hist || []).map(h => h.n));
  const ideal = data?.machine?.ideal_ct;

  return (
    <div className="result-card">
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 12, alignItems: "flex-end" }}>
        <div className="ff" style={{ minWidth: 150 }}>
          <label>Section</label>
          <select value={zoneId} onChange={e => { setZoneId(e.target.value); setLineId(""); }}>
            <option value="">All sections</option>
            {zones.map(z => <option key={z.id} value={z.id}>{z.zone_name || z.name}</option>)}
          </select>
        </div>
        <div className="ff" style={{ minWidth: 160 }}>
          <label>Line</label>
          <select value={lineId} onChange={e => setLineId(e.target.value)}>
            <option value="">Select line</option>
            {zoneLines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
          </select>
        </div>
        <div className="ff" style={{ minWidth: 210 }}>
          <label>Machine</label>
          <select value={machine} onChange={e => setMachine(e.target.value)} disabled={!machines.length}>
            {machines.map(m => <option key={m.key} value={m.key}>{m.name}{m.kind === "main" ? " (line output)" : ""}</option>)}
          </select>
        </div>
        <div className="ff" style={{ minWidth: 190 }}>
          <label>Model (optional)</label>
          <select value={model} onChange={e => setModel(e.target.value)}>
            <option value="">All models</option>
            {(data?.models || []).map(m => <option key={m.model} value={m.model}>{m.model} ({m.cycles})</option>)}
          </select>
        </div>
        <div className="ff" style={{ minWidth: 170 }}>
          <label>Part code (optional)</label>
          <input type="text" value={partCode} placeholder="partial match" style={{ fontFamily: "monospace" }}
                 onChange={e => setPartCode(e.target.value)} onKeyDown={e => { if (e.key === "Enter") run(); }} />
        </div>
        <div className="ff" style={{ minWidth: 140 }}>
          <label>Shift date</label>
          <input type="date" value={shiftDate}
                 onChange={e => { setShiftDate(e.target.value); if (shiftSel) applyShift(shiftSel, e.target.value); }} />
        </div>
        <div className="ff" style={{ minWidth: 150 }}>
          <label>Shift</label>
          <select value={shiftSel} onChange={e => applyShift(e.target.value, shiftDate)} disabled={!shifts.length}>
            <option value="">Custom (From / To)</option>
            {shifts.map(sh => <option key={sh.name} value={sh.name}>Shift {sh.name} ({sh.start}–{sh.end})</option>)}
          </select>
        </div>
        <div className="ff" style={{ minWidth: 190 }}>
          <label>Serial from (optional)</label>
          <input type="text" value={serialFrom} placeholder="first part code / serial" style={{ fontFamily: "monospace" }}
                 onChange={e => setSerialFrom(e.target.value)} onKeyDown={e => { if (e.key === "Enter") run(); }} />
        </div>
        <div className="ff" style={{ minWidth: 190 }}>
          <label>Serial to (optional)</label>
          <input type="text" value={serialTo} placeholder="last part code / serial" style={{ fontFamily: "monospace" }}
                 onChange={e => setSerialTo(e.target.value)} onKeyDown={e => { if (e.key === "Enter") run(); }} />
        </div>
        <div className="ff" style={{ minWidth: 190 }}>
          <label>From</label>
          <input type="datetime-local" value={from} onChange={e => { setFrom(e.target.value); setShiftSel(""); }} />
        </div>
        <div className="ff" style={{ minWidth: 190 }}>
          <label>To</label>
          <input type="datetime-local" value={to} onChange={e => { setTo(e.target.value); setShiftSel(""); }} />
        </div>
        <button onClick={run} disabled={loading} style={{ padding: "11px 20px", borderRadius: 8, border: "none",
                background: "#1e3a8a", color: "#fff", fontWeight: 800, cursor: "pointer" }}>
          {loading ? "Loading…" : "Analyse"}
        </button>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 14 }}>
        {[["1", "Last 1 h"], ["4", "Last 4 h"], ["8", "Last 8 h"], ["today", "Today"],
          ["yesterday", "Yesterday"], ["7d", "Last 7 days"]].map(([k, l]) => (
          <button key={k} onClick={() => preset(k)} style={{ padding: "5px 11px", borderRadius: 99,
                  border: "1px solid #cbd5e1", background: "#fff", color: "#334155", fontSize: 12,
                  fontWeight: 700, cursor: "pointer" }}>{l}</button>
        ))}
        <span style={{ fontSize: 11.5, color: "#94a3b8", alignSelf: "center", marginLeft: 6 }}>
          Real recorded cycle times, no cap · max 31 days · a serial range is searched inside From–To
        </span>
      </div>

      {err && <div style={{ background: "#fef2f2", border: "1px solid #fecaca", color: "#b91c1c",
                            borderRadius: 10, padding: "10px 12px", fontSize: 13, fontWeight: 600,
                            marginBottom: 12 }}>{err}</div>}

      {!data && !err && (
        <div style={{ padding: 26, textAlign: "center", color: "#94a3b8", fontSize: 13 }}>
          Select a line, machine and time window, then press Analyse.
        </div>
      )}

      {data && s && (
        <>
          <div style={{ fontSize: 13, color: "#475569", marginBottom: 10 }}>
            <b>{data.line.name}</b> · {data.machine.name} · {data.window.from} → {data.window.to}
            {" "}({data.window.hours} h){shiftSel ? ` · Shift ${shiftSel}` : ""}{model ? ` · ${model}` : ""}
            {ideal ? ` · ideal CT ${ideal} s` : ""}
          </div>
          {data.serial_range && (
            <div style={{ fontSize: 12.5, color: "#1e3a8a", background: "#eef2ff", border: "1px solid #c7d2fe",
                          borderRadius: 8, padding: "7px 10px", marginBottom: 10, fontWeight: 600 }}>
              Serial range: <span style={{ fontFamily: "monospace" }}>{data.serial_range.from_part || "—"}</span>
              {" "}({data.serial_range.from_ts}) → <span style={{ fontFamily: "monospace" }}>{data.serial_range.to_part || "—"}</span>
              {" "}({data.serial_range.to_ts}) · {fmtN(data.serial_range.cycles)} parts
            </div>
          )}
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 16 }}>
            <Kpi label="CYCLES" value={fmtN(s.cycles)} sub={`OK ${fmtN(s.ok)} · NG ${fmtN(s.ng)}`} />
            <Kpi label="AVERAGE CT" value={fmtS(s.avg)} color="#1e3a8a"
                 sub={ideal ? (s.avg > ideal ? `+${(s.avg - ideal).toFixed(2)} s vs ideal` : `${(s.avg - ideal).toFixed(2)} s vs ideal`) : `OK only ${fmtS(s.avg_ok)}`} />
            <Kpi label="MEDIAN CT" value={fmtS(s.median)} sub="half the parts faster" />
            <Kpi label="MIN / MAX" value={`${s.min ?? "—"} / ${s.max ?? "—"}`} sub="seconds" />
            <Kpi label="SPREAD" value={fmtS(s.std)} sub={`P10 ${s.p10 ?? "—"} · P90 ${s.p90 ?? "—"}`} />
            <Kpi label="ACTUAL RATE" value={`${s.rate_per_hour ?? "—"}/h`}
                 sub={data.serial_range ? "parts per hour, first to last part" : "parts per hour in window"} color="#15803d" />
          </div>
          {s.excluded_zero > 0 && (
            <div style={{ fontSize: 11.5, color: "#94a3b8", marginTop: -8, marginBottom: 12 }}>
              {s.excluded_zero} record(s) with 0 s (no measured time) not counted.
            </div>
          )}

          {/* Prediction */}
          <div style={{ border: "1px solid #c7d2fe", background: "#eef2ff", borderRadius: 12,
                        padding: 14, marginBottom: 16 }}>
            <div style={{ fontWeight: 900, color: "#1e3a8a", marginBottom: 10 }}>Prediction</div>
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 12 }}>
              <div className="ff" style={{ minWidth: 190 }}>
                <label>Cycle time to use</label>
                <select value={basis} onChange={e => setBasis(e.target.value)}>
                  <option value="avg">Average ({fmtS(s.avg)})</option>
                  <option value="avg_ok">Average of OK ({fmtS(s.avg_ok)})</option>
                  <option value="median">Median ({fmtS(s.median)})</option>
                  <option value="custom">Custom…</option>
                </select>
              </div>
              {basis === "custom" && (
                <div className="ff" style={{ minWidth: 120 }}>
                  <label>Custom CT (s)</label>
                  <input type="number" min="0.1" step="0.1" value={customCt} onChange={e => setCustomCt(e.target.value)} />
                </div>
              )}
              <div className="ff" style={{ minWidth: 120 }}>
                <label>Hours available</label>
                <input type="number" min="0.1" step="0.5" value={predHours} onChange={e => setPredHours(e.target.value)} />
              </div>
              <div className="ff" style={{ minWidth: 130 }}>
                <label>Target parts (optional)</label>
                <input type="number" min="1" step="1" value={target} onChange={e => setTarget(e.target.value)} />
              </div>
            </div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
              <Kpi label={`PARTS IN ${hrs ?? "—"} H`} value={fmtN(partsPossible)} color="#1e3a8a"
                   sub={ct ? `at ${Number(ct).toFixed(2)} s per part` : "enter a cycle time"} />
              <Kpi label="OK PARTS EXPECTED" value={fmtN(okPossible)} color="#15803d"
                   sub={`at current OK rate ${(okRatio * 100).toFixed(1)}%`} />
              {tgt && <Kpi label={`TIME FOR ${fmtN(tgt)} PARTS`} value={fmtHM(timeForTarget)}
                           sub={ct ? `at ${Number(ct).toFixed(2)} s per part` : ""} />}
              {tgt && hrs && <Kpi label={`CT NEEDED FOR ${fmtN(tgt)} IN ${hrs} H`} value={fmtS(ctNeeded)}
                                  color={ct && ctNeeded >= ct ? "#15803d" : "#b91c1c"}
                                  sub={ct ? (ctNeeded >= ct ? "achievable at the chosen CT"
                                    : `short by ${fmtN(tgt - (partsPossible || 0))} parts at the chosen CT`) : ""} />}
            </div>
          </div>

          {/* Distribution */}
          {data.hist?.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontWeight: 800, color: "#334155", marginBottom: 8, fontSize: 13 }}>CT distribution (parts per CT band)</div>
              <div style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 130,
                            borderBottom: "1px solid #e2e8f0", paddingBottom: 2 }}>
                {data.hist.map((h, i) => (
                  <div key={i} title={`${h.lo}${h.hi != null ? `–${h.hi}` : "+"} s: ${h.n} parts`}
                       style={{ flex: 1, minWidth: 8, display: "flex", flexDirection: "column", alignItems: "center",
                                justifyContent: "flex-end", height: "100%" }}>
                    <div style={{ fontSize: 9.5, color: "#64748b" }}>{h.n || ""}</div>
                    <div style={{ width: "100%", height: `${Math.max(h.n ? 3 : 0, (h.n / histMax) * 100)}%`,
                                  background: h.hi == null ? "#f59e0b" : (ideal && h.lo >= ideal ? "#93c5fd" : "#1e40af"),
                                  borderRadius: "3px 3px 0 0" }} />
                  </div>
                ))}
              </div>
              <div style={{ display: "flex", gap: 3 }}>
                {data.hist.map((h, i) => (
                  <div key={i} style={{ flex: 1, minWidth: 8, fontSize: 9, color: "#94a3b8", textAlign: "center" }}>
                    {h.hi == null ? `${h.lo}+` : h.lo}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Hour-wise */}
          <div style={{ fontWeight: 800, color: "#334155", margin: "6px 0 8px", fontSize: 13 }}>Hour-wise</div>
          <div style={{ overflowX: "auto", maxHeight: 340, overflowY: "auto", marginBottom: 16 }}>
            <table className="slot-tbl" style={{ minWidth: 640 }}>
              <thead><tr>{["Hour", "Cycles", "OK", "NG", "Avg CT (s)", "Min (s)", "Max (s)"].map(h => <th key={h}>{h}</th>)}</tr></thead>
              <tbody>
                {data.hourly.map(h => (
                  <tr key={h.hour}>
                    <td style={{ whiteSpace: "nowrap" }}>{h.hour}</td>
                    <td style={{ fontFamily: "monospace" }}>{h.cycles}</td>
                    <td style={{ fontFamily: "monospace", color: "#15803d" }}>{h.ok}</td>
                    <td style={{ fontFamily: "monospace", color: h.ng ? "#b91c1c" : undefined }}>{h.ng}</td>
                    <td style={{ fontFamily: "monospace", fontWeight: 800 }}>{h.avg}</td>
                    <td style={{ fontFamily: "monospace" }}>{h.min}</td>
                    <td style={{ fontFamily: "monospace" }}>{h.max}</td>
                  </tr>
                ))}
                {!data.hourly.length && <tr><td colSpan={7} style={{ padding: 18, textAlign: "center", color: "#94a3b8" }}>No cycles in this window.</td></tr>}
              </tbody>
            </table>
          </div>

          {/* Part to part */}
          <div style={{ fontWeight: 800, color: "#334155", margin: "6px 0 8px", fontSize: 13 }}>
            Part to part {data.cycles_truncated ? `(latest ${fmtN(data.cycles.length)} of ${fmtN(s.cycles)})` : `(${fmtN(data.cycles.length)})`}
          </div>
          <div style={{ overflowX: "auto", maxHeight: 420, overflowY: "auto" }}>
            <table className="slot-tbl" style={{ minWidth: 640 }}>
              <thead><tr>{["#", "Time", "Part Code", "Model", "CT (s)", "Result"].map(h => <th key={h}>{h}</th>)}</tr></thead>
              <tbody>
                {[...data.cycles].reverse().slice(0, showRows).map((c, i) => (
                  <tr key={`${c.ts}-${i}`}>
                    <td style={{ color: "#94a3b8", fontFamily: "monospace" }}>{data.cycles.length - i}</td>
                    <td style={{ whiteSpace: "nowrap" }}>{c.ts}</td>
                    <td style={{ fontFamily: "monospace" }}>{c.part_code || "—"}</td>
                    <td style={{ maxWidth: 260 }}>{c.model || "—"}</td>
                    <td style={{ fontFamily: "monospace", fontWeight: 800,
                                 color: ideal && c.ct > ideal * 1.5 ? "#b45309" : undefined }}>{c.ct}</td>
                    <td>{c.ok ? <span style={{ color: "#15803d", fontWeight: 800 }}>OK</span>
                              : <span style={{ color: "#b91c1c", fontWeight: 800 }}>NG</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.cycles.length > showRows && (
            <button onClick={() => setShowRows(n => n + 500)} style={{ marginTop: 8, padding: "6px 14px",
                    borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer",
                    fontWeight: 700, fontSize: 12 }}>Show 500 more</button>
          )}
        </>
      )}
    </div>
  );
}
