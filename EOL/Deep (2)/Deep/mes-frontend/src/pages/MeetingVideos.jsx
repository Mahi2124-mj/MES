// pages/MeetingVideos.jsx — Local Meeting Video (2026-11-13)
//
// Operator: on the 65-inch panel there is no way to download a cycle video, so
// "ek alag page bana de": a supervisor saves a video from the Supervisor wall
// ("Save to meeting"), it shows up here zone-wise and line-wise, the picked
// one plays large at the top, and it stays 10 days — nothing to download.
// Backend: routers/meeting_videos.py (the clip is copied when it is saved).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import PageTopbar from "../components/PageTopbar";

const REFRESH_MS = 20000;
const DAY = 86400000;
const C = {
  ink: "#0f172a", sub: "#475569", muted: "#64748b", faint: "#94a3b8", line: "#e2e8f0",
  bg: "#f1f5f9", card: "#fff", main: "#1d4ed8", machine: "#b45309", ng: "#dc2626", ok: "#15803d",
};
const PREF_KEY = "mes_meeting_filter";

const two = (n) => String(n).padStart(2, "0");
const dt = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${two(d.getDate())}-${two(d.getMonth() + 1)}-${d.getFullYear()} ${two(d.getHours())}:${two(d.getMinutes())}`;
};
const daysLeft = (iso) => {
  const ms = new Date(iso).getTime() - Date.now();
  if (!Number.isFinite(ms)) return "";
  const d = Math.ceil(ms / DAY);
  return d <= 1 ? "Removed within a day" : `${d} days left`;
};
const ctText = (v) => (v == null ? "—" : `${Number(v).toFixed(2)} s`);
const overCt = (v) => v.ct != null && v.ideal_ct != null && Number(v.ideal_ct) > 0 && Number(v.ct) > Number(v.ideal_ct);
const label = (v) => v.machine_label || (v.kind === "line" ? "Final Inspection" : "Machine");
const cycNo = (v) => v.cycle_no ?? v.cycle_seq;
// 2026-11-13 — the default name always stays; a name typed when saving follows it
const defName = (v) => `${label(v)}${v.machine_name && v.machine_name !== label(v) ? ` ${v.machine_name}` : ""} · Cycle #${cycNo(v)}`;

function loadPref() {
  try { return JSON.parse(localStorage.getItem(PREF_KEY) || "{}") || {}; } catch { return {}; }
}
function savePref(p) {
  try { localStorage.setItem(PREF_KEY, JSON.stringify(p)); } catch { /* private mode */ }
}

function Chip({ active, onClick, children, color }) {
  return (
    <button onClick={onClick} style={{
      border: `1px solid ${active ? color : C.line}`, background: active ? color : "#fff",
      color: active ? "#fff" : C.ink, borderRadius: 999, padding: "7px 14px", fontSize: 13,
      fontWeight: 700, cursor: "pointer", fontFamily: "inherit", whiteSpace: "nowrap",
    }}>{children}</button>
  );
}

function Badge({ v }) {
  const isMain = v.kind === "line";
  return (
    <span style={{
      display: "inline-block", fontSize: 11, fontWeight: 800, letterSpacing: ".02em",
      color: isMain ? C.main : C.machine, background: isMain ? "#eff6ff" : "#fffbeb",
      border: `1px solid ${isMain ? "#bfdbfe" : "#fde68a"}`, borderRadius: 6, padding: "2px 7px",
    }}>{isMain ? `★ ${label(v)}` : label(v)}</span>
  );
}

export default function MeetingVideos() {
  const { token, theme } = useAuth();
  const accent = theme?.accent || "#2563eb";
  const [data, setData] = useState({ items: [], zones: [], keep_days: 10 });
  const [loaded, setLoaded] = useState(false);
  const [err, setErr] = useState("");
  const pref0 = useMemo(loadPref, []);
  const [zoneId, setZoneId] = useState(pref0.zoneId ?? null);
  const [lineId, setLineId] = useState(pref0.lineId ?? null);
  const [selId, setSelId] = useState(null);
  const [big, setBig] = useState(false);
  const [busy, setBusy] = useState(null);
  const vidRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const r = await api.get("/api/meeting-videos", token);
      setData(r || { items: [], zones: [], keep_days: 10 });
      setErr("");
    } catch (e) {
      setErr(e?.message || "Could not load the videos.");
    } finally {
      setLoaded(true);
    }
  }, [token]);

  useEffect(() => {
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);
  useEffect(() => { savePref({ zoneId, lineId }); }, [zoneId, lineId]);
  useEffect(() => {
    if (!big) return undefined;
    const k = (e) => { if (e.key === "Escape") setBig(false); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [big]);

  // a zone / line that no longer has videos falls back to "All"
  const zone = data.zones.find(z => z.zone_id === zoneId) || null;
  const lines = useMemo(() => (zone ? zone.lines : []), [zone]);
  useEffect(() => {
    if (!loaded) return;
    if (zoneId != null && !zone) { setZoneId(null); setLineId(null); }
    else if (lineId != null && !lines.some(l => l.line_id === lineId)) setLineId(null);
  }, [loaded, zoneId, lineId, zone, lines]);

  const shown = useMemo(() => data.items.filter(v =>
    (zoneId == null || v.zone_id === zoneId) && (lineId == null || v.line_id === lineId)), [data.items, zoneId, lineId]);
  const playable = shown.filter(v => v.status === "ready");
  const sel = data.items.find(v => v.id === selId && v.status === "ready") || null;
  useEffect(() => { if (selId != null && loaded && !data.items.some(v => v.id === selId)) setSelId(null); }, [data.items, selId, loaded]);

  // groups: zone -> line, newest first inside each line
  const groups = useMemo(() => {
    const m = new Map();
    for (const v of shown) {
      const k = `${v.zone_name}\u0000${v.line_name}`;
      if (!m.has(k)) m.set(k, { zone: v.zone_name, line: v.line_name, line_id: v.line_id, items: [] });
      m.get(k).items.push(v);
    }
    return [...m.values()].sort((a, b) => (a.zone || "").localeCompare(b.zone || "") || (a.line || "").localeCompare(b.line || ""));
  }, [shown]);

  const step = (d) => {
    if (!playable.length) return;
    const i = playable.findIndex(v => v.id === selId);
    const n = i < 0 ? 0 : (i + d + playable.length) % playable.length;
    setSelId(playable[n].id);
  };
  const pick = (v) => {
    if (v.status !== "ready") return;
    setSelId(v.id);
    if (!big) window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const remove = async (v) => {
    if (!window.confirm(`Remove this video from Local Meeting Video?\n\n${v.line_name} · ${defName(v)}${v.title ? ` — ${v.title}` : ""}`)) return;
    setBusy(v.id);
    try {
      await api.delete(`/api/meeting-videos/${v.id}`, token);
      if (selId === v.id) setSelId(null);
      await load();
    } catch (e) {
      window.alert(e?.message || "Could not remove the video.");
    } finally {
      setBusy(null);
    }
  };

  const src = sel ? `/api/meeting-videos/${sel.id}/video?token=${encodeURIComponent(token || "")}` : "";
  const total = data.items.length;

  const stage = (
    <section style={big ? {
      position: "fixed", inset: 0, zIndex: 2147483000, background: "#020617", display: "flex", flexDirection: "column",
      padding: "calc(var(--cap-sat, 0px) * var(--mes-zinv, 1) + 12px) 16px 12px",
    } : {
      background: "#0b1220", borderRadius: 14, padding: 14, marginBottom: 16, boxShadow: "0 4px 18px rgba(15,23,42,.18)",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
        <div style={{ color: "#e2e8f0", fontSize: 14, fontWeight: 700, minWidth: 0 }}>
          <span style={{ color: "#94a3b8", fontSize: 11, fontWeight: 800, letterSpacing: ".08em", marginRight: 10 }}>NOW SHOWING</span>
          {sel ? (
            <>
              <span>{sel.zone_name} › <b>{sel.line_name}</b></span>
              <span style={{ color: "#64748b", margin: "0 8px" }}>·</span>
              <span style={{ color: sel.kind === "line" ? "#93c5fd" : "#fcd34d" }}>{sel.kind === "line" ? `★ ${label(sel)}` : label(sel)}</span>
              {sel.machine_name && sel.machine_name !== label(sel) && <span> {sel.machine_name}</span>}
              <span style={{ color: "#64748b", margin: "0 8px" }}>·</span>
              <span>Cycle #{cycNo(sel)}</span>
              {sel.title && <span style={{ color: "#fff", fontWeight: 800 }}> — {sel.title}</span>}
              <span style={{ color: "#64748b", margin: "0 8px" }}>·</span>
              <span style={{ fontFamily: "monospace", color: overCt(sel) ? "#f87171" : "#86efac" }}>CT {ctText(sel.ct)}</span>
              {sel.is_ng && <span style={{ marginLeft: 8, color: "#fff", background: C.ng, borderRadius: 5, padding: "1px 7px", fontSize: 11 }}>ALARM</span>}
            </>
          ) : <span style={{ color: "#94a3b8" }}>No video selected</span>}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {[["◀ Previous", () => step(-1)], ["Next ▶", () => step(1)]].map(([t, fn]) => (
            <button key={t} onClick={fn} disabled={!playable.length} style={stageBtn(!playable.length)}>{t}</button>
          ))}
          <button onClick={() => setBig(b => !b)} disabled={!sel} style={stageBtn(!sel)}>{big ? "✕ Close large view" : "⛶ Large view"}</button>
        </div>
      </div>
      <div style={{ position: "relative", flex: big ? 1 : "none", minHeight: 0, display: "flex", alignItems: "center", justifyContent: "center",
                    background: "#000", borderRadius: 10, width: "100%", height: big ? "auto" : "min(62vh, 52vw)", overflow: "hidden" }}>
        {sel ? (
          <video ref={vidRef} key={sel.id} src={src} controls autoPlay muted loop playsInline preload="auto"
                 style={{ width: "100%", height: "100%", objectFit: "contain", background: "#000" }} />
        ) : (
          <div style={{ color: "#94a3b8", fontSize: 15, textAlign: "center", padding: 24, lineHeight: 1.6 }}>
            {total
              ? "Pick a video from the list below to show it here."
              : <>No videos saved yet.<br />Open a cycle video on the Supervisor dashboard and press <b style={{ color: "#e2e8f0" }}>📌 Save to meeting</b>.</>}
          </div>
        )}
      </div>
      {sel && (
        <div style={{ color: "#94a3b8", fontSize: 12, marginTop: 8, display: "flex", gap: 14, flexWrap: "wrap" }}>
          <span>Cycle time {dt(sel.cycle_ts)}{sel.shift ? ` · ${sel.shift} shift` : ""}</span>
          <span>Saved by {sel.added_by || "—"} on {dt(sel.added_at)}</span>
          <span>Kept until {dt(sel.expires_at)}</span>
        </div>
      )}
    </section>
  );

  return (
    <div style={{ minHeight: "100vh", background: C.bg, fontFamily: "'Segoe UI', Arial, sans-serif", color: C.ink }}>
      <PageTopbar leading="Local Meeting" accent="Video" />
      <div style={{ maxWidth: 1500, margin: "0 auto", padding: "14px 16px 40px" }}>
        {stage}

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>
          <span style={{ fontSize: 11, fontWeight: 800, color: C.muted, letterSpacing: ".08em", marginRight: 4 }}>ZONE</span>
          <Chip active={zoneId == null} color={accent} onClick={() => { setZoneId(null); setLineId(null); }}>All ({total})</Chip>
          {data.zones.map(z => (
            <Chip key={z.zone_id ?? 0} active={zoneId === z.zone_id} color={accent}
                  onClick={() => { setZoneId(z.zone_id); setLineId(null); }}>{z.zone_name} ({z.count})</Chip>
          ))}
        </div>
        {zone && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>
            <span style={{ fontSize: 11, fontWeight: 800, color: C.muted, letterSpacing: ".08em", marginRight: 4 }}>LINE</span>
            <Chip active={lineId == null} color={accent} onClick={() => setLineId(null)}>All lines ({zone.count})</Chip>
            {lines.map(l => (
              <Chip key={l.line_id} active={lineId === l.line_id} color={accent} onClick={() => setLineId(l.line_id)}>{l.line_name} ({l.count})</Chip>
            ))}
          </div>
        )}
        <div style={{ fontSize: 12, color: C.muted, margin: "4px 0 14px" }}>
          Videos are kept for {data.keep_days || 10} days, then removed automatically.
        </div>

        {err && <div style={{ background: "#fef2f2", color: C.ng, border: "1px solid #fecaca", borderRadius: 8, padding: "10px 12px", marginBottom: 12, fontSize: 13 }}>{err}</div>}
        {loaded && !err && total > 0 && !shown.length && (
          <div style={{ color: C.muted, fontSize: 14, padding: "18px 0" }}>No videos for this selection.</div>
        )}

        {groups.map(g => (
          <div key={`${g.zone}-${g.line_id}`} style={{ marginBottom: 18 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, borderBottom: `2px solid ${C.ink}`, paddingBottom: 4, marginBottom: 10 }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: C.muted, textTransform: "uppercase", letterSpacing: ".05em" }}>{g.zone}</span>
              <span style={{ color: C.faint }}>›</span>
              <span style={{ fontSize: 17, fontWeight: 800 }}>{g.line}</span>
              <span style={{ fontSize: 12, color: C.muted }}>{g.items.length} video{g.items.length === 1 ? "" : "s"}</span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 10 }}>
              {g.items.map(v => {
                const on = v.id === selId;
                const ready = v.status === "ready";
                return (
                  <div key={v.id} onClick={() => pick(v)} role="button" tabIndex={0}
                       onKeyDown={(e) => { if (e.key === "Enter") pick(v); }}
                       style={{
                         background: on ? "#eff6ff" : C.card, borderRadius: 10, padding: "10px 12px",
                         border: `2px solid ${on ? accent : (v.is_ng ? "#fecaca" : C.line)}`,
                         cursor: ready ? "pointer" : "default", opacity: busy === v.id ? 0.5 : 1,
                         display: "flex", flexDirection: "column", gap: 5, minWidth: 0,
                       }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                      <Badge v={v} />
                      {v.is_ng && <span style={{ fontSize: 11, fontWeight: 800, color: "#fff", background: C.ng, borderRadius: 5, padding: "2px 6px" }}>ALARM</span>}
                      {on && <span style={{ marginLeft: "auto", fontSize: 11, fontWeight: 800, color: accent }}>▶ SHOWING</span>}
                    </div>
                    {v.machine_name && v.machine_name !== label(v) && (
                      <div style={{ fontSize: 13, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={v.machine_name}>{v.machine_name}</div>
                    )}
                    <div style={{ fontSize: 14, fontWeight: 700, lineHeight: 1.35, overflowWrap: "anywhere" }}>
                      Cycle #{cycNo(v)}
                      {v.title && <span style={{ color: accent }}> — {v.title}</span>}
                    </div>
                    <div style={{ fontSize: 12, color: C.sub }}>
                      <span style={{ fontFamily: "monospace", fontWeight: 700, color: overCt(v) ? C.ng : C.ok }}>CT {ctText(v.ct)}</span>
                      {" · "}{dt(v.cycle_ts || v.added_at)}{v.shift ? ` · ${v.shift} shift` : ""}
                    </div>
                    <div style={{ fontSize: 11.5, color: C.muted }}>Saved by {v.added_by || "—"} · {daysLeft(v.expires_at)}</div>
                    {v.status === "saving" && <div style={{ fontSize: 12, color: "#b45309", fontWeight: 700 }}>Saving the video…</div>}
                    {v.status === "failed" && <div style={{ fontSize: 12, color: C.ng, fontWeight: 700 }}>{v.error || "The video could not be saved."}</div>}
                    <div style={{ display: "flex", gap: 8, marginTop: 2 }}>
                      {ready && (
                        <button onClick={(e) => { e.stopPropagation(); pick(v); }} style={cardBtn(accent, true)}>▶ Show</button>
                      )}
                      {v.can_remove && (
                        <button onClick={(e) => { e.stopPropagation(); remove(v); }} disabled={busy === v.id}
                                style={cardBtn(C.ng, false)}>Remove</button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function stageBtn(disabled) {
  return {
    border: "1px solid #334155", background: "#1e293b", color: disabled ? "#64748b" : "#e2e8f0",
    borderRadius: 8, padding: "7px 12px", fontSize: 13, fontWeight: 700, cursor: disabled ? "default" : "pointer",
    fontFamily: "inherit", whiteSpace: "nowrap",
  };
}
function cardBtn(color, solid) {
  return {
    border: `1px solid ${color}`, background: solid ? color : "#fff", color: solid ? "#fff" : color,
    borderRadius: 7, padding: "5px 12px", fontSize: 12.5, fontWeight: 700, cursor: "pointer", fontFamily: "inherit",
  };
}
