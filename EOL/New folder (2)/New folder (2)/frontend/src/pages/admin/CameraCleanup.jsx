/* ───────────────────────────────────────────────────────────────────
 * CameraCleanup.jsx   (/admin/cameras)   2026-10-08
 * ───────────────────────────────────────────────────────────────────
 * Operator: "CMS me extra camera delete option de de".  Every configured
 * camera, which machine(s) use it, and Delete for the ones no machine uses
 * (on 8-Oct: 14 of 144 — mostly PLC IPs entered as cameras and dead
 * addresses that the recorder keeps retrying).
 *
 * A camera that a machine still uses cannot be deleted here: removing it
 * would silently stop that machine's video.  Change the camera on the machine
 * first (Machines page), then it shows up as "Not used".
 *
 * Backend: GET /api/masters/cameras, GET /api/camera-configs,
 * DELETE /api/masters/cameras/<id> (admin role, enforced by the CMS).
 * Deliberately no live status column: /api/cameras/health TCP-probes every
 * camera, which these single-session cameras should not get on a page view.
 */
import { useState, useEffect, useMemo } from 'react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'
import { useAuth } from '../../context/AuthContext'
import { Camera, Trash2, RefreshCw, Search, ShieldAlert } from 'lucide-react'

export default function CameraCleanup() {
  const toast = useToast()
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'

  const [cams, setCams]         = useState([])
  const [bindings, setBindings] = useState([])
  const [loading, setLoading]   = useState(true)
  const [show, setShow]         = useState('unused')   // 'unused' | 'all'
  const [q, setQ]               = useState('')
  const [busy, setBusy]         = useState(null)        // camera id being deleted

  const load = async () => {
    setLoading(true)
    try {
      const [c, b] = await Promise.all([api.getCameras(), api.getCameraConfigs()])
      setCams(Array.isArray(c) ? c : [])
      setBindings(Array.isArray(b) ? b : [])
    } catch (e) {
      toast.error(e.message || 'Could not load cameras')
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { load() }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  // camera id -> machines using it
  const usedBy = useMemo(() => {
    const m = {}
    for (const b of bindings) {
      if (!b.camera_id) continue
      ;(m[b.camera_id] = m[b.camera_id] || []).push(
        [b.machine_name, b.line_name].filter(Boolean).join(' — ') || b.machine_id || 'machine')
    }
    return m
  }, [bindings])

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return cams
      .map(c => ({ ...c, used: usedBy[c.id] || [] }))
      .filter(c => show === 'all' || c.used.length === 0)
      .filter(c => !needle || [c.name, c.ip, c.id, ...c.used].join(' ').toLowerCase().includes(needle))
      .sort((a, b) => (a.used.length > 0) - (b.used.length > 0)
                      || String(a.ip || '').localeCompare(String(b.ip || ''), undefined, { numeric: true }))
  }, [cams, usedBy, show, q])

  const unusedCount = cams.filter(c => !(usedBy[c.id] || []).length).length

  const remove = async (cam) => {
    if (!window.confirm(`Delete camera "${cam.name}" (${cam.ip})?\n\nNo machine uses it. This cannot be undone.`)) return
    setBusy(cam.id)
    try {
      await api.deleteCamera(cam.id)
      toast.success(`Deleted ${cam.name}`)
      await load()
    } catch (e) {
      toast.error(e.response?.data?.message || e.message || 'Delete failed')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Camera className="w-5 h-5 text-slate-500" />
          <h2 className="text-lg font-bold text-slate-900 dark:text-white">Cameras</h2>
        </div>
        <span className="text-sm text-slate-500">
          {cams.length} configured · {cams.length - unusedCount} used by a machine ·{' '}
          <b className={unusedCount ? 'text-amber-600' : ''}>{unusedCount} not used</b>
        </span>
        <button onClick={load} disabled={loading}
          className="ml-auto inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-800">
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </div>

      {!isAdmin && (
        <div className="flex items-center gap-2 text-sm px-3 py-2 rounded-lg bg-amber-50 text-amber-800 border border-amber-200 dark:bg-amber-900/30 dark:text-amber-300 dark:border-amber-800">
          <ShieldAlert className="w-4 h-4" /> Only an admin can delete cameras.
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {[['unused', `Not used (${unusedCount})`], ['all', `All (${cams.length})`]].map(([k, l]) => (
          <button key={k} onClick={() => setShow(k)}
            className={`px-3 py-1.5 text-sm rounded-full border ${show === k
              ? 'bg-blue-600 border-blue-600 text-white'
              : 'border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-300'}`}>
            {l}
          </button>
        ))}
        <div className="relative ml-auto">
          <Search className="w-4 h-4 absolute left-2.5 top-2 text-slate-400" />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search name / IP / machine"
            className="pl-8 pr-3 py-1.5 text-sm rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 w-64" />
        </div>
      </div>

      <div className="bg-white dark:bg-slate-800 rounded-xl border border-slate-200 dark:border-slate-700 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-slate-500 border-b border-slate-200 dark:border-slate-700">
              <th className="px-4 py-2.5">Camera</th>
              <th className="px-4 py-2.5">IP</th>
              <th className="px-4 py-2.5">Used by</th>
              <th className="px-4 py-2.5 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {!loading && rows.length === 0 && (
              <tr><td colSpan={4} className="px-4 py-6 text-center text-slate-400">
                {show === 'unused' ? 'Every camera is used by a machine.' : 'No cameras.'}
              </td></tr>
            )}
            {rows.map(c => (
              <tr key={c.id} className="border-b last:border-0 border-slate-100 dark:border-slate-700/60">
                <td className="px-4 py-2.5">
                  <div className="font-semibold text-slate-900 dark:text-white">{c.name}</div>
                  <div className="text-xs text-slate-400 font-mono">{c.id}</div>
                </td>
                <td className="px-4 py-2.5 font-mono">{c.ip}</td>
                <td className="px-4 py-2.5">
                  {c.used.length
                    ? c.used.map((u, i) => <div key={i} className="text-slate-700 dark:text-slate-300">{u}</div>)
                    : <span className="text-amber-600 font-semibold">Not used</span>}
                </td>
                <td className="px-4 py-2.5 text-right whitespace-nowrap">
                  {c.used.length ? (
                    <span className="text-xs text-slate-400" title="Change the camera on the machine first">In use</span>
                  ) : (
                    <button onClick={() => remove(c)} disabled={!isAdmin || busy === c.id}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-red-600 text-white disabled:opacity-40 hover:bg-red-700">
                      <Trash2 className="w-4 h-4" /> {busy === c.id ? 'Deleting…' : 'Delete'}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-400">
        A camera used by a machine cannot be deleted here — change that machine's camera on the Machines page first.
      </p>
    </div>
  )
}
