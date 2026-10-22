// pages/PlantOverview.jsx — Plant Overview for HOD and Plant Head (2026-10-08)
//   Today (live, refreshes every 60 s) next to the last working day (closed),
//   PY bypass included.  Read-only: one call to /api/plant-overview, which
//   reuses the Shift Compile zone summary so both pages show the same numbers.
//   Scope comes from the server: Plant Head = every zone, HOD
//   (production_incharge) = the zones of their assigned lines.
import { useEffect, useState, useCallback, Fragment } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import PageTopbar from "../components/PageTopbar";

const REFRESH_MS = 60000;
const C = { ink: "#0f172a", sub: "#64748b", faint: "#94a3b8", line: "#e5e7eb", card: "#fff", wash: "#f8fafc",
            ok: "#15803d", okBg: "#dcfce7", warn: "#b45309", warnBg: "#fef3c7", bad: "#b91c1c", badBg: "#fee2e2",
            blue: "#1d4ed8", blueBg: "#dbeafe" };
const LOSS_COLOR = { breakdown: "#ef4444", quality: "#f97316", material: "#eab308", setup: "#3b82f6",
                     change_over: "#06b6d4", speed: "#22c55e", others: "#a855f7", not_run: "#64748b" };

const fmt = (v) => (v == null ? "—" : Number(v).toLocaleString("en-IN"));
const pct = (v) => (v == null ? "—" : `${Number(v).toFixed(1)}%`);
const mins = (m) => {
  if (m == null) return "—";
  m = Math.round(Number(m));
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
};
// tile-size duration: hours once it passes 10 h, so the big number never truncates
const bigMins = (m) => (m == null ? "—" : Number(m) >= 600 ? `${Math.round(Number(m) / 60)} h` : mins(m));
const dayLabel = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
};
const timeOf = (iso) => (iso ? new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "");
const lineUrl = (name) => `/${encodeURIComponent(String(name || "").trim().replace(/\s+/g, "-"))}/MANAGEMENT`;
const oeeTone = (v) => (v == null ? C.sub : v >= 85 ? C.ok : v >= 65 ? C.warn : C.bad);

function Delta({ now, before, unit = "", invert = false, digits = 0 }) {
  if (now == null || before == null) return null;
  const d = Number(now) - Number(before);
  if (Math.abs(d) < (digits ? 0.05 : 0.5)) return <span style={{ color: C.faint, fontSize: 12 }}>same as {""}day before</span>;
  const good = invert ? d < 0 : d > 0;
  const txt = `${d > 0 ? "▲" : "▼"} ${digits ? Math.abs(d).toFixed(digits) : Math.round(Math.abs(d)).toLocaleString("en-IN")}${unit}`;
  return <span style={{ color: good ? C.ok : C.bad, fontSize: 12, fontWeight: 700 }}>{txt}</span>;
}

function Tile({ label, today, lwd, lwdName }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.line}`, borderRadius: 14, padding: "12px 14px",
                  display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
      <div style={{ fontSize: 11, fontWeight: 800, letterSpacing: ".08em", textTransform: "uppercase", color: C.sub }}>{label}</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 10.5, color: C.faint, fontWeight: 700 }}>TODAY</div>
          {today}
        </div>
        <div style={{ minWidth: 0, borderLeft: `1px dashed ${C.line}`, paddingLeft: 10 }}>
          <div style={{ fontSize: 10.5, color: C.faint, fontWeight: 700 }}>{lwdName}</div>
          {lwd}
        </div>
      </div>
    </div>
  );
}
const Big = ({ children, color = C.ink }) => (
  <div style={{ fontSize: 24, fontWeight: 800, color, fontVariantNumeric: "tabular-nums", lineHeight: 1.15,
                whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{children}</div>
);
const Small = ({ children }) => <div style={{ fontSize: 12, color: C.sub, marginTop: 2 }}>{children}</div>;

function Chip({ tone = "n", children }) {
  const t = { ok: [C.okBg, C.ok], warn: [C.warnBg, C.warn], bad: [C.badBg, C.bad], blue: [C.blueBg, C.blue], n: ["#f1f5f9", C.sub] }[tone];
  return <span style={{ background: t[0], color: t[1], borderRadius: 999, padding: "2px 9px", fontSize: 11.5,
                        fontWeight: 700, whiteSpace: "nowrap" }}>{children}</span>;
}
const STATUS_CHIP = { running: ["ok", "Running"], stopped: ["bad", "Stopped (planned)"], not_planned: ["n", "Not planned"] };

function Section({ title, right, children, tone }) {
  const border = tone === "bad" ? "#fecaca" : C.line;
  const bg = tone === "bad" ? "#fff7f7" : C.card;
  return (
    <section style={{ background: bg, border: `1px solid ${border}`, borderRadius: 14, padding: "12px 14px", minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800, color: C.ink }}>{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

function AttentionGroup({ title, rows, render, empty, max = 6 }) {
  const [all, setAll] = useState(false);
  if (!rows || !rows.length) return null;
  const shown = all ? rows : rows.slice(0, max);
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 12.5, fontWeight: 800, color: C.ink, marginBottom: 4 }}>{title} <span style={{ color: C.bad }}>({rows.length})</span></div>
      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        {shown.map((r, i) => (
          <div key={i} style={{ fontSize: 12.5, color: C.ink, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>{render(r)}</div>
        ))}
      </div>
      {rows.length > max && (
        <button onClick={() => setAll(!all)} style={{ ...linkBtn, marginTop: 4 }}>{all ? "Show fewer" : `Show all ${rows.length}`}</button>
      )}
      {!rows.length && empty}
    </div>
  );
}
const linkBtn = { background: "none", border: "none", color: C.blue, fontSize: 12, fontWeight: 700, cursor: "pointer", padding: 0 };

function LossBars({ today, lwd, labels, lwdName }) {
  const keys = [...new Set([...Object.keys(today || {}), ...Object.keys(lwd || {})])]
    .sort((a, b) => (Number(lwd?.[b] || 0) + Number(today?.[b] || 0)) - (Number(lwd?.[a] || 0) + Number(today?.[a] || 0)));
  const max = Math.max(1, ...keys.map(k => Math.max(Number(today?.[k] || 0), Number(lwd?.[k] || 0))));
  if (!keys.length) return <div style={{ color: C.sub, fontSize: 13 }}>No losses recorded.</div>;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(110px,auto) 1fr", gap: "8px 12px", alignItems: "center" }}>
      {keys.map(k => (
        <Fragment key={k}>
          <div style={{ fontSize: 12.5, color: C.ink, display: "flex", alignItems: "center", gap: 6 }}>
            <i style={{ width: 9, height: 9, borderRadius: 2, background: LOSS_COLOR[k] || C.sub, display: "inline-block" }} />
            {labels?.[k] || k}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            {[["Today", today?.[k]], [lwdName, lwd?.[k]]].map(([n, v]) => (
              <div key={n} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <div style={{ flex: 1, height: 8, background: "#f1f5f9", borderRadius: 4, overflow: "hidden" }}>
                  <div style={{ width: `${(Number(v || 0) / max) * 100}%`, height: "100%", background: LOSS_COLOR[k] || C.sub,
                                opacity: n === "Today" ? 1 : 0.45, borderRadius: 4 }} />
                </div>
                <span style={{ fontSize: 11.5, color: C.sub, minWidth: 128, fontVariantNumeric: "tabular-nums" }}>{n}: {mins(v || 0)}</span>
              </div>
            ))}
          </div>
        </Fragment>
      ))}
    </div>
  );
}

function ZoneTable({ block }) {
  const [open, setOpen] = useState({});
  if (!block) return <div style={{ color: C.sub, fontSize: 13 }}>No data.</div>;
  const labels = block.loss_labels || {};
  const th = { textAlign: "left", fontSize: 11, fontWeight: 800, letterSpacing: ".05em", textTransform: "uppercase",
               color: C.sub, padding: "8px 10px", borderBottom: `1px solid ${C.line}`, whiteSpace: "nowrap", background: C.wash };
  const td = { padding: "8px 10px", borderBottom: `1px solid ${C.line}`, fontSize: 13, whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" };
  const ach = (a, p) => (p ? (a * 100) / p : null);
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ borderCollapse: "collapse", width: "100%", minWidth: 760 }}>
        <thead><tr>
          {["Zone / line", "Lines", "Plan", "OK", "NG", "% of plan", "OEE", "Biggest loss"].map(h => <th key={h} style={th}>{h}</th>)}
        </tr></thead>
        <tbody>
          {block.zones.map(z => {
            const isOpen = !!open[z.zone_name];
            const top = z.top_losses?.[0];
            return (
              <Fragment key={z.zone_name}>
                <tr onClick={() => setOpen(o => ({ ...o, [z.zone_name]: !isOpen }))} style={{ cursor: "pointer", background: isOpen ? "#f8fafc" : "#fff" }}>
                  <td style={{ ...td, fontWeight: 800 }}>{isOpen ? "▾" : "▸"} {z.zone_name}</td>
                  <td style={td}>
                    <span style={{ color: C.ok, fontWeight: 700 }}>{z.running} run</span>
                    {z.stopped ? <span style={{ color: C.bad, fontWeight: 700 }}> · {z.stopped} stopped</span> : null}
                    {z.not_planned ? <span style={{ color: C.sub }}> · {z.not_planned} not planned</span> : null}
                  </td>
                  <td style={td}>{fmt(z.plan)}</td>
                  <td style={{ ...td, fontWeight: 700 }}>{fmt(z.ok)}</td>
                  <td style={{ ...td, color: z.ng ? C.bad : C.sub }}>{fmt(z.ng)}</td>
                  <td style={td}>{pct(ach(z.actual, z.plan))}</td>
                  <td style={{ ...td, color: oeeTone(z.oee), fontWeight: 700 }}>
                    {pct(z.oee)} {z.no_loss_data ? <Chip>no loss data</Chip> : null}
                  </td>
                  <td style={td}>{top ? `${top.label} · ${mins(top.minutes)}` : "—"}</td>
                </tr>
                {isOpen && z.lines.map(l => {
                  const [tone, txt] = STATUS_CHIP[l.status] || ["n", l.status];
                  return (
                    <tr key={l.line_id} style={{ background: "#fbfdff" }}>
                      <td style={{ ...td, paddingLeft: 28 }}>
                        <a href={lineUrl(l.line_name)} target="_blank" rel="noreferrer" style={{ color: C.blue, fontWeight: 700, textDecoration: "none" }}
                           title="Open this line's MANAGEMENT dashboard">{l.line_name} ↗</a>
                      </td>
                      <td style={td}><Chip tone={tone}>{txt}</Chip>{l.note ? <span style={{ color: C.faint, fontSize: 11.5 }}> {l.note}</span> : null}</td>
                      <td style={td}>{fmt(l.plan)}</td>
                      <td style={td}>{fmt(l.ok)}</td>
                      <td style={{ ...td, color: l.ng ? C.bad : C.sub }}>{fmt(l.ng)}</td>
                      <td style={td}>{pct(ach(l.actual, l.plan))}</td>
                      <td style={{ ...td, color: oeeTone(l.oee) }}>{pct(l.oee)}</td>
                      <td style={td}>{l.top_loss ? `${labels[l.top_loss.key] || l.top_loss.key} · ${mins(l.top_loss.minutes)}` : "—"}</td>
                    </tr>
                  );
                })}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function PlantOverview() {
  const { token } = useAuth();
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState("today");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await api.get("/api/plant-overview", token);
      setData(d); setErr("");
    } catch (e) {
      setErr(e.message || "Could not load the Plant Overview");
    } finally { setLoading(false); }
  }, [token]);

  useEffect(() => {
    load();
    const t = setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
    const vis = () => { if (!document.hidden) load(); };
    document.addEventListener("visibilitychange", vis);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, [load]);

  const T = data?.today, L = data?.last_working_day, P = data?.prev_totals, A = data?.attention;
  const lwdName = L ? dayLabel(L.date).toUpperCase() : "LAST WORKING DAY";
  const tt = T?.totals || {}, lt = L?.totals || {};
  const py = A?.py_now;
  const attentionCount = (A?.andon_open?.length || 0) + (py?.waiting || 0) + (A?.sensor_faults?.length || 0)
    + (A?.stopped_lines?.length || 0) + (A?.shift_pending?.length || 0) + (A?.escalations?.length || 0);
  const cal = data?.today_calendar;

  return (
    <div style={{ minHeight: "100vh", background: "#f1f5f9" }}>
      <PageTopbar leading="Plant" accent="Overview" />
      <div style={{ padding: "16px 22px 60px", color: C.ink, maxWidth: 1600, margin: "0 auto" }}>

        {/* header line */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
          <Chip tone="blue">Today · {dayLabel(data?.production_day)}</Chip>
          <Chip>Last working day · {L ? dayLabel(L.date) : "—"}</Chip>
          {cal && !cal.working && <Chip tone="warn">Today is {cal.label.toLowerCase()}{cal.name ? ` · ${cal.name}` : ""}</Chip>}
          <span style={{ fontSize: 12.5, color: C.sub }}>
            {data ? (data.scope.role === "admin" || data.scope.role === "plant_head"
              ? `All zones · ${data.scope.lines} lines`
              : `Your zones: ${data.scope.zones.join(", ")} · ${data.scope.lines} lines`) : ""}
          </span>
          <span style={{ marginLeft: "auto", fontSize: 12, color: C.faint }}>
            {loading ? "Refreshing…" : data ? `Updated ${timeOf(data.generated_at)} · refreshes every minute` : ""}
          </span>
          <button onClick={load} style={{ border: `1px solid ${C.line}`, background: "#fff", borderRadius: 8, padding: "6px 12px",
                                          fontWeight: 700, fontSize: 12.5, cursor: "pointer" }}>↻ Refresh</button>
        </div>

        {err && <div style={{ color: C.bad, background: C.badBg, border: "1px solid #fecaca", borderRadius: 10,
                              padding: "10px 14px", marginBottom: 14, fontSize: 13 }}>{err}</div>}
        {data?.errors?.length > 0 && (
          <div style={{ color: C.warn, background: C.warnBg, border: "1px solid #fde68a", borderRadius: 10,
                        padding: "8px 14px", marginBottom: 14, fontSize: 12.5 }}>
            Some figures could not be read and are left out: {data.errors.map(e => e.split(":")[0]).join(", ")}.
          </div>
        )}
        {!data && !err && <div style={{ color: C.sub, padding: 40, textAlign: "center" }}>Loading the plant…</div>}

        {data && (
          <>
            {/* tiles */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 12, marginBottom: 14 }}>
              <Tile label="Production (OK)" lwdName={lwdName}
                today={<><Big>{fmt(tt.ok)}</Big><Small>of {fmt(tt.plan)} plan · {pct(tt.pct)} · NG {fmt(tt.ng)}</Small></>}
                lwd={<><Big>{fmt(lt.ok)}</Big><Small>of {fmt(lt.plan)} plan · {pct(lt.pct)}</Small><Delta now={lt.ok} before={P?.ok} /></>} />
              <Tile label="OEE" lwdName={lwdName}
                today={<><Big color={oeeTone(tt.oee)}>{pct(tt.oee)}</Big><Small>lines that ran</Small></>}
                lwd={<><Big color={oeeTone(lt.oee)}>{pct(lt.oee)}</Big><Delta now={lt.oee} before={P?.oee} unit=" pts" digits={1} /></>} />
              <Tile label="Lines" lwdName={lwdName}
                today={<><Big>{tt.running ?? "—"} <span style={{ fontSize: 14, color: C.sub }}>/ {tt.lines_total ?? "—"} running</span></Big>
                        <Small>{tt.stopped ? <b style={{ color: C.bad }}>{tt.stopped} stopped though planned</b> : "none stopped"}{tt.not_planned ? ` · ${tt.not_planned} not planned` : ""}</Small></>}
                lwd={<><Big>{lt.running ?? "—"} <span style={{ fontSize: 14, color: C.sub }}>/ {lt.lines_total ?? "—"} ran</span></Big>
                      <Small>{lt.stopped ? `${lt.stopped} planned, not run` : "all planned lines ran"}</Small></>} />
              <Tile label="Losses" lwdName={lwdName}
                today={<><Big>{bigMins(tt.loss_minutes)}</Big><Small>all lines, all kinds</Small></>}
                lwd={<><Big>{bigMins(lt.loss_minutes)}</Big><Small>all lines, all kinds</Small><Delta now={lt.loss_minutes} before={P?.loss_minutes} unit=" min" invert /></>} />
              <Tile label="Breakdowns (andon)" lwdName={lwdName}
                today={<><Big color={A?.andon_open?.length ? C.bad : C.ink}>{T?.breakdowns ? T.breakdowns.count : "—"}</Big>
                        <Small>calls · {T?.breakdowns ? mins(T.breakdowns.minutes) : "—"} · {A?.andon_open?.length || 0} andon calls open now</Small></>}
                lwd={<><Big>{L?.breakdowns ? L.breakdowns.count : "—"}</Big><Small>calls · {L?.breakdowns ? mins(L.breakdowns.minutes) : "—"} in total</Small></>} />
              <Tile label="PY bypass" lwdName={lwdName}
                today={<><Big color={py?.open?.length ? C.bad : C.ink}>{py?.open?.length ?? "—"} <span style={{ fontSize: 14, color: C.sub }}>open now</span></Big>
                        <Small>{T?.py ? `${fmt(T.py.raised)} raised today` : ""}{py?.waiting ? ` · ${py.waiting} waiting for approval` : ""}</Small></>}
                lwd={<><Big>{L?.py ? fmt(L.py.raised) : "—"} <span style={{ fontSize: 14, color: C.sub }}>raised</span></Big>
                      <Small>{L?.py ? `${fmt(L.py.cleared)} cleared on their own · ${fmt(L.py.approved)} approved · ${fmt(L.py.rejected)} rejected` : ""}</Small></>} />
              <Tile label="Approvals and shift close" lwdName={lwdName}
                today={<><Big color={T?.deviations?.pending ? C.warn : C.ink}>{T?.deviations ? T.deviations.pending : "—"}</Big>
                        <Small>deviations pending · {A?.shift_pending?.length ? `${A.shift_pending.length} shifts past close, not closed` : "no shift waiting to close"}</Small></>}
                lwd={<><Big>{L?.shift_close ? `${L.shift_close.closed_ontime + L.shift_close.closed_late} / ${L.shift_close.total}` : "—"}</Big>
                      <Small>{L?.shift_close ? `shifts closed · ${L.shift_close.closed_ontime} on time · ${L.shift_close.closed_late} late · ${L.shift_close.pending} not closed` : ""}</Small></>} />
            </div>

            {/* needs attention + losses */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(420px, 1fr))", gap: 12, marginBottom: 14 }}>
              <Section title="Needs attention now" tone={attentionCount ? "bad" : undefined}
                right={<span style={{ fontSize: 12, color: attentionCount ? C.bad : C.ok, fontWeight: 800 }}>{attentionCount ? `${attentionCount} items` : "Nothing open"}</span>}>
                {py && py.mail_configured === false && (
                  <div style={{ background: C.warnBg, color: C.warn, borderRadius: 8, padding: "7px 10px", fontSize: 12.5, marginBottom: 10 }}>
                    PY bypass approval mail is not set up, so no bypass can be approved and none becomes a deviation.
                    Set it in PY Bypass → Bypass approval mail.
                  </div>
                )}
                <AttentionGroup title="Andon calls open" rows={A?.andon_open}
                  render={r => <><b>{r.line_name}</b><span>{r.type}{r.machine ? ` · ${r.machine}` : ""}</span>
                    <span style={{ color: C.bad, fontWeight: 700 }}>{mins(r.minutes)}</span>{!r.acknowledged && <Chip tone="bad">not acknowledged</Chip>}</>} />
                <AttentionGroup title="PY bypass open" rows={py?.open}
                  render={r => <><b>{r.line_name}</b><span>{r.py_name || r.py_no}{r.machine ? ` · ${r.machine}` : ""}</span>
                    <span style={{ color: C.bad, fontWeight: 700 }}>{mins(r.minutes)}</span><Chip tone={r.status === "WAITING" ? "warn" : "n"}>{(r.status || "").toLowerCase()}</Chip></>} />
                <AttentionGroup title="PY switched to bypass by hand" rows={py?.manual}
                  render={r => <><b>{r.line_name}</b><span>{r.machine}</span><span style={{ color: C.sub }}>{r.by ? `by ${r.by}` : ""} {r.reason ? `· ${r.reason}` : ""}</span></>} />
                <AttentionGroup title="PY sensors not working" rows={A?.sensor_faults}
                  render={r => <><b>{r.line_name}</b><span>{r.py_name || r.py_no}</span><span style={{ color: C.bad, fontWeight: 700 }}>{mins(r.minutes)}</span></>} />
                <AttentionGroup title="Planned lines not running" rows={A?.stopped_lines}
                  render={r => <><a href={lineUrl(r.line_name)} target="_blank" rel="noreferrer" style={{ color: C.blue, fontWeight: 700, textDecoration: "none" }}>{r.line_name} ↗</a>
                    <span style={{ color: C.sub }}>{r.zone_name} · plan {fmt(r.plan)}{r.live_status ? ` · ${r.live_status}` : ""}</span></>} />
                <AttentionGroup title="Shifts ended, not closed in Shift Compile" rows={A?.shift_pending}
                  render={r => <><b>{r.line_name}</b><span>shift {r.shift}</span><span style={{ color: C.sub }}>ended {timeOf(r.ended_at)}</span></>} />
                <AttentionGroup title="Open shift escalations" rows={A?.escalations}
                  render={r => <><b>{r.line_name}</b><span>{dayLabel(r.date)} · shift {r.shift}</span><Chip tone="warn">level {r.level}</Chip></>} />
                {!attentionCount && !(py && py.mail_configured === false) && <div style={{ color: C.ok, fontSize: 13 }}>No open breakdown, bypass or unclosed shift.</div>}
              </Section>

              <Section title="Where the time went" right={<span style={{ fontSize: 12, color: C.sub }}>minutes lost by kind</span>}>
                <LossBars today={T?.losses} lwd={L?.losses} labels={T?.loss_labels || L?.loss_labels} lwdName={L ? dayLabel(L.date) : "Last working day"} />
              </Section>
            </div>

            {/* zone table */}
            <Section title="Zones"
              right={<div style={{ display: "flex", gap: 6 }}>
                {[["today", `Today · ${dayLabel(data.production_day)}`], ["lwd", `Last working day · ${L ? dayLabel(L.date) : "—"}`]].map(([k, lab]) => (
                  <button key={k} onClick={() => setTab(k)} style={{ border: `1px solid ${tab === k ? C.blue : C.line}`, background: tab === k ? C.blueBg : "#fff",
                    color: tab === k ? C.blue : C.ink, borderRadius: 8, padding: "5px 12px", fontWeight: 700, fontSize: 12.5, cursor: "pointer" }}>{lab}</button>
                ))}
              </div>}>
              <ZoneTable key={tab} block={tab === "today" ? T : L} />
              <div style={{ fontSize: 11.5, color: C.faint, marginTop: 8 }}>
                Click a zone to see its lines; click a line to open its MANAGEMENT dashboard. OEE averages the lines that ran.
                "No loss data" marks zones whose lines record no losses, so their OEE reads close to 100%.
              </div>
            </Section>

            {/* last working day details */}
            {L && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 12, marginTop: 14 }}>
                <Section title={`${dayLabel(L.date)} · by shift`}>
                  <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 13, fontVariantNumeric: "tabular-nums" }}>
                    <thead><tr>{["Shift", "Plan", "OK", "NG", "% of plan", "OEE"].map(h =>
                      <th key={h} style={{ textAlign: "left", color: C.sub, fontSize: 11, padding: "4px 6px", borderBottom: `1px solid ${C.line}` }}>{h}</th>)}</tr></thead>
                    <tbody>{(L.per_shift || []).map(s => (
                      <tr key={s.shift}>
                        <td style={{ padding: "6px", fontWeight: 800 }}>{s.shift}</td>
                        <td style={{ padding: "6px" }}>{fmt(s.plan)}</td>
                        <td style={{ padding: "6px", fontWeight: 700 }}>{fmt(s.ok)}</td>
                        <td style={{ padding: "6px", color: s.ng ? C.bad : C.sub }}>{fmt(s.ng)}</td>
                        <td style={{ padding: "6px" }}>{pct(s.pct)}</td>
                        <td style={{ padding: "6px", color: oeeTone(s.oee), fontWeight: 700 }}>{pct(s.oee)}</td>
                      </tr>))}</tbody>
                  </table>
                </Section>
                <Section title={`${dayLabel(L.date)} · longest breakdowns`}>
                  {L.breakdowns?.longest?.length ? L.breakdowns.longest.map((b, i) => (
                    <div key={i} style={{ display: "flex", gap: 8, fontSize: 13, padding: "4px 0", borderBottom: i < 2 ? `1px solid ${C.line}` : "none", flexWrap: "wrap" }}>
                      <b>{b.line_name}</b><span style={{ color: C.sub }}>{b.type}{b.machine ? ` · ${b.machine}` : ""} · {timeOf(b.started_at)}</span>
                      <span style={{ marginLeft: "auto", color: C.bad, fontWeight: 800 }}>{mins(b.minutes)}</span>
                    </div>)) : <div style={{ color: C.sub, fontSize: 13 }}>No breakdown calls.</div>}
                </Section>
                <Section title={`${dayLabel(L.date)} · PY bypass`}>
                  {L.py ? (
                    <>
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
                        <Chip>{fmt(L.py.raised)} raised</Chip><Chip tone="ok">{fmt(L.py.cleared)} cleared on their own</Chip>
                        <Chip tone="blue">{fmt(L.py.approved)} approved with deviation</Chip><Chip tone="bad">{fmt(L.py.rejected)} rejected</Chip>
                        {L.py.waiting ? <Chip tone="warn">{fmt(L.py.waiting)} still waiting</Chip> : null}
                      </div>
                      {(L.py.by_line || []).map(r => (
                        <div key={r.line_name} style={{ display: "flex", fontSize: 12.5, padding: "2px 0" }}>
                          <span>{r.line_name}</span><span style={{ marginLeft: "auto", fontWeight: 700 }}>{r.count}</span>
                        </div>))}
                    </>
                  ) : <div style={{ color: C.sub, fontSize: 13 }}>—</div>}
                </Section>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
