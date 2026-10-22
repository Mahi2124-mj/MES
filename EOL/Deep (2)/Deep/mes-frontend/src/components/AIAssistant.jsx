import { useState, useRef, useEffect, useMemo } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import axios from "axios";
import { useAuth } from "../context/AuthContext";
import { NAV_ITEMS } from "./SlideNav";

// 2026-10-11 — page/module name suggestions in the assistant input.  The slide
// nav's NAV_ITEMS carry the exact paths; the pages the nav injects per-role
// (maintenance / weld / quality) aren't in that static list, so they're added
// here (their route is always "/" + key).  Each entry is filtered through
// canAccess(key) at render, so a user only ever sees pages they can open.
const EXTRA_PAGES = [
  { key: "weld-monitor",          label: "Weld Monitor" },
  { key: "maintenance-dashboard", label: "Maintenance Dashboard" },
  { key: "maintenance-historical",label: "Maintenance Historical" },
  { key: "maintenance-capa",      label: "Maintenance CAPA" },
  { key: "maintenance-deviations",label: "Maintenance Deviations" },
  { key: "maintenance-poka-yoke", label: "Maintenance Poka Yoke" },
  { key: "maintenance-logbook",   label: "Maintenance Log Book" },
  { key: "maintenance-pm",        label: "Preventive Maintenance" },
  { key: "maintenance-air-flow",  label: "Air Flow" },
  { key: "quality-dashboard",     label: "Quality Dashboard" },
  { key: "quality-deviations",    label: "Quality Deviation" },
  { key: "video-coverage",        label: "Video Coverage" },
  { key: "py-bypass",             label: "PY Bypass" },
  { key: "sa-fi-history",         label: "SA / FI History" },
  { key: "redbin-lock",           label: "Red Bin Lock" },
  { key: "logs",                  label: "Log Viewer" },
].map(p => ({ ...p, path: "/" + p.key }));

const ALL_PAGES = (() => {
  const out = [], seen = new Set();
  const add = (it) => {
    if (!it || !it.path || !it.key || seen.has(it.path)) return;
    seen.add(it.path);
    out.push({ key: it.key, label: it.label, path: it.path, icon: it.iconImg ? null : it.icon });
  };
  for (const sec of (NAV_ITEMS || [])) (sec.items || []).forEach(add);
  EXTRA_PAGES.forEach(add);
  return out;
})();

// ───────────────────────────────────────────────────────────────────────
// AIAssistant — floating production-data chatbot.
// 2026-09-14 — Simplified: removed all decorative animations (particle canvas,
// rocket loader, typing effect, floating/scanline/glow effects) per operator
// request, and made the panel RESPONSIVE so it never overflows the screen on
// the phone app (was a fixed 400px wide panel → horizontal overflow < ~424px).
// Backend is the offline DB chatbot at /api/ai/chat (no external LLM).
// ───────────────────────────────────────────────────────────────────────

// Draggable floating button: size, where its position is remembered, and a
// clamp that keeps it fully on screen (also after a resize / rotation).
const BTN_SIZE = 54;
const BTN_POS_KEY = "mes_ai_btn_pos";
const clampBtn = (p) => ({
  x: Math.min(Math.max(4, p.x), Math.max(4, window.innerWidth - BTN_SIZE - 4)),
  y: Math.min(Math.max(4, p.y), Math.max(4, window.innerHeight - BTN_SIZE - 4)),
});
// The chat panel opens on the button's side of the screen (left/right,
// top/bottom), so moving the button also moves where the chat covers the
// page.  No saved position = the original bottom-right corner.
const panelAnchor = (p) => {
  if (!p) return { bottom: 16, right: 16 };
  const w = window.innerWidth, h = window.innerHeight;
  const pw = Math.min(400, w - 32), ph = Math.min(560, h - 88);
  const out = {};
  if (p.x + BTN_SIZE / 2 < w / 2) out.left  = Math.max(16, Math.min(p.x, w - 16 - pw));
  else                            out.right = Math.max(16, Math.min(w - p.x - BTN_SIZE, w - 16 - pw));
  if (p.y + BTN_SIZE / 2 < h / 2) out.top    = Math.max(16, Math.min(p.y, h - 16 - ph));
  else                            out.bottom = Math.max(16, Math.min(h - p.y - BTN_SIZE, h - 16 - ph));
  return out;
};

const api = axios.create({ baseURL: "" });
api.interceptors.request.use(cfg => {
  const t = sessionStorage.getItem("mes_token");
  if (t) cfg.headers.Authorization = `Bearer ${t}`;
  return cfg;
});
// 401 → wipe session + bounce to /login.  EXCEPT on the native Android app with
// a saved session, where a stray 401 (esp. from this always-mounted chatbot) is
// a transient blip, not a real logout — swallow it and let the boot /me probe be
// the real validity gate, so the operator is never bounced to the password screen.
function _appHasSavedSession() {
  try {
    if (!document.documentElement.classList.contains("cap-android")) return false;
    const raw = localStorage.getItem("mes_auth_persist");
    return !!(raw && JSON.parse(raw)?.mes_token);
  } catch { return false; }
}
api.interceptors.response.use(r => r, err => {
  if (err?.response?.status === 401 && !_appHasSavedSession()) {
    try {
      ["mes_token","mes_username","user_role","user_id","user_dept_slug"]
        .forEach(k => sessionStorage.removeItem(k));
    } catch {}
    if (window.location.pathname !== "/login") window.location.replace("/login");
  }
  return Promise.reject(err);
});

// Per-user key — session only (clears on refresh, persists on page switch)
const getStorageKey = () => {
  const uid = sessionStorage.getItem("user_id") || "guest";
  return `mes_ai_chat_session_${uid}`;
};

const QUICK_PROMPTS = [
  "Today's OEE summary",
  "Aaj kitne fault aaye",
  "Open PY bypass kitne hain",
  "Kaun si line abhi band hai",
  "Is hafte sabse zyada breakdown kis line par",
  "Fault history page kholo",
];

const WELCOME = {
  role: "assistant",
  content: "Hi — I can read your plant data.\nAsk about production, OEE, NG, losses, faults, PY bypass, cameras or comments — per line, zone, day, week or month.\nI can also open a page: \"fault history kholo\".",
  id: "init",
  welcome: true,
};

// 2026-10-09 — the assistant is on every page (mounted once in App.jsx) and
// opens with that page's own questions.  `key` also goes to the backend so a
// question with no subject ("aaj kitne aaye?") is read as being about this
// page's data.  First match wins; anything else gets the general set.
const PAGE_INFO = [
  { test: p => p.startsWith("/fault-history"), key: "fault-history", title: "Fault History",
    prompts: ["Aaj kitne fault aaye", "Aaj sabse zyada kaunsa fault aaya", "Is hafte kis machine pe sabse zyada fault",
              "Kal sabse zyada fault kis line pe aaye"] },
  { test: p => p.startsWith("/py-bypass"), key: "py-bypass", title: "PY Bypass",
    prompts: ["Open PY bypass kitne hain", "Kal kitne bypass aaye", "Is hafte sabse zyada bypass kis line par"] },
  { test: p => p.startsWith("/video-coverage") || p.startsWith("/cms"), key: "video-coverage", title: "Video Coverage",
    prompts: ["Abhi kitne camera hung hain", "Kis line pe camera hung hain", "Abhi kitne camera online hain"] },
  { test: p => p.startsWith("/comments-history"), key: "comments-history", title: "Comments History",
    prompts: ["Aaj kitne comments likhe gaye", "Kal kis line pe sabse zyada comments", "Is hafte ke comments kitne"] },
  { test: p => p.startsWith("/shift-compile"), key: "shift-compile", title: "Shift Compile",
    prompts: ["Aaj shift A vs B production", "Kal shift B me sabse zyada NG kis line par", "Kaun si line abhi band hai"] },
  { test: p => p.startsWith("/historical"), key: "historical", title: "Historical",
    prompts: ["Kal ka plant production", "Is hafte sabse zyada breakdown kis line par", "Pichhle hafte sabse kam OEE kis line ka",
              "Is mahine total production kitna"] },
  { test: p => p.startsWith("/quality") || p.startsWith("/sa-fi") || p.startsWith("/redbin"), key: "quality", title: "Quality",
    prompts: ["Aaj plant me kitne NG", "Kal sabse zyada NG kis line par", "Is hafte ka NG line wise", "Open PY bypass kitne hain"] },
  { test: p => p.startsWith("/maintenance") || p.startsWith("/prod-breakdown") || p.startsWith("/andon"), key: "maintenance", title: "Maintenance",
    prompts: ["Aaj sabse zyada breakdown kis line par", "Aaj kitne fault aaye", "Kaun si line abhi band hai",
              "Is hafte kis machine pe sabse zyada fault"] },
  { test: p => p.startsWith("/dashboard") || p === "/" || p.startsWith("/department"), key: "dashboard", title: "Dashboard",
    prompts: ["Today's OEE summary", "Aaj sabse zyada production kis line ka", "Kaun si line abhi band hai",
              "Aaj sabse kam OEE kis line ka", "Aaj plant ka total production"] },
];
const GENERAL = { key: "", title: "", prompts: QUICK_PROMPTS };
const pageInfoFor = (path) => PAGE_INFO.find(i => i.test(path || "")) || GENERAL;

// Not on the TV wall dashboards (they run unattended on the panels and must
// stay as they are) or before sign-in.
// 2026-10-10 — operator wants the assistant on the MANAGEMENT wall
// (/:line/MANAGEMENT), so that path is NO LONGER excluded.  The other
// unattended walls (SUPERVISOR, plain /fullscreen/, wallboard, submachine)
// stay excluded.
const NO_ASSISTANT = p => p === "/login" || /\/SUPERVISOR$/i.test(p) ||
  p.startsWith("/fullscreen/") || p.startsWith("/ywd-fullscreen/") ||
  p.startsWith("/wallboard/") || p.startsWith("/submachine-fullscreen/");

// ── Colors (dark, matches app; no animation) ──────────────────────────────
const C = {
  bg:      "#0b1220",
  panel:   "#0e1524",
  border:  "#1e2a40",
  userBg:  "#1e40af",
  aiBg:    "#111a2b",
  text:    "#dbe6f5",
  textDim: "#8aa0c0",
  accent:  "#3b82f6",
};

// The per-page <AIAssistant /> tags that 19 pages still carry render nothing:
// the one global instance (App.jsx, `global`) covers every page.
export default function AIAssistant({ global = false, pageContext = {} }) {
  const loc = useLocation();
  if (!global || NO_ASSISTANT(loc.pathname)) return null;
  return <AssistantPanel pageContext={pageContext} path={loc.pathname} />;
}

function AssistantPanel({ pageContext = {}, path = "" }) {
  const navigate = useNavigate();
  const { canAccess } = useAuth();
  const info = pageInfoFor(path);
  const [messages, setMessages] = useState(() => {
    try {
      const s = sessionStorage.getItem(getStorageKey());
      if (s) { const p = JSON.parse(s); if (p?.length) return p; }
    } catch {}
    return [WELCOME];
  });

  const [open, setOpen]     = useState(false);
  const [input, setInput]   = useState("");
  const [thinking, setThink] = useState(false);
  const [error, setError]   = useState("");
  const [lineNames, setLineNames] = useState([]);
  const bottomRef = useRef(null);
  const inputRef  = useRef(null);
  const panelRef  = useRef(null);

  // 2026-10-14 — the floating button can be dragged anywhere (it was covering
  // the TOTAL column); the spot is remembered per browser.  null = default
  // bottom-right corner.
  const [btnPos, setBtnPos] = useState(() => {
    try {
      const p = JSON.parse(localStorage.getItem(BTN_POS_KEY) || "null");
      if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) return clampBtn(p);
    } catch {}
    return null;
  });
  const drag = useRef(null);
  const justDragged = useRef(false);
  useEffect(() => {
    const onResize = () => setBtnPos(p => (p ? clampBtn(p) : p));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const onBtnDown = (e) => {
    if (e.button !== undefined && e.button !== 0) return;
    const r = e.currentTarget.getBoundingClientRect();
    drag.current = { sx: e.clientX, sy: e.clientY, ox: r.left, oy: r.top, moved: false };
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch {}
  };
  const onBtnMove = (e) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.sx, dy = e.clientY - d.sy;
    if (!d.moved && Math.hypot(dx, dy) < 6) return;
    d.moved = true;
    setBtnPos(clampBtn({ x: d.ox + dx, y: d.oy + dy }));
  };
  const onBtnUp = () => {
    const d = drag.current;
    drag.current = null;
    if (!d?.moved) return;
    justDragged.current = true;
    setBtnPos(p => {
      try { if (p) localStorage.setItem(BTN_POS_KEY, JSON.stringify(p)); } catch {}
      return p;
    });
  };

  // Line names power the autocomplete suggestions (e.g. "YSD-SS OEE today").
  useEffect(() => {
    let alive = true;
    api.get("/api/lines")
      .then(r => { if (alive) setLineNames((Array.isArray(r.data) ? r.data : []).map(l => l.line_name).filter(Boolean)); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  // ── Autocomplete: suggest the rest of the query as the user types ────────
  const SUGGESTIONS = useMemo(() => {
    const generic = [
      "Today's OEE summary", "Lowest efficiency line", "Highest production today",
      "Total loss time today", "Compare shifts A vs B", "Poka yoke alerts",
      "NG parts this shift", "Aaj kitne fault aaye", "Open PY bypass kitne hain",
      "Kaun si line abhi band hai", "Abhi kitne camera hung hain",
      "Is hafte sabse zyada breakdown kis line par", "Is mahine total production kitna",
      "Fault history page kholo",
    ];
    const per = [];
    for (const n of lineNames) {
      per.push(`${n} production today`, `${n} OEE today`, `${n} NG today`,
               `${n} loss today`, `${n} production yesterday shift A`);
    }
    return [...info.prompts, ...generic, ...per];
  }, [lineNames, info]);

  // Best completion for the current text (prefix match, case-insensitive; the
  // tightest — shortest — match wins so the ghost stays short).
  const suggestion = useMemo(() => {
    const q = input;
    if (!q.trim()) return "";
    const ql = q.toLowerCase();
    const hits = SUGGESTIONS
      .filter(s => s.toLowerCase().startsWith(ql) && s.toLowerCase() !== ql)
      .sort((a, b) => a.length - b.length);
    return hits[0] || "";
  }, [input, SUGGESTIONS]);
  const ghost = suggestion ? suggestion.slice(input.length) : "";
  const acceptSuggestion = () => {
    if (!suggestion) return;
    setInput(suggestion);
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  // ── Page / module name suggestions: type a name, pick it, the page opens ──
  const myPages = useMemo(() => ALL_PAGES.filter(p => canAccess(p.key)), [canAccess]);
  const pageMatches = useMemo(() => {
    const q = input.trim().toLowerCase();
    if (!q) return [];
    const starts = [], contains = [];
    for (const p of myPages) {
      const l = (p.label || "").toLowerCase();
      if (l.startsWith(q) || (p.key || "").toLowerCase().startsWith(q)) starts.push(p);
      else if (l.includes(q)) contains.push(p);
    }
    return [...starts, ...contains].slice(0, 6);
  }, [input, myPages]);
  const [pageIdx, setPageIdx] = useState(-1);
  useEffect(() => { setPageIdx(-1); }, [input]);
  const openPage = (p) => {
    if (!p || !canAccess(p.key)) return;
    setInput("");
    setOpen(false);
    setTimeout(() => navigate(p.path), 0);
  };

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  useEffect(() => {
    try { sessionStorage.setItem(getStorageKey(), JSON.stringify(messages)); } catch {}
  }, [messages]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, thinking]);
  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 120); }, [open]);

  const send = async (text) => {
    const msg = (text || input).trim();
    if (!msg || thinking) return;
    setInput("");
    setError("");
    setMessages(p => [...p, { role: "user", content: msg, id: Date.now() + "u" }]);
    setThink(true);
    try {
      const res = await api.post("/api/ai/chat", {
        message: msg,
        context: { ...pageContext, page: info.key, page_title: info.title, path },
        history: messages.slice(-10).map(m => ({ role: m.role, content: m.content })),
      });
      // 2026-10-08 — the assistant can open a page ("fault history kholo").
      // It only goes there if this user may see that page; the key is the
      // same one SlideNav checks.
      let reply = res.data?.reply || "No answer.";
      const act = res.data?.action;
      if (act?.navigate) {
        if (!act.page_key || canAccess(act.page_key)) {
          setTimeout(() => { setOpen(false); navigate(act.navigate); }, 500);
        } else {
          reply = "You don't have access to that page.";
        }
      }
      setMessages(p => [...p, { role: "assistant", content: reply, id: Date.now() + "a" }]);
    } catch (e) {
      setMessages(p => [...p, { role: "assistant", content: "Connection error. Please try again.", id: Date.now() + "e" }]);
      setError(e.message);
    } finally {
      setThink(false);
    }
  };

  const clear = () => setMessages([{ ...WELCOME, id: Date.now() + "c" }]);

  return (
    <>
      <style>{`@keyframes aiSpin{to{transform:rotate(360deg)}}`}</style>

      {/* Floating button */}
      {!open && (
        <button
          onClick={() => { if (justDragged.current) { justDragged.current = false; return; } setOpen(true); }}
          onPointerDown={onBtnDown} onPointerMove={onBtnMove}
          onPointerUp={onBtnUp} onPointerCancel={() => { drag.current = null; }}
          title="Assistant (drag to move)" style={{
          position: "fixed", zIndex: 10000,
          ...(btnPos ? { left: btnPos.x, top: btnPos.y } : { bottom: 20, right: 20 }),
          width: BTN_SIZE, height: BTN_SIZE, borderRadius: 14,
          background: C.panel, border: `1px solid ${C.border}`,
          cursor: "grab", outline: "none", touchAction: "none",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 24, boxShadow: "0 6px 18px rgba(0,0,0,.35)",
        }}>
          🤖
          <span style={{
            position: "absolute", top: 7, right: 7,
            width: 8, height: 8, borderRadius: "50%",
            background: "#22c55e", border: `2px solid ${C.panel}`,
          }} />
        </button>
      )}

      {/* Panel — responsive width/height so it never overflows the screen */}
      {open && (
        <div ref={panelRef} style={{
          position: "fixed", zIndex: 10000, ...panelAnchor(btnPos),
          width: "min(400px, calc(100vw - 32px))",
          height: "min(560px, calc(100dvh - 88px))",
          maxWidth: "calc(100vw - 32px)", maxHeight: "calc(100dvh - 88px)",
          background: C.bg, borderRadius: 16,
          border: `1px solid ${C.border}`,
          boxShadow: "0 18px 48px rgba(0,0,0,.5)",
          display: "flex", flexDirection: "column", overflow: "hidden",
          fontFamily: "system-ui, sans-serif",
        }}>

          {/* Header */}
          <div style={{
            padding: "12px 14px",
            borderBottom: `1px solid ${C.border}`,
            display: "flex", alignItems: "center", justifyContent: "space-between",
            background: C.panel, flexShrink: 0,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
              <span style={{ fontSize: 20 }}>🤖</span>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: C.text }}>MES Assistant</div>
                <div style={{ fontSize: 10, color: "#22c55e", fontWeight: 600 }}>
                  {thinking ? "Analyzing…" : "Online"}
                </div>
              </div>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              {[{ i: "↺", t: "Clear", a: clear }, { i: "✕", t: "Close", a: () => setOpen(false) }].map(b => (
                <button key={b.t} title={b.t} onClick={b.a} style={{
                  width: 30, height: 30, borderRadius: 8,
                  background: "transparent", border: `1px solid ${C.border}`,
                  cursor: "pointer", color: C.textDim, fontSize: 14,
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}>{b.i}</button>
              ))}
            </div>
          </div>

          {/* Messages */}
          <div style={{ flex: 1, overflowY: "auto", overflowX: "hidden", padding: "12px 12px", minHeight: 0 }}>
            {messages.map(m => {
              const isUser = m.role === "user";
              return (
                <div key={m.id} style={{
                  display: "flex", justifyContent: isUser ? "flex-end" : "flex-start", marginBottom: 10,
                }}>
                  <div style={{
                    maxWidth: "85%",
                    padding: "9px 12px",
                    borderRadius: isUser ? "14px 14px 4px 14px" : "14px 14px 14px 4px",
                    background: isUser ? C.userBg : C.aiBg,
                    color: isUser ? "#eaf2ff" : C.text,
                    fontSize: 12.5, lineHeight: 1.55,
                    fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                    border: `1px solid ${isUser ? "#2b4fd0" : C.border}`,
                    whiteSpace: "pre-wrap", wordBreak: "break-word",
                  }}>{(m.welcome || m.content === WELCOME.content) && info.title
                        ? `You're on ${info.title}. Tap a question below or type your own.\n` + m.content
                        : m.content}</div>
                </div>
              );
            })}
            {thinking && (
              <div style={{ display: "flex", alignItems: "center", gap: 8, color: C.textDim, fontSize: 12, margin: "4px 2px" }}>
                <span style={{
                  width: 14, height: 14, borderRadius: "50%",
                  border: `2px solid ${C.border}`, borderTopColor: C.accent,
                  display: "inline-block", animation: "aiSpin .7s linear infinite",
                }} />
                Analyzing…
              </div>
            )}
            {error && (
              <div style={{ margin: "6px 0", padding: "7px 10px", borderRadius: 8,
                background: "#2a1414", border: "1px solid #7f1d1d", fontSize: 11, color: "#fca5a5" }}>
                {error}
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Quick prompts (only on the welcome screen) */}
          {messages.length <= 2 && (
            <div style={{ padding: "8px 12px", borderTop: `1px solid ${C.border}`, background: C.panel, flexShrink: 0 }}>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {info.prompts.map(q => (
                  <button key={q} onClick={() => send(q)} style={{
                    background: C.aiBg, border: `1px solid ${C.border}`, borderRadius: 99,
                    padding: "5px 10px", fontSize: 11, color: C.textDim, cursor: "pointer",
                  }}>{q}</button>
                ))}
              </div>
            </div>
          )}

          {/* Input */}
          <div style={{ padding: "10px 12px", borderTop: `1px solid ${C.border}`, background: C.panel, flexShrink: 0 }}>
            {/* Page / module name suggestions — click to open, ↑/↓ then Enter to pick */}
            {pageMatches.length > 0 && (
              <div style={{ marginBottom: 8, borderRadius: 10, overflow: "hidden",
                            border: `1px solid ${C.border}`, background: C.bg }}>
                <div style={{ padding: "5px 10px", fontSize: 10, fontWeight: 700, color: C.textDim,
                              textTransform: "uppercase", letterSpacing: ".05em" }}>Open page</div>
                {pageMatches.map((p, i) => (
                  <div key={p.path}
                       onMouseDown={e => { e.preventDefault(); openPage(p); }}
                       onMouseEnter={() => setPageIdx(i)}
                       style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer",
                                padding: "8px 10px", fontSize: 12.5, color: C.text,
                                background: i === pageIdx ? C.aiBg : "transparent",
                                borderTop: `1px solid ${C.border}` }}>
                    <span style={{ fontSize: 14, flexShrink: 0 }}>{p.icon && !/^\//.test(p.icon) ? p.icon : "📄"}</span>
                    <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.label}</span>
                    <span style={{ fontSize: 10, fontWeight: 700, color: C.accent, flexShrink: 0 }}>Open ↵</span>
                  </div>
                ))}
              </div>
            )}
            {/* Autocomplete hint — tap to complete (touch), or press Tab */}
            {ghost && (
              <div onClick={acceptSuggestion} style={{
                marginBottom: 8, padding: "6px 10px", borderRadius: 8, cursor: "pointer",
                background: C.aiBg, border: `1px dashed ${C.border}`,
                fontSize: 11.5, color: C.textDim, display: "flex", alignItems: "center", gap: 8,
                whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
              }}>
                <span style={{ fontSize: 10, fontWeight: 700, color: C.accent, flexShrink: 0 }}>⇥ Tab</span>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", color: C.text }}>{suggestion}</span>
              </div>
            )}
            <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
              {/* Ghost overlay + transparent textarea share identical text metrics
                  so the gray continuation lines up right after the typed text. */}
              <div style={{ flex: 1, position: "relative", background: C.bg,
                            border: `1px solid ${C.border}`, borderRadius: 10, overflow: "hidden" }}>
                {ghost && (
                  <div aria-hidden style={{
                    position: "absolute", inset: 0, padding: "9px 12px",
                    fontSize: 12.5, fontFamily: "system-ui, sans-serif", lineHeight: 1.5,
                    whiteSpace: "pre-wrap", wordBreak: "break-word",
                    color: "transparent", pointerEvents: "none", overflow: "hidden",
                  }}>
                    {input}<span style={{ color: C.textDim, opacity: .8 }}>{ghost}</span>
                  </div>
                )}
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => {
                    if (pageMatches.length) {
                      if (e.key === "ArrowDown") { e.preventDefault(); setPageIdx(i => Math.min(i + 1, pageMatches.length - 1)); return; }
                      if (e.key === "ArrowUp")   { e.preventDefault(); setPageIdx(i => Math.max(i - 1, -1)); return; }
                      if (e.key === "Enter" && !e.shiftKey && pageIdx >= 0) { e.preventDefault(); openPage(pageMatches[pageIdx]); return; }
                    }
                    if (ghost && (e.key === "Tab" ||
                        (e.key === "ArrowRight" && e.target.selectionStart === input.length && e.target.selectionEnd === input.length))) {
                      e.preventDefault(); acceptSuggestion(); return;
                    }
                    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
                  }}
                  placeholder="Ask about production data…"
                  rows={1}
                  style={{
                    position: "relative", display: "block", width: "100%", resize: "none",
                    padding: "9px 12px", background: "transparent", border: "none",
                    fontSize: 12.5, color: C.text, outline: "none", lineHeight: 1.5,
                    fontFamily: "system-ui, sans-serif", boxSizing: "border-box", maxHeight: 90,
                  }}
                />
              </div>
              <button
                onClick={() => send()}
                disabled={thinking || !input.trim()}
                style={{
                  width: 40, height: 40, borderRadius: 10, flexShrink: 0,
                  background: thinking || !input.trim() ? C.aiBg : C.accent,
                  border: `1px solid ${C.border}`,
                  cursor: thinking || !input.trim() ? "not-allowed" : "pointer",
                  color: "#fff", fontSize: 18,
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}
              >↑</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
