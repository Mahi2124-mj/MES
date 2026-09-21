// ───────────────────────────────────────────────────────────────────────
// EscalationAdmin.jsx   (/escalation-admin)   2026-09-01   (admin only)
// ───────────────────────────────────────────────────────────────────────
// Build, per ZONE, the ordered person-chain the shift-end NG/alarm summary
// walks up: Level 1 (e.g. shift incharge) → Level 2 (section incharge) → …
// Add people, reorder, remove, Save.  Backed by
// /api/escalation/{admins, zone/{id}/chain}.
// ───────────────────────────────────────────────────────────────────────
import { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import PageTopbar from "../components/PageTopbar";
import OrgTree from "../components/OrgTree";

export default function EscalationAdmin() {
  const { token } = useAuth();
  const [zones, setZones]   = useState([]);
  const [admins, setAdmins] = useState([]);
  const [chains, setChains] = useState({});   // zone_id → [admin_id,…] (ordered)
  const [sel, setSel]       = useState({});   // zone_id → picker value
  const [toast, setToast]   = useState(null);
  const [busy, setBusy]     = useState(null);
  const [view, setView]     = useState("tree");   // 'tree' | 'edit'

  const flash = (m) => { setToast(m); setTimeout(() => setToast(null), 3000); };
  const nameOf = (id) => { const a = admins.find(x => String(x.id) === String(id)); return a ? `${a.name} (${a.role})` : `#${id}`; };

  const loadChain = useCallback(async (zid) => {
    try {
      const r = await api.get(`/api/escalation/zone/${zid}/chain`, token);
      setChains(c => ({ ...c, [zid]: (r.chain || []).map(x => x.admin_id) }));
    } catch { /* ignore */ }
  }, [token]);

  useEffect(() => {
    (async () => {
      try {
        const zs = await api.get("/api/zones/", token);
        const zl = Array.isArray(zs) ? zs : (zs.zones || []);
        setZones(zl);
        setAdmins(await api.get("/api/escalation/admins", token) || []);
        for (const z of zl) loadChain(z.id);
      } catch { /* ignore */ }
    })();
  }, [token, loadChain]);

  const setZ = (zid, arr) => setChains(c => ({ ...c, [zid]: arr }));
  const addPerson = (zid) => {
    const aid = sel[zid]; if (!aid) return;
    const cur = chains[zid] || [];
    if (cur.some(x => String(x) === String(aid))) { flash("Already in the chain"); return; }
    setZ(zid, [...cur, Number(aid)]);
    setSel(s => ({ ...s, [zid]: "" }));
  };
  const move = (zid, i, dir) => {
    const cur = [...(chains[zid] || [])]; const j = i + dir;
    if (j < 0 || j >= cur.length) return;
    [cur[i], cur[j]] = [cur[j], cur[i]]; setZ(zid, cur);
  };
  const remove = (zid, i) => setZ(zid, (chains[zid] || []).filter((_, k) => k !== i));

  const save = async (zid) => {
    setBusy(zid);
    try {
      await api.put(`/api/escalation/zone/${zid}/chain`, { admin_ids: chains[zid] || [] }, token);
      flash("Saved ✓");
      await loadChain(zid);
    } catch (e) { flash("Save failed: " + (e.message || "error")); }
    finally { setBusy(null); }
  };

  const box = { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12, padding: "14px 16px", marginBottom: 16, boxShadow: "0 1px 3px rgba(0,0,0,.05)" };

  return (
    <div style={{ padding: "18px 22px 60px", color: "#0f172a" }}>
      <PageTopbar leading="Escalation" accent="Hierarchy" />
      {toast && <div style={{ position: "fixed", top: 16, right: 16, zIndex: 6000, background: "#0f172a", color: "#fff", padding: "10px 16px", borderRadius: 10, fontSize: 13, fontWeight: 600 }}>{toast}</div>}

      {/* View toggle: plant org tree (default) vs the escalation-chain editor */}
      <div style={{ display: "flex", gap: 6, margin: "12px 0 16px", borderBottom: "2px solid #e2e8f0" }}>
        {[{ k: "tree", t: "Hierarchy Tree" }, { k: "edit", t: "Edit Escalation Chains" }].map(tb => (
          <button key={tb.k} onClick={() => setView(tb.k)}
                  style={{ padding: "9px 18px", border: "none", background: "none", cursor: "pointer",
                           fontSize: 13.5, fontWeight: view === tb.k ? 800 : 600,
                           color: view === tb.k ? "#1e40af" : "#94a3b8",
                           borderBottom: view === tb.k ? "3px solid #1e40af" : "3px solid transparent",
                           marginBottom: -2 }}>
            {tb.t}
          </button>
        ))}
      </div>

      {view === "tree" && <OrgTree />}

      {view === "edit" && (<>
      <div style={{ fontSize: 12.5, color: "#64748b", margin: "0 0 16px" }}>
        Set the order per zone — <b>Level 1 = shift incharge</b>, then upward. At shift end the alarm summary goes to the Level 1 user; when they <b>Complete</b> it, it moves to the next level.
      </div>

      {zones.length === 0 && <div style={{ color: "#94a3b8" }}>Loading zones…</div>}
      {zones.map(z => {
        const chain = chains[z.id] || [];
        const avail = admins.filter(a => !chain.some(x => String(x) === String(a.id)));
        return (
          <div key={z.id} style={box}>
            <div style={{ fontSize: 16, fontWeight: 800, marginBottom: 10 }}>{z.zone_name || z.name}</div>

            {chain.length === 0
              ? <div style={{ fontSize: 12.5, color: "#94a3b8", marginBottom: 10 }}>No chain set — escalation won't run for this zone until you add people.</div>
              : (
                <div style={{ display: "grid", gap: 6, marginBottom: 10 }}>
                  {chain.map((aid, i) => (
                    <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, background: "#f8fafc", border: "1px solid #eef2f7", borderRadius: 8, padding: "7px 10px" }}>
                      <span style={{ background: "#1e40af", color: "#fff", width: 24, height: 24, borderRadius: 999, display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 800, flexShrink: 0 }}>{i + 1}</span>
                      <span style={{ flex: 1, fontWeight: 600, fontSize: 13.5 }}>{nameOf(aid)}</span>
                      <button onClick={() => move(z.id, i, -1)} disabled={i === 0} style={ibtn}>▲</button>
                      <button onClick={() => move(z.id, i, +1)} disabled={i === chain.length - 1} style={ibtn}>▼</button>
                      <button onClick={() => remove(z.id, i)} style={{ ...ibtn, color: "#dc2626", borderColor: "#fecaca" }}>✕</button>
                    </div>
                  ))}
                </div>
              )}

            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <select value={sel[z.id] || ""} onChange={e => setSel(s => ({ ...s, [z.id]: e.target.value }))}
                      style={{ padding: "8px 10px", border: "1px solid #cbd5e1", borderRadius: 8, fontSize: 13, minWidth: 240 }}>
                <option value="">+ Add person…</option>
                {avail.map(a => <option key={a.id} value={a.id}>{a.name} ({a.role})</option>)}
              </select>
              <button onClick={() => addPerson(z.id)} style={{ border: "1px solid #1e40af", background: "#eff6ff", color: "#1e40af", borderRadius: 8, padding: "8px 14px", fontSize: 13, fontWeight: 700, cursor: "pointer" }}>Add</button>
              <button onClick={() => save(z.id)} disabled={busy === z.id} style={{ marginLeft: "auto", background: "#16a34a", color: "#fff", border: "none", borderRadius: 8, padding: "8px 18px", fontSize: 13, fontWeight: 700, cursor: "pointer", opacity: busy === z.id ? .6 : 1 }}>{busy === z.id ? "…" : "Save chain"}</button>
            </div>
          </div>
        );
      })}
      </>)}
    </div>
  );
}

const ibtn = { border: "1px solid #cbd5e1", background: "#fff", borderRadius: 6, width: 28, height: 28, cursor: "pointer", fontSize: 12, color: "#475569", flexShrink: 0 };
