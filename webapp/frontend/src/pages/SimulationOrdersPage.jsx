/**
 * 模拟委托（/simulation/orders）：全部委托记录 + 挂单撤单。
 *
 * 撮合引擎是「即时成交」：市价单立即 filled，限价单在价格未触发时 pending，
 * 触发后由下一次查询/下单时的撮合逻辑成交。撤单只允许 pending。
 */
import { useEffect, useMemo, useState } from 'react'
import {
  AsyncBlock,
  Badge,
  Button,
  Card,
  EmptyState,
  PageShell,
  Pagination,
  SectionHeader,
  Select,
  StatTile,
  Tabs,
} from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtNum } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

const PAGE_SIZE = 20
const POLL_MS = 30_000
const STATUS_TABS = [
  { value: '', label: '全部' },
  { value: 'filled', label: '已成交' },
  { value: 'pending', label: '挂单中' },
  { value: 'rejected', label: '已拒绝' },
]

const STATUS_TEXT = { filled: '已成交', pending: '挂单中', rejected: '已拒绝', cancelled: '已撤单' }
const STATUS_TONE = { filled: 'emerald', pending: 'amber', rejected: 'rose', cancelled: 'slate' }

const num = (v) => {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

export default function SimulationOrdersPage() {
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')
  const [accountId, setAccountId] = useState('')
  const [busyId, setBusyId] = useState(null)

  const accountsQ = useApi(() => api.get('/simulation/accounts'), [])
  const accounts = accountsQ.data?.items || []

  useEffect(() => {
    if (!accountId && accounts.length) setAccountId(accounts[0].id)
  }, [accounts, accountId])

  const ordersQ = useApi(
    () => api.get('/simulation/orders', { account_id: accountId || undefined, page, size: PAGE_SIZE }),
    [accountId, page],
    { enabled: !!accountId },
  )
  const summaryQ = useApi(
    () => api.get('/simulation/summary', { account_id: accountId || undefined }),
    [accountId],
    { enabled: !!accountId },
  )

  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      ordersQ.reload()
      summaryQ.reload()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [ordersQ.reload, summaryQ.reload])

  const all = ordersQ.data?.items || []
  const rows = useMemo(() => (status ? all.filter((o) => o.status === status) : all), [all, status])
  const s = summaryQ.data || {}
  const account = accounts.find((a) => a.id === accountId)

  async function cancel(order) {
    setBusyId(order.id)
    try {
      await api.del(`/simulation/orders/${order.id}`)
      toast.success('委托已撤销')
      ordersQ.reload()
      summaryQ.reload()
    } catch (e) {
      toast.error('撤单失败', e.message)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <PageShell
      title="模拟委托"
      subtitle="市价单即时成交，限价单挂起等待触发；挂单可撤销"
      actions={
        <Select
          value={accountId}
          onChange={(v) => {
            setAccountId(v)
            setPage(1)
          }}
          options={accounts.map((a) => ({ value: a.id, label: a.name }))}
          className="min-w-32"
        />
      }
    >
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile label="委托总数" value={fmtNum(s.orders ?? 0, 0)} sub={account?.name || '—'} />
        <StatTile label="已成交" value={fmtNum(s.filled_orders ?? 0, 0)} sub="含历史累计" />
        <StatTile label="被拒绝" value={fmtNum(s.rejected_orders ?? 0, 0)} sub="资金/持仓/价格校验" />
        <StatTile label="可用资金" value={fmtAmount(s.cash)} sub={`总资产 ${fmtAmount(s.total_value)}`} />
      </div>

      {!accountId ? (
        <AsyncBlock loading={accountsQ.loading} error={accountsQ.error} onRetry={accountsQ.reload} loadingLabel="账户加载中…" />
      ) : !ordersQ.data ? (
        <AsyncBlock loading={ordersQ.loading} error={ordersQ.error} onRetry={ordersQ.reload} loadingLabel="委托记录加载中…" />
      ) : (
        <Card>
          <SectionHeader
            title="委托记录"
            subtitle="按创建时间倒序 · 成交价与费用由后端撮合引擎写回"
            badge={<Badge tone="blue">{rows.length} 条 / 本页</Badge>}
          />
          <div className="mt-3">
            <Tabs
              tabs={STATUS_TABS}
              value={status}
              onChange={(v) => {
                setStatus(v)
                setPage(1)
              }}
            />
          </div>

          {rows.length === 0 ? (
            <EmptyState text="暂无委托记录" hint="到「模拟账户」页下一笔市价单试试" />
          ) : (
            <div className="mt-3 overflow-auto rounded-lg border border-slate-200 dark:border-slate-800">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 text-[11px] text-slate-500 dark:bg-slate-900 dark:text-slate-400">
                  <tr>
                    <th className="px-3 py-2">时间</th>
                    <th className="px-3 py-2">标的</th>
                    <th className="px-3 py-2">方向</th>
                    <th className="px-3 py-2">类型</th>
                    <th className="px-3 py-2 text-right">委托价</th>
                    <th className="px-3 py-2 text-right">数量</th>
                    <th className="px-3 py-2 text-right">成交价</th>
                    <th className="px-3 py-2 text-right">费用</th>
                    <th className="px-3 py-2">状态</th>
                    <th className="px-3 py-2 text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((o) => (
                    <tr key={o.id} className="border-t border-slate-100 dark:border-slate-800">
                      <td className="px-3 py-1.5 tabular-nums text-slate-500">
                        {(o.created_at || '').replace('T', ' ').slice(5, 16)}
                      </td>
                      <td className="px-3 py-1.5">
                        <span className="text-slate-700 dark:text-slate-200">{o.stock_name || o.ts_code}</span>
                        <span className="ml-1 font-mono text-[10px] text-slate-400">{o.ts_code}</span>
                      </td>
                      <td className={`px-3 py-1.5 ${o.side === 'buy' ? 'text-rose-500' : 'text-emerald-500'}`}>
                        {o.side === 'buy' ? '买入' : '卖出'}
                      </td>
                      <td className="px-3 py-1.5 text-slate-500">{o.order_type === 'market' ? '市价' : '限价'}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{num(o.price) === null ? '—' : fmtNum(o.price, 2)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{o.qty}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{num(o.filled_price) === null ? '—' : fmtNum(o.filled_price, 2)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{fmtNum(o.fee, 2)}</td>
                      <td className="px-3 py-1.5">
                        <Badge tone={STATUS_TONE[o.status] || 'slate'}>{STATUS_TEXT[o.status] || o.status}</Badge>
                        {o.status === 'rejected' && o.reason ? (
                          <p className="mt-0.5 max-w-40 truncate text-[10px] text-rose-400" title={o.reason}>
                            {o.reason}
                          </p>
                        ) : null}
                      </td>
                      <td className="px-3 py-1.5 text-right">
                        {o.status === 'pending' ? (
                          <Button size="sm" variant="secondary" loading={busyId === o.id} onClick={() => cancel(o)}>
                            撤单
                          </Button>
                        ) : (
                          <span className="text-[10px] text-slate-300 dark:text-slate-600">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <Pagination page={page} pageSize={PAGE_SIZE} total={ordersQ.data.total || 0} onChange={setPage} />
        </Card>
      )}
    </PageShell>
  )
}
