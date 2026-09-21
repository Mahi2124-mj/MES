import { useState } from 'react'
import { Lock } from 'lucide-react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function Login() {
  const { login, user } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [u, setU] = useState('')
  const [p, setP] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  // Where to send the user post-login: either the page they tried to
  // hit before being bounced here (passed via location.state.from), or
  // the dashboard.
  const redirectTo = location.state?.from?.pathname || '/'

  // If user is ALREADY logged in (e.g. revisits /login with a valid
  // session), bounce them straight to dashboard instead of showing
  // the form.  Without this the page just sits there showing "Sign In".
  if (user) return <Navigate to={redirectTo} replace />

  const submit = async (event) => {
    event.preventDefault()
    setErr('')
    setBusy(true)
    try {
      await login(u, p)
      // Bug fix: AuthContext.login() sets user state but doesn't
      // navigate.  /login is OUTSIDE the RequireAuth wrapper in
      // App.jsx, so even after user becomes truthy, React Router
      // keeps us on /login.  Force the redirect here.
      navigate(redirectTo, { replace: true })
    } catch (error) {
      setErr(error.response?.data?.message || error.message || 'Login failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 dark:bg-slate-950 toyota-grid p-4">
      <div className="w-full max-w-md">
        <div className="card p-8 shadow-xl">
          <div className="flex items-center gap-3 mb-6">
            <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-red-500 to-red-700 flex items-center justify-center shadow-lg shadow-red-500/25">
              <Lock size={20} className="text-white" />
            </div>
            <div>
              <h1 className="text-lg font-bold text-slate-800 dark:text-slate-100">Camera EMS Portal</h1>
              <p className="text-xs text-slate-400">Camera Monitoring System</p>
            </div>
          </div>

          {err && (
            <div className="mb-4 px-3 py-2 rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-red-700 dark:text-red-300 text-sm">
              {err}
            </div>
          )}

          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-slate-600 dark:text-slate-300 mb-1">Username</label>
              <input
                value={u}
                onChange={(event) => setU(event.target.value)}
                className="input-field"
                placeholder="admin"
                autoFocus
                disabled={busy}
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-600 dark:text-slate-300 mb-1">Password</label>
              <input
                type="password"
                value={p}
                onChange={(event) => setP(event.target.value)}
                className="input-field"
                placeholder="******"
                disabled={busy}
              />
            </div>

            <button type="submit" className="btn-primary w-full text-sm" disabled={busy}>
              {busy ? 'Signing in...' : 'Sign In'}
            </button>
          </form>

          <p className="text-[10px] text-slate-400 mt-4 text-center">
            Toyota Boshoku - Industrial Camera Control System
          </p>
        </div>
      </div>
    </div>
  )
}
