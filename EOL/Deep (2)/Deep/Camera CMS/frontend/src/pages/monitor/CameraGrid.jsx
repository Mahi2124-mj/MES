import { useState, useEffect } from 'react'
import { api, frameUrl, streamUrl } from '../../lib/api'
import { useToast } from '../../context/ToastContext'
import { CameraOff } from 'lucide-react'

export default function CameraGrid() {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(true)
  const [frameTick, setFrameTick] = useState(Date.now())
  const [fallbackFrames, setFallbackFrames] = useState({})
  const toast = useToast()

  const load = async () => { setLoading(true); try { setData(await api.getCameraGrid()) } catch (e) { toast.error('Error loading cameras') } setLoading(false) }
  useEffect(() => { load() }, [])
  useEffect(() => {
    const timer = setInterval(() => setFrameTick(Date.now()), 2000)
    return () => clearInterval(timer)
  }, [])

  const getPreviewUrl = cameraId => (
    fallbackFrames[cameraId]
      ? frameUrl(cameraId, frameTick)
      : streamUrl(cameraId)
  )

  return (
    <div className="space-y-4 h-full flex flex-col">
      <div className="flex justify-between items-end shrink-0">
        <div><h1 className="page-title">Live Camera Grid</h1><p className="page-subtitle">Real-time factory monitoring</p></div>
        <button onClick={load} className="btn-secondary">Refresh</button>
      </div>

      <div className="flex-1 overflow-y-auto">
         <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4 pb-4">
           {loading ? [...Array(6)].map((_,i)=><div key={i} className="aspect-video skeleton rounded-xl"/>) : 
            data.filter(m=>m.has_camera).map(m => (
             <div key={m.machine_id} className="glass overflow-hidden rounded-xl group relative">
                <div className="absolute top-0 left-0 right-0 p-2 z-10 bg-gradient-to-b from-black/60 to-transparent flex justify-between items-start">
                  <div>
                    <h3 className="text-white font-semibold text-sm drop-shadow-md">{m.machine_name}</h3>
                    <p className="text-white/80 text-[10px] drop-shadow">{m.zone_name}</p>
                  </div>
                  {m.recording ? <div className="live-dot red" title="Recording"/> : <div className="live-dot" title="Live"/>}
                </div>
                
                <div className="aspect-video bg-slate-900 w-full relative">
                  <img
                    src={getPreviewUrl(m.camera_id)}
                    alt={m.camera_name}
                    className="w-full h-full object-cover"
                    onLoad={(e)=>{
                      e.target.style.display='block'
                      e.target.nextSibling.style.display='none'
                    }}
                    onError={(e)=>{
                      if (!fallbackFrames[m.camera_id]) {
                        setFallbackFrames(prev => ({ ...prev, [m.camera_id]: true }))
                        return
                      }
                      e.target.style.display='none'
                      e.target.nextSibling.style.display='flex'
                    }}
                  />
                  <div className="absolute inset-0 flex-col items-center justify-center text-slate-500 hidden pb-4">
                    <CameraOff size={32} className="mb-2 opacity-50"/>
                    <span className="text-xs font-semibold">Stream Offline</span>
                  </div>
                </div>
             </div>
           ))}
           {data.filter(m=>m.has_camera).length === 0 && !loading && (
             <div className="col-span-full py-12 text-center text-slate-500">No cameras configured yet. Go to Configuration to bind cameras.</div>
           )}
         </div>
      </div>
    </div>
  )
}
