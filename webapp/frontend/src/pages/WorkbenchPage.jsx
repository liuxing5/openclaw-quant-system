/**
 * /workbench 工作台运营总览。
 *
 * 原站 /workbench 会直接 302 到 monitor 子页；本项目独立渲染总览：
 * 四个运营磁贴 + 四个快捷入口 + 最近反馈 / 最近订单，监控明细仍走 /workbench/monitor。
 *
 * 三个读接口（monitor / feedbacks / payments）各自 AsyncBlock 三态，任一未上线只坏自己那块。
 */
import React from 'react'
import { Link } from 'react-router-dom'
import { AsyncBlock, Badge, Card, PageShell, SectionHeader, TextLink } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtDateTime } from '../lib/format.js'

const arr = (v) => (Array.isArray(v) ? v : [])

function pick(obj, keys, fallback = null) {
  if (!obj) return fallback
  for (const k of keys) {
    const v = obj[k]
    if (v !== undefined && v !== null && v !== '') return v
  }
  return fallback
}

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

const FEEDBACK_TONE = { bug: 'rose', 缺陷: 'rose', feature: 'blue', 功能: 'blue', suggest: 'amber', 建议: 'amber', other: 'slate', 其他: 'slate' }
const FEEDBACK_LABEL = { bug: '缺陷', feature: '功能', suggest: '建议', other: '其他' }

const STATUS_TONE = { open: 'amber', pending: 'amber', replied: 'blue', resolved: 'emerald', success: 'emerald', paid: 'emerald', closed: 'slate', failed: 'rose', refund: 'rose', refunded: 'rose' }
const statusText = (s) => ({ open: '待处理', pending: '待处理', replied: '已回复', resolved: '已解决', success: '支付成功', paid: '已支付', closed: '已关闭', failed: '支付失败', refund: '已退款', refunded: '已退款' }[String(s || '').toLowerCase()] || s || '未知')

/* ------------------------------------------------------------------- 运营磁贴 */

function MetricTile({ label, value, sub, unit, tone, state, meta }) {
  return (
    <Card className="flex h-full flex-col">
      <p className="text-[11px] text-slate-500 dark:text-slate-400">{label}</p>
      <AsyncBlock
        loading={state.loading}
        error={state.error}
        onRetry={state.retry}
        loadingLabel="取数中"
        empty={state.empty}
        emptyText="暂无数据"
      >
        <>
          <p className={`mt-1 text-2xl font-semibold tabular-nums ${tone || 'text-slate-900 dark:text-slate-100'}`}>
            {value}
            {unit ? <span className="ml-1 text-xs font-normal text-slate-400">{unit}</span> : null}
          </p>
          <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">{sub}</p>
          <div className="mt-1.5">
            <MetaBadges meta={meta} />
          </div>
        </>
      </AsyncBlock>
    </Card>
  )
}

/* ------------------------------------------------------------------ 快捷入口磁贴 */

function QuickLink({ to, title, desc, icon }) {
  return (
    <Link
      to={to}
      className="group block rounded-xl transition-transform hover:-translate-y-0.5"
    >
      <Card className="h-full">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <span className="inline-flex size-7 items-center justify-center rounded-lg bg-brand-50 text-xs font-bold text-brand-600 dark:bg-brand-500/10 dark:text-brand-300">
              {icon}
            </span>
            <p className="mt-2 text-sm font-semibold text-slate-800 group-hover:text-brand-600 dark:text-slate-100 dark:group-hover:text-brand-300">
              {title}
            </p>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">{desc}</p>
          </div>
          <span className="shrink-0 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-brand-500">→</span>
        </div>
      </Card>
    </Link>
  )
}

/* ------------------------------------------------------------------------ 页面 */

export default function WorkbenchPage() {
  const monitor = useApi(() => api.get('/workbench/monitor'), [])
  const feedbacks = useApi(() => api.get('/workbench/feedbacks', { page: 1, size: 5 }), [])
  const payments = useApi(() => api.get('/workbench/payments', { page: 1, size: 5 }), [])

  const m = monitor.data || {}
  const sys = m.sys || m.system || m
  const sources = arr(m.feed_sources ?? m.sources ?? m.feeds)
  const badSources = sources.filter((s) => !['ok', 'up', 'healthy'].includes(String(s.status || '').toLowerCase()))
  const fbItems = arr(feedbacks.data?.items)
  const pendingFeedback =
    pick(feedbacks.data, ['pending', 'pending_count', 'open_count', 'todo'], null) ??
    fbItems.filter((f) => ['open', 'pending'].includes(String(f.status || '').toLowerCase())).length
  const payItems = arr(payments.data?.items)
  const todayOrders = pick(payments.data, ['today_count', 'today_orders', 'orders_today'], null) ?? payments.data?.total ?? null

  const reloadAll = () => {
    monitor.reload()
    feedbacks.reload()
    payments.reload()
  }

  return (
    <PageShell
      title="工作台"
      subtitle="运营总览：在线与运行状态、待办反馈、今日订单（原站此路径会跳转到运行监控）"
      actions={
        <button
          type="button"
          onClick={reloadAll}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
        >
          全部刷新
        </button>
      }
    >
      <div className="space-y-4">
        {/* 运营磁贴 */}
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          <MetricTile
            label="在线人数"
            value={monitor.error ? '—' : pick(sys, ['online_users', 'online', 'active_users'], 0)}
            unit="人"
            sub="运行监控采样"
            state={{
              loading: monitor.loading,
              error: monitor.error,
              retry: monitor.reload,
              empty: !monitor.loading && !monitor.error && !monitor.data,
            }}
            meta={m.meta}
          />
          <MetricTile
            label="数据源异常"
            value={sources.length ? badSources.length : '—'}
            unit={`/ ${sources.length} 个`}
            tone={badSources.length ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400'}
            sub={
              !sources.length
                ? '暂无数据源采样'
                : badSources.length
                  ? badSources.slice(0, 2).map((s) => pick(s, ['name', 'id'], '')).join('、')
                  : '全部正常'
            }
            state={{
              loading: monitor.loading,
              error: monitor.error,
              retry: monitor.reload,
              empty: !monitor.loading && !monitor.error && !sources.length,
            }}
            meta={m.meta}
          />
          <MetricTile
            label="待处理反馈"
            value={feedbacks.error ? '—' : pendingFeedback}
            unit="条"
            tone={pendingFeedback ? 'text-amber-600 dark:text-amber-400' : 'text-slate-900 dark:text-slate-100'}
            sub={`累计 ${feedbacks.data?.total ?? fbItems.length} 条反馈`}
            state={{
              loading: feedbacks.loading,
              error: feedbacks.error,
              retry: feedbacks.reload,
              empty: !feedbacks.loading && !feedbacks.error && !feedbacks.data,
            }}
            meta={feedbacks.data?.meta}
          />
          <MetricTile
            label="今日订单"
            value={payments.error ? '—' : todayOrders}
            unit="笔"
            sub={`订单总额 ${fmtAmount(pick(payments.data, ['total_amount', 'amount_today'], 0))}`}
            state={{
              loading: payments.loading,
              error: payments.error,
              retry: payments.reload,
              empty: !payments.loading && !payments.error && !payments.data,
            }}
            meta={payments.data?.meta}
          />
        </div>

        {/* 快捷入口 */}
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          <QuickLink to="/workbench/monitor" icon="监" title="运行监控" desc="连接池 / 数据源 / 定时任务 / 接口耗时" />
          <QuickLink to="/workbench/students" icon="学" title="学员管理" desc="学员名单、套餐与到期时间" />
          <QuickLink to="/workbench/payments" icon="单" title="订单管理" desc="订单号、金额、支付方式与状态" />
          <QuickLink to="/workbench/feedbacks" icon="反" title="反馈管理" desc="待处理反馈与内联回复" />
        </div>

        {/* 最近反馈 + 最近订单 */}
        <div className="grid gap-4 xl:grid-cols-2">
          <Card>
            <SectionHeader
              title="最近反馈"
              subtitle="最新 5 条用户反馈"
              badge={<MetaBadges meta={feedbacks.data?.meta} localLabel="本地单用户" />}
              action={<TextLink to="/workbench/feedbacks">全部反馈 →</TextLink>}
            />
            <AsyncBlock
              loading={feedbacks.loading}
              error={feedbacks.error}
              onRetry={feedbacks.reload}
              loadingLabel="反馈加载中"
              empty={fbItems.length === 0}
              emptyText="暂无反馈"
              emptyHint="用户提交反馈后会出现在这里"
            >
              <ul className="mt-3 space-y-2">
                {fbItems.map((f, i) => {
                  const cat = String(pick(f, ['category', 'type'], 'other'))
                  const status = String(pick(f, ['status'], 'open'))
                  return (
                    <li key={pick(f, ['id', 'feedback_id'], `fb-${i}`)} className="rounded-lg border border-slate-100 bg-slate-50/60 px-2.5 py-2 dark:border-slate-800 dark:bg-slate-800/50">
                      <div className="flex items-center justify-between gap-2">
                        <Badge tone={FEEDBACK_TONE[cat.toLowerCase()] || 'slate'}>{FEEDBACK_LABEL[cat.toLowerCase()] || cat}</Badge>
                        <span className="flex items-center gap-1.5">
                          <Badge tone={STATUS_TONE[status.toLowerCase()] || 'slate'}>{statusText(status)}</Badge>
                          <span className="tabular-nums text-[10px] text-slate-400">
                            {fmtDateTime(pick(f, ['created_at', 'submit_at', 'at']))}
                          </span>
                        </span>
                      </div>
                      <p className="mt-1 line-clamp-2 text-xs text-slate-600 dark:text-slate-300">
                        {pick(f, ['content', 'message', 'body'], '—')}
                      </p>
                    </li>
                  )
                })}
              </ul>
            </AsyncBlock>
          </Card>

          <Card>
            <SectionHeader
              title="最近订单"
              subtitle="最新 5 笔订单"
              badge={<MetaBadges meta={payments.data?.meta} localLabel="本地单用户" />}
              action={<TextLink to="/workbench/payments">全部订单 →</TextLink>}
            />
            <AsyncBlock
              loading={payments.loading}
              error={payments.error}
              onRetry={payments.reload}
              loadingLabel="订单加载中"
              empty={payItems.length === 0}
              emptyText="暂无订单"
              emptyHint="用户下单后会在这里出现"
            >
              <div className="mt-3 overflow-x-auto thin-scroll">
                <table className="w-full min-w-[440px] text-xs">
                  <thead className="sticky top-0 z-10 bg-white dark:bg-slate-900">
                    <tr className="text-left text-[11px] text-slate-400">
                      <th className="pb-1.5 font-medium">订单号</th>
                      <th className="pb-1.5 font-medium">套餐</th>
                      <th className="pb-1.5 text-right font-medium">金额</th>
                      <th className="pb-1.5 text-right font-medium">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {payItems.map((p, i) => {
                      const status = String(pick(p, ['status', 'state'], ''))
                      return (
                        <tr key={pick(p, ['id', 'order_no', 'out_trade_no'], `pay-${i}`)} className="border-t border-slate-100 dark:border-slate-800">
                          <td className="py-2 tabular-nums text-slate-600 dark:text-slate-300">
                            {pick(p, ['order_no', 'order_id', 'out_trade_no', 'id'], '—')}
                          </td>
                          <td className="py-2 text-slate-600 dark:text-slate-300">
                            {pick(p, ['plan', 'package', 'product', 'sku'], '—')}
                          </td>
                          <td className="py-2 text-right font-medium tabular-nums text-slate-700 dark:text-slate-200">
                            {fmtAmount(pick(p, ['amount', 'total_amount', 'price', 'fee'], 0))}
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
            </AsyncBlock>
          </Card>
        </div>
      </div>
    </PageShell>
  )
}
