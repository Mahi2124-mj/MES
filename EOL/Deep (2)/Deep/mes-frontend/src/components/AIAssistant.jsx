import { useState, useRef, useEffect } from "react";
import axios from "axios";

const api = axios.create({ baseURL: "" });
api.interceptors.request.use(cfg => {
  const t = sessionStorage.getItem("mes_token");
  if (t) cfg.headers.Authorization = `Bearer ${t}`;
  return cfg;
});
// 401 → wipe session + bounce to /login.
api.interceptors.response.use(r => r, err => {
  if (err?.response?.status === 401) {
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
};const QUICK_PROMPTS = [
  { icon: "📊", text: "Today's OEE summary" },
  { icon: "⚠️", text: "NG parts this shift" },
  { icon: "🏭", text: "Lowest efficiency line" },
  { icon: "⏱️", text: "Total loss time today" },
  { icon: "🔄", text: "Compare shifts A vs B" },
  { icon: "🛡️", text: "Poka yoke alerts" },
];

// ── Particle Network Background ───────────────────────────────────────────────
function ParticleNet({ thinking }) {
  const ref = useRef(null);
  const anim = useRef(null);

  useEffect(() => {
    const c = ref.current; if (!c) return;
    const ctx = c.getContext("2d");
    c.width  = c.offsetWidth;
    c.height = c.offsetHeight;
    const W = c.width, H = c.height;
    const count = thinking ? 50 : 20;

    const pts = Array.from({ length: count }, () => ({
      x: Math.random()*W, y: Math.random()*H,
      vx: (Math.random()-.5)*(thinking?1.2:.3),
      vy: (Math.random()-.5)*(thinking?1.2:.3),
      r:  Math.random()*2+.5,
      hue: Math.random()*60+200, // blue-cyan range
      life: Math.random(),
    }));

    const tick = () => {
      ctx.clearRect(0,0,W,H);
      pts.forEach((p,i) => {
        p.x += p.vx; p.y += p.vy;
        if(p.x<0||p.x>W) p.vx*=-1;
        if(p.y<0||p.y>H) p.vy*=-1;
        p.life = (p.life + .005) % 1;

        // Lines
        pts.slice(i+1).forEach(q => {
          const d = Math.hypot(p.x-q.x, p.y-q.y);
          if(d < 70) {
            ctx.beginPath();
            ctx.moveTo(p.x,p.y); ctx.lineTo(q.x,q.y);
            ctx.strokeStyle = thinking
              ? `hsla(${p.hue},100%,65%,${(1-d/70)*.25})`
              : `rgba(59,130,246,${(1-d/70)*.07})`;
            ctx.lineWidth = .6;
            ctx.stroke();
          }
        });

        // Dot
        ctx.beginPath();
        ctx.arc(p.x,p.y,p.r,0,Math.PI*2);
        const alpha = .4 + Math.sin(p.life*Math.PI*2)*.3;
        ctx.fillStyle = thinking
          ? `hsla(${p.hue},100%,70%,${alpha})`
          : `rgba(59,130,246,${alpha*.3})`;
        if(thinking) { ctx.shadowBlur=8; ctx.shadowColor=`hsl(${p.hue},100%,65%)`; }
        ctx.fill();
        ctx.shadowBlur=0;
      });
      anim.current = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(anim.current);
  }, [thinking]);

  return <canvas ref={ref} style={{ position:"absolute",inset:0,width:"100%",height:"100%",borderRadius:"inherit",pointerEvents:"none" }} />;
}

// ── Rocket Thinking Animation ─────────────────────────────────────────────────
function RocketThinking() {
  const [pos, setPos] = useState({ x:50, y:50 });
  const [angle, setAngle] = useState(0);
  const [sparks, setSparks] = useState([]);
  const t = useRef(0);

  useEffect(() => {
    const iv = setInterval(() => {
      t.current += 0.05;
      const x = 50 + Math.cos(t.current) * 28;
      const y = 50 + Math.sin(t.current*1.3) * 18;
      const dx = Math.cos(t.current) * -1;
      const dy = Math.sin(t.current*1.3) * 1.3;
      setAngle(Math.atan2(dy, dx) * 180/Math.PI);
      setPos({ x, y });
      setSparks(prev => {
        const ns = [...prev, {
          id: Date.now()+Math.random(),
          x, y,
          vx: (Math.random()-.5)*3,
          vy: (Math.random()-.5)*3,
          life: 1,
          color: ["#3b82f6","#06b6d4","#8b5cf6","#ec4899","#f59e0b"][Math.floor(Math.random()*5)],
        }].map(s => ({ ...s, life: s.life - .07 })).filter(s => s.life > 0);
        return ns;
      });
    }, 40);
    return () => clearInterval(iv);
  }, []);

  return (
    <div style={{ position:"relative", width:"100%", height:140, overflow:"hidden" }}>
      {/* Orbit path */}
      <svg style={{ position:"absolute",inset:0,width:"100%",height:"100%", opacity:.15 }}>
        <ellipse cx="50%" cy="50%" rx="42%" ry="30%" fill="none"
          stroke="#3b82f6" strokeWidth="1" strokeDasharray="4 3" />
      </svg>

      {/* Sparks */}
      {sparks.map(s => (
        <div key={s.id} style={{
          position:"absolute",
          left:`${s.x + s.vx * (1-s.life) * 8}%`,
          top:`${s.y + s.vy * (1-s.life) * 8}%`,
          width: 4, height: 4, borderRadius:"50%",
          background: s.color,
          opacity: s.life,
          boxShadow: `0 0 6px ${s.color}`,
          transform:"translate(-50%,-50%)",
          pointerEvents:"none",
          transition:"none",
        }} />
      ))}

      {/* Rocket */}
      <div style={{
        position:"absolute",
        left:`${pos.x}%`, top:`${pos.y}%`,
        transform:`translate(-50%,-50%) rotate(${angle}deg)`,
        fontSize: 28,
        filter:"drop-shadow(0 0 8px #3b82f6) drop-shadow(0 0 16px #06b6d4)",
        transition:"none",
      }}>🚀</div>

      {/* Center text */}
      <div style={{
        position:"absolute", inset:0,
        display:"flex", flexDirection:"column",
        alignItems:"center", justifyContent:"center",
        gap:4,
      }}>
        <div style={{
          fontSize:11, fontWeight:800, color:"#3b82f6",
          letterSpacing:".15em", textTransform:"uppercase",
          textShadow:"0 0 12px #3b82f6",
          animation:"neonPulse 1.5s ease infinite",
        }}>
          Analyzing Data
        </div>
        <div style={{ display:"flex", gap:5 }}>
          {[0,1,2,3].map(i => (
            <div key={i} style={{
              width:5, height:5, borderRadius:"50%",
              background:"#06b6d4",
              boxShadow:"0 0 8px #06b6d4",
              animation:`dotBounce .8s ease infinite`,
              animationDelay:`${i*.15}s`,
            }} />
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Message Bubble ────────────────────────────────────────────────────────────
function Bubble({ msg, isLatest }) {
  const isUser = msg.role === "user";
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setVisible(true), 30);
    return () => clearTimeout(t);
  }, []);

  return (
    <div style={{
      display:"flex",
      justifyContent: isUser ? "flex-end" : "flex-start",
      marginBottom:14,
      opacity: visible ? 1 : 0,
      transform: visible ? "translateY(0)" : "translateY(10px)",
      transition:"all .35s cubic-bezier(.4,0,.2,1)",
    }}>
      {!isUser && (
        <div style={{
          width:30, height:30, borderRadius:"50%", flexShrink:0, marginRight:8,
          background:"linear-gradient(135deg,#0a0f1a,#0d1420)",
          border:"1.5px solid #3b82f640",
          display:"flex", alignItems:"center", justifyContent:"center",
          fontSize:14,
          boxShadow:"0 0 12px #3b82f630",
        }}>🤖</div>
      )}

      <div style={{
        maxWidth:"78%",
        padding: isUser ? "9px 14px" : "11px 15px",
        borderRadius: isUser ? "18px 18px 4px 18px" : "18px 18px 18px 4px",
        background: isUser
          ? "linear-gradient(135deg, #1e3a5f, #1e40af)"
          : "linear-gradient(145deg, #0a0f1a, #0d1420)",
        color: isUser ? "#e0f2fe" : "#c8d8f0",
        fontSize:12.5,
        lineHeight:1.65,
        fontFamily:"'JetBrains Mono', monospace",
        fontWeight: isUser ? 500 : 400,
        border: isUser
          ? "1px solid #3b82f650"
          : "1px solid #1e3a5f80",
        boxShadow: isUser
          ? "0 4px 16px rgba(30,64,175,.25), inset 0 1px 0 rgba(255,255,255,.05)"
          : isLatest
            ? "0 4px 20px rgba(59,130,246,.15), 0 0 0 1px #3b82f620"
            : "0 2px 8px rgba(0,0,0,.3)",
        whiteSpace:"pre-wrap",
        wordBreak:"break-word",
        position:"relative",
        overflow:"hidden",
      }}>
        {/* Neon top border for AI messages */}
        {!isUser && (
          <div style={{
            position:"absolute", top:0, left:0, right:0, height:1,
            background:"linear-gradient(90deg, transparent, #3b82f660, #06b6d460, transparent)",
          }} />
        )}

        {/* Typing effect for latest AI message */}
        {!isUser && isLatest ? (
          <TypedText text={msg.content} />
        ) : (
          <span>{msg.content}</span>
        )}
      </div>

      {isUser && (
        <div style={{
          width:30, height:30, borderRadius:"50%", flexShrink:0, marginLeft:8,
          background:"linear-gradient(135deg,#1e3a5f,#1e40af)",
          border:"1.5px solid #3b82f660",
          display:"flex", alignItems:"center", justifyContent:"center",
          fontSize:12, fontWeight:800, color:"#93c5fd",
          boxShadow:"0 0 12px #3b82f640",
        }}>
          {(sessionStorage.getItem("mes_username")||"U")[0].toUpperCase()}
        </div>
      )}
    </div>
  );
}

// ── Typed Text Effect ─────────────────────────────────────────────────────────
function TypedText({ text }) {
  const [displayed, setDisplayed] = useState("");

  useEffect(() => {
    setDisplayed("");
    let i = 0;
    const iv = setInterval(() => {
      if (i >= text.length) { clearInterval(iv); return; }
      setDisplayed(text.slice(0, ++i));
    }, 12);
    return () => clearInterval(iv);
  }, [text]);

  return (
    <span>
      {displayed}
      {displayed.length < text.length && (
        <span style={{
          display:"inline-block", width:2, height:"1em",
          background:"#3b82f6", marginLeft:2, verticalAlign:"text-bottom",
          animation:"cursorBlink .7s step-end infinite",
          boxShadow:"0 0 6px #3b82f6",
        }} />
      )}
    </span>
  );
}

// ── Main AI Assistant ─────────────────────────────────────────────────────────
export default function AIAssistant({ pageContext = {} }) {
  const [messages, setMessages] = useState(() => {
  try {
    // sessionStorage clears on refresh, persists on page switch
    const s = sessionStorage.getItem(getStorageKey());
    if (s) { const p = JSON.parse(s); if (p?.length) return p; }
  } catch {}
  return [{
    role:"assistant",
    content:"⚡ Neural Core online.\n\nI have live access to your production database. Ask me about OEE, losses, NG parts, shift performance — anything.\n\nReady to analyze.",
    timestamp: Date.now(), id:"init",
  }];
  });

  const [open, setOpen]         = useState(false);
  const [visible, setVisible]   = useState(false);

  // Smooth open/close
  const openChat = () => { setOpen(true); setTimeout(() => setVisible(true), 10); };
  const closeChat = () => { setVisible(false); setTimeout(() => setOpen(false), 300); };
  const [input, setInput]     = useState("");
  const [thinking, setThink]  = useState(false);
  const [latestId, setLatest] = useState("init");
  const [error, setError]     = useState("");
  const [burst, setBurst]     = useState(false);
  const bottomRef  = useRef(null);
  const inputRef   = useRef(null);
  const panelRef   = useRef(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target)) {
        closeChat();
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);


  useEffect(() => {
    try { sessionStorage.setItem(getStorageKey(), JSON.stringify(messages)); } catch {}
  }, [messages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior:"smooth" });
  }, [messages, thinking]);

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 150);
  }, [open]);

  const send = async (text) => {
    const msg = (text || input).trim();
    if (!msg || thinking) return;
    setInput("");
    setError("");
    setBurst(true);
    setTimeout(() => setBurst(false), 600);

    const userMsg = { role:"user", content:msg, timestamp:Date.now(), id:Date.now()+"u" };
    setMessages(p => [...p, userMsg]);
    setThink(true);

    try {
      const res = await api.post("/api/ai/chat", {
        message: msg,
        context: pageContext,
        history: messages.slice(-10).map(m => ({ role:m.role, content:m.content })),
      });
      const id = Date.now()+"a";
      const aiMsg = { role:"assistant", content: res.data?.reply || "Got it.", timestamp:Date.now(), id };
      setLatest(id);
      setMessages(p => [...p, aiMsg]);
    } catch (e) {
      const id = Date.now()+"e";
      setLatest(id);
      setMessages(p => [...p, {
        role:"assistant",
        content:"⚠ Connection error. Check backend.",
        timestamp:Date.now(), id,
      }]);
      setError(e.message);
    } finally {
      setThink(false);
    }
  };

  const clear = () => {
    const id = Date.now()+"c";
    setMessages([{ role:"assistant", content:"⚡ Chat reset. Ready.", timestamp:Date.now(), id }]);
    setLatest(id);
    setError("");
  };

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700;800&family=Rajdhani:wght@600;700;800&display=swap');
        @keyframes neonPulse   { 0%,100%{text-shadow:0 0 8px #3b82f6,0 0 20px #3b82f6} 50%{text-shadow:0 0 16px #06b6d4,0 0 40px #06b6d4} }
        @keyframes dotBounce   { 0%,100%{transform:translateY(0);opacity:.5} 50%{transform:translateY(-5px);opacity:1} }
        @keyframes cursorBlink { 0%,100%{opacity:1} 50%{opacity:0} }
        @keyframes floatBtn    { 0%,100%{transform:translateY(0) rotate(0deg)} 50%{transform:translateY(-4px) rotate(3deg)} }
        @keyframes burstPulse  { 0%{transform:scale(1)} 30%{transform:scale(1.15)} 100%{transform:scale(1)} }
        @keyframes panelIn     { from{opacity:0;transform:translateY(20px) scale(.96)} to{opacity:1;transform:translateY(0) scale(1)} }
        @keyframes scanline    { 0%{transform:translateY(-100%)} 100%{transform:translateY(400px)} }
        @keyframes cornerGlow  { 0%,100%{opacity:.4} 50%{opacity:1} }
        .ai-scroll::-webkit-scrollbar { width:3px; }
        .ai-scroll::-webkit-scrollbar-thumb { background:#1e3a5f; border-radius:2px; }
        .qbtn:hover { background:#0d1a2e !important; border-color:#3b82f6 !important; color:#60a5fa !important; transform:translateY(-2px); box-shadow:0 4px 12px #3b82f630 !important; }
      `}</style>

      {/* ── Floating Button ── */}
      {!open && (
        <button onClick={() => openChat()} style={{
          position:"fixed", bottom:24, right:24, zIndex:10000,
          width:56, height:56, borderRadius:16,
          background:"linear-gradient(135deg,#060912,#0a0f1a)",
          border:"1.5px solid #3b82f650",
          cursor:"pointer", outline:"none",
          display:"flex", alignItems:"center", justifyContent:"center",
          boxShadow:"0 8px 24px rgba(59,130,246,.25), 0 0 0 1px #3b82f620, inset 0 1px 0 rgba(255,255,255,.05)",
          animation:"floatBtn 3s ease-in-out infinite",
          transition:"box-shadow .2s",
        }}
          onMouseEnter={e => e.currentTarget.style.boxShadow="0 12px 32px rgba(59,130,246,.4), 0 0 0 1px #3b82f640"}
          onMouseLeave={e => e.currentTarget.style.boxShadow="0 8px 24px rgba(59,130,246,.25), 0 0 0 1px #3b82f620"}
        >
          {/* Corner accents */}
          {["tl","tr","bl","br"].map(c => (
            <div key={c} style={{
              position:"absolute",
              top:    c.includes("t") ? 4 : "auto",
              bottom: c.includes("b") ? 4 : "auto",
              left:   c.includes("l") ? 4 : "auto",
              right:  c.includes("r") ? 4 : "auto",
              width:6, height:6,
              borderTop:    c.includes("t") ? "1.5px solid #3b82f6" : "none",
              borderBottom: c.includes("b") ? "1.5px solid #3b82f6" : "none",
              borderLeft:   c.includes("l") ? "1.5px solid #3b82f6" : "none",
              borderRight:  c.includes("r") ? "1.5px solid #3b82f6" : "none",
              animation:"cornerGlow 2s ease infinite",
              animationDelay: `${["tl","tr","bl","br"].indexOf(c)*.25}s`,
            }} />
          ))}
          <span style={{ fontSize:24, filter:"drop-shadow(0 0 8px #3b82f6)" }}>🤖</span>
          {/* Live dot */}
          <div style={{
            position:"absolute", top:6, right:6,
            width:8, height:8, borderRadius:"50%",
            background:"#00ff88", border:"1.5px solid #060912",
            boxShadow:"0 0 8px #00ff88",
            animation:"dotBounce 1.5s ease infinite",
          }} />
        </button>
      )}

      {/* ── Chat Panel ── */}
      {open && (
        <div ref={panelRef} style={{
          position:"fixed", bottom:24, right:24, zIndex:10000,
          width:400, height:560,
          background:"linear-gradient(145deg,#060912 0%,#080e1a 50%,#060912 100%)",
          borderRadius:20,
          border:"1px solid #1e3a5f80",
          boxShadow:"0 24px 64px rgba(0,0,0,.6), 0 0 0 1px #3b82f620, inset 0 1px 0 rgba(59,130,246,.1)",
          display:"flex", flexDirection:"column", overflow:"hidden",
          fontFamily:"'JetBrains Mono', monospace",
          opacity: visible ? 1 : 0,
          transform: visible ? "translateY(0) scale(1)" : "translateY(20px) scale(0.95)",
          transition: "opacity 0.3s cubic-bezier(.4,0,.2,1), transform 0.3s cubic-bezier(.4,0,.2,1)",        }}>

          {/* Particle background */}
          <div style={{ position:"absolute", inset:0, borderRadius:20, overflow:"hidden" }}>
            <ParticleNet thinking={thinking} />
          </div>

          {/* Scanline effect */}
          {thinking && (
            <div style={{
              position:"absolute", inset:0, zIndex:1, pointerEvents:"none", overflow:"hidden", borderRadius:20,
            }}>
              <div style={{
                position:"absolute", left:0, right:0, height:2,
                background:"linear-gradient(90deg,transparent,#3b82f630,#06b6d430,transparent)",
                animation:"scanline 1.5s linear infinite",
              }} />
            </div>
          )}

          {/* ── Header ── */}
          <div style={{
            position:"relative", zIndex:2,
            padding:"14px 18px",
            borderBottom:"1px solid #1e3a5f60",
            background:"linear-gradient(135deg,rgba(6,9,18,.95),rgba(10,15,26,.95))",
            backdropFilter:"blur(10px)",
          }}>
            {/* Top accent line */}
            <div style={{
              position:"absolute", top:0, left:0, right:0, height:2,
              background:"linear-gradient(90deg,transparent,#3b82f6,#06b6d4,#8b5cf6,transparent)",
            }} />

            <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between" }}>
              <div style={{ display:"flex", alignItems:"center", gap:12 }}>
                {/* Logo */}
                <div style={{
                  width:40, height:40, borderRadius:12,
                  background:"linear-gradient(135deg,#0a1628,#0d1f3c)",
                  border:"1.5px solid #3b82f640",
                  display:"flex", alignItems:"center", justifyContent:"center",
                  fontSize:20, position:"relative",
                  boxShadow:"0 0 20px #3b82f630",
                }}>
                  <span style={{ filter:"drop-shadow(0 0 6px #3b82f6)" }}>🤖</span>
                  <div style={{
                    position:"absolute", bottom:-2, right:-2,
                    width:10, height:10, borderRadius:"50%",
                    background: thinking ? "#f59e0b" : "#00ff88",
                    border:"2px solid #060912",
                    boxShadow:`0 0 8px ${thinking?"#f59e0b":"#00ff88"}`,
                    transition:"all .3s",
                  }} />
                </div>

                <div>
                  <div style={{
                    fontSize:14, fontWeight:800, color:"#e0f2fe",
                    fontFamily:"'Rajdhani', sans-serif", letterSpacing:".06em",
                    textShadow:"0 0 12px #3b82f640",
                  }}>NEURAL CORE</div>
                  <div style={{
                    fontSize:9, marginTop:2,
                    color: thinking ? "#f59e0b" : "#00ff88",
                    letterSpacing:".12em", fontWeight:700,
                    textShadow:`0 0 8px ${thinking?"#f59e0b":"#00ff88"}`,
                    animation:"neonPulse 2s ease infinite",
                  }}>
                    {thinking ? "◈ ANALYZING..." : "◈ LIVE · READY"}
                  </div>
                </div>
              </div>

              <div style={{ display:"flex", gap:6 }}>
                {[
                  { icon:"🗑", title:"Clear", action:clear },
                  { icon:"✕", title:"Close", action:()=>closeChat() },
                ].map(b => (
                  <button key={b.title} title={b.title} onClick={b.action} style={{
                    width:30, height:30, borderRadius:8,
                    background:"transparent", border:"1px solid #1e3a5f",
                    cursor:"pointer", color:"#4b6a9b", fontSize:13,
                    display:"flex", alignItems:"center", justifyContent:"center",
                    transition:"all .2s",
                  }}
                    onMouseEnter={e => { e.currentTarget.style.background="#0d1420"; e.currentTarget.style.borderColor="#3b82f640"; e.currentTarget.style.color="#60a5fa"; }}
                    onMouseLeave={e => { e.currentTarget.style.background="transparent"; e.currentTarget.style.borderColor="#1e3a5f"; e.currentTarget.style.color="#4b6a9b"; }}
                  >{b.icon}</button>
                ))}
              </div>
            </div>
          </div>

          {/* ── Messages ── */}
          <div className="ai-scroll" style={{
            flex:1, overflowY:"auto", padding:"14px 16px",
            position:"relative", zIndex:2,
          }}>
            {messages.map(m => (
              <Bubble key={m.id||m.timestamp} msg={m} isLatest={m.id===latestId} />
            ))}

            {/* Rocket loader */}
            {thinking && <RocketThinking />}

            {error && (
              <div style={{
                margin:"8px 0", padding:"8px 12px", borderRadius:10,
                background:"#1a0a0a", border:"1px solid #ef444430",
                fontSize:11, color:"#f87171",
              }}>⚠ {error}</div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* ── Quick Prompts ── */}
          {messages.length <= 2 && (
            <div style={{
              position:"relative", zIndex:2,
              padding:"8px 16px 10px",
              borderTop:"1px solid #1e3a5f40",
              background:"rgba(6,9,18,.8)",
            }}>
              <div style={{ fontSize:8, fontWeight:800, color:"#1e3a5f", letterSpacing:".15em", marginBottom:8 }}>
                QUICK COMMANDS
              </div>
              <div style={{ display:"flex", flexWrap:"wrap", gap:5 }}>
                {QUICK_PROMPTS.map(q => (
                  <button key={q.text} className="qbtn" onClick={() => send(q.text)} style={{
                    background:"#0a0f1a", border:"1px solid #1e3a5f",
                    borderRadius:99, padding:"4px 10px",
                    fontSize:10, fontWeight:600, color:"#4b6a9b",
                    cursor:"pointer", transition:"all .2s",
                    fontFamily:"'JetBrains Mono', monospace",
                  }}>
                    {q.icon} {q.text}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* ── Input ── */}
          <div style={{
            position:"relative", zIndex:2,
            padding:"12px 16px 14px",
            borderTop:"1px solid #1e3a5f60",
            background:"rgba(6,9,18,.95)",
          }}>
            <div style={{ display:"flex", gap:8, alignItems:"flex-end" }}>
              <div style={{ flex:1, position:"relative" }}>
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => { if(e.key==="Enter"&&!e.shiftKey){ e.preventDefault(); send(); } }}
                  placeholder="Query production data..."
                  rows={1}
                  style={{
                    width:"100%", resize:"none",
                    padding:"10px 14px",
                    background:"#0a0f1a",
                    border:"1px solid #1e3a5f",
                    borderRadius:12,
                    fontSize:12, color:"#c8d8f0",
                    fontFamily:"'JetBrains Mono', monospace",
                    outline:"none", lineHeight:1.5,
                    boxSizing:"border-box",
                    transition:"all .2s",
                  }}
                  onFocus={e => {
                    e.target.style.borderColor="#3b82f6";
                    e.target.style.boxShadow="0 0 0 2px #3b82f620, 0 0 12px #3b82f620";
                  }}
                  onBlur={e => {
                    e.target.style.borderColor="#1e3a5f";
                    e.target.style.boxShadow="none";
                  }}
                />
              </div>

              {/* Send button */}
              <button
                onClick={() => send()}
                disabled={thinking || !input.trim()}
                style={{
                  width:40, height:40, borderRadius:12, flexShrink:0,
                  background: thinking||!input.trim()
                    ? "#0a0f1a"
                    : "linear-gradient(135deg,#1e3a5f,#1e40af)",
                  border:`1px solid ${thinking||!input.trim()?"#1e3a5f":"#3b82f660"}`,
                  cursor: thinking||!input.trim() ? "not-allowed" : "pointer",
                  display:"flex", alignItems:"center", justifyContent:"center",
                  fontSize:16, color: thinking||!input.trim() ? "#1e3a5f" : "#93c5fd",
                  transition:"all .2s",
                  boxShadow: !thinking&&input.trim() ? "0 4px 16px #3b82f630" : "none",
                  animation: burst ? "burstPulse .5s ease" : "none",
                }}
                onMouseEnter={e => { if(!thinking&&input.trim()) e.currentTarget.style.boxShadow="0 6px 20px #3b82f650"; }}
                onMouseLeave={e => { e.currentTarget.style.boxShadow=!thinking&&input.trim()?"0 4px 16px #3b82f630":"none"; }}
              >
                {thinking
                  ? <div style={{ width:14,height:14,borderRadius:"50%",border:"2px solid #1e3a5f",borderTopColor:"#3b82f6",animation:"spin .6s linear infinite" }}/>
                  : "↑"
                }
              </button>
            </div>

            <div style={{
              display:"flex", justifyContent:"center", gap:12,
              marginTop:8, fontSize:9, color:"#1e3a5f",
            }}>
              <span>⏎ Send</span>
              <span>⇧+⏎ New line</span>
              <span style={{ color:"#00ff8850" }}>● Encrypted</span>
            </div>
          </div>

          {/* Corner accents on panel */}
          {["tl","tr","bl","br"].map(c => (
            <div key={c} style={{
              position:"absolute", zIndex:3,
              top:    c.includes("t") ? 8 : "auto",
              bottom: c.includes("b") ? 8 : "auto",
              left:   c.includes("l") ? 8 : "auto",
              right:  c.includes("r") ? 8 : "auto",
              width:10, height:10,
              borderTop:    c.includes("t") ? "1.5px solid #3b82f660" : "none",
              borderBottom: c.includes("b") ? "1.5px solid #3b82f660" : "none",
              borderLeft:   c.includes("l") ? "1.5px solid #3b82f660" : "none",
              borderRight:  c.includes("r") ? "1.5px solid #3b82f660" : "none",
              pointerEvents:"none",
            }} />
          ))}
        </div>
      )}

      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
    </>
  );
}