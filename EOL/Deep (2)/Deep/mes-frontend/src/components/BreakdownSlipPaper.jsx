/**
 * Read-only BREAK DOWN SLIP (TBDI / MAINT. / F / 001) — the same paper format
 * and logo as the Maintenance_DX (9965) slip: letterhead, header grid,
 * category ticks, time grid, production problem, the maintenance / tool-room
 * half, spares and the four names.  Used by Historical → Breakdown History.
 *
 * Maintenance_DX slips are loaded in full from /api/breakdown-history/slip;
 * MES entries show the fields they carry.
 */
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const MAINT_SRC = ["AUTO-MAINT", "AUTO-TOOLROOM", "MANUAL"];
const SRC_LABEL = {
  "AUTO-MAINT": "Auto slip · Maintenance", "AUTO-TOOLROOM": "Auto slip · Tool Room",
  "MANUAL": "Manual slip", "MES-LOG": "MES entry", "MES-SLIP": "MES slip",
};
const STAGE_CLR = {
  PENDING_PRODUCTION: ["#fef3c7", "#b45309"], PENDING_MAINTENANCE: ["#fee2e2", "#b91c1c"],
  COMPLETED: ["#dcfce7", "#15803d"], CLOSED: ["#dcfce7", "#15803d"], RESOLVED: ["#dcfce7", "#15803d"],
};
const FOOTER = { format_no: "TBDI / MAINT. / F / 001", rev_no: "00", rev_date: "20/03/2024" };

const txt = (v) => (v === null || v === undefined || String(v).trim() === "" ? "" : String(v));
const parseSpares = (v) => {
  if (!v) return [];
  if (Array.isArray(v)) return v;
  try { const j = JSON.parse(v); return Array.isArray(j) ? j : []; } catch { return []; }
};

function Cell({ label, value, wrap }) {
  return (
    <div className="bds-cell">
      <div className="bds-cell-label">{label} :-</div>
      <div className="bds-cell-input">
        <div className={wrap ? "bds-val bds-val-wrap" : "bds-val"}>{txt(value)}</div>
      </div>
    </div>
  );
}
function Row({ label, value }) {
  return (
    <div className="bds-row">
      <div className="bds-row-label">{label}</div>
      <div className="bds-row-input"><div className="bds-val bds-val-wrap bds-val-tall">{txt(value)}</div></div>
    </div>
  );
}
const Tick = ({ on, round }) => (
  <span className={`bds-tick${round ? " bds-tick-round" : ""}${on ? " on" : ""}`}>{on ? "✓" : ""}</span>
);

export default function BreakdownSlipPaper({ row, onClose }) {
  const { token } = useAuth();
  const isMaint = MAINT_SRC.includes(row.src);
  const [s, setS] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!isMaint) return;
    api.get(`/api/breakdown-history/slip?src=${encodeURIComponent(row.src)}&id=${row.id}`, token)
      .then(setS).catch((e) => setErr(e?.message || "Could not load the slip"));
  }, [row.src, row.id, isMaint, token]);

  const d = isMaint ? (s || {}) : {
    zone: row.zone, line: row.line, machine_no: row.machine_no, machine_name: row.machine_name,
    slip_date: row.date, shift: row.shift, model_no: row.model, category: row.category,
    mc_down_time_minutes: row.down_min, problem_reported_by_production: row.problem,
    problem_observed_by_maintenance: row.observed, action_taken_on_problem: row.action,
    spares_used: row.spares, bd_attended_by: row.attended_by, line_leader_name: row.line_leader,
  };
  const rel = String(d.problem_related_to || (row.dept === "Toolroom" ? "tool_room" : "")).toLowerCase();
  const spares = parseSpares(d.spares).filter((sp) => Object.values(sp || {}).some((v) => txt(v)));
  const spareUsed = spares.length > 0 || !!txt(d.spares_used);
  const cat = String(d.category || "").trim().toUpperCase();
  const sc = STAGE_CLR[row.stage] || ["#f1f5f9", "#475569"];
  const loading = isMaint && !s && !err;

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,.55)", zIndex: 1000,
         display: "flex", alignItems: "flex-start", justifyContent: "center", overflowY: "auto", padding: "24px 12px" }}>
      <style>{BDS_CSS}</style>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "100%", maxWidth: 980, background: "#fff",
           borderRadius: 12, boxShadow: "0 20px 60px rgba(0,0,0,.35)", overflow: "hidden" }}>
        <div className="bds-letterhead">
          <div className="bds-logo">
            <img src="/logo.jpg" alt="logo" style={{ width: "70%", height: "70%", objectFit: "contain" }}
                 onError={(e) => { e.target.style.display = "none"; }} />
            <div className="bds-logo-sub">TOYOTA BOSHOKU</div>
          </div>
          <div className="bds-letter-title">
            <div className="bds-company">TOYOTA BOSHOKU DEVICE INDIA PVT. LTD.</div>
            <div className="bds-doc-title">BREAK DOWN SLIP</div>
          </div>
          <div className="bds-status">
            <span style={{ background: sc[0], color: sc[1] }}>{row.stage_label}</span>
            <small>{SRC_LABEL[row.src] || row.src}</small>
          </div>
          <div className="bds-close-x" onClick={onClose} title="Close">×</div>
        </div>

        <div className="bds-body">
          {err && <div style={{ padding: 14, color: "#b91c1c", fontWeight: 700 }}>{err}</div>}
          {loading && <div style={{ padding: 30, textAlign: "center", color: "#94a3b8" }}>Loading slip…</div>}
          {!loading && !err && <>
            <div className="bds-grid bds-grid-3">
              <Cell label="ZONE" value={d.zone} />
              <Cell label="MACHINE NO." value={d.machine_no} />
              <Cell label="DATE" value={d.slip_date} />
              <Cell label="LINE" value={d.line} />
              <Cell label="SHIFT" value={d.shift} />
              <Cell label="LINE LEADER NAME" value={d.line_leader_name} />
              <Cell label="MACHINE OPERATOR NAME" value={d.machine_operator_name} />
              <Cell label="MACHINE NAME" value={d.machine_name} wrap />
              <Cell label="MODEL NO." value={d.model_no} />
            </div>

            <div className="bds-cat-head">
              <div>BREAK DOWN TYPE ( CATEGORY ) :-</div>
              <div className="bds-cat-tickdown">TICK DOWN<br />(✓)</div>
            </div>
            {[
              { code: "A", desc: "MACHINE OR LINE HAS STOPPED AND PRODUCTION LOSS DIRECTLY" },
              { code: "B", desc: "MACHINE RUNNING WITH PRODUCTION LOSS ( PRODUCTION EFFECTED ) ( ADJUSTMENT )" },
            ].map((c) => (
              <div key={c.code} className="bds-cat-row">
                <div className="bds-cat-cell-code">{c.code} CATEGORY B/D :-</div>
                <div className="bds-cat-cell-desc">{c.desc}</div>
                <div className="bds-cat-cell-tick"><Tick on={cat === c.code} /></div>
              </div>
            ))}

            <div className="bds-grid bds-grid-3">
              <Cell label="B/D START TIME" value={d.bd_start_time} />
              <Cell label="B/D RECEIVED TIME" value={d.bd_received_time} />
              <Cell label="RESPONSE TIME ( MIN )" value={d.response_time_minutes} />
              <Cell label="B/D OK TIME" value={d.bd_ok_time} />
              <Cell label="M/C DOWN TIME ( MIN )" value={d.mc_down_time_minutes} />
              <Cell label="FREQUENCY" value={d.frequency} />
              <Cell label="B/D START DATE" value={d.bd_start_date} />
              <Cell label="B/D END DATE" value={d.bd_end_date} />
              <div className="bds-cell" />
            </div>

            <Row label="PROBLEM REPORTED BY PRODUCTION" value={d.problem_reported_by_production} />

            <div className="bds-divider">TO BE FILLED BY MAINTENANCE/TOOL ROOM:-</div>
            <div className="bds-relto-row">
              <div className="bds-relto-label">PROBLEM RELATED TO ( PLEASE TICK ☑ )</div>
              <span className="bds-relto-opt"><Tick round on={rel.startsWith("maint")} /> MAINTENANCE</span>
              <span className="bds-relto-opt"><Tick round on={rel.includes("tool")} /> TOOL ROOM</span>
            </div>
            <div className="bds-relto-row">
              <div className="bds-relto-label">TYPE OF PROBLEM ( TICK ALL THAT APPLY )</div>
              <span className="bds-relto-opt"><Tick on={!!d.type_electrical} /> ELECTRICAL</span>
              <span className="bds-relto-opt"><Tick on={!!d.type_mechanical} /> MECHANICAL</span>
            </div>
            <Row label="ACTUAL PROBLEM OBSERVED BY MAINTENANCE / TOOL ROOM" value={d.problem_observed_by_maintenance} />
            <Row label="ACTION TAKEN ON PROBLEM" value={d.action_taken_on_problem} />
            <div className="bds-relto-row">
              <div className="bds-relto-label">SPARE USED ?</div>
              <span className="bds-relto-opt"><Tick round on={spareUsed} /> YES</span>
              <span className="bds-relto-opt"><Tick round on={!spareUsed && isMaint && row.stage === "COMPLETED"} /> NO</span>
            </div>
            {spareUsed && (
              <div className="bds-spares">
                <div className="bds-spares-head">🔧 SPARE DETAILS ( IF ANY )</div>
                {spares.length ? spares.map((sp, i) => (
                  <div key={i} className="bds-spare-row">
                    {[["spare_name", "SPARE NAME"], ["spare_model_no", "MODEL NUMBER"],
                      ["spare_cnmm_no", "SPARE ERP NUMBER"], ["spare_qty", "QUANTITY"]].map(([k, l]) => (
                      <div key={k} className="bds-spare-cell">
                        <div className="bds-spare-lbl">{l}{spares.length > 1 && k === "spare_name" ? ` ${i + 1}` : ""}</div>
                        <div className="bds-val">{txt(sp[k])}</div>
                      </div>
                    ))}
                  </div>
                )) : <div className="bds-val bds-val-wrap" style={{ padding: "6px 10px" }}>{txt(d.spares_used)}</div>}
              </div>
            )}
            <Row label="B/D ATTENDED BY" value={d.bd_attended_by} />

            <div className="bds-sign-head">
              <div>PREPARED BY :-</div>
              <div>RECEIVED BY :-</div>
              <div>HANDOVER TO :-<br /><span className="bds-sign-sub">LINE LEADER / OPERATOR</span></div>
              <div>HANDOVER TO :-<br /><span className="bds-sign-sub">QUALITY ENGINEER</span></div>
            </div>
            <div className="bds-sign-grid">
              {["prepared_by_name", "received_by_name", "line_leader_operator_name", "quality_engineer_name"].map((k) => (
                <div key={k} className="bds-sign-cell">
                  <div className="bds-sign-line"><span>NAME :-</span><b>{txt(d[k])}</b></div>
                </div>
              ))}
            </div>

            <div className="bds-docfooter">
              FORMAT NO.:- {FOOTER.format_no}
              <span style={{ display: "inline-block", width: 28 }} />REV. NO.:- {FOOTER.rev_no}
              <span style={{ display: "inline-block", width: 20 }} />REV. DATE:- {FOOTER.rev_date}
            </div>
          </>}
        </div>
        <div className="bds-footer">
          <div style={{ fontSize: 11.5, color: "#64748b" }}>
            {d.submitted_at ? `Submitted ${String(d.submitted_at).slice(0, 16)}` : ""}
            {d.production_at ? ` · Production filled ${String(d.production_at).slice(0, 16)}` : ""}
          </div>
          <button onClick={onClose} style={{ background: "#fff", color: "#2563eb", border: "1px solid #bfdbfe",
                  borderRadius: 8, padding: "8px 16px", fontSize: 13, cursor: "pointer", fontWeight: 700 }}>Close</button>
        </div>
      </div>
    </div>
  );
}

// The Toyota Boshoku BREAK DOWN SLIP CSS — same as the 9965 ClosureFormModal
// (and MES ProdBreakdownSlip), plus the lower-half blocks, read-only.
const BDS_CSS = `
  .bds-letterhead { display:flex; align-items:stretch; background:#fff; border-bottom:2px solid #0f172a; position:relative; }
  .bds-logo { width:120px; padding:8px 10px; border-right:1.5px solid #0f172a; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:2px; background:#fff; }
  .bds-logo img { max-height:56px; }
  .bds-logo-sub { font-size:8px; font-weight:700; color:#0f172a; letter-spacing:.05em; text-align:center; line-height:1.2; }
  .bds-letter-title { flex:1; padding:6px 12px; text-align:center; display:flex; flex-direction:column; justify-content:center; }
  .bds-company { font-size:18px; font-weight:800; color:#0f172a; letter-spacing:.04em; }
  .bds-doc-title { font-size:14px; font-weight:700; color:#0f172a; letter-spacing:.06em; margin-top:2px; }
  .bds-status { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:4px; padding:6px 12px; border-left:1.5px solid #0f172a; }
  .bds-status span { font-size:11px; font-weight:800; padding:3px 10px; border-radius:99px; white-space:nowrap; }
  .bds-status small { font-size:10px; color:#64748b; font-weight:700; white-space:nowrap; }
  .bds-close-x { width:46px; cursor:pointer; display:flex; align-items:center; justify-content:center; font-size:30px; color:#64748b; border-left:1.5px solid #0f172a; font-family:Arial; line-height:1; }
  .bds-close-x:hover { background:#fee2e2; color:#dc2626; }
  .bds-body { padding:0; max-height:74vh; overflow-y:auto; background:#fff; font-family:'Barlow',sans-serif; color:#0f172a; font-size:11px; }
  .bds-grid { display:grid; border-top:1.5px solid #0f172a; border-left:1.5px solid #0f172a; }
  .bds-grid-3 { grid-template-columns: 1fr 1fr 1fr; }
  .bds-cell { display:flex; align-items:stretch; border-right:1.5px solid #0f172a; border-bottom:1.5px solid #0f172a; min-height:36px; }
  .bds-cell-label { background:#f1f5f9; padding:6px 8px; font-size:10px; font-weight:800; color:#0f172a; letter-spacing:.02em; min-width:140px; display:flex; align-items:center; border-right:1px solid #cbd5e1; }
  .bds-cell-input { flex:1; padding:0; min-width:0; }
  .bds-val { padding:6px 10px; font-size:12px; font-weight:600; color:#0f172a; min-height:34px; display:flex; align-items:center; box-sizing:border-box; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .bds-val-wrap { white-space:pre-wrap; overflow-wrap:break-word; overflow:visible; line-height:1.35; }
  .bds-val-tall { min-height:60px; align-items:flex-start; font-weight:500; }
  .bds-cat-head { display:grid; grid-template-columns: 1fr 110px; background:#f1f5f9; border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a; padding:6px 10px; font-weight:800; font-size:11px; color:#0f172a; align-items:center; }
  .bds-cat-tickdown { text-align:center; font-size:9px; border-left:1px solid #cbd5e1; padding-left:8px; }
  .bds-cat-row { display:grid; grid-template-columns: 160px 1fr 110px; border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a; border-top:1.5px solid #0f172a; min-height:30px; }
  .bds-cat-cell-code { padding:6px 10px; font-weight:800; font-size:11px; background:#f8fafc; border-right:1px solid #cbd5e1; display:flex; align-items:center; }
  .bds-cat-cell-desc { padding:6px 10px; font-size:11px; color:#0f172a; border-right:1px solid #cbd5e1; display:flex; align-items:center; }
  .bds-cat-cell-tick { display:flex; align-items:center; justify-content:center; }
  .bds-tick { width:16px; height:16px; border:1.5px solid #475569; border-radius:3px; display:inline-flex; align-items:center; justify-content:center; font-size:12px; font-weight:900; color:#fff; background:#fff; box-sizing:border-box; }
  .bds-tick-round { border-radius:50%; }
  .bds-tick.on { background:#2563eb; border-color:#2563eb; }
  .bds-row { display:grid; grid-template-columns: 220px 1fr; border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a; border-top:1.5px solid #0f172a; min-height:60px; }
  .bds-row-label { padding:6px 10px; background:#f1f5f9; font-weight:800; font-size:10px; color:#0f172a; letter-spacing:.02em; border-right:1px solid #cbd5e1; display:flex; align-items:center; }
  .bds-row-input { padding:0; min-width:0; }
  .bds-divider { padding:6px 10px; background:#fee2e2; color:#991b1b; font-weight:800; font-size:11px; letter-spacing:.04em; border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a; border-top:1.5px solid #0f172a; border-bottom:1.5px solid #0f172a; }
  .bds-relto-row { display:flex; align-items:center; gap:24px; padding:8px 10px; border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a; border-top:1.5px solid #0f172a; font-size:11px; font-weight:700; flex-wrap:wrap; }
  .bds-relto-label { color:#0f172a; }
  .bds-relto-opt { display:flex; align-items:center; gap:6px; }
  .bds-spares { border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a; border-top:1.5px solid #0f172a; }
  .bds-spares-head { padding:6px 10px; background:#f1f5f9; font-weight:800; font-size:10px; color:#0f172a; letter-spacing:.02em; border-bottom:1px solid #cbd5e1; }
  .bds-spare-row { display:flex; align-items:stretch; border-bottom:1px solid #cbd5e1; }
  .bds-spare-row:last-child { border-bottom:none; }
  .bds-spare-cell { flex:1 1 0; min-width:0; border-right:1px solid #cbd5e1; display:flex; flex-direction:column; }
  .bds-spare-cell:last-child { border-right:none; }
  .bds-spare-lbl { padding:3px 8px; background:#f1f5f9; font-weight:800; font-size:9px; color:#0f172a; letter-spacing:.02em; border-bottom:1px solid #cbd5e1; white-space:nowrap; }
  .bds-sign-head { display:grid; grid-template-columns: 1fr 1fr 1fr 1fr; background:#f1f5f9; padding:6px 10px; border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a; border-top:1.5px solid #0f172a; font-size:11px; font-weight:800; }
  .bds-sign-head > div { padding:0 6px; }
  .bds-sign-head .bds-sign-sub { font-size:9px; color:#475569; font-weight:700; }
  .bds-sign-grid { display:grid; grid-template-columns: 1fr 1fr 1fr 1fr; border-left:1.5px solid #0f172a; border-right:1.5px solid #0f172a; border-top:1.5px solid #0f172a; border-bottom:1.5px solid #0f172a; }
  .bds-sign-cell { padding:6px 8px; border-right:1px solid #cbd5e1; }
  .bds-sign-cell:last-child { border-right:none; }
  .bds-sign-line { display:flex; align-items:center; gap:6px; padding:3px 0; font-size:10px; font-weight:700; color:#0f172a; }
  .bds-sign-line span { min-width:42px; }
  .bds-sign-line b { flex:1; border-bottom:1px solid #94a3b8; padding:2px 4px; font-size:11px; font-weight:600; min-height:16px; }
  .bds-docfooter { text-align:center; font-size:11px; font-weight:700; color:#111827; padding:10px 6px 8px; letter-spacing:.02em; }
  .bds-footer { display:flex; align-items:center; justify-content:space-between; gap:14px; padding:12px 18px; background:#f8fafc; border-top:1px solid #e2e8f0; flex-wrap:wrap; }
  @media (max-width: 700px) {
    .bds-grid-3 { grid-template-columns: 1fr; }
    .bds-cell-label { min-width:120px; }
    .bds-row { grid-template-columns: 1fr; }
    .bds-sign-head { display:none; }
    .bds-sign-grid { grid-template-columns: 1fr 1fr; }
    .bds-company { font-size:13px; }
  }
`;
