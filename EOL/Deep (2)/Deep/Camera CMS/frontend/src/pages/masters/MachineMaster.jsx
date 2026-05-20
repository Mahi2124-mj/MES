import { useState, useEffect } from 'react'
import { Edit2, Trash2, Check, X } from 'lucide-react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'

export default function MachineMaster() {
  const [data, setData] = useState([])
  const [zones, setZones] = useState([])
  const [lines, setLines] = useState([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ zone_id: '', line_id: '', name: '' })
  const [editingId, setEditingId] = useState(null)
  const [editName, setEditName] = useState('')
  const [filterZone, setFilterZone] = useState('')
  const [filterLine, setFilterLine] = useState('')
  const toast = useToast()

  const load = async () => {
    setLoading(true)
    try {
      const [ms, zs, ls] = await Promise.all([api.getMachines(), api.getZones(), api.getLines()])
      setData(ms); setZones(zs); setLines(ls)
    } catch { toast.error('Error loading data') }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  // Cascading filter: lines for selected zone in form
  const formLines = form.zone_id ? lines.filter(l => l.zone_id === form.zone_id) : []

  // Filter lines in filter bar
  const filterBarLines = filterZone ? lines.filter(l => l.zone_id === filterZone) : []

  // Filtered table data
  const displayed = data.filter(m => {
    if (filterZone && m.zone_id !== filterZone) return false
    if (filterLine && m.line_id !== filterLine) return false
    return true
  })

  const save = async e => {
    e.preventDefault()
    try {
      await api.createMachine(form)
      toast.success('Machine created')
      setShowForm(false); setForm({ zone_id: '', line_id: '', name: '' }); load()
    } catch (e) { toast.error(e.message) }
  }

  const startEdit = m => { setEditingId(m.machine_id); setEditName(m.machine_name) }
  const cancelEdit = () => { setEditingId(null); setEditName('') }

  const saveEdit = async (zId, lId, mId) => {
    if (!editName.trim()) return toast.error('Name cannot be empty')
    try {
      await api.updateMachine(zId, lId, mId, { name: editName.trim() })
      toast.success('Machine updated')
      cancelEdit(); load()
    } catch (e) { toast.error(e.response?.data?.message || 'Failed to update') }
  }

  const remove = async (zId, lId, mId) => {
    if (!confirm('Delete this machine?')) return
    try { await api.deleteMachine(zId, lId, mId); toast.success('Machine deleted'); load() }
    catch (e) { toast.error(e.message) }
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="page-title">Machine Master</h1>
          <p className="page-subtitle">Machine registry — linked to Zone &rarr; Line</p>
        </div>
        {!showForm && (
          <button className="btn-primary" onClick={() => setShowForm(true)}>+ Add Machine</button>
        )}
      </div>

      {showForm && (
        <div className="glass dark:bg-slate-800/50 dark:border-slate-700 p-6">
          <h2 className="text-base font-semibold text-gray-800 dark:text-slate-100 mb-4">Add New Machine</h2>
          <form onSubmit={save} className="flex gap-3 items-end flex-wrap">
            <div className="min-w-[160px]">
              <label className="block text-xs font-semibold text-gray-600 dark:text-slate-300 mb-1">Zone <span className="text-red-500">*</span></label>
              <select className="input-field" value={form.zone_id} onChange={e => setForm({ ...form, zone_id: e.target.value, line_id: '' })} required>
                <option value="">-- Select Zone --</option>
                {zones.map(z => <option key={z.zone_id} value={z.zone_id}>{z.zone_name}</option>)}
              </select>
            </div>
            <div className="min-w-[160px]">
              <label className="block text-xs font-semibold text-gray-600 dark:text-slate-300 mb-1">Line <span className="text-red-500">*</span></label>
              <select className="input-field" value={form.line_id} onChange={e => setForm({ ...form, line_id: e.target.value })} required disabled={!form.zone_id}>
                <option value="">-- Select Line --</option>
                {formLines.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
              </select>
            </div>
            <div className="flex-1 min-w-[200px]">
              <label className="block text-xs font-semibold text-gray-600 dark:text-slate-300 mb-1">Machine Name <span className="text-red-500">*</span></label>
              <input className="input-field" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} required placeholder="e.g. Welding Robot #1" />
            </div>
            <button type="submit" className="btn-primary text-sm">Save Machine</button>
            <button type="button" className="btn-secondary text-sm" onClick={() => { setShowForm(false); setForm({ zone_id: '', line_id: '', name: '' }) }}>Cancel</button>
          </form>
        </div>
      )}

      {/* Filter bar */}
      <div className="glass dark:bg-slate-800/50 dark:border-slate-700 px-4 py-3 flex gap-3 items-center flex-wrap">
        <span className="text-xs font-semibold text-gray-500 dark:text-slate-400">Filter:</span>
        <select className="input-field py-1 text-xs w-40" value={filterZone} onChange={e => { setFilterZone(e.target.value); setFilterLine('') }}>
          <option value="">All Zones</option>
          {zones.map(z => <option key={z.zone_id} value={z.zone_id}>{z.zone_name}</option>)}
        </select>
        <select className="input-field py-1 text-xs w-40" value={filterLine} onChange={e => setFilterLine(e.target.value)} disabled={!filterZone}>
          <option value="">All Lines</option>
          {filterBarLines.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
        </select>
        {(filterZone || filterLine) && (
          <button className="text-xs text-blue-600 hover:underline" onClick={() => { setFilterZone(''); setFilterLine('') }}>Clear</button>
        )}
        <span className="ml-auto text-xs text-gray-400">{displayed.length} of {data.length} machines</span>
      </div>

      <div className="glass overflow-hidden dark:bg-slate-800/50 dark:border-slate-700">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-gray-50 dark:bg-slate-800 border-b border-gray-200 dark:border-slate-700">
              <tr>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs w-8">#</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs">Machine Name</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs">Zone</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs">Line</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs">Camera</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-slate-700/60">
              {loading
                ? <tr><td colSpan={6} className="px-4 py-3"><div className="h-4 skeleton w-full" /></td></tr>
                : displayed.length === 0
                  ? <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">No machines found.</td></tr>
                  : displayed.map((m, idx) => (
                    <tr key={m.machine_id} className="hover:bg-blue-50/30 dark:hover:bg-blue-900/10 transition-colors">
                      <td className="px-4 py-3 text-xs text-gray-400">{idx + 1}</td>
                      <td className="px-4 py-3">
                        {editingId === m.machine_id ? (
                          <input
                            className="input-field py-1 text-sm w-full max-w-xs"
                            value={editName}
                            onChange={e => setEditName(e.target.value)}
                            onKeyDown={e => { if (e.key === 'Enter') saveEdit(m.zone_id, m.line_id, m.machine_id); if (e.key === 'Escape') cancelEdit() }}
                            autoFocus
                          />
                        ) : (
                          <span className="font-medium text-gray-800 dark:text-slate-100">{m.machine_name}</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300 text-xs font-semibold border border-blue-100 dark:border-blue-800">
                          {m.zone_name}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-purple-50 dark:bg-purple-900/20 text-purple-700 dark:text-purple-300 text-xs font-semibold border border-purple-100 dark:border-purple-800">
                          {m.line_name}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`badge ${m.camera_id ? 'badge-success' : 'badge-warning'}`}>
                          {m.camera_name || 'Unassigned'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        {editingId === m.machine_id ? (
                          <div className="inline-flex gap-1.5">
                            <button className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg bg-green-50 text-green-700 hover:bg-green-100 border border-green-200 transition-colors" onClick={() => saveEdit(m.zone_id, m.line_id, m.machine_id)}>
                              <Check size={13} /> Save
                            </button>
                            <button className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg bg-gray-50 text-gray-600 hover:bg-gray-100 border border-gray-200 transition-colors" onClick={cancelEdit}>
                              <X size={13} /> Cancel
                            </button>
                          </div>
                        ) : (
                          <div className="inline-flex gap-1.5">
                            <button className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg bg-blue-50 text-blue-700 hover:bg-blue-100 border border-blue-200 transition-colors" onClick={() => startEdit(m)}>
                              <Edit2 size={13} /> Edit
                            </button>
                            <button className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg bg-red-50 text-red-700 hover:bg-red-100 border border-red-200 transition-colors" onClick={() => remove(m.zone_id, m.line_id, m.machine_id)}>
                              <Trash2 size={13} /> Delete
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))
              }
            </tbody>
          </table>
        </div>
        <div className="px-4 py-2 bg-gray-50 dark:bg-slate-800 border-t border-gray-100 dark:border-slate-700/60 text-xs text-gray-400">
          {data.length} machine{data.length !== 1 ? 's' : ''} total
        </div>
      </div>
    </div>
  )
}
