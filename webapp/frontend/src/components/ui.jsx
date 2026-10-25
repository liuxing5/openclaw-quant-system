/** 通用 UI 原语：卡片、区块标题、状态、表格骨架、分页等。 */
import React from 'react'
import { Link } from 'react-router-dom'

export function Card({ className = '', children, ...rest }) {
  return (
    <section
      className={`rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900 ${className}`}
      {...rest}
    >
      {children}
    </section>
  )
}

export function SectionHeader({ icon = null, title, subtitle, action = null, badge = null }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="flex items-start gap-3">
        {icon ? (
          <div className="rounded-lg bg-brand-50 p-2 text-brand-600 dark:bg-brand-500/10 dark:text-brand-300">
            {icon}
          </div>
        ) : null}
        <div>
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">{title}</h2>
          {subtitle ? (
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{subtitle}</p>
          ) : null}
        </div>
      </div>
      <div className="flex items-center gap-2">
        {badge}
        {action}
      </div>
    </div>
  )
}

export function Spinner({ label = '加载中' }) {
  return (
    <div className="flex min-h-32 items-center justify-center gap-2 text-sm text-slate-500 dark:text-slate-400">
      <svg className="size-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
        <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      </svg>
      {label}
    </div>
  )
}

export function EmptyState({ text = '暂无数据', hint = null }) {
  return (
    <div className="flex min-h-28 flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-slate-200 bg-slate-50/60 px-4 py-6 text-center dark:border-slate-700 dark:bg-slate-800/40">
      <p className="text-sm text-slate-500 dark:text-slate-400">{text}</p>
      {hint ? <p className="text-xs text-slate-400 dark:text-slate-500">{hint}</p> : null}
    </div>
  )
}

export function ErrorState({ error, onRetry = null, text = null }) {
  const msg = text || (error && error.message) || '请求失败，请稍后重试'
  return (
    <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span>{msg}</span>
        {onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="rounded-md border border-rose-300 px-2 py-1 text-[11px] font-medium hover:bg-rose-100 dark:border-rose-500/40 dark:hover:bg-rose-500/20"
          >
            重试
          </button>
        ) : null}
      </div>
    </div>
  )
}

/** 数据块统一三态：loading / error / empty */
export function AsyncBlock({ loading, error, empty, onRetry, loadingLabel, emptyText, emptyHint, children }) {
  if (loading) return <Spinner label={loadingLabel || '数据加载中'} />
  if (error) return <ErrorState error={error} onRetry={onRetry} />
  if (empty) return <EmptyState text={emptyText || '暂无数据'} hint={emptyHint} />
  return children
}

export function Badge({ tone = 'slate', children, className = '' }) {
  const tones = {
    slate: 'border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300',
    blue: 'border-brand-200 bg-brand-50 text-brand-700 dark:border-brand-500/30 dark:bg-brand-500/10 dark:text-brand-300',
    emerald: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300',
    rose: 'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300',
    amber: 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300',
  }
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${tones[tone] || tones.slate} ${className}`}>
      {children}
    </span>
  )
}

export function Button({ variant = 'primary', size = 'md', loading = false, className = '', children, ...rest }) {
  const variants = {
    primary:
      'bg-brand-600 text-white hover:bg-brand-700 disabled:bg-brand-400',
    secondary:
      'border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700',
    ghost:
      'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800',
    danger:
      'bg-rose-600 text-white hover:bg-rose-700 disabled:bg-rose-400',
  }
  const sizes = {
    sm: 'px-2.5 py-1 text-xs',
    md: 'px-3.5 py-2 text-sm',
  }
  return (
    <button
      type="button"
      disabled={loading || rest.disabled}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-70 ${variants[variant]} ${sizes[size]} ${className}`}
      {...rest}
    >
      {loading ? (
        <svg className="size-3.5 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.3" strokeWidth="3" />
          <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
        </svg>
      ) : null}
      {children}
    </button>
  )
}

export function Tabs({ tabs, value, onChange, className = '' }) {
  return (
    <div className={`flex flex-wrap gap-1 rounded-lg bg-slate-100 p-1 dark:bg-slate-800/70 ${className}`}>
      {tabs.map((t) => {
        const key = t.value ?? t
        const label = t.label ?? t
        const active = key === value
        return (
          <button
            key={key}
            type="button"
            onClick={() => onChange(key)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              active
                ? 'bg-white text-slate-900 shadow-sm dark:bg-slate-700 dark:text-white'
                : 'text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200'
            }`}
          >
            {label}
          </button>
        )
      })}
    </div>
  )
}

export function Select({ value, onChange, options = [], className = '', ...rest }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-700 outline-none focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 ${className}`}
      {...rest}
    >
      {options.map((o) => (
        <option key={o.value ?? o} value={o.value ?? o}>
          {o.label ?? o}
        </option>
      ))}
    </select>
  )
}

export function Pagination({ page, pageSize, total, onChange }) {
  const pages = Math.max(1, Math.ceil((total || 0) / (pageSize || 20)))
  if (pages <= 1) return null
  return (
    <div className="flex items-center justify-between gap-3 pt-3 text-xs text-slate-500 dark:text-slate-400">
      <span>
        第 {page} / {pages} 页 · 共 {total} 条
      </span>
      <div className="flex gap-2">
        <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>
          上一页
        </Button>
        <Button variant="secondary" size="sm" disabled={page >= pages} onClick={() => onChange(page + 1)}>
          下一页
        </Button>
      </div>
    </div>
  )
}

export function StatTile({ label, value, sub = null, tone = null, className = '' }) {
  return (
    <div className={`rounded-lg border border-slate-200 bg-slate-50/60 p-3 dark:border-slate-700 dark:bg-slate-800/50 ${className}`}>
      <p className="text-[11px] text-slate-500 dark:text-slate-400">{label}</p>
      <p className={`mt-1 text-lg font-semibold tabular-nums ${tone || 'text-slate-900 dark:text-slate-100'}`}>{value}</p>
      {sub ? <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">{sub}</p> : null}
    </div>
  )
}

export function PageShell({ title, subtitle, actions = null, children }) {
  return (
    <div className="fade-in-up space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-900 dark:text-slate-100">{title}</h1>
          {subtitle ? <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{subtitle}</p> : null}
        </div>
        {actions}
      </header>
      {children}
    </div>
  )
}

export function TextLink({ to, children }) {
  return (
    <Link
      to={to}
      className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400 dark:hover:text-brand-300"
    >
      {children}
    </Link>
  )
}
