import { useEffect, useMemo, useState } from 'react'
import { GitBranch, MapPin, Video } from 'lucide-react'
import { Bar, Cell, CartesianGrid, ComposedChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../lib/api'
import { useToast } from '../context/ToastContext'

function SelectField({ label, value, onChange, options, disabled, icon: Icon }) {
  return (
    <label className="block">
      <span className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500 dark:text-slate-400">
        <Icon size={13} />
        {label}
      </span>
      <select className="input-field" value={value} onChange={onChange} disabled={disabled}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function CycleChartTooltip({ active, payload }) {
  const row = payload?.[0]?.payload
  if (!active || !row) return null

  return (
    <div className="min-w-[150px] rounded-2xl border border-slate-700 bg-slate-950/95 p-2 text-left shadow-2xl">
      <p className="text-[11px] font-semibold text-white">#{row.cycle_number} · {row.duration}s</p>
      <p className="mt-0.5 text-[11px] text-slate-300">{row.machine_name || 'Machine'}</p>
      <p className="text-[11px] text-slate-300">Start {row.start_time || '-'}</p>
      <p className="text-[11px] text-slate-300">End {row.end_time || '-'}</p>
      {row.target_time > 0 && <p className="text-[11px] text-slate-300">Target {row.target_time}s</p>}
    </div>
  )
}

function normalizeCycles(rows) {
  return rows
    .sort((a, b) => Number(a.cycle_number) - Number(b.cycle_number))
    .map((row) => {
      const duration = Number(row.duration || 0)
      const target_time = Number(row.target_time || 0)
      const isAboveTarget = target_time > 0 && duration > target_time

      return {
        ...row,
        cycleLabel: `C-${row.cycle_number}`,
        duration,
        target_time,
        barColor: isAboveTarget ? '#dc2626' : '#16a34a',
      }
    })
}

function summarizeCycles(cycles) {
  if (!cycles.length) {
    return { total: 0, best: 0, target: 0, latest: 0 }
  }

  return {
    total: cycles.length,
    best: Math.max(...cycles.map((row) => row.duration)),
    target: cycles.find((row) => row.target_time > 0)?.target_time || 0,
    latest: cycles[cycles.length - 1]?.duration || 0,
  }
}

function getChartMax(cycles, target) {
  const maxDuration = cycles.length ? Math.max(...cycles.map((row) => row.duration)) : 0
  const base = Math.max(maxDuration, target || 0, 1)
  return Math.ceil(base * 1.2)
}

function StatsRow({ summary }) {
  return (
    <div className="absolute bottom-4 right-4 flex justify-end gap-2">
      <div className="min-w-[72px] rounded-lg border border-slate-200 bg-white/90 px-2 py-1 text-center dark:border-slate-800 dark:bg-slate-900/70">
        <p className="text-[9px] uppercase tracking-wide text-slate-400">Cycles</p>
        <p className="mt-0.5 text-[13px] font-bold text-slate-900 dark:text-white">{summary.total}</p>
      </div>
      <div className="min-w-[72px] rounded-lg border border-slate-200 bg-white/90 px-2 py-1 text-center dark:border-slate-800 dark:bg-slate-900/70">
        <p className="text-[9px] uppercase tracking-wide text-slate-400">Peak</p>
        <p className="mt-0.5 text-[13px] font-bold text-slate-900 dark:text-white">{summary.best}s</p>
      </div>
      <div className="min-w-[72px] rounded-lg border border-slate-200 bg-white/90 px-2 py-1 text-center dark:border-slate-800 dark:bg-slate-900/70">
        <p className="text-[9px] uppercase tracking-wide text-slate-400">Target</p>
        <p className="mt-0.5 text-[13px] font-bold text-slate-900 dark:text-white">{summary.target ? `${summary.target}s` : '-'}</p>
      </div>
      <div className="min-w-[72px] rounded-lg border border-slate-200 bg-white/90 px-2 py-1 text-center dark:border-slate-800 dark:bg-slate-900/70">
        <p className="text-[9px] uppercase tracking-wide text-slate-400">Latest</p>
        <p className="mt-0.5 text-[13px] font-bold text-slate-900 dark:text-white">{summary.latest}s</p>
      </div>
    </div>
  )
}

function CycleGraphCard({ title, cycles, activeCycle, onOpenCycle, onCloseCycle, emptyText }) {
  const summary = summarizeCycles(cycles)
  const chartMax = getChartMax(cycles, summary.target)
  const activeVideoUrl = activeCycle?.file_path ? `/api/video?path=${encodeURIComponent(activeCycle.file_path)}` : null

  return (
    <div className="relative overflow-hidden rounded-[24px] border border-slate-200 bg-slate-50/80 p-4 dark:border-slate-800 dark:bg-slate-950/60">
      {title ? <h3 className="mb-3 text-sm font-semibold text-slate-900 dark:text-white">{title}</h3> : null}

      <div className="h-[420px] pb-14">
        {!cycles.length ? (
          <div className="flex h-full items-center justify-center text-sm text-slate-500 dark:text-slate-400">
            {emptyText}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={cycles} margin={{ top: 24, right: 20, left: 0, bottom: 12 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.25)" />
              <XAxis dataKey="cycleLabel" tick={{ fontSize: 11 }} />
              <YAxis domain={[0, chartMax]} tick={{ fontSize: 11 }} />
              {!activeCycle && <Tooltip content={<CycleChartTooltip />} cursor={{ stroke: 'rgba(37,99,235,0.25)', strokeWidth: 1 }} />}
              {summary.target > 0 && (
                <ReferenceLine
                  y={summary.target}
                  stroke="#f59e0b"
                  strokeDasharray="6 6"
                  label={{ value: `Target ${summary.target}s`, position: 'insideTopRight', fill: '#f59e0b' }}
                />
              )}
              <Bar dataKey="duration" radius={[10, 10, 0, 0]} barSize={56} onClick={(data) => onOpenCycle(data)}>
                {cycles.map((entry) => (
                  <Cell key={`${entry.machine_id}-${entry.cycle_number}`} fill={entry.barColor} cursor="pointer" />
                ))}
              </Bar>
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>

      <StatsRow summary={summary} />

      {activeCycle && (
        <div className="absolute right-4 top-4 z-20 w-[360px] max-w-[calc(100%-2rem)] rounded-3xl border border-slate-200 bg-white/98 p-3 shadow-2xl dark:border-slate-700 dark:bg-slate-950/96">
          <div className="mb-2 flex items-start justify-between gap-3">
            <div>
              <p className="text-xs uppercase tracking-[0.26em] text-blue-600 dark:text-blue-300">Video</p>
              <div className="mt-1 flex items-center gap-2">
                <h3 className="text-lg font-semibold text-slate-900 dark:text-white">#{activeCycle.cycle_number}</h3>
                <p className="truncate text-sm text-slate-600 dark:text-slate-300">{activeCycle.machine_name || 'Machine'}</p>
              </div>
            </div>
            <button className="btn-secondary px-3 py-2" onClick={onCloseCycle}>Close</button>
          </div>

          <div className="mb-2 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
            <span className="rounded-full bg-slate-100 px-2 py-1 dark:bg-slate-800">{activeCycle.duration}s</span>
            {activeCycle.target_time ? (
              <span className="rounded-full bg-amber-50 px-2 py-1 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
                Target {activeCycle.target_time}s
              </span>
            ) : null}
          </div>

          <div className="mb-2 grid grid-cols-2 gap-2 text-[10px] text-slate-500 dark:text-slate-400">
            <div className="rounded-xl bg-slate-50 px-2 py-2 dark:bg-slate-900/70">
              <p className="uppercase tracking-wide text-slate-400">Start</p>
              <p className="mt-1 font-medium text-slate-700 dark:text-slate-200">{activeCycle.start_time || '-'}</p>
            </div>
            <div className="rounded-xl bg-slate-50 px-2 py-2 dark:bg-slate-900/70">
              <p className="uppercase tracking-wide text-slate-400">End</p>
              <p className="mt-1 font-medium text-slate-700 dark:text-slate-200">{activeCycle.end_time || '-'}</p>
            </div>
          </div>

          <div className="overflow-hidden rounded-2xl border border-slate-200 bg-black dark:border-white/10">
            {activeVideoUrl ? (
              <video key={activeVideoUrl} src={activeVideoUrl} controls autoPlay className="aspect-video w-full bg-black object-cover" />
            ) : (
              <div className="flex aspect-video items-center justify-center text-sm text-slate-400">Video path nahi mila.</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default function Dashboard() {
  const [overview, setOverview] = useState({ hierarchy: [] })
  const [machineCycles, setMachineCycles] = useState([])
  const [lineMachineCycles, setLineMachineCycles] = useState({})
  const [loading, setLoading] = useState(true)
  const [cyclesLoading, setCyclesLoading] = useState(false)
  const [selectedZone, setSelectedZone] = useState('')
  const [selectedLine, setSelectedLine] = useState('')
  const [selectedMachine, setSelectedMachine] = useState('')
  const [activeCycleCard, setActiveCycleCard] = useState(null)
  const toast = useToast()

  const loadHierarchy = async () => {
    setLoading(true)
    try {
      const overviewData = await api.getOverview()
      setOverview(overviewData)
    } catch {
      toast.error('Failed to load dashboard data')
    } finally {
      setLoading(false)
    }
  }

  const loadCycles = async (machineId) => {
    if (!machineId) {
      setMachineCycles([])
      setActiveCycleCard(null)
      return
    }

    setCyclesLoading(true)
    try {
      const rows = await api.getCycleHistory({ machine_id: machineId, limit: 200 })
      const normalized = normalizeCycles(rows.filter((row) => row.machine_id === machineId))
      setMachineCycles(normalized)
      setActiveCycleCard((current) => normalized.find((row) => row.machine_id === current?.machine_id && row.cycle_number === current?.cycle_number) || null)
    } catch {
      toast.error('Failed to load cycle graph')
    } finally {
      setCyclesLoading(false)
    }
  }

  const loadLineCycles = async (lineMachines) => {
    if (!lineMachines.length) {
      setLineMachineCycles({})
      return
    }

    setCyclesLoading(true)
    try {
      const rows = await api.getCycleHistory({ limit: 500 })
      const machineIds = new Set(lineMachines.map((machine) => machine.id))
      const grouped = {}

      for (const machine of lineMachines) {
        grouped[machine.id] = []
      }

      for (const row of rows) {
        if (!machineIds.has(row.machine_id)) continue
        grouped[row.machine_id].push(row)
      }

      const normalized = {}
      for (const machine of lineMachines) {
        normalized[machine.id] = normalizeCycles(grouped[machine.id] || [])
      }

      setLineMachineCycles(normalized)
      setActiveCycleCard((current) => normalized[current?.machine_id]?.find((row) => row.cycle_number === current?.cycle_number) || null)
    } catch {
      toast.error('Failed to load line graphs')
    } finally {
      setCyclesLoading(false)
    }
  }

  useEffect(() => {
    loadHierarchy()
  }, [])

  const zones = overview.hierarchy || []
  const selectedZoneData = useMemo(() => zones.find((zone) => zone.id === selectedZone) || null, [zones, selectedZone])
  const lines = selectedZoneData?.lines || []
  const selectedLineData = useMemo(() => lines.find((line) => line.id === selectedLine) || null, [lines, selectedLine])
  const machines = selectedLineData?.machines || []

  useEffect(() => {
    if (!zones.length) return
    setSelectedZone((current) => (zones.some((zone) => zone.id === current) ? current : zones[0].id))
  }, [zones])

  useEffect(() => {
    if (!lines.length) {
      setSelectedLine('')
      return
    }
    setSelectedLine((current) => (lines.some((line) => line.id === current) ? current : lines[0].id))
  }, [lines])

  useEffect(() => {
    if (!machines.length) {
      setSelectedMachine('')
      return
    }
    setSelectedMachine((current) => (machines.some((machine) => machine.id === current) ? current : ''))
  }, [machines])

  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') setActiveCycleCard(null)
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])

  useEffect(() => {
    if (!selectedLine) return undefined

    const task = selectedMachine
      ? () => loadCycles(selectedMachine)
      : () => loadLineCycles(machines)

    task()
    const timer = setInterval(task, 30000)
    return () => clearInterval(timer)
  }, [selectedLine, selectedMachine, machines])

  const zoneOptions = useMemo(() => zones.map((zone) => ({ value: zone.id, label: zone.name })), [zones])
  const lineOptions = useMemo(() => lines.map((line) => ({ value: line.id, label: line.name })), [lines])
  const machineOptions = useMemo(
    () => [{ value: '', label: 'All Machines' }, ...machines.map((machine) => ({ value: machine.id, label: machine.name }))],
    [machines]
  )
  const lineGridMachines = useMemo(
    () => machines.map((machine) => ({ ...machine, cycles: lineMachineCycles[machine.id] || [] })),
    [machines, lineMachineCycles]
  )

  return (
    <div className="space-y-6">
      <section className="card p-5">
        <h2 className="text-xl font-bold text-slate-900 dark:text-white">Cycle Analysis</h2>

        <div className="mt-5 grid gap-4 md:grid-cols-3">
          <SelectField
            label="Zone"
            icon={MapPin}
            value={selectedZone}
            onChange={(event) => setSelectedZone(event.target.value)}
            options={zoneOptions}
            disabled={loading || zoneOptions.length === 0}
          />
          <SelectField
            label="Line"
            icon={GitBranch}
            value={selectedLine}
            onChange={(event) => setSelectedLine(event.target.value)}
            options={lineOptions}
            disabled={loading || lineOptions.length === 0}
          />
          <SelectField
            label="Machine"
            icon={Video}
            value={selectedMachine}
            onChange={(event) => setSelectedMachine(event.target.value)}
            options={machineOptions}
            disabled={loading || machineOptions.length === 0}
          />
        </div>
      </section>

      <section className="card p-5">
        <h2 className="mb-5 text-lg font-semibold text-slate-900 dark:text-white">Cycle Graph</h2>

        {loading || cyclesLoading ? (
          <div className="h-[360px] skeleton rounded-[24px]" />
        ) : !selectedLine ? (
          <div className="flex h-[240px] items-center justify-center rounded-[24px] border border-slate-200 bg-slate-50/80 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-950/60 dark:text-slate-400">
            Line select karo.
          </div>
        ) : selectedMachine ? (
          <CycleGraphCard
            cycles={machineCycles}
            activeCycle={activeCycleCard}
            onOpenCycle={setActiveCycleCard}
            onCloseCycle={() => setActiveCycleCard(null)}
            emptyText="Is machine ke liye cycle history nahi mili."
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {lineGridMachines.map((machine) => (
              <CycleGraphCard
                key={machine.id}
                title={machine.name}
                cycles={machine.cycles}
                activeCycle={activeCycleCard?.machine_id === machine.id ? activeCycleCard : null}
                onOpenCycle={setActiveCycleCard}
                onCloseCycle={() => setActiveCycleCard(null)}
                emptyText="No cycles"
              />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
