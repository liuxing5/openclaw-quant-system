/**
 * 市场环境（/market-env）：一屏回答「今天能不能开仓、开多大仓」。
 *
 * 数据来自 GET /market-env（后端把指数趋势、广度、情绪、量能四次聚合成一个响应）。
 * 页面结构按决策顺序排：
 *   1. 环境总分 + 定级 + 自动生成的要点（先给结论）；
 *   2. 六大指数（趋势方向）；
 *   3. 广度与涨跌分布（赚钱效应）；
 *   4. 情绪雷达 + 量能趋势（持续性与风险）。
 * 每 60 秒静默轮询；盘中数据由后端 warmup / 实时层刷新。
 */
import React, { useEffect, useMemo } from 'react'
import Chart, { ACCENT, DOWN, Sparkline, TIP, UP } from '../components/Chart.jsx'
import { AsyncBlock, Badge, Card, PageShell, SectionHeader, StatTile } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtDate, fmtNum, fmtPct, pctTone } from '../lib/format.js'

const POLL_MS = 60_000

const LEVEL_TONE = {
  进攻: 'rose',
  偏多: 'rose',
  中性: 'blue',
  防守: 'amber',
  冰点: 'emerald',
}

const num = (v) => {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function EnvironmentCard({ data }) {
  const score = num(data?.env_score) ?? 0
  const level = data?.env_level || '—'
  const notes = data?.notes || []

  const option = useMemo(
    () => ({
      series: [
        {
          type: 'gauge',
          startAngle: 210,
          endAngle: -30,
          min: 0,
          max: 100,
          radius: '92%',
          center: ['50%', '58%'],
          progress: {
            show: true,
            width: 14,
            itemStyle: { color: score >= 60 ? UP : score >= 40 ? ACCENT : DOWN },
          },
          axisLine: { lineStyle: { width: 14, color: [[1, 'rgba(148,163,184,0.18)']] } },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { show: false },
          pointer: { show: false },
          anchor: { show: false },
          title: { show: false },
          detail: {
            valueAnimation: true,
            offsetCenter: [0, '0%'],
            fontSize: 30,
            fontWeight: 700,
            color: '#e2e8f0',
            formatter: (v) => `${Math.round(v)}`,
          },
          data: [{ value: score }],
        },
      ],
    }),
    [score],
  )

  return (
    <Card className="flex h-full flex-col">
      <SectionHeader
        title="市场环境"
        subtitle="指数趋势 · 赚钱效应 · 情绪 · 量能 的加权结论"
        badge={<Badge tone={LEVEL_TONE[level] || 'slate'}>{level}</Badge>}
      />
      <div className="grid flex-1 gap-3 sm:grid-cols-[180px_1fr]">
        <Chart option={option} height={170} />
        <ul className="space-y-1.5 self-center text-xs leading-5 text-slate-600 dark:text-slate-300">
          {notes.length ? (
            notes.map((n, i) => (
              <li key={i} className="flex gap-2">
                <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-brand-400" />
                <span>{n}</span>
              </li>
            ))
          ) : (
            <li className="text-slate-400">暂无要点，等待行情聚合完成</li>
          )}
        </ul>
      </div>
      <p className="mt-2 text-[11px] text-slate-400 dark:text-slate-500">
        环境分 0—100：≥70 进攻、55—70 偏多、40—55 中性、25—40 防守、&lt;25 冰点
      </p>
    </Card>
  )
}

function IndexStrip({ indexes = [] }) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      {indexes.map((it) => {
        const pct = num(it.pct_chg)
        const points = (it.curve || []).map((p) => num(p.close)).filter((v) => v !== null)
        return (
          <Card key={it.ts_code} className="p-3">
            <div className="flex items-start justify-between gap-1">
              <div className="min-w-0">
                <p className="truncate text-xs text-slate-500 dark:text-slate-400">{it.name || it.ts_code}</p>
                <p className={`mt-1 text-lg font-semibold tabular-nums ${pctTone(pct)}`}>
                  {fmtNum(it.close, 2)}
                </p>
              </div>
              <Badge tone={pct === null ? 'slate' : pct > 0 ? 'rose' : pct < 0 ? 'emerald' : 'slate'}>
                {fmtPct(pct)}
              </Badge>
            </div>
            <Sparkline points={points} height={30} color={(pct ?? 0) >= 0 ? UP : DOWN} className="mt-1 w-full" />
            <p className="mt-1 flex justify-between text-[10px] tabular-nums text-slate-400 dark:text-slate-500">
              <span>高 {fmtNum(it.high, 2)}</span>
              <span>低 {fmtNum(it.low, 2)}</span>
            </p>
          </Card>
        )
      })}
    </div>
  )
}

function BreadthCard({ breadth }) {
  const b = breadth || {}
  const hist = b.histogram || []
  const up = num(b.up) ?? 0
  const down = num(b.down) ?? 0
  const flat = num(b.flat) ?? 0
  const total = Math.max(1, up + down + flat)

  const option = useMemo(
    () => ({
      grid: { left: 40, right: 12, top: 16, bottom: 38 },
      tooltip: { ...TIP },
      xAxis: {
        type: 'category',
        data: hist.map((h) => h.label),
        axisLabel: { color: '#94a3b8', fontSize: 10, interval: 0, rotate: 30 },
        axisLine: { lineStyle: { color: 'rgba(148,163,184,0.35)' } },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: '#94a3b8', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } },
      },
      series: [
        {
          type: 'bar',
          data: hist.map((h, i) => ({
            value: h.count,
            itemStyle: { color: i <= 4 ? UP : i === 5 ? '#94a3b8' : DOWN, borderRadius: [3, 3, 0, 0] },
          })),
          barMaxWidth: 26,
        },
      ],
    }),
    [hist],
  )

  return (
    <Card>
      <SectionHeader
        title="涨跌分布"
        subtitle={`上涨 ${up} · 平盘 ${flat} · 下跌 ${down} · 涨停 ${b.limit_up ?? '—'} / 跌停 ${b.limit_down ?? '—'}`}
        badge={<Badge tone={up > down ? 'rose' : 'emerald'}>上涨占比 {fmtNum((up / total) * 100, 1)}%</Badge>}
      />
      <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <span className="bg-rose-500" style={{ width: `${(up / total) * 100}%` }} />
        <span className="bg-slate-400" style={{ width: `${(flat / total) * 100}%` }} />
        <span className="bg-emerald-500" style={{ width: `${(down / total) * 100}%` }} />
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2 text-center text-[10px] text-slate-400">
        <span>上涨 {up} 家</span>
        <span>平盘 {flat} 家</span>
        <span>下跌 {down} 家</span>
      </div>
      {hist.length ? (
        <Chart option={option} height={190} className="mt-1" />
      ) : (
        <p className="py-8 text-center text-xs text-slate-400">暂无分布数据</p>
      )}
    </Card>
  )
}

function SentimentRadar({ sentiment }) {
  const dims = sentiment?.dims || []
  const option = useMemo(
    () => ({
      tooltip: { ...TIP },
      radar: {
        radius: '62%',
        center: ['50%', '54%'],
        indicator: dims.map((d) => ({ name: d.label, max: 100 })),
        axisName: { color: '#94a3b8', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.18)' } },
        splitArea: { areaStyle: { color: ['rgba(99,102,241,0.04)', 'rgba(99,102,241,0.01)'] } },
        axisLine: { lineStyle: { color: 'rgba(148,163,184,0.18)' } },
      },
      series: [
        {
          type: 'radar',
          data: [
            {
              value: dims.map((d) => num(d.value) ?? 0),
              name: '情绪维度',
              areaStyle: { color: 'rgba(99,102,241,0.22)' },
              lineStyle: { color: ACCENT, width: 2 },
              itemStyle: { color: ACCENT },
            },
          ],
        },
      ],
    }),
    [dims],
  )

  return (
    <Card>
      <SectionHeader
        title="情绪六维"
        subtitle={sentiment?.hint || '趋势 / 广度 / 涨停效应 / 连板 / 新高 / 量能'}
        badge={<Badge tone="blue">综合 {fmtNum(sentiment?.score, 1)} · {sentiment?.level || '—'}</Badge>}
      />
      {dims.length ? (
        <Chart option={option} height={230} />
      ) : (
        <p className="py-10 text-center text-xs text-slate-400">暂无情绪维度数据</p>
      )}
    </Card>
  )
}

function AmountTrend({ trend = [] }) {
  const rows = trend || []
  const option = useMemo(
    () => ({
      grid: { left: 56, right: 16, top: 20, bottom: 28 },
      tooltip: {
        ...TIP,
        formatter: (ps) => {
          const p = Array.isArray(ps) ? ps[0] : ps
          return `${p.axisValue}<br/>成交额 <b>${fmtAmount(p.value)}</b>`
        },
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: rows.map((r) => String(r.trade_date).slice(5)),
        axisLabel: { color: '#94a3b8', fontSize: 10 },
        axisLine: { lineStyle: { color: 'rgba(148,163,184,0.35)' } },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v) => fmtAmount(v) },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } },
      },
      series: [
        {
          type: 'line',
          data: rows.map((r) => num(r.amount)),
          showSymbol: false,
          smooth: true,
          lineStyle: { color: ACCENT, width: 2 },
          itemStyle: { color: ACCENT },
          areaStyle: { color: 'rgba(99,102,241,0.12)' },
        },
      ],
    }),
    [rows],
  )

  return (
    <Card>
      <SectionHeader
        title="两市成交额"
        subtitle={`近 ${rows.length || 0} 个交易日 · 量能是行情持续性的前提`}
        badge={rows.length ? <Badge tone="slate">{fmtAmount(rows[rows.length - 1]?.amount)}</Badge> : null}
      />
      {rows.length ? (
        <Chart option={option} height={210} />
      ) : (
        <p className="py-10 text-center text-xs text-slate-400">暂无成交额序列</p>
      )}
    </Card>
  )
}

export default function MarketEnvPage() {
  const { data, loading, error, reload } = useApi(() => api.get('/market-env'), [])

  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      reload()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [reload])

  const b = data?.breadth || {}
  const sentiment = data?.sentiment || {}

  return (
    <PageShell
      title="市场环境"
      subtitle={
        data
          ? `交易日 ${fmtDate(data.trade_date)} · 环境分 ${fmtNum(data.env_score, 0)} · ${data.env_level || '—'}`
          : '大盘环境与仓位建议'
      }
      actions={<span className="text-[11px] text-slate-400">每 60 秒自动刷新</span>}
    >
      {!data ? (
        <AsyncBlock
          loading={loading}
          error={error}
          onRetry={reload}
          loadingLabel="市场环境聚合中…"
          empty={!loading && !error}
          emptyText="暂无市场环境数据"
          emptyHint="等待行情聚合完成，或点此重试"
        />
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <EnvironmentCard data={data} />
            </div>
            <div className="grid grid-cols-2 gap-2 lg:grid-cols-1">
              <StatTile label="两市成交额" value={fmtAmount(b.amount)} sub={`20 日均额比 ${fmtNum((sentiment.amount_ratio ?? 0) * 100, 0)}%`} />
              <StatTile label="涨停 / 跌停" value={`${b.limit_up ?? '—'} / ${b.limit_down ?? '—'}`} sub={`封板率 ${fmtNum((b.seal_rate ?? 0) * 100, 0)}%`} />
              <StatTile label="上涨 / 下跌" value={`${b.up ?? '—'} / ${b.down ?? '—'}`} sub={`上涨占比 ${fmtNum((b.up_ratio ?? 0) * 100, 1)}%`} tone={pctTone((b.up_ratio ?? 0) - 0.5)} />
              <StatTile label="环境分" value={fmtNum(data.env_score, 0)} sub={data.env_level || '—'} />
            </div>
          </div>

          <IndexStrip indexes={data.indexes} />

          <div className="grid gap-4 lg:grid-cols-2">
            <BreadthCard breadth={b} />
            <SentimentRadar sentiment={sentiment} />
          </div>

          <AmountTrend trend={data.amount_trend} />
        </div>
      )}
    </PageShell>
  )
}
