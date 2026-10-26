/**
 * 总台（/dashboard）：指数卡片 · 涨跌分布 · 情绪雷达 · 涨停梯队 · 四榜单 · 板块热度 · 数据源状态。
 *
 * 数据全部来自本地 PostgreSQL 聚合接口 /api/v1/dashboard/overview，
 * 前端每 60 秒静默轮询一次，配合后端后台预热做到「打开即出、持续刷新」。
 */
import React, { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Chart, { ACCENT, DOWN, DOWN_SOFT, FLAT, Sparkline, TIP, UP, UP_SOFT } from '../components/Chart.jsx'
import { AsyncBlock, Badge, Card, PageShell, SectionHeader, Tabs, TextLink } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtDate, fmtNum, fmtPct, pctTone, ratioPct } from '../lib/format.js'

const POLL_MS = 60_000

const HIST_COLORS = [
  UP, // 涨停
  '#fb7185',
  '#fda4af',
  '#fecdd3',
  '#ffe4e6',
  FLAT,
  '#d1fae5',
  '#a7f3d0',
  '#6ee7b7',
  DOWN_SOFT,
  DOWN, // 跌停
]

const RANK_TABS = [
  { value: 'pct_chg_desc', label: '涨幅榜' },
  { value: 'pct_chg_asc', label: '跌幅榜' },
  { value: 'amount', label: '成交额榜' },
  { value: 'turnover_rate', label: '换手榜' },
]

/* ------------------------------------------------------------------ 指数卡片 */

function IndexCard({ item }) {
  const tone = pctTone(item.pct_chg)
  const curve = (item.curve || []).map((c) => c.close)
  return (
    <Card className="p-3.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-xs font-medium text-slate-500 dark:text-slate-400">{item.name}</p>
          <p className={`mt-1 text-xl font-semibold tabular-nums ${tone}`}>{fmtNum(item.close, 2)}</p>
        </div>
        <Badge tone={item.pct_chg > 0 ? 'rose' : item.pct_chg < 0 ? 'emerald' : 'slate'}>
          {fmtPct(item.pct_chg)}
        </Badge>
      </div>
      <Sparkline points={curve} height={34} color={item.pct_chg >= 0 ? UP : DOWN} className="mt-2 w-full" />
      <p className="mt-1 text-[10px] text-slate-400 dark:text-slate-500">
        高 {fmtNum(item.high, 2)} · 低 {fmtNum(item.low, 2)}
      </p>
    </Card>
  )
}

/* -------------------------------------------------------------- 涨跌分布直方图 */

function BreadthPanel({ breadth }) {
  const option = useMemo(() => {
    const hist = breadth.histogram || []
    return {
      grid: { left: 44, right: 12, top: 18, bottom: 26 },
      tooltip: { ...TIP, trigger: 'axis' },
      xAxis: {
        type: 'category',
        data: hist.map((h) => h.label),
        ...{ axisLine: { lineStyle: { color: 'rgba(148,163,184,0.35)' } }, axisLabel: { color: '#94a3b8', fontSize: 10, interval: 0, rotate: 32 } },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: '#94a3b8', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } },
      },
      series: [
        {
          type: 'bar',
          data: hist.map((h, i) => ({ value: h.count, itemStyle: { color: HIST_COLORS[i] || ACCENT, borderRadius: [3, 3, 0, 0] } })),
          barMaxWidth: 34,
        },
      ],
    }
  }, [breadth])

  const stats = [
    { label: '上涨', value: breadth.up, tone: 'text-rose-600 dark:text-rose-400' },
    { label: '平盘', value: breadth.flat, tone: 'text-slate-500 dark:text-slate-400' },
    { label: '下跌', value: breadth.down, tone: 'text-emerald-600 dark:text-emerald-400' },
    { label: '涨停', value: breadth.limit_up, tone: 'text-rose-600 dark:text-rose-400' },
    { label: '跌停', value: breadth.limit_down, tone: 'text-emerald-600 dark:text-emerald-400' },
    { label: '炸板', value: breadth.broken, tone: 'text-amber-600 dark:text-amber-400' },
  ]

  return (
    <Card>
      <SectionHeader
        title="涨跌分布"
        subtitle={`${breadth.trade_date} · 全市场 ${fmtNum(breadth.total, 0)} 只（不含停牌）`}
        badge={
          <Badge tone="blue">
            上涨占比 {ratioPct(breadth.up_ratio)}
          </Badge>
        }
      />
      <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-6">
        {stats.map((s) => (
          <div key={s.label} className="rounded-lg border border-slate-200 bg-slate-50/60 px-2 py-1.5 text-center dark:border-slate-700 dark:bg-slate-800/50">
            <p className="text-[10px] text-slate-500 dark:text-slate-400">{s.label}</p>
            <p className={`text-base font-semibold tabular-nums ${s.tone}`}>{fmtNum(s.value, 0)}</p>
          </div>
        ))}
      </div>
      <Chart option={option} height={240} className="mt-3" />
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-slate-500 dark:text-slate-400">
        <span>
          两市成交 <b className="tabular-nums text-slate-700 dark:text-slate-200">{fmtAmount(breadth.amount)}</b>
        </span>
        <span>
          触板 {breadth.touched} · 封板率{' '}
          <b className="tabular-nums text-slate-700 dark:text-slate-200">{ratioPct(breadth.seal_rate)}</b>
        </span>
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------ 量化引擎 */

function FactorLine({ label, value }) {
  const v = Math.max(0, Math.min(100, Number(value) || 0))
  return (
    <div className="flex items-center gap-2">
      <span className="w-9 shrink-0 text-[10px] text-slate-400">{label}</span>
      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <span className="block h-full rounded-full bg-brand-500" style={{ width: `${v}%` }} />
      </span>
      <span className="w-7 text-right text-[10px] tabular-nums text-slate-500">{Math.round(v)}</span>
    </div>
  )
}

function QuantPanel() {
  const navigate = useNavigate()
  const scanQ = useApi(() => api.get('/strategies/smart-scan', { preset: 'smart', min_score: 60, limit: 8 }), [])
  const statsQ = useApi(() => api.get('/strategies/run-stats', { days: 30 }), [])
  const hits = scanQ.data?.hits || []
  const stats = statsQ.data || {}
  const avg = (key) => {
    const vals = hits.map((h) => h.factors?.[key]).filter((v) => typeof v === 'number')
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0
  }

  return (
    <Card>
      <SectionHeader
        title="量化引擎"
        subtitle="全市场 5,000+ · 趋势/资金/质量/估值七因子打分，命中后按综合分排序"
        badge={<Badge tone="blue">{scanQ.loading ? '扫描中' : `${hits.length} 只信号`}</Badge>}
        action={<TextLink to="/strategy">查看全部量化选股 →</TextLink>}
      />
      <div className="mt-3 grid gap-4 xl:grid-cols-3">
        <div className="xl:col-span-2 overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 text-[10px] text-slate-500 dark:bg-slate-900 dark:text-slate-400">
              <tr>
                <th className="px-2 py-1.5">#</th>
                <th className="px-2 py-1.5">量化候选</th>
                <th className="px-2 py-1.5 text-right">综合分</th>
                <th className="px-2 py-1.5 text-right">今日</th>
                <th className="px-2 py-1.5">因子/理由</th>
              </tr>
            </thead>
            <tbody>
              {hits.slice(0, 6).map((h) => (
                <tr
                  key={h.ts_code}
                  className="cursor-pointer border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                  onClick={() => navigate(`/stock-analysis/${String(h.ts_code).split('.')[0]}`)}
                >
                  <td className="px-2 py-1.5 text-slate-400">{h.rank}</td>
                  <td className="px-2 py-1.5">
                    <span className="font-medium text-slate-700 dark:text-slate-200">{h.name}</span>
                    <span className="ml-1 font-mono text-[10px] text-slate-400">{h.ts_code}</span>
                  </td>
                  <td className="px-2 py-1.5 text-right font-semibold tabular-nums text-brand-500">{fmtNum(h.score, 1)}</td>
                  <td className={`px-2 py-1.5 text-right tabular-nums ${pctTone(h.pct_chg)}`}>{fmtPct(h.pct_chg)}</td>
                  <td className="px-2 py-1.5 text-[10px] text-slate-400">{(h.reasons || []).slice(0, 3).join(' · ')}</td>
                </tr>
              ))}
              {!hits.length ? (
                <tr>
                  <td colSpan={5} className="px-2 py-6 text-center text-xs text-slate-400">
                    {scanQ.error ? '量化扫描暂不可用' : '量化引擎计算中…'}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        <div className="space-y-3">
          <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
            <p className="text-[10px] text-slate-400">候选池平均因子</p>
            <div className="mt-2 space-y-1.5">
              <FactorLine label="趋势" value={avg('trend')} />
              <FactorLine label="资金" value={avg('capital')} />
              <FactorLine label="质量" value={avg('quality')} />
              <FactorLine label="估值" value={avg('valuation')} />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-2">
            {[
              { label: '策略数', value: fmtNum(stats.strategy_count ?? stats.total_strategies, 0), sub: '内置+自定义' },
              { label: '30日命中', value: fmtNum(stats.total_hits_30d, 0), sub: '累计入选' },
              { label: '平均耗时', value: stats.avg_elapsed_ms ? `${(stats.avg_elapsed_ms / 1000).toFixed(1)}s` : '—', sub: '全市场' },
            ].map((x) => (
              <div key={x.label} className="rounded-lg bg-slate-50 px-2 py-2 text-center dark:bg-slate-800/60">
                <p className="text-[9px] text-slate-400">{x.label}</p>
                <p className="text-sm font-semibold tabular-nums text-slate-700 dark:text-slate-200">{x.value}</p>
                <p className="text-[8px] text-slate-400">{x.sub}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------ 情绪雷达 */

const SCORE_TONES = {
  亢奋: 'text-rose-600 dark:text-rose-400',
  活跃: 'text-orange-600 dark:text-orange-400',
  温和: 'text-brand-600 dark:text-brand-400',
  低迷: 'text-amber-600 dark:text-amber-400',
  冰点: 'text-emerald-600 dark:text-emerald-400',
}

function SentimentPanel({ sentiment }) {
  const dims = sentiment.dims || []
  const option = useMemo(
    () => ({
      radar: {
        indicator: dims.map((d) => ({ name: d.label, max: 100 })),
        radius: '62%',
        axisName: { color: '#94a3b8', fontSize: 11 },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.2)' } },
        splitArea: { areaStyle: { color: ['rgba(99,102,241,0.04)', 'rgba(99,102,241,0.08)'] } },
        axisLine: { lineStyle: { color: 'rgba(148,163,184,0.25)' } },
      },
      tooltip: { ...TIP, trigger: 'item' },
      series: [
        {
          type: 'radar',
          data: [
            {
              name: sentiment.level || '情绪',
              value: dims.map((d) => d.value),
              lineStyle: { color: ACCENT, width: 2 },
              itemStyle: { color: ACCENT },
              areaStyle: { color: 'rgba(99,102,241,0.25)' },
            },
          ],
        },
      ],
    }),
    [dims, sentiment.level],
  )

  return (
    <Card className="flex h-full flex-col">
      <SectionHeader
        title="市场情绪"
        subtitle="六维打分：趋势 / 广度 / 涨停效应 / 封板率 / 连板高度 / 量能"
        badge={<Badge tone="amber">数据日 {fmtDate(sentiment.trade_date)}</Badge>}
      />
      <div className="mt-3 flex items-center gap-4">
        <div className="relative grid size-24 shrink-0 place-items-center rounded-full border-4 border-brand-500/25">
          <div className="text-center">
            <p className={`text-2xl font-bold tabular-nums ${SCORE_TONES[sentiment.level] || ''}`}>
              {sentiment.score}
            </p>
            <p className="text-[10px] text-slate-400">/ 100</p>
          </div>
        </div>
        <div className="min-w-0">
          <p className={`text-lg font-semibold ${SCORE_TONES[sentiment.level] || ''}`}>{sentiment.level}</p>
          <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{sentiment.hint}</p>
        </div>
      </div>
      <Chart option={option} height={230} className="mt-1" />
      <div className="mt-auto grid grid-cols-3 gap-2 pt-2 text-center">
        <div className="rounded-lg bg-slate-50 py-1.5 dark:bg-slate-800/60">
          <p className="text-[10px] text-slate-400">当日量</p>
          <p className="text-xs font-semibold tabular-nums">{fmtAmount(sentiment.amount_today)}</p>
        </div>
        <div className="rounded-lg bg-slate-50 py-1.5 dark:bg-slate-800/60">
          <p className="text-[10px] text-slate-400">20 日均</p>
          <p className="text-xs font-semibold tabular-nums">{fmtAmount(sentiment.amount_avg20)}</p>
        </div>
        <div className="rounded-lg bg-slate-50 py-1.5 dark:bg-slate-800/60">
          <p className="text-[10px] text-slate-400">量比</p>
          <p className="text-xs font-semibold tabular-nums">{fmtNum(sentiment.amount_ratio, 2)}</p>
        </div>
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------ 涨停梯队 */

function LadderPanel({ ladder }) {
  const navigate = useNavigate()
  const tiers = ladder.tiers || []
  return (
    <Card className="flex h-full flex-col">
      <SectionHeader
        title="涨停梯队"
        subtitle={`最高 ${ladder.max_streak} 板 · 涨停 ${ladder.limit_up} 家`}
        action={<TextLink to="/limit-ladder">完整梯队 →</TextLink>}
      />
      <div className="mt-3 flex-1 space-y-3 overflow-y-auto thin-scroll pr-1" style={{ maxHeight: 360 }}>
        {tiers.length === 0 ? (
          <p className="py-8 text-center text-xs text-slate-400">当日无涨停个股</p>
        ) : (
          tiers.map((tier) => (
            <div key={tier.streak}>
              <div className="mb-1.5 flex items-center gap-2">
                <span className="rounded-md bg-rose-600 px-1.5 py-0.5 text-[11px] font-bold text-white">
                  {tier.streak} 板
                </span>
                <span className="text-[11px] text-slate-400">{tier.count} 只</span>
                <span className="h-px flex-1 bg-slate-200 dark:bg-slate-700" />
              </div>
              <div className="flex flex-wrap gap-1.5">
                {tier.members.map((m) => (
                  <button
                    key={m.ts_code}
                    type="button"
                    onClick={() => navigate(`/stock-analysis/${m.ts_code}`)}
                    className="group flex items-center gap-1.5 rounded-md border border-rose-200 bg-rose-50/70 px-2 py-1 text-[11px] transition hover:border-rose-400 dark:border-rose-500/30 dark:bg-rose-500/10"
                    title={`${m.name} ${m.ts_code} · 换手 ${m.turnover_rate}%`}
                  >
                    <span className="font-medium text-rose-700 group-hover:underline dark:text-rose-300">
                      {m.name}
                    </span>
                    <span className="tabular-nums text-rose-500 dark:text-rose-400">
                      {fmtPct(m.pct_chg, 2)}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ))
        )}
      </div>
    </Card>
  )
}

/* -------------------------------------------------------------------- 四榜单 */

function RankPanel({ rankings }) {
  const navigate = useNavigate()
  const [tab, setTab] = useState('pct_chg_desc')
  const rows = rankings[tab] || []
  const cols = {
    pct_chg_desc: { key: 'pct_chg', label: '涨幅' },
    pct_chg_asc: { key: 'pct_chg', label: '涨幅' },
    amount: { key: 'amount', label: '成交额' },
    turnover_rate: { key: 'turnover_rate', label: '换手' },
  }
  const col = cols[tab]

  return (
    <Card>
      <SectionHeader
        title="个股榜单"
        subtitle="SQL 内排序取前 8，点击进入个股分析"
        action={<Tabs tabs={RANK_TABS} value={tab} onChange={setTab} />}
      />
      <table className="mt-3 w-full text-xs">
        <thead>
          <tr className="text-left text-[11px] text-slate-400">
            <th className="pb-1.5 font-medium">#</th>
            <th className="pb-1.5 font-medium">名称</th>
            <th className="pb-1.5 text-right font-medium">现价</th>
            <th className="pb-1.5 text-right font-medium">{col.label}</th>
            <th className="hidden pb-1.5 text-right font-medium sm:table-cell">成交额</th>
            <th className="hidden pb-1.5 text-right font-medium sm:table-cell">换手</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr
              key={r.ts_code}
              onClick={() => navigate(`/stock-analysis/${r.ts_code}`)}
              className="cursor-pointer border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/60"
            >
              <td className="py-1.5 tabular-nums text-slate-400">{i + 1}</td>
              <td className="py-1.5">
                <span className="font-medium text-slate-700 dark:text-slate-200">{r.name}</span>
                <span className="ml-1 text-[10px] text-slate-400">{r.board}</span>
              </td>
              <td className="py-1.5 text-right tabular-nums text-slate-600 dark:text-slate-300">
                {fmtNum(r.close, 2)}
              </td>
              <td className={`py-1.5 text-right font-medium tabular-nums ${pctTone(r.pct_chg)}`}>
                {tab === 'amount' ? fmtAmount(r.amount) : tab === 'turnover_rate' ? `${fmtNum(r.turnover_rate, 1)}%` : fmtPct(r.pct_chg)}
              </td>
              <td className="hidden py-1.5 text-right tabular-nums text-slate-500 sm:table-cell">
                {fmtAmount(r.amount)}
              </td>
              <td className="hidden py-1.5 text-right tabular-nums text-slate-500 sm:table-cell">
                {fmtNum(r.turnover_rate, 1)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}

/* -------------------------------------------------------------- 概念 / 行业热度 */

function BoardPanel({ title, subtitle, data, kind }) {
  const navigate = useNavigate()
  const gainers = data.gainers || []
  const losers = data.losers || []
  return (
    <Card>
      <SectionHeader
        title={title}
        subtitle={`${subtitle} · 共 ${data.total} 个（${fmtDate(data.trade_date)}）`}
        action={<TextLink to={kind === 'concept' ? '/concept-analysis' : '/industry-analysis'}>全部 →</TextLink>}
      />
      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        {[
          { label: '领涨', rows: gainers, tone: 'up' },
          { label: '领跌', rows: losers, tone: 'down' },
        ].map((block) => (
          <div key={block.label}>
            <p className={`mb-1.5 text-[11px] font-semibold ${block.tone === 'up' ? 'text-rose-500' : 'text-emerald-500'}`}>
              {block.label}
            </p>
            <ul className="space-y-1">
              {block.rows.map((b) => (
                <li key={`${block.label}-${b.code}`}>
                  <button
                    type="button"
                    onClick={() => navigate(`/${kind}-analysis/${b.code}`)}
                    className="flex w-full items-center justify-between gap-2 rounded-md px-1.5 py-1 text-xs transition hover:bg-slate-50 dark:hover:bg-slate-800/70"
                  >
                    <span className="min-w-0 truncate text-left">
                      <span className="tabular-nums text-slate-400">{b.rank}</span>
                      <span className="ml-1.5 font-medium text-slate-700 dark:text-slate-200">{b.name}</span>
                      {b.lead_stock ? (
                        <span className="ml-1.5 text-[10px] text-slate-400">领涨 {b.lead_stock}</span>
                      ) : null}
                    </span>
                    <span className={`shrink-0 tabular-nums font-medium ${pctTone(b.pct_chg)}`}>
                      {fmtPct(b.pct_chg)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------ 成交额序列 */

function AmountPanel({ series }) {
  const option = useMemo(() => {
    const rows = series || []
    return {
      grid: { left: 56, right: 14, top: 24, bottom: 26 },
      tooltip: {
        ...TIP,
        formatter: (ps) => {
          const p = Array.isArray(ps) ? ps[0] : ps
          return `${p.axisValue}<br/>成交额 <b>${fmtAmount(p.value)}</b>`
        },
      },
      xAxis: {
        type: 'category',
        data: rows.map((r) => r.date.slice(5)),
        axisLine: { lineStyle: { color: 'rgba(148,163,184,0.35)' } },
        axisLabel: { color: '#94a3b8', fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v) => `${(v / 1e12).toFixed(2)}万亿` },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } },
      },
      series: [
        {
          type: 'bar',
          data: rows.map((r) => r.amount),
          itemStyle: { color: 'rgba(99,102,241,0.65)', borderRadius: [3, 3, 0, 0] },
          barMaxWidth: 18,
        },
      ],
    }
  }, [series])

  return (
    <Card>
      <SectionHeader title="两市成交额" subtitle={`近 ${(series || []).length} 个交易日`} />
      <Chart option={option} height={230} className="mt-2" />
    </Card>
  )
}

/* ---------------------------------------------------------------- 数据源状态条 */

function SourcesPanel({ sources }) {
  const datasets = sources.datasets || []
  const ok = datasets.filter((d) => d.status === 'ok').length
  return (
    <Card className="h-full">
      <SectionHeader
        title="数据源状态"
        subtitle={`${sources.note || ''}`}
        badge={<Badge tone={ok === datasets.length ? 'emerald' : 'amber'}>{ok}/{datasets.length} 正常</Badge>}
      />
      <ul className="mt-3 space-y-1.5" style={{ maxHeight: 230, overflowY: 'auto' }}>
        {datasets.map((d) => (
          <li key={d.id} className="flex items-center justify-between gap-2 rounded-md border border-slate-100 bg-slate-50/60 px-2 py-1.5 text-[11px] dark:border-slate-800 dark:bg-slate-800/50">
            <span className="min-w-0 truncate">
              <span className={`mr-1.5 inline-block size-1.5 rounded-full ${d.status === 'ok' ? 'bg-emerald-500' : 'bg-amber-500'}`} />
              <span className="font-medium text-slate-700 dark:text-slate-200">{d.name}</span>
            </span>
            <span className="shrink-0 tabular-nums text-slate-400">
              {fmtNum(d.rows, 0)} 行 · {d.latest ? fmtDate(d.latest) : '—'}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[10px] text-slate-400">
        数据底座：PostgreSQL · 更新于 {fmtDate(sources.updated_at)}
      </p>
    </Card>
  )
}

/* --------------------------------------------------------------- 实时行情条 */

const TICKER_CODES = '000001.SH,399001.SZ,399006.SZ,000300.SH,000905.SH,000688.SH'
const TICKER_POLL_MS = 10_000

/**
 * 实时行情条：日线底表是「上一交易日收盘」，而盘中真正在变的是实时快照。
 * 这里每 10 秒取一次六大指数实时价（后端 10s 缓存 + 四级降级），
 * 让总台第一屏就能看到「现在」而不是「昨天」。
 */
function RealtimeTicker() {
  const ticker = useApi(() => api.get('/market-data/realtime', { codes: TICKER_CODES }), [])

  React.useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      ticker.reload()
    }, TICKER_POLL_MS)
    return () => clearInterval(id)
  }, [ticker.reload])

  const payload = ticker.data
  const items = payload?.items || []
  if (!items.length) return null

  return (
    <Card className="flex flex-wrap items-center gap-x-4 gap-y-2 py-2">
      <span className="flex items-center gap-1.5 text-[11px] font-medium text-slate-500 dark:text-slate-400">
        <span className={`inline-block size-1.5 rounded-full ${payload?.trading ? 'animate-pulse bg-rose-500' : 'bg-slate-400'}`} />
        实时行情
      </span>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-4 gap-y-1">
        {items.map((it) => (
          <span key={it.ts_code} className="flex items-baseline gap-1.5 text-xs">
            <span className="text-slate-500 dark:text-slate-400">{it.name || it.ts_code}</span>
            <span className={`font-semibold tabular-nums ${pctTone(it.pct_chg)}`}>{fmtNum(it.price, 2)}</span>
            <span className={`text-[11px] tabular-nums ${pctTone(it.pct_chg)}`}>{fmtPct(it.pct_chg)}</span>
          </span>
        ))}
      </div>
      <span className="text-[10px] text-slate-400 dark:text-slate-500">
        {payload?.as_of ? `${String(payload.as_of).slice(11, 19)} · ${payload.source || '实时'} · 10 秒刷新` : '10 秒刷新'}
        {ticker.error ? '（实时源不可用，已用缓存）' : ''}
      </span>
    </Card>
  )
}

/* ------------------------------------------------------------------------ 页面 */

export default function DashboardPage() {
  const { data, loading, error, reload } = useApi(() => api.get('/dashboard/overview'), [])

  // 静默轮询：已有数据时不再切换到 loading 态，避免闪烁
  React.useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      api
        .get('/dashboard/summary')
        .catch(() => {})
      reload()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [reload])

  const refresh = () => api.get('/dashboard/overview', { refresh: 1 }).then(reload).catch(() => reload())

  return (
    <PageShell
      title="总台"
      subtitle={
        data
          ? data.live
            ? `实时数据 ${fmtDate(data.trade_date)} · 全市场快照 ${String(data.as_of || '').slice(11, 19)} · 生成于 ${data.generated_at?.replace('T', ' ')}`
            : `日线数据 ${fmtDate(data.trade_date)}（T-1）· 上方实时条为盘中快照 · 生成于 ${data.generated_at?.replace('T', ' ')}`
          : '行情看板 · 一屏掌握当日大盘'
      }
      actions={
        <div className="flex items-center gap-2">
          <span className="hidden text-[11px] text-slate-400 sm:inline">每 60 秒自动刷新</span>
          <button
            type="button"
            onClick={refresh}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700"
          >
            立即刷新
          </button>
        </div>
      }
    >
      <RealtimeTicker />
      {!data ? (
        <AsyncBlock loading={loading} error={error} onRetry={reload} loadingLabel="大盘数据加载中（首次约 30 秒）" />
      ) : (
        <div className="space-y-4">
          {/* 指数卡片 */}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            {(data.indexes || []).map((it) => (
              <IndexCard key={it.ts_code} item={it} />
            ))}
          </div>

          <QuantPanel />

          {/* 涨跌分布 + 情绪 */}
          <div className="grid gap-4 xl:grid-cols-3">
            <div className="xl:col-span-2">
              <BreadthPanel breadth={data.breadth} />
            </div>
            <SentimentPanel sentiment={data.sentiment} />
          </div>

          {/* 梯队 + 榜单 */}
          <div className="grid gap-4 xl:grid-cols-3">
            <LadderPanel ladder={data.ladder} />
            <div className="xl:col-span-2">
              <RankPanel rankings={data.rankings} />
            </div>
          </div>

          {/* 板块热度 */}
          <div className="grid gap-4 xl:grid-cols-2">
            <BoardPanel title="概念热度" subtitle="按当日涨跌幅" data={data.concepts} kind="concept" />
            <BoardPanel title="行业热度" subtitle="按当日涨跌幅" data={data.industries} kind="industry" />
          </div>

          {/* 成交额 + 数据源 */}
          <div className="grid gap-4 xl:grid-cols-3">
            <div className="xl:col-span-2">
              <AmountPanel series={data.amount_series} />
            </div>
            <SourcesPanelBridge />
          </div>
        </div>
      )}
    </PageShell>
  )
}

/** 数据源状态单独取一次，避免重复拉全量 overview */
function SourcesPanelBridge() {
  const { data, loading, error, reload } = useApi(() => api.get('/system/data-sources'), [])
  return (
    <AsyncBlock loading={loading} error={error} onRetry={reload} loadingLabel="数据源状态加载中">
      {data ? <SourcesPanel sources={data} /> : null}
    </AsyncBlock>
  )
}
