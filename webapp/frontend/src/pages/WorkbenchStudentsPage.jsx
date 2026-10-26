/**
 * /workbench/students 学员管理：学员 / 成员名单的服务端分页表。
 *
 * 邮箱由后端打码返回（本地环境往往只有种子数据），因此 meta.seed 必须打
 * 「种子演示数据」徽标 —— 不能让用户以为这是真实付费学员档案。
 */
import React, { useState } from 'react'
import { AsyncBlock, Badge, Button, Card, PageShell, Pagination, SectionHeader, StatTile } from '../components/ui.jsx'
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

const STATUS_TONE = {
  active: 'emerald',
  normal: 'emerald',
  enabled: 'emerald',
  expired: 'rose',
  disabled: 'slate',
  inactive: 'slate',
  pending: 'amber',
  trial: 'blue',
  banned: 'rose',
}

const STATUS_TEXT = {
  active: '有效',
  normal: '有效',
  enabled: '有效',
  expired: '已到期',
  disabled: '停用',
  inactive: '未激活',
  pending: '待激活',
  trial: '试用',
  banned: '封禁',
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

export default function WorkbenchStudentsPage() {
  const [page, setPage] = useState(1)
  const { data, loading, error, reload } = useApi(
    () => api.get('/workbench/students', { page, size: PAGE_SIZE }),
    [page],
  )

  const items = arr(data?.items)
  const total = Number(data?.total ?? items.length) || 0
  const summary = data?.summary || data?.stats || {}

  return (
    <PageShell
      title="学员管理"
      subtitle="学员名单、套餐与到期时间 · 邮箱由后端脱敏返回"
      actions={
        <div className="flex items-center gap-2">
          <MetaBadges meta={data?.meta} />
          <Button variant="secondary" size="sm" onClick={reload} loading={loading}>
            刷新
          </Button>
        </div>
      }
    >
      <AsyncBlock
        loading={loading}
        error={error}
        onRetry={reload}
        loadingLabel="学员列表加载中"
        empty={!loading && !error && items.length === 0}
        emptyText="暂无学员"
        emptyHint="注册用户或导入名单后会出现在这里"
      >
        <div className="space-y-4">
          {/* 概览磁贴：后端给了 summary 就用，没有就按当页数据统计并标注口径 */}
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <StatTile label="学员总数" value={summary.total ?? total} sub="服务端分页总数" />
            <StatTile
              label="有效套餐"
              value={pick(summary, ['active', 'active_count', 'valid'], '—')}
              sub="状态为有效 / 试用"
            />
            <StatTile
              label="已到期"
              value={pick(summary, ['expired', 'expired_count'], '—')}
              sub="需要跟进续费"
              tone="text-rose-600 dark:text-rose-400"
            />
            <StatTile
              label="本月新增"
              value={pick(summary, ['new_this_month', 'month_new'], '—')}
              sub="按注册时间统计"
            />
          </div>

          <Card>
            <SectionHeader
              title="学员列表"
              subtitle={`共 ${total} 人 · 每页 ${PAGE_SIZE} 条`}
              badge={<MetaBadges meta={data?.meta} />}
            />
            <div className="mt-3 overflow-x-auto thin-scroll">
              <table className="w-full min-w-[760px] text-xs">
                <thead className="sticky top-0 z-10 bg-white dark:bg-slate-900">
                  <tr className="text-left text-[11px] text-slate-400">
                    <th className="pb-1.5 font-medium">姓名</th>
                    <th className="pb-1.5 font-medium">邮箱</th>
                    <th className="pb-1.5 font-medium">注册时间</th>
                    <th className="pb-1.5 font-medium">到期时间</th>
                    <th className="pb-1.5 font-medium">套餐</th>
                    <th className="pb-1.5 text-right font-medium">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((s, i) => {
                    const status = String(pick(s, ['status', 'state'], 'active')).toLowerCase()
                    const expiry = pick(s, ['expires_at', 'expiry', 'plan_expiry', 'expire_date', 'end_date'], null)
                    return (
                      <tr
                        key={pick(s, ['id', 'student_id', 'user_id', 'email'], `stu-${i}`)}
                        className="border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/60"
                      >
                        <td className="py-2 font-medium text-slate-700 dark:text-slate-200">
                          {pick(s, ['name', 'real_name', 'nickname', 'username'], '—')}
                        </td>
                        <td className="py-2 tabular-nums text-slate-500 dark:text-slate-400">
                          {pick(s, ['email', 'mail', 'email_masked'], '—')}
                        </td>
                        <td className="py-2 tabular-nums text-slate-500 dark:text-slate-400">
                          {fmtDateTime(pick(s, ['created_at', 'register_at', 'joined_at', 'signup_at']))}
                        </td>
                        <td className="py-2 tabular-nums text-slate-500 dark:text-slate-400">
                          {expiry ? fmtDateTime(expiry) : '—'}
                        </td>
                        <td className="py-2">
                          <Badge tone="blue">{pick(s, ['plan', 'package', 'sku', 'tier'], '免费版')}</Badge>
                        </td>
                        <td className="py-2 text-right">
                          <Badge tone={STATUS_TONE[status] || 'slate'}>
                            {STATUS_TEXT[status] || pick(s, ['status'], '未知')}
                          </Badge>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
          </Card>
        </div>
      </AsyncBlock>
    </PageShell>
  )
}
