import { useState, useRef, useEffect, useMemo } from "react";
import axios from "axios";

// ───────────────────────────────────────────────────────────────────────
// AIAssistant — floating production-data chatbot.
// 2026-09-14 — Simplified: removed all decorative animations (particle canvas,
// rocket loader, typing effect, floating/scanline/glow effects) per operator
// request, and made the panel RESPONSIVE so it never overflows the screen on
// the phone app (was a fixed 400px wide panel → horizontal overflow < ~424px).
// Backend is the offline DB chatbot at /api/ai/chat (no external LLM).
// ───────────────────────────────────────────────────────────────────────

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
  "NG parts this shift",
  "Lowest efficiency line",
  "Total loss time today",
  "Compare shifts A vs B",
  "Poka yoke alerts",
];

const WELCOME = {
  role: "assistant",
  content: "Hi — I can read your production data.\nAsk about a line's production, OEE, NG, losses or plan, or the plant summary.",
  id: "init",
};

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

export default function AIAssistant({ pageContext = {} }) {
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
      "NG parts this shift",
    ];
    const per = [];
    for (const n of lineNames) {
      per.push(`${n} production today`, `${n} OEE today`, `${n} NG today`,
               `${n} loss today`, `${n} production yesterday shift A`);
    }
    return [...generic, ...per];
  }, [lineNames]);

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
        context: pageContext,
        history: messages.slice(-10).map(m => ({ role: m.role, content: m.content })),
      });
      setMessages(p => [...p, { role: "assistant", content: res.data?.reply || "No answer.", id: Date.now() + "a" }]);
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
        <button onClick={() => setOpen(true)} title="Assistant" style={{
          position: "fixed", bottom: 20, right: 20, zIndex: 10000,
          width: 54, height: 54, borderRadius: 14,
          background: C.panel, border: `1px solid ${C.border}`,
          cursor: "pointer", outline: "none",
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
          position: "fixed", bottom: 16, right: 16, zIndex: 10000,
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
                  }}>{m.content}</div>
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
                {QUICK_PROMPTS.map(q => (
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
