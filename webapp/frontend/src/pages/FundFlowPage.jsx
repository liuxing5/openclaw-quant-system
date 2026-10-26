/**
 * 资金流向（/info/fund-flow）：
 *
 * 顶部 Tabs 切行业/概念 → 上半部「净流入 TOP / 净流出 TOP」双列表 →
 * 下半部 TOP10 净流入横向条形图 + 全量分页表格。
 *
 * 为什么列要动态判断：行业表可能带成交额/家数，概念表没有 amount/up_count/down_count，
 * 所以每一列都按「当前数据里是否真的存在该字段」决定显隐，缺字段不硬凑空列。
 */
import React, { useMemo, useState } from 'react'
import Chart, { TIP, UP } from '../components/Chart.jsx'
import { AsyncBlock, Badge, Card, PageShell, Pagination, SectionHeader, StatTile, Tabs } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtDate, fmtNum, fmtPct, pctTone } from '../lib/format.js'

const PAGE_SIZE = 15

const num = (v) => (v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? null : Number(v))

const pick = (o, keys) => {
  if (!o) return null
  for (const k of keys) {
    if (o[k] !== undefined && o[k] !== null && o[k] !== '') return o[k]
  }
  return null
}

const asItems = (d) => (Array.isArray(d) ? d : (d && d.items) || [])

const rowOf = (r, i) => ({
  code: pick(r, ['sector_code', 'code', 'board_code']) || i,
  name: pick(r, ['sector_name', 'name']) || '—',
  net: num(pick(r, ['net_inflow', 'net_amount', 'amount'])),
  pct: num(pick(r, ['pct_chg', 'change_pct'])),
  inflow: num(pick(r, ['inflow_amount', 'main_inflow', 'inflow'])),
  outflow: num(pick(r, ['outflow_amount', 'main_outflow', 'outflow'])),
  turnover: num(pick(r, ['amount', 'turnover', 'turnover_amount'])),
  count: num(pick(r, ['company_count', 'stock_count', 'count'])),
  upCount: num(pick(r, ['up_count', 'rise_count'])),
  downCount: num(pick(r, ['down_count', 'fall_count'])),
  leader: pick(r, ['leader_name', 'lead_stock', 'leader']),
  leaderPct: num(pick(r, ['leader_pct_chg', 'leader_pct', 'lead_pct_chg'])),
  rank: num(pick(r, ['rank_no', 'rank'])) ?? i + 1,
})

/* --------------------------------------------------------------- TOP 双列 */

function TopList({ title, tone, rows, onPick }) {
  return (
    <Card>
      <SectionHeader
        title={title}
        subtitle="按净流入金额绝对值排序"
        badge={<Badge tone={tone === 'up' ? 'rose' : 'emerald'}>{rows.length} 个板块</Badge>}
      />
      <ul className="mt-3 space-y-1">
        {rows.length === 0 ? (
          <li className="rounded-lg border border-dashed border-slate-200 px-3 py-6 text-center text-xs text-slate-400 dark:border-slate-700">
            当前分类暂无{tone === 'up' ? '净流入' : '净流出'}板块
          </li>
        ) : (
          rows.map((r, i) => (
            <li
              key={r.code}
              className="flex items-center justify-between gap-2 rounded-md px-1.5 py-1.5 transition hover:bg-slate-50 dark:hover:bg-slate-800/70"
            >
              <button
                type="button"
                onClick={() => onPick && onPick(r)}
                className="flex min-w-0 flex-1 items-center gap-2 text-left"
                title={`${r.name} · 涨跌 ${fmtPct(r.pct)}`}
              >
                <span className={`w-5 shrink-0 text-right text-xs tabular-nums ${tone === 'up' ? 'text-rose-500' : 'text-emerald-500'}`}>
                  {i + 1}
                </span>
                <span className="min-w-0 truncate text-xs font-medium text-slate-700 dark:text-slate-200">{r.name}</span>
                <span className={`shrink-0 text-[11px] tabular-nums ${pctTone(r.pct)}`}>{fmtPct(r.pct)}</span>
              </button>
              <span
                className={`shrink-0 text-xs font-semibold tabular-nums ${tone === 'up' ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400'}`}
              >
                {fmtAmount(r.net)}
              </span>
            </li>
          ))
        )}
      </ul>
    </Card>
  )
}

/* ------------------------------------------------------------- TOP10 条形图 */

function TopInflowChart({ rows }) {
  const chartRows = useMemo(() => [...rows].reverse(), [rows]) // 反转让第 1 名在最上

  const option = useMemo(
    () => ({
      tooltip: {
        ...TIP,
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (ps) => {
          const p = Array.isArray(ps) ? ps[0] : ps
          return `${p.axisValue}<br/>${p.marker}净流入 <b>${fmtAmount(p.value)}</b>`
        },
      },
      grid: { left: 8, right: 40, top: 12, bottom: 8, containLabel: true },
      xAxis: {
        type: 'value',
        axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v) => fmtAmount(v) },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } },
      },
      yAxis: {
        type: 'category',
        data: chartRows.map((r) => r.name),
        axisLabel: { color: '#94a3b8', fontSize: 11, width: 130, overflow: 'truncate' },
        axisLine: { lineStyle: { color: 'rgba(148,163,184,0.35)' } },
      },
      series: [
        {
          name: '净流入',
          type: 'bar',
          data: chartRows.map((r) => r.net || 0),
          itemStyle: { color: UP, borderRadius: [0, 3, 3, 0] },
          barMaxWidth: 16,
          label: {
            show: true,
            position: 'right',
            fontSize: 10,
            color: '#94a3b8',
            formatter: (p) => fmtAmount(p.value),
          },
        },
      ],
    }),
    [chartRows],
  )

  return <Chart option={option} height={Math.max(240, chartRows.length * 30)} className="mt-2" />
}

/* ------------------------------------------------------------------ 页面 */

export default function FundFlowPage() {
  const [type, setType] = useState('industry')
  const [page, setPage] = useState(1)

  const flowQ = useApi(() => api.get('/info/fund-flow/sectors', { sector_type: type }), [type])

  const data = flowQ.data || {}
  const rows = useMemo(() => asItems(data).map(rowOf), [data])

  const inflowTop = useMemo(
    () => rows.filter((r) => (r.net || 0) > 0).sort((a, b) => b.net - a.net).slice(0, 10),
    [rows],
  )
  const outflowTop = useMemo(
    () => rows.filter((r) => (r.net || 0) < 0).sort((a, b) => a.net - b.net).slice(0, 10),
    [rows],
  )

  const sumIn = rows.filter((r) => (r.net || 0) > 0).reduce((a, r) => a + r.net, 0)
  const sumOut = rows.filter((r) => (r.net || 0) < 0).reduce((a, r) => a + r.net, 0)

  // 概念表缺这些字段 → 对应列整列隐藏，而不是渲染一排空单元格
  const cols = {
    turnover: rows.some((r) => r.turnover !== null),
    flow: rows.some((r) => r.inflow !== null || r.outflow !== null),
    count: rows.some((r) => r.count !== null || r.upCount !== null || r.downCount !== null),
    leader: rows.some((r) => r.leader),
  }

  const paged = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  const countText = (r) => {
    if (r.upCount !== null || r.downCount !== null) {
      return (
        <span className="tabular-nums">
          <span className="text-rose-600 dark:text-rose-400">{fmtNum(r.upCount, 0)}</span>
          <span className="text-slate-400"> / </span>
          <span className="text-emerald-600 dark:text-emerald-400">{fmtNum(r.downCount, 0)}</span>
        </span>
      )
    }
    return <span className="tabular-nums text-slate-500">{fmtNum(r.count, 0)}</span>
  }

  return (
    <PageShell
      title="资金流向"
      subtitle={`行业与概念板块主力资金多空分布${data.trade_date ? ` · 数据日 ${fmtDate(data.trade_date)}` : ''}${
        data.updated_at ? ` · 更新于 ${data.updated_at.replace('T', ' ').slice(0, 16)}` : ''
      }`}
      actions={
        <Tabs
          tabs={[
            { value: 'industry', label: '行业资金' },
            { value: 'concept', label: '概念资金' },
          ]}
          value={type}
          onChange={(v) => {
            setType(v)
            setPage(1)
          }}
        />
      }
    >
      <AsyncBlock
        loading={flowQ.loading && !flowQ.data}
        error={flowQ.error}
        onRetry={flowQ.reload}
        loadingLabel="板块资金流加载中"
        empty={!flowQ.loading && !flowQ.error && rows.length === 0}
        emptyText={type === 'industry' ? '暂无行业资金流数据' : '暂无概念资金流数据'}
        emptyHint="收盘后同步板块资金数据即可展示"
      >
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatTile label="板块数" value={fmtNum(rows.length, 0)} sub={type === 'industry' ? '行业分类' : '概念分类'} />
            <StatTile
              label="净流入合计"
              value={fmtAmount(sumIn)}
              tone="text-rose-600 dark:text-rose-400"
              sub="仅统计流入板块"
            />
            <StatTile
              label="净流出合计"
              value={fmtAmount(sumOut)}
              tone="text-emerald-600 dark:text-emerald-400"
              sub="仅统计流出板块"
            />
            <StatTile
              label="板块净额"
              value={fmtAmount(sumIn + sumOut)}
              tone={pctTone(sumIn + sumOut)}
              sub="流入 − 流出"
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <TopList title="净流入 TOP 10" tone="up" rows={inflowTop} />
            <TopList title="净流出 TOP 10" tone="down" rows={outflowTop} />
          </div>

          <div className="grid gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-1">
              <SectionHeader title="TOP10 净流入" subtitle="横向条形图 · 单位为当日主力净额" />
              <TopInflowChart rows={inflowTop} />
            </Card>

            <Card className="xl:col-span-2">
              <SectionHeader
                title="全量板块明细"
                subtitle={`${type === 'industry' ? '行业' : '概念'} · 共 ${rows.length} 个，按净流入排序`}
                badge={<Badge tone="slate">第 {page} 页</Badge>}
              />
              <div className="mt-3 overflow-x-auto thin-scroll">
                <table className="w-full min-w-[720px] text-xs">
                  <thead>
                    <tr className="text-left text-[11px] text-slate-400">
                      <th className="pb-1.5 font-medium">#</th>
                      <th className="pb-1.5 font-medium">板块</th>
                      <th className="pb-1.5 text-right font-medium">涨跌幅</th>
                      <th className="pb-1.5 text-right font-medium">净流入</th>
                      {cols.turnover ? <th className="pb-1.5 text-right font-medium">成交额</th> : null}
                      {cols.flow ? (
                        <>
                          <th className="pb-1.5 text-right font-medium">流入</th>
                          <th className="pb-1.5 text-right font-medium">流出</th>
                        </>
                      ) : null}
                      {cols.count ? <th className="pb-1.5 text-right font-medium">涨/跌家数</th> : null}
                      {cols.leader ? (
                        <>
                          <th className="pb-1.5 font-medium">领涨股</th>
                          <th className="pb-1.5 text-right font-medium">领涨幅</th>
                        </>
                      ) : null}
                    </tr>
                  </thead>
                  <tbody>
                    {paged.map((r, i) => (
                      <tr key={r.code} className="border-t border-slate-100 dark:border-slate-800">
                        <td className="py-2 tabular-nums text-slate-400">{(page - 1) * PAGE_SIZE + i + 1}</td>
                        <td className="py-2 font-medium text-slate-700 dark:text-slate-200">{r.name}</td>
                        <td className={`py-2 text-right tabular-nums ${pctTone(r.pct)}`}>{fmtPct(r.pct)}</td>
                        <td
                          className={`py-2 text-right font-medium tabular-nums ${
                            (r.net || 0) >= 0 ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400'
                          }`}
                        >
                          {fmtAmount(r.net)}
                        </td>
                        {cols.turnover ? (
                          <td className="py-2 text-right tabular-nums text-slate-500">{fmtAmount(r.turnover)}</td>
                        ) : null}
                        {cols.flow ? (
                          <>
                            <td className="py-2 text-right tabular-nums text-slate-500">{fmtAmount(r.inflow)}</td>
                            <td className="py-2 text-right tabular-nums text-slate-500">{fmtAmount(r.outflow)}</td>
                          </>
                        ) : null}
                        {cols.count ? <td className="py-2 text-right">{countText(r)}</td> : null}
                        {cols.leader ? (
                          <>
                            <td className="py-2 text-slate-600 dark:text-slate-300">{r.leader || '—'}</td>
                            <td className={`py-2 text-right tabular-nums ${pctTone(r.leaderPct)}`}>{fmtPct(r.leaderPct)}</td>
                          </>
                        ) : null}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination page={page} pageSize={PAGE_SIZE} total={rows.length} onChange={setPage} />
            </Card>
          </div>
        </div>
      </AsyncBlock>
    </PageShell>
  )
}
