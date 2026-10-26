/**
 * 每日快讯（/info/news-flash）：左侧 7×24 快讯时间流 + 右侧信号雷达。
 *
 * 快讯是强时效内容：30 秒静默轮询、按发布时间倒序、首屏才走三态，已有数据时
 * 刷新不闪屏；信号雷达把近 48 小时的信号量按小时聚合，悬浮可看该小时的头条。
 */
import { useEffect, useMemo, useState } from 'react'
import Chart, { AXIS_STYLE, TIP } from '../components/Chart.jsx'
import { AsyncBlock, Badge, Card, EmptyState, PageShell, Pagination, SectionHeader, Tabs } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDateTime, fmtTime } from '../lib/format.js'

const POLL_MS = 30_000
const PAGE_SIZE = 20

const SOURCE_TABS = [
  { value: 'cls', label: '财联社' },
  { value: 'jin10', label: '金十数据' },
]

/** 雷达响应归一化：契约是「近 N 小时按小时聚合」，兼容常见字段命名 */
function normRadar(payload) {
  const raw = payload?.items || payload?.buckets || payload?.hours || payload?.series || []
  return raw.map((x) => ({
    hour: x.hour || x.bucket || x.ts || x.time || x.datetime || '',
    count: Number(x.count ?? x.total ?? x.signals ?? x.value ?? 0),
    titles: x.top_titles || x.titles || x.top || [],
  }))
}

function RadarCard({ data, loading, error, onRetry }) {
  const rows = useMemo(() => normRadar(data), [data])

  const option = useMemo(() => {
    const labels = rows.map((r) => {
      const s = String(r.hour)
      // 兼容 ISO 与 "YYYY-MM-DD HH" 两种形态，只保留月日时
      if (s.includes('T')) return s.slice(5, 16).replace('T', ' ')
      return s.length > 10 ? s.slice(5, 16) : s.slice(5)
    })
    return {
      grid: { left: 36, right: 12, top: 18, bottom: 46 },
      tooltip: {
        ...TIP,
        formatter: (ps) => {
          const p = Array.isArray(ps) ? ps[0] : ps
          const row = rows[p.dataIndex] || {}
          const titles = (row.titles || []).slice(0, 3)
          const head = `${row.hour || p.axisValue}<br/>信号 <b>${p.value}</b> 条`
          return titles.length ? `${head}<br/>${titles.join('<br/>')}` : head
        },
      },
      xAxis: {
        type: 'category',
        data: labels,
        ...AXIS_STYLE,
        axisLabel: { color: '#94a3b8', fontSize: 9, rotate: 45, interval: 1 },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        axisLabel: { color: '#94a3b8', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } },
      },
      series: [
        {
          type: 'bar',
          name: '信号量',
          data: rows.map((r) => r.count),
          itemStyle: { color: 'rgba(99,102,241,0.7)', borderRadius: [3, 3, 0, 0] },
          barMaxWidth: 18,
        },
      ],
    }
  }, [rows])

  return (
    <Card className="h-fit">
      <SectionHeader
        title="信号雷达"
        subtitle="近 48 小时信号量按小时聚合"
        badge={<Badge tone="blue">{rows.length ? `${rows.length} 小时` : '—'}</Badge>}
      />
      <AsyncBlock
        loading={loading}
        error={error}
        onRetry={onRetry}
        empty={!loading && !error && rows.length === 0}
        emptyText="近 48 小时暂无信号"
        emptyHint="快讯入库后会自动按小时归集"
        loadingLabel="信号雷达加载中…"
      >
        <Chart option={option} height={260} className="mt-2" />
      </AsyncBlock>
    </Card>
  )
}

export default function NewsFlashPage() {
  const [source, setSource] = useState('cls')
  const [page, setPage] = useState(1)

  const flash = useApi(() => api.get('/info/news-flash', { source, page, size: PAGE_SIZE }), [source, page])
  const radar = useApi(() => api.get('/info/news-radar', { limit: 48 }), [])

  // 轮询：快讯是本页最时效的内容，30 秒静默刷新一次
  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      flash.reload()
      radar.reload()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [flash.reload, radar.reload])

  const items = useMemo(() => {
    const rows = flash.data?.items || []
    return [...rows].sort((a, b) =>
      String(b.published_at || b.createdAt || '').localeCompare(String(a.published_at || a.createdAt || '')),
    )
  }, [flash.data])

  const updated = flash.data?.updated_at || flash.data?.as_of || items[0]?.published_at || null

  return (
    <PageShell
      title="每日快讯"
      subtitle={`7×24 小时财经快讯 · 最近更新 ${updated ? fmtDateTime(updated) : '—'} · 30 秒自动刷新`}
      actions={
        <Tabs
          tabs={SOURCE_TABS}
          value={source}
          onChange={(v) => {
            setSource(v)
            setPage(1)
          }}
        />
      }
    >
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <Card className="h-full">
            <SectionHeader
              title="快讯时间流"
              subtitle={`${SOURCE_TABS.find((t) => t.value === source)?.label || ''} · 倒序排列，最新在最上`}
              badge={<Badge tone="rose">{flash.data?.total ?? items.length} 条</Badge>}
            />

            {!flash.data ? (
              <AsyncBlock
                loading={flash.loading}
                error={flash.error}
                onRetry={flash.reload}
                loadingLabel="快讯加载中…"
              />
            ) : items.length === 0 ? (
              <div className="mt-3">
                <EmptyState text="暂无快讯" hint="换一个来源，或稍后刷新查看最新推送" />
              </div>
            ) : (
              <>
                <ul className="mt-3 divide-y divide-slate-100 dark:divide-slate-800">
                  {items.map((it, i) => (
                    <li key={it.id || i} className="flex gap-3 py-3 first:pt-0">
                      <div className="w-11 shrink-0 pt-0.5 text-right">
                        <p className="text-sm font-semibold tabular-nums text-slate-700 dark:text-slate-200">
                          {fmtTime(it.published_at)}
                        </p>
                        <p className="text-[10px] tabular-nums text-slate-400 dark:text-slate-500">
                          {String(it.published_at || '').slice(5, 10)}
                        </p>
                      </div>
                      <span
                        aria-hidden="true"
                        className="mt-2 h-2 w-2 shrink-0 rounded-full bg-brand-500"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge tone="slate">{it.source || SOURCE_TABS.find((t) => t.value === source)?.label}</Badge>
                          {i === 0 ? <Badge tone="rose">最新</Badge> : null}
                          {it.star ? <Badge tone="amber">★ 重点</Badge> : null}
                        </div>
                        <p className="mt-1 text-sm font-medium leading-6 text-slate-800 dark:text-slate-100">
                          {it.title}
                        </p>
                        {it.content && it.content !== it.title ? (
                          <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">{it.content}</p>
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ul>
                <Pagination
                  page={page}
                  pageSize={PAGE_SIZE}
                  total={flash.data.total || 0}
                  onChange={setPage}
                />
              </>
            )}
          </Card>
        </div>

        <RadarCard data={radar.data} loading={radar.loading} error={radar.error} onRetry={radar.reload} />
      </div>
    </PageShell>
  )
}
