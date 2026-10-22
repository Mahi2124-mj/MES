// components/AgentsPanel.jsx — Admin → Agents (2026-10-09)
//   Every MES agent on this server: on/off (needs the agent-control password,
//   which the admin sets here), and a live report — what it is doing, how
//   busy it is (CPU / memory of its processes), last activity and next run.
//   Backend: /api/agents (routers/agents.py).  Admin only.
import { useEffect, useState, useCallback } from "react";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";

const BUSY = { "Idle": ["#dcfce7", "#15803d"], "Light": ["#dbeafe", "#1d4ed8"], "Busy": ["#fef3c7", "#b45309"],
               "Very busy": ["#fee2e2", "#b91c1c"], "Waiting for next run": ["#f1f5f9", "#475569"], "Off": ["#f1f5f9", "#94a3b8"] };
const KIND = { unit: "Service", cron: "Scheduled job", view: "Inside Bin Filling" };
const when = (iso) => {
  if (!iso) return "—";
  const d = new Date(String(iso).replace(" IST", ""));
  if (isNaN(d)) return String(iso);
  return d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
};

function Chip({ bg, fg, children }) {
  return <span style={{ background: bg, color: fg, borderRadius: 999, padding: "3px 10px", fontSize: 11.5, fontWeight: 800, whiteSpace: "nowrap" }}>{children}</span>;
}

function PasswordBox({ isSet, onDone, say }) {
  const [cur, setCur] = useState(""), [nw, setNw] = useState(""), [nw2, setNw2] = useState("");
  const [busy, setBusy] = useState(false);
  const { token } = useAuth();
  const save = async () => {
    if (nw.length < 4) return say("Use at least 4 characters", "err");
    if (nw !== nw2) return say("The two new passwords do not match", "err");
    setBusy(true);
    try {
      await api.post("/api/agents/password", { current: cur, new: nw }, token);
      say(isSet ? "Agent password changed" : "Agent password set");
      setCur(""); setNw(""); setNw2(""); onDone();
    } catch (e) { say(e.message || "Could not save the password", "err"); }
    finally { setBusy(false); }
  };
  const inp = { padding: "8px 10px", borderRadius: 8, border: "1px solid #cbd5e1", fontSize: 13, minWidth: 0 };
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
      {isSet && <input type="password" placeholder="Current password" value={cur} onChange={e => setCur(e.target.value)} style={inp} autoComplete="current-password" />}
      <input type="password" placeholder="New password" value={nw} onChange={e => setNw(e.target.value)} style={inp} autoComplete="new-password" />
      <input type="password" placeholder="Repeat new password" value={nw2} onChange={e => setNw2(e.target.value)} style={inp} autoComplete="new-password" />
      <button type="button" disabled={busy} onClick={save}
              style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#1e40af", color: "#fff", fontWeight: 700, cursor: "pointer" }}>
        {isSet ? "Change password" : "Set password"}
      </button>
    </div>
  );
}

function AgentCard({ a, onChanged, say }) {
  const { token } = useAuth();
  const [ask, setAsk] = useState(false);
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [b, f] = BUSY[a.busy] || ["#f1f5f9", "#475569"];
  const want = !a.on;
  const go = async () => {
    setBusy(true);
    try {
      await api.post(`/api/agents/${a.key}/toggle`, { on: want, password: pw }, token);
      say(`${a.name} turned ${want ? "on" : "off"}`);
      setAsk(false); setPw(""); onChanged();
    } catch (e) { say(e.message || "Could not switch the agent", "err"); }
    finally { setBusy(false); }
  };
  return (
    <div style={{ background: "#fff", border: `1px solid ${a.on ? "#bbf7d0" : "#e2e8f0"}`, borderRadius: 12, padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ width: 10, height: 10, borderRadius: "50%", background: a.on ? "#22c55e" : "#cbd5e1", display: "inline-block" }} />
        <span style={{ fontWeight: 800, fontSize: 14.5, color: "#0f172a" }}>{a.name}</span>
        <span style={{ fontSize: 11.5, color: "#94a3b8" }}>{KIND[a.kind] || a.kind}</span>
        <span style={{ marginLeft: "auto" }}><Chip bg={b} fg={f}>{a.busy}</Chip></span>
      </div>
      <div style={{ fontSize: 12.5, color: "#475569" }}>{a.what}</div>
      {a.doing && <div style={{ fontSize: 12.5, color: "#0f172a", background: "#f8fafc", borderRadius: 8, padding: "7px 9px" }}>{a.doing}</div>}
      <div style={{ display: "flex", gap: "4px 16px", flexWrap: "wrap", fontSize: 12, color: "#475569" }}>
        <span>State: <b>{a.state}</b></span>
        {a.cpu_pct != null && <span>CPU: <b>{a.cpu_pct}%</b></span>}
        {a.mem_mb != null && <span>Memory: <b>{a.mem_mb} MB</b></span>}
        {a.processes > 0 && <span>Processes: <b>{a.processes}</b></span>}
        <span>Last activity: <b>{when(a.last_activity)}</b></span>
        {a.next_run && <span>Next run: <b>{when(a.next_run)}</b></span>}
        {a.schedule?.length > 0 && <span>Schedule: <code>{a.schedule.join(" · ")}</code></span>}
      </div>
      {a.rule && <div style={{ fontSize: 12, color: "#92400e", background: "#fef3c7", borderRadius: 8, padding: "6px 9px" }}>{a.rule}</div>}
      {a.note && <div style={{ fontSize: 12, color: "#475569", background: "#f1f5f9", borderRadius: 8, padding: "6px 9px" }}>{a.note}</div>}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        {a.switchable && !ask && (
          <button type="button" onClick={() => setAsk(true)}
                  style={{ padding: "7px 14px", borderRadius: 8, fontWeight: 800, cursor: "pointer",
                           border: `1px solid ${a.on ? "#fecaca" : "#bbf7d0"}`, background: a.on ? "#fff1f2" : "#f0fdf4", color: a.on ? "#b91c1c" : "#15803d" }}>
            {a.on ? "Turn off" : "Turn on"}
          </button>
        )}
        {ask && (
          <>
            <input type="password" autoFocus placeholder="Agent password" value={pw} onChange={e => setPw(e.target.value)}
                   onKeyDown={e => e.key === "Enter" && go()}
                   style={{ padding: "7px 10px", borderRadius: 8, border: "1px solid #cbd5e1", fontSize: 13 }} />
            <button type="button" disabled={busy || !pw} onClick={go}
                    style={{ padding: "7px 14px", borderRadius: 8, border: "none", fontWeight: 800, cursor: "pointer", color: "#fff", background: want ? "#15803d" : "#b91c1c" }}>
              {busy ? "Working…" : want ? `Confirm: turn on` : `Confirm: turn off`}
            </button>
            <button type="button" onClick={() => { setAsk(false); setPw(""); }}
                    style={{ padding: "7px 12px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer" }}>Cancel</button>
          </>
        )}
        {a.last_log?.length > 0 && (
          <button type="button" onClick={() => setLogOpen(!logOpen)}
                  style={{ marginLeft: "auto", border: "none", background: "none", color: "#1d4ed8", fontWeight: 700, fontSize: 12, cursor: "pointer" }}>
            {logOpen ? "Hide log" : "Last log lines"}
          </button>
        )}
      </div>
      {logOpen && (
        <pre style={{ margin: 0, fontSize: 11, background: "#0f172a", color: "#e2e8f0", borderRadius: 8, padding: 10, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {a.last_log.join("\n")}
        </pre>
      )}
    </div>
  );
}

export default function AgentsPanel({ toast }) {
  const { token } = useAuth();
  const say = useCallback((m, t = "ok") => (toast ? toast(m, t) : window.alert(m)), [toast]);
  const [data, setData] = useState(null);
  const [log, setLog] = useState([]);
  const [err, setErr] = useState("");
  const [pwOpen, setPwOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const [d, l] = await Promise.all([api.get("/api/agents", token), api.get("/api/agents/log?limit=30", token)]);
      setData(d); setLog(l.rows || []); setErr("");
    } catch (e) { setErr(e.message || "Could not load the agents"); }
  }, [token]);
  useEffect(() => {
    load();
    const t = setInterval(() => { if (!document.hidden) load(); }, 10000);
    return () => clearInterval(t);
  }, [load]);

  const on = data?.agents?.filter(a => a.on).length || 0;
  return (
    <div style={{ color: "#0f172a" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 800 }}>Agents</div>
          <div style={{ fontSize: 12.5, color: "#64748b" }}>
            {data ? `${on} of ${data.agents.length} on · updated ${when(data.generated_at)} · refreshes every 10 s` : "Loading…"}
          </div>
        </div>
        {data?.password_set && (
          <button type="button" onClick={() => setPwOpen(!pwOpen)}
                  style={{ marginLeft: "auto", padding: "7px 12px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff", fontWeight: 700, cursor: "pointer" }}>
            {pwOpen ? "Close" : "Change agent password"}
          </button>
        )}
      </div>
      {err && <div style={{ background: "#fee2e2", color: "#b91c1c", borderRadius: 10, padding: "8px 12px", marginBottom: 12, fontSize: 13 }}>{err}</div>}
      {data && !data.password_set && (
        <div style={{ background: "#eff6ff", border: "1px solid #bfdbfe", borderRadius: 12, padding: 14, marginBottom: 14 }}>
          <div style={{ fontWeight: 800, marginBottom: 6 }}>Set the agent password first</div>
          <div style={{ fontSize: 12.5, color: "#475569", marginBottom: 10 }}>Every on/off asks for it. Only you know it; it is stored as a hash, not as text.</div>
          <PasswordBox isSet={false} onDone={load} say={say} />
        </div>
      )}
      {data?.password_set && pwOpen && (
        <div style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12, padding: 14, marginBottom: 14 }}>
          <PasswordBox isSet onDone={() => { setPwOpen(false); load(); }} say={say} />
          {data.password_changed && <div style={{ fontSize: 11.5, color: "#94a3b8", marginTop: 6 }}>Last changed by {data.password_changed.by} on {when(data.password_changed.at)}</div>}
        </div>
      )}
      {data && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(380px, 1fr))", gap: 12 }}>
          {data.agents.map(a => <AgentCard key={a.key} a={a} onChanged={load} say={say} />)}
        </div>
      )}
      {log.length > 0 && (
        <div style={{ marginTop: 16, background: "#fff", border: "1px solid #e2e8f0", borderRadius: 12, padding: 12 }}>
          <div style={{ fontWeight: 800, fontSize: 13, marginBottom: 6 }}>Recent changes</div>
          {log.map((r, i) => (
            <div key={i} style={{ display: "flex", gap: 10, fontSize: 12.5, padding: "3px 0", flexWrap: "wrap" }}>
              <span style={{ color: "#64748b", minWidth: 120 }}>{when(r.at)}</span>
              <b>{r.username}</b>
              <span>{r.agent_key === "-" ? "password" : r.agent_key} → {r.action}</span>
              <span style={{ color: r.ok ? "#15803d" : "#b91c1c", fontWeight: 700 }}>{r.ok ? "done" : `refused${r.detail ? ` (${r.detail})` : ""}`}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
