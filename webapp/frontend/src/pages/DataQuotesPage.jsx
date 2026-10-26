/**
 * 行情数据（/data/quotes）：全市场日行情大表，服务端分页 + 服务端排序。
 *
 * 为什么不本地排：全市场五千余只个股，客户端只拿得到当前页数据，
 * 在前端排序/过滤会得出「本页第 1 名」这种假结论，所以关键词、市场、排序、
 * 分页四类条件全部下推给 `GET /data/quotes`，前端只负责发起请求与渲染。
 *
 * 搜索输入做 300ms 防抖，避免每敲一个字就打一次接口；
 * 表格横向列多 → `overflow-x-auto` + 表头吸顶 + tabular-nums，滚动时列头始终可见。
 */
import React, { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AsyncBlock, Badge, Card, Pagination, PageShell, SectionHeader, Select } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtDate, fmtNum, fmtPct, pctTone } from '../lib/format.js'

const POLL_MS = 60_000
const PAGE_SIZES = [50, 100, 200]
const DEBOUNCE_MS = 300

const MARKET_OPTIONS = [
  { value: '', label: '全部市场' },
  { value: 'SH', label: '沪市 SH' },
  { value: 'SZ', label: '深市 SZ' },
  { value: 'BJ', label: '北交所 BJ' },
]

/** 数值单元格：缺失一律「—」，不假装是 0 */
const numCell = (v, digits = 2) => <span className="tabular-nums">{fmtNum(v, digits)}</span>

/**
 * 候选列：后端可能用不同字段名（close/price、name/stock_name），
 * 按 keys 顺序取第一个有值的；整列在所有行都为空时不渲染，做到「以实际返回为准」。
 * sort 是发给后端的排序键，只在确认字段存在时才允许点击。
 */
const CANDIDATES = [
  { keys: ['ts_code', 'code', 'symbol'], label: '代码', sort: null, render: (v) => <span className="tabular-nums text-slate-500 dark:text-slate-400">{v}</span> },
  { keys: ['name', 'stock_name', 'sec_name'], label: '名称', sort: null, render: (v) => <span className="font-medium text-slate-700 dark:text-slate-200">{v}</span> },
  { keys: ['market'], label: '市场', sort: null, render: (v) => <Badge tone="slate">{v}</Badge> },
  { keys: ['close', 'price'], label: '收盘', sort: 'close', render: (v) => numCell(v) },
  {
    keys: ['pct_chg', 'pctChg', 'change_pct'],
    label: '涨跌幅',
    sort: 'pct_chg',
    render: (v) => <span className={`font-medium tabular-nums ${pctTone(v)}`}>{fmtPct(v)}</span>,
  },
  { keys: ['open'], label: '今开', sort: 'open', render: (v) => numCell(v) },
  { keys: ['high'], label: '最高', sort: 'high', render: (v) => numCell(v) },
  { keys: ['low'], label: '最低', sort: 'low', render: (v) => numCell(v) },
  { keys: ['volume', 'vol'], label: '成交量', sort: 'volume', render: (v) => numCell(v, 0) },
  { keys: ['amount'], label: '成交额', sort: 'amount', render: (v) => <span className="tabular-nums">{fmtAmount(v)}</span> },
  {
    keys: ['turnover_rate'],
    label: '换手率',
    sort: 'turnover_rate',
    render: (v) => <span className="tabular-nums">{fmtNum(v, 2)}%</span>,
  },
  { keys: ['pe_ratio', 'pe'], label: '市盈率', sort: 'pe_ratio', render: (v) => numCell(v) },
  { keys: ['pb_ratio', 'pb'], label: '市净率', sort: 'pb_ratio', render: (v) => numCell(v) },
  {
    keys: ['total_market_cap'],
    label: '总市值',
    sort: 'total_market_cap',
    render: (v) => <span className="tabular-nums">{fmtAmount(v)}</span>,
  },
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

/** 未知字段的兜底展示：金额类给亿/万分档，比率类给百分号，其余给两位小数 */
function autoRender(key, v) {
  if (v === null || v === undefined || v === '') return <span className="text-slate-400">—</span>
  if (typeof v === 'object') return <span className="text-slate-400">—</span>
  if (typeof v !== 'number') return <span>{String(v)}</span>
  if (/(amount|market_cap|revenue|profit|assets|liabilit|equity|cashflow)/i.test(key)) {
    return <span className="tabular-nums">{fmtAmount(v)}</span>
  }
  if (/(pct|_chg|_yoy|margin|roe|turnover_rate|amplitude)/i.test(key)) {
    return <span className="tabular-nums">{fmtNum(v, 2)}%</span>
  }
  return <span className="tabular-nums">{fmtNum(v, 2)}</span>
}

/** 以实际返回为准：全空列不显示；认领不到的未知字段补进表尾，避免只剩两列 */
function resolveColumns(rows, candidates) {
  const cols = candidates.filter((c) => hasValue(rows, c.keys))
  // 可排序的都是数值列，统一右对齐，方便竖着扫数
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

export default function DataQuotesPage() {
  const navigate = useNavigate()
  const [keyword, setKeyword] = useState('')
  const [q, setQ] = useState('')
  const [market, setMarket] = useState('')
  const [size, setSize] = useState(50)
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState('pct_chg')
  const [order, setOrder] = useState('desc')

  // 300ms 防抖：敲字期间不打接口，停顿后才以最新关键词重查并回到第 1 页
  useEffect(() => {
    const t = setTimeout(() => {
      setQ(keyword.trim())
      setPage(1)
    }, DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [keyword])

  const { data, loading, error, reload } = useApi(
    () => api.get('/data/quotes', { page, size, market, sort, order, keyword: q }),
    [page, size, market, sort, order, q],
  )

  const rows = useMemo(() => pickItems(data), [data])
  const cols = useMemo(() => resolveColumns(rows, CANDIDATES), [rows])
  const total = Number(data?.total ?? rows.length) || 0
  const tradeDate = data?.trade_date || data?.as_of || null
  const metaCols = useMemo(() => columnsMeta(data?.columns), [data])

  // 60 秒静默轮询（页面隐藏时跳过），卸载清理
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
      title="行情数据"
      subtitle="全市场日行情 · 服务端分页与排序，条件改变即回到第 1 页"
      actions={
        <div className="flex flex-wrap items-center gap-2">
          {tradeDate ? <Badge tone="blue">数据日 {fmtDate(tradeDate)}</Badge> : null}
          {loading && data ? <span className="text-[11px] text-slate-400">刷新中…</span> : null}
        </div>
      }
    >
      <Card>
        <SectionHeader
          title="筛选条件"
          subtitle="关键词支持代码 / 名称，300ms 防抖；点击表头切换排序字段与方向"
        />

        {/* 工具条：关键词 + 市场 + 每页条数 */}
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
            value={market}
            onChange={(v) => {
              setMarket(v)
              setPage(1)
            }}
            options={MARKET_OPTIONS}
            aria-label="市场筛选"
          />
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
            {market ? ` · ${market}` : ''}
          </span>
        </div>

        {/* 返回的 columns 元信息：让人一眼看到服务端实际下发了哪些字段 */}
        {metaCols && metaCols.length ? (
          <p className="mt-2 truncate text-[11px] text-slate-400" title={metaCols.join(', ')}>
            返回字段 {metaCols.length} 列：{metaCols.slice(0, 12).join('、')}
            {metaCols.length > 12 ? ' …' : ''}
          </p>
        ) : null}
      </Card>

      <Card>
        <SectionHeader
          title="行情表"
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
            loadingLabel="行情数据加载中…"
            empty={Boolean(data) && rows.length === 0}
            emptyText="没有符合条件的行情数据"
            emptyHint={q || market ? '换个关键词或市场试试，也可清空筛选' : '本地尚未采集该批日行情'}
          >
            <div className="max-h-[600px] overflow-x-auto overflow-y-auto thin-scroll">
              <table className="w-full min-w-[900px] text-xs">
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
                          <td
                            key={c.label}
                            className={`px-2.5 py-2 ${c.align === 'right' ? 'text-right' : 'text-left'} ${
                              v === null || v === undefined ? 'text-slate-300 dark:text-slate-600' : ''
                            }`}
                          >
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
