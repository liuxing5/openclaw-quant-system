/**
 * 模拟账户（/simulation/account）：一套可真正下单撮合的模拟盘。
 *
 * 能力闭环：建账户 → 看资金/持仓 → 下市价或限价单 → 后端按实时价撮合、计费、更新持仓。
 * 费率与后端一致：佣金 max(5, 成交额×0.03%)，卖出印花税 0.05%，过户费 0.002%。
 * 交易时段外仍可下单（用最近收盘价撮合），页面会提示价格来源。
 */
import { useEffect, useMemo, useState } from 'react'
import {
  AsyncBlock,
  Badge,
  Button,
  Card,
  EmptyState,
  PageShell,
  SectionHeader,
  Select,
  StatTile,
} from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtNum, fmtPct, pctTone } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

const POLL_MS = 30_000
const PAGE_SIZE = 20

const num = (v) => {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

const COMMISSION_RATE = 0.0003
const COMMISSION_MIN = 5
const STAMP_RATE = 0.0005
const TRANSFER_RATE = 0.00002

function feeOf(amount, side) {
  const commission = Math.max(COMMISSION_MIN, amount * COMMISSION_RATE)
  const stamp = side === 'sell' ? amount * STAMP_RATE : 0
  return commission + stamp + amount * TRANSFER_RATE
}

function ResetButton({ accountId, onDone }) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  async function doReset() {
    setBusy(true)
    try {
      await api.post('/simulation/reset', { account_id: accountId })
      toast.success('账户已重置', '清仓并恢复初始资金')
      onDone()
    } catch (e) {
      toast.error('重置失败', e.message)
    } finally {
      setBusy(false)
      setConfirming(false)
    }
  }

  if (!confirming) {
    return (
      <Button size="sm" variant="secondary" onClick={() => setConfirming(true)}>
        重置账户
      </Button>
    )
  }
  return (
    <span className="flex items-center gap-2">
      <span className="text-[11px] text-rose-500">清仓并恢复初始资金？</span>
      <Button size="sm" variant="danger" loading={busy} onClick={doReset}>
        确认重置
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
        取消
      </Button>
    </span>
  )
}

export default function SimulationAccountPage() {
  const accountsQ = useApi(() => api.get('/simulation/accounts'), [])
  const [accountId, setAccountId] = useState('')
  const [ticket, setTicket] = useState({
    ts_code: '',
    side: 'buy',
    order_type: 'market',
    price: '',
    qty: '100',
  })
  const [quote, setQuote] = useState(null)
  const [quoteLoading, setQuoteLoading] = useState(false)
  const [placing, setPlacing] = useState(false)
  const [newAccount, setNewAccount] = useState({ name: '', cash: '1000000' })
  const [creating, setCreating] = useState(false)

  const accounts = accountsQ.data?.items || []
  useEffect(() => {
    if (!accountId && accounts.length) setAccountId(accounts[0].id)
  }, [accounts, accountId])

  const summaryQ = useApi(
    () => api.get('/simulation/summary', { account_id: accountId || undefined }),
    [accountId],
    { enabled: !!accountId },
  )
  const positionsQ = useApi(
    () => api.get('/simulation/positions', { account_id: accountId || undefined, page: 1, size: 50 }),
    [accountId],
    { enabled: !!accountId },
  )
  const ordersQ = useApi(
    () => api.get('/simulation/orders', { account_id: accountId || undefined, page: 1, size: 10 }),
    [accountId],
    { enabled: !!accountId },
  )

  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      positionsQ.reload()
      summaryQ.reload()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [positionsQ.reload, summaryQ.reload])

  const reloadAll = () => {
    summaryQ.reload()
    positionsQ.reload()
    ordersQ.reload()
    accountsQ.reload()
  }

  async function lookup(code) {
    const c = (code || '').trim().toUpperCase()
    if (!c) return
    const full = c.includes('.') ? c : `${c}.${c.startsWith('6') || c.startsWith('9') ? 'SH' : 'SZ'}`
    setQuoteLoading(true)
    try {
      const res = await api.get('/market-data/realtime', { codes: full })
      const item = (res?.items || [])[0] || null
      setQuote(item)
      if (item && item.price) {
        setTicket((t) => ({ ...t, price: t.order_type === 'limit' ? String(item.price) : t.price }))
      }
    } catch {
      setQuote(null)
    } finally {
      setQuoteLoading(false)
    }
  }

  const price = ticket.order_type === 'limit' ? num(ticket.price) : num(quote?.price)
  const qty = num(ticket.qty) || 0
  const amount = price !== null && qty > 0 ? price * qty : null
  const fee = amount !== null ? feeOf(amount, ticket.side) : null

  async function placeOrder(e) {
    e.preventDefault()
    if (!accountId) {
      toast.error('请先创建模拟账户')
      return
    }
    if (!ticket.ts_code.trim()) {
      toast.error('请填写股票代码')
      return
    }
    if (!(qty > 0)) {
      toast.error('委托数量必须大于 0')
      return
    }
    const body = {
      account_id: accountId,
      ts_code: ticket.ts_code.trim().toUpperCase(),
      side: ticket.side,
      order_type: ticket.order_type,
      qty,
    }
    if (ticket.order_type === 'limit') {
      if (price === null || price <= 0) {
        toast.error('限价单必须填写有效价格')
        return
      }
      body.price = price
    }
    setPlacing(true)
    try {
      const res = await api.post('/simulation/orders', body)
      const order = res?.order || {}
      if (order.status === 'filled') {
        toast.success(
          '已成交',
          `${order.side === 'buy' ? '买入' : '卖出'} ${order.filled_qty} 股 @ ${fmtNum(order.filled_price, 2)}，费用 ${fmtNum(order.fee, 2)} 元`,
        )
      } else if (order.status === 'pending') {
        toast.success('委托已挂单', '限价单等待价格触发（可在委托页撤单）')
      } else {
        toast.error('委托被拒绝', order.reason || '资金或持仓不足')
      }
      reloadAll()
    } catch (e2) {
      toast.error('下单失败', e2.message)
    } finally {
      setPlacing(false)
    }
  }

  async function createAccount(e) {
    e.preventDefault()
    setCreating(true)
    try {
      const res = await api.post('/simulation/accounts', {
        name: newAccount.name.trim() || undefined,
        initial_cash: num(newAccount.cash) || 1000000,
      })
      toast.success('模拟账户已创建', res?.name || '')
      setNewAccount({ name: '', cash: '1000000' })
      await accountsQ.reload()
      if (res?.id) setAccountId(res.id)
    } catch (e2) {
      toast.error('创建失败', e2.message)
    } finally {
      setCreating(false)
    }
  }

  const s = summaryQ.data || {}
  const positions = positionsQ.data?.items || []
  const orders = ordersQ.data?.items || []
  const account = useMemo(() => accounts.find((a) => a.id === accountId), [accounts, accountId])

  return (
    <PageShell
      title="模拟账户"
      subtitle="真实费率撮合的模拟盘：实时价成交、T+1 不限制、整手买入、可重置"
      actions={
        <div className="flex items-center gap-2">
          <Select
            value={accountId}
            onChange={setAccountId}
            options={accounts.map((a) => ({ value: a.id, label: a.name }))}
            className="min-w-32"
          />
          {accountId ? <ResetButton accountId={accountId} onDone={reloadAll} /> : null}
        </div>
      }
    >
      {!accountsQ.data ? (
        <AsyncBlock loading={accountsQ.loading} error={accountsQ.error} onRetry={accountsQ.reload} loadingLabel="账户加载中…" />
      ) : accounts.length === 0 ? (
        <Card>
          <SectionHeader title="创建第一个模拟账户" subtitle="初始资金默认 100 万，可自定义" />
          <form onSubmit={createAccount} className="mt-3 flex flex-wrap items-end gap-3">
            <label className="block">
              <span className="mb-1 block text-[11px] text-slate-500">账户名称</span>
              <input
                value={newAccount.name}
                onChange={(e) => setNewAccount((n) => ({ ...n, name: e.target.value }))}
                placeholder="默认账户"
                className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-[11px] text-slate-500">初始资金（元）</span>
              <input
                type="number"
                value={newAccount.cash}
                onChange={(e) => setNewAccount((n) => ({ ...n, cash: e.target.value }))}
                className="w-40 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm tabular-nums dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
              />
            </label>
            <Button type="submit" loading={creating}>
              创建账户
            </Button>
          </form>
        </Card>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <StatTile label="总资产" value={fmtAmount(s.total_value)} sub={account?.name || '—'} />
            <StatTile label="可用资金" value={fmtAmount(s.cash)} sub={`初始 ${fmtAmount(s.initial_cash)}`} />
            <StatTile label="持仓市值" value={fmtAmount(s.equity)} sub={`${s.positions ?? 0} 只持仓`} />
            <StatTile
              label="累计盈亏"
              value={fmtAmount(s.total_pnl)}
              sub={`收益率 ${fmtPct(s.pnl_pct)}`}
              tone={pctTone(num(s.total_pnl))}
            />
            <StatTile label="今日盈亏" value={fmtAmount(s.today_pnl)} sub={`已实现 ${fmtAmount(s.realized_pnl)}`} tone={pctTone(num(s.today_pnl))} />
            <StatTile label="胜率" value={fmtPct(s.win_rate)} sub={`${s.win_count ?? 0} 胜 / ${s.closed_count ?? 0} 平仓`} />
          </div>

          <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
            <div className="space-y-4">
              <Card>
                <SectionHeader
                  title="持仓"
                  subtitle="价格取实时快照，缺源时用最近收盘价估值"
                  badge={<Badge tone="blue">{positions.length} 只</Badge>}
                />
                {positions.length === 0 ? (
                  <EmptyState text="暂无持仓" hint="在右侧下单面板买入第一只股票" />
                ) : (
                  <div className="mt-2 overflow-auto rounded-lg border border-slate-200 dark:border-slate-800">
                    <table className="w-full text-left text-xs">
                      <thead className="bg-slate-50 text-[11px] text-slate-500 dark:bg-slate-900 dark:text-slate-400">
                        <tr>
                          <th className="px-3 py-2">标的</th>
                          <th className="px-3 py-2 text-right">数量</th>
                          <th className="px-3 py-2 text-right">成本</th>
                          <th className="px-3 py-2 text-right">现价</th>
                          <th className="px-3 py-2 text-right">市值</th>
                          <th className="px-3 py-2 text-right">浮动盈亏</th>
                          <th className="px-3 py-2 text-right">今日盈亏</th>
                        </tr>
                      </thead>
                      <tbody>
                        {positions.map((p) => (
                          <tr key={p.id} className="border-t border-slate-100 dark:border-slate-800">
                            <td className="px-3 py-1.5">
                              <span className="font-medium text-slate-700 dark:text-slate-200">{p.stock_name || p.ts_code}</span>
                              <span className="ml-1 font-mono text-[10px] text-slate-400">{p.ts_code}</span>
                            </td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{p.qty}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{fmtNum(p.cost, 2)}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums">
                              {fmtNum(p.price, 2)}
                              <span className={`ml-1 text-[10px] ${pctTone(p.pct_chg)}`}>{fmtPct(p.pct_chg)}</span>
                            </td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{fmtAmount(p.market_value)}</td>
                            <td className={`px-3 py-1.5 text-right tabular-nums ${pctTone(p.pnl)}`}>
                              {fmtAmount(p.pnl)}
                              <span className="ml-1 text-[10px]">{fmtPct(p.pnl_pct)}</span>
                            </td>
                            <td className={`px-3 py-1.5 text-right tabular-nums ${pctTone(p.today_pnl)}`}>{fmtAmount(p.today_pnl)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>

              <Card>
                <SectionHeader title="最近委托" subtitle="市价单即时成交；限价单挂起，可在委托页撤单" />
                {orders.length === 0 ? (
                  <EmptyState text="暂无委托" hint="下单后这里会显示成交/拒绝原因" />
                ) : (
                  <div className="mt-2 overflow-auto rounded-lg border border-slate-200 dark:border-slate-800">
                    <table className="w-full text-left text-xs">
                      <thead className="bg-slate-50 text-[11px] text-slate-500 dark:bg-slate-900 dark:text-slate-400">
                        <tr>
                          <th className="px-3 py-2">时间</th>
                          <th className="px-3 py-2">标的</th>
                          <th className="px-3 py-2">方向</th>
                          <th className="px-3 py-2 text-right">数量</th>
                          <th className="px-3 py-2 text-right">成交价</th>
                          <th className="px-3 py-2 text-right">费用</th>
                          <th className="px-3 py-2">状态</th>
                        </tr>
                      </thead>
                      <tbody>
                        {orders.map((o) => (
                          <tr key={o.id} className="border-t border-slate-100 dark:border-slate-800">
                            <td className="px-3 py-1.5 tabular-nums text-slate-500">{(o.created_at || '').replace('T', ' ').slice(5, 16)}</td>
                            <td className="px-3 py-1.5">{o.stock_name || o.ts_code}</td>
                            <td className={`px-3 py-1.5 ${o.side === 'buy' ? 'text-rose-500' : 'text-emerald-500'}`}>
                              {o.side === 'buy' ? '买入' : '卖出'}
                            </td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{o.filled_qty || o.qty}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{fmtNum(o.filled_price ?? o.price, 2)}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{fmtNum(o.fee, 2)}</td>
                            <td className="px-3 py-1.5">
                              <Badge tone={o.status === 'filled' ? 'emerald' : o.status === 'pending' ? 'amber' : 'rose'}>
                                {o.status === 'filled' ? '已成交' : o.status === 'pending' ? '挂单中' : '已拒绝'}
                              </Badge>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </div>

            <Card className="h-fit">
              <SectionHeader title="下单" subtitle="市价单按实时价成交；限价单需指定价格" />
              <form onSubmit={placeOrder} className="mt-3 space-y-3">
                <div className="grid grid-cols-2 gap-1 rounded-lg bg-slate-100 p-1 dark:bg-slate-800">
                  <button
                    type="button"
                    onClick={() => setTicket((t) => ({ ...t, side: 'buy' }))}
                    className={`rounded-md py-1.5 text-xs font-medium ${
                      ticket.side === 'buy' ? 'bg-rose-500 text-white' : 'text-slate-500 dark:text-slate-400'
                    }`}
                  >
                    买入
                  </button>
                  <button
                    type="button"
                    onClick={() => setTicket((t) => ({ ...t, side: 'sell' }))}
                    className={`rounded-md py-1.5 text-xs font-medium ${
                      ticket.side === 'sell' ? 'bg-emerald-500 text-white' : 'text-slate-500 dark:text-slate-400'
                    }`}
                  >
                    卖出
                  </button>
                </div>

                <label className="block">
                  <span className="mb-1 block text-[11px] text-slate-500 dark:text-slate-400">股票代码</span>
                  <div className="flex gap-2">
                    <input
                      value={ticket.ts_code}
                      onChange={(e) => setTicket((t) => ({ ...t, ts_code: e.target.value }))}
                      onBlur={(e) => lookup(e.target.value)}
                      placeholder="600519"
                      className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                    />
                    <Button size="sm" variant="secondary" type="button" loading={quoteLoading} onClick={() => lookup(ticket.ts_code)}>
                      查价
                    </Button>
                  </div>
                </label>

                {quote ? (
                  <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs dark:bg-slate-800/60">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-slate-700 dark:text-slate-200">
                        {quote.name || quote.ts_code}
                      </span>
                      <span className={`tabular-nums font-semibold ${pctTone(quote.pct_chg)}`}>
                        {fmtNum(quote.price, 2)} {fmtPct(quote.pct_chg)}
                      </span>
                    </div>
                    <p className="mt-1 text-[10px] text-slate-400">
                      来源 {quote.source || 'realtime'} · {quote.status || 'snapshot'}
                      {quote.limit_up_price ? ` · 涨停 ${fmtNum(quote.limit_up_price, 2)}` : ''}
                    </p>
                  </div>
                ) : null}

                <div className="grid grid-cols-2 gap-2">
                  <label className="block">
                    <span className="mb-1 block text-[11px] text-slate-500 dark:text-slate-400">委托类型</span>
                    <Select
                      value={ticket.order_type}
                      onChange={(v) => setTicket((t) => ({ ...t, order_type: v }))}
                      options={[
                        { value: 'market', label: '市价单' },
                        { value: 'limit', label: '限价单' },
                      ]}
                      className="w-full py-1.5 text-sm"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-1 block text-[11px] text-slate-500 dark:text-slate-400">数量（股）</span>
                    <input
                      type="number"
                      step="100"
                      min="100"
                      value={ticket.qty}
                      onChange={(e) => setTicket((t) => ({ ...t, qty: e.target.value }))}
                      className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm tabular-nums dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                    />
                  </label>
                </div>

                {ticket.order_type === 'limit' ? (
                  <label className="block">
                    <span className="mb-1 block text-[11px] text-slate-500 dark:text-slate-400">限价（元）</span>
                    <input
                      type="number"
                      step="0.01"
                      value={ticket.price}
                      onChange={(e) => setTicket((t) => ({ ...t, price: e.target.value }))}
                      className="w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm tabular-nums dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                    />
                  </label>
                ) : null}

                <dl className="space-y-1 rounded-lg bg-slate-50 px-3 py-2 text-[11px] text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
                  <div className="flex justify-between">
                    <dt>预估成交额</dt>
                    <dd className="tabular-nums text-slate-700 dark:text-slate-200">{amount === null ? '—' : fmtAmount(amount)}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt>预估费用</dt>
                    <dd className="tabular-nums text-slate-700 dark:text-slate-200">{fee === null ? '—' : fmtNum(fee, 2)}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt>可用资金</dt>
                    <dd className="tabular-nums text-slate-700 dark:text-slate-200">{fmtAmount(s.cash)}</dd>
                  </div>
                </dl>

                <Button type="submit" loading={placing} className="w-full">
                  {ticket.side === 'buy' ? '买入' : '卖出'}
                </Button>
                <p className="text-[10px] leading-4 text-slate-400 dark:text-slate-500">
                  费率：佣金 0.03%（最低 5 元）+ 印花税 0.05%（仅卖出）+ 过户费 0.002%。买入按 100 股整数倍校验。
                </p>
              </form>
            </Card>
          </div>
        </div>
      )}
    </PageShell>
  )
}
