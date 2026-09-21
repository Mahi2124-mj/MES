// deviceCheckin.js — reports this device to /api/devices/checkin so admins
// can see which phones/tablets/browsers run the MES and on what version.
//
// Source detection:
//   • APK (TWA)  → document.referrer starts with android-app://in.tbdi.mes,
//                  and the APK launch URL also carries ?src=apk&av=<version>.
//   • PWA        → display-mode: standalone (installed from Chrome).
//   • browser    → everything else.
// The ?src / ?av markers are captured once and kept in sessionStorage so
// they survive client-side route changes.

// Bump this when you ship a notable frontend change (shows as "web version").
export const WEB_BUILD = "2026-09-02";

const APK_PACKAGE = "in.tbdi.mes";

function getDeviceId() {
  try {
    let id = localStorage.getItem("tbdi_device_id");
    if (!id) {
      id = (crypto?.randomUUID?.() || (Date.now() + "-" + Math.random().toString(16).slice(2)));
      localStorage.setItem("tbdi_device_id", id);
    }
    return id;
  } catch {
    return "no-storage-" + Math.random().toString(16).slice(2);
  }
}

function detect() {
  // Capture APK markers from the launch URL once, then remember them.
  try {
    const p = new URLSearchParams(window.location.search);
    if (p.get("src")) sessionStorage.setItem("tbdi_src", p.get("src"));
    if (p.get("av"))  sessionStorage.setItem("tbdi_av",  p.get("av"));
  } catch {}
  let src = null, av = null;
  try { src = sessionStorage.getItem("tbdi_src"); av = sessionStorage.getItem("tbdi_av"); } catch {}

  const ref = document.referrer || "";
  const isTWA = ref.indexOf("android-app://" + APK_PACKAGE) === 0;
  let standalone = false;
  try {
    standalone = (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches)
                 || window.navigator.standalone === true;
  } catch {}

  if (!src) src = isTWA ? "apk" : (standalone ? "pwa" : "browser");
  return { src, av };
}

// Fire a check-in.  Silent on any failure — telemetry must never break the app.
export function deviceCheckin(token) {
  try {
    const { src, av } = detect();
    const body = {
      device_id:   getDeviceId(),
      app_source:  src,
      app_version: av || WEB_BUILD,   // exact APK version if the launch URL carried it
      web_version: WEB_BUILD,
      user_agent:  navigator.userAgent,
      screen:      `${window.screen?.width || 0}x${window.screen?.height || 0}`,
    };
    fetch("/api/devices/checkin", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
      keepalive: true,
    }).catch(() => {});
  } catch {}
}
