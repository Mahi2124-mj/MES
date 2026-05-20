import { useState, useEffect } from 'react'
import { Edit2, Trash2, Check, X } from 'lucide-react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'

export default function LineMaster() {
  const [data, setData] = useState([])
  const [zones, setZones] = useState([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ zone_id: '', name: '' })
  const [editingId, setEditingId] = useState(null)
  const [editName, setEditName] = useState('')
  const toast = useToast()

  const load = async () => {
    setLoading(true)
    try {
      const [ls, zs] = await Promise.all([api.getLines(), api.getZones()])
      setData(ls); setZones(zs)
    } catch { toast.error('Error loading lines') }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  const save = async e => {
    e.preventDefault()
    try {
      await api.createLine(form)
      toast.success('Line created')
      setShowForm(false); setForm({ zone_id: '', name: '' }); load()
    } catch (e) { toast.error(e.message) }
  }

  const startEdit = l => { setEditingId(l.id); setEditName(l.name) }
  const cancelEdit = () => { setEditingId(null); setEditName('') }

  const saveEdit = async (zoneId, lineId) => {
    if (!editName.trim()) return toast.error('Name cannot be empty')
    try {
      await api.updateLine(zoneId, lineId, { name: editName.trim() })
      toast.success('Line updated')
      cancelEdit(); load()
    } catch (e) { toast.error(e.response?.data?.message || 'Failed to update') }
  }

  const remove = async (zId, lId) => {
    if (!confirm('Delete this line and all its machines?')) return
    try { await api.deleteLine(zId, lId); toast.success('Line deleted'); load() }
    catch (e) { toast.error(e.message) }
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="page-title">Line Master</h1>
          <p className="page-subtitle">Manage production lines — linked to zones</p>
        </div>
        {!showForm && (
          <button className="btn-primary" onClick={() => setShowForm(true)}>+ Add Line</button>
        )}
      </div>

      {showForm && (
        <div className="glass dark:bg-slate-800/50 dark:border-slate-700 p-6">
          <h2 className="text-base font-semibold text-gray-800 dark:text-slate-100 mb-4">Add New Line</h2>
          <form onSubmit={save} className="flex gap-3 items-end flex-wrap">
            <div className="min-w-[180px]">
              <label className="block text-xs font-semibold text-gray-600 dark:text-slate-300 mb-1">Parent Zone <span className="text-red-500">*</span></label>
              <select className="input-field" value={form.zone_id} onChange={e => setForm({ ...form, zone_id: e.target.value })} required>
                <option value="">-- Select Zone --</option>
                {zones.map(z => <option key={z.zone_id} value={z.zone_id}>{z.zone_name}</option>)}
              </select>
            </div>
            <div className="flex-1 min-w-[200px]">
              <label className="block text-xs font-semibold text-gray-600 dark:text-slate-300 mb-1">Line Name <span className="text-red-500">*</span></label>
              <input className="input-field" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} required placeholder="e.g. Assembly Line A" />
            </div>
            <button type="submit" className="btn-primary text-sm">Save Line</button>
            <button type="button" className="btn-secondary text-sm" onClick={() => { setShowForm(false); setForm({ zone_id: '', name: '' }) }}>Cancel</button>
          </form>
        </div>
      )}

      <div className="glass overflow-hidden dark:bg-slate-800/50 dark:border-slate-700">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-gray-50 dark:bg-slate-800 border-b border-gray-200 dark:border-slate-700">
              <tr>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs w-8">#</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs">Line Name</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs">Zone</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs">Machines</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-slate-700/60">
              {loading
                ? <tr><td colSpan={5} className="px-4 py-3"><div className="h-4 skeleton w-full" /></td></tr>
                : data.length === 0
                  ? <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-500">No lines found. Add one above.</td></tr>
                  : data.map((l, idx) => (
                    <tr key={l.id} className="hover:bg-blue-50/30 dark:hover:bg-blue-900/10 transition-colors">
                      <td className="px-4 py-3 text-xs text-gray-400">{idx + 1}</td>
                      <td className="px-4 py-3">
                        {editingId === l.id ? (
                          <input
                            className="input-field py-1 text-sm w-full max-w-xs"
                            value={editName}
                            onChange={e => setEditName(e.target.value)}
                            onKeyDown={e => { if (e.key === 'Enter') saveEdit(l.zone_id, l.id); if (e.key === 'Escape') cancelEdit() }}
                            autoFocus
                          />
                        ) : (
                          <span className="font-medium text-gray-800 dark:text-slate-100">{l.name}</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300 text-xs font-semibold border border-blue-100 dark:border-blue-800">
                          {l.zone_name}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className="badge badge-success">{l.machine_count} Machine{l.machine_count !== 1 ? 's' : ''}</span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        {editingId === l.id ? (
                          <div className="inline-flex gap-1.5">
                            <button className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg bg-green-50 text-green-700 hover:bg-green-100 border border-green-200 transition-colors" onClick={() => saveEdit(l.zone_id, l.id)}>
                              <Check size={13} /> Save
                            </button>
                            <button className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg bg-gray-50 text-gray-600 hover:bg-gray-100 border border-gray-200 transition-colors" onClick={cancelEdit}>
                              <X size={13} /> Cancel
                            </button>
                          </div>
                        ) : (
                          <div className="inline-flex gap-1.5">
                            <button className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg bg-blue-50 text-blue-700 hover:bg-blue-100 border border-blue-200 transition-colors" onClick={() => startEdit(l)}>
                              <Edit2 size={13} /> Edit
                            </button>
                            <button className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg bg-red-50 text-red-700 hover:bg-red-100 border border-red-200 transition-colors" onClick={() => remove(l.zone_id, l.id)}>
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
          {data.length} line{data.length !== 1 ? 's' : ''} total
        </div>
      </div>
    </div>
  )
}
