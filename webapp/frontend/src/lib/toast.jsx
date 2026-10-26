/** 轻量全局提示：模块级订阅，无需额外依赖。 */
import { useEffect, useState } from 'react'

let seq = 0
let toasts = []
const listeners = new Set()

function emit() {
  listeners.forEach((fn) => fn([...toasts]))
}

export const toast = {
  push({ tone = 'info', title, message = null, timeout = 3200 }) {
    const id = ++seq
    toasts = [...toasts, { id, tone, title, message }]
    emit()
    if (timeout) {
      setTimeout(() => {
        toasts = toasts.filter((t) => t.id !== id)
        emit()
      }, timeout)
    }
    return id
  },
  success: (title, message) => toast.push({ tone: 'success', title, message }),
  error: (title, message) => toast.push({ tone: 'error', title, message, timeout: 5000 }),
  info: (title, message) => toast.push({ tone: 'info', title, message }),
}

export function ToastHost() {
  const [items, setItems] = useState([])
  useEffect(() => {
    listeners.add(setItems)
    return () => listeners.delete(setItems)
  }, [])

  const tones = {
    success: 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-950/80 dark:text-emerald-200',
    error: 'border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-500/40 dark:bg-rose-950/80 dark:text-rose-200',
    info: 'border-brand-300 bg-brand-50 text-brand-800 dark:border-brand-500/40 dark:bg-brand-950/80 dark:text-brand-200',
  }

  if (!items.length) return null
  return (
    <div className="pointer-events-none fixed right-4 top-4 z-50 flex w-72 flex-col gap-2">
      {items.map((t) => (
        <div
          key={t.id}
          className={`pointer-events-auto rounded-lg border px-3 py-2 text-xs shadow-lg backdrop-blur ${tones[t.tone] || tones.info}`}
        >
          <p className="font-semibold">{t.title}</p>
          {t.message ? <p className="mt-0.5 opacity-80">{t.message}</p> : null}
        </div>
      ))}
    </div>
  )
}
