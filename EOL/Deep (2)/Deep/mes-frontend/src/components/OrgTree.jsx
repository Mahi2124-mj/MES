// ───────────────────────────────────────────────────────────────────────
// OrgTree.jsx   (2026-09-13)
// ───────────────────────────────────────────────────────────────────────
// Whole-plant hierarchy drawn as a proper connected org-chart: Plant Head at
// the top, connected down through Production Head → Section Heads → Shift
// Incharge → Leader → Operator (one node per person; a head over 2 zones is a
// single node with all reports under it). SVG connectors, photos (add/replace),
// active/idle dots, a shift-rotation toggle (1/2 week → current shift) and a
// search box that highlights people. Backed by /api/hierarchy/tree + /photo.
// ───────────────────────────────────────────────────────────────────────
import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const ROLE_LABEL = {
  operator: "Operator", production: "Operator", leader: "Leader",
  shift_incharge: "Shift Incharge", section_incharge: "Section Incharge",
  production_incharge: "Production Head", quality_incharge: "Quality Incharge",
  plant_head: "Plant Head", admin: "Plant Head",
};
const ROLE_COLOR = {
  admin: "#1e40af", plant_head: "#1e40af", production_incharge: "#047857",
  section_incharge: "#475569", quality_incharge: "#b45309", shift_incharge: "#0369a1",
  leader: "#6d28d9", operator: "#0ea5e9", production: "#0ea5e9",
};
const idleText = (s) => s == null ? "never" : s < 90 ? "now" : s < 3600 ? `${Math.round(s/60)}m` : s < 86400 ? `${Math.round(s/3600)}h` : `${Math.round(s/86400)}d`;

const NODE_W = 120, HALF = NODE_W / 2, ROW = 150, HGAP = 16;

function fileToThumb(file, size = 160) {
  return new Promise((resolve, reject) => {
    const img = new Image(); const fr = new FileReader();
    fr.onload = () => { img.src = fr.result; }; fr.onerror = reject;
    img.onload = () => {
      const s = Math.min(img.width, img.height);
      const c = document.createElement("canvas"); c.width = c.height = size;
      c.getContext("2d").drawImage(img, (img.width - s) / 2, (img.height - s) / 2, s, s, 0, 0, size, size);
      resolve(c.toDataURL("image/jpeg", 0.8));
    };
    img.onerror = reject; fr.readAsDataURL(file);
  });
}

// measure subtree width (px)
function measure(n) {
  const kids = n.children || [];
  if (!kids.length) return NODE_W;
  const span = kids.map(measure).reduce((a, b) => a + b, 0) + HGAP * (kids.length - 1);
  return Math.max(span, NODE_W);
}
// place nodes (assign absolute center-x + top-y); returns width used
function place(n, depth, xStart, out) {
  const kids = n.children || [];
  const y = depth * ROW;
  if (!kids.length) {
    out.push({ n, x: xStart + HALF, y, childX: [] });
    return NODE_W;
  }
  const widths = kids.map(measure);
  const span = widths.reduce((a, b) => a + b, 0) + HGAP * (kids.length - 1);
  const width = Math.max(span, NODE_W);
  let cursor = xStart + (width - span) / 2;
  const cxs = [];
  kids.forEach((k, i) => { place(k, depth + 1, cursor, out); cxs.push(cursor + widths[i] / 2); cursor += widths[i] + HGAP; });
  out.push({ n, x: (cxs[0] + cxs[cxs.length - 1]) / 2, y, childX: cxs });
  return width;
}

export default function OrgTree() {
  const { token, user } = useAuth();
  const canAssign = ["admin", "plant_head", "section_incharge", "production_incharge"].includes(user?.role);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [toast, setToast] = useState(null);
  const [detail, setDetail] = useState(null);   // selected person's card
  const [dLoading, setDLoading] = useState(false);
  const flash = (m) => { setToast(m); setTimeout(() => setToast(null), 2500); };

  const openPerson = async (id) => {
    setDetail({ loading: true }); setDLoading(true);
    try { setDetail(await api.get(`/api/hierarchy/person/${id}`, token)); }
    catch { setDetail(null); flash("Could not load person"); }
    finally { setDLoading(false); }
  };

  const load = useCallback(async () => {
    try { setData(await api.get("/api/hierarchy/tree", token)); }
    catch { setData(null); } finally { setLoading(false); }
  }, [token]);
  useEffect(() => { load(); }, [load]);

  const setRole = async (uid, role) => {
    try {
      await api.post("/api/hierarchy/set-role", { user_id: uid, role }, token);
      flash("Role updated"); await openPerson(uid); load();
    } catch (e) { flash("Could not update role: " + (e.message || "")); }
  };
  const onPhoto = async (user_id, photo) => {
    try { await api.post("/api/hierarchy/photo", { user_id, photo }, token); flash("Photo updated"); load(); }
    catch { flash("Photo save failed"); }
  };

  // layout: lay each root tree side-by-side
  const { placed, edges, W, H } = useMemo(() => {
    const roots = data?.tree || [];
    const out = []; let x = 0;
    roots.forEach(r => { const w = place(r, 0, x, out); x += w + 40; });
    const byId = new Map(out.map(p => [p.n.id, p]));
    const edges = [];
    out.forEach(p => (p.n.children || []).forEach(c => {
      const cp = byId.get(c.id); if (cp) edges.push([p.x, p.y + 58, cp.x, cp.y + 6]);
    }));
    const maxDepth = out.reduce((m, p) => Math.max(m, p.y), 0);
    return { placed: out, edges, W: Math.max(x, 320), H: maxDepth + 120 };
  }, [data]);

  const _q = q.trim().toLowerCase();
  const match = (n) => !_q || (n.name || "").toLowerCase().includes(_q) || (ROLE_LABEL[n.role] || n.role).toLowerCase().includes(_q);

  if (loading) return <div style={{ padding: 20, color: "#94a3b8" }}>Loading…</div>;
  if (!data) return <div style={{ padding: 20, color: "#b91c1c" }}>Could not load hierarchy.</div>;

  return (
    <div>
      {toast && <div style={{ position: "fixed", top: 16, right: 16, zIndex: 6000, background: "#0f172a", color: "#fff", padding: "10px 16px", borderRadius: 10, fontSize: 13, fontWeight: 600 }}>{toast}</div>}

      {/* Tree canvas */}
      <div style={{ overflow: "auto", border: "1px solid #e2e8f0", borderRadius: 14, background: "#fff",
                    backgroundImage: "radial-gradient(#eef2f7 1px, transparent 1px)", backgroundSize: "22px 22px", padding: 20 }}>
        <div style={{ position: "relative", width: W, height: H, margin: "0 auto" }}>
          <svg width={W} height={H} style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
            {edges.map(([x1, y1, x2, y2], i) => (
              <path key={i} d={`M ${x1} ${y1} C ${x1} ${(y1 + y2) / 2}, ${x2} ${(y1 + y2) / 2}, ${x2} ${y2}`}
                    fill="none" stroke="#cbd5e1" strokeWidth="1.6" />
            ))}
          </svg>
          {placed.map(({ n, x, y }) => {
            const rc = ROLE_COLOR[n.role] || "#64748b";
            const on = match(n);
            return (
              <div key={n.id} onClick={() => openPerson(n.id)} title="View details"
                   style={{ position: "absolute", left: x - HALF, top: y, width: NODE_W, cursor: "pointer",
                            display: "flex", flexDirection: "column", alignItems: "center",
                            opacity: on ? 1 : 0.22, transition: "opacity .15s" }}>
                <NodeCard n={n} rc={rc} onPhoto={onPhoto} highlight={_q && on} />
              </div>
            );
          })}
        </div>
      </div>

      {/* Search */}
      <div style={{ display: "flex", justifyContent: "center", marginTop: 16 }}>
        <div style={{ position: "relative", width: "min(420px, 90%)" }}>
          <span style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "#94a3b8" }}>🔍</span>
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search person / role…"
                 style={{ width: "100%", padding: "10px 34px", border: "1px solid #cbd5e1", borderRadius: 999, fontSize: 13.5, boxSizing: "border-box", boxShadow: "0 2px 8px rgba(0,0,0,.06)" }} />
          {q && <button onClick={() => setQ("")} style={{ position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)", border: "none", background: "none", cursor: "pointer", color: "#94a3b8", fontSize: 16 }}>×</button>}
        </div>
      </div>

      {/* Person detail drawer */}
      {detail && (
        <div onClick={e => e.target === e.currentTarget && setDetail(null)}
             style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,.45)", zIndex: 7000, display: "flex", justifyContent: "flex-end" }}>
          <div style={{ width: "min(420px, 96vw)", height: "100%", background: "#fff", boxShadow: "-8px 0 30px rgba(0,0,0,.15)", display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div style={{ padding: "14px 18px", borderBottom: "1px solid #e2e8f0", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ fontSize: 15, fontWeight: 800 }}>Employee details</div>
              <button onClick={() => setDetail(null)} style={{ border: "none", background: "none", fontSize: 22, cursor: "pointer", color: "#94a3b8" }}>×</button>
            </div>
            <div style={{ padding: 18, overflowY: "auto" }}>
              {detail.loading || dLoading ? <div style={{ color: "#94a3b8" }}>Loading…</div> : (<>
                <div style={{ display: "flex", gap: 14, alignItems: "center", marginBottom: 14 }}>
                  <div style={{ width: 60, height: 60, borderRadius: "50%", overflow: "hidden", border: `2px solid ${ROLE_COLOR[detail.role] || "#64748b"}`, background: detail.photo ? "#fff" : "#eef2f7", display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 800, color: ROLE_COLOR[detail.role] || "#64748b" }}>
                    {detail.photo ? <img src={detail.photo} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} /> : (detail.name || "?").slice(0, 2).toUpperCase()}
                  </div>
                  <div>
                    <div style={{ fontSize: 18, fontWeight: 800 }}>{detail.name}</div>
                    <div style={{ fontSize: 13, color: ROLE_COLOR[detail.role] || "#475569", fontWeight: 700 }}>{ROLE_LABEL[detail.role] || detail.role}</div>
                    <div style={{ fontSize: 11.5, color: detail.active ? "#15803d" : "#94a3b8", fontWeight: 600 }}>
                      {detail.active ? "🟢 Active" : "⚪ Idle"} · last active {idleText(detail.idle_seconds)} ago
                    </div>
                  </div>
                </div>

                <Row label="Current shift" val={detail.current_shift ? `Shift ${detail.current_shift}` : "—"} />
                <Row label="Zones" val={(detail.zones || []).join(", ") || "—"} />
                <Row label="Lines" val={(detail.lines || []).join(", ") || "—"} />
                <Row label="Last login" val={detail.last_login ? new Date(detail.last_login).toLocaleString() : "—"} />

                {canAssign && ["operator", "production", "leader", "shift_incharge"].includes(detail.role) && (
                  <div style={{ marginTop: 14 }}>
                    <div style={{ fontSize: 12.5, fontWeight: 800, color: "#334155", marginBottom: 6 }}>Assign role</div>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      {[["shift_incharge", "Shift Incharge"], ["leader", "Leader"], ["operator", "Operator"]].map(([rk, lb]) => {
                        const cur = detail.role === rk || (rk === "operator" && detail.role === "production");
                        return (
                          <button key={rk} disabled={cur} onClick={() => setRole(detail.id, rk)}
                                  style={{ padding: "6px 12px", borderRadius: 8, fontSize: 12.5, fontWeight: 700,
                                           cursor: cur ? "default" : "pointer",
                                           border: cur ? "2px solid #1e40af" : "1.5px solid #cbd5e1",
                                           background: cur ? "#eff6ff" : "#fff", color: cur ? "#1e40af" : "#334155" }}>
                            {cur ? `✓ ${lb}` : `Make ${lb}`}
                          </button>
                        );
                      })}
                    </div>
                    <div style={{ fontSize: 10.5, color: "#94a3b8", marginTop: 4 }}>Section incharge &amp; above can move this person between operator / leader / shift incharge.</div>
                  </div>
                )}

                <Section title={`Pending escalations (${detail.escalations?.length || 0})`} color="#b91c1c" />
                {(detail.escalations || []).length === 0
                  ? <Empty text="No pending escalations." />
                  : (detail.escalations).map(e => (
                    <div key={e.id} style={cardRed}>
                      <div style={{ fontWeight: 700, fontSize: 13 }}>{e.line || "—"} · Shift {e.shift} · {e.date}</div>
                      <div style={{ fontSize: 11.5, color: "#64748b" }}>{e.zone} · Level {e.level}</div>
                      {e.summary && <div style={{ fontSize: 12, color: "#334155", marginTop: 3 }}>{e.summary}</div>}
                    </div>
                  ))}

                <Section title={`Alerts (${detail.alerts?.length || 0})`} color="#a16207" />
                {(detail.alerts || []).length === 0
                  ? <Empty text="No open alerts on their lines." />
                  : (detail.alerts).map(a => (
                    <div key={a.id} style={cardAmber}>
                      <div style={{ fontWeight: 700, fontSize: 13 }}>{a.kind} · {a.line || "—"}</div>
                      <div style={{ fontSize: 11.5, color: "#64748b" }}>Shift {a.shift} · {a.date}</div>
                      {a.text && <div style={{ fontSize: 12, color: "#334155", marginTop: 3 }}>{a.text}</div>}
                    </div>
                  ))}
              </>)}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const Row = ({ label, val }) => (
  <div style={{ display: "flex", gap: 8, padding: "6px 0", borderBottom: "1px solid #f1f5f9", fontSize: 13 }}>
    <div style={{ width: 84, color: "#94a3b8", fontWeight: 600, flexShrink: 0 }}>{label}</div>
    <div style={{ color: "#0f172a" }}>{val}</div>
  </div>
);
const Section = ({ title, color }) => (
  <div style={{ fontSize: 12.5, fontWeight: 800, color, textTransform: "uppercase", letterSpacing: ".04em", margin: "16px 0 8px" }}>{title}</div>
);
const Empty = ({ text }) => <div style={{ fontSize: 12.5, color: "#94a3b8", padding: "4px 0" }}>{text}</div>;
const cardRed = { background: "#fef2f2", border: "1px solid #fecaca", borderRadius: 8, padding: "8px 10px", marginBottom: 8 };
const cardAmber = { background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 8, padding: "8px 10px", marginBottom: 8 };

function NodeCard({ n, rc, onPhoto, highlight }) {
  const fileRef = useRef(null);
  const initials = (n.name || "?").slice(0, 2).toUpperCase();
  const pick = async (e) => {
    e.stopPropagation();
    const f = e.target.files?.[0]; if (!f) return;
    try { onPhoto(n.id, await fileToThumb(f)); } catch { /* ignore */ }
    e.target.value = "";
  };
  return (
    <>
      <div style={{ position: "relative", zIndex: 1 }}>
        <div style={{ width: 50, height: 50, borderRadius: "50%", overflow: "hidden",
                      border: `2px solid ${rc}`, boxShadow: highlight ? `0 0 0 3px ${rc}55` : "none",
                      background: n.photo ? "#fff" : `${rc}22`, display: "flex", alignItems: "center", justifyContent: "center",
                      color: rc, fontWeight: 800, fontSize: 15 }}>
          {n.photo ? <img src={n.photo} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} /> : initials}
        </div>
        <span title={n.active ? "Active" : "Idle"} style={{ position: "absolute", right: 0, bottom: 2, width: 11, height: 11, borderRadius: "50%", border: "2px solid #fff", background: n.active ? "#16a34a" : "#cbd5e1" }} />
        <button onClick={(e) => { e.stopPropagation(); fileRef.current?.click(); }} title="Add / change photo"
                style={{ position: "absolute", left: -4, bottom: -2, width: 17, height: 17, borderRadius: "50%", border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer", fontSize: 9, lineHeight: 1, padding: 0 }}>📷</button>
        <input ref={fileRef} type="file" accept="image/*" onChange={pick} style={{ display: "none" }} />
      </div>
      <div style={{ fontSize: 12, fontWeight: 700, color: "#0f172a", marginTop: 3, textAlign: "center", maxWidth: NODE_W - 6, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{n.name}</div>
      <div style={{ fontSize: 9.5, color: rc, fontWeight: 700, textAlign: "center" }}>{ROLE_LABEL[n.role] || n.role}</div>
      {n.zones?.length > 0 && <div style={{ fontSize: 8.5, color: "#94a3b8", textAlign: "center", maxWidth: NODE_W - 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{n.zones.join(", ")}</div>}
    </>
  );
}
