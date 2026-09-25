// ───────────────────────────────────────────────────────────────────────
// SixSigmaPage.jsx   (6 Sigma — Ball Guide dual-camera clip review)
// ───────────────────────────────────────────────────────────────────────
// Ball Guide station (Seat Slider lines only) has TWO cameras. Both cut a clip
// on the SAME production cycle → each cycle has two synchronized clips. Clips
// are kept for 40 days. This page: (1) SETUP — pick the Seat Slider line + the
// two cameras (RTSP URL) + retention; (2) REVIEW — per cycle, watch both
// cameras' clips side by side. Backed by /api/sixsigma.
// ───────────────────────────────────────────────────────────────────────
import { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import PageTopbar from "../components/PageTopbar";

const card = { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 14, padding: 16, marginBottom: 18 };
const lbl  = { fontSize: 11.5, fontWeight: 700, color: "#334155", marginBottom: 4 };
const inp  = { padding: "8px 10px", border: "1px solid #cbd5e1", borderRadius: 8, fontSize: 13, background: "#fff", color: "#0f172a" };
const btn  = { background: "#1e40af", color: "#fff", border: "none", borderRadius: 8, padding: "9px 18px", fontSize: 13, fontWeight: 700, cursor: "pointer" };
const th   = { textAlign: "left", padding: "8px 12px", fontSize: 11, fontWeight: 700, color: "#64748b", textTransform: "uppercase", borderBottom: "1px solid #e2e8f0", whiteSpace: "nowrap" };
const td   = { padding: "9px 12px", fontSize: 13, color: "#0f172a", borderBottom: "1px solid #f1f5f9" };

export default function SixSigmaPage({ toast }) {
  const { token, user } = useAuth();
  const isAdmin = ["admin", "plant_head"].includes(user?.role);

  const [lines, setLines]     = useState([]);
  const [configs, setConfigs] = useState([]);
  const [defMachine, setDefMachine] = useState("Ball Guide");
  const [defRetention, setDefRetention] = useState(40);

  // config form
  const [lineId, setLineId]   = useState("");
  const [machine, setMachine] = useState("Ball Guide");
  const [c1n, setC1n] = useState("Camera 1");
  const [c1u, setC1u] = useState("");
  const [c2n, setC2n] = useState("Camera 2");
  const [c2u, setC2u] = useState("");
  const [ret, setRet] = useState(40);
  const [busy, setBusy] = useState(false);

  // viewer
  const [viewLine, setViewLine] = useState("");
  const [date, setDate]   = useState(() => new Date().toISOString().slice(0, 10));
  const [shift, setShift] = useState("");
  const [cycles, setCycles] = useState([]);
  const [viewCfg, setViewCfg] = useState(null);
  const [sel, setSel] = useState(null);   // selected cycle
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api.get("/api/sixsigma/config", token);
      setLines(r.lines || []);
      setConfigs(r.configs || []);
      setDefMachine(r.default_machine || "Ball Guide");
      setDefRetention(r.default_retention || 40);
      if (!viewLine && (r.configs || []).length) setViewLine(String(r.configs[0].line_id));
    } catch (e) { toast?.("Failed to load 6-Sigma config", "err"); }
  }, [token]); // eslint-disable-line
  useEffect(() => { load(); }, []); // eslint-disable-line

  const save = async () => {
    if (!lineId) { toast?.("Pick a Seat Slider line", "err"); return; }
    setBusy(true);
    try {
      await api.post("/api/sixsigma/config", {
        line_id: Number(lineId), machine_name: machine.trim() || "Ball Guide",
        cam1_name: c1n.trim(), cam1_url: c1u.trim(),
        cam2_name: c2n.trim(), cam2_url: c2u.trim(),
        retention_days: Number(ret) || 40,
      }, token);
      toast?.("Ball Guide 6-Sigma config saved ✓");
      setLineId(""); setMachine("Ball Guide"); setC1n("Camera 1"); setC1u("");
      setC2n("Camera 2"); setC2u(""); setRet(40);
      await load();
    } catch (e) { toast?.(e.message || "Save failed", "err"); }
    finally { setBusy(false); }
  };

  const edit = (c) => {
    setLineId(String(c.line_id)); setMachine(c.machine_name || "Ball Guide");
    setC1n(c.cam1_name || "Camera 1"); setC1u(c.cam1_url || "");
    setC2n(c.cam2_name || "Camera 2"); setC2u(c.cam2_url || "");
    setRet(c.retention_days || 40);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const del = async (id) => {
    if (!confirm("Remove this line's Ball Guide config?")) return;
    try { await api.delete(`/api/sixsigma/config?line_id=${id}`, token); await load(); }
    catch (e) { toast?.(e.message || "Delete failed", "err"); }
  };

  const loadClips = async () => {
    if (!viewLine) { toast?.("Pick a configured line", "err"); return; }
    setLoading(true); setSel(null);
    try {
      const qs = `line_id=${viewLine}&date=${date}${shift ? `&shift=${shift}` : ""}`;
      const r = await api.get(`/api/sixsigma/clips?${qs}`, token);
      setCycles(r.cycles || []);
      setViewCfg(r.config || null);
      if (!(r.cycles || []).length) toast?.("No cycles for this date/shift");
    } catch (e) { toast?.(e.message || "Failed to load clips", "err"); }
    finally { setLoading(false); }
  };

  const withTok = (u) => `${u}&token=${encodeURIComponent(token || "")}`;

  // 2026-09-25 — a downloaded clip must say what it is without being opened:
  // line, machine, camera, cycle, NG flag and the cycle's own timestamp.
  const clipFileName = (camName) => {
    const safe = (v) => String(v || "").trim().replace(/[^A-Za-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
    const lineName = (lines.find(l => String(l.id) === String(viewLine)) || {}).line_name
                     || (configs.find(c => String(c.line_id) === String(viewLine)) || {}).line_name
                     || `line-${viewLine}`;
    let stamp = "";
    if (sel?.ts) {
      const d = new Date(sel.ts);
      if (!isNaN(d)) {
        const p = (n) => String(n).padStart(2, "0");
        stamp = `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}_${p(d.getHours())}-${p(d.getMinutes())}-${p(d.getSeconds())}`;
      }
    }
    return [safe(lineName), safe(viewCfg?.machine_name || "Ball-Guide"), safe(camName),
            `cycle-${sel?.cycle_seq}`, sel?.is_ng ? "NG" : "OK", stamp]
           .filter(Boolean).join("_") + ".mp4";
  };

  return (
    <div style={{ minHeight: "100vh", background: "#f8fafc", paddingBottom: 60, color: "#0f172a" }}>
      <PageTopbar leading="6" accent="Sigma" />
      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "14px 18px" }}>
      <div style={{ fontSize: 12.5, color: "#64748b", marginBottom: 16 }}>
        Ball Guide station (Seat Slider) — two cameras cut a clip on the same production cycle. Clips kept for 40 days.
      </div>

      {/* ── SETUP ── */}
      {isAdmin && (
        <div style={card}>
          <div style={{ fontSize: 14, fontWeight: 800, color: "#0f172a", marginBottom: 12 }}>Setup — Ball Guide cameras</div>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 12 }}>
            <div>
              <div style={lbl}>Seat Slider line</div>
              <select value={lineId} onChange={e => setLineId(e.target.value)} style={{ ...inp, minWidth: 170 }}>
                <option value="">— pick line —</option>
                {lines.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
              </select>
            </div>
            <div>
              <div style={lbl}>Machine</div>
              <input value={machine} onChange={e => setMachine(e.target.value)} style={{ ...inp, width: 150 }} />
            </div>
            <div>
              <div style={lbl}>Retention (days)</div>
              <input type="number" value={ret} onChange={e => setRet(e.target.value)} style={{ ...inp, width: 110 }} />
            </div>
          </div>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
            <div style={{ flex: 1, minWidth: 300 }}>
              <div style={lbl}>Camera 1 — name</div>
              <input value={c1n} onChange={e => setC1n(e.target.value)} style={{ ...inp, width: "100%", boxSizing: "border-box", marginBottom: 8 }} />
              <div style={lbl}>Camera 1 — RTSP URL</div>
              <input value={c1u} onChange={e => setC1u(e.target.value)} placeholder="rtsp://user:pass@192.168.x.x:554/..." style={{ ...inp, width: "100%", boxSizing: "border-box" }} />
            </div>
            <div style={{ flex: 1, minWidth: 300 }}>
              <div style={lbl}>Camera 2 — name</div>
              <input value={c2n} onChange={e => setC2n(e.target.value)} style={{ ...inp, width: "100%", boxSizing: "border-box", marginBottom: 8 }} />
              <div style={lbl}>Camera 2 — RTSP URL</div>
              <input value={c2u} onChange={e => setC2u(e.target.value)} placeholder="rtsp://user:pass@192.168.x.x:554/..." style={{ ...inp, width: "100%", boxSizing: "border-box" }} />
            </div>
          </div>
          <button onClick={save} disabled={busy} style={{ ...btn, opacity: busy ? .6 : 1 }}>
            {busy ? "Saving…" : "Save configuration"}
          </button>

          {configs.length > 0 && (
            <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 12, marginTop: 16 }}>
              <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 620 }}>
                <thead><tr>{["Line", "Machine", "Camera 1", "Camera 2", "Retention", ""].map((h, i) => <th key={i} style={th}>{h}</th>)}</tr></thead>
                <tbody>
                  {configs.map(c => (
                    <tr key={c.line_id}>
                      <td style={{ ...td, fontWeight: 700 }}>{c.line_name}</td>
                      <td style={td}>{c.machine_name}</td>
                      <td style={td}>{c.cam1_name}{c.cam1_url ? "" : " (no URL)"}</td>
                      <td style={td}>{c.cam2_name}{c.cam2_url ? "" : " (no URL)"}</td>
                      <td style={td}>{c.retention_days} days</td>
                      <td style={td}>
                        <button onClick={() => edit(c)} style={{ fontSize: 12, border: "1px solid #cbd5e1", background: "#fff", borderRadius: 7, padding: "4px 10px", cursor: "pointer", marginRight: 6 }}>Edit</button>
                        <button onClick={() => del(c.line_id)} style={{ fontSize: 12, border: "1px solid #fecaca", color: "#dc2626", background: "#fff", borderRadius: 7, padding: "4px 10px", cursor: "pointer" }}>Delete</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── REVIEW ── */}
      <div style={card}>
        <div style={{ fontSize: 14, fontWeight: 800, color: "#0f172a", marginBottom: 12 }}>Clip review — both cameras, per cycle</div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 14 }}>
          <div>
            <div style={lbl}>Line</div>
            <select value={viewLine} onChange={e => setViewLine(e.target.value)} style={{ ...inp, minWidth: 170 }}>
              <option value="">— pick line —</option>
              {configs.map(c => <option key={c.line_id} value={c.line_id}>{c.line_name}</option>)}
            </select>
          </div>
          <div>
            <div style={lbl}>Date</div>
            <input type="date" value={date} onChange={e => setDate(e.target.value)} style={inp} />
          </div>
          <div>
            <div style={lbl}>Shift</div>
            <select value={shift} onChange={e => setShift(e.target.value)} style={{ ...inp, minWidth: 110 }}>
              <option value="">All</option><option value="A">A</option><option value="B">B</option>
            </select>
          </div>
          <button onClick={loadClips} disabled={loading} style={{ ...btn, opacity: loading ? .6 : 1 }}>
            {loading ? "Loading…" : "Load clips"}
          </button>
        </div>

        {configs.length === 0 && (
          <div style={{ fontSize: 13, color: "#94a3b8" }}>No Ball Guide line configured yet. Add one in Setup above.</div>
        )}

        {/* two players side by side */}
        {sel && (
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 16 }}>
            {[["cam1_url", viewCfg?.cam1_name || "Camera 1"], ["cam2_url", viewCfg?.cam2_name || "Camera 2"]].map(([k, nm]) => (
              <div key={k} style={{ flex: 1, minWidth: 300 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: "#334155", marginBottom: 6 }}>
                  {nm} · cycle #{sel.cycle_seq}{sel.is_ng ? " · NG" : ""}
                </div>
                {/* 2026-09-24 — a Ball Guide clip is shown ONLY if one exists.
                    The API used to return the line's ordinary cycle-video URL
                    with "&cam=1/2", which that endpoint ignores, so both boxes
                    played the same Final Inspection clip under the Ball Guide
                    camera names. Anything pointing at /cycle-video is therefore
                    NOT this station's footage and must not be presented as it. */}
                {(sel[k] && !String(sel[k]).includes("/cycle-video")) ? (
                  <>
                    {/* 2026-09-25 — plays as soon as the cycle is opened.  Muted
                        is not a preference: every browser blocks autoplay with
                        sound, so an unmuted <video autoPlay> would simply sit
                        still.  The controls are there to unmute. */}
                    <video key={sel.cycle_seq + k} controls autoPlay muted playsInline preload="auto"
                           style={{ width: "100%", borderRadius: 10, background: "#000", aspectRatio: "16/9" }}
                           src={withTok(sel[k])} />
                    <a href={withTok(sel[k])} download={clipFileName(nm)}
                       style={{ display: "inline-flex", alignItems: "center", gap: 6, marginTop: 6,
                                padding: "6px 12px", borderRadius: 8, fontSize: 12, fontWeight: 600,
                                border: "1px solid #cbd5e1", background: "#fff", color: "#1e40af",
                                textDecoration: "none" }}
                       title={clipFileName(nm)}>
                      ⬇ Download
                    </a>
                  </>
                ) : (
                  <div style={{ width: "100%", borderRadius: 10, background: "#0f172a", aspectRatio: "16/9",
                                display: "flex", alignItems: "center", justifyContent: "center",
                                textAlign: "center", padding: 16, color: "#cbd5e1", fontSize: 13, lineHeight: 1.5 }}>
                    No clip from this camera yet.<br />
                    The RTSP address is saved, but recording for the Ball Guide
                    station is not running, so this cycle has no footage.
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* cycle list */}
        {cycles.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {cycles.map(cy => (
              <button key={cy.cycle_seq} onClick={() => setSel(cy)}
                      style={{ padding: "6px 12px", borderRadius: 8, fontSize: 12, cursor: "pointer",
                        border: `1px solid ${cy.is_ng ? "#fecaca" : "#cbd5e1"}`,
                        background: sel?.cycle_seq === cy.cycle_seq ? "#1e40af" : (cy.is_ng ? "#fef2f2" : "#fff"),
                        color: sel?.cycle_seq === cy.cycle_seq ? "#fff" : (cy.is_ng ? "#dc2626" : "#0f172a"),
                        fontWeight: 600 }}>
                #{cy.cycle_seq}{cy.is_ng ? " NG" : ""}{cy.ts ? " · " + cy.ts.slice(11, 19) : ""}
              </button>
            ))}
          </div>
        )}
      </div>
      </div>
    </div>
  );
}
