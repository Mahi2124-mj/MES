/* sw-push.js — Web Push service worker for TBDI MES.
 * PUSH-ONLY: it handles 'push' + 'notificationclick' and nothing else.
 * There is deliberately NO 'fetch' handler, so this service worker never
 * intercepts, caches, or alters any app request — it cannot break the app.
 */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

// No-op fetch handler. It never calls respondWith, so every request proceeds
// normally — no caching, no interception, no risk. Its mere presence lets
// Chrome install the site as a real app (WebAPK) instead of a browser shortcut.
self.addEventListener("fetch", () => {});

self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; }
  catch { d = { body: event.data ? event.data.text() : "" }; }

  const title = d.title || "TBDI MES";
  const options = {
    body:  d.body || "",
    icon:  "/icon-192.png",
    badge: "/icon-192.png",
    tag:   d.tag || "tbdi",
    renotify: true,
    requireInteraction: true,                 // stays until tapped
    vibrate: [200, 100, 200, 100, 300],       // buzz pattern
    data: { url: d.url || "/my-escalations" },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/my-escalations";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if ("focus" in c) {
          try { c.navigate(url); } catch (e) {}
          return c.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
