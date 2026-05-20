import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Real-time camera feed hook.
 *
 * Phase 1 — MJPEG stream  (/live_feed/<id>)
 *   → true live video like the camera's native page, ~25-30 fps
 *   → browser renders each frame automatically via multipart/x-mixed-replace
 *
 * Phase 2 — Snapshot fallback  (/camera_frame/<id>?t=...)
 *   → if MJPEG errors, poll a JPEG snapshot every 2 s (still shows latest frame)
 *   → no blank flash: next frame preloads before swapping src
 *
 * Phase 3 — Offline
 *   → 3 consecutive snapshot failures → show "Stream Offline"
 *   → keeps retrying silently every 10 s to auto-recover
 */
export function useCameraFrame(cameraId, refreshMs = 2000) {
  const [src,     setSrc]     = useState(null)
  const [offline, setOffline] = useState(false)
  const [phase,   setPhase]   = useState('stream') // 'stream' | 'snapshot' | 'offline'

  const phaseRef      = useRef('stream')
  const mountedRef    = useRef(true)
  const timerRef      = useRef(null)
  const errorCountRef = useRef(0)

  const setP = (p) => { phaseRef.current = p; setPhase(p) }

  const schedule = useCallback((fn, delay) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(fn, delay)
  }, [])

  // ── snapshot poller (phase 2) ─────────────────────────────────────────────
  const startSnapshot = useCallback(() => {
    if (!mountedRef.current) return
    setP('snapshot')

    const poll = () => {
      if (!mountedRef.current) return
      const url = `/camera_frame/${cameraId}?t=${Date.now()}`
      const img = new Image()

      img.onload = () => {
        if (!mountedRef.current) return
        setSrc(url)
        setOffline(false)
        errorCountRef.current = 0
        schedule(poll, refreshMs)
      }
      img.onerror = () => {
        if (!mountedRef.current) return
        errorCountRef.current += 1
        if (errorCountRef.current >= 3) {
          setP('offline')
          setOffline(true)
        }
        const delay = Math.min(errorCountRef.current * 3000, 10000)
        schedule(poll, delay)
      }
      img.src = url
    }
    poll()
  }, [cameraId, refreshMs, schedule])

  // ── stream-error handler (called by <img> onError in MJPEG phase) ─────────
  const onStreamError = useCallback(() => {
    if (!mountedRef.current || phaseRef.current !== 'stream') return
    startSnapshot()
  }, [startSnapshot])

  // ── main effect ───────────────────────────────────────────────────────────
  useEffect(() => {
    mountedRef.current  = true
    errorCountRef.current = 0
    if (timerRef.current) clearTimeout(timerRef.current)

    if (!cameraId) {
      setOffline(true)
      setSrc(null)
      return () => { mountedRef.current = false }
    }

    // MJPEG stream: use absolute URL to Flask directly (bypass Vite proxy)
    // Vite proxy buffers MJPEG frames causing burst delivery → blur
    // Flask has Access-Control-Allow-Origin: * so <img> tag loads fine cross-origin
    setP('stream')
    setOffline(false)
    setSrc(`http://127.0.0.1:5000/live_feed/${cameraId}`)

    return () => {
      mountedRef.current = false
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [cameraId])

  return { src, offline, phase, onStreamError }
}
