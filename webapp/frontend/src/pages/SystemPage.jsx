/**
 * /system 系统管理总览：四个子模块入口磁贴 + 最近审计日志 + 数据源健康摘要。
 *
 * 为什么这样拆：D3 系列接口（/system/*、/workbench/monitor）在部分环境尚未上线，
 * 四个读接口各自独立取数、各自走 AsyncBlock 三态，任何一个 404/500 都只在自己的
 * 区块里降级为错误态并可重试，页面整体绝不白屏。
 *
 * 响应里的 meta.local / meta.seed / meta.degraded / meta.source 必须显式打徽标，
 * 本地演示数据不能伪装成真实业务数据。
 */
import React from 'react'
import { AsyncBlock, Badge, Card, PageShell, SectionHeader, StatTile, TextLink } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDateTime } from '../lib/format.js'

const arr = (v) => (Array.isArray(v) ? v : [])

/** 按候选字段名取第一个非空值：后端字段微调时页面不至于整块塌陷。 */
function pick(obj, keys, fallback = null) {
  if (!obj) return fallback
  for (const k of keys) {
    const v = obj[k]
    if (v !== undefined && v !== null && v !== '') return v
  }
  return fallback
}

/** 数据来源徽标：本地 / 种子 / 降级 / 来源，必须肉眼可见，不能默认当真实业务数据。 */
function MetaBadges({ meta, localLabel = '本地演示数据' }) {
  if (!meta) return null
  const badges = []
  if (meta.local) badges.push(<Badge key="local" tone="blue">{localLabel}</Badge>)
  if (meta.seed) badges.push(<Badge key="seed" tone="amber">种子演示数据</Badge>)
  if (meta.degraded) badges.push(<Badge key="degraded" tone="rose">降级数据</Badge>)
  if (meta.source) badges.push(<Badge key="src" tone="slate">来源 {String(meta.source)}</Badge>)
  if (!badges.length) return null
  return <span className="inline-flex flex-wrap items-center gap-1.5">{badges}</span>
}

const STATUS_TONE = {
  ok: 'emerald',
  up: 'emerald',
  success: 'emerald',
  healthy: 'emerald',
  warn: 'amber',
  warning: 'amber',
  pending: 'amber',
  disabled: 'slate',
  error: 'rose',
  fail: 'rose',
  failed: 'rose',
  down: 'rose',
}

function statusTone(status) {
  return STATUS_TONE[String(status || '').toLowerCase()] || 'slate'
}

/* --------------------------------------------------------------- 子模块入口磁贴 */

function EntryTile({ title, desc, to, unit, state, meta }) {
  return (
    <Card className="flex h-full flex-col">
      <SectionHeader
        title={title}
        subtitle={desc}
        action={<TextLink to={to}>进入 →</TextLink>}
      />
      <AsyncBlock
        loading={state.loading}
        error={state.error}
        onRetry={state.retry}
        loadingLabel="取数中"
        empty={state.empty}
        emptyText="暂无数据"
      >
        <div className="mt-3">
          <p className="text-2xl font-semibold tabular-nums text-slate-900 dark:text-slate-100">
            {state.value}
          </p>
          <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">{unit}</p>
          <div className="mt-1.5">
            <MetaBadges meta={meta} localLabel="本地单用户" />
          </div>
        </div>
      </AsyncBlock>
    </Card>
  )
}

/* ------------------------------------------------------------------ 最近审计日志 */

function RecentLogs({ logs }) {
  const items = arr(logs.data?.items)
  return (
    <Card>
      <SectionHeader
        title="最近审计日志"
        subtitle="最新 5 条操作留痕，便于追查越权与误操作"
        badge={<MetaBadges meta={logs.data?.meta} />}
        action={<TextLink to="/system/audit-logs">全部日志 →</TextLink>}
      />
      <AsyncBlock
        loading={logs.loading}
        error={logs.error}
        onRetry={logs.reload}
        loadingLabel="审计日志加载中"
        empty={items.length === 0}
        emptyText="暂无审计日志"
        emptyHint="登录、改配置、回复反馈等动作会在这里留痕"
      >
        <div className="mt-3 overflow-x-auto thin-scroll">
          <table className="w-full min-w-[560px] text-xs">
            <thead className="sticky top-0 z-10 bg-white dark:bg-slate-900">
              <tr className="text-left text-[11px] text-slate-400">
                <th className="pb-1.5 font-medium">时间</th>
                <th className="pb-1.5 font-medium">操作者</th>
                <th className="pb-1.5 font-medium">动作</th>
                <th className="pb-1.5 font-medium">对象</th>
                <th className="pb-1.5 text-right font-medium">IP</th>
              </tr>
            </thead>
            <tbody>
              {items.slice(0, 5).map((r, i) => (
                <tr key={pick(r, ['id', 'log_id'], `log-${i}`)} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="py-1.5 tabular-nums text-slate-500 dark:text-slate-400">
                    {fmtDateTime(pick(r, ['created_at', 'occurred_at', 'at', 'time']))}
                  </td>
                  <td className="py-1.5 font-medium text-slate-700 dark:text-slate-200">
                    {pick(r, ['actor', 'operator', 'username', 'user', 'account'], '—')}
                  </td>
                  <td className="py-1.5 text-slate-600 dark:text-slate-300">
                    {pick(r, ['action', 'event', 'method', 'type'], '—')}
                  </td>
                  <td className="max-w-[16rem] truncate py-1.5 text-slate-500 dark:text-slate-400">
                    {pick(r, ['target', 'object', 'resource', 'detail', 'message'], '—')}
                  </td>
                  <td className="py-1.5 text-right tabular-nums text-slate-400">
                    {pick(r, ['ip', 'ip_address', 'client_ip'], '—')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </AsyncBlock>
    </Card>
  )
}

/* --------------------------------------------------------------- 数据源 / 运行健康 */

function HealthPanel({ monitor }) {
  const data = monitor.data || {}
  const db = data.db || data.db_pool || {}
  const sources = arr(data.feed_sources ?? data.sources ?? data.feeds)
  const jobs = arr(data.jobs)
  const okSources = sources.filter((s) => ['ok', 'up', 'healthy'].includes(String(s.status || '').toLowerCase()))
    .length
  const okJobs = jobs.filter((j) => ['ok', 'success', 'running'].includes(String(j.status || '').toLowerCase())).length
  const live = pick(db, ['live', 'active', 'used', 'in_use'], 0)
  const max = pick(db, ['max', 'pool_max', 'size', 'limit'], 0)

  return (
    <Card>
      <SectionHeader
        title="数据源与运行健康"
        subtitle="来自 /workbench/monitor：数据源、定时任务、数据库连接池"
        badge={
          sources.length ? (
            <Badge tone={okSources === sources.length ? 'emerald' : 'amber'}>
              数据源 {okSources}/{sources.length} 正常
            </Badge>
          ) : null
        }
        action={<TextLink to="/workbench/monitor">运行监控 →</TextLink>}
      />
      <AsyncBlock
        loading={monitor.loading}
        error={monitor.error}
        onRetry={monitor.reload}
        loadingLabel="监控数据加载中"
        empty={!sources.length && !jobs.length && !db.live && !db.max}
        emptyText="暂无监控数据"
        emptyHint="数据库连接池 / 数据源 / 定时任务尚无采样"
      >
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <StatTile
            label="DB 连接池"
            value={max ? `${live}/${max}` : live || '—'}
            sub={`空闲 ${pick(db, ['idle'], 0)} · 异常 ${pick(db, ['bad', 'broken'], 0)}`}
          />
          <StatTile label="缓存条目" value={pick(data.cache, ['entries', 'keys', 'size', 'count'], '—')} sub="内存缓存" />
          <StatTile label="定时任务" value={jobs.length ? `${okJobs}/${jobs.length} 正常` : '—'} sub="近一次运行" />
          <StatTile
            label="接口健康"
            value={(() => {
              const apis = arr(data.api ?? data.endpoints ?? data.apis)
              if (!apis.length) return '—'
              const ok = apis.filter((a) => !pick(a, ['error', 'failed'], false)).length
              return `${ok}/${apis.length}`
            })()}
            sub="本周期探测"
          />
        </div>

        <ul className="mt-3 space-y-1.5">
          {sources.slice(0, 6).map((s, i) => (
            <li
              key={pick(s, ['id', 'code', 'name'], `src-${i}`)}
              className="flex items-center justify-between gap-2 rounded-md border border-slate-100 bg-slate-50/60 px-2 py-1.5 text-[11px] dark:border-slate-800 dark:bg-slate-800/50"
            >
              <span className="min-w-0 truncate">
                <span
                  className={`mr-1.5 inline-block size-1.5 rounded-full ${
                    ['ok', 'up', 'healthy'].includes(String(s.status || '').toLowerCase())
                      ? 'bg-emerald-500'
                      : String(s.status || '').toLowerCase() === 'disabled'
                        ? 'bg-slate-400'
                        : 'bg-amber-500'
                  }`}
                />
                <span className="font-medium text-slate-700 dark:text-slate-200">
                  {pick(s, ['name', 'id', 'source'], '未命名数据源')}
                </span>
              </span>
              <span className="flex shrink-0 items-center gap-1.5">
                <Badge tone={statusTone(s.status)}>{pick(s, ['status'], '未知')}</Badge>
                <span className="tabular-nums text-slate-400">
                  {fmtDateTime(pick(s, ['last_success_at', 'last_sync_at', 'updated_at']))}
                </span>
              </span>
            </li>
          ))}
        </ul>
        <div className="mt-2">
          <MetaBadges meta={data.meta} />
        </div>
      </AsyncBlock>
    </Card>
  )
}

/* ------------------------------------------------------------------------- 页面 */

export default function SystemPage() {
  const users = useApi(() => api.get('/system/users', { page: 1, size: 5 }), [])
  const perms = useApi(() => api.get('/system/permissions'), [])
  const logs = useApi(() => api.get('/system/audit-logs', { page: 1, size: 5 }), [])
  const monitor = useApi(() => api.get('/workbench/monitor'), [])

  // 一个接口挂了不能拖累其它区块，所以四个请求各自重试
  const retryAll = () => {
    users.reload()
    perms.reload()
    logs.reload()
    monitor.reload()
  }

  const userTotal = users.error ? '—' : pick(users.data, ['total', 'count', 'user_total'], null)
  const permTotal = perms.error ? '—' : pick(perms.data, ['total', 'count', 'permission_total'], null)
  const roleTotal = perms.error ? '—' : arr(perms.data?.roles ?? perms.data?.items).length || '—'
  const logTotal = logs.error ? '—' : pick(logs.data, ['total', 'count'], null)

  return (
    <PageShell
      title="系统管理"
      subtitle="用户 / 权限 / 审计 / 运行监控的统一入口，数据实时取自后端 D3 接口"
      actions={
        <button
          type="button"
          onClick={retryAll}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
        >
          全部刷新
        </button>
      }
    >
      <div className="space-y-4">
        {/* 四个子模块入口磁贴 */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <EntryTile
            title="用户管理"
            desc="账号、角色、注册与登录状态"
            to="/system/users"
            unit="个账号"
            meta={users.data?.meta}
            state={{
              loading: users.loading,
              error: users.error,
              retry: users.reload,
              empty: !users.loading && !users.error && userTotal === null,
              value: userTotal ?? arr(users.data?.items).length,
            }}
          />
          <EntryTile
            title="权限管理"
            desc="角色 × 权限点矩阵"
            to="/system/permissions"
            unit={`${roleTotal} 个角色下的权限点`}
            meta={perms.data?.meta}
            state={{
              loading: perms.loading,
              error: perms.error,
              retry: perms.reload,
              empty: !perms.loading && !perms.error && permTotal === null,
              value: permTotal ?? arr(perms.data?.permissions ?? perms.data?.items).length,
            }}
          />
          <EntryTile
            title="审计日志"
            desc="谁在什么时候动了什么"
            to="/system/audit-logs"
            unit="条留痕记录"
            meta={logs.data?.meta}
            state={{
              loading: logs.loading,
              error: logs.error,
              retry: logs.reload,
              empty: !logs.loading && !logs.error && logTotal === null,
              value: logTotal ?? arr(logs.data?.items).length,
            }}
          />
          <EntryTile
            title="运行监控"
            desc="连接池 / 数据源 / 定时任务"
            to="/workbench/monitor"
            unit={
              monitor.error
                ? '监控接口不可用'
                : `${arr(monitor.data?.feed_sources ?? monitor.data?.sources).length} 个数据源在监控中`
            }
            meta={monitor.data?.meta}
            state={{
              loading: monitor.loading,
              error: monitor.error,
              retry: monitor.reload,
              empty: !monitor.loading && !monitor.error && !monitor.data,
              value: monitor.loading ? null : monitor.error ? '—' : monitor.data?.status || '正常',
            }}
          />
        </div>

        {/* 最近审计日志 + 健康摘要 */}
        <div className="grid gap-4 xl:grid-cols-3">
          <div className="xl:col-span-2">
            <RecentLogs logs={logs} />
          </div>
          <HealthPanel monitor={monitor} />
        </div>
      </div>
    </PageShell>
  )
}
