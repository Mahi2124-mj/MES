import { useState, useEffect } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import AIAssistant from "../components/AIAssistant";

function PasswordStrength({ password }) {
  const checks = [
    { label: "8+ characters",     pass: password.length >= 8 },
    { label: "Uppercase letter",  pass: /[A-Z]/.test(password) },
    { label: "Lowercase letter",  pass: /[a-z]/.test(password) },
    { label: "Number",            pass: /[0-9]/.test(password) },
    { label: "Special character", pass: /[^A-Za-z0-9]/.test(password) },
  ];
  const score = checks.filter(c => c.pass).length;
  const levels = [
    { label: "Very Weak",   color: "#ef4444" },
    { label: "Weak",        color: "#f97316" },
    { label: "Fair",        color: "#eab308" },
    { label: "Strong",      color: "#84cc16" },
    { label: "Very Strong", color: "#16a34a" },
  ];
  const level = levels[Math.max(0, score - 1)] || levels[0];
  if (!password) return null;
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
        {levels.map((l, i) => (
          <div key={i} style={{
            flex: 1, height: 4, borderRadius: 2,
            background: i < score ? level.color : "#e2e8f0",
            transition: "background 0.3s",
          }} />
        ))}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10 }}>
        <span style={{ fontSize: 11, color: level.color, fontWeight: 700 }}>{level.label}</span>
        <span style={{ fontSize: 11, color: "#94a3b8" }}>{score}/5 criteria met</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4 }}>
        {checks.map(c => (
          <div key={c.label} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: 12, color: c.pass ? "#16a34a" : "#cbd5e1", fontWeight: 700 }}>
              {c.pass ? "✓" : "○"}
            </span>
            <span style={{ fontSize: 11, color: c.pass ? "#334155" : "#94a3b8" }}>{c.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const inputStyle = (focused) => ({
  width: "100%", padding: "10px 14px",
  background: "#f8fafc",
  border: `1.5px solid ${focused ? "#3b82f6" : "#e2e8f0"}`,
  borderRadius: 9, color: "#0f172a",
  fontSize: 13, outline: "none",
  fontFamily: "'Barlow', sans-serif",
  boxSizing: "border-box",
  transition: "border-color 0.15s, box-shadow 0.15s",
  boxShadow: focused ? "0 0 0 3px rgba(59,130,246,0.1)" : "none",
});

function Field({ label, type = "text", value, onChange, placeholder }) {
  const [focused, setFocused] = useState(false);
  return (
    <div style={{ marginBottom: 18 }}>
      <label style={{
        display: "block", fontSize: 10, fontWeight: 700,
        letterSpacing: "0.1em", textTransform: "uppercase",
        color: "#64748b", marginBottom: 7,
      }}>{label}</label>
      <input
        type={type} value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        style={inputStyle(focused)}
      />
    </div>
  );
}

function Toast({ msg, type, onClose }) {
  if (!msg) return null;
  const color = type === "ok" ? "#16a34a" : "#dc2626";
  const bg    = type === "ok" ? "rgba(22,163,74,0.06)" : "rgba(220,38,38,0.06)";
  const border= type === "ok" ? "rgba(22,163,74,0.25)" : "rgba(220,38,38,0.25)";
  return (
    <div style={{
      position: "fixed", bottom: 24, right: 24, zIndex: 999,
      background: "#fff", border: `1px solid ${border}`,
      borderLeft: `4px solid ${color}`,
      borderRadius: 10, padding: "12px 18px",
      display: "flex", alignItems: "center", gap: 12,
      boxShadow: "0 8px 30px rgba(0,0,0,0.12)",
      minWidth: 280, maxWidth: 340,
      animation: "slideUp 0.2s ease",
    }}>
      <span style={{ fontSize: 13, fontWeight: 600, color, flex: 1 }}>{msg}</span>
      <button onClick={onClose} style={{ background: "none", border: "none", color: "#94a3b8", cursor: "pointer", fontSize: 16 }}>✕</button>
      <style>{`@keyframes slideUp { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:none} }`}</style>
    </div>
  );
}

export default function Settings() {
  
  const { token, user, theme } = useAuth();
  const username = user?.username || "User";
  const role     = user?.role     || "operator";
  const userId   = user?.id       || "";

  const [cur,     setCur]     = useState("");
  const [nw,      setNw]      = useState("");
  const [con,     setCon]     = useState("");
  const [loading, setLoading] = useState(false);
  const [toast,   setToast]   = useState({ msg: "", type: "" });
  useEffect(() => { document.title = "Settings"; }, []);
  
  

  function showToast(msg, type = "ok") {
    setToast({ msg, type });
    setTimeout(() => setToast({ msg: "", type: "" }), 4000);
  }

  async function handleChangePassword() {
    if (!cur || !nw || !con) return showToast("All fields are required", "err");
    if (nw !== con)           return showToast("New passwords do not match", "err");
    if (nw.length < 6)        return showToast("Minimum 6 characters required", "err");
    setLoading(true);
    try {
      await api.post("/api/auth/change-password", { current_password: cur, new_password: nw }, token);
      showToast("Password changed successfully ✓");
      setCur(""); setNw(""); setCon("");
    } catch (e) {
      showToast(e.message || "Failed to change password", "err");
    } finally {
      setLoading(false);
    }
  }

  const ROLE_COLORS = {
    admin:    { bg: "rgba(30,64,175,0.08)",  border: "rgba(30,64,175,0.2)",  text: "#1e40af" },
    zone:     { bg: "rgba(22,163,74,0.08)",  border: "rgba(22,163,74,0.2)",  text: "#16a34a" },
    operator: { bg: "rgba(217,119,6,0.08)",  border: "rgba(217,119,6,0.2)",  text: "#d97706" },
  };
  const rc = ROLE_COLORS[role] || ROLE_COLORS.operator;
  

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700;800&family=Barlow+Condensed:wght@700;800&display=swap');
        * { box-sizing: border-box; }
      `}</style>

      <div style={{
        minHeight: "100vh",
        background: "#f8fafc",
        fontFamily: "'Barlow', sans-serif",
        paddingBottom: 60,
      }}>

        {/* Topbar */}
        <div style={{
          background: "#fff", borderBottom: "1px solid #e2e8f0",
          padding: "0 40px 0 88px", height: 60,
          display: "flex", alignItems: "center",
          position: "sticky", top: 0, zIndex: 100,
          boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
          position: "sticky",
        }}>
          <div style={{ fontFamily: "'Barlow Condensed',sans-serif", fontSize: 22, fontWeight: 800, color: "#0f172a" }} />
          {/* Blue underline */}
          <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: 2, background: theme.gradient }} />
          <div style={{
            position:"absolute", left:"50%", transform:"translateX(-50%)",
            fontFamily:"'Barlow Condensed',sans-serif",
            fontSize:37, fontWeight:800, color:"#0f172a", letterSpacing:"-.01em",
            pointerEvents:"none",
            }}>
            Account <span style={{ color: theme.accent }}>Settings</span>
          </div>
        </div>

        <div style={{ padding: "36px 40px 0", maxWidth: 900, margin: "0 auto" }}>
          {/* Heading */}
          <div style={{ textAlign: "center", marginBottom: 36 }}>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", gap: 20, alignItems: "start" }}>

            {/* LEFT — Profile */}
            <div style={{
              background: "#fff", border: "1px solid #e2e8f0",
              borderRadius: 14, overflow: "hidden",
              boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
            }}>
              {/* Themed header strip */}
              <div style={{
                background: `linear-gradient(135deg, ${theme.accentDark}, ${theme.accent})`,
                padding: "28px 24px", textAlign: "center",
              }}>
                <div style={{
                  width: 72, height: 72, borderRadius: "50%",
                  background: "rgba(255,255,255,0.2)",
                  border: "2px solid rgba(255,255,255,0.3)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 28, fontWeight: 800, color: "#fff",
                  margin: "0 auto 12px",
                }}>
                  {(username[0] || "U").toUpperCase()}
                </div>
                <div style={{ fontSize: 20, fontWeight: 700, color: "#fff" }}>{username}</div>
                <div style={{
                  display: "inline-flex", marginTop: 8,
                  background: "rgba(255,255,255,0.15)",
                  border: "1px solid rgba(255,255,255,0.25)",
                  borderRadius: 99, padding: "3px 14px",
                  fontSize: 11, fontWeight: 700, color: "#fff",
                  letterSpacing: "0.1em", textTransform: "uppercase",
                }}>
                  {role}
                </div>
              </div>

              {/* Info rows */}
              <div style={{ padding: "8px 0" }}>
                {[
                  { label: "Username", value: username },
                  { label: "Role",     value: role.toUpperCase() },
                  { label: "User ID",  value: `#${userId}` },
                  { label: "Platform", value: "v2.0" },
                  { label: "Location", value: "Bawal, Haryana" },
                ].map(r => (
                  <div key={r.label} style={{
                    display: "flex", justifyContent: "space-between", alignItems: "center",
                    padding: "11px 20px", borderBottom: "1px solid #f1f5f9",
                  }}>
                    <span style={{ fontSize: 12, color: "#94a3b8", fontWeight: 500 }}>{r.label}</span>
                    <span style={{ fontSize: 12, fontWeight: 700, color: "#334155", fontFamily: "monospace" }}>{r.value}</span>
                  </div>
                ))}
              </div>

              {/* Audit notice */}
              <div style={{
                margin: "12px 16px 16px",
                background: "rgba(217,119,6,0.06)",
                border: "1px solid rgba(217,119,6,0.2)",
                borderRadius: 10, padding: "12px 14px",
                display: "flex", gap: 10,
              }}>
                <span style={{ fontSize: 15, flexShrink: 0 }}>🔔</span>
                <div>
                  <div style={{ fontSize: 11, fontWeight: 700, color: "#d97706", marginBottom: 3 }}>Admin Notification</div>
                  <div style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.5 }}>
                    Password changes are logged in the Audit Log and visible to administrators.
                  </div>
                </div>
              </div>
            </div>

            {/* RIGHT — Change Password */}
            <div style={{
              background: "#fff", border: "1px solid #e2e8f0",
              borderRadius: 14, padding: 28,
              boxShadow: "0 1px 3px rgba(0,0,0,0.05)",
            }}>
              {/* Section title */}
              <div style={{
                display: "flex", alignItems: "center", gap: 10,
                marginBottom: 24, paddingBottom: 16,
                borderBottom: "1px solid #f1f5f9",
              }}>
                <span style={{ fontSize: 18 }}>🔐</span>
                <div>
                  <div style={{ fontSize: 15, fontWeight: 700, color: "#0f172a" }}>Change Password</div>
                  <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 2 }}>Update your account password</div>
                </div>
              </div>

              <Field label="Current Password"  type="password" value={cur} onChange={setCur} placeholder="Enter current password" />
              <Field label="New Password"       type="password" value={nw}  onChange={setNw}  placeholder="Enter new password" />
              <PasswordStrength password={nw} />
              <Field label="Confirm New Password" type="password" value={con} onChange={setCon} placeholder="Re-enter new password" />

              {/* Match indicator */}
              {nw && con && (
                <div style={{
                  marginBottom: 18, padding: "10px 14px", borderRadius: 9,
                  background: nw === con ? "rgba(22,163,74,0.06)" : "rgba(220,38,38,0.06)",
                  border: `1px solid ${nw === con ? "rgba(22,163,74,0.25)" : "rgba(220,38,38,0.25)"}`,
                  fontSize: 12, fontWeight: 600,
                  color: nw === con ? "#16a34a" : "#dc2626",
                  display: "flex", alignItems: "center", gap: 8,
                }}>
                  {nw === con ? "✓ Passwords match" : "✗ Passwords do not match"}
                </div>
              )}

              {/* Submit button */}
              <button
                onClick={handleChangePassword}
                disabled={loading || !cur || !nw || !con || nw !== con}
                style={{
                  width: "100%", padding: "13px",
                  background: (loading || !cur || !nw || !con || nw !== con)
                    ? "#f1f5f9"
                    : `linear-gradient(135deg, ${theme.accentDark}, ${theme.accent})`,
                  color: (loading || !cur || !nw || !con || nw !== con) ? "#94a3b8" : "#fff",
                  border: "none", borderRadius: 10,
                  fontSize: 14, fontWeight: 700,
                  cursor: (loading || !cur || !nw || !con || nw !== con) ? "not-allowed" : "pointer",
                  letterSpacing: "0.05em",
                  fontFamily: "'Barlow', sans-serif",
                  transition: "all 0.15s",
                  boxShadow: (!loading && cur && nw && con && nw === con)
                    ? "0 4px 16px rgba(30,64,175,0.3)" : "none",
                }}
              >
                {loading ? "Updating…" : "Update Password →"}
              </button>

              {/* Security tips */}
              <div style={{
                marginTop: 24, padding: "16px 18px",
                background: "#f8fafc", border: "1px solid #f1f5f9",
                borderRadius: 12,
              }}>
                <div style={{
                  fontSize: 10, fontWeight: 700, letterSpacing: "0.12em",
                  textTransform: "uppercase", color: "#64748b", marginBottom: 12,
                }}>
                  🛡 Security Tips
                </div>
                {[
                  "Never share your password with anyone",
                  "Use a password unique to this system",
                  "Change your password regularly",
                  "Log out when using shared computers",
                ].map(tip => (
                  <div key={tip} style={{
                    display: "flex", alignItems: "flex-start", gap: 8,
                    marginBottom: 8, fontSize: 12, color: "#64748b",
                  }}>
                    <span style={{ color: "#3b82f6", flexShrink: 0, fontWeight: 700 }}>›</span>
                    {tip}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      <Toast msg={toast.msg} type={toast.type} onClose={() => setToast({ msg: "", type: "" })} />
        <AIAssistant pageContext={{ page: "Settings" }} />
    </>
  );
  
}