/**
 * 本地会员账号上下文：token 存 localStorage，所有 API 自动带 Authorization。
 * 未登录 → 登录页；试用/会员到期 → 订阅墙；试用 3 天。
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api } from './api.js'

const TOKEN_KEY = 'qd.auth.token'
const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || '')
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  const clear = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY)
    setToken('')
    setUser(null)
  }, [])

  const refresh = useCallback(async () => {
    if (!localStorage.getItem(TOKEN_KEY)) {
      setUser(null)
      setLoading(false)
      return null
    }
    setLoading(true)
    try {
      const me = await api.get('/auth/me')
      setUser(me)
      return me
    } catch (e) {
      if (e.status === 401) clear()
      return null
    } finally {
      setLoading(false)
    }
  }, [clear])

  useEffect(() => {
    refresh()
    const onExpired = () => clear()
    window.addEventListener('qd:auth-expired', onExpired)
    return () => window.removeEventListener('qd:auth-expired', onExpired)
  }, [refresh, clear])

  const login = useCallback(async (email, password) => {
    const res = await api.post('/auth/login', { email, password })
    localStorage.setItem(TOKEN_KEY, res.token)
    setToken(res.token)
    setUser(res.user)
    return res.user
  }, [])

  const register = useCallback(async (email, password, nickname) => {
    const res = await api.post('/auth/register', { email, password, nickname })
    localStorage.setItem(TOKEN_KEY, res.token)
    setToken(res.token)
    setUser(res.user)
    return res.user
  }, [])

  const logout = useCallback(() => clear(), [clear])

  const membership = user?.membership || { status: 'active', is_expired: false }
  const value = useMemo(
    () => ({ token, user, loading, membership, login, register, logout, refresh }),
    [token, user, loading, membership, login, register, logout, refresh],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
