import { useState, useEffect } from 'react'
import { Plus, Trash2, Pencil } from 'lucide-react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'

const EMPTY_FORM = { name: '', ip: '', port: 554, username: '', password: '', path: '' }

export default function CameraMaster() {
  const [data, setData] = useState([])
  const [bindings, setBindings] = useState([])
  const [machines, setMachines] = useState([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [editId, setEditId] = useState(null)   // camera id when editing; null when adding
  const [form, setForm] = useState(EMPTY_FORM)
  const toast = useToast()

  // Build a {camera_id -> [machine_name, ...]} map.  A camera can be
  // bound to multiple machines (one camera covers a station with
  // several sub-machines), so we collect ALL of them and join with " / ".
  const cameraMachines = (() => {
    const m = {}
    for (const b of bindings) {
      const machine = machines.find(mm => mm.machine_id === b.machine_id)
      const label = machine?.machine_name || b.machine_id
      if (!m[b.camera_id]) m[b.camera_id] = []
      m[b.camera_id].push(label)
    }
    return m
  })()

  const load = async () => {
    setLoading(true)
    try {
      const [cams, binds, mchs] = await Promise.all([
        api.getCameras(),
        api.getCameraConfigs(),
        api.getMachines(),
      ])
      setData(cams)
      setBindings(binds)
      setMachines(mchs)
    } catch (e) {
      toast.error('Error loading cameras')
    }
    setLoading(false)
  }
  useEffect(() => { load() }, [])

  const openAdd = () => { setEditId(null); setForm(EMPTY_FORM); setShowModal(true) }
  const openEdit = (cam) => {
    setEditId(cam.id || cam.name)
    setForm({
      name: cam.name || '',
      ip: cam.ip || '',
      port: cam.port || 554,
      username: cam.username || '',
      password: '',   // never prefill — backend keeps existing on empty
      path: cam.path || '',
    })
    setShowModal(true)
  }

  const save = async (e) => {
    e.preventDefault()
    try {
      if (editId) {
        // PATCH — only send fields user actually changed.  Password
        // is NEVER pre-filled, so an empty password means "keep the
        // existing one".  Skip empty values to honour that.
        const body = {}
        for (const k of ['name','ip','port','username','password','path']) {
          const v = form[k]
          if (v !== '' && v !== null && v !== undefined) body[k] = v
        }
        await api.updateCamera(editId, body)
        toast.success('Camera updated')
      } else {
        await api.createCamera(form)
        toast.success('Camera added')
      }
      setShowModal(false)
      setForm(EMPTY_FORM)
      setEditId(null)
      load()
    } catch (e) {
      toast.error(e?.response?.data?.message || e.message || 'Save failed')
    }
  }

  const remove = async (id) => {
    if (!confirm('Delete this camera? Any bindings using it will need to be re-pointed.')) return
    try {
      await api.deleteCamera(id)
      toast.success('Deleted')
      load()
    } catch (e) {
      // Backend returns 400 when trying to delete the LAST camera —
      // surface that exact reason instead of a generic toast.
      toast.error(e?.response?.data?.message || e.message || 'Delete failed')
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-end">
        <div>
          <h1 className="page-title">Camera Master</h1>
          <p className="page-subtitle">RTSP stream configuration. The "Mounted On" column shows which machine each camera is currently bound to (configured under Configuration → Camera Config).</p>
        </div>
        <button className="btn-primary" onClick={openAdd}><Plus size={16}/>New Camera</button>
      </div>

      <div className="glass overflow-hidden">
        <div className="overflow-x-auto text-sm">
          <table className="w-full text-left">
            <thead className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
              <tr>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Mounted On</th>
                <th className="px-4 py-3">IP / URI</th>
                <th className="px-4 py-3">Port</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
              {loading ? (
                <tr><td colSpan={5} className="px-4 py-3"><div className="skeleton h-4"/></td></tr>
              ) : data.length === 0 ? (
                <tr><td colSpan={5} className="px-4 py-6 text-center text-slate-400">No cameras configured.</td></tr>
              ) : data.map(c => {
                const cid = c.id || c.name
                const mounts = cameraMachines[cid] || []
                return (
                  <tr key={cid} className="table-row-hover">
                    <td className="px-4 py-3 font-medium">{c.name}</td>
                    <td className="px-4 py-3">
                      {mounts.length === 0 ? (
                        <span className="text-slate-400 text-xs italic">— not bound —</span>
                      ) : (
                        mounts.map((m, idx) => (
                          <span key={idx} className="badge badge-info mr-1">{m}</span>
                        ))
                      )}
                    </td>
                    <td className="px-4 py-3 opacity-80 rtsp-col font-mono text-xs">{c.ip}{c.path}</td>
                    <td className="px-4 py-3">{c.port}</td>
                    <td className="px-4 py-3 text-right">
                      <button onClick={() => openEdit(c)} className="text-slate-400 hover:text-blue-500 mr-2" title="Edit / rename">
                        <Pencil size={16}/>
                      </button>
                      <button onClick={() => remove(cid)} className="text-slate-400 hover:text-red-500" title="Delete">
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
            <h3 className="text-lg font-bold mb-4">{editId ? 'Edit Camera' : 'Add Camera'}</h3>
            <form onSubmit={save} className="space-y-4">
              <div>
                <label>Name</label>
                <input
                  className="input-field mt-1"
                  value={form.name}
                  onChange={e => setForm({...form, name: e.target.value})}
                  placeholder="e.g. YNC Final M/c Cam"
                  required
                />
                <p className="text-[10px] text-slate-400 mt-1">
                  Use the machine name where this camera is mounted, so the dashboard
                  is self-explanatory.
                </p>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div><label>IP Address</label><input className="input-field mt-1" value={form.ip} onChange={e=>setForm({...form,ip:e.target.value})} required={!editId}/></div>
                <div><label>Port</label><input type="number" className="input-field mt-1" value={form.port} onChange={e=>setForm({...form,port:Number(e.target.value)})} required/></div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div><label>Username</label><input className="input-field mt-1" value={form.username} onChange={e=>setForm({...form,username:e.target.value})} required={!editId}/></div>
                <div>
                  <label>Password</label>
                  <input
                    type="password"
                    className="input-field mt-1"
                    value={form.password}
                    onChange={e => setForm({...form, password: e.target.value})}
                    placeholder={editId ? 'leave blank to keep' : ''}
                    required={!editId}
                  />
                </div>
              </div>
              <div>
                <label>Path (optional)</label>
                <input
                  className="input-field mt-1"
                  value={form.path}
                  onChange={e=>setForm({...form,path:e.target.value})}
                  placeholder="/h264/ch1/main"
                />
              </div>
              <div className="flex justify-end gap-2 mt-4">
                <button type="button" className="btn-secondary" onClick={()=>{setShowModal(false); setEditId(null); setForm(EMPTY_FORM)}}>Cancel</button>
                <button type="submit" className="btn-primary">{editId ? 'Save Changes' : 'Save'}</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
