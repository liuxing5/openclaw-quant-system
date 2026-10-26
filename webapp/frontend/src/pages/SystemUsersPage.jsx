/**
 * /system/users 用户管理：关键字搜索（300ms 防抖）+ 服务端分页表格。
 *
 * - 后端对邮箱/手机号已做脱敏，这里原样展示，不二次加工。
 * - meta.local 表示后端只有本地单用户数据，必须打「本地单用户」徽标，
 *   不能让用户误以为这是一个多租户的完整用户库。
 * - 接口未上线（404/500）时只显示错误态 + 重试按钮，页面不白屏。
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

const STATUS_TONE = {
  active: 'emerald',
  enabled: 'emerald',
  ok: 'emerald',
  normal: 'emerald',
  disabled: 'slate',
  inactive: 'slate',
  pending: 'amber',
  locked: 'amber',
  banned: 'rose',
  disabled_forced: 'rose',
  error: 'rose',
}

function statusText(status) {
  const map = {
    active: '正常',
    enabled: '正常',
    normal: '正常',
    disabled: '停用',
    inactive: '未激活',
    pending: '待激活',
    locked: '锁定',
    banned: '封禁',
  }
  return map[String(status || '').toLowerCase()] || status || '未知'
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

export default function SystemUsersPage() {
  const [keyword, setKeyword] = useState('')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)

  // 输入 300ms 后才发请求：边打字边查会给后端制造无谓的全表 LIKE
  useEffect(() => {
    const timer = setTimeout(() => {
      setQuery(keyword.trim())
      setPage(1)
    }, 300)
    return () => clearTimeout(timer)
  }, [keyword])

  const { data, loading, error, reload } = useApi(
    () => api.get('/system/users', { page, size: PAGE_SIZE, keyword: query }),
    [page, query],
  )

  const items = arr(data?.items)
  const total = Number(data?.total ?? items.length) || 0

  return (
    <PageShell
      title="用户管理"
      subtitle="读 auth.users 脱敏结果 · 支持按用户名 / 邮箱模糊搜索"
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
          title="账号列表"
          subtitle={`共 ${total} 个账号 · 每页 ${PAGE_SIZE} 条`}
          action={
            <label className="flex items-center gap-2">
              <span className="text-xs text-slate-500 dark:text-slate-400">搜索</span>
              <input
                type="search"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder="用户名 / 邮箱"
                className="w-56 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-700 outline-none placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
              />
            </label>
          }
        />
        <div className="mt-3">
          <AsyncBlock
            loading={loading}
            error={error}
            onRetry={reload}
            loadingLabel="用户列表加载中"
            empty={!loading && !error && items.length === 0}
            emptyText={query ? `没有匹配「${query}」的账号` : '暂无用户'}
            emptyHint={query ? '换个关键字或清空搜索框' : '注册或初始化账号后会出现在这里'}
          >
            <div className="overflow-x-auto thin-scroll">
              <table className="w-full min-w-[720px] text-xs">
                <thead className="sticky top-0 z-10 bg-white dark:bg-slate-900">
                  <tr className="text-left text-[11px] text-slate-400">
                    <th className="pb-1.5 font-medium">用户名</th>
                    <th className="pb-1.5 font-medium">邮箱</th>
                    <th className="pb-1.5 font-medium">角色</th>
                    <th className="pb-1.5 font-medium">注册时间</th>
                    <th className="pb-1.5 font-medium">最近登录</th>
                    <th className="pb-1.5 text-right font-medium">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((u, i) => {
                    const status = String(pick(u, ['status', 'state', 'account_status'], 'active'))
                    const roles = pick(u, ['roles'], null)
                    const roleText = Array.isArray(roles)
                      ? roles.join(' / ')
                      : pick(u, ['role', 'role_name', 'group'], '—')
                    return (
                      <tr
                        key={pick(u, ['id', 'user_id', 'username'], `user-${i}`)}
                        className="border-t border-slate-100 dark:border-slate-800"
                      >
                        <td className="py-2 font-medium text-slate-700 dark:text-slate-200">
                          {pick(u, ['username', 'user_name', 'login', 'name'], '—')}
                        </td>
                        <td className="py-2 tabular-nums text-slate-500 dark:text-slate-400">
                          {pick(u, ['email', 'mail', 'email_masked'], '—')}
                        </td>
                        <td className="py-2">
                          <Badge tone="blue">{roleText}</Badge>
                        </td>
                        <td className="py-2 tabular-nums text-slate-500 dark:text-slate-400">
                          {fmtDateTime(pick(u, ['created_at', 'register_at', 'signup_at', 'joined_at']))}
                        </td>
                        <td className="py-2 tabular-nums text-slate-500 dark:text-slate-400">
                          {fmtDateTime(pick(u, ['last_login_at', 'last_login', 'last_seen_at', 'login_at']))}
                        </td>
                        <td className="py-2 text-right">
                          <Badge tone={STATUS_TONE[status.toLowerCase()] || 'slate'}>{statusText(status)}</Badge>
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
