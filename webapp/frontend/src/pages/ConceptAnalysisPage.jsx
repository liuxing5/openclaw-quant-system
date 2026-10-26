/**
 * 概念分析（/concept-analysis 与 /concept-analysis/:code）——同一张页面的两级下钻。
 *
 * 无 :code：榜单模式（概览四格 + 领涨/领跌 Top10 + 可排序概念矩阵 + 分页）。
 * 有 :code：板块详情（行情卡 + 近 N 日走势 + 成分股，含领涨股高亮）。
 *
 * 为什么双模式共用一个文件：原站本来就是「榜单 → 下钻」的一张页面，
 * URL 即状态（:code 进 useApi 的 deps），浏览器前进后退天然触发重新拉取。
 * 后端 /boards/concepts/{code}/members 由并行单元实现，可能 404，
 * 因此成分股优先用该接口，失败时降级到板块详情里自带的 members，两者都没有才报错。
 */
import React, { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import Chart, { ACCENT, DOWN, TIP, UP } from '../components/Chart.jsx'
import {
  AsyncBlock,
  Badge,
  Button,
  Card,
  PageShell,
  Pagination,
  SectionHeader,
  StatTile,
  Tabs,
} from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { baseCode, fmtAmount, fmtDate, fmtNum, fmtPct, pctBg, pctTone } from '../lib/format.js'

const BASE = '/concept-analysis'
const RESOURCE = 'concepts'
const KIND = 'concept'
const KIND_LABEL = '概念'
const POLL_MS = 60_000
const PAGE_SIZE = 50

const SORT_TABS = [
  { value: 'pct_chg_desc', label: '涨幅榜' },
  { value: 'pct_chg_asc', label: '跌幅榜' },
  { value: 'net_inflow', label: '净流入榜' },
]

const num = (v) =>
  v === null || v === undefined || v === '' || Number.isNaN(Number(v)) ? null : Number(v)

/** URL 里的 code 可能被编码（THS:智谱AI → THS%3A...），只在确有 % 时解一次 */
function routeCode(raw) {
  if (!raw) return ''
  if (!raw.includes('%')) return raw
  try {
    return decodeURIComponent(raw)
  } catch {
    return raw
  }
}

/**
 * 板块列表请求。契约写的是 page/size，当前后端实现是 limit/offset，
 * 两套参数值保持一致即可在两种实现下取到同一页数据；
 * sort 枚举在并行开发中也可能互换（pct_chg_desc / pct_chg），422 时兜底换一个再试一次。
 */
async function fetchBoards(resource, params) {
  try {
    return await api.get(resource, params)
  } catch (e) {
    if (e && e.status === 422 && params.sort) {
      return api.get(resource, { ...params, sort: params.sort === 'pct_chg_desc' ? 'pct_chg' : 'pct_chg_desc' })
    }
    throw e
  }
}

function pickItems(raw) {
  if (Array.isArray(raw)) return raw
  if (raw && Array.isArray(raw.items)) return raw.items
  return []
}

function normRow(r) {
  const code = r.code || r.sector_code || ''
  return {
    code,
    name: r.name || r.sector_name || '',
    trade_date: r.trade_date || null,
    pct_chg: num(r.pct_chg),
    net_inflow: num(r.net_inflow),
    amount: num(r.amount),
    turnover_rate: num(r.turnover_rate),
    up_count: num(r.up_count),
    down_count: num(r.down_count),
    stock_count: num(r.stock_count) ?? num(r.member_count),
    leader: String(r.leader_name || r.lead_stock_name || '').trim(),
    leader_pct: num(r.leader_pct_chg),
    rank: r.rank,
  }
}

function normMember(r) {
  const code = r.ts_code || r.symbol || r.code || ''
  return {
    code,
    name: r.name || r.stock_name || baseCode(code),
    price: num(r.price ?? r.close),
    pct_chg: num(r.pct_chg),
    amount: num(r.amount),
    turnover_rate: num(r.turnover_rate),
    is_limit_up: !!r.is_limit_up,
    leader_score: num(r.leader_score),
  }
}

function normalizeDetail(raw) {
  if (!raw || typeof raw !== 'object') return null
  const sector = raw.sector || raw.snapshot || null
  return {
    sector: sector ? normRow(sector) : null,
    history: Array.isArray(raw.history) ? raw.history : [],
    members: pickItems(raw.members).map(normMember),
    member_count: num(raw.member_count),
    // 后端用 {detail:"板块不存在"} 表示查无此板块（HTTP 200），按空数据处理
    missing: !sector,
  }
}

/* ------------------------------------------------------------------ 概览四格 */

function SplitTile({ up, down, sub }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3 dark:border-slate-700 dark:bg-slate-800/50">
      <p className="text-[11px] text-slate-500 dark:text-slate-400">涨跌板块</p>
      <p className="mt-1 flex items-baseline gap-1 text-lg font-semibold tabular-nums">
        <span className="text-rose-600 dark:text-rose-400">{num(up) ?? 0}</span>
        <span className="text-sm font-normal text-slate-400">/</span>
        <span className="text-emerald-600 dark:text-emerald-400">{num(down) ?? 0}</span>
      </p>
      <p className="mt-0.5 text-[11px] text-slate-400 dark:text-slate-500">{sub}</p>
    </div>
  )
}

function OverviewTiles({ top, bottom, listData, rows }) {
  const flow = useMemo(() => {
    let best = null
    for (const r of rows) {
      if (r.net_inflow === null) continue
      if (!best || r.net_inflow > best.net_inflow) best = r
    }
    return best
  }, [rows])

  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <StatTile
        label="最强主线"
        value={top ? top.name : '—'}
        sub={top ? fmtPct(top.pct_chg) : '暂无数据'}
        tone={top ? pctTone(top.pct_chg) : undefined}
      />
      <StatTile
        label="最大风险"
        value={bottom ? bottom.name : '—'}
        sub={bottom ? fmtPct(bottom.pct_chg) : '暂无数据'}
        tone={bottom ? pctTone(bottom.pct_chg) : undefined}
      />
      <SplitTile up={listData?.up} down={listData?.down} sub={`当前榜 ${rows.length} 个板块`} />
      <StatTile
        label="资金活跃"
        value={flow ? flow.name : '—'}
        sub={flow ? `${fmtAmount(flow.net_inflow)} 净流入` : '暂无数据'}
        tone={flow && flow.net_inflow > 0 ? 'text-rose-600 dark:text-rose-400' : undefined}
      />
    </div>
  )
}

/* -------------------------------------------------------------- 领涨 / 领跌 Top10 */

function TopPanel({ title, subtitle, tone, rows, loading, error, onRetry, counts, onPick }) {
  return (
    <Card>
      <SectionHeader
        title={title}
        subtitle={subtitle}
        badge={<Badge tone={tone}>{rows.length ? `Top ${rows.length}` : 'Top 10'}</Badge>}
      />
      <div className="mt-3">
        <AsyncBlock
          loading={loading}
          error={error}
          onRetry={onRetry}
          empty={rows.length === 0}
          emptyText="暂无数据"
          loadingLabel={`${title}加载中`}
        >
          <ul className="space-y-0.5">
            {rows.map((r, i) => (
              <li key={r.code || `${title}-${i}`}>
                <button
                  type="button"
                  onClick={() => r.code && onPick(r.code)}
                  className="flex w-full items-center gap-2 rounded-md px-1.5 py-1.5 text-left transition hover:bg-slate-50 dark:hover:bg-slate-800/70"
                  title={`${r.name} ${r.code}`}
                >
                  <span className="w-5 shrink-0 text-center text-[11px] tabular-nums text-slate-400">{i + 1}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium text-slate-700 dark:text-slate-200">
                      {r.name}
                    </span>
                    <span className="block text-[10px] text-slate-400">{counts(r)}</span>
                  </span>
                  <span className={`shrink-0 text-xs font-semibold tabular-nums ${pctTone(r.pct_chg)}`}>
                    {fmtPct(r.pct_chg)}
                  </span>
                  <span className="hidden w-20 shrink-0 text-right text-[11px] tabular-nums text-slate-400 sm:block">
                    {r.net_inflow === null ? '—' : fmtAmount(r.net_inflow)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </AsyncBlock>
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------------ 概念矩阵 */

function MatrixTable({ rows, loading, error, onRetry, page, onPage, total, sort, onSort, onPick }) {
  const hasUpDown = rows.some((r) => r.up_count !== null || r.down_count !== null)
  const hasAmount = rows.some((r) => r.amount !== null)
  const hasTurnover = rows.some((r) => r.turnover_rate !== null)
  const hasCount = rows.some((r) => r.stock_count !== null)

  return (
    <Card>
      <SectionHeader
        title={`${KIND_LABEL}矩阵`}
        subtitle={`按服务端排序取一页 · 共 ${total} 个${KIND_LABEL} · 点击进入详情`}
        action={<Tabs tabs={SORT_TABS} value={sort} onChange={onSort} />}
      />
      <div className="mt-3">
        <AsyncBlock
          loading={loading}
          error={error}
          onRetry={onRetry}
          empty={rows.length === 0}
          emptyText={`暂无${KIND_LABEL}板块数据`}
          emptyHint="板块快照同步后即可查看"
          loadingLabel="榜单加载中"
        >
          {rows.length ? (
            <>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[680px] text-xs">
                  <thead>
                    <tr className="text-left text-[11px] text-slate-400">
                      <th className="pb-1.5 font-medium">#</th>
                      <th className="pb-1.5 font-medium">{KIND_LABEL}</th>
                      <th className="pb-1.5 text-right font-medium">涨跌幅</th>
                      {hasUpDown ? (
                        <th className="pb-1.5 text-right font-medium">上涨 / 下跌</th>
                      ) : null}
                      {hasCount ? <th className="pb-1.5 text-right font-medium">成分</th> : null}
                      <th className="pb-1.5 text-right font-medium">主力净流入</th>
                      {hasAmount ? <th className="pb-1.5 text-right font-medium">成交额</th> : null}
                      {hasTurnover ? <th className="pb-1.5 text-right font-medium">换手</th> : null}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr
                        key={r.code || i}
                        onClick={() => r.code && onPick(r.code)}
                        className="cursor-pointer border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/60"
                      >
                        <td className="py-2 tabular-nums text-slate-400">{r.rank ?? (page - 1) * PAGE_SIZE + i + 1}</td>
                        <td className="py-2">
                          <span className="font-medium text-slate-700 dark:text-slate-200">{r.name}</span>
                          {r.leader ? (
                            <span className="ml-1.5 text-[10px] text-slate-400">领涨 {r.leader}</span>
                          ) : null}
                        </td>
                        <td className="py-2 text-right">
                          <span className={`inline-block rounded px-1.5 py-0.5 font-medium tabular-nums ${pctBg(r.pct_chg)}`}>
                            {fmtPct(r.pct_chg)}
                          </span>
                        </td>
                        {hasUpDown ? (
                          <td className="py-2 text-right tabular-nums">
                            <span className="text-rose-600 dark:text-rose-400">{r.up_count ?? '—'}</span>
                            <span className="mx-1 text-slate-300 dark:text-slate-600">/</span>
                            <span className="text-emerald-600 dark:text-emerald-400">{r.down_count ?? '—'}</span>
                          </td>
                        ) : null}
                        {hasCount ? (
                          <td className="py-2 text-right tabular-nums text-slate-500 dark:text-slate-400">
                            {r.stock_count === null ? '—' : `${fmtNum(r.stock_count, 0)} 只`}
                          </td>
                        ) : null}
                        <td
                          className={`py-2 text-right tabular-nums ${
                            r.net_inflow === null
                              ? 'text-slate-400'
                              : r.net_inflow > 0
                                ? 'text-rose-600 dark:text-rose-400'
                                : 'text-emerald-600 dark:text-emerald-400'
                          }`}
                        >
                          {r.net_inflow === null ? '—' : fmtAmount(r.net_inflow)}
                        </td>
                        {hasAmount ? (
                          <td className="py-2 text-right tabular-nums text-slate-500 dark:text-slate-400">
                            {fmtAmount(r.amount)}
                          </td>
                        ) : null}
                        {hasTurnover ? (
                          <td className="py-2 text-right tabular-nums text-slate-500 dark:text-slate-400">
                            {r.turnover_rate === null ? '—' : `${fmtNum(r.turnover_rate, 2)}%`}
                          </td>
                        ) : null}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination page={page} pageSize={PAGE_SIZE} total={total} onChange={onPage} />
            </>
          ) : null}
        </AsyncBlock>
      </div>
    </Card>
  )
}

/* -------------------------------------------------------------- 详情：走势 */

function HistoryCard({ history, days }) {
  const rows = useMemo(
    () => [...history].sort((a, b) => (String(a.trade_date) < String(b.trade_date) ? -1 : 1)),
    [history],
  )
  const option = useMemo(() => {
    const hasNet = rows.some((r) => num(r.net_inflow) !== null)
    return {
      grid: { left: 46, right: hasNet ? 54 : 12, top: 30, bottom: 30 },
      tooltip: { ...TIP, trigger: 'axis' },
      legend: {
        data: hasNet ? ['涨跌幅', '主力净流入'] : ['涨跌幅'],
        textStyle: { color: '#94a3b8', fontSize: 11 },
        top: 0,
        right: 4,
      },
      xAxis: {
        type: 'category',
        data: rows.map((r) => String(r.trade_date).slice(5)),
        axisLine: { lineStyle: { color: 'rgba(148,163,184,0.35)' } },
        axisLabel: { color: '#94a3b8', fontSize: 10 },
      },
      yAxis: [
        {
          type: 'value',
          axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v) => `${v}%` },
          splitLine: { lineStyle: { color: 'rgba(148,163,184,0.14)' } },
        },
        ...(hasNet
          ? [
              {
                type: 'value',
                axisLabel: { color: '#94a3b8', fontSize: 10, formatter: (v) => `${(v / 1e8).toFixed(0)}亿` },
                splitLine: { show: false },
              },
            ]
          : []),
      ],
      series: [
        {
          name: '涨跌幅',
          type: 'bar',
          barMaxWidth: 16,
          data: rows.map((r) => {
            const v = num(r.pct_chg)
            return {
              value: v,
              itemStyle: { color: (v ?? 0) >= 0 ? UP : DOWN, borderRadius: [3, 3, 0, 0] },
            }
          }),
        },
        ...(hasNet
          ? [
              {
                name: '主力净流入',
                type: 'line',
                yAxisIndex: 1,
                smooth: true,
                data: rows.map((r) => num(r.net_inflow)),
                lineStyle: { color: ACCENT, width: 2 },
                itemStyle: { color: ACCENT },
                showSymbol: false,
              },
            ]
          : []),
      ],
    }
  }, [rows])

  return (
    <Card>
      <SectionHeader
        title={`近 ${rows.length || days} 日板块走势`}
        subtitle="柱 = 单日涨跌幅 · 线 = 主力净流入（右轴）"
        badge={rows.length ? <Badge tone="slate">{fmtDate(rows[0]?.trade_date)} ~ {fmtDate(rows[rows.length - 1]?.trade_date)}</Badge> : null}
      />
      <div className="mt-2">
        <AsyncBlock
          loading={false}
          error={null}
          empty={rows.length < 2}
          emptyText="暂无历史走势"
          emptyHint="当前仅返回当日板块快照"
        >
          <Chart option={option} height={240} />
        </AsyncBlock>
      </div>
    </Card>
  )
}

/* -------------------------------------------------------------- 详情：成分股 */

function MembersCard({ rows, total, degraded, loading, error, onRetry, leaderName, onPick }) {
  const hasTurnover = rows.some((r) => r.turnover_rate !== null)
  const hasAmount = rows.some((r) => r.amount !== null)
  const hasScore = rows.some((r) => r.leader_score !== null)

  // 领涨股按板块快照里的领涨股名精确匹配；匹配不到时，龙头分最高的那一只即主龙头
  const matched = leaderName ? rows.findIndex((r) => r.name === leaderName) : -1
  const hiIdx = matched >= 0 ? matched : rows.findIndex((r) => r.leader_score !== null)
  const hiLabel = matched >= 0 ? '领涨' : '龙头'

  return (
    <Card>
      <SectionHeader
        title="成分股"
        subtitle={`按龙头分 / 涨幅排序 · ${rows.length} 只在列${total ? ` · 板块共 ${total} 只` : ''}`}
        badge={
          degraded ? (
            <Badge tone="amber">成分股接口暂不可用，展示板块快照成分</Badge>
          ) : hiIdx >= 0 ? (
            <Badge tone="rose">
              {hiLabel} {rows[hiIdx]?.name}
            </Badge>
          ) : null
        }
      />
      <div className="mt-3">
        <AsyncBlock
          loading={loading}
          error={error}
          onRetry={onRetry}
          empty={rows.length === 0}
          emptyText="暂无成分股数据"
          emptyHint="成分映射尚未同步，可稍后重试"
          loadingLabel="成分股加载中"
        >
          {rows.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-xs">
                <thead>
                  <tr className="text-left text-[11px] text-slate-400">
                    <th className="pb-1.5 font-medium">#</th>
                    <th className="pb-1.5 font-medium">股票</th>
                    <th className="pb-1.5 text-right font-medium">现价</th>
                    <th className="pb-1.5 text-right font-medium">涨跌幅</th>
                    {hasTurnover ? <th className="pb-1.5 text-right font-medium">换手率</th> : null}
                    {hasAmount ? <th className="pb-1.5 text-right font-medium">成交额</th> : null}
                    {hasScore ? <th className="pb-1.5 text-right font-medium">龙头分</th> : null}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr
                      key={r.code || i}
                      onClick={() => r.code && onPick(r.code)}
                      className={`cursor-pointer border-t border-slate-100 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/60 ${
                        i === hiIdx ? 'bg-rose-50/70 dark:bg-rose-500/10' : ''
                      }`}
                    >
                      <td className="py-2 tabular-nums text-slate-400">{i + 1}</td>
                      <td className="py-2">
                        <span className="font-medium text-slate-700 dark:text-slate-200">{r.name}</span>
                        <span className="ml-1.5 text-[10px] tabular-nums text-slate-400">{baseCode(r.code)}</span>
                        {i === hiIdx ? (
                          <Badge tone="rose" className="ml-1.5">
                            {hiLabel}
                          </Badge>
                        ) : null}
                        {r.is_limit_up ? (
                          <Badge tone="rose" className="ml-1.5">
                            涨停
                          </Badge>
                        ) : null}
                      </td>
                      <td className="py-2 text-right tabular-nums text-slate-600 dark:text-slate-300">
                        {fmtNum(r.price, 2)}
                      </td>
                      <td className={`py-2 text-right font-medium tabular-nums ${pctTone(r.pct_chg)}`}>
                        {fmtPct(r.pct_chg)}
                      </td>
                      {hasTurnover ? (
                        <td className="py-2 text-right tabular-nums text-slate-500 dark:text-slate-400">
                          {r.turnover_rate === null ? '—' : `${fmtNum(r.turnover_rate, 2)}%`}
                        </td>
                      ) : null}
                      {hasAmount ? (
                        <td className="py-2 text-right tabular-nums text-slate-500 dark:text-slate-400">
                          {fmtAmount(r.amount)}
                        </td>
                      ) : null}
                      {hasScore ? (
                        <td className="py-2 text-right font-semibold tabular-nums text-slate-700 dark:text-slate-200">
                          {r.leader_score === null ? '—' : fmtNum(r.leader_score, 1)}
                        </td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </AsyncBlock>
      </div>
    </Card>
  )
}

/* ---------------------------------------------------------------- 榜单模式 */

function ListView({ list, topUp, topDown, page, setPage, sort, setSort, onPick }) {
  const rows = useMemo(() => pickItems(list.data).map(normRow), [list.data])
  const ups = useMemo(() => pickItems(topUp.data).map(normRow), [topUp.data])
  const downs = useMemo(() => pickItems(topDown.data).map(normRow), [topDown.data])
  const total = num(list.data?.total) ?? rows.length
  const tradeDate = list.data?.trade_date || topUp.data?.trade_date

  const counts = (r) => {
    if (r.up_count !== null || r.down_count !== null) {
      return `${r.up_count ?? '—'}涨 ${r.down_count ?? '—'}跌`
    }
    if (r.stock_count !== null) return `${fmtNum(r.stock_count, 0)} 只成分`
    if (r.leader) return `领涨 ${r.leader}`
    return r.code || ''
  }

  return (
    <PageShell
      title={`${KIND_LABEL}分析`}
      subtitle={`${fmtDate(tradeDate)} · ${fmtNum(total, 0)} 个${KIND_LABEL} · 榜单每 60 秒自动刷新`}
      actions={
        <span className="hidden text-[11px] text-slate-400 sm:inline">点击任意{KIND_LABEL}查看成分与走势</span>
      }
    >
      <div className="space-y-4">
        <OverviewTiles top={ups[0]} bottom={downs[0]} listData={list.data} rows={rows} />

        <div className="grid gap-4 lg:grid-cols-2">
          <TopPanel
            title="领涨主线"
            subtitle="Top 10 · 按当日涨跌幅降序"
            tone="rose"
            rows={ups}
            loading={topUp.loading}
            error={topUp.error}
            onRetry={topUp.reload}
            counts={counts}
            onPick={onPick}
          />
          <TopPanel
            title="领跌方向"
            subtitle="Top 10 · 按当日涨跌幅升序"
            tone="emerald"
            rows={downs}
            loading={topDown.loading}
            error={topDown.error}
            onRetry={topDown.reload}
            counts={counts}
            onPick={onPick}
          />
        </div>

        <MatrixTable
          rows={rows}
          loading={list.loading}
          error={list.error}
          onRetry={list.reload}
          page={page}
          onPage={setPage}
          total={total}
          sort={sort}
          onSort={(v) => {
            setSort(v)
            setPage(1)
          }}
          onPick={onPick}
        />
      </div>
    </PageShell>
  )
}

/* ---------------------------------------------------------------- 详情模式 */

function DetailView({ code, detail, membersApi, onBack, onPick }) {
  const info = useMemo(() => normalizeDetail(detail.data), [detail.data])
  const sector = info?.sector
  const snapshotMembers = info ? info.members : []
  const apiRows = useMemo(() => pickItems(membersApi.data).map(normMember), [membersApi.data])

  // 成分股优先用专门接口，404 / 500 时降级到板块详情自带的成员列表
  const rows = apiRows.length ? apiRows : snapshotMembers
  const degraded = apiRows.length === 0 && snapshotMembers.length > 0 && !!membersApi.error

  const leaderName = sector?.leader || ''
  const leaderPct =
    num(sector?.leader_pct) ?? (leaderName ? rows.find((r) => r.name === leaderName)?.pct_chg ?? null : null)

  const tiles = []
  if (sector) {
    tiles.push({ label: '涨跌幅', value: fmtPct(sector.pct_chg), tone: pctTone(sector.pct_chg) })
    tiles.push({ label: '成交额', value: fmtAmount(sector.amount) })
    tiles.push({
      label: '主力净流入',
      value: fmtAmount(sector.net_inflow),
      tone:
        sector.net_inflow === null
          ? undefined
          : sector.net_inflow > 0
            ? 'text-rose-600 dark:text-rose-400'
            : 'text-emerald-600 dark:text-emerald-400',
    })
    if (sector.turnover_rate !== null) tiles.push({ label: '换手率', value: `${fmtNum(sector.turnover_rate, 2)}%` })
    if (sector.stock_count !== null) tiles.push({ label: '成分股数', value: `${fmtNum(sector.stock_count, 0)} 只` })
    tiles.push({ label: '领涨股', value: leaderName || '—', sub: leaderPct !== null ? fmtPct(leaderPct) : null })
  }

  const showBreadth = sector && (sector.up_count !== null || sector.down_count !== null)

  return (
    <PageShell
      title={`${KIND_LABEL}分析 / ${sector?.name || code}`}
      subtitle={
        sector
          ? `${fmtDate(sector.trade_date)} · ${KIND_LABEL}详情 · 成分 ${info?.member_count ?? rows.length} 只`
          : `${KIND_LABEL}详情`
      }
      actions={
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={onBack}>
            ← 返回榜单
          </Button>
          <Button variant="secondary" size="sm" onClick={detail.reload} loading={detail.loading && !!sector}>
            刷新
          </Button>
        </div>
      }
    >
      <div className="space-y-4">
        <AsyncBlock
          loading={detail.loading}
          error={detail.error}
          onRetry={detail.reload}
          empty={!info || info.missing || !sector}
          emptyText="板块不存在或暂无行情数据"
          emptyHint="可能代码已变更，可返回榜单重新选择"
          loadingLabel="板块详情加载中"
        >
          {sector ? (
            <div className="space-y-4">
              <Card>
                <SectionHeader
                  title="板块行情"
                  subtitle={`${sector.name} · ${sector.code}`}
                  badge={<Badge tone={sector.pct_chg > 0 ? 'rose' : sector.pct_chg < 0 ? 'emerald' : 'slate'}>{fmtPct(sector.pct_chg)}</Badge>}
                />
                <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
                  {tiles.map((t) => (
                    <StatTile key={t.label} label={t.label} value={t.value} sub={t.sub} tone={t.tone} />
                  ))}
                </div>
                {showBreadth ? (
                  <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50/60 p-3 dark:border-slate-700 dark:bg-slate-800/50">
                    <div className="flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400">
                      <span>板块内涨跌分布</span>
                      <span className="tabular-nums">
                        <b className="text-rose-600 dark:text-rose-400">{sector.up_count ?? '—'} 涨</b>
                        <span className="mx-1">/</span>
                        <b className="text-emerald-600 dark:text-emerald-400">{sector.down_count ?? '—'} 跌</b>
                      </span>
                    </div>
                    <div className="mt-2 flex h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                      <div
                        className="bg-rose-500"
                        style={{
                          width: `${(((sector.up_count || 0) / Math.max(1, (sector.up_count || 0) + (sector.down_count || 0))) * 100).toFixed(1)}%`,
                        }}
                      />
                      <div className="flex-1 bg-emerald-500" />
                    </div>
                  </div>
                ) : null}
              </Card>

              <HistoryCard history={info?.history || []} days={20} />

              <MembersCard
                rows={rows}
                total={info?.member_count}
                degraded={degraded}
                loading={membersApi.loading && rows.length === 0}
                error={membersApi.error && rows.length === 0 ? membersApi.error : null}
                onRetry={membersApi.reload}
                leaderName={leaderName}
                onPick={onPick}
              />
            </div>
          ) : null}
        </AsyncBlock>
      </div>
    </PageShell>
  )
}

/* ------------------------------------------------------------------------ 页面 */

export default function ConceptAnalysisPage() {
  const navigate = useNavigate()
  const params = useParams()
  const code = routeCode(params.code)

  const [page, setPage] = useState(1)
  const [sort, setSort] = useState('pct_chg_desc')

  // 榜单模式的三份数据（code 存在时禁用，避免下钻后仍在拉榜单）
  const list = useApi(
    () =>
      fetchBoards(`/boards/${RESOURCE}`, {
        page,
        size: PAGE_SIZE,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
        sort,
      }),
    [page, sort],
    { enabled: !code },
  )
  const topUp = useApi(
    () => fetchBoards(`/boards/${RESOURCE}`, { page: 1, size: 10, limit: 10, offset: 0, sort: 'pct_chg_desc' }),
    [],
    { enabled: !code },
  )
  const topDown = useApi(
    () => fetchBoards(`/boards/${RESOURCE}`, { page: 1, size: 10, limit: 10, offset: 0, sort: 'pct_chg_asc' }),
    [],
    { enabled: !code },
  )

  // 详情模式：code 进 deps，URL 变化即重新拉取
  const detail = useApi(() => api.get(`/boards/${KIND}/${encodeURIComponent(code)}`, { days: 20 }), [code], {
    enabled: !!code,
  })
  const membersApi = useApi(
    () => api.get(`/boards/${KIND}/${encodeURIComponent(code)}/members`, { days: 20 }),
    [code],
    { enabled: !!code },
  )

  // 轮询只刷新当前模式的接口，卸载时清理定时器
  React.useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      if (code) {
        detail.reload()
        membersApi.reload()
      } else {
        list.reload()
        topUp.reload()
        topDown.reload()
      }
    }, POLL_MS)
    return () => clearInterval(id)
  }, [code, list.reload, topUp.reload, topDown.reload, detail.reload, membersApi.reload])

  const pick = (target) => navigate(`${BASE}/${encodeURIComponent(target)}`)
  const back = () => navigate(BASE)

  if (code) return <DetailView code={code} detail={detail} membersApi={membersApi} onBack={back} onPick={pick} />

  return (
    <ListView
      list={list}
      topUp={topUp}
      topDown={topDown}
      page={page}
      setPage={setPage}
      sort={sort}
      setSort={setSort}
      onPick={pick}
    />
  )
}
