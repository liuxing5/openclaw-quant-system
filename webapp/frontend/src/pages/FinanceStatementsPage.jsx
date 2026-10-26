/**
 * 财务报表（/finance/statements?ts_code&limit=）：单股近 N 期报表横向对比。
 *
 * 形态说明：原站把**报告期摆成列、科目摆成行**，这样同一行左右扫一眼就能看出
 * 营收 / 净利 / 毛利率的季度趋势，比一行行翻纵向表高效得多。
 *
 * 个股选择走已上线的 `/market-data/securities/search`（300ms 防抖，同时带上
 * `q` 与 `keyword` 两个参数名以兼容不同实现），选中后把 `ts_code` 写进 URL query，
 * 便于刷新与分享后仍然停在同一只股票上。
 *
 * 四态：未选个股 → 引导空态；请求中 → 骨架；接口 404/500 → 可重试错误块（不白屏）；
 * 有响应但无报告期 → 「本地暂无该股财务数据」。
 */
import React, { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AsyncBlock, Badge, Card, EmptyState, PageShell, SectionHeader, Select } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtDate, fmtNum, fmtPct, pctTone } from '../lib/format.js'

const DEBOUNCE_MS = 300
const LIMIT_OPTIONS = [4, 8, 12]

/* ------------------------------------------------------------- 个股搜索选择器 */

function SecurityPicker({ tsCode, stockName, onPick, onClear }) {
  const [kw, setKw] = useState('')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const t = setTimeout(() => setQ(kw.trim()), DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [kw])

  // 同时传 q 和 keyword：后端两种参数名都可能出现，多传一个不影响另一方
  const { data, loading, error, reload } = useApi(
    () => api.get('/market-data/securities/search', { q, keyword: q, limit: 10 }),
    [q],
    { enabled: q.length > 0 },
  )

  const items = useMemo(() => {
    const list = Array.isArray(data) ? data : data?.items || data?.list || []
    return Array.isArray(list) ? list : []
  }, [data])

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative w-full max-w-xs">
        <input
          value={kw}
          onChange={(e) => {
            setKw(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          placeholder="输入代码或名称，如 600519 / 茅台"
          aria-label="搜索个股"
          className="w-full rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-700 outline-none placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
        />
        {open && q ? (
          <ul className="absolute left-0 top-full z-20 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-700 dark:bg-slate-900">
            {loading ? (
              <li className="px-3 py-2 text-xs text-slate-400">搜索中…</li>
            ) : error ? (
              <li className="px-3 py-2 text-xs text-rose-600 dark:text-rose-400">
                搜索失败：{error.message}
                <button
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={reload}
                  className="ml-2 rounded border border-rose-300 px-1.5 py-0.5 text-[11px] dark:border-rose-500/40"
                >
                  重试
                </button>
              </li>
            ) : items.length === 0 ? (
              <li className="px-3 py-2 text-xs text-slate-400">没有匹配的个股</li>
            ) : (
              items.map((it) => (
                <li key={it.ts_code || it.code}>
                  <button
                    type="button"
                    // mousedown 先于 blur 触发，阻止默认行为避免下拉在点击前收起
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => {
                      onPick(it)
                      setKw('')
                      setOpen(false)
                    }}
                    className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-xs transition hover:bg-slate-50 dark:hover:bg-slate-800/70"
                  >
                    <span className="font-medium text-slate-700 dark:text-slate-200">
                      {it.stock_name || it.name || it.ts_code}
                    </span>
                    <span className="tabular-nums text-slate-400">{it.ts_code || it.code}</span>
                  </button>
                </li>
              ))
            )}
          </ul>
        ) : null}
      </div>

      {tsCode ? (
        <Badge tone="blue" className="cursor-pointer" onClick={onClear} title="点击移除该股">
          {stockName || tsCode}
          <span className="ml-1 tabular-nums text-[10px] opacity-70">{stockName ? tsCode : ''}</span>
          <span className="ml-1">✕</span>
        </Badge>
      ) : (
        <span className="text-[11px] text-slate-400">尚未选择个股</span>
      )}
    </div>
  )
}

/* ------------------------------------------------------------ 报表行定义 */

/** 金额统一走 fmtAmount，比率统一走 fmtNum + %；hot = 需要高亮的关键行 */
const ROWS = [
  { keys: ['revenue'], label: '营业收入', hot: true, render: (v) => <span className="tabular-nums">{fmtAmount(v)}</span> },
  { keys: ['revenue_yoy', 'q_revenue_yoy'], label: '营收同比', render: (v) => <span className={`tabular-nums ${pctTone(v)}`}>{fmtPct(v)}</span> },
  { keys: ['net_profit', 'profit'], label: '归母净利润', hot: true, render: (v) => <span className="tabular-nums">{fmtAmount(v)}</span> },
  { keys: ['profit_yoy', 'net_profit_yoy'], label: '净利同比', render: (v) => <span className={`tabular-nums ${pctTone(v)}`}>{fmtPct(v)}</span> },
  { keys: ['gross_margin'], label: '毛利率', hot: true, render: (v) => <span className="tabular-nums">{fmtNum(v, 2)}%</span> },
  { keys: ['net_margin'], label: '净利率', render: (v) => <span className="tabular-nums">{fmtNum(v, 2)}%</span> },
  { keys: ['operating_cashflow', 'ocf'], label: '经营现金流', hot: true, render: (v) => <span className="tabular-nums">{fmtAmount(v)}</span> },
  { keys: ['debt_ratio'], label: '资产负债率', hot: true, render: (v) => <span className="tabular-nums">{fmtNum(v, 2)}%</span> },
  { keys: ['total_assets'], label: '总资产', render: (v) => <span className="tabular-nums">{fmtAmount(v)}</span> },
  { keys: ['total_liabilities'], label: '总负债', render: (v) => <span className="tabular-nums">{fmtAmount(v)}</span> },
  { keys: ['roe', 'roe_avg', 'return_on_equity'], label: 'ROE', render: (v) => <span className="tabular-nums">{fmtNum(v, 2)}%</span> },
  { keys: ['eps', 'basic_eps'], label: '每股收益', render: (v) => <span className="tabular-nums">{fmtNum(v, 3)}</span> },
  { keys: ['bps'], label: '每股净资产', render: (v) => <span className="tabular-nums">{fmtNum(v, 2)}</span> },
]

function pick(row, keys) {
  if (!row) return null
  for (const k of keys) {
    const v = row[k]
    if (v !== undefined && v !== null && v !== '') return v
  }
  return null
}

/** 响应可能是数组，也可能是 {items|statements|periods|rows} */
function pickPeriods(data) {
  if (Array.isArray(data)) return data.filter((x) => x && typeof x === 'object')
  if (!data || typeof data !== 'object') return []
  const list = data.items || data.statements || data.periods || data.rows || data.list || []
  return Array.isArray(list) ? list.filter((x) => x && typeof x === 'object') : []
}

function periodLabel(p) {
  const v = pick(p, ['report_date', 'end_date', 'period', 'date'])
  return v ? String(v).slice(0, 10) : '—'
}

/* ---------------------------------------------------------------------- 页面 */

export default function FinanceStatementsPage() {
  const [sp, setSp] = useSearchParams()
  const tsCode = sp.get('ts_code') || ''
  const stockName = sp.get('name') || ''
  const [limit, setLimit] = useState(8)

  const updateParam = (key, value) => {
    const next = new URLSearchParams(sp)
    if (value) next.set(key, value)
    else next.delete(key)
    setSp(next, { replace: true })
  }

  const { data, loading, error, reload } = useApi(
    () => api.get('/finance/statements', { ts_code: tsCode, limit }),
    [tsCode, limit],
    { enabled: Boolean(tsCode) },
  )

  const periods = useMemo(() => {
    const list = pickPeriods(data)
    // 报告期倒序：最新一期在最左边，符合「先看最近」的阅读习惯
    return [...list].sort((a, b) => periodLabel(b).localeCompare(periodLabel(a))).slice(0, limit)
  }, [data, limit])

  const visibleRows = useMemo(
    () => ROWS.filter((r) => periods.some((p) => pick(p, r.keys) !== null)),
    [periods],
  )

  return (
    <PageShell
      title="财务报表"
      subtitle="按报告期横向对比最近 N 期关键科目"
      actions={
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-slate-400">最近</span>
          <Select
            value={limit}
            onChange={(v) => setLimit(Number(v))}
            options={LIMIT_OPTIONS.map((n) => ({ value: n, label: `${n} 期` }))}
            aria-label="报告期数量"
          />
        </div>
      }
    >
      <Card>
        <SectionHeader
          title="选择个股"
          subtitle="搜索代码或名称后加载该股财报；ts_code 会同步到地址栏，可直接分享"
        />
        <div className="mt-3">
          <SecurityPicker
            tsCode={tsCode}
            stockName={stockName}
            onPick={(it) => {
              updateParam('ts_code', it.ts_code || it.code)
              updateParam('name', it.stock_name || it.name || '')
            }}
            onClear={() => {
              updateParam('ts_code', '')
              updateParam('name', '')
            }}
          />
        </div>
      </Card>

      <Card>
        <SectionHeader
          title="报告期对比"
          subtitle={
            tsCode
              ? `${stockName || tsCode} · 最近 ${periods.length} 期（左起最新）`
              : '等待选择个股'
          }
          badge={
            periods.length ? <Badge tone="slate">最新 {periodLabel(periods[0])}</Badge> : null
          }
        />

        <div className="mt-3">
          {!tsCode ? (
            <EmptyState
              text="请先选择个股"
              hint="在上方搜索框输入代码或名称（如 600519 / 贵州茅台），选中后自动加载近 8 期财务报表"
            />
          ) : (
            <AsyncBlock
              loading={loading && !data}
              error={!data ? error : null}
              onRetry={reload}
              loadingLabel="财务数据加载中…"
              empty={Boolean(data) && periods.length === 0}
              emptyText="本地暂无该股财务数据"
              emptyHint={`${tsCode} 在 stock_fundamentals 中没有记录，可能未采集或该股无公开财报`}
            >
              <div className="overflow-x-auto overflow-y-auto thin-scroll">
                <table className="w-full min-w-[640px] text-xs">
                  <thead>
                    <tr className="text-left text-[11px] text-slate-400">
                      <th className="sticky top-0 z-10 border-b border-slate-200 bg-white px-2.5 py-2 font-medium dark:border-slate-700 dark:bg-slate-900">
                        科目 \\ 报告期
                      </th>
                      {periods.map((p, i) => (
                        <th
                          key={`${periodLabel(p)}-${i}`}
                          className={`sticky top-0 z-10 border-b border-slate-200 bg-white px-2.5 py-2 text-right font-medium dark:border-slate-700 dark:bg-slate-900 ${
                            i === 0 ? 'text-brand-600 dark:text-brand-300' : ''
                          }`}
                        >
                          {periodLabel(p)}
                          {i === 0 ? <span className="ml-1 text-[10px]">最新</span> : null}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {visibleRows.map((r) => (
                      <tr
                        key={r.label}
                        className={`border-b border-slate-100 last:border-0 dark:border-slate-800/70 ${
                          r.hot
                            ? 'bg-brand-50/60 font-medium dark:bg-brand-500/10'
                            : 'hover:bg-slate-50 dark:hover:bg-slate-800/50'
                        }`}
                      >
                        <td
                          className={`px-2.5 py-2 ${
                            r.hot
                              ? 'text-slate-800 dark:text-slate-100'
                              : 'text-slate-500 dark:text-slate-400'
                          }`}
                        >
                          {r.label}
                        </td>
                        {periods.map((p, i) => {
                          const v = pick(p, r.keys)
                          return (
                            <td
                              key={`${r.label}-${i}`}
                              className="px-2.5 py-2 text-right text-slate-600 dark:text-slate-300"
                            >
                              {v === null ? <span className="text-slate-400 dark:text-slate-600">—</span> : r.render(v)}
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-[11px] text-slate-400">
                单位：金额为元（按量级自动换算）· 比率为百分数 · 数据取自本地 stock_fundamentals
                {data?.source ? ` · 来源 ${data.source}` : ''}
              </p>
            </AsyncBlock>
          )}
        </div>
      </Card>
    </PageShell>
  )
}
