import { useState, useEffect } from 'react'
import { api } from '../../lib/api'
import { Download } from 'lucide-react'

export default function CycleTimeReport() {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true); api.getCycleHistory({ limit: 100 }).then(setData).catch().finally(()=>setLoading(false))
  }, [])

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-end">
         <div><h1 className="page-title">Cycle Time Report</h1><p className="page-subtitle">Historical cycle data</p></div>
         <button className="btn-secondary"><Download size={16}/> Export CSV</button>
      </div>
      <div className="glass overflow-hidden"><div className="overflow-x-auto text-sm">
         <table className="w-full text-left">
            <thead className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
              <tr><th className="px-4 py-3">Cycle #</th><th className="px-4 py-3">Machine</th><th className="px-4 py-3">Line</th><th className="px-4 py-3">Start Time</th><th className="px-4 py-3">Duration</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
              {loading?<tr><td colSpan={5}><div className="skeleton h-4 m-2"/></td></tr>:data.map((c,i)=>(
                 <tr key={i} className="table-row-hover">
                   <td className="px-4 py-3 font-medium">#{c.cycle_number}</td><td className="px-4 py-3">{c.machine_name}</td><td className="px-4 py-3">{c.line_name}</td><td className="px-4 py-3">{new Date(c.start_time).toLocaleString()}</td><td className="px-4 py-3"><span className={`badge ${c.duration>30?'badge-danger':'badge-success'}`}>{c.duration}s</span></td>
                 </tr>
              ))}
            </tbody>
         </table>
      </div></div>
    </div>
  )
}
