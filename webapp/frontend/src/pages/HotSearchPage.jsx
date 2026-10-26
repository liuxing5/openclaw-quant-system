/**
 * 全网热搜（/info/hot-search）：榜单式渲染 + 横向热度条。
 *
 * 排名是最强的视觉线索：前三名用金/银/铜大号序号，其余用普通序号；
 * 热度是绝对值差异很大的量，所以用「相对榜首的横向条 + 绝对值」双重编码。
 * 平台下拉始终保留——后端当前只提供一个平台时 UI 不塌，接入新源即刻生效。
 */
import { useMemo, useState } from 'react'
import { AsyncBlock, Badge, Button, Card, EmptyState, PageShell, SectionHeader, Select, StatTile } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDateTime, fmtNum } from '../lib/format.js'

const PLATFORM_OPTIONS = [
  { value: 'eastmoney', label: '东方财富股吧' },
  { value: 'cls', label: '财联社热点' },
  { value: 'sina', label: '新浪财经' },
  { value: 'weibo', label: '微博财经' },
]

const TOP_STYLE = [
  'bg-amber-400 text-white shadow-[0_2px_8px_rgba(251_191_36,0.45)]', // 金
  'bg-slate-400 text-white', // 银
  'bg-orange-500 text-white', // 铜
]

/** strength 可能缺失：按名次给出可解释的默认强度，不编造具体热度 */
function strengthOf(item, rank) {
  const s = item.strength
  if (s !== undefined && s !== null && s !== '') {
    if (typeof s === 'number') return { text: `强度 ${fmtNum(s, 0)}`, tone: s >= 80 ? 'rose' : s >= 50 ? 'amber' : 'slate' }
    const map = {
      爆热: 'rose',
      hot: 'rose',
      热: 'rose',
      热议: 'amber',
      warm: 'amber',
      关注: 'blue',
      top: 'blue',
    }
    return { text: String(s), tone: map[String(s)] || 'slate' }
  }
  if (rank <= 3) return { text: '爆热', tone: 'rose' }
  if (rank <= 10) return { text: '热议', tone: 'amber' }
  return { text: '上榜', tone: 'slate' }
}

function splitTitle(item) {
  const raw = item.title || item.name || item.stock_name || '—'
  const parts = raw.split('·').map((s) => s.trim())
  if (parts.length >= 2) return { name: parts[0], code: parts.slice(1).join(' · ') }
  return { name: item.name || item.stock_name || raw, code: item.code || item.ts_code || '' }
}

export default function HotSearchPage() {
  const [platform, setPlatform] = useState('eastmoney')
  const { data, loading, error, reload } = useApi(
    () => api.get('/info/hot-search', { platform, limit: 50 }),
    [platform],
  )

  const items = useMemo(() => (data?.items || []).map((it, i) => ({ ...it, rank: it.rank || i + 1 })), [data])
  const maxHeat = Math.max(1, ...items.map((it) => Number(it.heat) || 0))
  const totalHeat = items.reduce((sum, it) => sum + (Number(it.heat) || 0), 0)
  const updated = data?.updated_at || data?.as_of || data?.fetched_at || null

  return (
    <PageShell
      title="全网热搜"
      subtitle="聚合股吧人气、财经快讯与关键词热搜，只看金融相关热点。"
      actions={
        <div className="flex items-center gap-2">
          <Select value={platform} options={PLATFORM_OPTIONS} onChange={setPlatform} aria-label="选择平台" />
          <Button size="sm" variant="secondary" onClick={reload} loading={loading && !!data}>
            刷新
          </Button>
        </div>
      }
    >
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="上榜条目" value={fmtNum(items.length, 0)} sub="当前榜单" />
        <StatTile
          label="总提及热度"
          value={totalHeat ? fmtNum(totalHeat, 0) : '—'}
          sub={items.length ? `榜首 ${fmtNum(maxHeat, 0)}` : '暂无数据'}
        />
        <StatTile label="数据更新" value={updated ? fmtDateTime(updated) : '—'} />
        <StatTile
          label="平台"
          value={PLATFORM_OPTIONS.find((p) => p.value === platform)?.label || platform}
          sub={`platform=${platform}`}
        />
      </div>

      <Card>
        <SectionHeader
          title="热搜榜"
          subtitle={`共 ${items.length} 条 · 热度条以榜首为基准`}
          badge={data?.platform ? <Badge tone="blue">{data.platform}</Badge> : null}
        />

        <AsyncBlock
          loading={loading}
          error={error}
          onRetry={reload}
          empty={!loading && !error && items.length === 0}
          emptyText="暂无热搜数据"
          emptyHint="换一个平台，或稍后刷新重试"
          loadingLabel="热搜榜单加载中…"
        >
          <ol className="mt-3 divide-y divide-slate-100 dark:divide-slate-800">
            {items.map((it) => {
              const rank = it.rank
              const { name, code } = splitTitle(it)
              const heat = Number(it.heat) || 0
              const width = Math.max(2, Math.round((heat / maxHeat) * 100))
              const st = strengthOf(it, rank)
              return (
                <li key={it.id || `${it.platform || ''}-${rank}`} className="flex items-center gap-3 py-2.5">
                  <span
                    className={`grid size-8 shrink-0 place-items-center rounded-lg text-sm font-bold tabular-nums ${
                      rank <= 3 ? TOP_STYLE[rank - 1] : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'
                    }`}
                  >
                    {rank}
                  </span>

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{name}</span>
                      {code ? (
                        <span className="shrink-0 text-xs tabular-nums text-slate-400 dark:text-slate-500">{code}</span>
                      ) : null}
                      <Badge tone={st.tone}>{st.text}</Badge>
                    </div>
                    <div className="mt-1.5 flex items-center gap-2">
                      <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                        <span
                          className="block h-full rounded-full bg-gradient-to-r from-brand-400 to-rose-500"
                          style={{ width: `${width}%` }}
                        />
                      </span>
                      <span className="w-24 shrink-0 text-right text-xs tabular-nums text-slate-500 dark:text-slate-400">
                        热度 {fmtNum(heat, 0)}
                      </span>
                    </div>
                  </div>
                </li>
              )
            })}
          </ol>
        </AsyncBlock>
      </Card>
    </PageShell>
  )
}
