import { useState, useEffect } from 'react'
import { Plus, Trash2, Pencil } from 'lucide-react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'

const EMPTY_FORM = { ip: '', port: 502, bit_address: 'M100', description: '', enabled: true }

export default function PlcMaster() {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [editId, setEditId] = useState(null)   // null = adding, plc.id = editing
  const [form, setForm] = useState(EMPTY_FORM)
  const toast = useToast()

  const load = async () => {
    setLoading(true)
    try { setData(await api.getPlcs()) }
    catch (e) { toast.error('Error loading PLCs') }
    setLoading(false)
  }
  useEffect(() => { load() }, [])

  const openAdd = () => { setEditId(null); setForm(EMPTY_FORM); setShowModal(true) }
  const openEdit = (p) => {
    setEditId(p.id)
    setForm({
      ip: p.ip || '',
      port: p.port || 502,
      bit_address: p.bit_address || 'M100',
      description: p.description || '',
      enabled: !!p.enabled,
    })
    setShowModal(true)
  }
  const closeModal = () => { setShowModal(false); setEditId(null); setForm(EMPTY_FORM) }

  const save = async (e) => {
    e.preventDefault()
    try {
      if (editId) {
        await api.updatePlc(editId, form)
        toast.success('PLC updated')
      } else {
        await api.createPlc(form)
        toast.success('PLC added')
      }
      closeModal()
      load()
    } catch (e) {
      toast.error(e?.response?.data?.message || e.message || 'Save failed')
    }
  }

  const remove = async (id) => {
    if (!confirm('Delete this PLC? Bindings using it will need to be re-pointed.')) return
    try { await api.deletePlc(id); toast.success('Deleted'); load() }
    catch (e) { toast.error(e?.response?.data?.message || e.message || 'Delete failed') }
  }

  const toggle = async (p) => {
    try { await api.updatePlc(p.id, { enabled: !p.enabled }); load() }
    catch (e) { toast.error(e?.response?.data?.message || e.message || 'Toggle failed') }
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="page-title">PLC Master</h1>
          <p className="page-subtitle">Manage PLC connections — full edit available via the pencil icon.</p>
        </div>
        <button className="btn-primary" onClick={openAdd}><Plus size={16}/>New PLC</button>
      </div>

      <div className="glass overflow-hidden">
        <div className="overflow-x-auto text-sm">
          <table className="w-full text-left">
            <thead className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
              <tr>
                <th className="px-4 py-3">Description</th>
                <th className="px-4 py-3">IP Address</th>
                <th className="px-4 py-3">Port</th>
                <th className="px-4 py-3">Bit Addr</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
              {loading ? (
                <tr><td colSpan={6} className="px-4 py-3"><div className="skeleton h-4"/></td></tr>
              ) : data.length === 0 ? (
                <tr><td colSpan={6} className="px-4 py-6 text-center text-slate-400">No PLCs configured.</td></tr>
              ) : data.map(p => (
                <tr key={p.id} className="table-row-hover">
                  <td className="px-4 py-3 font-medium">{p.description}</td>
                  <td className="px-4 py-3 font-mono">{p.ip}</td>
                  <td className="px-4 py-3">{p.port}</td>
                  <td className="px-4 py-3">{p.bit_address}</td>
                  <td className="px-4 py-3">
                    <button onClick={() => toggle(p)} className={`badge text-white ${p.enabled ? 'bg-emerald-500' : 'bg-slate-500'}`} title="Click to toggle">
                      {p.enabled ? 'Active' : 'Disabled'}
                    </button>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button onClick={() => openEdit(p)} className="text-slate-400 hover:text-blue-500 mr-2" title="Edit">
                      <Pencil size={16}/>
                    </button>
                    <button onClick={() => remove(p.id)} className="text-slate-400 hover:text-red-500" title="Delete">
                      <Trash2 size={16}/>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {showModal && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="card w-full max-w-md p-6">
            <h3 className="text-lg font-bold mb-4">{editId ? 'Edit PLC' : 'Add PLC'}</h3>
            <form onSubmit={save} className="space-y-4">
              <div><label>Description</label><input className="input-field mt-1" value={form.description} onChange={e=>setForm({...form,description:e.target.value})} required placeholder="Main Line PLC"/></div>
              <div className="grid grid-cols-2 gap-4">
                <div><label>IP Address</label><input className="input-field mt-1" value={form.ip} onChange={e=>setForm({...form,ip:e.target.value})} required placeholder="192.168.1.100"/></div>
                <div><label>Port</label><input type="number" className="input-field mt-1" value={form.port} onChange={e=>setForm({...form,port:Number(e.target.value)})} required/></div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div><label>Target Bit Address</label><input className="input-field mt-1" value={form.bit_address} onChange={e=>setForm({...form,bit_address:e.target.value})} required placeholder="M100"/></div>
                <div className="flex items-center gap-2 pt-8">
                  <input type="checkbox" checked={form.enabled} onChange={e=>setForm({...form,enabled:e.target.checked})} className="w-5 h-5"/>
                  <span className="text-slate-600 dark:text-slate-300 font-semibold text-xs">Enabled</span>
                </div>
              </div>
              <div className="flex justify-end gap-2 mt-4">
                <button type="button" className="btn-secondary" onClick={closeModal}>Cancel</button>
                <button type="submit" className="btn-primary">{editId ? 'Save Changes' : 'Save'}</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
