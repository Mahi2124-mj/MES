// ───────────────────────────────────────────────────────────────────────
// LeaderAllocation.jsx   (2026-09-13)
// ───────────────────────────────────────────────────────────────────────
// The 2nd module inside Operator Master (shift-incharge & above): assign line
// LEADERS (role='leader' users) to LINES — one leader can handle many lines —
// and see each leader's CAPABILITY FACTOR (auto from production OEE + quality
// NG% of their lines over the last 7 days) plus a manual Trained / Needs-
// training flag, so the best/most-capable leaders surface on top.
// Backed by /api/leaders (list / {id}/lines / {id}/meta).
// ───────────────────────────────────────────────────────────────────────
import { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const th = { textAlign: "left", padding: "8px 12px", fontSize: 11, fontWeight: 700, color: "#64748b", textTransform: "uppercase", letterSpacing: ".04em", borderBottom: "1px solid #e2e8f0", whiteSpace: "nowrap" };
const td = { padding: "9px 12px", fontSize: 13, color: "#0f172a", borderBottom: "1px solid #f1f5f9", verticalAlign: "top" };
const btnG = { background: "#fff", color: "#334155", border: "1px solid #cbd5e1", borderRadius: 8, padding: "6px 12px", fontSize: 12.5, fontWeight: 600, cursor: "pointer" };

const LABEL_STYLE = {
  "Best":            { bg: "#dcfce7", fg: "#15803d" },
  "Good":            { bg: "#dbeafe", fg: "#1e40af" },
  "Average":         { bg: "#fef3c7", fg: "#a16207" },
  "Needs training":  { bg: "#fee2e2", fg: "#b91c1c" },
  "No data":         { bg: "#f1f5f9", fg: "#64748b" },
};

export default function LeaderAllocation() {
  const { token } = useAuth();
  const [rows, setRows]     = useState([]);
  const [lines, setLines]   = useState([]);
  const [loading, setLd]    = useState(true);
  const [edit, setEdit]     = useState(null);     // leader being line-edited
  const [sel, setSel]       = useState(new Set());
  const [busy, setBusy]     = useState(false);
  const [toast, setToast]   = useState(null);
  const [addOpen, setAddOpen] = useState(false);   // Assign Leader modal
  const [cands, setCands]     = useState([]);      // existing users to promote
  const [selCand, setSelCand] = useState("");
  const [q, setQ]           = useState("");   // search leaders
  const [newOpen, setNewOpen] = useState(false);   // Add-new-leader modal
  const [newName, setNewName] = useState("");      // leader name
  const [newEmp, setNewEmp]   = useState("");      // employee code
  const [newZone, setNewZone] = useState("");      // zone (optional)
  const [zones, setZones]     = useState([]);
  // 2026-09-19 — Edit: name, employee code, zone.
  const [editLd, setEditLd]   = useState(null);    // leader being edited
  const [eName, setEName]     = useState("");
  const [eEmp,  setEEmp]      = useState("");
  const [eZone, setEZone]     = useState("");
  const flash = (m) => { setToast(m); setTimeout(() => setToast(null), 3000); };

  const load = useCallback(async () => {
    try {
      const [lr, ls, zs] = await Promise.all([
        api.get("/api/leaders", token),
        api.get("/api/lines/", token).catch(() => []),
        api.get("/api/leaders/zones", token).catch(() => ({ zones: [] })),
      ]);
      setRows(lr.leaders || []);
      setLines(Array.isArray(ls) ? ls : []);
      setZones(zs.zones || []);
    } catch (e) { flash("Failed to load leaders"); }
    finally { setLd(false); }
  }, [token]);
  useEffect(() => { load(); }, [load]);

  const openEdit = (ld) => { setEdit(ld); setSel(new Set(ld.lines.map(l => l.id))); };
  const toggle = (id) => setSel(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const saveLines = async () => {
    if (!edit) return;
    setBusy(true);
    try {
      await api.put(`/api/leaders/${edit.id}/lines`, { line_ids: [...sel] }, token);
      flash(`Lines updated for ${edit.username} ✓`);
      setEdit(null); await load();
    } catch (e) { flash("Save failed: " + (e.message || "")); }
    finally { setBusy(false); }
  };

  const openAssign = async () => {
    setAddOpen(true); setSelCand("");
    try { const r = await api.get("/api/leaders/candidates", token); setCands(r.candidates || []); }
    catch { setCands([]); }
  };
  const assignLeader = async () => {
    if (!selCand) { flash("Select a user"); return; }
    setBusy(true);
    try {
      await api.post("/api/leaders/assign", { user_id: Number(selCand) }, token);
      const u = cands.find(c => String(c.id) === String(selCand));
      flash(`${u ? u.username : "User"} is now a Leader ✓`);
      setAddOpen(false); setSelCand(""); await load();
    } catch (e) { flash("Assign failed: " + (e.message || "")); }
    finally { setBusy(false); }
  };

  // Add a brand-new leader with just a NAME + EMPLOYEE CODE. The employee code
  // becomes the initial login password (leader can change it later).
  const openNew = () => { setNewOpen(true); setNewName(""); setNewEmp(""); setNewZone(""); };
  const createNew = async () => {
    const name = newName.trim(), emp = newEmp.trim();
    if (!name) { flash("Enter a name"); return; }
    if (emp.length < 4) { flash("Employee code must be at least 4 characters"); return; }
    setBusy(true);
    try {
      const r = await api.post("/api/leaders",
        { username: name, employee_code: emp, zone_id: newZone ? Number(newZone) : null }, token);
      // Same name as someone else → the server makes the login "Name (code)".
      flash(r && r.username && r.username !== name
        ? `Leader added ✓ — same name exists, so the login ID is "${r.username}"`
        : `Leader ${name} added ✓`);
      setNewOpen(false); await load();
    } catch (e) {
      flash("Add failed: " + (e.message || ""));
    } finally { setBusy(false); }
  };

  // The form shows the plain name even when the login is "Name (code)".
  const plainName = (ld) => {
    const u = ld.username || "", sfx = ld.employee_code ? ` (${ld.employee_code})` : null;
    return sfx && u.endsWith(sfx) ? u.slice(0, -sfx.length) : u;
  };
  const openEditLd = (ld) => {
    setEditLd(ld); setEName(plainName(ld)); setEEmp(ld.employee_code || "");
    setEZone(ld.zone_id != null ? String(ld.zone_id) : "");
  };
  const saveEditLd = async () => {
    if (!editLd) return;
    const name = eName.trim();
    if (!name) { flash("Enter a name"); return; }
    setBusy(true);
    try {
      const r = await api.put(`/api/leaders/${editLd.id}`,
        { username: name, employee_code: eEmp.trim() || null,
          zone_id: eZone ? Number(eZone) : null }, token);
      flash(r && r.login_changed ? `Saved ✓ — login ID is now "${r.username}"` : "Leader updated ✓");
      setEditLd(null); await load();
    } catch (e) { flash("Save failed: " + (e.message || "")); }
    finally { setBusy(false); }
  };

  const deleteLeader = async (ld) => {
    if (!window.confirm(`Delete leader "${ld.username}"? This removes their login and line assignments. This cannot be undone.`)) return;
    setBusy(true);
    try {
      await api.delete(`/api/leaders/${ld.id}`, token);
      flash(`Leader ${ld.username} deleted`);
      setRows(rs => rs.filter(r => r.id !== ld.id));
    } catch (e) { flash("Delete failed: " + (e.message || "")); }
  };

  // 2026-09-17 — a leader's signature, so the breakdown slip can stamp it the
  // moment that leader is picked instead of waiting for a paper signature.
  // The image is downscaled here, in the browser: a phone photo is several MB
  // and the server rightly refuses anything over ~300 KB, so shrinking it at
  // the source is what makes "just take a picture of the sign" actually work.
  const uploadSignature = async (ld, file) => {
    if (!file) return;
    if (!/^image\//.test(file.type)) { flash("Image file chuniye"); return; }
    try {
      const dataUrl = await new Promise((res, rej) => {
        const fr = new FileReader();
        fr.onload = () => res(fr.result);
        fr.onerror = rej;
        fr.readAsDataURL(file);
      });
      const small = await new Promise((res) => {
        const img = new Image();
        img.onload = () => {
          const maxW = 480, scale = Math.min(1, maxW / img.width);
          const cv = document.createElement("canvas");
          cv.width = Math.round(img.width * scale);
          cv.height = Math.round(img.height * scale);
          cv.getContext("2d").drawImage(img, 0, 0, cv.width, cv.height);
          res(cv.toDataURL("image/png"));
        };
        img.onerror = () => res(dataUrl);
        img.src = dataUrl;
      });
      setBusy(true);
      await api.post(`/api/leaders/${ld.id}/signature`, { signature_image: small }, token);
      flash(`${ld.username} ka signature save ho gaya`);
      load();
    } catch (e) {
      flash("Signature save nahi hua: " + (e.message || ""));
    } finally { setBusy(false); }
  };

  const clearSignature = async (ld) => {
    if (!window.confirm(`Remove ${ld.username}'s signature?`)) return;
    try {
      setBusy(true);
      await api.post(`/api/leaders/${ld.id}/signature`, { signature_image: null }, token);
      flash("Signature hata diya"); load();
    } catch (e) { flash("Nahi hata: " + (e.message || "")); }
    finally { setBusy(false); }
  };

  const toggleTrained = async (ld) => {
    try {
      await api.put(`/api/leaders/${ld.id}/meta`, { trained: !ld.trained }, token);
      setRows(rs => rs.map(r => r.id === ld.id ? { ...r, trained: !r.trained } : r));
    } catch (e) { flash("Update failed"); }
  };

  if (loading) return <div style={{ padding: 20, color: "#94a3b8" }}>Loading…</div>;

  const _q = q.trim().toLowerCase();
  const filtered = _q
    ? rows.filter(r => [r.username, r.employee_code, r.zone_name, r.label, ...(r.lines || []).map(l => l.name)]
        .some(v => String(v || "").toLowerCase().includes(_q)))
    : rows;

  return (
    <div>
      {toast && <div style={{ position: "fixed", top: 16, right: 16, zIndex: 6000, background: "#0f172a", color: "#fff", padding: "10px 16px", borderRadius: 10, fontSize: 13, fontWeight: 600 }}>{toast}</div>}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
        <div style={{ fontSize: 12.5, color: "#64748b", flex: 1, minWidth: 220 }}>
          Add & manage <b>Leaders</b> here. Assign a leader to a line each shift in <b>Shift Allocation</b>. Capability = auto Production (OEE) + Quality (NG%) of the lines they led in the last 7 days, plus the Trained flag. Best on top.
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <div style={{ position: "relative" }}>
            <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "#94a3b8", fontSize: 13, pointerEvents: "none" }}>🔍</span>
            <input value={q} onChange={e => setQ(e.target.value)}
                   placeholder="Search leader / line…"
                   style={{ padding: "8px 30px 8px 30px", border: "1px solid #cbd5e1", borderRadius: 8, fontSize: 13, minWidth: 220, background: "#fff", color: "#0f172a" }} />
            {q && <button onClick={() => setQ("")} title="Clear"
                          style={{ position: "absolute", right: 6, top: "50%", transform: "translateY(-50%)", border: "none", background: "none", color: "#94a3b8", cursor: "pointer", fontSize: 15, lineHeight: 1 }}>×</button>}
          </div>
          {_q && <span style={{ fontSize: 11.5, color: "#64748b", whiteSpace: "nowrap" }}>{filtered.length} / {rows.length}</span>}
          <button onClick={openNew}
                  style={{ background: "#16a34a", color: "#fff", border: "none", borderRadius: 8, padding: "8px 16px", fontSize: 13, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap" }}>
            + Add Leader
          </button>
          <button onClick={openAssign}
                  style={{ background: "#1e40af", color: "#fff", border: "none", borderRadius: 8, padding: "8px 16px", fontSize: 13, fontWeight: 700, cursor: "pointer", whiteSpace: "nowrap" }}>
            + Assign Leader
          </button>
        </div>
      </div>

      {rows.length === 0 ? (
        <div style={{ textAlign: "center", padding: "40px 16px", color: "#64748b" }}>
          <div style={{ fontSize: 34, opacity: .3 }}>🧑‍🏭</div>
          <div style={{ fontSize: 14, fontWeight: 600, marginTop: 8 }}>No leaders yet</div>
          <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 4 }}>
            Click <b>+ Add Leader</b> to add a new leader (name + employee code), or
            <b> + Assign Leader</b> to make an existing user a leader.
          </div>
          <div style={{ display: "flex", gap: 10, justifyContent: "center", marginTop: 14 }}>
            <button onClick={openNew}
                    style={{ background: "#16a34a", color: "#fff", border: "none", borderRadius: 8, padding: "9px 20px", fontSize: 13, fontWeight: 700, cursor: "pointer" }}>
              + Add Leader
            </button>
            <button onClick={openAssign}
                    style={{ background: "#1e40af", color: "#fff", border: "none", borderRadius: 8, padding: "9px 20px", fontSize: 13, fontWeight: 700, cursor: "pointer" }}>
              + Assign Leader
            </button>
          </div>
        </div>
      ) : (
        <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 12 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 720 }}>
            <thead><tr>{["Leader", "Employee Code", "Zone", "Capability", "OEE", "NG %", "Lines led (7d)", "Trained", "Signature", ""].map((h, i) => <th key={i} style={th}>{h}</th>)}</tr></thead>
            <tbody>
              {filtered.length === 0 && (
                <tr><td colSpan={10} style={{ ...td, textAlign: "center", color: "#94a3b8" }}>No leader matches “{q}”.</td></tr>
              )}
              {filtered.map(ld => {
                const ls = LABEL_STYLE[ld.label] || LABEL_STYLE["No data"];
                return (
                  <tr key={ld.id}>
                    <td style={{ ...td, fontWeight: 700 }}>{ld.username}</td>
                    <td style={{ ...td, fontFamily: "ui-monospace, Menlo, monospace", color: ld.employee_code ? "#0f172a" : "#cbd5e1" }}>
                      {ld.employee_code || "—"}
                    </td>
                    <td style={{ ...td, color: ld.zone_name ? "#0f172a" : "#cbd5e1" }}>{ld.zone_name || "—"}</td>
                    <td style={td}>
                      <span style={{ display: "inline-block", padding: "3px 10px", borderRadius: 999, fontSize: 11.5, fontWeight: 700, background: ls.bg, color: ls.fg }}>
                        {ld.label}{ld.capability != null ? ` · ${ld.capability}` : ""}
                      </span>
                      {ld.parts > 0 && <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 2 }}>{ld.parts.toLocaleString("en-IN")} parts / 7d</div>}
                    </td>
                    <td style={td}>{ld.oee == null ? "—" : `${ld.oee}%`}</td>
                    <td style={{ ...td, color: ld.ng_pct > 3 ? "#b91c1c" : "#334155" }}>{ld.ng_pct != null ? `${ld.ng_pct}%` : "—"}</td>
                    <td style={td}>
                      <b>{ld.line_count}</b>
                      {ld.lines.length > 0 && <div style={{ fontSize: 10.5, color: "#94a3b8", maxWidth: 220 }}>{ld.lines.map(l => l.name).join(", ")}</div>}
                    </td>
                    <td style={td}>
                      <button onClick={() => toggleTrained(ld)}
                              style={{ ...btnG, padding: "4px 10px",
                                       border: ld.trained ? "1.5px solid #15803d" : "1.5px solid #cbd5e1",
                                       background: ld.trained ? "#dcfce7" : "#fff",
                                       color: ld.trained ? "#15803d" : "#94a3b8", fontWeight: 700 }}>
                        {ld.trained ? "✓ Trained" : "Needs training"}
                      </button>
                    </td>
                    <td style={{ ...td, whiteSpace: "nowrap" }}>
                      {ld.signature_image ? (
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                          <img src={ld.signature_image} alt="signature"
                               style={{ height: 26, maxWidth: 110, objectFit: "contain",
                                        border: "1px solid #e2e8f0", borderRadius: 4,
                                        background: "#fff" }} />
                          <button onClick={() => clearSignature(ld)} disabled={busy}
                                  title="Remove this signature"
                                  style={{ ...btnG, padding: "2px 7px", fontSize: 11 }}>✕</button>
                        </span>
                      ) : (
                        <label style={{ ...btnG, padding: "4px 10px", fontSize: 11.5,
                                        cursor: "pointer", display: "inline-block" }}>
                          ⤴ Upload sign
                          <input type="file" accept="image/*" style={{ display: "none" }}
                                 onChange={e => { uploadSignature(ld, e.target.files?.[0]);
                                                  e.target.value = ""; }} />
                        </label>
                      )}
                    </td>
                    <td style={{ ...td, textAlign: "right", whiteSpace: "nowrap" }}>
                      <button onClick={() => openEditLd(ld)} disabled={busy} title="Edit name, employee code, zone"
                              style={{ ...btnG, padding: "4px 10px", marginRight: 6, fontWeight: 700 }}>
                        ✎ Edit
                      </button>
                      <button onClick={() => deleteLeader(ld)} disabled={busy} title="Delete this leader"
                              style={{ ...btnG, padding: "4px 10px", borderColor: "#fecaca", color: "#b91c1c", fontWeight: 700 }}>
                        🗑 Delete
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}


      {/* Add-new-leader modal — just NAME + EMPLOYEE CODE */}
      {newOpen && (
        <div onClick={e => e.target === e.currentTarget && setNewOpen(false)}
             style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,.5)", zIndex: 7000, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
          <div style={{ background: "#fff", borderRadius: 14, width: "min(460px,95vw)", overflow: "hidden" }}>
            <div style={{ padding: "14px 18px", borderBottom: "1px solid #e2e8f0", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ fontSize: 15, fontWeight: 800 }}>Add Leader</div>
              <button onClick={() => setNewOpen(false)} style={{ border: "none", background: "none", fontSize: 20, cursor: "pointer", color: "#94a3b8" }}>×</button>
            </div>
            <div style={{ padding: "16px 18px", display: "flex", flexDirection: "column", gap: 14 }}>
              <div>
                <label style={{ fontSize: 12.5, fontWeight: 700, color: "#334155", display: "block", marginBottom: 5 }}>Name</label>
                <input value={newName} onChange={e => setNewName(e.target.value)} autoFocus
                       placeholder="Leader name"
                       onKeyDown={e => { if (e.key === "Enter") createNew(); }}
                       style={{ width: "100%", padding: "10px 11px", border: "1px solid #cbd5e1", borderRadius: 8, fontSize: 14, boxSizing: "border-box", background: "#fff", color: "#0f172a" }} />
              </div>
              <div>
                <label style={{ fontSize: 12.5, fontWeight: 700, color: "#334155", display: "block", marginBottom: 5 }}>Employee Code</label>
                <input value={newEmp} onChange={e => setNewEmp(e.target.value)}
                       placeholder="e.g. 10482"
                       onKeyDown={e => { if (e.key === "Enter") createNew(); }}
                       style={{ width: "100%", padding: "10px 11px", border: "1px solid #cbd5e1", borderRadius: 8, fontSize: 14, boxSizing: "border-box", background: "#fff", color: "#0f172a", fontFamily: "ui-monospace, Menlo, monospace" }} />
                <div style={{ fontSize: 10.5, color: "#94a3b8", marginTop: 6 }}>
                  The employee code is the leader's initial login password (they can change it later). At least 4 characters.
                  Two leaders may share a name — the employee code tells them apart.
                </div>
              </div>
              <div>
                <label style={{ fontSize: 12.5, fontWeight: 700, color: "#334155", display: "block", marginBottom: 5 }}>Zone</label>
                <select value={newZone} onChange={e => setNewZone(e.target.value)}
                        style={{ width: "100%", padding: "10px 11px", border: "1px solid #cbd5e1", borderRadius: 8, fontSize: 14, boxSizing: "border-box", background: "#fff", color: "#0f172a" }}>
                  <option value="">— not set —</option>
                  {zones.map(z => <option key={z.id} value={z.id}>{z.zone_name}</option>)}
                </select>
              </div>
            </div>
            <div style={{ padding: "12px 18px", borderTop: "1px solid #e2e8f0", display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button onClick={() => setNewOpen(false)} style={btnG}>Cancel</button>
              <button onClick={createNew} disabled={busy || !newName.trim() || newEmp.trim().length < 4}
                      style={{ background: "#16a34a", color: "#fff", border: "none", borderRadius: 8, padding: "8px 18px", fontSize: 13, fontWeight: 700, cursor: "pointer", opacity: (busy || !newName.trim() || newEmp.trim().length < 4) ? .6 : 1 }}>
                {busy ? "Adding…" : "Add Leader"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit-leader modal — name, employee code, zone */}
      {editLd && (
        <div onClick={e => e.target === e.currentTarget && setEditLd(null)}
             style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,.5)", zIndex: 7000, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
          <div style={{ background: "#fff", borderRadius: 14, width: "min(460px,95vw)", overflow: "hidden" }}>
            <div style={{ padding: "14px 18px", borderBottom: "1px solid #e2e8f0", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ fontSize: 15, fontWeight: 800 }}>Edit Leader</div>
              <button onClick={() => setEditLd(null)} style={{ border: "none", background: "none", fontSize: 20, cursor: "pointer", color: "#94a3b8" }}>×</button>
            </div>
            <div style={{ padding: "16px 18px", display: "flex", flexDirection: "column", gap: 14 }}>
              <div>
                <label style={{ fontSize: 12.5, fontWeight: 700, color: "#334155", display: "block", marginBottom: 5 }}>Name</label>
                <input value={eName} onChange={e => setEName(e.target.value)} autoFocus
                       onKeyDown={e => { if (e.key === "Enter") saveEditLd(); }}
                       style={{ width: "100%", padding: "10px 11px", border: "1px solid #cbd5e1", borderRadius: 8, fontSize: 14, boxSizing: "border-box", background: "#fff", color: "#0f172a" }} />
              </div>
              <div>
                <label style={{ fontSize: 12.5, fontWeight: 700, color: "#334155", display: "block", marginBottom: 5 }}>Employee Code</label>
                <input value={eEmp} onChange={e => setEEmp(e.target.value)}
                       onKeyDown={e => { if (e.key === "Enter") saveEditLd(); }}
                       style={{ width: "100%", padding: "10px 11px", border: "1px solid #cbd5e1", borderRadius: 8, fontSize: 14, boxSizing: "border-box", background: "#fff", color: "#0f172a", fontFamily: "ui-monospace, Menlo, monospace" }} />
              </div>
              <div>
                <label style={{ fontSize: 12.5, fontWeight: 700, color: "#334155", display: "block", marginBottom: 5 }}>Zone</label>
                <select value={eZone} onChange={e => setEZone(e.target.value)}
                        style={{ width: "100%", padding: "10px 11px", border: "1px solid #cbd5e1", borderRadius: 8, fontSize: 14, boxSizing: "border-box", background: "#fff", color: "#0f172a" }}>
                  <option value="">— not set —</option>
                  {zones.map(z => <option key={z.id} value={z.id}>{z.zone_name}</option>)}
                </select>
              </div>
              <div style={{ fontSize: 10.5, color: "#94a3b8", lineHeight: 1.5 }}>
                Current login ID: <b style={{ color: "#475569" }}>{editLd.username}</b>.
                Changing the name changes the login ID; the password is not changed.
              </div>
            </div>
            <div style={{ padding: "12px 18px", borderTop: "1px solid #e2e8f0", display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button onClick={() => setEditLd(null)} style={btnG}>Cancel</button>
              <button onClick={saveEditLd} disabled={busy || !eName.trim()}
                      style={{ background: "#1e40af", color: "#fff", border: "none", borderRadius: 8, padding: "8px 18px", fontSize: 13, fontWeight: 700, cursor: "pointer", opacity: (busy || !eName.trim()) ? .6 : 1 }}>
                {busy ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Assign-leader modal — pick an EXISTING user (no new account) */}
      {addOpen && (
        <div onClick={e => e.target === e.currentTarget && setAddOpen(false)}
             style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,.5)", zIndex: 7000, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
          <div style={{ background: "#fff", borderRadius: 14, width: "min(520px,95vw)", maxHeight: "85vh", display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div style={{ padding: "14px 18px", borderBottom: "1px solid #e2e8f0", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div style={{ fontSize: 15, fontWeight: 800 }}>Assign Leader</div>
              <button onClick={() => setAddOpen(false)} style={{ border: "none", background: "none", fontSize: 20, cursor: "pointer", color: "#94a3b8" }}>×</button>
            </div>
            <div style={{ padding: "14px 18px", overflowY: "auto" }}>
              <div style={{ fontSize: 12.5, fontWeight: 700, color: "#334155", marginBottom: 6 }}>
                Pick an existing user — they become a Leader
              </div>
              <select value={selCand} onChange={e => setSelCand(e.target.value)} autoFocus
                      style={{ width: "100%", padding: "10px 11px", border: "1px solid #cbd5e1", borderRadius: 8, fontSize: 14, boxSizing: "border-box", background: "#fff" }}>
                <option value="">— select a user —</option>
                {cands.map(c => (
                  <option key={c.id} value={c.id}>{c.username} · {c.role}</option>
                ))}
              </select>
              <div style={{ fontSize: 10.5, color: "#94a3b8", marginTop: 6 }}>
                {cands.length === 0
                  ? "No assignable users (everyone is already a leader/admin)."
                  : "No new account is created — the selected user's role becomes Leader. Line assignment is done in Shift Allocation."}
              </div>
            </div>
            <div style={{ padding: "12px 18px", borderTop: "1px solid #e2e8f0", display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button onClick={() => setAddOpen(false)} style={btnG}>Cancel</button>
              <button onClick={assignLeader} disabled={busy || !selCand}
                      style={{ background: "#16a34a", color: "#fff", border: "none", borderRadius: 8, padding: "8px 18px", fontSize: 13, fontWeight: 700, cursor: "pointer", opacity: (busy || !selCand) ? .6 : 1 }}>
                {busy ? "Assigning…" : "Assign Leader"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
