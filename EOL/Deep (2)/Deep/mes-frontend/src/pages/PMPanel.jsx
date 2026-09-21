/* ───────────────────────────────────────────────────────────────────
 * PMPanel.jsx — Preventive Maintenance check sheets
 * ───────────────────────────────────────────────────────────────────
 * 2026-06-13 — Three surfaces in one page:
 *   • Check Sheets : zone → machine → fill the PM sheet (same Excel format)
 *   • Dashboard    : this-month done / fill-pending / not-started
 *   • Edit Points  : admin (canWrite) add / edit / delete the template points
 *
 * All DB-backed via /api/pm/*.  PM schedule (which machine due which month)
 * is operator-supplied later — dashboard shows actual records until then.
 * ─────────────────────────────────────────────────────────────────── */
import { useEffect, useState, useCallback } from "react";
import { useAuth } from "../context/AuthContext";

const monthISO = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}`; };

export default function PMPanel() {
  const { token, canWrite } = useAuth();
  const isAdmin = canWrite ? canWrite("maintenance-pm") : false;

  const api = useCallback(async (path, opts = {}) => {
    const r = await fetch(`/api/pm${path}`, {
      ...opts,
      headers: { "Content-Type": "application/json",
                 ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers || {}) },
    });
    if (!r.ok) throw new Error((await r.text().catch(()=> "")) || `HTTP ${r.status}`);
    return r.json();
  }, [token]);

  const [view,   setView]   = useState("sheets");      // 'sheets' | 'dashboard'
  const [month,  setMonth]  = useState(monthISO());
  const [zones,  setZones]  = useState([]);
  const [zone,   setZone]   = useState(null);
  const [machines, setMachines] = useState([]);
  const [machine,  setMachine]  = useState(null);      // {machine, points}
  const [rec,    setRec]    = useState({ pm_date:"", team_name:"", status:"open" });
  const [fills,  setFills]  = useState({});            // point_id -> {observation,action_taken,...}
  const [edit,   setEdit]   = useState(false);         // admin edit-points mode
  const [dash,   setDash]   = useState(null);
  const [busy,   setBusy]   = useState(false);
  const [msg,    setMsg]    = useState("");
  // ── schedule / planner / mail (2026-06-17) ──
  const [schedule, setSchedule] = useState([]);
  const [mailCfg,  setMailCfg]  = useState({ recipient:"", cc:"", auto_enabled:true });
  const [plZone,    setPlZone]    = useState("");
  const [plMachines,setPlMachines]= useState([]);
  const [plMachine, setPlMachine] = useState("");
  const [plDate,    setPlDate]    = useState("");
  const [plRepeat,  setPlRepeat]  = useState(false);

  // load zones
  useEffect(() => { api("/zones").then(d => setZones(d.zones || [])).catch(()=>{}); }, [api]);
  // load dashboard
  useEffect(() => {
    if (view === "dashboard") api(`/dashboard?month=${month}`).then(setDash).catch(()=>{});
  }, [view, month, api]);
  // schedule + mail config
  useEffect(() => {
    if (view === "schedule") {
      api(`/schedule?month=${month}`).then(d => setSchedule(d.schedule || [])).catch(()=>{});
      api(`/mail-config`).then(setMailCfg).catch(()=>{});
    }
  }, [view, month, api]);
  // planner: load machines for the picked zone
  useEffect(() => {
    if (plZone) api(`/machines?zone=${encodeURIComponent(plZone)}`).then(d => setPlMachines(d.machines || [])).catch(()=>{});
    else setPlMachines([]);
    setPlMachine("");
  }, [plZone, api]);

  const openZone = (z) => {
    setZone(z); setMachine(null);
    api(`/machines?zone=${encodeURIComponent(z)}`).then(d => setMachines(d.machines || [])).catch(()=>{});
  };
  const openMachine = async (mid) => {
    setMsg(""); setEdit(false);
    const d = await api(`/machines/${mid}`);
    setMachine(d);
    const r = await api(`/machines/${mid}/record?month=${month}`);
    setRec(r.record || { pm_date:"", team_name:"", status:"open" });
    setFills(r.fills || {});
  };
  // reload record when month changes while a machine is open
  useEffect(() => {
    if (machine?.machine?.id) {
      api(`/machines/${machine.machine.id}/record?month=${month}`)
        .then(r => { setRec(r.record || { pm_date:"", team_name:"", status:"open" }); setFills(r.fills || {}); })
        .catch(()=>{});
    }
  }, [month]); // eslint-disable-line

  const setFill = (pid, k, v) => setFills(f => ({ ...f, [pid]: { ...(f[pid]||{}), [k]: v } }));

  const saveSheet = async () => {
    if (!machine) return;
    setBusy(true); setMsg("");
    try {
      const body = {
        pm_month: month, pm_date: rec.pm_date || null,
        team_name: rec.team_name || "", status: rec.status || "open",
        fills: (machine.points||[]).map(p => ({ point_id: p.id, ...(fills[p.id]||{}) })),
      };
      await api(`/machines/${machine.machine.id}/record`, { method:"POST", body: JSON.stringify(body) });
      setMsg("✓ Saved");
    } catch (e) { setMsg(String(e.message||e).slice(0,160)); }
    finally { setBusy(false); }
  };

  // ── admin: edit template points ──
  const reloadPoints = async () => { const d = await api(`/machines/${machine.machine.id}`); setMachine(d); };
  const addPoint = async () => {
    await api(`/machines/${machine.machine.id}/points`, { method:"POST",
      body: JSON.stringify({ check_point:"New check point", judgement_standard:"", method:"" }) });
    reloadPoints();
  };
  const editPoint = async (p, k, v) =>
    api(`/points/${p.id}`, { method:"PUT", body: JSON.stringify({ ...p, [k]: v }) }).then(reloadPoints).catch(()=>{});
  const delPoint = async (pid) => {
    if (!window.confirm("Delete this check point?")) return;
    await api(`/points/${pid}`, { method:"DELETE" }); reloadPoints();
  };

  // ── schedule / planner / mail handlers ──
  const reloadSchedule = () => api(`/schedule?month=${month}`).then(d => setSchedule(d.schedule || [])).catch(()=>{});
  const addPlan = async () => {
    if (!plMachine || !plDate) { setMsg("Pick machine + date"); return; }
    const m = plMachines.find(x => String(x.id) === String(plMachine));
    setBusy(true); setMsg("");
    try {
      await api(`/schedule`, { method:"POST", body: JSON.stringify({
        sheet_id: Number(plMachine), zone: plZone,
        machine_no: m?.machine_code, machine_name: m?.machine_name,
        due_date: plDate, repeat_12m: plRepeat }) });
      setPlDate(""); setPlRepeat(false); setMsg("✓ Scheduled"); reloadSchedule();
    } catch (e) { setMsg(String(e.message||e).slice(0,140)); }
    finally { setBusy(false); }
  };
  const togglePlan = async (s) => {
    await api(`/schedule/${s.id}`, { method:"PATCH",
      body: JSON.stringify({ status: (s.status||"").toLowerCase()==="done" ? "Pending" : "Done" }) });
    reloadSchedule();
  };
  const delPlan = async (id) => { await api(`/schedule/${id}`, { method:"DELETE" }); reloadSchedule(); };
  const saveMail = async () => {
    try { await api(`/mail-config`, { method:"PUT", body: JSON.stringify(mailCfg) }); setMsg("✓ Mail recipient saved"); }
    catch (e) { setMsg(String(e.message||e).slice(0,140)); }
  };
  const sendMail = async (type) => {
    setBusy(true); setMsg("");
    try { const r = await api(`/send-reminder?type=${type}`, { method:"POST" });
          setMsg(r.sent ? `✓ Reminder sent (${r.count} PM)` : `Not sent: ${r.reason||"—"}`); }
    catch (e) { setMsg(String(e.message||e).slice(0,140)); }
    finally { setBusy(false); }
  };

  // ── styles ──
  const card = { background:"#fff", border:"1px solid #e2e8f0", borderRadius:10, padding:14 };
  const bd = "1px solid #cbd5e1";
  const th = { border:bd, padding:"5px 6px", fontSize:11, fontWeight:800, background:"#f1f5f9", color:"#1e293b", textAlign:"center" };
  const inp = { width:"100%", border:"none", outline:"none", background:"transparent", fontSize:12, padding:"4px 5px", boxSizing:"border-box", resize:"vertical", fontFamily:"inherit" };

  return (
    <div style={{ padding:18, background:"#f1f5f9", minHeight:"100%" }}>
      <style>{`
        @page { size: A4 landscape; margin: 0; }
        @media print {
          html, body { margin:0 !important; padding:0 !important; background:#fff !important;
                       -webkit-print-color-adjust:exact; print-color-adjust:exact; }
          .pm-noprint { display:none !important; }
          .pm-sheet { box-shadow:none !important; padding:2mm !important; }
          .pm-sheet textarea { background:transparent !important; }
        }
      `}</style>
      {/* header */}
      <div style={{ display:"flex", alignItems:"center", gap:12, flexWrap:"wrap", marginBottom:14 }}>
        <span style={{ fontSize:18, fontWeight:900, color:"#0f172a" }}>🛠 Preventive Maintenance</span>
        <div style={{ display:"flex", gap:4, background:"#e2e8f0", borderRadius:8, padding:3 }}>
          {[["sheets","Check Sheets"],["schedule","Schedule"],["dashboard","Dashboard"]].map(([k,l]) => (
            <button key={k} onClick={()=>setView(k)} style={{
              padding:"5px 14px", borderRadius:6, border:"none", cursor:"pointer", fontWeight:700, fontSize:12,
              background: view===k ? "#2563eb" : "transparent", color: view===k ? "#fff" : "#475569" }}>{l}</button>
          ))}
        </div>
        <span style={{ flex:1 }} />
        <label style={{ fontSize:12, color:"#334155", fontWeight:600 }}>Month{" "}
          <input type="month" value={month} onChange={e=>setMonth(e.target.value)}
            style={{ padding:"4px 6px", borderRadius:6, border:bd }} />
        </label>
      </div>

      {/* ── DASHBOARD ── */}
      {view==="dashboard" && (
        <div style={card}>
          {!dash ? <div style={{ color:"#64748b" }}>Loading…</div> : (
            <>
              <div style={{ display:"flex", gap:12, flexWrap:"wrap", marginBottom:14 }}>
                {[["Done",dash.counts.done,"#16a34a"],["Fill pending",dash.counts.fill_pending,"#d97706"],
                  ["Not started",dash.counts.not_started,"#dc2626"],["Total machines",dash.counts.total,"#475569"]].map(([l,n,c])=>(
                  <div key={l} style={{ ...card, minWidth:140, borderTop:`3px solid ${c}` }}>
                    <div style={{ fontSize:11, color:"#64748b", fontWeight:700 }}>{l}</div>
                    <div style={{ fontSize:26, fontWeight:900, color:c }}>{n}</div>
                  </div>
                ))}
              </div>
              {/* schedule-driven due this month + next month (from pm_schedule) */}
              <div style={{ display:"flex", gap:12, flexWrap:"wrap", marginBottom:12 }}>
                <div style={{ ...card, flex:"1 1 280px", borderTop:"3px solid #d97706" }}>
                  <div style={{ fontWeight:800, fontSize:12, color:"#b45309", marginBottom:6 }}>⏰ Due this month (scheduled) — {dash.scheduled_pending?.length||0}</div>
                  {(dash.scheduled_pending||[]).slice(0,6).map(s=>(
                    <div key={s.id} style={{ fontSize:11, color:"#475569", padding:"2px 0" }}>{s.due_date} · <b>{s.machine_name||s.machine_no}</b> ({s.zone})</div>))}
                  {(dash.scheduled_pending||[]).length===0 && <div style={{ fontSize:11, color:"#94a3b8" }}>None pending.</div>}
                </div>
                <div style={{ ...card, flex:"1 1 280px", borderTop:"3px solid #2563eb" }}>
                  <div style={{ fontWeight:800, fontSize:12, color:"#1e40af", marginBottom:6 }}>📅 Next month (scheduled) — {dash.next_month?.length||0}</div>
                  {(dash.next_month||[]).slice(0,6).map(s=>(
                    <div key={s.id} style={{ fontSize:11, color:"#475569", padding:"2px 0" }}>{s.due_date} · <b>{s.machine_name||s.machine_no}</b> ({s.zone})</div>))}
                  {(dash.next_month||[]).length===0 && <div style={{ fontSize:11, color:"#94a3b8" }}>None.</div>}
                </div>
              </div>
              <div style={{ fontSize:11, color:"#94a3b8", marginBottom:8, fontStyle:"italic" }}>
                Below = PM started but check sheet not fully filled. (Manage the schedule in the <b>Schedule</b> tab.)
              </div>
              <div style={{ fontWeight:800, fontSize:13, color:"#b45309", margin:"6px 0" }}>⚠ Check-sheet fill pending</div>
              {dash.fill_pending.length===0 ? <div style={{ color:"#64748b", fontSize:12 }}>None.</div> : (
                <table style={{ width:"100%", borderCollapse:"collapse" }}>
                  <thead><tr>{["Zone","Machine","Code","Filled / Points","PM Date"].map(h=>(
                    <th key={h} style={{ ...th, textAlign:"left" }}>{h}</th>))}</tr></thead>
                  <tbody>
                    {dash.fill_pending.map(m=>(
                      <tr key={m.id} style={{ cursor:"pointer" }}
                          onClick={()=>{ setView("sheets"); openZone(m.zone); setTimeout(()=>openMachine(m.id),150); }}>
                        <td style={{ border:bd, padding:"4px 6px", fontSize:12 }}>{m.zone}</td>
                        <td style={{ border:bd, padding:"4px 6px", fontSize:12, color:"#2563eb", fontWeight:600 }}>{m.machine_name}</td>
                        <td style={{ border:bd, padding:"4px 6px", fontSize:12 }}>{m.machine_code}</td>
                        <td style={{ border:bd, padding:"4px 6px", fontSize:12 }}>{m.filled} / {m.points}</td>
                        <td style={{ border:bd, padding:"4px 6px", fontSize:12 }}>{m.pm_date||"—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          )}
        </div>
      )}

      {/* ── SCHEDULE / PLANNER / CALENDAR / MAIL ── */}
      {view==="schedule" && (() => {
        const today = new Date(); today.setHours(0,0,0,0);
        const isDone = (s) => (s.status||"").toLowerCase()==="done";
        const isOverdue = (s) => !isDone(s) && s.due_date && new Date(s.due_date+"T00:00:00") < today;
        const total = schedule.length, done = schedule.filter(isDone).length;
        const pending = total - done, overdue = schedule.filter(isOverdue).length;
        const compliance = total ? Math.round(done/total*100) : 0;
        const [yy, mm] = month.split("-").map(Number);
        const first = new Date(yy, mm-1, 1);
        const gridStart = new Date(yy, mm-1, 1 - first.getDay());
        const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
        const byDay = {}; schedule.forEach(s => { (byDay[s.due_date] = byDay[s.due_date]||[]).push(s); });
        const cells = Array.from({length:42},(_,i)=>{ const d=new Date(gridStart); d.setDate(gridStart.getDate()+i); return d; });
        const todayY = ymd(today);
        const kpi = (l,n,c) => (<div style={{...card,minWidth:118,borderTop:`3px solid ${c}`}}>
          <div style={{fontSize:11,color:"#64748b",fontWeight:700}}>{l}</div>
          <div style={{fontSize:24,fontWeight:900,color:c}}>{n}</div></div>);
        return (
        <>
          <div style={{display:"flex",gap:12,flexWrap:"wrap",marginBottom:14}}>
            {kpi("Scheduled",total,"#2563eb")}{kpi("Done",done,"#16a34a")}{kpi("Pending",pending,"#d97706")}
            {kpi("Overdue",overdue,"#dc2626")}{kpi("Compliance",compliance+"%",compliance>=90?"#16a34a":"#d97706")}
          </div>

          <div style={{display:"grid",gridTemplateColumns:"1fr 320px",gap:14,alignItems:"start"}}>
            {/* calendar */}
            <div style={card}>
              <div style={{fontWeight:800,fontSize:14,marginBottom:8,color:"#0f172a"}}>📅 PM Calendar — {month}</div>
              <div style={{display:"grid",gridTemplateColumns:"repeat(7,1fr)",gap:4}}>
                {["Sun","Mon","Tue","Wed","Thu","Fri","Sat"].map(d=>(
                  <div key={d} style={{textAlign:"center",fontSize:10,fontWeight:800,color:"#64748b",padding:"2px 0"}}>{d}</div>))}
                {cells.map((d,i)=>{
                  const k=ymd(d), inMonth=d.getMonth()===mm-1, items=byDay[k]||[], isToday=k===todayY;
                  return (<div key={i} style={{minHeight:60,border:"1px solid #e2e8f0",borderRadius:6,padding:3,
                      background:inMonth?(isToday?"#eff6ff":"#fff"):"#f8fafc",outline:isToday?"2px solid #2563eb":"none"}}>
                    <div style={{fontSize:10,fontWeight:700,color:inMonth?"#334155":"#cbd5e1"}}>{d.getDate()}</div>
                    {items.slice(0,3).map(s=>(
                      <div key={s.id} title={s.machine_name} style={{fontSize:9,marginTop:2,padding:"1px 3px",borderRadius:3,
                          background:isDone(s)?"#dcfce7":isOverdue(s)?"#fee2e2":"#fef3c7",
                          color:isDone(s)?"#15803d":isOverdue(s)?"#b91c1c":"#b45309",
                          whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{s.machine_name||s.machine_no||"PM"}</div>))}
                    {items.length>3 && <div style={{fontSize:9,color:"#94a3b8"}}>+{items.length-3}</div>}
                  </div>);
                })}
              </div>
              <div style={{display:"flex",gap:14,marginTop:8,fontSize:10,color:"#64748b"}}>
                <span>🟨 Pending</span><span>🟩 Done</span><span>🟥 Overdue</span>
              </div>
            </div>

            {/* planner + mail */}
            <div style={{display:"flex",flexDirection:"column",gap:14}}>
              <div style={card}>
                <div style={{fontWeight:800,fontSize:14,marginBottom:8,color:"#0f172a"}}>➕ Plan a PM</div>
                <select value={plZone} onChange={e=>setPlZone(e.target.value)} style={{width:"100%",padding:6,borderRadius:6,border:bd,marginBottom:8,fontSize:12}}>
                  <option value="">— zone —</option>
                  {zones.map(z=><option key={z.zone} value={z.zone}>{z.zone}</option>)}
                </select>
                <select value={plMachine} onChange={e=>setPlMachine(e.target.value)} disabled={!plZone} style={{width:"100%",padding:6,borderRadius:6,border:bd,marginBottom:8,fontSize:12}}>
                  <option value="">— machine —</option>
                  {plMachines.map(m=><option key={m.id} value={m.id}>{m.machine_name||m.machine_code}</option>)}
                </select>
                <input type="date" value={plDate} onChange={e=>setPlDate(e.target.value)} style={{width:"100%",padding:6,borderRadius:6,border:bd,marginBottom:8,fontSize:12,boxSizing:"border-box"}} />
                <label style={{display:"flex",alignItems:"center",gap:6,fontSize:12,color:"#334155",marginBottom:10}}>
                  <input type="checkbox" checked={plRepeat} onChange={e=>setPlRepeat(e.target.checked)} /> Repeat every month (12)
                </label>
                <button onClick={addPlan} disabled={busy} style={{width:"100%",padding:8,borderRadius:6,border:"none",background:"#2563eb",color:"#fff",fontWeight:800,fontSize:13,cursor:"pointer"}}>
                  {busy?"…":"Add to schedule"}</button>
                {msg && <div style={{fontSize:11,fontWeight:700,marginTop:8,color:msg.startsWith("✓")?"#16a34a":"#dc2626"}}>{msg}</div>}
              </div>
              <div style={card}>
                <div style={{fontWeight:800,fontSize:14,marginBottom:8,color:"#0f172a"}}>✉ Reminder Mail</div>
                <input value={mailCfg.recipient||""} onChange={e=>setMailCfg(c=>({...c,recipient:e.target.value}))}
                  placeholder="recipient@tbdi.com" style={{width:"100%",padding:6,borderRadius:6,border:bd,marginBottom:8,fontSize:12,boxSizing:"border-box"}} />
                <label style={{display:"flex",alignItems:"center",gap:6,fontSize:11,color:"#334155",marginBottom:8}}>
                  <input type="checkbox" checked={!!mailCfg.auto_enabled} onChange={e=>setMailCfg(c=>({...c,auto_enabled:e.target.checked}))} /> Auto (Mon→this-week, Sat→next-week)
                </label>
                <div style={{display:"flex",gap:6}}>
                  <button onClick={saveMail} style={{flex:1,padding:6,borderRadius:6,border:bd,background:"#fff",fontWeight:700,fontSize:11,cursor:"pointer"}}>Save</button>
                  <button onClick={()=>sendMail("current-week")} disabled={busy} style={{flex:1,padding:6,borderRadius:6,border:"none",background:"#16a34a",color:"#fff",fontWeight:700,fontSize:11,cursor:"pointer"}}>This week</button>
                  <button onClick={()=>sendMail("next-week")} disabled={busy} style={{flex:1,padding:6,borderRadius:6,border:"none",background:"#0891b2",color:"#fff",fontWeight:700,fontSize:11,cursor:"pointer"}}>Next week</button>
                </div>
              </div>
            </div>
          </div>

          {/* schedule table */}
          <div style={{...card,marginTop:14,padding:0,overflow:"hidden"}}>
            <div style={{padding:"10px 14px",fontWeight:800,fontSize:13,color:"#0f172a",borderBottom:bd}}>Scheduled PMs — {month}</div>
            {schedule.length===0 ? <div style={{padding:16,color:"#64748b",fontSize:12}}>No PM scheduled this month. Use "Plan a PM" above.</div> : (
              <table style={{width:"100%",borderCollapse:"collapse"}}>
                <thead><tr>{["Machine","Zone / Line","Due","Status",""].map(h=>(<th key={h} style={{...th,textAlign:"left"}}>{h}</th>))}</tr></thead>
                <tbody>
                  {schedule.map(s=>(
                    <tr key={s.id} style={{background:isOverdue(s)?"#fff1f2":"transparent"}}>
                      <td style={{border:bd,padding:"5px 8px",fontSize:12,fontWeight:600}}>{s.machine_name||s.machine_no||"—"}</td>
                      <td style={{border:bd,padding:"5px 8px",fontSize:12}}>{s.zone||"—"} / {s.line||"—"}</td>
                      <td style={{border:bd,padding:"5px 8px",fontSize:12,fontFamily:"monospace",color:isOverdue(s)?"#dc2626":"#475569"}}>{s.due_date}{isOverdue(s)?" ⚠":""}</td>
                      <td style={{border:bd,padding:"5px 8px"}}>
                        <button onClick={()=>togglePlan(s)} style={{padding:"2px 10px",borderRadius:99,border:"none",cursor:"pointer",fontSize:11,fontWeight:700,
                          background:isDone(s)?"#dcfce7":"#fef3c7",color:isDone(s)?"#15803d":"#b45309"}}>{isDone(s)?"✓ Done":"Pending"}</button>
                      </td>
                      <td style={{border:bd,padding:"5px 8px",textAlign:"center"}}>
                        <button onClick={()=>delPlan(s.id)} style={{border:"none",background:"transparent",color:"#dc2626",cursor:"pointer",fontWeight:800}}>×</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
        );
      })()}

      {/* ── CHECK SHEETS ── */}
      {view==="sheets" && (
        <>
          {/* breadcrumb */}
          <div style={{ fontSize:13, color:"#475569", marginBottom:10 }}>
            <span style={{ cursor:"pointer", fontWeight:zone?400:800 }} onClick={()=>{setZone(null);setMachine(null);}}>Zones</span>
            {zone && <> ▸ <span style={{ cursor:"pointer", fontWeight:machine?400:800 }} onClick={()=>setMachine(null)}>{zone}</span></>}
            {machine && <> ▸ <b>{machine.machine.machine_name}</b></>}
          </div>

          {/* zones grid */}
          {!zone && (
            <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fill,minmax(180px,1fr))", gap:12 }}>
              {zones.map(z=>(
                <button key={z.zone} onClick={()=>openZone(z.zone)} style={{
                  ...card, cursor:"pointer", textAlign:"left", borderLeft:"4px solid #2563eb" }}>
                  <div style={{ fontSize:15, fontWeight:800, color:"#0f172a" }}>{z.zone}</div>
                  <div style={{ fontSize:12, color:"#64748b", marginTop:4 }}>{z.machines} machines</div>
                </button>
              ))}
            </div>
          )}

          {/* machine list */}
          {zone && !machine && (
            <div style={{ ...card, padding:0, overflow:"hidden" }}>
              <table style={{ width:"100%", borderCollapse:"collapse" }}>
                <thead><tr>{["#","Machine Name","Code","Area / Line","Points"].map(h=>(
                  <th key={h} style={{ ...th, textAlign:"left" }}>{h}</th>))}</tr></thead>
                <tbody>
                  {machines.map((m,i)=>(
                    <tr key={m.id} onClick={()=>openMachine(m.id)} style={{ cursor:"pointer" }}>
                      <td style={{ border:bd, padding:"5px 7px", fontSize:12 }}>{i+1}</td>
                      <td style={{ border:bd, padding:"5px 7px", fontSize:12.5, color:"#2563eb", fontWeight:600 }}>{m.machine_name||m.sheet_name}</td>
                      <td style={{ border:bd, padding:"5px 7px", fontSize:12 }}>{m.machine_code||"—"}</td>
                      <td style={{ border:bd, padding:"5px 7px", fontSize:12 }}>{m.area_line||"—"}</td>
                      <td style={{ border:bd, padding:"5px 7px", fontSize:12 }}>{m.points}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* the check sheet — EXACT TBDI PM format */}
          {machine && (() => {
            const M = machine.machine;
            const sb = "1px solid #000";
            const sth = { border:sb, padding:"4px 5px", fontSize:10.5, fontWeight:800, background:"#f3f4f6", color:"#111827", textAlign:"center", verticalAlign:"middle" };
            const lbl = { border:sb, padding:"3px 6px", fontSize:11, fontWeight:700, whiteSpace:"nowrap", background:"#fff", verticalAlign:"middle" };
            const val = { border:sb, padding:"3px 6px", fontSize:11.5, background:"#fff", verticalAlign:"middle" };
            const fin = (pid, k, center) => (
              <td style={{ border:sb, padding:0 }}>
                <textarea rows={1} value={(fills[pid]||{})[k]||""} onChange={e=>setFill(pid,k,e.target.value)}
                  style={{ ...inp, minHeight:26, textAlign:center?"center":"left" }} />
              </td>);
            return (
            <>
              {/* toolbar (NOT part of the form) */}
              <div className="pm-noprint" style={{ display:"flex", gap:8, alignItems:"center", marginBottom:10 }}>
                {isAdmin && (
                  <button onClick={()=>setEdit(e=>!e)} style={{ padding:"5px 12px", borderRadius:6, border:bd,
                    background: edit?"#fef3c7":"#fff", fontWeight:700, fontSize:12, cursor:"pointer" }}>
                    {edit ? "✓ Editing points" : "✎ Edit points (admin)"}</button>
                )}
                {isAdmin && edit && <button onClick={addPoint} style={{ padding:"5px 12px", borderRadius:6, border:"none", background:"#16a34a", color:"#fff", fontWeight:700, fontSize:12, cursor:"pointer" }}>+ Point</button>}
                <span style={{ flex:1 }} />
                {msg && <span style={{ fontSize:12, fontWeight:700, color: msg.startsWith("✓")?"#16a34a":"#dc2626" }}>{msg}</span>}
                <button onClick={()=>window.print()} style={{ padding:"6px 14px", borderRadius:6, border:bd, background:"#fff", fontWeight:700, fontSize:12, cursor:"pointer" }}>🖨 Print</button>
                {!edit && <button onClick={saveSheet} disabled={busy} style={{ padding:"6px 18px", borderRadius:6, border:"none",
                  background:busy?"#94a3b8":"#2563eb", color:"#fff", fontWeight:800, fontSize:12, cursor:busy?"default":"pointer" }}>{busy?"Saving…":"Save"}</button>}
              </div>

              {/* THE FORM SHEET */}
              <div className="pm-sheet" style={{ background:"#fff", color:"#111827", boxShadow:"0 4px 16px rgba(0,0,0,.12)", padding:10 }}>
                {/* title band */}
                <table style={{ width:"100%", borderCollapse:"collapse", tableLayout:"fixed" }}><tbody><tr>
                  <td style={{ border:sb, width:110, textAlign:"center", background:"#fff" }}>
                    <img src="/logo.jpg" alt="Toyota Boshoku" style={{ maxWidth:"100%", maxHeight:54, objectFit:"contain", display:"block", margin:"0 auto" }} />
                  </td>
                  <td style={{ border:sb, textAlign:"center", padding:"4px 8px" }}>
                    <div style={{ fontSize:16, fontWeight:900 }}>TOYOTA BOSHOKU DEVICE INDIA PVT LTD</div>
                    <div style={{ fontSize:13, fontWeight:800, marginTop:2 }}>PREVENTIVE MAINTENANCE CHECK SHEET</div>
                  </td>
                  <td style={{ border:sb, width:190, padding:0, verticalAlign:"top", fontSize:10.5 }}>
                    <div style={{ borderBottom:sb, padding:"2px 5px", fontWeight:700, textAlign:"center" }}>Check Sheet Points Revision History</div>
                    <div style={{ display:"flex" }}>
                      <div style={{ flex:1, borderRight:sb, padding:"2px 5px" }}><b>Rev No.</b> {M.rev_no||""}</div>
                      <div style={{ flex:1.3, padding:"2px 5px" }}><b>Rev Date</b> {(M.rev_date||"").slice(0,10)}</div>
                    </div>
                  </td>
                </tr></tbody></table>

                {/* machine info band */}
                <table style={{ width:"100%", borderCollapse:"collapse", tableLayout:"fixed", borderTop:"none" }}><tbody>
                  <tr>
                    <td style={{ ...lbl, width:"22%" }}>Machine/Equip./Jig /Fixture Name:-</td>
                    <td style={val} colSpan={2}>{M.machine_name||""}</td>
                    <td style={{ ...lbl, width:"12%" }}>Deptt:-</td>
                    <td style={{ ...val, width:"18%" }}>{M.dept||"Maintenance"}</td>
                  </tr>
                  <tr>
                    <td style={lbl}>M/C / Fixture Code No :</td>
                    <td style={val} colSpan={2}>{M.machine_code||""}</td>
                    <td style={lbl}>PM Date:-</td>
                    <td style={{ border:sb, padding:0 }}><input type="date" value={rec.pm_date||""} onChange={e=>setRec(r=>({...r,pm_date:e.target.value}))} style={{ ...inp, padding:"3px 5px" }} /></td>
                  </tr>
                  <tr>
                    <td style={lbl}>Area / Line :</td>
                    <td style={val} colSpan={2}>{M.area_line||""}</td>
                    <td style={lbl}>PM Team Name:-</td>
                    <td style={{ border:sb, padding:0 }}><input value={rec.team_name||""} onChange={e=>setRec(r=>({...r,team_name:e.target.value}))} placeholder="team name" style={{ ...inp, padding:"3px 5px" }} /></td>
                  </tr>
                  <tr>
                    <td style={lbl}>Month :</td>
                    <td style={val} colSpan={4}>{month}</td>
                  </tr>
                </tbody></table>

                {/* the check-points grid — exact 9 columns */}
                <div style={{ overflowX:"auto" }}>
                  <table style={{ width:"100%", minWidth:1060, borderCollapse:"collapse", borderTop:"none", tableLayout:"fixed" }}>
                    <colgroup>
                      <col style={{ width:"4%" }} /><col style={{ width:"19%" }} /><col style={{ width:"14%" }} /><col style={{ width:"11%" }} />
                      <col style={{ width:"15%" }} /><col style={{ width:"12%" }} /><col style={{ width:"9%" }} /><col style={{ width:"7%" }} /><col style={{ width:"9%" }} />
                      {edit && <col style={{ width:"3%" }} />}
                    </colgroup>
                    <thead><tr>
                      <th style={sth}>S.NO.</th>
                      <th style={sth}>CHECK POINTS / DETAIL OF WORK</th>
                      <th style={sth}>JUDGEMENT STANDARD</th>
                      <th style={sth}>METHOD</th>
                      <th style={sth}>OBSERVATION OF CHECK POINTS</th>
                      <th style={sth}>ACTION TAKEN</th>
                      <th style={sth}>SPARES USED</th>
                      <th style={sth}>STATUS</th>
                      <th style={sth}>SIGN.</th>
                      {edit && <th style={{ ...sth, background:"#fff" }} className="pm-noprint"></th>}
                    </tr></thead>
                    <tbody>
                      {(machine.points||[]).map((p,i)=>{
                        const E = (k) => edit
                          ? <td style={{ border:sb, padding:0 }}><textarea rows={1} defaultValue={p[k]||""}
                              onBlur={e=>editPoint(p,k,e.target.value)} style={{ ...inp, minHeight:26, background:"#fffbeb" }} /></td>
                          : <td style={{ border:sb, padding:"4px 6px", fontSize:11, verticalAlign:"top" }}>{p[k]||""}</td>;
                        return (
                          <tr key={p.id}>
                            <td style={{ ...sth, background:"#fff", fontSize:11 }}>{p.sno ?? i+1}</td>
                            {E("check_point")}{E("judgement_standard")}{E("method")}
                            {fin(p.id,"observation")}{fin(p.id,"action_taken")}{fin(p.id,"spares_used")}{fin(p.id,"status",true)}{fin(p.id,"sign",true)}
                            {edit && <td style={{ border:sb, textAlign:"center" }} className="pm-noprint">
                              <button onClick={()=>delPoint(p.id)} style={{ border:"none", background:"transparent", color:"#dc2626", cursor:"pointer", fontWeight:800 }}>×</button></td>}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
            );
          })()}
        </>
      )}
    </div>
  );
}
