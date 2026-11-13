// MachineMap.jsx — one row per machine: MES next to Maintenance.
//
// Operator (29-Oct): "meri ek machine ka naam and number and zone ka number and
// name kya h maintenance and mes me … ek new page bna jisme hr machine ki row
// and column table bna de … bs read from both db".
//
// Read-only.  GET /api/machine-map reads mes_plc_configs (+ lines, zones) and
// maintenance_db.maintenance_machines and pairs them by PLC IP.  Nothing on this
// page writes anywhere.
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import PageTopbar from "../components/PageTopbar";

const STATUS = {
  "Matched":       { c: "#15803d", cd: "#4ade80", bg: "#dcfce7", tip: "Same PLC IP, same name" },
  "Name differs":  { c: "#a16207", cd: "#fbbf24", bg: "#fef3c7", tip: "Same PLC IP, different name" },
  "Shared PLC":    { c: "#1d4ed8", cd: "#60a5fa", bg: "#dbeafe", tip: "One PLC IP serves several MES machines" },
  "Multiple rows": { c: "#7c3aed", cd: "#a78bfa", bg: "#ede9fe", tip: "The IP is on more than one maintenance row" },
  "MES only":      { c: "#b91c1c", cd: "#f87171", bg: "#fee2e2", tip: "No IP, or the IP is not in maintenance" },
  "Maint. only":   { c: "#475569", cd: "#94a3b8", bg: "#e2e8f0", tip: "No MES machine uses this IP" },
};
const STATUS_ORDER = Object.keys(STATUS);

const COLS = [
  // [key, label, group]
  ["mes_zone_id",    "Zone No.",     "mes"],
  ["mes_zone_code",  "Zone Code",    "mes"],
  ["mes_zone",       "Zone",         "mes"],
  ["mes_line_id",    "Line ID",      "mes"],
  ["mes_line",       "Line",         "mes"],
  ["mes_machine_id", "Machine ID",   "mes"],
  ["mes_seq",        "Seq",          "mes"],
  ["mes_machine",    "Machine Name", "mes"],
  ["mes_machine_no", "Machine No.",  "mes"],
  ["mes_ip",         "IP",           "mes"],
  ["mm_zone",        "Zone",         "mm"],
  ["mm_line",        "Line",         "mm"],
  ["mm_machine_no",  "Machine No.",  "mm"],
  ["mm_machine",     "Machine Name", "mm"],
  ["mm_ip",          "IP",           "mm"],
  ["mm_serial",      "Serial No.",   "mm"],
  ["mm_id",          "Row ID",       "mm"],
];

const txt = (v) => (v === null || v === undefined || v === "") ? "" : String(v).replace(/\s*\n\s*/g, " / ");

export default function MachineMap() {
  const { token, theme, isAdmin } = useAuth();
  const [acting, setActing] = useState("");
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const [zone, setZone] = useState("");
  const [status, setStatus] = useState("");

  const dark = theme === "dark";
  const bg   = dark ? "#0b1220" : "#f8fafc";
  const sub  = dark ? "#94a3b8" : "#64748b";
  const line = dark ? "#243049" : "#e2e8f0";
  const card = { background: dark ? "#131c30" : "#fff", border: `1px solid ${line}`,
                 borderRadius: 10, padding: 12, marginBottom: 12 };
  const inp  = { padding: "7px 9px", borderRadius: 7, fontSize: 13,
                 border: `1px solid ${dark ? "#243049" : "#cbd5e1"}`,
                 background: dark ? "#0f1729" : "#fff", color: dark ? "#e6edf7" : "#0f172a" };

  const load = useCallback(async () => {
    setBusy(true);
    try { setD(await api.get("/api/machine-map", token)); setErr(""); }
    catch (e) {
      setErr(/404|Not Found/.test(String(e?.message || e))
        ? "Available after the next MES-API restart." : (e?.message || "Could not load."));
    } finally { setBusy(false); }
  }, [token]);
  useEffect(() => { load(); }, [load]);

  // Admin buttons.  The server looks up the maintenance value itself (by IP);
  // only MES is written, and every change can be undone.
  const apply = async (r, field) => {
    const msg = field === "name"
      ? `Rename the MES machine\n\n  "${txt(r.mes_machine)}"\n→ "${txt(r.mm_machine)}"\n\n`
        + "Dashboards show the new name right away; the collector uses it after its next restart. "
        + "Rows already saved keep the old name. Undo is available."
      : `Set MES machine no. of "${txt(r.mes_machine)}" to ${r.mm_machine_no}?`;
    if (!window.confirm(msg)) return;
    setActing(`${r.mes_machine_id}:${field}`);
    try { await api.post("/api/machine-map/apply", { plc_config_id: r.mes_machine_id, field }, token); await load(); }
    catch (e) { window.alert(e?.message || "Could not save."); }
    finally { setActing(""); }
  };
  const undo = async (r, u) => {
    const what = u.field === "name" ? "name" : "machine no.";
    if (!window.confirm(`Undo: set the MES ${what} back to "${u.old_value ?? ""}"?`)) return;
    setActing(`undo:${u.id}`);
    try { await api.post("/api/machine-map/undo", { change_id: u.id }, token); await load(); }
    catch (e) { window.alert(e?.message || "Could not undo."); }
    finally { setActing(""); }
  };

  const rows = d?.rows || [];
  const zones = useMemo(() => {
    const mes = [...new Set(rows.map(r => r.mes_zone).filter(Boolean))];
    const mm  = [...new Set(rows.map(r => r.mm_zone).filter(Boolean))].sort();
    return { mes, mm };
  }, [rows]);

  const shown = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return rows.filter(r => {
      if (status && r.status !== status) return false;
      if (zone.startsWith("mes:") && r.mes_zone !== zone.slice(4)) return false;
      if (zone.startsWith("mm:")  && r.mm_zone  !== zone.slice(3)) return false;
      if (!ql) return true;
      return COLS.some(([k]) => txt(r[k]).toLowerCase().includes(ql));
    });
  }, [rows, q, zone, status]);

  const exportCsv = () => {
    const head = ["Status", ...COLS.map(([, lb, g]) => `${g === "mes" ? "MES" : "Maintenance"} ${lb}`)];
    const esc = (v) => { const s = txt(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const body = shown.map(r => [r.status, ...COLS.map(([k]) => r[k])].map(esc).join(","));
    const blob = new Blob(["﻿" + [head.join(","), ...body].join("\r\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `machine-map-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };

  const th = { textAlign: "left", padding: "7px 9px", fontSize: 10.5, fontWeight: 800,
               textTransform: "uppercase", letterSpacing: ".03em", color: sub,
               borderBottom: `1px solid ${line}`, whiteSpace: "nowrap",
               position: "sticky", top: 30, background: dark ? "#0f1729" : "#f1f5f9", zIndex: 1 };
  const grp = { ...th, top: 0, height: 30, boxSizing: "border-box", textAlign: "center", fontSize: 11.5,
                letterSpacing: ".08em", zIndex: 2 };
  const td = { padding: "6px 9px", fontSize: 12.5, borderBottom: `1px solid ${line}`, whiteSpace: "nowrap" };
  const split = { borderLeft: `2px solid ${dark ? "#334155" : "#cbd5e1"}` };
  const btn = (c) => ({ padding: "3px 9px", borderRadius: 6, fontSize: 11.5, fontWeight: 800, cursor: "pointer",
                         color: dark ? "#e6edf7" : c, background: "transparent", border: `1.5px solid ${c}`,
                         whiteSpace: "nowrap" });
  const mesCols = COLS.filter(c => c[2] === "mes").length;
  const mmCols  = COLS.length - mesCols;

  return (
    <div style={{ minHeight: "100vh", background: bg, padding: 16, color: dark ? "#e6edf7" : "#0f172a" }}>
      {/* 2026-11-05 — the shared two-tone page bar, as on the other pages (the
          plain heading sat under the logo button). */}
      <PageTopbar leading="Machine" accent="Map" />
      <div style={{ fontSize: 12.5, color: sub, margin: "12px 0" }}>
        Every machine in MES and in Maintenance, paired by PLC IP. Read-only.
        {d?.generated_at && <> · Loaded {d.generated_at.replace("T", " ")}</>}
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        <Chip on={!status} onClick={() => setStatus("")} c={dark ? "#e6edf7" : "#0f172a"}
              bg={dark ? "#1e293b" : "#fff"} line={line} label="All" n={rows.length} />
        {STATUS_ORDER.filter(s => d?.counts?.[s]).map(s => (
          <Chip key={s} on={status === s} onClick={() => setStatus(status === s ? "" : s)}
                c={dark ? STATUS[s].cd : STATUS[s].c} bg={dark ? "#1e293b" : STATUS[s].bg} line={line} label={s} n={d.counts[s]} tip={STATUS[s].tip} />
        ))}
        {d && <span style={{ marginLeft: "auto", alignSelf: "center", fontSize: 12.5, color: sub }}>
          MES {d.mes_total} machines · Maintenance {d.maint_total} machines
        </span>}
      </div>

      <div style={{ ...card, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <input value={q} onChange={e => setQ(e.target.value)}
               placeholder="Search name, machine no., line, IP"
               style={{ ...inp, minWidth: 240, flex: "1 1 240px", maxWidth: 380 }} />
        <select value={zone} onChange={e => setZone(e.target.value)} style={inp}>
          <option value="">All zones</option>
          <optgroup label="MES zones">
            {zones.mes.map(z => <option key={"mes" + z} value={"mes:" + z}>{z}</option>)}
          </optgroup>
          <optgroup label="Maintenance zones">
            {zones.mm.map(z => <option key={"mm" + z} value={"mm:" + z}>{z}</option>)}
          </optgroup>
        </select>
        <button onClick={load} disabled={busy} style={{ ...inp, cursor: "pointer", fontWeight: 700 }}>
          {busy ? "Loading…" : "Refresh"}
        </button>
        <button onClick={exportCsv} disabled={!shown.length} style={{ ...inp, cursor: "pointer", fontWeight: 700 }}>
          Export CSV
        </button>
        <span style={{ marginLeft: "auto", fontSize: 12.5, color: sub }}>{shown.length} rows</span>
      </div>

      {err && <div style={{ ...card, borderLeft: "4px solid #dc2626", color: "#dc2626" }}>{err}</div>}

      {d && (
        <div style={{ ...card, padding: 0, overflow: "auto", maxHeight: "calc(100vh - 250px)" }}>
          <table style={{ borderCollapse: "separate", borderSpacing: 0, width: "100%" }}>
            <thead>
              <tr>
                <th style={grp} colSpan={2}></th>
                <th style={{ ...grp, color: "#1d4ed8" }} colSpan={mesCols}>MES</th>
                <th style={{ ...grp, ...split, color: "#b91c1c" }} colSpan={mmCols}>MAINTENANCE</th>
                {isAdmin && <th style={{ ...grp, ...split }}></th>}
              </tr>
              <tr>
                <th style={th}>#</th>
                <th style={th}>Status</th>
                {COLS.map(([k, lb, g], i) => (
                  <th key={k} style={{ ...th, ...(g === "mm" && COLS[i - 1]?.[2] === "mes" ? split : {}) }}>{lb}</th>
                ))}
                {isAdmin && <th style={{ ...th, ...split }}>Make Same (MES)</th>}
              </tr>
            </thead>
            <tbody>
              {shown.map((r, i) => {
                const st = STATUS[r.status] || STATUS["Maint. only"];
                const sc = dark ? st.cd : st.c;
                return (
                  <tr key={`${r.mes_machine_id ?? "x"}-${r.mm_id ?? "x"}-${i}`}>
                    <td style={{ ...td, color: sub }}>{i + 1}</td>
                    <td style={td}>
                      <span title={st.tip} style={{ padding: "2px 8px", borderRadius: 6, fontSize: 11.5, fontWeight: 800,
                                                     color: sc, background: dark ? "transparent" : st.bg,
                                                     border: `1px solid ${sc}55` }}>{r.status}</span>
                    </td>
                    {COLS.map(([k, , g], ci) => {
                      let v = txt(r[k]);
                      const strong = k === "mes_machine" || k === "mm_machine_no" || k === "mm_machine";
                      const extra = (k === "mes_line" && r.mes_line_active === false) ? " (inactive)"
                                  : (k === "mm_machine_no" && r.mm_active === false) ? " (inactive)" : "";
                      return (
                        <td key={k} style={{ ...td, ...(g === "mm" && COLS[ci - 1]?.[2] === "mes" ? split : {}),
                                             fontWeight: strong ? 700 : 400,
                                             fontFamily: k.endsWith("_ip") || k === "mm_machine_no" ? "ui-monospace,monospace" : undefined,
                                             color: v ? undefined : sub }}>
                          {v || "—"}{extra && <span style={{ color: "#b91c1c", fontWeight: 600 }}>{extra}</span>}
                        </td>
                      );
                    })}
                    {isAdmin && (
                      <td style={{ ...td, ...split }}>
                        <div style={{ display: "flex", gap: 6 }}>
                          {r.can_copy_name && (
                            <button style={btn("#a16207")} disabled={!!acting} onClick={() => apply(r, "name")}
                                    title="Copy the maintenance name to MES">
                              {acting === `${r.mes_machine_id}:name` ? "…" : "Use name"}
                            </button>)}
                          {r.can_copy_no && (
                            <button style={btn("#1d4ed8")} disabled={!!acting} onClick={() => apply(r, "machine_no")}
                                    title="Copy the maintenance machine no. to MES">
                              {acting === `${r.mes_machine_id}:machine_no` ? "…" : "Use No."}
                            </button>)}
                          {(r.undo || []).map(u => (
                            <button key={u.id} style={btn("#475569")} disabled={!!acting} onClick={() => undo(r, u)}
                                    title={`Changed by ${u.changed_by || "?"} at ${(u.changed_at || "").replace("T", " ")}: "${u.old_value ?? ""}" → "${u.new_value ?? ""}"`}>
                              {acting === `undo:${u.id}` ? "…" : (u.field === "name" ? "Undo name" : "Undo No.")}
                            </button>))}
                          {!r.can_copy_name && !r.can_copy_no && !(r.undo || []).length && (
                            <span style={{ color: sub }}>—</span>)}
                        </div>
                      </td>
                    )}
                  </tr>
                );
              })}
              {!shown.length && (
                <tr><td colSpan={COLS.length + 2 + (isAdmin ? 1 : 0)} style={{ ...td, textAlign: "center", color: sub, padding: 24 }}>
                  No machines match the filter.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      {!d && !err && <div style={{ ...card, color: sub }}>Loading…</div>}
    </div>
  );
}

function Chip({ on, onClick, c, bg, line, label, n, tip }) {
  return (
    <button onClick={onClick} title={tip}
            style={{ padding: "6px 12px", borderRadius: 999, fontSize: 12.5, fontWeight: 800, cursor: "pointer",
                     color: c, background: on ? bg : "transparent",
                     border: `1.5px solid ${on ? c : line}` }}>
      {label} <span style={{ opacity: .75 }}>{n}</span>
    </button>
  );
}
