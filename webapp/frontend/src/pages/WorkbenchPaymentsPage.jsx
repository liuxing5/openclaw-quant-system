/**
 * /workbench/payments 订单管理：汇总磁贴 + 服务端分页订单表。
 *
 * 汇总优先用后端 summary/stats；后端没给时按当页明细现算，并在磁贴副标题里
 * 明确标注「本页合计」—— 不把当页口径冒充全站口径。
 * meta.seed / meta.local / meta.degraded 必须显式打徽标。
 */
import React, { useState } from 'react'
import { AsyncBlock, Badge, Button, Card, PageShell, Pagination, SectionHeader, StatTile } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtDateTime } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

const PLAN_PRICE = { month: 98, quarter: 268, year: 688 }
const PLAN_LABEL = { month: '月卡 30 天', quarter: '季卡 90 天', year: '年卡 365 天' }
const CHANNEL_LABEL = { wechat: '微信', alipay: '支付宝' }

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
  success: 'emerald',
  paid: 'emerald',
  ok: 'emerald',
  closed: 'slate',
  cancelled: 'slate',
  canceled: 'slate',
  pending: 'amber',
  processing: 'amber',
  created: 'amber',
  failed: 'rose',
  fail: 'rose',
  error: 'rose',
  refund: 'rose',
  refunded: 'rose',
}

const STATUS_TEXT = {
  success: '支付成功',
  paid: '已支付',
  pending: '待支付',
  processing: '处理中',
  created: '待支付',
  failed: '支付失败',
  fail: '支付失败',
  closed: '已关闭',
  cancelled: '已取消',
  canceled: '已取消',
  refund: '已退款',
  refunded: '已退款',
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

function RecordPayment({ onDone }) {
  const [email, setEmail] = useState('')
  const [plan, setPlan] = useState('month')
  const [amount, setAmount] = useState(PLAN_PRICE.month)
  const [channel, setChannel] = useState('wechat')
  const [adminKey, setAdminKey] = useState('')
  const [note, setNote] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setLoading(true)
    try {
      const r = await api.post('/workbench/payments', {
        email, plan, amount: Number(amount), channel, admin_key: adminKey, note,
      })
      toast.success('收款已登记', `${r.order_no} · ${CHANNEL_LABEL[channel]} ${fmtAmount(r.amount)}`)
      setEmail(''); setNote('')
      await onDone?.()
    } catch (err) {
      toast.error('登记失败', err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card>
      <SectionHeader
        title="登记人工收款"
        subtitle="客户通过微信 / 支付宝转账后，在此登记并立即开通或续费会员"
        badge={<Badge tone="amber">管理员操作</Badge>}
      />
      <form onSubmit={submit} className="mt-3 grid gap-2 md:grid-cols-6">
        <input
          type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
          placeholder="客户注册邮箱"
          className="rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-xs md:col-span-2 dark:border-slate-700 dark:bg-slate-800"
        />
        <select
          value={plan}
          onChange={(e) => { setPlan(e.target.value); setAmount(PLAN_PRICE[e.target.value]) }}
          className="rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-xs dark:border-slate-700 dark:bg-slate-800"
        >
          {Object.entries(PLAN_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <input
          type="number" min="1" step="0.01" required value={amount}
          onChange={(e) => setAmount(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-xs tabular-nums dark:border-slate-700 dark:bg-slate-800"
        />
        <select
          value={channel} onChange={(e) => setChannel(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-xs dark:border-slate-700 dark:bg-slate-800"
        >
          <option value="wechat">微信</option>
          <option value="alipay">支付宝</option>
        </select>
        <input
          type="password" required value={adminKey} onChange={(e) => setAdminKey(e.target.value)}
          placeholder="管理员密钥"
          className="rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-xs dark:border-slate-700 dark:bg-slate-800"
        />
        <input
          value={note} onChange={(e) => setNote(e.target.value)}
          placeholder="备注（可选，如转账尾号）"
          className="rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-xs md:col-span-3 dark:border-slate-700 dark:bg-slate-800"
        />
        <Button type="submit" size="sm" loading={loading} className="md:col-span-1">确认收款并开通</Button>
      </form>
      <p className="mt-2 text-[10px] text-slate-400">
        这是人工收款登记，不是微信/支付宝官方支付接口回调；订单会记录渠道、金额、客户邮箱和操作时间。
      </p>
    </Card>
  )
}

export default function WorkbenchPaymentsPage() {
  const [page, setPage] = useState(1)
  const { data, loading, error, reload } = useApi(
    () => api.get('/workbench/payments', { page, size: PAGE_SIZE }),
    [page],
  )

  const items = arr(data?.items)
  const total = Number(data?.total ?? items.length) || 0
  const summary = data?.summary || data?.stats || null

  // 后端没给汇总时按当页明细现算，并把口径写在副标题里
  const local = {
    count: items.length,
    amount: items.reduce((sum, o) => sum + (Number(pick(o, ['amount', 'total_amount', 'price', 'fee'], 0)) || 0), 0),
    success: items.filter((o) => ['success', 'paid', 'ok'].includes(String(pick(o, ['status'], '')).toLowerCase())).length,
    failed: items.filter((o) => ['failed', 'fail', 'error'].includes(String(pick(o, ['status'], '')).toLowerCase())).length,
  }
  const basis = summary ? '服务端汇总' : '本页合计（后端未返回 summary）'
  const summaryValue = (keys, fallback) => (summary ? pick(summary, keys, fallback) : fallback)

  return (
    <PageShell
      title="订单管理"
      subtitle="订单号、金额、支付方式与状态 · 服务端分页"
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
        loadingLabel="订单列表加载中"
        empty={!loading && !error && items.length === 0}
        emptyText="暂无订单"
        emptyHint="用户下单支付后会出现在这里"
      >
        <div className="space-y-4">
          {/* 汇总磁贴 */}
          <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
            <StatTile label="订单数" value={summaryValue(['count', 'total', 'orders'], local.count)} sub={`${basis} · 全站 ${total} 笔`} />
            <StatTile
              label="订单总额"
              value={fmtAmount(summaryValue(['amount', 'total_amount', 'gmv'], local.amount))}
              sub={basis}
            />
            <StatTile
              label="成功"
              value={summaryValue(['success', 'success_count', 'paid', 'paid_count'], local.success)}
              sub="支付成功 / 已支付"
              tone="text-emerald-600 dark:text-emerald-400"
            />
            <StatTile
              label="失败"
              value={summaryValue(['failed', 'fail_count', 'failed_count'], local.failed)}
              sub="支付失败 / 已退款"
              tone="text-rose-600 dark:text-rose-400"
            />
          </div>

          <RecordPayment onDone={reload} />

          <Card>
            <SectionHeader
              title="订单列表"
              subtitle={`共 ${total} 笔 · 每页 ${PAGE_SIZE} 条`}
              badge={<MetaBadges meta={data?.meta} />}
            />
            <div className="mt-3 overflow-x-auto thin-scroll">
              <table className="w-full min-w-[820px] text-xs">
                <thead className="sticky top-0 z-10 bg-white dark:bg-slate-900">
                  <tr className="text-left text-[11px] text-slate-400">
                    <th className="pb-1.5 font-medium">订单号</th>
                    <th className="pb-1.5 font-medium">用户</th>
                    <th className="pb-1.5 font-medium">套餐</th>
                    <th className="pb-1.5 text-right font-medium">金额</th>
                    <th className="pb-1.5 font-medium">支付方式</th>
                    <th className="pb-1.5 font-medium">状态</th>
                    <th className="pb-1.5 text-right font-medium">时间</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((o, i) => {
                    const status = String(pick(o, ['status', 'state'], '')).toLowerCase()
                    const amount = pick(o, ['amount', 'total_amount', 'price', 'fee'], 0)
                    return (
                      <tr
                        key={pick(o, ['id', 'order_no', 'out_trade_no', 'trade_no'], `order-${i}`)}
                        className="border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/60"
                      >
                        <td className="py-2 font-mono tabular-nums text-slate-600 dark:text-slate-300">
                          {pick(o, ['order_no', 'out_trade_no', 'trade_no', 'order_id', 'id'], '—')}
                        </td>
                        <td className="py-2 text-slate-600 dark:text-slate-300">
                          {pick(o, ['username', 'user_name', 'nickname', 'user', 'buyer'], '—')}
                        </td>
                        <td className="py-2">
                          <Badge tone="blue">{pick(o, ['plan', 'package', 'product', 'sku'], '—')}</Badge>
                        </td>
                        <td className="py-2 text-right font-semibold tabular-nums text-slate-800 dark:text-slate-100">
                          {fmtAmount(amount)}
                        </td>
                        <td className="py-2 text-slate-500 dark:text-slate-400">
                          {pick(o, ['channel', 'pay_method', 'payment_method', 'method'], '—')}
                        </td>
                        <td className="py-2">
                          <Badge tone={STATUS_TONE[status] || 'slate'}>
                            {STATUS_TEXT[status] || pick(o, ['status'], '未知')}
                          </Badge>
                        </td>
                        <td className="py-2 text-right tabular-nums text-slate-500 dark:text-slate-400">
                          {fmtDateTime(pick(o, ['created_at', 'paid_at', 'paid_time', 'updated_at', 'at']))}
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
