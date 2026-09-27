// PyBypass.jsx — poka-yoke bypasses waiting for a quality decision.
//
// A bypass opens a case and mails the quality team with Approve / Reject
// buttons that work inside the mail.  Approve creates a deviation; reject sets
// the configured bit on the machine.  Both close by themselves when the
// poka-yoke is working again.  This page shows the live state; the decision is
// taken in the mail.  Admins can set the per-line bit here.
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import PageTopbar from "../components/PageTopbar";

const REFRESH_MS = 20000;
const fsel = { padding: "6px 10px", border: "1px solid #cbd5e1", borderRadius: 7, fontSize: 12.5 };

const dt = (iso) =>
  iso ? new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
const ago = (iso) => {
  if (!iso) return "—";
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  return m < 60 ? `${m} min` : `${(m / 60).toFixed(1)} h`;
};

const STATUS = {
  WAITING:  ["Waiting for quality", "#b45309", "#fef3c7"],
  APPROVED: ["Approved", "#15803d", "#dcfce7"],
  REJECTED: ["Rejected", "#b91c1c", "#fee2e2"],
  CLEARED:  ["Cleared before decision", "#475569", "#e2e8f0"],
};

function Pill({ status }) {
  const [label, fg, bg] = STATUS[status] || [status, "#475569", "#e2e8f0"];
  return (
    <span style={{ background: bg, color: fg, padding: "2px 10px", borderRadius: 999,
                   fontSize: 12, fontWeight: 700, whiteSpace: "nowrap" }}>{label}</span>
  );
}

function Kpi({ label, value, tone }) {
  return (
    <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderTop: `3px solid ${tone}`,
                  borderRadius: 10, padding: "12px 14px" }}>
      <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".05em", color: "#64748b" }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 800, color: "#0f172a", marginTop: 2 }}>{value}</div>
    </div>
  );
}

//  2026-09-27 — full history.  The two tabs above only ever showed what is
//  open plus the last 24 hours, so "which bypass produced which deviation, and
//  where does it stand" had no answer on screen.  This asks the API for the
//  whole record with filters and says the outcome in words.
function History({ token }) {
  const today = new Date().toISOString().slice(0, 10);
  const weekAgo = new Date(Date.now() - 6 * 864e5).toISOString().slice(0, 10);
  const [f, setF] = useState({ date_from: weekAgo, date_to: today, zone_id: "", line_id: "",
                               shift: "", status: "", machine: "", q: "" });
  const [machineOpts, setMachineOpts] = useState([]);
  const [d, setD] = useState(null);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [meta, setMeta] = useState({ lines: [], shifts: ["A", "B"] });

  useEffect(() => {
    api.get("/api/py-bypass/mail", token)
       .then(r => setMeta({ lines: r?.lines || [], shifts: r?.shifts || ["A", "B"] }))
       .catch(() => {});
  }, [token]);

  const load = useCallback(async (pg = page) => {
    setBusy(true);
    try {
      const qs = new URLSearchParams({ page: String(pg), page_size: "200" });
      Object.entries(f).forEach(([k, v]) => { if (v) qs.set(k, v); });
      const r = await api.get(`/api/py-bypass/history?${qs}`, token);
      setD(r);
      //  Machine list comes from what the range actually contains, so the
      //  dropdown never offers a machine with nothing behind it.
      if (!f.machine) {
        setMachineOpts([...new Set((r?.rows || [])
          .map(x => x.machine_label).filter(Boolean))].sort());
      }
    } catch (e) { setD({ rows: [], total: 0, error: e?.message || "Could not load history." }); }
    setBusy(false);
  }, [f, page, token]);
  useEffect(() => { load(1); setPage(1); }, [token]);   // eslint-disable-line

  const zones = useMemo(() => {
    const m = new Map();
    for (const l of meta.lines) if (l.zone_id) m.set(String(l.zone_id), l.zone_name);
    return [...m.entries()];
  }, [meta.lines]);
  const linesFor = useMemo(
    () => meta.lines.filter(l => !f.zone_id || String(l.zone_id) === String(f.zone_id)),
    [meta.lines, f.zone_id]);

  const rows = d?.rows || [];
  const total = d?.total || 0;
  const pages = Math.max(1, Math.ceil(total / 200));
  const set = (k) => (e) => setF(x => ({ ...x, [k]: e.target.value,
                                         ...(k === "zone_id" ? { line_id: "" } : {}) }));
  const inp = { padding: "6px 9px", border: "1px solid #cbd5e1", borderRadius: 7, fontSize: 12.5 };
  const th2 = { textAlign: "left", padding: "9px 10px", borderBottom: "1px solid #e2e8f0", whiteSpace: "nowrap" };
  const td2 = { padding: "8px 10px", verticalAlign: "top" };
  const tone = (o) => o?.startsWith("Deviation pending") ? "#b45309"
             : o?.startsWith("Deviation approved") ? "#15803d"
             : o?.startsWith("Deviation closed") ? "#1e40af"
             : o?.startsWith("Rejected") ? "#b91c1c"
             : o?.startsWith("Waiting") ? "#b45309" : "#475569";

  return (
    <div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", marginBottom: 12 }}>
        <label style={{ display: "grid", gap: 3, fontSize: 10.5, fontWeight: 700, color: "#64748b" }}>FROM
          <input type="date" value={f.date_from} max={f.date_to} onChange={set("date_from")} style={inp} /></label>
        <label style={{ display: "grid", gap: 3, fontSize: 10.5, fontWeight: 700, color: "#64748b" }}>TO
          <input type="date" value={f.date_to} min={f.date_from} onChange={set("date_to")} style={inp} /></label>
        <label style={{ display: "grid", gap: 3, fontSize: 10.5, fontWeight: 700, color: "#64748b" }}>ZONE
          <select value={f.zone_id} onChange={set("zone_id")} style={{ ...inp, minWidth: 140 }}>
            <option value="">All zones</option>
            {zones.map(([id, nm]) => <option key={id} value={id}>{nm}</option>)}
          </select></label>
        <label style={{ display: "grid", gap: 3, fontSize: 10.5, fontWeight: 700, color: "#64748b" }}>LINE
          <select value={f.line_id} onChange={set("line_id")} style={{ ...inp, minWidth: 160 }}>
            <option value="">All lines</option>
            {linesFor.map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
          </select></label>
        <label style={{ display: "grid", gap: 3, fontSize: 10.5, fontWeight: 700, color: "#64748b" }}>SHIFT
          <select value={f.shift} onChange={set("shift")} style={inp}>
            <option value="">All</option>
            {meta.shifts.map(x => <option key={x} value={x}>{x}</option>)}
          </select></label>
        <label style={{ display: "grid", gap: 3, fontSize: 10.5, fontWeight: 700, color: "#64748b" }}>MACHINE
          <select value={f.machine} onChange={set("machine")} style={{ ...inp, minWidth: 160 }}>
            <option value="">All machines</option>
            {machineOpts.map(mn => <option key={mn} value={mn}>{mn}</option>)}
          </select></label>
        <label style={{ display: "grid", gap: 3, fontSize: 10.5, fontWeight: 700, color: "#64748b" }}>STATUS
          <select value={f.status} onChange={set("status")} style={inp}>
            <option value="">All</option>
            {["WAITING", "APPROVED", "REJECTED", "CLEARED"].map(x => <option key={x} value={x}>{x}</option>)}
          </select></label>
        <label style={{ display: "grid", gap: 3, fontSize: 10.5, fontWeight: 700, color: "#64748b" }}>SEARCH
          <input value={f.q} onChange={set("q")} placeholder="PY / line / DEV no" style={{ ...inp, minWidth: 170 }} /></label>
        <button onClick={() => { setPage(1); load(1); }} disabled={busy}
                style={{ background: "#1e40af", color: "#fff", border: "none", borderRadius: 7,
                         padding: "8px 16px", fontSize: 13, fontWeight: 700, cursor: "pointer" }}>
          {busy ? "Loading…" : "Load"}</button>
      </div>

      {d?.error && <div style={{ background: "#fee2e2", color: "#b91c1c", padding: "9px 13px",
                                 borderRadius: 8, marginBottom: 10, fontSize: 13 }}>{d.error}</div>}

      <div style={{ fontSize: 12.5, color: "#64748b", marginBottom: 8 }}>
        {total} bypass{total === 1 ? "" : "es"} found
        {Object.entries(d?.counts || {}).map(([k, v]) => ` · ${k} ${v}`).join("")}
      </div>

      <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 10, background: "#fff" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5, minWidth: 1120 }}>
          <thead><tr style={{ background: "#f8fafc", color: "#64748b", fontSize: 11, textTransform: "uppercase" }}>
            {["Detected", "Shift", "Line / zone", "Machine", "Poka-Yoke", "Expected / actual", "Status",
              "Decided by", "Deviation", "Deviation state", "Outcome", "Closed"]
              .map(h => <th key={h} style={th2}>{h}</th>)}
          </tr></thead>
          <tbody>
            {rows.length === 0 && !busy && (
              <tr><td colSpan={12} style={{ padding: 22, textAlign: "center", color: "#94a3b8" }}>
                No bypass in this range.</td></tr>)}
            {rows.map(c => (
              <tr key={c.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                <td style={td2}>{dt(c.detected_at)}</td>
                <td style={td2}>{c.shift_name || "—"}</td>
                <td style={td2}><b>{c.line_name || c.line_id}</b>
                  <div style={{ color: "#94a3b8", fontSize: 11 }}>{c.zone_name}</div></td>
                <td style={td2}>{c.machine_label || "—"}</td>
                <td style={td2}>{c.py_name || "—"}
                  <div style={{ color: "#94a3b8", fontSize: 11, fontFamily: "monospace" }}>{c.py_no}</div></td>
                <td style={{ ...td2, fontFamily: "monospace", fontSize: 12 }}>
                  {(c.expected_value || "—")} → <b style={{ color: "#b91c1c" }}>{c.actual_value || "—"}</b></td>
                <td style={td2}><Pill status={c.status} /></td>
                <td style={td2}>{c.decided_by || "—"}
                  {c.decided_at && <div style={{ color: "#94a3b8", fontSize: 11 }}>{dt(c.decided_at)}</div>}</td>
                <td style={td2}>{c.deviation_no
                  ? <span style={{ color: "#15803d", fontWeight: 700 }}>{c.deviation_no}</span>
                  : <span style={{ color: "#94a3b8" }}>none</span>}</td>
                <td style={td2}>{c.dev_status || "—"}
                  {c.dev_upto_date && <div style={{ color: "#94a3b8", fontSize: 11 }}>upto {c.dev_upto_date}</div>}</td>
                <td style={{ ...td2, fontWeight: 700, color: tone(c.outcome) }}>{c.outcome}</td>
                <td style={td2}>{c.closed_at ? dt(c.closed_at) : "—"}
                  {c.close_reason && <div style={{ color: "#94a3b8", fontSize: 11 }}>{c.close_reason}</div>}</td>
              </tr>))}
          </tbody>
        </table>
      </div>

      {pages > 1 && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10, fontSize: 12.5 }}>
          <button onClick={() => { const n = page - 1; setPage(n); load(n); }} disabled={page <= 1 || busy}
                  style={{ ...inp, cursor: "pointer" }}>‹ Prev</button>
          <span style={{ color: "#64748b" }}>Page {page} of {pages}</span>
          <button onClick={() => { const n = page + 1; setPage(n); load(n); }} disabled={page >= pages || busy}
                  style={{ ...inp, cursor: "pointer" }}>Next ›</button>
        </div>)}
    </div>
  );
}


//  2026-09-27 — who gets the approval mail, per LINE and per SHIFT.  Empty To
//  deletes the row, so that line/shift falls back to the line-wide row and then
//  to Admin → Mail Config.
function MailConfig({ token }) {
  const [d, setD] = useState(null);
  const [draft, setDraft] = useState({ line_id: "", shift_name: "", to_addrs: "", cc_addrs: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    try { setD(await api.get("/api/py-bypass/mail", token)); }
    catch (e) { setMsg(e?.message || "Could not load mail settings."); }
  }, [token]);
  useEffect(() => { load(); }, [load]);

  const save = async (row) => {
    if (!row.line_id) { setMsg("Pick a line first."); return; }
    setBusy(true); setMsg("");
    try {
      const r = await api.put("/api/py-bypass/mail", {
        line_id: Number(row.line_id), shift_name: row.shift_name || "",
        to_addrs: row.to_addrs || "", cc_addrs: row.cc_addrs || "",
      }, token);
      setMsg(r?.cleared ? "Removed — that line/shift now uses the fallback." : "Saved ✓");
      setDraft({ line_id: "", shift_name: "", to_addrs: "", cc_addrs: "" });
      await load();
    } catch (e) { setMsg(e?.message || "Save failed."); }
    setBusy(false);
  };

  const inp = { padding: "6px 9px", border: "1px solid #cbd5e1", borderRadius: 7, fontSize: 12.5 };
  const th2 = { textAlign: "left", padding: "9px 10px", borderBottom: "1px solid #e2e8f0", fontSize: 11,
                textTransform: "uppercase", color: "#64748b" };
  const rows = d?.rows || [];

  return (
    <div style={{ marginTop: 26 }}>
      <div style={{ fontSize: 15, fontWeight: 700, color: "#0f172a", marginBottom: 4 }}>
        Bypass approval mail, per line and shift
      </div>
      <div style={{ color: "#64748b", fontSize: 12, marginBottom: 10 }}>
        The approve / reject mail goes to the most specific match: that line and shift first,
        then a row for the line with shift left blank, and finally the global list in
        Admin → Mail Config. Clear the To field to delete a row.
        {d && <> Current fallback: <b>{(d.fallback?.to || []).join(", ") || "none set"}</b>.</>}
      </div>
      {msg && <div style={{ fontSize: 12.5, marginBottom: 8,
                            color: msg.includes("✓") || msg.startsWith("Removed") ? "#15803d" : "#b91c1c" }}>{msg}</div>}

      <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 10, background: "#fff" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 860 }}>
          <thead><tr style={{ background: "#f8fafc" }}>
            {["Line", "Shift", "To", "Cc", "Updated", ""].map(h => <th key={h} style={th2}>{h}</th>)}
          </tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.line_id}-${r.shift_name}`} style={{ borderBottom: "1px solid #f1f5f9" }}>
                <td style={{ padding: "7px 10px", fontWeight: 600 }}>{r.line_name || r.line_id}
                  <div style={{ color: "#94a3b8", fontSize: 11 }}>{r.zone_name}</div></td>
                <td style={{ padding: "7px 10px" }}>{r.shift_name || "all shifts"}</td>
                <td style={{ padding: "7px 10px" }}>
                  <input value={r.to_addrs || ""} style={{ ...inp, width: 240 }}
                         onChange={e => { const n = [...rows]; n[i] = { ...r, to_addrs: e.target.value };
                                          setD({ ...d, rows: n }); }} /></td>
                <td style={{ padding: "7px 10px" }}>
                  <input value={r.cc_addrs || ""} style={{ ...inp, width: 200 }}
                         onChange={e => { const n = [...rows]; n[i] = { ...r, cc_addrs: e.target.value };
                                          setD({ ...d, rows: n }); }} /></td>
                <td style={{ padding: "7px 10px", color: "#94a3b8", fontSize: 11 }}>
                  {r.updated_at ? `${dt(r.updated_at)} · ${r.updated_by || ""}` : "—"}</td>
                <td style={{ padding: "7px 10px" }}>
                  <button onClick={() => save(r)} disabled={busy}
                          style={{ background: "#1e40af", color: "#fff", border: "none", borderRadius: 6,
                                   padding: "5px 14px", cursor: "pointer", fontSize: 12, fontWeight: 600 }}>
                    Save</button></td>
              </tr>))}
            <tr style={{ background: "#f8fafc" }}>
              <td style={{ padding: "7px 10px" }}>
                <select value={draft.line_id} onChange={e => setDraft({ ...draft, line_id: e.target.value })}
                        style={{ ...inp, minWidth: 170 }}>
                  <option value="">+ Add line…</option>
                  {(d?.lines || []).map(l => <option key={l.id} value={l.id}>{l.line_name}</option>)}
                </select></td>
              <td style={{ padding: "7px 10px" }}>
                <select value={draft.shift_name} onChange={e => setDraft({ ...draft, shift_name: e.target.value })}
                        style={inp}>
                  <option value="">all shifts</option>
                  {(d?.shifts || []).map(x => <option key={x} value={x}>{x}</option>)}
                </select></td>
              <td style={{ padding: "7px 10px" }}>
                <input value={draft.to_addrs} placeholder="name@tbdi.in, other@tbdi.in"
                       onChange={e => setDraft({ ...draft, to_addrs: e.target.value })}
                       style={{ ...inp, width: 240 }} /></td>
              <td style={{ padding: "7px 10px" }}>
                <input value={draft.cc_addrs} placeholder="optional"
                       onChange={e => setDraft({ ...draft, cc_addrs: e.target.value })}
                       style={{ ...inp, width: 200 }} /></td>
              <td />
              <td style={{ padding: "7px 10px" }}>
                <button onClick={() => save(draft)} disabled={busy || !draft.line_id}
                        style={{ background: "#16a34a", color: "#fff", border: "none", borderRadius: 6,
                                 padding: "5px 14px", cursor: "pointer", fontSize: 12, fontWeight: 600,
                                 opacity: draft.line_id ? 1 : .5 }}>Add</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}


//  2026-09-27 — bit setup per LINE and per MACHINE.  Two bits per machine:
//  the STOP bit MES sets when quality rejects a bypass (and clears when the
//  PY reads OK again), and a BYPASS bit the operator can switch on by hand.
//
//  About "state": this system has NO live read-back of an arbitrary PLC bit.
//  MES queues the write in mes_plc_bit_commands and the line's own collector
//  applies it, because the PLC allows one session and the collector holds it.
//  So the state shown is the last value MES wrote plus whether the collector
//  confirmed it — and it says exactly that, rather than pretending to be live.
function BitSetup({ token }) {
  const [d, setD] = useState(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [openLine, setOpenLine] = useState({});

  const load = useCallback(async () => {
    try { setD(await api.get("/api/py-bypass/bits", token)); }
    catch (e) { setMsg(e?.message || "Could not load bit setup."); }
  }, [token]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);

  const saveRow = async (line, m) => {
    setBusy(`${line.line_id}|${m.machine_key}`); setMsg("");
    try {
      await api.put("/api/py-bypass/bits", {
        line_id: line.line_id, machine_key: m.machine_key,
        bit_addr: m.bit_addr || "", bypass_bit_addr: m.bypass_bit_addr || "",
        active: m.active !== false,
      }, token);
      setMsg("Saved ✓"); await load();
    } catch (e) { setMsg(e?.message || "Save failed."); }
    setBusy("");
  };

  const toggleBypass = async (line, m) => {
    const turningOn = !m.manual_bypass_on;
    const what = `${turningOn ? "TURN ON" : "turn off"} the bypass bit ` +
                 `${m.bypass_bit_addr} on ${line.line_name} · ${m.machine_label}`;
    if (!window.confirm(`This writes to the machine.\n\n${what}?`)) return;
    setBusy(`${line.line_id}|${m.machine_key}`); setMsg("");
    try {
      const r = await api.post("/api/py-bypass/manual-bypass", {
        line_id: line.line_id, machine_key: m.machine_key, on: turningOn,
      }, token);
      setMsg(r?.detail || "Queued."); await load();
    } catch (e) { setMsg(e?.message || "Could not queue the bit."); }
    setBusy("");
  };

  const patch = (li, mi, key, val) => {
    const lines = [...(d?.lines || [])];
    const ms = [...lines[li].machines];
    ms[mi] = { ...ms[mi], [key]: val };
    lines[li] = { ...lines[li], machines: ms };
    setD({ ...d, lines });
  };

  const State = ({ st }) => {
    if (!st) return <span style={{ color: "#94a3b8" }}>—</span>;
    if (!st.known) return <span style={{ color: "#94a3b8" }} title="MES has never written this bit">
      {st.bit} · never written</span>;
    const bad = st.applied_ok === false;
    const pending = st.applied_ok == null;
    const col = bad ? "#b91c1c" : st.on ? "#b45309" : "#15803d";
    return (
      <span style={{ color: col, fontWeight: 700 }}
            title={[st.reason, st.error, st.applied_at ? `applied ${dt(st.applied_at)}` : "not applied yet"]
                     .filter(Boolean).join(" · ")}>
        {st.bit} · {st.on ? "ON" : "off"}
        {pending && <span style={{ color: "#64748b", fontWeight: 600 }}> · queued</span>}
        {bad && <span style={{ fontWeight: 600 }}> · write failed</span>}
      </span>);
  };

  const bi = { padding: "5px 8px", border: "1px solid #cbd5e1", borderRadius: 6,
               fontFamily: "monospace", textTransform: "uppercase", width: 100 };
  const th2 = { textAlign: "left", padding: "8px 10px", borderBottom: "1px solid #e2e8f0",
                fontSize: 11, textTransform: "uppercase", color: "#64748b" };

  return (
    <div style={{ marginTop: 26 }}>
      <div style={{ fontSize: 15, fontWeight: 700, color: "#0f172a", marginBottom: 4 }}>
        Stop bit and bypass bit, per line and machine
      </div>
      <div style={{ color: "#64748b", fontSize: 12, marginBottom: 10 }}>
        <b>Stop bit</b> — MES sets this when quality rejects a bypass, and clears it when the
        poka-yoke reads OK again. <b>Bypass bit</b> — you switch it on yourself for that machine;
        while it is on it shows in the bypass list above. Leave a field empty and nothing is ever
        written for it. What each bit does on the machine is ladder logic.
        <div style={{ marginTop: 4 }}>
          State below is the <b>last value MES wrote</b> and whether the line's collector confirmed
          it — there is no live read-back of a PLC bit in this system.
        </div>
      </div>
      {msg && <div style={{ fontSize: 12.5, marginBottom: 8,
                            color: msg.includes("✓") || msg.includes("queued") ? "#15803d" : "#b91c1c" }}>{msg}</div>}

      {(d?.lines || []).map((line, li) => {
        const set = line.machines.filter(m => m.bit_addr || m.bypass_bit_addr).length;
        const on  = line.machines.filter(m => m.manual_bypass_on).length;
        const isOpen = !!openLine[line.line_id];
        return (
          <div key={line.line_id} style={{ border: "1px solid #e2e8f0", borderRadius: 10,
                                           background: "#fff", marginBottom: 8 }}>
            <button onClick={() => setOpenLine(o => ({ ...o, [line.line_id]: !isOpen }))}
                    style={{ width: "100%", display: "flex", alignItems: "center", gap: 10,
                             background: "none", border: "none", cursor: "pointer",
                             padding: "10px 12px", textAlign: "left" }}>
              <span style={{ color: "#64748b" }}>{isOpen ? "▾" : "▸"}</span>
              <b style={{ fontSize: 13.5 }}>{line.line_name}</b>
              <span style={{ color: "#94a3b8", fontSize: 12 }}>{line.zone_name}</span>
              <span style={{ marginLeft: "auto", fontSize: 12, color: "#64748b" }}>
                {set} of {line.machines.length} configured
                {on > 0 && <b style={{ color: "#b45309" }}> · {on} bypass ON</b>}
                {line.holding?.length > 0 && <b style={{ color: "#b91c1c" }}> · {line.holding.length} stop held</b>}
              </span>
            </button>
            {isOpen && (
              <div style={{ overflowX: "auto", borderTop: "1px solid #f1f5f9" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 900 }}>
                  <thead><tr style={{ background: "#f8fafc" }}>
                    {["Machine", "Stop bit", "Stop state", "Bypass bit", "Bypass state",
                      "Manual bypass", "Updated", ""].map(h => <th key={h} style={th2}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {line.machines.map((m, mi) => (
                      <tr key={m.machine_key} style={{ borderBottom: "1px solid #f1f5f9" }}>
                        <td style={{ padding: "7px 10px", fontWeight: m.machine_key ? 600 : 500,
                                     color: m.machine_key ? "#0f172a" : "#64748b" }}>{m.machine_label}</td>
                        <td style={{ padding: "7px 10px" }}>
                          <input value={m.bit_addr || ""} placeholder="M500" style={bi}
                                 onChange={e => patch(li, mi, "bit_addr", e.target.value)} /></td>
                        <td style={{ padding: "7px 10px", fontSize: 12 }}><State st={m.stop_state} /></td>
                        <td style={{ padding: "7px 10px" }}>
                          <input value={m.bypass_bit_addr || ""} placeholder="M510" style={bi}
                                 onChange={e => patch(li, mi, "bypass_bit_addr", e.target.value)} /></td>
                        <td style={{ padding: "7px 10px", fontSize: 12 }}><State st={m.bypass_state} /></td>
                        <td style={{ padding: "7px 10px" }}>
                          {m.bypass_bit_addr ? (
                            <button onClick={() => toggleBypass(line, m)}
                                    disabled={busy === `${line.line_id}|${m.machine_key}`}
                                    style={{ border: `1px solid ${m.manual_bypass_on ? "#b45309" : "#cbd5e1"}`,
                                             background: m.manual_bypass_on ? "#b45309" : "#fff",
                                             color: m.manual_bypass_on ? "#fff" : "#475569",
                                             borderRadius: 999, padding: "4px 12px", fontSize: 12,
                                             fontWeight: 700, cursor: "pointer" }}>
                              {m.manual_bypass_on ? "ON — turn off" : "Turn on"}
                            </button>)
                            : <span style={{ color: "#94a3b8", fontSize: 12 }}>set a bit first</span>}
                          {m.manual_bypass_on && (
                            <div style={{ color: "#94a3b8", fontSize: 11 }}>
                              {m.manual_by} · {dt(m.manual_at)}</div>)}
                        </td>
                        <td style={{ padding: "7px 10px", color: "#94a3b8", fontSize: 11 }}>
                          {m.updated_at ? `${dt(m.updated_at)} · ${m.updated_by || ""}` : "—"}</td>
                        <td style={{ padding: "7px 10px" }}>
                          <button onClick={() => saveRow(line, m)}
                                  disabled={busy === `${line.line_id}|${m.machine_key}`}
                                  style={{ background: "#1e40af", color: "#fff", border: "none",
                                           borderRadius: 6, padding: "5px 14px", cursor: "pointer",
                                           fontSize: 12, fontWeight: 600 }}>Save</button></td>
                      </tr>))}
                  </tbody>
                </table>
              </div>)}
          </div>);
      })}
    </div>
  );
}


export default function PyBypass() {
  //  2026-09-25 — this page was written against an axios-shaped client: it
  //  called api.get(path) with no token, read r.data, and looked for
  //  e.response.status.  api/client.jsx is a fetch wrapper: the token is the
  //  SECOND argument, the parsed body IS the return value, and a failure is a
  //  plain Error whose message holds the detail.  So every call here went out
  //  with no Authorization header, the backend answered 401, and the client's
  //  401 handler sent the user to the login screen — opening PY Bypass logged
  //  you out.
  const { user, token, canAccessModule } = useAuth();
  const isAdmin = (user?.role || "") === "admin";
  const canMod = (m) => (canAccessModule ? canAccessModule("py-bypass", m) : true);
  //  2026-09-27 — the open/closed lists are filtered by zone, line and machine
  //  (operator: "open list bhi per machine line zone k according ho").
  const [fz, setFz] = useState("");
  const [fl, setFl] = useState("");
  const [fm, setFm] = useState("");
  const [grp, setGrp] = useState(true);
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState("open");

  const load = useCallback(async () => {
    if (!token) return;
    try {
      setData(await api.get("/api/py-bypass/cases?hours=24", token));
      setErr("");
    } catch (e) {
      const msg = e?.message || "";
      setErr(msg.includes("404")
        ? "This page becomes active after the next MES-API restart."
        : (msg || "Could not load bypass cases."));
    }
  }, [token]);

  useEffect(() => { load(); }, [load]);   // eslint-disable-line react-hooks/set-state-in-effect
  useEffect(() => {
    const t = setInterval(() => load(), REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  const counts = data?.counts || {};
  const baseRows = useMemo(() => (tab === "open" ? data?.open : data?.recent) || [], [tab, data]);
  const machineOf = (c) => c.machine_label || c.machine_name || c.station_code || "—";
  const zoneOpts = useMemo(() => {
    const m = new Map();
    for (const c of baseRows) if (c.zone_name) m.set(String(c.zone_id ?? c.zone_name), c.zone_name);
    return [...m.entries()];
  }, [baseRows]);
  const lineOpts = useMemo(() => {
    const m = new Map();
    for (const c of baseRows) {
      if (fz && String(c.zone_id ?? c.zone_name) !== fz) continue;
      if (c.line_id != null) m.set(String(c.line_id), c.line_name || c.line_id);
    }
    return [...m.entries()];
  }, [baseRows, fz]);
  const machineOpts = useMemo(() => {
    const set = new Set();
    for (const c of baseRows) {
      if (fz && String(c.zone_id ?? c.zone_name) !== fz) continue;
      if (fl && String(c.line_id) !== fl) continue;
      set.add(machineOf(c));
    }
    return [...set].sort();
  }, [baseRows, fz, fl]);
  const rows = useMemo(() => baseRows.filter(c =>
    (!fz || String(c.zone_id ?? c.zone_name) === fz) &&
    (!fl || String(c.line_id) === fl) &&
    (!fm || machineOf(c) === fm)), [baseRows, fz, fl, fm]);
  //  Grouped view: zone -> line -> machine, so a leader reads their own line
  //  without scanning the whole plant.
  const grouped = useMemo(() => {
    const out = [];
    const key = (c) => `${c.zone_name || "—"}||${c.line_name || c.line_id}`;
    const seen = new Map();
    for (const c of rows) {
      const k = key(c);
      if (!seen.has(k)) { seen.set(k, { zone: c.zone_name || "—", line: c.line_name || c.line_id, items: [] }); }
      seen.get(k).items.push(c);
    }
    for (const g of seen.values()) {
      g.items.sort((a, b) => String(machineOf(a)).localeCompare(String(machineOf(b))));
      out.push(g);
    }
    out.sort((a, b) => (a.zone + a.line).localeCompare(b.zone + b.line));
    return out;
  }, [rows]);

  return (
    <div style={{ padding: 16, maxWidth: 1280, margin: "0 auto" }}>
      <PageTopbar title="Poka-Yoke Bypass" />
      <div style={{ color: "#64748b", fontSize: 13, margin: "2px 0 14px" }}>
        Quality approves or rejects each bypass from the mail. Approve creates a deviation;
        reject sets the machine bit. Both clear automatically when the poka-yoke is OK again.
        {data ? ` Reminder after ${data.reminder_min} min, escalation after ${data.escalate_min} min.` : ""}
      </div>

      {err && <div style={{ background: "#fee2e2", color: "#b91c1c", padding: "10px 14px",
                            borderRadius: 8, marginBottom: 12, fontSize: 13 }}>{err}</div>}

      <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fill,minmax(150px,1fr))",
                    marginBottom: 16 }}>
        <Kpi label="Open" value={counts.open ?? "—"} tone="#1e40af" />
        <Kpi label="Waiting for quality" value={counts.waiting ?? "—"} tone="#b45309" />
        <Kpi label="Approved (24 h)" value={counts.approved ?? "—"} tone="#15803d" />
        <Kpi label="Rejected (24 h)" value={counts.rejected ?? "—"} tone="#b91c1c" />
        <Kpi label="Machine bit ON" value={counts.bit_on ?? "—"} tone="#7c3aed" />
      </div>

      <div style={{ display: "flex", gap: 18, borderBottom: "1px solid #e2e8f0", marginBottom: 12 }}>
        {[["open", `Open (${data?.open?.length ?? 0})`],
          ["recent", `Closed, last 24 h (${data?.recent?.length ?? 0})`],
          ["history", "History"]].filter(([k]) => canMod(k))
          .map(([k, label]) => (
            <button key={k} onClick={() => setTab(k)} style={{
              background: "none", border: "none", padding: "8px 2px", cursor: "pointer", fontSize: 14,
              fontWeight: tab === k ? 700 : 500, color: tab === k ? "#0f172a" : "#64748b",
              borderBottom: tab === k ? "2px solid #1e40af" : "2px solid transparent" }}>{label}</button>
          ))}
      </div>

      {tab !== "history" && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 10 }}>
          <select value={fz} onChange={e => { setFz(e.target.value); setFl(""); setFm(""); }}
                  style={fsel}>
            <option value="">All zones</option>
            {zoneOpts.map(([id, nm]) => <option key={id} value={id}>{nm}</option>)}
          </select>
          <select value={fl} onChange={e => { setFl(e.target.value); setFm(""); }} style={fsel}>
            <option value="">All lines</option>
            {lineOpts.map(([id, nm]) => <option key={id} value={id}>{nm}</option>)}
          </select>
          <select value={fm} onChange={e => setFm(e.target.value)} style={fsel}>
            <option value="">All machines</option>
            {machineOpts.map(mn => <option key={mn} value={mn}>{mn}</option>)}
          </select>
          {(fz || fl || fm) && (
            <button onClick={() => { setFz(""); setFl(""); setFm(""); }}
                    style={{ ...fsel, cursor: "pointer" }}>Clear</button>)}
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, color: "#64748b" }}>
            <input type="checkbox" checked={grp} onChange={e => setGrp(e.target.checked)} />
            Group by zone / line / machine
          </label>
          <span style={{ fontSize: 12.5, color: "#64748b", marginLeft: "auto" }}>
            {rows.length} of {baseRows.length} shown
          </span>
        </div>)}

      {tab === "history" && canMod("history") && <History token={token} />}

      {tab !== "history" && (
      <div style={{ overflowX: "auto", border: "1px solid #e2e8f0", borderRadius: 10, background: "#fff" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 900 }}>
          <thead>
            <tr style={{ background: "#f8fafc", color: "#64748b", fontSize: 11, textTransform: "uppercase" }}>
              {["Line", "Poka-Yoke", "Register", "Expected / actual", "Detected", "Waiting", "Status", "Decision", "Result"]
                .map((h) => <th key={h} style={{ textAlign: "left", padding: "9px 10px", borderBottom: "1px solid #e2e8f0" }}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={9} style={{ padding: 22, textAlign: "center", color: "#15803d", fontWeight: 600 }}>
                No bypass {tab === "open" ? "open right now" : "closed in the last 24 hours"}.
              </td></tr>
            )}
            {(grp ? grouped : [{ zone: null, line: null, items: rows }]).map((g, gi) => (
              <Fragment key={gi}>
                {g.zone !== null && (
                  <tr><td colSpan={9} style={{ background: "#f1f5f9", padding: "6px 10px",
                                               fontSize: 12, fontWeight: 800, color: "#334155" }}>
                    {g.zone} · {g.line}
                    <span style={{ color: "#64748b", fontWeight: 600 }}> — {g.items.length} bypass</span>
                  </td></tr>)}
                {g.items.map((c) => (
              <tr key={c.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                <td style={{ padding: "9px 10px" }}>
                  <b>{c.line_name || c.line_id}</b>
                  <div style={{ color: "#94a3b8", fontSize: 11 }}>{c.zone_name}</div>
                </td>
                <td style={{ padding: "9px 10px" }}>
                  {c.py_name || "—"}
                  <div style={{ color: "#94a3b8", fontSize: 11 }}>
                    {c.py_no}{machineOf(c) !== "—" ? ` · ${machineOf(c)}` : ""}</div>
                </td>
                <td style={{ padding: "9px 10px", fontFamily: "monospace" }}>{c.register_addr || "—"}</td>
                <td style={{ padding: "9px 10px", fontFamily: "monospace", fontSize: 12 }}>
                  {(c.expected_value || "—")} → <b style={{ color: "#b91c1c" }}>{c.actual_value || "—"}</b>
                </td>
                <td style={{ padding: "9px 10px" }}>{dt(c.detected_at)}</td>
                <td style={{ padding: "9px 10px" }}>{c.decided_at ? "—" : ago(c.detected_at)}</td>
                <td style={{ padding: "9px 10px" }}><Pill status={c.status} /></td>
                <td style={{ padding: "9px 10px" }}>
                  {c.decided_by || "—"}
                  {c.decided_at && <div style={{ color: "#94a3b8", fontSize: 11 }}>{dt(c.decided_at)}</div>}
                </td>
                <td style={{ padding: "9px 10px" }}>
                  {c.deviation_no && <span style={{ color: "#15803d", fontWeight: 600 }}>{c.deviation_no}</span>}
                  {c.bit_addr && (
                    <span style={{ color: c.bit_state ? "#b91c1c" : "#64748b", fontWeight: 600 }}>
                      Bit {c.bit_addr} {c.bit_state ? "ON" : "off"}
                    </span>
                  )}
                  {!c.deviation_no && !c.bit_addr && (c.status === "REJECTED"
                    ? <span style={{ color: "#b45309" }}>No bit configured</span> : "—")}
                  {c.closed_at && <div style={{ color: "#94a3b8", fontSize: 11 }}>{c.close_reason}</div>}
                </td>
              </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>)}

      {isAdmin && canMod("mail") && <MailConfig token={token} />}

      {isAdmin && canMod("bit") && <BitSetup token={token} />}
    </div>
  );
}
