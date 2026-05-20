import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import AIAssistant from "../components/AIAssistant";

export default function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError]       = useState("");
  const [loading, setLoading]   = useState(false);
  const [showPass, setShowPass] = useState(false);
  const [mounted, setMounted]   = useState(false);

  const { login, token } = useAuth();
  const navigate  = useNavigate();
  useEffect(() => { document.title = "Login"; }, []);

  // Every user — admin, plant_head, department, production, operator —
  // ALWAYS lands on /dashboard after sign-in.  The Dashboard route is a
  // switch (DashboardForUser in App.jsx): Maintenance dept user → their
  // MaintenanceDashboard, everyone else → the Production Dashboard.
  // We deliberately ignore `location.state.from`: if user A logs out
  // while on /maintenance-capa and user B then signs in, B should NOT
  // be sent to A's last page.  Each role lands on its own home.
  // (Browser-refresh-on-current-page is unaffected — that's URL-based,
  // not login-flow, and stays where it is as long as the user has access.)

  useEffect(() => {
    if (token) navigate("/dashboard", { replace: true });
  }, [token]);

  useEffect(() => {
    const t = setTimeout(() => setMounted(true), 50);
    return () => clearTimeout(t);
  }, []);

  const handleSubmit = async (e) => {
    e?.preventDefault();
    if (!username.trim() || !password) { setError("Enter username and password."); return; }
    setError("");
    setLoading(true);
    try {
      await login(username.trim(), password);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      if (err.message === "Failed to fetch" || err.message.includes("fetch")) {
        setError("Cannot reach server. Check your network or VPN connection.");
      } else {
        setError(err.message || "Invalid credentials. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@300;400;500;600;700;800&family=Barlow+Condensed:wght@600;700;800&display=swap');

        .login-root {
          height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          background: #060d1a;
          font-family: 'Barlow', sans-serif;
          overflow: hidden;
          position: relative;
        }

        .login-root::before {
          content: '';
          position: absolute;
          inset: 0;
          background-image:
            linear-gradient(rgba(30,64,175,0.07) 1px, transparent 1px),
            linear-gradient(90deg, rgba(30,64,175,0.07) 1px, transparent 1px);
          background-size: 48px 48px;
          mask-image: radial-gradient(ellipse 80% 80% at 50% 50%, black 40%, transparent 100%);
        }

        .login-root::after {
          content: '';
          position: absolute;
          width: 600px; height: 600px;
          border-radius: 50%;
          background: radial-gradient(circle, rgba(30,64,175,0.18) 0%, transparent 70%);
          top: 50%; left: 38%;
          transform: translate(-50%, -50%);
          pointer-events: none;
        }

        .login-card *, .login-card *::before, .login-card *::after {
          box-sizing: border-box;
        }

        .login-card {
          position: relative; z-index: 1;
          display: flex;
          width: 860px; max-width: 96vw;
          border-radius: 20px;
          overflow: hidden;
          box-shadow:
            0 0 0 1px rgba(255,255,255,0.06),
            0 40px 80px rgba(0,0,0,0.6),
            0 0 60px rgba(30,64,175,0.12);
        }

        .login-left {
          width: 360px; flex-shrink: 0;
          background: linear-gradient(160deg, #0d1b3e 0%, #060d1a 100%);
          padding: 48px 44px;
          display: flex; flex-direction: column;
          position: relative; overflow: hidden;
        }

        .login-left::before {
          content: '';
          position: absolute;
          top: 0; right: 0;
          width: 180px; height: 180px;
          background:
            linear-gradient(rgba(30,64,175,0.3) 1px, transparent 1px),
            linear-gradient(90deg, rgba(30,64,175,0.3) 1px, transparent 1px);
          background-size: 24px 24px;
          mask-image: radial-gradient(circle at top right, black, transparent 70%);
        }

        .login-left::after {
          content: '';
          position: absolute;
          bottom: 0; left: 0;
          width: 180px; height: 180px;
          background:
            linear-gradient(rgba(30,64,175,0.2) 1px, transparent 1px),
            linear-gradient(90deg, rgba(30,64,175,0.2) 1px, transparent 1px);
          background-size: 24px 24px;
          mask-image: radial-gradient(circle at bottom left, black, transparent 70%);
        }

        .login-logo-row {
          display: flex; align-items: center; gap: 14px;
          margin-bottom: auto;
          position: relative; z-index: 1;
        }

        .login-logo-circle {
          width: 52px; height: 52px;
          border-radius: 14px;
          background: #ffffff;
          border: 1px solid rgba(255,255,255,0.2);
          overflow: hidden;
          display: flex; align-items: center; justify-content: center;
          box-shadow: 0 4px 20px rgba(0,0,0,0.3);
          flex-shrink: 0;
        }

        .login-logo-circle img {
          width: 90%; height: 90%;
          object-fit: contain;
        }

        .login-brand-text b {
          display: block;
          font-size: 13px; font-weight: 700;
          color: #fff; letter-spacing: 0.01em;
        }

        .login-brand-text span {
          font-size: 11px; color: rgba(255,255,255,0.45);
          line-height: 1.5;
        }

        .login-hero {
          position: relative; z-index: 1;
          margin-top: auto;
        }

        .login-mes-label {
          font-size: 10px; font-weight: 700;
          letter-spacing: 0.25em; text-transform: uppercase;
          color: #3b82f6;
          margin-bottom: 16px;
          display: flex; align-items: center; gap: 8px;
        }

        .login-mes-label::before {
          content: '';
          display: inline-block;
          width: 20px; height: 2px;
          background: #3b82f6;
        }

        .login-headline {
          font-family: 'Barlow Condensed', sans-serif;
          font-size: 46px; font-weight: 800;
          color: #fff;
          line-height: 1;
          letter-spacing: -0.01em;
          margin-bottom: 4px;
        }

        .login-headline span { color: #3b82f6; }

        .login-sub-headline {
          font-family: 'Barlow Condensed', sans-serif;
          font-size: 46px; font-weight: 300;
          color: rgba(255,255,255,0.3);
          line-height: 1;
          margin-bottom: 24px;
        }

        .login-desc {
          font-size: 12px;
          color: rgba(255,255,255,0.38);
          line-height: 1.8;
          max-width: 240px;
        }

        .login-stats {
          display: flex; gap: 10px;
          margin-top: 28px;
        }

        .login-stat-pill {
          background: rgba(255,255,255,0.04);
          border: 1px solid rgba(255,255,255,0.09);
          border-radius: 8px;
          padding: 10px 14px;
        }

        .login-stat-pill .val {
          font-family: 'Barlow Condensed', sans-serif;
          font-size: 20px; font-weight: 700;
          color: #fff;
        }

        .login-stat-pill .lbl {
          font-size: 9px; font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.1em;
          color: rgba(255,255,255,0.3);
          margin-top: 2px;
        }

        .login-right {
          flex: 1;
          min-width: 0;
          background: #0f1829;
          padding: 52px 48px;
          box-sizing: border-box;
          display: flex; flex-direction: column; justify-content: center;
          border-left: 1px solid rgba(255,255,255,0.05);
          overflow: hidden;
        }

        .login-form-title {
          font-family: 'Barlow Condensed', sans-serif;
          font-size: 32px; font-weight: 700;
          color: #fff;
          letter-spacing: 0.01em;
          margin-bottom: 4px;
        }

        .login-form-sub {
          font-size: 13px; color: rgba(255,255,255,0.32);
          margin-bottom: 40px;
        }

        .login-field {
          display: flex; flex-direction: column; gap: 7px;
          margin-bottom: 20px;
        }

        .login-field label {
          font-size: 10px; font-weight: 700;
          letter-spacing: 0.14em; text-transform: uppercase;
          color: rgba(255,255,255,0.38);
        }

        .login-input-wrap { position: relative; width: 100%; box-sizing: border-box; }

        .login-input {
          width: 100%;
          box-sizing: border-box;
          background: rgba(255,255,255,0.04);
          border: 1.5px solid rgba(255,255,255,0.09);
          border-radius: 10px;
          padding: 13px 44px 13px 16px;
          color: #fff;
          font-family: 'Barlow', sans-serif;
          font-size: 14px;
          outline: none;
          transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
        }

        .login-input::placeholder { color: rgba(255,255,255,0.18); }

        .login-input:focus {
          border-color: #3b82f6;
          background: rgba(59,130,246,0.06);
          box-shadow: 0 0 0 4px rgba(59,130,246,0.12);
        }

        .login-input-icon {
          position: absolute;
          right: 14px; top: 50%;
          transform: translateY(-50%);
          color: rgba(255,255,255,0.22);
          font-size: 15px;
          cursor: pointer;
          user-select: none;
          transition: color 0.12s;
        }

        .login-input-icon:hover { color: rgba(255,255,255,0.55); }

        .login-btn {
          width: 100%;
          box-sizing: border-box;
          padding: 14px;
          margin-top: 8px;
          background: linear-gradient(135deg, #1e40af, #2563eb);
          border: none;
          border-radius: 10px;
          color: #fff;
          font-family: 'Barlow', sans-serif;
          font-size: 13px; font-weight: 700;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          cursor: pointer;
          transition: all 0.15s;
          box-shadow: 0 4px 20px rgba(37,99,235,0.35);
          display: flex; align-items: center; justify-content: center; gap: 8px;
        }

        .login-btn:hover:not(:disabled) {
          transform: translateY(-1px);
          box-shadow: 0 8px 28px rgba(37,99,235,0.5);
          filter: brightness(1.1);
        }

        .login-btn:active:not(:disabled) { transform: translateY(0); }
        .login-btn:disabled { opacity: 0.55; cursor: not-allowed; }

        .login-spinner {
          width: 15px; height: 15px;
          border-radius: 50%;
          border: 2px solid rgba(255,255,255,0.3);
          border-top-color: #fff;
          animation: lspin 0.6s linear infinite;
          flex-shrink: 0;
        }
        @keyframes lspin { to { transform: rotate(360deg); } }

        .login-error {
          margin-top: 16px;
          padding: 12px 16px;
          background: rgba(239,68,68,0.1);
          border: 1px solid rgba(239,68,68,0.25);
          border-radius: 8px;
          color: #fca5a5;
          font-size: 12px;
          display: flex; align-items: flex-start; gap: 8px;
          animation: errShake 0.3s ease;
        }
        @keyframes errShake {
          0%,100% { transform: translateX(0); }
          25% { transform: translateX(-5px); }
          75% { transform: translateX(5px); }
        }

        .login-divider {
          height: 1px;
          background: rgba(255,255,255,0.06);
          margin: 32px 0 24px;
        }

        .login-footer-note {
          font-size: 11px;
          color: rgba(255,255,255,0.18);
          text-align: center;
          line-height: 1.7;
        }

        .login-footer-note strong { color: rgba(255,255,255,0.3); }

        @media (max-width: 640px) {
          .login-left { display: none; }
          .login-card { width: 100vw; border-radius: 0; min-height: 100vh; align-items: center; }
          .login-right { padding: 40px 28px; justify-content: center; }
        }
      `}</style>

      <div className="login-root">
        <div
          className="login-card"
          style={{
            transform: mounted ? "translateY(0)" : "translateY(28px)",
            opacity: mounted ? 1 : 0,
            transition: "transform 0.65s cubic-bezier(0.16,1,0.3,1), opacity 0.65s ease",
          }}
        >
          {/* LEFT */}
          <div className="login-left">
            <div className="login-logo-row">
              <div className="login-logo-circle">
                <img
                  src="/logo.jpg"
                  alt="Toyota Boshoku"
                  onError={e => {
                    e.target.style.display = "none";
                    e.target.parentElement.innerHTML = `<span style="font-size:18px;font-weight:800;color:#1e40af">TB</span>`;
                  }}
                />
              </div>
              <div className="login-brand-text">
                <b>Toyota Boshoku Device India</b>
                <span>Pvt. Ltd. · Bawal, Haryana</span>
              </div>
            </div>

            <div className="login-hero">
              <div className="login-mes-label">Platform</div>
              <div className="login-headline">Manu<span>fac</span>turing</div>
              <div className="login-sub-headline">Execution System</div>
              <div className="login-desc">
                Centralized production control. Real-time data. Fully automated line provisioning.
              </div>
              <div className="login-stats">
                <div className="login-stat-pill">
                  <div className="val">v2.0</div>
                  <div className="lbl">Version</div>
                </div>
                <div className="login-stat-pill">
                  <div className="val">24/7</div>
                  <div className="lbl">Uptime</div>
                </div>
                <div className="login-stat-pill">
                  <div className="val">OEE</div>
                  <div className="lbl">Tracked</div>
                </div>
              </div>
            </div>
          </div>

          {/* RIGHT */}
          <div className="login-right">
            <div
              style={{
                transform: mounted ? "translateY(0)" : "translateY(16px)",
                opacity: mounted ? 1 : 0,
                transition: "transform 0.65s cubic-bezier(0.16,1,0.3,1) 0.18s, opacity 0.65s ease 0.18s",
              }}
            >
              <div className="login-form-title">Sign In</div>
              <div className="login-form-sub">Enter your credentials to access the control panel</div>

              <form onSubmit={handleSubmit}>
                <div className="login-field">
                  <label>Username</label>
                  <div className="login-input-wrap">
                    <input
                      className="login-input"
                      type="text"
                      placeholder="username"
                      autoComplete="off"
                      value={username}
                      onChange={e => { setUsername(e.target.value); setError(""); }}
                      disabled={loading}
                    />
                    <span className="login-input-icon" style={{ cursor: "default" }}>◎</span>
                  </div>
                </div>

                <div className="login-field">
                  <label>Password</label>
                  <div className="login-input-wrap">
                    <input
                      className="login-input"
                      type={showPass ? "text" : "password"}
                      placeholder="••••••••"
                      autoComplete="current-password"
                      value={password}
                      onChange={e => { setPassword(e.target.value); setError(""); }}
                      disabled={loading}
                    />
                    <span
                      className="login-input-icon"
                      onClick={() => setShowPass(v => !v)}
                      title={showPass ? "Hide password" : "Show password"}
                    >
                      {showPass ? "○" : "●"}
                    </span>
                  </div>
                </div>

                <button className="login-btn" type="submit" disabled={loading}>
                  {loading
                    ? <><div className="login-spinner" /> Authenticating…</>
                    : <>Sign In →</>
                  }
                </button>
              </form>

              {error && (
                <div className="login-error">
                  <span style={{ flexShrink: 0 }}>⚠</span>
                  <span>{error}</span>
                </div>
              )}

              <div className="login-divider" />

              <div className="login-footer-note">
                <strong>Restricted System</strong> — Authorized personnel only.<br />
                © All rights reserved - DX Team TBDI
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
