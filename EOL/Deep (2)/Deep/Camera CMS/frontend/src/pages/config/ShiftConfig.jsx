import { useEffect, useMemo, useState } from 'react'
import { Clock3, Pencil, Plus, Save, Trash2, X } from 'lucide-react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'

const EMPTY_FORM = { id: '', name: '', start: '06:00', end: '14:00' }

function timeToMinutes(value) {
  if (!value || !value.includes(':')) return 0
  const [hours, minutes] = value.split(':').map(Number)
  return (hours * 60) + minutes
}

function getShiftDuration(start, end) {
  const startMinutes = timeToMinutes(start)
  const endMinutes = timeToMinutes(end)
  const diff = endMinutes >= startMinutes ? endMinutes - startMinutes : (1440 - startMinutes) + endMinutes
  return Math.round((diff / 60) * 10) / 10
}

function crossesMidnight(start, end) {
  return timeToMinutes(end) <= timeToMinutes(start)
}

function getCurrentShift(shifts) {
  const now = new Date()
  const currentMinutes = (now.getHours() * 60) + now.getMinutes()
  return shifts.find((shift) => {
    const start = timeToMinutes(shift.start)
    const end = timeToMinutes(shift.end)
    if (end > start) return currentMinutes >= start && currentMinutes < end
    return currentMinutes >= start || currentMinutes < end
  }) || null
}

export default function ShiftConfig() {
  const [shifts, setShifts] = useState([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editingShift, setEditingShift] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const toast = useToast()

  const load = async () => {
    setLoading(true)
    try {
      const items = await api.getShifts()
      setShifts(items)
    } catch (error) {
      toast.error('Failed to load shifts')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  const currentShift = useMemo(() => getCurrentShift(shifts), [shifts])
  const totalCoverage = useMemo(
    () => shifts.reduce((total, shift) => total + getShiftDuration(shift.start, shift.end), 0),
    [shifts]
  )

  const openCreate = () => {
    setEditingShift(null)
    setForm(EMPTY_FORM)
    setShowForm(true)
  }

  const openEdit = (shift) => {
    setEditingShift(shift)
    setForm({
      id: shift.id,
      name: shift.name,
      start: shift.start,
      end: shift.end,
    })
    setShowForm(true)
  }

  const resetForm = () => {
    setEditingShift(null)
    setForm(EMPTY_FORM)
    setShowForm(false)
  }

  const handleSave = async (event) => {
    event.preventDefault()
    setSaving(true)
    try {
      await api.saveShift(form)
      toast.success(editingShift ? 'Shift updated' : 'Shift created')
      resetForm()
      await load()
    } catch (error) {
      toast.error(error?.response?.data?.message || 'Unable to save shift')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (shift) => {
    if (!confirm(`Delete ${shift.name}?`)) return
    try {
      await api.deleteShift(shift.id)
      toast.success('Shift deleted')
      await load()
    } catch (error) {
      toast.error(error?.response?.data?.message || 'Unable to delete shift')
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="page-title">Shift Configuration</h1>
          <p className="page-subtitle">Keep the same production timing structure used across the EMS-style portal.</p>
        </div>
        <button className="btn-primary" onClick={openCreate} disabled={showForm}>
          <Plus size={16} />
          Add Shift
        </button>
      </div>

      <div className="grid gap-4 md:grid-cols-3 stagger">
        <div className="card p-5">
          <p className="text-xs uppercase tracking-[0.24em] text-slate-400 mb-2">Current Window</p>
          <div className="flex items-center gap-3">
            <div className={`w-3 h-3 rounded-full ${currentShift ? 'bg-emerald-500 animate-pulse' : 'bg-slate-300 dark:bg-slate-600'}`} />
            <div>
              <p className="text-lg font-bold text-slate-900 dark:text-white">{currentShift?.name || 'No active shift'}</p>
              <p className="text-sm text-slate-500 dark:text-slate-400">{currentShift ? `${currentShift.start} to ${currentShift.end}` : 'Check configured timings'}</p>
            </div>
          </div>
        </div>

        <div className="card p-5">
          <p className="text-xs uppercase tracking-[0.24em] text-slate-400 mb-2">Configured Shifts</p>
          <p className="text-3xl font-bold text-slate-900 dark:text-white">{shifts.length}</p>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">Shift A / B / C pattern can be preserved or expanded.</p>
        </div>

        <div className="card p-5">
          <p className="text-xs uppercase tracking-[0.24em] text-slate-400 mb-2">Daily Coverage</p>
          <p className="text-3xl font-bold text-slate-900 dark:text-white">{totalCoverage}h</p>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">Total scheduled runtime across all configured shifts.</p>
        </div>
      </div>

      {showForm && (
        <div className="glass p-6">
          <div className="flex items-center justify-between gap-3 mb-5">
            <div>
              <h2 className="text-base font-semibold text-slate-900 dark:text-white">{editingShift ? 'Edit Shift' : 'Create Shift'}</h2>
              <p className="text-sm text-slate-500 dark:text-slate-400">Use simple start/end windows so the backend and recorder logic stay aligned.</p>
            </div>
            <button className="btn-secondary" type="button" onClick={resetForm}>
              <X size={16} />
              Cancel
            </button>
          </div>

          <form onSubmit={handleSave} className="grid gap-4 md:grid-cols-3">
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">Shift Name</label>
              <input
                className="input-field"
                value={form.name}
                onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
                placeholder="Shift A"
                required
              />
            </div>
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">Start Time</label>
              <input
                type="time"
                className="input-field"
                value={form.start}
                onChange={(event) => setForm((current) => ({ ...current, start: event.target.value }))}
                required
              />
            </div>
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">End Time</label>
              <input
                type="time"
                className="input-field"
                value={form.end}
                onChange={(event) => setForm((current) => ({ ...current, end: event.target.value }))}
                required
              />
            </div>

            <div className="md:col-span-3 flex items-center justify-between gap-4 flex-wrap pt-2">
              <div className="text-sm text-slate-500 dark:text-slate-400">
                Duration: <span className="font-semibold text-slate-700 dark:text-slate-200">{getShiftDuration(form.start, form.end)}h</span>
                {crossesMidnight(form.start, form.end) && (
                  <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-violet-100 px-2 py-0.5 text-xs font-semibold text-violet-700 dark:bg-violet-900/30 dark:text-violet-300">
                    <Clock3 size={12} />
                    Crosses midnight
                  </span>
                )}
              </div>

              <button className="btn-primary" type="submit" disabled={saving}>
                <Save size={16} />
                {saving ? 'Saving...' : editingShift ? 'Update Shift' : 'Save Shift'}
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 dark:bg-slate-800/60 border-b border-slate-200 dark:border-slate-700">
              <tr>
                <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-slate-500">Shift</th>
                <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-slate-500">Window</th>
                <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-slate-500">Duration</th>
                <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-slate-500">Status</th>
                <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-slate-500 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {loading && (
                <tr>
                  <td colSpan={5} className="px-4 py-6">
                    <div className="h-4 skeleton w-full" />
                  </td>
                </tr>
              )}

              {!loading && shifts.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-slate-500 dark:text-slate-400">
                    No shifts configured yet.
                  </td>
                </tr>
              )}

              {!loading && shifts.map((shift) => {
                const active = currentShift?.id === shift.id
                return (
                  <tr key={shift.id} className="hover:bg-slate-50 dark:hover:bg-slate-800/40 transition-colors">
                    <td className="px-4 py-3">
                      <p className="font-semibold text-slate-900 dark:text-white">{shift.name}</p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">{shift.id}</p>
                    </td>
                    <td className="px-4 py-3 text-slate-600 dark:text-slate-300">{shift.start} to {shift.end}</td>
                    <td className="px-4 py-3">
                      <span className="badge badge-blue">{getShiftDuration(shift.start, shift.end)}h</span>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`badge ${active ? 'badge-success' : 'badge-warning'}`}>
                        {active ? 'Active now' : crossesMidnight(shift.start, shift.end) ? 'Overnight' : 'Scheduled'}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-2">
                        <button className="btn-secondary px-3 py-2 text-xs" onClick={() => openEdit(shift)}>
                          <Pencil size={14} />
                          Edit
                        </button>
                        <button
                          className="inline-flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs font-semibold text-red-700 transition-colors hover:bg-red-100 dark:border-red-900/40 dark:bg-red-900/20 dark:text-red-300"
                          onClick={() => handleDelete(shift)}
                        >
                          <Trash2 size={14} />
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
