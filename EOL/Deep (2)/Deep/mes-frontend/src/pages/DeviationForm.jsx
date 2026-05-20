/* ───────────────────────────────────────────────────────────────────
 * DeviationForm.jsx
 * ───────────────────────────────────────────────────────────────────
 * Online Deviation form modal — used in two modes:
 *
 *   "raise"   Maintenance creates a brand-new deviation against a
 *             breakdown (or stand-alone).  Upper half editable.
 *
 *   "review"  Quality Sec Head reviews a PENDING_QA deviation.
 *             All fields read-only EXCEPT HOD-Quality sign / note.
 *             Approve / Reject buttons in footer.
 *
 *   "view"    Pure read-only viewer for closed / rejected / extended
 *             deviations.
 *
 * Layout mirrors the paper "DEVIATION FORM" 1:1 (per
 * PY EMAIL ALERT CONTENT WITH DEVIATION.xlsx → 'Deviation Form' sheet)
 * so a printed copy still reads identical to today's hard-copy.
 */
import { useEffect, useState } from "react";

const API = "";
const api = {
  async get(path, token) {
    const r = await fetch(API + path, { headers: { Authorization: `Bearer ${token}` } });
    if (!r.ok) throw new Error(await r.text() || `HTTP ${r.status}`);
    return r.json();
  },
  async post(path, body, token) {
    const r = await fetch(API + path, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
    return r.json();
  },
  async put(path, body, token) {
    const r = await fetch(API + path, {
      method: "PUT",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
    return r.json();
  },
};

const BLANK_ACTION = { action: "", resp: "", deptt: "", tgt_date: "", approver: "", remarks: "" };

function buildEmpty(prev = {}) {
  return {
    breakdown_id:           prev.breakdown_id           || null,
    line_id:                prev.line_id                || null,
    line_name:              prev.line_name              || "",
    zone_id:                prev.zone_id                || null,
    zone_name:              prev.zone_name              || "",
    machine_no:             prev.machine_no             || "",
    machine_name:           prev.machine_name           || "",
    category:               prev.category               || "",
    process_name:           prev.process_name           || "",
    process_no:             prev.process_no             || "",
    srv_no:                 prev.srv_no                 || "",
    deviation_qty:          prev.deviation_qty          || "",
    deviation_upto_qty:     prev.deviation_upto_qty     || "",
    deviation_upto_date:    prev.deviation_upto_date    || "",
    initiated_by:           prev.initiated_by           || "",
    reason:                 prev.reason                 || "",
    requirement:            prev.requirement            || "",
    observation:            prev.observation            || "",
    root_cause_occurrence:  prev.root_cause_occurrence  || "",
    root_cause_detection:   prev.root_cause_detection   || "",
    potential_consequences: prev.potential_consequences || "",
    hod_production:         prev.hod_production         || "",
    hod_production_note:    prev.hod_production_note    || "",
    hod_quality:            prev.hod_quality            || "",
    hod_quality_note:       prev.hod_quality_note       || "",
    containment_actions:    Array.isArray(prev.containment_actions) && prev.containment_actions.length
                              ? prev.containment_actions
                              : Array.from({length:3}, () => ({...BLANK_ACTION})),
    permanent_actions:      Array.isArray(prev.permanent_actions) && prev.permanent_actions.length
                              ? prev.permanent_actions
                              : Array.from({length:3}, () => ({...BLANK_ACTION})),
    extensions:             Array.isArray(prev.extensions) ? prev.extensions : [],
    closure_remarks:        prev.closure_remarks        || "",
  };
}

export default function DeviationForm({ deviation, token, mode = "raise",
                                          onClose, onSaved }) {
  const [data, setData] = useState(() => buildEmpty(deviation || {}));
  const [saving, setSaving] = useState(false);
  const [extOpen, setExtOpen] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);

  const isView   = mode === "view";
  const isReview = mode === "review";
  const isRaise  = mode === "raise";
  const readOnly = isView;

  const set    = (k, v) => setData(d => ({...d, [k]: v}));
  const setArr = (group, i, k, v) => setData(d => {
    const arr = [...(d[group] || [])];
    arr[i] = {...(arr[i] || BLANK_ACTION), [k]: v};
    return {...d, [group]: arr};
  });
  const addRow = (group) => setData(d => ({...d, [group]: [...(d[group] || []), {...BLANK_ACTION}]}));

  // Save (raise) or update.
  const save = async () => {
    setSaving(true);
    try {
      let saved;
      if (isRaise && !deviation?.id) {
        saved = await api.post("/api/quality/deviations", data, token);
      } else {
        saved = await api.put(`/api/quality/deviations/${deviation.id}`, data, token);
      }
      onSaved?.(saved);
    } catch (e) { alert(e.message || "Save failed"); }
    finally   { setSaving(false); }
  };

  const approve = async () => {
    setSaving(true);
    try {
      await api.post(`/api/quality/deviations/${deviation.id}/approve`,
                     { hod_quality: data.hod_quality, hod_quality_note: data.hod_quality_note },
                     token);
      onSaved?.();
    } catch (e) { alert(e.message || "Approve failed"); }
    finally   { setSaving(false); }
  };

  const reject = async (remarks) => {
    setSaving(true);
    try {
      await api.post(`/api/quality/deviations/${deviation.id}/reject`,
                     { hod_quality: data.hod_quality, rejection_reason: remarks },
                     token);
      onSaved?.();
    } catch (e) { alert(e.message || "Reject failed"); }
    finally   { setSaving(false); setRejectOpen(false); }
  };

  const addExtension = async (ext) => {
    setSaving(true);
    try {
      await api.post(`/api/quality/deviations/${deviation.id}/extend`, ext, token);
      onSaved?.();
    } catch (e) { alert(e.message || "Extension failed"); }
    finally   { setSaving(false); setExtOpen(false); }
  };

  const closeDeviation = async () => {
    setSaving(true);
    try {
      await api.post(`/api/quality/deviations/${deviation.id}/close`,
                     { closure_remarks: data.closure_remarks }, token);
      onSaved?.();
    } catch (e) { alert(e.message || "Close failed"); }
    finally   { setSaving(false); }
  };

  const printForm = () => {
    const sheet = document.getElementById("dev-sheet");
    if (!sheet) return window.print();
    const w = window.open("", "_blank", "width=1200,height=900");
    w.document.write(`
      <html><head><title>Deviation ${deviation?.dev_no || "draft"}</title>
      <style>
        @page { size: A4 portrait; margin: 8mm; }
        * { box-sizing: border-box; font-family: Arial, sans-serif; }
        body { margin: 0; color: #000; font-size: 9px; }
        .dev-sec { border: 1.2px solid #000; }
        .dev-row { display: flex; }
        .dev-cell { border-right: 1px solid #000; border-bottom: 1px solid #000;
                    padding: 2px 4px; flex: 1; }
        .dev-cell:last-child { border-right: none; }
        .dev-row:last-child .dev-cell { border-bottom: none; }
        .lbl { background: #f3f4f6; font-weight: 700; }
        .title { background: #1f2937; color: #fff; font-weight: 800; text-align: center;
                  padding: 4px; text-transform: uppercase; }
        input, textarea { border: none; background: transparent; font: inherit;
                          color: #000; padding: 0; width: 100%; resize: none; }
        table { width: 100%; border-collapse: collapse; }
        td, th { border: 1px solid #000; padding: 2px 4px; font-size: 8.5px; }
        th { background: #f3f4f6; font-weight: 700; text-align: center; }
      </style></head><body>${sheet.outerHTML}</body></html>`);
    w.document.close();
    setTimeout(() => { w.focus(); w.print(); }, 300);
  };

  const status = deviation?.status;

  return (
    <div onClick={onClose} style={{
      position:"fixed", inset:0, background:"rgba(15,23,42,.55)",
      backdropFilter:"blur(2px)", zIndex:9000,
      display:"flex", alignItems:"flex-start", justifyContent:"center",
      overflowY:"auto", padding:"24px 12px",
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        width:"100%", maxWidth:1100, background:"#fff", borderRadius:10,
        boxShadow:"0 20px 60px rgba(0,0,0,.35)", overflow:"hidden",
        display:"flex", flexDirection:"column", maxHeight:"95vh",
      }}>
        {/* Header */}
        <div style={{display:"flex", alignItems:"stretch", borderBottom:"2px solid #0f172a", flexShrink:0}}>
          <div style={{flex:1, padding:"12px 18px"}}>
            <div style={{fontFamily:"'Barlow Condensed',sans-serif", fontSize:20, fontWeight:800, color:"#0f172a"}}>
              {deviation?.id
                ? `Deviation ${deviation.dev_no || "#" + deviation.id}`
                : "Raise Online Deviation"}
              {status && (
                <span style={{marginLeft:10, fontSize:11, padding:"2px 9px", borderRadius:99,
                                background:"#fef3c7", color:"#854d0e", fontWeight:700,
                                letterSpacing:".1em", textTransform:"uppercase"}}>
                  {status.replace("_", " ")}
                </span>
              )}
            </div>
            <div style={{fontSize:11, color:"#64748b", marginTop:2}}>
              Toyota Boshoku Device India — Online Deviation Form
            </div>
          </div>
          <div onClick={onClose} title="Close"
               style={{width:42, cursor:"pointer", display:"flex",
                         alignItems:"center", justifyContent:"center",
                         fontSize:28, color:"#64748b", borderLeft:"1.5px solid #0f172a"}}>×</div>
        </div>

        {/* Form sheet — scrollable */}
        <div id="dev-sheet" style={{
          padding:"14px 18px", overflowY:"auto", flex:1, background:"#fff",
          fontFamily:"Arial,sans-serif", fontSize:11, color:"#0f172a",
        }}>
          <style>{`
            .dev-sec { border: 1.4px solid #0f172a; margin-bottom: 4px; }
            .dev-row { display: flex; }
            .dev-cell { border-right: 1px solid #0f172a; border-bottom: 1px solid #0f172a;
                        padding: 3px 6px; font-size: 11px; min-height: 24px;
                        flex: 1; display: flex; align-items: center; }
            .dev-cell:last-child { border-right: none; }
            .dev-row:last-child .dev-cell { border-bottom: none; }
            .dev-cell.lbl { background: #f3f4f6; font-weight: 700; color: #0f172a; }
            .dev-cell.title { background: #0f172a; color: #fff; font-weight: 800;
                              text-align: center; padding: 6px; font-size: 12px;
                              letter-spacing: .05em; text-transform: uppercase;
                              justify-content: center; }
            .dev-cell input, .dev-cell textarea { border: none; background: transparent;
                                                  font: inherit; color: #0f172a;
                                                  padding: 0; width: 100%; outline: none;
                                                  resize: vertical; }
            .dev-cell textarea { min-height: 22px; padding: 2px 0; }
            .dev-cell input:disabled, .dev-cell textarea:disabled { color: #475569; }
            .dev-tbl { width: 100%; border-collapse: collapse; }
            .dev-tbl th, .dev-tbl td { border: 1px solid #0f172a; padding: 3px 5px;
                                       font-size: 10px; vertical-align: middle; }
            .dev-tbl th { background: #f3f4f6; font-weight: 700; text-align: center; }
            .dev-tbl td input, .dev-tbl td textarea { width: 100%; border: none;
                                                     background: transparent; font: inherit;
                                                     padding: 0; outline: none; resize: none; }
          `}</style>

          {/* Header row */}
          <div className="dev-sec">
            <div className="dev-row">
              <div className="dev-cell lbl" style={{flex:1.2, fontSize:13, justifyContent:"center"}}>
                TOYOTA BOSHOKU DEVICE INDIA PVT. LTD.
              </div>
              <div className="dev-cell lbl" style={{flex:0.3}}>DATE :-</div>
              <div className="dev-cell" style={{flex:0.4}}>
                {deviation?.created_at ? new Date(deviation.created_at).toLocaleDateString() : new Date().toLocaleDateString()}
              </div>
            </div>
            <div className="dev-row">
              <div className="dev-cell title" style={{flex:1.2}}>DEVIATION FORM</div>
              <div className="dev-cell lbl" style={{flex:0.3}}>DEV. NO.</div>
              <div className="dev-cell" style={{flex:0.4, fontFamily:"monospace", fontWeight:700}}>
                {deviation?.dev_no || "(auto on save)"}
              </div>
            </div>
            <div className="dev-row">
              <div className="dev-cell lbl" style={{flex:0.18}}>Category</div>
              <div className="dev-cell" style={{flex:0.3}}>
                <input type="text" value={data.category} disabled={readOnly}
                       onChange={e => set("category", e.target.value)}/>
              </div>
              <div className="dev-cell lbl" style={{flex:0.15}}>Process</div>
              <div className="dev-cell" style={{flex:0.3}}>
                <input type="text" value={data.process_name} disabled={readOnly}
                       onChange={e => set("process_name", e.target.value)}/>
              </div>
              <div className="dev-cell lbl" style={{flex:0.13}}>Process No.</div>
              <div className="dev-cell" style={{flex:0.18}}>
                <input type="text" value={data.process_no} disabled={readOnly}
                       onChange={e => set("process_no", e.target.value)}/>
              </div>
              <div className="dev-cell lbl" style={{flex:0.13}}>SRV No.</div>
              <div className="dev-cell" style={{flex:0.18}}>
                <input type="text" value={data.srv_no} disabled={readOnly}
                       onChange={e => set("srv_no", e.target.value)}/>
              </div>
            </div>
            <div className="dev-row">
              <div className="dev-cell lbl" style={{flex:0.18}}>Deviation Qty.</div>
              <div className="dev-cell" style={{flex:0.18}}>
                <input type="number" value={data.deviation_qty} disabled={readOnly}
                       onChange={e => set("deviation_qty", e.target.value)}/>
              </div>
              <div className="dev-cell lbl" style={{flex:0.18}}>Deviation Upto</div>
              <div className="dev-cell" style={{flex:0.16}}>
                <input type="number" placeholder="qty" value={data.deviation_upto_qty} disabled={readOnly}
                       onChange={e => set("deviation_upto_qty", e.target.value)}/>
              </div>
              <div className="dev-cell" style={{flex:0.16}}>
                <input type="date" value={data.deviation_upto_date} disabled={readOnly}
                       onChange={e => set("deviation_upto_date", e.target.value)}/>
              </div>
              <div className="dev-cell lbl" style={{flex:0.16}}>Initiated By</div>
              <div className="dev-cell" style={{flex:0.18}}>
                <input type="text" value={data.initiated_by} disabled={readOnly}
                       onChange={e => set("initiated_by", e.target.value)}/>
              </div>
            </div>
            <div className="dev-row">
              <div className="dev-cell lbl" style={{flex:0.18}}>Reason for Deviation</div>
              <div className="dev-cell" style={{flex:0.82}}>
                <textarea rows={2} value={data.reason} disabled={readOnly}
                          onChange={e => set("reason", e.target.value)}/>
              </div>
            </div>
          </div>

          {/* Non-conformance */}
          <div className="dev-sec">
            <div className="dev-row"><div className="dev-cell title">Details of Non-Conformance</div></div>
            <div className="dev-row">
              <div className="dev-cell lbl" style={{flex:0.18}}>1. Requirement</div>
              <div className="dev-cell" style={{flex:0.82}}>
                <textarea rows={2} value={data.requirement} disabled={readOnly}
                          onChange={e => set("requirement", e.target.value)}/>
              </div>
            </div>
            <div className="dev-row">
              <div className="dev-cell lbl" style={{flex:0.18}}>2. Observation</div>
              <div className="dev-cell" style={{flex:0.82}}>
                <textarea rows={2} value={data.observation} disabled={readOnly}
                          onChange={e => set("observation", e.target.value)}/>
              </div>
            </div>
          </div>

          {/* Root cause */}
          <div className="dev-sec">
            <div className="dev-row"><div className="dev-cell title">Root Cause &amp; Functional Analysis</div></div>
            <div className="dev-row">
              <div className="dev-cell lbl" style={{flex:0.25}}>Root Cause for Non-Conformance (Occurrence)</div>
              <div className="dev-cell" style={{flex:0.75}}>
                <textarea rows={2} value={data.root_cause_occurrence} disabled={readOnly}
                          onChange={e => set("root_cause_occurrence", e.target.value)}/>
              </div>
            </div>
            <div className="dev-row">
              <div className="dev-cell lbl" style={{flex:0.25}}>Root Cause for Non-Conformance (Detection)</div>
              <div className="dev-cell" style={{flex:0.75}}>
                <textarea rows={2} value={data.root_cause_detection} disabled={readOnly}
                          onChange={e => set("root_cause_detection", e.target.value)}/>
              </div>
            </div>
            <div className="dev-row">
              <div className="dev-cell lbl" style={{flex:0.25}}>Potential Consequences (Functional Analysis)</div>
              <div className="dev-cell" style={{flex:0.75}}>
                <textarea rows={2} value={data.potential_consequences} disabled={readOnly}
                          onChange={e => set("potential_consequences", e.target.value)}/>
              </div>
            </div>
          </div>

          {/* Sign-offs */}
          <div className="dev-sec">
            <div className="dev-row">
              <div className="dev-cell lbl" style={{flex:0.18}}>HOD Production</div>
              <div className="dev-cell" style={{flex:0.32}}>
                <input type="text" value={data.hod_production} disabled={readOnly}
                       onChange={e => set("hod_production", e.target.value)}/>
              </div>
              <div className="dev-cell lbl" style={{flex:0.1}}>Note</div>
              <div className="dev-cell" style={{flex:0.4}}>
                <input type="text" value={data.hod_production_note} disabled={readOnly}
                       onChange={e => set("hod_production_note", e.target.value)}/>
              </div>
            </div>
            <div className="dev-row">
              <div className="dev-cell lbl" style={{flex:0.18}}>HOD Quality</div>
              <div className="dev-cell" style={{flex:0.32}}>
                <input type="text" value={data.hod_quality}
                       disabled={readOnly && !isReview}
                       onChange={e => set("hod_quality", e.target.value)}/>
              </div>
              <div className="dev-cell lbl" style={{flex:0.1}}>Note</div>
              <div className="dev-cell" style={{flex:0.4}}>
                <input type="text" value={data.hod_quality_note}
                       disabled={readOnly && !isReview}
                       onChange={e => set("hod_quality_note", e.target.value)}/>
              </div>
            </div>
          </div>

          {/* Containment Action */}
          <div className="dev-sec">
            <div className="dev-row"><div className="dev-cell title">Interim Containment Action</div></div>
            <div style={{padding:"4px"}}>
              <table className="dev-tbl">
                <thead>
                  <tr>{["S.No","Action","Responsible","Deptt.","Tgt. Date","Approval Authority","Remarks"].map(h =>
                    <th key={h}>{h}</th>)}</tr>
                </thead>
                <tbody>
                  {data.containment_actions.map((r,i) => (
                    <tr key={i}>
                      <td style={{textAlign:"center", fontWeight:700}}>{i+1}</td>
                      <td><input type="text" value={r.action} disabled={readOnly} onChange={e=>setArr("containment_actions",i,"action",e.target.value)}/></td>
                      <td><input type="text" value={r.resp} disabled={readOnly} onChange={e=>setArr("containment_actions",i,"resp",e.target.value)}/></td>
                      <td><input type="text" value={r.deptt} disabled={readOnly} onChange={e=>setArr("containment_actions",i,"deptt",e.target.value)}/></td>
                      <td><input type="date" value={r.tgt_date} disabled={readOnly} onChange={e=>setArr("containment_actions",i,"tgt_date",e.target.value)}/></td>
                      <td><input type="text" value={r.approver} disabled={readOnly} onChange={e=>setArr("containment_actions",i,"approver",e.target.value)}/></td>
                      <td><input type="text" value={r.remarks} disabled={readOnly} onChange={e=>setArr("containment_actions",i,"remarks",e.target.value)}/></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!readOnly && (
                <button type="button" onClick={() => addRow("containment_actions")}
                        style={{marginTop:6, padding:"4px 12px", border:"1px solid #0f172a",
                                background:"#fff", borderRadius:6, fontSize:11, fontWeight:700,
                                cursor:"pointer"}}>
                  + Add Row
                </button>
              )}
            </div>
          </div>

          {/* Permanent Corrective Action */}
          <div className="dev-sec">
            <div className="dev-row"><div className="dev-cell title">Permanent Corrective Action</div></div>
            <div style={{padding:"4px"}}>
              <table className="dev-tbl">
                <thead>
                  <tr>{["S.No","Action","Responsible","Deptt.","Tgt. Date","Approval Authority","Remarks"].map(h =>
                    <th key={h}>{h}</th>)}</tr>
                </thead>
                <tbody>
                  {data.permanent_actions.map((r,i) => (
                    <tr key={i}>
                      <td style={{textAlign:"center", fontWeight:700}}>{i+1}</td>
                      <td><input type="text" value={r.action} disabled={readOnly} onChange={e=>setArr("permanent_actions",i,"action",e.target.value)}/></td>
                      <td><input type="text" value={r.resp} disabled={readOnly} onChange={e=>setArr("permanent_actions",i,"resp",e.target.value)}/></td>
                      <td><input type="text" value={r.deptt} disabled={readOnly} onChange={e=>setArr("permanent_actions",i,"deptt",e.target.value)}/></td>
                      <td><input type="date" value={r.tgt_date} disabled={readOnly} onChange={e=>setArr("permanent_actions",i,"tgt_date",e.target.value)}/></td>
                      <td><input type="text" value={r.approver} disabled={readOnly} onChange={e=>setArr("permanent_actions",i,"approver",e.target.value)}/></td>
                      <td><input type="text" value={r.remarks} disabled={readOnly} onChange={e=>setArr("permanent_actions",i,"remarks",e.target.value)}/></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!readOnly && (
                <button type="button" onClick={() => addRow("permanent_actions")}
                        style={{marginTop:6, padding:"4px 12px", border:"1px solid #0f172a",
                                background:"#fff", borderRadius:6, fontSize:11, fontWeight:700,
                                cursor:"pointer"}}>
                  + Add Row
                </button>
              )}
            </div>
          </div>

          {/* Extensions (read-only list) */}
          {data.extensions.length > 0 && (
            <div className="dev-sec">
              <div className="dev-row"><div className="dev-cell title">Extensions of Deviation</div></div>
              <div style={{padding:"4px"}}>
                <table className="dev-tbl">
                  <thead>
                    <tr>{["#","From Qty/Date","To Qty/Date","Reason","HOD Concerned","HOD Quality","HOD Operation","Status"].map(h =>
                      <th key={h}>{h}</th>)}</tr>
                  </thead>
                  <tbody>
                    {data.extensions.map((e,i) => (
                      <tr key={i}>
                        <td style={{textAlign:"center"}}>{i+1}</td>
                        <td>{e.from_qty_date || "—"}</td>
                        <td>{e.to_qty_date || "—"}</td>
                        <td>{e.reason || "—"}</td>
                        <td>{e.hod_concerned || "—"}</td>
                        <td>{e.hod_quality || "—"}</td>
                        <td>{e.hod_operation || "—"}</td>
                        <td>{e.decision || "PENDING"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Closure remarks (only relevant after approval) */}
          {(status === "APPROVED" || status === "EXTENDED" || status === "CLOSED") && (
            <div className="dev-sec">
              <div className="dev-row">
                <div className="dev-cell lbl" style={{flex:0.18}}>Closure Remarks</div>
                <div className="dev-cell" style={{flex:0.82}}>
                  <textarea rows={2} value={data.closure_remarks}
                             disabled={status === "CLOSED"}
                             onChange={e => set("closure_remarks", e.target.value)}/>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{padding:"10px 18px", background:"#f8fafc", borderTop:"1px solid #e2e8f0",
                       display:"flex", alignItems:"center", justifyContent:"space-between",
                       gap:10, flexWrap:"wrap", flexShrink:0}}>
          <div style={{fontSize:11, color:"#64748b"}}>
            {isRaise   && "Maintenance fills upper half. Quality reviews next."}
            {isReview  && "Quality Sec Head — review fields above, approve or reject below."}
            {isView    && "Read-only viewer."}
          </div>
          <div style={{display:"flex", gap:8, flexWrap:"wrap"}}>
            <button onClick={printForm}
                    style={{background:"#fff", color:"#0f172a", border:"1.5px solid #0f172a",
                             padding:"7px 14px", borderRadius:8, fontWeight:700, fontSize:12, cursor:"pointer"}}>
              🖨 Print
            </button>
            <button onClick={onClose}
                    style={{background:"#fff", color:"#475569", border:"1.5px solid #e2e8f0",
                             padding:"7px 14px", borderRadius:8, fontWeight:700, fontSize:12, cursor:"pointer"}}>
              {readOnly ? "Close" : "Cancel"}
            </button>

            {isRaise && (
              <button onClick={save} disabled={saving}
                      style={{background:"linear-gradient(135deg,#1e40af,#2563eb)", color:"#fff",
                               border:"none", padding:"7px 16px", borderRadius:8, fontWeight:800,
                               fontSize:12, cursor:"pointer"}}>
                {saving ? "Saving…" : (deviation?.id ? "Update" : "Raise Deviation")}
              </button>
            )}

            {isReview && (
              <>
                <button onClick={() => setRejectOpen(true)} disabled={saving}
                        className="qd-btn qd-btn-deny"
                        style={{background:"#fff", color:"#dc2626", border:"1.5px solid #dc2626",
                                 padding:"7px 16px", borderRadius:8, fontWeight:800, fontSize:12, cursor:"pointer"}}>
                  ✗ Reject
                </button>
                <button onClick={approve} disabled={saving}
                        style={{background:"linear-gradient(135deg,#16a34a,#15803d)", color:"#fff",
                                 border:"none", padding:"7px 16px", borderRadius:8, fontWeight:800,
                                 fontSize:12, cursor:"pointer"}}>
                  {saving ? "Approving…" : "✓ Approve"}
                </button>
              </>
            )}

            {(status === "APPROVED" || status === "EXTENDED") && (
              <>
                <button onClick={() => setExtOpen(true)} disabled={saving}
                        style={{background:"#fff", color:"#0e7490", border:"1.5px solid #0e7490",
                                 padding:"7px 14px", borderRadius:8, fontWeight:700, fontSize:12, cursor:"pointer"}}>
                  + Extension
                </button>
                <button onClick={closeDeviation} disabled={saving}
                        style={{background:"#fff", color:"#475569", border:"1.5px solid #475569",
                                 padding:"7px 14px", borderRadius:8, fontWeight:700, fontSize:12, cursor:"pointer"}}>
                  Close Deviation
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Reject prompt */}
      {rejectOpen && (
        <div onClick={() => setRejectOpen(false)} style={{
          position:"fixed", inset:0, background:"rgba(0,0,0,.6)", zIndex:9100,
          display:"flex", alignItems:"center", justifyContent:"center"
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            background:"#fff", borderRadius:12, padding:24, minWidth:440,
          }}>
            <div style={{fontSize:16, fontWeight:800, marginBottom:8, color:"#dc2626"}}>
              Reject Deviation
            </div>
            <textarea autoFocus id="dev-reject-reason" rows={4}
                       placeholder="Reason for rejection (required)"
                       style={{width:"100%", padding:10, borderRadius:8,
                                border:"1.5px solid #e2e8f0", fontSize:13,
                                fontFamily:"inherit", boxSizing:"border-box"}}/>
            <div style={{display:"flex", gap:10, justifyContent:"flex-end", marginTop:14}}>
              <button onClick={() => setRejectOpen(false)}
                      style={{padding:"7px 14px", border:"1.5px solid #e2e8f0",
                               background:"#fff", borderRadius:8, fontWeight:700, fontSize:12,
                               cursor:"pointer"}}>
                Cancel
              </button>
              <button onClick={() => {
                const v = document.getElementById("dev-reject-reason").value.trim();
                if (!v) { alert("Reason required"); return; }
                reject(v);
              }} disabled={saving}
                      style={{padding:"7px 14px", border:"none",
                               background:"#dc2626", color:"#fff", borderRadius:8,
                               fontWeight:800, fontSize:12, cursor:"pointer"}}>
                Confirm Reject
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Extension prompt (QA Head) */}
      {extOpen && (
        <ExtensionPrompt onCancel={() => setExtOpen(false)}
                          onConfirm={addExtension}/>
      )}
    </div>
  );
}

function ExtensionPrompt({ onCancel, onConfirm }) {
  const [ext, setExt] = useState({
    from_qty_date: "", to_qty_date: "", reason: "",
    hod_concerned: "", hod_quality: "", hod_operation: "",
    decision: "APPROVED",
  });
  return (
    <div onClick={onCancel} style={{
      position:"fixed", inset:0, background:"rgba(0,0,0,.6)", zIndex:9100,
      display:"flex", alignItems:"center", justifyContent:"center"
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        background:"#fff", borderRadius:12, padding:24, minWidth:480,
      }}>
        <div style={{fontSize:16, fontWeight:800, marginBottom:14, color:"#0f172a"}}>
          Grant Extension — QA Head
        </div>
        <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:10}}>
          {[
            ["From Qty / Date", "from_qty_date", "text"],
            ["To Qty / Date",   "to_qty_date",   "text"],
            ["Reason",          "reason",        "text"],
            ["HOD Concerned",   "hod_concerned", "text"],
            ["HOD Quality",     "hod_quality",   "text"],
            ["HOD Operation",   "hod_operation", "text"],
          ].map(([lbl, k, t]) => (
            <div key={k} style={{display:"flex", flexDirection:"column", gap:4}}>
              <label style={{fontSize:10, fontWeight:700, color:"#64748b",
                              letterSpacing:".06em", textTransform:"uppercase"}}>
                {lbl}
              </label>
              <input type={t} value={ext[k]} onChange={e=>setExt({...ext, [k]:e.target.value})}
                     style={{padding:8, borderRadius:6, border:"1.5px solid #e2e8f0",
                              fontSize:13, fontFamily:"inherit"}}/>
            </div>
          ))}
        </div>
        <div style={{display:"flex", gap:10, justifyContent:"flex-end", marginTop:16}}>
          <button onClick={onCancel}
                  style={{padding:"7px 14px", border:"1.5px solid #e2e8f0",
                           background:"#fff", borderRadius:8, fontWeight:700, fontSize:12,
                           cursor:"pointer"}}>
            Cancel
          </button>
          <button onClick={() => onConfirm(ext)}
                  style={{padding:"7px 14px", border:"none",
                           background:"linear-gradient(135deg,#0e7490,#0891b2)", color:"#fff", borderRadius:8,
                           fontWeight:800, fontSize:12, cursor:"pointer"}}>
            Add Extension
          </button>
        </div>
      </div>
    </div>
  );
}
