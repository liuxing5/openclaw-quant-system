/**
 * /workbench/monitor 运行监控：数据库连接池 · 缓存 · 数据源健康 · 定时任务 · 系统信息 · 接口健康。
 *
 * 全部数据来自 GET /workbench/monitor；字段做了多形状兼容（db/db_pool、feed_sources/sources、
 * jobs/crons、sys/system），接口 404/500 时整页给错误态 + 重试，绝不白屏。
 * 有时序（series/latency_series）就补一张趋势图，没有就只呈现 StatTile 与表格。
 */
import React, { useMemo } from 'react'
import { AsyncBlock, Badge, Card, PageShell, SectionHeader, StatTile } from '../components/ui.jsx'
import Chart, { AXIS_STYLE, TIP } from '../components/Chart.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDateTime, fmtNum } from '../lib/format.js'

const arr = (v) => (Array.isArray(v) ? v : [])

function pick(obj, keys, fallback = null) {
  if (!obj) return fallback
  for (const k of keys) {
    const v = obj[k]
    if (v !== undefined && v !== null && v !== '') return v
  }
  return fallback
}

const STATUS_TONE = {
  ok: 'emerald',
  up: 'emerald',
  success: 'emerald',
  running: 'blue',
  healthy: 'emerald',
  warn: 'amber',
  warning: 'amber',
  pending: 'amber',
  idle: 'slate',
  disabled: 'slate',
  stopped: 'slate',
  error: 'rose',
  failed: 'rose',
  fail: 'rose',
  down: 'rose',
}

const statusTone = (s) => STATUS_TONE[String(s || '').toLowerCase()] || 'slate'
const isOk = (s) => ['ok', 'up', 'success', 'healthy', 'running'].includes(String(s || '').toLowerCase())

function MetaBadges({ meta }) {
  if (!meta) return null
  const badges = []
  if (meta.local) badges.push(<Badge key="local" tone="blue">本地演示数据</Badge>)
  if (meta.seed) badges.push(<Badge key="seed" tone="amber">种子演示数据</Badge>)
  if (meta.degraded) badges.push(<Badge key="degraded" tone="rose">降级数据</Badge>)
  if (meta.source) badges.push(<Badge key="src" tone="slate">来源 {String(meta.source)}</Badge>)
  if (!badges.length) return null
  return <span className="inline-flex flex-wrap items-center gap-1.5">{badges}</span>
}

/** 连接池占用进度条（live / max），超 80% 转红提示。 */
function PoolBar({ live, max }) {
  const pct = max > 0 ? Math.min(100, (Number(live) / Number(max)) * 100) : 0
  const hot = pct >= 80
  return (
    <div className="mt-3">
      <div className="mb-1 flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400">
        <span>连接池占用</span>
        <span className="tabular-nums">{max ? `${fmtNum(pct, 1)}%` : '未配置上限'}</span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
        <div
          className={`h-full rounded-full transition-all ${hot ? 'bg-rose-500' : 'bg-brand-500'}`}
          style={{ width: `${max ? pct : 0}%` }}
        />
      </div>
    </div>
  )
}

/** 接口耗时柱状图：仅当返回了 latency / avg_ms 这类数值才画。 */
function ApiChart({ apis }) {
  const rows = apis
    .map((a) => ({
      name: String(pick(a, ['path', 'name', 'api', 'endpoint'], '—')),
      ms: Number(pick(a, ['p95_ms', 'avg_ms', 'elapsed_ms', 'latency_ms', 'ms'], NaN)),
    }))
    .filter((r) => Number.isFinite(r.ms))

  const option = useMemo(
    () => ({
      grid: { left: 46, right: 14, top: 18, bottom: 46 },
      tooltip: { ...TIP, formatter: (ps) => `${ps[0].axisValue}<br/>耗时 <b>${ps[0].value} ms</b>` },
      xAxis: {
        type: 'category',
        data: rows.map((r) => r.name),
        axisLabel: { color: '#94a3b8', fontSize: 10, rotate: 34, interval: 0 },
        ...AXIS_STYLE,
      },
      yAxis: { type: 'value', axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v) => `${v}ms` }, splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } } },
      series: [
        {
          type: 'bar',
          data: rows.map((r) => r.ms),
          itemStyle: { color: 'rgba(99,102,241,0.7)', borderRadius: [3, 3, 0, 0] },
          barMaxWidth: 26,
        },
      ],
    }),
    [rows],
  )

  if (!rows.length) return null
  return <Chart option={option} height={220} className="mt-3" />
}

/** 时序趋势：series / latency_series / history 任一存在才渲染。 */
function SeriesChart({ series }) {
  const rows = arr(series)
    .map((s) => ({
      label: String(pick(s, ['label', 'ts', 'time', 'at', 'trade_date', 'date'], '')),
      value: Number(pick(s, ['value', 'latency', 'ms', 'avg_ms', 'count', 'p95'], NaN)),
    }))
    .filter((r) => r.label && Number.isFinite(r.value))

  const option = useMemo(
    () => ({
      grid: { left: 46, right: 14, top: 18, bottom: 28 },
      tooltip: { ...TIP, trigger: 'axis' },
      xAxis: { type: 'category', data: rows.map((r) => r.label), ...AXIS_STYLE },
      yAxis: { type: 'value', axisLabel: { color: '#94a3b8', fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } } },
      series: [
        {
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: rows.map((r) => r.value),
          lineStyle: { color: '#6366f1', width: 2 },
          areaStyle: { color: 'rgba(99,102,241,0.16)' },
        },
      ],
    }),
    [rows],
  )

  if (rows.length < 2) return null
  return <Chart option={option} height={220} className="mt-3" />
}

/* ------------------------------------------------------------------------- 页面 */

export default function WorkbenchMonitorPage() {
  const { data, loading, error, reload } = useApi(() => api.get('/workbench/monitor'), [])

  const m = data || {}
  const db = m.db || m.db_pool || m.pool || {}
  const cache = m.cache || m.caches || {}
  const sources = arr(m.feed_sources ?? m.sources ?? m.feeds)
  const jobs = arr(m.jobs ?? m.crons ?? m.schedulers)
  const sys = m.sys || m.system || {}
  const apis = arr(m.api ?? m.endpoints ?? m.apis ?? m.api_health)
  const series = m.series ?? m.latency_series ?? m.history ?? null

  const live = Number(pick(db, ['live', 'active', 'used', 'in_use'], 0))
  const idle = Number(pick(db, ['idle', 'free'], 0))
  const max = Number(pick(db, ['max', 'pool_max', 'size', 'limit'], 0))
  const bad = Number(pick(db, ['bad', 'broken', 'closed', 'errors'], 0))
  const poolHealthy = bad === 0 && (!max || live / max < 0.8)

  // 命中率：后端可能给 0.98 也可能给 98%，>1 视作百分数原样显示
  const hitRate = pick(cache, ['hit_rate', 'hit_ratio'], null)
  const hitText =
    hitRate === null
      ? pick(cache, ['hits', 'hit_count'], '—')
      : `${fmtNum(Number(hitRate) > 1 ? Number(hitRate) : Number(hitRate) * 100, 1)}%`

  return (
    <PageShell
      title="运行监控"
      subtitle="数据库连接池 · 缓存 · 数据源健康 · 定时任务 · 系统信息 · 接口健康"
      actions={
        <div className="flex items-center gap-2">
          <MetaBadges meta={m.meta} />
          <Badge tone={poolHealthy && !bad ? 'emerald' : 'amber'}>
            {m.status ? `整体 ${m.status}` : poolHealthy ? '运行正常' : '需要关注'}
          </Badge>
          <button
            type="button"
            onClick={reload}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
          >
            刷新
          </button>
        </div>
      }
    >
      <AsyncBlock
        loading={loading}
        error={error}
        onRetry={reload}
        loadingLabel="运行监控加载中"
        empty={!loading && !error && !data}
        emptyText="暂无监控数据"
        emptyHint="数据库连接池 / 数据源 / 定时任务尚无采样"
      >
        <div className="space-y-4">
          {/* 数据库连接池 */}
          <Card>
            <SectionHeader
              title="数据库连接池"
              subtitle="PostgreSQL 连接占用与坏连接数"
              badge={
                <Badge tone={poolHealthy ? 'emerald' : 'rose'}>
                  {poolHealthy ? '健康' : '告警'}
                </Badge>
              }
            />
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <StatTile label="活跃连接 live" value={max ? `${live}/${max}` : live} sub="当前被占用" tone={poolHealthy ? null : 'text-rose-600 dark:text-rose-400'} />
              <StatTile label="空闲连接 idle" value={idle} sub="可立即复用" />
              <StatTile label="池上限 max" value={max || '未配置'} sub="pool_size" />
              <StatTile label="异常连接 bad" value={bad} sub="坏连接 / 已失效" tone={bad ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400'} />
            </div>
            <PoolBar live={live} max={max} />
          </Card>

          {/* 缓存 + 系统信息 */}
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <SectionHeader
                title="缓存"
                subtitle="内存缓存条目与命中情况"
                badge={<Badge tone="slate">{pick(cache, ['entries', 'keys', 'size', 'count'], 0)} 条</Badge>}
              />
              <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
                <StatTile label="缓存条目" value={pick(cache, ['entries', 'keys', 'size', 'count'], 0)} sub="当前驻留" />
                <StatTile
                  label="命中率"
                  value={hitText}
                  sub="read hit"
                />
                <StatTile label="淘汰条目" value={pick(cache, ['evicted', 'evictions', 'expired'], '—')} sub="LRU / TTL" />
              </div>
            </Card>

            <Card>
              <SectionHeader title="系统信息" subtitle="进程与运行环境" />
              <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                {[
                  ['Python', pick(sys, ['python', 'python_version', 'version'], '—')],
                  ['平台', pick(sys, ['platform', 'os', 'hostname'], '—')],
                  ['当前时间', fmtDateTime(pick(sys, ['now', 'server_time', 'time', 'current_time'], m.now))],
                  ['交易日', String(pick(sys, ['trade_date', 'trading_day', 'is_trading_day'], pick(m, ['trade_date'], '—')))],
                  ['运行时长', pick(sys, ['uptime', 'uptime_sec'], '—')],
                  ['版本', pick(sys, ['app_version', 'version', 'release'], '—')],
                ].map(([k, v]) => (
                  <div key={k} className="flex items-baseline justify-between gap-2 border-b border-dashed border-slate-100 pb-1.5 dark:border-slate-800">
                    <dt className="text-slate-400">{k}</dt>
                    <dd className="truncate font-medium tabular-nums text-slate-700 dark:text-slate-200">{String(v ?? '—')}</dd>
                  </div>
                ))}
              </dl>
            </Card>
          </div>

          {/* 数据源健康 */}
          <Card>
            <SectionHeader
              title="数据源健康"
              subtitle="feed_sources 采集状态与最近一次成功时间"
              badge={
                sources.length ? (
                  <Badge tone={sources.every((s) => isOk(s.status)) ? 'emerald' : 'amber'}>
                    {sources.filter((s) => isOk(s.status)).length}/{sources.length} 正常
                  </Badge>
                ) : null
              }
            />
            <AsyncBlock
              loading={false}
              error={null}
              empty={sources.length === 0}
              emptyText="暂无数据源"
              emptyHint="后端 feed_sources 表为空或接口未返回该字段"
            >
              <div className="mt-3 overflow-x-auto thin-scroll">
                <table className="w-full min-w-[720px] text-xs">
                  <thead className="sticky top-0 z-10 bg-white dark:bg-slate-900">
                    <tr className="text-left text-[11px] text-slate-400">
                      <th className="pb-1.5 font-medium">数据源</th>
                      <th className="pb-1.5 font-medium">分类</th>
                      <th className="pb-1.5 font-medium">状态</th>
                      <th className="pb-1.5 font-medium">最近成功</th>
                      <th className="pb-1.5 text-right font-medium">连续失败</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sources.map((s, i) => (
                      <tr key={pick(s, ['id', 'code', 'name'], `src-${i}`)} className="border-t border-slate-100 dark:border-slate-800">
                        <td className="py-2 font-medium text-slate-700 dark:text-slate-200">
                          {pick(s, ['name', 'id', 'source'], '—')}
                          {pick(s, ['last_error_msg'], null) ? (
                            <span className="ml-2 max-w-[16rem] truncate align-middle text-[10px] text-rose-500" title={s.last_error_msg}>
                              {s.last_error_msg}
                            </span>
                          ) : null}
                        </td>
                        <td className="py-2 text-slate-500 dark:text-slate-400">{pick(s, ['category', 'kind', 'type'], '—')}</td>
                        <td className="py-2"><Badge tone={statusTone(s.status)}>{pick(s, ['status'], '未知')}</Badge></td>
                        <td className="py-2 tabular-nums text-slate-500 dark:text-slate-400">
                          {fmtDateTime(pick(s, ['last_success_at', 'last_sync_at', 'updated_at']))}
                        </td>
                        <td className="py-2 text-right tabular-nums text-slate-500 dark:text-slate-400">
                          {pick(s, ['consecutive_failures', 'failures', 'fail_count'], 0)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </AsyncBlock>
          </Card>

          {/* 定时任务 */}
          <Card>
            <SectionHeader
              title="定时任务"
              subtitle="调度任务的上次运行时间、状态与耗时"
              badge={
                jobs.length ? (
                  <Badge tone={jobs.every((j) => isOk(j.status)) ? 'emerald' : 'amber'}>
                    {jobs.filter((j) => isOk(j.status)).length}/{jobs.length} 正常
                  </Badge>
                ) : null
              }
            />
            <AsyncBlock
              loading={false}
              error={null}
              empty={jobs.length === 0}
              emptyText="暂无定时任务"
              emptyHint="全局交易时段设置里启用的任务会在这里出现"
            >
              <div className="mt-3 overflow-x-auto thin-scroll">
                <table className="w-full min-w-[640px] text-xs">
                  <thead className="sticky top-0 z-10 bg-white dark:bg-slate-900">
                    <tr className="text-left text-[11px] text-slate-400">
                      <th className="pb-1.5 font-medium">任务</th>
                      <th className="pb-1.5 font-medium">上次运行</th>
                      <th className="pb-1.5 font-medium">状态</th>
                      <th className="pb-1.5 text-right font-medium">耗时</th>
                    </tr>
                  </thead>
                  <tbody>
                    {jobs.map((j, i) => (
                      <tr key={pick(j, ['id', 'code', 'name'], `job-${i}`)} className="border-t border-slate-100 dark:border-slate-800">
                        <td className="py-2">
                          <span className="font-medium text-slate-700 dark:text-slate-200">{pick(j, ['name', 'id', 'job'], '—')}</span>
                          {pick(j, ['cron', 'schedule'], null) ? (
                            <span className="ml-2 font-mono text-[10px] text-slate-400">{j.cron || j.schedule}</span>
                          ) : null}
                        </td>
                        <td className="py-2 tabular-nums text-slate-500 dark:text-slate-400">
                          {fmtDateTime(pick(j, ['last_run_at', 'last_success_at', 'last_run', 'updated_at']))}
                        </td>
                        <td className="py-2">
                          <Badge tone={statusTone(j.status)}>{pick(j, ['status'], '未知')}</Badge>
                        </td>
                        <td className="py-2 text-right tabular-nums text-slate-500 dark:text-slate-400">
                          {pick(j, ['elapsed_ms', 'duration_ms', 'elapsed'], '—')}
                          {pick(j, ['elapsed_ms', 'duration_ms'], null) ? ' ms' : ''}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </AsyncBlock>
          </Card>

          {/* 接口健康 + 趋势图 */}
          <div className="grid gap-4 xl:grid-cols-2">
            <Card>
              <SectionHeader
                title="接口健康"
                subtitle="近一周期各接口耗时探测"
                badge={apis.length ? <Badge tone="slate">{apis.length} 个接口</Badge> : null}
              />
              <AsyncBlock
                loading={false}
                error={null}
                empty={apis.length === 0}
                emptyText="暂无接口探测数据"
                emptyHint="后端返回 api/endpoints 列表后这里会出耗时图"
              >
                <>
                  <ApiChart apis={apis} />
                  <ul className="mt-3 space-y-1.5">
                    {apis.slice(0, 8).map((a, i) => {
                      const st = pick(a, ['status', 'state'], 'ok')
                      return (
                        <li key={pick(a, ['path', 'name', 'id'], `api-${i}`)} className="flex items-center justify-between gap-2 rounded-md border border-slate-100 bg-slate-50/60 px-2 py-1.5 text-[11px] dark:border-slate-800 dark:bg-slate-800/50">
                          <span className="min-w-0 truncate font-mono text-slate-600 dark:text-slate-300">
                            {pick(a, ['path', 'name', 'api', 'endpoint'], '—')}
                          </span>
                          <span className="flex shrink-0 items-center gap-2">
                            <span className="tabular-nums text-slate-400">
                              {pick(a, ['p95_ms', 'avg_ms', 'elapsed_ms', 'latency_ms', 'ms'], '—')}
                              {pick(a, ['p95_ms', 'avg_ms', 'elapsed_ms', 'latency_ms', 'ms'], null) ? ' ms' : ''}
                            </span>
                            <Badge tone={statusTone(st)}>{String(st)}</Badge>
                          </span>
                        </li>
                      )
                    })}
                  </ul>
                </>
              </AsyncBlock>
            </Card>

            <Card>
              <SectionHeader
                title="采样趋势"
                subtitle="返回了时序字段时展示，否则留空说明"
                badge={series ? <Badge tone="blue">时序</Badge> : null}
              />
              {series ? (
                <SeriesChart series={series} />
              ) : (
                <div className="mt-3">
                  <AsyncBlock
                    loading={false}
                    error={null}
                    empty
                    emptyText="暂无时序数据"
                    emptyHint="接口返回 series / latency_series 后这里会渲染趋势图"
                  />
                </div>
              )}
            </Card>
          </div>
        </div>
      </AsyncBlock>
    </PageShell>
  )
}
