import React from 'react'
import { Badge, Card, SectionHeader } from './ui.jsx'
import { api, useApi } from '../lib/api.js'
import { fmtNum } from '../lib/format.js'

const SIGNAL = {
  buy: { label: '偏多', tone: 'rose' },
  hold: { label: '中性', tone: 'slate' },
  sell: { label: '偏空', tone: 'emerald' },
}
const DECISION = {
  buy: { label: '买入观察', tone: 'rose' },
  hold: { label: '观望', tone: 'amber' },
  sell: { label: '减仓', tone: 'emerald' },
}

function AgentTile({ a }) {
  const s = SIGNAL[a.signal] || SIGNAL.hold
  return (
    <div className="rounded-lg border border-slate-200 p-2.5 dark:border-slate-700">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold text-slate-700 dark:text-slate-200">{a.label}</p>
        <Badge tone={s.tone}>{s.label}</Badge>
      </div>
      <p className="mt-1 text-[10px] text-slate-400">置信度 {Math.round((a.confidence || 0) * 100)}%</p>
      <p className="mt-1 line-clamp-2 text-[10px] leading-4 text-slate-500 dark:text-slate-400">
        {(a.reasons || []).join(' · ')}
      </p>
    </div>
  )
}

export default function AgentResearchCard({ symbol }) {
  const q = useApi(() => api.get(`/stock-analysis/${encodeURIComponent(symbol)}/research`), [symbol])
  if (!symbol) return null
  if (q.loading && !q.data) {
    return (
      <Card>
        <SectionHeader title="AI 多 Agent 会诊" subtitle="技术面 / 信息面 / 风险面 / 策略技能 / 决策" />
        <p className="mt-3 text-xs text-slate-400">会诊中…</p>
      </Card>
    )
  }
  if (!q.data) return null
  const d = q.data
  const dec = DECISION[d.decision?.type] || DECISION.hold
  const flags = d.risk_flags || []
  return (
    <Card>
      <SectionHeader
        title="AI 多 Agent 会诊"
        subtitle="技术 40% · 信息 30% · 风险 30% · 技能 20% · 规则可解释"
        badge={<Badge tone={dec.tone}>{dec.label}</Badge>}
      />
      <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/60">
          <p className="text-[10px] text-slate-400">综合评分</p>
          <p className={`mt-1 text-2xl font-bold tabular-nums ${d.decision?.score >= 60 ? 'text-rose-500' : d.decision?.score <= 40 ? 'text-emerald-500' : 'text-amber-500'}`}>
            {fmtNum(d.decision?.score, 1)}
          </p>
          <p className="text-[10px] text-slate-400">置信度 {d.decision?.confidence || '—'}</p>
        </div>
        <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/60">
          <p className="text-[10px] text-slate-400">操作建议</p>
          <p className="mt-1 text-sm font-semibold text-slate-700 dark:text-slate-200">{d.decision?.advice || '—'}</p>
        </div>
        <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/60">
          <p className="text-[10px] text-slate-400">目标 / 止损</p>
          <p className="mt-1 text-sm font-semibold tabular-nums text-rose-500">{fmtNum(d.decision?.target, 2)}</p>
          <p className="text-[10px] tabular-nums text-emerald-500">止损 {fmtNum(d.decision?.stop, 2)}</p>
        </div>
        <div className="rounded-lg bg-slate-50 p-3 dark:bg-slate-800/60">
          <p className="text-[10px] text-slate-400">模型概率</p>
          <p className="mt-1 text-sm font-semibold tabular-nums text-brand-500">{d.decision?.probability ?? '—'}%</p>
        </div>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        {(d.agents || []).map((a) => <AgentTile key={a.agent} a={a} />)}
      </div>

      <div className="mt-3 grid gap-3 xl:grid-cols-2">
        <div>
          <p className="text-[11px] font-semibold text-slate-500 dark:text-slate-400">策略技能</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {(d.skills || []).filter((s) => s.matched).map((s) => (
              <span key={s.skill} className="rounded-full bg-brand-50 px-2 py-1 text-[10px] text-brand-700 dark:bg-brand-500/10 dark:text-brand-300">
                {s.name}
              </span>
            ))}
            {!(d.skills || []).some((s) => s.matched) ? <span className="text-[10px] text-slate-400">没有匹配的策略技能</span> : null}
          </div>
        </div>
        <div>
          <p className="text-[11px] font-semibold text-slate-500 dark:text-slate-400">风险标记</p>
          <div className="mt-1.5 space-y-1">
            {flags.length ? flags.slice(0, 5).map((f, i) => (
              <p key={i} className={`text-[10px] ${f.severity === 'high' ? 'text-rose-500' : 'text-amber-500'}`}>
                [{f.category}] {f.description}
              </p>
            )) : <p className="text-[10px] text-emerald-500">未发现高优先级风险</p>}
          </div>
        </div>
      </div>

      {(d.evidence?.news?.length || d.evidence?.announcements?.length) ? (
        <div className="mt-3 border-t border-slate-100 pt-2 dark:border-slate-800">
          <p className="text-[10px] font-semibold text-slate-500">证据</p>
          <ul className="mt-1 space-y-0.5 text-[10px] text-slate-400">
            {[...(d.evidence.news || []), ...(d.evidence.announcements || [])].slice(0, 4).map((x, i) => (
              <li key={i} className="truncate">· {x}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {d.data_limitations?.length ? (
        <p className="mt-2 text-[10px] text-amber-500">数据限制：{d.data_limitations.join('；')}</p>
      ) : null}
      <p className="mt-2 text-[9px] text-slate-400">{d.disclaimer}</p>
    </Card>
  )
}
