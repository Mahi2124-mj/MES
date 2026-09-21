import { useState, useEffect, useMemo } from 'react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'
import { useCameraFrame } from '../../hooks/useCameraFrame'
import {
  CameraOff, RefreshCw, LayoutGrid, Rows3, MapPin,
  GitBranch, Cpu, ChevronDown, ChevronRight, Maximize2, X
} from 'lucide-react'

// ─── Single Camera Card ──────────────────────────────────────────────────────
function CameraCard({ m, frameTick, onFullscreen }) {
  const { src, offline, phase, onStreamError } = useCameraFrame(m.camera_id, 2000)

  if (!m.has_camera) {
    return (
      <div className="glass dark:bg-slate-800/50 dark:border-slate-700 overflow-hidden rounded-xl">
        <div className="aspect-video bg-slate-100 dark:bg-slate-800 flex flex-col items-center justify-center text-slate-400 gap-2">
          <CameraOff size={28} className="opacity-40" />
          <span className="text-xs font-semibold opacity-60">No Camera</span>
        </div>
        <div className="px-3 py-2 border-t border-gray-100 dark:border-slate-700">
          <p className="text-xs font-semibold text-gray-700 dark:text-slate-200 truncate">{m.machine_name}</p>
          <p className="text-[10px] text-gray-400 truncate">{m.zone_name} · {m.line_name}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="glass dark:bg-slate-800/50 dark:border-slate-700 overflow-hidden rounded-xl group relative">
      {/* Top overlay */}
      <div className="absolute top-0 left-0 right-0 z-10 p-2 bg-gradient-to-b from-black/70 to-transparent flex justify-between items-start pointer-events-none">
        <div className="flex-1 min-w-0 mr-2">
          <p className="text-white font-semibold text-xs drop-shadow leading-tight truncate">{m.machine_name}</p>
          <div className="flex gap-1 mt-0.5 flex-wrap">
            <span className="inline-flex items-center gap-0.5 text-[9px] font-semibold text-white/80 bg-blue-600/60 rounded px-1 py-0.5">{m.zone_name}</span>
            <span className="inline-flex items-center gap-0.5 text-[9px] font-semibold text-white/80 bg-purple-600/60 rounded px-1 py-0.5">{m.line_name}</span>
          </div>
        </div>
        <div className="flex items-center gap-1.5 pointer-events-auto">
          {m.recording
            ? <span className="flex items-center gap-1 text-[9px] font-bold text-red-300"><span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse inline-block" />REC</span>
            : <span className="flex items-center gap-1 text-[9px] font-bold text-green-300"><span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse inline-block" />LIVE</span>
          }
          <button
            onClick={() => onFullscreen(m)}
            className="text-white/70 hover:text-white transition-colors p-0.5 rounded hover:bg-white/20"
          >
            <Maximize2 size={11} />
          </button>
        </div>
      </div>

      {/* Video area */}
      <div className="aspect-video bg-slate-900 w-full relative overflow-hidden">
        {src && (
          <img
            key={phase === 'stream' ? 'mjpeg' : src}
            src={src}
            alt={m.camera_name}
            className="w-full h-full object-cover"
            onError={phase === 'stream' ? onStreamError : undefined}
          />
        )}
        {offline && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-500 bg-slate-900/90">
            <CameraOff size={28} className="mb-1.5 opacity-40" />
            <span className="text-xs font-semibold opacity-60">Stream Offline</span>
            <span className="text-[10px] opacity-40 mt-0.5">{m.camera_name}</span>
          </div>
        )}
        {!src && !offline && (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="w-5 h-5 border-2 border-slate-500 border-t-blue-400 rounded-full animate-spin" />
          </div>
        )}
      </div>

      {/* Bottom info */}
      <div className="px-3 py-1.5 border-t border-white/10 dark:border-slate-700/60">
        <div className="flex items-center justify-between">
          <span className="text-[10px] text-gray-500 dark:text-slate-400 truncate">{m.camera_name}</span>
          {m.cycle_number && <span className="text-[9px] font-mono text-blue-500">Cycle #{m.cycle_number}</span>}
        </div>
      </div>
    </div>
  )
}

// ─── Fullscreen Modal ────────────────────────────────────────────────────────
function FullscreenModal({ m, onClose }) {
  const { src, offline, phase, onStreamError } = useCameraFrame(m.camera_id, 2000)

  useEffect(() => {
    const esc = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-50 bg-black/90 backdrop-blur-sm flex flex-col items-center justify-center p-4" onClick={onClose}>
      <div className="w-full max-w-5xl" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-white font-bold text-lg">{m.machine_name}</h2>
            <div className="flex gap-2 mt-1">
              <span className="inline-flex items-center gap-1 text-xs font-semibold text-white/70 bg-blue-600/50 rounded px-2 py-0.5">
                <MapPin size={10} /> {m.zone_name}
              </span>
              <span className="inline-flex items-center gap-1 text-xs font-semibold text-white/70 bg-purple-600/50 rounded px-2 py-0.5">
                <GitBranch size={10} /> {m.line_name}
              </span>
              <span className="inline-flex items-center gap-1 text-xs font-semibold text-white/70 bg-slate-600/50 rounded px-2 py-0.5">
                <Cpu size={10} /> {m.camera_name}
              </span>
            </div>
          </div>
          <button onClick={onClose} className="text-white/60 hover:text-white p-2 rounded-lg hover:bg-white/10 transition-colors">
            <X size={22} />
          </button>
        </div>

        {/* Feed */}
        <div className="relative rounded-xl overflow-hidden bg-slate-900 w-full" style={{ aspectRatio: '16/9' }}>
          {src && (
            <img
              key={phase === 'stream' ? 'mjpeg' : src}
              src={src}
              alt={m.camera_name}
              className="w-full h-full object-contain"
              onError={phase === 'stream' ? onStreamError : undefined}
            />
          )}
          {offline && (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-500 bg-slate-900/90">
              <CameraOff size={48} className="mb-3 opacity-40" />
              <span className="text-sm font-semibold opacity-60">Stream Offline</span>
            </div>
          )}
          {!src && !offline && (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="w-8 h-8 border-2 border-slate-500 border-t-blue-400 rounded-full animate-spin" />
            </div>
          )}

          {/* Live badge */}
          <div className="absolute top-3 left-3">
            {m.recording
              ? <span className="flex items-center gap-1.5 text-xs font-bold text-red-300 bg-black/60 rounded-full px-2.5 py-1"><span className="w-2 h-2 rounded-full bg-red-500 animate-pulse inline-block" />REC</span>
              : <span className="flex items-center gap-1.5 text-xs font-bold text-green-300 bg-black/60 rounded-full px-2.5 py-1"><span className="w-2 h-2 rounded-full bg-green-400 animate-pulse inline-block" />LIVE</span>
            }
          </div>
        </div>
        <p className="text-center text-white/30 text-xs mt-2">Press Esc or click outside to close</p>
      </div>
    </div>
  )
}

// ─── Group Header ────────────────────────────────────────────────────────────
const GROUP_COLOR_CLS = {
  blue:   'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400',
  purple: 'bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400',
  green:  'bg-green-100 dark:bg-green-900/30 text-green-600 dark:text-green-400',
}

function GroupHeader({ icon: Icon, label, count, color = 'blue', collapsed, onToggle }) {
  const iconCls = GROUP_COLOR_CLS[color] || GROUP_COLOR_CLS.blue
  return (
    <button
      onClick={onToggle}
      className="w-full flex items-center gap-2.5 px-4 py-2.5 rounded-xl bg-white dark:bg-slate-800 border border-gray-200 dark:border-slate-700 hover:bg-gray-50 dark:hover:bg-slate-700/60 transition-colors text-left"
    >
      {collapsed ? <ChevronRight size={14} className="text-gray-400 flex-shrink-0" /> : <ChevronDown size={14} className="text-gray-400 flex-shrink-0" />}
      <span className={`flex items-center justify-center w-6 h-6 rounded-md flex-shrink-0 ${iconCls}`}>
        <Icon size={13} />
      </span>
      <span className="font-semibold text-sm text-gray-800 dark:text-slate-100 flex-1">{label}</span>
      <span className="text-xs text-gray-400 bg-gray-100 dark:bg-slate-700 px-2 py-0.5 rounded-full">{count} cam{count !== 1 ? 's' : ''}</span>
    </button>
  )
}

// ─── Main Component ──────────────────────────────────────────────────────────
const COL_OPTIONS = [
  { label: '1×', value: 1, cls: 'grid-cols-1' },
  { label: '2×', value: 2, cls: 'grid-cols-1 sm:grid-cols-2' },
  { label: '3×', value: 3, cls: 'grid-cols-1 sm:grid-cols-2 xl:grid-cols-3' },
  { label: '4×', value: 4, cls: 'grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4' },
  { label: '6×', value: 6, cls: 'grid-cols-2 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-6' },
]

export default function CameraGrid() {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)
  const [frameTick, setFrameTick] = useState(Date.now())
  const [filterZone, setFilterZone] = useState('')
  const [filterLine, setFilterLine] = useState('')
  const [filterMachine, setFilterMachine] = useState('')
  const [groupBy, setGroupBy] = useState('zone') // 'flat' | 'zone' | 'line'
  const [cols, setCols] = useState(3)
  const [showOffline, setShowOffline] = useState(false)
  const [collapsed, setCollapsed] = useState({})
  const [fullscreen, setFullscreen] = useState(null)
  const toast = useToast()

  const load = async () => {
    setLoading(true)
    try { setData(await api.getCameraGrid()) }
    catch { toast.error('Error loading cameras') }
    setLoading(false)
  }

  useEffect(() => { load() }, [])
  useEffect(() => {
    const t = setInterval(() => setFrameTick(Date.now()), 2000)
    return () => clearInterval(t)
  }, [])

  // ── Derived filter options from data ──────────────────────────────────────
  const uniqueZones = useMemo(() => {
    const seen = new Map()
    data.forEach(m => { if (!seen.has(m.zone_id)) seen.set(m.zone_id, m.zone_name) })
    return [...seen.entries()].map(([id, name]) => ({ id, name }))
  }, [data])

  const uniqueLines = useMemo(() => {
    const seen = new Map()
    data
      .filter(m => !filterZone || m.zone_id === filterZone)
      .forEach(m => { if (!seen.has(m.line_id)) seen.set(m.line_id, m.line_name) })
    return [...seen.entries()].map(([id, name]) => ({ id, name }))
  }, [data, filterZone])

  const uniqueMachines = useMemo(() => {
    return data.filter(m => {
      if (filterZone && m.zone_id !== filterZone) return false
      if (filterLine && m.line_id !== filterLine) return false
      return true
    })
  }, [data, filterZone, filterLine])

  // ── Filtered list ─────────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    return data.filter(m => {
      if (!showOffline && !m.has_camera) return false
      if (filterZone && m.zone_id !== filterZone) return false
      if (filterLine && m.line_id !== filterLine) return false
      if (filterMachine && m.machine_id !== filterMachine) return false
      return true
    })
  }, [data, showOffline, filterZone, filterLine, filterMachine])

  // ── Grouped ───────────────────────────────────────────────────────────────
  const grouped = useMemo(() => {
    if (groupBy === 'flat') return [{ key: 'all', label: 'All Cameras', icon: LayoutGrid, color: 'blue', items: filtered }]
    const map = new Map()
    filtered.forEach(m => {
      const key = groupBy === 'zone' ? m.zone_id : m.line_id
      const label = groupBy === 'zone' ? m.zone_name : m.line_name
      const parent = groupBy === 'line' ? ` · ${m.zone_name}` : ''
      if (!map.has(key)) map.set(key, { key, label: label + parent, icon: groupBy === 'zone' ? MapPin : GitBranch, color: groupBy === 'zone' ? 'blue' : 'purple', items: [] })
      map.get(key).items.push(m)
    })
    return [...map.values()]
  }, [filtered, groupBy])

  const colCfg = COL_OPTIONS.find(o => o.value === cols) || COL_OPTIONS[2]
  const cameraCount = filtered.filter(m => m.has_camera).length
  const totalCount = data.filter(m => m.has_camera).length

  const toggleCollapse = key => setCollapsed(p => ({ ...p, [key]: !p[key] }))

  return (
    <div className="space-y-4 h-full flex flex-col">
      {fullscreen && <FullscreenModal m={fullscreen} onClose={() => setFullscreen(null)} />}

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="flex justify-between items-start shrink-0 flex-wrap gap-2">
        <div>
          <h1 className="page-title">Live Camera Grid</h1>
          <p className="page-subtitle">Real-time factory monitoring — {cameraCount} of {totalCount} cameras shown</p>
        </div>
        <button onClick={load} className="btn-secondary flex items-center gap-1.5">
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {/* ── Toolbar ────────────────────────────────────────────────────── */}
      <div className="glass dark:bg-slate-800/50 dark:border-slate-700 rounded-xl px-4 py-3 space-y-3 shrink-0">
        {/* Row 1: Filters */}
        <div className="flex gap-3 flex-wrap items-center">
          <span className="text-xs font-bold text-gray-500 dark:text-slate-400 uppercase tracking-wider">Filter</span>

          {/* Zone */}
          <div className="flex items-center gap-1.5">
            <MapPin size={12} className="text-blue-500" />
            <select
              className="input-field py-1 text-xs w-36"
              value={filterZone}
              onChange={e => { setFilterZone(e.target.value); setFilterLine(''); setFilterMachine('') }}
            >
              <option value="">All Zones</option>
              {uniqueZones.map(z => <option key={z.id} value={z.id}>{z.name}</option>)}
            </select>
          </div>

          {/* Line */}
          <div className="flex items-center gap-1.5">
            <GitBranch size={12} className="text-purple-500" />
            <select
              className="input-field py-1 text-xs w-36"
              value={filterLine}
              onChange={e => { setFilterLine(e.target.value); setFilterMachine('') }}
              disabled={!filterZone}
            >
              <option value="">All Lines</option>
              {uniqueLines.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
          </div>

          {/* Machine */}
          <div className="flex items-center gap-1.5">
            <Cpu size={12} className="text-emerald-500" />
            <select
              className="input-field py-1 text-xs w-40"
              value={filterMachine}
              onChange={e => setFilterMachine(e.target.value)}
              disabled={!filterZone && !filterLine}
            >
              <option value="">All Machines</option>
              {uniqueMachines.map(m => <option key={m.machine_id} value={m.machine_id}>{m.machine_name}</option>)}
            </select>
          </div>

          {(filterZone || filterLine || filterMachine) && (
            <button
              className="text-xs text-blue-600 hover:underline"
              onClick={() => { setFilterZone(''); setFilterLine(''); setFilterMachine('') }}
            >
              Clear
            </button>
          )}

          {/* Show offline toggle */}
          <label className="ml-auto flex items-center gap-2 cursor-pointer select-none">
            <div
              onClick={() => setShowOffline(p => !p)}
              className={`relative inline-flex items-center w-9 h-5 rounded-full transition-colors ${showOffline ? 'bg-blue-500' : 'bg-gray-300 dark:bg-slate-600'}`}
            >
              <span className={`absolute w-3.5 h-3.5 bg-white rounded-full shadow transition-transform ${showOffline ? 'translate-x-4' : 'translate-x-0.5'}`} />
            </div>
            <span className="text-xs text-gray-500 dark:text-slate-400">Show no-camera</span>
          </label>
        </div>

        {/* Row 2: Group + Grid size */}
        <div className="flex gap-3 flex-wrap items-center">
          <span className="text-xs font-bold text-gray-500 dark:text-slate-400 uppercase tracking-wider">Group</span>
          <div className="flex rounded-lg border border-gray-200 dark:border-slate-700 overflow-hidden">
            {[
              { val: 'flat', label: 'Flat', icon: Rows3 },
              { val: 'zone', label: 'Zone', icon: MapPin },
              { val: 'line', label: 'Line', icon: GitBranch },
            ].map(opt => (
              <button
                key={opt.val}
                onClick={() => setGroupBy(opt.val)}
                className={`flex items-center gap-1 px-3 py-1.5 text-xs font-semibold transition-colors ${groupBy === opt.val ? 'bg-blue-500 text-white' : 'text-gray-500 dark:text-slate-400 hover:bg-gray-50 dark:hover:bg-slate-700'}`}
              >
                <opt.icon size={12} /> {opt.label}
              </button>
            ))}
          </div>

          <span className="text-xs font-bold text-gray-500 dark:text-slate-400 uppercase tracking-wider ml-2">Grid</span>
          <div className="flex rounded-lg border border-gray-200 dark:border-slate-700 overflow-hidden">
            {COL_OPTIONS.map(opt => (
              <button
                key={opt.value}
                onClick={() => setCols(opt.value)}
                className={`px-2.5 py-1.5 text-xs font-semibold transition-colors ${cols === opt.value ? 'bg-blue-500 text-white' : 'text-gray-500 dark:text-slate-400 hover:bg-gray-50 dark:hover:bg-slate-700'}`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* ── Grid ───────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto space-y-5 pb-6">
        {loading ? (
          <div className={`grid ${colCfg.cls} gap-4`}>
            {[...Array(6)].map((_, i) => (
              <div key={i} className="rounded-xl overflow-hidden">
                <div className="aspect-video skeleton" />
                <div className="h-8 skeleton mt-0.5" />
              </div>
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-slate-500 gap-3">
            <CameraOff size={40} className="opacity-30" />
            <p className="font-semibold">No cameras match the current filter</p>
            <p className="text-sm opacity-60">Try changing filters or enable "Show no-camera" option</p>
          </div>
        ) : (
          grouped.map(group => (
            <div key={group.key}>
              {groupBy !== 'flat' && (
                <div className="mb-3">
                  <GroupHeader
                    icon={group.icon}
                    label={group.label}
                    count={group.items.filter(m => m.has_camera).length}
                    color={group.color}
                    collapsed={!!collapsed[group.key]}
                    onToggle={() => toggleCollapse(group.key)}
                  />
                </div>
              )}

              {!collapsed[group.key] && (
                <div className={`grid ${colCfg.cls} gap-4`}>
                  {group.items.map(m => (
                    <CameraCard
                      key={m.machine_id}
                      m={m}
                      frameTick={frameTick}
                      onFullscreen={setFullscreen}
                    />
                  ))}
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
