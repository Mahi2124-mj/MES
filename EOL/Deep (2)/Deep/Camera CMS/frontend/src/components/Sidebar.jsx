import { useState, useEffect, useRef } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  LayoutDashboard, Database, MapPin, GitBranch, Cpu, Camera,
  Network, ChevronDown, PanelLeftClose, Settings2, Grid2x2,
  Video, FileBarChart, Clock, Layers, HardDrive
} from 'lucide-react'
import logo from '../assets/logo.jpg'

const NAV = [
  { label: 'Dashboard', to: '/', icon: LayoutDashboard },
  {
    label: 'Masters', icon: Database,
    children: [
      { to: '/masters/zones',    label: 'Zone Master',    icon: MapPin    },
      { to: '/masters/lines',    label: 'Line Master',    icon: GitBranch },
      { to: '/masters/machines', label: 'Machine Master', icon: Cpu       },
      { to: '/masters/cameras',  label: 'Camera Master',  icon: Camera    },
      { to: '/masters/plcs',     label: 'PLC Master',     icon: Network   },
    ],
  },
  {
    label: 'Configuration', icon: Settings2,
    children: [
      { to: '/config/camera-binding', label: 'Camera Config',    icon: Layers     },
      { to: '/config/shifts',         label: 'Shift Config',     icon: Clock      },
      { to: '/config/system',         label: 'System Settings',  icon: HardDrive  },
    ],
  },
  {
    label: 'Monitor', icon: Grid2x2,
    children: [
      { to: '/monitor/camera-grid', label: 'Camera Grid',    icon: Grid2x2 },
      { to: '/monitor/cycles',      label: 'Cycle Monitor',  icon: Video   },
    ],
  },
  { label: 'Reports', to: '/reports', icon: FileBarChart },
]

function NavLeaf({ item, collapsed, onNavigate }) {
  const Icon = item.icon
  const btnRef = useRef(null)
  const [tipY, setTipY] = useState(null)

  if (collapsed) {
    return (
      <div className="px-2">
        <NavLink
          ref={btnRef}
          to={item.to}
          end={item.to === '/'}
          onClick={onNavigate}
          onMouseEnter={() => { const r = btnRef.current?.getBoundingClientRect(); if (r) setTipY(r.top + r.height / 2) }}
          onMouseLeave={() => setTipY(null)}
          className={({ isActive }) =>
            `flex items-center justify-center h-9 w-9 rounded-xl transition-all mx-auto ${
              isActive
                ? 'bg-blue-500 text-white shadow-lg shadow-blue-500/30'
                : 'text-slate-500 dark:text-slate-500 hover:bg-slate-100 dark:hover:bg-white/10 hover:text-slate-800 dark:hover:text-white'
            }`
          }
        >
          <Icon size={17} />
        </NavLink>
        {tipY !== null && (
          <div className="fixed z-[200] flex items-center pointer-events-none" style={{ left: 72, top: tipY, transform: 'translateY(-50%)' }}>
            <div className="w-2 h-2 rotate-45 bg-slate-800 dark:bg-slate-700 -mr-1 flex-shrink-0" />
            <div className="bg-slate-800 dark:bg-slate-700 text-white text-xs font-medium px-2.5 py-1.5 rounded-lg shadow-xl whitespace-nowrap">{item.label}</div>
          </div>
        )}
      </div>
    )
  }
  return (
    <NavLink
      to={item.to}
      end={item.to === '/'}
      onClick={onNavigate}
      className={({ isActive }) =>
        `flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-semibold transition-all mx-2 ${
          isActive
            ? 'bg-blue-500 text-white shadow-md shadow-blue-500/25'
            : 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-white/8 hover:text-slate-900 dark:hover:text-white'
        }`
      }
    >
      {({ isActive }) => (
        <>
          <span className={`flex-shrink-0 flex items-center justify-center w-7 h-7 rounded-lg ${isActive ? 'bg-white/20' : 'text-slate-400 dark:text-slate-500'}`}>
            <Icon size={15} />
          </span>
          <span>{item.label}</span>
        </>
      )}
    </NavLink>
  )
}

function NavGroup({ item, collapsed, onNavigate }) {
  const location = useLocation()
  const isChildActive = item.children.some(c => location.pathname === c.to || location.pathname.startsWith(c.to + '/'))
  const [open, setOpen] = useState(isChildActive)
  const [flyY, setFlyY] = useState(null)
  const [flyOpen, setFlyOpen] = useState(false)
  const btnRef = useRef(null)
  const Icon = item.icon

  useEffect(() => { if (isChildActive) setOpen(true) }, [location.pathname])

  if (collapsed) {
    return (
      <div className="px-2">
        <button
          ref={btnRef}
          onMouseEnter={() => { const r = btnRef.current?.getBoundingClientRect(); if (r) setFlyY(r.top); setFlyOpen(true) }}
          onMouseLeave={() => setFlyOpen(false)}
          className={`flex items-center justify-center h-9 w-9 rounded-xl transition-all mx-auto ${
            isChildActive
              ? 'bg-blue-500/15 text-blue-500 dark:text-blue-400'
              : 'text-slate-500 dark:text-slate-500 hover:bg-slate-100 dark:hover:bg-white/10 hover:text-slate-800 dark:hover:text-white'
          }`}
        >
          <Icon size={17} />
        </button>
        {flyOpen && flyY !== null && (
          <div className="fixed z-[200] w-48" style={{ left: 72, top: flyY }} onMouseEnter={() => setFlyOpen(true)} onMouseLeave={() => setFlyOpen(false)}>
            <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-2xl py-1.5 overflow-hidden">
              <p className="px-3 py-1.5 text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-wider">{item.label}</p>
              {item.children.map(child => {
                const CIcon = child.icon
                return (
                  <NavLink key={child.to} to={child.to} end onClick={() => { setFlyOpen(false); onNavigate() }}
                    className={({ isActive }) =>
                      `flex items-center gap-2.5 px-3 py-2 text-sm transition-colors ${
                        isActive ? 'bg-blue-500 text-white' : 'text-slate-600 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-white/8 hover:text-slate-900 dark:hover:text-white'
                      }`
                    }
                  >
                    <CIcon size={14} />
                    <span>{child.label}</span>
                  </NavLink>
                )
              })}
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="mx-2">
      <button
        onClick={() => setOpen(o => !o)}
        className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-semibold transition-all ${
          isChildActive ? 'text-slate-900 dark:text-white' : 'text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-white/8 hover:text-slate-900 dark:hover:text-white'
        }`}
      >
        <span className={`flex-shrink-0 flex items-center justify-center w-7 h-7 rounded-lg ${isChildActive ? 'bg-blue-500/15 text-blue-500 dark:text-blue-400' : 'text-slate-400 dark:text-slate-500'}`}>
          <Icon size={15} />
        </span>
        <span className="flex-1 text-left">{item.label}</span>
        <span className={`text-slate-400 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}>
          <ChevronDown size={13} />
        </span>
      </button>

      {open && (
        <div className="mt-0.5 ml-5 border-l border-slate-200 dark:border-slate-700/60 pl-3 space-y-0.5">
          {item.children.map(child => {
            const CIcon = child.icon
            return (
              <NavLink key={child.to} to={child.to} end onClick={onNavigate}
                className={({ isActive }) =>
                  `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition-all ${
                    isActive
                      ? 'bg-blue-500 text-white shadow-md shadow-blue-500/20'
                      : 'text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-white/8 hover:text-slate-900 dark:hover:text-white'
                  }`
                }
              >
                <CIcon size={14} />
                <span>{child.label}</span>
              </NavLink>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default function Sidebar({ isOpen, setIsOpen, collapsed, setCollapsed }) {
  const closeOnMobile = () => { if (window.innerWidth < 768) setIsOpen(false) }

  return (
    <>
      <aside className={`
        fixed left-0 top-0 h-screen z-40 flex flex-col
        bg-white dark:bg-slate-900
        border-r border-slate-200 dark:border-slate-800
        shadow-sm dark:shadow-none
        transition-all duration-300 ease-in-out
        ${collapsed ? 'w-[68px]' : 'w-60'}
        ${isOpen ? 'translate-x-0' : '-translate-x-full'}
        md:translate-x-0
      `}>
        {/* Header */}
        <div className="flex items-center gap-3 px-3 py-3 border-b border-slate-200 dark:border-slate-800 min-h-[60px]">
          <button
            onClick={() => setCollapsed(c => !c)}
            className="w-8 h-8 rounded-lg overflow-hidden flex-shrink-0 ring-1 ring-slate-200 dark:ring-slate-700 hover:ring-blue-400 transition-all bg-white flex items-center justify-center p-0.5"
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <img src={logo} alt="EMS" className="w-full h-full object-contain mix-blend-multiply dark:mix-blend-normal" />
          </button>

          {!collapsed && (
            <>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-bold text-slate-900 dark:text-white leading-tight">Camera EMS Portal</p>
                <p className="text-[10px] text-slate-400 uppercase tracking-widest leading-tight">Monitoring Console</p>
              </div>
              <button
                onClick={() => setIsOpen(false)}
                className="md:hidden text-slate-400 hover:text-slate-700 dark:hover:text-white p-1 rounded-lg hover:bg-slate-100 dark:hover:bg-white/10"
              >
                <PanelLeftClose size={15} />
              </button>
            </>
          )}
        </div>

        {/* Navigation */}
        <nav className={`flex-1 py-3 space-y-0.5 ${collapsed ? 'overflow-visible' : 'overflow-y-auto overflow-x-hidden'}`}>
          {NAV.map(item =>
            item.children ? (
              <NavGroup key={item.label} item={item} collapsed={collapsed} onNavigate={closeOnMobile} />
            ) : (
              <NavLeaf key={item.label} item={item} collapsed={collapsed} onNavigate={closeOnMobile} />
            )
          )}
        </nav>

        {/* Footer */}
        {!collapsed && (
          <div className="border-t border-slate-200 dark:border-slate-800 px-4 py-3">
            <div className="flex items-center gap-2">
              <span className="relative flex h-2 w-2 flex-shrink-0">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
              </span>
              <span className="text-xs text-slate-400 dark:text-slate-500 flex-1">System Live</span>
              <span className="text-[10px] text-slate-300 dark:text-slate-600">v2.0</span>
            </div>
          </div>
        )}
      </aside>

      {/* Mobile overlay */}
      {isOpen && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-30 md:hidden" onClick={() => setIsOpen(false)} />
      )}
    </>
  )
}
