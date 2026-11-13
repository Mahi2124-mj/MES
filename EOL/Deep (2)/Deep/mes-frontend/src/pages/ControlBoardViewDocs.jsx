// pages/ControlBoardViewDocs.jsx — the board documents band of the Control
// Board View (2026-11-07, operator's "Rev. Control Board View" sheet: "Control
// Board Document Merging with Dx Management Dashboard").  Pilot on YNC-SS.
//
// Five tiles under the line's live dashboard:
//   Track Record (left, tall) · Yamazumi [Last 10 Cycle] · 4M Change → Operator
//   Status · Defect Record · Annual Safety Chart.
// The sheet tiles show the real Control Board formats (public/control-board-docs)
// as live previews for this line and month; a tap opens the full sheet (same
// viewer as the Control Board page: Save PDF, Full screen, line / month pick).
// Yamazumi = man-wise (7-Nov): one bar per operator of the crew, his pace over
// the last 10 cycles of his machines, against the takt (see useManYamazumi).
// Read-only: nothing here writes to MES.
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { SheetDoc, DOCS, ALL } from "./ControlBoard";

const DOC_BASE = "/control-board-docs/";
const LABEL_CLR = "#c55a11";          // the reference sheet's label text
const LABEL_BORDER = "#4472c4";

// ── small GET cache: several tiles + the viewer ask for the same data ──
const cache = new Map();              // url -> { t, p }
function getCached(url, token, ttl) {
  const hit = cache.get(url);
  if (hit && Date.now() - hit.t < ttl) return hit.p;
  const p = api.get(url, token);
  cache.set(url, { t: Date.now(), p });
  p.catch(() => cache.delete(url));
  return p;
}
function useApi(url, token, { ttl = 60000, refreshMs = 0 } = {}) {
  const [st, setSt] = useState({ data: null, loading: !!url, error: null });
  useEffect(() => {
    if (!url) { setSt({ data: null, loading: false, error: null }); return undefined; }
    let alive = true;
    const load = (force) => {
      if (force) cache.delete(url);
      setSt(s => ({ ...s, loading: true }));
      getCached(url, token, ttl)
        .then(d => { if (alive) setSt({ data: d, loading: false, error: null }); })
        .catch(e => {
          if (!alive) return;
          const msg = /404|Not Found/i.test(String(e && e.message))
            ? "MES data becomes available after the next MES-API restart." : "Could not load MES data.";
          setSt({ data: null, loading: false, error: msg });
        });
    };
    load(false);
    const t = refreshMs ? setInterval(() => load(true), refreshMs) : null;
    return () => { alive = false; if (t) clearInterval(t); };
  }, [url, token, ttl, refreshMs]);
  return st;
}

// ── live sheet preview: the format drawn at its own width, zoomed to the tile ──
function SheetThumb({ file, width, data }) {
  const boxRef = useRef(null);
  const frameRef = useRef(null);
  const [w, setW] = useState(0);
  const [h, setH] = useState(0);
  const [inner, setInner] = useState(null);     // null = asked, true = sheet zooms itself, false = transform
  const fallback = useRef(null);
  useEffect(() => {
    const el = boxRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const upd = () => { setW(el.clientWidth); setH(el.clientHeight); };
    upd();
    const ro = new ResizeObserver(upd);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const scale = w > 0 ? Math.min(1, w / width) : 0.3;
  useEffect(() => {
    const onMsg = (e) => {
      const f = frameRef.current;
      if (!f || e.source !== f.contentWindow || !e.data) return;
      if (e.data.type === "cb-zoom-ok") { clearTimeout(fallback.current); setInner(true); }
    };
    window.addEventListener("message", onMsg);
    return () => { window.removeEventListener("message", onMsg); clearTimeout(fallback.current); };
  }, []);
  const post = (msg) => {
    try { frameRef.current && frameRef.current.contentWindow.postMessage(msg, "*"); } catch { /* frame gone */ }
  };
  const dataKey = data ? JSON.stringify(data) : "";
  useEffect(() => { if (data) post({ type: "cb-data", ...data }); }, [dataKey]);       // eslint-disable-line
  useEffect(() => { if (inner !== false) post({ type: "cb-zoom", zoom: scale }); }, [scale, inner]);  // eslint-disable-line
  const onLoad = () => {
    if (data) post({ type: "cb-data", ...data });
    post({ type: "cb-zoom", zoom: scale });
    clearTimeout(fallback.current);
    fallback.current = setTimeout(() => setInner(x => (x === null ? false : x)), 1500);
  };
  const useTransform = inner === false && scale < 1;
  return (
    <div ref={boxRef} style={{ position: "absolute", inset: 0, overflow: "hidden", background: "#fff" }}>
      <iframe ref={frameRef} title={file} src={DOC_BASE + file} onLoad={onLoad} tabIndex={-1}
              sandbox="allow-scripts" scrolling="no" aria-hidden="true"
              style={useTransform
                ? { position: "absolute", left: 0, top: 0, border: "none", width, height: h / scale,
                    transform: `scale(${scale})`, transformOrigin: "0 0", pointerEvents: "none" }
                : { position: "absolute", inset: 0, border: "none", width: "100%", height: "100%",
                    pointerEvents: "none" }} />
    </div>
  );
}

// the reference's label box: white, blue border, brown-orange text
function TileLabel({ children, style }) {
  return (
    <span style={{ position: "absolute", left: "50%", top: "50%", transform: "translate(-50%,-50%)",
                   background: "rgba(255,255,255,.94)", border: `2px solid ${LABEL_BORDER}`, color: LABEL_CLR,
                   fontWeight: 800, fontSize: "clamp(12px, 1.6vmin, 22px)", padding: "0.5em 1.4em",
                   whiteSpace: "nowrap", boxShadow: "0 2px 8px rgba(0,0,0,.18)", pointerEvents: "none",
                   fontFamily: "'Segoe UI',system-ui,sans-serif", ...style }}>
      {children}
    </span>
  );
}

function Tile({ area, onOpen, title, children }) {
  return (
    <div role="button" tabIndex={0} title={title} onClick={onOpen}
         onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); } }}
         style={{ gridArea: area, position: "relative", minHeight: 0, minWidth: 0, overflow: "hidden",
                  borderRadius: 6, border: "1px solid #94a3b8", background: "#fff", cursor: "pointer" }}>
      {children}
    </div>
  );
}

// ── Yamazumi, man-wise (operator, 7-Nov: "man wise bnti h not machine wise") ──
// One bar per operator of the line's crew (Shift Allocation: this shift, else
// the latest saved crew of the line).  An operator's time = his pace over the
// last 10 cycles of the machines he runs:
//   * a machine's time = average of its last 10 cycle times (cycle-to-cycle,
//     from the PLC: sub-machine cycle log, or the line's own log for the main
//     PLC — Final Inspection);
//   * parallel twins (same name, "# 01" / "# 02", e.g. Rail Assy) share the
//     parts, so together they give one part every 1 / (1/t1 + 1/t2) seconds;
//   * machines in series run at the operator's pace, so he is as slow as his
//     slowest one (max).  Summing cycle-to-cycle times would count his loop twice.
// A process with no machine signal (E-Ring, Manual Movement) has no time — the
// bar shows "No signal".  Green = at or under the target (takt), red = above.
const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const twinKey = (name) => String(name || "").toLowerCase().replace(/\s*(#|no\.?)?\s*0*\d+\s*$/, "").trim();
const firstName = (n) => String(n || "").trim().split(/\s+/)[0] || "—";

async function crewOf(lineId, recordDate, shiftName, token) {
  // this shift first, then the other shift, going back up to 7 days
  const cur = /^[AB]$/.test(shiftName || "") ? shiftName
    : (/^GAP_([AB])/.exec(shiftName || "") || [])[1] || "A";
  // shifts going back in time from the current one: B → A → yesterday B → A …
  const base = recordDate ? new Date(`${recordDate}T12:00:00`) : new Date();
  const seq = [];
  for (let back = 0; back <= 7; back++) {
    const d = new Date(base); d.setDate(d.getDate() - back);
    const day = ymd(d);
    if (back > 0 || cur === "B") seq.push([day, "B"]);  // today's B only once it is the current shift
    seq.push([day, "A"]);
  }
  for (let i = 0; i < seq.length; i += 4) {               // four shifts per round trip
    const chunk = seq.slice(i, i + 4);
    const got = await Promise.all(chunk.map(([day, sh]) =>
      api.get(`/api/manpower/allocations?line_id=${lineId}&date=${day}&shift=${sh}`, token).catch(() => [])));
    for (let k = 0; k < chunk.length; k++) {
      const rows = Array.isArray(got[k]) ? got[k] : [];
      if (rows.length) return { rows, date: chunk[k][0], shift: chunk[k][1] };
    }
  }
  return { rows: [], date: null, shift: null };
}

async function lastCyclesSub(subId, base, token) {
  let cyc = [];
  for (let back = 0; back < 7 && cyc.length < 10; back++) {
    const d = new Date(base); d.setDate(d.getDate() - back);
    try {
      const r = await api.get(`/api/submachines/${subId}/ct-history?date=${ymd(d)}&limit=${10 - cyc.length}`, token);
      cyc = [...(Array.isArray(r) ? r : []).filter(c => Number(c.ct_seconds) > 0).map(c => Number(c.ct_seconds)), ...cyc];
    } catch { break; }
  }
  return cyc.slice(-10);
}
async function lastCyclesLine(lineId, base, ideal, token) {
  const cap = Math.max((ideal || 15) * 5, 60);       // same outlier cap as the sub-machine log
  let cyc = [];
  for (let back = 0; back < 7 && cyc.length < 10; back++) {
    const d = new Date(base); d.setDate(d.getDate() - back);
    try {
      const r = await api.get(`/api/lines/${lineId}/ct-history?date=${ymd(d)}`, token);
      const v = (Array.isArray(r) ? r : []).map(c => Number(c.ct_value)).filter(x => x > 0).map(x => Math.min(x, cap));
      cyc = [...v.slice(-(10 - cyc.length)), ...cyc];
    } catch { break; }
  }
  return cyc.slice(-10);
}

function useManYamazumi(lineId, recordDate, shiftName, idealCt, token) {
  const [st, setSt] = useState({ ops: null, crew: null, error: null });
  useEffect(() => {
    if (!lineId) return undefined;
    let alive = true;
    const load = async () => {
      try {
        const base = recordDate ? new Date(`${recordDate}T12:00:00`) : new Date();
        const [crew, procs, subs] = await Promise.all([
          crewOf(lineId, recordDate, shiftName, token),
          getCached(`/api/manpower/processes?line_id=${lineId}`, token, 600000),
          getCached(`/api/lines/${lineId}/submachines`, token, 600000),
        ]);
        const procMap = new Map((Array.isArray(procs) ? procs : []).map(p => [p.id, p]));
        const subMap = new Map((Array.isArray(subs) ? subs : []).map(s => [s.id, s]));
        // operators in process order, each with his machines
        const ops = new Map();
        for (const a of crew.rows) {
          const p = procMap.get(a.process_id) || {};
          const o = ops.get(a.operator_id) || { id: a.operator_id, name: a.full_name || `Operator ${a.operator_id}`,
                                               code: a.employee_id || a.badge_code || "", procs: [], plcs: [] };
          o.procs.push(a.process_name || p.process_name || "—");
          if (p.plc_config_id) o.plcs.push({ plc: p.plc_config_id, proc: a.process_name || p.process_name });
          ops.set(a.operator_id, o);
        }
        // last 10 cycles of every machine the crew runs (once each)
        const need = [...new Set([...ops.values()].flatMap(o => o.plcs.map(x => x.plc)))];
        const times = new Map();
        await Promise.all(need.map(async (plc) => {
          const cyc = subMap.has(plc) ? await lastCyclesSub(plc, base, token)
                                      : await lastCyclesLine(lineId, base, idealCt, token);   // the line's main PLC
          times.set(plc, cyc.length ? { avg: cyc.reduce((a, b) => a + b, 0) / cyc.length, n: cyc.length } : null);
        }));
        const out = [...ops.values()].map(o => {
          const groups = new Map();                    // twin machines together
          for (const x of o.plcs) {
            const t = times.get(x.plc);
            // twins only when a machine is built slower than the line (ideal ≥ 1.5 × line ideal,
            // e.g. Rail Assy 30 s on a 15 s line) — otherwise "Station 1 / 2" are in series
            const mIdeal = Number((subMap.get(x.plc) || {}).ideal_ct) || 0;
            const slow = idealCt > 0 && mIdeal >= idealCt * 1.5;
            const key = slow ? `twin:${twinKey(x.proc)}` : `plc:${x.plc}`;
            const g = groups.get(key) || { name: x.proc, items: [] };
            g.items.push({ proc: x.proc, t });
            groups.set(key, g);
          }
          const parts = [...groups.values()].map(g => {
            const ok = g.items.filter(i => i.t && i.t.avg > 0);
            const t = ok.length ? 1 / ok.reduce((s, i) => s + 1 / i.t.avg, 0) : null;
            return { name: g.items.length > 1 ? `${g.name.replace(/\s*(#|no\.?)?\s*0*\d+\s*$/i, "")} (×${g.items.length})` : g.name,
                     t, items: g.items };
          });
          const vals = parts.map(p => p.t).filter(v => v != null);
          return { ...o, parts, time: vals.length ? Math.max(...vals) : null,
                   manual: o.plcs.length === 0 };
        });
        if (alive) setSt({ ops: out, crew: { date: crew.date, shift: crew.shift }, error: null });
      } catch {
        if (alive) setSt(s => ({ ...s, error: "Could not load the crew / cycle times." }));
      }
    };
    load();
    const t = setInterval(load, 120000);
    return () => { alive = false; clearInterval(t); };
  }, [lineId, recordDate, shiftName, idealCt, token]);
  return st;
}

const Y_GREEN = "#2e9e44", Y_RED = "#d03b3b", Y_TARGET = "#1f3a93", Y_GREY = "#b8bec8";
function YamazumiChart({ ops, target }) {
  const boxRef = useRef(null);
  const [sz, setSz] = useState({ w: 400, h: 200 });
  const [tip, setTip] = useState(null);
  useEffect(() => {
    const el = boxRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const upd = () => setSz({ w: Math.max(160, el.clientWidth), h: Math.max(90, el.clientHeight) });
    upd();
    const ro = new ResizeObserver(upd);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const { w, h } = sz;
  const fs = Math.max(10, Math.min(15, h / 14));
  const padL = 30, padR = 6, padT = fs * 1.2, padB = fs * 2.3;
  const top = Math.max(1, target || 0, ...ops.map(o => o.time || 0)) * 1.15;
  const cw = (w - padL - padR) / Math.max(1, ops.length);
  const bw = Math.min(cw * 0.6, 80);
  const y = (v) => padT + (h - padT - padB) * (1 - v / top);
  const step = top > 40 ? 10 : 5;
  const ticks = [];
  for (let v = 0; v <= top; v += step) ticks.push(v);
  return (
    <div ref={boxRef} style={{ position: "absolute", inset: 0 }} onMouseLeave={() => setTip(null)}>
      <svg width={w} height={h} style={{ display: "block" }}>
        {ticks.map(v => (
          <g key={v}>
            <line x1={padL} x2={w - padR} y1={y(v)} y2={y(v)} stroke={v ? "#d9d9d9" : "#9ca3af"} strokeWidth="1" />
            <text x={padL - 4} y={y(v) + fs * 0.35} textAnchor="end" fontSize={fs * 0.75} fill="#6b7280">{v}</text>
          </g>
        ))}
        {ops.map((o, i) => {
          const cx = padL + cw * i + cw / 2;
          const has = o.time != null;
          const fill = !has ? Y_GREY : (target && o.time > target ? Y_RED : Y_GREEN);
          return (
            <g key={o.id} onMouseEnter={() => setTip({ o, x: cx })} onClick={() => setTip({ o, x: cx })}>
              <rect x={cx - cw / 2} y={padT} width={cw} height={h - padT - padB} fill="transparent" />
              {has ? (
                <>
                  <rect x={cx - bw / 2} y={y(o.time)} width={bw} height={Math.max(0, y(0) - y(o.time))} fill={fill} />
                  <text x={cx} y={Math.min(y(o.time) + fs * 1.2, y(0) - 3)} textAnchor="middle" fontSize={fs}
                        fontWeight="700" fill="#fff">{o.time.toFixed(1)}</text>
                </>
              ) : (
                <>
                  <rect x={cx - bw / 2} y={y(0) - fs * 1.6} width={bw} height={fs * 1.6} fill="none"
                        stroke={Y_GREY} strokeDasharray="4 3" />
                  <text x={cx} y={y(0) - fs * 0.5} textAnchor="middle" fontSize={fs * 0.72} fill="#6b7280">
                    {o.manual ? "Manual" : "No data"}</text>
                </>
              )}
              <text x={cx} y={h - padB + fs * 1.1} textAnchor="middle" fontSize={fs * 0.85} fontWeight="700" fill="#374151">
                {firstName(o.name).slice(0, Math.max(4, Math.floor(cw / (fs * 0.55))))}</text>
              <text x={cx} y={h - padB + fs * 2.1} textAnchor="middle" fontSize={fs * 0.68} fill="#6b7280">
                {`OP ${i + 1}`}</text>
            </g>
          );
        })}
        {target > 0 && (
          <g>
            <line x1={padL} x2={w - padR} y1={y(target)} y2={y(target)} stroke={Y_TARGET} strokeWidth="2.5" />
          </g>
        )}
      </svg>
      {tip && (
        <div style={{ position: "absolute", left: Math.min(Math.max(4, tip.x - 120), w - 244), top: 4, width: 240,
                      background: "#0f172a", color: "#e2e8f0", borderRadius: 6, padding: "6px 9px", fontSize: 11,
                      lineHeight: 1.45, pointerEvents: "none", boxShadow: "0 4px 14px rgba(0,0,0,.3)", zIndex: 2 }}>
          <div style={{ fontWeight: 800, color: "#fff" }}>{tip.o.name}{tip.o.code ? ` · ${tip.o.code}` : ""}</div>
          {tip.o.time != null
            ? <div>{tip.o.time.toFixed(1)} s per part{target ? ` · target ${target.toFixed(1)} s` : ""}</div>
            : <div>{tip.o.manual ? "Manual process — no machine signal" : "No cycles in the last 7 days"}</div>}
          {tip.o.parts.map(p => (
            <div key={p.name} style={{ color: "#cbd5e1" }}>
              {p.name}: {p.t != null ? `${p.t.toFixed(1)} s` : "no data"}
              {p.items.length > 1 && p.items.every(i => i.t) ? ` (${p.items.map(i => i.t.avg.toFixed(1)).join(" + ")} s in parallel)` : ""}
            </div>
          ))}
          {tip.o.procs.filter((p, k, a) => a.indexOf(p) === k).length > tip.o.parts.reduce((s, p) => s + p.items.length, 0) && (
            <div style={{ color: "#94a3b8" }}>Also: {tip.o.procs.filter(p => !tip.o.parts.some(g => g.items.some(i => i.proc === p))).join(", ")}</div>
          )}
        </div>
      )}
    </div>
  );
}

function YamazumiTile({ lineId, recordDate, shiftName, idealCt, target, token, onOpen }) {
  const { ops, crew, error } = useManYamazumi(lineId, recordDate, shiftName, idealCt, token);
  const leg = (sw, label) => (
    <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "clamp(10px, 1.15vmin, 13px)",
                  color: "#374151", lineHeight: 1.2 }}>{sw}<span>{label}</span></div>
  );
  const box = (c, dashed) => <i style={{ flexShrink: 0, display: "inline-block", width: 12, height: 12, background: dashed ? "transparent" : c,
                                          border: dashed ? `1.5px dashed ${c}` : "none" }} />;
  return (
    <div style={{ gridArea: "yam", position: "relative", minHeight: 0, minWidth: 0, overflow: "hidden",
                  borderRadius: 6, border: "1px solid #94a3b8",
                  background: "linear-gradient(#f5f5f5,#e9e9e9)", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px 0" }}>
        <span style={{ flex: 1, textAlign: "center", fontSize: "clamp(13px, 1.9vmin, 26px)", fontWeight: 700,
                       color: "#404040", fontFamily: "'Segoe UI',system-ui,sans-serif" }}>
          Yamazumi [ Last 10 Cycle ]
        </span>
        <button onClick={onOpen} title="Open the line layout / Yamazumi sheet"
                style={{ border: "1px solid #cbd5e1", background: "#fff", color: "#334155", borderRadius: 6,
                         fontSize: 11, fontWeight: 700, padding: "3px 8px", cursor: "pointer" }}>Layout ›</button>
      </div>
      <div style={{ textAlign: "center", fontSize: 10, color: "#6b7280" }}>
        Man-wise{crew && crew.date ? ` · crew of ${crew.date.slice(8, 10)}-${crew.date.slice(5, 7)} ${crew.shift} shift (Shift Allocation)` : ""}
      </div>
      <div style={{ flex: 1, position: "relative", minHeight: 0, minWidth: 0 }}>
        {ops && ops.length > 0 ? <YamazumiChart ops={ops} target={target} />
          : (
            <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center",
                          fontSize: 12, color: "#64748b", textAlign: "center", padding: 10 }}>
              {error || (ops ? "No crew saved in Shift Allocation for this line in the last 7 days." : "Loading…")}
            </div>
          )}
      </div>
      {/* legend at the bottom (operator, 7-Nov) */}
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", columnGap: 16, rowGap: 3,
                    padding: "3px 8px 6px", borderTop: "1px solid #d4d4d8" }}>
        {leg(box(Y_GREEN), "Within target")}
        {leg(box(Y_RED), "Above target")}
        {leg(<i style={{ flexShrink: 0, display: "inline-block", width: 16, borderTop: `3px solid ${Y_TARGET}` }} />,
             `Target (takt)${target ? ` ${target.toFixed(1)} s` : ""}`)}
        {leg(box(Y_GREY, true), "Manual / no signal")}
      </div>
    </div>
  );
}

// ── viewer: the full sheet over the page (same component as the Control Board page) ──
function Viewer({ open, onClose, sheets, title, rotated }) {
  const [tab, setTab] = useState(0);
  useEffect(() => { setTab(0); }, [open]);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  const cur = sheets[Math.min(tab, sheets.length - 1)];
  return createPortal(
    <div onClick={onClose} style={{ position: "fixed", zIndex: 9400, background: "rgba(2,6,23,.72)",
                                    overflowY: "auto", padding: "calc(var(--cap-sat, 0px) + 14px) 14px 30px",
                                    // turned with the page on a vertical TV (page "Rotate" on)
                                    ...(rotated
                                      ? { top: 0, left: "100vw", width: "100vh", height: "100vw",
                                          transform: "rotate(90deg)", transformOrigin: "top left" }
                                      : { inset: 0 }) }}>
      <div onClick={e => e.stopPropagation()} style={{ maxWidth: 1900, margin: "0 auto",
                                                      fontFamily: "'Barlow',sans-serif", color: "#0f172a" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
          <span style={{ fontSize: 18, fontWeight: 800, color: "#fff" }}>{title}</span>
          {sheets.length > 1 && (
            <div style={{ display: "flex", border: "1px solid #64748b", borderRadius: 8, overflow: "hidden" }}>
              {sheets.map((s, i) => (
                <button key={s.tab} onClick={() => setTab(i)} style={{
                  border: "none", padding: "7px 14px", fontSize: 13, fontWeight: 700, cursor: "pointer",
                  fontFamily: "inherit", background: i === tab ? "#2563eb" : "#fff",
                  color: i === tab ? "#fff" : "#334155" }}>{s.tab}</button>
              ))}
            </div>
          )}
          <button onClick={onClose} style={{ marginLeft: "auto", border: "1px solid #cbd5e1", background: "#fff",
                                             color: "#334155", borderRadius: 8, padding: "7px 14px", fontSize: 13,
                                             fontWeight: 700, cursor: "pointer", fontFamily: "inherit" }}>
            Close
          </button>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>{cur.render()}</div>
      </div>
    </div>,
    document.body);
}

const sheetOf = (key) => {
  const i = ALL.findIndex(s => s.key === key);
  return { sheet: ALL[i], n: i + 1 };
};

export default function ControlBoardViewDocs({ lineId, lineName, zoneId, recordDate, shiftName, idealCt, taktSec,
                                               narrow, rotated, style }) {
  const { token } = useAuth();
  const month = String(recordDate || ymd(new Date())).slice(0, 7);
  const isRec = Number(zoneId) === 2;          // 2 = Recliner zone
  const zoneName = isRec ? "Recliner" : "";

  // line list of the sheets' line dropdowns (the lines this login may see)
  const linesQ = useApi("/api/lines/", token, { ttl: 300000 });
  const lineOpts = useMemo(() => (Array.isArray(linesQ.data) ? linesQ.data : [])
    .map(l => ({ line_id: l.id, line_name: l.line_name || l.line_code || `Line ${l.id}`, zone_name: l.zone_name || "No zone" }))
    .sort((a, b) => a.zone_name.localeCompare(b.zone_name) || a.line_name.localeCompare(b.line_name)),
  [linesQ.data]);
  const stdQ = useApi("/api/standard-data/track-record", token, { ttl: 300000 });

  // tiles: this line, this month (refreshed every 5 min)
  const base = { line_id: Number(lineId) || null, month };
  const thumbTrackQ = useApi(lineId ? `/api/standard-data/track-record/sheet?line_id=${lineId}&month=${month}` : null,
                             token, { ttl: 240000, refreshMs: 300000 });

  // viewer: starts on this line / month; a pick inside a sheet moves it
  const [open, setOpen] = useState(null);      // "track" | "4m" | "defect" | "safety" | "layout"
  const [sel, setSel] = useState(base);
  useEffect(() => { if (open) setSel(base); }, [open]);    // eslint-disable-line react-hooks/exhaustive-deps
  const onDataSelect = (kind, msg) => {
    const lid = msg && msg.line_id ? Number(msg.line_id) : null;
    const mon = (msg && msg.month) || sel.month;
    setSel(s => (s.line_id === lid && s.month === mon) ? s : { line_id: lid, month: mon });
  };
  const viewTrackQ = useApi(open === "track" && sel.line_id && sel.month
    ? `/api/standard-data/track-record/sheet?line_id=${sel.line_id}&month=${sel.month}` : null, token, { ttl: 60000 });

  const trackLines = Array.isArray(stdQ.data) ? stdQ.data.map(r => ({
    line_id: r.line_id, line_name: r.line_name, zone_name: r.zone_name, cycle_time: r.cycle_time, target: r.target,
  })) : null;
  const trackData = (s, q) => (trackLines ? {
    lines: trackLines, line_id: s.line_id, month: s.month, sheet: q.data, loading: q.loading, error: q.error,
  } : null);
  const docData = {
    "track-record": trackData(sel, viewTrackQ),
    "line-month": { lines: lineOpts, line_id: sel.line_id, month: sel.month },
  };
  const thumbLineMonth = lineOpts.length ? { lines: lineOpts, ...base } : null;

  const fileTag = `${lineName || ""} ${recordDate || ""}`.trim();
  const doc = (key, variants, label) => {
    const { sheet, n } = sheetOf(key);
    const sh = label ? { ...sheet, label } : sheet;
    return (
      <SheetDoc key={`${key}:${variants.map(v => `${v.data || v.file}:${v.ops || v.label}`).join("|")}`} sheet={sh} n={n} variants={variants}
                zoneName={zoneName} fileTag={fileTag} token={token} docData={docData}
                onDataSelect={onDataSelect} onClose={() => setOpen(null)}
                pageSel={{ line_id: Number(lineId) || null, date: recordDate || new Date().toISOString().slice(0, 10) }} />
    );
  };
  const defectVariants = DOCS["defect-recording"];
  const defect = defectVariants.find(v => (v.zone === "recliner") === isRec) || defectVariants[0];
  const layoutVariants = (DOCS["layout-yamazumi"] || []).filter(v => !v.match || v.match({
    zoneName: isRec ? "Recliner" : "SEAT SLIDER", lineName, hasLine: true, all: false,
    isYRASS: /^YRA-SS$/i.test(String(lineName || "").trim()),
  }));
  const VIEW = {
    track:  { title: "Track Record Sheet", sheets: [{ tab: "Track Record", render: () => doc("track-sheet", DOCS["track-sheet"]) }] },
    "4m":   { title: "4M Change", sheets: [
      { tab: "Operator Status", render: () => DOCS["operator-visual"].map(v =>
          doc("operator-visual", [v], `Operator Visualization Sheet — ${v.label}`)) },
      { tab: "4M Change Control Sheet", render: () => doc("4m-change", DOCS["4m-change"]) },
    ] },
    defect: { title: "Defect Recording Sheet", sheets: [{ tab: "Defect", render: () => doc("defect-recording", [defect]) }] },
    safety: { title: "Annual Safety Chart", sheets: [{ tab: "Safety", render: () => doc("annual-safety", DOCS["annual-safety"]) }] },
    layout: { title: "Layout / Yamazumi Chart", sheets: [{ tab: "Layout", render: () => (layoutVariants.length
      ? doc("layout-yamazumi", layoutVariants)
      : <div style={{ background: "#fff", borderRadius: 12, padding: 20 }}>Layout not added for this line yet.</div>) }] },
  };

  const track = DOCS["track-sheet"][0];
  const m4 = DOCS["4m-change"][0];
  const safety = DOCS["annual-safety"][0];
  return (
    <div style={{ display: "grid", gap: 6, minHeight: 0,
                  ...(narrow
                    // phone: one column, each tile its own height
                    ? { gridTemplateColumns: "minmax(0,1fr)", gridTemplateRows: "560px 300px 340px 340px 400px",
                        gridTemplateAreas: `"track" "yam" "m4" "defect" "safety"` }
                    : { gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)",
                        gridTemplateRows: "repeat(6, minmax(0,1fr))",
                        gridTemplateAreas: `"track yam" "track yam" "track m4" "track m4" "defect safety" "defect safety"` }),
                  ...style }}>
      <Tile area="track" title="Open the Track Record Sheet" onOpen={() => setOpen("track")}>
        <SheetThumb file={track.file} width={track.width} data={trackData(base, thumbTrackQ)} />
        <TileLabel>Track Record</TileLabel>
      </Tile>
      <YamazumiTile lineId={lineId} recordDate={recordDate} shiftName={shiftName} idealCt={idealCt} target={taktSec}
                    token={token} onOpen={() => setOpen("layout")} />
      <Tile area="m4" title="Open 4M Change → Operator Status" onOpen={() => setOpen("4m")}>
        <SheetThumb file={m4.file} width={m4.width} data={thumbLineMonth} />
        <TileLabel style={{ top: "34%" }}>4M Change</TileLabel>
        <span style={{ position: "absolute", right: 8, bottom: 8, border: "2px dashed #1f4e9c",
                       background: "rgba(255,255,255,.94)", color: LABEL_CLR, fontWeight: 800,
                       fontSize: "clamp(11px, 1.4vmin, 19px)", padding: "0.45em 1.1em", pointerEvents: "none",
                       fontFamily: "'Segoe UI',system-ui,sans-serif" }}>
          Operator Status ›
        </span>
      </Tile>
      <Tile area="defect" title="Open the Defect Recording Sheet" onOpen={() => setOpen("defect")}>
        <SheetThumb file={defect.file} width={defect.width} data={thumbLineMonth} />
        <TileLabel>Defect Record</TileLabel>
      </Tile>
      <Tile area="safety" title="Open the Annual Safety Chart" onOpen={() => setOpen("safety")}>
        <SheetThumb file={safety.file} width={safety.width} />
        <TileLabel style={{ top: "72%" }}>Safety Chart</TileLabel>
      </Tile>
      <Viewer open={!!open} onClose={() => setOpen(null)} title={open ? VIEW[open].title : ""} rotated={rotated}
              sheets={open ? VIEW[open].sheets : []} />
    </div>
  );
}
