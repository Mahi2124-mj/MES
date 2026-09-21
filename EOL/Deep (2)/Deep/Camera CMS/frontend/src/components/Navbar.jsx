import { useState, useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Menu, Sun, Moon, Zap, LogOut, User } from 'lucide-react'
import { useTheme } from '../context/ThemeContext'
import { useAuth } from '../context/AuthContext'

const PT = {
  '/':                       { label: 'Dashboard',     sub: 'System overview',          crumbs: [] },
  '/masters/zones':          { label: 'Zone Master',   sub: 'Manage factory zones',     crumbs: ['Masters'] },
  '/masters/lines':          { label: 'Line Master',   sub: 'Production lines',         crumbs: ['Masters'] },
  '/masters/machines':       { label: 'Machine Master',sub: 'Machine registry',         crumbs: ['Masters'] },
  '/masters/cameras':        { label: 'Camera Master', sub: 'RTSP source registry',     crumbs: ['Masters'] },
  '/masters/plcs':           { label: 'PLC Master',    sub: 'PLC controllers',          crumbs: ['Masters'] },
  '/config/camera-binding':  { label: 'Camera Config', sub: 'Bind cameras to machines', crumbs: ['Configuration'] },
  '/config/shifts':          { label: 'Shift Config',  sub: 'Shift schedule setup',     crumbs: ['Configuration'] },
  '/monitor/camera-grid':    { label: 'Camera Grid',   sub: 'Live camera feeds',        crumbs: ['Monitor'] },
  '/monitor/cycles':         { label: 'Cycle Monitor', sub: 'Trigger & track cycles',   crumbs: ['Monitor'] },
  '/reports':                { label: 'Reports',       sub: 'Cycle time analytics',     crumbs: [] },
}

export default function Navbar({ onMenuClick }) {
  const loc = useLocation(), nav = useNavigate()
  const { theme, toggleTheme } = useTheme()
  const { user, logout } = useAuth()
  const [showUser, setShowUser] = useState(false)
  const uRef = useRef(null)
  const pg = PT[loc.pathname] || { label: 'Camera EMS Portal', sub: '', crumbs: [] }

  useEffect(() => {
    const h = e => { if (uRef.current && !uRef.current.contains(e.target)) setShowUser(false) }
    document.addEventListener('mousedown', h); return () => document.removeEventListener('mousedown', h)
  }, [])

  return (
    <header className="sticky top-0 z-30 h-14 flex items-center px-3 sm:px-4 gap-2 sm:gap-3 bg-white/95 dark:bg-slate-900/95 backdrop-blur-md border-b border-slate-200 dark:border-slate-800 shadow-sm">
      <button onClick={onMenuClick} className="md:hidden flex items-center justify-center w-11 h-11 rounded-xl text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"><Menu size={20} /></button>
      <div className="flex-1 min-w-0">
        {pg.crumbs?.length > 0 && <div className="hidden sm:flex items-center gap-1 mb-0.5">
          <span className="text-[10px] text-slate-400 hover:text-blue-500 cursor-pointer" onClick={() => nav('/')}>Home</span>
          {pg.crumbs.map((c,i)=><span key={i} className="flex items-center gap-1"><span className="text-[10px] text-slate-300 dark:text-slate-700">/</span><span className="text-[10px] text-slate-400">{c}</span></span>)}
          <span className="text-[10px] text-slate-300 dark:text-slate-700">/</span>
          <span className="text-[10px] font-semibold text-blue-500">{pg.label}</span>
        </div>}
        <h1 className="text-sm font-bold text-slate-800 dark:text-slate-100 truncate">{pg.label}</h1>
        {pg.sub && !pg.crumbs?.length && <p className="text-[11px] text-slate-400 hidden sm:block">{pg.sub}</p>}
      </div>
      <div className="flex items-center gap-1">
        <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800/50 mr-1">
          <Zap size={10} className="text-emerald-500" /><span className="text-[10px] font-bold text-emerald-600 dark:text-emerald-400">LIVE</span>
        </div>
        <button onClick={toggleTheme} className="p-2 rounded-lg text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors">
          {theme==='dark'?<Sun size={16} className="text-amber-400"/>:<Moon size={16}/>}
        </button>
        <div ref={uRef} className="relative">
          <button onClick={()=>setShowUser(s=>!s)} className="flex items-center gap-2 px-2.5 py-1.5 rounded-xl text-xs font-semibold text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 transition-all">
            <div className="w-6 h-6 rounded-full bg-blue-500/15 border border-blue-500/30 flex items-center justify-center"><User size={12} className="text-blue-500"/></div>
            <span className="hidden sm:block">{user?.username??'User'}</span>
            <span className="hidden sm:block text-[10px] text-slate-400 font-normal capitalize">{user?.role}</span>
          </button>
          {showUser&&<div className="absolute right-0 top-full mt-2 w-44 z-50 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-xl py-1.5">
            <div className="px-3 py-2 border-b border-slate-100 dark:border-slate-700 mb-1"><p className="text-xs font-semibold text-slate-800 dark:text-slate-200">{user?.username}</p><p className="text-[10px] text-slate-400 capitalize">{user?.role}</p></div>
            <button onClick={()=>{logout();nav('/login',{replace:true})}} className="w-full flex items-center gap-2 px-3 py-2 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20"><LogOut size={13}/>Sign Out</button>
          </div>}
        </div>
      </div>
    </header>
  )
}
