import { useState, useEffect, useMemo } from 'react'
import { Download, RefreshCw, Filter, X, ChevronLeft, ChevronRight, Play } from 'lucide-react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'

const PAGE_SIZE = 50
const SHIFTS = ['Morning', 'Evening', 'Night']

function DurationBadge({ duration, target }) {
  if (!duration && duration !== 0) return <span className="text-gray-400">—</span>
  const over = target > 0 && Number(duration) > Number(target)
  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold ${
      over
        ? 'bg-red-50 text-red-700 border border-red-200 dark:bg-red-900/20 dark:text-red-300 dark:border-red-800'
        : 'bg-green-50 text-green-700 border border-green-200 dark:bg-green-900/20 dark:text-green-300 dark:border-green-800'
    }`}>
      {duration}s {over && target > 0 ? <span className="opacity-70">+{Number(duration)-Number(target)}s</span> : null}
    </span>
  )
}

function StatCard({ label, value, sub, color = 'blue' }) {
  const colors = {
    blue:  'bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300',
    green: 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300',
    red:   'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300',
    amber: 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-300',
  }
  return (
    <div className={`rounded-xl px-4 py-3 ${colors[color]}`}>
      <p className="text-xs font-semibold opacity-70 uppercase tracking-wider">{label}</p>
      <p className="text-2xl font-bold mt-0.5">{value}</p>
      {sub && <p className="text-xs opacity-60 mt-0.5">{sub}</p>}
    </div>
  )
}

export default function CycleTimeReport() {
  const [allData, setAllData] = useState([])
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const toast = useToast()

  // Filters
  const [fZone, setFZone]     = useState('')
  const [fLine, setFLine]     = useState('')
  const [fMachine, setFMachine] = useState('')
  const [fShift, setFShift]   = useState('')
  const [fDateFrom, setFDateFrom] = useState('')
  const [fDateTo, setFDateTo]   = useState('')

  // Video modal
  const [videoRow, setVideoRow] = useState(null)

  const load = async () => {
    setLoading(true)
    try {
      const rows = await api.getCycleHistory({})
      setAllData(rows)
    } catch {
      toast.error('Failed to load cycle history')
    }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  // Derived filter options from data
  const uniqueZones    = useMemo(() => [...new Set(allData.map(r => r.zone_name).filter(Boolean))].sort(), [allData])
  const uniqueLines    = useMemo(() => [...new Set(allData.filter(r => !fZone || r.zone_name === fZone).map(r => r.line_name).filter(Boolean))].sort(), [allData, fZone])
  const uniqueMachines = useMemo(() => [...new Set(allData.filter(r => (!fZone || r.zone_name === fZone) && (!fLine || r.line_name === fLine)).map(r => r.machine_name).filter(Boolean))].sort(), [allData, fZone, fLine])

  // Filtered data
  const filtered = useMemo(() => {
    return allData.filter(r => {
      if (!r.machine_id && !r.machine_name) return false // skip empty rows
      if (fZone    && r.zone_name    !== fZone)    return false
      if (fLine    && r.line_name    !== fLine)    return false
      if (fMachine && r.machine_name !== fMachine) return false
      if (fShift   && r.shift        !== fShift)   return false
      if (fDateFrom && r.start_time  < fDateFrom)  return false
      if (fDateTo  && r.start_time.slice(0,10) > fDateTo) return false
      return true
    }).reverse() // newest first
  }, [allData, fZone, fLine, fMachine, fShift, fDateFrom, fDateTo])

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  // Stats
  const stats = useMemo(() => {
    if (!filtered.length) return null
    const durations = filtered.map(r => Number(r.duration)).filter(d => d > 0)
    const aboveTarget = filtered.filter(r => r.target_time > 0 && Number(r.duration) > Number(r.target_time)).length
    const avg = durations.length ? Math.round(durations.reduce((a,b)=>a+b,0) / durations.length) : 0
    const best = durations.length ? Math.min(...durations) : 0
    return {
      total: filtered.length,
      avg,
      best,
      aboveTarget,
      pct: filtered.length ? Math.round((aboveTarget / filtered.length) * 100) : 0,
    }
  }, [filtered])

  const hasFilter = fZone || fLine || fMachine || fShift || fDateFrom || fDateTo

  const clearFilters = () => {
    setFZone(''); setFLine(''); setFMachine(''); setFShift(''); setFDateFrom(''); setFDateTo('')
    setPage(1)
  }

  // Export CSV
  const exportCSV = () => {
    const headers = ['Cycle#','Machine','Part Code','Zone','Line','Shift','Start Time','End Time','Duration(s)','Target(s)','Status','File']
    const rows = filtered.map(r => [
      r.cycle_number, r.machine_name, r.part_code || '', r.zone_name, r.line_name, r.shift,
      r.start_time, r.end_time, r.duration, r.target_time || '',
      (r.target_time > 0 && Number(r.duration) > Number(r.target_time)) ? 'OVER' : 'OK',
      r.file_path
    ])
    const csv = [headers, ...rows].map(row => row.map(v => `"${String(v||'').replace(/"/g,'""')}"`).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url
    a.download = `cycle-report-${new Date().toISOString().slice(0,10)}.csv`
    a.click(); URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex justify-between items-start flex-wrap gap-2">
        <div>
          <h1 className="page-title">Cycle Time Report</h1>
          <p className="page-subtitle">Historical cycle data — {allData.length} total records</p>
        </div>
        <div className="flex gap-2">
          <button className="btn-secondary flex items-center gap-1.5" onClick={load}>
            <RefreshCw size={14}/> Refresh
          </button>
          <button className="btn-secondary flex items-center gap-1.5" onClick={exportCSV} disabled={!filtered.length}>
            <Download size={14}/> Export CSV
          </button>
        </div>
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="Total Cycles" value={stats.total} color="blue"/>
          <StatCard label="Avg Duration" value={`${stats.avg}s`} color="blue"/>
          <StatCard label="Best (Fastest)" value={`${stats.best}s`} color="green"/>
          <StatCard label="Over Target" value={`${stats.aboveTarget}`} sub={`${stats.pct}% of cycles`} color={stats.pct > 20 ? 'red' : 'amber'}/>
        </div>
      )}

      {/* Filters */}
      <div className="glass dark:bg-slate-800/50 dark:border-slate-700 rounded-xl px-4 py-3 space-y-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Filter size={13} className="text-gray-400"/>
          <span className="text-xs font-bold text-gray-500 uppercase tracking-wider">Filters</span>
          {hasFilter && (
            <button onClick={clearFilters} className="ml-auto text-xs text-blue-600 hover:underline flex items-center gap-1">
              <X size={11}/> Clear all
            </button>
          )}
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
          <select className="input-field py-1.5 text-xs" value={fZone} onChange={e=>{setFZone(e.target.value);setFLine('');setFMachine('');setPage(1)}}>
            <option value="">All Zones</option>
            {uniqueZones.map(z=><option key={z} value={z}>{z}</option>)}
          </select>
          <select className="input-field py-1.5 text-xs" value={fLine} onChange={e=>{setFLine(e.target.value);setFMachine('');setPage(1)}} disabled={!fZone}>
            <option value="">All Lines</option>
            {uniqueLines.map(l=><option key={l} value={l}>{l}</option>)}
          </select>
          <select className="input-field py-1.5 text-xs" value={fMachine} onChange={e=>{setFMachine(e.target.value);setPage(1)}} disabled={!fLine}>
            <option value="">All Machines</option>
            {uniqueMachines.map(m=><option key={m} value={m}>{m}</option>)}
          </select>
          <select className="input-field py-1.5 text-xs" value={fShift} onChange={e=>{setFShift(e.target.value);setPage(1)}}>
            <option value="">All Shifts</option>
            {SHIFTS.map(s=><option key={s} value={s}>{s}</option>)}
          </select>
          <input type="date" className="input-field py-1.5 text-xs" value={fDateFrom} onChange={e=>{setFDateFrom(e.target.value);setPage(1)}} placeholder="From"/>
          <input type="date" className="input-field py-1.5 text-xs" value={fDateTo} onChange={e=>{setFDateTo(e.target.value);setPage(1)}} placeholder="To"/>
        </div>
        {hasFilter && (
          <p className="text-xs text-gray-400">Showing {filtered.length} of {allData.filter(r=>r.machine_id||r.machine_name).length} records</p>
        )}
      </div>

      {/* Table */}
      <div className="glass overflow-hidden dark:bg-slate-800/50 dark:border-slate-700">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-gray-50 dark:bg-slate-800 border-b border-gray-200 dark:border-slate-700">
              <tr>
                <th className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">#</th>
                <th className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Cycle</th>
                <th className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Machine</th>
                <th className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Part Code</th>
                <th className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Zone</th>
                <th className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Line</th>
                <th className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Shift</th>
                <th className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Start</th>
                <th className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">End</th>
                <th className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Duration</th>
                <th className="px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">Target</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-slate-700/60">
              {loading ? (
                [...Array(5)].map((_,i) => (
                  <tr key={i}><td colSpan={11} className="px-4 py-3"><div className="h-4 skeleton w-full"/></td></tr>
                ))
              ) : paged.length === 0 ? (
                <tr><td colSpan={11} className="px-4 py-12 text-center text-slate-500">
                  {hasFilter ? 'No records match current filters.' : 'No cycle records yet.'}
                </td></tr>
              ) : paged.map((r, i) => {
                const rowNum = (page-1)*PAGE_SIZE + i + 1
                const isOver = r.target_time > 0 && Number(r.duration) > Number(r.target_time)
                return (
                  <tr key={`${r.cycle_number}-${i}`} className={`transition-colors ${isOver ? 'hover:bg-red-50/30 dark:hover:bg-red-900/10' : 'hover:bg-blue-50/20 dark:hover:bg-blue-900/10'}`}>
                    <td className="px-4 py-3 text-xs text-gray-400">{rowNum}</td>
                    <td className="px-4 py-3 font-mono text-xs font-bold text-blue-600 dark:text-blue-400">#{r.cycle_number}</td>
                    <td className="px-4 py-3 font-medium text-gray-800 dark:text-slate-100 max-w-[160px] truncate" title={r.machine_name}>{r.machine_name || '—'}</td>
                    <td className="px-4 py-3">
                      {r.part_code ? <span className="inline-flex px-2 py-0.5 rounded bg-cyan-50 dark:bg-cyan-900/20 text-cyan-700 dark:text-cyan-300 text-xs font-semibold">{r.part_code}</span> : '—'}
                    </td>
                    <td className="px-4 py-3">
                      {r.zone_name ? <span className="inline-flex px-2 py-0.5 rounded bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300 text-xs font-semibold">{r.zone_name}</span> : '—'}
                    </td>
                    <td className="px-4 py-3">
                      {r.line_name ? <span className="inline-flex px-2 py-0.5 rounded bg-purple-50 dark:bg-purple-900/20 text-purple-700 dark:text-purple-300 text-xs font-semibold">{r.line_name}</span> : '—'}
                    </td>
                    <td className="px-4 py-3">
                      {r.shift ? <span className="inline-flex px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 text-xs">{r.shift}</span> : '—'}
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-500 dark:text-slate-400 whitespace-nowrap">{r.start_time ? new Date(r.start_time).toLocaleString() : '—'}</td>
                    <td className="px-4 py-3 text-xs text-gray-500 dark:text-slate-400 whitespace-nowrap">{r.end_time ? new Date(r.end_time).toLocaleString() : '—'}</td>
                    <td className="px-4 py-3"><DurationBadge duration={r.duration} target={r.target_time}/></td>
                    <td className="px-4 py-3 text-xs text-gray-500">{r.target_time ? `${r.target_time}s` : '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="px-4 py-2.5 bg-gray-50 dark:bg-slate-800 border-t border-gray-100 dark:border-slate-700/60 flex items-center justify-between">
          <span className="text-xs text-gray-400">
            {filtered.length} record{filtered.length !== 1 ? 's' : ''}
            {totalPages > 1 ? ` · Page ${page} of ${totalPages}` : ''}
          </span>
          {totalPages > 1 && (
            <div className="flex items-center gap-1">
              <button
                className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-slate-700 text-gray-500 disabled:opacity-30"
                onClick={() => setPage(p => Math.max(1, p-1))} disabled={page === 1}
              ><ChevronLeft size={14}/></button>
              {[...Array(Math.min(5, totalPages))].map((_, i) => {
                const pg = totalPages <= 5 ? i+1 : Math.max(1, Math.min(totalPages-4, page-2)) + i
                return (
                  <button key={pg}
                    onClick={() => setPage(pg)}
                    className={`w-7 h-7 rounded-lg text-xs font-semibold ${pg===page ? 'bg-blue-500 text-white' : 'text-gray-500 hover:bg-gray-200 dark:hover:bg-slate-700'}`}
                  >{pg}</button>
                )
              })}
              <button
                className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-slate-700 text-gray-500 disabled:opacity-30"
                onClick={() => setPage(p => Math.min(totalPages, p+1))} disabled={page === totalPages}
              ><ChevronRight size={14}/></button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
