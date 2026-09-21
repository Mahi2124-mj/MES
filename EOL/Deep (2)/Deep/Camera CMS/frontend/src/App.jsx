import { Suspense, lazy, useState } from 'react'
import { Routes, Route, Navigate, Outlet } from 'react-router-dom'
import { ThemeProvider } from './context/ThemeContext'
import { AuthProvider, useAuth } from './context/AuthContext'
import { ToastProvider } from './context/ToastContext'
import Sidebar from './components/Sidebar'
import Navbar from './components/Navbar'
import Login from './pages/Login'

const ZoneMaster = lazy(() => import('./pages/masters/ZoneMaster'))
const LineMaster = lazy(() => import('./pages/masters/LineMaster'))
const MachineMaster = lazy(() => import('./pages/masters/MachineMaster'))
const CameraMaster = lazy(() => import('./pages/masters/CameraMaster'))
const PlcMaster = lazy(() => import('./pages/masters/PlcMaster'))
const CameraConfig = lazy(() => import('./pages/config/CameraConfig'))
const ShiftConfig = lazy(() => import('./pages/config/ShiftConfig'))
const SystemSettings = lazy(() => import('./pages/config/SystemSettings'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const CameraGrid = lazy(() => import('./pages/monitor/CameraGrid'))
const CycleMonitor = lazy(() => import('./pages/monitor/CycleMonitor'))
const Report = lazy(() => import('./pages/reports/CycleTimeReport'))

function RequireAuth() {
  const { user, loading } = useAuth()
  if (loading) return null
  return user ? <Outlet /> : <Navigate to="/login" replace />
}

function AppShell() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  return (
    <div className="flex h-screen bg-slate-50 dark:bg-slate-950 text-slate-800 dark:text-slate-100 overflow-hidden font-sans">
      <Sidebar isOpen={sidebarOpen} setIsOpen={setSidebarOpen} collapsed={collapsed} setCollapsed={setCollapsed} />
      <div className={`flex flex-col flex-1 min-w-0 transition-all duration-300 ${collapsed ? 'md:ml-[68px]' : 'md:ml-60'}`}>
        <Navbar onMenuClick={() => setSidebarOpen(true)} />
        <main className="flex-1 overflow-auto p-4 md:p-6 pb-20 page-enter">
          <Suspense fallback={<div className="h-full flex items-center justify-center"><div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin"/></div>}>
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <ToastProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<RequireAuth />}><Route element={<AppShell />}>
              <Route path="/" element={<Dashboard />} />
              <Route path="/masters/zones" element={<ZoneMaster />} />
              <Route path="/masters/lines" element={<LineMaster />} />
              <Route path="/masters/machines" element={<MachineMaster />} />
              <Route path="/masters/cameras" element={<CameraMaster />} />
              <Route path="/masters/plcs" element={<PlcMaster />} />
              <Route path="/config/camera-binding" element={<CameraConfig />} />
              <Route path="/config/shifts" element={<ShiftConfig />} />
              <Route path="/config/system" element={<SystemSettings />} />
              <Route path="/monitor/camera-grid" element={<CameraGrid />} />
              <Route path="/monitor/cycles" element={<CycleMonitor />} />
              <Route path="/reports" element={<Report />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route></Route>
          </Routes>
        </ToastProvider>
      </AuthProvider>
    </ThemeProvider>
  )
}
