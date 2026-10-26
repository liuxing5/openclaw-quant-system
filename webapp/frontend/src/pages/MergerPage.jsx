/**
 * 并购重组（/merger-restructuring）：本期公告列表 + 涉及个股磁贴。
 *
 * 公告标题普遍很长，用卡片行而不是表格：日期/分类做头部元信息，标题独占一行
 * 保证可读性，来源链接新开标签页，方便对照交易所原文。
 */
import { useMemo, useState } from 'react'
import {
  AsyncBlock,
  Badge,
  Card,
  EmptyState,
  PageShell,
  Pagination,
  SectionHeader,
  StatTile,
} from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDateTime, fmtNum } from '../lib/format.js'

const PAGE_SIZE = 20

const CATEGORY_TONE = {
  收购: 'rose',
  重组: 'blue',
  股权转让: 'amber',
  合并: 'rose',
  借壳: 'amber',
  终止: 'emerald',
}

function toneOf(category = '') {
  const hit = Object.keys(CATEGORY_TONE).find((k) => String(category).includes(k))
  return hit ? CATEGORY_TONE[hit] : 'slate'
}

function fmtDay(v) {
  if (!v) return '—'
  const s = String(v)
  return s.length > 10 ? s.slice(0, 10) : s
}

/** 外链样式与 ui.jsx 的 TextLink 保持一致，但需要新开标签页，故在页内实现 */
function ExtLink({ href, children }) {
  if (!href) return <span className="text-xs text-slate-400 dark:text-slate-500">暂无原文</span>
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400 dark:hover:text-brand-300"
    >
      {children} ↗
    </a>
  )
}

function normalize(raw, idx) {
  const code = raw.ts_code || raw.code || raw.symbol || ''
  return {
    id: raw.id || `${code}-${fmtDay(raw.trade_date || raw.date || raw.announce_date)}-${idx}`,
    date: raw.trade_date || raw.date || raw.announce_date || raw.published_at || null,
    code,
    name: raw.name || raw.stock_name || code || '—',
    title: raw.title || raw.summary || raw.subject || raw.name || '',
    category: raw.category || raw.type || raw.announce_type || '并购重组',
    url: raw.url || raw.link || raw.source_url || raw.announcement_url || null,
  }
}

export default function MergerPage() {
  const [page, setPage] = useState(1)
  const { data, loading, error, reload } = useApi(
    () => api.get('/merger-restructuring', { page, size: PAGE_SIZE }),
    [page],
  )

  const items = useMemo(() => (data?.items || data?.rows || []).map(normalize), [data])

  const stockCount = useMemo(() => {
    if (data?.stock_count !== undefined && data?.stock_count !== null) return data.stock_count
    return new Set(items.map((it) => it.code).filter(Boolean)).size
  }, [data, items])

  const total = data?.total ?? items.length
  const summary = data?.summary || {}
  const range =
    data?.start_date && data?.end_date ? `${fmtDay(data.start_date)} ~ ${fmtDay(data.end_date)}` : null

  return (
    <PageShell
      title="并购重组"
      subtitle="跟踪近期重组、收购与股权转让公告，股票代码可直接进入个股分析。"
    >
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="本期公告数" value={fmtNum(total, 0)} sub={range || '近 30 天'} />
        <StatTile label="涉及个股" value={fmtNum(summary.companies ?? stockCount, 0)} sub="去重后的代码数" />
        <StatTile label="最新公告" value={summary.latest ? fmtDay(summary.latest) : data?.as_of ? fmtDay(data.as_of) : '—'} sub={data?.meta?.source || '公告 + 信号'} />
        <StatTile label="数据来源" value={data?.meta?.source === 'stock_announcements' ? '巨潮公告' : '公告 + 信号'} sub="按发布时间倒序" />
      </div>

      {summary.top?.length ? (
        <Card>
          <SectionHeader
            title="重点公司 Top10"
            subtitle="按最新成交额排序 · 右侧为区间内并购公告数"
            badge={<Badge tone="amber">{summary.top.length} 家</Badge>}
          />
          <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
            {summary.top.map((x, i) => (
              <a
                key={x.ts_code}
                href={`/stock-analysis/${String(x.ts_code).split('.')[0]}`}
                className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2 transition hover:border-brand-400 dark:border-slate-700 dark:bg-slate-800/50"
              >
                <p className="flex items-center gap-1 text-xs font-medium text-slate-700 dark:text-slate-200">
                  <span className="tabular-nums text-slate-400">{i + 1}</span>
                  <span className="truncate">{x.stock_name}</span>
                </p>
                <p className="mt-0.5 font-mono text-[10px] text-slate-400">{x.ts_code}</p>
                <p className="mt-1 text-[10px] text-slate-500 dark:text-slate-400">
                  {x.count} 条 · {x.amount ? `${(x.amount / 1e8).toFixed(1)} 亿` : '—'}
                </p>
              </a>
            ))}
          </div>
        </Card>
      ) : null}

      <Card>
        <SectionHeader
          title="重组公告流"
          subtitle="按公告日期排列 · 分类为公告主题，点击来源可核对原文"
          badge={<Badge tone="blue">{total} 条</Badge>}
        />

        <AsyncBlock
          loading={loading}
          error={error}
          onRetry={reload}
          empty={!loading && !error && items.length === 0}
          emptyText="暂无并购重组相关公告"
          emptyHint="本期数据源尚未推送重组公告，稍后刷新即可"
          loadingLabel="公告列表加载中…"
        >
          <ul className="mt-3 space-y-2.5">
            {items.map((it) => (
              <li
                key={it.id}
                className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm transition hover:border-brand-300 dark:border-slate-800 dark:bg-slate-900 dark:hover:border-brand-500/50"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-medium tabular-nums text-slate-500 dark:text-slate-400">
                    {fmtDay(it.date)}
                  </span>
                  <Badge tone={toneOf(it.category)}>{it.category}</Badge>
                  <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">{it.name}</span>
                  {it.code ? (
                    <a
                      href={`/stock-analysis/${it.code}`}
                      className="text-xs tabular-nums text-slate-400 transition hover:text-brand-600 dark:text-slate-500 dark:hover:text-brand-400"
                    >
                      {it.code}
                    </a>
                  ) : null}
                </div>
                <div className="mt-1.5 flex items-start justify-between gap-3">
                  <p className="min-w-0 text-sm leading-6 text-slate-600 dark:text-slate-300">{it.title}</p>
                  <ExtLink href={it.url}>查看原文</ExtLink>
                </div>
              </li>
            ))}
          </ul>

          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={setPage} />
        </AsyncBlock>
      </Card>
    </PageShell>
  )
}
