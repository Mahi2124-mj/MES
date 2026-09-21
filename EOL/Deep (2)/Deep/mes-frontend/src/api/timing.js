/*
 * timing.js — record how long the UI made someone wait.
 *
 * Two things are measured, both in the browser because that is where the wait
 * actually happens (server timing alone misses queueing, the tunnel and decode):
 *
 *   page  — route change to first paint of that page
 *   video — click on a cycle dot to the first frame playing
 *
 * Samples are queued and flushed in batches so a burst of navigation never
 * turns into a burst of requests.  This is telemetry measuring the app: it must
 * never slow the app down or break it, so everything here is fire-and-forget
 * and every failure is swallowed silently.
 */

const QUEUE = [];
let flushTimer = null;

const FLUSH_MS = 4000;      // batch window
const MAX_QUEUE = 50;       // hard cap if the endpoint is unreachable

// 2026-08-14 — who the samples belong to.  sendBeacon cannot set an
// Authorization header, so every sample stored before today landed with
// username NULL and per-user reporting was impossible.  The token rides in the
// BODY instead (never a URL — that would put it in access logs).  Anonymous
// still works: wall displays and kiosks have no token and are meant to be
// counted anyway.
let authToken = null;
export function setTimingToken(t) { authToken = t || null; }

function flush() {
  flushTimer = null;
  if (!QUEUE.length) return;
  const items = QUEUE.splice(0, QUEUE.length);
  try {
    const body = JSON.stringify(authToken ? { items, token: authToken } : { items });
    // sendBeacon survives a tab closing mid-navigation, which is exactly when
    // the slowest page loads get abandoned — those are the ones worth keeping.
    if (navigator.sendBeacon) {
      navigator.sendBeacon("/api/ui-timing",
        new Blob([body], { type: "application/json" }));
    } else {
      fetch("/api/ui-timing", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body, keepalive: true,
      }).catch(() => {});
    }
  } catch { /* telemetry must never throw into the app */ }
}

/** Queue one sample.  kind: "page" | "video" | "dwell". */
export function record(kind, name, ms, extra = {}) {
  try {
    if (!name || !isFinite(ms) || ms < 0) return;
    QUEUE.push({
      kind,
      name: String(name).slice(0, 120),
      ms: Math.round(ms),
      line_id: extra.lineId != null ? Number(extra.lineId) : null,
      source: extra.source || null,
      detail: extra.detail || null,
    });
    if (QUEUE.length > MAX_QUEUE) QUEUE.splice(0, QUEUE.length - MAX_QUEUE);
    if (!flushTimer) flushTimer = setTimeout(flush, FLUSH_MS);
  } catch { /* ignore */ }
}

/** Start a stopwatch; call the returned fn once the thing is actually visible.
 *
 * `moreExtra` may be a PROMISE of the extra fields.  The elapsed time is still
 * taken at the moment of the call — only the annotation waits.  That matters
 * for the clip source: it comes from a one-byte probe request, and an archived
 * clip starts playing in ~90 ms, well before that probe resolves.  Reading the
 * value synchronously therefore recorded EVERY sample as "render", including
 * the instant ones, which made the Waiting Time page report the opposite of
 * what was happening.
 */
export function startTimer(kind, name, extra = {}) {
  const t0 = performance.now();
  let done = false;
  return (moreExtra = {}) => {
    if (done) return 0;              // first call wins; retries must not re-log
    done = true;
    const ms = performance.now() - t0;
    const finish = (ex) => record(kind, name, ms, { ...extra, ...(ex || {}) });
    if (moreExtra && typeof moreExtra.then === "function") {
      moreExtra.then(finish, () => finish(null));
    } else {
      finish(moreExtra);
    }
    return ms;
  };
}

// Don't lose the tail of the queue when the tab goes away.
if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flush();
  });
  window.addEventListener("pagehide", flush);
}
