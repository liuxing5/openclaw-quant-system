/**
 * 个股分析（/stock-analysis 搜索选股、/stock-analysis/:symbol 详情）：
 *
 * - 无 symbol：大搜索框（防抖调 securities/search）+ 今日焦点 + 最近查看。
 * - 有 symbol：档案头卡 + 日线 K 线（蜡烛 + 成交量副图 + MA5/10/20）+ 关键指标栅格 +
 *   52 周高低 / 区间涨跌 / 走势 Sparkline。
 *
 * 为什么均线、区间涨跌都在前端算：日线接口最多回 250 根，量级很小，
 * 直接在前端算可以同时适配 `series`（现网）与契约里的 `bars` 两种字段名。
 * K 线用到 candlestick 系列，Chart.jsx 按需注册里没有它，这里补注册一次（模块级单例）。
 */
import React, { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { CandlestickChart } from 'echarts/charts'
import * as echarts from 'echarts/core'
import Chart, { ACCENT, DOWN, DOWN_SOFT, Sparkline, TIP, UP, UP_SOFT } from '../components/Chart.jsx'
import { AsyncBlock, Badge, Card, PageShell, SectionHeader, TextLink } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { boardOf, fmtAmount, fmtDate, fmtNum, fmtPct, marketTag, pctTone } from '../lib/format.js'

// echarts/core 与 Chart.jsx 共享同一模块实例，注册一次即可让 <Chart> 画 K 线
echarts.use([CandlestickChart])

const RECENT_KEY = 'qd.stock.recent'

/** 原站「今日市场焦点」固定标的，缺接口时也总有可点的入口 */
const HOT_STOCKS = [
  { code: '600519', name: '贵州茅台' },
  { code: '300750', name: '宁德时代' },
  { code: '300308', name: '中际旭创' },
  { code: '300033', name: '同花顺' },
  { code: '300059', name: '东方财富' },
  { code: '688256', name: '寒武纪' },
]

const num = (v) => (v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? null : Number(v))

const pick = (o, keys) => {
  if (!o) return null
  for (const k of keys) {
    if (o[k] !== undefined && o[k] !== null && o[k] !== '') return o[k]
  }
  return null
}

const asItems = (d) => (Array.isArray(d) ? d : (d && d.items) || [])

function readRecent() {
  try {
    const raw = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]')
    return Array.isArray(raw) ? raw : []
  } catch {
    return []
  }
}

function pushRecent(code, name) {
  try {
    const list = readRecent().filter((x) => x.code !== code)
    list.unshift({ code, name })
    localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, 10)))
  } catch {
    // 隐私模式写入失败不影响浏览
  }
}

/** 成交量轴/提示的友好格式：手 → 万/亿 */
function fmtVol(v) {
  const x = num(v)
  if (x === null) return '—'
  if (Math.abs(x) >= 1e8) return `${(x / 1e8).toFixed(2)}亿`
  if (Math.abs(x) >= 1e4) return `${(x / 1e4).toFixed(0)}万`
  return fmtNum(x, 0)
}

/* --------------------------------------------------------------- 搜索选股页 */

function StockRow({ item, onPick }) {
  const code = String(pick(item, ['ts_code', 'symbol', 'code']) || '').split('.')[0]
  const name = pick(item, ['stock_name', 'name']) || code
  const market = pick(item, ['market']) || ''
  return (
    <button
      type="button"
      onClick={() => onPick(code, name)}
      className="flex w-full items-center justify-between gap-3 rounded-lg border border-slate-100 px-3 py-2 text-left transition hover:border-brand-300 hover:bg-brand-50/50 dark:border-slate-800 dark:hover:border-brand-500/40 dark:hover:bg-brand-500/10"
    >
      <span className="flex min-w-0 items-center gap-2">
        <span className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{name}</span>
        <span className="font-mono text-xs tabular-nums text-slate-400">{code}</span>
        {market ? <Badge tone="slate">{market}</Badge> : null}
        {item.is_st ? <Badge tone="amber">ST</Badge> : null}
      </span>
      <span className="shrink-0 text-xs text-brand-600 dark:text-brand-400">进入分析 →</span>
    </button>
  )
}

function StockSearchView() {
  const navigate = useNavigate()
  const [text, setText] = useState('')
  const [kw, setKw] = useState('')
  const [recent, setRecent] = useState(() => readRecent())

  // 300ms 防抖，避免每敲一个字都打一次搜索接口
  useEffect(() => {
    const id = setTimeout(() => setKw(text.trim()), 300)
    return () => clearTimeout(id)
  }, [text])

  const searchQ = useApi(
    () => api.get('/market-data/securities/search', { keyword: kw, q: kw, limit: 20 }),
    [kw],
    { enabled: kw.length > 0 },
  )
  const results = asItems(searchQ.data)

  const pickStock = (code, name) => {
    pushRecent(code, name)
    setRecent(readRecent())
    navigate(`/stock-analysis/${code}`)
  }

  const submit = (e) => {
    e.preventDefault()
    const hit = results[0]
    if (hit) {
      pickStock(String(pick(hit, ['ts_code', 'symbol', 'code']) || '').split('.')[0], pick(hit, ['stock_name', 'name']))
    } else if (/^\d{6}$/.test(kw)) {
      pickStock(kw, kw)
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <Card className="p-6">
        <SectionHeader
          title="AI 个股分析"
          subtitle="输入股票代码或名称，进入 K 线、量价与关键指标分析；输入后按 Enter 直接进入"
        />
        <form onSubmit={submit} className="mt-4 flex gap-2">
          <div className="relative flex-1">
            <svg
              className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              aria-hidden="true"
            >
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-3.5-3.5" strokeLinecap="round" />
            </svg>
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="如 600519 / 贵州茅台"
              aria-label="股票代码或名称"
              className="w-full rounded-xl border border-slate-300 bg-white py-3 pl-9 pr-3 text-sm text-slate-800 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
            />
          </div>
          <button
            type="submit"
            className="rounded-xl bg-brand-600 px-4 text-sm font-medium text-white transition hover:bg-brand-700"
          >
            分析
          </button>
        </form>

        {kw ? (
          <div className="mt-4">
            <AsyncBlock
              loading={searchQ.loading && !searchQ.data}
              error={searchQ.error}
              onRetry={searchQ.reload}
              loadingLabel="搜索中"
              empty={!searchQ.loading && !searchQ.error && results.length === 0}
              emptyText={`没有匹配「${kw}」的个股`}
              emptyHint="试试 6 位代码，或只输入名称前几个字"
            >
              <div className="space-y-2">
                {results.map((r, i) => (
                  <StockRow key={`${pick(r, ['ts_code', 'code']) || i}`} item={r} onPick={pickStock} />
                ))}
              </div>
            </AsyncBlock>
          </div>
        ) : null}
      </Card>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <SectionHeader title="🔥 今日市场焦点" subtitle="热门资金与高关注标的，点击即可进入分析" />
          <div className="mt-3 grid grid-cols-2 gap-2">
            {HOT_STOCKS.map((s) => (
              <button
                key={s.code}
                type="button"
                onClick={() => pickStock(s.code, s.name)}
                className="rounded-lg border border-slate-100 px-3 py-2 text-left transition hover:border-rose-300 hover:bg-rose-50/60 dark:border-slate-800 dark:hover:border-rose-500/40 dark:hover:bg-rose-500/10"
              >
                <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{s.name}</p>
                <p className="font-mono text-xs tabular-nums text-slate-400">{s.code}</p>
              </button>
            ))}
          </div>
        </Card>

        <Card>
          <SectionHeader title="最近查看" subtitle={`本地记录 ${recent.length} / 10 条`} />
          <div className="mt-3">
            {recent.length === 0 ? (
              <p className="rounded-lg border border-dashed border-slate-200 px-3 py-6 text-center text-xs text-slate-400 dark:border-slate-700">
                暂无浏览记录，搜索并进入详情后会自动加入这里。
              </p>
            ) : (
              <div className="space-y-2">
                {recent.map((r) => (
                  <StockRow key={r.code} item={{ ts_code: r.code, stock_name: r.name }} onPick={pickStock} />
                ))}
              </div>
            )}
          </div>
        </Card>
      </div>
    </div>
  )
}

/* --------------------------------------------------------------- 详情 · K 线 */

function KLinePanel({ bars }) {
  const option = useMemo(() => {
    const dates = bars.map((b) => b.date.slice(5))
    const closes = bars.map((b) => b.close)
    const ma = (k) =>
      bars.map((_, i) => {
        if (i < k - 1) return null
        let sum = 0
        for (let j = i - k + 1; j <= i; j += 1) sum += closes[j]
        return Math.round((sum / k) * 100) / 100
      })
    const vols = bars.map((b) => ({
      value: b.volume || 0,
      itemStyle: {
        color: (b.close ?? 0) >= (b.open ?? 0) ? 'rgba(244,63,94,0.65)' : 'rgba(16,185,129,0.65)',
      },
    }))

    return {
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      tooltip: {
        ...TIP,
        formatter: (ps) => {
          const arr = Array.isArray(ps) ? ps : [ps]
          const head = arr[0]?.axisValue || ''
          const lines = arr.map((p) => {
            const v = p.value
            if (p.seriesType === 'candlestick') {
              const [o, c, l, h] = Array.isArray(v) ? v : []
              return `${p.marker}开 <b>${fmtNum(o, 2)}</b> 收 <b>${fmtNum(c, 2)}</b> 低 <b>${fmtNum(l, 2)}</b> 高 <b>${fmtNum(h, 2)}</b>`
            }
            if (p.seriesName === '成交量') return `${p.marker}成交量 <b>${fmtVol(v)}</b>`
            return `${p.marker}${p.seriesName} <b>${v === null || v === undefined ? '—' : fmtNum(v, 2)}</b>`
          })
          return [head, ...lines].join('<br/>')
        },
      },
      legend: {
        data: ['MA5', 'MA10', 'MA20'],
        top: 0,
        right: 8,
        textStyle: { color: '#94a3b8', fontSize: 11 },
        itemWidth: 14,
        itemHeight: 8,
      },
      grid: [
        { left: 52, right: 16, top: 24, height: '56%' },
        { left: 52, right: 16, top: '72%', height: '14%' },
      ],
      xAxis: [
        {
          type: 'category',
          data: dates,
          boundaryGap: true,
          axisLine: { lineStyle: { color: 'rgba(148,163,184,0.35)' } },
          axisLabel: { show: false },
          axisTick: { show: false },
        },
        {
          type: 'category',
          gridIndex: 1,
          data: dates,
          boundaryGap: true,
          axisLine: { lineStyle: { color: 'rgba(148,163,184,0.35)' } },
          axisLabel: { color: '#94a3b8', fontSize: 10 },
          axisTick: { show: false },
        },
      ],
      yAxis: [
        {
          scale: true,
          axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v) => Number(v).toLocaleString('zh-CN') },
          splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } },
        },
        {
          gridIndex: 1,
          scale: true,
          splitNumber: 2,
          axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v) => fmtVol(v) },
          splitLine: { show: false },
        },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
        {
          type: 'slider',
          xAxisIndex: [0, 1],
          top: '90%',
          height: 16,
          borderColor: 'rgba(148,163,184,0.3)',
          fillerColor: 'rgba(99,102,241,0.12)',
          handleStyle: { color: ACCENT },
          textStyle: { color: '#94a3b8', fontSize: 10 },
        },
      ],
      series: [
        {
          name: '日K',
          type: 'candlestick',
          data: bars.map((b) => [b.open, b.close, b.low, b.high]),
          itemStyle: {
            color: UP,
            color0: DOWN,
            borderColor: UP,
            borderColor0: DOWN,
          },
        },
        {
          name: '成交量',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: vols,
          barMaxWidth: 12,
        },
        { name: 'MA5', type: 'line', data: ma(5), showSymbol: false, smooth: true, lineStyle: { width: 1.4, color: UP_SOFT } },
        { name: 'MA10', type: 'line', data: ma(10), showSymbol: false, smooth: true, lineStyle: { width: 1.4, color: ACCENT } },
        { name: 'MA20', type: 'line', data: ma(20), showSymbol: false, smooth: true, lineStyle: { width: 1.4, color: DOWN_SOFT } },
      ],
    }
  }, [bars])

  return <Chart option={option} height={430} className="mt-2" />
}

/* --------------------------------------------------------------- 详情 · 指标 */

function MetricTile({ label, value, tone = null }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/60 px-2.5 py-2 dark:border-slate-700 dark:bg-slate-800/50">
      <p className="text-[10px] text-slate-500 dark:text-slate-400">{label}</p>
      <p className={`mt-0.5 truncate text-sm font-semibold tabular-nums ${tone || 'text-slate-800 dark:text-slate-100'}`}>
        {value}
      </p>
    </div>
  )
}

function StockDetailView({ symbol }) {
  const dataQ = useApi(async () => {
    // 行情日线是主数据（失败即整页 error 态）；档案接口若尚未上线则降级用日线里的字段
    const [daily, profile] = await Promise.all([
      api.get('/market-data/quotes/daily', { symbol, limit: 250, ts_code: symbol, days: 250 }),
      api.get(`/market-data/securities/${symbol}`).catch(() => null),
    ])
    return { daily, profile }
  }, [symbol])

  // 记忆化：dataQ.data 未就绪时也不会每次渲染都换引用，避免下面的 memo 反复重算
  const daily = useMemo(() => (dataQ.data && dataQ.data.daily) || {}, [dataQ.data])
  const profile = (dataQ.data && dataQ.data.profile) || null

  const bars = useMemo(() => {
    const raw = daily.series || daily.bars || daily.items || (Array.isArray(daily) ? daily : [])
    return raw
      .map((b) => ({
        date: String(pick(b, ['date', 'trade_date']) || ''),
        open: num(b.open),
        high: num(b.high),
        low: num(b.low),
        close: num(b.close),
        volume: num(b.volume),
        amount: num(b.amount),
        pct_chg: num(pick(b, ['pct_chg', 'change_pct'])),
        turnover_rate: num(pick(b, ['turnover_rate'])),
        pre_close: num(pick(b, ['pre_close', 'prev_close'])),
        limit_up: num(pick(b, ['limit_up_price', 'limit_up'])),
        limit_down: num(pick(b, ['limit_down_price', 'limit_down'])),
      }))
      .filter((b) => b.date && b.close !== null)
      .sort((a, b) => (a.date < b.date ? -1 : 1))
  }, [daily])

  // 进过详情就记一笔「最近查看」，回搜索页能看到
  useEffect(() => {
    if (daily && daily.name) pushRecent(symbol, daily.name)
  }, [symbol, daily])

  // 52 周高低的取数窗口：按最后一根日线往前推一年（不足一年用全部）
  const yearBars = useMemo(() => {
    if (!bars.length) return []
    const end = new Date(bars[bars.length - 1].date)
    const from = new Date(end.getTime() - 365 * 86400000)
    const inRange = bars.filter((b) => new Date(b.date) >= from)
    return inRange.length ? inRange : bars
  }, [bars])

  // 四态之一：接口 404/500 时给可重试的错误块，绝不白屏（所有 hook 必须在此之前调用完）
  if (dataQ.error) {
    return (
      <PageShell title="个股分析" subtitle={`代码 ${symbol}`}>
        <AsyncBlock loading={dataQ.loading} error={dataQ.error} onRetry={dataQ.reload} loadingLabel="个股数据加载中">
          <Card>
            <p className="text-sm text-slate-500 dark:text-slate-400">暂无该标的数据</p>
          </Card>
        </AsyncBlock>
      </PageShell>
    )
  }

  const last = bars[bars.length - 1] || {}
  const latest = profile?.latest || daily.latest || {}
  const name = profile?.name || daily.name || symbol
  const market = profile?.market || daily.market || ''
  const isSt = profile?.is_st ?? daily.is_st
  const industry = profile?.industry || daily.industry
  const listDate = profile?.list_date || daily.list_date

  const price = num(latest.close) ?? last.close
  const pct = num(latest.pct_chg) ?? last.pct_chg
  const preClose = num(latest.prev_close) ?? last.pre_close
  const chg = price !== null && preClose !== null ? Math.round((price - preClose) * 100) / 100 : null
  const quoteTime = daily.quote_time || (last.date ? `截至 ${last.date} 收盘` : '')

  // 52 周高低与区间涨跌：全部由日线数组前端计算（≤250 根）
  const high52 = yearBars.reduce((m, b) => (b.high !== null && b.high > m ? b.high : m), -Infinity)
  const low52 = yearBars.reduce((m, b) => (b.low !== null && b.low < m ? b.low : m), Infinity)

  const rangeReturn = (k) => {
    if (bars.length < 2) return null
    const from = bars[Math.max(0, bars.length - 1 - k)].close
    const to = bars[bars.length - 1].close
    if (from === null || !from || to === null) return null
    return ((to / from - 1) * 100)
  }
  const ranges = [
    { label: '近 5 日', v: rangeReturn(5) },
    { label: '近 20 日', v: rangeReturn(20) },
    { label: '近 60 日', v: rangeReturn(60) },
    { label: '近 120 日', v: rangeReturn(120) },
    { label: '近 250 日', v: rangeReturn(250) },
  ]

  const metrics = [
    { label: '今开', value: fmtNum(num(latest.open) ?? last.open, 2), raw: num(latest.open) ?? last.open, base: preClose },
    { label: '最高', value: fmtNum(num(latest.high) ?? last.high, 2), raw: num(latest.high) ?? last.high, base: preClose },
    { label: '最低', value: fmtNum(num(latest.low) ?? last.low, 2), raw: num(latest.low) ?? last.low, base: preClose },
    { label: '昨收', value: fmtNum(preClose, 2) },
    { label: '成交额', value: fmtAmount(num(latest.amount) ?? last.amount) },
    { label: '换手率', value: num(latest.turnover_rate) ?? last.turnover_rate ? `${fmtNum(num(latest.turnover_rate) ?? last.turnover_rate, 2)}%` : '—' },
    { label: '量比', value: fmtNum(num(pick(latest, ['volume_ratio', 'vol_ratio'])), 2) },
    { label: 'PE(动)', value: fmtNum(num(pick(latest, ['pe', 'pe_ttm', 'pe_lyr'])), 2) },
    { label: 'PB', value: fmtNum(num(pick(latest, ['pb'])), 2) },
    { label: '总市值', value: fmtAmount(num(pick(latest, ['total_mv', 'total_market_cap', 'market_cap']))) },
    { label: '流通市值', value: fmtAmount(num(pick(latest, ['float_mv', 'float_market_cap']))) },
    { label: '涨停价', value: fmtNum(last.limit_up, 2), tone: 'text-rose-600 dark:text-rose-400' },
    { label: '跌停价', value: fmtNum(last.limit_down, 2), tone: 'text-emerald-600 dark:text-emerald-400' },
  ]

  const spark = bars.map((b) => b.close).filter((v) => v !== null)

  return (
    <div className="space-y-4">
      {/* 档案头卡 */}
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">{name}</h2>
              <span className="font-mono text-sm tabular-nums text-slate-500">
                {symbol}
                {market ? `.${market}` : ''}
              </span>
              <Badge tone="blue">{boardOf(`${symbol}.${market}`)}</Badge>
              <Badge tone="slate">{marketTag(`${symbol}.${market}`) || 'A股'}</Badge>
              {isSt ? <Badge tone="amber">ST</Badge> : null}
            </div>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
              <span>上市 {listDate ? fmtDate(listDate) : '—'}</span>
              <span>行业 {industry || '—'}</span>
              <span>{quoteTime}</span>
              {profile ? null : <span className="text-amber-500">档案接口暂不可用，已用行情数据降级</span>}
            </div>
          </div>
          <div className="text-right">
            <p className={`text-3xl font-bold tabular-nums ${pctTone(pct)}`}>{fmtNum(price, 2)}</p>
            <p className={`mt-0.5 text-sm font-medium tabular-nums ${pctTone(pct)}`}>
              {chg === null ? '—' : `${chg > 0 ? '+' : ''}${fmtNum(chg, 2)}`} · {fmtPct(pct)}
            </p>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-3">
        {/* K 线 */}
        <Card className="xl:col-span-2">
          <SectionHeader
            title="专业 K 线量价分析"
            subtitle={`最近 ${bars.length} 个交易日 · 当前价 ${fmtNum(price, 2)}（前复权）`}
            action={<TextLink to="/market-env">大盘环境 →</TextLink>}
          />
          <AsyncBlock
            loading={dataQ.loading && bars.length === 0}
            error={dataQ.error}
            onRetry={dataQ.reload}
            loadingLabel="日线数据加载中"
            empty={!dataQ.loading && !dataQ.error && bars.length === 0}
            emptyText="该标的暂无日线数据"
          >
            <KLinePanel bars={bars} />
          </AsyncBlock>
          <div className="mt-2 flex items-center justify-between gap-3 text-[11px] text-slate-500 dark:text-slate-400">
            <span>红涨绿跌 · 滚轮缩放，拖拽平移</span>
            <span className="flex items-center gap-2">
              区间涨跌
              {ranges.map((r) => (
                <span key={r.label} className="tabular-nums" title={r.label}>
                  {r.label.replace('近 ', '')} {fmtPct(r.v)}
                </span>
              ))}
            </span>
          </div>
        </Card>

        {/* 走势 + 52 周 */}
        <Card className="flex flex-col">
          <SectionHeader title="走势与区间" subtitle="近 250 个交易日收盘价" />
          <Sparkline points={spark} height={96} className="mt-3 w-full" />
          <div className="mt-3 grid grid-cols-2 gap-2 text-center">
            <div className="rounded-lg bg-slate-50 py-2 dark:bg-slate-800/60">
              <p className="text-[10px] text-slate-400">52 周最高</p>
              <p className="text-sm font-semibold tabular-nums text-rose-600 dark:text-rose-400">
                {Number.isFinite(high52) ? fmtNum(high52, 2) : '—'}
              </p>
            </div>
            <div className="rounded-lg bg-slate-50 py-2 dark:bg-slate-800/60">
              <p className="text-[10px] text-slate-400">52 周最低</p>
              <p className="text-sm font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">
                {Number.isFinite(low52) ? fmtNum(low52, 2) : '—'}
              </p>
            </div>
          </div>
          <div className="mt-3 space-y-1.5">
            {ranges.map((r) => (
              <div key={r.label} className="flex items-center justify-between text-xs">
                <span className="text-slate-500 dark:text-slate-400">{r.label}</span>
                <span className={`font-medium tabular-nums ${pctTone(r.v)}`}>{fmtPct(r.v)}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* 关键指标栅格 */}
      <Card>
        <SectionHeader
          title="关键指标"
          subtitle={`数据日 ${last.date || '—'} · 缺失字段显示为 —`}
        />
        <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-7">
          {metrics.map((m) => (
            <MetricTile
              key={m.label}
              label={m.label}
              value={m.value}
              tone={
                m.base && m.raw !== null && m.raw !== undefined && m.base
                  ? pctTone(m.raw - m.base)
                  : m.tone || null
              }
            />
          ))}
        </div>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------- 页面 */

export default function StockAnalysisPage() {
  const { symbol } = useParams()
  return (
    <PageShell
      title="个股分析"
      subtitle={symbol ? `代码 ${symbol} · K 线与关键指标` : '输入代码或名称，快速进入 K 线与关键指标分析'}
      actions={symbol ? <TextLink to="/stock-analysis">← 换一只</TextLink> : null}
    >
      {symbol ? <StockDetailView symbol={symbol} /> : <StockSearchView />}
    </PageShell>
  )
}
