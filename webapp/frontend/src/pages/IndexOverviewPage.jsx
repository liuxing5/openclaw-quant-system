/**
 * 指数总览（/index）：六大指数卡片 + 可切换的指数日线折线。
 *
 * 为什么只打一个接口：`GET /index/summary` 已经按指数带了近 30 个交易日的 `curve`，
 * 卡片迷你走势和下方日线图共用这一份序列即可；若再为 6 只指数各请求一次日线，
 * 既放大首屏耗时，也更容易踩到尚未上线的 `/index/quotes/{code}`。
 *
 * 四态（加载 / 可重试错误 / 空 / 正常）全部显式处理，字段缺失一律降级成「—」，
 * 接口 404/500 时只在本页出错误块，绝不白屏。
 */
import React, { useEffect, useMemo, useState } from 'react'
import Chart, { DOWN, Sparkline, TIP, UP } from '../components/Chart.jsx'
import { AsyncBlock, Badge, Card, EmptyState, PageShell, SectionHeader, Tabs } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtDate, fmtNum, fmtPct, pctTone } from '../lib/format.js'

const POLL_MS = 60_000

/** items 为空时的兜底指数池：保证切换器与图表始终有可选对象（顺序与总台一致） */
const FALLBACK_INDEXES = [
  { ts_code: '000001.SH', name: '上证指数' },
  { ts_code: '399001.SZ', name: '深证成指' },
  { ts_code: '399006.SZ', name: '创业板指' },
  { ts_code: '000300.SH', name: '沪深300' },
  { ts_code: '000905.SH', name: '中证500' },
  { ts_code: '000688.SH', name: '科创50' },
]

/* ------------------------------------------------------------------ 字段兼容 */

function toNum(v) {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

/** 响应可能是数组，也可能是 {items|indexes|list}，统一取出列表 */
function pickItems(data) {
  if (Array.isArray(data)) return data.filter((x) => x && typeof x === 'object')
  if (!data || typeof data !== 'object') return []
  const list = data.items || data.indexes || data.list || []
  return Array.isArray(list) ? list.filter((x) => x && typeof x === 'object') : []
}

/**
 * 曲线字段名后端可能给 curve / sparkline / series，点位可能是对象也可能是纯数字，
 * 统一归一成 [{date, close}]，缺失日期时退化成空串（轴标签为空但线仍画得出来）。
 */
function toSeries(item) {
  const raw = item?.curve || item?.sparkline || item?.series || item?.history || []
  if (!Array.isArray(raw)) return []
  const out = []
  for (const p of raw) {
    if (typeof p === 'number' || typeof p === 'string') {
      const v = toNum(p)
      if (v !== null) out.push({ date: '', close: v })
      continue
    }
    if (!p || typeof p !== 'object') continue
    const v = toNum(p.close ?? p.value ?? p.y ?? p.price)
    if (v === null) continue
    out.push({ date: p.date || p.trade_date || p.day || '', close: v })
  }
  return out
}

/* ---------------------------------------------------------------- 指数卡片 */

function IndexCard({ item, active, onSelect }) {
  const pct = toNum(item.pct_chg)
  const tone = pctTone(pct)
  const points = toSeries(item).map((p) => p.close)
  const meta = [
    ['今开', fmtNum(item.open, 2)],
    ['最高', fmtNum(item.high, 2)],
    ['最低', fmtNum(item.low, 2)],
    ['成交额', fmtAmount(item.amount ?? item.turnover)],
  ]

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={active}
      className={`rounded-xl border p-3.5 text-left shadow-sm transition-colors ${
        active
          ? 'border-brand-500 bg-brand-50/70 dark:border-brand-400 dark:bg-brand-500/10'
          : 'border-slate-200 bg-white hover:border-slate-300 dark:border-slate-800 dark:bg-slate-900 dark:hover:border-slate-700'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-xs font-medium text-slate-500 dark:text-slate-400">{item.name || item.ts_code}</p>
          <p className={`mt-1 text-xl font-semibold tabular-nums ${tone}`}>{fmtNum(item.close ?? item.price, 2)}</p>
        </div>
        <Badge tone={pct === null ? 'slate' : pct > 0 ? 'rose' : pct < 0 ? 'emerald' : 'slate'}>
          {fmtPct(pct)}
        </Badge>
      </div>

      <Sparkline points={points} height={34} color={(pct ?? 0) >= 0 ? UP : DOWN} className="mt-2 w-full" />

      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[10px] text-slate-400 dark:text-slate-500">
        {meta.map(([label, value]) => (
          <p key={label} className="flex items-center justify-between gap-1">
            <span>{label}</span>
            <span className="tabular-nums text-slate-600 dark:text-slate-300">{value}</span>
          </p>
        ))}
      </div>
    </button>
  )
}

/* --------------------------------------------------------------- 指数日线图 */

function IndexTrendCard({ item, action = null }) {
  const series = useMemo(() => toSeries(item), [item])

  const option = useMemo(() => {
    const up = (toNum(item?.pct_chg) ?? 0) >= 0
    const lineColor = up ? UP : '#10b981'
    return {
      grid: { left: 58, right: 20, top: 26, bottom: 30 },
      tooltip: {
        ...TIP,
        trigger: 'axis',
        formatter: (ps) => {
          const arr = Array.isArray(ps) ? ps : [ps]
          const p = arr[0]
          if (!p) return ''
          return `${p.axisValue || ''}<br/>收盘 <b>${fmtNum(p.value, 2)}</b>`
        },
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: series.map((s) => (s.date ? s.date.slice(5) : '')),
        axisLine: { lineStyle: { color: 'rgba(148,163,184,0.35)' } },
        axisLabel: { color: '#94a3b8', fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        scale: true,
        axisLabel: { color: '#94a3b8', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } },
      },
      series: [
        {
          type: 'line',
          name: '收盘',
          data: series.map((s) => s.close),
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 2, color: lineColor },
          itemStyle: { color: lineColor },
          areaStyle: { color: up ? 'rgba(244,63,94,0.12)' : 'rgba(16,185,129,0.12)' },
        },
      ],
    }
  }, [series, item])

  const closes = series.map((s) => s.close)
  const high = closes.length ? Math.max(...closes) : null
  const low = closes.length ? Math.min(...closes) : null
  const first = closes[0]
  const last = closes[closes.length - 1]
  const chg = first && last ? ((last - first) / first) * 100 : null

  return (
    <Card>
      <SectionHeader
        title={`${item?.name || '指数'}日线`}
        subtitle={
          series.length
            ? `近 ${series.length} 个交易日 · 区间高 ${fmtNum(high, 2)} / 低 ${fmtNum(low, 2)}`
            : '区间收盘走势'
        }
        badge={
          series.length ? (
            <Badge tone={chg > 0 ? 'rose' : chg < 0 ? 'emerald' : 'slate'}>区间 {fmtPct(chg)}</Badge>
          ) : null
        }
        action={action}
      />
      <AsyncBlock
        empty={series.length < 2}
        emptyText="暂无该指数日线序列"
        emptyHint="summary 返回的 curve 为空或点位不足，接口补齐后会自动恢复"
      >
        <Chart option={option} height={300} className="mt-2" />
      </AsyncBlock>
    </Card>
  )
}

/* ---------------------------------------------------------------------- 页面 */

export default function IndexOverviewPage() {
  const { data, loading, error, reload } = useApi(() => api.get('/index/summary'), [])
  const items = useMemo(() => pickItems(data), [data])
  const options = items.length ? items : FALLBACK_INDEXES
  const [code, setCode] = useState(FALLBACK_INDEXES[0].ts_code)

  // 后端返回后把选中指数对齐到真实列表（首屏默认取第一只）
  useEffect(() => {
    if (!items.length) return
    setCode((prev) => (items.some((i) => i.ts_code === prev) ? prev : items[0].ts_code))
  }, [items])

  // 静默轮询：已有数据时不回到骨架态，避免整页闪烁；卸载时清理定时器
  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      reload()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [reload])

  const active = items.find((i) => i.ts_code === code) || options[0] || null
  const subtitle = data
    ? `六大指数行情 · 数据日 ${fmtDate(data.trade_date)}${data.generated_at ? ` · 生成于 ${String(data.generated_at).replace('T', ' ')}` : ''}`
    : '六大指数行情 · 点位 / 涨跌 / 量能一览'

  return (
    <PageShell
      title="指数总览"
      subtitle={subtitle}
      actions={<span className="text-[11px] text-slate-400">每 60 秒自动刷新</span>}
    >
      {!data ? (
        <AsyncBlock
          loading={loading}
          error={error}
          onRetry={reload}
          loadingLabel="指数数据加载中…"
          empty={!loading && !error}
          emptyText="暂无指数数据"
          emptyHint="index/summary 返回空响应，稍后重试"
        />
      ) : items.length === 0 ? (
        <EmptyState text="暂无指数数据" hint="接口已返回但未包含任何指数，可能尚未同步 index_quotes / index_basic" />
      ) : (
        <div className="space-y-4">
          {/* 六大指数卡片：点击卡片即可切换下方日线 */}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            {items.map((it) => (
              <IndexCard
                key={it.ts_code || it.name}
                item={it}
                active={active?.ts_code === it.ts_code}
                onSelect={() => setCode(it.ts_code)}
              />
            ))}
          </div>

          <IndexTrendCard
            item={active}
            action={
              <Tabs
                tabs={options.map((o) => ({ value: o.ts_code, label: o.name || o.ts_code }))}
                value={active?.ts_code}
                onChange={setCode}
              />
            }
          />
        </div>
      )}
    </PageShell>
  )
}
