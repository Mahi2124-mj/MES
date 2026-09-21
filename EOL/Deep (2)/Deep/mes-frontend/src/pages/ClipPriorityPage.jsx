// ClipPriorityPage.jsx — Admin → Production → Clip Priority.  2026-09-21.
//
// The 48-hour footage plan's decisions, set here instead of by message:
// which zones and machine types get their clips first.  The GPU cuts at most
// ~110,000 clips a day while the plant makes ~217,000 cycles, so:
//   P1  every zone — Final Inspection + NG + poka-yoke bypass cycles
//   P2  the top N zones below — every sub-machine clip
//   P3  the other zones — cut on click from the 48-hour footage
// Saved to /api/clip-priority; the clip archiver's priority lane reads it.
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const card = { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12, padding: 18, marginBottom: 16 };
const h = { fontSize: 13, fontWeight: 800, color: "#0f172a", marginBottom: 4 };
const sub = { fontSize: 12, color: "#64748b", marginBottom: 12 };
const btn = { border: "1px solid #cbd5e1", background: "#fff", borderRadius: 6, padding: "2px 8px", cursor: "pointer", fontSize: 12 };
const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString("en-IN"));

function Ordered({ items, onMove, readOnly, render }) {
  return (
    <div>
      {items.map((it, i) => (
        <div key={it} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px",
                               border: "1px solid #eef2f7", borderRadius: 8, marginBottom: 6, background: "#f8fafc" }}>
          <b style={{ width: 22, color: "#1e40af" }}>{i + 1}</b>
          <div style={{ flex: 1, minWidth: 0 }}>{render(it, i)}</div>
          {!readOnly && (
            <>
              <button style={btn} disabled={i === 0} onClick={() => onMove(i, -1)} title="Move up">↑</button>
              <button style={btn} disabled={i === items.length - 1} onClick={() => onMove(i, 1)} title="Move down">↓</button>
            </>
          )}
        </div>
      ))}
    </div>
  );
}

export default function ClipPriorityPage({ toast, readOnly = false }) {
  const { token } = useAuth();
  const [d, setD] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const say = useCallback((m, t = "ok") => (toast ? toast(m, t) : alert(m)), [toast]);

  const load = useCallback(async () => {
    try {
      const r = await api.get("/api/clip-priority", token);
      setD(r); setCfg(r.config); setErr("");
    } catch (e) {
      setErr(/404|Not Found/i.test(String(e && e.message)) ? "This page becomes active after the next MES-API restart."
                                                 : "Could not load the clip priority settings.");
    }
  }, [token]);
  useEffect(() => { load(); }, [load]);   // eslint-disable-line react-hooks/set-state-in-effect

  const vol = (d && d.volumes && d.volumes.zones) || {};
  const plan = useMemo(() => {
    if (!cfg) return null;
    const zones = cfg.zone_order || [];
    const p1 = zones.reduce((a, z) => a + ((vol[z] || {}).fi || 0), 0);
    const p2 = zones.slice(0, cfg.p2_zones || 0).reduce((a, z) => a + ((vol[z] || {}).sub || 0), 0);
    const p3 = zones.slice(cfg.p2_zones || 0).reduce((a, z) => a + ((vol[z] || {}).sub || 0), 0);
    return { p1, p2, p3, gpu: (d && d.gpu_clips_per_day) || 110000 };
  }, [cfg, vol, d]);

  const move = (key) => (i, dir) => setCfg(c => {
    const a = [...c[key]]; const j = i + dir;
    [a[i], a[j]] = [a[j], a[i]];
    return { ...c, [key]: a };
  });

  const save = async () => {
    setSaving(true);
    try {
      const r = await api.put("/api/clip-priority", { config: cfg }, token);
      setCfg(r.config); say("Clip priority saved");
      load();
    } catch (e) {
      say((e && e.message) || "Save failed", "err");
    }
    setSaving(false);
  };

  if (err) return <div style={{ ...card, color: "#b45309" }}>{err}</div>;
  if (!cfg) return <div style={card}>Loading…</div>;

  const fits = plan.p1 + plan.p2 <= plan.gpu;
  return (
    <div style={{ maxWidth: 980 }}>
      <div style={card}>
        <div style={h}>Clip priority — 48-hour footage plan</div>
        <div style={sub}>
          Raw footage is kept 48 hours. The GPU can cut about {fmt(plan.gpu)} clips a day, fewer than the
          cycles the plant makes, so clips are cut in this order. Anything not cut in time can still be
          cut on click from the 48-hour footage.
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))", gap: 10 }}>
          {[["P1 · Final Inspection + NG", plan.p1, "#15803d"],
            [`P2 · sub-machines, top ${cfg.p2_zones} zone(s)`, plan.p2, "#1d4ed8"],
            ["P3 · cut on click", plan.p3, "#64748b"],
            ["GPU per day", plan.gpu, fits ? "#15803d" : "#b91c1c"]].map(([t, n, c]) => (
            <div key={t} style={{ border: "1px solid #e2e8f0", borderTop: `3px solid ${c}`, borderRadius: 8, padding: "8px 10px" }}>
              <div style={{ fontSize: 11, color: "#64748b" }}>{t}</div>
              <div style={{ fontSize: 20, fontWeight: 800, color: "#0f172a" }}>{fmt(n)}</div>
            </div>
          ))}
        </div>
        <div style={{ marginTop: 10, fontSize: 12, color: fits ? "#15803d" : "#b91c1c", fontWeight: 600 }}>
          {fits ? `P1 + P2 = ${fmt(plan.p1 + plan.p2)} clips/day — fits the GPU.`
                : `P1 + P2 = ${fmt(plan.p1 + plan.p2)} clips/day — more than the GPU can cut; lower the P2 zone count.`}
          <span style={{ color: "#94a3b8", fontWeight: 400 }}> Volumes from {d.volumes.day || "—"}.</span>
        </div>
      </div>

      <div style={card}>
        <div style={h}>1 · Zone order</div>
        <div style={sub}>Zones at the top get their sub-machine clips first.</div>
        <Ordered items={cfg.zone_order} onMove={move("zone_order")} readOnly={readOnly}
          render={(z, i) => (
            <span>
              <b>{z}</b>
              <span style={{ marginLeft: 10, fontSize: 12, color: "#64748b" }}>
                FI {fmt((vol[z] || {}).fi)} · sub-machines {fmt((vol[z] || {}).sub)} cycles/day
              </span>
              <span style={{ marginLeft: 10, fontSize: 11, fontWeight: 800, padding: "1px 8px", borderRadius: 999,
                             background: i < cfg.p2_zones ? "#eff6ff" : "#f1f5f9",
                             color: i < cfg.p2_zones ? "#1d4ed8" : "#64748b" }}>
                {i < cfg.p2_zones ? "P2 — all clips" : "P3 — on click"}
              </span>
            </span>
          )} />
        <div style={{ marginTop: 10, fontSize: 13 }}>
          Zones that get every sub-machine clip (P2):{" "}
          <input type="number" min={0} max={cfg.zone_order.length} value={cfg.p2_zones} disabled={readOnly}
                 onChange={e => setCfg(c => ({ ...c, p2_zones: Math.max(0, Math.min(c.zone_order.length, Number(e.target.value) || 0)) }))}
                 style={{ width: 60, padding: "4px 6px", border: "1px solid #cbd5e1", borderRadius: 6 }} />
        </div>
      </div>

      <div style={card}>
        <div style={h}>2 · Machine order within a zone</div>
        <div style={sub}>When the GPU is short, machine types at the top are cut first.</div>
        <Ordered items={cfg.machine_order} onMove={move("machine_order")} readOnly={readOnly}
                 render={(m) => <b>{m}</b>} />
      </div>

      <div style={card}>
        <div style={h}>3 · Always cut (P1, every zone)</div>
        {[["final_inspection", "Final Inspection cycles"], ["ng_cycles", "NG cycles on every machine"],
          ["py_bypass", "Cycles run with a poka-yoke bypass"]].map(([k, t]) => (
          <label key={k} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, marginBottom: 6 }}>
            <input type="checkbox" checked={!!cfg.p1[k]} disabled={readOnly}
                   onChange={e => setCfg(c => ({ ...c, p1: { ...c.p1, [k]: e.target.checked } }))} />
            {t}
          </label>
        ))}
      </div>

      <div style={card}>
        <div style={h}>4 · Keep clips for</div>
        <div style={sub}>Stored only — not applied yet. Today every archived clip is kept 30 days;
          a shorter tier deletes clips, so it is switched on only after approval.</div>
        <div style={{ display: "flex", gap: 20, flexWrap: "wrap", fontSize: 13 }}>
          <label>Final Inspection and NG clips{" "}
            <input type="number" min={1} max={365} value={cfg.retention.fi_ng_days} disabled={readOnly}
                   onChange={e => setCfg(c => ({ ...c, retention: { ...c.retention, fi_ng_days: Number(e.target.value) } }))}
                   style={{ width: 64, padding: "4px 6px", border: "1px solid #cbd5e1", borderRadius: 6 }} /> days
          </label>
          <label>Sub-machine OK clips{" "}
            <input type="number" min={1} max={365} value={cfg.retention.sub_ok_days} disabled={readOnly}
                   onChange={e => setCfg(c => ({ ...c, retention: { ...c.retention, sub_ok_days: Number(e.target.value) } }))}
                   style={{ width: 64, padding: "4px 6px", border: "1px solid #cbd5e1", borderRadius: 6 }} /> days
          </label>
        </div>
      </div>

      {!readOnly && (
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button onClick={save} disabled={saving} style={{ background: "#1e40af", color: "#fff", border: "none",
                  borderRadius: 8, padding: "9px 22px", fontWeight: 700, cursor: "pointer" }}>
            {saving ? "Saving…" : "Save priority"}
          </button>
          <span style={{ fontSize: 12, color: "#94a3b8" }}>
            {d.saved ? `Last saved by ${d.updated_by || "—"} · ${d.updated_at ? new Date(d.updated_at).toLocaleString("en-GB") : ""}`
                     : "Not saved yet — showing the recommended order."}
          </span>
        </div>
      )}
    </div>
  );
}
