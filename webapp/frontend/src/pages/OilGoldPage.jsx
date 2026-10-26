/**
 * 全球商品（/oil-gold）：原油、黄金、铜、白银、天然气与玉米。
 *
 * 后端 GET /commodity/quotes 按「东财 push2 → 新浪 hf_* → 静态条目」降级；
 * 拿不到价格的品种仍保留卡片（显示「— / 暂无免费源」），避免整页塌掉。
 * 报价单位因品种而异，卡片上明确标注，避免把「美元/盎司」当「美元/桶」读。
 */
import { useEffect } from 'react'
import { AsyncBlock, Badge, Card, PageShell, SectionHeader } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDateTime, fmtNum, fmtPct, pctTone } from '../lib/format.js'

const POLL_MS = 300_000
const SYMBOL_ICON = {
  BRENT: '🛢️',
  CL: '🛢️',
  SC: '🛢️',
  GC: '🥇',
  SI: '🥈',
  HG: '🟤',
  NG: '🔥',
  C: '🌽',
}
const CURRENCY_SYMBOL = { USD: '$', CNY: '¥' }

const num = (v) => {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function CommodityCard({ item }) {
  const price = num(item.price)
  const pct = num(item.pct_chg)
  const digits = item.unit?.includes('桶') ? 3 : item.unit?.includes('百万英热') ? 3 : 2
  const sym = item.unit?.includes('美分') ? '' : CURRENCY_SYMBOL[item.currency] || ''

  return (
    <Card className="flex flex-col">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-sm font-semibold text-slate-800 dark:text-slate-100">
            <span aria-hidden="true">{SYMBOL_ICON[item.symbol] || '📈'}</span>
            {item.name || item.symbol}
          </p>
          <p className="mt-0.5 font-mono text-[10px] text-slate-400">
            {item.symbol} · {item.market || '国际'}
          </p>
        </div>
        <Badge tone={pct === null ? 'slate' : pct > 0 ? 'rose' : pct < 0 ? 'emerald' : 'slate'}>
          {pct === null ? '暂无数据' : fmtPct(pct)}
        </Badge>
      </div>

      <p className="mt-1 text-[10px] text-slate-400 dark:text-slate-500">计价单位：{item.unit || '—'}</p>

      <div className="mt-3 flex items-baseline gap-2">
        <span className={`text-2xl font-semibold tabular-nums ${price === null ? 'text-slate-400' : pctTone(pct)}`}>
          {price === null ? '—' : `${sym}${fmtNum(price, digits)}`}
        </span>
        {pct !== null ? <span className={`text-xs tabular-nums ${pctTone(pct)}`}>{fmtPct(pct)}</span> : null}
      </div>

      <div className="mt-auto flex items-center justify-between gap-2 pt-3 text-[10px] text-slate-400 dark:text-slate-500">
        <span>{item.as_of ? `更新 ${fmtDateTime(item.as_of)}` : price === null ? '暂无免费数据源' : '非交易时段收盘价'}</span>
        <span className="truncate">{item.source || '—'}</span>
      </div>
    </Card>
  )
}

export default function OilGoldPage() {
  const { data, loading, error, reload } = useApi(() => api.get('/commodity/quotes'), [])

  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      reload()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [reload])

  const items = data?.items || []
  const withPrice = items.filter((i) => num(i.price) !== null)
  const meta = data?.meta || {}

  return (
    <PageShell
      title="全球商品"
      subtitle="原油、黄金、铜、白银与天然气实时行情 · 每 5 分钟刷新"
      actions={<span className="text-[11px] text-slate-400">每 5 分钟自动刷新</span>}
    >
      {!data ? (
        <AsyncBlock
          loading={loading}
          error={error}
          onRetry={reload}
          loadingLabel="商品行情加载中…"
          empty={!loading && !error}
          emptyText="暂无商品行情"
          emptyHint="外部数据源暂不可用，稍后重试"
        />
      ) : (
        <div className="space-y-4">
          <Card className="flex flex-wrap items-center justify-between gap-2 py-2.5">
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
              <Badge tone={meta.degraded ? 'amber' : 'emerald'}>
                {meta.degraded ? '降级数据' : `${withPrice.length}/${items.length} 个品种有报价`}
              </Badge>
              <span>数据时间：{data.as_of ? fmtDateTime(data.as_of) : '—'}</span>
              {meta.source ? <span className="text-slate-400">来源 {meta.source}</span> : null}
            </div>
            <span className="text-[11px] text-slate-400">INE 原油等无免费实时源，保留条目并置空价格</span>
          </Card>

          <SectionHeader title="实时行情" subtitle="涨=红、跌=绿；价格为该品种最近一笔公开报价" />
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {items.map((it) => (
              <CommodityCard key={it.symbol} item={it} />
            ))}
          </div>

          {meta.note ? (
            <p className="text-[11px] leading-5 text-slate-400 dark:text-slate-500">{meta.note}</p>
          ) : null}
        </div>
      )}
    </PageShell>
  )
}
