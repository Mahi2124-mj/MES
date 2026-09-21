/*
 * WaitingTime.jsx — how long the UI actually made people wait.
 *
 * Admin-only. Reads mes_ui_timing, which the app fills as people use it:
 *   page  — route change to first paint
 *   video — click on a cycle dot to the first frame playing
 *
 * Both are measured in the browser, so these are real waits, not server timings.
 * The point of the page is to answer "is it slow, where, and is it getting
 * better" with numbers instead of opinions — so p95 is shown next to the
 * average everywhere (an average hides the bad clicks that people complain
 * about), and the source split shows whether the clip archive is doing its job.
 */

import { useEffect, useState, useMemo } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const C = {
  bg: "#0b1220", card: "#111c30", line: "#1e2a44",
  text: "#e2e8f0", mut: "#93a4bd",
  page: "#38bdf8", video: "#f59e0b",
  good: "#22c55e", warn: "#f59e0b", bad: "#ef4444",
};

const ms = (v) => {
  const n = Number(v);
  if (!isFinite(n)) return "—";
  return n < 1000 ? `${Math.round(n)} ms` : `${(n / 1000).toFixed(2)} s`;
};
// Same thresholds everywhere so a colour always means the same thing.
const grade = (v) => (v < 1000 ? C.good : v < 3000 ? C.warn : C.bad);

function Card({ title, sub, children, span = 1 }) {
  return (
    <div style={{
      background: C.card, border: `1px solid ${C.line}`, borderRadius: 14,
      padding: "14px 16px", gridColumn: `span ${span}`, minWidth: 0,
    }}>
      <div style={{ fontSize: 12, fontWeight: 800, letterSpacing: ".08em",
                    textTransform: "uppercase", color: C.mut, marginBottom: 2 }}>
        {title}
      </div>
      {sub && <div style={{ fontSize: 11, color: C.mut, marginBottom: 10, opacity: .8 }}>{sub}</div>}
      {children}
    </div>
  );
}

function Summary({ label, colour, d }) {
  const avg = Number(d?.avg_ms || 0);
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 14, flexWrap: "wrap" }}>
      <div style={{ fontSize: 30, fontWeight: 900, color: grade(avg), fontFamily: "monospace" }}>
        {ms(avg)}
      </div>
      <div style={{ fontSize: 11, color: C.mut, lineHeight: 1.7 }}>
        <div>median <b style={{ color: C.text }}>{ms(d?.p50)}</b>
             {"  ·  "}p95 <b style={{ color: C.text }}>{ms(d?.p95)}</b></div>
        <div>worst <b style={{ color: C.text }}>{ms(d?.max_ms)}</b>
             {"  ·  "}{Number(d?.samples || 0).toLocaleString()} samples</div>
      </div>
      <div style={{ marginLeft: "auto", fontSize: 11, fontWeight: 800,
                    color: colour, letterSpacing: ".08em" }}>{label}</div>
    </div>
  );
}

/** Horizontal bars — readable at a glance and no chart library needed. */
function Bars({ rows, colour, valueKey = "avg_ms", labelKey = "name" }) {
  const max = Math.max(1, ...rows.map(r => Number(r[valueKey]) || 0));
  if (!rows.length) return <div style={{ color: C.mut, fontSize: 12 }}>No samples yet.</div>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
      {rows.map((r, i) => {
        const v = Number(r[valueKey]) || 0;
        return (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ width: "42%", fontSize: 11, color: C.text,
                          whiteSpace: "nowrap", overflow: "hidden",
                          textOverflow: "ellipsis" }} title={r[labelKey]}>
              {r[labelKey]}
            </div>
            <div style={{ flex: 1, height: 16, background: "rgba(255,255,255,.06)",
                          borderRadius: 4, overflow: "hidden" }}>
              <div style={{ width: `${(v / max) * 100}%`, height: "100%",
                            background: colour, opacity: .85 }} />
            </div>
            <div style={{ width: 76, textAlign: "right", fontSize: 11,
                          fontFamily: "monospace", color: grade(v) }}>{ms(v)}</div>
            <div style={{ width: 42, textAlign: "right", fontSize: 10, color: C.mut }}>
              n={r.samples}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Day-by-day average, page vs video, as a simple SVG line chart. */
function Trend({ rows }) {
  const { days, series } = useMemo(() => {
    const days = [...new Set(rows.map(r => String(r.day)))].sort();
    const pick = (k) => days.map(d => {
      const hit = rows.find(r => String(r.day) === d && r.kind === k);
      return hit ? Number(hit.avg_ms) : null;
    });
    return { days, series: { page: pick("page"), video: pick("video") } };
  }, [rows]);

  if (days.length < 2) {
    return <div style={{ color: C.mut, fontSize: 12 }}>
      Need at least two days of samples to draw a trend.
    </div>;
  }
  const W = 760, H = 170, PAD = 34;
  const all = [...series.page, ...series.video].filter(v => v != null);
  const max = Math.max(1000, ...all);
  const x = (i) => PAD + (i * (W - PAD - 10)) / Math.max(1, days.length - 1);
  const y = (v) => H - 22 - (v / max) * (H - 46);
  const path = (arr) => arr.map((v, i) => v == null ? null : `${x(i)},${y(v)}`)
                           .filter(Boolean).join(" ");
  return (
    <div style={{ overflowX: "auto" }}>
      <svg width={W} height={H} style={{ display: "block" }}>
        {[0, .5, 1].map(f => (
          <g key={f}>
            <line x1={PAD} x2={W - 10} y1={y(max * f)} y2={y(max * f)}
                  stroke="rgba(255,255,255,.08)" />
            <text x={2} y={y(max * f) + 4} fill={C.mut} fontSize="9">
              {ms(max * f)}
            </text>
          </g>
        ))}
        {["page", "video"].map(k => (
          <polyline key={k} fill="none" strokeWidth="2"
                    stroke={k === "page" ? C.page : C.video}
                    points={path(series[k])} />
        ))}
        {days.map((d, i) => (
          <text key={d} x={x(i)} y={H - 6} fill={C.mut} fontSize="9"
                textAnchor="middle">{d.slice(5)}</text>
        ))}
      </svg>
      <div style={{ display: "flex", gap: 16, fontSize: 11, color: C.mut, marginTop: 4 }}>
        <span><b style={{ color: C.page }}>—</b> page</span>
        <span><b style={{ color: C.video }}>—</b> video</span>
      </div>
    </div>
  );
}

const BUCKETS = ["0-0.5s", "0.5-1s", "1-2s", "2-3s", "3-5s", "5-10s", "10s+"];

function Histogram({ rows, kind, colour }) {
  const data = BUCKETS.map(b => {
    const hit = rows.find(r => r.kind === kind && r.bucket === b);
    return { bucket: b, samples: hit ? Number(hit.samples) : 0 };
  });
  const max = Math.max(1, ...data.map(d => d.samples));
  const total = data.reduce((a, d) => a + d.samples, 0) || 1;
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 6, height: 130 }}>
      {data.map(d => (
        <div key={d.bucket} style={{ flex: 1, textAlign: "center", minWidth: 0 }}>
          <div style={{ fontSize: 9, color: C.mut, marginBottom: 3 }}>
            {d.samples ? `${Math.round((d.samples / total) * 100)}%` : ""}
          </div>
          <div style={{ height: `${(d.samples / max) * 84}px`, background: colour,
                        opacity: .85, borderRadius: "3px 3px 0 0", minHeight: d.samples ? 2 : 0 }} />
          <div style={{ fontSize: 9, color: C.mut, marginTop: 4,
                        whiteSpace: "nowrap" }}>{d.bucket}</div>
        </div>
      ))}
    </div>
  );
}

export default function WaitingTime() {
  const { user } = useAuth() || {};
  const token = sessionStorage.getItem("mes_token");
  const [days, setDays] = useState(7);
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => { document.title = "Waiting Time"; }, []);

  useEffect(() => {
    let stop = false;
    setLoading(true); setErr("");
    api.get(`/api/ui-timing/stats?days=${days}`, token)
      .then(r => { if (!stop) { setD(r); setLoading(false); } })
      .catch(e => { if (!stop) { setErr(e.message || "failed"); setLoading(false); } });
    return () => { stop = true; };
  }, [days, token]);

  const isAdmin = (user?.role || "").toLowerCase() === "admin";
  if (user && !isAdmin) {
    return <div style={{ padding: 40, color: C.mut }}>Admin only.</div>;
  }

  return (
    <div className="wt-page" style={{ background: C.bg, minHeight: "100vh", margin: "-16px -24px",
                  padding: "20px 26px 60px", color: C.text,
                  fontFamily: "'Barlow',sans-serif" }}>
      {/* On phones the parent has no side-padding, so the -24px full-bleed margin
          spilled off-screen → kill the horizontal bleed under 600px. */}
      <style>{`@media (max-width:600px){.wt-page{margin-left:0 !important;margin-right:0 !important;padding-left:14px !important;padding-right:14px !important;}}`}</style>
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 6 }}>
        <h2 style={{ margin: 0, fontSize: 20, fontWeight: 900 }}>Waiting Time</h2>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          {[1, 7, 30].map(n => (
            <button key={n} onClick={() => setDays(n)} style={{
              padding: "5px 14px", fontSize: 11, fontWeight: 800, borderRadius: 7,
              cursor: "pointer", border: `1px solid ${C.line}`,
              background: days === n ? C.page : "transparent",
              color: days === n ? "#04121e" : C.mut,
            }}>{n === 1 ? "Today" : `${n} days`}</button>
          ))}
        </div>
      </div>
      <div style={{ fontSize: 12, color: C.mut, marginBottom: 18 }}>
        Measured in the browser — page = route change to first paint, video =
        click on a cycle dot to the first frame playing.
      </div>

      {loading && <div style={{ color: C.mut }}>Loading…</div>}
      {err && <div style={{ color: C.bad }}>Could not load: {err}</div>}

      {d && !loading && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0,1fr))",
                      gap: 14 }}>
          <Card title="Page open" sub="all routes">
            <Summary label="PAGE" colour={C.page} d={d.page_summary} />
          </Card>
          <Card title="Video open" sub="cycle clip → first frame">
            <Summary label="VIDEO" colour={C.video} d={d.video_summary} />
          </Card>

          <Card title="Slowest pages" sub="average, min 3 samples">
            <Bars rows={d.page_slowest || []} colour={C.page} />
          </Card>
          <Card title="Slowest videos" sub="by machine, average">
            <Bars rows={d.video_slowest || []} colour={C.video} />
          </Card>

          <Card title="Trend" sub="daily average" span={2}>
            <Trend rows={d.trend || []} />
          </Card>

          <Card title="Page wait distribution">
            <Histogram rows={d.buckets || []} kind="page" colour={C.page} />
          </Card>
          <Card title="Video wait distribution">
            <Histogram rows={d.buckets || []} kind="video" colour={C.video} />
          </Card>

          <Card title="Where clips came from"
                sub="archive = pre-rendered on disk, render = built on the click">
            <Bars rows={(d.video_sources || []).map(r => ({ ...r, name: r.source }))}
                  colour={C.good} />
          </Card>

          <Card title="Latest samples">
            <div style={{ maxHeight: 240, overflowY: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
                <tbody>
                  {(d.recent || []).map((r, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid rgba(255,255,255,.05)" }}>
                      <td style={{ padding: "3px 6px", color: C.mut, whiteSpace: "nowrap" }}>
                        {String(r.ts).slice(11)}
                      </td>
                      <td style={{ padding: "3px 6px",
                                   color: r.kind === "video" ? C.video : C.page,
                                   fontWeight: 800 }}>{r.kind}</td>
                      <td style={{ padding: "3px 6px", color: C.text, maxWidth: 190,
                                   overflow: "hidden", textOverflow: "ellipsis",
                                   whiteSpace: "nowrap" }} title={r.name}>{r.name}</td>
                      <td style={{ padding: "3px 6px", textAlign: "right",
                                   fontFamily: "monospace",
                                   color: grade(Number(r.ms)) }}>{ms(r.ms)}</td>
                      <td style={{ padding: "3px 6px", color: C.mut }}>{r.source}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
