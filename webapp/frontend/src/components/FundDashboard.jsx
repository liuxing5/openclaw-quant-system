import React, { useMemo, useState } from 'react'
import Chart, { DOWN, FLAT, TIP, UP, UP_SOFT } from './Chart.jsx'
import { Badge, Card, SectionHeader, Tabs } from './ui.jsx'
import { fmtCompact, fmtPct, pctTone } from '../lib/format.js'

const num = (v) => {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

const tone = (v) => (v > 0 ? 'text-rose-500' : v < 0 ? 'text-emerald-500' : 'text-slate-400')
const sign = (v) => (v > 0 ? '+' : '')

function Metric({ label, value, sub, tone: force }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 dark:border-slate-800 dark:bg-slate-900/60">
      <p className="text-[10px] text-slate-400">{label}</p>
      <p className={`mt-1 text-lg font-semibold tabular-nums ${force || tone(value)}`}>{sign(value)}{fmtCompact(value)}</p>
      <p className="mt-0.5 text-[10px] text-slate-400">{sub}</p>
    </div>
  )
}

function FlowRows({ rows, side, onPick }) {
  const max = Math.max(1, ...rows.map((r) => Math.abs(num(r.main_net))))
  if (!rows.length) return <p className="py-4 text-center text-xs text-slate-400">暂无数据</p>
  return (
    <ul className="space-y-1.5">
      {rows.map((r, i) => (
        <li key={`${r.code}-${i}`}>
          <button type="button" className="group block w-full text-left">
            <div className="flex items-center justify-between gap-2">
              <span className="flex min-w-0 items-center gap-1.5">
                <span className="w-4 shrink-0 text-[10px] tabular-nums text-slate-400">{i + 1}</span>
                <span className="truncate text-xs font-medium text-slate-700 group-hover:text-brand-600 dark:text-slate-200">{r.name}</span>
                <span className={`shrink-0 text-[10px] tabular-nums ${pctTone(num(r.pct_chg))}`}>{fmtPct(num(r.pct_chg))}</span>
              </span>
              <span className={`shrink-0 text-xs font-semibold tabular-nums ${side === 'in' ? 'text-rose-500' : 'text-emerald-500'}`}>
                {sign(num(r.main_net))}{fmtCompact(num(r.main_net))}
              </span>
            </div>
            <div className="mt-0.5 h-1 overflow-hidden rounded bg-slate-100 dark:bg-slate-800">
              <div
                className={`h-full rounded ${side === 'in' ? 'bg-rose-400' : 'bg-emerald-400'}`}
                style={{ width: `${Math.max(4, (Math.abs(num(r.main_net)) / max) * 100)}%` }}
              />
            </div>
          </button>
        </li>
      ))}
    </ul>
  )
}

function StockRows({ rows, side, onOpen }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-[10px] text-slate-400">
          <tr>
            <th className="px-1 py-1 text-left">个股</th>
            <th className="px-1 py-1 text-right">涨跌</th>
            <th className="px-1 py-1 text-right">主力净额</th>
            <th className="px-1 py-1 text-right">超大单</th>
            <th className="px-1 py-1 text-right">净占比</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 8).map((r) => (
            <tr
              key={r.ts_code}
              className="cursor-pointer border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
              onClick={() => onOpen?.(r.ts_code)}
            >
              <td className="max-w-[120px] truncate px-1 py-1.5 text-slate-700 dark:text-slate-200">{r.name || r.ts_code}</td>
              <td className={`px-1 py-1.5 text-right tabular-nums ${pctTone(num(r.pct_chg))}`}>{fmtPct(num(r.pct_chg))}</td>
              <td className={`px-1 py-1.5 text-right font-medium tabular-nums ${side === 'in' ? 'text-rose-500' : 'text-emerald-500'}`}>
                {sign(num(r.net_amount))}{fmtCompact(num(r.net_amount))}
              </td>
              <td className={`px-1 py-1.5 text-right tabular-nums ${tone(num(r.super_net))}`}>{sign(num(r.super_net))}{fmtCompact(num(r.super_net))}</td>
              <td className="px-1 py-1.5 text-right tabular-nums text-slate-400">{r.net_ratio == null ? '—' : `${r.net_ratio}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function FundDashboard({ data, onOpenStock }) {
  const [sectorType, setSectorType] = useState('concept')
  const m = data?.market || {}
  const s = m.summary || {}
  const series = m.series || []
  const breadth = data?.breadth || {}
  const sectors = data?.sectors?.[sectorType] || { inflow: [], outflow: [] }
  const stocks = data?.stocks || { inflow: [], outflow: [] }

  const up = num(breadth.up)
  const down = num(breadth.down)
  const flat = num(breadth.flat)
  const total = Math.max(1, up + down + flat)

  const trendOption = useMemo(() => ({
    tooltip: {
      ...TIP,
      valueFormatter: (v) => `${v > 0 ? '+' : ''}${fmtCompact(v)}`,
    },
    legend: { top: 0, textStyle: { color: '#94a3b8', fontSize: 10 } },
    grid: { left: 46, right: 16, top: 28, bottom: 24 },
    xAxis: {
      type: 'category',
      data: series.map((x) => String(x.time).slice(11, 16)),
      axisLabel: { color: '#94a3b8', fontSize: 9 },
      axisLine: { lineStyle: { color: 'rgba(148,163,184,.3)' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#94a3b8', fontSize: 9, formatter: (v) => fmtCompact(v) },
      splitLine: { lineStyle: { color: 'rgba(148,163,184,.12)' } },
    },
    series: [
      { name: '主力', type: 'line', showSymbol: false, smooth: true, data: series.map((x) => x.main), lineStyle: { color: UP, width: 2 }, areaStyle: { color: 'rgba(244,63,94,.08)' } },
      { name: '超大单', type: 'line', showSymbol: false, smooth: true, data: series.map((x) => x.super), lineStyle: { color: UP_SOFT, width: 1.5 } },
      { name: '散户', type: 'line', showSymbol: false, smooth: true, data: series.map((x) => x.small), lineStyle: { color: DOWN, width: 1.5 } },
    ],
  }), [series])

  const breadthOption = useMemo(() => ({
    tooltip: { ...TIP, trigger: 'item', valueFormatter: (v) => `${v} 只` },
    series: [{
      type: 'pie',
      radius: ['58%', '80%'],
      center: ['50%', '48%'],
      label: { show: false },
      data: [
        { value: up, name: '上涨', itemStyle: { color: UP } },
        { value: down, name: '下跌', itemStyle: { color: DOWN } },
        { value: flat, name: '平盘', itemStyle: { color: FLAT } },
      ],
    }],
    graphic: [{
      type: 'text', left: 'center', top: '42%',
      style: { text: `${Math.round((up / total) * 100)}%`, fill: '#f43f5e', fontSize: 22, fontWeight: 700, textAlign: 'center' },
    }, {
      type: 'text', left: 'center', top: '56%',
      style: { text: '上涨占比', fill: '#94a3b8', fontSize: 10, textAlign: 'center' },
    }],
  }), [up, down, flat, total])

  const main5 = series.length > 5 ? num(series[series.length - 1].main) - num(series[series.length - 6].main) : 0
  const main20 = series.length > 20 ? num(series[series.length - 1].main) - num(series[series.length - 21].main) : 0
  const topSector = sectors.inflow?.[0]
  const topStock = stocks.inflow?.[0]

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
        <Metric label="主力净额" value={s.main_net} sub="沪深分钟资金流" />
        <Metric label="超大单" value={s.super_net} sub="机构/大资金" />
        <Metric label="大单" value={s.large_net} sub="中大单" />
        <Metric label="中单" value={s.medium_net} sub="中户" />
        <Metric label="散户（小单）" value={s.small_net} sub="小单净额" />
        <Metric label="近5分钟主力" value={main5} sub={`近20分钟 ${sign(main20)}${fmtCompact(main20)}`} />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <SectionHeader
            title="大盘资金链路"
            subtitle="沪指 + 深成指分钟资金净额累计 · 主力/超大单/散户"
            badge={<Badge tone={data?.live ? 'rose' : 'slate'}>{data?.live ? '实时' : '降级'}</Badge>}
          />
          <Chart option={trendOption} height={280} />
          <div className="mt-2 grid grid-cols-3 gap-2 border-t border-slate-100 pt-2 text-center dark:border-slate-800">
            {[
              ['大盘主力', s.main_net],
              ['流入板块', topSector?.main_net],
              ['流入个股', topStock?.net_amount],
            ].map(([label, value]) => (
              <div key={label}>
                <p className="text-[10px] text-slate-400">{label}</p>
                <p className={`text-xs font-semibold tabular-nums ${tone(num(value))}`}>{sign(num(value))}{fmtCompact(num(value))}</p>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <SectionHeader title="市场广度" subtitle="沪深两市实时涨跌家数" />
          <Chart option={breadthOption} height={210} />
          <div className="grid grid-cols-3 gap-1 text-center text-[10px]">
            <div><p className="text-slate-400">上涨</p><p className="text-sm font-semibold text-rose-500">{up}</p></div>
            <div><p className="text-slate-400">下跌</p><p className="text-sm font-semibold text-emerald-500">{down}</p></div>
            <div><p className="text-slate-400">平盘</p><p className="text-sm font-semibold text-slate-400">{flat}</p></div>
          </div>
          <p className="mt-2 text-center text-[10px] text-slate-400">
            {up + down > 0 ? `涨跌比 ${(up / Math.max(1, down)).toFixed(2)}` : '实时广度暂不可用'}
          </p>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <SectionHeader
            title="板块资金流"
            subtitle="主力净额 · 概念/行业"
            action={<Tabs tabs={[{ value: 'concept', label: '概念' }, { value: 'industry', label: '行业' }]} value={sectorType} onChange={setSectorType} />}
          />
          <div className="mt-3 grid gap-4 md:grid-cols-2">
            <div>
              <p className="mb-2 text-[11px] font-semibold text-rose-500">净流入 TOP</p>
              <FlowRows rows={sectors.inflow || []} side="in" />
            </div>
            <div>
              <p className="mb-2 text-[11px] font-semibold text-emerald-500">净流出 TOP</p>
              <FlowRows rows={sectors.outflow || []} side="out" />
            </div>
          </div>
        </Card>

        <Card>
          <SectionHeader title="资金链路" subtitle="大盘 → 板块 → 个股" />
          <div className="mt-3 space-y-2">
            {[
              { label: '大盘主力', data: s.main_net, name: '沪深两市' },
              { label: '最强板块', data: topSector?.main_net, name: topSector?.name || '—' },
              { label: '最强个股', data: topStock?.net_amount, name: topStock?.name || '—' },
            ].map((x, i) => (
              <div key={x.label} className="relative rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2 dark:border-slate-700 dark:bg-slate-800/50">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-[10px] text-slate-400">{x.label}</p>
                    <p className="text-xs font-semibold text-slate-700 dark:text-slate-200">{x.name}</p>
                  </div>
                  <p className={`text-sm font-bold tabular-nums ${tone(num(x.data))}`}>{sign(num(x.data))}{fmtCompact(num(x.data))}</p>
                </div>
                {i < 2 ? <div className="mx-auto mt-1 text-center text-[10px] text-slate-300">↓</div> : null}
              </div>
            ))}
          </div>
          <p className="mt-3 text-[10px] leading-4 text-slate-400">
            资金链路展示当前最强的板块和个股，不代表全市场所有流入；金额为主力净额口径。
          </p>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <SectionHeader title="个股主力流入 TOP" subtitle="主力净额 · 点击进入个股分析" badge={<Badge tone="rose">流入</Badge>} />
          <StockRows rows={stocks.inflow || []} side="in" onOpen={onOpenStock} />
        </Card>
        <Card>
          <SectionHeader title="个股主力流出 TOP" subtitle="主力净额 · 点击进入个股分析" badge={<Badge tone="emerald">流出</Badge>} />
          <StockRows rows={stocks.outflow || []} side="out" onOpen={onOpenStock} />
        </Card>
      </div>

      {data?.notes?.length ? (
        <p className="text-[10px] text-amber-500">{data.notes.join('；')}</p>
      ) : null}
    </div>
  )
}
