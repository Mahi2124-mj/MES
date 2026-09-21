// appVersion.js — detect the running Android APK's version and decide whether
// a newer APK is available, so an in-app "update available" prompt can show.
//
// The web app itself is always latest (a TWA loads live mes.tbdi.in), but the
// APK *shell* can be old.  The APK launch URL carries ?src=apk&av=<version>
// (v1.0.1+); a v1.0.0 APK has no av → treated as outdated.

// ── bump these when a new APK ships ──────────────────────────────────────
// 2026-09-06 — the false-update LOOP is fixed. Background: the old shipped apk
// was versionName 2.0.3 but its baked launch URL carried `?av=2.0.2` (whoever
// built it bumped versionName but forgot the `av` in capacitor.config.json), so
// every install kept reporting 2.0.2 and re-prompting forever. A CORRECTED apk
// is now shipped: versionName 2.0.3, versionCode 8, and `av=2.0.3` baked in
// (config synced in capacitor/android/.../assets/capacitor.config.json). Users
// on the old broken build (av 2.0.2) get ONE prompt → install this fixed apk →
// it launches with av=2.0.3 == LATEST → "Up to date" and the loop ends for
// good. ALWAYS bump `av` in capacitor.config.json in lockstep with versionName.
export const LATEST_APK = "2.0.8";
// What the latest APK adds (shown in the update prompt).
export const WHATS_NEW = [
  "🔔 Alerts now reach you even when the app is closed",
  "🔒 Stay logged in — even across app updates",
  "✅ Approve / reject deviations straight from the email",
  "📱 Fixed side-scrolling / hidden content on every page",
];

function captureMarkers() {
  try {
    const p = new URLSearchParams(window.location.search);
    if (p.get("src")) sessionStorage.setItem("tbdi_src", p.get("src"));
    if (p.get("av"))  sessionStorage.setItem("tbdi_av",  p.get("av"));
    // 2026-09-06 — the LAN build (65" interactive panel) carries &lan=1 and
    // loads the app from the local server IP over HTTP. It must NEVER show the
    // "update available" prompt: that apk points at mes.tbdi.in, so installing
    // it would silently switch the panel OFF the LAN back to the internet.
    if (p.get("lan")) sessionStorage.setItem("tbdi_lan", p.get("lan"));
  } catch {}
}

export function getApkInfo() {
  captureMarkers();
  let src = null, av = null, lan = null;
  try {
    src = sessionStorage.getItem("tbdi_src");
    av  = sessionStorage.getItem("tbdi_av");
    lan = sessionStorage.getItem("tbdi_lan");
  } catch {}
  const isTWA = (document.referrer || "").indexOf("android-app://in.tbdi.mes") === 0;
  return { isApk: src === "apk" || src === "capacitor" || isTWA, version: av || null, isLan: lan === "1" };
}

// semver-ish compare: -1 if a<b, 0 equal, 1 if a>b
function cmp(a, b) {
  const pa = String(a || "0").split(".").map(n => parseInt(n, 10) || 0);
  const pb = String(b || "0").split(".").map(n => parseInt(n, 10) || 0);
  for (let i = 0; i < 3; i++) {
    const d = (pa[i] || 0) - (pb[i] || 0);
    if (d) return d < 0 ? -1 : 1;
  }
  return 0;
}

// True only when running inside the APK AND its version is older than LATEST.
// Browser / PWA always run the latest web app → never "outdated".
export function apkOutdated() {
  const { isApk, version, isLan } = getApkInfo();
  if (!isApk) return false;
  if (isLan) return false;         // LAN panel build never self-updates (see above)
  return cmp(version || "0.0.0", LATEST_APK) < 0;
}

// ── update confirmation ──────────────────────────────────────────────────
// The running version is compared against the last version this device saw
// (persisted in localStorage). When it goes up, the user just installed an
// update → show a "✓ Updated" confirmation. Read-only; call markVersionSeen()
// once afterwards to record the new baseline for next launch.
export function getUpdateStatus() {
  const { isApk, version } = getApkInfo();
  if (!isApk || !version) return { justUpdated: false, version: null, from: null };
  let seen = null;
  try { seen = localStorage.getItem("tbdi_seen_ver"); } catch {}
  return { justUpdated: !!(seen && cmp(version, seen) > 0), from: seen, version };
}

// Record the current APK version as the new baseline (so the next launch can
// tell whether an update happened since this one).
export function markVersionSeen() {
  const { isApk, version } = getApkInfo();
  if (!isApk || !version) return;
  try { localStorage.setItem("tbdi_seen_ver", version); } catch {}
}

// A short, always-available status line (used in the side-nav footer) so the
// user can check the app version and whether it is current at any time.
export function versionStatus() {
  const { isApk, version } = getApkInfo();
  if (!isApk) return { isApk: false, label: "Web app", detail: "Always latest", ok: true };
  const outdated = apkOutdated();
  return {
    isApk: true,
    label: `App v${version || "?"}`,
    detail: outdated ? `Update to v${LATEST_APK}` : "Up to date",
    ok: !outdated,
  };
}
