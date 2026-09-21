import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";

// Capture the APK launch markers (?src=apk&av=<version>) synchronously, BEFORE
// React Router mounts and strips the query string. Device tracking and the
// in-app update prompt read these from sessionStorage.
try {
  const p = new URLSearchParams(window.location.search);
  if (p.get("src")) sessionStorage.setItem("tbdi_src", p.get("src"));
  if (p.get("av"))  sessionStorage.setItem("tbdi_av",  p.get("av"));
} catch (e) { /* ignore */ }

// Auto-recover from a STALE LAZY CHUNK after a new deploy.  Routes are code-split
// (React.lazy), so each page is a hashed chunk.  When the frontend is rebuilt the
// hashes change and the old chunks are removed; a tab that was already open then
// 404s the old chunk on its next route navigation (e.g. login -> /dashboard) and
// React renders a BLANK page — the "IP se login karne par blank" symptom.  Vite
// fires `vite:preloadError` on window in exactly that case, so do a ONE-TIME full
// reload: index.html is served no-store, so the reload pulls the fresh HTML +
// the new chunks and the app comes back.  Guarded by a short sessionStorage
// stamp so a genuinely unresolvable chunk can never cause a reload loop.
window.addEventListener("vite:preloadError", (e) => {
  try {
    const KEY = "mes_chunk_reload_at";
    const last = Number(sessionStorage.getItem(KEY) || 0);
    if (Date.now() - last > 10000) {          // at most once per 10s → no loop
      sessionStorage.setItem(KEY, String(Date.now()));
      try { e.preventDefault(); } catch (_) {}
      window.location.reload();
    }
  } catch (_) { try { window.location.reload(); } catch (__) {} }
});

// Native app (Capacitor): make the phone status bar a solid blue bar that does
// NOT overlap the web content, keeping time/battery/signal visible with white
// icons. No-op in a normal browser (guarded by isNativePlatform).
import("@capacitor/core").then(({ Capacitor, registerPlugin }) => {
  if (!Capacitor?.isNativePlatform?.()) return;
  // Hide the native splash once the web app has taken over. The APK keeps a
  // branded "starting" splash (logo + spinner) up during load instead of a
  // blank blue screen; we dismiss it here the moment the app is ready. Done
  // via registerPlugin so no extra JS dependency is needed; no-op in a browser.
  try {
    const SplashScreen = registerPlugin("SplashScreen");
    // one frame + a beat so the first React paint is on screen before the
    // splash lifts (avoids a blue flash between the two).
    requestAnimationFrame(() => setTimeout(() => {
      try { SplashScreen.hide(); } catch (e) { /* ignore */ }
    }, 60));
  } catch (e) { /* ignore */ }
  import("@capacitor/status-bar").then(({ StatusBar, Style }) => {
    StatusBar.setOverlaysWebView({ overlays: false }).catch(() => {});
    StatusBar.setStyle({ style: Style.Dark }).catch(() => {});          // white icons
    StatusBar.setBackgroundColor({ color: "#1e40af" }).catch(() => {});
  }).catch(() => {});
}).catch(() => {});

// Register the push service worker on load so Chrome installs a real app
// (WebAPK) via "Install app" — not a browser shortcut. The SW is push-only
// with a no-op fetch handler, so it never caches or intercepts anything.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw-push.js", { scope: "/" }).catch(() => {});
  });
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>
);

// Tell the native LAN panel shell (TBDI-MES-LAN.apk) that the UI has rendered,
// so it drops its blue "Starting MES…" cover exactly when the app appears —
// never a blank frame in between. window.MesSplash exists only in the LAN app;
// harmless everywhere else. Fire after the first paint, and again shortly after
// as a safety in case the very first frame was still empty.
try {
  const _mesReady = () => { try { window.MesSplash && window.MesSplash.ready(); } catch (e) {} };
  requestAnimationFrame(() => setTimeout(_mesReady, 30));
  setTimeout(_mesReady, 1500);
} catch (e) { /* ignore */ }