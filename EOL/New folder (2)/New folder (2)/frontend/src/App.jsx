import { Suspense, lazy, useState } from 'react'
import { Routes, Route, Navigate, Outlet } from 'react-router-dom'
import { ThemeProvider } from './context/ThemeContext'
import { AuthProvider, useAuth } from './context/AuthContext'
import { ToastProvider } from './context/ToastContext'
import Sidebar from './components/Sidebar'
import Navbar from './components/Navbar'
import OfflineCameraBar from './components/OfflineCameraBar'
import Login from './pages/Login'

// Routes redesigned (2026-05-13):
//   - Dropped: PLC Master, Camera Master, Camera Config, Reports — all
//     functionality merged into Machine Detail.
//   - /admin/* is the new namespace (renamed from /masters/*) to match
//     the "Camera Admin" sidebar group.  Legacy /masters/* paths still
//     redirect so old bookmarks don't 404.
const ZoneMaster    = lazy(() => import('./pages/masters/ZoneMaster'))
const LineMaster    = lazy(() => import('./pages/masters/LineMaster'))
const MachineMaster = lazy(() => import('./pages/masters/MachineMaster'))
const MachineDetail = lazy(() => import('./pages/masters/MachineDetail'))
const Dashboard     = lazy(() => import('./pages/Dashboard'))
const CameraGrid    = lazy(() => import('./pages/monitor/CameraGrid'))

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
        <OfflineCameraBar />
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

              {/* New unified Camera Admin namespace */}
              <Route path="/admin/zones"               element={<ZoneMaster />} />
              <Route path="/admin/lines"               element={<LineMaster />} />
              <Route path="/admin/machines"            element={<MachineMaster />} />
              <Route path="/admin/machines/:machineId" element={<MachineDetail />} />

              <Route path="/monitor/camera-grid" element={<CameraGrid />} />

              {/* Legacy redirects so existing bookmarks don't 404 */}
              <Route path="/masters/zones"     element={<Navigate to="/admin/zones"    replace />} />
              <Route path="/masters/lines"     element={<Navigate to="/admin/lines"    replace />} />
              <Route path="/masters/machines"  element={<Navigate to="/admin/machines" replace />} />
              <Route path="/masters/cameras"   element={<Navigate to="/admin/machines" replace />} />
              <Route path="/masters/plcs"      element={<Navigate to="/admin/machines" replace />} />
              <Route path="/config/camera-binding" element={<Navigate to="/admin/machines" replace />} />
              <Route path="/reports"           element={<Navigate to="/" replace />} />

              <Route path="*" element={<Navigate to="/" replace />} />
            </Route></Route>
          </Routes>
        </ToastProvider>
      </AuthProvider>
    </ThemeProvider>
  )
}
