import { useEffect, useState } from 'react'
import { AlertTriangle, X } from 'lucide-react'
import { api } from '../lib/api'

/**
 * OfflineCameraBar — sticky bottom bar that appears whenever one or
 * more configured cameras are unreachable.  Polls /api/cameras/health
 * every 30 s (server caches 25 s, so it's effectively a TCP-ping per
 * camera per half-minute).
 *
 * Hidden when:
 *   - all cameras online
 *   - no cameras configured yet
 *   - user manually dismisses it (resets on next page reload)
 *
 * Replaces the Reports page per user request 2026-05-13.
 */
export default function OfflineCameraBar() {
  const [offline, setOffline] = useState([])
  const [dismissed, setDismissed] = useState(false)

  const refresh = async () => {
    try {
      const rows = await api.getCameraHealth()
      const off = (rows || []).filter(c => c.online === false)
      setOffline(off)
      // Auto-undismiss when the offline set CHANGES so a new outage
      // surfaces even if user dismissed the previous one.
      setDismissed(prev => {
        const prevIds = JSON.stringify(offline.map(c => c.id).sort())
        const nextIds = JSON.stringify(off.map(c => c.id).sort())
        return prevIds === nextIds ? prev : false
      })
    } catch { /* silent — bar simply stays hidden if API errors */ }
  }

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 30000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (dismissed || offline.length === 0) return null

  return (
    <div className="fixed bottom-0 left-0 right-0 z-40 md:ml-60 transition-all">
      <div className="mx-3 mb-3 bg-red-50 border border-red-300 text-red-900 dark:bg-red-950/40 dark:border-red-800 dark:text-red-200 rounded-xl shadow-lg">
        <div className="flex items-center gap-3 px-4 py-2.5">
          <AlertTriangle size={18} className="flex-shrink-0 text-red-600 dark:text-red-400" />
          <span className="text-sm font-semibold flex-shrink-0">
            {offline.length === 1 ? 'Camera offline:' : `${offline.length} cameras offline:`}
          </span>
          <span className="text-sm font-mono truncate flex-1">
            {offline.map(c => `${c.ip || '?'}${c.name ? ` (${c.name})` : ''}`).join('   ·   ')}
          </span>
          <button
            onClick={() => setDismissed(true)}
            className="flex-shrink-0 p-1 rounded hover:bg-red-100 dark:hover:bg-red-900/30"
            title="Dismiss (will reappear if a different camera goes offline)"
          >
            <X size={15} />
          </button>
        </div>
      </div>
    </div>
  )
}
