/* ───────────────────────────────────────────────────────────────────
 * StorageAdmin.jsx   (/admin/storage)   2026-06-19
 * ───────────────────────────────────────────────────────────────────
 * Admin page to control where cycle videos are stored and how long they
 * are kept:
 *   • Video Save Path  — pick any folder, incl. an external/USB drive,
 *     via the server-side folder browser.  Hot-reloads recorders.
 *   • Retention        — keep clips for N hours / days, then auto-delete.
 *   • Storage health    — live banner if the configured drive is gone /
 *     read-only (recorder falls back to the local default) or low on space.
 *
 * Backend: GET/POST /api/config/video-path, GET /api/config/storage-health,
 * POST /api/admin/video-cleanup (admin), folder browser /api/config/list-*.
 * Admin-role only (the rest of the app has no role gate yet).
 */
import { useState, useEffect } from 'react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'
import { useAuth } from '../../context/AuthContext'
import {
  HardDrive, FolderOpen, Folder, FolderPlus, ChevronLeft, X, Check,
  AlertTriangle, CheckCircle2, Clock, Save, Trash2, RefreshCw, ShieldAlert,
} from 'lucide-react'

export default function StorageAdmin() {
  const toast = useToast()
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'

  const [info, setInfo]               = useState(null)   // get-video-path payload
  const [health, setHealth]           = useState(null)   // storage-health payload
  const [savePath, setSavePath]       = useState('')
  const [retVal, setRetVal]           = useState(2)       // numeric value
  const [retUnit, setRetUnit]         = useState('days')  // 'hours' | 'days'
  const [loading, setLoading]         = useState(true)
  const [saving, setSaving]           = useState(false)
  const [cleaning, setCleaning]       = useState(false)
  const [showPicker, setShowPicker]   = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [vp, h] = await Promise.all([
        api.getVideoPath(),
        api.getStorageHealth().catch(() => null),
      ])
      setInfo(vp)
      setHealth(h)
      setSavePath(vp.save_path || '')
      const rh = vp.retention_hours ?? 48
      // Show whole days when it divides cleanly, else hours.
      if (rh % 24 === 0 && rh >= 24) { setRetUnit('days');  setRetVal(rh / 24) }
      else                          { setRetUnit('hours'); setRetVal(rh) }
    } catch (e) {
      toast.error(e?.response?.data?.message || 'Could not load storage config')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { if (isAdmin) load() }, [])  // eslint-disable-line

  const retentionHours = () => {
    const v = Math.max(1, Math.round(Number(retVal) || 0))
    return retUnit === 'days' ? v * 24 : v
  }

  const saveAll = async () => {
    const rh = retentionHours()
    if (rh < 1 || rh > 8760) { toast.error('Retention must be 1 hour … 365 days'); return }
    setSaving(true)
    try {
      const r = await api.setVideoPath({ save_path: savePath.trim(), retention_hours: rh })
      toast.success(r?.message || 'Storage settings saved')
      await load()
    } catch (e) {
      toast.error(e?.response?.data?.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const cleanNow = async () => {
    if (!confirm('Apply retention now? Clips older than the retention window will be deleted immediately.')) return
    setCleaning(true)
    try {
      const r = await api.runVideoCleanup()
      const d = r?.deleted ?? r?.deleted_mp4 ?? 0
      const mb = r?.freed_mb ?? 0
      toast.success(`Retention applied — removed ${d} old clip(s), freed ${mb} MB`)
      await load()
    } catch (e) {
      toast.error(e?.response?.data?.message || 'Cleanup failed')
    } finally {
      setCleaning(false)
    }
  }

  if (!isAdmin) {
    return (
      <div className="space-y-4">
        <div><h1 className="page-title">Storage</h1></div>
        <div className="glass p-8 text-center text-slate-500 dark:text-slate-400">
          <ShieldAlert className="mx-auto mb-3 text-amber-500" size={32} />
          This page is admin-only.
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="page-title">Storage &amp; Retention</h1>
          <p className="page-subtitle">Where cycle videos are stored and how long they are kept</p>
        </div>
        <button className="btn-secondary text-sm flex items-center gap-1" onClick={load} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh
        </button>
      </div>

      {/* ── Storage health banner ───────────────────────────────────── */}
      <HealthBanner health={health} />

      {/* ── Save path card ──────────────────────────────────────────── */}
      <div className="glass dark:bg-slate-800/50 dark:border-slate-700 p-5 space-y-4">
        <div className="flex items-center gap-2">
          <HardDrive size={16} className="text-blue-600" />
          <span className="text-sm font-bold text-gray-700 dark:text-slate-200">Video Save Path</span>
          {info?.free_gb != null && (
            <span className="text-xs text-gray-400 ml-auto">
              Disk free:{' '}
              <strong className={info.free_gb < 10 ? 'text-red-500' : 'text-emerald-600'}>
                {info.free_gb} GB
              </strong>
            </span>
          )}
        </div>

        <div className="flex gap-2 items-center flex-wrap">
          <input
            className="input-field font-mono text-sm flex-1 min-w-[220px]"
            value={savePath}
            onChange={e => setSavePath(e.target.value)}
            placeholder="e.g. D:\MES_Videos  or  E:\ (external drive)"
            onKeyDown={e => { if (e.key === 'Enter') saveAll() }}
          />
          <button
            className="btn-secondary text-sm whitespace-nowrap flex items-center gap-1"
            onClick={() => setShowPicker(true)}
            title="Browse folders / drives on the server"
          >
            <FolderOpen size={14} /> Browse
          </button>
        </div>
        {info?.effective_path && (
          <p className="text-xs text-gray-400">
            Recording to:{' '}
            <code className="bg-gray-100 dark:bg-slate-700 px-1 rounded">{info.effective_path}</code>
          </p>
        )}

        {/* Retention */}
        <div className="pt-3 border-t border-slate-200 dark:border-slate-700">
          <div className="flex items-center gap-2 mb-2">
            <Clock size={16} className="text-blue-600" />
            <span className="text-sm font-bold text-gray-700 dark:text-slate-200">Retention</span>
          </div>
          <div className="flex gap-2 items-center flex-wrap">
            <span className="text-sm text-gray-600 dark:text-slate-300">Keep videos for</span>
            <input
              type="number" min={1}
              className="input-field text-sm w-24"
              value={retVal}
              onChange={e => setRetVal(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') saveAll() }}
            />
            <select
              className="input-field text-sm w-28"
              value={retUnit}
              onChange={e => setRetUnit(e.target.value)}
            >
              <option value="hours">Hours</option>
              <option value="days">Days</option>
            </select>
            <span className="text-xs text-gray-400">
              then auto-delete · = {retentionHours()} h
            </span>
          </div>
          <p className="text-xs text-gray-400 mt-2">
            Older clips are removed hourly and at each shift start. TS recordings still rotate per shift.
          </p>
        </div>

        <div className="flex gap-2 items-center pt-2">
          <button className="btn-primary text-sm flex items-center gap-1" onClick={saveAll} disabled={saving}>
            <Save size={14} /> {saving ? 'Saving…' : 'Save settings'}
          </button>
          <button className="btn-secondary text-sm flex items-center gap-1" onClick={cleanNow} disabled={cleaning}>
            <Trash2 size={14} /> {cleaning ? 'Applying…' : 'Apply retention now'}
          </button>
        </div>
      </div>

      <p className="text-xs text-gray-400">
        Videos save as{' '}
        <code className="bg-gray-100 dark:bg-slate-700 px-1 rounded">path/Zone/Line/Machine/Date/Shift/Slot/partcode.mp4</code>
      </p>

      {showPicker && (
        <FolderPickerModal
          initial={savePath}
          toast={toast}
          onClose={() => setShowPicker(false)}
          onPick={(p) => { setSavePath(p); setShowPicker(false) }}
        />
      )}
    </div>
  )
}

/* ── Storage-health banner ─────────────────────────────────────────── */
function HealthBanner({ health }) {
  if (!health) return null
  const { status, configured_path, default_path, effective_path, free_gb } = health
  if (status === 'ok') {
    return (
      <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-300 text-sm">
        <CheckCircle2 size={16} /> Storage healthy — recording to{' '}
        <code className="font-mono">{effective_path}</code>
        {free_gb != null && <span className="ml-auto text-xs">{free_gb} GB free</span>}
      </div>
    )
  }
  if (status === 'fallback') {
    return (
      <div className="flex items-start gap-2 px-4 py-3 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-300 dark:border-red-800 text-red-700 dark:text-red-300 text-sm">
        <AlertTriangle size={18} className="flex-shrink-0 mt-0.5" />
        <div>
          <div className="font-bold">Configured drive not available — using fallback!</div>
          <div className="text-xs mt-0.5">
            <code className="font-mono">{configured_path}</code> is missing or not writable
            (external drive removed / read-only). Recording to{' '}
            <code className="font-mono">{default_path}</code> instead. Reconnect the drive or pick a writable folder, then Save.
          </div>
        </div>
      </div>
    )
  }
  if (status === 'low_space') {
    return (
      <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-300 dark:border-amber-800 text-amber-700 dark:text-amber-300 text-sm">
        <AlertTriangle size={16} /> Low disk space — only{' '}
        <strong>{free_gb} GB</strong> free on <code className="font-mono">{effective_path}</code>. Reduce retention or free space.
      </div>
    )
  }
  // status === 'default'
  return (
    <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 text-blue-700 dark:text-blue-300 text-sm">
      <HardDrive size={16} /> No custom path set — using the local default{' '}
      <code className="font-mono">{default_path}</code>. Pick a folder/drive below to change it.
    </div>
  )
}

/* ════════════════════════════════════════════════════════════════════
 * FolderPickerModal — server-side directory browser
 * (self-contained copy; walks the server FS via /api/config/list-* )
 * ════════════════════════════════════════════════════════════════════ */
function FolderPickerModal({ initial, onClose, onPick, toast }) {
  const [drives, setDrives]   = useState([])
  const [path, setPath]       = useState(initial || '')
  const [folders, setFolders] = useState([])
  const [parent, setParent]   = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState('')
  const [creating, setCreating] = useState(false)
  const [newName, setNewName]   = useState('')

  useEffect(() => {
    api.listDrives()
      .then(r => setDrives(r.drives || []))
      .catch(e => setError(e?.message || 'Could not list drives'))
    if (initial) listAt(initial).catch(() => setPath(''))
  }, [])  // eslint-disable-line

  const listAt = async (p) => {
    setLoading(true); setError('')
    try {
      const r = await api.listDir(p)
      setPath(r.path); setParent(r.parent); setFolders(r.folders || [])
    } catch (e) {
      setError(e?.response?.data?.message || e?.message || 'Could not read directory')
    } finally { setLoading(false) }
  }

  const goBack = () => { if (parent) listAt(parent); else { setPath(''); setFolders([]); setParent(null) } }

  const createNew = async () => {
    if (!path) { setError('Pick a parent folder first'); return }
    if (!newName.trim()) { setError('Enter a folder name'); return }
    setCreating(true); setError('')
    try {
      const r = await api.createDir({ parent: path, name: newName.trim() })
      toast?.success?.('Folder created')
      setNewName('')
      await listAt(r.path)
    } catch (e) { setError(e?.response?.data?.message || e?.message || 'Could not create folder') }
    finally { setCreating(false) }
  }

  const crumbs = (() => {
    if (!path) return []
    const norm = path.replace(/\\/g, '/').replace(/\/+$/, '')
    const parts = norm.split('/').filter(Boolean)
    const sep = path.includes('\\') ? '\\' : '/'
    const out = []
    let acc = ''
    parts.forEach((p, i) => {
      if (i === 0 && /^[A-Za-z]:$/.test(p)) acc = p + sep
      else acc = acc ? `${acc}${sep}${p}` : `${sep}${p}`
      out.push({ label: p, path: acc.replace(/\\\\/g, '\\') })
    })
    return out
  })()

  return (
    <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white dark:bg-slate-800 rounded-xl shadow-2xl w-full max-w-3xl max-h-[80vh] flex flex-col" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between p-4 border-b border-slate-200 dark:border-slate-700">
          <div className="flex items-center gap-2">
            <FolderOpen size={20} className="text-blue-600" />
            <h3 className="text-lg font-bold">Pick a folder</h3>
          </div>
          <button onClick={onClose} className="btn-icon" title="Close"><X size={18} /></button>
        </div>

        <div className="p-3 border-b border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40">
          <div className="flex items-center gap-2 flex-wrap">
            <button className="btn-secondary text-sm flex items-center gap-1" onClick={goBack} disabled={!parent && !path}>
              <ChevronLeft size={14} /> Back
            </button>
            <button className="btn-secondary text-sm flex items-center gap-1" onClick={() => { setPath(''); setFolders([]); setParent(null) }}>
              <HardDrive size={14} /> Drives
            </button>
            <div className="flex-1 flex items-center gap-1 flex-wrap text-sm font-mono">
              {crumbs.map((c, i) => (
                <span key={i} className="flex items-center gap-1">
                  {i > 0 && <span className="text-slate-400">›</span>}
                  <button className="px-1.5 py-0.5 rounded hover:bg-blue-100 dark:hover:bg-slate-700 text-blue-700 dark:text-blue-300" onClick={() => listAt(c.path)}>
                    {c.label}
                  </button>
                </span>
              ))}
            </div>
          </div>
          {path && (
            <div className="flex items-center gap-2 mt-2">
              <input
                className="input-field text-sm flex-1"
                placeholder="New folder name (e.g. recordings)"
                value={newName}
                onChange={e => setNewName(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') createNew() }}
              />
              <button className="btn-primary text-sm flex items-center gap-1" onClick={createNew} disabled={creating || !newName.trim()}>
                <FolderPlus size={14} /> {creating ? 'Creating…' : 'New Folder'}
              </button>
            </div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-3">
          {error && (
            <div className="px-3 py-2 mb-3 rounded bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300 text-sm">{error}</div>
          )}
          {loading ? (
            <div className="text-center py-12 text-slate-500">Loading…</div>
          ) : !path ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {drives.map(d => (
                <button key={d.path}
                        className="flex items-center gap-3 p-3 rounded-lg border border-slate-200 dark:border-slate-700 hover:border-blue-500 hover:bg-blue-50 dark:hover:bg-slate-700/50 text-left transition"
                        onClick={() => listAt(d.path)}>
                  <HardDrive size={20} className="text-slate-500" />
                  <div className="flex-1 min-w-0">
                    <div className="font-bold text-sm">{d.label}</div>
                    <div className="text-xs text-slate-400 font-mono truncate">{d.path}</div>
                  </div>
                  {d.free_gb != null && (
                    <span className={`text-xs font-bold ${d.free_gb < 10 ? 'text-red-500' : 'text-emerald-600'}`}>{d.free_gb} GB free</span>
                  )}
                </button>
              ))}
              {drives.length === 0 && <div className="col-span-2 text-center py-12 text-slate-400">No drives detected.</div>}
            </div>
          ) : (
            folders.length === 0 ? (
              <div className="text-center py-12 text-slate-400 italic">
                No sub-folders here.
                <div className="text-xs mt-1">Use <strong>+ New Folder</strong> above, or <strong>Use this folder</strong> below to pick this one.</div>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {folders.map(f => (
                  <button key={f.path}
                          className="flex items-center gap-3 p-2.5 rounded-lg border border-slate-200 dark:border-slate-700 hover:border-blue-500 hover:bg-blue-50 dark:hover:bg-slate-700/50 text-left transition"
                          onClick={() => listAt(f.path)}>
                    <Folder size={18} className="text-amber-500 flex-shrink-0" />
                    <span className="text-sm font-mono truncate">{f.name}</span>
                  </button>
                ))}
              </div>
            )
          )}
        </div>

        <div className="p-3 border-t border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 flex items-center gap-2 flex-wrap">
          <div className="flex-1 min-w-0">
            <div className="text-xs text-slate-500 mb-0.5">Selected</div>
            <code className="text-sm font-mono bg-white dark:bg-slate-800 px-2 py-1 rounded border border-slate-200 dark:border-slate-700 inline-block max-w-full truncate">
              {path || '(none)'}
            </code>
          </div>
          <button className="btn-secondary text-sm" onClick={onClose}>Cancel</button>
          <button className="btn-primary text-sm flex items-center gap-1" onClick={() => onPick(path)} disabled={!path}>
            <Check size={14} /> Use this folder
          </button>
        </div>
      </div>
    </div>
  )
}
