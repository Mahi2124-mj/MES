import { useEffect, useState } from 'react'
import { HardDrive, Save, AlertTriangle, CheckCircle2, Info, RefreshCw, Database } from 'lucide-react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'

/**
 * System Settings — primarily the video storage path.
 *
 * The recorded cycle videos can be diverted to an external HDD (e.g.
 * F:\CameraCMS_Videos) so the system drive doesn't fill up.  When the
 * `VIDEOS_DIR` environment variable is set (via start_all.bat), it
 * overrides this UI setting and the field is locked.
 */
export default function SystemSettings() {
  const [loading, setLoading]     = useState(true)
  const [saving, setSaving]       = useState(false)
  const [path, setPath]           = useState('')
  const [effective, setEffective] = useState('')
  const [envLocked, setEnvLocked] = useState(false)
  const [syncing, setSyncing]     = useState(false)
  const [lastSync, setLastSync]   = useState(null)   // { zones, lines, machines, cameras_preserved, cameras_orphaned }
  const toast = useToast()

  const load = async () => {
    setLoading(true)
    try {
      const s = await api.getSettings()
      setPath(s?.videos_dir || '')
      setEffective(s?.videos_dir_effective || '')
      setEnvLocked(!!s?.videos_dir_env_locked)
    } catch (e) {
      toast.error('Could not load settings')
    }
    setLoading(false)
  }
  useEffect(() => { load() }, [])

  const save = async (e) => {
    e.preventDefault()
    setSaving(true)
    try {
      const out = await api.saveSettings({ videos_dir: path.trim() })
      setEffective(out?.videos_dir_effective || '')
      toast.success('Storage path saved')
    } catch (e) {
      toast.error(e?.response?.data?.message || e.message || 'Save failed')
    }
    setSaving(false)
  }

  const reset = () => setPath('')

  const syncMes = async () => {
    if (!confirm('Pull Zones / Lines / Machines from MES Postgres? Local zones.json will be REPLACED with whatever MES has. Camera assignments are preserved when machines still match.')) return
    setSyncing(true)
    try {
      const out = await api.syncFromMes()
      setLastSync(out)
      toast.success(`Synced ${out?.zones || 0} zones, ${out?.lines || 0} lines, ${out?.machines || 0} machines`)
    } catch (e) {
      toast.error(e?.response?.data?.message || e.message || 'Sync failed')
    }
    setSyncing(false)
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="page-title">System Settings</h1>
        <p className="page-subtitle">Where cycle videos are stored, plus other system-wide options.</p>
      </div>

      <div className="glass p-6 max-w-2xl">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-xl bg-blue-500/10 text-blue-500 flex items-center justify-center">
            <HardDrive size={20} />
          </div>
          <div>
            <h3 className="text-base font-bold">Video Storage Path</h3>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Absolute folder where every recorded cycle .mp4 is written.
              Use an external HDD path (e.g. <code className="font-mono">F:\CameraCMS_Videos</code>) to keep the system drive free.
            </p>
          </div>
        </div>

        {envLocked && (
          <div className="mb-4 flex items-start gap-2 p-3 rounded-lg bg-amber-500/10 text-amber-700 dark:text-amber-400 text-xs">
            <AlertTriangle size={14} className="flex-shrink-0 mt-0.5" />
            <span>
              <strong>VIDEOS_DIR</strong> environment variable is currently
              forcing the path. Edit <code className="font-mono">start_all.bat</code> to change it,
              or unset the variable to use the value below.
            </span>
          </div>
        )}

        <form onSubmit={save} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-600 dark:text-slate-400 mb-1">
              Storage Folder (absolute path)
            </label>
            <input
              type="text"
              className="input-field font-mono text-sm disabled:opacity-50"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="F:\CameraCMS_Videos"
              disabled={loading || saving || envLocked}
            />
            <p className="mt-1 text-[11px] text-slate-400">
              Leave blank to use the default: <code className="font-mono">backend/videos</code>
            </p>
          </div>

          {effective && (
            <div className="flex items-start gap-2 p-3 rounded-lg bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 text-xs">
              <CheckCircle2 size={14} className="flex-shrink-0 mt-0.5" />
              <span>
                Currently writing to: <code className="font-mono">{effective}</code>
              </span>
            </div>
          )}

          <div className="flex items-start gap-2 p-3 rounded-lg bg-slate-100 dark:bg-slate-800/50 text-slate-600 dark:text-slate-400 text-xs">
            <Info size={14} className="flex-shrink-0 mt-0.5" />
            <span>
              Changes apply on the very next cycle — no service restart needed.
              The drive must already be mounted; otherwise the save is rejected.
            </span>
          </div>

          <div className="flex items-center justify-end gap-2 pt-2">
            <button type="button" onClick={reset} disabled={saving || envLocked}
                    className="btn-secondary disabled:opacity-50">
              Reset to Default
            </button>
            <button type="submit" disabled={saving || envLocked}
                    className="btn-primary disabled:opacity-50">
              <Save size={16} />
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
        </form>
      </div>

      {/* MES sync card — pulls Zones / Lines / Machines from the MES
          Postgres into the CMS local store.  This is the single click
          that keeps both systems aligned: any structural change made on
          the MES side (new line, renamed machine, etc.) flows in here
          on demand. */}
      <div className="glass p-6 max-w-2xl">
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-xl bg-indigo-500/10 text-indigo-500 flex items-center justify-center">
            <Database size={20} />
          </div>
          <div>
            <h3 className="text-base font-bold">Sync from MES</h3>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Pull the latest <strong>Zones, Lines, and Machines</strong> from the MES Postgres
              database. Camera assignments on each machine are preserved automatically when the
              machine name still matches.
            </p>
          </div>
        </div>

        <div className="flex items-start gap-2 p-3 rounded-lg bg-slate-100 dark:bg-slate-800/50 text-slate-600 dark:text-slate-400 text-xs mb-4">
          <Info size={14} className="flex-shrink-0 mt-0.5" />
          <span>
            MES is the source of truth. Add/rename/delete a machine in MES first,
            then click Sync here to bring CMS into alignment. Local cameras and bindings
            are NOT touched.
          </span>
        </div>

        {lastSync && (
          <div className="flex items-start gap-2 p-3 rounded-lg bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 text-xs mb-4">
            <CheckCircle2 size={14} className="flex-shrink-0 mt-0.5" />
            <span>
              Last sync: <strong>{lastSync.zones}</strong> zones, <strong>{lastSync.lines}</strong> lines,
              <strong> {lastSync.machines}</strong> machines.&nbsp;
              <strong>{lastSync.cameras_preserved || 0}</strong> camera assignment(s) preserved
              {lastSync.cameras_orphaned ? `, ${lastSync.cameras_orphaned} orphaned (machine no longer exists in MES)` : ''}.
            </span>
          </div>
        )}

        <div className="flex justify-end">
          <button type="button" onClick={syncMes} disabled={syncing}
                  className="btn-primary disabled:opacity-50">
            <RefreshCw size={16} className={syncing ? 'animate-spin' : ''} />
            {syncing ? 'Syncing...' : 'Sync from MES'}
          </button>
        </div>
      </div>
    </div>
  )
}
