import { useState, useEffect } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'

const EMPTY_FORM = {
  machine_id: '',
  camera_id: '',
  plc_id: '',
  trigger_type: 'MAIN',
  m_bit_address: '',
  target_time: 30,
}

export default function CameraConfig() {
  const [data, setData] = useState([])
  const [machines, setMachines] = useState([])
  const [cameras, setCameras] = useState([])
  const [plcs, setPlcs] = useState([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [selectedZone, setSelectedZone] = useState('')
  const [selectedLine, setSelectedLine] = useState('')
  const [form, setForm] = useState(EMPTY_FORM)
  const toast = useToast()

  const uniqueZones = Array.from(new Map(machines.map(m=>[m.zone_id, {zone_id:m.zone_id, zone_name:m.zone_name}])).values())
  const filteredLines = Array.from(new Map(machines.filter(m=>m.zone_id===selectedZone).map(m=>[m.line_id, {line_id:m.line_id, line_name:m.line_name}])).values())
  const filteredMachines = machines.filter(m => m.zone_id === selectedZone && m.line_id === selectedLine)

  const load = async () => { setLoading(true); try { const [cfg, m, c, p] = await Promise.all([api.getCameraConfigs(), api.getMachines(), api.getCameras(), api.getPlcs()]); setData(cfg); setMachines(m); setCameras(c); setPlcs(p) } catch (e) { toast.error('Error loading config data') } setLoading(false) }
  useEffect(() => { load() }, [])

  const resetForm = () => { setForm(EMPTY_FORM); setSelectedZone(''); setSelectedLine('') }

  const save = async e => {
    e.preventDefault()
    // SUB mode requires M-bit address; MAIN mode requires PLC
    if (form.trigger_type === 'SUB' && !form.m_bit_address.trim()) {
      toast.error('M-bit address is required for SUB trigger (e.g. M100)')
      return
    }
    if (form.trigger_type === 'MAIN' && !form.plc_id) {
      toast.error('PLC is required for MAIN trigger')
      return
    }
    try {
      const payload = { ...form, m_bit_address: form.m_bit_address.trim().toUpperCase() }
      // For SUB mode, plc_id may be blank (sub-PLCs live in MES Postgres, not CMS)
      await api.saveCameraConfig(payload)
      toast.success('Saved binding')
      setShowModal(false)
      resetForm()
      load()
    } catch (e) {
      toast.error(e.message)
    }
  }
  const remove = async (id) => { if(!confirm('Delete binding?'))return; try { await api.deleteCameraConfig(id); toast.success('Deleted'); load() } catch(e){ toast.error(e.message) } }

  // Camera identifier: prefer IP (all cameras are Panasonic, one type — name causes confusion)
  const camLabel = c => c?.ip || c?.id || '—'
  const camById = id => cameras.find(c => c.id === id)

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-end">
        <div><h1 className="page-title">Camera Configuration</h1><p className="page-subtitle">Bind Cameras and PLCs to Machines</p></div>
        <button className="btn-primary" onClick={() => { resetForm(); setShowModal(true) }}><Plus size={16}/>New Binding</button>
      </div>

      <div className="glass overflow-hidden">
        <div className="overflow-x-auto text-sm">
          <table className="w-full text-left">
            <thead className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
              <tr>
                <th className="px-4 py-3">Machine</th>
                <th className="px-4 py-3">Camera (IP)</th>
                <th className="px-4 py-3">Trigger</th>
                <th className="px-4 py-3">PLC / M-bit</th>
                <th className="px-4 py-3">Target Cycle (s)</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
              {loading?<tr><td colSpan={6} className="px-4 py-3"><div className="skeleton h-4"/></td></tr>:data.map(b=>{
                const trig = (b.trigger_type || 'MAIN').toUpperCase()
                const trigBadge = trig === 'SUB' ? 'badge badge-info' : 'badge badge-success'
                return (
                  <tr key={b.id} className="table-row-hover">
                    <td className="px-4 py-3 font-medium text-blue-600 dark:text-blue-400">{machines.find(m=>m.machine_id===b.machine_id)?.machine_name || b.machine_id}</td>
                    <td className="px-4 py-3 font-mono text-xs">{camLabel(camById(b.camera_id))}</td>
                    <td className="px-4 py-3"><span className={trigBadge}>{trig}</span></td>
                    <td className="px-4 py-3 text-xs">
                      {trig === 'SUB'
                        ? <span className="font-mono">{b.m_bit_address || '—'}</span>
                        : (plcs.find(p=>p.id===b.plc_id)?.description || b.plc_id || '—')}
                    </td>
                    <td className="px-4 py-3"><span className="badge badge-warning">{b.target_time}s</span></td>
                    <td className="px-4 py-3 text-right"><button onClick={()=>remove(b.id)} className="text-slate-400 hover:text-red-500"><Trash2 size={16}/></button></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {showModal && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
          <div className="card w-full max-w-md p-6 max-h-[90vh] overflow-y-auto">
            <h3 className="text-lg font-bold mb-4">Add Binding</h3>
            <form onSubmit={save} className="space-y-4">
              {/* Trigger Type — controls which fields show below */}
              <div>
                <label className="block mb-2 font-medium">Trigger Type</label>
                <div className="flex gap-4">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="trigger_type"
                      value="MAIN"
                      checked={form.trigger_type === 'MAIN'}
                      onChange={() => setForm({...form, trigger_type: 'MAIN', m_bit_address: ''})}
                    />
                    <span><strong>MAIN</strong> <span className="text-xs text-slate-500">(L108/L109 → per-cycle MP4 by barcode)</span></span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="trigger_type"
                      value="SUB"
                      checked={form.trigger_type === 'SUB'}
                      onChange={() => setForm({...form, trigger_type: 'SUB', plc_id: ''})}
                    />
                    <span><strong>SUB</strong> <span className="text-xs text-slate-500">(M-bit → single TS for shift)</span></span>
                  </label>
                </div>
              </div>

              <div><label>Zone filter</label><select className="input-field mt-1" value={selectedZone} onChange={e=>{setSelectedZone(e.target.value); setSelectedLine(''); setForm({...form, machine_id:''})}}><option value="">- All Zones -</option>{uniqueZones.map(z=><option key={z.zone_id} value={z.zone_id}>{z.zone_name}</option>)}</select></div>
              <div><label>Line filter</label><select className="input-field mt-1" value={selectedLine} onChange={e=>{setSelectedLine(e.target.value); setForm({...form, machine_id:''})}} disabled={!selectedZone}><option value="">- Select Line -</option>{filteredLines.map(l=><option key={l.line_id} value={l.line_id}>{l.line_name}</option>)}</select></div>
              <div><label>Machine Details</label><select className="input-field mt-1" value={form.machine_id} onChange={e=>setForm({...form,machine_id:e.target.value})} required disabled={!selectedLine}><option value="">- Select Machine -</option>{filteredMachines.map(m=><option key={m.machine_id} value={m.machine_id}>{m.machine_name}</option>)}</select></div>

              <div>
                <label>Camera</label>
                <select className="input-field mt-1" value={form.camera_id} onChange={e=>setForm({...form,camera_id:e.target.value})} required>
                  <option value="">- Select Camera by IP -</option>
                  {cameras.map(c=><option key={c.id} value={c.id}>{c.ip}{c.port?`:${c.port}`:''}</option>)}
                </select>
              </div>

              {/* Conditional: MAIN needs PLC, SUB needs M-bit */}
              {form.trigger_type === 'MAIN' ? (
                <div>
                  <label>Main PLC (L108/L109)</label>
                  <select className="input-field mt-1" value={form.plc_id} onChange={e=>setForm({...form,plc_id:e.target.value})} required>
                    <option value="">- Select -</option>
                    {plcs.map(p=><option key={p.id} value={p.id}>{p.description} ({p.ip})</option>)}
                  </select>
                </div>
              ) : (
                <div>
                  <label>Sub-PLC M-bit Address</label>
                  <input
                    type="text"
                    className="input-field mt-1 font-mono"
                    placeholder="e.g. M100"
                    value={form.m_bit_address}
                    onChange={e=>setForm({...form, m_bit_address: e.target.value.toUpperCase()})}
                    required
                  />
                  <p className="text-xs text-slate-500 mt-1">Sub-PLC rising edge bit. Sub-PLC IP/port is configured in MES &gt; Sub-machine Config.</p>
                </div>
              )}

              <div>
                <label>Target Cycle Time (s)</label>
                <input type="number" className="input-field mt-1" value={form.target_time} onChange={e=>setForm({...form,target_time:Number(e.target.value)})} required/>
              </div>

              <div className="flex justify-end gap-2 mt-4">
                <button type="button" className="btn-secondary" onClick={()=>{setShowModal(false); resetForm()}}>Cancel</button>
                <button type="submit" className="btn-primary">Save Binding</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
