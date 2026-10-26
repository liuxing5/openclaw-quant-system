/**
 * 游资龙虎榜（/dragon-tiger）：
 *
 * 顶部磁贴（上榜家数 / 总净买额 / 机构买入）+ 两个 Tab：
 *  - 榜单明细：服务端分页 + 关键词防抖搜索 + 日期筛选，净买额红涨绿跌着色；
 *  - 席位排行：营业部聚合的买入/卖出/净额横向条形图 + 表格。
 *
 * 字段归一化的原因：明细/席位接口由并行后端单元实现，`is_inst`、买卖金额等
 * 可能存在别名（如 net_buy_amount / net_amount），统一走 pick 兜底，避免展示为空。
 */
import React, { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Chart, { DOWN, TIP, UP } from '../components/Chart.jsx'
import { AsyncBlock, Badge, Button, Card, PageShell, Pagination, SectionHeader, StatTile, Tabs } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtDate, fmtNum, fmtPct, pctTone } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

const PAGE_SIZE = 20
const SEAT_PAGE_SIZE = 20

const num = (v) => (v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? null : Number(v))

const pick = (o, keys) => {
  if (!o) return null
  for (const k of keys) {
    if (o[k] !== undefined && o[k] !== null && o[k] !== '') return o[k]
  }
  return null
}

const asItems = (d) => (Array.isArray(d) ? d : (d && d.items) || [])

/** 机构标识可能是 is_inst 布尔/0-1，也可能是席位名列表里带「机构」 */
const isInstRow = (r) => {
  const direct = r.is_inst ?? r.is_institution ?? r.inst
  if (direct !== null && direct !== undefined) {
    if (typeof direct === 'string') return direct === '1' || direct.toLowerCase() === 'true' || direct.includes('机构')
    return Boolean(direct)
  }
  const seats = r.hot_money_names || r.seats || []
  return Array.isArray(seats) && seats.some((s) => String(s).includes('机构'))
}

const rowOf = (r) => ({
  id: r.id || `${pick(r, ['ts_code', 'stock_code'])}-${pick(r, ['trade_date', 'date'])}`,
  date: pick(r, ['trade_date', 'date']),
  tsCode: pick(r, ['ts_code', 'stock_code', 'symbol', 'code']),
  name: pick(r, ['stock_name', 'name']),
  reason: pick(r, ['listing_reason', 'reason', 'reason_text']),
  buy: num(pick(r, ['buy_amount', 'amount_buy', 'total_buy'])),
  sell: num(pick(r, ['sell_amount', 'amount_sell', 'total_sell'])),
  net: num(pick(r, ['net_amount', 'net_buy_amount', 'net_buy', 'net_inflow'])),
  pct: num(pick(r, ['change_pct', 'pct_chg'])),
  isInst: isInstRow(r),
})

const seatOf = (s) => ({
  name: pick(s, ['seat_name', 'name', 'branch_name', 'seat']) || '未知席位',
  buy: num(pick(s, ['buy_amount', 'total_buy'])),
  sell: num(pick(s, ['sell_amount', 'total_sell'])),
  net: num(pick(s, ['net_amount', 'net_buy_amount', 'net'])),
  count: num(pick(s, ['stock_count', 'count', 'times', 'appear_times'])),
})

/* ------------------------------------------------------------ 明细列表 */

function DetailTable({ rows, navigate }) {
  return (
    <div className="mt-3 overflow-x-auto thin-scroll">
      <table className="w-full min-w-[900px] text-xs">
        <thead>
          <tr className="text-left text-[11px] text-slate-400">
            <th className="pb-1.5 font-medium">日期</th>
            <th className="pb-1.5 font-medium">代码</th>
            <th className="pb-1.5 font-medium">名称</th>
            <th className="pb-1.5 font-medium">上榜原因</th>
            <th className="pb-1.5 text-right font-medium">买入额</th>
            <th className="pb-1.5 text-right font-medium">卖出额</th>
            <th className="pb-1.5 text-right font-medium">净买额</th>
            <th className="pb-1.5 text-right font-medium">涨跌幅</th>
            <th className="pb-1.5 text-right font-medium">席位属性</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.id}
              onClick={() => navigate(`/stock-analysis/${String(r.tsCode || '').split('.')[0]}`)}
              className="cursor-pointer border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/60"
            >
              <td className="py-2 tabular-nums text-slate-500">{r.date || '—'}</td>
              <td className="py-2 font-mono tabular-nums text-slate-500">{r.tsCode || '—'}</td>
              <td className="py-2 font-medium text-slate-700 dark:text-slate-200">{r.name || '—'}</td>
              <td className="max-w-[260px] py-2 text-slate-500 dark:text-slate-400">
                <span className="line-clamp-2" title={r.reason || ''}>
                  {r.reason || '—'}
                </span>
              </td>
              <td className="py-2 text-right tabular-nums text-slate-600 dark:text-slate-300">{fmtAmount(r.buy)}</td>
              <td className="py-2 text-right tabular-nums text-slate-600 dark:text-slate-300">{fmtAmount(r.sell)}</td>
              <td className={`py-2 text-right font-medium tabular-nums ${pctTone(r.net)}`}>{fmtAmount(r.net)}</td>
              <td className={`py-2 text-right tabular-nums ${pctTone(r.pct)}`}>{fmtPct(r.pct)}</td>
              <td className="py-2 text-right">
                {r.isInst ? (
                  <Badge tone="blue">机构专用</Badge>
                ) : (
                  <span className="text-[11px] text-slate-400">游资席位</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ------------------------------------------------------------ 席位排行 */

function SeatPanel({ seats }) {
  const [page, setPage] = useState(1)

  const chartRows = useMemo(() => {
    const sorted = [...seats].sort((a, b) => Math.abs(b.net || 0) - Math.abs(a.net || 0)).slice(0, 10)
    return sorted.reverse() // ECharts 类目轴自下而上，反转后第一名在最上
  }, [seats])

  const option = useMemo(
    () => ({
      tooltip: { ...TIP, trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: {
        data: ['买入额', '卖出额', '净额'],
        top: 0,
        right: 4,
        textStyle: { color: '#94a3b8', fontSize: 11 },
        itemWidth: 12,
        itemHeight: 8,
      },
      grid: { left: 8, right: 24, top: 28, bottom: 8, containLabel: true },
      xAxis: {
        type: 'value',
        axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v) => fmtAmount(v) },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } },
      },
      yAxis: {
        type: 'category',
        data: chartRows.map((s) => s.name),
        axisLabel: { color: '#94a3b8', fontSize: 10, width: 150, overflow: 'truncate' },
        axisLine: { lineStyle: { color: 'rgba(148,163,184,0.35)' } },
      },
      series: [
        {
          name: '买入额',
          type: 'bar',
          data: chartRows.map((s) => s.buy || 0),
          itemStyle: { color: 'rgba(99,102,241,0.7)' },
          barMaxWidth: 9,
        },
        {
          name: '卖出额',
          type: 'bar',
          data: chartRows.map((s) => s.sell || 0),
          itemStyle: { color: 'rgba(148,163,184,0.6)' },
          barMaxWidth: 9,
        },
        {
          name: '净额',
          type: 'bar',
          data: chartRows.map((s) => ({
            value: s.net || 0,
            itemStyle: { color: (s.net || 0) >= 0 ? UP : DOWN },
          })),
          barMaxWidth: 9,
        },
      ],
    }),
    [chartRows],
  )

  const paged = seats.slice((page - 1) * SEAT_PAGE_SIZE, page * SEAT_PAGE_SIZE)

  return (
    <div className="space-y-3">
      <Chart option={option} height={320} />
      <div className="overflow-x-auto thin-scroll">
        <table className="w-full min-w-[640px] text-xs">
          <thead>
            <tr className="text-left text-[11px] text-slate-400">
              <th className="pb-1.5 font-medium">#</th>
              <th className="pb-1.5 font-medium">营业部 / 席位</th>
              <th className="pb-1.5 text-right font-medium">买入额</th>
              <th className="pb-1.5 text-right font-medium">卖出额</th>
              <th className="pb-1.5 text-right font-medium">净额</th>
              <th className="pb-1.5 text-right font-medium">上榜个股</th>
            </tr>
          </thead>
          <tbody>
            {paged.map((s, i) => (
              <tr key={`${s.name}-${i}`} className="border-t border-slate-100 dark:border-slate-800">
                <td className="py-2 tabular-nums text-slate-400">{(page - 1) * SEAT_PAGE_SIZE + i + 1}</td>
                <td className="py-2 font-medium text-slate-700 dark:text-slate-200">{s.name}</td>
                <td className="py-2 text-right tabular-nums text-slate-600 dark:text-slate-300">{fmtAmount(s.buy)}</td>
                <td className="py-2 text-right tabular-nums text-slate-600 dark:text-slate-300">{fmtAmount(s.sell)}</td>
                <td className={`py-2 text-right font-medium tabular-nums ${pctTone(s.net)}`}>{fmtAmount(s.net)}</td>
                <td className="py-2 text-right tabular-nums text-slate-500">{fmtNum(s.count, 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination page={page} pageSize={SEAT_PAGE_SIZE} total={seats.length} onChange={setPage} />
    </div>
  )
}

/* ---------------------------------------------------------------- 页面 */

export default function DragonTigerPage() {
  const navigate = useNavigate()
  const [tab, setTab] = useState('detail')
  const [page, setPage] = useState(1)
  const [date, setDate] = useState('')
  const [kwInput, setKwInput] = useState('')
  const [kw, setKw] = useState('')
  const [reloadKey, setReloadKey] = useState(0)

  // 关键词防抖 300ms，避免每敲一个字都翻页请求
  useEffect(() => {
    const id = setTimeout(() => setKw(kwInput.trim()), 300)
    return () => clearTimeout(id)
  }, [kwInput])
  useEffect(() => setPage(1), [kw, date])

  const listQ = useApi(
    () => api.get('/dragon-tiger', { page, size: PAGE_SIZE, date, keyword: kw }),
    [page, date, kw, reloadKey],
  )
  const seatQ = useApi(() => api.get('/dragon-tiger/seat-rank', { days: 20 }), [reloadKey], {
    enabled: tab === 'seat',
  })

  const data = listQ.data || {}
  const rows = useMemo(() => asItems(data).map(rowOf), [data])
  const total = num(data.total) ?? rows.length
  const seats = useMemo(() => asItems(seatQ.data).map(seatOf), [seatQ.data])

  const netSum = rows.reduce((acc, r) => acc + (r.net || 0), 0)
  const instNet = rows.reduce((acc, r) => acc + (r.isInst ? r.net || 0 : 0), 0)
  const hasServerAgg = data.total_net_buy !== undefined || data.inst_net_buy !== undefined

  const refresh = () => {
    setReloadKey((k) => k + 1)
    toast.info('正在刷新', '重新拉取龙虎榜与席位数据')
  }

  const tiles = [
    { label: '上榜家数', value: fmtNum(total, 0), sub: date ? `${date} 全部记录` : '当前筛选全部记录' },
    {
      label: '总净买额',
      value: fmtAmount(num(data.total_net_buy) ?? netSum),
      sub: hasServerAgg ? `交易日 ${data.trade_date || '—'}` : '按当前页汇总',
      tone: pctTone(num(data.total_net_buy) ?? netSum),
    },
    {
      label: '机构买入',
      value: fmtAmount(num(data.inst_net_buy) ?? instNet),
      sub: hasServerAgg ? '机构专用席位合计' : '按当前页机构席位汇总',
      tone: pctTone(num(data.inst_net_buy) ?? instNet),
    },
  ]

  return (
    <PageShell
      title="游资龙虎榜"
      subtitle={`跟庄资金 · 每日席位解码${data.trade_date ? ` · 交易日 ${fmtDate(data.trade_date)}` : ''}`}
      actions={
        <Button variant="secondary" size="sm" onClick={refresh}>
          刷新
        </Button>
      }
    >
      <div className="space-y-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {tiles.map((t) => (
            <StatTile key={t.label} label={t.label} value={t.value} sub={t.sub} tone={t.tone} />
          ))}
        </div>

        <Card>
          <SectionHeader
            title="榜单数据"
            subtitle="明细支持关键词与日期筛选；席位排行按近 20 个交易日聚合"
            action={
              <Tabs
                tabs={[
                  { value: 'detail', label: '榜单明细' },
                  { value: 'seat', label: '席位排行' },
                ]}
                value={tab}
                onChange={setTab}
              />
            }
          />

          {tab === 'detail' ? (
            <>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <div className="relative">
                  <input
                    value={kwInput}
                    onChange={(e) => setKwInput(e.target.value)}
                    placeholder="搜索代码 / 名称 / 上榜原因"
                    aria-label="关键词搜索"
                    className="w-56 rounded-lg border border-slate-300 bg-white py-1.5 pl-7 pr-2 text-xs text-slate-700 outline-none focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                  />
                  <svg
                    className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-slate-400"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    aria-hidden="true"
                  >
                    <circle cx="11" cy="11" r="7" />
                    <path d="m20 20-3.5-3.5" strokeLinecap="round" />
                  </svg>
                </div>
                <input
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  aria-label="按日期筛选"
                  className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-700 outline-none focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                />
                {date || kw ? (
                  <Button variant="ghost" size="sm" onClick={() => { setDate(''); setKwInput(''); setKw('') }}>
                    清除筛选
                  </Button>
                ) : null}
              </div>

              <AsyncBlock
                loading={listQ.loading && !listQ.data}
                error={listQ.error}
                onRetry={listQ.reload}
                loadingLabel="龙虎榜加载中"
                empty={!listQ.loading && !listQ.error && rows.length === 0}
                emptyText="当前筛选没有上榜记录"
                emptyHint="换个日期或清空关键词试试"
              >
                <DetailTable rows={rows} navigate={navigate} />
                <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
              </AsyncBlock>
            </>
          ) : (
            <AsyncBlock
              loading={seatQ.loading && !seatQ.data}
              error={seatQ.error}
              onRetry={seatQ.reload}
              loadingLabel="席位排行加载中"
              empty={!seatQ.loading && !seatQ.error && seats.length === 0}
              emptyText="近 20 个交易日暂无席位聚合数据"
            >
              <div className="mt-3">
                <SeatPanel seats={seats} />
              </div>
            </AsyncBlock>
          )}
        </Card>
      </div>
    </PageShell>
  )
}
