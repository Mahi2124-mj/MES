/**
 * Quality → Red Bin Lock  (2026-10-07)
 *
 * A bar code LOCKED in the Red Bin portal must not pass Final Inspection.
 * The portal owns the lock list (redbin."PartLock"); MES only reads it.  The
 * collector checks every Final part and, on a locked code, pulses the line's
 * Red Bin bit (Bit Master tab; initially L230, the Semi-Auto NG bit) and logs
 * the restriction (Restricted at Final tab).
 */
import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const pad2     = (n) => String(n).padStart(2, "0");
const localYmd = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
const todayStr = () => localYmd(new Date());
const daysAgo  = (n) => { const d = new Date(); d.setDate(d.getDate() - n); return localYmd(d); };

const lbl = { fontSize: 11, fontWeight: 800, color: "#475569",
              display: "block", marginBottom: 4, letterSpacing: ".04em" };
const inp = { width: "100%", padding: "8px 10px", borderRadius: 8,
              border: "1px solid #cbd5e1", fontSize: 13, background: "#fff",
              boxSizing: "border-box", height: 38 };
const field = { flex: "1 1 150px", minWidth: 140, maxWidth: 220 };
const th = { padding: "9px 10px", fontWeight: 800, color: "#334155",
             whiteSpace: "nowrap", textAlign: "left", background: "#f1f5f9",
             borderBottom: "1px solid #e2e8f0", position: "sticky", top: 0 };
const td = { padding: "8px 10px", whiteSpace: "nowrap", borderBottom: "1px solid #f1f5f9" };
const card = { background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12,
               padding: 14, marginBottom: 14 };
const mono = { fontFamily: "monospace", fontWeight: 700 };
const btn = (bg, fg = "#fff") => ({ padding: "8px 14px", borderRadius: 8, border: "none",
  background: bg, color: fg, fontWeight: 800, fontSize: 13, cursor: "pointer", height: 38 });

function Badge({ text, tone }) {
  const t = {
    red:   ["#fee2e2", "#b91c1c"], green: ["#dcfce7", "#15803d"],
    amber: ["#fef3c7", "#b45309"], grey:  ["#f1f5f9", "#475569"],
    blue:  ["#dbeafe", "#1d4ed8"],
  }[tone] || ["#f1f5f9", "#475569"];
  return (
    <span style={{ background: t[0], color: t[1], padding: "2px 8px", borderRadius: 999,
                   fontSize: 11, fontWeight: 800, letterSpacing: ".03em" }}>{text}</span>
  );
}

function Msg({ kind, children }) {
  const c = kind === "error" ? ["#fef2f2", "#fecaca", "#b91c1c"] : ["#f0f9ff", "#bae6fd", "#0369a1"];
  return (
    <div style={{ background: c[0], border: `1px solid ${c[1]}`, color: c[2], borderRadius: 10,
                  padding: "10px 12px", fontSize: 13, fontWeight: 600, marginBottom: 12 }}>
      {children}
    </div>
  );
}

/* ── Locked parts (live from the Red Bin portal) ───────────────────────── */
function LockedTab({ token, onCount }) {
  const [data, setData] = useState(null);
  const [showAll, setShowAll] = useState(false);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.get(`/api/quality/redbin/locks?show=${showAll ? "all" : "locked"}`, token);
      setData(r);
      if (r?.counts) onCount?.(r.counts.LOCKED || 0);
    } catch (e) {
      setData({ ok: false, error: "Could not load the lock list", rows: [] });
    } finally { setLoading(false); }
  };
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); },
            // eslint-disable-next-line react-hooks/exhaustive-deps
            [showAll]);

  const rows = useMemo(() => {
    const s = q.trim().toUpperCase();
    return (data?.rows || []).filter(r => !s || (r.part_code || "").toUpperCase().includes(s)
      || (r.red_bin_number || "").toUpperCase().includes(s));
  }, [data, q]);

  return (
    <>
      <div style={card}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
          <div style={field}>
            <label style={lbl}>SHOW</label>
            <select style={inp} value={showAll ? "all" : "locked"}
                    onChange={e => setShowAll(e.target.value === "all")}>
              <option value="locked">Locked only</option>
              <option value="all">All (incl. unlocked)</option>
            </select>
          </div>
          <div style={{ ...field, maxWidth: 320 }}>
            <label style={lbl}>SEARCH</label>
            <input style={{ ...inp, ...mono, fontWeight: 400 }} value={q}
                   placeholder="Part code or Red Bin no." onChange={e => setQ(e.target.value)} />
          </div>
          <button style={btn("#1e3a8a")} onClick={load} disabled={loading}>
            {loading ? "Loading…" : "Refresh"}
          </button>
          <div style={{ marginLeft: "auto", fontSize: 12, color: "#64748b" }}>
            {data?.counts && <>Locked: <b style={{ color: "#b91c1c" }}>{data.counts.LOCKED || 0}</b>
              {data.counts.UNLOCKED ? <> · Unlocked: <b>{data.counts.UNLOCKED}</b></> : null}
              {" · "}</>}
            {data?.fetched_at ? `Updated ${data.fetched_at.slice(11)}` : ""} · auto-refresh 30 s
          </div>
        </div>
      </div>
      {data && !data.ok && (
        <Msg kind="error">{data.error} — Final machines keep using the last list they read.</Msg>
      )}
      <div style={{ ...card, padding: 0, overflow: "auto", maxHeight: "62vh" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead><tr>
            <th style={th}>Part Code</th><th style={th}>Lock</th><th style={th}>Red Bin No.</th>
            <th style={th}>Workflow Status</th><th style={th}>Zone</th><th style={th}>Line</th>
            <th style={th}>Part Name</th><th style={th}>Locked At</th><th style={th}>Locked By</th>
            {showAll && <><th style={th}>Unlocked At</th><th style={th}>Unlocked By</th></>}
          </tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.part_code}>
                <td style={{ ...td, ...mono }}>{r.part_code}</td>
                <td style={td}><Badge text={r.status} tone={r.status === "LOCKED" ? "red" : "green"} /></td>
                <td style={td}>{r.red_bin_number || "—"}</td>
                <td style={td}><Badge text={(r.record_status || "—").replace(/_/g, " ")} tone="amber" /></td>
                <td style={td}>{r.zone || "—"}</td>
                <td style={td}>{r.line || "—"}</td>
                <td style={td}>{r.part_name || "—"}</td>
                <td style={td}>{r.locked_at || "—"}</td>
                <td style={td}>{r.locked_by || "—"}</td>
                {showAll && <><td style={td}>{r.unlocked_at || "—"}</td><td style={td}>{r.unlocked_by || "—"}</td></>}
              </tr>
            ))}
            {!rows.length && (
              <tr><td style={{ ...td, color: "#64748b", padding: 18 }} colSpan={showAll ? 11 : 9}>
                {loading ? "Loading…" : "No locked parts."}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

/* ── Parts restricted at Final ─────────────────────────────────────────── */
function BlocksTab({ token, lines, onCount }) {
  const [from, setFrom] = useState(daysAgo(7));
  const [to, setTo] = useState(todayStr());
  const [lineId, setLineId] = useState("");
  const [pc, setPc] = useState("");
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const load = async () => {
    setLoading(true); setErr("");
    try {
      const p = new URLSearchParams({ date_from: from, date_to: to });
      if (lineId) p.set("line_id", lineId);
      if (pc.trim()) p.set("part_code", pc.trim());
      const r = await api.get(`/api/quality/redbin/blocks?${p}`, token);
      setRows(r?.rows || []); onCount?.((r?.rows || []).length);
    } catch { setErr("Could not load restricted parts"); }
    finally { setLoading(false); }
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, []);

  return (
    <>
      <div style={card}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
          <div style={field}><label style={lbl}>FROM</label>
            <input style={inp} type="date" value={from} max={todayStr()} onChange={e => setFrom(e.target.value)} /></div>
          <div style={field}><label style={lbl}>TO</label>
            <input style={inp} type="date" value={to} max={todayStr()} onChange={e => setTo(e.target.value)} /></div>
          <div style={field}><label style={lbl}>LINE</label>
            <select style={inp} value={lineId} onChange={e => setLineId(e.target.value)}>
              <option value="">All lines</option>
              {lines.map(l => <option key={l.line_id} value={l.line_id}>{l.line_name}</option>)}
            </select></div>
          <div style={{ ...field, maxWidth: 300 }}><label style={lbl}>PART CODE</label>
            <input style={{ ...inp, fontFamily: "monospace" }} value={pc} placeholder="partial match"
                   onChange={e => setPc(e.target.value)} onKeyDown={e => { if (e.key === "Enter") load(); }} /></div>
          <button style={btn("#1e3a8a")} onClick={load} disabled={loading}>{loading ? "Loading…" : "Search"}</button>
        </div>
      </div>
      {err && <Msg kind="error">{err}</Msg>}
      <div style={{ ...card, padding: 0, overflow: "auto", maxHeight: "62vh" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead><tr>
            <th style={th}>Time</th><th style={th}>Shift</th><th style={th}>Line</th>
            <th style={th}>Machine</th><th style={th}>Part Code</th><th style={th}>Red Bin No.</th>
            <th style={th}>Workflow Status</th><th style={th}>Locked By</th><th style={th}>Bit</th>
            <th style={th}>Bit Written</th><th style={th}>Note</th>
          </tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id}>
                <td style={td}>{r.ts}</td>
                <td style={td}>{r.shift_name || "—"}</td>
                <td style={{ ...td, fontWeight: 700 }}>{r.line_name}</td>
                <td style={td}>{r.machine_name || "—"}</td>
                <td style={{ ...td, ...mono }}>{r.part_code}</td>
                <td style={td}>{r.red_bin_number || "—"}</td>
                <td style={td}>{(r.record_status || "—").replace(/_/g, " ")}</td>
                <td style={td}>{r.locked_by || "—"}</td>
                <td style={{ ...td, ...mono }}>{r.bit_address}</td>
                <td style={td}>{r.bit_written ? <Badge text="YES" tone="green" /> : <Badge text="NO" tone="red" />}</td>
                <td style={{ ...td, color: "#64748b" }}>
                  {[r.note, r.lookup_source === "last-list" ? "Red Bin DB offline: used last list" : null]
                    .filter(Boolean).join(" · ") || "—"}
                </td>
              </tr>
            ))}
            {!rows.length && (
              <tr><td style={{ ...td, color: "#64748b", padding: 18 }} colSpan={11}>
                {loading ? "Loading…" : "No part was restricted at Final in this period."}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

/* ── Bit master: which Final bit each line uses ────────────────────────── */
function BitMasterTab({ token, data, reload }) {
  const [edit, setEdit] = useState(null);   // { line_id, bit_address, hold_sec, enabled }
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState(null);
  const [showAll, setShowAll] = useState(false);
  const canEdit = !!data?.can_edit;
  const rows = (data?.rows || []).filter(r => showAll || r.bit_address);

  const save = async () => {
    setSaving(true); setMsg(null);
    try {
      await api.post("/api/quality/redbin/bit-master", {
        line_id: edit.line_id, bit_address: edit.bit_address,
        hold_sec: Number(edit.hold_sec), enabled: !!edit.enabled,
      }, token);
      setMsg({ kind: "info", text: "Saved. The Final machine applies it within 30 seconds." });
      setEdit(null); reload();
    } catch (e) {
      setMsg({ kind: "error", text: e?.message || "Save failed" });
    } finally { setSaving(false); }
  };

  return (
    <>
      <Msg kind="info">
        When a locked part reaches Final, this bit is set on that line's Final machine for the hold
        time, so the machine rejects the part. Lines without a bit are not checked.
      </Msg>
      {msg && <Msg kind={msg.kind}>{msg.text}</Msg>}
      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 10 }}>
        <label style={{ fontSize: 13, color: "#334155", display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} />
          Show all lines (to add a new one)
        </label>
        {!canEdit && <span style={{ fontSize: 12, color: "#64748b" }}>Read-only for your login.</span>}
      </div>
      <div style={{ ...card, padding: 0, overflow: "auto", maxHeight: "62vh" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead><tr>
            <th style={th}>Zone</th><th style={th}>Line</th><th style={th}>Final Machine</th>
            <th style={th}>Red Bin Bit</th><th style={th}>Hold (s)</th><th style={th}>Status</th>
            <th style={th}>Semi-Auto NG Bit</th><th style={th}>Updated</th>
            {canEdit && <th style={th}></th>}
          </tr></thead>
          <tbody>
            {rows.map(r => {
              const isEd = edit?.line_id === r.line_id;
              return (
                <tr key={r.line_id} style={{ background: isEd ? "#eff6ff" : undefined }}>
                  <td style={td}>{r.zone_name || "—"}</td>
                  <td style={{ ...td, fontWeight: 700 }}>{r.line_name}</td>
                  <td style={td}>{r.final_machine}{r.final_ip ? <span style={{ color: "#94a3b8" }}> · {r.final_ip}</span> : null}</td>
                  <td style={td}>{isEd
                    ? <input style={{ ...inp, width: 110, height: 32, ...mono }} value={edit.bit_address}
                             onChange={e => setEdit({ ...edit, bit_address: e.target.value })} />
                    : (r.bit_address ? <span style={mono}>{r.bit_address}</span> : <span style={{ color: "#94a3b8" }}>Not set</span>)}
                  </td>
                  <td style={td}>{isEd
                    ? <input style={{ ...inp, width: 80, height: 32 }} type="number" min="0.5" max="30" step="0.5"
                             value={edit.hold_sec} onChange={e => setEdit({ ...edit, hold_sec: e.target.value })} />
                    : (r.hold_sec ?? "—")}
                  </td>
                  <td style={td}>{isEd
                    ? <select style={{ ...inp, width: 110, height: 32 }} value={edit.enabled ? "1" : "0"}
                              onChange={e => setEdit({ ...edit, enabled: e.target.value === "1" })}>
                        <option value="1">Active</option><option value="0">Off</option>
                      </select>
                    : (r.bit_address ? <Badge text={r.enabled ? "ACTIVE" : "OFF"} tone={r.enabled ? "green" : "grey"} /> : "—")}
                  </td>
                  <td style={{ ...td, ...mono, color: "#64748b", fontWeight: 400 }}>{r.sa_ng_bit || "—"}</td>
                  <td style={{ ...td, color: "#64748b" }}>{r.updated_at ? `${r.updated_at} · ${r.updated_by || ""}` : "—"}</td>
                  {canEdit && (
                    <td style={td}>
                      {isEd ? (
                        <span style={{ display: "flex", gap: 6 }}>
                          <button style={{ ...btn("#15803d"), height: 32, padding: "4px 12px" }}
                                  disabled={saving} onClick={save}>{saving ? "Saving…" : "Save"}</button>
                          <button style={{ ...btn("#e2e8f0", "#334155"), height: 32, padding: "4px 12px" }}
                                  onClick={() => setEdit(null)}>Cancel</button>
                        </span>
                      ) : (
                        <button style={{ ...btn("#1e3a8a"), height: 32, padding: "4px 12px" }}
                                onClick={() => { setMsg(null); setEdit({
                                  line_id: r.line_id,
                                  bit_address: r.bit_address || r.sa_ng_bit || "",
                                  hold_sec: r.hold_sec ?? 2, enabled: r.bit_address ? r.enabled : true }); }}>
                          {r.bit_address ? "Edit" : "Add"}
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
            {!rows.length && (
              <tr><td style={{ ...td, color: "#64748b", padding: 18 }} colSpan={canEdit ? 9 : 8}>No lines.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default function RedBinLock() {
  const { token } = useAuth();
  const [tab, setTab] = useState("locked");
  const [lockedN, setLockedN] = useState(null);
  const [blockN, setBlockN] = useState(null);
  const [bm, setBm] = useState(null);
  const [pending, setPending] = useState(false);   // backend not loaded yet

  const loadBm = async () => {
    try { setBm(await api.get("/api/quality/redbin/bit-master", token)); setPending(false); }
    catch (e) { if (/not found|404/i.test(e?.message || "")) setPending(true); }
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { loadBm(); }, []);
  const checkedLines = (bm?.rows || []).filter(r => r.bit_address);

  const tabBtn = (key, label, n) => (
    <button key={key} onClick={() => setTab(key)} style={{
      padding: "9px 16px", borderRadius: 10, fontWeight: 800, fontSize: 13, cursor: "pointer",
      border: tab === key ? "1px solid #1e3a8a" : "1px solid #e2e8f0",
      background: tab === key ? "#1e3a8a" : "#fff", color: tab === key ? "#fff" : "#334155",
    }}>
      {label}{n != null ? <span style={{ marginLeft: 6, opacity: .85 }}>({n})</span> : null}
    </button>
  );

  return (
    <div style={{ padding: "18px 22px", maxWidth: 1500, margin: "0 auto" }}>
      <h2 style={{ fontSize: 20, fontWeight: 900, color: "#0f172a", margin: "0 0 3px" }}>
        Red Bin Lock
      </h2>
      <div style={{ fontSize: 12, color: "#64748b", marginBottom: 14 }}>
        Parts locked in the Red Bin portal are rejected at Final Inspection
        {checkedLines.length ? ` · checked on ${checkedLines.filter(r => r.enabled).length} line(s)` : ""}
        {" · "}unlock is done in the Red Bin portal (Scan to Unlock)
      </div>
      {pending && (
        <Msg kind="error">
          This page is installed but its server side is not active yet. It starts working after
          the next MES server restart.
        </Msg>
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
        {tabBtn("locked", "Locked Parts", lockedN)}
        {tabBtn("blocks", "Restricted at Final", blockN)}
        {tabBtn("bits", "Bit Master")}
      </div>
      {tab === "locked" && <LockedTab token={token} onCount={setLockedN} />}
      {tab === "blocks" && <BlocksTab token={token} onCount={setBlockN}
                                      lines={checkedLines.length ? checkedLines : (bm?.rows || [])} />}
      {tab === "bits" && <BitMasterTab token={token} data={bm} reload={loadBm} />}
    </div>
  );
}
