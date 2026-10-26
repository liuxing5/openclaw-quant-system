/**
 * 实时异动（/anomaly）：全市场实时信号表。
 *
 * 对齐原站信息结构：板块 / 信号 / 方向 / 时段 / 只看持仓 五组筛选 + 固定列宽表格 +
 * 30 秒静默轮询 + 立即扫描。数据来自 `/anomaly/events`（腾讯全市场快照推导）。
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AsyncBlock, Badge, Button, Card, EmptyState, PageShell, Pagination, SectionHeader, Tabs } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { baseCode, fmtNum, fmtPct, fmtTime, pctTone } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

const POLL_MS = 30_000
const PAGE_SIZE = 50

const SIGNAL_OPTIONS = [
  { value: '', label: '全部信号' },
  { value: 'limit_up', label: '涨停' },
  { value: 'broken', label: '炸板' },
  { value: 'limit_down', label: '跌停' },
  { value: 'rapid_rise', label: '急拉' },
  { value: 'rapid_fall', label: '急跌' },
  { value: 'main_inflow', label: '主力流入' },
  { value: 'main_outflow', label: '主力流出' },
  { value: 'volume_spike', label: '量能异动' },
  { value: 'turnover_high', label: '高换手' },
  { value: 'new_high', label: '阶段新高' },
  { value: 'board_surge', label: '板块大涨' },
]

const KIND_META = {
  limit_up: { label: '涨停', tone: 'rose' },
  broken: { label: '炸板', tone: 'amber' },
  limit_down: { label: '跌停', tone: 'emerald' },
  rapid_rise: { label: '急拉', tone: 'rose' },
  rapid_fall: { label: '急跌', tone: 'emerald' },
  main_inflow: { label: '主力流入', tone: 'rose' },
  main_outflow: { label: '主力流出', tone: 'emerald' },
  volume_spike: { label: '量能异动', tone: 'amber' },
  turnover_high: { label: '高换手', tone: 'amber' },
  new_high: { label: '阶段新高', tone: 'blue' },
  board_surge: { label: '板块大涨', tone: 'rose' },
}

const BOARD_TABS = [
  { value: '', label: '全部' },
  { value: '主板', label: '主板' },
  { value: '创业板', label: '创业板' },
  { value: '科创板', label: '科创板' },
  { value: '北交所', label: '北交所' },
  { value: 'ST', label: 'ST' },
  { value: '板块', label: '板块' },
]
const DIRECTION_TABS = [
  { value: '', label: '全部方向' },
  { value: 'up', label: '上涨 / 流入' },
  { value: 'down', label: '下跌 / 流出' },
]
const SESSION_TABS = [
  { value: '', label: '全部' },
  { value: '竞价', label: '竞价' },
  { value: '盘中', label: '盘中' },
  { value: '偏移', label: '偏移' },
]

const num = (v) => (v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? null : Number(v))

function Row({ ev, index, onOpen }) {
  const meta = KIND_META[ev.kind] || { label: ev.kind_label || ev.kind, tone: 'slate' }
  const pct = num(ev.pct_chg)
  return (
    <tr
      className="cursor-pointer border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
      onClick={() => ev.scope !== 'board' && onOpen(ev.ts_code)}
    >
      <td className="px-2 py-1.5 text-center text-slate-400">{index}</td>
      <td className="px-2 py-1.5">
        <div className="flex items-center gap-1.5">
          <span className="font-medium text-slate-700 dark:text-slate-200">{ev.name || baseCode(ev.ts_code)}</span>
          <span className="font-mono text-[10px] text-slate-400">{ev.ts_code}</span>
          {ev.board && ev.board !== '板块' ? (
            <span className="rounded bg-slate-100 px-1 text-[9px] text-slate-500 dark:bg-slate-800 dark:text-slate-400">
              {ev.board === '主板' ? '主' : ev.board === '创业板' ? '创' : ev.board === '科创板' ? '科' : '北'}
            </span>
          ) : null}
        </div>
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums text-slate-700 dark:text-slate-200">
        {ev.price === null ? '—' : fmtNum(ev.price, 2)}
      </td>
      <td className={`px-2 py-1.5 text-right tabular-nums ${pctTone(pct)}`}>{fmtPct(pct)}</td>
      <td className="px-2 py-1.5">
        <Badge tone={meta.tone}>{meta.label}</Badge>
        <span className="ml-1 max-w-48 truncate text-[10px] text-slate-400" title={ev.detail}>
          {ev.detail}
        </span>
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{fmtNum(ev.volume_ratio, 2)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">
        {ev.amplitude === null || ev.amplitude === undefined ? '—' : `${fmtNum(ev.amplitude, 2)}%`}
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">
        {ev.turnover_rate === null || ev.turnover_rate === undefined ? '—' : `${fmtNum(ev.turnover_rate, 2)}%`}
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums text-slate-400">{fmtTime(ev.occurred_at)}</td>
    </tr>
  )
}

export default function AnomalyPage() {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const [signal, setSignal] = useState('')
  const [board, setBoard] = useState('')
  const [direction, setDirection] = useState('')
  const [session, setSession] = useState('')
  const [onlyHoldings, setOnlyHoldings] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [scannedAt, setScannedAt] = useState(null)

  const { data, loading, error, reload } = useApi(
    () =>
      api.get('/anomaly/events', {
        page,
        size: PAGE_SIZE,
        scope: 'all_market',
        signal: signal || undefined,
        board: board || undefined,
        direction: direction || undefined,
        session: session || undefined,
        only_holdings: onlyHoldings ? 1 : undefined,
      }),
    [page, signal, board, direction, session, onlyHoldings],
  )

  useEffect(() => {
    const id = setInterval(() => {
      if (!document.hidden) reload()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [reload])

  const items = data?.items || []
  const live = !!data?.meta?.live
  const lastScan = scannedAt || data?.last_scan_at

  const scan = async () => {
    setScanning(true)
    try {
      const r = await api.post('/anomaly/events/scan', { scope: 'all_market' })
      toast.success('扫描完成', `扫描 ${r?.scanned ?? 0} 条，新增 ${r?.new ?? 0} 条`)
      setScannedAt(new Date().toISOString())
      reload()
    } catch (e) {
      toast.error('扫描失败', e.message)
    } finally {
      setScanning(false)
    }
  }

  const active = useMemo(() => {
    const parts = []
    if (signal) parts.push(SIGNAL_OPTIONS.find((x) => x.value === signal)?.label)
    if (board) parts.push(board)
    if (direction === 'up') parts.push('上涨')
    if (direction === 'down') parts.push('下跌')
    if (session) parts.push(session)
    if (onlyHoldings) parts.push('只看持仓')
    return parts.join(' · ')
  }, [signal, board, direction, session, onlyHoldings])

  return (
    <PageShell
      title="实时异动"
      subtitle={
        live
          ? `全市场实时快照 · ${fmtTime(data?.as_of || data?.meta?.as_of)} 更新 · 共 ${data?.total ?? 0} 条 · 30 秒刷新`
          : `最近交易日快照 · 共 ${data?.total ?? 0} 条 · 30 秒刷新`
      }
      actions={
        <div className="flex items-center gap-2 text-[11px] text-slate-400">
          <span>
            最近成功扫描 <b className="tabular-nums text-slate-600 dark:text-slate-300">{lastScan ? fmtTime(lastScan) : '—'}</b>
          </span>
          <Button size="sm" loading={scanning} onClick={scan}>
            立即扫描
          </Button>
        </div>
      }
    >
      <Card className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <Tabs tabs={BOARD_TABS} value={board} onChange={(v) => { setBoard(v); setPage(1) }} />
          <select
            value={signal}
            onChange={(e) => { setSignal(e.target.value); setPage(1) }}
            className="rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
          >
            {SIGNAL_OPTIONS.map((x) => (
              <option key={x.value} value={x.value}>{x.label}</option>
            ))}
          </select>
          <Tabs tabs={DIRECTION_TABS} value={direction} onChange={(v) => { setDirection(v); setPage(1) }} />
          <Tabs tabs={SESSION_TABS} value={session} onChange={(v) => { setSession(v); setPage(1) }} />
          <label className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
            <input type="checkbox" checked={onlyHoldings} onChange={(e) => { setOnlyHoldings(e.target.checked); setPage(1) }} />
            只看持仓股
          </label>
          {active ? <span className="text-[11px] text-brand-500">{active}</span> : null}
        </div>
        <p className="text-[11px] text-slate-400">按发生时间倒序 · 每页 {PAGE_SIZE} 条 · 点击个股行进入 AI 量价推演</p>
      </Card>

      {!data ? (
        <AsyncBlock loading={loading} error={error} onRetry={reload} loadingLabel="异动事件加载中" />
      ) : items.length === 0 ? (
        <Card>
          <EmptyState text="当前筛选下没有异动" hint="可放宽板块 / 信号 / 方向筛选，或点击「立即扫描」重算" />
        </Card>
      ) : (
        <Card>
          <SectionHeader
            title="异动信号"
            subtitle={`${items.length} 条 / 本页 · 固定高度表格`}
            badge={<Badge tone={live ? 'rose' : 'slate'}>{live ? '实时' : 'T-1'}</Badge>}
          />
          <div className="mt-2 max-h-[70vh] overflow-auto rounded-lg border border-slate-200 dark:border-slate-800">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 z-10 bg-slate-50 text-[11px] text-slate-500 dark:bg-slate-900 dark:text-slate-400">
                <tr>
                  <th className="px-2 py-2 text-center">#</th>
                  <th className="px-2 py-2">代码 / 名称</th>
                  <th className="px-2 py-2 text-right">现价</th>
                  <th className="px-2 py-2 text-right">今日</th>
                  <th className="px-2 py-2">信号</th>
                  <th className="px-2 py-2 text-right">量比</th>
                  <th className="px-2 py-2 text-right">振幅</th>
                  <th className="px-2 py-2 text-right">换手</th>
                  <th className="px-2 py-2 text-right">时间</th>
                </tr>
              </thead>
              <tbody>
                {items.map((ev, i) => (
                  <Row key={ev.id} ev={ev} index={(page - 1) * PAGE_SIZE + i + 1} onOpen={(code) => navigate(`/stock-analysis/${baseCode(code)}`)} />
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={page} pageSize={PAGE_SIZE} total={data.total || 0} onChange={setPage} />
        </Card>
      )}
    </PageShell>
  )
}
