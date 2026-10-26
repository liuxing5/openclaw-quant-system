/**
 * 基本面数据（/data/fundamentals）：全市场财报/估值大表，服务端分页 + 服务端排序。
 *
 * 与行情表同理：数据来自最新一期 `stock_fundamentals` 快照，动辄数千行，
 * 关键词、排序、分页全部下推 `GET /data/fundamentals`，前端不做过滤与重排，
 * 否则会出现「只在当前页里最大」的假排名。
 *
 * 列定义按「以实际返回为准」：某列在所有行都为空就不渲染（比如 ROE 未采集时），
 * 后端若加了新字段，也会兜底补进表尾而不是整行丢弃。
 */
import React, { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AsyncBlock, Badge, Card, Pagination, PageShell, SectionHeader, Select } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtDate, fmtNum, pctTone } from '../lib/format.js'

const POLL_MS = 60_000
const PAGE_SIZES = [50, 100, 200]
const DEBOUNCE_MS = 300

const numCell = (v, digits = 2) => <span className="tabular-nums">{fmtNum(v, digits)}</span>

/** 比率列：后端可能给 3.21（百分数）也可能给 0.0321（小数），≤1 视作小数 */
function pctCell(v) {
  const n = Number(v)
  if (v === null || v === undefined || v === '' || Number.isNaN(n)) return <span className="text-slate-400">—</span>
  const shown = Math.abs(n) <= 1 ? n * 100 : n
  return <span className={`tabular-nums ${pctTone(shown)}`}>{fmtNum(shown, 2)}%</span>
}

const CANDIDATES = [
  { keys: ['ts_code', 'code', 'symbol'], label: '代码', render: (v) => <span className="tabular-nums text-slate-500 dark:text-slate-400">{v}</span> },
  { keys: ['name', 'stock_name', 'sec_name'], label: '名称', render: (v) => <span className="font-medium text-slate-700 dark:text-slate-200">{v}</span> },
  { keys: ['industry'], label: '行业', render: (v) => <Badge tone="slate">{v}</Badge> },
  { keys: ['report_date', 'end_date', 'period'], label: '报告期', render: (v) => <span className="tabular-nums">{String(v).slice(0, 10)}</span> },
  { keys: ['revenue'], label: '营业收入', sort: 'revenue', render: (v) => <span className="tabular-nums">{fmtAmount(v)}</span> },
  { keys: ['revenue_yoy', 'q_revenue_yoy'], label: '营收同比', sort: 'revenue_yoy', render: (v) => pctCell(v) },
  { keys: ['net_profit', 'profit'], label: '净利润', sort: 'net_profit', render: (v) => <span className="tabular-nums">{fmtAmount(v)}</span> },
  { keys: ['profit_yoy', 'net_profit_yoy'], label: '净利同比', sort: 'profit_yoy', render: (v) => pctCell(v) },
  { keys: ['gross_margin'], label: '毛利率', sort: 'gross_margin', render: (v) => <span className="tabular-nums">{fmtNum(v, 2)}%</span> },
  { keys: ['net_margin'], label: '净利率', sort: 'net_margin', render: (v) => <span className="tabular-nums">{fmtNum(v, 2)}%</span> },
  { keys: ['roe', 'roe_avg', 'return_on_equity'], label: 'ROE', sort: 'roe', render: (v) => <span className="tabular-nums">{fmtNum(v, 2)}%</span> },
  { keys: ['debt_ratio'], label: '资产负债率', sort: 'debt_ratio', render: (v) => <span className="tabular-nums">{fmtNum(v, 2)}%</span> },
  { keys: ['eps', 'basic_eps'], label: 'EPS', sort: null, render: (v) => numCell(v, 3) },
  { keys: ['pe_ratio', 'pe'], label: '市盈率', sort: 'pe_ratio', render: (v) => numCell(v) },
  { keys: ['pb_ratio', 'pb'], label: '市净率', sort: 'pb_ratio', render: (v) => numCell(v) },
  { keys: ['total_market_cap'], label: '总市值', sort: 'total_market_cap', render: (v) => <span className="tabular-nums">{fmtAmount(v)}</span> },
  { keys: ['circulating_market_cap'], label: '流通市值', sort: null, render: (v) => <span className="tabular-nums">{fmtAmount(v)}</span> },
]

/* ------------------------------------------------------------------ 字段工具 */

function pickItems(data) {
  if (Array.isArray(data)) return data.filter((x) => x && typeof x === 'object')
  if (!data || typeof data !== 'object') return []
  const list = data.items || data.rows || data.list || []
  return Array.isArray(list) ? list.filter((x) => x && typeof x === 'object') : []
}

function pick(row, keys) {
  if (!row) return null
  for (const k of keys) {
    const v = row[k]
    if (v !== undefined && v !== null && v !== '') return v
  }
  return null
}

function hasValue(rows, keys) {
  return rows.some((r) => keys.some((k) => r && r[k] !== undefined && r[k] !== null && r[k] !== ''))
}

function autoRender(key, v) {
  if (v === null || v === undefined || v === '') return <span className="text-slate-400">—</span>
  if (typeof v === 'object') return <span className="text-slate-400">—</span>
  if (typeof v !== 'number') return <span>{String(v)}</span>
  if (/(amount|market_cap|revenue|profit|assets|liabilit|equity|cashflow)/i.test(key)) {
    return <span className="tabular-nums">{fmtAmount(v)}</span>
  }
  if (/(pct|_chg|_yoy|margin|roe|turnover_rate|ratio$)/i.test(key)) {
    return <span className="tabular-nums">{fmtNum(v, 2)}%</span>
  }
  return <span className="tabular-nums">{fmtNum(v, 2)}</span>
}

function resolveColumns(rows, candidates) {
  const cols = candidates.filter((c) => hasValue(rows, c.keys))
  const aligned = cols.map((c) => (c.sort ? { ...c, align: 'right' } : c))
  if (!rows.length || aligned.length >= 4) return aligned
  const used = new Set(aligned.flatMap((c) => c.keys))
  const extra = Object.keys(rows[0] || {})
    .filter((k) => !used.has(k))
    .slice(0, 10)
    .map((k) => ({ keys: [k], label: k, sort: null, render: (v) => autoRender(k, v) }))
  return [...aligned, ...extra]
}

function columnsMeta(cols) {
  if (!Array.isArray(cols) || !cols.length) return null
  if (typeof cols[0] === 'object' && cols[0] !== null) {
    return cols.map((c) => c.label || c.key || c.name).filter(Boolean)
  }
  return cols.map((c) => String(c))
}

/* --------------------------------------------------------------- 表头排序单元 */

function SortTh({ col, sort, order, onSort }) {
  const base = `sticky top-0 z-10 border-b border-slate-200 bg-white px-2.5 py-2 font-medium dark:border-slate-700 dark:bg-slate-900 ${
    col.align === 'right' ? 'text-right' : 'text-left'
  }`
  if (!col.sort) return <th className={`${base} text-slate-400`}>{col.label}</th>
  const active = sort === col.sort
  return (
    <th className={base}>
      <button
        type="button"
        onClick={() => onSort(col.sort)}
        title={`按${col.label}排序`}
        className={`inline-flex items-center gap-1 rounded transition-colors ${
          active
            ? 'text-brand-600 dark:text-brand-300'
            : 'text-slate-400 hover:text-slate-700 dark:hover:text-slate-200'
        }`}
      >
        {col.label}
        <span className="text-[10px]">{active ? (order === 'desc' ? '↓' : '↑') : '⇅'}</span>
      </button>
    </th>
  )
}

/* ---------------------------------------------------------------------- 页面 */

export default function DataFundamentalsPage() {
  const navigate = useNavigate()
  const [keyword, setKeyword] = useState('')
  const [q, setQ] = useState('')
  const [size, setSize] = useState(50)
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState('net_profit')
  const [order, setOrder] = useState('desc')

  useEffect(() => {
    const t = setTimeout(() => {
      setQ(keyword.trim())
      setPage(1)
    }, DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [keyword])

  const { data, loading, error, reload } = useApi(
    () => api.get('/data/fundamentals', { page, size, sort, order, keyword: q }),
    [page, size, sort, order, q],
  )

  const rows = useMemo(() => pickItems(data), [data])
  const cols = useMemo(() => resolveColumns(rows, CANDIDATES), [rows])
  const total = Number(data?.total ?? rows.length) || 0
  const reportDate = data?.report_date || data?.trade_date || null
  const metaCols = useMemo(() => columnsMeta(data?.columns), [data])

  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      reload()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [reload])

  const onSort = (key) => {
    setPage(1)
    if (sort === key) setOrder(order === 'desc' ? 'asc' : 'desc')
    else {
      setSort(key)
      setOrder('desc')
    }
  }

  const firstLoad = !data

  return (
    <PageShell
      title="基本面数据"
      subtitle="全市场最新一期财报与估值 · 服务端分页与排序"
      actions={
        <div className="flex flex-wrap items-center gap-2">
          {reportDate ? <Badge tone="blue">报告期 {fmtDate(reportDate)}</Badge> : null}
          {loading && data ? <span className="text-[11px] text-slate-400">刷新中…</span> : null}
        </div>
      }
    >
      <Card>
        <SectionHeader title="筛选条件" subtitle="关键词支持代码 / 名称，300ms 防抖；点击表头切换排序字段与方向" />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <div className="relative">
            <input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="搜索代码 / 名称…"
              aria-label="搜索代码或名称"
              className="w-56 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-700 outline-none placeholder:text-slate-400 focus:border-brand-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
            />
            {keyword ? (
              <button
                type="button"
                onClick={() => setKeyword('')}
                aria-label="清空关键词"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
              >
                ✕
              </button>
            ) : null}
          </div>
          <Select
            value={size}
            onChange={(v) => {
              setSize(Number(v))
              setPage(1)
            }}
            options={PAGE_SIZES.map((s) => ({ value: s, label: `每页 ${s} 条` }))}
            aria-label="每页条数"
          />
          <span className="text-[11px] text-slate-400">
            共 {total.toLocaleString('zh-CN')} 条
            {q ? ` · 关键词「${q}」` : ''}
          </span>
        </div>
        {metaCols && metaCols.length ? (
          <p className="mt-2 truncate text-[11px] text-slate-400" title={metaCols.join(', ')}>
            返回字段 {metaCols.length} 列：{metaCols.slice(0, 12).join('、')}
            {metaCols.length > 12 ? ' …' : ''}
          </p>
        ) : null}
      </Card>

      <Card>
        <SectionHeader
          title="基本面表"
          subtitle={`第 ${page} 页 · 每页 ${size} 条 · 排序 ${sort} ${order === 'desc' ? '降序' : '升序'}`}
        />
        <div className="mt-3">
          {data && error ? (
            <div className="mb-3">
              <AsyncBlock loading={false} error={error} onRetry={reload} />
            </div>
          ) : null}
          <AsyncBlock
            loading={firstLoad && loading}
            error={firstLoad ? error : null}
            onRetry={reload}
            loadingLabel="基本面数据加载中…"
            empty={Boolean(data) && rows.length === 0}
            emptyText="没有符合条件的基本面数据"
            emptyHint={q ? '换个关键词试试，或清空搜索' : '本地尚未采集该批 stock_fundamentals'}
          >
            <div className="max-h-[600px] overflow-x-auto overflow-y-auto thin-scroll">
              <table className="w-full min-w-[1000px] text-xs">
                <thead>
                  <tr>
                    {cols.map((c) => (
                      <SortTh key={c.label} col={c} sort={sort} order={order} onSort={onSort} />
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr
                      key={r.ts_code || r.code || `${i}`}
                      onClick={() => {
                        const code = pick(r, ['ts_code', 'code', 'symbol'])
                        if (code) navigate(`/stock-analysis/${code}`)
                      }}
                      className="cursor-pointer border-b border-slate-100 transition-colors last:border-0 hover:bg-slate-50 dark:border-slate-800/70 dark:hover:bg-slate-800/60"
                    >
                      {cols.map((c) => {
                        const v = pick(r, c.keys)
                        return (
                          <td key={c.label} className={`px-2.5 py-2 ${c.align === 'right' ? 'text-right' : 'text-left'}`}>
                            {v === null || v === undefined ? <span className="text-slate-400">—</span> : c.render(v, r)}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={page} pageSize={size} total={total} onChange={setPage} />
          </AsyncBlock>
        </div>
      </Card>
    </PageShell>
  )
}
