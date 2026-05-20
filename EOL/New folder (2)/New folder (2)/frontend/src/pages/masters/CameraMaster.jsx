import { useState, useEffect } from 'react'
import { Plus, Trash2, Edit2, Check, X, Folder, FolderOpen, HardDrive, ChevronLeft, FolderPlus } from 'lucide-react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'
import { useCameraFrame } from '../../hooks/useCameraFrame'

function CameraPreview({ camera }) {
  const { src, offline, setOffline } = useCameraFrame(camera.id, 2000)

  return (
    <div className="w-40">
      <div className="aspect-video rounded-lg overflow-hidden bg-slate-900 border border-slate-200 dark:border-slate-700 relative">
        {src && (
          <img src={src} alt={camera.name} className="w-full h-full object-cover" />
        )}
        {offline && (
          <div className="absolute inset-0 w-full h-full flex items-center justify-center text-[11px] text-slate-400 bg-slate-900/90">
            Stream offline
          </div>
        )}
        {!src && !offline && (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="w-4 h-4 border-2 border-slate-600 border-t-blue-400 rounded-full animate-spin" />
          </div>
        )}
      </div>
    </div>
  )
}

export default function CameraMaster() {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [form, setForm] = useState({ name: '', ip: '', port: 554, username: '', password: '', path: '' })
  const toast = useToast()

  // For assignment dropdowns
  const [zones, setZones] = useState([])
  const [machines, setMachines] = useState([])  // flat list with zone/line info
  const [bindings, setBindings] = useState([])  // camera-machine bindings
  const [assignModal, setAssignModal] = useState(null) // camera being assigned
  const [assignForm, setAssignForm] = useState({ zone_id: '', line_id: '', machine_id: '' })
  const [pings, setPings] = useState({})
  const [videoPath, setVideoPath] = useState('')
  const [videoPathInfo, setVideoPathInfo] = useState(null)
  const [savingPath, setSavingPath] = useState(false)
  const [showPicker, setShowPicker] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [cams, z, m, b] = await Promise.all([
        api.getCameras(),
        api.getZones(),
        api.getMachines(),
        api.getCameraConfigs(),
      ])
      setData(cams)
      setZones(z)
      setMachines(m)
      setBindings(b)
    } catch (e) { toast.error('Error loading data') }
    setLoading(false)
  }
  useEffect(() => { load(); loadVideoPath() }, [])

  const loadVideoPath = async () => {
    try {
      const r = await api.getVideoPath()
      setVideoPath(r.save_path || '')
      setVideoPathInfo(r)
    } catch {}
  }

  const saveVideoPath = async () => {
    setSavingPath(true)
    try {
      await api.setVideoPath({ save_path: videoPath })
      toast.success('Video save path updated')
      loadVideoPath()
    } catch(e) { toast.error(e.message || 'Failed to save path') }
    finally { setSavingPath(false) }
  }

  const save = async e => { e.preventDefault(); try { await api.createCamera(form); toast.success('Created'); setShowModal(false); setForm({name:'',ip:'',port:554,username:'',password:'',path:''}); load() } catch(e){ toast.error(e.message) } }
  const remove = async (id) => { if(!confirm('Delete?'))return; try { await api.deleteCamera(id); toast.success('Deleted'); load() } catch(e){ toast.error(e.message) } }

  // Find which machine a camera is bound to
  const getBinding = (camId) => bindings.find(b => b.camera_id === camId)
  const getMachine = (machineId) => machines.find(m => m.machine_id === machineId)

  // Ping a camera
  const pingCam = async (ip) => {
    setPings(p => ({...p, [ip]: 'loading'}))
    try {
      const r = await api.ping(ip)
      setPings(p => ({...p, [ip]: r}))
    } catch { setPings(p => ({...p, [ip]: {ok:false}})) }
  }

  // Assignment helpers
  const openAssign = (cam) => {
    const b = getBinding(cam.id)
    const m = b ? getMachine(b.machine_id) : null
    setAssignForm({
      zone_id: m?.zone_id || '',
      line_id: m?.line_id || '',
      machine_id: b?.machine_id || '',
    })
    setAssignModal(cam)
  }

  // Build unique lines for selected zone
  const filteredLines = (() => {
    if (!assignForm.zone_id) return []
    const seen = new Set()
    return machines
      .filter(m => m.zone_id === assignForm.zone_id)
      .filter(m => { if (seen.has(m.line_id)) return false; seen.add(m.line_id); return true })
      .map(m => ({ id: m.line_id, name: m.line_name || m.line_id }))
  })()
  const filteredMachines = machines.filter(m =>
    (!assignForm.zone_id || m.zone_id === assignForm.zone_id) &&
    (!assignForm.line_id || m.line_id === assignForm.line_id)
  )

  const saveAssign = async () => {
    if (!assignModal || !assignForm.machine_id) { toast.error('Select a machine'); return }
    const existing = getBinding(assignModal.id)
    try {
      // Remove old binding if exists
      if (existing) await api.deleteCameraConfig(existing.id)
      // 1. Assign camera in zones.json (display)
      await api.assignCamera(assignForm.zone_id, assignForm.line_id, assignForm.machine_id, { camera_id: assignModal.id })
      // 2. Create camera_config binding (PLC trigger → camera → video recording)
      //    Find which PLC is configured
      let plcId = ""
      try {
        const plcs = await api.getPlcs()
        if (plcs.length > 0) plcId = plcs[0].id
      } catch {}
      await api.saveCameraConfig({
        machine_id: assignForm.machine_id,
        camera_id:  assignModal.id,
        plc_id:     plcId,
        target_time: 15,
      })
      toast.success('Camera assigned')
      setAssignModal(null)
      load()
    } catch(e) { toast.error(e.message || 'Failed to assign') }
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-end">
        <div><h1 className="page-title">Camera Master</h1><p className="page-subtitle">RTSP stream configuration</p></div>
        <button className="btn-primary" onClick={() => setShowModal(true)}><Plus size={16}/>New Camera</button>
      </div>

      {/* Video Save Path Config */}
      <div className="glass dark:bg-slate-800/50 dark:border-slate-700 p-4">
        <div className="flex items-center gap-2 mb-3">
          <span className="text-sm font-bold text-gray-700 dark:text-slate-200">Video Save Path</span>
          {videoPathInfo?.free_gb != null && (
            <span className="text-xs text-gray-400 ml-auto">
              Disk free: <strong className={videoPathInfo.free_gb < 10 ? 'text-red-500' : 'text-emerald-600'}>{videoPathInfo.free_gb} GB</strong>
            </span>
          )}
        </div>
        <div className="flex gap-2 items-center">
          <input
            className="input-field font-mono text-sm flex-1"
            value={videoPath}
            onChange={e => setVideoPath(e.target.value)}
            placeholder="D:\recordings"
            onKeyDown={e => { if (e.key === 'Enter') saveVideoPath() }}
          />
          <button
            className="btn-secondary text-sm whitespace-nowrap flex items-center gap-1"
            onClick={() => setShowPicker(true)}
            title="Browse folders on this server"
          >
            <FolderOpen size={14}/> Browse
          </button>
          <button className="btn-primary text-sm whitespace-nowrap" onClick={saveVideoPath} disabled={savingPath}>
            {savingPath ? 'Saving...' : 'Save Path'}
          </button>
        </div>
        {videoPathInfo?.effective_path && (
          <p className="text-xs text-gray-400 mt-2">
            Active: <code className="bg-gray-100 dark:bg-slate-700 px-1 rounded">{videoPathInfo.effective_path}</code>
          </p>
        )}
        <p className="text-xs text-gray-400 mt-2">
          Videos auto-save as: <code className="bg-gray-100 dark:bg-slate-700 px-1 rounded">path/Zone/Line/Machine/Date/Shift/Slot/partcode.mp4</code>
        </p>
      </div>

      <div className="glass overflow-hidden">
         <div className="overflow-x-auto text-sm">
            <table className="w-full text-left">
              <thead className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
                <tr>
                  <th className="px-4 py-3">Name</th>
                  <th className="px-4 py-3">IP / URI</th>
                  <th className="px-4 py-3">Port</th>
                  <th className="px-4 py-3">Assigned To</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Preview</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
               {loading?<tr><td colSpan={7} className="px-4 py-3"><div className="skeleton h-4"/></td></tr>:data.map(c=>{
                 const b = getBinding(c.id)
                 const m = b ? getMachine(b.machine_id) : null
                 const ping = pings[c.ip]
                 return (
                 <tr key={c.id||c.name} className="table-row-hover">
                   <td className="px-4 py-3 font-medium">{c.name}</td>
                   <td className="px-4 py-3 opacity-80 font-mono text-xs">{c.ip}{c.path}</td>
                   <td className="px-4 py-3">{c.port}</td>
                   <td className="px-4 py-3 text-xs">
                     {m ? (
                       <div className="space-y-0.5">
                         <div className="font-semibold text-gray-800 dark:text-slate-100">{m.machine_name || b.machine_id}</div>
                         <div className="text-gray-400">{m.zone_name || '—'} → {m.line_name || '—'}</div>
                       </div>
                     ) : (
                       <span className="text-gray-400 italic">Not assigned</span>
                     )}
                   </td>
                   <td className="px-4 py-3">
                     {ping === 'loading' ? (
                       <span className="text-xs text-gray-400">...</span>
                     ) : ping ? (
                       <span className={`inline-flex items-center gap-1 text-xs font-semibold ${ping.ok ? 'text-emerald-600' : 'text-red-500'}`}>
                         <span className={`w-2 h-2 rounded-full ${ping.ok ? 'bg-emerald-500' : 'bg-red-500'}`}/>
                         {ping.ok ? `Online ${ping.ms}ms` : 'Offline'}
                       </span>
                     ) : (
                       <button onClick={() => pingCam(c.ip)} className="text-xs text-blue-600 hover:underline">Ping</button>
                     )}
                   </td>
                   <td className="px-4 py-3"><CameraPreview camera={c} /></td>
                   <td className="px-4 py-3 text-right">
                     <div className="inline-flex gap-1.5">
                       <button onClick={() => openAssign(c)}
                         className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-semibold rounded-lg bg-blue-50 text-blue-700 hover:bg-blue-100 border border-blue-200 transition-colors">
                         <Edit2 size={12}/> Assign
                       </button>
                       <button onClick={()=>remove(c.id||c.name)} className="text-slate-400 hover:text-red-500 p-1.5"><Trash2 size={16}/></button>
                     </div>
                   </td>
                 </tr>
                 )
               })}
              </tbody>
            </table>
         </div>
      </div>

      {/* Add Camera Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="card w-full max-w-md p-6"><h3 className="text-lg font-bold mb-4">Add Camera</h3>
            <form onSubmit={save} className="space-y-4">
              <div><label>Name</label><input className="input-field mt-1" value={form.name} onChange={e=>setForm({...form,name:e.target.value})} required/></div>
              <div className="grid grid-cols-2 gap-4">
                <div><label>IP Address</label><input className="input-field mt-1" value={form.ip} onChange={e=>setForm({...form,ip:e.target.value})} required/></div>
                <div><label>Port</label><input type="number" className="input-field mt-1" value={form.port} onChange={e=>setForm({...form,port:Number(e.target.value)})} required/></div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div><label>Username</label><input className="input-field mt-1" value={form.username} onChange={e=>setForm({...form,username:e.target.value})} required/></div>
                <div><label>Password</label><input type="password" className="input-field mt-1" value={form.password} onChange={e=>setForm({...form,password:e.target.value})} required/></div>
              </div>
              <div><label>Path (optional)</label><input className="input-field mt-1" value={form.path} onChange={e=>setForm({...form,path:e.target.value})} placeholder="/h264/ch1/main"/></div>
              <div className="flex justify-end gap-2 mt-4"><button type="button" className="btn-secondary" onClick={()=>setShowModal(false)}>Cancel</button><button type="submit" className="btn-primary">Save</button></div>
            </form>
          </div>
        </div>
      )}

      {/* Assign Camera to Machine Modal */}
      {assignModal && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick={()=>setAssignModal(null)}>
          <div className="card w-full max-w-md p-6" onClick={e=>e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-1">Assign Camera</h3>
            <p className="text-xs text-gray-400 mb-4">
              <span className="font-semibold text-blue-600">{assignModal.name}</span> ({assignModal.ip}) → select zone, line, machine
            </p>
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-semibold text-gray-600 dark:text-slate-300 mb-1">Zone</label>
                <select className="input-field" value={assignForm.zone_id}
                  onChange={e => setAssignForm({zone_id: e.target.value, line_id: '', machine_id: ''})}>
                  <option value="">Select Zone</option>
                  {zones.map(z => <option key={z.zone_id} value={z.zone_id}>{z.zone_name}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 dark:text-slate-300 mb-1">Line</label>
                <select className="input-field" value={assignForm.line_id} disabled={!assignForm.zone_id}
                  onChange={e => setAssignForm({...assignForm, line_id: e.target.value, machine_id: ''})}>
                  <option value="">Select Line</option>
                  {filteredLines.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 dark:text-slate-300 mb-1">Machine</label>
                <select className="input-field" value={assignForm.machine_id} disabled={!assignForm.line_id}
                  onChange={e => setAssignForm({...assignForm, machine_id: e.target.value})}>
                  <option value="">Select Machine</option>
                  {filteredMachines.map(m => (
                    <option key={m.machine_id} value={m.machine_id}>
                      {m.machine_name} {m.camera_id ? `(cam: ${m.camera_name || m.camera_id})` : ''}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-5">
              <button className="btn-secondary text-sm" onClick={()=>setAssignModal(null)}>Cancel</button>
              <button className="btn-primary text-sm" onClick={saveAssign} disabled={!assignForm.machine_id}>Save Assignment</button>
            </div>
          </div>
        </div>
      )}

      {showPicker && (
        <FolderPickerModal
          initial={videoPath || ''}
          onClose={() => setShowPicker(false)}
          onPick={(p) => { setVideoPath(p); setShowPicker(false); }}
          toast={toast}
        />
      )}
    </div>
  )
}


/* ════════════════════════════════════════════════════════════════════
 * FolderPickerModal — server-side directory browser
 * ════════════════════════════════════════════════════════════════════
 * Browsers can't expose absolute filesystem paths through <input
 * type="file">, so the picker walks the server's filesystem via three
 * backend endpoints:
 *   GET  /api/config/list-drives   → drive roots (C:\, D:\, …)
 *   GET  /api/config/list-dir?path → immediate sub-folders of a path
 *   POST /api/config/create-dir    → create a new sub-folder in place
 *
 * UX:
 *   • Drive list shown when no path picked yet.
 *   • Folder list shown for the current path; click ↗ to drill in.
 *   • ⬅ Back goes to parent.  Breadcrumbs at the top let you jump
 *     directly to any ancestor.
 *   • + New Folder lets you carve a fresh target on the spot.
 *   • Footer shows the selected path + "Use this folder" button.
 */
function FolderPickerModal({ initial, onClose, onPick, toast }) {
  const [drives, setDrives]   = useState([])
  const [path, setPath]       = useState(initial || '')
  const [folders, setFolders] = useState([])
  const [parent, setParent]   = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState('')
  const [creating, setCreating]     = useState(false)
  const [newName, setNewName]       = useState('')

  // Load drives on first mount; if `initial` was an existing path, we
  // start by listing it directly.
  useEffect(() => {
    api.listDrives()
      .then(r => setDrives(r.drives || []))
      .catch(e => setError(e?.message || 'Could not list drives'))
    if (initial) listAt(initial).catch(() => setPath(''))
  }, [])

  const listAt = async (p) => {
    setLoading(true); setError('')
    try {
      const r = await api.listDir(p)
      setPath(r.path); setParent(r.parent); setFolders(r.folders || [])
    } catch (e) {
      setError(e?.message || 'Could not read directory')
    } finally { setLoading(false) }
  }

  const goBack = () => { if (parent) listAt(parent); else { setPath(''); setFolders([]); setParent(null); } }

  const createNew = async () => {
    if (!path) { setError('Pick a parent folder first'); return }
    if (!newName.trim()) { setError('Enter a folder name'); return }
    setCreating(true); setError('')
    try {
      const r = await api.createDir({ parent: path, name: newName.trim() })
      toast?.success?.('Folder created')
      setNewName('')
      // Refresh the listing so the new folder appears.
      await listAt(path)
      // And drill into it for convenience.
      await listAt(r.path)
    } catch (e) { setError(e?.message || 'Could not create folder') }
    finally { setCreating(false) }
  }

  // Build clickable breadcrumb segments from the current path.
  const crumbs = (() => {
    if (!path) return []
    const norm = path.replace(/\\/g, '/').replace(/\/+$/, '')
    const parts = norm.split('/').filter(Boolean)
    const sep = path.includes('\\') ? '\\' : '/'
    const out = []
    let acc = ''
    parts.forEach((p, i) => {
      if (i === 0 && /^[A-Za-z]:$/.test(p)) {
        acc = p + sep
      } else {
        acc = acc ? `${acc}${sep}${p}` : `${sep}${p}`
      }
      out.push({ label: p, path: acc.replace(/\\\\/g, '\\') })
    })
    return out
  })()

  return (
    <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4"
         onClick={onClose}>
      <div className="bg-white dark:bg-slate-800 rounded-xl shadow-2xl w-full max-w-3xl max-h-[80vh] flex flex-col"
           onClick={e => e.stopPropagation()}>

        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-slate-200 dark:border-slate-700">
          <div className="flex items-center gap-2">
            <FolderOpen size={20} className="text-blue-600"/>
            <h3 className="text-lg font-bold">Pick a folder</h3>
          </div>
          <button onClick={onClose} className="btn-icon" title="Close"><X size={18}/></button>
        </div>

        {/* Toolbar — back / breadcrumbs / new-folder */}
        <div className="p-3 border-b border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40">
          <div className="flex items-center gap-2 flex-wrap">
            <button className="btn-secondary text-sm flex items-center gap-1"
                    onClick={goBack} disabled={!parent && !path}>
              <ChevronLeft size={14}/> Back
            </button>
            <button className="btn-secondary text-sm flex items-center gap-1"
                    onClick={() => { setPath(''); setFolders([]); setParent(null); }}>
              <HardDrive size={14}/> Drives
            </button>
            <div className="flex-1 flex items-center gap-1 flex-wrap text-sm font-mono">
              {crumbs.map((c, i) => (
                <span key={i} className="flex items-center gap-1">
                  {i > 0 && <span className="text-slate-400">›</span>}
                  <button
                    className="px-1.5 py-0.5 rounded hover:bg-blue-100 dark:hover:bg-slate-700 text-blue-700 dark:text-blue-300"
                    onClick={() => listAt(c.path)}>
                    {c.label}
                  </button>
                </span>
              ))}
            </div>
          </div>
          {path && (
            <div className="flex items-center gap-2 mt-2">
              <input
                className="input-field text-sm flex-1"
                placeholder="New folder name (e.g. recordings)"
                value={newName}
                onChange={e => setNewName(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') createNew() }}
              />
              <button className="btn-primary text-sm flex items-center gap-1"
                      onClick={createNew} disabled={creating || !newName.trim()}>
                <FolderPlus size={14}/> {creating ? 'Creating…' : 'New Folder'}
              </button>
            </div>
          )}
        </div>

        {/* Body — drives list OR folder list */}
        <div className="flex-1 overflow-y-auto p-3">
          {error && (
            <div className="px-3 py-2 mb-3 rounded bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300 text-sm">
              {error}
            </div>
          )}

          {loading ? (
            <div className="text-center py-12 text-slate-500">Loading…</div>
          ) : !path ? (
            /* ── Drive list ──────────────────────────────────────── */
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {drives.map(d => (
                <button key={d.path}
                        className="flex items-center gap-3 p-3 rounded-lg border border-slate-200 dark:border-slate-700 hover:border-blue-500 hover:bg-blue-50 dark:hover:bg-slate-700/50 text-left transition"
                        onClick={() => listAt(d.path)}>
                  <HardDrive size={20} className="text-slate-500"/>
                  <div className="flex-1 min-w-0">
                    <div className="font-bold text-sm">{d.label}</div>
                    <div className="text-xs text-slate-400 font-mono truncate">{d.path}</div>
                  </div>
                  {d.free_gb != null && (
                    <span className={`text-xs font-bold ${d.free_gb < 10 ? 'text-red-500' : 'text-emerald-600'}`}>
                      {d.free_gb} GB free
                    </span>
                  )}
                </button>
              ))}
              {drives.length === 0 && (
                <div className="col-span-2 text-center py-12 text-slate-400">
                  No drives detected.
                </div>
              )}
            </div>
          ) : (
            /* ── Folder list at current path ─────────────────────── */
            folders.length === 0 ? (
              <div className="text-center py-12 text-slate-400 italic">
                No sub-folders here.
                <div className="text-xs mt-1">
                  Use <strong>+ New Folder</strong> above to create one,
                  or click <strong>Use this folder</strong> below to pick this one.
                </div>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {folders.map(f => (
                  <button key={f.path}
                          className="flex items-center gap-3 p-2.5 rounded-lg border border-slate-200 dark:border-slate-700 hover:border-blue-500 hover:bg-blue-50 dark:hover:bg-slate-700/50 text-left transition"
                          onClick={() => listAt(f.path)}>
                    <Folder size={18} className="text-amber-500 flex-shrink-0"/>
                    <span className="text-sm font-mono truncate">{f.name}</span>
                  </button>
                ))}
              </div>
            )
          )}
        </div>

        {/* Footer — selected path + use-this-folder */}
        <div className="p-3 border-t border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 flex items-center gap-2 flex-wrap">
          <div className="flex-1 min-w-0">
            <div className="text-xs text-slate-500 mb-0.5">Selected</div>
            <code className="text-sm font-mono bg-white dark:bg-slate-800 px-2 py-1 rounded border border-slate-200 dark:border-slate-700 inline-block max-w-full truncate">
              {path || '(none)'}
            </code>
          </div>
          <button className="btn-secondary text-sm" onClick={onClose}>Cancel</button>
          <button className="btn-primary text-sm flex items-center gap-1"
                  onClick={() => onPick(path)}
                  disabled={!path}>
            <Check size={14}/> Use this folder
          </button>
        </div>
      </div>
    </div>
  )
}
