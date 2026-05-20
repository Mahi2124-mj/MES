import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronRight, AlertCircle } from 'lucide-react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'

/**
 * MachineMaster — list view backed by MES Postgres (mes_plc_configs).
 *
 * Click any row → /admin/machines/:id  (MachineDetail) where the camera
 * binding can be edited.  MES machine identity / PLC config / trigger
 * fields are read-only here — that data is owned by MES Admin Panel.
 *
 * 2026-05-18 — Removed "Add Machine" button per operator spec: "mes me
 * machines set kri to cms me machine add nhi krunga m ok auto details
 * vha aajaye bs camera details add and update ka option de".  Machines
 * appear here automatically as they're added in MES.  CMS scope is
 * purely camera assignment + recording binding.
 */
export default function MachineMaster() {
  const navigate = useNavigate()
  const toast = useToast()
  const [rows, setRows] = useState([])
  const [cameras, setCameras] = useState([])
  const [loading, setLoading] = useState(true)
  const [filterZone, setFilterZone] = useState('')
  const [filterLine, setFilterLine] = useState('')
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const [machines, cams] = await Promise.all([
        api.getMesMachines(),
        api.getCameras(),
      ])
      setRows(Array.isArray(machines) ? machines : [])
      setCameras(cams || [])
    } catch (e) {
      setError(e?.message || 'MES backend unreachable on :8080 — verify Phase2 is running')
    }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  // Cascading filter
  const zones = useMemo(() => {
    const seen = new Map()
    rows.forEach(r => { if (r.zone_name && !seen.has(r.zone_name)) seen.set(r.zone_name, true) })
    return Array.from(seen.keys()).sort()
  }, [rows])

  const linesForFilter = useMemo(() => {
    const seen = new Map()
    rows.filter(r => !filterZone || r.zone_name === filterZone)
        .forEach(r => { if (r.line_name && !seen.has(r.line_name)) seen.set(r.line_name, true) })
    return Array.from(seen.keys()).sort()
  }, [rows, filterZone])

  const displayed = rows.filter(r => {
    if (filterZone && r.zone_name !== filterZone) return false
    if (filterLine && r.line_name !== filterLine) return false
    return true
  })

  const camById = (id) => cameras.find(c => c.id === id)

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="page-title">Machines</h1>
          <p className="page-subtitle">
            Click any row to assign / update its camera.
            Machines are auto-synced from MES Admin Panel — they appear here when added there.
          </p>
        </div>
      </div>

      {error && (
        <div className="rounded-xl border border-red-300 bg-red-50 dark:bg-red-900/20 dark:border-red-700 p-3 flex items-start gap-2 text-sm">
          <AlertCircle size={16} className="flex-shrink-0 mt-0.5 text-red-600 dark:text-red-400" />
          <span className="text-red-700 dark:text-red-200">{error}</span>
        </div>
      )}

      {/* Filter bar */}
      <div className="glass dark:bg-slate-800/50 dark:border-slate-700 px-4 py-3 flex gap-3 items-center flex-wrap">
        <span className="text-xs font-semibold text-gray-500 dark:text-slate-400">Filter:</span>
        <select className="input-field py-1 text-xs w-40" value={filterZone} onChange={e => { setFilterZone(e.target.value); setFilterLine('') }}>
          <option value="">All Zones</option>
          {zones.map(z => <option key={z} value={z}>{z}</option>)}
        </select>
        <select className="input-field py-1 text-xs w-48" value={filterLine} onChange={e => setFilterLine(e.target.value)} disabled={!filterZone && linesForFilter.length === 0}>
          <option value="">All Lines</option>
          {linesForFilter.map(l => <option key={l} value={l}>{l}</option>)}
        </select>
        {(filterZone || filterLine) && (
          <button className="text-xs text-blue-600 hover:underline" onClick={() => { setFilterZone(''); setFilterLine('') }}>Clear</button>
        )}
        <span className="ml-auto text-xs text-gray-400">{displayed.length} of {rows.length} machines</span>
      </div>

      {/* Table */}
      <div className="glass overflow-hidden dark:bg-slate-800/50 dark:border-slate-700">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-gray-50 dark:bg-slate-800 border-b border-gray-200 dark:border-slate-700">
              <tr>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs w-8">#</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs">Machine</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs">Zone / Line</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs">PLC</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs">Trigger</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs">Camera (IP)</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs w-8"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-slate-700/60">
              {loading ? (
                <tr><td colSpan={7} className="px-4 py-3"><div className="h-4 skeleton w-full" /></td></tr>
              ) : displayed.length === 0 ? (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-slate-500">
                  No machines visible. Add them in MES Admin Panel — they'll appear here automatically.
                </td></tr>
              ) : displayed.map((m, idx) => {
                const isSub = !!m.parent_plc_id
                const trig = isSub ? 'SUB' : 'MAIN'
                const cam  = camById(m.nf2_camera_id)
                return (
                  <tr key={m.id}
                      className="hover:bg-blue-50/30 dark:hover:bg-blue-900/10 transition-colors cursor-pointer"
                      onClick={() => navigate(`/admin/machines/${m.id}`)}>
                    <td className="px-4 py-3 text-xs text-gray-400">{idx + 1}</td>
                    <td className="px-4 py-3 font-medium text-gray-800 dark:text-slate-100">
                      {m.machine_name}
                      {m.machine_seq != null && <span className="ml-2 text-xs text-gray-400">#{m.machine_seq}</span>}
                    </td>
                    <td className="px-4 py-3 text-xs">
                      <div><span className="font-semibold text-blue-700 dark:text-blue-300">{m.zone_name || '—'}</span></div>
                      <div className="text-purple-700 dark:text-purple-300">{m.line_name || '—'}</div>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs">
                      {m.plc_ip ? `${m.plc_ip}:${m.plc_port}` : <span className="text-gray-400">—</span>}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`badge ${trig === 'SUB' ? 'badge-info' : 'badge-success'}`}>{trig}</span>
                      <div className="text-xs text-gray-500 font-mono mt-1">
                        {trig === 'MAIN'
                          ? [m.ok_bit_address, m.ng_bit_address].filter(Boolean).join(' / ') || '—'
                          : m.process_seq_address || '—'}
                      </div>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs">
                      {cam ? <>{cam.ip}{cam.port ? `:${cam.port}` : ''}</> : <span className="text-amber-600 dark:text-amber-400">Unassigned</span>}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-400">
                      <ChevronRight size={16} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <div className="px-4 py-2 bg-gray-50 dark:bg-slate-800 border-t border-gray-100 dark:border-slate-700/60 text-xs text-gray-400">
          {rows.length} machine{rows.length !== 1 ? 's' : ''} total · source: MES Postgres
        </div>
      </div>
    </div>
  )
}
