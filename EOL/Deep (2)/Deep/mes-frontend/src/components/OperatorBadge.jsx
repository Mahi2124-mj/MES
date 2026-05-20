/**
 * OperatorBadge
 * =============
 * Small chip-shaped widget that shows who is currently signed-in as the
 * line's operator.  Click it to open the badge-scan modal — the input
 * inside is focused so a USB barcode/RFID scanner can drop the code +
 * Enter directly.
 *
 * Props:
 *   lineId   — int   (required) which line this widget is for
 *   token    — JWT   (required) auth bearer
 *   shift    — str   (optional) current shift to stamp on the session
 *
 * Backend endpoints used:
 *   GET  /api/operators/active/{line_id}
 *   POST /api/operators/login    { badge_code, line_id, shift_name }
 *   POST /api/operators/logout   { line_id }
 *
 * The component refreshes who's-on-duty every 30 s — covers the case
 * where a second floor PC logs a different operator out from under us.
 */
import { useState, useEffect, useRef, useCallback } from "react";
import { api } from "../api/client";

export default function OperatorBadge({ lineId, token, shift }) {
  const [active,  setActive]  = useState(null);   // { full_name, badge_code, ... } | null
  const [modal,   setModal]   = useState(false);
  const [code,    setCode]    = useState("");
  const [busy,    setBusy]    = useState(false);
  const [err,     setErr]     = useState("");
  const inputRef = useRef(null);

  // ── Poll active operator every 30 s ───────────────────────────
  const refresh = useCallback(async () => {
    if (!lineId || !token) return;
    try {
      const r = await api.get(`/api/operators/active/${lineId}`, token);
      setActive(r?.active ? r : null);
    } catch { /* swallow — widget is non-critical */ }
  }, [lineId, token]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30000);
    return () => clearInterval(t);
  }, [refresh]);

  // ── Open modal → focus input so USB scanner can dump code ─────
  useEffect(() => {
    if (modal && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 50);
    }
    if (!modal) { setCode(""); setErr(""); }
  }, [modal]);

  const submitLogin = async (badgeCode) => {
    const clean = (badgeCode || "").trim();
    if (!clean) { setErr("Scan a badge or type the code"); return; }
    setBusy(true); setErr("");
    try {
      const r = await api.post("/api/operators/login", {
        badge_code: clean,
        line_id:    lineId,
        shift_name: shift || null,
      }, token);
      setActive({
        active: true,
        full_name: r.operator,
        badge_code: clean,
      });
      setModal(false);
      refresh();
    } catch (e) {
      setErr(e?.message || "Login failed");
    } finally { setBusy(false); }
  };

  const logout = async () => {
    if (!confirm(`Sign out operator ${active?.full_name || "current"}?`)) return;
    try {
      await api.post("/api/operators/logout", { line_id: lineId }, token);
      setActive(null);
    } catch (e) { alert(e?.message || "Logout failed"); }
  };

  // USB barcode scanners emulate a keyboard → they type all chars
  // rapidly then end with Enter (\\n).  React's onKeyDown gives us
  // the Enter; we read the input value at that moment.
  const onKeyDown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submitLogin(code);
    }
  };

  // ── RENDER ────────────────────────────────────────────────────
  const chipBase = {
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "4px 10px", borderRadius: 999, fontSize: 11.5,
    fontWeight: 600, cursor: "pointer", userSelect: "none",
    border: "1px solid", transition: "transform 120ms ease",
  };
  const chipOn = {
    ...chipBase,
    background: "#dcfce7", color: "#166534", borderColor: "#86efac",
  };
  const chipOff = {
    ...chipBase,
    background: "#f1f5f9", color: "#64748b", borderColor: "#cbd5e1",
  };

  return (
    <>
      {active ? (
        <span style={chipOn} title="Click to sign out" onClick={logout}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#16a34a" }} />
          {active.full_name}
          {active.badge_code && (
            <span style={{ color: "#15803d", fontFamily: "monospace", fontSize: 10, opacity: 0.7 }}>
              · {active.badge_code}
            </span>
          )}
        </span>
      ) : (
        <span style={chipOff} title="Scan operator badge" onClick={() => setModal(true)}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#94a3b8" }} />
          Scan badge to sign in
        </span>
      )}

      {modal && (
        <div
          onClick={() => setModal(false)}
          style={{
            position: "fixed", inset: 0, background: "rgba(15,23,42,0.45)",
            zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center",
          }}>
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "#fff", borderRadius: 12, padding: "24px 28px",
              minWidth: 380, boxShadow: "0 20px 50px rgba(0,0,0,0.25)",
            }}>
            <div style={{ fontSize: 16, fontWeight: 700, color: "#0f172a", marginBottom: 4 }}>
              Operator sign-in
            </div>
            <div style={{ fontSize: 12, color: "#64748b", marginBottom: 16 }}>
              Scan the badge with the USB reader, or type the code and press Enter.
            </div>
            <input
              ref={inputRef}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="badge code"
              autoFocus
              style={{
                width: "100%", padding: "10px 12px", fontSize: 14,
                fontFamily: "monospace", border: "2px solid #3b82f6",
                borderRadius: 8, outline: "none",
              }}
            />
            {err && <div style={{ color: "#b91c1c", fontSize: 12, marginTop: 8 }}>{err}</div>}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
              <button
                onClick={() => setModal(false)}
                style={{
                  padding: "6px 14px", fontSize: 12, fontWeight: 600,
                  background: "#fff", border: "1px solid #cbd5e1", borderRadius: 6,
                  color: "#475569", cursor: "pointer",
                }}>
                Cancel
              </button>
              <button
                disabled={busy}
                onClick={() => submitLogin(code)}
                style={{
                  padding: "6px 14px", fontSize: 12, fontWeight: 600,
                  background: "#3b82f6", border: "1px solid #3b82f6", borderRadius: 6,
                  color: "#fff", cursor: busy ? "wait" : "pointer", opacity: busy ? 0.6 : 1,
                }}>
                {busy ? "Signing in…" : "Sign in"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
