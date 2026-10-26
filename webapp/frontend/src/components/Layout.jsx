import React, { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { NAV, FOOTER_NOTE } from '../lib/nav.js'
import { ToastHost } from '../lib/toast.jsx'
import { api } from '../lib/api.js'

function useTheme() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
    try {
      localStorage.setItem('qd.theme', dark ? 'dark' : 'light')
    } catch {
      /* ignore */
    }
  }, [dark])
  return [dark, setDark]
}

function HealthDot() {
  const [state, setState] = useState('checking')
  useEffect(() => {
    let alive = true
    const check = () =>
      api
        .get('/health')
        .then((d) => alive && setState(d && d.status === 'ok' ? 'ok' : 'down'))
        .catch(() => alive && setState('down'))
    check()
    const id = setInterval(check, 60000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])
  const map = {
    ok: { dot: 'bg-emerald-500', text: '服务正常' },
    down: { dot: 'bg-rose-500', text: '服务不可达' },
    checking: { dot: 'bg-slate-400', text: '检测中' },
  }
  const s = map[state]
  return (
    <span className="hidden items-center gap-1.5 rounded-full border border-slate-200 bg-white/70 px-2.5 py-1 text-[11px] text-slate-500 lg:inline-flex dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-400">
      <span className={`size-1.5 rounded-full ${s.dot}`} />
      {s.text}
    </span>
  )
}

export default function Layout({ children }) {
  const [dark, setDark] = useTheme()
  const [collapsed, setCollapsed] = useState(false)
  const location = useLocation()

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' in window ? 'instant' : 'auto' })
  }, [location.pathname])

  return (
    <div className="relative isolate min-h-dvh overflow-x-hidden bg-slate-50 text-slate-800 dark:bg-transparent dark:text-slate-100">
      {/* 背景光斑 */}
      <div aria-hidden="true" className="pointer-events-none fixed inset-0 z-0 overflow-hidden">
        <div className="liquid-orb -left-40 -top-48 size-[38rem] bg-blue-300/35 dark:bg-blue-600/20" />
        <div className="liquid-orb -right-44 top-[12%] size-[34rem] bg-violet-300/30 dark:bg-violet-600/18" />
        <div className="liquid-orb bottom-[-18rem] left-[34%] size-[34rem] bg-cyan-200/25 dark:bg-cyan-500/10" />
      </div>

      {/* 侧边栏 */}
      <aside
        className={`liquid-glass-surface fixed inset-y-0 left-0 z-30 hidden border-r border-slate-200/70 bg-white/70 shadow-[1px_0_16px_rgba(15,23,42,0.055)] transition-[width] duration-300 md:flex md:flex-col dark:border-slate-800/70 dark:bg-slate-900/60 dark:shadow-[1px_0_18px_rgba(0,0,0,0.32)] ${
          collapsed ? 'w-16' : 'w-60'
        }`}
      >
        <div className="flex h-14 shrink-0 items-center gap-2 border-b border-slate-200/60 px-4 dark:border-slate-800/60">
          <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-brand-600 text-sm font-bold text-white">
            彪
          </span>
          {!collapsed ? (
            <span className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
              彪哥财经之英雄本色
            </span>
          ) : null}
        </div>

        <nav className="thin-scroll flex-1 space-y-4 overflow-y-auto px-3 py-4">
          {NAV.map((group) => (
            <div key={group.group}>
              {!collapsed ? (
                <p className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
                  {group.group}
                </p>
              ) : (
                <div className="mx-2 mb-2 border-t border-slate-200 dark:border-slate-800" />
              )}
              <ul className="space-y-0.5">
                {group.items.map((item) => (
                  <li key={item.to}>
                    <NavLink
                      to={item.to}
                      title={item.label}
                      className={({ isActive }) =>
                        `flex items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] transition-colors ${
                          isActive
                            ? 'bg-brand-600 text-white shadow-sm'
                            : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800/70'
                        }`
                      }
                    >
                      <span
                        aria-hidden="true"
                        className="size-1.5 shrink-0 rounded-full bg-current opacity-50"
                      />
                      {!collapsed ? <span className="truncate">{item.label}</span> : null}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <div className="border-t border-slate-200/60 p-3 dark:border-slate-800/60">
          <button
            type="button"
            onClick={() => setCollapsed((v) => !v)}
            className="w-full rounded-lg border border-slate-200 bg-white/60 px-2 py-1.5 text-xs text-slate-500 hover:text-slate-800 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-400 dark:hover:text-slate-200"
          >
            {collapsed ? '展开' : '折叠侧边栏'}
          </button>
        </div>
      </aside>

      {/* 主区域 */}
      <div className={`relative z-10 transition-[padding] duration-300 ${collapsed ? 'md:pl-16' : 'md:pl-60'}`}>
        <header className="liquid-glass-surface sticky top-0 z-20 flex h-14 items-center justify-between gap-3 border-b border-slate-200/70 bg-white/70 px-4 dark:border-slate-800/70 dark:bg-slate-900/60">
          <div className="flex min-w-0 items-center gap-3">
            <button
              type="button"
              onClick={() => setCollapsed((v) => !v)}
              className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 md:hidden dark:text-slate-400 dark:hover:bg-slate-800"
              aria-label="切换侧边栏"
            >
              ☰
            </button>
            <span className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
              彪哥财经 · 数据一体化
            </span>
          </div>
          <div className="flex items-center gap-2">
            <HealthDot />
            <button
              type="button"
              onClick={() => setDark((v) => !v)}
              title={dark ? '切换到浅色主题' : '切换到深色主题'}
              aria-label={dark ? '切换到浅色主题' : '切换到深色主题'}
              className="rounded-md p-1.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-200"
            >
              {dark ? '☀' : '☾'}
            </button>
          </div>
        </header>

        <main className="mx-auto w-full max-w-[1600px] px-4 py-5">{children}</main>

        <footer className="border-t border-slate-200/70 px-4 py-5 text-center text-[11px] text-slate-400 dark:border-slate-800/70 dark:text-slate-500">
          {FOOTER_NOTE}
        </footer>
      </div>

      <ToastHost />
    </div>
  )
}
