// UpdateBanner.jsx — the in-app update experience for the Android APK shell.
// Three states, all APK-only (browser/PWA always run the latest web app):
//   1. Just updated  → a green "App updated ✓" confirmation toast, so the user
//      knows the update actually completed (their earlier ask: no status was
//      shown after tapping update).
//   2. Outdated      → an "update available" prompt with What's new.
//   3. After tapping "Update now" → clear step-by-step install guidance
//      (download → open file → Install → reopen), instead of silently closing.
import { useState, useEffect } from "react";
import { useAuth } from "../context/AuthContext";
import {
  apkOutdated, getApkInfo, getUpdateStatus, markVersionSeen,
  LATEST_APK, WHATS_NEW,
} from "../api/appVersion";

export default function UpdateBanner() {
  const { user } = useAuth();
  // Read the update status ONCE, before we record the new baseline below.
  const [status] = useState(getUpdateStatus);
  const [showDone, setShowDone] = useState(status.justUpdated && !apkOutdated());
  const [dismissed, setDismissed] = useState(() => {
    try { return sessionStorage.getItem("tbdi_upd_dismiss") === "1"; } catch { return false; }
  });
  const [step, setStep] = useState("prompt"); // "prompt" | "installing"

  // Record the current version as the baseline for the next launch.
  useEffect(() => { markVersionSeen(); }, []);

  // Auto-dismiss the success toast after a few seconds.
  useEffect(() => {
    if (!showDone) return;
    const t = setTimeout(() => setShowDone(false), 8000);
    return () => clearTimeout(t);
  }, [showDone]);

  if (!user) return null;

  // ── 1) Just updated → success confirmation ──────────────────────────────
  if (showDone) {
    return (
      <div style={toast} onClick={() => setShowDone(false)}>
        <span style={{ fontSize: 22, lineHeight: 1 }}>✅</span>
        <div style={{ display: "grid", gap: 2, flex: 1 }}>
          <div style={{ fontWeight: 800, fontSize: 14 }}>App updated</div>
          <div style={{ fontSize: 12, opacity: 0.92 }}>
            You are now on v{status.version} — the latest version.
          </div>
        </div>
        <button onClick={(e) => { e.stopPropagation(); setShowDone(false); }} style={toastX}>✕</button>
      </div>
    );
  }

  // ── 2) Outdated → update prompt ─────────────────────────────────────────
  if (dismissed || !apkOutdated()) return null;
  const { version } = getApkInfo();

  const close = () => {
    try { sessionStorage.setItem("tbdi_upd_dismiss", "1"); } catch {}
    setDismissed(true);
  };

  // Same-origin .apk. The old app shell's WebView can't download a file itself,
  // so target=_blank / window.open hands the URL to the system browser, which
  // downloads it (the server sends Content-Disposition:attachment so it
  // downloads straight away instead of opening a blank tab).
  // Use the VERSION-SPECIFIC filename, not the stable /TBDI-MES.apk: Cloudflare
  // caches the stable path by URL ignoring the query, so a `?v=&r=` cache-buster
  // did NOT help (it kept serving the previously-cached bytes for hours). A
  // per-version path is a genuinely new URL → CF fetches it fresh, and it's
  // immutable so it can be cached forever safely. dist keeps TBDI-MES-v<X>.apk.
  const APK_URL = `/TBDI-MES-v${LATEST_APK}.apk`;

  const startUpdate = () => {
    try { window.open(APK_URL, "_blank", "noopener"); } catch {}
    setStep("installing");
  };

  return (
    <div style={overlay} onClick={close}>
      <div style={card} onClick={e => e.stopPropagation()}>
        {step === "prompt" ? (
          <>
            <div style={{ fontSize: 40 }}>🎉</div>
            <h2 style={h2}>App update available</h2>
            <div style={sub}>
              You are on {version ? `v${version}` : "an older build"} · Latest is{" "}
              <b style={{ color: "#1e40af" }}>v{LATEST_APK}</b>
            </div>

            <div style={whatsBox}>
              <div style={whatsHdr}>What's new</div>
              <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 6 }}>
                {WHATS_NEW.map((w, i) => <li key={i} style={li}>{w}</li>)}
              </ul>
            </div>

            <button onClick={startUpdate} style={primaryBtn}>Update now (v{LATEST_APK})</button>
            <button onClick={close} style={laterBtn}>Later</button>
          </>
        ) : (
          <>
            <div style={{ fontSize: 40 }}>⬇️</div>
            <h2 style={h2}>Downloading update…</h2>
            <div style={sub}>
              The update (<b>v{LATEST_APK}</b>) is downloading in your browser.
            </div>
            <ol style={steps}>
              <li>Open the <b>notification bar</b> (or <b>Downloads</b>) and find the downloaded <b>.apk</b>.</li>
              <li>Tap it and choose <b>Install</b> — it installs over the app, no uninstall needed.</li>
              <li><b>Reopen the app</b> — you'll see an <b>“App updated ✓”</b> confirmation.</li>
            </ol>
            <div style={{ fontSize: 12, color: "#64748b", margin: "2px 0 10px", textAlign: "left" }}>
              Didn't start? Tap to download directly:
            </div>
            <a href={APK_URL} target="_blank" rel="noopener"
               style={{ ...primaryBtn, textDecoration: "none", boxSizing: "border-box", marginBottom: 10 }}>
              ⬇ Download update (v{LATEST_APK})
            </a>
            <div style={{ display: "flex", gap: 10 }}>
              <button onClick={() => setStep("prompt")} style={backBtn}>← Back</button>
              <button onClick={close} style={{ ...primaryBtn, flex: 1, background: "#475569", boxShadow: "none" }}>Close</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

const overlay = {
  position: "fixed", inset: 0, zIndex: 8000,
  background: "rgba(2,8,40,.55)", backdropFilter: "blur(3px)",
  display: "flex", alignItems: "center", justifyContent: "center", padding: 20,
};
const card = {
  background: "#fff", borderRadius: 18, padding: "24px 22px 16px",
  maxWidth: 380, width: "100%", textAlign: "center",
  boxShadow: "0 24px 60px rgba(2,8,40,.4)",
};
const h2 = { margin: "6px 0 2px", fontSize: 20, fontWeight: 900, color: "#0f172a" };
const sub = { fontSize: 13, color: "#64748b", marginBottom: 14 };
const whatsBox = {
  textAlign: "left", background: "#f8fafc", border: "1px solid #eef2f7",
  borderRadius: 10, padding: "12px 14px", marginBottom: 16,
};
const whatsHdr = {
  fontSize: 11, fontWeight: 800, color: "#64748b", textTransform: "uppercase",
  letterSpacing: ".05em", marginBottom: 8,
};
const li = { fontSize: 13, color: "#334155", lineHeight: 1.4 };
const steps = {
  textAlign: "left", margin: "0 0 6px", paddingLeft: 20,
  display: "grid", gap: 9, fontSize: 13, color: "#334155", lineHeight: 1.45,
};
const primaryBtn = {
  display: "block", width: "100%", background: "linear-gradient(135deg,#1e40af,#2563eb)",
  color: "#fff", border: "none", fontWeight: 800, fontSize: 15, padding: "13px",
  borderRadius: 12, cursor: "pointer", boxShadow: "0 8px 22px rgba(30,64,175,.35)",
};
const laterBtn = {
  background: "none", border: "none", color: "#64748b", fontSize: 13,
  fontWeight: 600, cursor: "pointer", padding: "10px 6px 4px",
};
const backBtn = {
  flex: 1, background: "#fff", border: "1.5px solid #e2e8f0", color: "#334155",
  fontSize: 15, fontWeight: 800, padding: "13px", borderRadius: 12, cursor: "pointer",
};

// Success toast (top of screen, non-blocking).
const toast = {
  position: "fixed", top: "calc(env(safe-area-inset-top, 0px) + 12px)",
  left: 12, right: 12, zIndex: 8000, margin: "0 auto", maxWidth: 420,
  display: "flex", alignItems: "center", gap: 12,
  background: "linear-gradient(135deg,#059669,#10b981)", color: "#fff",
  padding: "12px 14px", borderRadius: 14,
  boxShadow: "0 14px 34px rgba(5,150,105,.4)", cursor: "pointer",
};
const toastX = {
  background: "rgba(255,255,255,.2)", border: "none", color: "#fff",
  width: 24, height: 24, borderRadius: 12, cursor: "pointer",
  fontSize: 12, fontWeight: 800, flexShrink: 0,
};
