/**
 * 外围指数（/global-index）：美股 / 港股 / 日经 / 欧股的一屏行情。
 *
 * 后端 GET /global-indexes 采用「东财 push2 主源 → 新浪备源 → 静态名单」三级降级，
 * 页面必须把降级状态显式告诉用户（徽标 + 底部说明），不能让空价格看起来像 bug。
 * 后端缓存 10 分钟，因此页面 5 分钟轮询即可。
 */
import { useEffect, useMemo } from 'react'
import { AsyncBlock, Badge, Card, EmptyState, PageShell, SectionHeader } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDateTime, fmtNum, fmtPct, pctTone } from '../lib/format.js'

const POLL_MS = 300_000
const MARKET_ORDER = ['美国', '中国香港', '日本', '英国', '德国', '法国', '韩国', '海外']

const num = (v) => {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

const FLAG = {
  美国: '🇺🇸',
  中国香港: '🇭🇰',
  日本: '🇯🇵',
  英国: '🇬🇧',
  德国: '🇩🇪',
  法国: '🇫🇷',
  韩国: '🇰🇷',
}

function IndexCard({ item }) {
  const pct = num(item.pct_chg)
  const chg = num(item.change)
  const hasPrice = num(item.price) !== null

  return (
    <Card className="flex flex-col">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 truncate text-sm font-semibold text-slate-800 dark:text-slate-100">
            <span aria-hidden="true">{FLAG[item.market] || '🌐'}</span>
            {item.name || item.code}
          </p>
          <p className="mt-0.5 font-mono text-[10px] text-slate-400">
            {item.code} · {item.market || '海外'} · {item.currency || 'USD'}
          </p>
        </div>
        <Badge tone={pct === null ? 'slate' : pct > 0 ? 'rose' : pct < 0 ? 'emerald' : 'slate'}>
          {fmtPct(pct)}
        </Badge>
      </div>

      <div className="mt-3 flex items-baseline gap-2">
        <span className={`text-2xl font-semibold tabular-nums ${hasPrice ? pctTone(pct) : 'text-slate-400'}`}>
          {hasPrice ? fmtNum(item.price, 2) : '—'}
        </span>
        {chg !== null ? (
          <span className={`text-xs tabular-nums ${pctTone(pct)}`}>
            {chg > 0 ? '+' : ''}
            {fmtNum(chg, 2)}
          </span>
        ) : null}
      </div>

      <dl className="mt-3 grid grid-cols-3 gap-1 border-t border-slate-100 pt-2 text-[10px] dark:border-slate-800">
        {[
          ['开盘', item.open],
          ['最高', item.high],
          ['最低', item.low],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="text-slate-400 dark:text-slate-500">{label}</dt>
            <dd className="mt-0.5 tabular-nums text-slate-600 dark:text-slate-300">
              {num(value) === null ? '—' : fmtNum(value, 2)}
            </dd>
          </div>
        ))}
      </dl>

      {item.source ? (
        <p className="mt-2 truncate text-[10px] text-slate-400 dark:text-slate-500">
          来源：{item.source === 'eastmoney-push2' ? '东方财富 push2' : item.source === 'sina-hq' ? '新浪财经' : item.source}
        </p>
      ) : null}
    </Card>
  )
}

export default function GlobalIndexPage() {
  const { data, loading, error, reload } = useApi(() => api.get('/global-indexes'), [])

  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      reload()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [reload])

  const items = data?.items || []
  const groups = useMemo(() => {
    const map = new Map()
    for (const it of items) {
      const key = it.market || '海外'
      if (!map.has(key)) map.set(key, [])
      map.get(key).push(it)
    }
    return [...map.entries()].sort(
      (a, b) => (MARKET_ORDER.indexOf(a[0]) + 1 || 99) - (MARKET_ORDER.indexOf(b[0]) + 1 || 99),
    )
  }, [items])

  const meta = data?.meta || {}
  const degraded = meta.degraded

  return (
    <PageShell
      title="外围指数"
      subtitle="道琼斯、纳斯达克、标普500、日经225 与欧洲主要指数的真实行情，后台每 5 分钟刷新"
      actions={<span className="text-[11px] text-slate-400">每 5 分钟自动刷新</span>}
    >
      {!data ? (
        <AsyncBlock
          loading={loading}
          error={error}
          onRetry={reload}
          loadingLabel="外围指数加载中…"
          empty={!loading && !error}
          emptyText="暂无外围指数数据"
          emptyHint="外部行情源暂不可用，稍后重试"
        />
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            text="暂无外围指数行情"
            hint={meta.reason || '东财 / 新浪行情源均不可用，请稍后重试'}
          />
        </Card>
      ) : (
        <div className="space-y-4">
          <Card className="flex flex-wrap items-center justify-between gap-2 py-2.5">
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
              <Badge tone={degraded ? 'amber' : 'emerald'}>{degraded ? '降级数据' : '实时行情'}</Badge>
              <span>
                数据更新时间：{meta.cached_until ? fmtDateTime(meta.cached_until) : data.as_of ? fmtDateTime(data.as_of) : '—'}
              </span>
              {meta.source ? <span className="text-slate-400">来源 {meta.source}</span> : null}
            </div>
            {meta.missing?.length ? (
              <span className="text-[11px] text-amber-500">缺失：{meta.missing.join('、')}</span>
            ) : null}
          </Card>

          {groups.map(([market, rows]) => (
            <section key={market} className="space-y-2">
              <SectionHeader
                title={`${FLAG[market] || '🌐'} ${market}`}
                subtitle={`${rows.length} 个指数`}
              />
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {rows.map((it) => (
                  <IndexCard key={it.code} item={it} />
                ))}
              </div>
            </section>
          ))}

          <p className="text-[11px] leading-5 text-slate-400 dark:text-slate-500">
            {meta.note || '行情来自公开免费接口，按「东财 push2 → 新浪财经 → 静态名单」降级；延迟以数据源为准。'}
            {meta.push2_error ? ` push2：${meta.push2_error}` : ''}
            {meta.sina_error ? ` 新浪：${meta.sina_error}` : ''}
          </p>
        </div>
      )}
    </PageShell>
  )
}
