/**
 * 财务指标（/finance/indicators）：单只股票的盈利 / 成长 / 偿债 / 估值快照。
 *
 * 数据来自 GET /finance/indicators（stock_fundamentals 最新一期 + 时间序列）。
 * 本地财务表覆盖有限（多数标的只有一期），因此页面：
 *   1. 指标卡缺值一律显示「—」，不拿 0 冒充；
 *   2. 估值（PE/PB/市值）从当日 daily_quotes 补，保证有数可看；
 *   3. 序列不足两期时明确提示「历史期数不足」，而不是画一条假曲线。
 */
import { useEffect, useMemo, useState } from 'react'
import Chart, { ACCENT, TIP } from '../components/Chart.jsx'
import {
  AsyncBlock,
  Badge,
  Button,
  Card,
  EmptyState,
  PageShell,
  SectionHeader,
  StatTile,
} from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtNum, fmtPct, pctTone } from '../lib/format.js'

const num = (v) => {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

const pctOrDash = (v, digits = 2) => (num(v) === null ? '—' : `${fmtNum(v, digits)}%`)

function MetricCard({ label, value, sub = null, tone = null }) {
  return <StatTile label={label} value={value} sub={sub} tone={tone} />
}

function normalizeSeries(series) {
  if (!series || typeof series !== 'object') return []
  const dates = series.report_date || []
  return dates.map((d, i) => ({
    report_date: d,
    revenue: num(series.revenue?.[i]),
    net_profit: num(series.net_profit?.[i]),
    gross_margin: num(series.gross_margin?.[i]),
    net_margin: num(series.net_margin?.[i]),
    eps: num(series.eps?.[i]),
    roe: num(series.roe?.[i]),
    debt_ratio: num(series.debt_ratio?.[i]),
    revenue_yoy: num(series.revenue_yoy?.[i]),
    profit_yoy: num(series.profit_yoy?.[i]),
  }))
}

function ReportChart({ rows }) {
  const option = useMemo(
    () => ({
      grid: { left: 52, right: 52, top: 34, bottom: 30 },
      tooltip: { ...TIP, trigger: 'axis' },
      legend: { top: 0, textStyle: { color: '#94a3b8', fontSize: 10 } },
      xAxis: {
        type: 'category',
        data: rows.map((r) => String(r.report_date).slice(0, 10)),
        axisLabel: { color: '#94a3b8', fontSize: 10 },
        axisLine: { lineStyle: { color: 'rgba(148,163,184,0.35)' } },
      },
      yAxis: [
        {
          type: 'value',
          name: '金额',
          axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v) => fmtAmount(v) },
          splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } },
        },
        {
          type: 'value',
          name: '毛利率',
          axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v) => `${v}%` },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: '营业收入',
          type: 'bar',
          data: rows.map((r) => r.revenue),
          itemStyle: { color: 'rgba(99,102,241,0.55)', borderRadius: [3, 3, 0, 0] },
          barMaxWidth: 26,
        },
        {
          name: '净利润',
          type: 'bar',
          data: rows.map((r) => r.net_profit),
          itemStyle: { color: 'rgba(244,63,94,0.55)', borderRadius: [3, 3, 0, 0] },
          barMaxWidth: 26,
        },
        {
          name: '毛利率',
          type: 'line',
          yAxisIndex: 1,
          data: rows.map((r) => r.gross_margin),
          showSymbol: true,
          lineStyle: { color: ACCENT, width: 2 },
          itemStyle: { color: ACCENT },
          connectNulls: true,
        },
      ],
    }),
    [rows],
  )
  return <Chart option={option} height={300} />
}

export default function FinanceIndicatorsPage() {
  const [symbol, setSymbol] = useState('600519.SH')
  const [input, setInput] = useState('600519')
  const [errorText, setErrorText] = useState(null)

  const { data, loading, error, reload } = useApi(
    () => api.get('/finance/indicators', { ts_code: symbol }),
    [symbol],
  )
  const quotes = useApi(() => api.get('/market-data/quotes/daily', { symbol, limit: 5 }), [symbol], {
    enabled: !!symbol,
  })

  useEffect(() => {
    setInput(symbol.split('.')[0])
  }, [symbol])

  async function submit(e) {
    e?.preventDefault?.()
    const kw = input.trim()
    if (!kw) return
    setErrorText(null)
    try {
      const res = await api.get('/market-data/securities/search', { keyword: kw, q: kw, limit: 1 })
      const hit = (res?.items || [])[0]
      if (!hit?.ts_code) {
        setErrorText(`没有找到「${kw}」对应的股票`)
        return
      }
      setSymbol(hit.ts_code)
    } catch (e2) {
      setErrorText(e2?.message || '搜索失败')
    }
  }

  const latest = data?.latest || {}
  const series = useMemo(() => normalizeSeries(data?.series), [data])
  const latestQuote = quotes.data?.latest || {}
  const pe = num(latest.pe_ratio ?? latestQuote.pe_ratio)
  const pb = num(latest.pb_ratio ?? latestQuote.pb_ratio)

  const rows = [...series].reverse()

  return (
    <PageShell
      title="财务指标"
      subtitle={`${latest.stock_name || data?.ts_code || symbol} · 最新报告期 ${latest.report_date || '—'} · 盈利 / 成长 / 偿债 / 估值`}
      actions={
        <form onSubmit={submit} className="flex items-center gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="代码或名称，如 600519"
            className="w-44 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs text-slate-700 outline-none focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
          />
          <Button size="sm" type="submit">
            查询
          </Button>
        </form>
      }
    >
      {errorText ? (
        <Card className="border-amber-300 bg-amber-50/60 text-xs text-amber-700 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-300">
          {errorText}
        </Card>
      ) : null}

      {!data ? (
        <AsyncBlock
          loading={loading}
          error={error}
          onRetry={reload}
          loadingLabel="财务指标加载中…"
          empty={!loading && !error}
          emptyText="暂无财务指标"
          emptyHint="该标的可能没有同步 stock_fundamentals 记录"
        />
      ) : !latest.ts_code ? (
        <Card>
          <EmptyState text="暂无财务数据" hint="stock_fundamentals 中没有该标的记录，可换一只股票试试" />
        </Card>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <MetricCard label="营业收入" value={fmtAmount(latest.revenue)} sub={latest.report_date || '—'} />
            <MetricCard label="净利润" value={fmtAmount(latest.net_profit)} sub={`净利率 ${pctOrDash(latest.net_margin)}`} />
            <MetricCard
              label="毛利率"
              value={pctOrDash(latest.gross_margin)}
              sub="gross margin"
              tone={pctTone(num(latest.gross_margin))}
            />
            <MetricCard
              label="资产负债率"
              value={pctOrDash(latest.debt_ratio)}
              sub="越低越稳健"
              tone={num(latest.debt_ratio) !== null && num(latest.debt_ratio) > 70 ? 'text-amber-500' : null}
            />
            <MetricCard label="PE / PB" value={`${pe === null ? '—' : fmtNum(pe, 1)} / ${pb === null ? '—' : fmtNum(pb, 2)}`} sub="当日估值" />
            <MetricCard
              label="营收 / 利润同比"
              value={`${pctOrDash(latest.revenue_yoy)} / ${pctOrDash(latest.profit_yoy)}`}
              sub="最新报告期"
              tone={num(latest.profit_yoy) !== null ? pctTone(num(latest.profit_yoy)) : null}
            />
          </div>

          <Card>
            <SectionHeader
              title="报告期序列"
              subtitle={`共 ${series.length} 期 · 柱状为营收/净利润，折线为毛利率`}
              badge={<Badge tone="slate">{latest.report_date || '—'}</Badge>}
            />
            {series.length >= 2 ? (
              <ReportChart rows={series} />
            ) : (
              <div className="py-12 text-center text-xs text-slate-400">
                <p>历史期数不足（当前仅 {series.length} 期），无法绘制趋势。</p>
                <p className="mt-1">本地 stock_fundamentals 以单期快照为主，补齐多期财报后自动显示。</p>
              </div>
            )}
          </Card>

          <Card>
            <SectionHeader title="指标明细" subtitle="按报告期倒序 · 缺失字段显示「—」" />
            <div className="mt-2 overflow-auto rounded-lg border border-slate-200 dark:border-slate-800">
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 text-[11px] text-slate-500 dark:bg-slate-900 dark:text-slate-400">
                  <tr>
                    <th className="px-3 py-2">报告期</th>
                    <th className="px-3 py-2 text-right">营收</th>
                    <th className="px-3 py-2 text-right">净利润</th>
                    <th className="px-3 py-2 text-right">毛利率</th>
                    <th className="px-3 py-2 text-right">净利率</th>
                    <th className="px-3 py-2 text-right">EPS</th>
                    <th className="px-3 py-2 text-right">ROE</th>
                    <th className="px-3 py-2 text-right">负债率</th>
                    <th className="px-3 py-2 text-right">营收同比</th>
                    <th className="px-3 py-2 text-right">利润同比</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.report_date} className="border-t border-slate-100 dark:border-slate-800">
                      <td className="px-3 py-1.5 tabular-nums text-slate-600 dark:text-slate-300">{r.report_date}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{fmtAmount(r.revenue)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{fmtAmount(r.net_profit)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{pctOrDash(r.gross_margin)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{pctOrDash(r.net_margin)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{r.eps === null ? '—' : fmtNum(r.eps, 2)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{pctOrDash(r.roe)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{pctOrDash(r.debt_ratio)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{pctOrDash(r.revenue_yoy)}</td>
                      <td className="px-3 py-1.5 text-right tabular-nums">{pctOrDash(r.profit_yoy)}</td>
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
