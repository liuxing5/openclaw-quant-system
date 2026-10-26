/**
 * 连板排行（/limit-ladder）：按连板高度分层的涨停梯队。
 *
 * 为什么用「分层卡片」而不是一张普通表格：这一页唯一的核心维度是板高（几连板），
 * 层与层的高度差必须一眼可辨——用递减色阶 + 高度条 + 大号层数表达，
 * 个股的行情明细（换手/成交额/封单额）降级为层内的次要小卡。
 *
 * 接口 GET /api/v1/limit-ladder（min_level / board 作为筛选参数下发），
 * 同时兼容本地后端的 {tiers:[{streak,members}]} 与原站的 {levels,items} 两种结构；
 * 涨停时间、封单额这类字段后端可能尚未返回，因此所有明细列都按「有值才渲染」处理。
 */
import React, { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AsyncBlock, Badge, Button, Card, PageShell, SectionHeader, Select, StatTile, Tabs } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { baseCode, boardOf, fmtAmount, fmtDate, fmtNum, fmtPct, fmtTime, pctTone } from '../lib/format.js'

const POLL_MS = 60_000

/** 板高筛选色阶：最高板最深、首板最浅，深浅本身就是「高度」的第一视觉线索 */
const TIER_RAMP = ['#be123c', '#e11d48', '#f43f5e', '#fb7185', '#fda4af', '#fecdd3']

const BOARD_TABS = [
  { value: '', label: '全部' },
  { value: 'A主板', label: '主板' },
  { value: '创业板', label: '创业板' },
  { value: '科创板', label: '科创板' },
  { value: '北交所', label: '北交所' },
]

const num = (v) =>
  v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? null : Number(v)

/** 封板率在两套实现里口径不一（0.8723 与 87.23），这里统一兜底成百分数文本 */
function fmtRate(v) {
  const n = num(v)
  if (n === null) return '—'
  return `${(Math.abs(n) > 1 ? n : n * 100).toFixed(1)}%`
}

function tierColor(level, maxLevel) {
  const span = Math.max(1, (maxLevel || 1) - 1)
  const ratio = Math.min(1, Math.max(0, ((level || 1) - 1) / span))
  const idx = TIER_RAMP.length - 1 - Math.round(ratio * (TIER_RAMP.length - 1))
  return TIER_RAMP[idx]
}

/* ------------------------------------------------------------------ 数据规整 */

function normalizeMember(raw, level) {
  const code = raw.ts_code || raw.symbol || raw.code || ''
  return {
    code,
    name: raw.name || raw.stock_name || baseCode(code),
    level: raw.level ?? raw.streak ?? level ?? 1,
    board: raw.board || boardOf(code),
    status: raw.status || null,
    limit_up: raw.limit_up ?? raw.seal_time ?? null,
    pct_chg: num(raw.pct_chg),
    close: num(raw.close ?? raw.price),
    turnover_rate: num(raw.turnover_rate),
    amount: num(raw.amount),
    seal_amount: num(raw.seal_amount ?? raw.seal ?? raw.fengdan),
    is_broken: !!raw.is_broken,
    is_st: !!raw.is_st,
  }
}

/** 两种返回结构统一成 {summary, tiers:[{level,count,members}]}（层按板高降序） */
function normalizeLadder(raw) {
  if (!raw || typeof raw !== 'object') return null
  const summary = {
    trade_date: raw.trade_date,
    updated_at: raw.updated_at,
    highest: num(raw.max_streak ?? raw.highest_level) ?? 0,
    limit_up: num(raw.limit_up ?? raw.limit_up_count),
    limit_down: num(raw.limit_down ?? raw.limit_down_count),
    broken: num(raw.broken ?? raw.broken_count),
    touched: num(raw.touched),
    seal_rate: num(raw.seal_rate),
    broken_rate: num(raw.broken_rate),
  }

  let tiers = []
  if (Array.isArray(raw.tiers)) {
    tiers = raw.tiers.map((t) => {
      const level = t.streak ?? t.level ?? 1
      const members = Array.isArray(t.members) ? t.members : []
      return {
        level: Number(level) || 1,
        count: num(t.count) ?? members.length,
        members: members.map((m) => normalizeMember(m, level)),
      }
    })
  } else if (Array.isArray(raw.levels)) {
    // 原站结构：levels 给分布、items 给明细，需要按 level 归并
    const byLevel = new Map()
    for (const it of Array.isArray(raw.items) ? raw.items : []) {
      const lv = Number(it.level ?? it.streak ?? 1) || 1
      if (!byLevel.has(lv)) byLevel.set(lv, [])
      byLevel.get(lv).push(normalizeMember(it, lv))
    }
    tiers = raw.levels.map((l) => {
      const members = byLevel.get(Number(l.level)) || []
      return { level: Number(l.level) || 1, count: num(l.count) ?? members.length, members }
    })
  }

  tiers = tiers.filter((t) => Number.isFinite(t.level)).sort((a, b) => b.level - a.level)
  if (!summary.highest && tiers.length) summary.highest = tiers[0].level
  return { summary, tiers }
}

/* ---------------------------------------------------------------- 统计条 / 层卡 */

function StatStrip({ summary, highest }) {
  const tiles = [
    { label: '涨停', value: fmtNum(summary.limit_up, 0), tone: 'text-rose-600 dark:text-rose-400' },
    { label: '跌停', value: fmtNum(summary.limit_down, 0), tone: 'text-emerald-600 dark:text-emerald-400' },
    { label: '炸板', value: fmtNum(summary.broken, 0), tone: 'text-amber-600 dark:text-amber-400' },
    { label: '触板', value: fmtNum(summary.touched, 0), tone: 'text-slate-900 dark:text-slate-100' },
    { label: '封板率', value: fmtRate(summary.seal_rate), tone: 'text-slate-900 dark:text-slate-100' },
    { label: '最高板', value: highest ? `${highest} 板` : '—', tone: 'text-rose-600 dark:text-rose-400' },
  ]
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
      {tiles.map((t) => (
        <StatTile key={t.label} label={t.label} value={t.value} tone={t.tone} />
      ))}
    </div>
  )
}

/** 连板高度统计条：从最高板到首板逐级铺开，0 也保留，缺口本身就是信息 */
function LevelBar({ levels, maxLevel, maxCount }) {
  return (
    <div className="space-y-1.5">
      {levels.map((lv) => {
        const color = tierColor(lv.level, maxLevel)
        const pct = maxCount > 0 ? Math.max(lv.count > 0 ? 2 : 0, (lv.count / maxCount) * 100) : 0
        return (
          <div key={lv.level} className="flex items-center gap-2">
            <span className="w-11 shrink-0 text-right text-[11px] font-medium text-slate-500 dark:text-slate-400">
              {lv.level <= 1 ? '首板' : `${lv.level}板`}
            </span>
            <div className="h-3 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
              <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
            </div>
            <span className="w-8 shrink-0 text-right text-[11px] font-semibold tabular-nums text-slate-600 dark:text-slate-300">
              {lv.count}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function MemberCard({ m, fields, onOpen }) {
  return (
    <button
      type="button"
      onClick={() => m.code && onOpen(m.code)}
      className="group rounded-lg border border-rose-200/80 bg-white px-2.5 py-2 text-left transition hover:border-rose-400 hover:shadow-sm dark:border-rose-500/25 dark:bg-slate-800/70 dark:hover:border-rose-400/60"
      title={`${m.name} ${m.code} · ${m.board}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-[13px] font-medium text-slate-800 group-hover:text-rose-600 dark:text-slate-100 dark:group-hover:text-rose-300">
          {m.name}
        </span>
        <Badge tone="rose" className="shrink-0">
          {m.level}板
        </Badge>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[10px] text-slate-400 dark:text-slate-500">
        <span className="tabular-nums">{baseCode(m.code)}</span>
        <span>{m.board}</span>
        {m.status ? <span>{m.status}</span> : null}
        {m.is_broken ? <span className="text-amber-500">炸板</span> : null}
        {m.is_st ? <span className="text-amber-500">ST</span> : null}
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px]">
        <span className={`font-semibold tabular-nums ${pctTone(m.pct_chg)}`}>{fmtPct(m.pct_chg)}</span>
        <span className="tabular-nums text-slate-500 dark:text-slate-400">收 {fmtNum(m.close, 2)}</span>
        {fields.time ? (
          <span className="tabular-nums text-slate-500 dark:text-slate-400" title="涨停时间">
            封于 {fmtTime(m.limit_up)}
          </span>
        ) : null}
        {fields.turnover ? (
          <span className="tabular-nums text-slate-500 dark:text-slate-400">换手 {fmtNum(m.turnover_rate, 1)}%</span>
        ) : null}
        {fields.amount ? (
          <span className="tabular-nums text-slate-500 dark:text-slate-400">额 {fmtAmount(m.amount)}</span>
        ) : null}
        {fields.seal ? (
          <span className="tabular-nums text-rose-500 dark:text-rose-400" title="封单额">
            封单 {fmtAmount(m.seal_amount)}
          </span>
        ) : null}
      </div>
    </button>
  )
}

function TierCard({ tier, maxLevel, maxCount, fields, onOpen }) {
  const color = tierColor(tier.level, maxLevel)
  const pct = maxCount > 0 ? Math.max(3, (tier.count / maxCount) * 100) : 0
  return (
    <Card className="overflow-hidden" style={{ borderLeft: `4px solid ${color}` }}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex items-baseline gap-1">
          <span className="text-3xl font-bold leading-none tabular-nums" style={{ color }}>
            {tier.level}
          </span>
          <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">板</span>
        </div>
        <div className="min-w-24 flex-1">
          <div className="flex items-center justify-between gap-2 text-[11px] text-slate-400">
            <span>
              {tier.level <= 1 ? '今日涨停（首板）' : '昨日连板晋级'}
              {tier.level === maxLevel && tier.level > 1 ? ' · 空间板' : ''}
            </span>
            <span className="font-semibold tabular-nums text-slate-600 dark:text-slate-300">{tier.count} 只</span>
          </div>
          <div className="mt-1 h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
            <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
          </div>
        </div>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
        {tier.members.map((m) => (
          <MemberCard key={m.code || m.name} m={m} fields={fields} onOpen={onOpen} />
        ))}
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------------ 页面 */

export default function LimitLadderPage() {
  const navigate = useNavigate()
  const [minLevel, setMinLevel] = useState(0)
  const [board, setBoard] = useState('')

  const { data, loading, error, reload } = useApi(
    () => api.get('/limit-ladder', { min_level: minLevel || undefined, board: board || undefined }),
    [minLevel, board],
  )

  // 静默轮询：页面隐藏时跳过，卸载时清理，避免后台标签页空转
  React.useEffect(() => {
    const id = setInterval(() => {
      if (!document.hidden) reload()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [reload])

  const ladder = useMemo(() => normalizeLadder(data), [data])

  // min_level / board 即使后端已按参数过滤，这里再兜一层，保证筛选在任何实现下都生效
  const view = useMemo(() => {
    if (!ladder) return null
    const tiers = ladder.tiers
      .map((t) => ({ ...t, members: t.members.filter((m) => !board || m.board === board) }))
      .filter((t) => t.level >= minLevel && t.members.length > 0)
    return { summary: ladder.summary, tiers }
  }, [ladder, minLevel, board])

  const levelOptions = useMemo(() => {
    const max = Math.max(ladder?.summary?.highest || 1, minLevel)
    const opts = [{ value: 0, label: '全部板高' }]
    for (let lv = 1; lv <= max; lv += 1) opts.push({ value: lv, label: `≥ ${lv} 板` })
    return opts
  }, [ladder, minLevel])

  // 明细列「有值才显示」：后端补上涨停时间/封单额后会自动出现，缺失时不留空列
  const fields = useMemo(() => {
    const all = (view?.tiers || []).flatMap((t) => t.members)
    return {
      time: all.some((m) => m.limit_up !== null && m.limit_up !== undefined),
      turnover: all.some((m) => m.turnover_rate !== null),
      amount: all.some((m) => m.amount !== null),
      seal: all.some((m) => m.seal_amount !== null),
    }
  }, [view])

  const levelRows = useMemo(() => {
    if (!view) return []
    const maxLevel = view.summary.highest || (view.tiers[0] ? view.tiers[0].level : 0)
    const counts = new Map(view.tiers.map((t) => [t.level, t.members.length]))
    const rows = []
    for (let lv = maxLevel; lv >= Math.max(1, minLevel); lv -= 1) {
      rows.push({ level: lv, count: counts.get(lv) || 0 })
    }
    return rows
  }, [view, minLevel])

  const maxCount = Math.max(1, ...levelRows.map((r) => r.count))
  // 层内卡片的高度条按「最多的一层」归一，才能横向比较每层的拥挤程度
  const maxTierCount = Math.max(1, ...(view?.tiers || []).map((t) => t.members.length))
  const shown = (view?.tiers || []).reduce((n, t) => n + t.members.length, 0)
  const highest = view?.summary?.highest || 0

  return (
    <PageShell
      title="连板排行"
      subtitle={
        view
          ? `连板梯队 · 数据日 ${fmtDate(view.summary.trade_date)}${
              view.summary.updated_at ? ` · 更新于 ${String(view.summary.updated_at).replace('T', ' ').slice(0, 16)}` : ''
            }`
          : '连板梯队 · 按连板高度分层'
      }
      actions={
        <div className="flex items-center gap-2">
          <span className="hidden text-[11px] text-slate-400 sm:inline">每 60 秒自动刷新</span>
          <Button variant="secondary" size="sm" onClick={reload} loading={loading && !!data}>
            立即刷新
          </Button>
        </div>
      }
    >
      <div className="space-y-4">
        {view ? <StatStrip summary={view.summary} highest={highest} /> : null}

        <AsyncBlock
          loading={loading}
          error={error}
          onRetry={reload}
          empty={!view || view.tiers.length === 0}
          emptyText="暂无连板个股"
          emptyHint="当日尚无涨停，或当前板高 / 板块筛选下没有标的"
          loadingLabel="连板梯队加载中"
        >
          {view ? (
            <div className="space-y-4">
              <div className="grid gap-4 lg:grid-cols-3">
                <Card className="lg:col-span-2">
                  <SectionHeader
                    title="连板高度统计"
                    subtitle={`${shown} 只在榜 · 从最高板逐级递减`}
                    badge={<Badge tone="rose">最高 {highest} 板</Badge>}
                  />
                  <div className="mt-3">
                    <LevelBar levels={levelRows} maxLevel={highest} maxCount={maxCount} />
                  </div>
                </Card>

                <Card>
                  <SectionHeader title="筛选" subtitle="按最低板数 / 板块缩小范围" />
                  <div className="mt-3 space-y-3">
                    <div>
                      <p className="mb-1 text-[11px] text-slate-400">最低板数</p>
                      <Select
                        className="w-full"
                        value={minLevel}
                        onChange={(v) => setMinLevel(Number(v))}
                        options={levelOptions}
                      />
                    </div>
                    <div>
                      <p className="mb-1 text-[11px] text-slate-400">板块</p>
                      <Tabs tabs={BOARD_TABS} value={board} onChange={setBoard} />
                    </div>
                  </div>
                </Card>
              </div>

              <div className="space-y-4">
                {view.tiers.map((tier) => (
                  <TierCard
                    key={tier.level}
                    tier={tier}
                    maxLevel={highest}
                    maxCount={maxTierCount}
                    fields={fields}
                    onOpen={(code) => navigate(`/stock-analysis/${code}`)}
                  />
                ))}
              </div>
            </div>
          ) : null}
        </AsyncBlock>
      </div>
    </PageShell>
  )
}
