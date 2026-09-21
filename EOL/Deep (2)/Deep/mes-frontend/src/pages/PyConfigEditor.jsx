/* ───────────────────────────────────────────────────────────────────
 * PyConfigEditor.jsx  —  Maintenance Panel → "PY Config" sub-page
 * ───────────────────────────────────────────────────────────────────
 * Model-DRIVEN, per-STATION Poka-Yoke setup (confirmed flow):
 *   Model Master → models assigned to LINE (mes_model_mappings, model_number =
 *   the value the PLC model register D6048 reports) → for EACH station, for EACH
 *   model, each PY check reads a D-bit and has a DESIRED register value (0/1/2),
 *   for 1 or 2 registers.  Every station is INDEPENDENT.
 *
 * The user drives it with dropdowns:
 *   Line  →  Station (SS_01..SS_08 from the machine master, SS_08 = final insp.)
 *         →  Model
 *   PY    ←  picked from the PY MASTER (mes_py_master), filtered to the chosen
 *            station (all final-inspection PYs are tagged SS_08) — auto-fills
 *            py_no, py_name, D-bit, reg-count; user just sets the desired value(s).
 *
 * Backed by /api/py-config (mes_py_config).  Fully EDITABLE.  Parallel to the old
 * PY system.  Data is entered manually (the SS "PY Data" sheet is just reference).
 * ─────────────────────────────────────────────────────────────────── */
import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";

const C = { border:"#e2e8f0", text:"#0f172a", sub:"#64748b", blue:"#1e40af",
            blueBg:"#eff6ff", red:"#dc2626", green:"#16a34a", head:"#f8fafc" };
const th  = { padding:"8px 9px", fontSize:11, fontWeight:700, color:C.sub, textAlign:"left",
              borderBottom:`2px solid ${C.border}`, whiteSpace:"nowrap" };
const td  = { padding:"6px 9px", fontSize:12.5, color:C.text, borderBottom:`1px solid ${C.border}`, verticalAlign:"top" };
const inp = { width:"100%", padding:"5px 7px", fontSize:12.5, border:`1px solid ${C.border}`,
              borderRadius:5, fontFamily:"'Barlow',sans-serif", boxSizing:"border-box" };
const btn = (bg, fg="#fff") => ({ padding:"5px 11px", fontSize:12, fontWeight:600, border:"none",
              borderRadius:6, cursor:"pointer", background:bg, color:fg, fontFamily:"'Barlow',sans-serif" });
const lbl = { fontSize:10.5, fontWeight:700, color:C.sub, textTransform:"uppercase", letterSpacing:.4, marginBottom:3 };

const BLANK = { station_label:"", station_seq:"", model_number:"", model_name:"",
                py_master_id:"", py_seq:"", py_no:"", py_name:"", d_bit:"", reg_count:1,
                sensing_bits:"",
                desired_value:"", desired_value_2:"", enabled:true };

export default function PyConfigEditor({ readOnly = false, toast } = {}) {
  const { token } = useAuth();
  const [lines, setLines]       = useState([]);
  const [lineId, setLineId]     = useState("");
  const [models, setModels]     = useState([]);        // [{modelNumber, modelName}]
  const [stations, setStations] = useState([]);        // [{stationCode, seq, machineName}]
  const [pyMaster, setPyMaster] = useState([]);        // PY Master catalog (dropdown source)
  const [stationFilter, setStationFilter] = useState(""); // "" = all stations (SS_08 …)
  const [modelFilter, setModelFilter]     = useState(""); // "" = all models
  const [rows, setRows]   = useState([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(null);        // id | "new"
  const [form, setForm]   = useState(BLANK);
  const [err, setErr]     = useState("");
  const fail = (e) => setErr(e?.message || String(e));

  // 2026-08-17 — DRAFT vs PUBLISHED.  Saving edits a draft; the floor keeps
  // running the last published snapshot until "Update to Software" is pressed.
  // pubSt = { published, pending, publishedAt }
  const [pubSt,      setPubSt]      = useState(null);
  const [publishing, setPublishing] = useState(false);

  const loadPubStatus = useCallback(() => {
    if (!lineId) { setPubSt(null); return; }
    api.get(`/api/py-config/publish-status?line_id=${lineId}`, token)
       .then(setPubSt).catch(() => setPubSt(null));
  }, [lineId, token]);

  const publish = async () => {
    if (!lineId) return;
    if (!window.confirm(
      "Push this line's PY config to the running system?\n\n" +
      "Until now the floor has been running the previously published version. " +
      "After this, the live checks use what you see here.")) return;
    setPublishing(true);
    try {
      const r = await api.post(`/api/py-config/publish?line_id=${lineId}`, {}, token);
      toast && toast(`Updated in software ✓ (${r.published_rows} rows now live)`);
      loadPubStatus();
    } catch (e) { fail(e); }
    finally { setPublishing(false); }
  };

  useEffect(() => { api.get("/api/py-config/meta/lines", token).then(setLines).catch(fail); }, [token]);

  useEffect(() => {
    if (!lineId) { setModels([]); setStations([]); setPyMaster([]); setRows([]); return; }
    api.get(`/api/py-config/meta/models?line_id=${lineId}`,    token).then((m)=>setModels(m||[])).catch(fail);
    api.get(`/api/py-config/meta/stations?line_id=${lineId}`,  token).then((m)=>setStations(m||[])).catch(fail);
    api.get(`/api/py-config/meta/py-master?line_id=${lineId}`, token).then((m)=>setPyMaster(m||[])).catch(fail);
  }, [lineId, token]);

  const loadRows = useCallback(() => {
    if (!lineId) return;
    setLoading(true);
    const q = `line_id=${lineId}`
      + (modelFilter   ? `&model_number=${modelFilter}`   : "")
      + (stationFilter ? `&station_label=${encodeURIComponent(stationFilter)}` : "");
    api.get(`/api/py-config/?${q}`, token).then((r)=>setRows(Array.isArray(r)?r:[]))
       .catch(fail).finally(()=>setLoading(false));
  }, [lineId, modelFilter, stationFilter, token]);
  useEffect(() => { loadRows(); }, [loadRows]);
  useEffect(() => { loadPubStatus(); }, [loadPubStatus]);

  const stationSeq = (code) => { const s = stations.find(x=>x.stationCode===code); return s ? s.seq : ""; };

  const startAdd = () => {
    const m = models.find(x => String(x.modelNumber) === String(modelFilter));
    setForm({ ...BLANK,
      station_label: stationFilter || "",
      station_seq:   stationFilter ? stationSeq(stationFilter) : "",
      model_number:  modelFilter || "",
      model_name:    m ? m.modelName : "" });
    setEditing("new");
  };
  const startEdit = (r) => {
    setForm({ station_label:r.stationLabel??"", station_seq:r.stationSeq??"",
      model_number:r.modelNumber??"", model_name:r.modelName??"", py_master_id:"", py_seq:r.pySeq??"",
      py_no:r.pyNo??"", py_name:r.pyName??"", d_bit:r.dBit??"", reg_count:r.regCount??1,
      sensing_bits:r.sensingBits??"",
      desired_value:r.desiredValue??"", desired_value_2:r.desiredValue2??"", enabled:r.enabled??true });
    setEditing(r.id);
  };
  const cancel = () => { setEditing(null); setForm(BLANK); setErr(""); };

  const num = (v) => (v==="" || v==null ? null : Number(v));
  const setF = (k,v) => setForm((f)=>({ ...f, [k]:v }));
  const F = (k) => ({ value: form[k] ?? "", onChange:(e)=>setF(k, e.target.value) });

  // pick a PY from the PY MASTER → auto-fill py_no / py_name / d_bit / reg_count
  const pickPy = (masterId) => {
    const p = pyMaster.find(x => String(x.id) === String(masterId));
    setForm((f)=>({ ...f, py_master_id:masterId,
      py_no:   p ? (p.pyNo || "")   : f.py_no,
      py_name: p ? (p.pyName || "") : f.py_name,
      d_bit:   p ? (p.dBit || "")   : f.d_bit,
      // Seed the sensor from the master too — it is the right value for most
      // lines, and this row is exactly where you override it for the one line
      // whose sensor is wired to a different input.
      sensing_bits: p ? (p.sensingBits || "") : f.sensing_bits,
      reg_count: p ? (p.regCount || 1) : f.reg_count }));
  };
  const pickStation = (code) => setForm((f)=>({ ...f, station_label:code, station_seq:stationSeq(code) }));
  const pickModel = (mn) => {
    const m = models.find(x => String(x.modelNumber) === String(mn));
    setForm((f)=>({ ...f, model_number:mn, model_name:m ? m.modelName : "" }));
  };

  // PY-master options for the current row's station (all final-insp PYs → SS_08)
  const pyForStation = pyMaster.filter(p => !form.station_label || p.stationCode === form.station_label);

  const save = async () => {
    setErr("");
    if (!form.station_label) { setErr("Pick a station (SS_08 …) first."); return; }
    const body = {
      line_id:Number(lineId), plc_config_id:null,
      station_label:form.station_label||null, station_seq:num(form.station_seq),
      model_number:num(form.model_number), model_name:form.model_name||null,
      py_seq:num(form.py_seq), py_no:form.py_no||null, py_name:form.py_name||null,
      d_bit:form.d_bit||null, reg_count:num(form.reg_count)||1,
      // Empty → NULL, which means "inherit the PY master's sensor".
      sensing_bits:form.sensing_bits||null,
      desired_value:form.desired_value||null,
      desired_value_2:(Number(form.reg_count)===2 ? (form.desired_value_2||null) : null),
      enabled:!!form.enabled,
    };
    try {
      if (editing==="new") await api.post("/api/py-config/", body, token);
      else                 await api.put(`/api/py-config/${editing}`, body, token);
      // Saved to the DRAFT — say so, or the operator assumes it is live.
      toast && toast("Saved to draft — press “Update to Software” to go live");
      cancel(); loadRows(); loadPubStatus();
    } catch (e) { fail(e); }
  };
  const remove = async (id) => {
    if (!window.confirm("Delete this PY row?")) return;
    try { await api.delete(`/api/py-config/${id}`, token); loadRows(); loadPubStatus(); } catch (e) { fail(e); }
  };

  const stationLabelOf = (code) => { const s = stations.find(x=>x.stationCode===code);
    return code + (s && s.machineName ? ` — ${s.machineName}` : ""); };
  const modelLabel = (mn, name) => mn==null||mn==="" ? "—" : `${mn} · ${name||""}`;

  // 2026-08-17 — RENDERED AS A FUNCTION CALL, never as <EditRow />.
  // It used to be used as a component.  Because it is defined inside this
  // component body, every keystroke re-ran PyConfigEditor and produced a NEW
  // function identity for EditRow — React compares element types by reference,
  // saw a different type, and unmounted/remounted the whole row.  That threw
  // away the live <input> DOM node, so focus was lost after EVERY character
  // and the user had to click the box again for each keypress.
  // Calling it inlines the JSX into the parent's tree instead of creating a
  // component boundary, so the same DOM node survives across renders.
  const EditRow = (rowKey) => (
    <tr key={rowKey} style={{ background:C.blueBg }}>
      <td style={td}>
        <select style={{ ...inp, minWidth:96 }} value={form.station_label} onChange={(e)=>pickStation(e.target.value)}>
          <option value="">— station —</option>
          {stations.map((s)=><option key={s.stationCode} value={s.stationCode}>{s.stationCode}</option>)}
        </select>
      </td>
      <td style={td}>
        <select style={{ ...inp, minWidth:150 }} value={form.model_number} onChange={(e)=>pickModel(e.target.value)}>
          <option value="">— model —</option>
          {models.map((m)=><option key={m.modelNumber} value={m.modelNumber}>{modelLabel(m.modelNumber,m.modelName)}</option>)}
        </select>
      </td>
      <td style={td} colSpan={2}>
        <select style={{ ...inp, minWidth:210 }} value={form.py_master_id} onChange={(e)=>pickPy(e.target.value)}
                disabled={!form.station_label}>
          <option value="">{form.station_label ? "— pick PY from master —" : "pick station first"}</option>
          {pyForStation.map((p)=><option key={p.id} value={p.id}>{p.pyNo} · {p.pyName}{p.sensingBits?` [${p.sensingBits}]`:""}</option>)}
        </select>
        <div style={{ display:"flex", gap:4, marginTop:4 }}>
          <input style={{ ...inp, flex:1 }} placeholder="PY no"   {...F("py_no")} />
          <input style={{ ...inp, flex:2 }} placeholder="PY name" {...F("py_name")} />
        </div>
      </td>
      <td style={td}><input style={{ ...inp, width:74 }} placeholder="D401" {...F("d_bit")} /></td>
      <td style={td}><input style={{ ...inp, width:84 }} placeholder="X15"
                            title="Sensor X-bit for THIS line. Leave blank to inherit the PY master's value."
                            {...F("sensing_bits")} /></td>
      <td style={td}>
        <select style={{ ...inp, width:52 }} value={form.reg_count} onChange={(e)=>setF("reg_count", Number(e.target.value))}>
          <option value={1}>1</option><option value={2}>2</option>
        </select>
      </td>
      <td style={td}><input style={{ ...inp, width:56 }} placeholder="0/1/2" {...F("desired_value")} /></td>
      <td style={td}><input style={{ ...inp, width:56 }} placeholder="0/1/2" {...F("desired_value_2")}
                            disabled={Number(form.reg_count)!==2} /></td>
      <td style={td}><input type="checkbox" checked={!!form.enabled} onChange={(e)=>setF("enabled", e.target.checked)} /></td>
      <td style={{ ...td, whiteSpace:"nowrap" }}>
        <button style={btn(C.green)} onClick={save}>Save</button>{" "}
        <button style={btn("#e2e8f0", C.text)} onClick={cancel}>✕</button>
      </td>
    </tr>
  );

  const selLine = lines.find(l => String(l.id) === String(lineId));

  return (
    <div style={{ fontFamily:"'Barlow',sans-serif" }}>
      <div style={{ display:"flex", alignItems:"center", gap:10, flexWrap:"wrap", marginBottom:4 }}>
        <div style={{ fontSize:16, fontWeight:800, color:C.text }}>⚑ Poka-Yoke Config</div>
        <span style={{ fontSize:11, color:C.sub, background:C.head, padding:"3px 8px", borderRadius:5 }}>
          model-driven · per station (SS_01…SS_08) · PY from master · D-bit → desired 0/1/2
        </span>
      </div>

      {/* line → station → model dropdown bar */}
      <div style={{ display:"flex", gap:14, flexWrap:"wrap", alignItems:"flex-end",
                    background:C.head, border:`1px solid ${C.border}`, borderRadius:8, padding:"10px 12px", marginBottom:12 }}>
        <div><div style={lbl}>Line</div>
          <select style={{ ...inp, width:210 }} value={lineId}
            onChange={(e)=>{ setLineId(e.target.value); setStationFilter(""); setModelFilter(""); cancel(); }}>
            <option value="">— select line —</option>
            {lines.map((l)=><option key={l.id} value={l.id}>{l.lineCode} — {l.lineName}</option>)}
          </select>
        </div>
        <div><div style={lbl}>Station</div>
          <select style={{ ...inp, width:230 }} value={stationFilter} disabled={!lineId}
            onChange={(e)=>{ setStationFilter(e.target.value); cancel(); }}>
            <option value="">All stations</option>
            {stations.map((s)=><option key={s.stationCode} value={s.stationCode}>{stationLabelOf(s.stationCode)}</option>)}
          </select>
        </div>
        <div><div style={lbl}>Model</div>
          <select style={{ ...inp, width:230 }} value={modelFilter} disabled={!lineId}
            onChange={(e)=>{ setModelFilter(e.target.value); cancel(); }}>
            <option value="">All models</option>
            {models.map((m)=><option key={m.modelNumber} value={m.modelNumber}>{modelLabel(m.modelNumber,m.modelName)}</option>)}
          </select>
        </div>
        <div style={{ flex:1 }} />
        {!readOnly && lineId &&
          <button style={btn(C.blue)} onClick={startAdd} disabled={editing==="new"}>+ Add PY output</button>}

        {/* 2026-08-17 — the draft/live gate.  Nothing edited above reaches the
            running checks until this is pressed, so a half-finished config can
            never disturb a shift in progress. */}
        {lineId && pubSt && (
          <div style={{ display:"flex", alignItems:"center", gap:10 }}>
            <div style={{ textAlign:"right", lineHeight:1.3 }}>
              <div style={{ fontSize:11, fontWeight:800,
                             color: pubSt.pending ? "#b45309" : C.green }}>
                {pubSt.pending
                  ? `${pubSt.pending} change(s) not live yet`
                  : "Software is up to date"}
              </div>
              <div style={{ fontSize:10, color:C.sub }}>
                {pubSt.published
                  ? `last update: ${pubSt.publishedAt
                       ? new Date(pubSt.publishedAt).toLocaleString("en-IN") : "—"}`
                  : "never pushed — line still runs the draft"}
              </div>
            </div>
            {!readOnly && (
              <button
                onClick={publish}
                disabled={publishing || !pubSt.pending}
                title={pubSt.pending
                  ? "Push these settings to the running poka-yoke checks"
                  : "Nothing to update — the floor already runs this config"}
                style={{
                  ...btn(pubSt.pending ? "#b45309" : "#cbd5e1"),
                  cursor: pubSt.pending ? "pointer" : "default",
                  fontWeight: 800,
                }}>
                {publishing ? "Updating…" : "⤴ Update to Software"}
              </button>
            )}
          </div>
        )}
      </div>

      <div style={{ fontSize:11.5, color:C.sub, marginBottom:12 }}>
        Station codes (SS_01…SS_08) come from the machine master — SS_08 = final inspection. Model no. = the value the
        PLC model register (D6048) reports. Pick a station &amp; model, add each PY from the master, then set its desired
        register value(s) 0/1/2 — every station is independent, only the desired output changes per model.
      </div>

      {err && <div style={{ background:"#fef2f2", color:C.red, border:"1px solid #fecaca", padding:"8px 12px", borderRadius:6, marginBottom:10, fontSize:12.5 }}>⚠ {err}</div>}
      {!lineId && <div style={{ padding:40, textAlign:"center", color:C.sub }}>Select a production line to view / edit its PY config.</div>}

      {lineId && (
        <div style={{ overflowX:"auto", border:`1px solid ${C.border}`, borderRadius:8 }}>
          <table style={{ width:"100%", borderCollapse:"collapse", minWidth:1020 }}>
            <thead style={{ background:C.head }}>
              <tr>
                <th style={th}>Station</th><th style={th}>Model no.</th>
                <th style={th}>PY No</th><th style={th}>PY Name</th>
                <th style={th}>D-bit</th><th style={th}>Sensor</th><th style={th}>Regs</th><th style={th}>Desired</th><th style={th}>Des-2</th>
                <th style={th}>On</th><th style={th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {editing==="new" && EditRow("new")}
              {loading && <tr><td style={td} colSpan={11}>Loading…</td></tr>}
              {!loading && rows.length===0 && editing!=="new" &&
                <tr><td style={{ ...td, color:C.sub, padding:24, textAlign:"center" }} colSpan={11}>
                  No PY output set for this selection. Click <b>+ Add PY output</b> to create one.
                </td></tr>}
              {rows.map((r)=> editing===r.id ? EditRow(r.id) : (
                <tr key={r.id}>
                  <td style={{ ...td, fontWeight:700 }}>{r.stationLabel || "—"}</td>
                  <td style={td}>{modelLabel(r.modelNumber, r.modelName)}</td>
                  <td style={{ ...td, fontWeight:600 }}>{r.pyNo || "—"}</td>
                  <td style={td}>{r.pyName || ""}</td>
                  <td style={{ ...td, fontFamily:"monospace", fontSize:12, fontWeight:600 }}>{r.dBit || ""}</td>
                  <td style={{ ...td, fontFamily:"monospace", fontSize:12, fontWeight:600 }}>
                    {r.sensingBits
                      ? r.sensingBits
                      : <span style={{ color:"#cbd5e1", fontFamily:"'Barlow',sans-serif",
                                        fontSize:11, fontWeight:400 }}
                              title="No per-line override — uses the PY master's sensor">
                          inherited
                        </span>}
                  </td>
                  <td style={td}>{r.regCount || 1}</td>
                  <td style={{ ...td, fontWeight:700, color:C.blue }}>{r.desiredValue ?? ""}</td>
                  <td style={{ ...td, fontWeight:700, color:C.blue }}>{r.regCount===2 ? (r.desiredValue2 ?? "") : ""}</td>
                  <td style={td}>{r.enabled ? "✅" : "⛔"}</td>
                  <td style={{ ...td, whiteSpace:"nowrap" }}>
                    {!readOnly && <>
                      <button style={btn("#e2e8f0", C.text)} onClick={()=>startEdit(r)}>Edit</button>{" "}
                      <button style={btn("#fee2e2", C.red)} onClick={()=>remove(r.id)}>Del</button>
                    </>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {lineId && rows.length>0 &&
        <div style={{ marginTop:8, fontSize:11.5, color:C.sub }}>
          {rows.length} PY output row(s){selLine?` · ${selLine.lineCode}`:""}
          {stationFilter?` · ${stationFilter}`:""}{modelFilter?` · model ${modelFilter}`:""}.
        </div>}
    </div>
  );
}
