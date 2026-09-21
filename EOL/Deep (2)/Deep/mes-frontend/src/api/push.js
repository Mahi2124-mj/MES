// push.js — client side of Web Push. Registers the push-only service worker,
// subscribes with the server's VAPID public key, and stores/removes the
// subscription on the backend. All failures are surfaced as thrown Errors so
// the UI can show a message; auto-resubscribe stays silent.

function urlB64ToUint8(base64) {
  const pad = "=".repeat((4 - (base64.length % 4)) % 4);
  const b64 = (base64 + pad).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(b64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}

export function pushSupported() {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

async function registerSW() {
  return navigator.serviceWorker.register("/sw-push.js", { scope: "/" });
}

// Turn notifications ON (must be called from a user gesture the first time,
// because it may prompt for permission).
export async function enablePush(token) {
  if (!pushSupported()) throw new Error("Notifications aren't supported on this device.");
  const perm = await Notification.requestPermission();
  if (perm !== "granted") throw new Error("Notification permission was denied (allow it in Settings).");

  const reg = await registerSW();
  await navigator.serviceWorker.ready;

  const r = await fetch("/api/push/vapid-public", { headers: { Authorization: `Bearer ${token}` } });
  if (!r.ok) throw new Error("Server did not return a push key (HTTP " + r.status + ")");
  const { public_key } = await r.json();

  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlB64ToUint8(public_key),
    });
  }
  const j = sub.toJSON();
  const s = await fetch("/api/push/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ endpoint: j.endpoint, keys: j.keys }),
  });
  if (!s.ok) throw new Error("Could not save the subscription (HTTP " + s.status + ")");
  try { localStorage.setItem("tbdi_push_on", "1"); } catch {}
  return true;
}

export async function disablePush(token) {
  try {
    const reg = (await navigator.serviceWorker.getRegistration("/sw-push.js"))
             || (await navigator.serviceWorker.ready);
    const sub = reg && (await reg.pushManager.getSubscription());
    if (sub) {
      await fetch("/api/push/unsubscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ endpoint: sub.endpoint }),
      });
      await sub.unsubscribe();
    }
  } catch {}
  try { localStorage.removeItem("tbdi_push_on"); } catch {}
}

export function pushIsOn() {
  try { return localStorage.getItem("tbdi_push_on") === "1" && Notification.permission === "granted"; }
  catch { return false; }
}

// Silent — on app load, if the user had notifications on and permission is
// still granted, make sure a live subscription exists (endpoints can rotate).
export async function autoResubscribe(token) {
  try {
    if (!pushSupported() || Notification.permission !== "granted") return;
    if (localStorage.getItem("tbdi_push_on") !== "1") return;
    await enablePush(token);   // no prompt when permission already granted
  } catch {}
}

export async function testPush(token) {
  const r = await fetch("/api/push/test", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!r.ok) throw new Error("Could not send the test (HTTP " + r.status + ")");
  return r.json();
}
