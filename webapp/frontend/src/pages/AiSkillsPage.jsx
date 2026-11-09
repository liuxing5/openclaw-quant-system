import React, { useMemo, useState } from 'react'
import { Badge, Card, PageShell, SectionHeader, Tabs } from '../components/ui.jsx'
import { api, useApi } from '../lib/api.js'

const CATEGORY_LABEL = {
  trend: '趋势',
  reversal: '反转',
  framework: '框架',
  pattern: '形态',
}

export default function AiSkillsPage() {
  const q = useApi(() => api.get('/ai/skills'), [])
  const [category, setCategory] = useState('all')
  const items = q.data?.items || []
  const categories = useMemo(() => {
    const set = new Set(items.map((s) => s.category).filter(Boolean))
    return [{ value: 'all', label: '全部' }, ...[...set].map((c) => ({ value: c, label: CATEGORY_LABEL[c] || c }))]
  }, [items])
  const rows = category === 'all' ? items : items.filter((s) => s.category === category)

  return (
    <PageShell
      title="AI 策略技能"
      subtitle="YAML 自定义策略库 · 放到 strategies/agent_skills/ 即可被自动加载"
      actions={<Tabs tabs={categories} value={category} onChange={setCategory} />}
    >
      <div className="space-y-4">
        <Card>
          <SectionHeader
            title="策略技能库"
            subtitle="外部 daily_stock_analysis 的 Strategy Skill 模式；支持自然语言说明 + 可选自动规则"
            badge={<Badge tone="blue">{rows.length} 个</Badge>}
          />
          <p className="mt-2 text-[11px] leading-5 text-slate-400">
            新增 YAML 文件后，个股页「AI 多 Agent 会诊」会自动把匹配到的技能纳入决策。
            没有配置 rules 的技能只作为自然语言策略说明，不会被规则引擎自动判定。
          </p>
        </Card>

        {rows.map((s) => (
          <Card key={s.name}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{s.display_name}</h3>
                  <span className="font-mono text-[10px] text-slate-400">{s.name}</span>
                  <Badge tone="slate">{CATEGORY_LABEL[s.category] || s.category}</Badge>
                  {s.market_regimes?.length ? <Badge tone="amber">{s.market_regimes.join(' / ')}</Badge> : null}
                </div>
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{s.description}</p>
              </div>
              <span className="text-[10px] text-slate-400">{s.source}</span>
            </div>
            {s.instructions ? (
              <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-slate-50 p-2.5 text-[11px] leading-5 text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
                {s.instructions}
              </pre>
            ) : null}
            {s.rules?.length ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {s.rules.map((r, i) => (
                  <span key={i} className="rounded-md bg-brand-50 px-2 py-1 font-mono text-[10px] text-brand-700 dark:bg-brand-500/10 dark:text-brand-300">
                    {r.field} {r.op} {r.field2 ?? JSON.stringify(r.value)}
                  </span>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-[10px] text-amber-500">未配置自动 rules，仅作为 AI 自然语言策略</p>
            )}
          </Card>
        ))}
      </div>
    </PageShell>
  )
}
