import { useState, useEffect } from 'react'
import { Edit2, Trash2, Check, X } from 'lucide-react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'

export default function ZoneMaster() {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ name: '' })
  const [editingId, setEditingId] = useState(null)
  const [editName, setEditName] = useState('')
  const toast = useToast()

  const load = async () => {
    setLoading(true)
    try { setData(await api.getZones()) } catch { toast.error('Failed to load zones') }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  const save = async e => {
    e.preventDefault()
    try {
      await api.createZone(form)
      toast.success('Zone created')
      setShowForm(false); setForm({ name: '' }); load()
    } catch (e) { toast.error(e.message) }
  }

  const startEdit = z => { setEditingId(z.zone_id); setEditName(z.zone_name) }
  const cancelEdit = () => { setEditingId(null); setEditName('') }

  const saveEdit = async (zId) => {
    if (!editName.trim()) return toast.error('Name cannot be empty')
    try {
      await api.updateZone(zId, { name: editName.trim() })
      toast.success('Zone updated')
      cancelEdit(); load()
    } catch (e) { toast.error(e.response?.data?.message || 'Failed to update') }
  }

  const remove = async (zId) => {
    if (!confirm('Delete this zone and all its lines/machines?')) return
    try { await api.deleteZone(zId); toast.success('Zone deleted'); load() }
    catch (e) { toast.error('Failed to delete') }
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="page-title">Zone Master</h1>
          <p className="page-subtitle">Manage factory zones</p>
        </div>
        {!showForm && (
          <button className="btn-primary" onClick={() => setShowForm(true)}>+ Add Zone</button>
        )}
      </div>

      {showForm && (
        <div className="glass dark:bg-slate-800/50 dark:border-slate-700 p-6">
          <h2 className="text-base font-semibold text-gray-800 dark:text-slate-100 mb-4">Add New Zone</h2>
          <form onSubmit={save} className="flex gap-3 items-end">
            <div className="flex-1">
              <label className="block text-xs font-semibold text-gray-600 dark:text-slate-300 mb-1">Zone Name <span className="text-red-500">*</span></label>
              <input className="input-field" value={form.name} onChange={e => setForm({ name: e.target.value })} required placeholder="e.g. Production Area 1" autoFocus />
            </div>
            <button type="submit" className="btn-primary text-sm">Save Zone</button>
            <button type="button" className="btn-secondary text-sm" onClick={() => { setShowForm(false); setForm({ name: '' }) }}>Cancel</button>
          </form>
        </div>
      )}

      <div className="glass overflow-hidden dark:bg-slate-800/50 dark:border-slate-700">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-gray-50 dark:bg-slate-800 border-b border-gray-200 dark:border-slate-700">
              <tr>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs w-8">#</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs">Zone Name</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs">Lines</th>
                <th className="px-4 py-3 font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-xs text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-slate-700/60">
              {loading
                ? [...Array(3)].map((_, i) => <tr key={i}><td colSpan={4} className="px-4 py-3"><div className="h-4 skeleton w-full" /></td></tr>)
                : data.length === 0
                  ? <tr><td colSpan={4} className="px-4 py-8 text-center text-slate-500">No zones found. Add one above.</td></tr>
                  : data.map((z, idx) => (
                    <tr key={z.zone_id} className="hover:bg-blue-50/30 dark:hover:bg-blue-900/10 transition-colors">
                      <td className="px-4 py-3 text-xs text-gray-400">{idx + 1}</td>
                      <td className="px-4 py-3">
                        {editingId === z.zone_id ? (
                          <input
                            className="input-field py-1 text-sm w-full max-w-xs"
                            value={editName}
                            onChange={e => setEditName(e.target.value)}
                            onKeyDown={e => { if (e.key === 'Enter') saveEdit(z.zone_id); if (e.key === 'Escape') cancelEdit() }}
                            autoFocus
                          />
                        ) : (
                          <span className="font-semibold text-gray-800 dark:text-slate-100">{z.zone_name}</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className="badge badge-success">{z.line_count} Line{z.line_count !== 1 ? 's' : ''}</span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        {editingId === z.zone_id ? (
                          <div className="inline-flex gap-1.5">
                            <button className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg bg-green-50 text-green-700 hover:bg-green-100 border border-green-200 transition-colors" onClick={() => saveEdit(z.zone_id)}>
                              <Check size={13} /> Save
                            </button>
                            <button className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg bg-gray-50 text-gray-600 hover:bg-gray-100 border border-gray-200 transition-colors" onClick={cancelEdit}>
                              <X size={13} /> Cancel
                            </button>
                          </div>
                        ) : (
                          <div className="inline-flex gap-1.5">
                            <button className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg bg-blue-50 text-blue-700 hover:bg-blue-100 border border-blue-200 transition-colors" onClick={() => startEdit(z)}>
                              <Edit2 size={13} /> Edit
                            </button>
                            <button className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg bg-red-50 text-red-700 hover:bg-red-100 border border-red-200 transition-colors" onClick={() => remove(z.zone_id)}>
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
          {data.length} zone{data.length !== 1 ? 's' : ''} total
        </div>
      </div>
    </div>
  )
}
