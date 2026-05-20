import { useState, useEffect } from 'react'
import { api } from '../../lib/api'
import { useToast } from '../../context/ToastContext'
import { Play, Square } from 'lucide-react'

export default function CycleMonitor() {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)
  const toast = useToast()

  const load = async () => { setLoading(true); try { setData(await api.getCycleStatus()) } catch(e){} setLoading(false) }
  useEffect(() => { load(); const t = setInterval(load, 5000); return ()=>clearInterval(t) }, [])

  const trigger = async (mId, action) => {
    try { await api.triggerCycle({ machine_id: mId, action }); load(); toast.success('Cycle ' + action + 'ed') } catch(e) { toast.error(e.message) }
  }

  return (
    <div className="space-y-4">
      <h1 className="page-title">Cycle Monitor</h1><p className="page-subtitle">Manual cycle triggers & status</p>
      <div className="glass overflow-hidden">
         <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
              <tr><th className="px-4 py-3">Machine</th><th className="px-4 py-3">Zone</th><th className="px-4 py-3">Status</th><th className="px-4 py-3">Current Cycle</th><th className="px-4 py-3 text-right">Actions</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
              {loading?<tr><td colSpan={5}><div className="skeleton h-4 m-2"/></td></tr>:data.map(m=>(
                 <tr key={m.machine_id} className="table-row-hover">
                   <td className="px-4 py-3 font-medium">{m.machine_name}</td><td className="px-4 py-3">{m.zone_name}</td>
                   <td className="px-4 py-3">{m.recording ? <span className="badge badge-danger">Recording</span> : <span className="badge badge-success">Idle</span>}</td>
                   <td className="px-4 py-3 text-xs">{m.recording && m.start_time ? `Cycle #${m.cycle_number} | Start: ${new Date(m.start_time).toLocaleTimeString()}` : '-'}</td>
                   <td className="px-4 py-3 text-right flex justify-end gap-2">
                     <button onClick={()=>trigger(m.machine_id, 'start')} disabled={m.recording} className={`p-2 rounded-lg text-white ${m.recording?'bg-slate-300':'bg-emerald-500 hover:bg-emerald-600'} transition-colors`}><Play size={14}/></button>
                     <button onClick={()=>trigger(m.machine_id, 'stop')} disabled={!m.recording} className={`p-2 rounded-lg text-white ${!m.recording?'bg-slate-300':'bg-red-500 hover:bg-red-600'} transition-colors`}><Square size={14}/></button>
                   </td>
                 </tr>
              ))}
            </tbody>
         </table>
      </div>
    </div>
  )
}
