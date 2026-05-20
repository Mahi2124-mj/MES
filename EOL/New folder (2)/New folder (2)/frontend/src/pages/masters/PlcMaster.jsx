import { useState, useEffect } from 'react'
import { Trash2, Edit2, Check, X, Plus, Wifi, WifiOff } from 'lucide-react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'

const EMPTY = { ip: '', port: 5002, ok_bit: '', ng_bit: '', description: '', enabled: true }

function BitBadges({ raw }) {
  const bits = (raw || '').split(',').map(b => b.trim()).filter(Boolean)
  if (!bits.length) return <span className="text-gray-400 text-xs">—</span>
  return (
    <div className="flex gap-1 flex-wrap">
      {bits.map(b => (
        <span key={b} className="inline-flex items-center px-2 py-0.5 rounded-md bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-300 text-xs font-mono font-bold border border-amber-200 dark:border-amber-800">
          {b}
        </span>
      ))}
    </div>
  )
}

export default function PlcMaster() {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [form, setForm] = useState(EMPTY)
  const toast = useToast()

  const load = async () => {
    setLoading(true)
    try { setData(await api.getPlcs()) } catch { toast.error('Error loading PLCs') }
    setLoading(false)
  }

  useEffect(() => { load() }, [])

  const openAdd = () => { setForm(EMPTY); setEditingId(null); setShowForm(true) }
  const openEdit = (p) => {
    setForm({ ip: p.ip, port: p.port, ok_bit: p.ok_bit || '', ng_bit: p.ng_bit || '', description: p.description, enabled: p.enabled })
    setEditingId(p.id)
    setShowForm(true)
  }
  const closeForm = () => { setShowForm(false); setEditingId(null); setForm(EMPTY) }

  const save = async e => {
    e.preventDefault()
    const payload = { ...form, ok_bit: form.ok_bit.trim(), ng_bit: form.ng_bit.trim() }
    try {
      if (editingId) {
        await api.updatePlc(editingId, payload)
        toast.success('PLC updated')
      } else {
        await api.createPlc(payload)
        toast.success('PLC added')
      }
      closeForm(); load()
    } catch (e) { toast.error(e.message) }
  }

  const remove = async (id) => {
    if (!confirm('Delete this PLC?')) return
    try { await api.deletePlc(id); toast.success('Deleted'); load() } catch { toast.error('Failed to delete') }
  }

  const toggle = async (p) => {
    try { await api.updatePlc(p.id, { enabled: !p.enabled }); load() } catch { toast.error('Failed to update') }
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="page-title">PLC Master</h1>
          <p className="page-subtitle">Manage PLC trigger connections — supports multiple bits (OR logic)</p>
        </div>
        {!showForm && (
          <button className="btn-primary flex items-center gap-1.5" onClick={openAdd}>
            <Plus size={15} /> New PLC
          </button>
        )}
      </div>

      {showForm && (
        <div className="glass dark:bg-slate-800/50 dark:border-slate-700 p-6">
          <h2 className="text-base font-semibold text-gray-800 dark:text-slate-100 mb-4">
            {editingId ? 'Edit PLC' : 'Add New PLC'}
          </h2>
          <form onSubmit={save} className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-gray-600 dark:text-slate-300 mb-1">Description</label>
                <input className="input-field" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} required placeholder="e.g. Line-1 PLC" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-semibold text-gray-600 dark:text-slate-300 mb-1">IP Address</label>
                  <input className="input-field" value={form.ip} onChange={e => setForm({ ...form, ip: e.target.value })} required placeholder="192.168.10.150" />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-600 dark:text-slate-300 mb-1">Port</label>
                  <input type="number" className="input-field" value={form.port} onChange={e => setForm({ ...form, port: Number(e.target.value) })} required />
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-gray-600 dark:text-slate-300 mb-1">
                  OK Bit <span className="text-emerald-500">*</span>
                  <span className="ml-1 text-gray-400 font-normal normal-case">Cycle end (OK part)</span>
                </label>
                <input
                  className="input-field font-mono"
                  value={form.ok_bit}
                  onChange={e => setForm({ ...form, ok_bit: e.target.value })}
                  required
                  placeholder="L108"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 dark:text-slate-300 mb-1">
                  NG Bit
                  <span className="ml-1 text-gray-400 font-normal normal-case">Cycle end (NG part)</span>
                </label>
                <input
                  className="input-field font-mono"
                  value={form.ng_bit}
                  onChange={e => setForm({ ...form, ng_bit: e.target.value })}
                  placeholder="L109"
                />
              </div>
            </div>
            {(form.ok_bit || form.ng_bit) && (
              <div className="flex items-center gap-2 text-xs text-gray-400">
                <span>Triggers:</span>
                {form.ok_bit && <span className="px-2 py-0.5 rounded-md bg-emerald-50 text-emerald-700 font-mono font-bold border border-emerald-200">{form.ok_bit} = OK</span>}
                {form.ng_bit && <span className="px-2 py-0.5 rounded-md bg-red-50 text-red-700 font-mono font-bold border border-red-200">{form.ng_bit} = NG</span>}
                <span>— either bit rising edge triggers new cycle</span>
              </div>
            )}

            <div className="flex items-center gap-3">
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <div
                  onClick={() => setForm(f => ({ ...f, enabled: !f.enabled }))}
                  className={`relative inline-flex items-center w-9 h-5 rounded-full transition-colors ${form.enabled ? 'bg-blue-500' : 'bg-gray-300 dark:bg-slate-600'}`}
                >
                  <span className={`absolute w-3.5 h-3.5 bg-white rounded-full shadow transition-transform ${form.enabled ? 'translate-x-4' : 'translate-x-0.5'}`} />
                </div>
                <span className="text-xs font-semibold text-gray-600 dark:text-slate-300">Enabled</span>
              </label>
            </div>

            <div className="flex gap-2 pt-1">
              <button type="submit" className="btn-primary text-sm">{editingId ? 'Update PLC' : 'Save PLC'}</button>
              <button type="button" className="btn-secondary text-sm" onClick={closeForm}>Cancel</button>
            </div>
          </form>
        </div>
      )}

      <div className="glass overflow-hidden dark:bg-slate-800/50 dark:border-slate-700">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-gray-50 dark:bg-slate-800 border-b border-gray-200 dark:border-slate-700">
              <tr>
                <th className="px-4 py-3 text-xs font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider">Description</th>
                <th className="px-4 py-3 text-xs font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider">IP : Port</th>
                <th className="px-4 py-3 text-xs font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider">OK Bit</th>
                <th className="px-4 py-3 text-xs font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider">NG Bit</th>
                <th className="px-4 py-3 text-xs font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider">Status</th>
                <th className="px-4 py-3 text-xs font-semibold text-gray-600 dark:text-slate-300 uppercase tracking-wider text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-slate-700/60">
              {loading
                ? <tr><td colSpan={6} className="px-4 py-3"><div className="h-4 skeleton w-full" /></td></tr>
                : data.length === 0
                  ? <tr><td colSpan={6} className="px-4 py-8 text-center text-slate-500">No PLCs configured yet.</td></tr>
                  : data.map(p => (
                    <tr key={p.id} className="hover:bg-blue-50/30 dark:hover:bg-blue-900/10 transition-colors">
                      <td className="px-4 py-3 font-medium text-gray-800 dark:text-slate-100">{p.description || '—'}</td>
                      <td className="px-4 py-3 font-mono text-xs text-gray-600 dark:text-slate-300">
                        {p.ip}<span className="text-gray-400">:{p.port}</span>
                      </td>
                      <td className="px-4 py-3">
                        {p.ok_bit
                          ? <span className="px-2 py-0.5 rounded-md bg-emerald-50 text-emerald-700 font-mono font-bold text-xs border border-emerald-200 dark:bg-emerald-900/20 dark:text-emerald-300 dark:border-emerald-800">{p.ok_bit}</span>
                          : <span className="text-gray-400 text-xs">—</span>}
                      </td>
                      <td className="px-4 py-3">
                        {p.ng_bit
                          ? <span className="px-2 py-0.5 rounded-md bg-red-50 text-red-700 font-mono font-bold text-xs border border-red-200 dark:bg-red-900/20 dark:text-red-300 dark:border-red-800">{p.ng_bit}</span>
                          : <span className="text-gray-400 text-xs">—</span>}
                      </td>
                      <td className="px-4 py-3">
                        <button
                          onClick={() => toggle(p)}
                          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold border transition-colors ${
                            p.enabled
                              ? 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100 dark:bg-emerald-900/20 dark:text-emerald-300 dark:border-emerald-800'
                              : 'bg-gray-50 text-gray-500 border-gray-200 hover:bg-gray-100 dark:bg-slate-700/50 dark:text-slate-400 dark:border-slate-600'
                          }`}
                        >
                          {p.enabled ? <Wifi size={11} /> : <WifiOff size={11} />}
                          {p.enabled ? 'Active' : 'Disabled'}
                        </button>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="inline-flex gap-1.5">
                          <button
                            className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg bg-blue-50 text-blue-700 hover:bg-blue-100 border border-blue-200 transition-colors"
                            onClick={() => openEdit(p)}
                          >
                            <Edit2 size={12} /> Edit
                          </button>
                          <button
                            className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-lg bg-red-50 text-red-700 hover:bg-red-100 border border-red-200 transition-colors"
                            onClick={() => remove(p.id)}
                          >
                            <Trash2 size={12} /> Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))
              }
            </tbody>
          </table>
        </div>
        <div className="px-4 py-2 bg-gray-50 dark:bg-slate-800 border-t border-gray-100 dark:border-slate-700/60 text-xs text-gray-400">
          {data.length} PLC{data.length !== 1 ? 's' : ''} configured
        </div>
      </div>

      {/* Info box */}
      <div className="glass dark:bg-slate-800/50 dark:border-slate-700 rounded-xl px-4 py-3 text-xs text-gray-500 dark:text-slate-400 space-y-1">
        <p className="font-semibold text-gray-700 dark:text-slate-200">How OK/NG trigger bits work:</p>
        <p>• <strong className="text-emerald-600">OK Bit</strong> rising edge (0→1) = part produced successfully → ends cycle, starts next</p>
        <p>• <strong className="text-red-600">NG Bit</strong> rising edge (0→1) = part rejected → ends cycle (marked NG), starts next</p>
        <p>• Both bits trigger cycle recording — video saved for OK and NG parts</p>
        <p>• Polling interval: 300ms &nbsp;|&nbsp; Protocol: MC Protocol (Type 4E)</p>
      </div>
    </div>
  )
}
