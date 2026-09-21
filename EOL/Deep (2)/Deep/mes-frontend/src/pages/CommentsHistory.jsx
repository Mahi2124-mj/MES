/* ───────────────────────────────────────────────────────────────────
 * CommentsHistory.jsx
 * ───────────────────────────────────────────────────────────────────
 * Combined audit log of:
 *   • mes_cycle_comments       (free-text comments from Fullscreen
 *                                video modal — multi-comment thread
 *                                per part_code)
 *   • mes_ng_process_remarks   (per-machine NG remarks from Wallboard
 *                                sub-machine modal — one row per
 *                                (part_code, machine_id))
 *
 * Filters: date range, shift, line, part_code substring, machine,
 *          free-text search across comment / remark.
 *
 * Built 2026-05-27 on operator request: "kuch bhi cycle pe comment
 * dale uski history kha aayegi" → dedicated page, no need to dig DB.
 *
 * Backend: GET /api/lines/{line_id}/comments-history
 *          line_id = 0  → all lines
 *
 * Access: any logged-in user; no role gating (operator + quality +
 *         production all benefit from visibility).
 * ───────────────────────────────────────────────────────────────── */
import { useRef, useEffect, useMemo, useState } from "react";
import VideoProgressBar from "../components/VideoProgressBar";
import { useAuth } from "../context/AuthContext";

const API = "";

function api(path, token) {
  return fetch(API + path, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  }).then(r => {
    if (!r.ok) return r.text().then(t => { throw new Error(t || `HTTP ${r.status}`); });
    return r.json();
  });
}

// Local (plant) date.  toISOString() is UTC, which between 00:00 and 05:30 IST
// still says yesterday — with a one-day default that would open on the wrong day.
function todayISO() { return new Date().toLocaleDateString("en-CA"); }

// 2026-06-09 — derive the video's own date + a playable cycle-video URL from a
// row's part_code.  Video is only kept for CURRENT-date cycles (recorder
// rotates old TS), so the Play button only shows when partDate == today.
//   • main-line comment : "cycle_<seq>_<YYYY-MM-DD>"   → /api/lines/{line}/cycle-video
//   • sub-machine NG     : "M<machine_id>-C<cycle>-<date>" → /api/submachines/{id}/cycle-video
function todayLocal() { return new Date().toLocaleDateString("en-CA"); }   // YYYY-MM-DD local
function partDate(pc) {
  const m = String(pc || "").match(/(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : null;
}
function videoUrlFor(r) {
  const pc = String(r.part_code || "");
  let m = pc.match(/^cycle_(\d+)_/i);
  if (m) return `/api/lines/${r.line_id}/cycle-video?cycle_seq=${m[1]}`;
  m = pc.match(/^M(\d+)-C(\d+)-/i);
  if (m) return `/api/submachines/${m[1]}/cycle-video?cycle_seq=${m[2]}`;
  return null;
}

// 2026-09-19 — the Date / Time columns show WHEN THE VIDEO WAS CAPTURED (the
// cycle's own time from its ct_log row), not when the comment was typed; the
// two are often an hour apart.  The API sends both as plant-clock strings
// ("YYYY-MM-DD HH:MM:SS"), so nothing here depends on the viewing laptop's
// timezone.  A row whose cycle is not in the log keeps its date (the part code
// carries it) and shows no time, rather than passing the comment time off as
// the capture time.
function captureOf(r) {
  if (r.video_ts) {
    return { date: r.video_ts.slice(0, 10), time: r.video_ts.slice(11, 19), fromLog: true };
  }
  const d = partDate(r.part_code) || r.record_date || (r.ts_local || "").slice(0, 10) || "";
  return { date: d, time: "", fromLog: false };
}
function fmtDate(iso) {                 // "2026-09-19" -> "19-09-2026"
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || "");
  return m ? `${m[3]}-${m[2]}-${m[1]}` : "";
}
function fmtTime(hms) {                 // "13:05:07" -> "01:05:07 PM"
  const m = /^(\d{2}):(\d{2}):(\d{2})/.exec(hms || "");
  if (!m) return "";
  const h = +m[1];
  return `${String(h % 12 || 12).padStart(2, "0")}:${m[2]}:${m[3]} ${h >= 12 ? "PM" : "AM"}`;
}
function commentedAt(r) {
  const t = r.ts_local || "";
  return t ? `${fmtDate(t.slice(0, 10))} ${fmtTime(t.slice(11, 19))}` : "";
}
function localStamp() {                 // plant clock "YYYY-MM-DD HH:MM:SS", now
  const d = new Date(), p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} `
       + `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// jsPDF is kept next to xlsx.full.min.js and loaded only when a PDF is asked
// for — a CDN copy would leave the plant's LAN-only machines without a PDF.
function loadJsPdf() {
  if (window.jspdf && window.jspdf.jsPDF) return Promise.resolve(window.jspdf.jsPDF);
  if (loadJsPdf._p) return loadJsPdf._p;
  loadJsPdf._p = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "/jspdf.umd.min.js";
    s.onload = () => (window.jspdf && window.jspdf.jsPDF)
      ? resolve(window.jspdf.jsPDF) : reject(new Error("jsPDF did not load"));
    s.onerror = () => { loadJsPdf._p = null; reject(new Error("could not load /jspdf.umd.min.js")); };
    document.head.appendChild(s);
  });
  return loadJsPdf._p;
}
// The PDF's built-in font covers Latin-1 only; anything else would print as
// garbage, so fold it to its plain equivalent first.
function pdfText(v) {
  return String(v == null ? "" : v).normalize("NFKC")
    .replace(/[\u2010-\u2015]/g, "-").replace(/\u2026/g, "...")
    .replace(/[\u2018\u2019]/g, "'").replace(/[\u201C\u201D]/g, '"')
    .replace(/[^\t\n\r -ÿ]/g, "?");
}

export default function CommentsHistory() {
  // Player element, so the always-visible progress bar can read its time.
  const vidRef = useRef(null);
  const { token, canAccessModule } = useAuth() || {};
  const canComments = canAccessModule ? canAccessModule("comments-history", "comments") : true;
  const canPareto   = canAccessModule ? canAccessModule("comments-history", "pareto")   : true;

  // Which module (tab) is showing. Default to whichever the user can see.
  const [tab, setTab] = useState(canComments ? "comments" : (canPareto ? "pareto" : "comments"));

  // Pareto module state (comment-based: which problem repeats most; scoped by
  // the page's machine / line / zone / date / shift filters).
  const [pareto,   setPareto]   = useState(null);
  const [pLoading, setPLoading] = useState(false);

  // Filter state
  // 2026-09-21 — opens on today only (was the last 7 days, which made the page
  // slow to open).  Any wider range is still one date-picker change away.
  const [dateFrom, setDateFrom] = useState(todayISO());
  const [dateTo,   setDateTo]   = useState(todayISO());
  const [zoneId,   setZoneId]   = useState(0);          // 0 = all zones
  const [lineId,   setLineId]   = useState(0);          // 0 = all lines
  const [shift,    setShift]    = useState("");
  const [partCode, setPartCode] = useState("");
  const [machineId, setMachineId] = useState("");
  const [q,        setQ]        = useState("");
  // Cycle-time band filter: show/export only cycles whose CT falls between
  // these seconds (operator: "sirf 15 se 30 ke export karne hain"). Applied on
  // the client because every row already carries cycle_time — no refetch.
  const [ctMin,    setCtMin]    = useState("");
  const [ctMax,    setCtMax]    = useState("");
  // Headline counts for the selected window (total parts / over-target / commented).
  const [summary,  setSummary]  = useState(null);

  const [zones,   setZones]   = useState([]);
  const [lines,   setLines]   = useState([]);
  const [machines, setMachines] = useState([]);
  const [rows,    setRows]    = useState([]);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState("");
  const [lastFetch, setLastFetch] = useState(null);
  const [videoRow,  setVideoRow]  = useState(null);   // row whose video popup is open

  // Load lines + machines for filter dropdowns (once)
  useEffect(() => {
    api("/api/lines/", token).then(d => {
      const arr = Array.isArray(d) ? d : (d.data || d.lines || []);
      setLines(arr);
    }).catch(() => {});
    api("/api/zones/", token).then(d => {
      const arr = Array.isArray(d) ? d : (d.data || d.zones || []);
      setZones(arr);
    }).catch(() => {});
    api("/api/plc-configs/", token).then(d => {
      const arr = Array.isArray(d) ? d : (d.data || []);
      setMachines(arr.filter(m => m.parent_plc_id));
    }).catch(() => {});
  }, [token]);

  // Fetch comments
  const refresh = () => {
    setLoading(true); setError("");
    const params = new URLSearchParams({
      date_from: dateFrom,
      date_to:   dateTo,
    });
    if (shift)     params.set("shift_name", shift);
    if (partCode)  params.set("part_code",  partCode);
    if (machineId) params.set("machine_id", machineId);
    if (q)         params.set("q",          q);
    const ln = Number(lineId) || 0;
    api(`/api/lines/${ln}/comments-history?${params.toString()}`, token)
      .then(d => { setRows(d.rows || []); setLastFetch(new Date()); })
      .catch(e => setError(String(e.message || e)))
      .finally(() => setLoading(false));
    // Headline counts for the same window — how many parts ran, how many were
    // over target, and how many carried a comment.
    const sp = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
    if (shift) sp.set("shift_name", shift);
    api(`/api/lines/${ln}/comments-summary?${sp.toString()}`, token)
      .then(d => setSummary(d))
      .catch(() => setSummary(null));
  };

  // Auto-load on mount
  useEffect(() => { refresh(); /* eslint-disable-next-line */ }, []);

  // Pareto fetch — recorded conditions (comments + NG remarks) grouped by base.
  const refreshPareto = () => {
    setPLoading(true);
    const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
    if (shift)          params.set("shift_name", shift);
    if (machineId)      params.set("machine_id", String(machineId));
    if (Number(zoneId)) params.set("zone_id", String(Number(zoneId)));
    const ln = Number(lineId) || 0;   // 0 = all lines (line_id is in the path)
    api(`/api/lines/${ln}/comments-pareto?${params.toString()}`, token)
      .then(d => setPareto(d))
      .catch(() => setPareto({ items: [], total: 0 }))
      .finally(() => setPLoading(false));
  };
  // Reload the Pareto when its tab opens (Apply re-runs it for filter changes).
  useEffect(() => {
    if (tab === "pareto") refreshPareto();
    /* eslint-disable-next-line */
  }, [tab]);

  // Rows actually shown/exported. The ZONE filter is applied HERE (not just to
  // the Line dropdown): pick a zone with Line = "All" → only that zone's lines.
  // A specific Line is already filtered by the backend query.
  const displayRows = useMemo(() => {
    let out = rows;
    if (!Number(lineId) && Number(zoneId)) {
      const zids = new Set(
        lines.filter(l => String(l.zone_id) === String(zoneId)).map(l => l.id));
      out = out.filter(r => zids.has(r.line_id));
    }
    // Cycle-time band (seconds). Rows with no CT are dropped once a band is set,
    // since they can't be judged against it.
    const lo = ctMin === "" ? null : Number(ctMin);
    const hi = ctMax === "" ? null : Number(ctMax);
    if (lo != null && !Number.isNaN(lo)) out = out.filter(r => r.cycle_time != null && r.cycle_time >= lo);
    if (hi != null && !Number.isNaN(hi)) out = out.filter(r => r.cycle_time != null && r.cycle_time <= hi);
    return out;
  }, [rows, lineId, zoneId, lines, ctMin, ctMax]);

  // Cycle-time distribution of the rows on screen — 10 buckets across the
  // observed CT range, so the operator can see where the mass sits and then
  // set the band filter on it.
  const ctDist = useMemo(() => {
    const vals = displayRows.map(r => r.cycle_time).filter(v => v != null && !Number.isNaN(v));
    if (!vals.length) return null;
    const sorted = [...vals].sort((a, b) => a - b);
    const min = sorted[0], max = sorted[sorted.length - 1];
    // Stopped/idle cycles produce huge outliers (seen: max 1925 s against a
    // ~15 s target), which would squash every real cycle into one bar. Bucket
    // across the 1st-95th percentile and collect the tail in one overflow bar,
    // so the shape of the ACTUAL cycle times is visible.
    const pct = p => sorted[Math.min(sorted.length - 1,
                                     Math.max(0, Math.floor((p / 100) * sorted.length)))];
    let lo = pct(1), hi = pct(95);
    if (!(hi > lo)) { lo = min; hi = max; }
    const N = 10, step = ((hi - lo) || 1) / N;
    const buckets = Array.from({ length: N }, (_, i) => ({
      from: +(lo + i * step).toFixed(1),
      to:   +(lo + (i + 1) * step).toFixed(1),
      n: 0,
    }));
    let over = 0, under = 0;
    for (const v of vals) {
      if (v > hi) { over++; continue; }
      if (v < lo) { under++; continue; }
      let i = Math.floor((v - lo) / step);
      if (i >= N) i = N - 1;
      if (i < 0) i = 0;
      buckets[i].n++;
    }
    if (over) buckets.push({ from: +hi.toFixed(1), to: +max.toFixed(1), n: over, overflow: true });
    const peak = Math.max(...buckets.map(b => b.n)) || 1;
    return { buckets, peak, min, max, count: vals.length, over, under,
             avg: +(vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(2) };
  }, [displayRows]);

  // ── Chart images for the export ────────────────────────────────────────
  // Both charts are redrawn onto an offscreen canvas from the SAME data the
  // screen uses, rather than screenshotting the DOM. Two reasons: the
  // distribution chart is built from <div> bars (nothing to serialise), and
  // the Pareto <svg> only exists while its tab is open — the operator
  // exports from the Comments tab, so a DOM grab would come back empty.
  const PNG_W = 1000, PNG_H = 420;

  const newCanvas = () => {
    const cv = document.createElement("canvas");
    cv.width = PNG_W; cv.height = PNG_H;
    const g = cv.getContext("2d");
    g.fillStyle = "#ffffff"; g.fillRect(0, 0, PNG_W, PNG_H);
    g.font = "13px Segoe UI, system-ui, sans-serif";
    return [cv, g];
  };

  const drawParetoPng = (items) => {
    if (!items || !items.length) return null;
    const [cv, g] = newCanvas();
    const bars = items.slice(0, 20);
    const padL = 55, padR = 55, padT = 40, padB = 110;
    const plotW = PNG_W - padL - padR, plotH = PNG_H - padT - padB;
    const maxCount = Math.max(...bars.map(b => Number(b.count) || 0), 1);
    const bw = Math.min(46, (plotW / bars.length) * 0.62);
    const xC = i => padL + (plotW / bars.length) * (i + 0.5);
    const yCount = v => padT + plotH * (1 - (Number(v) || 0) / maxCount);
    const yPct = p => padT + plotH * (1 - (Number(p) || 0) / 100);

    g.fillStyle = "#0f172a"; g.font = "bold 15px Segoe UI, system-ui, sans-serif";
    g.fillText("Problem Pareto", padL, 24);

    // gridlines + both axes
    g.font = "11px Segoe UI, system-ui, sans-serif";
    [0, .25, .5, .75, 1].forEach(f => {
      const y = padT + plotH * (1 - f);
      g.strokeStyle = "#eef2f7"; g.beginPath();
      g.moveTo(padL, y); g.lineTo(PNG_W - padR, y); g.stroke();
      g.fillStyle = "#94a3b8"; g.textAlign = "right";
      g.fillText(String(Math.round(maxCount * f)), padL - 8, y + 4);
      g.textAlign = "left";
      g.fillText(Math.round(100 * f) + "%", PNG_W - padR + 8, y + 4);
    });
    // 80% reference line — the vital-few cut
    g.strokeStyle = "#f59e0b"; g.setLineDash([5, 4]); g.beginPath();
    g.moveTo(padL, yPct(80)); g.lineTo(PNG_W - padR, yPct(80)); g.stroke();
    g.setLineDash([]);

    bars.forEach((b, i) => {
      const vital = (i === 0 ? 0 : Number(bars[i - 1].cum_pct) || 0) < 80;
      const y = yCount(b.count);
      g.fillStyle = vital ? "#2563eb" : "#93c5fd";
      g.fillRect(xC(i) - bw / 2, y, bw, padT + plotH - y);
      g.fillStyle = "#0f172a"; g.textAlign = "center";
      g.font = "10px Segoe UI, system-ui, sans-serif";
      g.fillText(String(b.count), xC(i), y - 4);
      // rotated problem label
      g.save();
      g.translate(xC(i), padT + plotH + 10);
      g.rotate(-Math.PI / 4);
      g.fillStyle = "#475569"; g.textAlign = "right";
      const nm = String(b.name || "");
      g.fillText(nm.length > 26 ? nm.slice(0, 25) + "…" : nm, 0, 0);
      g.restore();
    });
    // cumulative curve
    g.strokeStyle = "#ef4444"; g.lineWidth = 2; g.beginPath();
    bars.forEach((b, i) => {
      const x = xC(i), y = yPct(b.cum_pct);
      i === 0 ? g.moveTo(x, y) : g.lineTo(x, y);
    });
    g.stroke();
    g.fillStyle = "#ef4444";
    bars.forEach((b, i) => {
      g.beginPath(); g.arc(xC(i), yPct(b.cum_pct), 3, 0, Math.PI * 2); g.fill();
    });
    g.lineWidth = 1; g.textAlign = "left";
    return cv.toDataURL("image/png");
  };

  const drawCtDistPng = (dist) => {
    if (!dist || !dist.buckets || !dist.buckets.length) return null;
    const [cv, g] = newCanvas();
    const bs = dist.buckets;
    const padL = 55, padR = 25, padT = 40, padB = 70;
    const plotW = PNG_W - padL - padR, plotH = PNG_H - padT - padB;
    const maxN = Math.max(...bs.map(b => b.n || 0), 1);
    const bw = (plotW / bs.length) * 0.8;
    const xC = i => padL + (plotW / bs.length) * (i + 0.5);

    g.fillStyle = "#0f172a"; g.font = "bold 15px Segoe UI, system-ui, sans-serif";
    g.fillText("Cycle Time Distribution", padL, 24);
    g.font = "11px Segoe UI, system-ui, sans-serif";
    g.fillStyle = "#64748b";
    g.fillText(`${dist.count} cycles  ·  min ${dist.min}s  ·  avg ${dist.avg}s  ·  max ${dist.max}s`,
               padL + 200, 24);

    [0, .5, 1].forEach(f => {
      const y = padT + plotH * (1 - f);
      g.strokeStyle = "#eef2f7"; g.beginPath();
      g.moveTo(padL, y); g.lineTo(PNG_W - padR, y); g.stroke();
      g.fillStyle = "#94a3b8"; g.textAlign = "right";
      g.fillText(String(Math.round(maxN * f)), padL - 8, y + 4);
    });
    bs.forEach((b, i) => {
      const h = plotH * ((b.n || 0) / maxN);
      const y = padT + plotH - h;
      // the last bucket collects the stopped/idle outliers — flag it orange
      g.fillStyle = b.overflow ? "#f59e0b" : "#3b82f6";
      g.fillRect(xC(i) - bw / 2, y, bw, h);
      g.textAlign = "center"; g.fillStyle = "#0f172a";
      g.font = "10px Segoe UI, system-ui, sans-serif";
      if (b.n) g.fillText(String(b.n), xC(i), y - 4);
      g.fillStyle = "#475569";
      g.fillText(b.overflow ? `>${b.from}s` : String(b.from), xC(i), padT + plotH + 16);
    });
    g.textAlign = "left";
    return cv.toDataURL("image/png");
  };

  // Group counts for top stats
  const stats = useMemo(() => {
    const tot = displayRows.length;
    const cmt = displayRows.filter(r => r.type === "Comment").length;
    const ng  = displayRows.filter(r => r.type === "NG-Remark").length;
    return { tot, cmt, ng };
  }, [displayRows]);

  // Resolve line name + machine name from the loaded lookup tables
  const lineNameById = useMemo(() => {
    const m = new Map();
    lines.forEach(l => m.set(l.id, l.line_name || l.line_code || "—"));
    return m;
  }, [lines]);
  // Line dropdown is scoped to the chosen zone (0 = all zones → all lines).
  const filteredLines = useMemo(
    () => (zoneId ? lines.filter(l => String(l.zone_id) === String(zoneId)) : lines),
    [lines, zoneId]);
  const machineNameById = useMemo(() => {
    const m = new Map();
    machines.forEach(x => m.set(x.id, x.machine_name || `M${x.id}`));
    return m;
  }, [machines]);

  // Human label for the current Pareto scope (driven by the filter bar).
  const paretoScope =
      machineId       ? (machineNameById.get(Number(machineId)) || `Machine ${machineId}`)
    : Number(lineId)  ? (lineNameById.get(Number(lineId)) || "—")
    : Number(zoneId)  ? (((zones.find(z => String(z.id) === String(zoneId)) || {}).zone_name) || `Zone ${zoneId}`)
    :                   "All machines · lines · zones";

  // ── Export ─────────────────────────────────────────────────────────────
  // Two files, one layout.  Excel is the data (Comments, Pareto and Summary
  // sheets, both charts as pictures); PDF is the same report ready to print.
  // Both carry exactly the rows on screen — every filter, incl. Zone and the
  // cycle-time band — in the same columns as the table, Date and Time being
  // the video's capture time.
  //
  // 2026-09-19 — replaces the .mht web-archive.  It carried the charts but was
  // not a spreadsheet: it opened in a browser, or in Excel only after a
  // "format does not match" warning.  Operator: "excel format me chahiye, sath
  // me pdf ka bhi button" — and the Pareto tab gets its own Excel button.
  const [exporting, setExporting] = useState("");   // "" | "xlsx" | "pdf" | "pareto"
  const [exportMsg, setExportMsg] = useState("");

  // width = Excel column width; pdf = millimetres on landscape A4 (277 usable)
  const EXPORT_COLS = [
    { key: "date",    header: "Date",             type: "date",   width: 12, pdf: 19 },
    { key: "time",    header: "Time",             type: "time",   width: 13, pdf: 21 },
    { key: "line",    header: "Line",             type: "text",   width: 13, pdf: 22 },
    { key: "shift",   header: "Shift",            type: "text",   width: 7,  pdf: 10 },
    { key: "machine", header: "Machine",          type: "text",   width: 20, pdf: 30 },
    { key: "part",    header: "Part Code",        type: "text",   width: 24, pdf: 37 },
    { key: "ct",      header: "Cycle Time (s)",   type: "number", width: 13, pdf: 17 },
    { key: "text",    header: "Comment / Remark", type: "text",   width: 46, pdf: 82 },
    { key: "author",  header: "Author",           type: "text",   width: 12, pdf: 18 },
    { key: "leader",  header: "Leader",           type: "text",   width: 16, pdf: 21 },
  ];
  // Raw values: dates/times go to Excel as ISO text and the server turns them
  // into real Excel dates; the PDF formats the same values like the screen.
  const exportCells = r => {
    const cap = captureOf(r);
    return [
      cap.date, cap.time,
      lineNameById.get(r.line_id) || String(r.line_id ?? ""),
      r.shift_name || "",
      r.machine_name || (r.machine_id ? (machineNameById.get(r.machine_id) || "") : ""),
      r.part_code || "",
      r.cycle_time != null ? r.cycle_time : "",
      r.text || "", r.author || "", r.leader_name || "",
    ];
  };
  const pdfCells = r => {
    const c = exportCells(r);
    return [fmtDate(c[0]) || "-", c[1] ? fmtTime(c[1]) : "-", c[2] || "-", c[3] || "-",
            c[4] || "-", c[5] || "-", c[6] !== "" ? `${c[6]}s` : "-", c[7] || "-",
            c[8] || "-", c[9] || "-"];
  };

  const filterText = () => [
    `${fmtDate(dateFrom)} to ${fmtDate(dateTo)}`,
    shift ? `Shift ${shift}` : "All shifts",
    Number(lineId) ? (lineNameById.get(Number(lineId)) || `Line ${lineId}`)
      : Number(zoneId) ? ((zones.find(z => String(z.id) === String(zoneId)) || {}).zone_name
                          || `Zone ${zoneId}`)
      : "All lines",
    machineId ? (machineNameById.get(Number(machineId)) || `Machine ${machineId}`) : null,
    (ctMin || ctMax) ? `Cycle time ${ctMin || "0"}-${ctMax || "any"}s` : null,
    partCode ? `Part ${partCode}` : null,
    q ? `Search "${q}"` : null,
  ].filter(Boolean).join("  ·  ");

  // The same six numbers the stats strip shows.
  const summaryRows = () => [
    ["Total entries",       stats.tot],
    ["Per-cycle comments",  stats.cmt],
    ["NG remarks",          stats.ng],
    ...(summary ? [
      ["Total parts",                                    summary.total_parts || 0],
      [`Over target (${summary.over_target_pct ?? 0}%)`, summary.over_target || 0],
      [`Commented (${summary.commented_pct ?? 0}%)`,     summary.comments    || 0],
    ] : []),
  ];

  // The Pareto tab may never have been opened, so its data can be missing —
  // fetch it here so the full export always carries the ranking and its chart.
  const paretoFor = () => (pareto && pareto.items)
    ? Promise.resolve(pareto)
    : (() => {
        const pp = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
        if (shift)          pp.set("shift_name", shift);
        if (machineId)      pp.set("machine_id", String(machineId));
        if (Number(zoneId)) pp.set("zone_id", String(Number(zoneId)));
        return api(`/api/lines/${Number(lineId) || 0}/comments-pareto?${pp.toString()}`, token)
          .then(d => { setPareto(d); return d; })
          .catch(() => ({ items: [], total: 0 }));
      })();

  const fileStem = () => `comments-history_${dateFrom}_to_${dateTo}`;
  const saveBlob = (blob, name) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 400);
  };
  const postXlsx = body =>
    fetch(API + "/api/comments-export/xlsx", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify(body),
    }).then(r => {
      if (r.ok) return r.blob();
      if (r.status === 404) throw new Error("the server does not have the Excel export yet — it needs an API restart");
      return r.text().then(t => {
        let msg = t;
        try { msg = JSON.parse(t).detail || t; } catch { /* plain text */ }
        throw new Error(msg || `HTTP ${r.status}`);
      });
    });
  const runExport = (kind, job) => {
    if (exporting) return;
    setExporting(kind); setExportMsg("");
    Promise.resolve().then(job)
      .catch(e => setExportMsg("Export failed: " + String((e && e.message) || e)))
      .finally(() => setExporting(""));
  };

  const exportXlsx = () => {
    if (!displayRows.length) return;
    runExport("xlsx", () => paretoFor().then(pd => {
      const items = (pd && pd.items) || [];
      const charts = [
        ["Cycle Time Distribution", drawCtDistPng(ctDist)],
        ["Problem Pareto",          drawParetoPng(items)],
      ].filter(([, png]) => png).map(([title, png]) => ({ title, png }));
      return postXlsx({
        kind: "full", title: "Comments History", filters: filterText(),
        filename: fileStem() + ".xlsx", summary: summaryRows(),
        columns: EXPORT_COLS.map(({ key, header, type, width }) => ({ key, header, type, width })),
        rows: displayRows.map(exportCells),
        pareto: items, pareto_scope: paretoScope, charts,
      }).then(blob => saveBlob(blob, fileStem() + ".xlsx"));
    }));
  };

  // Pareto tab's own button: just the ranking and its chart.
  const exportParetoXlsx = () => {
    const items = (pareto && pareto.items) || [];
    if (!items.length) return;
    const name = `pareto_${dateFrom}_to_${dateTo}.xlsx`;
    runExport("pareto", () => postXlsx({
      kind: "pareto", title: "Problem Pareto", filters: filterText(), filename: name,
      pareto: items, pareto_scope: paretoScope,
      charts: [{ title: "Problem Pareto", png: drawParetoPng(items) }].filter(c => c.png),
    }).then(blob => saveBlob(blob, name)));
  };

  const PDF_PARETO_TOP = 30;
  const exportPdf = () => {
    if (!displayRows.length) return;
    runExport("pdf", () => Promise.all([loadJsPdf(), paretoFor()]).then(([JsPDF, pd]) => {
      const items = (pd && pd.items) || [];
      // compress: a week of comments is ~300 pages; uncompressed that came out
      // at 15 MB, too big to mail around.
      const doc = new JsPDF({ orientation: "landscape", unit: "mm", format: "a4",
                              compress: true });
      const W = 297, H = 210, M = 10, IW = W - 2 * M;
      const ink   = () => doc.setTextColor(15, 23, 42);
      const muted = () => doc.setTextColor(100, 116, 139);
      let y = M;

      // ── page 1: heading, the six numbers, cycle-time chart ──────────────
      doc.setFont("helvetica", "bold"); doc.setFontSize(16); ink();
      doc.text("Comments History", M, y + 6);
      doc.setFont("helvetica", "normal"); doc.setFontSize(9); muted();
      doc.text(doc.splitTextToSize(pdfText(filterText()), IW)[0], M, y + 12);
      const now = localStamp();
      doc.text(`Exported ${fmtDate(now.slice(0, 10))} ${fmtTime(now.slice(11, 19))}`, M, y + 17);
      y += 23;

      const sr = summaryRows();
      const bw = IW / sr.length;
      sr.forEach(([k, v], i) => {
        const x = M + i * bw;
        doc.setDrawColor(226, 232, 240); doc.setFillColor(248, 250, 252);
        doc.rect(x + 1, y, bw - 2, 16, "FD");
        doc.setFontSize(7); muted();
        doc.text(pdfText(String(k).toUpperCase()), x + 3, y + 5);
        doc.setFont("helvetica", "bold"); doc.setFontSize(13); ink();
        doc.text(Number(v).toLocaleString("en-IN"), x + 3, y + 12.5);
        doc.setFont("helvetica", "normal");
      });
      y += 22;

      const ctImg = drawCtDistPng(ctDist);
      if (ctImg) { doc.addImage(ctImg, "PNG", M, y, 230, 230 * PNG_H / PNG_W); }

      // ── page 2: Pareto chart + top of the ranking ───────────────────────
      const pImg = drawParetoPng(items);
      if (pImg || items.length) {
        doc.addPage(); y = M;
        doc.setFont("helvetica", "bold"); doc.setFontSize(12); ink();
        doc.text(pdfText(`Problem Pareto — ${paretoScope}`), M, y + 5);
        y += 8;
        if (pImg) { doc.addImage(pImg, "PNG", M, y, 150, 150 * PNG_H / PNG_W); }
        // ranking beside the chart
        const tx = M + 156, cw = [9, 70, 14, 14, 14];
        const hdrs = ["#", "Problem", "Count", "%", "Cum %"];
        let ty = y;
        doc.setFillColor(219, 228, 240); doc.rect(tx, ty, cw.reduce((a, b) => a + b), 5.5, "F");
        doc.setFont("helvetica", "bold"); doc.setFontSize(7.5); ink();
        let cx = tx;
        hdrs.forEach((h, i) => { doc.text(h, cx + 1, ty + 3.8); cx += cw[i]; });
        ty += 5.5;
        doc.setFont("helvetica", "normal");
        items.slice(0, PDF_PARETO_TOP).forEach((it, i) => {
          cx = tx;
          [String(i + 1), doc.splitTextToSize(pdfText(it.name), cw[1] - 2)[0],
           String(it.count), String(it.pct ?? ""), String(it.cum_pct ?? "")]
            .forEach((v, j) => { doc.text(v, cx + 1, ty + 3.6); cx += cw[j]; });
          doc.setDrawColor(226, 232, 240); doc.line(tx, ty + 5, tx + cw.reduce((a, b) => a + b), ty + 5);
          ty += 5;
        });
        if (items.length > PDF_PARETO_TOP) {
          doc.setFontSize(7); muted();
          doc.text(`Top ${PDF_PARETO_TOP} of ${items.length} problems — the full ranking `
                   + "is in the Excel export.", tx, ty + 4);
        }
      }

      // ── comments table, header repeated on every page ───────────────────
      const cols = EXPORT_COLS, widths = cols.map(c => c.pdf);
      const FS = 7.2, LHF = 1.25, LH = FS * 0.3528 * LHF, PX = 1.2, PY = 1.2;
      doc.setLineHeightFactor(LHF);
      // Headers wrap inside their own column — "Cycle Time (s)" in one line ran
      // into "Comment / Remark".
      const HFS = 7.6, HLH = HFS * 0.3528 * LHF;
      doc.setFont("helvetica", "bold"); doc.setFontSize(HFS);
      const hLines = cols.map((c, i) => doc.splitTextToSize(c.header, widths[i] - 2 * PX));
      const HH = Math.max(...hLines.map(l => l.length)) * HLH + 2.6;
      const header = () => {
        doc.setFillColor(219, 228, 240); doc.rect(M, y, IW, HH, "F");
        doc.setFont("helvetica", "bold"); doc.setFontSize(HFS); ink();
        let x = M;
        hLines.forEach((ls, i) => { doc.text(ls, x + PX, y + 1.3 + HFS * 0.3528 * 0.85); x += widths[i]; });
        y += HH;
        doc.setFont("helvetica", "normal"); doc.setFontSize(FS);
      };
      doc.addPage(); y = M;
      doc.setFont("helvetica", "bold"); doc.setFontSize(12); ink();
      doc.text(`Comments (${displayRows.length})`, M, y + 5);
      y += 8;
      header();
      displayRows.forEach((r, idx) => {
        const lines = pdfCells(r).map((v, i) => doc.splitTextToSize(pdfText(v), widths[i] - 2 * PX));
        const h = Math.max(1, ...lines.map(l => l.length)) * LH + 2 * PY;
        if (y + h > H - M - 6) { doc.addPage(); y = M; header(); }
        if (r.type === "NG-Remark") { doc.setFillColor(254, 242, 242); doc.rect(M, y, IW, h, "F"); }
        else if (idx % 2)           { doc.setFillColor(248, 250, 252); doc.rect(M, y, IW, h, "F"); }
        ink();
        let x = M;
        lines.forEach((ls, i) => { doc.text(ls, x + PX, y + PY + FS * 0.3528 * 0.85); x += widths[i]; });
        doc.setDrawColor(226, 232, 240); doc.line(M, y + h, W - M, y + h);
        y += h;
      });

      // ── footer on every page ─────────────────────────────────────────────
      const total = doc.internal.getNumberOfPages();
      const foot = doc.splitTextToSize(pdfText("Comments History  ·  " + filterText()), IW - 30)[0];
      for (let pg = 1; pg <= total; pg++) {
        doc.setPage(pg);
        doc.setFont("helvetica", "normal"); doc.setFontSize(7.5); muted();
        doc.text(foot, M, H - 5);
        doc.text(`Page ${pg} of ${total}`, W - M, H - 5, { align: "right" });
      }
      saveBlob(doc.output("blob"), fileStem() + ".pdf");
    }));
  };

  // ── Styles (inline, matches QualityDashboard / Historical look) ──
  const wrap = {
    padding: 18, minHeight: "100vh",
    background: "#f3f4f6", fontFamily: "'Barlow',sans-serif", color: "#0f172a",
  };
  const card = {
    background: "#fff", borderRadius: 8, boxShadow: "0 1px 4px rgba(0,0,0,0.06)",
    padding: 14, marginBottom: 14,
  };
  const lbl = { fontSize: 11, fontWeight: 700, color: "#475569",
                textTransform: "uppercase", letterSpacing: ".05em",
                marginBottom: 4, display: "block" };
  const inp = { padding: "6px 10px", fontSize: 13, borderRadius: 6,
                border: "1px solid #cbd5e1", background: "#fff",
                color: "#0f172a", minWidth: 0, width: "100%",
                boxSizing: "border-box" };   // border-box: width:100% no longer
                                             // overflows its grid cell (the date
                                             // input was spilling into "Line")
  const btnPri = { padding: "8px 18px", borderRadius: 6, border: "none",
                   background: "#2563eb", color: "#fff", cursor: "pointer",
                   fontSize: 13, fontWeight: 700, letterSpacing: ".04em" };
  const btnSec = { ...btnPri, background: "#16a34a" };

  return (
    <div style={wrap}>
     <div
  style={{
    display: "flex",
    alignItems: "center",
    marginBottom: 14,
    position: "relative",
  }}
>
  <h1
    style={{
      fontSize: 22,
      fontWeight: 800,
      margin: 0,
      color: "#0f172a",
      position: "absolute",
      left: "50%",
      transform: "translateX(-50%)",
      paddingBottom: 4,
    }}
  >
    Comments History
  </h1>

  <div
    style={{
      fontSize: 11,
      color: "#64748b",
      marginLeft: "auto",
    }}
  >
    {lastFetch
      ? `Last fetched: ${lastFetch.toLocaleTimeString("en-IN")}`
      : "—"}
  </div>
</div>

      {/* ── Filter bar ─────────────────────────────────── */}
      <div style={card}>
        <div style={{ display: "grid",
                       gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
                       gap: 10, marginBottom: 12 }}>
          <div>
            <label style={lbl}>From</label>
            <input type="date" value={dateFrom}
                   onChange={e => setDateFrom(e.target.value)}
                   style={{ ...inp, width: "100%" }} />
          </div>
          <div>
            <label style={lbl}>To</label>
            <input type="date" value={dateTo}
                   onChange={e => setDateTo(e.target.value)}
                   style={{ ...inp, width: "100%" }} />
          </div>
          <div>
            <label style={lbl}>Zone</label>
            <select value={zoneId}
                    onChange={e => { setZoneId(e.target.value); setLineId(0); }}
                    style={{ ...inp, width: "100%" }}>
              <option value={0}>All zones</option>
              {zones.map(z => (
                <option key={z.id} value={z.id}>
                  {z.zone_name || `Zone ${z.id}`}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label style={lbl}>Line</label>
            <select value={lineId} onChange={e => setLineId(e.target.value)}
                    style={{ ...inp, width: "100%" }}>
              <option value={0}>All lines</option>
              {filteredLines.map(l => (
                <option key={l.id} value={l.id}>
                  {l.line_name || "—"}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label style={lbl}>Shift</label>
            <select value={shift} onChange={e => setShift(e.target.value)}
                    style={{ ...inp, width: "100%" }}>
              <option value="">All</option>
              <option value="A">A</option>
              <option value="B">B</option>
              <option value="GAP">GAP</option>
            </select>
          </div>
          <div>
            <label style={lbl}>Machine</label>
            <select value={machineId} onChange={e => setMachineId(e.target.value)}
                    style={{ ...inp, width: "100%" }}>
              <option value="">All</option>
              {machines.map(m => (
                <option key={m.id} value={m.id}>
                  {m.machine_name || `M${m.id}`}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label style={lbl}>Part code (substring)</label>
            <input value={partCode} onChange={e => setPartCode(e.target.value)}
                   placeholder="e.g. 0761601080131"
                   style={{ ...inp, width: "100%" }} />
          </div>
          <div>
            <label style={lbl}>Search text</label>
            <input value={q} onChange={e => setQ(e.target.value)}
                   placeholder="search in comment / remark"
                   style={{ ...inp, width: "100%" }} />
          </div>
          {/* Cycle-time band — show/export only cycles in this range (seconds). */}
          <div>
            <label style={lbl}>Cycle time from (s)</label>
            <input value={ctMin} onChange={e => setCtMin(e.target.value)}
                   type="number" min="0" step="0.1" placeholder="e.g. 15"
                   style={{ ...inp, width: "100%" }} />
          </div>
          <div>
            <label style={lbl}>Cycle time to (s)</label>
            <input value={ctMax} onChange={e => setCtMax(e.target.value)}
                   type="number" min="0" step="0.1" placeholder="e.g. 30"
                   style={{ ...inp, width: "100%" }} />
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={() => { refresh(); if (tab === "pareto") refreshPareto(); }}
                  disabled={loading} style={btnPri}>
            {loading ? "Loading…" : "Apply filters"}
          </button>
          <button onClick={exportXlsx} disabled={!displayRows.length || !!exporting}
                  style={{ ...btnSec, opacity: exporting && exporting !== "xlsx" ? 0.6 : 1 }}
                  title="Excel workbook — Comments, Pareto and Summary sheets, both charts">
            {exporting === "xlsx" ? "Preparing…" : `⤓ Excel (${displayRows.length})`}
          </button>
          <button onClick={exportPdf} disabled={!displayRows.length || !!exporting}
                  style={{ ...btnSec, background: "#dc2626",
                           opacity: exporting && exporting !== "pdf" ? 0.6 : 1 }}
                  title="PDF report — summary, both charts, Pareto and the comment table">
            {exporting === "pdf" ? "Preparing…" : `⤓ PDF (${displayRows.length})`}
          </button>
          <button
            onClick={() => {
              setDateFrom(todayISO()); setDateTo(todayISO());
              setLineId(0); setShift(""); setPartCode("");
              setMachineId(""); setQ(""); setCtMin(""); setCtMax("");
            }}
            style={{ ...btnPri, background: "#94a3b8" }}>
            Reset
          </button>
        </div>
        {exportMsg && (
          <div style={{ marginTop: 8, fontSize: 12.5, color: "#b91c1c" }}>{exportMsg}</div>
        )}
      </div>

      {/* ── Module tabs (Comments | Pareto) ───────────────── */}
      {(canComments || canPareto) && (
        <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
          {canComments && (
            <button onClick={() => setTab("comments")} style={tabBtn(tab === "comments")}>
              Comments
            </button>
          )}
          {canPareto && (
            <button onClick={() => setTab("pareto")} style={tabBtn(tab === "pareto")}>
              📊 Pareto
            </button>
          )}
        </div>
      )}

      {tab === "comments" && canComments && (
      <>
      {/* ── Stats strip ────────────────────────────────── */}
      <div style={{ ...card, display: "flex", gap: 24, padding: "10px 14px", flexWrap: "wrap" }}>
        <Stat label="Total entries" value={stats.tot} color="#0f172a" />
        <Stat label="Per-cycle comments" value={stats.cmt} color="#2563eb" />
        <Stat label="NG remarks"        value={stats.ng}  color="#ef4444" />
        {/* Production context for the same window: how many parts ran, how many
            were over target, and what share of them carry a comment. */}
        {summary && (
          <>
            <div style={{ width: 1, alignSelf: "stretch", background: "#e2e8f0" }} />
            <Stat label="Total parts" value={(summary.total_parts || 0).toLocaleString("en-IN")} color="#0f172a" />
            <Stat label={`Over target (${summary.over_target_pct ?? 0}%)`}
                  value={(summary.over_target || 0).toLocaleString("en-IN")} color="#b45309" />
            <Stat label={`Commented (${summary.commented_pct ?? 0}%)`}
                  value={(summary.comments || 0).toLocaleString("en-IN")} color="#2563eb" />
          </>
        )}
      </div>

      {/* ── Cycle-time distribution ────────────────────── */}
      {ctDist && (
        <div style={{ ...card, padding: "12px 14px" }}>
          <div style={{ display: "flex", justifyContent: "space-between",
                        alignItems: "baseline", flexWrap: "wrap", gap: 8, marginBottom: 10 }}>
            <div style={{ fontSize: 13, fontWeight: 800, color: "#0f172a" }}>
              Cycle Time Distribution
              <span style={{ fontWeight: 500, color: "#64748b", marginLeft: 8 }}>
                {ctDist.count.toLocaleString("en-IN")} cycles · min {ctDist.min}s · avg {ctDist.avg}s · max {ctDist.max}s
              </span>
            </div>
            <div style={{ fontSize: 11.5, color: "#64748b" }}>
              Click a bar to filter that band, then Export
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 130 }}>
            {ctDist.buckets.map((b, i) => (
              <div key={i}
                   onClick={() => { setCtMin(String(b.from)); setCtMax(String(b.to)); }}
                   title={b.overflow
                     ? `Over ${b.from}s (up to ${b.to}s): ${b.n} cycles — stopped / idle outliers (click to filter)`
                     : `${b.from}s – ${b.to}s : ${b.n} cycles (click to filter)`}
                   style={{ flex: 1, display: "flex", flexDirection: "column",
                            alignItems: "center", justifyContent: "flex-end",
                            height: "100%", cursor: "pointer" }}>
                <div style={{ fontSize: 10, color: "#64748b", marginBottom: 2 }}>{b.n || ""}</div>
                <div style={{ width: "100%",
                              height: `${Math.round((b.n / ctDist.peak) * 100)}%`,
                              minHeight: b.n ? 2 : 0,
                              background: b.overflow ? "#f59e0b" : "#2563eb",
                              borderRadius: "3px 3px 0 0" }} />
                <div style={{ fontSize: 9.5, color: "#94a3b8", marginTop: 4,
                              whiteSpace: "nowrap" }}>{b.overflow ? `>${b.from}` : b.from}</div>
              </div>
            ))}
          </div>
          {(ctMin !== "" || ctMax !== "") && (
            <div style={{ marginTop: 8, fontSize: 12, color: "#334155" }}>
              Band filter active: <b>{ctMin || "0"}s – {ctMax || "∞"}s</b>
              {" · "}{displayRows.length.toLocaleString("en-IN")} rows
              <button onClick={() => { setCtMin(""); setCtMax(""); }}
                      style={{ marginLeft: 10, border: "1px solid #cbd5e1", background: "#fff",
                               borderRadius: 6, padding: "2px 8px", fontSize: 11.5, cursor: "pointer" }}>
                Clear band
              </button>
            </div>
          )}
        </div>
      )}

      {/* ── Error banner ───────────────────────────────── */}
      {error && (
        <div style={{ ...card, background: "#fef2f2", borderLeft: "4px solid #ef4444",
                      color: "#7f1d1d", fontSize: 13 }}>
          {error}
        </div>
      )}

      {/* ── Results table ──────────────────────────────── */}
      <div style={{ ...card, padding: 0, overflow: "hidden" }}>
        <div style={{ maxHeight: "calc(100vh - 360px)", overflow: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead style={{ background: "#f1f5f9", position: "sticky", top: 0 }}>
              <tr>
                <th style={th()}>Date</th>
                <th style={th()} title="When the video was captured — the cycle's own time">Time</th>
                <th style={th()}>Line</th>
                <th style={th()}>Shift</th>
                <th style={th()}>Machine</th>
                <th style={th()}>Part code</th>
                <th style={th()}>Cycle Time</th>
                <th style={th()}>Comment / Remark</th>
                <th style={th()}>Author</th>
                <th style={th()}>Leader</th>
                <th style={th()}>Video</th>
              </tr>
            </thead>
            <tbody>
              {displayRows.length === 0 && !loading && (
                <tr><td colSpan={11} style={{ padding: 24, textAlign: "center",
                                              color: "#64748b" }}>
                  {error ? "Error fetching data — see banner above"
                         : "No comments found for the selected filters."}
                </td></tr>
              )}
              {displayRows.map((r, i) => {
                const isNg = r.type === "NG-Remark";
                return (
                  <tr key={`${r.type}_${r.id}_${i}`}
                      style={{ borderBottom: "1px solid #e2e8f0",
                               background: isNg ? "#fef2f2" : "#fff" }}>
                    {(() => {
                      const cap = captureOf(r);
                      const tip = (cap.fromLog ? "Video captured " + fmtDate(cap.date) + " "
                                    + fmtTime(cap.time)
                                  : "This cycle is not in the cycle log, so its capture "
                                    + "time is unknown")
                                  + (r.ts_local ? " · comment added " + commentedAt(r) : "");
                      return (<>
                        <td style={{ ...td(), whiteSpace: "nowrap" }} title={tip}>
                          {fmtDate(cap.date) || "—"}
                        </td>
                        <td style={{ ...td(), whiteSpace: "nowrap" }} title={tip}>
                          {cap.time ? fmtTime(cap.time)
                                    : <span style={{ color: "#94a3b8" }}>—</span>}
                        </td>
                      </>);
                    })()}
                    <td style={td()}>{lineNameById.get(r.line_id) || r.line_id}</td>
                    <td style={td()}>{r.shift_name || "—"}</td>
                    <td style={td()}>{r.machine_name || (r.machine_id
                                       ? machineNameById.get(r.machine_id) : "—")}</td>
                    <td style={{ ...td(), fontFamily: "monospace", fontSize: 12 }}>
                      {r.part_code || "—"}
                    </td>
                    <td style={{ ...td(), whiteSpace: "nowrap", fontWeight: 600 }}>
                      {r.cycle_time != null ? `${r.cycle_time}s` : "—"}
                    </td>
                    <td style={{ ...td(), maxWidth: 400, wordBreak: "break-word",
                                 whiteSpace: "pre-wrap" }}>
                      {r.text}
                    </td>
                    <td style={td()}>{r.author || "—"}</td>
                    <td style={td()}>{r.leader_name || "—"}</td>
                    <td style={{ ...td(), textAlign: "center", whiteSpace: "nowrap" }}>
                      {(() => {
                        const url   = videoUrlFor(r);
                        const today = partDate(r.part_code) === todayLocal();
                        if (url && today) return (
                          <button onClick={() => setVideoRow({ ...r, _url: url })}
                                  style={{ padding: "3px 12px", fontSize: 12, fontWeight: 700,
                                           background: "#2563eb", color: "#fff", border: "none",
                                           borderRadius: 5, cursor: "pointer" }}>▶ Play</button>
                        );
                        return <span style={{ color: "#94a3b8" }}>—</span>;
                      })()}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      </>
      )}

      {/* ── Pareto module ─────────────────────────────────── */}
      {tab === "pareto" && canPareto && (
        <>
          <ParetoPanel data={pareto} loading={pLoading} card={card} scope={paretoScope}
                       onExport={exportParetoXlsx} exporting={exporting === "pareto"}
                       busy={!!exporting} />
        </>
      )}

      {/* 2026-06-09 — inline cycle-video popup (current-date rows only) */}
      {videoRow && (
        <div onClick={() => setVideoRow(null)}
             style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.7)",
                      zIndex: 9999, display: "flex", alignItems: "center",
                      justifyContent: "center", padding: 24 }}>
          <div onClick={e => e.stopPropagation()}
               style={{ background: "#0b1220", borderRadius: 10, padding: 14,
                        width: "min(760px, 94vw)", boxShadow: "0 20px 60px rgba(0,0,0,.6)" }}>
            <div style={{ display: "flex", justifyContent: "space-between",
                          alignItems: "center", marginBottom: 8, color: "#e2e8f0" }}>
              <span style={{ fontSize: 13, fontWeight: 700 }}>
                {videoRow.machine_name || "Final Inspection"} · {videoRow.part_code}
              </span>
              <button onClick={() => setVideoRow(null)}
                      style={{ background: "none", border: "none", color: "#94a3b8",
                               fontSize: 22, cursor: "pointer", lineHeight: 1 }}>×</button>
            </div>
            <video ref={vidRef} src={videoRow._url} autoPlay
                   style={{ width: "100%", borderRadius: 6, background: "#000",
                            maxHeight: "70vh" }} />
            {/* always-visible progress + time (native strip fades out) */}
            <VideoProgressBar videoRef={vidRef} />
            <div style={{ fontSize: 10, color: "#64748b", marginTop: 6 }}>
              Video sirf aaj ki date ke cycles ke liye available hota hai.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, color }) {
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <span style={{ fontSize: 10, color: "#64748b", textTransform: "uppercase",
                     letterSpacing: ".05em", fontWeight: 700 }}>{label}</span>
      <span style={{ fontSize: 24, fontWeight: 800, color }}>{value}</span>
    </div>
  );
}

function th() {
  return { padding: "8px 12px", textAlign: "left", fontSize: 11, fontWeight: 700,
           color: "#475569", textTransform: "uppercase", letterSpacing: ".05em",
           borderBottom: "1px solid #cbd5e1" };
}
function td() {
  return { padding: "8px 12px", color: "#0f172a", verticalAlign: "top" };
}

// Tab / toggle button style (active = filled blue).
function tabBtn(active) {
  return {
    padding: "8px 20px", borderRadius: 8, border: "1px solid",
    borderColor: active ? "#2563eb" : "#cbd5e1",
    background: active ? "#2563eb" : "#fff",
    color: active ? "#fff" : "#334155",
    fontSize: 13, fontWeight: 700, cursor: "pointer",
  };
}

// ── Pareto module ─────────────────────────────────────────────────────────
// COMMENT-BASED Pareto: which PROBLEM (comment / NG-remark text) repeats most,
// as classic vertical bars (count, descending) + a cumulative % line, so the
// MAJOR vs MINOR problems stand out. Scope = the page filters (a machine, line
// or zone; date; shift). The "vital few" (bars up to the 80% line) are dark blue.
function ParetoPanel({ data, loading, card, scope, onExport, exporting, busy }) {
  const all   = (data && data.items) || [];
  const total = (data && data.total) || 0;

  // Top 12 problems as bars; the rest lumped into "Others" so the chart stays
  // readable while the cumulative line still reaches 100%.
  const TOPN = 12;
  let bars = all.slice(0, TOPN).map(x => ({ ...x }));
  if (all.length > TOPN) {
    const rest = all.slice(TOPN).reduce((s, x) => s + x.count, 0);
    if (rest > 0) bars.push({ name: "Others", count: rest, _others: true });
  }
  let run = 0;
  bars = bars.map(b => {
    run += b.count;
    return { ...b, cum: total ? +(run * 100 / total).toFixed(1) : 0 };
  });
  const maxCount = bars.reduce((m, b) => Math.max(m, b.count), 0) || 1;

  // SVG geometry (vertical bars + cumulative line).
  const W = 940, H = 360, padL = 44, padR = 46, padT = 16, padB = 110;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const n = bars.length || 1;
  const slot = plotW / n;
  const bw = Math.min(46, slot * 0.62);
  const xC = i => padL + slot * i + slot / 2;
  const yCount = c => padT + plotH * (1 - c / maxCount);
  const yPct   = p => padT + plotH * (1 - p / 100);
  const trunc  = (s, k = 16) => (s.length > k ? s.slice(0, k - 1) + "…" : s);
  const cumPts = bars.map((b, i) => `${xC(i)},${yPct(b.cum)}`).join(" ");

  return (
    <div>
      <div style={{ ...card, display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#64748b",
                        textTransform: "uppercase", letterSpacing: ".05em" }}>Problem Pareto — scope</div>
          <div style={{ fontSize: 15, fontWeight: 800, color: "#0f172a" }}>{scope}</div>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 22 }}>
          <Stat label="Total occurrences" value={total} color="#0f172a" />
          <Stat label="Distinct problems"
                value={(data && data.distinct) != null ? data.distinct : all.length} color="#2563eb" />
          {onExport && (
            <button onClick={onExport} disabled={!all.length || busy}
                    title="The full ranking and its chart as an Excel workbook"
                    style={{ alignSelf: "center", padding: "8px 18px", borderRadius: 6,
                             border: "none", background: "#16a34a", color: "#fff",
                             fontWeight: 700, fontSize: 13, cursor: "pointer",
                             opacity: !all.length || busy ? 0.6 : 1 }}>
              {exporting ? "Preparing…" : "⤓ Excel"}
            </button>
          )}
        </div>
      </div>

      <div style={card}>
        {loading ? (
          <div style={{ padding: 24, textAlign: "center", color: "#64748b" }}>Loading…</div>
        ) : bars.length === 0 ? (
          <div style={{ padding: 24, textAlign: "center", color: "#64748b" }}>
            No comments / NG remarks recorded for the selected filters.
          </div>
        ) : (
          <>
          <div style={{ overflowX: "auto" }}>
            <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", minWidth: 640, display: "block" }}>
              {[0, .25, .5, .75, 1].map((f, i) => {
                const y = padT + plotH * (1 - f);
                return (
                  <g key={i}>
                    <line x1={padL} y1={y} x2={W - padR} y2={y} stroke="#eef2f7" />
                    <text x={padL - 6} y={y + 3} fontSize="9" fill="#94a3b8" textAnchor="end">
                      {Math.round(maxCount * f)}</text>
                    <text x={W - padR + 6} y={y + 3} fontSize="9" fill="#94a3b8" textAnchor="start">
                      {Math.round(100 * f)}%</text>
                  </g>
                );
              })}
              {/* 80% reference line */}
              <line x1={padL} y1={yPct(80)} x2={W - padR} y2={yPct(80)}
                    stroke="#f59e0b" strokeDasharray="4 3" />
              <text x={W - padR} y={yPct(80) - 4} fontSize="9" fill="#d97706" textAnchor="end">80%</text>
              {/* bars */}
              {bars.map((b, i) => {
                const vital = (i === 0 ? 0 : bars[i - 1].cum) < 80;
                return (
                  <g key={i}>
                    <rect x={xC(i) - bw / 2} y={yCount(b.count)} width={bw}
                          height={padT + plotH - yCount(b.count)} rx="2"
                          fill={b._others ? "#cbd5e1" : (vital ? "#2563eb" : "#93c5fd")} />
                    <text x={xC(i)} y={yCount(b.count) - 4} fontSize="9" fill="#334155"
                          textAnchor="middle" fontWeight="700">{b.count}</text>
                    <text x={xC(i)} y={padT + plotH + 12} fontSize="9" fill="#475569"
                          textAnchor="end"
                          transform={`rotate(-40 ${xC(i)} ${padT + plotH + 12})`}>{trunc(b.name)}</text>
                  </g>
                );
              })}
              {/* cumulative % line */}
              <polyline points={cumPts} fill="none" stroke="#ef4444" strokeWidth="2" />
              {bars.map((b, i) => (
                <circle key={i} cx={xC(i)} cy={yPct(b.cum)} r="3" fill="#ef4444" />
              ))}
            </svg>
          </div>

          {/* full detail table (every problem, not just the top bars) */}
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, marginTop: 8 }}>
            <thead>
              <tr>
                <th style={pth(40)}>#</th>
                <th style={pth()}>Problem (comment / remark)</th>
                <th style={{ ...pth(70), textAlign: "right" }}>Count</th>
                <th style={{ ...pth(60), textAlign: "right" }}>%</th>
                <th style={{ ...pth(80), textAlign: "right" }}>Cum %</th>
              </tr>
            </thead>
            <tbody>
              {all.map((it, i) => {
                const vital = (i === 0 ? 0 : all[i - 1].cum_pct) < 80;
                return (
                  <tr key={it.name + i} style={{ borderBottom: "1px solid #eef2f7",
                                                 background: vital ? "#f0f7ff" : "#fff" }}>
                    <td style={{ ...ptd(), color: "#94a3b8", fontWeight: 700 }}>{i + 1}</td>
                    <td style={{ ...ptd(), fontWeight: 600 }}>{it.name}</td>
                    <td style={{ ...ptd(), textAlign: "right", fontWeight: 700 }}>{it.count}</td>
                    <td style={{ ...ptd(), textAlign: "right", color: "#64748b" }}>{it.pct}%</td>
                    <td style={{ ...ptd(), textAlign: "right", fontWeight: 700,
                                 color: vital ? "#16a34a" : "#334155" }}>{it.cum_pct}%</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div style={{ fontSize: 11, color: "#64748b", marginTop: 8 }}>
            Dark-blue bars + the highlighted rows (up to the 80% line) are the <b>vital few</b> —
            the major problems to tackle first. Red line = cumulative %.
          </div>
          </>
        )}
      </div>
    </div>
  );
}
function pth(w) {
  return { padding: "8px 10px", textAlign: "left", fontSize: 11, fontWeight: 700,
           color: "#475569", textTransform: "uppercase", letterSpacing: ".05em",
           borderBottom: "1px solid #cbd5e1", width: w };
}
function ptd() {
  return { padding: "7px 10px", color: "#0f172a", verticalAlign: "middle" };
}
