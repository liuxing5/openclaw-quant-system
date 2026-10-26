/**
 * 每日复盘（/review）：左侧交易日索引 + 右侧复盘正文，顶部情绪历史曲线。
 *
 * 关键交互：选中某日若后端返回 404，说明该日报告尚未撰写，页面会**自动**调用
 * `POST /reviews/{date}/generate` 并显示「正在生成复盘…」；每个交易日只自动触发
 * 一次，之后失败走可重试的错误态，避免后端持续 404 时陷入死循环。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import Chart, { ACCENT, TIP } from '../components/Chart.jsx'
import {
  AsyncBlock,
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  PageShell,
  Pagination,
  SectionHeader,
  Spinner,
  StatTile,
} from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtAmount, fmtNum, fmtPct, pctTone } from '../lib/format.js'
import { toast } from '../lib/toast.jsx'

const PAGE_SIZE = 20

const MOOD_TONE = {
  亢奋: 'rose',
  活跃: 'rose',
  温和: 'blue',
  低迷: 'amber',
  冰点: 'emerald',
}

const METRIC_LABELS = {
  amount: '两市成交额',
  amount_today: '当日成交额',
  amount_avg20: '20 日均额',
  up_count: '上涨家数',
  down_count: '下跌家数',
  flat_count: '平盘家数',
  limit_up: '涨停家数',
  limit_down: '跌停家数',
  touched: '触板家数',
  broken: '炸板家数',
  seal_rate: '封板率',
  up_ratio: '上涨占比',
  streak_max: '最高连板',
  turnover_rate: '全市场换手',
  north_inflow: '北向净流入',
  score: '情绪得分',
}

const LEADER_LABELS = {
  gainers: '领涨',
  top_gainers: '领涨',
  gain: '领涨',
  leaders_up: '领涨',
  losers: '领跌',
  top_losers: '领跌',
  loss: '领跌',
  leaders_down: '领跌',
  hot_sectors: '热板块',
  sectors: '热板块',
  boards: '热板块',
  concepts: '热概念',
  lhb: '龙虎榜',
  dragon_tiger: '龙虎榜',
  limit_up: '涨停梯队',
}

const todayStr = () => {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function metricLabel(key) {
  return METRIC_LABELS[key] || key.replace(/_/g, ' ')
}

function metricValue(key, v) {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v !== 'number') return String(v)
  if (/amount|inflow|capital/i.test(key)) return fmtAmount(v)
  if (/rate|pct/i.test(key)) return `${fmtNum(v, 2)}%`
  return fmtNum(v, Number.isInteger(v) ? 0 : 2)
}

/** 领涨/领跌/热板块/龙虎榜：后端给数组或对象都要能渲染，缺字段不报错 */
function LeaderValue({ value }) {
  if (Array.isArray(value)) {
    if (value.length === 0) return <p className="text-xs text-slate-400">暂无数据</p>
    return (
      <ul className="space-y-1">
        {value.slice(0, 8).map((it, i) => {
          const isObj = it && typeof it === 'object'
          const name = isObj ? it.name || it.title || it.ts_code || it.stock_name || '—' : String(it)
          const pct = isObj ? it.pct_chg ?? it.change_pct : null
          return (
            <li key={i} className="flex items-center justify-between gap-2 text-xs">
              <span className="min-w-0 truncate text-slate-600 dark:text-slate-300">
                <span className="mr-1 tabular-nums text-slate-400">{i + 1}</span>
                {name}
                {isObj && it.ts_code ? (
                  <span className="ml-1 text-[10px] tabular-nums text-slate-400">{it.ts_code}</span>
                ) : null}
              </span>
              {pct !== null && pct !== undefined ? (
                <span className={`shrink-0 tabular-nums font-medium ${pctTone(pct)}`}>{fmtPct(pct)}</span>
              ) : null}
            </li>
          )
        })}
      </ul>
    )
  }
  if (value && typeof value === 'object') {
    return (
      <ul className="space-y-1">
        {Object.entries(value).map(([k, v]) => (
          <li key={k} className="flex items-center justify-between gap-2 text-xs">
            <span className="text-slate-500 dark:text-slate-400">{k}</span>
            <span className="tabular-nums text-slate-700 dark:text-slate-200">{String(v)}</span>
          </li>
        ))}
      </ul>
    )
  }
  return <p className="text-xs text-slate-600 dark:text-slate-300">{String(value)}</p>
}

function ReviewBody({ content, date }) {
  const sections = content.sections || []
  const metrics = Object.entries(content.metrics || {})
  const leaders = Object.entries(content.leaders || {})
  const mood = content.mood || ''

  return (
    <div className="space-y-4">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="slate">{date}</Badge>
          {mood ? <Badge tone={MOOD_TONE[mood] || 'slate'}>情绪 {mood}</Badge> : null}
          {content.score !== null && content.score !== undefined ? (
            <Badge tone={MOOD_TONE[mood] || 'blue'}>得分 {fmtNum(content.score, 0)}</Badge>
          ) : null}
        </div>
        <h2 className="mt-2 text-lg font-semibold text-slate-900 dark:text-slate-100">
          {content.title || `${date} 复盘`}
        </h2>
        {content.summary ? (
          <p className="mt-2 border-l-2 border-brand-500 pl-3 text-sm leading-6 text-slate-600 dark:border-brand-400 dark:text-slate-300">
            {content.summary}
          </p>
        ) : null}
      </div>

      {metrics.length > 0 ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
          {metrics.map(([k, v]) => (
            <StatTile key={k} label={metricLabel(k)} value={metricValue(k, v)} />
          ))}
        </div>
      ) : null}

      {sections.map((s, i) => (
        <section key={i}>
          <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-800 dark:text-slate-100">
            <span className="h-3.5 w-1 rounded-full bg-brand-500" aria-hidden="true" />
            {s.heading}
          </h3>
          <ul className="mt-2 space-y-1.5 pl-4">
            {(s.bullets || []).map((b, bi) => (
              <li key={bi} className="list-disc text-sm leading-6 text-slate-600 dark:text-slate-300">
                {b}
              </li>
            ))}
          </ul>
        </section>
      ))}

      {leaders.length > 0 ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {leaders.map(([k, v]) => (
            <Card key={k} className="p-3">
              <p className="mb-2 text-xs font-semibold text-slate-700 dark:text-slate-200">
                {LEADER_LABELS[k] || k.replace(/_/g, ' ')}
              </p>
              <LeaderValue value={v} />
            </Card>
          ))}
        </div>
      ) : null}
    </div>
  )
}

export default function ReviewPage() {
  const [page, setPage] = useState(1)
  const [date, setDate] = useState('')
  const [generating, setGenerating] = useState(false)
  const [genError, setGenError] = useState(null)
  const autoTried = useRef(new Set())

  const list = useApi(() => api.get('/reviews', { page, size: PAGE_SIZE }), [page])
  const sentiment = useApi(() => api.get('/sentiment/history', { days: 180 }), [])
  const detail = useApi(() => api.get(`/reviews/${date}`, { region: 'cn' }), [date], { enabled: !!date })

  // 列表到位后默认选中最新一期复盘
  useEffect(() => {
    const items = list.data?.items || []
    if (!date && items.length > 0) setDate(items[0].trade_date)
  }, [list.data, date])

  async function generate(target) {
    if (!target) return
    setGenerating(true)
    setGenError(null)
    try {
      await api.post(`/reviews/${target}/generate`, { region: 'cn' })
      toast.success('复盘已生成', target)
      await detail.reload()
    } catch (e) {
      setGenError(e)
      toast.error('生成复盘失败', e.message)
    } finally {
      setGenerating(false)
    }
  }

  // 404 = 该交易日尚无复盘 → 自动触发一次生成
  useEffect(() => {
    const err = detail.error
    if (!err || err.status !== 404 || !date) return
    if (autoTried.current.has(date)) return
    autoTried.current.add(date)
    generate(date)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail.error, date])

  const chartOption = useMemo(() => {
    const rows = sentiment.data?.items || []
    return {
      grid: { left: 38, right: 16, top: 18, bottom: 26 },
      tooltip: {
        ...TIP,
        formatter: (ps) => {
          const p = Array.isArray(ps) ? ps[0] : ps
          const row = rows[p.dataIndex] || {}
          return `${row.trade_date}<br/>情绪得分 <b>${row.score ?? '—'}</b>${row.mood ? ` · ${row.mood}` : ''}`
        },
      },
      xAxis: {
        type: 'category',
        data: rows.map((r) => String(r.trade_date).slice(5)),
        axisLine: { lineStyle: { color: 'rgba(148,163,184,0.35)' } },
        axisLabel: { color: '#94a3b8', fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        min: 0,
        max: 100,
        axisLabel: { color: '#94a3b8', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } },
      },
      series: [
        {
          name: '情绪得分',
          type: 'line',
          showSymbol: false,
          smooth: true,
          // score 可能为 null（数据缺失）：保留断点，不用 0 冒充
          connectNulls: false,
          data: rows.map((r) => (r.score === null || r.score === undefined ? null : Number(r.score))),
          lineStyle: { color: ACCENT, width: 2 },
          itemStyle: { color: ACCENT },
          areaStyle: { color: 'rgba(99,102,241,0.12)' },
        },
      ],
    }
  }, [sentiment.data])

  const items = list.data?.items || []
  const latestTradeDate =
    (sentiment.data?.items || []).filter((r) => r.trade_date).slice(-1)[0]?.trade_date || todayStr()

  const generateLatest = () => {
    autoTried.current.delete(latestTradeDate)
    setDate(latestTradeDate)
    generate(latestTradeDate)
  }

  const waitingGen = generating || (detail.error?.status === 404 && !autoTried.current.has(date))

  return (
    <PageShell
      title="每日复盘"
      subtitle="汇总当日大盘、板块、龙虎榜与情绪数据，生成可回看的复盘报告。"
      actions={
        <Button size="sm" onClick={generateLatest} loading={generating}>
          生成最近交易日
        </Button>
      }
    >
      <Card>
        <SectionHeader
          title="市场情绪历史"
          subtitle={`近 ${(sentiment.data?.items || []).length || 0} 个交易日 · 得分区间 0—100 · 缺数据的日子断点显示`}
          badge={<Badge tone="blue">近 180 天</Badge>}
        />
        <AsyncBlock
          loading={sentiment.loading}
          error={sentiment.error}
          onRetry={sentiment.reload}
          empty={!sentiment.loading && !sentiment.error && (sentiment.data?.items || []).length === 0}
          emptyText="暂无情绪历史数据"
          emptyHint="完成一次复盘生成后即可看到情绪曲线"
          loadingLabel="情绪序列加载中…"
        >
          {sentiment.data ? <Chart option={chartOption} height={200} className="mt-2" /> : null}
        </AsyncBlock>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        {/* 左：交易日索引 */}
        <Card className="h-fit">
          <SectionHeader title="历史复盘" subtitle={`共 ${list.data?.total ?? items.length} 期`} />
          {!list.data ? (
            <AsyncBlock loading={list.loading} error={list.error} onRetry={list.reload} loadingLabel="复盘列表加载中…" />
          ) : items.length === 0 ? (
            <div className="mt-3 space-y-3">
              <EmptyState text="尚无复盘" hint="点击下方按钮生成最近交易日的复盘报告" />
              <Button className="w-full" onClick={generateLatest} loading={generating}>
                生成最近交易日
              </Button>
            </div>
          ) : (
            <>
              <ul className="mt-3 max-h-[420px] space-y-1 overflow-y-auto thin-scroll pr-1">
                {items.map((it) => {
                  const active = it.trade_date === date
                  return (
                    <li key={it.trade_date}>
                      <button
                        type="button"
                        onClick={() => setDate(it.trade_date)}
                        className={`w-full rounded-lg border px-2.5 py-2 text-left transition ${
                          active
                            ? 'border-brand-500 bg-brand-50 text-brand-700 dark:border-brand-400/60 dark:bg-brand-500/10 dark:text-brand-200'
                            : 'border-transparent hover:border-slate-200 hover:bg-slate-50 dark:hover:border-slate-700 dark:hover:bg-slate-800/60'
                        }`}
                      >
                        <span className="flex items-center justify-between gap-2">
                          <span className="text-xs font-semibold tabular-nums">{it.trade_date}</span>
                          <span className="flex items-center gap-1">
                            {it.mood ? (
                              <Badge tone={MOOD_TONE[it.mood] || 'slate'}>{it.mood}</Badge>
                            ) : null}
                            {it.score !== null && it.score !== undefined ? (
                              <span className="text-[11px] tabular-nums text-slate-500 dark:text-slate-400">
                                {fmtNum(it.score, 0)} 分
                              </span>
                            ) : null}
                          </span>
                        </span>
                        <span className="mt-0.5 block truncate text-[11px] text-slate-500 dark:text-slate-400">
                          {it.title || it.summary || '复盘报告'}
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
              <Pagination page={page} pageSize={PAGE_SIZE} total={list.data.total || 0} onChange={setPage} />
            </>
          )}
        </Card>

        {/* 右：复盘正文 */}
        <Card className="lg:col-span-2">
          {!date ? (
            <EmptyState text="请选择左侧交易日" hint="选中日期后在此加载当日复盘正文" />
          ) : waitingGen ? (
            <Spinner label={`正在生成复盘…（${date}）`} />
          ) : genError ? (
            <div className="space-y-3">
              <ErrorState error={genError} onRetry={() => generate(date)} />
              <p className="text-xs text-slate-500 dark:text-slate-400">
                生成失败不会影响已存在的复盘，修正后可再次点击重试。
              </p>
            </div>
          ) : !detail.data ? (
            <AsyncBlock
              loading={detail.loading}
              error={detail.error}
              onRetry={detail.reload}
              loadingLabel="复盘正文加载中…"
            />
          ) : (
            <ReviewBody content={detail.data.content || detail.data} date={date} />
          )}
        </Card>
      </div>
    </PageShell>
  )
}
