import React, { useMemo, useState } from 'react'
import Chart, { ACCENT, TIP } from '../components/Chart.jsx'
import { AsyncBlock, Badge, Card, PageShell, SectionHeader, Tabs } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDateTime, fmtNum, fmtPct, pctTone } from '../lib/format.js'

const MARKETS = [
  { value: 'us', label: '美股', symbols: ['AAPL.US', 'MSFT.US', 'NVDA.US', 'TSLA.US'] },
  { value: 'hk', label: '港股', symbols: ['0700.HK', '9988.HK', '3690.HK'] },
  { value: 'jp', label: '日股', symbols: ['7203.T', '6758.T', '9984.T'] },
  { value: 'kr', label: '韩股', symbols: ['005930.KS', '000660.KS'] },
  { value: 'cn', label: 'A股', symbols: ['600519.SH', '000001.SZ'] },
]

const num = (v) => {
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

export default function GlobalMarketPage() {
  const [market, setMarket] = useState('us')
  const [input, setInput] = useState(MARKETS[0].symbols.join(','))
  const [selected, setSelected] = useState('')
  const symbols = useMemo(() => input.split(/[,，\s]+/).filter(Boolean), [input])
  const q = useApi(() => api.get('/market-data/global/quotes', { symbols: symbols.join(',') }), [symbols.join(',')])
  const h = useApi(() => api.get('/market-data/global/history', { symbol: selected, days: 250 }), [selected], { enabled: !!selected })

  const items = q.data?.items || []
  const history = h.data?.items || []
  const option = {
    tooltip: { ...TIP, trigger: 'axis' },
    grid: { left: 44, right: 18, top: 16, bottom: 28 },
    xAxis: { type: 'category', data: history.map((x) => x.date), axisLabel: { color: '#94a3b8', fontSize: 9 } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: '#94a3b8', fontSize: 9 }, splitLine: { lineStyle: { color: 'rgba(148,163,184,.14)' } } },
    series: [{ type: 'line', data: history.map((x) => x.close), showSymbol: false, smooth: true, lineStyle: { color: ACCENT, width: 2 }, areaStyle: { color: 'rgba(99,102,241,.12)' } }],
  }

  const switchMarket = (value) => {
    const m = MARKETS.find((x) => x.value === value)
    setMarket(value)
    setInput(m.symbols.join(','))
    setSelected('')
  }

  return (
    <PageShell
      title="全球行情"
      subtitle="A股 / 港股 / 美股 / 日股 / 韩股 · 腾讯行情源 · 历史深度以数据源为准"
      actions={<Tabs tabs={MARKETS.map((m) => ({ value: m.value, label: m.label }))} value={market} onChange={switchMarket} />}
    >
      <div className="space-y-4">
        <Card>
          <SectionHeader title="市场代码" subtitle="输入逗号分隔代码，例如 AAPL.US、0700.HK、7203.T、005930.KS" />
          <div className="mt-3 flex flex-wrap gap-2">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              className="min-w-[280px] flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-mono dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
              placeholder="AAPL.US,0700.HK"
            />
            <button onClick={q.reload} className="rounded-lg bg-brand-600 px-3 py-2 text-xs font-medium text-white hover:bg-brand-700">
              刷新行情
            </button>
          </div>
        </Card>

        <AsyncBlock loading={q.loading && !items.length} error={q.error} onRetry={q.reload} empty={!q.loading && !items.length} emptyText="暂无行情" loadingLabel="全球行情加载中">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {items.map((it) => (
              <Card key={it.symbol} className={`cursor-pointer ${selected === it.symbol ? 'ring-2 ring-brand-400/50' : ''}`}>
                <button type="button" className="w-full text-left" onClick={() => setSelected(it.symbol)}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{it.name}</p>
                      <p className="font-mono text-[10px] text-slate-400">{it.symbol} · {it.market} · {it.currency}</p>
                    </div>
                    <Badge tone={(num(it.pct_chg) || 0) >= 0 ? 'rose' : 'emerald'}>{fmtPct(it.pct_chg)}</Badge>
                  </div>
                  <p className={`mt-2 text-2xl font-semibold tabular-nums ${pctTone(it.pct_chg)}`}>{fmtNum(it.price, 2)}</p>
                  <p className="mt-0.5 text-[10px] text-slate-400">高 {fmtNum(it.high, 2)} · 低 {fmtNum(it.low, 2)}</p>
                  <p className="mt-1 text-[10px] text-slate-400">{it.as_of ? fmtDateTime(it.as_of) : '—'}</p>
                </button>
              </Card>
            ))}
          </div>
        </AsyncBlock>

        {selected ? (
          <Card>
            <SectionHeader title={`${selected} 历史走势`} subtitle={`${history.length} 根日线 · 数据源 ${itSource(items, selected)}`} />
            {h.loading ? <p className="mt-3 text-xs text-slate-400">历史加载中…</p> : history.length ? <Chart option={option} height={300} /> : <p className="mt-3 text-xs text-slate-400">该市场历史深度不足，暂不展示 K 线。</p>}
          </Card>
        ) : (
          <p className="text-[11px] text-slate-400">点击上方卡片查看历史走势。免费源对美股/日股/韩股历史深度有限，不会伪造长历史。</p>
        )}
      </div>
    </PageShell>
  )
}

function itSource(items, symbol) {
  return (items.find((x) => x.symbol === symbol) || {}).source || '—'
}
