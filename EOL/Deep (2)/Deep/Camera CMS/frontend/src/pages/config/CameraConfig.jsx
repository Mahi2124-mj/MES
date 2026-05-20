import { useState, useEffect } from 'react'
import { Plus, Trash2, Pencil } from 'lucide-react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'

const EMPTY_FORM = { machine_id: '', camera_id: '', plc_id: '', target_time: 30 }

export default function CameraConfig() {
  const [data, setData] = useState([])
  const [machines, setMachines] = useState([])
  const [cameras, setCameras] = useState([])
  const [plcs, setPlcs] = useState([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [editId, setEditId] = useState(null)   // binding.id when editing
  const [selectedZone, setSelectedZone] = useState('')
  const [selectedLine, setSelectedLine] = useState('')
  const [form, setForm] = useState(EMPTY_FORM)
  const toast = useToast()

  // Zone / Line dropdown sources, derived from machines list
  const uniqueZones = Array.from(new Set(machines.map(m => m.zone_id)))
    .map(id => machines.find(m => m.zone_id === id))
  const filteredLines = Array.from(new Set(machines.filter(m => m.zone_id === selectedZone).map(m => m.line_id)))
    .map(id => machines.find(m => m.line_id === id))
  const filteredMachines = machines.filter(m => m.zone_id === selectedZone && m.line_id === selectedLine)

  // Lookup helpers used by the table to show full Zone / Line context
  // for each binding's machine — without these the user can't tell at
  // a glance which line a binding belongs to.
  const machineById = Object.fromEntries(machines.map(m => [m.machine_id, m]))
  const cameraById  = Object.fromEntries(cameras.map(c => [c.id, c]))
  const plcById     = Object.fromEntries(plcs.map(p => [p.id, p]))

  const load = async () => {
    setLoading(true)
    try {
      const [cfg, m, c, p] = await Promise.all([
        api.getCameraConfigs(), api.getMachines(), api.getCameras(), api.getPlcs(),
      ])
      setData(cfg); setMachines(m); setCameras(c); setPlcs(p)
    } catch (e) { toast.error('Error loading config data') }
    setLoading(false)
  }
  useEffect(() => { load() }, [])

  const openAdd = () => {
    setEditId(null)
    setForm(EMPTY_FORM)
    setSelectedZone('')
    setSelectedLine('')
    setShowModal(true)
  }
  const openEdit = (b) => {
    const machine = machineById[b.machine_id]
    setEditId(b.id)
    setForm({
      machine_id:  b.machine_id || '',
      camera_id:   b.camera_id || '',
      plc_id:      b.plc_id || '',
      target_time: b.target_time || 30,
    })
    // Pre-fill zone / line filters so the machine is selectable in the
    // dropdown without forcing the user to navigate again.
    setSelectedZone(machine?.zone_id || '')
    setSelectedLine(machine?.line_id || '')
    setShowModal(true)
  }
  const closeModal = () => {
    setShowModal(false)
    setEditId(null)
    setForm(EMPTY_FORM)
    setSelectedZone('')
    setSelectedLine('')
  }

  // Backend's POST /api/camera-configs is an UPSERT by machine_id —
  // saving with the same machine_id replaces the old binding.  So we
  // can use the same call for both add and edit; no separate PATCH
  // route needed.  When editing AND changing machine_id, the old
  // binding (under the previous machine_id) needs an explicit delete
  // so it doesn't linger.
  const save = async (e) => {
    e.preventDefault()
    try {
      if (editId) {
        // Find the old binding to detect machine_id change
        const old = data.find(b => b.id === editId)
        if (old && old.machine_id && old.machine_id !== form.machine_id) {
          // Machine reassigned — drop the old binding row before upsert
          try { await api.deleteCameraConfig(editId) } catch {}
        }
      }
      await api.saveCameraConfig(form)
      toast.success(editId ? 'Binding updated' : 'Binding created')
      closeModal()
      load()
    } catch (e) {
      toast.error(e?.response?.data?.message || e.message || 'Save failed')
    }
  }

  const remove = async (id) => {
    if (!confirm('Delete this binding?')) return
    try { await api.deleteCameraConfig(id); toast.success('Deleted'); load() }
    catch (e) { toast.error(e?.response?.data?.message || e.message || 'Delete failed') }
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="page-title">Camera Configuration</h1>
          <p className="page-subtitle">Bind Cameras and PLCs to Machines. Each row shows the full Zone → Line → Machine path so you know exactly where a camera sits.</p>
        </div>
        <button className="btn-primary" onClick={openAdd}><Plus size={16}/>New Binding</button>
      </div>

      <div className="glass overflow-hidden">
        <div className="overflow-x-auto text-sm">
          <table className="w-full text-left">
            <thead className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
              <tr>
                <th className="px-4 py-3">Zone</th>
                <th className="px-4 py-3">Line</th>
                <th className="px-4 py-3">Machine</th>
                <th className="px-4 py-3">Camera</th>
                <th className="px-4 py-3">PLC</th>
                <th className="px-4 py-3">Target (s)</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
              {loading ? (
                <tr><td colSpan={7} className="px-4 py-3"><div className="skeleton h-4"/></td></tr>
              ) : data.length === 0 ? (
                <tr><td colSpan={7} className="px-4 py-6 text-center text-slate-400">No bindings yet — click "New Binding" to assign a camera to a machine.</td></tr>
              ) : data.map(b => {
                const m = machineById[b.machine_id]
                const c = cameraById[b.camera_id]
                const p = plcById[b.plc_id]
                return (
                  <tr key={b.id} className="table-row-hover">
                    <td className="px-4 py-3">{m?.zone_name || <span className="text-slate-400 italic">unknown zone</span>}</td>
                    <td className="px-4 py-3">{m?.line_name || <span className="text-slate-400 italic">unknown line</span>}</td>
                    <td className="px-4 py-3 font-medium text-blue-600 dark:text-blue-400">{m?.machine_name || b.machine_id}</td>
                    <td className="px-4 py-3">{c?.name || <span className="text-slate-400 italic">{b.camera_id}</span>}</td>
                    <td className="px-4 py-3">{p?.description || <span className="text-slate-400 italic">{b.plc_id}</span>}</td>
                    <td className="px-4 py-3"><span className="badge badge-warning">{b.target_time}s</span></td>
                    <td className="px-4 py-3 text-right">
                      <button onClick={() => openEdit(b)} className="text-slate-400 hover:text-blue-500 mr-2" title="Edit">
                        <Pencil size={16}/>
                      </button>
                      <button onClick={() => remove(b.id)} className="text-slate-400 hover:text-red-500" title="Delete">
                        <Trash2 size={16}/>
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {showModal && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="card w-full max-w-md p-6">
            <h3 className="text-lg font-bold mb-4">{editId ? 'Edit Binding' : 'Add Binding'}</h3>
            <form onSubmit={save} className="space-y-4">
              <div>
                <label>Zone</label>
                <select className="input-field mt-1" value={selectedZone}
                        onChange={e => { setSelectedZone(e.target.value); setSelectedLine(''); setForm({...form, machine_id: ''}) }}>
                  <option value="">- Select Zone -</option>
                  {uniqueZones.map(z => <option key={z.zone_id} value={z.zone_id}>{z.zone_name}</option>)}
                </select>
              </div>
              <div>
                <label>Line</label>
                <select className="input-field mt-1" value={selectedLine}
                        onChange={e => { setSelectedLine(e.target.value); setForm({...form, machine_id: ''}) }}
                        disabled={!selectedZone}>
                  <option value="">- Select Line -</option>
                  {filteredLines.map(l => <option key={l.line_id} value={l.line_id}>{l.line_name}</option>)}
                </select>
              </div>
              <div>
                <label>Machine</label>
                <select className="input-field mt-1" value={form.machine_id}
                        onChange={e => setForm({...form, machine_id: e.target.value})}
                        required disabled={!selectedLine}>
                  <option value="">- Select Machine -</option>
                  {filteredMachines.map(m => <option key={m.machine_id} value={m.machine_id}>{m.machine_name}</option>)}
                </select>
              </div>
              <div>
                <label>Camera</label>
                <select className="input-field mt-1" value={form.camera_id}
                        onChange={e => setForm({...form, camera_id: e.target.value})} required>
                  <option value="">- Select -</option>
                  {cameras.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </div>
              <div>
                <label>PLC Trigger</label>
                <select className="input-field mt-1" value={form.plc_id}
                        onChange={e => setForm({...form, plc_id: e.target.value})} required>
                  <option value="">- Select -</option>
                  {plcs.map(p => <option key={p.id} value={p.id}>{p.description} ({p.ip})</option>)}
                </select>
              </div>
              <div>
                <label>Target Cycle Time (s)</label>
                <input type="number" className="input-field mt-1" value={form.target_time}
                       onChange={e => setForm({...form, target_time: Number(e.target.value)})} required/>
              </div>
              <div className="flex justify-end gap-2 mt-4">
                <button type="button" className="btn-secondary" onClick={closeModal}>Cancel</button>
                <button type="submit" className="btn-primary">{editId ? 'Save Changes' : 'Save Binding'}</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
