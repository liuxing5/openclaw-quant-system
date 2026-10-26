/**
 * 极简 API 客户端：所有后端请求统一走这里。
 * 后端契约与 /api/v1/* 对齐，返回 JSON。
 */
import { useCallback, useEffect, useRef, useState } from 'react'

export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }
}

async function request(path, { method = 'GET', body, params, signal } = {}) {
  let url = path.startsWith('/api') ? path : `/api/v1${path}`
  if (params) {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === '') continue
      if (Array.isArray(v)) v.forEach((item) => qs.append(k, item))
      else qs.append(k, v)
    }
    const s = qs.toString()
    if (s) url += (url.includes('?') ? '&' : '?') + s
  }

  const token = typeof localStorage !== 'undefined' ? localStorage.getItem('qd.auth.token') : null
  const headers = {}
  if (body) headers['Content-Type'] = 'application/json'
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(url, {
    method,
    signal,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })

  if (res.status === 401 && token) {
    window.dispatchEvent(new CustomEvent('qd:auth-expired'))
  }

  const text = await res.text()
  let payload = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }

  if (!res.ok) {
    const msg =
      (payload && (payload.detail || payload.message || payload.error)) ||
      `请求失败 (${res.status})`
    throw new ApiError(typeof msg === 'string' ? msg : JSON.stringify(msg), res.status, payload)
  }
  return payload
}

export const api = {
  get: (path, params, opts) => request(path, { ...opts, params }),
  post: (path, body, opts) => request(path, { ...opts, method: 'POST', body }),
  put: (path, body, opts) => request(path, { ...opts, method: 'PUT', body }),
  patch: (path, body, opts) => request(path, { ...opts, method: 'PATCH', body }),
  delete: (path, opts) => request(path, { ...opts, method: 'DELETE' }),
  // `del` 是 `delete` 的简写别名（部分页面写的是 api.del），两种都保留
  del: (path, opts) => request(path, { ...opts, method: 'DELETE' }),
}

/**
 * 页面级数据加载 hook：统一 loading / error / 空态处理。
 */
export function useApi(loader, deps = [], { enabled = true } = {}) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(enabled)
  const [error, setError] = useState(null)
  const seq = useRef(0)
  const loaderRef = useRef(loader)
  loaderRef.current = loader

  const run = useCallback(async () => {
    const id = ++seq.current
    setLoading(true)
    setError(null)
    try {
      const result = await loaderRef.current()
      if (id === seq.current) setData(result)
    } catch (e) {
      if (id === seq.current) setError(e)
    } finally {
      if (id === seq.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!enabled) {
      setLoading(false)
      return undefined
    }
    run()
    return undefined
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, ...deps])

  return { data, loading, error, reload: run, setData }
}
