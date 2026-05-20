import { createContext, useContext, useState, useEffect, useMemo } from 'react'
import axios from 'axios'

const AuthContext = createContext()

const TOKEN_KEY = 'tb-ems-token'
const USER_KEY = 'tb-ems-user'

const api = axios.create({ baseURL: '/api' })

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? JSON.parse(raw) : null
  })
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY)
    if (token && !user) {
      setLoading(true)
      api.get('/auth/me', { headers: { Authorization: `Bearer ${token}` } })
        .then(r => {
          const u = r.data?.data || r.data
          setUser(u)
          localStorage.setItem(USER_KEY, JSON.stringify(u))
        })
        .catch(() => {
          localStorage.removeItem(TOKEN_KEY)
          localStorage.removeItem(USER_KEY)
          setUser(null)
        })
        .finally(() => setLoading(false))
    }
  }, [])

  const login = async (username, password) => {
    const res = await api.post('/auth/login', { username, password })
    const data = res.data?.data || res.data
    const { token, ...userInfo } = data
    localStorage.setItem(TOKEN_KEY, token)
    localStorage.setItem(USER_KEY, JSON.stringify(userInfo))
    setUser(userInfo)
    return userInfo
  }

  const logout = () => {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
    setUser(null)
  }

  const value = useMemo(() => ({ user, loading, login, logout }), [user, loading])

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)
