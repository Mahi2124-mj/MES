// pages/ProdBreakdownSlip.jsx — Production Breakdown Slip (MES / :5656)
//   The ANDON auto-slip (generated on Maintenance_DX) reaches PRODUCTION here
//   first (PENDING_PRODUCTION). Production fills its half in the EXACT Toyota
//   Boshoku "BREAK DOWN SLIP" (TBDI/MAINT/F/001) format — same as 9965 — and
//   submits → PENDING_MAINTENANCE. Backed by /api/prod-breakdown-slips/*.
import { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import PageTopbar from "../components/PageTopbar";

const ageTxt = (d, t) => {
  if (!d) return "";
  const hhmm = /^\d{1,2}:\d{2}/.test(String(t || "")) ? String(t).slice(0, 5) : "00:00";
  const start = new Date(`${String(d).slice(0, 10)}T${hhmm}:00`);
  if (isNaN(start)) return "";
  const m = Math.max(0, Math.floor((Date.now() - start.getTime()) / 60000));
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.floor(h / 24)}d ago`;
};

// production-editable fields on an AUTO slip (rest come from ANDON, locked)
const EDITABLE = new Set(["machine_no", "line_leader_name", "machine_operator_name", "model_no",
                          "category", "frequency", "problem_reported_by_production"]);

export default function ProdBreakdownSlip() {
  const { token } = useAuth();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [slip, setSlip] = useState(null);        // open slip (form data)
  const [machines, setMachines] = useState([]);  // machine master for the slip's zone/line
  // 2026-09-17 — the leader + operator names and the leader's signature.
  // They used to be typed by hand on every slip even though the system already
  // knew them: the line leader from the shift allocation, the operators from
  // manpower allocation.  /api/leaders/slip-defaults returns both.
  const [leaderOpts, setLeaderOpts] = useState([]);   // [{id,name,signature_image}]
  const [signature,  setSignature]  = useState(null); // data URL of the picked leader
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState(null);

  const flash = (msg, kind = "ok") => { setToast({ msg, kind }); setTimeout(() => setToast(null), 3500); };

  const load = useCallback(async () => {
    setLoading(true);
    try { setRows(await api.get("/api/prod-breakdown-slips/pending", token) || []); }
    catch (e) { flash("Load failed: " + (e.message || "error"), "err"); }
    finally { setLoading(false); }
  }, [token]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);

  const open = async (r) => {
    // fetch the full slip (all fields) so the slip form is complete
    let t = r;
    try { t = await api.get(`/api/prod-breakdown-slips/${r.id}?src=${r.src}`, token); } catch {}
    setSlip({ ...t, src: r.src });
    // machine master for this (zone, line) → MACHINE NO. dropdown
    setMachines([]);
    try {
      const ms = await api.get(
        `/api/prod-breakdown-slips/machines?zone=${encodeURIComponent(t.zone || "")}&line=${encodeURIComponent(t.line || "")}`,
        token);
      setMachines(Array.isArray(ms) ? ms : []);
    } catch { setMachines([]); }

    // Fill the two names from the allocations, and load the signatures.
    // Anything already on the slip WINS — a slip that was filled in earlier is
    // never overwritten by today's allocation.
    setLeaderOpts([]); setSignature(null);
    try {
      const q = new URLSearchParams({ line_id: t.line_id || t.lineId || "" });
      if (t.slip_date) q.set("date", t.slip_date);
      if (t.shift)     q.set("shift", t.shift);
      const d = await api.get(`/api/leaders/slip-defaults?${q}`, token);
      setLeaderOpts(d.leader_options || []);
      const opNames = (d.operators || []).map(o => o.name).filter(Boolean);
      setSlip(s2 => {
        if (!s2) return s2;
        const next = { ...s2 };
        if (!String(next.line_leader_name || "").trim() && d.leader?.name)
          next.line_leader_name = d.leader.name;
        if (!String(next.machine_operator_name || "").trim() && opNames.length)
          next.machine_operator_name = opNames.join(", ");
        return next;
      });
      if (d.leader?.signature_image) setSignature(d.leader.signature_image);
    } catch { /* allocation missing is normal — leave the fields editable */ }
  };

  // Picking a leader stamps that leader's signature straight away.
  const onPickLeader = (name) => {
    const hit = leaderOpts.find(l => l.name === name);
    setSignature(hit?.signature_image || null);
    setSlip(s2 => ({ ...s2, line_leader_name: name }));
  };
  const set = (k) => (v) => setSlip(s => ({ ...s, [k]: v }));
  // picking a MACHINE NO. auto-fills MACHINE NAME (like the 9965 form)
  const onPickMachine = (mno) => setSlip(s => {
    const hit = machines.find(m => String(m.machine_no) === String(mno));
    return { ...s, machine_no: mno, machine_name: hit ? hit.machine_name : s.machine_name };
  });

  const submit = async () => {
    if (!slip) return;
    if (!String(slip.problem_reported_by_production || "").trim()) { flash("PROBLEM REPORTED BY PRODUCTION zaroori hai", "err"); return; }
    setSaving(true);
    try {
      await api.post(`/api/prod-breakdown-slips/${slip.id}/submit`,
        { src: slip.src || "maintenance", production_data: {
          machine_no: slip.machine_no,
          machine_name: slip.machine_name,
          line_leader_name: slip.line_leader_name,
          machine_operator_name: slip.machine_operator_name,
          model_no: slip.model_no,
          category: slip.category,
          frequency: Number(slip.frequency) || 1,
          problem_reported_by_production: slip.problem_reported_by_production,
        } }, token);
      flash("Submitted ✓ — maintenance ko bhej diya");
      setSlip(null); await load();
    } catch (e) { flash("Submit failed: " + (e.message || "error"), "err"); }
    finally { setSaving(false); }
  };

  const ed = (k) => EDITABLE.has(k);

  // Required-field gate — same as 9965: Submit enables only when EVERY
  // editable production field is filled. (frequency defaults to 1; times/dates
  // are ANDON-locked so they don't gate.)
  const REQUIRED = ["machine_no", "line_leader_name", "machine_operator_name",
                    "model_no", "category", "problem_reported_by_production"];
  const missing = slip ? REQUIRED.filter(k => !String(slip[k] ?? "").trim())
                         .concat(Number(slip.frequency) >= 1 ? [] : ["frequency"]) : [];
  const complete = !!slip && missing.length === 0;
  const LBL = { machine_no: "MACHINE NO.", line_leader_name: "LINE LEADER NAME",
                machine_operator_name: "MACHINE OPERATOR NAME", model_no: "MODEL NO.",
                category: "CATEGORY", problem_reported_by_production: "PROBLEM REPORTED BY PRODUCTION",
                frequency: "FREQUENCY" };

  return (
    <div style={{ padding: "18px 22px 60px", color: "#0f172a" }}>
      <PageTopbar leading="Production" accent="Breakdown Slip" />

      <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "14px 0 10px" }}>
        <span style={{ fontWeight: 800, fontSize: 15 }}>Pending — production to fill</span>
        <span style={{ background: rows.length ? "#fee2e2" : "#dcfce7", color: rows.length ? "#b91c1c" : "#15803d",
          borderRadius: 999, padding: "2px 12px", fontSize: 13, fontWeight: 700 }}>{rows.length}</span>
        <button onClick={load} style={{ ...btnGhost, marginLeft: "auto" }}>↻ Refresh</button>
      </div>

      <div style={{ border: "1px solid #e5e7eb", borderRadius: 14, background: "#fff", overflow: "hidden", maxWidth: 900 }}>
        {loading && <div style={{ padding: 16, color: "#94a3b8" }}>Loading…</div>}
        {!loading && rows.length === 0 &&
          <div style={{ padding: 16, color: "#94a3b8" }}>Koi pending breakdown slip nahi. 👍</div>}
        {rows.map(r => (
          <div key={`${r.src}-${r.id}`} onClick={() => open(r)}
            style={{ padding: "12px 14px", borderTop: "1px solid #eef2f7", cursor: "pointer",
              borderLeft: `4px solid ${r.src === "toolroom" ? "#b45309" : "#0e7490"}` }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <b style={{ fontSize: 14 }}>#{r.id} · {r.line} <span style={{ color: "#94a3b8", fontWeight: 500 }}>· {r.machine_no || "—"}</span></b>
              <span style={{ fontSize: 12, color: "#dc2626", fontWeight: 600 }}>{ageTxt(r.bd_start_date, r.bd_start_time)}</span>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 5, fontSize: 12, color: "#475569" }}>
              <span style={{ background: r.src === "toolroom" ? "#fffbeb" : "#ecfeff", color: r.src === "toolroom" ? "#b45309" : "#0e7490",
                borderRadius: 999, padding: "1px 9px", fontWeight: 700 }}>{r.src === "toolroom" ? "Tool Room" : "Maintenance"}</span>
              <span>{r.zone}</span>{r.model_no && <span>· {r.model_no}</span>}
              <span>· start {String(r.bd_start_time || "").slice(0, 5)}</span>
              {r.mc_down_time_minutes != null && <span>· down {r.mc_down_time_minutes}m</span>}
            </div>
          </div>
        ))}
      </div>

      {/* ── SLIP MODAL — exact Toyota Boshoku BREAK DOWN SLIP format ── */}
      {slip && (
        <div onClick={() => setSlip(null)} style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,.55)",
          backdropFilter: "blur(2px)", zIndex: 9000, display: "flex", alignItems: "flex-start",
          justifyContent: "center", overflowY: "auto", padding: "24px 12px" }}>
          <div className="bds-modal" onClick={e => e.stopPropagation()} style={{ width: "100%", maxWidth: 980,
            background: "#fff", borderRadius: 12, boxShadow: "0 20px 60px rgba(0,0,0,.35)", overflow: "hidden" }}>
            {/* letterhead */}
            <div className="bds-letterhead">
              <div className="bds-logo">
                <div className="bds-logo-tb">TB</div>
                <div className="bds-logo-sub">TOYOTA BOSHOKU</div>
              </div>
              <div className="bds-letter-title">
                <div className="bds-company">TOYOTA BOSHOKU DEVICE INDIA PVT. LTD.</div>
                <div className="bds-doc-title">BREAK DOWN SLIP</div>
              </div>
              <div className="bds-close-x" onClick={() => setSlip(null)} title="Close">×</div>
            </div>

            <div className="bds-body">
              {/* header grid 3×3 */}
              <div className="bds-grid bds-grid-3">
                <Cell label="ZONE" value={slip.zone} readOnly />
                <Cell label="MACHINE NO." value={slip.machine_no} readOnly={!ed("machine_no")}
                  options={ed("machine_no") ? machines.map(m => m.machine_no).filter(Boolean) : undefined}
                  onChange={onPickMachine} />
                <Cell label="DATE" type="date" value={slip.slip_date} readOnly />
                <Cell label="LINE" value={slip.line} readOnly />
                <Cell label="SHIFT" value={slip.shift} readOnly />
                <Cell label="LINE LEADER NAME" value={slip.line_leader_name}
                  readOnly={!ed("line_leader_name")}
                  options={ed("line_leader_name") && leaderOpts.length
                             ? leaderOpts.map(l => l.name) : undefined}
                  onChange={onPickLeader} />
                <Cell label="MACHINE OPERATOR NAME" value={slip.machine_operator_name} readOnly={!ed("machine_operator_name")} onChange={set("machine_operator_name")} />
                <Cell label="MACHINE NAME" wrap value={slip.machine_name} readOnly />
                <Cell label="MODEL NO." value={slip.model_no} readOnly={!ed("model_no")} onChange={set("model_no")} />
              </div>

              {/* The leader's signature, stamped as soon as a leader is picked.
                  Nothing is shown when that leader has not uploaded one — an
                  empty box is honest, a placeholder scribble would not be. */}
              <div style={{ display: "flex", alignItems: "center", gap: 12,
                            padding: "6px 2px", borderBottom: "1px solid #e2e8f0" }}>
                <div className="bds-cell-label" style={{ minWidth: 150 }}>
                  LINE LEADER SIGN :-
                </div>
                {signature ? (
                  <img src={signature} alt="leader signature"
                       style={{ height: 46, maxWidth: 240, objectFit: "contain" }} />
                ) : (
                  <span style={{ fontSize: 12, color: "#94a3b8" }}>
                    {slip.line_leader_name
                      ? "is leader ka signature upload nahi hai (Operator Master → Leaders)"
                      : "leader select karte hi sign aa jayega"}
                  </span>
                )}
              </div>

              {/* category */}
              <div className="bds-cat-head">
                <div>BREAK DOWN TYPE ( CATEGORY ) :-</div>
                <div className="bds-cat-tickdown">TICK DOWN<br />(✓)</div>
              </div>
              {[{ code: "A", desc: "MACHINE OR LINE HAS STOPPED AND PRODUCTION LOSS DIRECTLY" },
                { code: "B", desc: "MACHINE RUNNING WITH PRODUCTION LOSS ( PRODUCTION EFFECTED ) ( ADJUSTMENT )" }].map(c => (
                <div key={c.code} className="bds-cat-row">
                  <div className="bds-cat-cell-code">{c.code} CATEGORY B/D :-</div>
                  <div className="bds-cat-cell-desc">{c.desc}</div>
                  <div className="bds-cat-cell-tick">
                    <input type="checkbox" disabled={!ed("category")}
                      checked={String(slip.category || "").trim() === c.code}
                      onChange={e => set("category")(e.target.checked ? c.code : "")} />
                  </div>
                </div>
              ))}

              {/* time + date grid 3×3 */}
              <div className="bds-grid bds-grid-3">
                <Cell label="B/D START TIME" type="time" value={slip.bd_start_time} readOnly />
                <Cell label="B/D RECEIVED TIME" type="time" value={slip.bd_received_time} readOnly />
                <Cell label="RESPONSE TIME ( MIN )" type="number" value={slip.response_time_minutes} readOnly />
                <Cell label="B/D OK TIME" type="time" value={slip.bd_ok_time} readOnly />
                <Cell label="M/C DOWN TIME ( MIN )" type="number" value={slip.mc_down_time_minutes} readOnly />
                <Cell label="FREQUENCY" type="number" value={slip.frequency} readOnly={!ed("frequency")} onChange={set("frequency")} />
                <Cell label="B/D START DATE" type="date" value={slip.bd_start_date} readOnly />
                <Cell label="B/D END DATE" type="date" value={slip.bd_end_date} readOnly />
                <div />
              </div>

              {/* problem reported by production */}
              <Row label="PROBLEM REPORTED BY PRODUCTION" value={slip.problem_reported_by_production}
                readOnly={!ed("problem_reported_by_production")} onChange={set("problem_reported_by_production")} />

              <div className="bds-divider">TO BE FILLED BY MAINTENANCE / TOOL ROOM :-</div>
            </div>

            {/* actions — Submit disabled till EVERY field filled (like 9965) */}
            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap",
              padding: "14px 16px", borderTop: "1.5px solid #0f172a", background: "#f8fafc" }}>
              <button onClick={submit} disabled={saving || !complete}
                style={{ ...btnPrimary, opacity: (saving || !complete) ? 0.5 : 1,
                  cursor: (saving || !complete) ? "not-allowed" : "pointer" }}
                title={complete ? "" : "Saare fields bharo tabhi submit hoga (9965 jaisa)"}>
                {saving ? "Submitting…" : "Submit → Maintenance"}
              </button>
              <button onClick={() => setSlip(null)} style={btnGhost}>Cancel</button>
              {!complete && (
                <span style={{ fontSize: 12, color: "#b45309", fontWeight: 700 }}>
                  Baaki bharein: {missing.map(k => LBL[k] || k).join(", ")}
                </span>
              )}
            </div>
          </div>
          <style>{BDS_CSS}</style>
        </div>
      )}

      {toast && (
        <div style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)",
          background: toast.kind === "err" ? "#dc2626" : "#16a34a", color: "#fff", padding: "10px 18px",
          borderRadius: 10, fontWeight: 600, boxShadow: "0 6px 20px rgba(0,0,0,.18)", zIndex: 9500 }}>{toast.msg}</div>
      )}
    </div>
  );
}

// ── BdsCell / BdsRow — copied from the 9965 ClosureFormModal ──────────────
function Cell({ label, value, type = "text", readOnly, onChange, wrap, options }) {
  const up = (v) => (type === "text" && typeof v === "string" ? v.toUpperCase() : v);
  return (
    <div className="bds-cell">
      <div className="bds-cell-label">{label} :-</div>
      <div className="bds-cell-input">
        {options?.length ? (
          <select value={value || ""} disabled={readOnly}
            onChange={(e) => onChange?.(e.target.value)}
            style={{ width: "100%", height: "100%", minHeight: 34, padding: "6px 10px", boxSizing: "border-box",
              border: "none", background: "transparent", font: "inherit", fontSize: 12, fontWeight: 600,
              color: "#0f172a", outline: "none", cursor: readOnly ? "default" : "pointer" }}>
            <option value="">— select —</option>
            {options.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        ) : wrap ? (
          <div style={{ padding: "6px 10px", fontSize: 12, fontWeight: 600, color: "#0f172a", lineHeight: 1.35,
            whiteSpace: "normal", overflowWrap: "break-word", display: "flex", alignItems: "center", minHeight: 34 }}>
            {value ?? ""}
          </div>
        ) : (
          <input type={type} value={value ?? ""} disabled={readOnly}
            onChange={(e) => onChange?.(up(e.target.value))} />
        )}
      </div>
    </div>
  );
}
function Row({ label, value, readOnly, onChange }) {
  return (
    <div className="bds-row">
      <div className="bds-row-label">{label}</div>
      <div className="bds-row-input">
        <textarea value={value || ""} disabled={readOnly} rows={2}
          onChange={(e) => onChange?.(e.target.value.toUpperCase())} />
      </div>
    </div>
  );
}

const btnPrimary = { background: "#2563eb", color: "#fff", border: "1px solid #2563eb", borderRadius: 8,
  padding: "10px 20px", fontSize: 14, fontWeight: 700, cursor: "pointer" };
const btnGhost = { background: "#fff", color: "#2563eb", border: "1px solid #bfdbfe", borderRadius: 8,
  padding: "8px 14px", fontSize: 13, cursor: "pointer", fontWeight: 600 };

// ── the Toyota Boshoku BREAK DOWN SLIP CSS (from 9965) ────────────────────
const BDS_CSS = `
  .bds-letterhead { display:flex; align-items:stretch; background:#fff; border-bottom:2px solid #0f172a; position:relative; }
  .bds-logo { width:120px; padding:8px 10px; border-right:1.5px solid #0f172a; display:flex; flex-direction:column; align-items:center; gap:2px; background:#fff; }
  .bds-logo-tb { font-family:'Barlow Condensed',sans-serif; font-size:34px; font-weight:900; color:#dc2626; line-height:1; }
  .bds-logo-sub { font-size:8px; font-weight:700; color:#0f172a; letter-spacing:.05em; text-align:center; line-height:1.2; }
  .bds-letter-title { flex:1; padding:6px 12px; text-align:center; display:flex; flex-direction:column; justify-content:center; }
  .bds-company { font-size:18px; font-weight:800; color:#0f172a; letter-spacing:.04em; }
  .bds-doc-title { font-size:14px; font-weight:700; color:#0f172a; letter-spacing:.06em; margin-top:2px; }
  .bds-close-x { width:46px; cursor:pointer; display:flex; align-items:center; justify-content:center; font-size:30px; color:#64748b; border-left:1.5px solid #0f172a; font-family:Arial; line-height:1; }
  .bds-close-x:hover { background:#fee2e2; color:#dc2626; }
  .bds-body { padding:0; max-height:74vh; overflow-y:auto; background:#fff; font-family:'Barlow',sans-serif; color:#0f172a; font-size:11px; }
  .bds-grid { display:grid; border-top:1.5px solid #0f172a; border-left:1.5px solid #0f172a; }
  .bds-grid-3 { grid-template-columns: 1fr 1fr 1fr; }
  .bds-cell { display:flex; align-items:stretch; border-right:1.5px solid #0f172a; border-bottom:1.5px solid #0f172a; min-height:36px; }
  .bds-cell-label { background:#f1f5f9; padding:6px 8px; font-size:10px; font-weight:800; color:#0f172a; letter-spacing:.02em; min-width:140px; display:flex; align-items:center; border-right:1px solid #cbd5e1; }
  .bds-cell-input { flex:1; padding:0; }
  .bds-cell-input input { width:100%; height:100%; min-height:34px; border:none; outline:none; background:transparent; padding:6px 10px; font-size:12px; font-weight:600; color:#0f172a; font-family:inherit; box-sizing:border-box; }
  .bds-cell-input input:disabled { color:#0f172a; opacity:1; }
  .bds-cat-head { display:grid; grid-template-columns: 1fr 110px; background:#f1f5f9; border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a; padding:6px 10px; font-weight:800; font-size:11px; color:#0f172a; align-items:center; }
  .bds-cat-tickdown { text-align:center; font-size:9px; border-left:1px solid #cbd5e1; padding-left:8px; }
  .bds-cat-row { display:grid; grid-template-columns: 160px 1fr 110px; border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a; border-top:1.5px solid #0f172a; min-height:30px; }
  .bds-cat-cell-code { padding:6px 10px; font-weight:800; font-size:11px; background:#f8fafc; border-right:1px solid #cbd5e1; display:flex; align-items:center; }
  .bds-cat-cell-desc { padding:6px 10px; font-size:11px; color:#0f172a; border-right:1px solid #cbd5e1; display:flex; align-items:center; }
  .bds-cat-cell-tick { display:flex; align-items:center; justify-content:center; }
  .bds-cat-cell-tick input { width:18px; height:18px; cursor:pointer; }
  .bds-row { display:grid; grid-template-columns: 220px 1fr; border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a; border-top:1.5px solid #0f172a; min-height:60px; }
  .bds-row-label { padding:6px 10px; background:#f1f5f9; font-weight:800; font-size:10px; color:#0f172a; letter-spacing:.02em; border-right:1px solid #cbd5e1; display:flex; align-items:center; }
  .bds-row-input { padding:0; }
  .bds-row-input textarea { width:100%; height:100%; min-height:60px; border:none; outline:none; background:transparent; padding:6px 10px; font-size:12px; font-weight:500; color:#0f172a; font-family:inherit; resize:vertical; box-sizing:border-box; }
  .bds-divider { padding:6px 10px; background:#fee2e2; color:#991b1b; font-weight:800; font-size:11px; letter-spacing:.04em; border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a; border-top:1.5px solid #0f172a; border-bottom:1.5px solid #0f172a; }
`;
