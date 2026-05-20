import { useState, useRef, useEffect } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";

function MessageBubble({ msg }) {
  const isUser = msg.role === "user";
  return (
    <div style={{ display:"flex", justifyContent:isUser?"flex-end":"flex-start", marginBottom:10 }}>
      {!isUser && (
        <div style={{ width:26, height:26, borderRadius:"50%", background:"linear-gradient(135deg,#1e40af,#2563eb)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:10, color:"#fff", fontWeight:800, flexShrink:0, marginRight:8, marginTop:2 }}>
          AI
        </div>
      )}
      <div style={{
        maxWidth:"82%", padding:"9px 13px",
        borderRadius: isUser ? "12px 12px 4px 12px" : "12px 12px 12px 4px",
        background: isUser ? "linear-gradient(135deg,#1e40af,#2563eb)" : "#f1f5f9",
        color: isUser ? "#fff" : "#0f172a",
        fontSize:12, lineHeight:1.7,
        boxShadow:"0 1px 3px rgba(0,0,0,0.08)",
        whiteSpace:"pre-wrap",
        fontFamily: isUser ? "inherit" : "'Segoe UI',sans-serif",
      }}>
        {msg.content}
      </div>
    </div>
  );
}

const QUICK_PROMPTS = [
  "What is today's OEE?",
  "How many NG parts yesterday?",
  "Which line has lowest efficiency?",
  "Show poka yoke faults today",
  "Compare shifts A and B",
  "Total loss time this week",
];

export default function AIAssistant({ pageContext = {} }) {
  const { token } = useAuth();

  const [open,     setOpen]     = useState(false);
  const [messages, setMessages] = useState([]);
  const [input,    setInput]    = useState("");
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState("");

  const bottomRef = useRef(null);
  const inputRef  = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior:"smooth" });
  }, [messages, loading]);

  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 100);
      if (messages.length === 0) {
        setMessages([{
          role:"assistant",
          content:"Hi! I'm your MES AI Assistant powered by Claude.\n\nI have direct access to your production database — ask me anything about OEE, NG parts, losses, poka yoke events, shift performance, or any other production data. 🏭",
        }]);
      }
    }
  }, [open]);

  const send = async (text) => {
    const msg = (text || input).trim();
    if (!msg || loading) return;
    setInput("");
    setError("");

    const newMessages = [...messages, { role:"user", content:msg }];
    setMessages(newMessages);
    setLoading(true);

    try {
      const res = await api.post("/api/ai/chat", {
        message: msg,
        context: pageContext,
        history: messages.slice(-8),
      }, token);

      setMessages(prev => [...prev, {
        role:"assistant",
        content: res.reply,
      }]);
    } catch(e) {
      const errMsg = e.message || "Failed to get response";
      setError(errMsg);
      setMessages(prev => [...prev, {
        role:"assistant",
        content:"Sorry, I encountered an error. Please try again.",
      }]);
    } finally {
      setLoading(false);
    }
  };

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const clearChat = () => {
    setMessages([{
      role:"assistant",
      content:"Chat cleared. How can I help you with your MES data?",
    }]);
    setError("");
  };

  return (
    <>
      <style>{`
        @keyframes aiPulse {
          0%,100%{box-shadow:0 4px 20px rgba(30,64,175,0.4)}
          50%{box-shadow:0 4px 32px rgba(30,64,175,0.7)}
        }
        @keyframes aiSlideUp {
          from{opacity:0;transform:translateY(20px) scale(0.95)}
          to{opacity:1;transform:none}
        }
        @keyframes aiBlink {
          0%,100%{opacity:1} 50%{opacity:0.3}
        }
        .ai-scroll::-webkit-scrollbar{width:3px;}
        .ai-scroll::-webkit-scrollbar-thumb{background:#cbd5e1;border-radius:2px;}
        .ai-quick:hover{background:#dbeafe !important;color:#1e40af !important;}
        .ai-send:hover:not(:disabled){filter:brightness(1.1);transform:scale(1.05);}
      `}</style>

      {/* ── FLOATING BUTTON ── */}
      {!open && (
        <button
          onClick={() => setOpen(true)}
          title="MES AI Assistant"
          style={{
            position:"fixed", bottom:28, right:28, zIndex:9000,
            width:54, height:54, borderRadius:"50%",
            background:"linear-gradient(135deg,#1e40af,#2563eb)",
            border:"none", cursor:"pointer",
            display:"flex", alignItems:"center", justifyContent:"center",
            animation:"aiPulse 3s ease infinite",
            transition:"transform .15s",
            boxShadow:"0 4px 20px rgba(30,64,175,0.4)",
          }}
          onMouseEnter={e => e.currentTarget.style.transform="scale(1.12)"}
          onMouseLeave={e => e.currentTarget.style.transform="scale(1)"}
        >
          <span style={{ fontSize:24 }}>🤖</span>
        </button>
      )}

      {/* ── CHAT PANEL ── */}
      {open && (
        <div style={{
          position:"fixed", bottom:28, right:28, zIndex:9000,
          width:390, height:600,
          background:"#fff", borderRadius:18,
          boxShadow:"0 24px 80px rgba(0,0,0,0.18), 0 0 0 1px rgba(0,0,0,0.06)",
          display:"flex", flexDirection:"column",
          animation:"aiSlideUp .25s cubic-bezier(.16,1,.3,1)",
          overflow:"hidden",
          fontFamily:"'Segoe UI',system-ui,sans-serif",
        }}>

          {/* Header */}
          <div style={{ background:"linear-gradient(135deg,#1e40af,#2563eb)", padding:"12px 16px", flexShrink:0 }}>
            <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center" }}>
              <div style={{ display:"flex", alignItems:"center", gap:10 }}>
                <div style={{ width:36, height:36, borderRadius:"50%", background:"rgba(255,255,255,0.15)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:18, border:"2px solid rgba(255,255,255,0.3)" }}>
                  🤖
                </div>
                <div>
                  <div style={{ fontSize:13, fontWeight:700, color:"#fff" }}>MES AI Assistant</div>
                  <div style={{ fontSize:9, color:"rgba(255,255,255,0.65)", marginTop:1 }}>
                    Toyota Boshoku Device India · Powered by Claude
                  </div>
                </div>
              </div>
              <div style={{ display:"flex", gap:6 }}>
                {/* Clear button */}
                <button onClick={clearChat}
                  title="Clear chat"
                  style={{ background:"rgba(255,255,255,0.12)", border:"1px solid rgba(255,255,255,0.2)", borderRadius:7, width:28, height:28, cursor:"pointer", color:"#fff", fontSize:13, display:"flex", alignItems:"center", justifyContent:"center" }}>
                  🗑
                </button>
                {/* Close button */}
                <button onClick={() => setOpen(false)}
                  style={{ background:"rgba(255,255,255,0.12)", border:"1px solid rgba(255,255,255,0.2)", borderRadius:7, width:28, height:28, cursor:"pointer", color:"#fff", fontSize:14, display:"flex", alignItems:"center", justifyContent:"center" }}>
                  ✕
                </button>
              </div>
            </div>

            {/* DB access indicator */}
            <div style={{ marginTop:8, display:"flex", alignItems:"center", gap:5, padding:"4px 10px", background:"rgba(255,255,255,0.1)", borderRadius:99, width:"fit-content" }}>
              <div style={{ width:6, height:6, borderRadius:"50%", background:"#22c55e", animation:"aiBlink 2s infinite" }}/>
              <span style={{ fontSize:9, color:"rgba(255,255,255,0.85)", fontWeight:600 }}>Live DB Access · {pageContext.page || "MES"}</span>
            </div>
          </div>

          {/* Messages */}
          <div className="ai-scroll" style={{ flex:1, overflowY:"auto", padding:"12px 12px 4px" }}>

            {messages.map((msg, i) => (
              <MessageBubble key={i} msg={msg} />
            ))}

            {/* Thinking indicator */}
            {loading && (
              <div style={{ display:"flex", alignItems:"center", gap:8, marginBottom:10 }}>
                <div style={{ width:26, height:26, borderRadius:"50%", background:"linear-gradient(135deg,#1e40af,#2563eb)", display:"flex", alignItems:"center", justifyContent:"center", fontSize:10, color:"#fff", fontWeight:800, flexShrink:0 }}>
                  AI
                </div>
                <div style={{ padding:"10px 14px", background:"#f1f5f9", borderRadius:"12px 12px 12px 4px", display:"flex", alignItems:"center", gap:5 }}>
                  <span style={{ fontSize:11, color:"#64748b", marginRight:4 }}>Querying database</span>
                  {[0,1,2].map(i => (
                    <div key={i} style={{ width:5, height:5, borderRadius:"50%", background:"#94a3b8", animation:`aiBlink ${0.6+i*0.2}s ease infinite` }}/>
                  ))}
                </div>
              </div>
            )}

            {error && (
              <div style={{ padding:"8px 12px", background:"rgba(220,38,38,0.06)", border:"1px solid rgba(220,38,38,0.2)", borderRadius:8, fontSize:11, color:"#dc2626", marginBottom:8 }}>
                ⚠ {error}
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Quick prompts */}
          {messages.length <= 1 && (
            <div style={{ padding:"4px 12px 6px", display:"flex", flexWrap:"wrap", gap:5 }}>
              {QUICK_PROMPTS.map(q => (
                <button key={q} className="ai-quick"
                  onClick={() => send(q)}
                  style={{
                    padding:"3px 10px", borderRadius:99, fontSize:10, fontWeight:600,
                    background:"#eff6ff", color:"#2563eb",
                    border:"1px solid #bfdbfe", cursor:"pointer",
                    transition:"all .12s",
                  }}
                >
                  {q}
                </button>
              ))}
            </div>
          )}

          {/* Input */}
          <div style={{ padding:"8px 12px 12px", borderTop:"1px solid #f1f5f9", flexShrink:0 }}>
            <div style={{ display:"flex", gap:8, alignItems:"flex-end" }}>
              <textarea
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={onKey}
                placeholder="Ask about production data, OEE, NG parts, losses…"
                rows={2}
                style={{
                  flex:1, resize:"none", padding:"9px 12px",
                  background:"#f8fafc", border:"1.5px solid #e2e8f0",
                  borderRadius:10, fontSize:12,
                  color:"#0f172a", lineHeight:1.5,
                  fontFamily:"'Segoe UI',sans-serif",
                  outline:"none", transition:"border-color .15s",
                }}
                onFocus={e  => e.target.style.borderColor="#3b82f6"}
                onBlur={e   => e.target.style.borderColor="#e2e8f0"}
              />
              <button
                className="ai-send"
                onClick={() => send()}
                disabled={loading || !input.trim()}
                style={{
                  width:40, height:40, borderRadius:10, flexShrink:0,
                  background: loading||!input.trim()
                    ? "#f1f5f9"
                    : "linear-gradient(135deg,#1e40af,#2563eb)",
                  border:"none",
                  cursor: loading||!input.trim() ? "not-allowed" : "pointer",
                  display:"flex", alignItems:"center", justifyContent:"center",
                  fontSize:16,
                  color: loading||!input.trim() ? "#94a3b8" : "#fff",
                  transition:"all .12s",
                  boxShadow: !loading&&input.trim() ? "0 2px 8px rgba(30,64,175,.3)" : "none",
                }}
              >
                {loading
                  ? <div style={{ width:14, height:14, borderRadius:"50%", border:"2px solid rgba(255,255,255,.3)", borderTopColor:"#fff", animation:"spin .6s linear infinite" }}/>
                  : "↑"
                }
              </button>
            </div>
            <div style={{ fontSize:9, color:"#cbd5e1", marginTop:5, textAlign:"center" }}>
              Enter to send · Shift+Enter for new line · Claude has live DB access
            </div>
          </div>

        </div>
      )}
    </>
  );
}