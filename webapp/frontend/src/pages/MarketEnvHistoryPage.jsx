/**
 * 环境历史（/market-env/history）：把「情绪得分 / 涨跌家数 / 成交额」按交易日拉成曲线。
 *
 * 数据来自 GET /market-env/history?days=N（后端读 app_sentiment_history + 日线聚合）。
 * 用法：先看趋势拐点，再用下方明细表核对具体日期；支持 60 / 120 / 250 日切换。
 */
import React, { useEffect, useMemo, useState } from 'react'
import Chart, { ACCENT, DOWN, TIP, UP } from '../components/Chart.jsx'
import { AsyncBlock, Badge, Card, PageShell, SectionHeader, Select, StatTile } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtDate, fmtNum } from '../lib/format.js'

const num = (v) => {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

const RANGE_OPTIONS = [
  { value: '30', label: '近 30 日' },
  { value: '60', label: '近 60 日' },
  { value: '120', label: '近 120 日' },
]

function ScoreAmountChart({ rows }) {
  const option = useMemo(
    () => ({
      grid: { left: 48, right: 58, top: 34, bottom: 30 },
      tooltip: {
        ...TIP,
        trigger: 'axis',
        formatter: (ps) => {
          const arr = Array.isArray(ps) ? ps : [ps]
          const date = arr[0]?.axisValue || ''
          const score = arr.find((p) => p.seriesName === '情绪得分')?.value
          const amount = arr.find((p) => p.seriesName === '成交额')?.value
          return `${date}<br/>情绪得分 <b>${score ?? '—'}</b><br/>成交额 <b>${fmtAmount(amount)}</b>`
        },
      },
      legend: { top: 0, textStyle: { color: '#94a3b8', fontSize: 10 } },
      xAxis: {
        type: 'category',
        data: rows.map((r) => String(r.trade_date).slice(5)),
        axisLabel: { color: '#94a3b8', fontSize: 10 },
        axisLine: { lineStyle: { color: 'rgba(148,163,184,0.35)' } },
      },
      yAxis: [
        {
          type: 'value',
          name: '得分',
          min: 0,
          max: 100,
          nameTextStyle: { color: '#94a3b8', fontSize: 10 },
          axisLabel: { color: '#94a3b8', fontSize: 10 },
          splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } },
        },
        {
          type: 'value',
          name: '成交额',
          nameTextStyle: { color: '#94a3b8', fontSize: 10 },
          axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v) => fmtAmount(v) },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: '成交额',
          type: 'bar',
          yAxisIndex: 1,
          data: rows.map((r) => num(r.amount)),
          itemStyle: { color: 'rgba(148,163,184,0.25)', borderRadius: [2, 2, 0, 0] },
          barMaxWidth: 14,
        },
        {
          name: '情绪得分',
          type: 'line',
          yAxisIndex: 0,
          data: rows.map((r) => num(r.score)),
          showSymbol: false,
          smooth: true,
          connectNulls: false,
          lineStyle: { color: ACCENT, width: 2 },
          itemStyle: { color: ACCENT },
          areaStyle: { color: 'rgba(99,102,241,0.10)' },
        },
      ],
    }),
    [rows],
  )
  return <Chart option={option} height={300} />
}

function BreadthChart({ rows }) {
  const option = useMemo(
    () => ({
      grid: { left: 44, right: 16, top: 30, bottom: 28 },
      tooltip: { ...TIP, trigger: 'axis' },
      legend: { top: 0, textStyle: { color: '#94a3b8', fontSize: 10 } },
      xAxis: {
        type: 'category',
        data: rows.map((r) => String(r.trade_date).slice(5)),
        axisLabel: { color: '#94a3b8', fontSize: 10 },
        axisLine: { lineStyle: { color: 'rgba(148,163,184,0.35)' } },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: '#94a3b8', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } },
      },
      series: [
        {
          name: '上涨家数',
          type: 'line',
          data: rows.map((r) => num(r.up)),
          showSymbol: false,
          smooth: true,
          lineStyle: { color: UP, width: 2 },
          itemStyle: { color: UP },
        },
        {
          name: '下跌家数',
          type: 'line',
          data: rows.map((r) => num(r.down)),
          showSymbol: false,
          smooth: true,
          lineStyle: { color: DOWN, width: 2 },
          itemStyle: { color: DOWN },
        },
      ],
    }),
    [rows],
  )
  return <Chart option={option} height={240} />
}

export default function MarketEnvHistoryPage() {
  const [range, setRange] = useState('60')
  const { data, loading, error, reload } = useApi(
    () => api.get('/market-env/history', { days: Number(range) }),
    [range],
  )

  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      reload()
    }, 120_000)
    return () => clearInterval(id)
  }, [reload])

  // 后端 /market-env/history 按 trade_date DESC 返回：前端统一升序，图表从左到右才是时间前进
  const rows = useMemo(
    () => [...(data?.items || [])].sort((a, b) => String(a.trade_date).localeCompare(String(b.trade_date))),
    [data],
  )
  const latest = rows[rows.length - 1] || {}
  const avgAmount = rows.length ? rows.reduce((a, r) => a + (num(r.amount) || 0), 0) / rows.length : null
  const upDayRatio = rows.length
    ? rows.filter((r) => (num(r.up) || 0) > (num(r.down) || 0)).length / rows.length
    : null
  const maxLimitUp = rows.length ? Math.max(...rows.map((r) => num(r.limit_up) || 0)) : null

  return (
    <PageShell
      title="环境历史"
      subtitle={
        data
          ? `最近 ${rows.length} 个交易日 · 情绪来源：${data.meta?.sentiment_source || '日线聚合'} · 数据日 ${fmtDate(latest.trade_date)}`
          : '涨跌广度 / 情绪得分 / 成交额的历史序列'
      }
      actions={<Select value={range} onChange={setRange} options={RANGE_OPTIONS} />}
    >
      {!data ? (
        <AsyncBlock
          loading={loading}
          error={error}
          onRetry={reload}
          loadingLabel="历史序列加载中…"
          empty={!loading && !error}
          emptyText="暂无历史数据"
          emptyHint="交易日历或行情尚未同步，稍后重试"
        />
      ) : rows.length === 0 ? (
        <Card>
          <p className="py-10 text-center text-xs text-slate-400">
            暂无历史数据。请先运行{' '}
            <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">
              python webapp/scripts/backfill_sentiment.py
            </code>{' '}
            回填情绪历史。
          </p>
        </Card>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatTile
              label="最新情绪得分"
              value={fmtNum(latest.score, 1)}
              sub={`${latest.mood || '—'} · ${fmtDate(latest.trade_date)}`}
            />
            <StatTile label="区间日均成交额" value={fmtAmount(avgAmount)} sub={`${rows.length} 个交易日`} />
            <StatTile label="上涨日占比" value={`${fmtNum((upDayRatio ?? 0) * 100, 1)}%`} sub="上涨家数 > 下跌家数" />
            <StatTile label="区间最高涨停家数" value={fmtNum(maxLimitUp, 0)} sub="单日峰值" />
          </div>

          <Card>
            <SectionHeader
              title="情绪与量能"
              subtitle="柱状为两市成交额（右轴），折线为情绪得分（0—100）"
              badge={data.meta?.degraded ? <Badge tone="amber">部分缺口</Badge> : <Badge tone="blue">数据完整</Badge>}
            />
            <ScoreAmountChart rows={rows} />
            {data.meta?.note ? (
              <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-500">{data.meta.note}</p>
            ) : null}
          </Card>

          <Card>
            <SectionHeader title="涨跌家数" subtitle="上涨家数（红）与下跌家数（绿）的背离往往先于指数拐点" />
            <BreadthChart rows={rows} />
          </Card>

          <Card>
            <SectionHeader title="交易日明细" subtitle="按时间倒序 · 可核对每个交易日的广度、涨停与情绪" />
            <div className="mt-2 max-h-96 overflow-auto rounded-lg border border-slate-200 dark:border-slate-800">
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-slate-50 text-[11px] text-slate-500 dark:bg-slate-900 dark:text-slate-400">
                  <tr>
                    <th className="px-3 py-2">交易日</th>
                    <th className="px-3 py-2 text-right">上涨</th>
                    <th className="px-3 py-2 text-right">下跌</th>
                    <th className="px-3 py-2 text-right">涨停</th>
                    <th className="px-3 py-2 text-right">成交额</th>
                    <th className="px-3 py-2 text-right">情绪分</th>
                    <th className="px-3 py-2">档位</th>
                  </tr>
                </thead>
                <tbody>
                  {[...rows].reverse().map((r) => (
                    <tr key={r.trade_date} className="border-t border-slate-100 dark:border-slate-800">
                      <td className="px-3 py-1.5 tabular-nums text-slate-600 dark:text-slate-300">{r.trade_date}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-rose-500">{r.up ?? '—'}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-emerald-500">{r.down ?? '—'}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-slate-600 dark:text-slate-300">{r.limit_up ?? '—'}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums text-slate-600 dark:text-slate-300">{fmtAmount(r.amount)}</td>
                      <td className="px-3 py-1.5 text-right font-medium tabular-nums text-slate-700 dark:text-slate-200">{fmtNum(r.score, 1)}</td>
                      <td className="px-3 py-1.5 text-slate-500 dark:text-slate-400">{r.mood || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </PageShell>
  )
}
