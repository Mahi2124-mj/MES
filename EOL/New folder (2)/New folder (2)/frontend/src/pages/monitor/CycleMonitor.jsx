import { useState, useEffect, useMemo } from 'react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'
import {
  Play, Square, RefreshCw, MapPin, GitBranch,
  Activity, Wifi, WifiOff, Clock, CheckCircle2, AlertCircle, Zap
} from 'lucide-react'

// ─── PLC Bit Status Panel ─────────────────────────────────────────────────────
function PlcStatusPanel({ plcs }) {
  if (!plcs || plcs.length === 0) return null
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
      {plcs.map(plc => (
        <div key={plc.id} className="glass dark:bg-slate-800/50 dark:border-slate-700 rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Activity size={14} className="text-blue-500" />
              <span className="text-sm font-bold text-gray-700 dark:text-slate-200">{plc.description || plc.ip}</span>
            </div>
            <span className={`flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full ${
              plc.connected ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                            : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'}`}>
              {plc.connected ? <Wifi size={9}/> : <WifiOff size={9}/>}
              {plc.connected ? 'Connected' : 'Offline'}
            </span>
          </div>
          <div className="text-[10px] text-gray-400 mb-3">{plc.ip}:{plc.port}</div>
          <div className="flex flex-wrap gap-2">
            {plc.bits.map(b => (
              <div key={b.bit} className={`flex flex-col items-center px-3 py-2 rounded-lg border-2 transition-all ${
                b.value === true  ? 'border-green-500 bg-green-50 dark:bg-green-900/20'
              : b.value === false ? 'border-slate-200 bg-white dark:bg-slate-800 dark:border-slate-600'
                                  : 'border-slate-100 bg-slate-50 dark:bg-slate-900'}`}>
                {/* Bit indicator */}
                <div className={`w-3 h-3 rounded-full mb-1 ${
                  b.value === true  ? 'bg-green-500 shadow-lg shadow-green-500/50 animate-pulse'
                : b.value === false ? 'bg-slate-300 dark:bg-slate-600'
                                    : 'bg-slate-200'}`} />
                <span className={`text-xs font-mono font-bold ${
                  b.value === true ? 'text-green-600 dark:text-green-400' : 'text-slate-600 dark:text-slate-400'}`}>
                  {b.bit}
                </span>
                <span className={`text-[9px] font-semibold mt-0.5 ${
                  b.value === true  ? 'text-green-600 dark:text-green-400'
                : b.value === false ? 'text-slate-400'
                                    : 'text-slate-300'}`}>
                  {b.value === true ? 'ON' : b.value === false ? 'OFF' : '—'}
                </span>
                {b.last_change && (
                  <span className="text-[8px] text-slate-400 mt-0.5">
                    {new Date(b.last_change).toLocaleTimeString()}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// ─── Duration helper ──────────────────────────────────────────────────────────
function liveDuration(startIso) {
  if (!startIso) return '—'
  const secs = Math.floor((Date.now() - new Date(startIso).getTime()) / 1000)
  if (secs < 60) return `${secs}s`
  const m = Math.floor(secs / 60), s = secs % 60
  return `${m}m ${s}s`
}

function fmtTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString()
}

// ─── Machine Row ──────────────────────────────────────────────────────────────
function MachineRow({ m, onTrigger, tick, isPlcBound }) {
  const dur = m.recording ? liveDuration(m.start_time) : null

  return (
    <tr className={`table-row-hover ${isPlcBound ? 'bg-blue-50/40 dark:bg-blue-900/10' : ''}`}>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <div>
            <p className="text-sm font-semibold text-gray-800 dark:text-slate-200">{m.machine_name}</p>
            <p className="text-[10px] text-gray-400">{m.zone_name} · {m.line_name}</p>
          </div>
          {isPlcBound && (
            <span className="flex-shrink-0 flex items-center gap-1 text-[9px] font-bold px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400 border border-blue-200 dark:border-blue-800">
              <Zap size={8}/> PLC
            </span>
          )}
        </div>
      </td>
      <td className="px-4 py-3">
        {m.recording
          ? <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-bold bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400">
              <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse inline-block"/>REC
            </span>
          : <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 inline-block"/>Idle
            </span>
        }
      </td>
      <td className="px-4 py-3 font-mono text-sm">
        {m.cycle_number ? `#${m.cycle_number}` : '—'}
      </td>
      <td className="px-4 py-3 text-sm">
        {m.recording && m.start_time ? fmtTime(m.start_time) : '—'}
      </td>
      <td className="px-4 py-3">
        {m.recording
          ? <span className="font-mono text-sm font-semibold text-orange-600 dark:text-orange-400">{dur}</span>
          : <span className="text-gray-400 text-sm">—</span>
        }
      </td>
      <td className="px-4 py-3 text-right">
        <div className="flex justify-end gap-2">
          <button
            onClick={() => onTrigger(m.machine_id, 'start')}
            disabled={m.recording}
            title="Start Cycle"
            className={`p-2 rounded-lg text-white transition-colors ${m.recording ? 'bg-slate-200 dark:bg-slate-700 cursor-not-allowed' : 'bg-emerald-500 hover:bg-emerald-600'}`}
          >
            <Play size={13}/>
          </button>
          <button
            onClick={() => onTrigger(m.machine_id, 'stop')}
            disabled={!m.recording}
            title="Stop Cycle"
            className={`p-2 rounded-lg text-white transition-colors ${!m.recording ? 'bg-slate-200 dark:bg-slate-700 cursor-not-allowed' : 'bg-red-500 hover:bg-red-600'}`}
          >
            <Square size={13}/>
          </button>
        </div>
      </td>
    </tr>
  )
}

// ─── Recent Events (from cycle history) ──────────────────────────────────────
function EventFeed({ events }) {
  if (!events || events.length === 0) {
    return <p className="text-sm text-slate-400 text-center py-6">No recent cycles</p>
  }
  return (
    <div className="space-y-2 max-h-80 overflow-y-auto">
      {events.map((e, i) => (
        <div key={i} className="flex items-start gap-3 p-3 rounded-lg bg-white dark:bg-slate-800 border border-gray-100 dark:border-slate-700">
          <div className={`mt-0.5 flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-white text-[10px] font-bold ${
            e.duration <= (e.target_time || 9999) ? 'bg-emerald-500' : 'bg-orange-500'}`}>
            {e.duration <= (e.target_time || 9999) ? <CheckCircle2 size={12}/> : <AlertCircle size={12}/>}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-semibold text-gray-800 dark:text-slate-200 truncate">
                {e.machine_name} <span className="text-gray-400 font-normal">· Cycle #{e.cycle_number}</span>
              </p>
              <span className={`flex-shrink-0 text-xs font-mono font-bold px-2 py-0.5 rounded ${
                e.duration <= (e.target_time || 9999) ? 'text-emerald-700 bg-emerald-100 dark:text-emerald-400 dark:bg-emerald-900/30'
                                                      : 'text-orange-700 bg-orange-100 dark:text-orange-400 dark:bg-orange-900/30'}`}>
                {e.duration}s
                {e.target_time ? ` / ${e.target_time}s` : ''}
              </span>
            </div>
            <p className="text-[10px] text-gray-400 mt-0.5">
              {e.zone_name} · {e.line_name} · {e.shift || '—'}
            </p>
            <p className="text-[10px] text-gray-400">
              <span className="text-green-600 dark:text-green-400">▶ {fmtTime(e.start_time)}</span>
              {' → '}
              <span className="text-red-500">■ {fmtTime(e.end_time)}</span>
            </p>
          </div>
        </div>
      ))}
    </div>
  )
}

// ─── Main ─────────────────────────────────────────────────────────────────────
export default function CycleMonitor() {
  const [machines,     setMachines]     = useState([])
  const [plcStatus,    setPlcStatus]    = useState([])
  const [history,      setHistory]      = useState([])
  const [bindings,     setBindings]     = useState([])
  const [loading,      setLoading]      = useState(true)
  const [tick,         setTick]         = useState(0)
  const [filterZone,   setFilterZone]   = useState('')
  const [filterLine,   setFilterLine]   = useState('')
  const [showAll,      setShowAll]      = useState(true)
  const toast = useToast()

  // ── Load all data ───────────────────────────────────────────────────────────
  const load = async () => {
    const [msResult, psResult, hsResult, bsResult] = await Promise.allSettled([
      api.getCycleStatus(),
      api.getPlcLiveStatus(),
      api.getCycleHistory({ limit: 30 }),
      api.getCameraConfigs(),
    ])
    if (msResult.status === 'fulfilled') setMachines(Array.isArray(msResult.value) ? msResult.value : [])
    if (psResult.status === 'fulfilled') setPlcStatus(Array.isArray(psResult.value) ? psResult.value : [])
    if (hsResult.status === 'fulfilled') setHistory(Array.isArray(hsResult.value) ? hsResult.value : [])
    if (bsResult.status === 'fulfilled') setBindings(Array.isArray(bsResult.value) ? bsResult.value : [])
    setLoading(false)
  }

  useEffect(() => {
    load()
    const refresh   = setInterval(load, 2000)      // full data refresh every 2s
    const tickTimer = setInterval(() => setTick(t => t + 1), 1000)  // live duration tick
    return () => { clearInterval(refresh); clearInterval(tickTimer) }
  }, [])

  // ── Derived filter options ──────────────────────────────────────────────────
  const zones = useMemo(() => {
    const m = new Map()
    machines.forEach(x => { if (!m.has(x.zone_id)) m.set(x.zone_id, x.zone_name) })
    return [...m.entries()].map(([id, name]) => ({ id, name }))
  }, [machines])

  const lines = useMemo(() => {
    const m = new Map()
    machines.filter(x => !filterZone || x.zone_id === filterZone)
      .forEach(x => { if (!m.has(x.line_id)) m.set(x.line_id, x.line_name) })
    return [...m.entries()].map(([id, name]) => ({ id, name }))
  }, [machines, filterZone])

  // Machine IDs bound to a PLC trigger (highlighted in table)
  // ⚠ Must be defined BEFORE `filtered` which depends on it
  const plcBound = useMemo(() => {
    const s = new Set()
    bindings.filter(b => b.plc_id).forEach(b => s.add(b.machine_id))
    return s
  }, [bindings])

  const filtered = useMemo(() => {
    const list = machines.filter(m => {
      if (!showAll && !m.recording && !m.cycle_number) return false
      if (filterZone && m.zone_id !== filterZone) return false
      if (filterLine && m.line_id !== filterLine) return false
      return true
    })
    // PLC-bound + recording machines float to the top
    return list.sort((a, b) => {
      const aScore = (plcBound.has(a.machine_id) ? 2 : 0) + (a.recording ? 1 : 0)
      const bScore = (plcBound.has(b.machine_id) ? 2 : 0) + (b.recording ? 1 : 0)
      return bScore - aScore
    })
  }, [machines, filterZone, filterLine, showAll, plcBound])

  const recording = machines.filter(m => m.recording).length

  const trigger = async (mId, action) => {
    try {
      await api.triggerCycle({ machine_id: mId, action })
      toast.success(`Cycle ${action === 'start' ? 'started' : 'stopped'}`)
      load()
    } catch (e) {
      toast.error(e.response?.data?.message || e.message || 'Trigger failed')
    }
  }

  return (
    <div className="space-y-5">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="flex justify-between items-start flex-wrap gap-2">
        <div>
          <h1 className="page-title">Cycle Monitor</h1>
          <p className="page-subtitle">
            Live PLC bit monitoring + cycle control —
            <span className="text-red-500 font-semibold ml-1">{recording} recording</span>
            {' / '}{machines.length} machines
          </p>
        </div>
        <button onClick={load} className="btn-secondary flex items-center gap-1.5">
          <RefreshCw size={13}/> Refresh
        </button>
      </div>

      {/* ── PLC Bit Status ─────────────────────────────────────────────── */}
      <div>
        <h2 className="text-sm font-bold text-gray-500 dark:text-slate-400 uppercase tracking-wider mb-2 flex items-center gap-1.5">
          <Zap size={13} className="text-yellow-500"/> PLC Live Bit Status
          <span className="text-[10px] font-normal text-gray-400 ml-1">(triggers cycle on rising edge)</span>
        </h2>
        {loading ? (
          <div className="skeleton h-28 rounded-xl"/>
        ) : (
          <PlcStatusPanel plcs={plcStatus} />
        )}
      </div>

      {/* ── Main content: machines + events ───────────────────────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">

        {/* ── Machines Table ─────────────────────────────────────────── */}
        <div className="xl:col-span-2 space-y-3">
          {/* Filters */}
          <div className="glass dark:bg-slate-800/50 dark:border-slate-700 rounded-xl px-4 py-3 flex flex-wrap gap-3 items-center">
            <span className="text-xs font-bold text-gray-500 dark:text-slate-400 uppercase tracking-wider">Filter</span>
            <div className="flex items-center gap-1.5">
              <MapPin size={12} className="text-blue-500"/>
              <select className="input-field py-1 text-xs w-36" value={filterZone}
                onChange={e => { setFilterZone(e.target.value); setFilterLine('') }}>
                <option value="">All Zones</option>
                {zones.map(z => <option key={z.id} value={z.id}>{z.name}</option>)}
              </select>
            </div>
            <div className="flex items-center gap-1.5">
              <GitBranch size={12} className="text-purple-500"/>
              <select className="input-field py-1 text-xs w-36" value={filterLine}
                onChange={e => setFilterLine(e.target.value)} disabled={!filterZone}>
                <option value="">All Lines</option>
                {lines.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
              </select>
            </div>
            {(filterZone || filterLine) && (
              <button className="text-xs text-blue-600 hover:underline"
                onClick={() => { setFilterZone(''); setFilterLine('') }}>Clear</button>
            )}
            <label className="ml-auto flex items-center gap-2 cursor-pointer select-none text-xs text-gray-500 dark:text-slate-400">
              <input type="checkbox" className="rounded" checked={showAll}
                onChange={e => setShowAll(e.target.checked)}/>
              Show all machines
            </label>
          </div>

          {/* Table */}
          <div className="glass dark:bg-slate-800/50 dark:border-slate-700 overflow-hidden rounded-xl">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
                  <tr>
                    <th className="px-4 py-3 text-xs font-semibold text-gray-500 dark:text-slate-400 uppercase tracking-wider">Machine</th>
                    <th className="px-4 py-3 text-xs font-semibold text-gray-500 dark:text-slate-400 uppercase tracking-wider">Status</th>
                    <th className="px-4 py-3 text-xs font-semibold text-gray-500 dark:text-slate-400 uppercase tracking-wider">Cycle</th>
                    <th className="px-4 py-3 text-xs font-semibold text-gray-500 dark:text-slate-400 uppercase tracking-wider">Started</th>
                    <th className="px-4 py-3 text-xs font-semibold text-gray-500 dark:text-slate-400 uppercase tracking-wider">Duration</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-gray-500 dark:text-slate-400 uppercase tracking-wider">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
                  {loading ? (
                    <tr><td colSpan={6} className="px-4 py-4">
                      <div className="skeleton h-4 w-full"/>
                    </td></tr>
                  ) : filtered.length === 0 ? (
                    <tr><td colSpan={6} className="px-4 py-8 text-center text-sm text-slate-400">
                      No machines match. Enable "Show all machines" or clear filter.
                    </td></tr>
                  ) : (
                    filtered.map(m => (
                      <MachineRow key={m.machine_id} m={m} onTrigger={trigger} tick={tick} isPlcBound={plcBound.has(m.machine_id)}/>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* ── Recent Cycle Events ─────────────────────────────────────── */}
        <div>
          <h2 className="text-sm font-bold text-gray-500 dark:text-slate-400 uppercase tracking-wider mb-3 flex items-center gap-1.5">
            <Clock size={13}/> Recent Cycles
          </h2>
          <div className="glass dark:bg-slate-800/50 dark:border-slate-700 rounded-xl p-3">
            {loading ? <div className="skeleton h-40 rounded-lg"/> : <EventFeed events={history}/>}
          </div>
        </div>
      </div>
    </div>
  )
}
