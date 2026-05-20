import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import AIAssistant from "../components/AIAssistant";

const ACTION_COLORS = {
  CREATED:      { bg: "rgba(22,163,74,.1)",    border: "rgba(22,163,74,.25)",   text: "#16a34a" },
  ADDED:        { bg: "rgba(22,163,74,.1)",    border: "rgba(22,163,74,.25)",   text: "#16a34a" },
  UPDATED:      { bg: "rgba(30,64,175,.1)",    border: "rgba(30,64,175,.2)",    text: "#1e40af" },
  SAVED:        { bg: "rgba(30,64,175,.1)",    border: "rgba(30,64,175,.2)",    text: "#1e40af" },
  ASSIGNED:     { bg: "rgba(139,92,246,.1)",   border: "rgba(139,92,246,.2)",   text: "#8b5cf6" },
  AUTH_LOGIN:   { bg: "rgba(14,165,233,.1)",   border: "rgba(14,165,233,.2)",   text: "#0ea5e9" },
  LOGIN:        { bg: "rgba(14,165,233,.1)",   border: "rgba(14,165,233,.2)",   text: "#0ea5e9" },
  VIEWED:       { bg: "rgba(100,116,139,.1)",  border: "rgba(100,116,139,.2)",  text: "#64748b" },
  SEARCHED:     { bg: "rgba(100,116,139,.1)",  border: "rgba(100,116,139,.2)",  text: "#64748b" },
  DEACTIVATED:  { bg: "rgba(217,119,6,.1)",    border: "rgba(217,119,6,.2)",    text: "#d97706" },
  DELETED:      { bg: "rgba(220,38,38,.1)",    border: "rgba(220,38,38,.2)",    text: "#dc2626" },
  REMOVED:      { bg: "rgba(220,38,38,.1)",    border: "rgba(220,38,38,.2)",    text: "#dc2626" },
  STOPPED:      { bg: "rgba(220,38,38,.1)",    border: "rgba(220,38,38,.2)",    text: "#dc2626" },
  DEFAULT:      { bg: "#f1f5f9",               border: "#e2e8f0",               text: "#64748b" },
};

function getActionStyle(action = "") {
  const key = Object.keys(ACTION_COLORS).find(k => action.includes(k));
  return ACTION_COLORS[key] || ACTION_COLORS.DEFAULT;
}

function ActionPill({ action }) {
  const s = getActionStyle(action);
  // Format: "PLANT_CREATED" → "Plant Created"
  const label = action.split("_").map(w => w[0] + w.slice(1).toLowerCase()).join(" ");
  return (
    <span style={{
      display: "inline-block",
      padding: "3px 10px", borderRadius: 99,
      background: s.bg, border: `1px solid ${s.border}`,
      fontSize: 11, fontWeight: 700, color: s.text,
      whiteSpace: "nowrap",
    }}>{label}</span>
  );
}

function EntityBadge({ type, id }) {
  if (!type) return <span style={{ color: "#94a3b8" }}>—</span>;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: "2px 8px", borderRadius: 6,
      background: "#f1f5f9", border: "1px solid #e2e8f0",
      fontSize: 11, color: "#334155", fontFamily: "monospace",
    }}>
      {type}{id ? ` #${id}` : ""}
    </span>
  );
}

function UserBadge({ username, role }) {
  if (!username) return <span style={{ color: "#94a3b8", fontSize: 11 }}>system</span>;
  const roleColor = role === "admin" || role === "plant_head"
    ? "#dc2626"
    : role === "production"
    ? "#16a34a"
    : role === "operator"
    ? "#0ea5e9"
    : "#64748b";
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 6,
      fontSize: 11, fontWeight: 600, color: "#334155",
    }}>
      <span style={{
        width: 22, height: 22, borderRadius: "50%",
        background: roleColor, color: "#fff",
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        fontSize: 10, fontWeight: 800, textTransform: "uppercase",
      }}>{username[0]}</span>
      {username}
    </span>
  );
}

const inputStyle = {
  background: "#f8fafc", border: "1.5px solid #e2e8f0", borderRadius: 8,
  padding: "9px 12px", color: "#0f172a",
  fontFamily: "'Barlow',sans-serif", fontSize: 13, outline: "none",
  transition: "border-color .15s",
};

function fmtRelative(ts) {
  if (!ts) return "Never";
  const d = new Date(ts);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60)        return `${Math.floor(diff)}s ago`;
  if (diff < 3600)      return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400)     return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

function fmtFullTs(ts) {
  if (!ts) return "—";
  return new Date(ts).toLocaleString("en-IN", {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    hour12: false,
  });
}

export default function Audit() {
  const { token, theme } = useAuth();

  const today = new Date().toISOString().split("T")[0];
  // Default range: last 30 days so existing/historical rows show up.
  // User can switch to "Single Day" if they want today-only view.
  const _d30 = new Date(); _d30.setDate(_d30.getDate() - 30);
  const thirtyAgo = _d30.toISOString().split("T")[0];

  const [logs,       setLogs]      = useState([]);
  const [total,      setTotal]     = useState(0);
  const [hasMore,    setHasMore]   = useState(false);
  const [loading,    setLoading]   = useState(true);
  const [loadingMore,setLoadingMore] = useState(false);
  const [actions,    setActions]   = useState([]);  // distinct action types
  const [users,      setUsers]     = useState([]);  // top-card users
  const [usersLoading, setUsersLoading] = useState(true);

  const [dateFrom,   setDateFrom]  = useState(thirtyAgo);
  const [dateTo,     setDateTo]    = useState(today);
  const [selAction,  setSelAction] = useState("");
  const [selUser,    setSelUser]   = useState("");   // click a user card → filter
  const [useSingle,  setUseSingle] = useState(false); // default: range (last 30 days)
  useEffect(() => { document.title = "Audit Log"; }, []);

  const LIMIT = 50;

  // Load distinct actions + users for top card
  useEffect(() => {
    api.get("/api/audit/actions", token)
      .then(d => setActions(Array.isArray(d) ? d : []))
      .catch(() => {});
    setUsersLoading(true);
    api.get("/api/audit/users", token)
      .then(d => setUsers(Array.isArray(d) ? d : []))
      .catch(() => setUsers([]))
      .finally(() => setUsersLoading(false));
  }, [token]);

  const buildParams = useCallback((offset = 0) => {
    const params = new URLSearchParams();
    params.set("limit", LIMIT);
    params.set("offset", offset);
    if (useSingle) {
      params.set("date_from", dateFrom);
      params.set("date_to", dateFrom);
    } else {
      if (dateFrom) params.set("date_from", dateFrom);
      if (dateTo)   params.set("date_to",   dateTo);
    }
    if (selAction) params.set("action",   selAction);
    if (selUser)   params.set("username", selUser);
    return params.toString();
  }, [dateFrom, dateTo, selAction, selUser, useSingle]);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.get(`/api/audit?${buildParams(0)}`, token);
      setLogs(data.logs || []);
      setTotal(data.total || 0);
      setHasMore(data.has_more || false);
    } catch { }
    finally { setLoading(false); }
  }, [buildParams, token]);

  useEffect(() => { fetchLogs(); }, [fetchLogs]);

  const loadMore = async () => {
    setLoadingMore(true);
    try {
      const data = await api.get(`/api/audit?${buildParams(logs.length)}`, token);
      setLogs(prev => [...prev, ...(data.logs || [])]);
      setHasMore(data.has_more || false);
    } catch { }
    finally { setLoadingMore(false); }
  };

  const clearFilters = () => {
    setDateFrom(thirtyAgo); setDateTo(today);
    setSelAction(""); setSelUser(""); setUseSingle(false);
  };

  // Group logs by date for visual separation
  const grouped = logs.reduce((acc, log) => {
    const date = new Date(log.created_at).toLocaleDateString("en-IN", {
      day: "2-digit", month: "long", year: "numeric",
    });
    if (!acc[date]) acc[date] = [];
    acc[date].push(log);
    return acc;
  }, {});

  const roleColor = (role) => (
    role === "admin" || role === "plant_head" ? "#dc2626"
    : role === "production"                  ? "#16a34a"
    : role === "operator"                    ? "#0ea5e9"
    : role === "department"                  ? "#8b5cf6"
    : "#64748b"
  );

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@700;800&display=swap');
        .audit-root { min-height:100vh; background:#f8fafc; font-family:'Barlow',sans-serif; padding-bottom:60px; }
        .audit-topbar {
          background:#fff; border-bottom:1px solid #e2e8f0;
          padding:0 40px 0 88px; height:60px;
          display:flex; align-items:center;
          position:sticky; top:0; z-index:100;
          box-shadow:0 1px 3px rgba(0,0,0,.06);
        }
        .audit-topbar::after { content:''; position:absolute; bottom:0; left:0; right:0; height:2px; background:${theme.gradient}; }
        .audit-logo { font-family:'Barlow Condensed',sans-serif; font-size:22px; font-weight:800; color:#0f172a; }
        .audit-logo span { color:${theme.accent}; }
        .audit-body { padding:36px 40px 0; max-width:1100px; margin:0 auto; }
        .audit-heading { text-align:center; margin-bottom:24px; }
        .audit-heading h1 { font-family:'Barlow Condensed',sans-serif; font-size:42px; font-weight:800; color:#0f172a; letter-spacing:-.01em; }
        .audit-heading h1 span { color:${theme.accent}; }

        /* Users top card */
        .users-card { background:#fff; border:1px solid #e2e8f0; border-radius:14px; padding:18px 20px 14px; margin-bottom:18px; box-shadow:0 1px 3px rgba(0,0,0,.05); }
        .users-card-title { display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; }
        .users-card-title h3 { font-family:'Barlow Condensed',sans-serif; font-size:18px; font-weight:800; color:#0f172a; letter-spacing:.02em; text-transform:uppercase; }
        .users-card-title small { font-size:11px; color:#94a3b8; }
        .users-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:10px; }
        .user-tile { padding:11px 14px; background:#f8fafc; border:1.5px solid #e2e8f0; border-radius:10px; cursor:pointer; transition:all .12s; position:relative; overflow:hidden; }
        .user-tile:hover { border-color:${theme.accent}; background:#fff; transform:translateY(-1px); box-shadow:0 4px 10px rgba(15,23,42,.06); }
        .user-tile.selected { border-color:${theme.accent}; background:${theme.soft}; box-shadow:0 0 0 3px ${theme.soft}; }
        .user-row1 { display:flex; align-items:center; gap:8px; }
        .user-avatar { width:30px; height:30px; border-radius:50%; color:#fff; display:flex; align-items:center; justify-content:center; font-size:13px; font-weight:800; text-transform:uppercase; flex-shrink:0; }
        .user-name { font-size:13px; font-weight:700; color:#0f172a; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .role-chip { font-size:9px; font-weight:800; padding:2px 6px; border-radius:4px; text-transform:uppercase; letter-spacing:.04em; }
        .user-meta { font-size:10px; color:#64748b; margin-top:6px; display:flex; justify-content:space-between; }
        .user-meta b { color:#0f172a; font-weight:700; }
        .active-dot { width:6px; height:6px; border-radius:50%; display:inline-block; margin-right:4px; }

        .filter-card { background:#fff; border:1px solid #e2e8f0; border-radius:14px; padding:18px 22px; margin-bottom:18px; box-shadow:0 1px 3px rgba(0,0,0,.05); }
        .filter-row { display:flex; align-items:flex-end; gap:14px; flex-wrap:wrap; }
        .ff { display:flex; flex-direction:column; gap:6px; }
        .ff label { font-size:10px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:#64748b; }
        .toggle-wrap { display:flex; background:#f1f5f9; border-radius:8px; padding:3px; gap:2px; }
        .toggle-btn { padding:6px 14px; border-radius:6px; border:none; cursor:pointer; font-family:'Barlow',sans-serif; font-size:12px; font-weight:600; transition:all .12s; }
        .toggle-btn.active { background:#fff; color:${theme.accentDark}; box-shadow:0 1px 4px rgba(0,0,0,.1); }
        .toggle-btn:not(.active) { background:none; color:#64748b; }
        .clear-btn { padding:9px 16px; background:#f8fafc; border:1.5px solid #e2e8f0; border-radius:8px; color:#64748b; font-family:'Barlow',sans-serif; font-size:13px; font-weight:600; cursor:pointer; transition:all .12s; white-space:nowrap; }
        .clear-btn:hover { border-color:${theme.accent}; color:${theme.accentDark}; }
        .filter-chip { display:inline-flex; align-items:center; gap:6px; padding:5px 11px; background:${theme.soft}; border:1px solid ${theme.accent}; border-radius:99px; font-size:11px; font-weight:700; color:${theme.accentDark}; }
        .filter-chip button { background:none; border:none; cursor:pointer; color:${theme.accentDark}; font-size:14px; line-height:1; padding:0; }

        .date-group-label { font-size:11px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:#94a3b8; padding:16px 0 10px; display:flex; align-items:center; gap:10px; }
        .date-group-label::after { content:''; flex:1; height:1px; background:#e2e8f0; }
        .log-row { display:flex; align-items:flex-start; gap:14px; padding:14px 18px; background:#fff; border:1px solid #e2e8f0; border-radius:10px; margin-bottom:8px; transition:border-color .12s; }
        .log-row:hover { border-color:#3b82f6; }
        .log-timeline { display:flex; flex-direction:column; align-items:center; gap:4px; padding-top:3px; flex-shrink:0; }
        .log-dot { width:10px; height:10px; border-radius:50%; flex-shrink:0; }
        .load-more-btn { width:100%; padding:12px; background:#fff; border:1.5px solid #e2e8f0; border-radius:10px; color:#334155; font-family:'Barlow',sans-serif; font-size:13px; font-weight:600; cursor:pointer; transition:all .12s; margin-top:8px; }
        .load-more-btn:hover { border-color:${theme.accent}; color:${theme.accentDark}; background:${theme.soft}; }
        .load-more-btn:disabled { opacity:.6; cursor:not-allowed; }
        @keyframes spin { to { transform:rotate(360deg) } }
        .spinner { width:20px; height:20px; border-radius:50%; border:2px solid #e2e8f0; border-top-color:${theme.accentDark}; animation:spin .6s linear infinite; margin:40px auto; display:block; }
        select:focus, input:focus { border-color:#3b82f6 !important; box-shadow:0 0 0 3px rgba(59,130,246,.1); }
      `}</style>

      <div className="audit-root">
        <div className="audit-topbar">
          <div className="audit-logo" />
          <div style={{
            position:"absolute", left:"50%", transform:"translateX(-50%)",
            fontFamily:"'Barlow Condensed',sans-serif",
            fontSize:37, fontWeight:800, color:"#0f172a", letterSpacing:"-.01em",
            pointerEvents:"none",
            }}>
            Audit <span style={{ color: theme.accent }}>Log</span>
          </div>
        </div>

        <div className="audit-body">

          {/* Users · Last Login top card */}
          <div className="users-card">
            <div className="users-card-title">
              <h3>Users · Last Login</h3>
              <small>
                {usersLoading
                  ? "loading…"
                  : `${users.length} user${users.length !== 1 ? "s" : ""} · click any tile to filter log`}
              </small>
            </div>
            {usersLoading ? (
              <div className="spinner" style={{ margin: "20px auto" }} />
            ) : users.length === 0 ? (
              <div style={{ fontSize: 12, color: "#94a3b8", padding: "10px 0" }}>No users yet.</div>
            ) : (
              <div className="users-grid">
                {users.map(u => {
                  const rc      = roleColor(u.role);
                  const online  = u.last_login && (Date.now() - new Date(u.last_login).getTime() < 12 * 3600 * 1000);
                  const isSel   = selUser === u.username;
                  return (
                    <div
                      key={u.id}
                      className={`user-tile${isSel ? " selected" : ""}`}
                      onClick={() => setSelUser(isSel ? "" : u.username)}
                      title={`Last login: ${fmtFullTs(u.last_login)}\nLast action: ${fmtFullTs(u.last_action_at)}`}
                    >
                      <div className="user-row1">
                        <div className="user-avatar" style={{ background: rc }}>
                          {u.username?.[0] || "?"}
                        </div>
                        <div className="user-name">{u.username}</div>
                        <span className="role-chip" style={{
                          background: rc + "22", color: rc, border: `1px solid ${rc}44`,
                        }}>{u.role}</span>
                      </div>
                      <div className="user-meta">
                        <span>
                          <span className="active-dot" style={{ background: online ? "#16a34a" : "#cbd5e1" }} />
                          {fmtRelative(u.last_login)}
                        </span>
                        <span><b>{u.actions_24h || 0}</b> in 24h</span>
                      </div>
                      {u.department_name && (
                        <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 3 }}>
                          {u.department_name}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Filters */}
          <div className="filter-card">
            <div className="filter-row">
              {/* Single / Range toggle */}
              <div className="ff">
                <label>Date Mode</label>
                <div className="toggle-wrap">
                  <button className={`toggle-btn${useSingle ? " active" : ""}`} onClick={() => setUseSingle(true)}>Single Day</button>
                  <button className={`toggle-btn${!useSingle ? " active" : ""}`} onClick={() => setUseSingle(false)}>Date Range</button>
                </div>
              </div>

              {/* Date inputs */}
              {useSingle ? (
                <div className="ff">
                  <label>Date</label>
                  <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={inputStyle} />
                </div>
              ) : (
                <>
                  <div className="ff">
                    <label>From</label>
                    <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={inputStyle} />
                  </div>
                  <div className="ff">
                    <label>To</label>
                    <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} style={inputStyle} />
                  </div>
                </>
              )}

              {/* Action type filter */}
              <div className="ff" style={{ minWidth: 200 }}>
                <label>Action Type</label>
                <select
                  value={selAction}
                  onChange={e => setSelAction(e.target.value)}
                  style={inputStyle}
                >
                  <option value="">All Actions</option>
                  {actions.map(a => (
                    <option key={a} value={a}>
                      {a.split("_").map(w => w[0] + w.slice(1).toLowerCase()).join(" ")}
                    </option>
                  ))}
                </select>
              </div>

              {/* User filter (text dropdown) */}
              <div className="ff" style={{ minWidth: 160 }}>
                <label>User</label>
                <select
                  value={selUser}
                  onChange={e => setSelUser(e.target.value)}
                  style={inputStyle}
                >
                  <option value="">All Users</option>
                  {users.map(u => (
                    <option key={u.id} value={u.username}>{u.username}</option>
                  ))}
                </select>
              </div>

              <button className="clear-btn" onClick={clearFilters}>✕ Clear</button>
            </div>

            {/* Active-filter chips */}
            {(selUser || selAction) && (
              <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
                {selUser && (
                  <span className="filter-chip">
                    User: {selUser}
                    <button onClick={() => setSelUser("")}>✕</button>
                  </span>
                )}
                {selAction && (
                  <span className="filter-chip">
                    Action: {selAction.split("_").map(w => w[0] + w.slice(1).toLowerCase()).join(" ")}
                    <button onClick={() => setSelAction("")}>✕</button>
                  </span>
                )}
              </div>
            )}
          </div>

          {/* Results */}
          {loading ? (
            <div className="spinner" />
          ) : logs.length === 0 ? (
            <div style={{ textAlign: "center", padding: "60px 40px", color: "#94a3b8" }}>
              <div style={{ fontSize: 44, opacity: .2, marginBottom: 14 }}>≡</div>
              <div style={{ fontSize: 15, fontWeight: 600, color: "#64748b" }}>No records found</div>
              <div style={{ fontSize: 13, marginTop: 6 }}>Try changing the date, user, or action filter</div>
            </div>
          ) : (
            <>
              {Object.entries(grouped).map(([date, dayLogs]) => (
                <div key={date}>
                  <div className="date-group-label">{date} · {dayLogs.length} action{dayLogs.length !== 1 ? "s" : ""}</div>
                  {dayLogs.map((log, i) => {
                    const s = getActionStyle(log.action || "");
                    return (
                      <div className="log-row" key={log.id || i}>
                        {/* Timeline dot */}
                        <div className="log-timeline">
                          <div className="log-dot" style={{ background: s.text }} />
                        </div>

                        {/* Timestamp */}
                        <div style={{ flexShrink: 0, width: 90 }}>
                          <div style={{ fontFamily: "monospace", fontSize: 12, color: "#0f172a", fontWeight: 600 }}>
                            {new Date(log.created_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}
                          </div>
                          <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 2 }}>
                            {new Date(log.created_at).toLocaleDateString("en-IN", { day: "2-digit", month: "short" })}
                          </div>
                        </div>

                        {/* User */}
                        <div style={{ flexShrink: 0, width: 130 }}>
                          <UserBadge username={log.username} role={log.user_role} />
                        </div>

                        {/* Action pill */}
                        <div style={{ flexShrink: 0, width: 170 }}>
                          <ActionPill action={log.action || ""} />
                        </div>

                        {/* Entity */}
                        <div style={{ flexShrink: 0, width: 120 }}>
                          <EntityBadge type={log.entity_type} id={log.entity_id} />
                        </div>

                        {/* Details */}
                        <div style={{ flex: 1, fontSize: 12, color: "#64748b", wordBreak: "break-word" }}>
                          {log.details || "—"}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ))}

              {/* Load more */}
              {hasMore && (
                <button className="load-more-btn" onClick={loadMore} disabled={loadingMore}>
                  {loadingMore
                    ? <span style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}>
                        <div style={{ width: 14, height: 14, borderRadius: "50%", border: "2px solid #e2e8f0", borderTopColor: theme.accentDark, animation: "spin .6s linear infinite" }} />
                        Loading…
                      </span>
                    : `Load more — ${total - logs.length} remaining`
                  }
                </button>
              )}

              {/* End of records */}
              {!hasMore && logs.length > 0 && (
                <div style={{ textAlign: "center", padding: "20px 0", fontSize: 12, color: "#94a3b8" }}>
                  All {total} records loaded
                </div>
              )}
            </>
          )}
        </div>
      </div>
      <AIAssistant pageContext={{ page: "Audit Log" }} />
    </>
  );
}
