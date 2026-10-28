import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AsyncBlock, Badge, Button, Card, EmptyState, PageShell, SectionHeader } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtDate, fmtNum } from '../lib/format.js'

const SOURCE = 'https://liuxing5.github.io/openclaw-quant-system/funnel/funnel.html'

function Stat({ label, value, sub }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900/60">
      <p className="text-[11px] text-slate-400">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums text-slate-800 dark:text-slate-100">{value}</p>
      <p className="mt-0.5 text-[10px] text-slate-400">{sub}</p>
    </div>
  )
}

function CandidateCard({ c, onOpen }) {
  const code = String(c.code || '').split('.')[0]
  return (
    <button
      type="button"
      onClick={() => code && onOpen(code)}
      className="group rounded-xl border border-slate-200 bg-white p-3 text-left transition hover:border-brand-400 hover:shadow-sm dark:border-slate-800 dark:bg-slate-900/60 dark:hover:border-brand-500/60"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-semibold text-slate-800 group-hover:text-brand-600 dark:text-slate-100 dark:group-hover:text-brand-300">
              {c.name || code}
            </span>
            {c.source ? <span className="rounded bg-slate-100 px-1 text-[9px] text-slate-500 dark:bg-slate-800 dark:text-slate-400">{c.source}</span> : null}
          </div>
          <p className="mt-0.5 font-mono text-[10px] text-slate-400">{c.code}</p>
        </div>
        <span className="rounded-lg bg-brand-50 px-2 py-1 text-sm font-bold tabular-nums text-brand-600 dark:bg-brand-500/10 dark:text-brand-300">
          {c.score || '—'}
        </span>
      </div>

      <div className="mt-2 grid grid-cols-3 gap-1.5">
        {[
          ['入场', c.entry],
          ['止损', c.stop],
          ['目标', c.target],
        ].map(([label, value]) => (
          <div key={label} className="rounded-md bg-slate-50 px-2 py-1.5 text-center dark:bg-slate-800/60">
            <p className="text-[9px] text-slate-400">{label}</p>
            <p className={`text-xs font-medium tabular-nums ${label === '止损' ? 'text-emerald-500' : label === '目标' ? 'text-rose-500' : 'text-slate-700 dark:text-slate-200'}`}>
              {value || '—'}
            </p>
          </div>
        ))}
      </div>
      {c.quant_score ? <p className="mt-1 text-right text-[10px] text-slate-400">量化分 {c.quant_score}</p> : null}
      {c.tags?.length ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {c.tags.slice(0, 4).map((t) => (
            <span key={t} className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500 dark:bg-slate-800 dark:text-slate-400">
              {t}
            </span>
          ))}
        </div>
      ) : null}
    </button>
  )
}

export default function RecommendPage() {
  const navigate = useNavigate()
  const { data, loading, error, reload } = useApi(() => api.get('/recommend/funnel'), [])
  const strategies = data?.strategies || []
  const [active, setActive] = useState(0)

  useEffect(() => {
    if (active >= strategies.length && strategies.length) setActive(0)
  }, [active, strategies.length])

  const current = strategies[active]
  const pathGroups = useMemo(() => {
    const groups = []
    const index = new Map()
    for (const c of current?.candidates || []) {
      const key = c.path || ''
      if (!index.has(key)) {
        index.set(key, { path: key, items: [] })
        groups.push(index.get(key))
      }
      index.get(key).items.push(c)
    }
    return groups
  }, [current])
  const totalCandidates = useMemo(() => strategies.reduce((n, s) => n + (s.candidates?.length || 0), 0), [strategies])
  const latest = useMemo(() => {
    const vals = strategies.map((s) => s.updated).filter(Boolean)
    return vals.length ? vals.sort().slice(-1)[0] : '—'
  }, [strategies])
  const latestDate = String(latest).slice(0, 10)
  const pendingNames = useMemo(
    () =>
      strategies
        .filter((s) => s.updated && String(s.updated).slice(0, 10) < latestDate)
        .map((s) => s.name.replace(/^[^\w\u4e00-\u9fa5]+/, '')),
    [strategies, latestDate],
  )

  return (
    <PageShell
      title="荐股"
      subtitle={`六策略对比选股系统 · 数据日 ${latestDate || data?.report_date || '—'} · 盘后 15:10 更新`}
      actions={
        <a href={SOURCE} target="_blank" rel="noreferrer" className="text-xs font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400">
          数据源原文 ↗
        </a>
      }
    >
      <AsyncBlock
        loading={loading}
        error={error}
        onRetry={reload}
        empty={!loading && !error && strategies.length === 0}
        emptyText="荐股数据暂不可用"
        emptyHint="数据源为 GitHub Pages 静态报告，可点右上角查看原文"
        loadingLabel="荐股策略加载中"
      >
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="策略" value={`${strategies.length} 套`} sub="八步 / 七步 / LLM / 主升浪 / 回调 / 原版八步" />
            <Stat label="候选" value={`${totalCandidates} 只`} sub="当前报告全部策略去重前" />
            <Stat label="报告日期" value={data?.report_date ? fmtDate(data.report_date) : '—'} sub="数据源生成日" />
            <Stat label="最新更新" value={latest} sub="各策略最近运行时间" />
          </div>

          {pendingNames.length ? (
            <div className="rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
              {pendingNames.join(' / ')} 仍为 {data?.report_date || '上一交易日'} 收盘版；
              七步漏斗/LLM/回调在收盘后 15:10~16:20 生成，当前尚未到盘后更新时间。
            </div>
          ) : null}

          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
            {strategies.map((s, i) => {
              const on = i === active
              return (
                <button
                  type="button"
                  key={s.id}
                  onClick={() => setActive(i)}
                  className={`rounded-xl border p-3 text-left transition ${
                    on
                      ? 'border-brand-500 bg-brand-50/70 shadow-sm dark:border-brand-500/70 dark:bg-brand-500/10'
                      : 'border-slate-200 bg-white hover:border-slate-300 dark:border-slate-800 dark:bg-slate-900/60'
                  }`}
                >
                  <p className="truncate text-xs font-semibold text-slate-700 dark:text-slate-200">{s.name.replace(/^[^\w\u4e00-\u9fa5]+/, '')}</p>
                  <p className="mt-1 text-[10px] text-slate-400">{s.updated || '—'}</p>
                  <div className="mt-2 flex items-center justify-between">
                    <Badge tone={on ? 'blue' : 'slate'}>{s.candidate_count || 0} 只</Badge>
                    {s.candidates?.[0] ? (
                      <span className="truncate text-[10px] text-slate-500 dark:text-slate-400">
                        {s.candidates[0].name} {s.candidates[0].score}
                      </span>
                    ) : null}
                  </div>
                </button>
              )
            })}
          </div>

          {current ? (
            <div className="grid gap-4 xl:grid-cols-3">
              <Card className="xl:col-span-1">
                <SectionHeader
                  title="漏斗路径"
                  subtitle={`${current.name.replace(/^[^\w\u4e00-\u9fa5]+/, '')} · ${current.updated || ''}`}
                  badge={<Badge tone="blue">{current.steps.length} 层</Badge>}
                />
                <div className="mt-3 space-y-2">
                  {current.steps.map((step, i) => {
                    const pass = Number(String(step.pass || '').replace(/[^\d.]/g, ''))
                    const elim = Number(String(step.eliminated || '').replace(/[^\d.]/g, ''))
                    const total = pass + elim
                    const pct = total ? Math.round((pass / total) * 100) : null
                    return (
                      <div key={`${step.name}-${i}`} className="rounded-lg border border-slate-100 px-2.5 py-2 dark:border-slate-800">
                        <div className="flex items-center gap-2">
                          <span className="grid size-5 shrink-0 place-items-center rounded-md bg-slate-100 text-[10px] font-semibold text-slate-500 dark:bg-slate-800 dark:text-slate-300">
                            {i + 1}
                          </span>
                          <span className="min-w-0 flex-1 truncate text-xs font-medium text-slate-700 dark:text-slate-200">{step.name}</span>
                          <span className="text-xs font-semibold tabular-nums text-brand-600 dark:text-brand-300">{step.pass || '—'}</span>
                          {step.eliminated ? <span className="text-[10px] text-slate-400">淘汰 {step.eliminated}</span> : null}
                        </div>
                        {pct !== null ? (
                          <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                            <div className="h-full rounded-full bg-brand-500" style={{ width: `${pct}%` }} />
                          </div>
                        ) : null}
                        {step.rule ? <p className="mt-1 line-clamp-2 text-[10px] text-slate-400">{step.rule}</p> : null}
                      </div>
                    )
                  })}
                </div>
              </Card>

              <Card className="xl:col-span-2">
                <SectionHeader
                  title="候选标的"
                  subtitle="点击进入本地个股分析；入场/止损/目标来自策略报告"
                  badge={<Badge tone="rose">{current.candidates.length} 只</Badge>}
                />
                {current.candidates.length ? (
                  <div className="mt-3 space-y-4">
                    {pathGroups.map((group) => (
                      <div key={group.path || 'all'}>
                        {group.path ? (
                          <div className="mb-2 flex items-center gap-2">
                            <span
                              className={`size-2 rounded-full ${
                                group.path.includes('高位') ? 'bg-amber-500' : 'bg-emerald-500'
                              }`}
                            />
                            <span className="text-xs font-semibold text-slate-700 dark:text-slate-200">{group.path}</span>
                            <span className="text-[10px] text-slate-400">{group.items.length} 只</span>
                          </div>
                        ) : null}
                        <div className="grid gap-2 sm:grid-cols-2 2xl:grid-cols-3">
                          {group.items.map((c) => (
                            <CandidateCard key={`${c.code}-${c.score}`} c={c} onOpen={(code) => navigate(`/stock-analysis/${code}`)} />
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <EmptyState
                    text={current.status === 'no_candidates' ? '本期扫描无候选' : '本期策略未生成候选'}
                    hint={
                      current.status === 'no_candidates'
                        ? '本期没有同时满足漏斗条件的标的'
                        : '该策略当日未运行，下一期生成后再展示'
                    }
                  />
                )}

                {current.backtest?.length ? (
                  <div className="mt-4">
                    <SectionHeader title="历史前向收益" subtitle="策略报告自带回测口径" />
                    <div className="mt-2 overflow-x-auto">
                      <table className="w-full min-w-[520px] text-xs">
                        <thead className="text-[10px] text-slate-400">
                          <tr>
                            {Object.keys(current.backtest[0]).map((h) => (
                              <th key={h} className="px-2 py-1.5 text-right font-medium first:text-left">{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {current.backtest.map((row, i) => (
                            <tr key={i} className="border-t border-slate-100 dark:border-slate-800">
                              {Object.values(row).map((v, j) => (
                                <td key={j} className={`px-2 py-1.5 tabular-nums ${j === 0 ? 'text-left font-medium text-slate-600 dark:text-slate-300' : 'text-right text-slate-500'}`}>
                                  {v || '—'}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ) : null}
              </Card>
            </div>
          ) : null}

          <p className="text-[10px] text-slate-400">
            数据来自「{data?.title || '六策略对比'}」静态报告；量化分为原策略报告口径，点击候选进入本地个股分析，不构成投资建议。
          </p>
        </div>
      </AsyncBlock>
    </PageShell>
  )
}
