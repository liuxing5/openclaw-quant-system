/**
 * /system/audit-logs 审计日志：时间 / 操作者 / 动作 / 对象 / IP 的服务端分页表。
 *
 * - 支持按操作者筛选（输入 300ms 防抖），筛选条件变化自动回到第 1 页。
 * - meta.source 标记数据来自 auth.audit_log_entries 还是本系统操作日志，
 *   必须打徽标展示，避免把两套来源混为一谈。
 */
import React, { useEffect, useState } from 'react'
import { AsyncBlock, Badge, Button, Card, PageShell, Pagination, SectionHeader } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDateTime } from '../lib/format.js'

const PAGE_SIZE = 20
const arr = (v) => (Array.isArray(v) ? v : [])

function pick(obj, keys, fallback = null) {
  if (!obj) return fallback
  for (const k of keys) {
    const v = obj[k]
    if (v !== undefined && v !== null && v !== '') return v
  }
  return fallback
}

/** 动作前缀决定徽标色：写操作红、读操作蓝、登录类绿。 */
function actionTone(action = '') {
  const a = String(action).toLowerCase()
  if (/(delete|remove|drop|reset|ban|disable|patch|put|post)/.test(a)) return 'rose'
  if (/(login|logout|register|auth)/.test(a)) return 'emerald'
  return 'blue'
}

function MetaBadges({ meta }) {
  if (!meta) return null
  const badges = []
  if (meta.local) badges.push(<Badge key="local" tone="blue">本地单用户</Badge>)
  if (meta.seed) badges.push(<Badge key="seed" tone="amber">种子演示数据</Badge>)
  if (meta.degraded) badges.push(<Badge key="degraded" tone="rose">降级数据</Badge>)
  if (meta.source) badges.push(<Badge key="src" tone="slate">来源 {String(meta.source)}</Badge>)
  if (!badges.length) return null
  return <span className="inline-flex flex-wrap items-center gap-1.5">{badges}</span>
}

export default function SystemAuditLogsPage() {
  const [actor, setActor] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)

  // 防抖：只在停顿 300ms 后才发起带 actor 条件的查询
  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(actor.trim())
      setPage(1)
    }, 300)
    return () => clearTimeout(timer)
  }, [actor])

  const { data, loading, error, reload } = useApi(
    () => api.get('/system/audit-logs', { page, size: PAGE_SIZE, actor: query }),
    [page, query],
  )

  const items = arr(data?.items)
  const total = Number(data?.total ?? items.length) || 0

  return (
    <PageShell
      title="审计日志"
      subtitle="读 auth.audit_log_entries + 本系统操作日志 · 追查越权、误操作与配置变更"
      actions={
        <div className="flex items-center gap-2">
          <MetaBadges meta={data?.meta} />
          <Button variant="secondary" size="sm" onClick={reload} loading={loading}>
            刷新
          </Button>
        </div>
      }
    >
      <Card>
        <SectionHeader
          title="操作留痕"
          subtitle={`共 ${total} 条 · 每页 ${PAGE_SIZE} 条`}
          action={
            <label className="flex items-center gap-2">
              <span className="text-xs text-slate-500 dark:text-slate-400">操作者</span>
              <input
                type="search"
                value={actor}
                onChange={(e) => setActor(e.target.value)}
                placeholder="按操作者筛选"
                className="w-52 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-700 outline-none placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
              />
            </label>
          }
        />
        <div className="mt-3">
          <AsyncBlock
            loading={loading}
            error={error}
            onRetry={reload}
            loadingLabel="审计日志加载中"
            empty={!loading && !error && items.length === 0}
            emptyText={query ? `操作者「${query}」暂无日志` : '暂无审计日志'}
            emptyHint={query ? '换个操作者名或清空筛选' : '登录、改配置、回复反馈等动作会在这里留痕'}
          >
            <div className="overflow-x-auto thin-scroll">
              <table className="w-full min-w-[760px] text-xs">
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
                  {items.map((r, i) => {
                    const action = pick(r, ['action', 'event', 'method', 'type', 'operation'], '—')
                    return (
                      <tr
                        key={pick(r, ['id', 'log_id', 'entry_id'], `log-${i}`)}
                        className="border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/60"
                      >
                        <td className="py-2 tabular-nums text-slate-500 dark:text-slate-400">
                          {fmtDateTime(pick(r, ['created_at', 'occurred_at', 'at', 'time', 'ts']))}
                        </td>
                        <td className="py-2 font-medium text-slate-700 dark:text-slate-200">
                          {pick(r, ['actor', 'operator', 'username', 'user', 'account'], '—')}
                        </td>
                        <td className="py-2">
                          <Badge tone={actionTone(action)}>{action}</Badge>
                        </td>
                        <td className="max-w-[22rem] truncate py-2 text-slate-600 dark:text-slate-300">
                          {pick(r, ['target', 'object', 'resource', 'detail', 'message', 'path'], '—')}
                        </td>
                        <td className="py-2 text-right tabular-nums text-slate-400">
                          {pick(r, ['ip', 'ip_address', 'client_ip'], '—')}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
          </AsyncBlock>
        </div>
      </Card>
    </PageShell>
  )
}
